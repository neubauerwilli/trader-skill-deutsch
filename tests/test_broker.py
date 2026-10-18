import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-broker", "ticket")
check = load("trader-risiko", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risiko", "orders-reel.json")
TODAY = "2026-10-13"
AAPL = {"symbol": "AAPL", "side": "buy", "notional": "1250", "type": "market", "time_in_force": "day"}
BATCH = ["AAPL", "AXP", "GOOGL", "KO", "BAC"]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=AAPL):
        return ticket.parse_yes(reply, order, BATCH)[0]

    def test_german_ja_counts(self):
        for r in ("ja", "Ja.", "JA", "ja, kauf es", "Ja AAPL", "ja $1,250 von AAPL", "ja 1250"):
            self.assertTrue(self.yes(r), r)

    def test_german_doubt_and_batch_do_not_count(self):
        for r in ("ja zu allen", "ja, alle", "ja beide", "ja, aber nur 1000", "ja wenn er unter 340 ist",
                  "ja, warte", "ja vielleicht", "ja, später", "ja außer AXP", "nein", "nicht ja",
                  "ja, erst morgen", "ja?", "ok", "passt", "mach"):
            self.assertFalse(self.yes(r), r)

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes AAPL", "yes $1,250 of AAPL",
                  "yes 1250"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "absolutely", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both",
                  "yes AXP", "yes 1000", "yes but make it 1000", "yes?", "no", "not yes",
                  "yes, wait", "yes if it is under 340"):
            self.assertFalse(self.yes(r), r)

    def test_amount_must_match_this_order(self):
        sell = {"symbol": "KO", "side": "sell", "qty": "3", "type": "market", "time_in_force": "day"}
        self.assertTrue(self.yes("yes sell 3 KO", sell))
        self.assertFalse(self.yes("yes sell 4", sell))

    def test_wrong_side_unknown_ticker_and_mixed_amounts_are_not_approval(self):
        for reply in ("ja verkaufe AAPL", "yes sell AAPL", "ja TSLA", "ja 1250 und 500",
                      "yes 1250 500", "ja -1250", "ja morgen", "ja Auftrag 2", "ja1250"):
            with self.subTest(reply=reply):
                self.assertFalse(self.yes(reply))
        limit = {"symbol": "KO", "side": "buy", "qty": "3", "type": "limit", "limit_price": "95"}
        self.assertFalse(self.yes("yes buy 4 KO at 95", limit))


class TicketShape(unittest.TestCase):
    def test_tool_input_is_the_alpaca_call(self):
        o = dict(AAPL, why="copying Berkshire")
        self.assertEqual(ticket.tool_input(o), AAPL)
        self.assertEqual(ticket.tool_input({"symbol": "KO", "side": "buy", "qty": 3, "type": "limit",
                                            "limit_price": 89.5, "time_in_force": "day"}),
                         {"symbol": "KO", "side": "buy", "qty": "3", "type": "limit", "limit_price": "89.5",
                          "time_in_force": "day"})

    def test_plain_english(self):
        self.assertEqual(ticket.plain(AAPL), "KAUF $1,250 von AAPL, Market-Order, gültig für heute")
        self.assertEqual(ticket.plain({"symbol": "KO", "side": "sell", "qty": "1", "type": "limit",
                                       "limit_price": "95", "time_in_force": "gtc"}),
                         "VERKAUF 1 Aktie von KO, Limit-Order zu $95, gültig bis Widerruf")


class DesktopConfig(unittest.TestCase):
    def test_example_is_paper_with_placeholder_keys(self):
        with open(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")) as fh:
            server = json.load(fh)["mcpServers"]["alpaca"]
        self.assertEqual((server["command"], server["args"]), ("uvx", ["alpaca-mcp-server"]))
        self.assertEqual(server["env"]["ALPACA_PAPER_TRADE"], "true")
        for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            self.assertIn("NEVER_IN_CHAT", server["env"][k])


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--today", TODAY]

    def run_risk(self, today=TODAY):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", os.path.join(self.d, "none"), "--state", self.state,
                               "--today", today, "--out", self.risk])

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NICHT BEREIT", text)
        self.assertNotIn('Antworte "yes"', text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("nicht bereit", err)

    def test_the_full_two_yes_flow(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("BEREIT: trader-risiko PASS", text)
        self.assertIn('"notional": "1250"', text)
        self.assertIn("Allow once", text)
        self.assertIn("PAPER-Konto", text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Nicht erfasst", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("place_stock_order", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("FREIGEGEBEN", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "abc123",
                                      "--filled-qty", "3.6628", "--filled-price", "341.27", "--portfolio", self.pf))
        self.assertEqual(code, 0)
        with open(self.state) as fh:
            st = json.load(fh)
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed"])
        self.assertEqual(st["orders"][0]["reply"], "yes")
        self.assertEqual(st["orders"][1]["broker_order_id"], "abc123")
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 3750.0)
        self.assertEqual(pf["positions"][0]["lots"][0]["cost"], 1250.0)

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("ERLEDIGT: platziert", text)
        code, text, _ = run(["log", "--state", self.state, "--today", TODAY])
        self.assertIn("1 platziert heute", text)

        # trader-risiko now counts it: a second AAPL buy today is a duplicate
        res = check.check([AAPL], {"budget": 5000, "max_position_pct": 100}, state=st, today=TODAY)
        self.assertIn("heute schon abgeschickt", res["results"][0]["reasons"][-1])

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        with open(self.orders) as fh:
            doc = json.load(fh)
        doc["orders"][0]["notional"] = "1300"
        with open(self.orders, "w") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("war nicht in der letzten Risikoprüfung", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(today="2026-10-12")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("nicht von heute", text)

    def test_blocked_orders_are_never_ready(self):
        with open(self.cfg) as fh:
            c = json.load(fh)
        c["blocked"] = ["AAPL"]
        with open(self.cfg, "w") as fh:
            json.dump(c, fh)
        self.assertEqual(self.run_risk(), 1)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("GESPERRT durch trader-risiko", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "2", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("kein Ja erfasst", err)
        code, text, _ = run(self.args("decline", "--index", "2", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        code, text, _ = run(self.args("show", "--index", "2"))
        self.assertIn("ERLEDIGT: abgelehnt", text)

    def test_ibkr_tickets_send_the_final_yes_to_ibkr(self):
        with open(self.cfg) as fh:
            c = json.load(fh)
        c["broker"] = "ibkr"
        with open(self.cfg, "w") as fh:
            json.dump(c, fh)
        self.run_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("Interactive Brokers", text)
        self.assertIn("nachdem du es dort freigegeben hast", text)
        self.assertNotIn("place_stock_order", text)

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "KO", "shares": 3, "lots": [
            {"id": "KO-1", "date": "2025-01-02", "shares": 2, "cost": 120.0},
            {"id": "KO-2", "date": "2026-01-02", "shares": 1, "cost": 70.0}]}]}
        with open(self.pf, "w") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "KO", "--side", "sell", "--qty", "2.5", "--price", "90",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg])
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 225.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["KO-2"])
        self.assertEqual(lots[0]["shares"], 0.5)
        self.assertEqual(lots[0]["cost"], 35.0)
        code, _, err = run(["fill", "--symbol", "KO", "--side", "sell", "--qty", "5", "--price", "90",
                            "--portfolio", self.pf, "--config", self.cfg])
        self.assertEqual(code, 1)


class InvalidFill(unittest.TestCase):
    def test_new_fill_does_not_reuse_surviving_lot_id(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "portfolio.json")
            portfolio = {"cash": 1000, "positions": [{"ticker": "KO", "shares": 1,
                         "lots": [{"id": "KO-2", "date": "2025-01-01", "shares": 1, "cost": 50}]}]}
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(portfolio, stream)
            result = ticket.apply_fill(path, {"paper": True}, "KO", "buy", 1, 60, TODAY)
            identifiers = [lot["id"] for lot in result["positions"][0]["lots"]]
            self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_invalid_fill_leaves_existing_portfolio_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "portfolio.json")
            original = '{"cash":1000,"positions":[]}'
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(original)
            for qty, price in (("NaN", "10"), ("1", "Infinity"), ("-1", "10"),
                               (True, "10"), ("1e308", "1e308")):
                with self.subTest(qty=qty, price=price), self.assertRaises(ticket.TicketError):
                    ticket.apply_fill(path, {"paper": True}, "KO", "buy", qty, price, TODAY)
                with open(path, encoding="utf-8") as stream:
                    self.assertEqual(stream.read(), original)

    def test_bad_shapes_are_clean_cli_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            orders = os.path.join(folder, "orders.json")
            with open(orders, "w") as stream:
                json.dump({"orders": [None]}, stream)
            code, _, error = run(["show", "--orders", orders])
            self.assertEqual(code, 1)
            self.assertIn("keine Aufträge", error)


if __name__ == "__main__":
    unittest.main()

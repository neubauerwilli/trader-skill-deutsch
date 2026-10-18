import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risiko", "check")
ticket = load("trader-broker", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risiko", "orders-reel.json")
TODAY = "2026-10-13"
PRICES = {"AAPL": 341.27, "AXP": 309.84, "GOOGL": 353.78, "KO": 90.11, "BAC": 53.96}


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def reel_orders():
    with open(ORDERS_PATH) as fh:
        return json.load(fh)["orders"]


def buy(sym, usd):
    return {"symbol": sym, "side": "buy", "notional": str(usd), "type": "market", "time_in_force": "day"}


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i):
    return " | ".join(res["results"][i]["reasons"])


class TheReel(unittest.TestCase):
    def test_the_five_buys_pass_on_a_fresh_5000_paper_account(self):
        res = check.check(reel_orders(), cfg(), today=TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 500)
        self.assertIs(res["paper"], True)
        self.assertEqual(res["results"][0]["value_usd"], 1250)
        self.assertEqual(res["results"][0]["amount"], "$1,250")

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(reel_orders() + [buy("AAPL", 100)], cfg(), today=TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("Bargeld:", why)
        self.assertIn("Position:", why)
        self.assertIn("Doppelt:", why)

    def test_fingerprint_is_shared_with_the_broker(self):
        o = reel_orders()[0]
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, notional="1251")), check.fingerprint(o))


class Limits(unittest.TestCase):
    def one(self, order, **over):
        res = check.check([order], cfg(**over), prices=PRICES, today=TODAY)
        return res["results"][0]

    def test_max_and_min_order(self):
        self.assertIn("über deinem Höchstauftrag von $2,000", " ".join(self.one(buy("KO", 2500), max_position_pct=100)["reasons"]))
        self.assertIn("unter deinem Mindestauftrag von $5", " ".join(self.one(buy("KO", 2))["reasons"]))

    def test_blocked_list(self):
        r = self.one(buy("KO", 100), blocked=["KO"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("Sperrliste", r["reasons"][0])

    def test_options_off_unless_allowed(self):
        opt = {"symbol": "BE270617C00100000", "side": "buy", "qty": "1", "type": "limit",
               "limit_price": "12.50", "time_in_force": "day"}
        r = self.one(opt)
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("option", " ".join(r["reasons"]))
        r = self.one(opt, allow_options=True)
        self.assertEqual(r["verdict"], "PASS")
        self.assertTrue(r["warnings"])

    def test_crypto_and_junk_symbols(self):
        crypto = {"symbol": "BTC/USD", "side": "buy", "notional": "50", "type": "market", "time_in_force": "day"}
        self.assertIn("nur Aktien und ETFs", " ".join(self.one(crypto)["reasons"]))

    def test_format_rules(self):
        both = dict(buy("KO", 100), qty="1")
        self.assertIn("genau eins von qty oder notional", " ".join(self.one(both)["reasons"]))
        lim = dict(buy("KO", 100), type="limit", limit_price="90")
        self.assertIn("Markt-Auftrag mit time_in_force day", " ".join(self.one(lim)["reasons"]))
        nolimit = {"symbol": "KO", "side": "buy", "qty": "1", "type": "limit", "time_in_force": "day"}
        self.assertIn("braucht einen limit_price", " ".join(self.one(nolimit)["reasons"]))
        bad = {"symbol": "KO", "side": "hold", "notional": "100"}
        self.assertIn("side muss buy oder sell sein", " ".join(self.one(bad)["reasons"]))

    def test_numbers_should_be_strings(self):
        r = self.one({"symbol": "KO", "side": "buy", "notional": 100, "type": "market", "time_in_force": "day"})
        self.assertEqual(r["verdict"], "PASS")
        self.assertIn("als String", r["warnings"][0])

    def test_share_orders_need_a_price(self):
        q = {"symbol": "ZZZZ", "side": "buy", "qty": "2", "type": "market", "time_in_force": "day"}
        self.assertIn("kein Kurs für ZZZZ", " ".join(self.one(q)["reasons"]))
        q["symbol"] = "KO"
        r = self.one(q)
        self.assertEqual(r["verdict"], "PASS")
        self.assertAlmostEqual(r["value_usd"], 180.22)

    def test_margin(self):
        r = check.check([buy("KO", 1500)], cfg(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": 1000, "positions": []}, today=TODAY)["results"][0]
        self.assertIn("Margin", " ".join(r["reasons"]))


class Account(unittest.TestCase):
    PF = {"cash": 500.0, "positions": [{"ticker": "KO", "shares": 7.8571,
                                         "lots": [{"date": "2026-10-13", "shares": 7.8571, "cost": 708.0}]}]}

    def test_short_sales_are_blocked(self):
        sell = {"symbol": "KO", "side": "sell", "qty": "10", "type": "market", "time_in_force": "day"}
        r = check.check([sell], cfg(), portfolio=self.PF, prices=PRICES, today=TODAY)["results"][0]
        self.assertIn("Leerverkauf:", " ".join(r["reasons"]))
        sell["qty"] = "3"
        r = check.check([sell], cfg(), portfolio=self.PF, prices=PRICES, today=TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [{"symbol": "KO", "side": "sell", "qty": "5", "type": "market", "time_in_force": "day"},
                  buy("BAC", 450)]
        res = check.check(orders, cfg(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("BAC", 450)], cfg(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        placed = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed", "at": TODAY + "T10:00:00"}
                             for i in range(10)]}
        res = check.check([buy("KO", 100)], cfg(), state=placed, today=TODAY)
        self.assertIn("Tageslimit", reasons(res, 0))
        res = check.check([buy("KO", 100)], cfg(), state=placed, today="2026-10-14")
        self.assertEqual(verdicts(res), ["PASS"])
        ko = {"orders": [{"symbol": "KO", "side": "buy", "status": "placed", "at": TODAY + "T10:00:00"}]}
        res = check.check([buy("KO", 100)], cfg(), state=ko, today=TODAY)
        self.assertIn("heute schon abgeschickt", reasons(res, 0))
        declined = {"orders": [{"symbol": "KO", "side": "buy", "status": "declined", "at": TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("KO", 100)], cfg(), state=declined, today=TODAY)), ["PASS"])

    def test_live_account_warns(self):
        res = check.check([buy("KO", 100)], cfg(paper=False), today=TODAY)
        self.assertIn("LIVE", res["warnings"][0])


class Cli(unittest.TestCase):
    def test_writes_verdicts_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", ORDERS_PATH, "--config", CFG_PATH, "--portfolio", os.path.join(d, "none"),
                                   "--prices", os.path.join(d, "none"), "--state", os.path.join(d, "none"),
                                   "--today", TODAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 bestanden, 0 geblockt", buf.getvalue())
            self.assertIn("Keine Anlageberatung", buf.getvalue())
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(len(rec["results"]), 5)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))

            bad = os.path.join(d, "orders.json")
            with open(bad, "w") as fh:
                json.dump({"orders": reel_orders() + [buy("AAPL", 100)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", bad, "--config", CFG_PATH, "--portfolio", os.path.join(d, "none"),
                                   "--prices", os.path.join(d, "none"), "--state", os.path.join(d, "none"),
                                   "--today", TODAY, "--no-write"])
            self.assertEqual(code, 1)

    def test_missing_orders_file(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("keine Auftragsdatei", err.getvalue())


class AdversarialRisk(unittest.TestCase):
    def test_non_finite_boolean_and_negative_order_values_never_pass(self):
        for value in ("NaN", "Infinity", "-Infinity", float("nan"), float("inf"), True, -100, "0"):
            with self.subTest(value=value):
                result = check.check([dict(buy("KO", 100), notional=value)], cfg(), today=TODAY)
                self.assertEqual(verdicts(result), ["BLOCK"])
                self.assertEqual(result["cash_after"], 5000)

    def test_multiplication_overflow_never_passes(self):
        order = {"symbol": "KO", "side": "buy", "qty": "1e308", "type": "market", "time_in_force": "day"}
        result = check.check([order], cfg(), prices={"KO": 1e308}, today=TODAY)
        self.assertEqual(verdicts(result), ["BLOCK"])
        self.assertIsNone(result["results"][0]["value_usd"])

    def test_large_finite_budget_still_enforces_percentage_cap(self):
        result = check.check([buy("KO", 7e307)], cfg(budget=1e308, cash_buffer_pct=0,
                             max_position_pct=50, max_order_usd=0))
        self.assertEqual(verdicts(result), ["BLOCK"])
        self.assertIn("Position:", reasons(result, 0))

    def test_invalid_config_cannot_disable_limits(self):
        for values in ({"paper": "false"}, {"allow_short": "false"}, {"cash_buffer_pct": "NaN"},
                       {"max_position_pct": float("inf")}, {"max_order_usd": -1},
                       {"max_orders_per_day": 1.5}, {"budget": -100}, {"blocked": "KO"}):
            with self.subTest(values=values), self.assertRaises(check.RiskError):
                check.check([buy("KO", 100)], cfg(**values))

    def test_malformed_inputs_fail_closed(self):
        for kwargs in ({"orders": [None]}, {"cfg": []}, {"portfolio": []},
                       {"prices": []}, {"prices": {"KO": float("nan")}},
                       {"state": {"orders": [None]}},
                       {"portfolio": {"cash": "NaN", "positions": []}},
                       {"portfolio": {"cash": 1000, "positions": [{"ticker": "KO", "shares": -1}]}},
                       {"portfolio": {"cash": 1000, "positions": [{"ticker": "KO", "shares": 1}]}}):
            args = {"orders": [buy("KO", 100)], "cfg": cfg()}
            args.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(check.RiskError):
                check.check(**args)

    def test_batch_cash_is_aggregated_and_dependency_is_recorded(self):
        orders = [buy("KO", 600), buy("AAPL", 600)]
        result = check.check(orders, cfg(budget=1000, cash_buffer_pct=20, max_position_pct=100))
        self.assertEqual(verdicts(result), ["PASS", "BLOCK"])
        self.assertEqual(result["cash_after"], 400)
        self.assertEqual(result["results"][1]["requires"], [check.fingerprint(orders[0])])

    def test_zero_position_cap_really_blocks_buys(self):
        self.assertEqual(verdicts(check.check([buy("KO", 100)], cfg(max_position_pct=0))), ["BLOCK"])

    def test_option_risk_includes_standard_contract_multiplier(self):
        order = {"symbol": "BE270617C00100000", "side": "buy", "qty": "2", "type": "limit",
                 "limit_price": "12.50", "time_in_force": "day"}
        result = check.check([order], cfg(allow_options=True))
        self.assertEqual(result["results"][0]["value_usd"], 2500)
        self.assertEqual(verdicts(result), ["BLOCK"])

    def test_duplicate_portfolio_rows_accumulate_cost_basis(self):
        portfolio = {"cash": 500, "positions": [
            {"ticker": "KO", "shares": 1, "lots": [{"shares": 1, "cost": 300}]},
            {"ticker": "KO", "shares": 1, "lots": [{"shares": 1, "cost": 200}]}]}
        acct, _ = check.account(cfg(), portfolio, {})
        self.assertEqual(acct["values"]["KO"], 500)
        self.assertEqual(acct["shares"]["KO"], 2)

    def test_advanced_and_invalid_trailing_orders_are_blocked(self):
        for order in (dict(buy("KO", 100), order_class="bracket"),
                      {"symbol": "KO", "side": "sell", "qty": "1", "type": "trailing_stop", "trail_price": "NaN"}):
            self.assertEqual(verdicts(check.check([order], cfg(), prices=PRICES)), ["BLOCK"])


if __name__ == "__main__":
    unittest.main()

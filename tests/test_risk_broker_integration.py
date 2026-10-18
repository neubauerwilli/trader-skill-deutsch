"""Real file/CLI handoffs, no broker or network calls."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load

risk = load("trader-risiko", "check")
broker = load("trader-broker", "ticket")
TODAY = "2026-10-18"


def buy(ticker, value):
    return {"symbol": ticker, "side": "buy", "notional": str(value), "type": "market", "time_in_force": "day"}


class Handoff(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = self.temp.name
        self.paths = {name: os.path.join(self.folder, name + ".json")
                      for name in ("orders", "config", "portfolio", "prices", "risk", "state")}
        self.config = {"budget": 1000, "cash_buffer_pct": 0, "max_position_pct": 100,
                       "min_order_usd": 1, "max_order_usd": 1000}
        self.orders = [buy("AAPL", 200), buy("KO", 200)]
        self.write("config", self.config)
        self.write("orders", {"orders": self.orders})

    def write(self, name, doc):
        with open(self.paths[name], "w", encoding="utf-8") as stream:
            json.dump(doc, stream)

    def check(self):
        args = [arg for name in ("orders", "config", "portfolio", "prices", "state")
                for arg in ("--" + name, self.paths[name])]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return risk.main(args + ["--today", TODAY, "--out", self.paths["risk"]])

    def broker(self, command, *extra):
        args = [arg for name in ("orders", "config", "risk", "state") for arg in ("--" + name, self.paths[name])]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = broker.main([command] + args + ["--today", TODAY] + list(extra))
        return code, out.getvalue(), err.getvalue()

    def test_paper_defaults_and_successful_single_ticket_roundtrip(self):
        self.assertEqual(self.check(), 0)
        code, text, _ = self.broker("show", "--index", "1", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(text)["paper"])
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 1)
        self.assertEqual(self.broker("result", "--index", "1", "--status", "placed", "--broker-id", "offline-fixture")[0], 0)
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 1)

    def test_changed_rules_paper_or_order_sequence_require_new_risk(self):
        for key, value in (("paper", False), ("max_order_usd", 100)):
            self.write("config", self.config)
            self.check()
            self.write("config", dict(self.config, **{key: value}))
            self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 1)
        self.write("config", self.config)
        self.check()
        self.write("orders", {"orders": list(reversed(self.orders))})
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 1)

    def test_changed_account_data_and_stale_day_require_new_risk(self):
        self.check()
        self.write("portfolio", {"cash": 1, "positions": []})
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 1)
        os.remove(self.paths["portfolio"])
        self.check()
        with open(self.paths["risk"], encoding="utf-8") as stream:
            doc = json.load(stream)
        doc["date"] = "2026-10-17"
        self.write("risk", doc)
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 1)

    def test_duplicate_fingerprint_cannot_reuse_first_tickets_pass(self):
        self.write("orders", {"orders": [self.orders[0], self.orders[0]]})
        self.assertEqual(self.check(), 1)
        self.assertEqual(self.broker("approve", "--index", "2", "--reply", "ja")[0], 1)
        self.assertFalse(os.path.exists(self.paths["state"]))
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)
        self.assertEqual(self.broker("result", "--index", "2", "--status", "placed", "--broker-id", "wrong-ticket")[0], 1)

    def test_only_one_pending_approval_and_retry_requires_new_risk(self):
        self.check()
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)
        self.assertEqual(self.broker("approve", "--index", "2", "--reply", "ja")[0], 1)
        self.assertEqual(self.broker("result", "--index", "1", "--status", "failed")[0], 0)
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 1)
        self.check()
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)

    def test_result_cannot_log_a_different_or_invalid_fill(self):
        self.check()
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)
        for qty, price in (("NaN", "10"), ("30", "10")):
            with self.subTest(qty=qty):
                self.assertEqual(self.broker("result", "--index", "1", "--status", "placed",
                                 "--broker-id", "offline-fixture", "--filled-qty", qty,
                                 "--filled-price", price, "--portfolio", self.paths["portfolio"])[0], 1)
        with open(self.paths["state"], encoding="utf-8") as stream:
            self.assertEqual([row["status"] for row in json.load(stream)["orders"]], ["approved"])

    def test_corrupt_risk_or_state_is_a_clean_error_without_approval(self):
        self.check()
        with open(self.paths["risk"], encoding="utf-8") as stream:
            original = json.load(stream)
        for malformed in ([], {"results": [None]}, dict(original, results=[dict(original["results"][0], requires=1)])):
            self.write("risk", malformed)
            code, _, error = self.broker("approve", "--index", "1", "--reply", "ja")
            self.assertEqual(code, 1)
            self.assertIn("ticket.py:", error)
            self.assertFalse(os.path.exists(self.paths["state"]))
        self.write("risk", original)
        for malformed in ({}, {"orders": [None]}, {"orders": [{}]}):
            self.write("state", malformed)
            code, _, error = self.broker("approve", "--index", "1", "--reply", "ja")
            self.assertEqual(code, 1)
            self.assertIn("Broker-Stand", error)

    def test_fill_cannot_be_redirected_to_another_portfolio(self):
        self.write("portfolio", {"cash": 1000, "positions": []})
        second = os.path.join(self.folder, "another-account.json")
        with open(second, "w", encoding="utf-8") as stream:
            json.dump({"cash": 5000, "positions": []}, stream)
        self.check()
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)
        code, _, error = self.broker("result", "--index", "1", "--status", "placed", "--broker-id", "offline-id",
                       "--filled-qty", "2", "--filled-price", "100", "--portfolio", second)
        self.assertEqual(code, 1)
        self.assertIn("geprüfte Depotdatei", error)
        with open(second, encoding="utf-8") as stream:
            self.assertEqual(json.load(stream), {"cash": 5000, "positions": []})
        with open(self.paths["state"], encoding="utf-8") as stream:
            self.assertEqual([r["status"] for r in json.load(stream)["orders"]], ["approved"])
        self.assertEqual(self.broker("result", "--index", "1", "--status", "placed", "--broker-id", "offline-id",
                         "--filled-qty", "2", "--filled-price", "100", "--portfolio", self.paths["portfolio"])[0], 0)

    def test_current_daily_limit_counts_results_since_the_risk_check(self):
        self.config["max_orders_per_day"] = 1
        self.write("config", self.config)
        self.write("orders", {"orders": [buy("KO", 100)]})
        self.check()
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)
        other_orders, other_risk = os.path.join(self.folder, "other-orders.json"), os.path.join(self.folder, "other-risk.json")
        with open(other_orders, "w", encoding="utf-8") as stream:
            json.dump({"orders": [buy("AAPL", 100)]}, stream)
        original_orders, original_risk = self.paths["orders"], self.paths["risk"]
        self.paths.update(orders=other_orders, risk=other_risk)
        self.assertEqual(self.check(), 0)
        self.paths.update(orders=original_orders, risk=original_risk)
        self.assertEqual(self.broker("result", "--index", "1", "--status", "placed", "--broker-id", "offline-ko")[0], 0)
        self.paths.update(orders=other_orders, risk=other_risk)
        code, _, error = self.broker("approve", "--index", "1", "--reply", "ja")
        self.assertEqual(code, 1)
        self.assertIn("Tageslimit", error)
        with open(self.paths["state"], encoding="utf-8") as stream:
            self.assertEqual(sum(r["status"] == "placed" for r in json.load(stream)["orders"]), 1)

    def test_own_pending_approval_does_not_invalidate_its_result(self):
        self.check()
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)
        code, text, _ = self.broker("show", "--index", "1")
        self.assertEqual(code, 1)
        self.assertIn("FREIGEGEBEN", text)
        self.assertEqual(self.broker("result", "--index", "1", "--status", "denied")[0], 0)

    def test_sell_funded_buy_waits_for_actual_account_update(self):
        self.write("portfolio", {"cash": 0, "positions": [{"ticker": "KO", "shares": 10,
                   "lots": [{"date": "2025-01-01", "shares": 10, "cost": 1000}]}]})
        self.write("prices", {"KO": 100})
        sell = {"symbol": "KO", "side": "sell", "qty": "5", "type": "market", "time_in_force": "day"}
        self.write("orders", {"orders": [sell, buy("AAPL", 400)]})
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.broker("approve", "--index", "2", "--reply", "ja")[0], 1)
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "ja")[0], 0)
        self.assertEqual(self.broker("result", "--index", "1", "--status", "placed", "--broker-id", "not-filled")[0], 0)
        self.assertEqual(self.broker("approve", "--index", "2", "--reply", "ja")[0], 1)
        broker.apply_fill(self.paths["portfolio"], self.config, "KO", "sell", 5, 100, TODAY)
        self.check()
        self.assertEqual(self.broker("approve", "--index", "2", "--reply", "ja")[0], 0)

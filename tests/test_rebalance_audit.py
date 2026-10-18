"""Fiktive bestehende Positionen, Limits und ungültige Eingaben."""
import math
import unittest
from _load import load
reb = load('trader-ausgleich', 'rebalance')


class RebalanceAudit(unittest.TestCase):
    def test_large_finite_weight_normalises_without_intermediate_overflow(self):
        self.assertEqual(reb.parse_targets({'KO': 1e308}), {'KO': 100})
        with self.assertRaisesRegex(reb.RebalanceError, 'Summe der Gewichte'):
            reb.parse_targets({'KO': 1e308, 'AAPL': 1e308})

    def test_target_allocation_divides_before_multiplying(self):
        self.assertEqual(reb.capped_targets({'KO': 1e308}, 1000, 2000), {'KO': 1000})

    def test_large_finite_account_keeps_percent_math_finite(self):
        result = reb.plan({'cash': 1e308, 'positions': []}, {'KO': 100}, {'KO': 1e308},
                          {'cash_buffer_pct': 10, 'max_position_pct': 25})
        self.assertTrue(math.isfinite(result['cash_buffer']))
        self.assertTrue(math.isfinite(result['cap']))
        self.assertAlmostEqual(result['cash_buffer'] / 1e308, .1)
        self.assertAlmostEqual(result['cap'] / 1e308, .25)
        self.assertEqual(result['rows'][0]['target_pct'], 25)
        self.assertEqual(result['rows'][0]['drift'], -25)

    def plan(self, portfolio=None, prices=None, weights=None, cfg=None, **kwargs):
        return reb.plan(portfolio or {'cash': 1000, 'positions': []}, prices or {'AAPL': 100, 'OLD': 100},
                        weights or {'AAPL': 100}, cfg or {'cash_buffer_pct': 20, 'max_position_pct': 100}, **kwargs)

    def test_sell_untracked_respects_max_order(self):
        result = self.plan({'cash': 200, 'positions': [{'ticker': 'OLD', 'shares': 10}]},
                           cfg={'max_order_usd': 200, 'max_position_pct': 100}, sell_untracked=True)
        sell = next(o for o in result['orders'] if o['side'] == 'sell')
        self.assertEqual(sell['qty'], '2')
        self.assertLessEqual(sell['_usd'], 200)
        self.assertTrue(any('gekürzt' in n for n in result['notes']))

    def test_untracked_sale_does_not_round_above_owned_shares(self):
        held = 1.23456
        result = self.plan({'cash': 200, 'positions': [{'ticker': 'OLD', 'shares': held}]}, sell_untracked=True)
        sell = next(o for o in result['orders'] if o['side'] == 'sell')
        self.assertLessEqual(float(sell['qty']), held)
        self.assertAlmostEqual(sell['_usd'], float(sell['qty']) * 100, places=2)

    def test_invalid_numbers_are_refused(self):
        for cfg in [{'cash_buffer_pct': -1}, {'cash_buffer_pct': 101}, {'max_position_pct': 0},
                    {'min_order_usd': -1}, {'max_order_usd': -1}, {'rebalance_band_pct': -1},
                    {'max_position_pct': float('nan')}, {'fractional': 'false'}]:
            with self.subTest(cfg=cfg), self.assertRaises(reb.RebalanceError): self.plan(cfg=cfg)
        for value in [float('nan'), float('inf'), -1, True]:
            with self.subTest(cash=value), self.assertRaises(reb.RebalanceError): self.plan({'cash': value, 'positions': []})
            with self.subTest(price=value), self.assertRaises(reb.RebalanceError): self.plan(prices={'AAPL': value})
            with self.subTest(shares=value), self.assertRaises(reb.RebalanceError):
                self.plan({'cash': 1000, 'positions': [{'ticker': 'AAPL', 'shares': value}]})

    def test_invalid_targets_are_refused(self):
        for target in [{'AAPL': float('nan')}, {'AAPL': float('inf')}, {'AAPL': -1}, {' ': 1}, [None]]:
            with self.subTest(target=target), self.assertRaises(reb.RebalanceError): reb.parse_targets(target)

    def test_serialized_orders_reconcile_to_cash_after(self):
        pf = {'cash': 317.15, 'positions': [{'ticker': 'AAPL', 'shares': 4.654321}, {'ticker': 'OLD', 'shares': 2.718281}]}
        prices = {'AAPL': 111.17, 'OLD': 71.13, 'MSFT': 97.33}
        result = reb.plan(pf, prices, {'AAPL': 1, 'MSFT': 1}, {'cash_buffer_pct': 20, 'max_position_pct': 50}, band=0, sell_untracked=True)
        cash = pf['cash']
        for o in result['orders']:
            dollars = float(o.get('notional', 0)) if 'notional' in o else float(o['qty']) * prices[o['symbol']]
            cash += dollars if o['side'] == 'sell' else -dollars
        self.assertAlmostEqual(cash, result['cash_after'], places=2)
        self.assertGreaterEqual(cash + .01, result['cash_buffer'])

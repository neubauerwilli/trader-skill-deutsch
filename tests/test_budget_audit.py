"""Regressionen aus der unabhängigen Geld-/Eingabeprüfung, nur fiktive Daten."""
import io
import json
import math
import os
import random
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from _load import load

size = load('trader-budget', 'size')


class BudgetAudit(unittest.TestCase):
    def test_large_finite_weight_normalises_without_intermediate_overflow(self):
        rows = size.parse_weights({'KO': 1e308})
        normal, _ = size.normalise(rows)
        self.assertEqual(normal[0]['weight'], 100)
        result = size.size(rows, self.cfg(budget=1000, max_position_pct=100))
        self.assertEqual(result['positions'][0]['dollars'], 1000)
        self.assertEqual(result['positions'][0]['weight'], 100)
        with self.assertRaisesRegex(size.SizeError, 'Summe der Gewichte'):
            size.size(size.parse_weights({'KO': 1e308, 'AAPL': 1e308}), self.cfg())

    def test_allocation_divides_large_weight_before_multiplying_money(self):
        allocated, capped = size.allocate([{'ticker': 'KO', 'weight': 1e308}], 1000, 2000)
        self.assertEqual(allocated, {'KO': 1000})
        self.assertEqual(capped, set())

    def cfg(self, **over):
        cfg = {'budget': 31, 'cash_buffer_pct': 0, 'max_position_pct': 50, 'min_order_usd': 0}
        cfg.update(over)
        return cfg

    def test_rounding_never_crosses_position_cap(self):
        result = size.size(size.parse_weights({'AAPL': 1, 'MSFT': 1}), self.cfg())
        self.assertEqual([p['dollars'] for p in result['positions']], [15, 15])
        self.assertEqual(result['idle'], 1)

    def test_fractional_cent_limits_round_in_safe_direction(self):
        result = size.size(size.parse_weights({'AAPL': 1, 'MSFT': 1}),
                           self.cfg(budget=31.15, cash_buffer_pct=13, max_position_pct=50), cents=True)
        self.assertGreaterEqual(result['cash_buffer'], 31.15 * .13)
        self.assertLessEqual(result['cap'], 31.15 * .5)

    def test_nonfinite_weights_and_blank_tickers_are_refused(self):
        for data in [{'AAPL': float('nan')}, {'AAPL': float('inf')}, {' ': 1}, {'AAPL': True}]:
            with self.subTest(data=data), self.assertRaises(size.SizeError):
                size.size(size.parse_weights(data), self.cfg())

    def test_invalid_rules_are_domain_errors(self):
        for key, value in [('budget', float('nan')), ('budget', float('inf')), ('budget', 'kaputt'),
                           ('budget', .001), ('budget', 1e308),
                           ('budget', True), ('min_order_usd', -1), ('min_order_usd', float('nan')),
                           ('cash_buffer_pct', 'x'), ('max_position_pct', float('inf')),
                           ('fractional', 'false')]:
            with self.subTest(key=key, value=value), self.assertRaises(size.SizeError):
                size.size(size.parse_weights({'AAPL': 1}), self.cfg(**{key: value}))

    def test_invalid_prices_are_refused_before_whole_share_allocation(self):
        for value in [-1, float('nan'), float('inf'), True, 'wrong']:
            with self.subTest(value=value), self.assertRaises(size.SizeError):
                size.size(size.parse_weights({'AAPL': 1}), self.cfg(fractional=False), prices={'AAPL': value})

    def test_top_must_be_positive_integer(self):
        for value in [-1, 0, True]:
            with self.subTest(value=value), self.assertRaises(size.SizeError):
                size.size(size.parse_weights({'AAPL': 1, 'MSFT': 1}), self.cfg(), top=value)

    def test_many_fictional_allocations_preserve_money_and_caps(self):
        rng = random.Random(18102026)
        for i in range(180):
            budget = rng.randrange(20, 200000) / 100
            count = rng.randrange(2, 9)
            weights = {'TEST%d' % j: rng.randrange(1, 500) for j in range(count)}
            cfg = self.cfg(budget=budget, cash_buffer_pct=rng.randrange(0, 60),
                           max_position_pct=rng.randrange(15, 101))
            for cents in [False, True]:
                result = size.size(size.parse_weights(weights), cfg, cents=cents)
                with self.subTest(i=i, cents=cents):
                    self.assertLessEqual(result['invested'], result['investable'] + 1e-7)
                    self.assertGreaterEqual(result['idle'], 0)
                    self.assertAlmostEqual(result['invested'] + result['idle'] + result['cash_buffer'], budget, places=2)
                    self.assertTrue(all(math.isfinite(p['dollars']) and 0 <= p['dollars'] <= result['cap'] + 1e-7 for p in result['positions']))
                    self.assertLessEqual(result['cap'], budget * cfg['max_position_pct'] / 100 + 1e-7)
                    self.assertGreaterEqual(result['cash_buffer'] + 1e-7, budget * cfg['cash_buffer_pct'] / 100)

    def test_invalid_json_numeric_input_does_not_overwrite_orders(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, 'config.json'); out = os.path.join(d, 'orders.json')
            with open(cfg, 'w') as f: json.dump(self.cfg(budget=float('nan')), f)
            with open(out, 'w') as f: f.write('PRESERVE')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = size.main(['--config', cfg, '--weights', 'AAPL=1', '--write', out])
            self.assertEqual(code, 1)
            with open(out) as f: self.assertEqual(f.read(), 'PRESERVE')

    def test_malformed_config_is_clean_cli_error(self):
        with tempfile.TemporaryDirectory() as d:
            config = os.path.join(d, 'config.json')
            for data in ['{', '[]', '{"budget":100,"min_order_usd":-1}']:
                with open(config, 'w') as f: f.write(data)
                err = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(err):
                    code = size.main(['--config', config, '--weights', 'AAPL=1'])
                self.assertEqual(code, 1)
                self.assertNotIn('Traceback', err.getvalue())

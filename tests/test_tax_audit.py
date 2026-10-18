"""Lot-Bestandserhaltung, Randfälle und US-Wash-Sale-Modellgrenzen."""
import copy
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from _load import load
tax = load('trader-steuer', 'lots')
TODAY = dt.date(2026, 10, 18)


def lot(lid='old', shares=1, cost=150, date='2025-01-01'):
    return {'id': lid, 'shares': shares, 'cost': cost, 'date': date}


class TaxAudit(unittest.TestCase):
    def test_fractional_lots_are_not_rounded_up_or_destroyed_on_record(self):
        held = 1.23456789
        pf = {'cash': 0, 'positions': [{'ticker': 'AAPL', 'lots': [lot(shares=held, cost=200)]}]}
        candidates = tax.harvest(pf, {'AAPL': 100}, TODAY, min_loss=1)
        order = tax.harvest_orders(candidates)[0]
        self.assertLessEqual(float(order['qty']), held)
        sold = .2345678
        sale = tax.simulate_sale(tax.lots_of(pf)['AAPL'], sold, 100, TODAY)
        new, state = tax.record_sale(pf, 'AAPL', sale, TODAY, 100, {})
        left = tax.lots_of(new)['AAPL'][0]['shares']
        self.assertAlmostEqual(left + sold, held, places=10)
        self.assertAlmostEqual(state['realized'][0]['shares'], sold, places=10)

    def test_record_preserves_other_positions_with_inferred_shares(self):
        pf = {'cash': 0, 'positions': [{'symbol': 'AAPL', 'lots': [lot()]},
                                      {'ticker': 'MSFT', 'lots': [lot('msft', 3, 90)]}]}
        sale = tax.simulate_sale(tax.lots_of(pf)['AAPL'], .5, 100, TODAY)
        new, _ = tax.record_sale(pf, 'AAPL', sale, TODAY, 100, {})
        self.assertEqual({p.get('ticker', p.get('symbol')) for p in new['positions']}, {'AAPL', 'MSFT'})
        by = tax.lots_of(new)
        self.assertEqual(by['AAPL'][0]['shares'], .5)
        self.assertEqual(by['MSFT'][0]['shares'], 3)
        self.assertEqual(new['cash'], 50)

    def test_duplicate_specific_ids_cannot_sell_same_lot_twice(self):
        pf = {'positions': [{'ticker': 'AAPL', 'lots': [lot('a'), lot('b')]}]}
        with self.assertRaises(tax.TaxError):
            tax.simulate_sale(tax.lots_of(pf)['AAPL'], 1.5, 100, TODAY, 'specific', ['a', 'a'])

    def test_invalid_lot_numbers_and_ambiguous_ids_are_refused(self):
        bad = [lot(shares=-1), lot(shares=float('nan')), lot(cost=-1), lot(cost=float('inf')),
               lot(shares=True), lot(date='2026-10-18garbage')]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(tax.TaxError):
                tax.lots_of({'positions': [{'ticker': 'AAPL', 'lots': [item]}]})
        for positions in [[{'ticker': 'AAPL', 'lots': [lot(), lot()]}],
                          [{'ticker': 'AAPL', 'lots': [lot()]}, {'ticker': 'AAPL', 'lots': [lot('b')]}],
                          [{'ticker': 'AAPL', 'shares': 2, 'lots': [lot()]}]]:
            with self.subTest(positions=positions), self.assertRaises(tax.TaxError): tax.lots_of({'positions': positions})

    def test_invalid_sale_and_harvest_inputs_are_refused(self):
        pf = {'positions': [{'ticker': 'AAPL', 'lots': [lot()]}]}
        ls = tax.lots_of(pf)['AAPL']
        for shares in [0, -1, float('nan'), float('inf'), True]:
            with self.subTest(shares=shares), self.assertRaises(tax.TaxError): tax.simulate_sale(ls, shares, 100, TODAY)
        for price in [0, -1, float('nan'), float('inf')]:
            with self.subTest(price=price), self.assertRaises(tax.TaxError): tax.simulate_sale(ls, 1, price, TODAY)
        with self.assertRaises(tax.TaxError): tax.simulate_sale(ls, 1, 100, dt.date(2024, 12, 31))
        for minimum in [-1, float('nan'), float('inf')]:
            with self.subTest(minimum=minimum), self.assertRaises(tax.TaxError):
                tax.harvest(pf, {'AAPL': 100}, TODAY, min_loss=minimum)

    def test_shared_lot_id_across_share_classes_still_flags_replacement(self):
        pf = {'positions': [{'ticker': 'GOOGL', 'lots': [lot('same')]},
                            {'ticker': 'GOOG', 'lots': [lot('same', date='2026-10-10')]}]}
        sale = tax.simulate_sale(tax.lots_of(pf)['GOOGL'], 1, 100, TODAY)
        hits = tax.wash_check('GOOGL', TODAY, sale['lots'], pf, [], {'GOOGL': 100, 'GOOG': 100})
        self.assertEqual([h['ticker'] for h in hits], ['GOOG'])

    def test_thirty_day_window_is_inclusive(self):
        pf = {'positions': [{'ticker': 'AAPL', 'lots': [lot('minus30', date='2026-09-18'),
                                                      lot('minus31', date='2026-09-17'),
                                                      lot('plus30', date='2026-11-17'),
                                                      lot('plus31', date='2026-11-18')]}]}
        hits = tax.wash_check('AAPL', TODAY, [], pf, [], {'AAPL': 100})
        self.assertEqual({h['date'] for h in hits}, {'2026-09-18', '2026-11-17'})

    def test_net_profit_does_not_hide_loss_lot_wash_sale(self):
        pf = {'cash': 0, 'positions': [{'ticker': 'AAPL', 'lots': [lot('loss', cost=150),
              lot('gain', cost=0, date='2025-02-01'), lot('replacement', cost=100, date='2026-10-10')]}]}
        with tempfile.TemporaryDirectory() as d:
            pfpath = os.path.join(d, 'portfolio.json'); pxpath = os.path.join(d, 'prices.json')
            with open(pfpath, 'w') as f: json.dump(pf, f)
            with open(pxpath, 'w') as f: json.dump({'AAPL': 100}, f)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = tax.main(['sell', 'AAPL', '2', '--portfolio', pfpath, '--prices', pxpath,
                                 '--orders', os.path.join(d, 'none'), '--state', os.path.join(d, 'state'),
                                 '--today', TODAY.isoformat(), '--json'])
            self.assertEqual(code, 0)
            sale = json.loads(out.getvalue())
            self.assertEqual(sale['gain'], 50)
            self.assertEqual(sale['wash_disallowed'], 50)

    def test_partial_disallowance_uses_sold_lots_not_net_average(self):
        sale = {'lots': [{'shares': 1, 'gain': -50}, {'shares': 1, 'gain': -10}]}
        self.assertEqual(tax.sale_disallowed(sale, [{'shares': 1}]), 50)
        self.assertEqual(tax.sale_disallowed(sale, [{'shares': None}]), 60)

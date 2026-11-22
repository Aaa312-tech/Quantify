import unittest

import numpy as np
import pandas as pd

from quantfolio.dataset import label_mask, walk_forward_folds
from quantfolio.features import base_features, price_features
from quantfolio.ledger import causal_price_index
from quantfolio.market import LABEL_STATUS, gross_entitlement, legal_label
from tests.factories import STOCK_META, flat_bars


class TimingTests(unittest.TestCase):
    def test_minimum_exit_next_day_and_no_same_day_profit(self):
        dates, raw = flat_bars()
        raw.loc[dates[1], "close"] = 10.8
        y, end, status, delay = legal_label(STOCK_META, raw, dates, 0, 1, [])
        self.assertEqual(end, 2)
        self.assertEqual(status, 0)
        self.assertAlmostEqual(y, 0.0)

    def test_limit_down_delays_exit_and_end_time(self):
        dates, raw = flat_bars()
        raw.loc[dates[2], ["open", "close", "high", "low"]] = 9.0
        raw.loc[dates[3], ["open", "close", "high", "low"]] = 9.0
        y, end, status, delay = legal_label(STOCK_META, raw, dates, 0, 1, [])
        self.assertEqual((end, status, delay), (3, 0, 1))
        self.assertAlmostEqual(y, -0.1)

    def test_unbuyable_entry_is_not_fake_profit(self):
        dates, raw = flat_bars()
        raw.loc[dates[1], ["open", "close", "high", "low"]] = 11.0
        y, end, status, _ = legal_label(STOCK_META, raw, dates, 0, 1, [])
        self.assertTrue(np.isnan(y))
        self.assertEqual(status, LABEL_STATUS["NO_ENTRY"])

    def test_record_date_rights_and_no_ex_date_inheritance(self):
        dates, raw = flat_bars()
        action = {
            "record_date": str(dates[1].date()),
            "ex_date": str(dates[2].date()),
            "cash_per_share": 1.0,
            "share_multiplier": 1.0,
            "cash_pay_date": str(dates[8].date()),
        }
        raw.loc[dates[2] :, ["open", "close", "high", "low"]] = 9.0
        y, end, status, _ = legal_label(STOCK_META, raw, dates, 0, 1, [action])
        self.assertAlmostEqual(y, 0.0)
        self.assertEqual(status, 0)
        wealth, _, _ = gross_entitlement(dates[2], dates[3], 9.0, [action])
        self.assertEqual(wealth, 9.0)

    def test_bonus_shares_delay_full_exit_until_listing(self):
        dates, raw = flat_bars()
        action = {
            "record_date": str(dates[1].date()),
            "ex_date": str(dates[2].date()),
            "cash_per_share": 0.0,
            "share_multiplier": 2.0,
            "share_list_date": str(dates[4].date()),
        }
        raw.loc[dates[2] :, ["open", "close", "high", "low"]] = 5.0
        y, end, status, delay = legal_label(STOCK_META, raw, dates, 0, 1, [action])
        self.assertEqual((end, status, delay), (4, 0, 2))
        self.assertAlmostEqual(y, 0.0)

    def test_delayed_label_is_purged_across_fold_boundary(self):
        table = pd.DataFrame(
            {
                "date_index": [1, 1, 2],
                "eligible": [True] * 3,
                "end5": [8, 20, 9],
                "status5": [0] * 3,
                "y5": [0.1, 0.2, 0.3],
            }
        )
        self.assertEqual(label_mask(table, 5, 1, 2, 10).tolist(), [True, False, True])

    def test_calendar_split_not_individual_stock_rows(self):
        folds = walk_forward_folds(range(696))
        self.assertEqual(len(folds), 11)
        for f in folds:
            self.assertEqual(f["validation_start"] - f["train_end"] - 1, 6)
            self.assertEqual(f["test_start"] - f["validation_end"] - 1, 6)

    def test_future_prices_and_actions_do_not_change_prior_features(self):
        dates, raw = flat_bars(220)
        raw["close"] = 10 + np.arange(220) * 0.01
        raw["open"] = raw.close
        raw["high"] = raw.close + 0.1
        raw["low"] = raw.close - 0.1
        changed = raw.copy()
        changed.loc[dates[180] :, ["open", "close", "high", "low"]] *= 1.3
        future = {
            "record_date": str(dates[190].date()),
            "ex_date": str(dates[191].date()),
            "cash_per_share": 0.3,
            "share_multiplier": 1.0,
            "share_list_date": None,
        }
        frames = []
        for history, actions in ((raw, []), (changed, [future])):
            causal, splits = causal_price_index(history, actions)
            normalized = history.copy()
            normalized["volume_lots"] = history.volume_lots / splits
            prices = price_features(normalized, causal, causal.close.pct_change(fill_method=None))
            frames.append(base_features(history, prices))
        pd.testing.assert_frame_equal(frames[0].iloc[:180], frames[1].iloc[:180])


if __name__ == "__main__":
    unittest.main()

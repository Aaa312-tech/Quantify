import unittest

import numpy as np
import pandas as pd

from quantfolio.features import INDUSTRY_FEATURES, features
from quantfolio.models import candidate_scores, factor_check, neutral_scores, training_targets


class IndustryTests(unittest.TestCase):
    def table(self):
        return pd.DataFrame(
            {
                "date_index": np.repeat([0, 1], 12),
                "symbol": list("abcdefghijkl") * 2,
                "industry": list("aaaabbbbcccc") * 2,
                "momentum_percentile": [1.0] * 24,
                "momentum_score": list(range(12)) * 2,
                "eligible": [True] * 24,
                "y5": list(np.tile([0.01, 0.02, 0.03, 0.04], 3)) * 2,
                "status5": [0] * 24,
                "end5": [6] * 12 + [7] * 12,
            }
        )

    def test_label_industry_constant_shift_invariant(self):
        t = self.table()
        rows = np.arange(24)
        a = training_targets(t, rows)
        t.loc[t.industry == "b", "y5"] += 1
        b = training_targets(t, rows)
        np.testing.assert_array_equal(a[1], b[1])

    def test_unsupported_sector_not_trained(self):
        t = self.table()
        t.loc[[0, 1], "industry"] = "tiny"
        rows, _, w, info = training_targets(t, np.arange(24))
        self.assertNotIn(0, rows)
        self.assertNotIn(2, rows)
        self.assertAlmostEqual(w.mean(), 1.0)

    def test_future_label_does_not_change_train(self):
        t = self.table()
        a = training_targets(t, np.arange(12))
        t.loc[12:, "y5"] = 100
        b = training_targets(t, np.arange(12))
        np.testing.assert_array_equal(a[1], b[1])

    def test_prediction_industry_offsets_removed(self):
        t = self.table()
        x = np.arange(24, dtype=float)
        a, _ = neutral_scores(t, np.arange(24), x)
        x[t.industry == "a"] += 100
        b, _ = neutral_scores(t, np.arange(24), x)
        np.testing.assert_allclose(a, b)

    def test_outside_momentum_pool_unchanged(self):
        t = self.table()
        s = candidate_scores(
            t, np.arange(24), {"ridge": np.arange(24), "tree": np.arange(24)[::-1]}
        )
        low = s["momentum"] < 0.7
        for v in s.values():
            np.testing.assert_array_equal(v[low], s["momentum"][low])

    def test_factor_diagnostic_purges_unavailable_labels(self):
        t = self.table()
        a = factor_check(t, np.arange(24), np.arange(24), 7)
        t.loc[12:, "y5"] = 100
        b = factor_check(t, np.arange(24), np.arange(24), 7)
        self.assertEqual(a, b)
        self.assertEqual(a["last_label_end"], 6)

    def test_feature_prefix_is_causal(self):
        t = self.table()
        for c in INDUSTRY_FEATURES:
            t[c] = np.arange(24, dtype=float)
        meta = {s: {"industry": g} for s, g in zip(t.symbol, t.industry)}
        a, _ = features(t, meta)
        b, _ = features(t.iloc[:12], meta)
        pd.testing.assert_frame_equal(a.iloc[:12], b)

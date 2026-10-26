"""Focused checks for the model contract and timestamp feature logic."""

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service import DataUnavailable, FEATURES, prepare_features  # noqa: E402


class FeaturePreparationTests(unittest.TestCase):
    def setUp(self):
        self.times = pd.date_range("2024-03-01", periods=168, freq="h", tz="UTC")
        self.values = pd.Series(np.arange(168, dtype=float))
        self.temps = pd.Series(np.arange(168, dtype=float) / 10)

    def test_training_feature_order_and_shift_definitions(self):
        row = prepare_features(self.values, self.times, self.temps)
        self.assertEqual(list(row.columns), FEATURES)
        self.assertEqual(row.loc[0, "demanda_lag_1"], 167)
        self.assertEqual(row.loc[0, "demanda_lag_24"], 144)
        self.assertEqual(row.loc[0, "demanda_lag_168"], 0)
        self.assertEqual(row.loc[0, "media_24h"], np.mean(np.arange(144, 168)))
        self.assertEqual(row.loc[0, "temperature_2m"], 16.7)

    def test_gaps_are_rejected(self):
        times = self.times.delete(70)
        with self.assertRaises(DataUnavailable):
            prepare_features(self.values, times, self.temps)

    def test_non_finite_values_are_rejected(self):
        values = self.values.copy()
        values.iloc[-1] = np.nan
        with self.assertRaises(DataUnavailable):
            prepare_features(values, self.times, self.temps)


if __name__ == "__main__":
    unittest.main()

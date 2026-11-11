"""Prediction log against the existing `predictions` table, with a fake connection."""

from pathlib import Path
import sys
import unittest

import pandas as pd

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
from prediction_store import PredictionStore  # noqa: E402


class FakeDatabase:
    """Minimal stand-in for the `predictions` table and a psycopg2 connection."""

    def __init__(self, fail: bool = False):
        self.rows = {}
        self.fail = fail
        self.statements = []

    def connect(self):
        if self.fail:
            raise ConnectionError("could not connect to server")
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return FakeCursor(self.database)

    def commit(self):
        pass

    def close(self):
        pass


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)

    def execute(self, sql, params):
        db = self.database
        db.statements.append(sql.split()[0])
        if sql.lstrip().startswith("INSERT"):
            stamp, model, predicted = params
            key = (pd.Timestamp(stamp), model)
            actual = db.rows.get(key, (None, None))[1]
            db.rows[key] = (predicted, actual)
        elif sql.lstrip().startswith("UPDATE"):
            actual, stamp, model = params
            key = (pd.Timestamp(stamp), model)
            db.rows[key] = (db.rows[key][0], actual)
        elif "actual_demand IS NULL" in sql:
            model, until = params
            self.result = [(stamp,) for (stamp, name), (_, actual) in db.rows.items()
                           if name == model and actual is None and stamp <= until]
        else:
            model, since = params
            self.result = sorted((stamp, predicted, actual)
                                 for (stamp, name), (predicted, actual) in db.rows.items()
                                 if name == model and stamp >= since)

    def fetchall(self):
        return self.result


def forecast(mode: str, start: pd.Timestamp, values: list[float]) -> dict:
    return {"prediction_mode": mode, "predictions": [
        {"timestamp_utc": (start + pd.Timedelta(hours=step)).isoformat(), "demand_mw": value}
        for step, value in enumerate(values)]}


class PredictionStoreTests(unittest.TestCase):
    def setUp(self):
        self.database = FakeDatabase()
        self.store = PredictionStore("postgresql://user:secret@host/db",
                                     connect=self.database.connect)
        self.start = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=3)

    def test_without_database_url_nothing_is_stored_and_tracking_says_so(self):
        store = PredictionStore(None, connect=self.database.connect)
        self.assertEqual(store.save_forecast(forecast("current_forecast", self.start, [1])), 0)
        self.assertEqual(store.tracking()["configured"], False)
        self.assertEqual(self.database.statements, [])

    def test_historical_simulations_are_never_stored(self):
        saved = self.store.save_forecast(
            forecast("historical_simulation", self.start, [30_000, 31_000]))
        self.assertEqual(saved, 0)
        self.assertEqual(self.database.rows, {})

    def test_current_forecast_is_compared_with_real_demand_and_scored(self):
        saved = self.store.save_forecast(
            forecast("current_forecast", self.start, [30_000, 31_000, 32_000]))
        self.assertEqual(saved, 3)
        # A newer forecast for the same hour replaces the previous prediction.
        self.store.save_forecast(forecast("current_forecast", self.start, [29_000]))
        observed = pd.DataFrame({
            "timestamp_utc": [self.start, self.start + pd.Timedelta(hours=1)],
            "value": [30_000.0, 30_000.0]})
        self.assertEqual(self.store.record_actuals(observed), 2)
        tracking = self.store.tracking(7)
        self.assertTrue(tracking["available"])
        self.assertEqual(tracking["stored"], 3)
        self.assertEqual(tracking["records"][0]["predicted_mw"], 29_000)
        self.assertIsNone(tracking["records"][2]["actual_mw"])
        metrics = tracking["metrics"]
        self.assertEqual(metrics["compared_hours"], 2)
        self.assertAlmostEqual(metrics["mae_mw"], 1_000)
        self.assertAlmostEqual(metrics["mape_pct"], 100 * 1_000 / 30_000)

    def test_database_errors_degrade_without_leaking_the_connection_string(self):
        store = PredictionStore("postgresql://user:secret@host/db",
                                connect=FakeDatabase(fail=True).connect)
        self.assertEqual(store.save_forecast(
            forecast("current_forecast", self.start, [30_000])), 0)
        tracking = store.tracking()
        self.assertTrue(tracking["configured"])
        self.assertFalse(tracking["available"])
        self.assertIn("ConnectionError", tracking["error"])
        self.assertNotIn("secret", tracking["error"])


if __name__ == "__main__":
    unittest.main()

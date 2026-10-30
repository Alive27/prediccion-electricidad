"""Focused checks for the model contract and timestamp feature logic."""

from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service import (DataUnavailable, DemandService, FEATURES, ModelUnavailable,  # noqa: E402
                     PRICE_FEATURES, PriceService, load_price_model, prepare_features)  # noqa: E402


class PriceServiceTests(unittest.TestCase):
    class Model:
        feature_names_in_ = np.array(PRICE_FEATURES)
        n_features_in_ = 4

        def predict(self, frame):
            self.features = frame.copy()
            return np.arange(len(frame), dtype=float) + 50

    def test_existing_artifact_loads_with_expected_schema(self):
        path = Path(__file__).resolve().parents[3] / "models" / "modelo_precio_v01.joblib"
        model, info = load_price_model(path)
        self.assertEqual(type(model).__name__, "RandomForestRegressor")
        self.assertEqual(info["features"], PRICE_FEATURES)
        self.assertEqual(info["unit"], "€/MWh")

    def test_prediction_horizons_and_local_calendar_features(self):
        model = self.Model()
        service = PriceService(model, {"loaded": True})
        for horizon in (6, 12, 24, 48):
            result = service.predict(horizon)
            self.assertEqual(len(result["records"]), horizon)
            self.assertEqual(list(model.features.columns), PRICE_FEATURES)
            self.assertEqual(result["prediction_type"], "model_estimate")
            self.assertTrue(np.isfinite([r["price_eur_mwh"] for r in result["records"]]).all())

    def test_model_absence_and_invalid_horizon_are_errors(self):
        with self.assertRaises(ModelUnavailable):
            PriceService(None, {"error": "missing artifact"}).predict(6)
        with self.assertRaises(ValueError):
            PriceService(self.Model(), {}).predict(5)

    def test_history_validates_pvpc_indicator_geo_unit_and_sorts(self):
        current_hour = pd.Timestamp.now(tz="UTC").floor("h")
        payload = {"indicator": {
            "id": 1001, "name": "Término de facturación PVPC Península",
            "magnitud": [{"name": "Precio", "unit": "€/MWh"}],
            "geos": [{"geo_id": 8741, "geo_name": "Península"}],
            "values": [
                {"datetime_utc": (current_hour - pd.Timedelta(hours=1)).isoformat(),
                 "geo_id": 8741, "value": 1.0},
                {"datetime_utc": current_hour.isoformat(), "geo_id": 8741, "value": 2.0},
            ],
        }}
        response = Mock(status_code=200)
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        service = PriceService(None, {}, "unit-test-token")
        with patch("service.requests.get", return_value=response):
            result = service.history(1)
        self.assertEqual(result["indicator_id"], 1001)
        self.assertEqual(result["unit"], "€/MWh")
        self.assertEqual([row["price_eur_mwh"] for row in result["records"]], [1.0, 2.0])

    def test_history_failure_is_explicit_and_never_fabricates_data(self):
        service = PriceService(None, {}, "unit-test-token")
        with patch("service.requests.get", side_effect=requests.ConnectionError):
            result = service.history(1)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["records"], [])


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


class DemandServiceTests(unittest.TestCase):
    class ConstantModel:
        def predict(self, features):
            if list(features.columns) != FEATURES:
                raise AssertionError("El orden de variables cambió.")
            return np.array([32_000.0])

    def make_service(self, data_dir=Path("/no-local-data")):
        service = DemandService(self.ConstantModel(), {"model_type": "TestModel"}, data_dir)
        service.live_cache_path = None
        service.esios_cache_dir = data_dir / "test-esios-cache"
        return service

    def test_redata_evolution_uses_peninsular_scope_and_validates_observations(self):
        service = self.make_service()
        now = pd.Timestamp.now(tz="UTC").floor("h")
        values = [
            {"datetime": (now - pd.Timedelta(hours=2)).isoformat(), "value": 30_000},
            {"datetime": (now - pd.Timedelta(hours=1)).isoformat(), "value": 31_000},
            {"datetime": now.isoformat(), "value": 32_000},
            {"datetime": now.isoformat(), "value": 32_100},
            {"datetime": (now + pd.Timedelta(hours=1)).isoformat(), "value": 90_000},
            {"datetime": now.isoformat(), "value": float("inf")},
            {"datetime": now.isoformat(), "value": -1},
        ]
        response = Mock()
        response.json.return_value = {
            "data": {"attributes": {
                "title": "Evolución de la demanda", "last-update": now.isoformat()}},
            "included": [{"attributes": {
                "title": "Demanda", "magnitude": None,
                "last-update": now.isoformat(), "values": values,
            }}],
        }
        service.local_demand = lambda: pd.DataFrame(columns=["timestamp_utc", "value"])
        with patch.object(service.session, "get", return_value=response) as get:
            demand = service.demand_data()
        query = get.call_args.kwargs["params"]
        self.assertEqual(query["geo_limit"], "peninsular")
        self.assertEqual(query["geo_trunc"], "electric_system")
        self.assertEqual(query["geo_ids"], "8741")
        self.assertEqual(get.call_args.args[0],
                         "https://apidatos.ree.es/es/datos/demanda/evolucion")
        self.assertEqual(len(demand), 3)
        self.assertEqual(demand.value.iloc[-1], 32_100)
        self.assertEqual(demand.timestamp_utc.max(), now)
        self.assertEqual(service.demand_source_status, "connected")
        self.assertIsNotNone(service.demand_source_updated_at.tzinfo)

    def test_esios_curves_parse_official_ids_and_retain_native_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            service.esios_token = "test-only-token"
            now = pd.Timestamp.now(tz="UTC").floor("5min")

            def response_for(url, **kwargs):
                indicator_id = int(url.rsplit("/", 1)[-1])
                name = (
                    "Demanda real Peninsular"
                    if indicator_id == 1293 else "Demanda prevista Peninsular")
                body = {"indicator": {
                    "id": indicator_id, "name": name, "values_updated_at": now.isoformat(),
                    "magnitud": [{"name": "Potencia", "id": 23}],
                    "geos": [{"geo_id": 8741, "geo_name": "Península"}],
                    "values": [{"datetime_utc": (now - pd.Timedelta(minutes=5 * n)).isoformat(),
                                "value": 30_000 + n} for n in reversed(range(4))]}}
                mock = Mock()
                mock.json.return_value = body
                return mock
            with patch.object(service.session, "get", side_effect=response_for) as get:
                curves = service.esios_curves(hours=1)
            self.assertEqual(get.call_count, 2)
            self.assertIn("x-api-key", get.call_args.kwargs["headers"])
            self.assertEqual(curves["curves"]["real"]["indicator_id"], 1293)
            self.assertEqual(curves["curves"]["official_forecast"]["indicator_id"], 544)
            self.assertEqual(curves["curves"]["real"]["resolution_minutes"], 5)
            self.assertEqual(len(curves["curves"]["real"]["records"]), 4)

    def test_esios_missing_token_does_not_block_or_claim_connection(self):
        service = self.make_service()
        service.esios_token = ""
        curves = service.esios_curves(hours=24)
        self.assertEqual(curves["curves"]["real"]["status"], "not_configured")
        self.assertIn("ESIOS_API_TOKEN", curves["curves"]["real"]["error"])
        self.assertEqual(curves["curves"]["real"]["records"], [])

    def test_esios_cached_values_are_marked_as_cache_when_token_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            service.esios_cache_dir.mkdir(parents=True)
            now = pd.Timestamp.now(tz="UTC").floor("5min")
            path = service.esios_cache_dir / "esios_real.parquet"
            pd.DataFrame({"timestamp_utc": [now], "value": [30_000.]}).to_parquet(path)
            path.with_suffix(".json").write_text(json.dumps({
                "status": "connected",
                "last_success_at_utc": (now - pd.Timedelta(hours=1)).isoformat(),
                "resolution_minutes": 5.0,
            }), encoding="utf-8")
            service.esios_token = ""
            result = service.esios_curves(24)["curves"]["real"]
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual(result["data_origin"], "cache")
        self.assertEqual(result["last_success_at_utc"],
                         (now - pd.Timedelta(hours=1)).isoformat())

    def test_source_comparison_uses_exact_utc_timestamps(self):
        service = self.make_service()
        stamp = pd.Timestamp.now(tz="UTC").floor("h")
        service.demand_source_status = "connected"
        service.demand_data = lambda: pd.DataFrame({
            "timestamp_utc": [stamp, stamp + pd.Timedelta(minutes=5)],
            "value": [30_000., 30_100.]})
        service.esios_curves = lambda hours: {
            "curves": {
                "real": {
                    "status": "connected",
                    "records": [{"timestamp_utc": stamp.isoformat(), "demand_mw": 30_500.}],
                }
            }
        }
        comparison = service.compare_demand_sources(48)
        self.assertEqual(len(comparison["records"]), 1)
        self.assertEqual(comparison["records"][0]["difference_mw"], -500.)

    def test_successful_query_time_is_not_overwritten_by_old_cache_metadata(self):
        service = self.make_service()
        now = pd.Timestamp.now(tz="UTC").floor("h")
        response = Mock()
        response.json.return_value = {
            "data": {"attributes": {
                "title": "Evolución de la demanda", "last-update": now.isoformat()}},
            "included": [{"attributes": {"title": "Demanda", "values": [
                {"datetime": now.isoformat(), "value": 30_000.0},
            ]}}],
        }
        service.local_demand = lambda: pd.DataFrame(columns=["timestamp_utc", "value"])
        with tempfile.TemporaryDirectory() as folder:
            service.live_cache_path = Path(folder) / "live.parquet"
            pd.DataFrame({"timestamp_utc": [now - pd.Timedelta(days=1)],
                          "value": [29_000.]}).to_parquet(service.live_cache_path)
            old_success = (now - pd.Timedelta(days=1)).isoformat()
            service.live_cache_path.with_suffix(".json").write_text(json.dumps({
                "source_last_success_at_utc": old_success,
                "source_updated_at_utc": old_success,
            }), encoding="utf-8")
            with patch.object(service.session, "get", return_value=response):
                service.demand_data()
            metadata = json.loads(service.live_cache_path.with_suffix(".json").read_text(
                encoding="utf-8"))
        self.assertEqual(service.demand_source_last_success_at, service.demand_source_checked_at)
        self.assertGreater(service.demand_source_last_success_at, pd.Timestamp(old_success))
        self.assertEqual(metadata["source_last_success_at_utc"],
                         service.demand_source_last_success_at.isoformat())

    def test_source_error_keeps_historical_values_labeled_and_not_fresh(self):
        service = self.make_service()
        history = pd.DataFrame({
            "timestamp_utc": pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC"),
            "value": [10_000.0, 11_000.0],
        })
        service.local_demand = lambda: history.copy()
        with patch.object(service.session, "get", side_effect=requests.ConnectionError):
            demand = service.demand_data()
        health = service.health()
        self.assertEqual(len(demand), 2)
        self.assertEqual(service.demand_source_status, "unavailable")
        self.assertEqual(service.demand_source, "Histórico local de 2024")
        self.assertFalse(health["demand_fresh"])
        self.assertFalse(health["future_forecast_available"])
        self.assertIsNone(health["demand_delta_mw"])

    def test_redata_http_502_retries_once_and_preserves_official_detail(self):
        service = self.make_service()
        response = Mock(status_code=502)
        response.raise_for_status.side_effect = requests.HTTPError(
            "502 Bad Gateway", response=response)
        response.json.return_value = {"errors": [{
            "status": "502", "detail": "Los datos no están disponibles."}]}
        service.local_demand = lambda: pd.DataFrame(columns=["timestamp_utc", "value"])
        with patch.object(service.session, "get", return_value=response) as get:
            service.demand_data()
        self.assertEqual(get.call_count, 2)
        self.assertIn("HTTP 502", service.demand_source_error)
        self.assertIn("Los datos no están disponibles", service.demand_source_error)

    def test_predict_builds_48_consecutive_hours_from_last_observation(self):
        service = self.make_service()
        origin = pd.Timestamp.now(tz="UTC").floor("h")
        history_times = pd.date_range(end=origin, periods=168, freq="h")
        weather_times = pd.date_range(start=history_times[0], periods=216, freq="h")
        demand = pd.DataFrame({"timestamp_utc": history_times,
                               "value": np.linspace(30_000, 31_000, 168)})
        weather = pd.DataFrame({"timestamp_utc": weather_times,
                                "temperature_2m": np.linspace(15, 18, len(weather_times))})
        service.demand_data = lambda: demand.copy()
        service.weather_data = lambda: weather.copy()
        for horizon in (6, 12, 24, 48):
            with self.subTest(horizon=horizon):
                forecast = service.predict(horizon)
                stamps = pd.to_datetime([row["timestamp_utc"] for row in forecast["predictions"]],
                                        utc=True)
                self.assertEqual(len(forecast["predictions"]), horizon)
                self.assertEqual(forecast["forecast_origin_utc"], origin.isoformat())
                self.assertTrue((stamps.to_series().diff().dropna() == pd.Timedelta(hours=1)).all())
                self.assertEqual(stamps[0], origin + pd.Timedelta(hours=1))
                self.assertEqual(stamps[-1], origin + pd.Timedelta(hours=horizon))
                self.assertTrue(
                    np.isfinite(
                        [row["demand_mw"] for row in forecast["predictions"]]
                    ).all())
                self.assertEqual(forecast["prediction_mode"], "current_forecast")

    def test_stale_data_requires_explicit_historical_simulation_and_returns_48(self):
        service = self.make_service()
        origin = pd.Timestamp("2024-12-31T22:00:00Z")
        history_times = pd.date_range(end=origin, periods=168, freq="h")
        demand = pd.DataFrame({"timestamp_utc": history_times,
                               "value": np.linspace(30_000, 31_000, 168)})
        weather = pd.DataFrame({"timestamp_utc": history_times,
                                "temperature_2m": np.linspace(5, 10, 168)})
        future_times = pd.date_range(start=origin + pd.Timedelta(hours=1), periods=48, freq="h")
        service.demand_data = lambda: demand.copy()
        service.weather_data = lambda: weather.copy()
        service.archived_weather = lambda start, end: pd.DataFrame({
            "timestamp_utc": future_times, "temperature_2m": np.linspace(7, 11, 48)})
        with self.assertRaisesRegex(DataUnavailable, "Confirma explícitamente"):
            service.predict(48)
        forecast = service.predict(48, allow_stale_simulation=True)
        stamps = pd.to_datetime([row["timestamp_utc"] for row in forecast["predictions"]],
                                utc=True)
        self.assertEqual(forecast["prediction_mode"], "historical_simulation")
        self.assertEqual(forecast["simulation_reference_utc"], origin.isoformat())
        self.assertEqual(len(stamps), 48)
        self.assertEqual(stamps[-1], origin + pd.Timedelta(hours=48))

    def test_redata_failure_with_valid_historical_inputs_allows_warned_simulation(self):
        service = self.make_service()
        origin = pd.Timestamp("2024-12-31T22:00:00Z")
        history_times = pd.date_range(end=origin, periods=168, freq="h")
        demand = pd.DataFrame({"timestamp_utc": history_times,
                               "value": np.linspace(30_000, 31_000, 168)})
        weather = pd.DataFrame({"timestamp_utc": history_times,
                                "temperature_2m": np.linspace(5, 10, 168)})
        future_times = pd.date_range(start=origin + pd.Timedelta(hours=1), periods=48, freq="h")
        service.local_demand = lambda: demand.copy()
        service.weather_data = lambda: weather.copy()
        service.archived_weather = lambda start, end: pd.DataFrame({
            "timestamp_utc": future_times, "temperature_2m": np.linspace(7, 11, 48)})
        with patch.object(service.session, "get", side_effect=requests.ConnectionError):
            service.demand_data()
            status = service.health()
            forecast = service.predict(48, allow_stale_simulation=True)
        self.assertEqual(service.demand_source_status, "unavailable")
        self.assertIn("ConnectionError", status["source_notice"])
        self.assertTrue(status["historical_simulation_available"])
        self.assertEqual(forecast["prediction_mode"], "historical_simulation")
        self.assertEqual(len(forecast["predictions"]), 48)

    def test_insufficient_historical_demand_blocks_simulation(self):
        service = self.make_service()
        times = pd.date_range("2024-12-25", periods=167, freq="h", tz="UTC")
        service.demand_data = lambda: pd.DataFrame({"timestamp_utc": times, "value": 30_000.})
        service.weather_data = lambda: pd.DataFrame({
            "timestamp_utc": times, "temperature_2m": 10.})
        with self.assertRaisesRegex(DataUnavailable, "Solo hay 167 horas"):
            service.predict(48, allow_stale_simulation=True)


if __name__ == "__main__":
    unittest.main()

"""HTTP contract smoke checks using the real artifact and local historical files."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_DIR.parents[1]
sys.path.insert(0, str(API_DIR))
import main as api  # noqa: E402


@unittest.skipUnless(
    (PROJECT_ROOT / "data" / "raw" / "redata_demanda_2024.parquet").is_file()
    and (PROJECT_ROOT / "data" / "raw" / "openmeteo_madrid_2024.parquet").is_file(),
    "Las pruebas HTTP con datos reales requieren los Parquet locales ignorados por Git.",
)
class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model_patch = patch.object(
            api, "MODEL_PATH", PROJECT_ROOT / "models" / "modelo_demanda_v01.joblib")
        cls.data_patch = patch.object(api, "DATA_DIR", PROJECT_ROOT / "data" / "raw")
        cls.model_patch.start()
        cls.data_patch.start()
        cls.client = TestClient(api.app)
        cls.client.__enter__()
        cls.service = api.app.state.service
        cls.service.demand_data = cls.service.local_demand
        cls.service.weather_data = cls.service.local_weather

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.model_patch.stop()
        cls.data_patch.stop()

    def test_model_and_health_report_verified_stale_artifact(self):
        info = self.client.get("/model/info")
        self.assertEqual(info.status_code, 200)
        self.assertEqual(info.json()["model_type"], "RandomForestRegressor")
        self.assertEqual(len(info.json()["features"]), 8)
        liveness = self.client.get("/health").json()
        self.assertEqual(liveness["status"], "alive")
        self.assertTrue(liveness["api"])
        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json()["detail"]["last_demand_utc"],
                         "2024-12-31T22:00:00+00:00")
        self.assertFalse(ready.json()["detail"]["future_forecast_available"])

    def test_history_and_prediction_validation(self):
        history = self.client.get("/history", params={"limit": 2})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()["records"]), 2)
        invalid = self.client.post("/predict", json={"horizon_hours": 49})
        self.assertEqual(invalid.status_code, 422)
        stale = self.client.post("/predict", json={"horizon_hours": 5})
        self.assertEqual(stale.status_code, 503)

    def test_curves_endpoint_reports_missing_esios_credential_without_hiding_it(self):
        self.service.esios_token = ""
        curves = self.client.get("/curves", params={"hours": 24})
        self.assertEqual(curves.status_code, 200)
        self.assertEqual(curves.json()["curves"]["real"]["status"], "not_configured")
        self.assertIn("ESIOS_API_TOKEN", curves.json()["curves"]["real"]["error"])

    def test_unloaded_model_is_reported_without_blocking_liveness(self):
        service = api.app.state.service
        api.app.state.service = None
        api.app.state.model_error = "No se pudo cargar el modelo (ValueError): faltan variables."
        try:
            self.assertEqual(self.client.get("/health").status_code, 200)
            response = self.client.get("/ready")
            self.assertEqual(response.status_code, 503)
            self.assertIn("faltan variables", response.json()["detail"]["reason"])
        finally:
            api.app.state.service = service

    def test_historical_evaluation_uses_real_model(self):
        evaluation = self.client.get("/evaluation")
        self.assertEqual(evaluation.status_code, 200)
        self.assertEqual(evaluation.json()["test_records"], 1723)
        self.assertAlmostEqual(evaluation.json()["mae_mw"], 500.2053, places=2)
        horizons = evaluation.json()["horizon_metrics"]
        self.assertEqual([item["horizon_hours"] for item in horizons], [1, 6, 12, 24, 48])
        self.assertTrue(all("peak_mae_mw" in item for item in horizons))


if __name__ == "__main__":
    unittest.main()

"""Regression checks using the real Parquet sources, without writing to data/."""

import io
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import requests
from streamlit.testing.v1 import AppTest

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))

from components.layout import PAGES  # noqa: E402
from components.tables import csv_bytes  # noqa: E402
from services.analysis import select_dates, select_period  # noqa: E402
from services.data_loader import load_local, load_upload, refresh  # noqa: E402
from services.data_quality import aligned_data, prepare  # noqa: E402
from utils.paths import FILENAMES, candidate_paths  # noqa: E402
from views.prices import _frame as price_prediction_frame  # noqa: E402
from views.prices import best_window, price_bands  # noqa: E402


class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = DIRECTORY.parents[1] / "data" / "raw"
        cls.sources = {
            kind: pd.read_parquet(cls.directory / filename)
            for kind, filename in FILENAMES.items()
        }
        cls.datasets = {kind: prepare(kind, raw, "test") for kind, raw in cls.sources.items()}

    def test_original_values_and_timestamps_are_preserved(self):
        for kind, dataset in self.datasets.items():
            pd.testing.assert_frame_equal(dataset.raw, self.sources[kind])
            self.assertEqual(dataset.quality["records"], 8784)
            self.assertEqual(dataset.quality["timezone"], "UTC")
            self.assertEqual(dataset.quality["step"], pd.Timedelta(hours=1))
            self.assertEqual(sum(dataset.quality["nulls"].values()), 0)
            self.assertEqual(dataset.quality["duplicate_times"], 0)
            self.assertEqual(dataset.quality["missing_slots"], 0)

    def test_price_prediction_timestamps_keep_both_repeated_local_dst_hours(self):
        frame = price_prediction_frame([
            {"timestamp_utc": "2024-10-27T00:00:00Z", "price_eur_mwh": 80.0},
            {"timestamp_utc": "2024-10-27T01:00:00Z", "price_eur_mwh": 75.0},
        ])
        local = frame.timestamp_utc.dt.tz_convert("Europe/Madrid")
        self.assertEqual(len(frame), 2)
        self.assertEqual(local.dt.hour.tolist(), [2, 2])
        self.assertNotEqual(local.iloc[0].utcoffset(), local.iloc[1].utcoffset())

    def test_alignment_uses_exact_instants_and_leaves_edges_unmatched(self):
        joined, _ = aligned_data(self.datasets["demand"], self.datasets["weather"])
        self.assertEqual(len(joined), 8783)
        left = self.sources["demand"].set_index("datetime").value
        right = self.sources["weather"].set_index("time").temperature_2m
        for column, source in (("value_demand", left), ("value_weather", right)):
            self.assertEqual(joined.iloc[0][column], source.loc[joined.iloc[0].timestamp])

    def test_naive_dates_are_never_assumed_to_be_utc(self):
        raw = self.sources["weather"].copy()
        raw["time"] = raw.time.dt.tz_localize(None)
        dataset = prepare("weather", raw, "test")
        self.assertIsNone(dataset.quality["timezone"])
        self.assertEqual(dataset.frame.timestamp.iloc[0], raw.time.iloc[0])
        joined, message = aligned_data(self.datasets["demand"], dataset)
        self.assertTrue(joined.empty)
        self.assertIn("pendiente", message)

    def test_duplicate_instants_block_joint_analysis(self):
        raw = pd.concat([self.sources["weather"], self.sources["weather"].iloc[:1]])
        dataset = prepare("weather", raw, "test")
        self.assertEqual(dataset.quality["duplicate_times"], 1)
        self.assertTrue(aligned_data(self.datasets["demand"], dataset)[0].empty)
        self.assertTrue(dataset.frame.index.is_unique)

    def test_invalid_values_are_excluded_without_changing_raw(self):
        raw = self.sources["demand"].copy()
        raw.loc[0, "value"] = float("inf")
        raw.loc[1, "value"] = -1
        raw.loc[2, "datetime"] = pd.NaT
        dataset = prepare("demand", raw, "test")
        self.assertEqual(dataset.quality["invalid_records"], 3)
        self.assertEqual(len(dataset.frame), len(raw) - 3)
        pd.testing.assert_frame_equal(dataset.raw, raw)

    def test_schema_mismatch_and_empty_file_are_handled(self):
        self.assertFalse(prepare("demand", self.sources["weather"], "test").available)
        self.assertFalse(prepare("demand", self.sources["demand"].iloc[:0], "test").available)

    def test_quick_filters_use_last_available_observation(self):
        frame = self.datasets["demand"].frame
        for period, expected in (("24 horas", 24), ("7 días", 168), ("30 días", 720)):
            result = select_period(frame, period)
            self.assertEqual(len(result), expected)
            self.assertEqual(result.timestamp.max(), frame.timestamp.max())

    def test_local_calendar_dates_respect_dst(self):
        frame = self.datasets["demand"].frame
        spring = select_dates(frame, (date(2024, 3, 31), date(2024, 3, 31)))
        autumn = select_dates(frame, (date(2024, 10, 27), date(2024, 10, 27)))
        self.assertEqual(len(spring), 23)
        self.assertEqual(len(autumn), 25)
        self.assertEqual(autumn.timestamp.dt.hour.eq(2).sum(), 2)
        self.assertTrue(autumn.timestamp.is_unique)

    def test_upload_reads_real_bytes_without_any_file_write(self):
        content = (self.directory / FILENAMES["demand"]).read_bytes()
        with patch("pathlib.Path.write_bytes", side_effect=AssertionError("disk write")):
            dataset = load_upload("demand", content, ())
        self.assertTrue(dataset.available)
        pd.testing.assert_frame_equal(dataset.raw, self.sources["demand"])

    def test_corrupt_upload_is_a_handled_error(self):
        dataset = load_upload("demand", b"not a parquet file", ())
        self.assertFalse(dataset.available)
        self.assertTrue(dataset.errors)

    def test_missing_file_keeps_checked_paths(self):
        absent = DIRECTORY / "nonexistent" / FILENAMES["demand"]
        with patch("services.data_loader.candidate_paths", return_value=(absent,)):
            dataset = load_local("demand")
        self.assertFalse(dataset.available)
        self.assertEqual(dataset.checked_paths, (str(absent),))
        self.assertFalse(dataset.errors)

    def test_optional_environment_path_takes_precedence(self):
        with patch.dict("os.environ", {"DASHBOARD_DATA_DIR": str(self.directory)}):
            self.assertEqual(candidate_paths("demand")[0], self.directory / FILENAMES["demand"])

    def test_second_load_reuses_parquet_cache(self):
        refresh()
        with patch("services.data_loader.pd.read_parquet", wraps=pd.read_parquet) as reader:
            load_local("demand")
            load_local("demand")
            self.assertEqual(reader.call_count, 1)
        refresh()

    def test_csv_keeps_original_data_and_timezone(self):
        raw = self.sources["demand"].iloc[:3]
        result = pd.read_csv(io.BytesIO(csv_bytes(raw)), sep=";", decimal=",")
        self.assertEqual(result.datetime.iloc[0], str(raw.datetime.iloc[0]))
        self.assertAlmostEqual(result.value.iloc[0], raw.value.iloc[0])


class ViewTests(unittest.TestCase):
    def _price_forecast(self, horizon):
        origin = pd.Timestamp.now(tz="Europe/Madrid").floor("h")
        return {
            "horizon_hours": horizon,
            "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "records": [
                {"timestamp_utc": (origin.tz_convert("UTC")
                                   + pd.Timedelta(hours=step)).isoformat(),
                 "price_eur_mwh": 60.0 + step}
                for step in range(1, horizon + 1)
            ],
        }

    def _demand_status(self):
        origin = pd.Timestamp.now(tz="UTC").floor("h")
        return {
            "api": True, "model_loaded": True, "model_ready": True,
            "model_schema_valid": True,
            "data_available": True, "last_demand_utc": origin.isoformat(),
            "last_demand_mw": 31_000.0, "demand_delta_mw": 100.0,
            "demand_fresh": True, "demand_source_status": "connected",
            "future_forecast_available": True, "source_notice": None,
        }

    def _demand_forecast(self, horizon=48):
        origin = pd.Timestamp(self._demand_status()["last_demand_utc"])
        return {
            "model": "RandomForestRegressor", "horizon_hours": horizon,
            "forecast_origin_utc": origin.isoformat(),
            "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "prediction_mode": "current_forecast",
            "predictions": [
                {"timestamp_utc": (origin + pd.Timedelta(hours=step)).isoformat(),
                 "demand_mw": 31_000.0 + step}
                for step in range(1, horizon + 1)
            ],
        }

    def test_dashboard_summary_uses_api_and_embeds_the_3d_city(self):
        with patch("services.api_client.cached_status", return_value=self._demand_status()), \
                patch("services.api_client.cached_model_info", return_value={
                    "model_type": "RandomForestRegressor"}), \
                patch("services.api_client.post",
                      side_effect=lambda path, body: self._demand_forecast(
                          body["horizon_hours"])) as demand, \
                patch("services.api_client.cached_price_history",
                      return_value={"status": "not_configured", "records": []}), \
                patch("services.api_client.predict_price", return_value={
                    "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                    "records": [{"price_eur_mwh": 80.0,
                                 "timestamp_local": "2026-11-02T10:00:00+01:00"}],
                }), \
                patch("components.city_scene.st.iframe") as city:
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
        self.assertFalse(app.exception)
        self.assertEqual(PAGES, ["Resumen", "Modelo", "Precios", "Datos", "Estado del sistema"])
        self.assertEqual(app.session_state["current_page"], "Resumen")
        self.assertEqual(demand.call_args.args[0], "/predict")
        self.assertEqual(demand.call_args.args[1], {"horizon_hours": 48})
        self.assertTrue(city.called)
        self.assertTrue(any("intensidad luminosa" in item.value.lower()
                            for item in app.markdown))

    def test_all_price_horizons_refresh_the_model_output(self):
        with patch("services.api_client.predict_price",
                   side_effect=self._price_forecast) as predict:
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Precios"
            app.run()
            self.assertEqual(predict.call_count, 0)
            self.assertEqual(app.button(key="price_generate").label, "Generar previsión 24 h")
            for horizon in (6, 12, 24, 48):
                app.selectbox(key="price_horizon").set_value(horizon).run()
                app.button(key="price_generate").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.get("plotly_chart")), 1)
                self.assertEqual(app.session_state["price_forecast_horizon"], horizon)
                markup = " ".join(item.value for item in app.markdown)
                self.assertIn("price-table", markup)
                self.assertIn("Valle", markup)
                self.assertIn("Punta", markup)
                self.assertTrue(any("€/kWh" in metric.value for metric in app.metric))
        self.assertEqual([call.args[0] for call in predict.call_args_list], [6, 12, 24, 48])

    def test_price_generation_persists_when_switching_pages(self):
        with patch("services.api_client.predict_price",
                   side_effect=self._price_forecast) as predict, \
                patch("services.api_client.cached_price_model",
                      side_effect=requests.ConnectionError("down")), \
                patch("services.api_client.cached_status",
                      side_effect=requests.ConnectionError("down")):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Precios"
            app.run()
            app.selectbox(key="price_horizon").set_value(12).run()
            app.button(key="price_generate").click().run()
            app.session_state["current_page"] = "Datos"
            app.run()
            app.session_state["current_page"] = "Precios"
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(predict.call_count, 1)
        self.assertEqual(app.selectbox(key="price_horizon").value, 12)
        self.assertTrue(any("price-table" in item.value for item in app.markdown))

    def test_demand_generation_persists_after_visiting_the_summary(self):
        with patch("services.api_client.cached_status", return_value=self._demand_status()), \
                patch("services.api_client.cached_model_info", return_value={
                    "model_type": "RandomForestRegressor"}), \
                patch("services.api_client.post",
                      side_effect=lambda path, body: self._demand_forecast(
                          body["horizon_hours"])), \
                patch("services.api_client.cached_history", return_value={"records": []}), \
                patch("services.api_client.cached_curves", return_value={"curves": {}}), \
                patch("services.api_client.cached_history_between",
                      return_value={"records": []}), \
                patch("services.api_client.cached_price_history",
                      return_value={"status": "not_configured", "records": []}), \
                patch("services.api_client.predict_price",
                      side_effect=self._price_forecast), \
                patch("components.city_scene.st.iframe"):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Modelo"
            app.run()
            app.selectbox(key="prediction_horizon").set_value(12).run()
            app.button(key="prediction_generate").click().run()
            app.session_state["current_page"] = "Resumen"
            app.run()
            app.session_state["current_page"] = "Modelo"
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["model_demand_forecast"]["horizon_hours"], 12)
        self.assertEqual(app.session_state["overview_demand_forecast"]["horizon_hours"], 48)
        self.assertEqual(app.selectbox(key="prediction_horizon").value, 12)
        self.assertTrue(any("Máximo previsto" in metric.label for metric in app.metric))

    def test_model_page_tracks_logged_forecasts_against_real_demand(self):
        origin = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=2)
        tracking = {
            "configured": True, "available": True, "error": None, "stored": 3,
            "records": [
                {"timestamp_utc": (origin + pd.Timedelta(hours=step)).isoformat(),
                 "predicted_mw": 30_000.0 + step, "actual_mw": None if step == 2 else 29_000.0}
                for step in range(3)],
            "metrics": {"compared_hours": 2, "mae_mw": 1_000.5, "mape_pct": 3.45},
        }
        with patch("services.api_client.cached_status", return_value=self._demand_status()), \
                patch("services.api_client.cached_model_info", return_value={
                    "model_type": "RandomForestRegressor"}), \
                patch("services.api_client.cached_tracking", return_value=tracking):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Modelo"
            app.run()
        self.assertFalse(app.exception)
        markup = " ".join(item.value for item in app.markdown)
        self.assertIn("Seguimiento del modelo", markup)
        self.assertIn("1.000 MW", markup)
        self.assertIn("3,5 %", markup)
        self.assertEqual(len(app.get("plotly_chart")), 1)

    def test_model_page_explains_when_the_prediction_log_is_not_configured(self):
        with patch("services.api_client.cached_status", return_value=self._demand_status()), \
                patch("services.api_client.cached_model_info", return_value={}), \
                patch("services.api_client.cached_tracking", return_value={
                    "configured": False, "available": False, "records": []}):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Modelo"
            app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("DATABASE_URL" in item.value for item in app.info))

    def test_legacy_historical_page_selection_returns_to_summary(self):
        with patch("services.api_client.cached_status", return_value={
                    **self._demand_status(), "future_forecast_available": False}), \
                patch("services.api_client.cached_model_info", return_value={}):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Análisis histórico"
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["current_page"], "Resumen")
        self.assertEqual(len(app.get("file_uploader")), 0)

    def test_model_errors_are_shown_without_example_predictions(self):
        with patch("services.api_client.predict_price",
                   side_effect=requests.ConnectionError("API unavailable")):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Precios"
            app.run()
            app.button(key="price_generate").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("No se pudo ejecutar el modelo" in item.value
                            for item in app.error))
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertEqual(len(app.dataframe), 0)

    def test_consumption_model_page_keeps_its_summary_and_charts(self):
        with patch("services.api_client.cached_status", return_value=self._demand_status()), \
                patch("services.api_client.cached_model_info", return_value={
                    "model_type": "RandomForestRegressor"}), \
                patch("services.api_client.post",
                      side_effect=lambda path, body: self._demand_forecast(
                          body["horizon_hours"])), \
                patch("services.api_client.cached_history", return_value={"records": []}), \
                patch("services.api_client.cached_curves", return_value={"curves": {}}), \
                patch("services.api_client.cached_history_between",
                      return_value={"records": []}):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Modelo"
            app.run()
            self.assertEqual(app.selectbox(key="prediction_horizon").options,
                             ["6 horas", "12 horas", "24 horas", "48 horas"])
            app.selectbox(key="prediction_horizon").set_value(6).run()
            app.button(key="prediction_generate").click().run()
        self.assertEqual(app.session_state["prediction_horizon"], 6)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("plotly_chart")), 2)
        self.assertTrue(any("Máximo previsto" in metric.label for metric in app.metric))

    def test_system_page_reports_both_models_and_api(self):
        with patch("services.api_client.cached_status", return_value=self._demand_status()), \
                patch("services.api_client.cached_model_info", return_value={
                    "model_type": "RandomForestRegressor"}), \
                patch("services.api_client.cached_price_model", return_value={
                    "loaded": True,
                    "model": {"model_type": "RandomForestRegressor",
                              "features": ["hora", "dia_semana", "mes", "dia_mes"],
                              "unit": "€/MWh",
                              "timezone_convention": "Europe/Madrid"},
                }):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25)
            app.session_state["current_page"] = "Estado del sistema"
            app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Estado del sistema" in item.value for item in app.markdown))
        self.assertTrue(any("Modelo de precios" in item.value for item in app.markdown))

    def test_theme_selector_still_works(self):
        with patch("services.api_client.cached_status", return_value={
                    **self._demand_status(), "future_forecast_available": False}), \
                patch("services.api_client.cached_model_info", return_value={}):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
            for theme in ("Claro", "Oscuro", "Sistema"):
                app.selectbox(key="appearance").set_value(theme).run()
                self.assertFalse(app.exception)


class PriceBandTests(unittest.TestCase):
    def test_prices_are_shown_per_kwh_and_split_in_relative_bands(self):
        stamps = pd.date_range("2026-11-03", periods=6, freq="h", tz="UTC")
        frame = price_bands(pd.DataFrame({
            "timestamp_utc": stamps, "price_eur_mwh": [120, 80, 60, 70, 150, 200]}))
        self.assertAlmostEqual(frame.price_eur_kwh.iloc[0], 0.12)
        self.assertEqual(list(frame.band), ["llano", "llano", "valle", "valle", "punta", "punta"])
        cheapest = best_window(frame, 2)
        self.assertEqual(list(cheapest.price_eur_mwh), [60, 70])
        self.assertEqual(list(best_window(frame, 2, cheapest=False).price_eur_mwh), [150, 200])


class NoEmojiTests(unittest.TestCase):
    def test_interface_uses_svg_icons_instead_of_emoji_or_arrow_glyphs(self):
        glyphs = set("←↑→↓✓✔✕✖⚡⬆⬇")
        for path in [*DIRECTORY.glob("views/*.py"), *DIRECTORY.glob("components/*.py"),
                     *DIRECTORY.glob("static/city/*.js"), DIRECTORY / "static/city/index.html"]:
            text = path.read_text(encoding="utf-8")
            found = {char for char in text if char in glyphs or ord(char) >= 0x1F300}
            self.assertFalse(found, f"{path.name}: {found}")


if __name__ == "__main__":
    unittest.main()

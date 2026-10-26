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

from components.charts import base_figure  # noqa: E402
from components.layout import PAGES  # noqa: E402
from components.tables import csv_bytes  # noqa: E402
from services.analysis import select_dates, select_period  # noqa: E402
from services.data_loader import load_local, load_upload, refresh  # noqa: E402
from services.data_quality import Dataset, aligned_data, prepare  # noqa: E402
from utils.paths import FILENAMES, candidate_paths  # noqa: E402


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
    def test_historical_range_options_select_exact_rolling_windows(self):
        frame = pd.DataFrame({"timestamp": pd.date_range(
            "2026-01-01", periods=24 * 8, freq="h", tz="Europe/Madrid"),
            "value": range(24 * 8)})
        for label, expected in (("24 horas", 24), ("48 horas", 48),
                                ("3 días", 72), ("7 días", 168)):
            with self.subTest(label=label):
                selected = select_period(frame, label)
                self.assertEqual(len(selected), expected)
                self.assertEqual(selected.timestamp.max(), frame.timestamp.max())

    def test_public_navigation_is_demand_first_and_charts_are_locked(self):
        self.assertEqual(PAGES, ["Resumen", "Demanda", "Análisis histórico", "Modelo",
                                 "Precios", "Datos", "Estado del sistema"])
        figure = base_figure("Demanda · MW")
        self.assertFalse(figure.layout.dragmode)
        self.assertTrue(figure.layout.xaxis.fixedrange)
        self.assertTrue(figure.layout.yaxis.fixedrange)

    def test_all_views_render_with_real_data(self):
        app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
        self.assertFalse(app.exception)
        for page in PAGES:
            with self.subTest(page=page):
                app.sidebar.button(key=f"nav_{PAGES.index(page)}").click().run()
                self.assertFalse(app.exception)

    def test_themes_and_period_filters_work(self):
        app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
        for theme in ("Claro", "Oscuro", "Sistema"):
            app.sidebar.selectbox[0].set_value(theme).run()
            self.assertFalse(app.exception)
        app.sidebar.button(key=f"nav_{PAGES.index('Demanda')}").click().run()
        for period in ("24 horas", "7 días", "30 días", "Todo el periodo", "Personalizado"):
            app.main.segmented_control[0].set_value(period).run()
            self.assertFalse(app.exception)

    def test_missing_datasets_do_not_break_any_view(self):
        real = {kind: load_local(kind) for kind in FILENAMES}
        for absent_kinds in (("demand",), ("demand", "weather")):
            replacement = {kind: Dataset(kind) if kind in absent_kinds else dataset
                           for kind, dataset in real.items()}
            with patch("services.data_loader.load_local", side_effect=replacement.__getitem__):
                app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
                self.assertEqual(len(app.get("file_uploader")), 0)
                for page in PAGES:
                    with self.subTest(absent=absent_kinds, page=page):
                        app.sidebar.button(key=f"nav_{PAGES.index(page)}").click().run()
                        self.assertFalse(app.exception)
                        self.assertEqual(len(app.get("file_uploader")),
                                         int("demand" in absent_kinds) if page == "Datos" else 0)

    def test_stale_demand_requires_explicit_historical_simulation(self):
        status = {"api": True, "model_loaded": True, "data_available": True,
                  "last_demand_utc": "2024-12-31T22:00:00+00:00",
                  "future_forecast_available": False,
                  "reason": "La demanda más reciente supera el límite de frescura."}
        model = {"model_type": "RandomForestRegressor",
                 "artifact_version": "modelo_demanda_v01"}
        with patch("services.api_client.cached_status", return_value=status), \
                patch("services.api_client.cached_model_info", return_value=model), \
                patch("services.api_client.cached_evaluation",
                      side_effect=requests.ConnectionError):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
            self.assertEqual(len(app.get("file_uploader")), 0)
            app.sidebar.button(key=f"nav_{PAGES.index('Modelo')}").click().run()
            generate = next(button for button in app.button
                            if button.label == "Generar simulación histórica 48 h")
            self.assertTrue(generate.disabled)
            self.assertEqual(len(app.get("plotly_chart")), 0)

    def test_confirmed_historical_simulation_is_sent_as_simulation(self):
        origin = pd.Timestamp("2024-12-31T22:00:00Z")
        generated = pd.Timestamp.now(tz="UTC")
        forecast = {
            "model": "RandomForestRegressor", "horizon_hours": 48,
            "forecast_origin_utc": origin.isoformat(),
            "simulation_reference_utc": origin.isoformat(),
            "generated_at_utc": generated.isoformat(),
            "prediction_mode": "historical_simulation",
            "predictions": [{"timestamp_utc": (origin + pd.Timedelta(hours=hour)).isoformat(),
                             "demand_mw": 31_000.0 + hour}
                            for hour in range(1, 49)],
        }
        status = {"api": True, "model_loaded": True, "model_schema_valid": True,
                  "data_available": True, "last_demand_utc": origin.isoformat(),
                  "last_demand_mw": 31_000.0, "last_demand_age_hours": 16000,
                  "demand_fresh": False, "historical_simulation_available": True,
                  "future_forecast_available": False,
                  "reason": "Dato histórico obsoleto.", "source_notice": "REData no disponible."}
        model = {"model_type": "RandomForestRegressor",
                 "artifact_version": "modelo_demanda_v01"}
        with patch("services.api_client.cached_status", return_value=status), \
                patch("services.api_client.cached_model_info", return_value=model), \
                patch("services.api_client.post", return_value=forecast) as post, \
                patch("services.api_client.cached_history", return_value={"records": []}), \
                patch("services.api_client.cached_history_between", return_value={"records": []}):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
            app.sidebar.button(key=f"nav_{PAGES.index('Modelo')}").click().run()
            self.assertTrue(app.button(key="prediction_generate").disabled)
            app.checkbox(key="confirm_historical_simulation").check().run()
            app.button(key="prediction_generate").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(post.called)
        self.assertTrue(post.call_args.args[1]["allow_stale_simulation"])
        self.assertTrue(any("SIMULACIÓN HISTÓRICA" in item.value for item in app.warning))

    def test_model_page_uses_api_forecast_and_exposes_reset_controls(self):
        origin = pd.Timestamp.now(tz="UTC").floor("h")
        generated = pd.Timestamp.now(tz="UTC")
        predictions = [{"timestamp_utc": (origin + pd.Timedelta(hours=hour)).isoformat(),
                        "demand_mw": 31_000.0 + hour * 10}
                       for hour in range(1, 49)]
        forecast = {"model": "RandomForestRegressor", "horizon_hours": 48,
                    "forecast_origin_utc": origin.isoformat(),
                    "generated_at_utc": generated.isoformat(), "predictions": predictions}
        status = {"api": True, "model_loaded": True, "model_schema_valid": True,
                  "data_available": True, "last_demand_utc": origin.isoformat(),
                  "last_demand_mw": 31_000.0, "demand_delta_mw": 100.0,
                  "demand_fresh": True, "demand_source_status": "connected",
                  "future_forecast_available": True, "source_notice": None}
        model = {"model_type": "RandomForestRegressor",
                 "artifact_version": "modelo_demanda_v01"}
        history = {"records": [{"timestamp_utc": origin.isoformat(),
                                "demand_mw": 31_000.0}]}
        with patch("services.api_client.cached_status", return_value=status), \
                patch("services.api_client.cached_model_info", return_value=model), \
                patch("services.api_client.post", return_value=forecast), \
                patch("services.api_client.cached_history", return_value=history), \
                patch("services.api_client.cached_history_between", return_value={"records": []}):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
            app.sidebar.button(key=f"nav_{PAGES.index('Modelo')}").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("plotly_chart")), 2)
        self.assertTrue(app.button(key="reset_view_model_forecast_timeline"))
        self.assertTrue(app.button(key="reset_view_model_hourly_profile"))
        labels = [item.label for item in app.metric]
        self.assertFalse(any("MAE" in label or "RMSE" in label for label in labels))

    def test_selected_prediction_horizon_is_sent_to_the_api(self):
        origin = pd.Timestamp.now(tz="UTC").floor("h")
        generated = pd.Timestamp.now(tz="UTC")
        prediction = {"model": "RandomForestRegressor", "horizon_hours": 5,
                      "forecast_origin_utc": origin.isoformat(),
                      "generated_at_utc": generated.isoformat(),
                      "prediction_mode": "current_forecast",
                      "predictions": [
                          {
                              "timestamp_utc": (
                                  origin + pd.Timedelta(hours=hour)).isoformat(),
                              "demand_mw": 31_000.0 + hour,
                          }
                          for hour in range(1, 6)
                      ]}
        status = {"api": True, "model_loaded": True, "model_schema_valid": True,
                  "data_available": True, "last_demand_utc": origin.isoformat(),
                  "demand_fresh": True, "future_forecast_available": True}
        model = {"model_type": "RandomForestRegressor",
                 "artifact_version": "modelo_demanda_v01"}
        with patch("services.api_client.cached_status", return_value=status), \
                patch("services.api_client.cached_model_info", return_value=model), \
                patch("services.api_client.cached_curves", return_value={"curves": {}}), \
                patch("services.api_client.cached_history", return_value={"records": []}), \
                patch("services.api_client.cached_history_between", return_value={"records": []}), \
                patch("services.api_client.post", return_value=prediction) as post:
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
            app.sidebar.button(key=f"nav_{PAGES.index('Modelo')}").click().run()
            app.select_slider(key="prediction_horizon").set_value(5).run()
            app.button(key="prediction_generate").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(post.call_args.args[1]["horizon_hours"], 5)
        self.assertEqual(len(app.get("plotly_chart")), 2)

    def test_manual_source_survives_navigation_and_refresh(self):
        raw_path = DIRECTORY.parents[1] / "data" / "raw" / FILENAMES["demand"]
        content = raw_path.read_bytes()
        weather = load_local("weather")
        replacement = {"demand": Dataset("demand"), "weather": weather}
        offline_status = {
            "api": True, "model_loaded": True, "data_available": True,
            "last_demand_utc": "2024-12-31T22:00:00+00:00", "demand_fresh": False,
            "historical_simulation_available": False,
            "future_forecast_available": False,
            "reason": "REData no disponible en esta prueba.",
        }
        with patch("services.data_loader.load_local", side_effect=replacement.__getitem__), \
                patch("services.api_client.cached_status", return_value=offline_status), \
                patch("services.api_client.refresh_sources", return_value=offline_status):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
            app.sidebar.button(key="nav_5").click().run()
            self.assertEqual(len(app.file_uploader), 1)
            app.file_uploader[0].set_value((FILENAMES["demand"], b"not parquet",
                                            "application/octet-stream")).run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertNotIn("manual_demand", app.session_state)
            app.file_uploader[0].set_value((FILENAMES["demand"], content,
                                            "application/octet-stream")).run()
            for page in ("Datos", "Demanda", "Estado del sistema", "Resumen"):
                app.sidebar.button(key=f"nav_{PAGES.index(page)}").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.get("file_uploader")), 0)
                self.assertEqual(app.session_state["manual_demand"], content)
            app.sidebar.button(key="refresh_data").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["manual_demand"], content)
            self.assertEqual(len(app.get("plotly_chart")), 0)
            app.sidebar.button(key=f"nav_{PAGES.index('Demanda')}").click().run()
            self.assertGreater(len(app.get("plotly_chart")), 0)
            app.sidebar.button(key="nav_5").click().run()
            app.button(key="discard_demand").click().run()
            self.assertFalse(app.exception)
            self.assertNotIn("manual_demand", app.session_state)
            self.assertEqual(len(app.file_uploader), 1)

    def test_pagination_changes_rows_and_resets_when_filter_changes(self):
        app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
        app.sidebar.button(key="nav_1").click().run()
        first = next(item.value for item in app.markdown if 'class="data-table"' in item.value)
        app.button(key="next_demand").click().run()
        second = next(item.value for item in app.markdown if 'class="data-table"' in item.value)
        self.assertNotEqual(first, second)
        self.assertEqual(app.session_state["table_page_demand"], 2)
        app.button(key="previous_demand").click().run()
        self.assertEqual(app.session_state["table_page_demand"], 1)
        self.assertTrue(app.button(key="previous_demand").disabled)
        app.button(key="next_demand").click().run()
        app.main.segmented_control[0].set_value("24 horas").run()
        self.assertEqual(app.session_state["table_page_demand"], 1)
        self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()

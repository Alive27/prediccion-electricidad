"""Checks for local Kenney assets and the Streamlit-hosted city scene."""

import sys
import unittest
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))

from components.city_scene import render_city  # noqa: E402


class CitySceneTests(unittest.TestCase):
    def test_scene_uses_local_assets_and_real_metrics_only(self):
        forecast = {"predictions": [{"demand_mw": 30_500.0} for _ in range(24)]}
        metrics = {
            "last_demand_mw": 31_200.0,
            "last_demand_utc": "2026-11-02T08:00:00+00:00",
            "demand_fresh": True,
            "api_state": "API operativa · Modelo listo",
            "freshness": "Actualizados",
            "latest_price_eur_mwh": 74.5,
            "latest_price_utc": "2026-11-02T08:00:00+00:00",
        }
        with patch("components.city_scene.st.iframe") as render:
            render_city(metrics, forecast)
        url = render.call_args.args[0]
        query = parse_qs(urlparse(url).query)
        payload = json.loads(query["metrics"][0])
        self.assertTrue(url.startswith("/app/static/city/index.html?metrics="))
        self.assertEqual(payload["demand"], 31_200.0)
        self.assertEqual(payload["price"], 74.5)
        self.assertEqual(len(payload["forecastScale"]), 24)
        self.assertNotIn("cdn.", url.lower())
        self.assertEqual(render.call_args.kwargs["height"], 690)

    def test_zoom_is_fixed_wheel_scrolls_page_and_listener_is_cleaned_up(self):
        script = (DIRECTORY / "static" / "city" / "city.js").read_text(encoding="utf-8")
        self.assertIn("controls.enableZoom = false", script)
        self.assertNotIn("ctrlKey", script)
        self.assertIn("controls.minDistance = distance", script)
        self.assertIn("controls.maxDistance = distance", script)
        self.assertIn("window.parent.scrollBy", script)
        self.assertIn("removeEventListener(\"wheel\", onWheel, true)", script)

    def test_city_buildings_open_every_section_through_the_sidebar_keys(self):
        with patch("components.city_scene.st.iframe") as render:
            render_city({}, {})
        payload = json.loads(parse_qs(urlparse(render.call_args.args[0]).query)["metrics"][0])
        self.assertEqual(payload["navigation"], {
            "Resumen": "nav_0", "Modelo": "nav_1", "Precios": "nav_2", "Datos": "nav_3",
            "Estado del sistema": "nav_4"})
        hotspots = (DIRECTORY / "static" / "city" / "hotspots.js").read_text(encoding="utf-8")
        for page in ("Modelo", "Precios", "Datos", "Estado del sistema"):
            self.assertIn(f'page: "{page}"', hotspots)

    def test_zone_legend_is_not_rendered(self):
        page = (DIRECTORY / "static" / "city" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("RESIDENCIAL", page)
        self.assertNotIn('class="zones"', page)


    def test_blender_city_glb_is_self_contained_and_loaded_by_the_scene(self):
        city_dir = DIRECTORY / "static" / "city"
        data = (city_dir / "energy_city.glb").read_bytes()
        self.assertEqual(data[:4], b"glTF")
        json_length = int.from_bytes(data[12:16], "little")
        gltf = json.loads(data[20:20 + json_length])
        self.assertTrue(gltf["images"])
        self.assertTrue(all("uri" not in image for image in gltf["images"]))
        self.assertIn("city_glow", {material["name"] for material in gltf["materials"]})
        layout = json.loads((city_dir / "energy_city.json").read_text(encoding="utf-8"))
        self.assertTrue(all(len(route) > 1 for route in layout["energyRoutes"].values()))
        script = (city_dir / "city.js").read_text(encoding="utf-8")
        self.assertIn("./energy_city.glb", script)
        self.assertIn("./energy_city.json", script)


if __name__ == "__main__":
    unittest.main()

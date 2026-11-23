"""Streamlit host for the local Three.js energy city scene."""

import json
from urllib.parse import quote

import streamlit as st

from components.layout import PAGES


STATIC_URL = "/app/static"
DEMAND_VISUAL_FULL_SCALE_MW = 40_000.0
SCENE_VERSION = "v18"   # bump to bypass the browser cache after changing static/city


def _scale(value_mw: float) -> float:
    return max(0.0, min(1.0, float(value_mw) / DEMAND_VISUAL_FULL_SCALE_MW))


def render_city(demand_mw: float | None, forecast: dict | None = None,
                labels: dict[str, str] | None = None) -> None:
    """Serve the same-origin scene with the only inputs it uses: the observed demand and
    the forecast profile (both as 0-1 light intensity), the sidebar key of each page and
    the live value shown on each section's hotspot."""
    records = (forecast or {}).get("predictions", [])
    values = [float(record["demand_mw"]) for record in records
              if isinstance(record, dict) and record.get("demand_mw") is not None] \
        if isinstance(records, list) else []
    payload = {
        "demandScale": _scale(demand_mw) if demand_mw is not None else 0.0,
        "forecastScale": [_scale(value) for value in values],
        # Sidebar button keys, so the city's buildings can open each section.
        "navigation": {page: f"nav_{index}" for index, page in enumerate(PAGES)},
        "labels": labels or {},
    }
    serialized = json.dumps(payload, separators=(",", ":"))
    url = (f"{STATIC_URL}/city/index.html?config={quote(serialized, safe='')}"
           f"&scene={SCENE_VERSION}")
    st.iframe(url, height=690)

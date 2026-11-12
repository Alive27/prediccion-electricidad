"""Streamlit host for the local Three.js energy city scene."""

import json
from urllib.parse import quote

import streamlit as st

from components.layout import PAGES


STATIC_URL = "/app/static"
DEMAND_VISUAL_FULL_SCALE_MW = 40_000.0


def render_city(metrics: dict, forecast: dict | None = None) -> None:
    """Serve the local same-origin scene and pass only real API values to it."""
    forecast = forecast or {}
    records = forecast.get("predictions", [])
    values = [
        float(record["demand_mw"])
        for record in records
        if isinstance(record, dict) and record.get("demand_mw") is not None
    ] if isinstance(records, list) else []
    demand = metrics.get("last_demand_mw")
    demand_available = demand is not None
    demand_scale = (float(demand) / DEMAND_VISUAL_FULL_SCALE_MW
                    if demand_available else 0)
    payload = {
        "demand": demand,
        "demandTime": metrics.get("last_demand_utc"),
        "demandFresh": bool(metrics.get("demand_fresh")),
        "demandScale": max(0.0, min(1.0, demand_scale)),
        "forecast": values,
        "forecastScale": [max(0.0, min(1.0, value / DEMAND_VISUAL_FULL_SCALE_MW))
                          for value in values],
        "apiState": metrics.get("api_state", "API no disponible"),
        "freshness": metrics.get("freshness", "Sin datos"),
        "price": metrics.get("latest_price_eur_mwh"),
        "priceTime": metrics.get("latest_price_utc"),
        "priceStale": bool(metrics.get("price_stale")),
        "priceEstimate": metrics.get("estimated_price_eur_mwh"),
        "priceEstimateTime": metrics.get("estimated_price_utc"),
        # Sidebar button keys, so the city's buildings can open each section.
        "navigation": {page: f"nav_{index}" for index, page in enumerate(PAGES)},
    }
    serialized = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    url = f"{STATIC_URL}/city/index.html?metrics={quote(serialized, safe='')}&scene=v12"
    st.iframe(url, height=690)

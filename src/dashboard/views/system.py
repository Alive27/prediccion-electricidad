"""Operational liveness, readiness, source freshness and model input checks."""

import pandas as pd
import requests
import streamlit as st

from components.cards import dataset_status, status_html
from components.layout import header, section
from services import api_client
from utils.formatting import number, timestamp


def _local_time(value: str | None) -> str:
    if not value:
        return "Sin dato"
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return timestamp(stamp, with_zone=True)


def _status_list(rows: list[tuple[str, str, str]]) -> None:
    st.markdown(f'<div class="status-list">{"".join(status_html(*row) for row in rows)}</div>',
                unsafe_allow_html=True)


def _status(value: bool | None, ready: str, blocked: str) -> tuple[str, str]:
    return (ready, "success") if value else (blocked, "error" if value is False else "pending")


def render(datasets: dict) -> None:
    header("Estado del sistema", "Disponibilidad operativa de las fuentes y de los modelos",
           eyebrow="ESTADO DEL SISTEMA")
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se puede consultar FastAPI ({type(error).__name__}).")
        status = None
        model = {}
    if status is not None:
        try:
            model = api_client.cached_model_info()
        except (requests.RequestException, ValueError):
            model = {}

    section("Servicio y preparación")
    if status is None:
        _status_list([("API · disponibilidad", "Sin conexión", "error"),
                      ("Modelo y fuentes", "No comprobables", "pending")])
    else:
        _status_list([
            ("API · disponibilidad",
             *_status(status.get("api"), "Operativa", "No disponible")),
            ("Artefacto y esquema del modelo",
             *_status(status.get("model_loaded") and status.get("model_schema_valid"),
                      model.get("model_type", "Cargado"), "No disponible")),
            ("Fuente de demanda · REData",
             *_status(status.get("demand_source_status") == "connected",
                      "Conectada", "Sin conexión")),
            ("Frescura de la última demanda",
             *_status(status.get("demand_fresh"), "Dentro del límite", "Desactualizada")),
            ("Histórico y retardos del modelo",
             *_status(status.get("historical_features_available"),
                      "Disponibles", "Insuficientes")),
            ("Temperaturas futuras · Open-Meteo",
             *_status(status.get("forecast_weather_available"),
                      "48 horas disponibles", "No disponibles")),
            ("Previsión operativa · 48 h",
             *_status(status.get("future_forecast_available"), "Lista", "Bloqueada")),
            ("Histórico apto para simulación",
             *_status(status.get("historical_simulation_available"),
                      "Sí, con confirmación", "Entradas insuficientes")),
        ])

        cards = st.columns(3)
        last_value = status.get("last_demand_mw")
        cards[0].metric("Última observación real",
                        f"{number(last_value)} MW" if last_value is not None else "Sin dato",
                        _local_time(status.get("last_demand_utc")))
        cards[1].metric("Consulta REData", _local_time(status.get(
            "demand_source_checked_at_utc")),
            f"Última correcta {_local_time(status.get('demand_source_last_success_at_utc'))}")
        cards[2].metric("Antigüedad del dato",
                        f"{number(status['last_demand_age_hours'], 1)} h"
                        if status.get("last_demand_age_hours") is not None else "Sin dato",
                        f"Límite {status.get('freshness_limit_hours', '—')} h")
        if status.get("source_notice"):
            st.warning(status["source_notice"])
        if status.get("reason"):
            st.info(status["reason"])

    section("Datos y modelos")
    state, tone = dataset_status(datasets["demand"])
    rows = [("Histórico local 2024 · REData", state, tone)]
    price_error = None
    try:
        price_status = api_client.cached_price_model()
    except (requests.RequestException, ValueError):
        rows.append(("Modelo de precios", "Sin conexión", "error"))
    else:
        loaded = price_status.get("loaded", False)
        rows.append(("Modelo de precios",
                     price_status.get("model", {}).get("model_type", "Cargado") if loaded
                     else "No cargado", "success" if loaded else "error"))
        price_error = None if loaded else price_status.get("error")
    _status_list(rows)
    if price_error:
        st.error(f"Error del modelo de precios: {price_error}")

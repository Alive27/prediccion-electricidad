"""Operational liveness, readiness, source freshness and model input checks."""

import pandas as pd
import requests
import streamlit as st

from components.cards import dataset_status, status_row
from components.layout import header, section
from services import api_client
from utils.formatting import day_label, timestamp


def _local_time(value: str | None) -> str:
    if not value:
        return "Sin dato"
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return timestamp(stamp, with_zone=True)


def _status(value: bool | None, ready: str, blocked: str) -> tuple[str, str]:
    return (ready, "success") if value else (blocked, "error" if value is False else "pending")


def render(datasets: dict) -> None:
    header("Estado del sistema", "Disponibilidad operativa de las fuentes y del modelo")
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

    section("Servicio y preparación",
            "Vivo: la API responde. Listo: puede prever las próximas 48 horas.")
    if status is None:
        status_row("API · liveness", "Sin conexión", "error")
        status_row("Modelo y fuentes", "No comprobables", "pending")
    else:
        status_row("API · liveness",
                   *_status(status.get("api"), "Operativa", "No disponible"))
        status_row("Artefacto y esquema del modelo",
                   *_status(status.get("model_loaded") and status.get("model_schema_valid"),
                            model.get("model_type", "Cargado"), "No disponible"))
        status_row("Fuente de demanda · REData",
                   *_status(status.get("demand_source_status") == "connected",
                            "Conectada", "Sin conexión"))
        status_row("Frescura de la última demanda",
                   *_status(status.get("demand_fresh"), "Dentro del límite", "Desactualizada"))
        status_row("Histórico y lags del modelo",
                   *_status(status.get("historical_features_available"),
                            "Disponibles", "Insuficientes"))
        status_row("Temperaturas futuras · Open-Meteo",
                   *_status(status.get("forecast_weather_available"),
                            "48 horas disponibles", "No disponibles"))
        status_row("Previsión operativa · 48 h",
                   *_status(status.get("future_forecast_available"), "Lista", "Bloqueada"))
        status_row("Histórico apto para simulación",
                   *_status(status.get("historical_simulation_available"),
                            "Sí, con confirmación", "Entradas insuficientes"))

        cards = st.columns(3)
        last_value = status.get("last_demand_mw")
        cards[0].metric("Última observación real",
                        f"{last_value:,.0f} MW" if last_value is not None else "Sin dato",
                        _local_time(status.get("last_demand_utc")))
        cards[1].metric("Consulta REData", _local_time(status.get(
            "demand_source_checked_at_utc")),
            f"Última correcta {_local_time(status.get('demand_source_last_success_at_utc'))}")
        cards[2].metric("Edad del dato",
                        f"{status['last_demand_age_hours']:.1f} h"
                        if status.get("last_demand_age_hours") is not None else "Sin dato",
                        f"Límite {status.get('freshness_limit_hours', '—')} h")
        st.caption(f"Open-Meteo consultado {_local_time(status.get('weather_source_checked_at_utc'))}; "
                   "temperatura a 2 m de Madrid, usada internamente por el modelo.")
        if status.get("source_notice"):
            st.warning(status["source_notice"])
        if status.get("reason"):
            st.info(status["reason"])
        if status.get("weather_source_error"):
            st.caption(f"Open-Meteo: {status['weather_source_error']}")

    section("Histórico local 2024", "Permanece separado de las observaciones actuales de REData.")
    dataset = datasets["demand"]
    state, tone = dataset_status(dataset)
    status_row("Parquet REData · demanda", state, tone)
    if dataset.available:
        st.caption(f"Cobertura: {day_label(dataset.frame.timestamp.min())} — "
                   f"{day_label(dataset.frame.timestamp.max())} · "
                   f"{dataset.quality['records']:,} registros.")
    elif dataset.errors:
        st.caption("El histórico local tiene un problema de lectura. La demanda actual "
                   "se consulta por separado en REData.")
    else:
        st.caption("No se encontró el archivo histórico local de 2024 en este entorno.")

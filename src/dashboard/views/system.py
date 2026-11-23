"""Estado del sistema: liveness, readiness and freshness of every source and model."""

import requests
import streamlit as st

from components.blocks import html, meter, metric, ornament, status_list
from components.cards import dataset_status
from components.layout import header
from services import api_client
from utils.formatting import clock, number, short_datetime

TONES = {"success": "success", "warning": "warning", "error": "error", "pending": "pending"}


def _row(icon: str, color: str, label: str, ok: bool | None, ready: str,
         blocked: str) -> tuple[str, str, str, str, str]:
    """A list row whose value and tone follow a boolean check (None = unknown)."""
    if ok:
        return icon, color, label, ready, "success"
    return icon, color, label, blocked, "error" if ok is False else "pending"


def _service_rows(status: dict | None, model: dict) -> list[tuple]:
    if status is None:
        return [("server", "#0a84ff", "API · disponibilidad", "Sin conexión", "error"),
                ("model", "#af52de", "Modelo y fuentes", "No comprobables", "pending")]
    get = status.get
    return [
        _row("server", "#0a84ff", "API · disponibilidad", get("api"), "Operativa",
             "No disponible"),
        _row("model", "#af52de", "Artefacto y esquema del modelo",
             bool(get("model_loaded") and get("model_schema_valid")),
             model.get("model_type", "Cargado").replace("Regressor", ""), "No disponible"),
        _row("tower", "#ff9500", "Fuente de demanda · REData",
             get("demand_source_status") == "connected", "Conectada", "Sin conexión"),
        _row("clock", "#30b0c7", "Frescura de la última demanda", get("demand_fresh"),
             "Dentro del límite", "Desactualizada"),
        _row("history", "#8e8e93", "Histórico y retardos del modelo",
             get("historical_features_available"), "Disponibles", "Insuficientes"),
        _row("thermo", "#ff3b30", "Temperaturas futuras · Open-Meteo",
             get("forecast_weather_available"), "48 horas disponibles", "No disponibles"),
        _row("bolt", "#34c759", "Previsión operativa · 48 h", get("future_forecast_available"),
             "Lista", "Bloqueada"),
        _row("refresh", "#5e5ce6", "Histórico apto para simulación",
             get("historical_simulation_available"), "Sí, con confirmación",
             "Entradas insuficientes"),
    ]


def _data_rows(datasets: dict) -> tuple[list[tuple], str | None]:
    state, tone = dataset_status(datasets["demand"])
    if datasets["demand"].available:
        state = f"{state} · {number(datasets['demand'].quality['records'])} registros"
    rows = [("database", "#3ecf8e", "Histórico local 2024 · REData", state, TONES[tone])]
    try:
        price = api_client.cached_price_model()
    except (requests.RequestException, ValueError):
        rows.append(("payments", "#ff9500", "Modelo de precios", "Sin conexión", "error"))
        return rows, None
    loaded = price.get("loaded", False)
    rows.append(("payments", "#ff9500", "Modelo de precios",
                 price.get("model", {}).get("model_type", "Cargado").replace("Regressor", "")
                 if loaded else "No cargado", "success" if loaded else "error"))
    return rows, None if loaded else price.get("error")


def _observation_cards(status: dict) -> str:
    value, age = status.get("last_demand_mw"), status.get("last_demand_age_hours")
    limit = status.get("freshness_limit_hours") or 6
    checked = status.get("demand_source_checked_at_utc")
    success = status.get("demand_source_last_success_at_utc")
    return "".join([
        metric("Última observación real", number(value) if value is not None else "Sin dato",
               "MW" if value is not None else "",
               short_datetime(status["last_demand_utc"]) if status.get("last_demand_utc") else ""),
        metric("Consulta REData", clock(checked) if checked else "Sin dato", "",
               f"Última correcta {clock(success)}" if success else "Sin consultas correctas"),
        metric("Antigüedad del dato", number(age, 1) if age is not None else "Sin dato",
               "h" if age is not None else "", f"Límite {number(limit)} h",
               extra=meter(age / limit if age is not None else 0,
                           "success" if status.get("demand_fresh") else "warning")),
    ])


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

    service = _service_rows(status, model)
    data, price_error = _data_rows(datasets)
    left, right = st.columns([1.65, 1], gap="medium")
    with left:
        html('<h2 class="v-gh">Servicio y preparación</h2>' + status_list(service)
             + '<h2 class="v-gh v-gh-next">Datos y modelos</h2>' + status_list(data))
    with right:
        if status is not None:
            html('<h2 class="v-gh">Última observación</h2>'
                 f'<div class="v-stack">{_observation_cards(status)}</div>')
    if status is not None:
        if status.get("source_notice"):
            st.warning(status["source_notice"])
        if status.get("reason"):
            st.info(status["reason"])
    if price_error:
        st.error(f"Error del modelo de precios: {price_error}")

    problems = sum(tone != "success" for *_, tone in service + data)
    checked = status.get("demand_source_checked_at_utc") if status else None
    when = f" · comprobado {clock(checked)}" if checked else ""
    html(ornament(("Todo operativo" if not problems else
                   f"{problems} comprobaciones con avisos") + when,
                  "success" if not problems else "warning"))

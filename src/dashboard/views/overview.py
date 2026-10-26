"""Executive demand and 48-hour forecast overview."""

import pandas as pd
import requests
import streamlit as st

from components.forecast import (
    LOCAL_ZONE, is_current, prediction_frame, render_timeline,
)
from components.layout import header, section
from services import api_client
from utils.formatting import number


def _local_time(value: str | None) -> str:
    if not value:
        return "Sin dato"
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.tz_convert(LOCAL_ZONE).strftime("%d/%m/%Y %H:%M %Z")


def _request_forecast() -> None:
    st.session_state["demand_forecast_attempted"] = True
    try:
        with st.spinner("Calculando la previsión de demanda de las próximas 48 horas…"):
            forecast = api_client.post("/predict", {"horizon_hours": 48})
        st.session_state["demand_forecast"] = forecast
    except requests.HTTPError as error:
        detail = error.response.text if error.response is not None else str(error)
        st.error(f"La API no pudo generar la previsión: {detail[:400]}")
    except requests.RequestException as error:
        st.error(f"No se pudo solicitar la previsión ({type(error).__name__}).")


def render(datasets: dict) -> None:
    header("Demanda eléctrica peninsular", "Demanda real de REData y previsión horaria a 48 horas")
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se puede consultar FastAPI ({type(error).__name__}).")
        st.caption(f"API configurada: {api_client.BASE_URL}")
        return
    try:
        model = api_client.cached_model_info()
    except (requests.RequestException, ValueError):
        model = {}

    latest = status.get("last_demand_mw")
    current_value = f"{number(latest, 0)} MW" if latest is not None else "Sin dato"
    previous_delta = status.get("demand_delta_mw")
    delta_value = (f"{previous_delta:+,.0f} MW" if previous_delta is not None
                   else "Sin hora comparable")
    source_state = status.get("demand_source_status", "unavailable")
    source_label = "Conectada" if source_state == "connected" else "No disponible"

    section("Situación actual", "Solo observaciones reales publicadas por REData.")
    cards = st.columns(4)
    cards[0].metric("Última demanda real", current_value,
                    _local_time(status.get("last_demand_utc")))
    cards[1].metric("Variación última hora", delta_value,
                    (f"Respecto a {_local_time(status.get('previous_demand_utc'))}"
                     if previous_delta is not None else "Sin comparación horaria válida"))
    cards[2].metric("Fuente REData", source_label,
                    f"Consultada {_local_time(status.get('demand_source_checked_at_utc'))}")
    cards[3].metric("Disponibilidad modelo", "Lista" if status.get(
        "future_forecast_available") else "Bloqueada",
        model.get("model_type", "Modelo no cargado") if status.get("model_loaded")
        else "Artefacto no disponible")
    st.caption(f"Última consulta correcta REData: "
               f"{_local_time(status.get('demand_source_last_success_at_utc'))} · "
               f"last-update indicado por la fuente: "
               f"{_local_time(status.get('demand_source_updated_at_utc'))} · "
               f"último intento: {_local_time(status.get('demand_source_checked_at_utc'))}.")

    if status.get("source_notice"):
        st.warning(status["source_notice"])
    if not status.get("demand_fresh"):
        st.warning(status.get("reason") or "El dato real está fuera del límite de frescura.")
    elif not status.get("future_forecast_available"):
        st.warning(status.get("reason") or "Faltan entradas para la previsión de 48 horas.")

    forecast = st.session_state.get("demand_forecast")
    last_observation = status.get("last_demand_utc")
    if forecast and (forecast.get("prediction_mode") == "historical_simulation"
                     or not is_current(forecast, last_observation)):
        if forecast.get("prediction_mode") != "historical_simulation":
            st.session_state.pop("demand_forecast", None)
        forecast = None
    button = st.button(
        "Actualizar previsión 48 h", type="primary", icon=":material/refresh:",
        disabled=not status.get("future_forecast_available"), key="overview_generate",
    )
    auto_generate = (forecast is None and not st.session_state.get(
        "demand_forecast_attempted") and status.get("future_forecast_available"))
    if button or auto_generate:
        _request_forecast()
        forecast = st.session_state.get("demand_forecast")

    section("Previsión de demanda", "Estimación futura separada del último valor observado · MW")
    if forecast and is_current(forecast, last_observation):
        frame = prediction_frame(forecast)
        if len(frame) == 48:
            peak = frame.loc[frame.demand_mw.idxmax()]
            valley = frame.loc[frame.demand_mw.idxmin()]
            summary = st.columns(2)
            summary[0].metric(
                "Máximo previsto · próximas 48 h", f"{peak.demand_mw:,.0f} MW",
                peak.timestamp_local.strftime("%d/%m %H:%M · Europe/Madrid"),
            )
            summary[1].metric(
                "Mínimo previsto · próximas 48 h", f"{valley.demand_mw:,.0f} MW",
                valley.timestamp_local.strftime("%d/%m %H:%M · Europe/Madrid"),
            )
            st.caption(f"Generada {_local_time(forecast.get('generated_at_utc'))} · "
                       f"modelo {forecast.get('model', 'no identificado')} · "
                       f"origen real {_local_time(forecast.get('forecast_origin_utc'))}.")
            try:
                history = api_client.cached_history(168)["records"]
                render_timeline(history, forecast, frame, "overview_forecast")
            except requests.RequestException as error:
                st.info(f"Histórico reciente no disponible ({type(error).__name__}).")
            if st.button("Abrir análisis del modelo", key="overview_details"):
                st.session_state["current_page"] = "Modelo"
                st.rerun()
        else:
            st.warning("La API no devolvió 48 horas completas; no se resumen valores parciales.")
    else:
        st.info("La previsión aparecerá cuando REData y las entradas del modelo estén listas.")

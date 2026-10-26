"""Demand-first executive view backed by the live API and saved forecasts."""

import requests
import streamlit as st

from components.forecast import (
    is_current, prediction_frame, render_summary, render_timeline,
)
from components.layout import header, section
from services import api_client


def render(datasets: dict) -> None:
    header("Demanda eléctrica peninsular", "Resumen ejecutivo de demanda y previsión horaria")
    try:
        status = api_client.cached_status()
        model = api_client.cached_model_info()
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se puede conectar con FastAPI ({type(error).__name__}).")
        st.caption(f"API configurada: {api_client.BASE_URL}")
        return

    api_col, model_col, demand_col = st.columns(3)
    api_col.metric("API", "Operativa" if status.get("api") else "No disponible")
    model_label = model.get("model_type", "No disponible") if status.get(
        "model_loaded") else "No disponible"
    model_col.metric("Modelo", model_label)
    demand_col.metric("Última demanda UTC", status.get("last_demand_utc") or "Sin datos")

    section("Previsión", "Horizonte máximo 48 horas · modelo de demanda, no de precio.")
    horizon = st.select_slider("Horizonte", options=[6, 12, 24, 48], value=24,
                               format_func=lambda hours: f"{hours} horas",
                               key="overview_horizon")
    forecast_ready = bool(status.get("future_forecast_available"))
    if not forecast_ready:
        st.warning(status.get("reason") or "No hay datos actuales suficientes para predecir.")
    if st.button("Generar previsión", type="primary", icon=":material/bolt:",
                 disabled=not forecast_ready, key="overview_generate"):
        try:
            st.session_state["demand_forecast"] = api_client.post(
                "/predict", {"horizon_hours": horizon})
            st.session_state["demand_forecast_horizon"] = horizon
        except requests.HTTPError as error:
            detail = error.response.text if error.response is not None else str(error)
            st.error(f"La API rechazó la previsión: {detail[:500]}")
        except requests.RequestException as error:
            st.error(f"No se pudo solicitar la previsión ({type(error).__name__}).")

    forecast = st.session_state.get("demand_forecast")
    if forecast and not is_current(forecast):
        st.info("La previsión guardada caducó; genera otra para actualizar el resumen.")
        st.session_state.pop("demand_forecast", None)
        forecast = None
    if forecast:
        frame = prediction_frame(forecast)
        render_summary(forecast, frame)
        try:
            history = api_client.cached_history(168)["records"]
            render_timeline(history, forecast, frame, "overview_forecast")
        except (requests.RequestException, ValueError) as error:
            st.warning(f"No se pudo cargar el histórico para el gráfico ({type(error).__name__}).")
        st.caption(f"Artefacto: {model.get('artifact_version', 'no identificado')} · "
                   f"{forecast.get('horizon_hours', len(frame))} h solicitadas · "
                   "la previsión está separada del último dato real.")
    elif forecast_ready:
        st.caption(f"Modelo cargado: {model.get('model_type', 'no identificado')} · "
                   "solicita un horizonte para calcular las tarjetas y la gráfica.")

    if st.button("Abrir análisis de picos y comparaciones", key="overview_details"):
        st.session_state["current_page"] = "Predicción"
        st.rerun()

"""Real acquisition diagnostics; no calls to a hypothetical API."""

import streamlit as st
import requests

from components.cards import project_status
from components.diagnostics import technical_details
from components.layout import header, section
from services.data_quality import aligned_data
from utils.formatting import number
from services import api_client


def render(datasets: dict) -> None:
    header("Estado del sistema", "Disponibilidad y diagnóstico de la carga histórica")
    api_status = None
    model_type = None
    section("API y modelo", f"FastAPI · {api_client.BASE_URL}")
    try:
        status = api_client.cached_status()
        model = api_client.cached_model_info()
        api_status = status
        model_type = model.get("model_type")
        st.status("API operativa" if status.get("api") else "API no disponible",
                  state="complete" if status.get("api") else "error")
        left, right, third = st.columns(3)
        left.metric("Modelo", model.get("model_type", "No disponible")
                    if status.get("model_loaded") else "No disponible")
        right.metric("Última demanda UTC", status.get("last_demand_utc") or "Sin datos")
        third.metric("Modo", "Previsión futura" if status.get("future_forecast_available")
                     else "Solo histórico")
        if not status.get("future_forecast_available"):
            st.info(status.get("reason") or "Datos actuales insuficientes para predecir.")
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se pudo consultar FastAPI ({type(error).__name__}).")
    with st.container(border=True, key="panel-system-status"):
        section("Estado general")
        project_status(datasets, dashboard=True, api_status=api_status,
                       model_type=model_type)
    section("Diagnóstico", "Origen de los datos, avisos y comprobaciones temporales.")
    for dataset in datasets.values():
        technical_details(dataset, dataset.label)
    with st.expander("Alineación temporal"):
        joined, explanation = aligned_data(datasets["demand"], datasets["weather"])
        st.caption(explanation)
        if not joined.empty:
            st.markdown(f"**Instantes comunes:** {number(len(joined))}. "
                        "Los registros sin pareja se conservan en sus históricos independientes.")
    st.caption("Actualizar datos renueva la lectura de los históricos. "
               "Las cargas manuales se mantienen en memoria durante la sesión.")

"""Entry point: configuration, navigation and composition only."""

import streamlit as st

from components.layout import apply_theme, sidebar
from services import api_client
from services.data_loader import load_local, load_upload, refresh
from views import data, demand, historical, overview, prediction, prices, system


st.set_page_config(
    page_title="Predicción Eléctrica", page_icon=":material/bolt:", layout="wide",
    initial_sidebar_state="auto",
)
page, theme, reload_requested = sidebar()
apply_theme(theme)
if reload_requested:
    refresh()
    api_client.cached_status.clear()
    api_client.cached_history.clear()
    api_client.cached_history_between.clear()
    try:
        api_client.refresh_sources()
    except Exception as error:
        st.session_state["source_refresh_error"] = type(error).__name__
    else:
        st.session_state.pop("source_refresh_error", None)

with st.spinner("Cargando datos históricos…"):
    datasets = {"demand": load_local("demand")}
for kind, dataset in datasets.items():
    content = st.session_state.get(f"manual_{kind}")
    if not dataset.available and content is not None:
        datasets[kind] = load_upload(kind, content, dataset.checked_paths, dataset.errors)

views = {
    "Resumen": overview.render,
    "Demanda": demand.render,
    "Análisis histórico": historical.render,
    "Modelo": prediction.render,
    "Precios": prices.render,
    "Datos": data.render,
    "Estado del sistema": system.render,
}
views[page](datasets)

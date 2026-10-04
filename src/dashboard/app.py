"""Entry point: configuration, navigation and composition only."""

import streamlit as st

from components.layout import apply_theme, sidebar
from services.data_loader import load_local, load_upload, refresh
from views import data, demand, historical, overview, prediction, system, weather


st.set_page_config(
    page_title="Predicción Eléctrica", page_icon=":material/bolt:", layout="wide",
    initial_sidebar_state="auto",
)
page, theme, reload_requested = sidebar()
apply_theme(theme)
if reload_requested:
    refresh()

with st.spinner("Cargando datos históricos…"):
    datasets = {kind: load_local(kind) for kind in ("demand", "weather")}
for kind, dataset in datasets.items():
    content = st.session_state.get(f"manual_{kind}")
    if not dataset.available and content is not None:
        datasets[kind] = load_upload(kind, content, dataset.checked_paths, dataset.errors)

views = {
    "Resumen": overview.render,
    "Demanda": demand.render,
    "Meteorología": weather.render,
    "Análisis histórico": historical.render,
    "Predicción": prediction.render,
    "Datos": data.render,
    "Estado del sistema": system.render,
}
views[page](datasets)

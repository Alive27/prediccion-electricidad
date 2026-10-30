"""Entry point: configuration, navigation and composition only."""

import streamlit as st

from components.layout import apply_theme, sidebar
from services.data_loader import load_local, load_upload
from views import data, overview, prediction, prices, system


st.set_page_config(
    page_title="Predicción Eléctrica", page_icon=":material/bolt:", layout="wide",
    initial_sidebar_state="auto",
)
page, theme = sidebar()
apply_theme(theme)
datasets = {}
if page != "Precios":
    with st.spinner("Cargando datos históricos…"):
        datasets = {"demand": load_local("demand")}
    for kind, dataset in datasets.items():
        content = st.session_state.get(f"manual_{kind}")
        if not dataset.available and content is not None:
            datasets[kind] = load_upload(kind, content, dataset.checked_paths, dataset.errors)

views = {
    "Resumen": overview.render,
    "Modelo": prediction.render,
    "Precios": prices.render,
    "Datos": data.render,
    "Estado del sistema": system.render,
}
views[page](datasets)

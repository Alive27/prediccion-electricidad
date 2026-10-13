"""Honest placeholder for the future trained-model integration."""

import streamlit as st

from components.icons import svg
from components.layout import header
from components.states import empty


def render(datasets: dict) -> None:
    header("Predicción de demanda", "Predicción horaria de las próximas 24 horas")
    empty("Modelo predictivo pendiente de integración",
          "Esta sección se activará cuando el modelo entrenado esté disponible a través de la API.",
          "model", flow=True)
    cards = "".join(
        f'<div class="pending-card"><span class="pending-icon">{svg("lock")}</span>'
        f'<h3>{label}</h3><span class="badge pending">Pendiente</span></div>'
        for label in ("Predicción próximas 24 h", "Real vs predicción", "Métricas", "Baseline")
    )
    st.markdown(f'<div class="pending-grid">{cards}</div>', unsafe_allow_html=True)
    st.button("Generar predicción", disabled=True, type="primary", icon=":material/bolt:",
              help="Disponible cuando el modelo esté conectado.")
    st.caption("Disponible cuando el modelo esté conectado.")

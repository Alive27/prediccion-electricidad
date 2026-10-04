"""Real acquisition diagnostics; no calls to a hypothetical API."""

import streamlit as st

from components.cards import project_status
from components.diagnostics import technical_details
from components.layout import header, section
from services.data_quality import aligned_data
from utils.formatting import number


def render(datasets: dict) -> None:
    header("Estado del sistema", "Disponibilidad y diagnóstico de la carga histórica")
    with st.container(border=True, key="panel-system-status"):
        section("Estado general")
        project_status(datasets, dashboard=True)
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

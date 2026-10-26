"""Price page intentionally parked until a real source is connected."""

import streamlit as st

from components.layout import header, section


def render(datasets: dict) -> None:
    header("Precios", "Funcionalidad pendiente de integrar")
    section("Fuente no conectada", "Esta versión no consulta ni muestra series de precios.")
    st.info("Los precios horarios requieren una fuente real configurada y validada. "
            "No hay una API de precios conectada ni se muestran datos de ejemplo.")
    st.caption("El modelo guardado predice demanda en MW; no estima precios en €/MWh.")

"""Execution bar of the prediction pages (demand model and prices).

It is rendered last on the page and floats over the bottom edge of the window (a visionOS
"ornament"), staying at the bottom of the screen while the page scrolls.
"""

import streamlit as st

from utils.formatting import local_datetime

HORIZONS = [6, 12, 24, 48]


def run_bar(key: str, action: str = "Generar previsión", default: int = 48,
            enabled: bool = True, confirm: str | None = None) -> tuple[int, bool, bool]:
    """Horizon selector, optional confirmation and the primary button.

    Returns (horizon, generate_clicked, confirmed). The selected horizon is kept in
    session state, so it survives switching pages (Streamlit drops the state of widgets
    that are not rendered).
    """
    remembered = st.session_state.get(f"{key}_horizon_selected", default)
    with st.container(key=f"ornament-{key}", horizontal=True, vertical_alignment="center",
                      gap="small"):
        st.markdown('<span class="v-orn-label">Horizonte</span>', unsafe_allow_html=True)
        horizon = st.segmented_control(
            "Horizonte de predicción", HORIZONS, default=remembered, key=f"{key}_horizon",
            format_func=lambda value: f"{value} h", label_visibility="collapsed",
            required=True)
        st.session_state[f"{key}_horizon_selected"] = horizon
        confirmed = True
        if confirm:
            confirmed = st.checkbox(confirm, key=f"{key}_confirm")
        generate = st.button(action, type="primary", icon=":material/bolt:",
                             disabled=not enabled or not confirmed, key=f"{key}_generate")
    return horizon, generate, confirmed


def generation_notice(shown: int, horizon: int, generated_utc: str | None,
                      outdated: bool = False) -> None:
    """Explain that the persisted generation differs from the current selection."""
    when = local_datetime(generated_utc) if generated_utc else "sin fecha"
    if shown != horizon:
        st.info(f"Se muestra la última generación: {shown} horas, calculada el {when}. "
                f"Pulsa «Generar previsión» para calcular {horizon} horas.")
    elif outdated:
        st.info(f"Generación calculada el {when}. Puede haber datos más recientes; "
                "pulsa «Generar» para actualizarla.")


def pending(horizon: int, what: str) -> None:
    st.info(f"Pulsa «Generar previsión» para calcular {what} de las próximas {horizon} horas "
            "con el modelo existente.")

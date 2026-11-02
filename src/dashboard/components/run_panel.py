"""Shared execution interface of the prediction pages (demand model and prices)."""

from html import escape

import streamlit as st

from components.layout import section
from utils.formatting import local_datetime

HORIZONS = [6, 12, 24, 48]


def status_chips(chips: list[tuple[str, str, str]]) -> None:
    """Compact strip of (tone, label, value); tone is success, warning, error or ''."""
    html = "".join(
        f'<span class="chip"><span class="dot {tone}"></span>{escape(label)} '
        f'<strong>{escape(str(value))}</strong></span>' for tone, label, value in chips)
    st.markdown(f'<div class="chip-row">{html}</div>', unsafe_allow_html=True)


def run_panel(key: str, description: str, action: str, default: int = 48,
              enabled: bool = True, confirm: str | None = None) -> tuple[int, bool, bool]:
    """Horizon selector, optional confirmation and the primary "Generar" button.

    Returns (horizon, generate_clicked, confirmed). The selected horizon is kept
    in session state, so it survives switching pages (Streamlit drops the state
    of widgets that are not rendered).
    """
    remembered = st.session_state.get(f"{key}_horizon_selected", default)
    with st.container(key=f"panel-run-{key}"):
        section("Configuración de la ejecución", description)
        selector, detail = st.columns([1, 2.4], vertical_alignment="bottom")
        with selector:
            horizon = st.selectbox("Horizonte de predicción", HORIZONS,
                                   index=HORIZONS.index(remembered),
                                   format_func=lambda value: f"{value} horas",
                                   key=f"{key}_horizon")
        st.session_state[f"{key}_horizon_selected"] = horizon
        with detail:
            st.caption(f"Se solicitarán {horizon} predicciones horarias al modelo · "
                       "resolución de salida: 1 hora.")
        confirmed = True
        if confirm:
            confirmed = st.checkbox(confirm, key=f"{key}_confirm")
        generate = st.button(f"{action} {horizon} h", type="primary", icon=":material/bolt:",
                             disabled=not enabled or not confirmed, key=f"{key}_generate")
    return horizon, generate, confirmed


def generation_notice(shown: int, horizon: int, generated_utc: str | None,
                      outdated: bool = False) -> None:
    """Explain that the persisted generation differs from the current selection."""
    when = local_datetime(generated_utc) if generated_utc else "sin fecha"
    if shown != horizon:
        st.info(f"Se muestra la última generación: {shown} horas, calculada el {when}. "
                f"Pulsa «Generar» para calcular {horizon} horas.")
    elif outdated:
        st.info(f"Generación calculada el {when}. Puede haber datos más recientes; "
                "pulsa «Generar» para actualizarla.")


def pending(horizon: int, what: str) -> None:
    st.info(f"Pulsa «Generar» para calcular {what} de las próximas {horizon} horas con el "
            "modelo existente.")

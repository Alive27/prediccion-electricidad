"""Sidebar, page headings and CSS-only appearance preferences."""

from html import escape
from pathlib import Path

import streamlit as st

from components.icons import svg

PAGES = ["Resumen", "Demanda", "Meteorología", "Análisis histórico", "Predicción",
         "Datos", "Estado del sistema"]
TOKENS = {
    "Claro": "--scheme:light;--paper:#f6f7f9;--card:#ffffff;--sidebar:#fcfcfd;"
             "--ink:#202630;--muted:#596575;--border:#e3e6eb;--blue:#2466ce;"
             "--soft:#edf3fd;--success:#187448;--amber:#90600f;--error:#b83232;"
             "--grid:#e9ecf0;--shadow:0 2px 8px rgba(20,30,45,.025);",
    "Oscuro": "--scheme:dark;--paper:#17191e;--card:#202329;--sidebar:#1c1e24;"
              "--ink:#f1f2f5;--muted:#b0b7c4;--border:#343840;--blue:#8ab8ff;"
              "--soft:#27344a;--success:#83d9ac;--amber:#f0c272;--error:#ff9999;"
              "--grid:#343840;--shadow:0 2px 8px rgba(0,0,0,.09);",
}
NAV_GROUPS = (
    ("EXPLORAR", (("Resumen", "dashboard"), ("Demanda", "show_chart"),
                  ("Meteorología", "thermostat"), ("Análisis histórico", "analytics"))),
    ("MODELO", (("Predicción", "model_training"),)),
    ("SISTEMA", (("Datos", "database"), ("Estado del sistema", "monitor_heart"))),
)


def navigate(page: str) -> None:
    st.session_state["current_page"] = page


def sidebar() -> tuple[str, str, bool]:
    with st.sidebar:
        st.markdown(
            f'<div class="brand"><span class="brand-icon">{svg("bolt")}</span>'
            '<div><strong>Predicción Eléctrica</strong><small>España peninsular</small>'
            '</div></div>', unsafe_allow_html=True,
        )
        page = st.session_state.get("current_page", "Resumen")
        with st.container(key="sidebar-nav"):
            for group, items in NAV_GROUPS:
                st.markdown(f'<div class="nav-heading">{group}</div>',
                            unsafe_allow_html=True)
                for label, icon in items:
                    st.button(label, icon=f":material/{icon}:", width="stretch",
                              type="primary" if label == page else "tertiary",
                              key=f"nav_{PAGES.index(label)}", on_click=navigate, args=(label,))
        with st.container(key="sidebar-tools"):
            theme = st.selectbox("Tema", ["Sistema", "Claro", "Oscuro"], key="appearance")
            reload_requested = st.button("Actualizar datos", icon=":material/refresh:",
                                         type="tertiary", key="refresh_data",
                                         help="Renueva la lectura de los históricos en memoria.")
            st.caption("Datos históricos reales")
    return page, theme, reload_requested


def apply_theme(theme: str) -> None:
    styles = (Path(__file__).resolve().parents[1] / "styles" / "dashboard.css").read_text(
        encoding="utf-8",
    )
    tokens = TOKENS.get(theme, TOKENS["Claro"])
    system = ""
    if theme == "Sistema":
        system = f"@media(prefers-color-scheme:dark){{:root{{{TOKENS['Oscuro']}}}}}"
    st.markdown(f"<style>:root{{{tokens}}}{system}{styles}</style>", unsafe_allow_html=True)


def header(title: str, subtitle: str, eyebrow: str = "ESPAÑA PENINSULAR") -> None:
    st.markdown(
        f'<div class="page-heading"><div class="eyebrow">{escape(eyebrow)}</div>'
        f'<h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div>', unsafe_allow_html=True,
    )


def section(title: str, subtitle: str = "") -> None:
    detail = f'<p>{escape(subtitle)}</p>' if subtitle else ""
    st.markdown(f'<div class="section-heading"><h2>{escape(title)}</h2>{detail}</div>',
                unsafe_allow_html=True)

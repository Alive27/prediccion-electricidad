"""Sidebar, page headings and CSS-only appearance preferences."""

from html import escape
from pathlib import Path

import streamlit as st

from components.icons import svg

PAGES = ["Resumen", "Modelo", "Precios", "Datos", "Estado del sistema"]
TOKENS = {
    "Sistema": (
        "--scheme:dark;--paper:#07130f;--card:#0d1d17;--sidebar:#091711;"
        "--ink:#e7f5ed;--muted:#8da99a;--border:#1c3a2c;--blue:#21d18b;"
        "--soft:#103b2a;--success:#54e6a4;--amber:#f0c272;--error:#ff9999;"
        "--grid:#1a382a;--shadow:0 12px 32px rgba(0,0,0,.28);"
    ),
    "Claro": "--scheme:light;--paper:#f6f7f9;--card:#ffffff;--sidebar:#fcfcfd;"
             "--ink:#17251f;--muted:#60746a;--border:#d9e7df;--blue:#078c63;"
             "--soft:#e5f6ed;--success:#08794f;--amber:#9a6c12;--error:#b83232;"
             "--grid:#deebe3;--shadow:0 12px 32px rgba(13,58,39,.07);",
    "Oscuro": "--scheme:dark;--paper:#07130f;--card:#0d1d17;--sidebar:#091711;"
              "--ink:#e7f5ed;--muted:#8da99a;--border:#1c3a2c;--blue:#21d18b;"
              "--soft:#103b2a;--success:#54e6a4;--amber:#f0c272;--error:#ff9999;"
              "--grid:#1a382a;--shadow:0 12px 32px rgba(0,0,0,.28);",
}
NAV_GROUPS = (
    ("PREDICCIONES", (("Resumen", "dashboard"), ("Modelo", "model_training"),
                      ("Precios", "payments"))),
    ("DATOS", (("Datos", "database"),)),
    ("ESTADO", (("Estado del sistema", "monitor_heart"),)),
)


def navigate(page: str) -> None:
    st.session_state["current_page"] = page


def sidebar() -> tuple[str, str]:
    with st.sidebar:
        st.markdown(
            f'<div class="brand"><span class="brand-icon">{svg("bolt")}</span>'
            '<div><strong>Predicción Eléctrica</strong><small>España peninsular</small>'
            '</div></div>', unsafe_allow_html=True,
        )
        # Deep link (?page=Precios), also used by the 3D city as a fallback.
        requested = st.query_params.get("page")
        if requested in PAGES:
            st.session_state["current_page"] = requested
            del st.query_params["page"]
        if st.session_state.get("current_page") not in PAGES:
            st.session_state["current_page"] = "Resumen"
        page = st.session_state["current_page"]
        with st.container(key="sidebar-nav"):
            for group, items in NAV_GROUPS:
                st.markdown(f'<div class="nav-heading">{group}</div>',
                            unsafe_allow_html=True)
                for label, icon in items:
                    st.button(label, icon=f":material/{icon}:", width="stretch",
                              type="primary" if label == page else "tertiary",
                              key=f"nav_{PAGES.index(label)}", on_click=navigate, args=(label,))
        if "appearance" not in st.session_state:
            st.session_state["appearance"] = "Sistema"
        theme = st.selectbox("Tema", ["Sistema", "Claro", "Oscuro"], key="appearance")
    return page, theme


def apply_theme(theme: str) -> None:
    styles = (Path(__file__).resolve().parents[1] / "styles" / "dashboard.css").read_text(
        encoding="utf-8",
    )
    tokens = TOKENS.get(theme, TOKENS["Claro"])
    system = (f"@media(prefers-color-scheme:light){{:root{{{TOKENS['Claro']}}}}}"
              if theme == "Sistema" else "")
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

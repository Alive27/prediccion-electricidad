"""Sidebar, page headings and CSS-only appearance preferences."""

from html import escape
from pathlib import Path

import streamlit as st

from components.icons import svg

PAGES = ["Resumen", "Modelo", "Precios", "Datos", "Estado del sistema"]
# Colour tokens per appearance. Everything else (type, spacing, radii, motion) lives in
# styles/dashboard.css. Surfaces go from the page background (--bg) to panels
# (--surface), cards inside them (--surface-2) and floating layers (--surface-3).
_DARK = (
    "--scheme:dark;"
    "--bg:#070b11;--bg-glow-1:rgba(53,208,224,.16);--bg-glow-2:rgba(79,124,255,.14);"
    "--bg-glow-3:rgba(124,92,246,.09);"
    "--surface:rgba(26,35,48,.46);--surface-solid:#111922;--surface-2:rgba(255,255,255,.04);"
    "--sheen:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.012) 46%);"
    "--surface-3:rgba(16,23,32,.94);--sidebar:rgba(11,16,23,.86);"
    "--line:rgba(255,255,255,.085);--line-strong:rgba(255,255,255,.15);"
    "--highlight:rgba(255,255,255,.09);"
    "--segment:rgba(255,255,255,.11);"
    "--ink:#e9eef5;--ink-2:#a7b2c0;--ink-3:#6f7b8b;"
    "--accent:#35d0e0;--accent-strong:#67e3ef;--accent-soft:rgba(53,208,224,.13);"
    "--accent-ink:#04141a;--electric:#5b8cff;"
    "--success:#3ddc97;--warning:#f2b45a;--danger:#ff6b6b;"
    "--grid:rgba(148,163,184,.12);"
    "--shadow-1:0 1px 0 rgba(255,255,255,.04) inset,0 10px 30px rgba(0,0,0,.32);"
    "--shadow-2:0 1px 0 rgba(255,255,255,.06) inset,0 24px 60px rgba(0,0,0,.48);"
)
TOKENS = {
    "Sistema": _DARK,
    "Claro": (
        "--scheme:light;"
        "--bg:#eaeff5;--bg-glow-1:rgba(10,165,184,.16);--bg-glow-2:rgba(79,124,255,.13);"
        "--bg-glow-3:rgba(124,92,246,.08);"
        "--surface:rgba(255,255,255,.58);--surface-solid:#ffffff;"
        "--sheen:linear-gradient(180deg,rgba(255,255,255,.75),rgba(255,255,255,.25) 46%);"
        "--surface-2:rgba(15,23,42,.028);--surface-3:rgba(255,255,255,.97);"
        "--sidebar:rgba(248,250,252,.9);"
        "--line:rgba(15,23,42,.08);--line-strong:rgba(15,23,42,.16);"
        "--highlight:rgba(255,255,255,.7);"
        "--segment:#ffffff;"
        "--ink:#0f1722;--ink-2:#4a5667;--ink-3:#7a8697;"
        "--accent:#0a9db0;--accent-strong:#077f8f;--accent-soft:rgba(10,157,176,.11);"
        "--accent-ink:#ffffff;--electric:#3d6fe6;"
        "--success:#12915f;--warning:#b7791f;--danger:#d64545;"
        "--grid:rgba(15,23,42,.08);"
        "--shadow-1:0 1px 0 rgba(255,255,255,.8) inset,0 8px 24px rgba(15,23,42,.07);"
        "--shadow-2:0 1px 0 rgba(255,255,255,.8) inset,0 20px 48px rgba(15,23,42,.12);"
    ),
    "Oscuro": _DARK,
}
THEME_ICONS = {"Sistema": ":material/contrast:", "Claro": ":material/light_mode:",
               "Oscuro": ":material/dark_mode:"}
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
        if st.session_state.get("appearance") not in THEME_ICONS:
            st.session_state["appearance"] = "Sistema"
        with st.container(key="sidebar-theme"):
            theme = st.segmented_control(
                "Apariencia", list(THEME_ICONS), key="appearance", required=True,
                format_func=lambda name: f"{THEME_ICONS[name]} {name}", width="stretch")
    return page, theme


def apply_theme(theme: str) -> None:
    styles = (Path(__file__).resolve().parents[1] / "styles" / "dashboard.css").read_text(
        encoding="utf-8",
    )
    tokens = TOKENS.get(theme, TOKENS["Claro"])
    system = (f"@media(prefers-color-scheme:light){{:root{{{TOKENS['Claro']}}}}}"
              if theme == "Sistema" else "")
    st.markdown(f"<style>:root{{{tokens}}}{system}{styles}</style>", unsafe_allow_html=True)


def header(title: str, subtitle: str, eyebrow: str = "SISTEMA ELÉCTRICO PENINSULAR") -> None:
    st.markdown(
        f'<div class="page-heading"><div class="eyebrow">{escape(eyebrow)}</div>'
        f'<h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div>', unsafe_allow_html=True,
    )


def section(title: str, subtitle: str = "") -> None:
    detail = f'<p>{escape(subtitle)}</p>' if subtitle else ""
    st.markdown(f'<div class="section-heading"><h2>{escape(title)}</h2>{detail}</div>',
                unsafe_allow_html=True)

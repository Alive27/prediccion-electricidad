"""Sidebar, page headings and CSS-only appearance preferences."""

from html import escape
from pathlib import Path

import streamlit as st

from components.icons import svg

PAGES = ["Resumen", "Modelo", "Precios", "Datos", "Estado del sistema"]
# Colour tokens per appearance ("Vision" style: glass over a blurred render of the city).
# Everything else (type, spacing, radii, motion) lives in styles/dashboard.css.
# Layers: backdrop image > glass window and rail (--surface, --sidebar) > platters inside
# the window (--surface-2) > popovers and menus (--surface-3).
_DARK = (
    "--scheme:dark;--bg:#0b1016;"
    "--backdrop:url('/app/static/ui/backdrop-dark.webp');"
    "--veil:linear-gradient(180deg,rgba(8,12,18,.30),rgba(8,12,18,.62));"
    "--surface:rgba(44,50,60,.46);--surface-solid:#1b2129;--surface-2:rgba(255,255,255,.065);"
    "--surface-3:rgba(30,35,43,.95);--sidebar:rgba(44,50,60,.50);"
    "--overlay:rgba(36,42,51,.55);"
    "--line:rgba(255,255,255,.09);--line-strong:rgba(255,255,255,.17);"
    "--highlight:rgba(255,255,255,.14);--segment:rgba(255,255,255,.24);"
    "--ink:#ffffff;--ink-2:rgba(255,255,255,.74);--ink-3:rgba(255,255,255,.58);"
    "--accent:#64d2ff;--accent-strong:#9be3ff;--accent-soft:rgba(100,210,255,.16);"
    "--accent-ink:#04202e;--electric:#9d8cff;"
    "--success:#30d158;--warning:#ffcc4d;--danger:#ff6961;"
    "--grid:rgba(255,255,255,.09);--fill:rgba(255,255,255,.07);--zebra:rgba(255,255,255,.05);"
    "--chip:rgba(255,255,255,.10);--chip-line:rgba(255,255,255,.12);--well:rgba(255,255,255,.11);"
    "--valle:#30d158;--llano:#ffd60a;--punta:#ff6961;--pos:#ffb37a;--neg:#7cf29a;"
    "--note-bg:rgba(100,210,255,.14);--note-ink:#bcebff;"
    "--shadow-1:0 1px 0 rgba(255,255,255,.06) inset;"
    "--shadow-2:0 1px 0 rgba(255,255,255,.12) inset,0 30px 70px rgba(0,0,0,.40);"
)
TOKENS = {
    "Sistema": _DARK,
    "Claro": (
        "--scheme:light;--bg:#e8edf2;"
        "--backdrop:url('/app/static/ui/backdrop-light.webp');"
        "--veil:linear-gradient(180deg,rgba(240,244,248,.20),rgba(240,244,248,.55));"
        "--surface:rgba(255,255,255,.55);--surface-solid:#ffffff;"
        "--surface-2:rgba(255,255,255,.62);--surface-3:rgba(255,255,255,.97);"
        "--sidebar:rgba(255,255,255,.58);--overlay:rgba(255,255,255,.86);"
        "--line:rgba(15,23,42,.08);--line-strong:rgba(15,23,42,.14);"
        "--highlight:rgba(255,255,255,.9);--segment:#ffffff;"
        "--ink:#0b1220;--ink-2:#3c4655;--ink-3:#586374;"
        "--accent:#0071c2;--accent-strong:#005a9c;--accent-soft:rgba(0,113,194,.10);"
        "--accent-ink:#ffffff;--electric:#6b5ce7;"
        "--success:#15803d;--warning:#a15c00;--danger:#c0392b;"
        "--grid:rgba(15,23,42,.08);--fill:rgba(15,23,42,.045);--zebra:rgba(15,23,42,.04);"
        "--chip:rgba(255,255,255,.72);--chip-line:rgba(15,23,42,.10);--well:rgba(15,23,42,.06);"
        "--valle:#1f9d4f;--llano:#b07d00;--punta:#d63a2f;--pos:#b45309;--neg:#15803d;"
        "--note-bg:rgba(0,113,194,.10);--note-ink:#0b4a99;"
        "--shadow-1:0 1px 0 rgba(255,255,255,.9) inset;"
        "--shadow-2:0 1px 0 rgba(255,255,255,.9) inset,0 24px 60px rgba(15,23,42,.14);"
    ),
    "Oscuro": _DARK,
}
THEME_ICONS = {"Sistema": ":material/desktop_windows:", "Claro": ":material/light_mode:",
               "Oscuro": ":material/dark_mode:"}
NAV_GROUPS = (
    ("Predicciones", (("Resumen", "dashboard"), ("Modelo", "model_training"),
                      ("Precios", "payments"))),
    ("Datos", (("Datos", "database"),)),
    ("Estado", (("Estado del sistema", "monitor_heart"),)),
)


def navigate(page: str) -> None:
    st.session_state["current_page"] = page


def sidebar() -> tuple[str, str]:
    with st.sidebar:
        st.markdown(
            f'<div class="brand"><span class="brand-icon">{svg("bolt", stroke=2.2)}</span>'
            '<strong>Energía<br>Peninsular</strong></div>', unsafe_allow_html=True,
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
            # Icons only on screen; the name stays in the markup for screen readers.
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


def heading_html(title: str, subtitle: str, eyebrow: str) -> str:
    return (f'<div class="page-heading"><div class="eyebrow">{escape(eyebrow)}</div>'
            f'<h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div>')


def header(title: str, subtitle: str, eyebrow: str = "SISTEMA ELÉCTRICO PENINSULAR") -> None:
    st.markdown(heading_html(title, subtitle, eyebrow), unsafe_allow_html=True)

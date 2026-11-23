"""HTML building blocks of the "Vision" interface; their look lives in styles/dashboard.css.

Every function returns markup (so blocks can be nested inside one card); `html()` renders it.
Values passed in are already formatted for display.
"""

from html import escape

import streamlit as st

from components.icons import svg


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def card(*parts: str, extra: str = "") -> str:
    return f'<section class="v-card {extra}">{"".join(parts)}</section>'


def card_header(title: str, subtitle: str = "", right: str = "") -> str:
    detail = f"<p>{escape(subtitle)}</p>" if subtitle else ""
    return f'<div class="v-ct"><div><h3>{escape(title)}</h3>{detail}</div>{right}</div>'


def subheading(text: str) -> str:
    return f'<div class="v-sub-h">{escape(text)}</div>'


def legend(items: list[tuple[str, str, str]]) -> str:
    """Chart legend from (colour, label, mark); mark is "line", "dash" or "square"."""
    keys = "".join(f'<span><i class="v-lg {mark}" style="background:{color}"></i>{escape(label)}'
                   f'</span>' for color, label, mark in items)
    return f'<div class="v-legend">{keys}</div>'


def dot(tone: str = "success") -> str:
    return f'<i class="v-dot {tone}"></i>'


def chip(text: str, tone: str | None = "success", icon: str | None = None) -> str:
    mark = (f'<span class="v-chip-ic">{svg(icon, 14)}</span>' if icon
            else dot(tone) if tone else "")
    return f'<span class="v-chip">{mark}{escape(text)}</span>'


def chip_row(chips: list[str], note: str = "") -> str:
    muted = f'<span class="v-muted">{escape(note)}</span>' if note else ""
    return f'<div class="v-chips">{"".join(chips)}{muted}</div>'


def metric(label: str, value: str, unit: str = "", detail: str = "", tone: str | None = None,
           extra: str = "") -> str:
    mark = dot(tone) if tone else ""
    unit_html = f"<small>{escape(unit)}</small>" if unit else ""
    detail_html = f'<div class="v-m-s">{escape(detail)}</div>' if detail else ""
    return (f'<div class="v-m"><div class="v-m-l">{escape(label)}</div>'
            f'<div class="v-m-v">{mark}{escape(value)}{unit_html}</div>{detail_html}{extra}</div>')


def grid(items: list[str], columns: int = 4, extra: str = "") -> str:
    return (f'<div class="v-grid {extra}" style="--cols:{columns}">{"".join(items)}</div>')


def mini(label: str, value: str, detail: str = "") -> str:
    detail_html = f'<div class="v-m-s">{escape(detail)}</div>' if detail else ""
    return (f'<div class="v-mini"><div class="v-m-l">{escape(label)}</div>'
            f'<div class="v-mini-v">{escape(value)}</div>{detail_html}</div>')


def meter(share: float, tone: str = "success") -> str:
    width = max(0.0, min(1.0, share)) * 100
    return f'<div class="v-meter"><i class="{tone}" style="width:{width:.1f}%"></i></div>'


def note(text: str, icon: str = "info") -> str:
    return f'<div class="v-note">{svg(icon, 16)}<span>{escape(text)}</span></div>'


def rank_bars(rows: list[tuple[str, float, str]]) -> str:
    """Ranked horizontal bars from (label, share 0-1, value text)."""
    items = "".join(
        f'<div class="v-rk"><span class="v-rk-n">{index}</span><span class="v-rk-h">'
        f'{escape(label)}</span><span class="v-rk-bar"><i style="width:{share * 100:.1f}%"></i>'
        f'</span><span class="v-rk-v">{escape(value)}</span></div>'
        for index, (label, share, value) in enumerate(rows, start=1))
    return f'<div class="v-rank">{items}</div>'


def icon_tile(icon: str, color: str, size: str = "") -> str:
    return f'<span class="v-tile {size}" style="--nc:{color}">{svg(icon, stroke=2)}</span>'


def status_list(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Grouped list of (icon, tile colour, label, value, tone)."""
    items = "".join(
        f'<div class="v-row">{icon_tile(icon, color, "sm")}<span class="v-row-l">'
        f'{escape(label)}</span><span class="v-row-v">{escape(value)}{dot(tone)}</span></div>'
        for icon, color, label, value, tone in rows)
    return f'<div class="v-list">{items}</div>'


def ornament(text: str, tone: str | None = None, icon: str | None = None) -> str:
    """Static capsule under the window (e.g. the time of the last check)."""
    mark = svg(icon, 14) if icon else dot(tone) if tone else ""
    return f'<div class="v-orn-static"><span>{mark}{escape(text)}</span></div>'

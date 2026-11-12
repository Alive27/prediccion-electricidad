"""HTML tables with fixed column widths, aligned figures and a sticky header.

Replaces Streamlit's canvas tables in the prediction pages so they follow the
dashboard tokens (styles/dashboard.css, `.ui-table`). Cells are formatted text;
the data itself is computed by the views.
"""

from dataclasses import dataclass
from html import escape

import streamlit as st

from utils.formatting import number


@dataclass(frozen=True)
class Column:
    label: str
    width: str              # share of the table width, e.g. "30%"
    numeric: bool = False   # right-aligned, tabular figures


def mw(value) -> str:
    """Megawatts without decimals, Spanish separators; empty for missing values."""
    return "" if value is None or value != value else number(value)


def signed_mw(value) -> tuple[str, str]:
    """Signed difference and its tone class (up / down / '')."""
    if value is None or value != value:
        return "", ""
    rounded = round(float(value))
    if rounded == 0:
        return "0", ""
    sign = "+" if rounded > 0 else "−"
    return f"{sign}{number(abs(rounded))}", "up" if rounded > 0 else "down"


def _cell(value, column: Column) -> str:
    text, tone = value if isinstance(value, tuple) else (value, "")
    classes = " ".join(filter(None, ["num" if column.numeric else "", tone]))
    content = escape(str(text)) if text not in (None, "") else '<span class="empty">—</span>'
    return f'<td class="{classes}">{content}</td>'


def styled_table(columns: list[Column], rows: list[list], title: str | None = None,
                 note: str | None = None, max_height: int | None = 420,
                 label: str = "Tabla de datos") -> None:
    colgroup = "".join(f'<col style="width:{column.width}">' for column in columns)
    head = "".join(f'<th class="{"num" if column.numeric else ""}">{escape(column.label)}</th>'
                   for column in columns)
    body = "".join("<tr>" + "".join(_cell(value, column) for value, column in zip(row, columns))
                   + "</tr>" for row in rows)
    heading = f'<div class="ui-table-title">{escape(title)}</div>' if title else ""
    footer = f'<div class="ui-table-note">{escape(note)}</div>' if note else ""
    limit = f' style="max-height:{max_height}px"' if max_height else ""
    st.markdown(
        f'<div class="ui-table-card">{heading}<div class="ui-table-scroll"{limit} role="region" '
        f'aria-label="{escape(label)}" tabindex="0"><table class="ui-table"><colgroup>{colgroup}'
        f'</colgroup><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
        f'{footer}</div>', unsafe_allow_html=True)

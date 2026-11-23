"""HTML tables with fixed column widths, aligned figures, zebra rows and a sticky header.

Replaces Streamlit's canvas tables so they follow the dashboard tokens
(styles/dashboard.css, `.v-tb`). Cells are formatted text; the views compute the data.
A cell is a string or a (text, tone) pair; tone is a CSS class such as "up", "down",
"strong" or "band-valle".
"""

from dataclasses import dataclass
from html import escape

from components.blocks import card, card_header, html
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
    if tone.startswith("band-"):
        content = f'<i class="v-dot"></i>{escape(str(text))}'
    else:
        content = escape(str(text)) if text not in (None, "") else '<span class="empty">—</span>'
    return f'<td class="{classes}">{content}</td>'


def table_html(columns: list[Column], rows: list[list], max_height: int | None = None,
               label: str = "Tabla de datos") -> str:
    colgroup = "".join(f'<col style="width:{column.width}">' for column in columns)
    head = "".join(f'<th class="{"num" if column.numeric else ""}">{escape(column.label)}</th>'
                   for column in columns)
    body = "".join("<tr>" + "".join(_cell(value, column) for value, column in zip(row, columns))
                   + "</tr>" for row in rows)
    limit = f' style="max-height:{max_height}px"' if max_height else ""
    return (f'<div class="v-tb-scroll"{limit} role="region" aria-label="{escape(label)}" '
            f'tabindex="0"><table class="v-tb"><colgroup>{colgroup}</colgroup><thead><tr>'
            f'{head}</tr></thead><tbody>{body}</tbody></table></div>')


def styled_table(columns: list[Column], rows: list[list], title: str | None = None,
                 subtitle: str = "", note: str | None = None, max_height: int | None = 420,
                 label: str = "Tabla de datos") -> None:
    """A table in its own card."""
    footer = f'<p class="v-foot">{escape(note)}</p>' if note else ""
    html(card(card_header(title, subtitle) if title else "",
              table_html(columns, rows, max_height, title or label), footer))

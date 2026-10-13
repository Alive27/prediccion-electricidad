"""Small paginated HTML tables and safe CSV downloads of original values."""

import pandas as pd
import streamlit as st

from utils.formatting import number


def change_page(key: str, delta: int) -> None:
    st.session_state[key] += delta


def table(frame: pd.DataFrame, key: str, filename: str | None = None) -> None:
    if frame.empty:
        st.info("No hay registros que mostrar.")
        return
    page_size = 10
    total_pages = (len(frame) + page_size - 1) // page_size
    state_key = f"table_page_{key}"
    signature = (len(frame), str(frame.iloc[0].to_dict()), str(frame.iloc[-1].to_dict()))
    if st.session_state.get(f"table_signature_{key}") != signature:
        st.session_state[state_key] = 1
        st.session_state[f"table_signature_{key}"] = signature
    page = min(total_pages, max(1, st.session_state[state_key]))
    st.session_state[state_key] = page
    with st.container(key=f"table-toolbar-{key}", horizontal=True,
                      vertical_alignment="center"):
        st.caption(f"{number(len(frame))} registros")
        if filename:
            st.download_button("Exportar CSV", csv_bytes(frame), file_name=filename,
                               mime="text/csv", key=f"csv_{key}", icon=":material/download:",
                               help="Todas las filas filtradas; separador ; y decimal español.")
    start = (page - 1) * page_size
    subset = frame.iloc[start:start + page_size]
    # HTML tables support the selected CSS theme, unlike canvas-based tables.
    html = subset.to_html(index=False, escape=True, border=0,
                          float_format=lambda value: number(value, 2))
    st.markdown(f'<div class="data-table" role="region" aria-label="Tabla de registros" '
                f'tabindex="0">{html}</div>', unsafe_allow_html=True)
    with st.container(key=f"pagination-{key}", horizontal=True,
                      horizontal_alignment="center", vertical_alignment="center"):
        st.button("Anterior", key=f"previous_{key}", disabled=page == 1,
                  on_click=change_page, args=(state_key, -1))
        st.markdown(f'<div class="page-count">Página {number(page)} de '
                    f'{number(total_pages)}</div>', unsafe_allow_html=True)
        st.button("Siguiente", key=f"next_{key}", disabled=page == total_pages,
                  on_click=change_page, args=(state_key, 1))


def csv_bytes(frame: pd.DataFrame) -> bytes:
    """Escape spreadsheet formula prefixes in textual fields of uploaded sources."""
    export = frame.copy()
    text_columns = [column for column in export.columns
                    if pd.api.types.is_object_dtype(export[column].dtype)
                    or pd.api.types.is_string_dtype(export[column].dtype)]
    for column in text_columns:
        export[column] = export[column].map(
            lambda value: "'" + value if isinstance(value, str)
            and value.lstrip().startswith(("=", "+", "-", "@")) else value,
        )
    return export.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")

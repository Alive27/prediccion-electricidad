"""Empty, validation and upload states."""

from html import escape

import streamlit as st

from components.icons import svg
from components.layout import navigate
from services.data_loader import load_upload
from services.data_quality import Dataset


def empty(title: str, detail: str, icon: str = "database", flow: bool = False) -> None:
    steps = ('<div class="prediction-flow"><span>Modelo</span><span aria-hidden="true">→</span>'
             '<span>API</span><span aria-hidden="true">→</span><span>Predicción 24 h</span>'
             '</div>') if flow else ""
    st.markdown(
        f'<div class="empty-state"><span class="empty-icon">{svg(icon)}</span>'
        f'<h2>{escape(title)}</h2><p>{escape(detail)}</p>{steps}</div>', unsafe_allow_html=True,
    )


def dataset_ready(dataset: Dataset) -> bool:
    if not dataset.available:
        title = (f"No se ha podido cargar {dataset.label}." if dataset.errors
                 else "Datos históricos no disponibles en este entorno.")
        empty(title, "Carga el Parquet real en Datos para consultar este histórico.",
              "error" if dataset.errors else "database")
        if st.session_state.get("current_page", "Resumen") != "Datos":
            st.button("Ir a Datos", icon=":material/database:",
                      key=f"open_data_{dataset.kind}", on_click=navigate, args=("Datos",))
    elif dataset.errors or dataset.warnings:
        st.caption("Hay avisos de carga o calidad. Consulta los detalles en Datos.")
    if dataset.errors or dataset.warnings:
        with st.expander(f"Ver avisos de {dataset.label}"):
            for message in (*dataset.errors, *dataset.warnings):
                st.write(message)
    return dataset.available


def upload_fallback(dataset: Dataset) -> Dataset:
    """Fallback shown only in Datos; bytes survive removal of the upload widget."""
    if dataset.available:
        return dataset
    with st.container(border=True, key=f"panel-upload-{dataset.kind}"):
        st.markdown(f"**Cargar {dataset.label}**")
        st.caption("Carga temporal en memoria. Selecciona el Parquet real de este histórico.")
        uploaded = st.file_uploader(dataset.filename, type=["parquet"],
                                    key=f"upload_{dataset.kind}")
        if uploaded is not None:
            content = uploaded.getvalue()
            with st.spinner("Validando Parquet…"):
                result = load_upload(dataset.kind, content, dataset.checked_paths, dataset.errors)
            if result.available:
                st.session_state[f"manual_{dataset.kind}"] = content
                st.rerun()
            st.error("El archivo no contiene un histórico válido. Selecciona otro Parquet.")
            with st.expander("Ver detalles del archivo"):
                for message in result.errors:
                    st.write(message)
    return dataset

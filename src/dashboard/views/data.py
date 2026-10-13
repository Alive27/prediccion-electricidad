"""Source provenance, quality, paginated raw data and filtered CSV export."""

import streamlit as st

from components.cards import dataset_status, status_row
from components.diagnostics import technical_details
from components.filters import period_filter
from components.layout import header, section
from components.states import dataset_ready, upload_fallback
from components.tables import table
from services.data_quality import Dataset
from utils.formatting import day_label, frequency, number


def dataset_card(dataset: Dataset) -> None:
    with st.container(border=True, key=f"panel-data-{dataset.kind}"):
        section(dataset.label)
        status_row("Estado", *dataset_status(dataset))
        quality = dataset.quality
        if quality and dataset.available:
            st.markdown(f"**{number(quality['records'])} registros originales**")
            first, last = dataset.frame.timestamp.min(), dataset.frame.timestamp.max()
            st.caption(f"{day_label(first)} → {day_label(last)} · "
                       f"{'Hora peninsular' if quality['timezone'] else 'Hora original'}")
            st.caption(f"Frecuencia aproximada: {frequency(quality['step'])}")
            issues = (dataset.warnings or dataset.errors or sum(quality["nulls"].values())
                      or quality["duplicate_rows"])
            status_row("Calidad", "Revisar avisos" if issues else "Correcta",
                       "warning" if issues else "success")
        else:
            st.caption("Carga un histórico válido para consultar su cobertura y calidad.")
        technical_details(dataset)


def render(datasets: dict) -> None:
    header("Datos", "Origen, cobertura y calidad de los históricos cargados")
    for kind, dataset in datasets.items():
        datasets[kind] = upload_fallback(dataset)
    with st.container(key="chart-pair-data"):
        columns = st.columns(2)
    for column, dataset in zip(columns, datasets.values()):
        with column:
            dataset_card(dataset)
    label = st.selectbox("Dataset", ["REData · Demanda", "Open-Meteo · Madrid"], key="data_source")
    dataset = datasets["demand" if label.startswith("REData") else "weather"]
    if not dataset_ready(dataset):
        return
    section("Explorar registros originales",
            "Se conservan las columnas, valores y fechas del Parquet.")
    frame = period_filter(dataset, f"data_{dataset.kind}", "Todo el periodo")
    if st.session_state.get(f"period_data_{dataset.kind}") == "Todo el periodo":
        raw = dataset.raw
    else:
        raw = dataset.raw.iloc[frame.source_row.to_numpy()]
    st.caption("La tabla y el CSV conservan las columnas y la zona horaria originales.")
    table(raw, f"data_{dataset.kind}", f"{dataset.kind}_filtrado.csv")

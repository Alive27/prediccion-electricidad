"""Accessible KPI and status cards, always backed by actual observations."""

from html import escape

import pandas as pd
import streamlit as st

from services.data_quality import Dataset
from utils.formatting import number, timestamp


def metric(label: str, value: str, unit: str, detail: str) -> None:
    st.markdown(metric_html(label, value, unit, detail), unsafe_allow_html=True)


def metric_html(label: str, value: str, unit: str, detail: str) -> str:
    return (f'<div class="metric-card"><div class="metric-label">{escape(label)}</div>'
            f'<div class="metric-value">{escape(value)} <span>{escape(unit)}</span></div>'
            f'<div class="metric-detail">{escape(detail)}</div></div>')


def metric_grid(items: list[tuple[str, str, str, str]]) -> None:
    cards = "".join(metric_html(*item) for item in items)
    st.markdown(f'<div class="metric-grid" style="--metric-columns:{len(items)}">'
                f'{cards}</div>', unsafe_allow_html=True)


def demand_metrics(frame: pd.DataFrame) -> None:
    maximum = frame.loc[frame.value.idxmax()]
    minimum = frame.loc[frame.value.idxmin()]
    last = frame.iloc[-1]
    values = [
        ("Demanda media", frame.value.mean(), "Media de los registros seleccionados"),
        ("Máximo del periodo", maximum.value, timestamp(maximum.timestamp)),
        ("Mínimo del periodo", minimum.value, timestamp(minimum.timestamp)),
        ("Último valor del periodo", last.value, timestamp(last.timestamp)),
    ]
    metric_grid([(label, number(value), "MW", detail) for label, value, detail in values])


def weather_metrics(frame: pd.DataFrame) -> None:
    values = [
        ("Temperatura media", frame.value.mean(), "Media de los registros seleccionados"),
        ("Temperatura máxima", frame.value.max(), "Máximo del periodo"),
        ("Temperatura mínima", frame.value.min(), "Mínimo del periodo"),
    ]
    metric_grid([(label, number(value, 1), "°C", detail) for label, value, detail in values])


def status_row(label: str, state: str, tone: str = "pending") -> None:
    st.markdown(status_html(label, state, tone), unsafe_allow_html=True)


def status_html(label: str, state: str, tone: str = "pending") -> str:
    return (f'<div class="status-row"><span>{escape(label)}</span>'
            f'<span class="badge {tone}">{escape(state)}</span></div>')


def dataset_status(dataset: Dataset) -> tuple[str, str]:
    if dataset.available:
        return ("Disponible · revisar avisos", "warning") if dataset.warnings or dataset.errors \
            else ("Disponible", "success")
    return ("Error de lectura", "error") if dataset.errors else ("No disponible", "pending")


def project_status(datasets: dict[str, Dataset], dashboard: bool = False,
                   compact: bool = False) -> None:
    rows = [("Dashboard", "Operativo", "success")] if dashboard else []
    rows += [(label, *dataset_status(datasets[kind]))
             for kind, label in (("demand", "REData"), ("weather", "Open-Meteo"))]
    rows += [("Modelo predictivo", "Pendiente", "pending"), ("API", "Pendiente", "pending")]
    contents = "".join(status_html(*row) for row in rows)
    layout = "status-grid" if compact else "status-list"
    st.markdown(f'<div class="{layout}">{contents}</div>', unsafe_allow_html=True)

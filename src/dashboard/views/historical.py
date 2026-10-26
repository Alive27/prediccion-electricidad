"""Descriptive historical patterns, without modelling or invented metrics."""

import streamlit as st

from components import charts
from components.cards import metric_grid
from components.filters import period_filter
from components.layout import header, section
from components.states import dataset_ready
from services.analysis import monthly_profile
from utils.formatting import WEEKDAYS, number, timestamp


def render(datasets: dict) -> None:
    header("Análisis histórico", "Patrones observados en el histórico de demanda de 2024")
    dataset = datasets["demand"]
    if not dataset_ready(dataset):
        return
    st.info(f"Periodo local del archivo: {dataset.frame.timestamp.min():%d/%m/%Y} — "
            f"{dataset.frame.timestamp.max():%d/%m/%Y}. No representa la demanda actual.")
    frame = period_filter(dataset, "historical", "Todo el periodo")
    if frame.empty:
        return
    with st.container(border=True, key="panel-historical-heatmap"):
        section("El ritmo de la demanda", "Demanda media por día de semana y hora · MW. "
                "Cada celda muestra su número de observaciones al pasar el cursor.")
        charts.heatmap(frame, WEEKDAYS)
    with st.container(key="chart-pair-historical"):
        left, right = st.columns(2)
    with left, st.container(border=True, key="panel-historical-month"):
        section("Demanda media por mes")
        monthly = monthly_profile(frame)
        charts.bars(list(monthly.index), monthly["mean"], monthly["count"],
                    "MW", "historical_month", monthly=True)
    with right, st.container(border=True, key="panel-historical-distribution"):
        section("Distribución de la demanda", "Cómo se distribuyen los valores observados.")
        charts.distribution(frame)
    extremes = []
    for label, index in (("Mayor demanda observada", frame.value.idxmax()),
                         ("Menor demanda observada", frame.value.idxmin())):
        row = frame.loc[index]
        extremes.append((label, number(row.value), "MW", timestamp(row.timestamp, True)))
    metric_grid(extremes)

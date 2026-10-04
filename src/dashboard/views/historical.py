"""Descriptive historical patterns, without modelling or invented metrics."""

import streamlit as st

from components import charts
from components.cards import metric_grid
from components.filters import period_filter
from components.layout import header, section
from components.states import dataset_ready
from services.analysis import monthly_profile
from services.data_quality import aligned_data
from utils.formatting import WEEKDAYS, number, timestamp


def render(datasets: dict) -> None:
    header("Análisis histórico", "Patrones de consumo y relación con la temperatura de Madrid")
    dataset = datasets["demand"]
    if not dataset_ready(dataset):
        return
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
    with st.container(border=True, key="panel-historical-joint"):
        section("Temperatura y demanda", "Observaciones simultáneas · análisis descriptivo.")
        joined, explanation = aligned_data(dataset, datasets["weather"])
        has_common = not joined.empty
        if has_common:
            joined = joined.loc[joined.timestamp.isin(frame.timestamp)]
        if joined.empty:
            st.info("No hay instantes comunes en el periodo seleccionado."
                    if has_common else explanation)
        else:
            st.caption(f"{number(len(joined))} instantes comunes en el periodo. {explanation}")
            charts.temperature_demand(joined)
            st.caption("La relación observada no demuestra causalidad. También influyen "
                       "la hora, el calendario y otros factores no analizados aquí.")

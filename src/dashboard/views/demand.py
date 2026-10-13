"""REData demand analysis in peninsular calendar time."""

import streamlit as st

from components import charts
from components.cards import demand_metrics
from components.filters import period_filter
from components.layout import header, section
from components.states import dataset_ready
from components.tables import table
from services.analysis import calendar_profile, monthly_profile
from utils.formatting import WEEKDAYS, number, timestamp


def render(datasets: dict) -> None:
    header("Demanda eléctrica", "Cómo varía el consumo eléctrico de España peninsular",
           "HISTÓRICO · REDATA")
    dataset = datasets["demand"]
    if not dataset_ready(dataset):
        return
    frame = period_filter(dataset, "demand", "30 días")
    if frame.empty:
        return
    demand_metrics(frame)
    with st.container(border=True, key="panel-demand-line"):
        section("Evolución de la demanda", "Los marcadores identifican el máximo y mínimo.")
        charts.line(frame, "MW", "demand_line", extrema=True, height=290)
    with st.container(key="chart-pair-demand"):
        left, right = st.columns(2)
    with left, st.container(border=True, key="panel-demand-hour"):
        section("Perfil horario medio", "Media de cada hora del día en el periodo seleccionado.")
        profile = calendar_profile(frame, "hour")
        charts.bars([f"{hour:02d}:00" for hour in profile.index], profile["mean"],
                    profile["count"], "MW", "demand_hour")
    with right, st.container(border=True, key="panel-demand-weekday"):
        section("Demanda por día de la semana", "Media de los registros de cada día.")
        profile = calendar_profile(frame, "dayofweek")
        charts.bars([WEEKDAYS[day] for day in profile.index], profile["mean"],
                    profile["count"], "MW", "demand_weekday")
    monthly = monthly_profile(frame)
    if len(monthly) > 1:
        with st.container(border=True, key="panel-demand-month"):
            section("Demanda media mensual", "Los meses incluyen solo los registros filtrados.")
            charts.bars(list(monthly.index), monthly["mean"], monthly["count"],
                        "MW", "demand_month", monthly=True)
    section("Registros del periodo", "Los más recientes primero · hora peninsular.")
    display = frame.iloc[::-1].copy()
    display["Fecha"] = display.timestamp.map(lambda value: timestamp(value, True))
    display["Demanda · MW"] = display.value.map(lambda value: number(value, 2))
    table(display[["Fecha", "Demanda · MW"]], "demand")

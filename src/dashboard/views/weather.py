"""Only the temperature field actually present in Open-Meteo."""

import streamlit as st

from components import charts
from components.cards import weather_metrics
from components.filters import period_filter
from components.layout import header, section
from components.states import dataset_ready
from services.analysis import monthly_profile


def render(datasets: dict) -> None:
    header("Meteorología", "Temperatura de Madrid y su evolución en el tiempo",
           "HISTÓRICO · OPEN-METEO")
    dataset = datasets["weather"]
    if not dataset_ready(dataset):
        return
    frame = period_filter(dataset, "weather", "30 días")
    if frame.empty:
        return
    weather_metrics(frame)
    with st.container(border=True, key="panel-weather-line"):
        section("Evolución de la temperatura", "Temperatura del aire a 2 m · °C")
        charts.line(frame, "°C", "weather_line", charts.AMBER, extrema=True, height=290)
    with st.container(border=True, key="panel-weather-month"):
        section("Temperatura media por mes", "Solo registros del periodo seleccionado.")
        monthly = monthly_profile(frame)
        charts.bars(list(monthly.index), monthly["mean"], monthly["count"],
                    "°C", "weather_month", charts.AMBER, monthly=True)
    st.caption("Madrid representa una única localización meteorológica; estos valores "
               "no son una media de toda España peninsular.")

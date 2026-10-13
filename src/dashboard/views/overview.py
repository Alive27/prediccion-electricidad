"""Overview with actual demand and Madrid temperature observations."""

import streamlit as st

from components import charts
from components.cards import demand_metrics, metric, project_status
from components.filters import period_filter
from components.layout import header, section
from components.states import dataset_ready
from services.analysis import calendar_profile, select_period
from utils.formatting import number, timestamp


def render(datasets: dict) -> None:
    header("Predicción Eléctrica",
           "Análisis histórico de la demanda eléctrica en España peninsular")
    demand, weather = datasets["demand"], datasets["weather"]
    if demand.available:
        st.markdown('<div class="overview-meta">REData · Último registro disponible: '
                    f'{timestamp(demand.frame.timestamp.max())} · Hora peninsular</div>',
                    unsafe_allow_html=True)
    primary = demand if demand.available else weather
    selected = period_filter(primary, "overview") if primary.available else primary.frame
    if dataset_ready(demand) and not selected.empty:
        demand_metrics(selected)
        with st.container(border=True, key="panel-overview-demand"):
            section("Evolución de la demanda eléctrica", "Demanda observada · MW")
            charts.line(selected, "MW", "overview_demand", extrema=True, height=290)
    with st.container(key="chart-pair-overview"):
        left, right = st.columns([1.25, 1])
        with left, st.container(border=True, key="panel-overview-profile"):
            section("El perfil del periodo", "Demanda media por hora · MW")
            if demand.available and not selected.empty:
                profile = calendar_profile(selected, "hour")
                charts.bars([f"{hour:02d}:00" for hour in profile.index], profile["mean"],
                            profile["count"], "MW", "overview_profile", height=255)
            else:
                st.caption("El perfil estará disponible al cargar el histórico de demanda.")
    with right, st.container(border=True, key="panel-overview-weather"):
        section("Temperatura", "Madrid · temperatura del aire a 2 m · Open-Meteo")
        if dataset_ready(weather):
            if selected.empty:
                frame = weather.frame.iloc[:0]
            elif not demand.available:
                frame = selected
            elif (demand.available and demand.quality.get("timezone")
                    and weather.quality.get("timezone")):
                frame = weather.frame.loc[
                    weather.frame.timestamp.ge(selected.timestamp.min())
                    & weather.frame.timestamp.le(selected.timestamp.max())
                ]
            else:
                period = st.session_state.get("period_overview", "7 días")
                dates = st.session_state.get("dates_overview")
                frame = select_period(weather.frame, period,
                                      tuple(dates) if dates and len(dates) == 2 else None)
            if not frame.empty:
                metric("Temperatura media", number(frame.value.mean(), 1), "°C",
                       f"Mínima {number(frame.value.min(), 1)} °C · "
                       f"máxima {number(frame.value.max(), 1)} °C")
                charts.line(frame, "°C", "overview_weather", charts.AMBER, height=190)
            else:
                st.info("No hay temperatura disponible en este periodo.")
    with st.container(border=True, key="panel-overview-status"):
        section("Estado del proyecto")
        project_status(datasets, compact=True)

"""REData demand analysis in peninsular calendar time."""

import streamlit as st
import requests
import pandas as pd

from components import charts
from components.cards import demand_metrics
from components.filters import period_filter
from components.layout import header, section
from components.states import dataset_ready
from components.tables import table
from services.analysis import calendar_profile, monthly_profile
from services import api_client
from utils.formatting import WEEKDAYS, number, timestamp


def render(datasets: dict) -> None:
    header("Demanda · histórico 2024",
           "Curvas oficiales recientes y archivo histórico peninsular de 2024",
           "HISTÓRICO · REDATA")
    with st.container(border=True, key="panel-live-demand"):
        section("Curvas oficiales recientes · ESIOS",
                "Demanda real y previsión oficial peninsular. "
                "Puntos originales, sin interpolación.")
        try:
            curves = api_client.cached_curves(168)
            st.caption(
                f"Consulta del estado: {curves.get('retrieved_at_utc', 'sin fecha')} · "
                "intervalo solicitado: últimos 7 días; la fecha de descarga de cada fuente "
                "aparece por separado.")
            for key, label in (("real", "Demanda real — REE"),
                               ("official_forecast", "Demanda prevista — REE")):
                item = curves.get("curves", {}).get(key, {})
                last = item.get("records", [])[-1:]
                last_label = last[0].get("timestamp_utc") if last else "sin observaciones"
                resolution = item.get("resolution_minutes")
                cadence = (
                    f"{resolution:g} min observados"
                    if resolution else "resolución no disponible")
                status_label = {"connected": "conectada", "not_configured": "sin credencial",
                                "unavailable": "error; caché si existe"}.get(
                                    item.get("status"), item.get("status", "sin estado"))
                st.caption(
                    f"{label} · indicador {item.get('indicator_id')} · {status_label} · "
                    f"origen {item.get('data_origin', 'no disponible')} · "
                    f"último punto {last_label} · {cadence} · "
                    f"última descarga {item.get('last_success_at_utc') or 'nunca'}")
                if item.get("error"):
                    st.warning(f"{label}: {item['error']}")
            charts.official_demand_curves(curves)
            if (curves.get("curves", {}).get("real", {}).get("records")
                    and curves.get("curves", {}).get("real", {}).get("status") == "connected"):
                comparison = api_client.cached_comparison(48)
                pairs = comparison.get("records", [])
                section("Diagnóstico REData / ESIOS",
                        "Solo se comparan observaciones en el mismo instante UTC; "
                        "no se aproxima la hora.")
                if pairs:
                    frame_comparison = pd.DataFrame(pairs)
                    frame_comparison["Hora peninsular"] = pd.to_datetime(
                        frame_comparison.timestamp_utc, utc=True).dt.tz_convert(
                            "Europe/Madrid").map(lambda value: value.strftime("%d/%m/%Y %H:%M %Z"))
                    st.dataframe(
                        frame_comparison[[
                            "Hora peninsular", "redata_mw", "esios_mw", "difference_mw"
                        ]].rename(columns={
                            "redata_mw": "REData · MW",
                            "esios_mw": "ESIOS real · MW",
                            "difference_mw": "Diferencia · MW",
                        }),
                        width="stretch",
                        hide_index=True,
                    )
                else:
                    st.info(
                        "Aún no hay marcas temporales coincidentes exactas "
                        "entre las dos fuentes.")
        except (requests.RequestException, ValueError) as error:
            st.warning(
                "No se pudo consultar la API para las curvas oficiales "
                f"({type(error).__name__}).")
    dataset = datasets["demand"]
    if not dataset_ready(dataset):
        return
    st.info(f"Histórico local de {dataset.frame.timestamp.min():%d/%m/%Y} a "
            f"{dataset.frame.timestamp.max():%d/%m/%Y}. Estos registros no son la demanda actual.")
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

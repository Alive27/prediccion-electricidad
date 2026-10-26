"""Public model page: 48-hour demand forecast and observation comparisons."""

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from components import charts
from components.forecast import (
    LOCAL_ZONE, is_current, prediction_frame, render_summary, render_timeline,
)
from components.layout import header, section
from services import api_client


def _error_detail(error: requests.HTTPError) -> str:
    try:
        response = error.response.json()
        return str(response.get("detail", response))[:400]
    except (ValueError, AttributeError):
        return str(error)[:400]


def _hourly_profile(frame: pd.DataFrame, simulation: bool = False) -> None:
    profile = frame.assign(hour=frame.timestamp_local.dt.hour).groupby("hour").demand_mw.mean()
    figure = charts.base_figure("Demanda prevista · MW", 280)
    figure.add_trace(go.Bar(
        x=[f"{hour:02d}:00" for hour in profile.index], y=profile.values,
        customdata=profile.index, marker_color=charts.AMBER,
        name="Demanda simulada" if simulation else "Demanda prevista",
        hovertemplate="Hora local %{customdata:02.0f}:00<br>"
                      "Demanda prevista %{y:,.0f} MW<extra></extra>",
    ))
    figure.update_xaxes(title="Hora peninsular · Europe/Madrid", type="category")
    charts.show(figure, "model_hourly_profile")


def _peak_tables(frame: pd.DataFrame, simulation: bool = False) -> None:
    kind = "simulación" if simulation else "previsión"
    section("Horas punta y extremos diarios",
            f"Ranking calculado desde las {len(frame)} salidas de la {kind}.")
    peaks = frame.nlargest(5, "demand_mw").copy()
    peaks["Hora local"] = peaks.timestamp_local.map(
        lambda value: value.strftime("%a %d/%m %H:%M %Z"))
    value_label = f"Demanda {kind} · MW"
    peaks = peaks.rename(columns={"demand_mw": value_label})
    daily = []
    for day, group in frame.groupby(frame.timestamp_local.dt.date, sort=True):
        maximum = group.loc[group.demand_mw.idxmax()]
        minimum = group.loc[group.demand_mw.idxmin()]
        daily.append({
            "Día local": day.isoformat(),
            "Máximo · MW": maximum.demand_mw,
            "Hora máxima": maximum.timestamp_local.strftime("%H:%M %Z"),
            "Mínimo · MW": minimum.demand_mw,
            "Hora mínima": minimum.timestamp_local.strftime("%H:%M %Z"),
        })
    left, right = st.columns([1, 1.4])
    with left:
        st.markdown(f"**Cinco horas de mayor demanda en la {kind}**")
        st.dataframe(peaks[["Hora local", value_label]], width="stretch", hide_index=True)
    with right:
        st.markdown("**Máximo y mínimo previstos por día**")
        st.dataframe(pd.DataFrame(daily), width="stretch", hide_index=True)


def _previous_day_comparison(frame: pd.DataFrame, simulation: bool = False) -> None:
    section("Comparación con el día anterior", "Emparejamiento por fecha y hora local peninsular.")
    forecast_days = set(frame.timestamp_local.dt.date)
    reference_days = {day - timedelta(days=1) for day in forecast_days}
    first_day = min(reference_days)
    last_day = max(reference_days)
    start_local = pd.Timestamp(first_day).tz_localize(LOCAL_ZONE)
    end_local = pd.Timestamp(last_day + timedelta(days=1)).tz_localize(LOCAL_ZONE)
    try:
        result = api_client.cached_history_between(
            start_local.tz_convert("UTC").isoformat(),
            end_local.tz_convert("UTC").isoformat(),
        )
    except requests.HTTPError as error:
        st.info(f"No se pudo consultar el histórico comparable: {_error_detail(error)}")
        return
    except requests.RequestException as error:
        st.info(f"No se pudo consultar REData ({type(error).__name__}).")
        return
    observations = pd.DataFrame(result.get("records", []))
    if observations.empty or not {"timestamp_utc", "demand_mw"}.issubset(observations):
        st.info("No hay demanda observada del día anterior para este horizonte.")
        return
    observations["timestamp_utc"] = pd.to_datetime(observations.timestamp_utc, utc=True,
                                                   errors="coerce")
    observations["demand_mw"] = pd.to_numeric(observations.demand_mw, errors="coerce")
    observations = observations.dropna(subset=["timestamp_utc", "demand_mw"])
    observations["timestamp_local"] = observations.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
    observations["day"] = observations.timestamp_local.dt.date
    observations["hour"] = observations.timestamp_local.dt.hour
    duplicated = observations.duplicated(["day", "hour"], keep=False)
    observations = observations.loc[~duplicated]
    by_local_hour = dict(zip(zip(observations.day, observations.hour),
                             observations.demand_mw))

    forecast = frame.copy()
    forecast["day"] = forecast.timestamp_local.dt.date
    forecast["hour"] = forecast.timestamp_local.dt.hour
    repeated_forecast_hour = forecast.duplicated(["day", "hour"], keep=False)
    forecast["Día anterior observado · MW"] = [
        None if repeated else by_local_hour.get((day - timedelta(days=1), hour))
        for day, hour, repeated in zip(forecast.day, forecast.hour, repeated_forecast_hour)
    ]
    forecast["Diferencia · MW"] = (
        forecast.demand_mw - forecast["Día anterior observado · MW"])
    forecast["Hora local"] = forecast.timestamp_local.map(
        lambda value: value.strftime("%d/%m %H:%M %Z"))
    value_name = "Simulación · MW" if simulation else "Previsión · MW"
    comparison = forecast.rename(columns={"demand_mw": value_name})
    comparison = comparison[["Hora local", value_name,
                             "Día anterior observado · MW", "Diferencia · MW"]]
    if comparison["Día anterior observado · MW"].notna().any():
        st.dataframe(comparison, width="stretch", hide_index=True)
        st.caption("Los pares sin observación se dejan vacíos. Las horas repetidas o inexistentes "
                   "por el cambio horario se excluyen para evitar comparaciones ambiguas.")
    else:
        st.info("REData no dispone de horas equivalentes del día anterior.")


def _hourly_table(frame: pd.DataFrame, simulation: bool = False) -> None:
    title = "Simulación histórica por hora" if simulation else "Previsión horaria"
    section(title, "Cada fila procede de la respuesta de FastAPI; unidad MW.")
    table = frame[["timestamp_local", "demand_mw"]].copy()
    table["Hora peninsular"] = table.timestamp_local.map(
        lambda value: value.strftime("%A %d/%m/%Y %H:%M %Z"))
    value_label = "Demanda simulada · MW" if simulation else "Demanda prevista · MW"
    table = table.rename(columns={"demand_mw": value_label})
    st.dataframe(table[["Hora peninsular", value_label]],
                 width="stretch", hide_index=True)


def render(datasets: dict) -> None:
    header("Modelo", "Previsión horaria con el modelo existente · 5, 12, 24 o 48 horas")
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se puede consultar la API ({type(error).__name__}).")
        return
    try:
        model = api_client.cached_model_info()
    except (requests.RequestException, ValueError):
        model = {}

    st.caption(f"Modelo cargado: {model.get('model_type', 'no disponible')} · "
               f"artefacto {model.get('artifact_version', 'no identificado')}.")
    if status.get("source_notice"):
        st.warning(status["source_notice"])
    stale = not status.get("demand_fresh")
    last_value = status.get("last_demand_mw")
    last_label = f"{last_value:,.0f} MW" if last_value is not None else "sin valor"
    st.caption(f"Última observación real: {status.get('last_demand_utc') or 'sin datos'} · "
               f"{last_label} · antigüedad {status.get('last_demand_age_hours', 'sin dato')} h")
    if stale:
        st.warning(status.get("reason") or "El dato real está fuera del límite de frescura.")
        st.info("Puedes pedir una simulación desde ese instante si hay suficientes datos "
                "y temperaturas históricas. No representa las próximas 48 horas actuales.")
    elif not status.get("future_forecast_available"):
        st.warning(status.get("reason") or "Faltan entradas para una previsión de 48 horas.")

    horizon = st.select_slider("Horizonte de predicción", options=[5, 12, 24, 48],
                               value=48, format_func=lambda value: f"{value} horas",
                               key="prediction_horizon")
    st.caption(f"Se solicitarán {horizon} predicciones horarias al modelo; resolución de salida: 1 hora.")

    forecast = st.session_state.get("demand_forecast")
    latest_observation = status.get("last_demand_utc")
    if forecast and not is_current(forecast, latest_observation):
        st.session_state.pop("demand_forecast", None)
        forecast = None
    confirm_simulation = False
    if stale and status.get("historical_simulation_available"):
        confirm_simulation = st.checkbox(
            "Confirmo que quiero una simulación histórica, no una previsión actual.",
            key="confirm_historical_simulation")
    can_generate = bool(status.get("model_loaded") and (
        status.get("historical_simulation_available") if stale
        else status.get("future_forecast_available")))
    if st.button(f"Generar simulación histórica {horizon} h" if stale
                 else f"Generar previsión {horizon} h",
                 type="primary", icon=":material/bolt:",
                 disabled=not can_generate or (stale and not confirm_simulation),
                 key="prediction_generate"):
        try:
            with st.spinner(f"Calculando {horizon} horas…"):
                forecast = api_client.post("/predict", {
                    "horizon_hours": horizon,
                    "allow_stale_simulation": bool(stale and confirm_simulation),
                })
            st.session_state["demand_forecast"] = forecast
        except requests.HTTPError as error:
            st.error(f"La API no aceptó la solicitud: {_error_detail(error)}")
        except requests.RequestException as error:
            st.error(f"No se pudo solicitar la previsión ({type(error).__name__}).")

    forecast = st.session_state.get("demand_forecast")
    if (not forecast or not is_current(forecast, latest_observation)
            or forecast.get("horizon_hours") != horizon):
        if stale and not status.get("historical_simulation_available"):
            st.info(status.get("reason") or "No hay suficientes entradas históricas para simular.")
        elif not stale and not status.get("future_forecast_available"):
            st.info(status.get("reason") or "Faltan entradas para la previsión actual.")
        else:
            st.info(f"Genera una previsión de {horizon} horas basada en los datos disponibles.")
        return
    frame = prediction_frame(forecast)
    if len(frame) != horizon:
        st.error(f"La API no devolvió {horizon} horas completas. No se presentan resúmenes parciales.")
        return

    simulation = forecast.get("prediction_mode") == "historical_simulation"
    section("Resumen de simulación" if simulation else "Resumen ejecutivo",
            "Resultados históricos recursivos; no son una previsión actual."
            if simulation else "Máximo, mínimo y rango calculados desde la salida del modelo.")
    render_summary(forecast, frame)
    history_records = []
    try:
        history_records = api_client.cached_history(168).get("records", [])
    except requests.RequestException as error:
        st.info(f"Demanda real reciente no disponible ({type(error).__name__}).")
    if not simulation:
        try:
            official = api_client.cached_curves(168)
        except requests.RequestException as error:
            official = {"curves": {}}
            st.info(f"Curvas oficiales ESIOS no disponibles ({type(error).__name__}).")
        charts.official_demand_curves(
            official, "model_forecast_timeline", frame, history_records)
    else:
        render_timeline(history_records, forecast, frame, "model_forecast_timeline")
    _peak_tables(frame, simulation)
    _hourly_profile(frame, simulation)
    _previous_day_comparison(frame, simulation)
    _hourly_table(frame, simulation)

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
from components.data_table import Column, mw, signed_mw, styled_table
from components.layout import header, section
from components.run_panel import generation_notice, pending, run_panel, status_chips
from services import api_client
from utils.formatting import WEEKDAYS, number, timestamp


def _error_detail(error: requests.HTTPError) -> str:
    try:
        response = error.response.json()
        return str(response.get("detail", response))[:400]
    except (ValueError, AttributeError):
        return str(error)[:400]


def _status_chips(model: dict, status: dict, stale: bool) -> None:
    """Loaded model, last real observation and its freshness."""
    value = status.get("last_demand_mw")
    age = status.get("last_demand_age_hours")
    limit = status.get("freshness_limit_hours")
    observed = status.get("last_demand_utc")
    observed = (timestamp(pd.Timestamp(observed).tz_convert(LOCAL_ZONE), with_zone=True)
                if observed else "sin datos")
    status_chips([
        ("success" if status.get("model_loaded") else "error", "Modelo",
         model.get("model_type", "no disponible")),
        ("", "Artefacto", model.get("artifact_version", "no identificado")),
        ("", "Última observación",
         f"{number(value)} MW · {observed}" if value is not None else observed),
        ("warning" if stale else "success", "Antigüedad",
         (f"{number(age, 1)} h" if age is not None else "sin dato")
         + (f" · límite {number(limit)} h" if limit is not None else "")),
    ])


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


def _day(stamp: pd.Timestamp, year: bool = False) -> str:
    """Short Spanish weekday and date, e.g. "Jue 12/11"."""
    return f"{WEEKDAYS[stamp.weekday()][:3]} {stamp.strftime('%d/%m/%Y' if year else '%d/%m')}"


def _peak_tables(frame: pd.DataFrame, simulation: bool = False) -> None:
    kind = "simulación" if simulation else "previsión"
    section("Horas punta y extremos diarios",
            f"Ranking calculado desde las {len(frame)} salidas de la {kind}.")
    peaks = frame.nlargest(5, "demand_mw")
    daily = []
    for _, group in frame.groupby(frame.timestamp_local.dt.date, sort=True):
        maximum = group.loc[group.demand_mw.idxmax()]
        minimum = group.loc[group.demand_mw.idxmin()]
        daily.append([_day(maximum.timestamp_local, year=True),
                      mw(maximum.demand_mw), maximum.timestamp_local.strftime("%H:%M %Z"),
                      mw(minimum.demand_mw), minimum.timestamp_local.strftime("%H:%M %Z")])
    left, right = st.columns([1, 1.5])
    with left:
        styled_table(
            [Column("Nº", "18%"), Column("Hora local", "50%"),
             Column("MW", "32%", numeric=True)],
            [[f"{rank}", f"{_day(row.timestamp_local)} · {row.timestamp_local:%H:%M %Z}",
              mw(row.demand_mw)] for rank, row in enumerate(peaks.itertuples(), start=1)],
            title=f"Cinco horas de mayor demanda en la {kind}", max_height=None)
    with right:
        styled_table(
            [Column("Día", "28%"), Column("Máx. MW", "18%", numeric=True),
             Column("Hora", "18%", numeric=True), Column("Mín. MW", "18%", numeric=True),
             Column("Hora", "18%", numeric=True)],
            daily, title="Máximo y mínimo previstos por día", max_height=None)


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
    value_name = "Simulación · MW" if simulation else "Previsión · MW"
    if forecast["Día anterior observado · MW"].notna().any():
        styled_table(
            [Column("Hora local", "28%"), Column(value_name, "24%", numeric=True),
             Column("Día anterior · MW", "24%", numeric=True),
             Column("Diferencia · MW", "24%", numeric=True)],
            [[f"{_day(row.timestamp_local)} · {row.timestamp_local:%H:%M %Z}",
              mw(row.demand_mw), mw(previous), signed_mw(difference)]
             for row, previous, difference in zip(
                 forecast.itertuples(), forecast["Día anterior observado · MW"],
                 forecast["Diferencia · MW"])],
            note="Los pares sin observación se dejan vacíos. Las horas repetidas o inexistentes "
                 "por el cambio horario se excluyen para evitar comparaciones ambiguas.",
            label="Comparación con el día anterior")
    else:
        st.info("REData no dispone de horas equivalentes del día anterior.")


def _hourly_table(frame: pd.DataFrame, simulation: bool = False) -> None:
    title = "Simulación histórica por hora" if simulation else "Previsión horaria"
    section(title, "Salida horaria del modelo de demanda · MW · hora peninsular.")
    value_label = "Demanda simulada · MW" if simulation else "Demanda prevista · MW"
    styled_table(
        [Column("Día", "34%"), Column("Hora", "30%"), Column(value_label, "36%", numeric=True)],
        [[_day(row.timestamp_local, year=True), row.timestamp_local.strftime("%H:%M %Z"),
          mw(row.demand_mw)] for row in frame.itertuples()],
        label=title)


def _tracking_section(days: int = 7) -> None:
    """Logged current forecasts against the real demand published afterwards."""
    section("Seguimiento del modelo",
            f"Previsiones actuales guardadas en la base de datos frente a la demanda real "
            f"publicada después por REData · últimos {days} días. Las simulaciones históricas "
            "no se registran.")
    try:
        tracking = api_client.cached_tracking(days)
    except (requests.RequestException, ValueError) as error:
        st.info(f"Registro de previsiones no disponible ({type(error).__name__}).")
        return
    if not tracking.get("configured"):
        st.info("Registro de previsiones no configurado: define DATABASE_URL en el .env para "
                "guardar las previsiones en la base de datos.")
        return
    if not tracking.get("available"):
        st.warning("No se pudo leer el registro de previsiones de la base de datos "
                   f"({tracking.get('error') or 'sin detalle'}).")
        return
    metrics = tracking.get("metrics") or {}
    records = tracking.get("records", [])
    mae, mape = metrics.get("mae_mw"), metrics.get("mape_pct")
    status_chips([
        ("success", "Base de datos", "conectada"),
        ("", "Horas registradas", number(tracking.get("stored", 0))),
        ("", "Horas con dato real", number(metrics.get("compared_hours", 0))),
        ("", "Error medio (MAE)", f"{number(mae)} MW" if mae is not None else "sin datos"),
        ("", "Error relativo (MAPE)", f"{number(mape, 1)} %" if mape is not None else "sin datos"),
    ])
    if not records:
        st.info(f"No hay previsiones registradas en los últimos {days} días. Se guardan al "
                "generar una previsión actual con datos de REData recientes.")
        return
    frame = pd.DataFrame(records)
    frame["local"] = pd.to_datetime(frame.timestamp_utc, utc=True).dt.tz_convert(
        LOCAL_ZONE).dt.tz_localize(None)
    figure = charts.base_figure("Demanda · MW", 300)
    for column, name, color, dash in (("actual_mw", "Demanda real · REData", charts.BLUE, "solid"),
                                      ("predicted_mw", "Previsión registrada", charts.AMBER,
                                       "dash")):
        figure.add_trace(go.Scatter(
            x=frame.local, y=frame[column], name=name, mode="lines+markers",
            line={"color": color, "width": 2.2, "dash": dash}, marker={"size": 5},
            hovertemplate=f"%{{x|%d/%m %H:%M}}<br>{name} · %{{y:,.0f}} MW<extra></extra>"))
    figure.update_layout(showlegend=True, hovermode="x unified")
    figure.update_xaxes(title="Hora peninsular")
    charts.show(figure, "model_tracking")
    if not metrics:
        st.caption("La demanda real de estas horas aún no se ha publicado; el error se "
                   "calculará cuando REData la publique.")


def render(datasets: dict) -> None:
    header("Predicción de demanda",
           "Previsión horaria de la demanda eléctrica peninsular · 6, 12, 24 o 48 horas",
           eyebrow="MODELO DE DEMANDA")
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se puede consultar la API ({type(error).__name__}).")
        return
    try:
        model = api_client.cached_model_info()
    except (requests.RequestException, ValueError):
        model = {}

    stale = not status.get("demand_fresh")
    _status_chips(model, status, stale)
    if status.get("source_notice"):
        st.warning(status["source_notice"])
    if stale:
        st.warning(status.get("reason") or "El dato real está fuera del límite de frescura.")
    elif not status.get("future_forecast_available"):
        st.warning(status.get("reason") or "Faltan entradas para una previsión de 48 horas.")

    _forecast_area(status, stale)
    _tracking_section()


def _forecast_area(status: dict, stale: bool) -> None:
    """Run panel and the results of the last generation of this session."""
    latest_observation = status.get("last_demand_utc")
    can_generate = bool(status.get("model_loaded") and (
        status.get("historical_simulation_available") if stale
        else status.get("future_forecast_available")))
    horizon, generate, confirm_simulation = run_panel(
        "prediction",
        "Simulación histórica recursiva desde la última observación real; no representa "
        "las próximas horas." if stale else "Previsión horaria desde la última observación real.",
        "Generar simulación histórica" if stale else "Generar previsión",
        enabled=can_generate,
        confirm=("Confirmo que quiero una simulación histórica, no una previsión actual."
                 if stale and status.get("historical_simulation_available") else None),
    )
    if generate:
        try:
            with st.spinner(f"Calculando {horizon} horas…"):
                forecast = api_client.post("/predict", {
                    "horizon_hours": horizon,
                    "allow_stale_simulation": bool(stale and confirm_simulation),
                })
            # Own key: the summary page keeps its 48 h city profile separately.
            st.session_state["model_demand_forecast"] = forecast
        except requests.HTTPError as error:
            st.error(f"La API no aceptó la solicitud: {_error_detail(error)}")
        except requests.RequestException as error:
            st.error(f"No se pudo solicitar la previsión ({type(error).__name__}).")

    # The last generation persists for the whole browser session.
    forecast = st.session_state.get("model_demand_forecast")
    if not forecast:
        if stale and not status.get("historical_simulation_available"):
            st.info(status.get("reason") or "No hay suficientes entradas históricas para simular.")
        elif not stale and not status.get("future_forecast_available"):
            st.info(status.get("reason") or "Faltan entradas para la previsión actual.")
        else:
            pending(horizon, "la simulación histórica" if stale else "la demanda")
        return
    frame = prediction_frame(forecast)
    shown = int(forecast.get("horizon_hours") or len(frame))
    generation_notice(shown, horizon, forecast.get("generated_at_utc"),
                      outdated=not is_current(forecast, latest_observation))
    if len(frame) != shown:
        st.error(
            f"La API no devolvió {shown} horas completas. "
            "No se presentan resúmenes parciales.")
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

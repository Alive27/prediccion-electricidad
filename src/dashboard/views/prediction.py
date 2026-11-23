"""Modelo: hourly demand forecast of the trained model, its context and its tracking."""

from datetime import timedelta

import pandas as pd
import requests
import streamlit as st

from components import charts
from components.blocks import (card, card_header, chip, chip_row, grid, html, legend, metric,
                               mini, rank_bars, subheading)
from components.data_table import Column, mw, signed_mw, styled_table, table_html
from components.forecast import LOCAL_ZONE, is_current, prediction_frame, render_timeline
from components.layout import header
from components.run_panel import generation_notice, pending, run_bar
from services import api_client
from utils.formatting import local_datetime, number, short_date

DATABASE_HINT = ("Registro de previsiones no configurado: define DATABASE_URL en el .env para "
                 "guardar las previsiones en la base de datos.")


def _error_detail(error: requests.HTTPError) -> str:
    try:
        response = error.response.json()
        return str(response.get("detail", response))[:400]
    except (ValueError, AttributeError):
        return str(error)[:400]


def _when(stamp: pd.Timestamp) -> str:
    return f"{short_date(stamp)} · {stamp:%H:%M}"


def _chips(status: dict, model: dict, forecast: dict | None) -> None:
    source_ok = status.get("demand_source_status") == "connected"
    model_type = model.get("model_type", "no disponible").replace("Regressor", "")
    if status.get("model_ready"):
        model_chip = chip(f"Modelo listo · {model_type}")
    elif status.get("model_loaded"):
        model_chip = chip(f"Modelo cargado · {model_type}", "warning")
    else:
        model_chip = chip("Modelo no disponible", "error")
    weather_ok = status.get("forecast_weather_available")
    note = ""
    if forecast:
        simulation = forecast.get("prediction_mode") == "historical_simulation"
        mode = ("horas de simulación histórica" if simulation
                else "horas desde la última observación real")
        note = (f"Generada el {local_datetime(forecast['generated_at_utc'])} · "
                f"{forecast.get('horizon_hours')} {mode}")
    html(chip_row([
        chip("REData conectada" if source_ok else "REData sin conexión",
             "success" if source_ok else "error"),
        model_chip,
        chip("Open-Meteo · 48 h disponibles" if weather_ok else "Open-Meteo · sin previsión",
             "success" if weather_ok else "warning"),
    ], note))


def _generate(horizon: int, stale: bool, confirmed: bool) -> None:
    try:
        with st.spinner(f"Calculando {horizon} horas…"):
            forecast = api_client.post("/predict", {
                "horizon_hours": horizon, "allow_stale_simulation": bool(stale and confirmed)})
        # Own key: the summary page keeps its 48 h city profile separately.
        st.session_state["model_demand_forecast"] = forecast
        api_client.cached_tracking.clear()   # the API has just logged this forecast
    except requests.HTTPError as error:
        st.error(f"La API no aceptó la solicitud: {_error_detail(error)}")
    except requests.RequestException as error:
        st.error(f"No se pudo solicitar la previsión ({type(error).__name__}).")


def _previous_day(frame: pd.DataFrame) -> tuple[pd.Series | None, str]:
    """Observed demand at the same local hour of the previous day, aligned with `frame`.

    Repeated or missing local hours (DST changes) are left empty instead of guessed.
    """
    reference_days = {day - timedelta(days=1) for day in frame.timestamp_local.dt.date}
    start = pd.Timestamp(min(reference_days)).tz_localize(LOCAL_ZONE)
    end = pd.Timestamp(max(reference_days) + timedelta(days=1)).tz_localize(LOCAL_ZONE)
    try:
        result = api_client.cached_history_between(start.tz_convert("UTC").isoformat(),
                                                   end.tz_convert("UTC").isoformat())
    except requests.HTTPError as error:
        return None, f"No se pudo consultar el histórico comparable: {_error_detail(error)}"
    except requests.RequestException as error:
        return None, f"No se pudo consultar REData ({type(error).__name__})."
    observed = pd.DataFrame(result.get("records", []))
    if observed.empty or not {"timestamp_utc", "demand_mw"}.issubset(observed):
        return None, "No hay demanda observada del día anterior para este horizonte."
    observed["timestamp_utc"] = pd.to_datetime(observed.timestamp_utc, utc=True, errors="coerce")
    observed["demand_mw"] = pd.to_numeric(observed.demand_mw, errors="coerce")
    observed = observed.dropna(subset=["timestamp_utc", "demand_mw"])
    local = observed.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
    observed = observed.assign(day=local.dt.date, hour=local.dt.hour)
    observed = observed.loc[~observed.duplicated(["day", "hour"], keep=False)]
    by_hour = dict(zip(zip(observed.day, observed.hour), observed.demand_mw))
    days, hours = frame.timestamp_local.dt.date, frame.timestamp_local.dt.hour
    repeated = pd.DataFrame({"d": days, "h": hours}).duplicated(keep=False)
    previous = pd.Series([None if twice else by_hour.get((day - timedelta(days=1), hour))
                          for day, hour, twice in zip(days, hours, repeated)],
                         index=frame.index, dtype="float64")
    if previous.isna().all():
        return None, "REData no dispone de horas equivalentes del día anterior."
    return previous, ""


def _summary(frame: pd.DataFrame) -> None:
    local, values = frame.timestamp_local, frame.demand_mw
    peak, valley = values.idxmax(), values.idxmin()
    html(grid([
        metric("Siguiente hora", number(values.iloc[0]), "MW", _when(local.iloc[0])),
        metric("Media prevista", number(round(values.mean(), -1)), "MW", f"{len(frame)} horas"),
        metric("Máximo previsto", number(values.loc[peak]), "MW", _when(local.loc[peak])),
        metric("Mínimo previsto", number(values.loc[valley]), "MW", _when(local.loc[valley])),
    ], 4))


def _extremes_card(frame: pd.DataFrame, kind: str) -> str:
    daily = []
    for _, group in frame.groupby(frame.timestamp_local.dt.date, sort=True):
        high = group.loc[group.demand_mw.idxmax()]
        low = group.loc[group.demand_mw.idxmin()]
        daily.append([short_date(high.timestamp_local), mw(high.demand_mw),
                      f"{high.timestamp_local:%H:%M}", mw(low.demand_mw),
                      f"{low.timestamp_local:%H:%M}"])
    top = frame.nlargest(5, "demand_mw")
    low, high = frame.demand_mw.min(), frame.demand_mw.max()
    span = (high - low) or 1
    bars = [(f"{row.timestamp_local:%H:%M}", (row.demand_mw - low) / span,
             f"{number(row.demand_mw)} MW") for row in top.itertuples()]
    return card(
        card_header("Horas punta y extremos diarios",
                    "Máximo, mínimo y rango calculados desde la salida del modelo."),
        table_html([Column("Fecha", "30%"), Column("Máx. MW", "19%", True),
                    Column("Hora", "14%", True), Column("Mín. MW", "19%", True),
                    Column("Hora", "18%", True)], daily, label="Extremos diarios"),
        subheading(f"Cinco horas de mayor demanda en la {kind}"), rank_bars(bars))


def _comparison_card(frame: pd.DataFrame, previous: pd.Series | None, message: str,
                     value_label: str) -> str:
    head = card_header("Comparación con el día anterior",
                       "Emparejamiento por fecha y hora local peninsular.")
    if previous is None:
        return card(head, f'<p class="v-empty">{message}</p>')
    several_days = frame.timestamp_local.dt.date.nunique() > 1
    rows = [[f"{short_date(stamp)[:3]} {stamp:%H:%M}" if several_days else f"{stamp:%H:%M}",
             mw(value), mw(before), signed_mw(value - before if before == before else None)]
            for stamp, value, before in zip(frame.timestamp_local, frame.demand_mw, previous)]
    return card(head, table_html(
        [Column("Hora", "22%"), Column(value_label, "26%", True),
         Column("Día anterior · MW", "26%", True), Column("Diferencia · MW", "26%", True)],
        rows, max_height=372, label="Comparación con el día anterior"))


def _tracking(days: int = 7) -> None:
    with st.container(key="plot-tracking"):
        html(card_header("Seguimiento del modelo",
                         "Previsiones actuales guardadas en la base de datos frente a la "
                         "demanda real publicada después."))
        try:
            tracking = api_client.cached_tracking(days)
        except (requests.RequestException, ValueError) as error:
            st.info(f"Registro de previsiones no disponible ({type(error).__name__}).")
            return
        if not tracking.get("configured"):
            st.info(DATABASE_HINT)
            return
        if not tracking.get("available"):
            st.warning("No se pudo leer el registro de previsiones de la base de datos "
                       f"({tracking.get('error') or 'sin detalle'}).")
            return
        metrics = tracking.get("metrics") or {}
        mae, mape = metrics.get("mae_mw"), metrics.get("mape_pct")
        html(grid([
            mini("Error medio (MAE)", f"{number(mae)} MW" if mae is not None else "Sin datos"),
            mini("Error relativo (MAPE)",
                 f"{number(mape, 1)} %" if mape is not None else "Sin datos"),
            mini("Horas con dato real", number(metrics.get("compared_hours", 0))),
            mini("Previsiones registradas", number(tracking.get("stored", 0)),
                 f"últimos {days} días"),
        ], 4, "v-minis"))
        records = tracking.get("records", [])
        if not records:
            st.info(f"No hay previsiones registradas en los últimos {days} días. Se guardan al "
                    "generar una previsión actual con datos de REData recientes.")
            return
        frame = pd.DataFrame(records)
        frame["timestamp_local"] = pd.to_datetime(frame.timestamp_utc, utc=True).dt.tz_convert(
            LOCAL_ZONE)
        frame[["actual_mw", "predicted_mw"]] = frame[["actual_mw", "predicted_mw"]].apply(
            pd.to_numeric, errors="coerce")
        html(legend(charts.tracking_keys()))
        charts.plot(charts.tracking_figure(frame), "model_tracking")
        if not metrics:
            st.caption("La demanda real de estas horas aún no se ha publicado; el error se "
                       "calculará cuando REData la publique.")


def _details(forecast: dict, frame: pd.DataFrame, simulation: bool) -> None:
    """Everything the summary above does not show: run details and wider context."""
    origin = pd.Timestamp(forecast["forecast_origin_utc"]).tz_convert(LOCAL_ZONE)
    span = frame.demand_mw.max() - frame.demand_mw.min()
    html(card(card_header("Detalle de la ejecución",
                          "Rango de la salida, origen de los datos reales y modelo utilizado."),
              grid([mini("Rango " + ("simulado" if simulation else "previsto"),
                         f"{number(span)} MW", "máximo menos mínimo"),
                    mini("Referencia de simulación" if simulation else "Origen real",
                         _when(origin), "Europe/Madrid"),
                    mini("Horizonte", f"{len(frame)} h", "resolución 1 hora"),
                    mini("Modelo", str(forecast.get("model", "—")).replace("Regressor", ""),
                         "artefacto cargado en la API")],
                   4, "v-minis")))
    history = []
    try:
        history = api_client.cached_history(168).get("records", [])
    except requests.RequestException as error:
        st.info(f"Demanda real reciente no disponible ({type(error).__name__}).")
    if simulation:
        render_timeline(history, forecast, frame, "model_forecast_timeline")
    else:
        try:
            official = api_client.cached_curves(168)
        except requests.RequestException as error:
            official = {"curves": {}}
            st.info(f"Curvas oficiales ESIOS no disponibles ({type(error).__name__}).")
        charts.official_demand_curves(official, "model_forecast_timeline", frame, history)
    charts.hourly_profile(frame, "model_hourly_profile", simulation)
    value_label = "Demanda simulada · MW" if simulation else "Demanda prevista · MW"
    styled_table([Column("Día", "34%"), Column("Hora", "30%"),
                  Column(value_label, "36%", numeric=True)],
                 [[short_date(row.timestamp_local), f"{row.timestamp_local:%H:%M %Z}",
                   mw(row.demand_mw)] for row in frame.itertuples()],
                 title="Simulación histórica por hora" if simulation else "Previsión por hora",
                 subtitle="Salida horaria del modelo de demanda · MW · hora peninsular.",
                 label="Previsión por hora")


def _forecast(forecast: dict, status: dict, horizon: int) -> None:
    frame = prediction_frame(forecast)
    shown = int(forecast.get("horizon_hours") or len(frame))
    generation_notice(shown, horizon, forecast.get("generated_at_utc"),
                      outdated=not is_current(forecast, status.get("last_demand_utc")))
    if len(frame) != shown:
        st.error(f"La API no devolvió {shown} horas completas. "
                 "No se presentan resúmenes parciales.")
        return None
    simulation = forecast.get("prediction_mode") == "historical_simulation"
    if simulation:
        reference = local_datetime(forecast["simulation_reference_utc"])
        st.warning(f"SIMULACIÓN HISTÓRICA: no representa las próximas {shown} horas actuales. "
                   f"Referencia: {reference}; temperatura: "
                   f"{forecast.get('weather_input', 'histórica')}.")
    _summary(frame)
    previous, message = _previous_day(frame)
    charts.forecast_chart(frame, previous, "model_forecast", simulation)
    left, right = st.columns(2)
    with left:
        html(_extremes_card(frame, "simulación" if simulation else "previsión"))
    with right:
        html(_comparison_card(frame, previous, message,
                              "Simulación · MW" if simulation else "Previsión · MW"))
    return frame, simulation


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
    simulation_allowed = bool(stale and status.get("historical_simulation_available"))
    page = st.container()   # the execution bar below is rendered last, over the window edge
    horizon, generate, confirmed = run_bar(
        "prediction", "Generar simulación" if stale else "Generar previsión",
        enabled=bool(status.get("model_loaded") and (
            simulation_allowed if stale else status.get("future_forecast_available"))),
        confirm="Confirmo simulación histórica" if simulation_allowed else None)

    with page:
        if generate:
            _generate(horizon, stale, confirmed)
        # The last generation persists for the whole browser session.
        forecast = st.session_state.get("model_demand_forecast")
        _chips(status, model, forecast)
        if status.get("source_notice"):
            st.warning(status["source_notice"])
        if stale:
            st.warning(status.get("reason") or "El dato real está fuera del límite de frescura.")
        elif not status.get("future_forecast_available"):
            st.warning(status.get("reason") or "Faltan entradas para una previsión de 48 horas.")
        result = _forecast(forecast, status, horizon) if forecast else None
        if not forecast:
            if stale and not simulation_allowed:
                st.info(status.get("reason") or "No hay suficientes entradas históricas para "
                        "simular.")
            elif stale or status.get("future_forecast_available"):
                pending(horizon, "la simulación histórica" if stale else "la demanda")
        _tracking()
        if result:
            _details(forecast, *result)

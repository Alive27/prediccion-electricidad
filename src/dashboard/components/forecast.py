"""Demand forecast payloads: parsing, freshness and the observed-versus-simulated chart."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from components.charts import base_figure, palette, show


LOCAL_ZONE = "Europe/Madrid"


def prediction_frame(forecast: dict) -> pd.DataFrame:
    frame = pd.DataFrame(forecast.get("predictions", []))
    if frame.empty or not {"timestamp_utc", "demand_mw"}.issubset(frame.columns):
        return pd.DataFrame()
    frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True)
    frame["demand_mw"] = pd.to_numeric(frame.demand_mw, errors="coerce")
    frame = frame.dropna().sort_values("timestamp_utc").reset_index(drop=True)
    frame = frame.loc[np.isfinite(frame.demand_mw)].copy()
    frame["timestamp_local"] = frame.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
    return frame


def render_timeline(history_records: list[dict], forecast: dict,
                    predictions: pd.DataFrame, key: str) -> None:
    """Observed demand up to the reference instant, then the historical simulation."""
    colors = palette()
    figure = base_figure(360)
    origin = pd.Timestamp(forecast["forecast_origin_utc"])
    observed = pd.DataFrame(history_records)
    keys = []
    if not observed.empty:
        observed["timestamp_utc"] = pd.to_datetime(observed.timestamp_utc, utc=True)
        observed["demand_mw"] = pd.to_numeric(observed.demand_mw, errors="coerce")
        observed = observed.loc[observed.timestamp_utc.le(origin)
                                & np.isfinite(observed.demand_mw)].tail(168)
        local = observed.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
        figure.add_trace(go.Scatter(
            x=local.dt.tz_localize(None), y=observed.demand_mw, mode="lines",
            name="Demanda observada", line={"color": colors["observed"], "width": 2},
            hovertemplate="Demanda real · <b>%{y:,.0f} MW</b><extra></extra>"))
        keys.append((colors["observed"], "Demanda observada", "line"))
    local = predictions.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
    figure.add_trace(go.Scatter(
        x=local.dt.tz_localize(None), y=predictions.demand_mw, mode="lines",
        name="Simulación histórica", line={"color": colors["model"], "width": 2.5},
        hovertemplate="Demanda simulada · <b>%{y:,.0f} MW</b><extra></extra>"))
    keys.append((colors["model"], "Simulación histórica", "line"))
    origin_axis = origin.tz_convert(LOCAL_ZONE).tz_localize(None)
    figure.add_vline(x=origin_axis, line_dash="dot", line_color=colors["reference"],
                     line_width=1.2)
    figure.update_xaxes(tickformat="%d/%m<br>%H:%M")
    show(figure, key, "Contexto de la simulación",
         "Demanda observada hasta la referencia y salida simulada después · MW.", keys)


def is_current(forecast: dict, latest_observation_utc: str | None = None) -> bool:
    """A saved forecast is current only while its future timestamps remain ahead."""
    frame = prediction_frame(forecast)
    if frame.empty:
        return False
    generated = pd.Timestamp(forecast["generated_at_utc"])
    now = pd.Timestamp.now(tz="UTC")
    simulation = forecast.get("prediction_mode") == "historical_simulation"
    current = (now - generated.tz_convert("UTC") <= pd.Timedelta(hours=1)
               and (simulation or frame.timestamp_utc.max() > now))
    if simulation:
        return bool(current)
    if latest_observation_utc:
        origin = pd.Timestamp(forecast.get("forecast_origin_utc"))
        latest = pd.Timestamp(latest_observation_utc)
        if origin.tzinfo is None:
            origin = origin.tz_localize("UTC")
        if latest.tzinfo is None:
            latest = latest.tz_localize("UTC")
        current = current and latest.tz_convert("UTC") <= origin.tz_convert("UTC")
    return bool(current)

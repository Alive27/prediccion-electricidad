"""Shared executive forecast metrics and a deliberately split time-series chart."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.charts import base_figure, show as show_chart


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


def render_summary(forecast: dict, frame: pd.DataFrame) -> None:
    if frame.empty:
        st.warning("La API no devolvió valores finitos para este horizonte.")
        return
    peak = frame.loc[frame.demand_mw.idxmax()]
    valley = frame.loc[frame.demand_mw.idxmin()]
    origin = pd.Timestamp(forecast["forecast_origin_utc"]).tz_convert(LOCAL_ZONE)
    generated = pd.Timestamp(forecast["generated_at_utc"]).tz_convert(LOCAL_ZONE)
    cols = st.columns(5)
    simulation = forecast.get("prediction_mode") == "historical_simulation"
    mode_label = "Simulación" if simulation else "Previsto"
    cols[0].metric(f"Máximo {mode_label.lower()}", f"{peak.demand_mw:,.0f} MW",
                   peak.timestamp_local.strftime("%d/%m %H:%M · hora local"))
    cols[1].metric(f"Mínimo {mode_label.lower()}", f"{valley.demand_mw:,.0f} MW",
                   valley.timestamp_local.strftime("%d/%m %H:%M · hora local"))
    cols[2].metric(f"Rango {mode_label.lower()}", f"{peak.demand_mw - valley.demand_mw:,.0f} MW",
                   "máximo menos mínimo")
    cols[3].metric("Referencia de simulación" if simulation else "Origen real",
                   origin.strftime("%d/%m %H:%M"), "Europe/Madrid")
    cols[4].metric("Horizonte / modelo", f"{len(frame)} h", forecast.get("model", "—"))
    st.caption(f"Generada el {generated:%d/%m/%Y %H:%M} · hora Europe/Madrid · "
               f"origen UTC {forecast['forecast_origin_utc']} · unidad MW")
    if simulation:
        reference = pd.Timestamp(forecast["simulation_reference_utc"]).tz_convert(LOCAL_ZONE)
        horizon = forecast.get("horizon_hours", len(frame))
        st.warning(f"SIMULACIÓN HISTÓRICA: no representa las próximas {horizon} horas actuales. "
                   f"Referencia: {reference:%d/%m/%Y %H:%M %Z}; "
                   f"temperatura: {forecast.get('weather_input', 'histórica')}.")


def render_timeline(history_records: list[dict], forecast: dict,
                    predictions: pd.DataFrame, key: str) -> None:
    figure = base_figure("Demanda · MW", 430)
    origin = pd.Timestamp(forecast["forecast_origin_utc"])
    if history_records:
        observed = pd.DataFrame(history_records)
        observed["timestamp_utc"] = pd.to_datetime(observed.timestamp_utc, utc=True)
        observed["demand_mw"] = pd.to_numeric(observed.demand_mw, errors="coerce")
        observed = observed.loc[
            observed.timestamp_utc.le(origin) & np.isfinite(observed.demand_mw)
        ].tail(168)
        if not observed.empty:
            local = observed.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
            axis = observed.timestamp_utc.dt.tz_convert("UTC").dt.tz_localize(None)
            custom = [[time.strftime("%d/%m/%Y %H:%M %Z"), utc.isoformat()]
                      for time, utc in zip(local, observed.timestamp_utc)]
            figure.add_trace(go.Scatter(
                x=axis, y=observed.demand_mw, mode="lines", name="Demanda observada",
                line={"color": "#4385e5", "width": 2.5},
                customdata=custom,
                hovertemplate="%{customdata[0]} (Europe/Madrid)<br>UTC %{customdata[1]}<br>"
                              "Demanda real · <b>%{y:,.0f} MW</b><extra></extra>",
            ))
    local_forecast = predictions.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
    figure.add_trace(go.Scatter(
        x=predictions.timestamp_utc.dt.tz_convert("UTC").dt.tz_localize(None),
        y=predictions.demand_mw,
        mode="lines+markers",
        name=("Simulación histórica" if forecast.get("prediction_mode") ==
              "historical_simulation" else "Previsión"),
        line={"color": "#c47d22", "width": 2.7, "dash": "dash"},
        marker={"size": 5},
        customdata=[[time.strftime("%d/%m/%Y %H:%M %Z"), utc.isoformat()]
                    for time, utc in zip(local_forecast, predictions.timestamp_utc)],
        hovertemplate="%{customdata[0]} (Europe/Madrid)<br>UTC %{customdata[1]}<br>"
                      "Demanda estimada · <b>%{y:,.0f} MW</b><extra></extra>",
    ))
    origin_axis = origin.tz_convert("UTC").tz_localize(None)
    figure.add_vline(x=origin_axis, line_dash="dot", line_color="#718096", line_width=1.5)
    figure.add_annotation(x=origin_axis, y=1, yref="paper", text=(
        "Referencia de simulación" if forecast.get("prediction_mode") ==
        "historical_simulation" else "Última demanda real"),
                          showarrow=False, xanchor="left", yanchor="bottom",
                          font={"size": 11, "color": "#596575"})
    tick_times = pd.concat([
        observed.timestamp_utc if history_records else pd.Series(dtype="datetime64[ns, UTC]"),
        predictions.timestamp_utc,
    ]).drop_duplicates().sort_values()
    tick_times = tick_times.iloc[::max(1, len(tick_times) // 7)]
    figure.update_xaxes(
        title="Hora peninsular · Europe/Madrid", showgrid=False,
        tickvals=tick_times.dt.tz_convert("UTC").dt.tz_localize(None),
        ticktext=tick_times.dt.tz_convert(LOCAL_ZONE).dt.strftime("%d/%m %H:%M %Z"),
    )
    figure.update_layout(showlegend=True, legend={"orientation": "h", "y": 1.12},
                         hovermode="x unified")
    show_chart(figure, key)


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

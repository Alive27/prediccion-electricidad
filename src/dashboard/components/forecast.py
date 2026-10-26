"""Shared executive forecast metrics and a deliberately split time-series chart."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.charts import base_figure


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
    first_six_mean = frame.head(6).demand_mw.mean()
    origin = pd.Timestamp(forecast["forecast_origin_utc"]).tz_convert(LOCAL_ZONE)
    generated = pd.Timestamp(forecast["generated_at_utc"]).tz_convert(LOCAL_ZONE)
    cols = st.columns(6)
    cols[0].metric("Máximo previsto", f"{peak.demand_mw:,.0f} MW",
                   peak.timestamp_local.strftime("%d/%m %H:%M · hora local"))
    cols[1].metric("Mínimo previsto", f"{valley.demand_mw:,.0f} MW",
                   valley.timestamp_local.strftime("%d/%m %H:%M · hora local"))
    cols[2].metric("Rango previsto", f"{peak.demand_mw - valley.demand_mw:,.0f} MW",
                   "máximo menos mínimo")
    cols[3].metric("Media próximas 6 h", f"{first_six_mean:,.0f} MW",
                   f"calculada con {min(6, len(frame))} horas")
    cols[4].metric("Origen real", origin.strftime("%d/%m %H:%M"), "Europe/Madrid")
    cols[5].metric("Horizonte / modelo", f"{len(frame)} h", forecast.get("model", "—"))
    st.caption(f"Generada el {generated:%d/%m/%Y %H:%M} · hora Europe/Madrid · "
               f"origen UTC {forecast['forecast_origin_utc']} · unidad MW")


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
            axis = local.dt.tz_localize(None)
            custom = [[time.strftime("%d/%m/%Y %H:%M %Z"), utc.isoformat()]
                      for time, utc in zip(local, observed.timestamp_utc)]
            figure.add_trace(go.Scatter(
                x=axis, y=observed.demand_mw, mode="lines", name="Demanda real",
                line={"color": "#4385e5", "width": 2.5},
                customdata=custom,
                hovertemplate="%{customdata[0]} (Europe/Madrid)<br>"
                              "UTC %{customdata[1]}<br><b>%{y:,.0f} MW</b><extra></extra>",
            ))
    local_forecast = predictions.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
    figure.add_trace(go.Scatter(
        x=local_forecast.dt.tz_localize(None), y=predictions.demand_mw,
        mode="lines+markers", name="Previsión",
        line={"color": "#c47d22", "width": 2.7, "dash": "dash"},
        marker={"size": 5},
        customdata=[[time.strftime("%d/%m/%Y %H:%M %Z"), utc.isoformat()]
                    for time, utc in zip(local_forecast, predictions.timestamp_utc)],
        hovertemplate="%{customdata[0]} (Europe/Madrid)<br>"
                      "UTC %{customdata[1]}<br><b>%{y:,.0f} MW</b><extra></extra>",
    ))
    local_origin = origin.tz_convert(LOCAL_ZONE).tz_localize(None)
    figure.add_vline(x=local_origin, line_dash="dot", line_color="#718096", line_width=1.5)
    figure.add_annotation(x=local_origin, y=1, yref="paper", text="Inicio previsión",
                          showarrow=False, xanchor="left", yanchor="bottom",
                          font={"size": 11, "color": "#596575"})
    figure.update_xaxes(title="Hora local · Europe/Madrid", showgrid=False)
    figure.update_layout(showlegend=True, legend={"orientation": "h", "y": 1.12},
                         hovermode="x unified")
    st.plotly_chart(figure, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "displayModeBar": True,
                            "scrollZoom": True, "responsive": True})


def is_current(forecast: dict) -> bool:
    """A saved forecast is current only while its future timestamps remain ahead."""
    frame = prediction_frame(forecast)
    if frame.empty:
        return False
    generated = pd.Timestamp(forecast["generated_at_utc"])
    now = pd.Timestamp.now(tz="UTC")
    return now - generated.tz_convert("UTC") <= pd.Timedelta(hours=1) and (
        frame.timestamp_utc.max() > now
    )

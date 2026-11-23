"""Plotly charts in the "Vision" style. Times are peninsular local time; DST is preserved."""

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.blocks import card_header, html, legend
from utils.formatting import number, short_date

LOCAL_ZONE = "Europe/Madrid"
FONT = ('-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro", "Segoe UI Variable Text", '
        '"Segoe UI", Inter, Roboto, sans-serif')
# Plotly paints series colours itself, so CSS tokens cannot reach them: one palette per
# appearance. Model output in cyan, observed data in white (ink), references dashed.
_PALETTES = {
    "dark": {"model": "#64d2ff", "observed": "#ffffff", "reference": "rgba(255,255,255,.5)",
             "official": "#9d8cff", "valle": "#30d158", "llano": "#ffd60a", "punta": "#ff6961",
             "annotation": "#ffffff", "annotation_ink": "#0b1016", "marker": "#2a3038"},
    "light": {"model": "#0a84ff", "observed": "#0b1220", "reference": "rgba(11,18,32,.42)",
              "official": "#6b5ce7", "valle": "#1f9d4f", "llano": "#d99a00", "punta": "#e0453a",
              "annotation": "#0b1220", "annotation_ink": "#ffffff", "marker": "#ffffff"},
}


def palette() -> dict:
    return _PALETTES["light" if st.session_state.get("appearance") == "Claro" else "dark"]


def _rgba(color: str, alpha: float) -> str:
    color = color.lstrip("#")
    red, green, blue = (int(color[index:index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red},{green},{blue},{alpha})"


def nice_ticks(low: float, high: float, count: int = 4) -> list[float]:
    """Round axis ticks that cover [low, high] in about `count` steps."""
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        return [low]
    raw = (high - low) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(factor * magnitude for factor in (1, 2, 2.5, 4, 5, 10)
                if factor * magnitude >= raw)
    start = math.floor(low / step) * step
    stop = math.ceil(high / step) * step
    return [start + index * step for index in range(int(round((stop - start) / step)) + 1)]


def base_figure(height: int = 300, y_ticks: list[float] | None = None,
                y_format: str = ",.0f", y_suffix: str = "") -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        height=height, margin=dict(l=2, r=6, t=12, b=2),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color="#8b939e", size=10.5),
        separators=",.", showlegend=False, hovermode="x unified", dragmode=False,
        hoverlabel=dict(bgcolor="rgba(30,35,43,.96)", bordercolor="rgba(255,255,255,.16)",
                        font=dict(family=FONT, color="#ffffff", size=12)),
        yaxis=dict(gridcolor="rgba(255,255,255,.1)", zeroline=False, tickformat=y_format,
                   ticksuffix=y_suffix, fixedrange=True, showline=False, ticks="",
                   automargin=True, title=None),
        xaxis=dict(showgrid=False, zeroline=False, showline=False, ticks="", tickangle=0,
                   fixedrange=True, automargin=True, title=None, ticklabelstandoff=8),
    )
    if y_ticks:
        figure.update_yaxes(range=[y_ticks[0], y_ticks[-1]], tickvals=y_ticks)
        if y_suffix:   # Plotly drops ticksuffix when the tick values are explicit
            figure.update_yaxes(ticktext=[f"{number(value)}{y_suffix}" for value in y_ticks])
    return figure


def show(figure: go.Figure, key: str, title: str = "", subtitle: str = "",
         keys: list[tuple[str, str, str]] | None = None, reset: bool = False) -> None:
    """Chart card: optional header with a legend on the right, then the plot."""
    with st.container(key=f"plot-{key}"):
        if title:
            html(card_header(title, subtitle, legend(keys) if keys else ""))
        if reset:
            # Only charts that can be panned need a way back to the default view.
            clicked = st.button("Restablecer vista", icon=":material/restart_alt:",
                                type="tertiary", key=f"reset_view_{key}")
            figure.update_layout(uirevision=f"{key}-{int(clicked)}")
        plot(figure, key)


def _time_ticks(stamps: pd.Series, every: int, fmt: str = "%H:%M") -> dict:
    selected = stamps.iloc[::max(1, every)]
    return dict(tickvals=selected.dt.tz_localize(None), ticktext=selected.dt.strftime(fmt))


def _area_line(figure: go.Figure, x, y, color: str, low: float, high: float, name: str,
               hover: list[str], alpha: float = .22, width: float = 2.5) -> None:
    figure.add_trace(go.Scatter(
        x=x, y=y, mode="lines", name=name, line=dict(color=color, width=width, shape="spline",
                                                     smoothing=.6),
        fill="tozeroy", fillgradient=dict(
            type="vertical", start=low, stop=high,
            colorscale=[[0, _rgba(color, 0)], [1, _rgba(color, alpha)]]),
        customdata=hover, hovertemplate="%{customdata}<br>" + name
        + " · <b>%{y:,.0f} MW</b><extra></extra>"))


def forecast_chart(frame: pd.DataFrame, previous: pd.Series | None, key: str,
                   simulation: bool = False) -> None:
    """Hourly model output with the previous day's observed demand and the peak marked."""
    colors = palette()
    name = "Simulación · MW" if simulation else "Previsión · MW"
    local = frame.timestamp_local
    x = local.dt.tz_localize(None)
    values = list(frame.demand_mw) + ([] if previous is None else list(previous.dropna()))
    ticks = nice_ticks(min(values), max(values))
    figure = base_figure(300, ticks)
    keys = [(colors["model"], name, "line")]
    if previous is not None and previous.notna().any():
        figure.add_trace(go.Scatter(
            x=x, y=previous, mode="lines", name="Día anterior observado · MW",
            line=dict(color=colors["reference"], width=2, dash="5px,5px", shape="spline",
                      smoothing=.6),
            hovertemplate="Día anterior · %{y:,.0f} MW<extra></extra>"))
        keys.append((colors["reference"], "Día anterior observado · MW", "dash"))
    hover = [f"{short_date(stamp)} · {stamp:%H:%M}" for stamp in local]
    _area_line(figure, x, frame.demand_mw, colors["model"], ticks[0], ticks[-1], name, hover)
    peak = frame.demand_mw.idxmax()
    figure.add_trace(go.Scatter(
        x=[x.loc[peak]], y=[frame.demand_mw.loc[peak]], mode="markers", hoverinfo="skip",
        marker=dict(size=9, color=colors["marker"], line=dict(color=colors["model"], width=2.5))))
    figure.add_annotation(
        x=x.loc[peak], y=frame.demand_mw.loc[peak], yshift=20, showarrow=False,
        text=f"<b>Máx. {number(frame.demand_mw.loc[peak])} MW · {local.loc[peak]:%H:%M}</b>",
        bgcolor=colors["annotation"], bordercolor=colors["annotation"], borderpad=5,
        font=dict(color=colors["annotation_ink"], size=11.5))
    figure.update_xaxes(**_time_ticks(local, 2 if len(frame) <= 24 else 4))
    show(figure, key, "Simulación horaria" if simulation else "Previsión horaria",
         "Salida horaria del modelo de demanda · MW · hora peninsular.", keys)


def plot(figure: go.Figure, key: str) -> None:
    """A chart inside a card the caller already opened."""
    st.plotly_chart(figure, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "displayModeBar": False,
                            "scrollZoom": False, "doubleClick": False, "responsive": True})


def tracking_keys() -> list[tuple[str, str, str]]:
    colors = palette()
    return [(colors["model"], "Previsión registrada", "dash"),
            (colors["observed"], "Demanda real · REData", "line")]


def tracking_figure(frame: pd.DataFrame) -> go.Figure:
    """Logged forecasts (dashed) against the real demand published afterwards."""
    colors = palette()
    local = frame.timestamp_local
    x = local.dt.tz_localize(None)
    values = pd.concat([frame.actual_mw, frame.predicted_mw]).dropna()
    ticks = nice_ticks(values.min(), values.max(), 2)
    figure = base_figure(220, ticks)
    for column, name, color, dash in (
            ("actual_mw", "Demanda real · REData", colors["observed"], None),
            ("predicted_mw", "Previsión registrada", colors["model"], "4px,4px")):
        figure.add_trace(go.Scatter(
            x=x, y=frame[column], mode="lines", name=name, connectgaps=False,
            line=dict(color=color, width=1.8, dash=dash, shape="spline", smoothing=.5),
            hovertemplate=name + " · %{y:,.0f} MW<extra></extra>"))
    marks = local.loc[local.dt.minute.eq(0) & local.dt.hour.isin([0, 12])]
    figure.update_xaxes(
        tickvals=marks.dt.tz_localize(None),
        ticktext=[f"{short_date(stamp)[:3]} 00:00" if stamp.hour == 0 else "12:00"
                  for stamp in marks])
    return figure


def price_bars(frame: pd.DataFrame, colors: list[str], key: str,
               keys: list[tuple[str, str, str]]) -> None:
    """Hourly price estimate in €/kWh as rounded bars coloured by price band."""
    local = frame.local
    top = max(frame.price_eur_kwh.max(), 0.01)
    ticks = nice_ticks(0, top, 4)
    figure = base_figure(280, ticks, y_format=",.2f")
    labels = local.dt.strftime("%d/%m %H:%M")
    figure.add_trace(go.Bar(
        x=labels, y=frame.price_eur_kwh, marker=dict(color=colors, cornerradius=5),
        customdata=[f"{short_date(stamp)} · {stamp:%H:%M}" for stamp in local],
        hovertemplate="%{customdata}<br>Precio estimado · <b>%{y:,.4f} €/kWh</b>"
                      "<extra></extra>"))
    every = 2 if len(frame) <= 24 else 4
    figure.update_layout(bargap=.36, hovermode="closest")
    figure.update_xaxes(type="category", tickvals=list(labels.iloc[::every]),
                        ticktext=list(local.iloc[::every].dt.strftime("%H:%M")))
    show(figure, key, "Precio por horas",
         "Salida horaria del modelo de precios · €/kWh · hora peninsular.", keys)


def recent_line(frame: pd.DataFrame, key: str, title: str, subtitle: str) -> None:
    """Observed demand line with a soft area and the last point marked."""
    colors = palette()
    local = frame.timestamp_local
    x = local.dt.tz_localize(None)
    ticks = nice_ticks(frame.demand_mw.min(), frame.demand_mw.max(), 2)
    figure = base_figure(360, ticks)   # fills the card beside the 8-row table
    hover = [f"{short_date(stamp)} · {stamp:%H:%M}" for stamp in local]
    _area_line(figure, x, frame.demand_mw, colors["observed"], ticks[0], ticks[-1],
               "Demanda real", hover, alpha=.1, width=2)
    figure.add_trace(go.Scatter(
        x=[x.iloc[-1]], y=[frame.demand_mw.iloc[-1]], mode="markers", hoverinfo="skip",
        marker=dict(size=9, color=colors["marker"], line=dict(color=colors["observed"],
                                                              width=2.5))))
    marks = local.iloc[[0, len(local) // 2, len(local) - 1]]
    figure.update_xaxes(tickvals=marks.dt.tz_localize(None),
                        ticktext=[f"{short_date(stamp)[:3]} {stamp:%H:%M}" for stamp in marks])
    show(figure, key, title, subtitle)


def coverage_figure(labels: list[str], shares: list[float], details: list[str]) -> go.Figure:
    """Monthly share (0-100 %) of the expected hourly records."""
    figure = base_figure(170, [0, 50, 100], y_format=",.0f", y_suffix=" %")
    figure.add_trace(go.Bar(
        x=labels, y=shares, customdata=details,
        marker=dict(color=palette()["model"], cornerradius=5),
        hovertemplate="%{customdata}<extra></extra>"))
    figure.update_layout(bargap=.5, hovermode="closest")
    figure.update_xaxes(type="category")
    return figure


def hourly_profile(frame: pd.DataFrame, key: str, simulation: bool = False) -> None:
    """Mean forecast demand for each local hour of the day."""
    profile = frame.assign(hour=frame.timestamp_local.dt.hour).groupby("hour").demand_mw.mean()
    figure = base_figure(260, nice_ticks(0, profile.max(), 4))
    figure.add_trace(go.Bar(
        x=[f"{hour:02d}:00" for hour in profile.index], y=profile.values,
        marker=dict(color=_rgba(palette()["model"], .85), cornerradius=4),
        hovertemplate="Hora local %{x}<br>Demanda media · %{y:,.0f} MW<extra></extra>"))
    figure.update_layout(bargap=.3, hovermode="closest")
    figure.update_xaxes(type="category", tickvals=[f"{hour:02d}:00" for hour in range(0, 24, 3)])
    show(figure, key, "Perfil horario " + ("simulado" if simulation else "previsto"),
         "Demanda media de cada hora del día en el horizonte generado · MW.")


def official_demand_curves(payload: dict, key: str, prediction: pd.DataFrame | None = None,
                           fallback_records: list[dict] | None = None) -> None:
    """ESIOS measured and published demand with the model output, native timestamps."""
    colors = palette()
    figure = base_figure(360)
    series = (("real", "Demanda real — REE", colors["observed"], None),
              ("official_forecast", "Demanda prevista — REE", colors["official"], "6px,4px"))
    origin = (prediction.timestamp_utc.min() - pd.Timedelta(hours=1)
              if prediction is not None and not prediction.empty else None)
    traces = []
    for key_name, label, color, dash in series:
        source = payload.get("curves", {}).get(key_name, {})
        traces.append((_curve(source.get("records", []), origin if key_name == "real" else None),
                       label, color, dash, source))
    if traces[0][0].empty and fallback_records:
        traces[0] = (_curve(fallback_records, origin), "Demanda real · REData",
                     colors["observed"], None, {"data_origin": "REData"})
    keys = []
    for frame, label, color, dash, source in traces:
        if frame.empty:
            continue
        local = frame.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
        resolution = source.get("resolution_minutes")
        cadence = f"{resolution:g} min" if resolution else "nativa"
        figure.add_trace(go.Scattergl(
            x=local.dt.tz_localize(None), y=frame.demand_mw, mode="lines", name=label,
            line=dict(color=color, width=2, dash=dash),
            hovertemplate=label + " · %{y:,.0f} MW<br>Origen: "
            + str(source.get("data_origin", "sin metadatos")) + f" · resolución {cadence}"
            + "<extra></extra>"))
        keys.append((color, label, "dash" if dash else "line"))
    if prediction is not None and not prediction.empty:
        local = prediction.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
        figure.add_trace(go.Scattergl(
            x=local.dt.tz_localize(None), y=prediction.demand_mw, mode="lines",
            name="Predicción del modelo", line=dict(color=colors["model"], width=2.5),
            hovertemplate="Predicción del modelo · %{y:,.0f} MW<extra></extra>"))
        keys.append((colors["model"], "Predicción del modelo", "line"))
    if not figure.data:
        st.info("ESIOS no ha devuelto puntos para el intervalo seleccionado.")
        return
    figure.update_layout(dragmode="pan")
    figure.update_xaxes(fixedrange=False, tickformat="%d/%m<br>%H:%M",
                        rangeslider=dict(visible=True, thickness=.07,
                                         bgcolor="rgba(148,163,184,.06)"))
    figure.update_yaxes(fixedrange=False)
    show(figure, key, "Contexto: demanda real y previsión oficial",
         "Última semana de REE (ESIOS) junto a la salida del modelo · MW · hora peninsular.",
         keys, reset=True)


def _curve(records: list[dict], until: pd.Timestamp | None) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if frame.empty or not {"timestamp_utc", "demand_mw"}.issubset(frame.columns):
        return pd.DataFrame(columns=["timestamp_utc", "demand_mw"])
    frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True, errors="coerce")
    frame["demand_mw"] = pd.to_numeric(frame.demand_mw, errors="coerce")
    frame = frame.dropna(subset=["timestamp_utc", "demand_mw"]).sort_values("timestamp_utc")
    return frame.loc[frame.timestamp_utc.le(until)] if until is not None else frame

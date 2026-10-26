"""Consistent Plotly visuals. Dates retain exact instants across DST changes."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.formatting import WEEKDAYS, month_label, timestamp


BLUE = "#4385e5"
AMBER = "#c47d22"


def base_figure(unit: str, height: int = 290) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        height=height, margin=dict(l=8, r=12, t=24, b=38),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="-apple-system, Segoe UI, sans-serif", color="#596575", size=12),
        separators=",.", showlegend=False, hovermode="closest",
        hoverlabel=dict(bgcolor="#ffffff", font_color="#202630", font_size=13),
        yaxis=dict(title=unit, gridcolor="rgba(120,140,170,.15)", zeroline=False,
                   tickformat=",.0f", automargin=True, fixedrange=True),
        xaxis=dict(showgrid=False, zeroline=False, tickangle=0, automargin=True,
                   fixedrange=True),
        dragmode=False,
    )
    return figure


def show(figure: go.Figure, key: str) -> None:
    reset = st.button("Restablecer vista", icon=":material/restart_alt:", type="tertiary",
                      key=f"reset_view_{key}",
                      help="Restablece la escala predeterminada de la gráfica.")
    figure.update_layout(uirevision=f"{key}-{int(reset)}",
                         dragmode=figure.layout.dragmode or False)
    st.plotly_chart(figure, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "displayModeBar": False,
                            "scrollZoom": False, "doubleClick": False,
                            "responsive": True})


def line(frame: pd.DataFrame, unit: str, key: str, color: str = BLUE,
         extrema: bool = False, height: int = 330) -> None:
    figure = base_figure(unit, height)
    figure.update_layout(dragmode="pan")
    figure.update_xaxes(fixedrange=False)
    figure.update_yaxes(fixedrange=False)
    times = frame.timestamp
    x = times.dt.tz_convert("UTC").dt.tz_localize(None) if times.dt.tz else times
    labels = [timestamp(value, with_zone=True) for value in times]
    plot = pd.DataFrame({"x": x, "value": frame.value, "label": labels})
    deltas = x.diff().dropna()
    deltas = deltas.loc[deltas.gt(pd.Timedelta(0))]
    if not deltas.empty:
        step = deltas.mode().iloc[0]
        gaps = plot.loc[x.diff().gt(step)].copy()
        if not gaps.empty:
            gaps["x"] = gaps.x - step / 2
            gaps["value"] = float("nan")
            plot = pd.concat([plot, gaps]).sort_values("x")
    figure.add_trace(go.Scatter(
        x=plot.x, y=plot.value, customdata=plot.label, mode="lines", connectgaps=False,
        name="Demanda observada" if unit == "MW" else "Temperatura histórica",
        line=dict(color=color, width=2.4),
        hovertemplate="%{customdata}<br>Demanda histórica · <b>%{y:,.1f} "
        + unit + "</b><extra></extra>",
    ))
    positions = sorted({int(i * (len(frame) - 1) / 5) for i in range(6)})
    ticktext = [times.iloc[i].strftime("%d/%m<br>%H:%M") for i in positions]
    figure.update_xaxes(tickvals=[x.iloc[i] for i in positions], ticktext=ticktext)
    if extrema:
        for label, index, symbol in (("Máximo", frame.value.idxmax(), "triangle-up"),
                                     ("Mínimo", frame.value.idxmin(), "triangle-down")):
            row = frame.loc[index]
            figure.add_trace(go.Scatter(
                x=[x.loc[index]], y=[row.value], mode="markers", name=label,
                marker=dict(color=color, size=11, symbol=symbol,
                            line=dict(color="white", width=1)),
                hovertemplate=label + " · " + timestamp(row.timestamp, True)
                + "<br>%{y:,.1f} " + unit + "<extra></extra>",
            ))
        figure.update_layout(showlegend=True, legend=dict(orientation="h", y=1.12, x=0))
    show(figure, key)


def official_demand_curves(payload: dict, key: str = "official_demand_curves",
                           prediction: pd.DataFrame | None = None,
                           fallback_records: list[dict] | None = None) -> None:
    """Overlay ESIOS measured and published demand, preserving native timestamps."""
    figure = base_figure("Demanda · MW", 390)
    series = (("real", "Demanda real — REE", BLUE, "solid"),
              ("official_forecast", "Demanda prevista — REE", AMBER, "dash"))
    any_values = False
    axis_times: list[pd.Timestamp] = []
    for key_name, label, color, dash in series:
        source = payload.get("curves", {}).get(key_name, {})
        frame = pd.DataFrame(source.get("records", []))
        if frame.empty:
            continue
        frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True, errors="coerce")
        frame["demand_mw"] = pd.to_numeric(frame.demand_mw, errors="coerce")
        frame = frame.dropna().sort_values("timestamp_utc")
        model_origin = None
        if prediction is not None and not prediction.empty:
            model_origin = prediction.timestamp_utc.min() - pd.Timedelta(hours=1)
        if key_name == "real" and model_origin is not None:
            frame = frame.loc[frame.timestamp_utc.le(model_origin)]
        if frame.empty:
            continue
        local = frame.timestamp_utc.dt.tz_convert("Europe/Madrid")
        x = frame.timestamp_utc.dt.tz_localize(None)
        axis_times.extend(frame.timestamp_utc.tolist())
        labels = local.map(lambda value: value.strftime("%A %d/%m/%Y %H:%M %Z"))
        resolution = source.get("resolution_minutes")
        cadence = f"{resolution:g} min" if resolution else "nativa"
        provenance = source.get("data_origin", "sin metadatos")
        figure.add_trace(go.Scattergl(
            x=x, y=frame.demand_mw, customdata=labels, mode="lines",
            connectgaps=False, name=label,
            line=dict(color=color, width=2.2, dash=dash),
            hovertemplate="%{customdata}<br>" + label + " · %{y:,.1f} MW"
                          f"<br>Fuente: ESIOS · origen: {provenance}"
                          f" · resolución observada: {cadence}<extra></extra>"))
        any_values = True
    real_records = payload.get("curves", {}).get("real", {}).get("records", [])
    if not real_records and fallback_records:
        fallback = pd.DataFrame(fallback_records)
        if {"timestamp_utc", "demand_mw"}.issubset(fallback.columns):
            fallback["timestamp_utc"] = pd.to_datetime(
                fallback.timestamp_utc, utc=True, errors="coerce")
            fallback["demand_mw"] = pd.to_numeric(fallback.demand_mw, errors="coerce")
            fallback = fallback.dropna().sort_values("timestamp_utc")
            if prediction is not None and not prediction.empty:
                origin = prediction.timestamp_utc.min() - pd.Timedelta(hours=1)
                fallback = fallback.loc[fallback.timestamp_utc.le(origin)]
            if not fallback.empty:
                local = fallback.timestamp_utc.dt.tz_convert("Europe/Madrid")
                axis_times.extend(fallback.timestamp_utc.tolist())
                figure.add_trace(go.Scattergl(
                    x=fallback.timestamp_utc.dt.tz_localize(None), y=fallback.demand_mw,
                    mode="lines", connectgaps=False, name="Demanda real — REE · REData",
                    line=dict(color=BLUE, width=2.2),
                    customdata=local.map(lambda value: value.strftime("%A %d/%m/%Y %H:%M %Z")),
                    hovertemplate="%{customdata}<br>Demanda real · %{y:,.1f} MW"
                                  "<br>Fuente: REData · resolución horaria<extra></extra>"))
                any_values = True
    if prediction is not None and not prediction.empty:
        local = prediction.timestamp_utc.dt.tz_convert("Europe/Madrid")
        origin = prediction.timestamp_utc.min() - pd.Timedelta(hours=1)
        axis_times.extend(prediction.timestamp_utc.tolist())
        figure.add_trace(go.Scattergl(
            x=prediction.timestamp_utc.dt.tz_localize(None), y=prediction.demand_mw,
            mode="lines+markers", name="Predicción del modelo",
            line=dict(color="#8b5cf6", width=2.7, dash="dashdot"), marker=dict(size=5),
            customdata=local.map(lambda value: value.strftime("%A %d/%m/%Y %H:%M %Z")),
            hovertemplate="%{customdata}<br>Predicción del modelo · %{y:,.1f} MW"
                          "<br>Resolución: 1 hora<extra></extra>"))
        figure.add_vline(x=origin.tz_localize(None), line_dash="dot",
                         line_color="#718096", line_width=1.5)
        any_values = True
    figure.update_layout(showlegend=True, hovermode="x unified",
                         legend=dict(orientation="h", y=1.12, x=0), dragmode="pan")
    figure.update_xaxes(fixedrange=False, rangeslider=dict(visible=True, thickness=0.08),
                        title="Fecha y hora peninsular")
    if axis_times:
        ticks = pd.Series(pd.to_datetime(axis_times, utc=True)).drop_duplicates().sort_values()
        positions = sorted({int(index * (len(ticks) - 1) / 6) for index in range(7)})
        selected = ticks.iloc[positions]
        figure.update_xaxes(
            tickvals=selected.dt.tz_convert("UTC").dt.tz_localize(None),
            ticktext=selected.dt.tz_convert("Europe/Madrid").dt.strftime("%d/%m<br>%H:%M %Z"))
    figure.update_yaxes(fixedrange=False)
    if not any_values:
        st.info("ESIOS no ha devuelto puntos para el intervalo seleccionado.")
        return
    show(figure, key)


def bars(labels: list[str], values, counts, unit: str, key: str,
         color: str = BLUE, monthly: bool = False, height: int = 270) -> None:
    figure = base_figure(unit, height)
    hoverlabels = [month_label(label, full=True) for label in labels] if monthly else labels
    figure.add_trace(go.Bar(
        x=labels, y=values, customdata=list(zip(hoverlabels, counts)), marker_color=color,
        hovertemplate="%{customdata[0]}<br>Media observada · <b>%{y:,.1f} " + unit
        + "</b><br>%{customdata[1]} registros<extra></extra>",
    ))
    figure.update_xaxes(type="category", categoryorder="array", categoryarray=labels)
    if monthly:
        years = {label[:4] for label in labels}
        ticktext = [month_label(label) + (f" {label[:4]}" if len(years) > 1 else "")
                    for label in labels]
        figure.update_xaxes(tickvals=labels, ticktext=ticktext)
    elif len(labels) > 12:
        figure.update_xaxes(tickvals=labels[::3], ticktext=labels[::3])
    elif all(label in WEEKDAYS for label in labels):
        figure.update_xaxes(tickvals=labels, ticktext=[label[:3] for label in labels])
    show(figure, key)


def heatmap(frame: pd.DataFrame, weekdays: list[str]) -> None:
    cells = frame.assign(day=frame.timestamp.dt.dayofweek, hour=frame.timestamp.dt.hour)
    matrix = cells.pivot_table(index="day", columns="hour", values="value", aggfunc="mean")
    matrix = matrix.reindex(index=range(7), columns=range(24))
    counts = cells.pivot_table(index="day", columns="hour", values="value", aggfunc="count")
    counts = counts.reindex(index=range(7), columns=range(24)).fillna(0)
    figure = base_figure("", 320)
    figure.add_trace(go.Heatmap(
        z=matrix.to_numpy(), x=[f"{hour:02d}:00" for hour in range(24)], y=weekdays,
        customdata=counts.to_numpy(),
        colorscale=[[0, "#dfeafb"], [0.45, "#87ade5"], [1, "#285fae"]],
        colorbar=dict(title="MW", orientation="h", y=-0.2, yanchor="top", thickness=9,
                      len=0.8, tickformat=",.0f", nticks=4), hoverongaps=False,
        hovertemplate="%{y} · %{x}<br>Demanda histórica · <b>%{z:,.0f} MW</b>"
        "<br>%{customdata:.0f} registros<extra></extra>",
    ))
    figure.update_yaxes(autorange="reversed")
    figure.update_xaxes(tickvals=[f"{hour:02d}:00" for hour in range(0, 24, 4)])
    figure.update_layout(margin=dict(l=8, r=12, t=8, b=100))
    show(figure, "historical_heatmap")


def distribution(frame: pd.DataFrame) -> None:
    figure = base_figure("Número de registros", 300)
    figure.add_trace(go.Histogram(
        x=frame.value, nbinsx=35, marker_color=BLUE,
        hovertemplate="Demanda histórica · %{x:,.0f} MW<br>%{y} registros<extra></extra>",
    ))
    figure.update_xaxes(title="Demanda eléctrica · MW", tickformat=",.0f")
    show(figure, "historical_distribution")


def temperature_demand(joined: pd.DataFrame) -> None:
    figure = base_figure("Demanda · MW", 350)
    figure.add_trace(go.Scattergl(
        x=joined.value_weather, y=joined.value_demand, mode="markers",
        marker=dict(color=BLUE, opacity=0.35, size=5),
        customdata=[timestamp(value, True) for value in joined.timestamp],
        hovertemplate="%{customdata}<br>%{x:.1f} °C · %{y:,.0f} MW<extra></extra>",
    ))
    figure.update_xaxes(title="Temperatura de Madrid · °C")
    show(figure, "historical_temperature_demand")

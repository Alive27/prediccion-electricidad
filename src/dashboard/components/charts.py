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
                   tickformat=",.0f", automargin=True),
        xaxis=dict(showgrid=False, zeroline=False, tickangle=0, automargin=True),
    )
    return figure


def show(figure: go.Figure, key: str) -> None:
    st.plotly_chart(figure, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "displayModeBar": False,
                            "scrollZoom": False, "responsive": True})


def line(frame: pd.DataFrame, unit: str, key: str, color: str = BLUE,
         extrema: bool = False, height: int = 330) -> None:
    figure = base_figure(unit, height)
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
        name="Demanda" if unit == "MW" else "Temperatura",
        line=dict(color=color, width=2.4),
        hovertemplate="%{customdata}<br><b>%{y:,.1f} " + unit + "</b><extra></extra>",
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


def bars(labels: list[str], values, counts, unit: str, key: str,
         color: str = BLUE, monthly: bool = False, height: int = 270) -> None:
    figure = base_figure(unit, height)
    hoverlabels = [month_label(label, full=True) for label in labels] if monthly else labels
    figure.add_trace(go.Bar(
        x=labels, y=values, customdata=list(zip(hoverlabels, counts)), marker_color=color,
        hovertemplate="%{customdata[0]}<br><b>%{y:,.1f} " + unit
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
        hovertemplate="%{y} · %{x}<br><b>%{z:,.0f} MW</b>"
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
        hovertemplate="%{x:,.0f} MW<br>%{y} registros<extra></extra>",
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

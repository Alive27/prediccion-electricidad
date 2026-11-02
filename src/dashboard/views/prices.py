"""Calendar-model price estimates; this view does not request price history.

The API returns €/MWh; the page shows €/kWh (÷ 1000), the unit of household bills.
"""

from html import escape

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from components.charts import base_figure, show
from components.layout import header, section
from components.run_panel import generation_notice, pending, run_panel, status_chips
from services import api_client
from utils.formatting import WEEKDAYS, local_datetime, number

LOCAL_ZONE = "Europe/Madrid"
BANDS = {   # relative bands over the forecast, cheapest third to most expensive third
    "valle": ("Valle", "Horas más baratas: buen momento para concentrar consumo", "#21d18b"),
    "llano": ("Llano", "Precio intermedio", "#f0c272"),
    "punta": ("Punta", "Horas más caras: mejor desplazar el consumo", "#ff7b72"),
}


def _frame(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True, errors="coerce")
    frame["price_eur_mwh"] = pd.to_numeric(frame.price_eur_mwh, errors="coerce")
    return frame.dropna(subset=["timestamp_utc", "price_eur_mwh"]).sort_values(
        "timestamp_utc")


def price_bands(frame: pd.DataFrame) -> pd.DataFrame:
    """Add €/kWh, local time and the valle/llano/punta band (terciles of the forecast)."""
    out = frame.copy().reset_index(drop=True)
    out["price_eur_kwh"] = out.price_eur_mwh / 1000
    out["local"] = out.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
    low, high = out.price_eur_kwh.quantile([1 / 3, 2 / 3])
    out["band"] = ["valle" if value <= low else "punta" if value > high else "llano"
                   for value in out.price_eur_kwh]
    return out


def best_window(frame: pd.DataFrame, hours: int, cheapest: bool = True) -> pd.DataFrame:
    """Consecutive block of `hours` with the lowest (or highest) mean price."""
    hours = min(hours, len(frame))
    means = frame.price_eur_kwh.rolling(hours).mean()
    end = int(means.idxmin() if cheapest else means.idxmax())
    return frame.iloc[end - hours + 1:end + 1]


def _kwh(value: float) -> str:
    return f"{number(value, 4)} €/kWh"


def _hour(stamp: pd.Timestamp) -> str:
    return stamp.strftime("%H:%M")


def _window_card(frame: pd.DataFrame, band: str, title: str) -> str:
    label, hint, color = BANDS[band]
    start, end = frame.local.iloc[0], frame.local.iloc[-1] + pd.Timedelta(hours=1)
    day = f"{WEEKDAYS[start.weekday()]} {start:%d/%m}"
    return (
        f'<div class="band-card" style="--band:{color}"><small>{escape(title)}</small>'
        f'<strong>{_hour(start)} – {_hour(end)}</strong><span>{escape(day)} · media '
        f'{_kwh(frame.price_eur_kwh.mean())}</span><em>{escape(label)} · {escape(hint)}</em></div>')


def _band_summary(frame: pd.DataFrame) -> None:
    section("Franjas de precio",
            "Clasificación relativa sobre esta previsión: tercio más barato (valle), "
            "intermedio (llano) y más caro (punta). No son los periodos oficiales de la "
            "tarifa 2.0TD.")
    block = 3 if len(frame) >= 12 else 2
    counts = frame.band.value_counts()
    legend = "".join(
        f'<span class="band-key" style="--band:{color}"><i></i>{escape(label)} '
        f'<strong>{number(counts.get(key, 0))} h</strong></span>'
        for key, (label, _, color) in BANDS.items())
    strip = "".join(
        f'<span class="band-cell" style="--band:{BANDS[row.band][2]}" '
        f'title="{_hour(row.local)} · {_kwh(row.price_eur_kwh)} · {BANDS[row.band][0]}">'
        f'{"<b>" + row.local.strftime("%H") + "</b>" if row.local.hour % 3 == 0 else ""}</span>'
        for row in frame.itertuples())
    cheap = _window_card(best_window(frame, block), "valle", f"Mejor bloque de {block} h")
    dear = _window_card(best_window(frame, block, cheapest=False), "punta",
                        f"Bloque de {block} h más caro")
    st.markdown(
        f'<div class="band-grid">{cheap}{dear}</div>'
        f'<div class="band-strip" style="--hours:{len(frame)}" role="img" '
        f'aria-label="Franja de cada hora prevista">{strip}</div>'
        f'<div class="band-legend">{legend}</div>', unsafe_allow_html=True)


def _band_table(frame: pd.DataFrame) -> None:
    section("Precio por horas", "Salida horaria del modelo de precios · €/kWh · hora peninsular.")
    top = frame.price_eur_kwh.max() or 1
    rows = []
    for row in frame.itertuples():
        label, _, color = BANDS[row.band]
        rows.append(
            f'<tr><td>{WEEKDAYS[row.local.weekday()][:3]} {row.local:%d/%m}</td>'
            f'<td class="num">{_hour(row.local)}</td>'
            f'<td class="num">{number(row.price_eur_kwh, 4)}</td>'
            f'<td class="level"><span class="bar" style="--band:{color};'
            f'--w:{row.price_eur_kwh / top * 100:.1f}%"></span></td>'
            f'<td><span class="band-badge" style="--band:{color}">{label}</span></td></tr>')
    st.markdown(
        '<div class="data-table price-table" role="region" aria-label="Precio por horas" '
        'tabindex="0"><table><thead><tr><th>Día</th><th>Hora</th><th>€/kWh</th>'
        '<th class="level">Nivel</th><th>Franja</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>', unsafe_allow_html=True)


def _chart(frame: pd.DataFrame, key: str) -> None:
    figure = base_figure("€/kWh", 340)
    figure.add_trace(go.Scatter(
        x=frame.local.dt.tz_localize(None), y=frame.price_eur_kwh,
        customdata=list(zip(frame.local.dt.strftime("%d/%m/%Y %H:%M %Z"),
                            [BANDS[band][0] for band in frame.band])),
        mode="lines+markers", name="Precio previsto",
        line={"color": "#8b5cf6", "width": 2.5, "dash": "dash"},
        marker={"size": 8, "color": [BANDS[band][2] for band in frame.band]},
        hovertemplate="%{customdata[0]}<br>Precio previsto · %{y:,.4f} €/kWh"
                      "<br>Franja %{customdata[1]}<extra></extra>"))
    figure.update_xaxes(title="Hora peninsular", fixedrange=False)
    figure.update_yaxes(fixedrange=False, tickformat=",.3f")
    show(figure, key)


def render(datasets: dict) -> None:
    header("Predicción de precios",
           "Previsión horaria del precio de la electricidad · 6, 12, 24 o 48 horas")
    try:
        model = api_client.cached_price_model()
    except (requests.RequestException, ValueError):
        model = {}
    details = model.get("model", {}) if isinstance(model, dict) else {}
    loaded = model.get("loaded") if isinstance(model, dict) else None
    status_chips([
        ("success" if loaded else "warning" if loaded is None else "error", "Modelo",
         details.get("model_type", "RandomForest") if loaded is not False else "No cargado"),
        ("", "Variables", "calendario"),
        ("", "Unidad", "€/kWh"),
        ("", "Zona horaria", LOCAL_ZONE),
    ])

    horizon, generate, _ = run_panel(
        "price", "Precio horario estimado por el modelo de calendario; no es un precio "
        "observado ni oficial. Horas futuras en Europe/Madrid.", "Generar previsión", default=24)
    if generate:
        try:
            with st.spinner(f"Calculando {horizon} horas…"):
                forecast = api_client.predict_price(horizon)
            st.session_state["price_forecast"] = forecast
            st.session_state["price_forecast_horizon"] = horizon
        except requests.HTTPError as error:
            try:
                detail = error.response.json().get("detail", str(error))
            except (ValueError, AttributeError):
                detail = str(error)
            st.error(f"No se pudo cargar o ejecutar el modelo de precios: {detail}")
            return
        except (requests.RequestException, ValueError, KeyError) as error:
            st.error(f"No se pudo ejecutar el modelo de precios ({type(error).__name__}).")
            return

    # The last generation persists for the whole browser session.
    forecast = st.session_state.get("price_forecast")
    if forecast is None:
        pending(horizon, "el precio")
        return
    shown = int(st.session_state.get("price_forecast_horizon") or horizon)
    generation_notice(shown, horizon, forecast.get("generated_at_utc"))
    predicted = _frame(forecast.get("records", []))
    if len(predicted) != shown:
        st.error("El modelo no devolvió el número esperado de predicciones horarias.")
        return
    frame = price_bands(predicted)
    prices = frame.price_eur_kwh
    section("Resumen ejecutivo", "Calculado desde la salida horaria del modelo de precios.")
    cheapest = frame.loc[prices.idxmin()]
    priciest = frame.loc[prices.idxmax()]
    metrics = st.columns(4)
    metrics[0].metric("Siguiente hora", _kwh(prices.iloc[0]),
                      f"{_hour(frame.local.iloc[0])} · {BANDS[frame.band.iloc[0]][0]}")
    metrics[1].metric("Media prevista", _kwh(prices.mean()), f"{shown} horas")
    metrics[2].metric("Máximo previsto", _kwh(prices.max()),
                      f"{priciest.local:%d/%m %H:%M} · hora local")
    metrics[3].metric("Mínimo previsto", _kwh(prices.min()),
                      f"{cheapest.local:%d/%m %H:%M} · hora local")
    generated = forecast.get("generated_at_utc")
    st.caption(f"Generada el {local_datetime(generated) if generated else 'sin fecha'} · "
               "hora peninsular · unidad €/kWh (salida del modelo en €/MWh ÷ 1000)")
    _chart(frame, "pvpc_model_estimate")
    _band_summary(frame)
    _band_table(frame)

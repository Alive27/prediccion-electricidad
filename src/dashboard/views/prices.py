"""Precios: calendar-model price estimates; this view does not request price history.

The API returns €/MWh; the page shows €/kWh (÷ 1000), the unit of household bills.
"""

from html import escape

import pandas as pd
import requests
import streamlit as st

from components import charts
from components.blocks import card, card_header, grid, html, metric, mini, note
from components.data_table import Column, table_html
from components.icons import svg
from components.layout import header
from components.run_panel import generation_notice, pending, run_bar
from services import api_client
from utils.formatting import WEEKDAYS, local_datetime, number, short_date

LOCAL_ZONE = "Europe/Madrid"
BANDS = {   # relative bands over the forecast, cheapest third to most expensive third
    "valle": ("Valle", "Horas más baratas: buen momento para concentrar consumo"),
    "llano": ("Llano", "Precio intermedio"),
    "punta": ("Punta", "Horas más caras: mejor desplazar el consumo"),
}
MODEL_NOTE = "Precio horario estimado por el modelo de calendario; no es un precio oficial PVPC."


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
    return number(value, 4)


def _hour_label(stamp: pd.Timestamp) -> str:
    return f"{short_date(stamp)[:3]} {stamp:%H:%M}"


def _summary(frame: pd.DataFrame) -> None:
    prices, local = frame.price_eur_kwh, frame.local
    high, low = prices.idxmax(), prices.idxmin()
    html(grid([
        metric("Siguiente hora", _kwh(prices.iloc[0]), "€/kWh",
               f"{short_date(local.iloc[0])} · {local.iloc[0]:%H:%M} · "
               f"{BANDS[frame.band.iloc[0]][0]}"),
        metric("Media prevista", _kwh(prices.mean()), "€/kWh", f"{len(frame)} horas"),
        metric("Máximo previsto", _kwh(prices.loc[high]), "€/kWh",
               f"{short_date(local.loc[high])} · {local.loc[high]:%H:%M}"),
        metric("Mínimo previsto", _kwh(prices.loc[low]), "€/kWh",
               f"{short_date(local.loc[low])} · {local.loc[low]:%H:%M}"),
    ], 4))


def _block(frame: pd.DataFrame, cheapest: bool, hours: int) -> str:
    window = best_window(frame, hours, cheapest)
    start, end = window.local.iloc[0], window.local.iloc[-1] + pd.Timedelta(hours=1)
    label = f"Mejor bloque de {hours} h" if cheapest else f"Bloque de {hours} h más caro"
    band = "valle" if cheapest else "punta"
    return (f'<div class="v-blk" style="--bc:var(--{band})"><span class="v-blk-i">'
            f'{svg("down" if cheapest else "up", 18, 2.2)}</span><div>'
            f'<div class="v-m-l">{escape(label)}</div>'
            f'<div class="v-blk-v">{start:%H:%M} – {end:%H:%M}</div>'
            f'<div class="v-m-s">media {_kwh(window.price_eur_kwh.mean())} €/kWh · '
            f'{escape(short_date(start))}</div></div></div>')


def _bands_card(frame: pd.DataFrame) -> None:
    tiles = []
    for key, (label, hint) in BANDS.items():
        values = frame.loc[frame.band.eq(key), "price_eur_kwh"]
        if values.empty:
            continue
        tiles.append(
            f'<div class="v-band" style="--bc:var(--{key})"><div class="v-band-h">'
            f'<i class="v-dot"></i><b>{label}</b><span>· {len(values)} horas</span></div>'
            f'<div class="v-band-v">{_kwh(values.min())} – {_kwh(values.max())} '
            f'<small>€/kWh</small></div><p>{escape(hint)}</p></div>')
    hours = 3 if len(frame) >= 12 else 2
    html(card(card_header("Franjas de precio",
                          "Clasificación relativa sobre esta previsión: tercio más barato "
                          "(valle), intermedio (llano) y más caro (punta)."),
              grid(tiles, 3, "v-tight"),
              grid([_block(frame, True, hours), _block(frame, False, hours)], 2, "v-tight")))


def _chart(frame: pd.DataFrame) -> None:
    colors = charts.palette()
    charts.price_bars(frame, [colors[band] for band in frame.band], "pvpc_model_estimate",
                      [(colors[key], label, "square") for key, (label, _) in BANDS.items()])


def _hours_card(frame: pd.DataFrame) -> None:
    columns = [Column("Hora", "40%"), Column("€/kWh", "26%", True), Column("Franja", "34%")]
    middle = (len(frame) + 1) // 2
    halves = [frame.iloc[:middle], frame.iloc[middle:]]
    tables = "".join(table_html(columns, [
        [_hour_label(row.local), _kwh(row.price_eur_kwh), (BANDS[row.band][0], f"band-{row.band}")]
        for row in half.itertuples()], label="Franja de cada hora prevista") for half in halves)
    html(card(card_header("Franja de cada hora prevista", "Hora local peninsular · Europe/Madrid."),
              f'<div class="v-halves">{tables}</div>'))


def _details(frame: pd.DataFrame, model: dict) -> None:
    """Everything the summary above does not show: the model and each hour's level."""
    details = model.get("model", {}) if isinstance(model, dict) else {}
    loaded = model.get("loaded") if isinstance(model, dict) else None
    model_name = details.get("model_type", "RandomForest").replace("Regressor", "")
    html(card(card_header("Modelo de precios", "Artefacto cargado en la API y sus entradas."),
              grid([mini("Modelo", model_name if loaded is not False else "No cargado"),
                    mini("Variables", "calendario", "hora, día de la semana, mes y día"),
                    mini("Unidad", "€/kWh", "salida del modelo en €/MWh ÷ 1000"),
                    mini("Zona horaria", LOCAL_ZONE, "hora peninsular")], 4, "v-minis")))
    top = frame.price_eur_kwh.max() or 1
    rows = "".join(
        f'<tr><td>{WEEKDAYS[row.local.weekday()][:3]} {row.local:%d/%m}</td>'
        f'<td class="num">{row.local:%H:%M}</td><td class="num">{_kwh(row.price_eur_kwh)}</td>'
        f'<td class="level"><span class="bar" style="--band:var(--{row.band});'
        f'--w:{row.price_eur_kwh / top * 100:.1f}%"></span></td>'
        f'<td><span class="band-badge" style="--band:var(--{row.band})">'
        f'{BANDS[row.band][0]}</span></td></tr>' for row in frame.itertuples())
    html(card(card_header("Detalle por hora", "Nivel de cada hora frente a la más cara."),
              '<div class="data-table price-table" role="region" aria-label="Detalle por hora" '
              'tabindex="0"><table><thead><tr><th>Día</th><th>Hora</th><th>€/kWh</th>'
              f'<th class="level">Nivel</th><th>Franja</th></tr></thead><tbody>{rows}</tbody>'
              '</table></div>'))


def _generate(horizon: int) -> bool:
    try:
        with st.spinner(f"Calculando {horizon} horas…"):
            forecast = api_client.predict_price(horizon)
        st.session_state["price_forecast"] = forecast
        st.session_state["price_forecast_horizon"] = horizon
        return True
    except requests.HTTPError as error:
        try:
            detail = error.response.json().get("detail", str(error))
        except (ValueError, AttributeError):
            detail = str(error)
        st.error(f"No se pudo cargar o ejecutar el modelo de precios: {detail}")
    except (requests.RequestException, ValueError, KeyError) as error:
        st.error(f"No se pudo ejecutar el modelo de precios ({type(error).__name__}).")
    return False


def render(datasets: dict) -> None:
    header("Predicción de precios",
           "Previsión horaria del precio de la electricidad · 6, 12, 24 o 48 horas",
           eyebrow="MODELO DE PRECIOS")
    page = st.container()   # the execution bar below is rendered last, over the window edge
    horizon, generate, _ = run_bar("price", default=24)
    with page:
        if generate and not _generate(horizon):
            return
        # The last generation persists for the whole browser session.
        forecast = st.session_state.get("price_forecast")
        if forecast is None:
            html(note(MODEL_NOTE))
            pending(horizon, "el precio")
            return
        shown = int(st.session_state.get("price_forecast_horizon") or horizon)
        generated = forecast.get("generated_at_utc")
        html(note(f"{MODEL_NOTE} Generada el "
                  f"{local_datetime(generated) if generated else 'sin fecha'} · {shown} horas."))
        generation_notice(shown, horizon, generated)
        predicted = _frame(forecast.get("records", []))
        if len(predicted) != shown:
            st.error("El modelo no devolvió el número esperado de predicciones horarias.")
            return
        frame = price_bands(predicted)
        _summary(frame)
        _bands_card(frame)
        _chart(frame)
        _hours_card(frame)
        try:
            model = api_client.cached_price_model()
        except (requests.RequestException, ValueError):
            model = {}
        _details(frame, model)

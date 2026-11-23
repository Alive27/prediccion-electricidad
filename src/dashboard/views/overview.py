"""Resumen: the 3D city fills the screen and the live indicators float over it."""

import pandas as pd
import requests
import streamlit as st

from components.blocks import card, card_header, chip, grid, html, metric, mini
from components.city_scene import render_city
from components.forecast import is_current, prediction_frame
from components.icons import svg
from components.layout import heading_html
from services import api_client
from utils.formatting import local_datetime, local_stamp, number, short_date, short_datetime


TITLE = "Centro de control energético"
SUBTITLE = "Demanda agregada peninsular, datos observados y modelos en una vista interactiva."


def _latest_price() -> dict:
    """Return the latest observed PVPC point; never substitute a model estimate."""
    try:
        response = api_client.cached_price_history(1)
    except (requests.RequestException, ValueError):
        return {}
    frame = pd.DataFrame(response.get("records", []))
    if frame.empty:
        return response
    frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True, errors="coerce")
    frame["price_eur_mwh"] = pd.to_numeric(frame.price_eur_mwh, errors="coerce")
    frame = frame.dropna(subset=["timestamp_utc", "price_eur_mwh"])
    if frame.empty:
        return response
    point = frame.sort_values("timestamp_utc").iloc[-1]
    return {**response, "latest_price_eur_mwh": float(point.price_eur_mwh),
            "latest_price_utc": point.timestamp_utc.isoformat()}


def _price_estimate() -> dict:
    """Independent calendar-model estimate, reused for an hour within the session."""
    saved = st.session_state.get("overview_price_estimate")
    if saved:
        generated = pd.Timestamp(saved.get("generated_at_utc"))
        if pd.Timestamp.now(tz="UTC") - generated.tz_convert("UTC") < pd.Timedelta(hours=1):
            return saved
    try:
        estimate = api_client.predict_price(24)
        st.session_state["overview_price_estimate"] = estimate
        return estimate
    except requests.HTTPError as error:
        detail = error.response.text if error.response is not None else str(error)
        st.warning(f"Estimación de precio no disponible: {detail[:320]}")
    except requests.RequestException as error:
        st.warning(f"No se pudo consultar el modelo de precios ({type(error).__name__}).")
    return {}


def _demand_forecast(status: dict) -> dict:
    """48-hour profile that drives the city lights, reused while it is still current."""
    saved = st.session_state.get("overview_demand_forecast")
    if saved and is_current(saved, status.get("last_demand_utc")) \
            and saved.get("horizon_hours") == 48:
        return saved
    st.session_state.pop("overview_demand_forecast", None)
    if not status.get("future_forecast_available"):
        return {}
    try:
        with st.spinner("Calculando el perfil previsto para la ciudad…"):
            forecast = api_client.post("/predict", {"horizon_hours": 48})
        st.session_state["overview_demand_forecast"] = forecast
        return forecast
    except requests.HTTPError as error:
        detail = error.response.text if error.response is not None else str(error)
        st.warning(f"Previsión de demanda no disponible: {detail[:320]}")
    except requests.RequestException as error:
        st.warning(f"No se pudo consultar la previsión de demanda ({type(error).__name__}).")
    return {}


def _service(status: dict) -> dict:
    """Plain-language state of the API, the model and the observed demand."""
    api_up = bool(status.get("api"))
    model = ("Modelo listo" if status.get("model_ready") else
             "Modelo cargado · datos insuficientes" if status.get("model_loaded")
             else "Modelo no disponible")
    latest = status.get("last_demand_utc")
    fresh = bool(status.get("demand_fresh"))
    source = ("REData conectada" if status.get("demand_source_status") == "connected"
              else "REData no disponible")
    freshness = "Actualizados" if fresh else "Desactualizados" if latest else "Sin observación"
    healthy = bool(api_up and status.get("model_ready") and fresh)
    return {
        "source": source, "freshness": freshness,
        "source_tone": "success" if fresh else "warning" if latest else "error",
        "state": "Operativo" if healthy else "Degradado" if api_up else "Sin conexión",
        "state_tone": "success" if healthy else "warning" if api_up else "error",
        "detail": f"{'API operativa' if api_up else 'API no disponible'} · {model}",
    }


def _price(price: dict, estimate: dict) -> dict:
    """Observed PVPC when it is current; otherwise the model estimate, labelled as such."""
    observed = price.get("latest_price_eur_mwh")
    records = estimate.get("records", [])
    if observed is not None and not price.get("stale"):
        value = number(observed / 1000, 4)
        return {"label": "PVPC observado", "value": value,
                "detail": "Indicador 1001 · último dato publicado",
                "time": f"PVPC: {local_datetime(price['latest_price_utc'])}",
                "hotspot": f"{value} €/kWh · PVPC"}
    if records and records[0].get("price_eur_mwh") is not None:
        first = records[0]
        value = number(first["price_eur_mwh"] / 1000, 4)
        when = first.get("timestamp_utc") or first["timestamp_local"]
        return {"label": "Precio estimado", "value": value,
                "detail": "Estimación del modelo · siguiente hora",
                "time": f"Modelo de precio: {local_datetime(when)}",
                "hotspot": f"{value} €/kWh · siguiente hora"}
    if price.get("stale"):
        return {"label": "PVPC desactualizado", "value": "—",
                "detail": "El dato antiguo no se muestra como actual",
                "time": f"PVPC observado: {local_datetime(price['last_observation_utc'])}",
                "hotspot": "Precio no disponible"}
    return {"label": "Precio eléctrico", "value": "—", "detail": "Fuente no disponible",
            "time": "Precio: sin dato disponible", "hotspot": "Precio no disponible"}


def _peak(frame: pd.DataFrame) -> dict:
    """Highest forecast hour within the next 24 hours."""
    if frame.empty:
        return {"value": "—", "detail": "Sin previsión disponible", "hotspot": "Sin previsión"}
    window = frame.head(24)
    row = window.loc[window.demand_mw.idxmax()]
    stamp = row.timestamp_local
    today = local_stamp(pd.Timestamp.now(tz="UTC")).date()
    day = ("Hoy" if stamp.date() == today else
           "Mañana" if (stamp.date() - today).days == 1 else short_date(stamp).capitalize())
    return {"value": number(row.demand_mw),
            "detail": f"{day} a las {stamp:%H:%M} · modelo de demanda",
            "hotspot": f"Pico {number(row.demand_mw)} MW · {stamp:%H:%M}"}


def _title_panel(service: dict) -> str:
    now = local_stamp(pd.Timestamp.now(tz="UTC"))
    chips = (chip(f"{service['source']} · {service['freshness']}", service["source_tone"])
             + chip(f"{short_date(now)} · {now:%H:%M}", None, "clock"))
    return (f'<div class="home-title glass-panel">{heading_html(TITLE, SUBTITLE, "RESUMEN")}'
            f'<div class="v-chips">{chips}</div></div>')


def _notices(status: dict, price: dict) -> None:
    latest = status.get("last_demand_utc")
    if latest and not status.get("demand_fresh"):
        st.warning(
            f"La última observación de demanda es del {local_datetime(latest)} "
            "(hora peninsular). La fuente o el dato pueden estar desactualizados; la ciudad "
            "conserva el contexto real y no lo presenta como una medición actual.")
    if status.get("source_notice"):
        st.info(status["source_notice"])
    if status and not status.get("future_forecast_available"):
        st.caption(status.get("reason") or "La previsión no está disponible con las "
                   "entradas actuales.")
    if price.get("status") == "not_configured":
        st.caption("No se ha configurado una fuente PVPC observada; la tarjeta muestra la "
                   "estimación del modelo de calendario como dato distinto.")
    elif price.get("stale"):
        st.caption("PVPC observado desactualizado desde "
                   f"{local_datetime(price.get('last_observation_utc'))}; la tarjeta "
                   "muestra la estimación del modelo, no ese dato antiguo.")
    elif price.get("status") == "unavailable":
        st.caption(f"Fuente PVPC no disponible: {price.get('error') or 'sin detalle'}. "
                   "La tarjeta de precio, si está disponible, corresponde al modelo.")


def render(datasets: dict) -> None:
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        status = {}
        st.error(f"No se puede consultar FastAPI ({type(error).__name__}).")

    forecast = _demand_forecast(status) if status else {}
    frame = prediction_frame(forecast) if forecast else pd.DataFrame()
    price = _latest_price()
    needs_estimate = (price.get("stale") or price.get("latest_price_eur_mwh") is None
                      or price.get("status") in {"not_configured", "unavailable"})
    price_info = _price(price, _price_estimate() if needs_estimate else {})
    service, peak = _service(status), _peak(frame)
    latest, demand = status.get("last_demand_utc"), status.get("last_demand_mw")

    kpis = "".join([
        metric("Demanda actual", number(demand) if demand is not None else "—", "MW",
               f"Dato real REData · {short_datetime(latest)}" if latest else "Sin observación"),
        metric("Pico previsto · 24 h", peak["value"], "MW", peak["detail"]),
        metric(price_info["label"], price_info["value"], "€/kWh", price_info["detail"]),
        metric("Estado", service["state"], "", service["detail"], service["state_tone"]),
    ])
    labels = {"Modelo": peak["hotspot"], "Precios": price_info["hotspot"],
              "Datos": f"REData · {local_stamp(latest):%H:%M}" if latest else "REData · sin dato",
              "Estado del sistema": service["state"]}
    with st.container(key="home-stage"):
        html(_title_panel(service))
        render_city(demand, forecast, labels)
        html(f'<div class="home-kpis glass-panel" role="group" aria-label="Indicadores">'
             f'{kpis}</div>')
        html(f'<div class="home-hint glass-panel">{svg("move", 14)}Arrastra para girar · '
             'pulsa un edificio para abrir su sección</div>')
        html('<p class="home-caption">La intensidad luminosa sigue la demanda agregada '
             'observada y su perfil previsto; no asigna consumo individual a edificios.</p>')

    # Below the stage: the remaining indicators and every notice about the data.
    with st.container(key="home-more"):
        demand_time = (f"Demanda real: {local_datetime(latest)}" if latest
                       else "Demanda real: sin observación")
        next_hour = number(frame.demand_mw.iloc[0]) if not frame.empty else "—"
        day_ahead = number(frame.demand_mw.iloc[23]) if len(frame) > 23 else "—"
        html(card(card_header("Más indicadores",
                              f"{demand_time} · {price_info['time']} · hora peninsular."),
                  grid([mini("Próxima hora", f"{next_hour} MW", "Salida del modelo de demanda"),
                        mini("Horizonte 24 h", f"{day_ahead} MW", "Estimación a 24 horas"),
                        mini("Fuente de demanda", service["source"], service["freshness"])],
                       3, "v-minis")))
        _notices(status, price)

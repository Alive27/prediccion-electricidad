"""Executive dashboard overview and the live-data-driven 3D city."""

import pandas as pd
import requests
import streamlit as st

from components.city_scene import render_city
from components.forecast import is_current
from components.layout import header
from services import api_client
from utils.formatting import local_datetime


def _latest_price() -> dict:
    """Return the latest observed PVPC point; never substitute a model estimate."""
    try:
        response = api_client.cached_price_history(1)
    except (requests.RequestException, ValueError):
        return {}
    records = response.get("records", [])
    if not records:
        return response
    frame = pd.DataFrame(records)
    frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True, errors="coerce")
    frame["price_eur_mwh"] = pd.to_numeric(frame.price_eur_mwh, errors="coerce")
    frame = frame.dropna(subset=["timestamp_utc", "price_eur_mwh"])
    if frame.empty:
        return response
    point = frame.sort_values("timestamp_utc").iloc[-1]
    return {
        **response,
        "latest_price_eur_mwh": float(point.price_eur_mwh),
        "latest_price_utc": point.timestamp_utc.isoformat(),
    }


def _price_estimate() -> dict:
    """Use the independent calendar model if the observed price is unavailable."""
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
    saved = st.session_state.get("overview_demand_forecast")
    latest = status.get("last_demand_utc")
    if saved and is_current(saved, latest) and saved.get("horizon_hours") == 48:
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


def render(datasets: dict) -> None:
    header(
        "Centro de control energético",
        "Demanda agregada peninsular, datos observados y modelos en una vista interactiva.",
        eyebrow="RESUMEN",
    )
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        status = {}
        st.error(f"No se puede consultar FastAPI ({type(error).__name__}).")

    forecast = _demand_forecast(status) if status else {}
    price = _latest_price()
    latest_observation = status.get("last_demand_utc")
    freshness = "Actualizados" if status.get("demand_fresh") else (
        "Desactualizados" if latest_observation else "Sin observación")
    source = status.get("demand_source_status", "unavailable")
    source_text = "REData conectada" if source == "connected" else "REData no disponible"
    if status.get("model_ready"):
        model_text = "Modelo listo"
    elif status.get("model_loaded"):
        model_text = "Modelo cargado · datos insuficientes"
    else:
        model_text = "Modelo no disponible"
    api_text = "API operativa" if status.get("api") else "API no disponible"
    metrics = {
        **status,
        "api_state": f"{api_text} · {model_text}",
        "freshness": freshness,
        "source_label": source_text,
        "latest_price_eur_mwh": price.get("latest_price_eur_mwh"),
        "latest_price_utc": price.get("latest_price_utc")
        or price.get("last_observation_utc"),
        "price_stale": price.get("stale", False),
    }
    if (price.get("stale") or price.get("latest_price_eur_mwh") is None
            or price.get("status") in {"not_configured", "unavailable"}):
        estimate = _price_estimate()
        records = estimate.get("records", [])
        if records:
            metrics["estimated_price_eur_mwh"] = records[0].get("price_eur_mwh")
            metrics["estimated_price_utc"] = records[0].get("timestamp_local")

    render_city(metrics, forecast)
    st.markdown(
        '<div class="energy-city-caption"><span>La intensidad luminosa sigue la demanda '
        'agregada observada y su perfil previsto; no asigna consumo individual a edificios.</span>'
        f'<span>{source_text} · {freshness}</span></div>',
        unsafe_allow_html=True,
    )
    if latest_observation and not status.get("demand_fresh"):
        st.warning(
            f"La última observación de demanda es del {local_datetime(latest_observation)} "
            "(hora peninsular). "
            "La fuente o el dato pueden estar desactualizados; la ciudad conserva el contexto "
            "real y no lo presenta como una medición actual."
        )
    if status.get("source_notice"):
        st.info(status["source_notice"])
    if status and not status.get("future_forecast_available"):
        st.caption(
            status.get("reason")
            or "La previsión no está disponible con las entradas actuales."
        )
    if price.get("status") == "not_configured":
        st.caption("No se ha configurado una fuente PVPC observada; la tarjeta muestra la "
                   "estimación del modelo de calendario como dato distinto.")
    elif price.get("stale"):
        st.caption(f"PVPC observado desactualizado desde {price.get('last_observation_utc')}; "
                   "la tarjeta muestra la estimación del modelo, no ese dato antiguo.")
    elif price.get("status") == "unavailable":
        st.caption(f"Fuente PVPC no disponible: {price.get('error') or 'sin detalle'}. "
                   "La tarjeta de precio, si está disponible, corresponde al modelo.")

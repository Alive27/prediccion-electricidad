"""Operational source status and concise 2024 historical coverage."""

import pandas as pd
import requests
import streamlit as st

from components.cards import dataset_status, status_row
from components.diagnostics import discard_manual
from components.layout import header, section
from components.states import dataset_ready, upload_fallback
from services import api_client
from services.data_quality import Dataset
from utils.formatting import day_label, frequency, number, timestamp


def _local_time(value: str | None) -> str:
    if not value:
        return "Sin dato"
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return timestamp(stamp, with_zone=True)


def render(datasets: dict) -> None:
    header("Datos", "Procedencia y cobertura de la demanda real e histórica")
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        status = None
        st.warning(f"FastAPI no está disponible ({type(error).__name__}).")

    section("Demanda actual", "Observaciones recientes consultadas a REData · unidad MW.")
    if status is not None:
        source, tone = ("Conectada", "success") if status.get(
            "demand_source_status") == "connected" else ("Sin conexión", "error")
        status_row("Fuente oficial REData", source, tone)
        cards = st.columns(4)
        last_mw = status.get("last_demand_mw")
        cards[0].metric("Última observación",
                        f"{number(last_mw, 0)} MW" if last_mw is not None else "Sin dato",
                        f"{_local_time(status.get('last_demand_utc'))} · "
                        f"{status.get('last_demand_age_hours', 'sin dato')} h")
        cards[1].metric("Última consulta correcta",
                        _local_time(status.get("demand_source_last_success_at_utc")),
                        "Petición REData que devolvió datos válidos")
        cards[2].metric("Actualización indicada por REData",
                        _local_time(status.get("demand_source_updated_at_utc")),
                        "Metadato last-update del widget")
        cards[3].metric("Preparación para predecir",
                        "Lista" if status.get("future_forecast_available") else "Bloqueada",
                        status.get("reason") or "Demanda y entradas requeridas disponibles")
        st.caption(f"Último intento de consulta: "
                   f"{_local_time(status.get('demand_source_checked_at_utc'))}.")
        if status.get("source_notice"):
            st.warning(status["source_notice"])
        elif not status.get("demand_fresh"):
            st.warning(status.get("reason") or "La última observación es demasiado antigua.")
    else:
        st.info("La procedencia y frescura se mostrarán al reconectar la API.")

    section("Histórico local de 2024",
            "Archivo independiente de la demanda actual de REData.")
    dataset: Dataset = datasets["demand"]
    dataset = upload_fallback(dataset)
    if not dataset_ready(dataset):
        return
    first, last = dataset.frame.timestamp.min(), dataset.frame.timestamp.max()
    state, tone = dataset_status(dataset)
    status_row("Parquet REData · demanda 2024", state, tone)
    if dataset.source == "Carga manual · solo en memoria":
        st.button("Retirar histórico temporal", key="discard_demand",
                  type="tertiary", icon=":material/close:",
                  on_click=discard_manual, args=("demand",))
    cards = st.columns(3)
    cards[0].metric("Cobertura histórica", f"{day_label(first)} — {day_label(last)}",
                    "Fechas del archivo local")
    cards[1].metric("Registros válidos", number(dataset.quality["records"]),
                    f"Frecuencia aproximada: {frequency(dataset.quality['step'])}")
    issues = dataset.quality["invalid_records"] + dataset.quality["duplicate_rows"]
    cards[2].metric("Validación", "Con avisos" if issues else "Correcta",
                    f"{number(issues)} filas excluidas o duplicadas")
    loaded_at = dataset.loaded_at.isoformat() if dataset.loaded_at else None
    st.caption(f"Leído {_local_time(loaded_at)}. "
               "Los gráficos de la pestaña Demanda y Análisis histórico usan este periodo 2024; "
               "nunca se presentan como valores actuales.")

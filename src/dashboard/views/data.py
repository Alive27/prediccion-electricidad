"""Datos: where the data comes from, its live state and the 2024 local history."""

from html import escape

import pandas as pd
import requests
import streamlit as st

from components import charts
from components.blocks import (card, card_header, chip, grid, html, icon_tile, mini, ornament)
from components.cards import dataset_status
from components.data_table import Column, table_html
from components.icons import svg
from components.layout import header
from components.states import dataset_ready, discard_manual, upload_fallback
from services import api_client
from services.data_quality import Dataset
from utils.formatting import (LOCAL_TZ, MONTHS, clock, frequency, number, short_date,
                              short_datetime)

# Source and service tiles of the data flow: icon and tile colour.
TILES = {"REData": ("tower", "#ff9500"), "Open-Meteo": ("thermo", "#ff3b30"),
         "API FastAPI": ("server", "#0a84ff"), "Modelo": ("model", "#af52de"),
         "Dashboard": ("display", "#30b0c7"), "Supabase": ("database", "#3ecf8e"),
         "Local": ("database", "#8e8e93")}


def _state(label: str, tone: str) -> str:
    return f'<span class="v-state {tone}"><i class="v-dot {tone}"></i>{escape(label)}</span>'


def _source_state(value: str | None) -> tuple[str, str]:
    return {"connected": ("Conectada", "success"), "pending": ("Pendiente", "warning")
            }.get(value or "", ("Sin conexión", "error"))


def _flow_card(status: dict | None) -> None:
    if status is None:
        nodes = [("REData", "Demanda peninsular", "Sin datos", "pending"),
                 ("Open-Meteo", "Temperatura 2 m", "Sin datos", "pending"),
                 ("API FastAPI", "Servicio de predicción", "No disponible", "error"),
                 ("Modelo", "Demanda horaria", "Sin datos", "pending"),
                 ("Dashboard", "Esta aplicación", "Operativo", "success")]
    else:
        model_state = (("Previsión lista", "success") if status.get("future_forecast_available")
                       else ("Solo simulación", "warning")
                       if status.get("historical_simulation_available") else ("Bloqueado", "error"))
        nodes = [
            ("REData", "Demanda peninsular", *_source_state(status.get("demand_source_status"))),
            ("Open-Meteo", "Temperatura 2 m", *_source_state(status.get("weather_source_status"))),
            ("API FastAPI", "Servicio de predicción",
             *(("Operativa", "success") if status.get("api") else ("No disponible", "error"))),
            ("Modelo", "Demanda horaria", *model_state),
            ("Dashboard", "Esta aplicación", "Operativo", "success"),
        ]
    arrow = f'<span class="v-flow-a">{svg("arrow", 18, 2)}</span>'
    flow = arrow.join(
        f'<div class="v-node">{icon_tile(*TILES[name])}<div><b>{escape(name)}</b>'
        f'<p>{escape(detail)}</p></div>{_state(label, tone)}</div>'
        for name, detail, label, tone in nodes)
    try:
        tracking = api_client.cached_tracking(7)
    except (requests.RequestException, ValueError):
        tracking = {}
    database = (("Conectada", "success") if tracking.get("available") else
                ("Sin conexión", "error") if tracking.get("configured") else
                ("No configurada", "pending"))
    row = (f'<div class="v-dbrow">{icon_tile(*TILES["Supabase"], "sm")}<div class="v-grow">'
           '<b>Base de datos · Supabase (PostgreSQL)</b><p>Registro de previsiones actuales '
           'para el seguimiento del modelo · las simulaciones no se guardan</p></div>'
           f'{_state(*database)}</div>')
    html(card(card_header("Flujo de datos",
                          "Estado real de cada fuente y servicio que alimenta el dashboard."),
              f'<div class="v-flow">{flow}</div>', row))


def _recent(frame: pd.DataFrame) -> None:
    """Latest observations as a table and as a 24-hour line."""
    left, right = st.columns(2)
    with left:
        rows = [[short_date(row.timestamp_local), f"{row.timestamp_local:%H:%M}",
                 (number(row.demand_mw), "strong")]
                for row in frame.iloc[::-1].head(8).itertuples()]
        html(card(card_header("Demanda actual",
                              "Observaciones recientes consultadas a REData · unidad MW."),
                  table_html([Column("Fecha", "40%"), Column("Hora", "25%"),
                              Column("Demanda · MW", "35%", True)], rows,
                             label="Demanda actual")))
    with right:
        charts.recent_line(frame, "data_last_24h", "Últimas 24 horas observadas",
                           "Demanda real · REData · hora peninsular.")


def _coverage(dataset: Dataset) -> tuple[list[str], list[float], list[str]]:
    """Monthly share of the expected hourly records present in the local Parquet."""
    step = dataset.quality.get("step") or pd.Timedelta(hours=1)
    stamps = dataset.frame.timestamp
    if stamps.dt.tz is not None:
        stamps = stamps.dt.tz_localize(None)   # months of the local (peninsular) calendar
    counts = stamps.dt.to_period("M").value_counts().sort_index()
    labels, shares, details = [], [], []
    for period, count in counts.items():
        expected = period.days_in_month * pd.Timedelta(days=1) / step
        share = min(1.0, count / expected) if expected else 0
        labels.append(MONTHS[period.month - 1][:3])
        shares.append(share * 100)
        details.append(f"{MONTHS[period.month - 1].capitalize()} {period.year}: "
                       f"{number(count)} registros ({number(share * 100, 1)} %)")
    return labels, shares, details


def _history_card(dataset: Dataset) -> None:
    first, last = dataset.frame.timestamp.min(), dataset.frame.timestamp.max()
    issues = dataset.quality["invalid_records"] + dataset.quality["duplicate_rows"]
    with st.container(key="plot-history-2024"):
        html(card_header("Histórico local de 2024",
                         "Archivo independiente de la demanda actual de REData."))
        html(grid([
            mini("Registros válidos", number(dataset.quality["records"]),
                 f"Horas registradas · frecuencia {frequency(dataset.quality['step'])}"),
            mini("Periodo local del archivo", f"{first:%d/%m/%Y} — {last:%d/%m/%Y}"),
            mini("Filas excluidas", number(issues), "Horas repetidas o inexistentes"),
            mini("Zona horaria", LOCAL_TZ, "Hora peninsular"),
        ], 4, "v-minis"))
        html('<div class="v-sub-h">Cobertura mensual</div>')
        charts.plot(charts.coverage_figure(*_coverage(dataset)), "data_coverage")


def _source_details(status: dict) -> None:
    """REData query times and freshness, and the inputs the model needs."""
    def when(field: str) -> str:
        return short_datetime(status[field]) if status.get(field) else "Sin dato"

    age, limit = status.get("last_demand_age_hours"), status.get("freshness_limit_hours") or 6
    tone = "success" if status.get("demand_fresh") else "warning"
    kv = "".join(f'<div class="kv"><span>{escape(label)}</span><strong>{escape(value)}'
                 f'</strong></div>' for label, value in (
                     ("Última consulta correcta", when("demand_source_last_success_at_utc")),
                     ("Actualización indicada por REData", when("demand_source_updated_at_utc")),
                     ("Último intento de consulta", when("demand_source_checked_at_utc")),
                     ("Antigüedad del dato", f"{number(age, 1)} h · límite {number(limit)} h"
                      if age is not None else "Sin dato")))
    checks = [("Histórico de demanda para variables", status.get("historical_features_available")),
              ("Temperatura prevista", status.get("forecast_weather_available")),
              ("Entradas completas del modelo", status.get("model_inputs_available")),
              ("Dato real dentro del límite", status.get("demand_fresh"))]
    items = "".join(f'<li class="{"ok" if ok else "ko"}"><span class="check">'
                    f'{svg("check" if ok else "close")}</span>{escape(label)}</li>'
                    for label, ok in checks)
    ready = status.get("future_forecast_available")
    reason = status.get("reason")
    left, right = st.columns(2)
    with left:
        html(card(card_header("Fuente REData", "Consultas y frescura del último dato real.",
                              chip("Dentro del límite" if tone == "success" else "Desactualizado",
                                   tone)),
                  f'<div class="kv-list">{kv}</div>'))
    with right:
        state = (("Previsión lista", "success") if ready else
                 ("Solo simulación histórica", "warning")
                 if status.get("historical_simulation_available") else ("Bloqueada", "error"))
        note = (f'<div class="info-note">{escape(reason)}</div>' if reason and not ready else "")
        html(card(card_header("Entradas del modelo de demanda",
                              "Lo que necesita una previsión actual.", chip(*state)),
                  f'<ul class="checklist">{items}</ul>{note}'))


def render(datasets: dict) -> None:
    header("Datos", "Procedencia, conexiones y cobertura de la demanda real e histórica",
           eyebrow="DATOS")
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        status = None
        st.warning(f"FastAPI no está disponible ({type(error).__name__}).")
    _flow_card(status)
    if status and status.get("source_notice"):
        st.warning(status["source_notice"])

    try:
        recent = pd.DataFrame(api_client.cached_history(24).get("records", []))
    except requests.RequestException as error:
        recent = pd.DataFrame()
        st.info(f"Demanda real reciente no disponible ({type(error).__name__}).")
    if not recent.empty:
        recent["timestamp_utc"] = pd.to_datetime(recent.timestamp_utc, utc=True)
        recent["demand_mw"] = pd.to_numeric(recent.demand_mw, errors="coerce")
        recent["timestamp_local"] = recent.timestamp_utc.dt.tz_convert(LOCAL_TZ)
        _recent(recent.dropna(subset=["demand_mw"]))

    dataset: Dataset = upload_fallback(datasets["demand"])
    if dataset_ready(dataset):
        _history_card(dataset)
        state, tone = dataset_status(dataset)
        html(card(card_header("Archivo local", f"Leído desde {dataset.source}.",
                              chip(state, tone)),
                  '<p class="v-foot">Los gráficos históricos usan el periodo 2024; nunca se '
                  'presentan como valores actuales.</p>'))
        if dataset.source == "Carga manual · solo en memoria":
            st.button("Retirar histórico temporal", key="discard_demand", type="tertiary",
                      icon=":material/close:", on_click=discard_manual, args=("demand",))
    if status:
        _source_details(status)
        checked = status.get("demand_source_checked_at_utc")
        html(ornament(f"Última comprobación · {clock(checked)}" if checked
                      else "Sin comprobación reciente", icon="clock"))

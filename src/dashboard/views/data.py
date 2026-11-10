"""Operational source status and concise 2024 historical coverage."""

from html import escape

import pandas as pd
import requests
import streamlit as st

from components.cards import dataset_status
from components.diagnostics import discard_manual
from components.icons import svg
from components.layout import header, section
from components.states import dataset_ready, upload_fallback
from services import api_client
from services.data_quality import Dataset
from utils.formatting import MONTHS, day_label, frequency, number, timestamp


def _local_time(value: str | None) -> str:
    if not value:
        return "Sin dato"
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return timestamp(stamp, with_zone=True)


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _state(label: str, tone: str) -> str:
    return f'<span class="state {tone}"><span class="dot {tone}"></span>{escape(label)}</span>'


def _pipeline(status: dict | None, dataset: Dataset) -> None:
    """Data flow from the sources to the dashboard, with the real state of each step."""
    def source_state(value: str | None) -> tuple[str, str]:
        return {"connected": ("Conectada", "success"), "pending": ("Pendiente", "warning")
                }.get(value or "", ("Sin conexión", "error"))

    if status is None:
        nodes = [("tower", "REData", "Demanda peninsular", "Sin datos", ""),
                 ("thermo", "Open-Meteo", "Temperatura 2 m", "Sin datos", ""),
                 ("server", "API FastAPI", "Servicio de predicción", "No disponible", "error"),
                 ("model", "Modelo", "Demanda horaria", "Sin datos", ""),
                 ("monitor", "Dashboard", "Esta aplicación", "Operativo", "success")]
    else:
        forecast = status.get("future_forecast_available")
        nodes = [
            ("tower", "REData", "Demanda peninsular",
             *source_state(status.get("demand_source_status"))),
            ("thermo", "Open-Meteo", "Temperatura 2 m",
             *source_state(status.get("weather_source_status"))),
            ("server", "API FastAPI", "Servicio de predicción",
             *(("Operativa", "success") if status.get("api") else ("No disponible", "error"))),
            ("model", "Modelo", "Demanda horaria",
             *(("Previsión lista", "success") if forecast else
               ("Solo simulación", "warning") if status.get("historical_simulation_available")
               else ("Bloqueado", "error"))),
            ("monitor", "Dashboard", "Esta aplicación", "Operativo", "success"),
        ]
    state, tone = dataset_status(dataset)
    items = "".join(
        f'<div class="pipe-node {tone}"><span class="pipe-icon">{svg(icon)}</span>'
        f'<div><strong>{escape(name)}</strong><small>{escape(detail)}</small></div>'
        f'{_state(label, tone)}</div>'
        for icon, name, detail, label, tone in nodes)
    local = (f'<div class="pipe-side"><span class="pipe-icon">{svg("database")}</span>'
             f'<div><strong>Histórico local 2024</strong><small>Parquet REData · '
             f'gráficos históricos</small></div>{_state(state, tone)}</div>')
    _html(f'<div class="pipeline" role="list">{items}</div>{local}')


def _freshness(status: dict) -> str:
    age = status.get("last_demand_age_hours")
    limit = status.get("freshness_limit_hours") or 6
    if age is None:
        return ""
    ratio = min(1.0, float(age) / (float(limit) * 4))
    tone = "success" if age <= limit else "warning" if age <= limit * 4 else "error"
    marker = 25.0     # the scale spans four times the freshness limit
    return (
        f'<div class="fresh"><div class="fresh-head"><span>Antigüedad del último dato</span>'
        f'<strong class="{tone}">{number(age, 1)} h</strong></div>'
        f'<div class="fresh-bar"><span class="{tone}" style="width:{ratio * 100:.1f}%"></span>'
        f'<i style="left:{marker:.1f}%" title="Límite"></i></div>'
        f'<div class="fresh-scale"><span>0 h</span><span style="left:{marker:.1f}%">'
        f'límite {number(limit)} h</span><span>{number(limit * 4)} h</span></div></div>')


def _live_panel(status: dict) -> None:
    value = status.get("last_demand_mw")
    rows = [
        ("Última consulta correcta", _local_time(status.get("demand_source_last_success_at_utc"))),
        (
            "Actualización indicada por REData",
            _local_time(status.get("demand_source_updated_at_utc")),
        ),
        ("Último intento de consulta", _local_time(status.get("demand_source_checked_at_utc"))),
    ]
    kv = "".join(f'<div class="kv"><span>{escape(k)}</span><strong>{escape(v)}</strong></div>'
                 for k, v in rows)
    big = (f'{number(value)} <span>MW</span>' if value is not None else "Sin dato")
    connected = status.get("demand_source_status") == "connected"
    source = _state("Conectada", "success") if connected else _state("Sin conexión", "error")
    _html(
        f'<div class="info-card"><div class="info-card-head"><div><small>DEMANDA EN TIEMPO REAL'
        f'</small><h3>REData · sistema peninsular</h3></div>{source}</div>'
        f'<div class="big-value">{big}</div>'
        f'<div class="big-detail">Observada {escape(_local_time(status.get("last_demand_utc")))}'
        f'</div>{_freshness(status)}<div class="kv-list">{kv}</div></div>')


def _readiness_panel(status: dict) -> None:
    checks = [
        ("Histórico de demanda para variables", status.get("historical_features_available")),
        ("Temperatura prevista", status.get("forecast_weather_available")),
        ("Entradas completas del modelo", status.get("model_inputs_available")),
        ("Dato real dentro del límite", status.get("demand_fresh")),
    ]
    items = "".join(
        f'<li class="{"ok" if ok else "ko"}"><span class="check">'
        f'{svg("check" if ok else "close")}</span>'
        f'{escape(label)}</li>' for label, ok in checks)
    ready = status.get("future_forecast_available")
    label, tone = (("Previsión lista", "success") if ready else
                   ("Solo simulación histórica", "warning")
                   if status.get("historical_simulation_available") else ("Bloqueada", "error"))
    reason = status.get("reason")
    note = f'<div class="info-note">{escape(reason)}</div>' if reason and not ready else ""
    _html(
        f'<div class="info-card"><div class="info-card-head"><div><small>PREPARACIÓN PARA PREDECIR'
        f'</small><h3>Entradas del modelo de demanda</h3></div>{_state(label, tone)}</div>'
        f'<ul class="checklist">{items}</ul>{note}</div>')


def _coverage(dataset: Dataset) -> str:
    """Monthly share of the expected hourly records present in the local Parquet."""
    frame = dataset.frame
    step = dataset.quality.get("step") or pd.Timedelta(hours=1)
    stamps = frame.timestamp
    months = stamps.dt.to_period("M")
    counts = months.value_counts().sort_index()
    bars = []
    for period, count in counts.items():
        expected = period.days_in_month * pd.Timedelta(days=1) / step
        share = min(1.0, count / expected) if expected else 0
        tone = "success" if share >= 0.98 else "warning" if share >= 0.8 else "error"
        bars.append(
            f'<div class="cov-bar" title="{MONTHS[period.month - 1].capitalize()} '
            f'{period.year}: {number(count)} registros ({number(share * 100, 1)} %)">'
            f'<span class="{tone}" style="height:{max(share, 0.04) * 100:.1f}%"></span>'
            f'<small>{MONTHS[period.month - 1][:3]}</small></div>')
    return f'<div class="coverage" role="img" aria-label="Cobertura mensual">{"".join(bars)}</div>'


def _history_panel(dataset: Dataset) -> None:
    first, last = dataset.frame.timestamp.min(), dataset.frame.timestamp.max()
    state, tone = dataset_status(dataset)
    issues = dataset.quality["invalid_records"] + dataset.quality["duplicate_rows"]
    stats = [
        ("Cobertura", f"{day_label(first)} — {day_label(last)}"),
        ("Registros válidos", number(dataset.quality["records"])),
        ("Frecuencia", frequency(dataset.quality["step"])),
        ("Filas excluidas", number(issues)),
    ]
    stat_html = "".join(f'<div class="stat"><small>{escape(k)}</small><strong>{escape(v)}'
                        f'</strong></div>' for k, v in stats)
    validation = _state("Validación correcta", "success") if not issues else \
        _state("Validación con avisos", "warning")
    loaded_at = dataset.loaded_at.isoformat() if dataset.loaded_at else None
    _html(
        f'<div class="info-card"><div class="info-card-head"><div><small>HISTÓRICO LOCAL'
        f'</small><h3>Parquet REData · demanda 2024</h3></div><div class="head-states">'
        f'{_state(state, tone)}{validation}</div></div><div class="stat-row">{stat_html}</div>'
        f'<div class="cov-title">Cobertura mensual de registros horarios</div>{_coverage(dataset)}'
        f'<div class="info-note muted">Leído {escape(_local_time(loaded_at))} desde '
        f'<code>{escape(dataset.source)}</code>. Los gráficos históricos usan este periodo 2024; '
        f'nunca se presentan como valores actuales.</div></div>')


def render(datasets: dict) -> None:
    header("Datos", "Procedencia, conexiones y cobertura de la demanda real e histórica")
    try:
        status = api_client.cached_status()
    except (requests.RequestException, ValueError) as error:
        status = None
        st.warning(f"FastAPI no está disponible ({type(error).__name__}).")
    dataset: Dataset = datasets["demand"]

    section("Flujo de datos", "Estado real de cada fuente y servicio que alimenta el dashboard.")
    _pipeline(status, dataset)

    section("Demanda actual", "Observaciones recientes consultadas a REData · unidad MW.")
    if status is not None:
        if status.get("source_notice"):
            st.warning(status["source_notice"])
        live, ready = st.columns([1.15, 1])
        with live:
            _live_panel(status)
        with ready:
            _readiness_panel(status)
    else:
        st.info("La procedencia y frescura se mostrarán al reconectar la API.")

    section("Histórico local de 2024", "Archivo independiente de la demanda actual de REData.")
    dataset = upload_fallback(dataset)
    if not dataset_ready(dataset):
        return
    _history_panel(dataset)
    if dataset.source == "Carga manual · solo en memoria":
        st.button("Retirar histórico temporal", key="discard_demand",
                  type="tertiary", icon=":material/close:",
                  on_click=discard_manual, args=("demand",))

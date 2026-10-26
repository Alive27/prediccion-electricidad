"""Forecast analysis: demand peaks, observed comparisons, prices and evaluation."""

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from components.forecast import (
    LOCAL_ZONE, is_current, prediction_frame, render_summary, render_timeline,
)
from components.layout import header, section
from services import api_client


def _utc_records(records: list[dict], value_column: str) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if frame.empty or not {"timestamp_utc", value_column}.issubset(frame.columns):
        return pd.DataFrame()
    frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True)
    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
    return frame.dropna(subset=[value_column]).sort_values("timestamp_utc")


def _error_detail(error: requests.HTTPError) -> str:
    try:
        response = error.response.json()
        return str(response.get("detail", response))[:500]
    except (ValueError, AttributeError):
        return str(error)[:500]


def _forecast_peaks(frame: pd.DataFrame) -> None:
    section("Horas punta y valle", "Ranking y extremos calculados solo con esta previsión.")
    peak_table = frame.nlargest(5, "demand_mw")[
        ["timestamp_local", "demand_mw"]].copy()
    peak_table["Periodo local"] = peak_table.timestamp_local.map(
        lambda value: value.strftime("%a %d/%m %H:%M %Z"))
    peak_table = peak_table.rename(columns={"demand_mw": "Demanda prevista · MW"})
    daily_rows = []
    for local_day, group in frame.groupby(frame.timestamp_local.dt.date, sort=True):
        high = group.loc[group.demand_mw.idxmax()]
        low = group.loc[group.demand_mw.idxmin()]
        daily_rows.append({
            "Día local": local_day.isoformat(),
            "Máximo · MW": high.demand_mw,
            "Hora máxima": high.timestamp_local.strftime("%H:%M %Z"),
            "Mínimo · MW": low.demand_mw,
            "Hora mínima": low.timestamp_local.strftime("%H:%M %Z"),
            "Rango diario · MW": high.demand_mw - low.demand_mw,
        })
    left, right = st.columns([1, 1.4])
    with left:
        st.markdown("**Cinco horas con mayor demanda prevista**")
        st.dataframe(peak_table[["Periodo local", "Demanda prevista · MW"]],
                     width="stretch", hide_index=True)
        st.caption("Un pico de demanda no implica por sí mismo un pico de precio.")
    with right:
        st.markdown("**Máximo y mínimo por día**")
        st.dataframe(pd.DataFrame(daily_rows), width="stretch", hide_index=True)

    profile = frame.assign(hour=frame.timestamp_local.dt.hour).groupby("hour").demand_mw.mean()
    figure = go.Figure(go.Bar(
        x=[f"{hour:02d}:00" for hour in profile.index], y=profile.values,
        marker_color="#c47d22", customdata=profile.index,
        hovertemplate="Hora local %{customdata:02.0f}:00<br>"
                      "Media prevista %{y:,.0f} MW<extra></extra>",
    ))
    figure.update_layout(height=280, margin={"l": 8, "r": 8, "t": 12, "b": 35},
                         paper_bgcolor="rgba(0,0,0,0)",
                         font={"family": "-apple-system, Segoe UI, sans-serif"},
                         yaxis_title="Demanda prevista · MW", xaxis_title="Hora local",
                         showlegend=False)
    st.plotly_chart(figure, width="stretch", theme=None, key="forecast_hourly_profile",
                    config={"displaylogo": False, "displayModeBar": True,
                            "scrollZoom": True, "responsive": True})

    if len(frame) >= 24 and frame.timestamp_local.dt.date.nunique() > 1:
        heat = frame.assign(
            day=frame.timestamp_local.dt.strftime("%a %d/%m"),
            hour=frame.timestamp_local.dt.hour,
        ).pivot_table(index="day", columns="hour", values="demand_mw", aggfunc="mean")
        if heat.size >= 24:
            heat = heat.reindex(columns=range(24))
            heat_figure = go.Figure(go.Heatmap(
                z=heat.to_numpy(), x=[f"{hour:02d}:00" for hour in heat.columns],
                y=heat.index, colorscale=[[0, "#f1f5fb"], [0.5, "#9ebae0"],
                                          [1, "#285fae"]],
                colorbar={"title": "MW"}, hoverongaps=False,
                hovertemplate="%{y} · %{x}<br>%{z:,.0f} MW<extra></extra>",
            ))
            heat_figure.update_layout(height=220, margin={"l": 8, "r": 8, "t": 12, "b": 30},
                                      paper_bgcolor="rgba(0,0,0,0)",
                                      xaxis_title="Hora local", yaxis_title="Día previsto")
            section("Mapa horario de la previsión",
                    "Solo se muestra con al menos 24 h y más de un día.")
            st.plotly_chart(heat_figure, width="stretch", theme=None,
                            key="forecast_heatmap",
                            config={"displaylogo": False, "displayModeBar": True,
                                    "responsive": True})


def _comparisons(forecast: dict, frame: pd.DataFrame) -> None:
    section(
        "Comparación con demanda observada",
        "Referencias REData; previsión y observación se identifican por separado.",
    )
    start = (frame.timestamp_utc.min() - pd.Timedelta(days=7)).isoformat()
    end = (frame.timestamp_utc.max() - pd.Timedelta(days=1)).isoformat()
    try:
        records = api_client.cached_history_between(start, end)["records"]
    except requests.HTTPError as error:
        st.info(f"No se pudieron cargar las referencias: {_error_detail(error)}")
        records = []
    except requests.RequestException as error:
        st.info(f"No se pudieron cargar las referencias ({type(error).__name__}).")
        records = []
    references = _utc_records(records, "demand_mw")
    if references.empty:
        st.info("REData no devolvió observaciones suficientes para el día anterior "
                "o la semana anterior.")
    else:
        by_time = dict(zip(references.timestamp_utc, references.demand_mw))
        rows = []
        for item in frame.itertuples():
            yesterday = by_time.get(item.timestamp_utc - pd.Timedelta(hours=24))
            last_week = by_time.get(item.timestamp_utc - pd.Timedelta(hours=168))
            row = {"Hora prevista local": item.timestamp_local.strftime("%d/%m %H:%M %Z"),
                   "Previsión · MW": item.demand_mw}
            for label, reference in (("Día anterior", yesterday),
                                     ("Mismo día semana anterior", last_week)):
                row[f"{label} observado · MW"] = reference
                row[f"Δ {label} · MW"] = (item.demand_mw - reference
                                          if reference is not None else None)
                row[f"Δ {label} · %"] = (
                    (item.demand_mw - reference) / reference * 100
                    if reference is not None and reference != 0 else None
                )
            rows.append(row)
        comparison = pd.DataFrame(rows)
        st.dataframe(comparison, width="stretch", hide_index=True)
        if any(
            item.timestamp_local.hour != (item.timestamp_utc - pd.Timedelta(hours=24))
            .tz_convert(LOCAL_ZONE).hour
            for item in frame.itertuples()
        ):
            st.caption("Los desplazamientos de 24/168 h se calculan en UTC. En cambios de horario "
                       "estacional, la hora local comparable puede diferir una hora.")

    st.markdown("**Elegir otro día observado**")
    local_dates = sorted(frame.timestamp_local.dt.date.unique())
    forecast_day = st.selectbox("Día de previsión", local_dates,
                                format_func=lambda value: value.strftime("%A %d/%m/%Y"),
                                key="comparison_forecast_day")
    default_reference = forecast_day - timedelta(days=7)
    reference_day = st.date_input("Día histórico de referencia", value=default_reference,
                                  min_value=date(2023, 12, 31), max_value=date.today(),
                                  key="comparison_reference_day")
    local_start = pd.Timestamp(reference_day).tz_localize(LOCAL_ZONE)
    local_end = local_start + pd.DateOffset(days=1)
    try:
        selected = api_client.cached_history_between(
            local_start.tz_convert("UTC").isoformat(),
            local_end.tz_convert("UTC").isoformat())
        historical = _utc_records(selected["records"], "demand_mw")
    except requests.HTTPError as error:
        st.info(f"No se pudo consultar ese día ({_error_detail(error)}).")
        historical = pd.DataFrame()
    except requests.RequestException as error:
        st.info(f"No se pudo consultar ese día ({type(error).__name__}).")
        historical = pd.DataFrame()
    if historical.empty:
        st.info("No hay observaciones REData para el día seleccionado.")
    else:
        historical["timestamp_local"] = historical.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
        historical = historical.loc[historical.timestamp_local.dt.date.eq(reference_day)]
        chosen_forecast = frame.loc[frame.timestamp_local.dt.date.eq(forecast_day)].copy()
        chosen_forecast["hour"] = chosen_forecast.timestamp_local.dt.hour
        historical["hour"] = historical.timestamp_local.dt.hour
        duplicate_hours = historical.hour.duplicated(keep=False)
        historical = historical.loc[~duplicate_hours]
        reference_by_hour = dict(zip(historical.hour, historical.demand_mw))
        chosen_forecast["observed_mw"] = chosen_forecast.hour.map(reference_by_hour)
        chosen_forecast["difference_mw"] = (
            chosen_forecast.demand_mw - chosen_forecast.observed_mw)
        chosen_forecast["difference_pct"] = (
            chosen_forecast.difference_mw / chosen_forecast.observed_mw * 100)
        chosen_forecast["Hora local"] = chosen_forecast.timestamp_local.map(
            lambda value: value.strftime("%H:%M %Z"))
        display = chosen_forecast.rename(columns={
            "demand_mw": "Previsión · MW", "observed_mw": "Histórico observado · MW",
            "difference_mw": "Diferencia · MW", "difference_pct": "Diferencia · %",
        })
        st.dataframe(display[["Hora local", "Previsión · MW", "Histórico observado · MW",
                              "Diferencia · MW", "Diferencia · %"]],
                     width="stretch", hide_index=True)
        st.caption(f"Comparación por hora local Europe/Madrid: previsión del "
                   f"{forecast_day:%d/%m} frente a observaciones del {reference_day:%d/%m/%Y}. "
                   "Las horas repetidas por DST se omiten para evitar emparejamientos ambiguos.")


def _price_section() -> None:
    section("Precio horario · ESIOS", "Serie complementaria; el modelo actual no predice precios.")
    try:
        result = api_client.cached_prices(8784)
    except requests.HTTPError as error:
        detail = _error_detail(error)
        if error.response is not None and error.response.status_code == 503:
            st.info("No hay un Parquet horario de precios ESIOS conectado a la aplicación. "
                    "El script `tests/probar_esios.py` requiere `ESIOS_TOKEN` para descargar "
                    "histórico; no se mostrarán precios como previsión.")
        else:
            st.info(f"Precio no disponible: {detail}")
        return
    except requests.RequestException as error:
        st.info(f"No se pudo consultar ESIOS ({type(error).__name__}).")
        return
    prices = _utc_records(result.get("records", []), "price_eur_mwh")
    if prices.empty:
        st.info("No hay valores de precio disponibles en el archivo local.")
        return
    prices["timestamp_local"] = prices.timestamp_utc.dt.tz_convert(LOCAL_ZONE)
    figure = go.Figure(go.Scatter(
        x=prices.timestamp_local.dt.tz_localize(None), y=prices.price_eur_mwh,
        mode="lines", name="Precio observado ESIOS",
        line={"color": "#7b61a8", "width": 2},
        customdata=[stamp.strftime("%d/%m/%Y %H:%M %Z") for stamp in prices.timestamp_local],
        hovertemplate="%{customdata} · Europe/Madrid<br><b>%{y:,.2f} €/MWh</b>"
                      "<extra></extra>",
    ))
    figure.update_layout(height=340, margin={"l": 8, "r": 8, "t": 12, "b": 35},
                         paper_bgcolor="rgba(0,0,0,0)", yaxis_title="Precio · €/MWh",
                         xaxis_title="Hora local · Europe/Madrid")
    st.plotly_chart(figure, width="stretch", theme=None, key="historical_esios_prices",
                    config={"displaylogo": False, "displayModeBar": True,
                            "scrollZoom": True, "responsive": True})
    maximum = prices.loc[prices.price_eur_mwh.idxmax()]
    minimum = prices.loc[prices.price_eur_mwh.idxmin()]
    st.caption(f"Observaciones históricas descargadas · {len(prices):,} filas · "
               f"máximo {maximum.price_eur_mwh:,.2f} €/MWh el "
               f"{maximum.timestamp_local:%d/%m/%Y %H:%M %Z} · mínimo "
               f"{minimum.price_eur_mwh:,.2f} €/MWh el "
               f"{minimum.timestamp_local:%d/%m/%Y %H:%M %Z}. No son precios futuros.")


def _evaluation_section() -> None:
    section("Calidad y evaluación", "Separación temporal 80/20 y comparación con baseline.")
    try:
        metrics = api_client.cached_evaluation()
    except requests.HTTPError as error:
        st.info(f"Evaluación no disponible: {_error_detail(error)}")
        return
    except requests.RequestException as error:
        st.info(f"Evaluación no disponible ({type(error).__name__}).")
        return
    cards = st.columns(4)
    cards[0].metric("MAE · un paso", f"{metrics['mae_mw']:,.1f} MW")
    cards[1].metric("RMSE · un paso", f"{metrics['rmse_mw']:,.1f} MW")
    cards[2].metric("MAE baseline", f"{metrics['baseline_mae_mw']:,.1f} MW")
    cards[3].metric("RMSE baseline", f"{metrics['baseline_rmse_mw']:,.1f} MW")
    horizon_rows = metrics.get("horizon_metrics", [])
    if horizon_rows:
        table = pd.DataFrame(horizon_rows).rename(columns={
            "horizon_hours": "Horizonte · h", "origins": "Orígenes evaluados",
            "mae_mw": "MAE · MW", "rmse_mw": "RMSE · MW",
            "baseline_mae_mw": "MAE baseline · MW",
            "baseline_rmse_mw": "RMSE baseline · MW",
            "peak_records": "Casos de demanda punta",
            "peak_mae_mw": "MAE punta · MW",
        })
        st.dataframe(table, width="stretch", hide_index=True)
        st.caption(f"Punta definida como el 10 % superior de la demanda observada "
                   f"(umbral {metrics['peak_threshold_mw']:,.0f} MW). "
                   "La serie meteorológica futura usa observaciones a posteriori; el error "
                   "no incluye la incertidumbre de la previsión del tiempo.")
    st.caption(f"Test desde {metrics['test_start_utc']} · {metrics['test_records']:,} "
               f"registros de un paso. {metrics['note']}")


def render(datasets: dict) -> None:
    header("Análisis de previsión", "Demanda peninsular · picos, referencias y evaluación")
    try:
        status = api_client.cached_status()
        model = api_client.cached_model_info()
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se puede consultar la API ({type(error).__name__}).")
        return

    horizon = st.select_slider("Horizonte solicitado", options=[6, 12, 24, 48], value=24,
                               format_func=lambda hours: f"{hours} horas",
                               key="prediction_horizon")
    if not status.get("future_forecast_available"):
        st.warning(status.get("reason") or "No hay datos actuales suficientes para predecir.")
        st.caption(f"Última demanda real UTC: {status.get('last_demand_utc') or 'sin datos'}. "
                   "Una observación histórica antigua nunca se presenta como previsión actual.")
    if st.button("Generar previsión", type="primary", icon=":material/bolt:",
                 disabled=not status.get("future_forecast_available"),
                 key="prediction_generate"):
        try:
            st.session_state["demand_forecast"] = api_client.post(
                "/predict", {"horizon_hours": horizon})
            st.session_state["demand_forecast_horizon"] = horizon
        except requests.HTTPError as error:
            st.error(f"La API no aceptó la solicitud: {_error_detail(error)}")
        except requests.RequestException as error:
            st.error(f"No se pudo solicitar la previsión ({type(error).__name__}).")

    forecast = st.session_state.get("demand_forecast")
    if forecast and is_current(forecast):
        frame = prediction_frame(forecast)
        section("Resumen ejecutivo", f"{len(frame)} horas solicitadas · "
                f"{model.get('model_type', forecast.get('model', 'modelo no identificado'))}")
        render_summary(forecast, frame)
        try:
            history = api_client.cached_history(168)["records"]
            render_timeline(history, forecast, frame, "analysis_forecast_timeline")
        except requests.RequestException as error:
            st.info(f"Histórico reciente no disponible ({type(error).__name__}).")
        _forecast_peaks(frame)
        _comparisons(forecast, frame)
    elif forecast:
        st.warning("La previsión guardada caducó. Genera otra antes de interpretar sus horas.")
    else:
        st.info("Genera una previsión para calcular resúmenes y picos "
                "desde la salida real de la API.")

    st.divider()
    _price_section()
    st.divider()
    _evaluation_section()
    st.caption("Variable meteorológica del modelo: temperatura a 2 m en Madrid. "
               "Esta única ubicación contextual no representa el tiempo de toda la Península.")

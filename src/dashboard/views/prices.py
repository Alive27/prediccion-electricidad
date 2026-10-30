"""Calendar-model price estimates; this view does not request price history."""

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from components.charts import base_figure, show
from components.layout import header, section
from services import api_client


def _frame(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True, errors="coerce")
    frame["price_eur_mwh"] = pd.to_numeric(frame.price_eur_mwh, errors="coerce")
    return frame.dropna(subset=["timestamp_utc", "price_eur_mwh"]).sort_values(
        "timestamp_utc")


def _chart(frame: pd.DataFrame, label: str, color: str, key: str,
           dash: str = "solid") -> None:
    figure = base_figure("€/MWh", 340)
    local = frame.timestamp_utc.dt.tz_convert("Europe/Madrid")
    figure.add_trace(go.Scatter(
        x=frame.timestamp_utc.dt.tz_localize(None), y=frame.price_eur_mwh,
        customdata=local.dt.strftime("%A %d/%m/%Y %H:%M %Z"), mode="lines+markers",
        name=label, line={"color": color, "width": 2.5, "dash": dash},
        hovertemplate="%{customdata}<br>" + label + " · %{y:,.2f} €/MWh<extra></extra>"))
    figure.update_layout(showlegend=True, hovermode="x unified")
    figure.update_xaxes(title="Hora peninsular", fixedrange=False)
    figure.update_yaxes(fixedrange=False, tickformat=",.2f")
    show(figure, key)


def _table(frame: pd.DataFrame, label: str) -> None:
    table = frame.copy()
    table["Fecha y hora (Europe/Madrid)"] = table.timestamp_utc.dt.tz_convert(
        "Europe/Madrid").map(lambda stamp: stamp.strftime("%d/%m/%Y %H:%M %Z"))
    table = table.rename(columns={"price_eur_mwh": label})
    st.dataframe(table[["Fecha y hora (Europe/Madrid)", label]],
                 width="stretch", hide_index=True)


def render(datasets: dict) -> None:
    header("Predicción de precios", "Estimaciones horarias del modelo entrenado")
    section("Estimaciones del modelo", "RandomForest · hora, día de semana, mes y día del mes")
    horizon = st.selectbox("Horizonte de predicción", [6, 12, 24, 48], index=2,
                           format_func=lambda value: f"{value} horas", key="pvpc_horizon")
    st.caption(
        "Zona horaria futura: Europe/Madrid. El notebook no registra explícitamente "
        "la zona horaria del índice de entrenamiento."
    )
    horizon_key = st.session_state.get("price_forecast_horizon")
    forecast = st.session_state.get("price_forecast")
    if forecast is None or horizon_key != horizon:
        try:
            with st.spinner("Calculando precios con el modelo existente…"):
                forecast = api_client.predict_price(horizon)
            st.session_state["price_forecast"] = forecast
            st.session_state["price_forecast_horizon"] = horizon
        except requests.HTTPError as error:
            detail = (
                error.response.json().get("detail", str(error))
                if error.response else str(error)
            )
            st.error(f"No se pudo cargar o ejecutar el modelo de precios: {detail}")
            return
        except (requests.RequestException, ValueError, KeyError) as error:
            st.error(f"No se pudo ejecutar el modelo de precios ({type(error).__name__}).")
            return

    predicted = _frame(forecast.get("records", []))
    if len(predicted) != horizon:
        st.error("El modelo no devolvió el número esperado de predicciones horarias.")
        return
    prices = predicted.price_eur_mwh
    st.info("Estimaciones del modelo; no son precios observados ni una publicación oficial.")
    st.caption(f"Horizonte: {horizon} horas · última ejecución: "
               f"{forecast.get('generated_at_utc', 'sin fecha')} · unidad: €/MWh")
    metrics = st.columns(4)
    metrics[0].metric("Siguiente hora", f"{prices.iloc[0]:,.2f} €/MWh")
    metrics[1].metric("Media prevista", f"{prices.mean():,.2f} €/MWh")
    metrics[2].metric("Máximo previsto", f"{prices.max():,.2f} €/MWh")
    metrics[3].metric("Mínimo previsto", f"{prices.min():,.2f} €/MWh")
    _chart(predicted, "Estimación del modelo", "#8b5cf6", "pvpc_model_estimate", "dash")
    _table(predicted, "Estimación del modelo (€/MWh)")

"""Live predictions and retrospective evaluation through the FastAPI client."""

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from components.charts import base_figure, show
from components.layout import header, section
from services import api_client


def render(datasets: dict) -> None:
    header("Predicción de demanda", "Previsión futura validada o evaluación retrospectiva")
    try:
        status = api_client.cached_status()
        model = api_client.cached_model_info()
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se puede conectar con la API ({type(error).__name__}).")
        st.caption(f"API configurada: {api_client.BASE_URL}")
        return
    st.caption(f"Artefacto activo: {model.get('artifact_version', 'no identificado')} · "
               f"{model.get('model_type', 'modelo no disponible')}")

    if not status.get("future_forecast_available"):
        last = status.get("last_demand_utc") or "sin datos disponibles"
        st.warning("La previsión futura está desactivada: los datos de demanda no son "
                   "suficientemente recientes.")
        st.caption(f"Último dato real de demanda (UTC): {last}. "
                   "La salida del modelo no se presentará como previsión actual.")

    horizon = st.select_slider("Horizonte de previsión", options=[6, 12, 24, 48], value=24,
                               format_func=lambda value: f"{value} horas")
    if st.button("Generar previsión", type="primary", icon=":material/bolt:",
                 disabled=not status.get("future_forecast_available")):
        try:
            st.session_state["demand_forecast"] = api_client.post(
                "/predict", {"horizon_hours": horizon})
        except requests.HTTPError as error:
            detail = error.response.text if error.response is not None else str(error)
            st.error(f"La API rechazó la previsión: {detail[:600]}")
        except requests.RequestException as error:
            st.error(f"No se pudo completar la solicitud ({type(error).__name__}).")

    forecast = st.session_state.get("demand_forecast")
    if forecast:
        records = forecast["predictions"]
        predicted = pd.DataFrame(records)
        predicted["timestamp_utc"] = pd.to_datetime(predicted.timestamp_utc, utc=True)
        history = api_client.cached_history(168)["records"]
        observed = pd.DataFrame(history)
        if not observed.empty:
            observed["timestamp_utc"] = pd.to_datetime(observed.timestamp_utc, utc=True)
        figure = base_figure("MW", 360)
        if not observed.empty:
            figure.add_trace(go.Scatter(x=observed.timestamp_utc, y=observed.demand_mw,
                                        mode="lines", name="Demanda real",
                                        line={"color": "#4385e5", "width": 2.4}))
        figure.add_trace(go.Scatter(x=predicted.timestamp_utc, y=predicted.demand_mw,
                                    mode="lines+markers", name="Previsión",
                                    line={"color": "#c47d22", "width": 2.6,
                                          "dash": "dash"}))
        figure.update_layout(showlegend=True, legend={"orientation": "h", "y": 1.12})
        section("Demanda real y previsión",
                "La línea futura se muestra tras validar datos actuales.")
        show(figure, "forecast_demand")
        st.dataframe(predicted.rename(columns={"timestamp_utc": "Fecha UTC",
                                               "demand_mw": "Demanda prevista (MW)"}),
                     width="stretch", hide_index=True)
        st.caption(f"Origen: {forecast['forecast_origin_utc']} · Generada: "
                   f"{forecast['generated_at_utc']} · Modelo: {forecast['model']}")

    st.divider()
    section("Evaluación retrospectiva",
            "Prueba cronológica histórica; no es una previsión en vivo.")
    try:
        metrics = api_client.cached_evaluation()
        cols = st.columns(4)
        for column, label, key in zip(cols, ("MAE modelo", "RMSE modelo", "MAE baseline",
                                             "RMSE baseline"),
                                      ("mae_mw", "rmse_mw", "baseline_mae_mw",
                                       "baseline_rmse_mw")):
            column.metric(label, f"{metrics[key]:,.1f} MW")
        st.caption(f"{metrics['type']} · prueba desde {metrics['test_start_utc']} · "
                   f"{metrics['test_records']:,} registros. {metrics['note']}")
    except (requests.RequestException, ValueError, KeyError) as error:
        st.info(f"Métricas históricas no disponibles ({type(error).__name__}).")

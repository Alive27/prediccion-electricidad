"""FastAPI entry point for the existing demand model."""

from contextlib import asynccontextmanager
from datetime import datetime
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from service import (DataUnavailable, DemandService, ModelUnavailable,
                     load_model)


MODEL_PATH = Path(os.getenv("MODEL_PATH", "/models/modelo_demanda_v01.joblib"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data/raw"))
FRESHNESS_HOURS = int(os.getenv("MAX_DEMAND_AGE_HOURS", "6"))
LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Curves can be inspected even if the ML artifact fails validation/loading.
    app.state.curve_service = DemandService(None, {}, DATA_DIR)
    try:
        model, model_info = load_model(MODEL_PATH)
        app.state.service = DemandService(model, model_info, DATA_DIR,
                                          FRESHNESS_HOURS)
        app.state.curve_service = app.state.service
    except Exception as error:
        app.state.service = None
        app.state.model_error = (
            f"No se pudo cargar el modelo ({type(error).__name__}): {error}")
        LOGGER.exception("No se pudo cargar o validar el artefacto de demanda.")
    yield


app = FastAPI(title="Predicción de demanda eléctrica", version="0.1.0",
              lifespan=lifespan)


class PredictionRequest(BaseModel):
    horizon_hours: Literal[5, 12, 24, 48]
    allow_stale_simulation: bool = False


def get_service() -> DemandService:
    service = getattr(app.state, "service", None)
    if service is None:
        raise HTTPException(status_code=503, detail={
            "message": "El modelo no está disponible.",
            "reason": getattr(app.state, "model_error", "No se pudo cargar el modelo."),
        })
    return service


@app.get("/health")
def health():
    """Liveness probe: reports the process without calling external sources."""
    service = getattr(app.state, "service", None)
    loaded = service is not None
    return {
        "status": "alive", "api": True, "model_loaded": loaded,
        "model_schema_valid": loaded,
        "reason": None if loaded else getattr(
            app.state, "model_error", "Modelo no disponible."),
    }


@app.get("/ready")
def readiness(refresh: bool = False):
    """Readiness probe checks the model contract and current external inputs."""
    service = getattr(app.state, "service", None)
    if service is None:
        status = {
            "status": "degraded", "api": True, "model_loaded": False,
            "model_schema_valid": False, "data_available": False,
            "model_ready": False, "historical_simulation_available": False,
            "demand_source_status": "unavailable", "weather_source_status": "unavailable",
            "demand_fresh": False, "historical_features_available": False,
            "forecast_weather_available": False, "model_inputs_available": False,
            "future_forecast_available": False,
            "reason": getattr(app.state, "model_error", "Modelo no disponible."),
        }
    else:
        if refresh:
            service.refresh_sources()
        status = service.health()
    if not status.get("model_loaded") or not status.get("future_forecast_available"):
        raise HTTPException(status_code=503, detail=status)
    return status


@app.get("/model/info")
def model_info():
    return get_service().model_info


@app.get("/history")
def history(limit: int = Query(default=168, ge=1, le=8784),
            start: datetime | None = None, end: datetime | None = None):
    try:
        return get_service().history(limit, start, end)
    except DataUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/curves")
def official_curves(hours: int = Query(default=168, ge=1, le=720)):
    """Official ESIOS curves, with per-series status and real sample cadence."""
    return app.state.curve_service.esios_curves(hours)


@app.get("/comparison")
def compare_sources(hours: int = Query(default=48, ge=1, le=720)):
    """Exact timestamp comparison between REData hourly and ESIOS measured values."""
    return app.state.curve_service.compare_demand_sources(hours)


@app.post("/predict")
def predict(request: PredictionRequest):
    try:
        return get_service().predict(request.horizon_hours,
                                     request.allow_stale_simulation)
    except DataUnavailable as error:
        raise HTTPException(status_code=503, detail={
            "message": "No se puede generar una previsión futura válida.",
            "reason": str(error),
        }) from error
    except ModelUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/evaluation")
def evaluation():
    try:
        return get_service().evaluation()
    except DataUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

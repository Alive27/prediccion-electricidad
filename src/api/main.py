"""FastAPI entry point for the existing demand model."""

from contextlib import asynccontextmanager
from datetime import datetime
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from service import (DataUnavailable, DemandService, ModelUnavailable,
                     load_model)


MODEL_PATH = Path(os.getenv("MODEL_PATH", "/models/modelo_demanda_v01.joblib"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data/raw"))
FRESHNESS_HOURS = int(os.getenv("MAX_DEMAND_AGE_HOURS", "6"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        model, model_info = load_model(MODEL_PATH)
        app.state.service = DemandService(model, model_info, DATA_DIR,
                                          FRESHNESS_HOURS)
    except Exception as error:
        app.state.service = None
        app.state.model_error = f"{type(error).__name__}: {error}"
    yield


app = FastAPI(title="Predicción de demanda eléctrica", version="0.1.0",
              lifespan=lifespan)


class PredictionRequest(BaseModel):
    horizon_hours: int = Field(ge=1, le=48)


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
    service = getattr(app.state, "service", None)
    if service is None:
        return {"status": "degraded", "api": True, "model_loaded": False,
                "data_available": False, "last_demand_utc": None,
                "future_forecast_available": False,
                "reason": getattr(app.state, "model_error", "Modelo no disponible.")}
    return service.health()


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


@app.post("/predict")
def predict(request: PredictionRequest):
    try:
        return get_service().predict(request.horizon_hours)
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


@app.get("/prices")
def prices(limit: int = Query(default=168, ge=1, le=8784),
           start: datetime | None = None, end: datetime | None = None):
    try:
        return get_service().price_history(limit, start, end)
    except DataUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

"""Loading and validation of the trained model artifacts (joblib)."""

import os
from pathlib import Path
from typing import Any

from features import FEATURES, PRICE_FEATURES


def _validate_schema(model, expected: list[str], label: str) -> None:
    names = getattr(model, "feature_names_in_", None)
    if names is not None and list(names) != expected:
        raise ValueError(f"Esquema de {label} incompatible: {list(names)}")
    count = getattr(model, "n_features_in_", len(expected))
    if int(count) != len(expected):
        raise ValueError(f"El modelo de {label} espera {count} variables, no {len(expected)}.")


def _limit_threads(model) -> None:
    if hasattr(model, "n_jobs"):
        model.n_jobs = int(os.getenv("MODEL_N_JOBS", "1"))


def load_model(path: Path) -> tuple[Any, dict[str, Any]]:
    """Load the trusted local demand artifact and verify its estimator metadata."""
    import joblib
    import sklearn

    if not path.is_file():
        raise FileNotFoundError(f"No se encuentra el artefacto en {path}.")
    artifact = joblib.load(path)
    model = artifact.get("modelo") if isinstance(artifact, dict) else artifact
    if model is None or not callable(getattr(model, "predict", None)):
        raise TypeError("El artefacto no contiene un estimador predictivo.")
    _validate_schema(model, FEATURES, "demanda")
    _limit_threads(model)
    return model, {"loaded": True, "model_type": type(model).__name__,
                   "features": FEATURES, "artifact": path.name,
                   "artifact_version": "modelo_demanda_v01",
                   "scikit_learn_version": sklearn.__version__}


def load_price_model(path: Path) -> tuple[Any, dict[str, Any]]:
    """Load and validate the existing calendar-only PVPC estimator."""
    import joblib

    if not path.is_file():
        raise FileNotFoundError(f"No se encuentra el modelo de precios en {path}.")
    model = joblib.load(path)
    if not callable(getattr(model, "predict", None)):
        raise TypeError("El artefacto de precios no contiene predict().")
    _validate_schema(model, PRICE_FEATURES, "precios")
    _limit_threads(model)
    return model, {
        "model_type": type(model).__name__, "artifact": path.name,
        "target": "precio", "unit": "€/MWh", "frequency": "1h",
        "features": PRICE_FEATURES,
        "timezone_convention": (
            "Europe/Madrid (asunción explícita; el notebook no declara la zona del índice)"),
    }

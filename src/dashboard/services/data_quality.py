"""Validate schemas; preserve source data and explicit timezone information."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from utils.formatting import LOCAL_TZ
from utils.paths import FILENAMES


SCHEMAS = {"demand": ("datetime", "value"), "weather": ("time", "temperature_2m")}
LABELS = {"demand": "REData · Demanda", "weather": "Open-Meteo · Madrid"}


@dataclass
class Dataset:
    kind: str
    source: str = ""
    checked_paths: tuple[str, ...] = ()
    raw: pd.DataFrame = field(default_factory=pd.DataFrame)
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    quality: dict = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    loaded_at: datetime | None = None

    @property
    def available(self) -> bool:
        return not self.frame.empty

    @property
    def filename(self) -> str:
        return FILENAMES[self.kind]

    @property
    def label(self) -> str:
        return LABELS[self.kind]

    @property
    def time_label(self) -> str:
        return "Europe/Madrid" if self.quality.get("timezone") else "Zona horaria sin declarar"


def prepare(kind: str, raw: pd.DataFrame, source: str) -> Dataset:
    dataset = Dataset(kind=kind, raw=raw, source=source,
                      loaded_at=datetime.now(timezone.utc))
    time_column, value_column = SCHEMAS[kind]
    if not {time_column, value_column}.issubset(raw.columns):
        dataset.errors = (
            f"El archivo debe incluir las columnas {time_column} y {value_column}.",
        )
        return dataset
    if raw.empty:
        dataset.errors = ("El archivo no contiene registros.",)
        return dataset
    try:
        times = pd.to_datetime(raw[time_column], errors="coerce")
        tz = times.dt.tz
    except (ValueError, TypeError, AttributeError):
        dataset.errors = (
            "Las fechas tienen formatos o zonas horarias incompatibles. Revisa el archivo.",
        )
        return dataset
    values = pd.to_numeric(raw[value_column], errors="coerce")
    valid = times.notna() & np.isfinite(values)
    if kind == "demand":
        valid &= values.ge(0)
    display_times = times.dt.tz_convert(LOCAL_TZ) if tz is not None else times
    frame = pd.DataFrame({"timestamp": display_times, "value": values,
                          "source_row": np.arange(len(raw))})
    dataset.frame = frame.loc[valid].sort_values("timestamp", kind="stable").reset_index(drop=True)
    unique_times = times.dropna().drop_duplicates().sort_values()
    deltas = unique_times.diff().dropna()
    step = deltas.mode().iloc[0] if not deltas.empty else None
    missing_slots = None
    if step is not None and step > pd.Timedelta(0):
        if ((deltas / step) % 1 == 0).all():
            missing_slots = int((deltas / step - 1).sum())
    dataset.quality = {
        "records": len(raw),
        "columns": {str(col): str(dtype) for col, dtype in raw.dtypes.items()},
        "nulls": {str(col): int(count) for col, count in raw.isna().sum().items()},
        "duplicate_rows": int(raw.duplicated().sum()),
        "duplicate_times": int(times.dropna().duplicated().sum()),
        "invalid_records": int((~valid).sum()),
        "timezone": str(tz) if tz is not None else None,
        "start": times.min(), "end": times.max(), "step": step,
        "missing_slots": missing_slots,
    }
    warnings = []
    if tz is None:
        warnings.append("Zona horaria no declarada: se conserva la hora original sin desplazarla.")
    if dataset.quality["invalid_records"]:
        warnings.append("Hay registros no válidos; se conservan en la tabla original y se "
                        "excluyen de los gráficos y cálculos.")
    if dataset.quality["duplicate_times"]:
        warnings.append("Hay fechas duplicadas; el análisis conjunto queda deshabilitado.")
    if missing_slots:
        warnings.append("Hay huecos en la serie temporal; no se han rellenado.")
    dataset.warnings = tuple(warnings)
    if dataset.frame.empty:
        dataset.errors = ("No hay fechas y valores válidos para analizar.",)
    return dataset


def aligned_data(demand: Dataset, weather: Dataset) -> tuple[pd.DataFrame, str]:
    """Join only unique, timezone-aware instants, with one-to-one validation."""
    pending = ("El análisis conjunto temperatura-demanda está pendiente de validar "
               "la alineación temporal de ambos datasets.")
    if not demand.available or not weather.available:
        return pd.DataFrame(), "Se necesitan ambos históricos para el análisis conjunto."
    for dataset in (demand, weather):
        if not dataset.quality.get("timezone") or dataset.quality.get("duplicate_times"):
            return pd.DataFrame(), pending
    left = demand.frame.assign(timestamp=demand.frame.timestamp.dt.tz_convert("UTC"))
    right = weather.frame.assign(timestamp=weather.frame.timestamp.dt.tz_convert("UTC"))
    joined = left.merge(right, on="timestamp", suffixes=("_demand", "_weather"),
                        validate="one_to_one")
    if joined.empty:
        return joined, "Los datasets no tienen instantes comunes; no se desplazan las fechas."
    joined["timestamp"] = joined.timestamp.dt.tz_convert(LOCAL_TZ)
    return joined, "Unión exacta de instantes UTC, sin interpolación ni desplazamientos."

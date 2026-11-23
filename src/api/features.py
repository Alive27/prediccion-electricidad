"""Input contracts of the two models and how their feature rows are built."""

import numpy as np
import pandas as pd

from common import DataUnavailable


# Column order must match the trained artifacts exactly.
FEATURES = ["hora", "dia_semana", "mes", "temperature_2m",
            "demanda_lag_1", "demanda_lag_24", "demanda_lag_168", "media_24h"]
PRICE_FEATURES = ["hora", "dia_semana", "mes", "dia_mes"]
HISTORY_HOURS = 168


def prepare_features(demand: pd.Series, timestamps: pd.DatetimeIndex,
                     temperatures: pd.Series) -> pd.DataFrame:
    """Reproduce the training notebook's lag and rolling-window definitions."""
    values = pd.Series(demand, dtype="float64").reset_index(drop=True)
    times = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))
    temps = pd.Series(temperatures, dtype="float64").reset_index(drop=True)
    if len(values) < HISTORY_HOURS or len(times) != len(values) or len(temps) != len(values):
        raise DataUnavailable("Se requieren 168 horas consecutivas de demanda y temperatura.")
    if not times.is_monotonic_increasing or times.has_duplicates:
        raise DataUnavailable("La serie horaria no está ordenada o contiene duplicados.")
    intervals = times.to_series().diff().dropna()
    if not intervals.eq(pd.Timedelta(hours=1)).all():
        raise DataUnavailable("Hay huecos en la serie; se requieren horas consecutivas.")
    if not np.isfinite(values).all() or not np.isfinite(temps).all():
        raise DataUnavailable("Hay valores nulos o no finitos en los datos de entrada.")
    stamp = times[-1]
    return pd.DataFrame([{
        "hora": stamp.hour, "dia_semana": stamp.day_of_week, "mes": stamp.month,
        "temperature_2m": temps.iloc[-1], "demanda_lag_1": values.iloc[-1],
        "demanda_lag_24": values.iloc[-24], "demanda_lag_168": values.iloc[-HISTORY_HOURS],
        "media_24h": values.iloc[-24:].mean(),
    }], columns=FEATURES)


def price_features(stamps: pd.DatetimeIndex) -> pd.DataFrame:
    """Calendar features of the price model for local (Europe/Madrid) hours."""
    return pd.DataFrame({"hora": stamps.hour, "dia_semana": stamps.dayofweek,
                         "mes": stamps.month, "dia_mes": stamps.day}, columns=PRICE_FEATURES)

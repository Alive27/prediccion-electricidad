"""Retrospective evaluation of the demand model on the local 2024 history."""

import numpy as np
import pandas as pd

from common import DataUnavailable
from features import FEATURES, HISTORY_HOURS, prepare_features


TRAIN_FRACTION = 0.8
HORIZONS = (1, 6, 12, 24, 48)
# Daily-spaced origins keep the recursive backtest representative while bounding the
# cost of 48 sequential RandomForest predictions per origin.
ORIGIN_STRIDE = 48
NOTE = ("Los lags usan solo demanda observada hasta el origen y después predicciones "
        "recursivas. La temperatura futura es observada a posteriori; estas métricas no "
        "incluyen el error meteorológico ni representan una previsión operativa actual.")


def _mae(actual, predicted) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def _rmse(actual, predicted) -> float:
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def _training_frame(demand: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Rows with the same lag definitions used to train the model, plus the next-hour target."""
    joined = demand.merge(weather, on="timestamp_utc", how="inner").sort_values(
        "timestamp_utc").reset_index(drop=True)
    joined["hora"] = joined.timestamp_utc.dt.hour
    joined["dia_semana"] = joined.timestamp_utc.dt.day_of_week
    joined["mes"] = joined.timestamp_utc.dt.month
    joined["demanda_lag_1"] = joined.value
    joined["demanda_lag_24"] = joined.value.shift(23)
    joined["demanda_lag_168"] = joined.value.shift(167)
    joined["media_24h"] = joined.value.rolling(24).mean()
    joined["objetivo"] = joined.value.shift(-1)
    return joined.dropna().reset_index(drop=True)


def evaluate(model, demand: pd.DataFrame, weather: pd.DataFrame) -> dict:
    """One-step metrics on the last 20 % of 2024 and recursive metrics per horizon."""
    if demand.empty or weather.empty:
        raise DataUnavailable("La evaluación requiere los dos Parquet históricos locales.")
    joined = _training_frame(demand, weather)
    split = int(len(joined) * TRAIN_FRACTION)
    test = joined.iloc[split:]
    predicted = np.asarray(model.predict(test[FEATURES]), dtype=float)
    actual = test.objetivo.to_numpy(dtype=float)
    baseline = test.demanda_lag_1.to_numpy(dtype=float)
    return {
        "type": "evaluación retrospectiva temporal",
        "train_fraction": TRAIN_FRACTION, "test_records": len(test),
        "test_start_utc": test.timestamp_utc.iloc[0].isoformat(),
        "mae_mw": _mae(actual, predicted), "rmse_mw": _rmse(actual, predicted),
        "baseline_mae_mw": _mae(actual, baseline), "baseline_rmse_mw": _rmse(actual, baseline),
        "horizon_metrics": _horizon_metrics(model, joined, split),
        "peak_threshold_mw": float(np.quantile(actual, 0.9)),
        "note": NOTE,
    }


def _recursive_forecast(model, joined: pd.DataFrame, origin: int, temperatures: dict,
                        steps: int) -> list[float]:
    """Forecast `steps` hours from `origin`, feeding each prediction back as a lag."""
    first = origin - (HISTORY_HOURS - 1)
    times = pd.DatetimeIndex(joined.timestamp_utc.iloc[first:origin + 1])
    values = joined.value.iloc[first:origin + 1].astype(float).tolist()
    meteo = joined.temperature_2m.iloc[first:origin + 1].astype(float).tolist()
    predictions = []
    for _ in range(steps):
        features = prepare_features(pd.Series(values[-HISTORY_HOURS:]),
                                    pd.DatetimeIndex(times[-HISTORY_HOURS:]),
                                    pd.Series(meteo[-HISTORY_HOURS:]))
        result = float(np.asarray(model.predict(features)).reshape(-1)[0])
        target_time = times[-1] + pd.Timedelta(hours=1)
        target_temperature = temperatures.get(target_time)
        if target_temperature is None or not np.isfinite(result):
            break
        predictions.append(result)
        times = times.append(pd.DatetimeIndex([target_time]))
        values.append(result)
        meteo.append(float(target_temperature))
    return predictions


def _horizon_metrics(model, joined: pd.DataFrame, split: int) -> list[dict]:
    maximum = max(HORIZONS)
    temperatures = dict(zip(joined.timestamp_utc, joined.temperature_2m))
    threshold = float(np.quantile(joined.objetivo.iloc[split:].dropna(), 0.9))
    buckets = {hour: {"actual": [], "predicted": [], "baseline": [], "peak": []}
               for hour in HORIZONS}
    for origin in range(split, len(joined) - maximum, ORIGIN_STRIDE):
        predictions = _recursive_forecast(model, joined, origin, temperatures, maximum)
        if len(predictions) < maximum:
            continue
        origin_value = float(joined.value.iloc[origin])
        for hour in HORIZONS:
            observed = float(joined.value.iloc[origin + hour])
            bucket = buckets[hour]
            bucket["actual"].append(observed)
            bucket["predicted"].append(predictions[hour - 1])
            bucket["baseline"].append(origin_value)
            if observed >= threshold:
                bucket["peak"].append((observed, predictions[hour - 1]))

    metrics = []
    for hour in HORIZONS:
        bucket = buckets[hour]
        actual = np.asarray(bucket["actual"], dtype=float)
        if not len(actual):
            continue
        predicted = np.asarray(bucket["predicted"], dtype=float)
        baseline = np.asarray(bucket["baseline"], dtype=float)
        peaks = np.asarray(bucket["peak"], dtype=float).reshape(-1, 2)
        metrics.append({
            "horizon_hours": hour, "origins": int(len(actual)),
            "mae_mw": _mae(actual, predicted), "rmse_mw": _rmse(actual, predicted),
            "baseline_mae_mw": _mae(actual, baseline),
            "baseline_rmse_mw": _rmse(actual, baseline),
            "peak_records": int(len(peaks)),
            "peak_mae_mw": _mae(peaks[:, 0], peaks[:, 1]) if len(peaks) else None,
        })
    return metrics

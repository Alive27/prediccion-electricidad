"""Model loading, data acquisition and feature preparation."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests


FEATURES = ["hora", "dia_semana", "mes", "temperature_2m",
            "demanda_lag_1", "demanda_lag_24", "demanda_lag_168", "media_24h"]
REE_URL = "https://apidatos.ree.es/es/datos/demanda/demanda-tiempo-real"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
UTC = timezone.utc


class DataUnavailable(RuntimeError):
    pass


class ModelUnavailable(RuntimeError):
    pass


def load_model(path: Path) -> tuple[Any, dict[str, Any]]:
    """Load the trusted local artifact and verify its estimator metadata."""
    import joblib

    if not path.is_file():
        raise FileNotFoundError(f"No se encuentra el artefacto en {path}.")
    artifact = joblib.load(path)
    model = artifact.get("modelo") if isinstance(artifact, dict) else artifact
    if model is None or not callable(getattr(model, "predict", None)):
        raise TypeError("El artefacto no contiene un estimador predictivo.")
    names = getattr(model, "feature_names_in_", None)
    if names is not None and list(names) != FEATURES:
        raise ValueError(f"Orden de variables incompatible: {list(names)}")
    count = getattr(model, "n_features_in_", len(FEATURES))
    if int(count) != len(FEATURES):
        raise ValueError(f"El modelo espera {count} variables, no {len(FEATURES)}.")
    if hasattr(model, "n_jobs"):
        model.n_jobs = int(os.getenv("MODEL_N_JOBS", "1"))
    import sklearn

    info = {"loaded": True, "model_type": type(model).__name__,
            "features": FEATURES, "artifact": path.name,
            "artifact_version": "modelo_demanda_v01",
            "scikit_learn_version": sklearn.__version__}
    return model, info


def read_local(path: Path, kind: str) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    timestamp = "datetime" if kind == "demand" else "time"
    value = "value" if kind == "demand" else "temperature_2m"
    if timestamp not in frame or value not in frame:
        raise DataUnavailable(f"{path.name} no contiene {timestamp} y {value}.")
    return pd.DataFrame({"timestamp_utc": pd.to_datetime(frame[timestamp], utc=True),
                         value: pd.to_numeric(frame[value], errors="coerce")})


def prepare_features(demand: pd.Series, timestamps: pd.DatetimeIndex,
                     temperatures: pd.Series) -> pd.DataFrame:
    """Reproduce the training notebook's lag and rolling-window definitions."""
    values = pd.Series(demand, dtype="float64").reset_index(drop=True)
    times = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))
    temps = pd.Series(temperatures, dtype="float64").reset_index(drop=True)
    if len(values) < 168 or len(times) != len(values) or len(temps) != len(values):
        raise DataUnavailable("Se requieren 168 horas consecutivas de demanda y temperatura.")
    if not times.is_monotonic_increasing or times.has_duplicates:
        raise DataUnavailable("La serie horaria no está ordenada o contiene duplicados.")
    intervals = times.to_series().diff().dropna()
    if not intervals.eq(pd.Timedelta(hours=1)).all():
        raise DataUnavailable("Hay huecos en la serie; se requieren horas consecutivas.")
    if not np.isfinite(values).all() or not np.isfinite(temps).all():
        raise DataUnavailable("Hay valores nulos o no finitos en los datos de entrada.")
    stamp = times[-1]
    row = pd.DataFrame([{
        "hora": stamp.hour, "dia_semana": stamp.day_of_week, "mes": stamp.month,
        "temperature_2m": temps.iloc[-1], "demanda_lag_1": values.iloc[-1],
        "demanda_lag_24": values.iloc[-24], "demanda_lag_168": values.iloc[-168],
        "media_24h": values.iloc[-24:].mean(),
    }], columns=FEATURES)
    return row


class DemandService:
    def __init__(self, model, model_info, data_dir: Path, freshness_hours: int = 6):
        self.model = model
        self.model_info = model_info
        self.data_dir = data_dir
        self.freshness_hours = freshness_hours
        self.session = requests.Session()
        self.demand_source = "histórico local"
        self._demand_cache = None
        self._weather_cache = None
        self._demand_cached_at = None
        self._weather_cached_at = None
        self._evaluation_cache = None
        self._evaluation_cached_at = None

    def _cache_is_fresh(self, timestamp) -> bool:
        return timestamp is not None and datetime.now(UTC) - timestamp < timedelta(minutes=5)

    def local_demand(self) -> pd.DataFrame:
        return read_local(self.data_dir / "redata_demanda_2024.parquet", "demand")

    def local_weather(self) -> pd.DataFrame:
        return read_local(self.data_dir / "openmeteo_madrid_2024.parquet", "weather")

    def price_history(self, limit: int, start: str | None, end: str | None):
        path = self.data_dir / "esios_precio_2023_2025.parquet"
        if not path.is_file():
            raise DataUnavailable(
                "No existe el histórico local de precios ESIOS (esios_precio_2023_2025.parquet). "
                "El dashboard no dispone de precios y el modelo no predice precios."
            )
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError) as error:
            raise DataUnavailable(
                "No se pudo leer el Parquet histórico de precios ESIOS.") from error
        if not {"timestamp", "price_mwh"}.issubset(frame.columns):
            raise DataUnavailable("El Parquet de ESIOS debe contener timestamp y price_mwh.")
        frame = pd.DataFrame({
            "timestamp_utc": pd.to_datetime(frame.timestamp, utc=True),
            "price_eur_mwh": pd.to_numeric(frame.price_mwh, errors="coerce"),
        }).dropna().sort_values("timestamp_utc")
        for column, bound_value, operator in (
                ("timestamp_utc", start, "ge"), ("timestamp_utc", end, "le")):
            if bound_value:
                bound = pd.Timestamp(bound_value)
                bound = bound.tz_localize("UTC") if bound.tzinfo is None else (
                    bound.tz_convert("UTC"))
                frame = frame.loc[getattr(frame[column], operator)(bound)]
        frame = frame.tail(limit)
        return {"unit": "€/MWh", "source": "ESIOS · archivo histórico local",
                "kind": "historical_observations",
                "records": [{"timestamp_utc": stamp.isoformat(),
                             "price_eur_mwh": float(value)}
                            for stamp, value in zip(frame.timestamp_utc,
                                                    frame.price_eur_mwh)]}

    def demand_data(self) -> pd.DataFrame:
        if self._cache_is_fresh(self._demand_cached_at):
            return self._demand_cache.copy()
        local = self.local_demand()
        days = int(os.getenv("DEMAND_LOOKBACK_DAYS", "10"))
        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=days)
        try:
            response = self.session.get(REE_URL, params={
                "start_date": start.strftime("%Y-%m-%dT%H:%M"),
                "end_date": end.strftime("%Y-%m-%dT%H:%M"), "time_trunc": "hour",
            }, headers={"Accept": "application/json"}, timeout=12)
            response.raise_for_status()
            records = response.json()["included"][0]["attributes"]["values"]
            live = pd.DataFrame(records)
            live = pd.DataFrame({
                "timestamp_utc": pd.to_datetime(live["datetime"], utc=True),
                "value": pd.to_numeric(live["value"], errors="coerce"),
            })
            self.demand_source = "REData + histórico local"
            result = pd.concat([local, live], ignore_index=True).drop_duplicates(
                "timestamp_utc", keep="last").sort_values("timestamp_utc")
            self._demand_cache = result
            self._demand_cached_at = datetime.now(UTC)
            return result.copy()
        except Exception:
            result = local.sort_values("timestamp_utc") if not local.empty else local
            self._demand_cache = result
            self._demand_cached_at = datetime.now(UTC)
            return result.copy()

    def weather_data(self) -> pd.DataFrame:
        if self._cache_is_fresh(self._weather_cached_at):
            return self._weather_cache.copy()
        local = self.local_weather()
        try:
            response = self.session.get(WEATHER_URL, params={
                "latitude": 40.4168, "longitude": -3.7038,
                "hourly": "temperature_2m", "forecast_days": 7,
                "past_days": 8, "timezone": "UTC",
            }, timeout=12)
            response.raise_for_status()
            hourly = response.json()["hourly"]
            live = pd.DataFrame({
                "timestamp_utc": pd.to_datetime(hourly["time"], utc=True),
                "temperature_2m": pd.to_numeric(hourly["temperature_2m"], errors="coerce"),
            })
            result = pd.concat([local, live], ignore_index=True).drop_duplicates(
                "timestamp_utc", keep="last").sort_values("timestamp_utc")
            self._weather_cache = result
            self._weather_cached_at = datetime.now(UTC)
            return result.copy()
        except Exception:
            result = local.sort_values("timestamp_utc") if not local.empty else local
            self._weather_cache = result
            self._weather_cached_at = datetime.now(UTC)
            return result.copy()

    def history(self, limit: int, start: str | None, end: str | None):
        frame = self.demand_data()
        if frame.empty:
            raise DataUnavailable("No hay datos locales de demanda ni respuesta de REData.")
        if start:
            bound = pd.Timestamp(start)
            bound = bound.tz_localize("UTC") if bound.tzinfo is None else bound.tz_convert("UTC")
            frame = frame[frame.timestamp_utc >= bound]
        if end:
            bound = pd.Timestamp(end)
            bound = bound.tz_localize("UTC") if bound.tzinfo is None else bound.tz_convert("UTC")
            frame = frame[frame.timestamp_utc <= bound]
        frame = frame.tail(limit)
        return {"unit": "MW", "source": self.demand_source,
                "records": [{"timestamp_utc": stamp.isoformat(), "demand_mw": float(value)}
                            for stamp, value in zip(frame.timestamp_utc, frame.value)
                            if pd.notna(value)]}

    def health(self):
        demand = self.demand_data()
        last = demand.timestamp_utc.max() if not demand.empty else None
        available = last is not None
        fresh = available and (pd.Timestamp.now(tz="UTC") - last).total_seconds() <= (
            self.freshness_hours * 3600)
        ready = False
        reason = "La demanda más reciente supera el límite de frescura."
        if fresh:
            weather = self.weather_data()
            aligned = demand.merge(weather, on="timestamp_utc", how="inner").sort_values(
                "timestamp_utc")
            aligned = aligned[aligned.timestamp_utc <= last].tail(168)
            if len(aligned) == 168:
                try:
                    prepare_features(aligned.value, pd.DatetimeIndex(aligned.timestamp_utc),
                                     aligned.temperature_2m)
                    future_times = pd.date_range(last + pd.Timedelta(hours=1), periods=48,
                                                 freq="h")
                    ready = weather.timestamp_utc.isin(future_times).sum() == 48
                    reason = None if ready else "La previsión meteorológica no cubre 48 horas."
                except DataUnavailable as error:
                    reason = str(error)
            else:
                reason = "Faltan 168 horas alineadas de demanda y temperatura."
        return {"status": "ok" if ready else "degraded", "api": True,
                "model_loaded": True, "data_available": available,
                "last_demand_utc": last.isoformat() if available else None,
                "future_forecast_available": bool(ready), "reason": reason}

    def predict(self, horizon: int):
        demand = self.demand_data()
        weather = self.weather_data()
        if demand.empty or weather.empty:
            raise DataUnavailable("No se pudo obtener demanda reciente y previsión meteorológica.")
        demand = demand.dropna().sort_values("timestamp_utc").drop_duplicates(
            "timestamp_utc", keep="last")
        weather = weather.dropna().sort_values("timestamp_utc").drop_duplicates(
            "timestamp_utc", keep="last")
        origin = demand.timestamp_utc.iloc[-1]
        age = pd.Timestamp.now(tz="UTC") - origin
        if age > pd.Timedelta(hours=self.freshness_hours):
            raise DataUnavailable(f"Última demanda: {origin.isoformat()}; supera "
                                  f"{self.freshness_hours} h de antigüedad.")
        matched = demand.merge(weather, on="timestamp_utc", how="inner").sort_values(
            "timestamp_utc")
        matched = matched[matched.timestamp_utc <= origin].tail(168)
        if len(matched) < 168:
            raise DataUnavailable("No hay 168 horas alineadas de demanda y temperatura.")
        timestamps = pd.DatetimeIndex(matched.timestamp_utc)
        values = matched.value.astype(float).tolist()
        temperatures = matched.temperature_2m.astype(float).tolist()
        future = []
        for _ in range(horizon):
            features = prepare_features(pd.Series(values[-168:]),
                                        pd.DatetimeIndex(timestamps[-168:]),
                                        pd.Series(temperatures[-168:]))
            try:
                result = float(np.asarray(self.model.predict(features)).reshape(-1)[0])
            except Exception as error:
                raise ModelUnavailable(f"Error del estimador: {type(error).__name__}.") from error
            if not np.isfinite(result):
                raise ModelUnavailable("El estimador produjo un valor no finito.")
            predicted_at = timestamps[-1] + pd.Timedelta(hours=1)
            weather_row = weather[weather.timestamp_utc == predicted_at]
            if weather_row.empty:
                raise DataUnavailable("Falta temperatura prevista para "
                                      f"{predicted_at.isoformat()}.")
            future.append({"timestamp_utc": predicted_at.isoformat(), "demand_mw": result})
            timestamps = timestamps.append(pd.DatetimeIndex([predicted_at]))
            values.append(result)
            temperatures.append(float(weather_row.temperature_2m.iloc[0]))
        return {"model": self.model_info["model_type"], "horizon_hours": horizon,
                "unit": "MW", "forecast_origin_utc": origin.isoformat(),
                "generated_at_utc": datetime.now(UTC).isoformat(), "predictions": future}

    def evaluation(self):
        if self._evaluation_cached_at is not None and datetime.now(UTC) - (
                self._evaluation_cached_at) < timedelta(hours=1):
            return self._evaluation_cache
        demand = self.local_demand()
        weather = self.local_weather()
        if demand.empty or weather.empty:
            raise DataUnavailable("La evaluación requiere los dos Parquet históricos locales.")
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
        joined = joined.dropna().reset_index(drop=True)
        split = int(len(joined) * 0.8)
        test = joined.iloc[split:]
        predicted = np.asarray(self.model.predict(test[FEATURES]), dtype=float)
        actual = test.objetivo.to_numpy(dtype=float)
        baseline = test.demanda_lag_1.to_numpy(dtype=float)
        horizons = self._evaluate_horizons(joined, split)
        self._evaluation_cache = {
            "type": "evaluación retrospectiva temporal",
            "train_fraction": 0.8, "test_records": len(test),
            "test_start_utc": test.timestamp_utc.iloc[0].isoformat(),
            "mae_mw": float(np.mean(np.abs(actual - predicted))),
            "rmse_mw": float(np.sqrt(np.mean((actual - predicted) ** 2))),
            "baseline_mae_mw": float(np.mean(np.abs(actual - baseline))),
            "baseline_rmse_mw": float(np.sqrt(np.mean((actual - baseline) ** 2))),
            "horizon_metrics": horizons,
            "peak_threshold_mw": float(np.quantile(actual, 0.9)),
            "note": "Los lags usan solo demanda observada hasta el origen y después "
                    "predicciones recursivas. La temperatura futura es observada a "
                    "posteriori; estas métricas no incluyen el error meteorológico ni "
                    "representan una previsión operativa actual.",
        }
        self._evaluation_cached_at = datetime.now(UTC)
        return self._evaluation_cache

    def _evaluate_horizons(self, joined: pd.DataFrame, split: int):
        horizons = (1, 6, 12, 24, 48)
        maximum = max(horizons)
        # Daily-spaced origins keep the recursive backtest representative while
        # bounding the cost of 48 sequential RandomForest predictions per origin.
        stride = 48
        temperatures = dict(zip(joined.timestamp_utc, joined.temperature_2m))
        threshold = float(np.quantile(joined.objetivo.iloc[split:].dropna(), 0.9))
        errors = {hour: {"actual": [], "predicted": [], "baseline": [], "peak": []}
                  for hour in horizons}
        for origin_index in range(split, len(joined) - maximum, stride):
            first = origin_index - 167
            times = pd.DatetimeIndex(joined.timestamp_utc.iloc[first:origin_index + 1])
            values = joined.value.iloc[first:origin_index + 1].astype(float).tolist()
            meteo = joined.temperature_2m.iloc[first:origin_index + 1].astype(float).tolist()
            origin_value = values[-1]
            predictions = []
            for _ in range(maximum):
                features = prepare_features(pd.Series(values[-168:]),
                                            pd.DatetimeIndex(times[-168:]),
                                            pd.Series(meteo[-168:]))
                result = float(np.asarray(self.model.predict(features)).reshape(-1)[0])
                target_time = times[-1] + pd.Timedelta(hours=1)
                target_temperature = temperatures.get(target_time)
                if target_temperature is None or not np.isfinite(result):
                    break
                predictions.append(result)
                times = times.append(pd.DatetimeIndex([target_time]))
                values.append(result)
                meteo.append(float(target_temperature))
            if len(predictions) < maximum:
                continue
            for hour in horizons:
                observed = float(joined.value.iloc[origin_index + hour])
                bucket = errors[hour]
                bucket["actual"].append(observed)
                bucket["predicted"].append(predictions[hour - 1])
                bucket["baseline"].append(origin_value)
                if observed >= threshold:
                    bucket["peak"].append((observed, predictions[hour - 1]))

        result = []
        for hour in horizons:
            bucket = errors[hour]
            actual_values = np.asarray(bucket["actual"], dtype=float)
            predicted_values = np.asarray(bucket["predicted"], dtype=float)
            baseline_values = np.asarray(bucket["baseline"], dtype=float)
            if not len(actual_values):
                continue
            peak_pairs = np.asarray(bucket["peak"], dtype=float).reshape(-1, 2)
            result.append({
                "horizon_hours": hour, "origins": int(len(actual_values)),
                "mae_mw": float(np.mean(np.abs(actual_values - predicted_values))),
                "rmse_mw": float(np.sqrt(np.mean((actual_values - predicted_values) ** 2))),
                "baseline_mae_mw": float(np.mean(np.abs(actual_values - baseline_values))),
                "baseline_rmse_mw": float(np.sqrt(np.mean(
                    (actual_values - baseline_values) ** 2))),
                "peak_records": int(len(peak_pairs)),
                "peak_mae_mw": (float(np.mean(np.abs(peak_pairs[:, 0] - peak_pairs[:, 1])))
                                if len(peak_pairs) else None),
            })
        return result

"""Model loading, data acquisition and feature preparation."""

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


FEATURES = ["hora", "dia_semana", "mes", "temperature_2m",
            "demanda_lag_1", "demanda_lag_24", "demanda_lag_168", "media_24h"]
REE_URL = "https://apidatos.ree.es/es/datos/demanda/evolucion"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
UTC = timezone.utc
LOCAL_ZONE = "Europe/Madrid"
LOGGER = logging.getLogger(__name__)
ESIOS_URL = "https://api.esios.ree.es/indicators"
ESIOS_SERIES = {"real": 1293, "official_forecast": 544}


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
    try:
        frame = pd.read_parquet(path)
    except Exception as error:
        raise DataUnavailable(
            f"No se pudo leer el histórico local {path.name} ({type(error).__name__})."
        ) from error
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
        retries = Retry(total=1, connect=1, read=1, status=0, backoff_factor=0.25,
                        status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods=frozenset({"GET"}))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.demand_source = "Histórico local de 2024"
        self.demand_source_status = "pending"
        self.demand_source_error = None
        self.demand_source_checked_at = None
        self.demand_source_last_success_at = None
        self.demand_source_updated_at = None
        self.weather_source_status = "pending"
        self.weather_source_error = None
        self.weather_source_checked_at = None
        self.local_demand_error = None
        self.local_weather_error = None
        self._demand_cache = None
        cache_path = os.getenv("LIVE_DEMAND_CACHE_PATH")
        self.live_cache_path = Path(cache_path) if cache_path else None
        self._weather_cache = None
        self._demand_cached_at = None
        self._weather_cached_at = None
        self._evaluation_cache = None
        self._evaluation_cached_at = None
        self.esios_token = os.getenv("ESIOS_API_TOKEN", "").strip()
        self._esios_cache = {key: pd.DataFrame(columns=["timestamp_utc", "value"])
                             for key in ESIOS_SERIES}
        self._esios_meta = {key: {"status": "not_configured" if not self.esios_token
                                  else "pending", "error": None,
                                  "checked_at_utc": None, "updated_at_utc": None,
                                  "last_success_at_utc": None}
                            for key in ESIOS_SERIES}
        self._esios_cached_at = None
        cache_root = Path(os.getenv("ESIOS_CACHE_DIR", str(data_dir.parent / "cache")))
        self.esios_cache_dir = cache_root

    def _cache_is_fresh(self, timestamp) -> bool:
        return timestamp is not None and datetime.now(UTC) - timestamp < timedelta(minutes=5)

    def refresh_sources(self) -> None:
        """Invalidate source caches so an explicit user refresh checks both providers."""
        self._demand_cached_at = None
        self._weather_cached_at = None
        self._esios_cached_at = None

    def _esios_read_cache(self, key: str) -> pd.DataFrame:
        if not self._esios_cache[key].empty:
            return self._esios_cache[key].copy()
        path = self.esios_cache_dir / f"esios_{key}.parquet"
        try:
            if path.is_file():
                frame = pd.read_parquet(path)
                frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True)
                frame["value"] = pd.to_numeric(frame.value, errors="coerce")
                frame = frame.dropna().sort_values("timestamp_utc")
                self._esios_cache[key] = frame
                meta_path = path.with_suffix(".json")
                if meta_path.is_file():
                    self._esios_meta[key].update(json.loads(meta_path.read_text("utf-8")))
                return frame.copy()
        except Exception as error:
            LOGGER.warning("No se pudo leer caché ESIOS %s (%s).", key, type(error).__name__)
        return pd.DataFrame(columns=["timestamp_utc", "value"])

    def esios_curves(self, hours: int = 168) -> dict:
        """Return official ESIOS series and honest per-series source metadata."""
        now = datetime.now(UTC)
        if self._esios_cached_at is not None and now - self._esios_cached_at < timedelta(minutes=5):
            return self._serialize_esios_curves(hours)
        if not self.esios_token:
            for key in ESIOS_SERIES:
                self._esios_read_cache(key)
                self._esios_meta[key].update(
                    status="not_configured", error="Falta ESIOS_API_TOKEN.")
            return self._serialize_esios_curves(hours)

        end = pd.Timestamp(now)
        start = end - pd.Timedelta(hours=hours)
        for key, indicator_id in ESIOS_SERIES.items():
            meta = self._esios_meta[key]
            meta["checked_at_utc"] = now.isoformat()
            try:
                response = self._source_get(
                    f"{ESIOS_URL}/{indicator_id}",
                    params={"start_date": start.isoformat(), "end_date": end.isoformat()},
                    headers={"Accept": "application/vnd.esios-api-v1+json",
                             "Content-Type": "application/json",
                             "x-api-key": self.esios_token}, timeout=(3, 12))
                payload = response.json()
                indicator = payload.get("indicator", {})
                if int(indicator.get("id", -1)) != indicator_id:
                    raise DataUnavailable(
                        f"ESIOS respondió con un indicador distinto de {indicator_id}.")
                name = str(indicator.get("name") or indicator.get("short_name") or "").lower()
                expected = "real" if key == "real" else "prevista"
                if expected not in name:
                    raise DataUnavailable(
                        f"El indicador {indicator_id} no identifica demanda {expected}.")
                scope = " ".join([
                    name,
                    *(str(geo.get("geo_name", "")) for geo in indicator.get("geos", [])),
                ]).lower()
                if "peninsul" not in scope:
                    raise DataUnavailable(
                        f"El indicador {indicator_id} no acredita ámbito peninsular "
                        "en su respuesta.")
                magnitudes = indicator.get("magnitud", [])
                if magnitudes and not any(
                        "potencia" in str(item.get("name", "")).lower()
                        for item in magnitudes if isinstance(item, dict)):
                    raise DataUnavailable(
                        f"El indicador {indicator_id} no declara magnitud de potencia.")
                values = indicator.get("values")
                if not isinstance(values, list) or not values:
                    raise DataUnavailable(
                        f"ESIOS no devolvió valores para el indicador {indicator_id}.")
                frame = pd.DataFrame(values)
                time_col = "datetime_utc" if "datetime_utc" in frame else "datetime"
                if time_col not in frame or "value" not in frame:
                    raise DataUnavailable("La respuesta ESIOS no contiene datetime y value.")
                frame = pd.DataFrame({
                    "timestamp_utc": pd.to_datetime(frame[time_col], utc=True, errors="coerce"),
                    "value": pd.to_numeric(frame.value, errors="coerce"),
                })
                frame = frame.dropna().loc[lambda x: np.isfinite(x.value) & x.value.ge(0)]
                frame = frame.loc[frame.timestamp_utc.le(pd.Timestamp(now))]
                frame = frame.drop_duplicates(
                    "timestamp_utc", keep="last").sort_values("timestamp_utc")
                if frame.empty:
                    raise DataUnavailable(
                        f"El indicador {indicator_id} no contiene observaciones válidas.")
                self._esios_cache[key] = frame
                updated = indicator.get("values_updated_at")
                update_timestamp = pd.Timestamp(updated) if updated else None
                if update_timestamp is not None:
                    update_timestamp = (update_timestamp.tz_localize("UTC")
                                        if update_timestamp.tzinfo is None
                                        else update_timestamp.tz_convert("UTC"))
                meta.update(status="connected", error=None,
                            updated_at_utc=(update_timestamp.isoformat()
                                            if update_timestamp is not None else None),
                            last_success_at_utc=now.isoformat(),
                            resolution_minutes=self._resolution_minutes(frame))
                self.esios_cache_dir.mkdir(parents=True, exist_ok=True)
                path = self.esios_cache_dir / f"esios_{key}.parquet"
                frame.to_parquet(path, index=False)
                path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
            except Exception as error:
                meta.update(status="unavailable", error=self._http_error_detail(error))
                LOGGER.warning("ESIOS indicador %s falló (%s): %s", indicator_id,
                               type(error).__name__, meta["error"])
                self._esios_read_cache(key)
        self._esios_cached_at = now
        return self._serialize_esios_curves(hours)

    @staticmethod
    def _resolution_minutes(frame: pd.DataFrame) -> float | None:
        intervals = pd.to_datetime(frame.timestamp_utc, utc=True).sort_values().diff().dropna()
        if intervals.empty:
            return None
        return float(intervals.dt.total_seconds().div(60).median())

    def _serialize_esios_curves(self, hours: int) -> dict:
        end = pd.Timestamp.now(tz="UTC")
        start = end - pd.Timedelta(hours=hours)
        curves = {}
        for key, indicator_id in ESIOS_SERIES.items():
            frame = self._esios_cache[key]
            if frame.empty:
                frame = self._esios_read_cache(key)
            selected = (
                frame.loc[frame.timestamp_utc.between(start, end)] if not frame.empty else frame
            )
            meta = dict(self._esios_meta[key])
            if not meta.get("resolution_minutes") and not selected.empty:
                meta["resolution_minutes"] = self._resolution_minutes(selected)
            origin = ("live" if meta.get("status") == "connected"
                      else "cache" if not selected.empty else "none")
            curves[key] = {
                "indicator_id": indicator_id,
                "data_origin": origin,
                **meta,
                "records": [
                    {"timestamp_utc": stamp.isoformat(), "demand_mw": float(value)}
                    for stamp, value in zip(selected.timestamp_utc, selected.value)
                ],
            }
        return {"geography": "Península · indicador ESIOS específico", "unit": "MW",
                "requested_hours": hours, "retrieved_at_utc": end.isoformat(), "curves": curves}

    def compare_demand_sources(self, hours: int = 48) -> dict:
        """Compare only coincident instants; never round timestamps to manufacture pairs."""
        curves = self.esios_curves(hours)
        demand = self.demand_data()
        end = pd.Timestamp.now(tz="UTC")
        start = end - pd.Timedelta(hours=hours)
        observed = demand.loc[demand.timestamp_utc.between(start, end),
                              ["timestamp_utc", "value"]].rename(
                                  columns={"value": "redata_mw"})
        official = pd.DataFrame(curves["curves"]["real"]["records"])
        if official.empty or observed.empty:
            pairs = pd.DataFrame()
        else:
            official["timestamp_utc"] = pd.to_datetime(official.timestamp_utc, utc=True)
            official["demand_mw"] = pd.to_numeric(official.demand_mw, errors="coerce")
            pairs = observed.merge(
                official.rename(columns={"demand_mw": "esios_mw"}),
                on="timestamp_utc",
                how="inner",
                validate="one_to_one",
            )
            pairs["difference_mw"] = pairs.redata_mw - pairs.esios_mw
        return {"window_hours": hours, "match_rule": "timestamp UTC exacto; sin redondeo",
                "records": [{"timestamp_utc": row.timestamp_utc.isoformat(),
                             "redata_mw": float(row.redata_mw),
                             "esios_mw": float(row.esios_mw),
                             "difference_mw": float(row.difference_mw)}
                            for row in pairs.itertuples(index=False)],
                "redata_status": self.demand_source_status,
                "esios_status": curves["curves"]["real"]["status"]}

    def _source_get(self, url: str, **kwargs):
        """Retry transient HTTP statuses once while retaining the final response body."""
        for attempt in range(2):
            response = self.session.get(url, **kwargs)
            if response.status_code not in {429, 500, 502, 503, 504} or attempt:
                response.raise_for_status()
                return response
            time.sleep(0.25)
        raise AssertionError("unreachable")

    @staticmethod
    def _http_error_detail(error: Exception) -> str:
        if isinstance(error, requests.HTTPError) and error.response is not None:
            response = error.response
            detail = ""
            try:
                errors = response.json().get("errors", [])
                if errors:
                    detail = str(errors[0].get("detail") or errors[0].get("title") or "")
            except (ValueError, AttributeError):
                detail = response.text[:240].strip()
            return f"HTTP {response.status_code}" + (f": {detail[:240]}" if detail else "")
        if isinstance(error, requests.Timeout):
            return f"Timeout: {error}"
        if isinstance(error, requests.ConnectionError):
            return f"ConnectionError: {error}"
        return f"{type(error).__name__}: {error}"

    def _read_live_cache(self) -> pd.DataFrame:
        if self._demand_cache is not None:
            return self._demand_cache.copy()
        if self.live_cache_path is None or not self.live_cache_path.is_file():
            return pd.DataFrame(columns=["timestamp_utc", "value"])
        try:
            meta_path = self.live_cache_path.with_suffix(".json")
            if meta_path.is_file():
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                updated = metadata.get("source_updated_at_utc")
                if updated:
                    self.demand_source_updated_at = pd.Timestamp(updated).to_pydatetime()
                succeeded = metadata.get("source_last_success_at_utc")
                if succeeded:
                    self.demand_source_last_success_at = pd.Timestamp(succeeded).to_pydatetime()
            return self._clean_demand(pd.read_parquet(self.live_cache_path), datetime.now(UTC))
        except Exception as error:
            LOGGER.warning("No se pudo leer caché REData (%s): %s", type(error).__name__, error)
            return pd.DataFrame(columns=["timestamp_utc", "value"])

    def _persist_live_cache(self, frame: pd.DataFrame) -> None:
        if self.live_cache_path is None:
            return
        try:
            self.live_cache_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(self.live_cache_path, index=False)
            meta_path = self.live_cache_path.with_suffix(".json")
            meta_path.write_text(json.dumps({
                "source_updated_at_utc": (self.demand_source_updated_at.isoformat()
                                          if self.demand_source_updated_at else None),
                "source_last_success_at_utc": (
                    self.demand_source_last_success_at.isoformat()
                    if self.demand_source_last_success_at else None),
                "last_observation_utc": (frame.timestamp_utc.max().isoformat()
                                         if not frame.empty else None),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception as error:
            LOGGER.warning("No se pudo persistir caché REData (%s): %s",
                           type(error).__name__, error)

    def local_demand(self) -> pd.DataFrame:
        return read_local(self.data_dir / "redata_demanda_2024.parquet", "demand")

    def local_weather(self) -> pd.DataFrame:
        return read_local(self.data_dir / "openmeteo_madrid_2024.parquet", "weather")

    def demand_data(self) -> pd.DataFrame:
        if self._cache_is_fresh(self._demand_cached_at):
            return self._demand_cache.copy()
        try:
            local = self.local_demand()
            self.local_demand_error = None
        except DataUnavailable as error:
            local = pd.DataFrame(columns=["timestamp_utc", "value"])
            self.local_demand_error = str(error)
            LOGGER.warning("No se pudo cargar el histórico local de demanda: %s", error)
        days = int(os.getenv("DEMAND_LOOKBACK_DAYS", "14"))
        checked_at = datetime.now(UTC)
        end = pd.Timestamp(checked_at).tz_convert(LOCAL_ZONE).floor("h")
        start = end - pd.Timedelta(days=days)
        try:
            response = self._source_get(REE_URL, params={
                "start_date": start.strftime("%Y-%m-%dT%H:%M"),
                "end_date": end.strftime("%Y-%m-%dT%H:%M"), "time_trunc": "hour",
                "geo_trunc": "electric_system", "geo_limit": "peninsular",
                "geo_ids": "8741",
            }, headers={"Accept": "application/json",
                        "Content-Type": "application/json"}, timeout=(2, 5))
            response.raise_for_status()
            payload = response.json()
            widget = payload.get("data", {}).get("attributes", {})
            widget_title = str(widget.get("title") or "").lower()
            included = payload.get("included") if isinstance(payload, dict) else None
            if not isinstance(included, list) or not included:
                raise DataUnavailable("REData no devolvió indicadores de demanda.")
            indicators = [item for item in included if isinstance(item, dict)
                          and isinstance(item.get("attributes"), dict)]
            demand_indicators = [
                item for item in indicators
                if str(item["attributes"].get("title") or "").strip().lower() == "demanda"
            ]
            if "evolución de la demanda" not in widget_title or len(demand_indicators) != 1:
                raise DataUnavailable(
                    "REData no devolvió el widget regional Evolución de la demanda.")
            indicator = demand_indicators[0]
            attributes = indicator["attributes"]
            raw_magnitude = attributes.get("magnitude")
            magnitude = str(raw_magnitude).strip().upper() if raw_magnitude else ""
            if magnitude and magnitude not in {"MW", "MEGAWATTS"}:
                raise DataUnavailable(f"REData devolvió una unidad inesperada: {magnitude}.")
            records = attributes.get("values")
            if not isinstance(records, list) or not records:
                raise DataUnavailable("REData no devolvió observaciones horarias.")
            live = pd.DataFrame(records)
            if not {"datetime", "value"}.issubset(live.columns):
                raise DataUnavailable("El indicador de REData no contiene fecha y valor.")
            live = pd.DataFrame({
                "timestamp_utc": pd.to_datetime(live["datetime"], utc=True,
                                                errors="coerce"),
                "value": pd.to_numeric(live["value"], errors="coerce"),
            })
            live = live.dropna().loc[
                lambda frame: np.isfinite(frame.value) & frame.value.ge(0)]
            live = live.loc[live.timestamp_utc.le(pd.Timestamp(checked_at))]
            live = live.drop_duplicates("timestamp_utc", keep="last").sort_values(
                "timestamp_utc")
            if live.empty:
                raise DataUnavailable("REData no devolvió observaciones reales válidas.")
            self.demand_source_status = "connected"
            self.demand_source_error = None
            self.demand_source_checked_at = checked_at
            last_update = attributes.get("last-update") or widget.get("last-update")
            if last_update:
                update_time = pd.Timestamp(last_update)
                update_time = (update_time.tz_localize("UTC") if update_time.tzinfo is None
                               else update_time.tz_convert("UTC"))
                self.demand_source_updated_at = update_time.to_pydatetime()
            self.demand_source = "REData · evolución de demanda peninsular"
            previous_live = (self._demand_cache.loc[
                self._demand_cache.timestamp_utc >= checked_at - pd.Timedelta(days=days)]
                if self._demand_cache is not None else self._read_live_cache())
            self.demand_source_last_success_at = checked_at
            result = self._clean_demand(pd.concat([local, previous_live, live],
                                                  ignore_index=True), checked_at)
            self._demand_cache = result
            self._demand_cached_at = checked_at
            self._persist_live_cache(result)
            return result.copy()
        except Exception as error:
            self.demand_source_status = "unavailable"
            self.demand_source_error = self._http_error_detail(error)
            self.demand_source_checked_at = checked_at
            LOGGER.warning("No se pudo consultar REData (%s): %s",
                           type(error).__name__, self.demand_source_error)
            previous_live = (self._demand_cache.loc[
                self._demand_cache.timestamp_utc >= checked_at - pd.Timedelta(days=days)]
                if self._demand_cache is not None else self._read_live_cache())
            if not previous_live.empty:
                previous_live = previous_live.loc[
                    previous_live.timestamp_utc >= checked_at - pd.Timedelta(days=days)]
            result = self._clean_demand(pd.concat([local, previous_live],
                                                  ignore_index=True), checked_at)
            self.demand_source = (
                "REData en caché + histórico local 2024" if not previous_live.empty
                else "Histórico local de 2024"
            )
            self._demand_cache = result
            self._demand_cached_at = checked_at
            return result.copy()

    @staticmethod
    def _clean_demand(frame: pd.DataFrame, observed_before: datetime) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=["timestamp_utc", "value"])
        cleaned = frame.copy()
        cleaned["timestamp_utc"] = pd.to_datetime(cleaned.timestamp_utc, utc=True,
                                                  errors="coerce")
        cleaned["value"] = pd.to_numeric(cleaned.value, errors="coerce")
        cleaned = cleaned.dropna(subset=["timestamp_utc", "value"])
        cleaned = cleaned.loc[
            np.isfinite(cleaned.value) & cleaned.value.ge(0)
            & cleaned.timestamp_utc.le(pd.Timestamp(observed_before))
        ]
        return cleaned.drop_duplicates("timestamp_utc", keep="last").sort_values(
            "timestamp_utc").reset_index(drop=True)

    def weather_data(self) -> pd.DataFrame:
        if self._cache_is_fresh(self._weather_cached_at):
            return self._weather_cache.copy()
        try:
            local = self.local_weather()
            self.local_weather_error = None
        except DataUnavailable as error:
            local = pd.DataFrame(columns=["timestamp_utc", "temperature_2m"])
            self.local_weather_error = str(error)
            LOGGER.warning("No se pudo cargar el histórico local meteorológico: %s", error)
        checked_at = datetime.now(UTC)
        try:
            response = self.session.get(WEATHER_URL, params={
                "latitude": 40.4168, "longitude": -3.7038,
                "hourly": "temperature_2m", "forecast_days": 7,
                "past_days": 8, "timezone": "UTC",
            }, timeout=(2, 5))
            response.raise_for_status()
            payload = response.json()
            hourly = payload.get("hourly") if isinstance(payload, dict) else None
            if not isinstance(hourly, dict) or not {
                    "time", "temperature_2m"}.issubset(hourly):
                raise DataUnavailable("Open-Meteo no devolvió temperatura horaria.")
            live = pd.DataFrame({
                "timestamp_utc": pd.to_datetime(hourly["time"], utc=True,
                                                errors="coerce"),
                "temperature_2m": pd.to_numeric(hourly["temperature_2m"], errors="coerce"),
            }).dropna().drop_duplicates("timestamp_utc", keep="last").sort_values(
                "timestamp_utc")
            live = live.loc[np.isfinite(live.temperature_2m)]
            if live.empty:
                raise DataUnavailable("Open-Meteo no devolvió temperaturas válidas.")
            result = self._clean_weather(pd.concat([local, live], ignore_index=True))
            self._weather_cache = result
            self.weather_source_status = "connected"
            self.weather_source_error = None
            self.weather_source_checked_at = checked_at
            self._weather_cached_at = checked_at
            return result.copy()

        except Exception as error:
            status = (f" HTTP {error.response.status_code}"
                      if isinstance(error, requests.HTTPError) and error.response is not None
                      else "")
            self.weather_source_status = "unavailable"
            self.weather_source_error = f"{type(error).__name__}{status}"
            self.weather_source_checked_at = checked_at
            LOGGER.exception("No se pudo consultar la temperatura de Open-Meteo.")
            previous_weather = (self._weather_cache.loc[
                self._weather_cache.timestamp_utc >= checked_at - pd.Timedelta(days=10)]
                if self._weather_cache is not None else pd.DataFrame())
            result = self._clean_weather(pd.concat([local, previous_weather],
                                                   ignore_index=True))
            self._weather_cache = result
            self._weather_cached_at = checked_at
            return result.copy()

    def archived_weather(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Fetch reanalysis temperatures for a historical simulation's exact interval."""
        start = pd.Timestamp(start).tz_convert("UTC")
        end = pd.Timestamp(end).tz_convert("UTC")
        response = self._source_get(WEATHER_ARCHIVE_URL, params={
            "latitude": 40.4168, "longitude": -3.7038,
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "hourly": "temperature_2m", "timezone": "UTC",
        }, timeout=(3, 12))
        payload = response.json()
        hourly = payload.get("hourly") if isinstance(payload, dict) else None
        if not isinstance(hourly, dict) or not {
                "time", "temperature_2m"}.issubset(hourly):
            raise DataUnavailable("Open-Meteo Archive no devolvió temperatura horaria.")
        frame = pd.DataFrame({
            "timestamp_utc": pd.to_datetime(hourly["time"], utc=True, errors="coerce"),
            "temperature_2m": pd.to_numeric(hourly["temperature_2m"], errors="coerce"),
        })
        return (
            self._clean_weather(frame)
            .loc[lambda data: data.timestamp_utc.between(start, end)]
            .reset_index(drop=True)
        )

    @staticmethod
    def _clean_weather(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=["timestamp_utc", "temperature_2m"])
        cleaned = frame.copy()
        cleaned["timestamp_utc"] = pd.to_datetime(cleaned.timestamp_utc, utc=True,
                                                  errors="coerce")
        cleaned["temperature_2m"] = pd.to_numeric(cleaned.temperature_2m, errors="coerce")
        cleaned = cleaned.dropna(subset=["timestamp_utc", "temperature_2m"])
        cleaned = cleaned.loc[np.isfinite(cleaned.temperature_2m)]
        return cleaned.drop_duplicates("timestamp_utc", keep="last").sort_values(
            "timestamp_utc").reset_index(drop=True)

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
                "source_status": self.demand_source_status,
                "source_checked_at_utc": (
                    self.demand_source_checked_at.isoformat()
                    if self.demand_source_checked_at else None),
                "source_updated_at_utc": (
                    self.demand_source_updated_at.isoformat()
                    if self.demand_source_updated_at else None),
                "source_last_success_at_utc": (
                    self.demand_source_last_success_at.isoformat()
                    if self.demand_source_last_success_at else None),
                "records": [{"timestamp_utc": stamp.isoformat(), "demand_mw": float(value)}
                            for stamp, value in zip(frame.timestamp_utc, frame.value)
                            if pd.notna(value)]}

    def health(self):
        demand = self._clean_demand(self.demand_data(), datetime.now(UTC))
        last = demand.timestamp_utc.max() if not demand.empty else None
        available = last is not None
        now = pd.Timestamp.now(tz="UTC")
        age_hours = (max(0.0, (now - last).total_seconds() / 3600)
                     if available else None)
        fresh = available and age_hours <= self.freshness_hours
        last_row = demand.loc[demand.timestamp_utc.eq(last)].iloc[-1] if available else None
        previous_time = last - pd.Timedelta(hours=1) if fresh else None
        previous = demand.loc[demand.timestamp_utc.eq(previous_time)] if fresh else pd.DataFrame()
        delta = (float(last_row.value - previous.value.iloc[-1])
                 if not previous.empty else None)
        weather = self.weather_data()
        ready = False
        reason = None
        history_ready = False
        weather_ready = False
        if available:
            aligned = demand.merge(weather, on="timestamp_utc", how="inner").sort_values(
                "timestamp_utc")
            aligned = aligned[aligned.timestamp_utc <= last].tail(168)
            if len(aligned) == 168:
                try:
                    prepare_features(aligned.value, pd.DatetimeIndex(aligned.timestamp_utc),
                                     aligned.temperature_2m)
                    history_ready = True
                    future_times = pd.date_range(last + pd.Timedelta(hours=1), periods=48,
                                                 freq="h")
                    weather_ready = weather.timestamp_utc.isin(future_times).sum() == 48
                    ready = bool(fresh and history_ready and weather_ready)
                except DataUnavailable as error:
                    reason = str(error)
            else:
                reason = (f"Hay {len(aligned)} horas alineadas; se requieren 168 "
                          "consecutivas de demanda y temperatura.")
        if not available:
            reason = "No hay observaciones de demanda disponibles."
        elif not fresh and not reason:
            reason = (f"La última observación real tiene {age_hours:.1f} h y supera el "
                      f"límite de {self.freshness_hours} h. Solo se permite simulación "
                      "histórica con confirmación explícita.")
        elif fresh and not weather_ready and not reason:
            reason = "Open-Meteo no cubre las próximas 48 horas con temperatura horaria."
        if not self.demand_source_error and self.demand_source_status == "connected":
            source_notice = None
        elif self.demand_source_error:
            source_notice = ("REData no está accesible en la última consulta "
                             f"({self.demand_source_error}); se conserva el último dato real.")
        else:
            source_notice = "Aún no se ha confirmado una consulta reciente a REData."
        status = "ok" if ready else "degraded"
        return {
            "status": status, "api": True, "model_loaded": True,
            "model_schema_valid": True, "data_available": available,
            "model_ready": bool(history_ready),
            "demand_source": "REData · demanda peninsular",
            "demand_source_status": self.demand_source_status,
            "demand_source_error": self.demand_source_error,
            "demand_source_checked_at_utc": (
                self.demand_source_checked_at.isoformat()
                if self.demand_source_checked_at else None),
            "demand_source_updated_at_utc": (
                self.demand_source_updated_at.isoformat()
                if self.demand_source_updated_at else None),
            "demand_source_last_success_at_utc": (
                self.demand_source_last_success_at.isoformat()
                if self.demand_source_last_success_at else None),
            "weather_source": "Open-Meteo · Madrid · temperatura a 2 m",
            "weather_source_status": self.weather_source_status,
            "weather_source_error": self.weather_source_error,
            "weather_source_checked_at_utc": (
                self.weather_source_checked_at.isoformat()
                if self.weather_source_checked_at else None),
            "last_demand_utc": last.isoformat() if available else None,
            "last_demand_mw": float(last_row.value) if available else None,
            "previous_demand_utc": previous_time.isoformat() if not previous.empty else None,
            "previous_demand_mw": float(previous.value.iloc[-1]) if not previous.empty else None,
            "demand_delta_mw": delta,
            "last_demand_age_hours": age_hours,
            "freshness_limit_hours": self.freshness_hours,
            "demand_fresh": bool(fresh),
            "historical_features_available": bool(history_ready),
            "forecast_weather_available": bool(weather_ready),
            "model_inputs_available": bool(history_ready and weather_ready),
            "future_forecast_available": bool(ready), "reason": reason,
            "historical_simulation_available": bool(history_ready),
            "source_notice": source_notice,
        }

    def predict(self, horizon: int, allow_stale_simulation: bool = False):
        demand = self._clean_demand(self.demand_data(), datetime.now(UTC))
        weather = self.weather_data()
        if demand.empty:
            raise DataUnavailable(
                "Faltan observaciones de demanda válidas; no se generó predicción.")
        weather = self._clean_weather(weather)
        origin = demand.timestamp_utc.iloc[-1]
        age = pd.Timestamp.now(tz="UTC") - origin
        stale = age > pd.Timedelta(hours=self.freshness_hours)
        if stale and not allow_stale_simulation:
            raise DataUnavailable(
                f"Última demanda real: {origin.isoformat()} ({age.total_seconds()/3600:.1f} h). "
                "Supera el límite de frescura. Confirma explícitamente la simulación histórica.")
        matched = demand.merge(weather, on="timestamp_utc", how="inner").sort_values(
            "timestamp_utc")
        matched = matched[matched.timestamp_utc <= origin].tail(168)
        if len(matched) < 168:
            raise DataUnavailable(
                f"Solo hay {len(matched)} horas alineadas; se necesitan 168 consecutivas "
                "de demanda y temperatura para conservar lags y media móvil.")
        timestamps = pd.DatetimeIndex(matched.timestamp_utc)
        values = matched.value.astype(float).tolist()
        temperatures = matched.temperature_2m.astype(float).tolist()
        future_times = pd.date_range(origin + pd.Timedelta(hours=1), periods=horizon, freq="h")
        future_weather = weather.loc[weather.timestamp_utc.isin(future_times)].copy()
        if stale and allow_stale_simulation and len(future_weather) < horizon:
            try:
                archived = self.archived_weather(future_times[0], future_times[-1])
                future_weather = self._clean_weather(pd.concat(
                    [future_weather, archived], ignore_index=True)).loc[
                        lambda frame: frame.timestamp_utc.isin(future_times)]
            except (DataUnavailable, requests.RequestException) as error:
                raise DataUnavailable(
                    "Faltan temperaturas históricas para continuar esta simulación "
                    f"desde {origin.isoformat()}: {self._http_error_detail(error)}") from error
        if len(future_weather) < horizon:
            missing = future_times.difference(pd.DatetimeIndex(future_weather.timestamp_utc))
            raise DataUnavailable(
                f"Faltan {len(missing)} temperaturas horarias para las horas posteriores "
                f"a {origin.isoformat()}; no se sustituyen ni interpolan.")
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
            weather_row = future_weather[future_weather.timestamp_utc == predicted_at]
            if weather_row.empty:
                raise DataUnavailable("Falta temperatura prevista para "
                                      f"{predicted_at.isoformat()}.")
            future.append({"timestamp_utc": predicted_at.isoformat(), "demand_mw": result})
            timestamps = timestamps.append(pd.DatetimeIndex([predicted_at]))
            values.append(result)
            temperatures.append(float(weather_row.temperature_2m.iloc[0]))
        return {"model": self.model_info["model_type"], "horizon_hours": horizon,
                "unit": "MW", "forecast_origin_utc": origin.isoformat(),
                "generated_at_utc": datetime.now(UTC).isoformat(), "predictions": future,
                "prediction_mode": "historical_simulation" if stale else "current_forecast",
                "simulation_reference_utc": origin.isoformat() if stale else None,
                "demand_age_hours": age.total_seconds() / 3600,
                "weather_input": (
                    "Open-Meteo Archive reanalysis" if stale else "Open-Meteo forecast")}

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

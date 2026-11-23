"""Demand service: observed demand (REData), temperature (Open-Meteo), readiness checks
and the recursive hourly forecast of the trained model.

Source data is cached in memory for five minutes. Every response states where its data
comes from, and missing inputs are reported instead of being filled in or interpolated.
"""

from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from common import (DEMAND_COLUMNS, LOCAL_ZONE, UTC, WEATHER_COLUMNS, DataUnavailable,
                    ModelUnavailable, clean_demand, clean_weather, get_with_retry,
                    http_error_detail, http_session, iso, read_local, to_utc)
from esios import EsiosCurves
from evaluation import evaluate
from features import HISTORY_HOURS, prepare_features


LOGGER = logging.getLogger(__name__)
REE_URL = "https://apidatos.ree.es/es/datos/demanda/evolucion"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MADRID = {"latitude": 40.4168, "longitude": -3.7038}
SOURCE_CACHE_TTL = timedelta(minutes=5)
EVALUATION_CACHE_TTL = timedelta(hours=1)
FORECAST_WEATHER_HOURS = 48
WEATHER_KEEP_DAYS = 10


def _is_recent(timestamp, ttl: timedelta) -> bool:
    return timestamp is not None and datetime.now(UTC) - timestamp < ttl


def _hourly_temperatures(payload, source: str) -> pd.DataFrame:
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict) or not {"time", "temperature_2m"}.issubset(hourly):
        raise DataUnavailable(f"{source} no devolvió temperatura horaria.")
    return pd.DataFrame({
        "timestamp_utc": pd.to_datetime(hourly["time"], utc=True, errors="coerce"),
        "temperature_2m": pd.to_numeric(hourly["temperature_2m"], errors="coerce"),
    })


class DemandService:
    def __init__(self, model, model_info, data_dir: Path, freshness_hours: int = 6):
        self.model = model
        self.model_info = model_info
        self.data_dir = data_dir
        self.freshness_hours = freshness_hours
        self.session = http_session()
        self.esios = EsiosCurves(
            self.session, Path(os.getenv("ESIOS_CACHE_DIR", str(data_dir.parent / "cache"))),
            os.getenv("ESIOS_API_TOKEN", "").strip())
        live_cache = os.getenv("LIVE_DEMAND_CACHE_PATH")
        self.live_cache_path = Path(live_cache) if live_cache else None

        # Source state reported by /ready and shown in the dashboard.
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
        self._demand_cached_at = None
        self._weather_cache = None
        self._weather_cached_at = None
        self._evaluation = None
        self._evaluation_at = None

    # ------------------------------------------------------------------ sources
    def refresh_sources(self) -> None:
        """Invalidate source caches so an explicit refresh queries every provider again."""
        self._demand_cached_at = None
        self._weather_cached_at = None
        self.esios.invalidate()

    def local_demand(self) -> pd.DataFrame:
        return read_local(self.data_dir / "redata_demanda_2024.parquet", "demand")

    def local_weather(self) -> pd.DataFrame:
        return read_local(self.data_dir / "openmeteo_madrid_2024.parquet", "weather")

    def esios_curves(self, hours: int = 168) -> dict:
        return self.esios.curves(hours)

    def observed_demand(self) -> pd.DataFrame:
        """Clean demand observations up to now."""
        return clean_demand(self.demand_data(), datetime.now(UTC))

    def demand_data(self) -> pd.DataFrame:
        """Local 2024 history plus recent REData observations (falls back to the cache)."""
        if _is_recent(self._demand_cached_at, SOURCE_CACHE_TTL):
            return self._demand_cache.copy()
        try:
            local = self.local_demand()
            self.local_demand_error = None
        except DataUnavailable as error:
            local = pd.DataFrame(columns=DEMAND_COLUMNS)
            self.local_demand_error = str(error)
            LOGGER.warning("No se pudo cargar el histórico local de demanda: %s", error)
        days = int(os.getenv("DEMAND_LOOKBACK_DAYS", "14"))
        checked_at = datetime.now(UTC)
        previous = self._recent_live(checked_at - pd.Timedelta(days=days))
        self.demand_source_checked_at = checked_at
        try:
            live = self._fetch_redata(checked_at, days)
        except Exception as error:
            self.demand_source_status = "unavailable"
            self.demand_source_error = http_error_detail(error)
            LOGGER.warning("No se pudo consultar REData (%s): %s",
                           type(error).__name__, self.demand_source_error)
            self.demand_source = ("REData en caché + histórico local 2024" if not previous.empty
                                  else "Histórico local de 2024")
            result = clean_demand(pd.concat([local, previous], ignore_index=True), checked_at)
        else:
            self.demand_source_status = "connected"
            self.demand_source_error = None
            self.demand_source_last_success_at = checked_at
            self.demand_source = "REData · evolución de demanda peninsular"
            result = clean_demand(pd.concat([local, previous, live], ignore_index=True),
                                  checked_at)
            self._persist_live_cache(result)
        self._demand_cache = result
        self._demand_cached_at = checked_at
        return result.copy()

    def _fetch_redata(self, checked_at: datetime, days: int) -> pd.DataFrame:
        """Hourly peninsular demand of the last `days` from the REData "evolución" widget."""
        end = pd.Timestamp(checked_at).tz_convert(LOCAL_ZONE).floor("h")
        start = end - pd.Timedelta(days=days)
        response = get_with_retry(self.session, REE_URL, params={
            "start_date": start.strftime("%Y-%m-%dT%H:%M"),
            "end_date": end.strftime("%Y-%m-%dT%H:%M"), "time_trunc": "hour",
            "geo_trunc": "electric_system", "geo_limit": "peninsular", "geo_ids": "8741",
        }, headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=(2, 5))
        payload = response.json()
        widget = payload.get("data", {}).get("attributes", {})
        included = payload.get("included") if isinstance(payload, dict) else None
        if not isinstance(included, list) or not included:
            raise DataUnavailable("REData no devolvió indicadores de demanda.")
        demand_indicators = [
            item for item in included
            if isinstance(item, dict) and isinstance(item.get("attributes"), dict)
            and str(item["attributes"].get("title") or "").strip().lower() == "demanda"
        ]
        if ("evolución de la demanda" not in str(widget.get("title") or "").lower()
                or len(demand_indicators) != 1):
            raise DataUnavailable("REData no devolvió el widget regional Evolución de la demanda.")
        attributes = demand_indicators[0]["attributes"]
        magnitude = str(attributes.get("magnitude") or "").strip().upper()
        if magnitude and magnitude not in {"MW", "MEGAWATTS"}:
            raise DataUnavailable(f"REData devolvió una unidad inesperada: {magnitude}.")
        records = attributes.get("values")
        if not isinstance(records, list) or not records:
            raise DataUnavailable("REData no devolvió observaciones horarias.")
        raw = pd.DataFrame(records)
        if not {"datetime", "value"}.issubset(raw.columns):
            raise DataUnavailable("El indicador de REData no contiene fecha y valor.")
        live = clean_demand(raw.rename(columns={"datetime": "timestamp_utc"}), checked_at)
        if live.empty:
            raise DataUnavailable("REData no devolvió observaciones reales válidas.")
        last_update = attributes.get("last-update") or widget.get("last-update")
        if last_update:
            self.demand_source_updated_at = to_utc(last_update).to_pydatetime()
        return live

    def _recent_live(self, since) -> pd.DataFrame:
        """Previously fetched REData observations (memory, then disk) newer than `since`."""
        frame = self._demand_cache if self._demand_cache is not None else self._read_live_cache()
        return frame.loc[frame.timestamp_utc >= since] if not frame.empty else frame

    def _read_live_cache(self) -> pd.DataFrame:
        if self.live_cache_path is None or not self.live_cache_path.is_file():
            return pd.DataFrame(columns=DEMAND_COLUMNS)
        try:
            meta_path = self.live_cache_path.with_suffix(".json")
            if meta_path.is_file():
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                if metadata.get("source_updated_at_utc"):
                    self.demand_source_updated_at = pd.Timestamp(
                        metadata["source_updated_at_utc"]).to_pydatetime()
                if metadata.get("source_last_success_at_utc"):
                    self.demand_source_last_success_at = pd.Timestamp(
                        metadata["source_last_success_at_utc"]).to_pydatetime()
            return clean_demand(pd.read_parquet(self.live_cache_path), datetime.now(UTC))
        except Exception as error:
            LOGGER.warning("No se pudo leer caché REData (%s): %s", type(error).__name__, error)
            return pd.DataFrame(columns=DEMAND_COLUMNS)

    def _persist_live_cache(self, frame: pd.DataFrame) -> None:
        if self.live_cache_path is None:
            return
        try:
            self.live_cache_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(self.live_cache_path, index=False)
            self.live_cache_path.with_suffix(".json").write_text(json.dumps({
                "source_updated_at_utc": iso(self.demand_source_updated_at),
                "source_last_success_at_utc": iso(self.demand_source_last_success_at),
                "last_observation_utc": (frame.timestamp_utc.max().isoformat()
                                         if not frame.empty else None),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception as error:
            LOGGER.warning("No se pudo persistir caché REData (%s): %s",
                           type(error).__name__, error)

    def weather_data(self) -> pd.DataFrame:
        """Local 2024 temperatures plus the Open-Meteo forecast for Madrid."""
        if _is_recent(self._weather_cached_at, SOURCE_CACHE_TTL):
            return self._weather_cache.copy()
        try:
            local = self.local_weather()
            self.local_weather_error = None
        except DataUnavailable as error:
            local = pd.DataFrame(columns=WEATHER_COLUMNS)
            self.local_weather_error = str(error)
            LOGGER.warning("No se pudo cargar el histórico local meteorológico: %s", error)
        checked_at = datetime.now(UTC)
        self.weather_source_checked_at = checked_at
        try:
            response = self.session.get(WEATHER_URL, params={
                **MADRID, "hourly": "temperature_2m", "forecast_days": 7, "past_days": 8,
                "timezone": "UTC"}, timeout=(2, 5))
            response.raise_for_status()
            live = clean_weather(_hourly_temperatures(response.json(), "Open-Meteo"))
            if live.empty:
                raise DataUnavailable("Open-Meteo no devolvió temperaturas válidas.")
            result = self._fill_from_archive(clean_weather(pd.concat([local, live],
                                                                     ignore_index=True)))
        except Exception as error:
            status = (f" HTTP {error.response.status_code}"
                      if isinstance(error, requests.HTTPError) and error.response is not None
                      else "")
            self.weather_source_status = "unavailable"
            self.weather_source_error = f"{type(error).__name__}{status}"
            LOGGER.exception("No se pudo consultar la temperatura de Open-Meteo.")
            since = checked_at - pd.Timedelta(days=WEATHER_KEEP_DAYS)
            previous = (self._weather_cache.loc[self._weather_cache.timestamp_utc >= since]
                        if self._weather_cache is not None else pd.DataFrame())
            result = clean_weather(pd.concat([local, previous], ignore_index=True))
        else:
            self.weather_source_status = "connected"
            self.weather_source_error = None
        self._weather_cache = result
        self._weather_cached_at = checked_at
        return result.copy()

    def _fill_from_archive(self, weather: pd.DataFrame) -> pd.DataFrame:
        """The forecast feed can miss recent past hours; fill only those with measured
        reanalysis temperatures, never with interpolated values."""
        recent = self.demand_data().tail(HISTORY_HOURS)
        if len(recent) < HISTORY_HOURS:
            return weather
        aligned = recent.merge(weather, on="timestamp_utc", how="inner").timestamp_utc.nunique()
        if aligned >= HISTORY_HOURS:
            return weather
        try:
            archived = self.archived_weather(recent.timestamp_utc.iloc[0],
                                             recent.timestamp_utc.iloc[-1])
        except (requests.RequestException, DataUnavailable, ValueError) as error:
            LOGGER.warning("No se pudieron recuperar horas meteorológicas reales desde "
                           "Archive (%s).", type(error).__name__)
            return weather
        return clean_weather(pd.concat([weather, archived], ignore_index=True))

    def archived_weather(self, start, end) -> pd.DataFrame:
        """Reanalysis temperatures (Open-Meteo Archive) for an exact past interval."""
        start, end = to_utc(start), to_utc(end)
        response = get_with_retry(self.session, WEATHER_ARCHIVE_URL, params={
            **MADRID, "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"), "hourly": "temperature_2m",
            "timezone": "UTC"}, timeout=(3, 12))
        frame = clean_weather(_hourly_temperatures(response.json(), "Open-Meteo Archive"))
        return frame.loc[frame.timestamp_utc.between(start, end)].reset_index(drop=True)

    def compare_demand_sources(self, hours: int = 48) -> dict:
        """Compare only coincident instants; never round timestamps to manufacture pairs."""
        curves = self.esios_curves(hours)
        demand = self.demand_data()
        end = pd.Timestamp.now(tz="UTC")
        start = end - pd.Timedelta(hours=hours)
        observed = demand.loc[demand.timestamp_utc.between(start, end),
                              ["timestamp_utc", "value"]].rename(columns={"value": "redata_mw"})
        official = pd.DataFrame(curves["curves"]["real"]["records"])
        if official.empty or observed.empty:
            pairs = pd.DataFrame()
        else:
            official["timestamp_utc"] = pd.to_datetime(official.timestamp_utc, utc=True)
            official["demand_mw"] = pd.to_numeric(official.demand_mw, errors="coerce")
            pairs = observed.merge(official.rename(columns={"demand_mw": "esios_mw"}),
                                   on="timestamp_utc", how="inner", validate="one_to_one")
            pairs["difference_mw"] = pairs.redata_mw - pairs.esios_mw
        return {"window_hours": hours, "match_rule": "timestamp UTC exacto; sin redondeo",
                "records": [{"timestamp_utc": row.timestamp_utc.isoformat(),
                             "redata_mw": float(row.redata_mw), "esios_mw": float(row.esios_mw),
                             "difference_mw": float(row.difference_mw)}
                            for row in pairs.itertuples(index=False)],
                "redata_status": self.demand_source_status,
                "esios_status": curves["curves"]["real"]["status"]}

    # ------------------------------------------------------------------ endpoints
    def history(self, limit: int, start=None, end=None) -> dict:
        frame = self.demand_data()
        if frame.empty:
            raise DataUnavailable("No hay datos locales de demanda ni respuesta de REData.")
        if start:
            frame = frame[frame.timestamp_utc >= to_utc(start)]
        if end:
            frame = frame[frame.timestamp_utc <= to_utc(end)]
        frame = frame.tail(limit)
        return {"unit": "MW", "source": self.demand_source,
                "source_status": self.demand_source_status,
                "source_checked_at_utc": iso(self.demand_source_checked_at),
                "source_updated_at_utc": iso(self.demand_source_updated_at),
                "source_last_success_at_utc": iso(self.demand_source_last_success_at),
                "records": [{"timestamp_utc": stamp.isoformat(), "demand_mw": float(value)}
                            for stamp, value in zip(frame.timestamp_utc, frame.value)
                            if pd.notna(value)]}

    def health(self) -> dict:
        """Readiness of every model input; a current forecast needs all of them."""
        demand = self.observed_demand()
        last = demand.timestamp_utc.max() if not demand.empty else None
        available = last is not None
        age_hours = (max(0.0, (pd.Timestamp.now(tz="UTC") - last).total_seconds() / 3600)
                     if available else None)
        fresh = available and age_hours <= self.freshness_hours
        last_value = float(demand.value.iloc[-1]) if available else None
        previous = (demand.loc[demand.timestamp_utc.eq(last - pd.Timedelta(hours=1))]
                    if fresh else demand.iloc[0:0])
        previous_value = float(previous.value.iloc[-1]) if not previous.empty else None
        weather = self.weather_data()
        history_ready, weather_ready, reason = self._input_readiness(demand, weather, last)
        ready = bool(fresh and history_ready and weather_ready)
        if not available:
            reason = "No hay observaciones de demanda disponibles."
        elif not fresh and not reason:
            reason = (f"La última observación real tiene {age_hours:.1f} h y supera el "
                      f"límite de {self.freshness_hours} h. Solo se permite simulación "
                      "histórica con confirmación explícita.")
        elif fresh and not weather_ready and not reason:
            reason = "Open-Meteo no cubre las próximas 48 horas con temperatura horaria."
        return {
            "status": "ok" if ready else "degraded", "api": True, "model_loaded": True,
            "model_schema_valid": True, "data_available": available,
            "model_ready": bool(history_ready),
            "demand_source": "REData · demanda peninsular",
            "demand_source_status": self.demand_source_status,
            "demand_source_error": self.demand_source_error,
            "demand_source_checked_at_utc": iso(self.demand_source_checked_at),
            "demand_source_updated_at_utc": iso(self.demand_source_updated_at),
            "demand_source_last_success_at_utc": iso(self.demand_source_last_success_at),
            "weather_source": "Open-Meteo · Madrid · temperatura a 2 m",
            "weather_source_status": self.weather_source_status,
            "weather_source_error": self.weather_source_error,
            "weather_source_checked_at_utc": iso(self.weather_source_checked_at),
            "last_demand_utc": iso(last),
            "last_demand_mw": last_value,
            "previous_demand_utc": (iso(last - pd.Timedelta(hours=1))
                                    if previous_value is not None else None),
            "previous_demand_mw": previous_value,
            "demand_delta_mw": (last_value - previous_value
                                if previous_value is not None else None),
            "last_demand_age_hours": age_hours,
            "freshness_limit_hours": self.freshness_hours,
            "demand_fresh": bool(fresh),
            "historical_features_available": bool(history_ready),
            "forecast_weather_available": bool(weather_ready),
            "model_inputs_available": bool(history_ready and weather_ready),
            "future_forecast_available": ready, "reason": reason,
            "historical_simulation_available": bool(history_ready),
            "source_notice": self._source_notice(),
        }

    @staticmethod
    def _input_readiness(demand: pd.DataFrame, weather: pd.DataFrame, last):
        """(history ready, future weather ready, reason) for a forecast from `last`."""
        if last is None:
            return False, False, None
        aligned = demand.merge(weather, on="timestamp_utc", how="inner").sort_values(
            "timestamp_utc")
        aligned = aligned[aligned.timestamp_utc <= last].tail(HISTORY_HOURS)
        if len(aligned) < HISTORY_HOURS:
            return False, False, (f"Hay {len(aligned)} horas alineadas; se requieren 168 "
                                  "consecutivas de demanda y temperatura.")
        try:
            prepare_features(aligned.value, pd.DatetimeIndex(aligned.timestamp_utc),
                             aligned.temperature_2m)
        except DataUnavailable as error:
            return False, False, str(error)
        future = pd.date_range(last + pd.Timedelta(hours=1), periods=FORECAST_WEATHER_HOURS,
                               freq="h")
        return True, bool(weather.timestamp_utc.isin(future).sum() == FORECAST_WEATHER_HOURS), None

    def _source_notice(self) -> str | None:
        if self.demand_source_error:
            return ("REData no está accesible en la última consulta "
                    f"({self.demand_source_error}); se conserva el último dato real.")
        if self.demand_source_status != "connected":
            return "Aún no se ha confirmado una consulta reciente a REData."
        return None

    def predict(self, horizon: int, allow_stale_simulation: bool = False) -> dict:
        """Recursive hourly forecast: each prediction becomes the next hour's lag.

        With stale demand it only runs as an explicitly confirmed historical simulation,
        using archived (observed) temperatures instead of the forecast.
        """
        demand = self.observed_demand()
        weather = self.weather_data()
        if demand.empty:
            raise DataUnavailable(
                "Faltan observaciones de demanda válidas; no se generó predicción.")
        origin = demand.timestamp_utc.iloc[-1]
        age = pd.Timestamp.now(tz="UTC") - origin
        stale = age > pd.Timedelta(hours=self.freshness_hours)
        if stale and not allow_stale_simulation:
            raise DataUnavailable(
                f"Última demanda real: {origin.isoformat()} ({age.total_seconds()/3600:.1f} h). "
                "Supera el límite de frescura. Confirma explícitamente la simulación histórica.")
        matched = demand.merge(weather, on="timestamp_utc", how="inner").sort_values(
            "timestamp_utc")
        matched = matched[matched.timestamp_utc <= origin].tail(HISTORY_HOURS)
        if len(matched) < HISTORY_HOURS:
            raise DataUnavailable(
                f"Solo hay {len(matched)} horas alineadas; se necesitan 168 consecutivas "
                "de demanda y temperatura para conservar lags y media móvil.")
        future_times = pd.date_range(origin + pd.Timedelta(hours=1), periods=horizon, freq="h")
        future_temperatures = self._future_temperatures(weather, future_times, origin, stale)

        timestamps = pd.DatetimeIndex(matched.timestamp_utc)
        values = matched.value.astype(float).tolist()
        temperatures = matched.temperature_2m.astype(float).tolist()
        predictions = []
        for predicted_at in future_times:
            features = prepare_features(pd.Series(values[-HISTORY_HOURS:]),
                                        pd.DatetimeIndex(timestamps[-HISTORY_HOURS:]),
                                        pd.Series(temperatures[-HISTORY_HOURS:]))
            try:
                result = float(np.asarray(self.model.predict(features)).reshape(-1)[0])
            except Exception as error:
                raise ModelUnavailable(f"Error del estimador: {type(error).__name__}.") from error
            if not np.isfinite(result):
                raise ModelUnavailable("El estimador produjo un valor no finito.")
            predictions.append({"timestamp_utc": predicted_at.isoformat(), "demand_mw": result})
            timestamps = timestamps.append(pd.DatetimeIndex([predicted_at]))
            values.append(result)
            temperatures.append(future_temperatures[predicted_at])
        return {"model": self.model_info["model_type"], "horizon_hours": horizon,
                "unit": "MW", "forecast_origin_utc": origin.isoformat(),
                "generated_at_utc": datetime.now(UTC).isoformat(), "predictions": predictions,
                "prediction_mode": "historical_simulation" if stale else "current_forecast",
                "simulation_reference_utc": origin.isoformat() if stale else None,
                "demand_age_hours": age.total_seconds() / 3600,
                "weather_input": (
                    "Open-Meteo Archive reanalysis" if stale else "Open-Meteo forecast")}

    def _future_temperatures(self, weather: pd.DataFrame, future_times: pd.DatetimeIndex,
                             origin, stale: bool) -> dict:
        """Temperature for every forecast hour; raises if any hour is missing."""
        future = weather.loc[weather.timestamp_utc.isin(future_times)]
        if stale and len(future) < len(future_times):
            try:
                archived = self.archived_weather(future_times[0], future_times[-1])
            except (DataUnavailable, requests.RequestException) as error:
                raise DataUnavailable(
                    "Faltan temperaturas históricas para continuar esta simulación "
                    f"desde {origin.isoformat()}: {http_error_detail(error)}") from error
            future = clean_weather(pd.concat([future, archived], ignore_index=True))
            future = future.loc[future.timestamp_utc.isin(future_times)]
        if len(future) < len(future_times):
            missing = future_times.difference(pd.DatetimeIndex(future.timestamp_utc))
            raise DataUnavailable(
                f"Faltan {len(missing)} temperaturas horarias para las horas posteriores "
                f"a {origin.isoformat()}; no se sustituyen ni interpolan.")
        return dict(zip(future.timestamp_utc, future.temperature_2m.astype(float)))

    def evaluation(self) -> dict:
        if not _is_recent(self._evaluation_at, EVALUATION_CACHE_TTL):
            self._evaluation = evaluate(self.model, self.local_demand(), self.local_weather())
            self._evaluation_at = datetime.now(UTC)
        return self._evaluation

"""Official REE demand curves from ESIOS (real and forecast), with a disk cache.

Each indicator is checked against its declared name, scope and magnitude before
its values are accepted, so a wrong or changed indicator is reported, not shown.
"""

from datetime import datetime, timedelta
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from common import DEMAND_COLUMNS, UTC, DataUnavailable, get_with_retry, http_error_detail, to_utc


LOGGER = logging.getLogger(__name__)
ESIOS_URL = "https://api.esios.ree.es/indicators"
ESIOS_SERIES = {"real": 1293, "official_forecast": 544}
REFRESH_EVERY = timedelta(minutes=5)


def esios_headers(token: str) -> dict[str, str]:
    return {"Accept": "application/vnd.esios-api-v1+json", "x-api-key": token}


def resolution_minutes(frame: pd.DataFrame) -> float | None:
    """Median spacing of the native samples, in minutes."""
    intervals = pd.to_datetime(frame.timestamp_utc, utc=True).sort_values().diff().dropna()
    if intervals.empty:
        return None
    return float(intervals.dt.total_seconds().div(60).median())


def _check_indicator(indicator: dict, key: str, indicator_id: int) -> None:
    if int(indicator.get("id", -1)) != indicator_id:
        raise DataUnavailable(f"ESIOS respondió con un indicador distinto de {indicator_id}.")
    name = str(indicator.get("name") or indicator.get("short_name") or "").lower()
    expected = "real" if key == "real" else "prevista"
    if expected not in name:
        raise DataUnavailable(f"El indicador {indicator_id} no identifica demanda {expected}.")
    scope = " ".join([name, *(str(geo.get("geo_name", ""))
                              for geo in indicator.get("geos", []))]).lower()
    if "peninsul" not in scope:
        raise DataUnavailable(
            f"El indicador {indicator_id} no acredita ámbito peninsular en su respuesta.")
    magnitudes = indicator.get("magnitud", [])
    if magnitudes and not any("potencia" in str(item.get("name", "")).lower()
                              for item in magnitudes if isinstance(item, dict)):
        raise DataUnavailable(f"El indicador {indicator_id} no declara magnitud de potencia.")


def _parse_values(values, indicator_id: int, observed_before: pd.Timestamp) -> pd.DataFrame:
    if not isinstance(values, list) or not values:
        raise DataUnavailable(f"ESIOS no devolvió valores para el indicador {indicator_id}.")
    raw = pd.DataFrame(values)
    time_col = "datetime_utc" if "datetime_utc" in raw else "datetime"
    if time_col not in raw or "value" not in raw:
        raise DataUnavailable("La respuesta ESIOS no contiene datetime y value.")
    frame = pd.DataFrame({
        "timestamp_utc": pd.to_datetime(raw[time_col], utc=True, errors="coerce"),
        "value": pd.to_numeric(raw.value, errors="coerce"),
    }).dropna()
    frame = frame.loc[np.isfinite(frame.value) & frame.value.ge(0)
                      & frame.timestamp_utc.le(observed_before)]
    frame = frame.drop_duplicates("timestamp_utc", keep="last").sort_values("timestamp_utc")
    if frame.empty:
        raise DataUnavailable(f"El indicador {indicator_id} no contiene observaciones válidas.")
    return frame


class EsiosCurves:
    def __init__(self, session: requests.Session, cache_dir: Path, token: str):
        self.session = session
        self.cache_dir = cache_dir
        self.token = token
        self._frames = {key: pd.DataFrame(columns=DEMAND_COLUMNS) for key in ESIOS_SERIES}
        self._meta = {key: {"status": "pending" if token else "not_configured", "error": None,
                            "checked_at_utc": None, "updated_at_utc": None,
                            "last_success_at_utc": None}
                      for key in ESIOS_SERIES}
        self._fetched_at = None

    def invalidate(self) -> None:
        self._fetched_at = None

    def curves(self, hours: int = 168) -> dict:
        """Both series for the last `hours`, with honest per-series source metadata."""
        now = datetime.now(UTC)
        if self._fetched_at is not None and now - self._fetched_at < REFRESH_EVERY:
            return self._serialize(hours)
        if not self.token:
            for key in ESIOS_SERIES:
                self._read_cache(key)
                self._meta[key].update(status="not_configured", error="Falta ESIOS_API_TOKEN.")
            return self._serialize(hours)

        end = pd.Timestamp(now)
        start = end - pd.Timedelta(hours=hours)
        for key, indicator_id in ESIOS_SERIES.items():
            meta = self._meta[key]
            meta["checked_at_utc"] = now.isoformat()
            try:
                response = get_with_retry(
                    self.session, f"{ESIOS_URL}/{indicator_id}",
                    params={"start_date": start.isoformat(), "end_date": end.isoformat()},
                    headers={**esios_headers(self.token), "Content-Type": "application/json"},
                    timeout=(3, 12))
                indicator = response.json().get("indicator", {})
                _check_indicator(indicator, key, indicator_id)
                frame = _parse_values(indicator.get("values"), indicator_id, end)
                updated = indicator.get("values_updated_at")
                self._frames[key] = frame
                meta.update(status="connected", error=None,
                            updated_at_utc=to_utc(updated).isoformat() if updated else None,
                            last_success_at_utc=now.isoformat(),
                            resolution_minutes=resolution_minutes(frame))
                self._write_cache(key, frame, meta)
            except Exception as error:
                meta.update(status="unavailable", error=http_error_detail(error))
                LOGGER.warning("ESIOS indicador %s falló (%s): %s", indicator_id,
                               type(error).__name__, meta["error"])
                self._read_cache(key)
        self._fetched_at = now
        return self._serialize(hours)

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"esios_{key}.parquet"

    def _write_cache(self, key: str, frame: pd.DataFrame, meta: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(key)
        frame.to_parquet(path, index=False)
        path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")

    def _read_cache(self, key: str) -> pd.DataFrame:
        if not self._frames[key].empty:
            return self._frames[key].copy()
        path = self._cache_path(key)
        try:
            if path.is_file():
                frame = pd.read_parquet(path)
                frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True)
                frame["value"] = pd.to_numeric(frame.value, errors="coerce")
                frame = frame.dropna().sort_values("timestamp_utc")
                self._frames[key] = frame
                meta_path = path.with_suffix(".json")
                if meta_path.is_file():
                    self._meta[key].update(json.loads(meta_path.read_text("utf-8")))
                return frame.copy()
        except Exception as error:
            LOGGER.warning("No se pudo leer caché ESIOS %s (%s).", key, type(error).__name__)
        return pd.DataFrame(columns=DEMAND_COLUMNS)

    def _serialize(self, hours: int) -> dict:
        end = pd.Timestamp.now(tz="UTC")
        start = end - pd.Timedelta(hours=hours)
        curves = {}
        for key, indicator_id in ESIOS_SERIES.items():
            frame = self._frames[key] if not self._frames[key].empty else self._read_cache(key)
            selected = frame.loc[frame.timestamp_utc.between(start, end)] if not frame.empty \
                else frame
            meta = dict(self._meta[key])
            if not meta.get("resolution_minutes") and not selected.empty:
                meta["resolution_minutes"] = resolution_minutes(selected)
            origin = ("live" if meta.get("status") == "connected"
                      else "cache" if not selected.empty else "none")
            curves[key] = {
                "indicator_id": indicator_id, "data_origin": origin, **meta,
                "records": [{"timestamp_utc": stamp.isoformat(), "demand_mw": float(value)}
                            for stamp, value in zip(selected.timestamp_utc, selected.value)],
            }
        return {"geography": "Península · indicador ESIOS específico", "unit": "MW",
                "requested_hours": hours, "retrieved_at_utc": end.isoformat(), "curves": curves}

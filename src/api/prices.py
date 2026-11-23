"""Electricity price: PVPC history (ESIOS indicator 1001) and the calendar price model."""

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from common import LOCAL_ZONE, DataUnavailable, ModelUnavailable
from esios import ESIOS_URL, esios_headers
from features import price_features


LOGGER = logging.getLogger(__name__)
PVPC_INDICATOR = 1001
PENINSULA_GEO_ID = 8741
HISTORY_DAYS = (1, 2, 3, 7)
HORIZONS = (6, 12, 24, 48)
STALE_AFTER = pd.Timedelta(hours=26)
PRICE_COLUMNS = ["timestamp_utc", "price_eur_mwh"]


def _check_pvpc(indicator: dict) -> None:
    """Reject any answer that is not the peninsular PVPC price in €/MWh."""
    if int(indicator.get("id", -1)) != PVPC_INDICATOR:
        raise DataUnavailable("ESIOS devolvió un indicador distinto del PVPC 1001.")
    name = str(indicator.get("name", "")).lower()
    if "pvpc" not in name or ("peníns" not in name and "penins" not in name):
        raise DataUnavailable("ESIOS no confirma PVPC peninsular en la respuesta.")
    magnitude_text = " ".join(
        str(value) for magnitude in indicator.get("magnitud", []) if isinstance(magnitude, dict)
        for value in (magnitude.get("name", ""), magnitude.get("unit", ""))).lower()
    if "precio" not in magnitude_text or "mwh" not in magnitude_text:
        raise DataUnavailable("ESIOS no confirmó magnitud de precio expresada por MWh.")
    peninsular = any(str(geo.get("geo_id")) == str(PENINSULA_GEO_ID)
                     or "penins" in str(geo.get("geo_name", "")).lower()
                     for geo in indicator.get("geos", []) if isinstance(geo, dict))
    if not peninsular:
        raise DataUnavailable("ESIOS no confirmó la geografía Península (geo_id 8741).")


def _parse_prices(records) -> pd.DataFrame:
    raw = pd.DataFrame(records)
    if raw.empty or not {"datetime_utc", "value"}.issubset(raw.columns):
        raise DataUnavailable("ESIOS no devolvió fechas y valores PVPC.")
    if "geo_id" in raw:
        raw = raw.loc[pd.to_numeric(raw.geo_id, errors="coerce").eq(PENINSULA_GEO_ID)]
    frame = pd.DataFrame({
        "timestamp_utc": pd.to_datetime(raw.datetime_utc, utc=True, errors="coerce"),
        "price_eur_mwh": pd.to_numeric(raw.value, errors="coerce"),
    }).dropna()
    frame = frame.loc[np.isfinite(frame.price_eur_mwh)]
    return frame.drop_duplicates("timestamp_utc", keep="last").sort_values("timestamp_utc")


class PriceService:
    def __init__(self, model, model_info, token: str = "", cache_dir: Path | None = None):
        self.model = model
        self.model_info = model_info
        self.token = token.strip()
        self.cache_path = cache_dir / "esios_pvpc_1001.parquet" if cache_dir else None
        self.last_success_at = None
        self.last_error = None
        self._read_cache_metadata()

    def history(self, days: int = 1) -> dict:
        """Observed PVPC prices of the last `days`; cached values if ESIOS is unreachable."""
        if days not in HISTORY_DAYS:
            raise ValueError("El periodo de precios debe ser 1, 2, 3 o 7 días.")
        now = pd.Timestamp.now(tz="UTC")
        start = now - pd.Timedelta(days=days)
        status, error = "connected", None
        if not self.token:
            status, error = "not_configured", "Falta ESIOS_API_TOKEN."
            frame = self._cached()
        else:
            try:
                frame = self._fetch(start, now)
                self.last_success_at = now.isoformat()
                self._write_cache(frame)
                self.last_error = None
            except (requests.RequestException, ValueError, KeyError, TypeError,
                    DataUnavailable) as exc:
                status, error = "unavailable", f"{type(exc).__name__}: {exc}"
                self.last_error = error
                LOGGER.warning("PVPC ESIOS no disponible (%s).", type(exc).__name__)
                frame = self._cached()
        recent = frame.loc[frame.timestamp_utc.between(start, now)]
        newest = frame.timestamp_utc.max() if not frame.empty else None
        return {
            "status": status, "error": error, "source": "ESIOS",
            "indicator_id": PVPC_INDICATOR, "market": "PVPC 2.0TD", "geography": "Península",
            "unit": "€/MWh", "frequency": "nativa ESIOS; sin remuestreo",
            "timezone": "UTC timestamps", "requested_days": days,
            "last_observation_utc": newest.isoformat() if newest is not None else None,
            "last_success_at_utc": self.last_success_at,
            "data_origin": (
                "live" if status == "connected" else "cache" if not recent.empty else "none"),
            "stale": bool(newest is not None and now - newest > STALE_AFTER),
            "records": [{"timestamp_utc": row.timestamp_utc.isoformat(),
                         "timestamp_local": row.timestamp_utc.tz_convert(LOCAL_ZONE).isoformat(),
                         "price_eur_mwh": float(row.price_eur_mwh)}
                        for row in recent.itertuples(index=False)],
        }

    def predict(self, horizon: int) -> dict:
        """Model estimate for the next `horizon` local hours; never presented as observed PVPC."""
        if self.model is None:
            raise ModelUnavailable(self.model_info.get("error", "Modelo de precios no disponible."))
        if horizon not in HORIZONS:
            raise ValueError("El horizonte debe ser 6, 12, 24 o 48 horas.")
        now_local = pd.Timestamp.now(tz=LOCAL_ZONE).floor("h")
        stamps = pd.date_range(now_local + pd.Timedelta(hours=1), periods=horizon, freq="h")
        try:
            values = np.asarray(self.model.predict(price_features(stamps)),
                                dtype="float64").reshape(-1)
        except Exception as exc:
            raise ModelUnavailable(f"Error de inferencia ({type(exc).__name__}).") from exc
        if len(values) != horizon or not np.isfinite(values).all():
            raise ModelUnavailable("El modelo devolvió valores no finitos o longitud inesperada.")
        return {"status": "available", "prediction_type": "model_estimate",
                "model": self.model_info, "unit": "€/MWh", "frequency": "1h",
                "horizon_hours": horizon,
                "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                "timezone": LOCAL_ZONE,
                "records": [{"timestamp_utc": stamp.tz_convert("UTC").isoformat(),
                             "timestamp_local": stamp.isoformat(), "price_eur_mwh": float(value)}
                            for stamp, value in zip(stamps, values)]}

    def _fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        response = requests.get(
            f"{ESIOS_URL}/{PVPC_INDICATOR}",
            params={"start_date": start.isoformat(), "end_date": end.isoformat(),
                    "geo_ids[]": PENINSULA_GEO_ID},
            headers=esios_headers(self.token), timeout=(3, 12))
        response.raise_for_status()
        indicator = response.json().get("indicator", {})
        _check_pvpc(indicator)
        return _parse_prices(indicator.get("values", []))

    def _read_cache_metadata(self) -> None:
        if self.cache_path is None:
            return
        metadata_path = self.cache_path.with_suffix(".json")
        try:
            if metadata_path.is_file():
                self.last_success_at = json.loads(
                    metadata_path.read_text(encoding="utf-8")).get("last_success_at_utc")
        except (OSError, ValueError, TypeError):
            LOGGER.warning("No se pudo leer metadato de caché PVPC.")

    def _cached(self) -> pd.DataFrame:
        if not self.cache_path or not self.cache_path.is_file():
            return pd.DataFrame(columns=PRICE_COLUMNS)
        frame = pd.read_parquet(self.cache_path)
        frame["timestamp_utc"] = pd.to_datetime(frame.timestamp_utc, utc=True)
        return frame.dropna().drop_duplicates("timestamp_utc", keep="last").sort_values(
            "timestamp_utc")

    def _write_cache(self, frame: pd.DataFrame) -> None:
        if self.cache_path is not None and not frame.empty:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(self.cache_path, index=False)
            self.cache_path.with_suffix(".json").write_text(
                json.dumps({"last_success_at_utc": self.last_success_at}), encoding="utf-8")

"""Shared building blocks: errors, time helpers, HTTP access and series cleaning."""

from datetime import timezone
from pathlib import Path
import time

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


UTC = timezone.utc
LOCAL_ZONE = "Europe/Madrid"
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
DEMAND_COLUMNS = ["timestamp_utc", "value"]
WEATHER_COLUMNS = ["timestamp_utc", "temperature_2m"]


class DataUnavailable(RuntimeError):
    """The inputs needed for a valid answer are missing or inconsistent."""


class ModelUnavailable(RuntimeError):
    """The estimator is not loaded or failed during inference."""


def to_utc(value) -> pd.Timestamp:
    """Timestamp in UTC; naive values are taken as UTC."""
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def http_session() -> requests.Session:
    """Session with one quick retry on connection problems for the public data sources."""
    session = requests.Session()
    retries = Retry(total=1, connect=1, read=1, status=0, backoff_factor=0.25,
                    status_forcelist=tuple(TRANSIENT_STATUSES),
                    allowed_methods=frozenset({"GET"}))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def get_with_retry(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET that repeats a transient HTTP status once and keeps the final response body."""
    response = session.get(url, **kwargs)
    if response.status_code in TRANSIENT_STATUSES:
        time.sleep(0.25)
        response = session.get(url, **kwargs)
    response.raise_for_status()
    return response


def http_error_detail(error: Exception) -> str:
    """Short, human-readable reason for a failed source request."""
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


def clean_demand(frame: pd.DataFrame, observed_before) -> pd.DataFrame:
    """Valid, unique, sorted demand observations up to `observed_before`."""
    if frame.empty:
        return pd.DataFrame(columns=DEMAND_COLUMNS)
    cleaned = frame.copy()
    cleaned["timestamp_utc"] = pd.to_datetime(cleaned.timestamp_utc, utc=True, errors="coerce")
    cleaned["value"] = pd.to_numeric(cleaned.value, errors="coerce")
    cleaned = cleaned.dropna(subset=DEMAND_COLUMNS)
    cleaned = cleaned.loc[
        np.isfinite(cleaned.value) & cleaned.value.ge(0)
        & cleaned.timestamp_utc.le(pd.Timestamp(observed_before))
    ]
    return cleaned.drop_duplicates("timestamp_utc", keep="last").sort_values(
        "timestamp_utc").reset_index(drop=True)


def clean_weather(frame: pd.DataFrame) -> pd.DataFrame:
    """Valid, unique, sorted hourly temperatures."""
    if frame.empty:
        return pd.DataFrame(columns=WEATHER_COLUMNS)
    cleaned = frame.copy()
    cleaned["timestamp_utc"] = pd.to_datetime(cleaned.timestamp_utc, utc=True, errors="coerce")
    cleaned["temperature_2m"] = pd.to_numeric(cleaned.temperature_2m, errors="coerce")
    cleaned = cleaned.dropna(subset=WEATHER_COLUMNS)
    cleaned = cleaned.loc[np.isfinite(cleaned.temperature_2m)]
    return cleaned.drop_duplicates("timestamp_utc", keep="last").sort_values(
        "timestamp_utc").reset_index(drop=True)


def read_local(path: Path, kind: str) -> pd.DataFrame:
    """Local 2024 Parquet (REData demand or Open-Meteo weather) in the API's column names."""
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

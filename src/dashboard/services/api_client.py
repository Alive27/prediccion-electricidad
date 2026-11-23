"""HTTP client for the dashboard's FastAPI backend.

Responses are cached per endpoint (`st.cache_data`) and connections are reused, so moving
between pages does not wait for the API. `warm_up()` fills those caches in the background
when a session starts, so even the first visit to each page is immediate.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import threading

import requests
import streamlit as st

# 127.0.0.1 rather than "localhost": on Windows, localhost tries IPv6 first and every new
# connection waits about two seconds before falling back. Docker sets API_BASE_URL itself.
BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("API_TIMEOUT_SECONDS", "35"))
_local = threading.local()


def _session() -> requests.Session:
    """One keep-alive session per thread (requests sessions are not thread-safe)."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def get(path: str, params: dict | None = None) -> dict:
    response = _session().get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def post(path: str, payload: dict) -> dict:
    response = _session().post(f"{BASE_URL}{path}", json=payload, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=30, show_spinner=False)
def cached_status() -> dict:
    """/ready answers 503 while a forecast is not possible, but its body is still the status."""
    response = _session().get(f"{BASE_URL}/ready", timeout=TIMEOUT_SECONDS)
    if response.status_code == 503:
        detail = response.json().get("detail", {})
        if isinstance(detail, dict):
            return detail
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=600, show_spinner=False)
def cached_model_info() -> dict:
    return get("/model/info")


@st.cache_data(ttl=120, show_spinner=False)
def cached_history(limit: int = 168) -> dict:
    return get("/history", {"limit": limit})


@st.cache_data(ttl=120, show_spinner=False)
def cached_history_between(start_utc: str, end_utc: str) -> dict:
    return get("/history", {"limit": 8784, "start": start_utc, "end": end_utc})


@st.cache_data(ttl=300, show_spinner=False)
def cached_curves(hours: int = 168) -> dict:
    return get("/curves", {"hours": hours})


@st.cache_data(ttl=300, show_spinner=False)
def cached_price_history(days: int = 1) -> dict:
    return get("/prices/history", {"days": days})


@st.cache_data(ttl=600, show_spinner=False)
def cached_price_model() -> dict:
    return get("/prices/model")


@st.cache_data(ttl=120, show_spinner=False)
def cached_tracking(days: int = 7) -> dict:
    return get("/predictions/tracking", {"days": days})


def predict_price(horizon_hours: int) -> dict:
    return post("/prices/predict", {"horizon_hours": horizon_hours})


# Everything the pages read on their first render, fetched in parallel.
_WARM_UP_CALLS = (
    (cached_status, ()), (cached_model_info, ()), (cached_price_model, ()),
    (cached_tracking, (7,)), (cached_history, (24,)), (cached_history, (168,)),
    (cached_curves, (168,)), (cached_price_history, (1,)),
)


def _fill(function, args) -> None:
    try:
        function(*args)
    except (requests.RequestException, ValueError):
        pass   # the page that needs it will show the error when it asks again


def warm_up() -> None:
    """Fill the API caches in the background; returns immediately."""
    def run() -> None:
        with ThreadPoolExecutor(max_workers=len(_WARM_UP_CALLS)) as pool:
            for function, args in _WARM_UP_CALLS:
                pool.submit(_fill, function, args)

    threading.Thread(target=run, name="api-warm-up", daemon=True).start()

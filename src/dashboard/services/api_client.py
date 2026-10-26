"""Small HTTP client for the dashboard's FastAPI backend."""

import os

import requests
import streamlit as st


BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("API_TIMEOUT_SECONDS", "8"))


def get(path: str, params: dict | None = None) -> dict:
    response = requests.get(f"{BASE_URL}{path}", params=params,
                            timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def post(path: str, payload: dict) -> dict:
    response = requests.post(f"{BASE_URL}{path}", json=payload,
                             timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=30, show_spinner=False)
def cached_status() -> dict:
    return get("/health")


@st.cache_data(ttl=300, show_spinner=False)
def cached_model_info() -> dict:
    return get("/model/info")


@st.cache_data(ttl=120, show_spinner=False)
def cached_history(limit: int = 168) -> dict:
    return get("/history", {"limit": limit})


@st.cache_data(ttl=900, show_spinner=False)
def cached_evaluation() -> dict:
    return get("/evaluation")

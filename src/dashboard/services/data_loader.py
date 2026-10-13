"""Cached filesystem and in-memory adapters. No writes and no API calls."""

from io import BytesIO

import pandas as pd
import streamlit as st

from services.data_quality import Dataset, prepare
from utils.paths import candidate_paths


@st.cache_data(show_spinner=False, max_entries=12)
def read_disk(kind: str, path: str, mtime_ns: int, size: int) -> Dataset:
    """File metadata is in the cache key, so replacements invalidate it."""
    return prepare(kind, pd.read_parquet(path, engine="pyarrow"), path)


@st.cache_data(show_spinner=False, max_entries=8)
def read_upload(kind: str, content: bytes) -> Dataset:
    return prepare(kind, pd.read_parquet(BytesIO(content), engine="pyarrow"),
                   "Carga manual · solo en memoria")


def load_local(kind: str) -> Dataset:
    paths = candidate_paths(kind)
    checked = []
    errors = []
    fallback = Dataset(kind=kind)
    for path in paths:
        checked.append(str(path))
        try:
            if not path.is_file():
                continue
            stat = path.stat()
            dataset = read_disk(kind, str(path), stat.st_mtime_ns, stat.st_size)
            dataset.checked_paths = tuple(checked)
            if dataset.available:
                dataset.errors = tuple(errors) + dataset.errors
                return dataset
            errors.extend(f"{path.name}: {message}" for message in dataset.errors)
            fallback = dataset
        except Exception as error:
            errors.append(f"{path.name}: lectura fallida ({type(error).__name__}).")
    fallback.checked_paths = tuple(checked)
    fallback.errors = tuple(errors)
    return fallback


def load_upload(kind: str, content: bytes, checked_paths: tuple[str, ...],
                previous_errors: tuple[str, ...] = ()) -> Dataset:
    try:
        dataset = read_upload(kind, content)
    except Exception as error:
        dataset = Dataset(kind=kind, source="Carga manual · solo en memoria",
                          errors=(f"No se pudo leer el Parquet ({type(error).__name__}).",))
    dataset.checked_paths = checked_paths
    dataset.errors = previous_errors + dataset.errors
    return dataset


def refresh() -> None:
    """Clear just the dashboard's acquisition caches."""
    read_disk.clear()
    read_upload.clear()

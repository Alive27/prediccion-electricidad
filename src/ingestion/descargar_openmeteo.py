"""
Descarga la temperatura horaria de Madrid desde Open-Meteo
(2023-2025) y la guarda en Parquet.

Uso (desde la raíz del repositorio, con las dependencias de
requirements-ingesta.txt instaladas):
    python src/ingestion/descargar_openmeteo.py

El resultado se guarda en data/raw/openmeteo_madrid_2023_2025.parquet.
"""

from pathlib import Path

import pandas as pd
import requests

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

url = "https://archive-api.open-meteo.com/v1/archive"
params = {
    "latitude": 40.4168,     # Madrid, como ciudad representativa
    "longitude": -3.7038,
    "start_date": "2023-01-01",
    "end_date": "2025-12-31",
    "hourly": "temperature_2m",
    "timezone": "UTC",
}

resp = requests.get(url, params=params)
resp.raise_for_status()
data = resp.json()

df = pd.DataFrame(data["hourly"])
df["time"] = pd.to_datetime(df["time"], utc=True)

print(df.head())
print(f"Filas descargadas: {len(df)}")

df.to_parquet(RAW_DIR / "openmeteo_madrid_2023_2025.parquet", index=False)

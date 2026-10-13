"""
Descarga el precio horario del mercado diario (SPOT) de España desde
ESIOS (indicador 600, geo_id 3) para 2023-2025, y lo guarda en
Parquet.

La API de ESIOS limita las peticiones a bloques de unas 3 semanas,
así que se descarga por tramos y se van concatenando.

Uso:
    python probar_esios.py
"""

import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

ESIOS_TOKEN = os.getenv("ESIOS_TOKEN")
INDICATOR_ID = 600  # Precio mercado SPOT Diario
GEO_ID_ESPANA = 3

BASE_URL = f"https://api.esios.ree.es/indicators/{INDICATOR_ID}"
HEADERS = {
    # Confirmado por soporte de REE (consultasios@ree.es): la
    # autenticación va en x-api-key, NO en la cabecera Authorization.
    "x-api-key": ESIOS_TOKEN,
    "Accept": "application/json; application/vnd.esios-api-v1+json",
    "Content-Type": "application/json",
    "Host": "api.esios.ree.es",
}

DIAS_POR_TRAMO = 20  # margen de seguridad frente al límite de ~3 semanas
AÑO_INICIO = 2023
AÑO_FIN = 2025  # incluido


def descargar_tramo(fecha_inicio, fecha_fin):
    params = {
        "start_date": fecha_inicio.strftime("%Y-%m-%dT%H:%M"),
        "end_date": fecha_fin.strftime("%Y-%m-%dT%H:%M"),
        "geo_ids[]": GEO_ID_ESPANA,
    }
    resp = requests.get(BASE_URL, headers=HEADERS, params=params)
    resp.raise_for_status()
    data = resp.json()
    valores = data["indicator"]["values"]
    # Nos quedamos solo con España, por si acaso devuelve más de una geo
    valores = [v for v in valores if v.get("geo_id") == GEO_ID_ESPANA]
    return valores


def main():
    if not ESIOS_TOKEN:
        raise RuntimeError("Falta ESIOS_TOKEN en el .env")

    todos_los_datos = []
    fecha_inicio = datetime(AÑO_INICIO, 1, 1)
    fin_total = datetime(AÑO_FIN, 12, 31, 23, 59)

    while fecha_inicio < fin_total:
        fecha_fin = min(
            fecha_inicio + timedelta(days=DIAS_POR_TRAMO) - timedelta(minutes=1),
            fin_total,
        )
        valores = descargar_tramo(fecha_inicio, fecha_fin)
        todos_los_datos.extend(valores)
        print(f"{fecha_inicio.date()} a {fecha_fin.date()}: {len(valores)} filas descargadas")
        fecha_inicio = fecha_fin + timedelta(minutes=1)

    df = pd.DataFrame(todos_los_datos)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
    df = df.rename(columns={"datetime_utc": "timestamp", "value": "price_mwh"})
    df = df[["timestamp", "price_mwh"]].sort_values("timestamp").reset_index(drop=True)

    print(f"Total filas antes de agregar: {len(df)}")

    # Desde 2025 el mercado diario publica en bloques de 15 minutos
    # en vez de horarios; agregamos siempre a horario (media) para
    # que la granularidad sea constante en todo el histórico y
    # coincida con electricity_demand y weather_data.
    df = (
        df.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .reset_index()
    )

    print(f"Total filas tras agregar a horario: {len(df)}")
    print(df.head())

    df.to_parquet("../data/raw/esios_precio_2023_2025.parquet", index=False)


if __name__ == "__main__":
    main()

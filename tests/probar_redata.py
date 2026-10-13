"""
Descarga la demanda horaria de España peninsular desde REData
(2023-2025) y la guarda en Parquet.

La API devuelve resolución de 5 minutos, así que se agrega a
horaria antes de guardar. Se pide mes a mes porque el widget no
admite rangos de un año completo con time_trunc=hour.

Uso:
    python probar_redata.py
"""

import requests
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta

url = "https://apidatos.ree.es/es/datos/demanda/demanda-tiempo-real"
headers = {"Accept": "application/json", "Content-Type": "application/json"}

AÑO_INICIO = 2023
AÑO_FIN = 2025  # incluido
MESES_TOTALES = (AÑO_FIN - AÑO_INICIO + 1) * 12

todos_los_datos = []
fecha_inicio = datetime(AÑO_INICIO, 1, 1)

for _ in range(MESES_TOTALES):
    fecha_fin = fecha_inicio + relativedelta(months=1) - relativedelta(minutes=1)
    params = {
        "start_date": fecha_inicio.strftime("%Y-%m-%dT%H:%M"),
        "end_date": fecha_fin.strftime("%Y-%m-%dT%H:%M"),
        "time_trunc": "hour",
    }
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    valores = data["included"][0]["attributes"]["values"]
    todos_los_datos.extend(valores)
    print(f"{fecha_inicio.strftime('%Y-%m')}: {len(valores)} filas descargadas")
    fecha_inicio += relativedelta(months=1)

df = pd.DataFrame(todos_los_datos)
df["datetime"] = pd.to_datetime(df["datetime"], utc=True)

# Agregar de 5 minutos a horario (media de los 12 valores de cada hora)
df = df.set_index("datetime").resample("1h").mean(numeric_only=True).reset_index()

print(f"\nTotal filas tras agregar a horario: {len(df)}")

df.to_parquet("../data/raw/redata_demanda_2023_2025.parquet", index=False)

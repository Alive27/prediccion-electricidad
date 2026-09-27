# probar_redata.py
import requests
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta

url = "https://apidatos.ree.es/es/datos/demanda/demanda-tiempo-real"
headers = {"Accept": "application/json", "Content-Type": "application/json"}

todos_los_datos = []
fecha_inicio = datetime(2024, 1, 1)

for _ in range(12):
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

df.to_parquet("../data/raw/redata_demanda_2024.parquet", index=False)
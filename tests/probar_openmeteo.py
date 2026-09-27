# probar_openmeteo.py
import requests
import pandas as pd

url = "https://archive-api.open-meteo.com/v1/archive"
params = {
    "latitude": 40.4168,     # Madrid, como ciudad representativa
    "longitude": -3.7038,
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
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

df.to_parquet("data/raw/openmeteo_madrid_2024.parquet", index=False)
"""
Carga en la base de datos los ficheros .parquet ya descargados
(demanda de REData, precio de ESIOS y meteorología de Open-Meteo),
y construye a partir de ellos la tabla unificada
demanda_clima_historico.

Requiere que el esquema de schema_bbdd.sql ya esté creado.

Uso:
    python load_to_db.py
"""

import os
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
# Ejemplo de formato esperado en el .env:
# DATABASE_URL=postgresql://usuario:password@localhost:5432/nombre_bd

RUTA_DEMANDA = "data/raw/redata_demanda_2023_2025.parquet"
RUTA_PRECIO = "data/raw/esios_precio_2023_2025.parquet"
RUTA_METEO = "data/raw/openmeteo_madrid_2023_2025.parquet"
CIUDAD_METEO = "madrid"


def conectar():
    if not DATABASE_URL:
        raise RuntimeError(
            "Falta DATABASE_URL en el .env. Añade algo como:\n"
            "DATABASE_URL=postgresql://usuario:password@localhost:5432/nombre_bd"
        )
    return psycopg2.connect(DATABASE_URL)


def cargar_demanda(conn):
    print(f"Leyendo {RUTA_DEMANDA} ...")
    df = pd.read_parquet(RUTA_DEMANDA)
    df = df.rename(columns={"datetime": "timestamp", "value": "demand_mw"})

    if "price_mwh" not in df.columns:
        df["price_mwh"] = None

    filas = list(
        df[["timestamp", "demand_mw", "price_mwh"]].itertuples(index=False, name=None)
    )

    sql = """
        INSERT INTO electricity_demand (timestamp, demand_mw, price_mwh)
        VALUES %s
        ON CONFLICT (timestamp) DO UPDATE SET
            demand_mw = EXCLUDED.demand_mw,
            price_mwh = COALESCE(EXCLUDED.price_mwh, electricity_demand.price_mwh);
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, filas, page_size=1000)
    conn.commit()
    print(f"  -> {len(filas)} filas insertadas/actualizadas en electricity_demand")


def cargar_precio(conn):
    """
    Lee el precio horario de ESIOS y actualiza price_mwh en
    electricity_demand para las horas que ya existen (por eso usa
    UPDATE y no INSERT: la fila la crea cargar_demanda()).
    """
    if not os.path.exists(RUTA_PRECIO):
        print(f"Aviso: no se encontró {RUTA_PRECIO}, se omite la carga de precio.")
        return

    print(f"Leyendo {RUTA_PRECIO} ...")
    df = pd.read_parquet(RUTA_PRECIO)

    filas = list(df[["timestamp", "price_mwh"]].itertuples(index=False, name=None))

    sql = """
        UPDATE electricity_demand AS e
        SET price_mwh = v.price_mwh
        FROM (VALUES %s) AS v(timestamp, price_mwh)
        WHERE e.timestamp = v.timestamp;
    """
    with conn.cursor() as cur:
        # OJO: con execute_values + page_size, cur.rowcount solo
        # refleja el ÚLTIMO lote ejecutado, no el total acumulado.
        # Para tener un recuento fiable, comprobamos directamente
        # cuántas filas tienen ya precio después de la actualización.
        execute_values(cur, sql, filas, page_size=1000)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM electricity_demand WHERE price_mwh IS NOT NULL;")
        total_con_precio = cur.fetchone()[0]
    print(f"  -> {len(filas)} filas de precio procesadas")
    print(f"  -> {total_con_precio} filas de electricity_demand tienen price_mwh (verificado con COUNT)")


def cargar_meteo(conn):
    print(f"Leyendo {RUTA_METEO} ...")
    df = pd.read_parquet(RUTA_METEO)
    df = df.rename(columns={"time": "timestamp"})
    df["city"] = CIUDAD_METEO

    for col in ["humidity", "radiation"]:
        if col not in df.columns:
            df[col] = None

    filas = list(
        df[["timestamp", "city", "temperature_2m", "humidity", "radiation"]]
        .rename(columns={"temperature_2m": "temperature"})
        .itertuples(index=False, name=None)
    )

    sql = """
        INSERT INTO weather_data (timestamp, city, temperature, humidity, radiation)
        VALUES %s
        ON CONFLICT (timestamp, city) DO UPDATE SET
            temperature = EXCLUDED.temperature,
            humidity = EXCLUDED.humidity,
            radiation = EXCLUDED.radiation;
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, filas, page_size=1000)
    conn.commit()
    print(f"  -> {len(filas)} filas insertadas/actualizadas en weather_data")


def construir_historico(conn):
    """
    Une electricity_demand y weather_data por hora (UTC) y
    guarda el resultado en demanda_clima_historico.
    """
    print("Construyendo demanda_clima_historico ...")
    sql = """
        INSERT INTO demanda_clima_historico
            (timestamp_utc, demanda_mw, precio_mwh, temp_media_c)
        SELECT
            d.timestamp AS timestamp_utc,
            d.demand_mw AS demanda_mw,
            d.price_mwh AS precio_mwh,
            AVG(w.temperature) AS temp_media_c
        FROM electricity_demand d
        LEFT JOIN weather_data w ON w.timestamp = d.timestamp
        GROUP BY d.timestamp, d.demand_mw, d.price_mwh
        ON CONFLICT (timestamp_utc) DO UPDATE SET
            demanda_mw = EXCLUDED.demanda_mw,
            precio_mwh = EXCLUDED.precio_mwh,
            temp_media_c = EXCLUDED.temp_media_c;
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        filas = cur.rowcount
    conn.commit()
    print(f"  -> {filas} filas insertadas/actualizadas en demanda_clima_historico")


def main():
    conn = conectar()
    try:
        cargar_demanda(conn)
        cargar_precio(conn)
        cargar_meteo(conn)
        construir_historico(conn)
        print("\nCarga completada correctamente.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

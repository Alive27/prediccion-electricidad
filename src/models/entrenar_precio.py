"""
Entrena el modelo de precio (Random Forest) con el PVPC de ESIOS y lo guarda en .joblib.

Es el mismo procedimiento que notebooks/entrenamiento_precio.ipynb: usa el
indicador 1001 (PVPC, Península) y cuatro variables de calendario (hora, día de
la semana, mes y día del mes), entrena con el 80 % inicial y evalúa con el 20 %
final. No usa temperatura ni demanda. El modelo resultante solo estima el precio
a partir del calendario; no es una previsión de mercado (ver la documentación).

Datos de entrada: el Parquet que guarda el cuaderno, data/raw/precios_pvpc_2025.parquet,
que no está versionado. Si no existe, se puede descargar con --descargar, que
necesita la librería `esios` y un token de ESIOS en ESIOS_API_TOKEN (o ESIOS_TOKEN)
dentro del .env. El notebook descarga del 1 al 7 de enero de 2025; se mantiene
ese rango por defecto.

Uso (desde la raíz del repositorio):
    python src/models/entrenar_precio.py
    python src/models/entrenar_precio.py --descargar

Por defecto el modelo NO sustituye al del repositorio: se guarda en
models/reentrenado/modelo_precio_v01.joblib. Para sustituirlo, indicar
--salida models/modelo_precio_v01.joblib.
"""

import argparse
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

RAIZ = Path(__file__).resolve().parents[2]
ARCHIVO_PRECIOS = RAIZ / "data" / "raw" / "precios_pvpc_2025.parquet"
SALIDA_POR_DEFECTO = RAIZ / "models" / "reentrenado" / "modelo_precio_v01.joblib"

INDICADOR_PVPC = 1001
INICIO_DESCARGA = "2025-01-01"
FIN_DESCARGA = "2025-01-07"
VARIABLES = ["hora", "dia_semana", "mes", "dia_mes"]
PROPORCION_ENTRENAMIENTO = 0.8
PARAMETROS_MODELO = {
    "n_estimators": 200,
    "random_state": 42,
    "min_samples_leaf": 2,
    "n_jobs": -1,
}


def descargar_precios(archivo=ARCHIVO_PRECIOS):
    """Descarga el PVPC con la librería esios y lo guarda en Parquet."""
    try:
        from dotenv import load_dotenv
        from esios import ESIOSClient
    except ImportError as error:
        raise SystemExit(
            f"Falta una dependencia para descargar ({error.name}). "
            "Instala requirements-ingesta.txt y la librería `esios`."
        ) from error

    load_dotenv()
    token = os.getenv("ESIOS_API_TOKEN") or os.getenv("ESIOS_TOKEN")
    if not token:
        raise SystemExit("Falta ESIOS_API_TOKEN (o ESIOS_TOKEN) en el .env")
    os.environ["ESIOS_API_KEY"] = token

    precios = ESIOSClient().indicators.get(INDICADOR_PVPC).historical(
        INICIO_DESCARGA, FIN_DESCARGA, chunk_workers=1)
    Path(archivo).parent.mkdir(parents=True, exist_ok=True)
    precios.to_parquet(archivo)
    return precios


def construir_variables(precios):
    """Se queda con el precio de la Península y crea las variables de calendario."""
    datos = precios[["Península"]].copy().reset_index()
    datos.columns = ["datetime", "precio"]
    datos = datos.sort_values("datetime").reset_index(drop=True)
    datos["hora"] = datos["datetime"].dt.hour
    datos["dia_semana"] = datos["datetime"].dt.dayofweek
    datos["mes"] = datos["datetime"].dt.month
    datos["dia_mes"] = datos["datetime"].dt.day
    return datos


def dividir(datos):
    """División cronológica 80 % / 20 %, sin mezclar el orden temporal."""
    corte = int(len(datos) * PROPORCION_ENTRENAMIENTO)
    x, y = datos[VARIABLES], datos["precio"]
    return x.iloc[:corte], x.iloc[corte:], y.iloc[:corte], y.iloc[corte:]


def entrenar(x_train, y_train, **parametros):
    modelo = RandomForestRegressor(**{**PARAMETROS_MODELO, **parametros})
    modelo.fit(x_train, y_train)
    return modelo


def evaluar(modelo, x_test, y_test):
    prediccion = modelo.predict(x_test)
    return {
        "mae": mean_absolute_error(y_test, prediccion),
        "rmse": float(np.sqrt(mean_squared_error(y_test, prediccion))),
    }


def guardar(modelo, ruta):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(modelo, ruta)
    return ruta


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--descargar", action="store_true",
                        help="descarga el PVPC de ESIOS en lugar de leer el Parquet")
    parser.add_argument("--salida", type=Path, default=SALIDA_POR_DEFECTO,
                        help="ruta del .joblib que se genera")
    argumentos = parser.parse_args()

    if argumentos.descargar:
        precios = descargar_precios()
    elif ARCHIVO_PRECIOS.exists():
        precios = pd.read_parquet(ARCHIVO_PRECIOS)
    else:
        raise SystemExit(
            f"No existe {ARCHIVO_PRECIOS}. Genéralo con --descargar (necesita token de ESIOS).")

    datos = construir_variables(precios)
    x_train, x_test, y_train, y_test = dividir(datos)
    print(f"Filas de entrenamiento: {len(x_train)}, de prueba: {len(x_test)}")

    modelo = entrenar(x_train, y_train)
    metricas = evaluar(modelo, x_test, y_test)
    print(f"MAE:  {metricas['mae']:.2f}")
    print(f"RMSE: {metricas['rmse']:.2f}")
    print(f"Modelo guardado en: {guardar(modelo, argumentos.salida)}")


if __name__ == "__main__":
    main()

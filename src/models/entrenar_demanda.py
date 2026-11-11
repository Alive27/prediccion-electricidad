"""
Entrena el modelo de demanda eléctrica (Random Forest) y lo guarda en .joblib.

Es el mismo procedimiento que la celda de entrenamiento de
notebooks/eda_demanda.ipynb, extraído a un script para poder repetirlo sin
abrir el cuaderno: une la demanda de REData con la temperatura de Open-Meteo,
construye las ocho variables de entrada, entrena con el 80 % inicial de los
datos y evalúa con el 20 % final frente a una predicción de referencia
(la demanda de la hora siguiente es igual a la actual).

Uso (desde la raíz del repositorio, con las dependencias de la API instaladas
y pyarrow para leer los Parquet):
    python src/models/entrenar_demanda.py

Por defecto el modelo NO sustituye al del repositorio: se guarda en
models/reentrenado/modelo_demanda_v01.joblib. Para sustituirlo, indicar
--salida models/modelo_demanda_v01.joblib.
"""

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

RAIZ = Path(__file__).resolve().parents[2]
ARCHIVO_DEMANDA = RAIZ / "data" / "raw" / "redata_demanda_2024.parquet"
ARCHIVO_METEO = RAIZ / "data" / "raw" / "openmeteo_madrid_2024.parquet"
SALIDA_POR_DEFECTO = RAIZ / "models" / "reentrenado" / "modelo_demanda_v01.joblib"

VARIABLES = [
    "hora",
    "dia_semana",
    "mes",
    "temperature_2m",
    "demanda_lag_1",
    "demanda_lag_24",
    "demanda_lag_168",
    "media_24h",
]
PROPORCION_ENTRENAMIENTO = 0.8
PARAMETROS_MODELO = {
    "n_estimators": 200,
    "max_depth": 15,
    "min_samples_leaf": 2,
    "random_state": 42,
    "n_jobs": -1,
}


def cargar_datos(archivo_demanda=ARCHIVO_DEMANDA, archivo_meteo=ARCHIVO_METEO):
    """Lee los dos Parquet y une demanda y temperatura por fecha y hora."""
    for archivo in (archivo_demanda, archivo_meteo):
        if not Path(archivo).exists():
            raise FileNotFoundError(f"No se encuentra el archivo de datos: {archivo}")
    demanda = pd.read_parquet(archivo_demanda)
    meteo = pd.read_parquet(archivo_meteo)
    demanda["datetime"] = pd.to_datetime(demanda["datetime"], utc=True)
    meteo["time"] = pd.to_datetime(meteo["time"], utc=True)
    unido = demanda.merge(
        meteo[["time", "temperature_2m"]],
        left_on="datetime",
        right_on="time",
        how="inner",
        validate="one_to_one",
    ).drop(columns=["time"])
    return unido[["datetime", "value", "temperature_2m"]]


def construir_variables(unido):
    """Crea las ocho variables de entrada y el objetivo (demanda de la hora siguiente)."""
    datos = unido.copy()
    datos["datetime"] = pd.to_datetime(datos["datetime"], utc=True)
    datos = datos.sort_values("datetime").reset_index(drop=True)

    datos["hora"] = datos["datetime"].dt.hour
    datos["dia_semana"] = datos["datetime"].dt.dayofweek
    datos["mes"] = datos["datetime"].dt.month

    # Los desplazamientos son los del entrenamiento original (23 y 167 filas):
    # se mantienen para que el modelo y la API usen las mismas definiciones.
    datos["demanda_lag_1"] = datos["value"]
    datos["demanda_lag_24"] = datos["value"].shift(23)
    datos["demanda_lag_168"] = datos["value"].shift(167)
    datos["media_24h"] = datos["value"].rolling(24).mean()
    datos["objetivo"] = datos["value"].shift(-1)

    return datos.dropna().reset_index(drop=True)


def dividir(datos):
    """División cronológica 80 % / 20 %, sin mezclar el orden temporal."""
    corte = int(len(datos) * PROPORCION_ENTRENAMIENTO)
    x, y = datos[VARIABLES], datos["objetivo"]
    return x.iloc[:corte], x.iloc[corte:], y.iloc[:corte], y.iloc[corte:], corte


def entrenar(x_train, y_train, **parametros):
    modelo = RandomForestRegressor(**{**PARAMETROS_MODELO, **parametros})
    modelo.fit(x_train, y_train)
    return modelo


def evaluar(modelo, x_test, y_test):
    """MAE y RMSE del modelo y de la referencia (demanda siguiente = demanda actual)."""
    prediccion = modelo.predict(x_test)
    referencia = x_test["demanda_lag_1"].to_numpy()
    return {
        "mae": mean_absolute_error(y_test, prediccion),
        "rmse": float(np.sqrt(mean_squared_error(y_test, prediccion))),
        "mae_referencia": mean_absolute_error(y_test, referencia),
        "rmse_referencia": float(np.sqrt(mean_squared_error(y_test, referencia))),
    }


def guardar(modelo, ruta):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "modelo": modelo,
        "variables": VARIABLES,
        "objetivo": "demanda_siguiente_hora",
        "horizonte_horas": 1,
        "version": "0.1",
    }, ruta)
    return ruta


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--salida", type=Path, default=SALIDA_POR_DEFECTO,
                        help="ruta del .joblib que se genera")
    argumentos = parser.parse_args()

    datos = construir_variables(cargar_datos())
    x_train, x_test, y_train, y_test, corte = dividir(datos)
    print(f"Filas utilizables: {len(datos)} "
          f"(entrenamiento {len(x_train)}, prueba {len(x_test)})")
    print(f"Inicio del periodo de prueba: {datos['datetime'].iloc[corte]}")

    modelo = entrenar(x_train, y_train)
    metricas = evaluar(modelo, x_test, y_test)
    print(f"MAE del modelo:     {metricas['mae']:.2f}")
    print(f"RMSE del modelo:    {metricas['rmse']:.2f}")
    print(f"MAE de referencia:  {metricas['mae_referencia']:.2f}")
    print(f"RMSE de referencia: {metricas['rmse_referencia']:.2f}")

    print(f"Modelo guardado en: {guardar(modelo, argumentos.salida)}")


if __name__ == "__main__":
    main()

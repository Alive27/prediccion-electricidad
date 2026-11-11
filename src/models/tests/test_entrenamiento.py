"""Pruebas de los scripts de entrenamiento con datos sintéticos (sin Parquet ni red)."""

from pathlib import Path
import sys
import tempfile
import unittest

import joblib
import numpy as np
import pandas as pd

MODELS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODELS_DIR))
import entrenar_demanda as demanda  # noqa: E402
import entrenar_precio as precio  # noqa: E402


def serie_demanda(horas=400):
    marcas = pd.date_range("2024-01-01", periods=horas, freq="h", tz="UTC")
    return pd.DataFrame({
        "datetime": marcas,
        "value": np.arange(horas, dtype=float) + 30000,
        "temperature_2m": 10.0 + (np.arange(horas) % 24) / 2,
    })


class EntrenamientoDemandaTests(unittest.TestCase):
    def test_variables_y_objetivo(self):
        datos = demanda.construir_variables(serie_demanda())
        self.assertEqual(len(datos), 400 - 168)
        self.assertFalse(datos[demanda.VARIABLES + ["objetivo"]].isna().any().any())
        fila = datos.iloc[0]
        # Primera fila con historial completo: posición 167 de la serie original.
        self.assertEqual(fila.demanda_lag_1, 30000 + 167)
        self.assertEqual(fila.demanda_lag_24, 30000 + 167 - 23)
        self.assertEqual(fila.demanda_lag_168, 30000)
        self.assertEqual(fila.objetivo, 30000 + 168)
        self.assertEqual(fila.media_24h, np.mean(np.arange(144, 168) + 30000))

    def test_division_cronologica_y_entrenamiento(self):
        datos = demanda.construir_variables(serie_demanda())
        x_train, x_test, y_train, y_test, corte = demanda.dividir(datos)
        self.assertEqual(len(x_train) + len(x_test), len(datos))
        self.assertLess(datos.datetime.iloc[corte - 1], datos.datetime.iloc[corte])
        modelo = demanda.entrenar(x_train, y_train, n_estimators=5)
        metricas = demanda.evaluar(modelo, x_test, y_test)
        self.assertTrue(all(np.isfinite(valor) for valor in metricas.values()))

    def test_guardado_con_el_formato_que_lee_la_api(self):
        datos = demanda.construir_variables(serie_demanda())
        x_train, _, y_train, _, _ = demanda.dividir(datos)
        modelo = demanda.entrenar(x_train, y_train, n_estimators=3)
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = demanda.guardar(modelo, Path(carpeta) / "sub" / "m.joblib")
            artefacto = joblib.load(ruta)
        self.assertEqual(artefacto["variables"], demanda.VARIABLES)
        self.assertEqual(artefacto["horizonte_horas"], 1)
        self.assertEqual(artefacto["version"], "0.1")


class EntrenamientoPrecioTests(unittest.TestCase):
    def test_variables_de_calendario_y_entrenamiento(self):
        indice = pd.date_range("2025-01-01", periods=168, freq="h", name="datetime")
        precios = pd.DataFrame({"Península": np.linspace(100, 200, 168)}, index=indice)
        datos = precio.construir_variables(precios)
        self.assertEqual(list(datos[precio.VARIABLES].columns), precio.VARIABLES)
        self.assertEqual(datos.dia_mes.min(), 1)
        self.assertEqual(datos.dia_mes.max(), 7)
        x_train, x_test, y_train, y_test = precio.dividir(datos)
        self.assertEqual((len(x_train), len(x_test)), (134, 34))
        modelo = precio.entrenar(x_train, y_train, n_estimators=3)
        metricas = precio.evaluar(modelo, x_test, y_test)
        self.assertTrue(all(np.isfinite(valor) for valor in metricas.values()))


if __name__ == "__main__":
    unittest.main()

"""Registro de previsiones de demanda en la tabla `predictions` (Supabase/PostgreSQL).

Usa la tabla ya definida en schema_bbdd.sql y no crea ninguna otra:

    predictions("timestamp", model_name, predicted_demand, actual_demand, created_at)
    UNIQUE ("timestamp", model_name)

- Solo se guardan previsiones actuales; las simulaciones históricas nunca se registran.
- Para cada hora queda la última previsión emitida por el modelo.
- `actual_demand` se completa cuando REData publica la demanda real de esa hora.

Sin DATABASE_URL, o si la base de datos no responde, la API sigue funcionando y el
registro informa de que no está disponible.
"""

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd


LOGGER = logging.getLogger(__name__)
MODEL_NAME = "modelo_demanda_v01"

UPSERT_PREDICTION = """
    INSERT INTO predictions ("timestamp", model_name, predicted_demand)
    VALUES (%s, %s, %s)
    ON CONFLICT ("timestamp", model_name) DO UPDATE
    SET predicted_demand = EXCLUDED.predicted_demand, created_at = now()
"""
PENDING_ACTUALS = """
    SELECT "timestamp" FROM predictions
    WHERE model_name = %s AND actual_demand IS NULL AND "timestamp" <= %s
"""
UPDATE_ACTUAL = """
    UPDATE predictions SET actual_demand = %s
    WHERE "timestamp" = %s AND model_name = %s
"""
TRACKING = """
    SELECT "timestamp", predicted_demand, actual_demand FROM predictions
    WHERE model_name = %s AND "timestamp" >= %s
    ORDER BY "timestamp"
"""


class PredictionStore:
    def __init__(self, database_url: str | None, model_name: str = MODEL_NAME,
                 connect=None):
        self.database_url = (database_url or "").strip()
        self.model_name = model_name
        self._connect = connect
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.database_url)

    def _connection(self):
        if self._connect is not None:
            return self._connect()
        import psycopg2  # imported lazily: the API must start without a database

        return psycopg2.connect(self.database_url, connect_timeout=5)

    def _fail(self, action: str, error: Exception) -> None:
        # Only the error type and first line: never the connection string.
        detail = str(error).strip().splitlines()[0][:160] if str(error).strip() else ""
        self.last_error = f"{type(error).__name__}{': ' + detail if detail else ''}"
        LOGGER.warning("Registro de predicciones: no se pudo %s (%s).", action,
                       self.last_error)

    def save_forecast(self, forecast: dict) -> int:
        """Guarda las horas de una previsión actual. Devuelve las filas escritas."""
        if not self.configured or forecast.get("prediction_mode") != "current_forecast":
            return 0
        rows = [(record["timestamp_utc"], self.model_name, float(record["demand_mw"]))
                for record in forecast.get("predictions", [])]
        if not rows:
            return 0
        try:
            connection = self._connection()
            try:
                with connection.cursor() as cursor:
                    cursor.executemany(UPSERT_PREDICTION, rows)
                connection.commit()
            finally:
                connection.close()
        except Exception as error:
            self._fail("guardar la previsión", error)
            return 0
        self.last_error = None
        return len(rows)

    def record_actuals(self, observed: pd.DataFrame) -> int:
        """Completa actual_demand con la demanda real publicada por REData."""
        if not self.configured or observed.empty:
            return 0
        values = dict(zip(pd.to_datetime(observed.timestamp_utc, utc=True),
                          observed.value.astype(float)))
        try:
            connection = self._connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(PENDING_ACTUALS, (self.model_name, datetime.now(UTC)))
                    pending = [pd.Timestamp(row[0]).tz_convert("UTC")
                               for row in cursor.fetchall()]
                    updates = [(values[stamp], stamp.to_pydatetime(), self.model_name)
                               for stamp in pending if stamp in values]
                    if updates:
                        cursor.executemany(UPDATE_ACTUAL, updates)
                connection.commit()
            finally:
                connection.close()
        except Exception as error:
            self._fail("completar la demanda real", error)
            return 0
        self.last_error = None
        return len(updates)

    def tracking(self, days: int = 7) -> dict:
        """Previsto frente a real de los últimos días y error medio del modelo."""
        result = {"configured": self.configured, "available": False, "error": None,
                  "model_name": self.model_name, "window_days": days, "stored": 0,
                  "records": [], "metrics": None}
        if not self.configured:
            return result
        since = datetime.now(UTC) - timedelta(days=days)
        try:
            connection = self._connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(TRACKING, (self.model_name, since))
                    rows = cursor.fetchall()
            finally:
                connection.close()
        except Exception as error:
            self._fail("leer el registro", error)
            result["error"] = self.last_error
            return result
        records = [{"timestamp_utc": pd.Timestamp(stamp).tz_convert("UTC").isoformat(),
                    "predicted_mw": None if predicted is None else float(predicted),
                    "actual_mw": None if actual is None else float(actual)}
                   for stamp, predicted, actual in rows]
        compared = [record for record in records
                    if record["predicted_mw"] is not None and record["actual_mw"] is not None]
        if compared:
            errors = [abs(r["predicted_mw"] - r["actual_mw"]) for r in compared]
            relative = [abs(r["predicted_mw"] - r["actual_mw"]) / r["actual_mw"]
                        for r in compared if r["actual_mw"] > 0]
            result["metrics"] = {
                "compared_hours": len(compared),
                "mae_mw": sum(errors) / len(errors),
                "mape_pct": 100 * sum(relative) / len(relative) if relative else None,
            }
        result.update(available=True, stored=len(records), records=records)
        self.last_error = None
        return result

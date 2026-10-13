-- ============================================================
-- Predicción de demanda y precio de la electricidad en España
-- Esquema de base de datos (PostgreSQL / Supabase)
-- Basado en el modelo de datos del documento NF2 (apartado 7)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS weather_data (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "timestamp" TIMESTAMPTZ NOT NULL,
    city        TEXT NOT NULL DEFAULT 'madrid',
    temperature REAL,
    humidity    REAL,
    radiation   REAL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE ("timestamp", city)
);

CREATE INDEX IF NOT EXISTS idx_weather_data_timestamp
    ON weather_data ("timestamp");

CREATE TABLE IF NOT EXISTS electricity_demand (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "timestamp" TIMESTAMPTZ NOT NULL UNIQUE,
    demand_mw   REAL,
    price_mwh   REAL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_electricity_demand_timestamp
    ON electricity_demand ("timestamp");

CREATE TABLE IF NOT EXISTS demanda_clima_historico (
    timestamp_utc TIMESTAMPTZ PRIMARY KEY,
    demanda_mw    REAL,
    precio_mwh    REAL,
    temp_media_c  REAL
);

CREATE TABLE IF NOT EXISTS features_ml (
    timestamp_utc TIMESTAMPTZ PRIMARY KEY
        REFERENCES demanda_clima_historico (timestamp_utc)
        ON DELETE CASCADE,
    lag_24h       REAL,
    lag_168h      REAL,
    hora_sin      REAL,
    hora_cos      REAL,
    dia_semana    SMALLINT CHECK (dia_semana BETWEEN 0 AND 6),
    es_festivo    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS predictions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "timestamp"      TIMESTAMPTZ NOT NULL,
    model_name       TEXT NOT NULL,
    predicted_demand REAL,
    actual_demand    REAL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE ("timestamp", model_name)
);

CREATE INDEX IF NOT EXISTS idx_predictions_timestamp
    ON predictions ("timestamp");

-- ============================================================
-- Fin del esquema
-- ============================================================

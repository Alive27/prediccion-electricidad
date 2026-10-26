# Predicción de demanda eléctrica peninsular

Aplicación de preview para consultar demanda real de la España peninsular, analizar el histórico de 2024 y generar una previsión horaria de 5, 12, 24 o 48 horas con el modelo entrenado existente. FastAPI obtiene y valida las fuentes; Streamlit consume la API interna.

## Datos y modelo

- **Demanda reciente peninsular:** endpoint GET [`/es/datos/demanda/evolucion`](https://apidatos.ree.es/es/datos/demanda/evolucion), con `time_trunc=hour`, `geo_trunc=electric_system`, `geo_limit=peninsular` y `geo_ids=8741`. El widget `demanda-tiempo-real` devuelve datos nacionales y falla con HTTP 502 al aplicarle el filtro peninsular en esta consulta; no se usa esa serie nacional para el modelo peninsular. `evolucion` devuelve el indicador horario regional `Demanda`; no requiere token. El servicio conserva en caché el instante UTC y el `last-update` que informa REData.
- **Curvas recientes oficiales:** ESIOS `/indicators/1293` (demanda real peninsular) y `/indicators/544` (demanda prevista peninsular), conservando los instantes recibidos y midiendo la resolución desde ellos. Requieren `ESIOS_API_TOKEN` personal. El endpoint interno `/curves` informa el estado y error de cada curva y persiste una caché separada; nunca interpola ni presenta puntos ausentes como observados.
- **Histórico:** `data/raw/redata_demanda_2024.parquet` y `data/raw/openmeteo_madrid_2024.parquet` permanecen intactos. La página Demanda y el análisis histórico usan el archivo de 2024 y muestran su cobertura; nunca se usan para afirmar que la demanda es actual.
- **Meteorología interna del modelo:** temperatura horaria a 2 m de Madrid desde [Open-Meteo Forecast API](https://open-meteo.com/en/docs). El servicio no comercial no requiere clave; requiere atribución CC BY 4.0. Es una variable de entrada del modelo, no una sección pública del dashboard.
- **Previsión:** `models/modelo_demanda_v01.joblib`, sin reentrenar ni cambiar el contrato. El artefacto es `RandomForestRegressor` y espera, en este orden, `hora`, `dia_semana`, `mes`, `temperature_2m`, `demanda_lag_1`, `demanda_lag_24`, `demanda_lag_168` y `media_24h`. La preparación conserva las definiciones del notebook (desplazamientos 23/167 y media móvil de 24 filas). La API predice recursivamente hasta 48 horas.

REData consulta una ventana de 14 días, tiene timeouts y un reintento acotado que conserva el código HTTP y el detalle final. Se persiste la última respuesta válida en `data/cache/redata_demanda_live.parquet`, separada del Parquet original de 2024. El estado distingue la conexión, la última observación, su antigüedad, la última consulta y el `last-update` de REData. El límite de frescura por defecto es 6 horas: una previsión actual exige observaciones recientes y temperaturas futuras. Si el último dato está obsoleto, el usuario puede confirmar explícitamente una simulación histórica; ésta usa temperaturas del archivo Open-Meteo Archive, conserva el origen/fecha de referencia y nunca se etiqueta como previsión actual. Si faltan horas o temperaturas, la API explica la carencia y no interpola ni inventa datos.

## Dashboard

- **Resumen:** última observación real y variación respecto a la hora anterior solo cuando ambas observaciones consecutivas existen; máximo y mínimo de la previsión real de 48 horas cuando está disponible.
- **Demanda:** exploración del Parquet histórico de 2024, con sus fechas visibles.
- **Análisis histórico:** perfil horario, semanal, mensual, extremos y distribución de demanda. No muestra análisis meteorológico.
- **Modelo:** selector de horizonte de 5, 12, 24 o 48 horas; la API calcula la salida seleccionada con resolución horaria, tabla por instante y resúmenes derivados de esas salidas. La simulación histórica necesita confirmación y muestra fecha de referencia.
- **Precios:** sección pendiente; no consulta ni muestra series de precio. El modelo predice demanda en MW, no precios en €/MWh.
- **Datos y Estado del sistema:** cobertura del archivo 2024, liveness de la API, preparación del modelo, conexión de fuentes y frescura. La evaluación técnica continúa disponible en `GET /evaluation`, fuera de la navegación pública.

Las curvas temporales permiten desplazamiento y zoom, tooltips unificados y botón «Restablecer vista». El histórico ofrece intervalos de 24/48 horas, 3/7 días y periodos más largos. Las horas se almacenan y comparan en UTC; la interfaz las presenta en `Europe/Madrid`, incluyendo la zona en el tooltip y las horas de cambio estacional.

## Configuración

Copia `.env.example` a `.env` si quieres cambiar los valores predeterminados. REData no requiere token; ESIOS sí necesita un token personal (`ESIOS_API_TOKEN`). Solicítalo a REE/ESIOS y añádelo solo a `.env` local o al gestor de secretos del despliegue, nunca al repositorio. Sin token, ESIOS se marca como no configurado y se conserva una caché anterior si existe.

```text
MAX_DEMAND_AGE_HOURS=6
DEMAND_LOOKBACK_DAYS=14
API_TIMEOUT_SECONDS=35
LIVE_DEMAND_CACHE_PATH=/cache/redata_demanda_live.parquet
```

Para desarrollo local, instala las dependencias y ejecuta FastAPI:

```powershell
python -m pip install -r src/api/requirements.txt
python -m pip install -r src/dashboard/requirements.txt
$env:MODEL_PATH = (Resolve-Path .\models\modelo_demanda_v01.joblib).Path
$env:DATA_DIR = (Resolve-Path .\data\raw).Path
uvicorn main:app --app-dir src/api --host 127.0.0.1 --port 8000
```

En otra terminal configura `$env:API_BASE_URL = "http://localhost:8000"` y ejecuta `streamlit run src/dashboard/app.py`. Dentro de Compose el dashboard utiliza `http://api:8000`.

## Docker Compose

Los montajes de `models/` y `data/raw/` son de solo lectura. Arranca y prueba localmente:

```powershell
docker compose config --quiet
docker compose up --build
```

Dashboard: <http://localhost:8501>. API: <http://localhost:8000/docs>. `/health` es liveness y no depende de fuentes externas; `/ready` distingue la capacidad de simulación histórica de la disponibilidad de previsión actual. Compose monta el caché REData en `data/cache/`, separado y escribible, mientras conserva `data/raw/` como solo lectura.

## API

- `GET /health`: liveness de FastAPI y estado de carga del artefacto.
- `GET /ready`: informa conexión de REData/Open-Meteo, última observación, frescura, entradas históricas para simulación y disponibilidad de previsión actual. Devuelve 503 cuando no puede producirse una previsión actual.
- `GET /model/info`: tipo de modelo y contrato validado de variables.
- `GET /history?limit=168`: observaciones de demanda con procedencia; admite `start` y `end` ISO 8601.
- `GET /curves?hours=168`: indicadores ESIOS 1293 y 544, separados y con metadatos de resolución, frescura y conexión; requiere `ESIOS_API_TOKEN`.
- `GET /comparison?hours=48`: comparación REData/ESIOS únicamente en marcas temporales UTC exactamente coincidentes.
- `POST /predict`: cuerpo `{"horizon_hours":48}`; horizontes permitidos 5, 12, 24 o 48, en horas. Para datos obsoletos se requiere además `{"allow_stale_simulation":true}` y las temperaturas históricas exactas deben estar disponibles.
- `GET /evaluation`: evaluación retrospectiva técnica, no expuesta en las páginas públicas.

## Pruebas

```powershell
python -m unittest discover -s src/api/tests
python -m unittest discover -s src/dashboard/tests
flake8 src/ --max-line-length=100
docker compose config --quiet
docker compose up --build
```

Las pruebas de API que utilizan el artefacto requieren el modelo local. La validación de la conexión real requiere conectividad a Internet; REData no requiere credenciales. Si la fuente no está disponible, el dashboard muestra HTTP/error y la fecha de caché más reciente sin presentar 2024 como actual.

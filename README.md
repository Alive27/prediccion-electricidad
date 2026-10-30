# ⚡ Predicción de demanda y precio de la electricidad en España

<!-- Insignias de las tecnologías -->
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=Streamlit&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)

Aplicación de preview para consultar el estado de los modelos y datos, y generar predicciones horarias de consumo y de precios. FastAPI sirve ambos modelos de forma independiente; Streamlit consume la API interna.

---

## 🎯 Objetivo

El objetivo general del proyecto es desarrollar y desplegar un sistema que prediga la demanda eléctrica horaria de España peninsular para las próximas 24 horas. Como objetivo secundario, el sistema también buscará predecir el precio horario del mercado diario. Finalmente, las predicciones se expondrán mediante una API REST y un dashboard interactivo.

## 👥 Miembros del equipo

| Nombre | Rol | Responsabilidades clave |
| :--- | :--- | :--- |
| **David Lopez Solera** | 🗄️ Data Engineer | Ingesta, copia local, limpieza, alineación horaria y almacenamiento. |
| **Kevin Tortosa Villanueva** | ⚙️ Platform / MLOps | Repositorio, tablero, Docker, CI, secretos, despliegue y reuniones. |
| **Pablo Fernandez Fernandez** | 🧠 ML Engineer | Análisis exploratorio, baseline, modelos y evaluación. |
| **Juan Carlos Castro Pazo** | 📊 API y Dashboard | Endpoints de FastAPI, dashboard y visualizaciones. |

## 📂 Organización del repositorio

El repositorio, llamado `prediccion-electricidad`, seguirá la siguiente estructura de carpetas y archivos base recomendada:

```text
📦 prediccion-electricidad
 ┣ 📂 docs/                 # Documentación (NF1, cronograma, memoria, diagramas)
 ┣ 📂 src/                  # Código fuente principal
 ┃ ┣ 📂 ingestion/          # Conectores REData, ESIOS, Open-Meteo
 ┃ ┣ 📂 features/           # Ingeniería de características
 ┃ ┣ 📂 models/             # Modelos de Machine Learning
 ┃ ┣ 📂 api/                # FastAPI
 ┃ ┗ 📂 dashboard/          # Interfaz de usuario (Streamlit)
 ┣ 📂 data/                 # Histórico de datos (raw y processed) - No se versionará
 ┣ 📂 notebooks/            # Análisis Exploratorio de Datos (EDA) y experimentos
 ┣ 📂 tests/                # Directorio destinado a las pruebas del sistema
 ┣ 📂 .github/workflows/    # Configuración para la integración continua (CI)
 ┣ 📜 README.md             # Este archivo principal de descripción del proyecto
 ┣ 📜 .gitignore            # Archivo para omitir subida de datos pesados/secretos
 ┣ 📜 docker-compose.yml    # Archivo base para levantar el entorno (API y dashboard)
 ┗ 📜 .env.example          # Plantilla para variables de entorno y claves privadas
```

---

## ⚙️ Datos y modelo

*   **Demanda reciente peninsular:** endpoint `GET /es/datos/demanda/evolucion`, con `time_trunc=hour`, `geo_trunc=electric_system`, `geo_limit=peninsular` y `geo_ids=8741`. El widget demanda-tiempo-real devuelve datos nacionales y falla con HTTP 502 al aplicarle el filtro peninsular en esta consulta; no se usa esa serie nacional para el modelo peninsular. `evolucion` devuelve el indicador horario regional Demanda; no requiere token. El servicio conserva en caché el instante UTC y el *last-update* que informa REData.
*   **Curvas recientes oficiales:** ESIOS `/indicators/1293` (demanda real peninsular) y `/indicators/544` (demanda prevista peninsular), conservando los instantes recibidos y midiendo la resolución desde ellos. Requieren `ESIOS_API_TOKEN` personal. El endpoint interno `/curves` informa el estado y error de cada curva y persiste una caché separada; nunca interpola ni presenta puntos ausentes como observados.
*   **Histórico:** `data/raw/redata_demanda_2024.parquet` y `data/raw/openmeteo_madrid_2024.parquet` permanecen intactos. La página Demanda y el análisis histórico usan el archivo de 2024 y muestran su cobertura; nunca se usan para afirmar que la demanda es actual.
*   **Meteorología interna del modelo:** temperatura horaria a 2 m de Madrid desde Open-Meteo Forecast API. El servicio no comercial no requiere clave; requiere atribución CC BY 4.0. Es una variable de entrada del modelo, no una sección pública del dashboard.
*   **Previsión:** `models/modelo_demanda_v01.joblib`, sin reentrenar ni cambiar el contrato. El artefacto es `RandomForestRegressor` y espera, en este orden: `hora`, `dia_semana`, `mes`, `temperature_2m`, `demanda_lag_1`, `demanda_lag_24`, `demanda_lag_168` y `media_24h`. La preparación conserva las definiciones del notebook (desplazamientos 23/167 y media móvil de 24 filas). La API predice recursivamente hasta 48 horas.

> **Comportamiento de REData:** Consulta una ventana de 14 días, tiene timeouts y un reintento acotado que conserva el código HTTP y el detalle final. Se persiste la última respuesta válida en `data/cache/redata_demanda_live.parquet`, separada del Parquet original de 2024. El estado distingue la conexión, la última observación, su antigüedad, la última consulta y el *last-update* de REData. 
> 
> *Nota sobre frescura de datos:* El límite por defecto es de 6 horas. Una previsión actual exige observaciones recientes y temperaturas futuras. Si el último dato está obsoleto, el usuario puede confirmar explícitamente una simulación histórica; ésta usa temperaturas del archivo Open-Meteo Archive, conserva el origen/fecha de referencia y nunca se etiqueta como previsión actual. Si faltan horas o temperaturas, la API explica la carencia y no interpola ni inventa datos.

---

## 🖥️ Dashboard

*   **Resumen:** Última demanda real disponible y resumen/gráfica de su previsión horaria cuando el modelo y sus entradas están listos.
*   **Modelo:** Predicción de consumo con el artefacto existente, selector de 6, 12, 24 o 48 horas, resumen, gráficas y tabla horaria. Las simulaciones con datos antiguos requieren confirmación explícita.
*   **Precios:** Predicción automática de 24 horas con `models/modelo_precio_v01.joblib`; permite seleccionar 6, 12, 24 o 48 horas y muestra resumen, gráfica y tabla en €/MWh. El modelo usa `hora`, `dia_semana`, `mes` y `dia_mes`; no utiliza demanda, meteorología, histórico de precios ni conexión a ESIOS.
*   El notebook de precios no documenta la zona horaria del índice de entrenamiento: las fechas futuras usan `Europe/Madrid` como supuesto explícito, por lo que la compatibilidad temporal exacta no está validada.
*   **Datos y Estado del sistema:** Cobertura de los datos cargados, disponibilidad de la API, fuentes y estado independiente de los modelos de consumo y precios.

> **Interfaz de Usuario (UI):** Las curvas temporales permiten desplazamiento y zoom, tooltips unificados y botón «Restablecer vista». El histórico ofrece intervalos de 24/48 horas, 3/7 días y periodos más largos. Las horas se almacenan y comparan en UTC; la interfaz las presenta en Europe/Madrid, incluyendo la zona en el tooltip y las horas de cambio estacional.

---

## 🛠️ Configuración e Instalación

Copia `.env.example` a `.env` si quieres cambiar los valores predeterminados. REData no requiere token; ESIOS sí necesita un token personal (`ESIOS_API_TOKEN`). Solicítalo a REE/ESIOS y añádelo solo a `.env` local o al gestor de secretos del despliegue, **nunca al repositorio**. Sin token, ESIOS se marca como no configurado y se conserva una caché anterior si existe.

Variables de entorno principales:
```env
MAX_DEMAND_AGE_HOURS=6
DEMAND_LOOKBACK_DAYS=14
API_TIMEOUT_SECONDS=35
LIVE_DEMAND_CACHE_PATH=/cache/redata_demanda_live.parquet
PRICE_MODEL_PATH=/models/modelo_precio_v01.joblib
```

### Desarrollo local (Sin Docker)

Instala las dependencias:
```bash
python -m pip install -r src/api/requirements.txt
python -m pip install -r src/dashboard/requirements.txt
```

Configura las variables de entorno para el modelo y datos (ejemplo PowerShell), y ejecuta FastAPI:
```powershell
$env:MODEL_PATH = (Resolve-Path .\models\modelo_demanda_v01.joblib).Path
$env:DATA_DIR = (Resolve-Path .\data\raw).Path
uvicorn main:app --app-dir src/api --host 127.0.0.1 --port 8000
```

En otra terminal, configura la URL de la API y ejecuta Streamlit:
```powershell
$env:API_BASE_URL = "http://localhost:8000"
streamlit run src/dashboard/app.py
```
*(Dentro de Compose el dashboard utiliza `http://api:8000`)*.

---

## 🐳 Docker Compose

Los montajes de `models/` y `data/raw/` son de solo lectura. Para arrancar y probar localmente:

```bash
docker compose config --quiet
docker compose up --build
```

*   **Dashboard:** [http://localhost:8501](http://localhost:8501)
*   **API (Docs):** [http://localhost:8000/docs](http://localhost:8000/docs)

`/health` es liveness y no depende de fuentes externas; `/ready` distingue la capacidad de simulación histórica de la disponibilidad de previsión actual. Compose monta el caché REData en `data/cache/`, separado y escribible, mientras conserva `data/raw/` como solo lectura.

---

## 🔌 API Endpoints

*   `GET /health`: Liveness de FastAPI y estado de carga del artefacto.
*   `GET /ready`: Informa conexión de REData/Open-Meteo, última observación, frescura, entradas históricas para simulación y disponibilidad de previsión actual. Devuelve 503 cuando no puede producirse una previsión actual.
*   `GET /model/info`: Tipo de modelo y contrato validado de variables.
*   `GET /history?limit=168`: Observaciones de demanda con procedencia; admite `start` y `end` ISO 8601.
*   `GET /curves?hours=168`: Indicadores ESIOS 1293 y 544, separados y con metadatos de resolución, frescura y conexión; requiere `ESIOS_API_TOKEN`.
*   `GET /comparison?hours=48`: Comparación REData/ESIOS únicamente en marcas temporales UTC exactamente coincidentes.
*   `POST /predict`: Cuerpo `{"horizon_hours":48}`; horizontes permitidos 6, 12, 24 o 48, en horas. Para datos obsoletos se requiere además `{"allow_stale_simulation":true}` y las temperaturas históricas exactas deben estar disponibles.
*   `GET /evaluation`: Evaluación retrospectiva técnica, no expuesta en las páginas públicas.
*   `GET /prices/history?days=1`: histórico PVPC real del indicador ESIOS 1001 para Península; admite 1, 2, 3 o 7 días y devuelve unidad, origen, frescura y timestamps UTC/local.
*   `GET /prices/model`: estado y esquema del modelo de precios cargado.
*   `POST /prices/predict`: cuerpo `{"horizon_hours":24}`; acepta 6, 12, 24 o 48 y devuelve estimaciones etiquetadas como salida del modelo.

ESIOS requiere `ESIOS_API_TOKEN`. El indicador 1001 es PVPC (término de energía de la tarifa regulada); no debe confundirse con el indicador 600, que es el mercado diario SPOT. El artefacto solo tiene variables temporales, así que estas estimaciones son una proyección calendario basada en el Random Forest entrenado y no incorporan condiciones futuras de oferta/demanda. La zona horaria futura se asume Europe/Madrid porque el notebook no deja constancia de la conversión temporal aplicada al índice.

---

## 🧪 Pruebas

Para ejecutar las pruebas del proyecto:

```bash
python -m unittest discover -s src/api/tests
python -m unittest discover -s src/dashboard/tests
```

Comprobación de estilo de código:
```bash
flake8 src/ --max-line-length=100
```

Validación de Docker:
```bash
docker compose config --quiet
docker compose up --build
```

*Nota:* Las pruebas de API que utilizan el artefacto requieren el modelo local. La validación de la conexión real requiere conectividad a Internet; REData no requiere credenciales. Si la fuente no está disponible, el dashboard muestra HTTP/error y la fecha de caché más reciente sin presentar 2024 como actual.

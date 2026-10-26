# ⚡ Predicción de demanda y precio de la electricidad en España

<!-- Opcional: Aquí puedes añadir un banner del proyecto o insignias de las tecnologías -->
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=Streamlit&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)

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

## Preview provisional: API de demanda y dashboard

Esta integración es una **preview**, una versión previa y provisional para revisión y aprobación. Mantiene el artefacto entrenado `models/modelo_demanda_v01.joblib`; no entrena ni sustituye el modelo. El estimador predice una hora por paso. La API itera hasta 48 horas, usando las predicciones anteriores como lags y temperatura pronosticada por Open-Meteo.

El modelo de notebook usa horas UTC, `value` como demanda (se expone como MW, según los datos REData) y `temperature_2m`. Para reproducir exactamente el entrenamiento, los lags son los desplazamientos de 23 y 167 filas, y `media_24h` incluye la fila actual. Un hueco, duplicado o valor no finito impide predecir.

La dependencia de Scikit-learn se fija en `1.9.0`, la versión registrada por el artefacto guardado.

### Arranque local

Instala dependencias en el entorno Python activo:

```powershell
python -m pip install -r src/api/requirements.txt
python -m pip install -r src/dashboard/requirements.txt
```

La ruta predeterminada del artefacto es `/models/modelo_demanda_v01.joblib` y la de los datos `/data/raw`, adecuadas para contenedores. En local, configura `MODEL_PATH` y `DATA_DIR` a las rutas absolutas del checkout; por ejemplo:

```powershell
$env:MODEL_PATH = (Resolve-Path .\models\modelo_demanda_v01.joblib).Path
$env:DATA_DIR = (Resolve-Path .\data\raw).Path
uvicorn main:app --app-dir src/api --host 127.0.0.1 --port 8000
```

En otra terminal, configura `API_BASE_URL=http://localhost:8000` y ejecuta `streamlit run src/dashboard/app.py`. La UI usa `API_BASE_URL` para consultar FastAPI; en Compose se configura `http://api:8000` automáticamente. La API consulta REData (demanda reciente) y Open-Meteo (temperatura; [documentación de Forecast API](https://open-meteo.com/en/docs)) sin claves. Se puede ajustar `MAX_DEMAND_AGE_HOURS` (predeterminado 6) y `DEMAND_LOOKBACK_DAYS` (predeterminado 10). Las peticiones externas tienen timeout y, si fallan, no se simulan datos.

### Docker Compose

El modelo y `data/raw` se montan en solo lectura desde el checkout. Confirma que el modelo y los Parquet estén en las rutas predeterminadas y ejecuta:

```powershell
docker compose up --build
```

Dashboard: <http://localhost:8501>. API y Swagger: <http://localhost:8000/docs>. Compose solo pasa las variables de configuración indicadas; no inyecta `.env` ni secretos.

### Endpoints

- `GET /health`: estado del proceso, modelo, demanda más reciente y disponibilidad de futuro.
- `GET /model/info`: tipo y variables verificadas del modelo.
- `GET /history?limit=168`: observaciones REData disponibles; admite también `start` y `end` en ISO 8601.
- `POST /predict`: cuerpo `{"horizon_hours":24}`; rango permitido 1–48.
- `GET /evaluation`: métricas MAE/RMSE del test cronológico de 2024 frente al baseline.

La previsión requiere hasta 168 observaciones horarias consecutivas, demanda reciente (máximo 6 horas por defecto) y temperatura alineada. Con solo los Parquet de 2024, en 2026 el endpoint `/predict` devuelve 503 con la fecha real del último dato disponible; esa salida no se rotula como previsión actual. La evaluación es histórica, de un paso con datos observados y no representa el rendimiento de un horizonte futuro de 24 horas.

### Pruebas y revisión

Ejecuta `python -m unittest discover -s src/api/tests`, `python -m unittest discover -s src/dashboard/tests` y `flake8 src/ --max-line-length=100`. El smoke test del modelo requiere las dependencias de `src/api/requirements.txt` y el artefacto original. Las cifras de `/evaluation` se calculan al consultar ese artefacto y los datos históricos locales; no están hardcodeadas.

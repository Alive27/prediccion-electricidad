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

# prediccion-electricidad

# Predicción de demanda y precio de la electricidad en España

## Objetivo

El objetivo general del proyecto es desarrollar y desplegar un sistema que prediga la demanda eléctrica horaria de España peninsular para las próximas 24 horas. Como objetivo secundario, el sistema también buscará predecir el precio horario del mercado diario. Finalmente, las predicciones se expondrán mediante una API REST y un dashboard interactivo.

## Miembros del equipo

**David Lopez Solera**: Data Engineer (encargado de la ingesta, copia local, limpieza, alineación horaria y almacenamiento).

**Kevin Tortosa Villanueva**: Platform / MLOps y coordinación (encargado del repositorio, tablero, Docker, CI, secretos, despliegue y reuniones).

**Pablo Fernandez Fernandez**: ML Engineer (encargado del análisis exploratorio, baseline, modelos y evaluación).

**Juan Carlos Castro Pazo**: API y Dashboard (encargado de los endpoints de FastAPI, dashboard y visualizaciones).

## Cómo se organizará el repositorio

El repositorio, llamado prediccion-electricidad, seguirá esta estructura de carpetas y archivos base recomendada:   

**- docs/**: Carpeta destinada a la documentación (NF1, cronograma, memoria, diagramas). Aquí se ubicará el documento NF1_Presentacion_y_Viabilidad.pdf.

**- src/**: Directorio principal del código fuente. Estará subdividido internamente en ingestion/ (conectores REData, ESIOS, Open-Meteo), features/, models/, api/ (FastAPI) y dashboard/.

**- data/**: Directorio para almacenar el histórico de datos en sus capas raw y processed. Esta carpeta no se versionará y puede iniciarse vacía o con un archivo .gitkeep.

**- notebooks/**: Carpeta reservada para el Análisis Exploratorio de Datos (EDA) y otros experimentos.

**- tests/**: Directorio destinado a las pruebas del sistema.

**- .github/workflows/**: Directorio de configuración para la integración continua (CI).

**- README.md**: Este archivo principal de descripción del proyecto.   

**- .gitignore**: Archivo para omitir la subida de datos pesados, secretos o archivos innecesarios al control de versiones.

**- docker-compose.yml**: Archivo base para levantar el entorno del proyecto (API y dashboard).

**- .env.example**: Archivo de plantilla para las variables de entorno y claves privadas (como los tokens de las APIs).

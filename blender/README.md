# Ciudad 3D del dashboard (Blender)

El dashboard solo necesita los archivos ya generados en `src/dashboard/static/city/`
(`energy_city.glb` y `energy_city.json`). Esta carpeta guarda los scripts para regenerarlos.

| Archivo | Uso |
|---|---|
| `city_masterplan.py` | Plano urbano: red viaria, distritos y parcelas. `python blender/city_masterplan.py` imprime el plano y lo valida. |
| `build_city.py` | Construye la escena en Blender, la valida y exporta el GLB, el JSON de rutas, el `.blend` y renders de inspección. |
| `audit_models.py` | Auditoría de dimensiones, pivotes y materiales de los modelos Kenney. |

## Regenerar la ciudad

1. Descarga los kits de Kenney (licencia CC0) y descomprímelos en `src/dashboard/` con estos nombres:
   `kenney_city-kit-roads`, `kenney_city-kit-commercial_2.1`, `kenney_city-kit-industrial_2.0`,
   `kenney_city-kit-suburban_20`
   (https://kenney.nl/assets/city-kit-roads, city-kit-commercial, city-kit-industrial, city-kit-suburban).
2. Ejecuta Blender 5.x en modo headless desde la raíz del repositorio:

   ```bash
   blender --background --factory-startup --python blender/build_city.py
   ```

   Opciones: `-- --no-render` (sin renders) y `-- --no-export` (no sobrescribe el GLB).

Los kits, el `.blend`, los renders y los informes generados no se versionan (ver `.gitignore`).

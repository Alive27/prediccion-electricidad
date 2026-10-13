"""Read-only discovery, independent of the current working directory."""

import os
from pathlib import Path


FILENAMES = {
    "demand": "redata_demanda_2024.parquet",
    "weather": "openmeteo_madrid_2024.parquet",
}
OPTIONAL_PATH_VARIABLES = ("DASHBOARD_DATA_DIR", "RAW_DATA_DIR", "DATA_DIR", "DATA_PATH")


def candidate_paths(kind: str) -> tuple[Path, ...]:
    """Optional existing environment paths precede local and container paths."""
    filename = FILENAMES[kind]
    candidates = []
    for name in OPTIONAL_PATH_VARIABLES:
        value = os.environ.get(name)
        if value:
            configured = Path(value).expanduser()
            if configured.suffix.lower() == ".parquet":
                if configured.name == filename:
                    candidates.append(configured)
            else:
                candidates.extend((configured / filename, configured / "raw" / filename))
    dashboard = Path(__file__).resolve().parent.parent
    # Docker places the dashboard directly in /app, which has only one ancestor.
    project_roots = tuple(dashboard.parents)[1:2]
    roots = (*project_roots, dashboard, Path.cwd(), Path("/app"), Path("/"))
    for root in roots:
        candidates.append(root / "data" / "raw" / filename)
        candidates.append(root / "data" / filename)
    candidates.append(Path("/data/raw") / filename)
    return tuple(dict.fromkeys(path.resolve() for path in candidates))

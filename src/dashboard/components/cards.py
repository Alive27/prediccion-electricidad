"""Display state of a local dataset, shared by the Datos and Estado del sistema pages."""

from services.data_quality import Dataset


def dataset_status(dataset: Dataset) -> tuple[str, str]:
    """(label, tone) of a dataset; tone is success, warning, error or pending."""
    if dataset.available:
        return ("Cargado · revisar avisos", "warning") if dataset.warnings or dataset.errors \
            else ("Cargado", "success")
    return ("Error de lectura", "error") if dataset.errors else ("No disponible", "pending")

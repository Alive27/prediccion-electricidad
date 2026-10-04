"""Shared progressive disclosure for source provenance and existing quality checks."""

import pandas as pd
import streamlit as st

from services.data_quality import Dataset
from utils.formatting import LOCAL_TZ, frequency, number, timestamp


def discard_manual(kind: str) -> None:
    st.session_state.pop(f"manual_{kind}", None)
    st.session_state.pop(f"upload_{kind}", None)


def technical_details(dataset: Dataset, label: str = "Ver detalles técnicos") -> None:
    with st.expander(label):
        quality = dataset.quality
        if quality:
            details = {
                "Zona horaria original": quality["timezone"] or "Sin declarar",
                "Rango original": f"{timestamp(quality['start'], True)} — "
                                  f"{timestamp(quality['end'], True)}",
                "Frecuencia aproximada": frequency(quality["step"]),
                "Celdas nulas": number(sum(quality["nulls"].values())),
                "Filas duplicadas": number(quality["duplicate_rows"]),
                "Fechas duplicadas": number(quality["duplicate_times"]),
                "Registros excluidos del análisis": number(quality["invalid_records"]),
                "Huecos temporales": number(quality["missing_slots"])
                if quality["missing_slots"] is not None else "Sin determinar",
            }
            for name, value in details.items():
                st.markdown(f"**{name}:** {value}")
            schema = pd.DataFrame([
                {"Columna": column, "Tipo": dtype, "Nulos": quality["nulls"][column]}
                for column, dtype in quality["columns"].items()
            ])
            st.table(schema.set_index("Columna"))
        if dataset.loaded_at is not None:
            loaded_at = pd.Timestamp(dataset.loaded_at).tz_convert(LOCAL_TZ)
            st.caption(f"Hora de lectura: {timestamp(loaded_at, True)}")
        if dataset.source:
            st.markdown("**Origen utilizado**")
            st.code(dataset.source, language=None)
        if dataset.source == "Carga manual · solo en memoria":
            st.button("Descartar carga temporal", key=f"discard_{dataset.kind}",
                      type="tertiary", icon=":material/close:",
                      help="Retira este archivo de la sesión para poder cargar otro histórico.",
                      on_click=discard_manual, args=(dataset.kind,))
        for message in (*dataset.errors, *dataset.warnings):
            st.write(message)
        if dataset.checked_paths:
            st.markdown("**Rutas comprobadas**")
            for path in dataset.checked_paths:
                st.code(path, language=None)

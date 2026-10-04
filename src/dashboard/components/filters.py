"""Useful, small period controls shared by the analytical views."""

import pandas as pd
import streamlit as st

from components.states import empty
from services.analysis import PERIODS, select_period
from services.data_quality import Dataset
from utils.formatting import date_range, number


def period_filter(dataset: Dataset, key: str,
                  default: str = "7 días") -> pd.DataFrame:
    period = st.segmented_control(
        "Periodo", PERIODS, default=default, required=True, key=f"period_{key}",
        help="Periodos rápidos desde el último dato disponible.",
    )
    dates = None
    if period == "Personalizado":
        first = dataset.frame.timestamp.min().date()
        last = dataset.frame.timestamp.max().date()
        selected = st.date_input("Rango de fechas", (first, last), min_value=first,
                                 max_value=last, format="DD/MM/YYYY", key=f"dates_{key}")
        if len(selected) != 2:
            st.info("Selecciona la fecha de inicio y de fin.")
            return dataset.frame.iloc[:0]
        dates = tuple(selected)
    frame = select_period(dataset.frame, period, dates)
    zone = "Hora peninsular" if dataset.quality.get("timezone") else "Zona horaria sin declarar"
    st.caption(f"{date_range(frame)} · {number(len(frame))} registros · {zone}")
    if frame.empty:
        empty("Sin registros en este periodo", "Prueba otro periodo o ajusta las fechas.", "chart")
    return frame

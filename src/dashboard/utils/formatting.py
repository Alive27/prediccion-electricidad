"""Spanish labels without dependence on the host locale."""

import math

import pandas as pd


LOCAL_TZ = "Europe/Madrid"
WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def day_label(value: pd.Timestamp) -> str:
    return f"{value.day} {MONTHS[value.month - 1][:3]} {value.year}"


def month_label(value: str, full: bool = False) -> str:
    year, month = value.split("-")
    name = MONTHS[int(month) - 1]
    return f"{name.capitalize()} {year}" if full else name[:3].capitalize()


def number(value: float, decimals: int = 0) -> str:
    if value is None or not math.isfinite(float(value)):
        return "Sin datos"
    return f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def timestamp(value: pd.Timestamp, with_zone: bool = False) -> str:
    if pd.isna(value):
        return "Sin datos"
    suffix = " %Z" if with_zone and value.tzinfo is not None else ""
    return value.strftime("%d/%m/%Y · %H:%M" + suffix)


def date_range(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "Sin datos en este periodo"
    return f"{timestamp(frame.timestamp.min())} — {timestamp(frame.timestamp.max())}"


def frequency(step: pd.Timedelta | None) -> str:
    if step is None:
        return "Sin determinar"
    seconds = step.total_seconds()
    if seconds % 3600 == 0:
        hours = int(seconds / 3600)
        return f"{hours} hora" if hours == 1 else f"{hours} horas"
    if seconds % 60 == 0:
        return f"{number(seconds / 60)} minutos"
    return f"{number(seconds)} segundos"

"""Period selection and descriptive statistics on validated observations."""

from datetime import date, timedelta

import pandas as pd


PERIODS = ["24 horas", "7 días", "30 días", "Todo el periodo", "Personalizado"]
HOURS = {"24 horas": 24, "7 días": 168, "30 días": 720}


def select_period(frame: pd.DataFrame, period: str,
                  dates: tuple[date, date] | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame
    if period in HOURS:
        end = frame.timestamp.max()
        return frame.loc[frame.timestamp.gt(end - pd.Timedelta(hours=HOURS[period]))]
    if period == "Personalizado" and dates is not None:
        return select_dates(frame, dates)
    return frame


def select_dates(frame: pd.DataFrame, dates: tuple[date, date]) -> pd.DataFrame:
    if frame.empty:
        return frame
    tz = frame.timestamp.dt.tz
    start = pd.Timestamp(dates[0]).tz_localize(tz)
    end = pd.Timestamp(dates[1] + timedelta(days=1)).tz_localize(tz)
    return frame.loc[frame.timestamp.ge(start) & frame.timestamp.lt(end)]


def calendar_profile(frame: pd.DataFrame, by: str) -> pd.DataFrame:
    calendar = getattr(frame.timestamp.dt, by)
    return frame.groupby(calendar).value.agg(["mean", "count"]).sort_index()


def monthly_profile(frame: pd.DataFrame) -> pd.DataFrame:
    month = frame.timestamp.dt.strftime("%Y-%m")
    return frame.groupby(month).value.agg(["mean", "min", "max", "count"])

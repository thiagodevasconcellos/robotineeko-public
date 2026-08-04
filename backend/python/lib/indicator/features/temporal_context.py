from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ...calculator import Calculator


TOKYO_TZ = ZoneInfo('Asia/Tokyo')
LONDON_TZ = ZoneInfo('Europe/London')
NEW_YORK_TZ = ZoneInfo('America/New_York')


def _coerce_utc_timestamps(raw_time_series: pd.Series) -> pd.Series:
    numeric_time = pd.to_numeric(raw_time_series, errors='coerce')
    valid_time = numeric_time.dropna()

    if valid_time.empty:
        return pd.Series(pd.NaT, index=raw_time_series.index, dtype='datetime64[ns, UTC]')

    median_abs = float(valid_time.abs().median())
    unit = 'ms' if median_abs >= 1_000_000_000_000 else 's'
    return pd.to_datetime(numeric_time, unit=unit, utc=True, errors='coerce')


def _session_flag(local_timestamp_series: pd.Series, start_hour: int, end_hour: int) -> pd.Series:
    local_hour = local_timestamp_series.dt.hour
    valid_mask = local_hour.notna()
    session_mask = (local_hour >= int(start_hour)) & (local_hour < int(end_hour))
    return pd.Series(
        np.where(valid_mask, session_mask.astype(float), np.nan),
        index=local_timestamp_series.index,
        dtype=float,
    )


class TemporalContext(Calculator):
    """
    Exposes small, reusable intraday time-context features for strategies:
    - hour_utc / minute_utc / minute_of_day_utc / weekday_utc / day_of_month_utc
    - tokyo_session / london_session / new_york_session
    - tokyo_london_overlap / london_new_york_overlap
    """

    def __init__(self, symbol):
        super().__init__('TemporalContext')

        timestamps_utc = _coerce_utc_timestamps(symbol.time)
        london_time = timestamps_utc.dt.tz_convert(LONDON_TZ)
        new_york_time = timestamps_utc.dt.tz_convert(NEW_YORK_TZ)
        tokyo_time = timestamps_utc.dt.tz_convert(TOKYO_TZ)

        utc_hour = timestamps_utc.dt.hour.astype(float)
        utc_minute = timestamps_utc.dt.minute.astype(float)
        minute_of_day_utc = ((utc_hour * 60.0) + utc_minute).astype(float)
        weekday_utc = timestamps_utc.dt.weekday.astype(float)
        day_of_month_utc = timestamps_utc.dt.day.astype(float)

        tokyo_session = _session_flag(tokyo_time, start_hour=8, end_hour=17)
        london_session = _session_flag(london_time, start_hour=8, end_hour=17)
        new_york_session = _session_flag(new_york_time, start_hour=8, end_hour=17)

        tokyo_london_overlap = tokyo_session * london_session
        london_new_york_overlap = london_session * new_york_session

        symbol.add_feature(f'{self.name}_hour_utc', utc_hour)
        symbol.add_feature(f'{self.name}_minute_utc', utc_minute)
        symbol.add_feature(f'{self.name}_minute_of_day_utc', minute_of_day_utc)
        symbol.add_feature(f'{self.name}_weekday_utc', weekday_utc)
        symbol.add_feature(f'{self.name}_day_of_month_utc', day_of_month_utc)
        symbol.add_feature(f'{self.name}_tokyo_session', tokyo_session)
        symbol.add_feature(f'{self.name}_london_session', london_session)
        symbol.add_feature(f'{self.name}_new_york_session', new_york_session)
        symbol.add_feature(f'{self.name}_tokyo_london_overlap', tokyo_london_overlap)
        symbol.add_feature(f'{self.name}_london_new_york_overlap', london_new_york_overlap)

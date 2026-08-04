from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ...calculator import Calculator
from .temporal_context import _coerce_utc_timestamps


TOKYO_TZ = ZoneInfo('Asia/Tokyo')
LONDON_TZ = ZoneInfo('Europe/London')
NEW_YORK_TZ = ZoneInfo('America/New_York')

SESSION_CONFIG = {
    'tokyo': (TOKYO_TZ, 8, 17),
    'london': (LONDON_TZ, 8, 17),
    'new_york': (NEW_YORK_TZ, 8, 17),
}


def _resolve_session_config(raw_session_name: str | None):
    safe_session_name = str(raw_session_name or 'tokyo').strip().lower()
    if safe_session_name not in SESSION_CONFIG:
        safe_session_name = 'tokyo'
    return safe_session_name, SESSION_CONFIG[safe_session_name]


class SessionOpeningRangeStateV1(Calculator):
    """
    Per-session opening-range state with simple breakout and reclaim flags.
    """

    def __init__(self, symbol, sessionName='tokyo', rangeBars=3):
        safe_session_name, session_config = _resolve_session_config(sessionName)
        safe_range_bars = max(1, int(rangeBars or 1))
        super().__init__('SessionOpeningRangeStateV1', safe_session_name, safe_range_bars)

        session_tz, start_hour, end_hour = session_config
        candles = symbol.candles.copy().reset_index(drop=True)
        frame = candles.loc[:, ['time', 'open', 'high', 'low', 'close']].copy()
        frame['time'] = pd.to_numeric(frame['time'], errors='coerce')
        frame['open'] = pd.to_numeric(frame['open'], errors='coerce')
        frame['high'] = pd.to_numeric(frame['high'], errors='coerce')
        frame['low'] = pd.to_numeric(frame['low'], errors='coerce')
        frame['close'] = pd.to_numeric(frame['close'], errors='coerce')

        timestamps_utc = _coerce_utc_timestamps(frame['time'])
        local_time = timestamps_utc.dt.tz_convert(session_tz)
        local_hour = local_time.dt.hour
        session_flag = ((local_hour >= int(start_hour)) & (local_hour < int(end_hour))).astype(float)
        session_flag = session_flag.where(local_hour.notna(), np.nan)

        count = len(frame.index)
        session_bar_index = np.full(count, np.nan, dtype=float)
        range_high = np.full(count, np.nan, dtype=float)
        range_low = np.full(count, np.nan, dtype=float)
        range_mid = np.full(count, np.nan, dtype=float)
        width_ratio = np.full(count, np.nan, dtype=float)
        position_ratio = np.full(count, np.nan, dtype=float)
        range_ready_flag = np.zeros(count, dtype=float)
        breakout_up_flag = np.zeros(count, dtype=float)
        breakout_down_flag = np.zeros(count, dtype=float)
        reclaim_up_flag = np.zeros(count, dtype=float)
        reclaim_down_flag = np.zeros(count, dtype=float)

        current_bar_index = -1
        current_range_high = np.nan
        current_range_low = np.nan
        previous_in_session = False

        highs = frame['high'].to_numpy(dtype=float)
        lows = frame['low'].to_numpy(dtype=float)
        closes = frame['close'].to_numpy(dtype=float)
        session_mask = session_flag.fillna(0.0).to_numpy(dtype=float) > 0.0

        for index in range(count):
            in_session = bool(session_mask[index])
            if not in_session:
                previous_in_session = False
                current_bar_index = -1
                current_range_high = np.nan
                current_range_low = np.nan
                continue

            high_value = float(highs[index]) if np.isfinite(highs[index]) else np.nan
            low_value = float(lows[index]) if np.isfinite(lows[index]) else np.nan
            close_value = float(closes[index]) if np.isfinite(closes[index]) else np.nan

            if not previous_in_session:
                current_bar_index = 0
                current_range_high = high_value
                current_range_low = low_value
            else:
                current_bar_index += 1
                if current_bar_index < safe_range_bars:
                    if np.isfinite(high_value):
                        current_range_high = (
                            high_value
                            if not np.isfinite(current_range_high)
                            else max(current_range_high, high_value)
                        )
                    if np.isfinite(low_value):
                        current_range_low = (
                            low_value
                            if not np.isfinite(current_range_low)
                            else min(current_range_low, low_value)
                        )

            session_bar_index[index] = float(current_bar_index)
            range_high[index] = current_range_high
            range_low[index] = current_range_low
            if np.isfinite(current_range_high) and np.isfinite(current_range_low):
                current_range_width = max(current_range_high - current_range_low, 0.0)
                range_mid[index] = current_range_low + (current_range_width / 2.0)
                if np.isfinite(close_value) and close_value != 0:
                    width_ratio[index] = current_range_width / abs(close_value)
                if current_range_width > 0.0 and np.isfinite(close_value):
                    position_ratio[index] = (close_value - current_range_low) / current_range_width
            else:
                current_range_width = np.nan

            ready = current_bar_index >= safe_range_bars - 1
            if ready:
                range_ready_flag[index] = 1.0
                previous_close = float(closes[index - 1]) if index > 0 and np.isfinite(closes[index - 1]) else np.nan
                if np.isfinite(close_value) and np.isfinite(current_range_high):
                    if close_value > current_range_high and (
                        not np.isfinite(previous_close) or previous_close <= current_range_high
                    ):
                        breakout_up_flag[index] = 1.0
                    if np.isfinite(low_value) and low_value <= current_range_high and close_value > current_range_high:
                        reclaim_up_flag[index] = 1.0
                if np.isfinite(close_value) and np.isfinite(current_range_low):
                    if close_value < current_range_low and (
                        not np.isfinite(previous_close) or previous_close >= current_range_low
                    ):
                        breakout_down_flag[index] = 1.0
                    if np.isfinite(high_value) and high_value >= current_range_low and close_value < current_range_low:
                        reclaim_down_flag[index] = 1.0

            previous_in_session = True

        feature_prefix = self.name
        symbol.add_feature(f'{feature_prefix}_session_flag', pd.Series(session_flag, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_session_bar_index', pd.Series(session_bar_index, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_range_high', pd.Series(range_high, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_range_low', pd.Series(range_low, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_range_mid', pd.Series(range_mid, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_width_ratio', pd.Series(width_ratio, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_position_ratio', pd.Series(position_ratio, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_range_ready_flag', pd.Series(range_ready_flag, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_breakout_up_flag', pd.Series(breakout_up_flag, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_breakout_down_flag', pd.Series(breakout_down_flag, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_reclaim_up_flag', pd.Series(reclaim_up_flag, index=frame.index, dtype=float))
        symbol.add_feature(f'{feature_prefix}_reclaim_down_flag', pd.Series(reclaim_down_flag, index=frame.index, dtype=float))

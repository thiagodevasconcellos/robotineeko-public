import numpy as np
import pandas as pd

from ...calculator import Calculator


def _compute_atr(frame: pd.DataFrame, window: int) -> pd.Series:
    previous_close = frame['close'].shift(1)
    true_range = pd.concat(
        [
            frame['high'] - frame['low'],
            (frame['high'] - previous_close).abs(),
            (frame['low'] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(window=window, min_periods=1).mean()
    return atr.replace(0.0, np.nan)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0.0:
        return np.nan
    return float(numerator / denominator)


class ElliottWaveProxyV1(Calculator):
    """
    Causal Elliott-style wave proxy based on ATR-confirmed swing reversals.

    This intentionally does not try to label exact Elliott waves. Instead, it
    exposes a reusable swing/leg structure that can be used by strategies or
    research without future-looking pivots or repaint-dependent wave counts.
    """

    def __init__(
        self,
        symbol,
        atrWindow=14,
        reversalAtrMultiplier=1.5,
        minimumBars=3,
        breakoutAtrMultiplier=0.25,
        retestAtrMultiplier=0.5,
        projectionRangeMultiplier=1.0,
    ):
        safe_atr_window = max(2, int(atrWindow or 2))
        safe_reversal_multiplier = max(0.1, float(reversalAtrMultiplier or 0.1))
        safe_minimum_bars = max(1, int(minimumBars or 1))
        safe_breakout_multiplier = max(0.0, float(breakoutAtrMultiplier or 0.0))
        safe_retest_multiplier = max(0.0, float(retestAtrMultiplier or 0.0))
        safe_projection_multiplier = max(0.0, float(projectionRangeMultiplier or 0.0))
        super().__init__(
            'ElliottWaveProxyV1',
            safe_atr_window,
            safe_reversal_multiplier,
            safe_minimum_bars,
            safe_breakout_multiplier,
            safe_retest_multiplier,
            safe_projection_multiplier,
        )

        candles = symbol.candles.copy().reset_index(drop=True)
        frame = candles.loc[:, ['time', 'open', 'high', 'low', 'close']].copy()
        for column in ['time', 'open', 'high', 'low', 'close']:
            frame[column] = pd.to_numeric(frame[column], errors='coerce')

        atr = _compute_atr(frame, safe_atr_window)
        count = len(frame.index)

        last_confirmed_swing_high = np.full(count, np.nan, dtype=float)
        last_confirmed_swing_low = np.full(count, np.nan, dtype=float)
        swing_high_flag = np.zeros(count, dtype=float)
        swing_low_flag = np.zeros(count, dtype=float)
        active_leg_direction = np.zeros(count, dtype=float)
        active_leg_age_bars = np.full(count, np.nan, dtype=float)
        active_leg_size_atr = np.full(count, np.nan, dtype=float)
        retracement_ratio = np.full(count, np.nan, dtype=float)
        impulse_score = np.full(count, np.nan, dtype=float)
        correction_score = np.full(count, np.nan, dtype=float)
        wave_confidence = np.full(count, np.nan, dtype=float)
        candidate_impulse_flag = np.zeros(count, dtype=float)
        candidate_correction_flag = np.zeros(count, dtype=float)
        bull_breakout_trigger = np.full(count, np.nan, dtype=float)
        bear_breakout_trigger = np.full(count, np.nan, dtype=float)
        bull_broken_resistance_level = np.full(count, np.nan, dtype=float)
        bear_broken_support_level = np.full(count, np.nan, dtype=float)
        bull_support_envelope_low = np.full(count, np.nan, dtype=float)
        bull_support_envelope_high = np.full(count, np.nan, dtype=float)
        bear_resistance_envelope_low = np.full(count, np.nan, dtype=float)
        bear_resistance_envelope_high = np.full(count, np.nan, dtype=float)
        bull_projection_target = np.full(count, np.nan, dtype=float)
        bear_projection_target = np.full(count, np.nan, dtype=float)
        bull_breakout_flag = np.zeros(count, dtype=float)
        bear_breakout_flag = np.zeros(count, dtype=float)
        bull_breakout_state = np.zeros(count, dtype=float)
        bear_breakout_state = np.zeros(count, dtype=float)
        bull_retest_flag = np.zeros(count, dtype=float)
        bear_retest_flag = np.zeros(count, dtype=float)

        highs = frame['high'].to_numpy(dtype=float)
        lows = frame['low'].to_numpy(dtype=float)
        closes = frame['close'].to_numpy(dtype=float)
        atr_values = atr.to_numpy(dtype=float)

        trend = 0
        seed_high = np.nan
        seed_low = np.nan
        seed_high_index = -1
        seed_low_index = -1

        last_swing_high_price = np.nan
        last_swing_low_price = np.nan
        anchor_price = np.nan
        anchor_index = -1
        extreme_price = np.nan
        extreme_index = -1
        breakout_direction = 0
        breakout_level = np.nan
        breakout_range = np.nan

        for index in range(count):
            high_value = float(highs[index]) if np.isfinite(highs[index]) else np.nan
            low_value = float(lows[index]) if np.isfinite(lows[index]) else np.nan
            close_value = float(closes[index]) if np.isfinite(closes[index]) else np.nan
            atr_value = float(atr_values[index]) if np.isfinite(atr_values[index]) else np.nan

            if not (np.isfinite(high_value) and np.isfinite(low_value) and np.isfinite(close_value)):
                last_confirmed_swing_high[index] = last_swing_high_price
                last_confirmed_swing_low[index] = last_swing_low_price
                continue

            if not np.isfinite(seed_high):
                seed_high = high_value
                seed_low = low_value
                seed_high_index = index
                seed_low_index = index

            reversal_amount = max(safe_reversal_multiplier * atr_value, 1e-9) if np.isfinite(atr_value) else np.nan

            if trend == 0:
                if high_value >= seed_high:
                    seed_high = high_value
                    seed_high_index = index
                if low_value <= seed_low:
                    seed_low = low_value
                    seed_low_index = index

                upward_move = high_value - seed_low
                downward_move = seed_high - low_value
                can_start_up = (
                    np.isfinite(reversal_amount)
                    and upward_move >= reversal_amount
                    and (index - seed_low_index) >= safe_minimum_bars
                )
                can_start_down = (
                    np.isfinite(reversal_amount)
                    and downward_move >= reversal_amount
                    and (index - seed_high_index) >= safe_minimum_bars
                )

                if can_start_up or can_start_down:
                    if can_start_up and (not can_start_down or upward_move >= downward_move):
                        trend = 1
                        anchor_price = seed_low
                        anchor_index = seed_low_index
                        extreme_price = high_value
                        extreme_index = index
                        last_swing_low_price = anchor_price
                    else:
                        trend = -1
                        anchor_price = seed_high
                        anchor_index = seed_high_index
                        extreme_price = low_value
                        extreme_index = index
                        last_swing_high_price = anchor_price

            elif trend > 0:
                if high_value >= extreme_price or not np.isfinite(extreme_price):
                    extreme_price = high_value
                    extreme_index = index

                if np.isfinite(reversal_amount):
                    reversal_distance = extreme_price - low_value
                    if (
                        reversal_distance >= reversal_amount
                        and (index - extreme_index) >= safe_minimum_bars
                    ):
                        last_swing_high_price = extreme_price
                        swing_high_flag[index] = 1.0
                        trend = -1
                        anchor_price = last_swing_high_price
                        anchor_index = extreme_index
                        extreme_price = low_value
                        extreme_index = index

            else:
                if low_value <= extreme_price or not np.isfinite(extreme_price):
                    extreme_price = low_value
                    extreme_index = index

                if np.isfinite(reversal_amount):
                    reversal_distance = high_value - extreme_price
                    if (
                        reversal_distance >= reversal_amount
                        and (index - extreme_index) >= safe_minimum_bars
                    ):
                        last_swing_low_price = extreme_price
                        swing_low_flag[index] = 1.0
                        trend = 1
                        anchor_price = last_swing_low_price
                        anchor_index = extreme_index
                        extreme_price = high_value
                        extreme_index = index

            last_confirmed_swing_high[index] = last_swing_high_price
            last_confirmed_swing_low[index] = last_swing_low_price

            if trend == 0 or not np.isfinite(anchor_price) or not np.isfinite(extreme_price):
                continue

            active_leg_direction[index] = float(trend)
            active_leg_age_bars[index] = float(max(index - anchor_index, 0))

            leg_price_distance = (
                extreme_price - anchor_price if trend > 0 else anchor_price - extreme_price
            )
            leg_price_distance = max(float(leg_price_distance), 0.0)
            active_leg_size_atr[index] = _safe_ratio(leg_price_distance, atr_value)

            if trend > 0:
                retracement_distance = max(extreme_price - close_value, 0.0)
            else:
                retracement_distance = max(close_value - extreme_price, 0.0)

            retracement_ratio[index] = _safe_ratio(retracement_distance, leg_price_distance)
            clipped_retracement = (
                min(max(retracement_ratio[index], 0.0), 1.5)
                if np.isfinite(retracement_ratio[index])
                else np.nan
            )

            size_component = (
                min(active_leg_size_atr[index] / max(safe_reversal_multiplier, 1e-9), 2.0)
                if np.isfinite(active_leg_size_atr[index])
                else np.nan
            )
            age_component = min(active_leg_age_bars[index] / max(safe_minimum_bars, 1), 2.0)
            shallow_retracement_score = (
                1.0 - min(max(clipped_retracement, 0.0), 1.0)
                if np.isfinite(clipped_retracement)
                else np.nan
            )

            if np.isfinite(size_component) and np.isfinite(shallow_retracement_score):
                impulse_score[index] = max(size_component * shallow_retracement_score, 0.0)
            if np.isfinite(clipped_retracement):
                correction_score[index] = clipped_retracement
            if np.isfinite(size_component) and np.isfinite(shallow_retracement_score):
                wave_confidence[index] = np.clip(
                    (0.55 * min(size_component / 2.0, 1.0))
                    + (0.20 * min(age_component / 2.0, 1.0))
                    + (0.25 * shallow_retracement_score),
                    0.0,
                    1.0,
                )

            if (
                np.isfinite(active_leg_size_atr[index])
                and np.isfinite(retracement_ratio[index])
                and active_leg_size_atr[index] >= (safe_reversal_multiplier * 1.5)
                and retracement_ratio[index] <= 0.382
            ):
                candidate_impulse_flag[index] = 1.0
            if np.isfinite(retracement_ratio[index]) and 0.382 <= retracement_ratio[index] <= 0.786:
                candidate_correction_flag[index] = 1.0

            breakout_buffer = (
                safe_breakout_multiplier * atr_value
                if np.isfinite(atr_value)
                else np.nan
            )
            retest_buffer = (
                safe_retest_multiplier * atr_value
                if np.isfinite(atr_value)
                else np.nan
            )
            swing_range = (
                last_swing_high_price - last_swing_low_price
                if np.isfinite(last_swing_high_price)
                and np.isfinite(last_swing_low_price)
                and last_swing_high_price > last_swing_low_price
                else np.nan
            )

            if np.isfinite(last_swing_high_price) and np.isfinite(breakout_buffer):
                bull_breakout_trigger[index] = last_swing_high_price + breakout_buffer
            if np.isfinite(last_swing_low_price) and np.isfinite(breakout_buffer):
                bear_breakout_trigger[index] = last_swing_low_price - breakout_buffer

            if breakout_direction == 1 and np.isfinite(breakout_level) and np.isfinite(retest_buffer):
                support_floor = breakout_level - retest_buffer
                if close_value < support_floor:
                    breakout_direction = 0
                    breakout_level = np.nan
                    breakout_range = np.nan

            if breakout_direction == -1 and np.isfinite(breakout_level) and np.isfinite(retest_buffer):
                resistance_ceiling = breakout_level + retest_buffer
                if close_value > resistance_ceiling:
                    breakout_direction = 0
                    breakout_level = np.nan
                    breakout_range = np.nan

            bullish_breakout = (
                trend > 0
                and np.isfinite(last_swing_high_price)
                and np.isfinite(bull_breakout_trigger[index])
                and close_value >= bull_breakout_trigger[index]
            )
            bearish_breakout = (
                trend < 0
                and np.isfinite(last_swing_low_price)
                and np.isfinite(bear_breakout_trigger[index])
                and close_value <= bear_breakout_trigger[index]
            )

            if bullish_breakout and (
                breakout_direction != 1
                or not np.isfinite(breakout_level)
                or abs(breakout_level - last_swing_high_price) > 1e-12
            ):
                bull_breakout_flag[index] = 1.0
                breakout_direction = 1
                breakout_level = last_swing_high_price
                breakout_range = swing_range if np.isfinite(swing_range) else np.nan
            elif bearish_breakout and (
                breakout_direction != -1
                or not np.isfinite(breakout_level)
                or abs(breakout_level - last_swing_low_price) > 1e-12
            ):
                bear_breakout_flag[index] = 1.0
                breakout_direction = -1
                breakout_level = last_swing_low_price
                breakout_range = swing_range if np.isfinite(swing_range) else np.nan

            if breakout_direction == 1 and np.isfinite(breakout_level):
                bull_breakout_state[index] = 1.0
                bull_broken_resistance_level[index] = breakout_level
                if np.isfinite(retest_buffer):
                    bull_support_envelope_low[index] = breakout_level - retest_buffer
                    bull_support_envelope_high[index] = breakout_level + retest_buffer
                    if (
                        bull_breakout_flag[index] == 0.0
                        and low_value <= bull_support_envelope_high[index]
                        and close_value >= breakout_level
                    ):
                        bull_retest_flag[index] = 1.0
                if np.isfinite(breakout_range):
                    bull_projection_target[index] = breakout_level + (
                        breakout_range * safe_projection_multiplier
                    )

            if breakout_direction == -1 and np.isfinite(breakout_level):
                bear_breakout_state[index] = 1.0
                bear_broken_support_level[index] = breakout_level
                if np.isfinite(retest_buffer):
                    bear_resistance_envelope_low[index] = breakout_level - retest_buffer
                    bear_resistance_envelope_high[index] = breakout_level + retest_buffer
                    if (
                        bear_breakout_flag[index] == 0.0
                        and high_value >= bear_resistance_envelope_low[index]
                        and close_value <= breakout_level
                    ):
                        bear_retest_flag[index] = 1.0
                if np.isfinite(breakout_range):
                    bear_projection_target[index] = breakout_level - (
                        breakout_range * safe_projection_multiplier
                    )

        feature_prefix = self.name
        symbol.add_feature(
            f'{feature_prefix}_last_confirmed_swing_high',
            pd.Series(last_confirmed_swing_high, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_last_confirmed_swing_low',
            pd.Series(last_confirmed_swing_low, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_swing_high_flag',
            pd.Series(swing_high_flag, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_swing_low_flag',
            pd.Series(swing_low_flag, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_active_leg_direction',
            pd.Series(active_leg_direction, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_active_leg_age_bars',
            pd.Series(active_leg_age_bars, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_active_leg_size_atr',
            pd.Series(active_leg_size_atr, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_retracement_ratio',
            pd.Series(retracement_ratio, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_impulse_score',
            pd.Series(impulse_score, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_correction_score',
            pd.Series(correction_score, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_wave_confidence',
            pd.Series(wave_confidence, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_candidate_impulse_flag',
            pd.Series(candidate_impulse_flag, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_candidate_correction_flag',
            pd.Series(candidate_correction_flag, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bull_breakout_trigger',
            pd.Series(bull_breakout_trigger, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bear_breakout_trigger',
            pd.Series(bear_breakout_trigger, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bull_broken_resistance_level',
            pd.Series(bull_broken_resistance_level, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bear_broken_support_level',
            pd.Series(bear_broken_support_level, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bull_support_envelope_low',
            pd.Series(bull_support_envelope_low, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bull_support_envelope_high',
            pd.Series(bull_support_envelope_high, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bear_resistance_envelope_low',
            pd.Series(bear_resistance_envelope_low, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bear_resistance_envelope_high',
            pd.Series(bear_resistance_envelope_high, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bull_projection_target',
            pd.Series(bull_projection_target, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bear_projection_target',
            pd.Series(bear_projection_target, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bull_breakout_flag',
            pd.Series(bull_breakout_flag, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bear_breakout_flag',
            pd.Series(bear_breakout_flag, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bull_breakout_state',
            pd.Series(bull_breakout_state, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bear_breakout_state',
            pd.Series(bear_breakout_state, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bull_retest_flag',
            pd.Series(bull_retest_flag, index=frame.index, dtype=float),
        )
        symbol.add_feature(
            f'{feature_prefix}_bear_retest_flag',
            pd.Series(bear_retest_flag, index=frame.index, dtype=float),
        )

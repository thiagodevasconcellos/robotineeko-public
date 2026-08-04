import numpy as np
import pandas as pd

from ...calculator import Calculator


REGIME_CODE_COMPRESSION = 1.0
REGIME_CODE_RANGE = 0.0
REGIME_CODE_TREND_UP = 2.0
REGIME_CODE_TREND_DOWN = -2.0
REGIME_CODE_VOLATILE_UP = 3.0
REGIME_CODE_VOLATILE_DOWN = -3.0

TREND_THRESHOLD = 0.58
COMPRESSION_THRESHOLD = 0.62
VOLATILITY_THRESHOLD = 0.72
DIRECTION_POSITIVE_THRESHOLD = 0.2
DIRECTION_NEGATIVE_THRESHOLD = -0.2


def _safe_clip(series, low, high):
    return series.clip(lower=low, upper=high)


def _finite_or_zero(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0

    if not np.isfinite(number):
        return 0.0

    return number


class MarketRegime(Calculator):
    """
    MarketRegime v1

    Outputs:
    - trend_score: confidence that the market is directional/trending
    - volatility_score: confidence that current movement amplitude is elevated
    - compression_score: confidence that the market is compressed/coiling
    - direction_score: directional bias from -1 (bearish) to +1 (bullish)
    - stability_score: confidence that the current regime is mature rather than freshly flipping
    - regime_age: bars elapsed since the last confirmed regime transition
    - regime_code:
        -3 = volatile_down
        -2 = trend_down
         0 = range / neutral
         1 = compression
         2 = trend_up
         3 = volatile_up
    """

    def __init__(
        self,
        symbol,
        ema_fast_period=9,
        ema_slow_period=21,
        adx_period=14,
        atr_period=14,
        bollinger_period=20,
        bollinger_std_dev=2,
        donchian_period=20,
        choppiness_period=14,
        supertrend_atr_period=10,
        supertrend_multiplier=3,
        vwap_source='hlc3',
        score_smoothing_period=5,
        regime_confirm_bars=3,
    ):
        super().__init__(
            'MarketRegime',
            ema_fast_period,
            ema_slow_period,
            adx_period,
            atr_period,
            bollinger_period,
            bollinger_std_dev,
            donchian_period,
            choppiness_period,
            supertrend_atr_period,
            supertrend_multiplier,
            vwap_source,
            score_smoothing_period,
            regime_confirm_bars,
        )

        safe_ema_fast = max(1, int(ema_fast_period))
        safe_ema_slow = max(safe_ema_fast + 1, int(ema_slow_period))
        safe_adx = max(2, int(adx_period))
        safe_atr = max(2, int(atr_period))
        safe_bb_period = max(2, int(bollinger_period))
        safe_bb_std = float(bollinger_std_dev)
        safe_donchian = max(2, int(donchian_period))
        safe_choppiness = max(2, int(choppiness_period))
        safe_supertrend_atr = max(2, int(supertrend_atr_period))
        safe_supertrend_multiplier = float(supertrend_multiplier)
        safe_vwap_source = str(vwap_source or 'hlc3').strip().lower()
        safe_score_smoothing = max(1, int(score_smoothing_period))
        safe_regime_confirm_bars = max(1, int(regime_confirm_bars))

        open_ = symbol['open']
        high = symbol['high']
        low = symbol['low']
        close = symbol['close']
        volume = symbol['volume'].fillna(0)
        prev_close = close.shift(1)

        true_range = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.ewm(alpha=1 / safe_atr, adjust=False).mean()
        safe_atr_series = atr.replace(0, np.nan)
        safe_close = close.replace(0, np.nan)

        ema_fast = close.ewm(span=safe_ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=safe_ema_slow, adjust=False).mean()
        ema_gap_ratio = (ema_fast - ema_slow) / safe_atr_series

        plus_dm = (high - high.shift(1))
        minus_dm = (low.shift(1) - low)
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        adx_atr = true_range.ewm(alpha=1 / safe_adx, adjust=False).mean().replace(0, np.nan)
        plus_di = 100 * plus_dm.ewm(alpha=1 / safe_adx, adjust=False).mean() / adx_atr
        minus_di = 100 * minus_dm.ewm(alpha=1 / safe_adx, adjust=False).mean() / adx_atr
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
        adx = dx.ewm(alpha=1 / safe_adx, adjust=False).mean()

        bb_middle = close.rolling(window=safe_bb_period, min_periods=safe_bb_period).mean()
        bb_std = close.rolling(window=safe_bb_period, min_periods=safe_bb_period).std()
        bb_upper = bb_middle + (bb_std * safe_bb_std)
        bb_lower = bb_middle - (bb_std * safe_bb_std)
        bb_width_ratio = (bb_upper - bb_lower) / safe_close

        donchian_upper = high.rolling(window=safe_donchian, min_periods=safe_donchian).max()
        donchian_lower = low.rolling(window=safe_donchian, min_periods=safe_donchian).min()
        donchian_width_ratio = (donchian_upper - donchian_lower) / safe_close

        chop_tr_sum = true_range.rolling(window=safe_choppiness, min_periods=safe_choppiness).sum()
        chop_high = high.rolling(window=safe_choppiness, min_periods=safe_choppiness).max()
        chop_low = low.rolling(window=safe_choppiness, min_periods=safe_choppiness).min()
        choppiness = 100 * (
            np.log10(chop_tr_sum / (chop_high - chop_low).replace(0, np.nan))
            / np.log10(safe_choppiness)
        )
        trendiness = 100 - choppiness

        hl2 = (high + low) / 2
        st_atr = true_range.ewm(alpha=1 / safe_supertrend_atr, adjust=False).mean()
        basic_upper = hl2 + (safe_supertrend_multiplier * st_atr)
        basic_lower = hl2 - (safe_supertrend_multiplier * st_atr)
        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()
        supertrend_direction = np.ones(len(close), dtype=float)
        for index in range(1, len(close)):
            previous_close = close.iloc[index - 1]
            if basic_upper.iloc[index] < final_upper.iloc[index - 1] or previous_close > final_upper.iloc[index - 1]:
                final_upper.iloc[index] = basic_upper.iloc[index]
            else:
                final_upper.iloc[index] = final_upper.iloc[index - 1]

            if basic_lower.iloc[index] > final_lower.iloc[index - 1] or previous_close < final_lower.iloc[index - 1]:
                final_lower.iloc[index] = basic_lower.iloc[index]
            else:
                final_lower.iloc[index] = final_lower.iloc[index - 1]

            if close.iloc[index] > final_upper.iloc[index - 1]:
                supertrend_direction[index] = 1.0
            elif close.iloc[index] < final_lower.iloc[index - 1]:
                supertrend_direction[index] = -1.0
            else:
                supertrend_direction[index] = supertrend_direction[index - 1]
        supertrend_direction = pd.Series(supertrend_direction, index=close.index)

        if safe_vwap_source == 'close':
            vwap_price = close
        elif safe_vwap_source == 'ohlc4':
            vwap_price = (open_ + high + low + close) / 4
        else:
            vwap_price = (high + low + close) / 3
        cumulative_volume = volume.cumsum().replace(0, np.nan)
        vwap = (vwap_price * volume).cumsum() / cumulative_volume
        vwap_distance_ratio = (close - vwap) / safe_atr_series

        raw_trend_score = _safe_clip(
            (
                _safe_clip(adx / 50, 0, 1) * 0.4
                + _safe_clip(trendiness / 100, 0, 1) * 0.35
                + _safe_clip(ema_gap_ratio.abs() / 2, 0, 1) * 0.25
            ),
            0,
            1,
        )
        raw_volatility_score = _safe_clip(
            (
                _safe_clip((safe_atr_series / safe_close) / 0.01, 0, 1) * 0.6
                + _safe_clip(bb_width_ratio / 0.02, 0, 1) * 0.4
            ),
            0,
            1,
        )
        raw_compression_score = _safe_clip(
            (
                1 - _safe_clip(bb_width_ratio / 0.02, 0, 1)
            ) * 0.55
            + (
                1 - _safe_clip(donchian_width_ratio / 0.03, 0, 1)
            ) * 0.45,
            0,
            1,
        )

        raw_direction_score = _safe_clip(
            _safe_clip(ema_gap_ratio / 2, -1, 1) * 0.45
            + supertrend_direction * 0.35
            + _safe_clip(vwap_distance_ratio / 2, -1, 1) * 0.20,
            -1,
            1,
        )

        trend_score = raw_trend_score.ewm(span=safe_score_smoothing, adjust=False).mean()
        volatility_score = raw_volatility_score.ewm(span=safe_score_smoothing, adjust=False).mean()
        compression_score = raw_compression_score.ewm(span=safe_score_smoothing, adjust=False).mean()
        direction_score = raw_direction_score.ewm(span=safe_score_smoothing, adjust=False).mean()

        raw_regime_code = pd.Series(
            np.full(len(close), REGIME_CODE_RANGE, dtype=float),
            index=close.index,
        )
        trend_up_mask = (trend_score >= TREND_THRESHOLD) & (direction_score > DIRECTION_POSITIVE_THRESHOLD)
        trend_down_mask = (trend_score >= TREND_THRESHOLD) & (direction_score < DIRECTION_NEGATIVE_THRESHOLD)
        compression_mask = (compression_score >= COMPRESSION_THRESHOLD) & (trend_score < TREND_THRESHOLD)
        volatile_mask = (volatility_score >= VOLATILITY_THRESHOLD) & ~(trend_up_mask | trend_down_mask | compression_mask)

        raw_regime_code.loc[compression_mask] = REGIME_CODE_COMPRESSION
        raw_regime_code.loc[trend_up_mask] = REGIME_CODE_TREND_UP
        raw_regime_code.loc[trend_down_mask] = REGIME_CODE_TREND_DOWN
        raw_regime_code.loc[volatile_mask & (direction_score >= 0)] = REGIME_CODE_VOLATILE_UP
        raw_regime_code.loc[volatile_mask & (direction_score < 0)] = REGIME_CODE_VOLATILE_DOWN

        confirmed_regime = np.full(len(close), REGIME_CODE_RANGE, dtype=float)
        regime_age = np.zeros(len(close), dtype=float)
        stability_score = np.zeros(len(close), dtype=float)
        current_regime = REGIME_CODE_RANGE
        candidate_regime = None
        candidate_count = 0
        current_age = 0

        for index in range(len(close)):
            next_regime = float(raw_regime_code.iloc[index])

            if index == 0:
                current_regime = next_regime
                current_age = 0
            elif next_regime == current_regime:
                candidate_regime = None
                candidate_count = 0
                current_age += 1
            else:
                if candidate_regime == next_regime:
                    candidate_count += 1
                else:
                    candidate_regime = next_regime
                    candidate_count = 1

                if candidate_count >= safe_regime_confirm_bars:
                    current_regime = next_regime
                    candidate_regime = None
                    candidate_count = 0
                    current_age = 0
                else:
                    current_age += 1

            confirmed_regime[index] = current_regime
            regime_age[index] = float(current_age)

            conviction_score = max(
                _finite_or_zero(trend_score.iloc[index]),
                _finite_or_zero(volatility_score.iloc[index]),
                _finite_or_zero(compression_score.iloc[index]),
            )
            age_component = np.clip(current_age / max(1, safe_regime_confirm_bars * 2), 0, 1)
            stability_score[index] = float(np.clip((age_component * 0.6) + (conviction_score * 0.4), 0, 1))

        regime_code = pd.Series(confirmed_regime, index=close.index)
        regime_age = pd.Series(regime_age, index=close.index)
        stability_score = pd.Series(stability_score, index=close.index)

        symbol.add_feature(f'{self.name}_trend_score', trend_score)
        symbol.add_feature(f'{self.name}_volatility_score', volatility_score)
        symbol.add_feature(f'{self.name}_compression_score', compression_score)
        symbol.add_feature(f'{self.name}_direction_score', direction_score)
        symbol.add_feature(f'{self.name}_stability_score', stability_score)
        symbol.add_feature(f'{self.name}_regime_age', regime_age)
        symbol.add_feature(f'{self.name}_regime_code', regime_code)

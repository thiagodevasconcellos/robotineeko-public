import numpy as np
import pandas as pd

from ...calculator import Calculator


def _body_high(open_: pd.Series, close: pd.Series) -> pd.Series:
    return pd.concat([open_, close], axis=1).max(axis=1)


def _body_low(open_: pd.Series, close: pd.Series) -> pd.Series:
    return pd.concat([open_, close], axis=1).min(axis=1)


def _bool_flag(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(float)


class CandlestickPatterns(Calculator):
    """
    Classical candlestick pattern map for reversal and continuation studies.

    The indicator writes:
    - aggregate scores for bullish/bearish reversal and continuation
    - exact 0/1 flags for the named classical patterns that feed those scores

    The contract is intentionally compact enough for chart use while still
    exposing strategy-friendly exact flags.
    """

    def __init__(self, symbol, trendLookback=5, bodyAveragePeriod=14):
        super().__init__('CandlestickPatterns', trendLookback, bodyAveragePeriod)

        safe_trend_lookback = max(2, int(trendLookback))
        safe_body_average_period = max(3, int(bodyAveragePeriod))

        open_ = symbol['open'].astype(float)
        high = symbol['high'].astype(float)
        low = symbol['low'].astype(float)
        close = symbol['close'].astype(float)

        body = (close - open_).abs()
        candle_range = (high - low).replace(0, np.nan)
        body_high = _body_high(open_, close)
        body_low = _body_low(open_, close)
        upper_wick = (high - body_high).clip(lower=0.0)
        lower_wick = (body_low - low).clip(lower=0.0)
        body_ratio = (body / candle_range).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        rolling_body_mean = body.rolling(
            window=safe_body_average_period,
            min_periods=1,
        ).mean().shift(1)
        expanding_body_mean = body.expanding(min_periods=1).mean().shift(1)
        average_body = rolling_body_mean.fillna(expanding_body_mean).fillna(body).replace(0, np.nan)

        bull = close > open_
        bear = close < open_
        strong_bull = bull & (body >= average_body * 1.1)
        strong_bear = bear & (body >= average_body * 1.1)
        small_body = body <= average_body * 0.7

        trend_delta = close - close.shift(safe_trend_lookback)
        trend_up = trend_delta > 0
        trend_down = trend_delta < 0

        previous_open = open_.shift(1)
        previous_close = close.shift(1)
        previous_high = high.shift(1)
        previous_low = low.shift(1)
        previous_body = body.shift(1)
        previous_body_high = body_high.shift(1)
        previous_body_low = body_low.shift(1)

        first_open = open_.shift(2)
        first_close = close.shift(2)
        first_body_high = body_high.shift(2)
        first_body_low = body_low.shift(2)

        hammer = (
            trend_down
            & (lower_wick >= body * 2.0)
            & (upper_wick <= (body * 0.6 + candle_range * 0.05))
            & (body_ratio <= 0.45)
            & (body_high >= (low + candle_range * 0.65))
        )

        shooting_star = (
            trend_up
            & (upper_wick >= body * 2.0)
            & (lower_wick <= (body * 0.6 + candle_range * 0.05))
            & (body_ratio <= 0.45)
            & (body_low <= (low + candle_range * 0.35))
        )

        bullish_engulfing = (
            trend_down
            & bear.shift(1)
            & bull
            & (body >= previous_body * 0.9)
            & (open_ <= previous_close)
            & (close >= previous_open)
        )

        bearish_engulfing = (
            trend_up
            & bull.shift(1)
            & bear
            & (body >= previous_body * 0.9)
            & (open_ >= previous_close)
            & (close <= previous_open)
        )

        bullish_harami = (
            trend_down
            & strong_bear.shift(1)
            & bull
            & small_body
            & (body_high <= previous_body_high)
            & (body_low >= previous_body_low)
        )

        bearish_harami = (
            trend_up
            & strong_bull.shift(1)
            & bear
            & small_body
            & (body_high <= previous_body_high)
            & (body_low >= previous_body_low)
        )

        morning_star = (
            trend_down
            & strong_bear.shift(2)
            & small_body.shift(1)
            & bull
            & (close >= ((first_open + first_close) / 2.0))
            & (previous_body_low <= first_close)
            & (close > previous_body_high)
        )

        evening_star = (
            trend_up
            & strong_bull.shift(2)
            & small_body.shift(1)
            & bear
            & (close <= ((first_open + first_close) / 2.0))
            & (previous_body_high >= first_close)
            & (close < previous_body_low)
        )

        rising_three_methods = (
            trend_up
            & strong_bull.shift(4)
            & bear.shift(3)
            & bear.shift(2)
            & bear.shift(1)
            & small_body.shift(3)
            & small_body.shift(2)
            & small_body.shift(1)
            & (high.shift(3) <= high.shift(4))
            & (high.shift(2) <= high.shift(4))
            & (high.shift(1) <= high.shift(4))
            & (low.shift(3) >= low.shift(4))
            & (low.shift(2) >= low.shift(4))
            & (low.shift(1) >= low.shift(4))
            & bull
            & (close >= close.shift(4))
        )

        falling_three_methods = (
            trend_down
            & strong_bear.shift(4)
            & bull.shift(3)
            & bull.shift(2)
            & bull.shift(1)
            & small_body.shift(3)
            & small_body.shift(2)
            & small_body.shift(1)
            & (high.shift(3) <= high.shift(4))
            & (high.shift(2) <= high.shift(4))
            & (high.shift(1) <= high.shift(4))
            & (low.shift(3) >= low.shift(4))
            & (low.shift(2) >= low.shift(4))
            & (low.shift(1) >= low.shift(4))
            & bear
            & (close <= close.shift(4))
        )

        bullish_reversal_score = pd.concat(
            [
                _bool_flag(hammer) * 0.65,
                _bool_flag(bullish_engulfing) * 0.85,
                _bool_flag(bullish_harami) * 0.55,
                _bool_flag(morning_star) * 1.0,
            ],
            axis=1,
        ).max(axis=1)

        bearish_reversal_score = pd.concat(
            [
                _bool_flag(shooting_star) * 0.65,
                _bool_flag(bearish_engulfing) * 0.85,
                _bool_flag(bearish_harami) * 0.55,
                _bool_flag(evening_star) * 1.0,
            ],
            axis=1,
        ).max(axis=1)

        bullish_continuation_score = _bool_flag(rising_three_methods)
        bearish_continuation_score = _bool_flag(falling_three_methods)

        feature_map = {
            'bullish_reversal_score': bullish_reversal_score,
            'bearish_reversal_score': bearish_reversal_score,
            'bullish_continuation_score': bullish_continuation_score,
            'bearish_continuation_score': bearish_continuation_score,
            'hammer': _bool_flag(hammer),
            'shooting_star': _bool_flag(shooting_star),
            'bullish_engulfing': _bool_flag(bullish_engulfing),
            'bearish_engulfing': _bool_flag(bearish_engulfing),
            'bullish_harami': _bool_flag(bullish_harami),
            'bearish_harami': _bool_flag(bearish_harami),
            'morning_star': _bool_flag(morning_star),
            'evening_star': _bool_flag(evening_star),
            'rising_three_methods': _bool_flag(rising_three_methods),
            'falling_three_methods': _bool_flag(falling_three_methods),
        }

        for suffix, values in feature_map.items():
            symbol.add_feature(f'{self.name}_{suffix}', values.astype(float))

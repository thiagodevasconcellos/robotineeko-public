import unittest

import pandas as pd

from backend.python.lib.indicator.features.candlestick_patterns import CandlestickPatterns
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('TEST', 'M1', len(candles), candles=candles)


def candle(time, open_, high, low, close, volume=100):
    return {
        'time': time,
        'open': open_,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume,
    }


class CandlestickPatternsIndicatorTest(unittest.TestCase):
    def test_indicator_creates_expected_columns_and_ranges(self):
        rows = []
        for index in range(1, 40):
            base = 100 + (index * 0.15)
            rows.append(candle(
                index,
                base - 0.05,
                base + 0.20,
                base - 0.18,
                base + 0.04,
                100 + index,
            ))

        symbol = make_symbol(rows)
        indicator = CandlestickPatterns(symbol)
        prefix = indicator.name

        expected_columns = [
            f'{prefix}_bullish_reversal_score',
            f'{prefix}_bearish_reversal_score',
            f'{prefix}_bullish_continuation_score',
            f'{prefix}_bearish_continuation_score',
            f'{prefix}_hammer',
            f'{prefix}_shooting_star',
            f'{prefix}_bullish_engulfing',
            f'{prefix}_bearish_engulfing',
            f'{prefix}_bullish_harami',
            f'{prefix}_bearish_harami',
            f'{prefix}_morning_star',
            f'{prefix}_evening_star',
            f'{prefix}_rising_three_methods',
            f'{prefix}_falling_three_methods',
        ]

        for column_name in expected_columns:
            self.assertIn(column_name, symbol.candles.columns)

        for column_name in expected_columns:
            values = symbol.candles[column_name].dropna()
            self.assertGreater(len(values), 0)
            self.assertTrue(((values >= 0.0) & (values <= 1.0)).all(), column_name)

    def test_detects_bullish_engulfing_and_shooting_star(self):
        bullish_rows = [
            candle(1, 10.00, 10.05, 9.85, 9.90),
            candle(2, 9.90, 9.95, 9.65, 9.70),
            candle(3, 9.70, 9.75, 9.45, 9.50),
            candle(4, 9.50, 9.55, 9.25, 9.30),
            candle(5, 9.40, 9.45, 9.05, 9.10),
            candle(6, 9.00, 9.50, 8.95, 9.45),
        ]
        bullish_symbol = make_symbol(bullish_rows)
        bullish_indicator = CandlestickPatterns(
            bullish_symbol,
            trendLookback=3,
            bodyAveragePeriod=4,
        )
        bullish_prefix = bullish_indicator.name

        self.assertEqual(
            float(bullish_symbol.candles[f'{bullish_prefix}_bullish_engulfing'].iloc[-1]),
            1.0,
        )
        self.assertGreater(
            float(bullish_symbol.candles[f'{bullish_prefix}_bullish_reversal_score'].iloc[-1]),
            0.8,
        )

        bearish_rows = [
            candle(1, 10.00, 10.15, 9.95, 10.10),
            candle(2, 10.10, 10.35, 10.05, 10.30),
            candle(3, 10.30, 10.55, 10.25, 10.50),
            candle(4, 10.50, 10.75, 10.45, 10.70),
            candle(5, 10.70, 10.95, 10.65, 10.90),
            candle(6, 10.92, 11.42, 10.87, 10.88),
        ]
        bearish_symbol = make_symbol(bearish_rows)
        bearish_indicator = CandlestickPatterns(
            bearish_symbol,
            trendLookback=3,
            bodyAveragePeriod=4,
        )
        bearish_prefix = bearish_indicator.name

        self.assertEqual(
            float(bearish_symbol.candles[f'{bearish_prefix}_shooting_star'].iloc[-1]),
            1.0,
        )
        self.assertGreater(
            float(bearish_symbol.candles[f'{bearish_prefix}_bearish_reversal_score'].iloc[-1]),
            0.6,
        )

    def test_detects_rising_and_falling_three_methods(self):
        rising_rows = [
            candle(1, 10.00, 10.12, 9.95, 10.10),
            candle(2, 10.10, 10.32, 10.05, 10.30),
            candle(3, 10.30, 10.52, 10.25, 10.50),
            candle(4, 10.50, 11.25, 10.45, 11.20),
            candle(5, 11.15, 11.20, 10.90, 10.95),
            candle(6, 11.00, 11.05, 10.85, 10.90),
            candle(7, 10.95, 11.00, 10.80, 10.85),
            candle(8, 10.95, 11.40, 10.90, 11.35),
        ]
        rising_symbol = make_symbol(rising_rows)
        rising_indicator = CandlestickPatterns(
            rising_symbol,
            trendLookback=3,
            bodyAveragePeriod=4,
        )
        rising_prefix = rising_indicator.name

        self.assertEqual(
            float(rising_symbol.candles[f'{rising_prefix}_rising_three_methods'].iloc[-1]),
            1.0,
        )
        self.assertEqual(
            float(rising_symbol.candles[f'{rising_prefix}_bullish_continuation_score'].iloc[-1]),
            1.0,
        )

        falling_rows = [
            candle(1, 12.00, 12.05, 11.88, 11.90),
            candle(2, 11.90, 11.95, 11.68, 11.70),
            candle(3, 11.70, 11.75, 11.48, 11.50),
            candle(4, 11.50, 11.55, 10.75, 10.80),
            candle(5, 10.85, 11.10, 10.80, 11.05),
            candle(6, 11.00, 11.15, 10.95, 11.10),
            candle(7, 10.98, 11.08, 10.92, 11.02),
            candle(8, 11.00, 11.05, 10.30, 10.40),
        ]
        falling_symbol = make_symbol(falling_rows)
        falling_indicator = CandlestickPatterns(
            falling_symbol,
            trendLookback=3,
            bodyAveragePeriod=4,
        )
        falling_prefix = falling_indicator.name

        self.assertEqual(
            float(falling_symbol.candles[f'{falling_prefix}_falling_three_methods'].iloc[-1]),
            1.0,
        )
        self.assertEqual(
            float(falling_symbol.candles[f'{falling_prefix}_bearish_continuation_score'].iloc[-1]),
            1.0,
        )


if __name__ == '__main__':
    unittest.main()

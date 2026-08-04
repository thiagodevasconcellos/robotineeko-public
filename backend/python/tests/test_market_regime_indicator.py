import unittest

import pandas as pd

from backend.python.lib.indicator.trend.market_regime import (
    MarketRegime,
    REGIME_CODE_COMPRESSION,
    REGIME_CODE_RANGE,
    REGIME_CODE_TREND_DOWN,
    REGIME_CODE_TREND_UP,
    REGIME_CODE_VOLATILE_DOWN,
    REGIME_CODE_VOLATILE_UP,
)
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('TEST', 'M1', len(candles), candles=candles)


class MarketRegimeIndicatorTest(unittest.TestCase):
    def test_market_regime_creates_expected_columns_and_value_ranges(self):
        rows = []
        for index in range(1, 80):
            base = 100 + (index * 0.3)
            rows.append({
                'time': index,
                'open': base - 0.1,
                'high': base + 0.4,
                'low': base - 0.5,
                'close': base,
                'volume': 100 + index,
            })

        symbol = make_symbol(rows)
        indicator = MarketRegime(symbol)
        indicator_prefix = indicator.name

        expected_columns = [
            f'{indicator_prefix}_trend_score',
            f'{indicator_prefix}_volatility_score',
            f'{indicator_prefix}_compression_score',
            f'{indicator_prefix}_direction_score',
            f'{indicator_prefix}_stability_score',
            f'{indicator_prefix}_regime_age',
            f'{indicator_prefix}_regime_code',
        ]

        for column_name in expected_columns:
            self.assertIn(column_name, symbol.candles.columns)

        trend_score = symbol.candles[f'{indicator_prefix}_trend_score'].dropna()
        volatility_score = symbol.candles[f'{indicator_prefix}_volatility_score'].dropna()
        compression_score = symbol.candles[f'{indicator_prefix}_compression_score'].dropna()
        direction_score = symbol.candles[f'{indicator_prefix}_direction_score'].dropna()
        stability_score = symbol.candles[f'{indicator_prefix}_stability_score'].dropna()
        regime_age = symbol.candles[f'{indicator_prefix}_regime_age'].dropna()
        regime_code = symbol.candles[f'{indicator_prefix}_regime_code'].dropna()

        self.assertTrue(((trend_score >= 0) & (trend_score <= 1)).all())
        self.assertTrue(((volatility_score >= 0) & (volatility_score <= 1)).all())
        self.assertTrue(((compression_score >= 0) & (compression_score <= 1)).all())
        self.assertTrue(((direction_score >= -1) & (direction_score <= 1)).all())
        self.assertTrue(((stability_score >= 0) & (stability_score <= 1)).all())
        self.assertTrue((regime_age >= 0).all())
        self.assertTrue(
            regime_code.isin(
                {
                    REGIME_CODE_VOLATILE_DOWN,
                    REGIME_CODE_TREND_DOWN,
                    REGIME_CODE_RANGE,
                    REGIME_CODE_COMPRESSION,
                    REGIME_CODE_TREND_UP,
                    REGIME_CODE_VOLATILE_UP,
                }
            ).all()
        )

    def test_market_regime_confirmation_prevents_one_bar_flip(self):
        rows = []
        base = 100.0
        for index in range(1, 120):
            if index < 45:
                close = base + index * 0.25
            elif index == 45:
                close = base - 4.0
            else:
                close = base + index * 0.25

            rows.append({
                'time': index,
                'open': close - 0.1,
                'high': close + 0.5,
                'low': close - 0.5,
                'close': close,
                'volume': 100 + index,
            })

        symbol = make_symbol(rows)
        indicator = MarketRegime(symbol, regime_confirm_bars=3)
        regime_code = symbol.candles[f'{indicator.name}_regime_code']

        self.assertEqual(regime_code.iloc[44], regime_code.iloc[46])


if __name__ == '__main__':
    unittest.main()

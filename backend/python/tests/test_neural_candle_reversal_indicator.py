import unittest

import pandas as pd

from backend.python.lib.indicator.features.neural_candle_reversal_v10_1_dr035 import (
    NeuralCandleReversalV10_1DR035,
)
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('TEST', 'M15', len(candles), candles=candles)


class NeuralCandleReversalIndicatorTest(unittest.TestCase):
    def test_indicator_creates_score_columns_with_expected_ranges(self):
        rows = []
        for index in range(1, 320):
            wave = (index % 18) - 9
            base = 100 + (index * 0.04) + (wave * 0.11)
            close = base + ((-1) ** index) * 0.06
            rows.append({
                'time': index,
                'open': close - 0.08,
                'high': close + 0.22,
                'low': close - 0.24,
                'close': close,
                'volume': 100 + (index % 25) * 3,
            })

        symbol = make_symbol(rows)
        indicator = NeuralCandleReversalV10_1DR035(symbol)
        prefix = indicator.name

        expected_columns = [
            f'{prefix}_bear_score',
            f'{prefix}_neutral_score',
            f'{prefix}_bull_score',
            f'{prefix}_direction_score',
        ]
        for column_name in expected_columns:
            self.assertIn(column_name, symbol.candles.columns)

        bear_score = symbol.candles[f'{prefix}_bear_score'].dropna()
        neutral_score = symbol.candles[f'{prefix}_neutral_score'].dropna()
        bull_score = symbol.candles[f'{prefix}_bull_score'].dropna()
        direction_score = symbol.candles[f'{prefix}_direction_score'].dropna()

        self.assertGreater(len(bear_score), 0)
        self.assertGreater(len(neutral_score), 0)
        self.assertGreater(len(bull_score), 0)
        self.assertGreater(len(direction_score), 0)

        self.assertTrue(((bear_score >= 0.0) & (bear_score <= 1.0)).all())
        self.assertTrue(((neutral_score >= 0.0) & (neutral_score <= 1.0)).all())
        self.assertTrue(((bull_score >= 0.0) & (bull_score <= 1.0)).all())
        self.assertTrue(((direction_score >= -1.0) & (direction_score <= 1.0)).all())


if __name__ == '__main__':
    unittest.main()

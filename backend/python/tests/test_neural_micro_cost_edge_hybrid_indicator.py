import unittest

import pandas as pd

from backend.python.lib.indicator.features.neural_micro_cost_edge_hybrid_s7_s4 import (
    NeuralMicroCostEdgeHybridS7S4,
)
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('EURUSD', 'M1', len(candles), candles=candles)


class NeuralMicroCostEdgeHybridIndicatorTest(unittest.TestCase):
    def test_indicator_creates_hybrid_scores_and_gate_columns(self):
        rows = []
        for index in range(1, 420):
            wave = ((index % 21) - 10) * 0.00008
            drift = index * 0.000015
            close = 1.08 + drift + wave
            rows.append({
                'time': index,
                'open': close - 0.00005,
                'high': close + 0.00018,
                'low': close - 0.00017,
                'close': close,
                'volume': 100 + (index % 19) * 7,
            })

        symbol = make_symbol(rows)
        indicator = NeuralMicroCostEdgeHybridS7S4(symbol)
        prefix = indicator.name

        expected_columns = [
            f'{prefix}_v2_short_score',
            f'{prefix}_v2_long_score',
            f'{prefix}_v3_gate_score',
            f'{prefix}_hybrid_short_score',
            f'{prefix}_hybrid_long_score',
            f'{prefix}_hybrid_score',
            f'{prefix}_rsi_14',
            f'{prefix}_trendiness_14',
            f'{prefix}_recent_move_atr_3',
            f'{prefix}_close_location',
            f'{prefix}_best_safe_gate',
            f'{prefix}_cadence_gate',
        ]
        for column_name in expected_columns:
            self.assertIn(column_name, symbol.candles.columns)

        bounded_zero_one_columns = [
            f'{prefix}_v2_short_score',
            f'{prefix}_v2_long_score',
            f'{prefix}_v3_gate_score',
            f'{prefix}_hybrid_short_score',
            f'{prefix}_hybrid_long_score',
            f'{prefix}_hybrid_score',
        ]
        for column_name in bounded_zero_one_columns:
            values = symbol.candles[column_name].dropna()
            self.assertGreater(len(values), 0)
            self.assertTrue(((values >= 0.0) & (values <= 1.0)).all())

        for column_name in [f'{prefix}_best_safe_gate', f'{prefix}_cadence_gate']:
            values = symbol.candles[column_name].dropna()
            self.assertGreater(len(values), 0)
            self.assertTrue(values.isin([0.0, 1.0]).all())


if __name__ == '__main__':
    unittest.main()

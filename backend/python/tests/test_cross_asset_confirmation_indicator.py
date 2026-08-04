import unittest
from unittest.mock import patch

import pandas as pd

from backend.python.lib.indicator.features.cross_asset_confirmation import CrossAssetConfirmation
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('EURUSD', 'M1', len(candles), candles=candles)


class CrossAssetConfirmationIndicatorTest(unittest.TestCase):
    def test_indicator_aligns_peer_symbol_and_creates_expected_columns(self):
        primary_rows = []
        peer_rows = []

        for index in range(1, 40):
            primary_close = 1.1000 + (index * 0.0001)
            peer_close = 1.2500 + (index * 0.00012)
            primary_rows.append({
                'time': 1_700_000_000 + (index * 60),
                'open': primary_close - 0.00005,
                'high': primary_close + 0.00008,
                'low': primary_close - 0.00008,
                'close': primary_close,
                'volume': 100 + index,
            })
            peer_rows.append({
                'time': 1_700_000_000 + (index * 60),
                'open': peer_close - 0.00005,
                'high': peer_close + 0.00008,
                'low': peer_close - 0.00008,
                'close': peer_close,
                'volume': 200 + index,
            })

        symbol = make_symbol(primary_rows)

        with patch(
            'backend.python.lib.indicator.features.cross_asset_confirmation.ensure_market_data',
            return_value={
                'ready': True,
                'candles': peer_rows,
            },
        ):
            indicator = CrossAssetConfirmation(symbol, 'GBPUSD', 3, 4)

        prefix = indicator.name
        expected_columns = [
            f'{prefix}_primary_return',
            f'{prefix}_peer_return',
            f'{prefix}_return_gap',
            f'{prefix}_agreement',
            f'{prefix}_confirmation_score',
            f'{prefix}_divergence_score',
        ]
        for column_name in expected_columns:
            self.assertIn(column_name, symbol.candles.columns)

        agreement_values = symbol.candles[f'{prefix}_agreement'].dropna()
        self.assertGreater(len(agreement_values), 0)
        self.assertTrue(agreement_values.isin([-1.0, 0.0, 1.0]).all())

        confirmation_values = symbol.candles[f'{prefix}_confirmation_score'].dropna()
        self.assertGreater(len(confirmation_values), 0)
        self.assertTrue(((confirmation_values >= 0.0) & (confirmation_values <= 1.0)).all())
        self.assertGreater(float(confirmation_values.iloc[-1]), 0.0)

        divergence_values = symbol.candles[f'{prefix}_divergence_score'].dropna()
        self.assertGreater(len(divergence_values), 0)
        self.assertTrue(((divergence_values >= 0.0) & (divergence_values <= 1.0)).all())


if __name__ == '__main__':
    unittest.main()

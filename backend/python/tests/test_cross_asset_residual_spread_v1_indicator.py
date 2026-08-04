import unittest
from unittest.mock import patch

import pandas as pd

from backend.python.lib.indicator.features.cross_asset_residual_spread_v1 import CrossAssetResidualSpreadV1
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('EURUSD', 'M1', len(candles), candles=candles)


class CrossAssetResidualSpreadV1IndicatorTest(unittest.TestCase):
    def test_indicator_creates_residual_columns_for_direct_pair(self):
        primary_rows = []
        peer_rows = []

        for index in range(1, 60):
            primary_close = 1.1000 + (index * 0.00011)
            peer_close = 1.2500 + (index * 0.00010)
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
            indicator = CrossAssetResidualSpreadV1(symbol, 'GBPUSD', 'direct', 3, 12, 4)

        prefix = indicator.name
        expected_columns = [
            f'{prefix}_adjusted_peer_return',
            f'{prefix}_signed_residual_return',
            f'{prefix}_normalized_residual',
            f'{prefix}_absolute_normalized_residual',
            f'{prefix}_residual_zscore',
            f'{prefix}_agreement',
            f'{prefix}_sync_score',
        ]
        for column_name in expected_columns:
            self.assertIn(column_name, symbol.candles.columns)

        agreement_values = symbol.candles[f'{prefix}_agreement'].dropna()
        self.assertGreater(len(agreement_values), 0)
        self.assertTrue(agreement_values.isin([-1.0, 0.0, 1.0]).all())
        self.assertGreater(float(agreement_values.iloc[-1]), 0.0)

        abs_residual_values = symbol.candles[f'{prefix}_absolute_normalized_residual'].dropna()
        self.assertGreater(len(abs_residual_values), 0)
        self.assertTrue(((abs_residual_values >= 0.0) & (abs_residual_values <= 1.0)).all())

        sync_score_values = symbol.candles[f'{prefix}_sync_score'].dropna()
        self.assertGreater(len(sync_score_values), 0)
        self.assertTrue(((sync_score_values >= 0.0) & (sync_score_values <= 1.0)).all())
        self.assertGreater(float(sync_score_values.iloc[-1]), 0.0)

    def test_indicator_inverts_peer_direction_for_inverse_relation(self):
        primary_rows = []
        peer_rows = []

        primary_closes = [1.1000, 1.1002, 1.1005, 1.1007, 1.1010, 1.1012]
        peer_closes = [0.9100, 0.9098, 0.9094, 0.9091, 0.9088, 0.9086]

        for index, (primary_close, peer_close) in enumerate(zip(primary_closes, peer_closes), start=1):
            primary_rows.append({
                'time': 1_700_100_000 + (index * 60),
                'open': primary_close - 0.00005,
                'high': primary_close + 0.00008,
                'low': primary_close - 0.00008,
                'close': primary_close,
                'volume': 100 + index,
            })
            peer_rows.append({
                'time': 1_700_100_000 + (index * 60),
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
            indicator = CrossAssetResidualSpreadV1(symbol, 'USDCHF', 'inverse', 1, 3, 2)

        prefix = indicator.name
        adjusted_peer = symbol.candles[f'{prefix}_adjusted_peer_return'].dropna()
        agreement = symbol.candles[f'{prefix}_agreement'].dropna()

        self.assertGreater(len(adjusted_peer), 0)
        self.assertTrue((adjusted_peer > 0).all())
        self.assertTrue((agreement >= 0).all())


if __name__ == '__main__':
    unittest.main()

import unittest
import pickle
from pathlib import Path

from backend.python.lib.indicator.features.neural_ema_low_adx_setup_quality_v6_cc50000 import (
    NeuralEmaLowAdxSetupQualityV6CC50000,
)
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    return Symbol('TEST', 'M5', len(rows), candles=rows)


class NeuralEmaLowAdxSetupQualityV6IndicatorTest(unittest.TestCase):
    def test_indicator_creates_score_columns_with_expected_ranges(self):
        snapshot_path = (
            Path(__file__).resolve().parents[1]
            / 'data'
            / 'neural'
            / 'auth-user_1'
            / 'ema_low_adx_setup_quality_cnn_v1'
            / 'runs'
            / 'ae34019d7ed245ca9a0cdd25ecdbb1cc'
            / 'market_snapshot.pkl'
        )
        if not snapshot_path.exists():
            self.skipTest(f'Missing market snapshot fixture: {snapshot_path}')

        payload = pickle.loads(snapshot_path.read_bytes())
        rows = list((payload or {}).get('candles') or [])[:600]
        self.assertGreater(len(rows), 0)

        symbol = make_symbol(rows)
        indicator = NeuralEmaLowAdxSetupQualityV6CC50000(symbol)
        prefix = indicator.name

        expected_columns = [
            f'{prefix}_not_good_score',
            f'{prefix}_good_score',
            f'{prefix}_edge_score',
        ]
        for column_name in expected_columns:
            self.assertIn(column_name, symbol.candles.columns)

        not_good_score = symbol.candles[f'{prefix}_not_good_score'].dropna()
        good_score = symbol.candles[f'{prefix}_good_score'].dropna()
        edge_score = symbol.candles[f'{prefix}_edge_score'].dropna()

        self.assertGreater(len(not_good_score), 0)
        self.assertGreater(len(good_score), 0)
        self.assertGreater(len(edge_score), 0)

        self.assertTrue(((not_good_score >= 0.0) & (not_good_score <= 1.0)).all())
        self.assertTrue(((good_score >= 0.0) & (good_score <= 1.0)).all())
        self.assertTrue(((edge_score >= -1.0) & (edge_score <= 1.0)).all())

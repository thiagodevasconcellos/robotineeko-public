import pickle
import unittest
from pathlib import Path

from backend.python.lib.indicator.features.ema_low_adx_setup_cluster_context_v1 import (
    EmaLowAdxSetupClusterContextV1,
)
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    return Symbol('TEST', 'M5', len(rows), candles=rows)


class EmaLowAdxSetupClusterContextV1IndicatorTest(unittest.TestCase):
    def test_indicator_creates_cluster_context_columns(self):
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
        indicator = EmaLowAdxSetupClusterContextV1(symbol)
        prefix = indicator.name

        expected_columns = [
            f'{prefix}_slq_di_spread_14',
            f'{prefix}_slq_rsi_delta_3',
            f'{prefix}_slq_reclaim_strength',
            f'{prefix}_slqc_base_candidate_flag',
            f'{prefix}_slqc_prev_candidate_gap_24',
            f'{prefix}_slqc_recent_candidate_density_12',
            f'{prefix}_slqc_recent_candidate_density_24',
            f'{prefix}_slqc_last_candidate_di_spread',
            f'{prefix}_slqc_di_vs_last_candidate',
            f'{prefix}_slqc_di_vs_recent_candidate_max_12',
            f'{prefix}_slqc_di_vs_recent_candidate_mean_12',
            f'{prefix}_slqc_reclaim_vs_recent_candidate_max_12',
        ]
        for column_name in expected_columns:
            self.assertIn(column_name, symbol.candles.columns)

        prev_gap = symbol.candles[f'{prefix}_slqc_prev_candidate_gap_24'].dropna()
        density_12 = symbol.candles[f'{prefix}_slqc_recent_candidate_density_12'].dropna()
        di_vs_recent_max = symbol.candles[f'{prefix}_slqc_di_vs_recent_candidate_max_12'].dropna()

        self.assertGreater(len(prev_gap), 0)
        self.assertGreater(len(density_12), 0)
        self.assertGreater(len(di_vs_recent_max), 0)

        self.assertTrue(((prev_gap >= 0.0) & (prev_gap <= 1.0)).all())
        self.assertTrue(((density_12 >= 0.0) & (density_12 <= 1.0)).all())

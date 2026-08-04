import math
import unittest
from unittest.mock import patch

import pandas as pd

from backend.python.neural.registry import get_neural_network
from backend.python.neural.supervised.config import SupervisedFeatureConfig
from backend.python.neural.supervised.features import BasicFeedForwardFeaturePipeline, REGIME_CLASS_CODES
from backend.python.services.neural_service import _normalize_network_config


def _make_candles(total_rows: int = 320):
    rows = []
    for index in range(total_rows):
        base = 100.0 + (index * 0.05)
        wave = math.sin(index / 9.0) * 1.4
        close = base + wave
        rows.append({
            'time': index + 1,
            'open': close - (0.18 if index % 2 == 0 else -0.12),
            'high': close + 0.45 + abs(math.sin(index / 5.0)) * 0.25,
            'low': close - 0.42 - abs(math.cos(index / 7.0)) * 0.22,
            'close': close,
            'volume': 100 + ((index % 17) * 7),
        })
    return pd.DataFrame(rows)


class NeuralMarketRegimeTest(unittest.TestCase):
    def test_registry_exposes_neural_market_regime_cnn_v1(self):
        network = get_neural_network('neural_market_regime_cnn_v1')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'neural_market_regime_cnn_v1')
        self.assertEqual(network['score_metric'], 'macro_f1')
        self.assertIn('nmr_choppiness_14', [target['id'] for target in network['normalization_targets']])

    def test_normalize_network_config_sets_regime_target_mode(self):
        config = _normalize_network_config(
            'neural_market_regime_cnn_v1',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 12000,
                'observationWindow': 48,
                'kernelSize': 7,
                'normalizationColumns': ['nmr_choppiness_14', 'invalid_column'],
                'targetRegimeCompressionThreshold': 0.8,
                'targetRegimeVolatilityThreshold': 2.4,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_regime_classification')
        self.assertEqual(config['kernelSize'], 7)
        self.assertEqual(config['normalizationColumns'], ['nmr_choppiness_14'])
        self.assertAlmostEqual(config['targetRegimeCompressionThreshold'], 0.8)
        self.assertAlmostEqual(config['targetRegimeVolatilityThreshold'], 2.4)

    def test_regime_sequence_dataset_uses_allowed_regime_codes(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['rsi_7'] = 48.0 + pd.Series([math.sin(index / 8.0) * 12.0 for index in range(len(candles))])
        candles['rsi_14'] = 50.0 + pd.Series([math.sin(index / 10.0) * 10.0 for index in range(len(candles))])
        candles['atr_14'] = 0.48
        candles['adx_14'] = 26.0
        candles['plus_di_14'] = 27.0
        candles['minus_di_14'] = 16.0
        candles['macd_line'] = candles['close'].diff().fillna(0.0) * 0.08
        candles['macd_signal'] = candles['macd_line'].ewm(span=9, adjust=False).mean()
        candles['macd_histogram'] = candles['macd_line'] - candles['macd_signal']
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.2)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['stoch_k'] = 50.0 + pd.Series([math.sin(index / 6.0) * 25.0 for index in range(len(candles))])
        candles['stoch_d'] = candles['stoch_k'].rolling(window=3, min_periods=1).mean()
        candles['roc_10'] = candles['close'].pct_change(10).fillna(0.0) * 100.0
        candles['choppiness_14'] = 48.0
        candles['trendiness_14'] = 52.0
        candles['donchian_width_20'] = 1.6
        candles['supertrend_direction'] = 1.0
        candles['vwap_distance_ratio'] = 0.001
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M15',
            bars=len(candles),
            network_id='neural_market_regime_cnn_v1',
            feature_profile='market_regime_fusion',
            observation_window=24,
            target_horizon=12,
            target_mode='future_regime_classification',
        )
        pipeline = BasicFeedForwardFeaturePipeline.from_candles(config, candles.to_dict(orient='records'))
        with patch.object(
            BasicFeedForwardFeaturePipeline,
            '_resolve_indicator_columns',
            side_effect=[
                [
                    'ema_9',
                    'ema_21',
                    'rsi_7',
                    'rsi_14',
                    'atr_14',
                    'adx_14',
                    'plus_di_14',
                    'minus_di_14',
                    'macd_line',
                    'macd_signal',
                    'macd_histogram',
                    'bb_upper',
                    'bb_lower',
                    'bb_width',
                    'stoch_k',
                    'stoch_d',
                    'roc_10',
                ],
                [
                    'choppiness_14',
                    'trendiness_14',
                    'donchian_width_20',
                    'supertrend_direction',
                    'vwap_distance_ratio',
                ],
            ],
        ):
            dataset = pipeline.build_regime_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('nmr_choppiness_14', dataset['feature_columns'])
        self.assertIn('nmr_supertrend_direction', dataset['feature_columns'])
        self.assertTrue(set(dataset['class_codes']).issubset(set(REGIME_CLASS_CODES)))
        self.assertTrue(set(dataset['y_class']).issubset(set(range(len(REGIME_CLASS_CODES)))))


if __name__ == '__main__':
    unittest.main()

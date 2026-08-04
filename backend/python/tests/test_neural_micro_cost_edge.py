import math
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backend.python.neural.registry import get_neural_network
from backend.python.neural.runners import (
    MICRO_COST_EDGE_EVENT_THRESHOLD_SELECTION_VERSION,
    MICRO_COST_EDGE_HIERARCHICAL_EVENT_THRESHOLD_SELECTION_VERSION,
    _resolve_micro_cost_edge_v3_event_threshold,
    _search_micro_cost_edge_hierarchical_threshold,
    _resolve_micro_cost_edge_v2_event_threshold,
    _search_micro_cost_edge_side_threshold,
)
from backend.python.neural.supervised.config import SupervisedFeatureConfig
from backend.python.neural.supervised.features import (
    BasicFeedForwardFeaturePipeline,
    MICRO_COST_EDGE_CLASS_CODES,
    MICRO_COST_EDGE_SIDE_CLASS_CODES,
)
from backend.python.services.neural_service import _normalize_network_config


def _make_candles(total_rows: int = 420):
    rows = []
    for index in range(total_rows):
        anchor = 1.10 + math.sin(index / 30.0) * 0.002
        drift = math.sin(index / 7.0) * 0.0009
        impulse = 0.0012 if index % 29 == 0 else 0.0
        rejection = -0.0010 if index % 31 == 0 else 0.0
        close = anchor + drift + impulse + rejection
        open_price = close - (0.00018 if index % 2 == 0 else -0.00014)
        rows.append({
            'time': 1_700_000_000 + (index * 60),
            'open': open_price,
            'high': max(open_price, close) + 0.00045 + abs(math.sin(index / 5.0)) * 0.00025,
            'low': min(open_price, close) - 0.00048 - abs(math.cos(index / 6.0)) * 0.00023,
            'close': close,
            'volume': 100 + ((index % 17) * 9),
        })
    return pd.DataFrame(rows)


class MicroCostEdgeCnnTest(unittest.TestCase):
    class _FakeMicroCostEdgeModel:
        def transform_features(self, values):
            return np.asarray(values, dtype=float)

        def predict_probabilities(self, values):
            safe_values = np.asarray(values, dtype=float).reshape(-1)
            return np.column_stack([1.0 - safe_values, safe_values])

    class _FakeColumnProbModel:
        def __init__(self, column_index: int):
            self.column_index = int(column_index)

        def transform_features(self, values):
            return np.asarray(values, dtype=float)

        def predict_probabilities(self, values):
            safe_values = np.asarray(values, dtype=float)
            if safe_values.ndim == 1:
                scores = safe_values.reshape(-1)
            else:
                scores = safe_values[:, self.column_index].reshape(-1)
            return np.column_stack([1.0 - scores, scores])

    def test_registry_exposes_micro_cost_edge_cnn_v1(self):
        network = get_neural_network('micro_cost_edge_cnn_v1')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'micro_cost_edge_cnn_v1')
        self.assertEqual(network['score_metric'], 'directional_edge_macro_f1')
        self.assertIn('mce_cost_to_atr_14', [target['id'] for target in network['normalization_targets']])

    def test_registry_exposes_micro_cost_edge_cnn_v2(self):
        network = get_neural_network('micro_cost_edge_cnn_v2')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'micro_cost_edge_cnn_v2')
        self.assertEqual(network['score_metric'], 'directional_edge_macro_f1')
        self.assertIn('mce_cost_to_range', [target['id'] for target in network['normalization_targets']])

    def test_registry_exposes_micro_cost_edge_cnn_v3(self):
        network = get_neural_network('micro_cost_edge_cnn_v3')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'micro_cost_edge_cnn_v3')
        self.assertEqual(network['score_metric'], 'directional_edge_macro_f1')
        self.assertIn('mce_cost_to_range', [target['id'] for target in network['normalization_targets']])

    def test_registry_exposes_micro_cost_edge_cnn_v4(self):
        network = get_neural_network('micro_cost_edge_cnn_v4')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'micro_cost_edge_cnn_v4')
        self.assertEqual(network['score_metric'], 'directional_edge_macro_f1')
        self.assertIn('mcep_bullish_reversal_score', [target['id'] for target in network['normalization_targets']])

    def test_registry_exposes_micro_cost_edge_cnn_v5(self):
        network = get_neural_network('micro_cost_edge_cnn_v5')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'micro_cost_edge_cnn_v5')
        self.assertEqual(network['score_metric'], 'directional_edge_macro_f1')
        self.assertIn('mcep_bullish_reversal_score', [target['id'] for target in network['normalization_targets']])

    def test_normalize_network_config_sets_micro_cost_edge_target_mode(self):
        config = _normalize_network_config(
            'micro_cost_edge_cnn_v1',
            {
                'symbol': 'eurusd',
                'timeframe': 'm1',
                'bars': 10000,
                'targetHorizon': 5,
                'pipSize': 0.0001,
                'roundTripCostPips': 1.9,
                'targetCostEdgeMultiple': 1.5,
                'normalizationColumns': ['mce_rsi_14', 'invalid_column'],
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M1')
        self.assertEqual(config['targetMode'], 'micro_cost_edge_classification')
        self.assertAlmostEqual(config['pipSize'], 0.0001)
        self.assertAlmostEqual(config['roundTripCostPips'], 1.9)
        self.assertAlmostEqual(config['targetCostEdgeMultiple'], 1.5)
        self.assertEqual(config['normalizationColumns'], ['mce_rsi_14'])

    def test_normalize_network_config_sets_micro_cost_edge_side_target_mode(self):
        config = _normalize_network_config(
            'micro_cost_edge_cnn_v2',
            {
                'symbol': 'eurusd',
                'timeframe': 'm1',
                'bars': 10000,
                'targetHorizon': 8,
                'pipSize': 0.0001,
                'roundTripCostPips': 1.8,
                'targetCostEdgeMultiple': 1.75,
                'normalizationColumns': ['mce_cost_to_atr_14'],
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M1')
        self.assertEqual(config['targetMode'], 'micro_cost_edge_side_classification')
        self.assertAlmostEqual(config['roundTripCostPips'], 1.8)
        self.assertAlmostEqual(config['targetCostEdgeMultiple'], 1.75)
        self.assertEqual(config['normalizationColumns'], ['mce_cost_to_atr_14'])

    def test_normalize_network_config_sets_micro_cost_edge_hierarchical_target_mode(self):
        config = _normalize_network_config(
            'micro_cost_edge_cnn_v3',
            {
                'symbol': 'eurusd',
                'timeframe': 'm1',
                'bars': 10000,
                'targetHorizon': 8,
                'pipSize': 0.0001,
                'roundTripCostPips': 1.7,
                'targetCostEdgeMultiple': 1.75,
                'normalizationColumns': ['mce_cost_to_range'],
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M1')
        self.assertEqual(config['targetMode'], 'micro_cost_edge_hierarchical_classification')
        self.assertAlmostEqual(config['roundTripCostPips'], 1.7)
        self.assertAlmostEqual(config['targetCostEdgeMultiple'], 1.75)
        self.assertEqual(config['normalizationColumns'], ['mce_cost_to_range'])

    def test_normalize_network_config_sets_micro_cost_edge_pattern_context_target_mode(self):
        config = _normalize_network_config(
            'micro_cost_edge_cnn_v4',
            {
                'symbol': 'eurusd',
                'timeframe': 'm1',
                'bars': 10000,
                'targetHorizon': 8,
                'pipSize': 0.0001,
                'roundTripCostPips': 1.7,
                'targetCostEdgeMultiple': 1.75,
                'normalizationColumns': ['mcep_bullish_reversal_score', 'invalid_column'],
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M1')
        self.assertEqual(config['targetMode'], 'micro_cost_edge_hierarchical_classification')
        self.assertAlmostEqual(config['roundTripCostPips'], 1.7)
        self.assertAlmostEqual(config['targetCostEdgeMultiple'], 1.75)
        self.assertEqual(config['normalizationColumns'], ['mcep_bullish_reversal_score'])

    def test_normalize_network_config_sets_micro_cost_edge_side_pattern_context_target_mode(self):
        config = _normalize_network_config(
            'micro_cost_edge_cnn_v5',
            {
                'symbol': 'eurusd',
                'timeframe': 'm1',
                'bars': 10000,
                'targetHorizon': 8,
                'pipSize': 0.0001,
                'roundTripCostPips': 1.7,
                'targetCostEdgeMultiple': 1.75,
                'normalizationColumns': ['mcep_bearish_continuation_score', 'invalid_column'],
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M1')
        self.assertEqual(config['targetMode'], 'micro_cost_edge_side_classification')
        self.assertAlmostEqual(config['roundTripCostPips'], 1.7)
        self.assertAlmostEqual(config['targetCostEdgeMultiple'], 1.75)
        self.assertEqual(config['normalizationColumns'], ['mcep_bearish_continuation_score'])

    def test_micro_cost_edge_sequence_dataset_uses_expected_class_codes(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.00065
        candles['adx_14'] = 22.0
        candles['plus_di_14'] = 24.0
        candles['minus_di_14'] = 17.0
        candles['rsi_7'] = 48.0 + pd.Series([math.sin(index / 5.0) * 9.0 for index in range(len(candles))])
        candles['rsi_14'] = 50.0 + pd.Series([math.sin(index / 7.0) * 7.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0003)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['choppiness_14'] = 44.0
        candles['trendiness_14'] = 56.0
        candles['vwap_distance_ratio'] = pd.Series([math.sin(index / 11.0) * 0.002 for index in range(len(candles))])
        config = SupervisedFeatureConfig(
            symbol_name='EURUSD',
            timeframe='M1',
            bars=len(candles),
            network_id='micro_cost_edge_cnn_v1',
            feature_profile='micro_cost_edge',
            observation_window=18,
            target_horizon=5,
            pip_size=0.0001,
            round_trip_cost_pips=1.4,
            target_cost_edge_multiple=1.2,
        )
        pipeline = BasicFeedForwardFeaturePipeline.from_candles(config, candles.to_dict(orient='records'))
        with patch.object(
            BasicFeedForwardFeaturePipeline,
            '_resolve_indicator_columns',
            return_value=[
                'ema_9',
                'ema_21',
                'atr_14',
                'adx_14',
                'plus_di_14',
                'minus_di_14',
                'rsi_7',
                'rsi_14',
                'bb_upper',
                'bb_lower',
                'bb_width',
                'choppiness_14',
                'trendiness_14',
                'vwap_distance_ratio',
            ],
        ):
            dataset = pipeline.build_micro_cost_edge_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('mce_cost_to_atr_14', dataset['feature_columns'])
        self.assertEqual(dataset['class_codes'], MICRO_COST_EDGE_CLASS_CODES)
        self.assertTrue(set(dataset['y_class']).issubset(set(range(len(MICRO_COST_EDGE_CLASS_CODES)))))
        self.assertGreater(dataset['candidate_summary']['rows_after_cleaning'], 0)

    def test_micro_cost_edge_canonical_sequence_dataset_uses_side_classes(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.00065
        candles['adx_14'] = 22.0
        candles['plus_di_14'] = 24.0
        candles['minus_di_14'] = 17.0
        candles['rsi_7'] = 48.0 + pd.Series([math.sin(index / 5.0) * 9.0 for index in range(len(candles))])
        candles['rsi_14'] = 50.0 + pd.Series([math.sin(index / 7.0) * 7.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0003)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['choppiness_14'] = 44.0
        candles['trendiness_14'] = 56.0
        candles['vwap_distance_ratio'] = pd.Series([math.sin(index / 11.0) * 0.002 for index in range(len(candles))])
        config = SupervisedFeatureConfig(
            symbol_name='EURUSD',
            timeframe='M1',
            bars=len(candles),
            network_id='micro_cost_edge_cnn_v2',
            feature_profile='micro_cost_edge',
            observation_window=18,
            target_horizon=5,
            pip_size=0.0001,
            round_trip_cost_pips=1.4,
            target_cost_edge_multiple=1.2,
        )
        pipeline = BasicFeedForwardFeaturePipeline.from_candles(config, candles.to_dict(orient='records'))
        with patch.object(
            BasicFeedForwardFeaturePipeline,
            '_resolve_indicator_columns',
            return_value=[
                'ema_9',
                'ema_21',
                'atr_14',
                'adx_14',
                'plus_di_14',
                'minus_di_14',
                'rsi_7',
                'rsi_14',
                'bb_upper',
                'bb_lower',
                'bb_width',
                'choppiness_14',
                'trendiness_14',
                'vwap_distance_ratio',
            ],
        ):
            dataset = pipeline.build_micro_cost_edge_canonical_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertEqual(dataset['class_codes'], MICRO_COST_EDGE_SIDE_CLASS_CODES)
        self.assertEqual(dataset['event_class_codes'], MICRO_COST_EDGE_CLASS_CODES)
        self.assertEqual(len(dataset['X_long']), len(dataset['X_short']))
        self.assertEqual(len(dataset['y_long']), len(dataset['y_short']))
        self.assertGreater(dataset['candidate_summary']['event_rows'], 0)

    def test_micro_cost_edge_pattern_context_dataset_exposes_side_relative_pattern_scores(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.00065
        candles['adx_14'] = 22.0
        candles['plus_di_14'] = 24.0
        candles['minus_di_14'] = 17.0
        candles['rsi_7'] = 48.0 + pd.Series([math.sin(index / 5.0) * 9.0 for index in range(len(candles))])
        candles['rsi_14'] = 50.0 + pd.Series([math.sin(index / 7.0) * 7.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0003)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['choppiness_14'] = 44.0
        candles['trendiness_14'] = 56.0
        candles['vwap_distance_ratio'] = pd.Series([math.sin(index / 11.0) * 0.002 for index in range(len(candles))])
        candles['candlestick_patterns_5_14_bullish_reversal_score'] = pd.Series([0.1 + ((index % 5) * 0.02) for index in range(len(candles))])
        candles['candlestick_patterns_5_14_bearish_reversal_score'] = pd.Series([0.08 + ((index % 7) * 0.015) for index in range(len(candles))])
        candles['candlestick_patterns_5_14_bullish_continuation_score'] = pd.Series([0.05 + ((index % 3) * 0.03) for index in range(len(candles))])
        candles['candlestick_patterns_5_14_bearish_continuation_score'] = pd.Series([0.06 + ((index % 4) * 0.025) for index in range(len(candles))])
        config = SupervisedFeatureConfig(
            symbol_name='EURUSD',
            timeframe='M1',
            bars=len(candles),
            network_id='micro_cost_edge_cnn_v4',
            feature_profile='micro_cost_edge_pattern_score_context',
            observation_window=18,
            target_horizon=5,
            pip_size=0.0001,
            round_trip_cost_pips=1.4,
            target_cost_edge_multiple=1.2,
        )
        pipeline = BasicFeedForwardFeaturePipeline.from_candles(config, candles.to_dict(orient='records'))
        with patch.object(
            BasicFeedForwardFeaturePipeline,
            '_resolve_indicator_columns',
            side_effect=[
                [
                    'ema_9',
                    'ema_21',
                    'atr_14',
                    'adx_14',
                    'plus_di_14',
                    'minus_di_14',
                    'rsi_7',
                    'rsi_14',
                    'bb_upper',
                    'bb_lower',
                    'bb_width',
                    'choppiness_14',
                    'trendiness_14',
                    'vwap_distance_ratio',
                ],
                [
                    'candlestick_patterns_5_14_bullish_reversal_score',
                    'candlestick_patterns_5_14_bearish_reversal_score',
                    'candlestick_patterns_5_14_bullish_continuation_score',
                    'candlestick_patterns_5_14_bearish_continuation_score',
                ],
            ],
        ):
            dataset = pipeline.build_micro_cost_edge_canonical_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('mce2_side_reversal_score', dataset['feature_columns'])
        self.assertIn('mce2_opposite_reversal_score', dataset['feature_columns'])
        self.assertIn('mce2_side_continuation_score', dataset['feature_columns'])
        self.assertIn('mce2_opposite_continuation_score', dataset['feature_columns'])

    def test_micro_cost_edge_threshold_search_avoids_always_tradable_collapse(self):
        class_labels = {-1: 'short_edge', 0: 'no_edge', 1: 'long_edge'}
        y_event_indices = (
            [1] * 40
            + [2] * 15
            + [2] * 15
            + [0] * 15
            + [0] * 15
        )
        long_positive_scores = (
            [0.500] * 40
            + [0.507] * 15
            + [0.504] * 15
            + [0.503] * 15
            + [0.502] * 15
        )
        short_positive_scores = (
            [0.500] * 40
            + [0.503] * 15
            + [0.502] * 15
            + [0.507] * 15
            + [0.504] * 15
        )

        payload = _search_micro_cost_edge_side_threshold(
            y_event_indices,
            long_positive_scores,
            short_positive_scores,
            class_codes=MICRO_COST_EDGE_CLASS_CODES,
            class_labels=class_labels,
        )

        self.assertGreaterEqual(payload['threshold'], 0.495)
        self.assertLess(payload['threshold'], 0.505)
        self.assertAlmostEqual(
            payload['metrics']['predicted_tradability_rate'],
            payload['metrics']['actual_tradability_rate'],
            places=6,
        )
        self.assertGreater(payload['metrics']['class_no_edge_f1'], 0.7)
        self.assertGreater(payload['metrics']['macro_f1'], 0.68)

    def test_micro_cost_edge_v2_test_recalibrates_legacy_threshold_metadata(self):
        class_labels = {-1: 'short_edge', 0: 'no_edge', 1: 'long_edge'}
        y_event_codes = (
            [0] * 40
            + [1] * 15
            + [1] * 15
            + [-1] * 15
            + [-1] * 15
        )
        long_positive_scores = np.asarray(
            (
                [0.500] * 40
                + [0.507] * 15
                + [0.504] * 15
                + [0.503] * 15
                + [0.502] * 15
            ),
            dtype=float,
        ).reshape(-1, 1)
        short_positive_scores = np.asarray(
            (
                [0.500] * 40
                + [0.503] * 15
                + [0.502] * 15
                + [0.507] * 15
                + [0.504] * 15
            ),
            dtype=float,
        ).reshape(-1, 1)
        sequence_dataset = {
            'X_long': long_positive_scores,
            'X_short': short_positive_scores,
            'y_event_code': np.asarray(y_event_codes, dtype=int),
            'event_class_codes': MICRO_COST_EDGE_CLASS_CODES,
            'event_class_labels': class_labels,
        }

        payload = _resolve_micro_cost_edge_v2_event_threshold(
            self._FakeMicroCostEdgeModel(),
            {'selected_event_threshold': 0.35},
            sequence_dataset,
            train_events=0,
            validation_events=len(y_event_codes),
        )

        self.assertEqual(payload['source'], 'validation_recalibrated_legacy_artifact')
        self.assertEqual(payload['version'], MICRO_COST_EDGE_EVENT_THRESHOLD_SELECTION_VERSION)
        self.assertGreaterEqual(payload['threshold'], 0.495)
        self.assertLess(payload['threshold'], 0.505)
        self.assertIsInstance(payload['validation_metrics'], dict)
        self.assertGreater(payload['validation_metrics']['macro_f1'], 0.68)

    def test_micro_cost_edge_hierarchical_threshold_search_prefers_calibrated_gate(self):
        class_labels = {-1: 'short_edge', 0: 'no_edge', 1: 'long_edge'}
        y_event_indices = [1] * 40 + [2] * 30 + [0] * 30
        tradability_scores = [0.495] * 40 + [0.505] * 30 + [0.505] * 30
        long_positive_scores = [0.50] * 40 + [0.80] * 30 + [0.20] * 30
        short_positive_scores = [0.50] * 40 + [0.20] * 30 + [0.80] * 30

        payload = _search_micro_cost_edge_hierarchical_threshold(
            y_event_indices,
            tradability_scores,
            long_positive_scores,
            short_positive_scores,
            class_codes=MICRO_COST_EDGE_CLASS_CODES,
            class_labels=class_labels,
        )

        self.assertGreaterEqual(payload['threshold'], 0.495)
        self.assertLess(payload['threshold'], 0.505)
        self.assertAlmostEqual(
            payload['metrics']['predicted_tradability_rate'],
            payload['metrics']['actual_tradability_rate'],
            places=6,
        )
        self.assertGreater(payload['metrics']['class_no_edge_f1'], 0.95)
        self.assertGreater(payload['metrics']['directional_edge_macro_f1'], 0.95)

    def test_micro_cost_edge_v3_test_recalibrates_legacy_threshold_metadata(self):
        class_labels = {-1: 'short_edge', 0: 'no_edge', 1: 'long_edge'}
        y_event_codes = [0] * 40 + [1] * 30 + [-1] * 30
        stage1_long_scores = [0.495] * 40 + [0.505] * 30 + [0.505] * 30
        stage1_short_scores = [0.495] * 40 + [0.505] * 30 + [0.505] * 30
        stage2_long_scores = [0.50] * 40 + [0.80] * 30 + [0.20] * 30
        stage2_short_scores = [0.50] * 40 + [0.20] * 30 + [0.80] * 30
        sequence_dataset = {
            'X_long': np.asarray(list(zip(stage1_long_scores, stage2_long_scores)), dtype=float),
            'X_short': np.asarray(list(zip(stage1_short_scores, stage2_short_scores)), dtype=float),
            'y_event_code': np.asarray(y_event_codes, dtype=int),
            'event_class_codes': MICRO_COST_EDGE_CLASS_CODES,
            'event_class_labels': class_labels,
        }

        payload = _resolve_micro_cost_edge_v3_event_threshold(
            self._FakeColumnProbModel(0),
            self._FakeColumnProbModel(1),
            {'selected_event_threshold': 0.35},
            sequence_dataset,
            train_events=0,
            validation_events=len(y_event_codes),
        )

        self.assertEqual(payload['source'], 'validation_recalibrated_legacy_artifact')
        self.assertEqual(payload['version'], MICRO_COST_EDGE_HIERARCHICAL_EVENT_THRESHOLD_SELECTION_VERSION)
        self.assertGreaterEqual(payload['threshold'], 0.495)
        self.assertLess(payload['threshold'], 0.505)
        self.assertIsInstance(payload['validation_metrics'], dict)
        self.assertGreater(payload['validation_metrics']['macro_f1'], 0.95)


if __name__ == '__main__':
    unittest.main()

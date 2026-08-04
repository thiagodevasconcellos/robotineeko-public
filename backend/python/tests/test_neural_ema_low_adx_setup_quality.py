import math
import unittest
from unittest.mock import patch

import pandas as pd

from backend.python.neural.registry import get_neural_network
from backend.python.neural.supervised.config import SupervisedFeatureConfig
from backend.python.neural.supervised.features import (
    BasicFeedForwardFeaturePipeline,
    SETUP_QUALITY_CLASS_CODES,
    SETUP_QUALITY_BINARY_CLASS_CODES,
    SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES,
)
from backend.python.services.neural_service import _normalize_network_config


def _make_candles(total_rows: int = 420):
    rows = []
    for index in range(total_rows):
        anchor = 100.0 + math.sin(index / 40.0) * 0.25
        drift = math.sin(index / 9.0) * 1.75
        dip = -2.1 if index % 37 in (0, 1) else 0.0
        rebound = 1.25 if index % 37 == 2 else 0.0
        close = anchor + drift + dip + rebound
        open_price = close - (0.28 if index % 2 == 0 else -0.18)
        rows.append({
            'time': index + 1,
            'open': open_price,
            'high': max(open_price, close) + 0.52 + abs(math.sin(index / 5.0)) * 0.24,
            'low': min(open_price, close) - 0.56 - abs(math.cos(index / 7.0)) * 0.26,
            'close': close,
            'volume': 110 + ((index % 19) * 8),
        })
    return pd.DataFrame(rows)


class EmaLowAdxSetupQualityCnnTest(unittest.TestCase):
    def test_registry_exposes_ema_low_adx_setup_quality_cnn_v1(self):
        network = get_neural_network('ema_low_adx_setup_quality_cnn_v1')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'ema_low_adx_setup_quality_cnn_v1')
        self.assertEqual(network['score_metric'], 'macro_f1')
        self.assertIn('slq_reclaim_strength', [target['id'] for target in network['normalization_targets']])

    def test_registry_exposes_ema_low_adx_setup_quality_cnn_v2(self):
        network = get_neural_network('ema_low_adx_setup_quality_cnn_v2')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'ema_low_adx_setup_quality_cnn_v2')
        self.assertEqual(network['score_metric'], 'macro_f1')

    def test_registry_exposes_ema_low_adx_setup_quality_cnn_v3(self):
        network = get_neural_network('ema_low_adx_setup_quality_cnn_v3')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'ema_low_adx_setup_quality_cnn_v3')
        self.assertEqual(network['score_metric'], 'macro_f1')

    def test_registry_exposes_ema_low_adx_setup_quality_cnn_v4(self):
        network = get_neural_network('ema_low_adx_setup_quality_cnn_v4')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'ema_low_adx_setup_quality_cnn_v4')
        self.assertEqual(network['score_metric'], 'class_good_setup_f1')

    def test_registry_exposes_ema_low_adx_setup_quality_cnn_v5(self):
        network = get_neural_network('ema_low_adx_setup_quality_cnn_v5')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'ema_low_adx_setup_quality_cnn_v5')
        self.assertEqual(network['score_metric'], 'class_good_setup_f1')
        self.assertIn('slqp_bullish_reversal_score', [target['id'] for target in network['normalization_targets']])

    def test_registry_exposes_ema_low_adx_setup_quality_cnn_v6(self):
        network = get_neural_network('ema_low_adx_setup_quality_cnn_v6')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'ema_low_adx_setup_quality_cnn_v6')
        self.assertEqual(network['score_metric'], 'class_good_setup_f1')
        self.assertIn('slqc_recent_candidate_density_12', [target['id'] for target in network['normalization_targets']])

    def test_registry_exposes_ema_low_adx_setup_quality_cnn_v7(self):
        network = get_neural_network('ema_low_adx_setup_quality_cnn_v7')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'ema_low_adx_setup_quality_cnn_v7')
        self.assertEqual(network['score_metric'], 'class_good_setup_f1')
        self.assertIn('slqp_bullish_reversal_score', [target['id'] for target in network['normalization_targets']])

    def test_normalize_network_config_sets_setup_quality_target_mode(self):
        config = _normalize_network_config(
            'ema_low_adx_setup_quality_cnn_v1',
            {
                'symbol': 'eurusd',
                'timeframe': 'm5',
                'bars': 12000,
                'observationWindow': 20,
                'kernelSize': 5,
                'normalizationColumns': ['slq_rsi_14', 'invalid_column'],
                'setupAdxCeiling': 31,
                'targetQualityGoodExcursionThreshold': 0.9,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M5')
        self.assertEqual(config['targetMode'], 'ema_low_adx_setup_quality_classification')
        self.assertEqual(config['kernelSize'], 5)
        self.assertEqual(config['normalizationColumns'], ['slq_rsi_14'])
        self.assertAlmostEqual(config['setupAdxCeiling'], 31.0)
        self.assertAlmostEqual(config['targetQualityGoodExcursionThreshold'], 0.9)

    def test_normalize_network_config_sets_setup_quality_v2_target_mode(self):
        config = _normalize_network_config(
            'ema_low_adx_setup_quality_cnn_v2',
            {
                'symbol': 'eurusd',
                'timeframe': 'm5',
                'bars': 50000,
                'targetQualityGoodCounterExcursionCeiling': 0.4,
                'targetQualityBadCounterExcursionCeiling': 0.35,
            },
        )

        self.assertEqual(config['targetMode'], 'ema_low_adx_setup_quality_binary_classification')
        self.assertAlmostEqual(config['targetQualityGoodCounterExcursionCeiling'], 0.4)
        self.assertAlmostEqual(config['targetQualityBadCounterExcursionCeiling'], 0.35)

    def test_normalize_network_config_sets_setup_quality_v3_target_mode(self):
        config = _normalize_network_config(
            'ema_low_adx_setup_quality_cnn_v3',
            {
                'symbol': 'eurusd',
                'timeframe': 'm5',
                'bars': 50000,
                'setupAdxCeiling': 35,
                'targetQualityGoodExcursionThreshold': 0.9,
            },
        )

        self.assertEqual(config['targetMode'], 'ema_low_adx_setup_quality_first_touch_binary_classification')
        self.assertAlmostEqual(config['setupAdxCeiling'], 35.0)
        self.assertAlmostEqual(config['targetQualityGoodExcursionThreshold'], 0.9)

    def test_normalize_network_config_sets_setup_quality_v4_target_mode(self):
        config = _normalize_network_config(
            'ema_low_adx_setup_quality_cnn_v4',
            {
                'symbol': 'eurusd',
                'timeframe': 'm5',
                'bars': 50000,
                'setupAdxCeiling': 35,
            },
        )

        self.assertEqual(config['targetMode'], 'ema_low_adx_setup_quality_good_vs_rest_classification')
        self.assertAlmostEqual(config['setupAdxCeiling'], 35.0)

    def test_normalize_network_config_sets_setup_quality_v5_target_mode(self):
        config = _normalize_network_config(
            'ema_low_adx_setup_quality_cnn_v5',
            {
                'symbol': 'eurusd',
                'timeframe': 'm5',
                'bars': 50000,
                'setupAdxCeiling': 29,
                'setupDiSpreadFloor': 0.02,
                'setupCandidateMinGapBars': 6,
                'normalizationColumns': ['slqp_bullish_reversal_score', 'invalid_column'],
            },
        )

        self.assertEqual(config['targetMode'], 'ema_low_adx_setup_quality_good_vs_rest_classification')
        self.assertAlmostEqual(config['setupAdxCeiling'], 29.0)
        self.assertAlmostEqual(config['setupDiSpreadFloor'], 0.02)
        self.assertEqual(config['setupCandidateMinGapBars'], 6)
        self.assertEqual(config['normalizationColumns'], ['slqp_bullish_reversal_score'])

    def test_normalize_network_config_sets_setup_quality_v6_target_mode(self):
        config = _normalize_network_config(
            'ema_low_adx_setup_quality_cnn_v6',
            {
                'symbol': 'eurusd',
                'timeframe': 'm5',
                'bars': 50000,
                'normalizationColumns': ['slqc_recent_candidate_density_12', 'invalid_column'],
            },
        )

        self.assertEqual(config['targetMode'], 'ema_low_adx_setup_quality_good_vs_rest_classification')
        self.assertEqual(config['normalizationColumns'], ['slqc_recent_candidate_density_12'])

    def test_normalize_network_config_sets_setup_quality_v7_target_mode(self):
        config = _normalize_network_config(
            'ema_low_adx_setup_quality_cnn_v7',
            {
                'symbol': 'eurusd',
                'timeframe': 'm5',
                'bars': 50000,
                'targetReversalTakeProfitAtr': 1.0,
                'targetReversalStopLossAtr': 1.0,
                'normalizationColumns': ['slqp_bullish_reversal_score', 'invalid_column'],
            },
        )

        self.assertEqual(config['targetMode'], 'ema_low_adx_setup_quality_tp_sl_good_vs_rest_classification')
        self.assertAlmostEqual(config['targetReversalTakeProfitAtr'], 1.0)
        self.assertAlmostEqual(config['targetReversalStopLossAtr'], 1.0)
        self.assertEqual(config['normalizationColumns'], ['slqp_bullish_reversal_score'])

    def test_setup_quality_candidate_gap_dedupe_keeps_highest_priority_rows(self):
        candidate_mask = pd.Series([False, True, True, False, True, True, False], dtype=bool)
        priority_series = pd.Series([0.0, 0.03, 0.05, 0.0, 0.02, 0.07, 0.0], dtype=float)

        deduped = BasicFeedForwardFeaturePipeline._dedupe_candidate_mask_by_priority(
            candidate_mask,
            priority_series,
            min_gap_bars=2,
        )

        self.assertEqual(deduped.tolist(), [False, False, True, False, False, True, False])

    def test_setup_quality_sequence_dataset_uses_allowed_class_codes(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 24.0
        candles['plus_di_14'] = 28.0
        candles['minus_di_14'] = 16.0
        candles['rsi_14'] = 42.0 + pd.Series([math.sin(index / 7.0) * 8.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.2)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_middle'] = rolling_mean
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M5',
            bars=len(candles),
            network_id='ema_low_adx_setup_quality_cnn_v1',
            feature_profile='ema_low_adx_setup_quality',
            observation_window=18,
            target_horizon=8,
            setup_adx_ceiling=100.0,
            setup_prev_rsi_ceiling=100.0,
            setup_current_rsi_floor=0.0,
            setup_current_rsi_ceiling=100.0,
            setup_touch_slack_atr=2.0,
            setup_prev_band_slack_atr=2.0,
            setup_bounce_fraction=0.0,
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
                    'rsi_14',
                    'bb_upper',
                    'bb_middle',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'ema_9',
                    'atr_14',
                    'adx_14',
                    'rsi_14',
                    'bb_middle',
                    'bb_lower',
                ],
            ],
        ):
            dataset = pipeline.build_ema_low_adx_setup_quality_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('slq_reclaim_strength', dataset['feature_columns'])
        self.assertTrue(set(dataset['class_codes']).issubset(set(SETUP_QUALITY_CLASS_CODES)))
        self.assertTrue(set(dataset['y_class']).issubset(set(range(len(SETUP_QUALITY_CLASS_CODES)))))
        self.assertGreater(dataset['candidate_summary']['candidate_rows'], 0)

    def test_setup_quality_v2_sequence_dataset_uses_binary_class_codes(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 24.0
        candles['plus_di_14'] = 28.0
        candles['minus_di_14'] = 16.0
        candles['rsi_14'] = 42.0 + pd.Series([math.sin(index / 7.0) * 8.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.2)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_middle'] = rolling_mean
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M5',
            bars=len(candles),
            network_id='ema_low_adx_setup_quality_cnn_v2',
            feature_profile='ema_low_adx_setup_quality',
            observation_window=18,
            target_horizon=8,
            setup_adx_ceiling=100.0,
            setup_prev_rsi_ceiling=100.0,
            setup_current_rsi_floor=0.0,
            setup_current_rsi_ceiling=100.0,
            setup_touch_slack_atr=2.0,
            setup_prev_band_slack_atr=2.0,
            setup_bounce_fraction=0.0,
            target_quality_good_counter_excursion_ceiling=5.0,
            target_quality_bad_counter_excursion_ceiling=5.0,
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
                    'rsi_14',
                    'bb_upper',
                    'bb_middle',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'ema_9',
                    'atr_14',
                    'adx_14',
                    'rsi_14',
                    'bb_middle',
                    'bb_lower',
                ],
            ],
        ):
            dataset = pipeline.build_ema_low_adx_setup_quality_v2_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertTrue(set(dataset['class_codes']).issubset(set(SETUP_QUALITY_BINARY_CLASS_CODES)))
        self.assertTrue(set(dataset['y_class']).issubset(set(range(len(SETUP_QUALITY_BINARY_CLASS_CODES)))))
        self.assertGreater(dataset['candidate_summary']['binary_kept_rows'], 0)

    def test_setup_quality_v3_sequence_dataset_uses_first_touch_binary_class_codes(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 24.0
        candles['plus_di_14'] = 28.0
        candles['minus_di_14'] = 16.0
        candles['rsi_14'] = 42.0 + pd.Series([math.sin(index / 7.0) * 8.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.2)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_middle'] = rolling_mean
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M5',
            bars=len(candles),
            network_id='ema_low_adx_setup_quality_cnn_v3',
            feature_profile='ema_low_adx_setup_quality',
            observation_window=18,
            target_horizon=8,
            setup_adx_ceiling=100.0,
            setup_prev_rsi_ceiling=100.0,
            setup_current_rsi_floor=0.0,
            setup_current_rsi_ceiling=100.0,
            setup_touch_slack_atr=2.0,
            setup_prev_band_slack_atr=2.0,
            setup_bounce_fraction=0.0,
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
                    'rsi_14',
                    'bb_upper',
                    'bb_middle',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'ema_9',
                    'atr_14',
                    'adx_14',
                    'rsi_14',
                    'bb_middle',
                    'bb_lower',
                ],
            ],
        ):
            dataset = pipeline.build_ema_low_adx_setup_quality_v3_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertTrue(set(dataset['class_codes']).issubset(set(SETUP_QUALITY_BINARY_CLASS_CODES)))
        self.assertTrue(set(dataset['y_class']).issubset(set(range(len(SETUP_QUALITY_BINARY_CLASS_CODES)))))
        self.assertGreater(dataset['candidate_summary']['binary_first_touch_kept_rows'], 0)
        self.assertIn('binary_first_touch_timeout_rows', dataset['candidate_summary'])
        self.assertIn('binary_first_touch_ambiguous_rows', dataset['candidate_summary'])

    def test_setup_quality_v4_sequence_dataset_uses_good_vs_rest_class_codes(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 24.0
        candles['plus_di_14'] = 28.0
        candles['minus_di_14'] = 16.0
        candles['rsi_14'] = 42.0 + pd.Series([math.sin(index / 7.0) * 8.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.2)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_middle'] = rolling_mean
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M5',
            bars=len(candles),
            network_id='ema_low_adx_setup_quality_cnn_v4',
            feature_profile='ema_low_adx_setup_quality',
            observation_window=18,
            target_horizon=8,
            setup_adx_ceiling=100.0,
            setup_prev_rsi_ceiling=100.0,
            setup_current_rsi_floor=0.0,
            setup_current_rsi_ceiling=100.0,
            setup_touch_slack_atr=2.0,
            setup_prev_band_slack_atr=2.0,
            setup_bounce_fraction=0.0,
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
                    'rsi_14',
                    'bb_upper',
                    'bb_middle',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'ema_9',
                    'atr_14',
                    'adx_14',
                    'rsi_14',
                    'bb_middle',
                    'bb_lower',
                ],
            ],
        ):
            dataset = pipeline.build_ema_low_adx_setup_quality_v4_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertEqual(dataset['class_codes'], [0, 1])
        self.assertGreater(dataset['candidate_summary']['good_vs_rest_negative_rows'], 0)
        self.assertIn('good_vs_rest_positive_rows', dataset['candidate_summary'])
        self.assertIn('good_vs_rest_bad_rows', dataset['candidate_summary'])

    def test_setup_quality_v5_sequence_dataset_includes_pattern_scores(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 24.0
        candles['plus_di_14'] = 28.0
        candles['minus_di_14'] = 16.0
        candles['rsi_14'] = 42.0 + pd.Series([math.sin(index / 7.0) * 8.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.2)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_middle'] = rolling_mean
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['csp_bullish_reversal_score'] = 0.15
        candles['csp_bearish_reversal_score'] = 0.12
        candles['csp_bullish_continuation_score'] = 0.18
        candles['csp_bearish_continuation_score'] = 0.11
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M5',
            bars=len(candles),
            network_id='ema_low_adx_setup_quality_cnn_v5',
            feature_profile='ema_low_adx_setup_quality_pattern_score_context',
            observation_window=18,
            target_horizon=8,
            setup_adx_ceiling=100.0,
            setup_prev_rsi_ceiling=100.0,
            setup_current_rsi_floor=0.0,
            setup_current_rsi_ceiling=100.0,
            setup_touch_slack_atr=2.0,
            setup_prev_band_slack_atr=2.0,
            setup_bounce_fraction=0.0,
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
                    'rsi_14',
                    'bb_upper',
                    'bb_middle',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'csp_bullish_reversal_score',
                    'csp_bearish_reversal_score',
                    'csp_bullish_continuation_score',
                    'csp_bearish_continuation_score',
                ],
                [
                    'ema_9',
                    'atr_14',
                    'adx_14',
                    'rsi_14',
                    'bb_middle',
                    'bb_lower',
                ],
            ],
        ):
            dataset = pipeline.build_ema_low_adx_setup_quality_v4_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('slqp_bullish_reversal_score', dataset['feature_columns'])
        self.assertIn('slqp_bearish_continuation_score', dataset['feature_columns'])
        self.assertEqual(dataset['class_codes'], list(SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES))
        self.assertIn('good_vs_rest_positive_rows', dataset['candidate_summary'])
        self.assertEqual(len(dataset['event_rows']), dataset['rows'])
        self.assertTrue(all(row['setup_side'] == 'long' for row in dataset['event_rows']))

    def test_setup_quality_v6_sequence_dataset_includes_cluster_context(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 24.0
        candles['plus_di_14'] = 28.0
        candles['minus_di_14'] = 16.0
        candles['rsi_14'] = 42.0 + pd.Series([math.sin(index / 7.0) * 8.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.2)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_middle'] = rolling_mean
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['csp_bullish_reversal_score'] = 0.15
        candles['csp_bearish_reversal_score'] = 0.12
        candles['csp_bullish_continuation_score'] = 0.18
        candles['csp_bearish_continuation_score'] = 0.11
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M5',
            bars=len(candles),
            network_id='ema_low_adx_setup_quality_cnn_v6',
            feature_profile='ema_low_adx_setup_quality_pattern_score_cluster_context',
            observation_window=18,
            target_horizon=8,
            setup_adx_ceiling=100.0,
            setup_prev_rsi_ceiling=100.0,
            setup_current_rsi_floor=0.0,
            setup_current_rsi_ceiling=100.0,
            setup_touch_slack_atr=2.0,
            setup_prev_band_slack_atr=2.0,
            setup_bounce_fraction=0.0,
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
                    'rsi_14',
                    'bb_upper',
                    'bb_middle',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'csp_bullish_reversal_score',
                    'csp_bearish_reversal_score',
                    'csp_bullish_continuation_score',
                    'csp_bearish_continuation_score',
                ],
                [
                    'ema_9',
                    'atr_14',
                    'adx_14',
                    'rsi_14',
                    'bb_middle',
                    'bb_lower',
                ],
                [
                    'ema_9',
                    'atr_14',
                    'adx_14',
                    'rsi_14',
                    'bb_middle',
                    'bb_lower',
                ],
            ],
        ):
            dataset = pipeline.build_ema_low_adx_setup_quality_v4_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('slqc_recent_candidate_density_12', dataset['feature_columns'])
        self.assertIn('slqc_di_vs_recent_candidate_max_12', dataset['feature_columns'])
        self.assertIn('good_vs_rest_positive_rows', dataset['candidate_summary'])

    def test_setup_quality_v7_sequence_dataset_uses_tp_sl_good_vs_rest_target(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].ewm(span=9, adjust=False).mean()
        candles['ema_21'] = candles['close'].ewm(span=21, adjust=False).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 24.0
        candles['plus_di_14'] = 28.0
        candles['minus_di_14'] = 16.0
        candles['rsi_14'] = 42.0 + pd.Series([math.sin(index / 7.0) * 8.0 for index in range(len(candles))])
        rolling_mean = candles['close'].rolling(window=20, min_periods=1).mean()
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.2)
        candles['bb_upper'] = rolling_mean + (rolling_std * 2.0)
        candles['bb_middle'] = rolling_mean
        candles['bb_lower'] = rolling_mean - (rolling_std * 2.0)
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['csp_bullish_reversal_score'] = 0.15
        candles['csp_bearish_reversal_score'] = 0.12
        candles['csp_bullish_continuation_score'] = 0.18
        candles['csp_bearish_continuation_score'] = 0.11
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M5',
            bars=len(candles),
            network_id='ema_low_adx_setup_quality_cnn_v7',
            feature_profile='ema_low_adx_setup_quality_pattern_score_context',
            observation_window=18,
            target_horizon=8,
            target_reversal_take_profit_atr=1.0,
            target_reversal_stop_loss_atr=1.0,
            setup_adx_ceiling=100.0,
            setup_prev_rsi_ceiling=100.0,
            setup_current_rsi_floor=0.0,
            setup_current_rsi_ceiling=100.0,
            setup_touch_slack_atr=2.0,
            setup_prev_band_slack_atr=2.0,
            setup_bounce_fraction=0.0,
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
                    'rsi_14',
                    'bb_upper',
                    'bb_middle',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'csp_bullish_reversal_score',
                    'csp_bearish_reversal_score',
                    'csp_bullish_continuation_score',
                    'csp_bearish_continuation_score',
                ],
                [
                    'ema_9',
                    'atr_14',
                    'adx_14',
                    'rsi_14',
                    'bb_middle',
                    'bb_lower',
                ],
            ],
        ):
            dataset = pipeline.build_ema_low_adx_setup_quality_v7_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertEqual(dataset['class_codes'], list(SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES))
        self.assertIn('tp_sl_good_vs_rest_positive_rows', dataset['candidate_summary'])
        self.assertIn('target_bullish_tp_sl_code', dataset['event_rows'][0])
        self.assertTrue(all(row['setup_side'] == 'long' for row in dataset['event_rows']))


if __name__ == '__main__':
    unittest.main()

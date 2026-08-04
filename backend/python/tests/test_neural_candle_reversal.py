import math
import unittest
from unittest.mock import patch

import pandas as pd

from backend.python.neural.registry import get_neural_network
from backend.python.neural.runners import (
    _build_supervised_feature_config,
    _build_stage1_setup_targets,
    _search_dual_head_reversal_thresholds,
    _filter_stage1_gate_examples,
    _search_hierarchical_reversal_threshold,
    _search_tri_head_reversal_thresholds,
)
from backend.python.neural.supervised.config import SupervisedFeatureConfig
from backend.python.neural.supervised.features import (
    BasicFeedForwardFeaturePipeline,
    REVERSAL_CLASS_CODES,
    _filter_candle_reversal_target_frame,
)
from backend.python.services.neural_service import _normalize_network_config


def _make_candles(total_rows: int = 360):
    rows = []
    for index in range(total_rows):
        base = 100.0 + (index * 0.015)
        wave_fast = math.sin(index / 6.0) * 2.4
        wave_slow = math.sin(index / 19.0) * 1.3
        close = base + wave_fast + wave_slow
        body_shift = 0.22 if index % 3 == 0 else (-0.16 if index % 3 == 1 else 0.08)
        rows.append({
            'time': index + 1,
            'open': close - body_shift,
            'high': close + 0.55 + abs(math.sin(index / 4.0)) * 0.35,
            'low': close - 0.52 - abs(math.cos(index / 5.0)) * 0.3,
            'close': close,
            'volume': 120 + ((index % 23) * 6),
        })
    return pd.DataFrame(rows)


class CandleReversalCnnTest(unittest.TestCase):
    def test_registry_exposes_candle_reversal_cnn_v1(self):
        network = get_neural_network('candle_reversal_cnn_v1')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v1')
        self.assertEqual(network['score_metric'], 'macro_f1')
        self.assertIn('crx_signed_body_ratio', [target['id'] for target in network['normalization_targets']])

    def test_normalize_network_config_sets_candle_reversal_target_mode(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v1',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9000,
                'observationWindow': 24,
                'kernelSize': 5,
                'normalizationColumns': ['crx_signed_body_ratio', 'invalid_column'],
                'pretrendLookback': 8,
                'pretrendThreshold': 1.4,
                'reversalThreshold': 1.1,
                'dominanceRatio': 1.5,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertEqual(config['kernelSize'], 5)
        self.assertEqual(config['normalizationColumns'], ['crx_signed_body_ratio'])
        self.assertEqual(config['pretrendLookback'], 8)
        self.assertAlmostEqual(config['pretrendThreshold'], 1.4)
        self.assertAlmostEqual(config['reversalThreshold'], 1.1)
        self.assertAlmostEqual(config['dominanceRatio'], 1.5)

    def test_registry_exposes_candle_reversal_cnn_v2(self):
        network = get_neural_network('candle_reversal_cnn_v2')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v2')
        self.assertEqual(network['defaults']['classWeightMode'], 'inverse_frequency')
        self.assertAlmostEqual(float(network['defaults']['classWeightExponent']), 0.75)
        self.assertAlmostEqual(float(network['defaults']['neutralRetention']), 0.35)

    def test_normalize_network_config_sets_candle_reversal_v2_balance_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v2',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9000,
                'classWeightMode': 'inverse_frequency',
                'classWeightExponent': 0.6,
                'neutralRetention': 0.25,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertEqual(config['classWeightMode'], 'inverse_frequency')
        self.assertAlmostEqual(config['classWeightExponent'], 0.6)
        self.assertAlmostEqual(config['neutralRetention'], 0.25)

    def test_registry_exposes_candle_reversal_cnn_v3(self):
        network = get_neural_network('candle_reversal_cnn_v3')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v3')
        self.assertEqual(network['defaults']['classWeightMode'], 'inverse_frequency')
        self.assertAlmostEqual(float(network['defaults']['neutralRetention']), 0.35)
        self.assertIn('hierarchical', network['description'].lower())

    def test_normalize_network_config_sets_candle_reversal_v3_balance_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v3',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'classWeightMode': 'inverse_frequency',
                'classWeightExponent': 0.9,
                'neutralRetention': 0.3,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertEqual(config['classWeightMode'], 'inverse_frequency')
        self.assertAlmostEqual(config['classWeightExponent'], 0.9)
        self.assertAlmostEqual(config['neutralRetention'], 0.3)

    def test_registry_exposes_candle_reversal_cnn_v4(self):
        network = get_neural_network('candle_reversal_cnn_v4')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v4')
        self.assertAlmostEqual(float(network['defaults']['neutralRetention']), 1.0)
        self.assertAlmostEqual(float(network['defaults']['stage1NeutralPretrendCeiling']), 0.85)
        self.assertAlmostEqual(float(network['defaults']['stage1NeutralExcursionCeiling']), 0.85)

    def test_normalize_network_config_sets_candle_reversal_v4_gate_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v4',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'classWeightMode': 'inverse_frequency',
                'classWeightExponent': 0.9,
                'neutralRetention': 1.0,
                'stage1NeutralPretrendCeiling': 0.7,
                'stage1NeutralExcursionCeiling': 0.8,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertEqual(config['classWeightMode'], 'inverse_frequency')
        self.assertAlmostEqual(config['classWeightExponent'], 0.9)
        self.assertAlmostEqual(config['neutralRetention'], 1.0)
        self.assertAlmostEqual(config['stage1NeutralPretrendCeiling'], 0.7)
        self.assertAlmostEqual(config['stage1NeutralExcursionCeiling'], 0.8)

    def test_registry_exposes_candle_reversal_cnn_v5(self):
        network = get_neural_network('candle_reversal_cnn_v5')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v5')
        self.assertAlmostEqual(float(network['defaults']['stage1PositivePretrendFloor']), 1.05)
        self.assertAlmostEqual(float(network['defaults']['stage1PositiveExcursionFloor']), 1.1)

    def test_normalize_network_config_sets_candle_reversal_v5_gate_margin_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v5',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'classWeightMode': 'inverse_frequency',
                'classWeightExponent': 0.9,
                'neutralRetention': 1.0,
                'stage1NeutralPretrendCeiling': 0.7,
                'stage1NeutralExcursionCeiling': 0.8,
                'stage1PositivePretrendFloor': 1.1,
                'stage1PositiveExcursionFloor': 1.2,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertEqual(config['classWeightMode'], 'inverse_frequency')
        self.assertAlmostEqual(config['classWeightExponent'], 0.9)
        self.assertAlmostEqual(config['neutralRetention'], 1.0)
        self.assertAlmostEqual(config['stage1NeutralPretrendCeiling'], 0.7)
        self.assertAlmostEqual(config['stage1NeutralExcursionCeiling'], 0.8)
        self.assertAlmostEqual(config['stage1PositivePretrendFloor'], 1.1)
        self.assertAlmostEqual(config['stage1PositiveExcursionFloor'], 1.2)

    def test_registry_exposes_candle_reversal_cnn_v6(self):
        network = get_neural_network('candle_reversal_cnn_v6')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v6')
        self.assertIn('trend_context', network['feature_set'])
        self.assertIn('crx_ema_gap_9_21_ratio', [target['id'] for target in network['normalization_targets']])

    def test_normalize_network_config_sets_candle_reversal_v6_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v6',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'classWeightMode': 'inverse_frequency',
                'classWeightExponent': 0.85,
                'neutralRetention': 0.3,
                'normalizationColumns': ['crx_ema_gap_9_21_ratio', 'crx_bb_position', 'invalid_column'],
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertEqual(config['classWeightMode'], 'inverse_frequency')
        self.assertAlmostEqual(config['classWeightExponent'], 0.85)
        self.assertAlmostEqual(config['neutralRetention'], 0.3)
        self.assertEqual(config['normalizationColumns'], ['crx_ema_gap_9_21_ratio', 'crx_bb_position'])

    def test_registry_exposes_candle_reversal_cnn_v7(self):
        network = get_neural_network('candle_reversal_cnn_v7')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v7')
        self.assertAlmostEqual(float(network['defaults']['targetCleanNeutralPretrendCeiling']), 0.9)
        self.assertIn('crx_bb_width_ratio', [target['id'] for target in network['normalization_targets']])

    def test_normalize_network_config_sets_candle_reversal_v7_clean_target_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v7',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'targetCleanNeutralPretrendCeiling': 0.8,
                'targetCleanNeutralExcursionCeiling': 0.75,
                'targetCleanPositivePretrendFloor': 1.1,
                'targetCleanPositiveExcursionFloor': 1.15,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertAlmostEqual(config['targetCleanNeutralPretrendCeiling'], 0.8)
        self.assertAlmostEqual(config['targetCleanNeutralExcursionCeiling'], 0.75)
        self.assertAlmostEqual(config['targetCleanPositivePretrendFloor'], 1.1)
        self.assertAlmostEqual(config['targetCleanPositiveExcursionFloor'], 1.15)

    def test_registry_exposes_candle_reversal_cnn_v7_1(self):
        network = get_neural_network('candle_reversal_cnn_v7_1')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v7_1')
        self.assertAlmostEqual(float(network['defaults']['neutralRetention']), 1.0)
        self.assertAlmostEqual(float(network['defaults']['targetCleanNeutralPretrendCeiling']), 1.0)
        self.assertAlmostEqual(float(network['defaults']['targetCleanNeutralExcursionCeiling']), 1.0)

    def test_normalize_network_config_sets_candle_reversal_v7_1_relaxed_clean_target_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v7_1',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'neutralRetention': 1.0,
                'targetCleanNeutralPretrendCeiling': 1.0,
                'targetCleanNeutralExcursionCeiling': 1.0,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertAlmostEqual(config['neutralRetention'], 1.0)
        self.assertAlmostEqual(config['targetCleanNeutralPretrendCeiling'], 1.0)
        self.assertAlmostEqual(config['targetCleanNeutralExcursionCeiling'], 1.0)

    def test_registry_exposes_candle_reversal_cnn_v8(self):
        network = get_neural_network('candle_reversal_cnn_v8')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v8')
        self.assertAlmostEqual(float(network['defaults']['stage1SetupPretrendFloor']), 1.15)
        self.assertAlmostEqual(float(network['defaults']['stage1SetupExcursionFloor']), 1.1)
        self.assertAlmostEqual(float(network['defaults']['stage1SetupDominanceFloor']), 2.0)

    def test_normalize_network_config_sets_candle_reversal_v8_setup_gate_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v8',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'stage1SetupPretrendFloor': 1.2,
                'stage1SetupExcursionFloor': 1.15,
                'stage1SetupDominanceFloor': 2.4,
                'stage1SetupMarginFloor': 0.35,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertAlmostEqual(config['stage1SetupPretrendFloor'], 1.2)
        self.assertAlmostEqual(config['stage1SetupExcursionFloor'], 1.15)
        self.assertAlmostEqual(config['stage1SetupDominanceFloor'], 2.4)
        self.assertAlmostEqual(config['stage1SetupMarginFloor'], 0.35)

    def test_registry_exposes_candle_reversal_cnn_v9(self):
        network = get_neural_network('candle_reversal_cnn_v9')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v9')
        self.assertIn('dual-head', network['description'].lower())

    def test_normalize_network_config_sets_candle_reversal_v9_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v9',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'neutralRetention': 1.0,
                'targetCleanNeutralPretrendCeiling': 1.0,
                'targetCleanNeutralExcursionCeiling': 1.0,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertAlmostEqual(config['neutralRetention'], 1.0)
        self.assertAlmostEqual(config['targetCleanNeutralPretrendCeiling'], 1.0)
        self.assertAlmostEqual(config['targetCleanNeutralExcursionCeiling'], 1.0)

    def test_registry_exposes_candle_reversal_cnn_v10(self):
        network = get_neural_network('candle_reversal_cnn_v10')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v10')
        self.assertIn('tri-head', network['description'].lower())

    def test_normalize_network_config_sets_candle_reversal_v10_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v10',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'neutralRetention': 1.0,
                'targetCleanNeutralPretrendCeiling': 1.0,
                'targetCleanNeutralExcursionCeiling': 1.0,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertAlmostEqual(config['neutralRetention'], 1.0)
        self.assertAlmostEqual(config['targetCleanNeutralPretrendCeiling'], 1.0)
        self.assertAlmostEqual(config['targetCleanNeutralExcursionCeiling'], 1.0)

    def test_registry_exposes_candle_reversal_cnn_v10_1(self):
        network = get_neural_network('candle_reversal_cnn_v10_1')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v10_1')
        self.assertIn('tri-head', network['signature'].lower())

    def test_normalize_network_config_sets_candle_reversal_v10_1_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v10_1',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'neutralRetention': 1.0,
                'directionalHeadRestRetention': 0.55,
                'targetCleanNeutralPretrendCeiling': 1.0,
                'targetCleanNeutralExcursionCeiling': 1.0,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_classification')
        self.assertAlmostEqual(config['neutralRetention'], 1.0)
        self.assertAlmostEqual(config['directionalHeadRestRetention'], 0.55)
        self.assertAlmostEqual(config['targetCleanNeutralPretrendCeiling'], 1.0)
        self.assertAlmostEqual(config['targetCleanNeutralExcursionCeiling'], 1.0)

    def test_registry_exposes_candle_reversal_cnn_v11(self):
        network = get_neural_network('candle_reversal_cnn_v11')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v11')
        self.assertIn('candlestick_pattern_scores', network['feature_set'])
        self.assertIn('crxp_hammer', [target['id'] for target in network['normalization_targets']])

    def test_registry_exposes_candle_reversal_cnn_v11_scores_only(self):
        network = get_neural_network('candle_reversal_cnn_v11_scores_only')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v11_scores_only')
        self.assertIn('candlestick_pattern_scores', network['feature_set'])
        self.assertNotIn('candlestick_pattern_flags', network['feature_set'])
        normalization_targets = [target['id'] for target in network['normalization_targets']]
        self.assertIn('crxp_bullish_reversal_score', normalization_targets)
        self.assertNotIn('crxp_hammer', normalization_targets)

    def test_registry_exposes_candle_reversal_cnn_v12_scores_only(self):
        network = get_neural_network('candle_reversal_cnn_v12_scores_only')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_cnn_v12_scores_only')
        self.assertIn('candlestick_pattern_scores', network['feature_set'])
        self.assertEqual(network['defaults']['targetMode'], 'future_candle_reversal_tp_sl_classification')
        self.assertAlmostEqual(float(network['defaults']['reversalTakeProfitAtr']), 0.75)
        self.assertAlmostEqual(float(network['defaults']['reversalStopLossAtr']), 1.0)
        schema_keys = [field['key'] for field in network['parameter_schema']]
        self.assertIn('reversalTakeProfitAtr', schema_keys)
        self.assertIn('reversalStopLossAtr', schema_keys)
        self.assertNotIn('reversalThreshold', schema_keys)
        self.assertNotIn('dominanceRatio', schema_keys)

    def test_registry_exposes_candle_reversal_setup_quality_cnn_v1(self):
        network = get_neural_network('candle_reversal_setup_quality_cnn_v1')

        self.assertIsNotNone(network)
        self.assertEqual(network['family'], 'supervised_learning')
        self.assertEqual(network['architecture_type'], 'convolutional')
        self.assertEqual(network['runner_id'], 'candle_reversal_setup_quality_cnn_v1')
        self.assertIn('candlestick_pattern_scores', network['feature_set'])
        self.assertEqual(network['defaults']['targetMode'], 'candle_reversal_setup_quality_good_vs_rest_classification')
        self.assertEqual(network['score_metric'], 'class_good_setup_f1')
        schema_keys = [field['key'] for field in network['parameter_schema']]
        self.assertIn('reversalTakeProfitAtr', schema_keys)
        self.assertIn('reversalStopLossAtr', schema_keys)
        self.assertIn('classWeightMode', schema_keys)
        self.assertIn('classWeightExponent', schema_keys)
        self.assertNotIn('directionalHeadRestRetention', schema_keys)
        self.assertNotIn('neutralRetention', schema_keys)

    def test_normalize_network_config_sets_candle_reversal_v12_tp_sl_controls(self):
        config = _normalize_network_config(
            'candle_reversal_cnn_v12_scores_only',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'neutralRetention': 1.0,
                'directionalHeadRestRetention': 0.55,
                'targetCleanNeutralPretrendCeiling': 1.0,
                'targetCleanNeutralExcursionCeiling': 1.0,
                'reversalTakeProfitAtr': 0.8,
                'reversalStopLossAtr': 1.15,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'future_candle_reversal_tp_sl_classification')
        self.assertAlmostEqual(config['directionalHeadRestRetention'], 0.55)
        self.assertAlmostEqual(config['reversalTakeProfitAtr'], 0.8)
        self.assertAlmostEqual(config['reversalStopLossAtr'], 1.15)
        self.assertNotIn('reversalThreshold', config)
        self.assertNotIn('dominanceRatio', config)

    def test_normalize_network_config_sets_candle_reversal_setup_quality_v1_controls(self):
        config = _normalize_network_config(
            'candle_reversal_setup_quality_cnn_v1',
            {
                'symbol': 'eurusd',
                'timeframe': 'm15',
                'bars': 9500,
                'pretrendLookback': 10,
                'pretrendThreshold': 1.35,
                'reversalTakeProfitAtr': 0.8,
                'reversalStopLossAtr': 1.1,
                'classWeightMode': 'inverse_frequency',
                'classWeightExponent': 0.65,
            },
        )

        self.assertEqual(config['symbol'], 'EURUSD')
        self.assertEqual(config['timeframe'], 'M15')
        self.assertEqual(config['targetMode'], 'candle_reversal_setup_quality_good_vs_rest_classification')
        self.assertEqual(config['pretrendLookback'], 10)
        self.assertAlmostEqual(config['pretrendThreshold'], 1.35)
        self.assertAlmostEqual(config['reversalTakeProfitAtr'], 0.8)
        self.assertAlmostEqual(config['reversalStopLossAtr'], 1.1)
        self.assertEqual(config['classWeightMode'], 'inverse_frequency')
        self.assertAlmostEqual(config['classWeightExponent'], 0.65)

    def test_v7_1_uses_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_cnn_v7_1',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_context')

    def test_v8_uses_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_cnn_v8',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_context')

    def test_v9_uses_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_cnn_v9',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_context')

    def test_v10_uses_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_cnn_v10',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_context')

    def test_v10_1_uses_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_cnn_v10_1',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_context')

    def test_v11_uses_pattern_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_cnn_v11',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_pattern_context')

    def test_v11_scores_only_uses_pattern_score_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_cnn_v11_scores_only',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_pattern_score_context')

    def test_v12_scores_only_uses_pattern_score_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_cnn_v12_scores_only',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
            'targetMode': 'future_candle_reversal_tp_sl_classification',
            'reversalTakeProfitAtr': 0.8,
            'reversalStopLossAtr': 1.1,
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_pattern_score_context')
        self.assertEqual(config.target_mode, 'future_candle_reversal_tp_sl_classification')
        self.assertAlmostEqual(config.target_reversal_take_profit_atr, 0.8)
        self.assertAlmostEqual(config.target_reversal_stop_loss_atr, 1.1)

    def test_setup_quality_v1_uses_pattern_score_context_feature_profile(self):
        config = _build_supervised_feature_config({
            'networkId': 'candle_reversal_setup_quality_cnn_v1',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'bars': 10000,
            'normalizationColumns': [],
            'targetMode': 'candle_reversal_setup_quality_good_vs_rest_classification',
            'reversalTakeProfitAtr': 0.8,
            'reversalStopLossAtr': 1.1,
        })

        self.assertEqual(config.feature_profile, 'candle_reversal_pattern_score_context')
        self.assertEqual(config.target_mode, 'candle_reversal_setup_quality_good_vs_rest_classification')
        self.assertAlmostEqual(config.target_reversal_take_profit_atr, 0.8)
        self.assertAlmostEqual(config.target_reversal_stop_loss_atr, 1.1)

    def test_reversal_sequence_dataset_uses_allowed_reversal_codes(self):
        candles = _make_candles()
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M15',
            bars=len(candles),
            network_id='candle_reversal_cnn_v1',
            feature_profile='candle_reversal',
            observation_window=16,
            target_horizon=6,
            target_mode='future_candle_reversal_classification',
            target_pretrend_lookback=6,
            target_pretrend_threshold=1.2,
            target_reversal_threshold=1.0,
            target_dominance_ratio=1.35,
        )
        pipeline = BasicFeedForwardFeaturePipeline.from_candles(config, candles.to_dict(orient='records'))
        dataset = pipeline.build_candle_reversal_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('crx_signed_body_ratio', dataset['feature_columns'])
        self.assertIn('crx_wick_imbalance_ratio', dataset['feature_columns'])
        self.assertEqual(dataset['class_codes'], REVERSAL_CLASS_CODES)
        self.assertTrue(set(dataset['y_class']).issubset(set(range(len(REVERSAL_CLASS_CODES)))))
        self.assertEqual(dataset['target_context_columns'], ['target_prev_move_atr', 'target_future_upside_atr', 'target_future_downside_atr'])
        self.assertEqual(len(dataset['target_context']), dataset['rows'])

    def test_reversal_context_sequence_dataset_exposes_context_columns(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].rolling(window=9, min_periods=1).mean()
        candles['ema_21'] = candles['close'].rolling(window=21, min_periods=1).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 27.5
        candles['plus_di_14'] = 31.0
        candles['minus_di_14'] = 18.0
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0)
        candles['bb_upper'] = candles['close'] + (rolling_std * 2.0) + 0.1
        candles['bb_lower'] = candles['close'] - (rolling_std * 2.0) - 0.1
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M15',
            bars=len(candles),
            network_id='candle_reversal_cnn_v6',
            feature_profile='candle_reversal_context',
            observation_window=16,
            target_horizon=6,
            target_mode='future_candle_reversal_classification',
            target_pretrend_lookback=6,
            target_pretrend_threshold=1.2,
            target_reversal_threshold=1.0,
            target_dominance_ratio=1.35,
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
                'bb_upper',
                'bb_lower',
                'bb_width',
            ],
        ):
            dataset = pipeline.build_candle_reversal_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('crx_ema_gap_9_21_ratio', dataset['feature_columns'])
        self.assertIn('crx_adx_14', dataset['feature_columns'])
        self.assertIn('crx_bb_position', dataset['feature_columns'])

    def test_reversal_pattern_context_dataset_exposes_pattern_columns(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].rolling(window=9, min_periods=1).mean()
        candles['ema_21'] = candles['close'].rolling(window=21, min_periods=1).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 27.5
        candles['plus_di_14'] = 31.0
        candles['minus_di_14'] = 18.0
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0)
        candles['bb_upper'] = candles['close'] + (rolling_std * 2.0) + 0.1
        candles['bb_lower'] = candles['close'] - (rolling_std * 2.0) - 0.1
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['pattern_bullish_reversal_score'] = 0.2
        candles['pattern_bearish_reversal_score'] = 0.1
        candles['pattern_bullish_continuation_score'] = 0.0
        candles['pattern_bearish_continuation_score'] = 0.3
        candles['pattern_hammer'] = 0.0
        candles['pattern_shooting_star'] = 0.0
        candles['pattern_bullish_engulfing'] = 0.0
        candles['pattern_bearish_engulfing'] = 1.0
        candles['pattern_bullish_harami'] = 0.0
        candles['pattern_bearish_harami'] = 0.0
        candles['pattern_morning_star'] = 0.0
        candles['pattern_evening_star'] = 0.0
        candles['pattern_rising_three_methods'] = 0.0
        candles['pattern_falling_three_methods'] = 0.0
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M15',
            bars=len(candles),
            network_id='candle_reversal_cnn_v11',
            feature_profile='candle_reversal_pattern_context',
            observation_window=16,
            target_horizon=6,
            target_mode='future_candle_reversal_classification',
            target_pretrend_lookback=6,
            target_pretrend_threshold=1.2,
            target_reversal_threshold=1.0,
            target_dominance_ratio=1.35,
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
                    'bb_upper',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'pattern_bullish_reversal_score',
                    'pattern_bearish_reversal_score',
                    'pattern_bullish_continuation_score',
                    'pattern_bearish_continuation_score',
                ],
                [
                    'pattern_hammer',
                    'pattern_shooting_star',
                    'pattern_bullish_engulfing',
                    'pattern_bearish_engulfing',
                    'pattern_bullish_harami',
                    'pattern_bearish_harami',
                    'pattern_morning_star',
                    'pattern_evening_star',
                    'pattern_rising_three_methods',
                    'pattern_falling_three_methods',
                ],
            ],
        ):
            dataset = pipeline.build_candle_reversal_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('crxp_bullish_reversal_score', dataset['feature_columns'])
        self.assertIn('crxp_bearish_continuation_score', dataset['feature_columns'])
        self.assertIn('crxp_bearish_engulfing', dataset['feature_columns'])

    def test_reversal_pattern_score_context_dataset_exposes_only_score_columns(self):
        candles = _make_candles()
        candles['ema_9'] = candles['close'].rolling(window=9, min_periods=1).mean()
        candles['ema_21'] = candles['close'].rolling(window=21, min_periods=1).mean()
        candles['atr_14'] = 0.45
        candles['adx_14'] = 27.5
        candles['plus_di_14'] = 31.0
        candles['minus_di_14'] = 18.0
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0)
        candles['bb_upper'] = candles['close'] + (rolling_std * 2.0) + 0.1
        candles['bb_lower'] = candles['close'] - (rolling_std * 2.0) - 0.1
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['pattern_bullish_reversal_score'] = 0.2
        candles['pattern_bearish_reversal_score'] = 0.1
        candles['pattern_bullish_continuation_score'] = 0.0
        candles['pattern_bearish_continuation_score'] = 0.3
        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M15',
            bars=len(candles),
            network_id='candle_reversal_cnn_v11_scores_only',
            feature_profile='candle_reversal_pattern_score_context',
            observation_window=16,
            target_horizon=6,
            target_mode='future_candle_reversal_classification',
            target_pretrend_lookback=6,
            target_pretrend_threshold=1.2,
            target_reversal_threshold=1.0,
            target_dominance_ratio=1.35,
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
                    'bb_upper',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'pattern_bullish_reversal_score',
                    'pattern_bearish_reversal_score',
                    'pattern_bullish_continuation_score',
                    'pattern_bearish_continuation_score',
                ],
            ],
        ):
            dataset = pipeline.build_candle_reversal_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertIn('crxp_bullish_reversal_score', dataset['feature_columns'])
        self.assertIn('crxp_bearish_continuation_score', dataset['feature_columns'])
        self.assertNotIn('crxp_bearish_engulfing', dataset['feature_columns'])

    def test_reversal_tp_sl_target_uses_first_touch_economic_labels(self):
        candles = _make_candles()
        scripted_path = [
            {'open': 100.1, 'high': 100.4, 'low': 99.6, 'close': 100.0, 'volume': 100},
            {'open': 99.1, 'high': 99.4, 'low': 98.6, 'close': 99.0, 'volume': 105},
            {'open': 98.1, 'high': 98.4, 'low': 97.6, 'close': 98.0, 'volume': 110},
            {'open': 96.1, 'high': 96.4, 'low': 95.6, 'close': 96.0, 'volume': 115},
            {'open': 95.1, 'high': 95.4, 'low': 94.6, 'close': 95.0, 'volume': 120},
            {'open': 96.2, 'high': 96.4, 'low': 94.7, 'close': 96.3, 'volume': 125},
            {'open': 97.1, 'high': 97.4, 'low': 96.6, 'close': 97.0, 'volume': 130},
            {'open': 100.1, 'high': 100.4, 'low': 99.6, 'close': 100.0, 'volume': 135},
            {'open': 103.1, 'high': 103.4, 'low': 102.6, 'close': 103.0, 'volume': 140},
            {'open': 102.2, 'high': 103.2, 'low': 101.9, 'close': 102.1, 'volume': 145},
            {'open': 98.1, 'high': 98.4, 'low': 97.6, 'close': 98.0, 'volume': 150},
            {'open': 97.1, 'high': 97.4, 'low': 96.6, 'close': 97.0, 'volume': 155},
            {'open': 95.1, 'high': 95.4, 'low': 94.6, 'close': 95.0, 'volume': 160},
            {'open': 94.2, 'high': 95.4, 'low': 93.8, 'close': 94.2, 'volume': 165},
            {'open': 96.0, 'high': 96.2, 'low': 94.5, 'close': 96.0, 'volume': 170},
        ]
        start_index = 220
        for offset, row in enumerate(scripted_path):
            for key, value in row.items():
                candles.loc[start_index + offset, key] = value

        candles['ema_9'] = candles['close'].rolling(window=9, min_periods=1).mean()
        candles['ema_21'] = candles['close'].rolling(window=21, min_periods=1).mean()
        candles['atr_14'] = 1.0
        candles['adx_14'] = 25.0
        candles['plus_di_14'] = 30.0
        candles['minus_di_14'] = 20.0
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0)
        candles['bb_upper'] = candles['close'] + (rolling_std * 2.0) + 0.1
        candles['bb_lower'] = candles['close'] - (rolling_std * 2.0) - 0.1
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['pattern_bullish_reversal_score'] = 0.25
        candles['pattern_bearish_reversal_score'] = 0.2
        candles['pattern_bullish_continuation_score'] = 0.1
        candles['pattern_bearish_continuation_score'] = 0.15

        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M15',
            bars=len(candles),
            network_id='candle_reversal_cnn_v12_scores_only',
            feature_profile='candle_reversal_pattern_score_context',
            observation_window=1,
            target_horizon=2,
            target_mode='future_candle_reversal_tp_sl_classification',
            target_pretrend_lookback=2,
            target_pretrend_threshold=2.0,
            target_reversal_take_profit_atr=0.75,
            target_reversal_stop_loss_atr=1.0,
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
                    'bb_upper',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'pattern_bullish_reversal_score',
                    'pattern_bearish_reversal_score',
                    'pattern_bullish_continuation_score',
                    'pattern_bearish_continuation_score',
                ],
            ],
        ), patch.object(
            BasicFeedForwardFeaturePipeline,
            '_build_atr_ratio',
            return_value=(1.0 / candles['close']).astype(float),
        ):
            frame = pipeline.build_candle_reversal_classification_dataset()

        self.assertIn('target_bullish_tp_sl_code', frame.columns)
        self.assertIn('target_bearish_tp_sl_code', frame.columns)
        self.assertIn('target_bullish_resolution_code', frame.columns)
        self.assertIn('target_bearish_resolution_code', frame.columns)
        class_counts = frame['target_reversal_code'].value_counts().to_dict()
        self.assertGreaterEqual(int(class_counts.get(1, 0)), 1)
        self.assertGreaterEqual(int(class_counts.get(-1, 0)), 1)
        self.assertGreaterEqual(int(class_counts.get(0, 0)), 1)
        self.assertIn(-1, frame['target_bullish_tp_sl_code'].tolist())

    def test_reversal_setup_quality_target_uses_candidate_good_vs_rest_labels(self):
        candles = _make_candles()
        scripted_path = [
            {'open': 100.1, 'high': 100.4, 'low': 99.6, 'close': 100.0, 'volume': 100},
            {'open': 99.1, 'high': 99.4, 'low': 98.6, 'close': 99.0, 'volume': 105},
            {'open': 98.1, 'high': 98.4, 'low': 97.6, 'close': 98.0, 'volume': 110},
            {'open': 96.1, 'high': 96.4, 'low': 95.6, 'close': 96.0, 'volume': 115},
            {'open': 95.1, 'high': 95.4, 'low': 94.6, 'close': 95.0, 'volume': 120},
            {'open': 96.2, 'high': 96.4, 'low': 94.7, 'close': 96.3, 'volume': 125},
            {'open': 97.1, 'high': 97.4, 'low': 96.6, 'close': 97.0, 'volume': 130},
            {'open': 100.1, 'high': 100.4, 'low': 99.6, 'close': 100.0, 'volume': 135},
            {'open': 103.1, 'high': 103.4, 'low': 102.6, 'close': 103.0, 'volume': 140},
            {'open': 102.2, 'high': 103.2, 'low': 101.9, 'close': 102.1, 'volume': 145},
            {'open': 98.1, 'high': 98.4, 'low': 97.6, 'close': 98.0, 'volume': 150},
            {'open': 97.1, 'high': 97.4, 'low': 96.6, 'close': 97.0, 'volume': 155},
            {'open': 95.1, 'high': 95.4, 'low': 94.6, 'close': 95.0, 'volume': 160},
            {'open': 94.2, 'high': 95.4, 'low': 93.8, 'close': 94.2, 'volume': 165},
            {'open': 96.0, 'high': 96.2, 'low': 94.5, 'close': 96.0, 'volume': 170},
        ]
        start_index = 220
        for offset, row in enumerate(scripted_path):
            for key, value in row.items():
                candles.loc[start_index + offset, key] = value

        candles['ema_9'] = candles['close'].rolling(window=9, min_periods=1).mean()
        candles['ema_21'] = candles['close'].rolling(window=21, min_periods=1).mean()
        candles['atr_14'] = 1.0
        candles['adx_14'] = 25.0
        candles['plus_di_14'] = 30.0
        candles['minus_di_14'] = 20.0
        rolling_std = candles['close'].rolling(window=20, min_periods=1).std(ddof=0).fillna(0.0)
        candles['bb_upper'] = candles['close'] + (rolling_std * 2.0) + 0.1
        candles['bb_lower'] = candles['close'] - (rolling_std * 2.0) - 0.1
        candles['bb_width'] = candles['bb_upper'] - candles['bb_lower']
        candles['pattern_bullish_reversal_score'] = 0.25
        candles['pattern_bearish_reversal_score'] = 0.2
        candles['pattern_bullish_continuation_score'] = 0.1
        candles['pattern_bearish_continuation_score'] = 0.15

        config = SupervisedFeatureConfig(
            symbol_name='TEST',
            timeframe='M15',
            bars=len(candles),
            network_id='candle_reversal_setup_quality_cnn_v1',
            feature_profile='candle_reversal_pattern_score_context',
            observation_window=1,
            target_horizon=2,
            target_mode='candle_reversal_setup_quality_good_vs_rest_classification',
            target_pretrend_lookback=2,
            target_pretrend_threshold=2.0,
            target_reversal_take_profit_atr=0.75,
            target_reversal_stop_loss_atr=1.0,
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
                    'bb_upper',
                    'bb_lower',
                    'bb_width',
                ],
                [
                    'pattern_bullish_reversal_score',
                    'pattern_bearish_reversal_score',
                    'pattern_bullish_continuation_score',
                    'pattern_bearish_continuation_score',
                ],
            ],
        ), patch.object(
            BasicFeedForwardFeaturePipeline,
            '_build_atr_ratio',
            return_value=(1.0 / candles['close']).astype(float),
        ):
            dataset = pipeline.build_candle_reversal_setup_quality_v1_sequence_dataset()

        self.assertGreater(dataset['rows'], 0)
        self.assertEqual(dataset['class_codes'], [0, 1])
        self.assertEqual(dataset['class_labels'][0], 'not_good_setup')
        self.assertEqual(dataset['class_labels'][1], 'good_setup')
        self.assertGreater(dataset['candidate_summary']['candidate_rows'], 0)
        self.assertGreater(dataset['candidate_summary']['good_rows'], 0)
        self.assertGreater(dataset['candidate_summary']['rest_rows'], 0)
        self.assertIn('crxp_bullish_reversal_score', dataset['feature_columns'])
        self.assertEqual({'long', 'short'}, {row['setup_side'] for row in dataset['event_rows']})

    def test_clean_target_filter_can_drop_ambiguous_middle_bucket(self):
        frame = pd.DataFrame([
            {'target_reversal_code': -1, 'target_prev_move_atr': 1.4, 'target_future_upside_atr': 0.2, 'target_future_downside_atr': 1.3},
            {'target_reversal_code': 1, 'target_prev_move_atr': -1.5, 'target_future_upside_atr': 1.4, 'target_future_downside_atr': 0.2},
            {'target_reversal_code': 0, 'target_prev_move_atr': 0.3, 'target_future_upside_atr': 0.4, 'target_future_downside_atr': 0.3},
            {'target_reversal_code': 0, 'target_prev_move_atr': 1.0, 'target_future_upside_atr': 0.9, 'target_future_downside_atr': 0.8},
        ])

        filtered, summary = _filter_candle_reversal_target_frame(
            frame,
            pretrend_threshold=1.2,
            reversal_threshold=1.0,
            positive_pretrend_floor=1.0,
            positive_excursion_floor=1.0,
            neutral_pretrend_ceiling=0.8,
            neutral_excursion_ceiling=0.8,
        )

        self.assertTrue(summary['applied'])
        self.assertEqual(summary['rows_before'], 4)
        self.assertEqual(summary['rows_after'], 3)
        self.assertEqual(summary['class_counts_before'], {-1: 1, 0: 2, 1: 1})
        self.assertEqual(summary['class_counts_after'], {-1: 1, 0: 1, 1: 1})
        self.assertEqual(filtered['target_reversal_code'].tolist(), [-1, 1, 0])

    def test_relaxed_clean_target_filter_keeps_more_neutral_rows(self):
        frame = pd.DataFrame([
            {'target_reversal_code': -1, 'target_prev_move_atr': 1.4, 'target_future_upside_atr': 0.2, 'target_future_downside_atr': 1.3},
            {'target_reversal_code': 1, 'target_prev_move_atr': -1.5, 'target_future_upside_atr': 1.4, 'target_future_downside_atr': 0.2},
            {'target_reversal_code': 0, 'target_prev_move_atr': 0.3, 'target_future_upside_atr': 0.4, 'target_future_downside_atr': 0.3},
            {'target_reversal_code': 0, 'target_prev_move_atr': 1.0, 'target_future_upside_atr': 0.95, 'target_future_downside_atr': 0.8},
        ])

        strict_filtered, strict_summary = _filter_candle_reversal_target_frame(
            frame,
            pretrend_threshold=1.2,
            reversal_threshold=1.0,
            positive_pretrend_floor=1.0,
            positive_excursion_floor=1.0,
            neutral_pretrend_ceiling=0.9,
            neutral_excursion_ceiling=0.9,
        )
        relaxed_filtered, relaxed_summary = _filter_candle_reversal_target_frame(
            frame,
            pretrend_threshold=1.2,
            reversal_threshold=1.0,
            positive_pretrend_floor=1.0,
            positive_excursion_floor=1.0,
            neutral_pretrend_ceiling=1.0,
            neutral_excursion_ceiling=1.0,
        )

        self.assertEqual(strict_summary['neutral_rows_after'], 1)
        self.assertEqual(relaxed_summary['neutral_rows_after'], 2)
        self.assertEqual(len(strict_filtered), 3)
        self.assertEqual(len(relaxed_filtered), 4)

    def test_hierarchical_threshold_search_can_escape_always_neutral_prediction(self):
        y_true = [0, 1, 2, 1]
        reversal_probabilities = [
            [0.85, 0.15],
            [0.42, 0.58],
            [0.38, 0.62],
            [0.75, 0.25],
        ]
        direction_probabilities = [
            [0.80, 0.20],
            [0.70, 0.30],
            [0.25, 0.75],
            [0.55, 0.45],
        ]

        result = _search_hierarchical_reversal_threshold(
            y_true,
            reversal_probabilities,
            direction_probabilities,
            class_codes=REVERSAL_CLASS_CODES,
            class_labels={-1: 'bearish_reversal', 0: 'no_reversal', 1: 'bullish_reversal'},
            neutral_class_index=1,
            bearish_class_index=0,
            bullish_class_index=2,
        )

        self.assertGreaterEqual(result['threshold'], 0.2)
        self.assertLessEqual(result['threshold'], 0.8)
        self.assertGreater(result['metrics']['predicted_transition_rate'], 0.0)
        self.assertGreater(result['metrics']['macro_f1'], 0.3)

    def test_dual_head_threshold_search_can_escape_always_neutral_prediction(self):
        y_true = [1, 0, 2, 0]
        bearish_probabilities = [
            [0.35, 0.65],
            [0.80, 0.20],
            [0.78, 0.22],
            [0.45, 0.55],
        ]
        bullish_probabilities = [
            [0.70, 0.30],
            [0.82, 0.18],
            [0.25, 0.75],
            [0.40, 0.60],
        ]

        result = _search_dual_head_reversal_thresholds(
            y_true,
            bearish_probabilities,
            bullish_probabilities,
            class_codes=REVERSAL_CLASS_CODES,
            class_labels={-1: 'bearish_reversal', 0: 'no_reversal', 1: 'bullish_reversal'},
            neutral_class_index=1,
            bearish_class_index=0,
            bullish_class_index=2,
        )

        self.assertGreaterEqual(result['bearish_threshold'], 0.2)
        self.assertLessEqual(result['bearish_threshold'], 0.8)
        self.assertGreaterEqual(result['bullish_threshold'], 0.2)
        self.assertLessEqual(result['bullish_threshold'], 0.8)
        self.assertGreater(result['metrics']['predicted_transition_rate'], 0.0)
        self.assertGreater(result['metrics']['macro_f1'], 0.3)

    def test_tri_head_threshold_search_can_recover_neutral_class(self):
        y_true = [1, 0, 2, 0, 1, 2]
        bearish_probabilities = [
            [0.20, 0.80],
            [0.80, 0.20],
            [0.82, 0.18],
            [0.70, 0.30],
            [0.24, 0.76],
            [0.75, 0.25],
        ]
        neutral_probabilities = [
            [0.78, 0.22],
            [0.20, 0.80],
            [0.72, 0.28],
            [0.18, 0.82],
            [0.81, 0.19],
            [0.68, 0.32],
        ]
        bullish_probabilities = [
            [0.72, 0.28],
            [0.78, 0.22],
            [0.18, 0.82],
            [0.74, 0.26],
            [0.79, 0.21],
            [0.22, 0.78],
        ]

        result = _search_tri_head_reversal_thresholds(
            y_true,
            bearish_probabilities,
            neutral_probabilities,
            bullish_probabilities,
            class_codes=REVERSAL_CLASS_CODES,
            class_labels={-1: 'bearish_reversal', 0: 'no_reversal', 1: 'bullish_reversal'},
            neutral_class_index=1,
            bearish_class_index=0,
            bullish_class_index=2,
        )

        self.assertGreaterEqual(result['bearish_threshold'], 0.2)
        self.assertLessEqual(result['bearish_threshold'], 0.8)
        self.assertGreaterEqual(result['neutral_threshold'], 0.2)
        self.assertLessEqual(result['neutral_threshold'], 0.8)
        self.assertGreaterEqual(result['bullish_threshold'], 0.2)
        self.assertLessEqual(result['bullish_threshold'], 0.8)
        self.assertGreater(result['metrics']['class_no_reversal_recall'], 0.0)
        self.assertGreater(result['metrics']['macro_f1'], 0.3)

    def test_stage1_gate_filter_keeps_clean_neutral_examples_and_all_positives(self):
        X = [[[0.0]], [[1.0]], [[2.0]], [[3.0]]]
        y = [0, 0, 1, 1]
        target_context = [
            [0.2, 0.3, 0.2],
            [1.1, 1.0, 0.9],
            [1.5, 1.4, 0.3],
            [1.6, 0.2, 1.5],
        ]

        X_filtered, y_filtered, context_filtered, summary = _filter_stage1_gate_examples(
            X,
            y,
            target_context,
            neutral_class_index=0,
            pretrend_threshold=1.2,
            reversal_threshold=1.0,
            neutral_pretrend_ceiling=0.85,
            neutral_excursion_ceiling=0.85,
        )

        self.assertFalse(summary['fallback_applied'])
        self.assertEqual(summary['rows_before'], 4)
        self.assertEqual(summary['rows_after'], 3)
        self.assertEqual(summary['neutral_candidates_after'], 1)
        self.assertEqual(list(y_filtered), [0, 1, 1])
        self.assertEqual(len(X_filtered), 3)
        self.assertEqual(len(context_filtered), 3)

    def test_stage1_gate_filter_can_keep_only_strong_positive_examples(self):
        X = [[[0.0]], [[1.0]], [[2.0]], [[3.0]], [[4.0]]]
        y = [0, 0, 1, 1, 1]
        target_context = [
            [0.2, 0.3, 0.2],
            [0.3, 0.2, 0.4],
            [1.15, 1.05, 0.2],
            [1.45, 1.30, 0.2],
            [1.6, 0.2, 1.5],
        ]

        X_filtered, y_filtered, context_filtered, summary = _filter_stage1_gate_examples(
            X,
            y,
            target_context,
            neutral_class_index=0,
            pretrend_threshold=1.2,
            reversal_threshold=1.0,
            neutral_pretrend_ceiling=0.85,
            neutral_excursion_ceiling=0.85,
            positive_pretrend_floor=1.1,
            positive_excursion_floor=1.1,
        )

        self.assertFalse(summary['fallback_applied'])
        self.assertEqual(summary['rows_before'], 5)
        self.assertEqual(summary['rows_after'], 4)
        self.assertEqual(summary['positive_candidates_before'], 3)
        self.assertEqual(summary['positive_candidates_after'], 2)
        self.assertEqual(list(y_filtered), [0, 0, 1, 1])
        self.assertEqual(len(X_filtered), 4)
        self.assertEqual(len(context_filtered), 4)

    def test_stage1_setup_target_can_demote_weak_reversal_rows(self):
        y_class = [1, 0, 2, 2]
        target_context = [
            [0.2, 0.3, 0.2],
            [1.5, 0.7, 1.2],
            [1.6, 1.4, 0.5],
            [1.5, 0.8, 1.2],
        ]

        labels, summary = _build_stage1_setup_targets(
            y_class,
            target_context,
            neutral_class_index=1,
            pretrend_threshold=1.2,
            reversal_threshold=1.0,
            positive_pretrend_floor=1.15,
            positive_excursion_floor=1.1,
            dominance_floor=2.0,
            margin_floor=0.0,
        )

        self.assertFalse(summary['fallback_applied'])
        self.assertEqual(summary['positive_rows_before'], 3)
        self.assertEqual(summary['positive_rows_after'], 1)
        self.assertEqual(list(labels), [0, 0, 1, 0])


if __name__ == '__main__':
    unittest.main()

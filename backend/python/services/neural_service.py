import json
import multiprocessing
import os
import pickle
import time
import gc
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone

try:
    from ..app_state import state
    from ..neural.registry import get_neural_network, list_neural_networks
    from ..neural.runners import get_neural_runner
    from .market_data_service import wait_for_market_data
    from .neural_store import (
        create_neural_run,
        delete_best_neural_model,
        delete_neural_run,
        delete_neural_network_user_state,
        get_best_neural_model,
        get_network_storage_paths,
        get_neural_run,
        get_neural_network_alias,
        list_neural_runs,
        promote_neural_model_to_best,
        remove_model_artifact,
        reset_neural_network_history,
        sanitize_json_value,
        set_neural_network_alias,
        update_neural_run,
        write_model_artifact_metadata,
    )
except ImportError:
    from app_state import state
    from neural.registry import get_neural_network, list_neural_networks
    from neural.runners import get_neural_runner
    from services.market_data_service import wait_for_market_data
    from services.neural_store import (
        create_neural_run,
        delete_best_neural_model,
        delete_neural_run,
        delete_neural_network_user_state,
        get_best_neural_model,
        get_network_storage_paths,
        get_neural_run,
        get_neural_network_alias,
        list_neural_runs,
        promote_neural_model_to_best,
        remove_model_artifact,
        reset_neural_network_history,
        sanitize_json_value,
        set_neural_network_alias,
        update_neural_run,
        write_model_artifact_metadata,
    )


NEURAL_RUNTIME_HEARTBEAT_INTERVAL_SECONDS = 2.5
NEURAL_RUNTIME_RECEIVING_WINDOW_SECONDS = 10.0
NEURAL_RUNTIME_WAITING_WINDOW_SECONDS = 20.0
NEURAL_RUNTIME_STALE_WINDOW_SECONDS = 45.0
NEURAL_RUNTIME_CANCELLED_ZOMBIE_SANITIZE_SECONDS = 60.0
NEURAL_RUNTIME_STARTUP_GRACE_SECONDS = 30.0


def build_neural_runtime_payload():
    _refresh_registered_neural_jobs()
    active_jobs = sanitize_json_value(dict(state.neural.active_jobs))
    active_counts = {}
    for payload in (active_jobs or {}).values():
        status = str((payload or {}).get('status') or 'unknown').strip().lower() or 'unknown'
        active_counts[status] = int(active_counts.get(status, 0) or 0) + 1

    return {
        'active_jobs': active_jobs,
        'active_counts': active_counts,
        'last_run_at': sanitize_json_value(state.neural.last_run_at),
        'last_error': state.neural.last_error,
        'recent_events': sanitize_json_value(list(state.neural.recent_events or [])),
    }


def _record_neural_event(kind: str, network_id: str | None = None, run_id: str | None = None, **extra):
    state.neural.recent_events = [
        {
            'kind': str(kind or 'event'),
            'network_id': str(network_id or '').strip() or None,
            'run_id': str(run_id or '').strip() or None,
            'at': time.time(),
            **sanitize_json_value(dict(extra or {})),
        },
        *list(state.neural.recent_events or []),
    ][:20]


def _job_key(user_id: str, network_id: str):
    return json.dumps([str(user_id or ''), str(network_id or '')], ensure_ascii=True, separators=(',', ':'))


def _parse_job_key(raw_key: str):
    try:
        payload = json.loads(str(raw_key or ''))
    except Exception:
        payload = None

    if isinstance(payload, list) and len(payload) == 2:
        return str(payload[0] or ''), str(payload[1] or '')

    legacy_key = str(raw_key or '')
    user_id, _, network_id = legacy_key.partition(':')
    return user_id, network_id


class NeuralJobCancelledError(Exception):
    """Raised when a neural job is cancelled by the user."""


def _network_defaults(network_id: str):
    network = get_neural_network(network_id)
    if not network:
        raise ValueError(f'Unknown neural network: {network_id}')
    return dict(network.get('defaults') or {})


def _network_parameter_schema(network_id: str):
    network = get_neural_network(network_id)
    if not network:
        raise ValueError(f'Unknown neural network: {network_id}')
    return list(network.get('parameter_schema') or [])


def _network_normalization_target_ids(network_id: str):
    network = get_neural_network(network_id)
    if not network:
        raise ValueError(f'Unknown neural network: {network_id}')
    return [
        str(target.get('id') or '').strip()
        for target in (network.get('normalization_targets') or [])
        if str(target.get('id') or '').strip()
    ]


def _max_bars_for_network(network_id: str):
    network = get_neural_network(network_id)
    family = str((network or {}).get('family') or '').strip().lower()
    if family == 'supervised_learning':
        return 2_000_000
    if family == 'reinforcement_learning':
        return 500_000
    return 2_000_000


def _coerce_config_value(field: dict, raw_value, default_value):
    field_type = str(field.get('type') or 'string').strip().lower()

    if raw_value is None or raw_value == '':
        raw_value = default_value

    if field_type == 'boolean':
        return bool(raw_value)

    if field_type == 'number':
        numeric = float(raw_value)
        min_value = field.get('min')
        max_value = field.get('max')
        if min_value is not None:
            numeric = max(float(min_value), numeric)
        if max_value is not None:
            numeric = min(float(max_value), numeric)

        step = field.get('step')
        if step != 'any':
            try:
                if float(step).is_integer() and numeric.is_integer():
                    return int(numeric)
            except (TypeError, ValueError, AttributeError):
                pass

        return int(numeric) if numeric.is_integer() else numeric

    return str(raw_value or '').strip()


def _normalize_network_config(network_id: str, payload: dict | None):
    defaults = _network_defaults(network_id)
    raw = dict(payload or {})
    config = {}

    for field in _network_parameter_schema(network_id):
        key = field.get('key')
        if not key:
            continue
        config[key] = _coerce_config_value(field, raw.get(key), defaults.get(key))

    if 'symbol' in config:
        config['symbol'] = str(config['symbol']).strip().upper()
    if 'timeframe' in config:
        config['timeframe'] = str(config['timeframe']).strip().upper()
    config['networkId'] = network_id

    config['algorithm'] = str(raw.get('algorithm', defaults.get('algorithm', 'PPO'))).strip().upper() or 'PPO'
    config['bars'] = max(100, int(config.get('bars', defaults.get('bars', 5000))))
    max_bars = _max_bars_for_network(network_id)
    if config['bars'] > max_bars:
        raise ValueError(
            f'Bars={config["bars"]} is too large for {network_id}. '
            f'Maximum allowed is {max_bars}.'
        )
    config['totalTimesteps'] = max(1000, int(config.get('totalTimesteps', defaults.get('totalTimesteps', 100000))))
    config['observationWindow'] = max(1, int(config.get('observationWindow', defaults.get('observationWindow', 1))))
    config['testEpisodes'] = max(1, int(config.get('testEpisodes', defaults.get('testEpisodes', 1))))
    config['validationSplit'] = float(config.get('validationSplit', defaults.get('validationSplit', 0.15)))
    config['testSplit'] = float(config.get('testSplit', defaults.get('testSplit', 0.15)))

    total_holdout = config['validationSplit'] + config['testSplit']
    if total_holdout >= 0.8:
        raise ValueError('Validation split + test split must leave at least 20% for training.')

    architecture_builder_networks = {
        'temporal_cnn_indicator_fusion_v1',
        'neural_market_regime_cnn_v1',
        'ema_low_adx_setup_quality_cnn_v1',
        'ema_low_adx_setup_quality_cnn_v2',
        'ema_low_adx_setup_quality_cnn_v3',
        'ema_low_adx_setup_quality_cnn_v4',
        'ema_low_adx_setup_quality_cnn_v5',
        'ema_low_adx_setup_quality_cnn_v6',
        'ema_low_adx_setup_quality_cnn_v7',
        'micro_cost_edge_cnn_v1',
        'micro_cost_edge_cnn_v2',
        'micro_cost_edge_cnn_v3',
        'micro_cost_edge_cnn_v4',
        'micro_cost_edge_cnn_v5',
        'candle_reversal_cnn_v1',
        'candle_reversal_cnn_v2',
        'candle_reversal_cnn_v3',
        'candle_reversal_cnn_v4',
        'candle_reversal_cnn_v5',
        'candle_reversal_cnn_v6',
        'candle_reversal_cnn_v7',
        'candle_reversal_cnn_v7_1',
        'candle_reversal_cnn_v8',
        'candle_reversal_cnn_v9',
        'candle_reversal_cnn_v10',
        'candle_reversal_cnn_v10_1',
        'candle_reversal_cnn_v11',
        'candle_reversal_cnn_v11_scores_only',
        'candle_reversal_cnn_v12_scores_only',
        'candle_reversal_setup_quality_cnn_v1',
    }

    if network_id in architecture_builder_networks:
        available_normalization_columns = _network_normalization_target_ids(network_id)
        normalization_columns = raw.get('normalizationColumns')
        if isinstance(normalization_columns, list):
            config['normalizationColumns'] = [
                str(item).strip()
                for item in normalization_columns
                if str(item).strip() in available_normalization_columns
            ]
        else:
            normalization_mode = str(
                raw.get(
                    'normalizationMode',
                    raw.get('normalizeVolume', 'volume' if defaults.get('normalizationColumns') else 'none'),
                ) or 'volume'
            ).strip().lower()
            if normalization_mode in ('true', '1', 'yes'):
                normalization_mode = 'volume'
            elif normalization_mode in ('false', '0', 'no'):
                normalization_mode = 'none'

            if normalization_mode == 'all_inputs':
                config['normalizationColumns'] = list(available_normalization_columns)
            elif normalization_mode == 'volume':
                config['normalizationColumns'] = ['ff_volume'] if 'ff_volume' in available_normalization_columns else []
            else:
                config['normalizationColumns'] = []

        config['normalizeVolume'] = 'ff_volume' in config['normalizationColumns']
        config['observationWindow'] = max(1, int(config.get('observationWindow', defaults.get('observationWindow', 1))))
        config['convFilters'] = max(4, int(config.get('convFilters', defaults.get('convFilters', 16))))
        config['kernelSize'] = max(2, int(config.get('kernelSize', defaults.get('kernelSize', 3))))
        if config['kernelSize'] > config['observationWindow']:
            raise ValueError('Kernel size cannot be larger than the observation window.')

        if network_id == 'neural_market_regime_cnn_v1':
            config['targetMode'] = 'future_regime_classification'
            config['targetRegimeCompressionThreshold'] = max(0.0, float(config.get('targetRegimeCompressionThreshold', defaults.get('targetRegimeCompressionThreshold', 0.9))))
            config['targetRegimeVolatilityThreshold'] = max(0.0, float(config.get('targetRegimeVolatilityThreshold', defaults.get('targetRegimeVolatilityThreshold', 2.2))))
            config['targetRegimeTrendEfficiencyThreshold'] = max(0.0, float(config.get('targetRegimeTrendEfficiencyThreshold', defaults.get('targetRegimeTrendEfficiencyThreshold', 0.55))))
            config['targetRegimeDirectionalMoveThreshold'] = max(0.0, float(config.get('targetRegimeDirectionalMoveThreshold', defaults.get('targetRegimeDirectionalMoveThreshold', 0.35))))
            config['targetRegimeDirectionalDominanceThreshold'] = max(0.0, float(config.get('targetRegimeDirectionalDominanceThreshold', defaults.get('targetRegimeDirectionalDominanceThreshold', 0.6))))
        elif network_id in {'ema_low_adx_setup_quality_cnn_v1', 'ema_low_adx_setup_quality_cnn_v2', 'ema_low_adx_setup_quality_cnn_v3', 'ema_low_adx_setup_quality_cnn_v4', 'ema_low_adx_setup_quality_cnn_v5', 'ema_low_adx_setup_quality_cnn_v6', 'ema_low_adx_setup_quality_cnn_v7'}:
            config['targetMode'] = (
                'ema_low_adx_setup_quality_tp_sl_good_vs_rest_classification'
                if network_id == 'ema_low_adx_setup_quality_cnn_v7'
                else
                'ema_low_adx_setup_quality_good_vs_rest_classification'
                if network_id in {'ema_low_adx_setup_quality_cnn_v4', 'ema_low_adx_setup_quality_cnn_v5', 'ema_low_adx_setup_quality_cnn_v6'}
                else
                'ema_low_adx_setup_quality_first_touch_binary_classification'
                if network_id == 'ema_low_adx_setup_quality_cnn_v3'
                else
                'ema_low_adx_setup_quality_binary_classification'
                if network_id == 'ema_low_adx_setup_quality_cnn_v2'
                else 'ema_low_adx_setup_quality_classification'
            )
            config['setupAdxCeiling'] = max(0.0, float(config.get('setupAdxCeiling', defaults.get('setupAdxCeiling', 28.0))))
            config['setupPrevRsiCeiling'] = max(0.0, float(config.get('setupPrevRsiCeiling', defaults.get('setupPrevRsiCeiling', 38.0))))
            config['setupCurrentRsiFloor'] = max(0.0, float(config.get('setupCurrentRsiFloor', defaults.get('setupCurrentRsiFloor', 38.0))))
            config['setupCurrentRsiCeiling'] = max(0.0, float(config.get('setupCurrentRsiCeiling', defaults.get('setupCurrentRsiCeiling', 50.0))))
            config['setupTouchSlackAtr'] = max(0.0, float(config.get('setupTouchSlackAtr', defaults.get('setupTouchSlackAtr', 0.06))))
            config['setupPrevBandSlackAtr'] = max(0.0, float(config.get('setupPrevBandSlackAtr', defaults.get('setupPrevBandSlackAtr', 0.08))))
            config['setupBounceFraction'] = max(0.0, float(config.get('setupBounceFraction', defaults.get('setupBounceFraction', 0.02))))
            config['setupDiSpreadFloor'] = max(0.0, float(config.get('setupDiSpreadFloor', defaults.get('setupDiSpreadFloor', 0.0))))
            config['setupCandidateMinGapBars'] = max(0, int(config.get('setupCandidateMinGapBars', defaults.get('setupCandidateMinGapBars', 0))))
            if network_id == 'ema_low_adx_setup_quality_cnn_v7':
                config['targetReversalTakeProfitAtr'] = max(0.05, float(config.get('targetReversalTakeProfitAtr', defaults.get('targetReversalTakeProfitAtr', 1.0))))
                config['targetReversalStopLossAtr'] = max(0.05, float(config.get('targetReversalStopLossAtr', defaults.get('targetReversalStopLossAtr', 1.0))))
            config['targetQualityGoodExcursionThreshold'] = max(0.0, float(config.get('targetQualityGoodExcursionThreshold', defaults.get('targetQualityGoodExcursionThreshold', 0.82))))
            config['targetQualityBadExcursionThreshold'] = max(0.0, float(config.get('targetQualityBadExcursionThreshold', defaults.get('targetQualityBadExcursionThreshold', 0.52))))
            config['targetQualityGoodDominanceRatio'] = max(1.0, float(config.get('targetQualityGoodDominanceRatio', defaults.get('targetQualityGoodDominanceRatio', 1.1))))
            config['targetQualityBadDominanceRatio'] = max(1.0, float(config.get('targetQualityBadDominanceRatio', defaults.get('targetQualityBadDominanceRatio', 1.1))))
            config['targetQualityGoodCounterExcursionCeiling'] = max(0.0, float(config.get('targetQualityGoodCounterExcursionCeiling', defaults.get('targetQualityGoodCounterExcursionCeiling', 0.45))))
            config['targetQualityBadCounterExcursionCeiling'] = max(0.0, float(config.get('targetQualityBadCounterExcursionCeiling', defaults.get('targetQualityBadCounterExcursionCeiling', 0.45))))
            config['classWeightMode'] = str(config.get('classWeightMode', defaults.get('classWeightMode', 'none')) or 'none').strip().lower() or 'none'
            config['classWeightExponent'] = max(0.0, float(config.get('classWeightExponent', defaults.get('classWeightExponent', 1.0))))
            config['neutralRetention'] = max(0.05, min(1.0, float(config.get('neutralRetention', defaults.get('neutralRetention', 1.0)))))
        elif network_id in {'micro_cost_edge_cnn_v1', 'micro_cost_edge_cnn_v2', 'micro_cost_edge_cnn_v3', 'micro_cost_edge_cnn_v4', 'micro_cost_edge_cnn_v5'}:
            config['targetMode'] = (
                'micro_cost_edge_hierarchical_classification'
                if network_id in {'micro_cost_edge_cnn_v3', 'micro_cost_edge_cnn_v4'}
                else
                'micro_cost_edge_side_classification'
                if network_id in {'micro_cost_edge_cnn_v2', 'micro_cost_edge_cnn_v5'}
                else 'micro_cost_edge_classification'
            )
            config['pipSize'] = max(1e-8, float(config.get('pipSize', defaults.get('pipSize', 0.0001))))
            config['roundTripCostPips'] = max(0.0, float(config.get('roundTripCostPips', defaults.get('roundTripCostPips', 1.6))))
            config['targetCostEdgeMultiple'] = max(1.0, float(config.get('targetCostEdgeMultiple', defaults.get('targetCostEdgeMultiple', 1.75))))
            config['classWeightMode'] = str(config.get('classWeightMode', defaults.get('classWeightMode', 'none')) or 'none').strip().lower() or 'none'
            config['classWeightExponent'] = max(0.0, float(config.get('classWeightExponent', defaults.get('classWeightExponent', 1.0))))
            config['neutralRetention'] = max(0.05, min(1.0, float(config.get('neutralRetention', defaults.get('neutralRetention', 1.0)))))
        elif network_id == 'candle_reversal_setup_quality_cnn_v1':
            config['targetMode'] = 'candle_reversal_setup_quality_good_vs_rest_classification'
            config['pretrendLookback'] = max(2, int(config.get('pretrendLookback', defaults.get('pretrendLookback', 6))))
            config['pretrendThreshold'] = max(0.0, float(config.get('pretrendThreshold', defaults.get('pretrendThreshold', 1.2))))
            config['reversalTakeProfitAtr'] = max(0.05, float(config.get('reversalTakeProfitAtr', defaults.get('reversalTakeProfitAtr', 0.75))))
            config['reversalStopLossAtr'] = max(0.05, float(config.get('reversalStopLossAtr', defaults.get('reversalStopLossAtr', 1.0))))
            config['classWeightMode'] = str(config.get('classWeightMode', defaults.get('classWeightMode', 'none')) or 'none').strip().lower() or 'none'
            config['classWeightExponent'] = max(0.0, float(config.get('classWeightExponent', defaults.get('classWeightExponent', 1.0))))
        elif network_id in {'candle_reversal_cnn_v1', 'candle_reversal_cnn_v2', 'candle_reversal_cnn_v3', 'candle_reversal_cnn_v4', 'candle_reversal_cnn_v5', 'candle_reversal_cnn_v6', 'candle_reversal_cnn_v7', 'candle_reversal_cnn_v7_1', 'candle_reversal_cnn_v8', 'candle_reversal_cnn_v9', 'candle_reversal_cnn_v10', 'candle_reversal_cnn_v10_1', 'candle_reversal_cnn_v11', 'candle_reversal_cnn_v11_scores_only', 'candle_reversal_cnn_v12_scores_only'}:
            config['targetMode'] = (
                'future_candle_reversal_tp_sl_classification'
                if network_id == 'candle_reversal_cnn_v12_scores_only'
                else 'future_candle_reversal_classification'
            )
            config['pretrendLookback'] = max(2, int(config.get('pretrendLookback', defaults.get('pretrendLookback', 6))))
            config['pretrendThreshold'] = max(0.0, float(config.get('pretrendThreshold', defaults.get('pretrendThreshold', 1.2))))
            if network_id == 'candle_reversal_cnn_v12_scores_only':
                config['reversalTakeProfitAtr'] = max(0.05, float(config.get('reversalTakeProfitAtr', defaults.get('reversalTakeProfitAtr', 0.75))))
                config['reversalStopLossAtr'] = max(0.05, float(config.get('reversalStopLossAtr', defaults.get('reversalStopLossAtr', 1.0))))
            else:
                config['reversalThreshold'] = max(0.0, float(config.get('reversalThreshold', defaults.get('reversalThreshold', 1.0))))
                config['dominanceRatio'] = max(1.0, float(config.get('dominanceRatio', defaults.get('dominanceRatio', 1.35))))
            config['classWeightMode'] = str(config.get('classWeightMode', defaults.get('classWeightMode', 'none')) or 'none').strip().lower() or 'none'
            config['classWeightExponent'] = max(0.0, float(config.get('classWeightExponent', defaults.get('classWeightExponent', 1.0))))
            config['neutralRetention'] = max(0.05, min(1.0, float(config.get('neutralRetention', defaults.get('neutralRetention', 1.0)))))
            config['stage1NeutralPretrendCeiling'] = max(0.0, min(1.0, float(config.get('stage1NeutralPretrendCeiling', defaults.get('stage1NeutralPretrendCeiling', 0.85)))))
            config['stage1NeutralExcursionCeiling'] = max(0.0, min(1.0, float(config.get('stage1NeutralExcursionCeiling', defaults.get('stage1NeutralExcursionCeiling', 0.85)))))
            config['stage1PositivePretrendFloor'] = max(0.0, min(2.0, float(config.get('stage1PositivePretrendFloor', defaults.get('stage1PositivePretrendFloor', 0.0)))))
            config['stage1PositiveExcursionFloor'] = max(0.0, min(2.0, float(config.get('stage1PositiveExcursionFloor', defaults.get('stage1PositiveExcursionFloor', 0.0)))))
            config['targetCleanNeutralPretrendCeiling'] = max(0.0, min(1.0, float(config.get('targetCleanNeutralPretrendCeiling', defaults.get('targetCleanNeutralPretrendCeiling', 0.0)))))
            config['targetCleanNeutralExcursionCeiling'] = max(0.0, min(1.0, float(config.get('targetCleanNeutralExcursionCeiling', defaults.get('targetCleanNeutralExcursionCeiling', 0.0)))))
            config['targetCleanPositivePretrendFloor'] = max(0.0, min(2.0, float(config.get('targetCleanPositivePretrendFloor', defaults.get('targetCleanPositivePretrendFloor', 0.0)))))
            config['targetCleanPositiveExcursionFloor'] = max(0.0, min(2.0, float(config.get('targetCleanPositiveExcursionFloor', defaults.get('targetCleanPositiveExcursionFloor', 0.0)))))
            config['stage1SetupPretrendFloor'] = max(0.0, min(2.0, float(config.get('stage1SetupPretrendFloor', defaults.get('stage1SetupPretrendFloor', 1.0)))))
            config['stage1SetupExcursionFloor'] = max(0.0, min(2.0, float(config.get('stage1SetupExcursionFloor', defaults.get('stage1SetupExcursionFloor', 1.0)))))
            config['stage1SetupDominanceFloor'] = max(1.0, min(5.0, float(config.get('stage1SetupDominanceFloor', defaults.get('stage1SetupDominanceFloor', 1.35)))))
            config['stage1SetupMarginFloor'] = max(0.0, min(5.0, float(config.get('stage1SetupMarginFloor', defaults.get('stage1SetupMarginFloor', 0.0)))))
            config['directionalHeadRestRetention'] = max(0.05, min(1.0, float(config.get('directionalHeadRestRetention', defaults.get('directionalHeadRestRetention', 1.0)))))
        else:
            config['targetMode'] = 'std_threshold_signal'
            config['targetStdWindow'] = max(2, int(config.get('targetStdWindow', defaults.get('targetStdWindow', 20))))
            config['targetStdThreshold'] = max(0.0, float(config.get('targetStdThreshold', defaults.get('targetStdThreshold', 1.0))))

        raw_layers = raw.get('hiddenLayers')
        if isinstance(raw_layers, list) and raw_layers:
            normalized_layers = []
            for index, layer in enumerate(raw_layers):
                if not isinstance(layer, dict):
                    continue
                normalized_layers.append({
                    'id': str(layer.get('id') or f'layer_{index + 1}'),
                    'size': max(4, int(layer.get('size', 32))),
                    'activation': str(layer.get('activation') or 'tanh').strip().lower() or 'tanh',
                    'dropout': max(0.0, min(0.9, float(layer.get('dropout', 0.0) or 0.0))),
                })
            config['hiddenLayers'] = normalized_layers or [{'id': 'layer_1', 'size': 32, 'activation': 'tanh', 'dropout': 0.0}]
        else:
            default_layers = defaults.get('hiddenLayers')
            if isinstance(default_layers, list) and default_layers:
                normalized_layers = []
                for index, layer in enumerate(default_layers):
                    if not isinstance(layer, dict):
                        continue
                    normalized_layers.append({
                        'id': str(layer.get('id') or f'layer_{index + 1}'),
                        'size': max(4, int(layer.get('size', 32))),
                        'activation': str(layer.get('activation') or 'tanh').strip().lower() or 'tanh',
                        'dropout': max(0.0, min(0.9, float(layer.get('dropout', 0.0) or 0.0))),
                    })
                config['hiddenLayers'] = normalized_layers or [{'id': 'layer_1', 'size': 32, 'activation': 'tanh', 'dropout': 0.0}]

    return config


def normalize_neural_config_payload(network_id: str, payload: dict | None):
    return _normalize_network_config(network_id, payload)


def list_neural_network_summaries(user_id: str):
    _refresh_registered_neural_jobs()
    payload = []

    for network in list_neural_networks():
        alias_payload = get_neural_network_alias(user_id, network['id'])
        if alias_payload and alias_payload.get('is_deleted'):
            continue
        best_model = get_best_neural_model(user_id, network['id'])
        recent_runs = list_neural_runs(user_id, network['id'], limit=5)
        recent_runs = _reconcile_recent_runs_from_runtime(user_id, network['id'], recent_runs)
        active_job = _recover_active_job_from_runtime(user_id, network['id'], recent_runs=recent_runs)
        payload.append({
            **network,
            'alias': alias_payload['alias'] if alias_payload else '',
            'display_label': (alias_payload['alias'] if alias_payload else '') or network.get('label') or network.get('id'),
            'is_favorite': bool(alias_payload.get('is_favorite')) if alias_payload else False,
            'best_model': sanitize_json_value(best_model),
            'active_job': active_job,
        })

    return payload


def get_neural_network_summary(user_id: str, network_id: str, limit_runs: int = 10):
    _refresh_registered_neural_jobs()
    network = get_neural_network(network_id)
    if not network:
        raise ValueError(f'Unknown neural network: {network_id}')

    alias_payload = get_neural_network_alias(user_id, network_id)
    if alias_payload and alias_payload.get('is_deleted'):
        raise ValueError(f'Neural network {network_id} was deleted from this workspace.')
    runs = list_neural_runs(user_id, network_id, limit=limit_runs)
    runs = _reconcile_recent_runs_from_runtime(user_id, network_id, runs)
    active_job = _recover_active_job_from_runtime(user_id, network_id, recent_runs=runs)

    return {
        **network,
        'alias': alias_payload['alias'] if alias_payload else '',
        'display_label': (alias_payload['alias'] if alias_payload else '') or network.get('label') or network.get('id'),
        'is_favorite': bool(alias_payload.get('is_favorite')) if alias_payload else False,
        'best_model': sanitize_json_value(get_best_neural_model(user_id, network_id)),
        'active_job': active_job,
        'runs': sanitize_json_value(runs),
    }


def update_neural_network_alias(user_id: str, network_id: str, alias: str | None = None, is_favorite: bool | None = None):
    network = get_neural_network(network_id)
    if not network:
        raise ValueError(f'Unknown neural network: {network_id}')
    set_neural_network_alias(
        user_id=user_id,
        network_id=network_id,
        alias=alias,
        is_favorite=is_favorite,
    )
    return get_neural_network_summary(user_id, network_id)


def clear_neural_network_history(user_id: str, network_id: str):
    _refresh_registered_neural_jobs()
    if state.neural.active_jobs.get(_job_key(user_id, network_id)) or _recover_active_job_from_runtime(user_id, network_id):
        raise ValueError(f'Cannot reset {network_id} history while a neural job is active.')
    reset_neural_network_history(user_id=user_id, network_id=network_id)
    return get_neural_network_summary(user_id, network_id)


def delete_neural_network(user_id: str, network_id: str):
    network = get_neural_network(network_id)
    if not network:
        raise ValueError(f'Unknown neural network: {network_id}')

    _refresh_registered_neural_jobs()
    if state.neural.active_jobs.get(_job_key(user_id, network_id)) or _recover_active_job_from_runtime(user_id, network_id):
        raise ValueError(f'Cannot delete {network_id} while a neural job is active.')

    delete_neural_network_user_state(user_id=user_id, network_id=network_id)
    return list_neural_network_summaries(user_id)


def delete_neural_run_artifact(user_id: str, network_id: str, run_id: str):
    _refresh_registered_neural_jobs()
    run = get_neural_run(run_id)
    if not run or run['user_id'] != user_id or run['network_id'] != network_id:
        raise ValueError(f'Neural run {run_id} was not found.')
    if run.get('status') in {'queued', 'running'}:
        raise ValueError('Cannot delete a model file while the run is still active.')

    artifact_path = run.get('artifact_path')
    if not artifact_path:
        return get_neural_network_summary(user_id, network_id)

    best_model = get_best_neural_model(user_id, network_id)
    remove_model_artifact(artifact_path)

    updates = {'artifact_path': None}
    if best_model and str(best_model.get('model_path') or '') == str(artifact_path):
        delete_best_neural_model(user_id, network_id)
        updates['promoted_to_best'] = 0

    update_neural_run(run_id, **updates)
    return get_neural_network_summary(user_id, network_id)


def delete_neural_run_record(user_id: str, network_id: str, run_id: str):
    _refresh_registered_neural_jobs()
    run = get_neural_run(run_id)
    if not run or run['user_id'] != user_id or run['network_id'] != network_id:
        raise ValueError(f'Neural run {run_id} was not found.')
    if run.get('status') in {'queued', 'running'}:
        raise ValueError('Cannot delete a run while it is still active.')

    best_model = get_best_neural_model(user_id, network_id)
    artifact_path = run.get('artifact_path')
    if artifact_path:
        remove_model_artifact(artifact_path)

    if best_model and (
        str(best_model.get('run_id') or '') == str(run_id)
        or str(best_model.get('model_path') or '') == str(artifact_path or '')
    ):
        delete_best_neural_model(user_id, network_id)

    deleted_run = delete_neural_run(user_id=user_id, network_id=network_id, run_id=run_id)
    if not deleted_run:
        raise ValueError(f'Neural run {run_id} was not found.')

    return get_neural_network_summary(user_id, network_id)


def _set_active_job(user_id: str, network_id: str, payload: dict | None):
    key = _job_key(user_id, network_id)
    if payload is None:
        state.neural.active_jobs.pop(key, None)
        worker_ref = state.neural.job_threads.get(key)
        if worker_ref is None or not getattr(worker_ref, 'is_alive', lambda: False)():
            state.neural.job_threads.pop(key, None)
        return

    state.neural.active_jobs[key] = dict(payload)


def _set_job_thread(user_id: str, network_id: str, thread_ref):
    key = _job_key(user_id, network_id)
    if thread_ref is None:
        state.neural.job_threads.pop(key, None)
        return
    state.neural.job_threads[key] = thread_ref


def _recover_active_job_from_runtime(user_id: str, network_id: str, recent_runs: list[dict] | None = None):
    key = _job_key(user_id, network_id)
    active_job = state.neural.active_jobs.get(key)
    if active_job:
        return sanitize_json_value(active_job)

    runs = recent_runs if recent_runs is not None else list_neural_runs(user_id, network_id, limit=10)
    for run in runs or []:
        run_status = str(run.get('status') or '').strip().lower()
        if run_status not in {'queued', 'running'}:
            continue

        run_id = str(run.get('id') or '').strip()
        if not run_id:
            continue

        paths = _build_job_runtime_paths(user_id, network_id, run_id)
        runtime_payload = _read_runtime_state(paths['runtime_path'])
        runtime_status = str((runtime_payload or {}).get('status') or '').strip().lower()
        if not runtime_payload or runtime_status not in {'queued', 'running'}:
            continue

        recovered_job = {
            **runtime_payload,
            **_derive_runtime_feed_state(runtime_payload, worker_alive=False),
        }
        state.neural.active_jobs[key] = recovered_job
        return sanitize_json_value(recovered_job)

    return None


def _reconcile_recent_runs_from_runtime(user_id: str, network_id: str, runs: list[dict] | None):
    reconciled = False

    for run in runs or []:
        run_id = str(run.get('id') or '').strip()
        if not run_id:
            continue

        runtime_payload = _read_runtime_state(_build_job_runtime_paths(user_id, network_id, run_id)['runtime_path'])
        runtime_status = str((runtime_payload or {}).get('status') or '').strip().lower()
        run_status = str(run.get('status') or '').strip().lower()
        if runtime_status not in {'completed', 'failed', 'cancelled'} or runtime_status == run_status:
            continue

        update_payload = {
            'status': runtime_status,
            'ended_at': runtime_payload.get('finished_at') or run.get('ended_at') or time.time(),
            'duration_seconds': runtime_payload.get('elapsed_seconds') or run.get('duration_seconds'),
        }
        if runtime_status == 'completed':
            update_payload['error'] = None
        else:
            update_payload['error'] = str(runtime_payload.get('error') or run.get('error') or '').strip() or None
        update_neural_run(run_id, **update_payload)
        reconciled = True

    if reconciled:
        return list_neural_runs(user_id, network_id, limit=max(1, len(runs or [])))
    return runs or []


def _build_job_runtime_paths(user_id: str, network_id: str, run_id: str):
    model_base_path = _build_run_model_base_path(user_id, network_id, run_id)
    run_dir = Path(model_base_path).parent
    return {
        'run_dir': run_dir,
        'runtime_path': run_dir / 'runtime.json',
        'cancel_path': run_dir / 'cancel.flag',
        'market_snapshot_path': run_dir / 'market_snapshot.pkl',
    }


def _prepare_neural_worker_process():
    for env_key in (
        'OMP_NUM_THREADS',
        'OPENBLAS_NUM_THREADS',
        'MKL_NUM_THREADS',
        'NUMEXPR_NUM_THREADS',
        'VECLIB_MAXIMUM_THREADS',
        'BLIS_NUM_THREADS',
    ):
        os.environ[env_key] = '1'

    try:
        os.nice(15)
    except Exception:
        pass

    try:
        cpu_count = int(os.cpu_count() or 1)
        if cpu_count > 1 and hasattr(os, 'sched_setaffinity'):
            available = sorted(os.sched_getaffinity(0))
            if len(available) > 1:
                worker_cpus = available[:-1]
                if worker_cpus:
                    os.sched_setaffinity(0, set(worker_cpus))
    except Exception:
        pass


def _read_runtime_state(runtime_path: Path):
    if not runtime_path.exists():
        return {}
    try:
        return json.loads(runtime_path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _write_runtime_state(runtime_path: Path, payload: dict):
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = runtime_path.parent / f'{runtime_path.name}.{uuid.uuid4().hex}.tmp'
    temp_path.write_text(
        json.dumps(sanitize_json_value(payload), ensure_ascii=True, allow_nan=False),
        encoding='utf-8',
    )
    temp_path.replace(runtime_path)


def _write_market_snapshot(snapshot_path: Path, payload: dict):
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = snapshot_path.parent / f'{snapshot_path.name}.{uuid.uuid4().hex}.tmp'
    with temp_path.open('wb') as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    temp_path.replace(snapshot_path)


def _read_market_snapshot(snapshot_path: str | Path | None):
    safe_path = Path(snapshot_path) if snapshot_path else None
    if safe_path is None or not safe_path.exists():
        return None
    try:
        with safe_path.open('rb') as handle:
            return pickle.load(handle)
    except Exception:
        return None


def _touch_runtime_heartbeat(runtime_path: Path, **updates):
    payload = _read_runtime_state(runtime_path)
    payload['heartbeat_at'] = time.time()
    for key_name, key_value in (updates or {}).items():
        payload[key_name] = sanitize_json_value(key_value)
    _write_runtime_state(runtime_path, payload)
    return payload


def _update_runtime_state(runtime_path: Path, message: str | None = None, level: str = 'info', progress: float | None = None, **updates):
    payload = _read_runtime_state(runtime_path)
    logs = list(payload.get('logs') or [])
    safe_message = str(message or '').strip()
    append_log = bool(updates.pop('append_log', True))
    if safe_message:
        payload['last_message'] = safe_message
        if append_log:
            logs.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'message': safe_message,
                'level': str(level or 'info'),
            })
            payload['logs'] = logs[-200:]
    if progress is not None:
        payload['progress'] = max(0.0, min(1.0, float(progress)))
    for key_name, key_value in (updates or {}).items():
        payload[key_name] = sanitize_json_value(key_value)
    payload['updated_at'] = time.time()
    _write_runtime_state(runtime_path, payload)
    return payload


def _derive_runtime_feed_state(active_job: dict | None, worker_alive: bool):
    payload = dict(active_job or {})
    status = str(payload.get('status') or '').strip().lower()
    phase = str(payload.get('phase') or '').strip().lower()
    now = time.time()
    updated_at = payload.get('updated_at')
    heartbeat_at = payload.get('heartbeat_at')
    started_at = payload.get('started_at')
    update_age_seconds = max(0.0, now - float(updated_at)) if updated_at is not None else None
    heartbeat_age_seconds = max(0.0, now - float(heartbeat_at)) if heartbeat_at is not None else None
    runtime_age_seconds = max(0.0, now - float(started_at)) if started_at is not None else None

    feed_status = 'idle'
    feed_label = 'Idle'
    feed_detail = ''
    auto_sanitize_recommended = False

    if status in {'queued', 'running'}:
        in_startup_grace = (
            runtime_age_seconds is not None
            and runtime_age_seconds <= NEURAL_RUNTIME_STARTUP_GRACE_SECONDS
            and phase in {'queued', 'starting', 'preparing_snapshot', ''}
        )
        if in_startup_grace:
            feed_status = 'waiting'
            feed_label = 'Waiting for data'
            feed_detail = (
                'Neural runtime is still preparing its isolated data source. '
                'Steady worker updates have not started yet.'
            )
        elif update_age_seconds is not None and update_age_seconds <= NEURAL_RUNTIME_RECEIVING_WINDOW_SECONDS:
            feed_status = 'receiving'
            feed_label = 'Receiving data'
            feed_detail = 'Live updates are arriving from the neural worker.'
        elif heartbeat_age_seconds is not None and heartbeat_age_seconds <= NEURAL_RUNTIME_WAITING_WINDOW_SECONDS:
            feed_status = 'waiting'
            feed_label = 'Waiting for data'
            feed_detail = 'Worker heartbeat is healthy, but no new runtime update arrived yet.'
        else:
            feed_status = 'stale'
            feed_label = 'Feed stale'
            feed_detail = 'Worker updates look stale or stopped.'
            auto_sanitize_recommended = (
                not worker_alive
                or (
                    payload.get('cancel_requested')
                    and heartbeat_age_seconds is not None
                    and heartbeat_age_seconds >= NEURAL_RUNTIME_CANCELLED_ZOMBIE_SANITIZE_SECONDS
                )
            )
    elif status in {'completed', 'failed', 'cancelled'}:
        feed_status = 'finished'
        feed_label = 'Finished'
        feed_detail = 'Neural worker completed and the runtime is no longer streaming.'

    return {
        'data_feed_status': feed_status,
        'data_feed_label': feed_label,
        'data_feed_detail': feed_detail,
        'update_age_seconds': sanitize_json_value(update_age_seconds),
        'heartbeat_age_seconds': sanitize_json_value(heartbeat_age_seconds),
        'runtime_age_seconds': sanitize_json_value(runtime_age_seconds),
        'worker_alive': bool(worker_alive),
        'auto_sanitize_recommended': bool(auto_sanitize_recommended),
    }


def _start_runtime_heartbeat(runtime_path: Path, worker_pid: int):
    stop_event = threading.Event()

    def loop():
        while not stop_event.wait(NEURAL_RUNTIME_HEARTBEAT_INTERVAL_SECONDS):
            try:
                _touch_runtime_heartbeat(runtime_path, worker_pid=worker_pid)
            except Exception:
                pass

    thread = threading.Thread(target=loop, name=f'neural-heartbeat-{worker_pid}', daemon=True)
    thread.start()
    return stop_event, thread


def _is_cancel_requested_from_path(cancel_path: Path):
    return cancel_path.exists()


def _refresh_registered_neural_jobs():
    for key, active_job in list(state.neural.active_jobs.items()):
        key_user_id, key_network_id = _parse_job_key(key)
        run_id = str((active_job or {}).get('run_id') or '').strip()
        if not run_id:
            continue
        paths = _build_job_runtime_paths(key_user_id, key_network_id, run_id)
        runtime_payload = _read_runtime_state(paths['runtime_path'])
        if runtime_payload:
            state.neural.active_jobs[key] = {
                **dict(active_job or {}),
                **runtime_payload,
            }
            runtime_status = str(runtime_payload.get('status') or '').strip().lower()
            if runtime_status in {'completed', 'failed', 'cancelled'}:
                existing_run = get_neural_run(run_id)
                if existing_run and str(existing_run.get('status') or '').strip().lower() != runtime_status:
                    update_payload = {
                        'status': runtime_status,
                        'ended_at': runtime_payload.get('finished_at') or time.time(),
                        'duration_seconds': runtime_payload.get('elapsed_seconds'),
                    }
                    if runtime_status == 'completed':
                        update_payload['error'] = None
                    else:
                        update_payload['error'] = str(runtime_payload.get('error') or existing_run.get('error') or '').strip() or None
                    update_neural_run(run_id, **update_payload)

        worker_ref = state.neural.job_threads.get(key)
        worker_known = worker_ref is not None
        worker_alive = bool(worker_ref and getattr(worker_ref, 'is_alive', lambda: False)())
        current_job = dict(state.neural.active_jobs.get(key) or {})
        feed_state = _derive_runtime_feed_state(current_job, worker_alive)
        state.neural.active_jobs[key] = {
            **current_job,
            **feed_state,
        }
        current_status = str((state.neural.active_jobs.get(key) or {}).get('status') or '').strip().lower()

        heartbeat_age_seconds = (state.neural.active_jobs.get(key) or {}).get('heartbeat_age_seconds')
        update_age_seconds = (state.neural.active_jobs.get(key) or {}).get('update_age_seconds')
        cancel_requested = bool((state.neural.active_jobs.get(key) or {}).get('cancel_requested'))
        runtime_age_seconds = (state.neural.active_jobs.get(key) or {}).get('runtime_age_seconds')

        if worker_alive and cancel_requested:
            safe_heartbeat_age = float(heartbeat_age_seconds) if heartbeat_age_seconds is not None else None
            safe_update_age = float(update_age_seconds) if update_age_seconds is not None else None
            if (
                safe_heartbeat_age is not None
                and safe_heartbeat_age >= NEURAL_RUNTIME_CANCELLED_ZOMBIE_SANITIZE_SECONDS
                and (safe_update_age is None or safe_update_age >= NEURAL_RUNTIME_CANCELLED_ZOMBIE_SANITIZE_SECONDS)
            ):
                failure_message = 'Cancelled neural job stopped sending heartbeats and was auto-sanitized.'
                try:
                    worker_ref.terminate()
                    worker_ref.join(timeout=1.0)
                except Exception:
                    pass
                worker_alive = bool(worker_ref and getattr(worker_ref, 'is_alive', lambda: False)())
                if not worker_alive:
                    updated_run = update_neural_run(
                        run_id,
                        status='cancelled',
                        error=failure_message,
                        ended_at=time.time(),
                        duration_seconds=max(0.0, time.time() - float((state.neural.active_jobs.get(key) or {}).get('started_at') or time.time())),
                    )
                    _update_runtime_state(paths['runtime_path'], failure_message, level='warn', status='cancelled', finished_at=time.time(), error=failure_message)
                    if updated_run:
                        state.neural.last_error = failure_message
                    _record_neural_event(
                        'cancelled',
                        network_id=key_network_id,
                        run_id=run_id,
                        status='cancelled',
                        error=failure_message,
                    )
                    state.neural.active_jobs.pop(key, None)
                    state.neural.job_threads.pop(key, None)
                    continue

        safe_runtime_age = float(runtime_age_seconds) if runtime_age_seconds is not None else None
        if (
            worker_known
            and
            not worker_alive
            and current_status in {'queued', 'running'}
            and (safe_runtime_age is None or safe_runtime_age > NEURAL_RUNTIME_STARTUP_GRACE_SECONDS)
        ):
            failure_message = 'Neural worker exited before reporting progress.'
            updated_run = update_neural_run(
                run_id,
                status='failed',
                error=failure_message,
                ended_at=time.time(),
                duration_seconds=max(0.0, time.time() - float((state.neural.active_jobs.get(key) or {}).get('started_at') or time.time())),
            )
            state.neural.active_jobs[key] = {
                **dict(state.neural.active_jobs.get(key) or {}),
                'status': 'failed',
                'error': failure_message,
                'finished_at': time.time(),
                'logs': [
                    *list((state.neural.active_jobs.get(key) or {}).get('logs') or []),
                    {
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'message': failure_message,
                        'level': 'error',
                    },
                ][-200:],
            }
            state.neural.last_error = failure_message
            _record_neural_event(
                'failed',
                network_id=key_network_id,
                run_id=run_id,
                status='failed',
                error=failure_message,
            )
            state.neural.active_jobs.pop(key, None)
            state.neural.job_threads.pop(key, None)
            continue
        if not worker_alive and current_status in {'completed', 'failed', 'cancelled'}:
            _record_neural_event(
                current_status,
                network_id=key_network_id,
                run_id=run_id,
                status=current_status,
                progress=(state.neural.active_jobs.get(key) or {}).get('progress'),
            )
            state.neural.active_jobs.pop(key, None)
            state.neural.job_threads.pop(key, None)


def _update_active_job(user_id: str, network_id: str, **updates):
    key = _job_key(user_id, network_id)
    active_job = state.neural.active_jobs.get(key)
    if not active_job:
        return None

    active_job.update(updates)
    state.neural.active_jobs[key] = active_job
    return dict(active_job)


def _append_job_log(user_id: str, network_id: str, message: str | None, level: str = 'info', progress: float | None = None, **job_updates):
    key = _job_key(user_id, network_id)
    active_job = dict(state.neural.active_jobs.get(key) or {})
    safe_message = str(message or '').strip()
    logs = list(active_job.get('logs') or [])
    if safe_message:
        logs.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': safe_message,
            'level': str(level or 'info'),
        })
        active_job['logs'] = logs[-200:]
    if progress is not None:
        active_job['progress'] = max(0.0, min(1.0, float(progress)))
    for key_name, key_value in (job_updates or {}).items():
        active_job[key_name] = sanitize_json_value(key_value)
    active_job['updated_at'] = time.time()
    if safe_message:
        active_job['last_message'] = safe_message
    state.neural.active_jobs[key] = active_job
    return dict(active_job)


def _is_cancel_requested(user_id: str, network_id: str):
    active_job = state.neural.active_jobs.get(_job_key(user_id, network_id)) or {}
    return bool(active_job.get('cancel_requested'))


def cancel_neural_job(user_id: str, network_id: str):
    _refresh_registered_neural_jobs()
    key = _job_key(user_id, network_id)
    active_job = dict(state.neural.active_jobs.get(key) or {})
    if not active_job:
        raise ValueError(f'No active neural job for {network_id}.')

    if active_job.get('cancel_requested'):
        return dict(active_job)

    active_job['cancel_requested'] = True
    active_job['last_message'] = 'Cancellation requested.'
    state.neural.active_jobs[key] = active_job
    run_id = str(active_job.get('run_id') or '').strip()
    if run_id:
        paths = _build_job_runtime_paths(user_id, network_id, run_id)
        paths['cancel_path'].touch()
        _update_runtime_state(paths['runtime_path'], 'Cancellation requested by user.', level='warn', cancel_requested=True)
    _append_job_log(user_id, network_id, 'Cancellation requested by user.', level='warn', cancel_requested=True)
    _record_neural_event(
        'cancel_requested',
        network_id=network_id,
        run_id=run_id,
        status=active_job.get('status'),
    )
    return dict(state.neural.active_jobs.get(key) or active_job)


def sanitize_neural_runtime(user_id: str, network_id: str | None = None, wait_seconds: float = 2.5):
    safe_wait_seconds = max(0.0, min(10.0, float(wait_seconds)))
    requested = []
    removed_orphans = []
    still_running = []
    updated_runs = []

    candidate_keys = []
    for key in set(list(state.neural.active_jobs.keys()) + list(state.neural.job_threads.keys())):
        key_user_id, key_network_id = _parse_job_key(key)
        if key_user_id != user_id:
            continue
        if network_id and key_network_id != network_id:
            continue
        candidate_keys.append((key, key_network_id))

    for key, key_network_id in candidate_keys:
        active_job = dict(state.neural.active_jobs.get(key) or {})
        worker_ref = state.neural.job_threads.get(key)
        worker_alive = bool(worker_ref and getattr(worker_ref, 'is_alive', lambda: False)())
        run_id = str(active_job.get('run_id') or '').strip()
        if run_id:
            paths = _build_job_runtime_paths(user_id, key_network_id, run_id)
            paths['cancel_path'].touch()
            _update_runtime_state(paths['runtime_path'], 'Runtime sanitize requested by user.', level='warn', cancel_requested=True)

        if active_job and not active_job.get('cancel_requested'):
            active_job['cancel_requested'] = True
            active_job['last_message'] = 'Runtime sanitize requested.'
            state.neural.active_jobs[key] = active_job
            _append_job_log(user_id, key_network_id, 'Runtime sanitize requested by user.', level='warn')
            requested.append({
                'network_id': key_network_id,
                'run_id': active_job.get('run_id'),
            })

        if worker_alive and safe_wait_seconds > 0:
            try:
                worker_ref.join(timeout=safe_wait_seconds)
            except RuntimeError:
                pass
            worker_alive = bool(worker_ref and getattr(worker_ref, 'is_alive', lambda: False)())

        active_job = dict(state.neural.active_jobs.get(key) or {})
        if worker_alive:
            try:
                worker_ref.terminate()
                worker_ref.join(timeout=1.0)
            except Exception:
                pass
            worker_alive = bool(worker_ref and getattr(worker_ref, 'is_alive', lambda: False)())

        if active_job and not worker_alive:
            run_id = str(active_job.get('run_id') or '').strip()
            status = str(active_job.get('status') or '').strip().lower()
            if run_id and status in {'queued', 'running'}:
                updated_run = update_neural_run(
                    run_id,
                    status='cancelled',
                    error='Neural runtime sanitized after the job stopped responding.',
                    ended_at=time.time(),
                    duration_seconds=max(0.0, time.time() - float(active_job.get('started_at') or time.time())),
                )
                if updated_run:
                    updated_runs.append(updated_run['id'])
            state.neural.active_jobs.pop(key, None)
            state.neural.job_threads.pop(key, None)
            removed_orphans.append({
                'network_id': key_network_id,
                'run_id': run_id or None,
            })
            continue

        if worker_alive:
            still_running.append({
                'network_id': key_network_id,
                'run_id': active_job.get('run_id'),
            })

    gc.collect()

    return {
        'requested_cancellations': requested,
        'removed_orphans': removed_orphans,
        'still_running': still_running,
        'updated_runs': updated_runs,
        'wait_seconds': safe_wait_seconds,
    }


def _build_run_model_base_path(user_id: str, network_id: str, run_id: str):
    paths = get_network_storage_paths(user_id, network_id)
    run_dir = paths['runs_dir'] / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / 'model'


def _prepare_isolated_market_snapshot(config: dict, snapshot_path: Path, should_cancel=None):
    requested_symbol = str(config.get('symbol') or '').strip().upper()
    requested_timeframe = str(config.get('timeframe') or '').strip().upper()
    requested_bars = max(1, int(config.get('bars') or 1))
    try:
        market_context = wait_for_market_data(
            requested_symbol,
            requested_timeframe,
            requested_bars,
            timeout_seconds=30.0,
            poll_interval=0.1,
            source='neural',
            should_cancel=should_cancel,
        )
    except RuntimeError as error:
        if 'cancelled' in str(error).strip().lower():
            raise NeuralJobCancelledError('Neural job cancelled before the worker started.') from error
        raise
    if not market_context['ready']:
        diagnostics = dict(market_context.get('diagnostics') or {})
        request_status = str(market_context.get('request_status') or '').strip().lower()
        detail_parts = [
            f'{requested_symbol} {requested_timeframe} {requested_bars:,} bars',
            f"source={market_context.get('source') or 'missing'}",
        ]
        if request_status:
            detail_parts.append(f'request_status={request_status}')
        if diagnostics.get('bridge_heartbeat_age_seconds') is not None:
            detail_parts.append(f"bridge_heartbeat_age={float(diagnostics['bridge_heartbeat_age_seconds']):.2f}s")
        error = market_context.get('error') or 'Neural market view is not ready.'
        raise ValueError(f"{error} ({', '.join(detail_parts)})")

    snapshot_candles = [dict(candle) for candle in (market_context.get('candles') or [])]
    snapshot_payload = {
        'symbol': requested_symbol,
        'timeframe': requested_timeframe,
        'bars': requested_bars,
        'captured_at': time.time(),
        'source': str(market_context.get('source') or 'market_data'),
        'candles': snapshot_candles,
    }
    _write_market_snapshot(snapshot_path, snapshot_payload)
    return {
        'symbol': requested_symbol,
        'timeframe': requested_timeframe,
        'bars': requested_bars,
        'available_bars': len(snapshot_candles),
        'snapshot_path': str(snapshot_path),
        'captured_at': snapshot_payload['captured_at'],
        'cache_key': market_context.get('cache_key'),
        'source': market_context.get('source'),
    }


def _launch_neural_worker_async(user_id: str, network_id: str, run_id: str, run_type: str, config: dict, source_run_id: str | None = None):
    paths = _build_job_runtime_paths(user_id, network_id, run_id)

    def launcher():
        try:
            _update_runtime_state(
                paths['runtime_path'],
                'Preparing isolated market snapshot from requested market context.',
                level='info',
                progress=0.0,
                phase='preparing_snapshot',
                phase_label='Preparing snapshot',
                detail='Requesting and capturing market candles locally so the neural worker does not depend on the chart session.',
                snapshot_ready=False,
            )
            if _is_cancel_requested_from_path(paths['cancel_path']):
                raise NeuralJobCancelledError('Neural job cancelled before the worker started.')

            snapshot_meta = _prepare_isolated_market_snapshot(
                config,
                paths['market_snapshot_path'],
                should_cancel=lambda: _is_cancel_requested_from_path(paths['cancel_path']),
            )

            if _is_cancel_requested_from_path(paths['cancel_path']):
                raise NeuralJobCancelledError('Neural job cancelled before the worker started.')

            _update_runtime_state(
                paths['runtime_path'],
                f"Isolated market snapshot captured with {int(snapshot_meta['bars']):,} candles.",
                level='info',
                progress=0.01,
                phase='queued',
                phase_label='Queued',
                detail='Snapshot is ready. Launching the neural worker on isolated data.',
                snapshot_ready=True,
                market_snapshot=snapshot_meta,
            )

            worker = multiprocessing.get_context('spawn').Process(
                target=_run_neural_job_process,
                kwargs={
                    'user_id': user_id,
                    'network_id': network_id,
                    'run_id': run_id,
                    'run_type': run_type,
                    'config': config,
                    'source_run_id': source_run_id,
                    'market_snapshot_path': str(paths['market_snapshot_path']),
                },
            )
            _set_job_thread(user_id, network_id, worker)
            worker.start()
        except NeuralJobCancelledError as error:
            update_neural_run(
                run_id,
                status='cancelled',
                error=str(error),
                ended_at=time.time(),
                duration_seconds=0.0,
            )
            _write_runtime_state(paths['runtime_path'], {
                **_read_runtime_state(paths['runtime_path']),
                'status': 'cancelled',
                'finished_at': time.time(),
                'cancel_requested': True,
                'error': str(error),
                'last_message': str(error),
            })
            state.neural.active_jobs.pop(_job_key(user_id, network_id), None)
            state.neural.job_threads.pop(_job_key(user_id, network_id), None)
        except Exception as error:
            message = str(error)
            update_neural_run(
                run_id,
                status='failed',
                error=message,
                ended_at=time.time(),
                duration_seconds=0.0,
            )
            _write_runtime_state(paths['runtime_path'], {
                **_read_runtime_state(paths['runtime_path']),
                'status': 'failed',
                'finished_at': time.time(),
                'error': message,
                'last_message': message,
            })
            state.neural.last_error = message
            state.neural.active_jobs.pop(_job_key(user_id, network_id), None)
            state.neural.job_threads.pop(_job_key(user_id, network_id), None)

    launcher_thread = threading.Thread(
        target=launcher,
        name=f'neural-launcher-{network_id}-{run_id[:8]}',
        daemon=True,
    )
    _set_job_thread(user_id, network_id, launcher_thread)
    launcher_thread.start()


def _run_neural_job_process(user_id: str, network_id: str, run_id: str, run_type: str, config: dict, source_run_id: str | None = None, market_snapshot_path: str | None = None):
    _prepare_neural_worker_process()
    started_at = time.time()
    worker_pid = os.getpid()
    paths = _build_job_runtime_paths(user_id, network_id, run_id)
    if paths['cancel_path'].exists():
        paths['cancel_path'].unlink()
    _write_runtime_state(paths['runtime_path'], {
        'run_id': run_id,
        'run_type': run_type,
        'status': 'running',
        'started_at': started_at,
        'source_run_id': source_run_id,
        'progress': 0.0,
        'logs': [],
        'cancel_requested': False,
        'phase': 'starting',
        'phase_label': 'Starting worker',
        'detail': 'Initializing neural worker process.',
        'heartbeat_at': started_at,
        'worker_pid': worker_pid,
        'logs': [{
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': 'Neural worker process started.',
            'level': 'info',
        }],
        'last_message': 'Neural worker process started.',
    })
    heartbeat_stop, heartbeat_thread = _start_runtime_heartbeat(paths['runtime_path'], worker_pid)

    try:
        network = get_neural_network(network_id)
        runner = get_neural_runner(network)
        if not runner or not callable(runner.get(run_type)):
            raise ValueError(f'No {run_type} runner is registered for {network_id}.')

        _update_runtime_state(
            paths['runtime_path'],
            'Worker initialization completed. Loading runner.',
            level='info',
            progress=0.01,
            phase='starting',
            phase_label='Starting worker',
            detail='Loading neural runner and preparing runtime context.',
            market_snapshot_path=market_snapshot_path,
        )
        model_base_path = _build_run_model_base_path(user_id, network_id, run_id)
        log_callback = lambda message=None, level='info', progress=None, **job_updates: _update_runtime_state(
            paths['runtime_path'],
            message,
            level=level,
            progress=progress,
            **job_updates,
        )
        should_cancel = lambda: _is_cancel_requested_from_path(paths['cancel_path'])

        if run_type == 'train':
            result = runner['train'](
                config,
                str(model_base_path),
                market_snapshot_path=market_snapshot_path,
                log_callback=log_callback,
                should_cancel=should_cancel,
            )
            if result.get('artifact_path'):
                write_model_artifact_metadata(result.get('artifact_path'), {
                    'user_id': user_id,
                    'network_id': network_id,
                    'run_id': run_id,
                    'run_type': run_type,
                    'config': config,
                    'metrics': result.get('metrics') or {},
                    'score': result.get('score'),
                    'saved_at': time.time(),
                })
            completed = update_neural_run(
                run_id,
                status='completed',
                metrics_json=json.dumps(sanitize_json_value(result.get('metrics') or {}), ensure_ascii=True, allow_nan=False),
                artifact_path=result.get('artifact_path'),
                score=sanitize_json_value(result.get('score')),
                ended_at=time.time(),
                duration_seconds=time.time() - started_at,
                error=None,
            )
            score = float(result.get('score') or 0.0)
            current_best = get_best_neural_model(user_id, network_id)
            current_best_score = float(current_best['score']) if current_best and current_best.get('score') is not None else None
            if result.get('artifact_path') and (current_best_score is None or score > current_best_score):
                promote_neural_model_to_best(
                    user_id=user_id,
                    network_id=network_id,
                    run_id=run_id,
                    source_model_path=result.get('artifact_path'),
                    score=score,
                    metrics=result.get('metrics') or {},
                )
                completed = update_neural_run(
                    run_id,
                    promoted_to_best=1,
                )
                _update_runtime_state(paths['runtime_path'], 'Training artifact promoted to best model.', level='success')
        else:
            source_run = get_neural_run(source_run_id) if source_run_id else None
            best_model = get_best_neural_model(user_id, network_id) if not source_run else None
            model_path = source_run.get('artifact_path') if source_run else (best_model or {}).get('model_path')
            result = runner['test'](
                config,
                model_path,
                market_snapshot_path=market_snapshot_path,
                log_callback=log_callback,
                should_cancel=should_cancel,
            )
            if result.get('model_path') or model_path:
                write_model_artifact_metadata(result.get('model_path') or model_path, {
                    'user_id': user_id,
                    'network_id': network_id,
                    'run_id': source_run['id'] if source_run else run_id,
                    'evaluation_run_id': run_id,
                    'run_type': 'test',
                    'config': config,
                    'metrics': result.get('metrics') or {},
                    'score': result.get('score'),
                    'saved_at': time.time(),
                })
            metrics = result.get('metrics') or {}
            score = float(result.get('score') or 0.0)
            promoted = False
            current_best = get_best_neural_model(user_id, network_id)
            current_best_score = float(current_best['score']) if current_best and current_best.get('score') is not None else None

            if source_run and (current_best_score is None or score > current_best_score):
                promoted = True
                promoted_best = promote_neural_model_to_best(
                    user_id=user_id,
                    network_id=network_id,
                    run_id=source_run['id'],
                    source_model_path=result.get('model_path') or model_path,
                    score=score,
                    metrics=metrics,
                )
                if (result.get('model_path') or model_path) != promoted_best['model_path']:
                    remove_model_artifact(result.get('model_path') or model_path)
                update_neural_run(
                    source_run['id'],
                    artifact_path=promoted_best['model_path'],
                    promoted_to_best=1,
                    score=score,
                )
            elif source_run:
                remove_model_artifact(result.get('model_path') or model_path)

            completed = update_neural_run(
                run_id,
                status='completed',
                metrics_json=json.dumps(sanitize_json_value(metrics), ensure_ascii=True, allow_nan=False),
                artifact_path=(result.get('model_path') or model_path) if not promoted else get_best_neural_model(user_id, network_id)['model_path'],
                score=sanitize_json_value(score),
                promoted_to_best=1 if promoted else 0,
                ended_at=time.time(),
                duration_seconds=time.time() - started_at,
                error=None,
            )
            if promoted:
                _update_runtime_state(paths['runtime_path'], 'Model promoted to best after test.', level='success', progress=1.0)
        _write_runtime_state(paths['runtime_path'], {
            **_read_runtime_state(paths['runtime_path']),
            'status': 'completed',
            'finished_at': time.time(),
            'progress': 1.0,
        })
        return completed
    except Exception as error:
        if _is_cancel_requested_from_path(paths['cancel_path']):
            cancelled = update_neural_run(
                run_id,
                status='cancelled',
                error='Neural job cancelled by user.',
                ended_at=time.time(),
                duration_seconds=time.time() - started_at,
            )
            _write_runtime_state(paths['runtime_path'], {
                **_read_runtime_state(paths['runtime_path']),
                'status': 'cancelled',
                'finished_at': time.time(),
                'error': 'Neural job cancelled by user.',
                'cancel_requested': True,
            })
            return cancelled
        failed = update_neural_run(
            run_id,
            status='failed',
            error=str(error),
            ended_at=time.time(),
            duration_seconds=time.time() - started_at,
        )
        _write_runtime_state(paths['runtime_path'], {
            **_read_runtime_state(paths['runtime_path']),
            'status': 'failed',
            'finished_at': time.time(),
            'error': str(error),
        })
        return failed
    finally:
        heartbeat_stop.set()
        try:
            heartbeat_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            _touch_runtime_heartbeat(paths['runtime_path'], worker_pid=worker_pid)
        except Exception:
            pass


def start_neural_training(user_id: str, network_id: str, config_payload: dict | None):
    _refresh_registered_neural_jobs()
    key = _job_key(user_id, network_id)
    if key in state.neural.active_jobs or _recover_active_job_from_runtime(user_id, network_id):
        raise ValueError(f'Neural job already running for {network_id}.')

    config = _normalize_network_config(network_id, config_payload)
    run = create_neural_run(
        user_id=user_id,
        network_id=network_id,
        run_type='train',
        config=config,
    )
    _set_active_job(user_id, network_id, {
        'run_id': run['id'],
        'run_type': 'train',
        'status': 'queued',
        'started_at': time.time(),
        'progress': 0.0,
        'logs': [{
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': 'Neural job queued. Waiting for worker process.',
            'level': 'info',
        }],
        'cancel_requested': False,
        'phase': 'queued',
        'phase_label': 'Queued',
        'last_message': 'Neural job queued. Waiting for worker process.',
        'detail': 'Waiting for the isolated neural worker to start.',
        'data_feed_status': 'waiting',
        'data_feed_label': 'Waiting for data',
        'data_feed_detail': 'Worker process has not produced runtime data yet.',
    })
    paths = _build_job_runtime_paths(user_id, network_id, run['id'])
    _write_runtime_state(paths['runtime_path'], {
        'run_id': run['id'],
        'run_type': 'train',
        'status': 'queued',
        'started_at': time.time(),
        'progress': 0.0,
        'logs': [{
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': 'Neural job queued. Waiting for worker process.',
            'level': 'info',
        }],
        'cancel_requested': False,
        'phase': 'queued',
        'phase_label': 'Queued',
        'last_message': 'Neural job queued. Waiting for worker process.',
        'detail': 'Waiting for the isolated neural worker to start.',
        'heartbeat_at': time.time(),
    })
    _launch_neural_worker_async(
        user_id=user_id,
        network_id=network_id,
        run_id=run['id'],
        run_type='train',
        config=config,
    )
    _record_neural_event(
        'queued',
        network_id=network_id,
        run_id=run['id'],
        run_type='train',
        bars=config.get('bars'),
        symbol=config.get('symbol'),
        timeframe=config.get('timeframe'),
    )
    return run


def start_neural_test(user_id: str, network_id: str, config_payload: dict | None, source_run_id: str | None = None):
    _refresh_registered_neural_jobs()
    key = _job_key(user_id, network_id)
    if key in state.neural.active_jobs or _recover_active_job_from_runtime(user_id, network_id):
        raise ValueError(f'Neural job already running for {network_id}.')

    config = _normalize_network_config(network_id, config_payload)
    run = create_neural_run(
        user_id=user_id,
        network_id=network_id,
        run_type='test',
        config=config,
        source_run_id=source_run_id,
    )
    _set_active_job(user_id, network_id, {
        'run_id': run['id'],
        'run_type': 'test',
        'status': 'queued',
        'started_at': time.time(),
        'source_run_id': source_run_id,
        'progress': 0.0,
        'logs': [{
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': 'Neural job queued. Waiting for worker process.',
            'level': 'info',
        }],
        'cancel_requested': False,
        'phase': 'queued',
        'phase_label': 'Queued',
        'last_message': 'Neural job queued. Waiting for worker process.',
        'detail': 'Waiting for the isolated neural worker to start.',
        'data_feed_status': 'waiting',
        'data_feed_label': 'Waiting for data',
        'data_feed_detail': 'Worker process has not produced runtime data yet.',
    })
    paths = _build_job_runtime_paths(user_id, network_id, run['id'])
    _write_runtime_state(paths['runtime_path'], {
        'run_id': run['id'],
        'run_type': 'test',
        'status': 'queued',
        'started_at': time.time(),
        'source_run_id': source_run_id,
        'progress': 0.0,
        'logs': [{
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': 'Neural job queued. Waiting for worker process.',
            'level': 'info',
        }],
        'cancel_requested': False,
        'phase': 'queued',
        'phase_label': 'Queued',
        'last_message': 'Neural job queued. Waiting for worker process.',
        'detail': 'Waiting for the isolated neural worker to start.',
        'heartbeat_at': time.time(),
    })
    _launch_neural_worker_async(
        user_id=user_id,
        network_id=network_id,
        run_id=run['id'],
        run_type='test',
        config=config,
        source_run_id=source_run_id,
    )
    _record_neural_event(
        'queued',
        network_id=network_id,
        run_id=run['id'],
        run_type='test',
        bars=config.get('bars'),
        symbol=config.get('symbol'),
        timeframe=config.get('timeframe'),
        source_run_id=source_run_id,
    )
    return run


def debug_neural_feature_context(user_id: str, network_id: str, config_payload: dict | None):
    network = get_neural_network(network_id)
    if not network:
        raise ValueError(f'Unknown neural network: {network_id}')

    config = _normalize_network_config(network_id, config_payload)
    runner = get_neural_runner(network)
    if not runner or not callable(runner.get('debug_features')):
        raise ValueError(f'No feature debug runner is registered for {network_id}.')

    debug_payload = runner['debug_features'](config)
    return {
        'process_id': os.getpid(),
        'network_id': network_id,
        'config': sanitize_json_value(config),
        'debug': sanitize_json_value(debug_payload),
    }

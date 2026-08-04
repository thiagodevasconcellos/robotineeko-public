import json
import os
import pickle
import tempfile
import zipfile
from pathlib import Path

import numpy as np

try:
    from .reinforcement.config import RLFeatureConfig, RLTrainingConfig
    from .reinforcement.features import MarketRegimeRLFeaturePipeline, VasconcellosRLFeaturePipeline
    from .reinforcement.trainer import (
        CancellationRequestedError as RLCancellationRequestedError,
        StableBaselinesRLTrainer,
        evaluate_trained_model,
        load_trained_model,
    )
    from .supervised.config import SupervisedFeatureConfig, SupervisedTrainingConfig
    from .supervised.features import BasicFeedForwardFeaturePipeline
    from .supervised.trainer import BasicFeedForwardRegressor, TemporalConvolutionalClassifier, TemporalConvolutionalRegressor
except ImportError:
    from neural.reinforcement.config import RLFeatureConfig, RLTrainingConfig
    from neural.reinforcement.features import MarketRegimeRLFeaturePipeline, VasconcellosRLFeaturePipeline
    from neural.reinforcement.trainer import (
        CancellationRequestedError as RLCancellationRequestedError,
        StableBaselinesRLTrainer,
        evaluate_trained_model,
        load_trained_model,
    )
    from neural.supervised.config import SupervisedFeatureConfig, SupervisedTrainingConfig
    from neural.supervised.features import BasicFeedForwardFeaturePipeline
    from neural.supervised.trainer import CancellationRequestedError as SupervisedCancellationRequestedError, BasicFeedForwardRegressor, TemporalConvolutionalClassifier, TemporalConvolutionalRegressor
else:
    from .supervised.trainer import CancellationRequestedError as SupervisedCancellationRequestedError


MICRO_COST_EDGE_EVENT_THRESHOLD_SELECTION_VERSION = 2
MICRO_COST_EDGE_HIERARCHICAL_EVENT_THRESHOLD_SELECTION_VERSION = 1


def _load_market_snapshot_candles(market_snapshot_path: str | None):
    safe_path = Path(market_snapshot_path) if market_snapshot_path else None
    if safe_path is None or not safe_path.exists():
        return None
    with safe_path.open('rb') as handle:
        payload = pickle.load(handle)
    candles = payload.get('candles') if isinstance(payload, dict) else None
    return list(candles or [])


def _build_feature_config(config: dict):
    return RLFeatureConfig(
        symbol_name=config['symbol'],
        timeframe=config['timeframe'],
        bars=config['bars'],
    )


def _build_market_regime_feature_config(config: dict):
    return RLFeatureConfig(
        symbol_name=config['symbol'],
        timeframe=config['timeframe'],
        bars=config['bars'],
        feature_profile='market_regime',
        market_regime_ema_fast_period=max(1, int(config.get('marketRegimeEmaFastPeriod', 9))),
        market_regime_ema_slow_period=max(2, int(config.get('marketRegimeEmaSlowPeriod', 21))),
        market_regime_adx_period=max(2, int(config.get('marketRegimeAdxPeriod', 14))),
        market_regime_atr_period=max(2, int(config.get('marketRegimeAtrPeriod', 14))),
        market_regime_bollinger_period=max(2, int(config.get('marketRegimeBollingerPeriod', 20))),
        market_regime_bollinger_std_dev=max(0.0, float(config.get('marketRegimeBollingerStdDev', 2.0))),
        market_regime_donchian_period=max(2, int(config.get('marketRegimeDonchianPeriod', 20))),
        market_regime_choppiness_period=max(2, int(config.get('marketRegimeChoppinessPeriod', 14))),
        market_regime_supertrend_atr_period=max(2, int(config.get('marketRegimeSupertrendAtrPeriod', 10))),
        market_regime_supertrend_multiplier=max(0.1, float(config.get('marketRegimeSupertrendMultiplier', 3.0))),
        market_regime_vwap_source=str(config.get('marketRegimeVwapSource', 'hlc3') or 'hlc3').strip().lower(),
        market_regime_score_smoothing_period=max(1, int(config.get('marketRegimeScoreSmoothingPeriod', 5))),
        market_regime_regime_confirm_bars=max(1, int(config.get('marketRegimeConfirmBars', 3))),
    )


def _build_training_config(config: dict):
    return RLTrainingConfig(
        algorithm=config['algorithm'],
        total_timesteps=config['totalTimesteps'],
        learning_rate=config['learningRate'],
        gamma=config['gamma'],
        observation_window=config['observationWindow'],
        transaction_cost=config['transactionCost'],
        reward_scale=config['rewardScale'],
        position_size=config['positionSize'],
        allow_short=config['allowShort'],
        holding_cost=float(config.get('holdingCost', 0.0)),
        flat_reward=float(config.get('flatReward', 0.0)),
        imbalance_penalty=float(config.get('imbalancePenalty', 0.0)),
        same_side_streak_penalty=float(config.get('sameSideStreakPenalty', 0.0)),
    )


def _split_training_frame(frame, validation_split: float, test_split: float):
    total_rows = len(frame)
    if total_rows < 100:
        raise ValueError('Neural training requires at least 100 clean rows after feature generation.')

    test_rows = max(1, int(total_rows * max(0.0, float(test_split))))
    validation_rows = max(1, int(total_rows * max(0.0, float(validation_split))))
    train_rows = total_rows - validation_rows - test_rows

    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    train_frame = frame.iloc[:train_rows].reset_index(drop=True)
    validation_frame = frame.iloc[train_rows:train_rows + validation_rows].reset_index(drop=True)
    test_frame = frame.iloc[train_rows + validation_rows:].reset_index(drop=True)

    if len(validation_frame) < 10 or len(test_frame) < 10:
        raise ValueError('Validation and test splits must each contain at least 10 rows.')

    return {
        'train': train_frame,
        'validation': validation_frame,
        'test': test_frame,
        'sizes': {
            'total': total_rows,
            'train': len(train_frame),
            'validation': len(validation_frame),
            'test': len(test_frame),
        },
    }


def run_vasconcellos_rl_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str | None, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing feature pipeline.', progress=0.05)
    feature_config = _build_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        VasconcellosRLFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else VasconcellosRLFeaturePipeline.from_bridge(feature_config)
    )
    frame = pipeline.build_training_frame(
        dropna=True,
        normalize_volume=bool(config['normalizeVolume']),
    )
    log(f'Feature frame built with {len(frame)} rows.', progress=0.18)
    splits = _split_training_frame(frame, config['validationSplit'], config['testSplit'])
    split_sizes = splits['sizes']
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )
    trainer = StableBaselinesRLTrainer(
        splits['train'],
        feature_columns=pipeline.observation_columns,
        config=_build_training_config(config),
    )
    log('Training model.', progress=0.32)
    total_timesteps = max(1, int(config['totalTimesteps']))
    model = trainer.train(
        should_cancel=should_cancel,
        progress_callback=lambda message=None, **progress_state: log(
            message,
            progress=0.32 + (0.46 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='RL optimization',
            detail=(
                f"{int(progress_state.get('current_timestep') or 0):,}"
                f" / {total_timesteps:,} timesteps"
                + (
                    f" · {float(progress_state.get('throughput') or 0.0):.1f} steps/s"
                    if progress_state.get('throughput') is not None
                    else ''
                )
            ),
            current_timestep=int(progress_state.get('current_timestep') or 0),
            total_timesteps=total_timesteps,
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            throughput=progress_state.get('throughput'),
        ),
    )
    log('Training finished. Running validation.', progress=0.78)
    validation_metrics = evaluate_trained_model(
        model,
        splits['validation'],
        pipeline.observation_columns,
        config=_build_training_config(config),
        episodes=config['testEpisodes'],
        should_cancel=should_cancel,
        progress_callback=lambda **progress_state: log(
            (
                'Validation episode '
                f"{int(progress_state.get('current_episode') or 0)}/{max(1, int(config['testEpisodes']))} started."
            )
            if progress_state.get('current_episode') and progress_state.get('current_episode') < max(1, int(config['testEpisodes']))
            and progress_state.get('current_episode') == 1
            else None,
            progress=0.78 + (
                0.12 * (
                    (float(progress_state.get('current_episode') or 0))
                    / max(1, int(progress_state.get('total_episodes') or 1))
                )
            ),
            phase='validation',
            phase_label='Validation episodes',
            detail=(
                f"Episode {int(progress_state.get('current_episode') or 0):,}"
                f" / {int(progress_state.get('total_episodes') or 1):,}"
            ),
            current_episode=int(progress_state.get('current_episode') or 0),
            total_episodes=int(progress_state.get('total_episodes') or 1),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            last_episode_reward=progress_state.get('last_episode_reward'),
            last_episode_steps=progress_state.get('last_episode_steps'),
        ),
    )
    model.save(str(model_base_path))
    artifact_path = f'{model_base_path}.zip'
    log('Model artifact saved.', progress=0.92, phase='saving', phase_label='Saving artifact', detail='Persisting trained model to disk.')

    metrics = {
        'rows': len(frame),
        'split_sizes': split_sizes,
        'validation': validation_metrics,
        'observation_columns': list(pipeline.observation_columns),
        'observation_size': len(pipeline.observation_columns) * max(1, int(config['observationWindow'])),
    }
    score = float(validation_metrics.get('mean_reward') or 0.0)
    log(f'Validation finished with mean reward {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': metrics,
        'score': score,
    }


def run_vasconcellos_rl_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str | None, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing test dataset.', progress=0.08)
    feature_config = _build_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        VasconcellosRLFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else VasconcellosRLFeaturePipeline.from_bridge(feature_config)
    )
    frame = pipeline.build_training_frame(
        dropna=True,
        normalize_volume=bool(config['normalizeVolume']),
    )
    splits = _split_training_frame(frame, config['validationSplit'], config['testSplit'])
    split_sizes = splits['sizes']
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.22,
    )
    model = load_trained_model(model_path, config['algorithm'])
    log('Running test episodes.', progress=0.45)
    metrics = evaluate_trained_model(
        model,
        splits['test'],
        pipeline.observation_columns,
        config=_build_training_config(config),
        episodes=config['testEpisodes'],
        should_cancel=should_cancel,
        progress_callback=lambda **progress_state: log(
            None,
            progress=0.45 + (
                0.5 * (
                    (float(progress_state.get('current_episode') or 0))
                    / max(1, int(progress_state.get('total_episodes') or 1))
                )
            ),
            phase='testing',
            phase_label='Test episodes',
            detail=(
                f"Episode {int(progress_state.get('current_episode') or 0):,}"
                f" / {int(progress_state.get('total_episodes') or 1):,}"
            ),
            current_episode=int(progress_state.get('current_episode') or 0),
            total_episodes=int(progress_state.get('total_episodes') or 1),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            last_episode_reward=progress_state.get('last_episode_reward'),
            last_episode_steps=progress_state.get('last_episode_steps'),
        ),
    )
    metrics['split_sizes'] = split_sizes
    score = float(metrics.get('mean_reward') or 0.0)
    log(f'Test finished with mean reward {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def debug_vasconcellos_rl_features(config: dict):
    feature_config = _build_feature_config(config)
    pipeline = VasconcellosRLFeaturePipeline.from_bridge(feature_config)
    pipeline.apply()
    available_columns = list(pipeline.symbol.candles.columns)
    return {
        'network_id': 'vasconcellos_rl_v1',
        'process_id': os.getpid(),
        'symbol': feature_config.symbol_name,
        'timeframe': feature_config.timeframe,
        'bars': feature_config.bars,
        'requested_columns': list(pipeline.requested_observation_columns),
        'resolved_columns': list(pipeline.observation_columns),
        'available_columns': available_columns,
        'available_indicator_columns': [
            column_name
            for column_name in available_columns
            if column_name not in ('time', 'open', 'high', 'low', 'close', 'volume')
        ],
    }


def run_market_regime_rl_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str | None, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing market regime feature pipeline.', progress=0.05)
    feature_config = _build_market_regime_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        MarketRegimeRLFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else MarketRegimeRLFeaturePipeline.from_bridge(feature_config)
    )
    frame = pipeline.build_training_frame(
        dropna=True,
        normalize_volume=bool(config['normalizeVolume']),
    )
    log(f'Market regime frame built with {len(frame)} rows.', progress=0.18)
    splits = _split_training_frame(frame, config['validationSplit'], config['testSplit'])
    split_sizes = splits['sizes']
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )
    trainer = StableBaselinesRLTrainer(
        splits['train'],
        feature_columns=pipeline.observation_columns,
        config=_build_training_config(config),
    )
    log('Training PPO policy on OHLCV + Market Regime observations.', progress=0.32)
    total_timesteps = max(1, int(config['totalTimesteps']))
    model = trainer.train(
        should_cancel=should_cancel,
        progress_callback=lambda message=None, **progress_state: log(
            message,
            progress=0.32 + (0.46 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='RL optimization',
            detail=(
                f"{int(progress_state.get('current_timestep') or 0):,}"
                f" / {total_timesteps:,} timesteps"
                + (
                    f" · {float(progress_state.get('throughput') or 0.0):.1f} steps/s"
                    if progress_state.get('throughput') is not None
                    else ''
                )
            ),
            current_timestep=int(progress_state.get('current_timestep') or 0),
            total_timesteps=total_timesteps,
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            throughput=progress_state.get('throughput'),
        ),
    )
    log('Training finished. Running validation.', progress=0.78)
    validation_metrics = evaluate_trained_model(
        model,
        splits['validation'],
        pipeline.observation_columns,
        config=_build_training_config(config),
        episodes=config['testEpisodes'],
        should_cancel=should_cancel,
        progress_callback=lambda **progress_state: log(
            None,
            progress=0.78 + (
                0.12 * (
                    (float(progress_state.get('current_episode') or 0))
                    / max(1, int(progress_state.get('total_episodes') or 1))
                )
            ),
            phase='validation',
            phase_label='Validation episodes',
            detail=(
                f"Episode {int(progress_state.get('current_episode') or 0):,}"
                f" / {int(progress_state.get('total_episodes') or 1):,}"
            ),
            current_episode=int(progress_state.get('current_episode') or 0),
            total_episodes=int(progress_state.get('total_episodes') or 1),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            last_episode_reward=progress_state.get('last_episode_reward'),
            last_episode_steps=progress_state.get('last_episode_steps'),
        ),
    )
    model.save(str(model_base_path))
    artifact_path = f'{model_base_path}.zip'
    log('Model artifact saved.', progress=0.92, phase='saving', phase_label='Saving artifact', detail='Persisting trained model to disk.')

    metrics = {
        'rows': len(frame),
        'split_sizes': split_sizes,
        'validation': validation_metrics,
        'observation_columns': list(pipeline.observation_columns),
        'observation_size': len(pipeline.observation_columns) * max(1, int(config['observationWindow'])),
    }
    score = float(validation_metrics.get('mean_reward') or 0.0)
    log(f'Validation finished with mean reward {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': metrics,
        'score': score,
    }


def run_market_regime_rl_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str | None, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing OHLCV + Market Regime test dataset.', progress=0.08)
    feature_config = _build_market_regime_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        MarketRegimeRLFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else MarketRegimeRLFeaturePipeline.from_bridge(feature_config)
    )
    frame = pipeline.build_training_frame(
        dropna=True,
        normalize_volume=bool(config['normalizeVolume']),
    )
    splits = _split_training_frame(frame, config['validationSplit'], config['testSplit'])
    split_sizes = splits['sizes']
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.22,
    )
    model = load_trained_model(model_path, config['algorithm'])
    log('Running test episodes.', progress=0.45)
    metrics = evaluate_trained_model(
        model,
        splits['test'],
        pipeline.observation_columns,
        config=_build_training_config(config),
        episodes=config['testEpisodes'],
        should_cancel=should_cancel,
        progress_callback=lambda **progress_state: log(
            None,
            progress=0.45 + (
                0.5 * (
                    (float(progress_state.get('current_episode') or 0))
                    / max(1, int(progress_state.get('total_episodes') or 1))
                )
            ),
            phase='testing',
            phase_label='Test episodes',
            detail=(
                f"Episode {int(progress_state.get('current_episode') or 0):,}"
                f" / {int(progress_state.get('total_episodes') or 1):,}"
            ),
            current_episode=int(progress_state.get('current_episode') or 0),
            total_episodes=int(progress_state.get('total_episodes') or 1),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            last_episode_reward=progress_state.get('last_episode_reward'),
            last_episode_steps=progress_state.get('last_episode_steps'),
        ),
    )
    metrics['split_sizes'] = split_sizes
    score = float(metrics.get('mean_reward') or 0.0)
    log(f'Test finished with mean reward {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def debug_market_regime_rl_features(config: dict):
    feature_config = _build_market_regime_feature_config(config)
    pipeline = MarketRegimeRLFeaturePipeline.from_bridge(feature_config)
    pipeline.apply()
    available_columns = list(pipeline.symbol.candles.columns)
    return {
        'network_id': str(config.get('networkId') or 'market_regime_rl_v1'),
        'process_id': os.getpid(),
        'symbol': feature_config.symbol_name,
        'timeframe': feature_config.timeframe,
        'bars': feature_config.bars,
        'requested_columns': list(pipeline.requested_observation_columns),
        'resolved_columns': list(pipeline.observation_columns),
        'available_columns': available_columns,
        'available_indicator_columns': [
            column_name
            for column_name in available_columns
            if column_name not in ('time', 'open', 'high', 'low', 'close', 'volume')
        ],
    }


NETWORK_RUNNER_REGISTRY = {}


def _build_supervised_feature_config(config: dict):
    normalization_columns = config.get('normalizationColumns')
    if not isinstance(normalization_columns, list):
        normalization_columns = ['ff_volume'] if bool(config.get('normalizeVolume', True)) else []
    network_id = str(config.get('networkId') or '').strip()
    if network_id == 'neural_market_regime_cnn_v1':
        feature_profile = 'market_regime_fusion'
    elif network_id == 'ema_low_adx_setup_quality_cnn_v6':
        feature_profile = 'ema_low_adx_setup_quality_pattern_score_cluster_context'
    elif network_id in {'ema_low_adx_setup_quality_cnn_v5', 'ema_low_adx_setup_quality_cnn_v7'}:
        feature_profile = 'ema_low_adx_setup_quality_pattern_score_context'
    elif network_id in {'ema_low_adx_setup_quality_cnn_v1', 'ema_low_adx_setup_quality_cnn_v2', 'ema_low_adx_setup_quality_cnn_v3', 'ema_low_adx_setup_quality_cnn_v4'}:
        feature_profile = 'ema_low_adx_setup_quality'
    elif network_id in {'micro_cost_edge_cnn_v4', 'micro_cost_edge_cnn_v5'}:
        feature_profile = 'micro_cost_edge_pattern_score_context'
    elif network_id in {'micro_cost_edge_cnn_v1', 'micro_cost_edge_cnn_v2', 'micro_cost_edge_cnn_v3'}:
        feature_profile = 'micro_cost_edge'
    elif network_id in {'candle_reversal_cnn_v11_scores_only', 'candle_reversal_cnn_v12_scores_only', 'candle_reversal_setup_quality_cnn_v1'}:
        feature_profile = 'candle_reversal_pattern_score_context'
    elif network_id == 'candle_reversal_cnn_v11':
        feature_profile = 'candle_reversal_pattern_context'
    elif network_id in {'candle_reversal_cnn_v6', 'candle_reversal_cnn_v7', 'candle_reversal_cnn_v7_1', 'candle_reversal_cnn_v8', 'candle_reversal_cnn_v9', 'candle_reversal_cnn_v10', 'candle_reversal_cnn_v10_1'}:
        feature_profile = 'candle_reversal_context'
    elif network_id in {'candle_reversal_cnn_v1', 'candle_reversal_cnn_v2', 'candle_reversal_cnn_v3', 'candle_reversal_cnn_v4', 'candle_reversal_cnn_v5'}:
        feature_profile = 'candle_reversal'
    else:
        feature_profile = 'indicator_fusion'
    return SupervisedFeatureConfig(
        symbol_name=config['symbol'],
        timeframe=config['timeframe'],
        bars=config['bars'],
        network_id=network_id,
        feature_profile=feature_profile,
        observation_window=max(1, int(config.get('observationWindow', 1))),
        normalize_volume=bool(normalization_columns),
        normalization_mode='selected_columns' if normalization_columns else 'none',
        normalization_columns=normalization_columns,
        target_horizon=max(1, int(config.get('targetHorizon', 1))),
        target_mode=str(config.get('targetMode', 'excursion_signal') or 'excursion_signal').strip().lower(),
        target_std_window=max(2, int(config.get('targetStdWindow', 20))),
        target_std_threshold=max(0.0, float(config.get('targetStdThreshold', 1.0))),
        target_regime_compression_threshold=max(0.0, float(config.get('targetRegimeCompressionThreshold', 0.9))),
        target_regime_volatility_threshold=max(0.0, float(config.get('targetRegimeVolatilityThreshold', 2.2))),
        target_regime_trend_efficiency_threshold=max(0.0, float(config.get('targetRegimeTrendEfficiencyThreshold', 0.55))),
        target_regime_directional_move_threshold=max(0.0, float(config.get('targetRegimeDirectionalMoveThreshold', 0.35))),
        target_regime_directional_dominance_threshold=max(0.0, float(config.get('targetRegimeDirectionalDominanceThreshold', 0.6))),
        target_pretrend_lookback=max(2, int(config.get('pretrendLookback', 6))),
        target_pretrend_threshold=max(0.0, float(config.get('pretrendThreshold', 1.2))),
        target_reversal_threshold=max(0.0, float(config.get('reversalThreshold', 1.0))),
        target_dominance_ratio=max(1.0, float(config.get('dominanceRatio', 1.35))),
        target_reversal_take_profit_atr=max(0.05, float(config.get('reversalTakeProfitAtr', 0.75))),
        target_reversal_stop_loss_atr=max(0.05, float(config.get('reversalStopLossAtr', 1.0))),
        target_stage1_neutral_pretrend_ceiling=max(0.0, float(config.get('stage1NeutralPretrendCeiling', 0.85))),
        target_stage1_neutral_excursion_ceiling=max(0.0, float(config.get('stage1NeutralExcursionCeiling', 0.85))),
        target_stage1_positive_pretrend_floor=max(0.0, float(config.get('stage1PositivePretrendFloor', 0.0))),
        target_stage1_positive_excursion_floor=max(0.0, float(config.get('stage1PositiveExcursionFloor', 0.0))),
        target_clean_neutral_pretrend_ceiling=max(0.0, float(config.get('targetCleanNeutralPretrendCeiling', 0.0))),
        target_clean_neutral_excursion_ceiling=max(0.0, float(config.get('targetCleanNeutralExcursionCeiling', 0.0))),
        target_clean_positive_pretrend_floor=max(0.0, float(config.get('targetCleanPositivePretrendFloor', 0.0))),
        target_clean_positive_excursion_floor=max(0.0, float(config.get('targetCleanPositiveExcursionFloor', 0.0))),
        target_quality_good_excursion_threshold=max(0.0, float(config.get('targetQualityGoodExcursionThreshold', 0.82))),
        target_quality_bad_excursion_threshold=max(0.0, float(config.get('targetQualityBadExcursionThreshold', 0.52))),
        target_quality_good_dominance_ratio=max(1.0, float(config.get('targetQualityGoodDominanceRatio', 1.1))),
        target_quality_bad_dominance_ratio=max(1.0, float(config.get('targetQualityBadDominanceRatio', 1.1))),
        target_quality_good_counter_excursion_ceiling=max(0.0, float(config.get('targetQualityGoodCounterExcursionCeiling', 0.45))),
        target_quality_bad_counter_excursion_ceiling=max(0.0, float(config.get('targetQualityBadCounterExcursionCeiling', 0.45))),
        pip_size=max(1e-8, float(config.get('pipSize', 0.0001))),
        round_trip_cost_pips=max(0.0, float(config.get('roundTripCostPips', 1.6))),
        target_cost_edge_multiple=max(1.0, float(config.get('targetCostEdgeMultiple', 1.75))),
        setup_adx_ceiling=max(0.0, float(config.get('setupAdxCeiling', 28.0))),
        setup_prev_rsi_ceiling=max(0.0, float(config.get('setupPrevRsiCeiling', 38.0))),
        setup_current_rsi_floor=max(0.0, float(config.get('setupCurrentRsiFloor', 38.0))),
        setup_current_rsi_ceiling=max(0.0, float(config.get('setupCurrentRsiCeiling', 50.0))),
        setup_touch_slack_atr=max(0.0, float(config.get('setupTouchSlackAtr', 0.06))),
        setup_prev_band_slack_atr=max(0.0, float(config.get('setupPrevBandSlackAtr', 0.08))),
        setup_bounce_fraction=max(0.0, float(config.get('setupBounceFraction', 0.02))),
        setup_di_spread_floor=max(0.0, float(config.get('setupDiSpreadFloor', 0.0))),
        setup_candidate_min_gap_bars=max(0, int(config.get('setupCandidateMinGapBars', 0))),
    )


def _build_supervised_training_config(config: dict):
    hidden_layers = config.get('hiddenLayers')
    if not isinstance(hidden_layers, list) or not hidden_layers:
        hidden_layers = [{'size': max(4, int(config.get('hiddenSize', 32))), 'activation': 'tanh', 'dropout': 0.0}]
    return SupervisedTrainingConfig(
        hidden_layers=hidden_layers,
        conv_filters=max(4, int(config.get('convFilters', 16))),
        kernel_size=max(2, int(config.get('kernelSize', 3))),
        learning_rate=float(config.get('learningRate', 0.01)),
        epochs=max(1, int(config.get('epochs', 120))),
        batch_size=max(1, int(config.get('batchSize', 64))),
        threshold=float(config.get('classificationThreshold', 0.5)),
        seed=max(1, int(config.get('seed', 42))),
        class_weight_mode=str(config.get('classWeightMode', 'none') or 'none').strip().lower(),
        class_weight_exponent=max(0.0, float(config.get('classWeightExponent', 1.0) or 0.0)),
        neutral_retention=max(0.05, min(1.0, float(config.get('neutralRetention', 1.0) or 1.0))),
    )


def _split_supervised_frame(frame, validation_split: float, test_split: float):
    total_rows = len(frame)
    if total_rows < 120:
        raise ValueError('Supervised training requires at least 120 clean rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(test_split))))
    validation_rows = max(10, int(total_rows * max(0.0, float(validation_split))))
    train_rows = total_rows - validation_rows - test_rows

    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    return {
        'train': frame.iloc[:train_rows].reset_index(drop=True),
        'validation': frame.iloc[train_rows:train_rows + validation_rows].reset_index(drop=True),
        'test': frame.iloc[train_rows + validation_rows:].reset_index(drop=True),
        'sizes': {
            'total': total_rows,
            'train': train_rows,
            'validation': validation_rows,
            'test': len(frame.iloc[train_rows + validation_rows:]),
        },
    }


def _build_class_index_counts(y_values):
    counts = {}
    for class_index in np.asarray(y_values, dtype=int).reshape(-1):
        counts[str(int(class_index))] = counts.get(str(int(class_index)), 0) + 1
    return counts


def _rebalance_sequence_classes(X_values, y_values, *, seed: int, retained_class_index: int | None = None, retention: float = 1.0):
    X_array = np.asarray(X_values, dtype=float)
    y_array = np.asarray(y_values, dtype=int).reshape(-1)
    summary = {
        'rows_before': int(len(y_array)),
        'rows_after': int(len(y_array)),
        'class_counts_before': _build_class_index_counts(y_array),
        'class_counts_after': _build_class_index_counts(y_array),
        'retained_class_index': retained_class_index if retained_class_index is None else int(retained_class_index),
        'retention': float(retention),
    }
    if (
        retained_class_index is None
        or retention >= 0.999
        or retention <= 0.0
        or len(y_array) == 0
    ):
        return X_array, y_array, summary

    target_mask = y_array == int(retained_class_index)
    target_indexes = np.flatnonzero(target_mask)
    other_indexes = np.flatnonzero(~target_mask)
    if len(target_indexes) == 0 or len(other_indexes) == 0:
        return X_array, y_array, summary

    keep_count = max(1, int(round(len(target_indexes) * float(retention))))
    if keep_count >= len(target_indexes):
        return X_array, y_array, summary

    random = np.random.default_rng(int(seed))
    kept_target_indexes = np.sort(random.choice(target_indexes, size=keep_count, replace=False))
    selected_indexes = np.sort(np.concatenate([other_indexes, kept_target_indexes]))
    X_rebalanced = X_array[selected_indexes]
    y_rebalanced = y_array[selected_indexes]
    summary['rows_after'] = int(len(y_rebalanced))
    summary['class_counts_after'] = _build_class_index_counts(y_rebalanced)
    return X_rebalanced, y_rebalanced, summary


def _build_inverse_frequency_class_weights(y_values, *, num_classes: int, mode: str = 'none', exponent: float = 1.0):
    safe_mode = str(mode or 'none').strip().lower()
    if safe_mode == 'none':
        return np.ones((max(1, int(num_classes)),), dtype=float)

    y_array = np.asarray(y_values, dtype=int).reshape(-1)
    if len(y_array) == 0:
        return np.ones((max(1, int(num_classes)),), dtype=float)

    counts = np.bincount(y_array, minlength=max(1, int(num_classes))).astype(float)
    counts[counts <= 0.0] = 1.0
    safe_exponent = max(0.0, float(exponent))

    if safe_mode == 'inverse_frequency':
        weights = np.power(float(len(y_array)) / (float(num_classes) * counts), safe_exponent)
    else:
        weights = np.ones_like(counts, dtype=float)

    weights = np.where(np.isfinite(weights) & (weights > 0.0), weights, 1.0)
    return weights / max(1e-8, float(np.mean(weights)))


def _filter_stage1_gate_examples(
    X_values,
    y_values,
    target_context,
    *,
    neutral_class_index: int,
    pretrend_threshold: float,
    reversal_threshold: float,
    neutral_pretrend_ceiling: float,
    neutral_excursion_ceiling: float,
    positive_pretrend_floor: float = 0.0,
    positive_excursion_floor: float = 0.0,
):
    X_array = np.asarray(X_values, dtype=float)
    y_array = np.asarray(y_values, dtype=int).reshape(-1)
    context_array = np.asarray(target_context, dtype=float)
    if len(X_array) != len(y_array) or len(y_array) != len(context_array):
        raise ValueError('Stage 1 gate filtering requires aligned X, y, and target context arrays.')

    summary = {
        'rows_before': int(len(y_array)),
        'rows_after': int(len(y_array)),
        'class_counts_before': _build_class_index_counts(y_array),
        'class_counts_after': _build_class_index_counts(y_array),
        'neutral_class_index': int(neutral_class_index),
        'neutral_pretrend_ceiling': float(neutral_pretrend_ceiling),
        'neutral_excursion_ceiling': float(neutral_excursion_ceiling),
        'positive_pretrend_floor': float(positive_pretrend_floor),
        'positive_excursion_floor': float(positive_excursion_floor),
        'fallback_applied': False,
        'neutral_candidates_before': int(np.sum(y_array == int(neutral_class_index))),
        'neutral_candidates_after': int(np.sum(y_array == int(neutral_class_index))),
        'positive_candidates_before': int(np.sum(y_array != int(neutral_class_index))),
        'positive_candidates_after': int(np.sum(y_array != int(neutral_class_index))),
    }
    if len(y_array) == 0 or context_array.ndim != 2 or context_array.shape[1] < 3:
        return X_array, y_array, context_array, summary

    positive_mask = y_array != int(neutral_class_index)
    neutral_mask = ~positive_mask
    if not np.any(positive_mask) or not np.any(neutral_mask):
        return X_array, y_array, context_array, summary

    prev_move_atr = np.abs(context_array[:, 0])
    future_excursion_atr = np.maximum(context_array[:, 1], context_array[:, 2])
    clean_positive_mask = positive_mask
    if float(positive_pretrend_floor) > 0.0:
        clean_positive_mask = clean_positive_mask & (
            prev_move_atr >= (float(pretrend_threshold) * float(positive_pretrend_floor))
        )
    if float(positive_excursion_floor) > 0.0:
        clean_positive_mask = clean_positive_mask & (
            future_excursion_atr >= (float(reversal_threshold) * float(positive_excursion_floor))
        )
    clean_neutral_mask = (
        neutral_mask
        & (prev_move_atr <= (float(pretrend_threshold) * max(0.0, float(neutral_pretrend_ceiling))))
        & (future_excursion_atr <= (float(reversal_threshold) * max(0.0, float(neutral_excursion_ceiling))))
    )
    selected_mask = clean_positive_mask | clean_neutral_mask
    minimum_rows_after_filter = max(3, int(np.sum(clean_positive_mask)) + 1)
    if not np.any(clean_neutral_mask) or not np.any(clean_positive_mask) or np.sum(selected_mask) < minimum_rows_after_filter:
        summary['fallback_applied'] = True
        return X_array, y_array, context_array, summary

    X_filtered = X_array[selected_mask]
    y_filtered = y_array[selected_mask]
    context_filtered = context_array[selected_mask]
    summary['rows_after'] = int(len(y_filtered))
    summary['class_counts_after'] = _build_class_index_counts(y_filtered)
    summary['neutral_candidates_after'] = int(np.sum(y_filtered == int(neutral_class_index)))
    summary['positive_candidates_after'] = int(np.sum(y_filtered != int(neutral_class_index)))
    return X_filtered, y_filtered, context_filtered, summary


def _build_stage1_setup_targets(
    y_values,
    target_context,
    *,
    neutral_class_index: int,
    pretrend_threshold: float,
    reversal_threshold: float,
    positive_pretrend_floor: float,
    positive_excursion_floor: float,
    dominance_floor: float = 1.0,
    margin_floor: float = 0.0,
):
    y_array = np.asarray(y_values, dtype=int).reshape(-1)
    context_array = np.asarray(target_context, dtype=float)
    baseline_labels = (y_array != int(neutral_class_index)).astype(int)
    summary = {
        'applied': True,
        'fallback_applied': False,
        'rows': int(len(y_array)),
        'neutral_class_index': int(neutral_class_index),
        'positive_rows_before': int(np.sum(baseline_labels == 1)),
        'positive_rows_after': int(np.sum(baseline_labels == 1)),
        'positive_rate_before': float(np.mean(baseline_labels == 1)) if len(baseline_labels) else 0.0,
        'positive_rate_after': float(np.mean(baseline_labels == 1)) if len(baseline_labels) else 0.0,
        'positive_pretrend_floor': float(positive_pretrend_floor),
        'positive_excursion_floor': float(positive_excursion_floor),
        'dominance_floor': float(dominance_floor),
        'margin_floor': float(margin_floor),
    }
    if len(y_array) == 0 or context_array.ndim != 2 or context_array.shape[1] < 3:
        summary['fallback_applied'] = True
        return baseline_labels, summary

    prev_move_atr = np.abs(context_array[:, 0])
    future_upside_atr = np.asarray(context_array[:, 1], dtype=float)
    future_downside_atr = np.asarray(context_array[:, 2], dtype=float)
    winning_excursion = np.maximum(future_upside_atr, future_downside_atr)
    losing_excursion = np.minimum(future_upside_atr, future_downside_atr)
    dominance_ratio = winning_excursion / np.maximum(1e-8, losing_excursion)
    dominance_margin = winning_excursion - losing_excursion

    setup_positive_mask = (
        (baseline_labels == 1)
        & (prev_move_atr >= (float(pretrend_threshold) * max(0.0, float(positive_pretrend_floor))))
        & (winning_excursion >= (float(reversal_threshold) * max(0.0, float(positive_excursion_floor))))
        & (dominance_ratio >= max(1.0, float(dominance_floor)))
        & (dominance_margin >= max(0.0, float(margin_floor)))
    )
    setup_labels = setup_positive_mask.astype(int)
    if not np.any(setup_labels == 1) or not np.any(setup_labels == 0):
        summary['fallback_applied'] = True
        return baseline_labels, summary

    summary['positive_rows_after'] = int(np.sum(setup_labels == 1))
    summary['positive_rate_after'] = float(np.mean(setup_labels == 1))
    return setup_labels, summary


def _build_stage1_targets_for_network(
    network_id: str | None,
    y_values,
    target_context,
    *,
    neutral_class_index: int,
    config: dict,
):
    safe_network_id = str(network_id or '').strip().lower()
    baseline_labels = (np.asarray(y_values, dtype=int).reshape(-1) != int(neutral_class_index)).astype(int)
    if safe_network_id == 'candle_reversal_cnn_v8':
        return _build_stage1_setup_targets(
            y_values,
            target_context,
            neutral_class_index=neutral_class_index,
            pretrend_threshold=float(config.get('pretrendThreshold', 1.2)),
            reversal_threshold=float(config.get('reversalThreshold', 1.0)),
            positive_pretrend_floor=float(config.get('stage1SetupPretrendFloor', 1.15)),
            positive_excursion_floor=float(config.get('stage1SetupExcursionFloor', 1.1)),
            dominance_floor=float(config.get('stage1SetupDominanceFloor', 2.0)),
            margin_floor=float(config.get('stage1SetupMarginFloor', 0.0)),
        )
    summary = {
        'applied': False,
        'fallback_applied': False,
        'rows': int(len(baseline_labels)),
        'neutral_class_index': int(neutral_class_index),
        'positive_rows_before': int(np.sum(baseline_labels == 1)),
        'positive_rows_after': int(np.sum(baseline_labels == 1)),
        'positive_rate_before': float(np.mean(baseline_labels == 1)) if len(baseline_labels) else 0.0,
        'positive_rate_after': float(np.mean(baseline_labels == 1)) if len(baseline_labels) else 0.0,
    }
    return baseline_labels, summary


def _stage1_gate_profile_for_network(network_id: str | None):
    safe_network_id = str(network_id or '').strip().lower()
    if safe_network_id == 'candle_reversal_cnn_v8':
        return {
            'class_labels': {0: 'background_or_weak_context', 1: 'dominant_reversal_setup'},
            'stage_role': 'reversal_setup_gate',
        }
    return {
        'class_labels': {0: 'no_reversal', 1: 'reversal'},
        'stage_role': 'reversal_gate',
    }


def _augment_micro_cost_edge_metrics(metrics: dict | None):
    safe_metrics = dict(metrics or {})
    long_f1 = float(safe_metrics.get('class_long_edge_f1') or 0.0)
    short_f1 = float(safe_metrics.get('class_short_edge_f1') or 0.0)
    safe_metrics['directional_edge_macro_f1'] = (long_f1 + short_f1) / 2.0

    class_codes = [int(code) for code in list(safe_metrics.get('class_codes') or [])]
    confusion = np.asarray(safe_metrics.get('confusion_matrix') or [], dtype=float)
    if confusion.ndim == 2 and confusion.shape[0] == confusion.shape[1] == len(class_codes) and 0 in class_codes:
        no_edge_index = class_codes.index(0)
        actual_edge_rows = [index for index in range(len(class_codes)) if index != no_edge_index]
        predicted_edge_columns = actual_edge_rows
        true_positives = float(confusion[np.ix_(actual_edge_rows, predicted_edge_columns)].sum())
        predicted_positives = float(confusion[:, predicted_edge_columns].sum())
        actual_positives = float(confusion[actual_edge_rows, :].sum())
        tradability_precision = true_positives / predicted_positives if predicted_positives else 0.0
        tradability_recall = true_positives / actual_positives if actual_positives else 0.0
        tradability_f1 = (
            2.0 * tradability_precision * tradability_recall / (tradability_precision + tradability_recall)
            if (tradability_precision + tradability_recall)
            else 0.0
        )
        total_rows = float(confusion.sum())
        safe_metrics['tradability_precision'] = float(tradability_precision)
        safe_metrics['tradability_recall'] = float(tradability_recall)
        safe_metrics['tradability_f1'] = float(tradability_f1)
        safe_metrics['actual_tradability_rate'] = float(actual_positives / total_rows) if total_rows else 0.0
        safe_metrics['predicted_tradability_rate'] = float(predicted_positives / total_rows) if total_rows else 0.0
    else:
        safe_metrics['tradability_precision'] = 0.0
        safe_metrics['tradability_recall'] = 0.0
        safe_metrics['tradability_f1'] = 0.0
        safe_metrics['actual_tradability_rate'] = 0.0
        safe_metrics['predicted_tradability_rate'] = 0.0
    return safe_metrics


def _combine_micro_cost_edge_side_predictions(
    long_positive_scores,
    short_positive_scores,
    *,
    threshold: float,
    class_codes,
    class_labels,
):
    long_scores = np.asarray(long_positive_scores, dtype=float).reshape(-1)
    short_scores = np.asarray(short_positive_scores, dtype=float).reshape(-1)
    if long_scores.shape != short_scores.shape:
        raise ValueError('Micro cost-edge side combination requires matching long and short score shapes.')

    safe_threshold = max(0.05, min(0.95, float(threshold)))
    class_code_list = [int(code) for code in list(class_codes or [])]
    code_to_index = {int(code): index for index, code in enumerate(class_code_list)}
    neutral_class_index = code_to_index.get(0)
    short_class_index = code_to_index.get(-1)
    long_class_index = code_to_index.get(1)
    if neutral_class_index is None or short_class_index is None or long_class_index is None:
        raise ValueError('Micro cost-edge side combination requires class codes [-1, 0, 1].')

    total_rows = len(long_scores)
    raw_probabilities = np.zeros((total_rows, len(class_code_list)), dtype=float)
    raw_probabilities[:, short_class_index] = np.clip(short_scores, 0.0, 1.0)
    raw_probabilities[:, long_class_index] = np.clip(long_scores, 0.0, 1.0)
    raw_probabilities[:, neutral_class_index] = np.clip(1.0 - np.maximum(long_scores, short_scores), 0.0, 1.0)
    probability_sums = raw_probabilities.sum(axis=1, keepdims=True)
    combined_probabilities = raw_probabilities / np.where(probability_sums > 0.0, probability_sums, 1.0)

    predicted_indices = np.full((total_rows,), int(neutral_class_index), dtype=int)
    long_active = long_scores >= safe_threshold
    short_active = short_scores >= safe_threshold
    active_mask = long_active | short_active
    if np.any(active_mask):
        predicted_indices[active_mask] = np.where(
            long_scores[active_mask] >= short_scores[active_mask],
            int(long_class_index),
            int(short_class_index),
        )

    return predicted_indices, combined_probabilities


def _rank_micro_cost_edge_side_threshold_metrics(metrics: dict | None):
    safe_metrics = dict(metrics or {})
    macro_f1 = float(safe_metrics.get('macro_f1') or 0.0)
    tradability_f1 = float(safe_metrics.get('tradability_f1') or 0.0)
    directional_edge_macro_f1 = float(safe_metrics.get('directional_edge_macro_f1') or 0.0)
    balanced_accuracy = float(safe_metrics.get('balanced_accuracy') or 0.0)
    actual_tradability_rate = float(safe_metrics.get('actual_tradability_rate') or 0.0)
    predicted_tradability_rate = float(safe_metrics.get('predicted_tradability_rate') or 0.0)
    calibration_gap = abs(predicted_tradability_rate - actual_tradability_rate)
    quality_balance = (
        (2.0 * macro_f1 * tradability_f1) / (macro_f1 + tradability_f1)
        if (macro_f1 + tradability_f1)
        else 0.0
    )
    coverage_alignment = 1.0 - calibration_gap
    return (
        float(quality_balance),
        float(coverage_alignment),
        directional_edge_macro_f1,
        balanced_accuracy,
        macro_f1,
        tradability_f1,
    )


def _search_micro_cost_edge_side_threshold(
    y_event_indices,
    long_positive_scores,
    short_positive_scores,
    *,
    class_codes,
    class_labels,
):
    thresholds = np.arange(0.35, 0.801, 0.005, dtype=float)
    best_payload = None
    for threshold in thresholds:
        predicted_indices, combined_probabilities = _combine_micro_cost_edge_side_predictions(
            long_positive_scores,
            short_positive_scores,
            threshold=float(threshold),
            class_codes=class_codes,
            class_labels=class_labels,
        )
        metrics = _augment_micro_cost_edge_metrics(_evaluate_class_predictions(
            y_event_indices,
            predicted_indices,
            class_codes=class_codes,
            class_labels=class_labels,
            probabilities=combined_probabilities,
        ))
        ranking = _rank_micro_cost_edge_side_threshold_metrics(metrics)
        if best_payload is None or ranking > best_payload['ranking']:
            best_payload = {
                'threshold': float(threshold),
                'metrics': metrics,
                'predicted_indices': predicted_indices,
                'combined_probabilities': combined_probabilities,
                'ranking': ranking,
            }
    return best_payload


def _resolve_micro_cost_edge_v2_event_threshold(
    model,
    metadata,
    sequence_dataset,
    *,
    train_events: int,
    validation_events: int,
):
    safe_metadata = dict(metadata or {})
    raw_version = safe_metadata.get('selected_event_threshold_version')
    try:
        threshold_version = int(raw_version)
    except (TypeError, ValueError):
        threshold_version = 0

    selected_threshold = safe_metadata.get('selected_event_threshold')
    if selected_threshold is not None and threshold_version >= MICRO_COST_EDGE_EVENT_THRESHOLD_SELECTION_VERSION:
        return {
            'threshold': float(selected_threshold),
            'source': 'artifact_metadata',
            'version': int(threshold_version),
            'validation_metrics': None,
        }

    if validation_events <= 0:
        fallback_threshold = float(selected_threshold) if selected_threshold is not None else 0.5
        return {
            'threshold': fallback_threshold,
            'source': 'legacy_threshold_fallback' if selected_threshold is not None else 'default_threshold_fallback',
            'version': int(threshold_version),
            'validation_metrics': None,
        }

    validation_slice = slice(train_events, train_events + validation_events)
    X_validation_long = model.transform_features(sequence_dataset['X_long'][validation_slice])
    X_validation_short = model.transform_features(sequence_dataset['X_short'][validation_slice])
    validation_long_scores = model.predict_probabilities(X_validation_long)[:, 1]
    validation_short_scores = model.predict_probabilities(X_validation_short)[:, 1]
    event_code_to_index = {
        int(code): index
        for index, code in enumerate(sequence_dataset['event_class_codes'])
    }
    y_validation_event_indices = np.asarray([
        event_code_to_index[int(code)]
        for code in sequence_dataset['y_event_code'][validation_slice]
    ], dtype=int)
    validation_payload = _search_micro_cost_edge_side_threshold(
        y_validation_event_indices,
        validation_long_scores,
        validation_short_scores,
        class_codes=sequence_dataset['event_class_codes'],
        class_labels=sequence_dataset['event_class_labels'],
    )
    return {
        'threshold': float(validation_payload['threshold']),
        'source': 'validation_recalibrated_legacy_artifact',
        'version': int(MICRO_COST_EDGE_EVENT_THRESHOLD_SELECTION_VERSION),
        'validation_metrics': dict(validation_payload.get('metrics') or {}),
    }


def _predict_micro_cost_edge_v3_tradability_scores(stage1_model, X_long, X_short):
    X_long_stage1 = stage1_model.transform_features(X_long)
    X_short_stage1 = stage1_model.transform_features(X_short)
    long_scores = stage1_model.predict_probabilities(X_long_stage1)[:, 1]
    short_scores = stage1_model.predict_probabilities(X_short_stage1)[:, 1]
    return (
        0.5 * (np.asarray(long_scores, dtype=float) + np.asarray(short_scores, dtype=float)),
        X_long_stage1,
        X_short_stage1,
    )


def _combine_micro_cost_edge_hierarchical_predictions(
    tradability_scores,
    long_positive_scores,
    short_positive_scores,
    *,
    threshold: float,
    class_codes,
    class_labels,
):
    gate_scores = np.asarray(tradability_scores, dtype=float).reshape(-1)
    long_scores = np.asarray(long_positive_scores, dtype=float).reshape(-1)
    short_scores = np.asarray(short_positive_scores, dtype=float).reshape(-1)
    if gate_scores.shape != long_scores.shape or gate_scores.shape != short_scores.shape:
        raise ValueError('Hierarchical micro cost-edge combination requires matching gate and direction score shapes.')

    safe_threshold = max(0.05, min(0.95, float(threshold)))
    class_code_list = [int(code) for code in list(class_codes or [])]
    code_to_index = {int(code): index for index, code in enumerate(class_code_list)}
    neutral_class_index = code_to_index.get(0)
    short_class_index = code_to_index.get(-1)
    long_class_index = code_to_index.get(1)
    if neutral_class_index is None or short_class_index is None or long_class_index is None:
        raise ValueError('Hierarchical micro cost-edge combination requires class codes [-1, 0, 1].')

    safe_gate_scores = np.clip(gate_scores, 0.0, 1.0)
    safe_long_scores = np.clip(long_scores, 0.0, 1.0)
    safe_short_scores = np.clip(short_scores, 0.0, 1.0)
    direction_strength = np.column_stack([safe_short_scores, safe_long_scores])
    direction_sums = direction_strength.sum(axis=1, keepdims=True)
    direction_shares = np.divide(
        direction_strength,
        np.where(direction_sums > 0.0, direction_sums, 1.0),
    )
    zero_direction_mask = np.asarray(direction_sums.reshape(-1) <= 0.0, dtype=bool)
    if np.any(zero_direction_mask):
        direction_shares[zero_direction_mask] = 0.5

    total_rows = len(safe_gate_scores)
    raw_probabilities = np.zeros((total_rows, len(class_code_list)), dtype=float)
    raw_probabilities[:, neutral_class_index] = np.clip(1.0 - safe_gate_scores, 0.0, 1.0)
    raw_probabilities[:, short_class_index] = safe_gate_scores * direction_shares[:, 0]
    raw_probabilities[:, long_class_index] = safe_gate_scores * direction_shares[:, 1]
    probability_sums = raw_probabilities.sum(axis=1, keepdims=True)
    combined_probabilities = raw_probabilities / np.where(probability_sums > 0.0, probability_sums, 1.0)

    predicted_indices = np.full((total_rows,), int(neutral_class_index), dtype=int)
    active_mask = safe_gate_scores >= safe_threshold
    if np.any(active_mask):
        predicted_indices[active_mask] = np.where(
            safe_long_scores[active_mask] >= safe_short_scores[active_mask],
            int(long_class_index),
            int(short_class_index),
        )

    return predicted_indices, combined_probabilities


def _search_micro_cost_edge_hierarchical_threshold(
    y_event_indices,
    tradability_scores,
    long_positive_scores,
    short_positive_scores,
    *,
    class_codes,
    class_labels,
):
    thresholds = np.arange(0.20, 0.801, 0.005, dtype=float)
    best_payload = None
    for threshold in thresholds:
        predicted_indices, combined_probabilities = _combine_micro_cost_edge_hierarchical_predictions(
            tradability_scores,
            long_positive_scores,
            short_positive_scores,
            threshold=float(threshold),
            class_codes=class_codes,
            class_labels=class_labels,
        )
        metrics = _augment_micro_cost_edge_metrics(_evaluate_class_predictions(
            y_event_indices,
            predicted_indices,
            class_codes=class_codes,
            class_labels=class_labels,
            probabilities=combined_probabilities,
        ))
        ranking = _rank_micro_cost_edge_side_threshold_metrics(metrics)
        if best_payload is None or ranking > best_payload['ranking']:
            best_payload = {
                'threshold': float(threshold),
                'metrics': metrics,
                'predicted_indices': predicted_indices,
                'combined_probabilities': combined_probabilities,
                'ranking': ranking,
            }
    return best_payload


def _resolve_micro_cost_edge_v3_event_threshold(
    stage1_model,
    stage2_model,
    manifest,
    sequence_dataset,
    *,
    train_events: int,
    validation_events: int,
):
    safe_manifest = dict(manifest or {})
    raw_version = safe_manifest.get('selected_event_threshold_version')
    try:
        threshold_version = int(raw_version)
    except (TypeError, ValueError):
        threshold_version = 0

    selected_threshold = safe_manifest.get('selected_event_threshold')
    if selected_threshold is not None and threshold_version >= MICRO_COST_EDGE_HIERARCHICAL_EVENT_THRESHOLD_SELECTION_VERSION:
        return {
            'threshold': float(selected_threshold),
            'source': 'artifact_metadata',
            'version': int(threshold_version),
            'validation_metrics': None,
        }

    if validation_events <= 0:
        fallback_threshold = float(selected_threshold) if selected_threshold is not None else 0.5
        return {
            'threshold': fallback_threshold,
            'source': 'legacy_threshold_fallback' if selected_threshold is not None else 'default_threshold_fallback',
            'version': int(threshold_version),
            'validation_metrics': None,
        }

    validation_slice = slice(train_events, train_events + validation_events)
    validation_tradability_scores, _, _ = _predict_micro_cost_edge_v3_tradability_scores(
        stage1_model,
        sequence_dataset['X_long'][validation_slice],
        sequence_dataset['X_short'][validation_slice],
    )
    X_validation_long_stage2 = stage2_model.transform_features(sequence_dataset['X_long'][validation_slice])
    X_validation_short_stage2 = stage2_model.transform_features(sequence_dataset['X_short'][validation_slice])
    validation_long_scores = stage2_model.predict_probabilities(X_validation_long_stage2)[:, 1]
    validation_short_scores = stage2_model.predict_probabilities(X_validation_short_stage2)[:, 1]
    event_code_to_index = {
        int(code): index
        for index, code in enumerate(sequence_dataset['event_class_codes'])
    }
    y_validation_event_indices = np.asarray([
        event_code_to_index[int(code)]
        for code in sequence_dataset['y_event_code'][validation_slice]
    ], dtype=int)
    validation_payload = _search_micro_cost_edge_hierarchical_threshold(
        y_validation_event_indices,
        validation_tradability_scores,
        validation_long_scores,
        validation_short_scores,
        class_codes=sequence_dataset['event_class_codes'],
        class_labels=sequence_dataset['event_class_labels'],
    )
    return {
        'threshold': float(validation_payload['threshold']),
        'source': 'validation_recalibrated_legacy_artifact',
        'version': int(MICRO_COST_EDGE_HIERARCHICAL_EVENT_THRESHOLD_SELECTION_VERSION),
        'validation_metrics': dict(validation_payload.get('metrics') or {}),
    }


def _save_hierarchical_micro_cost_edge_artifact(
    model_base_path: str,
    *,
    stage1_model: TemporalConvolutionalClassifier,
    stage2_model: TemporalConvolutionalClassifier,
    manifest: dict,
):
    artifact_path = Path(f'{model_base_path}.zip')
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='micro_cost_edge_v3_', dir=str(artifact_path.parent)) as temp_dir:
        temp_path = Path(temp_dir)
        stage1_path = Path(stage1_model.save(str(temp_path / 'stage1_model'), metadata=manifest.get('stage1_model_metadata') or {}))
        stage2_path = Path(stage2_model.save(str(temp_path / 'stage2_model'), metadata=manifest.get('stage2_model_metadata') or {}))
        manifest_path = temp_path / 'manifest.json'
        manifest_path.write_text(
            json.dumps(manifest or {}, ensure_ascii=True, allow_nan=False, indent=2),
            encoding='utf-8',
        )
        with zipfile.ZipFile(artifact_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(stage1_path, arcname='stage1_model.npz')
            archive.write(stage2_path, arcname='stage2_model.npz')
            archive.write(manifest_path, arcname='manifest.json')
    return str(artifact_path)


def _load_hierarchical_micro_cost_edge_artifact(model_path: str):
    safe_path = Path(str(model_path or '')).expanduser()
    if not safe_path.exists():
        raise ValueError('No trained hierarchical micro cost-edge model is available to test.')

    with tempfile.TemporaryDirectory(prefix='micro_cost_edge_v3_load_') as temp_dir:
        temp_path = Path(temp_dir)
        with zipfile.ZipFile(safe_path, 'r') as archive:
            archive.extractall(temp_path)
        manifest_path = temp_path / 'manifest.json'
        if not manifest_path.exists():
            raise ValueError('Hierarchical micro cost-edge artifact is missing manifest.json.')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        stage1_model, stage1_metadata = TemporalConvolutionalClassifier.load(temp_path / 'stage1_model.npz')
        stage2_model, stage2_metadata = TemporalConvolutionalClassifier.load(temp_path / 'stage2_model.npz')

    return {
        'manifest': manifest,
        'stage1_model': stage1_model,
        'stage1_metadata': stage1_metadata,
        'stage2_model': stage2_model,
        'stage2_metadata': stage2_metadata,
    }


def _evaluate_class_predictions(y_true, predicted_indices, *, class_codes, class_labels, probabilities=None):
    class_code_list = [int(code) for code in list(class_codes or [])]
    if not class_code_list:
        raise ValueError('Class prediction evaluation requires at least one class code.')

    y_array = np.asarray(y_true, dtype=int).reshape(-1)
    predicted_array = np.asarray(predicted_indices, dtype=int).reshape(-1)
    if len(y_array) != len(predicted_array):
        raise ValueError('y_true and predicted_indices must have the same length.')

    class_count = len(class_code_list)
    if len(y_array) == 0:
        return {
            'accuracy': 0.0,
            'macro_f1': 0.0,
            'balanced_accuracy': 0.0,
            'directional_accuracy': 0.0,
            'mean_confidence': 0.0,
            'actual_transition_rate': 0.0,
            'predicted_transition_rate': 0.0,
            'class_codes': class_code_list,
            'confusion_matrix': [[0 for _ in class_code_list] for _ in class_code_list],
        }

    confusion = np.zeros((class_count, class_count), dtype=int)
    for actual_index, predicted_index in zip(y_array, predicted_array):
        if 0 <= int(actual_index) < class_count and 0 <= int(predicted_index) < class_count:
            confusion[int(actual_index), int(predicted_index)] += 1

    actual_codes = np.asarray([class_code_list[int(index)] for index in y_array], dtype=float)
    predicted_codes = np.asarray([class_code_list[int(index)] for index in predicted_array], dtype=float)
    accuracy = float(np.mean(predicted_array == y_array))
    directional_accuracy = float(np.mean(np.sign(actual_codes) == np.sign(predicted_codes)))
    actual_transition_rate = float(np.mean(y_array[1:] != y_array[:-1])) if len(y_array) > 1 else 0.0
    predicted_transition_rate = float(np.mean(predicted_array[1:] != predicted_array[:-1])) if len(predicted_array) > 1 else 0.0

    probability_matrix = None
    if probabilities is not None:
        probability_matrix = np.asarray(probabilities, dtype=float)
        if probability_matrix.ndim == 2 and probability_matrix.shape[0] == len(y_array):
            if probability_matrix.shape[1] != class_count:
                probability_matrix = None
        else:
            probability_matrix = None
    mean_confidence = float(np.mean(np.max(probability_matrix, axis=1))) if probability_matrix is not None and len(probability_matrix) else 0.0

    precision_values = []
    recall_values = []
    f1_values = []
    metrics = {
        'accuracy': accuracy,
        'directional_accuracy': directional_accuracy,
        'mean_confidence': mean_confidence,
        'actual_transition_rate': actual_transition_rate,
        'predicted_transition_rate': predicted_transition_rate,
        'class_codes': class_code_list,
        'confusion_matrix': confusion.tolist(),
    }
    safe_labels = {
        int(code): str(class_labels.get(code) or class_labels.get(str(code)) or code)
        for code in class_code_list
    }
    for class_index, class_code in enumerate(class_code_list):
        label_slug = safe_labels[int(class_code)].strip().lower().replace(' ', '_')
        true_positives = float(confusion[class_index, class_index])
        predicted_positives = float(confusion[:, class_index].sum())
        actual_positives = float(confusion[class_index, :].sum())
        precision = true_positives / predicted_positives if predicted_positives else 0.0
        recall = true_positives / actual_positives if actual_positives else 0.0
        f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        precision_values.append(precision)
        recall_values.append(recall)
        f1_values.append(f1)
        metrics[f'class_{label_slug}_precision'] = float(precision)
        metrics[f'class_{label_slug}_recall'] = float(recall)
        metrics[f'class_{label_slug}_f1'] = float(f1)
        metrics[f'class_{label_slug}_support'] = int(actual_positives)

    metrics['balanced_accuracy'] = float(np.mean(recall_values)) if recall_values else 0.0
    metrics['macro_f1'] = float(np.mean(f1_values)) if f1_values else 0.0
    return metrics


def _combine_hierarchical_reversal_predictions(
    reversal_probabilities,
    direction_probabilities,
    *,
    threshold: float,
    neutral_class_index: int,
    bearish_class_index: int,
    bullish_class_index: int,
):
    reversal_matrix = np.asarray(reversal_probabilities, dtype=float)
    direction_matrix = np.asarray(direction_probabilities, dtype=float)
    if reversal_matrix.ndim != 2 or reversal_matrix.shape[1] < 2:
        raise ValueError('Hierarchical reversal combination requires binary reversal probabilities.')
    if direction_matrix.ndim != 2 or direction_matrix.shape[1] < 2:
        raise ValueError('Hierarchical reversal combination requires binary direction probabilities.')
    if reversal_matrix.shape[0] != direction_matrix.shape[0]:
        raise ValueError('Hierarchical reversal combination requires matching row counts.')

    safe_threshold = max(0.05, min(0.95, float(threshold)))
    reversal_scores = reversal_matrix[:, 1]
    bearish_scores = direction_matrix[:, 0]
    bullish_scores = direction_matrix[:, 1]

    total_rows = reversal_matrix.shape[0]
    class_count = max(neutral_class_index, bearish_class_index, bullish_class_index) + 1
    combined_probabilities = np.zeros((total_rows, class_count), dtype=float)
    combined_probabilities[:, neutral_class_index] = np.clip(1.0 - reversal_scores, 0.0, 1.0)
    combined_probabilities[:, bearish_class_index] = np.clip(reversal_scores * bearish_scores, 0.0, 1.0)
    combined_probabilities[:, bullish_class_index] = np.clip(reversal_scores * bullish_scores, 0.0, 1.0)

    predicted_indices = np.full((total_rows,), int(neutral_class_index), dtype=int)
    active_mask = reversal_scores >= safe_threshold
    if np.any(active_mask):
        predicted_indices[active_mask] = np.where(
            bearish_scores[active_mask] >= bullish_scores[active_mask],
            int(bearish_class_index),
            int(bullish_class_index),
        )

    return predicted_indices, combined_probabilities


def _search_hierarchical_reversal_threshold(
    y_true,
    reversal_probabilities,
    direction_probabilities,
    *,
    class_codes,
    class_labels,
    neutral_class_index: int,
    bearish_class_index: int,
    bullish_class_index: int,
):
    thresholds = np.arange(0.20, 0.801, 0.02, dtype=float)
    best_payload = None
    for threshold in thresholds:
        predicted_indices, combined_probabilities = _combine_hierarchical_reversal_predictions(
            reversal_probabilities,
            direction_probabilities,
            threshold=float(threshold),
            neutral_class_index=neutral_class_index,
            bearish_class_index=bearish_class_index,
            bullish_class_index=bullish_class_index,
        )
        metrics = _evaluate_class_predictions(
            y_true,
            predicted_indices,
            class_codes=class_codes,
            class_labels=class_labels,
            probabilities=combined_probabilities,
        )
        ranking = (
            float(metrics.get('macro_f1') or 0.0),
            float(metrics.get('balanced_accuracy') or 0.0),
            float(metrics.get('directional_accuracy') or 0.0),
            -abs(float(metrics.get('predicted_transition_rate') or 0.0) - float(metrics.get('actual_transition_rate') or 0.0)),
        )
        if best_payload is None or ranking > best_payload['ranking']:
            best_payload = {
                'threshold': float(threshold),
                'metrics': metrics,
                'predicted_indices': predicted_indices,
                'combined_probabilities': combined_probabilities,
                'ranking': ranking,
            }

    return best_payload


def _combine_dual_head_reversal_predictions(
    bearish_probabilities,
    bullish_probabilities,
    *,
    bearish_threshold: float,
    bullish_threshold: float,
    neutral_class_index: int,
    bearish_class_index: int,
    bullish_class_index: int,
):
    bearish_matrix = np.asarray(bearish_probabilities, dtype=float)
    bullish_matrix = np.asarray(bullish_probabilities, dtype=float)
    if bearish_matrix.ndim != 2 or bearish_matrix.shape[1] < 2:
        raise ValueError('Dual-head reversal combination requires binary bearish probabilities.')
    if bullish_matrix.ndim != 2 or bullish_matrix.shape[1] < 2:
        raise ValueError('Dual-head reversal combination requires binary bullish probabilities.')
    if bearish_matrix.shape[0] != bullish_matrix.shape[0]:
        raise ValueError('Dual-head reversal combination requires matching row counts.')

    safe_bearish_threshold = max(0.05, min(0.95, float(bearish_threshold)))
    safe_bullish_threshold = max(0.05, min(0.95, float(bullish_threshold)))
    bearish_scores = bearish_matrix[:, 1]
    bullish_scores = bullish_matrix[:, 1]

    total_rows = bearish_matrix.shape[0]
    class_count = max(neutral_class_index, bearish_class_index, bullish_class_index) + 1
    raw_probabilities = np.zeros((total_rows, class_count), dtype=float)
    raw_probabilities[:, bearish_class_index] = np.clip(bearish_scores, 0.0, 1.0)
    raw_probabilities[:, bullish_class_index] = np.clip(bullish_scores, 0.0, 1.0)
    raw_probabilities[:, neutral_class_index] = np.clip(1.0 - np.maximum(bearish_scores, bullish_scores), 0.0, 1.0)
    probability_sums = raw_probabilities.sum(axis=1, keepdims=True)
    combined_probabilities = raw_probabilities / np.where(probability_sums > 0.0, probability_sums, 1.0)

    predicted_indices = np.full((total_rows,), int(neutral_class_index), dtype=int)
    bearish_active = bearish_scores >= safe_bearish_threshold
    bullish_active = bullish_scores >= safe_bullish_threshold
    active_mask = bearish_active | bullish_active
    if np.any(active_mask):
        predicted_indices[active_mask] = np.where(
            bearish_scores[active_mask] >= bullish_scores[active_mask],
            int(bearish_class_index),
            int(bullish_class_index),
        )

    return predicted_indices, combined_probabilities


def _search_dual_head_reversal_thresholds(
    y_true,
    bearish_probabilities,
    bullish_probabilities,
    *,
    class_codes,
    class_labels,
    neutral_class_index: int,
    bearish_class_index: int,
    bullish_class_index: int,
):
    thresholds = np.arange(0.20, 0.801, 0.02, dtype=float)
    best_payload = None
    for bearish_threshold in thresholds:
        for bullish_threshold in thresholds:
            predicted_indices, combined_probabilities = _combine_dual_head_reversal_predictions(
                bearish_probabilities,
                bullish_probabilities,
                bearish_threshold=float(bearish_threshold),
                bullish_threshold=float(bullish_threshold),
                neutral_class_index=neutral_class_index,
                bearish_class_index=bearish_class_index,
                bullish_class_index=bullish_class_index,
            )
            metrics = _evaluate_class_predictions(
                y_true,
                predicted_indices,
                class_codes=class_codes,
                class_labels=class_labels,
                probabilities=combined_probabilities,
            )
            ranking = (
                float(metrics.get('macro_f1') or 0.0),
                float(metrics.get('balanced_accuracy') or 0.0),
                float(metrics.get('directional_accuracy') or 0.0),
                -abs(float(metrics.get('predicted_transition_rate') or 0.0) - float(metrics.get('actual_transition_rate') or 0.0)),
            )
            if best_payload is None or ranking > best_payload['ranking']:
                best_payload = {
                    'bearish_threshold': float(bearish_threshold),
                    'bullish_threshold': float(bullish_threshold),
                    'metrics': metrics,
                    'predicted_indices': predicted_indices,
                    'combined_probabilities': combined_probabilities,
                    'ranking': ranking,
                }

    return best_payload


def _combine_tri_head_reversal_predictions(
    bearish_probabilities,
    neutral_probabilities,
    bullish_probabilities,
    *,
    bearish_threshold: float,
    neutral_threshold: float,
    bullish_threshold: float,
    neutral_class_index: int,
    bearish_class_index: int,
    bullish_class_index: int,
):
    bearish_matrix = np.asarray(bearish_probabilities, dtype=float)
    neutral_matrix = np.asarray(neutral_probabilities, dtype=float)
    bullish_matrix = np.asarray(bullish_probabilities, dtype=float)
    if bearish_matrix.ndim != 2 or bearish_matrix.shape[1] < 2:
        raise ValueError('Tri-head reversal combination requires binary bearish probabilities.')
    if neutral_matrix.ndim != 2 or neutral_matrix.shape[1] < 2:
        raise ValueError('Tri-head reversal combination requires binary neutral probabilities.')
    if bullish_matrix.ndim != 2 or bullish_matrix.shape[1] < 2:
        raise ValueError('Tri-head reversal combination requires binary bullish probabilities.')
    if not (bearish_matrix.shape[0] == neutral_matrix.shape[0] == bullish_matrix.shape[0]):
        raise ValueError('Tri-head reversal combination requires matching row counts.')

    safe_bearish_threshold = max(0.05, min(0.95, float(bearish_threshold)))
    safe_neutral_threshold = max(0.05, min(0.95, float(neutral_threshold)))
    safe_bullish_threshold = max(0.05, min(0.95, float(bullish_threshold)))
    bearish_scores = bearish_matrix[:, 1]
    neutral_scores = neutral_matrix[:, 1]
    bullish_scores = bullish_matrix[:, 1]

    total_rows = bearish_matrix.shape[0]
    class_count = max(neutral_class_index, bearish_class_index, bullish_class_index) + 1
    strength_matrix = np.zeros((total_rows, class_count), dtype=float)
    strength_matrix[:, bearish_class_index] = bearish_scores / safe_bearish_threshold
    strength_matrix[:, neutral_class_index] = neutral_scores / safe_neutral_threshold
    strength_matrix[:, bullish_class_index] = bullish_scores / safe_bullish_threshold
    strength_sums = strength_matrix.sum(axis=1, keepdims=True)
    combined_probabilities = strength_matrix / np.where(strength_sums > 0.0, strength_sums, 1.0)

    predicted_indices = np.full((total_rows,), int(neutral_class_index), dtype=int)
    active_mask = np.max(strength_matrix, axis=1) >= 1.0
    if np.any(active_mask):
        predicted_indices[active_mask] = np.argmax(strength_matrix[active_mask], axis=1).astype(int)

    return predicted_indices, combined_probabilities


def _search_tri_head_reversal_thresholds(
    y_true,
    bearish_probabilities,
    neutral_probabilities,
    bullish_probabilities,
    *,
    class_codes,
    class_labels,
    neutral_class_index: int,
    bearish_class_index: int,
    bullish_class_index: int,
):
    thresholds = np.arange(0.20, 0.801, 0.02, dtype=float)
    best_payload = None
    for bearish_threshold in thresholds:
        for neutral_threshold in thresholds:
            for bullish_threshold in thresholds:
                predicted_indices, combined_probabilities = _combine_tri_head_reversal_predictions(
                    bearish_probabilities,
                    neutral_probabilities,
                    bullish_probabilities,
                    bearish_threshold=float(bearish_threshold),
                    neutral_threshold=float(neutral_threshold),
                    bullish_threshold=float(bullish_threshold),
                    neutral_class_index=neutral_class_index,
                    bearish_class_index=bearish_class_index,
                    bullish_class_index=bullish_class_index,
                )
                metrics = _evaluate_class_predictions(
                    y_true,
                    predicted_indices,
                    class_codes=class_codes,
                    class_labels=class_labels,
                    probabilities=combined_probabilities,
                )
                ranking = (
                    float(metrics.get('macro_f1') or 0.0),
                    float(metrics.get('balanced_accuracy') or 0.0),
                    float(metrics.get('class_no_reversal_recall') or 0.0),
                    float(metrics.get('directional_accuracy') or 0.0),
                    -abs(float(metrics.get('predicted_transition_rate') or 0.0) - float(metrics.get('actual_transition_rate') or 0.0)),
                )
                if best_payload is None or ranking > best_payload['ranking']:
                    best_payload = {
                        'bearish_threshold': float(bearish_threshold),
                        'neutral_threshold': float(neutral_threshold),
                        'bullish_threshold': float(bullish_threshold),
                        'metrics': metrics,
                        'predicted_indices': predicted_indices,
                        'combined_probabilities': combined_probabilities,
                        'ranking': ranking,
                    }

    return best_payload


def _save_hierarchical_candle_reversal_artifact(
    model_base_path: str,
    *,
    stage1_model: TemporalConvolutionalClassifier,
    stage2_model: TemporalConvolutionalClassifier,
    manifest: dict,
):
    artifact_path = Path(f'{model_base_path}.zip')
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candle_reversal_v3_', dir=str(artifact_path.parent)) as temp_dir:
        temp_path = Path(temp_dir)
        stage1_path = Path(stage1_model.save(str(temp_path / 'stage1_model'), metadata=manifest.get('stage1_model_metadata') or {}))
        stage2_path = Path(stage2_model.save(str(temp_path / 'stage2_model'), metadata=manifest.get('stage2_model_metadata') or {}))
        manifest_path = temp_path / 'manifest.json'
        manifest_path.write_text(
            json.dumps(manifest or {}, ensure_ascii=True, allow_nan=False, indent=2),
            encoding='utf-8',
        )
        with zipfile.ZipFile(artifact_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(stage1_path, arcname='stage1_model.npz')
            archive.write(stage2_path, arcname='stage2_model.npz')
            archive.write(manifest_path, arcname='manifest.json')
    return str(artifact_path)


def _load_hierarchical_candle_reversal_artifact(model_path: str):
    safe_path = Path(str(model_path or '')).expanduser()
    if not safe_path.exists():
        raise ValueError('No trained hierarchical candle reversal model is available to test.')

    with tempfile.TemporaryDirectory(prefix='candle_reversal_v3_load_') as temp_dir:
        temp_path = Path(temp_dir)
        with zipfile.ZipFile(safe_path, 'r') as archive:
            archive.extractall(temp_path)
        manifest_path = temp_path / 'manifest.json'
        if not manifest_path.exists():
            raise ValueError('Hierarchical candle reversal artifact is missing manifest.json.')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        stage1_model, stage1_metadata = TemporalConvolutionalClassifier.load(temp_path / 'stage1_model.npz')
        stage2_model, stage2_metadata = TemporalConvolutionalClassifier.load(temp_path / 'stage2_model.npz')

    return {
        'manifest': manifest,
        'stage1_model': stage1_model,
        'stage1_metadata': stage1_metadata,
        'stage2_model': stage2_model,
        'stage2_metadata': stage2_metadata,
    }


def _save_tri_head_candle_reversal_artifact(
    model_base_path: str,
    *,
    bearish_head_model: TemporalConvolutionalClassifier,
    neutral_head_model: TemporalConvolutionalClassifier,
    bullish_head_model: TemporalConvolutionalClassifier,
    manifest: dict,
):
    artifact_path = Path(f'{model_base_path}.zip')
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candle_reversal_v10_', dir=str(artifact_path.parent)) as temp_dir:
        temp_path = Path(temp_dir)
        bearish_path = Path(bearish_head_model.save(str(temp_path / 'bearish_head_model'), metadata=manifest.get('bearish_head_model_metadata') or {}))
        neutral_path = Path(neutral_head_model.save(str(temp_path / 'neutral_head_model'), metadata=manifest.get('neutral_head_model_metadata') or {}))
        bullish_path = Path(bullish_head_model.save(str(temp_path / 'bullish_head_model'), metadata=manifest.get('bullish_head_model_metadata') or {}))
        manifest_path = temp_path / 'manifest.json'
        manifest_path.write_text(
            json.dumps(manifest or {}, ensure_ascii=True, allow_nan=False, indent=2),
            encoding='utf-8',
        )
        with zipfile.ZipFile(artifact_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(bearish_path, arcname='bearish_head_model.npz')
            archive.write(neutral_path, arcname='neutral_head_model.npz')
            archive.write(bullish_path, arcname='bullish_head_model.npz')
            archive.write(manifest_path, arcname='manifest.json')
    return str(artifact_path)


def _load_tri_head_candle_reversal_artifact(model_path: str):
    safe_path = Path(str(model_path or '')).expanduser()
    if not safe_path.exists():
        raise ValueError('No trained tri-head candle reversal model is available to test.')

    with tempfile.TemporaryDirectory(prefix='candle_reversal_v10_load_') as temp_dir:
        temp_path = Path(temp_dir)
        with zipfile.ZipFile(safe_path, 'r') as archive:
            archive.extractall(temp_path)
        manifest_path = temp_path / 'manifest.json'
        if not manifest_path.exists():
            raise ValueError('Tri-head candle reversal artifact is missing manifest.json.')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        bearish_head_model, bearish_head_metadata = TemporalConvolutionalClassifier.load(temp_path / 'bearish_head_model.npz')
        neutral_head_model, neutral_head_metadata = TemporalConvolutionalClassifier.load(temp_path / 'neutral_head_model.npz')
        bullish_head_model, bullish_head_metadata = TemporalConvolutionalClassifier.load(temp_path / 'bullish_head_model.npz')

    return {
        'manifest': manifest,
        'bearish_head_model': bearish_head_model,
        'bearish_head_metadata': bearish_head_metadata,
        'neutral_head_model': neutral_head_model,
        'neutral_head_metadata': neutral_head_metadata,
        'bullish_head_model': bullish_head_model,
        'bullish_head_metadata': bullish_head_metadata,
    }


def _candle_reversal_variant_suffix(network_id: str | None, fallback: str = 'v3'):
    raw_network_id = str(network_id or '').strip().lower()
    prefix = 'candle_reversal_cnn_'
    if raw_network_id.startswith(prefix):
        suffix = raw_network_id[len(prefix):].strip()
        return suffix or fallback
    return fallback


def _candle_reversal_phase_label(network_id: str | None, stage_label: str, fallback: str = 'v3'):
    return f'Candle reversal {_candle_reversal_variant_suffix(network_id, fallback=fallback)} · {stage_label}'


def _candle_reversal_artifact_type(network_id: str | None, fallback: str = 'v3'):
    return f'hierarchical_candle_reversal_{_candle_reversal_variant_suffix(network_id, fallback=fallback)}'


def run_basic_ff_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing feed-forward feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    frame = pipeline.build_dataset()
    log(f'Feed-forward dataset built with {len(frame)} rows.', progress=0.16)
    splits = _split_supervised_frame(frame, config['validationSplit'], config['testSplit'])
    split_sizes = splits['sizes']
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    feature_columns = list(pipeline.feature_columns)
    X_train = splits['train'][feature_columns].to_numpy(dtype=float)
    y_train = splits['train']['target_signal_score'].to_numpy(dtype=float)
    X_validation = splits['validation'][feature_columns].to_numpy(dtype=float)
    y_validation = splits['validation']['target_signal_score'].to_numpy(dtype=float)

    training_config = _build_supervised_training_config(config)
    model = BasicFeedForwardRegressor(
        input_size=len(feature_columns),
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = feature_columns
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training feed-forward regressor.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Feed-forward training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': feature_columns,
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
        },
    )
    score = float(validation_metrics.get('signal_directional_accuracy') or 0.0)
    log(f'Validation finished with signal accuracy {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': len(frame),
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': feature_columns,
            'feature_size': len(feature_columns),
            'target_horizon': int(config.get('targetHorizon', 1)),
            'target_mode': str(config.get('targetMode', 'excursion_signal') or 'excursion_signal').strip().lower(),
            'target_std_window': int(config.get('targetStdWindow', 20)),
            'target_std_threshold': float(config.get('targetStdThreshold', 1.0)),
            'hidden_layers': training_config.hidden_layers,
        },
        'score': score,
    }


def run_basic_ff_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing feed-forward test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    frame = pipeline.build_dataset()
    splits = _split_supervised_frame(frame, config['validationSplit'], config['testSplit'])
    split_sizes = splits['sizes']
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = BasicFeedForwardRegressor.load(model_path)
    feature_columns = list(metadata.get('feature_columns') or pipeline.feature_columns)
    X_test = splits['test'][feature_columns].to_numpy(dtype=float)
    y_test = splits['test']['target_signal_score'].to_numpy(dtype=float)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(feature_columns)
    metrics['target_horizon'] = int(config.get('targetHorizon', 1))
    metrics['target_mode'] = str(config.get('targetMode', 'excursion_signal') or 'excursion_signal').strip().lower()
    metrics['target_std_window'] = int(config.get('targetStdWindow', 20))
    metrics['target_std_threshold'] = float(config.get('targetStdThreshold', 1.0))
    score = float(metrics.get('signal_directional_accuracy') or 0.0)
    log(f'Test finished with signal accuracy {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_temporal_cnn_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing temporal CNN feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_sequence_dataset()
    log(f'Temporal sequence dataset built with {sequence_dataset["rows"]} rows.', progress=0.16)

    frame = {
        'X': sequence_dataset['X'],
        'y': sequence_dataset['y'],
        'feature_columns': sequence_dataset['feature_columns'],
        'observation_window': sequence_dataset['observation_window'],
        'rows': sequence_dataset['rows'],
    }
    total_rows = int(frame['rows'])
    if total_rows < 120:
        raise ValueError('Temporal CNN training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = frame['X'][:train_rows]
    y_train = frame['y'][:train_rows]
    X_validation = frame['X'][train_rows:train_rows + validation_rows]
    y_validation = frame['y'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    model = TemporalConvolutionalRegressor(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(frame['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training temporal CNN regressor.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Temporal CNN training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(frame['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': frame['observation_window'],
        },
    )
    score = float(validation_metrics.get('signal_directional_accuracy') or 0.0)
    log(f'Validation finished with signal accuracy {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(frame['feature_columns']),
            'feature_size': len(frame['feature_columns']),
            'observation_window': int(frame['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 1)),
            'target_mode': str(config.get('targetMode', 'excursion_signal') or 'excursion_signal').strip().lower(),
            'target_std_window': int(config.get('targetStdWindow', 20)),
            'target_std_threshold': float(config.get('targetStdThreshold', 1.0)),
            'hidden_layers': training_config.hidden_layers,
        },
        'score': score,
    }


def run_temporal_cnn_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing temporal CNN test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalRegressor.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_columns'] = list(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 1))
    metrics['target_mode'] = str(config.get('targetMode', 'excursion_signal') or 'excursion_signal').strip().lower()
    metrics['target_std_window'] = int(config.get('targetStdWindow', 20))
    metrics['target_std_threshold'] = float(config.get('targetStdThreshold', 1.0))
    score = float(metrics.get('signal_directional_accuracy') or 0.0)
    log(f'Test finished with signal accuracy {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_neural_market_regime_cnn_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing neural market regime feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_regime_sequence_dataset()
    log(f'Neural market regime dataset built with {sequence_dataset["rows"]} rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('Neural market regime training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = sequence_dataset['X'][:train_rows]
    y_train = sequence_dataset['y_class'][:train_rows]
    X_validation = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    model = TemporalConvolutionalClassifier(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training neural market regime classifier.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Neural market regime training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
        },
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 1)),
            'target_mode': str(config.get('targetMode', 'future_regime_classification') or 'future_regime_classification').strip().lower(),
            'target_regime_compression_threshold': float(config.get('targetRegimeCompressionThreshold', 0.9)),
            'target_regime_volatility_threshold': float(config.get('targetRegimeVolatilityThreshold', 2.2)),
            'target_regime_trend_efficiency_threshold': float(config.get('targetRegimeTrendEfficiencyThreshold', 0.55)),
            'target_regime_directional_move_threshold': float(config.get('targetRegimeDirectionalMoveThreshold', 0.35)),
            'target_regime_directional_dominance_threshold': float(config.get('targetRegimeDirectionalDominanceThreshold', 0.6)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
        },
        'score': score,
    }


def run_neural_market_regime_cnn_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing neural market regime test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_regime_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 1))
    metrics['target_mode'] = str(config.get('targetMode', 'future_regime_classification') or 'future_regime_classification').strip().lower()
    metrics['target_regime_compression_threshold'] = float(config.get('targetRegimeCompressionThreshold', 0.9))
    metrics['target_regime_volatility_threshold'] = float(config.get('targetRegimeVolatilityThreshold', 2.2))
    metrics['target_regime_trend_efficiency_threshold'] = float(config.get('targetRegimeTrendEfficiencyThreshold', 0.55))
    metrics['target_regime_directional_move_threshold'] = float(config.get('targetRegimeDirectionalMoveThreshold', 0.35))
    metrics['target_regime_directional_dominance_threshold'] = float(config.get('targetRegimeDirectionalDominanceThreshold', 0.6))
    score = float(metrics.get('macro_f1') or 0.0)
    log(f'Test finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_ema_low_adx_setup_quality_cnn_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing EMA low-ADX setup-quality feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_ema_low_adx_setup_quality_sequence_dataset()
    log(f'EMA low-ADX setup-quality dataset built with {sequence_dataset["rows"]} candidate rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('EMA low-ADX setup-quality training requires at least 120 candidate sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = sequence_dataset['X'][:train_rows]
    y_train = sequence_dataset['y_class'][:train_rows]
    X_validation = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    neutral_class_index = None
    if 0 in list(sequence_dataset['class_codes']):
        neutral_class_index = list(sequence_dataset['class_codes']).index(0)
    X_train, y_train, rebalance_summary = _rebalance_sequence_classes(
        X_train,
        y_train,
        seed=training_config.seed,
        retained_class_index=neutral_class_index,
        retention=training_config.neutral_retention,
    )
    class_weight_vector = _build_inverse_frequency_class_weights(
        y_train,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Training balance prepared: '
            f'rows {rebalance_summary["rows_before"]} -> {rebalance_summary["rows_after"]}, '
            f'class weights={class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )

    model = TemporalConvolutionalClassifier(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training EMA low-ADX setup-quality classifier.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        class_weights=class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='EMA low-ADX setup-quality training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': training_config.class_weight_exponent,
            'class_weight_vector': class_weight_vector.tolist(),
            'neutral_retention': training_config.neutral_retention,
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 8)),
            'target_mode': str(config.get('targetMode', 'ema_low_adx_setup_quality_classification') or 'ema_low_adx_setup_quality_classification').strip().lower(),
            'setup_adx_ceiling': float(config.get('setupAdxCeiling', 28.0)),
            'setup_prev_rsi_ceiling': float(config.get('setupPrevRsiCeiling', 38.0)),
            'setup_current_rsi_floor': float(config.get('setupCurrentRsiFloor', 38.0)),
            'setup_current_rsi_ceiling': float(config.get('setupCurrentRsiCeiling', 50.0)),
            'setup_touch_slack_atr': float(config.get('setupTouchSlackAtr', 0.06)),
            'setup_prev_band_slack_atr': float(config.get('setupPrevBandSlackAtr', 0.08)),
            'setup_bounce_fraction': float(config.get('setupBounceFraction', 0.02)),
            'setup_di_spread_floor': float(config.get('setupDiSpreadFloor', 0.0)),
            'setup_candidate_min_gap_bars': int(config.get('setupCandidateMinGapBars', 0)),
            'target_quality_good_excursion_threshold': float(config.get('targetQualityGoodExcursionThreshold', 0.82)),
            'target_quality_bad_excursion_threshold': float(config.get('targetQualityBadExcursionThreshold', 0.52)),
            'target_quality_good_dominance_ratio': float(config.get('targetQualityGoodDominanceRatio', 1.1)),
            'target_quality_bad_dominance_ratio': float(config.get('targetQualityBadDominanceRatio', 1.1)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'class_weight_vector': class_weight_vector.tolist(),
            'neutral_retention': float(training_config.neutral_retention),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
        'score': score,
    }


def run_ema_low_adx_setup_quality_cnn_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing EMA low-ADX setup-quality test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_ema_low_adx_setup_quality_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 8))
    metrics['target_mode'] = str(config.get('targetMode', 'ema_low_adx_setup_quality_classification') or 'ema_low_adx_setup_quality_classification').strip().lower()
    metrics['setup_adx_ceiling'] = float(config.get('setupAdxCeiling', 28.0))
    metrics['setup_prev_rsi_ceiling'] = float(config.get('setupPrevRsiCeiling', 38.0))
    metrics['setup_current_rsi_floor'] = float(config.get('setupCurrentRsiFloor', 38.0))
    metrics['setup_current_rsi_ceiling'] = float(config.get('setupCurrentRsiCeiling', 50.0))
    metrics['setup_touch_slack_atr'] = float(config.get('setupTouchSlackAtr', 0.06))
    metrics['setup_prev_band_slack_atr'] = float(config.get('setupPrevBandSlackAtr', 0.08))
    metrics['setup_bounce_fraction'] = float(config.get('setupBounceFraction', 0.02))
    metrics['setup_di_spread_floor'] = float(config.get('setupDiSpreadFloor', 0.0))
    metrics['setup_candidate_min_gap_bars'] = int(config.get('setupCandidateMinGapBars', 0))
    metrics['target_quality_good_excursion_threshold'] = float(config.get('targetQualityGoodExcursionThreshold', 0.82))
    metrics['target_quality_bad_excursion_threshold'] = float(config.get('targetQualityBadExcursionThreshold', 0.52))
    metrics['target_quality_good_dominance_ratio'] = float(config.get('targetQualityGoodDominanceRatio', 1.1))
    metrics['target_quality_bad_dominance_ratio'] = float(config.get('targetQualityBadDominanceRatio', 1.1))
    metrics['candidate_summary'] = metadata.get('candidate_summary') or sequence_dataset.get('candidate_summary') or {}
    score = float(metrics.get('macro_f1') or 0.0)
    log(f'Test finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_ema_low_adx_setup_quality_cnn_v2_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing EMA low-ADX setup-quality v2 feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_ema_low_adx_setup_quality_v2_sequence_dataset()
    log(f'EMA low-ADX setup-quality v2 dataset built with {sequence_dataset["rows"]} clean candidate rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 80:
        raise ValueError('EMA low-ADX setup-quality v2 training requires at least 80 clean candidate sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 40:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = sequence_dataset['X'][:train_rows]
    y_train = sequence_dataset['y_class'][:train_rows]
    X_validation = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    class_weight_vector = _build_inverse_frequency_class_weights(
        y_train,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    rebalance_summary = {
        'rows_before': int(len(y_train)),
        'rows_after': int(len(y_train)),
        'class_counts_before': {
            str(index): int(np.sum(y_train == index))
            for index in range(len(sequence_dataset['class_codes']))
        },
        'class_counts_after': {
            str(index): int(np.sum(y_train == index))
            for index in range(len(sequence_dataset['class_codes']))
        },
        'retained_class_index': None,
        'retention': 1.0,
    }
    log(
        (
            'Training balance prepared: '
            f'rows {rebalance_summary["rows_before"]} -> {rebalance_summary["rows_after"]}, '
            f'class weights={class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )

    model = TemporalConvolutionalClassifier(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training EMA low-ADX setup-quality v2 classifier.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        class_weights=class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='EMA low-ADX setup-quality v2 training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': training_config.class_weight_exponent,
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 8)),
            'target_mode': str(config.get('targetMode', 'ema_low_adx_setup_quality_binary_classification') or 'ema_low_adx_setup_quality_binary_classification').strip().lower(),
            'setup_adx_ceiling': float(config.get('setupAdxCeiling', 28.0)),
            'setup_prev_rsi_ceiling': float(config.get('setupPrevRsiCeiling', 38.0)),
            'setup_current_rsi_floor': float(config.get('setupCurrentRsiFloor', 38.0)),
            'setup_current_rsi_ceiling': float(config.get('setupCurrentRsiCeiling', 50.0)),
            'setup_touch_slack_atr': float(config.get('setupTouchSlackAtr', 0.06)),
            'setup_prev_band_slack_atr': float(config.get('setupPrevBandSlackAtr', 0.08)),
            'setup_bounce_fraction': float(config.get('setupBounceFraction', 0.02)),
            'target_quality_good_excursion_threshold': float(config.get('targetQualityGoodExcursionThreshold', 0.82)),
            'target_quality_bad_excursion_threshold': float(config.get('targetQualityBadExcursionThreshold', 0.52)),
            'target_quality_good_dominance_ratio': float(config.get('targetQualityGoodDominanceRatio', 1.1)),
            'target_quality_bad_dominance_ratio': float(config.get('targetQualityBadDominanceRatio', 1.1)),
            'target_quality_good_counter_excursion_ceiling': float(config.get('targetQualityGoodCounterExcursionCeiling', 0.45)),
            'target_quality_bad_counter_excursion_ceiling': float(config.get('targetQualityBadCounterExcursionCeiling', 0.45)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
        'score': score,
    }


def run_ema_low_adx_setup_quality_cnn_v2_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing EMA low-ADX setup-quality v2 test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_ema_low_adx_setup_quality_v2_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 40:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 8))
    metrics['target_mode'] = str(config.get('targetMode', 'ema_low_adx_setup_quality_binary_classification') or 'ema_low_adx_setup_quality_binary_classification').strip().lower()
    metrics['setup_adx_ceiling'] = float(config.get('setupAdxCeiling', 28.0))
    metrics['setup_prev_rsi_ceiling'] = float(config.get('setupPrevRsiCeiling', 38.0))
    metrics['setup_current_rsi_floor'] = float(config.get('setupCurrentRsiFloor', 38.0))
    metrics['setup_current_rsi_ceiling'] = float(config.get('setupCurrentRsiCeiling', 50.0))
    metrics['setup_touch_slack_atr'] = float(config.get('setupTouchSlackAtr', 0.06))
    metrics['setup_prev_band_slack_atr'] = float(config.get('setupPrevBandSlackAtr', 0.08))
    metrics['setup_bounce_fraction'] = float(config.get('setupBounceFraction', 0.02))
    metrics['target_quality_good_excursion_threshold'] = float(config.get('targetQualityGoodExcursionThreshold', 0.82))
    metrics['target_quality_bad_excursion_threshold'] = float(config.get('targetQualityBadExcursionThreshold', 0.52))
    metrics['target_quality_good_dominance_ratio'] = float(config.get('targetQualityGoodDominanceRatio', 1.1))
    metrics['target_quality_bad_dominance_ratio'] = float(config.get('targetQualityBadDominanceRatio', 1.1))
    metrics['target_quality_good_counter_excursion_ceiling'] = float(config.get('targetQualityGoodCounterExcursionCeiling', 0.45))
    metrics['target_quality_bad_counter_excursion_ceiling'] = float(config.get('targetQualityBadCounterExcursionCeiling', 0.45))
    metrics['candidate_summary'] = metadata.get('candidate_summary') or sequence_dataset.get('candidate_summary') or {}
    score = float(metrics.get('macro_f1') or 0.0)
    log(f'Holdout finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_ema_low_adx_setup_quality_cnn_v3_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing EMA low-ADX setup-quality v3 feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_ema_low_adx_setup_quality_v3_sequence_dataset()
    log(f'EMA low-ADX setup-quality v3 dataset built with {sequence_dataset["rows"]} first-touch candidate rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 80:
        raise ValueError('EMA low-ADX setup-quality v3 training requires at least 80 first-touch candidate sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 40:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = sequence_dataset['X'][:train_rows]
    y_train = sequence_dataset['y_class'][:train_rows]
    X_validation = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    class_weight_vector = _build_inverse_frequency_class_weights(
        y_train,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    rebalance_summary = {
        'rows_before': int(len(y_train)),
        'rows_after': int(len(y_train)),
        'class_counts_before': {
            str(index): int(np.sum(y_train == index))
            for index in range(len(sequence_dataset['class_codes']))
        },
        'class_counts_after': {
            str(index): int(np.sum(y_train == index))
            for index in range(len(sequence_dataset['class_codes']))
        },
        'retained_class_index': None,
        'retention': 1.0,
    }
    log(
        (
            'Training balance prepared: '
            f'rows {rebalance_summary["rows_before"]} -> {rebalance_summary["rows_after"]}, '
            f'class weights={class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )

    model = TemporalConvolutionalClassifier(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training EMA low-ADX setup-quality v3 classifier.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        class_weights=class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='EMA low-ADX setup-quality v3 training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': training_config.class_weight_exponent,
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 8)),
            'target_mode': str(config.get('targetMode', 'ema_low_adx_setup_quality_first_touch_binary_classification') or 'ema_low_adx_setup_quality_first_touch_binary_classification').strip().lower(),
            'setup_adx_ceiling': float(config.get('setupAdxCeiling', 28.0)),
            'setup_prev_rsi_ceiling': float(config.get('setupPrevRsiCeiling', 38.0)),
            'setup_current_rsi_floor': float(config.get('setupCurrentRsiFloor', 38.0)),
            'setup_current_rsi_ceiling': float(config.get('setupCurrentRsiCeiling', 50.0)),
            'setup_touch_slack_atr': float(config.get('setupTouchSlackAtr', 0.06)),
            'setup_prev_band_slack_atr': float(config.get('setupPrevBandSlackAtr', 0.08)),
            'setup_bounce_fraction': float(config.get('setupBounceFraction', 0.02)),
            'target_quality_good_excursion_threshold': float(config.get('targetQualityGoodExcursionThreshold', 0.82)),
            'target_quality_bad_excursion_threshold': float(config.get('targetQualityBadExcursionThreshold', 0.52)),
            'target_quality_good_dominance_ratio': float(config.get('targetQualityGoodDominanceRatio', 1.1)),
            'target_quality_bad_dominance_ratio': float(config.get('targetQualityBadDominanceRatio', 1.1)),
            'target_reversal_take_profit_atr': float(config.get('targetReversalTakeProfitAtr', 1.0)),
            'target_reversal_stop_loss_atr': float(config.get('targetReversalStopLossAtr', 1.0)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
        'score': score,
    }


def run_ema_low_adx_setup_quality_cnn_v3_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing EMA low-ADX setup-quality v3 test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_ema_low_adx_setup_quality_v3_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 40:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 8))
    metrics['target_mode'] = str(config.get('targetMode', 'ema_low_adx_setup_quality_first_touch_binary_classification') or 'ema_low_adx_setup_quality_first_touch_binary_classification').strip().lower()
    metrics['setup_adx_ceiling'] = float(config.get('setupAdxCeiling', 28.0))
    metrics['setup_prev_rsi_ceiling'] = float(config.get('setupPrevRsiCeiling', 38.0))
    metrics['setup_current_rsi_floor'] = float(config.get('setupCurrentRsiFloor', 38.0))
    metrics['setup_current_rsi_ceiling'] = float(config.get('setupCurrentRsiCeiling', 50.0))
    metrics['setup_touch_slack_atr'] = float(config.get('setupTouchSlackAtr', 0.06))
    metrics['setup_prev_band_slack_atr'] = float(config.get('setupPrevBandSlackAtr', 0.08))
    metrics['setup_bounce_fraction'] = float(config.get('setupBounceFraction', 0.02))
    metrics['target_quality_good_excursion_threshold'] = float(config.get('targetQualityGoodExcursionThreshold', 0.82))
    metrics['target_quality_bad_excursion_threshold'] = float(config.get('targetQualityBadExcursionThreshold', 0.52))
    metrics['target_quality_good_dominance_ratio'] = float(config.get('targetQualityGoodDominanceRatio', 1.1))
    metrics['target_quality_bad_dominance_ratio'] = float(config.get('targetQualityBadDominanceRatio', 1.1))
    metrics['target_reversal_take_profit_atr'] = float(config.get('targetReversalTakeProfitAtr', 1.0))
    metrics['target_reversal_stop_loss_atr'] = float(config.get('targetReversalStopLossAtr', 1.0))
    metrics['candidate_summary'] = metadata.get('candidate_summary') or sequence_dataset.get('candidate_summary') or {}
    score = float(metrics.get('macro_f1') or 0.0)
    log(f'Holdout finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_ema_low_adx_setup_quality_cnn_v4_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    network_id = str(config.get('networkId') or 'ema_low_adx_setup_quality_cnn_v4').strip() or 'ema_low_adx_setup_quality_cnn_v4'
    version_label = network_id.replace('ema_low_adx_setup_quality_cnn_', 'v')
    log(f'Preparing EMA low-ADX setup-quality {version_label} feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    if network_id == 'ema_low_adx_setup_quality_cnn_v7':
        sequence_dataset = pipeline.build_ema_low_adx_setup_quality_v7_sequence_dataset()
    else:
        sequence_dataset = pipeline.build_ema_low_adx_setup_quality_v4_sequence_dataset()
    log(f"EMA low-ADX setup-quality {version_label} dataset built with {sequence_dataset['rows']} candidate rows.", progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 80:
        raise ValueError(f'EMA low-ADX setup-quality {version_label} training requires at least 80 candidate sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 40:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = sequence_dataset['X'][:train_rows]
    y_train = sequence_dataset['y_class'][:train_rows]
    X_validation = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    class_weight_vector = _build_inverse_frequency_class_weights(
        y_train,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    rebalance_summary = {
        'rows_before': int(len(y_train)),
        'rows_after': int(len(y_train)),
        'class_counts_before': {
            str(index): int(np.sum(y_train == index))
            for index in range(len(sequence_dataset['class_codes']))
        },
        'class_counts_after': {
            str(index): int(np.sum(y_train == index))
            for index in range(len(sequence_dataset['class_codes']))
        },
        'retained_class_index': None,
        'retention': 1.0,
    }
    log(
        (
            'Training balance prepared: '
            f"rows {rebalance_summary['rows_before']} -> {rebalance_summary['rows_after']}, "
            f'class weights={class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )

    model = TemporalConvolutionalClassifier(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log(f'Training EMA low-ADX setup-quality {version_label} classifier.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        class_weights=class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label=f'EMA low-ADX setup-quality {version_label} training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': training_config.class_weight_exponent,
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
    )
    score = float(
        validation_metrics.get('class_good_setup_f1')
        or validation_metrics.get('macro_f1')
        or 0.0
    )
    log(f'Validation finished with good-setup F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 8)),
            'target_mode': str(config.get('targetMode', 'ema_low_adx_setup_quality_good_vs_rest_classification') or 'ema_low_adx_setup_quality_good_vs_rest_classification').strip().lower(),
            'setup_adx_ceiling': float(config.get('setupAdxCeiling', 28.0)),
            'setup_prev_rsi_ceiling': float(config.get('setupPrevRsiCeiling', 38.0)),
            'setup_current_rsi_floor': float(config.get('setupCurrentRsiFloor', 38.0)),
            'setup_current_rsi_ceiling': float(config.get('setupCurrentRsiCeiling', 50.0)),
            'setup_touch_slack_atr': float(config.get('setupTouchSlackAtr', 0.06)),
            'setup_prev_band_slack_atr': float(config.get('setupPrevBandSlackAtr', 0.08)),
            'setup_bounce_fraction': float(config.get('setupBounceFraction', 0.02)),
            'target_quality_good_excursion_threshold': float(config.get('targetQualityGoodExcursionThreshold', 0.82)),
            'target_quality_bad_excursion_threshold': float(config.get('targetQualityBadExcursionThreshold', 0.52)),
            'target_quality_good_dominance_ratio': float(config.get('targetQualityGoodDominanceRatio', 1.1)),
            'target_quality_bad_dominance_ratio': float(config.get('targetQualityBadDominanceRatio', 1.1)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
        'score': score,
    }


def run_ema_low_adx_setup_quality_cnn_v4_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    network_id = str(config.get('networkId') or 'ema_low_adx_setup_quality_cnn_v4').strip() or 'ema_low_adx_setup_quality_cnn_v4'
    version_label = network_id.replace('ema_low_adx_setup_quality_cnn_', 'v')
    log(f'Preparing EMA low-ADX setup-quality {version_label} test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    if network_id == 'ema_low_adx_setup_quality_cnn_v7':
        sequence_dataset = pipeline.build_ema_low_adx_setup_quality_v7_sequence_dataset()
    else:
        sequence_dataset = pipeline.build_ema_low_adx_setup_quality_v4_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 40:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 8))
    metrics['target_mode'] = str(config.get('targetMode', 'ema_low_adx_setup_quality_good_vs_rest_classification') or 'ema_low_adx_setup_quality_good_vs_rest_classification').strip().lower()
    metrics['setup_adx_ceiling'] = float(config.get('setupAdxCeiling', 28.0))
    metrics['setup_prev_rsi_ceiling'] = float(config.get('setupPrevRsiCeiling', 38.0))
    metrics['setup_current_rsi_floor'] = float(config.get('setupCurrentRsiFloor', 38.0))
    metrics['setup_current_rsi_ceiling'] = float(config.get('setupCurrentRsiCeiling', 50.0))
    metrics['setup_touch_slack_atr'] = float(config.get('setupTouchSlackAtr', 0.06))
    metrics['setup_prev_band_slack_atr'] = float(config.get('setupPrevBandSlackAtr', 0.08))
    metrics['setup_bounce_fraction'] = float(config.get('setupBounceFraction', 0.02))
    metrics['target_quality_good_excursion_threshold'] = float(config.get('targetQualityGoodExcursionThreshold', 0.82))
    metrics['target_quality_bad_excursion_threshold'] = float(config.get('targetQualityBadExcursionThreshold', 0.52))
    metrics['target_quality_good_dominance_ratio'] = float(config.get('targetQualityGoodDominanceRatio', 1.1))
    metrics['target_quality_bad_dominance_ratio'] = float(config.get('targetQualityBadDominanceRatio', 1.1))
    metrics['candidate_summary'] = metadata.get('candidate_summary') or sequence_dataset.get('candidate_summary') or {}
    score = float(
        metrics.get('class_good_setup_f1')
        or metrics.get('macro_f1')
        or 0.0
    )
    log(f'Holdout finished with good-setup F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_ema_low_adx_setup_quality_cnn_v5_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'ema_low_adx_setup_quality_cnn_v5').strip()
        or 'ema_low_adx_setup_quality_cnn_v5'
    )
    return run_ema_low_adx_setup_quality_cnn_v4_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_ema_low_adx_setup_quality_cnn_v5_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'ema_low_adx_setup_quality_cnn_v5').strip()
        or 'ema_low_adx_setup_quality_cnn_v5'
    )
    return run_ema_low_adx_setup_quality_cnn_v4_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_ema_low_adx_setup_quality_cnn_v6_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'ema_low_adx_setup_quality_cnn_v6').strip()
        or 'ema_low_adx_setup_quality_cnn_v6'
    )
    return run_ema_low_adx_setup_quality_cnn_v4_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_ema_low_adx_setup_quality_cnn_v6_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'ema_low_adx_setup_quality_cnn_v6').strip()
        or 'ema_low_adx_setup_quality_cnn_v6'
    )
    return run_ema_low_adx_setup_quality_cnn_v4_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_ema_low_adx_setup_quality_cnn_v7_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'ema_low_adx_setup_quality_cnn_v7').strip()
        or 'ema_low_adx_setup_quality_cnn_v7'
    )
    next_config['targetMode'] = 'ema_low_adx_setup_quality_tp_sl_good_vs_rest_classification'
    next_config.setdefault('targetReversalTakeProfitAtr', 1.0)
    next_config.setdefault('targetReversalStopLossAtr', 1.0)
    return run_ema_low_adx_setup_quality_cnn_v4_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_ema_low_adx_setup_quality_cnn_v7_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'ema_low_adx_setup_quality_cnn_v7').strip()
        or 'ema_low_adx_setup_quality_cnn_v7'
    )
    next_config['targetMode'] = 'ema_low_adx_setup_quality_tp_sl_good_vs_rest_classification'
    next_config.setdefault('targetReversalTakeProfitAtr', 1.0)
    next_config.setdefault('targetReversalStopLossAtr', 1.0)
    return run_ema_low_adx_setup_quality_cnn_v4_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_micro_cost_edge_cnn_v1_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing micro cost-edge feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_micro_cost_edge_sequence_dataset()
    log(f"Micro cost-edge dataset built with {sequence_dataset['rows']} sequence rows.", progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('Micro cost-edge training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = sequence_dataset['X'][:train_rows]
    y_train = sequence_dataset['y_class'][:train_rows]
    X_validation = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    no_edge_class_index = list(sequence_dataset['class_codes']).index(0)
    X_train, y_train, rebalance_summary = _rebalance_sequence_classes(
        X_train,
        y_train,
        seed=training_config.seed,
        retained_class_index=no_edge_class_index,
        retention=training_config.neutral_retention,
    )
    class_weight_vector = _build_inverse_frequency_class_weights(
        y_train,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Training balance prepared: '
            f'rows {rebalance_summary["rows_before"]} -> {rebalance_summary["rows_after"]}, '
            f'class weights={class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )

    model = TemporalConvolutionalClassifier(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training micro cost-edge classifier.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        class_weights=class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Micro cost-edge training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = _augment_micro_cost_edge_metrics(model.evaluate(X_validation, y_validation))
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': training_config.class_weight_exponent,
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
    )
    score = float(
        validation_metrics.get('directional_edge_macro_f1')
        or validation_metrics.get('macro_f1')
        or 0.0
    )
    log(f'Validation finished with directional edge F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 5)),
            'target_mode': str(config.get('targetMode', 'micro_cost_edge_classification') or 'micro_cost_edge_classification').strip().lower(),
            'pip_size': float(config.get('pipSize', 0.0001)),
            'round_trip_cost_pips': float(config.get('roundTripCostPips', 1.6)),
            'target_cost_edge_multiple': float(config.get('targetCostEdgeMultiple', 1.75)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
        'score': score,
    }


def run_micro_cost_edge_cnn_v1_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing micro cost-edge test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_micro_cost_edge_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = _augment_micro_cost_edge_metrics(model.evaluate(X_test, y_test))
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 5))
    metrics['target_mode'] = str(config.get('targetMode', 'micro_cost_edge_classification') or 'micro_cost_edge_classification').strip().lower()
    metrics['pip_size'] = float(config.get('pipSize', 0.0001))
    metrics['round_trip_cost_pips'] = float(config.get('roundTripCostPips', 1.6))
    metrics['target_cost_edge_multiple'] = float(config.get('targetCostEdgeMultiple', 1.75))
    metrics['candidate_summary'] = metadata.get('candidate_summary') or sequence_dataset.get('candidate_summary') or {}
    score = float(
        metrics.get('directional_edge_macro_f1')
        or metrics.get('macro_f1')
        or 0.0
    )
    log(f'Holdout finished with directional edge F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_micro_cost_edge_cnn_v2_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing mirrored micro cost-edge feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_micro_cost_edge_canonical_sequence_dataset()
    log(f"Mirrored micro cost-edge dataset built with {sequence_dataset['rows']} event rows.", progress=0.16)

    total_events = int(sequence_dataset['rows'])
    if total_events < 120:
        raise ValueError('Micro cost-edge v2 training requires at least 120 clean event rows after feature generation.')

    test_events = max(10, int(total_events * max(0.0, float(config['testSplit']))))
    validation_events = max(10, int(total_events * max(0.0, float(config['validationSplit']))))
    train_events = total_events - validation_events - test_events
    if train_events < 50:
        raise ValueError('Not enough event rows left for training after validation/test split.')

    split_sizes = {
        'total': total_events,
        'train': train_events,
        'validation': validation_events,
        'test': total_events - train_events - validation_events,
        'train_side': train_events * 2,
        'validation_side': validation_events * 2,
        'test_side': (total_events - train_events - validation_events) * 2,
    }
    log(
        (
            f"Event split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    train_slice = slice(0, train_events)
    validation_slice = slice(train_events, train_events + validation_events)

    X_train_long = sequence_dataset['X_long'][train_slice]
    X_train_short = sequence_dataset['X_short'][train_slice]
    y_train_long = sequence_dataset['y_long'][train_slice]
    y_train_short = sequence_dataset['y_short'][train_slice]
    X_validation_long = sequence_dataset['X_long'][validation_slice]
    X_validation_short = sequence_dataset['X_short'][validation_slice]
    y_validation_event_codes = sequence_dataset['y_event_code'][validation_slice]

    X_train_side = np.concatenate([X_train_long, X_train_short], axis=0)
    y_train_side = np.concatenate([y_train_long, y_train_short], axis=0)
    X_validation_side = np.concatenate([X_validation_long, X_validation_short], axis=0)
    y_validation_side = np.concatenate([
        sequence_dataset['y_long'][validation_slice],
        sequence_dataset['y_short'][validation_slice],
    ], axis=0)

    training_config = _build_supervised_training_config(config)
    X_train_side, y_train_side, rebalance_summary = _rebalance_sequence_classes(
        X_train_side,
        y_train_side,
        seed=training_config.seed,
        retained_class_index=0,
        retention=training_config.neutral_retention,
    )
    class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_side,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Training balance prepared: '
            f'rows {rebalance_summary["rows_before"]} -> {rebalance_summary["rows_after"]}, '
            f'class weights={class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )

    model = TemporalConvolutionalClassifier(
        input_features=X_train_side.shape[2],
        sequence_length=X_train_side.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train_side)
    X_train_side = model.transform_features(X_train_side)
    X_validation_side = model.transform_features(X_validation_side)
    X_validation_long = model.transform_features(X_validation_long)
    X_validation_short = model.transform_features(X_validation_short)

    log('Training mirrored micro cost-edge classifier.', progress=0.34)
    model.train(
        X_train_side,
        y_train_side,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_side,
        y_validation=y_validation_side,
        class_weights=class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Mirrored micro cost-edge training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )

    validation_side_metrics = model.evaluate(X_validation_side, y_validation_side)
    validation_long_scores = model.predict_probabilities(X_validation_long)[:, 1]
    validation_short_scores = model.predict_probabilities(X_validation_short)[:, 1]
    event_code_to_index = {
        int(code): index
        for index, code in enumerate(sequence_dataset['event_class_codes'])
    }
    y_validation_event_indices = np.asarray([
        event_code_to_index[int(code)]
        for code in y_validation_event_codes
    ], dtype=int)
    validation_event_payload = _search_micro_cost_edge_side_threshold(
        y_validation_event_indices,
        validation_long_scores,
        validation_short_scores,
        class_codes=sequence_dataset['event_class_codes'],
        class_labels=sequence_dataset['event_class_labels'],
    )
    validation_metrics = dict(validation_event_payload['metrics'])
    validation_metrics['threshold'] = float(validation_event_payload['threshold'])
    validation_metrics['threshold_source'] = 'validation_search'
    validation_metrics['threshold_selection_version'] = int(MICRO_COST_EDGE_EVENT_THRESHOLD_SELECTION_VERSION)
    validation_metrics['side_class_edge_for_side_f1'] = float(validation_side_metrics.get('class_edge_for_side_f1') or 0.0)
    validation_metrics['side_class_not_edge_for_side_recall'] = float(validation_side_metrics.get('class_not_edge_for_side_recall') or 0.0)
    validation_metrics['side_macro_f1'] = float(validation_side_metrics.get('macro_f1') or 0.0)
    validation_metrics['side_accuracy'] = float(validation_side_metrics.get('accuracy') or 0.0)

    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'event_class_codes': list(sequence_dataset['event_class_codes']),
            'event_class_labels': dict(sequence_dataset['event_class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': training_config.class_weight_exponent,
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
            'selected_event_threshold': float(validation_event_payload['threshold']),
            'selected_event_threshold_version': int(MICRO_COST_EDGE_EVENT_THRESHOLD_SELECTION_VERSION),
        },
    )
    score = float(
        validation_metrics.get('directional_edge_macro_f1')
        or validation_metrics.get('macro_f1')
        or 0.0
    )
    log(f'Validation finished with mirrored directional edge F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_events,
            'side_rows': int(sequence_dataset.get('side_rows') or (total_events * 2)),
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 5)),
            'target_mode': str(config.get('targetMode', 'micro_cost_edge_side_classification') or 'micro_cost_edge_side_classification').strip().lower(),
            'pip_size': float(config.get('pipSize', 0.0001)),
            'round_trip_cost_pips': float(config.get('roundTripCostPips', 1.6)),
            'target_cost_edge_multiple': float(config.get('targetCostEdgeMultiple', 1.75)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'event_class_codes': list(sequence_dataset['event_class_codes']),
            'event_class_labels': dict(sequence_dataset['event_class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
        'score': score,
    }


def run_micro_cost_edge_cnn_v2_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing mirrored micro cost-edge test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_micro_cost_edge_canonical_sequence_dataset()
    total_events = int(sequence_dataset['rows'])
    test_events = max(10, int(total_events * max(0.0, float(config['testSplit']))))
    validation_events = max(10, int(total_events * max(0.0, float(config['validationSplit']))))
    train_events = total_events - validation_events - test_events
    if train_events < 50:
        raise ValueError('Not enough event rows left for training after validation/test split.')
    split_sizes = {
        'total': total_events,
        'train': train_events,
        'validation': validation_events,
        'test': total_events - train_events - validation_events,
        'train_side': train_events * 2,
        'validation_side': validation_events * 2,
        'test_side': (total_events - train_events - validation_events) * 2,
    }
    log(
        (
            f"Testing on chronological event holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    threshold_payload = _resolve_micro_cost_edge_v2_event_threshold(
        model,
        metadata,
        sequence_dataset,
        train_events=train_events,
        validation_events=validation_events,
    )
    threshold = float(threshold_payload['threshold'])
    if threshold_payload['source'] != 'artifact_metadata':
        log(
            (
                f"Recalibrated mirrored event threshold at {threshold:.3f} "
                f"from {threshold_payload['source']}."
            ),
            progress=0.32,
        )
    test_slice = slice(train_events + validation_events, total_events)
    X_test_long = model.transform_features(sequence_dataset['X_long'][test_slice])
    X_test_short = model.transform_features(sequence_dataset['X_short'][test_slice])
    y_test_side = np.concatenate([
        sequence_dataset['y_long'][test_slice],
        sequence_dataset['y_short'][test_slice],
    ], axis=0)
    X_test_side = np.concatenate([X_test_long, X_test_short], axis=0)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')

    side_metrics = model.evaluate(X_test_side, y_test_side)
    test_long_scores = model.predict_probabilities(X_test_long)[:, 1]
    test_short_scores = model.predict_probabilities(X_test_short)[:, 1]
    event_code_to_index = {
        int(code): index
        for index, code in enumerate(sequence_dataset['event_class_codes'])
    }
    y_test_event_indices = np.asarray([
        event_code_to_index[int(code)]
        for code in sequence_dataset['y_event_code'][test_slice]
    ], dtype=int)
    predicted_indices, combined_probabilities = _combine_micro_cost_edge_side_predictions(
        test_long_scores,
        test_short_scores,
        threshold=threshold,
        class_codes=sequence_dataset['event_class_codes'],
        class_labels=sequence_dataset['event_class_labels'],
    )
    metrics = _augment_micro_cost_edge_metrics(_evaluate_class_predictions(
        y_test_event_indices,
        predicted_indices,
        class_codes=sequence_dataset['event_class_codes'],
        class_labels=sequence_dataset['event_class_labels'],
        probabilities=combined_probabilities,
    ))
    metrics['threshold'] = float(threshold)
    metrics['threshold_source'] = str(threshold_payload['source'])
    metrics['threshold_selection_version'] = int(threshold_payload['version'])
    threshold_validation_metrics = dict(threshold_payload.get('validation_metrics') or {})
    if threshold_validation_metrics:
        metrics['threshold_validation_macro_f1'] = float(threshold_validation_metrics.get('macro_f1') or 0.0)
        metrics['threshold_validation_tradability_f1'] = float(threshold_validation_metrics.get('tradability_f1') or 0.0)
        metrics['threshold_validation_directional_edge_macro_f1'] = float(
            threshold_validation_metrics.get('directional_edge_macro_f1') or 0.0
        )
        metrics['threshold_validation_predicted_tradability_rate'] = float(
            threshold_validation_metrics.get('predicted_tradability_rate') or 0.0
        )
        metrics['threshold_validation_actual_tradability_rate'] = float(
            threshold_validation_metrics.get('actual_tradability_rate') or 0.0
        )
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 5))
    metrics['target_mode'] = str(config.get('targetMode', 'micro_cost_edge_side_classification') or 'micro_cost_edge_side_classification').strip().lower()
    metrics['pip_size'] = float(config.get('pipSize', 0.0001))
    metrics['round_trip_cost_pips'] = float(config.get('roundTripCostPips', 1.6))
    metrics['target_cost_edge_multiple'] = float(config.get('targetCostEdgeMultiple', 1.75))
    metrics['candidate_summary'] = metadata.get('candidate_summary') or sequence_dataset.get('candidate_summary') or {}
    metrics['side_class_edge_for_side_f1'] = float(side_metrics.get('class_edge_for_side_f1') or 0.0)
    metrics['side_class_not_edge_for_side_recall'] = float(side_metrics.get('class_not_edge_for_side_recall') or 0.0)
    metrics['side_macro_f1'] = float(side_metrics.get('macro_f1') or 0.0)
    metrics['side_accuracy'] = float(side_metrics.get('accuracy') or 0.0)
    score = float(
        metrics.get('directional_edge_macro_f1')
        or metrics.get('macro_f1')
        or 0.0
    )
    log(f'Holdout finished with mirrored directional edge F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_candle_reversal_cnn_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing candle reversal feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    log(f'Candle reversal dataset built with {sequence_dataset["rows"]} rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('Candle reversal training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = sequence_dataset['X'][:train_rows]
    y_train = sequence_dataset['y_class'][:train_rows]
    X_validation = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    neutral_class_index = None
    if 0 in list(sequence_dataset['class_codes']):
        neutral_class_index = list(sequence_dataset['class_codes']).index(0)
    X_train, y_train, rebalance_summary = _rebalance_sequence_classes(
        X_train,
        y_train,
        seed=training_config.seed,
        retained_class_index=neutral_class_index,
        retention=training_config.neutral_retention,
    )
    class_weight_vector = _build_inverse_frequency_class_weights(
        y_train,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Training balance prepared: '
            f'rows {rebalance_summary["rows_before"]} -> {rebalance_summary["rows_after"]}, '
            f'class weights={class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )
    model = TemporalConvolutionalClassifier(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training candle reversal classifier.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        class_weights=class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Candle reversal training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': training_config.class_weight_exponent,
            'class_weight_vector': class_weight_vector.tolist(),
            'neutral_retention': training_config.neutral_retention,
            'rebalance_summary': rebalance_summary,
        },
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 6)),
            'target_mode': str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower(),
            'pretrend_lookback': int(config.get('pretrendLookback', 6)),
            'pretrend_threshold': float(config.get('pretrendThreshold', 1.2)),
            'reversal_threshold': float(config.get('reversalThreshold', 1.0)),
            'dominance_ratio': float(config.get('dominanceRatio', 1.35)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'class_weight_vector': class_weight_vector.tolist(),
            'neutral_retention': float(training_config.neutral_retention),
            'rebalance_summary': rebalance_summary,
        },
        'score': score,
    }


def run_candle_reversal_cnn_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing candle reversal test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 6))
    metrics['target_mode'] = str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower()
    metrics['pretrend_lookback'] = int(config.get('pretrendLookback', 6))
    metrics['pretrend_threshold'] = float(config.get('pretrendThreshold', 1.2))
    metrics['reversal_threshold'] = float(config.get('reversalThreshold', 1.0))
    metrics['dominance_ratio'] = float(config.get('dominanceRatio', 1.35))
    metrics['class_weight_mode'] = str(config.get('classWeightMode', 'none') or 'none').strip().lower()
    metrics['class_weight_exponent'] = float(config.get('classWeightExponent', 1.0))
    metrics['neutral_retention'] = float(config.get('neutralRetention', 1.0))
    score = float(metrics.get('macro_f1') or 0.0)
    log(f'Test finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_micro_cost_edge_cnn_v3_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    network_id = str(config.get('networkId') or 'micro_cost_edge_cnn_v3').strip() or 'micro_cost_edge_cnn_v3'
    log('Preparing hierarchical micro cost-edge feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_micro_cost_edge_canonical_sequence_dataset()
    log(f"Hierarchical micro cost-edge dataset built with {sequence_dataset['rows']} event rows.", progress=0.16)

    total_events = int(sequence_dataset['rows'])
    if total_events < 120:
        raise ValueError('Micro cost-edge v3 training requires at least 120 clean event rows after feature generation.')

    test_events = max(10, int(total_events * max(0.0, float(config['testSplit']))))
    validation_events = max(10, int(total_events * max(0.0, float(config['validationSplit']))))
    train_events = total_events - validation_events - test_events
    if train_events < 50:
        raise ValueError('Not enough event rows left for training after validation/test split.')

    split_sizes = {
        'total': total_events,
        'train': train_events,
        'validation': validation_events,
        'test': total_events - train_events - validation_events,
        'train_side': train_events * 2,
        'validation_side': validation_events * 2,
        'test_side': (total_events - train_events - validation_events) * 2,
    }
    log(
        (
            f"Event split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    train_slice = slice(0, train_events)
    validation_slice = slice(train_events, train_events + validation_events)
    train_event_codes = np.asarray(sequence_dataset['y_event_code'][train_slice], dtype=int)
    validation_event_codes = np.asarray(sequence_dataset['y_event_code'][validation_slice], dtype=int)
    train_edge_mask = train_event_codes != 0
    validation_edge_mask = validation_event_codes != 0

    X_train_long = sequence_dataset['X_long'][train_slice]
    X_train_short = sequence_dataset['X_short'][train_slice]
    X_validation_long = sequence_dataset['X_long'][validation_slice]
    X_validation_short = sequence_dataset['X_short'][validation_slice]

    training_config = _build_supervised_training_config(config)

    stage1_train_target = np.where(train_edge_mask, 1, 0).astype(int)
    stage1_validation_target = np.where(validation_event_codes != 0, 1, 0).astype(int)
    X_train_stage1 = np.concatenate([X_train_long, X_train_short], axis=0)
    y_train_stage1 = np.concatenate([stage1_train_target, stage1_train_target], axis=0)
    X_validation_stage1 = np.concatenate([X_validation_long, X_validation_short], axis=0)
    y_validation_stage1 = np.concatenate([stage1_validation_target, stage1_validation_target], axis=0)
    X_train_stage1, y_train_stage1, stage1_rebalance_summary = _rebalance_sequence_classes(
        X_train_stage1,
        y_train_stage1,
        seed=training_config.seed,
        retained_class_index=0,
        retention=training_config.neutral_retention,
    )
    stage1_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_stage1,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Stage 1 balance prepared: '
            f'rows {stage1_rebalance_summary["rows_before"]} -> {stage1_rebalance_summary["rows_after"]}, '
            f'class weights={stage1_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )
    stage1_model = TemporalConvolutionalClassifier(
        input_features=X_train_stage1.shape[2],
        sequence_length=X_train_stage1.shape[1],
        class_codes=[0, 1],
        class_labels={0: 'no_edge', 1: 'tradable_edge'},
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    stage1_model.feature_columns = list(sequence_dataset['feature_columns'])
    stage1_model.fit_normalizer(X_train_stage1)
    X_train_stage1 = stage1_model.transform_features(X_train_stage1)
    X_validation_stage1 = stage1_model.transform_features(X_validation_stage1)

    log('Training stage 1 tradability gate.', progress=0.34)
    stage1_model.train(
        X_train_stage1,
        y_train_stage1,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_stage1,
        y_validation=y_validation_stage1,
        class_weights=stage1_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Hierarchical micro cost-edge stage 1',
            detail=(
                f"stage 1 epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    stage1_validation_metrics = stage1_model.evaluate(X_validation_stage1, y_validation_stage1)

    X_train_stage2 = np.concatenate([X_train_long[train_edge_mask], X_train_short[train_edge_mask]], axis=0)
    y_train_stage2 = np.concatenate([
        sequence_dataset['y_long'][train_slice][train_edge_mask],
        sequence_dataset['y_short'][train_slice][train_edge_mask],
    ], axis=0)
    X_validation_stage2 = np.concatenate([X_validation_long[validation_edge_mask], X_validation_short[validation_edge_mask]], axis=0)
    y_validation_stage2 = np.concatenate([
        sequence_dataset['y_long'][validation_slice][validation_edge_mask],
        sequence_dataset['y_short'][validation_slice][validation_edge_mask],
    ], axis=0)
    if len(y_train_stage2) < 40:
        raise ValueError('Stage 2 directional training requires at least 40 tradable side rows in the train split.')
    if len(y_validation_stage2) < 10:
        raise ValueError('Stage 2 directional validation requires at least 10 tradable side rows in the validation split.')

    stage2_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_stage2,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Stage 2 balance prepared: '
            f'rows {len(y_train_stage2)} -> {len(y_train_stage2)}, '
            f'class weights={stage2_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.58,
    )
    stage2_model = TemporalConvolutionalClassifier(
        input_features=X_train_stage2.shape[2],
        sequence_length=X_train_stage2.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed + 17,
    )
    stage2_model.feature_columns = list(sequence_dataset['feature_columns'])
    stage2_model.fit_normalizer(X_train_stage2)
    X_train_stage2 = stage2_model.transform_features(X_train_stage2)
    X_validation_stage2 = stage2_model.transform_features(X_validation_stage2)

    log('Training stage 2 directional head.', progress=0.6)
    stage2_model.train(
        X_train_stage2,
        y_train_stage2,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_stage2,
        y_validation=y_validation_stage2,
        class_weights=stage2_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.6 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Hierarchical micro cost-edge stage 2',
            detail=(
                f"stage 2 epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    stage2_validation_metrics = stage2_model.evaluate(X_validation_stage2, y_validation_stage2)

    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    log('Selecting hierarchical micro cost-edge threshold on validation split.', progress=0.84)
    validation_tradability_scores, _, _ = _predict_micro_cost_edge_v3_tradability_scores(
        stage1_model,
        sequence_dataset['X_long'][validation_slice],
        sequence_dataset['X_short'][validation_slice],
    )
    X_validation_long_stage2 = stage2_model.transform_features(sequence_dataset['X_long'][validation_slice])
    X_validation_short_stage2 = stage2_model.transform_features(sequence_dataset['X_short'][validation_slice])
    validation_long_scores = stage2_model.predict_probabilities(X_validation_long_stage2)[:, 1]
    validation_short_scores = stage2_model.predict_probabilities(X_validation_short_stage2)[:, 1]
    event_code_to_index = {
        int(code): index
        for index, code in enumerate(sequence_dataset['event_class_codes'])
    }
    y_validation_event_indices = np.asarray([
        event_code_to_index[int(code)]
        for code in validation_event_codes
    ], dtype=int)
    validation_event_payload = _search_micro_cost_edge_hierarchical_threshold(
        y_validation_event_indices,
        validation_tradability_scores,
        validation_long_scores,
        validation_short_scores,
        class_codes=sequence_dataset['event_class_codes'],
        class_labels=sequence_dataset['event_class_labels'],
    )
    validation_metrics = dict(validation_event_payload['metrics'])
    validation_metrics['threshold'] = float(validation_event_payload['threshold'])
    validation_metrics['threshold_source'] = 'validation_search'
    validation_metrics['threshold_selection_version'] = int(MICRO_COST_EDGE_HIERARCHICAL_EVENT_THRESHOLD_SELECTION_VERSION)
    validation_metrics['gate_class_tradable_edge_f1'] = float(stage1_validation_metrics.get('class_tradable_edge_f1') or 0.0)
    validation_metrics['gate_class_no_edge_recall'] = float(stage1_validation_metrics.get('class_no_edge_recall') or 0.0)
    validation_metrics['gate_macro_f1'] = float(stage1_validation_metrics.get('macro_f1') or 0.0)
    validation_metrics['gate_accuracy'] = float(stage1_validation_metrics.get('accuracy') or 0.0)
    validation_metrics['side_class_edge_for_side_f1'] = float(stage2_validation_metrics.get('class_edge_for_side_f1') or 0.0)
    validation_metrics['side_class_not_edge_for_side_recall'] = float(stage2_validation_metrics.get('class_not_edge_for_side_recall') or 0.0)
    validation_metrics['side_macro_f1'] = float(stage2_validation_metrics.get('macro_f1') or 0.0)
    validation_metrics['side_accuracy'] = float(stage2_validation_metrics.get('accuracy') or 0.0)

    log('Training finished. Saving hierarchical model.', progress=0.92)
    manifest = {
        'artifact_type': 'hierarchical_micro_cost_edge_v3',
        'network_id': network_id,
        'feature_columns': list(sequence_dataset['feature_columns']),
        'split_sizes': split_sizes,
        'class_codes': list(sequence_dataset['event_class_codes']),
        'class_labels': dict(sequence_dataset['event_class_labels']),
        'side_class_codes': list(sequence_dataset['class_codes']),
        'side_class_labels': dict(sequence_dataset['class_labels']),
        'selected_event_threshold': float(validation_event_payload['threshold']),
        'selected_event_threshold_version': int(MICRO_COST_EDGE_HIERARCHICAL_EVENT_THRESHOLD_SELECTION_VERSION),
        'class_weight_mode': training_config.class_weight_mode,
        'class_weight_exponent': float(training_config.class_weight_exponent),
        'neutral_retention': float(training_config.neutral_retention),
        'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        'stage1_rebalance_summary': stage1_rebalance_summary,
        'stage1_class_weight_vector': stage1_class_weight_vector.tolist(),
        'stage2_class_weight_vector': stage2_class_weight_vector.tolist(),
        'stage1_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': {0: 'no_edge', 1: 'tradable_edge'},
            'stage_role': 'tradability_gate',
        },
        'stage2_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'stage_role': 'side_direction',
        },
    }
    artifact_path = _save_hierarchical_micro_cost_edge_artifact(
        model_base_path,
        stage1_model=stage1_model,
        stage2_model=stage2_model,
        manifest=manifest,
    )
    score = float(
        validation_metrics.get('directional_edge_macro_f1')
        or validation_metrics.get('macro_f1')
        or 0.0
    )
    log(f'Validation finished with hierarchical directional edge F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_events,
            'side_rows': int(sequence_dataset.get('side_rows') or (total_events * 2)),
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'stage1_validation': stage1_validation_metrics,
            'stage2_validation': stage2_validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 5)),
            'target_mode': str(config.get('targetMode', 'micro_cost_edge_hierarchical_classification') or 'micro_cost_edge_hierarchical_classification').strip().lower(),
            'pip_size': float(config.get('pipSize', 0.0001)),
            'round_trip_cost_pips': float(config.get('roundTripCostPips', 1.6)),
            'target_cost_edge_multiple': float(config.get('targetCostEdgeMultiple', 1.75)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['event_class_codes']),
            'class_labels': dict(sequence_dataset['event_class_labels']),
            'side_class_codes': list(sequence_dataset['class_codes']),
            'side_class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'stage1_class_weight_vector': stage1_class_weight_vector.tolist(),
            'stage2_class_weight_vector': stage2_class_weight_vector.tolist(),
            'stage1_rebalance_summary': stage1_rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
        'score': score,
    }


def run_micro_cost_edge_cnn_v3_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing hierarchical micro cost-edge test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_micro_cost_edge_canonical_sequence_dataset()
    total_events = int(sequence_dataset['rows'])
    test_events = max(10, int(total_events * max(0.0, float(config['testSplit']))))
    validation_events = max(10, int(total_events * max(0.0, float(config['validationSplit']))))
    train_events = total_events - validation_events - test_events
    if train_events < 50:
        raise ValueError('Not enough event rows left for training after validation/test split.')
    split_sizes = {
        'total': total_events,
        'train': train_events,
        'validation': validation_events,
        'test': total_events - train_events - validation_events,
        'train_side': train_events * 2,
        'validation_side': validation_events * 2,
        'test_side': (total_events - train_events - validation_events) * 2,
    }
    log(
        (
            f"Testing on chronological event holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    loaded = _load_hierarchical_micro_cost_edge_artifact(model_path)
    manifest = loaded['manifest']
    stage1_model = loaded['stage1_model']
    stage2_model = loaded['stage2_model']
    threshold_payload = _resolve_micro_cost_edge_v3_event_threshold(
        stage1_model,
        stage2_model,
        manifest,
        sequence_dataset,
        train_events=train_events,
        validation_events=validation_events,
    )
    threshold = float(threshold_payload['threshold'])
    if threshold_payload['source'] != 'artifact_metadata':
        log(
            (
                f"Recalibrated hierarchical event threshold at {threshold:.3f} "
                f"from {threshold_payload['source']}."
            ),
            progress=0.32,
        )

    test_slice = slice(train_events + validation_events, total_events)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')

    test_tradability_scores, X_test_long_stage1, X_test_short_stage1 = _predict_micro_cost_edge_v3_tradability_scores(
        stage1_model,
        sequence_dataset['X_long'][test_slice],
        sequence_dataset['X_short'][test_slice],
    )
    X_test_long_stage2 = stage2_model.transform_features(sequence_dataset['X_long'][test_slice])
    X_test_short_stage2 = stage2_model.transform_features(sequence_dataset['X_short'][test_slice])
    test_long_scores = stage2_model.predict_probabilities(X_test_long_stage2)[:, 1]
    test_short_scores = stage2_model.predict_probabilities(X_test_short_stage2)[:, 1]
    event_code_to_index = {
        int(code): index
        for index, code in enumerate(sequence_dataset['event_class_codes'])
    }
    y_test_event_codes = np.asarray(sequence_dataset['y_event_code'][test_slice], dtype=int)
    y_test_event_indices = np.asarray([
        event_code_to_index[int(code)]
        for code in y_test_event_codes
    ], dtype=int)
    predicted_indices, combined_probabilities = _combine_micro_cost_edge_hierarchical_predictions(
        test_tradability_scores,
        test_long_scores,
        test_short_scores,
        threshold=threshold,
        class_codes=sequence_dataset['event_class_codes'],
        class_labels=sequence_dataset['event_class_labels'],
    )
    metrics = _augment_micro_cost_edge_metrics(_evaluate_class_predictions(
        y_test_event_indices,
        predicted_indices,
        class_codes=sequence_dataset['event_class_codes'],
        class_labels=sequence_dataset['event_class_labels'],
        probabilities=combined_probabilities,
    ))
    metrics['threshold'] = float(threshold)
    metrics['threshold_source'] = str(threshold_payload['source'])
    metrics['threshold_selection_version'] = int(threshold_payload['version'])
    threshold_validation_metrics = dict(threshold_payload.get('validation_metrics') or {})
    if threshold_validation_metrics:
        metrics['threshold_validation_macro_f1'] = float(threshold_validation_metrics.get('macro_f1') or 0.0)
        metrics['threshold_validation_tradability_f1'] = float(threshold_validation_metrics.get('tradability_f1') or 0.0)
        metrics['threshold_validation_directional_edge_macro_f1'] = float(
            threshold_validation_metrics.get('directional_edge_macro_f1') or 0.0
        )
        metrics['threshold_validation_predicted_tradability_rate'] = float(
            threshold_validation_metrics.get('predicted_tradability_rate') or 0.0
        )
        metrics['threshold_validation_actual_tradability_rate'] = float(
            threshold_validation_metrics.get('actual_tradability_rate') or 0.0
        )

    y_test_stage1 = np.concatenate([
        np.where(y_test_event_codes != 0, 1, 0),
        np.where(y_test_event_codes != 0, 1, 0),
    ], axis=0)
    X_test_stage1 = np.concatenate([X_test_long_stage1, X_test_short_stage1], axis=0)
    metrics['stage1_test'] = stage1_model.evaluate(X_test_stage1, y_test_stage1)
    test_edge_mask = y_test_event_codes != 0
    if np.any(test_edge_mask):
        X_test_stage2 = np.concatenate([X_test_long_stage2[test_edge_mask], X_test_short_stage2[test_edge_mask]], axis=0)
        y_test_stage2 = np.concatenate([
            sequence_dataset['y_long'][test_slice][test_edge_mask],
            sequence_dataset['y_short'][test_slice][test_edge_mask],
        ], axis=0)
        metrics['stage2_test'] = stage2_model.evaluate(X_test_stage2, y_test_stage2)
    else:
        metrics['stage2_test'] = {}

    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(manifest.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int((loaded['stage1_metadata'] or {}).get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int((loaded['stage1_metadata'] or {}).get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int((loaded['stage1_metadata'] or {}).get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 5))
    metrics['target_mode'] = str(config.get('targetMode', 'micro_cost_edge_hierarchical_classification') or 'micro_cost_edge_hierarchical_classification').strip().lower()
    metrics['pip_size'] = float(config.get('pipSize', 0.0001))
    metrics['round_trip_cost_pips'] = float(config.get('roundTripCostPips', 1.6))
    metrics['target_cost_edge_multiple'] = float(config.get('targetCostEdgeMultiple', 1.75))
    metrics['candidate_summary'] = manifest.get('candidate_summary') or sequence_dataset.get('candidate_summary') or {}
    metrics['class_weight_mode'] = str(manifest.get('class_weight_mode', config.get('classWeightMode', 'none')) or 'none').strip().lower()
    metrics['class_weight_exponent'] = float(manifest.get('class_weight_exponent', config.get('classWeightExponent', 1.0)))
    metrics['neutral_retention'] = float(manifest.get('neutral_retention', config.get('neutralRetention', 1.0)))
    metrics['gate_class_tradable_edge_f1'] = float(metrics['stage1_test'].get('class_tradable_edge_f1') or 0.0)
    metrics['gate_class_no_edge_recall'] = float(metrics['stage1_test'].get('class_no_edge_recall') or 0.0)
    metrics['gate_macro_f1'] = float(metrics['stage1_test'].get('macro_f1') or 0.0)
    metrics['gate_accuracy'] = float(metrics['stage1_test'].get('accuracy') or 0.0)
    metrics['side_class_edge_for_side_f1'] = float(metrics['stage2_test'].get('class_edge_for_side_f1') or 0.0)
    metrics['side_class_not_edge_for_side_recall'] = float(metrics['stage2_test'].get('class_not_edge_for_side_recall') or 0.0)
    metrics['side_macro_f1'] = float(metrics['stage2_test'].get('macro_f1') or 0.0)
    metrics['side_accuracy'] = float(metrics['stage2_test'].get('accuracy') or 0.0)
    score = float(
        metrics.get('directional_edge_macro_f1')
        or metrics.get('macro_f1')
        or 0.0
    )
    log(f'Holdout finished with hierarchical directional edge F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_micro_cost_edge_cnn_v4_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = 'micro_cost_edge_cnn_v4'
    next_config.setdefault('targetMode', 'micro_cost_edge_hierarchical_classification')
    return run_micro_cost_edge_cnn_v3_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_micro_cost_edge_cnn_v4_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = 'micro_cost_edge_cnn_v4'
    next_config.setdefault('targetMode', 'micro_cost_edge_hierarchical_classification')
    return run_micro_cost_edge_cnn_v3_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_micro_cost_edge_cnn_v5_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = 'micro_cost_edge_cnn_v5'
    next_config.setdefault('targetMode', 'micro_cost_edge_side_classification')
    return run_micro_cost_edge_cnn_v2_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_micro_cost_edge_cnn_v5_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = 'micro_cost_edge_cnn_v5'
    next_config.setdefault('targetMode', 'micro_cost_edge_side_classification')
    return run_micro_cost_edge_cnn_v2_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v3_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    network_id = str(config.get('networkId') or 'candle_reversal_cnn_v3').strip() or 'candle_reversal_cnn_v3'
    log('Preparing hierarchical candle reversal feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    log(f'Hierarchical candle reversal dataset built with {sequence_dataset["rows"]} rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('Hierarchical candle reversal training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    class_codes = list(sequence_dataset['class_codes'])
    class_labels = dict(sequence_dataset['class_labels'])
    if 0 not in class_codes or -1 not in class_codes or 1 not in class_codes:
        raise ValueError('Hierarchical candle reversal training requires class codes -1, 0 and 1.')
    neutral_class_index = class_codes.index(0)
    bearish_class_index = class_codes.index(-1)
    bullish_class_index = class_codes.index(1)

    X_train_full = sequence_dataset['X'][:train_rows]
    y_train_full = sequence_dataset['y_class'][:train_rows]
    context_train_full = sequence_dataset['target_context'][:train_rows]
    X_validation_full = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation_full = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]
    context_validation_full = sequence_dataset['target_context'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    stage1_gate_profile = _stage1_gate_profile_for_network(network_id)

    y_train_stage1, stage1_target_summary = _build_stage1_targets_for_network(
        network_id,
        y_train_full,
        context_train_full,
        neutral_class_index=neutral_class_index,
        config=config,
    )
    y_validation_stage1, _stage1_validation_target_summary = _build_stage1_targets_for_network(
        network_id,
        y_validation_full,
        context_validation_full,
        neutral_class_index=neutral_class_index,
        config=config,
    )
    X_train_stage1, y_train_stage1, stage1_rebalance_summary = _rebalance_sequence_classes(
        X_train_full,
        y_train_stage1,
        seed=training_config.seed,
        retained_class_index=0,
        retention=training_config.neutral_retention,
    )
    stage1_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_stage1,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Stage 1 target prepared: '
            f'positives {stage1_target_summary["positive_rows_before"]} -> {stage1_target_summary["positive_rows_after"]}, '
            'then balanced as '
            f'rows {stage1_rebalance_summary["rows_before"]} -> {stage1_rebalance_summary["rows_after"]}, '
            f'class weights={stage1_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )
    stage1_model = TemporalConvolutionalClassifier(
        input_features=X_train_stage1.shape[2],
        sequence_length=X_train_stage1.shape[1],
        class_codes=[0, 1],
        class_labels=dict(stage1_gate_profile['class_labels']),
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    stage1_model.feature_columns = list(sequence_dataset['feature_columns'])
    stage1_model.fit_normalizer(X_train_stage1)
    X_train_stage1 = stage1_model.transform_features(X_train_stage1)
    X_validation_stage1 = stage1_model.transform_features(X_validation_full)

    log('Training stage 1 reversal detector.', progress=0.34)
    stage1_model.train(
        X_train_stage1,
        y_train_stage1,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_stage1,
        y_validation=y_validation_stage1,
        class_weights=stage1_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label=_candle_reversal_phase_label(network_id, 'stage 1', fallback='v3'),
            detail=(
                f"stage 1 epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    stage1_validation_metrics = stage1_model.evaluate(X_validation_stage1, y_validation_stage1)

    direction_train_mask = np.asarray(y_train_full, dtype=int) != neutral_class_index
    direction_validation_mask = np.asarray(y_validation_full, dtype=int) != neutral_class_index
    X_train_stage2 = np.asarray(X_train_full, dtype=float)[direction_train_mask]
    y_train_stage2 = np.where(np.asarray(y_train_full, dtype=int)[direction_train_mask] == bearish_class_index, 0, 1)
    X_validation_stage2 = np.asarray(X_validation_full, dtype=float)[direction_validation_mask]
    y_validation_stage2 = np.where(np.asarray(y_validation_full, dtype=int)[direction_validation_mask] == bearish_class_index, 0, 1)
    if len(y_train_stage2) < 40:
        raise ValueError('Stage 2 directional training requires at least 40 reversal rows in the train split.')
    if len(y_validation_stage2) < 10:
        raise ValueError('Stage 2 directional validation requires at least 10 reversal rows in the validation split.')

    stage2_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_stage2,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Stage 2 balance prepared: '
            f'rows {len(y_train_stage2)} -> {len(y_train_stage2)}, '
            f'class weights={stage2_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.58,
    )
    stage2_model = TemporalConvolutionalClassifier(
        input_features=X_train_stage2.shape[2],
        sequence_length=X_train_stage2.shape[1],
        class_codes=[-1, 1],
        class_labels={-1: 'bearish_reversal', 1: 'bullish_reversal'},
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed + 17,
    )
    stage2_model.feature_columns = list(sequence_dataset['feature_columns'])
    stage2_model.fit_normalizer(X_train_stage2)
    X_train_stage2 = stage2_model.transform_features(X_train_stage2)
    X_validation_stage2 = stage2_model.transform_features(X_validation_stage2)

    log('Training stage 2 reversal direction classifier.', progress=0.6)
    stage2_model.train(
        X_train_stage2,
        y_train_stage2,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_stage2,
        y_validation=y_validation_stage2,
        class_weights=stage2_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.6 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label=_candle_reversal_phase_label(network_id, 'stage 2', fallback='v3'),
            detail=(
                f"stage 2 epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    stage2_validation_metrics = stage2_model.evaluate(X_validation_stage2, y_validation_stage2)

    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    log('Selecting hierarchical reversal threshold on validation split.', progress=0.84)
    validation_reversal_probabilities = stage1_model.predict_probabilities(X_validation_stage1)
    validation_direction_probabilities = stage2_model.predict_probabilities(stage2_model.transform_features(X_validation_full))
    threshold_search = _search_hierarchical_reversal_threshold(
        y_validation_full,
        validation_reversal_probabilities,
        validation_direction_probabilities,
        class_codes=class_codes,
        class_labels=class_labels,
        neutral_class_index=neutral_class_index,
        bearish_class_index=bearish_class_index,
        bullish_class_index=bullish_class_index,
    )
    selected_threshold = float(threshold_search['threshold'])
    validation_metrics = dict(threshold_search['metrics'])
    validation_metrics['selected_reversal_threshold'] = selected_threshold
    log(
        f'Validation threshold selected at {selected_threshold:.2f} with macro F1 {validation_metrics.get("macro_f1", 0.0):.4f}.',
        progress=0.9,
    )

    log('Training finished. Saving hierarchical model.', progress=0.92)
    manifest = {
        'artifact_type': _candle_reversal_artifact_type(network_id, fallback='v3'),
        'network_id': network_id,
        'feature_columns': list(sequence_dataset['feature_columns']),
        'split_sizes': split_sizes,
        'class_codes': class_codes,
        'class_labels': class_labels,
        'neutral_class_index': int(neutral_class_index),
        'bearish_class_index': int(bearish_class_index),
        'bullish_class_index': int(bullish_class_index),
        'selected_reversal_threshold': selected_threshold,
        'class_weight_mode': training_config.class_weight_mode,
        'class_weight_exponent': float(training_config.class_weight_exponent),
        'neutral_retention': float(training_config.neutral_retention),
        'target_filter_summary': sequence_dataset.get('target_filter_summary'),
        'stage1_target_summary': stage1_target_summary,
        'stage1_rebalance_summary': stage1_rebalance_summary,
        'stage1_class_weight_vector': stage1_class_weight_vector.tolist(),
        'stage2_class_weight_vector': stage2_class_weight_vector.tolist(),
        'stage1_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': dict(stage1_gate_profile['class_labels']),
            'stage_role': str(stage1_gate_profile['stage_role']),
        },
        'stage2_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [-1, 1],
            'class_labels': {-1: 'bearish_reversal', 1: 'bullish_reversal'},
            'stage_role': 'reversal_direction',
        },
    }
    artifact_path = _save_hierarchical_candle_reversal_artifact(
        model_base_path,
        stage1_model=stage1_model,
        stage2_model=stage2_model,
        manifest=manifest,
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'stage1_validation': stage1_validation_metrics,
            'stage2_validation': stage2_validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 6)),
            'target_mode': str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower(),
            'pretrend_lookback': int(config.get('pretrendLookback', 6)),
            'pretrend_threshold': float(config.get('pretrendThreshold', 1.2)),
            'reversal_threshold': float(config.get('reversalThreshold', 1.0)),
            'dominance_ratio': float(config.get('dominanceRatio', 1.35)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': class_codes,
            'class_labels': class_labels,
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'neutral_retention': float(training_config.neutral_retention),
            'target_filter_summary': sequence_dataset.get('target_filter_summary'),
            'stage1_target_summary': stage1_target_summary,
            'stage1_rebalance_summary': stage1_rebalance_summary,
            'stage1_class_weight_vector': stage1_class_weight_vector.tolist(),
            'stage2_class_weight_vector': stage2_class_weight_vector.tolist(),
            'selected_reversal_threshold': selected_threshold,
        },
        'score': score,
    }


def run_candle_reversal_cnn_v3_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing hierarchical candle reversal test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    loaded = _load_hierarchical_candle_reversal_artifact(model_path)
    manifest = loaded['manifest']
    stage1_model = loaded['stage1_model']
    stage2_model = loaded['stage2_model']
    class_codes = list(manifest.get('class_codes') or sequence_dataset['class_codes'])
    class_labels = dict(manifest.get('class_labels') or sequence_dataset['class_labels'])
    neutral_class_index = int(manifest.get('neutral_class_index', class_codes.index(0)))
    bearish_class_index = int(manifest.get('bearish_class_index', class_codes.index(-1)))
    bullish_class_index = int(manifest.get('bullish_class_index', class_codes.index(1)))
    selected_threshold = float(manifest.get('selected_reversal_threshold', 0.5))
    network_id = str(manifest.get('network_id') or config.get('networkId') or 'candle_reversal_cnn_v3').strip() or 'candle_reversal_cnn_v3'

    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    context_test = sequence_dataset['target_context'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test_stage1 = stage1_model.transform_features(X_test)
    X_test_stage2 = stage2_model.transform_features(X_test)
    reversal_probabilities = stage1_model.predict_probabilities(X_test_stage1)
    direction_probabilities = stage2_model.predict_probabilities(X_test_stage2)
    predicted_indices, combined_probabilities = _combine_hierarchical_reversal_predictions(
        reversal_probabilities,
        direction_probabilities,
        threshold=selected_threshold,
        neutral_class_index=neutral_class_index,
        bearish_class_index=bearish_class_index,
        bullish_class_index=bullish_class_index,
    )
    metrics = _evaluate_class_predictions(
        y_test,
        predicted_indices,
        class_codes=class_codes,
        class_labels=class_labels,
        probabilities=combined_probabilities,
    )
    y_test_stage1, stage1_test_target_summary = _build_stage1_targets_for_network(
        network_id,
        y_test,
        context_test,
        neutral_class_index=neutral_class_index,
        config=config,
    )
    metrics['stage1_test'] = stage1_model.evaluate(X_test_stage1, y_test_stage1)
    direction_test_mask = np.asarray(y_test, dtype=int) != neutral_class_index
    if np.any(direction_test_mask):
        y_test_stage2 = np.where(np.asarray(y_test, dtype=int)[direction_test_mask] == bearish_class_index, 0, 1)
        metrics['stage2_test'] = stage2_model.evaluate(X_test_stage2[direction_test_mask], y_test_stage2)
    else:
        metrics['stage2_test'] = {}
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(manifest.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int((loaded['stage1_metadata'] or {}).get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int((loaded['stage1_metadata'] or {}).get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int((loaded['stage1_metadata'] or {}).get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 6))
    metrics['target_mode'] = str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower()
    metrics['pretrend_lookback'] = int(config.get('pretrendLookback', 6))
    metrics['pretrend_threshold'] = float(config.get('pretrendThreshold', 1.2))
    metrics['reversal_threshold'] = float(config.get('reversalThreshold', 1.0))
    metrics['dominance_ratio'] = float(config.get('dominanceRatio', 1.35))
    metrics['class_weight_mode'] = str(manifest.get('class_weight_mode', config.get('classWeightMode', 'none')) or 'none').strip().lower()
    metrics['class_weight_exponent'] = float(manifest.get('class_weight_exponent', config.get('classWeightExponent', 1.0)))
    metrics['neutral_retention'] = float(manifest.get('neutral_retention', config.get('neutralRetention', 1.0)))
    metrics['target_filter_summary'] = manifest.get('target_filter_summary')
    metrics['stage1_target_summary'] = manifest.get('stage1_target_summary')
    metrics['stage1_test_target_summary'] = stage1_test_target_summary
    metrics['selected_reversal_threshold'] = selected_threshold
    metrics['stage1_neutral_pretrend_ceiling'] = float(manifest.get('stage1_neutral_pretrend_ceiling', config.get('stage1NeutralPretrendCeiling', 0.85)))
    metrics['stage1_neutral_excursion_ceiling'] = float(manifest.get('stage1_neutral_excursion_ceiling', config.get('stage1NeutralExcursionCeiling', 0.85)))
    metrics['stage1_positive_pretrend_floor'] = float(manifest.get('stage1_positive_pretrend_floor', config.get('stage1PositivePretrendFloor', 0.0)))
    metrics['stage1_positive_excursion_floor'] = float(manifest.get('stage1_positive_excursion_floor', config.get('stage1PositiveExcursionFloor', 0.0)))
    metrics['stage1_filter_summary'] = manifest.get('stage1_filter_summary')
    metrics['stage1_rebalance_summary'] = manifest.get('stage1_rebalance_summary')
    metrics['stage1_class_weight_vector'] = manifest.get('stage1_class_weight_vector')
    metrics['stage2_class_weight_vector'] = manifest.get('stage2_class_weight_vector')
    score = float(metrics.get('macro_f1') or 0.0)
    log(f'Test finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_candle_reversal_cnn_v4_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing hierarchical candle reversal v4 feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    log(f'Hierarchical candle reversal v4 dataset built with {sequence_dataset["rows"]} rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('Hierarchical candle reversal v4 training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    class_codes = list(sequence_dataset['class_codes'])
    class_labels = dict(sequence_dataset['class_labels'])
    if 0 not in class_codes or -1 not in class_codes or 1 not in class_codes:
        raise ValueError('Hierarchical candle reversal v4 training requires class codes -1, 0 and 1.')
    neutral_class_index = class_codes.index(0)
    bearish_class_index = class_codes.index(-1)
    bullish_class_index = class_codes.index(1)

    X_train_full = sequence_dataset['X'][:train_rows]
    y_train_full = sequence_dataset['y_class'][:train_rows]
    context_train_full = sequence_dataset['target_context'][:train_rows]
    X_validation_full = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation_full = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    stage1_neutral_pretrend_ceiling = max(0.0, float(config.get('stage1NeutralPretrendCeiling', 0.85)))
    stage1_neutral_excursion_ceiling = max(0.0, float(config.get('stage1NeutralExcursionCeiling', 0.85)))

    y_train_stage1 = (np.asarray(y_train_full, dtype=int) != neutral_class_index).astype(int)
    y_validation_stage1 = (np.asarray(y_validation_full, dtype=int) != neutral_class_index).astype(int)
    X_train_stage1, y_train_stage1, context_train_stage1, stage1_filter_summary = _filter_stage1_gate_examples(
        X_train_full,
        y_train_stage1,
        context_train_full,
        neutral_class_index=0,
        pretrend_threshold=float(config.get('pretrendThreshold', 1.2)),
        reversal_threshold=float(config.get('reversalThreshold', 1.0)),
        neutral_pretrend_ceiling=stage1_neutral_pretrend_ceiling,
        neutral_excursion_ceiling=stage1_neutral_excursion_ceiling,
    )
    X_train_stage1, y_train_stage1, stage1_rebalance_summary = _rebalance_sequence_classes(
        X_train_stage1,
        y_train_stage1,
        seed=training_config.seed,
        retained_class_index=0,
        retention=training_config.neutral_retention,
    )
    stage1_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_stage1,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Stage 1 gate prepared: '
            f'filtered rows {stage1_filter_summary["rows_before"]} -> {stage1_filter_summary["rows_after"]}, '
            f'rebalanced rows {stage1_rebalance_summary["rows_before"]} -> {stage1_rebalance_summary["rows_after"]}, '
            f'class weights={stage1_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )
    stage1_model = TemporalConvolutionalClassifier(
        input_features=X_train_stage1.shape[2],
        sequence_length=X_train_stage1.shape[1],
        class_codes=[0, 1],
        class_labels={0: 'no_reversal', 1: 'reversal'},
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    stage1_model.feature_columns = list(sequence_dataset['feature_columns'])
    stage1_model.fit_normalizer(X_train_stage1)
    X_train_stage1 = stage1_model.transform_features(X_train_stage1)
    X_validation_stage1 = stage1_model.transform_features(X_validation_full)

    log('Training stage 1 reversal detector.', progress=0.34)
    stage1_model.train(
        X_train_stage1,
        y_train_stage1,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_stage1,
        y_validation=y_validation_stage1,
        class_weights=stage1_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Candle reversal v4 · stage 1',
            detail=(
                f"stage 1 epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    stage1_validation_metrics = stage1_model.evaluate(X_validation_stage1, y_validation_stage1)

    direction_train_mask = np.asarray(y_train_full, dtype=int) != neutral_class_index
    direction_validation_mask = np.asarray(y_validation_full, dtype=int) != neutral_class_index
    X_train_stage2 = np.asarray(X_train_full, dtype=float)[direction_train_mask]
    y_train_stage2 = np.where(np.asarray(y_train_full, dtype=int)[direction_train_mask] == bearish_class_index, 0, 1)
    X_validation_stage2 = np.asarray(X_validation_full, dtype=float)[direction_validation_mask]
    y_validation_stage2 = np.where(np.asarray(y_validation_full, dtype=int)[direction_validation_mask] == bearish_class_index, 0, 1)
    if len(y_train_stage2) < 40:
        raise ValueError('Stage 2 directional training requires at least 40 reversal rows in the train split.')
    if len(y_validation_stage2) < 10:
        raise ValueError('Stage 2 directional validation requires at least 10 reversal rows in the validation split.')

    stage2_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_stage2,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Stage 2 balance prepared: '
            f'rows {len(y_train_stage2)} -> {len(y_train_stage2)}, '
            f'class weights={stage2_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.58,
    )
    stage2_model = TemporalConvolutionalClassifier(
        input_features=X_train_stage2.shape[2],
        sequence_length=X_train_stage2.shape[1],
        class_codes=[-1, 1],
        class_labels={-1: 'bearish_reversal', 1: 'bullish_reversal'},
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed + 17,
    )
    stage2_model.feature_columns = list(sequence_dataset['feature_columns'])
    stage2_model.fit_normalizer(X_train_stage2)
    X_train_stage2 = stage2_model.transform_features(X_train_stage2)
    X_validation_stage2 = stage2_model.transform_features(X_validation_stage2)

    log('Training stage 2 reversal direction classifier.', progress=0.6)
    stage2_model.train(
        X_train_stage2,
        y_train_stage2,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_stage2,
        y_validation=y_validation_stage2,
        class_weights=stage2_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.6 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Candle reversal v4 · stage 2',
            detail=(
                f"stage 2 epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    stage2_validation_metrics = stage2_model.evaluate(X_validation_stage2, y_validation_stage2)

    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    log('Selecting hierarchical reversal threshold on validation split.', progress=0.84)
    validation_reversal_probabilities = stage1_model.predict_probabilities(X_validation_stage1)
    validation_direction_probabilities = stage2_model.predict_probabilities(stage2_model.transform_features(X_validation_full))
    threshold_search = _search_hierarchical_reversal_threshold(
        y_validation_full,
        validation_reversal_probabilities,
        validation_direction_probabilities,
        class_codes=class_codes,
        class_labels=class_labels,
        neutral_class_index=neutral_class_index,
        bearish_class_index=bearish_class_index,
        bullish_class_index=bullish_class_index,
    )
    selected_threshold = float(threshold_search['threshold'])
    validation_metrics = dict(threshold_search['metrics'])
    validation_metrics['selected_reversal_threshold'] = selected_threshold
    log(
        f'Validation threshold selected at {selected_threshold:.2f} with macro F1 {validation_metrics.get("macro_f1", 0.0):.4f}.',
        progress=0.9,
    )

    log('Training finished. Saving hierarchical model.', progress=0.92)
    manifest = {
        'artifact_type': 'hierarchical_candle_reversal_v4',
        'network_id': str(config.get('networkId') or 'candle_reversal_cnn_v4'),
        'feature_columns': list(sequence_dataset['feature_columns']),
        'split_sizes': split_sizes,
        'class_codes': class_codes,
        'class_labels': class_labels,
        'neutral_class_index': int(neutral_class_index),
        'bearish_class_index': int(bearish_class_index),
        'bullish_class_index': int(bullish_class_index),
        'selected_reversal_threshold': selected_threshold,
        'class_weight_mode': training_config.class_weight_mode,
        'class_weight_exponent': float(training_config.class_weight_exponent),
        'neutral_retention': float(training_config.neutral_retention),
        'stage1_neutral_pretrend_ceiling': stage1_neutral_pretrend_ceiling,
        'stage1_neutral_excursion_ceiling': stage1_neutral_excursion_ceiling,
        'stage1_filter_summary': stage1_filter_summary,
        'stage1_rebalance_summary': stage1_rebalance_summary,
        'stage1_class_weight_vector': stage1_class_weight_vector.tolist(),
        'stage2_class_weight_vector': stage2_class_weight_vector.tolist(),
        'stage1_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': {0: 'no_reversal', 1: 'reversal'},
            'stage_role': 'reversal_gate_filtered',
        },
        'stage2_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [-1, 1],
            'class_labels': {-1: 'bearish_reversal', 1: 'bullish_reversal'},
            'stage_role': 'reversal_direction',
        },
    }
    artifact_path = _save_hierarchical_candle_reversal_artifact(
        model_base_path,
        stage1_model=stage1_model,
        stage2_model=stage2_model,
        manifest=manifest,
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'stage1_validation': stage1_validation_metrics,
            'stage2_validation': stage2_validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 6)),
            'target_mode': str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower(),
            'pretrend_lookback': int(config.get('pretrendLookback', 6)),
            'pretrend_threshold': float(config.get('pretrendThreshold', 1.2)),
            'reversal_threshold': float(config.get('reversalThreshold', 1.0)),
            'dominance_ratio': float(config.get('dominanceRatio', 1.35)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': class_codes,
            'class_labels': class_labels,
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'neutral_retention': float(training_config.neutral_retention),
            'stage1_neutral_pretrend_ceiling': stage1_neutral_pretrend_ceiling,
            'stage1_neutral_excursion_ceiling': stage1_neutral_excursion_ceiling,
            'stage1_filter_summary': stage1_filter_summary,
            'stage1_rebalance_summary': stage1_rebalance_summary,
            'stage1_class_weight_vector': stage1_class_weight_vector.tolist(),
            'stage2_class_weight_vector': stage2_class_weight_vector.tolist(),
            'selected_reversal_threshold': selected_threshold,
        },
        'score': score,
    }


def run_candle_reversal_cnn_v4_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_test(
        config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v5_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing hierarchical candle reversal v5 feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    log(f'Hierarchical candle reversal v5 dataset built with {sequence_dataset["rows"]} rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('Hierarchical candle reversal v5 training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    class_codes = list(sequence_dataset['class_codes'])
    class_labels = dict(sequence_dataset['class_labels'])
    if 0 not in class_codes or -1 not in class_codes or 1 not in class_codes:
        raise ValueError('Hierarchical candle reversal v5 training requires class codes -1, 0 and 1.')
    neutral_class_index = class_codes.index(0)
    bearish_class_index = class_codes.index(-1)
    bullish_class_index = class_codes.index(1)

    X_train_full = sequence_dataset['X'][:train_rows]
    y_train_full = sequence_dataset['y_class'][:train_rows]
    context_train_full = sequence_dataset['target_context'][:train_rows]
    X_validation_full = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation_full = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(config)
    stage1_neutral_pretrend_ceiling = max(0.0, float(config.get('stage1NeutralPretrendCeiling', 0.85)))
    stage1_neutral_excursion_ceiling = max(0.0, float(config.get('stage1NeutralExcursionCeiling', 0.85)))
    stage1_positive_pretrend_floor = max(0.0, float(config.get('stage1PositivePretrendFloor', 1.05)))
    stage1_positive_excursion_floor = max(0.0, float(config.get('stage1PositiveExcursionFloor', 1.1)))

    y_train_stage1 = (np.asarray(y_train_full, dtype=int) != neutral_class_index).astype(int)
    y_validation_stage1 = (np.asarray(y_validation_full, dtype=int) != neutral_class_index).astype(int)
    X_train_stage1, y_train_stage1, context_train_stage1, stage1_filter_summary = _filter_stage1_gate_examples(
        X_train_full,
        y_train_stage1,
        context_train_full,
        neutral_class_index=0,
        pretrend_threshold=float(config.get('pretrendThreshold', 1.2)),
        reversal_threshold=float(config.get('reversalThreshold', 1.0)),
        neutral_pretrend_ceiling=stage1_neutral_pretrend_ceiling,
        neutral_excursion_ceiling=stage1_neutral_excursion_ceiling,
        positive_pretrend_floor=stage1_positive_pretrend_floor,
        positive_excursion_floor=stage1_positive_excursion_floor,
    )
    X_train_stage1, y_train_stage1, stage1_rebalance_summary = _rebalance_sequence_classes(
        X_train_stage1,
        y_train_stage1,
        seed=training_config.seed,
        retained_class_index=0,
        retention=training_config.neutral_retention,
    )
    stage1_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_stage1,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Stage 1 gate prepared: '
            f'filtered rows {stage1_filter_summary["rows_before"]} -> {stage1_filter_summary["rows_after"]}, '
            f'rebalanced rows {stage1_rebalance_summary["rows_before"]} -> {stage1_rebalance_summary["rows_after"]}, '
            f'class weights={stage1_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )
    stage1_model = TemporalConvolutionalClassifier(
        input_features=X_train_stage1.shape[2],
        sequence_length=X_train_stage1.shape[1],
        class_codes=[0, 1],
        class_labels={0: 'no_reversal', 1: 'reversal'},
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    stage1_model.feature_columns = list(sequence_dataset['feature_columns'])
    stage1_model.fit_normalizer(X_train_stage1)
    X_train_stage1 = stage1_model.transform_features(X_train_stage1)
    X_validation_stage1 = stage1_model.transform_features(X_validation_full)

    log('Training stage 1 reversal detector.', progress=0.34)
    stage1_model.train(
        X_train_stage1,
        y_train_stage1,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_stage1,
        y_validation=y_validation_stage1,
        class_weights=stage1_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Candle reversal v5 · stage 1',
            detail=(
                f"stage 1 epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    stage1_validation_metrics = stage1_model.evaluate(X_validation_stage1, y_validation_stage1)

    direction_train_mask = np.asarray(y_train_full, dtype=int) != neutral_class_index
    direction_validation_mask = np.asarray(y_validation_full, dtype=int) != neutral_class_index
    X_train_stage2 = np.asarray(X_train_full, dtype=float)[direction_train_mask]
    y_train_stage2 = np.where(np.asarray(y_train_full, dtype=int)[direction_train_mask] == bearish_class_index, 0, 1)
    X_validation_stage2 = np.asarray(X_validation_full, dtype=float)[direction_validation_mask]
    y_validation_stage2 = np.where(np.asarray(y_validation_full, dtype=int)[direction_validation_mask] == bearish_class_index, 0, 1)
    if len(y_train_stage2) < 40:
        raise ValueError('Stage 2 directional training requires at least 40 reversal rows in the train split.')
    if len(y_validation_stage2) < 10:
        raise ValueError('Stage 2 directional validation requires at least 10 reversal rows in the validation split.')

    stage2_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_stage2,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Stage 2 balance prepared: '
            f'rows {len(y_train_stage2)} -> {len(y_train_stage2)}, '
            f'class weights={stage2_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.58,
    )
    stage2_model = TemporalConvolutionalClassifier(
        input_features=X_train_stage2.shape[2],
        sequence_length=X_train_stage2.shape[1],
        class_codes=[-1, 1],
        class_labels={-1: 'bearish_reversal', 1: 'bullish_reversal'},
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed + 17,
    )
    stage2_model.feature_columns = list(sequence_dataset['feature_columns'])
    stage2_model.fit_normalizer(X_train_stage2)
    X_train_stage2 = stage2_model.transform_features(X_train_stage2)
    X_validation_stage2 = stage2_model.transform_features(X_validation_stage2)

    log('Training stage 2 reversal direction classifier.', progress=0.6)
    stage2_model.train(
        X_train_stage2,
        y_train_stage2,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_stage2,
        y_validation=y_validation_stage2,
        class_weights=stage2_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.6 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Candle reversal v5 · stage 2',
            detail=(
                f"stage 2 epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    stage2_validation_metrics = stage2_model.evaluate(X_validation_stage2, y_validation_stage2)

    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    log('Selecting hierarchical reversal threshold on validation split.', progress=0.84)
    validation_reversal_probabilities = stage1_model.predict_probabilities(X_validation_stage1)
    validation_direction_probabilities = stage2_model.predict_probabilities(stage2_model.transform_features(X_validation_full))
    threshold_search = _search_hierarchical_reversal_threshold(
        y_validation_full,
        validation_reversal_probabilities,
        validation_direction_probabilities,
        class_codes=class_codes,
        class_labels=class_labels,
        neutral_class_index=neutral_class_index,
        bearish_class_index=bearish_class_index,
        bullish_class_index=bullish_class_index,
    )
    selected_threshold = float(threshold_search['threshold'])
    validation_metrics = dict(threshold_search['metrics'])
    validation_metrics['selected_reversal_threshold'] = selected_threshold
    log(
        f'Validation threshold selected at {selected_threshold:.2f} with macro F1 {validation_metrics.get("macro_f1", 0.0):.4f}.',
        progress=0.9,
    )

    log('Training finished. Saving hierarchical model.', progress=0.92)
    manifest = {
        'artifact_type': 'hierarchical_candle_reversal_v5',
        'network_id': str(config.get('networkId') or 'candle_reversal_cnn_v5'),
        'feature_columns': list(sequence_dataset['feature_columns']),
        'split_sizes': split_sizes,
        'class_codes': class_codes,
        'class_labels': class_labels,
        'neutral_class_index': int(neutral_class_index),
        'bearish_class_index': int(bearish_class_index),
        'bullish_class_index': int(bullish_class_index),
        'selected_reversal_threshold': selected_threshold,
        'class_weight_mode': training_config.class_weight_mode,
        'class_weight_exponent': float(training_config.class_weight_exponent),
        'neutral_retention': float(training_config.neutral_retention),
        'stage1_neutral_pretrend_ceiling': stage1_neutral_pretrend_ceiling,
        'stage1_neutral_excursion_ceiling': stage1_neutral_excursion_ceiling,
        'stage1_positive_pretrend_floor': stage1_positive_pretrend_floor,
        'stage1_positive_excursion_floor': stage1_positive_excursion_floor,
        'stage1_filter_summary': stage1_filter_summary,
        'stage1_rebalance_summary': stage1_rebalance_summary,
        'stage1_class_weight_vector': stage1_class_weight_vector.tolist(),
        'stage2_class_weight_vector': stage2_class_weight_vector.tolist(),
        'stage1_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': {0: 'no_reversal', 1: 'reversal'},
            'stage_role': 'reversal_gate_margin',
        },
        'stage2_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [-1, 1],
            'class_labels': {-1: 'bearish_reversal', 1: 'bullish_reversal'},
            'stage_role': 'reversal_direction',
        },
    }
    artifact_path = _save_hierarchical_candle_reversal_artifact(
        model_base_path,
        stage1_model=stage1_model,
        stage2_model=stage2_model,
        manifest=manifest,
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'stage1_validation': stage1_validation_metrics,
            'stage2_validation': stage2_validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 6)),
            'target_mode': str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower(),
            'pretrend_lookback': int(config.get('pretrendLookback', 6)),
            'pretrend_threshold': float(config.get('pretrendThreshold', 1.2)),
            'reversal_threshold': float(config.get('reversalThreshold', 1.0)),
            'dominance_ratio': float(config.get('dominanceRatio', 1.35)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': class_codes,
            'class_labels': class_labels,
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'neutral_retention': float(training_config.neutral_retention),
            'stage1_neutral_pretrend_ceiling': stage1_neutral_pretrend_ceiling,
            'stage1_neutral_excursion_ceiling': stage1_neutral_excursion_ceiling,
            'stage1_positive_pretrend_floor': stage1_positive_pretrend_floor,
            'stage1_positive_excursion_floor': stage1_positive_excursion_floor,
            'stage1_filter_summary': stage1_filter_summary,
            'stage1_rebalance_summary': stage1_rebalance_summary,
            'stage1_class_weight_vector': stage1_class_weight_vector.tolist(),
            'stage2_class_weight_vector': stage2_class_weight_vector.tolist(),
            'selected_reversal_threshold': selected_threshold,
        },
        'score': score,
    }


def run_candle_reversal_cnn_v5_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_test(
        config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v6_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_train(
        config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v6_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_test(
        config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v7_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_train(
        config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v7_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_test(
        config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v7_1_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_train(
        config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v7_1_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_test(
        config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v8_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_train(
        config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v8_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    return run_candle_reversal_cnn_v3_test(
        config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v9_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    network_id = str(config.get('networkId') or 'candle_reversal_cnn_v9').strip() or 'candle_reversal_cnn_v9'
    log('Preparing dual-head candle reversal feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    log(f'Dual-head candle reversal dataset built with {sequence_dataset["rows"]} rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('Dual-head candle reversal training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    class_codes = list(sequence_dataset['class_codes'])
    class_labels = dict(sequence_dataset['class_labels'])
    if 0 not in class_codes or -1 not in class_codes or 1 not in class_codes:
        raise ValueError('Dual-head candle reversal training requires class codes -1, 0 and 1.')
    neutral_class_index = class_codes.index(0)
    bearish_class_index = class_codes.index(-1)
    bullish_class_index = class_codes.index(1)

    X_train_full = sequence_dataset['X'][:train_rows]
    y_train_full = np.asarray(sequence_dataset['y_class'][:train_rows], dtype=int)
    X_validation_full = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation_full = np.asarray(sequence_dataset['y_class'][train_rows:train_rows + validation_rows], dtype=int)

    training_config = _build_supervised_training_config(config)

    y_train_bearish = (y_train_full == bearish_class_index).astype(int)
    y_validation_bearish = (y_validation_full == bearish_class_index).astype(int)
    bearish_head_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_bearish,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Bearish head balance prepared: '
            f'rows {len(y_train_bearish)} -> {len(y_train_bearish)}, '
            f'class weights={bearish_head_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )
    bearish_head_model = TemporalConvolutionalClassifier(
        input_features=X_train_full.shape[2],
        sequence_length=X_train_full.shape[1],
        class_codes=[0, 1],
        class_labels={0: 'rest', 1: 'bearish_setup'},
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    bearish_head_model.feature_columns = list(sequence_dataset['feature_columns'])
    bearish_head_model.fit_normalizer(X_train_full)
    X_train_bearish = bearish_head_model.transform_features(X_train_full)
    X_validation_bearish = bearish_head_model.transform_features(X_validation_full)

    log('Training bearish setup head.', progress=0.34)
    bearish_head_model.train(
        X_train_bearish,
        y_train_bearish,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_bearish,
        y_validation=y_validation_bearish,
        class_weights=bearish_head_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label=_candle_reversal_phase_label(network_id, 'bearish head', fallback='v9'),
            detail=(
                f"bearish head epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    bearish_head_validation_metrics = bearish_head_model.evaluate(X_validation_bearish, y_validation_bearish)

    y_train_bullish = (y_train_full == bullish_class_index).astype(int)
    y_validation_bullish = (y_validation_full == bullish_class_index).astype(int)
    bullish_head_class_weight_vector = _build_inverse_frequency_class_weights(
        y_train_bullish,
        num_classes=2,
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    log(
        (
            'Bullish head balance prepared: '
            f'rows {len(y_train_bullish)} -> {len(y_train_bullish)}, '
            f'class weights={bullish_head_class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.58,
    )
    bullish_head_model = TemporalConvolutionalClassifier(
        input_features=X_train_full.shape[2],
        sequence_length=X_train_full.shape[1],
        class_codes=[0, 1],
        class_labels={0: 'rest', 1: 'bullish_setup'},
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed + 17,
    )
    bullish_head_model.feature_columns = list(sequence_dataset['feature_columns'])
    bullish_head_model.fit_normalizer(X_train_full)
    X_train_bullish = bullish_head_model.transform_features(X_train_full)
    X_validation_bullish = bullish_head_model.transform_features(X_validation_full)

    log('Training bullish setup head.', progress=0.6)
    bullish_head_model.train(
        X_train_bullish,
        y_train_bullish,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation_bullish,
        y_validation=y_validation_bullish,
        class_weights=bullish_head_class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.6 + (0.22 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label=_candle_reversal_phase_label(network_id, 'bullish head', fallback='v9'),
            detail=(
                f"bullish head epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    bullish_head_validation_metrics = bullish_head_model.evaluate(X_validation_bullish, y_validation_bullish)

    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    log('Selecting dual-head thresholds on validation split.', progress=0.84)
    validation_bearish_probabilities = bearish_head_model.predict_probabilities(X_validation_bearish)
    validation_bullish_probabilities = bullish_head_model.predict_probabilities(X_validation_bullish)
    threshold_search = _search_dual_head_reversal_thresholds(
        y_validation_full,
        validation_bearish_probabilities,
        validation_bullish_probabilities,
        class_codes=class_codes,
        class_labels=class_labels,
        neutral_class_index=neutral_class_index,
        bearish_class_index=bearish_class_index,
        bullish_class_index=bullish_class_index,
    )
    selected_bearish_threshold = float(threshold_search['bearish_threshold'])
    selected_bullish_threshold = float(threshold_search['bullish_threshold'])
    validation_metrics = dict(threshold_search['metrics'])
    validation_metrics['selected_bearish_threshold'] = selected_bearish_threshold
    validation_metrics['selected_bullish_threshold'] = selected_bullish_threshold
    log(
        (
            f'Validation thresholds selected at bearish={selected_bearish_threshold:.2f}, '
            f'bullish={selected_bullish_threshold:.2f} with macro F1 {validation_metrics.get("macro_f1", 0.0):.4f}.'
        ),
        progress=0.9,
    )

    log('Training finished. Saving dual-head model.', progress=0.92)
    manifest = {
        'artifact_type': 'dual_head_candle_reversal_v9',
        'network_id': network_id,
        'feature_columns': list(sequence_dataset['feature_columns']),
        'split_sizes': split_sizes,
        'class_codes': class_codes,
        'class_labels': class_labels,
        'neutral_class_index': int(neutral_class_index),
        'bearish_class_index': int(bearish_class_index),
        'bullish_class_index': int(bullish_class_index),
        'selected_bearish_threshold': selected_bearish_threshold,
        'selected_bullish_threshold': selected_bullish_threshold,
        'class_weight_mode': training_config.class_weight_mode,
        'class_weight_exponent': float(training_config.class_weight_exponent),
        'neutral_retention': float(training_config.neutral_retention),
        'target_filter_summary': sequence_dataset.get('target_filter_summary'),
        'bearish_head_class_weight_vector': bearish_head_class_weight_vector.tolist(),
        'bullish_head_class_weight_vector': bullish_head_class_weight_vector.tolist(),
        'stage1_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': {0: 'rest', 1: 'bearish_setup'},
            'stage_role': 'bearish_setup_head',
        },
        'stage2_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': {0: 'rest', 1: 'bullish_setup'},
            'stage_role': 'bullish_setup_head',
        },
    }
    artifact_path = _save_hierarchical_candle_reversal_artifact(
        model_base_path,
        stage1_model=bearish_head_model,
        stage2_model=bullish_head_model,
        manifest=manifest,
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'bearish_head_validation': bearish_head_validation_metrics,
            'bullish_head_validation': bullish_head_validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 6)),
            'target_mode': str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower(),
            'pretrend_lookback': int(config.get('pretrendLookback', 6)),
            'pretrend_threshold': float(config.get('pretrendThreshold', 1.2)),
            'reversal_threshold': float(config.get('reversalThreshold', 1.0)),
            'dominance_ratio': float(config.get('dominanceRatio', 1.35)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': class_codes,
            'class_labels': class_labels,
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'neutral_retention': float(training_config.neutral_retention),
            'target_filter_summary': sequence_dataset.get('target_filter_summary'),
            'bearish_head_class_weight_vector': bearish_head_class_weight_vector.tolist(),
            'bullish_head_class_weight_vector': bullish_head_class_weight_vector.tolist(),
            'selected_bearish_threshold': selected_bearish_threshold,
            'selected_bullish_threshold': selected_bullish_threshold,
        },
        'score': score,
    }


def run_candle_reversal_cnn_v9_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing dual-head candle reversal test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    loaded = _load_hierarchical_candle_reversal_artifact(model_path)
    manifest = loaded['manifest']
    bearish_head_model = loaded['stage1_model']
    bullish_head_model = loaded['stage2_model']
    class_codes = list(manifest.get('class_codes') or sequence_dataset['class_codes'])
    class_labels = dict(manifest.get('class_labels') or sequence_dataset['class_labels'])
    neutral_class_index = int(manifest.get('neutral_class_index', class_codes.index(0)))
    bearish_class_index = int(manifest.get('bearish_class_index', class_codes.index(-1)))
    bullish_class_index = int(manifest.get('bullish_class_index', class_codes.index(1)))
    selected_bearish_threshold = float(manifest.get('selected_bearish_threshold', 0.5))
    selected_bullish_threshold = float(manifest.get('selected_bullish_threshold', 0.5))

    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = np.asarray(sequence_dataset['y_class'][train_rows + validation_rows:], dtype=int)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test_bearish = bearish_head_model.transform_features(X_test)
    X_test_bullish = bullish_head_model.transform_features(X_test)
    bearish_probabilities = bearish_head_model.predict_probabilities(X_test_bearish)
    bullish_probabilities = bullish_head_model.predict_probabilities(X_test_bullish)
    predicted_indices, combined_probabilities = _combine_dual_head_reversal_predictions(
        bearish_probabilities,
        bullish_probabilities,
        bearish_threshold=selected_bearish_threshold,
        bullish_threshold=selected_bullish_threshold,
        neutral_class_index=neutral_class_index,
        bearish_class_index=bearish_class_index,
        bullish_class_index=bullish_class_index,
    )
    metrics = _evaluate_class_predictions(
        y_test,
        predicted_indices,
        class_codes=class_codes,
        class_labels=class_labels,
        probabilities=combined_probabilities,
    )
    metrics['bearish_head_test'] = bearish_head_model.evaluate(X_test_bearish, (y_test == bearish_class_index).astype(int))
    metrics['bullish_head_test'] = bullish_head_model.evaluate(X_test_bullish, (y_test == bullish_class_index).astype(int))
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(manifest.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int((loaded['stage1_metadata'] or {}).get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int((loaded['stage1_metadata'] or {}).get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int((loaded['stage1_metadata'] or {}).get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 6))
    metrics['target_mode'] = str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower()
    metrics['pretrend_lookback'] = int(config.get('pretrendLookback', 6))
    metrics['pretrend_threshold'] = float(config.get('pretrendThreshold', 1.2))
    metrics['reversal_threshold'] = float(config.get('reversalThreshold', 1.0))
    metrics['dominance_ratio'] = float(config.get('dominanceRatio', 1.35))
    metrics['class_weight_mode'] = str(manifest.get('class_weight_mode', config.get('classWeightMode', 'none')) or 'none').strip().lower()
    metrics['class_weight_exponent'] = float(manifest.get('class_weight_exponent', config.get('classWeightExponent', 1.0)))
    metrics['neutral_retention'] = float(manifest.get('neutral_retention', config.get('neutralRetention', 1.0)))
    metrics['target_filter_summary'] = manifest.get('target_filter_summary')
    metrics['selected_bearish_threshold'] = selected_bearish_threshold
    metrics['selected_bullish_threshold'] = selected_bullish_threshold
    metrics['bearish_head_class_weight_vector'] = manifest.get('bearish_head_class_weight_vector')
    metrics['bullish_head_class_weight_vector'] = manifest.get('bullish_head_class_weight_vector')
    score = float(metrics.get('macro_f1') or 0.0)
    log(f'Test finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_candle_reversal_cnn_v10_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    network_id = str(config.get('networkId') or 'candle_reversal_cnn_v10').strip() or 'candle_reversal_cnn_v10'
    log('Preparing tri-head candle reversal feature pipeline.', progress=0.05)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    log(f'Tri-head candle reversal dataset built with {sequence_dataset["rows"]} rows.', progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 120:
        raise ValueError('Tri-head candle reversal training requires at least 120 clean sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    class_codes = list(sequence_dataset['class_codes'])
    class_labels = dict(sequence_dataset['class_labels'])
    if 0 not in class_codes or -1 not in class_codes or 1 not in class_codes:
        raise ValueError('Tri-head candle reversal training requires class codes -1, 0 and 1.')
    neutral_class_index = class_codes.index(0)
    bearish_class_index = class_codes.index(-1)
    bullish_class_index = class_codes.index(1)

    X_train_full = sequence_dataset['X'][:train_rows]
    y_train_full = np.asarray(sequence_dataset['y_class'][:train_rows], dtype=int)
    X_validation_full = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation_full = np.asarray(sequence_dataset['y_class'][train_rows:train_rows + validation_rows], dtype=int)

    training_config = _build_supervised_training_config(config)
    directional_head_rest_retention = max(
        0.05,
        min(
            1.0,
            float(
                config.get(
                    'directionalHeadRestRetention',
                    0.6 if network_id == 'candle_reversal_cnn_v10_1' else 1.0,
                ) or (0.6 if network_id == 'candle_reversal_cnn_v10_1' else 1.0)
            ),
        ),
    )

    def build_head_payload(target_index: int, *, positive_label: str, seed_offset: int, rest_retention: float = 1.0):
        y_train_head = (y_train_full == target_index).astype(int)
        y_validation_head = (y_validation_full == target_index).astype(int)
        X_train_head_raw = X_train_full
        rebalance_summary = {
            'applied': False,
            'rows_before': int(len(y_train_head)),
            'rows_after': int(len(y_train_head)),
            'class_counts_before': _build_class_index_counts(y_train_head),
            'class_counts_after': _build_class_index_counts(y_train_head),
            'retained_class_index': 0,
            'retention': float(rest_retention),
        }
        if float(rest_retention) < 0.999:
            X_train_head_raw, y_train_head, rebalance_summary = _rebalance_sequence_classes(
                X_train_head_raw,
                y_train_head,
                seed=training_config.seed + seed_offset,
                retained_class_index=0,
                retention=float(rest_retention),
            )
        class_weight_vector = _build_inverse_frequency_class_weights(
            y_train_head,
            num_classes=2,
            mode=training_config.class_weight_mode,
            exponent=training_config.class_weight_exponent,
        )
        head_model = TemporalConvolutionalClassifier(
            input_features=X_train_head_raw.shape[2],
            sequence_length=X_train_head_raw.shape[1],
            class_codes=[0, 1],
            class_labels={0: 'rest', 1: positive_label},
            conv_filters=training_config.conv_filters,
            kernel_size=training_config.kernel_size,
            hidden_layers=training_config.hidden_layers,
            learning_rate=training_config.learning_rate,
            seed=training_config.seed + seed_offset,
        )
        head_model.feature_columns = list(sequence_dataset['feature_columns'])
        head_model.fit_normalizer(X_train_head_raw)
        X_train_head = head_model.transform_features(X_train_head_raw)
        X_validation_head = head_model.transform_features(X_validation_full)
        return {
            'model': head_model,
            'X_train': X_train_head,
            'X_validation': X_validation_head,
            'y_train': y_train_head,
            'y_validation': y_validation_head,
            'class_weight_vector': class_weight_vector,
            'rebalance_summary': rebalance_summary,
        }

    bearish_head = build_head_payload(
        bearish_class_index,
        positive_label='bearish_setup',
        seed_offset=0,
        rest_retention=directional_head_rest_retention,
    )
    log(
        (
            'Bearish head balance prepared: '
            f'rows {bearish_head["rebalance_summary"]["rows_before"]} -> {bearish_head["rebalance_summary"]["rows_after"]}, '
            f'class weights={bearish_head["class_weight_vector"].round(3).tolist()}.'
        ),
        progress=0.29,
    )
    log('Training bearish setup head.', progress=0.32)
    bearish_head['model'].train(
        bearish_head['X_train'],
        bearish_head['y_train'],
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=bearish_head['X_validation'],
        y_validation=bearish_head['y_validation'],
        class_weights=bearish_head['class_weight_vector'],
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.32 + (0.16 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label=_candle_reversal_phase_label(network_id, 'bearish head', fallback='v10'),
            detail=(
                f"bearish head epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    bearish_head_validation_metrics = bearish_head['model'].evaluate(bearish_head['X_validation'], bearish_head['y_validation'])

    neutral_head = build_head_payload(neutral_class_index, positive_label='neutral_setup', seed_offset=17, rest_retention=1.0)
    log(
        (
            'Neutral head balance prepared: '
            f'rows {len(neutral_head["y_train"])} -> {len(neutral_head["y_train"])}, '
            f'class weights={neutral_head["class_weight_vector"].round(3).tolist()}.'
        ),
        progress=0.49,
    )
    log('Training neutral setup head.', progress=0.52)
    neutral_head['model'].train(
        neutral_head['X_train'],
        neutral_head['y_train'],
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=neutral_head['X_validation'],
        y_validation=neutral_head['y_validation'],
        class_weights=neutral_head['class_weight_vector'],
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.52 + (0.16 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label=_candle_reversal_phase_label(network_id, 'neutral head', fallback='v10'),
            detail=(
                f"neutral head epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    neutral_head_validation_metrics = neutral_head['model'].evaluate(neutral_head['X_validation'], neutral_head['y_validation'])

    bullish_head = build_head_payload(
        bullish_class_index,
        positive_label='bullish_setup',
        seed_offset=31,
        rest_retention=directional_head_rest_retention,
    )
    log(
        (
            'Bullish head balance prepared: '
            f'rows {bullish_head["rebalance_summary"]["rows_before"]} -> {bullish_head["rebalance_summary"]["rows_after"]}, '
            f'class weights={bullish_head["class_weight_vector"].round(3).tolist()}.'
        ),
        progress=0.69,
    )
    log('Training bullish setup head.', progress=0.72)
    bullish_head['model'].train(
        bullish_head['X_train'],
        bullish_head['y_train'],
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=bullish_head['X_validation'],
        y_validation=bullish_head['y_validation'],
        class_weights=bullish_head['class_weight_vector'],
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.72 + (0.16 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label=_candle_reversal_phase_label(network_id, 'bullish head', fallback='v10'),
            detail=(
                f"bullish head epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    bullish_head_validation_metrics = bullish_head['model'].evaluate(bullish_head['X_validation'], bullish_head['y_validation'])

    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    log('Selecting tri-head thresholds on validation split.', progress=0.9)
    validation_bearish_probabilities = bearish_head['model'].predict_probabilities(bearish_head['X_validation'])
    validation_neutral_probabilities = neutral_head['model'].predict_probabilities(neutral_head['X_validation'])
    validation_bullish_probabilities = bullish_head['model'].predict_probabilities(bullish_head['X_validation'])
    threshold_search = _search_tri_head_reversal_thresholds(
        y_validation_full,
        validation_bearish_probabilities,
        validation_neutral_probabilities,
        validation_bullish_probabilities,
        class_codes=class_codes,
        class_labels=class_labels,
        neutral_class_index=neutral_class_index,
        bearish_class_index=bearish_class_index,
        bullish_class_index=bullish_class_index,
    )
    selected_bearish_threshold = float(threshold_search['bearish_threshold'])
    selected_neutral_threshold = float(threshold_search['neutral_threshold'])
    selected_bullish_threshold = float(threshold_search['bullish_threshold'])
    validation_metrics = dict(threshold_search['metrics'])
    validation_metrics['selected_bearish_threshold'] = selected_bearish_threshold
    validation_metrics['selected_neutral_threshold'] = selected_neutral_threshold
    validation_metrics['selected_bullish_threshold'] = selected_bullish_threshold
    log(
        (
            'Validation thresholds selected at '
            f'bearish={selected_bearish_threshold:.2f}, '
            f'neutral={selected_neutral_threshold:.2f}, '
            f'bullish={selected_bullish_threshold:.2f} '
            f'with macro F1 {validation_metrics.get("macro_f1", 0.0):.4f}.'
        ),
        progress=0.94,
    )

    log('Training finished. Saving tri-head model.', progress=0.96)
    manifest = {
        'artifact_type': 'tri_head_candle_reversal_v10',
        'network_id': network_id,
        'feature_columns': list(sequence_dataset['feature_columns']),
        'split_sizes': split_sizes,
        'class_codes': class_codes,
        'class_labels': class_labels,
        'neutral_class_index': int(neutral_class_index),
        'bearish_class_index': int(bearish_class_index),
        'bullish_class_index': int(bullish_class_index),
        'selected_bearish_threshold': selected_bearish_threshold,
        'selected_neutral_threshold': selected_neutral_threshold,
        'selected_bullish_threshold': selected_bullish_threshold,
        'class_weight_mode': training_config.class_weight_mode,
        'class_weight_exponent': float(training_config.class_weight_exponent),
        'neutral_retention': float(training_config.neutral_retention),
        'directional_head_rest_retention': float(directional_head_rest_retention),
        'target_filter_summary': sequence_dataset.get('target_filter_summary'),
        'bearish_head_class_weight_vector': bearish_head['class_weight_vector'].tolist(),
        'bearish_head_rebalance_summary': bearish_head['rebalance_summary'],
        'neutral_head_class_weight_vector': neutral_head['class_weight_vector'].tolist(),
        'bullish_head_class_weight_vector': bullish_head['class_weight_vector'].tolist(),
        'bullish_head_rebalance_summary': bullish_head['rebalance_summary'],
        'bearish_head_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': {0: 'rest', 1: 'bearish_setup'},
            'stage_role': 'bearish_setup_head',
        },
        'neutral_head_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': {0: 'rest', 1: 'neutral_setup'},
            'stage_role': 'neutral_setup_head',
        },
        'bullish_head_model_metadata': {
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': [0, 1],
            'class_labels': {0: 'rest', 1: 'bullish_setup'},
            'stage_role': 'bullish_setup_head',
        },
    }
    artifact_path = _save_tri_head_candle_reversal_artifact(
        model_base_path,
        bearish_head_model=bearish_head['model'],
        neutral_head_model=neutral_head['model'],
        bullish_head_model=bullish_head['model'],
        manifest=manifest,
    )
    score = float(validation_metrics.get('macro_f1') or 0.0)
    log(f'Validation finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'bearish_head_validation': bearish_head_validation_metrics,
            'neutral_head_validation': neutral_head_validation_metrics,
            'bullish_head_validation': bullish_head_validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(config.get('targetHorizon', 6)),
            'target_mode': str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower(),
            'pretrend_lookback': int(config.get('pretrendLookback', 6)),
            'pretrend_threshold': float(config.get('pretrendThreshold', 1.2)),
            'reversal_threshold': float(config.get('reversalThreshold', 1.0)),
            'dominance_ratio': float(config.get('dominanceRatio', 1.35)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': class_codes,
            'class_labels': class_labels,
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'neutral_retention': float(training_config.neutral_retention),
            'directional_head_rest_retention': float(directional_head_rest_retention),
            'target_filter_summary': sequence_dataset.get('target_filter_summary'),
            'bearish_head_class_weight_vector': bearish_head['class_weight_vector'].tolist(),
            'bearish_head_rebalance_summary': bearish_head['rebalance_summary'],
            'neutral_head_class_weight_vector': neutral_head['class_weight_vector'].tolist(),
            'bullish_head_class_weight_vector': bullish_head['class_weight_vector'].tolist(),
            'bullish_head_rebalance_summary': bullish_head['rebalance_summary'],
            'selected_bearish_threshold': selected_bearish_threshold,
            'selected_neutral_threshold': selected_neutral_threshold,
            'selected_bullish_threshold': selected_bullish_threshold,
        },
        'score': score,
    }


def run_candle_reversal_cnn_v10_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    log('Preparing tri-head candle reversal test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 50:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    loaded = _load_tri_head_candle_reversal_artifact(model_path)
    manifest = loaded['manifest']
    bearish_head_model = loaded['bearish_head_model']
    neutral_head_model = loaded['neutral_head_model']
    bullish_head_model = loaded['bullish_head_model']
    class_codes = list(manifest.get('class_codes') or sequence_dataset['class_codes'])
    class_labels = dict(manifest.get('class_labels') or sequence_dataset['class_labels'])
    neutral_class_index = int(manifest.get('neutral_class_index', class_codes.index(0)))
    bearish_class_index = int(manifest.get('bearish_class_index', class_codes.index(-1)))
    bullish_class_index = int(manifest.get('bullish_class_index', class_codes.index(1)))
    selected_bearish_threshold = float(manifest.get('selected_bearish_threshold', 0.5))
    selected_neutral_threshold = float(manifest.get('selected_neutral_threshold', 0.5))
    selected_bullish_threshold = float(manifest.get('selected_bullish_threshold', 0.5))

    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = np.asarray(sequence_dataset['y_class'][train_rows + validation_rows:], dtype=int)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test_bearish = bearish_head_model.transform_features(X_test)
    X_test_neutral = neutral_head_model.transform_features(X_test)
    X_test_bullish = bullish_head_model.transform_features(X_test)
    bearish_probabilities = bearish_head_model.predict_probabilities(X_test_bearish)
    neutral_probabilities = neutral_head_model.predict_probabilities(X_test_neutral)
    bullish_probabilities = bullish_head_model.predict_probabilities(X_test_bullish)
    predicted_indices, combined_probabilities = _combine_tri_head_reversal_predictions(
        bearish_probabilities,
        neutral_probabilities,
        bullish_probabilities,
        bearish_threshold=selected_bearish_threshold,
        neutral_threshold=selected_neutral_threshold,
        bullish_threshold=selected_bullish_threshold,
        neutral_class_index=neutral_class_index,
        bearish_class_index=bearish_class_index,
        bullish_class_index=bullish_class_index,
    )
    metrics = _evaluate_class_predictions(
        y_test,
        predicted_indices,
        class_codes=class_codes,
        class_labels=class_labels,
        probabilities=combined_probabilities,
    )
    metrics['bearish_head_test'] = bearish_head_model.evaluate(X_test_bearish, (y_test == bearish_class_index).astype(int))
    metrics['neutral_head_test'] = neutral_head_model.evaluate(X_test_neutral, (y_test == neutral_class_index).astype(int))
    metrics['bullish_head_test'] = bullish_head_model.evaluate(X_test_bullish, (y_test == bullish_class_index).astype(int))
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(manifest.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int((loaded['bearish_head_metadata'] or {}).get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int((loaded['bearish_head_metadata'] or {}).get('conv_filters') or config.get('convFilters', 16))
    metrics['kernel_size'] = int((loaded['bearish_head_metadata'] or {}).get('kernel_size') or config.get('kernelSize', 3))
    metrics['target_horizon'] = int(config.get('targetHorizon', 6))
    metrics['target_mode'] = str(config.get('targetMode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower()
    metrics['pretrend_lookback'] = int(config.get('pretrendLookback', 6))
    metrics['pretrend_threshold'] = float(config.get('pretrendThreshold', 1.2))
    metrics['reversal_threshold'] = float(config.get('reversalThreshold', 1.0))
    metrics['dominance_ratio'] = float(config.get('dominanceRatio', 1.35))
    metrics['class_weight_mode'] = str(manifest.get('class_weight_mode', config.get('classWeightMode', 'none')) or 'none').strip().lower()
    metrics['class_weight_exponent'] = float(manifest.get('class_weight_exponent', config.get('classWeightExponent', 1.0)))
    metrics['neutral_retention'] = float(manifest.get('neutral_retention', config.get('neutralRetention', 1.0)))
    metrics['directional_head_rest_retention'] = float(manifest.get('directional_head_rest_retention', config.get('directionalHeadRestRetention', 1.0)))
    metrics['target_filter_summary'] = manifest.get('target_filter_summary')
    metrics['selected_bearish_threshold'] = selected_bearish_threshold
    metrics['selected_neutral_threshold'] = selected_neutral_threshold
    metrics['selected_bullish_threshold'] = selected_bullish_threshold
    metrics['bearish_head_class_weight_vector'] = manifest.get('bearish_head_class_weight_vector')
    metrics['bearish_head_rebalance_summary'] = manifest.get('bearish_head_rebalance_summary')
    metrics['neutral_head_class_weight_vector'] = manifest.get('neutral_head_class_weight_vector')
    metrics['bullish_head_class_weight_vector'] = manifest.get('bullish_head_class_weight_vector')
    metrics['bullish_head_rebalance_summary'] = manifest.get('bullish_head_rebalance_summary')
    score = float(metrics.get('macro_f1') or 0.0)
    log(f'Test finished with macro F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'metrics': metrics,
        'score': score,
        'model_path': model_path,
    }


def run_candle_reversal_cnn_v10_1_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = str(next_config.get('networkId') or 'candle_reversal_cnn_v10_1').strip() or 'candle_reversal_cnn_v10_1'
    return run_candle_reversal_cnn_v10_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v10_1_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = str(next_config.get('networkId') or 'candle_reversal_cnn_v10_1').strip() or 'candle_reversal_cnn_v10_1'
    return run_candle_reversal_cnn_v10_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v11_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = str(next_config.get('networkId') or 'candle_reversal_cnn_v11').strip() or 'candle_reversal_cnn_v11'
    return run_candle_reversal_cnn_v10_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v11_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = str(next_config.get('networkId') or 'candle_reversal_cnn_v11').strip() or 'candle_reversal_cnn_v11'
    return run_candle_reversal_cnn_v10_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v11_scores_only_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'candle_reversal_cnn_v11_scores_only').strip()
        or 'candle_reversal_cnn_v11_scores_only'
    )
    return run_candle_reversal_cnn_v10_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v11_scores_only_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'candle_reversal_cnn_v11_scores_only').strip()
        or 'candle_reversal_cnn_v11_scores_only'
    )
    return run_candle_reversal_cnn_v10_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v12_scores_only_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'candle_reversal_cnn_v12_scores_only').strip()
        or 'candle_reversal_cnn_v12_scores_only'
    )
    return run_candle_reversal_cnn_v10_train(
        next_config,
        model_base_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_cnn_v12_scores_only_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'candle_reversal_cnn_v12_scores_only').strip()
        or 'candle_reversal_cnn_v12_scores_only'
    )
    return run_candle_reversal_cnn_v10_test(
        next_config,
        model_path,
        market_snapshot_path=market_snapshot_path,
        log_callback=log_callback,
        should_cancel=should_cancel,
    )


def run_candle_reversal_setup_quality_cnn_v1_train(config: dict, model_base_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None, **job_updates):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress, **job_updates)

    log('Preparing candle-reversal setup-quality v1 feature pipeline.', progress=0.05)
    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'candle_reversal_setup_quality_cnn_v1').strip()
        or 'candle_reversal_setup_quality_cnn_v1'
    )
    feature_config = _build_supervised_feature_config(next_config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_setup_quality_v1_sequence_dataset()
    log(f"Candle-reversal setup-quality v1 dataset built with {sequence_dataset['rows']} candidate rows.", progress=0.16)

    total_rows = int(sequence_dataset['rows'])
    if total_rows < 80:
        raise ValueError('Candle-reversal setup-quality v1 training requires at least 80 candidate sequence rows after feature generation.')

    test_rows = max(10, int(total_rows * max(0.0, float(next_config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(next_config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 40:
        raise ValueError('Not enough rows left for training after validation/test split.')

    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Data split ready: train={split_sizes['train']}, "
            f"validation={split_sizes['validation']}, test={split_sizes['test']}."
        ),
        progress=0.24,
    )

    X_train = sequence_dataset['X'][:train_rows]
    y_train = sequence_dataset['y_class'][:train_rows]
    X_validation = sequence_dataset['X'][train_rows:train_rows + validation_rows]
    y_validation = sequence_dataset['y_class'][train_rows:train_rows + validation_rows]

    training_config = _build_supervised_training_config(next_config)
    class_weight_vector = _build_inverse_frequency_class_weights(
        y_train,
        num_classes=len(sequence_dataset['class_codes']),
        mode=training_config.class_weight_mode,
        exponent=training_config.class_weight_exponent,
    )
    rebalance_summary = {
        'rows_before': int(len(y_train)),
        'rows_after': int(len(y_train)),
        'class_counts_before': {
            str(index): int(np.sum(y_train == index))
            for index in range(len(sequence_dataset['class_codes']))
        },
        'class_counts_after': {
            str(index): int(np.sum(y_train == index))
            for index in range(len(sequence_dataset['class_codes']))
        },
        'retained_class_index': None,
        'retention': 1.0,
    }
    log(
        (
            'Training balance prepared: '
            f"rows {rebalance_summary['rows_before']} -> {rebalance_summary['rows_after']}, "
            f'class weights={class_weight_vector.round(3).tolist()}.'
        ),
        progress=0.3,
    )

    model = TemporalConvolutionalClassifier(
        input_features=X_train.shape[2],
        sequence_length=X_train.shape[1],
        class_codes=sequence_dataset['class_codes'],
        class_labels=sequence_dataset['class_labels'],
        conv_filters=training_config.conv_filters,
        kernel_size=training_config.kernel_size,
        hidden_layers=training_config.hidden_layers,
        learning_rate=training_config.learning_rate,
        seed=training_config.seed,
    )
    model.feature_columns = list(sequence_dataset['feature_columns'])
    model.fit_normalizer(X_train)
    X_train = model.transform_features(X_train)
    X_validation = model.transform_features(X_validation)

    log('Training candle-reversal setup-quality v1 classifier.', progress=0.34)
    model.train(
        X_train,
        y_train,
        epochs=training_config.epochs,
        batch_size=training_config.batch_size,
        X_validation=X_validation,
        y_validation=y_validation,
        class_weights=class_weight_vector,
        log_callback=lambda message, level='info', **progress_state: log(
            message,
            level=level,
            progress=0.34 + (0.54 * float(progress_state.get('progress_fraction') or 0.0)),
            phase='training',
            phase_label='Candle-reversal setup-quality v1 training',
            detail=(
                f"epoch {int(progress_state.get('current_epoch') or 0)}"
                f" / {int(progress_state.get('total_epochs') or training_config.epochs)}"
            ),
            current_epoch=int(progress_state.get('current_epoch') or 0),
            total_epochs=int(progress_state.get('total_epochs') or training_config.epochs),
            elapsed_seconds=progress_state.get('elapsed_seconds'),
            eta_seconds=progress_state.get('eta_seconds'),
            append_log=False,
        ),
        should_cancel=should_cancel,
    )
    validation_metrics = model.evaluate(X_validation, y_validation)
    log('Training finished. Saving model.', progress=0.92)
    artifact_path = model.save(
        model_base_path,
        metadata={
            'feature_columns': list(sequence_dataset['feature_columns']),
            'training_config': next_config,
            'hidden_layers': training_config.hidden_layers,
            'conv_filters': training_config.conv_filters,
            'kernel_size': training_config.kernel_size,
            'observation_window': sequence_dataset['observation_window'],
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': training_config.class_weight_exponent,
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
    )
    score = float(
        validation_metrics.get('class_good_setup_f1')
        or validation_metrics.get('macro_f1')
        or 0.0
    )
    log(f'Validation finished with good-setup F1 {score:.4f}.', level='success', progress=1.0)

    return {
        'artifact_path': artifact_path,
        'metrics': {
            'rows': total_rows,
            'split_sizes': split_sizes,
            'validation': validation_metrics,
            'feature_columns': list(sequence_dataset['feature_columns']),
            'feature_size': len(sequence_dataset['feature_columns']),
            'observation_window': int(sequence_dataset['observation_window']),
            'conv_filters': int(training_config.conv_filters),
            'kernel_size': int(training_config.kernel_size),
            'target_horizon': int(next_config.get('targetHorizon', 8)),
            'target_mode': str(next_config.get('targetMode', 'candle_reversal_setup_quality_good_vs_rest_classification') or 'candle_reversal_setup_quality_good_vs_rest_classification').strip().lower(),
            'pretrend_lookback': int(next_config.get('pretrendLookback', 6)),
            'pretrend_threshold': float(next_config.get('pretrendThreshold', 1.2)),
            'reversal_take_profit_atr': float(next_config.get('reversalTakeProfitAtr', 0.75)),
            'reversal_stop_loss_atr': float(next_config.get('reversalStopLossAtr', 1.0)),
            'hidden_layers': training_config.hidden_layers,
            'class_codes': list(sequence_dataset['class_codes']),
            'class_labels': dict(sequence_dataset['class_labels']),
            'class_weight_mode': training_config.class_weight_mode,
            'class_weight_exponent': float(training_config.class_weight_exponent),
            'class_weight_vector': class_weight_vector.tolist(),
            'rebalance_summary': rebalance_summary,
            'candidate_summary': sequence_dataset.get('candidate_summary') or {},
        },
        'score': score,
    }


def run_candle_reversal_setup_quality_cnn_v1_test(config: dict, model_path: str, market_snapshot_path: str | None = None, log_callback=None, should_cancel=None):
    def log(message: str, level: str = 'info', progress=None):
        if callable(log_callback):
            log_callback(message, level=level, progress=progress)

    if not model_path:
        raise ValueError('No trained model is available to test.')

    next_config = dict(config or {})
    next_config['networkId'] = (
        str(next_config.get('networkId') or 'candle_reversal_setup_quality_cnn_v1').strip()
        or 'candle_reversal_setup_quality_cnn_v1'
    )
    log('Preparing candle-reversal setup-quality v1 test dataset.', progress=0.08)
    feature_config = _build_supervised_feature_config(next_config)
    market_candles = _load_market_snapshot_candles(market_snapshot_path)
    pipeline = (
        BasicFeedForwardFeaturePipeline.from_candles(feature_config, market_candles)
        if market_candles is not None
        else BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    )
    sequence_dataset = pipeline.build_candle_reversal_setup_quality_v1_sequence_dataset()
    total_rows = int(sequence_dataset['rows'])
    test_rows = max(10, int(total_rows * max(0.0, float(next_config['testSplit']))))
    validation_rows = max(10, int(total_rows * max(0.0, float(next_config['validationSplit']))))
    train_rows = total_rows - validation_rows - test_rows
    if train_rows < 40:
        raise ValueError('Not enough rows left for training after validation/test split.')
    split_sizes = {
        'total': total_rows,
        'train': train_rows,
        'validation': validation_rows,
        'test': total_rows - train_rows - validation_rows,
    }
    log(
        (
            f"Testing on chronological holdout with {split_sizes['test']} rows "
            f"after train={split_sizes['train']} and validation={split_sizes['validation']}."
        ),
        progress=0.24,
    )

    model, metadata = TemporalConvolutionalClassifier.load(model_path)
    X_test = sequence_dataset['X'][train_rows + validation_rows:]
    y_test = sequence_dataset['y_class'][train_rows + validation_rows:]
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    X_test = model.transform_features(X_test)
    if callable(should_cancel) and should_cancel():
        raise SupervisedCancellationRequestedError('Neural job cancelled by user.')
    metrics = model.evaluate(X_test, y_test)
    metrics['split_sizes'] = split_sizes
    metrics['feature_size'] = len(metadata.get('feature_columns') or sequence_dataset['feature_columns'])
    metrics['observation_window'] = int(metadata.get('observation_window') or sequence_dataset['observation_window'])
    metrics['conv_filters'] = int(metadata.get('conv_filters') or next_config.get('convFilters', 16))
    metrics['kernel_size'] = int(metadata.get('kernel_size') or next_config.get('kernelSize', 3))
    metrics['target_horizon'] = int(next_config.get('targetHorizon', 8))
    metrics['target_mode'] = str(next_config.get('targetMode', 'candle_reversal_setup_quality_good_vs_rest_classification') or 'candle_reversal_setup_quality_good_vs_rest_classification').strip().lower()
    metrics['pretrend_lookback'] = int(next_config.get('pretrendLookback', 6))
    metrics['pretrend_threshold'] = float(next_config.get('pretrendThreshold', 1.2))
    metrics['reversal_take_profit_atr'] = float(next_config.get('reversalTakeProfitAtr', 0.75))
    metrics['reversal_stop_loss_atr'] = float(next_config.get('reversalStopLossAtr', 1.0))
    metrics['candidate_summary'] = metadata.get('candidate_summary') or sequence_dataset.get('candidate_summary') or {}
    metrics['class_weight_mode'] = str(metadata.get('class_weight_mode') or next_config.get('classWeightMode', 'none') or 'none').strip().lower()
    metrics['class_weight_exponent'] = float(metadata.get('class_weight_exponent') or next_config.get('classWeightExponent', 1.0))
    metrics['class_weight_vector'] = metadata.get('class_weight_vector') or []
    metrics['rebalance_summary'] = metadata.get('rebalance_summary') or {}
    log(
        (
            'Chronological holdout finished with '
            f"good-setup F1 {float(metrics.get('class_good_setup_f1') or 0.0):.4f}."
        ),
        level='success',
        progress=1.0,
    )
    score = float(metrics.get('class_good_setup_f1') or metrics.get('macro_f1') or 0.0)
    return {
        'model_path': model_path,
        'metrics': metrics,
        'score': score,
    }


def debug_basic_ff_features(config: dict):
    feature_config = _build_supervised_feature_config(config)
    pipeline = BasicFeedForwardFeaturePipeline.from_bridge(feature_config)
    pipeline.apply()
    available_columns = list(pipeline.symbol.candles.columns)
    return {
        'network_id': str(config.get('networkId') or 'temporal_cnn_indicator_fusion_v1'),
        'process_id': os.getpid(),
        'symbol': feature_config.symbol_name,
        'timeframe': feature_config.timeframe,
        'bars': feature_config.bars,
        'requested_columns': list(pipeline.requested_feature_columns),
        'resolved_columns': list(pipeline.feature_columns),
        'available_columns': available_columns,
        'available_indicator_columns': [
            column_name
            for column_name in available_columns
            if column_name not in ('time', 'open', 'high', 'low', 'close', 'volume')
        ],
    }


NETWORK_RUNNER_REGISTRY['temporal_cnn_indicator_fusion_v1'] = {
    'id': 'temporal_cnn_indicator_fusion_v1',
    'train': run_temporal_cnn_train,
    'test': run_temporal_cnn_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['neural_market_regime_cnn_v1'] = {
    'id': 'neural_market_regime_cnn_v1',
    'train': run_neural_market_regime_cnn_train,
    'test': run_neural_market_regime_cnn_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v1'] = {
    'id': 'candle_reversal_cnn_v1',
    'train': run_candle_reversal_cnn_train,
    'test': run_candle_reversal_cnn_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v2'] = {
    'id': 'candle_reversal_cnn_v2',
    'train': run_candle_reversal_cnn_train,
    'test': run_candle_reversal_cnn_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v3'] = {
    'id': 'candle_reversal_cnn_v3',
    'train': run_candle_reversal_cnn_v3_train,
    'test': run_candle_reversal_cnn_v3_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v4'] = {
    'id': 'candle_reversal_cnn_v4',
    'train': run_candle_reversal_cnn_v4_train,
    'test': run_candle_reversal_cnn_v4_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v5'] = {
    'id': 'candle_reversal_cnn_v5',
    'train': run_candle_reversal_cnn_v5_train,
    'test': run_candle_reversal_cnn_v5_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v6'] = {
    'id': 'candle_reversal_cnn_v6',
    'train': run_candle_reversal_cnn_v6_train,
    'test': run_candle_reversal_cnn_v6_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v7'] = {
    'id': 'candle_reversal_cnn_v7',
    'train': run_candle_reversal_cnn_v7_train,
    'test': run_candle_reversal_cnn_v7_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v7_1'] = {
    'id': 'candle_reversal_cnn_v7_1',
    'train': run_candle_reversal_cnn_v7_1_train,
    'test': run_candle_reversal_cnn_v7_1_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v8'] = {
    'id': 'candle_reversal_cnn_v8',
    'train': run_candle_reversal_cnn_v8_train,
    'test': run_candle_reversal_cnn_v8_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v9'] = {
    'id': 'candle_reversal_cnn_v9',
    'train': run_candle_reversal_cnn_v9_train,
    'test': run_candle_reversal_cnn_v9_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v10'] = {
    'id': 'candle_reversal_cnn_v10',
    'train': run_candle_reversal_cnn_v10_train,
    'test': run_candle_reversal_cnn_v10_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v10_1'] = {
    'id': 'candle_reversal_cnn_v10_1',
    'train': run_candle_reversal_cnn_v10_1_train,
    'test': run_candle_reversal_cnn_v10_1_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v11'] = {
    'id': 'candle_reversal_cnn_v11',
    'train': run_candle_reversal_cnn_v11_train,
    'test': run_candle_reversal_cnn_v11_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v11_scores_only'] = {
    'id': 'candle_reversal_cnn_v11_scores_only',
    'train': run_candle_reversal_cnn_v11_scores_only_train,
    'test': run_candle_reversal_cnn_v11_scores_only_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_cnn_v12_scores_only'] = {
    'id': 'candle_reversal_cnn_v12_scores_only',
    'train': run_candle_reversal_cnn_v12_scores_only_train,
    'test': run_candle_reversal_cnn_v12_scores_only_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['candle_reversal_setup_quality_cnn_v1'] = {
    'id': 'candle_reversal_setup_quality_cnn_v1',
    'train': run_candle_reversal_setup_quality_cnn_v1_train,
    'test': run_candle_reversal_setup_quality_cnn_v1_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['ema_low_adx_setup_quality_cnn_v1'] = {
    'id': 'ema_low_adx_setup_quality_cnn_v1',
    'train': run_ema_low_adx_setup_quality_cnn_train,
    'test': run_ema_low_adx_setup_quality_cnn_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['ema_low_adx_setup_quality_cnn_v2'] = {
    'id': 'ema_low_adx_setup_quality_cnn_v2',
    'train': run_ema_low_adx_setup_quality_cnn_v2_train,
    'test': run_ema_low_adx_setup_quality_cnn_v2_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['ema_low_adx_setup_quality_cnn_v3'] = {
    'id': 'ema_low_adx_setup_quality_cnn_v3',
    'train': run_ema_low_adx_setup_quality_cnn_v3_train,
    'test': run_ema_low_adx_setup_quality_cnn_v3_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['ema_low_adx_setup_quality_cnn_v4'] = {
    'id': 'ema_low_adx_setup_quality_cnn_v4',
    'train': run_ema_low_adx_setup_quality_cnn_v4_train,
    'test': run_ema_low_adx_setup_quality_cnn_v4_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['ema_low_adx_setup_quality_cnn_v5'] = {
    'id': 'ema_low_adx_setup_quality_cnn_v5',
    'train': run_ema_low_adx_setup_quality_cnn_v5_train,
    'test': run_ema_low_adx_setup_quality_cnn_v5_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['ema_low_adx_setup_quality_cnn_v6'] = {
    'id': 'ema_low_adx_setup_quality_cnn_v6',
    'train': run_ema_low_adx_setup_quality_cnn_v6_train,
    'test': run_ema_low_adx_setup_quality_cnn_v6_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['ema_low_adx_setup_quality_cnn_v7'] = {
    'id': 'ema_low_adx_setup_quality_cnn_v7',
    'train': run_ema_low_adx_setup_quality_cnn_v7_train,
    'test': run_ema_low_adx_setup_quality_cnn_v7_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['micro_cost_edge_cnn_v1'] = {
    'id': 'micro_cost_edge_cnn_v1',
    'train': run_micro_cost_edge_cnn_v1_train,
    'test': run_micro_cost_edge_cnn_v1_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['micro_cost_edge_cnn_v2'] = {
    'id': 'micro_cost_edge_cnn_v2',
    'train': run_micro_cost_edge_cnn_v2_train,
    'test': run_micro_cost_edge_cnn_v2_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['micro_cost_edge_cnn_v3'] = {
    'id': 'micro_cost_edge_cnn_v3',
    'train': run_micro_cost_edge_cnn_v3_train,
    'test': run_micro_cost_edge_cnn_v3_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['micro_cost_edge_cnn_v4'] = {
    'id': 'micro_cost_edge_cnn_v4',
    'train': run_micro_cost_edge_cnn_v4_train,
    'test': run_micro_cost_edge_cnn_v4_test,
    'debug_features': debug_basic_ff_features,
}
NETWORK_RUNNER_REGISTRY['micro_cost_edge_cnn_v5'] = {
    'id': 'micro_cost_edge_cnn_v5',
    'train': run_micro_cost_edge_cnn_v5_train,
    'test': run_micro_cost_edge_cnn_v5_test,
    'debug_features': debug_basic_ff_features,
}

NETWORK_RUNNER_REGISTRY['market_regime_rl_v1'] = {
    'id': 'market_regime_rl_v1',
    'train': run_market_regime_rl_train,
    'test': run_market_regime_rl_test,
    'debug_features': debug_market_regime_rl_features,
}

NETWORK_RUNNER_REGISTRY['market_regime_rl_v2'] = {
    'id': 'market_regime_rl_v2',
    'train': run_market_regime_rl_train,
    'test': run_market_regime_rl_test,
    'debug_features': debug_market_regime_rl_features,
}

NETWORK_RUNNER_REGISTRY['market_regime_rl_v3'] = {
    'id': 'market_regime_rl_v3',
    'train': run_market_regime_rl_train,
    'test': run_market_regime_rl_test,
    'debug_features': debug_market_regime_rl_features,
}


def get_neural_runner(network: dict | None):
    if not network:
        return None

    runner_id = str(network.get('runner_id') or '').strip()
    if not runner_id:
        return None

    return NETWORK_RUNNER_REGISTRY.get(runner_id)

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ...calculator import Calculator
from ...symbol import Symbol
from ....neural.supervised.config import SupervisedFeatureConfig
from ....neural.supervised.features import BasicFeedForwardFeaturePipeline
from ....neural.supervised.trainer import TemporalConvolutionalClassifier


ASSET_DIR = Path(__file__).resolve().parent / 'assets' / 'neural_ema_low_adx_setup_quality_v4_bg020'
MANIFEST_PATH = ASSET_DIR / 'manifest.json'
METADATA_PATH = ASSET_DIR / 'model.metadata.json'
MODEL_PATH = ASSET_DIR / 'model.npz'


@lru_cache(maxsize=1)
def _load_bundle():
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f'Missing neural indicator manifest: {MANIFEST_PATH}')
    if not METADATA_PATH.exists():
        raise FileNotFoundError(f'Missing neural indicator metadata: {METADATA_PATH}')
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f'Missing neural indicator model: {MODEL_PATH}')

    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    metadata = json.loads(METADATA_PATH.read_text(encoding='utf-8'))
    model, model_metadata = TemporalConvolutionalClassifier.load(MODEL_PATH)
    return {
        'manifest': manifest,
        'metadata': metadata,
        'model': model,
        'model_metadata': model_metadata,
    }


def _build_feature_config(symbol: Symbol, bundle: dict):
    metadata = dict(bundle.get('metadata') or {})
    training_config = dict(metadata.get('config') or {})
    manifest = dict(bundle.get('manifest') or {})

    return SupervisedFeatureConfig(
        symbol_name=str(symbol.name),
        timeframe=str(symbol.timeframe),
        bars=int(len(symbol.candles.index)),
        network_id=str(
            manifest.get('network_id')
            or training_config.get('networkId')
            or metadata.get('network_id')
            or 'ema_low_adx_setup_quality_cnn_v4'
        ),
        feature_profile='ema_low_adx_setup_quality',
        observation_window=int(manifest.get('observation_window') or training_config.get('observationWindow') or 24),
        include_volume=True,
        normalize_volume=bool(training_config.get('normalizeVolume', False)),
        normalization_columns=list(training_config.get('normalizationColumns') or []),
        target_horizon=int(training_config.get('targetHorizon', 8) or 8),
        target_mode=str(training_config.get('targetMode') or 'ema_low_adx_setup_quality_good_vs_rest_classification'),
        setup_adx_ceiling=float(training_config.get('setupAdxCeiling', 40.0) or 40.0),
        setup_prev_rsi_ceiling=float(training_config.get('setupPrevRsiCeiling', 50.0) or 50.0),
        setup_current_rsi_floor=float(training_config.get('setupCurrentRsiFloor', 32.0) or 32.0),
        setup_current_rsi_ceiling=float(training_config.get('setupCurrentRsiCeiling', 60.0) or 60.0),
        setup_touch_slack_atr=float(training_config.get('setupTouchSlackAtr', 0.25) or 0.25),
        setup_prev_band_slack_atr=float(training_config.get('setupPrevBandSlackAtr', 0.25) or 0.25),
        setup_bounce_fraction=float(training_config.get('setupBounceFraction', 0.0) or 0.0),
        target_quality_good_excursion_threshold=float(training_config.get('targetQualityGoodExcursionThreshold', 0.82) or 0.82),
        target_quality_bad_excursion_threshold=float(training_config.get('targetQualityBadExcursionThreshold', 0.52) or 0.52),
        target_quality_good_dominance_ratio=float(training_config.get('targetQualityGoodDominanceRatio', 1.1) or 1.1),
        target_quality_bad_dominance_ratio=float(training_config.get('targetQualityBadDominanceRatio', 1.1) or 1.1),
        target_quality_good_counter_excursion_ceiling=float(
            training_config.get('targetQualityGoodCounterExcursionCeiling', 0.45) or 0.45
        ),
        target_quality_bad_counter_excursion_ceiling=float(
            training_config.get('targetQualityBadCounterExcursionCeiling', 0.45) or 0.45
        ),
    )


def _build_sequence_matrix(feature_frame: pd.DataFrame, observation_window: int):
    values = feature_frame.to_numpy(dtype=float)
    windows = []
    end_positions = []
    total_rows = len(feature_frame.index)

    for end_index in range(max(0, observation_window - 1), total_rows):
        start_index = end_index - observation_window + 1
        window = values[start_index:end_index + 1]
        if not np.isfinite(window).all():
            continue
        windows.append(window)
        end_positions.append(end_index)

    if not windows:
        return np.empty((0, observation_window, feature_frame.shape[1]), dtype=float), []

    return np.asarray(windows, dtype=float), end_positions


class NeuralEmaLowAdxSetupQualityV4BG020(Calculator):
    """
    Neural EMA Low ADX Setup Quality v4 · Broader Gate / Inv 2.0

    Source network:
    - network_id: ema_low_adx_setup_quality_cnn_v4
    - source run: 50a4dec5e9aa406d981a95656d750b07
    - feature profile: ema_low_adx_setup_quality
    - observation window: 24 bars

    Network signature:
    - event family: seed-120 style EMA reclaim setups in low-ADX conditions
    - target: first-touch good_setup vs rest
    - positive class: future upside reaches the good ATR target first
    - negative class: bad touch, timeout, or same-bar ambiguous resolution

    Outputs:
    - not_good_score: probability that the setup is not a clean good follow-through
    - good_score: probability that the setup is a clean good follow-through
    - edge_score: good_score - not_good_score, clipped to [-1, 1]
    """

    def __init__(self, symbol):
        super().__init__('NeuralEmaLowAdxSetupQualityV4BG020')

        bundle = _load_bundle()
        temp_symbol = Symbol(symbol.name, symbol.timeframe, len(symbol.candles), candles=symbol.candles)
        feature_config = _build_feature_config(temp_symbol, bundle)
        pipeline = BasicFeedForwardFeaturePipeline(temp_symbol, feature_config)
        pipeline.apply()

        manifest = dict(bundle.get('manifest') or {})
        metadata = dict(bundle.get('metadata') or {})
        model = bundle['model']
        model_metadata = dict(bundle.get('model_metadata') or {})
        feature_columns = list(
            manifest.get('feature_columns')
            or metadata.get('metrics', {}).get('feature_columns')
            or model_metadata.get('feature_columns')
            or []
        )
        if not feature_columns:
            raise ValueError('Neural EMA Low ADX indicator is missing feature column metadata.')

        feature_frame = temp_symbol.candles.loc[:, feature_columns].apply(pd.to_numeric, errors='coerce')
        observation_window = max(1, int(manifest.get('observation_window') or 24))
        X_sequences, end_positions = _build_sequence_matrix(feature_frame, observation_window)

        class_codes = list(manifest.get('class_codes') or model_metadata.get('class_codes') or [0, 1])
        class_labels = dict(manifest.get('class_labels') or model_metadata.get('class_labels') or {})
        good_index = None
        for index, class_code in enumerate(class_codes):
            label = str(class_labels.get(str(class_code)) or class_labels.get(class_code) or '').strip().lower()
            if label == 'good_setup':
                good_index = index
                break
        if good_index is None:
            good_index = int(manifest.get('good_class_index', 1) or 1)
        not_good_index = 0 if good_index != 0 else 1

        index = symbol.candles.index
        not_good_score = pd.Series(np.nan, index=index, dtype=float)
        good_score = pd.Series(np.nan, index=index, dtype=float)
        edge_score = pd.Series(np.nan, index=index, dtype=float)

        if len(end_positions) > 0:
            probabilities = model.predict_probabilities(model.transform_features(X_sequences))
            good_probabilities = np.clip(probabilities[:, good_index], 0.0, 1.0)
            not_good_probabilities = np.clip(probabilities[:, not_good_index], 0.0, 1.0)
            edge_values = np.clip(good_probabilities - not_good_probabilities, -1.0, 1.0)

            not_good_score.iloc[end_positions] = not_good_probabilities
            good_score.iloc[end_positions] = good_probabilities
            edge_score.iloc[end_positions] = edge_values

        symbol.add_feature(f'{self.name}_not_good_score', not_good_score)
        symbol.add_feature(f'{self.name}_good_score', good_score)
        symbol.add_feature(f'{self.name}_edge_score', edge_score)

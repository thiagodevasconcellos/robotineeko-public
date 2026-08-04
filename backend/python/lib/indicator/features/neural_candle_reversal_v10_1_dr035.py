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


ASSET_DIR = Path(__file__).resolve().parent / 'assets' / 'neural_candle_reversal_v10_1_dr035'
MANIFEST_PATH = ASSET_DIR / 'manifest.json'
METADATA_PATH = ASSET_DIR / 'model.metadata.json'
BEARISH_MODEL_PATH = ASSET_DIR / 'bearish_head_model.npz'
NEUTRAL_MODEL_PATH = ASSET_DIR / 'neutral_head_model.npz'
BULLISH_MODEL_PATH = ASSET_DIR / 'bullish_head_model.npz'


@lru_cache(maxsize=1)
def _load_bundle():
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f'Missing neural indicator manifest: {MANIFEST_PATH}')
    if not METADATA_PATH.exists():
        raise FileNotFoundError(f'Missing neural indicator metadata: {METADATA_PATH}')

    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    metadata = json.loads(METADATA_PATH.read_text(encoding='utf-8'))
    bearish_model, bearish_metadata = TemporalConvolutionalClassifier.load(BEARISH_MODEL_PATH)
    neutral_model, neutral_metadata = TemporalConvolutionalClassifier.load(NEUTRAL_MODEL_PATH)
    bullish_model, bullish_metadata = TemporalConvolutionalClassifier.load(BULLISH_MODEL_PATH)

    return {
        'manifest': manifest,
        'metadata': metadata,
        'bearish_model': bearish_model,
        'bearish_metadata': bearish_metadata,
        'neutral_model': neutral_model,
        'neutral_metadata': neutral_metadata,
        'bullish_model': bullish_model,
        'bullish_metadata': bullish_metadata,
    }


def _build_feature_config(symbol: Symbol, bundle: dict):
    config = dict((bundle.get('metadata') or {}).get('config') or {})
    manifest = dict(bundle.get('manifest') or {})
    return SupervisedFeatureConfig(
        symbol_name=str(symbol.name),
        timeframe=str(symbol.timeframe),
        bars=int(len(symbol.candles.index)),
        network_id=str(config.get('networkId') or bundle.get('metadata', {}).get('network_id') or 'candle_reversal_cnn_v10_1'),
        feature_profile='candle_reversal_context',
        observation_window=int(manifest.get('observation_window') or config.get('observationWindow') or 16),
        include_volume=True,
        normalize_volume=bool(config.get('normalizeVolume', False)),
        normalization_columns=list(config.get('normalizationColumns') or []),
        target_horizon=int(config.get('targetHorizon', 6) or 6),
        target_mode=str(config.get('targetMode') or 'future_candle_reversal_classification'),
        target_pretrend_lookback=int(config.get('pretrendLookback', 6) or 6),
        target_pretrend_threshold=float(config.get('pretrendThreshold', 1.2) or 1.2),
        target_reversal_threshold=float(config.get('reversalThreshold', 1.0) or 1.0),
        target_dominance_ratio=float(config.get('dominanceRatio', 1.35) or 1.35),
        target_clean_neutral_pretrend_ceiling=float(config.get('targetCleanNeutralPretrendCeiling', 0.0) or 0.0),
        target_clean_neutral_excursion_ceiling=float(config.get('targetCleanNeutralExcursionCeiling', 0.0) or 0.0),
        target_clean_positive_pretrend_floor=float(config.get('targetCleanPositivePretrendFloor', 0.0) or 0.0),
        target_clean_positive_excursion_floor=float(config.get('targetCleanPositiveExcursionFloor', 0.0) or 0.0),
    )


def _build_sequence_matrix(feature_frame: pd.DataFrame, observation_window: int):
    windows = []
    end_positions = []
    values = feature_frame.to_numpy(dtype=float)
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


class NeuralCandleReversalV10_1DR035(Calculator):
    """
    Neural Candle Reversal v10.1 · Directional Recovery 0.35

    Source network:
    - network_id: candle_reversal_cnn_v10_1
    - source run: fa1856715b63487d877492d93e303ef2
    - feature profile: candle_reversal_context
    - observation window: 16 bars
    - copied model assets live under indicator-owned storage

    Outputs:
    - bear_score: standalone bearish reversal probability from the bearish setup head
    - neutral_score: standalone no-reversal probability from the neutral setup head
    - bull_score: standalone bullish reversal probability from the bullish setup head
    - direction_score: bull_score - bear_score, clipped to [-1, 1]
    """

    def __init__(self, symbol):
        super().__init__('NeuralCandleReversalV10_1DR035')

        bundle = _load_bundle()
        temp_symbol = Symbol(symbol.name, symbol.timeframe, len(symbol.candles), candles=symbol.candles)
        feature_config = _build_feature_config(temp_symbol, bundle)
        pipeline = BasicFeedForwardFeaturePipeline(temp_symbol, feature_config)
        pipeline.apply()

        manifest = dict(bundle.get('manifest') or {})
        metadata = dict(bundle.get('metadata') or {})
        metrics = dict(metadata.get('metrics') or {})
        feature_columns = list(manifest.get('feature_columns') or metrics.get('feature_columns') or [])
        if not feature_columns:
            raise ValueError('Neural indicator is missing feature column metadata.')

        feature_frame = temp_symbol.candles.loc[:, feature_columns].apply(pd.to_numeric, errors='coerce')
        observation_window = max(1, int(manifest.get('observation_window') or 16))
        X_sequences, end_positions = _build_sequence_matrix(feature_frame, observation_window)

        index = symbol.candles.index
        bear_score = pd.Series(np.nan, index=index, dtype=float)
        neutral_score = pd.Series(np.nan, index=index, dtype=float)
        bull_score = pd.Series(np.nan, index=index, dtype=float)
        direction_score = pd.Series(np.nan, index=index, dtype=float)

        if len(end_positions) > 0:
            bearish_model = bundle['bearish_model']
            neutral_model = bundle['neutral_model']
            bullish_model = bundle['bullish_model']

            bearish_probabilities = bearish_model.predict_probabilities(bearish_model.transform_features(X_sequences))[:, 1]
            neutral_probabilities = neutral_model.predict_probabilities(neutral_model.transform_features(X_sequences))[:, 1]
            bullish_probabilities = bullish_model.predict_probabilities(bullish_model.transform_features(X_sequences))[:, 1]
            direction_values = np.clip(bullish_probabilities - bearish_probabilities, -1.0, 1.0)

            bear_score.iloc[end_positions] = np.clip(bearish_probabilities, 0.0, 1.0)
            neutral_score.iloc[end_positions] = np.clip(neutral_probabilities, 0.0, 1.0)
            bull_score.iloc[end_positions] = np.clip(bullish_probabilities, 0.0, 1.0)
            direction_score.iloc[end_positions] = direction_values

        symbol.add_feature(f'{self.name}_bear_score', bear_score)
        symbol.add_feature(f'{self.name}_neutral_score', neutral_score)
        symbol.add_feature(f'{self.name}_bull_score', bull_score)
        symbol.add_feature(f'{self.name}_direction_score', direction_score)

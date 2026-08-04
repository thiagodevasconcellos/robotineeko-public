import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ...calculator import Calculator
from ....neural.runners import (
    _build_supervised_feature_config,
    _predict_micro_cost_edge_v3_tradability_scores,
)
from ....neural.supervised.features import BasicFeedForwardFeaturePipeline
from ....neural.supervised.trainer import TemporalConvolutionalClassifier


ASSET_DIR = Path(__file__).resolve().parent / 'assets' / 'neural_micro_cost_edge_hybrid_s7_s4'
MANIFEST_PATH = ASSET_DIR / 'manifest.json'
V2_MODEL_PATH = ASSET_DIR / 'v2_model.npz'
V3_STAGE1_MODEL_PATH = ASSET_DIR / 'v3_stage1_model.npz'
V3_MANIFEST_PATH = ASSET_DIR / 'v3_manifest.json'


@lru_cache(maxsize=1)
def _load_bundle():
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f'Missing hybrid neural indicator manifest: {MANIFEST_PATH}')
    if not V2_MODEL_PATH.exists():
        raise FileNotFoundError(f'Missing hybrid v2 model asset: {V2_MODEL_PATH}')
    if not V3_STAGE1_MODEL_PATH.exists():
        raise FileNotFoundError(f'Missing hybrid v3 stage1 asset: {V3_STAGE1_MODEL_PATH}')
    if not V3_MANIFEST_PATH.exists():
        raise FileNotFoundError(f'Missing hybrid v3 manifest asset: {V3_MANIFEST_PATH}')

    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    v3_manifest = json.loads(V3_MANIFEST_PATH.read_text(encoding='utf-8'))
    v2_model, v2_metadata = TemporalConvolutionalClassifier.load(V2_MODEL_PATH)
    v3_stage1_model, v3_stage1_metadata = TemporalConvolutionalClassifier.load(V3_STAGE1_MODEL_PATH)

    return {
        'manifest': manifest,
        'v3_manifest': v3_manifest,
        'v2_model': v2_model,
        'v2_metadata': v2_metadata,
        'v3_stage1_model': v3_stage1_model,
        'v3_stage1_metadata': v3_stage1_metadata,
    }


def _coerce_numeric_frame(frame: pd.DataFrame):
    return frame.apply(pd.to_numeric, errors='coerce')


def _build_event_payload(config: dict, candles: pd.DataFrame):
    feature_config = _build_supervised_feature_config(config)
    pipeline = BasicFeedForwardFeaturePipeline.from_candles(feature_config, candles.to_dict('records'))
    pipeline.apply()

    symbol_candles = pipeline.symbol.candles.copy().reset_index(drop=True)
    feature_frame = _coerce_numeric_frame(symbol_candles[pipeline.feature_columns])
    open_series = pd.to_numeric(symbol_candles['open'], errors='coerce')
    high_series = pd.to_numeric(symbol_candles['high'], errors='coerce')
    low_series = pd.to_numeric(symbol_candles['low'], errors='coerce')
    observation_window = max(1, int(config.get('observationWindow', 24) or 24))
    horizon = max(1, int(config.get('targetHorizon', 5) or 5))
    pip_size = max(1e-8, float(config.get('pipSize', 0.0001) or 0.0001))
    round_trip_cost_pips = max(0.0, float(config.get('roundTripCostPips', 1.6) or 0.0))
    target_cost_edge_multiple = max(1.0, float(config.get('targetCostEdgeMultiple', 1.75) or 1.0))
    edge_hurdle_pips = round_trip_cost_pips * target_cost_edge_multiple
    edge_hurdle_price = edge_hurdle_pips * pip_size

    entry_ref_series = open_series.shift(-1)
    future_high, future_low = pipeline._build_future_extrema(
        pd.to_numeric(symbol_candles['high'], errors='coerce'),
        pd.to_numeric(symbol_candles['low'], errors='coerce'),
        horizon,
    )
    cost_edge_codes, resolution_codes = pipeline._build_future_cost_edge_codes(
        entry_ref_series,
        high_series,
        low_series,
        horizon=horizon,
        edge_hurdle_price=edge_hurdle_price,
    )

    frame = feature_frame.copy()
    frame['time'] = pd.to_numeric(symbol_candles['time'], errors='coerce')
    frame['source_bar_index'] = symbol_candles.index.to_numpy(dtype=int)
    frame['target_entry_ref'] = entry_ref_series
    frame['target_future_long_edge_pips'] = ((future_high - entry_ref_series) / pip_size).clip(lower=0.0)
    frame['target_future_short_edge_pips'] = ((entry_ref_series - future_low) / pip_size).clip(lower=0.0)
    frame['target_cost_edge_code'] = cost_edge_codes.astype(int)
    frame['target_cost_edge_resolution_code'] = resolution_codes.astype(int)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    if frame.empty:
        raise ValueError('No clean micro-cost-edge rows survived feature generation for the hybrid indicator.')

    long_view, short_view, feature_columns = pipeline._build_micro_cost_edge_canonical_views(frame[pipeline.feature_columns])
    long_values = long_view.to_numpy(dtype=float)
    short_values = short_view.to_numpy(dtype=float)
    frame_records = frame.reset_index(drop=True)
    sequence_end_positions = list(range(observation_window - 1, len(frame_records)))
    if not sequence_end_positions:
        raise ValueError('No canonical sequence rows survived the observation-window requirement for the hybrid indicator.')

    X_long = []
    X_short = []
    rows = []
    for end_position in sequence_end_positions:
        start_position = end_position - observation_window + 1
        row = frame_records.iloc[end_position]
        X_long.append(long_values[start_position:end_position + 1])
        X_short.append(short_values[start_position:end_position + 1])
        rows.append({
            'time': int(row['time']),
            'source_bar_index': int(row['source_bar_index']),
            'entry_bar_index': int(row['source_bar_index']) + 1,
            'target_entry_ref': float(row['target_entry_ref']),
            'actual_event_code': int(row['target_cost_edge_code']),
            'actual_resolution_code': int(row['target_cost_edge_resolution_code']),
            'future_long_edge_pips': float(row['target_future_long_edge_pips']),
            'future_short_edge_pips': float(row['target_future_short_edge_pips']),
        })

    return {
        'candles': symbol_candles,
        'event_table': pd.DataFrame(rows),
        'X_long': np.asarray(X_long, dtype=float),
        'X_short': np.asarray(X_short, dtype=float),
        'feature_columns': list(feature_columns),
        'observation_window': int(observation_window),
    }


def _build_context_feature_table(config: dict, candles: pd.DataFrame):
    feature_config = _build_supervised_feature_config(config)
    pipeline = BasicFeedForwardFeaturePipeline.from_candles(feature_config, candles.to_dict('records'))
    pipeline.apply()

    symbol_candles = pipeline.symbol.candles.copy().reset_index(drop=True)
    feature_frame = _coerce_numeric_frame(symbol_candles[pipeline.feature_columns]).copy()
    feature_frame['source_bar_index'] = symbol_candles.index.to_numpy(dtype=int)
    feature_frame = feature_frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    return feature_frame


def _score_v2_event_table(bundle: dict, candles: pd.DataFrame):
    config = dict(bundle['manifest'].get('v2_config') or {})
    event_payload = _build_event_payload(config, candles)
    model = bundle['v2_model']
    X_long = model.transform_features(event_payload['X_long'])
    X_short = model.transform_features(event_payload['X_short'])
    long_scores = model.predict_probabilities(X_long)[:, 1]
    short_scores = model.predict_probabilities(X_short)[:, 1]

    event_table = event_payload['event_table'].copy()
    event_table['v2_long_score'] = np.clip(np.asarray(long_scores, dtype=float), 0.0, 1.0)
    event_table['v2_short_score'] = np.clip(np.asarray(short_scores, dtype=float), 0.0, 1.0)
    return event_table


def _build_v3_gate_table(bundle: dict, candles: pd.DataFrame):
    config = dict(bundle['manifest'].get('v3_config') or {})
    event_payload = _build_event_payload(config, candles)
    tradability_scores, _, _ = _predict_micro_cost_edge_v3_tradability_scores(
        bundle['v3_stage1_model'],
        event_payload['X_long'],
        event_payload['X_short'],
    )
    gate_table = event_payload['event_table'].copy()
    gate_table['v3_gate_score'] = np.clip(np.asarray(tradability_scores, dtype=float), 0.0, 1.0)
    return gate_table[['source_bar_index', 'v3_gate_score']]


def _build_hybrid_table(bundle: dict, candles: pd.DataFrame):
    v2_event_table = _score_v2_event_table(bundle, candles)
    context_feature_table = _build_context_feature_table(dict(bundle['manifest'].get('v2_config') or {}), candles)
    v3_gate_table = _build_v3_gate_table(bundle, candles)

    merged = (
        v2_event_table
        .merge(context_feature_table, on='source_bar_index', how='left')
        .merge(v3_gate_table, on='source_bar_index', how='inner')
        .sort_values('source_bar_index')
        .reset_index(drop=True)
    )
    if merged.empty:
        raise ValueError('Hybrid neural indicator produced an empty merged event table.')

    direction_strength = np.column_stack([
        np.clip(np.asarray(merged['v2_short_score'], dtype=float), 0.0, 1.0),
        np.clip(np.asarray(merged['v2_long_score'], dtype=float), 0.0, 1.0),
    ])
    direction_sums = direction_strength.sum(axis=1, keepdims=True)
    direction_shares = np.divide(
        direction_strength,
        np.where(direction_sums > 0.0, direction_sums, 1.0),
    )
    zero_direction_mask = np.asarray(direction_sums.reshape(-1) <= 0.0, dtype=bool)
    if np.any(zero_direction_mask):
        direction_shares[zero_direction_mask] = 0.5

    gate_scores = np.clip(np.asarray(merged['v3_gate_score'], dtype=float), 0.0, 1.0)
    merged['hybrid_long_score'] = gate_scores * direction_shares[:, 1]
    merged['hybrid_short_score'] = gate_scores * direction_shares[:, 0]
    merged['hybrid_score'] = np.maximum(merged['hybrid_long_score'], merged['hybrid_short_score'])
    merged['dominant_short_flag'] = (merged['hybrid_short_score'] >= merged['hybrid_long_score']).astype(float)
    return merged


def _build_gate_series(merged: pd.DataFrame, profile: dict):
    safe_profile = dict(profile or {})
    mask = merged['dominant_short_flag'] > 0.5

    score_floor = safe_profile.get('score_floor')
    if score_floor is not None:
        mask &= pd.to_numeric(merged['hybrid_short_score'], errors='coerce') >= float(score_floor)

    rsi_min = safe_profile.get('rsi_min')
    if rsi_min is not None and 'mce_rsi_14' in merged.columns:
        mask &= pd.to_numeric(merged['mce_rsi_14'], errors='coerce') >= float(rsi_min)

    veto_recent_move_atr_3_gt = safe_profile.get('veto_recent_move_atr_3_gt')
    veto_close_location_gt = safe_profile.get('veto_close_location_gt')
    if (
        veto_recent_move_atr_3_gt is not None
        and veto_close_location_gt is not None
        and 'mce_recent_move_atr_3' in merged.columns
        and 'mce_close_location' in merged.columns
    ):
        mask &= ~(
            (pd.to_numeric(merged['mce_recent_move_atr_3'], errors='coerce') > float(veto_recent_move_atr_3_gt))
            & (pd.to_numeric(merged['mce_close_location'], errors='coerce') > float(veto_close_location_gt))
        )

    trendiness_min = safe_profile.get('trendiness_min')
    if trendiness_min is not None and 'mce_trendiness_14' in merged.columns:
        mask &= pd.to_numeric(merged['mce_trendiness_14'], errors='coerce') >= float(trendiness_min)

    recent_move_max = safe_profile.get('recent_move_max')
    if recent_move_max is not None and 'mce_recent_move_atr_3' in merged.columns:
        mask &= pd.to_numeric(merged['mce_recent_move_atr_3'], errors='coerce') <= float(recent_move_max)

    close_location_max = safe_profile.get('close_location_max')
    if close_location_max is not None and 'mce_close_location' in merged.columns:
        mask &= pd.to_numeric(merged['mce_close_location'], errors='coerce') <= float(close_location_max)

    return mask.astype(float)


class NeuralMicroCostEdgeHybridS7S4(Calculator):
    """
    Neural Micro Cost Edge Hybrid S7/S4.

    Source network lineage:
    - directional branch: micro_cost_edge_cnn_v2 / stage4_h8_x175_r046_w140
    - tradability gate: micro_cost_edge_cnn_v3 / stage7_v3_h8_x175_r100_w140
    - symbol/timeframe baseline: EURUSD M1
    - target horizon: 8 bars
    - edge hurdle: 2.8 pips (1.6 round-trip cost * 1.75 edge multiple)

    Output intent:
    - expose the frozen v2 directional scores
    - expose the frozen v3 tradability gate
    - expose the hybrid short/long score used in the research frontier
    - expose fixed-floor experimental gates for UI replay of the best-safe and cadence-comparator checkpoints
    """

    def __init__(self, symbol):
        super().__init__('NeuralMicroCostEdgeHybridS7S4')

        bundle = _load_bundle()
        candles = symbol.candles.copy().reset_index(drop=True)
        merged = _build_hybrid_table(bundle, candles)
        profiles = dict(bundle['manifest'].get('profiles') or {})

        index = symbol.candles.index
        series_map = {
            'v2_short_score': pd.Series(np.nan, index=index, dtype=float),
            'v2_long_score': pd.Series(np.nan, index=index, dtype=float),
            'v3_gate_score': pd.Series(np.nan, index=index, dtype=float),
            'hybrid_short_score': pd.Series(np.nan, index=index, dtype=float),
            'hybrid_long_score': pd.Series(np.nan, index=index, dtype=float),
            'hybrid_score': pd.Series(np.nan, index=index, dtype=float),
            'rsi_14': pd.Series(np.nan, index=index, dtype=float),
            'trendiness_14': pd.Series(np.nan, index=index, dtype=float),
            'recent_move_atr_3': pd.Series(np.nan, index=index, dtype=float),
            'close_location': pd.Series(np.nan, index=index, dtype=float),
            'best_safe_gate': pd.Series(np.nan, index=index, dtype=float),
            'cadence_gate': pd.Series(np.nan, index=index, dtype=float),
        }

        target_index = merged['source_bar_index'].astype(int).to_numpy()
        assignments = {
            'v2_short_score': np.clip(np.asarray(merged['v2_short_score'], dtype=float), 0.0, 1.0),
            'v2_long_score': np.clip(np.asarray(merged['v2_long_score'], dtype=float), 0.0, 1.0),
            'v3_gate_score': np.clip(np.asarray(merged['v3_gate_score'], dtype=float), 0.0, 1.0),
            'hybrid_short_score': np.clip(np.asarray(merged['hybrid_short_score'], dtype=float), 0.0, 1.0),
            'hybrid_long_score': np.clip(np.asarray(merged['hybrid_long_score'], dtype=float), 0.0, 1.0),
            'hybrid_score': np.clip(np.asarray(merged['hybrid_score'], dtype=float), 0.0, 1.0),
            'rsi_14': np.asarray(pd.to_numeric(merged.get('mce_rsi_14'), errors='coerce'), dtype=float),
            'trendiness_14': np.asarray(pd.to_numeric(merged.get('mce_trendiness_14'), errors='coerce'), dtype=float),
            'recent_move_atr_3': np.asarray(pd.to_numeric(merged.get('mce_recent_move_atr_3'), errors='coerce'), dtype=float),
            'close_location': np.asarray(pd.to_numeric(merged.get('mce_close_location'), errors='coerce'), dtype=float),
            'best_safe_gate': np.asarray(_build_gate_series(merged, profiles.get('best_safe')), dtype=float),
            'cadence_gate': np.asarray(_build_gate_series(merged, profiles.get('cadence_comparator')), dtype=float),
        }

        for key, values in assignments.items():
            series_map[key].iloc[target_index] = values
            symbol.add_feature(f'{self.name}_{key}', series_map[key])

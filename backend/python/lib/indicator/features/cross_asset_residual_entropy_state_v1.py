import math

import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


class CrossAssetResidualEntropyStateV1(Calculator):
    """
    Residual-entropy state for gating or routing direct residual shells.

    It rebuilds the fixed residual relation and exposes:
    - signed_residual_return and normalized_residual
    - residual_state: {-1, 0, 1} from normalized residual bands
    - residual_entropy: normalized rolling Shannon entropy of residual_state
    - residual_state_persistence: rolling share of consecutive repeated states
    - agreement and sync_score for optional coherence gating
    """

    def __init__(
        self,
        symbol,
        peerSymbol='GBPUSD',
        relation='direct',
        lookback=3,
        entropyWindow=24,
        smoothingPeriod=5,
        neutralBand=0.15,
    ):
        safe_peer_symbol = str(peerSymbol or '').strip().upper() or 'GBPUSD'
        safe_relation = str(relation or 'direct').strip().lower()
        if safe_relation not in {'direct', 'inverse'}:
            safe_relation = 'direct'
        relation_sign = 1.0 if safe_relation == 'direct' else -1.0
        safe_lookback = max(1, int(lookback or 1))
        safe_entropy_window = max(6, int(entropyWindow or 6))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        neutral_band_param = int(round(float(neutralBand or 15)))
        safe_neutral_band = float(neutral_band_param)
        if safe_neutral_band > 1.0:
            safe_neutral_band = safe_neutral_band / 100.0
        safe_neutral_band = max(0.01, safe_neutral_band)
        super().__init__(
            'CrossAssetResidualEntropyStateV1',
            safe_peer_symbol,
            safe_relation,
            safe_lookback,
            safe_entropy_window,
            safe_smoothing,
            neutral_band_param,
        )

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ['time', 'close']].copy()
        primary_frame['time'] = pd.to_numeric(primary_frame['time'], errors='coerce')
        primary_frame['primary_close'] = pd.to_numeric(primary_frame['close'], errors='coerce')

        peer_context = cross_asset_confirmation_module.ensure_market_data(
            safe_peer_symbol,
            symbol.timeframe,
            len(symbol.candles.index),
            source='cross_asset_residual_entropy_state_v1_indicator',
        )
        if not peer_context.get('ready'):
            raise ValueError(
                f'CrossAssetResidualEntropyStateV1 requires ready peer data for {safe_peer_symbol} {symbol.timeframe}.'
            )

        aligned = cross_asset_confirmation_module._build_aligned_peer_frame(
            primary_frame,
            peer_context,
            safe_peer_symbol,
        )

        primary_return = aligned['primary_close'].pct_change(safe_lookback, fill_method=None)
        peer_return = aligned['peer_close'].pct_change(safe_lookback, fill_method=None)
        adjusted_peer_return = peer_return * relation_sign
        signed_residual_return = primary_return - adjusted_peer_return

        denominator = primary_return.abs().add(adjusted_peer_return.abs()).replace(0.0, np.nan)
        normalized_residual = signed_residual_return.divide(denominator)
        normalized_residual = normalized_residual.clip(lower=-1.0, upper=1.0)

        residual_state = pd.Series(0.0, index=aligned.index, dtype=float)
        residual_state.loc[normalized_residual > safe_neutral_band] = 1.0
        residual_state.loc[normalized_residual < -safe_neutral_band] = -1.0

        def _rolling_entropy(window_series: pd.Series) -> float:
            clean = window_series.dropna()
            if len(clean) < 3:
                return np.nan
            values = clean.to_numpy(dtype=float)
            counts = np.array(
                [
                    float(np.sum(values < -0.5)),
                    float(np.sum((values >= -0.5) & (values <= 0.5))),
                    float(np.sum(values > 0.5)),
                ],
                dtype=float,
            )
            total = counts.sum()
            if total <= 0.0:
                return np.nan
            probs = counts / total
            probs = probs[probs > 0.0]
            if len(probs) == 0:
                return 0.0
            entropy = -float(np.sum(probs * np.log(probs)))
            return entropy / math.log(3.0)

        residual_entropy = residual_state.rolling(
            window=safe_entropy_window,
            min_periods=6,
        ).apply(_rolling_entropy, raw=False)

        repeated = (residual_state == residual_state.shift(1)).astype(float)
        repeated.loc[residual_state.isna() | residual_state.shift(1).isna()] = np.nan
        residual_state_persistence = repeated.rolling(
            window=safe_smoothing,
            min_periods=1,
        ).mean()

        agreement = pd.Series(0.0, index=aligned.index, dtype=float)
        valid_mask = primary_return.notna() & adjusted_peer_return.notna()
        if valid_mask.any():
            primary_direction = np.sign(primary_return[valid_mask].to_numpy(dtype=float))
            peer_direction = np.sign(adjusted_peer_return[valid_mask].to_numpy(dtype=float))
            agreement.loc[valid_mask] = primary_direction * peer_direction

        absolute_normalized_residual = normalized_residual.abs()
        sync_score = pd.Series(0.0, index=aligned.index, dtype=float)
        coherent_mask = agreement > 0.0
        sync_score.loc[coherent_mask] = (
            1.0 - absolute_normalized_residual.loc[coherent_mask]
        ).clip(lower=0.0, upper=1.0)
        sync_score = sync_score.rolling(window=safe_smoothing, min_periods=1).mean()

        symbol.add_feature(f'{self.name}_primary_return', primary_return)
        symbol.add_feature(f'{self.name}_peer_return', peer_return)
        symbol.add_feature(f'{self.name}_adjusted_peer_return', adjusted_peer_return)
        symbol.add_feature(f'{self.name}_signed_residual_return', signed_residual_return)
        symbol.add_feature(f'{self.name}_normalized_residual', normalized_residual)
        symbol.add_feature(f'{self.name}_residual_state', residual_state)
        symbol.add_feature(f'{self.name}_residual_entropy', residual_entropy)
        symbol.add_feature(f'{self.name}_residual_state_persistence', residual_state_persistence)
        symbol.add_feature(f'{self.name}_agreement', agreement)
        symbol.add_feature(f'{self.name}_sync_score', sync_score)

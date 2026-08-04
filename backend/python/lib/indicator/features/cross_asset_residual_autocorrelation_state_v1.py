import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


class CrossAssetResidualAutocorrelationStateV1(Calculator):
    """
    Residual-autocorrelation state for routing between cross-asset shells.

    It rebuilds the fixed residual relation and exposes:
    - signed_residual_return and normalized_residual
    - residual_autocorrelation: rolling lag-1 autocorrelation of normalized residual
    - residual_persistence: local signed persistence proxy
    - agreement and sync_score for optional coherence gating
    """

    def __init__(
        self,
        symbol,
        peerSymbol='GBPUSD',
        relation='direct',
        lookback=3,
        autocorrWindow=24,
        smoothingPeriod=5,
    ):
        safe_peer_symbol = str(peerSymbol or '').strip().upper() or 'GBPUSD'
        safe_relation = str(relation or 'direct').strip().lower()
        if safe_relation not in {'direct', 'inverse'}:
            safe_relation = 'direct'
        relation_sign = 1.0 if safe_relation == 'direct' else -1.0
        safe_lookback = max(1, int(lookback or 1))
        safe_window = max(6, int(autocorrWindow or 6))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        super().__init__(
            'CrossAssetResidualAutocorrelationStateV1',
            safe_peer_symbol,
            safe_relation,
            safe_lookback,
            safe_window,
            safe_smoothing,
        )

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ['time', 'close']].copy()
        primary_frame['time'] = pd.to_numeric(primary_frame['time'], errors='coerce')
        primary_frame['primary_close'] = pd.to_numeric(primary_frame['close'], errors='coerce')

        peer_context = cross_asset_confirmation_module.ensure_market_data(
            safe_peer_symbol,
            symbol.timeframe,
            len(symbol.candles.index),
            source='cross_asset_residual_autocorrelation_state_v1_indicator',
        )
        if not peer_context.get('ready'):
            raise ValueError(
                f'CrossAssetResidualAutocorrelationStateV1 requires ready peer data for {safe_peer_symbol} {symbol.timeframe}.'
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

        lagged = normalized_residual.shift(1)

        def _rolling_autocorr(window_series: pd.Series) -> float:
            clean = window_series.dropna()
            if len(clean) < 3:
                return np.nan
            current = clean.iloc[1:].to_numpy(dtype=float)
            prev = clean.iloc[:-1].to_numpy(dtype=float)
            if len(current) < 2:
                return np.nan
            current_std = np.std(current)
            prev_std = np.std(prev)
            if current_std == 0.0 or prev_std == 0.0:
                return np.nan
            return float(np.corrcoef(current, prev)[0, 1])

        residual_autocorrelation = normalized_residual.rolling(
            window=safe_window,
            min_periods=6,
        ).apply(_rolling_autocorr, raw=False)

        residual_persistence = (normalized_residual * lagged).rolling(
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
        symbol.add_feature(f'{self.name}_residual_autocorrelation', residual_autocorrelation)
        symbol.add_feature(f'{self.name}_residual_persistence', residual_persistence)
        symbol.add_feature(f'{self.name}_agreement', agreement)
        symbol.add_feature(f'{self.name}_sync_score', sync_score)

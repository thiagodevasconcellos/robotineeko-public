import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


class CrossAssetResidualSpreadV1(Calculator):
    """
    Cross-asset residual surface that exposes a relation-adjusted spread between
    primary and peer returns.

    The indicator keeps the raw aligned returns visible and derives:
    - adjusted_peer_return: peer return after direct/inverse relation alignment
    - signed_residual_return: primary minus adjusted peer return
    - normalized_residual: signed residual scaled by total absolute movement
    - absolute_normalized_residual: absolute normalized residual for cap filters
    - residual_mean, residual_std, residual_zscore: rolling normalized-residual context
    - agreement: sign agreement between primary return and adjusted peer return
    - sync_score: smoothed coherence score that stays high only when the pair moves together
    """

    def __init__(
        self,
        symbol,
        peerSymbol='GBPUSD',
        relation='direct',
        lookback=3,
        zscoreWindow=24,
        smoothingPeriod=5,
    ):
        safe_peer_symbol = str(peerSymbol or '').strip().upper() or 'GBPUSD'
        safe_relation = str(relation or 'direct').strip().lower()
        if safe_relation not in {'direct', 'inverse'}:
            safe_relation = 'direct'
        relation_sign = 1.0 if safe_relation == 'direct' else -1.0
        safe_lookback = max(1, int(lookback or 1))
        safe_zscore_window = max(2, int(zscoreWindow or 2))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        super().__init__(
            'CrossAssetResidualSpreadV1',
            safe_peer_symbol,
            safe_relation,
            safe_lookback,
            safe_zscore_window,
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
            source='cross_asset_residual_spread_v1_indicator',
        )
        if not peer_context.get('ready'):
            raise ValueError(
                f'CrossAssetResidualSpreadV1 requires ready peer data for {safe_peer_symbol} {symbol.timeframe}.'
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
        absolute_normalized_residual = normalized_residual.abs()

        residual_mean = normalized_residual.rolling(window=safe_zscore_window, min_periods=3).mean()
        residual_std = (
            normalized_residual.rolling(window=safe_zscore_window, min_periods=3).std(ddof=0)
        )
        residual_std = residual_std.replace(0.0, np.nan)
        residual_zscore = (normalized_residual - residual_mean).divide(residual_std)

        agreement = pd.Series(0.0, index=aligned.index, dtype=float)
        valid_mask = primary_return.notna() & adjusted_peer_return.notna()
        if valid_mask.any():
            primary_direction = np.sign(primary_return[valid_mask].to_numpy(dtype=float))
            peer_direction = np.sign(adjusted_peer_return[valid_mask].to_numpy(dtype=float))
            agreement.loc[valid_mask] = primary_direction * peer_direction

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
        symbol.add_feature(
            f'{self.name}_absolute_normalized_residual',
            absolute_normalized_residual,
        )
        symbol.add_feature(f'{self.name}_residual_mean', residual_mean)
        symbol.add_feature(f'{self.name}_residual_std', residual_std)
        symbol.add_feature(f'{self.name}_residual_zscore', residual_zscore)
        symbol.add_feature(f'{self.name}_agreement', agreement)
        symbol.add_feature(f'{self.name}_sync_score', sync_score)

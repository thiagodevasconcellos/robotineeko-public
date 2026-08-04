import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


class CrossAssetDynamicHedgeResidualV1(Calculator):
    """
    Cross-asset residual surface with a dynamic hedge ratio on aligned returns.

    The indicator keeps the raw aligned returns visible and derives:
    - adjusted_peer_return: peer return after direct/inverse alignment
    - hedge_beta, hedge_beta_smooth, beta_stability: rolling hedge relation
    - beta_adjusted_peer_return: aligned peer return scaled by dynamic hedge beta
    - signed_residual_return: primary minus beta-adjusted peer return
    - normalized_residual, absolute_normalized_residual
    - residual_mean, residual_std, residual_zscore
    - agreement: sign agreement between primary return and beta-adjusted peer return
    - sync_score: smoothed coherence score under the dynamic hedge relation
    """

    def __init__(
        self,
        symbol,
        peerSymbol='GBPUSD',
        relation='direct',
        lookback=3,
        betaWindow=24,
        zscoreWindow=24,
        smoothingPeriod=5,
    ):
        safe_peer_symbol = str(peerSymbol or '').strip().upper() or 'GBPUSD'
        safe_relation = str(relation or 'direct').strip().lower()
        if safe_relation not in {'direct', 'inverse'}:
            safe_relation = 'direct'
        relation_sign = 1.0 if safe_relation == 'direct' else -1.0
        safe_lookback = max(1, int(lookback or 1))
        safe_beta_window = max(6, int(betaWindow or 6))
        safe_zscore_window = max(3, int(zscoreWindow or 3))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        super().__init__(
            'CrossAssetDynamicHedgeResidualV1',
            safe_peer_symbol,
            safe_relation,
            safe_lookback,
            safe_beta_window,
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
            source='cross_asset_dynamic_hedge_residual_v1_indicator',
        )
        if not peer_context.get('ready'):
            raise ValueError(
                f'CrossAssetDynamicHedgeResidualV1 requires ready peer data for {safe_peer_symbol} {symbol.timeframe}.'
            )

        aligned = cross_asset_confirmation_module._build_aligned_peer_frame(
            primary_frame,
            peer_context,
            safe_peer_symbol,
        )

        primary_return = aligned['primary_close'].pct_change(safe_lookback, fill_method=None)
        peer_return = aligned['peer_close'].pct_change(safe_lookback, fill_method=None)
        adjusted_peer_return = peer_return * relation_sign

        return_mean = primary_return.rolling(window=safe_beta_window, min_periods=6).mean()
        peer_mean = adjusted_peer_return.rolling(window=safe_beta_window, min_periods=6).mean()
        covariance = (
            (primary_return - return_mean) * (adjusted_peer_return - peer_mean)
        ).rolling(window=safe_beta_window, min_periods=6).mean()
        peer_variance = (
            (adjusted_peer_return - peer_mean) ** 2
        ).rolling(window=safe_beta_window, min_periods=6).mean()
        peer_variance = peer_variance.replace(0.0, np.nan)
        hedge_beta = covariance.divide(peer_variance)
        hedge_beta = hedge_beta.clip(lower=-3.0, upper=3.0)

        hedge_beta_smooth = hedge_beta.rolling(window=safe_smoothing, min_periods=1).mean()
        beta_scale = hedge_beta_smooth.abs().clip(lower=0.05)
        beta_delta = (hedge_beta - hedge_beta_smooth).abs()
        beta_stability = 1.0 - beta_delta.divide(beta_scale)
        beta_stability = beta_stability.clip(lower=0.0, upper=1.0)

        beta_adjusted_peer_return = adjusted_peer_return * hedge_beta_smooth
        signed_residual_return = primary_return - beta_adjusted_peer_return

        denominator = primary_return.abs().add(beta_adjusted_peer_return.abs()).replace(0.0, np.nan)
        normalized_residual = signed_residual_return.divide(denominator)
        normalized_residual = normalized_residual.clip(lower=-1.0, upper=1.0)
        absolute_normalized_residual = normalized_residual.abs()

        residual_mean = normalized_residual.rolling(window=safe_zscore_window, min_periods=3).mean()
        residual_std = normalized_residual.rolling(window=safe_zscore_window, min_periods=3).std(ddof=0)
        residual_std = residual_std.replace(0.0, np.nan)
        residual_zscore = (normalized_residual - residual_mean).divide(residual_std)

        agreement = pd.Series(0.0, index=aligned.index, dtype=float)
        valid_mask = primary_return.notna() & beta_adjusted_peer_return.notna()
        if valid_mask.any():
            primary_direction = np.sign(primary_return[valid_mask].to_numpy(dtype=float))
            peer_direction = np.sign(beta_adjusted_peer_return[valid_mask].to_numpy(dtype=float))
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
        symbol.add_feature(f'{self.name}_hedge_beta', hedge_beta)
        symbol.add_feature(f'{self.name}_hedge_beta_smooth', hedge_beta_smooth)
        symbol.add_feature(f'{self.name}_beta_stability', beta_stability)
        symbol.add_feature(f'{self.name}_beta_adjusted_peer_return', beta_adjusted_peer_return)
        symbol.add_feature(f'{self.name}_signed_residual_return', signed_residual_return)
        symbol.add_feature(f'{self.name}_normalized_residual', normalized_residual)
        symbol.add_feature(f'{self.name}_absolute_normalized_residual', absolute_normalized_residual)
        symbol.add_feature(f'{self.name}_residual_mean', residual_mean)
        symbol.add_feature(f'{self.name}_residual_std', residual_std)
        symbol.add_feature(f'{self.name}_residual_zscore', residual_zscore)
        symbol.add_feature(f'{self.name}_agreement', agreement)
        symbol.add_feature(f'{self.name}_sync_score', sync_score)

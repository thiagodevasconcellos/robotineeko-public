import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


class CrossAssetCointegrationGateV1(Calculator):
    """
    Rolling cointegration-style relation gate for cross-asset studies.

    The indicator aligns a peer symbol to the current chart and exposes:
    - primary_return, peer_return, adjusted_peer_return
    - primary_path, adjusted_peer_path: relation-aligned log-price paths
    - hedge_beta: rolling hedge ratio between the aligned paths
    - hedge_beta_smooth, beta_stability: short-horizon stability proxy
    - path_correlation: rolling path correlation under the aligned relation
    - spread, spread_mean, spread_std, spread_zscore
    - cointegration_score: blended relation-stability score
    """

    def __init__(
        self,
        symbol,
        peerSymbol='GBPUSD',
        relation='direct',
        lookback=3,
        betaWindow=36,
        zscoreWindow=36,
        smoothingPeriod=5,
    ):
        safe_peer_symbol = str(peerSymbol or '').strip().upper() or 'GBPUSD'
        safe_relation = str(relation or 'direct').strip().lower()
        if safe_relation not in {'direct', 'inverse'}:
            safe_relation = 'direct'
        relation_sign = 1.0 if safe_relation == 'direct' else -1.0
        safe_lookback = max(1, int(lookback or 1))
        safe_beta_window = max(6, int(betaWindow or 6))
        safe_zscore_window = max(6, int(zscoreWindow or 6))
        safe_smoothing = max(2, int(smoothingPeriod or 2))
        super().__init__(
            'CrossAssetCointegrationGateV1',
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
            source='cross_asset_cointegration_gate_v1_indicator',
        )
        if not peer_context.get('ready'):
            raise ValueError(
                f'CrossAssetCointegrationGateV1 requires ready peer data for {safe_peer_symbol} {symbol.timeframe}.'
            )

        aligned = cross_asset_confirmation_module._build_aligned_peer_frame(
            primary_frame,
            peer_context,
            safe_peer_symbol,
        )

        primary_return = aligned['primary_close'].pct_change(safe_lookback, fill_method=None)
        peer_return = aligned['peer_close'].pct_change(safe_lookback, fill_method=None)
        adjusted_peer_return = peer_return * relation_sign

        primary_path = np.log(aligned['primary_close'].replace(0.0, np.nan))
        adjusted_peer_path = np.log(aligned['peer_close'].replace(0.0, np.nan)) * relation_sign

        beta_mean = primary_path.rolling(window=safe_beta_window, min_periods=6).mean()
        peer_mean = adjusted_peer_path.rolling(window=safe_beta_window, min_periods=6).mean()
        covariance = (
            (primary_path - beta_mean) * (adjusted_peer_path - peer_mean)
        ).rolling(window=safe_beta_window, min_periods=6).mean()
        peer_variance = (
            (adjusted_peer_path - peer_mean) ** 2
        ).rolling(window=safe_beta_window, min_periods=6).mean()
        peer_variance = peer_variance.replace(0.0, np.nan)
        hedge_beta = covariance.divide(peer_variance)

        hedge_beta_smooth = hedge_beta.rolling(window=safe_smoothing, min_periods=1).mean()
        beta_scale = hedge_beta_smooth.abs().clip(lower=0.05)
        beta_delta = (hedge_beta - hedge_beta_smooth).abs()
        beta_stability = 1.0 - beta_delta.divide(beta_scale)
        beta_stability = beta_stability.clip(lower=0.0, upper=1.0)

        spread = primary_path - hedge_beta * adjusted_peer_path
        spread_mean = spread.rolling(window=safe_zscore_window, min_periods=6).mean()
        spread_std = spread.rolling(window=safe_zscore_window, min_periods=6).std(ddof=0)
        spread_std = spread_std.replace(0.0, np.nan)
        spread_zscore = (spread - spread_mean).divide(spread_std)

        path_correlation = primary_path.rolling(window=safe_beta_window, min_periods=6).corr(adjusted_peer_path)
        correlation_quality = path_correlation.clip(lower=0.0, upper=1.0)
        spread_tightness = 1.0 - spread_zscore.abs().divide(2.5)
        spread_tightness = spread_tightness.clip(lower=0.0, upper=1.0)

        cointegration_score = (
            correlation_quality.fillna(0.0) * 0.45
            + beta_stability.fillna(0.0) * 0.25
            + spread_tightness.fillna(0.0) * 0.30
        ).clip(lower=0.0, upper=1.0)

        symbol.add_feature(f'{self.name}_primary_return', primary_return)
        symbol.add_feature(f'{self.name}_peer_return', peer_return)
        symbol.add_feature(f'{self.name}_adjusted_peer_return', adjusted_peer_return)
        symbol.add_feature(f'{self.name}_primary_path', primary_path)
        symbol.add_feature(f'{self.name}_adjusted_peer_path', adjusted_peer_path)
        symbol.add_feature(f'{self.name}_hedge_beta', hedge_beta)
        symbol.add_feature(f'{self.name}_hedge_beta_smooth', hedge_beta_smooth)
        symbol.add_feature(f'{self.name}_beta_stability', beta_stability)
        symbol.add_feature(f'{self.name}_path_correlation', path_correlation)
        symbol.add_feature(f'{self.name}_spread', spread)
        symbol.add_feature(f'{self.name}_spread_mean', spread_mean)
        symbol.add_feature(f'{self.name}_spread_std', spread_std)
        symbol.add_feature(f'{self.name}_spread_zscore', spread_zscore)
        symbol.add_feature(f'{self.name}_cointegration_score', cointegration_score)

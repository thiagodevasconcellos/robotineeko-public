import numpy as np
import pandas as pd

from ...calculator import Calculator
from ....services.market_data_service import ensure_market_data


def _build_aligned_peer_frame(primary_frame: pd.DataFrame, peer_context: dict, peer_symbol: str):
    peer_candles = pd.DataFrame(list(peer_context.get('candles') or []))
    if peer_candles.empty:
        raise ValueError(
            f'CrossAssetConfirmation could not find peer candles for {str(peer_symbol or "").strip().upper()}.'
        )

    required_columns = {'time', 'close'}
    if not required_columns.issubset(set(peer_candles.columns)):
        raise ValueError(
            f'CrossAssetConfirmation received an invalid peer snapshot for {str(peer_symbol or "").strip().upper()}.'
        )

    peer_frame = (
        peer_candles.loc[:, ['time', 'close']]
        .rename(columns={'close': 'peer_close'})
        .copy()
    )
    peer_frame['peer_close'] = pd.to_numeric(peer_frame['peer_close'], errors='coerce')
    peer_frame['time'] = pd.to_numeric(peer_frame['time'], errors='coerce')

    aligned = primary_frame.merge(peer_frame, on='time', how='left')
    return aligned


class CrossAssetConfirmation(Calculator):
    """
    Cross-asset confirmation scaffold for the new aggressive scalp study.

    The indicator aligns a peer symbol to the current chart by time and exposes a
    small, auditable set of intermarket features:
    - primary_return: local percentage return over the requested lookback
    - peer_return: peer percentage return over the same lookback
    - return_gap: primary minus peer return
    - agreement: sign agreement between primary and peer (-1, 0, +1)
    - confirmation_score: smoothed confirmation score when both assets move together
    - divergence_score: smoothed divergence score when the assets move against each other
    """

    def __init__(self, symbol, peerSymbol='GBPUSD', lookback=3, smoothingPeriod=5):
        safe_peer_symbol = str(peerSymbol or '').strip().upper() or 'GBPUSD'
        safe_lookback = max(1, int(lookback or 1))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        super().__init__('CrossAssetConfirmation', safe_peer_symbol, safe_lookback, safe_smoothing)

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ['time', 'close']].copy()
        primary_frame['time'] = pd.to_numeric(primary_frame['time'], errors='coerce')
        primary_frame['primary_close'] = pd.to_numeric(primary_frame['close'], errors='coerce')

        peer_context = ensure_market_data(
            safe_peer_symbol,
            symbol.timeframe,
            len(symbol.candles.index),
            source='cross_asset_confirmation_indicator',
        )
        if not peer_context.get('ready'):
            raise ValueError(
                f'CrossAssetConfirmation requires ready peer data for {safe_peer_symbol} {symbol.timeframe}.'
            )

        aligned = _build_aligned_peer_frame(primary_frame, peer_context, safe_peer_symbol)

        primary_return = aligned['primary_close'].pct_change(safe_lookback, fill_method=None)
        peer_return = aligned['peer_close'].pct_change(safe_lookback, fill_method=None)
        return_gap = primary_return - peer_return

        agreement = pd.Series(0.0, index=aligned.index, dtype=float)
        valid_mask = primary_return.notna() & peer_return.notna()
        if valid_mask.any():
            primary_direction = np.sign(primary_return[valid_mask].to_numpy(dtype=float))
            peer_direction = np.sign(peer_return[valid_mask].to_numpy(dtype=float))
            agreement.loc[valid_mask] = primary_direction * peer_direction

        denominator = primary_return.abs().add(peer_return.abs()).replace(0.0, np.nan)
        normalized_gap = return_gap.abs().divide(denominator)
        normalized_gap = normalized_gap.clip(lower=0.0, upper=1.0)

        raw_confirmation = pd.Series(0.0, index=aligned.index, dtype=float)
        raw_divergence = pd.Series(0.0, index=aligned.index, dtype=float)

        positive_agreement = agreement > 0.0
        negative_agreement = agreement < 0.0
        raw_confirmation.loc[positive_agreement] = (1.0 - normalized_gap.loc[positive_agreement]).clip(lower=0.0, upper=1.0)
        raw_divergence.loc[negative_agreement] = normalized_gap.loc[negative_agreement].fillna(0.0)

        confirmation_score = raw_confirmation.rolling(window=safe_smoothing, min_periods=1).mean()
        divergence_score = raw_divergence.rolling(window=safe_smoothing, min_periods=1).mean()

        symbol.add_feature(f'{self.name}_primary_return', primary_return)
        symbol.add_feature(f'{self.name}_peer_return', peer_return)
        symbol.add_feature(f'{self.name}_return_gap', return_gap)
        symbol.add_feature(f'{self.name}_agreement', agreement)
        symbol.add_feature(f'{self.name}_confirmation_score', confirmation_score)
        symbol.add_feature(f'{self.name}_divergence_score', divergence_score)

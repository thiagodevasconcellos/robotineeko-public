import numpy as np
import pandas as pd

from ...calculator import Calculator
from ....services.market_data_service import ensure_market_data


def _aligned_peer_ohlc_frame(primary_frame: pd.DataFrame, peer_context: dict, peer_symbol: str) -> pd.DataFrame:
    peer_candles = pd.DataFrame(list(peer_context.get("candles") or []))
    if peer_candles.empty:
        raise ValueError(
            f'CrossAssetNewsProxyShockV1 could not find peer candles for {str(peer_symbol or "").strip().upper()}.'
        )

    required_columns = {"time", "high", "low", "close"}
    if not required_columns.issubset(set(peer_candles.columns)):
        raise ValueError(
            f'CrossAssetNewsProxyShockV1 received an invalid peer snapshot for {str(peer_symbol or "").strip().upper()}.'
        )

    peer_frame = (
        peer_candles.loc[:, ["time", "high", "low", "close"]]
        .rename(
            columns={
                "high": "peer_high",
                "low": "peer_low",
                "close": "peer_close",
            }
        )
        .copy()
    )
    for column in ("peer_high", "peer_low", "peer_close"):
        peer_frame[column] = pd.to_numeric(peer_frame[column], errors="coerce")
    peer_frame["time"] = pd.to_numeric(peer_frame["time"], errors="coerce")

    return primary_frame.merge(peer_frame, on="time", how="left")


class CrossAssetNewsProxyShockV1(Calculator):
    """
    Volatility-shock proxy for news-blackout experiments on cross-asset shells.

    The indicator aligns the peer OHLC to the primary symbol and exposes:
    - primary and peer absolute returns
    - primary and peer candle-range fractions
    - primary and peer shock proxies: max(abs return, range fraction)
    - rolling z-scores of those shock proxies
    - combined shock z-score across primary and peer
    - relation-adjusted agreement so the route can be combined with peer state
    """

    def __init__(
        self,
        symbol,
        peerSymbol="GBPUSD",
        relation="direct",
        lookback=1,
        shockWindow=24,
    ):
        safe_peer_symbol = str(peerSymbol or "").strip().upper() or "GBPUSD"
        safe_relation = str(relation or "direct").strip().lower()
        if safe_relation not in {"direct", "inverse"}:
            safe_relation = "direct"
        relation_sign = 1.0 if safe_relation == "direct" else -1.0
        safe_lookback = max(1, int(lookback or 1))
        safe_shock_window = max(6, int(shockWindow or 6))
        super().__init__(
            "CrossAssetNewsProxyShockV1",
            safe_peer_symbol,
            safe_relation,
            safe_lookback,
            safe_shock_window,
        )

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ["time", "high", "low", "close"]].copy()
        primary_frame["time"] = pd.to_numeric(primary_frame["time"], errors="coerce")
        for column in ("high", "low", "close"):
            primary_frame[column] = pd.to_numeric(primary_frame[column], errors="coerce")
        primary_frame = primary_frame.rename(
            columns={
                "high": "primary_high",
                "low": "primary_low",
                "close": "primary_close",
            }
        )

        peer_context = ensure_market_data(
            safe_peer_symbol,
            symbol.timeframe,
            len(symbol.candles.index),
            source="cross_asset_news_proxy_shock_v1_indicator",
        )
        if not peer_context.get("ready"):
            raise ValueError(
                f"CrossAssetNewsProxyShockV1 requires ready peer data for {safe_peer_symbol} {symbol.timeframe}."
            )

        aligned = _aligned_peer_ohlc_frame(primary_frame, peer_context, safe_peer_symbol)

        primary_return = aligned["primary_close"].pct_change(safe_lookback, fill_method=None)
        peer_return = aligned["peer_close"].pct_change(safe_lookback, fill_method=None)
        adjusted_peer_return = peer_return * relation_sign

        primary_prev_close = aligned["primary_close"].shift(safe_lookback).replace(0.0, np.nan)
        peer_prev_close = aligned["peer_close"].shift(safe_lookback).replace(0.0, np.nan)

        primary_range_fraction = (
            (aligned["primary_high"] - aligned["primary_low"]).abs().divide(primary_prev_close)
        )
        peer_range_fraction = (
            (aligned["peer_high"] - aligned["peer_low"]).abs().divide(peer_prev_close)
        )

        primary_abs_return = primary_return.abs()
        peer_abs_return = peer_return.abs()

        primary_proxy_move = pd.concat([primary_abs_return, primary_range_fraction], axis=1).max(axis=1)
        peer_proxy_move = pd.concat([peer_abs_return, peer_range_fraction], axis=1).max(axis=1)

        primary_proxy_mean = primary_proxy_move.rolling(window=safe_shock_window, min_periods=6).mean()
        primary_proxy_std = (
            primary_proxy_move.rolling(window=safe_shock_window, min_periods=6).std(ddof=0).replace(0.0, np.nan)
        )
        peer_proxy_mean = peer_proxy_move.rolling(window=safe_shock_window, min_periods=6).mean()
        peer_proxy_std = (
            peer_proxy_move.rolling(window=safe_shock_window, min_periods=6).std(ddof=0).replace(0.0, np.nan)
        )

        primary_proxy_zscore = (primary_proxy_move - primary_proxy_mean).divide(primary_proxy_std)
        peer_proxy_zscore = (peer_proxy_move - peer_proxy_mean).divide(peer_proxy_std)
        combined_proxy_zscore = pd.concat([primary_proxy_zscore, peer_proxy_zscore], axis=1).max(axis=1)

        agreement = pd.Series(0.0, index=aligned.index, dtype=float)
        valid_mask = primary_return.notna() & adjusted_peer_return.notna()
        if valid_mask.any():
            primary_direction = np.sign(primary_return[valid_mask].to_numpy(dtype=float))
            peer_direction = np.sign(adjusted_peer_return[valid_mask].to_numpy(dtype=float))
            agreement.loc[valid_mask] = primary_direction * peer_direction

        symbol.add_feature(f"{self.name}_primary_abs_return", primary_abs_return)
        symbol.add_feature(f"{self.name}_peer_abs_return", peer_abs_return)
        symbol.add_feature(f"{self.name}_primary_range_fraction", primary_range_fraction)
        symbol.add_feature(f"{self.name}_peer_range_fraction", peer_range_fraction)
        symbol.add_feature(f"{self.name}_primary_proxy_move", primary_proxy_move)
        symbol.add_feature(f"{self.name}_peer_proxy_move", peer_proxy_move)
        symbol.add_feature(f"{self.name}_primary_proxy_zscore", primary_proxy_zscore)
        symbol.add_feature(f"{self.name}_peer_proxy_zscore", peer_proxy_zscore)
        symbol.add_feature(f"{self.name}_combined_proxy_zscore", combined_proxy_zscore)
        symbol.add_feature(f"{self.name}_agreement", agreement)

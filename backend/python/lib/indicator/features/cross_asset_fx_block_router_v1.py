import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


def _parse_group_tokens(raw_value: str | None, fallback: list[str]) -> list[str]:
    if raw_value is None:
        return list(fallback)
    raw_text = str(raw_value)
    if "," in raw_text:
        separator = ","
    elif "|" in raw_text:
        separator = "|"
    elif "z" in raw_text:
        separator = "z"
    else:
        separator = ","
    tokens = [str(item).strip().upper() for item in raw_text.split(separator)]
    safe_tokens = [token for token in tokens if token]
    return safe_tokens or list(fallback)


def _load_adjusted_block_return(
    *,
    primary_frame: pd.DataFrame,
    timeframe: str,
    bars: int,
    peer_symbols: list[str],
    relation_sign: float,
    lookback: int,
    source: str,
) -> pd.Series:
    returns: list[pd.Series] = []
    for peer_symbol in peer_symbols:
        peer_context = cross_asset_confirmation_module.ensure_market_data(
            peer_symbol,
            timeframe,
            bars,
            source=source,
        )
        if not peer_context.get("ready"):
            raise ValueError(
                f"CrossAssetFxBlockRouterV1 requires ready peer data for {peer_symbol} {timeframe}."
            )
        aligned = cross_asset_confirmation_module._build_aligned_peer_frame(
            primary_frame,
            peer_context,
            peer_symbol,
        )
        peer_close = pd.to_numeric(aligned["peer_close"], errors="coerce")
        peer_return = peer_close.pct_change(lookback, fill_method=None)
        returns.append(peer_return * relation_sign)

    if not returns:
        return pd.Series(np.nan, index=primary_frame.index, dtype=float)

    return pd.concat(returns, axis=1).mean(axis=1)


class CrossAssetFxBlockRouterV1(Calculator):
    """
    Route EURUSD by dominant FX block instead of single-peer state.
    """

    def __init__(
        self,
        symbol,
        europePeerSymbols="GBPUSD",
        commodityPeerSymbols="AUDUSDzNZDUSD",
        dollarPeerSymbols="USDCHF",
        lookback=3,
        smoothingPeriod=5,
    ):
        europe_peers = _parse_group_tokens(europePeerSymbols, ["GBPUSD"])
        commodity_peers = _parse_group_tokens(commodityPeerSymbols, ["AUDUSD", "NZDUSD"])
        dollar_peers = _parse_group_tokens(dollarPeerSymbols, ["USDCHF"])
        safe_lookback = max(1, int(lookback or 1))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        super().__init__(
            "CrossAssetFxBlockRouterV1",
            "z".join(europe_peers),
            "z".join(commodity_peers),
            "z".join(dollar_peers),
            safe_lookback,
            safe_smoothing,
        )

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ["time", "close"]].copy()
        primary_frame["time"] = pd.to_numeric(primary_frame["time"], errors="coerce")
        primary_frame["primary_close"] = pd.to_numeric(primary_frame["close"], errors="coerce")
        primary_return = primary_frame["primary_close"].pct_change(safe_lookback, fill_method=None)

        europe_block_return = _load_adjusted_block_return(
            primary_frame=primary_frame,
            timeframe=symbol.timeframe,
            bars=len(symbol.candles.index),
            peer_symbols=europe_peers,
            relation_sign=1.0,
            lookback=safe_lookback,
            source="cross_asset_fx_block_router_v1_europe",
        )
        commodity_block_return = _load_adjusted_block_return(
            primary_frame=primary_frame,
            timeframe=symbol.timeframe,
            bars=len(symbol.candles.index),
            peer_symbols=commodity_peers,
            relation_sign=1.0,
            lookback=safe_lookback,
            source="cross_asset_fx_block_router_v1_commodity",
        )
        dollar_block_return = _load_adjusted_block_return(
            primary_frame=primary_frame,
            timeframe=symbol.timeframe,
            bars=len(symbol.candles.index),
            peer_symbols=dollar_peers,
            relation_sign=-1.0,
            lookback=safe_lookback,
            source="cross_asset_fx_block_router_v1_dollar",
        )

        block_frame = pd.concat(
            [
                europe_block_return.rename("europe"),
                commodity_block_return.rename("commodity"),
                dollar_block_return.rename("dollar"),
            ],
            axis=1,
        )
        block_strengths = block_frame.abs()
        total_strength = block_strengths.sum(axis=1, min_count=1).replace(0.0, np.nan)

        dominant_block_code = np.zeros(len(primary_frame.index), dtype=float)
        dominant_block_return = np.full(len(primary_frame.index), np.nan, dtype=float)
        dominant_block_strength = np.full(len(primary_frame.index), np.nan, dtype=float)
        second_block_strength = np.full(len(primary_frame.index), np.nan, dtype=float)

        for row_index, row_values in enumerate(block_strengths.to_numpy(dtype=float)):
            finite_mask = np.isfinite(row_values)
            if not finite_mask.any():
                continue
            finite_values = row_values[finite_mask]
            local_position = int(np.argmax(finite_values))
            original_positions = np.flatnonzero(finite_mask)
            dominant_position = int(original_positions[local_position])
            ordered = sorted((float(value) for value in finite_values), reverse=True)
            dominant_block_code[row_index] = float(dominant_position + 1)
            dominant_block_strength[row_index] = float(row_values[dominant_position])
            second_block_strength[row_index] = float(ordered[1]) if len(ordered) > 1 else 0.0
            dominant_block_return[row_index] = float(block_frame.iloc[row_index, dominant_position])

        dominant_block_code = pd.Series(dominant_block_code, index=primary_frame.index, dtype=float)
        dominant_block_return = pd.Series(dominant_block_return, index=primary_frame.index, dtype=float)
        dominant_block_strength = pd.Series(dominant_block_strength, index=primary_frame.index, dtype=float)
        second_block_strength = pd.Series(second_block_strength, index=primary_frame.index, dtype=float)
        dominant_block_dominance = dominant_block_strength.divide(total_strength)
        dominant_block_margin_ratio = dominant_block_strength.subtract(second_block_strength).divide(total_strength)

        dominant_block_agreement = pd.Series(0.0, index=primary_frame.index, dtype=float)
        valid_mask = primary_return.notna() & dominant_block_return.notna()
        if valid_mask.any():
            dominant_block_agreement.loc[valid_mask] = (
                np.sign(primary_return[valid_mask].to_numpy(dtype=float))
                * np.sign(dominant_block_return[valid_mask].to_numpy(dtype=float))
            )

        feature_prefix = self.name
        symbol.add_feature(f"{feature_prefix}_primary_return", primary_return)
        symbol.add_feature(f"{feature_prefix}_europe_block_return", europe_block_return)
        symbol.add_feature(f"{feature_prefix}_commodity_block_return", commodity_block_return)
        symbol.add_feature(f"{feature_prefix}_dollar_block_return", dollar_block_return)
        symbol.add_feature(f"{feature_prefix}_dominant_block_code", dominant_block_code)
        symbol.add_feature(f"{feature_prefix}_dominant_block_return", dominant_block_return)
        symbol.add_feature(f"{feature_prefix}_dominant_block_dominance", dominant_block_dominance.rolling(window=safe_smoothing, min_periods=1).mean())
        symbol.add_feature(f"{feature_prefix}_dominant_block_margin_ratio", dominant_block_margin_ratio.rolling(window=safe_smoothing, min_periods=1).mean())
        symbol.add_feature(f"{feature_prefix}_dominant_block_agreement", dominant_block_agreement)

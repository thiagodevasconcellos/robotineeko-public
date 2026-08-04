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


def _parse_relations(raw_value: str | None, target_size: int) -> list[float]:
    tokens = _parse_group_tokens(raw_value, ["direct"] * target_size)
    if len(tokens) < target_size:
        tokens.extend(["direct"] * (target_size - len(tokens)))
    relation_signs: list[float] = []
    for token in tokens[:target_size]:
        safe = str(token or "DIRECT").strip().lower()
        relation_signs.append(-1.0 if safe == "inverse" else 1.0)
    return relation_signs


class CrossAssetWeightedPeerConfidenceV1(Calculator):
    """
    Continuous weighted peer-confidence surface across a small peer set.
    """

    def __init__(
        self,
        symbol,
        peerSymbols="GBPUSDzAUDUSDzUSDCHF",
        relations="directzdirectzinverse",
        lookback=3,
        zscoreWindow=24,
        smoothingPeriod=5,
    ):
        safe_peer_symbols = _parse_group_tokens(peerSymbols, ["GBPUSD", "AUDUSD", "USDCHF"])
        relation_signs = _parse_relations(relations, len(safe_peer_symbols))
        safe_lookback = max(1, int(lookback or 1))
        safe_zscore_window = max(3, int(zscoreWindow or 3))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        super().__init__(
            "CrossAssetWeightedPeerConfidenceV1",
            "z".join(safe_peer_symbols),
            "z".join("inverse" if sign < 0 else "direct" for sign in relation_signs),
            safe_lookback,
            safe_zscore_window,
            safe_smoothing,
        )

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ["time", "close"]].copy()
        primary_frame["time"] = pd.to_numeric(primary_frame["time"], errors="coerce")
        primary_frame["primary_close"] = pd.to_numeric(primary_frame["close"], errors="coerce")
        primary_return = primary_frame["primary_close"].pct_change(safe_lookback, fill_method=None)

        adjusted_returns: list[pd.Series] = []
        confidence_rows: list[pd.Series] = []
        weighted_strength_rows: list[pd.Series] = []

        for index, peer_symbol in enumerate(safe_peer_symbols):
            peer_context = cross_asset_confirmation_module.ensure_market_data(
                peer_symbol,
                symbol.timeframe,
                len(symbol.candles.index),
                source="cross_asset_weighted_peer_confidence_v1_indicator",
            )
            if not peer_context.get("ready"):
                raise ValueError(
                    f"CrossAssetWeightedPeerConfidenceV1 requires ready peer data for {peer_symbol} {symbol.timeframe}."
                )

            aligned = cross_asset_confirmation_module._build_aligned_peer_frame(
                primary_frame,
                peer_context,
                peer_symbol,
            )
            peer_close = pd.to_numeric(aligned["peer_close"], errors="coerce")
            peer_return = peer_close.pct_change(safe_lookback, fill_method=None)
            adjusted_peer_return = peer_return * relation_signs[index]
            adjusted_returns.append(adjusted_peer_return)

            denominator = primary_return.abs().add(adjusted_peer_return.abs()).replace(0.0, np.nan)
            normalized_gap = (primary_return - adjusted_peer_return).divide(denominator).clip(
                lower=-1.0,
                upper=1.0,
            )

            agreement = pd.Series(0.0, index=primary_frame.index, dtype=float)
            valid_mask = primary_return.notna() & adjusted_peer_return.notna()
            if valid_mask.any():
                agreement.loc[valid_mask] = (
                    np.sign(primary_return[valid_mask].to_numpy(dtype=float))
                    * np.sign(adjusted_peer_return[valid_mask].to_numpy(dtype=float))
                )

            confidence = pd.Series(0.0, index=primary_frame.index, dtype=float)
            coherent_mask = agreement > 0.0
            confidence.loc[coherent_mask] = (
                1.0 - normalized_gap.loc[coherent_mask].abs()
            ).clip(lower=0.0, upper=1.0)
            confidence = confidence.rolling(window=safe_smoothing, min_periods=1).mean()
            confidence_rows.append(confidence)
            weighted_strength_rows.append(confidence * adjusted_peer_return.abs())

        adjusted_frame = pd.concat(adjusted_returns, axis=1)
        confidence_frame = pd.concat(confidence_rows, axis=1)
        strength_frame = pd.concat(weighted_strength_rows, axis=1)

        total_strength = strength_frame.sum(axis=1, min_count=1).replace(0.0, np.nan)
        weighted_peer_return = adjusted_frame.mul(strength_frame).sum(axis=1, min_count=1).divide(total_strength)
        weighted_confidence = confidence_frame.mean(axis=1)
        active_confident_peer_count = (confidence_frame >= 0.35).sum(axis=1).astype(float)

        strength_values = strength_frame.to_numpy(dtype=float)
        top_strength = np.full(len(primary_frame.index), np.nan, dtype=float)
        second_strength = np.full(len(primary_frame.index), np.nan, dtype=float)
        for row_index, row_values in enumerate(strength_values):
            finite = row_values[np.isfinite(row_values)]
            if finite.size == 0:
                continue
            ordered = sorted((float(value) for value in finite), reverse=True)
            top_strength[row_index] = ordered[0]
            second_strength[row_index] = ordered[1] if len(ordered) > 1 else 0.0

        confidence_concentration = pd.Series(top_strength, index=primary_frame.index, dtype=float).divide(total_strength)
        confidence_margin = (
            pd.Series(top_strength, index=primary_frame.index, dtype=float)
            .subtract(pd.Series(second_strength, index=primary_frame.index, dtype=float))
            .divide(total_strength)
        )

        weighted_agreement = pd.Series(0.0, index=primary_frame.index, dtype=float)
        valid_mask = primary_return.notna() & weighted_peer_return.notna()
        if valid_mask.any():
            weighted_agreement.loc[valid_mask] = (
                np.sign(primary_return[valid_mask].to_numpy(dtype=float))
                * np.sign(weighted_peer_return[valid_mask].to_numpy(dtype=float))
            )

        weighted_residual = primary_return - weighted_peer_return
        denominator = primary_return.abs().add(weighted_peer_return.abs()).replace(0.0, np.nan)
        normalized_weighted_residual = weighted_residual.divide(denominator).clip(lower=-1.0, upper=1.0)
        absolute_weighted_residual = normalized_weighted_residual.abs()
        residual_mean = normalized_weighted_residual.rolling(window=safe_zscore_window, min_periods=3).mean()
        residual_std = normalized_weighted_residual.rolling(window=safe_zscore_window, min_periods=3).std(ddof=0).replace(0.0, np.nan)
        weighted_residual_zscore = (normalized_weighted_residual - residual_mean).divide(residual_std)

        feature_prefix = self.name
        symbol.add_feature(f"{feature_prefix}_primary_return", primary_return)
        symbol.add_feature(f"{feature_prefix}_weighted_peer_return", weighted_peer_return)
        symbol.add_feature(f"{feature_prefix}_weighted_confidence", weighted_confidence)
        symbol.add_feature(f"{feature_prefix}_active_confident_peer_count", active_confident_peer_count)
        symbol.add_feature(f"{feature_prefix}_confidence_concentration", confidence_concentration)
        symbol.add_feature(f"{feature_prefix}_confidence_margin", confidence_margin)
        symbol.add_feature(f"{feature_prefix}_weighted_agreement", weighted_agreement)
        symbol.add_feature(f"{feature_prefix}_weighted_residual", normalized_weighted_residual)
        symbol.add_feature(f"{feature_prefix}_absolute_weighted_residual", absolute_weighted_residual)
        symbol.add_feature(f"{feature_prefix}_weighted_residual_zscore", weighted_residual_zscore)

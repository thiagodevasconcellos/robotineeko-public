import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


def _percentile_rank(window_values):
    values = np.asarray(window_values, dtype=float)
    if values.size == 0:
        return np.nan
    current = values[-1]
    if not np.isfinite(current):
        return np.nan
    valid = values[np.isfinite(values)]
    if valid.size == 0:
        return np.nan
    return float(np.mean(valid <= current))


def _parse_csv_tokens(raw_value: str | None, fallback: list[str]) -> list[str]:
    if raw_value is None:
        return list(fallback)
    tokens = [str(item).strip() for item in str(raw_value).split(",")]
    safe_tokens = [token for token in tokens if token]
    return safe_tokens or list(fallback)


def _parse_relations(raw_value: str | None, target_size: int) -> list[float]:
    tokens = _parse_csv_tokens(raw_value, ["direct"] * target_size)
    if len(tokens) < target_size:
        tokens.extend(["direct"] * (target_size - len(tokens)))
    relation_signs: list[float] = []
    for token in tokens[:target_size]:
        safe = str(token or "direct").strip().lower()
        relation_signs.append(-1.0 if safe == "inverse" else 1.0)
    return relation_signs


def _parse_weights(raw_value: str | None, target_size: int) -> list[float]:
    tokens = _parse_csv_tokens(raw_value, ["1"] * target_size)
    if len(tokens) < target_size:
        tokens.extend(["1"] * (target_size - len(tokens)))
    weights: list[float] = []
    for token in tokens[:target_size]:
        try:
            weight = float(token)
        except (TypeError, ValueError):
            weight = 1.0
        weights.append(abs(weight) if np.isfinite(weight) and abs(weight) > 0 else 1.0)
    return weights


class CrossAssetSyntheticPeerBasketV1(Calculator):
    """
    Cross-asset residual surface against a synthetic peer basket.
    """

    def __init__(
        self,
        symbol,
        peerSymbols="GBPUSD,AUDUSD,USDCHF",
        relations="direct,direct,inverse",
        weights="1,1,1",
        lookback=3,
        zscoreWindow=24,
        percentileWindow=24,
        smoothingPeriod=5,
    ):
        safe_peer_symbols = [str(token).strip().upper() for token in _parse_csv_tokens(peerSymbols, ["GBPUSD", "AUDUSD", "USDCHF"])]
        safe_peer_symbols = [token for token in safe_peer_symbols if token]
        if not safe_peer_symbols:
            safe_peer_symbols = ["GBPUSD", "AUDUSD", "USDCHF"]
        relation_signs = _parse_relations(relations, len(safe_peer_symbols))
        safe_weights = _parse_weights(weights, len(safe_peer_symbols))
        safe_lookback = max(1, int(lookback or 1))
        safe_zscore_window = max(3, int(zscoreWindow or 3))
        safe_percentile_window = max(6, int(percentileWindow or 6))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        super().__init__(
            "CrossAssetSyntheticPeerBasketV1",
            ",".join(safe_peer_symbols),
            ",".join("inverse" if sign < 0 else "direct" for sign in relation_signs),
            ",".join(f"{weight:g}" for weight in safe_weights),
            safe_lookback,
            safe_zscore_window,
            safe_percentile_window,
            safe_smoothing,
        )

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ["time", "close"]].copy()
        primary_frame["time"] = pd.to_numeric(primary_frame["time"], errors="coerce")
        primary_frame["primary_close"] = pd.to_numeric(primary_frame["close"], errors="coerce")

        adjusted_returns: list[pd.Series] = []
        weight_mask_frames: list[pd.Series] = []
        for index, peer_symbol in enumerate(safe_peer_symbols):
            peer_context = cross_asset_confirmation_module.ensure_market_data(
                peer_symbol,
                symbol.timeframe,
                len(symbol.candles.index),
                source="cross_asset_synthetic_peer_basket_v1_indicator",
            )
            if not peer_context.get("ready"):
                raise ValueError(
                    f"CrossAssetSyntheticPeerBasketV1 requires ready peer data for {peer_symbol} {symbol.timeframe}."
                )
            peer_aligned = cross_asset_confirmation_module._build_aligned_peer_frame(
                primary_frame,
                peer_context,
                peer_symbol,
            )
            peer_close = pd.to_numeric(peer_aligned["peer_close"], errors="coerce")
            peer_return = peer_close.pct_change(safe_lookback, fill_method=None)
            adjusted_peer_return = peer_return * relation_signs[index]
            adjusted_returns.append(adjusted_peer_return)
            weight_mask_frames.append(adjusted_peer_return.notna().astype(float) * safe_weights[index])

        primary_return = primary_frame["primary_close"].pct_change(safe_lookback, fill_method=None)
        adjusted_return_frame = pd.concat(adjusted_returns, axis=1)
        weight_mask_frame = pd.concat(weight_mask_frames, axis=1)
        weighted_sum = adjusted_return_frame.mul(safe_weights, axis=1).sum(axis=1, min_count=1)
        weight_sum = weight_mask_frame.sum(axis=1, min_count=1).replace(0.0, np.nan)
        adjusted_basket_return = weighted_sum.divide(weight_sum)

        signed_residual_return = primary_return - adjusted_basket_return
        denominator = primary_return.abs().add(adjusted_basket_return.abs()).replace(0.0, np.nan)
        normalized_residual = signed_residual_return.divide(denominator)
        normalized_residual = normalized_residual.clip(lower=-1.0, upper=1.0)
        absolute_normalized_residual = normalized_residual.abs()

        residual_mean = normalized_residual.rolling(window=safe_zscore_window, min_periods=3).mean()
        residual_std = normalized_residual.rolling(window=safe_zscore_window, min_periods=3).std(ddof=0)
        residual_std = residual_std.replace(0.0, np.nan)
        residual_zscore = (normalized_residual - residual_mean).divide(residual_std)

        absolute_residual_percentile = absolute_normalized_residual.rolling(
            window=safe_percentile_window,
            min_periods=6,
        ).apply(_percentile_rank, raw=True)
        signed_residual_percentile = normalized_residual.rolling(
            window=safe_percentile_window,
            min_periods=6,
        ).apply(_percentile_rank, raw=True)

        agreement = pd.Series(0.0, index=primary_frame.index, dtype=float)
        valid_mask = primary_return.notna() & adjusted_basket_return.notna()
        if valid_mask.any():
            primary_direction = np.sign(primary_return[valid_mask].to_numpy(dtype=float))
            basket_direction = np.sign(adjusted_basket_return[valid_mask].to_numpy(dtype=float))
            agreement.loc[valid_mask] = primary_direction * basket_direction

        sync_score = pd.Series(0.0, index=primary_frame.index, dtype=float)
        coherent_mask = agreement > 0.0
        sync_score.loc[coherent_mask] = (
            1.0 - absolute_normalized_residual.loc[coherent_mask]
        ).clip(lower=0.0, upper=1.0)
        sync_score = sync_score.rolling(window=safe_smoothing, min_periods=1).mean()

        feature_prefix = "CrossAssetSyntheticPeerBasketV1"
        symbol.add_feature(f"{feature_prefix}_primary_return", primary_return)
        symbol.add_feature(f"{feature_prefix}_adjusted_basket_return", adjusted_basket_return)
        symbol.add_feature(f"{feature_prefix}_signed_residual_return", signed_residual_return)
        symbol.add_feature(f"{feature_prefix}_normalized_residual", normalized_residual)
        symbol.add_feature(f"{feature_prefix}_absolute_normalized_residual", absolute_normalized_residual)
        symbol.add_feature(f"{feature_prefix}_residual_mean", residual_mean)
        symbol.add_feature(f"{feature_prefix}_residual_std", residual_std)
        symbol.add_feature(f"{feature_prefix}_residual_zscore", residual_zscore)
        symbol.add_feature(f"{feature_prefix}_absolute_residual_percentile", absolute_residual_percentile)
        symbol.add_feature(f"{feature_prefix}_signed_residual_percentile", signed_residual_percentile)
        symbol.add_feature(f"{feature_prefix}_agreement", agreement)
        symbol.add_feature(f"{feature_prefix}_sync_score", sync_score)

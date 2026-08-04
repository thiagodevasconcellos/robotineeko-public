import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


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


class CrossAssetClusterLeaderFollowerV1(Calculator):
    """
    Dynamic leader-follower surface across a small cross-asset peer cluster.
    """

    def __init__(
        self,
        symbol,
        peerSymbols="GBPUSD,AUDUSD,USDCHF",
        relations="direct,direct,inverse",
        lookback=3,
        zscoreWindow=24,
        smoothingPeriod=5,
    ):
        safe_peer_symbols = [
            str(token).strip().upper()
            for token in _parse_csv_tokens(peerSymbols, ["GBPUSD", "AUDUSD", "USDCHF"])
        ]
        safe_peer_symbols = [token for token in safe_peer_symbols if token]
        if not safe_peer_symbols:
            safe_peer_symbols = ["GBPUSD", "AUDUSD", "USDCHF"]
        relation_signs = _parse_relations(relations, len(safe_peer_symbols))
        safe_lookback = max(1, int(lookback or 1))
        safe_zscore_window = max(6, int(zscoreWindow or 6))
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        super().__init__(
            "CrossAssetClusterLeaderFollowerV1",
            ",".join(safe_peer_symbols),
            ",".join("inverse" if sign < 0 else "direct" for sign in relation_signs),
            safe_lookback,
            safe_zscore_window,
            safe_smoothing,
        )

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ["time", "close"]].copy()
        primary_frame["time"] = pd.to_numeric(primary_frame["time"], errors="coerce")
        primary_frame["primary_close"] = pd.to_numeric(primary_frame["close"], errors="coerce")

        adjusted_returns: list[pd.Series] = []
        for index, peer_symbol in enumerate(safe_peer_symbols):
            peer_context = cross_asset_confirmation_module.ensure_market_data(
                peer_symbol,
                symbol.timeframe,
                len(symbol.candles.index),
                source="cross_asset_cluster_leader_follower_v1_indicator",
            )
            if not peer_context.get("ready"):
                raise ValueError(
                    f"CrossAssetClusterLeaderFollowerV1 requires ready peer data for {peer_symbol} {symbol.timeframe}."
                )
            peer_aligned = cross_asset_confirmation_module._build_aligned_peer_frame(
                primary_frame,
                peer_context,
                peer_symbol,
            )
            peer_close = pd.to_numeric(peer_aligned["peer_close"], errors="coerce")
            peer_return = peer_close.pct_change(safe_lookback, fill_method=None)
            adjusted_returns.append(peer_return * relation_signs[index])

        primary_return = primary_frame["primary_close"].pct_change(safe_lookback, fill_method=None)
        adjusted_return_frame = pd.concat(adjusted_returns, axis=1)
        adjusted_values = adjusted_return_frame.to_numpy(dtype=float)

        leader_adjusted_return = np.full(len(primary_frame.index), np.nan, dtype=float)
        leader_strength = np.full(len(primary_frame.index), np.nan, dtype=float)
        second_strength = np.full(len(primary_frame.index), np.nan, dtype=float)
        leader_code = np.zeros(len(primary_frame.index), dtype=float)
        leader_dominance = np.full(len(primary_frame.index), np.nan, dtype=float)

        for row_index, row_values in enumerate(adjusted_values):
            finite_mask = np.isfinite(row_values)
            if not finite_mask.any():
                continue
            finite_values = row_values[finite_mask]
            finite_abs = np.abs(finite_values)
            local_leader_position = int(np.argmax(finite_abs))
            original_positions = np.flatnonzero(finite_mask)
            leader_position = int(original_positions[local_leader_position])
            leader_value = float(row_values[leader_position])
            leader_abs = abs(leader_value)
            ordered_abs = sorted((float(value) for value in finite_abs), reverse=True)
            second_abs = float(ordered_abs[1]) if len(ordered_abs) > 1 else 0.0
            total_abs = float(np.sum(finite_abs))

            leader_adjusted_return[row_index] = leader_value
            leader_strength[row_index] = leader_abs
            second_strength[row_index] = second_abs
            leader_code[row_index] = float(leader_position + 1)
            if total_abs > 0:
                leader_dominance[row_index] = leader_abs / total_abs

        leader_adjusted_return = pd.Series(leader_adjusted_return, index=primary_frame.index, dtype=float)
        leader_strength = pd.Series(leader_strength, index=primary_frame.index, dtype=float)
        second_strength = pd.Series(second_strength, index=primary_frame.index, dtype=float)
        leader_code = pd.Series(leader_code, index=primary_frame.index, dtype=float)
        leader_margin = leader_strength - second_strength
        leader_dominance = pd.Series(leader_dominance, index=primary_frame.index, dtype=float)

        denominator = primary_return.abs().add(leader_adjusted_return.abs()).replace(0.0, np.nan)
        normalized_follow_gap = (leader_adjusted_return - primary_return).divide(denominator)
        normalized_follow_gap = normalized_follow_gap.clip(lower=-1.0, upper=1.0)
        absolute_normalized_follow_gap = normalized_follow_gap.abs()

        gap_mean = normalized_follow_gap.rolling(window=safe_zscore_window, min_periods=6).mean()
        gap_std = normalized_follow_gap.rolling(window=safe_zscore_window, min_periods=6).std(ddof=0)
        gap_std = gap_std.replace(0.0, np.nan)
        leader_follow_gap_zscore = (normalized_follow_gap - gap_mean).divide(gap_std)

        agreement = pd.Series(0.0, index=primary_frame.index, dtype=float)
        valid_mask = primary_return.notna() & leader_adjusted_return.notna()
        if valid_mask.any():
            primary_direction = np.sign(primary_return[valid_mask].to_numpy(dtype=float))
            leader_direction = np.sign(leader_adjusted_return[valid_mask].to_numpy(dtype=float))
            agreement.loc[valid_mask] = primary_direction * leader_direction

        sync_score = pd.Series(0.0, index=primary_frame.index, dtype=float)
        coherent_mask = agreement > 0.0
        if coherent_mask.any():
            local_sync = (
                leader_dominance.loc[coherent_mask].fillna(0.0)
                * (1.0 - absolute_normalized_follow_gap.loc[coherent_mask].fillna(1.0))
            )
            sync_score.loc[coherent_mask] = local_sync.clip(lower=0.0, upper=1.0)
        sync_score = sync_score.rolling(window=safe_smoothing, min_periods=1).mean()

        feature_prefix = "CrossAssetClusterLeaderFollowerV1"
        symbol.add_feature(f"{feature_prefix}_primary_return", primary_return)
        symbol.add_feature(f"{feature_prefix}_leader_adjusted_return", leader_adjusted_return)
        symbol.add_feature(f"{feature_prefix}_leader_strength", leader_strength)
        symbol.add_feature(f"{feature_prefix}_second_strength", second_strength)
        symbol.add_feature(f"{feature_prefix}_leader_margin", leader_margin)
        symbol.add_feature(f"{feature_prefix}_leader_dominance", leader_dominance)
        symbol.add_feature(f"{feature_prefix}_normalized_follow_gap", normalized_follow_gap)
        symbol.add_feature(f"{feature_prefix}_absolute_normalized_follow_gap", absolute_normalized_follow_gap)
        symbol.add_feature(f"{feature_prefix}_leader_follow_gap_zscore", leader_follow_gap_zscore)
        symbol.add_feature(f"{feature_prefix}_agreement", agreement)
        symbol.add_feature(f"{feature_prefix}_sync_score", sync_score)
        symbol.add_feature(f"{feature_prefix}_leader_code", leader_code)

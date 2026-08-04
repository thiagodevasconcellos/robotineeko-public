import numpy as np
import pandas as pd

from ...calculator import Calculator
from . import cross_asset_confirmation as cross_asset_confirmation_module


def _parse_csv_tokens(raw_value: str | None, fallback: list[str]) -> list[str]:
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
    tokens = [str(item).strip() for item in raw_text.split(separator)]
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


class CrossAssetPeerDisagreementGraphV1(Calculator):
    """
    Cross-asset graph surface that treats disagreement topology as signal.
    """

    def __init__(
        self,
        symbol,
        peerSymbols="GBPUSD,AUDUSD,USDCHF",
        relations="direct,direct,inverse",
        lookback=3,
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
        safe_smoothing = max(1, int(smoothingPeriod or 1))
        serialized_peer_symbols = "z".join(safe_peer_symbols)
        serialized_relations = "z".join(
            "inverse" if sign < 0 else "direct"
            for sign in relation_signs
        )
        super().__init__(
            "CrossAssetPeerDisagreementGraphV1",
            serialized_peer_symbols,
            serialized_relations,
            safe_lookback,
            safe_smoothing,
        )

        primary_frame = symbol.candles.copy().reset_index(drop=True)
        primary_frame = primary_frame.loc[:, ["time", "close"]].copy()
        primary_frame["time"] = pd.to_numeric(primary_frame["time"], errors="coerce")
        primary_frame["primary_close"] = pd.to_numeric(primary_frame["close"], errors="coerce")
        primary_return = primary_frame["primary_close"].pct_change(safe_lookback, fill_method=None)

        adjusted_returns: list[pd.Series] = []
        agreement_rows: list[pd.Series] = []
        similarity_rows: list[pd.Series] = []
        pressure_rows: list[pd.Series] = []

        for index, peer_symbol in enumerate(safe_peer_symbols):
            peer_context = cross_asset_confirmation_module.ensure_market_data(
                peer_symbol,
                symbol.timeframe,
                len(symbol.candles.index),
                source="cross_asset_peer_disagreement_graph_v1_indicator",
            )
            if not peer_context.get("ready"):
                raise ValueError(
                    f"CrossAssetPeerDisagreementGraphV1 requires ready peer data for {peer_symbol} {symbol.timeframe}."
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

            denominator = primary_return.abs().add(adjusted_peer_return.abs()).replace(0.0, np.nan)
            normalized_gap = (primary_return - adjusted_peer_return).divide(denominator).clip(
                lower=-1.0,
                upper=1.0,
            )
            similarity_rows.append((1.0 - normalized_gap.abs()).clip(lower=0.0, upper=1.0))

            agreement = pd.Series(np.nan, index=primary_frame.index, dtype=float)
            valid_mask = primary_return.notna() & adjusted_peer_return.notna()
            if valid_mask.any():
                agreement.loc[valid_mask] = (
                    np.sign(primary_return[valid_mask].to_numpy(dtype=float))
                    * np.sign(adjusted_peer_return[valid_mask].to_numpy(dtype=float))
                )
            agreement_rows.append(agreement)
            pressure_rows.append(adjusted_peer_return.abs())

        adjusted_return_frame = pd.concat(adjusted_returns, axis=1)
        agreement_frame = pd.concat(agreement_rows, axis=1)
        similarity_frame = pd.concat(similarity_rows, axis=1)
        pressure_frame = pd.concat(pressure_rows, axis=1)

        valid_peer_count = agreement_frame.notna().sum(axis=1).astype(float)
        aligned_mask = agreement_frame > 0.0
        disagreement_mask = agreement_frame < 0.0
        neutral_mask = agreement_frame == 0.0

        aligned_count = aligned_mask.sum(axis=1).astype(float)
        disagreement_count = disagreement_mask.sum(axis=1).astype(float)
        neutral_count = neutral_mask.sum(axis=1).astype(float)

        safe_valid_count = valid_peer_count.replace(0.0, np.nan)
        agreement_balance = aligned_count.subtract(disagreement_count).divide(safe_valid_count)
        mixed_graph_flag = ((aligned_count > 0.0) & (disagreement_count > 0.0)).astype(float)

        total_pressure = pressure_frame.sum(axis=1, min_count=1).replace(0.0, np.nan)
        alignment_pressure = pressure_frame.where(aligned_mask, 0.0).sum(axis=1, min_count=1).divide(total_pressure)
        disagreement_pressure = pressure_frame.where(disagreement_mask, 0.0).sum(axis=1, min_count=1).divide(total_pressure)
        pressure_gap = alignment_pressure.subtract(disagreement_pressure)

        aligned_similarity_mean = similarity_frame.where(aligned_mask).mean(axis=1)
        disagree_similarity_mean = similarity_frame.where(disagreement_mask).mean(axis=1)
        similarity_gap = aligned_similarity_mean.subtract(disagree_similarity_mean)
        aligned_similarity_mean = aligned_similarity_mean.rolling(window=safe_smoothing, min_periods=1).mean()
        disagree_similarity_mean = disagree_similarity_mean.rolling(window=safe_smoothing, min_periods=1).mean()
        similarity_gap = similarity_gap.rolling(window=safe_smoothing, min_periods=1).mean()

        adjusted_values = adjusted_return_frame.to_numpy(dtype=float)
        agreement_values = agreement_frame.to_numpy(dtype=float)
        leader_code = np.zeros(len(primary_frame.index), dtype=float)
        leader_agreement = np.full(len(primary_frame.index), np.nan, dtype=float)

        for row_index, row_values in enumerate(adjusted_values):
            finite_mask = np.isfinite(row_values)
            if not finite_mask.any():
                continue
            finite_abs = np.abs(row_values[finite_mask])
            leader_local_position = int(np.argmax(finite_abs))
            original_positions = np.flatnonzero(finite_mask)
            leader_position = int(original_positions[leader_local_position])
            leader_code[row_index] = float(leader_position + 1)
            leader_agreement[row_index] = float(agreement_values[row_index, leader_position])

        leader_agreement = pd.Series(leader_agreement, index=primary_frame.index, dtype=float)
        leader_disagrees = (leader_agreement < 0.0).astype(float)
        leader_agrees = (leader_agreement > 0.0).astype(float)

        feature_prefix = self.name
        symbol.add_feature(f"{feature_prefix}_primary_return", primary_return)
        symbol.add_feature(f"{feature_prefix}_valid_peer_count", valid_peer_count)
        symbol.add_feature(f"{feature_prefix}_aligned_count", aligned_count)
        symbol.add_feature(f"{feature_prefix}_disagreement_count", disagreement_count)
        symbol.add_feature(f"{feature_prefix}_neutral_count", neutral_count)
        symbol.add_feature(f"{feature_prefix}_agreement_balance", agreement_balance)
        symbol.add_feature(f"{feature_prefix}_mixed_graph_flag", mixed_graph_flag)
        symbol.add_feature(f"{feature_prefix}_alignment_pressure", alignment_pressure)
        symbol.add_feature(f"{feature_prefix}_disagreement_pressure", disagreement_pressure)
        symbol.add_feature(f"{feature_prefix}_pressure_gap", pressure_gap)
        symbol.add_feature(f"{feature_prefix}_aligned_similarity_mean", aligned_similarity_mean)
        symbol.add_feature(f"{feature_prefix}_disagree_similarity_mean", disagree_similarity_mean)
        symbol.add_feature(f"{feature_prefix}_similarity_gap", similarity_gap)
        symbol.add_feature(f"{feature_prefix}_leader_code", pd.Series(leader_code, index=primary_frame.index, dtype=float))
        symbol.add_feature(f"{feature_prefix}_leader_agreement", leader_agreement)
        symbol.add_feature(f"{feature_prefix}_leader_disagrees", leader_disagrees)
        symbol.add_feature(f"{feature_prefix}_leader_agrees", leader_agrees)

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyOpenCandidate:
    strategy_id: str
    strategy_label: str
    priority: int
    side: str
    metadata: dict[str, Any] = field(default_factory=dict)


def sort_strategy_open_candidates(candidates: list[StrategyOpenCandidate] | None):
    return sorted(
        list(candidates or []),
        key=lambda item: (int(item.priority), str(item.strategy_id or ''), str(item.side or '')),
    )


def resolve_open_conflicts(
    candidates: list[StrategyOpenCandidate] | None,
    *,
    current_portfolio_position: int = 0,
    allow_hedge: bool = False,
):
    sorted_candidates = sort_strategy_open_candidates(candidates)
    accepted = []
    skipped = []

    effective_direction = 0
    if int(current_portfolio_position or 0) > 0:
        effective_direction = 1
    elif int(current_portfolio_position or 0) < 0:
        effective_direction = -1

    for candidate in sorted_candidates:
        side = str(candidate.side or '').strip().lower()
        candidate_direction = 1 if side == 'long' else -1 if side == 'short' else 0

        if candidate_direction == 0:
            skipped.append({
                'candidate': candidate,
                'reason': 'invalid_side',
            })
            continue

        if allow_hedge:
            accepted.append(candidate)
            continue

        if effective_direction == 0:
            accepted.append(candidate)
            effective_direction = candidate_direction
            continue

        if candidate_direction == effective_direction:
            accepted.append(candidate)
            continue

        skipped.append({
            'candidate': candidate,
            'reason': 'conflict_with_portfolio_direction',
            'blocked_by_direction': effective_direction,
        })

    return {
        'accepted': accepted,
        'skipped': skipped,
        'effective_direction': effective_direction,
    }

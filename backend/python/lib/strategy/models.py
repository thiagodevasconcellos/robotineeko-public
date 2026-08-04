from dataclasses import dataclass, field
from typing import Any


@dataclass
class TradeEvent:
    kind: str
    side: str
    time: int | None
    bar_index: int
    price: float | None
    position_after: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyEngineResult:
    history: Any
    events: list[TradeEvent] = field(default_factory=list)
    final_position: int = 0
    pending_action: dict[str, Any] | None = None


@dataclass
class PortfolioTradeEvent:
    kind: str
    strategy_id: str
    strategy_label: str
    side: str
    time: int | None
    bar_index: int
    price: float | None
    portfolio_position_after: int
    strategy_position_after: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyPortfolioState:
    strategy_id: str
    strategy_label: str
    priority: int
    enabled: bool = True
    position: int = 0
    pending_action: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MultiStrategyEngineResult:
    history: Any
    events: list[PortfolioTradeEvent] = field(default_factory=list)
    strategy_states: list[StrategyPortfolioState] = field(default_factory=list)
    final_portfolio_position: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BridgeState:
    request: dict = field(default_factory=lambda: {
        'symbol': 'EURUSD',
        'timeframe': 'M1',
        'bars': 1000,
    })
    candles: list[dict] = field(default_factory=list)
    history_ready: bool = False
    history_loading: bool = False
    history_error: str | None = None
    history_request_started_at: float | None = None
    history_timeout_seconds: float = 15.0
    revision: int = 0
    last_affected_index: int | None = None
    last_update_replaced_times: list[int] = field(default_factory=list)
    history_meta: dict = field(default_factory=lambda: {
        'symbol': None,
        'timeframe': None,
        'requested_bars': None,
        'loaded_candles': 0,
        'first_time': None,
        'last_time': None,
        'last_reset_reason': None,
    })
    history_chunk_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_request_id: str | None = None
    ea_session_id: str | None = None
    ea_last_status: str | None = None
    ea_last_message: str | None = None
    ea_last_error: str | None = None
    ea_last_error_at: float | None = None
    ea_last_event: str | None = None
    ea_last_event_at: float | None = None
    ea_last_heartbeat_at: float | None = None
    ea_last_request_id: str | None = None
    ea_account_position_mode: str | None = None
    ea_account_hedge_allowed: bool | None = None
    ea_market_watch_symbols: list[str] = field(default_factory=list)
    ea_market_watch_exhaustive: bool = False
    ea_timeout_seconds: float = 8.0
    ea_recent_events: list[dict] = field(default_factory=list)
    trade_command_poll_count: int = 0
    trade_command_last_polled_at: float | None = None
    trade_command_last_command_id: str | None = None
    trade_command_last_command_at: float | None = None
    trade_command_last_ack_id: str | None = None
    trade_command_last_ack_at: float | None = None
    trade_command_last_result_id: str | None = None
    trade_command_last_result_status: str | None = None
    trade_command_last_result_at: float | None = None


@dataclass
class MarketDataRuntimeState:
    requests_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    request_order: list[str] = field(default_factory=list)
    pending_queue: list[str] = field(default_factory=list)
    cache_by_key: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_request_id: str | None = None
    last_cache_key: str | None = None
    revision: int = 0
    last_error: str | None = None


@dataclass
class ChartState:
    request: dict = field(default_factory=lambda: {
        'symbol': 'EURUSD',
        'timeframe': 'M1',
        'bars': 1000,
        'indicators': [],
    })
    snapshot_signature: dict | None = None
    snapshot_symbol: Any = None
    snapshot_candles: list[dict] = field(default_factory=list)
    snapshot_indicators: list[dict] = field(default_factory=list)
    snapshot_applied_indicators: list[dict] = field(default_factory=list)
    snapshot_available_columns: list[str] = field(default_factory=list)
    snapshot_available_column_details: list[dict] = field(default_factory=list)
    snapshot_built_at: float | None = None
    snapshot_error: str | None = None
    snapshot_dirty_reason: str | None = None
    snapshot_affected_from_index: int | None = None
    snapshot_refresh_mode: str | None = None
    snapshot_partial_eligible: bool = False
    snapshot_partial_blockers: list[dict] = field(default_factory=list)
    snapshot_partial_opportunity: dict | None = None
    snapshot_runtime_contracts: list[dict] = field(default_factory=list)
    snapshot_runtime_window: dict | None = None
    snapshot_performance: dict | None = None
    snapshot_refresh_counts: dict = field(default_factory=lambda: {
        'full': 0,
        'partial': 0,
    })
    snapshot_recent_reasons: list[dict] = field(default_factory=list)


@dataclass
class MarketRuntimeState:
    revision: int = 0
    tick_revision: int = 0
    candle_revision: int = 0
    last_event: str | None = None
    affected_from_index: int | None = None
    latest_candle_time: int | None = None
    previous_candle_time: int | None = None
    last_update_at: float | None = None
    last_replaced_times: list[int] = field(default_factory=list)
    changed_features: list[str] = field(default_factory=list)
    changed_feature_details: list[dict] = field(default_factory=list)


@dataclass
class StrategyRuntimeState:
    request: dict | None = None
    workspace_user_id: str | None = None
    workspace_id: str | None = None
    backtest_active: bool = False
    symbol: Any = None
    strategy: Any = None
    backtester: Any = None
    results: Any = None
    stats: dict | None = None
    applied_indicators: list[dict] = field(default_factory=list)
    available_columns: list[str] = field(default_factory=list)
    available_column_details: list[dict] = field(default_factory=list)
    required_features: list[str] = field(default_factory=list)
    required_feature_details: list[dict] = field(default_factory=list)
    strategy_view_meta: dict | None = None
    trade_markers: list[dict] = field(default_factory=list)
    last_applied_at: float | None = None
    last_results_generated_at: float | None = None
    last_invalidated_reason: str | None = None
    last_invalidated_overlap: list[str] = field(default_factory=list)
    is_stale: bool = False
    stale_reason: str | None = None
    stale_overlap: list[str] = field(default_factory=list)
    last_refresh_mode: str | None = None
    last_refresh_from_index: int | None = None
    refresh_counts: dict = field(default_factory=lambda: {
        'full': 0,
        'partial': 0,
    })
    recent_reasons: list[dict] = field(default_factory=list)
    performance: dict | None = None


@dataclass
class WorkspaceRuntimeState:
    active_user_id: str = 'local-user'
    active_workspace_id: str = 'default'
    state: dict = field(default_factory=dict)
    revision: int = 0
    last_saved_at: float | None = None
    last_broadcast_at: float | None = None
    last_error: str | None = None


@dataclass
class RuntimeServiceState:
    last_trigger: str | None = None
    last_run_at: float | None = None
    last_chart_warm_at: float | None = None
    last_strategy_refresh_at: float | None = None
    last_error: str | None = None


@dataclass
class TradeRuntimeState:
    mode: str = 'parallel_sleeves'
    execution_mode: str = 'paper'
    broker_profile_id: str = ''
    broker_profile_label: str = ''
    same_symbol_execution_policy: str = 'independent'
    signal_validity_seconds: int = 10
    portfolio_structure_version: int = 1
    status: str = 'idle'
    armed: bool = False
    live_dispatch_armed: bool = False
    live: bool = False
    latency_budget_ms: int = 150
    portfolios: list[dict[str, Any]] = field(default_factory=list)
    sleeves: list[dict[str, Any]] = field(default_factory=list)
    sleeve_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_symbols: list[str] = field(default_factory=list)
    order_intents: list[dict[str, Any]] = field(default_factory=list)
    order_commands: list[dict[str, Any]] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    latency_events: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=lambda: {
        'event_count': 0,
        'decision_count': 0,
        'dispatch_count': 0,
        'ack_count': 0,
        'fill_count': 0,
        'command_count': 0,
        'command_ack_count': 0,
        'command_fill_count': 0,
        'command_reject_count': 0,
        'last_latency_ms': None,
        'max_latency_ms': None,
    })
    last_configured_at: float | None = None
    last_armed_at: float | None = None
    last_live_dispatch_armed_at: float | None = None
    last_live_dispatch_disarmed_at: float | None = None
    last_disarmed_at: float | None = None
    last_event_at: float | None = None
    market_feed_status: str = 'idle'
    market_feed_issue: str | None = None
    last_market_sanitize_at: float | None = None
    bridge_online: bool = False
    bridge_last_status: str | None = None
    bridge_last_message: str | None = None
    bridge_last_request_id: str | None = None
    bridge_last_heartbeat_at: float | None = None
    bridge_timeout_seconds: float = 8.0
    broker_account_position_mode: str | None = None
    broker_account_hedge_allowed: bool | None = None
    market_history_ready: bool = False
    market_last_update_at: float | None = None
    market_latest_candle_time: int | None = None
    market_snapshot_symbol: str | None = None
    market_snapshot_timeframe: str | None = None
    market_snapshot_bars: int = 0
    market_snapshot_candles: list[dict[str, Any]] = field(default_factory=list)
    last_market_event_stage: str | None = None
    last_market_event_new_candle: bool = False
    last_market_event_candle_time: int | None = None
    broker_positions: list[dict[str, Any]] = field(default_factory=list)
    last_broker_positions_at: float | None = None
    broker_symbol_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    trade_cycle_sequence: int = 0
    last_error: str | None = None


@dataclass
class NeuralRuntimeState:
    active_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    job_threads: dict[str, Any] = field(default_factory=dict)
    last_run_at: float | None = None
    last_error: str | None = None
    recent_events: list[dict] = field(default_factory=list)


@dataclass
class ResearchRuntimeState:
    active_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_batches: dict[str, dict[str, Any]] = field(default_factory=dict)
    job_threads: dict[str, Any] = field(default_factory=dict)
    runtime_heartbeat_threads: dict[str, Any] = field(default_factory=dict)
    feature_view_cache: dict[tuple[Any, ...], dict[str, Any]] = field(default_factory=dict)
    feature_view_cache_order: list[tuple[Any, ...]] = field(default_factory=list)
    feature_view_cache_stats: dict[str, Any] = field(default_factory=lambda: {
        'hits': 0,
        'misses': 0,
        'stores': 0,
        'evictions': 0,
        'last_event': None,
        'last_key': None,
        'last_at': None,
    })
    last_run_at: float | None = None
    last_error: str | None = None
    recent_events: list[dict] = field(default_factory=list)


@dataclass
class BacktestJobRuntimeState:
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    job_threads: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0
    last_job_id: str | None = None
    last_run_at: float | None = None
    last_error: str | None = None


class AppState:
    def __init__(self):
        self.bridge = BridgeState()
        self.market_data = MarketDataRuntimeState()
        self.market = MarketRuntimeState()
        self.chart = ChartState()
        self.strategy = StrategyRuntimeState()
        self.workspace = WorkspaceRuntimeState()
        self.runtime_service = RuntimeServiceState()
        self.trade = TradeRuntimeState()
        self.neural = NeuralRuntimeState()
        self.research = ResearchRuntimeState()
        self.backtest_jobs = BacktestJobRuntimeState()


state = AppState()

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, model_validator
import numpy as np
import math
import re
import time
import threading
import uuid

try:
    from .app_state import state
    from .indicator_registry import (
        describe_indicator_columns,
        describe_indicator_feature_name,
        get_indicator_class,
        normalize_indicator_feature_name,
    )
    from .lib.strategy import Strategy, Backtester, MultiStrategyBacktester, PortfolioStackBacktester
    from .lib.strategy.backtest_cost_profiles import (
        build_backtest_cost_policy,
        merge_backtest_cost_profile_values,
        normalize_backtest_cost_profile,
    )
    from .lib.strategy.expression_identifiers import build_expression_safe_identifier
    from .lib.symbol import Symbol
    from .runtime.chart_runtime import ensure_chart_snapshot
    from .services.auth_service import require_request_auth, require_websocket_auth_or_close
    from .services.chart_service import wait_for_history
    from .services.engine_view_service import build_results_view, build_strategy_feature_view
    from .services.market_data_service import wait_for_market_data
    from .services.realtime_sync import realtime_sync
    from .services.workspace_service import append_and_broadcast_workspace_system_log_entries, persist_strategy_runtime_snapshot
    from .services.workspace_store import (
        BACKTEST_JOB_TERMINAL_RETENTION_SECONDS,
        create_workspace_backtest_job,
        create_workspace_system_log_session,
        get_workspace_backtest_job,
        list_workspace_backtest_jobs,
        purge_expired_workspace_backtest_jobs,
        update_workspace_backtest_job,
    )
except ImportError:
    from app_state import state
    from indicator_registry import (
        describe_indicator_columns,
        describe_indicator_feature_name,
        get_indicator_class,
        normalize_indicator_feature_name,
    )
    from lib.strategy import Strategy, Backtester, MultiStrategyBacktester, PortfolioStackBacktester
    from lib.strategy.backtest_cost_profiles import (
        build_backtest_cost_policy,
        merge_backtest_cost_profile_values,
        normalize_backtest_cost_profile,
    )
    from lib.strategy.expression_identifiers import build_expression_safe_identifier
    from lib.symbol import Symbol
    from runtime.chart_runtime import ensure_chart_snapshot
    from services.auth_service import require_request_auth, require_websocket_auth_or_close
    from services.chart_service import wait_for_history
    from services.engine_view_service import build_results_view, build_strategy_feature_view
    from services.market_data_service import wait_for_market_data
    from services.realtime_sync import realtime_sync
    from services.workspace_service import append_and_broadcast_workspace_system_log_entries, persist_strategy_runtime_snapshot
    from services.workspace_store import (
        BACKTEST_JOB_TERMINAL_RETENTION_SECONDS,
        create_workspace_backtest_job,
        create_workspace_system_log_session,
        get_workspace_backtest_job,
        list_workspace_backtest_jobs,
        purge_expired_workspace_backtest_jobs,
        update_workspace_backtest_job,
    )

try:
    from .config import build_feature_flags
except ImportError:
    from config import build_feature_flags

router = APIRouter()
FEATURE_FLAGS = build_feature_flags()
STRATEGY_CHANNEL_KEY = 'strategy:default'
MAX_RESEARCH_FEATURE_VIEW_CACHE_ENTRIES = 64
BACKTEST_JOB_RESPONSE_MAX_RESULT_ROWS = 512
BACKTEST_JOB_RESPONSE_MAX_SERIES_POINTS = 4000
BACKTEST_JOB_SERIES_KEYS = (
    'account_balance_series',
    'drawdown_amount_series',
    'drawdown_pct_series',
    'equity_curve',
    'drawdown_curve',
    'drawdown_pct_curve',
)
BACKTEST_JOB_EVENT_FLAG_KEYS = (
    'long_entry_flag',
    'short_entry_flag',
    'long_exit_flag',
    'short_exit_flag',
)
BACKTEST_JOB_EVENT_VALUE_KEYS = (
    'trade_net_pnl',
    'trade_cost',
)


class StrategySection(BaseModel):
    openPrice: str = 'close[0]'
    closePrice: str = 'close[0]'
    openIf: str = 'False'
    closeIf: str = 'False'
    gainPrice: str = ''
    lossPrice: str = ''
    trailingPrice: str = ''


class StrategyOther(BaseModel):
    allowInversion: bool = False
    priority: str = 'Short'


class StrategyPayload(BaseModel):
    long: StrategySection = Field(default_factory=StrategySection)
    short: StrategySection = Field(default_factory=StrategySection)
    other: StrategyOther = Field(default_factory=StrategyOther)


class StrategySetEntryPayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: str = ''
    label: str = ''
    priority: int = 0
    enabled: bool = True
    symbol: str = ''
    timeframe: str = ''
    allocationMode: str = 'fixed_volume'
    allocationValue: float | None = None
    volumeMode: str = 'fixed_volume'
    fixedVolume: float | None = None
    baseVolume: float | None = None
    maxVolumeCap: float | None = None
    referenceCapital: float | None = None
    portfolioId: str = ''
    portfolioLabel: str = ''
    pipelineId: str = ''
    pipelineLabel: str = ''
    legacyVolumeFallbackApplied: bool = False
    strategy: StrategyPayload = Field(default_factory=StrategyPayload)


class PortfolioPipelinePayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: str = ''
    label: str = ''
    enabled: bool = True
    portfolioMode: str = 'shared_pipe'
    strategyEntries: list[StrategySetEntryPayload] = Field(default_factory=list)


class PortfolioPayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: str = ''
    label: str = ''
    enabled: bool = True
    capitalMode: str = 'equity_percent'
    capitalValue: float | None = None
    rebalanceMode: str = 'static'
    pipelines: list[PortfolioPipelinePayload] = Field(default_factory=list)


class BacktestPayload(BaseModel):
    initialBalance: float = 10000.0
    assetType: str = 'forex'
    initialVolume: float = 1.0
    pipSize: float = 0.0001
    pipValuePerLot: float = 10.0
    costProfile: str = 'broker_active'
    spreadInPips: float = 1.0
    slippageInPips: float = 0.0
    entrySlippageInPips: float | None = None
    closeSlippageInPips: float | None = None
    takeProfitSlippageInPips: float | None = None
    stopLossSlippageInPips: float | None = None
    trailingStopSlippageInPips: float | None = None
    minimumStopDistanceInPips: float = 0.0
    volatilitySlippageMultiplier: float = 0.0
    executionMode: str = 'next_bar_open'
    portfolioMode: str = 'shared_pipe'
    historyScopeMode: str = 'loaded_chart'
    historyScopeBars: int | None = None
    brokerProfileId: str = ''
    brokerProfileLabel: str = ''
    brokerCode: str = ''
    brokerLabel: str = ''
    brokerMarketDomain: str = ''
    brokerCostProfile: str = ''
    brokerDefaultAssetType: str = ''

    @model_validator(mode='before')
    @classmethod
    def apply_cost_profile_defaults(cls, value):
        if isinstance(value, dict):
            return merge_backtest_cost_profile_values(value)
        return value


class ApplyStrategyRequest(BaseModel):
    strategy: StrategyPayload
    strategies: list[StrategySetEntryPayload] = Field(default_factory=list)
    portfolioStructureVersion: int | None = None
    capitalModel: dict | None = None
    portfolios: list[PortfolioPayload] = Field(default_factory=list)
    backtest: BacktestPayload = Field(default_factory=BacktestPayload)


class ApplyStrategyInContextRequest(ApplyStrategyRequest):
    symbol: str = 'EURUSD'
    timeframe: str = 'M1'
    bars: int = 1000
    indicators: list[dict] = Field(default_factory=list)


class StrategyDebugChartContext(BaseModel):
    symbol: str = 'EURUSD'
    timeframe: str = 'M1'
    bars: int = 1000
    indicators: list[dict] = Field(default_factory=list)


class StrategyDebugRequest(BaseModel):
    strategy: StrategyPayload
    backtest: BacktestPayload = Field(default_factory=BacktestPayload)
    chart: StrategyDebugChartContext
    draft_label: str | None = None


class ConfigureStrategyRequest(BaseModel):
    strategy: StrategyPayload


class PresetCompareEntry(BaseModel):
    id: str
    label: str
    strategy: StrategyPayload
    strategies: list[StrategySetEntryPayload] = Field(default_factory=list)


class PresetCompareRequest(BaseModel):
    baseline: PresetCompareEntry | None = None
    presets: list[PresetCompareEntry] = Field(default_factory=list)
    backtest: BacktestPayload = Field(default_factory=BacktestPayload)
    studyWindows: list[int] = Field(default_factory=list)
    studyTimeframes: list[str] = Field(default_factory=list)
    studySymbols: list[str] = Field(default_factory=list)
    walkforwardWindowBars: int | None = None
    walkforwardStepBars: int | None = None
    walkforwardTrainBars: int | None = None
    walkforwardTestBars: int | None = None
    chartContext: dict | None = None


class BacktestToggleRequest(BaseModel):
    enabled: bool = False
    strategy: StrategyPayload | None = None
    strategies: list[StrategySetEntryPayload] = Field(default_factory=list)
    portfolioStructureVersion: int | None = None
    capitalModel: dict | None = None
    portfolios: list[PortfolioPayload] = Field(default_factory=list)
    backtest: BacktestPayload = Field(default_factory=BacktestPayload)


class ResearchJobCancelledError(Exception):
    pass


DEFAULT_MARKET_REGIME_PARAMS = [9, 21, 14, 14, 20, 2, 20, 14, 10, 3, 'hlc3', 5, 3]
BACKTEST_JOB_TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}
BACKTEST_JOB_INTERRUPTED_ERROR = (
    'Backtest job tracking was interrupted before completion. '
    'The backend likely restarted or the worker disappeared.'
)
SUPPORTED_PORTFOLIO_VOLUME_MODES = {
    'fixed_volume',
    'max_affordable',
    'base_volume_compounding',
}


def invalidate_strategy_runtime(reason: str = 'manual_reset', preserve_runtime: bool = False):
    strategy_state = state.strategy
    preserve_runtime = preserve_runtime or strategy_state.backtest_active
    overlap = []

    if reason.startswith('history_updated:'):
        suffix = reason.split(':', 1)[1]
        overlap = [item for item in suffix.split(',') if item]

    strategy_state.last_invalidated_reason = reason
    strategy_state.last_invalidated_overlap = overlap
    strategy_state.is_stale = preserve_runtime
    strategy_state.stale_reason = reason if preserve_runtime else None
    strategy_state.stale_overlap = list(overlap) if preserve_runtime else []
    strategy_state.recent_reasons = [
        {
            'kind': 'invalidate',
            'reason': str(reason or 'manual_reset'),
            'overlap': list(overlap),
            'at': time.time(),
            'preserve_runtime': bool(preserve_runtime),
        },
        *list(strategy_state.recent_reasons or []),
    ][:12]

    if preserve_runtime:
        return

    strategy_state.request = None
    strategy_state.backtest_active = False
    strategy_state.symbol = None
    strategy_state.strategy = None
    strategy_state.backtester = None
    strategy_state.results = None
    strategy_state.stats = None
    strategy_state.applied_indicators = []
    strategy_state.available_columns = []
    strategy_state.available_column_details = []
    strategy_state.required_features = []
    strategy_state.required_feature_details = []
    strategy_state.trade_markers = []
    strategy_state.last_applied_at = None
    strategy_state.last_results_generated_at = None
    strategy_state.last_refresh_mode = None
    strategy_state.last_refresh_from_index = None


def normalize_expression(value: str):
    text = str(value).strip()

    if text.lower() == 'false':
        return 'False'

    if text.lower() == 'true':
        return 'True'

    return normalize_expression_identifiers(text)


def normalize_expression_identifiers(expression: str):
    if not expression:
        return ''

    def replace_identifier(match):
        identifier = match.group(0)
        normalized = normalize_indicator_feature_name(identifier)
        return normalized or identifier

    return re.sub(r'\b[A-Za-z_][A-Za-z0-9_]*\b', replace_identifier, expression)


def build_strategy_params(payload: StrategyPayload):
    return {
        'open_long_condition': normalize_expression(payload.long.openIf),
        'close_long_condition': normalize_expression(payload.long.closeIf),
        'open_short_condition': normalize_expression(payload.short.openIf),
        'close_short_condition': normalize_expression(payload.short.closeIf),

        'open_trade_price_long': normalize_expression(payload.long.openPrice),
        'open_trade_price_short': normalize_expression(payload.short.openPrice),
        'close_trade_price_long': normalize_expression(payload.long.closePrice),
        'close_trade_price_short': normalize_expression(payload.short.closePrice),

        'stop_gain_long_price': normalize_expression(payload.long.gainPrice),
        'stop_loss_long_price': normalize_expression(payload.long.lossPrice),
        'stop_gain_short_price': normalize_expression(payload.short.gainPrice),
        'stop_loss_short_price': normalize_expression(payload.short.lossPrice),
        'trailing_stop_long_price': normalize_expression(payload.long.trailingPrice),
        'trailing_stop_short_price': normalize_expression(payload.short.trailingPrice),

        'allow_invertion': payload.other.allowInversion,
        'prioritize': payload.other.priority.lower(),
    }


def normalize_strategy_set_entries(
    strategies: list[StrategySetEntryPayload] | None,
    fallback_strategy: StrategyPayload | None = None,
):
    entries = []

    for index, entry in enumerate(strategies or []):
        if isinstance(entry, StrategySetEntryPayload):
            normalized_entry = entry.model_copy(deep=True)
        else:
            normalized_entry = StrategySetEntryPayload.model_validate(entry or {})

        normalized_entry.priority = int(normalized_entry.priority if normalized_entry.priority is not None else index)
        if not str(normalized_entry.id or '').strip():
            normalized_entry.id = f'strategy-{index + 1}'
        if not str(normalized_entry.label or '').strip():
            normalized_entry.label = f'Strategy {index + 1}'
        entries.append(normalized_entry)

    if not entries:
        primary_strategy = fallback_strategy if isinstance(fallback_strategy, StrategyPayload) else StrategyPayload()
        entries.append(StrategySetEntryPayload(
            id='primary',
            label='Primary strategy',
            priority=0,
            enabled=True,
            allocationMode='fixed_volume',
            allocationValue=None,
            strategy=primary_strategy.model_copy(deep=True),
        ))

    entries.sort(key=lambda item: (int(item.priority), str(item.id or '')))
    return entries


def _normalize_portfolio_structure_version(value):
    try:
        parsed = int(value)
    except Exception:
        return 1
    return 2 if parsed >= 2 else 1


def _normalize_compiled_volume_mode(value):
    normalized = str(value or '').strip().lower() or 'fixed_volume'
    if normalized not in SUPPORTED_PORTFOLIO_VOLUME_MODES:
        return 'fixed_volume'
    return normalized


def _coerce_positive_float(value):
    try:
        parsed = float(value)
    except Exception:
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _normalize_portfolio_mode_value(value):
    normalized = str(value or '').strip().lower() or 'shared_pipe'
    if normalized not in {'shared_pipe', 'parallel_sleeves'}:
        return 'shared_pipe'
    return normalized


def _derive_entry_legacy_volume(entry: StrategySetEntryPayload, fallback_volume: float):
    requested_mode = _normalize_compiled_volume_mode(getattr(entry, 'volumeMode', 'fixed_volume'))
    fixed_volume = _coerce_positive_float(getattr(entry, 'fixedVolume', None))
    base_volume = _coerce_positive_float(getattr(entry, 'baseVolume', None))
    allocation_value = _coerce_positive_float(getattr(entry, 'allocationValue', None))
    if requested_mode == 'fixed_volume':
        return fixed_volume or allocation_value or fallback_volume, False
    return fixed_volume or base_volume or allocation_value or fallback_volume, True


def _build_implicit_legacy_portfolios(entries: list[StrategySetEntryPayload], *, portfolio_mode: str):
    return [{
        'id': 'legacy-default',
        'label': 'Legacy default portfolio',
        'enabled': True,
        'capitalMode': 'legacy_shared',
        'capitalValue': None,
        'rebalanceMode': 'static',
        'pipelines': [{
            'id': 'legacy-pipeline',
            'label': 'Legacy pipeline',
            'enabled': True,
            'portfolioMode': portfolio_mode,
            'strategyEntries': [entry.model_dump() for entry in entries],
        }],
    }]


def resolve_strategy_request_entries(payload: ApplyStrategyRequest):
    legacy_entries = normalize_strategy_set_entries(payload.strategies, payload.strategy)
    fallback_initial_volume = max(float(payload.backtest.initialVolume or 1.0), 0.01)
    fallback_portfolio_mode = _normalize_portfolio_mode_value(payload.backtest.portfolioMode)

    explicit_portfolios = list(payload.portfolios or [])
    if not (FEATURE_FLAGS.get('backtest_portfolios_v2') and explicit_portfolios):
        return {
            'entries': legacy_entries,
            'portfolio_structure_version': _normalize_portfolio_structure_version(payload.portfolioStructureVersion),
            'capital_model': dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None,
            'portfolios': _build_implicit_legacy_portfolios(legacy_entries, portfolio_mode=fallback_portfolio_mode),
        }

    compiled_entries = []
    normalized_portfolios = []
    next_priority = 0

    for portfolio_index, portfolio in enumerate(explicit_portfolios):
        normalized_portfolio = portfolio.model_copy(deep=True) if isinstance(portfolio, PortfolioPayload) else PortfolioPayload.model_validate(portfolio or {})
        normalized_portfolio.id = str(normalized_portfolio.id or f'portfolio-{portfolio_index + 1}').strip() or f'portfolio-{portfolio_index + 1}'
        normalized_portfolio.label = str(normalized_portfolio.label or f'Portfolio {portfolio_index + 1}').strip() or f'Portfolio {portfolio_index + 1}'
        next_pipelines = []

        for pipeline_index, pipeline in enumerate(normalized_portfolio.pipelines or []):
            normalized_pipeline = pipeline.model_copy(deep=True) if isinstance(pipeline, PortfolioPipelinePayload) else PortfolioPipelinePayload.model_validate(pipeline or {})
            normalized_pipeline.id = str(normalized_pipeline.id or f'{normalized_portfolio.id}-pipeline-{pipeline_index + 1}').strip() or f'{normalized_portfolio.id}-pipeline-{pipeline_index + 1}'
            normalized_pipeline.label = str(normalized_pipeline.label or f'Pipeline {pipeline_index + 1}').strip() or f'Pipeline {pipeline_index + 1}'
            normalized_pipeline.portfolioMode = _normalize_portfolio_mode_value(normalized_pipeline.portfolioMode or fallback_portfolio_mode)

            pipeline_entries = normalize_strategy_set_entries(normalized_pipeline.strategyEntries, payload.strategy)
            compiled_pipeline_entries = []

            for entry in pipeline_entries:
                requested_mode = _normalize_compiled_volume_mode(entry.volumeMode)
                legacy_volume, legacy_fallback_applied = _derive_entry_legacy_volume(entry, fallback_initial_volume)
                compiled_entry = entry.model_copy(
                    update={
                        'priority': next_priority,
                        'allocationMode': 'fixed_volume',
                        'allocationValue': legacy_volume,
                        'volumeMode': requested_mode,
                        'fixedVolume': _coerce_positive_float(entry.fixedVolume) if entry.fixedVolume is not None else None,
                        'baseVolume': _coerce_positive_float(entry.baseVolume) if entry.baseVolume is not None else None,
                        'maxVolumeCap': _coerce_positive_float(entry.maxVolumeCap) if entry.maxVolumeCap is not None else None,
                        'referenceCapital': _coerce_positive_float(entry.referenceCapital) if entry.referenceCapital is not None else None,
                        'portfolioId': normalized_portfolio.id,
                        'portfolioLabel': normalized_portfolio.label,
                        'pipelineId': normalized_pipeline.id,
                        'pipelineLabel': normalized_pipeline.label,
                        'legacyVolumeFallbackApplied': bool(legacy_fallback_applied),
                    },
                    deep=True,
                )
                compiled_pipeline_entries.append(compiled_entry)
                compiled_entries.append(compiled_entry)
                next_priority += 1

            next_pipelines.append({
                **normalized_pipeline.model_dump(),
                'portfolioMode': normalized_pipeline.portfolioMode,
                'strategyEntries': [entry.model_dump() for entry in compiled_pipeline_entries],
            })

        normalized_portfolios.append({
            **normalized_portfolio.model_dump(),
            'pipelines': next_pipelines,
        })

    if not compiled_entries:
        return {
            'entries': legacy_entries,
            'portfolio_structure_version': _normalize_portfolio_structure_version(payload.portfolioStructureVersion),
            'capital_model': dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None,
            'portfolios': _build_implicit_legacy_portfolios(legacy_entries, portfolio_mode=fallback_portfolio_mode),
        }

    return {
        'entries': compiled_entries,
        'portfolio_structure_version': 2,
        'capital_model': dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None,
        'portfolios': normalized_portfolios,
    }


def build_strategy_request_payload(
    payload: ApplyStrategyRequest,
    *,
    effective_strategy: StrategyPayload | None = None,
    backtest_payload: dict | None = None,
    extra_fields: dict | None = None,
):
    resolved_request = resolve_strategy_request_entries(payload)
    compiled_entries = list(resolved_request.get('entries') or [])
    request_payload = {
        **payload.model_dump(),
        'strategy': (
            effective_strategy.model_dump()
            if isinstance(effective_strategy, StrategyPayload)
            else payload.strategy.model_dump()
        ),
        'strategies': [entry.model_dump() for entry in compiled_entries],
        'portfolioStructureVersion': resolved_request.get('portfolio_structure_version'),
        'capitalModel': resolved_request.get('capital_model'),
        'portfolios': list(resolved_request.get('portfolios') or []),
        'backtest': backtest_payload if isinstance(backtest_payload, dict) else payload.backtest.model_dump(),
    }
    if isinstance(extra_fields, dict) and extra_fields:
        request_payload.update(extra_fields)
    return request_payload


def get_primary_strategy_for_request(payload: ApplyStrategyRequest):
    strategy_entries = resolve_strategy_request_entries(payload)['entries']
    enabled_entries = [entry for entry in strategy_entries if bool(entry.enabled)]
    if enabled_entries:
        return enabled_entries[0].strategy
    return strategy_entries[0].strategy


def build_runtime_strategy_bundle(payload: ApplyStrategyRequest):
    resolved_request = resolve_strategy_request_entries(payload)
    strategy_entries = resolved_request['entries']
    enabled_entries = [entry for entry in strategy_entries if bool(entry.enabled)]
    active_entries = enabled_entries if enabled_entries else strategy_entries

    runtime_entries = []
    required_features = set()
    required_feature_details = []

    for entry in active_entries:
        strategy_params = build_strategy_params(entry.strategy)
        strategy = Strategy()
        strategy.set_params(**strategy_params)
        entry_required_features = strategy.get_required_feature_names()
        for feature_name in entry_required_features:
            if feature_name not in required_features:
                required_features.add(feature_name)
                required_feature_details.append(describe_indicator_feature_name(feature_name))
        runtime_entries.append({
            'strategy_id': str(entry.id or '').strip() or 'strategy',
            'strategy_label': str(entry.label or '').strip() or 'Strategy',
            'priority': int(entry.priority),
            'enabled': bool(entry.enabled),
            'symbol': str(entry.symbol or '').strip().upper(),
            'timeframe': str(entry.timeframe or '').strip().upper(),
            'allocation_mode': str(entry.allocationMode or 'fixed_volume'),
            'allocation_value': entry.allocationValue,
            'volume_mode': str(entry.volumeMode or 'fixed_volume'),
            'fixed_volume': entry.fixedVolume,
            'base_volume': entry.baseVolume,
            'max_volume_cap': entry.maxVolumeCap,
            'reference_capital': entry.referenceCapital,
            'portfolio_id': str(entry.portfolioId or '').strip(),
            'portfolio_label': str(entry.portfolioLabel or '').strip(),
            'pipeline_id': str(entry.pipelineId or '').strip(),
            'pipeline_label': str(entry.pipelineLabel or '').strip(),
            'legacy_volume_fallback_applied': bool(entry.legacyVolumeFallbackApplied),
            'strategy_payload': entry.strategy,
            'strategy_params': strategy_params,
            'strategy': strategy,
        })

    runtime_entries.sort(key=lambda item: (int(item['priority']), str(item['strategy_id'])))
    return {
        'entries': runtime_entries,
        'primary_strategy': active_entries[0].strategy if active_entries else payload.strategy,
        'required_features': sorted(required_features),
        'required_feature_details': required_feature_details,
        'is_multi': len(runtime_entries) > 1 or int(resolved_request.get('portfolio_structure_version') or 1) >= 2,
    }


def build_backtester_runtime_entry(runtime_entry: dict):
    return {
        'strategy_id': runtime_entry['strategy_id'],
        'strategy_label': runtime_entry['strategy_label'],
        'priority': runtime_entry['priority'],
        'enabled': runtime_entry['enabled'],
        'symbol': runtime_entry.get('symbol'),
        'timeframe': runtime_entry.get('timeframe'),
        'allocation_mode': runtime_entry.get('allocation_mode'),
        'allocation_value': runtime_entry.get('allocation_value'),
        'volume_mode': runtime_entry.get('volume_mode'),
        'fixed_volume': runtime_entry.get('fixed_volume'),
        'base_volume': runtime_entry.get('base_volume'),
        'max_volume_cap': runtime_entry.get('max_volume_cap'),
        'reference_capital': runtime_entry.get('reference_capital'),
        'portfolio_id': runtime_entry.get('portfolio_id'),
        'portfolio_label': runtime_entry.get('portfolio_label'),
        'pipeline_id': runtime_entry.get('pipeline_id'),
        'pipeline_label': runtime_entry.get('pipeline_label'),
        'legacy_volume_fallback_applied': runtime_entry.get('legacy_volume_fallback_applied'),
        'strategy': runtime_entry['strategy'],
    }


def resolve_strategy_entry_market_context(runtime_entry: dict | None, default_symbol: str, default_timeframe: str):
    safe_entry = runtime_entry or {}
    symbol_name = str(safe_entry.get('symbol') or default_symbol or 'EURUSD').strip().upper() or 'EURUSD'
    timeframe = str(safe_entry.get('timeframe') or default_timeframe or 'M1').strip().upper() or 'M1'
    return symbol_name, timeframe


def strategy_bundle_uses_nondefault_market_context(
    strategy_bundle: dict | None,
    *,
    default_symbol: str,
    default_timeframe: str,
):
    safe_bundle = strategy_bundle or {}
    for runtime_entry in list(safe_bundle.get('entries') or []):
        symbol_name, timeframe = resolve_strategy_entry_market_context(
            runtime_entry,
            default_symbol=default_symbol,
            default_timeframe=default_timeframe,
        )
        if symbol_name != str(default_symbol or '').strip().upper() or timeframe != str(default_timeframe or '').strip().upper():
            return True
    return False


def build_backtest_params(payload: BacktestPayload, *, capital_model: dict | None = None):
    legacy_slippage = float(payload.slippageInPips)
    cost_profile = normalize_backtest_cost_profile(payload.costProfile)
    portfolio_mode = str(payload.portfolioMode or 'shared_pipe').strip().lower() or 'shared_pipe'
    if portfolio_mode not in {'shared_pipe', 'parallel_sleeves'}:
        portfolio_mode = 'shared_pipe'
    return {
        'initial_balance': float(payload.initialBalance),
        'asset_type': str(payload.assetType).strip().lower(),
        'initial_volume': float(payload.initialVolume),
        'pip_size': float(payload.pipSize),
        'pip_value_per_lot': float(payload.pipValuePerLot),
        'cost_profile': cost_profile,
        'spread_in_pips': float(payload.spreadInPips),
        'entry_slippage_in_pips': float(payload.entrySlippageInPips if payload.entrySlippageInPips is not None else legacy_slippage),
        'close_slippage_in_pips': float(payload.closeSlippageInPips if payload.closeSlippageInPips is not None else legacy_slippage),
        'take_profit_slippage_in_pips': float(payload.takeProfitSlippageInPips if payload.takeProfitSlippageInPips is not None else legacy_slippage),
        'stop_loss_slippage_in_pips': float(payload.stopLossSlippageInPips if payload.stopLossSlippageInPips is not None else legacy_slippage),
        'trailing_stop_slippage_in_pips': float(payload.trailingStopSlippageInPips if payload.trailingStopSlippageInPips is not None else legacy_slippage),
        'minimum_stop_distance_in_pips': max(float(payload.minimumStopDistanceInPips or 0.0), 0.0),
        'volatility_slippage_multiplier': float(payload.volatilitySlippageMultiplier),
        'execution_mode': str(payload.executionMode).strip().lower() or 'next_bar_open',
        'portfolio_mode': portfolio_mode,
        'history_scope_mode': str(payload.historyScopeMode or 'loaded_chart').strip().lower() or 'loaded_chart',
        'history_scope_bars': max(1, int(payload.historyScopeBars or 1)) if payload.historyScopeBars is not None else None,
        'broker_cost_context': {
            'broker_profile_id': str(payload.brokerProfileId or '').strip(),
            'broker_profile_label': str(payload.brokerProfileLabel or '').strip(),
            'broker_code': str(payload.brokerCode or '').strip().lower(),
            'broker_label': str(payload.brokerLabel or '').strip() or str(payload.brokerProfileLabel or '').strip(),
            'market_domain': str(payload.brokerMarketDomain or '').strip().lower(),
            'broker_cost_profile': str(payload.brokerCostProfile or '').strip().lower(),
            'broker_default_asset_type': str(payload.brokerDefaultAssetType or '').strip().lower(),
        },
        'capital_model': dict(capital_model or {}) if isinstance(capital_model, dict) else None,
    }


def extract_expression_identifiers(expression: str):
    if not expression:
        return []

    tokens = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', expression)

    ignored = {
        'and', 'or', 'not', 'True', 'False', 'None',
        'open', 'high', 'low', 'close',
        'long_open_price', 'short_open_price',
        'long_close_price', 'short_close_price',
        'long_order_life', 'short_order_life', 'opened_order_life',
        'last_long_close_timestamp', 'last_short_close_timestamp', 'last_trade_close_timestamp',
        'position',
        'long_trailing_stop_price', 'short_trailing_stop_price',
    }

    identifiers = []
    for token in tokens:
        if token not in ignored:
            identifiers.append(token)

    return identifiers


def validate_strategy_expressions(strategy_params: dict, available_columns: list[str]):
    expression_fields = [
        'open_long_condition',
        'close_long_condition',
        'open_short_condition',
        'close_short_condition',
        'open_trade_price_long',
        'open_trade_price_short',
        'close_trade_price_long',
        'close_trade_price_short',
        'stop_gain_long_price',
        'stop_loss_long_price',
        'stop_gain_short_price',
        'stop_loss_short_price',
        'trailing_stop_long_price',
        'trailing_stop_short_price',
    ]

    available_names = {
        normalize_indicator_feature_name(column_name)
        for column_name in available_columns
    }
    available_names.update(
        build_expression_safe_identifier(column_name)
        for column_name in available_columns
        if build_expression_safe_identifier(column_name)
    )

    for field_name in expression_fields:
        expression = strategy_params.get(field_name, '')
        identifiers = extract_expression_identifiers(expression)

        for identifier in identifiers:
            if identifier not in available_names:
                raise ValueError(
                    f'Unknown identifier "{identifier}" in {field_name}. '
                    f'Available columns: {available_columns}'
                )


def _normalize_alias_token(value):
    return str(value or '').strip()

def _get_indicator_alias_candidates(indicator: dict | None):
    safe_indicator = dict(indicator or {})
    primary_alias = _normalize_alias_token(safe_indicator.get('alias'))
    indicator_name = _normalize_alias_token(safe_indicator.get('name'))
    normalized_name = indicator_name.lower()
    has_explicit_custom_alias = bool(primary_alias) and primary_alias.lower() != normalized_name
    candidates = []
    seen = set()

    def register(candidate):
        safe_candidate = _normalize_alias_token(candidate)
        if not safe_candidate or safe_candidate in seen:
            return
        seen.add(safe_candidate)
        candidates.append(safe_candidate)

    if not has_explicit_custom_alias and normalized_name == 'marketregime':
        register('mreg')
    if not has_explicit_custom_alias and normalized_name == 'rsi':
        register('rsi')

    if primary_alias:
        register(primary_alias)
        register(primary_alias.lower())

    if not has_explicit_custom_alias and indicator_name:
        register(indicator_name)
        register(indicator_name.lower())

    return candidates


def _get_indicator_line_alias_suffix(line_detail: dict | None, indicator_name: str = ''):
    safe_line = dict(line_detail or {})
    line_key = _normalize_alias_token(safe_line.get('line_key'))
    line_label = _normalize_alias_token(safe_line.get('line_label'))
    line_suffix = _normalize_alias_token(safe_line.get('line_suffix'))
    normalized_indicator_name = _normalize_alias_token(indicator_name).lower()
    normalized_key = line_key.lower()

    if normalized_key in {'value', 'main'}:
        return 'value'
    if line_key:
        return line_key
    if line_label and line_label.lower() != normalized_indicator_name:
        return line_label
    return line_suffix


def build_indicator_alias_registry(applied_indicators: list[dict] | None):
    alias_to_column = {}
    duplicate_aliases = set()

    def register(alias, column_name):
        safe_alias = _normalize_alias_token(alias)
        safe_column_name = _normalize_alias_token(column_name)
        if not safe_alias or not safe_column_name or safe_alias in duplicate_aliases:
            return

        existing = alias_to_column.get(safe_alias)
        if existing is None:
            alias_to_column[safe_alias] = safe_column_name
            return

        if existing != safe_column_name:
            duplicate_aliases.add(safe_alias)
            alias_to_column.pop(safe_alias, None)

    for indicator in list(applied_indicators or []):
        if not isinstance(indicator, dict):
            continue

        safe_indicator = dict(indicator)
        indicator_name = _normalize_alias_token(safe_indicator.get('name'))
        alias_candidates = _get_indicator_alias_candidates(safe_indicator)
        line_details = list(safe_indicator.get('column_details') or [])
        fallback_line_details = []
        try:
            if safe_indicator.get('columns'):
                fallback_line_details = describe_indicator_columns(
                    indicator_name,
                    safe_indicator.get('params') or [],
                    safe_indicator.get('columns') or [],
                )
        except Exception:
            fallback_line_details = []
        if not line_details or len(line_details) < len(fallback_line_details):
            line_details = fallback_line_details
        if not line_details:
            continue

        if len(line_details) == 1:
            column_name = _normalize_alias_token(line_details[0].get('column_name'))
            for alias_candidate in alias_candidates:
                register(alias_candidate, column_name)
            continue

        for line_detail in line_details:
            column_name = _normalize_alias_token(line_detail.get('column_name'))
            suffix = _normalize_alias_token(_get_indicator_line_alias_suffix(line_detail, indicator_name))
            if not column_name or not suffix:
                continue
            for alias_candidate in alias_candidates:
                register(f'{alias_candidate}_{suffix}', column_name)

    return alias_to_column, duplicate_aliases


def resolve_strategy_param_aliases(strategy_params: dict | None, applied_indicators: list[dict] | None):
    safe_params = dict(strategy_params or {})
    alias_to_column, duplicate_aliases = build_indicator_alias_registry(applied_indicators)
    if duplicate_aliases:
        referenced_duplicates = set()
        for field_value in safe_params.values():
            if not isinstance(field_value, str) or not field_value.strip():
                continue
            for identifier in extract_expression_identifiers(field_value):
                if identifier in duplicate_aliases:
                    referenced_duplicates.add(identifier)
        if referenced_duplicates:
            duplicate_list = ', '.join(sorted(referenced_duplicates))
            raise ValueError(f'Duplicate indicator aliases found: {duplicate_list}')
    runtime_column_names = []
    for indicator in list(applied_indicators or []):
        if not isinstance(indicator, dict):
            continue
        for column_name in list(indicator.get('columns') or []):
            safe_column_name = _normalize_alias_token(column_name)
            if safe_column_name:
                runtime_column_names.append(safe_column_name)

    if not alias_to_column and not runtime_column_names:
        return safe_params

    alias_entries = sorted(alias_to_column.items(), key=lambda item: len(item[0]), reverse=True)
    safe_identifier_entries = sorted(
        {
            column_name: build_expression_safe_identifier(column_name)
            for column_name in runtime_column_names
            if build_expression_safe_identifier(column_name) and build_expression_safe_identifier(column_name) != column_name
        }.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    resolved_params = {}

    for field_name, field_value in safe_params.items():
        if not isinstance(field_value, str) or not field_value.strip():
            resolved_params[field_name] = field_value
            continue

        resolved_expression = field_value
        for alias, column_name in alias_entries:
            resolved_expression = re.sub(
                rf'\b{re.escape(alias)}\b',
                build_expression_safe_identifier(column_name),
                resolved_expression,
            )
        for column_name, safe_identifier in safe_identifier_entries:
            resolved_expression = resolved_expression.replace(column_name, safe_identifier)
        resolved_params[field_name] = resolved_expression

    return resolved_params


def sanitize_value(value):
    if value is None:
        return None

    if isinstance(value, np.generic):
        value = value.item()

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, dict):
        return {
            key: sanitize_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [sanitize_value(item) for item in value]

    return value


def sanitize_dict_values(data: dict):
    return {
        key: sanitize_value(value)
        for key, value in data.items()
    }


def sanitize_records(records: list[dict]):
    sanitized = []

    for record in records:
        sanitized_record = {}

        for key, value in record.items():
            sanitized_record[key] = sanitize_value(value)

        sanitized.append(sanitized_record)

    return sanitized


def serialize_results(results):
    if results is None:
        return []

    if hasattr(results, 'to_dict'):
        try:
            return sanitize_records(results.to_dict(orient='records'))
        except Exception:
            return []

    return []


def build_execution_policy_payload(backtest_request: dict | None = None):
    safe_backtest = merge_backtest_cost_profile_values(backtest_request or {})
    execution_mode = str(safe_backtest.get('executionMode') or 'next_bar_open').strip().lower() or 'next_bar_open'
    portfolio_mode = str(safe_backtest.get('portfolioMode') or 'shared_pipe').strip().lower() or 'shared_pipe'
    if portfolio_mode not in {'shared_pipe', 'parallel_sleeves'}:
        portfolio_mode = 'shared_pipe'

    return {
        **build_backtest_cost_policy(safe_backtest),
        'execution_mode': execution_mode,
        'portfolio_mode': portfolio_mode,
        'take_profit_fill': 'target_price',
        'stop_loss_fill': 'bar_extreme',
        'trailing_stop_fill': 'bar_extreme',
        'trailing_entry_policy': 'blocked_on_entry_candle',
        'same_bar_gain_exit': True,
        'same_bar_loss_exit': True,
        'same_bar_trailing_exit': False,
        'intrabar_conflict_policy': 'pessimistic_loss_first',
        'spread_in_pips': float(safe_backtest.get('spreadInPips') or 0.0),
        'entry_slippage_in_pips': float(
            safe_backtest.get('entrySlippageInPips')
            if safe_backtest.get('entrySlippageInPips') is not None
            else safe_backtest.get('slippageInPips') or 0.0
        ),
        'close_slippage_in_pips': float(
            safe_backtest.get('closeSlippageInPips')
            if safe_backtest.get('closeSlippageInPips') is not None
            else safe_backtest.get('slippageInPips') or 0.0
        ),
        'take_profit_slippage_in_pips': float(
            safe_backtest.get('takeProfitSlippageInPips')
            if safe_backtest.get('takeProfitSlippageInPips') is not None
            else safe_backtest.get('slippageInPips') or 0.0
        ),
        'stop_loss_slippage_in_pips': float(
            safe_backtest.get('stopLossSlippageInPips')
            if safe_backtest.get('stopLossSlippageInPips') is not None
            else safe_backtest.get('slippageInPips') or 0.0
        ),
        'trailing_stop_slippage_in_pips': float(
            safe_backtest.get('trailingStopSlippageInPips')
            if safe_backtest.get('trailingStopSlippageInPips') is not None
            else safe_backtest.get('slippageInPips') or 0.0
        ),
        'minimum_stop_distance_in_pips': max(float(safe_backtest.get('minimumStopDistanceInPips') or 0.0), 0.0),
        'volatility_slippage_multiplier': float(safe_backtest.get('volatilitySlippageMultiplier') or 0.0),
        'volatility_slippage_reference': 'previous_bar_range',
        'history_scope_mode': str(safe_backtest.get('historyScopeMode') or 'loaded_chart').strip().lower() or 'loaded_chart',
        'history_scope_bars': (
            max(1, int(safe_backtest.get('historyScopeBars') or 1))
            if safe_backtest.get('historyScopeBars') is not None
            else None
        ),
    }


def build_runtime_payload():
    strategy_state = state.strategy
    current_request = strategy_state.request or {}

    return {
        'has_strategy': strategy_state.strategy is not None,
        'has_backtester': strategy_state.backtester is not None,
        'has_results': strategy_state.results is not None,
        'backtest_active': strategy_state.backtest_active,
        'last_applied_at': strategy_state.last_applied_at,
        'last_results_generated_at': strategy_state.last_results_generated_at,
        'last_invalidated_reason': strategy_state.last_invalidated_reason,
        'stats': strategy_state.stats,
        'applied_indicators': strategy_state.applied_indicators,
        'available_columns': strategy_state.available_columns,
        'available_column_details': strategy_state.available_column_details,
        'required_features': strategy_state.required_features,
        'required_feature_details': strategy_state.required_feature_details,
        'strategy_view_meta': dict(strategy_state.strategy_view_meta) if strategy_state.strategy_view_meta else None,
        'results_view': build_results_view(
            request=current_request,
            stats=dict(strategy_state.stats or {}),
            results=strategy_state.results,
            trade_markers=list(strategy_state.trade_markers or []),
            strategy_view_meta=dict(strategy_state.strategy_view_meta or {}),
        ).get('meta'),
        'trade_markers': strategy_state.trade_markers,
        'last_invalidated_overlap': strategy_state.last_invalidated_overlap,
        'is_stale': strategy_state.is_stale,
        'stale_reason': strategy_state.stale_reason,
        'stale_overlap': strategy_state.stale_overlap,
        'last_refresh_mode': strategy_state.last_refresh_mode,
        'last_refresh_from_index': strategy_state.last_refresh_from_index,
        'refresh_counts': dict(strategy_state.refresh_counts or {}),
        'recent_reasons': list(strategy_state.recent_reasons or []),
        'performance': dict(strategy_state.performance) if strategy_state.performance else None,
        'workspace_user_id': strategy_state.workspace_user_id,
        'workspace_id': strategy_state.workspace_id,
        'execution_policy': build_execution_policy_payload(current_request.get('backtest')),
    }


def build_isolated_backtest_response(*, request_payload: dict, evaluation: dict):
    stats = dict(evaluation.get('stats') or {})
    return {
        'status': 'ok',
        'request': request_payload,
        'runtime': build_runtime_payload(),
        'rows': len(evaluation['serialized_results']),
        'results': evaluation['serialized_results'],
        'stats': stats,
        'scope_tree': stats.get('scope_tree') or {},
        'rollups': stats.get('rollups') or {},
        'ledger': list(stats.get('ledger') or []),
        'trade_markers': evaluation.get('trade_markers') or [],
        'strategy_view_meta': evaluation.get('strategy_view_meta') or None,
        'applied_indicators': list(evaluation.get('applied_indicators') or []),
        'available_columns': list(evaluation.get('available_columns') or []),
        'available_column_details': list(evaluation.get('available_column_details') or []),
        'has_results': bool(evaluation['serialized_results']),
        'last_results_generated_at': time.time(),
    }


def _record_strategy_refresh(mode: str, reason: str, started_at: float, rows: int = 0, stats: dict | None = None):
    strategy_state = state.strategy
    refresh_mode = str(mode or 'full').strip().lower() or 'full'
    refresh_counts = dict(strategy_state.refresh_counts or {})
    refresh_counts[refresh_mode] = int(refresh_counts.get(refresh_mode, 0) or 0) + 1
    strategy_state.refresh_counts = refresh_counts

    elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    performance = {
        'last_elapsed_ms': elapsed_ms,
        'last_rows': int(rows or 0),
        'last_reason': str(reason or refresh_mode),
        'last_mode': refresh_mode,
    }
    if stats:
        performance['last_n_trades'] = stats.get('n_trades')
        performance['last_net_pnl'] = stats.get('net_pnl')
    strategy_state.performance = performance
    strategy_state.recent_reasons = [
        {
            'kind': 'refresh',
            'mode': refresh_mode,
            'reason': str(reason or refresh_mode),
            'rows': int(rows or 0),
            'elapsed_ms': elapsed_ms,
            'at': time.time(),
        },
        *list(strategy_state.recent_reasons or []),
    ][:12]


def persist_strategy_runtime_if_configured():
    strategy_state = state.strategy

    if not strategy_state.workspace_user_id or not strategy_state.workspace_id:
        return None

    return persist_strategy_runtime_snapshot(
        user_id=strategy_state.workspace_user_id,
        workspace_id=strategy_state.workspace_id,
        strategy_request=strategy_state.request,
        stats=strategy_state.stats,
        results=serialize_results(strategy_state.results),
        trade_markers=sanitize_value(list(strategy_state.trade_markers or [])),
        runtime_payload=build_runtime_payload(),
    )


def build_configure_response_payload(status: str = 'ok'):
    strategy_state = state.strategy
    serialized_results = serialize_results(strategy_state.results)
    return {
        'status': status,
        'request': strategy_state.request,
        **build_runtime_payload(),
        'rows': len(serialized_results),
        'results': serialized_results,
    }


def configure_strategy_runtime(payload: ConfigureStrategyRequest):
    strategy_state = state.strategy
    history_loaded = wait_for_history(timeout_seconds=10.0)

    if not history_loaded:
        return {
            'status': 'error',
            'error': state.bridge.history_error or 'History not ready',
            **build_runtime_payload(),
        }

    try:
        ensure_chart_snapshot()
        chart_state = state.chart
        snapshot_symbol = chart_state.snapshot_symbol

        if snapshot_symbol is None:
            raise ValueError('Chart snapshot is not available')

        strategy_view = build_strategy_feature_view(
            chart_request=chart_state.request,
            snapshot_symbol=snapshot_symbol,
            applied_indicators=chart_state.snapshot_applied_indicators,
            available_column_details=chart_state.snapshot_available_column_details,
            snapshot_signature=chart_state.snapshot_signature,
        )
        symbol = strategy_view['symbol']
        available_columns = list(strategy_view['available_columns'])
        strategy_params = resolve_strategy_param_aliases(
            build_strategy_params(payload.strategy),
            strategy_view['applied_indicators'],
        )
        validate_strategy_expressions(
            strategy_params=strategy_params,
            available_columns=available_columns,
        )

        strategy = Strategy()
        strategy.set_params(**strategy_params)
        required_features = strategy.get_required_feature_names()
        required_feature_details = [
            describe_indicator_feature_name(feature_name)
            for feature_name in required_features
        ]

        existing_request = strategy_state.request or {}
        next_request = build_strategy_request_payload(
            ApplyStrategyRequest(
                strategy=payload.strategy,
                strategies=list(existing_request.get('strategies') or []),
                portfolioStructureVersion=existing_request.get('portfolioStructureVersion'),
                capitalModel=existing_request.get('capitalModel'),
                portfolios=list(existing_request.get('portfolios') or []),
                backtest=BacktestPayload.model_validate(
                    existing_request.get('backtest') or BacktestPayload().model_dump()
                ),
            ),
            effective_strategy=payload.strategy,
        )

        if strategy_state.backtest_active:
            strategy_state.request = next_request
            strategy_state.symbol = symbol
            strategy_state.strategy = strategy
            strategy_state.applied_indicators = list(chart_state.snapshot_applied_indicators)
            strategy_state.available_columns = available_columns
            strategy_state.available_column_details = list(chart_state.snapshot_available_column_details)
            strategy_state.required_features = required_features
            strategy_state.required_feature_details = required_feature_details
            strategy_state.strategy_view_meta = dict(strategy_view.get('meta') or {})
            strategy_state.last_applied_at = time.time()
            strategy_state.last_results_generated_at = None
            strategy_state.last_invalidated_reason = None
            strategy_state.last_invalidated_overlap = []
            strategy_state.is_stale = False
            strategy_state.stale_reason = None
            strategy_state.stale_overlap = []
            strategy_state.last_refresh_mode = 'full'
            strategy_state.last_refresh_from_index = 0

            refreshed = run_strategy_request(ApplyStrategyRequest(
                strategy=effective_strategy,
                strategies=list(next_request.get('strategies') or []),
                portfolioStructureVersion=next_request.get('portfolioStructureVersion'),
                capitalModel=next_request.get('capitalModel'),
                portfolios=list(next_request.get('portfolios') or []),
                backtest=BacktestPayload.model_validate(next_request['backtest']),
            ))

            if refreshed.get('status') == 'ok':
                refreshed['refresh_mode'] = 'full'
            return refreshed

        strategy_state.request = next_request
        strategy_state.symbol = symbol
        strategy_state.strategy = strategy
        strategy_state.applied_indicators = list(strategy_view['applied_indicators'])
        strategy_state.available_columns = available_columns
        strategy_state.available_column_details = list(strategy_view['available_column_details'])
        strategy_state.required_features = required_features
        strategy_state.required_feature_details = required_feature_details
        strategy_state.strategy_view_meta = dict(strategy_view.get('meta') or {})
        strategy_state.last_applied_at = time.time()
        strategy_state.last_results_generated_at = None
        strategy_state.last_invalidated_reason = None
        strategy_state.last_invalidated_overlap = []

        strategy_state.backtester = None
        strategy_state.results = None
        strategy_state.stats = None
        strategy_state.trade_markers = []
        strategy_state.is_stale = False
        strategy_state.stale_reason = None
        strategy_state.stale_overlap = []
        strategy_state.last_refresh_mode = None
        strategy_state.last_refresh_from_index = None
        strategy_state.last_results_generated_at = None

        persist_strategy_runtime_if_configured()

        return build_configure_response_payload()
    except Exception as error:
        return {
            'status': 'error',
            'error': str(error),
            **build_runtime_payload(),
        }


def build_strategy_event_payload(event_type: str = 'strategy.snapshot', source: str = 'server'):
    strategy_state = state.strategy
    serialized_results = serialize_results(strategy_state.results)

    return {
        'type': event_type,
        'source': source,
        'status': 'ok' if strategy_state.results is not None else 'empty',
        'request': strategy_state.request,
        **build_runtime_payload(),
        'rows': len(serialized_results),
        'results': serialized_results,
    }


async def broadcast_strategy_event(event_type: str = 'strategy.updated', source: str = 'server'):
    return await realtime_sync.broadcast(
        STRATEGY_CHANNEL_KEY,
        build_strategy_event_payload(event_type=event_type, source=source),
    )


def get_strategy_feature_overlap(changed_features: list[str] | None = None):
    strategy_state = state.strategy
    required_features = set(strategy_state.required_features or [])
    safe_changed_features = [str(feature or '').strip() for feature in (changed_features or []) if str(feature or '').strip()]

    overlap = [
        feature_name
        for feature_name in safe_changed_features
        if feature_name in required_features
    ]

    return sorted(set(overlap))


def evaluate_strategy_request(payload: ApplyStrategyRequest):
    history_loaded = wait_for_history(timeout_seconds=10.0)

    if not history_loaded:
        return {
            'status': 'error',
            'error': state.bridge.history_error or 'History not ready',
            **build_runtime_payload(),
        }

    try:
        ensure_chart_snapshot()
        chart_state = state.chart
        snapshot_symbol = chart_state.snapshot_symbol

        if snapshot_symbol is None:
            raise ValueError('Chart snapshot is not available')

        strategy_bundle = build_runtime_strategy_bundle(payload)
        effective_strategy = strategy_bundle['primary_strategy']
        backtest_params = build_backtest_params(
            payload.backtest,
            capital_model=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
        )
        chart_request = dict(chart_state.request or {})
        chart_symbol_name = str(chart_request.get('symbol') or 'EURUSD').strip().upper() or 'EURUSD'
        chart_timeframe = str(chart_request.get('timeframe') or 'M1').strip().upper() or 'M1'
        chart_bars = max(1, int(chart_request.get('bars') or len(snapshot_symbol.candles) or 1))

        if strategy_bundle_uses_nondefault_market_context(
            strategy_bundle,
            default_symbol=chart_symbol_name,
            default_timeframe=chart_timeframe,
        ):
            grouped_evaluation = evaluate_strategy_request_in_context(
                payload=payload,
                symbol_name=chart_symbol_name,
                timeframe=chart_timeframe,
                bars=chart_bars,
                indicators_payload=normalize_indicator_payload(list(chart_request.get('indicators') or [])),
            )
            if grouped_evaluation.get('status') == 'ok':
                grouped_evaluation['required_features'] = strategy_bundle['required_features']
                grouped_evaluation['required_feature_details'] = strategy_bundle['required_feature_details']
            return grouped_evaluation

        strategy_view = build_strategy_feature_view(
            chart_request=chart_request,
            snapshot_symbol=snapshot_symbol,
            applied_indicators=chart_state.snapshot_applied_indicators,
            available_column_details=chart_state.snapshot_available_column_details,
            backtest_params=backtest_params,
            snapshot_signature=chart_state.snapshot_signature,
        )
        symbol = strategy_view['symbol']
        history_scope_info = dict(strategy_view['history_scope_info'])
        available_columns = list(strategy_view['available_columns'])

        for runtime_entry in strategy_bundle['entries']:
            resolved_strategy_params = resolve_strategy_param_aliases(
                runtime_entry['strategy_params'],
                strategy_view['applied_indicators'],
            )
            validate_strategy_expressions(
                strategy_params=resolved_strategy_params,
                available_columns=available_columns,
            )
            runtime_entry['strategy'].set_params(
                **resolved_strategy_params,
                execution_mode=backtest_params['execution_mode'],
            )

        if strategy_bundle['is_multi']:
            backtester = MultiStrategyBacktester(
                symbol,
                [build_backtester_runtime_entry(runtime_entry) for runtime_entry in strategy_bundle['entries']],
                portfolio_mode=backtest_params['portfolio_mode'],
            )
            backtester.set_params(**backtest_params)
            results = backtester.run()
            strategy = effective_strategy
        else:
            strategy = strategy_bundle['entries'][0]['strategy']
            backtester = Backtester(symbol, strategy)
            backtester.set_params(**backtest_params)
            results = backtester.run()

        return {
            'status': 'ok',
            'symbol': symbol,
            'results': results,
            'strategy': strategy,
            'backtester': backtester,
            'stats': sanitize_dict_values(dict(backtester.stats)),
            'available_columns': available_columns,
            'available_column_details': list(strategy_view['available_column_details']),
            'required_features': strategy_bundle['required_features'],
            'required_feature_details': strategy_bundle['required_feature_details'],
            'strategy_view_meta': dict(strategy_view.get('meta') or {}),
            'trade_markers': sanitize_value(list(backtester.trade_markers)),
            'history_scope_info': history_scope_info,
            'applied_indicators': list(strategy_view['applied_indicators']),
            'serialized_results': serialize_results(results),
        }
    except Exception as error:
        return {
            'status': 'error',
            'error': str(error),
            **build_runtime_payload(),
            'chart_indicators': list(state.chart.request['indicators']),
            'available_columns': list(symbol.candles.columns) if 'symbol' in locals() and symbol is not None else [],
        }


def run_strategy_request(payload: ApplyStrategyRequest):
    strategy_state = state.strategy
    started_at = time.perf_counter()
    evaluation = evaluate_strategy_request(payload)

    if evaluation.get('status') != 'ok':
        return evaluation

    effective_strategy = get_primary_strategy_for_request(payload)
    strategy_state.request = build_strategy_request_payload(
        payload,
        effective_strategy=effective_strategy,
        backtest_payload={
            **payload.backtest.model_dump(),
            **evaluation['history_scope_info'],
        },
    )
    strategy_state.symbol = evaluation['symbol']
    strategy_state.strategy = evaluation['strategy']
    strategy_state.backtester = evaluation['backtester']
    strategy_state.results = evaluation['results']
    strategy_state.stats = evaluation['stats']
    strategy_state.applied_indicators = evaluation['applied_indicators']
    strategy_state.available_columns = evaluation['available_columns']
    strategy_state.available_column_details = evaluation['available_column_details']
    strategy_state.required_features = evaluation['required_features']
    strategy_state.required_feature_details = evaluation['required_feature_details']
    strategy_state.strategy_view_meta = evaluation.get('strategy_view_meta') or None
    strategy_state.trade_markers = evaluation['trade_markers']
    strategy_state.last_applied_at = time.time()
    strategy_state.last_results_generated_at = strategy_state.last_applied_at
    strategy_state.last_invalidated_reason = None
    strategy_state.last_invalidated_overlap = []
    strategy_state.is_stale = False
    strategy_state.stale_reason = None
    strategy_state.stale_overlap = []
    strategy_state.last_refresh_mode = 'full'
    strategy_state.last_refresh_from_index = 0
    _record_strategy_refresh(
        mode='full',
        reason='run_strategy_request',
        started_at=started_at,
        rows=len(evaluation['serialized_results']),
        stats=strategy_state.stats,
    )

    persist_strategy_runtime_if_configured()

    return {
        'status': 'ok',
        'request': strategy_state.request,
        **build_runtime_payload(),
        'rows': len(evaluation['serialized_results']),
        'results': evaluation['serialized_results'],
    }


def summarize_comparison_stats(stats: dict | None = None):
    safe_stats = stats or {}
    return {
        'net_pnl': safe_stats.get('net_pnl'),
        'win_rate': safe_stats.get('win_rate'),
        'expectancy_per_trade': safe_stats.get('expectancy_per_trade'),
        'max_drawdown': safe_stats.get('max_drawdown'),
        'max_drawdown_pct': safe_stats.get('max_drawdown_pct'),
        'n_trades': safe_stats.get('n_trades'),
        'strategy_count': safe_stats.get('strategy_count'),
        'portfolio_event_counts': safe_stats.get('portfolio_event_counts') or {},
        'portfolio_strategy_stats': safe_stats.get('portfolio_strategy_stats') or [],
        'portfolio_analytics': safe_stats.get('portfolio_analytics') or {},
        'regime_summary': safe_stats.get('regime_summary') or [],
        'regime_stability_summary': safe_stats.get('regime_stability_summary') or [],
    }


def build_comparison_deltas(summary: dict | None = None, baseline: dict | None = None):
    safe_summary = summary or {}
    safe_baseline = baseline or {}

    def delta(key):
        left = safe_summary.get(key)
        right = safe_baseline.get(key)
        if left is None or right is None:
            return None
        try:
            return float(left) - float(right)
        except Exception:
            return None

    return {
        'net_pnl': delta('net_pnl'),
        'win_rate': delta('win_rate'),
        'expectancy_per_trade': delta('expectancy_per_trade'),
        'max_drawdown': delta('max_drawdown'),
        'max_drawdown_pct': delta('max_drawdown_pct'),
        'n_trades': delta('n_trades'),
    }


def build_backtest_payload_for_window(
    backtest: BacktestPayload,
    bars: int | None = None,
):
    payload = backtest.model_copy(deep=True)
    if bars is not None:
        payload.historyScopeMode = 'custom'
        payload.historyScopeBars = max(1, int(bars))
    return payload


def build_research_chart_context_for_bars(
    chart_context: dict | None,
    bars: int | None = None,
):
    safe_context = normalize_research_chart_context(chart_context)
    if not safe_context:
        return None

    if bars is not None:
        safe_context['bars'] = max(1, int(bars))

    if safe_context.get('indicators'):
        safe_context['indicators'] = ensure_market_regime_indicator_payload(safe_context.get('indicators') or [])

    return safe_context


def evaluate_comparison_entry(
    entry: PresetCompareEntry,
    backtest: BacktestPayload,
    chart_context: dict | None = None,
):
    safe_context = normalize_research_chart_context(chart_context)
    request_payload = ApplyStrategyRequest(
        strategy=entry.strategy,
        strategies=list(entry.strategies or []),
        backtest=backtest,
    )

    if safe_context.get('symbol') and safe_context.get('timeframe'):
        return evaluate_strategy_request_in_context(
            payload=request_payload,
            symbol_name=safe_context['symbol'],
            timeframe=safe_context['timeframe'],
            bars=max(1, int(safe_context.get('bars') or 1)),
            indicators_payload=safe_context.get('indicators') or [],
        )

    return evaluate_strategy_request(request_payload)


def build_study_window_summary(
    *,
    bars: int,
    summary: dict | None = None,
    baseline_summary: dict | None = None,
):
    payload = {
        'bars': max(1, int(bars)),
        'summary': summary or {},
    }
    if baseline_summary is not None:
        payload['delta_vs_baseline'] = build_comparison_deltas(summary, baseline_summary)
    return payload


def normalize_indicator_payload(indicators):
    normalized = []

    for indicator in indicators or []:
        if not isinstance(indicator, dict):
            continue

        name = str(indicator.get('name') or '').strip()
        params = list(indicator.get('params') or [])
        if not name:
            continue

        normalized.append({
            'name': name,
            'params': params,
            'alias': str(indicator.get('alias') or '').strip(),
        })

    return normalized


def _freeze_indicator_signature_value(value):
    if isinstance(value, dict):
        return tuple(
            (str(key), _freeze_indicator_signature_value(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_indicator_signature_value(item) for item in value)
    return value


def build_indicator_payload_signature(indicators_payload: list[dict] | None):
    normalized = normalize_indicator_payload(indicators_payload or [])
    return tuple(
        (
            str(indicator.get('name') or '').strip().upper(),
            tuple(_freeze_indicator_signature_value(param) for param in list(indicator.get('params') or [])),
        )
        for indicator in normalized
    )


def _build_research_feature_view_cache_key(
    *,
    symbol_name: str,
    timeframe: str,
    bars: int,
    indicators_payload: list[dict] | None,
    market_context: dict,
):
    return (
        str(symbol_name or '').strip().upper(),
        str(timeframe or '').strip().upper(),
        max(1, int(bars or 1)),
        str(market_context.get('cache_key') or '').strip(),
        market_context.get('revision'),
        build_indicator_payload_signature(indicators_payload),
    )


def _touch_research_feature_view_cache_key(cache_key):
    cache_order = state.research.feature_view_cache_order
    if cache_key in cache_order:
        cache_order.remove(cache_key)
    cache_order.append(cache_key)


def _record_research_feature_view_cache_event(event: str, cache_key):
    stats = state.research.feature_view_cache_stats
    safe_event = str(event or '').strip().lower()
    if safe_event == 'hit':
        stats['hits'] = int(stats.get('hits') or 0) + 1
    elif safe_event == 'miss':
        stats['misses'] = int(stats.get('misses') or 0) + 1
    elif safe_event == 'store':
        stats['stores'] = int(stats.get('stores') or 0) + 1
    elif safe_event == 'evict':
        stats['evictions'] = int(stats.get('evictions') or 0) + 1
    stats['last_event'] = safe_event or None
    stats['last_key'] = str(cache_key)
    stats['last_at'] = time.time()


def _build_research_feature_view_cache_stats():
    stats = dict(state.research.feature_view_cache_stats or {})
    return {
        'hits': int(stats.get('hits') or 0),
        'misses': int(stats.get('misses') or 0),
        'stores': int(stats.get('stores') or 0),
        'evictions': int(stats.get('evictions') or 0),
        'size': len(state.research.feature_view_cache),
        'capacity': MAX_RESEARCH_FEATURE_VIEW_CACHE_ENTRIES,
        'last_event': stats.get('last_event'),
        'last_at': stats.get('last_at'),
    }


def _get_research_feature_view_cache_entry(cache_key):
    entry = state.research.feature_view_cache.get(cache_key)
    if entry is None:
        _record_research_feature_view_cache_event('miss', cache_key)
        return None
    _touch_research_feature_view_cache_key(cache_key)
    _record_research_feature_view_cache_event('hit', cache_key)
    return entry


def _store_research_feature_view_cache_entry(cache_key, entry):
    cache = state.research.feature_view_cache
    cache_order = state.research.feature_view_cache_order

    cache[cache_key] = entry
    _touch_research_feature_view_cache_key(cache_key)
    _record_research_feature_view_cache_event('store', cache_key)

    while len(cache_order) > MAX_RESEARCH_FEATURE_VIEW_CACHE_ENTRIES:
        oldest_key = cache_order.pop(0)
        if oldest_key == cache_key:
            continue
        cache.pop(oldest_key, None)
        _record_research_feature_view_cache_event('evict', oldest_key)


def ensure_market_regime_indicator_payload(indicators_payload: list[dict] | None):
    normalized = normalize_indicator_payload(indicators_payload or [])

    for indicator in normalized:
        if str(indicator.get('name') or '').strip().upper() == 'MARKETREGIME':
            return normalized

    return [
        *normalized,
        {
            'name': 'MarketRegime',
            'params': list(DEFAULT_MARKET_REGIME_PARAMS),
        },
    ]


def normalize_research_chart_context(chart_context: dict | None):
    safe_context = dict(chart_context or {})
    symbol_name = str(safe_context.get('symbol') or '').strip().upper()
    timeframe = str(safe_context.get('timeframe') or '').strip().upper()

    try:
        bars = max(1, int(safe_context.get('bars') or 1))
    except Exception:
        bars = 1

    return {
        **safe_context,
        'symbol': symbol_name,
        'timeframe': timeframe,
        'bars': bars,
        'indicators': ensure_market_regime_indicator_payload(safe_context.get('indicators') or []),
    }


def apply_indicator_payload(symbol: Symbol, indicators_payload: list[dict]):
    applied_indicators = []
    available_column_details = []

    for indicator in indicators_payload or []:
        name = indicator['name']
        params = list(indicator.get('params') or [])
        alias = str(indicator.get('alias') or '').strip()
        before_columns = list(symbol.candles.columns)

        indicator_class = get_indicator_class(name)
        if indicator_class is None:
            raise ValueError(f'Unknown indicator: {name}')

        indicator_class(symbol, *params)
        after_columns = list(symbol.candles.columns)
        created_columns = [column for column in after_columns if column not in before_columns]
        column_details = describe_indicator_columns(name, params, created_columns)
        applied_indicators.append({
            'name': name,
            'params': params,
            'alias': alias,
            'columns': created_columns,
            'column_details': column_details,
        })
        available_column_details.extend(column_details)

    return applied_indicators, available_column_details


def build_contextual_strategy_view(
    *,
    symbol_name: str,
    timeframe: str,
    bars: int,
    indicators_payload: list[dict],
    backtest_params: dict,
    should_cancel=None,
):
    safe_bars = max(1, int(bars or 1))
    if safe_bars <= 2_000:
        market_timeout_seconds = 60.0
    elif safe_bars <= 10_000:
        market_timeout_seconds = 180.0
    elif safe_bars <= 25_000:
        market_timeout_seconds = 240.0
    elif safe_bars <= 50_000:
        market_timeout_seconds = 300.0
    else:
        market_timeout_seconds = 360.0

    market_context = wait_for_market_data(
        symbol=symbol_name,
        timeframe=timeframe,
        bars=bars,
        timeout_seconds=market_timeout_seconds,
        poll_interval=0.1,
        source='strategy_timeframe_study',
        should_cancel=should_cancel,
        allow_truncated_fallback=True,
    )

    if not market_context.get('ready'):
        request_status = str(market_context.get('request_status') or '').strip()
        cache_key = str(market_context.get('cache_key') or '').strip()
        context_error = str(market_context.get('error') or '').strip()
        diagnostics = dict(market_context.get('diagnostics') or {})
        details = []
        if cache_key:
            details.append(f'cache_key={cache_key}')
        if request_status:
            details.append(f'request_status={request_status}')
        if context_error:
            details.append(f'error={context_error}')
        if diagnostics.get('request_age_seconds') is not None:
            details.append(f'request_age={diagnostics["request_age_seconds"]}s')
        if diagnostics.get('bridge_online') is False:
            details.append('bridge_online=false')
        if diagnostics.get('bridge_stale'):
            details.append('bridge_stale=true')
        if diagnostics.get('bridge_heartbeat_age_seconds') is not None:
            details.append(f'bridge_heartbeat_age={diagnostics["bridge_heartbeat_age_seconds"]}s')
        if diagnostics.get('bridge_last_status'):
            details.append(f'bridge_status={diagnostics["bridge_last_status"]}')
        if diagnostics.get('bridge_last_error'):
            details.append(f'bridge_error={diagnostics["bridge_last_error"]}')

        raise ValueError(
            f'Market data not ready for {symbol_name} {timeframe} {bars:,} bars'
            + (f' ({", ".join(details)})' if details else '.')
        )

    normalized_indicators = normalize_indicator_payload(indicators_payload or [])
    feature_cache_key = _build_research_feature_view_cache_key(
        symbol_name=symbol_name,
        timeframe=timeframe,
        bars=bars,
        indicators_payload=normalized_indicators,
        market_context=market_context,
    )
    cached_feature_view = _get_research_feature_view_cache_entry(feature_cache_key)

    if cached_feature_view is not None:
        snapshot_symbol = cached_feature_view['snapshot_symbol']
        applied_indicators = list(cached_feature_view.get('applied_indicators') or [])
        available_column_details = list(cached_feature_view.get('available_column_details') or [])
        refresh_mode = 'scoped_context_cache'
    else:
        effective_market_bars = max(
            1,
            int(market_context.get('bars_requested') or len(market_context.get('candles') or []) or bars or 1),
        )
        snapshot_symbol = Symbol(
            name=symbol_name,
            timeframe=timeframe,
            bars=effective_market_bars,
            candles=list(market_context.get('candles') or []),
            copy_candles=False,
        )
        applied_indicators, available_column_details = apply_indicator_payload(
            snapshot_symbol,
            normalized_indicators,
        )
        _store_research_feature_view_cache_entry(feature_cache_key, {
            'snapshot_symbol': snapshot_symbol,
            'applied_indicators': list(applied_indicators or []),
            'available_column_details': list(available_column_details or []),
        })
        refresh_mode = 'scoped_context'

    strategy_view = build_strategy_feature_view(
        chart_request={
            'symbol': symbol_name,
            'timeframe': timeframe,
            'bars': max(
                1,
                int(market_context.get('bars_requested') or len(market_context.get('candles') or []) or bars or 1),
            ),
        },
        snapshot_symbol=snapshot_symbol,
        applied_indicators=applied_indicators,
        available_column_details=available_column_details,
        backtest_params=backtest_params,
        snapshot_signature={
            'market_context_revision': market_context.get('revision'),
            'market_revision': None,
            'market_context_key': market_context.get('cache_key'),
            'refresh_mode': refresh_mode,
        },
    )
    strategy_view_meta = dict(strategy_view.get('meta') or {})
    strategy_view_meta['requested_market_bars'] = max(1, int(bars or 1))
    strategy_view_meta['available_market_bars'] = int(len(market_context.get('candles') or []))
    strategy_view_meta['market_context_truncated'] = bool(market_context.get('truncated'))
    strategy_view_meta['market_context_notice'] = market_context.get('notice')
    if market_context.get('truncated'):
        strategy_view_meta['market_context_truncated_from_bars'] = max(1, int(market_context.get('requested_bars_original') or bars or 1))
        strategy_view_meta['market_context_truncated_to_bars'] = max(
            1,
            int(market_context.get('bars_requested') or len(market_context.get('candles') or []) or 1),
        )
    strategy_view_meta['research_feature_cache'] = {
        'status': 'hit' if cached_feature_view is not None else 'miss',
        'stats': _build_research_feature_view_cache_stats(),
    }
    strategy_view['meta'] = strategy_view_meta
    return strategy_view


def _merge_available_column_details(detail_groups: list[list[dict]] | None = None):
    merged = []
    seen = set()
    for detail_group in detail_groups or []:
        for detail in detail_group or []:
            if not isinstance(detail, dict):
                continue
            key = str(
                detail.get('normalized_column_name')
                or detail.get('column_name')
                or detail.get('id')
                or ''
            ).strip()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(dict(detail))
    return merged


def _build_group_trade_markers_with_market_prefix(backtester, symbol_name: str, timeframe: str):
    return [dict(marker or {}) for marker in list(getattr(backtester, 'trade_markers', []) or [])]


def _build_grouped_strategy_evaluation(
    *,
    payload: ApplyStrategyRequest,
    strategy_bundle: dict,
    backtest_params: dict,
    default_symbol_name: str,
    default_timeframe: str,
    bars: int,
    indicators_payload: list[dict],
    should_cancel=None,
):
    grouped_runtime_entries = {}
    grouped_order = []
    grouped_strategy_views = {}
    safe_indicator_payload = normalize_indicator_payload(list(indicators_payload or []))

    for runtime_entry in list(strategy_bundle.get('entries') or []):
        symbol_name, timeframe = resolve_strategy_entry_market_context(
            runtime_entry,
            default_symbol=default_symbol_name,
            default_timeframe=default_timeframe,
        )
        group_key = (symbol_name, timeframe)
        if group_key not in grouped_runtime_entries:
            grouped_order.append(group_key)
            grouped_runtime_entries[group_key] = []
            grouped_strategy_views[group_key] = build_contextual_strategy_view(
                symbol_name=symbol_name,
                timeframe=timeframe,
                bars=bars,
                indicators_payload=safe_indicator_payload,
                backtest_params=backtest_params,
                should_cancel=should_cancel,
            )
        grouped_runtime_entries[group_key].append(runtime_entry)

    if not grouped_order:
        raise ValueError('No enabled strategy entries were available for grouped evaluation.')

    grouped_runs = []
    merged_available_columns = []
    merged_column_details = []
    merged_applied_indicators = []
    seen_available_columns = set()
    seen_indicator_signatures = set()
    primary_group_key = None
    requested_group_key = (
        str(default_symbol_name or '').strip().upper() or 'EURUSD',
        str(default_timeframe or '').strip().upper() or 'M1',
    )

    for group_key in grouped_order:
        symbol_name, timeframe = group_key
        strategy_view = grouped_strategy_views[group_key]
        available_columns = list(strategy_view.get('available_columns') or [])
        applied_indicators = list(strategy_view.get('applied_indicators') or [])
        available_column_details = list(strategy_view.get('available_column_details') or [])
        group_entries = list(grouped_runtime_entries[group_key] or [])

        if primary_group_key is None and (
            group_key == requested_group_key
            or any(str(entry.get('strategy_id') or '') == 'primary' for entry in group_entries)
        ):
            primary_group_key = group_key

        for column_name in available_columns:
            normalized_name = normalize_indicator_feature_name(column_name) or str(column_name or '').strip()
            if normalized_name and normalized_name not in seen_available_columns:
                seen_available_columns.add(normalized_name)
                merged_available_columns.append(column_name)

        merged_column_details.extend(available_column_details)
        for indicator in applied_indicators:
            signature = str(indicator)
            if signature in seen_indicator_signatures:
                continue
            seen_indicator_signatures.add(signature)
            merged_applied_indicators.append(indicator)

        for runtime_entry in group_entries:
            resolved_strategy_params = resolve_strategy_param_aliases(
                runtime_entry['strategy_params'],
                applied_indicators,
            )
            validate_strategy_expressions(
                strategy_params=resolved_strategy_params,
                available_columns=available_columns,
            )
            runtime_entry['strategy'].set_params(
                **resolved_strategy_params,
                execution_mode=backtest_params['execution_mode'],
            )

        if strategy_bundle['is_multi'] or len(group_entries) > 1:
            backtester = MultiStrategyBacktester(
                strategy_view['symbol'],
                [build_backtester_runtime_entry(runtime_entry) for runtime_entry in group_entries],
                portfolio_mode=backtest_params['portfolio_mode'],
            )
            backtester.set_params(**backtest_params)
            results = backtester.run()
        else:
            runtime_entry = group_entries[0]
            backtester = Backtester(strategy_view['symbol'], runtime_entry['strategy'])
            backtester.set_params(**backtest_params)
            results = backtester.run()

        grouped_runs.append({
            'symbol': symbol_name,
            'timeframe': timeframe,
            'market_label': f'{symbol_name} {timeframe}',
            'entries': group_entries,
            'strategy_view': strategy_view,
            'backtester': backtester,
            'results': results,
            'trade_markers': _build_group_trade_markers_with_market_prefix(backtester, symbol_name, timeframe),
        })

    if primary_group_key is None:
        primary_group_key = grouped_order[0]

    primary_strategy_view = grouped_strategy_views.get(primary_group_key) or grouped_strategy_views[grouped_order[0]]
    primary_group_run = next(
        (item for item in grouped_runs if (item.get('symbol'), item.get('timeframe')) == primary_group_key),
        grouped_runs[0],
    )
    grouped_column_details = _merge_available_column_details([merged_column_details])
    backtester = PortfolioStackBacktester(grouped_runs, portfolio_mode=backtest_params['portfolio_mode'])
    backtester.set_params(**backtest_params)
    results = backtester.run()
    strategy_view_meta = dict(primary_strategy_view.get('meta') or {})
    strategy_view_meta['portfolio_contexts'] = [
        {
            'symbol': group_run.get('symbol'),
            'timeframe': group_run.get('timeframe'),
            'strategy_count': len(list(group_run.get('entries') or [])),
        }
        for group_run in grouped_runs
    ]
    strategy_view_meta['market_group_count'] = len(grouped_runs)
    strategy_view_meta['portfolio_structure'] = 'multi_market_stack'

    return {
        'status': 'ok',
        'symbol': primary_strategy_view['symbol'],
        'results': results,
        'strategy': strategy_bundle['primary_strategy'],
        'backtester': backtester,
        'stats': sanitize_dict_values(dict(backtester.stats)),
        'trade_markers': sanitize_value(list(backtester.trade_markers)),
        'available_columns': merged_available_columns,
        'available_column_details': grouped_column_details,
        'required_features': strategy_bundle['required_features'],
        'required_feature_details': strategy_bundle['required_feature_details'],
        'serialized_results': serialize_results(results),
        'strategy_view_meta': strategy_view_meta,
        'applied_indicators': merged_applied_indicators,
        'history_scope_info': dict(primary_strategy_view.get('history_scope_info') or {}),
    }


def evaluate_strategy_request_in_context(
    *,
    payload: ApplyStrategyRequest,
    symbol_name: str,
    timeframe: str,
    bars: int,
    indicators_payload: list[dict],
    should_cancel=None,
):
    try:
        strategy_bundle = build_runtime_strategy_bundle(payload)
        backtest_params = build_backtest_params(
            payload.backtest,
            capital_model=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
        )
        if strategy_bundle_uses_nondefault_market_context(
            strategy_bundle,
            default_symbol=str(symbol_name or '').strip().upper() or 'EURUSD',
            default_timeframe=str(timeframe or '').strip().upper() or 'M1',
        ):
            return _build_grouped_strategy_evaluation(
                payload=payload,
                strategy_bundle=strategy_bundle,
                backtest_params=backtest_params,
                default_symbol_name=symbol_name,
                default_timeframe=timeframe,
                bars=bars,
                indicators_payload=indicators_payload,
                should_cancel=should_cancel,
            )
        strategy_view = build_contextual_strategy_view(
            symbol_name=symbol_name,
            timeframe=timeframe,
            bars=bars,
            indicators_payload=indicators_payload,
            backtest_params=backtest_params,
            should_cancel=should_cancel,
        )
        symbol = strategy_view['symbol']
        available_columns = list(strategy_view['available_columns'])

        for runtime_entry in strategy_bundle['entries']:
            resolved_strategy_params = resolve_strategy_param_aliases(
                runtime_entry['strategy_params'],
                strategy_view['applied_indicators'],
            )
            validate_strategy_expressions(
                strategy_params=resolved_strategy_params,
                available_columns=available_columns,
            )
            runtime_entry['strategy'].set_params(
                **resolved_strategy_params,
                execution_mode=backtest_params['execution_mode'],
            )

        if strategy_bundle['is_multi']:
            backtester = MultiStrategyBacktester(
                symbol,
                [build_backtester_runtime_entry(runtime_entry) for runtime_entry in strategy_bundle['entries']],
                portfolio_mode=backtest_params['portfolio_mode'],
            )
            backtester.set_params(**backtest_params)
            results = backtester.run()
            strategy = strategy_bundle['primary_strategy']
        else:
            strategy = strategy_bundle['entries'][0]['strategy']
            backtester = Backtester(symbol, strategy)
            backtester.set_params(**backtest_params)
            results = backtester.run()

        return {
            'status': 'ok',
            'symbol': symbol,
            'results': results,
            'strategy': strategy,
            'backtester': backtester,
            'stats': sanitize_dict_values(dict(backtester.stats)),
            'trade_markers': sanitize_value(list(backtester.trade_markers)),
            'available_columns': available_columns,
            'available_column_details': list(strategy_view['available_column_details']),
            'required_features': strategy_bundle['required_features'],
            'required_feature_details': strategy_bundle['required_feature_details'],
            'serialized_results': serialize_results(results),
            'strategy_view_meta': dict(strategy_view.get('meta') or {}),
            'applied_indicators': list(strategy_view['applied_indicators']),
            'history_scope_info': dict(strategy_view.get('history_scope_info') or {}),
        }
    except Exception as error:
        return {
            'status': 'error',
            'error': str(error),
            **build_runtime_payload(),
        }


def _build_backtest_job_payload(job: dict | None, *, include_result: bool = False):
    safe_job = dict(job or {})
    payload = {
        'id': str(safe_job.get('id') or '').strip(),
        'status': str(safe_job.get('status') or 'queued').strip() or 'queued',
        'progress': float(safe_job.get('progress') or 0.0),
        'phase': str(safe_job.get('phase') or '').strip(),
        'phase_label': str(safe_job.get('phase_label') or '').strip(),
        'detail': str(safe_job.get('detail') or '').strip(),
        'created_at': safe_job.get('created_at'),
        'started_at': safe_job.get('started_at'),
        'finished_at': safe_job.get('finished_at'),
        'cancel_requested': bool(safe_job.get('cancel_requested')),
        'error': str(safe_job.get('error') or '').strip(),
    }
    if include_result:
        payload['result'] = _summarize_backtest_job_result_payload(safe_job.get('result'))
    return payload


def _coerce_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _downsample_backtest_job_series(values, max_points=BACKTEST_JOB_RESPONSE_MAX_SERIES_POINTS):
    if not isinstance(values, list):
        return values
    if len(values) <= max_points:
        return list(values)
    if max_points <= 2:
        return [values[0], values[-1]]

    step = (len(values) - 1) / float(max_points - 1)
    indexes = {
        0,
        len(values) - 1,
    }
    for point_index in range(1, max_points - 1):
        indexes.add(int(round(point_index * step)))
    ordered_indexes = sorted(indexes)
    return [values[index] for index in ordered_indexes]


def _is_backtest_job_event_row(row):
    if not isinstance(row, dict):
        return False
    for key in BACKTEST_JOB_EVENT_FLAG_KEYS:
        try:
            if int(row.get(key) or 0) == 1:
                return True
        except (TypeError, ValueError):
            continue
    for key in BACKTEST_JOB_EVENT_VALUE_KEYS:
        number = _coerce_float(row.get(key))
        if number is not None and abs(number) > 1e-12:
            return True
    return False


def _summarize_backtest_job_rows(rows):
    if not isinstance(rows, list):
        return []
    event_rows = [dict(row) for row in rows if _is_backtest_job_event_row(row)]
    if len(event_rows) <= BACKTEST_JOB_RESPONSE_MAX_RESULT_ROWS:
        return event_rows
    return event_rows[-BACKTEST_JOB_RESPONSE_MAX_RESULT_ROWS:]


def _summarize_backtest_job_stats(stats):
    if not isinstance(stats, dict):
        return stats
    summarized = dict(stats)
    for key in BACKTEST_JOB_SERIES_KEYS:
        if isinstance(summarized.get(key), list):
            summarized[key] = _downsample_backtest_job_series(summarized[key])
    return summarized


def _summarize_backtest_job_result_payload(result):
    if not isinstance(result, dict):
        return result
    summarized = dict(result)
    original_rows = summarized.get('results')
    summarized['stats'] = _summarize_backtest_job_stats(summarized.get('stats') or {})
    summarized['results'] = _summarize_backtest_job_rows(original_rows)
    summarized['trade_markers'] = list(summarized.get('trade_markers') or [])
    summarized['applied_indicators'] = list(summarized.get('applied_indicators') or [])
    summarized['available_columns'] = list(summarized.get('available_columns') or [])
    summarized['available_column_details'] = list(summarized.get('available_column_details') or [])
    summarized['summary_only'] = True
    summarized['results_truncated'] = isinstance(original_rows, list) and len(summarized['results']) != len(original_rows)
    summarized['full_result_rows'] = len(original_rows) if isinstance(original_rows, list) else 0
    return summarized


def _purge_expired_runtime_backtest_jobs(*, now: float | None = None):
    runtime = state.backtest_jobs
    safe_now = float(now) if now is not None else time.time()
    removed_ids = []

    for job_id, job in list(runtime.jobs.items()):
        status = str((job or {}).get('status') or '').strip().lower()
        if status not in BACKTEST_JOB_TERMINAL_STATUSES:
            continue
        finished_at = job.get('finished_at')
        try:
            finished_at_value = float(finished_at)
        except (TypeError, ValueError):
            continue
        if safe_now - finished_at_value < BACKTEST_JOB_TERMINAL_RETENTION_SECONDS:
            continue
        runtime.jobs.pop(job_id, None)
        runtime.job_threads.pop(job_id, None)
        removed_ids.append(job_id)

    return removed_ids


def _persist_backtest_job_snapshot(job: dict | None):
    safe_job = dict(job or {})
    safe_job_id = str(safe_job.get('id') or '').strip()
    workspace_user_id = str(safe_job.get('workspace_user_id') or '').strip()
    workspace_id = str(safe_job.get('workspace_id') or '').strip() or 'default'
    if not safe_job_id or not workspace_user_id:
        return None

    return update_workspace_backtest_job(
        workspace_user_id,
        workspace_id,
        safe_job_id,
        status=str(safe_job.get('status') or 'queued').strip() or 'queued',
        progress=float(safe_job.get('progress') or 0.0),
        phase=str(safe_job.get('phase') or '').strip(),
        phase_label=str(safe_job.get('phase_label') or '').strip(),
        detail=str(safe_job.get('detail') or '').strip(),
        error=str(safe_job.get('error') or '').strip(),
        cancel_requested=bool(safe_job.get('cancel_requested')),
        result=_summarize_backtest_job_result_payload(safe_job.get('result')) if safe_job.get('result') is not None else None,
        started_at=safe_job.get('started_at'),
        finished_at=safe_job.get('finished_at'),
    )


def _load_backtest_job_from_store(user_id: str, workspace_id: str, job_id: str):
    safe_job_id = str(job_id or '').strip()
    if not safe_job_id:
        return None

    purge_expired_workspace_backtest_jobs(user_id, workspace_id)
    persisted_job = get_workspace_backtest_job(user_id, workspace_id, safe_job_id)
    if not persisted_job:
        return None

    persisted_status = str(persisted_job.get('status') or '').strip().lower()
    if persisted_status not in {'queued', 'running'}:
        return persisted_job

    return update_workspace_backtest_job(
        user_id,
        workspace_id,
        safe_job_id,
        status='failed',
        progress=max(float(persisted_job.get('progress') or 0.0), 1.0),
        phase='failed',
        phase_label='Failed',
        detail=BACKTEST_JOB_INTERRUPTED_ERROR,
        error=BACKTEST_JOB_INTERRUPTED_ERROR,
        finished_at=time.time(),
    )


def _update_backtest_job(job_id: str, **updates):
    runtime = state.backtest_jobs
    job = runtime.jobs.get(job_id)
    if not job:
        return None
    if updates.get('result') is not None:
        updates['result'] = _summarize_backtest_job_result_payload(updates.get('result'))
    job.update(updates)
    if updates.get('status') in {'completed', 'failed', 'cancelled'}:
        job['finished_at'] = updates.get('finished_at') or time.time()
    runtime.last_job_id = job_id
    runtime.last_run_at = time.time()
    if updates.get('error'):
        runtime.last_error = str(updates.get('error') or '').strip()
    _persist_backtest_job_snapshot(job)
    return job


def _is_backtest_job_cancel_requested(job_id: str):
    job = state.backtest_jobs.jobs.get(job_id) or {}
    return bool(job.get('cancel_requested'))


def _run_backtest_job(job_id: str, request_payload: dict):
    runtime = state.backtest_jobs
    started_at = time.time()
    _update_backtest_job(
        job_id,
        status='running',
        progress=0.05,
        phase='preparing_market',
        phase_label='Preparing market',
        detail='Preparing isolated market context for the backtest.',
        started_at=started_at,
        error='',
    )

    try:
        payload = ApplyStrategyInContextRequest.model_validate(request_payload or {})
        if _is_backtest_job_cancel_requested(job_id):
            _update_backtest_job(
                job_id,
                status='cancelled',
                progress=0.0,
                phase='cancelled',
                phase_label='Cancelled',
                detail='Backtest was cancelled before execution started.',
                finished_at=time.time(),
            )
            return

        _update_backtest_job(
            job_id,
            progress=0.2,
            phase='running_backtest',
            phase_label='Running backtest',
            detail='Executing strategy on the isolated market snapshot.',
        )

        evaluation = evaluate_strategy_request_in_context(
            payload=ApplyStrategyRequest(
                strategy=payload.strategy,
                strategies=list(payload.strategies or []),
                portfolioStructureVersion=payload.portfolioStructureVersion,
                capitalModel=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
                portfolios=list(payload.portfolios or []),
                backtest=payload.backtest,
            ),
            symbol_name=str(payload.symbol or 'EURUSD').strip().upper() or 'EURUSD',
            timeframe=str(payload.timeframe or 'M1').strip().upper() or 'M1',
            bars=max(1, int(payload.bars or 1)),
            indicators_payload=normalize_indicator_payload(list(payload.indicators or [])),
            should_cancel=lambda: _is_backtest_job_cancel_requested(job_id),
        )

        if _is_backtest_job_cancel_requested(job_id):
            _update_backtest_job(
                job_id,
                status='cancelled',
                progress=0.0,
                phase='cancelled',
                phase_label='Cancelled',
                detail='Backtest was cancelled.',
                finished_at=time.time(),
            )
            return

        if evaluation.get('status') != 'ok':
            _update_backtest_job(
                job_id,
                status='failed',
                progress=1.0,
                phase='failed',
                phase_label='Failed',
                detail='Backtest did not complete successfully.',
                error=str(evaluation.get('error') or '').strip(),
                result=evaluation,
                finished_at=time.time(),
            )
            return

        result_payload = build_isolated_backtest_response(
            request_payload=build_strategy_request_payload(
                ApplyStrategyRequest(
                    strategy=payload.strategy,
                    strategies=list(payload.strategies or []),
                    portfolioStructureVersion=payload.portfolioStructureVersion,
                    capitalModel=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
                    portfolios=list(payload.portfolios or []),
                    backtest=payload.backtest,
                ),
                extra_fields={
                    'symbol': str(payload.symbol or 'EURUSD').strip().upper() or 'EURUSD',
                    'timeframe': str(payload.timeframe or 'M1').strip().upper() or 'M1',
                    'bars': max(1, int(payload.bars or 1)),
                    'indicators': normalize_indicator_payload(list(payload.indicators or [])),
                },
            ),
            evaluation=evaluation,
        )

        _update_backtest_job(
            job_id,
            status='completed',
            progress=1.0,
            phase='completed',
            phase_label='Completed',
            detail='Backtest completed in the backend job runner.',
            result=result_payload,
            finished_at=time.time(),
        )
    except Exception as error:
        _update_backtest_job(
            job_id,
            status='failed',
            progress=1.0,
            phase='failed',
            phase_label='Failed',
            detail='Backtest job crashed unexpectedly.',
            error=str(error),
            finished_at=time.time(),
        )
    finally:
        runtime.job_threads.pop(job_id, None)


def _parse_backtest_job_status_filters(raw_value: str | None):
    if raw_value is None:
        return []
    return [
        status
        for status in (
            str(part or '').strip().lower()
            for part in str(raw_value or '').split(',')
        )
        if status
    ]


@router.post('/strategy/backtest-jobs')
async def create_backtest_job(
    payload: ApplyStrategyInContextRequest,
    request: Request,
):
    auth_user = require_request_auth(request)
    workspace_user_id = auth_user['workspace_user_id']
    workspace_id = 'default'
    purge_expired_workspace_backtest_jobs(workspace_user_id, workspace_id)
    _purge_expired_runtime_backtest_jobs()
    runtime = state.backtest_jobs
    runtime.sequence += 1
    job_id = f'btjob_{runtime.sequence}_{uuid.uuid4().hex[:8]}'
    created_at = time.time()
    serialized_request = build_strategy_request_payload(
        ApplyStrategyRequest(
            strategy=payload.strategy,
            strategies=list(payload.strategies or []),
            portfolioStructureVersion=payload.portfolioStructureVersion,
            capitalModel=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
            portfolios=list(payload.portfolios or []),
            backtest=payload.backtest,
        ),
        extra_fields={
            'symbol': str(payload.symbol or 'EURUSD').strip().upper() or 'EURUSD',
            'timeframe': str(payload.timeframe or 'M1').strip().upper() or 'M1',
            'bars': max(1, int(payload.bars or 1)),
            'indicators': normalize_indicator_payload(list(payload.indicators or [])),
        },
    )
    runtime.jobs[job_id] = {
        'id': job_id,
        'status': 'queued',
        'progress': 0.0,
        'phase': 'queued',
        'phase_label': 'Queued',
        'detail': 'Backtest job queued.',
        'created_at': created_at,
        'started_at': None,
        'finished_at': None,
        'cancel_requested': False,
        'error': '',
        'workspace_user_id': workspace_user_id,
        'workspace_id': workspace_id,
        'request': serialized_request,
        'result': None,
    }
    create_workspace_backtest_job(
        workspace_user_id,
        workspace_id,
        job_id=job_id,
        request=serialized_request,
        created_at=created_at,
    )
    worker = threading.Thread(
        target=_run_backtest_job,
        args=(job_id, serialized_request),
        daemon=True,
        name=f'robotineeko-backtest-{job_id}',
    )
    runtime.job_threads[job_id] = worker
    runtime.last_job_id = job_id
    worker.start()
    return {
        'status': 'ok',
        'job': _build_backtest_job_payload(runtime.jobs.get(job_id), include_result=False),
    }


@router.get('/strategy/backtest-jobs/latest')
async def get_latest_backtest_job(
    request: Request,
    prefer_job_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    auth_user = require_request_auth(request)
    workspace_user_id = auth_user['workspace_user_id']
    workspace_id = 'default'
    purge_expired_workspace_backtest_jobs(workspace_user_id, workspace_id)
    _purge_expired_runtime_backtest_jobs()

    status_filters = set(_parse_backtest_job_status_filters(status))
    safe_prefer_job_id = str(prefer_job_id or '').strip()

    if safe_prefer_job_id:
        preferred_job = state.backtest_jobs.jobs.get(safe_prefer_job_id)
        if preferred_job:
            preferred_status = str(preferred_job.get('status') or '').strip().lower()
            if not status_filters or preferred_status in status_filters:
                return {
                    'status': 'ok',
                    'job': _build_backtest_job_payload(preferred_job, include_result=True),
                }
        preferred_persisted_job = _load_backtest_job_from_store(workspace_user_id, workspace_id, safe_prefer_job_id)
        if preferred_persisted_job:
            preferred_status = str(preferred_persisted_job.get('status') or '').strip().lower()
            if not status_filters or preferred_status in status_filters:
                return {
                    'status': 'ok',
                    'job': _build_backtest_job_payload(preferred_persisted_job, include_result=True),
                }

    runtime_candidates = []
    for job in state.backtest_jobs.jobs.values():
        if str(job.get('workspace_user_id') or '').strip() != workspace_user_id:
            continue
        if str(job.get('workspace_id') or '').strip() != workspace_id:
            continue
        job_status = str(job.get('status') or '').strip().lower()
        if status_filters and job_status not in status_filters:
            continue
        runtime_candidates.append(job)

    if runtime_candidates:
        latest_runtime_job = max(
            runtime_candidates,
            key=lambda entry: (
                float(entry.get('created_at') or 0.0),
                float(entry.get('started_at') or 0.0),
                str(entry.get('id') or ''),
            ),
        )
        return {
            'status': 'ok',
            'job': _build_backtest_job_payload(latest_runtime_job, include_result=True),
        }

    latest_jobs = list_workspace_backtest_jobs(
        workspace_user_id,
        workspace_id,
        limit=1,
        statuses=status_filters or None,
    )
    if not latest_jobs:
        return {
            'status': 'error',
            'error': 'No backtest jobs were found for this workspace.',
        }

    latest_job_id = str(latest_jobs[0].get('id') or '').strip()
    latest_persisted_job = get_workspace_backtest_job(workspace_user_id, workspace_id, latest_job_id)
    if not latest_persisted_job:
        return {
            'status': 'error',
            'error': 'The latest workspace backtest job could not be loaded.',
        }

    return {
        'status': 'ok',
        'job': _build_backtest_job_payload(latest_persisted_job, include_result=True),
    }


@router.get('/strategy/backtest-jobs/{job_id}')
async def get_backtest_job(
    job_id: str,
    request: Request,
):
    auth_user = require_request_auth(request)
    workspace_user_id = auth_user['workspace_user_id']
    workspace_id = 'default'
    purge_expired_workspace_backtest_jobs(workspace_user_id, workspace_id)
    _purge_expired_runtime_backtest_jobs()
    safe_job_id = str(job_id or '').strip()
    job = state.backtest_jobs.jobs.get(safe_job_id)
    if not job:
        persisted_job = _load_backtest_job_from_store(workspace_user_id, workspace_id, safe_job_id)
        if not persisted_job:
            return {
                'status': 'error',
                'error': f'Backtest job {job_id} was not found.',
            }
        return {
            'status': 'ok',
            'job': _build_backtest_job_payload(persisted_job, include_result=True),
        }
    return {
        'status': 'ok',
        'job': _build_backtest_job_payload(job, include_result=True),
    }


@router.post('/strategy/backtest-jobs/{job_id}/cancel')
async def cancel_backtest_job(
    job_id: str,
    request: Request,
):
    auth_user = require_request_auth(request)
    workspace_user_id = auth_user['workspace_user_id']
    workspace_id = 'default'
    purge_expired_workspace_backtest_jobs(workspace_user_id, workspace_id)
    _purge_expired_runtime_backtest_jobs()
    safe_job_id = str(job_id or '').strip()
    job = state.backtest_jobs.jobs.get(safe_job_id)
    if not job:
        persisted_job = _load_backtest_job_from_store(workspace_user_id, workspace_id, safe_job_id)
        if not persisted_job:
            return {
                'status': 'error',
                'error': f'Backtest job {job_id} was not found.',
            }
        if str(persisted_job.get('status') or '').strip().lower() in {'completed', 'failed', 'cancelled'}:
            return {
                'status': 'ok',
                'job': _build_backtest_job_payload(persisted_job, include_result=False),
            }
        job = {
            **persisted_job,
            'workspace_user_id': workspace_user_id,
            'workspace_id': workspace_id,
        }
        state.backtest_jobs.jobs[safe_job_id] = job
    job['cancel_requested'] = True
    _persist_backtest_job_snapshot(job)
    if str(job.get('status') or '').strip() == 'queued':
        _update_backtest_job(
            safe_job_id,
            status='cancelled',
            progress=0.0,
            phase='cancelled',
            phase_label='Cancelled',
            detail='Backtest job cancelled before start.',
            finished_at=time.time(),
        )
    return {
        'status': 'ok',
        'job': _build_backtest_job_payload(state.backtest_jobs.jobs.get(safe_job_id), include_result=False),
    }


def evaluate_strategy_request_on_segment(
    *,
    payload: ApplyStrategyRequest,
    strategy_view: dict,
    symbol_name: str,
    timeframe: str,
    segment_start_index: int,
    segment_bars: int,
):
    try:
        strategy_bundle = build_runtime_strategy_bundle(payload)
        backtest_params = build_backtest_params(
            payload.backtest,
            capital_model=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
        )
        full_symbol = strategy_view['symbol']
        total_bars = len(full_symbol.candles.index)
        safe_start = max(0, min(int(segment_start_index or 0), max(0, total_bars - 1)))
        safe_end = max(safe_start + 1, min(total_bars, safe_start + max(1, int(segment_bars or 1))))
        segment_candles = full_symbol.candles.iloc[safe_start:safe_end].copy()
        segment_symbol = Symbol(
            name=symbol_name,
            timeframe=timeframe,
            bars=len(segment_candles.index),
            candles=segment_candles,
            copy_candles=False,
        )
        available_columns = list(segment_symbol.candles.columns)

        for runtime_entry in strategy_bundle['entries']:
            resolved_strategy_params = resolve_strategy_param_aliases(
                runtime_entry['strategy_params'],
                strategy_view.get('applied_indicators') or [],
            )
            validate_strategy_expressions(
                strategy_params=resolved_strategy_params,
                available_columns=available_columns,
            )
            runtime_entry['strategy'].set_params(
                **resolved_strategy_params,
                execution_mode=backtest_params['execution_mode'],
            )

        if strategy_bundle['is_multi']:
            backtester = MultiStrategyBacktester(
                segment_symbol,
                [build_backtester_runtime_entry(runtime_entry) for runtime_entry in strategy_bundle['entries']],
                portfolio_mode=backtest_params['portfolio_mode'],
            )
            backtester.set_params(**backtest_params)
            results = backtester.run()
            strategy = strategy_bundle['primary_strategy']
        else:
            strategy = strategy_bundle['entries'][0]['strategy']
            backtester = Backtester(segment_symbol, strategy)
            backtester.set_params(**backtest_params)
            results = backtester.run()

        return {
            'status': 'ok',
            'symbol': segment_symbol,
            'results': results,
            'strategy': strategy,
            'backtester': backtester,
            'stats': sanitize_dict_values(dict(backtester.stats)),
            'available_columns': available_columns,
            'serialized_results': serialize_results(results),
            'segment': {
                'start_index': safe_start,
                'end_index': safe_end,
                'bars': int(len(segment_candles.index)),
            },
        }
    except Exception as error:
        return {
            'status': 'error',
            'error': str(error),
            **build_runtime_payload(),
        }


def build_walkforward_segments(total_bars: int, window_bars: int, step_bars: int):
    safe_total = max(0, int(total_bars or 0))
    safe_window = max(1, int(window_bars or 1))
    safe_step = max(1, int(step_bars or safe_window))

    if safe_total <= 0:
        return []

    segments = []
    start_index = 0
    while start_index + safe_window <= safe_total:
        end_index = start_index + safe_window
        segments.append({
            'start_index': start_index,
            'end_index': end_index,
            'bars': safe_window,
            'label': f'{start_index + 1:,}-{end_index:,}',
        })
        start_index += safe_step

    if not segments:
        segments.append({
            'start_index': 0,
            'end_index': safe_total,
            'bars': safe_total,
            'label': f'1-{safe_total:,}',
        })

    return segments


def build_walkforward_train_test_pairs(total_bars: int, train_bars: int, test_bars: int, step_bars: int):
    safe_total = max(0, int(total_bars or 0))
    safe_train = max(1, int(train_bars or 1))
    safe_test = max(1, int(test_bars or 1))
    safe_step = max(1, int(step_bars or safe_test))

    if safe_total <= 0:
        return []

    pairs = []
    start_index = 0
    while start_index + safe_train + safe_test <= safe_total:
        train_end_index = start_index + safe_train
        test_end_index = train_end_index + safe_test
        pairs.append({
            'train_start_index': start_index,
            'train_end_index': train_end_index,
            'train_bars': safe_train,
            'test_start_index': train_end_index,
            'test_end_index': test_end_index,
            'test_bars': safe_test,
            'label': f'train {start_index + 1:,}-{train_end_index:,} · test {train_end_index + 1:,}-{test_end_index:,}',
        })
        start_index += safe_step

    if not pairs and safe_total >= (safe_train + safe_test):
        train_end_index = safe_train
        test_end_index = safe_train + safe_test
        pairs.append({
            'train_start_index': 0,
            'train_end_index': train_end_index,
            'train_bars': safe_train,
            'test_start_index': train_end_index,
            'test_end_index': test_end_index,
            'test_bars': safe_test,
            'label': f'train 1-{train_end_index:,} · test {train_end_index + 1:,}-{test_end_index:,}',
        })

    return pairs


def refresh_stale_strategy_if_needed():
    strategy_state = state.strategy

    if not strategy_state.backtest_active or not strategy_state.is_stale or not strategy_state.request:
        return None

    backtest_request = (strategy_state.request or {}).get('backtest') or {}
    history_scope_mode = str(backtest_request.get('historyScopeMode') or backtest_request.get('history_scope_mode') or 'loaded_chart').strip().lower()
    if history_scope_mode == 'custom':
        payload = ApplyStrategyRequest.model_validate(strategy_state.request)
        refreshed = run_strategy_request(payload)
        if refreshed.get('status') == 'ok':
            refreshed['refresh_mode'] = 'full'
        return refreshed

    chart_state = state.chart
    affected_from_index = state.market.affected_from_index

    if (
        strategy_state.backtester is not None
        and strategy_state.strategy is not None
        and strategy_state.backtester.execution is not None
    ):
        try:
            started_at = time.perf_counter()
            ensure_chart_snapshot()
            snapshot_symbol = chart_state.snapshot_symbol

            if snapshot_symbol is None:
                raise ValueError('Chart snapshot is not available')

            strategy_view = build_strategy_feature_view(
                chart_request=chart_state.request,
                snapshot_symbol=snapshot_symbol,
                applied_indicators=chart_state.snapshot_applied_indicators,
                available_column_details=chart_state.snapshot_available_column_details,
                backtest_params=build_backtest_params(
                    BacktestPayload.model_validate((strategy_state.request or {}).get('backtest') or {})
                ),
                snapshot_signature=chart_state.snapshot_signature,
            )
            symbol = strategy_view['symbol']

            strategy_state.symbol = symbol
            strategy_state.backtester.symbol = symbol
            strategy_state.strategy.symbol = symbol

            rerun_from_index = max(0, int(affected_from_index or 0))
            results = strategy_state.backtester.run_from(
                start_index=rerun_from_index,
                previous_execution=strategy_state.backtester.execution,
            )

            strategy_state.results = results
            strategy_state.stats = sanitize_dict_values(dict(strategy_state.backtester.stats))
            strategy_state.applied_indicators = list(strategy_view['applied_indicators'])
            strategy_state.available_columns = list(strategy_view['available_columns'])
            strategy_state.available_column_details = list(strategy_view['available_column_details'])
            strategy_state.strategy_view_meta = dict(strategy_view.get('meta') or {})
            strategy_state.trade_markers = sanitize_value(list(strategy_state.backtester.trade_markers))
            strategy_state.last_applied_at = time.time()
            strategy_state.last_results_generated_at = strategy_state.last_applied_at
            strategy_state.last_invalidated_reason = None
            strategy_state.last_invalidated_overlap = []
            strategy_state.is_stale = False
            strategy_state.stale_reason = None
            strategy_state.stale_overlap = []
            strategy_state.last_refresh_mode = 'partial'
            strategy_state.last_refresh_from_index = rerun_from_index

            serialized_results = serialize_results(results)
            _record_strategy_refresh(
                mode='partial',
                reason='refresh_stale_strategy_partial',
                started_at=started_at,
                rows=len(serialized_results),
                stats=strategy_state.stats,
            )
            persist_strategy_runtime_if_configured()

            return {
                'status': 'ok',
                'request': strategy_state.request,
                **build_runtime_payload(),
                'rows': len(serialized_results),
                'results': serialized_results,
                'rerun_from_index': rerun_from_index,
                'refresh_mode': 'partial',
            }
        except Exception:
            pass

    payload = ApplyStrategyRequest.model_validate(strategy_state.request)
    started_at = time.perf_counter()
    refreshed = run_strategy_request(payload)
    if refreshed.get('status') == 'ok':
        refreshed['refresh_mode'] = 'full'
    return refreshed


@router.post('/strategy/apply')
async def apply_strategy(
    payload: ApplyStrategyRequest,
    request: Request,
    source: str = Query(default='api'),
):
    auth_user = require_request_auth(request)
    strategy_state = state.strategy
    strategy_state.workspace_user_id = auth_user['workspace_user_id']
    strategy_state.workspace_id = 'default'
    response = run_strategy_request(payload)
    if response.get('status') == 'ok':
        await broadcast_strategy_event('strategy.updated', source=source)
    return response


@router.post('/strategy/apply-in-context')
async def apply_strategy_in_context(
    payload: ApplyStrategyInContextRequest,
    request: Request,
):
    require_request_auth(request)
    evaluation = evaluate_strategy_request_in_context(
        payload=ApplyStrategyRequest(
            strategy=payload.strategy,
            strategies=list(payload.strategies or []),
            portfolioStructureVersion=payload.portfolioStructureVersion,
            capitalModel=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
            portfolios=list(payload.portfolios or []),
            backtest=payload.backtest,
        ),
        symbol_name=str(payload.symbol or 'EURUSD').strip().upper() or 'EURUSD',
        timeframe=str(payload.timeframe or 'M1').strip().upper() or 'M1',
        bars=max(1, int(payload.bars or 1)),
        indicators_payload=normalize_indicator_payload(list(payload.indicators or [])),
    )

    if evaluation.get('status') != 'ok':
        return evaluation

    request_payload = build_strategy_request_payload(
        ApplyStrategyRequest(
            strategy=payload.strategy,
            strategies=list(payload.strategies or []),
            portfolioStructureVersion=payload.portfolioStructureVersion,
            capitalModel=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
            portfolios=list(payload.portfolios or []),
            backtest=payload.backtest,
        ),
        extra_fields={
            'symbol': str(payload.symbol or 'EURUSD').strip().upper() or 'EURUSD',
            'timeframe': str(payload.timeframe or 'M1').strip().upper() or 'M1',
            'bars': max(1, int(payload.bars or 1)),
            'indicators': normalize_indicator_payload(list(payload.indicators or [])),
        },
    )

    return build_isolated_backtest_response(
        request_payload=request_payload,
        evaluation=evaluation,
    )


@router.post('/strategy/debug')
async def debug_strategy(
    payload: StrategyDebugRequest,
    request: Request,
):
    auth_user = require_request_auth(request)
    workspace_user_id = auth_user['workspace_user_id']
    workspace_id = 'default'
    chart_symbol = str(payload.chart.symbol or 'EURUSD').strip().upper() or 'EURUSD'
    chart_timeframe = str(payload.chart.timeframe or 'M1').strip().upper() or 'M1'
    chart_bars = max(1, int(payload.chart.bars or 1))
    indicator_payload = normalize_indicator_payload(list(payload.chart.indicators or []))
    session_label = f"Strategy debug · {time.strftime('%Y-%m-%d %H:%M:%S')}"
    session = create_workspace_system_log_session(
        workspace_user_id,
        workspace_id,
        label=session_label,
        source='strategy_debug',
        metadata={
            'draft_label': str(payload.draft_label or '').strip(),
            'chart_symbol': chart_symbol,
            'chart_timeframe': chart_timeframe,
            'chart_bars': chart_bars,
        },
        status='debug',
    )
    debug_entries = []
    current_session = session

    async def append_debug_entry(message: str, level: str = 'info', category: str = 'operator', context: dict | None = None):
        nonlocal current_session, debug_entries
        appended = await append_and_broadcast_workspace_system_log_entries(
            [
                {
                    'message': str(message or '').strip() or 'Strategy debug event.',
                    'level': level,
                    'source': 'strategy_debug',
                    'scope': 'strategy_debug',
                    'category': category,
                    'context': {
                        'draft_label': str(payload.draft_label or '').strip(),
                        'chart_symbol': chart_symbol,
                        'chart_timeframe': chart_timeframe,
                        'chart_bars': chart_bars,
                        **(context or {}),
                    },
                    'created_at': time.time(),
                },
            ],
            user_id=workspace_user_id,
            workspace_id=workspace_id,
            session_id=int(session['id']),
            source='strategy_debug',
            label=session_label,
            metadata=session.get('metadata') or {},
        )
        current_session = appended.get('session') or current_session
        debug_entries = [
            *debug_entries,
            *list(appended.get('entries') or []),
        ]

    await append_debug_entry(
        f'Debug started for {str(payload.draft_label or "").strip() or "current editor strategy"} using {chart_symbol} {chart_timeframe} ({chart_bars} bars).',
        'info',
        'audit',
        {
            'indicator_count': len(indicator_payload),
        },
    )

    evaluation = None
    try:
        evaluation = evaluate_strategy_request_in_context(
            payload=ApplyStrategyRequest(
                strategy=payload.strategy,
                strategies=[],
                backtest=payload.backtest,
            ),
            symbol_name=chart_symbol,
            timeframe=chart_timeframe,
            bars=chart_bars,
            indicators_payload=indicator_payload,
        )

        if evaluation.get('status') != 'ok':
            await append_debug_entry(
                f'Strategy debug failed: {str(evaluation.get("error") or "unknown error").strip() or "unknown error"}.',
                'error',
                'failure',
                {
                    'debug_status': 'failed',
                },
            )
            return {
                **evaluation,
                'debug_session': current_session,
                'debug_entries': debug_entries,
            }

        response = build_isolated_backtest_response(
            request_payload={
                'strategy': payload.strategy.model_dump(),
                'strategies': [],
                'backtest': payload.backtest.model_dump(),
                'symbol': chart_symbol,
                'timeframe': chart_timeframe,
                'bars': chart_bars,
                'indicators': indicator_payload,
            },
            evaluation=evaluation,
        )
        await append_debug_entry(
            f'Strategy debug completed: rows={int(response.get("rows") or 0)}, results={len(response.get("results") or [])}, markers={len(response.get("trade_markers") or [])}.',
            'success',
            'result',
            {
                'debug_status': 'completed',
                'rows': int(response.get('rows') or 0),
                'result_count': len(response.get('results') or []),
                'marker_count': len(response.get('trade_markers') or []),
                'required_feature_count': len(response.get('required_features') or []),
            },
        )
        return {
            **response,
            'debug_session': current_session,
            'debug_entries': debug_entries,
        }
    except Exception as error:
        await append_debug_entry(
            f'Strategy debug crashed: {str(error or "unknown error").strip() or "unknown error"}.',
            'error',
            'failure',
            {
                'debug_status': 'crashed',
            },
        )
        fallback = evaluation if isinstance(evaluation, dict) else build_runtime_payload()
        return {
            **fallback,
            'status': 'error',
            'error': str(error or 'Strategy debug failed.').strip() or 'Strategy debug failed.',
            'debug_session': current_session,
            'debug_entries': debug_entries,
        }


def execute_preset_compare_request(
    payload: PresetCompareRequest,
    *,
    progress_callback=None,
    should_cancel=None,
    baseline_summary_override: dict | None = None,
):
    def report(progress: float | None = None, phase: str | None = None, phase_label: str | None = None, detail: str | None = None):
        if callable(progress_callback):
            progress_callback(progress=progress, phase=phase, phase_label=phase_label, detail=detail)

    def ensure_not_cancelled():
        if callable(should_cancel) and should_cancel():
            raise ResearchJobCancelledError('Research job cancelled by user.')

    report(0.02, 'starting', 'Starting', 'Validating compare request.')
    if not payload.presets:
        return {
            'status': 'error',
            'error': 'No presets were provided for comparison.',
            **build_runtime_payload(),
        }

    baseline_summary = None
    study_windows = []
    seen_windows = set()
    study_timeframes = []
    seen_timeframes = set()
    study_symbols = []
    seen_symbols = set()
    walkforward_window_bars = None
    walkforward_step_bars = None
    walkforward_train_bars = None
    walkforward_test_bars = None
    chart_context = normalize_research_chart_context(payload.chartContext or {})

    for raw_window in payload.studyWindows or []:
        try:
            window = max(1, int(raw_window))
        except Exception:
            continue
        if window in seen_windows:
            continue
        seen_windows.add(window)
        study_windows.append(window)

    for raw_timeframe in payload.studyTimeframes or []:
        timeframe = str(raw_timeframe or '').strip().upper()
        if not timeframe or timeframe in seen_timeframes:
            continue
        seen_timeframes.add(timeframe)
        study_timeframes.append(timeframe)

    for raw_symbol in payload.studySymbols or []:
        symbol_name = str(raw_symbol or '').strip().upper()
        if not symbol_name or symbol_name in seen_symbols:
            continue
        seen_symbols.add(symbol_name)
        study_symbols.append(symbol_name)

    try:
        if payload.walkforwardWindowBars is not None:
            walkforward_window_bars = max(1, int(payload.walkforwardWindowBars))
    except Exception:
        walkforward_window_bars = None

    try:
        if payload.walkforwardStepBars is not None:
            walkforward_step_bars = max(1, int(payload.walkforwardStepBars))
    except Exception:
        walkforward_step_bars = None

    try:
        if payload.walkforwardTrainBars is not None:
            walkforward_train_bars = max(1, int(payload.walkforwardTrainBars))
    except Exception:
        walkforward_train_bars = None

    try:
        if payload.walkforwardTestBars is not None:
            walkforward_test_bars = max(1, int(payload.walkforwardTestBars))
    except Exception:
        walkforward_test_bars = None

    try:
        ensure_not_cancelled()
        if payload.baseline is not None:
            if baseline_summary_override is not None:
                report(0.08, 'baseline', 'Baseline', 'Reusing pipeline baseline backtest.')
                baseline_summary = summarize_comparison_stats(baseline_summary_override)
            else:
                report(0.08, 'baseline', 'Baseline', 'Evaluating baseline strategy.')
                baseline_evaluation = evaluate_comparison_entry(payload.baseline, payload.backtest, chart_context)
                if baseline_evaluation.get('status') != 'ok':
                    return {
                        'status': 'error',
                        'error': f'Failed to compare baseline "{payload.baseline.label}": {baseline_evaluation.get("error") or "unknown error"}',
                        **build_runtime_payload(),
                    }
                baseline_summary = summarize_comparison_stats(baseline_evaluation.get('stats'))

        report(0.15, 'compare', 'Preset Compare', 'Running current-context preset comparison.')
        comparisons = []
        for preset in payload.presets:
            ensure_not_cancelled()
            evaluation = evaluate_comparison_entry(preset, payload.backtest, chart_context)
            if evaluation.get('status') != 'ok':
                return {
                    'status': 'error',
                    'error': f'Failed to compare preset "{preset.label}": {evaluation.get("error") or "unknown error"}',
                    **build_runtime_payload(),
                }
            comparisons.append({
                'id': preset.id,
                'label': preset.label,
                'summary': summarize_comparison_stats(evaluation.get('stats')),
            })

        if baseline_summary is not None:
            comparisons = [
                {
                    **entry,
                    'delta_vs_baseline': build_comparison_deltas(entry.get('summary'), baseline_summary),
                }
                for entry in comparisons
            ]

        ranked = sorted(
            comparisons,
            key=lambda item: (
                float(item['summary'].get('net_pnl') or 0.0),
                float(item['summary'].get('expectancy_per_trade') or 0.0),
                -abs(float(item['summary'].get('max_drawdown') or 0.0)),
            ),
            reverse=True,
        )

        study_payload = None
        timeframe_study_payload = None
        symbol_study_payload = None
        walkforward_study_payload = None

        if study_windows:
            report(0.35, 'window_study', 'Window Study', 'Running rolling window consistency study.')
            ensure_not_cancelled()
            baseline_study_rows = []
            if payload.baseline is not None:
                for bars in study_windows:
                    ensure_not_cancelled()
                    evaluation = evaluate_comparison_entry(
                        payload.baseline,
                        build_backtest_payload_for_window(payload.backtest, bars),
                        build_research_chart_context_for_bars(chart_context, bars),
                    )
                    if evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to study baseline "{payload.baseline.label}" at {bars} bars: {evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }
                    baseline_study_rows.append(build_study_window_summary(
                        bars=bars,
                        summary=summarize_comparison_stats(evaluation.get('stats')),
                    ))

            study_comparisons = []
            for preset in payload.presets:
                ensure_not_cancelled()
                window_rows = []
                wins_vs_baseline = 0
                total_windows = 0
                net_pnl_delta_total = 0.0
                expectancy_delta_total = 0.0
                drawdown_delta_total = 0.0

                for index, bars in enumerate(study_windows):
                    evaluation = evaluate_comparison_entry(
                        preset,
                        build_backtest_payload_for_window(payload.backtest, bars),
                        build_research_chart_context_for_bars(chart_context, bars),
                    )
                    if evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to study preset "{preset.label}" at {bars} bars: {evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }

                    summary = summarize_comparison_stats(evaluation.get('stats'))
                    baseline_window_summary = baseline_study_rows[index]['summary'] if index < len(baseline_study_rows) else None
                    row = build_study_window_summary(
                        bars=bars,
                        summary=summary,
                        baseline_summary=baseline_window_summary,
                    )
                    window_rows.append(row)

                    delta = row.get('delta_vs_baseline') or {}
                    if delta:
                        total_windows += 1
                        if float(delta.get('net_pnl') or 0.0) > 0:
                            wins_vs_baseline += 1
                        net_pnl_delta_total += float(delta.get('net_pnl') or 0.0)
                        expectancy_delta_total += float(delta.get('expectancy_per_trade') or 0.0)
                        drawdown_delta_total += float(delta.get('max_drawdown') or 0.0)

                study_comparisons.append({
                    'id': preset.id,
                    'label': preset.label,
                    'windows': window_rows,
                    'consistency': {
                        'wins_vs_baseline': wins_vs_baseline,
                        'window_count': total_windows,
                        'win_ratio_vs_baseline': (wins_vs_baseline / total_windows) if total_windows else None,
                        'avg_delta_net_pnl': (net_pnl_delta_total / total_windows) if total_windows else None,
                        'avg_delta_expectancy': (expectancy_delta_total / total_windows) if total_windows else None,
                        'avg_delta_drawdown': (drawdown_delta_total / total_windows) if total_windows else None,
                    },
                })

            ranked_study = sorted(
                study_comparisons,
                key=lambda item: (
                    float((item.get('consistency') or {}).get('win_ratio_vs_baseline') or 0.0),
                    float((item.get('consistency') or {}).get('avg_delta_net_pnl') or 0.0),
                    float((item.get('consistency') or {}).get('avg_delta_expectancy') or 0.0),
                    -abs(float((item.get('consistency') or {}).get('avg_delta_drawdown') or 0.0)),
                ),
                reverse=True,
            )

            study_payload = {
                'windows': list(study_windows),
                'baseline': {
                    'id': payload.baseline.id,
                    'label': payload.baseline.label,
                    'windows': baseline_study_rows,
                } if payload.baseline is not None else None,
                'comparisons': study_comparisons,
                'best_preset_id': ranked_study[0]['id'] if ranked_study else None,
            }

        if study_timeframes and chart_context:
            report(0.55, 'timeframe_study', 'Timeframe Study', 'Running timeframe consistency study.')
            ensure_not_cancelled()
            context_symbol = str(chart_context.get('symbol') or '').strip().upper()
            context_bars = max(1, int(chart_context.get('bars') or 1))
            context_indicators = ensure_market_regime_indicator_payload(chart_context.get('indicators') or [])

            if not context_symbol:
                return {
                    'status': 'error',
                    'error': 'Timeframe study requires a chart context symbol.',
                    **build_runtime_payload(),
                }

            baseline_timeframe_rows = []
            if payload.baseline is not None:
                for timeframe in study_timeframes:
                    ensure_not_cancelled()
                    evaluation = evaluate_strategy_request_in_context(
                        payload=ApplyStrategyRequest(
                            strategy=payload.baseline.strategy,
                            backtest=build_backtest_payload_for_window(payload.backtest, context_bars),
                        ),
                        symbol_name=context_symbol,
                        timeframe=timeframe,
                        bars=context_bars,
                        indicators_payload=context_indicators,
                    )
                    if evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to study baseline "{payload.baseline.label}" on timeframe {timeframe}: {evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }
                    baseline_timeframe_rows.append({
                        'timeframe': timeframe,
                        'summary': summarize_comparison_stats(evaluation.get('stats')),
                    })

            timeframe_comparisons = []
            for preset in payload.presets:
                ensure_not_cancelled()
                timeframe_rows = []
                wins_vs_baseline = 0
                total_timeframes = 0
                net_pnl_delta_total = 0.0
                expectancy_delta_total = 0.0
                drawdown_delta_total = 0.0

                for index, timeframe in enumerate(study_timeframes):
                    evaluation = evaluate_strategy_request_in_context(
                        payload=ApplyStrategyRequest(
                            strategy=preset.strategy,
                            backtest=build_backtest_payload_for_window(payload.backtest, context_bars),
                        ),
                        symbol_name=context_symbol,
                        timeframe=timeframe,
                        bars=context_bars,
                        indicators_payload=context_indicators,
                    )
                    if evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to study preset "{preset.label}" on timeframe {timeframe}: {evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }

                    summary = summarize_comparison_stats(evaluation.get('stats'))
                    baseline_timeframe_summary = baseline_timeframe_rows[index]['summary'] if index < len(baseline_timeframe_rows) else None
                    delta = build_comparison_deltas(summary, baseline_timeframe_summary) if baseline_timeframe_summary is not None else None
                    timeframe_rows.append({
                        'timeframe': timeframe,
                        'summary': summary,
                        'delta_vs_baseline': delta,
                    })

                    if delta:
                        total_timeframes += 1
                        if float(delta.get('net_pnl') or 0.0) > 0:
                            wins_vs_baseline += 1
                        net_pnl_delta_total += float(delta.get('net_pnl') or 0.0)
                        expectancy_delta_total += float(delta.get('expectancy_per_trade') or 0.0)
                        drawdown_delta_total += float(delta.get('max_drawdown') or 0.0)

                timeframe_comparisons.append({
                    'id': preset.id,
                    'label': preset.label,
                    'timeframes': timeframe_rows,
                    'consistency': {
                        'wins_vs_baseline': wins_vs_baseline,
                        'timeframe_count': total_timeframes,
                        'win_ratio_vs_baseline': (wins_vs_baseline / total_timeframes) if total_timeframes else None,
                        'avg_delta_net_pnl': (net_pnl_delta_total / total_timeframes) if total_timeframes else None,
                        'avg_delta_expectancy': (expectancy_delta_total / total_timeframes) if total_timeframes else None,
                        'avg_delta_drawdown': (drawdown_delta_total / total_timeframes) if total_timeframes else None,
                    },
                })

            ranked_timeframes = sorted(
                timeframe_comparisons,
                key=lambda item: (
                    float((item.get('consistency') or {}).get('win_ratio_vs_baseline') or 0.0),
                    float((item.get('consistency') or {}).get('avg_delta_net_pnl') or 0.0),
                    float((item.get('consistency') or {}).get('avg_delta_expectancy') or 0.0),
                    -abs(float((item.get('consistency') or {}).get('avg_delta_drawdown') or 0.0)),
                ),
                reverse=True,
            )

            timeframe_study_payload = {
                'symbol': context_symbol,
                'bars': context_bars,
                'timeframes': list(study_timeframes),
                'baseline': {
                    'id': payload.baseline.id,
                    'label': payload.baseline.label,
                    'timeframes': baseline_timeframe_rows,
                } if payload.baseline is not None else None,
                'comparisons': timeframe_comparisons,
                'best_preset_id': ranked_timeframes[0]['id'] if ranked_timeframes else None,
            }

        if study_symbols and chart_context:
            report(0.72, 'symbol_study', 'Symbol Study', 'Running cross-symbol consistency study.')
            ensure_not_cancelled()
            context_timeframe = str(chart_context.get('timeframe') or '').strip().upper()
            context_bars = max(1, int(chart_context.get('bars') or 1))
            context_indicators = ensure_market_regime_indicator_payload(chart_context.get('indicators') or [])

            if not context_timeframe:
                return {
                    'status': 'error',
                    'error': 'Symbol study requires a chart context timeframe.',
                    **build_runtime_payload(),
                }

            baseline_symbol_rows = []
            if payload.baseline is not None:
                for symbol_name in study_symbols:
                    ensure_not_cancelled()
                    evaluation = evaluate_strategy_request_in_context(
                        payload=ApplyStrategyRequest(
                            strategy=payload.baseline.strategy,
                            backtest=build_backtest_payload_for_window(payload.backtest, context_bars),
                        ),
                        symbol_name=symbol_name,
                        timeframe=context_timeframe,
                        bars=context_bars,
                        indicators_payload=context_indicators,
                    )
                    if evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to study baseline "{payload.baseline.label}" on symbol {symbol_name}: {evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }
                    baseline_symbol_rows.append({
                        'symbol': symbol_name,
                        'summary': summarize_comparison_stats(evaluation.get('stats')),
                    })

            symbol_comparisons = []
            for preset in payload.presets:
                ensure_not_cancelled()
                symbol_rows = []
                wins_vs_baseline = 0
                total_symbols = 0
                net_pnl_delta_total = 0.0
                expectancy_delta_total = 0.0
                drawdown_delta_total = 0.0

                for index, symbol_name in enumerate(study_symbols):
                    evaluation = evaluate_strategy_request_in_context(
                        payload=ApplyStrategyRequest(
                            strategy=preset.strategy,
                            backtest=build_backtest_payload_for_window(payload.backtest, context_bars),
                        ),
                        symbol_name=symbol_name,
                        timeframe=context_timeframe,
                        bars=context_bars,
                        indicators_payload=context_indicators,
                    )
                    if evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to study preset "{preset.label}" on symbol {symbol_name}: {evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }

                    summary = summarize_comparison_stats(evaluation.get('stats'))
                    baseline_symbol_summary = baseline_symbol_rows[index]['summary'] if index < len(baseline_symbol_rows) else None
                    delta = build_comparison_deltas(summary, baseline_symbol_summary) if baseline_symbol_summary is not None else None
                    symbol_rows.append({
                        'symbol': symbol_name,
                        'summary': summary,
                        'delta_vs_baseline': delta,
                    })

                    if delta:
                        total_symbols += 1
                        if float(delta.get('net_pnl') or 0.0) > 0:
                            wins_vs_baseline += 1
                        net_pnl_delta_total += float(delta.get('net_pnl') or 0.0)
                        expectancy_delta_total += float(delta.get('expectancy_per_trade') or 0.0)
                        drawdown_delta_total += float(delta.get('max_drawdown') or 0.0)

                symbol_comparisons.append({
                    'id': preset.id,
                    'label': preset.label,
                    'symbols': symbol_rows,
                    'consistency': {
                        'wins_vs_baseline': wins_vs_baseline,
                        'symbol_count': total_symbols,
                        'win_ratio_vs_baseline': (wins_vs_baseline / total_symbols) if total_symbols else None,
                        'avg_delta_net_pnl': (net_pnl_delta_total / total_symbols) if total_symbols else None,
                        'avg_delta_expectancy': (expectancy_delta_total / total_symbols) if total_symbols else None,
                        'avg_delta_drawdown': (drawdown_delta_total / total_symbols) if total_symbols else None,
                    },
                })

            ranked_symbols = sorted(
                symbol_comparisons,
                key=lambda item: (
                    float((item.get('consistency') or {}).get('win_ratio_vs_baseline') or 0.0),
                    float((item.get('consistency') or {}).get('avg_delta_net_pnl') or 0.0),
                    float((item.get('consistency') or {}).get('avg_delta_expectancy') or 0.0),
                    -abs(float((item.get('consistency') or {}).get('avg_delta_drawdown') or 0.0)),
                ),
                reverse=True,
            )

            symbol_study_payload = {
                'timeframe': context_timeframe,
                'bars': context_bars,
                'symbols': list(study_symbols),
                'baseline': {
                    'id': payload.baseline.id,
                    'label': payload.baseline.label,
                    'symbols': baseline_symbol_rows,
                } if payload.baseline is not None else None,
                'comparisons': symbol_comparisons,
                'best_preset_id': ranked_symbols[0]['id'] if ranked_symbols else None,
            }

        if walkforward_window_bars and chart_context:
            report(0.85, 'walkforward_study', 'Walk-forward', 'Running train/test walk-forward validation.')
            ensure_not_cancelled()
            context_symbol = str(chart_context.get('symbol') or '').strip().upper()
            context_timeframe = str(chart_context.get('timeframe') or '').strip().upper()
            context_bars = max(1, int(chart_context.get('bars') or 1))
            context_indicators = ensure_market_regime_indicator_payload(chart_context.get('indicators') or [])

            if not context_symbol or not context_timeframe:
                return {
                    'status': 'error',
                    'error': 'Walk-forward validation requires a chart context symbol and timeframe.',
                    **build_runtime_payload(),
                }

            full_strategy_view = build_contextual_strategy_view(
                symbol_name=context_symbol,
                timeframe=context_timeframe,
                bars=context_bars,
                indicators_payload=context_indicators,
                backtest_params=build_backtest_params(payload.backtest),
            )
            total_context_bars = len(full_strategy_view['symbol'].candles.index)
            effective_train_bars = walkforward_train_bars or walkforward_window_bars
            effective_test_bars = walkforward_test_bars or walkforward_window_bars
            effective_step_bars = walkforward_step_bars or effective_test_bars or walkforward_window_bars
            walkforward_pairs = build_walkforward_train_test_pairs(
                total_bars=total_context_bars,
                train_bars=effective_train_bars,
                test_bars=effective_test_bars,
                step_bars=effective_step_bars,
            )
            walkforward_segments = build_walkforward_segments(
                total_bars=total_context_bars,
                window_bars=walkforward_window_bars,
                step_bars=walkforward_step_bars or walkforward_window_bars,
            )

            baseline_walkforward_rows = []
            if payload.baseline is not None:
                for segment in walkforward_pairs:
                    ensure_not_cancelled()
                    train_evaluation = evaluate_strategy_request_on_segment(
                        payload=ApplyStrategyRequest(
                            strategy=payload.baseline.strategy,
                            backtest=build_backtest_payload_for_window(payload.backtest, segment['train_bars']),
                        ),
                        strategy_view=full_strategy_view,
                        symbol_name=context_symbol,
                        timeframe=context_timeframe,
                        segment_start_index=segment['train_start_index'],
                        segment_bars=segment['train_bars'],
                    )
                    if train_evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to walk-forward baseline "{payload.baseline.label}" on segment {segment["label"]}: {train_evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }
                    test_evaluation = evaluate_strategy_request_on_segment(
                        payload=ApplyStrategyRequest(
                            strategy=payload.baseline.strategy,
                            backtest=build_backtest_payload_for_window(payload.backtest, segment['test_bars']),
                        ),
                        strategy_view=full_strategy_view,
                        symbol_name=context_symbol,
                        timeframe=context_timeframe,
                        segment_start_index=segment['test_start_index'],
                        segment_bars=segment['test_bars'],
                    )
                    if test_evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to walk-forward baseline "{payload.baseline.label}" on segment {segment["label"]}: {test_evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }
                    baseline_walkforward_rows.append({
                        **segment,
                        'train_summary': summarize_comparison_stats(train_evaluation.get('stats')),
                        'test_summary': summarize_comparison_stats(test_evaluation.get('stats')),
                    })

            walkforward_comparisons = []
            for preset in payload.presets:
                ensure_not_cancelled()
                pair_rows = []
                wins_vs_baseline = 0
                total_pairs = 0
                net_pnl_delta_total = 0.0
                expectancy_delta_total = 0.0
                drawdown_delta_total = 0.0
                train_net_pnl_total = 0.0
                test_net_pnl_total = 0.0
                train_to_test_shift_total = 0.0
                stable_pairs = 0

                for index, segment in enumerate(walkforward_pairs):
                    train_evaluation = evaluate_strategy_request_on_segment(
                        payload=ApplyStrategyRequest(
                            strategy=preset.strategy,
                            backtest=build_backtest_payload_for_window(payload.backtest, segment['train_bars']),
                        ),
                        strategy_view=full_strategy_view,
                        symbol_name=context_symbol,
                        timeframe=context_timeframe,
                        segment_start_index=segment['train_start_index'],
                        segment_bars=segment['train_bars'],
                    )
                    if train_evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to walk-forward preset "{preset.label}" on segment {segment["label"]}: {train_evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }

                    test_evaluation = evaluate_strategy_request_on_segment(
                        payload=ApplyStrategyRequest(
                            strategy=preset.strategy,
                            backtest=build_backtest_payload_for_window(payload.backtest, segment['test_bars']),
                        ),
                        strategy_view=full_strategy_view,
                        symbol_name=context_symbol,
                        timeframe=context_timeframe,
                        segment_start_index=segment['test_start_index'],
                        segment_bars=segment['test_bars'],
                    )
                    if test_evaluation.get('status') != 'ok':
                        return {
                            'status': 'error',
                            'error': f'Failed to walk-forward preset "{preset.label}" on segment {segment["label"]}: {test_evaluation.get("error") or "unknown error"}',
                            **build_runtime_payload(),
                        }

                    train_summary = summarize_comparison_stats(train_evaluation.get('stats'))
                    test_summary = summarize_comparison_stats(test_evaluation.get('stats'))
                    baseline_test_summary = baseline_walkforward_rows[index]['test_summary'] if index < len(baseline_walkforward_rows) else None
                    delta = build_comparison_deltas(test_summary, baseline_test_summary) if baseline_test_summary is not None else None
                    train_net_pnl = float(train_summary.get('net_pnl') or 0.0)
                    test_net_pnl = float(test_summary.get('net_pnl') or 0.0)
                    train_to_test_shift = test_net_pnl - train_net_pnl

                    pair_rows.append({
                        **segment,
                        'train_summary': train_summary,
                        'test_summary': test_summary,
                        'delta_vs_baseline': delta,
                        'train_to_test_shift': train_to_test_shift,
                    })

                    if delta:
                        total_pairs += 1
                        if float(delta.get('net_pnl') or 0.0) > 0:
                            wins_vs_baseline += 1
                        net_pnl_delta_total += float(delta.get('net_pnl') or 0.0)
                        expectancy_delta_total += float(delta.get('expectancy_per_trade') or 0.0)
                        drawdown_delta_total += float(delta.get('max_drawdown') or 0.0)
                    train_net_pnl_total += train_net_pnl
                    test_net_pnl_total += test_net_pnl
                    train_to_test_shift_total += train_to_test_shift
                    if train_net_pnl > 0 and test_net_pnl > 0:
                        stable_pairs += 1

                walkforward_comparisons.append({
                    'id': preset.id,
                    'label': preset.label,
                    'pairs': pair_rows,
                    'segments': pair_rows,
                    'consistency': {
                        'wins_vs_baseline': wins_vs_baseline,
                        'segment_count': total_pairs,
                        'pair_count': total_pairs,
                        'win_ratio_vs_baseline': (wins_vs_baseline / total_pairs) if total_pairs else None,
                        'avg_delta_net_pnl': (net_pnl_delta_total / total_pairs) if total_pairs else None,
                        'avg_delta_expectancy': (expectancy_delta_total / total_pairs) if total_pairs else None,
                        'avg_delta_drawdown': (drawdown_delta_total / total_pairs) if total_pairs else None,
                    },
                    'train_test_consistency': {
                        'pair_count': total_pairs,
                        'stable_pair_count': stable_pairs,
                        'stable_pair_ratio': (stable_pairs / total_pairs) if total_pairs else None,
                        'avg_train_net_pnl': (train_net_pnl_total / total_pairs) if total_pairs else None,
                        'avg_test_net_pnl': (test_net_pnl_total / total_pairs) if total_pairs else None,
                        'avg_train_to_test_net_pnl_shift': (train_to_test_shift_total / total_pairs) if total_pairs else None,
                    },
                })

            ranked_walkforward = sorted(
                walkforward_comparisons,
                key=lambda item: (
                    float((item.get('consistency') or {}).get('win_ratio_vs_baseline') or 0.0),
                    float((item.get('consistency') or {}).get('avg_delta_net_pnl') or 0.0),
                    float((item.get('consistency') or {}).get('avg_delta_expectancy') or 0.0),
                    -abs(float((item.get('consistency') or {}).get('avg_delta_drawdown') or 0.0)),
                ),
                reverse=True,
            )

            walkforward_study_payload = {
                'mode': 'train_test',
                'symbol': context_symbol,
                'timeframe': context_timeframe,
                'bars': total_context_bars,
                'window_bars': walkforward_window_bars,
                'train_bars': effective_train_bars,
                'test_bars': effective_test_bars,
                'step_bars': effective_step_bars,
                'segments': walkforward_segments,
                'pairs': walkforward_pairs,
                'baseline': {
                    'id': payload.baseline.id,
                    'label': payload.baseline.label,
                    'pairs': baseline_walkforward_rows,
                    'segments': baseline_walkforward_rows,
                } if payload.baseline is not None else None,
                'comparisons': walkforward_comparisons,
                'best_preset_id': ranked_walkforward[0]['id'] if ranked_walkforward else None,
            }

        report(0.97, 'finalizing', 'Finalizing', 'Preparing study result payload.')
        ensure_not_cancelled()
        return {
            'status': 'ok',
            'baseline': {
                'id': payload.baseline.id,
                'label': payload.baseline.label,
                'summary': baseline_summary,
            } if payload.baseline is not None and baseline_summary is not None else None,
            'comparisons': comparisons,
            'best_preset_id': ranked[0]['id'] if ranked else None,
            'study': study_payload,
            'timeframe_study': timeframe_study_payload,
            'symbol_study': symbol_study_payload,
            'walkforward_study': walkforward_study_payload,
        }
    except ResearchJobCancelledError as error:
        return {
            'status': 'error',
            'error': str(error),
            **build_runtime_payload(),
        }


@router.post('/strategy/presets/compare')
def compare_strategy_presets(
    payload: PresetCompareRequest,
    request: Request,
):
    require_request_auth(request)
    return execute_preset_compare_request(payload)


@router.post('/strategy/configure')
async def configure_strategy(
    payload: ConfigureStrategyRequest,
    request: Request,
    source: str = Query(default='api'),
):
    auth_user = require_request_auth(request)
    strategy_state = state.strategy
    strategy_state.workspace_user_id = auth_user['workspace_user_id']
    strategy_state.workspace_id = 'default'
    response = configure_strategy_runtime(payload)
    if response.get('status') == 'ok':
        await broadcast_strategy_event('strategy.updated', source=source)
    return response


@router.post('/strategy/backtest/toggle')
async def toggle_backtest(
    payload: BacktestToggleRequest,
    request: Request,
    source: str = Query(default='api'),
):
    auth_user = require_request_auth(request)
    strategy_state = state.strategy
    strategy_state.workspace_user_id = auth_user['workspace_user_id']
    strategy_state.workspace_id = 'default'
    strategy_state.backtest_active = bool(payload.enabled)

    if not payload.enabled:
        strategy_state.is_stale = False
        strategy_state.stale_reason = None
        strategy_state.stale_overlap = []
        strategy_state.trade_markers = []
        persist_strategy_runtime_if_configured()
        response = build_configure_response_payload()
        await broadcast_strategy_event('strategy.updated', source=source)
        return response

    effective_strategy = payload.strategy
    effective_strategies = list(payload.strategies or [])
    effective_portfolio_structure_version = payload.portfolioStructureVersion
    effective_capital_model = (dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None)
    effective_portfolios = list(payload.portfolios or [])
    if effective_strategy is None:
        existing_request = strategy_state.request or {}
        strategy_payload = existing_request.get('strategy')
        if not strategy_payload:
            return {
                'status': 'error',
                'error': 'Apply a strategy before enabling the backtest.',
                **build_runtime_payload(),
            }
        effective_strategy = StrategyPayload.model_validate(strategy_payload)
        if not effective_strategies:
            effective_strategies = list(existing_request.get('strategies') or [])
        if effective_portfolio_structure_version is None:
            effective_portfolio_structure_version = existing_request.get('portfolioStructureVersion')
        if effective_capital_model is None and isinstance(existing_request.get('capitalModel'), dict):
            effective_capital_model = dict(existing_request.get('capitalModel') or {})
        if not effective_portfolios:
            effective_portfolios = list(existing_request.get('portfolios') or [])

    response = run_strategy_request(ApplyStrategyRequest(
        strategy=effective_strategy,
        strategies=effective_strategies,
        portfolioStructureVersion=effective_portfolio_structure_version,
        capitalModel=effective_capital_model,
        portfolios=effective_portfolios,
        backtest=payload.backtest,
    ))
    if response.get('status') == 'ok':
        strategy_state.backtest_active = True
        persist_strategy_runtime_if_configured()
        await broadcast_strategy_event('strategy.updated', source=source)
    return response


@router.get('/strategy/status')
async def get_strategy_status(request: Request):
    require_request_auth(request)
    refreshed = refresh_stale_strategy_if_needed()

    if refreshed and refreshed.get('status') == 'ok':
        await broadcast_strategy_event('strategy.updated', source='strategy_refresh')

    if refreshed and refreshed.get('status') == 'error':
        return {
            'status': 'error',
            'error': refreshed.get('error'),
            **build_runtime_payload(),
        }

    return {
        'status': 'ok',
        **build_runtime_payload(),
    }


@router.get('/strategy/results')
async def get_strategy_results(request: Request):
    require_request_auth(request)
    strategy_state = state.strategy
    refreshed = refresh_stale_strategy_if_needed()

    if refreshed and refreshed.get('status') == 'ok':
        await broadcast_strategy_event('strategy.updated', source='strategy_refresh')

    if refreshed and refreshed.get('status') == 'error':
        return refreshed

    if strategy_state.results is None:
        return {
            'status': 'empty',
            **build_runtime_payload(),
            'results': [],
        }

    serialized_results = serialize_results(strategy_state.results)

    return {
        'status': refreshed['status'] if refreshed else 'ok',
        **build_runtime_payload(),
        'rows': len(serialized_results),
        'results': serialized_results,
    }


@router.websocket('/ws/strategy')
async def strategy_websocket(
    websocket: WebSocket,
    source: str = Query(default='frontend'),
):
    auth_user = await require_websocket_auth_or_close(websocket)
    if not auth_user:
        return

    await websocket.accept()
    realtime_sync.subscribe(STRATEGY_CHANNEL_KEY, websocket)

    try:
        await websocket.send_json(
            build_strategy_event_payload(
                event_type='strategy.snapshot',
                source=source,
            )
        )

        while True:
            message = await websocket.receive_text()
            if message == 'ping':
                await websocket.send_json({'type': 'pong'})
    except WebSocketDisconnect:
        realtime_sync.unsubscribe(STRATEGY_CHANNEL_KEY, websocket)
    except Exception:
        realtime_sync.unsubscribe(STRATEGY_CHANNEL_KEY, websocket)

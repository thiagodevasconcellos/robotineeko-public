import datetime
import math
import os
import time
from time import perf_counter
import requests

try:
    from ..app_state import state
    from ..lib.symbol import Symbol
    from ..lib.strategy import Strategy
    from .market_data_service import ensure_market_data
    from ..config import build_feature_flags, build_trade_service_config
    from ..strategy_backend import apply_indicator_payload, normalize_indicator_payload, resolve_strategy_param_aliases
    from .workspace_store import (
        get_workspace_live_trade_by_command_id,
        update_workspace_live_trade_cycle_broker_position_ticket,
        upsert_workspace_live_trade,
    )
except ImportError:
    from app_state import state
    from lib.symbol import Symbol
    from lib.strategy import Strategy
    from services.market_data_service import ensure_market_data
    from config import build_feature_flags, build_trade_service_config
    from strategy_backend import apply_indicator_payload, normalize_indicator_payload, resolve_strategy_param_aliases
    from services.workspace_store import (
        get_workspace_live_trade_by_command_id,
        update_workspace_live_trade_cycle_broker_position_ticket,
        upsert_workspace_live_trade,
    )

MAX_TRADE_AUDIT_EVENTS = 250
MAX_TRADE_LATENCY_EVENTS = 500
MAX_TRADE_ORDER_INTENTS = 250
MAX_TRADE_ORDER_COMMANDS = 250
DEFAULT_LIVE_ORDER_VOLUME = 0.01
TRADE_COMMAND_STALE_SECONDS = 15.0
TRADE_MARKET_FEED_GRACE_SECONDS = 12.0
TRADE_MARKET_FEED_MIN_STALE_SECONDS = 20.0
TRADE_MARKET_FEED_EXTRA_SECONDS = 15.0
TRADE_RUNTIME_DEFAULT_BARS = 2000
VALID_SAME_SYMBOL_EXECUTION_POLICIES = {
    'independent',
    'single_active_per_symbol',
    'block_conflicts',
}
SUPPORTED_TRADE_PORTFOLIO_VOLUME_MODES = {
    'fixed_volume',
    'max_affordable',
    'base_volume_compounding',
}
BAR_OPEN_ONLY_DECISIONS = {
    'open_long',
    'open_short',
    'close_long',
    'close_short',
    'invert_to_long',
    'invert_to_short',
}
TRADE_SERVICE_CONFIG = build_trade_service_config()
FEATURE_FLAGS = build_feature_flags()

TIMEFRAME_TO_SECONDS = {
    'M1': 60,
    'M5': 300,
    'M15': 900,
    'M30': 1800,
    'H1': 3600,
    'H4': 14400,
    'D1': 86400,
}


def _trim_text(value):
    return str(value or '').strip()


def _parse_optional_float(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_trade_positive_float(value):
    parsed = _parse_optional_float(value)
    if parsed is None or not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _normalize_trade_portfolio_mode(value):
    normalized = _trim_text(value).lower() or 'parallel_sleeves'
    if normalized not in {'parallel_sleeves', 'shared_pipe'}:
        normalized = 'parallel_sleeves'
    return normalized


def _normalize_trade_portfolio_structure_version(value):
    try:
        parsed = int(value or 1)
    except Exception:
        return 1
    return 2 if parsed == 2 else 1


def _normalize_trade_volume_mode(value):
    normalized = _trim_text(value).lower() or 'fixed_volume'
    if normalized not in SUPPORTED_TRADE_PORTFOLIO_VOLUME_MODES:
        normalized = 'fixed_volume'
    return normalized


def _normalize_same_symbol_execution_policy(value):
    normalized = _trim_text(value).lower() or 'independent'
    if normalized not in VALID_SAME_SYMBOL_EXECUTION_POLICIES:
        normalized = 'independent'
    return normalized


def _resolve_effective_same_symbol_execution_policy(portfolio_mode, policy):
    normalized_mode = _normalize_trade_portfolio_mode(portfolio_mode)
    normalized_policy = _normalize_same_symbol_execution_policy(policy)
    if normalized_mode == 'shared_pipe':
        return 'single_active_per_symbol'
    return normalized_policy


def _parse_boolish(value):
    normalized = _trim_text(value).lower()
    if not normalized:
        return None
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    return None


def _is_nonfatal_market_sync_bridge_error_message(message: str | None):
    message_lower = _trim_text(message).lower()
    if not message_lower:
        return False
    if '/mt5/jobs/' not in message_lower:
        return False
    if '/trade/commands/' in message_lower:
        return False
    return (
        '/history' in message_lower
        or '/update' in message_lower
        or '/next' in message_lower
    )


def _has_active_live_command_queue():
    for entry in list(state.trade.order_commands or []):
        status = _trim_text((entry or {}).get('status')).lower() or 'queued'
        if status not in {'filled', 'rejected', 'stale'}:
            return True
    return False


def _is_nonfatal_idle_trade_command_poll_error_message(message: str | None):
    message_lower = _trim_text(message).lower()
    if not message_lower:
        return False
    if '/mt5/trade/commands/next' not in message_lower:
        return False
    if _has_active_live_command_queue():
        return False
    return 'status=1003' in message_lower or 'timeout' in message_lower


def _is_nonfatal_market_sync_bridge_error(kind: str, payload: dict | None = None):
    safe_kind = _trim_text(kind).lower()
    safe_payload = dict(payload or {})
    if safe_kind not in {'error', 'request_error', 'data_error'}:
        return False
    return _is_nonfatal_market_sync_bridge_error_message(safe_payload.get('message'))


def _is_weekend_market_closure_window(now_timestamp: float | int | None, latest_candle_time: float | int | None):
    safe_now = _parse_optional_float(now_timestamp)
    safe_latest = _parse_optional_float(latest_candle_time)
    if safe_now is None or safe_latest is None:
        return False
    now_dt = datetime.datetime.fromtimestamp(float(safe_now), tz=datetime.timezone.utc)
    latest_dt = datetime.datetime.fromtimestamp(float(safe_latest), tz=datetime.timezone.utc)
    if now_dt.weekday() == 5:
        return True
    if now_dt.weekday() == 6 and latest_dt.weekday() in {4, 5}:
        return True
    return False


def _sanitize_trade_payload_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _sanitize_trade_payload_value(entry)
            for key, entry in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_trade_payload_value(entry) for entry in value]
    item = getattr(value, 'item', None)
    if callable(item):
        try:
            return _sanitize_trade_payload_value(item())
        except Exception:
            return str(value)
    return str(value)


def normalize_trade_sleeve(entry, index: int = 0):
    payload = dict(entry or {})
    sleeve_id = _trim_text(payload.get('id')) or f'sleeve-{index + 1}'
    label = _trim_text(payload.get('label')) or f'Sleeve {index + 1}'
    symbol = _trim_text(payload.get('symbol')).upper() or 'EURUSD'
    timeframe = _trim_text(payload.get('timeframe')).upper() or 'M1'
    strategy = payload.get('strategy')
    requested_mode = _normalize_trade_volume_mode(
        payload.get('volume_mode', payload.get('volumeMode'))
    )
    fixed_volume = _coerce_trade_positive_float(
        payload.get('fixed_volume', payload.get('fixedVolume'))
    )
    base_volume = _coerce_trade_positive_float(
        payload.get('base_volume', payload.get('baseVolume'))
    )
    fallback_volume = max(
        0.01,
        float(
            fixed_volume
            or base_volume
            or payload.get('volume')
            or DEFAULT_LIVE_ORDER_VOLUME
        ),
    )
    legacy_fallback_applied = requested_mode != 'fixed_volume'

    return {
        'id': sleeve_id,
        'label': label,
        'enabled': payload.get('enabled') is not False,
        'symbol': symbol,
        'timeframe': timeframe,
        'volume': max(0.01, float(payload.get('volume') or fallback_volume or DEFAULT_LIVE_ORDER_VOLUME)),
        'volume_mode': requested_mode,
        'fixed_volume': fixed_volume,
        'base_volume': base_volume,
        'max_volume_cap': _coerce_trade_positive_float(
            payload.get('max_volume_cap', payload.get('maxVolumeCap'))
        ),
        'reference_capital': _coerce_trade_positive_float(
            payload.get('reference_capital', payload.get('referenceCapital'))
        ),
        'portfolio_id': _trim_text(payload.get('portfolio_id', payload.get('portfolioId'))),
        'portfolio_label': _trim_text(payload.get('portfolio_label', payload.get('portfolioLabel'))),
        'pipeline_id': _trim_text(payload.get('pipeline_id', payload.get('pipelineId'))),
        'pipeline_label': _trim_text(payload.get('pipeline_label', payload.get('pipelineLabel'))),
        'legacy_volume_fallback_applied': bool(
            payload.get('legacy_volume_fallback_applied', legacy_fallback_applied)
        ),
        'source_strategy_id': _trim_text(payload.get('source_strategy_id') or payload.get('sourceStrategyId')),
        'strategy': strategy if isinstance(strategy, dict) else None,
        'indicators': list(payload.get('indicators') or []) if isinstance(payload.get('indicators'), list) else [],
    }


def _normalize_trade_runtime_sleeve(
    entry,
    index: int = 0,
    *,
    forced_enabled: bool | None = None,
    default_portfolio_id: str = '',
    default_portfolio_label: str = '',
    default_pipeline_id: str = '',
    default_pipeline_label: str = '',
    default_pipeline_mode: str = 'parallel_sleeves',
):
    normalized = normalize_trade_sleeve(entry, index)
    if forced_enabled is not None:
        normalized['enabled'] = bool(forced_enabled) and normalized.get('enabled') is not False
    if not _trim_text(normalized.get('portfolio_id')):
        normalized['portfolio_id'] = _trim_text(default_portfolio_id)
    if not _trim_text(normalized.get('portfolio_label')):
        normalized['portfolio_label'] = _trim_text(default_portfolio_label)
    if not _trim_text(normalized.get('pipeline_id')):
        normalized['pipeline_id'] = _trim_text(default_pipeline_id)
    if not _trim_text(normalized.get('pipeline_label')):
        normalized['pipeline_label'] = _trim_text(default_pipeline_label)
    normalized['portfolio_mode'] = _normalize_trade_portfolio_mode(
        (entry or {}).get('portfolio_mode', (entry or {}).get('portfolioMode', default_pipeline_mode))
    )
    return normalized


def _build_implicit_trade_portfolios(compiled_sleeves: list[dict], *, portfolio_mode: str):
    next_sleeves = []
    for index, sleeve in enumerate(list(compiled_sleeves or [])):
        next_sleeves.append(_normalize_trade_runtime_sleeve(
            sleeve,
            index,
            default_portfolio_id='legacy-default',
            default_portfolio_label='Legacy default portfolio',
            default_pipeline_id='legacy-pipeline',
            default_pipeline_label='Legacy pipeline',
            default_pipeline_mode=portfolio_mode,
        ))
    return [{
        'id': 'legacy-default',
        'label': 'Legacy default portfolio',
        'enabled': True,
        'capital_mode': 'legacy_shared',
        'capital_value': None,
        'rebalance_mode': 'static',
        'pipelines': [{
            'id': 'legacy-pipeline',
            'label': 'Legacy pipeline',
            'enabled': True,
            'portfolio_mode': portfolio_mode,
            'sleeves': next_sleeves,
        }],
    }]


def resolve_trade_runtime_structure(payload: dict | None):
    safe_payload = dict(payload or {})
    fallback_mode = _normalize_trade_portfolio_mode(safe_payload.get('mode'))
    legacy_sleeves = [
        _normalize_trade_runtime_sleeve(
            entry,
            index,
            default_portfolio_id='legacy-default',
            default_portfolio_label='Legacy default portfolio',
            default_pipeline_id='legacy-pipeline',
            default_pipeline_label='Legacy pipeline',
            default_pipeline_mode=fallback_mode,
        )
        for index, entry in enumerate(list(safe_payload.get('sleeves') or []))
    ]
    explicit_portfolios = list(safe_payload.get('portfolios') or [])
    if not (FEATURE_FLAGS.get('trader_portfolios_v2') and explicit_portfolios):
        return {
            'portfolio_structure_version': _normalize_trade_portfolio_structure_version(
                safe_payload.get('portfolio_structure_version', safe_payload.get('portfolioStructureVersion'))
            ),
            'sleeves': legacy_sleeves,
            'portfolios': _build_implicit_trade_portfolios(legacy_sleeves, portfolio_mode=fallback_mode),
        }

    compiled_sleeves = []
    normalized_portfolios = []
    sleeve_index = 0

    for portfolio_index, portfolio in enumerate(explicit_portfolios):
        safe_portfolio = dict(portfolio or {})
        portfolio_id = _trim_text(safe_portfolio.get('id')) or f'portfolio-{portfolio_index + 1}'
        portfolio_label = _trim_text(safe_portfolio.get('label')) or f'Portfolio {portfolio_index + 1}'
        portfolio_enabled = safe_portfolio.get('enabled') is not False
        next_pipelines = []

        for pipeline_index, pipeline in enumerate(list(safe_portfolio.get('pipelines') or [])):
            safe_pipeline = dict(pipeline or {})
            pipeline_id = _trim_text(safe_pipeline.get('id')) or f'{portfolio_id}-pipeline-{pipeline_index + 1}'
            pipeline_label = _trim_text(safe_pipeline.get('label')) or f'Pipeline {pipeline_index + 1}'
            pipeline_enabled = portfolio_enabled and safe_pipeline.get('enabled') is not False
            pipeline_mode = _normalize_trade_portfolio_mode(
                safe_pipeline.get('portfolio_mode', safe_pipeline.get('portfolioMode', fallback_mode))
            )
            next_pipeline_sleeves = []

            for sleeve in list(safe_pipeline.get('sleeves') or []):
                compiled_sleeve = _normalize_trade_runtime_sleeve(
                    sleeve,
                    sleeve_index,
                    forced_enabled=pipeline_enabled,
                    default_portfolio_id=portfolio_id,
                    default_portfolio_label=portfolio_label,
                    default_pipeline_id=pipeline_id,
                    default_pipeline_label=pipeline_label,
                    default_pipeline_mode=pipeline_mode,
                )
                next_pipeline_sleeves.append(compiled_sleeve)
                compiled_sleeves.append(compiled_sleeve)
                sleeve_index += 1

            next_pipelines.append({
                'id': pipeline_id,
                'label': pipeline_label,
                'enabled': pipeline_enabled,
                'portfolio_mode': pipeline_mode,
                'sleeves': next_pipeline_sleeves,
            })

        normalized_portfolios.append({
            'id': portfolio_id,
            'label': portfolio_label,
            'enabled': portfolio_enabled,
            'capital_mode': _trim_text(safe_portfolio.get('capital_mode', safe_portfolio.get('capitalMode'))) or 'legacy_shared',
            'capital_value': _parse_optional_float(safe_portfolio.get('capital_value', safe_portfolio.get('capitalValue'))),
            'rebalance_mode': _trim_text(safe_portfolio.get('rebalance_mode', safe_portfolio.get('rebalanceMode'))) or 'static',
            'pipelines': next_pipelines,
        })

    if not compiled_sleeves:
        return {
            'portfolio_structure_version': _normalize_trade_portfolio_structure_version(
                safe_payload.get('portfolio_structure_version', safe_payload.get('portfolioStructureVersion'))
            ),
            'sleeves': legacy_sleeves,
            'portfolios': _build_implicit_trade_portfolios(legacy_sleeves, portfolio_mode=fallback_mode),
        }

    return {
        'portfolio_structure_version': 2,
        'sleeves': compiled_sleeves,
        'portfolios': normalized_portfolios,
    }


def _get_trade_indicator_payload(sleeve: dict | None):
    sleeve_indicators = list((sleeve or {}).get('indicators') or [])
    if sleeve_indicators:
        return normalize_indicator_payload(sleeve_indicators)

    chart_request = state.chart.request if isinstance(state.chart.request, dict) else {}
    chart_indicators = list(chart_request.get('indicators') or [])
    if chart_indicators:
        return normalize_indicator_payload(chart_indicators)

    return []


def _rebuild_active_symbols():
    trade_state = state.trade
    active_symbols = sorted({
        _trim_text(entry.get('symbol')).upper()
        for entry in list(trade_state.sleeves or [])
        if entry.get('enabled') is not False and _trim_text(entry.get('symbol'))
    })
    trade_state.active_symbols = active_symbols


def _timeframe_to_seconds(value: str | None):
    safe_value = _trim_text(value).upper()
    return TIMEFRAME_TO_SECONDS.get(safe_value, 60)


def _is_trade_isolated_service_mode():
    return os.getenv('ROBOTINEEKO_TRADE_SERVICE_ISOLATED', '0').strip().lower() in {'1', 'true', 'yes', 'on'}


def _build_trade_internal_headers():
    token = _trim_text(TRADE_SERVICE_CONFIG.get('internal_token'))
    headers = {}
    if token:
        headers['x-robotineeko-trade-internal-token'] = token
    return headers


def _is_trade_snapshot_fresh(symbol: str, timeframe: str, candles: list | None):
    safe_symbol = _trim_text(symbol).upper()
    safe_timeframe = _trim_text(timeframe).upper() or 'M1'
    safe_candles = list(candles or [])
    if not safe_symbol or not safe_candles:
        return False

    try:
        latest_candle_time = float((safe_candles[-1] or {}).get('time'))
    except Exception:
        return False

    if not math.isfinite(latest_candle_time) or latest_candle_time <= 0:
        return False

    timeframe_seconds = _timeframe_to_seconds(safe_timeframe)
    stale_after_seconds = max(
        TRADE_MARKET_FEED_MIN_STALE_SECONDS,
        (timeframe_seconds * 2.0) + TRADE_MARKET_FEED_EXTRA_SECONDS,
    )
    latest_candle_age_seconds = max(0.0, time.time() - latest_candle_time)
    return latest_candle_age_seconds <= stale_after_seconds


def _request_backend_market_snapshot(symbol: str, timeframe: str, bars: int):
    try:
        response = requests.get(
            f"{TRADE_SERVICE_CONFIG['backend_base_url']}/internal/market/snapshot",
            params={
                'symbol': symbol,
                'timeframe': timeframe,
                'bars': max(1, int(bars or 1)),
            },
            headers=_build_trade_internal_headers(),
            timeout=2.5,
        )
        payload = response.json()
    except Exception as error:
        return {
            'ready': False,
            'request_status': 'error',
            'error': f'Could not reach backend market snapshot: {error}',
            'candles': [],
        }

    if not response.ok:
        return {
            'ready': False,
            'request_status': 'error',
            'error': _trim_text(payload.get('error')) or f'Backend market snapshot failed with {response.status_code}.',
            'candles': [],
        }

    return {
        'ready': bool(payload.get('ready')),
        'request_status': _trim_text(payload.get('request_status')) or ('ready' if payload.get('ready') else 'waiting'),
        'error': payload.get('error'),
        'candles': list(payload.get('candles') or []),
        'bars_loaded': int(payload.get('bars_loaded') or len(payload.get('candles') or [])),
        'last_update_at': payload.get('last_update_at'),
        'latest_candle_time': payload.get('latest_candle_time'),
    }


def _ensure_trade_market_data(symbol: str, timeframe: str, bars: int):
    if _is_trade_isolated_service_mode():
        trade_state = state.trade
        cached_symbol = _trim_text(getattr(trade_state, 'market_snapshot_symbol', '')).upper()
        cached_timeframe = _trim_text(getattr(trade_state, 'market_snapshot_timeframe', '')).upper()
        cached_candles = list(getattr(trade_state, 'market_snapshot_candles', []) or [])
        if (
            cached_symbol == _trim_text(symbol).upper()
            and cached_timeframe == _trim_text(timeframe).upper()
            and _is_trade_snapshot_fresh(symbol, timeframe, cached_candles)
        ):
            sliced_candles = cached_candles[-max(1, int(bars or 1)):]
            return {
                'ready': True,
                'request_status': 'ready',
                'error': None,
                'candles': sliced_candles,
                'bars_loaded': len(sliced_candles),
                'last_update_at': trade_state.market_last_update_at,
                'latest_candle_time': trade_state.market_latest_candle_time,
            }
        return _request_backend_market_snapshot(symbol, timeframe, bars)

    return ensure_market_data(symbol, timeframe, bars, source='trade_runtime')


def _freeze_latest_runtime_signal_candle(candles: list | None):
    safe_candles = [dict(entry or {}) for entry in list(candles or [])]
    if not safe_candles:
        return safe_candles

    last_candle = dict(safe_candles[-1] or {})
    open_price = _parse_optional_float(last_candle.get('open'))
    if open_price is None:
        return safe_candles

    # Live runtime receives the currently forming candle. For `next_bar_open`
    # strategies, signal generation must stay anchored to the previously closed
    # candle while still letting the pending action execute on the new bar.
    # Freezing the last candle at its open preserves that contract and avoids
    # opening/closing from an unfinished bar that the backtest would not yet use
    # as a finalized signal source.
    last_candle['high'] = open_price
    last_candle['low'] = open_price
    last_candle['close'] = open_price
    safe_candles[-1] = last_candle
    return safe_candles


def _get_trade_market_feed_snapshot():
    trade_state = state.trade
    now = time.time()
    enabled_sleeves = [
        entry for entry in list(trade_state.sleeves or [])
        if entry.get('enabled') is not False
    ]
    min_timeframe_seconds = min(
        (_timeframe_to_seconds(entry.get('timeframe')) for entry in enabled_sleeves),
        default=60,
    )
    heartbeat_at = trade_state.bridge_last_heartbeat_at
    if heartbeat_at is None and not _is_trade_isolated_service_mode():
        heartbeat_at = getattr(state.bridge, 'ea_last_heartbeat_at', None)
    heartbeat_timeout = max(
        1.0,
        float(
            trade_state.bridge_timeout_seconds
            or (getattr(state.bridge, 'ea_timeout_seconds', None) if not _is_trade_isolated_service_mode() else None)
            or 8.0
        ),
    )
    heartbeat_age_seconds = (
        max(0.0, now - float(heartbeat_at))
        if heartbeat_at is not None else None
    )
    bridge_online = bool(trade_state.bridge_online)
    if not bridge_online and heartbeat_at and heartbeat_age_seconds is not None and heartbeat_age_seconds <= heartbeat_timeout:
        bridge_online = True
    last_update_at = trade_state.market_last_update_at
    if last_update_at is None and not _is_trade_isolated_service_mode():
        last_update_at = getattr(state.market, 'last_update_at', None)
    update_age_seconds = (
        max(0.0, now - float(last_update_at))
        if last_update_at is not None else None
    )
    latest_candle_time = trade_state.market_latest_candle_time
    if latest_candle_time is None and not _is_trade_isolated_service_mode():
        latest_candle_time = getattr(state.market, 'latest_candle_time', None)
    latest_candle_age_seconds = (
        max(0.0, now - float(latest_candle_time))
        if latest_candle_time is not None else None
    )
    stale_after_seconds = max(
        TRADE_MARKET_FEED_MIN_STALE_SECONDS,
        (min_timeframe_seconds * 2.0) + TRADE_MARKET_FEED_EXTRA_SECONDS,
    )
    waiting_grace_seconds = max(
        TRADE_MARKET_FEED_GRACE_SECONDS,
        min_timeframe_seconds * 0.5,
    )

    if not trade_state.armed:
        return {
            'status': 'idle',
            'detail': 'Trade runtime is disarmed.',
            'bridge_online': bridge_online,
            'heartbeat_age_seconds': heartbeat_age_seconds,
            'last_update_at': last_update_at,
            'update_age_seconds': update_age_seconds,
            'latest_candle_time': latest_candle_time,
            'latest_candle_age_seconds': latest_candle_age_seconds,
            'stale_after_seconds': stale_after_seconds,
            'auto_sanitized': False,
        }

    if not enabled_sleeves:
        return {
            'status': 'idle',
            'detail': 'Trade runtime is armed without any enabled sleeve.',
            'bridge_online': bridge_online,
            'heartbeat_age_seconds': heartbeat_age_seconds,
            'last_update_at': last_update_at,
            'update_age_seconds': update_age_seconds,
            'latest_candle_time': latest_candle_time,
            'latest_candle_age_seconds': latest_candle_age_seconds,
            'stale_after_seconds': stale_after_seconds,
            'auto_sanitized': False,
        }

    armed_at = trade_state.last_armed_at or now
    time_since_armed = max(0.0, now - float(armed_at))
    market_history_ready = bool(trade_state.market_history_ready)
    if not market_history_ready and not _is_trade_isolated_service_mode():
        market_history_ready = bool(getattr(state.bridge, 'history_ready', False))

    if not market_history_ready:
        return {
            'status': 'waiting',
            'detail': 'Waiting for market history to be ready.',
            'bridge_online': bridge_online,
            'heartbeat_age_seconds': heartbeat_age_seconds,
            'last_update_at': last_update_at,
            'update_age_seconds': update_age_seconds,
            'latest_candle_time': latest_candle_time,
            'latest_candle_age_seconds': latest_candle_age_seconds,
            'stale_after_seconds': stale_after_seconds,
            'auto_sanitized': False,
        }

    if update_age_seconds is None or latest_candle_time is None:
        return {
            'status': 'waiting' if time_since_armed <= waiting_grace_seconds else 'stale',
            'detail': (
                'Waiting for the first live market update.'
                if time_since_armed <= waiting_grace_seconds
                else 'Live market updates never became available after the runtime was armed.'
            ),
            'bridge_online': bridge_online,
            'heartbeat_age_seconds': heartbeat_age_seconds,
            'last_update_at': last_update_at,
            'update_age_seconds': update_age_seconds,
            'latest_candle_time': latest_candle_time,
            'latest_candle_age_seconds': latest_candle_age_seconds,
            'stale_after_seconds': stale_after_seconds,
            'auto_sanitized': False,
        }

    feed_stale = (
        update_age_seconds > stale_after_seconds
        and latest_candle_age_seconds is not None
        and latest_candle_age_seconds > stale_after_seconds
        and bridge_online
    )
    if feed_stale:
        likely_weekend_market_close = _is_weekend_market_closure_window(now, latest_candle_time)
        return {
            'status': ('closed' if likely_weekend_market_close else 'stale'),
            'detail': (
                f'Market feed paused since the weekly close. Last candle is {latest_candle_age_seconds:.1f}s old while the MT5 bridge stayed online.'
                if likely_weekend_market_close
                else f'Market feed stopped updating for {update_age_seconds:.1f}s while the MT5 bridge stayed online.'
            ),
            'bridge_online': bridge_online,
            'heartbeat_age_seconds': heartbeat_age_seconds,
            'last_update_at': last_update_at,
            'update_age_seconds': update_age_seconds,
            'latest_candle_time': latest_candle_time,
            'latest_candle_age_seconds': latest_candle_age_seconds,
            'stale_after_seconds': stale_after_seconds,
            'auto_sanitized': False,
        }

    return {
        'status': 'healthy',
        'detail': 'Live market updates are flowing normally.',
        'bridge_online': bridge_online,
        'heartbeat_age_seconds': heartbeat_age_seconds,
        'last_update_at': last_update_at,
        'update_age_seconds': update_age_seconds,
        'latest_candle_time': latest_candle_time,
        'latest_candle_age_seconds': latest_candle_age_seconds,
        'stale_after_seconds': stale_after_seconds,
        'auto_sanitized': False,
    }


def _reconcile_trade_market_feed():
    trade_state = state.trade
    snapshot = _get_trade_market_feed_snapshot()
    status = _trim_text(snapshot.get('status')).lower() or 'idle'
    detail = _trim_text(snapshot.get('detail')) or None
    trade_state.market_feed_status = status
    trade_state.market_feed_issue = detail if status in {'waiting', 'stale', 'closed'} else None

    if status in {'stale', 'closed'} and trade_state.armed:
        now = time.time()
        if _trim_text(trade_state.status).lower() != 'market_feed_stale' or detail != _trim_text(trade_state.market_feed_issue):
            record_trade_runtime_event(
                'pause_market_feed',
                detail or 'Trade runtime paused live execution because the market feed became stale.',
                update_age_seconds=snapshot.get('update_age_seconds'),
                heartbeat_age_seconds=snapshot.get('heartbeat_age_seconds'),
            )
        trade_state.live = False
        trade_state.status = 'market_feed_stale'
        trade_state.last_market_sanitize_at = now
        if (
            _trim_text(trade_state.last_error).lower().startswith('market feed')
            or _is_nonfatal_idle_trade_command_poll_error_message(trade_state.last_error)
        ):
            trade_state.last_error = None
        snapshot['auto_sanitized'] = False
        return snapshot

    if status == 'healthy' and _trim_text(trade_state.status).lower() == 'market_feed_stale':
        trade_state.status = 'live' if trade_state.armed else 'idle'
        trade_state.live = bool(trade_state.armed)
        if (
            _trim_text(trade_state.last_error) == _trim_text(detail)
            or _trim_text(trade_state.last_error).lower().startswith('market feed')
        ):
            trade_state.last_error = None

    if status in {'idle', 'healthy'} and (
        _trim_text(trade_state.last_error).lower().startswith('market feed')
        or _is_nonfatal_idle_trade_command_poll_error_message(trade_state.last_error)
    ):
        trade_state.last_error = None

    return snapshot


def _get_default_trade_bars():
    return max(1, int(TRADE_RUNTIME_DEFAULT_BARS))


def _build_strategy_instance(strategy_payload: dict | None, applied_indicators: list[dict] | None = None):
    safe_payload = dict(strategy_payload or {})
    params = None
    try:
        try:
            from .. import strategy_backend
        except ImportError:
            import strategy_backend

        params = strategy_backend.build_strategy_params(
            strategy_backend.StrategyPayload.model_validate(safe_payload)
        )
        params = resolve_strategy_param_aliases(params, applied_indicators or [])
    except Exception:
        long_section = dict(safe_payload.get('long') or {})
        short_section = dict(safe_payload.get('short') or {})
        other_section = dict(safe_payload.get('other') or {})
        params = {
            'open_long_condition': str(long_section.get('openIf') or 'False'),
            'close_long_condition': str(long_section.get('closeIf') or 'False'),
            'open_short_condition': str(short_section.get('openIf') or 'False'),
            'close_short_condition': str(short_section.get('closeIf') or 'False'),
            'open_trade_price_long': str(long_section.get('openPrice') or 'close[0]'),
            'open_trade_price_short': str(short_section.get('openPrice') or 'close[0]'),
            'close_trade_price_long': str(long_section.get('closePrice') or 'close[0]'),
            'close_trade_price_short': str(short_section.get('closePrice') or 'close[0]'),
            'stop_gain_long_price': str(long_section.get('gainPrice') or ''),
            'stop_loss_long_price': str(long_section.get('lossPrice') or ''),
            'stop_gain_short_price': str(short_section.get('gainPrice') or ''),
            'stop_loss_short_price': str(short_section.get('lossPrice') or ''),
            'trailing_stop_long_price': str(long_section.get('trailingPrice') or ''),
            'trailing_stop_short_price': str(short_section.get('trailingPrice') or ''),
            'allow_invertion': bool(other_section.get('allowInversion') or other_section.get('allow_invertion') or False),
            'prioritize': str(other_section.get('priority') or 'Short').strip().lower() or 'short',
        }

    strategy = Strategy()
    strategy.set_params(
        **params,
        execution_mode='next_bar_open',
    )
    return strategy


def _derive_sleeve_decision(execution):
    history = getattr(execution, 'history', None)
    if history is None or getattr(history, 'empty', True):
        return {
            'status': 'idle',
            'decision': 'hold',
            'position': 0,
            'strategy_position': 0,
            'pending_action': None,
            'order_type': None,
            'bar_time': None,
        }

    last_row = history.iloc[-1].to_dict()
    pending_action = last_row.get('pending_action_kind')
    order_type = last_row.get('order_type')
    position = int(last_row.get('position') or 0)
    decision = pending_action or order_type or 'hold'

    return {
        'status': 'ready',
        'decision': str(decision),
        'position': position,
        'strategy_position': position,
        'pending_action': pending_action,
        'order_type': order_type,
        'bar_time': last_row.get('time'),
        'long_take_profit_price': last_row.get('long_take_profit_price'),
        'long_stop_loss_price': last_row.get('long_stop_loss_price'),
        'long_trailing_stop_price': last_row.get('long_trailing_stop_price'),
        'long_open_price': last_row.get('long_open_price'),
        'short_take_profit_price': last_row.get('short_take_profit_price'),
        'short_stop_loss_price': last_row.get('short_stop_loss_price'),
        'short_trailing_stop_price': last_row.get('short_trailing_stop_price'),
        'short_open_price': last_row.get('short_open_price'),
    }


def _decision_side(decision: str):
    normalized = _trim_text(decision).lower()
    if '_long' in normalized:
        return 'long'
    if '_short' in normalized:
        return 'short'
    return ''


def _protective_decision_for_side(side: str, stop_kind: str):
    safe_side = _trim_text(side).lower()
    safe_kind = _trim_text(stop_kind).lower()
    if safe_side not in {'long', 'short'} or safe_kind not in {'gain', 'loss', 'trail'}:
        return ''
    return f'stop_{safe_side}_{safe_kind}'


def _rebase_live_protective_prices(decision_payload: dict | None, previous_state: dict | None):
    safe_decision = dict(decision_payload or {})
    safe_previous_state = dict(previous_state or {})
    if _trim_text(state.trade.execution_mode).lower() != 'live_mt5':
        return safe_decision

    live_side = _trim_text(
        safe_previous_state.get('live_entry_side')
        or safe_previous_state.get('broker_position_side')
        or safe_previous_state.get('actual_position_side')
    ).lower()
    if live_side not in {'long', 'short'}:
        return safe_decision

    live_entry_fill_price = _parse_optional_float(safe_previous_state.get('live_entry_fill_price'))
    if live_entry_fill_price is None:
        return safe_decision

    strategy_entry_price = _parse_optional_float(
        safe_decision.get(f'{live_side}_open_price')
    )
    if strategy_entry_price is None:
        strategy_entry_price = _parse_optional_float(safe_previous_state.get('strategy_entry_price'))
    if strategy_entry_price is None:
        return safe_decision

    protective_shift = live_entry_fill_price - strategy_entry_price
    for suffix in ('take_profit_price', 'stop_loss_price', 'trailing_stop_price'):
        key = f'{live_side}_{suffix}'
        candidate = _parse_optional_float(safe_decision.get(key))
        if candidate is not None:
            safe_decision[key] = candidate + protective_shift
            continue
        previous_candidate = _parse_optional_float(safe_previous_state.get(key))
        if previous_candidate is not None:
            safe_decision[key] = previous_candidate

    safe_decision['live_entry_fill_price'] = live_entry_fill_price
    safe_decision['strategy_entry_price'] = strategy_entry_price
    safe_decision['protective_price_shift'] = protective_shift
    safe_decision['live_entry_side'] = live_side
    safe_decision['live_entry_bar_time'] = safe_previous_state.get('live_entry_bar_time')
    return safe_decision


def _resolve_live_protective_hit(decision_payload: dict | None, previous_state: dict | None, sleeve: dict | None):
    safe_decision = dict(decision_payload or {})
    safe_previous_state = dict(previous_state or {})
    safe_sleeve = dict(sleeve or {})
    live_side = _trim_text(
        safe_decision.get('live_entry_side')
        or safe_previous_state.get('live_entry_side')
        or safe_previous_state.get('broker_position_side')
        or safe_previous_state.get('actual_position_side')
    ).lower()
    if live_side not in {'long', 'short'}:
        return ''

    candle = _get_trade_market_snapshot_tail(
        safe_decision.get('symbol') or safe_previous_state.get('symbol') or safe_sleeve.get('symbol'),
        safe_decision.get('timeframe') or safe_previous_state.get('timeframe') or safe_sleeve.get('timeframe'),
    )
    candle_high = _parse_optional_float((candle or {}).get('high'))
    candle_low = _parse_optional_float((candle or {}).get('low'))
    if candle_high is None or candle_low is None:
        return ''

    stop_loss = _parse_optional_float(safe_decision.get(f'{live_side}_stop_loss_price'))
    trailing_stop = _parse_optional_float(safe_decision.get(f'{live_side}_trailing_stop_price'))
    take_profit = _parse_optional_float(safe_decision.get(f'{live_side}_take_profit_price'))
    live_entry_bar_time = safe_decision.get('live_entry_bar_time')
    decision_bar_time = safe_decision.get('bar_time')
    is_entry_bar = live_entry_bar_time not in (None, '') and decision_bar_time == live_entry_bar_time

    if live_side == 'long':
        if stop_loss is not None and candle_low <= stop_loss:
            return _protective_decision_for_side(live_side, 'loss')
        if trailing_stop is not None and not is_entry_bar and candle_low <= trailing_stop:
            return _protective_decision_for_side(live_side, 'trail')
        if take_profit is not None and candle_high >= take_profit:
            return _protective_decision_for_side(live_side, 'gain')
        return ''

    if stop_loss is not None and candle_high >= stop_loss:
        return _protective_decision_for_side(live_side, 'loss')
    if trailing_stop is not None and not is_entry_bar and candle_high >= trailing_stop:
        return _protective_decision_for_side(live_side, 'trail')
    if take_profit is not None and candle_low <= take_profit:
        return _protective_decision_for_side(live_side, 'gain')
    return ''


def _normalize_live_decision_against_broker(sleeve: dict | None, decision_payload: dict | None, previous_state: dict | None = None):
    safe_sleeve = dict(sleeve or {})
    safe_decision = dict(decision_payload or {})
    safe_previous_state = dict(previous_state or {})
    normalized_decision = _trim_text(safe_decision.get('decision')).lower()
    if _trim_text(state.trade.execution_mode).lower() != 'live_mt5':
        return safe_decision

    broker_positions = _list_broker_positions_for_sleeve(safe_sleeve)
    broker_summary = _summarize_broker_positions(broker_positions)
    live_side = _trim_text(
        broker_summary.get('aggregate_side')
        or safe_previous_state.get('live_entry_side')
        or safe_previous_state.get('broker_position_side')
        or safe_previous_state.get('actual_position_side')
    ).lower()
    safe_decision = _rebase_live_protective_prices(safe_decision, safe_previous_state)
    normalized_decision = _trim_text(safe_decision.get('decision')).lower()

    protective_hit = ''
    if broker_positions:
        protective_hit = _resolve_live_protective_hit(safe_decision, safe_previous_state, safe_sleeve)
        if protective_hit:
            safe_decision['decision'] = protective_hit
            safe_decision['pending_action'] = None
            safe_decision['order_type'] = protective_hit
            return safe_decision

    stop_decisions = {
        'stop_long_gain',
        'stop_long_loss',
        'stop_long_trail',
        'stop_short_gain',
        'stop_short_loss',
        'stop_short_trail',
    }
    close_decisions = {
        'close_long',
        'close_short',
        *stop_decisions,
    }

    if normalized_decision not in close_decisions:
        if broker_positions and live_side in {'long', 'short'} and int(safe_decision.get('strategy_position') or 0) == 0:
            live_position = 1 if live_side == 'long' else -1
            safe_decision['position'] = live_position
            safe_decision['strategy_position'] = live_position
        return safe_decision

    if not broker_positions:
        safe_decision['decision'] = 'hold'
        safe_decision['pending_action'] = None
        safe_decision['order_type'] = None
        return safe_decision

    expected_side = _decision_side(normalized_decision)
    matching_side_positions = [
        dict(entry or {})
        for entry in broker_positions
        if _trim_text(entry.get('side')).lower() == expected_side
    ]
    if not matching_side_positions:
        safe_decision['decision'] = 'hold'
        safe_decision['pending_action'] = None
        safe_decision['order_type'] = None
        if broker_positions and live_side in {'long', 'short'} and normalized_decision in stop_decisions:
            live_position = 1 if live_side == 'long' else -1
            safe_decision['position'] = live_position
            safe_decision['strategy_position'] = live_position
    elif normalized_decision in stop_decisions and not protective_hit and live_side in {'long', 'short'}:
        live_position = 1 if live_side == 'long' else -1
        safe_decision['decision'] = 'hold'
        safe_decision['pending_action'] = None
        safe_decision['order_type'] = None
        safe_decision['position'] = live_position
        safe_decision['strategy_position'] = live_position
    return safe_decision


def _map_decision_to_order_intent(decision: str):
    normalized = _trim_text(decision).lower()
    mapping = {
        'open_long': ('open', 'long'),
        'invert_to_long': ('open', 'long'),
        'open_short': ('open', 'short'),
        'invert_to_short': ('open', 'short'),
        'close_long': ('close', 'long'),
        'close_short': ('close', 'short'),
        'stop_long_gain': ('close', 'long'),
        'stop_long_loss': ('close', 'long'),
        'stop_long_trail': ('close', 'long'),
        'stop_short_gain': ('close', 'short'),
        'stop_short_loss': ('close', 'short'),
        'stop_short_trail': ('close', 'short'),
    }
    return mapping.get(normalized)


def _decision_requires_new_candle(decision: str):
    return _trim_text(decision).lower() in BAR_OPEN_ONLY_DECISIONS


def _compute_intent_expiration_timestamp(intent: dict | None):
    safe_intent = dict(intent or {})
    if not _decision_requires_new_candle(safe_intent.get('decision')):
        return None

    bar_time = safe_intent.get('bar_time')
    if bar_time is None:
        return None

    signal_validity_seconds = int(getattr(state.trade, 'signal_validity_seconds', 10) or 0)
    if signal_validity_seconds <= 0:
        timeframe_seconds = _timeframe_to_seconds(safe_intent.get('timeframe'))
        return float(bar_time) + float(timeframe_seconds)

    return float(bar_time) + float(signal_validity_seconds)


def _is_intent_expired(intent: dict | None):
    expiration_at = _compute_intent_expiration_timestamp(intent)
    if expiration_at is None:
        return False

    safe_intent = dict(intent or {})
    execution_mode = _trim_text(
        safe_intent.get('execution_mode') or getattr(state.trade, 'execution_mode', '')
    ).lower()
    signal_validity_seconds = max(
        0,
        int(getattr(state.trade, 'signal_validity_seconds', 10) or 0),
    )

    if execution_mode == 'live_mt5' and signal_validity_seconds > 0:
        created_at = safe_intent.get('created_at')
        if created_at is not None:
            try:
                created_at = float(created_at)
                if created_at >= 1_000_000_000:
                    return time.time() >= (created_at + float(signal_validity_seconds))
            except Exception:
                pass

    latest_candle_time = getattr(state.trade, 'market_latest_candle_time', None)
    if latest_candle_time is not None:
        return float(latest_candle_time) >= float(expiration_at)

    # Market bar times are the canonical time domain for signal expiry.
    # Fall back to wall clock only when the payload clearly carries a real
    # epoch timestamp; synthetic test/runtime bar indexes should not expire
    # just because wall time is far ahead.
    if float(expiration_at) >= 1_000_000_000:
        return time.time() >= float(expiration_at)

    return False


def _should_gate_bar_open_decision(trigger: str, decision: str):
    if not _decision_requires_new_candle(decision):
        return False

    safe_trigger = _trim_text(trigger).lower()
    if safe_trigger == 'history_loaded':
        return True
    if safe_trigger != 'candle_update':
        return False

    return not bool(getattr(state.trade, 'last_market_event_new_candle', False))


def _list_pending_symbol_exposures(symbol: str, excluding_sleeve_id: str = ''):
    trade_state = state.trade
    safe_symbol = _trim_text(symbol).upper()
    safe_excluding_sleeve_id = _trim_text(excluding_sleeve_id)
    exposures = []

    for sleeve_id, sleeve_state in dict(trade_state.sleeve_states or {}).items():
        safe_sleeve_id = _trim_text(sleeve_id)
        if safe_sleeve_id == safe_excluding_sleeve_id:
            continue
        if _trim_text((sleeve_state or {}).get('symbol')).upper() != safe_symbol:
            continue
        position = int((sleeve_state or {}).get('position') or 0)
        if position > 0:
            exposures.append({
                'source': 'sleeve_state',
                'sleeve_id': safe_sleeve_id,
                'side': 'long',
                'pipeline_id': _trim_text((sleeve_state or {}).get('pipeline_id')),
                'portfolio_id': _trim_text((sleeve_state or {}).get('portfolio_id')),
            })
        elif position < 0:
            exposures.append({
                'source': 'sleeve_state',
                'sleeve_id': safe_sleeve_id,
                'side': 'short',
                'pipeline_id': _trim_text((sleeve_state or {}).get('pipeline_id')),
                'portfolio_id': _trim_text((sleeve_state or {}).get('portfolio_id')),
            })

    for entry in list(trade_state.order_intents or []):
        safe_sleeve_id = _trim_text(entry.get('sleeve_id'))
        if safe_sleeve_id == safe_excluding_sleeve_id:
            continue
        if _trim_text(entry.get('symbol')).upper() != safe_symbol:
            continue
        if _trim_text(entry.get('action')).lower() != 'open':
            continue
        status = _trim_text(entry.get('status')).lower()
        if status in {'filled', 'rejected'}:
            continue
        exposures.append({
            'source': 'intent',
            'sleeve_id': safe_sleeve_id,
            'side': _trim_text(entry.get('side')).lower(),
            'pipeline_id': _trim_text(entry.get('pipeline_id')),
            'portfolio_id': _trim_text(entry.get('portfolio_id')),
        })

    for entry in list(trade_state.order_commands or []):
        safe_sleeve_id = _trim_text(entry.get('sleeve_id'))
        if safe_sleeve_id == safe_excluding_sleeve_id:
            continue
        if _trim_text(entry.get('symbol')).upper() != safe_symbol:
            continue
        if _trim_text(entry.get('action')).lower() != 'open':
            continue
        status = _trim_text(entry.get('status')).lower()
        if status in {'filled', 'rejected', 'stale'}:
            continue
        exposures.append({
            'source': 'command',
            'sleeve_id': safe_sleeve_id,
            'side': _trim_text(entry.get('side')).lower(),
            'pipeline_id': _trim_text(entry.get('pipeline_id')),
            'portfolio_id': _trim_text(entry.get('portfolio_id')),
        })

    return exposures


def _can_queue_order_intent(intent: dict):
    trade_state = state.trade
    portfolio_mode = _normalize_trade_portfolio_mode(
        (intent or {}).get('portfolio_mode') or getattr(trade_state, 'mode', 'parallel_sleeves')
    )
    policy = _normalize_same_symbol_execution_policy(
        getattr(trade_state, 'same_symbol_execution_policy', 'independent')
    )

    action = _trim_text((intent or {}).get('action')).lower()
    if action != 'open':
        return True, None

    symbol = _trim_text((intent or {}).get('symbol')).upper()
    side = _trim_text((intent or {}).get('side')).lower()
    sleeve_id = _trim_text((intent or {}).get('sleeve_id'))
    pipeline_id = _trim_text((intent or {}).get('pipeline_id'))
    exposures = _list_pending_symbol_exposures(symbol, excluding_sleeve_id=sleeve_id)
    if not exposures:
        return True, None

    if portfolio_mode == 'shared_pipe' and any(
        _trim_text(entry.get('pipeline_id')) == pipeline_id
        for entry in exposures
    ):
        return False, f'portfolio mode shared_pipe blocked open on {symbol}: another sleeve already owns the shared symbol lane'

    if policy == 'single_active_per_symbol':
        return False, f'same-symbol policy blocked open on {symbol}: another sleeve is already active there'

    if policy == 'block_conflicts':
        if any(_trim_text(entry.get('side')).lower() and _trim_text(entry.get('side')).lower() != side for entry in exposures):
            return False, f'same-symbol policy blocked conflicting {side} open on {symbol}'

    return True, None


def _build_order_intent(sleeve_id: str, sleeve: dict, sleeve_state: dict, trigger: str):
    decision = _trim_text(sleeve_state.get('decision'))
    mapped = _map_decision_to_order_intent(decision)
    if not mapped:
        return None
    if _should_gate_bar_open_decision(trigger, decision):
        return None

    action, side = mapped
    bar_time = sleeve_state.get('last_bar_time')
    target_broker_position = None
    if action == 'close':
        target_broker_position = _select_broker_position_for_close({
            'sleeve_id': sleeve_id,
            'side': side,
        })
    target_broker_ticket = _trim_text((target_broker_position or {}).get('ticket')) or None
    fingerprint = f'{sleeve_id}|{action}|{side}|{bar_time}'
    if action == 'close' and target_broker_ticket:
        fingerprint = f'{fingerprint}|{target_broker_ticket}'
    exit_reason = _derive_trade_exit_reason({
        'action': action,
        'decision': decision,
    })
    expected_exit_price = None
    if action == 'close':
        if exit_reason == 'gain':
            expected_exit_price = (
                sleeve_state.get('long_take_profit_price')
                if side == 'long'
                else sleeve_state.get('short_take_profit_price')
            )
        elif exit_reason == 'loss':
            expected_exit_price = (
                sleeve_state.get('long_stop_loss_price')
                if side == 'long'
                else sleeve_state.get('short_stop_loss_price')
            )
        elif exit_reason == 'trail':
            expected_exit_price = (
                sleeve_state.get('long_trailing_stop_price')
                if side == 'long'
                else sleeve_state.get('short_trailing_stop_price')
            )
    cycle_id = _trim_text(sleeve_state.get('current_cycle_id'))
    if action == 'open':
        cycle_id = _trim_text(sleeve_state.get('pending_cycle_id')) or cycle_id or _next_trade_cycle_id(sleeve_id)
        sleeve_state['pending_cycle_id'] = cycle_id
    elif action == 'close':
        cycle_id = _trim_text(sleeve_state.get('current_cycle_id')) or _trim_text(sleeve_state.get('pending_cycle_id'))
    return {
        'id': f'oi_{int(time.time() * 1000)}_{sleeve_id}',
        'fingerprint': fingerprint,
        'status': 'queued',
        'portfolio_mode': _trim_text(sleeve.get('portfolio_mode')) or state.trade.mode,
        'execution_mode': state.trade.execution_mode,
        'portfolio_id': _trim_text(sleeve.get('portfolio_id')) or None,
        'portfolio_label': _trim_text(sleeve.get('portfolio_label')) or None,
        'pipeline_id': _trim_text(sleeve.get('pipeline_id')) or None,
        'pipeline_label': _trim_text(sleeve.get('pipeline_label')) or None,
        'sleeve_id': sleeve_id,
        'sleeve_label': sleeve.get('label'),
        'source_strategy_id': sleeve.get('source_strategy_id'),
        'symbol': sleeve_state.get('symbol') or sleeve.get('symbol'),
        'timeframe': sleeve_state.get('timeframe') or sleeve.get('timeframe'),
        'volume_mode': _trim_text(sleeve.get('volume_mode')) or 'fixed_volume',
        'cycle_id': cycle_id or None,
        'exit_reason': exit_reason or None,
        'expected_exit_price': (
            None if expected_exit_price in (None, '') else float(expected_exit_price)
        ),
        'take_profit_price': (
            sleeve_state.get('long_take_profit_price')
            if side == 'long' and action == 'open'
            else sleeve_state.get('short_take_profit_price')
            if side == 'short' and action == 'open'
            else None
        ),
        'stop_loss_price': (
            sleeve_state.get('long_stop_loss_price')
            if side == 'long' and action == 'open'
            else sleeve_state.get('short_stop_loss_price')
            if side == 'short' and action == 'open'
            else None
        ),
        'strategy_entry_price': (
            sleeve_state.get('long_open_price')
            if side == 'long' and action == 'open'
            else sleeve_state.get('short_open_price')
            if side == 'short' and action == 'open'
            else None
        ),
        'action': action,
        'side': side,
        'broker_ticket': target_broker_ticket,
        'decision': sleeve_state.get('decision'),
        'bar_time': bar_time,
        'expires_at': _compute_intent_expiration_timestamp({
            'decision': sleeve_state.get('decision'),
            'bar_time': bar_time,
            'timeframe': sleeve_state.get('timeframe') or sleeve.get('timeframe'),
        }),
        'trigger': trigger,
        'created_at': time.time(),
    }


def _build_sleeve_magic(sleeve_id: str):
    safe_sleeve = _trim_text(sleeve_id) or 'trade'
    hash_value = 2166136261
    for character in safe_sleeve:
        hash_value ^= ord(character)
        hash_value *= 16777619
        hash_value &= 0x7FFFFFFF
    return max(1000, hash_value)


def _next_trade_cycle_id(sleeve_id: str):
    trade_state = state.trade
    trade_state.trade_cycle_sequence = int(getattr(trade_state, 'trade_cycle_sequence', 0) or 0) + 1
    return f'{_trim_text(sleeve_id) or "sleeve"}-cycle-{trade_state.trade_cycle_sequence}'


def _list_broker_positions_for_sleeve(sleeve: dict | None):
    safe_sleeve = dict(sleeve or {})
    safe_symbol = _trim_text(safe_sleeve.get('symbol')).upper()
    target_magic = _build_sleeve_magic(_trim_text(safe_sleeve.get('id')))
    positions = []
    for entry in list(state.trade.broker_positions or []):
        if _trim_text(entry.get('symbol')).upper() != safe_symbol:
            continue
        if int(entry.get('magic') or 0) != target_magic:
            continue
        positions.append(dict(entry or {}))
    return positions


def _sort_broker_positions(positions: list | None):
    return sorted(
        [dict(entry or {}) for entry in list(positions or [])],
        key=lambda entry: (_trim_text(entry.get('ticket')), _trim_text(entry.get('side')).lower()),
    )


def _list_active_close_target_tickets_for_sleeve(sleeve_id: str):
    safe_sleeve_id = _trim_text(sleeve_id)
    if not safe_sleeve_id:
        return set()

    tickets = set()
    for entry in _list_active_order_intents_for_sleeve(safe_sleeve_id, action='close'):
        ticket = _trim_text((entry or {}).get('broker_ticket'))
        if ticket:
            tickets.add(ticket)
    for entry in _list_active_order_commands_for_sleeve(safe_sleeve_id, action='close'):
        ticket = _trim_text((entry or {}).get('broker_ticket'))
        if ticket:
            tickets.add(ticket)
    return tickets


def _summarize_broker_positions(broker_positions: list | None):
    safe_positions = _sort_broker_positions(broker_positions)
    sides = [
        _trim_text(entry.get('side')).lower()
        for entry in safe_positions
        if _trim_text(entry.get('side')).lower() in {'long', 'short'}
    ]
    unique_sides = sorted(set(sides))
    aggregate_side = unique_sides[0] if len(unique_sides) == 1 else ('multiple' if unique_sides else 'flat')
    tickets = [
        _trim_text(entry.get('ticket'))
        for entry in safe_positions
        if _trim_text(entry.get('ticket'))
    ]
    return {
        'positions': safe_positions,
        'count': len(safe_positions),
        'sides': sides,
        'unique_sides': unique_sides,
        'aggregate_side': aggregate_side,
        'tickets': tickets,
    }


def _select_broker_position_for_close(intent: dict | None):
    safe_intent = dict(intent or {})
    sleeve = _find_trade_sleeve(_trim_text(safe_intent.get('sleeve_id'))) or {}
    sleeve_id = _trim_text(safe_intent.get('sleeve_id'))
    summary = _summarize_broker_positions(_list_broker_positions_for_sleeve(sleeve))
    broker_positions = list(summary.get('positions') or [])
    if not broker_positions:
        return None

    requested_ticket = _trim_text(safe_intent.get('broker_ticket'))
    if requested_ticket:
        for entry in broker_positions:
            if _trim_text(entry.get('ticket')) == requested_ticket:
                return dict(entry or {})

    targeted_tickets = _list_active_close_target_tickets_for_sleeve(sleeve_id)
    available_positions = [
        dict(entry or {})
        for entry in broker_positions
        if _trim_text(entry.get('ticket')) not in targeted_tickets
    ]
    candidate_positions = available_positions or broker_positions

    requested_side = _trim_text(safe_intent.get('side')).lower()
    matching_side_positions = [
        dict(entry or {})
        for entry in candidate_positions
        if _trim_text(entry.get('side')).lower() == requested_side
    ]
    if matching_side_positions:
        return matching_side_positions[0]

    if len(candidate_positions) == 1:
        return dict(candidate_positions[0] or {})

    return None


def _append_resume_close_intent(sleeve: dict, broker_position: dict, reason: str):
    sleeve_id = _trim_text(sleeve.get('id'))
    side = _trim_text(broker_position.get('side')).lower()
    symbol = _trim_text(sleeve.get('symbol')).upper()
    fingerprint = f'{sleeve_id}|close|{side}|resume|{_trim_text(broker_position.get("ticket"))}'
    intent = {
        'id': f'oi_resume_{int(time.time() * 1000)}_{sleeve_id}',
        'fingerprint': fingerprint,
        'status': 'queued',
        'portfolio_mode': _trim_text(sleeve.get('portfolio_mode')) or state.trade.mode,
        'execution_mode': state.trade.execution_mode,
        'portfolio_id': _trim_text(sleeve.get('portfolio_id')) or None,
        'portfolio_label': _trim_text(sleeve.get('portfolio_label')) or None,
        'pipeline_id': _trim_text(sleeve.get('pipeline_id')) or None,
        'pipeline_label': _trim_text(sleeve.get('pipeline_label')) or None,
        'sleeve_id': sleeve_id,
        'sleeve_label': sleeve.get('label'),
        'source_strategy_id': sleeve.get('source_strategy_id'),
        'symbol': symbol,
        'timeframe': _trim_text(sleeve.get('timeframe')).upper() or 'M1',
        'volume_mode': _trim_text(sleeve.get('volume_mode')) or 'fixed_volume',
        'cycle_id': None,
        'action': 'close',
        'side': side,
        'decision': 'resume_close',
        'bar_time': None,
        'trigger': 'resume_policy',
        'created_at': time.time(),
        'resume_reason': reason,
        'broker_ticket': _trim_text(broker_position.get('ticket')) or None,
    }
    if _append_order_intent(intent) is not None:
        record_trade_runtime_event(
            'resume_close_intent',
            f'Resume policy queued a safety close for {sleeve.get("label") or sleeve_id}.',
            sleeve_id=sleeve_id,
            symbol=symbol,
            side=side,
            broker_ticket=intent.get('broker_ticket'),
            reason=reason,
        )


def _build_sleeve_reconciliation_state(sleeve: dict | None, sleeve_state: dict | None):
    safe_sleeve = dict(sleeve or {})
    safe_state = dict(sleeve_state or {})
    sleeve_id = _trim_text(safe_state.get('sleeve_id') or safe_sleeve.get('id'))
    broker_summary = _summarize_broker_positions(_list_broker_positions_for_sleeve(safe_sleeve))
    broker_positions = list(broker_summary.get('positions') or [])
    active_open_intents = _list_active_order_intents_for_sleeve(sleeve_id, action='open')
    active_open_commands = _list_active_order_commands_for_sleeve(sleeve_id, action='open')
    active_close_intents = _list_active_order_intents_for_sleeve(sleeve_id, action='close')
    active_close_commands = _list_active_order_commands_for_sleeve(sleeve_id, action='close')
    current_cycle_id = _trim_text(safe_state.get('current_cycle_id'))
    desired_position = int(safe_state.get('strategy_position') if safe_state.get('strategy_position') is not None else safe_state.get('position') or 0)
    desired_side = 'long' if desired_position > 0 else ('short' if desired_position < 0 else 'flat')
    broker_count = int(broker_summary.get('count') or 0)
    broker_tickets = list(broker_summary.get('tickets') or [])
    aggregate_side = _trim_text(broker_summary.get('aggregate_side')).lower() or 'flat'

    if broker_count == 0:
        if desired_side == 'flat':
            return {
                'status': 'match_flat',
                'desired_position': desired_position,
                'desired_side': desired_side,
                'actual_side': 'flat',
                'broker_position_count': 0,
                'broker_tickets': [],
                'detail': 'No broker position and runtime expects flat.',
                'should_queue_close': False,
            }
        return {
            'status': 'missing_broker_position',
            'desired_position': desired_position,
            'desired_side': desired_side,
            'actual_side': 'flat',
            'broker_position_count': 0,
            'broker_tickets': [],
            'detail': 'Runtime expects an open position, but broker is flat.',
            'should_queue_close': False,
            'broker_closed_detected': bool(current_cycle_id),
        }

    if (
        _trim_text(state.trade.execution_mode).lower() == 'live_mt5'
        and current_cycle_id
        and not active_close_intents
        and not active_close_commands
        and desired_side == 'flat'
        and aggregate_side in {'long', 'short'}
    ):
        desired_position = 1 if aggregate_side == 'long' else -1
        desired_side = aggregate_side

    broker_position = dict(broker_positions[0] or {})
    actual_side = _trim_text(broker_position.get('side')).lower() or 'flat'
    ticket = _trim_text(broker_position.get('ticket')) or None

    if broker_count > 1:
        actual_side = aggregate_side if aggregate_side in {'long', 'short'} else 'multiple'
        if desired_side == 'flat':
            return {
                'status': 'orphan_multiple_positions',
                'desired_position': desired_position,
                'desired_side': desired_side,
                'actual_side': actual_side,
                'broker_position_count': broker_count,
                'broker_tickets': broker_tickets,
                'detail': (
                    'Multiple broker positions exist for the sleeve while runtime expects flat.'
                    if actual_side == 'multiple'
                    else f'Multiple {actual_side} broker positions exist for the sleeve while runtime expects flat.'
                ),
                'should_queue_close': True,
                'close_targets': broker_positions,
            }

        if aggregate_side in {'long', 'short'} and desired_side == aggregate_side:
            return {
                'status': 'match_open_multiple',
                'desired_position': desired_position,
                'desired_side': desired_side,
                'actual_side': actual_side,
                'broker_position_count': broker_count,
                'broker_tickets': broker_tickets,
                'detail': f'Runtime direction matches {broker_count} broker positions on the {actual_side} side.',
                'should_queue_close': False,
            }

        close_targets = []
        if desired_side in {'long', 'short'}:
            close_targets = [
                dict(entry or {})
                for entry in broker_positions
                if _trim_text(entry.get('side')).lower() != desired_side
            ]
        if not close_targets:
            close_targets = broker_positions

        return {
            'status': 'conflicting_multiple_positions',
            'desired_position': desired_position,
            'desired_side': desired_side,
            'actual_side': actual_side,
            'broker_position_count': broker_count,
            'broker_tickets': broker_tickets,
            'detail': (
                'Multiple broker positions exist for the sleeve and conflict with the runtime direction.'
                if actual_side == 'multiple'
                else f'Multiple broker positions remain on the {actual_side} side while runtime expects {desired_side}.'
            ),
            'should_queue_close': True,
            'close_targets': close_targets,
        }

    if desired_side == 'flat':
        return {
            'status': 'orphan_broker_position',
            'desired_position': desired_position,
            'desired_side': desired_side,
            'actual_side': actual_side,
            'broker_position_count': 1,
            'broker_ticket': ticket,
            'broker_tickets': broker_tickets,
            'detail': 'Broker position exists without runtime ownership.',
            'should_queue_close': True,
            'close_targets': broker_positions,
        }

    if desired_side == actual_side:
        return {
            'status': 'match_open',
            'desired_position': desired_position,
            'desired_side': desired_side,
            'actual_side': actual_side,
            'broker_position_count': 1,
            'broker_ticket': ticket,
            'broker_tickets': broker_tickets,
            'detail': 'Runtime position matches broker position.',
            'should_queue_close': False,
        }

    return {
        'status': 'conflicting_broker_position',
        'desired_position': desired_position,
        'desired_side': desired_side,
        'actual_side': actual_side,
        'broker_position_count': 1,
        'broker_ticket': ticket,
        'broker_tickets': broker_tickets,
        'detail': 'Broker position conflicts with the runtime direction.',
        'should_queue_close': True,
        'close_targets': broker_positions,
    }


def _update_sleeve_reconciliation_state(sleeve: dict | None, sleeve_state: dict | None):
    safe_sleeve = dict(sleeve or {})
    safe_state = dict(sleeve_state or {})
    reconciliation = _build_sleeve_reconciliation_state(safe_sleeve, safe_state)
    previous_status = _trim_text(safe_state.get('reconciliation_status'))
    previous_ticket = _trim_text(safe_state.get('broker_position_ticket'))
    next_state = {
        **safe_state,
        'broker_position_side': reconciliation.get('actual_side'),
        'broker_position_ticket': reconciliation.get('broker_ticket'),
        'broker_position_tickets': list(reconciliation.get('broker_tickets') or []),
        'broker_position_count': reconciliation.get('broker_position_count'),
        'reconciliation_status': reconciliation.get('status'),
        'reconciliation_detail': reconciliation.get('detail'),
        'desired_position': reconciliation.get('desired_position'),
        'desired_side': reconciliation.get('desired_side'),
        'actual_position_side': reconciliation.get('actual_side'),
    }
    broker_close_reason = _persist_reconciled_broker_close_if_needed(
        safe_sleeve,
        safe_state,
        next_state,
        reconciliation,
    )
    if broker_close_reason:
        next_state = {
            **next_state,
            'status': next_state.get('status') or 'ready',
            'position': 0,
            'strategy_position': 0,
            'pending_action': None,
            'current_cycle_id': None,
            'pending_cycle_id': None,
            'desired_position': 0,
            'desired_side': 'flat',
            'actual_position_side': 'flat',
            'broker_position_side': 'flat',
            'broker_position_ticket': None,
            'broker_position_tickets': [],
            'broker_position_count': 0,
            'reconciliation_status': 'match_flat',
            'reconciliation_detail': 'Broker closed the position and runtime reconciled it to flat.',
            'last_broker_close_at': time.time(),
            'last_broker_close_reason': broker_close_reason,
        }
    current_cycle_id = (
        _trim_text(next_state.get('current_cycle_id'))
        or _trim_text(next_state.get('pending_cycle_id'))
    )
    next_ticket = _trim_text(reconciliation.get('broker_ticket'))
    if current_cycle_id and next_ticket and next_ticket != previous_ticket:
        _sync_live_trade_cycle_position_ticket(
            current_cycle_id,
            next_ticket,
            sleeve=safe_sleeve,
            sleeve_state=next_state,
        )
    if previous_status != _trim_text(reconciliation.get('status')):
        record_trade_runtime_event(
            'sleeve_reconciliation',
            f'Sleeve {safe_sleeve.get("label") or safe_state.get("sleeve_id") or "unknown"} reconciliation: {reconciliation.get("status")}.',
            sleeve_id=safe_state.get('sleeve_id') or safe_sleeve.get('id'),
            symbol=safe_state.get('symbol') or safe_sleeve.get('symbol'),
            reconciliation_status=reconciliation.get('status'),
            desired_side=reconciliation.get('desired_side'),
            actual_side=reconciliation.get('actual_side'),
            broker_ticket=reconciliation.get('broker_ticket'),
        )
    return next_state, reconciliation


def _get_trade_market_snapshot_tail(symbol: str, timeframe: str):
    safe_symbol = _trim_text(symbol).upper()
    safe_timeframe = _trim_text(timeframe).upper()
    snapshot_symbol = _trim_text(getattr(state.trade, 'market_snapshot_symbol', '')).upper()
    snapshot_timeframe = _trim_text(getattr(state.trade, 'market_snapshot_timeframe', '')).upper()
    candles = list(getattr(state.trade, 'market_snapshot_candles', []) or [])
    if not safe_symbol or not safe_timeframe:
        return None
    if snapshot_symbol != safe_symbol or snapshot_timeframe != safe_timeframe or not candles:
        return None
    return dict(candles[-1] or {})


def _infer_reconciled_broker_exit_reason(sleeve: dict | None, sleeve_state: dict | None):
    safe_sleeve = dict(sleeve or {})
    safe_state = dict(sleeve_state or {})
    previous_side = (
        _trim_text(safe_state.get('broker_position_side')).lower()
        or _trim_text(safe_state.get('actual_position_side')).lower()
        or _trim_text(safe_state.get('desired_side')).lower()
    )
    if previous_side not in {'long', 'short'}:
        return 'broker_reconcile'

    candle = _get_trade_market_snapshot_tail(
        safe_state.get('symbol') or safe_sleeve.get('symbol'),
        safe_state.get('timeframe') or safe_sleeve.get('timeframe'),
    )
    try:
        candle_high = None if candle is None else float(candle.get('high'))
    except Exception:
        candle_high = None
    try:
        candle_low = None if candle is None else float(candle.get('low'))
    except Exception:
        candle_low = None

    stop_loss = safe_state.get(f'{previous_side}_stop_loss_price')
    trailing_stop = safe_state.get(f'{previous_side}_trailing_stop_price')
    take_profit = safe_state.get(f'{previous_side}_take_profit_price')

    try:
        stop_loss = None if stop_loss in (None, '') else float(stop_loss)
    except Exception:
        stop_loss = None
    try:
        trailing_stop = None if trailing_stop in (None, '') else float(trailing_stop)
    except Exception:
        trailing_stop = None
    try:
        take_profit = None if take_profit in (None, '') else float(take_profit)
    except Exception:
        take_profit = None

    if previous_side == 'long':
        if stop_loss is not None and candle_low is not None and candle_low <= stop_loss:
            return 'loss'
        if trailing_stop is not None and candle_low is not None and candle_low <= trailing_stop:
            return 'trail'
        if take_profit is not None and candle_high is not None and candle_high >= take_profit:
            return 'gain'
        return 'broker_reconcile'

    if stop_loss is not None and candle_high is not None and candle_high >= stop_loss:
        return 'loss'
    if trailing_stop is not None and candle_high is not None and candle_high >= trailing_stop:
        return 'trail'
    if take_profit is not None and candle_low is not None and candle_low <= take_profit:
        return 'gain'
    return 'broker_reconcile'


def _persist_reconciled_broker_close_if_needed(
    sleeve: dict | None,
    previous_state: dict | None,
    next_state: dict | None,
    reconciliation: dict | None,
):
    safe_sleeve = dict(sleeve or {})
    safe_previous_state = dict(previous_state or {})
    safe_next_state = dict(next_state or {})
    safe_reconciliation = dict(reconciliation or {})
    if _trim_text(state.trade.execution_mode).lower() != 'live_mt5':
        return ''

    previous_side = (
        _trim_text(safe_previous_state.get('broker_position_side')).lower()
        or _trim_text(safe_previous_state.get('actual_position_side')).lower()
        or _trim_text(safe_previous_state.get('desired_side')).lower()
    )
    previous_ticket = _trim_text(safe_previous_state.get('broker_position_ticket'))
    cycle_id = (
        _trim_text(safe_previous_state.get('current_cycle_id'))
        or _trim_text(safe_previous_state.get('pending_cycle_id'))
        or _trim_text(safe_next_state.get('current_cycle_id'))
        or _trim_text(safe_next_state.get('pending_cycle_id'))
    )
    if previous_side not in {'long', 'short'} and not previous_ticket:
        return ''
    if _trim_text(safe_reconciliation.get('actual_side')).lower() != 'flat':
        return ''
    if _trim_text(safe_reconciliation.get('status')).lower() not in {'match_flat', 'missing_broker_position'}:
        return ''

    sleeve_id = _trim_text(safe_previous_state.get('sleeve_id') or safe_sleeve.get('id'))
    if _list_active_order_intents_for_sleeve(sleeve_id, action='close') or _list_active_order_commands_for_sleeve(sleeve_id, action='close'):
        return ''

    user_id = _trim_text(state.workspace.active_user_id) or 'local-user'
    workspace_id = _trim_text(state.workspace.active_workspace_id) or 'default'
    command_id = 'reconciled_close::' + (
        previous_ticket
        or cycle_id
        or sleeve_id
        or 'unknown'
    )
    if get_workspace_live_trade_by_command_id(user_id, workspace_id, command_id):
        return _trim_text(safe_previous_state.get('last_broker_close_reason')) or 'broker_reconcile'

    exit_reason = _infer_reconciled_broker_exit_reason(safe_sleeve, safe_previous_state)
    now = time.time()
    upsert_workspace_live_trade(
        user_id,
        workspace_id,
        command_id=command_id,
        source_intent_id=f'reconciled::{previous_ticket or cycle_id or sleeve_id or "unknown"}',
        execution_mode='live_mt5',
        portfolio_mode=_trim_text(state.trade.mode) or 'parallel_sleeves',
        status='filled',
        sleeve_id=sleeve_id,
        sleeve_label=_trim_text(safe_previous_state.get('label') or safe_sleeve.get('label')),
        source_strategy_id=_trim_text(safe_sleeve.get('source_strategy_id') or safe_sleeve.get('sourceStrategyId')),
        cycle_id=cycle_id,
        symbol=_trim_text(safe_previous_state.get('symbol') or safe_sleeve.get('symbol')).upper(),
        timeframe=_trim_text(safe_previous_state.get('timeframe') or safe_sleeve.get('timeframe')).upper(),
        action='close',
        side=previous_side,
        bar_time=safe_previous_state.get('last_bar_time'),
        created_at=now,
        claimed_at=now,
        acknowledged_at=now,
        filled_at=now,
        broker_order_id=previous_ticket,
        broker_position_ticket=previous_ticket,
        broker_deal_id='',
        fill_price=None,
        fill_volume=None,
        profit=None,
        commission=None,
        swap=None,
        exit_reason=exit_reason,
        message='Broker closed the position and runtime reconciled it into live trade history.',
        strategy=(safe_sleeve.get('strategy') if isinstance(safe_sleeve.get('strategy'), dict) else {}),
    )
    record_trade_runtime_event(
        'live_trade_broker_close_reconciled',
        'Persisted a broker-driven close into live trade history.',
        sleeve_id=sleeve_id or None,
        cycle_id=cycle_id or None,
        broker_ticket=previous_ticket or None,
        exit_reason=exit_reason,
    )
    return exit_reason


def _apply_runtime_resume_policy():
    trade_state = state.trade
    if _trim_text(trade_state.execution_mode).lower() != 'live_mt5':
        return

    next_states = dict(trade_state.sleeve_states or {})
    for sleeve in list(trade_state.sleeves or []):
        sleeve_id = _trim_text(sleeve.get('id'))
        if not sleeve_id:
            continue
        sleeve_state = dict(next_states.get(sleeve_id) or {})
        next_state, reconciliation = _update_sleeve_reconciliation_state(sleeve, sleeve_state)
        next_states[sleeve_id] = next_state

        if not reconciliation.get('should_queue_close'):
            continue

        close_targets = [
            dict(entry or {})
            for entry in list(reconciliation.get('close_targets') or [])
        ]
        if not close_targets:
            continue

        reconciliation_status = _trim_text(reconciliation.get('status'))
        if reconciliation_status in {'orphan_broker_position', 'orphan_multiple_positions'}:
            reason = 'Broker position exists without matching runtime ownership.'
        else:
            reason = 'Broker position conflicts with the runtime direction.'

        for broker_position in close_targets:
            _append_resume_close_intent(
                sleeve,
                broker_position,
                reason=reason,
            )
        next_states[sleeve_id] = {
            **next_states[sleeve_id],
            'resume_policy': 'close_orphan_position',
        }

    trade_state.sleeve_states = next_states


def _append_order_intent(intent: dict):
    trade_state = state.trade
    allowed, block_reason = _can_queue_order_intent(intent)
    if not allowed:
        record_trade_runtime_event(
            'order_intent_blocked_policy',
            block_reason or 'Same-symbol policy blocked the intent.',
            sleeve_id=intent.get('sleeve_id'),
            symbol=intent.get('symbol'),
            timeframe=intent.get('timeframe'),
            action=intent.get('action'),
            side=intent.get('side'),
            portfolio_mode=getattr(trade_state, 'mode', 'parallel_sleeves'),
            policy=getattr(trade_state, 'same_symbol_execution_policy', 'independent'),
        )
        return None

    existing_fingerprints = {
        _trim_text(item.get('fingerprint'))
        for item in list(trade_state.order_intents or [])
    }
    if _trim_text(intent.get('fingerprint')) in existing_fingerprints:
        return None

    intent_cycle_id = _trim_text(intent.get('cycle_id'))
    intent_action = _trim_text(intent.get('action')).lower()
    intent_sleeve_id = _trim_text(intent.get('sleeve_id'))
    intent_broker_ticket = _trim_text(intent.get('broker_ticket'))
    if intent_cycle_id and intent_action in {'open', 'close'}:
        for item in list(trade_state.order_intents or []):
            if _trim_text(item.get('sleeve_id')) != intent_sleeve_id:
                continue
            if _trim_text(item.get('cycle_id')) != intent_cycle_id:
                continue
            if _trim_text(item.get('action')).lower() != intent_action:
                continue
            if intent_action == 'close':
                existing_broker_ticket = _trim_text(item.get('broker_ticket'))
                if intent_broker_ticket and existing_broker_ticket and existing_broker_ticket != intent_broker_ticket:
                    continue
            status = _trim_text(item.get('status')).lower() or 'queued'
            if status in {'filled', 'rejected', 'expired', 'suppressed', 'stale'}:
                continue
            return None

    trade_state.order_intents = [
        intent,
        *list(trade_state.order_intents or []),
    ][:MAX_TRADE_ORDER_INTENTS]
    metrics = dict(trade_state.metrics or {})
    metrics['dispatch_count'] = int(metrics.get('dispatch_count') or 0) + 1
    trade_state.metrics = metrics
    record_trade_runtime_event(
        'order_intent',
        f'Queued {intent["action"]} {intent["side"]} intent for {intent["sleeve_label"] or intent["sleeve_id"]}.',
        sleeve_id=intent['sleeve_id'],
        symbol=intent['symbol'],
        timeframe=intent['timeframe'],
        action=intent['action'],
        side=intent['side'],
        execution_mode=intent['execution_mode'],
    )
    return intent


def _find_intent_index(intent_id: str):
    safe_id = _trim_text(intent_id)
    if not safe_id:
        return None

    for index, entry in enumerate(list(state.trade.order_intents or [])):
        if _trim_text(entry.get('id')) == safe_id:
            return index

    return None


def _list_active_order_commands_for_sleeve(sleeve_id: str, action: str | None = None):
    safe_sleeve_id = _trim_text(sleeve_id)
    safe_action = _trim_text(action).lower() if action is not None else ''
    commands = []
    for entry in list(state.trade.order_commands or []):
        if _trim_text(entry.get('sleeve_id')) != safe_sleeve_id:
            continue
        status = _trim_text(entry.get('status')).lower() or 'queued'
        if status in {'filled', 'rejected', 'stale'}:
            continue
        if safe_action and _trim_text(entry.get('action')).lower() != safe_action:
            continue
        commands.append(dict(entry or {}))
    return commands


def _list_active_order_intents_for_sleeve(sleeve_id: str, action: str | None = None):
    safe_sleeve_id = _trim_text(sleeve_id)
    safe_action = _trim_text(action).lower() if action is not None else ''
    intents = []
    for entry in list(state.trade.order_intents or []):
        if _trim_text(entry.get('sleeve_id')) != safe_sleeve_id:
            continue
        status = _trim_text(entry.get('status')).lower() or 'queued'
        if status in {'filled', 'rejected', 'expired', 'suppressed', 'stale'}:
            continue
        if safe_action and _trim_text(entry.get('action')).lower() != safe_action:
            continue
        intents.append(dict(entry or {}))
    return intents


def _sanitize_live_intent_before_dispatch(intent: dict):
    safe_intent = dict(intent or {})
    sleeve_id = _trim_text(safe_intent.get('sleeve_id'))
    action = _trim_text(safe_intent.get('action')).lower()
    side = _trim_text(safe_intent.get('side')).lower()
    sleeve = _find_trade_sleeve(sleeve_id) or {}
    broker_positions = _list_broker_positions_for_sleeve(sleeve)
    active_commands = _list_active_order_commands_for_sleeve(sleeve_id, action=action)

    if active_commands:
        return {
            'status': 'dispatch_blocked',
            'message': 'Another live broker command for this sleeve is still pending.',
            'event_kind': 'order_intent_blocked',
        }

    if action == 'open':
        if len(broker_positions) > 1:
            return {
                'status': 'dispatch_blocked',
                'message': 'Multiple broker positions exist for this sleeve. Open dispatch was blocked.',
                'event_kind': 'order_intent_blocked',
            }

        if len(broker_positions) == 1:
            broker_side = _trim_text(broker_positions[0].get('side')).lower()
            if broker_side == side:
                return {
                    'status': 'suppressed',
                    'message': 'Broker position is already open in the requested direction.',
                    'event_kind': 'order_intent_suppressed',
                }
            return {
                'status': 'dispatch_blocked',
                'message': 'Broker position is still open in the opposite direction. Waiting for reconciliation.',
                'event_kind': 'order_intent_blocked',
            }

        return None

    if action == 'close':
        if len(broker_positions) == 0:
            return {
                'status': 'suppressed',
                'message': 'No broker position exists for this sleeve anymore.',
                'event_kind': 'order_intent_suppressed',
            }
        target_broker_position = _select_broker_position_for_close(safe_intent)
        if target_broker_position is None:
            return {
                'status': 'dispatch_blocked',
                'message': 'Broker close dispatch could not select a deterministic position ticket for this sleeve.',
                'event_kind': 'order_intent_blocked',
            }

        broker_side = _trim_text(target_broker_position.get('side')).lower()
        if side and broker_side and broker_side != side:
            return {
                'status': 'suppressed',
                'message': f'Broker position side is {broker_side}, so there is no {side} position to close.',
                'event_kind': 'order_intent_suppressed',
            }

    return None


def _latest_market_reference_price(symbol: str):
    safe_symbol = _trim_text(symbol).upper()
    candles = list(getattr(state.trade, 'market_snapshot_candles', []) or [])
    snapshot_symbol = _trim_text(getattr(state.trade, 'market_snapshot_symbol', '')).upper()
    if safe_symbol and candles and snapshot_symbol == safe_symbol:
        last = dict(candles[-1] or {})
        try:
            return float(last.get('close'))
        except Exception:
            return None
    return None


def _validate_live_open_stops(intent: dict):
    trade_state = state.trade
    safe_intent = dict(intent or {})
    symbol = _trim_text(safe_intent.get('symbol')).upper()
    side = _trim_text(safe_intent.get('side')).lower()
    if not symbol or side not in {'long', 'short'}:
        return None

    symbol_rules = dict(getattr(trade_state, 'broker_symbol_rules', {}) or {}).get(symbol) or {}
    point = symbol_rules.get('point')
    stops_level_points = symbol_rules.get('stops_level_points')
    if point in (None, 0, 0.0) or stops_level_points in (None, ''):
        return None

    min_distance = float(point) * max(0, int(stops_level_points))
    if min_distance <= 0.0:
        return None

    reference_price = _latest_market_reference_price(symbol)
    if reference_price in (None, ''):
        return None
    reference_price = float(reference_price)

    take_profit = safe_intent.get('take_profit_price')
    stop_loss = safe_intent.get('stop_loss_price')
    digits = symbol_rules.get('digits')
    distance_text = f'{min_distance:.5f}' if digits in (None, '') else f'{min_distance:.{int(digits)}f}'

    if take_profit not in (None, ''):
        take_profit = float(take_profit)
        if (side == 'long' and take_profit <= reference_price) or (side == 'short' and take_profit >= reference_price):
            return {
                'status': 'dispatch_blocked',
                'message': f'Take profit for {symbol} is on the wrong side of the market. Adjust the strategy gain target.',
                'event_kind': 'order_intent_blocked',
            }
        if abs(take_profit - reference_price) < min_distance:
            return {
                'status': 'dispatch_blocked',
                'message': f'Broker stop rule for {symbol} requires take profit at least {distance_text} away from market. Increase the strategy gain distance.',
                'event_kind': 'order_intent_blocked',
            }

    if stop_loss not in (None, ''):
        stop_loss = float(stop_loss)
        if (side == 'long' and stop_loss >= reference_price) or (side == 'short' and stop_loss <= reference_price):
            return {
                'status': 'dispatch_blocked',
                'message': f'Stop loss for {symbol} is on the wrong side of the market. Adjust the strategy loss distance.',
                'event_kind': 'order_intent_blocked',
            }
        if abs(stop_loss - reference_price) < min_distance:
            return {
                'status': 'dispatch_blocked',
                'message': f'Broker stop rule for {symbol} requires stop loss at least {distance_text} away from market. Increase the strategy loss distance.',
                'event_kind': 'order_intent_blocked',
            }

    return None


def _update_order_intent(intent_id: str, **changes):
    intent_index = _find_intent_index(intent_id)
    if intent_index is None:
        return None

    intents = list(state.trade.order_intents or [])
    next_intent = {
        **dict(intents[intent_index] or {}),
        **{key: value for key, value in changes.items() if value is not None},
    }
    intents[intent_index] = next_intent
    state.trade.order_intents = intents
    return next_intent


def _find_order_intent(intent_id: str):
    intent_index = _find_intent_index(intent_id)
    if intent_index is None:
        return None
    intents = list(state.trade.order_intents or [])
    return dict(intents[intent_index] or {})


def _build_order_command(intent: dict):
    safe_intent = dict(intent or {})
    intent_id = _trim_text(safe_intent.get('id'))
    command_id = f'tcmd_{int(time.time() * 1000)}_{_trim_text(safe_intent.get("sleeve_id")) or "trade"}'
    sleeve = _find_trade_sleeve(_trim_text(safe_intent.get('sleeve_id'))) or {}
    target_broker_position = None
    if _trim_text(safe_intent.get('action')).lower() == 'close':
        target_broker_position = _select_broker_position_for_close(safe_intent)
    action = _trim_text(safe_intent.get('action')).lower()
    execution_mode = _trim_text(safe_intent.get('execution_mode') or getattr(state.trade, 'execution_mode', '')).lower()
    omit_broker_managed_protective_prices = action == 'open' and execution_mode == 'live_mt5'
    return {
        'id': command_id,
        'source_intent_id': intent_id,
        'fingerprint': _trim_text(safe_intent.get('fingerprint')),
        'status': 'queued',
        'portfolio_mode': _trim_text(safe_intent.get('portfolio_mode')) or state.trade.mode,
        'execution_mode': state.trade.execution_mode,
        'portfolio_id': _trim_text(safe_intent.get('portfolio_id')) or None,
        'portfolio_label': _trim_text(safe_intent.get('portfolio_label')) or None,
        'pipeline_id': _trim_text(safe_intent.get('pipeline_id')) or None,
        'pipeline_label': _trim_text(safe_intent.get('pipeline_label')) or None,
        'sleeve_id': safe_intent.get('sleeve_id'),
        'sleeve_label': safe_intent.get('sleeve_label'),
        'source_strategy_id': safe_intent.get('source_strategy_id'),
        'symbol': safe_intent.get('symbol'),
        'timeframe': safe_intent.get('timeframe'),
        'volume_mode': _trim_text(safe_intent.get('volume_mode')) or 'fixed_volume',
        'cycle_id': _trim_text(safe_intent.get('cycle_id')) or None,
        'exit_reason': _trim_text(safe_intent.get('exit_reason')) or None,
        'expected_exit_price': (
            None if safe_intent.get('expected_exit_price') in (None, '') else float(safe_intent.get('expected_exit_price'))
        ),
        'take_profit_price': (
            None
            if omit_broker_managed_protective_prices or safe_intent.get('take_profit_price') in (None, '')
            else float(safe_intent.get('take_profit_price'))
        ),
        'stop_loss_price': (
            None
            if omit_broker_managed_protective_prices or safe_intent.get('stop_loss_price') in (None, '')
            else float(safe_intent.get('stop_loss_price'))
        ),
        'strategy_entry_price': _parse_optional_float(safe_intent.get('strategy_entry_price')),
        'action': safe_intent.get('action'),
        'side': safe_intent.get('side'),
        'decision': safe_intent.get('decision'),
        'bar_time': safe_intent.get('bar_time'),
        'volume': max(0.01, float(sleeve.get('volume') or DEFAULT_LIVE_ORDER_VOLUME)),
        'broker_ticket': (
            _trim_text(safe_intent.get('broker_ticket'))
            or _trim_text((target_broker_position or {}).get('ticket'))
            or None
        ),
        'broker_position_side': (
            _trim_text((target_broker_position or {}).get('side')).lower()
            or None
        ),
        'created_at': time.time(),
        'bridge_session_id': None,
        'claimed_at': None,
        'acknowledged_at': None,
        'filled_at': None,
        'rejected_at': None,
        'broker_order_id': None,
        'broker_deal_id': None,
        'fill_price': None,
        'fill_volume': None,
        'message': None,
    }


def _derive_trade_exit_reason(command: dict | None):
    safe_command = dict(command or {})
    explicit_reason = _trim_text(safe_command.get('exit_reason'))
    if explicit_reason:
        return explicit_reason
    if _trim_text(safe_command.get('action')).lower() != 'close':
        return ''

    decision = _trim_text(safe_command.get('decision')).lower()
    mapping = {
        'close_long': 'close',
        'close_short': 'close',
        'stop_long_gain': 'gain',
        'stop_short_gain': 'gain',
        'stop_long_loss': 'loss',
        'stop_short_loss': 'loss',
        'stop_long_trail': 'trail',
        'stop_short_trail': 'trail',
        'resume_close': 'resume_close',
    }
    return mapping.get(decision, decision or 'close')


def _find_trade_sleeve(sleeve_id: str):
    safe_sleeve_id = _trim_text(sleeve_id)
    if not safe_sleeve_id:
        return None

    for sleeve in list(state.trade.sleeves or []):
        if _trim_text((sleeve or {}).get('id')) == safe_sleeve_id:
            return dict(sleeve or {})
    return None


def _persist_live_trade_command(command: dict | None):
    safe_command = dict(command or {})
    if _trim_text(safe_command.get('execution_mode')).lower() != 'live_mt5':
        return None

    runtime = state.workspace
    user_id = _trim_text(runtime.active_user_id) or 'local-user'
    workspace_id = _trim_text(runtime.active_workspace_id) or 'default'
    sleeve = _find_trade_sleeve(_trim_text(safe_command.get('sleeve_id')))
    source_intent = _find_order_intent(_trim_text(safe_command.get('source_intent_id')))
    sleeve_state = dict((state.trade.sleeve_states or {}).get(_trim_text(safe_command.get('sleeve_id'))) or {})
    effective_cycle_id = (
        _trim_text(safe_command.get('cycle_id'))
        or _trim_text((source_intent or {}).get('cycle_id'))
        or _trim_text(sleeve_state.get('current_cycle_id'))
        or _trim_text(sleeve_state.get('pending_cycle_id'))
    )
    effective_source_strategy_id = (
        _trim_text(safe_command.get('source_strategy_id'))
        or _trim_text((source_intent or {}).get('source_strategy_id'))
        or _trim_text((sleeve or {}).get('source_strategy_id') or (sleeve or {}).get('sourceStrategyId'))
    )
    effective_portfolio_id = (
        _trim_text(safe_command.get('portfolio_id'))
        or _trim_text((source_intent or {}).get('portfolio_id'))
        or _trim_text((sleeve or {}).get('portfolio_id'))
    )
    effective_portfolio_label = (
        _trim_text(safe_command.get('portfolio_label'))
        or _trim_text((source_intent or {}).get('portfolio_label'))
        or _trim_text((sleeve or {}).get('portfolio_label'))
    )
    effective_pipeline_id = (
        _trim_text(safe_command.get('pipeline_id'))
        or _trim_text((source_intent or {}).get('pipeline_id'))
        or _trim_text((sleeve or {}).get('pipeline_id'))
    )
    effective_pipeline_label = (
        _trim_text(safe_command.get('pipeline_label'))
        or _trim_text((source_intent or {}).get('pipeline_label'))
        or _trim_text((sleeve or {}).get('pipeline_label'))
    )
    broker_position_ticket = (
        _trim_text(safe_command.get('broker_position_ticket'))
        or _trim_text(safe_command.get('broker_ticket'))
    )

    return upsert_workspace_live_trade(
        user_id,
        workspace_id,
        command_id=_trim_text(safe_command.get('id')),
        source_intent_id=_trim_text(safe_command.get('source_intent_id')),
        execution_mode=_trim_text(safe_command.get('execution_mode')) or 'live_mt5',
        portfolio_mode=_trim_text(safe_command.get('portfolio_mode')),
        portfolio_id=effective_portfolio_id,
        portfolio_label=effective_portfolio_label,
        pipeline_id=effective_pipeline_id,
        pipeline_label=effective_pipeline_label,
        status=_trim_text(safe_command.get('status')),
        sleeve_id=_trim_text(safe_command.get('sleeve_id')),
        sleeve_label=_trim_text(safe_command.get('sleeve_label')),
        source_strategy_id=effective_source_strategy_id,
        cycle_id=effective_cycle_id,
        symbol=_trim_text(safe_command.get('symbol')).upper(),
        timeframe=_trim_text(safe_command.get('timeframe')).upper(),
        action=_trim_text(safe_command.get('action')),
        side=_trim_text(safe_command.get('side')),
        bar_time=safe_command.get('bar_time'),
        created_at=safe_command.get('created_at'),
        claimed_at=safe_command.get('claimed_at'),
        acknowledged_at=safe_command.get('acknowledged_at'),
        filled_at=safe_command.get('filled_at'),
        rejected_at=safe_command.get('rejected_at'),
        broker_order_id=_trim_text(safe_command.get('broker_order_id')),
        broker_position_ticket=broker_position_ticket,
        broker_deal_id=_trim_text(safe_command.get('broker_deal_id')),
        fill_price=safe_command.get('fill_price'),
        fill_volume=safe_command.get('fill_volume'),
        profit=safe_command.get('profit'),
        commission=safe_command.get('commission'),
        swap=safe_command.get('swap'),
        exit_reason=_derive_trade_exit_reason(safe_command),
        message=_trim_text(safe_command.get('message')),
        strategy=(sleeve or {}).get('strategy') if isinstance((sleeve or {}).get('strategy'), dict) else {},
        broker_profile_id=_trim_text(getattr(state.trade, 'broker_profile_id', '')),
        broker_profile_label=_trim_text(getattr(state.trade, 'broker_profile_label', '')),
    )


def _sync_live_trade_cycle_position_ticket(cycle_id: str, broker_position_ticket: str, sleeve: dict | None = None, sleeve_state: dict | None = None):
    safe_cycle_id = _trim_text(cycle_id)
    safe_ticket = _trim_text(broker_position_ticket)
    if not safe_cycle_id or not safe_ticket:
        return 0

    runtime = state.workspace
    user_id = _trim_text(runtime.active_user_id) or 'local-user'
    workspace_id = _trim_text(runtime.active_workspace_id) or 'default'
    safe_sleeve = dict(sleeve or {})
    safe_state = dict(sleeve_state or {})

    try:
        updated = update_workspace_live_trade_cycle_broker_position_ticket(
            user_id,
            workspace_id,
            safe_cycle_id,
            safe_ticket,
            sleeve_id=_trim_text(safe_state.get('sleeve_id') or safe_sleeve.get('id')) or None,
            symbol=_trim_text(safe_state.get('symbol') or safe_sleeve.get('symbol')).upper() or None,
            timeframe=_trim_text(safe_state.get('timeframe') or safe_sleeve.get('timeframe')).upper() or None,
        )
    except Exception as error:
        record_trade_runtime_event(
            'live_trade_history_sync_error',
            'Could not synchronize broker position tickets into persisted live trade history.',
            level='warning',
            cycle_id=safe_cycle_id,
            broker_ticket=safe_ticket,
            error=_trim_text(error) or 'unknown error',
        )
        return 0

    if updated:
        record_trade_runtime_event(
            'live_trade_history_sync',
            'Persisted live trade history was synchronized with the latest broker position ticket.',
            cycle_id=safe_cycle_id,
            broker_ticket=safe_ticket,
            updated_rows=updated,
            sleeve_id=_trim_text(safe_state.get('sleeve_id') or safe_sleeve.get('id')) or None,
        )
    return updated


def _append_order_command(command: dict):
    trade_state = state.trade
    existing_fingerprints = {
        _trim_text(item.get('fingerprint'))
        for item in list(trade_state.order_commands or [])
        if _trim_text(item.get('status')) not in {'rejected', 'filled'}
    }
    if _trim_text(command.get('fingerprint')) in existing_fingerprints:
        return None

    trade_state.order_commands = [
        command,
        *list(trade_state.order_commands or []),
    ][:MAX_TRADE_ORDER_COMMANDS]
    metrics = dict(trade_state.metrics or {})
    metrics['command_count'] = int(metrics.get('command_count') or 0) + 1
    trade_state.metrics = metrics
    record_trade_runtime_event(
        'order_command_queued',
        f'Queued live MT5 command for {command.get("sleeve_label") or command.get("sleeve_id")}.',
        command_id=command.get('id'),
        sleeve_id=command.get('sleeve_id'),
        symbol=command.get('symbol'),
        action=command.get('action'),
        side=command.get('side'),
    )
    return command


def _find_order_command_index(command_id: str):
    safe_id = _trim_text(command_id)
    if not safe_id:
        return None

    for index, entry in enumerate(list(state.trade.order_commands or [])):
        if _trim_text(entry.get('id')) == safe_id:
            return index

    return None


def _find_order_command(command_id: str):
    command_index = _find_order_command_index(command_id)
    if command_index is None:
        return None
    commands = list(state.trade.order_commands or [])
    return dict(commands[command_index] or {})


def _should_treat_close_rejection_as_benign_fill(command: dict | None, payload: dict | None):
    safe_command = dict(command or {})
    safe_payload = dict(payload or {})
    if _trim_text(safe_command.get('action')).lower() != 'close':
        return False

    broker_message = _trim_text(safe_payload.get('message')).lower()
    if 'no matching position found' not in broker_message:
        return False

    sleeve = _find_trade_sleeve(_trim_text(safe_command.get('sleeve_id')))
    broker_summary = _summarize_broker_positions(_list_broker_positions_for_sleeve(sleeve))
    requested_ticket = _trim_text(
        safe_command.get('broker_ticket')
        or safe_command.get('broker_position_ticket')
        or safe_payload.get('ticket')
        or safe_payload.get('order_id')
    )
    requested_side = _trim_text(safe_command.get('side')).lower()

    if requested_ticket and requested_ticket in set(broker_summary.get('tickets') or []):
        return False

    if requested_side:
        active_same_side_positions = [
            entry for entry in list(broker_summary.get('positions') or [])
            if _trim_text((entry or {}).get('side')).lower() == requested_side
        ]
        if active_same_side_positions:
            return False

    return True


def _update_order_command(command_id: str, **changes):
    command_index = _find_order_command_index(command_id)
    if command_index is None:
        return None

    commands = list(state.trade.order_commands or [])
    next_command = {
        **dict(commands[command_index] or {}),
        **{key: value for key, value in changes.items() if value is not None},
    }
    commands[command_index] = next_command
    state.trade.order_commands = commands
    return next_command


def claim_next_trade_order_command(session_id: str = ''):
    trade_state = state.trade
    safe_session_id = _trim_text(session_id) or None
    now = time.time()

    for command in list(trade_state.order_commands or []):
        status = _trim_text(command.get('status')).lower() or 'queued'
        if status != 'queued':
            continue

        claimed = _update_order_command(
            _trim_text(command.get('id')),
            status='claimed',
            claimed_at=now,
            bridge_session_id=safe_session_id,
        )
        if claimed:
            _update_order_intent(
                _trim_text(claimed.get('source_intent_id')),
                status='broker_claimed',
                claimed_at=now,
                bridge_session_id=safe_session_id,
            )
            record_trade_runtime_event(
                'order_command_claimed',
                f'Bridge claimed live MT5 command for {claimed.get("sleeve_label") or claimed.get("sleeve_id")}.',
                command_id=claimed.get('id'),
                sleeve_id=claimed.get('sleeve_id'),
                bridge_session_id=safe_session_id,
            )
            return claimed

    return None


def acknowledge_trade_order_command(command_id: str, payload: dict | None = None):
    safe_payload = dict(payload or {})
    now = time.time()
    command = _update_order_command(
        command_id,
        status='acknowledged',
        acknowledged_at=now,
        bridge_session_id=_trim_text(safe_payload.get('session_id')) or None,
        broker_order_id=_trim_text(safe_payload.get('order_id') or safe_payload.get('ticket')) or None,
        message=_trim_text(safe_payload.get('message')) or None,
    )
    if not command:
        return None

    _update_order_intent(
        _trim_text(command.get('source_intent_id')),
        status='broker_acknowledged',
        acknowledged_at=now,
        broker_order_id=command.get('broker_order_id'),
    )
    metrics = dict(state.trade.metrics or {})
    metrics['command_ack_count'] = int(metrics.get('command_ack_count') or 0) + 1
    state.trade.metrics = metrics
    record_trade_runtime_event(
        'order_command_ack',
        f'Bridge acknowledged live MT5 command for {command.get("sleeve_label") or command.get("sleeve_id")}.',
        command_id=command.get('id'),
        sleeve_id=command.get('sleeve_id'),
        broker_order_id=command.get('broker_order_id'),
    )
    _persist_live_trade_command(command)
    return command


def finalize_trade_order_command(command_id: str, payload: dict | None = None):
    safe_payload = dict(payload or {})
    current_command = _find_order_command(command_id)
    result_status = _trim_text(safe_payload.get('status')).lower() or 'filled'
    normalized_from_rejection = (
        result_status != 'filled'
        and _should_treat_close_rejection_as_benign_fill(current_command, safe_payload)
    )
    final_status = 'filled' if result_status == 'filled' or normalized_from_rejection else 'rejected'
    now = time.time()
    broker_message = _trim_text(safe_payload.get('message')) or None
    if normalized_from_rejection and broker_message:
        broker_message = f'{broker_message} (treated as already closed)'
    command = _update_order_command(
        command_id,
        status=final_status,
        filled_at=(now if final_status == 'filled' else None),
        rejected_at=(now if final_status == 'rejected' else None),
        broker_order_id=_trim_text(safe_payload.get('order_id') or safe_payload.get('ticket')) or None,
        broker_deal_id=_trim_text(safe_payload.get('deal_id')) or None,
        fill_price=(None if safe_payload.get('price') in (None, '') else float(safe_payload.get('price'))),
        fill_volume=(None if safe_payload.get('volume') in (None, '') else float(safe_payload.get('volume'))),
        profit=(None if safe_payload.get('profit') in (None, '') else float(safe_payload.get('profit'))),
        commission=(None if safe_payload.get('commission') in (None, '') else float(safe_payload.get('commission'))),
        swap=(None if safe_payload.get('swap') in (None, '') else float(safe_payload.get('swap'))),
        message=broker_message,
        broker_result_status=result_status,
        normalized_from_rejection=normalized_from_rejection,
    )
    if not command:
        return None

    _update_order_intent(
        _trim_text(command.get('source_intent_id')),
        status=('filled' if final_status == 'filled' else 'rejected'),
        filled_at=(now if final_status == 'filled' else None),
        rejected_at=(now if final_status == 'rejected' else None),
        broker_order_id=command.get('broker_order_id'),
        broker_deal_id=command.get('broker_deal_id'),
        fill_price=command.get('fill_price'),
        fill_volume=command.get('fill_volume'),
        rejection_message=(command.get('message') if final_status == 'rejected' else None),
    )
    metrics = dict(state.trade.metrics or {})
    if final_status == 'filled':
        metrics['command_fill_count'] = int(metrics.get('command_fill_count') or 0) + 1
    else:
        metrics['command_reject_count'] = int(metrics.get('command_reject_count') or 0) + 1
    state.trade.metrics = metrics
    record_trade_runtime_event(
        ('order_command_fill' if final_status == 'filled' else 'order_command_reject'),
        (
            f'Live MT5 command filled for {command.get("sleeve_label") or command.get("sleeve_id")}.'
            if final_status == 'filled'
            else f'Live MT5 command rejected for {command.get("sleeve_label") or command.get("sleeve_id")}.'
        ),
        command_id=command.get('id'),
        sleeve_id=command.get('sleeve_id'),
        broker_order_id=command.get('broker_order_id'),
        broker_deal_id=command.get('broker_deal_id'),
        broker_message=command.get('message'),
    )
    _persist_live_trade_command(command)
    sleeve_id = _trim_text(command.get('sleeve_id'))
    if sleeve_id:
        sleeve_states = dict(state.trade.sleeve_states or {})
        sleeve_state = dict(sleeve_states.get(sleeve_id) or {})
        action = _trim_text(command.get('action')).lower()
        side = _trim_text(command.get('side')).lower()
        cycle_id = _trim_text(command.get('cycle_id')) or _trim_text(sleeve_state.get('pending_cycle_id')) or _trim_text(sleeve_state.get('current_cycle_id'))
        if final_status == 'filled':
            if action == 'open':
                sleeve_state['current_cycle_id'] = cycle_id or None
                sleeve_state['pending_cycle_id'] = None
                next_position = 1 if side == 'long' else (-1 if side == 'short' else int(sleeve_state.get('position') or 0))
                sleeve_state['position'] = next_position
                sleeve_state['strategy_position'] = next_position
                sleeve_state['desired_position'] = next_position
                sleeve_state['desired_side'] = side if next_position else 'flat'
                sleeve_state['actual_position_side'] = side if next_position else 'flat'
                sleeve_state['broker_position_side'] = side if next_position else 'flat'
                sleeve_state['broker_position_ticket'] = (
                    _trim_text(command.get('broker_position_ticket'))
                    or _trim_text(command.get('broker_ticket'))
                    or sleeve_state.get('broker_position_ticket')
                )
                sleeve_state['broker_position_tickets'] = (
                    [_trim_text(command.get('broker_position_ticket')) or _trim_text(command.get('broker_ticket'))]
                    if (_trim_text(command.get('broker_position_ticket')) or _trim_text(command.get('broker_ticket')))
                    else list(sleeve_state.get('broker_position_tickets') or [])
                )
                sleeve_state['broker_position_count'] = 1 if next_position else 0
                sleeve_state['reconciliation_status'] = 'match_open' if next_position else 'match_flat'
                sleeve_state['reconciliation_detail'] = (
                    f'Broker position is synchronized on the {side} side.'
                    if next_position else 'No broker position and runtime expects flat.'
                )
                sleeve_state['live_entry_fill_price'] = _parse_optional_float(command.get('fill_price'))
                sleeve_state['strategy_entry_price'] = _parse_optional_float(command.get('strategy_entry_price'))
                sleeve_state['protective_price_shift'] = (
                    None
                    if sleeve_state.get('live_entry_fill_price') is None or sleeve_state.get('strategy_entry_price') is None
                    else float(sleeve_state.get('live_entry_fill_price')) - float(sleeve_state.get('strategy_entry_price'))
                )
                sleeve_state['live_entry_side'] = side or None
                sleeve_state['live_entry_bar_time'] = command.get('bar_time')
                if sleeve_state.get('protective_price_shift') is not None:
                    protective_shift = float(sleeve_state['protective_price_shift'])
                    for suffix in ('take_profit_price', 'stop_loss_price', 'trailing_stop_price'):
                        key = f'{side}_{suffix}'
                        candidate = _parse_optional_float(sleeve_state.get(key))
                        if candidate is not None:
                            sleeve_state[key] = candidate + protective_shift
            elif action == 'close':
                sleeve_state['current_cycle_id'] = None
                sleeve_state['pending_cycle_id'] = None
                sleeve_state['position'] = 0
                sleeve_state['strategy_position'] = 0
                sleeve_state['desired_position'] = 0
                sleeve_state['desired_side'] = 'flat'
                sleeve_state['actual_position_side'] = 'flat'
                sleeve_state['broker_position_side'] = 'flat'
                sleeve_state['broker_position_ticket'] = None
                sleeve_state['broker_position_tickets'] = []
                sleeve_state['broker_position_count'] = 0
                sleeve_state['reconciliation_status'] = 'match_flat'
                sleeve_state['reconciliation_detail'] = 'No broker position and runtime expects flat.'
                sleeve_state['live_entry_fill_price'] = None
                sleeve_state['strategy_entry_price'] = None
                sleeve_state['protective_price_shift'] = None
                sleeve_state['live_entry_side'] = None
                sleeve_state['live_entry_bar_time'] = None
        elif final_status == 'rejected' and action == 'open':
            sleeve_state['current_cycle_id'] = None
            sleeve_state['pending_cycle_id'] = None
            sleeve_state['position'] = 0
            sleeve_state['strategy_position'] = 0
            sleeve_state['desired_position'] = 0
            sleeve_state['desired_side'] = 'flat'
            sleeve_state['actual_position_side'] = 'flat'
            sleeve_state['broker_position_side'] = 'flat'
            sleeve_state['broker_position_ticket'] = None
            sleeve_state['broker_position_tickets'] = []
            sleeve_state['broker_position_count'] = 0
            sleeve_state['reconciliation_status'] = 'match_flat'
            sleeve_state['reconciliation_detail'] = 'No broker position and runtime expects flat.'
            sleeve_state['live_entry_fill_price'] = None
            sleeve_state['strategy_entry_price'] = None
            sleeve_state['protective_price_shift'] = None
            sleeve_state['live_entry_side'] = None
            sleeve_state['live_entry_bar_time'] = None
        sleeve_states[sleeve_id] = sleeve_state
        state.trade.sleeve_states = sleeve_states
    return command


def reconcile_trade_runtime_commands(stale_after_seconds: float | int | None = None):
    trade_state = state.trade
    timeout_seconds = max(1.0, float(stale_after_seconds or TRADE_COMMAND_STALE_SECONDS))
    now = time.time()
    stale_count = 0
    refreshed_commands = []

    for command in list(trade_state.order_commands or []):
        next_command = dict(command or {})
        status = _trim_text(next_command.get('status')).lower() or 'queued'
        reference_at = (
            next_command.get('acknowledged_at')
            or next_command.get('claimed_at')
            or next_command.get('created_at')
        )

        if status in {'queued', 'claimed', 'acknowledged'} and reference_at is not None:
            age_seconds = max(0.0, now - float(reference_at))
            next_command['age_seconds'] = round(age_seconds, 3)
            if age_seconds > timeout_seconds:
                stale_count += 1
                next_command['status'] = 'stale'
                next_command['stale_at'] = now
                next_command['message'] = (
                    next_command.get('message')
                    or f'Command exceeded stale threshold of {timeout_seconds:.1f}s.'
                )
                _update_order_intent(
                    _trim_text(next_command.get('source_intent_id')),
                    status='stale',
                    stale_at=now,
                    rejection_message=next_command.get('message'),
                )
                record_trade_runtime_event(
                    'order_command_stale',
                    f'Live MT5 command became stale for {next_command.get("sleeve_label") or next_command.get("sleeve_id")}.',
                    command_id=next_command.get('id'),
                    sleeve_id=next_command.get('sleeve_id'),
                    age_seconds=round(age_seconds, 3),
                )
        refreshed_commands.append(next_command)

    trade_state.order_commands = refreshed_commands
    if stale_count:
        trade_state.last_error = f'{stale_count} live trade command(s) became stale.'
        if trade_state.armed:
            trade_state.status = 'interrupted'

    return build_trade_runtime_payload()


def reset_trade_runtime_commands(clear_intents: bool = False):
    trade_state = state.trade
    cleared_commands = len(list(trade_state.order_commands or []))
    cleared_intents = len(list(trade_state.order_intents or [])) if clear_intents else 0
    now = time.time()

    if not clear_intents:
        refreshed_intents = []
        for intent in list(trade_state.order_intents or []):
            next_intent = dict(intent or {})
            status = _trim_text(next_intent.get('status')).lower() or 'queued'
            linked_command_id = _trim_text(next_intent.get('command_id'))
            if status in {'broker_claimed', 'broker_acknowledged'} and linked_command_id:
                linked_command = _find_order_command(linked_command_id)
                if not linked_command:
                    next_intent['status'] = 'stale'
                    next_intent['stale_at'] = now
                    next_intent['rejection_message'] = 'Linked broker command queue was reset by operator.'
            refreshed_intents.append(next_intent)
        trade_state.order_intents = refreshed_intents

    trade_state.order_commands = []
    if clear_intents:
        trade_state.order_intents = []
    trade_state.last_error = None
    record_trade_runtime_event(
        'order_command_reset',
        'Trade runtime command queue was reset by operator.',
        cleared_commands=cleared_commands,
        cleared_intents=cleared_intents,
    )
    return build_trade_runtime_payload()


def _clear_active_live_dispatch_queue(reason: str = 'manual'):
    trade_state = state.trade
    now = time.time()
    safe_reason = _trim_text(reason) or 'manual'
    active_command_statuses = {'queued', 'claimed', 'acknowledged'}
    active_intent_statuses = {'queued', 'dispatch_blocked', 'broker_queued', 'broker_claimed', 'broker_acknowledged'}

    cleared_command_ids = set()
    cleared_sleeve_ids = set()
    retained_commands = []
    cleared_commands = 0

    for entry in list(trade_state.order_commands or []):
        next_entry = dict(entry or {})
        status = _trim_text(next_entry.get('status')).lower() or 'queued'
        if status in active_command_statuses:
            cleared_commands += 1
            command_id = _trim_text(next_entry.get('id'))
            sleeve_id = _trim_text(next_entry.get('sleeve_id'))
            if command_id:
                cleared_command_ids.add(command_id)
            if sleeve_id:
                cleared_sleeve_ids.add(sleeve_id)
            continue
        retained_commands.append(next_entry)

    retained_intents = []
    cleared_intents = 0
    clear_message = f'Live dispatch queue cleared during {safe_reason}.'

    for entry in list(trade_state.order_intents or []):
        next_entry = dict(entry or {})
        status = _trim_text(next_entry.get('status')).lower() or 'queued'
        linked_command_id = _trim_text(next_entry.get('command_id'))
        sleeve_id = _trim_text(next_entry.get('sleeve_id'))
        should_clear = (
            status in active_intent_statuses
            or (linked_command_id and linked_command_id in cleared_command_ids)
        )
        if should_clear:
            cleared_intents += 1
            if sleeve_id:
                cleared_sleeve_ids.add(sleeve_id)
            next_entry['status'] = 'stale'
            next_entry['stale_at'] = now
            next_entry['rejection_message'] = _trim_text(next_entry.get('rejection_message')) or clear_message
        retained_intents.append(next_entry)

    if cleared_sleeve_ids:
        sleeve_states = dict(trade_state.sleeve_states or {})
        for sleeve_id in cleared_sleeve_ids:
            next_state = dict(sleeve_states.get(sleeve_id) or {})
            if not next_state:
                continue
            next_state['pending_action'] = None
            if not _trim_text(next_state.get('current_cycle_id')):
                next_state['pending_cycle_id'] = None
            sleeve_states[sleeve_id] = next_state
        trade_state.sleeve_states = sleeve_states

    trade_state.order_commands = retained_commands
    trade_state.order_intents = retained_intents
    if cleared_commands or cleared_intents:
        trade_state.last_error = None
        record_trade_runtime_event(
            'live_dispatch_queue_cleared',
            f'Cleared active live dispatch queue during {safe_reason}.',
            reason=safe_reason,
            cleared_commands=cleared_commands,
            cleared_intents=cleared_intents,
        )
    return {
        'cleared_commands': cleared_commands,
        'cleared_intents': cleared_intents,
    }


def process_trade_order_intents():
    trade_state = state.trade
    updated = []
    now = time.time()
    market_feed = _reconcile_trade_market_feed()
    market_feed_ready = _trim_text(market_feed.get('status')).lower() == 'healthy'

    for intent in list(trade_state.order_intents or []):
        next_intent = dict(intent)
        status = _trim_text(next_intent.get('status')).lower() or 'queued'
        linked_command_id = _trim_text(next_intent.get('command_id'))

        if status in {'broker_claimed', 'broker_acknowledged'} and linked_command_id:
            linked_command = _find_order_command(linked_command_id)
            if not linked_command:
                next_intent['status'] = 'stale'
                next_intent['stale_at'] = now
                next_intent['rejection_message'] = (
                    _trim_text(next_intent.get('rejection_message'))
                    or 'Linked broker command no longer exists in the runtime queue.'
                )
                updated.append(next_intent)
                continue

        if status == 'queued':
            if _is_intent_expired(next_intent):
                next_intent['status'] = 'expired'
                next_intent['expired_at'] = now
                next_intent['rejection_message'] = 'Signal expired before execution window.'
                record_trade_runtime_event(
                    'order_intent_expired',
                    f'Intent expired for {next_intent.get("sleeve_label") or next_intent.get("sleeve_id")}.',
                    sleeve_id=next_intent.get('sleeve_id'),
                    action=next_intent.get('action'),
                    side=next_intent.get('side'),
                    decision=next_intent.get('decision'),
                )
                updated.append(next_intent)
                continue
            if trade_state.execution_mode == 'paper':
                next_intent['status'] = 'acknowledged'
                next_intent['acknowledged_at'] = now
                metrics = dict(trade_state.metrics or {})
                metrics['ack_count'] = int(metrics.get('ack_count') or 0) + 1
                trade_state.metrics = metrics
                record_trade_runtime_event(
                    'order_intent_ack',
                    f'Acknowledged intent for {next_intent.get("sleeve_label") or next_intent.get("sleeve_id")}.',
                    sleeve_id=next_intent.get('sleeve_id'),
                    action=next_intent.get('action'),
                    side=next_intent.get('side'),
                )
            elif trade_state.execution_mode == 'live_mt5':
                if trade_state.live_dispatch_armed:
                    if not market_feed_ready:
                        updated.append(next_intent)
                        continue
                    sanitation = _sanitize_live_intent_before_dispatch(next_intent)
                    if sanitation:
                        next_intent['status'] = sanitation.get('status') or 'dispatch_blocked'
                        next_intent['rejection_message'] = sanitation.get('message')
                        record_trade_runtime_event(
                            sanitation.get('event_kind') or 'order_intent_blocked',
                            sanitation.get('message') or f'Live dispatch blocked for {next_intent.get("sleeve_label") or next_intent.get("sleeve_id")}.',
                            sleeve_id=next_intent.get('sleeve_id'),
                            action=next_intent.get('action'),
                            side=next_intent.get('side'),
                            broker_ticket=(
                                _trim_text((_select_broker_position_for_close(next_intent) or {}).get('ticket'))
                                or None
                            ),
                        )
                        updated.append(next_intent)
                        continue
                    command = _append_order_command(_build_order_command(next_intent))
                    next_intent['status'] = 'broker_queued'
                    next_intent['dispatched_at'] = now
                    next_intent['command_id'] = command.get('id') if command else next_intent.get('command_id')
                else:
                    next_intent['status'] = 'dispatch_blocked'
                    record_trade_runtime_event(
                        'order_intent_blocked',
                        f'Live dispatch blocked for {next_intent.get("sleeve_label") or next_intent.get("sleeve_id")}.',
                        sleeve_id=next_intent.get('sleeve_id'),
                        action=next_intent.get('action'),
                        side=next_intent.get('side'),
                    )
        elif status == 'dispatch_blocked':
            if trade_state.execution_mode == 'live_mt5' and trade_state.live_dispatch_armed:
                if not market_feed_ready:
                    updated.append(next_intent)
                    continue
                command = _append_order_command(_build_order_command(next_intent))
                next_intent['status'] = 'broker_queued'
                next_intent['dispatched_at'] = now
                next_intent['command_id'] = command.get('id') if command else next_intent.get('command_id')
        elif status == 'acknowledged':
            next_intent['status'] = 'filled' if trade_state.execution_mode == 'paper' else 'dispatched'
            next_intent['filled_at'] = now
            metrics = dict(trade_state.metrics or {})
            metrics['fill_count'] = int(metrics.get('fill_count') or 0) + 1
            trade_state.metrics = metrics
            record_trade_runtime_event(
                'order_intent_fill',
                f'Filled intent for {next_intent.get("sleeve_label") or next_intent.get("sleeve_id")}.',
                sleeve_id=next_intent.get('sleeve_id'),
                action=next_intent.get('action'),
                side=next_intent.get('side'),
                execution_mode=trade_state.execution_mode,
            )

        updated.append(next_intent)

    trade_state.order_intents = updated
    return build_trade_runtime_payload()


def auto_process_trade_order_intents_if_needed():
    trade_state = state.trade
    if not trade_state.armed:
        return build_trade_runtime_payload()

    execution_mode = _trim_text(trade_state.execution_mode).lower() or 'paper'
    if execution_mode == 'paper':
        auto_statuses = {'queued', 'acknowledged'}
    elif execution_mode == 'live_mt5':
        auto_statuses = {'queued', 'dispatch_blocked'}
    else:
        return build_trade_runtime_payload()

    iterations = 0
    while iterations < 4:
        iterations += 1
        pending_before = [
            (
                str(entry.get('id') or ''),
                str(entry.get('status') or ''),
            )
            for entry in list(trade_state.order_intents or [])
        ]
        if not any(status in auto_statuses for _, status in pending_before):
            break
        process_trade_order_intents()
        pending_after = [
            (
                str(entry.get('id') or ''),
                str(entry.get('status') or ''),
            )
            for entry in list(trade_state.order_intents or [])
        ]
        if pending_after == pending_before:
            break

    return build_trade_runtime_payload()


def record_trade_runtime_event(kind: str, message: str | None = None, **extra):
    trade_state = state.trade
    event = {
        'kind': _trim_text(kind) or 'event',
        'message': _trim_text(message),
        'timestamp': time.time(),
        **{key: value for key, value in extra.items() if value is not None},
    }
    trade_state.audit_events = [
        event,
        *list(trade_state.audit_events or []),
    ][:MAX_TRADE_AUDIT_EVENTS]
    trade_state.last_event_at = event['timestamp']
    metrics = dict(trade_state.metrics or {})
    metrics['event_count'] = int(metrics.get('event_count') or 0) + 1
    trade_state.metrics = metrics
    return event


def record_trade_latency_event(stage: str, latency_ms: float | int | None = None, **extra):
    trade_state = state.trade
    safe_latency = None if latency_ms is None else max(0.0, float(latency_ms))
    event = {
        'stage': _trim_text(stage) or 'unknown',
        'latency_ms': safe_latency,
        'timestamp': time.time(),
        **{key: value for key, value in extra.items() if value is not None},
    }
    trade_state.latency_events = [
        event,
        *list(trade_state.latency_events or []),
    ][:MAX_TRADE_LATENCY_EVENTS]

    metrics = dict(trade_state.metrics or {})
    if safe_latency is not None:
        metrics['last_latency_ms'] = safe_latency
        current_max = metrics.get('max_latency_ms')
        metrics['max_latency_ms'] = safe_latency if current_max is None else max(float(current_max), safe_latency)
    trade_state.metrics = metrics
    return event


def note_trade_bridge_heartbeat(payload: dict | None):
    trade_state = state.trade
    safe_payload = dict(payload or {})
    status = _trim_text(safe_payload.get('status')).lower() or 'idle'
    message = _trim_text(safe_payload.get('message'))
    request_id = _trim_text(safe_payload.get('request_id'))
    now = time.time()
    heartbeat_timeout = float(safe_payload.get('timeout_seconds') or trade_state.bridge_timeout_seconds or 8.0)
    bridge_online = bool(safe_payload.get('online'))
    if not bridge_online:
        bridge_online = True

    trade_state.bridge_online = bridge_online
    trade_state.bridge_last_status = status or None
    trade_state.bridge_last_message = message or None
    trade_state.bridge_last_request_id = request_id or None
    trade_state.bridge_last_heartbeat_at = now
    trade_state.bridge_timeout_seconds = max(1.0, heartbeat_timeout)
    account_position_mode = _trim_text(safe_payload.get('account_position_mode')).lower() or None
    if account_position_mode:
        trade_state.broker_account_position_mode = account_position_mode
    hedge_allowed = _parse_boolish(safe_payload.get('account_hedge_allowed'))
    if hedge_allowed is not None:
        trade_state.broker_account_hedge_allowed = hedge_allowed

    positions = safe_payload.get('positions')
    if isinstance(positions, list):
        trade_state.broker_positions = [
            {
                'ticket': _trim_text(entry.get('ticket')) or None,
                'symbol': _trim_text(entry.get('symbol')).upper() or None,
                'magic': int(entry.get('magic') or 0),
                'side': _trim_text(entry.get('side')).lower() or None,
                'volume': float(entry.get('volume') or 0.0),
            }
            for entry in positions
            if isinstance(entry, dict)
        ]
        trade_state.last_broker_positions_at = now

    symbol_rules = safe_payload.get('symbol_rules')
    if isinstance(symbol_rules, dict):
        next_rules = dict(getattr(trade_state, 'broker_symbol_rules', {}) or {})
        for symbol, entry in symbol_rules.items():
            safe_symbol = _trim_text(symbol).upper()
            if not safe_symbol or not isinstance(entry, dict):
                continue
            next_rules[safe_symbol] = {
                'symbol': safe_symbol,
                'digits': int(entry.get('digits')) if entry.get('digits') not in (None, '') else None,
                'point': float(entry.get('point')) if entry.get('point') not in (None, '') else None,
                'stops_level_points': int(entry.get('stops_level_points')) if entry.get('stops_level_points') not in (None, '') else None,
                'freeze_level_points': int(entry.get('freeze_level_points')) if entry.get('freeze_level_points') not in (None, '') else None,
                'updated_at': now,
            }
        trade_state.broker_symbol_rules = next_rules

    if trade_state.armed:
        trade_state.live = True
        trade_state.status = 'live'
    elif trade_state.status in {'idle', 'configured'}:
        trade_state.live = False

    record_trade_runtime_event(
        'bridge_heartbeat',
        message or 'MT5 heartbeat received.',
        bridge_status=status,
        request_id=request_id or None,
    )
    if (
        status in {'active', 'idle', 'ready'}
        and bridge_online
        and (
            _trim_text(trade_state.last_error).lower().startswith('symbolselect failed for ')
            or _is_nonfatal_market_sync_bridge_error_message(trade_state.last_error)
            or _is_nonfatal_idle_trade_command_poll_error_message(trade_state.last_error)
        )
    ):
        trade_state.last_error = None
    if trade_state.armed:
        _apply_runtime_resume_policy()


def note_trade_bridge_event(kind: str, payload: dict | None = None):
    trade_state = state.trade
    safe_payload = dict(payload or {})
    level = _trim_text(safe_payload.get('level')).lower() or 'info'
    message = _trim_text(safe_payload.get('message'))
    request_id = _trim_text(safe_payload.get('request_id'))
    bridge_status = _trim_text(safe_payload.get('status')).lower() or None
    message_lower = message.lower()
    is_trade_command_event = '/mt5/trade/commands/' in message_lower or 'trade command' in message_lower
    is_nonfatal_market_sync_error = _is_nonfatal_market_sync_bridge_error(kind, safe_payload)
    is_nonfatal_idle_trade_command_poll_error = _is_nonfatal_idle_trade_command_poll_error_message(message)
    ignore_as_runtime_error = (
        is_trade_command_event
        and not trade_state.armed
        and not list(trade_state.sleeves or [])
    )

    if (level == 'error' or kind in {'error', 'request_error', 'data_error', 'deinit_error'}) and (
        (trade_state.armed or is_trade_command_event)
        and not ignore_as_runtime_error
        and not is_nonfatal_market_sync_error
        and not is_nonfatal_idle_trade_command_poll_error
    ):
        trade_state.last_error = message or 'Bridge reported an error.'
        if trade_state.armed:
            trade_state.status = 'interrupted'
            trade_state.live = False

    record_trade_runtime_event(
        f'bridge_{_trim_text(kind).lower() or "event"}',
        message or 'MT5 bridge event received.',
        level=(
            'warning'
            if (is_nonfatal_market_sync_error or is_nonfatal_idle_trade_command_poll_error) and level == 'error'
            else level
        ),
        request_id=request_id or None,
        bridge_status=bridge_status,
    )


def note_trade_market_update(stage: str, symbol: str = '', timeframe: str = '', candle_count: int | None = None, latest_candle_time: int | None = None, candles: list | None = None):
    trade_state = state.trade
    now = time.time()
    safe_stage = _trim_text(stage).lower() or 'update'
    previous_candle_time = trade_state.market_latest_candle_time
    next_candle_time = int(latest_candle_time) if latest_candle_time is not None else previous_candle_time
    trade_state.last_market_event_stage = safe_stage
    trade_state.last_market_event_candle_time = next_candle_time
    trade_state.last_market_event_new_candle = (
        safe_stage == 'candle_update'
        and next_candle_time is not None
        and previous_candle_time is not None
        and int(next_candle_time) != int(previous_candle_time)
    )
    if safe_stage in {'history_loaded', 'candle_update'}:
        trade_state.market_history_ready = True
    if safe_stage in {'history_loaded', 'candle_update'}:
        trade_state.market_last_update_at = now
    if candle_count is not None:
        trade_state.last_event_at = now
    record_trade_runtime_event(
        f'market_{safe_stage}',
        'Market runtime updated.',
        symbol=_trim_text(symbol).upper() or None,
        timeframe=_trim_text(timeframe).upper() or None,
        candle_count=(None if candle_count is None else int(candle_count)),
    )
    record_trade_latency_event(
        f'market_{safe_stage}',
        latency_ms=0.0,
        symbol=_trim_text(symbol).upper() or None,
        timeframe=_trim_text(timeframe).upper() or None,
    )
    if latest_candle_time is not None:
        trade_state.market_latest_candle_time = int(latest_candle_time)
    if isinstance(candles, list):
        trade_state.market_snapshot_symbol = _trim_text(symbol).upper() or trade_state.market_snapshot_symbol
        trade_state.market_snapshot_timeframe = _trim_text(timeframe).upper() or trade_state.market_snapshot_timeframe
        trade_state.market_snapshot_candles = list(candles)
        trade_state.market_snapshot_bars = len(trade_state.market_snapshot_candles)
    if state.trade.armed:
        _reconcile_trade_market_feed()


def configure_trade_runtime(payload: dict | None):
    trade_state = state.trade
    safe_payload = dict(payload or {})
    mode = _normalize_trade_portfolio_mode(safe_payload.get('mode'))
    execution_mode = _trim_text(
        safe_payload.get('execution_mode', safe_payload.get('executionMode'))
    ).lower() or 'paper'
    broker_profile_id = _trim_text(
        safe_payload.get('broker_profile_id', safe_payload.get('brokerProfileId'))
    )
    broker_profile_label = _trim_text(
        safe_payload.get('broker_profile_label', safe_payload.get('brokerProfileLabel'))
    )
    if execution_mode not in {'simulation_backtest', 'paper', 'live_mt5'}:
        execution_mode = 'paper'
    same_symbol_execution_policy = _normalize_same_symbol_execution_policy(
        safe_payload.get('same_symbol_execution_policy', safe_payload.get('sameSymbolExecutionPolicy'))
    )
    signal_validity_seconds = max(
        0,
        int(safe_payload.get('signal_validity_seconds', safe_payload.get('signalValiditySeconds', 10)) or 0),
    )
    resolved_structure = resolve_trade_runtime_structure(safe_payload)
    sleeves = list(resolved_structure.get('sleeves') or [])
    portfolios = list(resolved_structure.get('portfolios') or [])

    trade_state.mode = mode
    trade_state.execution_mode = execution_mode
    trade_state.broker_profile_id = broker_profile_id
    trade_state.broker_profile_label = broker_profile_label
    trade_state.same_symbol_execution_policy = same_symbol_execution_policy
    trade_state.signal_validity_seconds = signal_validity_seconds
    trade_state.portfolio_structure_version = int(
        resolved_structure.get('portfolio_structure_version') or 1
    )
    trade_state.live_dispatch_armed = bool(
        safe_payload.get('live_dispatch_armed', safe_payload.get('liveDispatchArmed', False))
    )
    trade_state.latency_budget_ms = max(
        1,
        int(safe_payload.get('latency_budget_ms', safe_payload.get('latencyBudgetMs', 150)) or 150),
    )
    trade_state.portfolios = portfolios
    trade_state.sleeves = sleeves
    trade_state.sleeve_states = {
        entry['id']: {
            'sleeve_id': entry['id'],
            'label': entry['label'],
            'status': 'configured',
            'decision': 'hold',
            'position': 0,
            'symbol': entry['symbol'],
            'timeframe': entry['timeframe'],
            'portfolio_id': _trim_text(entry.get('portfolio_id')) or None,
            'portfolio_label': _trim_text(entry.get('portfolio_label')) or None,
            'pipeline_id': _trim_text(entry.get('pipeline_id')) or None,
            'pipeline_label': _trim_text(entry.get('pipeline_label')) or None,
            'portfolio_mode': _trim_text(entry.get('portfolio_mode')) or mode,
            'volume_mode': _trim_text(entry.get('volume_mode')) or 'fixed_volume',
            'last_evaluated_at': None,
            'last_bar_time': None,
            'current_cycle_id': None,
            'pending_cycle_id': None,
            'desired_position': 0,
            'desired_side': 'flat',
            'actual_position_side': 'flat',
            'broker_position_side': None,
            'broker_position_ticket': None,
            'broker_position_tickets': [],
            'broker_position_count': 0,
            'reconciliation_status': 'match_flat',
            'reconciliation_detail': 'No broker position and runtime expects flat.',
            'last_error': None,
        }
        for entry in sleeves
    }
    trade_state.order_intents = []
    trade_state.order_commands = []
    trade_state.trade_cycle_sequence = 0
    trade_state.last_configured_at = time.time()
    trade_state.market_feed_status = 'idle'
    trade_state.market_feed_issue = None
    trade_state.last_market_sanitize_at = None
    trade_state.last_error = None
    if trade_state.status == 'idle':
        trade_state.status = 'configured'
    _rebuild_active_symbols()

    record_trade_runtime_event(
        'configure',
        f'Configured trade runtime with {len(sleeves)} compiled sleeve(s) across {len(portfolios)} portfolio(s).',
        mode=trade_state.mode,
        execution_mode=trade_state.execution_mode,
        same_symbol_execution_policy=trade_state.same_symbol_execution_policy,
        signal_validity_seconds=trade_state.signal_validity_seconds,
        live_dispatch_armed=trade_state.live_dispatch_armed,
        sleeve_count=len(sleeves),
        portfolio_count=len(portfolios),
        portfolio_structure_version=trade_state.portfolio_structure_version,
    )
    return build_trade_runtime_payload()


def arm_trade_runtime():
    trade_state = state.trade
    trade_state.armed = True
    trade_state.live = False
    trade_state.status = 'armed'
    trade_state.last_armed_at = time.time()
    trade_state.market_feed_status = 'waiting'
    trade_state.market_feed_issue = 'Waiting for the first live market update.'
    trade_state.last_error = None
    record_trade_runtime_event(
        'arm',
        'Trade runtime armed.',
        mode=trade_state.mode,
        sleeve_count=len(list(trade_state.sleeves or [])),
    )
    market_ready = bool(trade_state.market_history_ready)
    if not market_ready and not _is_trade_isolated_service_mode():
        market_ready = bool(getattr(state.bridge, 'history_ready', False))

    if market_ready and list(trade_state.sleeves or []):
        # A previously stale cached snapshot should not block the first fresh
        # evaluation right after arming. Let evaluation request live data again.
        trade_state.market_history_ready = False
        trade_state.market_last_update_at = None
        trade_state.market_latest_candle_time = None
        trade_state.market_snapshot_symbol = ''
        trade_state.market_snapshot_timeframe = ''
        trade_state.market_snapshot_candles = []
        trade_state.market_snapshot_bars = 0
        payload = evaluate_trade_runtime(trigger='arm')
        return auto_process_trade_order_intents_if_needed() if payload.get('armed') else payload

    return build_trade_runtime_payload()


def arm_trade_live_dispatch():
    trade_state = state.trade
    trade_state.live_dispatch_armed = True
    trade_state.last_live_dispatch_armed_at = time.time()
    trade_state.last_error = None
    record_trade_runtime_event(
        'arm_live_dispatch',
        'Live MT5 dispatch armed.',
        execution_mode=trade_state.execution_mode,
    )
    if trade_state.armed and _trim_text(trade_state.execution_mode).lower() == 'live_mt5':
        return auto_process_trade_order_intents_if_needed()
    return build_trade_runtime_payload()


def disarm_trade_live_dispatch(reason: str = 'manual'):
    trade_state = state.trade
    trade_state.live_dispatch_armed = False
    trade_state.last_live_dispatch_disarmed_at = time.time()
    _clear_active_live_dispatch_queue(reason=f'live dispatch disarm ({_trim_text(reason) or "manual"})')
    record_trade_runtime_event(
        'disarm_live_dispatch',
        f'Live MT5 dispatch disarmed ({_trim_text(reason) or "manual"}).',
        reason=_trim_text(reason) or 'manual',
    )
    return build_trade_runtime_payload()


def disarm_trade_runtime(reason: str = 'manual'):
    trade_state = state.trade
    trade_state.armed = False
    trade_state.live_dispatch_armed = False
    trade_state.live = False
    trade_state.status = 'idle'
    trade_state.last_disarmed_at = time.time()
    trade_state.market_feed_status = 'idle'
    trade_state.market_feed_issue = None
    _clear_active_live_dispatch_queue(reason=f'runtime disarm ({_trim_text(reason) or "manual"})')
    record_trade_runtime_event(
        'disarm',
        f'Trade runtime disarmed ({_trim_text(reason) or "manual"}).',
        reason=_trim_text(reason) or 'manual',
    )
    return build_trade_runtime_payload()


def build_trade_runtime_payload():
    trade_state = state.trade
    market_feed = _reconcile_trade_market_feed()
    commands = []
    for entry in list(trade_state.order_commands or []):
        next_entry = dict(entry or {})
        reference_at = (
            next_entry.get('acknowledged_at')
            or next_entry.get('claimed_at')
            or next_entry.get('created_at')
        )
        if reference_at is not None and _trim_text(next_entry.get('status')).lower() in {'queued', 'claimed', 'acknowledged'}:
            next_entry['age_seconds'] = round(max(0.0, time.time() - float(reference_at)), 3)
        commands.append(next_entry)
    return _sanitize_trade_payload_value({
        'mode': trade_state.mode,
        'execution_mode': trade_state.execution_mode,
        'broker_profile_id': getattr(trade_state, 'broker_profile_id', ''),
        'broker_profile_label': getattr(trade_state, 'broker_profile_label', ''),
        'portfolio_structure_version': int(getattr(trade_state, 'portfolio_structure_version', 1) or 1),
        'status': trade_state.status,
        'armed': trade_state.armed,
        'live_dispatch_armed': trade_state.live_dispatch_armed,
        'live': trade_state.live,
        'same_symbol_execution_policy': trade_state.same_symbol_execution_policy,
        'signal_validity_seconds': trade_state.signal_validity_seconds,
        'latency_budget_ms': trade_state.latency_budget_ms,
        'broker_account_position_mode': trade_state.broker_account_position_mode,
        'broker_account_hedge_allowed': trade_state.broker_account_hedge_allowed,
        'market_feed': market_feed,
        'portfolios': list(getattr(trade_state, 'portfolios', []) or []),
        'sleeves': list(trade_state.sleeves or []),
        'sleeve_states': dict(trade_state.sleeve_states or {}),
        'active_symbols': list(trade_state.active_symbols or []),
        'broker_symbol_rules': dict(getattr(trade_state, 'broker_symbol_rules', {}) or {}),
        'order_intents': list(trade_state.order_intents or []),
        'order_commands': commands,
        'audit_events': list(trade_state.audit_events or []),
        'latency_events': list(trade_state.latency_events or []),
        'metrics': dict(trade_state.metrics or {}),
        'last_configured_at': trade_state.last_configured_at,
        'last_armed_at': trade_state.last_armed_at,
        'last_live_dispatch_armed_at': trade_state.last_live_dispatch_armed_at,
        'last_live_dispatch_disarmed_at': trade_state.last_live_dispatch_disarmed_at,
        'last_disarmed_at': trade_state.last_disarmed_at,
        'last_event_at': trade_state.last_event_at,
        'last_error': trade_state.last_error,
    })


def build_trade_runtime_health_payload():
    payload = build_trade_runtime_payload()
    lightweight_sleeves = []
    for entry in list(payload.get('sleeves') or []):
        safe_entry = dict(entry or {})
        lightweight_sleeves.append({
            'id': safe_entry.get('id'),
            'label': safe_entry.get('label'),
            'enabled': safe_entry.get('enabled') is not False,
            'symbol': safe_entry.get('symbol'),
            'timeframe': safe_entry.get('timeframe'),
            'volume': safe_entry.get('volume'),
            'volume_mode': safe_entry.get('volume_mode'),
            'portfolio_id': safe_entry.get('portfolio_id'),
            'pipeline_id': safe_entry.get('pipeline_id'),
            'portfolio_mode': safe_entry.get('portfolio_mode'),
            'source_strategy_id': safe_entry.get('source_strategy_id'),
        })

    return {
        'mode': payload.get('mode'),
        'execution_mode': payload.get('execution_mode'),
        'broker_profile_id': payload.get('broker_profile_id'),
        'broker_profile_label': payload.get('broker_profile_label'),
        'portfolio_structure_version': payload.get('portfolio_structure_version'),
        'status': payload.get('status'),
        'armed': payload.get('armed'),
        'live_dispatch_armed': payload.get('live_dispatch_armed'),
        'live': payload.get('live'),
        'same_symbol_execution_policy': payload.get('same_symbol_execution_policy'),
        'signal_validity_seconds': payload.get('signal_validity_seconds'),
        'latency_budget_ms': payload.get('latency_budget_ms'),
        'broker_account_position_mode': payload.get('broker_account_position_mode'),
        'broker_account_hedge_allowed': payload.get('broker_account_hedge_allowed'),
        'market_feed': dict(payload.get('market_feed') or {}),
        'portfolios': list(payload.get('portfolios') or []),
        'sleeves': lightweight_sleeves,
        'sleeve_states': dict(payload.get('sleeve_states') or {}),
        'active_symbols': list(payload.get('active_symbols') or []),
        'order_intent_count': len(list(payload.get('order_intents') or [])),
        'order_command_count': len(list(payload.get('order_commands') or [])),
        'order_intent_status_counts': {
            status: sum(
                1
                for entry in list(payload.get('order_intents') or [])
                if _trim_text((entry or {}).get('status')).lower() == status
            )
            for status in sorted({
                _trim_text((entry or {}).get('status')).lower() or 'unknown'
                for entry in list(payload.get('order_intents') or [])
            })
        },
        'order_command_status_counts': {
            status: sum(
                1
                for entry in list(payload.get('order_commands') or [])
                if _trim_text((entry or {}).get('status')).lower() == status
            )
            for status in sorted({
                _trim_text((entry or {}).get('status')).lower() or 'unknown'
                for entry in list(payload.get('order_commands') or [])
            })
        },
        'order_intents_preview': [
            {
                'id': (entry or {}).get('id'),
                'status': (entry or {}).get('status'),
                'action': (entry or {}).get('action'),
                'side': (entry or {}).get('side'),
                'decision': (entry or {}).get('decision'),
                'rejection_message': (entry or {}).get('rejection_message'),
                'command_id': (entry or {}).get('command_id'),
                'bridge_session_id': (entry or {}).get('bridge_session_id'),
                'claimed_at': (entry or {}).get('claimed_at'),
                'acknowledged_at': (entry or {}).get('acknowledged_at'),
            }
            for entry in list(payload.get('order_intents') or [])[:3]
        ],
        'order_commands_preview': [
            {
                'id': (entry or {}).get('id'),
                'status': (entry or {}).get('status'),
                'action': (entry or {}).get('action'),
                'side': (entry or {}).get('side'),
                'message': (entry or {}).get('message'),
                'source_intent_id': (entry or {}).get('source_intent_id'),
                'bridge_session_id': (entry or {}).get('bridge_session_id'),
                'created_at': (entry or {}).get('created_at'),
                'claimed_at': (entry or {}).get('claimed_at'),
                'acknowledged_at': (entry or {}).get('acknowledged_at'),
            }
            for entry in list(payload.get('order_commands') or [])[:3]
        ],
        'metrics': dict(payload.get('metrics') or {}),
        'last_configured_at': payload.get('last_configured_at'),
        'last_armed_at': payload.get('last_armed_at'),
        'last_live_dispatch_armed_at': payload.get('last_live_dispatch_armed_at'),
        'last_live_dispatch_disarmed_at': payload.get('last_live_dispatch_disarmed_at'),
        'last_disarmed_at': payload.get('last_disarmed_at'),
        'last_event_at': payload.get('last_event_at'),
        'last_error': payload.get('last_error'),
    }


def evaluate_trade_runtime(trigger: str = 'manual'):
    trade_state = state.trade
    market_feed = _reconcile_trade_market_feed()
    if _trim_text(market_feed.get('status')).lower() in {'stale', 'closed'}:
        return build_trade_runtime_payload()
    default_bars = _get_default_trade_bars()
    next_states = dict(trade_state.sleeve_states or {})

    for sleeve in list(trade_state.sleeves or []):
        sleeve_id = str(sleeve.get('id') or '').strip()
        if not sleeve_id:
            continue

        if sleeve.get('enabled') is False:
            next_states[sleeve_id] = {
                **dict(next_states.get(sleeve_id) or {}),
                'sleeve_id': sleeve_id,
                'label': sleeve.get('label'),
                'status': 'disabled',
                'decision': 'disabled',
                'position': 0,
                'symbol': sleeve.get('symbol'),
                'timeframe': sleeve.get('timeframe'),
                'last_evaluated_at': time.time(),
                'last_error': None,
            }
            continue

        started_at = perf_counter()
        symbol_name = str(sleeve.get('symbol') or '').strip().upper() or 'EURUSD'
        timeframe = str(sleeve.get('timeframe') or '').strip().upper() or 'M1'
        context = _ensure_trade_market_data(symbol_name, timeframe, default_bars)

        if not context.get('ready'):
            next_states[sleeve_id] = {
                **dict(next_states.get(sleeve_id) or {}),
                'sleeve_id': sleeve_id,
                'label': sleeve.get('label'),
                'status': 'waiting_market_data',
                'decision': 'waiting_market_data',
                'position': 0,
                'symbol': symbol_name,
                'timeframe': timeframe,
                'request_status': context.get('request_status'),
                'last_evaluated_at': time.time(),
                'last_error': context.get('error'),
            }
            continue

        try:
            trade_state.market_history_ready = True
            if context.get('last_update_at') is not None:
                trade_state.market_last_update_at = context.get('last_update_at')
            if context.get('latest_candle_time') is not None:
                trade_state.market_latest_candle_time = int(context.get('latest_candle_time'))
            raw_candles = list(context.get('candles') or [])[-default_bars:]
            evaluation_candles = raw_candles
            if _trim_text(trade_state.execution_mode).lower() == 'live_mt5':
                evaluation_candles = _freeze_latest_runtime_signal_candle(raw_candles)
            symbol = Symbol(
                name=symbol_name,
                timeframe=timeframe,
                bars=default_bars,
                candles=evaluation_candles,
            )
            indicator_payload = _get_trade_indicator_payload(sleeve)
            applied_indicators = []
            if indicator_payload:
                applied_indicators, _ = apply_indicator_payload(symbol, indicator_payload)
            strategy = _build_strategy_instance(sleeve.get('strategy'), applied_indicators)
            execution = strategy.execute(symbol)
            decision = _normalize_live_decision_against_broker(
                sleeve,
                _derive_sleeve_decision(execution),
                dict(next_states.get(sleeve_id) or {}),
            )
            strategy_position = int(decision.get('strategy_position') or 0)
            visible_position = strategy_position
            if _trim_text(trade_state.execution_mode).lower() == 'live_mt5':
                broker_positions = _list_broker_positions_for_sleeve(sleeve)
                broker_summary = _summarize_broker_positions(broker_positions)
                active_open_intents = _list_active_order_intents_for_sleeve(sleeve_id, action='open')
                active_open_commands = _list_active_order_commands_for_sleeve(sleeve_id, action='open')
                aggregate_side = _trim_text(broker_summary.get('aggregate_side')).lower()
                if aggregate_side == 'long':
                    visible_position = 1
                elif aggregate_side == 'short':
                    visible_position = -1
                elif len(broker_positions) == 0:
                    visible_position = 0
                    if (
                        strategy_position != 0
                        and not active_open_intents
                        and not active_open_commands
                        and _trim_text(decision.get('pending_action')).lower() in {'', 'none'}
                        and _trim_text(decision.get('order_type')).lower() in {'', 'none'}
                    ):
                        strategy_position = 0
                else:
                    visible_position = 0
            elapsed_ms = max(0.0, (perf_counter() - started_at) * 1000.0)
            previous_decision = dict(next_states.get(sleeve_id) or {}).get('decision')
            next_states[sleeve_id] = {
                **dict(next_states.get(sleeve_id) or {}),
                'sleeve_id': sleeve_id,
                'label': sleeve.get('label'),
                'status': decision['status'],
                'decision': decision['decision'],
                'position': visible_position,
                'strategy_position': strategy_position,
                'pending_action': decision['pending_action'],
                'order_type': decision['order_type'],
                'symbol': symbol_name,
                'timeframe': timeframe,
                'bars': default_bars,
                'last_bar_time': decision['bar_time'],
                'long_take_profit_price': decision.get('long_take_profit_price'),
                'long_stop_loss_price': decision.get('long_stop_loss_price'),
                'long_trailing_stop_price': decision.get('long_trailing_stop_price'),
                'long_open_price': decision.get('long_open_price'),
                'short_take_profit_price': decision.get('short_take_profit_price'),
                'short_stop_loss_price': decision.get('short_stop_loss_price'),
                'short_trailing_stop_price': decision.get('short_trailing_stop_price'),
                'short_open_price': decision.get('short_open_price'),
                'last_evaluated_at': time.time(),
                'last_latency_ms': round(elapsed_ms, 3),
                'last_error': None,
            }
            record_trade_latency_event(
                'sleeve_evaluation',
                latency_ms=elapsed_ms,
                sleeve_id=sleeve_id,
                symbol=symbol_name,
                timeframe=timeframe,
                trigger=trigger,
            )
            metrics = dict(trade_state.metrics or {})
            metrics['decision_count'] = int(metrics.get('decision_count') or 0) + 1
            trade_state.metrics = metrics
            if previous_decision != decision['decision']:
                record_trade_runtime_event(
                    'sleeve_decision',
                    f'Sleeve {sleeve.get("label") or sleeve_id} decision: {decision["decision"]}.',
                    sleeve_id=sleeve_id,
                    symbol=symbol_name,
                    timeframe=timeframe,
                    decision=decision['decision'],
                    position=visible_position,
                    strategy_position=strategy_position,
                )
            if trade_state.execution_mode in {'paper', 'live_mt5'}:
                intent = _build_order_intent(
                    sleeve_id=sleeve_id,
                    sleeve=sleeve,
                    sleeve_state=next_states[sleeve_id],
                    trigger=trigger,
                )
                if intent is not None:
                    _append_order_intent(intent)
        except Exception as error:
            next_states[sleeve_id] = {
                **dict(next_states.get(sleeve_id) or {}),
                'sleeve_id': sleeve_id,
                'label': sleeve.get('label'),
                'status': 'error',
                'decision': 'error',
                'position': 0,
                'symbol': symbol_name,
                'timeframe': timeframe,
                'last_evaluated_at': time.time(),
                'last_error': str(error),
            }
            record_trade_runtime_event(
                'sleeve_error',
                str(error),
                sleeve_id=sleeve_id,
                symbol=symbol_name,
                timeframe=timeframe,
            )

    trade_state.sleeve_states = next_states
    _apply_runtime_resume_policy()
    return build_trade_runtime_payload()

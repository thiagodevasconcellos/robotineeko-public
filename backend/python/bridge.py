from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
import os
import struct
import time
import uuid


try:
    from .app_state import state
    from .config import build_service_config
    from .indicator_registry import normalize_indicator_feature_name
    from .runtime.market_runtime import (
        build_market_runtime_payload,
        mark_candle_update,
        mark_history_loaded,
        reset_market_runtime,
    )
    from .services.market_data_service import ensure_market_data
    from .services.runtime_service import build_runtime_service_payload, run_runtime_maintenance
    from .services.trade_runtime_service import build_trade_runtime_payload
    from .services.trade_service_proxy import (
        forward_trade_request,
        get_trade_runtime_via_service,
        get_trade_service_health,
        post_trade_internal,
    )
    from .trade_runtime_contract import TradeRuntimeConfigureRequest
    from .services.auth_service import build_guest_access_denial_payload, require_request_auth
except ImportError:
    from app_state import state
    from config import build_service_config
    from indicator_registry import normalize_indicator_feature_name
    from runtime.market_runtime import (
        build_market_runtime_payload,
        mark_candle_update,
        mark_history_loaded,
        reset_market_runtime,
    )
    from services.market_data_service import ensure_market_data
    from services.runtime_service import build_runtime_service_payload, run_runtime_maintenance
    from services.trade_runtime_service import build_trade_runtime_payload
    from services.trade_service_proxy import (
        forward_trade_request,
        get_trade_runtime_via_service,
        get_trade_service_health,
        post_trade_internal,
    )
    from trade_runtime_contract import TradeRuntimeConfigureRequest
    from services.auth_service import build_guest_access_denial_payload, require_request_auth

app = FastAPI()
SERVICE_STARTED_AT = time.time()
SERVICE_CONFIG = build_service_config()

app.add_middleware(
    CORSMiddleware,
    allow_origins=SERVICE_CONFIG['cors_origins'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def enforce_guest_access_policy(request: Request, call_next):
    denial_payload = build_guest_access_denial_payload(request)
    if denial_payload:
        return JSONResponse(status_code=403, content=denial_payload)
    return await call_next(request)

LEGACY_CANDLE_STRUCT = struct.Struct('<iffff')
VOLUME_CANDLE_STRUCT = struct.Struct('<ifffff')
DUAL_VOLUME_CANDLE_STRUCT = struct.Struct('<ifffffff')
TRADE_MARKET_UPDATE_CANDLE_LIMIT = 2000


def _require_trade_internal(request: Request):
    expected_token = _trim_bridge_text(SERVICE_CONFIG.get('trade_internal_token'))
    if not expected_token:
        return
    provided_token = _trim_bridge_text(request.headers.get('x-robotineeko-trade-internal-token'))
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail={'error': 'Internal trade token required.'})


def build_market_cache_key(symbol: str, timeframe: str, bars: int):
    return f'{str(symbol or "").strip().upper()}|{str(timeframe or "").strip().upper()}|{max(1, int(bars or 1))}'


def _cache_snapshot_ready_for_request(snapshot: dict | None, bars: int):
    candles = list((snapshot or {}).get('candles') or [])
    try:
        loaded_bars = int((snapshot or {}).get('bars_loaded') or len(candles))
    except Exception:
        loaded_bars = len(candles)
    safe_bars = max(1, int(bars or 1))
    return len(candles) >= safe_bars and min(max(0, loaded_bars), len(candles)) >= safe_bars


def _trim_bridge_text(value):
    return str(value or '').strip()


def _parse_bridge_plain_payload(raw: bytes):
    text = raw.decode('utf-8', errors='replace')
    payload = {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        safe_key = _trim_bridge_text(key)
        if not safe_key:
            continue
        payload[safe_key] = value.strip()
    return payload


def _parse_bridge_positions(value: str | None):
    entries = []
    for chunk in str(value or '').split(';'):
        safe_chunk = chunk.strip()
        if not safe_chunk:
            continue
        parts = [part.strip() for part in safe_chunk.split(',')]
        if len(parts) < 5:
            continue
        entries.append({
            'ticket': parts[0] or None,
            'symbol': parts[1].upper() if parts[1] else None,
            'magic': int(parts[2] or 0),
            'side': parts[3].lower() if parts[3] else None,
            'volume': float(parts[4] or 0.0),
        })
    return entries


def _slice_trade_market_update_candles(candles: list[dict] | None):
    safe_candles = list(candles or [])
    safe_limit = max(1, int(TRADE_MARKET_UPDATE_CANDLE_LIMIT))
    if len(safe_candles) <= safe_limit:
        return safe_candles
    return safe_candles[-safe_limit:]


def _parse_bridge_symbol_list(value: str | None):
    seen = set()
    symbols = []
    for chunk in str(value or '').split(';'):
        safe_symbol = _trim_bridge_text(chunk).upper()
        if not safe_symbol or safe_symbol in seen:
            continue
        seen.add(safe_symbol)
        symbols.append(safe_symbol)
    return symbols


def _parse_bridge_boolish(value):
    normalized = _trim_bridge_text(value).lower()
    if not normalized:
        return None
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    return None


def _remember_bridge_event(kind: str, message: str | None = None, **extra):
    bridge_state = state.bridge
    event = {
        'kind': _trim_bridge_text(kind),
        'message': _trim_bridge_text(message),
        'timestamp': time.time(),
        **{key: value for key, value in extra.items() if value is not None},
    }
    bridge_state.ea_recent_events = [
        event,
        *list(bridge_state.ea_recent_events or []),
    ][:25]
    bridge_state.ea_last_event = event['kind'] or bridge_state.ea_last_event
    bridge_state.ea_last_event_at = event['timestamp']
    if event['message']:
        bridge_state.ea_last_message = event['message']


def note_bridge_heartbeat(payload: dict | None):
    bridge_state = state.bridge
    safe_payload = dict(payload or {})
    now = time.time()
    session_id = _trim_bridge_text(safe_payload.get('session_id'))
    status = _trim_bridge_text(safe_payload.get('status') or 'idle').lower() or 'idle'
    message = _trim_bridge_text(safe_payload.get('message'))
    request_id = _trim_bridge_text(safe_payload.get('request_id'))

    if session_id:
        bridge_state.ea_session_id = session_id
    bridge_state.ea_last_status = status
    bridge_state.ea_last_message = message or bridge_state.ea_last_message
    bridge_state.ea_last_request_id = request_id or bridge_state.ea_last_request_id
    account_position_mode = _trim_bridge_text(safe_payload.get('account_position_mode')).lower() or None
    if account_position_mode:
        bridge_state.ea_account_position_mode = account_position_mode
    hedge_allowed = _parse_bridge_boolish(safe_payload.get('account_hedge_allowed'))
    if hedge_allowed is not None:
        bridge_state.ea_account_hedge_allowed = hedge_allowed
    market_watch_symbols = _parse_bridge_symbol_list(safe_payload.get('market_watch_symbols'))
    if market_watch_symbols:
        bridge_state.ea_market_watch_symbols = market_watch_symbols
    market_watch_exhaustive = _parse_bridge_boolish(safe_payload.get('market_watch_exhaustive'))
    if market_watch_exhaustive is not None:
        bridge_state.ea_market_watch_exhaustive = market_watch_exhaustive
    bridge_state.ea_last_heartbeat_at = now
    if (
        status in {'active', 'idle', 'ready'}
        and not _trim_bridge_text(safe_payload.get('error'))
        and not _trim_bridge_text(safe_payload.get('last_error'))
    ):
        bridge_state.ea_last_error = None
        bridge_state.ea_last_error_at = None


def note_bridge_error(kind: str, message: str, payload: dict | None = None):
    bridge_state = state.bridge
    safe_payload = dict(payload or {})
    now = time.time()
    safe_message = _trim_bridge_text(message)
    bridge_state.ea_last_error = safe_message
    bridge_state.ea_last_error_at = now
    if _trim_bridge_text(safe_payload.get('session_id')):
        bridge_state.ea_session_id = _trim_bridge_text(safe_payload.get('session_id'))
    if _trim_bridge_text(safe_payload.get('request_id')):
        bridge_state.ea_last_request_id = _trim_bridge_text(safe_payload.get('request_id'))
    _remember_bridge_event(
        kind,
        safe_message,
        level='error',
        request_id=_trim_bridge_text(safe_payload.get('request_id')) or None,
    )


def build_bridge_agent_payload():
    bridge_state = state.bridge
    last_heartbeat_at = bridge_state.ea_last_heartbeat_at
    timeout_seconds = max(1.0, float(bridge_state.ea_timeout_seconds or 8.0))
    heartbeat_age = (time.time() - last_heartbeat_at) if last_heartbeat_at else None
    online = bool(last_heartbeat_at and heartbeat_age is not None and heartbeat_age <= timeout_seconds)
    return {
        'session_id': bridge_state.ea_session_id,
        'online': online,
        'stale': bool(last_heartbeat_at) and not online,
        'last_status': bridge_state.ea_last_status,
        'last_message': bridge_state.ea_last_message,
        'last_error': bridge_state.ea_last_error,
        'last_error_at': bridge_state.ea_last_error_at,
        'last_event': bridge_state.ea_last_event,
        'last_event_at': bridge_state.ea_last_event_at,
        'last_heartbeat_at': last_heartbeat_at,
        'heartbeat_age_seconds': round(heartbeat_age, 3) if heartbeat_age is not None else None,
        'last_request_id': bridge_state.ea_last_request_id,
        'account_position_mode': bridge_state.ea_account_position_mode,
        'account_hedge_allowed': bridge_state.ea_account_hedge_allowed,
        'market_watch_symbols': list(bridge_state.ea_market_watch_symbols or []),
        'market_watch_exhaustive': bool(bridge_state.ea_market_watch_exhaustive),
        'timeout_seconds': timeout_seconds,
        'recent_events': list(bridge_state.ea_recent_events or []),
        'trade_commands': {
            'poll_count': int(bridge_state.trade_command_poll_count or 0),
            'last_polled_at': bridge_state.trade_command_last_polled_at,
            'last_command_id': bridge_state.trade_command_last_command_id,
            'last_command_at': bridge_state.trade_command_last_command_at,
            'last_ack_id': bridge_state.trade_command_last_ack_id,
            'last_ack_at': bridge_state.trade_command_last_ack_at,
            'last_result_id': bridge_state.trade_command_last_result_id,
            'last_result_status': bridge_state.trade_command_last_result_status,
            'last_result_at': bridge_state.trade_command_last_result_at,
        },
    }


def _parse_bridge_symbol_rules(payload: dict | None):
    safe_payload = dict(payload or {})
    symbol = _trim_bridge_text(safe_payload.get('symbol')).upper()
    if not symbol:
        return {}

    try:
        digits = int(safe_payload.get('symbol_digits'))
    except Exception:
        digits = None
    try:
        point = float(safe_payload.get('symbol_point'))
    except Exception:
        point = None
    try:
        stops_level_points = int(safe_payload.get('symbol_stops_level_points'))
    except Exception:
        stops_level_points = None
    try:
        freeze_level_points = int(safe_payload.get('symbol_freeze_level_points'))
    except Exception:
        freeze_level_points = None

    if digits is None and point is None and stops_level_points is None and freeze_level_points is None:
        return {}

    return {
        symbol: {
            'symbol': symbol,
            'digits': digits,
            'point': point,
            'stops_level_points': stops_level_points,
            'freeze_level_points': freeze_level_points,
            'updated_at': time.time(),
        }
    }


def build_market_snapshot_payload(request_id: str | None = None):
    bridge_state = state.bridge
    cache_key = build_market_cache_key(
        bridge_state.history_meta.get('symbol') or bridge_state.request.get('symbol'),
        bridge_state.history_meta.get('timeframe') or bridge_state.request.get('timeframe'),
        bridge_state.history_meta.get('requested_bars') or bridge_state.request.get('bars'),
    )
    return {
        'request_id': request_id,
        'cache_key': cache_key,
        'symbol': bridge_state.history_meta.get('symbol'),
        'timeframe': bridge_state.history_meta.get('timeframe'),
        'bars_requested': bridge_state.history_meta.get('requested_bars'),
        'bars_loaded': len(bridge_state.candles),
        'first_time': bridge_state.history_meta.get('first_time'),
        'last_time': bridge_state.history_meta.get('last_time'),
        'candles': list(bridge_state.candles),
        'provider_meta': {
            'source': 'mt5_bridge',
            'bridge_revision': bridge_state.revision,
        },
        'built_at': time.time(),
    }


def _synchronize_active_market_data_request(request_id: str):
    safe_request_id = str(request_id or '').strip()
    if not safe_request_id:
        return {}

    payload = state.market_data.requests_by_id.get(safe_request_id) or {}
    if not payload:
        return {}

    active_request_id = str(state.bridge.active_request_id or '').strip()
    if safe_request_id != active_request_id:
        return payload

    status = str(payload.get('status') or '').strip().lower()
    if status not in {'queued', 'waiting', 'loading'}:
        return payload

    if state.bridge.history_loading:
        refresh_history_timeout(get_history_timeout_for_request(payload.get('bars')))
    elif state.bridge.history_error:
        fail_history_state(str(state.bridge.history_error or '').strip() or 'History load failed.')

    return state.market_data.requests_by_id.get(safe_request_id) or payload


def build_market_request_payload(request_id: str):
    payload = _synchronize_active_market_data_request(request_id)
    if not payload:
        payload = state.market_data.requests_by_id.get(str(request_id or '').strip()) or {}
    return dict(payload)


def _market_request_payload_key(payload: dict | None):
    safe_payload = payload or {}
    symbol = str(safe_payload.get('symbol') or '').strip().upper()
    timeframe = str(safe_payload.get('timeframe') or '').strip().upper()
    bars = max(1, int(safe_payload.get('bars') or 1))
    if not symbol or not timeframe:
        return ''
    return build_market_cache_key(symbol, timeframe, bars)


def invalidate_stale_active_market_data_request(
    *,
    expected_symbol: str | None = None,
    expected_timeframe: str | None = None,
    expected_bars: int | None = None,
    reason: str = 'stale_market_request',
):
    bridge_state = state.bridge
    market_data_state = state.market_data
    active_request_id = str(bridge_state.active_request_id or '').strip()
    if not active_request_id:
        return None

    request_payload = market_data_state.requests_by_id.get(active_request_id)
    cancellation_reasons = []

    if not request_payload:
        cancellation_reasons.append('active request payload is missing')
    else:
        request_status = str(request_payload.get('status') or '').strip().lower()
        if request_status not in {'queued', 'waiting', 'loading'}:
            cancellation_reasons.append(f'invalid active status {request_status or "unknown"}')

        declared_cache_key = str(request_payload.get('cache_key') or '').strip().upper()
        calculated_cache_key = _market_request_payload_key(request_payload)
        if not calculated_cache_key:
            cancellation_reasons.append('request payload is missing market identity')
        elif declared_cache_key and declared_cache_key != calculated_cache_key:
            cancellation_reasons.append(
                f'cache key mismatch ({declared_cache_key} != {calculated_cache_key})'
            )

        safe_expected_symbol = str(expected_symbol or '').strip().upper()
        safe_expected_timeframe = str(expected_timeframe or '').strip().upper()
        if safe_expected_symbol and safe_expected_timeframe:
            expected_cache_key = build_market_cache_key(
                safe_expected_symbol,
                safe_expected_timeframe,
                max(1, int(expected_bars or request_payload.get('bars') or 1)),
            )
            if calculated_cache_key and calculated_cache_key != expected_cache_key:
                cancellation_reasons.append(
                    f'request market {calculated_cache_key} != expected {expected_cache_key}'
                )

    if not cancellation_reasons:
        return None

    now = time.time()
    if request_payload:
        request_payload['status'] = 'cancelled'
        request_payload['finished_at'] = now
        request_payload['result_ready'] = False
        request_payload['error'] = f'Cancelled stale request: {"; ".join(cancellation_reasons)}'

    market_data_state.pending_queue = [
        request_id
        for request_id in list(market_data_state.pending_queue or [])
        if str(request_id or '').strip() != active_request_id
    ]
    market_data_state.revision += 1
    bridge_state.active_request_id = None
    bridge_state.ea_timeout_seconds = get_ea_timeout_for_request(1000)
    _remember_bridge_event(
        reason,
        f'Discarded stale active market-data request {active_request_id}: {"; ".join(cancellation_reasons)}',
        level='warn',
        request_id=active_request_id,
    )
    return {
        'request_id': active_request_id,
        'reason': '; '.join(cancellation_reasons),
    }


def build_mt5_job_response_text(request_payload: dict | None):
    if not request_payload:
        return ''

    request_id = str(request_payload.get('request_id') or '').strip()
    symbol = str(request_payload.get('symbol') or '').strip().upper()
    timeframe = str(request_payload.get('timeframe') or '').strip().upper()
    bars = max(1, int(request_payload.get('bars') or 1))

    if not request_id or not symbol or not timeframe:
        return ''

    return f'{request_id};{symbol};{timeframe};{bars}'


def build_mt5_trade_command_response_text(command_payload: dict | None):
    if not command_payload:
        return ''

    command_id = str(command_payload.get('id') or '').strip()
    symbol = str(command_payload.get('symbol') or '').strip().upper()
    action = str(command_payload.get('action') or '').strip().lower()
    side = str(command_payload.get('side') or '').strip().lower()
    volume = float(command_payload.get('volume') or 0.0)
    sleeve_id = str(command_payload.get('sleeve_id') or '').strip()
    broker_ticket = str(command_payload.get('broker_ticket') or '').strip()
    take_profit_price = command_payload.get('take_profit_price')
    stop_loss_price = command_payload.get('stop_loss_price')

    if not command_id or not symbol or not action or not side or volume <= 0.0:
        return ''

    tp_text = '' if take_profit_price in (None, '') else f'{float(take_profit_price):.5f}'
    sl_text = '' if stop_loss_price in (None, '') else f'{float(stop_loss_price):.5f}'
    return f'{command_id};{symbol};{action};{side};{volume:.2f};{sleeve_id};{broker_ticket};{tp_text};{sl_text}'


def activate_next_market_data_request():
    market_data_state = state.market_data
    bridge_state = state.bridge

    while market_data_state.pending_queue:
        next_request_id = market_data_state.pending_queue.pop(0)
        request_payload = market_data_state.requests_by_id.get(next_request_id)
        if not request_payload:
            continue
        if request_payload.get('status') not in {'queued', 'waiting'}:
            continue

        bridge_state.active_request_id = next_request_id
        bridge_state.request['symbol'] = request_payload['symbol']
        bridge_state.request['timeframe'] = request_payload['timeframe']
        bridge_state.request['bars'] = request_payload['bars']
        bridge_state.ea_timeout_seconds = get_ea_timeout_for_request(request_payload['bars'])

        request_payload['status'] = 'loading'
        request_payload['started_at'] = time.time()
        request_payload['bridge_request'] = dict(bridge_state.request)
        request_payload['result_ready'] = False

        existing_history_meta = dict(bridge_state.history_meta or {})
        existing_symbol = str(existing_history_meta.get('symbol') or '').strip().upper()
        existing_timeframe = str(existing_history_meta.get('timeframe') or '').strip().upper()
        existing_requested_bars = max(
            1,
            int(
                existing_history_meta.get('requested_bars')
                or len(bridge_state.candles or [])
                or 1
            ),
        )
        if (
            bridge_state.history_ready
            and existing_symbol
            and existing_timeframe
            and len(bridge_state.candles or []) > 0
        ):
            update_market_data_cache_from_bridge(
                request_id=None,
                symbol=existing_symbol,
                timeframe=existing_timeframe,
                bars=existing_requested_bars,
            )

        reset_history_state(reason='market_data_request_activated')
        return request_payload

    bridge_state.active_request_id = None
    bridge_state.ea_timeout_seconds = get_ea_timeout_for_request(1000)
    return None


def complete_active_market_data_request(request_id: str | None):
    bridge_state = state.bridge
    active_request_id = str(bridge_state.active_request_id or '').strip()
    completed_request_id = str(request_id or '').strip()

    if not active_request_id or completed_request_id != active_request_id:
        return None

    bridge_state.active_request_id = None
    next_request_payload = activate_next_market_data_request()
    if not next_request_payload:
        bridge_state.ea_timeout_seconds = get_ea_timeout_for_request(1000)
    return next_request_payload


def sync_market_data_request(symbol: str, timeframe: str, bars: int, source: str = 'api'):
    market_data_state = state.market_data
    invalidate_stale_active_market_data_request(
        expected_symbol=symbol,
        expected_timeframe=timeframe,
        expected_bars=bars,
        reason='market_request_superseded',
    )
    cache_key = build_market_cache_key(symbol, timeframe, bars)
    existing_request_id = None
    cache_payload = market_data_state.cache_by_key.get(cache_key) or {}
    cache_snapshot = cache_payload.get('snapshot') or {}
    has_ready_snapshot = _cache_snapshot_ready_for_request(cache_snapshot, bars)

    for request_id in reversed(market_data_state.request_order):
        payload = market_data_state.requests_by_id.get(request_id)
        if not payload:
            continue
        status = str(payload.get('status') or '').strip()
        if payload.get('cache_key') != cache_key:
            continue
        if status in {'queued', 'waiting', 'loading'}:
            existing_request_id = request_id
            break
        if status == 'completed' and has_ready_snapshot:
            existing_request_id = request_id
            break

    if existing_request_id:
        return build_market_request_payload(existing_request_id)

    request_id = f'mreq_{uuid.uuid4().hex}'
    created_at = time.time()
    request_payload = {
        'request_id': request_id,
        'cache_key': cache_key,
        'symbol': str(symbol or '').strip().upper(),
        'timeframe': str(timeframe or '').strip().upper(),
        'bars': max(1, int(bars or 1)),
        'status': 'queued',
        'source': source,
        'created_at': created_at,
        'started_at': None,
        'finished_at': None,
        'error': None,
        'result_ready': False,
    }
    market_data_state.requests_by_id[request_id] = request_payload
    market_data_state.request_order.append(request_id)
    market_data_state.pending_queue.append(request_id)
    market_data_state.last_request_id = request_id
    market_data_state.revision += 1

    if state.bridge.active_request_id is None:
        activate_next_market_data_request()

    return dict(request_payload)


def update_market_data_cache_from_bridge(request_id: str | None, symbol: str, timeframe: str, bars: int):
    market_data_state = state.market_data
    cache_key = build_market_cache_key(symbol, timeframe, bars)
    snapshot = build_market_snapshot_payload(request_id=request_id)
    requested_bars = max(1, int(bars or 1))
    loaded_bars = min(max(0, int(snapshot.get('bars_loaded') or 0)), len(snapshot.get('candles') or []))
    is_ready_snapshot = loaded_bars >= requested_bars
    cache_error = None if is_ready_snapshot else (
        f'Bridge cached only {loaded_bars:,} of {requested_bars:,} requested candles '
        f'for {str(symbol or "").strip().upper()} {str(timeframe or "").strip().upper()}.'
    )
    cache_payload = {
        'cache_key': cache_key,
        'symbol': symbol,
        'timeframe': timeframe,
        'bars': requested_bars,
        'status': 'ready' if is_ready_snapshot else 'partial',
        'ready': is_ready_snapshot,
        'loading': False,
        'error': cache_error,
        'revision': state.bridge.revision,
        'first_time': snapshot['first_time'],
        'last_time': snapshot['last_time'],
        'bars_loaded': snapshot['bars_loaded'],
        'last_refresh_at': time.time(),
        'snapshot': snapshot,
    }
    market_data_state.cache_by_key[cache_key] = cache_payload
    market_data_state.last_cache_key = cache_key
    market_data_state.revision += 1

    if request_id:
        request_payload = market_data_state.requests_by_id.get(request_id)
        if request_payload:
            request_payload['status'] = 'completed'
            request_payload['finished_at'] = time.time()
            request_payload['error'] = cache_error
            request_payload['result_ready'] = is_ready_snapshot
            request_payload['snapshot_summary'] = {
                'bars_loaded': snapshot['bars_loaded'],
                'first_time': snapshot['first_time'],
                'last_time': snapshot['last_time'],
            }


def invalidate_strategy_runtime_if_available(reason: str, preserve_runtime: bool = False):
    try:
        try:
            from . import strategy_backend
        except ImportError:
            import strategy_backend
        strategy_backend.invalidate_strategy_runtime(reason, preserve_runtime=preserve_runtime)
    except Exception:
        pass


def build_changed_market_features():
    changed_features = ['open', 'high', 'low', 'close', 'volume']

    for detail in state.chart.snapshot_available_column_details:
        normalized_column_name = normalize_indicator_feature_name(
            detail.get('normalized_column_name') or detail.get('column_name') or ''
        )

        if normalized_column_name and normalized_column_name not in changed_features:
            changed_features.append(normalized_column_name)

    return changed_features


def get_strategy_overlap_for_market_update(changed_features: list[str]):
    try:
        try:
            from . import strategy_backend
        except ImportError:
            import strategy_backend
        return strategy_backend.get_strategy_feature_overlap(changed_features)
    except Exception:
        return []


def invalidate_chart_snapshot_if_available(reason: str):
    try:
        try:
            from .runtime.chart_runtime import invalidate_chart_snapshot
        except ImportError:
            from runtime.chart_runtime import invalidate_chart_snapshot
        invalidate_chart_snapshot(
            reason,
            preserve_existing=(reason == 'history_updated'),
        )
    except Exception:
        pass


def reset_history_state(reason: str = 'manual_reset'):
    bridge_state = state.bridge

    bridge_state.candles = []
    bridge_state.history_chunk_sessions = {}
    bridge_state.history_ready = False
    bridge_state.history_loading = True
    bridge_state.history_error = None
    bridge_state.history_request_started_at = time.time()
    bridge_state.revision += 1

    bridge_state.history_meta = {
        'symbol': None,
        'timeframe': None,
        'requested_bars': None,
        'loaded_candles': 0,
        'first_time': None,
        'last_time': None,
        'last_reset_reason': reason,
    }

    reset_market_runtime(reason)
    invalidate_chart_snapshot_if_available(f'history_reset:{reason}')
    invalidate_strategy_runtime_if_available(f'history_reset:{reason}')


def fail_history_state(message: str):
    bridge_state = state.bridge
    market_data_state = state.market_data
    safe_message = str(message or '').strip() or 'History load failed.'
    active_request_id = str(bridge_state.active_request_id or '').strip()

    bridge_state.history_ready = False
    bridge_state.history_loading = False
    bridge_state.history_error = safe_message
    bridge_state.history_request_started_at = None

    if safe_message:
        bridge_state.ea_last_error = safe_message
        bridge_state.ea_last_error_at = time.time()
        market_data_state.last_error = safe_message

    if not active_request_id:
        return

    request_payload = market_data_state.requests_by_id.get(active_request_id)
    if request_payload:
        request_payload['status'] = 'error'
        request_payload['finished_at'] = time.time()
        request_payload['result_ready'] = False
        request_payload['error'] = safe_message
        request_payload['snapshot_summary'] = {
            'bars_loaded': len(bridge_state.candles or []),
            'first_time': bridge_state.history_meta.get('first_time'),
            'last_time': bridge_state.history_meta.get('last_time'),
        }
        market_data_state.revision += 1

    _remember_bridge_event(
        'market_data_request_failed',
        safe_message,
        level='error',
        request_id=active_request_id,
    )
    complete_active_market_data_request(active_request_id)


def update_history_meta():
    bridge_state = state.bridge
    candles = bridge_state.candles

    bridge_state.history_meta['loaded_candles'] = len(candles)
    bridge_state.history_meta['first_time'] = candles[0]['time'] if candles else None
    bridge_state.history_meta['last_time'] = candles[-1]['time'] if candles else None


def refresh_history_timeout(max_seconds: float | None = None):
    bridge_state = state.bridge

    timeout = (
        max_seconds
        if max_seconds is not None
        else bridge_state.history_timeout_seconds
    )

    if not bridge_state.history_loading:
        return

    if bridge_state.history_request_started_at is None:
        return

    elapsed = time.time() - bridge_state.history_request_started_at
    if elapsed > timeout:
        fail_history_state(f'History load timeout after {timeout:.1f}s')


def get_history_timeout_for_request(bars: int | None = None):
    safe_bars = max(1, int(bars or state.bridge.request.get('bars') or 1000))
    base_timeout = max(15.0, float(state.bridge.history_timeout_seconds or 15.0))

    if safe_bars <= 2_000:
        return base_timeout
    if safe_bars <= 10_000:
        return max(base_timeout, 30.0)
    if safe_bars <= 25_000:
        return max(base_timeout, 60.0)
    if safe_bars <= 50_000:
        return max(base_timeout, 120.0)
    return max(base_timeout, 180.0)


def get_ea_timeout_for_request(bars: int | None = None):
    safe_bars = max(1, int(bars or state.bridge.request.get('bars') or 1000))
    base_timeout = 8.0

    if safe_bars <= 2_000:
        return base_timeout
    if safe_bars <= 10_000:
        return max(base_timeout, 120.0)
    if safe_bars <= 25_000:
        return max(base_timeout, 180.0)
    if safe_bars <= 50_000:
        return max(base_timeout, 240.0)
    return max(base_timeout, 300.0)


def resolve_history_candle_struct(total_candles: int, payload_bytes: bytes):
    if total_candles == 0:
        return DUAL_VOLUME_CANDLE_STRUCT, DUAL_VOLUME_CANDLE_STRUCT.size
    if len(payload_bytes) == total_candles * DUAL_VOLUME_CANDLE_STRUCT.size:
        return DUAL_VOLUME_CANDLE_STRUCT, DUAL_VOLUME_CANDLE_STRUCT.size
    if len(payload_bytes) == total_candles * VOLUME_CANDLE_STRUCT.size:
        return VOLUME_CANDLE_STRUCT, VOLUME_CANDLE_STRUCT.size
    if len(payload_bytes) == total_candles * LEGACY_CANDLE_STRUCT.size:
        return LEGACY_CANDLE_STRUCT, LEGACY_CANDLE_STRUCT.size
    return None, None


def parse_history_candles(payload_bytes: bytes, total_candles: int):
    candle_struct, candle_size = resolve_history_candle_struct(total_candles, payload_bytes)
    if candle_struct is None or candle_size is None:
        raise ValueError(
            'Invalid payload size: '
            f'payload_len={len(payload_bytes)} '
            f'legacy_candle_size={LEGACY_CANDLE_STRUCT.size} '
            f'volume_candle_size={VOLUME_CANDLE_STRUCT.size} '
            f'dual_volume_candle_size={DUAL_VOLUME_CANDLE_STRUCT.size}'
        )

    parsed_count = len(payload_bytes) // candle_size if candle_size else 0
    if parsed_count != total_candles:
        raise ValueError(f'Invalid candle count: expected={total_candles} received={parsed_count}')

    parsed_candles = []
    for offset in range(0, len(payload_bytes), candle_size):
        if candle_struct is DUAL_VOLUME_CANDLE_STRUCT:
            t, o, h, l, c, v, tv, rv = candle_struct.unpack_from(payload_bytes, offset)
            parsed_candles.append({
                'time': int(t),
                'open': float(o),
                'high': float(h),
                'low': float(l),
                'close': float(c),
                'volume': float(v),
                'tick_volume': float(tv),
                'real_volume': float(rv),
            })
        elif candle_struct is VOLUME_CANDLE_STRUCT:
            t, o, h, l, c, v = candle_struct.unpack_from(payload_bytes, offset)
            parsed_candles.append({
                'time': int(t),
                'open': float(o),
                'high': float(h),
                'low': float(l),
                'close': float(c),
                'volume': float(v),
                'tick_volume': float(v),
                'real_volume': 0.0,
            })
        else:
            t, o, h, l, c = candle_struct.unpack_from(payload_bytes, offset)
            parsed_candles.append({
                'time': int(t),
                'open': float(o),
                'high': float(h),
                'low': float(l),
                'close': float(c),
                'volume': 0.0,
                'tick_volume': 0.0,
                'real_volume': 0.0,
            })

    parsed_candles.sort(key=lambda candle: candle['time'])
    return parsed_candles


def finalize_history_load(symbol: str, timeframe: str, candles: list[dict], request_id: str | None, requested_total_candles: int | None = None):
    bridge_state = state.bridge
    bridge_state.candles = list(candles or [])
    bridge_state.history_ready = True
    bridge_state.history_loading = False
    bridge_state.history_error = None
    bridge_state.history_request_started_at = None
    bridge_state.revision += 1

    effective_request_id = str(request_id or bridge_state.active_request_id or '').strip() or None
    active_request_payload = (
        state.market_data.requests_by_id.get(effective_request_id)
        if effective_request_id
        else None
    ) or {}
    requested_bars = max(
        1,
        int(
            active_request_payload.get('bars')
            or bridge_state.request.get('bars')
            or requested_total_candles
            or len(bridge_state.candles)
            or 1
        ),
    )

    bridge_state.history_meta = {
        'symbol': symbol,
        'timeframe': timeframe,
        'requested_bars': requested_bars,
        'loaded_candles': len(bridge_state.candles),
        'first_time': bridge_state.candles[0]['time'] if bridge_state.candles else None,
        'last_time': bridge_state.candles[-1]['time'] if bridge_state.candles else None,
        'last_reset_reason': bridge_state.history_meta.get('last_reset_reason'),
    }
    update_market_data_cache_from_bridge(
        request_id=effective_request_id,
        symbol=symbol,
        timeframe=timeframe,
        bars=requested_bars,
    )

    mark_history_loaded(bridge_state.candles)
    try:
        post_trade_internal('/internal/trade/market-update', {
            'stage': 'history_loaded',
            'symbol': symbol,
            'timeframe': timeframe,
            'candle_count': len(bridge_state.candles),
            'latest_candle_time': bridge_state.history_meta.get('last_time'),
            'candles': _slice_trade_market_update_candles(bridge_state.candles),
        })
    except Exception:
        pass
    invalidate_chart_snapshot_if_available('history_loaded')
    invalidate_strategy_runtime_if_available('history_loaded')
    maintenance_result = run_runtime_maintenance('history_loaded')
    try:
        try:
            from .market_backend import broadcast_market_event
        except ImportError:
            from market_backend import broadcast_market_event
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_market_event(
            'market.history_loaded',
            source='bridge_history',
            include_chart_delta=True,
        ))
    except Exception:
        pass

    next_request_payload = complete_active_market_data_request(effective_request_id)
    state.bridge.history_chunk_sessions.pop(str(effective_request_id or '').strip(), None)

    return {
        'status': 'ok',
        'request_id': effective_request_id,
        'next_request_id': (next_request_payload or {}).get('request_id'),
        'symbol': symbol,
        'timeframe': timeframe,
        'total_candles': len(bridge_state.candles),
        'maintenance': maintenance_result,
        **build_history_state_payload(),
    }


def normalize_candle(candle: dict):
    tick_volume = float(candle.get('tick_volume', 0.0) or 0.0)
    real_volume = float(candle.get('real_volume', 0.0) or 0.0)
    effective_volume = candle.get('volume', None)

    if effective_volume is None:
        effective_volume = real_volume if real_volume > 0.0 else tick_volume

    return {
        'time': int(candle['time']),
        'open': float(candle['open']),
        'high': float(candle['high']),
        'low': float(candle['low']),
        'close': float(candle['close']),
        'volume': float(effective_volume or 0.0),
        'tick_volume': tick_volume,
        'real_volume': real_volume,
    }


def merge_candle(candle: dict):
    bridge_state = state.bridge
    candles = bridge_state.candles

    candle = normalize_candle(candle)

    if not candles:
        candles.append(candle)
        update_history_meta()
        return 'appended_first', 0, []

    last_time = candles[-1]['time']

    if candle['time'] == last_time:
        if candles[-1] == candle:
            return 'unchanged_last', None, []
        candles[-1] = candle
        update_history_meta()
        return 'replaced_last', len(candles) - 1, [candle['time']]

    if candle['time'] > last_time:
        candles.append(candle)
        update_history_meta()
        return 'appended_new', len(candles) - 1, []

    if len(candles) >= 2 and candle['time'] == candles[-2]['time']:
        if candles[-2] == candle:
            return 'unchanged_previous', None, []
        candles[-2] = candle
        update_history_meta()
        return 'replaced_previous', len(candles) - 2, [candle['time']]

    for index in range(len(candles) - 3, -1, -1):
        if candles[index]['time'] == candle['time']:
            if candles[index] == candle:
                return 'unchanged_older', None, []
            candles[index] = candle
            update_history_meta()
            return 'replaced_older', index, [candle['time']]

    return 'ignored_out_of_order', None, []


def parse_update_text(text: str):
    if not text:
        return []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parsed_candles = []

    for line in lines:
        parts = line.split('|')
        if len(parts) not in (6, 7, 9) or parts[0] != 'U':
            return None

        try:
            candle = {
                'time': int(parts[1]),
                'open': float(parts[2]),
                'high': float(parts[3]),
                'low': float(parts[4]),
                'close': float(parts[5]),
            }
            if len(parts) >= 7:
                candle['volume'] = float(parts[6])
            if len(parts) >= 9:
                candle['tick_volume'] = float(parts[7])
                candle['real_volume'] = float(parts[8])
            parsed_candles.append(candle)
        except (TypeError, ValueError):
            return None

    return parsed_candles


def build_history_state_payload():
    bridge_state = state.bridge
    market_data_state = state.market_data
    refresh_history_timeout(get_history_timeout_for_request())

    return {
        'ready': bridge_state.history_ready,
        'loading': bridge_state.history_loading,
        'error': bridge_state.history_error,
        'candles_loaded': len(bridge_state.candles),
        'first_time': bridge_state.history_meta['first_time'],
        'last_time': bridge_state.history_meta['last_time'],
        'symbol': bridge_state.history_meta['symbol'],
        'timeframe': bridge_state.history_meta['timeframe'],
        'requested_bars': bridge_state.history_meta['requested_bars'],
        'last_reset_reason': bridge_state.history_meta['last_reset_reason'],
        'active_request_id': bridge_state.active_request_id,
        'last_affected_index': bridge_state.last_affected_index,
        'last_update_replaced_times': list(bridge_state.last_update_replaced_times),
        'agent': build_bridge_agent_payload(),
        'market_data': {
            'revision': market_data_state.revision,
            'last_request_id': market_data_state.last_request_id,
            'last_cache_key': market_data_state.last_cache_key,
            'queued_requests': len(market_data_state.pending_queue),
            'known_requests': len(market_data_state.requests_by_id),
            'cache_entries': len(market_data_state.cache_by_key),
            'last_error': market_data_state.last_error,
        },
        'market_runtime': build_market_runtime_payload(),
        'runtime_service': build_runtime_service_payload(),
    }


def build_service_health_payload():
    bridge_payload = build_history_state_payload()
    runtime_service_payload = build_runtime_service_payload()
    local_trade_runtime_payload = build_trade_runtime_payload()
    trade_service_health = get_trade_service_health(fallback_local=True)
    trade_runtime_via_service = get_trade_runtime_via_service(fallback_local=True)
    trade_runtime_payload = dict(
        trade_runtime_via_service.get('trade_runtime')
        or trade_service_health.get('trade_runtime')
        or local_trade_runtime_payload
    )
    trade_service_payload = dict(trade_service_health.get('service') or {})
    runtime_service_hint = dict(trade_runtime_via_service.get('trade_service') or {})
    if runtime_service_hint:
        trade_service_payload = {
            **trade_service_payload,
            **runtime_service_hint,
        }
    elif not trade_service_payload:
        trade_service_payload = {'reachable': False}
    neural_runtime_payload = {
        'active_jobs': dict(getattr(state.neural, 'active_jobs', {}) or {}),
        'last_run_at': getattr(state.neural, 'last_run_at', None),
        'last_error': getattr(state.neural, 'last_error', None),
    }
    chart_ready = state.chart.snapshot_error is None
    workspace_ready = state.workspace.last_error is None
    strategy_ready = True
    local_runtime_state_is_live = any((
        local_trade_runtime_payload.get('armed'),
        local_trade_runtime_payload.get('live_dispatch_armed'),
        list(local_trade_runtime_payload.get('sleeves') or []),
    ))
    runtime_state_is_live = any((
        trade_runtime_payload.get('armed'),
        trade_runtime_payload.get('live_dispatch_armed'),
        list(trade_runtime_payload.get('sleeves') or []),
        local_runtime_state_is_live,
    ))
    readiness_trade_runtime_payload = trade_runtime_payload
    if not bool(trade_service_payload.get('reachable', False)) and local_runtime_state_is_live:
        readiness_trade_runtime_payload = local_trade_runtime_payload
    trade_ready = not bool(readiness_trade_runtime_payload.get('last_error')) and (
        not readiness_trade_runtime_payload.get('armed')
        or bool(bridge_payload['agent']['online'])
    )
    if (
        not bool(trade_service_payload.get('reachable', False))
        and runtime_state_is_live
        and readiness_trade_runtime_payload is trade_runtime_payload
    ):
        trade_ready = False
    if str((readiness_trade_runtime_payload.get('market_feed') or {}).get('status') or '').lower() == 'stale':
        trade_ready = False
    neural_ready = neural_runtime_payload['last_error'] is None
    chart_runtime_warmed = (
        not bridge_payload['ready']
        or state.chart.snapshot_built_at is not None
    )
    strategy_runtime_ready = (
        state.strategy.request is None
        or state.strategy.last_applied_at is not None
        or runtime_service_payload['last_strategy_refresh_at'] is not None
    )
    runtime_ready = chart_runtime_warmed and strategy_runtime_ready
    service_ready = chart_ready and workspace_ready and strategy_ready and trade_ready and runtime_ready and neural_ready

    return {
        'status': 'ok' if service_ready else 'degraded',
        'service': {
            'process_id': os.getpid(),
            'started_at': SERVICE_STARTED_AT,
            'uptime_seconds': round(max(0.0, time.time() - SERVICE_STARTED_AT), 3),
        },
        'trade_service': trade_service_payload,
        'runtime_service': runtime_service_payload,
        'trade_runtime': trade_runtime_payload,
        'neural_runtime': neural_runtime_payload,
        'checks': {
            'bridge': {
                'ok': bridge_payload['error'] is None,
                'history_ready': bridge_payload['ready'],
                'history_loading': bridge_payload['loading'],
                'error': bridge_payload['error'],
                'revision': state.bridge.revision,
                'ea_online': bridge_payload['agent']['online'],
                'ea_stale': bridge_payload['agent']['stale'],
                'ea_last_status': bridge_payload['agent']['last_status'],
                'ea_last_error': bridge_payload['agent']['last_error'],
                'ea_last_heartbeat_at': bridge_payload['agent']['last_heartbeat_at'],
            },
            'chart': {
                'ok': chart_ready,
                'snapshot_error': state.chart.snapshot_error,
                'snapshot_dirty_reason': state.chart.snapshot_dirty_reason,
                'snapshot_built_at': state.chart.snapshot_built_at,
                'runtime_warmed': chart_runtime_warmed,
            },
            'strategy': {
                'ok': strategy_ready,
                'is_stale': state.strategy.is_stale,
                'last_invalidated_reason': state.strategy.last_invalidated_reason,
                'last_refresh_mode': state.strategy.last_refresh_mode,
                'last_refresh_from_index': state.strategy.last_refresh_from_index,
                'runtime_ready': strategy_runtime_ready,
            },
            'trade': {
                'ok': trade_ready,
                'status': trade_runtime_payload['status'],
                'mode': trade_runtime_payload['mode'],
                'armed': trade_runtime_payload['armed'],
                'live': trade_runtime_payload['live'],
                'sleeve_count': len(list(trade_runtime_payload['sleeves'] or [])),
                'active_symbols': list(trade_runtime_payload['active_symbols'] or []),
                'market_feed_status': (trade_runtime_payload.get('market_feed') or {}).get('status'),
                'last_error': trade_runtime_payload['last_error'],
            },
            'workspace': {
                'ok': workspace_ready,
                'revision': state.workspace.revision,
                'last_saved_at': state.workspace.last_saved_at,
                'last_error': state.workspace.last_error,
            },
            'runtime': {
                'ok': runtime_ready,
                'last_trigger': runtime_service_payload['last_trigger'],
                'last_run_at': runtime_service_payload['last_run_at'],
                'last_chart_warm_at': runtime_service_payload['last_chart_warm_at'],
                'last_strategy_refresh_at': runtime_service_payload['last_strategy_refresh_at'],
                'last_error': runtime_service_payload['last_error'],
            },
            'neural': {
                'ok': neural_ready,
                'active_jobs': list((neural_runtime_payload['active_jobs'] or {}).keys()),
                'last_run_at': neural_runtime_payload['last_run_at'],
                'last_error': neural_runtime_payload['last_error'],
            },
        },
    }


@app.get('/bridge/request', response_class=PlainTextResponse)
def get_bridge_request_text():
    bridge_state = state.bridge
    request_data = bridge_state.request
    return f'{request_data["symbol"]};{request_data["timeframe"]};{request_data["bars"]}'


@app.get('/mt5/jobs/next', response_class=PlainTextResponse)
@app.post('/mt5/jobs/next', response_class=PlainTextResponse)
def get_mt5_next_job():
    bridge_request = dict(state.bridge.request or {})
    bridge_symbol = str(bridge_request.get('symbol') or '').strip().upper()
    bridge_timeframe = str(bridge_request.get('timeframe') or '').strip().upper()
    bridge_bars = max(1, int(bridge_request.get('bars') or 1))
    invalidate_stale_active_market_data_request(
        expected_symbol=bridge_symbol or None,
        expected_timeframe=bridge_timeframe or None,
        expected_bars=bridge_bars,
        reason='mt5_poll_request_reconciled',
    )

    active_request_id = state.bridge.active_request_id
    if active_request_id:
        return build_mt5_job_response_text(build_market_request_payload(active_request_id))

    market_data_state = state.market_data
    if market_data_state.pending_queue:
        request_payload = activate_next_market_data_request()
        return build_mt5_job_response_text(request_payload)

    if bridge_symbol and bridge_timeframe:
        request_payload = sync_market_data_request(
            symbol=bridge_symbol,
            timeframe=bridge_timeframe,
            bars=bridge_bars,
            source='mt5_keepalive',
        )
        return build_mt5_job_response_text(request_payload)

    return ''


@app.get('/mt5/trade/commands/next', response_class=PlainTextResponse)
@app.post('/mt5/trade/commands/next', response_class=PlainTextResponse)
def get_mt5_next_trade_command():
    bridge_state = state.bridge
    bridge_state.trade_command_poll_count = int(bridge_state.trade_command_poll_count or 0) + 1
    bridge_state.trade_command_last_polled_at = time.time()
    try:
        payload = post_trade_internal(
            '/internal/mt5/trade/commands/next',
            {'session_id': state.bridge.ea_session_id or ''},
            timeout=5.0,
        )
    except Exception as error:
        safe_error = _trim_bridge_text(error) or 'Trade command polling failed.'
        bridge_state.ea_last_error = safe_error
        bridge_state.ea_last_error_at = time.time()
        _remember_bridge_event(
            'trade_command_poll_error',
            safe_error,
            level='error',
        )
        return ''
    command = payload.get('command')
    if command:
        bridge_state.trade_command_last_command_id = str(command.get('id') or '').strip() or None
        bridge_state.trade_command_last_command_at = time.time()
        _remember_bridge_event(
            'trade_command_next',
            'Trade command delivered to EA poll.',
            level='info',
            command_id=bridge_state.trade_command_last_command_id,
            action=str(command.get('action') or '').strip().lower() or None,
            side=str(command.get('side') or '').strip().lower() or None,
        )
    response_text = build_mt5_trade_command_response_text(command)
    if command and not response_text:
        bridge_state.ea_last_error = 'Trade command could not be serialized for EA delivery.'
        bridge_state.ea_last_error_at = time.time()
        _remember_bridge_event(
            'trade_command_delivery_error',
            'Trade command was claimed internally but could not be serialized for EA delivery.',
            level='error',
            command_id=bridge_state.trade_command_last_command_id,
        )
    return response_text


@app.post('/bridge/set-request')
async def set_bridge_request(payload: dict):
    bridge_state = state.bridge

    bridge_state.request['symbol'] = str(
        payload.get('symbol', bridge_state.request['symbol'])
    ).strip().upper()

    bridge_state.request['timeframe'] = str(
        payload.get('timeframe', bridge_state.request['timeframe'])
    ).strip().upper()

    bridge_state.request['bars'] = max(
        1,
        int(payload.get('bars', bridge_state.request['bars']))
    )

    request_payload = sync_market_data_request(
        symbol=bridge_state.request['symbol'],
        timeframe=bridge_state.request['timeframe'],
        bars=bridge_state.request['bars'],
        source='bridge_set_request',
    )
    try:
        try:
            from .market_backend import broadcast_market_event
        except ImportError:
            from market_backend import broadcast_market_event
        await broadcast_market_event(
            'market.request_changed',
            source='bridge_set_request',
            include_chart_delta=True,
        )
    except Exception:
        pass

    print('REQUEST UPDATED:', bridge_state.request)

    return {
        'status': 'ok',
        'request': dict(bridge_state.request),
        'request_id': request_payload.get('request_id'),
        **build_history_state_payload(),
    }


@app.post('/market-data/request')
async def create_market_data_request(payload: dict, request: Request):
    require_request_auth(request)
    request_payload = sync_market_data_request(
        symbol=payload.get('symbol'),
        timeframe=payload.get('timeframe'),
        bars=payload.get('bars'),
        source='market_data_api',
    )
    return {
        'status': request_payload.get('status') or 'queued',
        'request_id': request_payload.get('request_id'),
        'cache_key': request_payload.get('cache_key'),
        'request': request_payload,
    }


@app.get('/internal/market/snapshot')
def get_internal_market_snapshot(symbol: str, timeframe: str, bars: int, request: Request):
    _require_trade_internal(request)
    context = ensure_market_data(symbol, timeframe, bars, source='trade_internal')
    return {
        'status': 'ok',
        'ready': bool(context.get('ready')),
        'request_status': context.get('request_status'),
        'error': context.get('error'),
        'bars_loaded': context.get('bars_loaded'),
        'candles': list(context.get('candles') or []),
        'last_update_at': getattr(state.market, 'last_update_at', None),
        'latest_candle_time': getattr(state.market, 'latest_candle_time', None),
    }


@app.post('/mt5/heartbeat')
async def receive_mt5_heartbeat(request: Request):
    payload = _parse_bridge_plain_payload(await request.body())
    note_bridge_heartbeat(payload)
    symbol_rules = _parse_bridge_symbol_rules(payload)
    try:
        post_trade_internal('/internal/trade/bridge-heartbeat', {
            **payload,
            'online': build_bridge_agent_payload().get('online'),
            'timeout_seconds': state.bridge.ea_timeout_seconds,
            'positions': _parse_bridge_positions(payload.get('positions')),
            'symbol_rules': symbol_rules,
        })
    except Exception:
        pass
    return {
        'status': 'ok',
        'agent': build_bridge_agent_payload(),
    }


@app.post('/mt5/events')
async def receive_mt5_event(request: Request):
    payload = _parse_bridge_plain_payload(await request.body())
    kind = _trim_bridge_text(payload.get('kind') or 'event').lower() or 'event'
    message = _trim_bridge_text(payload.get('message'))
    if _trim_bridge_text(payload.get('session_id')):
        state.bridge.ea_session_id = _trim_bridge_text(payload.get('session_id'))
    if _trim_bridge_text(payload.get('status')):
        state.bridge.ea_last_status = _trim_bridge_text(payload.get('status')).lower()
    if _trim_bridge_text(payload.get('request_id')):
        state.bridge.ea_last_request_id = _trim_bridge_text(payload.get('request_id'))
    _remember_bridge_event(kind, message, level=_trim_bridge_text(payload.get('level') or 'info') or 'info')
    try:
        post_trade_internal('/internal/trade/bridge-event', {
            **payload,
            'kind': kind,
        })
    except Exception:
        pass
    if _trim_bridge_text(payload.get('level')).lower() == 'error' or kind in {'error', 'request_error', 'data_error', 'deinit_error'}:
        note_bridge_error(kind, message or 'MT5 bridge reported an error.', payload)
    return {
        'status': 'ok',
        'agent': build_bridge_agent_payload(),
    }


@app.post('/mt5/trade/commands/{command_id}/ack')
async def acknowledge_mt5_trade_command(command_id: str, request: Request):
    bridge_state = state.bridge
    payload = _parse_bridge_plain_payload(await request.body())
    try:
        command = post_trade_internal(
            f'/internal/mt5/trade/commands/{command_id}/ack',
            payload,
            timeout=5.0,
        ).get('command')
    except Exception as error:
        safe_error = _trim_bridge_text(error) or 'Trade command ack forwarding failed.'
        bridge_state.ea_last_error = safe_error
        bridge_state.ea_last_error_at = time.time()
        _remember_bridge_event(
            'trade_command_ack_forward_error',
            safe_error,
            level='error',
            command_id=str(command_id or '').strip() or None,
        )
        command = None
    bridge_state.trade_command_last_ack_id = str(command_id or '').strip() or None
    bridge_state.trade_command_last_ack_at = time.time()
    _remember_bridge_event(
        'trade_command_ack',
        'Trade command acknowledged by EA.',
        level='info',
        command_id=bridge_state.trade_command_last_ack_id,
        order_id=_trim_bridge_text(payload.get('order_id')) or None,
    )
    return {
        'status': 'ok' if command else 'not_found',
        'command_id': command_id,
        'trade_runtime': dict(get_trade_runtime_via_service(fallback_local=True).get('trade_runtime') or {}),
    }


@app.post('/mt5/trade/commands/{command_id}/result')
async def finalize_mt5_trade_command(command_id: str, request: Request):
    bridge_state = state.bridge
    payload = _parse_bridge_plain_payload(await request.body())
    try:
        command = post_trade_internal(
            f'/internal/mt5/trade/commands/{command_id}/result',
            payload,
            timeout=5.0,
        ).get('command')
    except Exception as error:
        safe_error = _trim_bridge_text(error) or 'Trade command result forwarding failed.'
        bridge_state.ea_last_error = safe_error
        bridge_state.ea_last_error_at = time.time()
        _remember_bridge_event(
            'trade_command_result_forward_error',
            safe_error,
            level='error',
            command_id=str(command_id or '').strip() or None,
        )
        command = None
    bridge_state.trade_command_last_result_id = str(command_id or '').strip() or None
    bridge_state.trade_command_last_result_status = _trim_bridge_text(payload.get('status')) or None
    bridge_state.trade_command_last_result_at = time.time()
    _remember_bridge_event(
        'trade_command_result',
        'Trade command result received from EA.',
        level='info',
        command_id=bridge_state.trade_command_last_result_id,
        result_status=bridge_state.trade_command_last_result_status,
        message=_trim_bridge_text(payload.get('message')) or None,
    )
    return {
        'status': 'ok' if command else 'not_found',
        'command_id': command_id,
        'trade_runtime': dict(get_trade_runtime_via_service(fallback_local=True).get('trade_runtime') or {}),
    }


@app.get('/market-data/request/{request_id}')
def get_market_data_request(request_id: str, request: Request):
    require_request_auth(request)
    payload = build_market_request_payload(request_id)
    if not payload:
        return {
            'status': 'not_found',
            'request_id': request_id,
        }
    return payload


@app.get('/market-data/request/{request_id}/result')
def get_market_data_request_result(request_id: str, request: Request):
    require_request_auth(request)
    payload = build_market_request_payload(request_id)
    if not payload:
        return {
            'status': 'not_found',
            'request_id': request_id,
        }

    cache_key = payload.get('cache_key')
    cache_payload = state.market_data.cache_by_key.get(cache_key) or {}
    return {
        'request_id': request_id,
        'status': payload.get('status'),
        'cache_key': cache_key,
        'snapshot': cache_payload.get('snapshot'),
        'error': payload.get('error'),
    }


@app.get('/market-data/cache')
def get_market_data_cache(symbol: str, timeframe: str, bars: int, request: Request):
    require_request_auth(request)
    cache_key = build_market_cache_key(symbol, timeframe, bars)
    cache_payload = state.market_data.cache_by_key.get(cache_key)
    if not cache_payload:
        return {
            'status': 'not_found',
            'cache_key': cache_key,
        }
    return {
        'status': 'ok',
        **dict(cache_payload),
    }


@app.post('/bridge/history')
@app.post('/mt5/jobs/{request_id}/history')
async def receive_history_binary(request: Request, request_id: str | None = None):
    bridge_state = state.bridge

    raw = await request.body()
    if not raw:
        fail_history_state('Empty history body')
        return {'status': 'empty_body', **build_history_state_payload()}

    newline_pos = raw.find(b'\n')
    if newline_pos == -1:
        fail_history_state('Invalid history header separator')
        return {'status': 'invalid_header', **build_history_state_payload()}

    header_bytes = raw[:newline_pos]
    payload_bytes = raw[newline_pos + 1:]

    try:
        header = header_bytes.decode('ascii', errors='strict')
    except UnicodeDecodeError:
        fail_history_state('History header is not valid ASCII')
        return {'status': 'invalid_header_encoding', **build_history_state_payload()}

    parts = header.split('|')
    header_kind = parts[0] if parts else ''

    if header_kind not in {'H', 'HC'}:
        fail_history_state(f'Invalid history header: {header}')
        return {'status': 'invalid_header', 'header': header, **build_history_state_payload()}
    try:
        if header_kind == 'H':
            if len(parts) != 4:
                raise ValueError(f'Invalid history header: {header}')
            symbol = parts[1].strip().upper()
            timeframe = parts[2].strip().upper()
            total_candles = max(0, int(parts[3]))
            parsed_candles = parse_history_candles(payload_bytes, total_candles)
            response_payload = finalize_history_load(
                symbol=symbol,
                timeframe=timeframe,
                candles=parsed_candles,
                request_id=request_id,
                requested_total_candles=total_candles,
            )
            print(
                f'HISTORY LOADED | symbol={symbol} tf={timeframe} | '
                f'total={len(parsed_candles)} | '
                f'first_time={parsed_candles[0]["time"] if parsed_candles else None} | '
                f'last_time={parsed_candles[-1]["time"] if parsed_candles else None}'
            )
            return response_payload

        if len(parts) != 7:
            raise ValueError(f'Invalid chunked history header: {header}')

        symbol = parts[1].strip().upper()
        timeframe = parts[2].strip().upper()
        total_candles = max(0, int(parts[3]))
        chunk_index = max(0, int(parts[4]))
        chunk_count = max(1, int(parts[5]))
        chunk_candles = max(0, int(parts[6]))
        parsed_chunk_candles = parse_history_candles(payload_bytes, chunk_candles)
    except ValueError as error:
        fail_history_state(str(error))
        return {'status': 'invalid_history_payload', 'error': str(error), **build_history_state_payload()}

    effective_request_id = str(request_id or bridge_state.active_request_id or '').strip() or '__default__'
    chunk_session = bridge_state.history_chunk_sessions.get(effective_request_id)
    if (
        not chunk_session
        or chunk_index == 0
        or chunk_session.get('symbol') != symbol
        or chunk_session.get('timeframe') != timeframe
        or int(chunk_session.get('total_candles') or 0) != total_candles
        or int(chunk_session.get('chunk_count') or 0) != chunk_count
    ):
        chunk_session = {
            'symbol': symbol,
            'timeframe': timeframe,
            'total_candles': total_candles,
            'chunk_count': chunk_count,
            'chunks': {},
            'started_at': time.time(),
        }
        bridge_state.history_chunk_sessions[effective_request_id] = chunk_session

    chunk_session['chunks'][chunk_index] = parsed_chunk_candles
    received_chunks = len(chunk_session['chunks'])
    loaded_candles = sum(len(chunk_session['chunks'].get(index) or []) for index in range(received_chunks))
    bridge_state.history_meta = {
        'symbol': symbol,
        'timeframe': timeframe,
        'requested_bars': total_candles,
        'loaded_candles': loaded_candles,
        'first_time': None,
        'last_time': None,
        'last_reset_reason': bridge_state.history_meta.get('last_reset_reason'),
    }

    if received_chunks < chunk_count:
        return {
            'status': 'partial',
            'request_id': effective_request_id,
            'chunk_index': chunk_index,
            'chunk_count': chunk_count,
            'received_chunks': received_chunks,
            'loaded_candles': loaded_candles,
            **build_history_state_payload(),
        }

    assembled_candles = []
    for index in range(chunk_count):
        next_chunk = chunk_session['chunks'].get(index)
        if next_chunk is None:
            fail_history_state(f'Missing history chunk {index + 1}/{chunk_count}')
            return {'status': 'missing_chunk', 'chunk_index': index, **build_history_state_payload()}
        assembled_candles.extend(next_chunk)

    assembled_candles.sort(key=lambda candle: candle['time'])
    if total_candles and len(assembled_candles) != total_candles:
        fail_history_state(f'Invalid assembled candle count: expected={total_candles} received={len(assembled_candles)}')
        return {'status': 'invalid_assembled_count', **build_history_state_payload()}

    response_payload = finalize_history_load(
        symbol=symbol,
        timeframe=timeframe,
        candles=assembled_candles,
        request_id=effective_request_id,
        requested_total_candles=total_candles,
    )
    print(
        f'HISTORY LOADED | symbol={symbol} tf={timeframe} | '
        f'total={len(assembled_candles)} | '
        f'first_time={assembled_candles[0]["time"] if assembled_candles else None} | '
        f'last_time={assembled_candles[-1]["time"] if assembled_candles else None}'
    )
    return response_payload


@app.post('/bridge/update')
@app.post('/mt5/jobs/{request_id}/update')
async def receive_update(request: Request, request_id: str | None = None):
    bridge_state = state.bridge

    raw = await request.body()
    text = raw.decode('utf-8', errors='replace').strip()

    parsed_candles = parse_update_text(text)
    if parsed_candles is None:
        return {'status': 'invalid_update', 'body': text, **build_history_state_payload()}

    refresh_history_timeout(get_history_timeout_for_request())

    if not bridge_state.history_ready:
        return {
            'status': 'ignored_not_ready',
            'message': 'Update ignored because full history is not ready',
            **build_history_state_payload(),
        }

    merge_results = []
    merge_changed = False
    affected_from_index = None
    replaced_times = []
    for candle in parsed_candles:
        merge_result, merge_index, merge_replaced_times = merge_candle(candle)
        if merge_result != 'ignored_out_of_order':
            merge_changed = True
        if merge_index is not None:
            if affected_from_index is None:
                affected_from_index = merge_index
            else:
                affected_from_index = min(affected_from_index, merge_index)
        replaced_times.extend(merge_replaced_times)

        merge_results.append({
            'time': candle['time'],
            'result': merge_result,
            'affected_index': merge_index,
        })

    if merge_changed:
        changed_features = build_changed_market_features()
        strategy_overlap = get_strategy_overlap_for_market_update(changed_features)

        bridge_state.revision += 1
        mark_candle_update(
            candles=bridge_state.candles,
            affected_from_index=affected_from_index,
            replaced_times=replaced_times,
            changed_features=changed_features,
        )
        try:
            post_trade_internal('/internal/trade/market-update', {
                'stage': 'candle_update',
                'symbol': str(bridge_state.history_meta.get('symbol') or bridge_state.request.get('symbol') or '').strip().upper(),
                'timeframe': str(bridge_state.history_meta.get('timeframe') or bridge_state.request.get('timeframe') or '').strip().upper(),
                'candle_count': len(bridge_state.candles),
                'latest_candle_time': getattr(state.market, 'latest_candle_time', None),
                'candles': _slice_trade_market_update_candles(bridge_state.candles),
            })
        except Exception:
            pass
        update_market_data_cache_from_bridge(
            request_id=str(request_id or bridge_state.active_request_id or '').strip() or None,
            symbol=str(bridge_state.history_meta.get('symbol') or bridge_state.request.get('symbol') or '').strip().upper(),
            timeframe=str(bridge_state.history_meta.get('timeframe') or bridge_state.request.get('timeframe') or '').strip().upper(),
            bars=max(1, int(bridge_state.history_meta.get('requested_bars') or bridge_state.request.get('bars') or len(bridge_state.candles) or 1)),
        )
        invalidate_chart_snapshot_if_available('history_updated')
        if strategy_overlap:
            invalidate_strategy_runtime_if_available(
                f'history_updated:{",".join(strategy_overlap)}',
                preserve_runtime=True,
            )
        maintenance_result = run_runtime_maintenance('history_updated')
        try:
            try:
                from .market_backend import broadcast_market_event
            except ImportError:
                from market_backend import broadcast_market_event
            await broadcast_market_event(
                'market.updated',
                source='bridge_update',
                include_chart_delta=True,
            )
        except Exception:
            pass

    else:
        maintenance_result = None

    return {
        'status': 'ok',
        'results': merge_results,
        'affected_from_index': affected_from_index,
        'replaced_times': replaced_times,
        'maintenance': maintenance_result,
        **build_history_state_payload(),
    }


@app.get('/candles')
def get_candles():
    return state.bridge.candles


@app.get('/bridge/last-candles')
def get_last_candles():
    candles = state.bridge.candles
    if len(candles) >= 2:
        return candles[-2:]
    return candles


@app.get('/bridge/ready')
def get_ready():
    payload = build_history_state_payload()
    return {
        'ready': payload['ready'],
        'loading': payload['loading'],
        'error': payload['error'],
    }


@app.get('/bridge/status')
def get_status():
    return {
        'request': dict(state.bridge.request),
        **build_history_state_payload(),
    }


@app.get('/trade/runtime')
def get_trade_runtime(request: Request):
    require_request_auth(request)
    return forward_trade_request('GET', '/trade/runtime', auth_header=request.headers.get('authorization'))


@app.post('/trade/runtime/configure')
def post_trade_runtime_configure(payload: TradeRuntimeConfigureRequest, request: Request):
    require_request_auth(request)
    return forward_trade_request(
        'POST',
        '/trade/runtime/configure',
        auth_header=request.headers.get('authorization'),
        json_payload=payload.model_dump(),
    )


@app.post('/trade/runtime/arm')
def post_trade_runtime_arm(request: Request):
    require_request_auth(request)
    return forward_trade_request('POST', '/trade/runtime/arm', auth_header=request.headers.get('authorization'))


@app.post('/trade/runtime/arm-live-dispatch')
def post_trade_runtime_arm_live_dispatch(request: Request):
    require_request_auth(request)
    return forward_trade_request('POST', '/trade/runtime/arm-live-dispatch', auth_header=request.headers.get('authorization'))


@app.post('/trade/runtime/disarm')
def post_trade_runtime_disarm(request: Request):
    require_request_auth(request)
    return forward_trade_request('POST', '/trade/runtime/disarm', auth_header=request.headers.get('authorization'))


@app.post('/trade/runtime/disarm-live-dispatch')
def post_trade_runtime_disarm_live_dispatch(request: Request):
    require_request_auth(request)
    return forward_trade_request('POST', '/trade/runtime/disarm-live-dispatch', auth_header=request.headers.get('authorization'))


@app.post('/trade/runtime/evaluate')
def post_trade_runtime_evaluate(request: Request):
    require_request_auth(request)
    return forward_trade_request('POST', '/trade/runtime/evaluate', auth_header=request.headers.get('authorization'))


@app.post('/trade/runtime/process-intents')
def post_trade_runtime_process_intents(request: Request):
    require_request_auth(request)
    return forward_trade_request('POST', '/trade/runtime/process-intents', auth_header=request.headers.get('authorization'))


@app.post('/trade/runtime/reconcile')
def post_trade_runtime_reconcile(request: Request):
    require_request_auth(request)
    return forward_trade_request('POST', '/trade/runtime/reconcile', auth_header=request.headers.get('authorization'))


@app.post('/trade/runtime/reset-commands')
def post_trade_runtime_reset_commands(payload: dict | None, request: Request):
    require_request_auth(request)
    return forward_trade_request(
        'POST',
        '/trade/runtime/reset-commands',
        auth_header=request.headers.get('authorization'),
        json_payload=dict(payload or {}),
    )


@app.get('/health')
def get_health():
    return build_service_health_payload()


@app.get('/health/live')
def get_liveness():
    payload = build_service_health_payload()
    return {
        'status': 'ok',
        'service': payload['service'],
    }


@app.get('/health/ready')
def get_readiness():
    payload = build_service_health_payload()
    return {
        'status': payload['status'],
        'ready': payload['status'] == 'ok',
        'checks': payload['checks'],
    }


try:
    from .chart_backend import router as chart_router
    from .auth_backend import router as auth_router
    from .docs_backend import router as docs_router
    from .market_backend import router as market_router
    from .neural_backend import router as neural_router
    from .strategy_backend import router as strategy_router
    from .workspace_backend import router as workspace_router
except ImportError:
    from chart_backend import router as chart_router
    from auth_backend import router as auth_router
    from docs_backend import router as docs_router
    from market_backend import router as market_router
    from neural_backend import router as neural_router
    from strategy_backend import router as strategy_router
    from workspace_backend import router as workspace_router

app.include_router(auth_router)
app.include_router(chart_router)
app.include_router(docs_router)
app.include_router(market_router)
app.include_router(neural_router)
app.include_router(strategy_router)
app.include_router(workspace_router)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import threading
import time

try:
    from .config import build_trade_service_config
    from .app_state import state
    from .services.auth_service import build_guest_access_denial_payload, require_request_auth
    from .services.trade_runtime_service import (
        acknowledge_trade_order_command,
        _sanitize_trade_payload_value,
        arm_trade_live_dispatch,
        arm_trade_runtime,
        auto_process_trade_order_intents_if_needed,
        build_trade_runtime_health_payload,
        build_trade_runtime_payload,
        claim_next_trade_order_command,
        configure_trade_runtime,
        disarm_trade_live_dispatch,
        disarm_trade_runtime,
        evaluate_trade_runtime,
        finalize_trade_order_command,
        note_trade_bridge_event,
        note_trade_bridge_heartbeat,
        note_trade_market_update,
        process_trade_order_intents,
        reconcile_trade_runtime_commands,
        reset_trade_runtime_commands,
    )
    from .trade_runtime_contract import TradeRuntimeConfigureRequest
except ImportError:
    from config import build_trade_service_config
    from app_state import state
    from services.auth_service import build_guest_access_denial_payload, require_request_auth
    from services.trade_runtime_service import (
        acknowledge_trade_order_command,
        _sanitize_trade_payload_value,
        arm_trade_live_dispatch,
        arm_trade_runtime,
        auto_process_trade_order_intents_if_needed,
        build_trade_runtime_health_payload,
        build_trade_runtime_payload,
        claim_next_trade_order_command,
        configure_trade_runtime,
        disarm_trade_live_dispatch,
        disarm_trade_runtime,
        evaluate_trade_runtime,
        finalize_trade_order_command,
        note_trade_bridge_event,
        note_trade_bridge_heartbeat,
        note_trade_market_update,
        process_trade_order_intents,
        reconcile_trade_runtime_commands,
        reset_trade_runtime_commands,
    )
    from trade_runtime_contract import TradeRuntimeConfigureRequest

app = FastAPI()
SERVICE_STARTED_AT = time.time()
SERVICE_CONFIG = build_trade_service_config()
_SCHEDULED_MARKET_EVALUATION_LOCK = threading.Lock()
_SCHEDULED_MARKET_EVALUATION_THREAD = None
_SCHEDULED_MARKET_EVALUATION_TRIGGER = None

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


def _trim_text(value):
    return str(value or '').strip()


def _require_internal(request: Request):
    configured_token = _trim_text(SERVICE_CONFIG.get('internal_token'))
    if not configured_token:
        return
    request_token = _trim_text(request.headers.get('x-robotineeko-trade-internal-token'))
    if request_token != configured_token:
        raise HTTPException(status_code=401, detail={'error': 'Internal trade token required.'})


def _bind_authenticated_workspace_user(auth_user: dict | None):
    if not auth_user:
        return
    workspace_user_id = _trim_text(auth_user.get('workspace_user_id'))
    if workspace_user_id:
        state.workspace.active_user_id = workspace_user_id
    state.workspace.active_workspace_id = 'default'


def _drain_scheduled_market_evaluation():
    global _SCHEDULED_MARKET_EVALUATION_THREAD, _SCHEDULED_MARKET_EVALUATION_TRIGGER

    while True:
        with _SCHEDULED_MARKET_EVALUATION_LOCK:
            trigger = _trim_text(_SCHEDULED_MARKET_EVALUATION_TRIGGER) or ''
            _SCHEDULED_MARKET_EVALUATION_TRIGGER = None
        if not trigger:
            break
        try:
            payload = evaluate_trade_runtime(trigger=trigger)
            if payload.get('armed'):
                auto_process_trade_order_intents_if_needed()
        except Exception as error:
            state.trade.last_error = _trim_text(error) or 'Trade runtime evaluation failed.'

    with _SCHEDULED_MARKET_EVALUATION_LOCK:
        if _trim_text(_SCHEDULED_MARKET_EVALUATION_TRIGGER):
            thread = threading.Thread(
                target=_drain_scheduled_market_evaluation,
                name='trade-market-evaluation',
                daemon=True,
            )
            _SCHEDULED_MARKET_EVALUATION_THREAD = thread
            thread.start()
        else:
            _SCHEDULED_MARKET_EVALUATION_THREAD = None


def _schedule_runtime_market_evaluation(trigger: str):
    global _SCHEDULED_MARKET_EVALUATION_THREAD, _SCHEDULED_MARKET_EVALUATION_TRIGGER

    safe_trigger = _trim_text(trigger) or 'market_update'
    with _SCHEDULED_MARKET_EVALUATION_LOCK:
        _SCHEDULED_MARKET_EVALUATION_TRIGGER = safe_trigger
        if _SCHEDULED_MARKET_EVALUATION_THREAD is not None and _SCHEDULED_MARKET_EVALUATION_THREAD.is_alive():
            return False
        thread = threading.Thread(
            target=_drain_scheduled_market_evaluation,
            name='trade-market-evaluation',
            daemon=True,
        )
        _SCHEDULED_MARKET_EVALUATION_THREAD = thread
        thread.start()
    return True


@app.get('/health')
def get_trade_health():
    payload = build_trade_runtime_health_payload()
    has_error = bool(_trim_text(payload.get('last_error')))
    return {
        'status': 'ok' if not has_error else 'degraded',
        'service': {
            'process_id': os.getpid(),
            'started_at': SERVICE_STARTED_AT,
            'uptime_seconds': round(max(0.0, time.time() - SERVICE_STARTED_AT), 3),
            'reachable': True,
        },
        'trade_runtime': payload,
    }


@app.get('/trade/runtime')
def get_trade_runtime(request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    return {
        'status': 'ok',
        'trade_runtime': build_trade_runtime_payload(),
    }


@app.get('/internal/trade/runtime')
def get_internal_trade_runtime(request: Request):
    _require_internal(request)
    return {
        'status': 'ok',
        'trade_runtime': build_trade_runtime_payload(),
    }


@app.post('/trade/runtime/configure')
def post_trade_runtime_configure(payload: TradeRuntimeConfigureRequest, request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    return {
        'status': 'ok',
        'trade_runtime': configure_trade_runtime(payload.model_dump()),
    }


@app.post('/trade/runtime/arm')
def post_trade_runtime_arm(request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    return {
        'status': 'ok',
        'trade_runtime': arm_trade_runtime(),
    }


@app.post('/trade/runtime/arm-live-dispatch')
def post_trade_runtime_arm_live_dispatch(request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    return {
        'status': 'ok',
        'trade_runtime': arm_trade_live_dispatch(),
    }


@app.post('/trade/runtime/disarm')
def post_trade_runtime_disarm(request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    return {
        'status': 'ok',
        'trade_runtime': disarm_trade_runtime(reason='manual'),
    }


@app.post('/trade/runtime/disarm-live-dispatch')
def post_trade_runtime_disarm_live_dispatch(request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    return {
        'status': 'ok',
        'trade_runtime': disarm_trade_live_dispatch(),
    }


@app.post('/trade/runtime/evaluate')
def post_trade_runtime_evaluate(request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    payload = evaluate_trade_runtime(trigger='manual')
    if payload.get('armed'):
        payload = auto_process_trade_order_intents_if_needed()
    return {
        'status': 'ok',
        'trade_runtime': payload,
    }


@app.post('/trade/runtime/process-intents')
def post_trade_runtime_process_intents(request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    return {
        'status': 'ok',
        'trade_runtime': process_trade_order_intents(),
    }


@app.post('/trade/runtime/reconcile')
def post_trade_runtime_reconcile(request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    return {
        'status': 'ok',
        'trade_runtime': reconcile_trade_runtime_commands(),
    }


@app.post('/trade/runtime/reset-commands')
def post_trade_runtime_reset_commands(payload: dict | None, request: Request):
    auth_user = require_request_auth(request)
    _bind_authenticated_workspace_user(auth_user)
    safe_payload = dict(payload or {})
    return {
        'status': 'ok',
        'trade_runtime': reset_trade_runtime_commands(
            clear_intents=bool(safe_payload.get('clearIntents') or safe_payload.get('clear_intents'))
        ),
    }


@app.post('/internal/trade/bridge-heartbeat')
def post_trade_bridge_heartbeat(payload: dict | None, request: Request):
    _require_internal(request)
    note_trade_bridge_heartbeat(payload)
    return {
        'status': 'ok',
        'trade_runtime': build_trade_runtime_payload(),
    }


@app.post('/internal/trade/bridge-event')
def post_trade_bridge_event(payload: dict | None, request: Request):
    _require_internal(request)
    safe_payload = dict(payload or {})
    note_trade_bridge_event(_trim_text(safe_payload.get('kind')) or 'event', safe_payload)
    return {
        'status': 'ok',
        'trade_runtime': build_trade_runtime_payload(),
    }


@app.post('/internal/trade/market-update')
def post_trade_market_update(payload: dict | None, request: Request):
    _require_internal(request)
    safe_payload = dict(payload or {})
    note_trade_market_update(
        safe_payload.get('stage') or 'update',
        symbol=safe_payload.get('symbol') or '',
        timeframe=safe_payload.get('timeframe') or '',
        candle_count=safe_payload.get('candle_count'),
        latest_candle_time=safe_payload.get('latest_candle_time'),
        candles=list(safe_payload.get('candles') or []),
    )
    if state.trade.armed:
        # Coalesce market-triggered evaluations so the trade service does not
        # synchronously call back into the main backend while it is still
        # delivering the current market update.
        _schedule_runtime_market_evaluation(_trim_text(safe_payload.get('stage')) or 'market_update')
    return {
        'status': 'ok',
        'trade_runtime': build_trade_runtime_payload(),
    }


@app.get('/internal/mt5/trade/commands/next')
@app.post('/internal/mt5/trade/commands/next')
def get_trade_command_next(request: Request, session_id: str = '', payload: dict | None = None):
    _require_internal(request)
    safe_payload = dict(payload or {})
    command = claim_next_trade_order_command(_trim_text(session_id) or _trim_text(safe_payload.get('session_id')))
    return {
        'status': 'ok' if command else 'empty',
        'command': _sanitize_trade_payload_value(command),
    }


@app.post('/internal/mt5/trade/commands/{command_id}/ack')
def post_trade_command_ack(command_id: str, payload: dict | None, request: Request):
    _require_internal(request)
    command = acknowledge_trade_order_command(command_id, payload)
    return {
        'status': 'ok' if command else 'missing',
        'command': _sanitize_trade_payload_value(command),
    }


@app.post('/internal/mt5/trade/commands/{command_id}/result')
def post_trade_command_result(command_id: str, payload: dict | None, request: Request):
    _require_internal(request)
    command = finalize_trade_order_command(command_id, payload)
    return {
        'status': 'ok' if command else 'missing',
        'command': _sanitize_trade_payload_value(command),
    }

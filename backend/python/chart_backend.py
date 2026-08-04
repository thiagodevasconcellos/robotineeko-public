from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

try:
    from . import bridge
    from .app_state import state
    from .runtime.chart_runtime import ensure_chart_snapshot
    from .services.chart_service import (
        apply_chart_settings,
        build_chart_symbol_catalog_payload,
        build_status_payload,
        extend_chart_history,
        wait_for_history,
        invalidate_strategy_runtime_if_available,
    )
    from .services.market_data_service import ensure_market_data
    from .services.auth_service import require_request_auth
except ImportError:
    import bridge
    from app_state import state
    from runtime.chart_runtime import ensure_chart_snapshot
    from services.chart_service import (
        apply_chart_settings,
        build_chart_symbol_catalog_payload,
        build_status_payload,
        extend_chart_history,
        wait_for_history,
        invalidate_strategy_runtime_if_available,
    )
    from services.market_data_service import ensure_market_data
    from services.auth_service import require_request_auth

router = APIRouter()


class ChartMarketTailItem(BaseModel):
    symbol: str
    timeframe: str
    bars: int = Field(default=2, ge=1, le=16)


class ChartMarketTailRequest(BaseModel):
    markets: list[ChartMarketTailItem] = Field(default_factory=list, max_length=64)


def resolve_chart_history_timeout(requested_timeout: float | None = None):
    safe_requested = max(1.0, float(requested_timeout or 0.0))
    requested_bars = max(1, int((state.chart.request or {}).get('bars') or 1))

    if requested_bars <= 10000:
        recommended_timeout = 12.0
    elif requested_bars <= 50000:
        recommended_timeout = 20.0
    elif requested_bars <= 100000:
        recommended_timeout = 30.0
    else:
        recommended_timeout = 45.0

    return max(safe_requested, recommended_timeout)


def build_indicator_column_details(applied_indicators):
    details = []

    for indicator in applied_indicators or []:
        for column_detail in indicator.get('column_details', []) or []:
            details.append(dict(column_detail))

    return details


def build_changed_indicator_column_details(indicator_rows, applied_indicators):
    if not indicator_rows:
        return []

    changed_columns = set()

    for row in indicator_rows:
        for key, value in (row or {}).items():
            if key == 'time':
                continue

            if value is not None:
                changed_columns.add(str(key).strip())

    details = []
    for detail in build_indicator_column_details(applied_indicators):
        normalized_column_name = str(
            detail.get('normalized_column_name')
            or detail.get('column_name')
            or ''
        ).strip()

        if normalized_column_name in changed_columns:
            details.append(detail)

    return details


def build_chart_delta_payload(since_revision: int | None = None):
    bridge_state = state.bridge
    chart_state = state.chart
    payload = build_status_payload()
    current_revision = payload.get('runtime', {}).get('market_runtime', {}).get('revision', 0)

    if not bridge_state.history_ready:
        return {
            'status': 'error' if payload['error'] else 'not_ready',
            **payload,
            'mode': 'pending',
            'candles': [],
            'indicators': [],
            'applied_indicators': [],
        }

    if since_revision is not None and since_revision == current_revision:
        return {
            'status': 'ok',
            **payload,
            'mode': 'no_change',
            'from_revision': since_revision,
            'to_revision': current_revision,
            'candles': [],
            'indicators': [],
            'applied_indicators': chart_state.snapshot_applied_indicators,
            'available_column_details': list(chart_state.snapshot_available_column_details),
            'indicator_column_details': build_indicator_column_details(chart_state.snapshot_applied_indicators),
            'changed_indicator_column_details': [],
        }

    try:
        _, applied_indicators = ensure_chart_snapshot()
        runtime_payload = payload.get('runtime', {}).get('market_runtime', {})
        affected_from_index = runtime_payload.get('affected_from_index')
        sequential_delta = (
            since_revision is not None
            and since_revision == current_revision - 1
            and affected_from_index is not None
        )

        if sequential_delta:
            candles = chart_state.snapshot_candles[affected_from_index:]
            indicators = chart_state.snapshot_indicators[affected_from_index:]
            mode = 'delta'
        else:
            candles = chart_state.snapshot_candles
            indicators = chart_state.snapshot_indicators
            mode = 'snapshot'

        return {
            'status': 'ok',
            **build_status_payload(),
            'mode': mode,
            'from_revision': since_revision,
            'to_revision': current_revision,
            'affected_from_index': affected_from_index,
            'candles': candles,
            'indicators': indicators,
            'applied_indicators': applied_indicators,
            'available_column_details': list(chart_state.snapshot_available_column_details),
            'indicator_column_details': build_indicator_column_details(applied_indicators),
            'changed_indicator_column_details': build_changed_indicator_column_details(indicators, applied_indicators),
        }

    except Exception as error:
        return {
            'status': 'partial',
            **build_status_payload(),
            'mode': 'snapshot',
            'from_revision': since_revision,
            'to_revision': current_revision,
            'candles': list(bridge_state.candles),
            'indicators': [],
            'applied_indicators': [],
            'error': str(error),
        }


class IndicatorRequest(BaseModel):
    name: str
    params: list = Field(default_factory=list)


class ChartSettings(BaseModel):
    symbol: str
    timeframe: str
    bars: int | None = None
    indicators: list[IndicatorRequest] = Field(default_factory=list)


class ChartHistoryExtensionRequest(BaseModel):
    extra_bars: int | None = None


@router.post('/chart/set-request')
@router.post('/chart/set-settings')
def set_chart_request(payload: ChartSettings, request: Request):
    require_request_auth(request)
    settings, market_request_changed = apply_chart_settings(
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        bars=payload.bars,
        indicators=payload.indicators,
    )

    return {
        'status': 'ok',
        'settings': settings,
        'market_request_changed': market_request_changed,
        **build_status_payload(),
    }


@router.post('/chart/reload')
def reload_chart(request: Request):
    require_request_auth(request)
    bridge.reset_history_state(reason='chart_reload')
    invalidate_strategy_runtime_if_available('chart_reload')

    return {
        'status': 'ok',
        'message': 'Chart history reset',
        **build_status_payload(),
    }


@router.post('/chart/load-more-left')
def load_more_chart_history(payload: ChartHistoryExtensionRequest, request: Request):
    require_request_auth(request)
    result = extend_chart_history(extra_bars=payload.extra_bars)
    return {
        'status': 'ok',
        **result,
        **build_status_payload(),
    }


@router.get('/chart/status')
def get_chart_status(request: Request):
    require_request_auth(request)
    payload = build_status_payload()

    status = 'ok'
    if payload['error']:
        status = 'error'
    elif payload['loading'] and not payload['ready']:
        status = 'loading'

    return {
        'status': status,
        **payload,
    }


@router.get('/chart/symbols')
def get_chart_symbols(request: Request):
    require_request_auth(request)
    payload = build_chart_symbol_catalog_payload()
    return {
        'status': 'ok',
        **payload,
    }


@router.get('/chart/data')
def get_chart_data(request: Request, timeout: float = 10.0):
    require_request_auth(request)
    bridge_state = state.bridge
    resolved_timeout = resolve_chart_history_timeout(timeout)

    history_loaded = wait_for_history(timeout_seconds=resolved_timeout)

    if not history_loaded:
        payload = build_status_payload()
        error_message = payload['error'] or 'History not received from bridge within timeout'

        return {
            'status': 'error',
            'error': error_message,
            **payload,
            'candles': [],
            'indicators': [],
            'applied_indicators': [],
        }

    try:
        _, applied_indicators = ensure_chart_snapshot()
        chart_state = state.chart
        candles = list(chart_state.snapshot_candles)
        indicators = list(chart_state.snapshot_indicators)

        print('CHART DATA STATUS: ok')
        print('APPLIED INDICATORS PAYLOAD:', applied_indicators)
        print('INDICATOR ROW COUNT:', len(indicators))

        return {
            'status': 'ok',
            **build_status_payload(),
            'candles': candles,
            'indicators': indicators,
            'applied_indicators': applied_indicators,
            'available_column_details': list(chart_state.snapshot_available_column_details),
            'indicator_column_details': build_indicator_column_details(applied_indicators),
        }

    except Exception as error:
        return {
            'status': 'partial',
            **build_status_payload(),
            'candles': list(bridge_state.candles),
            'indicators': [],
            'applied_indicators': [],
            'error': str(error),
        }


def build_chart_market_tails_payload(markets):
    deduped_targets = []
    seen = set()

    for entry in list(markets or []):
        symbol = str(getattr(entry, 'symbol', None) or (entry or {}).get('symbol') or '').strip().upper()
        timeframe = str(getattr(entry, 'timeframe', None) or (entry or {}).get('timeframe') or '').strip().upper()
        bars = max(1, min(16, int(getattr(entry, 'bars', None) or (entry or {}).get('bars') or 2)))
        if not symbol or not timeframe:
            continue
        dedupe_key = (symbol, timeframe, bars)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped_targets.append({
            'symbol': symbol,
            'timeframe': timeframe,
            'bars': bars,
        })

    payload_rows = []
    overall_status = 'ok'

    for target in deduped_targets:
        context = ensure_market_data(
            target['symbol'],
            target['timeframe'],
            target['bars'],
            source='mobile_trader',
        )
        snapshot = dict(context.get('snapshot') or {})
        candles = list(snapshot.get('candles') or [])
        sliced_candles = candles[-target['bars']:] if candles else []
        latest = sliced_candles[-1] if sliced_candles else None
        ready = bool(context.get('ready')) and len(sliced_candles) >= target['bars']
        request_status = str(context.get('request_status') or '').strip().lower()
        row_status = 'ok' if ready else (request_status or 'loading')
        if row_status != 'ok' and overall_status == 'ok':
            overall_status = 'partial'

        payload_rows.append({
            'key': f"{target['symbol']}::{target['timeframe']}",
            'symbol': target['symbol'],
            'timeframe': target['timeframe'],
            'bars_requested': target['bars'],
            'bars_loaded': len(sliced_candles),
            'ready': ready,
            'status': row_status,
            'request_status': request_status or ('completed' if ready else 'loading'),
            'provider': str(context.get('source') or snapshot.get('source') or '').strip(),
            'candles': sliced_candles,
            'last_time': latest.get('time') if isinstance(latest, dict) else None,
            'last_close': latest.get('close') if isinstance(latest, dict) else None,
            'error': str(context.get('error') or '').strip(),
        })

    return {
        'status': overall_status,
        'market_count': len(payload_rows),
        'markets': payload_rows,
    }


@router.get('/chart/last-candles')
def get_chart_last_candles(request: Request):
    require_request_auth(request)
    bridge_state = state.bridge
    chart_state = state.chart
    payload = build_status_payload()

    if not bridge_state.history_ready:
        return {
            'status': 'error' if payload['error'] else 'not_ready',
            **payload,
            'candles': [],
            'indicators': [],
            'applied_indicators': [],
        }

    try:
        _, applied_indicators = ensure_chart_snapshot()
        candles = chart_state.snapshot_candles
        indicators = chart_state.snapshot_indicators

        last_candles = candles[-2:] if len(candles) >= 2 else candles
        last_indicators = indicators[-2:] if len(indicators) >= 2 else indicators

        return {
            'status': 'ok',
            **build_status_payload(),
            'candles': last_candles,
            'indicators': last_indicators,
            'applied_indicators': applied_indicators,
            'available_column_details': list(chart_state.snapshot_available_column_details),
            'indicator_column_details': build_indicator_column_details(applied_indicators),
        }

    except Exception as error:
        last_candles = (
            bridge_state.candles[-2:]
            if len(bridge_state.candles) >= 2
            else bridge_state.candles
        )

        return {
            'status': 'partial',
            **build_status_payload(),
            'candles': last_candles,
            'indicators': [],
            'applied_indicators': [],
            'error': str(error),
        }


@router.post('/chart/market-tails')
def get_chart_market_tails(payload: ChartMarketTailRequest, request: Request):
    require_request_auth(request)
    return build_chart_market_tails_payload(payload.markets)


@router.get('/chart/delta')
def get_chart_delta(request: Request, since_revision: int | None = None):
    require_request_auth(request)
    return build_chart_delta_payload(since_revision=since_revision)

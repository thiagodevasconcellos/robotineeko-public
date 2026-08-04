import time

try:
    from ..app_state import state
    from ..indicator_registry import get_indicator_runtime_contract
    from ..runtime.chart_runtime import build_chart_runtime_payload, invalidate_chart_snapshot
    from .market_data_service import ensure_market_data, get_market_snapshot, wait_for_market_data
    from .runtime_service import run_runtime_maintenance
except ImportError:
    from app_state import state
    from indicator_registry import get_indicator_runtime_contract
    from runtime.chart_runtime import build_chart_runtime_payload, invalidate_chart_snapshot
    from services.market_data_service import ensure_market_data, get_market_snapshot, wait_for_market_data
    from services.runtime_service import run_runtime_maintenance

CHART_MIN_SEED_BARS = 600
CHART_MIN_LOAD_STEP = 1000
CHART_MAX_LOAD_STEP = 10000


def build_chart_symbol_catalog_payload():
    seen_symbols: set[str] = set()
    symbols: list[str] = []
    symbol_sources: dict[str, set[str]] = {}

    def remember_symbol(raw_symbol, source: str):
        safe_symbol = str(raw_symbol or '').strip().upper()
        if not safe_symbol:
            return
        symbol_sources.setdefault(safe_symbol, set()).add(source)
        if safe_symbol in seen_symbols:
            return
        seen_symbols.add(safe_symbol)
        symbols.append(safe_symbol)

    bridge_request = dict(getattr(state.bridge, 'request', {}) or {})
    bridge_history_meta = dict(getattr(state.bridge, 'history_meta', {}) or {})
    chart_request = dict(getattr(state.chart, 'request', {}) or {})
    market_watch_symbols = list(getattr(state.bridge, 'ea_market_watch_symbols', []) or [])
    market_watch_exhaustive = bool(getattr(state.bridge, 'ea_market_watch_exhaustive', False))
    trade_state = getattr(state, 'trade', None)
    use_strict_market_watch_catalog = bool(market_watch_symbols and market_watch_exhaustive)

    for symbol_name in market_watch_symbols:
        remember_symbol(symbol_name, 'mt5_market_watch')

    if not use_strict_market_watch_catalog:
        remember_symbol(chart_request.get('symbol'), 'chart_request')
        remember_symbol(bridge_request.get('symbol'), 'bridge_request')
        remember_symbol(bridge_history_meta.get('symbol'), 'bridge_history')

    if trade_state is not None and not use_strict_market_watch_catalog:
        for symbol_name in list(getattr(trade_state, 'active_symbols', []) or []):
            remember_symbol(symbol_name, 'trade_active_symbols')
        for symbol_name in dict(getattr(trade_state, 'broker_symbol_rules', {}) or {}).keys():
            remember_symbol(symbol_name, 'broker_symbol_rules')
        for position in list(getattr(trade_state, 'broker_positions', []) or []):
            if isinstance(position, dict):
                remember_symbol(position.get('symbol'), 'broker_positions')

    if not use_strict_market_watch_catalog:
        for cache_key, cache_payload in dict(getattr(state.market_data, 'cache_by_key', {}) or {}).items():
            if isinstance(cache_key, str):
                remember_symbol(cache_key.split('|', 1)[0], 'market_cache_key')
            if isinstance(cache_payload, dict):
                remember_symbol(cache_payload.get('symbol'), 'market_cache_entry')
                snapshot = cache_payload.get('snapshot')
                if isinstance(snapshot, dict):
                    remember_symbol(snapshot.get('symbol'), 'market_cache_snapshot')

        for request_payload in dict(getattr(state.market_data, 'requests_by_id', {}) or {}).values():
            if isinstance(request_payload, dict):
                remember_symbol(request_payload.get('symbol'), 'market_request')

    symbol_rows = [
        {
            'symbol': symbol_name,
            'sources': sorted(symbol_sources.get(symbol_name) or []),
        }
        for symbol_name in sorted(symbols)
    ]

    if use_strict_market_watch_catalog:
        source = 'mt5_market_watch'
        note = 'Exhaustive symbol catalog from the MT5 Market Watch only.'
    else:
        source = 'mt5_known_symbols_subset'
        note = (
            'Best-effort subset of symbols currently known through the MT5 bridge, '
            'market cache, and trade runtime. Custom symbols must remain allowed until '
            'the bridge publishes an exhaustive market-watch symbol catalog.'
        )

    return {
        'symbols': [row['symbol'] for row in symbol_rows],
        'rows': symbol_rows,
        'exhaustive': use_strict_market_watch_catalog,
        'source': source,
        'note': note,
    }


def invalidate_strategy_runtime_if_available(reason: str):
    try:
        try:
            from .. import strategy_backend
        except ImportError:
            import strategy_backend
        strategy_backend.invalidate_strategy_runtime(reason)
    except Exception:
        pass


def normalize_indicator_payload(indicators):
    normalized = []

    for indicator in indicators:
        if isinstance(indicator, dict):
            name = str(indicator.get('name') or '').strip()
            params = list(indicator.get('params') or [])
        else:
            name = str(getattr(indicator, 'name', '') or '').strip()
            params = list(getattr(indicator, 'params', []) or [])

        if not name:
            continue

        normalized.append({
            'name': name,
            'params': params,
        })

    return normalized


def _safe_int_param(raw_params, index: int, default: int):
    try:
        return int(raw_params[index])
    except (TypeError, ValueError, IndexError):
        return int(default)


def estimate_indicator_seed_bars(indicator: dict):
    indicator_name = str(indicator.get('name') or '').strip()
    raw_params = list(indicator.get('params') or [])
    contract = get_indicator_runtime_contract(indicator_name, raw_params)
    safe_name = indicator_name.strip().upper()
    warmup_bars = max(0, int(contract.get('warmup_bars', 0) or 0))
    patch_bars = max(0, int(contract.get('patch_bars', 0) or 0))
    estimated_seed = warmup_bars + patch_bars + 64

    if safe_name == 'EMA':
        period = max(1, _safe_int_param(raw_params, 1, 20))
        estimated_seed = max(estimated_seed, period * 6)
    elif safe_name == 'RSI':
        period = max(1, _safe_int_param(raw_params, 1, 14))
        estimated_seed = max(estimated_seed, period * 6)
    elif safe_name == 'MACD':
        slow_period = max(1, _safe_int_param(raw_params, 2, 26))
        estimated_seed = max(estimated_seed, slow_period * 6)
    elif safe_name in {'ADX', 'ATR', 'CHOPPINESSINDEX'}:
        period = max(1, _safe_int_param(raw_params, 0, 14))
        estimated_seed = max(estimated_seed, period * 6)
    elif safe_name == 'VWAP':
        estimated_seed = max(estimated_seed, 1000)
    elif safe_name == 'KELTNERCHANNELS':
        period = max(1, _safe_int_param(raw_params, 1, 20))
        estimated_seed = max(estimated_seed, period * 6)
    elif safe_name == 'SUPERTREND':
        period = max(1, _safe_int_param(raw_params, 0, 10))
        estimated_seed = max(estimated_seed, period * 8)
    elif safe_name == 'MARKETREGIME':
        estimated_seed = max(estimated_seed, 1000)

    return max(32, estimated_seed)


def calculate_chart_history_plan(indicators):
    normalized_indicators = normalize_indicator_payload(indicators)
    recommended_seed_bars = CHART_MIN_SEED_BARS

    for indicator in normalized_indicators:
        recommended_seed_bars = max(
            recommended_seed_bars,
            estimate_indicator_seed_bars(indicator),
        )

    load_step = max(
        CHART_MIN_LOAD_STEP,
        min(CHART_MAX_LOAD_STEP, ((recommended_seed_bars + 499) // 500) * 500),
    )

    return {
        'recommended_seed_bars': recommended_seed_bars,
        'history_load_step': load_step,
        'indicators': normalized_indicators,
    }


def resolve_chart_bars(symbol: str, timeframe: str, bars: int | None, indicators, previous_request: dict | None = None):
    plan = calculate_chart_history_plan(indicators)
    safe_symbol = str(symbol or '').strip().upper()
    safe_timeframe = str(timeframe or '').strip().upper()
    explicit_bars_requested = bars is not None
    requested_bars = max(0, int(bars or 0))
    previous_bars = 0

    if (
        previous_request
        and str(previous_request.get('symbol') or '').strip().upper() == safe_symbol
        and str(previous_request.get('timeframe') or '').strip().upper() == safe_timeframe
    ):
        previous_bars = max(0, int(previous_request.get('bars') or 0))

    baseline_bars = requested_bars if explicit_bars_requested else plan['recommended_seed_bars']

    if explicit_bars_requested:
        baseline_bars = requested_bars or previous_bars or plan['recommended_seed_bars']

    resolved_bars = max(plan['recommended_seed_bars'], baseline_bars)
    return resolved_bars, plan


def normalize_settings(symbol: str, timeframe: str, bars: int | None, indicators, previous_request: dict | None = None):
    resolved_bars, history_plan = resolve_chart_bars(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        indicators=indicators,
        previous_request=previous_request,
    )
    return {
        'symbol': symbol.strip().upper(),
        'timeframe': timeframe.strip().upper(),
        'bars': resolved_bars,
        'indicators': history_plan['indicators'],
        'recommended_seed_bars': history_plan['recommended_seed_bars'],
        'history_load_step': history_plan['history_load_step'],
    }


def has_market_request_changed(old_request: dict, new_request: dict):
    return (
        old_request['symbol'] != new_request['symbol']
        or old_request['timeframe'] != new_request['timeframe']
        or old_request['bars'] != new_request['bars']
    )


def apply_chart_settings(symbol: str, timeframe: str, bars: int | None, indicators):
    chart_state = state.chart
    bridge_state = state.bridge
    previous_request = chart_state.request

    settings = normalize_settings(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        indicators=indicators,
        previous_request=previous_request,
    )

    market_request_changed = has_market_request_changed(previous_request, settings)
    chart_request_changed = previous_request != settings
    chart_state.request = settings

    bridge_state.request['symbol'] = settings['symbol']
    bridge_state.request['timeframe'] = settings['timeframe']
    bridge_state.request['bars'] = settings['bars']

    if market_request_changed:
        market_context = ensure_market_data(
            symbol=settings['symbol'],
            timeframe=settings['timeframe'],
            bars=settings['bars'],
            source='chart_settings',
        )
        if not market_context['ready']:
            try:
                from .. import bridge
            except ImportError:
                import bridge
            bridge.reset_history_state(reason='chart_settings_changed')

    if chart_request_changed:
        invalidate_chart_snapshot('chart_settings_changed')
        invalidate_strategy_runtime_if_available('chart_settings_changed')

    if bridge_state.history_ready and chart_request_changed:
        run_runtime_maintenance('chart_settings_changed')

    print('CHART SETTINGS APPLIED:', chart_state.request)
    print('MARKET REQUEST CHANGED:', market_request_changed)
    print('CHART HISTORY PLAN:', {
        'recommended_seed_bars': settings['recommended_seed_bars'],
        'history_load_step': settings['history_load_step'],
    })

    return settings, market_request_changed


def extend_chart_history(extra_bars: int | None = None):
    chart_request = dict(state.chart.request or {})
    history_plan = calculate_chart_history_plan(chart_request.get('indicators') or [])
    load_step = max(1, int(history_plan['history_load_step']))
    requested_extra_bars = max(1, int(extra_bars or load_step))
    next_bars = max(
        max(1, int(chart_request.get('bars') or 1)) + requested_extra_bars,
        max(1, int(chart_request.get('bars') or 1)) + load_step,
    )

    settings, market_request_changed = apply_chart_settings(
        symbol=chart_request.get('symbol') or 'EURUSD',
        timeframe=chart_request.get('timeframe') or 'M1',
        bars=next_bars,
        indicators=chart_request.get('indicators') or [],
    )

    print('CHART HISTORY EXTENDED:', {
        'previous_bars': chart_request.get('bars'),
        'requested_extra_bars': requested_extra_bars,
        'next_bars': settings['bars'],
        'history_load_step': settings['history_load_step'],
    })

    return {
        'settings': settings,
        'market_request_changed': market_request_changed,
        'requested_extra_bars': requested_extra_bars,
        'next_bars': settings['bars'],
        'history_load_step': settings['history_load_step'],
    }


def wait_for_history(timeout_seconds: float = 10.0, poll_interval: float = 0.1):
    chart_request = dict(state.chart.request or {})
    context = wait_for_market_data(
        symbol=chart_request.get('symbol'),
        timeframe=chart_request.get('timeframe'),
        bars=chart_request.get('bars'),
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
        source='chart_wait',
    )
    return bool(context['ready'])


def build_status_payload():
    chart_state = state.chart
    context = get_market_snapshot(
        symbol=chart_state.request.get('symbol'),
        timeframe=chart_state.request.get('timeframe'),
        bars=chart_state.request.get('bars'),
    )

    return {
        'request': dict(chart_state.request),
        'ready': context['ready'],
        'loading': context['loading'],
        'error': context['error'],
        'meta': {
            'symbol': context['symbol'],
            'timeframe': context['timeframe'],
            'requested_bars': context['bars_requested'],
            'loaded_candles': context['bars_loaded'],
            'first_time': context['first_time'],
            'last_time': context['last_time'],
            'cache_key': context['cache_key'],
            'source': context['source'],
            'recommended_seed_bars': chart_state.request.get('recommended_seed_bars'),
            'history_load_step': chart_state.request.get('history_load_step'),
        },
        'snapshot': {
            'built_at': chart_state.snapshot_built_at,
            'error': chart_state.snapshot_error,
            'dirty_reason': chart_state.snapshot_dirty_reason,
            'affected_from_index': chart_state.snapshot_affected_from_index,
            'available_column_details': list(chart_state.snapshot_available_column_details),
        },
        'runtime': build_chart_runtime_payload(),
    }

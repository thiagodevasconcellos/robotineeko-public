import time

try:
    from ..app_state import state
except ImportError:
    from app_state import state


def build_market_cache_key(symbol: str, timeframe: str, bars: int):
    return f'{str(symbol or "").strip().upper()}|{str(timeframe or "").strip().upper()}|{max(1, int(bars or 1))}'


def _snapshot_loaded_bars(snapshot: dict | None):
    candles = list((snapshot or {}).get('candles') or [])
    try:
        declared_loaded = int((snapshot or {}).get('bars_loaded') or len(candles))
    except Exception:
        declared_loaded = len(candles)
    return min(max(0, declared_loaded), len(candles))


def _snapshot_satisfies_request(snapshot: dict | None, bars: int):
    safe_bars = max(1, int(bars or 1))
    candles = list((snapshot or {}).get('candles') or [])
    loaded_bars = _snapshot_loaded_bars(snapshot)
    return len(candles) >= safe_bars and loaded_bars >= safe_bars


def _snapshot_last_time(snapshot: dict | None):
    candles = list((snapshot or {}).get('candles') or [])
    if candles:
        return candles[-1].get('time')
    return (snapshot or {}).get('last_time')


def _freshest_known_last_time(symbol: str, timeframe: str):
    safe_symbol = str(symbol or '').strip().upper()
    safe_timeframe = str(timeframe or '').strip().upper()

    freshest = None

    for cache_payload in (state.market_data.cache_by_key or {}).values():
        snapshot = (cache_payload or {}).get('snapshot') or {}
        cache_symbol = str((cache_payload or {}).get('symbol') or snapshot.get('symbol') or '').strip().upper()
        cache_timeframe = str((cache_payload or {}).get('timeframe') or snapshot.get('timeframe') or '').strip().upper()
        if cache_symbol != safe_symbol or cache_timeframe != safe_timeframe:
            continue
        candidate_last_time = _snapshot_last_time(snapshot)
        if candidate_last_time is None:
            continue
        if freshest is None or candidate_last_time > freshest:
            freshest = candidate_last_time

    bridge_state = state.bridge
    history_meta = dict(bridge_state.history_meta or {})
    bridge_symbol = str(history_meta.get('symbol') or bridge_state.request.get('symbol') or '').strip().upper()
    bridge_timeframe = str(history_meta.get('timeframe') or bridge_state.request.get('timeframe') or '').strip().upper()
    if (
        bridge_state.history_ready
        and bridge_symbol == safe_symbol
        and bridge_timeframe == safe_timeframe
        and len(bridge_state.candles or []) > 0
    ):
        bridge_last_time = _snapshot_last_time({
            'last_time': history_meta.get('last_time'),
            'candles': list(bridge_state.candles or []),
        })
        if bridge_last_time is not None and (freshest is None or bridge_last_time > freshest):
            freshest = bridge_last_time

    return freshest


def get_market_cache_entry(symbol: str, timeframe: str, bars: int):
    cache_key = build_market_cache_key(symbol, timeframe, bars)
    return state.market_data.cache_by_key.get(cache_key), cache_key


def _build_ready_snapshot_context(
    *,
    source: str,
    cache_key: str,
    revision: int,
    error,
    symbol: str,
    timeframe: str,
    bars_requested: int,
    snapshot: dict,
):
    candles = list(snapshot.get('candles') or [])
    return {
        'source': source,
        'cache_key': cache_key,
        'revision': revision,
        'ready': True,
        'loading': False,
        'error': error,
        'symbol': snapshot.get('symbol') or symbol,
        'timeframe': snapshot.get('timeframe') or timeframe,
        'bars_requested': bars_requested,
        'bars_loaded': min(max(0, int(snapshot.get('bars_loaded') or len(candles))), len(candles)),
        'first_time': snapshot.get('first_time') if candles else None,
        'last_time': snapshot.get('last_time') if candles else None,
        'candles': candles,
        'diagnostics': _build_market_diagnostics(cache_key),
    }


def _slice_snapshot(snapshot: dict, bars: int, symbol: str, timeframe: str):
    candles = list((snapshot or {}).get('candles') or [])
    safe_bars = max(1, int(bars or 1))
    sliced_candles = candles[-safe_bars:] if len(candles) > safe_bars else candles
    return {
        'symbol': str((snapshot or {}).get('symbol') or symbol).strip().upper(),
        'timeframe': str((snapshot or {}).get('timeframe') or timeframe).strip().upper(),
        'bars_requested': safe_bars,
        'bars_loaded': len(sliced_candles),
        'first_time': sliced_candles[0]['time'] if sliced_candles else None,
        'last_time': sliced_candles[-1]['time'] if sliced_candles else None,
        'candles': sliced_candles,
    }


def _find_superset_cache_entry(symbol: str, timeframe: str, bars: int):
    safe_symbol = str(symbol or '').strip().upper()
    safe_timeframe = str(timeframe or '').strip().upper()
    safe_bars = max(1, int(bars or 1))

    best_match = None
    best_match_bars = None

    for cache_key, cache_payload in (state.market_data.cache_by_key or {}).items():
        snapshot = (cache_payload or {}).get('snapshot') or {}
        candles = list(snapshot.get('candles') or [])
        cache_symbol = str((cache_payload or {}).get('symbol') or snapshot.get('symbol') or '').strip().upper()
        cache_timeframe = str((cache_payload or {}).get('timeframe') or snapshot.get('timeframe') or '').strip().upper()
        cache_bars = max(0, int((cache_payload or {}).get('bars') or snapshot.get('bars_requested') or len(candles) or 0))

        if cache_symbol != safe_symbol or cache_timeframe != safe_timeframe:
            continue
        if cache_bars == safe_bars:
            continue
        if len(candles) < safe_bars or cache_bars < safe_bars:
            continue

        if best_match is None or cache_bars < best_match_bars:
            best_match = (cache_key, cache_payload, snapshot)
            best_match_bars = cache_bars

    return best_match


def _find_best_subset_snapshot(symbol: str, timeframe: str, bars: int):
    safe_symbol = str(symbol or '').strip().upper()
    safe_timeframe = str(timeframe or '').strip().upper()
    safe_bars = max(1, int(bars or 1))

    candidates = []

    for cache_key, cache_payload in (state.market_data.cache_by_key or {}).items():
        snapshot = (cache_payload or {}).get('snapshot') or {}
        candles = list(snapshot.get('candles') or [])
        cache_symbol = str((cache_payload or {}).get('symbol') or snapshot.get('symbol') or '').strip().upper()
        cache_timeframe = str((cache_payload or {}).get('timeframe') or snapshot.get('timeframe') or '').strip().upper()
        cache_requested_bars = max(0, int((cache_payload or {}).get('bars') or snapshot.get('bars_requested') or len(candles) or 0))
        loaded_bars = _snapshot_loaded_bars(snapshot)

        if cache_symbol != safe_symbol or cache_timeframe != safe_timeframe:
            continue
        if loaded_bars <= 0 or loaded_bars >= safe_bars:
            continue

        candidates.append({
            'source': 'cache_partial_fallback' if cache_requested_bars >= safe_bars else 'cache_subset_fallback',
            'cache_key': cache_key,
            'revision': (cache_payload or {}).get('revision') or state.market_data.revision,
            'bars_loaded': loaded_bars,
            'last_time': _snapshot_last_time(snapshot),
            'snapshot': snapshot,
            'priority': 2 if cache_requested_bars >= safe_bars else 1,
        })

    bridge_state = state.bridge
    history_meta = dict(bridge_state.history_meta or {})
    bridge_symbol = str(history_meta.get('symbol') or bridge_state.request.get('symbol') or '').strip().upper()
    bridge_timeframe = str(history_meta.get('timeframe') or bridge_state.request.get('timeframe') or '').strip().upper()
    bridge_loaded_bars = len(bridge_state.candles or [])
    bridge_requested_bars = max(0, int(history_meta.get('requested_bars') or bridge_state.request.get('bars') or 0))

    if (
        bridge_symbol == safe_symbol
        and bridge_timeframe == safe_timeframe
        and bridge_loaded_bars > 0
        and bridge_loaded_bars < safe_bars
    ):
        bridge_snapshot = {
            'symbol': bridge_symbol,
            'timeframe': bridge_timeframe,
            'bars_requested': bridge_requested_bars or bridge_loaded_bars,
            'bars_loaded': bridge_loaded_bars,
            'first_time': history_meta.get('first_time'),
            'last_time': history_meta.get('last_time'),
            'candles': list(bridge_state.candles or []),
        }
        candidates.append({
            'source': 'bridge_partial_fallback' if bridge_requested_bars >= safe_bars else 'bridge_subset_fallback',
            'cache_key': build_market_cache_key(safe_symbol, safe_timeframe, bridge_requested_bars or bridge_loaded_bars),
            'revision': state.bridge.revision,
            'bars_loaded': bridge_loaded_bars,
            'last_time': _snapshot_last_time(bridge_snapshot),
            'snapshot': bridge_snapshot,
            'priority': 4 if bridge_requested_bars >= safe_bars else 3,
        })

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda candidate: (
            float(candidate.get('last_time') or 0),
            int(candidate.get('bars_loaded') or 0),
            int(candidate.get('priority') or 0),
        ),
    )


def build_truncated_market_fallback(symbol: str, timeframe: str, bars: int, base_context: dict | None = None):
    safe_symbol = str(symbol or '').strip().upper()
    safe_timeframe = str(timeframe or '').strip().upper()
    safe_bars = max(1, int(bars or 1))
    fallback_candidate = _find_best_subset_snapshot(safe_symbol, safe_timeframe, safe_bars)
    if fallback_candidate is None:
        return None

    snapshot = _slice_snapshot(
        fallback_candidate.get('snapshot') or {},
        fallback_candidate.get('bars_loaded') or 0,
        safe_symbol,
        safe_timeframe,
    )
    effective_bars = len(snapshot.get('candles') or [])
    if effective_bars <= 0:
        return None

    safe_base_context = dict(base_context or {})
    diagnostics = dict(safe_base_context.get('diagnostics') or {})
    notice = (
        f'Requested {safe_bars:,} candles for {safe_symbol} {safe_timeframe}, '
        f'but only {effective_bars:,} are currently available. '
        'Using the maximum available history instead.'
    )

    return {
        'source': fallback_candidate.get('source') or 'subset_fallback',
        'cache_key': fallback_candidate.get('cache_key') or build_market_cache_key(safe_symbol, safe_timeframe, effective_bars),
        'revision': fallback_candidate.get('revision') or state.market_data.revision,
        'ready': True,
        'loading': False,
        'error': notice,
        'notice': notice,
        'truncated': True,
        'requested_bars_original': safe_bars,
        'bars_requested': effective_bars,
        'bars_loaded': effective_bars,
        'first_time': snapshot.get('first_time'),
        'last_time': snapshot.get('last_time'),
        'candles': list(snapshot.get('candles') or []),
        'request_id': safe_base_context.get('request_id'),
        'request_status': safe_base_context.get('request_status'),
        'symbol': safe_symbol,
        'timeframe': safe_timeframe,
        'diagnostics': diagnostics or _build_market_diagnostics(
            safe_base_context.get('cache_key') or build_market_cache_key(safe_symbol, safe_timeframe, safe_bars)
        ),
    }


def _build_market_diagnostics(cache_key: str, request_payload: dict | None = None):
    bridge_state = state.bridge
    now = time.time()
    heartbeat_at = bridge_state.ea_last_heartbeat_at
    heartbeat_age_seconds = max(0.0, now - float(heartbeat_at)) if heartbeat_at is not None else None
    request_started_at = (request_payload or {}).get('started_at')
    request_created_at = (request_payload or {}).get('created_at')
    request_finished_at = (request_payload or {}).get('finished_at')
    request_age_seconds = max(0.0, now - float(request_created_at)) if request_created_at is not None else None
    request_loading_age_seconds = max(0.0, now - float(request_started_at)) if request_started_at is not None else None
    request_finished_age_seconds = max(0.0, now - float(request_finished_at)) if request_finished_at is not None else None

    return {
        'cache_key': cache_key,
        'request_id': (request_payload or {}).get('request_id'),
        'request_status': (request_payload or {}).get('status'),
        'request_error': (request_payload or {}).get('error'),
        'request_age_seconds': round(request_age_seconds, 3) if request_age_seconds is not None else None,
        'request_loading_age_seconds': round(request_loading_age_seconds, 3) if request_loading_age_seconds is not None else None,
        'request_finished_age_seconds': round(request_finished_age_seconds, 3) if request_finished_age_seconds is not None else None,
        'bridge_active_request_id': bridge_state.active_request_id,
        'bridge_online': bool(heartbeat_at and heartbeat_age_seconds is not None and heartbeat_age_seconds <= max(1.0, float(bridge_state.ea_timeout_seconds or 8.0))),
        'bridge_stale': bool(heartbeat_at and heartbeat_age_seconds is not None and heartbeat_age_seconds > max(1.0, float(bridge_state.ea_timeout_seconds or 8.0))),
        'bridge_last_status': bridge_state.ea_last_status,
        'bridge_last_error': bridge_state.ea_last_error,
        'bridge_heartbeat_age_seconds': round(heartbeat_age_seconds, 3) if heartbeat_age_seconds is not None else None,
        'market_data_last_error': state.market_data.last_error,
        'queued_requests': len(state.market_data.pending_queue),
    }


def has_ready_market_snapshot(symbol: str, timeframe: str, bars: int):
    cache_payload, _cache_key = get_market_cache_entry(symbol, timeframe, bars)
    snapshot = (cache_payload or {}).get('snapshot') or {}
    if _snapshot_satisfies_request(snapshot, bars):
        return True

    superset_match = _find_superset_cache_entry(symbol, timeframe, bars)
    if superset_match:
        _superset_cache_key, _superset_payload, superset_snapshot = superset_match
        safe_symbol = str(symbol or '').strip().upper()
        safe_timeframe = str(timeframe or '').strip().upper()
        safe_bars = max(1, int(bars or 1))
        freshest_known_last_time = _freshest_known_last_time(safe_symbol, safe_timeframe)
        sliced_snapshot = _slice_snapshot(superset_snapshot, safe_bars, safe_symbol, safe_timeframe)
        superset_last_time = _snapshot_last_time(sliced_snapshot)
        if (
            freshest_known_last_time is None
            or superset_last_time is None
            or superset_last_time >= freshest_known_last_time
        ):
            return True

    bridge_state = state.bridge
    history_meta = dict(bridge_state.history_meta or {})
    bridge_symbol = str(history_meta.get('symbol') or bridge_state.request.get('symbol') or '').strip().upper()
    bridge_timeframe = str(history_meta.get('timeframe') or bridge_state.request.get('timeframe') or '').strip().upper()
    bridge_bars = max(0, int(history_meta.get('requested_bars') or bridge_state.request.get('bars') or 0))

    return bool(
        bridge_state.history_ready
        and bridge_symbol == str(symbol or '').strip().upper()
        and bridge_timeframe == str(timeframe or '').strip().upper()
        and bridge_bars >= max(1, int(bars or 1))
        and len(bridge_state.candles or []) >= max(1, int(bars or 1))
    )


def get_market_snapshot(symbol: str, timeframe: str, bars: int):
    safe_symbol = str(symbol or '').strip().upper()
    safe_timeframe = str(timeframe or '').strip().upper()
    safe_bars = max(1, int(bars or 1))
    freshest_known_last_time = _freshest_known_last_time(safe_symbol, safe_timeframe)

    cache_payload, cache_key = get_market_cache_entry(safe_symbol, safe_timeframe, safe_bars)
    snapshot = (cache_payload or {}).get('snapshot') or {}
    partial_snapshot = None
    partial_cache_payload = None
    ready_candidates = []

    if _snapshot_satisfies_request(snapshot, safe_bars):
        ready_candidates.append({
            'source': 'cache',
            'cache_key': cache_key,
            'revision': cache_payload.get('revision') or state.market_data.revision,
            'error': cache_payload.get('error'),
            'symbol': safe_symbol,
            'timeframe': safe_timeframe,
            'bars_requested': snapshot.get('bars_requested') or safe_bars,
            'snapshot': snapshot,
            'last_time': _snapshot_last_time(snapshot),
            'priority': 3,
        })
    elif snapshot.get('candles'):
        partial_snapshot = snapshot
        partial_cache_payload = cache_payload

    superset_match = _find_superset_cache_entry(safe_symbol, safe_timeframe, safe_bars)
    if superset_match is not None:
        superset_cache_key, superset_payload, superset_snapshot = superset_match
        sliced_snapshot = _slice_snapshot(superset_snapshot, safe_bars, safe_symbol, safe_timeframe)
        superset_last_time = _snapshot_last_time(sliced_snapshot)
        if (
            freshest_known_last_time is None
            or superset_last_time is None
            or superset_last_time >= freshest_known_last_time
        ):
            ready_candidates.append({
                'source': 'cache_superset',
                'cache_key': superset_cache_key,
                'revision': superset_payload.get('revision') or state.market_data.revision,
                'error': superset_payload.get('error'),
                'symbol': safe_symbol,
                'timeframe': safe_timeframe,
                'bars_requested': safe_bars,
                'snapshot': sliced_snapshot,
                'last_time': superset_last_time,
                'priority': 2,
            })

    bridge_state = state.bridge
    history_meta = dict(bridge_state.history_meta or {})
    bridge_symbol = str(history_meta.get('symbol') or bridge_state.request.get('symbol') or '').strip().upper()
    bridge_timeframe = str(history_meta.get('timeframe') or bridge_state.request.get('timeframe') or '').strip().upper()
    bridge_bars = max(1, int(history_meta.get('requested_bars') or bridge_state.request.get('bars') or 1))

    if (
        bridge_state.history_ready
        and bridge_symbol == safe_symbol
        and bridge_timeframe == safe_timeframe
        and bridge_bars >= safe_bars
        and len(bridge_state.candles or []) >= safe_bars
    ):
        bridge_snapshot = _slice_snapshot({
            'symbol': bridge_symbol,
            'timeframe': bridge_timeframe,
            'bars_requested': bridge_bars,
            'bars_loaded': len(bridge_state.candles or []),
            'first_time': history_meta.get('first_time'),
            'last_time': history_meta.get('last_time'),
            'candles': list(bridge_state.candles or []),
        }, safe_bars, safe_symbol, safe_timeframe)
        ready_candidates.append({
            'source': 'bridge' if bridge_bars == safe_bars else 'bridge_superset',
            'cache_key': cache_key,
            'revision': state.bridge.revision,
            'error': bridge_state.history_error,
            'symbol': safe_symbol,
            'timeframe': safe_timeframe,
            'bars_requested': safe_bars,
            'snapshot': bridge_snapshot,
            'last_time': _snapshot_last_time(bridge_snapshot),
            'priority': 1,
        })

    if ready_candidates:
        freshest_candidate = max(
            ready_candidates,
            key=lambda candidate: (
                float(candidate.get('last_time') or 0),
                int(candidate.get('priority') or 0),
            ),
        )
        return _build_ready_snapshot_context(
            source=freshest_candidate['source'],
            cache_key=freshest_candidate['cache_key'],
            revision=freshest_candidate['revision'],
            error=freshest_candidate['error'],
            symbol=freshest_candidate['symbol'],
            timeframe=freshest_candidate['timeframe'],
            bars_requested=freshest_candidate['bars_requested'],
            snapshot=freshest_candidate['snapshot'],
        )

    request_payload = {}
    for request_id in reversed(state.market_data.request_order):
        candidate = state.market_data.requests_by_id.get(request_id) or {}
        if candidate.get('cache_key') == cache_key:
            try:
                try:
                    from .. import bridge
                except ImportError:
                    import bridge
                request_payload = bridge.build_market_request_payload(request_id) or candidate
            except Exception:
                request_payload = candidate
            break

    if partial_snapshot is not None:
        partial_candles = list(partial_snapshot.get('candles') or [])
        partial_loaded_bars = _snapshot_loaded_bars(partial_snapshot)
        partial_error = str((partial_cache_payload or {}).get('error') or '').strip()
        if not partial_error:
            partial_error = (
                f'Market-data cache for {safe_symbol} {safe_timeframe} requested {safe_bars:,} bars, '
                f'but only {partial_loaded_bars:,} candles are currently available.'
            )
        return {
            'source': 'cache_partial',
            'cache_key': cache_key,
            'revision': (partial_cache_payload or {}).get('revision') or state.market_data.revision,
            'ready': False,
            'loading': bool(request_payload and request_payload.get('status') in {'queued', 'loading', 'waiting'}),
            'error': partial_error,
            'symbol': str(partial_snapshot.get('symbol') or safe_symbol).strip().upper(),
            'timeframe': str(partial_snapshot.get('timeframe') or safe_timeframe).strip().upper(),
            'bars_requested': safe_bars,
            'bars_loaded': partial_loaded_bars,
            'first_time': partial_snapshot.get('first_time') if partial_candles else None,
            'last_time': partial_snapshot.get('last_time') if partial_candles else None,
            'candles': partial_candles,
            'request_id': request_payload.get('request_id'),
            'request_status': request_payload.get('status'),
            'diagnostics': _build_market_diagnostics(cache_key, request_payload),
        }

    return {
        'source': 'missing',
        'cache_key': cache_key,
        'revision': state.market_data.revision,
        'ready': False,
        'loading': bool(request_payload and request_payload.get('status') in {'queued', 'loading', 'waiting'}),
        'error': request_payload.get('error'),
        'symbol': safe_symbol,
        'timeframe': safe_timeframe,
        'bars_requested': safe_bars,
        'bars_loaded': 0,
        'first_time': None,
        'last_time': None,
        'candles': [],
        'request_id': request_payload.get('request_id'),
        'request_status': request_payload.get('status'),
        'diagnostics': _build_market_diagnostics(cache_key, request_payload),
    }


def request_market_data(symbol: str, timeframe: str, bars: int, source: str = 'api'):
    try:
        try:
            from .. import bridge
        except ImportError:
            import bridge
        return bridge.sync_market_data_request(symbol=symbol, timeframe=timeframe, bars=bars, source=source)
    except Exception:
        return None


def ensure_market_data(symbol: str, timeframe: str, bars: int, source: str = 'api'):
    context = get_market_snapshot(symbol, timeframe, bars)
    if context['ready']:
        return context

    if str(context.get('request_status') or '').strip() == 'completed' and not has_ready_market_snapshot(symbol, timeframe, bars):
        request_market_data(symbol, timeframe, bars, source=source)
        return get_market_snapshot(symbol, timeframe, bars)

    request_market_data(symbol, timeframe, bars, source=source)
    return get_market_snapshot(symbol, timeframe, bars)


def wait_for_market_data(
    symbol: str,
    timeframe: str,
    bars: int,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.1,
    source: str = 'api',
    should_cancel=None,
    allow_truncated_fallback: bool = False,
):
    start_time = time.time()
    context = ensure_market_data(symbol, timeframe, bars, source=source)
    repaired_completed_request = False
    extended_loading_grace = False

    while time.time() - start_time < timeout_seconds:
        if callable(should_cancel) and should_cancel():
            raise RuntimeError('Research job cancelled by user.')
        if context['ready']:
            return context
        if allow_truncated_fallback and str(context.get('request_status') or '').strip().lower() in {'completed', 'error', 'cancelled'}:
            fallback_context = build_truncated_market_fallback(symbol, timeframe, bars, base_context=context)
            if fallback_context is not None:
                return fallback_context
        if (
            not repaired_completed_request
            and str(context.get('request_status') or '').strip() == 'completed'
            and not has_ready_market_snapshot(symbol, timeframe, bars)
        ):
            request_market_data(symbol, timeframe, bars, source=source)
            repaired_completed_request = True
        time.sleep(poll_interval)
        context = get_market_snapshot(symbol, timeframe, bars)

    # One last reconciliation pass: the cache may have become ready right as the
    # timeout window closed, especially for large history uploads.
    context = ensure_market_data(symbol, timeframe, bars, source=source)
    if context['ready']:
        return context
    if allow_truncated_fallback:
        fallback_context = build_truncated_market_fallback(symbol, timeframe, bars, base_context=context)
        if fallback_context is not None:
            return fallback_context

    diagnostics = dict(context.get('diagnostics') or {})
    request_status = str(context.get('request_status') or '').strip().lower()
    request_age_seconds = diagnostics.get('request_age_seconds')
    bridge_heartbeat_age_seconds = diagnostics.get('bridge_heartbeat_age_seconds')

    # If the request is still genuinely loading, give it one bounded grace
    # window instead of failing right at the nominal timeout.
    if (
        not extended_loading_grace
        and request_status in {'queued', 'waiting', 'loading'}
        and request_age_seconds is not None
        and float(request_age_seconds) <= max(timeout_seconds * 2.0, timeout_seconds + 60.0)
    ):
        extended_loading_grace = True
        grace_timeout_seconds = max(15.0, min(timeout_seconds, 120.0))
        grace_deadline = time.time() + grace_timeout_seconds

        while time.time() < grace_deadline:
            if callable(should_cancel) and should_cancel():
                raise RuntimeError('Research job cancelled by user.')

            # Reaffirm the request if the bridge looked stale during the first window.
            if bridge_heartbeat_age_seconds is not None and bridge_heartbeat_age_seconds > max(1.0, 2.0 * poll_interval):
                request_market_data(symbol, timeframe, bars, source=source)

            time.sleep(poll_interval)
            context = get_market_snapshot(symbol, timeframe, bars)
            if context['ready']:
                return context

            diagnostics = dict(context.get('diagnostics') or {})
            bridge_heartbeat_age_seconds = diagnostics.get('bridge_heartbeat_age_seconds')

    if allow_truncated_fallback:
        fallback_context = build_truncated_market_fallback(symbol, timeframe, bars, base_context=context)
        if fallback_context is not None:
            return fallback_context

    return context

import asyncio
import inspect
import os
import threading
import time

try:
    from ..app_state import state
    from ..strategy_backend import (
        ApplyStrategyRequest,
        PresetCompareRequest,
        evaluate_strategy_request_in_context,
        execute_preset_compare_request,
        summarize_comparison_stats,
    )
    from .realtime_sync import realtime_sync
    from .workspace_service import build_workspace_channel_key
    from .workspace_store import (
        create_workspace_research_batch,
        create_workspace_research_campaign,
        create_workspace_research_job,
        create_workspace_research_run,
        append_workspace_system_log_entries,
        get_workspace_research_batch,
        get_workspace_research_campaign,
        get_workspace_research_job,
        list_workspace_research_campaigns,
        list_workspace_research_batches,
        list_workspace_research_jobs,
        touch_workspace_research_batch,
        touch_workspace_research_job,
        delete_workspace_research_campaign,
        update_workspace_research_campaign,
        update_workspace_research_batch,
        update_workspace_research_job,
    )
except ImportError:
    from app_state import state
    from strategy_backend import (
        ApplyStrategyRequest,
        PresetCompareRequest,
        evaluate_strategy_request_in_context,
        execute_preset_compare_request,
        summarize_comparison_stats,
    )
    from services.realtime_sync import realtime_sync
    from services.workspace_service import build_workspace_channel_key
    from services.workspace_store import (
        create_workspace_research_batch,
        create_workspace_research_campaign,
        create_workspace_research_job,
        create_workspace_research_run,
        append_workspace_system_log_entries,
        get_workspace_research_batch,
        get_workspace_research_campaign,
        get_workspace_research_job,
        list_workspace_research_campaigns,
        list_workspace_research_batches,
        list_workspace_research_jobs,
        touch_workspace_research_batch,
        touch_workspace_research_job,
        delete_workspace_research_campaign,
        update_workspace_research_campaign,
        update_workspace_research_batch,
        update_workspace_research_job,
    )


RESEARCH_RUNTIME_HEARTBEAT_INTERVAL_SECONDS = 2.5
RESEARCH_RUNTIME_RECEIVING_WINDOW_SECONDS = 6.0
RESEARCH_RUNTIME_WAITING_WINDOW_SECONDS = 20.0
RESEARCH_RUNTIME_STARTUP_GRACE_SECONDS = 12.0
RESEARCH_RUNTIME_CANCELLED_ZOMBIE_SANITIZE_SECONDS = 15.0
RESEARCH_RECONCILE_SCAN_LIMIT = 500


def _strip_series_from_stats(stats: dict | None):
    if not isinstance(stats, dict):
        return {}

    return {
        key: value
        for key, value in stats.items()
        if not (str(key).endswith('_series') or str(key).endswith('_curve'))
    }


def _compact_chart_context(chart: dict | None):
    safe_chart = dict(chart or {})
    return {
        'symbol': str(safe_chart.get('symbol') or '').strip().upper(),
        'timeframe': str(safe_chart.get('timeframe') or '').strip().upper(),
        'bars': max(1, int(safe_chart.get('bars') or 1)),
        'indicators': list(safe_chart.get('indicators') or []),
    }


def _merge_indicator_payloads(*indicator_groups):
    merged = []
    seen = {}

    for group in indicator_groups:
        for indicator in list(group or []):
            if not isinstance(indicator, dict):
                continue
            name = str(indicator.get('name') or '').strip()
            params = list(indicator.get('params') or [])
            alias = str(indicator.get('alias') or '').strip()
            if not name:
                continue
            key = (name.upper(), tuple(params))
            existing_index = seen.get(key)
            if existing_index is not None:
                if alias and not str(merged[existing_index].get('alias') or '').strip():
                    merged[existing_index]['alias'] = alias
                continue
            seen[key] = len(merged)
            merged.append({
                'name': name,
                'params': params,
                'alias': alias,
            })

    return merged


def _compact_strategy_request_payload(request_payload: dict | None):
    safe_request = dict(request_payload or {})
    chart = _compact_chart_context(safe_request.get('chart') or {})
    return {
        'id': str(safe_request.get('id') or '').strip(),
        'label': str(safe_request.get('label') or '').strip(),
        'notes': str(safe_request.get('notes') or '').strip(),
        'chart': chart,
        'strategy': safe_request.get('strategy') or {},
        'strategies': list(safe_request.get('strategies') or []),
        'backtest': safe_request.get('backtest') or {},
        'researchPlan': {
            'kind': str(((safe_request.get('researchPlan') or {}).get('kind') or 'none')).strip().lower() or 'none',
        },
    }

def _compact_preset_compare_request_payload(request_payload: dict | None):
    safe_request = dict(request_payload or {})
    chart_context = _compact_chart_context(safe_request.get('chartContext') or {})
    presets = list(safe_request.get('presets') or [])
    baseline = dict(safe_request.get('baseline') or {})

    def compact_compare_entry(entry_payload: dict | None):
        safe_entry = dict(entry_payload or {})
        strategies = []
        enabled_strategy_count = 1

        for item in list(safe_entry.get('strategies') or []):
            if not isinstance(item, dict):
                continue
            enabled = bool(item.get('enabled', True))
            if enabled:
                enabled_strategy_count += 1
            strategies.append({
                'id': str(item.get('id') or '').strip(),
                'label': str(item.get('label') or '').strip(),
                'priority': int(item.get('priority') or 0),
                'enabled': enabled,
            })

        return {
            'id': str(safe_entry.get('id') or '').strip(),
            'label': str(safe_entry.get('label') or '').strip(),
            'strategy_count': enabled_strategy_count,
            'has_auxiliary_strategies': bool(strategies),
            'strategies': strategies[:20],
        }

    return {
        'baseline': compact_compare_entry(baseline) if baseline else None,
        'preset_count': len(presets),
        'portfolio_preset_count': sum(1 for preset in presets if list((preset or {}).get('strategies') or [])),
        'preset_labels': [
            str((preset or {}).get('label') or '').strip()
            for preset in presets[:20]
            if str((preset or {}).get('label') or '').strip()
        ],
        'presets': [
            compact_compare_entry(preset)
            for preset in presets[:20]
            if isinstance(preset, dict)
        ],
        'backtest': safe_request.get('backtest') or {},
        'chartContext': chart_context,
        'studyWindows': list(safe_request.get('studyWindows') or []),
        'studyTimeframes': list(safe_request.get('studyTimeframes') or []),
        'studySymbols': list(safe_request.get('studySymbols') or []),
        'walkforwardWindowBars': safe_request.get('walkforwardWindowBars'),
        'walkforwardTrainBars': safe_request.get('walkforwardTrainBars'),
        'walkforwardTestBars': safe_request.get('walkforwardTestBars'),
        'walkforwardStepBars': safe_request.get('walkforwardStepBars'),
    }


def _compact_compare_summary(result: dict | None):
    if not isinstance(result, dict):
        return None

    comparisons = []
    for item in list(result.get('comparisons') or []):
        if not isinstance(item, dict):
            continue
        comparisons.append({
            'id': str(item.get('id') or '').strip(),
            'label': str(item.get('label') or '').strip(),
            'summary': item.get('summary') or {},
            'strategy_count': int(item.get('strategy_count') or 1),
            'portfolio_event_counts': item.get('portfolio_event_counts') or {},
            'portfolio_strategy_stats': list(item.get('portfolio_strategy_stats') or []),
            'portfolio_analytics': item.get('portfolio_analytics') or {},
            'consistency': item.get('consistency') or {},
            'train_test_consistency': item.get('train_test_consistency') or {},
        })

    return {
        'status': str(result.get('status') or '').strip() or 'ok',
        'best_preset_id': result.get('best_preset_id'),
        'baseline': {
            'id': str(((result.get('baseline') or {}).get('id') or '')).strip(),
            'label': str(((result.get('baseline') or {}).get('label') or '')).strip(),
            'summary': ((result.get('baseline') or {}).get('summary') or {}),
            'strategy_count': int(((result.get('baseline') or {}).get('strategy_count') or 1)),
            'portfolio_event_counts': (((result.get('baseline') or {}).get('portfolio_event_counts') or {})),
            'portfolio_strategy_stats': list(((result.get('baseline') or {}).get('portfolio_strategy_stats') or [])),
            'portfolio_analytics': (((result.get('baseline') or {}).get('portfolio_analytics') or {})),
        } if isinstance(result.get('baseline'), dict) else None,
        'comparisons': comparisons,
        'study': {
            'best_preset_id': ((result.get('study') or {}).get('best_preset_id')),
            'window_count': len(((result.get('study') or {}).get('windows') or [])),
        } if isinstance(result.get('study'), dict) else None,
        'timeframe_study': {
            'best_preset_id': ((result.get('timeframe_study') or {}).get('best_preset_id')),
            'timeframe_count': len(((result.get('timeframe_study') or {}).get('timeframes') or [])),
        } if isinstance(result.get('timeframe_study'), dict) else None,
        'symbol_study': {
            'best_preset_id': ((result.get('symbol_study') or {}).get('best_preset_id')),
            'symbol_count': len(((result.get('symbol_study') or {}).get('symbols') or [])),
        } if isinstance(result.get('symbol_study'), dict) else None,
        'walkforward_study': {
            'best_preset_id': ((result.get('walkforward_study') or {}).get('best_preset_id')),
            'pair_count': len(((result.get('walkforward_study') or {}).get('pairs') or [])),
        } if isinstance(result.get('walkforward_study'), dict) else None,
    }


def _compact_pipeline_result(result_payload: dict | None):
    safe_result = dict(result_payload or {})
    pipeline = dict(safe_result.get('pipeline') or {})
    pipeline_request = dict(pipeline.get('request') or {})

    compact_pipeline = {
        'label': str(pipeline.get('label') or '').strip(),
        'chart': _compact_chart_context(pipeline.get('chart') or {}),
        'request': {
            'strategy': pipeline_request.get('strategy') or {},
            'strategies': list(pipeline_request.get('strategies') or []),
            'backtest': pipeline_request.get('backtest') or {},
        },
        'stats': _strip_series_from_stats(pipeline.get('stats') or {}),
        'results': [],
        'strategy_view_meta': pipeline.get('strategy_view_meta') or {},
        'applied_indicators': list(pipeline.get('applied_indicators') or []),
        'available_columns': list(pipeline.get('available_columns') or []),
        'available_column_details': [],
        'trade_markers': [],
    }

    compact_payload = {
        'status': str(safe_result.get('status') or '').strip() or 'ok',
        'job_type': str(safe_result.get('job_type') or 'strategy_pipeline').strip(),
        'pipeline': compact_pipeline,
        'research': _compact_compare_summary(safe_result.get('research')),
    }
    return compact_payload


def _compact_research_job_request(job_type: str, request_payload: dict | None):
    safe_type = str(job_type or '').strip().lower()
    if safe_type == 'strategy_pipeline':
        return _compact_strategy_request_payload(request_payload)
    if safe_type == 'preset_compare':
        return _compact_preset_compare_request_payload(request_payload)
    return request_payload or {}


def _compact_research_job_result(job_type: str, result_payload):
    safe_type = str(job_type or '').strip().lower()
    if safe_type == 'strategy_pipeline':
        return _compact_pipeline_result(result_payload)
    if safe_type == 'preset_compare':
        return _compact_compare_summary(result_payload)
    return result_payload


def _compact_research_batch_request(request_payload: dict | None):
    safe_request = dict(request_payload or {})
    jobs = []
    for item in list(safe_request.get('jobs') or []):
        if not isinstance(item, dict):
            continue
        request = item.get('request') or {}
        chart = request.get('chart') or {}
        jobs.append({
            'job_type': str(item.get('job_type') or '').strip(),
            'id': str(request.get('id') or '').strip(),
            'label': str(item.get('run_label') or request.get('label') or '').strip(),
            'symbol': str(chart.get('symbol') or '').strip().upper(),
            'timeframe': str(chart.get('timeframe') or '').strip().upper(),
            'bars': max(1, int(chart.get('bars') or 1)),
        })
    return {'jobs': jobs}


def _compact_research_batch_result(result_payload: dict | None):
    safe_result = dict(result_payload or {})
    jobs = []
    for item in list(safe_result.get('jobs') or []):
        if not isinstance(item, dict):
            continue
        raw_result = item.get('result')
        inferred_job_type = 'strategy_pipeline' if isinstance(raw_result, dict) and raw_result.get('pipeline') is not None else 'preset_compare'
        compact_result = _compact_research_job_result(inferred_job_type, raw_result)
        pipeline = ((compact_result or {}).get('pipeline') or {}) if isinstance(compact_result, dict) else {}
        jobs.append({
            'job_id': item.get('job_id'),
            'run_label': str(item.get('run_label') or '').strip(),
            'status': str(item.get('status') or '').strip(),
            'run_id': item.get('run_id'),
            'benchmark_id': item.get('benchmark_id'),
            'benchmark_label': str(item.get('benchmark_label') or '').strip(),
            'detail': str(item.get('detail') or '').strip(),
            'error': str(item.get('error') or '').strip(),
            'result': {
                'status': (compact_result or {}).get('status') if isinstance(compact_result, dict) else None,
                'pipeline': {
                    'label': str(pipeline.get('label') or '').strip(),
                    'chart': pipeline.get('chart') or {},
                    'stats': pipeline.get('stats') or {},
                } if pipeline else None,
                'research': (compact_result or {}).get('research') if isinstance(compact_result, dict) else None,
            },
        })
    return {'jobs': jobs}


def _build_job_key(user_id: str, workspace_id: str, job_id: int):
    return f'{user_id}:{workspace_id}:{int(job_id)}'


def _build_batch_key(user_id: str, workspace_id: str, batch_id: int):
    return f'{user_id}:{workspace_id}:batch:{int(batch_id)}'


def _set_active_batch(user_id: str, workspace_id: str, batch: dict):
    key = _build_batch_key(user_id, workspace_id, int(batch['id']))
    state.research.active_batches[key] = dict(batch or {})
    return key


def _refresh_active_batch(user_id: str, workspace_id: str, batch_id: int):
    batch = get_workspace_research_batch(user_id, workspace_id, batch_id)
    if not batch:
        return None
    _set_active_batch(user_id, workspace_id, batch)
    return batch


def _list_reconcile_jobs_snapshot(user_id: str, workspace_id: str):
    return list_workspace_research_jobs(
        user_id,
        workspace_id,
        limit=RESEARCH_RECONCILE_SCAN_LIMIT,
        include_payload=False,
    )


def _list_reconcile_batches_snapshot(user_id: str, workspace_id: str):
    return list_workspace_research_batches(
        user_id,
        workspace_id,
        limit=RESEARCH_RECONCILE_SCAN_LIMIT,
        include_payload=False,
    )


def _build_active_batch_job_index(batches: list[dict] | None = None):
    index: dict[int, dict] = {}

    for batch in list(batches or []):
        status = str((batch or {}).get('status') or '').strip().lower()
        if status not in {'queued', 'running'}:
            continue
        try:
            current_job_id = int((batch or {}).get('current_job_id'))
        except Exception:
            continue
        index[current_job_id] = batch

    return index


def _build_live_batch_worker_index(user_id: str, workspace_id: str, active_batch_jobs: dict[int, dict] | None = None):
    worker_by_job_id = {}
    key_prefix = f'{user_id}:{workspace_id}:batch:'

    for key, payload in list((state.research.active_batches or {}).items()):
        if not str(key or '').startswith(key_prefix):
            continue
        try:
            current_job_id = int((payload or {}).get('current_job_id'))
        except Exception:
            continue
        worker = state.research.job_threads.get(key)
        if worker is not None and worker.is_alive():
            worker_by_job_id[current_job_id] = worker

    for current_job_id, batch in dict(active_batch_jobs or {}).items():
        if current_job_id in worker_by_job_id:
            continue
        try:
            batch_key = _build_batch_key(user_id, workspace_id, int((batch or {}).get('id')))
        except Exception:
            continue
        worker = state.research.job_threads.get(batch_key)
        if worker is not None and worker.is_alive():
            worker_by_job_id[current_job_id] = worker

    return worker_by_job_id


def _is_job_owned_by_active_batch(user_id: str, workspace_id: str, job_id: int, active_batch_jobs: dict[int, dict] | None = None):
    try:
        safe_job_id = int(job_id)
    except Exception:
        return False

    if active_batch_jobs is not None:
        return safe_job_id in active_batch_jobs

    batches = _list_reconcile_batches_snapshot(user_id, workspace_id)
    return safe_job_id in _build_active_batch_job_index(batches)


def _find_live_batch_worker_for_job(
    user_id: str,
    workspace_id: str,
    job_id: int,
    *,
    active_batch_jobs: dict[int, dict] | None = None,
    live_batch_workers: dict[int, object] | None = None,
):
    try:
        safe_job_id = int(job_id)
    except Exception:
        return None

    if live_batch_workers is not None:
        return live_batch_workers.get(safe_job_id)

    safe_active_batch_jobs = active_batch_jobs
    if safe_active_batch_jobs is None:
        safe_active_batch_jobs = _build_active_batch_job_index(_list_reconcile_batches_snapshot(user_id, workspace_id))

    return _build_live_batch_worker_index(user_id, workspace_id, safe_active_batch_jobs).get(safe_job_id)


def _clear_research_runtime_key(key: str, *, batch: bool = False):
    _stop_research_runtime_heartbeat(key)
    state.research.job_threads.pop(key, None)
    if batch:
        state.research.active_batches.pop(key, None)
    else:
        state.research.active_jobs.pop(key, None)


def _touch_research_runtime_heartbeat(key: str, *, batch: bool = False, **updates):
    collection = state.research.active_batches if batch else state.research.active_jobs
    payload = dict(collection.get(key) or {})
    payload['heartbeat_at'] = time.time()
    for key_name, key_value in (updates or {}).items():
        payload[key_name] = key_value
    collection[key] = payload
    return payload


def _start_research_runtime_heartbeat(key: str, *, batch: bool = False, persist_callback=None):
    stop_event = threading.Event()

    def loop():
        while not stop_event.wait(RESEARCH_RUNTIME_HEARTBEAT_INTERVAL_SECONDS):
            try:
                touched_at = time.time()
                _touch_research_runtime_heartbeat(key, batch=batch, heartbeat_at=touched_at)
                if callable(persist_callback):
                    persist_callback(touched_at)
            except Exception:
                pass

    thread = threading.Thread(target=loop, name=f'research-heartbeat-{key}', daemon=True)
    thread.start()
    state.research.runtime_heartbeat_threads[key] = (stop_event, thread)
    return stop_event, thread


def _stop_research_runtime_heartbeat(key: str):
    holder = state.research.runtime_heartbeat_threads.pop(key, None)
    if not holder:
        return
    stop_event, thread = holder
    try:
        stop_event.set()
    except Exception:
        pass
    try:
        thread.join(timeout=1.0)
    except Exception:
        pass


def _derive_research_feed_state(active_payload: dict | None, worker_alive: bool):
    payload = dict(active_payload or {})
    status = str(payload.get('status') or '').strip().lower()
    phase = str(payload.get('phase') or '').strip().lower()
    now = time.time()
    updated_at = payload.get('updated_at')
    heartbeat_at = payload.get('heartbeat_at')
    started_at = payload.get('started_at')
    update_age_seconds = max(0.0, now - float(updated_at)) if updated_at is not None else None
    heartbeat_age_seconds = max(0.0, now - float(heartbeat_at)) if heartbeat_at is not None else None
    runtime_age_seconds = max(0.0, now - float(started_at)) if started_at is not None else None

    feed_status = 'idle'
    feed_label = 'Idle'
    feed_detail = ''
    auto_sanitize_recommended = False

    if status in {'queued', 'running'}:
        in_startup_grace = (
            runtime_age_seconds is not None
            and runtime_age_seconds <= RESEARCH_RUNTIME_STARTUP_GRACE_SECONDS
            and phase in {'queued', 'starting', ''}
        )
        if in_startup_grace:
            feed_status = 'waiting'
            feed_label = 'Waiting for worker'
            feed_detail = 'Research runtime is still starting.'
        elif update_age_seconds is not None and update_age_seconds <= RESEARCH_RUNTIME_RECEIVING_WINDOW_SECONDS:
            feed_status = 'receiving'
            feed_label = 'Receiving updates'
            feed_detail = 'Research worker is actively producing updates.'
        elif heartbeat_age_seconds is not None and heartbeat_age_seconds <= RESEARCH_RUNTIME_WAITING_WINDOW_SECONDS:
            feed_status = 'waiting'
            feed_label = 'Waiting for data'
            feed_detail = 'Research worker heartbeat is healthy, but no new progress update arrived yet.'
        else:
            feed_status = 'stale'
            feed_label = 'Worker stale'
            feed_detail = 'Research worker updates look stale or stopped.'
            auto_sanitize_recommended = (
                not worker_alive
                or (
                    payload.get('cancel_requested')
                    and heartbeat_age_seconds is not None
                    and heartbeat_age_seconds >= RESEARCH_RUNTIME_CANCELLED_ZOMBIE_SANITIZE_SECONDS
                )
            )
    elif status in {'completed', 'failed', 'cancelled'}:
        feed_status = 'finished'
        feed_label = 'Finished'
        feed_detail = 'Research runtime is no longer active.'

    return {
        'data_feed_status': feed_status,
        'data_feed_label': feed_label,
        'data_feed_detail': feed_detail,
        'update_age_seconds': update_age_seconds,
        'heartbeat_age_seconds': heartbeat_age_seconds,
        'runtime_age_seconds': runtime_age_seconds,
        'worker_alive': bool(worker_alive),
        'auto_sanitize_recommended': bool(auto_sanitize_recommended),
    }


def _is_runtime_startup_pending(entity: dict | None):
    if not isinstance(entity, dict):
        return False

    status = str(entity.get('status') or '').strip().lower()
    if status not in {'queued', 'running'}:
        return False

    phase = str(entity.get('phase') or '').strip().lower()
    if phase not in {'', 'queued', 'starting'}:
        return False

    now = time.time()
    reference_at = entity.get('updated_at')
    if reference_at is None:
        reference_at = entity.get('created_at')
    if reference_at is None:
        return False

    try:
        age_seconds = max(0.0, now - float(reference_at))
    except Exception:
        return False

    return age_seconds <= RESEARCH_RUNTIME_STARTUP_GRACE_SECONDS


def _enrich_runtime_entity(entity: dict | None, *, key: str, batch: bool = False):
    if not isinstance(entity, dict):
        return entity
    collection = state.research.active_batches if batch else state.research.active_jobs
    runtime_payload = dict(collection.get(key) or {})
    worker_ref = state.research.job_threads.get(key)
    worker_alive = bool(worker_ref and getattr(worker_ref, 'is_alive', lambda: False)())
    merged = {
        **entity,
        **runtime_payload,
    }
    merged.update(_derive_research_feed_state(merged, worker_alive))
    return merged


def _sanitize_stale_research_job(user_id: str, workspace_id: str, job: dict, key: str, now: float):
    cancelled = bool(job.get('cancel_requested'))
    detail = (
        'Research job was cancelled after the runtime became stale.'
        if cancelled
        else 'Research job runtime became stale and was auto-sanitized.'
    )
    error = '' if cancelled else (
        str(job.get('error') or '').strip()
        or 'Research worker stopped sending healthy runtime signals.'
    )
    _record_reconcile_system_log(
        user_id,
        workspace_id,
        level='error' if not cancelled else 'warning',
        message='Research runtime reconciler auto-sanitized a stale job.',
        context={
            'entity': 'job',
            'job_id': int(job['id']),
            'cancel_requested': cancelled,
            'data_feed_status': job.get('data_feed_status'),
            'data_feed_detail': job.get('data_feed_detail'),
            'worker_alive': bool(job.get('worker_alive')),
            'heartbeat_age_seconds': job.get('heartbeat_age_seconds'),
            'update_age_seconds': job.get('update_age_seconds'),
        },
    )
    updated = _update_job(
        user_id,
        workspace_id,
        int(job['id']),
        status='cancelled' if cancelled else 'failed',
        progress=0.0 if cancelled else 1.0,
        phase='cancelled' if cancelled else 'failed',
        phase_label='Cancelled' if cancelled else 'Failed',
        detail=detail,
        error=error,
        finished_at=now,
    )
    _clear_research_runtime_key(key, batch=False)
    return updated


def _sanitize_stale_research_batch(
    user_id: str,
    workspace_id: str,
    batch: dict,
    key: str,
    now: float,
    *,
    current_job: dict | None = None,
):
    cancelled = bool(batch.get('cancel_requested'))
    current_job_id = batch.get('current_job_id')
    if current_job is None and current_job_id is not None:
        try:
            current_job = get_workspace_research_job(user_id, workspace_id, int(current_job_id))
        except Exception:
            current_job = None

    summary = _derive_batch_summary_from_known_jobs(batch, current_job=current_job)
    completed_jobs = int(summary.get('completed_jobs') or 0)
    failed_jobs = int(summary.get('failed_jobs') or 0)
    cancelled_jobs = int(summary.get('cancelled_jobs') or 0)
    last_error = str(summary.get('last_error') or '').strip()
    detail = (
        'Batch was cancelled after the runtime became stale.'
        if cancelled
        else 'Batch runtime became stale and was auto-sanitized.'
    )
    error = '' if cancelled else (
        str((current_job or {}).get('error') or '').strip()
        or last_error
        or str(batch.get('error') or '').strip()
        or 'Batch worker stopped sending healthy runtime signals.'
    )
    _record_reconcile_system_log(
        user_id,
        workspace_id,
        level='error' if not cancelled else 'warning',
        message='Research runtime reconciler auto-sanitized a stale batch.',
        context={
            'entity': 'batch',
            'batch_id': int(batch['id']),
            'current_job_id': current_job_id,
            'cancel_requested': cancelled,
            'data_feed_status': batch.get('data_feed_status'),
            'data_feed_detail': batch.get('data_feed_detail'),
            'worker_alive': bool(batch.get('worker_alive')),
            'heartbeat_age_seconds': batch.get('heartbeat_age_seconds'),
            'update_age_seconds': batch.get('update_age_seconds'),
        },
    )
    updated = _update_batch(
        user_id,
        workspace_id,
        int(batch['id']),
        status='cancelled' if cancelled else 'failed',
        progress=0.0 if cancelled else 1.0,
        phase='cancelled' if cancelled else 'failed',
        phase_label='Cancelled' if cancelled else 'Failed',
        detail=detail,
        error=error,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        cancelled_jobs=cancelled_jobs,
        result={'jobs': summary.get('jobs') or []},
        finished_at=now,
    )
    _clear_research_runtime_key(key, batch=True)
    return updated


def _record_research_event(kind: str, *, user_id: str, workspace_id: str, job_id: int, status: str, detail: str | None = None):
    state.research.recent_events = [
        {
            'kind': str(kind or '').strip() or 'job',
            'user_id': user_id,
            'workspace_id': workspace_id,
            'job_id': int(job_id),
            'status': str(status or '').strip(),
            'detail': str(detail or '').strip(),
            'at': time.time(),
        },
        *list(state.research.recent_events or []),
    ][:50]


def _collect_reconcile_callers():
    callers = []
    for frame_info in inspect.stack()[2:8]:
        function_name = str(frame_info.function or '').strip()
        if function_name in {'_reconcile_stale_research_runtime', 'reconcile_research_runtime'}:
            continue
        filename = os.path.basename(str(frame_info.filename or ''))
        callers.append(f'{filename}:{frame_info.lineno}:{function_name}')
    return callers


def _record_reconcile_system_log(
    user_id: str,
    workspace_id: str,
    *,
    level: str,
    message: str,
    context: dict | None = None,
):
    try:
        append_workspace_system_log_entries(
            user_id,
            workspace_id,
            entries=[{
                'level': level,
                'source': 'research_runtime',
                'scope': 'backend',
                'category': 'research_reconcile',
                'message': message,
                'context': {
                    'process_id': os.getpid(),
                    'callers': _collect_reconcile_callers(),
                    **(context or {}),
                },
                'created_at': time.time(),
            }],
            source='research_runtime',
            metadata={'reason': 'research_reconcile'},
        )
    except Exception:
        pass


def _normalize_batch_result_jobs(batch: dict | None):
    jobs = []
    seen_job_ids = set()

    for item in list(((batch or {}).get('result') or {}).get('jobs') or []):
        if not isinstance(item, dict):
            continue
        try:
            job_id = int(item.get('job_id'))
        except Exception:
            job_id = None
        if job_id is not None and job_id in seen_job_ids:
            continue
        if job_id is not None:
            seen_job_ids.add(job_id)
        jobs.append({
            'job_id': job_id,
            'status': str(item.get('status') or '').strip().lower(),
            'detail': str(item.get('detail') or '').strip(),
            'error': str(item.get('error') or '').strip(),
            'run_id': item.get('run_id'),
            'run_label': str(item.get('run_label') or '').strip(),
            'result': item.get('result'),
        })

    return jobs, seen_job_ids


def _derive_batch_summary_from_known_jobs(batch: dict | None, current_job: dict | None = None):
    known_jobs, seen_job_ids = _normalize_batch_result_jobs(batch)

    if current_job:
        try:
            current_job_id = int(current_job.get('id'))
        except Exception:
            current_job_id = None
        if current_job_id is None or current_job_id not in seen_job_ids:
            known_jobs.append({
                'job_id': current_job_id,
                'status': str(current_job.get('status') or '').strip().lower(),
                'detail': str(current_job.get('detail') or '').strip(),
                'error': str(current_job.get('error') or '').strip(),
                'run_id': current_job.get('run_id'),
                'run_label': str(current_job.get('run_label') or '').strip(),
                'result': current_job.get('result'),
            })

    completed_jobs = 0
    failed_jobs = 0
    cancelled_jobs = 0
    last_error = ''

    for item in known_jobs:
        status = str(item.get('status') or '').strip().lower()
        if status == 'completed':
            completed_jobs += 1
        elif status == 'cancelled':
            cancelled_jobs += 1
            last_error = str(item.get('error') or item.get('detail') or last_error).strip()
        elif status:
            failed_jobs += 1
            last_error = str(item.get('error') or item.get('detail') or last_error).strip()

    return {
        'jobs': known_jobs,
        'completed_jobs': completed_jobs,
        'failed_jobs': failed_jobs,
        'cancelled_jobs': cancelled_jobs,
        'last_error': last_error,
    }


def _broadcast_job_update(user_id: str, workspace_id: str, job: dict):
    payload = {
        'type': 'workspace.research_job_updated',
        'user_id': user_id,
        'workspace_id': workspace_id,
        'job': job,
    }
    try:
        asyncio.run(realtime_sync.broadcast(build_workspace_channel_key(user_id, workspace_id), payload))
    except Exception:
        pass


def _broadcast_batch_update(user_id: str, workspace_id: str, batch: dict):
    payload = {
        'type': 'workspace.research_batch_updated',
        'user_id': user_id,
        'workspace_id': workspace_id,
        'batch': batch,
    }
    try:
        asyncio.run(realtime_sync.broadcast(build_workspace_channel_key(user_id, workspace_id), payload))
    except Exception:
        pass


def _set_active_job(user_id: str, workspace_id: str, job: dict):
    key = _build_job_key(user_id, workspace_id, int(job['id']))
    state.research.active_jobs[key] = dict(job or {})
    return key


def _refresh_active_job(user_id: str, workspace_id: str, job_id: int):
    job = get_workspace_research_job(user_id, workspace_id, job_id)
    if not job:
        return None
    _set_active_job(user_id, workspace_id, job)
    return job


def _reconcile_stale_research_runtime(user_id: str, workspace_id: str):
    now = time.time()
    jobs = _list_reconcile_jobs_snapshot(user_id, workspace_id)
    batches = _list_reconcile_batches_snapshot(user_id, workspace_id)
    jobs_by_id = {
        int(item['id']): item
        for item in jobs
        if item.get('id') is not None
    }
    active_batch_jobs = _build_active_batch_job_index(batches)
    live_batch_workers = _build_live_batch_worker_index(user_id, workspace_id, active_batch_jobs)

    for job in jobs:
        status = str(job.get('status') or '').strip().lower()
        if status not in {'queued', 'running'}:
            continue
        job_id = int(job['id'])
        key = _build_job_key(user_id, workspace_id, job_id)
        runtime_job = _enrich_runtime_entity(job, key=key, batch=False)
        thread = state.research.job_threads.get(key) or _find_live_batch_worker_for_job(
            user_id,
            workspace_id,
            job_id,
            active_batch_jobs=active_batch_jobs,
            live_batch_workers=live_batch_workers,
        )
        if _is_job_owned_by_active_batch(user_id, workspace_id, job_id, active_batch_jobs):
            continue
        if bool((runtime_job or {}).get('auto_sanitize_recommended')):
            updated_job = _sanitize_stale_research_job(user_id, workspace_id, runtime_job, key, now)
            if updated_job:
                jobs_by_id[job_id] = updated_job
            continue
        if thread is not None and thread.is_alive():
            continue
        if _is_runtime_startup_pending(runtime_job):
            continue
        if str((runtime_job or {}).get('data_feed_status') or '').strip().lower() in {'receiving', 'waiting'}:
            continue

        cancelled = bool((runtime_job or {}).get('cancel_requested'))
        _record_reconcile_system_log(
            user_id,
            workspace_id,
            level='error' if not cancelled else 'warning',
            message='Research runtime reconciler finalized a job as interrupted.',
            context={
                'entity': 'job',
                'job_id': job_id,
                'status_before': status,
                'cancel_requested': cancelled,
                'runtime_job_status': (runtime_job or {}).get('status'),
                'runtime_job_phase': (runtime_job or {}).get('phase'),
                'data_feed_status': (runtime_job or {}).get('data_feed_status'),
                'worker_alive': bool(thread and thread.is_alive()),
            },
        )
        updated_job = _update_job(
            user_id,
            workspace_id,
            job_id,
            status='cancelled' if cancelled else 'failed',
            progress=0.0 if cancelled else 1.0,
            phase='cancelled' if cancelled else 'failed',
            phase_label='Cancelled' if cancelled else 'Failed',
            detail='Research job was interrupted after backend restart.' if not cancelled else 'Research job was cancelled during backend restart.',
            error='' if cancelled else str((runtime_job or {}).get('error') or 'Research worker was not running in this backend process.'),
            finished_at=now,
        )
        if updated_job:
            jobs_by_id[job_id] = updated_job
        _clear_research_runtime_key(key, batch=False)

    for batch in batches:
        status = str(batch.get('status') or '').strip().lower()
        if status not in {'queued', 'running'}:
            continue
        batch_id = int(batch['id'])
        key = _build_batch_key(user_id, workspace_id, batch_id)
        runtime_batch = _enrich_runtime_entity(batch, key=key, batch=True)
        thread = state.research.job_threads.get(key)
        worker_alive = thread is not None and thread.is_alive()
        current_job = None
        current_job_id = (runtime_batch or {}).get('current_job_id')
        if current_job_id is not None:
            try:
                current_job = jobs_by_id.get(int(current_job_id))
            except Exception:
                current_job = None
        if current_job is None and current_job_id is not None:
            try:
                current_job = get_workspace_research_job(user_id, workspace_id, int(current_job_id), include_payload=False)
            except Exception:
                current_job = None
            if current_job:
                jobs_by_id[int(current_job_id)] = current_job
        current_job_status = str((current_job or {}).get('status') or '').strip().lower()

        if current_job_status in {'failed', 'cancelled'} and not worker_alive:
            cancelled = current_job_status == 'cancelled' or bool((runtime_batch or {}).get('cancel_requested'))
            summary = _derive_batch_summary_from_known_jobs(runtime_batch, current_job=current_job)
            completed_jobs = int(summary.get('completed_jobs') or 0)
            failed_jobs = int(summary.get('failed_jobs') or 0)
            cancelled_jobs = int(summary.get('cancelled_jobs') or 0)
            total_jobs = max(
                int((runtime_batch or {}).get('total_jobs') or 0),
                int(batch.get('total_jobs') or 0),
                completed_jobs + failed_jobs + cancelled_jobs,
            )
            batch_error = '' if cancelled else (
                str((current_job or {}).get('error') or '').strip()
                or str(summary.get('last_error') or '').strip()
                or str((runtime_batch or {}).get('error') or 'Batch worker was not running in this backend process.').strip()
            )
            batch_detail = (
                str((current_job or {}).get('detail') or '').strip()
                or (
                    'Batch was cancelled after the current child job stopped during backend restart.'
                    if cancelled
                    else 'Batch was interrupted after the current child job failed during backend restart.'
                )
            )
            _record_reconcile_system_log(
                user_id,
                workspace_id,
                level='error' if not cancelled else 'warning',
                message='Research runtime reconciler finalized a batch after the current child job was already terminal.',
                context={
                    'entity': 'batch',
                    'batch_id': batch_id,
                    'current_job_id': current_job_id,
                    'cancel_requested': cancelled,
                    'current_job_status': current_job_status,
                    'data_feed_status': (runtime_batch or {}).get('data_feed_status'),
                    'worker_alive': bool(worker_alive),
                },
            )
            _update_batch(
                user_id,
                workspace_id,
                batch_id,
                status='cancelled' if cancelled else 'failed',
                progress=1.0,
                phase='cancelled' if cancelled else 'failed',
                phase_label='Cancelled' if cancelled else 'Failed',
                detail=batch_detail,
                error=batch_error,
                completed_jobs=completed_jobs,
                failed_jobs=failed_jobs,
                cancelled_jobs=cancelled_jobs + max(0, total_jobs - (completed_jobs + failed_jobs + cancelled_jobs)) if cancelled else cancelled_jobs,
                result={'jobs': summary.get('jobs') or []},
                finished_at=now,
            )
            _clear_research_runtime_key(key, batch=True)
            continue

        if bool((runtime_batch or {}).get('auto_sanitize_recommended')):
            _sanitize_stale_research_batch(
                user_id,
                workspace_id,
                runtime_batch,
                key,
                now,
                current_job=current_job,
            )
            continue
        if worker_alive:
            continue
        if _is_runtime_startup_pending(runtime_batch):
            continue
        if str((runtime_batch or {}).get('data_feed_status') or '').strip().lower() in {'receiving', 'waiting'}:
            continue

        cancelled = bool((runtime_batch or {}).get('cancel_requested'))

        summary = _derive_batch_summary_from_known_jobs(runtime_batch, current_job=current_job)
        completed_jobs = int(summary.get('completed_jobs') or 0)
        failed_jobs = int(summary.get('failed_jobs') or 0)
        cancelled_jobs = int(summary.get('cancelled_jobs') or 0)
        total_jobs = max(
            int((runtime_batch or {}).get('total_jobs') or 0),
            completed_jobs + failed_jobs + cancelled_jobs,
        )
        last_error = str(summary.get('last_error') or '').strip()

        batch_status = 'cancelled' if cancelled else 'failed'
        batch_phase = 'cancelled' if cancelled else 'failed'
        batch_phase_label = 'Cancelled' if cancelled else 'Failed'
        batch_detail = (
            'Batch was cancelled during backend restart.'
            if cancelled
            else (
                str((current_job or {}).get('detail') or '').strip()
                or 'Batch was interrupted after backend restart.'
            )
        )
        batch_error = '' if cancelled else (
            str((current_job or {}).get('error') or '').strip()
            or last_error
            or str((runtime_batch or {}).get('error') or 'Batch worker was not running in this backend process.').strip()
        )

        _record_reconcile_system_log(
            user_id,
            workspace_id,
            level='error' if not cancelled else 'warning',
            message='Research runtime reconciler finalized a batch as interrupted.',
            context={
                'entity': 'batch',
                'batch_id': batch_id,
                'current_job_id': current_job_id,
                'cancel_requested': cancelled,
                'data_feed_status': (runtime_batch or {}).get('data_feed_status'),
                'worker_alive': bool(worker_alive),
            },
        )
        _update_batch(
            user_id,
            workspace_id,
            batch_id,
            status=batch_status,
            progress=0.0 if cancelled else 1.0,
            phase=batch_phase,
            phase_label=batch_phase_label,
            detail=batch_detail,
            error=batch_error,
            completed_jobs=completed_jobs,
            failed_jobs=failed_jobs,
            cancelled_jobs=cancelled_jobs + max(0, total_jobs - (completed_jobs + failed_jobs + cancelled_jobs)) if cancelled else cancelled_jobs,
            result={'jobs': summary.get('jobs') or []},
            finished_at=now,
        )
        _clear_research_runtime_key(key, batch=True)


def _update_job(user_id: str, workspace_id: str, job_id: int, **updates):
    job_type = None
    if 'request' in updates or 'result' in updates:
        existing = get_workspace_research_job(user_id, workspace_id, job_id) or {}
        job_type = str((existing.get('job_type') or '')).strip().lower()
    if 'request' in updates:
        updates['request'] = _compact_research_job_request(job_type or '', updates.get('request'))
    if 'result' in updates:
        updates['result'] = _compact_research_job_result(job_type or '', updates.get('result'))
    job = update_workspace_research_job(user_id, workspace_id, job_id, **updates)
    if job:
        _set_active_job(user_id, workspace_id, job)
        _touch_research_runtime_heartbeat(_build_job_key(user_id, workspace_id, job_id))
        _broadcast_job_update(user_id, workspace_id, job)
    return job


def _update_batch(user_id: str, workspace_id: str, batch_id: int, **updates):
    if 'request' in updates:
        updates['request'] = _compact_research_batch_request(updates.get('request'))
    if 'result' in updates:
        updates['result'] = _compact_research_batch_result(updates.get('result'))
    batch = update_workspace_research_batch(user_id, workspace_id, batch_id, **updates)
    if batch:
        _set_active_batch(user_id, workspace_id, batch)
        _touch_research_runtime_heartbeat(_build_batch_key(user_id, workspace_id, batch_id), batch=True)
        _broadcast_batch_update(user_id, workspace_id, batch)
    return batch


def reconcile_research_runtime(user_id: str, workspace_id: str):
    before_jobs = {
        int(item['id']): str(item.get('status') or '').strip().lower()
        for item in _list_reconcile_jobs_snapshot(user_id, workspace_id)
    }
    before_batches = {
        int(item['id']): str(item.get('status') or '').strip().lower()
        for item in _list_reconcile_batches_snapshot(user_id, workspace_id)
    }

    _reconcile_stale_research_runtime(user_id, workspace_id)

    after_jobs = {
        int(item['id']): str(item.get('status') or '').strip().lower()
        for item in _list_reconcile_jobs_snapshot(user_id, workspace_id)
    }
    after_batches = {
        int(item['id']): str(item.get('status') or '').strip().lower()
        for item in _list_reconcile_batches_snapshot(user_id, workspace_id)
    }

    changed_jobs = [
        {'id': job_id, 'from': before_jobs.get(job_id), 'to': after_jobs.get(job_id)}
        for job_id in sorted(set(before_jobs) | set(after_jobs))
        if before_jobs.get(job_id) != after_jobs.get(job_id)
    ]
    changed_batches = [
        {'id': batch_id, 'from': before_batches.get(batch_id), 'to': after_batches.get(batch_id)}
        for batch_id in sorted(set(before_batches) | set(after_batches))
        if before_batches.get(batch_id) != after_batches.get(batch_id)
    ]

    return {
        'changed_jobs': changed_jobs,
        'changed_batches': changed_batches,
        'changed_job_count': len(changed_jobs),
        'changed_batch_count': len(changed_batches),
    }


def list_research_jobs(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    include_payload: bool = True,
):
    jobs = list_workspace_research_jobs(user_id, workspace_id, limit=limit, include_payload=include_payload)
    return [
        _enrich_runtime_entity(job, key=_build_job_key(user_id, workspace_id, int(job['id'])), batch=False)
        for job in jobs
    ]


def get_research_job(
    user_id: str,
    workspace_id: str,
    job_id: int,
    *,
    include_payload: bool = True,
):
    job = get_workspace_research_job(user_id, workspace_id, job_id, include_payload=include_payload)
    return _enrich_runtime_entity(job, key=_build_job_key(user_id, workspace_id, int(job_id)), batch=False)


def list_research_batches(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    include_payload: bool = True,
):
    batches = list_workspace_research_batches(user_id, workspace_id, limit=limit, include_payload=include_payload)
    return [
        _enrich_runtime_entity(batch, key=_build_batch_key(user_id, workspace_id, int(batch['id'])), batch=True)
        for batch in batches
    ]


def get_research_batch(
    user_id: str,
    workspace_id: str,
    batch_id: int,
    *,
    include_payload: bool = True,
):
    batch = get_workspace_research_batch(user_id, workspace_id, batch_id, include_payload=include_payload)
    return _enrich_runtime_entity(batch, key=_build_batch_key(user_id, workspace_id, int(batch_id)), batch=True)


def list_research_campaigns(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    include_payload: bool = True,
):
    return list_workspace_research_campaigns(
        user_id,
        workspace_id,
        limit=limit,
        include_payload=include_payload,
    )

def get_research_campaign(
    user_id: str,
    workspace_id: str,
    campaign_id: int,
    *,
    include_payload: bool = True,
):
    return get_workspace_research_campaign(
        user_id,
        workspace_id,
        campaign_id,
        include_payload=include_payload,
    )


def _progress_callback_factory(user_id: str, workspace_id: str, job_id: int):
    def callback(progress=None, phase=None, phase_label=None, detail=None):
        _update_job(
            user_id,
            workspace_id,
            job_id,
            status='running',
            progress=progress if progress is not None else 0.1,
            phase=phase,
            phase_label=phase_label,
            detail=detail,
        )
    return callback


def _is_cancel_requested(user_id: str, workspace_id: str, job_id: int):
    job = get_workspace_research_job(user_id, workspace_id, job_id)
    return bool((job or {}).get('cancel_requested'))


def _run_preset_compare_job(user_id: str, workspace_id: str, job_id: int, request_payload: dict):
    started_at = time.time()
    _update_job(
        user_id,
        workspace_id,
        job_id,
        status='running',
        progress=0.02,
        phase='starting',
        phase_label='Starting',
        detail='Preparing research study payload.',
        started_at=started_at,
    )

    if _is_cancel_requested(user_id, workspace_id, job_id):
        _update_job(
            user_id,
            workspace_id,
            job_id,
            status='cancelled',
            progress=0.0,
            phase='cancelled',
            phase_label='Cancelled',
            detail='Job was cancelled before execution started.',
            finished_at=time.time(),
        )
        return

    payload = PresetCompareRequest.model_validate(request_payload or {})
    result = execute_preset_compare_request(
        payload,
        progress_callback=_progress_callback_factory(user_id, workspace_id, job_id),
        should_cancel=lambda: _is_cancel_requested(user_id, workspace_id, job_id),
    )

    if result.get('status') != 'ok':
        cancelled = str(result.get('error') or '').strip().lower().startswith('research job cancelled')
        _update_job(
            user_id,
            workspace_id,
            job_id,
            status='cancelled' if cancelled else 'failed',
            progress=0.0 if cancelled else 1.0,
            phase='cancelled' if cancelled else 'failed',
            phase_label='Cancelled' if cancelled else 'Failed',
            detail='Research job did not complete successfully.',
            error=str(result.get('error') or '').strip(),
            result=result,
            finished_at=time.time(),
        )
        return

    best_id = result.get('best_preset_id')
    best_label = ''
    comparison_count = len(result.get('comparisons') or [])
    for item in result.get('comparisons') or []:
        if str(item.get('id') or '') == str(best_id or ''):
            best_label = str(item.get('label') or '').strip()
            break

    archived_run = create_workspace_research_run(
        user_id,
        workspace_id,
        run_type='preset_compare',
        side=None,
        run_name='preset compare',
        version='backend_job_v1',
        best_id=best_id,
        best_label=best_label,
        comparison_count=comparison_count,
        run_label=(get_workspace_research_job(user_id, workspace_id, job_id) or {}).get('run_label'),
        run_notes=(get_workspace_research_job(user_id, workspace_id, job_id) or {}).get('run_notes'),
        pinned=False,
        payload=result,
    )

    _update_job(
        user_id,
        workspace_id,
        job_id,
        status='completed',
        progress=1.0,
        phase='completed',
        phase_label='Completed',
        detail='Research study completed in the backend.',
        result=result,
        run_id=archived_run['id'],
        finished_at=time.time(),
        error='',
    )


def _run_strategy_pipeline_job(user_id: str, workspace_id: str, job_id: int, request_payload: dict):
    started_at = time.time()
    safe_request = dict(request_payload or {})
    job_label = str(safe_request.get('label') or safe_request.get('name') or 'Batch run').strip() or 'Batch run'
    chart_config = dict(safe_request.get('chart') or {})
    research_plan = dict(safe_request.get('researchPlan') or {})

    _update_job(
        user_id,
        workspace_id,
        job_id,
        status='running',
        progress=0.02,
        phase='starting',
        phase_label='Starting',
        detail=f'Preparing pipeline for "{job_label}".',
        started_at=started_at,
    )

    if _is_cancel_requested(user_id, workspace_id, job_id):
        _update_job(
            user_id,
            workspace_id,
            job_id,
            status='cancelled',
            progress=0.0,
            phase='cancelled',
            phase_label='Cancelled',
            detail='Job was cancelled before execution started.',
            finished_at=time.time(),
        )
        return

    symbol = str(chart_config.get('symbol') or '').strip().upper()
    timeframe = str(chart_config.get('timeframe') or '').strip().upper()
    bars = max(1, int(chart_config.get('bars') or 1))
    indicators = list(chart_config.get('indicators') or [])
    apply_payload = ApplyStrategyRequest.model_validate({
        'strategy': safe_request.get('strategy') or {},
        'strategies': safe_request.get('strategies') or [],
        'backtest': safe_request.get('backtest') or {},
    })

    _update_job(
        user_id,
        workspace_id,
        job_id,
        progress=0.08,
        phase='backtest',
        phase_label='Backtest',
        detail=f'Running isolated backtest for {symbol or "--"} {timeframe or "--"} {bars:,} bars.',
    )

    evaluation = evaluate_strategy_request_in_context(
        payload=apply_payload,
        symbol_name=symbol,
        timeframe=timeframe,
        bars=bars,
        indicators_payload=indicators,
        should_cancel=lambda: _is_cancel_requested(user_id, workspace_id, job_id),
    )

    if evaluation.get('status') != 'ok':
        cancelled = str(evaluation.get('error') or '').strip().lower().startswith('research job cancelled')
        _update_job(
            user_id,
            workspace_id,
            job_id,
            status='cancelled' if cancelled else 'failed',
            progress=0.0 if cancelled else 1.0,
            phase='cancelled' if cancelled else 'failed',
            phase_label='Cancelled' if cancelled else 'Failed',
            detail='Pipeline backtest cancelled by user.' if cancelled else 'Pipeline backtest failed.',
            error=str(evaluation.get('error') or 'Unknown pipeline backtest error.'),
            finished_at=time.time(),
        )
        return

    backtest_result = {
        'label': job_label,
        'chart': {
            'symbol': symbol,
            'timeframe': timeframe,
            'bars': bars,
            'indicators': indicators,
        },
        'request': {
            'strategy': safe_request.get('strategy') or {},
            'strategies': safe_request.get('strategies') or [],
            'backtest': safe_request.get('backtest') or {},
        },
        'stats': evaluation.get('stats') or {},
        'results': evaluation.get('serialized_results') or [],
        'strategy_view_meta': evaluation.get('strategy_view_meta') or {},
        'applied_indicators': evaluation.get('applied_indicators') or [],
        'available_columns': evaluation.get('available_columns') or [],
        'available_column_details': evaluation.get('available_column_details') or [],
    }

    final_result = {
        'status': 'ok',
        'job_type': 'strategy_pipeline',
        'pipeline': backtest_result,
        'research': None,
    }

    research_kind = str(research_plan.get('kind') or research_plan.get('mode') or 'none').strip().lower() or 'none'
    research_payload = dict(research_plan.get('payload') or {})

    if research_kind == 'preset_compare':
        if _is_cancel_requested(user_id, workspace_id, job_id):
            _update_job(
                user_id,
                workspace_id,
                job_id,
                status='cancelled',
                progress=0.0,
                phase='cancelled',
                phase_label='Cancelled',
                detail='Job was cancelled before research started.',
                finished_at=time.time(),
            )
            return

        baseline_payload = dict(research_payload.get('baseline') or {})
        reuse_pipeline_baseline = not baseline_payload
        if not baseline_payload:
            baseline_payload = {
                'id': str(safe_request.get('id') or f'job-{job_id}'),
                'label': job_label,
                'strategy': safe_request.get('strategy') or {},
                'strategies': safe_request.get('strategies') or [],
            }

        research_chart_context = dict(research_payload.get('chartContext') or {})
        merged_research_indicators = _merge_indicator_payloads(
            indicators,
            research_chart_context.get('indicators') or [],
        )
        compare_payload = {
            **research_payload,
            'baseline': baseline_payload,
            'backtest': research_payload.get('backtest') or safe_request.get('backtest') or {},
            'chartContext': {
                **research_chart_context,
                'symbol': symbol,
                'timeframe': timeframe,
                'bars': max(1, int(research_chart_context.get('bars') or bars or 1)),
                'indicators': merged_research_indicators,
            },
        }

        _update_job(
            user_id,
            workspace_id,
            job_id,
            progress=0.55,
            phase='research',
            phase_label='Research',
            detail='Running preset-compare research after the backtest.',
        )

        compare_result = execute_preset_compare_request(
            PresetCompareRequest.model_validate(compare_payload),
            progress_callback=lambda progress=None, phase=None, phase_label=None, detail=None: _update_job(
                user_id,
                workspace_id,
                job_id,
                status='running',
                progress=min(0.98, 0.55 + (max(0.0, min(1.0, float(progress if progress is not None else 0.0))) * 0.4)),
                phase=phase or 'research',
                phase_label=phase_label or 'Research',
                detail=detail or 'Running backend research.',
            ),
            should_cancel=lambda: _is_cancel_requested(user_id, workspace_id, job_id),
            baseline_summary_override=summarize_comparison_stats(evaluation.get('stats')) if reuse_pipeline_baseline else None,
        )

        if compare_result.get('status') != 'ok':
            cancelled = str(compare_result.get('error') or '').strip().lower().startswith('research job cancelled')
            _update_job(
                user_id,
                workspace_id,
                job_id,
                status='cancelled' if cancelled else 'failed',
                progress=0.0 if cancelled else 1.0,
                phase='cancelled' if cancelled else 'failed',
                phase_label='Cancelled' if cancelled else 'Failed',
                detail='Pipeline research did not complete successfully.',
                error=str(compare_result.get('error') or '').strip(),
                result={
                    'status': 'error',
                    'pipeline': backtest_result,
                    'research': compare_result,
                },
                finished_at=time.time(),
            )
            return

        final_result['research'] = compare_result

    archived_run = create_workspace_research_run(
        user_id,
        workspace_id,
        run_type='strategy_pipeline',
        side=None,
        run_name=job_label,
        version='backend_pipeline_v1',
        best_id=None,
        best_label=None,
        comparison_count=None,
        run_label=(get_workspace_research_job(user_id, workspace_id, job_id) or {}).get('run_label'),
        run_notes=(get_workspace_research_job(user_id, workspace_id, job_id) or {}).get('run_notes'),
        pinned=False,
        payload=final_result,
    )

    _update_job(
        user_id,
        workspace_id,
        job_id,
        status='completed',
        progress=1.0,
        phase='completed',
        phase_label='Completed',
        detail='Pipeline completed in the backend.',
        result=final_result,
        run_id=archived_run['id'],
        finished_at=time.time(),
        error='',
    )


def _research_worker(job_type: str, user_id: str, workspace_id: str, job_id: int, request_payload: dict):
    key = _build_job_key(user_id, workspace_id, job_id)
    state.research.job_threads[key] = threading.current_thread()
    _start_research_runtime_heartbeat(
        key,
        batch=False,
        persist_callback=lambda touched_at: touch_workspace_research_job(
            user_id,
            workspace_id,
            job_id,
            updated_at=touched_at,
        ),
    )
    try:
        state.research.last_run_at = time.time()
        if job_type == 'preset_compare':
            _run_preset_compare_job(user_id, workspace_id, job_id, request_payload)
        elif job_type == 'strategy_pipeline':
            _run_strategy_pipeline_job(user_id, workspace_id, job_id, request_payload)
        else:
            _update_job(
                user_id,
                workspace_id,
                job_id,
                status='failed',
                phase='failed',
                phase_label='Failed',
                detail='Research job type is not supported yet.',
                error=f'Unsupported research job type: {job_type}',
                finished_at=time.time(),
            )
    except Exception as error:
        state.research.last_error = str(error)
        _update_job(
            user_id,
            workspace_id,
            job_id,
            status='failed',
            phase='failed',
            phase_label='Failed',
            detail='Research job crashed in the backend worker.',
            error=str(error),
            finished_at=time.time(),
        )
    finally:
        _stop_research_runtime_heartbeat(key)
        final_job = get_workspace_research_job(user_id, workspace_id, job_id)
        if final_job:
            _record_research_event(
                'finished',
                user_id=user_id,
                workspace_id=workspace_id,
                job_id=job_id,
                status=final_job.get('status') or '',
                detail=final_job.get('detail'),
            )
        state.research.job_threads.pop(key, None)
        state.research.active_jobs.pop(key, None)


def _is_batch_cancel_requested(user_id: str, workspace_id: str, batch_id: int):
    batch = get_workspace_research_batch(user_id, workspace_id, batch_id)
    return bool((batch or {}).get('cancel_requested'))


def _is_retryable_research_worker_error(job: dict | None):
    if not isinstance(job, dict):
        return False
    if bool(job.get('cancel_requested')):
        return False
    if job.get('run_id') is not None:
        return False
    if job.get('result'):
        return False

    status = str(job.get('status') or '').strip().lower()
    error = str(job.get('error') or '').strip().lower()

    if status != 'failed':
        return False

    return error == 'research worker was not running in this backend process.'


def _research_batch_worker(user_id: str, workspace_id: str, batch_id: int, request_payload: dict):
    key = _build_batch_key(user_id, workspace_id, batch_id)
    _start_research_runtime_heartbeat(
        key,
        batch=True,
        persist_callback=lambda touched_at: touch_workspace_research_batch(
            user_id,
            workspace_id,
            batch_id,
            updated_at=touched_at,
        ),
    )
    started_at = time.time()
    jobs = list((request_payload or {}).get('jobs') or [])
    total_jobs = len(jobs)
    results = []
    completed_jobs = 0
    failed_jobs = 0
    cancelled_jobs = 0
    last_error = ''

    try:
        _update_batch(
            user_id,
            workspace_id,
            batch_id,
            status='running',
            progress=0.02,
            phase='starting',
            phase_label='Starting',
            detail='Batch worker started.',
            total_jobs=total_jobs,
            started_at=started_at,
        )

        for index, item in enumerate(jobs):
            if _is_batch_cancel_requested(user_id, workspace_id, batch_id):
                cancelled_jobs += max(0, total_jobs - index)
                _update_batch(
                    user_id,
                    workspace_id,
                    batch_id,
                    status='cancelled',
                    progress=(index / max(1, total_jobs)),
                    phase='cancelled',
                    phase_label='Cancelled',
                    detail='Batch cancelled by user.',
                    completed_jobs=completed_jobs,
                    failed_jobs=failed_jobs,
                    cancelled_jobs=cancelled_jobs,
                    result={'jobs': results},
                    finished_at=time.time(),
                )
                return

            child_job = create_workspace_research_job(
                user_id,
                workspace_id,
                job_type=str(item.get('job_type') or 'preset_compare'),
                request=item.get('request') or {},
                run_label=str(item.get('run_label') or '').strip() or '',
                run_notes=str(item.get('run_notes') or '').strip() or '',
            )
            child_key = _build_job_key(user_id, workspace_id, int(child_job['id']))
            state.research.job_threads[child_key] = threading.current_thread()
            child_label = (
                str(child_job.get('run_label') or '').strip()
                or str(child_job.get('job_type') or '').strip()
                or f"job #{child_job.get('id')}"
            )
            _set_active_job(user_id, workspace_id, child_job)
            _broadcast_job_update(user_id, workspace_id, child_job)
            _update_batch(
                user_id,
                workspace_id,
                batch_id,
                phase='running_job',
                phase_label=f'Running {index + 1}/{total_jobs}',
                detail=f'Executing child job {index + 1} of {total_jobs}: {child_label}.',
                current_job_id=child_job['id'],
                progress=(index / max(1, total_jobs)),
            )
            _research_worker(child_job['job_type'], user_id, workspace_id, int(child_job['id']), child_job['request'])
            final_child = get_workspace_research_job(user_id, workspace_id, int(child_job['id'])) or child_job
            if _is_retryable_research_worker_error(final_child) and not _is_batch_cancel_requested(user_id, workspace_id, batch_id):
                _update_batch(
                    user_id,
                    workspace_id,
                    batch_id,
                    phase='retrying_job',
                    phase_label=f'Retrying {index + 1}/{total_jobs}',
                    detail=f'Retrying child job {index + 1} of {total_jobs} after transient worker failure: {child_label}.',
                    current_job_id=final_child.get('id'),
                    progress=(index / max(1, total_jobs)),
                    error=str(final_child.get('error') or '').strip(),
                )
                retry_job = create_workspace_research_job(
                    user_id,
                    workspace_id,
                    job_type=str(item.get('job_type') or 'preset_compare'),
                    request=item.get('request') or {},
                    run_label=str(item.get('run_label') or '').strip() or '',
                    run_notes=str(item.get('run_notes') or '').strip() or '',
                )
                retry_key = _build_job_key(user_id, workspace_id, int(retry_job['id']))
                state.research.job_threads[retry_key] = threading.current_thread()
                _set_active_job(user_id, workspace_id, retry_job)
                _broadcast_job_update(user_id, workspace_id, retry_job)
                _update_batch(
                    user_id,
                    workspace_id,
                    batch_id,
                    phase='running_job',
                    phase_label=f'Running {index + 1}/{total_jobs}',
                    detail=f'Executing retry for child job {index + 1} of {total_jobs}: {child_label}.',
                    current_job_id=retry_job['id'],
                    progress=(index / max(1, total_jobs)),
                    error='',
                )
                _research_worker(retry_job['job_type'], user_id, workspace_id, int(retry_job['id']), retry_job['request'])
                final_child = get_workspace_research_job(user_id, workspace_id, int(retry_job['id'])) or retry_job
            final_label = (
                str(final_child.get('run_label') or '').strip()
                or str(final_child.get('job_type') or '').strip()
                or str(final_child.get('id') or '').strip()
            )
            results.append({
                'job_id': final_child.get('id'),
                'run_label': final_child.get('run_label'),
                'status': final_child.get('status'),
                'run_id': final_child.get('run_id'),
                'detail': final_child.get('detail'),
                'error': final_child.get('error'),
                'result': final_child.get('result'),
            })
            final_status = str(final_child.get('status') or '').strip().lower()
            if final_status == 'completed':
                completed_jobs += 1
            elif final_status == 'cancelled':
                cancelled_jobs += 1
                last_error = str(final_child.get('error') or final_child.get('detail') or '').strip()
            else:
                failed_jobs += 1
                last_error = str(final_child.get('error') or final_child.get('detail') or '').strip()

            _update_batch(
                user_id,
                workspace_id,
                batch_id,
                completed_jobs=completed_jobs,
                failed_jobs=failed_jobs,
                cancelled_jobs=cancelled_jobs,
                current_job_id=final_child.get('id'),
                progress=((index + 1) / max(1, total_jobs)),
                detail=(
                    f'Child job failed: {final_label}.'
                    if final_status not in {'completed', 'cancelled'}
                    else f'Completed child job {index + 1} of {total_jobs}: {final_label}.'
                ),
                error=last_error if final_status in {'failed', 'cancelled'} else '',
                result={'jobs': results},
            )

        final_status = 'completed' if failed_jobs == 0 and cancelled_jobs == 0 else 'failed'
        final_phase_label = 'Completed' if final_status == 'completed' else 'Completed with issues'
        _update_batch(
            user_id,
            workspace_id,
            batch_id,
            status=final_status,
            progress=1.0,
            phase='completed',
            phase_label=final_phase_label,
            detail=(
                'Batch finished successfully.'
                if final_status == 'completed'
                else (last_error or 'Batch finished with failed or cancelled jobs.')
            ),
            completed_jobs=completed_jobs,
            failed_jobs=failed_jobs,
            cancelled_jobs=cancelled_jobs,
            error='' if final_status == 'completed' else last_error,
            result={'jobs': results},
            finished_at=time.time(),
        )
    except Exception as error:
        _update_batch(
            user_id,
            workspace_id,
            batch_id,
            status='failed',
            phase='failed',
            phase_label='Failed',
            detail='Batch worker crashed.',
            error=str(error),
            completed_jobs=completed_jobs,
            failed_jobs=failed_jobs + 1,
            cancelled_jobs=cancelled_jobs,
            result={'jobs': results},
            finished_at=time.time(),
        )
    finally:
        _stop_research_runtime_heartbeat(key)
        state.research.job_threads.pop(key, None)
        state.research.active_batches.pop(key, None)


def queue_research_job(
    user_id: str,
    workspace_id: str,
    *,
    job_type: str,
    request_payload: dict,
    run_label: str | None = None,
    run_notes: str | None = None,
):
    job = create_workspace_research_job(
        user_id,
        workspace_id,
        job_type=job_type,
        request=_compact_research_job_request(job_type, request_payload),
        run_label=run_label,
        run_notes=run_notes,
    )
    key = _set_active_job(user_id, workspace_id, job)
    thread = threading.Thread(
        target=_research_worker,
        args=(job['job_type'], user_id, workspace_id, int(job['id']), job['request']),
        name=f'research-job-{job["id"]}',
        daemon=True,
    )
    state.research.job_threads[key] = thread
    _record_research_event(
        'queued',
        user_id=user_id,
        workspace_id=workspace_id,
        job_id=job['id'],
        status=job['status'],
        detail=job.get('detail'),
    )
    _broadcast_job_update(user_id, workspace_id, job)
    thread.start()
    return job


def queue_research_batch(
    user_id: str,
    workspace_id: str,
    *,
    label: str,
    jobs: list[dict],
):
    full_request = {'jobs': list(jobs or [])}
    batch = create_workspace_research_batch(
        user_id,
        workspace_id,
        label=label,
        request=_compact_research_batch_request(full_request),
    )
    key = _build_batch_key(user_id, workspace_id, int(batch['id']))
    thread = threading.Thread(
        target=_research_batch_worker,
        args=(user_id, workspace_id, int(batch['id']), full_request),
        name=f'research-batch-{batch["id"]}',
        daemon=True,
    )
    state.research.job_threads[key] = thread
    _broadcast_batch_update(user_id, workspace_id, batch)
    thread.start()
    return batch


def create_research_campaign(
    user_id: str,
    workspace_id: str,
    *,
    label: str,
    description: str | None = None,
    jobs: list[dict] | None = None,
    batch_jobs: list[dict] | None = None,
    shared_features: list[dict] | None = None,
    options: dict | None = None,
):
    return create_workspace_research_campaign(
        user_id,
        workspace_id,
        label=label,
        description=description,
        request={
            'jobs': list(jobs or []),
            'batch_jobs': list(batch_jobs or []),
            'shared_features': list(shared_features or []),
            'options': dict(options or {}),
        },
    )


def _merge_research_campaign_request(
    existing_request: dict | None,
    *,
    jobs: list[dict] | None = None,
    batch_jobs: list[dict] | None = None,
    shared_features: list[dict] | None = None,
    options: dict | None = None,
):
    safe_existing = dict(existing_request or {})
    merged = {
        'jobs': list(safe_existing.get('jobs') or []),
        'batch_jobs': list(safe_existing.get('batch_jobs') or []),
        'shared_features': list(safe_existing.get('shared_features') or []),
        'options': dict(safe_existing.get('options') or {}),
    }
    if jobs is not None:
        merged['jobs'] = list(jobs)
    if batch_jobs is not None:
        merged['batch_jobs'] = list(batch_jobs)
    if shared_features is not None:
        merged['shared_features'] = list(shared_features)
    if options is not None:
        merged['options'] = dict(options)
    return merged


def _extract_executable_campaign_jobs(campaign: dict | None):
    safe_request = dict((campaign or {}).get('request') or {})
    executable_jobs = []
    for entry in list(safe_request.get('jobs') or []):
        if not isinstance(entry, dict):
            continue
        if not isinstance(entry.get('request'), dict):
            continue
        executable_jobs.append(entry)
    return executable_jobs


def update_research_campaign(
    user_id: str,
    workspace_id: str,
    campaign_id: int,
    *,
    label: str | None = None,
    description: str | None = None,
    jobs: list[dict] | None = None,
    batch_jobs: list[dict] | None = None,
    shared_features: list[dict] | None = None,
    options: dict | None = None,
):
    request = None
    if jobs is not None or batch_jobs is not None or shared_features is not None or options is not None:
        existing = get_workspace_research_campaign(user_id, workspace_id, campaign_id)
        if not existing:
            return None
        request = _merge_research_campaign_request(
            existing.get('request'),
            jobs=jobs,
            batch_jobs=batch_jobs,
            shared_features=shared_features,
            options=options,
        )
    return update_workspace_research_campaign(
        user_id,
        workspace_id,
        campaign_id,
        label=label,
        description=description,
        request=request,
    )


def delete_research_campaign(user_id: str, workspace_id: str, campaign_id: int):
    return delete_workspace_research_campaign(user_id, workspace_id, campaign_id)


def launch_research_campaign(user_id: str, workspace_id: str, campaign_id: int):
    campaign = get_workspace_research_campaign(user_id, workspace_id, campaign_id)
    if not campaign:
        return None
    jobs = _extract_executable_campaign_jobs(campaign)
    if not jobs:
        raise ValueError('Research campaign has no executable jobs to launch.')
    return queue_research_batch(
        user_id,
        workspace_id,
        label=campaign.get('label') or 'Research campaign',
        jobs=jobs,
    )


def cancel_research_job(user_id: str, workspace_id: str, job_id: int):
    job = get_workspace_research_job(user_id, workspace_id, job_id)
    if not job:
        return None

    if job.get('status') in {'completed', 'failed', 'cancelled'}:
        return job

    updated = _update_job(
        user_id,
        workspace_id,
        job_id,
        cancel_requested=True,
        detail='Cancellation requested by user.',
    )

    if updated and updated.get('status') == 'queued':
        updated = _update_job(
            user_id,
            workspace_id,
            job_id,
            status='cancelled',
            phase='cancelled',
            phase_label='Cancelled',
            progress=0.0,
            finished_at=time.time(),
        )

    return updated


def cancel_research_batch(user_id: str, workspace_id: str, batch_id: int):
    batch = get_workspace_research_batch(user_id, workspace_id, batch_id)
    if not batch:
        return None
    if batch.get('status') in {'completed', 'failed', 'cancelled'}:
        return batch
    updated = _update_batch(
        user_id,
        workspace_id,
        batch_id,
        cancel_requested=True,
        detail='Batch cancellation requested by user.',
    )
    current_job_id = (updated or batch).get('current_job_id')
    if current_job_id:
        cancel_research_job(user_id, workspace_id, int(current_job_id))
    return updated

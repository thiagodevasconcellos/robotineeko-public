import time

try:
    from ..app_state import state
    from .realtime_sync import realtime_sync
    from .workspace_store import (
        append_workspace_system_log_entries,
        create_workspace_save,
        ensure_active_workspace_system_log_session,
        delete_workspace_save,
        get_workspace_save,
        get_active_workspace_system_log_session,
        get_workspace_system_log_session,
        list_workspace_system_log_entries,
        list_workspace_system_log_sessions,
        list_workspace_saves,
        load_workspace_state,
        overwrite_workspace_save,
        rename_workspace_save,
        save_workspace_state,
        start_workspace_system_log_session,
    )
except ImportError:
    from app_state import state
    from services.realtime_sync import realtime_sync
    from services.workspace_store import (
        append_workspace_system_log_entries,
        create_workspace_save,
        ensure_active_workspace_system_log_session,
        delete_workspace_save,
        get_workspace_save,
        get_active_workspace_system_log_session,
        get_workspace_system_log_session,
        list_workspace_system_log_entries,
        list_workspace_system_log_sessions,
        list_workspace_saves,
        load_workspace_state,
        overwrite_workspace_save,
        rename_workspace_save,
        save_workspace_state,
        start_workspace_system_log_session,
    )


def build_workspace_channel_key(user_id: str, workspace_id: str):
    return f'workspace:{user_id}:{workspace_id}'


def load_workspace_runtime(user_id: str | None = None, workspace_id: str | None = None):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id

    stored = load_workspace_state(safe_user_id, safe_workspace_id)

    runtime.active_user_id = safe_user_id
    runtime.active_workspace_id = safe_workspace_id
    runtime.state = dict(stored['state'])
    runtime.revision = int(stored['revision'])
    runtime.last_saved_at = stored['updated_at']
    runtime.last_error = None

    return stored


async def save_and_broadcast_workspace_state(
    next_state: dict,
    user_id: str | None = None,
    workspace_id: str | None = None,
    expected_revision: int | None = None,
    source: str = 'api',
):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id

    saved = save_workspace_state(
        user_id=safe_user_id,
        workspace_id=safe_workspace_id,
        state=next_state or {},
        expected_revision=expected_revision,
    )

    runtime.active_user_id = safe_user_id
    runtime.active_workspace_id = safe_workspace_id
    runtime.state = dict(saved['state'])
    runtime.revision = int(saved['revision'])
    runtime.last_saved_at = saved['updated_at']
    runtime.last_error = None

    payload = {
        'type': 'workspace.updated',
        'user_id': safe_user_id,
        'workspace_id': safe_workspace_id,
        'revision': runtime.revision,
        'state': runtime.state,
        'updated_at': runtime.last_saved_at,
        'source': source,
    }

    await realtime_sync.broadcast(
        build_workspace_channel_key(safe_user_id, safe_workspace_id),
        payload,
    )

    return payload


async def save_and_broadcast_workspace_patch(
    patch_state: dict,
    user_id: str | None = None,
    workspace_id: str | None = None,
    expected_revision: int | None = None,
    source: str = 'api_patch',
):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id

    current = load_workspace_state(safe_user_id, safe_workspace_id)
    next_state = dict(current['state'] or {})
    changed_keys = []

    for key, value in (patch_state or {}).items():
        next_state[key] = value
        changed_keys.append(str(key))

    saved = save_workspace_state(
        user_id=safe_user_id,
        workspace_id=safe_workspace_id,
        state=next_state,
        expected_revision=expected_revision,
    )

    runtime.active_user_id = safe_user_id
    runtime.active_workspace_id = safe_workspace_id
    runtime.state = dict(saved['state'])
    runtime.revision = int(saved['revision'])
    runtime.last_saved_at = saved['updated_at']
    runtime.last_error = None

    payload = {
        'type': 'workspace.patch_applied',
        'user_id': safe_user_id,
        'workspace_id': safe_workspace_id,
        'revision': runtime.revision,
        'state': runtime.state,
        'patch': patch_state or {},
        'changed_keys': changed_keys,
        'updated_at': runtime.last_saved_at,
        'source': source,
    }

    await realtime_sync.broadcast(
        build_workspace_channel_key(safe_user_id, safe_workspace_id),
        payload,
    )

    return payload


def list_workspace_save_summaries(user_id: str | None = None, workspace_id: str | None = None, limit: int = 50):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id
    return list_workspace_saves(safe_user_id, safe_workspace_id, limit=limit)


def create_workspace_save_snapshot(name: str, user_id: str | None = None, workspace_id: str | None = None):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id
    current = load_workspace_state(safe_user_id, safe_workspace_id)
    return create_workspace_save(
        user_id=safe_user_id,
        workspace_id=safe_workspace_id,
        name=name,
        state=current['state'],
    )


def get_workspace_save_snapshot(save_id: int, user_id: str | None = None, workspace_id: str | None = None):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id
    return get_workspace_save(safe_user_id, safe_workspace_id, save_id)


def delete_workspace_save_snapshot(save_id: int, user_id: str | None = None, workspace_id: str | None = None):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id
    deleted = delete_workspace_save(safe_user_id, safe_workspace_id, save_id)
    if not deleted:
        raise ValueError(f'Workspace save {save_id} was not found')
    return deleted


def rename_workspace_save_snapshot(save_id: int, name: str, user_id: str | None = None, workspace_id: str | None = None):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id
    renamed = rename_workspace_save(safe_user_id, safe_workspace_id, save_id, name)
    if not renamed:
        raise ValueError(f'Workspace save {save_id} was not found')
    return renamed


def overwrite_workspace_save_snapshot(
    save_id: int,
    name: str | None = None,
    user_id: str | None = None,
    workspace_id: str | None = None,
):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id
    current = load_workspace_state(safe_user_id, safe_workspace_id)
    overwritten = overwrite_workspace_save(
        safe_user_id,
        safe_workspace_id,
        save_id,
        current['state'],
        name=name,
    )
    if not overwritten:
        raise ValueError(f'Workspace save {save_id} was not found')
    return overwritten


async def restore_workspace_save_snapshot(save_id: int, user_id: str | None = None, workspace_id: str | None = None):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id
    saved = get_workspace_save(safe_user_id, safe_workspace_id, save_id)
    if not saved:
        raise ValueError(f'Workspace save {save_id} was not found')

    restored = await save_and_broadcast_workspace_state(
        next_state=saved['state'],
        user_id=safe_user_id,
        workspace_id=safe_workspace_id,
        expected_revision=None,
        source=f'restore_save:{saved["id"]}',
    )

    return {
        'save': saved,
        'restored': restored,
    }


def get_workspace_system_log_payload(
    user_id: str | None = None,
    workspace_id: str | None = None,
    *,
    session_id: int | None = None,
    entry_limit: int = 500,
    create_if_missing: bool = True,
):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id

    if session_id is not None:
        session = get_workspace_system_log_session(safe_user_id, safe_workspace_id, int(session_id))
    else:
        session = get_active_workspace_system_log_session(safe_user_id, safe_workspace_id)
        if not session and create_if_missing:
            session = ensure_active_workspace_system_log_session(
                safe_user_id,
                safe_workspace_id,
                label='System log',
                source='workspace_service',
                metadata={},
            )

    if not session:
        return {
            'session': None,
            'entries': [],
            'sessions': [],
        }

    entries = list_workspace_system_log_entries(
        safe_user_id,
        safe_workspace_id,
        int(session['id']),
        limit=entry_limit,
    )
    sessions = list_workspace_system_log_sessions(safe_user_id, safe_workspace_id, limit=20)
    return {
        'session': session,
        'entries': entries,
        'sessions': sessions,
    }


async def append_and_broadcast_workspace_system_log_entries(
    entries: list[dict] | None,
    *,
    user_id: str | None = None,
    workspace_id: str | None = None,
    session_id: int | None = None,
    source: str = 'system_log',
    label: str | None = None,
    metadata: dict | None = None,
):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id

    appended = append_workspace_system_log_entries(
        safe_user_id,
        safe_workspace_id,
        entries=entries,
        session_id=session_id,
        label=label,
        source=source,
        metadata=metadata,
    )

    payload = {
        'type': 'workspace.system_log_appended',
        'user_id': safe_user_id,
        'workspace_id': safe_workspace_id,
        'session': appended.get('session'),
        'entries': list(appended.get('entries') or []),
        'source': source,
        'updated_at': (appended.get('session') or {}).get('updated_at'),
    }

    await realtime_sync.broadcast(
        build_workspace_channel_key(safe_user_id, safe_workspace_id),
        payload,
    )

    return payload


async def start_and_broadcast_workspace_system_log_session(
    *,
    user_id: str | None = None,
    workspace_id: str | None = None,
    label: str | None = None,
    source: str = 'system_log',
    metadata: dict | None = None,
):
    runtime = state.workspace
    safe_user_id = user_id or runtime.active_user_id
    safe_workspace_id = workspace_id or runtime.active_workspace_id

    started = start_workspace_system_log_session(
        safe_user_id,
        safe_workspace_id,
        label=label,
        source=source,
        metadata=metadata,
    )

    payload = {
        'type': 'workspace.system_log_started',
        'user_id': safe_user_id,
        'workspace_id': safe_workspace_id,
        'session': started.get('session'),
        'archived_session_ids': list(started.get('archived_session_ids') or []),
        'source': source,
        'updated_at': (started.get('session') or {}).get('updated_at'),
    }

    await realtime_sync.broadcast(
        build_workspace_channel_key(safe_user_id, safe_workspace_id),
        payload,
    )

    return payload


def build_workspace_runtime_payload():
    runtime = state.workspace
    return {
        'user_id': runtime.active_user_id,
        'workspace_id': runtime.active_workspace_id,
        'revision': runtime.revision,
        'state': runtime.state,
        'last_saved_at': runtime.last_saved_at,
        'last_broadcast_at': runtime.last_broadcast_at,
        'last_error': runtime.last_error,
        'channel': build_workspace_channel_key(runtime.active_user_id, runtime.active_workspace_id),
        'server_time': time.time(),
    }


def persist_strategy_runtime_snapshot(
    *,
    user_id: str,
    workspace_id: str,
    strategy_request: dict | None,
    stats: dict | None,
    results: list[dict],
    trade_markers: list[dict] | None,
    runtime_payload: dict | None,
):
    safe_user_id = str(user_id or '').strip()
    safe_workspace_id = str(workspace_id or 'default').strip() or 'default'

    if not safe_user_id:
        return None

    current = load_workspace_state(safe_user_id, safe_workspace_id)
    next_state = dict(current['state'] or {})

    request_payload = strategy_request or {}
    next_state['strategy'] = dict(request_payload.get('strategy') or next_state.get('strategy') or {})
    next_state['backtest'] = dict(request_payload.get('backtest') or next_state.get('backtest') or {})
    response_payload = {
        'status': 'ok' if results else 'empty',
        'request': request_payload or None,
        'stats': stats or {},
        'trade_markers': trade_markers or [],
        'results': results or [],
        'rows': len(results or []),
        'runtime': runtime_payload or {},
    }
    if runtime_payload:
        response_payload.update(runtime_payload)
    next_state['strategyResponse'] = response_payload

    saved = save_workspace_state(
        user_id=safe_user_id,
        workspace_id=safe_workspace_id,
        state=next_state,
        expected_revision=None,
    )

    runtime = state.workspace
    runtime.active_user_id = safe_user_id
    runtime.active_workspace_id = safe_workspace_id
    runtime.state = dict(saved['state'])
    runtime.revision = int(saved['revision'])
    runtime.last_saved_at = saved['updated_at']
    runtime.last_error = None

    return saved

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from .services.auth_service import (
        GUEST_DISPLAY_NEURAL_NETWORK_ID,
        GUEST_DISPLAY_OWNER_WORKSPACE_USER_ID,
        is_guest_user,
        require_request_auth,
    )
    from .neural.registry import get_neural_network
    from .services.neural_service import (
        build_neural_runtime_payload,
        cancel_neural_job,
        delete_neural_network,
        debug_neural_feature_context,
        get_neural_network_summary,
        list_neural_network_summaries,
        normalize_neural_config_payload,
        sanitize_neural_runtime,
        start_neural_test,
        start_neural_training,
        clear_neural_network_history,
        delete_neural_run_artifact,
        delete_neural_run_record,
        update_neural_network_alias,
    )
    from .services.neural_store import (
        create_neural_preset,
        delete_neural_preset,
        get_neural_run,
        list_neural_presets,
        list_neural_runs,
        set_neural_run_annotations,
        update_neural_preset,
    )
except ImportError:
    from services.auth_service import (
        GUEST_DISPLAY_NEURAL_NETWORK_ID,
        GUEST_DISPLAY_OWNER_WORKSPACE_USER_ID,
        is_guest_user,
        require_request_auth,
    )
    from neural.registry import get_neural_network
    from services.neural_service import (
        build_neural_runtime_payload,
        cancel_neural_job,
        delete_neural_network,
        debug_neural_feature_context,
        get_neural_network_summary,
        list_neural_network_summaries,
        normalize_neural_config_payload,
        sanitize_neural_runtime,
        start_neural_test,
        start_neural_training,
        clear_neural_network_history,
        delete_neural_run_artifact,
        delete_neural_run_record,
        update_neural_network_alias,
    )
    from services.neural_store import (
        create_neural_preset,
        delete_neural_preset,
        get_neural_run,
        list_neural_presets,
        list_neural_runs,
        set_neural_run_annotations,
        update_neural_preset,
    )


router = APIRouter()

GUEST_NEURAL_DROPPED_FIELDS = {
    'artifact',
    'artifact_path',
    'metadata_path',
    'source_run_id',
    'user_id',
}


def _guest_display_runtime_payload():
    payload = build_neural_runtime_payload()
    if isinstance(payload, dict):
        next_payload = dict(payload)
        next_payload['active_jobs'] = {}
        next_payload['active_counts'] = {}
        next_payload['last_error'] = None
        next_payload['recent_events'] = []
        return next_payload
    return payload


def _neural_runtime_for_user(auth_user: dict | None):
    if is_guest_user(auth_user):
        return _guest_display_runtime_payload()
    return build_neural_runtime_payload()


def _resolve_neural_read_user_id(auth_user: dict | None, network_id: str | None = None):
    if is_guest_user(auth_user):
        if network_id is not None and str(network_id or '').strip() != GUEST_DISPLAY_NEURAL_NETWORK_ID:
            raise HTTPException(status_code=404, detail={'error': f'Neural network {network_id} was not found.'})
        return GUEST_DISPLAY_OWNER_WORKSPACE_USER_ID
    return auth_user['workspace_user_id']


def _filter_guest_neural_networks(auth_user: dict | None, networks: list[dict]):
    if not is_guest_user(auth_user):
        return networks
    return [
        _scrub_guest_neural_network(network)
        for network in networks
        if str(network.get('id') or '').strip() == GUEST_DISPLAY_NEURAL_NETWORK_ID
    ]


def _scrub_guest_neural_network(network: dict):
    next_network = _scrub_guest_neural_value(network or {})
    next_network['active_job'] = None
    if isinstance(next_network.get('runs'), list):
        next_network['runs'] = [
            _scrub_guest_neural_run(run)
            for run in next_network.get('runs') or []
            if _guest_neural_run_is_displayable(run)
        ]
    return next_network


def _guest_neural_run_is_displayable(run: dict):
    status = str((run or {}).get('status') or '').strip().lower()
    error_text = str((run or {}).get('error') or '').strip()
    return status not in {'failed', 'cancelled', 'error'} and not error_text


def _scrub_guest_neural_run(run: dict):
    next_run = _scrub_guest_neural_value(run or {})
    next_run['error'] = ''
    return next_run


def _scrub_guest_neural_value(value):
    if isinstance(value, dict):
        next_value = {}
        for key, raw_value in value.items():
            if str(key or '').strip() in GUEST_NEURAL_DROPPED_FIELDS:
                continue
            if str(key or '').strip() == 'error':
                next_value[key] = ''
                continue
            next_value[key] = _scrub_guest_neural_value(raw_value)
        return next_value

    if isinstance(value, list):
        return [_scrub_guest_neural_value(item) for item in value]

    if isinstance(value, str):
        stripped = value.strip()
        if (
            stripped.startswith('/')
            or stripped.startswith('\\\\')
            or (len(stripped) >= 3 and stripped[1] == ':' and stripped[2] == '\\')
        ):
            return ''
        if '127.0.0.1' in stripped or 'localhost' in stripped:
            return ''
        return value

    return value


class NeuralTrainPayload(BaseModel):
    config: dict = Field(default_factory=dict)


class NeuralTestPayload(BaseModel):
    config: dict = Field(default_factory=dict)
    source_run_id: str | None = None


class NeuralDebugPayload(BaseModel):
    config: dict = Field(default_factory=dict)


class NeuralSanitizePayload(BaseModel):
    wait_seconds: float = Field(default=2.5, ge=0.0, le=10.0)


class NeuralRunAnnotationPayload(BaseModel):
    note: str | None = None
    is_favorite: bool | None = None
    is_baseline: bool | None = None
    is_archived: bool | None = None


class NeuralPresetPayload(BaseModel):
    name: str = Field(min_length=1)
    config: dict = Field(default_factory=dict)


class NeuralNetworkAliasPayload(BaseModel):
    alias: str | None = None
    is_favorite: bool | None = None


@router.get('/neural/runtime')
def get_neural_runtime(request: Request):
    auth_user = require_request_auth(request)
    return {
        'status': 'ok',
        'runtime': _neural_runtime_for_user(auth_user),
    }


@router.get('/neural/networks')
def get_neural_networks(request: Request):
    auth_user = require_request_auth(request)
    user_id = _resolve_neural_read_user_id(auth_user)
    networks = list_neural_network_summaries(user_id)
    return {
        'status': 'ok',
        'networks': _filter_guest_neural_networks(auth_user, networks),
        'runtime': _neural_runtime_for_user(auth_user),
    }


@router.get('/neural/networks/{network_id}')
def get_neural_network_detail(network_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = _resolve_neural_read_user_id(auth_user, network_id)
    try:
        summary = get_neural_network_summary(user_id, network_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'network': _scrub_guest_neural_network(summary) if is_guest_user(auth_user) else summary,
        'presets': list_neural_presets(user_id, network_id),
        'runtime': _neural_runtime_for_user(auth_user),
    }


@router.patch('/neural/networks/{network_id}')
def patch_neural_network_detail(network_id: str, payload: NeuralNetworkAliasPayload, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    try:
        summary = update_neural_network_alias(
            user_id,
            network_id,
            payload.alias,
            payload.is_favorite,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'network': summary,
        'presets': list_neural_presets(user_id, network_id),
        'runtime': build_neural_runtime_payload(),
    }


@router.delete('/neural/networks/{network_id}')
def delete_neural_network_detail(network_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    try:
        networks = delete_neural_network(user_id, network_id)
    except ValueError as error:
        message = str(error)
        status_code = 404 if 'Unknown neural network' in message else 409
        raise HTTPException(status_code=status_code, detail={'error': message}) from error

    return {
        'status': 'ok',
        'networks': networks,
        'runtime': build_neural_runtime_payload(),
    }


@router.get('/neural/networks/{network_id}/presets')
def get_neural_network_presets(network_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = _resolve_neural_read_user_id(auth_user, network_id)
    return {
        'status': 'ok',
        'presets': list_neural_presets(user_id, network_id),
        'runtime': _neural_runtime_for_user(auth_user),
    }


@router.post('/neural/networks/{network_id}/presets')
def post_neural_network_preset(network_id: str, payload: NeuralPresetPayload, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    try:
        sanitized_config = normalize_neural_config_payload(network_id, payload.config)
        preset = create_neural_preset(
            user_id=user_id,
            network_id=network_id,
            name=payload.name,
            config=sanitized_config,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail={'error': str(error)}) from error
    return {
        'status': 'ok',
        'preset': preset,
        'presets': list_neural_presets(user_id, network_id),
        'runtime': build_neural_runtime_payload(),
    }


@router.patch('/neural/networks/{network_id}/presets/{preset_id}')
def patch_neural_network_preset(network_id: str, preset_id: str, payload: NeuralPresetPayload, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    try:
        sanitized_config = normalize_neural_config_payload(network_id, payload.config)
        preset = update_neural_preset(
            user_id=user_id,
            network_id=network_id,
            preset_id=preset_id,
            name=payload.name,
            config=sanitized_config,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail={'error': str(error)}) from error
    if not preset:
        raise HTTPException(status_code=404, detail={'error': f'Neural preset {preset_id} was not found.'})
    return {
        'status': 'ok',
        'preset': preset,
        'presets': list_neural_presets(user_id, network_id),
        'runtime': build_neural_runtime_payload(),
    }


@router.delete('/neural/networks/{network_id}/presets/{preset_id}')
def delete_neural_network_preset(network_id: str, preset_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    delete_neural_preset(user_id, network_id, preset_id)
    return {
        'status': 'ok',
        'presets': list_neural_presets(user_id, network_id),
        'runtime': build_neural_runtime_payload(),
    }


@router.get('/neural/networks/{network_id}/runs')
def get_neural_network_runs(network_id: str, request: Request, limit: int = 20):
    auth_user = require_request_auth(request)
    user_id = _resolve_neural_read_user_id(auth_user, network_id)
    runs = list_neural_runs(user_id, network_id, limit=limit)
    if is_guest_user(auth_user):
        runs = [
            _scrub_guest_neural_run(run)
            for run in runs
            if _guest_neural_run_is_displayable(run)
        ]
    return {
        'status': 'ok',
        'runs': runs,
        'runtime': _neural_runtime_for_user(auth_user),
    }


@router.get('/neural/networks/{network_id}/runs/{run_id}')
def get_neural_network_run(network_id: str, run_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = _resolve_neural_read_user_id(auth_user, network_id)
    run = get_neural_run(run_id)

    if (
        not run
        or run['user_id'] != user_id
        or run['network_id'] != network_id
        or (is_guest_user(auth_user) and not _guest_neural_run_is_displayable(run))
    ):
        raise HTTPException(status_code=404, detail={'error': f'Neural run {run_id} was not found.'})

    return {
        'status': 'ok',
        'run': _scrub_guest_neural_run(run) if is_guest_user(auth_user) else run,
        'runtime': _neural_runtime_for_user(auth_user),
    }


@router.patch('/neural/networks/{network_id}/runs/{run_id}')
def patch_neural_network_run(network_id: str, run_id: str, payload: NeuralRunAnnotationPayload, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    run = set_neural_run_annotations(
        user_id=user_id,
        network_id=network_id,
        run_id=run_id,
        note=payload.note,
        is_favorite=payload.is_favorite,
        is_baseline=payload.is_baseline,
        is_archived=payload.is_archived,
    )

    if not run:
        raise HTTPException(status_code=404, detail={'error': f'Neural run {run_id} was not found.'})

    return {
        'status': 'ok',
        'run': run,
        'runtime': build_neural_runtime_payload(),
    }


@router.delete('/neural/networks/{network_id}/runs/{run_id}/artifact')
def delete_neural_network_run_artifact(network_id: str, run_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    try:
        summary = delete_neural_run_artifact(user_id, network_id, run_id)
    except ValueError as error:
        message = str(error)
        status_code = 404 if 'was not found' in message or 'Unknown neural network' in message else 409
        raise HTTPException(status_code=status_code, detail={'error': message}) from error

    return {
        'status': 'ok',
        'network': summary,
        'presets': list_neural_presets(user_id, network_id),
        'runtime': build_neural_runtime_payload(),
    }


@router.delete('/neural/networks/{network_id}/runs/{run_id}')
def delete_neural_network_run(network_id: str, run_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    try:
        summary = delete_neural_run_record(user_id, network_id, run_id)
    except ValueError as error:
        message = str(error)
        status_code = 404 if 'was not found' in message or 'Unknown neural network' in message else 409
        raise HTTPException(status_code=status_code, detail={'error': message}) from error

    return {
        'status': 'ok',
        'network': summary,
        'presets': list_neural_presets(user_id, network_id),
        'runtime': build_neural_runtime_payload(),
    }


@router.post('/neural/networks/{network_id}/train')
def post_neural_train(network_id: str, payload: NeuralTrainPayload, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    if not get_neural_network(network_id):
        raise HTTPException(status_code=404, detail={'error': f'Unknown neural network: {network_id}'})
    try:
        run = start_neural_training(user_id, network_id, payload.config)
    except ValueError as error:
        raise HTTPException(status_code=409, detail={'error': str(error)}) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'run': run,
        'runtime': build_neural_runtime_payload(),
    }


@router.post('/neural/networks/{network_id}/test')
def post_neural_test(network_id: str, payload: NeuralTestPayload, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    if not get_neural_network(network_id):
        raise HTTPException(status_code=404, detail={'error': f'Unknown neural network: {network_id}'})
    try:
        run = start_neural_test(user_id, network_id, payload.config, payload.source_run_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail={'error': str(error)}) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'run': run,
        'runtime': build_neural_runtime_payload(),
    }


@router.post('/neural/networks/{network_id}/cancel')
def post_neural_cancel(network_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    if not get_neural_network(network_id):
        raise HTTPException(status_code=404, detail={'error': f'Unknown neural network: {network_id}'})
    try:
        active_job = cancel_neural_job(user_id, network_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'active_job': active_job,
        'runtime': build_neural_runtime_payload(),
    }


@router.post('/neural/networks/{network_id}/sanitize')
def post_neural_sanitize(network_id: str, payload: NeuralSanitizePayload, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    if not get_neural_network(network_id):
        raise HTTPException(status_code=404, detail={'error': f'Unknown neural network: {network_id}'})

    try:
        sanitize_result = sanitize_neural_runtime(user_id, network_id, payload.wait_seconds)
    except ValueError as error:
        raise HTTPException(status_code=409, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'sanitize': sanitize_result,
        'runtime': build_neural_runtime_payload(),
    }


@router.delete('/neural/networks/{network_id}/history')
def delete_neural_network_history(network_id: str, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    if not get_neural_network(network_id):
        raise HTTPException(status_code=404, detail={'error': f'Unknown neural network: {network_id}'})
    try:
        summary = clear_neural_network_history(user_id, network_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'network': summary,
        'presets': list_neural_presets(user_id, network_id),
        'runtime': build_neural_runtime_payload(),
    }


@router.post('/neural/networks/{network_id}/debug/features')
def post_neural_debug_features(network_id: str, payload: NeuralDebugPayload, request: Request):
    auth_user = require_request_auth(request)
    user_id = auth_user['workspace_user_id']
    if not get_neural_network(network_id):
        raise HTTPException(status_code=404, detail={'error': f'Unknown neural network: {network_id}'})
    try:
        debug_payload = debug_neural_feature_context(user_id, network_id, payload.config)
    except ValueError as error:
        raise HTTPException(status_code=409, detail={'error': str(error)}) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        **debug_payload,
        'runtime': build_neural_runtime_payload(),
    }

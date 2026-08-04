import secrets

from fastapi import HTTPException, Request, WebSocket, status

from .auth_store import (
    authenticate_user,
    create_session,
    create_user,
    delete_session,
    get_session,
    get_user_by_email,
    get_user_by_id,
)
from .guest_workspace import ensure_guest_workspace
from .workspace_store import bootstrap_workspace_owner, purge_workspace_system_log


GUEST_EMAIL = 'guest@robotineeko.local'
GUEST_DISPLAY_NAME = 'Guest Recruiter'
GUEST_DISPLAY_OWNER_WORKSPACE_USER_ID = 'auth-user:demo-owner'
GUEST_DISPLAY_WORKSPACE_ID = 'default'
GUEST_DISPLAY_NEURAL_NETWORK_ID = 'temporal_cnn_indicator_fusion_v1'
GUEST_DISPLAY_RESEARCH_PAPER_ID = 32
GUEST_ACCESS_DENIAL_MESSAGE = (
    'Guest demo mode can view this surface, but cannot run heavy or operational actions on this system.'
)
GUEST_BLOCKED_PREFIXES = (
    '/trade/runtime/',
    '/neural/',
    '/workspace/state',
    '/workspace/research-',
    '/workspace/saves',
    '/workspace/saved-portfolios',
    '/workspace/strategy-benchmarks',
    '/workspace/system-log',
)
GUEST_BLOCKED_EXACT_PATHS = {
    ('POST', '/trade/runtime/configure'),
    ('POST', '/trade/runtime/arm'),
    ('POST', '/trade/runtime/arm-live-dispatch'),
    ('POST', '/trade/runtime/disarm'),
    ('POST', '/trade/runtime/disarm-live-dispatch'),
    ('POST', '/trade/runtime/evaluate'),
    ('POST', '/trade/runtime/process-intents'),
    ('POST', '/trade/runtime/reconcile'),
    ('POST', '/trade/runtime/reset-commands'),
    ('POST', '/strategy/apply'),
    ('POST', '/strategy/apply-in-context'),
    ('POST', '/strategy/debug'),
    ('POST', '/strategy/presets/compare'),
    ('POST', '/strategy/backtest/toggle'),
    ('POST', '/strategy/backtest-jobs'),
    ('POST', '/workspace/research-runtime/reconcile'),
    ('POST', '/workspace/research-runs'),
    ('POST', '/workspace/research-jobs'),
    ('POST', '/workspace/research-batches'),
    ('POST', '/workspace/research-campaigns'),
    ('POST', '/workspace/research-papers'),
    ('PUT', '/workspace/state'),
    ('PATCH', '/workspace/state'),
    ('POST', '/workspace/saved-portfolios'),
    ('POST', '/workspace/strategy-benchmarks'),
    ('POST', '/workspace/trade-reconciliations'),
    ('POST', '/market-data/request'),
    ('POST', '/chart/reload'),
    ('POST', '/chart/load-more-left'),
}
GUEST_BLOCKED_PATH_SUFFIXES = (
    '/launch',
)


def is_guest_user(user: dict | None):
    if not user:
        return False
    return (
        bool(user.get('is_guest'))
        or str(user.get('email') or '').strip().lower() == GUEST_EMAIL
    )


def build_public_user_payload(user: dict | None):
    if not user:
        return None

    is_guest = str(user['email'] or '').strip().lower() == GUEST_EMAIL
    return {
        'id': user['id'],
        'email': '' if is_guest else user['email'],
        'workspace_user_id': user['workspace_user_id'],
        'created_at': user['created_at'],
        'last_login_at': user['last_login_at'],
        'display_name': GUEST_DISPLAY_NAME if is_guest else user['email'],
        'is_guest': is_guest,
        'access_level': 'guest' if is_guest else 'owner',
    }


def extract_bearer_token(authorization_header: str | None):
    value = str(authorization_header or '').strip()
    if not value.lower().startswith('bearer '):
        return ''
    return value[7:].strip()


def get_authenticated_user_from_token(token: str | None):
    session = get_session(token or '')
    if not session:
        return None

    user = get_user_by_id(session['user_id'])
    if not user:
        delete_session(session['token'])
        return None

    return {
        'user': build_public_user_payload(user),
        'session': session,
    }


def require_request_auth(request: Request):
    auth = get_authenticated_user_from_token(
        extract_bearer_token(request.headers.get('authorization'))
    )

    if not auth:
        raise HTTPException(status_code=401, detail={'error': 'Authentication required.'})

    return auth['user']


def require_non_guest_request_auth(request: Request, action: str | None = None):
    user = require_request_auth(request)
    if is_guest_user(user):
        detail = GUEST_ACCESS_DENIAL_MESSAGE
        if action:
            detail = f'{detail} Restricted action: {action}.'
        raise HTTPException(status_code=403, detail={'error': detail, 'guest_restricted': True})
    return user


def _guest_route_is_blocked(method: str, path: str):
    safe_method = str(method or '').strip().upper()
    safe_path = str(path or '').strip() or '/'

    if (safe_method, safe_path) in GUEST_BLOCKED_EXACT_PATHS:
        return True

    if safe_method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        if any(safe_path.startswith(prefix) for prefix in GUEST_BLOCKED_PREFIXES):
            return True
        if safe_path.startswith('/strategy/backtest-jobs'):
            return True
        if safe_path.startswith('/workspace/research-campaigns') and safe_path.endswith(GUEST_BLOCKED_PATH_SUFFIXES):
            return True

    return False


def build_guest_access_denial_payload(request: Request):
    auth = get_authenticated_user_from_token(
        extract_bearer_token(request.headers.get('authorization'))
    )
    if not auth or not is_guest_user(auth.get('user')):
        return None

    if not _guest_route_is_blocked(request.method, request.url.path):
        return None

    return {
        'status': 'error',
        'error': GUEST_ACCESS_DENIAL_MESSAGE,
        'detail': {
            'error': GUEST_ACCESS_DENIAL_MESSAGE,
            'guest_restricted': True,
            'path': request.url.path,
            'method': request.method,
        },
        'guest_restricted': True,
    }


def require_websocket_auth(websocket: WebSocket):
    auth = get_authenticated_user_from_token(websocket.query_params.get('token'))

    if not auth:
        return None

    return auth['user']


async def require_websocket_auth_or_close(websocket: WebSocket):
    user = require_websocket_auth(websocket)

    if user:
        return user

    await websocket.close(
        code=status.WS_1008_POLICY_VIOLATION,
        reason='Authentication required.',
    )
    return None


def resolve_request_identity(request: Request, explicit_user_id: str | None = None):
    auth = get_authenticated_user_from_token(
        extract_bearer_token(request.headers.get('authorization'))
    )

    if auth:
        return auth['user']['workspace_user_id'], auth['user']

    return explicit_user_id or 'local-user', None


def resolve_websocket_identity(websocket: WebSocket, explicit_user_id: str | None = None):
    auth = get_authenticated_user_from_token(websocket.query_params.get('token'))

    if auth:
        return auth['user']['workspace_user_id'], auth['user']

    return explicit_user_id or 'local-user', None


def register_user_account(email: str, password: str):
    user = create_user(email, password)
    bootstrap_workspace_owner('local-user', user['workspace_user_id'])
    session = create_session(user['id'])
    safe_user = get_user_by_id(user['id'])
    return {
        'user': build_public_user_payload(safe_user),
        'session': session,
    }


def login_user_account(email: str, password: str):
    authenticated = authenticate_user(email, password)
    bootstrap_workspace_owner('local-user', authenticated['user']['workspace_user_id'])
    return {
        'user': build_public_user_payload(authenticated['user']),
        'session': authenticated['session'],
    }


def login_guest_account():
    user = get_user_by_email(GUEST_EMAIL)
    if not user:
        user = create_user(GUEST_EMAIL, secrets.token_urlsafe(32))

    ensure_guest_workspace(user['workspace_user_id'])
    purge_workspace_system_log(user['workspace_user_id'], GUEST_DISPLAY_WORKSPACE_ID)
    session = create_session(user['id'])
    safe_user = get_user_by_id(user['id'])
    return {
        'user': build_public_user_payload(safe_user),
        'session': session,
    }

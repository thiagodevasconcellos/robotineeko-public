import time
import requests

try:
    from ..config import build_trade_service_config
    from .trade_runtime_service import build_trade_runtime_payload
except ImportError:
    from config import build_trade_service_config
    from services.trade_runtime_service import build_trade_runtime_payload


TRADE_SERVICE_CONFIG = build_trade_service_config()
_LAST_TRADE_SERVICE_HEALTH = None
_LAST_TRADE_RUNTIME = None


def _trim_text(value):
    return str(value or '').strip()


def _build_trade_service_url(path: str):
    safe_path = path if str(path or '').startswith('/') else f'/{path}'
    return f"http://{TRADE_SERVICE_CONFIG['host']}:{TRADE_SERVICE_CONFIG['port']}{safe_path}"


def _build_internal_headers(extra_headers: dict | None = None):
    headers = dict(extra_headers or {})
    token = _trim_text(TRADE_SERVICE_CONFIG.get('internal_token'))
    if token:
        headers['x-robotineeko-trade-internal-token'] = token
    return headers


def _request_json(method: str, path: str, *, json_payload=None, headers: dict | None = None, timeout: float = 1.5):
    response = requests.request(
        method.upper(),
        _build_trade_service_url(path),
        json=json_payload,
        headers=headers,
        timeout=timeout,
    )
    payload = response.json()
    if not response.ok:
        error = payload.get('error') or payload.get('detail') or f'Trade service returned {response.status_code}.'
        raise RuntimeError(error if isinstance(error, str) else str(error))
    return payload


def _cache_trade_response(path: str, payload: dict | None):
    global _LAST_TRADE_SERVICE_HEALTH, _LAST_TRADE_RUNTIME

    if not isinstance(payload, dict):
        return payload

    normalized_runtime = dict(payload.get('trade_runtime') or {})

    if path == '/health':
        _LAST_TRADE_SERVICE_HEALTH = dict(payload)
        if normalized_runtime:
            _LAST_TRADE_RUNTIME = {
                'status': 'ok',
                'trade_runtime': normalized_runtime,
                'trade_service': {
                    **dict(payload.get('service') or {'reachable': True}),
                    'reachable': True,
                },
            }
    elif normalized_runtime:
        _LAST_TRADE_RUNTIME = {
            **dict(payload),
            'trade_runtime': normalized_runtime,
            'trade_service': {
                **dict((payload or {}).get('trade_service') or {}),
                'reachable': True,
            },
        }
        service_payload = {
            **dict((_LAST_TRADE_SERVICE_HEALTH or {}).get('service') or {'reachable': True}),
            'reachable': True,
        }
        _LAST_TRADE_SERVICE_HEALTH = {
            'status': 'ok',
            'service': service_payload,
            'trade_runtime': normalized_runtime,
        }

    return payload


def get_trade_runtime_via_service(fallback_local: bool = True):
    try:
        return _cache_trade_response(
            '/internal/trade/runtime',
            _request_json('GET', '/internal/trade/runtime', headers=_build_internal_headers(), timeout=5.0),
        )
    except Exception:
        if _LAST_TRADE_RUNTIME is not None:
            fallback_payload = dict(_LAST_TRADE_RUNTIME)
            fallback_payload['trade_service'] = {
                **dict(fallback_payload.get('trade_service') or {}),
                'reachable': False,
                'stale': True,
            }
            return fallback_payload
        if not fallback_local:
            raise
        return {
            'status': 'ok',
            'trade_runtime': build_trade_runtime_payload(),
            'trade_service': {
                'reachable': False,
            },
        }


def get_trade_service_health(fallback_local: bool = True):
    try:
        return _cache_trade_response(
            '/health',
            _request_json('GET', '/health', timeout=5.0),
        )
    except Exception:
        if _LAST_TRADE_RUNTIME is not None:
            runtime_payload = dict(_LAST_TRADE_RUNTIME.get('trade_runtime') or {})
            service_payload = {
                **dict((_LAST_TRADE_SERVICE_HEALTH or {}).get('service') or {}),
                'reachable': False,
                'stale': True,
            }
            return {
                'status': 'ok' if not _trim_text(runtime_payload.get('last_error')) else 'degraded',
                'service': service_payload,
                'trade_runtime': runtime_payload,
            }
        if _LAST_TRADE_SERVICE_HEALTH is not None:
            fallback_payload = dict(_LAST_TRADE_SERVICE_HEALTH)
            fallback_payload['service'] = {
                **dict(fallback_payload.get('service') or {}),
                'reachable': False,
                'stale': True,
            }
            return fallback_payload
        if not fallback_local:
            raise
        return {
            'status': 'degraded',
            'service': {
                'reachable': False,
            },
            'trade_runtime': build_trade_runtime_payload(),
        }


def forward_trade_request(method: str, path: str, *, auth_header: str | None = None, json_payload=None):
    headers = {}
    safe_auth = _trim_text(auth_header)
    if safe_auth:
        headers['authorization'] = safe_auth
    retryable_paths = {
        '/trade/runtime/arm',
        '/trade/runtime/disarm',
        '/trade/runtime/arm-live-dispatch',
        '/trade/runtime/disarm-live-dispatch',
    }
    last_error = None
    attempts = 2 if path in retryable_paths else 1
    for attempt in range(attempts):
        try:
            return _cache_trade_response(
                path,
                _request_json(method, path, json_payload=json_payload, headers=headers, timeout=8.0),
            )
        except Exception as error:
            last_error = error
            if attempt >= attempts - 1:
                raise
            time.sleep(0.25)
    raise last_error


def post_trade_internal(path: str, payload: dict | None = None, timeout: float = 1.5):
    return _request_json(
        'POST',
        path,
        json_payload=dict(payload or {}),
        headers=_build_internal_headers(),
        timeout=timeout,
    )

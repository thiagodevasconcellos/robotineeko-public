import asyncio
import json
import math
from pathlib import Path
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import requests
import websocket as websocket_client

try:
    from .services.auth_service import resolve_request_identity, resolve_websocket_identity
    from .services.auth_service import (
        GUEST_DISPLAY_OWNER_WORKSPACE_USER_ID,
        GUEST_DISPLAY_RESEARCH_PAPER_ID,
        GUEST_DISPLAY_WORKSPACE_ID,
        is_guest_user,
        require_request_auth,
        require_websocket_auth_or_close,
    )
    from .services.guest_workspace import build_guest_workspace_snapshot
    from .services.realtime_sync import realtime_sync
    from .services.research_service import (
        cancel_research_batch,
        create_research_campaign,
        delete_research_campaign,
        cancel_research_job,
        get_research_batch,
        get_research_campaign,
        get_research_job,
        launch_research_campaign,
        list_research_campaigns,
        list_research_batches,
        list_research_jobs,
        queue_research_batch,
        queue_research_job,
        reconcile_research_runtime,
        update_research_campaign,
    )
    from .services.shared_positive_history import discover_lane_roots, load_shared_positive_history_payload
    from .services.workspace_service import (
        append_and_broadcast_workspace_system_log_entries,
        build_workspace_channel_key,
        build_workspace_runtime_payload,
        create_workspace_save_snapshot,
        delete_workspace_save_snapshot,
        get_workspace_system_log_payload,
        get_workspace_save_snapshot,
        list_workspace_save_summaries,
        load_workspace_runtime,
        overwrite_workspace_save_snapshot,
        rename_workspace_save_snapshot,
        restore_workspace_save_snapshot,
        save_and_broadcast_workspace_patch,
        save_and_broadcast_workspace_state,
        start_and_broadcast_workspace_system_log_session,
    )
    from .services.workspace_store import load_workspace_state
    from .services.workspace_store import (
        delete_workspace_research_batch,
        create_workspace_broker_profile,
        create_workspace_saved_portfolio,
        create_workspace_strategy_benchmark,
        create_workspace_research_paper,
        create_workspace_research_run,
        delete_workspace_broker_profile,
        delete_workspace_research_job,
        delete_workspace_research_paper,
        delete_workspace_saved_portfolio,
        delete_workspace_strategy_benchmark,
        delete_workspace_research_run,
        get_workspace_broker_profile_by_id,
        get_workspace_research_paper,
        get_workspace_research_run,
        get_workspace_research_job,
        list_workspace_research_papers,
        list_workspace_broker_profiles,
        list_workspace_saved_portfolios,
        list_workspace_strategy_benchmarks,
        list_workspace_live_trades,
        list_workspace_system_log_sessions,
        create_workspace_trade_reconciliation,
        list_workspace_trade_reconciliations,
        list_workspace_research_jobs,
        list_workspace_research_runs,
        update_workspace_research_job,
        update_workspace_broker_profile,
        update_workspace_research_paper,
        update_workspace_saved_portfolio,
        update_workspace_strategy_benchmark,
        update_workspace_research_run,
    )
except ImportError:
    from services.auth_service import resolve_request_identity, resolve_websocket_identity
    from services.auth_service import (
        GUEST_DISPLAY_OWNER_WORKSPACE_USER_ID,
        GUEST_DISPLAY_RESEARCH_PAPER_ID,
        GUEST_DISPLAY_WORKSPACE_ID,
        is_guest_user,
        require_request_auth,
        require_websocket_auth_or_close,
    )
    from services.guest_workspace import build_guest_workspace_snapshot
    from services.realtime_sync import realtime_sync
    from services.research_service import (
        cancel_research_batch,
        create_research_campaign,
        delete_research_campaign,
        cancel_research_job,
        get_research_batch,
        get_research_campaign,
        get_research_job,
        launch_research_campaign,
        list_research_campaigns,
        list_research_batches,
        list_research_jobs,
        queue_research_batch,
        queue_research_job,
        reconcile_research_runtime,
        update_research_campaign,
    )
    from services.shared_positive_history import discover_lane_roots, load_shared_positive_history_payload
    from services.workspace_service import (
        append_and_broadcast_workspace_system_log_entries,
        build_workspace_channel_key,
        build_workspace_runtime_payload,
        create_workspace_save_snapshot,
        delete_workspace_save_snapshot,
        get_workspace_system_log_payload,
        get_workspace_save_snapshot,
        list_workspace_save_summaries,
        load_workspace_runtime,
        overwrite_workspace_save_snapshot,
        rename_workspace_save_snapshot,
        restore_workspace_save_snapshot,
        save_and_broadcast_workspace_patch,
        save_and_broadcast_workspace_state,
        start_and_broadcast_workspace_system_log_session,
    )
    from services.workspace_store import load_workspace_state
    from services.workspace_store import (
        delete_workspace_research_batch,
        create_workspace_broker_profile,
        create_workspace_saved_portfolio,
        create_workspace_strategy_benchmark,
        create_workspace_research_paper,
        create_workspace_research_run,
        delete_workspace_broker_profile,
        delete_workspace_research_job,
        delete_workspace_research_paper,
        delete_workspace_saved_portfolio,
        delete_workspace_strategy_benchmark,
        delete_workspace_research_run,
        get_workspace_broker_profile_by_id,
        get_workspace_research_paper,
        get_workspace_research_run,
        get_workspace_research_job,
        list_workspace_research_papers,
        list_workspace_broker_profiles,
        list_workspace_saved_portfolios,
        list_workspace_strategy_benchmarks,
        list_workspace_live_trades,
        list_workspace_system_log_sessions,
        create_workspace_trade_reconciliation,
        list_workspace_trade_reconciliations,
        list_workspace_research_jobs,
        list_workspace_research_runs,
        update_workspace_research_job,
        update_workspace_broker_profile,
        update_workspace_research_paper,
        update_workspace_saved_portfolio,
        update_workspace_strategy_benchmark,
        update_workspace_research_run,
    )


router = APIRouter()
RESEARCH_ARTIFACTS_ROOT = Path(__file__).resolve().parent / 'data' / 'research'


class WorkspaceStatePayload(BaseModel):
    state: dict
    expected_revision: int | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    source: str = 'api'


class WorkspacePatchPayload(BaseModel):
    patch: dict
    expected_revision: int | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    source: str = 'api_patch'


class WorkspaceSavePayload(BaseModel):
    name: str
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceSystemLogEntryPayload(BaseModel):
    client_entry_id: str | None = None
    message: str
    level: str | None = None
    source: str | None = None
    scope: str | None = None
    category: str | None = None
    context: dict | None = None
    created_at: float | None = None


class WorkspaceSystemLogAppendPayload(BaseModel):
    entries: list[WorkspaceSystemLogEntryPayload]
    session_id: int | None = None
    label: str | None = None
    metadata: dict | None = None
    source: str = 'system_log_ui'
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceSystemLogStartPayload(BaseModel):
    label: str | None = None
    metadata: dict | None = None
    source: str = 'system_log_ui'
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceResearchRunPayload(BaseModel):
    run_type: str
    side: str | None = None
    run_name: str
    version: str | None = None
    best_id: str | None = None
    best_label: str | None = None
    comparison_count: int | None = None
    run_label: str | None = None
    run_notes: str | None = None
    pinned: bool | None = None
    payload: dict | list | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceResearchRunPatchPayload(BaseModel):
    run_label: str | None = None
    run_notes: str | None = None
    pinned: bool | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceResearchJobPayload(BaseModel):
    job_type: str = 'preset_compare'
    request: dict
    run_label: str | None = None
    run_notes: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceResearchBatchJobPayload(BaseModel):
    job_type: str = 'preset_compare'
    request: dict
    run_label: str | None = None
    run_notes: str | None = None


class WorkspaceResearchBatchPayload(BaseModel):
    label: str
    jobs: list[WorkspaceResearchBatchJobPayload]
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceResearchCampaignPayload(BaseModel):
    label: str
    description: str | None = None
    jobs: list[WorkspaceResearchBatchJobPayload]
    batch_jobs: list[dict] | None = None
    shared_features: list[dict] | None = None
    options: dict | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceResearchCampaignPatchPayload(BaseModel):
    label: str | None = None
    description: str | None = None
    jobs: list[WorkspaceResearchBatchJobPayload] | None = None
    batch_jobs: list[dict] | None = None
    shared_features: list[dict] | None = None
    options: dict | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceStrategyBenchmarkPayload(BaseModel):
    label: str
    side: str | None = None
    source: str | None = None
    notes: str | None = None
    is_favorite: bool | None = None
    broker_profile_id: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    strategy: dict | None = None
    strategies: list[dict] | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceStrategyBenchmarkPatchPayload(BaseModel):
    label: str | None = None
    side: str | None = None
    source: str | None = None
    notes: str | None = None
    is_favorite: bool | None = None
    broker_profile_id: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceSavedPortfolioPayload(BaseModel):
    label: str
    source: str | None = None
    notes: str | None = None
    is_favorite: bool | None = None
    broker_profile_id: str | None = None
    portfolio: dict | None = None
    capitalModel: dict | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceSavedPortfolioPatchPayload(BaseModel):
    label: str | None = None
    source: str | None = None
    notes: str | None = None
    is_favorite: bool | None = None
    portfolio: dict | None = None
    capitalModel: dict | None = None
    broker_profile_id: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceBrokerProfilePayload(BaseModel):
    label: str
    broker_code: str | None = None
    connector_kind: str | None = None
    server_name: str | None = None
    market_domain: str | None = None
    base_currency: str | None = None
    notes: str | None = None
    is_default: bool | None = None
    is_favorite: bool | None = None
    profile: dict | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceBrokerProfilePatchPayload(BaseModel):
    label: str | None = None
    broker_code: str | None = None
    connector_kind: str | None = None
    server_name: str | None = None
    market_domain: str | None = None
    base_currency: str | None = None
    notes: str | None = None
    is_default: bool | None = None
    is_favorite: bool | None = None
    profile: dict | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspacePositiveHistoryWinnerSavePayload(BaseModel):
    entry: dict
    is_favorite: bool | None = None
    user_id: str | None = None
    workspace_id: str | None = None


BROKER_PROFILE_PROXY_ALLOWED_HTTP_PREFIXES = (
    'auth/',
    'chart/',
    'fund/',
    'health',
    'neural/',
    'strategy/',
    'system/',
    'trade/',
    'workspace/',
)
BROKER_PROFILE_PROXY_ALLOWED_WS_CHANNELS = {'market', 'strategy', 'workspace'}
BROKER_PROFILE_PROXY_REQUEST_TIMEOUT_SECONDS = 60.0
BROKER_PROFILE_PROXY_WS_CONNECT_TIMEOUT_SECONDS = 5.0
BROKER_PROFILE_PROXY_FORWARD_RESPONSE_HEADERS = {
    'cache-control',
    'content-disposition',
    'content-type',
    'etag',
    'last-modified',
}
BROKER_PROFILE_PROXY_EXCLUDED_REQUEST_HEADERS = {
    'connection',
    'content-length',
    'host',
    'transfer-encoding',
    'upgrade',
}


def _trim_broker_proxy_text(value):
    return str(value or '').strip()


def _normalize_broker_profile_proxy_path(proxy_path: str | None):
    return str(proxy_path or '').lstrip('/').strip()


def _is_allowed_broker_profile_proxy_path(proxy_path: str | None):
    safe_path = _normalize_broker_profile_proxy_path(proxy_path)
    if not safe_path:
        return False
    return any(
        safe_path == prefix or safe_path.startswith(prefix)
        for prefix in BROKER_PROFILE_PROXY_ALLOWED_HTTP_PREFIXES
    )


def _resolve_broker_profile_proxy_target(broker_profile_id: str | int | None):
    profile = get_workspace_broker_profile_by_id(broker_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail={'error': f'Broker profile {broker_profile_id} was not found'})

    safe_profile = dict(profile or {})
    safe_profile_config = dict(safe_profile.get('profile') or {})
    api_base_url = _trim_broker_proxy_text(
        safe_profile_config.get('api_base_url', safe_profile_config.get('apiBaseUrl'))
    ).rstrip('/')
    if not api_base_url:
        raise HTTPException(
            status_code=400,
            detail={'error': f'Broker profile {broker_profile_id} does not define an api_base_url.'},
        )

    return safe_profile, api_base_url


def _build_broker_profile_proxy_upstream_url(api_base_url: str, proxy_path: str, query_string: str = ''):
    safe_path = _normalize_broker_profile_proxy_path(proxy_path)
    upstream_url = f"{str(api_base_url or '').rstrip('/')}/{safe_path}"
    safe_query_string = _trim_broker_proxy_text(query_string)
    if safe_query_string:
        upstream_url = f'{upstream_url}?{safe_query_string}'
    return upstream_url


def _build_broker_profile_proxy_request_headers(request: Request):
    headers = {}
    for header_name, header_value in request.headers.items():
        safe_name = str(header_name or '').strip()
        if not safe_name or safe_name.lower() in BROKER_PROFILE_PROXY_EXCLUDED_REQUEST_HEADERS:
            continue
        headers[safe_name] = header_value
    return headers


def _build_broker_profile_proxy_response_headers(upstream_response):
    headers = {}
    for header_name, header_value in upstream_response.headers.items():
        safe_name = str(header_name or '').strip()
        if safe_name.lower() not in BROKER_PROFILE_PROXY_FORWARD_RESPONSE_HEADERS:
            continue
        headers[safe_name] = header_value
    return headers


def _build_broker_profile_proxy_websocket_url(api_base_url: str, channel: str, query_string: str = ''):
    normalized_base = str(api_base_url or '').strip().rstrip('/')
    if normalized_base.startswith('https://'):
        websocket_base = f"wss://{normalized_base.removeprefix('https://')}"
    elif normalized_base.startswith('http://'):
        websocket_base = f"ws://{normalized_base.removeprefix('http://')}"
    elif normalized_base.startswith('wss://') or normalized_base.startswith('ws://'):
        websocket_base = normalized_base
    else:
        websocket_base = f'ws://{normalized_base.lstrip("/")}'

    upstream_url = f"{websocket_base}/ws/{channel}"
    safe_query_string = _trim_broker_proxy_text(query_string)
    if safe_query_string:
        upstream_url = f'{upstream_url}?{safe_query_string}'
    return upstream_url


def _positive_history_sanitize_path_fragment(value: str):
    safe = ''.join(
        character if character.isalnum() or character in ('-', '_') else '_'
        for character in str(value or '').strip()
    )
    return safe or 'default'


def _positive_history_extract_numeric_metric(text_value: str | None, suffix_pattern: str):
    text = str(text_value or '')
    match = re.search(rf'([+-]?\d+(?:\.\d+)?)\s*{suffix_pattern}', text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _positive_history_extract_paper_number(*values):
    for value in values:
        match = re.search(r'paper[\s_:-]*(\d+)', str(value or ''), flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _positive_history_extract_candidate_id(*values):
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        candidate_match = re.search(r'candidate:([A-Za-z0-9_-]+)', text, flags=re.IGNORECASE)
        if candidate_match:
            return candidate_match.group(1).strip().lower()
        row_match = re.search(r'row\s*`?([A-Za-z0-9_-]+)`?', text, flags=re.IGNORECASE)
        if row_match:
            return row_match.group(1).strip().lower()
        normalized = text.lower()
        if normalized.startswith('s') and normalized[1:].isdigit():
            return normalized
    return ''


def _iter_positive_history_research_roots():
    yielded: set[Path] = set()

    def _yield_root(candidate: Path | None):
        if candidate is None:
            return
        resolved = candidate.resolve()
        if resolved in yielded or not resolved.is_dir():
            return
        yielded.add(resolved)
        return resolved

    try:
        for repo_root in discover_lane_roots():
            resolved_root = _yield_root(Path(repo_root) / 'backend' / 'python' / 'data' / 'research')
            if resolved_root is not None:
                yield resolved_root
    except Exception:
        pass

    fallback_root = _yield_root(RESEARCH_ARTIFACTS_ROOT)
    if fallback_root is not None:
        yield fallback_root


def _positive_history_build_strategy_payload(strategy_payload: dict | None, indicators: list | None):
    safe_strategy = dict(strategy_payload or {})
    safe_strategy['featureManifest'] = {
        'indicators': list(indicators or []),
    }
    return safe_strategy


def _positive_history_extract_strategy_expressions(strategy_payload: dict | None):
    safe_strategy = dict(strategy_payload or {})
    expressions: list[str] = []
    for side_name in ('long', 'short'):
        side_payload = safe_strategy.get(side_name)
        if not isinstance(side_payload, dict):
            continue
        for field_name in ('openIf', 'closeIf', 'openPrice', 'closePrice', 'gainPrice', 'lossPrice', 'trailingPrice'):
            field_value = side_payload.get(field_name)
            if isinstance(field_value, str) and field_value.strip():
                expressions.append(field_value.strip())
    return expressions


def _positive_history_coerce_indicator_param(value: str):
    text = str(value or '').strip()
    if not text:
        return text
    if re.fullmatch(r'-?\d+', text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r'-?\d+\.\d+', text):
        try:
            number = float(text)
        except ValueError:
            return text
        if number.is_integer():
            return int(number)
        return number
    return text


def _positive_history_parse_market_regime_params(expressions: list[str]):
    line_suffixes = (
        'trend_score',
        'volatility_score',
        'compression_score',
        'direction_score',
        'stability_score',
        'regime_age',
        'regime_code',
    )
    pattern = re.compile(
        rf'\bMarketRegime_([A-Za-z0-9_.-]+?)_(?:{"|".join(line_suffixes)})\b'
    )
    for expression in expressions:
        if not isinstance(expression, str) or not expression.strip():
            continue
        for match in pattern.finditer(expression):
            raw_params = [token for token in str(match.group(1) or '').split('_') if token != '']
            if len(raw_params) < 13:
                continue
            return [_positive_history_coerce_indicator_param(token) for token in raw_params[:13]]
    return None


def _positive_history_register_indicator(registry: dict, name: str, params: list | None = None, alias: str = ''):
    safe_name = str(name or '').strip()
    safe_params = list(params or [])
    safe_alias = str(alias or '').strip()
    if not safe_name:
        return
    key = (safe_name, tuple(safe_params))
    existing = registry.get(key)
    if existing is None:
        registry[key] = {
            'name': safe_name,
            'params': safe_params,
            'alias': safe_alias,
        }
        return
    if safe_alias and not str(existing.get('alias') or '').strip():
        existing['alias'] = safe_alias


def _positive_history_infer_indicators_from_strategy_payload(
    strategy_payload: dict | None,
    resolved_strategy_params: dict | None = None,
):
    raw_expressions = _positive_history_extract_strategy_expressions(strategy_payload)
    resolved_expressions = [
        str(value).strip()
        for value in dict(resolved_strategy_params or {}).values()
        if isinstance(value, str) and str(value).strip()
    ]
    all_expressions = [*raw_expressions, *resolved_expressions]
    registry: dict[tuple[str, tuple], dict] = {}
    market_regime_params = _positive_history_parse_market_regime_params(all_expressions) or [
        55, 21, 14, 14, 20, 2, 20, 14, 10, 3, 'hlc3', 5, 3,
    ]

    for expression in raw_expressions:
        for match in re.finditer(r'\bema(\d+)\b', expression, flags=re.IGNORECASE):
            period = int(match.group(1))
            _positive_history_register_indicator(registry, 'EMA', ['close', period], f'ema{period}')
        for match in re.finditer(r'\brsi(\d+)\b', expression, flags=re.IGNORECASE):
            period = int(match.group(1))
            _positive_history_register_indicator(registry, 'RSI', ['close', period], f'rsi{period}')
        for match in re.finditer(r'\batr(\d+)\b', expression, flags=re.IGNORECASE):
            period = int(match.group(1))
            _positive_history_register_indicator(registry, 'ATR', [period], f'atr{period}')
        for match in re.finditer(r'\badx(\d+)\b', expression, flags=re.IGNORECASE):
            period = int(match.group(1))
            _positive_history_register_indicator(registry, 'ADX', [period], f'adx{period}')
        for match in re.finditer(r'\broc(\d+)\b', expression, flags=re.IGNORECASE):
            period = int(match.group(1))
            _positive_history_register_indicator(registry, 'ROC', ['close', period], f'roc{period}')
        for match in re.finditer(r'\bchop(\d+)(?:_value)?\b', expression, flags=re.IGNORECASE):
            period = int(match.group(1))
            _positive_history_register_indicator(registry, 'ChoppinessIndex', [period], f'chop{period}')
        for match in re.finditer(r'\bdc(\d+)_(?:upper|middle|lower|width)\b', expression, flags=re.IGNORECASE):
            period = int(match.group(1))
            _positive_history_register_indicator(registry, 'DonchianChannels', [period], f'dc{period}')
        if re.search(r'\bbb_(?:upper|middle|lower|width)\b', expression, flags=re.IGNORECASE):
            _positive_history_register_indicator(registry, 'BollingerBands', ['close', 20, 2], 'bb')
        if re.search(r'\bvwap(?:_(?:distance|distance_ratio))?\b', expression, flags=re.IGNORECASE):
            _positive_history_register_indicator(registry, 'VWAP', ['hlc3'], 'vwap')
        if re.search(
            r'\b(?:reg|mreg)_(?:trend_score|volatility_score|compression_score|direction_score|stability_score|regime_age|regime_code)\b',
            expression,
            flags=re.IGNORECASE,
        ):
            alias = 'mreg' if re.search(r'\bmreg_', expression, flags=re.IGNORECASE) else 'reg'
            _positive_history_register_indicator(registry, 'MarketRegime', market_regime_params, alias)
        if re.search(
            r'\b(?:tc|TemporalContext)_(?:hour_utc|minute_utc|minute_of_day_utc|weekday_utc|day_of_month_utc|tokyo_session|london_session|new_york_session|tokyo_london_overlap|london_new_york_overlap)\b',
            expression,
            flags=re.IGNORECASE,
        ):
            alias = 'tc' if re.search(r'\btc_', expression, flags=re.IGNORECASE) else ''
            _positive_history_register_indicator(registry, 'TemporalContext', [], alias)

    for expression in all_expressions:
        for match in re.finditer(r'\bEMA_([A-Za-z0-9]+)_(\d+)\b', expression):
            _positive_history_register_indicator(
                registry,
                'EMA',
                [match.group(1), int(match.group(2))],
            )
        for match in re.finditer(r'\bRSI_([A-Za-z0-9]+)_(\d+)\b', expression):
            _positive_history_register_indicator(
                registry,
                'RSI',
                [match.group(1), int(match.group(2))],
            )
        for match in re.finditer(r'\bATR_(\d+)\b', expression):
            _positive_history_register_indicator(registry, 'ATR', [int(match.group(1))])
        for match in re.finditer(r'\bADX_(\d+)\b', expression):
            _positive_history_register_indicator(registry, 'ADX', [int(match.group(1))])
        for match in re.finditer(r'\bROC_([A-Za-z0-9]+)_(\d+)\b', expression):
            _positive_history_register_indicator(
                registry,
                'ROC',
                [match.group(1), int(match.group(2))],
            )
        for match in re.finditer(r'\bChoppinessIndex_(\d+)\b', expression):
            _positive_history_register_indicator(registry, 'ChoppinessIndex', [int(match.group(1))])
        for match in re.finditer(r'\bDonchianChannels_(\d+)_(?:upper|middle|lower|width)\b', expression):
            _positive_history_register_indicator(registry, 'DonchianChannels', [int(match.group(1))])
        for match in re.finditer(r'\bBollingerBands_([A-Za-z0-9]+)_(\d+)_(\d+(?:\.\d+)?)_(?:middle|upper|lower|width)\b', expression):
            _positive_history_register_indicator(
                registry,
                'BollingerBands',
                [
                    match.group(1),
                    int(match.group(2)),
                    _positive_history_coerce_indicator_param(match.group(3)),
                ],
            )
        for match in re.finditer(r'\bVWAP_([A-Za-z0-9]+)_(?:value|distance|distance_ratio)\b', expression):
            _positive_history_register_indicator(registry, 'VWAP', [match.group(1)])
        if re.search(
            r'\bTemporalContext_(?:hour_utc|minute_utc|minute_of_day_utc|weekday_utc|day_of_month_utc|tokyo_session|london_session|new_york_session|tokyo_london_overlap|london_new_york_overlap)\b',
            expression,
        ):
            _positive_history_register_indicator(registry, 'TemporalContext', [])
        if re.search(
            r'\bMarketRegime_[A-Za-z0-9_.-]+?_(?:trend_score|volatility_score|compression_score|direction_score|stability_score|regime_age|regime_code)\b',
            expression,
        ):
            _positive_history_register_indicator(registry, 'MarketRegime', market_regime_params)

    return list(registry.values())


def _positive_history_build_strategy_collection(candidate: dict | None):
    safe_candidate = dict(candidate or {})
    explicit_indicators = safe_candidate.get('indicators') if isinstance(safe_candidate.get('indicators'), list) else []
    explicit_strategy_payload = safe_candidate.get('strategy_payload') if isinstance(safe_candidate.get('strategy_payload'), dict) else {}

    if explicit_strategy_payload:
        return (
            _positive_history_build_strategy_payload(explicit_strategy_payload, explicit_indicators),
            [],
        )

    raw_entries = safe_candidate.get('strategy_entries') if isinstance(safe_candidate.get('strategy_entries'), list) else []
    resolved_entries = safe_candidate.get('resolved_strategy_entries') if isinstance(safe_candidate.get('resolved_strategy_entries'), list) else []
    if not raw_entries and not resolved_entries:
        return _positive_history_build_strategy_payload({}, []), []

    raw_entry_by_id = {}
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            continue
        safe_id = str(entry.get('id') or entry.get('strategy_id') or f'strategy-{index + 1}').strip()
        raw_entry_by_id[safe_id] = entry

    normalized_entries = []
    base_entries = resolved_entries or raw_entries

    for index, entry in enumerate(base_entries):
        if not isinstance(entry, dict):
            continue
        safe_id = str(
            entry.get('strategy_id')
            or entry.get('id')
            or f'strategy-{index + 1}'
        ).strip() or f'strategy-{index + 1}'
        raw_entry = raw_entry_by_id.get(safe_id)
        if raw_entry is None and index < len(raw_entries) and isinstance(raw_entries[index], dict):
            raw_entry = raw_entries[index]
        safe_raw_entry = dict(raw_entry or {})
        raw_strategy_payload = (
            safe_raw_entry.get('strategy_payload')
            if isinstance(safe_raw_entry.get('strategy_payload'), dict)
            else entry.get('strategy_payload')
        )
        if not isinstance(raw_strategy_payload, dict):
            raw_strategy_payload = {}
        resolved_strategy_params = (
            entry.get('resolved_strategy_params')
            if isinstance(entry.get('resolved_strategy_params'), dict)
            else {}
        )
        entry_indicators = (
            safe_raw_entry.get('indicators')
            if isinstance(safe_raw_entry.get('indicators'), list)
            else []
        )
        if not entry_indicators:
            entry_indicators = _positive_history_infer_indicators_from_strategy_payload(
                raw_strategy_payload,
                resolved_strategy_params,
            )
        normalized_entries.append({
            'id': safe_id,
            'label': str(
                entry.get('strategy_label')
                or entry.get('label')
                or safe_raw_entry.get('label')
                or f'Strategy {index + 1}'
            ).strip() or f'Strategy {index + 1}',
            'priority': int(entry.get('priority') if entry.get('priority') is not None else index),
            'enabled': bool(entry.get('enabled') if entry.get('enabled') is not None else True),
            'strategy': _positive_history_build_strategy_payload(raw_strategy_payload, entry_indicators),
        })

    normalized_entries.sort(key=lambda item: (int(item.get('priority') or 0), str(item.get('id') or '')))
    preferred_label = str(safe_candidate.get('label') or '').strip()
    normalized_preferred_label = _positive_history_normalize_benchmark_label(preferred_label)
    if normalized_preferred_label and len(normalized_entries) > 1:
        best_index = 0
        best_score = -1.0
        for index, entry in enumerate(normalized_entries):
            current_score = 0.0
            current_label = _positive_history_normalize_benchmark_label(entry.get('label'))
            if current_label:
                if current_label == normalized_preferred_label:
                    current_score = 1000.0
                elif (
                    current_label in normalized_preferred_label
                    or normalized_preferred_label in current_label
                ):
                    current_score = 600.0
                else:
                    preferred_tokens = set(normalized_preferred_label.split())
                    candidate_tokens = set(current_label.split())
                    for token in preferred_tokens:
                        if token not in candidate_tokens:
                            continue
                        current_score += 5.0 if re.search(r'\d', token) else 2.0
            if current_score > best_score:
                best_score = current_score
                best_index = index

        if best_index > 0 and best_score > 0:
            normalized_entries = [
                normalized_entries[best_index],
                *normalized_entries[:best_index],
                *normalized_entries[best_index + 1:],
            ]
            normalized_entries = [
                {
                    **entry,
                    'priority': index,
                }
                for index, entry in enumerate(normalized_entries)
            ]

    primary_strategy = dict(normalized_entries[0].get('strategy') or {}) if normalized_entries else {}
    companion_entries = [dict(entry) for entry in normalized_entries[1:]]
    return primary_strategy, companion_entries


def _positive_history_canonical_signature(symbol: str, timeframe: str, strategy: dict, strategies: list | None = None):
    return json.dumps(
        {
            'symbol': str(symbol or '').strip().upper(),
            'timeframe': str(timeframe or '').strip().upper(),
            'strategy': strategy or {},
            'strategies': list(strategies or []),
        },
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    )


def _positive_history_normalize_benchmark_label(value: str | None):
    text = re.sub(r'^(?:paper\d+|benchmark-\d+)\s*·\s*', '', str(value or '').strip(), flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip().lower()


def _iter_positive_history_candidate_artifacts(user_id: str, paper_id: int | None):
    safe_user_fragment = _positive_history_sanitize_path_fragment(user_id)
    for research_root in _iter_positive_history_research_roots():
        user_root = research_root / safe_user_fragment
        if not user_root.exists():
            continue

        for artifact_path in sorted(user_root.rglob('*.json')):
            artifact_name = artifact_path.name.lower()
            if 'scientific_record_payload' in artifact_name:
                continue
            if paper_id is not None:
                try:
                    with artifact_path.open('rb') as handle:
                        prefix = handle.read(4096)
                except Exception:
                    continue
                spaced_token = f'"paper_id": {paper_id}'.encode('utf-8')
                compact_token = f'"paper_id":{paper_id}'.encode('utf-8')
                if spaced_token not in prefix and compact_token not in prefix:
                    continue
            try:
                payload = json.loads(artifact_path.read_text(encoding='utf-8'))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            candidates = payload.get('candidates')
            if not isinstance(candidates, list) or not candidates:
                continue
            if paper_id is not None:
                try:
                    current_paper_id = int(payload.get('paper_id'))
                except (TypeError, ValueError):
                    continue
                if current_paper_id != paper_id:
                    continue
            yield artifact_path, payload


def _resolve_positive_history_candidate(user_id: str, entry: dict):
    explicit_paper_id = None
    try:
        explicit_paper_id = int(entry.get('paperId'))
    except (TypeError, ValueError, AttributeError):
        explicit_paper_id = None
    if explicit_paper_id is None:
        try:
            explicit_paper_id = int(entry.get('paper_id'))
        except (TypeError, ValueError, AttributeError):
            explicit_paper_id = None

    paper_id = explicit_paper_id if explicit_paper_id is not None else _positive_history_extract_paper_number(
        entry.get('sharedRegistryKey'),
        entry.get('id'),
        entry.get('label'),
        entry.get('study'),
        entry.get('checkpointContext'),
    )
    target_candidate_id = _positive_history_extract_candidate_id(
        entry.get('candidateId'),
        entry.get('candidate_id'),
        entry.get('sharedRegistryKey'),
        entry.get('checkpointContext'),
        entry.get('id'),
    )
    normalized_entry_label = _positive_history_normalize_benchmark_label(entry.get('label'))
    target_symbol = str(entry.get('symbol') or '').strip().upper()
    target_timeframe = str(entry.get('timeframe') or '').strip().upper()
    target_trades = None
    if entry.get('trades') is not None:
        try:
            target_trades = int(entry.get('trades'))
        except (TypeError, ValueError):
            target_trades = None
    target_net = _positive_history_extract_numeric_metric(entry.get('positiveCheckpoint'), 'net')
    target_monthly = _positive_history_extract_numeric_metric(
        entry.get('positiveCheckpoint'),
        '(?:per\\s+month|month|monthly)',
    )

    matches: list[tuple[float, Path, dict, int | None]] = []

    for artifact_path, artifact_payload in _iter_positive_history_candidate_artifacts(user_id, paper_id):
        for candidate in artifact_payload.get('candidates') or []:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get('candidate_id') or '').strip().lower()
            if target_candidate_id and candidate_id and candidate_id != target_candidate_id:
                continue

            candidate_symbol = str(candidate.get('symbol') or '').strip().upper()
            candidate_timeframe = str(candidate.get('timeframe') or '').strip().upper()
            if target_symbol and candidate_symbol != target_symbol:
                continue
            if target_timeframe and candidate_timeframe != target_timeframe:
                continue

            candidate_summary = candidate.get('candidate_summary') or {}
            try:
                candidate_trades = int(candidate_summary.get('n_trades'))
            except (TypeError, ValueError):
                candidate_trades = None
            try:
                candidate_net = float(candidate_summary.get('net_pnl'))
            except (TypeError, ValueError):
                candidate_net = None
            try:
                candidate_monthly = float(candidate_summary.get('monthly_projection'))
            except (TypeError, ValueError):
                candidate_monthly = None

            candidate_label = _positive_history_normalize_benchmark_label(candidate.get('label'))
            if target_trades is not None and candidate_trades != target_trades:
                continue
            if target_net is not None and (candidate_net is None or abs(candidate_net - target_net) > 0.2):
                continue
            if target_monthly is not None and (candidate_monthly is None or abs(candidate_monthly - target_monthly) > 0.2):
                continue

            score = 0.0
            if candidate_net is not None and target_net is not None:
                score += abs(candidate_net - target_net)
            if candidate_monthly is not None and target_monthly is not None:
                score += abs(candidate_monthly - target_monthly)
            if candidate_trades is not None and target_trades is not None:
                score += abs(candidate_trades - target_trades) * 10
            if normalized_entry_label and candidate_label and candidate_label != normalized_entry_label:
                score += 1000
            if target_candidate_id and candidate_id == target_candidate_id:
                score -= 100000

            matches.append((score, artifact_path, candidate, paper_id))

    if not matches:
        return None

    matches.sort(key=lambda item: (item[0], str(item[1])))
    _, artifact_path, candidate, resolved_paper_id = matches[0]
    artifact_payload = None
    for current_artifact_path, current_artifact_payload in _iter_positive_history_candidate_artifacts(user_id, resolved_paper_id):
        if str(current_artifact_path) == str(artifact_path):
            artifact_payload = current_artifact_payload
            break
    return {
        'artifact_path': str(artifact_path),
        'paper_id': resolved_paper_id,
        'candidate': candidate,
        'artifact_payload': artifact_payload if isinstance(artifact_payload, dict) else {},
    }


def _positive_history_normalize_scope_key(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').strip().lower())


def _positive_history_normalize_broker_code(value):
    normalized = _positive_history_normalize_scope_key(value)
    if normalized == 'forexcom':
        return 'forex.com'
    if normalized == 'oanda':
        return 'oanda'
    if normalized == 'clear':
        return 'clear'
    return str(value or '').strip().lower()


def _positive_history_normalize_market_domain(value):
    normalized = _positive_history_normalize_scope_key(value)
    if normalized in {'forex', 'fx'}:
        return 'forex'
    if normalized in {'b3', 'bovespa', 'brasil', 'brazil'}:
        return 'b3'
    return str(value or '').strip().lower()


def _positive_history_infer_market_domain_from_symbol(symbol: str | None):
    safe_symbol = str(symbol or '').strip().upper()
    if not safe_symbol or safe_symbol == 'MULTI-SYMBOL AGGREGATE':
        return ''
    tokens = [token.strip() for token in safe_symbol.split('+') if token.strip()]
    if not tokens:
        return ''
    inferred_domains = set()
    for token in tokens:
        if re.fullmatch(r'[A-Z]{6}', token):
            inferred_domains.add('forex')
            continue
        if (
            re.match(r'^(WIN|WDO|IND|DOL|BGI|CCM|ICF|SJC|DI1|DAP|FRC)', token)
            or re.fullmatch(r'[A-Z0-9]{4}\d{1,2}', token)
            or re.fullmatch(r'[A-Z0-9]{4}\d{1,2}[A-Z]\d?', token)
            or re.fullmatch(r'[A-Z0-9]{4}11', token)
        ):
            inferred_domains.add('b3')
    if len(inferred_domains) == 1:
        return next(iter(inferred_domains))
    if len(inferred_domains) > 1:
        return 'mixed'
    return ''


def _positive_history_resolve_broker_scope(
    user_id: str,
    workspace_id: str,
    candidate_symbol: str,
    candidate: dict | None,
    artifact_payload: dict | None,
):
    contexts = []
    artifact_backtest_params = (artifact_payload or {}).get('backtest_params')
    if isinstance(artifact_backtest_params, dict) and isinstance(artifact_backtest_params.get('broker_cost_context'), dict):
        contexts.append(dict(artifact_backtest_params.get('broker_cost_context') or {}))
    candidate_summary = (candidate or {}).get('candidate_summary')
    if isinstance(candidate_summary, dict) and isinstance(candidate_summary.get('broker_cost_context'), dict):
        contexts.append(dict(candidate_summary.get('broker_cost_context') or {}))

    target_broker_codes = {
        _positive_history_normalize_broker_code(context.get('broker_code') or context.get('broker_profile_id') or '')
        for context in contexts
        if context
    }
    target_broker_labels = {
        _positive_history_normalize_scope_key(context.get('broker_label') or context.get('broker_profile_label') or '')
        for context in contexts
        if context
    }
    target_market_domains = {
        _positive_history_normalize_market_domain(context.get('market_domain') or '')
        for context in contexts
        if context
    }
    inferred_symbol_domain = _positive_history_normalize_market_domain(
        _positive_history_infer_market_domain_from_symbol(candidate_symbol)
    )
    if inferred_symbol_domain:
        target_market_domains.add(inferred_symbol_domain)

    profiles = list_workspace_broker_profiles(user_id, workspace_id, limit=500)
    matches = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        normalized_profile_code = _positive_history_normalize_broker_code(profile.get('broker_code'))
        normalized_profile_label = _positive_history_normalize_scope_key(profile.get('label'))
        normalized_profile_domain = _positive_history_normalize_market_domain(profile.get('market_domain'))
        score = 0
        if normalized_profile_code and normalized_profile_code in target_broker_codes:
            score += 100
        if normalized_profile_label and normalized_profile_label in target_broker_labels:
            score += 40
        if normalized_profile_domain and normalized_profile_domain in target_market_domains:
            score += 20
        if score <= 0:
            continue
        try:
            numeric_id = int(profile.get('id'))
        except (TypeError, ValueError):
            numeric_id = 1_000_000_000
        matches.append((-score, numeric_id, profile))

    if not matches:
        return {
            'broker_profile_id': '',
            'broker_profile_label': '',
        }

    matches.sort(key=lambda item: (item[0], item[1]))
    selected = dict(matches[0][2] or {})
    return {
        'broker_profile_id': str(selected.get('id') or '').strip(),
        'broker_profile_label': str(selected.get('label') or '').strip(),
    }


def _save_positive_history_winner_as_benchmark(user_id: str, workspace_id: str, entry: dict, is_favorite: bool | None = None):
    safe_entry = dict(entry or {})
    if not safe_entry:
        raise HTTPException(status_code=400, detail={'error': 'Positive History entry payload is required.'})

    verdict_text = str(safe_entry.get('operatorVerdict') or '').lower()
    if str(safe_entry.get('classification') or '').lower() != 'promoted' and 'winner' not in verdict_text:
        raise HTTPException(status_code=400, detail={'error': 'Only winner rows can be saved from Positive History.'})

    existing_benchmarks = list_workspace_strategy_benchmarks(user_id, workspace_id, limit=500)

    resolved_candidate = _resolve_positive_history_candidate(user_id, safe_entry)
    if not resolved_candidate:
        raise HTTPException(
            status_code=404,
            detail={'error': 'I could not resolve a saveable research candidate for this Positive History winner.'},
        )

    candidate = resolved_candidate['candidate']
    artifact_payload = resolved_candidate.get('artifact_payload') if isinstance(resolved_candidate.get('artifact_payload'), dict) else {}
    candidate_strategy, candidate_strategies = _positive_history_build_strategy_collection(candidate)
    candidate_symbol = str(candidate.get('symbol') or safe_entry.get('symbol') or '').strip().upper()
    candidate_timeframe = str(candidate.get('timeframe') or safe_entry.get('timeframe') or '').strip().upper()
    candidate_signature = _positive_history_canonical_signature(
        candidate_symbol,
        candidate_timeframe,
        candidate_strategy,
        candidate_strategies,
    )

    for benchmark in existing_benchmarks:
        benchmark_signature = _positive_history_canonical_signature(
            benchmark.get('symbol') or '',
            benchmark.get('timeframe') or '',
            benchmark.get('strategy') or {},
            benchmark.get('strategies') or [],
        )
        if benchmark_signature == candidate_signature:
            return {
                'benchmark': benchmark,
                'already_exists': True,
                'resolved_from': 'existing_strategy_match',
            }

    notes = (
        f"Saved manually from Positive History winner table ({safe_entry.get('id') or safe_entry.get('label')}). "
        f"Resolved from paper {resolved_candidate.get('paper_id') or 'unknown'} "
        f"candidate {candidate.get('candidate_id') or 'unknown'} "
        f"at {resolved_candidate.get('artifact_path') or 'unknown artifact'}."
    )
    created = create_workspace_strategy_benchmark(
        user_id,
        workspace_id,
        label=str(safe_entry.get('label') or candidate.get('label') or 'Winner benchmark').strip(),
        side=safe_entry.get('side') or '',
        source='codex-research-winner',
        notes=notes,
        is_favorite=is_favorite,
        broker_profile_id=_positive_history_resolve_broker_scope(
            user_id,
            workspace_id,
            candidate_symbol,
            candidate,
            artifact_payload,
        ).get('broker_profile_id'),
        symbol=candidate_symbol,
        timeframe=candidate_timeframe,
        strategy=candidate_strategy,
        strategies=candidate_strategies,
    )
    return {
        'benchmark': created,
        'already_exists': False,
        'resolved_from': 'research_artifact',
        'artifact_path': resolved_candidate.get('artifact_path'),
        'candidate_id': candidate.get('candidate_id'),
        'paper_id': resolved_candidate.get('paper_id'),
    }


class WorkspaceResearchPaperPayload(BaseModel):
    project_key: str | None = None
    title: str
    status: str | None = None
    discipline: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    summary: str | None = None
    article: dict | None = None
    reuse_existing_project_key: bool | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceResearchPaperPatchPayload(BaseModel):
    project_key: str | None = None
    title: str | None = None
    status: str | None = None
    discipline: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    summary: str | None = None
    article: dict | None = None
    user_id: str | None = None
    workspace_id: str | None = None


class WorkspaceTradeReconciliationPayload(BaseModel):
    range_key: str = '7d'
    custom_days: int | None = None
    strategy_filter: str | None = None
    broker_profile_id: str | None = None
    strategy_payload: dict | None = None
    indicators: list[dict] | None = None
    symbol: str | None = None
    timeframe: str | None = None
    volume: float | None = None
    user_id: str | None = None
    workspace_id: str | None = None


def _resolve_trade_range_start(range_key: str = '7d', custom_days: int | None = None):
    safe_range_key = str(range_key or '7d').strip().lower() or '7d'
    now = time.time()

    if safe_range_key == 'today':
        local_now = time.localtime(now)
        return time.mktime((
            local_now.tm_year,
            local_now.tm_mon,
            local_now.tm_mday,
            0, 0, 0,
            local_now.tm_wday,
            local_now.tm_yday,
            local_now.tm_isdst,
        ))
    if safe_range_key in {'7d', 'week'}:
        return now - (7 * 24 * 60 * 60)
    if safe_range_key in {'30d', 'month'}:
        return now - (30 * 24 * 60 * 60)
    if safe_range_key == 'custom':
        return now - (max(1, int(custom_days or 1)) * 24 * 60 * 60)
    return None


def _timeframe_to_seconds(timeframe: str):
    safe = str(timeframe or 'M1').strip().upper() or 'M1'
    if safe == 'MN1':
        return 30 * 24 * 60 * 60
    unit = safe[0]
    try:
        value = max(1, int(safe[1:] or 1))
    except Exception:
        value = 1
    multiplier = {
        'S': 1,
        'M': 60,
        'H': 60 * 60,
        'D': 24 * 60 * 60,
        'W': 7 * 24 * 60 * 60,
    }.get(unit, 60)
    return value * multiplier


def _build_reconciliation_indicator_payload(strategy_payload: dict | None, indicators: list[dict] | None):
    explicit = []
    for entry in indicators or []:
        if not isinstance(entry, dict):
            continue
        explicit.append({
            'name': str(entry.get('name') or '').strip(),
            'params': list(entry.get('params') or []),
            'alias': str(entry.get('alias') or '').strip(),
        })
    if explicit:
        return explicit

    feature_manifest = (strategy_payload or {}).get('featureManifest') or {}
    manifest_indicators = feature_manifest.get('indicators') if isinstance(feature_manifest, dict) else []
    normalized = []
    for entry in manifest_indicators or []:
        if not isinstance(entry, dict):
            continue
        normalized.append({
            'name': str(entry.get('name') or '').strip(),
            'params': list(entry.get('params') or []),
            'alias': str(entry.get('alias') or '').strip(),
        })
    return normalized


def _build_expected_trade_operations(
    *,
    strategy_payload: dict,
    indicators: list[dict] | None,
    symbol: str,
    timeframe: str,
    range_key: str,
    custom_days: int | None,
):
    try:
        from .lib.symbol import Symbol
        from .lib.strategy import Backtester, Strategy
        from .services.market_data_service import wait_for_market_data
        from .strategy_backend import StrategyPayload, apply_indicator_payload, build_strategy_params, resolve_strategy_param_aliases
    except ImportError:
        from lib.symbol import Symbol
        from lib.strategy import Backtester, Strategy
        from services.market_data_service import wait_for_market_data
        from strategy_backend import StrategyPayload, apply_indicator_payload, build_strategy_params, resolve_strategy_param_aliases

    range_start = _resolve_trade_range_start(range_key, custom_days)
    timeframe_seconds = _timeframe_to_seconds(timeframe)
    if range_start is None:
        requested_bars = 5000
    else:
        bars_for_range = int(math.ceil(max(0.0, time.time() - range_start) / max(1, timeframe_seconds)))
        requested_bars = max(500, bars_for_range + 300)

    market_context = wait_for_market_data(
        symbol=symbol,
        timeframe=timeframe,
        bars=requested_bars,
        timeout_seconds=25.0,
        source='trade_reconciliation',
    )
    if not market_context.get('ready'):
        raise ValueError(
            market_context.get('error')
            or f'Market history for {symbol} {timeframe} ({requested_bars:,} bars) is not ready for comparison.'
        )

    snapshot_symbol = Symbol(
        name=symbol,
        timeframe=timeframe,
        bars=requested_bars,
        candles=list(market_context.get('candles') or []),
    )
    indicator_payload = _build_reconciliation_indicator_payload(strategy_payload, indicators)
    applied_indicators = []
    if indicator_payload:
        applied_indicators, _ = apply_indicator_payload(snapshot_symbol, indicator_payload)

    strategy = Strategy()
    strategy_params = resolve_strategy_param_aliases(
        build_strategy_params(StrategyPayload.model_validate(strategy_payload)),
        applied_indicators,
    )
    strategy.set_params(
        **strategy_params,
        execution_mode='next_bar_open',
    )

    backtester = Backtester(snapshot_symbol, strategy)
    backtester.set_params(
        execution_mode='next_bar_open',
        history_scope_mode='custom',
        history_scope_bars=requested_bars,
    )
    backtester.run()

    expected_rows = []
    current_operation = None

    for event in list(getattr(backtester.execution, 'events', []) or []):
        event_kind = str(getattr(event, 'kind', '') or '').strip().lower()
        side = str(getattr(event, 'side', '') or '').strip().lower()
        event_time = getattr(event, 'time', None)
        event_price = getattr(event, 'price', None)
        metadata = dict(getattr(event, 'metadata', {}) or {})

        if event_kind == 'open':
            if current_operation is not None:
                expected_rows.append(current_operation)
            current_operation = {
                'id': f'expected-{len(expected_rows) + 1}',
                'side': side,
                'expected_entry_time': event_time,
                'expected_entry_price': event_price,
                'expected_exit_time': None,
                'expected_exit_price': None,
                'expected_exit_reason': None,
                'expected_state': 'open',
            }
            continue

        if event_kind in {'close', 'stop'} and current_operation is not None:
            current_operation['expected_exit_time'] = event_time
            current_operation['expected_exit_price'] = event_price
            current_operation['expected_state'] = 'closed'
            if event_kind == 'stop':
                stop_type = str(metadata.get('stop_type') or '').strip().lower()
                current_operation['expected_exit_reason'] = f'stop {stop_type}'.strip()
            else:
                current_operation['expected_exit_reason'] = 'close normal'
            expected_rows.append(current_operation)
            current_operation = None

    if current_operation is not None:
        expected_rows.append(current_operation)

    filtered_rows = []
    for row in expected_rows:
        effective_time = row.get('expected_entry_time') or row.get('expected_exit_time')
        if range_start is not None and (effective_time is None or float(effective_time) < float(range_start)):
            continue
        filtered_rows.append(row)

    return filtered_rows


def _build_actual_trade_operations(rows: list[dict] | None):
    entries = sorted(
        list(rows or []),
        key=lambda entry: float(
            entry.get('filled_at')
            or entry.get('rejected_at')
            or entry.get('created_at')
            or entry.get('record_created_at')
            or 0.0
        ),
    )

    groups: dict[str, list[dict]] = {}
    ordered_keys: list[str] = []
    fallback_index = 0
    for entry in entries:
        cycle_id = str(entry.get('cycle_id') or '').strip()
        if not cycle_id:
            cycle_id = f'no-cycle-{fallback_index}'
            fallback_index += 1
        if cycle_id not in groups:
            groups[cycle_id] = []
            ordered_keys.append(cycle_id)
        groups[cycle_id].append(entry)

    operations = []
    for cycle_id in ordered_keys:
        group = groups.get(cycle_id) or []
        opens = [entry for entry in group if str(entry.get('action') or '').strip().lower() == 'open']
        closes = [entry for entry in group if str(entry.get('action') or '').strip().lower() == 'close']
        primary_open = opens[0] if opens else (group[0] if group else None)
        if not primary_open:
            continue
        open_status = str(primary_open.get('status') or '').strip().lower()
        first_close_filled = next((entry for entry in closes if str(entry.get('status') or '').strip().lower() == 'filled'), None)

        operation = {
            'id': f'actual-{cycle_id}',
            'cycle_id': cycle_id,
            'side': str(primary_open.get('side') or '').strip().lower(),
            'actual_entry_time': primary_open.get('filled_at') or primary_open.get('rejected_at') or primary_open.get('created_at'),
            'actual_entry_price': primary_open.get('fill_price'),
            'actual_exit_time': first_close_filled.get('filled_at') if first_close_filled else None,
            'actual_exit_price': first_close_filled.get('fill_price') if first_close_filled else None,
            'actual_exit_reason': first_close_filled.get('exit_reason') if first_close_filled else None,
            'actual_state': 'rejected' if open_status == 'rejected' else ('closed' if first_close_filled else 'open'),
            'actual_status': open_status or 'queued',
            'pnl': (
                float(first_close_filled.get('profit') or 0.0)
                + float(first_close_filled.get('commission') or 0.0)
                + float(first_close_filled.get('swap') or 0.0)
            ) if first_close_filled else 0.0,
            'message': str(primary_open.get('message') or first_close_filled.get('message') if first_close_filled else primary_open.get('message') or '').strip(),
            'broker_order_id': primary_open.get('broker_order_id'),
            'broker_position_ticket': (
                primary_open.get('broker_position_ticket')
                or (first_close_filled.get('broker_position_ticket') if first_close_filled else None)
            ),
            'broker_deal_id': primary_open.get('broker_deal_id') or (first_close_filled.get('broker_deal_id') if first_close_filled else None),
        }
        operations.append(operation)

    return operations


def _compare_expected_vs_actual(expected_rows: list[dict] | None, actual_rows: list[dict] | None):
    expected = list(expected_rows or [])
    actual = list(actual_rows or [])
    row_count = max(len(expected), len(actual))
    rows = []
    drift_values = []

    for index in range(row_count):
        expected_entry = expected[index] if index < len(expected) else None
        actual_entry = actual[index] if index < len(actual) else None

        if expected_entry and actual_entry:
            expected_side = str(expected_entry.get('side') or '').strip().lower()
            actual_side = str(actual_entry.get('side') or '').strip().lower()
            actual_state = str(actual_entry.get('actual_state') or '').strip().lower()
            if expected_side != actual_side:
                verdict = 'side_mismatch'
                note = 'Expected and executed sides diverged.'
            elif actual_state == 'rejected':
                verdict = 'rejected'
                note = actual_entry.get('message') or 'The trader attempted the operation, but the broker rejected it.'
            else:
                verdict = 'matched'
                note = ''
        elif expected_entry and not actual_entry:
            verdict = 'missed'
            note = 'The strategy expected an operation, but the trader did not execute one in that slot.'
        else:
            verdict = 'unexpected'
            note = 'The trader executed an operation that has no matching expected slot in the strategy replay.'

        entry_drift_seconds = None
        if expected_entry and actual_entry:
            expected_time = expected_entry.get('expected_entry_time')
            actual_time = actual_entry.get('actual_entry_time')
            if expected_time is not None and actual_time is not None:
                entry_drift_seconds = float(actual_time) - float(expected_time)
                drift_values.append(abs(entry_drift_seconds))

        rows.append({
            'id': f'comparison-{index + 1}',
            'index': index + 1,
            'verdict': verdict,
            'expected': expected_entry,
            'actual': actual_entry,
            'entry_drift_seconds': entry_drift_seconds,
            'note': str(note or '').strip(),
        })

    matched_count = sum(1 for row in rows if row.get('verdict') == 'matched')
    rejected_count = sum(1 for row in rows if row.get('verdict') == 'rejected')
    missed_count = sum(1 for row in rows if row.get('verdict') == 'missed')
    unexpected_count = sum(1 for row in rows if row.get('verdict') == 'unexpected')
    side_mismatch_count = sum(1 for row in rows if row.get('verdict') == 'side_mismatch')
    realized_pnl = sum(float((row.get('actual') or {}).get('pnl') or 0.0) for row in rows)

    expected_times = [
        float((row.get('expected') or {}).get('expected_entry_time'))
        for row in rows
        if (row.get('expected') or {}).get('expected_entry_time') is not None
    ]

    return {
        'rows': rows,
        'summary': {
            'expectedCount': len(expected),
            'actualCount': len(actual),
            'matchedCount': matched_count,
            'rejectedCount': rejected_count,
            'missedCount': missed_count,
            'unexpectedCount': unexpected_count,
            'sideMismatchCount': side_mismatch_count,
            'matchRate': (float(matched_count) / float(len(expected))) if expected else 0.0,
            'realizedPnl': realized_pnl,
            'avgEntryDriftSeconds': (sum(drift_values) / len(drift_values)) if drift_values else None,
            'maxEntryDriftSeconds': max(drift_values) if drift_values else None,
            'firstExpectedAt': min(expected_times) if expected_times else None,
            'lastExpectedAt': max(expected_times) if expected_times else None,
        },
    }


@router.get('/workspace/positive-history/shared-catalog')
def get_shared_positive_history_catalog():
    payload = load_shared_positive_history_payload()
    return {
        'status': 'ok',
        **payload,
    }


@router.get('/workspace/state')
def get_workspace_state(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    if is_guest_user(auth_user):
        return {
            'status': 'ok',
            'auth_user': auth_user,
            **build_guest_workspace_snapshot(workspace_id),
        }
    load_workspace_runtime(user_id=resolved_user_id, workspace_id=workspace_id)
    return {
        'status': 'ok',
        'auth_user': auth_user,
        **build_workspace_runtime_payload(),
    }


@router.put('/workspace/state')
async def put_workspace_state(payload: WorkspaceStatePayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    try:
        saved = await save_and_broadcast_workspace_state(
            next_state=payload.state,
            user_id=resolved_user_id,
            workspace_id=payload.workspace_id,
            expected_revision=payload.expected_revision,
            source=payload.source,
        )
    except ValueError as error:
        latest = load_workspace_state(
            resolved_user_id,
            payload.workspace_id or 'default',
        )
        raise HTTPException(
            status_code=409,
            detail={
                'error': str(error),
                'latest': latest,
            },
        ) from error

    return {
        'status': 'ok',
        **saved,
    }


@router.patch('/workspace/state')
async def patch_workspace_state(payload: WorkspacePatchPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    try:
        saved = await save_and_broadcast_workspace_patch(
            patch_state=payload.patch,
            user_id=resolved_user_id,
            workspace_id=payload.workspace_id,
            expected_revision=payload.expected_revision,
            source=payload.source,
        )
    except ValueError as error:
        latest = load_workspace_state(
            resolved_user_id,
            payload.workspace_id or 'default',
        )
        raise HTTPException(
            status_code=409,
            detail={
                'error': str(error),
                'latest': latest,
            },
        ) from error

    return {
        'status': 'ok',
        **saved,
    }


@router.get('/workspace/saves')
def get_workspace_saves(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    limit: int = Query(default=20),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    if is_guest_user(auth_user):
        return {
            'status': 'ok',
            'auth_user': auth_user,
            'saves': [],
            'temporary': True,
        }
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'saves': list_workspace_save_summaries(user_id=resolved_user_id, workspace_id=workspace_id, limit=limit),
    }


@router.get('/workspace/saves/{save_id}')
def get_workspace_save_by_id(
    save_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    if is_guest_user(auth_user):
        raise HTTPException(status_code=404, detail={'error': f'Workspace save {save_id} was not found'})
    saved = get_workspace_save_snapshot(user_id=resolved_user_id, workspace_id=workspace_id, save_id=save_id)
    if not saved:
        raise HTTPException(status_code=404, detail={'error': f'Workspace save {save_id} was not found'})
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'save': saved,
    }


@router.get('/workspace/system-log')
def get_workspace_system_log(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    session_id: int | None = Query(default=None),
    entry_limit: int = Query(default=500),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    if is_guest_user(auth_user):
        return {
            'status': 'ok',
            'auth_user': auth_user,
            'session': None,
            'entries': [],
            'sessions': [],
            'temporary': True,
        }
    payload = get_workspace_system_log_payload(
        user_id=resolved_user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        entry_limit=entry_limit,
        create_if_missing=session_id is None,
    )
    return {
        'status': 'ok',
        'auth_user': auth_user,
        **payload,
    }


@router.get('/workspace/system-log/sessions')
def get_workspace_system_log_sessions(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    limit: int = Query(default=20),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    if is_guest_user(auth_user):
        return {
            'status': 'ok',
            'auth_user': auth_user,
            'sessions': [],
            'temporary': True,
        }
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'sessions': list_workspace_system_log_sessions(resolved_user_id, workspace_id, limit=limit),
    }


@router.get('/workspace/research-runs')
def get_workspace_research_runs(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    limit: int = Query(default=100),
    include_payload: bool = Query(default=False),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'runs': list_workspace_research_runs(
            resolved_user_id,
            workspace_id,
            limit=limit,
            include_payload=include_payload,
        ),
    }


@router.get('/workspace/research-runs/{run_id}')
def get_workspace_research_run_by_id(
    run_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    include_payload: bool = Query(default=True),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    run = get_workspace_research_run(
        resolved_user_id,
        workspace_id,
        run_id,
        include_payload=include_payload,
    )
    if not run:
        raise HTTPException(status_code=404, detail={'error': f'Research run {run_id} was not found'})
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'run': run,
    }


@router.get('/workspace/research-jobs')
def get_workspace_research_jobs(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    limit: int = Query(default=100),
    include_payload: bool = Query(default=False),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'jobs': list_research_jobs(
            resolved_user_id,
            workspace_id,
            limit=limit,
            include_payload=include_payload,
        ),
    }


@router.get('/workspace/research-batches')
def get_workspace_research_batches(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    limit: int = Query(default=100),
    include_payload: bool = Query(default=False),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'batches': list_research_batches(
            resolved_user_id,
            workspace_id,
            limit=limit,
            include_payload=include_payload,
        ),
    }


@router.post('/workspace/research-runtime/reconcile')
def reconcile_workspace_research_runtime(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    summary = reconcile_research_runtime(resolved_user_id, workspace_id)
    return {
        'status': 'ok',
        'auth_user': auth_user,
        **summary,
    }


@router.get('/workspace/research-campaigns')
def get_workspace_research_campaigns(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    limit: int = Query(default=100),
    include_payload: bool = Query(default=False),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'campaigns': list_research_campaigns(
            resolved_user_id,
            workspace_id,
            limit=limit,
            include_payload=include_payload,
        ),
    }


@router.get('/workspace/research-batches/{batch_id}')
def get_workspace_research_batch_by_id(
    batch_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    include_payload: bool = Query(default=True),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    batch = get_research_batch(
        resolved_user_id,
        workspace_id,
        batch_id,
        include_payload=include_payload,
    )
    if not batch:
        raise HTTPException(status_code=404, detail={'error': f'Research batch {batch_id} was not found'})
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'batch': batch,
    }


@router.get('/workspace/research-campaigns/{campaign_id}')
def get_workspace_research_campaign_by_id(
    campaign_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    include_payload: bool = Query(default=True),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    campaign = get_research_campaign(
        resolved_user_id,
        workspace_id,
        campaign_id,
        include_payload=include_payload,
    )
    if not campaign:
        raise HTTPException(status_code=404, detail={'error': f'Research campaign {campaign_id} was not found'})
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'campaign': campaign,
    }


@router.get('/workspace/research-jobs/{job_id}')
def get_workspace_research_job_by_id(
    job_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    include_payload: bool = Query(default=True),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    job = get_research_job(
        resolved_user_id,
        workspace_id,
        job_id,
        include_payload=include_payload,
    )
    if not job:
        raise HTTPException(status_code=404, detail={'error': f'Research job {job_id} was not found'})
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'job': job,
    }


@router.get('/workspace/strategy-benchmarks')
def get_workspace_strategy_benchmarks(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    broker_profile_id: str = Query(default=''),
    limit: int = Query(default=100),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'benchmarks': list_workspace_strategy_benchmarks(
            resolved_user_id,
            workspace_id,
            limit=limit,
            broker_profile_id=broker_profile_id,
        ),
    }


@router.get('/workspace/saved-portfolios')
def get_workspace_saved_portfolios(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    broker_profile_id: str = Query(default=''),
    limit: int = Query(default=100),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'portfolios': list_workspace_saved_portfolios(
            resolved_user_id,
            workspace_id,
            limit=limit,
            broker_profile_id=broker_profile_id,
        ),
    }


@router.get('/workspace/broker-profiles')
def get_workspace_broker_profiles(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    limit: int = Query(default=100, ge=1, le=500),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'broker_profiles': list_workspace_broker_profiles(resolved_user_id, workspace_id, limit=limit),
    }


@router.get('/workspace/research-papers')
def get_workspace_research_papers(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    limit: int = Query(default=100, ge=1, le=500),
):
    auth_user = require_request_auth(request)
    if is_guest_user(auth_user):
        paper = get_workspace_research_paper(
            GUEST_DISPLAY_OWNER_WORKSPACE_USER_ID,
            GUEST_DISPLAY_WORKSPACE_ID,
            GUEST_DISPLAY_RESEARCH_PAPER_ID,
        )
        return {
            'status': 'ok',
            'auth_user': auth_user,
            'papers': [paper] if paper else [],
        }
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'papers': list_workspace_research_papers(resolved_user_id, workspace_id, limit=limit),
    }


@router.get('/workspace/research-papers/{paper_id}')
def get_workspace_research_paper_route(
    paper_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    if is_guest_user(auth_user):
        if int(paper_id) != GUEST_DISPLAY_RESEARCH_PAPER_ID:
            raise HTTPException(status_code=404, detail={'error': f'Research paper {paper_id} was not found'})
        paper = get_workspace_research_paper(
            GUEST_DISPLAY_OWNER_WORKSPACE_USER_ID,
            GUEST_DISPLAY_WORKSPACE_ID,
            GUEST_DISPLAY_RESEARCH_PAPER_ID,
        )
        if not paper:
            raise HTTPException(status_code=404, detail={'error': f'Research paper {paper_id} was not found'})
        return {
            'status': 'ok',
            'auth_user': auth_user,
            'paper': paper,
        }
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    paper = get_workspace_research_paper(resolved_user_id, workspace_id, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail={'error': f'Research paper {paper_id} was not found'})
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'paper': paper,
    }


@router.get('/workspace/live-trades')
def get_workspace_live_trades(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    range_key: str = Query(default='30d'),
    custom_days: int | None = Query(default=None),
    strategy_filter: str = Query(default=''),
    symbol_filter: str = Query(default=''),
    status_filter: str = Query(default='all'),
    broker_profile_id: str = Query(default=''),
    limit: int = Query(default=500, ge=1, le=5000),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    payload = list_workspace_live_trades(
        resolved_user_id,
        workspace_id,
        range_key=range_key,
        custom_days=custom_days,
        strategy_filter=strategy_filter,
        symbol_filter=symbol_filter,
        status_filter=status_filter,
        broker_profile_id=broker_profile_id,
        limit=limit,
    )
    return {
        'status': 'ok',
        'auth_user': auth_user,
        **payload,
    }


@router.get('/workspace/trade-reconciliations')
def get_workspace_trade_reconciliations(
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
    range_key: str = Query(default='7d'),
    custom_days: int | None = Query(default=None),
    strategy_filter: str = Query(default=''),
    broker_profile_id: str = Query(default=''),
    limit: int = Query(default=100, ge=1, le=1000),
):
    auth_user = require_request_auth(request)
    resolved_user_id, auth_user = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    reconciliations = list_workspace_trade_reconciliations(
        resolved_user_id,
        workspace_id,
        range_key=range_key,
        custom_days=custom_days,
        strategy_filter=strategy_filter,
        broker_profile_id=broker_profile_id,
        limit=limit,
    )
    return {
        'status': 'ok',
        'auth_user': auth_user,
        'reconciliations': reconciliations,
    }


@router.post('/workspace/trade-reconciliations')
def create_trade_reconciliation(payload: WorkspaceTradeReconciliationPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    safe_symbol = str(payload.symbol or '').strip().upper()
    safe_timeframe = str(payload.timeframe or '').strip().upper()
    safe_strategy_payload = dict(payload.strategy_payload or {})
    safe_strategy_filter = str(payload.strategy_filter or '').strip()

    if not safe_symbol or not safe_timeframe or not safe_strategy_payload:
        raise HTTPException(
            status_code=400,
            detail={'error': 'Strategy comparison requires a selected sleeve with strategy, symbol, and timeframe.'},
        )

    live_payload = list_workspace_live_trades(
        resolved_user_id,
        payload.workspace_id or 'default',
        range_key=payload.range_key,
        custom_days=payload.custom_days,
        strategy_filter=safe_strategy_filter,
        symbol_filter=safe_symbol,
        status_filter='all',
        broker_profile_id=payload.broker_profile_id,
        limit=5000,
    )
    actual_rows = _build_actual_trade_operations(live_payload.get('trades') or [])
    expected_rows = _build_expected_trade_operations(
        strategy_payload=safe_strategy_payload,
        indicators=list(payload.indicators or []),
        symbol=safe_symbol,
        timeframe=safe_timeframe,
        range_key=payload.range_key,
        custom_days=payload.custom_days,
    )
    comparison = _compare_expected_vs_actual(expected_rows, actual_rows)
    reconciliation = {
        'range_key': str(payload.range_key or '7d').strip().lower() or '7d',
        'custom_days': payload.custom_days,
        'strategy_filter': safe_strategy_filter,
        'broker_profile_id': str(payload.broker_profile_id or '').strip(),
        'symbol': safe_symbol,
        'timeframe': safe_timeframe,
        'expected_rows': expected_rows,
        'actual_rows': actual_rows,
        **comparison,
        'created_at': time.time(),
    }
    return {
        'status': 'ok',
        'reconciliation': reconciliation,
    }


@router.post('/workspace/research-runs')
def create_research_run(payload: WorkspaceResearchRunPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = create_workspace_research_run(
        resolved_user_id,
        payload.workspace_id or 'default',
        run_type=payload.run_type,
        side=payload.side,
        run_name=payload.run_name,
        version=payload.version,
        best_id=payload.best_id,
        best_label=payload.best_label,
        comparison_count=payload.comparison_count,
        run_label=payload.run_label,
        run_notes=payload.run_notes,
        pinned=payload.pinned,
        payload=payload.payload,
    )
    return {
        'status': 'ok',
        'run': created,
    }


@router.post('/workspace/system-log/entries')
async def append_workspace_system_log(payload: WorkspaceSystemLogAppendPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    appended = await append_and_broadcast_workspace_system_log_entries(
        [item.model_dump() for item in payload.entries],
        user_id=resolved_user_id,
        workspace_id=payload.workspace_id or 'default',
        session_id=payload.session_id,
        source=payload.source,
        label=payload.label,
        metadata=payload.metadata,
    )
    return {
        'status': 'ok',
        'session': appended.get('session'),
        'entries': list(appended.get('entries') or []),
        'updated_at': appended.get('updated_at'),
    }


@router.post('/workspace/system-log/start')
async def start_workspace_system_log(payload: WorkspaceSystemLogStartPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    started = await start_and_broadcast_workspace_system_log_session(
        user_id=resolved_user_id,
        workspace_id=payload.workspace_id or 'default',
        label=payload.label,
        source=payload.source,
        metadata=payload.metadata,
    )
    return {
        'status': 'ok',
        'session': started.get('session'),
        'archived_session_ids': list(started.get('archived_session_ids') or []),
        'updated_at': started.get('updated_at'),
    }


@router.post('/workspace/research-jobs')
def create_research_job(payload: WorkspaceResearchJobPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = queue_research_job(
        resolved_user_id,
        payload.workspace_id or 'default',
        job_type=payload.job_type,
        request_payload=payload.request,
        run_label=payload.run_label,
        run_notes=payload.run_notes,
    )
    return {
        'status': 'ok',
        'job': created,
    }


@router.post('/workspace/research-batches')
def create_research_batch(payload: WorkspaceResearchBatchPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = queue_research_batch(
        resolved_user_id,
        payload.workspace_id or 'default',
        label=payload.label,
        jobs=[item.model_dump() for item in payload.jobs],
    )
    return {
        'status': 'ok',
        'batch': created,
    }


@router.post('/workspace/research-campaigns')
def create_workspace_research_campaign(payload: WorkspaceResearchCampaignPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = create_research_campaign(
        resolved_user_id,
        payload.workspace_id or 'default',
        label=payload.label,
        description=payload.description,
        jobs=[item.model_dump() for item in payload.jobs],
        batch_jobs=list(payload.batch_jobs or []),
        shared_features=list(payload.shared_features or []),
        options=dict(payload.options or {}),
    )
    return {
        'status': 'ok',
        'campaign': created,
    }


@router.post('/workspace/broker-profiles')
def create_broker_profile(payload: WorkspaceBrokerProfilePayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = create_workspace_broker_profile(
        resolved_user_id,
        payload.workspace_id or 'default',
        label=payload.label,
        broker_code=payload.broker_code,
        connector_kind=payload.connector_kind,
        server_name=payload.server_name,
        market_domain=payload.market_domain,
        base_currency=payload.base_currency,
        notes=payload.notes,
        is_default=payload.is_default,
        is_favorite=payload.is_favorite,
        profile=dict(payload.profile or {}) if isinstance(payload.profile, dict) else {},
    )
    return {
        'status': 'ok',
        'broker_profile': created,
    }


@router.post('/workspace/strategy-benchmarks')
def create_strategy_benchmark(payload: WorkspaceStrategyBenchmarkPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = create_workspace_strategy_benchmark(
        resolved_user_id,
        payload.workspace_id or 'default',
        label=payload.label,
        side=payload.side,
        source=payload.source,
        notes=payload.notes,
        is_favorite=payload.is_favorite,
        broker_profile_id=payload.broker_profile_id,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        strategy=payload.strategy,
        strategies=payload.strategies,
    )
    return {
        'status': 'ok',
        'benchmark': created,
    }


@router.post('/workspace/saved-portfolios')
def create_saved_portfolio(payload: WorkspaceSavedPortfolioPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = create_workspace_saved_portfolio(
        resolved_user_id,
        payload.workspace_id or 'default',
        label=payload.label,
        source=payload.source,
        notes=payload.notes,
        is_favorite=payload.is_favorite,
        broker_profile_id=payload.broker_profile_id,
        portfolio=(dict(payload.portfolio or {}) if isinstance(payload.portfolio, dict) else {}),
        capital_model=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else {}),
    )
    return {
        'status': 'ok',
        'portfolio': created,
    }


@router.post('/workspace/strategy-benchmarks/from-positive-history')
def create_strategy_benchmark_from_positive_history(
    payload: WorkspacePositiveHistoryWinnerSavePayload,
    request: Request,
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    result = _save_positive_history_winner_as_benchmark(
        resolved_user_id,
        payload.workspace_id or 'default',
        payload.entry or {},
        is_favorite=payload.is_favorite,
    )
    return {
        'status': 'ok',
        **result,
    }


@router.post('/workspace/research-papers')
def create_research_paper(payload: WorkspaceResearchPaperPayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = create_workspace_research_paper(
        resolved_user_id,
        payload.workspace_id or 'default',
        project_key=payload.project_key,
        title=payload.title,
        status=payload.status,
        discipline=payload.discipline,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        summary=payload.summary,
        article=payload.article,
        reuse_existing_project_key=bool(payload.reuse_existing_project_key),
    )
    return {
        'status': 'ok',
        'paper': created,
    }


@router.patch('/workspace/strategy-benchmarks/{benchmark_id}')
def update_strategy_benchmark(
    benchmark_id: int,
    payload: WorkspaceStrategyBenchmarkPatchPayload,
    request: Request,
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    updated = update_workspace_strategy_benchmark(
        resolved_user_id,
        payload.workspace_id or 'default',
        benchmark_id,
        label=payload.label,
        side=payload.side,
        source=payload.source,
        notes=payload.notes,
        is_favorite=payload.is_favorite,
        broker_profile_id=payload.broker_profile_id,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
    )
    if not updated:
        raise HTTPException(status_code=404, detail={'error': f'Strategy benchmark {benchmark_id} was not found'})
    return {
        'status': 'ok',
        'benchmark': updated,
    }


@router.patch('/workspace/broker-profiles/{broker_profile_id}')
def update_broker_profile(
    broker_profile_id: int,
    payload: WorkspaceBrokerProfilePatchPayload,
    request: Request,
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    updated = update_workspace_broker_profile(
        resolved_user_id,
        payload.workspace_id or 'default',
        broker_profile_id,
        label=payload.label,
        broker_code=payload.broker_code,
        connector_kind=payload.connector_kind,
        server_name=payload.server_name,
        market_domain=payload.market_domain,
        base_currency=payload.base_currency,
        notes=payload.notes,
        is_default=payload.is_default,
        is_favorite=payload.is_favorite,
        profile=dict(payload.profile or {}) if isinstance(payload.profile, dict) else None,
    )
    if not updated:
        raise HTTPException(status_code=404, detail={'error': f'Broker profile {broker_profile_id} was not found'})
    return {
        'status': 'ok',
        'broker_profile': updated,
    }


@router.patch('/workspace/saved-portfolios/{portfolio_id}')
def update_saved_portfolio(
    portfolio_id: int,
    payload: WorkspaceSavedPortfolioPatchPayload,
    request: Request,
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    updated = update_workspace_saved_portfolio(
        resolved_user_id,
        payload.workspace_id or 'default',
        portfolio_id,
        label=payload.label,
        source=payload.source,
        notes=payload.notes,
        is_favorite=payload.is_favorite,
        broker_profile_id=payload.broker_profile_id,
        portfolio=(dict(payload.portfolio or {}) if isinstance(payload.portfolio, dict) else None),
        capital_model=(dict(payload.capitalModel or {}) if isinstance(payload.capitalModel, dict) else None),
    )
    if not updated:
        raise HTTPException(status_code=404, detail={'error': f'Saved portfolio {portfolio_id} was not found'})
    return {
        'status': 'ok',
        'portfolio': updated,
    }


@router.patch('/workspace/research-papers/{paper_id}')
def update_research_paper(
    paper_id: int,
    payload: WorkspaceResearchPaperPatchPayload,
    request: Request,
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    updated = update_workspace_research_paper(
        resolved_user_id,
        payload.workspace_id or 'default',
        paper_id,
        project_key=payload.project_key,
        title=payload.title,
        status=payload.status,
        discipline=payload.discipline,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        summary=payload.summary,
        article=payload.article,
    )
    if not updated:
        raise HTTPException(status_code=404, detail={'error': f'Research paper {paper_id} was not found'})
    return {
        'status': 'ok',
        'paper': updated,
    }


@router.patch('/workspace/research-runs/{run_id}')
def update_research_run(
    run_id: int,
    payload: WorkspaceResearchRunPatchPayload,
    request: Request,
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    updated = update_workspace_research_run(
        resolved_user_id,
        payload.workspace_id or 'default',
        run_id,
        run_label=payload.run_label,
        run_notes=payload.run_notes,
        pinned=payload.pinned,
    )
    if not updated:
        raise HTTPException(status_code=404, detail={'error': f'Research run {run_id} was not found'})
    return {
        'status': 'ok',
        'run': updated,
    }


@router.patch('/workspace/research-campaigns/{campaign_id}')
def update_workspace_research_campaign_route(
    campaign_id: int,
    payload: WorkspaceResearchCampaignPatchPayload,
    request: Request,
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    updated = update_research_campaign(
        resolved_user_id,
        payload.workspace_id or 'default',
        campaign_id,
        label=payload.label,
        description=payload.description,
        jobs=[item.model_dump() for item in payload.jobs] if payload.jobs is not None else None,
        batch_jobs=list(payload.batch_jobs or []) if payload.batch_jobs is not None else None,
        shared_features=list(payload.shared_features or []) if payload.shared_features is not None else None,
        options=dict(payload.options or {}) if payload.options is not None else None,
    )
    if not updated:
        raise HTTPException(status_code=404, detail={'error': f'Research campaign {campaign_id} was not found'})
    return {
        'status': 'ok',
        'campaign': updated,
    }


@router.delete('/workspace/research-runs/{run_id}')
def delete_research_run(
    run_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    deleted = delete_workspace_research_run(resolved_user_id, workspace_id, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={'error': f'Research run {run_id} was not found'})
    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.delete('/workspace/research-papers/{paper_id}')
def delete_research_paper(
    paper_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    deleted = delete_workspace_research_paper(resolved_user_id, workspace_id, paper_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={'error': f'Research paper {paper_id} was not found'})
    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.post('/workspace/research-campaigns/{campaign_id}/launch')
def launch_workspace_research_campaign(
    campaign_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    try:
        launched = launch_research_campaign(resolved_user_id, workspace_id, campaign_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={'error': str(error)}) from error
    if not launched:
        raise HTTPException(status_code=404, detail={'error': f'Research campaign {campaign_id} was not found'})
    return {
        'status': 'ok',
        'batch': launched,
    }


@router.delete('/workspace/research-campaigns/{campaign_id}')
def delete_workspace_research_campaign_route(
    campaign_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    deleted = delete_research_campaign(resolved_user_id, workspace_id, campaign_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={'error': f'Research campaign {campaign_id} was not found'})
    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.post('/workspace/research-batches/{batch_id}/cancel')
def cancel_workspace_research_batch(
    batch_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    cancelled = cancel_research_batch(resolved_user_id, workspace_id, batch_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail={'error': f'Research batch {batch_id} was not found'})
    return {
        'status': 'ok',
        'batch': cancelled,
    }


@router.delete('/workspace/research-batches/{batch_id}')
def delete_research_batch(
    batch_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    deleted = delete_workspace_research_batch(resolved_user_id, workspace_id, batch_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={'error': f'Research batch {batch_id} was not found'})
    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.post('/workspace/research-jobs/{job_id}/cancel')
def cancel_workspace_research_job(
    job_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    cancelled = cancel_research_job(resolved_user_id, workspace_id, job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail={'error': f'Research job {job_id} was not found'})
    return {
        'status': 'ok',
        'job': cancelled,
    }


@router.delete('/workspace/research-jobs/{job_id}')
def delete_research_job(
    job_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    deleted = delete_workspace_research_job(resolved_user_id, workspace_id, job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={'error': f'Research job {job_id} was not found'})
    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.delete('/workspace/strategy-benchmarks/{benchmark_id}')
def delete_strategy_benchmark(
    benchmark_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    deleted = delete_workspace_strategy_benchmark(resolved_user_id, workspace_id, benchmark_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={'error': f'Strategy benchmark {benchmark_id} was not found'})
    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.delete('/workspace/broker-profiles/{broker_profile_id}')
def delete_broker_profile(
    broker_profile_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    try:
        deleted = delete_workspace_broker_profile(resolved_user_id, workspace_id, broker_profile_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={'error': str(error)}) from error
    if not deleted:
        raise HTTPException(status_code=404, detail={'error': f'Broker profile {broker_profile_id} was not found'})
    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.api_route(
    '/workspace/broker-profiles/{broker_profile_id}/proxy/{proxy_path:path}',
    methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
)
async def proxy_broker_profile_http_request(
    broker_profile_id: int,
    proxy_path: str,
    request: Request,
):
    safe_proxy_path = _normalize_broker_profile_proxy_path(proxy_path)
    if not _is_allowed_broker_profile_proxy_path(safe_proxy_path):
        raise HTTPException(status_code=404, detail={'error': f'Unsupported broker proxy path: {safe_proxy_path or proxy_path}'})

    _, api_base_url = _resolve_broker_profile_proxy_target(broker_profile_id)
    upstream_url = _build_broker_profile_proxy_upstream_url(
        api_base_url,
        safe_proxy_path,
        str(request.url.query or '').strip(),
    )
    request_body = await request.body()

    try:
        upstream_response = requests.request(
            request.method.upper(),
            upstream_url,
            headers=_build_broker_profile_proxy_request_headers(request),
            data=request_body if request_body else None,
            timeout=BROKER_PROFILE_PROXY_REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail={'error': f'Broker profile proxy request failed: {error}'},
        ) from error

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=_build_broker_profile_proxy_response_headers(upstream_response),
    )


@router.websocket('/ws/broker-profiles/{broker_profile_id}/proxy/{proxy_channel}')
async def proxy_broker_profile_websocket(
    websocket: WebSocket,
    broker_profile_id: int,
    proxy_channel: str,
):
    safe_proxy_channel = _trim_broker_proxy_text(proxy_channel).lower()
    if safe_proxy_channel not in BROKER_PROFILE_PROXY_ALLOWED_WS_CHANNELS:
        await websocket.accept()
        await websocket.close(code=1008, reason='Unsupported broker websocket channel.')
        return

    try:
        _, api_base_url = _resolve_broker_profile_proxy_target(broker_profile_id)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {'error': str(error.detail)}
        await websocket.accept()
        await websocket.close(code=1008, reason=str(detail.get('error') or 'Broker websocket proxy unavailable.'))
        return

    query_string = (websocket.scope.get('query_string') or b'').decode('utf-8', errors='ignore')
    upstream_url = _build_broker_profile_proxy_websocket_url(api_base_url, safe_proxy_channel, query_string)

    try:
        upstream_socket = await asyncio.to_thread(
            websocket_client.create_connection,
            upstream_url,
            timeout=BROKER_PROFILE_PROXY_WS_CONNECT_TIMEOUT_SECONDS,
            enable_multithread=True,
        )
    except Exception:
        await websocket.accept()
        await websocket.close(code=1011, reason='Broker websocket upstream unavailable.')
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    stop_event = threading.Event()

    async def _close_local_websocket():
        try:
            await websocket.close()
        except Exception:
            pass

    def _forward_upstream_messages():
        try:
            while not stop_event.is_set():
                message = upstream_socket.recv()
                if message is None:
                    break
                if isinstance(message, (bytes, bytearray)):
                    future = asyncio.run_coroutine_threadsafe(
                        websocket.send_bytes(bytes(message)),
                        loop,
                    )
                else:
                    future = asyncio.run_coroutine_threadsafe(
                        websocket.send_text(str(message)),
                        loop,
                    )
                future.result()
        except Exception:
            pass
        finally:
            stop_event.set()
            try:
                upstream_socket.close()
            except Exception:
                pass
            try:
                asyncio.run_coroutine_threadsafe(_close_local_websocket(), loop)
            except Exception:
                pass

    upstream_reader_thread = threading.Thread(
        target=_forward_upstream_messages,
        name=f'broker-proxy-ws-{broker_profile_id}-{safe_proxy_channel}',
        daemon=True,
    )
    upstream_reader_thread.start()

    try:
        while not stop_event.is_set():
            message = await websocket.receive()
            if message.get('type') == 'websocket.disconnect':
                break
            if message.get('text') is not None:
                await asyncio.to_thread(upstream_socket.send, message['text'])
                continue
            if message.get('bytes') is not None:
                await asyncio.to_thread(upstream_socket.send_binary, message['bytes'])
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        try:
            await asyncio.to_thread(upstream_socket.close)
        except Exception:
            pass
        if upstream_reader_thread.is_alive():
            upstream_reader_thread.join(timeout=1.0)


@router.delete('/workspace/saved-portfolios/{portfolio_id}')
def delete_saved_portfolio(
    portfolio_id: int,
    request: Request,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    deleted = delete_workspace_saved_portfolio(resolved_user_id, workspace_id, portfolio_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={'error': f'Saved portfolio {portfolio_id} was not found'})
    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.post('/workspace/saves')
def create_workspace_save(payload: WorkspaceSavePayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    created = create_workspace_save_snapshot(
        name=payload.name,
        user_id=resolved_user_id,
        workspace_id=payload.workspace_id,
    )
    return {
        'status': 'ok',
        'save': created,
    }


@router.post('/workspace/saves/{save_id}/restore')
async def restore_workspace_save(save_id: int, request: Request, payload: WorkspaceSavePayload | None = None):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    try:
        restored = await restore_workspace_save_snapshot(
            save_id=save_id,
            user_id=resolved_user_id,
            workspace_id=payload.workspace_id if payload else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        **restored,
    }


@router.delete('/workspace/saves/{save_id}')
def delete_workspace_save(save_id: int, request: Request, user_id: str = Query(default='local-user'), workspace_id: str = Query(default='default')):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    try:
        deleted = delete_workspace_save_snapshot(
            save_id=save_id,
            user_id=resolved_user_id,
            workspace_id=workspace_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'deleted': deleted,
    }


@router.patch('/workspace/saves/{save_id}')
def rename_workspace_save(save_id: int, payload: WorkspaceSavePayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    try:
        renamed = rename_workspace_save_snapshot(
            save_id=save_id,
            name=payload.name,
            user_id=resolved_user_id,
            workspace_id=payload.workspace_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'save': renamed,
    }


@router.put('/workspace/saves/{save_id}')
def overwrite_workspace_save(save_id: int, payload: WorkspaceSavePayload, request: Request):
    auth_user = require_request_auth(request)
    resolved_user_id, _ = resolve_request_identity(request, explicit_user_id=auth_user['workspace_user_id'])
    try:
        overwritten = overwrite_workspace_save_snapshot(
            save_id=save_id,
            name=payload.name,
            user_id=resolved_user_id,
            workspace_id=payload.workspace_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        'save': overwritten,
    }


@router.websocket('/ws/workspace')
async def workspace_websocket(
    websocket: WebSocket,
    user_id: str = Query(default='local-user'),
    workspace_id: str = Query(default='default'),
):
    auth_user = await require_websocket_auth_or_close(websocket)
    if not auth_user:
        return

    await websocket.accept()
    resolved_user_id, auth_user = resolve_websocket_identity(websocket, explicit_user_id=auth_user['workspace_user_id'])
    if is_guest_user(auth_user):
        await websocket.send_json({
            'type': 'workspace.snapshot',
            'auth_user': auth_user,
            **build_guest_workspace_snapshot(workspace_id),
        })
        try:
            while True:
                message = await websocket.receive_text()
                if message == 'ping':
                    await websocket.send_json({'type': 'pong'})
        except WebSocketDisconnect:
            return
        except Exception:
            return

    snapshot = load_workspace_runtime(user_id=resolved_user_id, workspace_id=workspace_id)
    channel_key = build_workspace_channel_key(resolved_user_id, workspace_id)
    realtime_sync.subscribe(channel_key, websocket)

    try:
        await websocket.send_json({
            'type': 'workspace.snapshot',
            'auth_user': auth_user,
            **snapshot,
        })

        while True:
            message = await websocket.receive_text()
            if message == 'ping':
                await websocket.send_json({'type': 'pong'})
    except WebSocketDisconnect:
        realtime_sync.unsubscribe(channel_key, websocket)
    except Exception:
        realtime_sync.unsubscribe(channel_key, websocket)

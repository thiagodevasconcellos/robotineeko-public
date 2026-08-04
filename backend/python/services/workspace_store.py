import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(__file__).resolve().parents[1] / 'data' / 'workspace.db'
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30000
BACKTEST_JOB_TERMINAL_RETENTION_SECONDS = 30 * 60
BACKTEST_JOB_TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}
GENERIC_STRATEGY_ENTRY_LABEL_PATTERN = re.compile(r'^strategy\s+\d+(?:\s+[·-]\s+(?:long/short|long|short))?$', re.IGNORECASE)

STRATEGY_SCORE_CRITERIA = [
    ('net_profit_factor', 'higher', 1.75, 0.28),
    ('max_drawdown_pct', 'lower', 0.10, 0.24),
    ('sharpe_ratio', 'higher', 1.50, 0.16),
    ('sortino_ratio', 'higher', 2.00, 0.12),
    ('win_rate', 'higher', 0.55, 0.10),
    ('risk_reward_ratio', 'higher', 1.50, 0.06),
    ('recovery_factor', 'higher', 2.00, 0.02),
    ('kelly_fraction', 'higher', 0.20, 0.02),
]


def _to_finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number != number or number in (float('inf'), float('-inf')):
        return None

    return number


def _clamp01(value):
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def compute_strategy_score_from_state(state: dict | None):
    stats = ((state or {}).get('strategyResponse') or {}).get('stats') or {}
    if not isinstance(stats, dict) or not stats:
        return None

    weighted_score = 0.0
    total_weight = 0.0

    for key, direction, target, weight in STRATEGY_SCORE_CRITERIA:
        raw_value = _to_finite_float(stats.get(key))
        if raw_value is None or target <= 0:
            continue

        if key == 'max_drawdown_pct':
            raw_value = abs(raw_value)

        if direction == 'higher':
            score = _clamp01(raw_value / target)
        else:
            score = 1.0 if raw_value <= 0 else _clamp01(target / raw_value)

        weighted_score += score * weight
        total_weight += weight

    if total_weight <= 0:
        return None

    return round((weighted_score / total_weight) * 10.0, 2)


def _connect_workspace_db():
    connection = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS)
    connection.execute(f'PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}')
    return connection


def _normalize_timestamp_seconds(value):
    parsed = _to_finite_float(value)
    if parsed is None or parsed <= 0:
        return time.time()
    return parsed / 1000.0 if parsed >= 1_000_000_000_000 else parsed


def _deserialize_timestamp_value(value):
    parsed = _to_finite_float(value)
    if parsed is not None:
        return parsed / 1000.0 if parsed >= 1_000_000_000_000 else parsed
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _normalize_broker_profile_id(value):
    return str(value or '').strip()


def _normalize_broker_profile_label(value):
    return str(value or '').strip()


def _clone_json_serializable(value, fallback=None):
    try:
        return json.loads(json.dumps(value, ensure_ascii=True))
    except (TypeError, ValueError):
        return fallback


def _normalize_strategy_entry_market_value(value, fallback=''):
    return str(value or fallback or '').strip().upper()


def _is_generic_strategy_entry_label(value):
    return bool(GENERIC_STRATEGY_ENTRY_LABEL_PATTERN.fullmatch(str(value or '').strip()))


def _build_strategy_signature(strategy):
    safe_strategy = strategy if isinstance(strategy, dict) else {}
    normalized_indicators = []
    feature_manifest = safe_strategy.get('featureManifest') if isinstance(safe_strategy.get('featureManifest'), dict) else {}
    for indicator in feature_manifest.get('indicators') or []:
        if not isinstance(indicator, dict):
            continue
        normalized_indicators.append({
            'name': str(indicator.get('name') or '').strip(),
            'alias': str(indicator.get('alias') or '').strip(),
            'params': _clone_json_serializable(indicator.get('params'), []),
        })
    normalized_strategy = {
        'long': {
            'openPrice': str(((safe_strategy.get('long') or {}) if isinstance(safe_strategy.get('long'), dict) else {}).get('openPrice') or '').strip(),
            'closePrice': str(((safe_strategy.get('long') or {}) if isinstance(safe_strategy.get('long'), dict) else {}).get('closePrice') or '').strip(),
            'openIf': str(((safe_strategy.get('long') or {}) if isinstance(safe_strategy.get('long'), dict) else {}).get('openIf') or '').strip(),
            'closeIf': str(((safe_strategy.get('long') or {}) if isinstance(safe_strategy.get('long'), dict) else {}).get('closeIf') or '').strip(),
            'gainPrice': str(((safe_strategy.get('long') or {}) if isinstance(safe_strategy.get('long'), dict) else {}).get('gainPrice') or '').strip(),
            'lossPrice': str(((safe_strategy.get('long') or {}) if isinstance(safe_strategy.get('long'), dict) else {}).get('lossPrice') or '').strip(),
            'trailingPrice': str(((safe_strategy.get('long') or {}) if isinstance(safe_strategy.get('long'), dict) else {}).get('trailingPrice') or '').strip(),
        },
        'short': {
            'openPrice': str(((safe_strategy.get('short') or {}) if isinstance(safe_strategy.get('short'), dict) else {}).get('openPrice') or '').strip(),
            'closePrice': str(((safe_strategy.get('short') or {}) if isinstance(safe_strategy.get('short'), dict) else {}).get('closePrice') or '').strip(),
            'openIf': str(((safe_strategy.get('short') or {}) if isinstance(safe_strategy.get('short'), dict) else {}).get('openIf') or '').strip(),
            'closeIf': str(((safe_strategy.get('short') or {}) if isinstance(safe_strategy.get('short'), dict) else {}).get('closeIf') or '').strip(),
            'gainPrice': str(((safe_strategy.get('short') or {}) if isinstance(safe_strategy.get('short'), dict) else {}).get('gainPrice') or '').strip(),
            'lossPrice': str(((safe_strategy.get('short') or {}) if isinstance(safe_strategy.get('short'), dict) else {}).get('lossPrice') or '').strip(),
            'trailingPrice': str(((safe_strategy.get('short') or {}) if isinstance(safe_strategy.get('short'), dict) else {}).get('trailingPrice') or '').strip(),
        },
        'other': {
            'allowInversion': bool(((safe_strategy.get('other') or {}) if isinstance(safe_strategy.get('other'), dict) else {}).get('allowInversion')),
            'priority': str(((safe_strategy.get('other') or {}) if isinstance(safe_strategy.get('other'), dict) else {}).get('priority') or '').strip(),
        },
        'featureManifest': {
            'indicators': normalized_indicators,
        },
    }
    try:
        return json.dumps(normalized_strategy, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        return ''


def _load_workspace_strategy_benchmark_definition(connection, user_id: str, workspace_id: str, benchmark_id: str):
    safe_benchmark_id = str(benchmark_id or '').strip()
    if not safe_benchmark_id:
        return None

    row = connection.execute(
        '''
        SELECT id, label, symbol, timeframe, strategy_json, strategies_json
        FROM workspace_strategy_benchmarks
        WHERE user_id = ? AND workspace_id = ? AND id = ?
        ''',
        (user_id, workspace_id, int(safe_benchmark_id)),
    ).fetchone()

    if not row:
        return None

    try:
        strategy = json.loads(row[4] or '{}')
    except (TypeError, ValueError):
        strategy = {}

    try:
        strategies = json.loads(row[5] or '[]')
    except (TypeError, ValueError):
        strategies = []

    return {
        'id': str(row[0]),
        'label': str(row[1] or '').strip(),
        'symbol': _normalize_strategy_entry_market_value(row[2]),
        'timeframe': _normalize_strategy_entry_market_value(row[3]),
        'strategy': strategy if isinstance(strategy, dict) else {},
        'strategies': strategies if isinstance(strategies, list) else [],
    }


def _repair_benchmark_derived_strategy_entries(
    connection,
    user_id: str,
    workspace_id: str,
    primary_strategy,
    entries,
):
    if not isinstance(primary_strategy, dict) or not isinstance(entries, list) or not entries:
        return primary_strategy, entries, False

    safe_entries = [entry for entry in entries if isinstance(entry, dict)]
    if len(safe_entries) != len(entries):
        return primary_strategy, entries, False

    benchmark_groups: dict[str, list[dict]] = {}
    benchmark_order: list[str] = []
    for entry in safe_entries:
        benchmark_id = str(entry.get('sourceBenchmarkId') or '').strip()
        if not benchmark_id:
            continue
        if benchmark_id not in benchmark_groups:
            benchmark_groups[benchmark_id] = []
            benchmark_order.append(benchmark_id)
        benchmark_groups[benchmark_id].append(entry)

    if not benchmark_groups:
        return primary_strategy, entries, False

    benchmark_cache: dict[str, dict | None] = {}
    root_signature = _build_strategy_signature(primary_strategy)
    repaired_entries: list[dict] = []
    consumed_benchmark_ids: set[str] = set()
    changed = False

    def load_benchmark(benchmark_id: str):
        if benchmark_id not in benchmark_cache:
            benchmark_cache[benchmark_id] = _load_workspace_strategy_benchmark_definition(
                connection,
                user_id,
                workspace_id,
                benchmark_id,
            )
        return benchmark_cache[benchmark_id]

    def pop_matching_entry(pool, *, strategy_signature='', label=''):
        safe_label = str(label or '').strip()
        for index, candidate in enumerate(pool):
            candidate_signature = _build_strategy_signature(candidate.get('strategy'))
            if strategy_signature and candidate_signature == strategy_signature:
                return pool.pop(index)
            if safe_label and (
                str(candidate.get('sourceBenchmarkEntryLabel') or '').strip() == safe_label
                or str(candidate.get('label') or '').strip() == safe_label
            ):
                return pool.pop(index)
        return None

    def repair_group(benchmark_id: str, group_entries: list[dict]):
        benchmark = load_benchmark(benchmark_id)
        if not benchmark:
            return [_clone_json_serializable(entry, {}) for entry in group_entries]

        working_pool = [_clone_json_serializable(entry, {}) for entry in group_entries]
        benchmark_label = str(benchmark.get('label') or '').strip()
        benchmark_symbol = _normalize_strategy_entry_market_value(benchmark.get('symbol'))
        benchmark_timeframe = _normalize_strategy_entry_market_value(benchmark.get('timeframe'))
        benchmark_primary = benchmark.get('strategy') if isinstance(benchmark.get('strategy'), dict) else {}
        benchmark_primary_signature = _build_strategy_signature(benchmark_primary)

        primary_entry = None
        if root_signature:
            primary_entry = pop_matching_entry(working_pool, strategy_signature=root_signature)
        if primary_entry is None and benchmark_primary_signature:
            primary_entry = pop_matching_entry(working_pool, strategy_signature=benchmark_primary_signature)
        if primary_entry is None and benchmark_label:
            primary_entry = pop_matching_entry(working_pool, label=benchmark_label)
        if primary_entry is None and working_pool:
            primary_entry = working_pool.pop(0)

        if primary_entry is None:
            return [_clone_json_serializable(entry, {}) for entry in group_entries]

        displaced_primary_source_label = str(primary_entry.get('sourceBenchmarkEntryLabel') or '').strip()
        primary_source_label = str(primary_entry.get('sourceBenchmarkEntryLabel') or '').strip()
        if not primary_source_label or _is_generic_strategy_entry_label(primary_source_label) or primary_source_label == benchmark_label:
            fallback_source_label = str(primary_entry.get('label') or '').strip()
            if fallback_source_label and fallback_source_label != benchmark_label and not _is_generic_strategy_entry_label(fallback_source_label):
                primary_source_label = fallback_source_label

        repaired_group = []
        repaired_primary = dict(primary_entry)
        repaired_primary['label'] = benchmark_label or str(primary_entry.get('label') or '').strip()
        repaired_primary['sourceBenchmarkId'] = benchmark_id
        repaired_primary['sourceBenchmarkLabel'] = benchmark_label
        repaired_primary['sourceBenchmarkEntryLabel'] = primary_source_label
        repaired_primary['strategy'] = _clone_json_serializable(benchmark_primary, benchmark_primary)
        repaired_primary['symbol'] = _normalize_strategy_entry_market_value(repaired_primary.get('symbol'), benchmark_symbol)
        repaired_primary['timeframe'] = _normalize_strategy_entry_market_value(repaired_primary.get('timeframe'), benchmark_timeframe)
        repaired_group.append(repaired_primary)

        for companion in benchmark.get('strategies') or []:
            if not isinstance(companion, dict):
                continue
            companion_label = str(companion.get('label') or '').strip()
            companion_strategy = companion.get('strategy') if isinstance(companion.get('strategy'), dict) else {}
            companion_signature = _build_strategy_signature(companion_strategy)
            matched_entry = pop_matching_entry(
                working_pool,
                strategy_signature=companion_signature,
                label=companion_label,
            )
            if matched_entry is None and companion_label and companion_label == displaced_primary_source_label:
                matched_entry = {
                    'id': f"{str(primary_entry.get('id') or benchmark_id).strip() or benchmark_id}-repaired-{len(repaired_group)}",
                    'enabled': primary_entry.get('enabled') is not False,
                    'allocationMode': primary_entry.get('allocationMode'),
                    'allocationValue': primary_entry.get('allocationValue'),
                    'symbol': primary_entry.get('symbol'),
                    'timeframe': primary_entry.get('timeframe'),
                }
            if matched_entry is None:
                continue
            repaired_companion = dict(matched_entry)
            repaired_companion['label'] = companion_label or str(matched_entry.get('label') or '').strip()
            repaired_companion['sourceBenchmarkId'] = benchmark_id
            repaired_companion['sourceBenchmarkLabel'] = benchmark_label
            repaired_companion['sourceBenchmarkEntryLabel'] = companion_label
            repaired_companion['strategy'] = _clone_json_serializable(companion_strategy, companion_strategy)
            repaired_companion['symbol'] = _normalize_strategy_entry_market_value(
                repaired_companion.get('symbol'),
                _normalize_strategy_entry_market_value(companion.get('symbol'), benchmark_symbol),
            )
            repaired_companion['timeframe'] = _normalize_strategy_entry_market_value(
                repaired_companion.get('timeframe'),
                _normalize_strategy_entry_market_value(companion.get('timeframe'), benchmark_timeframe),
            )
            repaired_group.append(repaired_companion)

        for leftover in working_pool:
            if _build_strategy_signature(leftover.get('strategy')) == _build_strategy_signature(repaired_primary.get('strategy')):
                changed = True
                continue
            repaired_leftover = dict(leftover)
            source_label = str(repaired_leftover.get('sourceBenchmarkEntryLabel') or '').strip()
            current_label = str(repaired_leftover.get('label') or '').strip()
            if (
                source_label
                and source_label != benchmark_label
                and (current_label == benchmark_label or _is_generic_strategy_entry_label(current_label))
            ):
                repaired_leftover['label'] = source_label
            repaired_leftover['sourceBenchmarkId'] = benchmark_id
            repaired_leftover['sourceBenchmarkLabel'] = benchmark_label
            repaired_leftover['symbol'] = _normalize_strategy_entry_market_value(repaired_leftover.get('symbol'), benchmark_symbol)
            repaired_leftover['timeframe'] = _normalize_strategy_entry_market_value(repaired_leftover.get('timeframe'), benchmark_timeframe)
            repaired_group.append(repaired_leftover)

        return repaired_group

    for entry in entries:
        if not isinstance(entry, dict):
            repaired_entries.append(entry)
            continue
        benchmark_id = str(entry.get('sourceBenchmarkId') or '').strip()
        if not benchmark_id:
            repaired_entries.append(_clone_json_serializable(entry, entry))
            continue
        if benchmark_id in consumed_benchmark_ids:
            changed = True
            continue
        consumed_benchmark_ids.add(benchmark_id)
        repaired_entries.extend(repair_group(benchmark_id, benchmark_groups.get(benchmark_id) or [entry]))

    for index, entry in enumerate(repaired_entries):
        if isinstance(entry, dict):
            entry['priority'] = index

    repaired_primary_strategy = primary_strategy
    if repaired_entries and isinstance(repaired_entries[0], dict) and isinstance(repaired_entries[0].get('strategy'), dict):
        first_strategy = repaired_entries[0].get('strategy') or {}
        if _build_strategy_signature(first_strategy) != root_signature:
            repaired_primary_strategy = _clone_json_serializable(first_strategy, first_strategy)
            changed = True

    if json.dumps(repaired_entries, ensure_ascii=True, sort_keys=True) != json.dumps(entries, ensure_ascii=True, sort_keys=True):
        changed = True

    return repaired_primary_strategy, repaired_entries, changed


def _repair_workspace_strategy_collections(connection, user_id: str, workspace_id: str, state):
    if not isinstance(state, dict) or not state:
        return state if isinstance(state, dict) else {}

    repaired_state = _clone_json_serializable(state, {}) or {}
    changed = False

    primary_strategy, repaired_entries, collection_changed = _repair_benchmark_derived_strategy_entries(
        connection,
        user_id,
        workspace_id,
        repaired_state.get('strategy'),
        repaired_state.get('backtestStrategySet'),
    )
    if collection_changed:
        repaired_state['strategy'] = primary_strategy
        repaired_state['backtestStrategySet'] = repaired_entries
        changed = True

    for response_key in ('strategyResponse', 'backtestRunResponse'):
        response = repaired_state.get(response_key)
        if not isinstance(response, dict):
            continue
        request = response.get('request')
        if not isinstance(request, dict):
            continue
        request_strategy, request_entries, request_changed = _repair_benchmark_derived_strategy_entries(
            connection,
            user_id,
            workspace_id,
            request.get('strategy'),
            request.get('strategies'),
        )
        if not request_changed:
            continue
        repaired_request = dict(request)
        repaired_request['strategy'] = request_strategy
        repaired_request['strategies'] = request_entries
        repaired_response = dict(response)
        repaired_response['request'] = repaired_request
        repaired_state[response_key] = repaired_response
        changed = True

    return repaired_state if changed else state


def _deserialize_workspace_broker_profile(row):
    if not row:
        return None
    return {
        'id': str(row[0]),
        'label': str(row[1] or '').strip(),
        'broker_code': str(row[2] or '').strip(),
        'connector_kind': str(row[3] or '').strip() or 'mt5',
        'server_name': str(row[4] or '').strip(),
        'market_domain': str(row[5] or '').strip(),
        'base_currency': str(row[6] or '').strip().upper(),
        'notes': str(row[7] or '').strip(),
        'is_default': bool(row[8]),
        'is_favorite': bool(row[9]),
        'profile': json.loads(row[10] or '{}'),
        'created_at': float(row[11]) if row[11] is not None else None,
        'updated_at': float(row[12]) if row[12] is not None else None,
    }


def _get_workspace_broker_profile_row(connection, user_id: str, workspace_id: str, broker_profile_id: str | int | None):
    safe_id = _normalize_broker_profile_id(broker_profile_id)
    if not safe_id:
        return None
    try:
        numeric_id = int(safe_id)
    except (TypeError, ValueError):
        return None
    return connection.execute(
        '''
        SELECT id, label, broker_code, connector_kind, server_name, market_domain, base_currency, notes,
               is_default, is_favorite, profile_json, created_at, updated_at
        FROM workspace_broker_profiles
        WHERE user_id = ? AND workspace_id = ? AND id = ?
        ''',
        (user_id, workspace_id, numeric_id),
    ).fetchone()


def _get_workspace_default_broker_profile_row(connection, user_id: str, workspace_id: str):
    return connection.execute(
        '''
        SELECT id, label, broker_code, connector_kind, server_name, market_domain, base_currency, notes,
               is_default, is_favorite, profile_json, created_at, updated_at
        FROM workspace_broker_profiles
        WHERE user_id = ? AND workspace_id = ?
        ORDER BY is_default DESC, updated_at DESC, id DESC
        LIMIT 1
        ''',
        (user_id, workspace_id),
    ).fetchone()


def _resolve_workspace_broker_profile_scope(
    connection,
    user_id: str,
    workspace_id: str,
    *,
    broker_profile_id: str | int | None = None,
    broker_profile_label: str | None = None,
):
    resolved_row = _get_workspace_broker_profile_row(connection, user_id, workspace_id, broker_profile_id)
    if not resolved_row:
        resolved_row = _get_workspace_default_broker_profile_row(connection, user_id, workspace_id)
    if not resolved_row:
        return {
            'broker_profile_id': _normalize_broker_profile_id(broker_profile_id),
            'broker_profile_label': _normalize_broker_profile_label(broker_profile_label),
        }
    resolved = _deserialize_workspace_broker_profile(resolved_row) or {}
    return {
        'broker_profile_id': str(resolved.get('id') or '').strip(),
        'broker_profile_label': str(resolved.get('label') or '').strip(),
    }


def _cascade_workspace_broker_profile_label(
    connection,
    user_id: str,
    workspace_id: str,
    broker_profile_id: str,
    broker_profile_label: str,
):
    safe_profile_id = _normalize_broker_profile_id(broker_profile_id)
    if not safe_profile_id:
        return
    safe_profile_label = _normalize_broker_profile_label(broker_profile_label)
    for table_name in (
        'workspace_strategy_benchmarks',
        'workspace_saved_portfolios',
        'workspace_live_trades',
        'workspace_trade_reconciliations',
    ):
        connection.execute(
            f'''
            UPDATE {table_name}
            SET broker_profile_label = ?
            WHERE user_id = ? AND workspace_id = ? AND broker_profile_id = ?
            ''',
            (safe_profile_label, user_id, workspace_id, safe_profile_id),
        )


def _adopt_legacy_workspace_broker_profile_scope(
    connection,
    user_id: str,
    workspace_id: str,
    broker_profile_id: str,
    broker_profile_label: str,
):
    safe_profile_id = _normalize_broker_profile_id(broker_profile_id)
    if not safe_profile_id:
        return
    safe_profile_label = _normalize_broker_profile_label(broker_profile_label)
    for table_name in (
        'workspace_strategy_benchmarks',
        'workspace_saved_portfolios',
        'workspace_live_trades',
        'workspace_trade_reconciliations',
    ):
        connection.execute(
            f'''
            UPDATE {table_name}
            SET broker_profile_id = ?, broker_profile_label = ?
            WHERE user_id = ? AND workspace_id = ? AND COALESCE(broker_profile_id, '') = ''
            ''',
            (safe_profile_id, safe_profile_label, user_id, workspace_id),
        )


def ensure_workspace_store():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_state (
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                state_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, workspace_id)
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_saves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                score REAL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                run_type TEXT NOT NULL,
                side TEXT,
                run_name TEXT NOT NULL,
                version TEXT,
                best_id TEXT,
                best_label TEXT,
                comparison_count INTEGER,
                run_label TEXT,
                run_notes TEXT,
                pinned INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_research_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                phase TEXT,
                phase_label TEXT,
                detail TEXT,
                error TEXT,
                run_id INTEGER,
                run_label TEXT,
                run_notes TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                updated_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_backtest_jobs (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                phase TEXT,
                phase_label TEXT,
                detail TEXT,
                error TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                updated_at REAL NOT NULL,
                expires_at REAL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_research_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                phase TEXT,
                phase_label TEXT,
                detail TEXT,
                error TEXT,
                total_jobs INTEGER NOT NULL DEFAULT 0,
                completed_jobs INTEGER NOT NULL DEFAULT 0,
                failed_jobs INTEGER NOT NULL DEFAULT 0,
                cancelled_jobs INTEGER NOT NULL DEFAULT 0,
                current_job_id INTEGER,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                updated_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_strategy_benchmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                label TEXT NOT NULL,
                side TEXT,
                source TEXT,
                notes TEXT,
                is_favorite INTEGER NOT NULL DEFAULT 0,
                symbol TEXT,
                timeframe TEXT,
                strategy_json TEXT NOT NULL,
                strategies_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_saved_portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                label TEXT NOT NULL,
                source TEXT,
                notes TEXT,
                is_favorite INTEGER NOT NULL DEFAULT 0,
                portfolio_json TEXT NOT NULL DEFAULT '{}',
                capital_model_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_broker_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                label TEXT NOT NULL,
                broker_code TEXT NOT NULL,
                connector_kind TEXT NOT NULL DEFAULT 'mt5',
                server_name TEXT,
                market_domain TEXT,
                base_currency TEXT,
                notes TEXT,
                is_default INTEGER NOT NULL DEFAULT 0,
                is_favorite INTEGER NOT NULL DEFAULT 0,
                profile_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_broker_profiles_scope_updated
            ON workspace_broker_profiles (user_id, workspace_id, is_default DESC, updated_at DESC, id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_research_papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                project_key TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                discipline TEXT,
                symbol TEXT,
                timeframe TEXT,
                summary TEXT,
                current_version_number INTEGER NOT NULL DEFAULT 1,
                current_version_id INTEGER,
                current_article_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(user_id, workspace_id, project_key)
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_research_paper_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                paper_id INTEGER NOT NULL,
                version_number INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                discipline TEXT,
                symbol TEXT,
                timeframe TEXT,
                summary TEXT,
                change_note TEXT,
                article_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES workspace_research_papers(id) ON DELETE CASCADE,
                UNIQUE(user_id, workspace_id, paper_id, version_number)
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_research_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                label TEXT NOT NULL,
                description TEXT,
                request_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_system_log_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                closed_at REAL,
                updated_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_system_log_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_id INTEGER NOT NULL,
                client_entry_id TEXT,
                level TEXT NOT NULL,
                source TEXT,
                scope TEXT,
                category TEXT,
                message TEXT NOT NULL,
                context_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(session_id) REFERENCES workspace_system_log_sessions(id) ON DELETE CASCADE,
                UNIQUE(user_id, workspace_id, client_entry_id)
            )
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_system_log_sessions_scope_status
            ON workspace_system_log_sessions (user_id, workspace_id, status, updated_at DESC, id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_system_log_entries_session_created
            ON workspace_system_log_entries (session_id, created_at DESC, id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_research_runs_scope_created
            ON workspace_research_runs (user_id, workspace_id, pinned DESC, created_at DESC, id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_research_jobs_scope_created
            ON workspace_research_jobs (user_id, workspace_id, created_at DESC, id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_backtest_jobs_scope_created
            ON workspace_backtest_jobs (user_id, workspace_id, created_at DESC, job_id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_backtest_jobs_scope_expires
            ON workspace_backtest_jobs (user_id, workspace_id, expires_at ASC, job_id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_research_batches_scope_created
            ON workspace_research_batches (user_id, workspace_id, created_at DESC, id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_workspace_research_campaigns_scope_updated
            ON workspace_research_campaigns (user_id, workspace_id, updated_at DESC, id DESC)
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_live_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                source_intent_id TEXT,
                execution_mode TEXT NOT NULL,
                portfolio_mode TEXT,
                portfolio_id TEXT,
                portfolio_label TEXT,
                pipeline_id TEXT,
                pipeline_label TEXT,
                status TEXT NOT NULL,
                sleeve_id TEXT,
                sleeve_label TEXT,
                source_strategy_id TEXT,
                cycle_id TEXT,
                symbol TEXT,
                timeframe TEXT,
                action TEXT,
                side TEXT,
                bar_time REAL,
                created_at REAL,
                claimed_at REAL,
                acknowledged_at REAL,
                filled_at REAL,
                rejected_at REAL,
                broker_order_id TEXT,
                broker_position_ticket TEXT,
                broker_deal_id TEXT,
                fill_price REAL,
                fill_volume REAL,
                profit REAL,
                commission REAL,
                swap REAL,
                exit_reason TEXT,
                message TEXT,
                strategy_json TEXT,
                broker_profile_id TEXT,
                broker_profile_label TEXT,
                record_created_at REAL NOT NULL,
                UNIQUE(user_id, workspace_id, command_id)
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS workspace_trade_reconciliations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                range_key TEXT NOT NULL,
                custom_days INTEGER,
                strategy_filter TEXT,
                broker_profile_id TEXT,
                broker_profile_label TEXT,
                summary_json TEXT NOT NULL,
                rows_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            '''
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_saves)").fetchall()
        }
        if 'score' not in columns:
            connection.execute('ALTER TABLE workspace_saves ADD COLUMN score REAL')
        research_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_research_runs)").fetchall()
        }
        if 'run_label' not in research_columns:
            connection.execute('ALTER TABLE workspace_research_runs ADD COLUMN run_label TEXT')
        if 'run_notes' not in research_columns:
            connection.execute('ALTER TABLE workspace_research_runs ADD COLUMN run_notes TEXT')
        if 'pinned' not in research_columns:
            connection.execute('ALTER TABLE workspace_research_runs ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
        research_job_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_research_jobs)").fetchall()
        }
        if 'run_label' not in research_job_columns:
            connection.execute('ALTER TABLE workspace_research_jobs ADD COLUMN run_label TEXT')
        if 'run_notes' not in research_job_columns:
            connection.execute('ALTER TABLE workspace_research_jobs ADD COLUMN run_notes TEXT')
        if 'cancel_requested' not in research_job_columns:
            connection.execute('ALTER TABLE workspace_research_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0')
        backtest_job_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_backtest_jobs)").fetchall()
        }
        if 'cancel_requested' not in backtest_job_columns:
            connection.execute('ALTER TABLE workspace_backtest_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0')
        if 'expires_at' not in backtest_job_columns:
            connection.execute('ALTER TABLE workspace_backtest_jobs ADD COLUMN expires_at REAL')
        research_batch_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_research_batches)").fetchall()
        }
        if 'cancel_requested' not in research_batch_columns:
            connection.execute('ALTER TABLE workspace_research_batches ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0')
        strategy_benchmark_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_strategy_benchmarks)").fetchall()
        }
        if 'strategies_json' not in strategy_benchmark_columns:
            connection.execute("ALTER TABLE workspace_strategy_benchmarks ADD COLUMN strategies_json TEXT NOT NULL DEFAULT '[]'")
        if 'is_favorite' not in strategy_benchmark_columns:
            connection.execute('ALTER TABLE workspace_strategy_benchmarks ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0')
        if 'symbol' not in strategy_benchmark_columns:
            connection.execute('ALTER TABLE workspace_strategy_benchmarks ADD COLUMN symbol TEXT')
        if 'timeframe' not in strategy_benchmark_columns:
            connection.execute('ALTER TABLE workspace_strategy_benchmarks ADD COLUMN timeframe TEXT')
        saved_portfolio_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_saved_portfolios)").fetchall()
        }
        if 'source' not in saved_portfolio_columns:
            connection.execute('ALTER TABLE workspace_saved_portfolios ADD COLUMN source TEXT')
        if 'notes' not in saved_portfolio_columns:
            connection.execute('ALTER TABLE workspace_saved_portfolios ADD COLUMN notes TEXT')
        if 'is_favorite' not in saved_portfolio_columns:
            connection.execute('ALTER TABLE workspace_saved_portfolios ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0')
        if 'portfolio_json' not in saved_portfolio_columns:
            connection.execute("ALTER TABLE workspace_saved_portfolios ADD COLUMN portfolio_json TEXT NOT NULL DEFAULT '{}'")
        if 'capital_model_json' not in saved_portfolio_columns:
            connection.execute("ALTER TABLE workspace_saved_portfolios ADD COLUMN capital_model_json TEXT NOT NULL DEFAULT '{}'")
        if 'updated_at' not in saved_portfolio_columns:
            connection.execute('ALTER TABLE workspace_saved_portfolios ADD COLUMN updated_at REAL')
            connection.execute('UPDATE workspace_saved_portfolios SET updated_at = created_at WHERE updated_at IS NULL')
        if 'broker_profile_id' not in saved_portfolio_columns:
            connection.execute('ALTER TABLE workspace_saved_portfolios ADD COLUMN broker_profile_id TEXT')
        if 'broker_profile_label' not in saved_portfolio_columns:
            connection.execute('ALTER TABLE workspace_saved_portfolios ADD COLUMN broker_profile_label TEXT')
        broker_profile_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_broker_profiles)").fetchall()
        }
        if 'broker_code' not in broker_profile_columns:
            connection.execute("ALTER TABLE workspace_broker_profiles ADD COLUMN broker_code TEXT NOT NULL DEFAULT 'manual'")
        if 'connector_kind' not in broker_profile_columns:
            connection.execute("ALTER TABLE workspace_broker_profiles ADD COLUMN connector_kind TEXT NOT NULL DEFAULT 'mt5'")
        if 'server_name' not in broker_profile_columns:
            connection.execute('ALTER TABLE workspace_broker_profiles ADD COLUMN server_name TEXT')
        if 'market_domain' not in broker_profile_columns:
            connection.execute('ALTER TABLE workspace_broker_profiles ADD COLUMN market_domain TEXT')
        if 'base_currency' not in broker_profile_columns:
            connection.execute('ALTER TABLE workspace_broker_profiles ADD COLUMN base_currency TEXT')
        if 'notes' not in broker_profile_columns:
            connection.execute('ALTER TABLE workspace_broker_profiles ADD COLUMN notes TEXT')
        if 'is_default' not in broker_profile_columns:
            connection.execute('ALTER TABLE workspace_broker_profiles ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0')
        if 'is_favorite' not in broker_profile_columns:
            connection.execute('ALTER TABLE workspace_broker_profiles ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0')
        if 'profile_json' not in broker_profile_columns:
            connection.execute("ALTER TABLE workspace_broker_profiles ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}'")
        if 'updated_at' not in broker_profile_columns:
            connection.execute('ALTER TABLE workspace_broker_profiles ADD COLUMN updated_at REAL')
            connection.execute('UPDATE workspace_broker_profiles SET updated_at = created_at WHERE updated_at IS NULL')
        research_paper_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_research_papers)").fetchall()
        }
        if 'discipline' not in research_paper_columns:
            connection.execute("ALTER TABLE workspace_research_papers ADD COLUMN discipline TEXT")
        if 'symbol' not in research_paper_columns:
            connection.execute("ALTER TABLE workspace_research_papers ADD COLUMN symbol TEXT")
        if 'timeframe' not in research_paper_columns:
            connection.execute("ALTER TABLE workspace_research_papers ADD COLUMN timeframe TEXT")
        if 'summary' not in research_paper_columns:
            connection.execute("ALTER TABLE workspace_research_papers ADD COLUMN summary TEXT")
        if 'status' not in research_paper_columns:
            connection.execute("ALTER TABLE workspace_research_papers ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
        if 'current_version_number' not in research_paper_columns:
            connection.execute("ALTER TABLE workspace_research_papers ADD COLUMN current_version_number INTEGER NOT NULL DEFAULT 1")
        if 'current_version_id' not in research_paper_columns:
            connection.execute("ALTER TABLE workspace_research_papers ADD COLUMN current_version_id INTEGER")
        if 'current_article_json' not in research_paper_columns:
            connection.execute("ALTER TABLE workspace_research_papers ADD COLUMN current_article_json TEXT NOT NULL DEFAULT '{}'")
        live_trade_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_live_trades)").fetchall()
        }
        if 'source_strategy_id' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN source_strategy_id TEXT')
        if 'portfolio_id' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN portfolio_id TEXT')
        if 'portfolio_label' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN portfolio_label TEXT')
        if 'pipeline_id' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN pipeline_id TEXT')
        if 'pipeline_label' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN pipeline_label TEXT')
        if 'cycle_id' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN cycle_id TEXT')
        if 'profit' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN profit REAL')
        if 'commission' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN commission REAL')
        if 'swap' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN swap REAL')
        if 'strategy_json' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN strategy_json TEXT')
        if 'exit_reason' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN exit_reason TEXT')
        if 'broker_position_ticket' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN broker_position_ticket TEXT')
        if 'broker_profile_id' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN broker_profile_id TEXT')
        if 'broker_profile_label' not in live_trade_columns:
            connection.execute('ALTER TABLE workspace_live_trades ADD COLUMN broker_profile_label TEXT')
        trade_reconciliation_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workspace_trade_reconciliations)").fetchall()
        }
        if 'broker_profile_id' not in trade_reconciliation_columns:
            connection.execute('ALTER TABLE workspace_trade_reconciliations ADD COLUMN broker_profile_id TEXT')
        if 'broker_profile_label' not in trade_reconciliation_columns:
            connection.execute('ALTER TABLE workspace_trade_reconciliations ADD COLUMN broker_profile_label TEXT')
        if 'broker_profile_id' not in strategy_benchmark_columns:
            connection.execute('ALTER TABLE workspace_strategy_benchmarks ADD COLUMN broker_profile_id TEXT')
        if 'broker_profile_label' not in strategy_benchmark_columns:
            connection.execute('ALTER TABLE workspace_strategy_benchmarks ADD COLUMN broker_profile_label TEXT')
        connection.commit()


def _deserialize_workspace_live_trade(row):
    return {
        'id': int(row[0]),
        'command_id': str(row[1] or ''),
        'source_intent_id': str(row[2] or ''),
        'execution_mode': str(row[3] or ''),
        'portfolio_mode': str(row[4] or ''),
        'portfolio_id': str(row[5] or ''),
        'portfolio_label': str(row[6] or ''),
        'pipeline_id': str(row[7] or ''),
        'pipeline_label': str(row[8] or ''),
        'status': str(row[9] or ''),
        'sleeve_id': str(row[10] or ''),
        'sleeve_label': str(row[11] or ''),
        'source_strategy_id': str(row[12] or ''),
        'cycle_id': str(row[13] or ''),
        'symbol': str(row[14] or ''),
        'timeframe': str(row[15] or ''),
        'action': str(row[16] or ''),
        'side': str(row[17] or ''),
        'bar_time': float(row[18]) if row[18] is not None else None,
        'created_at': float(row[19]) if row[19] is not None else None,
        'claimed_at': float(row[20]) if row[20] is not None else None,
        'acknowledged_at': float(row[21]) if row[21] is not None else None,
        'filled_at': float(row[22]) if row[22] is not None else None,
        'rejected_at': float(row[23]) if row[23] is not None else None,
        'broker_order_id': str(row[24] or ''),
        'broker_position_ticket': str(row[25] or ''),
        'broker_deal_id': str(row[26] or ''),
        'fill_price': float(row[27]) if row[27] is not None else None,
        'fill_volume': float(row[28]) if row[28] is not None else None,
        'profit': float(row[29]) if row[29] is not None else None,
        'commission': float(row[30]) if row[30] is not None else None,
        'swap': float(row[31]) if row[31] is not None else None,
        'exit_reason': str(row[32] or ''),
        'message': str(row[33] or ''),
        'strategy': json.loads(row[34] or '{}'),
        'broker_profile_id': str(row[35] or '').strip(),
        'broker_profile_label': str(row[36] or '').strip(),
        'record_created_at': float(row[37]) if row[37] is not None else None,
    }


def _deserialize_workspace_research_run_summary(row):
    return {
        'id': int(row[0]),
        'type': str(row[1] or ''),
        'side': str(row[2] or ''),
        'run_name': str(row[3] or ''),
        'version': str(row[4] or ''),
        'best_id': str(row[5] or ''),
        'best_label': str(row[6] or ''),
        'comparison_count': int(row[7] or 0),
        'run_label': str(row[8] or ''),
        'run_notes': str(row[9] or ''),
        'pinned': bool(int(row[10] or 0)),
        'payload_loaded': False,
        'payload_size_bytes': int(row[11] or 0),
        'created_at': float(row[12]) if row[12] is not None else None,
    }


def _deserialize_workspace_research_run_detail(row):
    return {
        'id': int(row[0]),
        'type': str(row[1] or ''),
        'side': str(row[2] or ''),
        'run_name': str(row[3] or ''),
        'version': str(row[4] or ''),
        'best_id': str(row[5] or ''),
        'best_label': str(row[6] or ''),
        'comparison_count': int(row[7] or 0),
        'run_label': str(row[8] or ''),
        'run_notes': str(row[9] or ''),
        'pinned': bool(int(row[10] or 0)),
        'payload': json.loads(row[11] or '{}'),
        'payload_loaded': True,
        'payload_size_bytes': int(row[12] or 0),
        'created_at': float(row[13]) if row[13] is not None else None,
    }


def list_workspace_research_runs(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    include_payload: bool = True,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        if include_payload:
            rows = connection.execute(
                '''
                SELECT id, run_type, side, run_name, version, best_id, best_label, comparison_count, run_label, run_notes,
                       pinned, payload_json, length(payload_json), created_at
                FROM workspace_research_runs
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY pinned DESC, created_at DESC, id DESC
                LIMIT ?
                ''',
                (user_id, workspace_id, max(1, int(limit))),
            ).fetchall()
        else:
            rows = connection.execute(
                '''
                SELECT id, run_type, side, run_name, version, best_id, best_label, comparison_count, run_label, run_notes,
                       pinned, length(payload_json), created_at
                FROM workspace_research_runs
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY pinned DESC, created_at DESC, id DESC
                LIMIT ?
                ''',
                (user_id, workspace_id, max(1, int(limit))),
            ).fetchall()

    if include_payload:
        return [_deserialize_workspace_research_run_detail(row) for row in rows]
    return [_deserialize_workspace_research_run_summary(row) for row in rows]


def get_workspace_research_run(
    user_id: str,
    workspace_id: str,
    run_id: int,
    *,
    include_payload: bool = True,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        if include_payload:
            row = connection.execute(
                '''
                SELECT id, run_type, side, run_name, version, best_id, best_label, comparison_count, run_label, run_notes,
                       pinned, payload_json, length(payload_json), created_at
                FROM workspace_research_runs
                WHERE user_id = ? AND workspace_id = ? AND id = ?
                ''',
                (user_id, workspace_id, int(run_id)),
            ).fetchone()
        else:
            row = connection.execute(
                '''
                SELECT id, run_type, side, run_name, version, best_id, best_label, comparison_count, run_label, run_notes,
                       pinned, length(payload_json), created_at
                FROM workspace_research_runs
                WHERE user_id = ? AND workspace_id = ? AND id = ?
                ''',
                (user_id, workspace_id, int(run_id)),
            ).fetchone()

    if not row:
        return None
    if include_payload:
        return _deserialize_workspace_research_run_detail(row)
    return _deserialize_workspace_research_run_summary(row)


def _load_workspace_json_fragment(raw_value, default=None):
    if raw_value in (None, ''):
        return default
    if isinstance(raw_value, (dict, list)):
        return raw_value
    try:
        return json.loads(raw_value)
    except Exception:
        return default


def _deserialize_workspace_backtest_job_detail(row):
    return {
        'id': str(row[0] or ''),
        'status': str(row[1] or ''),
        'progress': float(row[2] or 0.0),
        'phase': str(row[3] or ''),
        'phase_label': str(row[4] or ''),
        'detail': str(row[5] or ''),
        'error': str(row[6] or ''),
        'cancel_requested': bool(int(row[7] or 0)),
        'request': json.loads(row[8] or '{}'),
        'result': json.loads(row[9] or '{}'),
        'request_loaded': True,
        'result_loaded': True,
        'request_size_bytes': int(row[10] or 0),
        'result_size_bytes': int(row[11] or 0),
        'created_at': float(row[12]) if row[12] is not None else None,
        'started_at': float(row[13]) if row[13] is not None else None,
        'finished_at': float(row[14]) if row[14] is not None else None,
        'updated_at': float(row[15]) if row[15] is not None else None,
        'expires_at': float(row[16]) if row[16] is not None else None,
    }


def _deserialize_workspace_backtest_job_summary(row):
    return {
        'id': str(row[0] or ''),
        'status': str(row[1] or ''),
        'progress': float(row[2] or 0.0),
        'phase': str(row[3] or ''),
        'phase_label': str(row[4] or ''),
        'detail': str(row[5] or ''),
        'error': str(row[6] or ''),
        'cancel_requested': bool(int(row[7] or 0)),
        'request': None,
        'result': None,
        'request_loaded': False,
        'result_loaded': False,
        'request_size_bytes': int(row[8] or 0),
        'result_size_bytes': int(row[9] or 0),
        'created_at': float(row[10]) if row[10] is not None else None,
        'started_at': float(row[11]) if row[11] is not None else None,
        'finished_at': float(row[12]) if row[12] is not None else None,
        'updated_at': float(row[13]) if row[13] is not None else None,
        'expires_at': float(row[14]) if row[14] is not None else None,
    }


def _deserialize_workspace_research_job_summary(row):
    return {
        'id': int(row[0]),
        'job_type': str(row[1] or ''),
        'status': str(row[2] or ''),
        'progress': float(row[3] or 0.0),
        'phase': str(row[4] or ''),
        'phase_label': str(row[5] or ''),
        'detail': str(row[6] or ''),
        'error': str(row[7] or ''),
        'run_id': int(row[8]) if row[8] is not None else None,
        'run_label': str(row[9] or ''),
        'run_notes': str(row[10] or ''),
        'cancel_requested': bool(int(row[11] or 0)),
        'request': json.loads(row[12] or '{}'),
        'request_loaded': True,
        'request_size_bytes': int(row[13] or 0),
        'result': (
            {
                'status': str(row[14] or ''),
                **(
                    {
                        'job_type': str(row[15] or row[1] or ''),
                        'pipeline': {
                            'label': str(row[16] or ''),
                            'chart': _load_workspace_json_fragment(row[17], {}),
                            'stats': _load_workspace_json_fragment(row[18], {}),
                        },
                    }
                    if str(row[1] or '').strip().lower() == 'strategy_pipeline' or row[16] or row[17] or row[18]
                    else {
                        'best_preset_id': row[20],
                        'comparison_count': int(row[21] or 0),
                    }
                ),
                **(
                    {'research': _load_workspace_json_fragment(row[19], None)}
                    if _load_workspace_json_fragment(row[19], None) is not None
                    else {}
                ),
            }
            if any(item is not None and item != '' for item in row[14:22])
            else {}
        ),
        'result_loaded': False,
        'result_size_bytes': int(row[22] or 0),
        'created_at': float(row[23]) if row[23] is not None else None,
        'started_at': float(row[24]) if row[24] is not None else None,
        'finished_at': float(row[25]) if row[25] is not None else None,
        'updated_at': float(row[26]) if row[26] is not None else None,
    }


def _deserialize_workspace_research_job_detail(row):
    return {
        'id': int(row[0]),
        'job_type': str(row[1] or ''),
        'status': str(row[2] or ''),
        'progress': float(row[3] or 0.0),
        'phase': str(row[4] or ''),
        'phase_label': str(row[5] or ''),
        'detail': str(row[6] or ''),
        'error': str(row[7] or ''),
        'run_id': int(row[8]) if row[8] is not None else None,
        'run_label': str(row[9] or ''),
        'run_notes': str(row[10] or ''),
        'cancel_requested': bool(int(row[11] or 0)),
        'request': json.loads(row[12] or '{}'),
        'result': json.loads(row[13] or '{}'),
        'request_loaded': True,
        'request_size_bytes': int(row[14] or 0),
        'result_loaded': True,
        'result_size_bytes': int(row[15] or 0),
        'created_at': float(row[16]) if row[16] is not None else None,
        'started_at': float(row[17]) if row[17] is not None else None,
        'finished_at': float(row[18]) if row[18] is not None else None,
        'updated_at': float(row[19]) if row[19] is not None else None,
    }


def list_workspace_research_jobs(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    include_payload: bool = True,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        if include_payload:
            rows = connection.execute(
                '''
                SELECT id, job_type, status, progress, phase, phase_label, detail, error, run_id, run_label, run_notes,
                       cancel_requested, request_json, result_json, length(request_json), length(result_json),
                       created_at, started_at, finished_at, updated_at
                FROM workspace_research_jobs
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                ''',
                (user_id, workspace_id, max(1, int(limit))),
            ).fetchall()
        else:
            rows = connection.execute(
                '''
                SELECT id, job_type, status, progress, phase, phase_label, detail, error, run_id, run_label, run_notes,
                       cancel_requested, request_json, length(request_json),
                       json_extract(result_json, '$.status'),
                       json_extract(result_json, '$.job_type'),
                       json_extract(result_json, '$.pipeline.label'),
                       json_extract(result_json, '$.pipeline.chart'),
                       json_extract(result_json, '$.pipeline.stats'),
                       json_extract(result_json, '$.research'),
                       json_extract(result_json, '$.best_preset_id'),
                       json_array_length(result_json, '$.comparisons'),
                       length(result_json),
                       created_at, started_at, finished_at, updated_at
                FROM workspace_research_jobs
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                ''',
                (user_id, workspace_id, max(1, int(limit))),
            ).fetchall()

    if include_payload:
        return [_deserialize_workspace_research_job_detail(row) for row in rows]
    return [_deserialize_workspace_research_job_summary(row) for row in rows]


def get_workspace_research_job(
    user_id: str,
    workspace_id: str,
    job_id: int,
    *,
    include_payload: bool = True,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        if include_payload:
            row = connection.execute(
                '''
                SELECT id, job_type, status, progress, phase, phase_label, detail, error, run_id, run_label, run_notes,
                       cancel_requested, request_json, result_json, length(request_json), length(result_json),
                       created_at, started_at, finished_at, updated_at
                FROM workspace_research_jobs
                WHERE user_id = ? AND workspace_id = ? AND id = ?
                ''',
                (user_id, workspace_id, int(job_id)),
            ).fetchone()
        else:
            row = connection.execute(
                '''
                SELECT id, job_type, status, progress, phase, phase_label, detail, error, run_id, run_label, run_notes,
                       cancel_requested, request_json, length(request_json),
                       json_extract(result_json, '$.status'),
                       json_extract(result_json, '$.job_type'),
                       json_extract(result_json, '$.pipeline.label'),
                       json_extract(result_json, '$.pipeline.chart'),
                       json_extract(result_json, '$.pipeline.stats'),
                       json_extract(result_json, '$.research'),
                       json_extract(result_json, '$.best_preset_id'),
                       json_array_length(result_json, '$.comparisons'),
                       length(result_json),
                       created_at, started_at, finished_at, updated_at
                FROM workspace_research_jobs
                WHERE user_id = ? AND workspace_id = ? AND id = ?
                ''',
                (user_id, workspace_id, int(job_id)),
            ).fetchone()

    if not row:
        return None
    if include_payload:
        return _deserialize_workspace_research_job_detail(row)
    return _deserialize_workspace_research_job_summary(row)


def _deserialize_workspace_research_batch_summary(row):
    return {
        'id': int(row[0]),
        'label': str(row[1] or ''),
        'status': str(row[2] or ''),
        'progress': float(row[3] or 0.0),
        'phase': str(row[4] or ''),
        'phase_label': str(row[5] or ''),
        'detail': str(row[6] or ''),
        'error': str(row[7] or ''),
        'total_jobs': int(row[8] or 0),
        'completed_jobs': int(row[9] or 0),
        'failed_jobs': int(row[10] or 0),
        'cancelled_jobs': int(row[11] or 0),
        'current_job_id': int(row[12]) if row[12] is not None else None,
        'cancel_requested': bool(int(row[13] or 0)),
        'request': json.loads(row[14] or '{}'),
        'request_loaded': True,
        'request_size_bytes': int(row[15] or 0),
        'result': {
            'jobs': _load_workspace_json_fragment(row[16], []),
        },
        'result_loaded': False,
        'result_size_bytes': int(row[17] or 0),
        'created_at': float(row[18]) if row[18] is not None else None,
        'started_at': float(row[19]) if row[19] is not None else None,
        'finished_at': float(row[20]) if row[20] is not None else None,
        'updated_at': float(row[21]) if row[21] is not None else None,
    }


def _deserialize_workspace_research_campaign_summary(row):
    return {
        'id': int(row[0]),
        'label': str(row[1] or ''),
        'description': str(row[2] or ''),
        'request': {
            'options': _load_workspace_json_fragment(row[3], {}),
            'shared_features': _load_workspace_json_fragment(row[4], []),
        },
        'request_loaded': False,
        'request_size_bytes': int(row[7] or 0),
        'job_count': int(row[5] or 0),
        'batch_job_count': int(row[6] or 0),
        'created_at': float(row[8]) if row[8] is not None else None,
        'updated_at': float(row[9]) if row[9] is not None else None,
    }


def _deserialize_workspace_research_campaign_detail(row):
    request = json.loads(row[3] or '{}')
    request_jobs = request.get('jobs') if isinstance(request, dict) else []
    batch_jobs = request.get('batch_jobs') if isinstance(request, dict) else []
    return {
        'id': int(row[0]),
        'label': str(row[1] or ''),
        'description': str(row[2] or ''),
        'request': request,
        'request_loaded': True,
        'request_size_bytes': int(row[6] or 0),
        'job_count': len(request_jobs) if isinstance(request_jobs, list) else 0,
        'batch_job_count': len(batch_jobs) if isinstance(batch_jobs, list) else 0,
        'created_at': float(row[4]) if row[4] is not None else None,
        'updated_at': float(row[5]) if row[5] is not None else None,
    }


def _deserialize_workspace_system_log_session(row):
    return {
        'id': int(row[0]),
        'label': str(row[1] or ''),
        'status': str(row[2] or ''),
        'source': str(row[3] or ''),
        'metadata': json.loads(row[4] or '{}'),
        'created_at': float(row[5]) if row[5] is not None else None,
        'closed_at': float(row[6]) if row[6] is not None else None,
        'updated_at': float(row[7]) if row[7] is not None else None,
        'entry_count': int(row[8] or 0) if len(row) > 8 else 0,
        'last_entry_at': float(row[9]) if len(row) > 9 and row[9] is not None else None,
    }


def _deserialize_workspace_system_log_entry(row):
    return {
        'id': int(row[0]),
        'session_id': int(row[1]),
        'client_entry_id': str(row[2] or ''),
        'level': str(row[3] or ''),
        'source': str(row[4] or ''),
        'scope': str(row[5] or ''),
        'category': str(row[6] or ''),
        'message': str(row[7] or ''),
        'context': json.loads(row[8] or '{}'),
        'created_at': float(row[9]) if row[9] is not None else None,
    }


def _deserialize_workspace_research_batch_detail(row):
    return {
        'id': int(row[0]),
        'label': str(row[1] or ''),
        'status': str(row[2] or ''),
        'progress': float(row[3] or 0.0),
        'phase': str(row[4] or ''),
        'phase_label': str(row[5] or ''),
        'detail': str(row[6] or ''),
        'error': str(row[7] or ''),
        'total_jobs': int(row[8] or 0),
        'completed_jobs': int(row[9] or 0),
        'failed_jobs': int(row[10] or 0),
        'cancelled_jobs': int(row[11] or 0),
        'current_job_id': int(row[12]) if row[12] is not None else None,
        'cancel_requested': bool(int(row[13] or 0)),
        'request': json.loads(row[14] or '{}'),
        'result': json.loads(row[15] or '{}'),
        'request_loaded': True,
        'request_size_bytes': int(row[16] or 0),
        'result_loaded': True,
        'result_size_bytes': int(row[17] or 0),
        'created_at': float(row[18]) if row[18] is not None else None,
        'started_at': float(row[19]) if row[19] is not None else None,
        'finished_at': float(row[20]) if row[20] is not None else None,
        'updated_at': float(row[21]) if row[21] is not None else None,
    }


def list_workspace_research_batches(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    include_payload: bool = True,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        if include_payload:
            rows = connection.execute(
                '''
                SELECT id, label, status, progress, phase, phase_label, detail, error, total_jobs, completed_jobs,
                       failed_jobs, cancelled_jobs, current_job_id, cancel_requested, request_json, result_json,
                       length(request_json), length(result_json),
                       created_at, started_at, finished_at, updated_at
                FROM workspace_research_batches
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                ''',
                (user_id, workspace_id, max(1, int(limit))),
            ).fetchall()
        else:
            rows = connection.execute(
                '''
                SELECT batches.id, batches.label, batches.status, batches.progress, batches.phase, batches.phase_label,
                       batches.detail, batches.error, batches.total_jobs, batches.completed_jobs, batches.failed_jobs,
                       batches.cancelled_jobs, batches.current_job_id, batches.cancel_requested, batches.request_json,
                       length(batches.request_json),
                       (
                           SELECT json_group_array(
                               json_object(
                                   'job_id', json_extract(value, '$.job_id'),
                                   'run_label', json_extract(value, '$.run_label'),
                                   'status', json_extract(value, '$.status'),
                                   'run_id', json_extract(value, '$.run_id'),
                                   'benchmark_id', json_extract(value, '$.benchmark_id'),
                                   'benchmark_label', json_extract(value, '$.benchmark_label'),
                                   'detail', json_extract(value, '$.detail'),
                                   'error', json_extract(value, '$.error')
                               )
                           )
                           FROM json_each(batches.result_json, '$.jobs')
                       ),
                       length(batches.result_json),
                       batches.created_at, batches.started_at, batches.finished_at, batches.updated_at
                FROM workspace_research_batches AS batches
                WHERE batches.user_id = ? AND batches.workspace_id = ?
                ORDER BY batches.created_at DESC, batches.id DESC
                LIMIT ?
                ''',
                (user_id, workspace_id, max(1, int(limit))),
            ).fetchall()

    if include_payload:
        return [_deserialize_workspace_research_batch_detail(row) for row in rows]
    return [_deserialize_workspace_research_batch_summary(row) for row in rows]


def get_workspace_research_batch(
    user_id: str,
    workspace_id: str,
    batch_id: int,
    *,
    include_payload: bool = True,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        if include_payload:
            row = connection.execute(
                '''
                SELECT id, label, status, progress, phase, phase_label, detail, error, total_jobs, completed_jobs,
                       failed_jobs, cancelled_jobs, current_job_id, cancel_requested, request_json, result_json,
                       length(request_json), length(result_json),
                       created_at, started_at, finished_at, updated_at
                FROM workspace_research_batches
                WHERE user_id = ? AND workspace_id = ? AND id = ?
                ''',
                (user_id, workspace_id, int(batch_id)),
            ).fetchone()
        else:
            row = connection.execute(
                '''
                SELECT batches.id, batches.label, batches.status, batches.progress, batches.phase, batches.phase_label,
                       batches.detail, batches.error, batches.total_jobs, batches.completed_jobs, batches.failed_jobs,
                       batches.cancelled_jobs, batches.current_job_id, batches.cancel_requested, batches.request_json,
                       length(batches.request_json),
                       (
                           SELECT json_group_array(
                               json_object(
                                   'job_id', json_extract(value, '$.job_id'),
                                   'run_label', json_extract(value, '$.run_label'),
                                   'status', json_extract(value, '$.status'),
                                   'run_id', json_extract(value, '$.run_id'),
                                   'benchmark_id', json_extract(value, '$.benchmark_id'),
                                   'benchmark_label', json_extract(value, '$.benchmark_label'),
                                   'detail', json_extract(value, '$.detail'),
                                   'error', json_extract(value, '$.error')
                               )
                           )
                           FROM json_each(batches.result_json, '$.jobs')
                       ),
                       length(batches.result_json),
                       batches.created_at, batches.started_at, batches.finished_at, batches.updated_at
                FROM workspace_research_batches AS batches
                WHERE batches.user_id = ? AND batches.workspace_id = ? AND batches.id = ?
                ''',
                (user_id, workspace_id, int(batch_id)),
            ).fetchone()

    if not row:
        return None
    if include_payload:
        return _deserialize_workspace_research_batch_detail(row)
    return _deserialize_workspace_research_batch_summary(row)


def list_workspace_research_campaigns(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    include_payload: bool = True,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        if include_payload:
            rows = connection.execute(
                '''
                SELECT id, label, description, request_json, created_at, updated_at, length(request_json)
                FROM workspace_research_campaigns
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                ''',
                (user_id, workspace_id, max(1, int(limit))),
            ).fetchall()
        else:
            rows = connection.execute(
                '''
                SELECT id, label, description,
                       json_extract(request_json, '$.options'),
                       json_extract(request_json, '$.shared_features'),
                       COALESCE(json_array_length(request_json, '$.jobs'), 0),
                       COALESCE(json_array_length(request_json, '$.batch_jobs'), 0),
                       length(request_json),
                       created_at,
                       updated_at
                FROM workspace_research_campaigns
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                ''',
                (user_id, workspace_id, max(1, int(limit))),
            ).fetchall()

    if include_payload:
        return [_deserialize_workspace_research_campaign_detail(row) for row in rows]
    return [_deserialize_workspace_research_campaign_summary(row) for row in rows]


def get_workspace_research_campaign(
    user_id: str,
    workspace_id: str,
    campaign_id: int,
    *,
    include_payload: bool = True,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        if include_payload:
            row = connection.execute(
                '''
                SELECT id, label, description, request_json, created_at, updated_at, length(request_json)
                FROM workspace_research_campaigns
                WHERE user_id = ? AND workspace_id = ? AND id = ?
                ''',
                (user_id, workspace_id, int(campaign_id)),
            ).fetchone()
        else:
            row = connection.execute(
                '''
                SELECT id, label, description,
                       json_extract(request_json, '$.options'),
                       json_extract(request_json, '$.shared_features'),
                       COALESCE(json_array_length(request_json, '$.jobs'), 0),
                       COALESCE(json_array_length(request_json, '$.batch_jobs'), 0),
                       length(request_json),
                       created_at,
                       updated_at
                FROM workspace_research_campaigns
                WHERE user_id = ? AND workspace_id = ? AND id = ?
                ''',
                (user_id, workspace_id, int(campaign_id)),
            ).fetchone()

    if not row:
        return None
    if include_payload:
        return _deserialize_workspace_research_campaign_detail(row)
    return _deserialize_workspace_research_campaign_summary(row)


def list_workspace_system_log_sessions(user_id: str, workspace_id: str, limit: int = 20):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        rows = connection.execute(
            '''
            SELECT sessions.id, sessions.label, sessions.status, sessions.source, sessions.metadata_json,
                   sessions.created_at, sessions.closed_at, sessions.updated_at,
                   COUNT(entries.id) AS entry_count,
                   MAX(entries.created_at) AS last_entry_at
            FROM workspace_system_log_sessions AS sessions
            LEFT JOIN workspace_system_log_entries AS entries
              ON entries.session_id = sessions.id
            WHERE sessions.user_id = ? AND sessions.workspace_id = ?
            GROUP BY sessions.id, sessions.label, sessions.status, sessions.source, sessions.metadata_json,
                     sessions.created_at, sessions.closed_at, sessions.updated_at
            ORDER BY sessions.updated_at DESC, sessions.id DESC
            LIMIT ?
            ''',
            (user_id, workspace_id, max(1, int(limit))),
        ).fetchall()

    return [_deserialize_workspace_system_log_session(row) for row in rows]


def _get_workspace_system_log_session_row(
    connection,
    *,
    user_id: str,
    workspace_id: str,
    session_id: int,
):
    return connection.execute(
        '''
        SELECT sessions.id, sessions.label, sessions.status, sessions.source, sessions.metadata_json,
               sessions.created_at, sessions.closed_at, sessions.updated_at,
               COUNT(entries.id) AS entry_count,
               MAX(entries.created_at) AS last_entry_at
        FROM workspace_system_log_sessions AS sessions
        LEFT JOIN workspace_system_log_entries AS entries
          ON entries.session_id = sessions.id
        WHERE sessions.user_id = ? AND sessions.workspace_id = ? AND sessions.id = ?
        GROUP BY sessions.id, sessions.label, sessions.status, sessions.source, sessions.metadata_json,
                 sessions.created_at, sessions.closed_at, sessions.updated_at
        ''',
        (user_id, workspace_id, int(session_id)),
    ).fetchone()


def get_workspace_system_log_session(user_id: str, workspace_id: str, session_id: int):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        row = _get_workspace_system_log_session_row(
            connection,
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session_id,
        )

    return _deserialize_workspace_system_log_session(row) if row else None


def get_active_workspace_system_log_session(user_id: str, workspace_id: str):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        row = connection.execute(
            '''
            SELECT sessions.id, sessions.label, sessions.status, sessions.source, sessions.metadata_json,
                   sessions.created_at, sessions.closed_at, sessions.updated_at,
                   COUNT(entries.id) AS entry_count,
                   MAX(entries.created_at) AS last_entry_at
            FROM workspace_system_log_sessions AS sessions
            LEFT JOIN workspace_system_log_entries AS entries
              ON entries.session_id = sessions.id
            WHERE sessions.user_id = ? AND sessions.workspace_id = ? AND sessions.status = 'active'
            GROUP BY sessions.id, sessions.label, sessions.status, sessions.source, sessions.metadata_json,
                     sessions.created_at, sessions.closed_at, sessions.updated_at
            ORDER BY sessions.updated_at DESC, sessions.id DESC
            LIMIT 1
            ''',
            (user_id, workspace_id),
        ).fetchone()

    return _deserialize_workspace_system_log_session(row) if row else None


def list_workspace_system_log_entries(user_id: str, workspace_id: str, session_id: int, limit: int = 500):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        rows = connection.execute(
            '''
            SELECT id, session_id, client_entry_id, level, source, scope, category, message, context_json, created_at
            FROM workspace_system_log_entries
            WHERE user_id = ? AND workspace_id = ? AND session_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            ''',
            (user_id, workspace_id, int(session_id), max(1, int(limit))),
        ).fetchall()

    return list(reversed([_deserialize_workspace_system_log_entry(row) for row in rows]))


def purge_workspace_system_log(user_id: str, workspace_id: str | None = None):
    ensure_workspace_store()

    safe_user_id = str(user_id or '').strip()
    safe_workspace_id = str(workspace_id or '').strip()
    if not safe_user_id:
        return {
            'entries_deleted': 0,
            'sessions_deleted': 0,
        }

    with _connect_workspace_db() as connection:
        if safe_workspace_id:
            entries_cursor = connection.execute(
                '''
                DELETE FROM workspace_system_log_entries
                WHERE user_id = ? AND workspace_id = ?
                ''',
                (safe_user_id, safe_workspace_id),
            )
            sessions_cursor = connection.execute(
                '''
                DELETE FROM workspace_system_log_sessions
                WHERE user_id = ? AND workspace_id = ?
                ''',
                (safe_user_id, safe_workspace_id),
            )
        else:
            entries_cursor = connection.execute(
                '''
                DELETE FROM workspace_system_log_entries
                WHERE user_id = ?
                ''',
                (safe_user_id,),
            )
            sessions_cursor = connection.execute(
                '''
                DELETE FROM workspace_system_log_sessions
                WHERE user_id = ?
                ''',
                (safe_user_id,),
            )
        connection.commit()

    return {
        'entries_deleted': max(0, int(entries_cursor.rowcount or 0)),
        'sessions_deleted': max(0, int(sessions_cursor.rowcount or 0)),
    }


def create_workspace_system_log_session(
    user_id: str,
    workspace_id: str,
    *,
    label: str | None = None,
    source: str | None = None,
    metadata: dict | None = None,
    status: str = 'active',
):
    ensure_workspace_store()
    now = time.time()
    safe_status = str(status or '').strip().lower() or 'active'

    with _connect_workspace_db() as connection:
        cursor = connection.execute(
            '''
            INSERT INTO workspace_system_log_sessions (
                user_id,
                workspace_id,
                label,
                status,
                source,
                metadata_json,
                created_at,
                closed_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(label or '').strip() or 'System log',
                safe_status,
                str(source or '').strip() or '',
                json.dumps(metadata or {}, ensure_ascii=True),
                now,
                now if safe_status != 'active' else None,
                now,
            ),
        )
        connection.commit()
        session_id = int(cursor.lastrowid)

    return get_workspace_system_log_session(user_id, workspace_id, session_id)


def ensure_active_workspace_system_log_session(
    user_id: str,
    workspace_id: str,
    *,
    label: str | None = None,
    source: str | None = None,
    metadata: dict | None = None,
):
    active = get_active_workspace_system_log_session(user_id, workspace_id)
    if active:
        return active

    return create_workspace_system_log_session(
        user_id,
        workspace_id,
        label=label,
        source=source,
        metadata=metadata,
        status='active',
    )


def start_workspace_system_log_session(
    user_id: str,
    workspace_id: str,
    *,
    label: str | None = None,
    source: str | None = None,
    metadata: dict | None = None,
):
    ensure_workspace_store()
    now = time.time()

    with _connect_workspace_db() as connection:
        archived_rows = connection.execute(
            '''
            SELECT id
            FROM workspace_system_log_sessions
            WHERE user_id = ? AND workspace_id = ? AND status = 'active'
            ORDER BY updated_at DESC, id DESC
            ''',
            (user_id, workspace_id),
        ).fetchall()
        archived_session_ids = [int(row[0]) for row in archived_rows]

        if archived_session_ids:
            connection.execute(
                '''
                UPDATE workspace_system_log_sessions
                SET status = 'archived', closed_at = ?, updated_at = ?
                WHERE user_id = ? AND workspace_id = ? AND status = 'active'
                ''',
                (now, now, user_id, workspace_id),
            )

        cursor = connection.execute(
            '''
            INSERT INTO workspace_system_log_sessions (
                user_id,
                workspace_id,
                label,
                status,
                source,
                metadata_json,
                created_at,
                closed_at,
                updated_at
            )
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(label or '').strip() or 'System log',
                str(source or '').strip() or '',
                json.dumps(metadata or {}, ensure_ascii=True),
                now,
                None,
                now,
            ),
        )
        connection.commit()
        session_id = int(cursor.lastrowid)

    return {
        'session': get_workspace_system_log_session(user_id, workspace_id, session_id),
        'archived_session_ids': archived_session_ids,
    }


def append_workspace_system_log_entries(
    user_id: str,
    workspace_id: str,
    *,
    entries: list[dict] | None,
    session_id: int | None = None,
    label: str | None = None,
    source: str | None = None,
    metadata: dict | None = None,
):
    ensure_workspace_store()
    safe_entries = [dict(entry or {}) for entry in list(entries or []) if isinstance(entry, dict)]
    if not safe_entries:
        target_session = (
            get_workspace_system_log_session(user_id, workspace_id, int(session_id))
            if session_id is not None
            else ensure_active_workspace_system_log_session(
                user_id,
                workspace_id,
                label=label,
                source=source,
                metadata=metadata,
            )
        )
        return {
            'session': target_session,
            'entries': [],
        }

    with _connect_workspace_db() as connection:
        if session_id is not None:
            session_row = _get_workspace_system_log_session_row(
                connection,
                user_id=user_id,
                workspace_id=workspace_id,
                session_id=int(session_id),
            )
            if not session_row:
                raise ValueError(f'System log session {session_id} was not found')
            target_session_id = int(session_row[0])
        else:
            existing_active = connection.execute(
                '''
                SELECT id
                FROM workspace_system_log_sessions
                WHERE user_id = ? AND workspace_id = ? AND status = 'active'
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                ''',
                (user_id, workspace_id),
            ).fetchone()
            if existing_active:
                target_session_id = int(existing_active[0])
            else:
                cursor = connection.execute(
                    '''
                    INSERT INTO workspace_system_log_sessions (
                        user_id,
                        workspace_id,
                        label,
                        status,
                        source,
                        metadata_json,
                        created_at,
                        closed_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
                    ''',
                    (
                        user_id,
                        workspace_id,
                        str(label or '').strip() or 'System log',
                        str(source or '').strip() or '',
                        json.dumps(metadata or {}, ensure_ascii=True),
                        time.time(),
                        None,
                        time.time(),
                    ),
                )
                target_session_id = int(cursor.lastrowid)

        persisted_rows = []
        latest_entry_at = None
        for raw_entry in safe_entries:
            message = str(raw_entry.get('message') or '').strip()
            if not message:
                continue

            created_at = _normalize_timestamp_seconds(raw_entry.get('created_at'))
            latest_entry_at = created_at if latest_entry_at is None else max(latest_entry_at, created_at)
            client_entry_id = str(raw_entry.get('client_entry_id') or '').strip() or None

            connection.execute(
                '''
                INSERT OR IGNORE INTO workspace_system_log_entries (
                    user_id,
                    workspace_id,
                    session_id,
                    client_entry_id,
                    level,
                    source,
                    scope,
                    category,
                    message,
                    context_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    user_id,
                    workspace_id,
                    target_session_id,
                    client_entry_id,
                    str(raw_entry.get('level') or '').strip().lower() or 'info',
                    str(raw_entry.get('source') or source or '').strip() or '',
                    str(raw_entry.get('scope') or '').strip() or '',
                    str(raw_entry.get('category') or '').strip() or '',
                    message,
                    json.dumps(raw_entry.get('context') or {}, ensure_ascii=True),
                    created_at,
                ),
            )

            if client_entry_id:
                row = connection.execute(
                    '''
                    SELECT id, session_id, client_entry_id, level, source, scope, category, message, context_json, created_at
                    FROM workspace_system_log_entries
                    WHERE user_id = ? AND workspace_id = ? AND client_entry_id = ?
                    ''',
                    (user_id, workspace_id, client_entry_id),
                ).fetchone()
            else:
                row = connection.execute(
                    '''
                    SELECT id, session_id, client_entry_id, level, source, scope, category, message, context_json, created_at
                    FROM workspace_system_log_entries
                    WHERE user_id = ? AND workspace_id = ? AND session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    ''',
                    (user_id, workspace_id, target_session_id),
                ).fetchone()

            if row:
                persisted_rows.append(row)

        connection.execute(
            '''
            UPDATE workspace_system_log_sessions
            SET updated_at = ?
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (
                latest_entry_at if latest_entry_at is not None else time.time(),
                user_id,
                workspace_id,
                target_session_id,
            ),
        )
        connection.commit()
        session_row = _get_workspace_system_log_session_row(
            connection,
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=target_session_id,
        )

    return {
        'session': _deserialize_workspace_system_log_session(session_row) if session_row else None,
        'entries': [_deserialize_workspace_system_log_entry(row) for row in persisted_rows],
    }


def list_workspace_broker_profiles(user_id: str, workspace_id: str, limit: int = 100):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        rows = connection.execute(
            '''
            SELECT id, label, broker_code, connector_kind, server_name, market_domain, base_currency, notes,
                   is_default, is_favorite, profile_json, created_at, updated_at
            FROM workspace_broker_profiles
            WHERE user_id = ? AND workspace_id = ?
            ORDER BY is_default DESC, is_favorite DESC, updated_at DESC, id DESC
            LIMIT ?
            ''',
            (user_id, workspace_id, max(1, int(limit))),
        ).fetchall()

    return [_deserialize_workspace_broker_profile(row) for row in rows]


def get_workspace_broker_profile_by_id(broker_profile_id: str | int | None):
    ensure_workspace_store()
    safe_profile_id = _normalize_broker_profile_id(broker_profile_id)
    if not safe_profile_id:
        return None
    try:
        numeric_profile_id = int(safe_profile_id)
    except (TypeError, ValueError):
        return None

    with _connect_workspace_db() as connection:
        row = connection.execute(
            '''
            SELECT id, label, broker_code, connector_kind, server_name, market_domain, base_currency, notes,
                   is_default, is_favorite, profile_json, created_at, updated_at
            FROM workspace_broker_profiles
            WHERE id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            ''',
            (numeric_profile_id,),
        ).fetchone()

    return _deserialize_workspace_broker_profile(row)


def create_workspace_broker_profile(
    user_id: str,
    workspace_id: str,
    *,
    label: str,
    broker_code: str | None = None,
    connector_kind: str | None = None,
    server_name: str | None = None,
    market_domain: str | None = None,
    base_currency: str | None = None,
    notes: str | None = None,
    is_default: bool | None = None,
    is_favorite: bool | None = None,
    profile: dict | None = None,
):
    ensure_workspace_store()
    now = time.time()

    with _connect_workspace_db() as connection:
        existing_count = int(connection.execute(
            '''
            SELECT COUNT(*)
            FROM workspace_broker_profiles
            WHERE user_id = ? AND workspace_id = ?
            ''',
            (user_id, workspace_id),
        ).fetchone()[0] or 0)
        should_default = bool(is_default) or existing_count <= 0
        if should_default:
            connection.execute(
                '''
                UPDATE workspace_broker_profiles
                SET is_default = 0
                WHERE user_id = ? AND workspace_id = ?
                ''',
                (user_id, workspace_id),
            )
        cursor = connection.execute(
            '''
            INSERT INTO workspace_broker_profiles (
                user_id, workspace_id, label, broker_code, connector_kind, server_name, market_domain, base_currency,
                notes, is_default, is_favorite, profile_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(label or '').strip() or 'Broker profile',
                str(broker_code or '').strip() or 'manual',
                str(connector_kind or '').strip() or 'mt5',
                str(server_name or '').strip(),
                str(market_domain or '').strip(),
                str(base_currency or '').strip().upper(),
                str(notes or '').strip(),
                1 if should_default else 0,
                1 if is_favorite else 0,
                json.dumps(profile or {}, ensure_ascii=True),
                now,
                now,
            ),
        )
        broker_profile_id = str(int(cursor.lastrowid))
        if existing_count <= 0:
            _adopt_legacy_workspace_broker_profile_scope(
                connection,
                user_id,
                workspace_id,
                broker_profile_id,
                str(label or '').strip() or 'Broker profile',
            )
        connection.commit()
        row = _get_workspace_broker_profile_row(connection, user_id, workspace_id, broker_profile_id)

    return _deserialize_workspace_broker_profile(row)


def update_workspace_broker_profile(
    user_id: str,
    workspace_id: str,
    broker_profile_id: str | int,
    *,
    label: str | None = None,
    broker_code: str | None = None,
    connector_kind: str | None = None,
    server_name: str | None = None,
    market_domain: str | None = None,
    base_currency: str | None = None,
    notes: str | None = None,
    is_default: bool | None = None,
    is_favorite: bool | None = None,
    profile: dict | None = None,
):
    ensure_workspace_store()
    safe_profile_id = _normalize_broker_profile_id(broker_profile_id)
    if not safe_profile_id:
        return None

    updates = []
    params: list[object] = []
    if label is not None:
        updates.append('label = ?')
        params.append(str(label or '').strip() or 'Broker profile')
    if broker_code is not None:
        updates.append('broker_code = ?')
        params.append(str(broker_code or '').strip() or 'manual')
    if connector_kind is not None:
        updates.append('connector_kind = ?')
        params.append(str(connector_kind or '').strip() or 'mt5')
    if server_name is not None:
        updates.append('server_name = ?')
        params.append(str(server_name or '').strip())
    if market_domain is not None:
        updates.append('market_domain = ?')
        params.append(str(market_domain or '').strip())
    if base_currency is not None:
        updates.append('base_currency = ?')
        params.append(str(base_currency or '').strip().upper())
    if notes is not None:
        updates.append('notes = ?')
        params.append(str(notes or '').strip())
    if is_default is not None:
        updates.append('is_default = ?')
        params.append(1 if is_default else 0)
    if is_favorite is not None:
        updates.append('is_favorite = ?')
        params.append(1 if is_favorite else 0)
    if profile is not None:
        updates.append('profile_json = ?')
        params.append(json.dumps(profile or {}, ensure_ascii=True))

    if not updates:
        for item in list_workspace_broker_profiles(user_id, workspace_id, limit=500):
            if str(item.get('id') or '') == safe_profile_id:
                return item
        return None

    updates.append('updated_at = ?')
    params.append(time.time())
    params.extend([user_id, workspace_id, int(safe_profile_id)])

    with _connect_workspace_db() as connection:
        if is_default:
            connection.execute(
                '''
                UPDATE workspace_broker_profiles
                SET is_default = 0
                WHERE user_id = ? AND workspace_id = ? AND id != ?
                ''',
                (user_id, workspace_id, int(safe_profile_id)),
            )
        connection.execute(
            f'''
            UPDATE workspace_broker_profiles
            SET {", ".join(updates)}
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            params,
        )
        row = _get_workspace_broker_profile_row(connection, user_id, workspace_id, safe_profile_id)
        resolved = _deserialize_workspace_broker_profile(row)
        if resolved:
            _cascade_workspace_broker_profile_label(
                connection,
                user_id,
                workspace_id,
                str(resolved.get('id') or ''),
                str(resolved.get('label') or ''),
            )
        connection.commit()

    return resolved


def delete_workspace_broker_profile(user_id: str, workspace_id: str, broker_profile_id: str | int):
    ensure_workspace_store()
    safe_profile_id = _normalize_broker_profile_id(broker_profile_id)
    if not safe_profile_id:
        return None

    with _connect_workspace_db() as connection:
        row = _get_workspace_broker_profile_row(connection, user_id, workspace_id, safe_profile_id)
        resolved = _deserialize_workspace_broker_profile(row)
        if not resolved:
            return None

        reference_counts = {}
        for table_name, label in (
            ('workspace_strategy_benchmarks', 'saved strategies'),
            ('workspace_saved_portfolios', 'saved portfolios'),
            ('workspace_live_trades', 'live-trade records'),
            ('workspace_trade_reconciliations', 'trade comparisons'),
        ):
            count = int(connection.execute(
                f'''
                SELECT COUNT(*)
                FROM {table_name}
                WHERE user_id = ? AND workspace_id = ? AND broker_profile_id = ?
                ''',
                (user_id, workspace_id, safe_profile_id),
            ).fetchone()[0] or 0)
            if count > 0:
                reference_counts[label] = count
        if reference_counts:
            fragments = [f'{count} {label}' for label, count in reference_counts.items()]
            raise ValueError(
                f'Broker profile "{resolved.get("label") or safe_profile_id}" is still referenced by '
                + ', '.join(fragments)
                + '.'
            )

        connection.execute(
            '''
            DELETE FROM workspace_broker_profiles
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(safe_profile_id)),
        )
        if resolved.get('is_default'):
            replacement = connection.execute(
                '''
                SELECT id
                FROM workspace_broker_profiles
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                ''',
                (user_id, workspace_id),
            ).fetchone()
            if replacement:
                connection.execute(
                    '''
                    UPDATE workspace_broker_profiles
                    SET is_default = 1, updated_at = ?
                    WHERE user_id = ? AND workspace_id = ? AND id = ?
                    ''',
                    (time.time(), user_id, workspace_id, int(replacement[0])),
                )
        connection.commit()

    return {
        'id': safe_profile_id,
        'label': str(resolved.get('label') or ''),
        'is_default': bool(resolved.get('is_default')),
    }


def list_workspace_strategy_benchmarks(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    broker_profile_id: str | None = None,
):
    ensure_workspace_store()

    safe_broker_profile_id = _normalize_broker_profile_id(broker_profile_id)
    with _connect_workspace_db() as connection:
        query = '''
            SELECT id, label, side, source, notes, is_favorite, symbol, timeframe, strategy_json, strategies_json,
                   created_at, broker_profile_id, broker_profile_label
            FROM workspace_strategy_benchmarks
            WHERE user_id = ? AND workspace_id = ?
        '''
        params: list[object] = [user_id, workspace_id]
        if safe_broker_profile_id:
            query += ' AND broker_profile_id = ?'
            params.append(safe_broker_profile_id)
        query += ' ORDER BY is_favorite DESC, created_at DESC, id DESC LIMIT ?'
        params.append(max(1, int(limit)))
        rows = connection.execute(query, params).fetchall()

    return [
        {
            'id': int(row[0]),
            'label': str(row[1] or ''),
            'side': str(row[2] or ''),
            'source': str(row[3] or ''),
            'notes': str(row[4] or ''),
            'is_favorite': bool(row[5]),
            'symbol': str(row[6] or '').strip().upper(),
            'timeframe': str(row[7] or '').strip().upper(),
            'strategy': json.loads(row[8] or '{}'),
            'strategies': json.loads(row[9] or '[]'),
            'created_at': float(row[10]) if row[10] is not None else None,
            'broker_profile_id': str(row[11] or '').strip(),
            'broker_profile_label': str(row[12] or '').strip(),
        }
        for row in rows
    ]


def list_workspace_saved_portfolios(
    user_id: str,
    workspace_id: str,
    limit: int = 100,
    *,
    broker_profile_id: str | None = None,
):
    ensure_workspace_store()

    safe_broker_profile_id = _normalize_broker_profile_id(broker_profile_id)
    with _connect_workspace_db() as connection:
        query = '''
            SELECT id, label, source, notes, is_favorite, portfolio_json, capital_model_json, created_at, updated_at,
                   broker_profile_id, broker_profile_label
            FROM workspace_saved_portfolios
            WHERE user_id = ? AND workspace_id = ?
        '''
        params: list[object] = [user_id, workspace_id]
        if safe_broker_profile_id:
            query += ' AND broker_profile_id = ?'
            params.append(safe_broker_profile_id)
        query += ' ORDER BY is_favorite DESC, updated_at DESC, id DESC LIMIT ?'
        params.append(max(1, int(limit)))
        rows = connection.execute(query, params).fetchall()

    return [
        {
            'id': int(row[0]),
            'label': str(row[1] or ''),
            'source': str(row[2] or ''),
            'notes': str(row[3] or ''),
            'is_favorite': bool(row[4]),
            'portfolioStructureVersion': 2,
            'portfolio': json.loads(row[5] or '{}'),
            'capitalModel': json.loads(row[6] or '{}'),
            'created_at': float(row[7]) if row[7] is not None else None,
            'updated_at': float(row[8]) if row[8] is not None else None,
            'broker_profile_id': str(row[9] or '').strip(),
            'broker_profile_label': str(row[10] or '').strip(),
        }
        for row in rows
    ]


def create_workspace_saved_portfolio(
    user_id: str,
    workspace_id: str,
    *,
    label: str,
    source: str | None,
    notes: str | None,
    is_favorite: bool | None,
    portfolio: dict | None,
    capital_model: dict | None = None,
    broker_profile_id: str | None = None,
    broker_profile_label: str | None = None,
):
    ensure_workspace_store()
    now = time.time()

    with _connect_workspace_db() as connection:
        resolved_scope = _resolve_workspace_broker_profile_scope(
            connection,
            user_id,
            workspace_id,
            broker_profile_id=broker_profile_id,
            broker_profile_label=broker_profile_label,
        )
        cursor = connection.execute(
            '''
            INSERT INTO workspace_saved_portfolios (
                user_id,
                workspace_id,
                label,
                source,
                notes,
                is_favorite,
                portfolio_json,
                capital_model_json,
                broker_profile_id,
                broker_profile_label,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(label or '').strip() or 'Portfolio',
                str(source or '').strip() or '',
                str(notes or '').strip() or '',
                1 if is_favorite else 0,
                json.dumps(portfolio or {}, ensure_ascii=True),
                json.dumps(capital_model or {}, ensure_ascii=True),
                str(resolved_scope.get('broker_profile_id') or '').strip(),
                str(resolved_scope.get('broker_profile_label') or '').strip(),
                now,
                now,
            ),
        )
        connection.commit()
        portfolio_id = int(cursor.lastrowid)

    return {
        'id': portfolio_id,
        'label': str(label or '').strip() or 'Portfolio',
        'source': str(source or '').strip() or '',
        'notes': str(notes or '').strip() or '',
        'is_favorite': bool(is_favorite),
        'portfolioStructureVersion': 2,
        'portfolio': portfolio or {},
        'capitalModel': capital_model or {},
        'broker_profile_id': str(resolved_scope.get('broker_profile_id') or '').strip(),
        'broker_profile_label': str(resolved_scope.get('broker_profile_label') or '').strip(),
        'created_at': now,
        'updated_at': now,
    }


def delete_workspace_saved_portfolio(user_id: str, workspace_id: str, portfolio_id: int):
    ensure_workspace_store()

    existing = None
    for item in list_workspace_saved_portfolios(user_id, workspace_id, limit=500):
        if int(item['id']) == int(portfolio_id):
            existing = item
            break

    if not existing:
        return None

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            DELETE FROM workspace_saved_portfolios
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(portfolio_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'label': existing['label'],
        'created_at': existing['created_at'],
        'updated_at': existing['updated_at'],
    }


def update_workspace_saved_portfolio(
    user_id: str,
    workspace_id: str,
    portfolio_id: int,
    *,
    label: str | None = None,
    source: str | None = None,
    notes: str | None = None,
    is_favorite: bool | None = None,
    portfolio: dict | None = None,
    capital_model: dict | None = None,
    broker_profile_id: str | None = None,
):
    ensure_workspace_store()

    updates = []
    params: list[object] = []

    if label is not None:
        updates.append('label = ?')
        params.append(str(label).strip() or 'Portfolio')
    if source is not None:
        updates.append('source = ?')
        params.append(str(source).strip())
    if notes is not None:
        updates.append('notes = ?')
        params.append(str(notes).strip())
    if is_favorite is not None:
        updates.append('is_favorite = ?')
        params.append(1 if is_favorite else 0)
    if portfolio is not None:
        updates.append('portfolio_json = ?')
        params.append(json.dumps(portfolio or {}, ensure_ascii=True))
    if capital_model is not None:
        updates.append('capital_model_json = ?')
        params.append(json.dumps(capital_model or {}, ensure_ascii=True))
    if broker_profile_id is not None:
        with _connect_workspace_db() as scope_connection:
            resolved_scope = _resolve_workspace_broker_profile_scope(
                scope_connection,
                user_id,
                workspace_id,
                broker_profile_id=broker_profile_id,
            )
        updates.append('broker_profile_id = ?')
        params.append(str(resolved_scope.get('broker_profile_id') or '').strip())
        updates.append('broker_profile_label = ?')
        params.append(str(resolved_scope.get('broker_profile_label') or '').strip())

    if not updates:
        for item in list_workspace_saved_portfolios(user_id, workspace_id, limit=500):
            if int(item['id']) == int(portfolio_id):
                return item
        return None

    updates.append('updated_at = ?')
    params.append(time.time())
    params.extend([user_id, workspace_id, int(portfolio_id)])

    with _connect_workspace_db() as connection:
        connection.execute(
            f'''
            UPDATE workspace_saved_portfolios
            SET {", ".join(updates)}
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            params,
        )
        connection.commit()

    for item in list_workspace_saved_portfolios(user_id, workspace_id, limit=500):
        if int(item['id']) == int(portfolio_id):
            return item
    return None


def create_workspace_strategy_benchmark(
    user_id: str,
    workspace_id: str,
    *,
    label: str,
    side: str | None,
    source: str | None,
    notes: str | None,
    is_favorite: bool | None,
    symbol: str | None,
    timeframe: str | None,
    strategy: dict | None,
    strategies: list | None = None,
    broker_profile_id: str | None = None,
    broker_profile_label: str | None = None,
):
    ensure_workspace_store()
    now = time.time()

    with _connect_workspace_db() as connection:
        resolved_scope = _resolve_workspace_broker_profile_scope(
            connection,
            user_id,
            workspace_id,
            broker_profile_id=broker_profile_id,
            broker_profile_label=broker_profile_label,
        )
        cursor = connection.execute(
            '''
            INSERT INTO workspace_strategy_benchmarks (
                user_id,
                workspace_id,
                label,
                side,
                source,
                notes,
                is_favorite,
                symbol,
                timeframe,
                strategy_json,
                strategies_json,
                broker_profile_id,
                broker_profile_label,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(label or '').strip() or 'Benchmark',
                str(side or '').strip() or '',
                str(source or '').strip() or '',
                str(notes or '').strip() or '',
                1 if is_favorite else 0,
                str(symbol or '').strip().upper() or '',
                str(timeframe or '').strip().upper() or '',
                json.dumps(strategy or {}, ensure_ascii=True),
                json.dumps(list(strategies or []), ensure_ascii=True),
                str(resolved_scope.get('broker_profile_id') or '').strip(),
                str(resolved_scope.get('broker_profile_label') or '').strip(),
                now,
            ),
        )
        connection.commit()
        benchmark_id = int(cursor.lastrowid)

    return {
        'id': benchmark_id,
        'label': str(label or '').strip() or 'Benchmark',
        'side': str(side or '').strip() or '',
        'source': str(source or '').strip() or '',
        'notes': str(notes or '').strip() or '',
        'is_favorite': bool(is_favorite),
        'symbol': str(symbol or '').strip().upper() or '',
        'timeframe': str(timeframe or '').strip().upper() or '',
        'strategy': strategy or {},
        'strategies': list(strategies or []),
        'broker_profile_id': str(resolved_scope.get('broker_profile_id') or '').strip(),
        'broker_profile_label': str(resolved_scope.get('broker_profile_label') or '').strip(),
        'created_at': now,
    }


def delete_workspace_strategy_benchmark(user_id: str, workspace_id: str, benchmark_id: int):
    ensure_workspace_store()

    existing = None
    for item in list_workspace_strategy_benchmarks(user_id, workspace_id, limit=500):
        if int(item['id']) == int(benchmark_id):
            existing = item
            break

    if not existing:
        return None

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            DELETE FROM workspace_strategy_benchmarks
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(benchmark_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'label': existing['label'],
        'created_at': existing['created_at'],
    }


def update_workspace_strategy_benchmark(
    user_id: str,
    workspace_id: str,
    benchmark_id: int,
    *,
    label: str | None = None,
    side: str | None = None,
    source: str | None = None,
    notes: str | None = None,
    is_favorite: bool | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    broker_profile_id: str | None = None,
):
    ensure_workspace_store()

    updates = []
    params: list[object] = []

    if label is not None:
        updates.append('label = ?')
        params.append(str(label).strip() or 'Benchmark')
    if side is not None:
        updates.append('side = ?')
        params.append(str(side).strip())
    if source is not None:
        updates.append('source = ?')
        params.append(str(source).strip())
    if notes is not None:
        updates.append('notes = ?')
        params.append(str(notes).strip())
    if is_favorite is not None:
        updates.append('is_favorite = ?')
        params.append(1 if is_favorite else 0)
    if symbol is not None:
        updates.append('symbol = ?')
        params.append(str(symbol or '').strip().upper() or '')
    if timeframe is not None:
        updates.append('timeframe = ?')
        params.append(str(timeframe or '').strip().upper() or '')
    if broker_profile_id is not None:
        with _connect_workspace_db() as scope_connection:
            resolved_scope = _resolve_workspace_broker_profile_scope(
                scope_connection,
                user_id,
                workspace_id,
                broker_profile_id=broker_profile_id,
            )
        updates.append('broker_profile_id = ?')
        params.append(str(resolved_scope.get('broker_profile_id') or '').strip())
        updates.append('broker_profile_label = ?')
        params.append(str(resolved_scope.get('broker_profile_label') or '').strip())

    if not updates:
        for item in list_workspace_strategy_benchmarks(user_id, workspace_id, limit=500):
            if int(item['id']) == int(benchmark_id):
                return item
        return None

    params.extend([user_id, workspace_id, int(benchmark_id)])

    with _connect_workspace_db() as connection:
        connection.execute(
            f'''
            UPDATE workspace_strategy_benchmarks
            SET {", ".join(updates)}
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            params,
        )
        connection.commit()

    for item in list_workspace_strategy_benchmarks(user_id, workspace_id, limit=500):
        if int(item['id']) == int(benchmark_id):
            return item
    return None


def _slugify_workspace_research_project_key(value: str | None):
    source = str(value or '').strip().lower()
    if not source:
        return ''

    parts: list[str] = []
    last_was_dash = False
    for character in source:
        if character.isalnum():
            parts.append(character)
            last_was_dash = False
            continue
        if not last_was_dash:
            parts.append('-')
            last_was_dash = True

    return ''.join(parts).strip('-')


def _normalize_workspace_research_article(article: dict | None):
    safe_article = article if isinstance(article, dict) else {}
    keywords = safe_article.get('keywords')
    safe_keywords: list[str] = []
    seen_keywords: set[str] = set()
    if isinstance(keywords, list):
        for raw_keyword in keywords:
            keyword = str(raw_keyword or '').strip()
            if not keyword:
                continue
            normalized_keyword = keyword.lower()
            if normalized_keyword in seen_keywords:
                continue
            seen_keywords.add(normalized_keyword)
            safe_keywords.append(keyword)

    sections = safe_article.get('sections')
    safe_sections: list[dict] = []
    if isinstance(sections, list):
        for index, raw_section in enumerate(sections, start=1):
            if not isinstance(raw_section, dict):
                continue
            title = str(raw_section.get('title') or '').strip() or f'Section {index}'
            content = str(raw_section.get('content') or '').strip()
            if not title and not content:
                continue
            section_id = _slugify_workspace_research_project_key(raw_section.get('id') or title) or f'section-{index}'
            safe_sections.append({
                'id': section_id,
                'title': title,
                'content': content,
            })

    feature_analysis = safe_article.get('feature_analysis')
    safe_feature_analysis: list[dict] = []
    if isinstance(feature_analysis, list):
        for index, raw_feature in enumerate(feature_analysis, start=1):
            if not isinstance(raw_feature, dict):
                continue
            name = str(raw_feature.get('name') or '').strip() or f'Feature group {index}'
            rationale = str(raw_feature.get('rationale') or '').strip()
            expectation = str(raw_feature.get('expectation') or '').strip()
            observed = str(raw_feature.get('observed') or '').strip()
            verdict = str(raw_feature.get('verdict') or '').strip().lower() or 'inconclusive'
            safe_feature_analysis.append({
                'id': _slugify_workspace_research_project_key(raw_feature.get('id') or name) or f'feature-{index}',
                'name': name,
                'rationale': rationale,
                'expectation': expectation,
                'observed': observed,
                'verdict': verdict,
            })

    raw_mandate = safe_article.get('mandate')
    safe_mandate = raw_mandate if isinstance(raw_mandate, dict) else {}

    experimental_log = safe_article.get('experimental_log')
    safe_experimental_log: list[dict] = []
    if isinstance(experimental_log, list):
        for index, raw_entry in enumerate(experimental_log, start=1):
            if not isinstance(raw_entry, dict):
                continue
            title = str(raw_entry.get('title') or '').strip() or f'Experiment step {index}'
            performed = str(raw_entry.get('performed') or '').strip()
            why = str(raw_entry.get('why') or '').strip()
            results = str(raw_entry.get('results') or '').strip()
            provisions = str(raw_entry.get('provisions') or '').strip()
            if not title and not performed and not why and not results and not provisions:
                continue
            safe_experimental_log.append({
                'id': _slugify_workspace_research_project_key(raw_entry.get('id') or title) or f'experiment-{index}',
                'title': title,
                'performed': performed,
                'why': why,
                'results': results,
                'provisions': provisions,
            })

    return {
        'abstract': str(safe_article.get('abstract') or '').strip(),
        'keywords': safe_keywords,
        'mandate': {
            'objective': str(safe_mandate.get('objective') or '').strip(),
            'strategy_specification': str(safe_mandate.get('strategy_specification') or '').strip(),
            'target_parameters': str(safe_mandate.get('target_parameters') or '').strip(),
            'acceptance_criteria': str(safe_mandate.get('acceptance_criteria') or '').strip(),
        },
        'sections': safe_sections,
        'feature_analysis': safe_feature_analysis,
        'experimental_log': safe_experimental_log,
    }


def _deserialize_workspace_research_paper_row(row):
    if not row:
        return None

    article = _normalize_workspace_research_article(json.loads(row[10] or '{}'))
    return {
        'id': int(row[0]),
        'project_key': str(row[1] or ''),
        'title': str(row[2] or ''),
        'status': str(row[3] or 'draft'),
        'discipline': str(row[4] or ''),
        'symbol': str(row[5] or ''),
        'timeframe': str(row[6] or ''),
        'summary': str(row[7] or ''),
        'article': article,
        'created_at': _deserialize_timestamp_value(row[11]),
        'updated_at': _deserialize_timestamp_value(row[12]),
    }


def _deserialize_workspace_research_paper_version_row(row):
    if not row:
        return None

    article = _normalize_workspace_research_article(json.loads(row[9] or '{}'))
    return {
        'id': int(row[0]),
        'paper_id': int(row[1]),
        'version_number': int(row[2] or 1),
        'title': str(row[3] or ''),
        'status': str(row[4] or 'draft'),
        'discipline': str(row[5] or ''),
        'symbol': str(row[6] or ''),
        'timeframe': str(row[7] or ''),
        'summary': str(row[8] or ''),
        'article': article,
        'change_note': str(row[10] or ''),
        'created_at': _deserialize_timestamp_value(row[11]),
    }


def _resolve_workspace_research_project_key(
    connection,
    user_id: str,
    workspace_id: str,
    preferred: str | None,
    *,
    exclude_paper_id: int | None = None,
):
    base_key = _slugify_workspace_research_project_key(preferred) or f'paper-{int(time.time())}'
    suffix = 0

    while True:
        candidate = base_key if suffix <= 0 else f'{base_key}-{suffix + 1}'
        if exclude_paper_id is None:
            row = connection.execute(
                '''
                SELECT id
                FROM workspace_research_papers
                WHERE user_id = ? AND workspace_id = ? AND project_key = ?
                LIMIT 1
                ''',
                (user_id, workspace_id, candidate),
            ).fetchone()
        else:
            row = connection.execute(
                '''
                SELECT id
                FROM workspace_research_papers
                WHERE user_id = ? AND workspace_id = ? AND project_key = ? AND id != ?
                LIMIT 1
                ''',
                (user_id, workspace_id, candidate, int(exclude_paper_id)),
            ).fetchone()
        if not row:
            return candidate
        suffix += 1


def _find_workspace_research_paper_id_by_project_key(
    connection,
    user_id: str,
    workspace_id: str,
    project_key: str | None,
):
    safe_project_key = _slugify_workspace_research_project_key(project_key)
    if not safe_project_key:
        return None
    row = connection.execute(
        '''
        SELECT id
        FROM workspace_research_papers
        WHERE user_id = ? AND workspace_id = ? AND project_key = ?
        LIMIT 1
        ''',
        (user_id, workspace_id, safe_project_key),
    ).fetchone()
    if not row:
        return None
    return int(row[0])


def list_workspace_research_papers(user_id: str, workspace_id: str, limit: int = 100):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        rows = connection.execute(
            '''
            SELECT
                id,
                project_key,
                title,
                status,
                discipline,
                symbol,
                timeframe,
                summary,
                current_version_number,
                current_version_id,
                current_article_json,
                created_at,
                updated_at
            FROM workspace_research_papers
            WHERE user_id = ? AND workspace_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            ''',
            (user_id, workspace_id, max(1, int(limit))),
        ).fetchall()

    return [_deserialize_workspace_research_paper_row(row) for row in rows]


def get_workspace_research_paper(user_id: str, workspace_id: str, paper_id: int):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        row = connection.execute(
            '''
            SELECT
                id,
                project_key,
                title,
                status,
                discipline,
                symbol,
                timeframe,
                summary,
                current_version_number,
                current_version_id,
                current_article_json,
                created_at,
                updated_at
            FROM workspace_research_papers
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            LIMIT 1
            ''',
            (user_id, workspace_id, int(paper_id)),
        ).fetchone()

    return _deserialize_workspace_research_paper_row(row)


def list_workspace_research_paper_versions(user_id: str, workspace_id: str, paper_id: int, limit: int = 100):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        rows = connection.execute(
            '''
            SELECT
                id,
                paper_id,
                version_number,
                title,
                status,
                discipline,
                symbol,
                timeframe,
                summary,
                article_json,
                change_note,
                created_at
            FROM workspace_research_paper_versions
            WHERE user_id = ? AND workspace_id = ? AND paper_id = ?
            ORDER BY version_number DESC, id DESC
            LIMIT ?
            ''',
            (user_id, workspace_id, int(paper_id), max(1, int(limit))),
        ).fetchall()

    return [_deserialize_workspace_research_paper_version_row(row) for row in rows]


def get_workspace_research_paper_version(user_id: str, workspace_id: str, version_id: int):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        row = connection.execute(
            '''
            SELECT
                id,
                paper_id,
                version_number,
                title,
                status,
                discipline,
                symbol,
                timeframe,
                summary,
                article_json,
                change_note,
                created_at
            FROM workspace_research_paper_versions
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            LIMIT 1
            ''',
            (user_id, workspace_id, int(version_id)),
        ).fetchone()

    return _deserialize_workspace_research_paper_version_row(row)


def create_workspace_research_paper(
    user_id: str,
    workspace_id: str,
    *,
    project_key: str | None,
    title: str,
    status: str | None,
    discipline: str | None,
    symbol: str | None,
    timeframe: str | None,
    summary: str | None,
    article: dict | None,
    reuse_existing_project_key: bool = False,
):
    ensure_workspace_store()
    now = time.time()
    safe_title = str(title or '').strip() or 'Untitled research paper'
    safe_status = str(status or '').strip().lower() or 'draft'
    safe_discipline = str(discipline or '').strip()
    safe_symbol = str(symbol or '').strip().upper()
    safe_timeframe = str(timeframe or '').strip().upper()
    safe_summary = str(summary or '').strip()
    safe_article = _normalize_workspace_research_article(article)
    serialized_article = json.dumps(safe_article, ensure_ascii=True)

    with _connect_workspace_db() as connection:
        if reuse_existing_project_key:
            existing_paper_id = _find_workspace_research_paper_id_by_project_key(
                connection,
                user_id,
                workspace_id,
                project_key or safe_title,
            )
            if existing_paper_id is not None:
                return get_workspace_research_paper(user_id, workspace_id, existing_paper_id)
        safe_project_key = _resolve_workspace_research_project_key(
            connection,
            user_id,
            workspace_id,
            project_key or safe_title,
        )
        paper_cursor = connection.execute(
            '''
            INSERT INTO workspace_research_papers (
                user_id,
                workspace_id,
                project_key,
                title,
                status,
                discipline,
                symbol,
                timeframe,
                summary,
                current_version_number,
                current_article_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                safe_project_key,
                safe_title,
                safe_status,
                safe_discipline,
                safe_symbol,
                safe_timeframe,
                safe_summary,
                1,
                serialized_article,
                now,
                now,
            ),
        )
        paper_id = int(paper_cursor.lastrowid)
        connection.commit()

    return get_workspace_research_paper(user_id, workspace_id, paper_id)


def update_workspace_research_paper(
    user_id: str,
    workspace_id: str,
    paper_id: int,
    *,
    project_key: str | None = None,
    title: str | None = None,
    status: str | None = None,
    discipline: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    summary: str | None = None,
    article: dict | None = None,
):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        current_row = connection.execute(
            '''
            SELECT
                id,
                project_key,
                title,
                status,
                discipline,
                symbol,
                timeframe,
                summary,
                current_version_number,
                current_version_id,
                current_article_json,
                created_at,
                updated_at
            FROM workspace_research_papers
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            LIMIT 1
            ''',
            (user_id, workspace_id, int(paper_id)),
        ).fetchone()
        current = _deserialize_workspace_research_paper_row(current_row)
        if not current:
            return None

        safe_title = str(title).strip() if title is not None else str(current['title'] or '')
        safe_title = safe_title or 'Untitled research paper'
        safe_status = str(status).strip().lower() if status is not None else str(current['status'] or 'draft')
        safe_status = safe_status or 'draft'
        safe_discipline = str(discipline).strip() if discipline is not None else str(current['discipline'] or '')
        safe_symbol = (str(symbol).strip().upper() if symbol is not None else str(current['symbol'] or '')).upper()
        safe_timeframe = (str(timeframe).strip().upper() if timeframe is not None else str(current['timeframe'] or '')).upper()
        safe_summary = str(summary).strip() if summary is not None else str(current['summary'] or '')
        safe_article = _normalize_workspace_research_article(article if article is not None else current['article'])
        serialized_article = json.dumps(safe_article, ensure_ascii=True)
        safe_project_key = _resolve_workspace_research_project_key(
            connection,
            user_id,
            workspace_id,
            project_key if project_key is not None else current['project_key'],
            exclude_paper_id=int(paper_id),
        )
        now = time.time()

        connection.execute(
            '''
            UPDATE workspace_research_papers
            SET
                project_key = ?,
                title = ?,
                status = ?,
                discipline = ?,
                symbol = ?,
                timeframe = ?,
                summary = ?,
                current_version_number = 1,
                current_version_id = NULL,
                current_article_json = ?,
                updated_at = ?
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (
                safe_project_key,
                safe_title,
                safe_status,
                safe_discipline,
                safe_symbol,
                safe_timeframe,
                safe_summary,
                serialized_article,
                now,
                user_id,
                workspace_id,
                int(paper_id),
            ),
        )
        connection.commit()

    return get_workspace_research_paper(user_id, workspace_id, paper_id)


def delete_workspace_research_paper(user_id: str, workspace_id: str, paper_id: int):
    ensure_workspace_store()
    existing = get_workspace_research_paper(user_id, workspace_id, paper_id)
    if not existing:
        return None

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            DELETE FROM workspace_research_papers
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(paper_id)),
        )
        connection.execute(
            '''
            DELETE FROM workspace_research_paper_versions
            WHERE user_id = ? AND workspace_id = ? AND paper_id = ?
            ''',
            (user_id, workspace_id, int(paper_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'project_key': existing['project_key'],
        'title': existing['title'],
    }


def create_workspace_research_run(
    user_id: str,
    workspace_id: str,
    *,
    run_type: str,
    side: str | None,
    run_name: str,
    version: str | None,
    best_id: str | None,
    best_label: str | None,
    comparison_count: int | None,
    run_label: str | None = None,
    run_notes: str | None = None,
    pinned: bool | None = None,
    payload: dict | list | None,
):
    ensure_workspace_store()
    now = time.time()
    serialized_payload = json.dumps(payload or {}, ensure_ascii=True)

    with _connect_workspace_db() as connection:
        cursor = connection.execute(
            '''
            INSERT INTO workspace_research_runs (
                user_id,
                workspace_id,
                run_type,
                side,
                run_name,
                version,
                best_id,
                best_label,
                comparison_count,
                run_label,
                run_notes,
                pinned,
                payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(run_type or '').strip() or 'study',
                str(side or '').strip() or '',
                str(run_name or '').strip() or 'Research run',
                str(version or '').strip() or '',
                str(best_id or '').strip() or '',
                str(best_label or '').strip() or '',
                int(comparison_count or 0),
                str(run_label or '').strip() or '',
                str(run_notes or '').strip() or '',
                1 if pinned else 0,
                serialized_payload,
                now,
            ),
        )
        connection.commit()
        run_id = int(cursor.lastrowid)

    return {
        'id': run_id,
        'type': str(run_type or '').strip() or 'study',
        'side': str(side or '').strip() or '',
        'run_name': str(run_name or '').strip() or 'Research run',
        'version': str(version or '').strip() or '',
        'best_id': str(best_id or '').strip() or '',
        'best_label': str(best_label or '').strip() or '',
        'comparison_count': int(comparison_count or 0),
        'run_label': str(run_label or '').strip() or '',
        'run_notes': str(run_notes or '').strip() or '',
        'pinned': bool(pinned),
        'payload': payload or {},
        'payload_loaded': True,
        'payload_size_bytes': len(serialized_payload),
        'created_at': now,
    }


def create_workspace_research_job(
    user_id: str,
    workspace_id: str,
    *,
    job_type: str,
    request: dict | None,
    run_label: str | None = None,
    run_notes: str | None = None,
):
    ensure_workspace_store()
    now = time.time()

    with _connect_workspace_db() as connection:
        cursor = connection.execute(
            '''
            INSERT INTO workspace_research_jobs (
                user_id,
                workspace_id,
                job_type,
                status,
                progress,
                phase,
                phase_label,
                detail,
                error,
                run_id,
                run_label,
                run_notes,
                cancel_requested,
                request_json,
                result_json,
                created_at,
                started_at,
                finished_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(job_type or '').strip() or 'preset_compare',
                'queued',
                0.0,
                'queued',
                'Queued',
                'Waiting for backend worker.',
                '',
                None,
                str(run_label or '').strip() or '',
                str(run_notes or '').strip() or '',
                0,
                json.dumps(request or {}, ensure_ascii=True),
                json.dumps({}, ensure_ascii=True),
                now,
                None,
                None,
                now,
            ),
        )
        connection.commit()
        job_id = int(cursor.lastrowid)

    return get_workspace_research_job(user_id, workspace_id, job_id)


def update_workspace_research_job(
    user_id: str,
    workspace_id: str,
    job_id: int,
    *,
    status: str | None = None,
    progress: float | None = None,
    phase: str | None = None,
    phase_label: str | None = None,
    detail: str | None = None,
    error: str | None = None,
    run_id: int | None = None,
    run_label: str | None = None,
    run_notes: str | None = None,
    cancel_requested: bool | None = None,
    request: dict | None = None,
    result: dict | list | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
):
    ensure_workspace_store()

    updates = []
    params: list[object] = []

    if status is not None:
        updates.append('status = ?')
        params.append(str(status).strip() or 'queued')
    if progress is not None:
        updates.append('progress = ?')
        params.append(max(0.0, min(1.0, float(progress))))
    if phase is not None:
        updates.append('phase = ?')
        params.append(str(phase).strip())
    if phase_label is not None:
        updates.append('phase_label = ?')
        params.append(str(phase_label).strip())
    if detail is not None:
        updates.append('detail = ?')
        params.append(str(detail).strip())
    if error is not None:
        updates.append('error = ?')
        params.append(str(error).strip())
    if run_id is not None:
        updates.append('run_id = ?')
        params.append(int(run_id))
    if run_label is not None:
        updates.append('run_label = ?')
        params.append(str(run_label).strip())
    if run_notes is not None:
        updates.append('run_notes = ?')
        params.append(str(run_notes).strip())
    if cancel_requested is not None:
        updates.append('cancel_requested = ?')
        params.append(1 if cancel_requested else 0)
    if request is not None:
        updates.append('request_json = ?')
        params.append(json.dumps(request, ensure_ascii=True))
    if result is not None:
        updates.append('result_json = ?')
        params.append(json.dumps(result, ensure_ascii=True))
    if started_at is not None:
        updates.append('started_at = ?')
        params.append(float(started_at))
    if finished_at is not None:
        updates.append('finished_at = ?')
        params.append(float(finished_at))

    updates.append('updated_at = ?')
    params.append(time.time())
    params.extend([user_id, workspace_id, int(job_id)])

    with _connect_workspace_db() as connection:
        connection.execute(
            f'''
            UPDATE workspace_research_jobs
            SET {", ".join(updates)}
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            params,
        )
        connection.commit()

    return get_workspace_research_job(user_id, workspace_id, job_id)


def touch_workspace_research_job(
    user_id: str,
    workspace_id: str,
    job_id: int,
    *,
    updated_at: float | None = None,
):
    ensure_workspace_store()
    now = float(updated_at) if updated_at is not None else time.time()

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            UPDATE workspace_research_jobs
            SET updated_at = ?
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (now, user_id, workspace_id, int(job_id)),
        )
        connection.commit()

    return get_workspace_research_job(user_id, workspace_id, job_id)


def purge_expired_workspace_backtest_jobs(
    user_id: str | None = None,
    workspace_id: str | None = None,
    *,
    now: float | None = None,
):
    ensure_workspace_store()
    safe_now = float(now) if now is not None else time.time()
    query = '''
        DELETE FROM workspace_backtest_jobs
        WHERE expires_at IS NOT NULL AND expires_at <= ?
    '''
    params: list[object] = [safe_now]

    if user_id is not None:
        query += ' AND user_id = ?'
        params.append(str(user_id or '').strip())
    if workspace_id is not None:
        query += ' AND workspace_id = ?'
        params.append(str(workspace_id or '').strip())

    with _connect_workspace_db() as connection:
        cursor = connection.execute(query, params)
        connection.commit()
        deleted = int(cursor.rowcount or 0)

    return {
        'deleted': deleted,
        'expired_before': safe_now,
    }


def create_workspace_backtest_job(
    user_id: str,
    workspace_id: str,
    *,
    job_id: str,
    request: dict | None,
    status: str = 'queued',
    progress: float = 0.0,
    phase: str | None = 'queued',
    phase_label: str | None = 'Queued',
    detail: str | None = 'Backtest job queued.',
    error: str | None = '',
    cancel_requested: bool = False,
    result: dict | list | None = None,
    created_at: float | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
):
    ensure_workspace_store()
    safe_job_id = str(job_id or '').strip()
    if not safe_job_id:
        raise ValueError('job_id is required')

    safe_created_at = float(created_at) if created_at is not None else time.time()
    safe_finished_at = float(finished_at) if finished_at is not None else None
    safe_status = str(status or '').strip() or 'queued'
    expires_at = None
    if safe_status in BACKTEST_JOB_TERMINAL_STATUSES:
        terminal_at = safe_finished_at if safe_finished_at is not None else safe_created_at
        expires_at = terminal_at + BACKTEST_JOB_TERMINAL_RETENTION_SECONDS

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            INSERT OR REPLACE INTO workspace_backtest_jobs (
                job_id,
                user_id,
                workspace_id,
                status,
                progress,
                phase,
                phase_label,
                detail,
                error,
                cancel_requested,
                request_json,
                result_json,
                created_at,
                started_at,
                finished_at,
                updated_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                safe_job_id,
                str(user_id or '').strip(),
                str(workspace_id or '').strip() or 'default',
                safe_status,
                max(0.0, min(1.0, float(progress))),
                str(phase or '').strip(),
                str(phase_label or '').strip(),
                str(detail or '').strip(),
                str(error or '').strip(),
                1 if cancel_requested else 0,
                json.dumps(request or {}, ensure_ascii=True),
                json.dumps(result if result is not None else {}, ensure_ascii=True),
                safe_created_at,
                None if started_at is None else float(started_at),
                safe_finished_at,
                safe_created_at,
                expires_at,
            ),
        )
        connection.commit()

    return get_workspace_backtest_job(user_id, workspace_id, safe_job_id)


def get_workspace_backtest_job(
    user_id: str,
    workspace_id: str,
    job_id: str,
):
    ensure_workspace_store()
    safe_job_id = str(job_id or '').strip()
    if not safe_job_id:
        return None

    with _connect_workspace_db() as connection:
        row = connection.execute(
            '''
            SELECT job_id, status, progress, phase, phase_label, detail, error, cancel_requested,
                   request_json, result_json, length(request_json), length(result_json),
                   created_at, started_at, finished_at, updated_at, expires_at
            FROM workspace_backtest_jobs
            WHERE user_id = ? AND workspace_id = ? AND job_id = ?
            ''',
            (str(user_id or '').strip(), str(workspace_id or '').strip() or 'default', safe_job_id),
        ).fetchone()

    return _deserialize_workspace_backtest_job_detail(row) if row else None


def list_workspace_backtest_jobs(
    user_id: str,
    workspace_id: str,
    limit: int = 20,
    *,
    statuses: list[str] | tuple[str, ...] | set[str] | None = None,
):
    ensure_workspace_store()
    safe_limit = max(1, min(int(limit or 20), 200))
    safe_statuses = [
        str(status or '').strip().lower()
        for status in (statuses or [])
        if str(status or '').strip()
    ]
    safe_statuses = [status for status in safe_statuses if status]

    query = '''
        SELECT job_id, status, progress, phase, phase_label, detail, error, cancel_requested,
               length(request_json), length(result_json),
               created_at, started_at, finished_at, updated_at, expires_at
        FROM workspace_backtest_jobs
        WHERE user_id = ? AND workspace_id = ?
    '''
    params: list[object] = [
        str(user_id or '').strip(),
        str(workspace_id or '').strip() or 'default',
    ]

    if safe_statuses:
        placeholders = ', '.join('?' for _ in safe_statuses)
        query += f' AND lower(status) IN ({placeholders})'
        params.extend(safe_statuses)

    query += ' ORDER BY created_at DESC, job_id DESC LIMIT ?'
    params.append(safe_limit)

    with _connect_workspace_db() as connection:
        rows = connection.execute(query, params).fetchall()

    return [_deserialize_workspace_backtest_job_summary(row) for row in rows]


def update_workspace_backtest_job(
    user_id: str,
    workspace_id: str,
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    phase: str | None = None,
    phase_label: str | None = None,
    detail: str | None = None,
    error: str | None = None,
    cancel_requested: bool | None = None,
    request: dict | None = None,
    result: dict | list | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
):
    ensure_workspace_store()
    safe_job_id = str(job_id or '').strip()
    if not safe_job_id:
        raise ValueError('job_id is required')

    updates = []
    params: list[object] = []
    safe_status = str(status or '').strip() if status is not None else None
    safe_finished_at = float(finished_at) if finished_at is not None else None

    if safe_status is not None:
        updates.append('status = ?')
        params.append(safe_status or 'queued')
    if progress is not None:
        updates.append('progress = ?')
        params.append(max(0.0, min(1.0, float(progress))))
    if phase is not None:
        updates.append('phase = ?')
        params.append(str(phase).strip())
    if phase_label is not None:
        updates.append('phase_label = ?')
        params.append(str(phase_label).strip())
    if detail is not None:
        updates.append('detail = ?')
        params.append(str(detail).strip())
    if error is not None:
        updates.append('error = ?')
        params.append(str(error).strip())
    if cancel_requested is not None:
        updates.append('cancel_requested = ?')
        params.append(1 if cancel_requested else 0)
    if request is not None:
        updates.append('request_json = ?')
        params.append(json.dumps(request, ensure_ascii=True))
    if result is not None:
        updates.append('result_json = ?')
        params.append(json.dumps(result, ensure_ascii=True))
    if started_at is not None:
        updates.append('started_at = ?')
        params.append(float(started_at))
    if safe_finished_at is not None:
        updates.append('finished_at = ?')
        params.append(safe_finished_at)

    if safe_status in BACKTEST_JOB_TERMINAL_STATUSES:
        terminal_at = safe_finished_at if safe_finished_at is not None else time.time()
        if safe_finished_at is None:
            updates.append('finished_at = ?')
            params.append(terminal_at)
        updates.append('expires_at = ?')
        params.append(terminal_at + BACKTEST_JOB_TERMINAL_RETENTION_SECONDS)
    elif safe_status is not None:
        updates.append('expires_at = ?')
        params.append(None)

    updates.append('updated_at = ?')
    params.append(time.time())
    params.extend([
        str(user_id or '').strip(),
        str(workspace_id or '').strip() or 'default',
        safe_job_id,
    ])

    with _connect_workspace_db() as connection:
        connection.execute(
            f'''
            UPDATE workspace_backtest_jobs
            SET {", ".join(updates)}
            WHERE user_id = ? AND workspace_id = ? AND job_id = ?
            ''',
            params,
        )
        connection.commit()

    return get_workspace_backtest_job(user_id, workspace_id, safe_job_id)


def upsert_workspace_live_trade(
    user_id: str,
    workspace_id: str,
    *,
    command_id: str,
    source_intent_id: str | None = None,
    execution_mode: str | None = None,
    portfolio_mode: str | None = None,
    portfolio_id: str | None = None,
    portfolio_label: str | None = None,
    pipeline_id: str | None = None,
    pipeline_label: str | None = None,
    status: str | None = None,
    sleeve_id: str | None = None,
    sleeve_label: str | None = None,
    source_strategy_id: str | None = None,
    cycle_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    action: str | None = None,
    side: str | None = None,
    bar_time: float | int | None = None,
    created_at: float | int | None = None,
    claimed_at: float | int | None = None,
    acknowledged_at: float | int | None = None,
    filled_at: float | int | None = None,
    rejected_at: float | int | None = None,
    broker_order_id: str | None = None,
    broker_position_ticket: str | None = None,
    broker_deal_id: str | None = None,
    fill_price: float | int | None = None,
    fill_volume: float | int | None = None,
    profit: float | int | None = None,
    commission: float | int | None = None,
    swap: float | int | None = None,
    exit_reason: str | None = None,
    message: str | None = None,
    strategy: dict | None = None,
    broker_profile_id: str | None = None,
    broker_profile_label: str | None = None,
):
    ensure_workspace_store()
    now = time.time()
    safe_command_id = str(command_id or '').strip()
    if not safe_command_id:
        raise ValueError('command_id is required')

    with _connect_workspace_db() as connection:
        existing = connection.execute(
            '''
            SELECT id
            FROM workspace_live_trades
            WHERE user_id = ? AND workspace_id = ? AND command_id = ?
            ''',
            (user_id, workspace_id, safe_command_id),
        ).fetchone()
        resolved_scope = _resolve_workspace_broker_profile_scope(
            connection,
            user_id,
            workspace_id,
            broker_profile_id=broker_profile_id,
            broker_profile_label=broker_profile_label,
        )

        record = {
            'source_intent_id': str(source_intent_id or '').strip(),
            'execution_mode': str(execution_mode or '').strip() or 'live_mt5',
            'portfolio_mode': str(portfolio_mode or '').strip(),
            'portfolio_id': str(portfolio_id or '').strip(),
            'portfolio_label': str(portfolio_label or '').strip(),
            'pipeline_id': str(pipeline_id or '').strip(),
            'pipeline_label': str(pipeline_label or '').strip(),
            'status': str(status or '').strip() or 'filled',
            'sleeve_id': str(sleeve_id or '').strip(),
            'sleeve_label': str(sleeve_label or '').strip(),
            'source_strategy_id': str(source_strategy_id or '').strip(),
            'cycle_id': str(cycle_id or '').strip(),
            'symbol': str(symbol or '').strip().upper(),
            'timeframe': str(timeframe or '').strip().upper(),
            'action': str(action or '').strip(),
            'side': str(side or '').strip(),
            'bar_time': None if bar_time is None else float(bar_time),
            'created_at': None if created_at is None else float(created_at),
            'claimed_at': None if claimed_at is None else float(claimed_at),
            'acknowledged_at': None if acknowledged_at is None else float(acknowledged_at),
            'filled_at': None if filled_at is None else float(filled_at),
            'rejected_at': None if rejected_at is None else float(rejected_at),
            'broker_order_id': str(broker_order_id or '').strip(),
            'broker_position_ticket': str(broker_position_ticket or '').strip(),
            'broker_deal_id': str(broker_deal_id or '').strip(),
            'fill_price': None if fill_price is None else float(fill_price),
            'fill_volume': None if fill_volume is None else float(fill_volume),
            'profit': None if profit is None else float(profit),
            'commission': None if commission is None else float(commission),
            'swap': None if swap is None else float(swap),
            'exit_reason': str(exit_reason or '').strip(),
            'message': str(message or '').strip(),
            'strategy_json': json.dumps(strategy or {}, ensure_ascii=True),
            'broker_profile_id': str(resolved_scope.get('broker_profile_id') or '').strip(),
            'broker_profile_label': str(resolved_scope.get('broker_profile_label') or '').strip(),
            'record_created_at': now,
        }

        if existing:
            connection.execute(
                '''
                UPDATE workspace_live_trades
                SET source_intent_id = ?, execution_mode = ?, portfolio_mode = ?, portfolio_id = ?, portfolio_label = ?,
                    pipeline_id = ?, pipeline_label = ?, status = ?, sleeve_id = ?, sleeve_label = ?, source_strategy_id = ?,
                    cycle_id = ?, symbol = ?, timeframe = ?, action = ?, side = ?, bar_time = ?, created_at = ?, claimed_at = ?,
                    acknowledged_at = ?, filled_at = ?, rejected_at = ?, broker_order_id = ?, broker_position_ticket = ?,
                    broker_deal_id = ?, fill_price = ?, fill_volume = ?, profit = ?, commission = ?, swap = ?, exit_reason = ?,
                    message = ?, strategy_json = ?, broker_profile_id = ?, broker_profile_label = ?
                WHERE user_id = ? AND workspace_id = ? AND command_id = ?
                ''',
                (
                    record['source_intent_id'],
                    record['execution_mode'],
                    record['portfolio_mode'],
                    record['portfolio_id'],
                    record['portfolio_label'],
                    record['pipeline_id'],
                    record['pipeline_label'],
                    record['status'],
                    record['sleeve_id'],
                    record['sleeve_label'],
                    record['source_strategy_id'],
                    record['cycle_id'],
                    record['symbol'],
                    record['timeframe'],
                    record['action'],
                    record['side'],
                    record['bar_time'],
                    record['created_at'],
                    record['claimed_at'],
                    record['acknowledged_at'],
                    record['filled_at'],
                    record['rejected_at'],
                    record['broker_order_id'],
                    record['broker_position_ticket'],
                    record['broker_deal_id'],
                    record['fill_price'],
                    record['fill_volume'],
                    record['profit'],
                    record['commission'],
                    record['swap'],
                    record['exit_reason'],
                    record['message'],
                    record['strategy_json'],
                    record['broker_profile_id'],
                    record['broker_profile_label'],
                    user_id,
                    workspace_id,
                    safe_command_id,
                ),
            )
        else:
            connection.execute(
                '''
                INSERT INTO workspace_live_trades (
                    user_id, workspace_id, command_id, source_intent_id, execution_mode, portfolio_mode, portfolio_id, portfolio_label,
                    pipeline_id, pipeline_label, status, sleeve_id, sleeve_label, source_strategy_id, cycle_id, symbol, timeframe, action,
                    side, bar_time, created_at, claimed_at, acknowledged_at, filled_at, rejected_at, broker_order_id, broker_position_ticket,
                    broker_deal_id, fill_price, fill_volume, profit, commission, swap, exit_reason, message, strategy_json,
                    broker_profile_id, broker_profile_label, record_created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    user_id,
                    workspace_id,
                    safe_command_id,
                    record['source_intent_id'],
                    record['execution_mode'],
                    record['portfolio_mode'],
                    record['portfolio_id'],
                    record['portfolio_label'],
                    record['pipeline_id'],
                    record['pipeline_label'],
                    record['status'],
                    record['sleeve_id'],
                    record['sleeve_label'],
                    record['source_strategy_id'],
                    record['cycle_id'],
                    record['symbol'],
                    record['timeframe'],
                    record['action'],
                    record['side'],
                    record['bar_time'],
                    record['created_at'],
                    record['claimed_at'],
                    record['acknowledged_at'],
                    record['filled_at'],
                    record['rejected_at'],
                    record['broker_order_id'],
                    record['broker_position_ticket'],
                    record['broker_deal_id'],
                    record['fill_price'],
                    record['fill_volume'],
                    record['profit'],
                    record['commission'],
                    record['swap'],
                    record['exit_reason'],
                    record['message'],
                    record['strategy_json'],
                    record['broker_profile_id'],
                    record['broker_profile_label'],
                    record['record_created_at'],
                ),
            )
        connection.commit()

    return get_workspace_live_trade_by_command_id(user_id, workspace_id, safe_command_id)


def get_workspace_live_trade_by_command_id(user_id: str, workspace_id: str, command_id: str):
    ensure_workspace_store()
    with _connect_workspace_db() as connection:
        row = connection.execute(
            '''
            SELECT id, command_id, source_intent_id, execution_mode, portfolio_mode, portfolio_id, portfolio_label, pipeline_id,
                   pipeline_label, status, sleeve_id, sleeve_label, source_strategy_id, cycle_id, symbol, timeframe, action, side,
                   bar_time, created_at, claimed_at, acknowledged_at, filled_at, rejected_at, broker_order_id, broker_position_ticket,
                   broker_deal_id, fill_price, fill_volume, profit, commission, swap, exit_reason, message, strategy_json,
                   broker_profile_id, broker_profile_label, record_created_at
            FROM workspace_live_trades
            WHERE user_id = ? AND workspace_id = ? AND command_id = ?
            ''',
            (user_id, workspace_id, str(command_id or '').strip()),
        ).fetchone()
    return _deserialize_workspace_live_trade(row) if row else None


def update_workspace_live_trade_cycle_broker_position_ticket(
    user_id: str,
    workspace_id: str,
    cycle_id: str,
    broker_position_ticket: str,
    *,
    sleeve_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
):
    ensure_workspace_store()
    safe_cycle_id = str(cycle_id or '').strip()
    safe_ticket = str(broker_position_ticket or '').strip()
    if not safe_cycle_id or not safe_ticket:
        return 0

    query = '''
        SELECT id, broker_position_ticket
        FROM workspace_live_trades
        WHERE user_id = ? AND workspace_id = ? AND cycle_id = ? AND execution_mode = 'live_mt5'
    '''
    params: list[object] = [user_id, workspace_id, safe_cycle_id]

    safe_sleeve_id = str(sleeve_id or '').strip()
    safe_symbol = str(symbol or '').strip().upper()
    safe_timeframe = str(timeframe or '').strip().upper()
    if safe_sleeve_id:
        query += ' AND sleeve_id = ?'
        params.append(safe_sleeve_id)
    if safe_symbol:
        query += ' AND symbol = ?'
        params.append(safe_symbol)
    if safe_timeframe:
        query += ' AND timeframe = ?'
        params.append(safe_timeframe)

    with _connect_workspace_db() as connection:
        rows = connection.execute(query, params).fetchall()
        target_ids = [
            int(row[0])
            for row in rows
            if str(row[1] or '').strip() != safe_ticket
        ]
        if not target_ids:
            return 0

        placeholders = ', '.join('?' for _ in target_ids)
        connection.execute(
            f'''
            UPDATE workspace_live_trades
            SET broker_position_ticket = ?
            WHERE id IN ({placeholders})
            ''',
            [safe_ticket, *target_ids],
        )
        connection.commit()

    return len(target_ids)


def list_workspace_live_trades(
    user_id: str,
    workspace_id: str,
    *,
    range_key: str = '30d',
    custom_days: int | None = None,
    strategy_filter: str | None = None,
    symbol_filter: str | None = None,
    status_filter: str | None = None,
    broker_profile_id: str | None = None,
    limit: int = 500,
):
    ensure_workspace_store()

    safe_user_id = str(user_id or '').strip() or 'local-user'
    safe_workspace_id = str(workspace_id or 'default').strip() or 'default'
    candidate_user_ids = [safe_user_id]
    if safe_user_id != 'local-user':
        candidate_user_ids.append('local-user')

    safe_range_key = str(range_key or '30d').strip().lower() or '30d'
    safe_strategy_filter = str(strategy_filter or '').strip().lower()
    safe_symbol_filter = str(symbol_filter or '').strip().upper()
    safe_status_filter = str(status_filter or '').strip().lower()
    safe_broker_profile_id = _normalize_broker_profile_id(broker_profile_id)
    now = time.time()
    range_start = None

    if safe_range_key == 'today':
        local_now = time.localtime(now)
        range_start = time.mktime((
            local_now.tm_year,
            local_now.tm_mon,
            local_now.tm_mday,
            0, 0, 0,
            local_now.tm_wday,
            local_now.tm_yday,
            local_now.tm_isdst,
        ))
    elif safe_range_key in {'7d', 'week'}:
        range_start = now - (7 * 24 * 60 * 60)
    elif safe_range_key in {'30d', 'month'}:
        range_start = now - (30 * 24 * 60 * 60)
    elif safe_range_key == 'custom':
        range_start = now - (max(1, int(custom_days or 1)) * 24 * 60 * 60)

    query = '''
        SELECT id, command_id, source_intent_id, execution_mode, portfolio_mode, portfolio_id, portfolio_label, pipeline_id,
               pipeline_label, status, sleeve_id, sleeve_label, source_strategy_id, cycle_id, symbol, timeframe, action, side,
               bar_time, created_at, claimed_at, acknowledged_at, filled_at, rejected_at, broker_order_id, broker_position_ticket,
               broker_deal_id, fill_price, fill_volume, profit, commission, swap, exit_reason, message, strategy_json,
               broker_profile_id, broker_profile_label, record_created_at
        FROM workspace_live_trades
        WHERE workspace_id = ? AND execution_mode = 'live_mt5'
    '''
    params: list[object] = [safe_workspace_id]
    if len(candidate_user_ids) == 1:
        query += ' AND user_id = ?'
        params.append(candidate_user_ids[0])
    else:
        placeholders = ', '.join('?' for _ in candidate_user_ids)
        query += f' AND user_id IN ({placeholders})'
        params.extend(candidate_user_ids)

    if range_start is not None:
        query += ' AND COALESCE(filled_at, rejected_at, created_at, record_created_at) >= ?'
        params.append(float(range_start))
    if safe_strategy_filter:
        query += ' AND (LOWER(sleeve_label) LIKE ? OR LOWER(source_strategy_id) LIKE ?)'
        params.extend([f'%{safe_strategy_filter}%', f'%{safe_strategy_filter}%'])
    if safe_symbol_filter:
        query += ' AND symbol = ?'
        params.append(safe_symbol_filter)
    if safe_status_filter and safe_status_filter != 'all':
        query += ' AND status = ?'
        params.append(safe_status_filter)
    if safe_broker_profile_id:
        query += ' AND broker_profile_id = ?'
        params.append(safe_broker_profile_id)

    query += ' ORDER BY COALESCE(filled_at, rejected_at, created_at, record_created_at) DESC, id DESC LIMIT ?'
    params.append(max(1, int(limit)))

    with _connect_workspace_db() as connection:
        rows = connection.execute(query, params).fetchall()

    trades = [_deserialize_workspace_live_trade(row) for row in rows]
    filled_trades = [entry for entry in trades if str(entry.get('status') or '').lower() == 'filled']
    rejected_trades = [entry for entry in trades if str(entry.get('status') or '').lower() == 'rejected']
    total_profit = sum(float(entry.get('profit') or 0.0) for entry in filled_trades)
    total_commission = sum(float(entry.get('commission') or 0.0) for entry in filled_trades)
    total_swap = sum(float(entry.get('swap') or 0.0) for entry in filled_trades)
    realized_pnl = total_profit + total_commission + total_swap
    wins = sum(1 for entry in filled_trades if float(entry.get('profit') or 0.0) > 0.0)

    return {
        'trades': trades,
        'summary': {
            'trade_count': len(trades),
            'filled_count': len(filled_trades),
            'rejected_count': len(rejected_trades),
            'win_count': wins,
            'win_rate': (float(wins) / float(len(filled_trades))) if filled_trades else 0.0,
            'gross_profit': total_profit,
            'commission_total': total_commission,
            'swap_total': total_swap,
            'realized_pnl': realized_pnl,
            'symbols': sorted({str(entry.get('symbol') or '') for entry in trades if entry.get('symbol')}),
            'strategies': sorted({str(entry.get('sleeve_label') or '') for entry in trades if entry.get('sleeve_label')}),
        },
        'filters': {
            'range_key': safe_range_key,
            'custom_days': (None if custom_days is None else max(1, int(custom_days))),
            'strategy_filter': strategy_filter or '',
            'symbol_filter': safe_symbol_filter,
            'status_filter': safe_status_filter or 'all',
            'broker_profile_id': safe_broker_profile_id,
            'limit': max(1, int(limit)),
        },
    }


def _compute_trade_reconciliation_summary(trades: list[dict] | None):
    entries = list(trades or [])
    filled_trades = [entry for entry in entries if str(entry.get('status') or '').strip().lower() == 'filled']
    rejected_trades = [entry for entry in entries if str(entry.get('status') or '').strip().lower() == 'rejected']
    resolved_trades = [entry for entry in entries if str(entry.get('status') or '').strip().lower() in {'filled', 'rejected'}]
    total_profit = sum(float(entry.get('profit') or 0.0) for entry in filled_trades)
    total_commission = sum(float(entry.get('commission') or 0.0) for entry in filled_trades)
    total_swap = sum(float(entry.get('swap') or 0.0) for entry in filled_trades)
    realized_pnl = total_profit + total_commission + total_swap
    delays = []
    for entry in resolved_trades:
        created_at = _to_finite_float(entry.get('created_at'))
        resolved_at = _to_finite_float(entry.get('filled_at') or entry.get('rejected_at'))
        if created_at is None or resolved_at is None:
            continue
        delays.append(max(0.0, resolved_at - created_at))

    timestamps = [
        _to_finite_float(entry.get('filled_at') or entry.get('rejected_at') or entry.get('created_at') or entry.get('record_created_at'))
        for entry in entries
    ]
    timestamps = [value for value in timestamps if value is not None]

    return {
        'total_commands': len(entries),
        'filled_count': len(filled_trades),
        'rejected_count': len(rejected_trades),
        'pending_count': max(0, len(entries) - len(filled_trades) - len(rejected_trades)),
        'execution_rate': (float(len(filled_trades)) / float(len(entries))) if entries else 0.0,
        'realized_pnl': realized_pnl,
        'avg_delay_seconds': (sum(delays) / len(delays)) if delays else None,
        'max_delay_seconds': max(delays) if delays else None,
        'first_event_at': min(timestamps) if timestamps else None,
        'last_event_at': max(timestamps) if timestamps else None,
    }


def create_workspace_trade_reconciliation(
    user_id: str,
    workspace_id: str,
    *,
    range_key: str = '7d',
    custom_days: int | None = None,
    strategy_filter: str | None = None,
    broker_profile_id: str | None = None,
    broker_profile_label: str | None = None,
    limit: int = 500,
):
    ensure_workspace_store()
    live_payload = list_workspace_live_trades(
        user_id,
        workspace_id,
        range_key=range_key,
        custom_days=custom_days,
        strategy_filter=strategy_filter,
        symbol_filter='',
        status_filter='all',
        broker_profile_id=broker_profile_id,
        limit=limit,
    )
    rows = list(live_payload.get('trades') or [])
    summary = _compute_trade_reconciliation_summary(rows)
    created_at = time.time()

    with _connect_workspace_db() as connection:
        resolved_scope = _resolve_workspace_broker_profile_scope(
            connection,
            user_id,
            workspace_id,
            broker_profile_id=broker_profile_id,
            broker_profile_label=broker_profile_label,
        )
        cursor = connection.execute(
            '''
            INSERT INTO workspace_trade_reconciliations (
                user_id, workspace_id, range_key, custom_days, strategy_filter, broker_profile_id, broker_profile_label,
                summary_json, rows_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(range_key or '7d').strip().lower() or '7d',
                None if custom_days is None else max(1, int(custom_days)),
                str(strategy_filter or '').strip(),
                str(resolved_scope.get('broker_profile_id') or '').strip(),
                str(resolved_scope.get('broker_profile_label') or '').strip(),
                json.dumps(summary, ensure_ascii=True),
                json.dumps(rows, ensure_ascii=True),
                created_at,
            ),
        )
        connection.commit()
        reconciliation_id = int(cursor.lastrowid)

    return {
        'id': reconciliation_id,
        'range_key': str(range_key or '7d').strip().lower() or '7d',
        'custom_days': (None if custom_days is None else max(1, int(custom_days))),
        'strategy_filter': str(strategy_filter or '').strip(),
        'broker_profile_id': str(resolved_scope.get('broker_profile_id') or '').strip(),
        'broker_profile_label': str(resolved_scope.get('broker_profile_label') or '').strip(),
        'summary': summary,
        'rows': rows,
        'created_at': created_at,
    }


def list_workspace_trade_reconciliations(
    user_id: str,
    workspace_id: str,
    *,
    range_key: str = '7d',
    custom_days: int | None = None,
    strategy_filter: str | None = None,
    broker_profile_id: str | None = None,
    limit: int = 100,
):
    ensure_workspace_store()
    safe_range_key = str(range_key or '7d').strip().lower() or '7d'
    safe_strategy_filter = str(strategy_filter or '').strip()
    safe_custom_days = None if custom_days is None else max(1, int(custom_days))
    safe_broker_profile_id = _normalize_broker_profile_id(broker_profile_id)

    query = '''
        SELECT id, range_key, custom_days, strategy_filter, broker_profile_id, broker_profile_label, summary_json, rows_json, created_at
        FROM workspace_trade_reconciliations
        WHERE user_id = ? AND workspace_id = ? AND range_key = ? AND COALESCE(custom_days, -1) = COALESCE(?, -1)
    '''
    params: list[object] = [user_id, workspace_id, safe_range_key, safe_custom_days]

    if safe_strategy_filter:
        query += ' AND strategy_filter = ?'
        params.append(safe_strategy_filter)
    else:
        query += " AND COALESCE(strategy_filter, '') = ''"
    if safe_broker_profile_id:
        query += ' AND broker_profile_id = ?'
        params.append(safe_broker_profile_id)

    query += ' ORDER BY created_at DESC, id DESC LIMIT ?'
    params.append(max(1, int(limit)))

    with _connect_workspace_db() as connection:
        rows = connection.execute(query, params).fetchall()

    reconciliations = []
    for row in rows:
        reconciliations.append({
            'id': int(row[0]),
            'range_key': str(row[1] or ''),
            'custom_days': (None if row[2] is None else int(row[2])),
            'strategy_filter': str(row[3] or ''),
            'broker_profile_id': str(row[4] or '').strip(),
            'broker_profile_label': str(row[5] or '').strip(),
            'summary': json.loads(row[6] or '{}'),
            'rows': json.loads(row[7] or '[]'),
            'created_at': float(row[8]) if row[8] is not None else None,
        })

    return reconciliations


def delete_workspace_research_job(user_id: str, workspace_id: str, job_id: int):
    ensure_workspace_store()

    existing = get_workspace_research_job(user_id, workspace_id, job_id)
    if not existing:
        return None

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            DELETE FROM workspace_research_jobs
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(job_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'job_type': existing['job_type'],
        'created_at': existing['created_at'],
    }


def create_workspace_research_batch(
    user_id: str,
    workspace_id: str,
    *,
    label: str,
    request: dict | None,
):
    ensure_workspace_store()
    now = time.time()
    jobs = list((request or {}).get('jobs') or [])
    total_jobs = len(jobs)

    with _connect_workspace_db() as connection:
        cursor = connection.execute(
            '''
            INSERT INTO workspace_research_batches (
                user_id, workspace_id, label, status, progress, phase, phase_label, detail, error,
                total_jobs, completed_jobs, failed_jobs, cancelled_jobs, current_job_id, cancel_requested,
                request_json, result_json, created_at, started_at, finished_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(label or '').strip() or 'Research batch',
                'queued',
                0.0,
                'queued',
                'Queued',
                'Waiting for backend batch worker.',
                '',
                total_jobs,
                0,
                0,
                0,
                None,
                0,
                json.dumps(request or {}, ensure_ascii=True),
                json.dumps({}, ensure_ascii=True),
                now,
                None,
                None,
                now,
            ),
        )
        connection.commit()
        batch_id = int(cursor.lastrowid)

    return get_workspace_research_batch(user_id, workspace_id, batch_id)


def update_workspace_research_batch(
    user_id: str,
    workspace_id: str,
    batch_id: int,
    *,
    label: str | None = None,
    status: str | None = None,
    progress: float | None = None,
    phase: str | None = None,
    phase_label: str | None = None,
    detail: str | None = None,
    error: str | None = None,
    total_jobs: int | None = None,
    completed_jobs: int | None = None,
    failed_jobs: int | None = None,
    cancelled_jobs: int | None = None,
    current_job_id: int | None = None,
    cancel_requested: bool | None = None,
    request: dict | None = None,
    result: dict | list | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
):
    ensure_workspace_store()

    updates = []
    params: list[object] = []

    if label is not None:
        updates.append('label = ?')
        params.append(str(label).strip() or 'Research batch')
    if status is not None:
        updates.append('status = ?')
        params.append(str(status).strip() or 'queued')
    if progress is not None:
        updates.append('progress = ?')
        params.append(max(0.0, min(1.0, float(progress))))
    if phase is not None:
        updates.append('phase = ?')
        params.append(str(phase).strip())
    if phase_label is not None:
        updates.append('phase_label = ?')
        params.append(str(phase_label).strip())
    if detail is not None:
        updates.append('detail = ?')
        params.append(str(detail).strip())
    if error is not None:
        updates.append('error = ?')
        params.append(str(error).strip())
    if total_jobs is not None:
        updates.append('total_jobs = ?')
        params.append(max(0, int(total_jobs)))
    if completed_jobs is not None:
        updates.append('completed_jobs = ?')
        params.append(max(0, int(completed_jobs)))
    if failed_jobs is not None:
        updates.append('failed_jobs = ?')
        params.append(max(0, int(failed_jobs)))
    if cancelled_jobs is not None:
        updates.append('cancelled_jobs = ?')
        params.append(max(0, int(cancelled_jobs)))
    if current_job_id is not None:
        updates.append('current_job_id = ?')
        params.append(int(current_job_id))
    if cancel_requested is not None:
        updates.append('cancel_requested = ?')
        params.append(1 if cancel_requested else 0)
    if request is not None:
        updates.append('request_json = ?')
        params.append(json.dumps(request, ensure_ascii=True))
    if result is not None:
        updates.append('result_json = ?')
        params.append(json.dumps(result, ensure_ascii=True))
    if started_at is not None:
        updates.append('started_at = ?')
        params.append(float(started_at))
    if finished_at is not None:
        updates.append('finished_at = ?')
        params.append(float(finished_at))

    updates.append('updated_at = ?')
    params.append(time.time())
    params.extend([user_id, workspace_id, int(batch_id)])

    with _connect_workspace_db() as connection:
        connection.execute(
            f'''
            UPDATE workspace_research_batches
            SET {", ".join(updates)}
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            params,
        )
        connection.commit()

    return get_workspace_research_batch(user_id, workspace_id, batch_id)


def touch_workspace_research_batch(
    user_id: str,
    workspace_id: str,
    batch_id: int,
    *,
    updated_at: float | None = None,
):
    ensure_workspace_store()
    now = float(updated_at) if updated_at is not None else time.time()

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            UPDATE workspace_research_batches
            SET updated_at = ?
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (now, user_id, workspace_id, int(batch_id)),
        )
        connection.commit()

    return get_workspace_research_batch(user_id, workspace_id, batch_id)


def delete_workspace_research_batch(user_id: str, workspace_id: str, batch_id: int):
    ensure_workspace_store()

    existing = get_workspace_research_batch(user_id, workspace_id, batch_id)
    if not existing:
        return None

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            DELETE FROM workspace_research_batches
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(batch_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'label': existing['label'],
        'created_at': existing['created_at'],
    }


def create_workspace_research_campaign(
    user_id: str,
    workspace_id: str,
    *,
    label: str,
    description: str | None = None,
    request: dict | None = None,
):
    ensure_workspace_store()
    now = time.time()

    with _connect_workspace_db() as connection:
        cursor = connection.execute(
            '''
            INSERT INTO workspace_research_campaigns (
                user_id, workspace_id, label, description, request_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                workspace_id,
                str(label or '').strip() or 'Research campaign',
                str(description or '').strip(),
                json.dumps(request or {}, ensure_ascii=True),
                now,
                now,
            ),
        )
        connection.commit()
        campaign_id = int(cursor.lastrowid)

    return get_workspace_research_campaign(user_id, workspace_id, campaign_id)


def update_workspace_research_campaign(
    user_id: str,
    workspace_id: str,
    campaign_id: int,
    *,
    label: str | None = None,
    description: str | None = None,
    request: dict | None = None,
):
    ensure_workspace_store()

    updates = []
    params: list[object] = []

    if label is not None:
        updates.append('label = ?')
        params.append(str(label).strip() or 'Research campaign')
    if description is not None:
        updates.append('description = ?')
        params.append(str(description).strip())
    if request is not None:
        updates.append('request_json = ?')
        params.append(json.dumps(request, ensure_ascii=True))

    if not updates:
        return get_workspace_research_campaign(user_id, workspace_id, campaign_id)

    updates.append('updated_at = ?')
    params.append(time.time())
    params.extend([user_id, workspace_id, int(campaign_id)])

    with _connect_workspace_db() as connection:
        connection.execute(
            f'''
            UPDATE workspace_research_campaigns
            SET {", ".join(updates)}
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            params,
        )
        connection.commit()

    return get_workspace_research_campaign(user_id, workspace_id, campaign_id)


def delete_workspace_research_campaign(user_id: str, workspace_id: str, campaign_id: int):
    ensure_workspace_store()

    existing = get_workspace_research_campaign(user_id, workspace_id, campaign_id)
    if not existing:
        return None

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            DELETE FROM workspace_research_campaigns
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(campaign_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'label': existing['label'],
        'created_at': existing['created_at'],
    }


def update_workspace_research_run(
    user_id: str,
    workspace_id: str,
    run_id: int,
    *,
    run_label: str | None = None,
    run_notes: str | None = None,
    pinned: bool | None = None,
):
    ensure_workspace_store()

    updates = []
    params: list[object] = []

    if run_label is not None:
        updates.append('run_label = ?')
        params.append(str(run_label).strip())
    if run_notes is not None:
        updates.append('run_notes = ?')
        params.append(str(run_notes).strip())
    if pinned is not None:
        updates.append('pinned = ?')
        params.append(1 if pinned else 0)

    if not updates:
        return get_workspace_research_run(user_id, workspace_id, run_id)

    params.extend([user_id, workspace_id, int(run_id)])

    with _connect_workspace_db() as connection:
        connection.execute(
            f'''
            UPDATE workspace_research_runs
            SET {", ".join(updates)}
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            params,
        )
        connection.commit()

    return get_workspace_research_run(user_id, workspace_id, run_id)


def delete_workspace_research_run(user_id: str, workspace_id: str, run_id: int):
    ensure_workspace_store()

    existing = get_workspace_research_run(user_id, workspace_id, run_id, include_payload=False)
    if not existing:
        return None

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            DELETE FROM workspace_research_runs
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(run_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'run_name': existing['run_name'],
        'created_at': existing['created_at'],
    }


def load_workspace_state(user_id: str, workspace_id: str):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        row = connection.execute(
            '''
            SELECT revision, state_json, updated_at
            FROM workspace_state
            WHERE user_id = ? AND workspace_id = ?
            ''',
            (user_id, workspace_id),
        ).fetchone()

    if not row:
        return {
            'user_id': user_id,
            'workspace_id': workspace_id,
            'revision': 0,
            'state': {},
            'updated_at': None,
        }

    revision, state_json, updated_at = row
    loaded_state = json.loads(state_json or '{}')

    with _connect_workspace_db() as repair_connection:
        repaired_state = _repair_workspace_strategy_collections(
            repair_connection,
            user_id,
            workspace_id,
            loaded_state,
        )

    return {
        'user_id': user_id,
        'workspace_id': workspace_id,
        'revision': int(revision),
        'state': repaired_state,
        'updated_at': float(updated_at) if updated_at is not None else None,
    }


def save_workspace_state(user_id: str, workspace_id: str, state: dict, expected_revision: int | None = None):
    ensure_workspace_store()
    now = time.time()

    with _connect_workspace_db() as connection:
        repaired_state = _repair_workspace_strategy_collections(
            connection,
            user_id,
            workspace_id,
            state or {},
        )
        payload = json.dumps(repaired_state or {}, ensure_ascii=True)
        row = connection.execute(
            '''
            SELECT revision
            FROM workspace_state
            WHERE user_id = ? AND workspace_id = ?
            ''',
            (user_id, workspace_id),
        ).fetchone()

        current_revision = int(row[0]) if row else 0

        if expected_revision is not None and current_revision != int(expected_revision):
            raise ValueError(
                f'Workspace revision conflict: expected {expected_revision}, current {current_revision}'
            )

        next_revision = current_revision + 1

        connection.execute(
            '''
            INSERT INTO workspace_state (user_id, workspace_id, revision, state_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, workspace_id) DO UPDATE SET
                revision = excluded.revision,
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            ''',
            (user_id, workspace_id, next_revision, payload, now),
        )
        connection.commit()

    return {
        'user_id': user_id,
        'workspace_id': workspace_id,
        'revision': next_revision,
        'state': repaired_state or {},
        'updated_at': now,
    }


def list_workspace_saves(user_id: str, workspace_id: str, limit: int = 50):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        rows = connection.execute(
            '''
            SELECT id, name, created_at, score
            FROM workspace_saves
            WHERE user_id = ? AND workspace_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            ''',
            (user_id, workspace_id, max(1, int(limit))),
        ).fetchall()

    return [
        {
            'id': int(row[0]),
            'name': str(row[1]),
            'created_at': float(row[2]),
            'score': _to_finite_float(row[3]),
        }
        for row in rows
    ]


def create_workspace_save(user_id: str, workspace_id: str, name: str, state: dict):
    ensure_workspace_store()
    now = time.time()
    payload = json.dumps(state or {}, ensure_ascii=True)
    score = compute_strategy_score_from_state(state)

    with _connect_workspace_db() as connection:
        cursor = connection.execute(
            '''
            INSERT INTO workspace_saves (user_id, workspace_id, name, state_json, created_at, score)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (user_id, workspace_id, str(name).strip() or 'Workspace save', payload, now, score),
        )
        connection.commit()
        save_id = int(cursor.lastrowid)

    return {
        'id': save_id,
        'user_id': user_id,
        'workspace_id': workspace_id,
        'name': str(name).strip() or 'Workspace save',
        'state': state or {},
        'created_at': now,
        'score': score,
    }


def get_workspace_save(user_id: str, workspace_id: str, save_id: int):
    ensure_workspace_store()

    with _connect_workspace_db() as connection:
        row = connection.execute(
            '''
            SELECT id, name, state_json, created_at, score
            FROM workspace_saves
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(save_id)),
        ).fetchone()

    if not row:
        return None

    return {
        'id': int(row[0]),
        'name': str(row[1]),
        'state': json.loads(row[2] or '{}'),
        'created_at': float(row[3]),
        'score': _to_finite_float(row[4]),
    }


def delete_workspace_save(user_id: str, workspace_id: str, save_id: int):
    ensure_workspace_store()

    existing = get_workspace_save(user_id, workspace_id, save_id)
    if not existing:
        return None

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            DELETE FROM workspace_saves
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (user_id, workspace_id, int(save_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'name': existing['name'],
        'created_at': existing['created_at'],
        'score': existing.get('score'),
    }


def rename_workspace_save(user_id: str, workspace_id: str, save_id: int, name: str):
    ensure_workspace_store()

    existing = get_workspace_save(user_id, workspace_id, save_id)
    if not existing:
        return None

    next_name = str(name).strip() or existing['name']

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            UPDATE workspace_saves
            SET name = ?
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (next_name, user_id, workspace_id, int(save_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'name': next_name,
        'created_at': existing['created_at'],
        'score': existing.get('score'),
    }


def overwrite_workspace_save(user_id: str, workspace_id: str, save_id: int, state: dict, name: str | None = None):
    ensure_workspace_store()

    existing = get_workspace_save(user_id, workspace_id, save_id)
    if not existing:
        return None

    next_name = str(name).strip() if name is not None else existing['name']
    if not next_name:
        next_name = existing['name']

    payload = json.dumps(state or {}, ensure_ascii=True)
    score = compute_strategy_score_from_state(state)

    with _connect_workspace_db() as connection:
        connection.execute(
            '''
            UPDATE workspace_saves
            SET name = ?, state_json = ?, score = ?
            WHERE user_id = ? AND workspace_id = ? AND id = ?
            ''',
            (next_name, payload, score, user_id, workspace_id, int(save_id)),
        )
        connection.commit()

    return {
        'id': existing['id'],
        'name': next_name,
        'state': state or {},
        'created_at': existing['created_at'],
        'score': score,
    }


def bootstrap_workspace_owner(
    source_user_id: str,
    target_user_id: str,
    workspace_id: str = 'default',
):
    ensure_workspace_store()

    safe_source_user_id = str(source_user_id or '').strip()
    safe_target_user_id = str(target_user_id or '').strip()
    safe_workspace_id = str(workspace_id or 'default').strip() or 'default'

    if not safe_source_user_id or not safe_target_user_id or safe_source_user_id == safe_target_user_id:
        return {
            'copied_state': False,
            'copied_saves': 0,
        }

    source_state = load_workspace_state(safe_source_user_id, safe_workspace_id)
    target_state = load_workspace_state(safe_target_user_id, safe_workspace_id)
    target_saves = list_workspace_saves(safe_target_user_id, safe_workspace_id, limit=1)

    if int(target_state['revision'] or 0) > 0 or target_saves:
        return {
            'copied_state': False,
            'copied_saves': 0,
        }

    copied_state = False
    copied_saves = 0

    with _connect_workspace_db() as connection:
        if source_state['state'] and int(source_state['revision'] or 0) > 0:
            connection.execute(
                '''
                INSERT INTO workspace_state (user_id, workspace_id, revision, state_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, workspace_id) DO UPDATE SET
                    revision = excluded.revision,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                ''',
                (
                    safe_target_user_id,
                    safe_workspace_id,
                    int(source_state['revision']),
                    json.dumps(source_state['state'] or {}, ensure_ascii=True),
                    float(source_state['updated_at'] or time.time()),
                ),
            )
            copied_state = True

        source_saves = list_workspace_saves(safe_source_user_id, safe_workspace_id, limit=500)
        for source_save in reversed(source_saves):
            full_save = get_workspace_save(safe_source_user_id, safe_workspace_id, source_save['id'])
            if not full_save:
                continue

            connection.execute(
                '''
                INSERT INTO workspace_saves (user_id, workspace_id, name, state_json, created_at, score)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (
                    safe_target_user_id,
                    safe_workspace_id,
                    full_save['name'],
                    json.dumps(full_save['state'] or {}, ensure_ascii=True),
                    float(full_save['created_at']),
                    full_save.get('score'),
                ),
            )
            copied_saves += 1

        connection.commit()

    return {
        'copied_state': copied_state,
        'copied_saves': copied_saves,
    }

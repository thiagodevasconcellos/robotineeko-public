import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_META_NAME = '.codex-lane.json'
DEFAULT_SHARED_ROOT_NAME = 'robotineeko-codex-shared'
DEFAULT_LANES_ROOT_NAME = 'robotineeko-codex-lanes'
SHARED_POSITIVE_HISTORY_DIRNAME = 'positive-history'
SHARED_PUBLISHED_CATALOG_FILENAME = 'research-positive-strategies-shared.json'
RESEARCH_DATA_RELATIVE_DIR = Path('backend/python/data/research')
STRATEGY_CATALOG_RELATIVE_PATH = Path('src/components/Console/researchPositiveStrategiesCatalog.js')
DEFAULT_EVIDENCE_REFS = [
    'docs/strategy-tryouts-register.md',
    'docs/codex-brain.md',
]
DEFAULT_RECENT_ARTIFACT_SCAN_LIMIT = 200
PAPER_ID_PATTERN = re.compile(r'paper\s*([0-9]+)', re.IGNORECASE)
CANDIDATE_ID_PATTERN = re.compile(r'row\s*`?([A-Za-z0-9_-]+)`?', re.IGNORECASE)
SCIENTIFIC_RECORD_PAPER_ID_PATTERN = re.compile(r'paper\s*([0-9]+)_scientific_record_payload_', re.IGNORECASE)
WHITESPACE_PATTERN = re.compile(r'\s+')
HELD_OUT_KEYWORDS = ('held-out', 'holdout')
WALK_FORWARD_KEYWORDS = ('walk-forward', 'walkforward')
COST_KEYWORDS = ('slippage', 'spread', 'cost model', 'cost bundle', 'saved cost model')
COST_PARAMETER_KEYS = (
    'spread_in_pips',
    'entry_slippage_in_pips',
    'close_slippage_in_pips',
    'take_profit_slippage_in_pips',
    'stop_loss_slippage_in_pips',
    'trailing_stop_slippage_in_pips',
    'volatility_slippage_multiplier',
)
HELD_OUT_PASS_PATTERNS = (
    re.compile(r'held-out[^.]{0,180}\+[0-9]'),
    re.compile(r'\+[0-9][^.]{0,180}held-out'),
    re.compile(r'stayed first at about \+[0-9]'),
    re.compile(r'stayed on top at about \+[0-9]'),
    re.compile(r'remained best at about \+[0-9]'),
    re.compile(r'remained on top at about \+[0-9]'),
    re.compile(r'control stayed at about \+[0-9]'),
    re.compile(r'improved to about \+[0-9]'),
    re.compile(r'positive held-out row'),
)
WALK_FORWARD_PASS_PATTERNS = (
    re.compile(r'carried [^.]{0,80} control stayed first'),
    re.compile(r'control stayed first at about \+[0-9]'),
    re.compile(r'control stayed on top at about \+[0-9]'),
    re.compile(r'remained the strongest held-out frontier'),
)
_SHARED_POSITIVE_HISTORY_PAYLOAD_CACHE_LOCK = Lock()
_SHARED_POSITIVE_HISTORY_PAYLOAD_CACHE: dict[str, Any] = {
    'fingerprint': '',
    'payload': None,
}


def _load_lane_meta(repo_root: Path | None = None) -> dict | None:
    lane_meta_path = (repo_root or REPO_ROOT) / LANE_META_NAME
    if not lane_meta_path.is_file():
        return None
    try:
        return json.loads(lane_meta_path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _coerce_path(value: str | os.PathLike[str] | None) -> Path | None:
    if not value:
        return None
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return None


def _append_unique_path(target: list[Path], candidate: Path | None) -> None:
    if candidate is None:
        return
    resolved = candidate.resolve()
    if resolved not in target:
        target.append(resolved)


def _resolve_default_shared_root_candidates() -> list[Path]:
    candidates: list[Path] = []
    _append_unique_path(candidates, REPO_ROOT.parent / DEFAULT_SHARED_ROOT_NAME)
    _append_unique_path(candidates, REPO_ROOT.parent / 'Softwares' / DEFAULT_SHARED_ROOT_NAME)
    return candidates


def resolve_shared_positive_history_root() -> Path:
    explicit_shared_root = os.getenv('ROBOTINEEKO_SHARED_ROOT', '').strip()
    explicit_path = _coerce_path(explicit_shared_root)
    if explicit_path and explicit_path.is_dir():
        return explicit_path

    lane_meta = _load_lane_meta()
    if lane_meta:
        shared_root = _coerce_path(str(lane_meta.get('shared_root') or '').strip())
        if shared_root and shared_root.is_dir():
            return shared_root
        source_repo = _coerce_path(str(lane_meta.get('source_repo') or '').strip())
        if source_repo:
            for candidate in (
                source_repo.parent / DEFAULT_SHARED_ROOT_NAME,
                source_repo.parent / 'Softwares' / DEFAULT_SHARED_ROOT_NAME,
            ):
                if candidate.is_dir():
                    return candidate.resolve()

    for candidate in _resolve_default_shared_root_candidates():
        if candidate.is_dir():
            return candidate

    return _resolve_default_shared_root_candidates()[0]


def _resolve_lane_roots_root_candidates(shared_root: Path) -> list[Path]:
    candidates: list[Path] = []
    _append_unique_path(candidates, shared_root.parent / DEFAULT_LANES_ROOT_NAME)
    _append_unique_path(candidates, REPO_ROOT.parent / DEFAULT_LANES_ROOT_NAME)
    _append_unique_path(candidates, REPO_ROOT.parent / 'Softwares' / DEFAULT_LANES_ROOT_NAME)
    return candidates


def discover_lane_roots() -> list[Path]:
    discovered: list[Path] = []
    _append_unique_path(discovered, REPO_ROOT)

    lane_meta = _load_lane_meta()
    if lane_meta:
        _append_unique_path(discovered, _coerce_path(str(lane_meta.get('source_repo') or '').strip()))

    explicit_roots = os.getenv('ROBOTINEEKO_LANE_ROOTS', '').strip()
    if explicit_roots:
        for raw_root in explicit_roots.split(os.pathsep):
            _append_unique_path(discovered, _coerce_path(raw_root.strip()))

    shared_root = resolve_shared_positive_history_root()
    for lanes_root in _resolve_lane_roots_root_candidates(shared_root):
        if not lanes_root.is_dir():
            continue
        for child in lanes_root.iterdir():
            if not child.is_dir():
                continue
            if (child / RESEARCH_DATA_RELATIVE_DIR).is_dir():
                _append_unique_path(discovered, child)

    return [path for path in discovered if path.is_dir() and (path / RESEARCH_DATA_RELATIVE_DIR).is_dir()]


def _safe_stat_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _scan_stage1_fingerprint(research_root: Path) -> tuple[int, float]:
    if not research_root.is_dir():
        return 0, 0.0

    count = 0
    latest_mtime = 0.0
    for artifact_path in research_root.rglob('*stage1*.json'):
        if artifact_path.suffix != '.json':
            continue
        count += 1
        latest_mtime = max(latest_mtime, _safe_stat_mtime(artifact_path))

    return count, latest_mtime


def _build_positive_history_payload_fingerprint(shared_root: Path, lane_roots: list[Path]) -> str:
    fingerprint_parts = [
        f"shared:{_safe_stat_mtime(shared_root / SHARED_POSITIVE_HISTORY_DIRNAME / SHARED_PUBLISHED_CATALOG_FILENAME):.6f}",
        f"lane_count:{len(lane_roots)}",
    ]

    for lane_root in sorted((path.resolve() for path in lane_roots), key=lambda path: str(path)):
        stage1_count, latest_stage1_mtime = _scan_stage1_fingerprint(lane_root / RESEARCH_DATA_RELATIVE_DIR)
        fingerprint_parts.append(
            '||'.join([
                _lane_name_for_root(lane_root),
                f"catalog:{_safe_stat_mtime(lane_root / STRATEGY_CATALOG_RELATIVE_PATH):.6f}",
                f"stage1_count:{stage1_count}",
                f"stage1_latest:{latest_stage1_mtime:.6f}",
            ])
        )

    return '\n'.join(fingerprint_parts)


def _lane_name_for_root(repo_root: Path) -> str:
    if repo_root.resolve() == REPO_ROOT.resolve():
        return 'robotineeko'
    lane_meta = _load_lane_meta(repo_root)
    lane_name = str((lane_meta or {}).get('lane') or '').strip()
    if lane_name:
        return lane_name
    return repo_root.name.strip() or 'robotineeko'


def _should_scan_artifacts_for_root(repo_root: Path) -> bool:
    return repo_root.is_dir()


def _should_load_lane_catalog_for_root(repo_root: Path) -> bool:
    if repo_root.resolve() != REPO_ROOT.resolve():
        return True
    return _load_lane_meta(repo_root) is not None


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_lane_local_catalog(repo_root: Path) -> tuple[list[dict[str, Any]], str]:
    catalog_path = repo_root / STRATEGY_CATALOG_RELATIVE_PATH
    if not catalog_path.is_file():
        return [], ''

    script = (
        f"import {{ RESEARCH_POSITIVE_STRATEGY_CATALOG, RESEARCH_POSITIVE_STRATEGIES_LAST_UPDATED }} "
        f"from {json.dumps(catalog_path.as_uri())};\n"
        "console.log(JSON.stringify({"
        "catalog: Array.isArray(RESEARCH_POSITIVE_STRATEGY_CATALOG) ? RESEARCH_POSITIVE_STRATEGY_CATALOG : [], "
        "lastUpdated: typeof RESEARCH_POSITIVE_STRATEGIES_LAST_UPDATED === 'string' ? RESEARCH_POSITIVE_STRATEGIES_LAST_UPDATED : ''"
        "}));\n"
    )
    try:
        completed = subprocess.run(
            ['node', '--input-type=module', '--eval', script],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return [], ''
    if completed.returncode != 0:
        return [], ''
    try:
        payload = json.loads(completed.stdout)
    except Exception:
        return [], ''
    catalog = payload.get('catalog')
    last_updated = payload.get('lastUpdated')
    normalized_catalog: list[dict[str, Any]] = []
    if isinstance(catalog, list):
        for entry in catalog:
            normalized_entry = _normalize_catalog_entry(entry)
            if normalized_entry is not None:
                normalized_catalog.append(normalized_entry)
    return (
        normalized_catalog,
        last_updated if isinstance(last_updated, str) else '',
    )


def _build_contextual_registry_key(
    label: str,
    study: str,
    symbol: str,
    timeframe: str,
    side: str,
) -> str:
    return '||'.join([
        'ctx',
        label.strip(),
        study.strip(),
        symbol.strip(),
        timeframe.strip(),
        side.strip(),
    ])


def _extract_paper_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value or '').strip()
    if not text:
        return None
    match = PAPER_ID_PATTERN.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _extract_candidate_id(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    match = CANDIDATE_ID_PATTERN.search(text)
    if match:
        return match.group(1).strip().lower()
    normalized = text.lower()
    if normalized.startswith('s') and normalized[1:].isdigit():
        return normalized
    return ''


def _extract_paper_candidate_refs(entry: dict[str, Any]) -> tuple[int | None, str]:
    if not isinstance(entry, dict):
        return None, ''

    paper_id_sources = [
        entry.get('paperId'),
        entry.get('paper_id'),
        entry.get('id'),
        entry.get('label'),
        entry.get('study'),
        entry.get('checkpointContext'),
    ]
    candidate_id_sources = [
        entry.get('candidateId'),
        entry.get('candidate_id'),
        entry.get('checkpointContext'),
        entry.get('id'),
    ]

    paper_id = next((candidate for candidate in (_extract_paper_id(source) for source in paper_id_sources) if candidate is not None), None)
    candidate_id = next((candidate for candidate in (_extract_candidate_id(source) for source in candidate_id_sources) if candidate), '')
    return paper_id, candidate_id


def _normalize_catalog_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    clone = dict(entry)
    paper_id, candidate_id = _extract_paper_candidate_refs(clone)
    if paper_id is not None and clone.get('paperId') is None:
        clone['paperId'] = paper_id
    if candidate_id and not clone.get('candidateId'):
        clone['candidateId'] = candidate_id
    for key, value in _build_catalog_narrative_evidence(clone).items():
        if clone.get(key) is None or (isinstance(clone.get(key), str) and not str(clone.get(key)).strip()):
            clone[key] = value
    return clone


def _has_expression(value: Any) -> bool:
    text = str(value or '').strip()
    if not text:
        return False
    return text.lower() not in {'false', '0', 'none', 'null'}


def _infer_candidate_side(candidate: dict[str, Any]) -> str:
    strategy_payload = candidate.get('strategy_payload')
    if isinstance(strategy_payload, dict):
        long_payload = strategy_payload.get('long')
        short_payload = strategy_payload.get('short')
        long_open = _has_expression((long_payload or {}).get('openIf')) if isinstance(long_payload, dict) else False
        short_open = _has_expression((short_payload or {}).get('openIf')) if isinstance(short_payload, dict) else False
        if long_open and short_open:
            return 'both'
        if long_open:
            return 'long'
        if short_open:
            return 'short'

    resolved_params = candidate.get('resolved_strategy_params')
    if isinstance(resolved_params, dict):
        long_open = _has_expression(resolved_params.get('open_long_condition'))
        short_open = _has_expression(resolved_params.get('open_short_condition'))
        if long_open and short_open:
            return 'both'
        if long_open:
            return 'long'
        if short_open:
            return 'short'

    raw_side = str(candidate.get('side') or '').strip().lower()
    if raw_side in {'long', 'short', 'both'}:
        return raw_side
    return 'both'


def _timeframe_minutes(timeframe: str) -> int | None:
    normalized = str(timeframe or '').strip().upper()
    direct = {
        'M1': 1,
        'M5': 5,
        'M15': 15,
        'M30': 30,
        'H1': 60,
        'H4': 240,
        'D1': 1440,
    }
    return direct.get(normalized)


def _round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _normalize_whitespace(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    return WHITESPACE_PATTERN.sub(' ', text).strip()


def _clip_text(text: str, limit: int = 360) -> str:
    normalized = _normalize_whitespace(text)
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3].rstrip()}..."


def _extract_scientific_record_paper_id(path: Path) -> int | None:
    match = SCIENTIFIC_RECORD_PAPER_ID_PATTERN.search(path.name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _index_latest_scientific_records(research_root: Path) -> dict[int, Path]:
    latest_by_paper: dict[int, Path] = {}
    for record_path in research_root.rglob('paper*_scientific_record_payload_*.json'):
        if record_path.suffix != '.json':
            continue
        paper_id = _extract_scientific_record_paper_id(record_path)
        if paper_id is None:
            continue
        current_latest = latest_by_paper.get(paper_id)
        if current_latest is None or record_path.stat().st_mtime > current_latest.stat().st_mtime:
            latest_by_paper[paper_id] = record_path
    return latest_by_paper


def _coerce_text_block(value: Any) -> str:
    if isinstance(value, str):
        return _normalize_whitespace(value)
    if isinstance(value, (list, dict)):
        try:
            return _normalize_whitespace(json.dumps(value, ensure_ascii=False))
        except Exception:
            return ''
    return _normalize_whitespace(value)


def _collect_scientific_record_segments(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []

    segments: list[str] = []
    summary = _coerce_text_block(payload.get('summary'))
    if summary:
        segments.append(summary)

    article = payload.get('article')
    if not isinstance(article, dict):
        return segments

    conclusion = article.get('conclusion')
    if isinstance(conclusion, dict):
        for key in ('summary', 'status'):
            text = _coerce_text_block(conclusion.get(key))
            if text:
                segments.append(text)

    experimental_log = article.get('experimental_log')
    if isinstance(experimental_log, list):
        for item in experimental_log:
            if not isinstance(item, dict):
                continue
            text = _coerce_text_block(item.get('summary'))
            if text:
                segments.append(text)

    sections = article.get('sections')
    if isinstance(sections, list):
        for item in sections:
            if not isinstance(item, dict):
                continue
            title = _coerce_text_block(item.get('title'))
            content = _coerce_text_block(item.get('content'))
            combined = ' '.join(bit for bit in (title, content) if bit)
            if combined:
                segments.append(combined)

    return [segment for segment in segments if segment]


def _find_keyword_snippet(segments: list[str], keywords: tuple[str, ...]) -> str:
    for segment in segments:
        lowered = segment.lower()
        if any(keyword in lowered for keyword in keywords):
            return _clip_text(segment)
    return ''


def _text_matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _build_text_forward_evidence(segments: list[str]) -> dict[str, Any]:
    if not segments:
        return {}
    combined_text = ' '.join(segments)
    combined_lower = combined_text.lower()
    held_out_summary = _find_keyword_snippet(segments, HELD_OUT_KEYWORDS)
    walk_forward_summary = _find_keyword_snippet(segments, WALK_FORWARD_KEYWORDS)
    cost_summary = _find_keyword_snippet(segments, COST_KEYWORDS)

    held_out_available = bool(held_out_summary)
    walk_forward_available = bool(walk_forward_summary)

    return {
        'heldOutEvidenceAvailable': held_out_available,
        'heldOutSummary': held_out_summary,
        'heldOutPassed': held_out_available and _text_matches_any(combined_lower, HELD_OUT_PASS_PATTERNS),
        'walkForwardEvidenceAvailable': walk_forward_available,
        'walkForwardSummary': walk_forward_summary,
        'walkForwardPassed': walk_forward_available and _text_matches_any(combined_lower, WALK_FORWARD_PASS_PATTERNS),
        'costValidationAvailable': bool(cost_summary),
        'costValidationSummary': cost_summary,
    }


def _build_scientific_record_evidence(record_payload: dict[str, Any] | None) -> dict[str, Any]:
    segments = _collect_scientific_record_segments(record_payload)
    if not segments:
        return {}

    evidence = _build_text_forward_evidence(segments)
    evidence['scientificRecordSummary'] = _clip_text(segments[0])
    return evidence


def _build_catalog_narrative_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    narrative_segments = [
        _coerce_text_block(entry.get('positiveCheckpoint')),
        _coerce_text_block(entry.get('takeaway')),
        _coerce_text_block(entry.get('cadenceNote')),
        _coerce_text_block(entry.get('operatorVerdict')),
        _coerce_text_block(entry.get('checkpointContext')),
    ]
    return {
        key: value
        for key, value in _build_text_forward_evidence(
            [segment for segment in narrative_segments if segment]
        ).items()
        if value not in (None, '')
    }


def _format_pip_value(value: float | None) -> str:
    if value is None:
        return ''
    return f'{value:.2f}'.rstrip('0').rstrip('.')


def _build_cost_validation_fields(
    backtest_params: dict[str, Any] | None,
    scientific_record_payload: dict[str, Any] | None,
    *,
    net_pnl: float | None,
) -> dict[str, Any]:
    params = backtest_params if isinstance(backtest_params, dict) else {}
    cost_values = {key: _coerce_number(params.get(key)) for key in COST_PARAMETER_KEYS}
    has_configured_costs = any(value is not None for value in cost_values.values())

    summary_bits: list[str] = []
    spread = cost_values.get('spread_in_pips')
    if spread is not None:
        summary_bits.append(f'spread {_format_pip_value(spread)} pip')

    entry_slippage = cost_values.get('entry_slippage_in_pips')
    close_slippage = cost_values.get('close_slippage_in_pips')
    if entry_slippage is not None or close_slippage is not None:
        summary_bits.append(
            'entry/close '
            f"{_format_pip_value(entry_slippage) or 'n/a'}/{_format_pip_value(close_slippage) or 'n/a'} pip"
        )

    tp_slippage = cost_values.get('take_profit_slippage_in_pips')
    sl_slippage = cost_values.get('stop_loss_slippage_in_pips')
    trailing_slippage = cost_values.get('trailing_stop_slippage_in_pips')
    if tp_slippage is not None or sl_slippage is not None or trailing_slippage is not None:
        summary_bits.append(
            'tp/sl/trail '
            f"{_format_pip_value(tp_slippage) or 'n/a'}/"
            f"{_format_pip_value(sl_slippage) or 'n/a'}/"
            f"{_format_pip_value(trailing_slippage) or 'n/a'} pip"
        )

    volatility_multiplier = cost_values.get('volatility_slippage_multiplier')
    if volatility_multiplier is not None:
        summary_bits.append(f'vol x{_format_pip_value(volatility_multiplier)}')

    explicit_summary = ''
    if summary_bits:
        explicit_summary = f"Replay cost bundle: {', '.join(summary_bits)}."

    record_segments = _collect_scientific_record_segments(scientific_record_payload)
    record_cost_summary = _find_keyword_snippet(record_segments, COST_KEYWORDS)

    return {
        'costValidationAvailable': bool(explicit_summary or record_cost_summary),
        'costValidationSummary': explicit_summary or record_cost_summary,
        'costConfigured': has_configured_costs,
        'costValidated': bool(has_configured_costs and net_pnl is not None and net_pnl > 0),
    }


def _format_completed_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def _build_positive_checkpoint(summary: dict[str, Any], winner_count: int) -> str:
    net_pnl = _coerce_number(summary.get('net_pnl'))
    monthly = _coerce_number(summary.get('monthly_projection'))
    trades = int(summary.get('n_trades') or winner_count or 0)
    bits: list[str] = []
    if net_pnl is not None:
        bits.append(f'~ {net_pnl:+.2f} net')
    if monthly is not None:
        bits.append(f'~ {monthly:+.2f} per month')
    if trades > 0:
        bits.append(f'{trades} trades')
    return ' / '.join(bits) or f'{trades} trades'


def _build_takeaway(summary: dict[str, Any], payload_status: str, lane_name: str) -> str:
    net_pnl = _coerce_number(summary.get('net_pnl'))
    trades = int(summary.get('n_trades') or 0)
    status_label = 'completed' if payload_status == 'completed' else 'running'
    if net_pnl is None:
        return f'{status_label.capitalize()} lane winner from `{lane_name}` with {trades} trades.'
    return (
        f'{status_label.capitalize()} lane winner from `{lane_name}` with '
        f'{net_pnl:+.2f} net over {trades} trades.'
    )


def _build_checkpoint_context(candidate: dict[str, Any], paper_id: Any) -> str:
    candidate_id = str(candidate.get('candidate_id') or candidate.get('candidate_key') or '').strip()
    premise = str(candidate.get('premise') or '').strip()
    label = str(candidate.get('label') or '').strip()
    prefix = f'Paper {paper_id}'
    if candidate_id:
        prefix = f'{prefix} row `{candidate_id}`'
    if premise:
        return f'{prefix}: {premise}'
    if label:
        return f'{prefix}: {label}'
    return prefix


def _build_artifact_positive_entry(
    *,
    repo_root: Path,
    lane_name: str,
    study_path: Path,
    payload: dict[str, Any],
    candidate: dict[str, Any],
    scientific_record_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    label = str(candidate.get('label') or '').strip()
    study = str(payload.get('title') or '').strip()
    symbol = str(candidate.get('symbol') or '').strip()
    timeframe = str(candidate.get('timeframe') or '').strip()
    if not label or not study or not symbol or not timeframe:
        return None

    side = _infer_candidate_side(candidate)
    contextual_key = _build_contextual_registry_key(label, study, symbol, timeframe, side)
    summary = candidate.get('candidate_summary') if isinstance(candidate.get('candidate_summary'), dict) else {}
    n_trades = int(summary.get('n_trades') or 0)
    bars = int(candidate.get('bars') or payload.get('backtest_params', {}).get('bars') or 0)
    trades_per_day = _coerce_number(summary.get('trades_per_day'))
    net_pnl = _coerce_number(summary.get('net_pnl'))
    monthly_projection = _coerce_number(summary.get('monthly_projection'))
    win_rate = _coerce_number(summary.get('win_rate'))
    expectancy_per_trade = _coerce_number(summary.get('expectancy_per_trade'))
    max_drawdown = _coerce_number(summary.get('max_drawdown'))
    max_drawdown_pct = _coerce_number(summary.get('max_drawdown_pct'))
    gross_profit = _coerce_number(summary.get('gross_profit'))
    gross_loss = _coerce_number(summary.get('gross_loss'))
    profit_factor = _coerce_number(summary.get('profit_factor'))
    timeframe_minutes = _timeframe_minutes(timeframe)
    candles_per_trade = _round_or_none((bars / n_trades) if bars > 0 and n_trades > 0 else None)
    hours_per_trade = _round_or_none(
        ((candles_per_trade or 0.0) * timeframe_minutes) / 60.0
        if candles_per_trade is not None and timeframe_minutes
        else None
    )
    days_per_trade = _round_or_none(1.0 / trades_per_day if trades_per_day and trades_per_day > 0 else None)
    payload_status = str(payload.get('status') or '').strip().lower() or 'completed'
    completed_at = _format_completed_at(study_path)
    candidate_id = str(candidate.get('candidate_id') or candidate.get('candidate_key') or '').strip()
    paper_id = payload.get('paper_id')
    classification = 'promoted' if payload_status == 'completed' else 'checkpoint_positive'
    operator_verdict = (
        f'Winner candidate discovered in {payload_status} lane study `{lane_name}`'
        if lane_name
        else f'Winner candidate discovered in {payload_status} lane study'
    )
    scientific_record_evidence = _build_scientific_record_evidence(scientific_record_payload)
    cost_validation_fields = _build_cost_validation_fields(
        payload.get('backtest_params'),
        scientific_record_payload,
        net_pnl=net_pnl,
    )
    return {
        'id': f'paper{paper_id}-{candidate_id or "winner"}-{lane_name}',
        'label': label,
        'study': study,
        'family': str(candidate.get('family') or study).strip(),
        'classification': classification,
        'operatorVerdict': operator_verdict,
        'symbol': symbol,
        'timeframe': timeframe,
        'side': side,
        'positiveCheckpoint': _build_positive_checkpoint(summary, n_trades),
        'checkpointContext': _build_checkpoint_context(candidate, paper_id),
        'completedAt': completed_at,
        'candlesEvaluated': bars or None,
        'trades': n_trades or None,
        'candlesPerTrade': candles_per_trade,
        'hoursPerTrade': hours_per_trade,
        'daysPerTrade': days_per_trade,
        'tradesPerDay': _round_or_none(trades_per_day),
        'expectedMonthlyPercent': _round_or_none(monthly_projection, 2),
        'netPnl': _round_or_none(net_pnl, 4),
        'monthlyProjection': _round_or_none(monthly_projection, 4),
        'winRate': _round_or_none(win_rate, 4),
        'expectancyPerTrade': _round_or_none(expectancy_per_trade, 4),
        'maxDrawdown': _round_or_none(max_drawdown, 4),
        'maxDrawdownPct': _round_or_none(max_drawdown_pct, 4),
        'grossProfit': _round_or_none(gross_profit, 4),
        'grossLoss': _round_or_none(gross_loss, 4),
        'profitFactor': _round_or_none(profit_factor, 4),
        'takeaway': _build_takeaway(summary, payload_status, lane_name),
        'evidenceRefs': DEFAULT_EVIDENCE_REFS,
        'cadenceNote': (
            f'Cadence comes from {payload_status} paper {paper_id} on {symbol} {timeframe}, '
            f'where this winner candidate closed with {n_trades} trades.'
            if n_trades > 0
            else f'Cadence summary is not available for paper {paper_id}.'
        ),
        'sharedRegistryKey': contextual_key,
        'sharedSourceLanes': [lane_name],
        'sharedPublishedAt': completed_at,
        'sharedSourceRepo': str(repo_root),
        'paperId': paper_id,
        'candidateId': candidate_id,
        'laneStatus': payload_status,
        **scientific_record_evidence,
        **cost_validation_fields,
    }


def _extract_artifact_positive_entries(
    repo_root: Path,
    *,
    recent_study_limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    research_root = repo_root / RESEARCH_DATA_RELATIVE_DIR
    if not research_root.is_dir():
        return [], []

    lane_name = _lane_name_for_root(repo_root)
    entries: list[dict[str, Any]] = []
    timestamps: list[str] = []
    latest_stage1_by_study: dict[Path, Path] = {}
    latest_scientific_record_by_paper = _index_latest_scientific_records(research_root)
    for study_path in research_root.rglob('*stage1*.json'):
        if study_path.suffix != '.json':
            continue
        study_dir = study_path.parent
        current_latest = latest_stage1_by_study.get(study_dir)
        if current_latest is None or study_path.stat().st_mtime > current_latest.stat().st_mtime:
            latest_stage1_by_study[study_dir] = study_path

    candidate_paths = list(latest_stage1_by_study.values())
    candidate_paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    if recent_study_limit is not None and recent_study_limit > 0:
        candidate_paths = candidate_paths[:recent_study_limit]

    for target_path in candidate_paths:
        payload = _safe_read_json(target_path)
        if not payload:
            continue
        scientific_record_payload = _safe_read_json(
            latest_scientific_record_by_paper.get(int(payload.get('paper_id') or 0), Path(''))
        ) if payload.get('paper_id') else None
        winner_count = int(payload.get('winner_candidate_count') or 0)
        if winner_count <= 0:
            continue
        winner_candidates = payload.get('winner_candidates')
        if not isinstance(winner_candidates, list) or not winner_candidates:
            continue
        timestamps.append(_format_completed_at(target_path))
        for candidate in winner_candidates:
            if not isinstance(candidate, dict):
                continue
            entry = _build_artifact_positive_entry(
                repo_root=repo_root,
                lane_name=lane_name,
                study_path=target_path,
                payload=payload,
                candidate=candidate,
                scientific_record_payload=scientific_record_payload,
            )
            if entry is not None:
                entries.append(entry)
    return entries, timestamps


def _merge_shared_source_lanes(left: Any, right: Any) -> list[str]:
    merged: list[str] = []
    for raw_list in (left, right):
        if not isinstance(raw_list, list):
            continue
        for item in raw_list:
            text = str(item or '').strip()
            if text and text not in merged:
                merged.append(text)
    return merged


def _prefer_value(primary: Any, fallback: Any) -> Any:
    if primary is None:
        return fallback
    if isinstance(primary, str) and not primary.strip():
        return fallback
    if isinstance(primary, list) and not primary:
        return fallback
    return primary


def _merge_catalog_entries(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key == 'sharedSourceLanes':
            merged[key] = _merge_shared_source_lanes(existing.get(key), value)
            continue
        if key in {'sharedPublishedAt', 'completedAt'}:
            current = str(existing.get(key) or '').strip()
            candidate = str(value or '').strip()
            if candidate and (not current or candidate > current):
                merged[key] = candidate
            continue
        merged[key] = _prefer_value(existing.get(key), value)
        if merged[key] is existing.get(key):
            continue
    return merged


def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    completed_at = str(entry.get('completedAt') or entry.get('sharedPublishedAt') or '')
    label = str(entry.get('label') or '')
    return completed_at, label


def _entry_registry_aliases(entry: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    entry_id = str(entry.get('id') or '').strip()
    if entry_id:
        aliases.append(f'id:{entry_id}')

    shared_registry_key = str(entry.get('sharedRegistryKey') or '').strip()
    if shared_registry_key:
        aliases.append(shared_registry_key)

    label = str(entry.get('label') or '').strip()
    study = str(entry.get('study') or '').strip()
    symbol = str(entry.get('symbol') or '').strip()
    timeframe = str(entry.get('timeframe') or '').strip()
    side = str(entry.get('side') or '').strip()
    aliases.append(_build_contextual_registry_key(label, study, symbol, timeframe, side))

    paper_id, candidate_id = _extract_paper_candidate_refs(entry)
    if paper_id is not None and candidate_id:
        aliases.append(f'paper:{paper_id}:candidate:{candidate_id}')

    return [alias for index, alias in enumerate(aliases) if alias and alias not in aliases[:index]]


def _merge_catalogs(*catalog_collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    alias_to_index: dict[str, int] = {}
    for catalog in catalog_collections:
        for entry in catalog:
            if not isinstance(entry, dict):
                continue
            aliases = _entry_registry_aliases(entry)
            existing_index = next((alias_to_index[alias] for alias in aliases if alias in alias_to_index), None)
            if existing_index is not None:
                merged[existing_index] = _merge_catalog_entries(merged[existing_index], entry)
                for alias in aliases:
                    alias_to_index[alias] = existing_index
                continue

            clone = dict(entry)
            if aliases:
                clone['sharedRegistryKey'] = aliases[0]
            next_index = len(merged)
            merged.append(clone)
            for alias in aliases:
                alias_to_index[alias] = next_index
    return sorted(merged, key=_entry_sort_key, reverse=True)


def _load_published_shared_payload(shared_root: Path) -> tuple[list[dict[str, Any]], str, str]:
    payload_path = shared_root / SHARED_POSITIVE_HISTORY_DIRNAME / SHARED_PUBLISHED_CATALOG_FILENAME
    if not payload_path.is_file():
        return [], '', ''

    payload = _safe_read_json(payload_path)
    if not payload:
        return [], '', ''

    catalog = payload.get('catalog')
    if not isinstance(catalog, list):
        catalog = []
    normalized_catalog: list[dict[str, Any]] = []
    for entry in catalog:
        normalized_entry = _normalize_catalog_entry(entry)
        if normalized_entry is not None:
            normalized_catalog.append(normalized_entry)

    last_updated = payload.get('lastUpdated') if isinstance(payload.get('lastUpdated'), str) else ''
    shared_registry_last_updated = (
        payload.get('sharedRegistryLastUpdated')
        if isinstance(payload.get('sharedRegistryLastUpdated'), str)
        else ''
    )
    return normalized_catalog, last_updated, shared_registry_last_updated


def load_shared_positive_history_payload() -> dict:
    shared_root = resolve_shared_positive_history_root()
    lane_roots = discover_lane_roots()
    fingerprint = _build_positive_history_payload_fingerprint(shared_root, lane_roots)

    with _SHARED_POSITIVE_HISTORY_PAYLOAD_CACHE_LOCK:
        cached_fingerprint = str(_SHARED_POSITIVE_HISTORY_PAYLOAD_CACHE.get('fingerprint') or '')
        cached_payload = _SHARED_POSITIVE_HISTORY_PAYLOAD_CACHE.get('payload')
        if cached_fingerprint == fingerprint and isinstance(cached_payload, dict):
            return cached_payload

    published_catalog, published_last_updated, published_registry_last_updated = _load_published_shared_payload(shared_root)

    lane_local_catalogs: list[list[dict[str, Any]]] = []
    lane_catalog_timestamps: list[str] = []
    artifact_catalogs: list[list[dict[str, Any]]] = []
    artifact_timestamps: list[str] = []
    for lane_root in lane_roots:
        if _should_load_lane_catalog_for_root(lane_root):
            lane_local_catalog, lane_catalog_last_updated = _load_lane_local_catalog(lane_root)
            if lane_local_catalog:
                lane_local_catalogs.append(lane_local_catalog)
            if lane_catalog_last_updated:
                lane_catalog_timestamps.append(lane_catalog_last_updated)
        if not _should_scan_artifacts_for_root(lane_root):
            continue
        recent_limit = (
            DEFAULT_RECENT_ARTIFACT_SCAN_LIMIT
            if _should_load_lane_catalog_for_root(lane_root) or lane_root.resolve() == REPO_ROOT.resolve()
            else None
        )
        lane_catalog, lane_timestamps = _extract_artifact_positive_entries(
            lane_root,
            recent_study_limit=recent_limit,
        )
        if lane_catalog:
            artifact_catalogs.append(lane_catalog)
        artifact_timestamps.extend(lane_timestamps)

    merged_catalog = _merge_catalogs(published_catalog, *lane_local_catalogs, *artifact_catalogs)
    timestamp_candidates = [
        published_last_updated,
        published_registry_last_updated,
        *lane_catalog_timestamps,
        *artifact_timestamps,
        *[
            str(entry.get('sharedPublishedAt') or entry.get('completedAt') or '')
            for entry in merged_catalog
        ],
    ]
    timestamp_candidates = [value for value in timestamp_candidates if isinstance(value, str) and value.strip()]
    latest_timestamp = max(timestamp_candidates) if timestamp_candidates else ''

    payload = {
        'lastUpdated': latest_timestamp,
        'sharedRegistryLastUpdated': published_registry_last_updated or latest_timestamp,
        'catalog': merged_catalog,
        'entryCount': len(merged_catalog),
    }

    with _SHARED_POSITIVE_HISTORY_PAYLOAD_CACHE_LOCK:
        _SHARED_POSITIVE_HISTORY_PAYLOAD_CACHE['fingerprint'] = fingerprint
        _SHARED_POSITIVE_HISTORY_PAYLOAD_CACHE['payload'] = payload

    return payload

import json
import ast
import operator
import re
from functools import lru_cache
from importlib import import_module
from pathlib import Path


MANIFEST_PATH = Path(__file__).resolve().parents[2] / 'shared' / 'indicatorManifest.json'
BASE_MARKET_COLUMNS = ('time', 'open', 'high', 'low', 'close', 'volume')
RUNTIME_TEMPLATE_PATTERN = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')
_manifest_cache = {
    'signature': None,
    'data': None,
}
_registry_cache = {
    'signature': None,
    'data': None,
}


def _safe_int_param(raw_params, index: int, default: int):
    try:
        return int(raw_params[index])
    except (TypeError, ValueError, IndexError):
        return int(default)


def _build_indicator_field_value_map(name: str, raw_params=None):
    manifest = get_indicator_manifest(name) or {}
    fields = manifest.get('fields') or []
    safe_raw_params = list(raw_params or [])
    values = {}

    for index, field in enumerate(fields):
        raw_value = safe_raw_params[index] if index < len(safe_raw_params) else field.get('defaultValue')
        values[str(field.get('key') or '').strip()] = raw_value

    return values


def _eval_runtime_formula(formula, field_values: dict):
    if formula is None or formula == '':
        return 0

    safe_formula = str(formula).strip()
    if not safe_formula:
        return 0

    allowed_binary = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    allowed_unary = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }
    allowed_functions = {
        'max': max,
        'min': min,
        'abs': abs,
        'int': int,
        'float': float,
        'round': round,
    }

    def coerce_value(value):
        if isinstance(value, (int, float)):
            return value

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value

        if numeric.is_integer():
            return int(numeric)
        return numeric

    normalized_values = {
        key: coerce_value(value)
        for key, value in (field_values or {}).items()
    }

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in normalized_values:
                raise ValueError(f'Unknown runtime formula field "{node.id}"')
            return normalized_values[node.id]
        if isinstance(node, ast.BinOp):
            operator_fn = allowed_binary.get(type(node.op))
            if operator_fn is None:
                raise ValueError('Unsupported runtime formula operator')
            return operator_fn(evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.UnaryOp):
            operator_fn = allowed_unary.get(type(node.op))
            if operator_fn is None:
                raise ValueError('Unsupported runtime formula unary operator')
            return operator_fn(evaluate(node.operand))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in allowed_functions:
                raise ValueError('Unsupported runtime formula function')
            return allowed_functions[node.func.id](*(evaluate(argument) for argument in node.args))
        raise ValueError('Unsupported runtime formula expression')

    parsed = ast.parse(safe_formula, mode='eval')
    return evaluate(parsed)


def _resolve_runtime_template_string(template: str, field_values: dict):
    safe_template = str(template or '').strip()
    if not safe_template:
        return ''

    def replace(match):
        field_name = str(match.group(1) or '').strip()
        value = field_values.get(field_name, '')
        return str(value).strip()

    return RUNTIME_TEMPLATE_PATTERN.sub(replace, safe_template)


def _resolve_manifest_runtime_value(value, field_values: dict):
    if isinstance(value, dict):
        if 'formula' in value:
            return _eval_runtime_formula(value.get('formula'), field_values)
        return {
            key: _resolve_manifest_runtime_value(inner_value, field_values)
            for key, inner_value in value.items()
        }

    if isinstance(value, list):
        return [
            _resolve_manifest_runtime_value(item, field_values)
            for item in value
        ]

    if isinstance(value, str):
        return _resolve_runtime_template_string(value, field_values)

    return value


def _build_legacy_indicator_runtime_contract(indicator_name: str, raw_params=None):
    safe_name = get_indicator_canonical_name(indicator_name).strip().upper()
    safe_params = list(raw_params or [])
    incremental_mode = get_indicator_incremental_mode(safe_name)
    contract = {
        'indicator_name': get_indicator_canonical_name(safe_name),
        'incremental_mode': incremental_mode,
        'supports_partial_rebuild': incremental_mode == 'rolling_window',
        'requires_full_rebuild': incremental_mode != 'rolling_window',
        'warmup_bars': 0,
        'patch_bars': 0,
        'output_layer': 'derived_indicator',
        'input_layer': 'market_data',
        'input_columns': ['open', 'high', 'low', 'close', 'volume'],
    }

    if safe_name == 'SMA':
        period = max(1, _safe_int_param(safe_params, 1, 20))
        contract['warmup_bars'] = period - 1
        contract['input_columns'] = [str(safe_params[0] if len(safe_params) >= 1 else 'close')]
    elif safe_name == 'EMA':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['input_columns'] = [str(safe_params[0] if len(safe_params) >= 1 else 'close')]
    elif safe_name == 'BOLLINGERBANDS':
        period = max(1, _safe_int_param(safe_params, 1, 20))
        contract['warmup_bars'] = period - 1
        contract['input_columns'] = [str(safe_params[0] if len(safe_params) >= 1 else 'close')]
    elif safe_name == 'MOMENTUM':
        period = max(1, _safe_int_param(safe_params, 1, 10))
        contract['warmup_bars'] = period
        contract['input_columns'] = [str(safe_params[0] if len(safe_params) >= 1 else 'close')]
    elif safe_name == 'ROC':
        period = max(1, _safe_int_param(safe_params, 1, 10))
        contract['warmup_bars'] = period
        contract['input_columns'] = [str(safe_params[0] if len(safe_params) >= 1 else 'close')]
    elif safe_name == 'RSI':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['input_columns'] = [str(safe_params[0] if len(safe_params) >= 1 else 'close')]
        contract['output_layer'] = 'momentum_indicator'
    elif safe_name == 'MACD':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['input_columns'] = [str(safe_params[0] if len(safe_params) >= 1 else 'close')]
        contract['output_layer'] = 'trend_indicator'
    elif safe_name == 'ADX':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['output_layer'] = 'trend_indicator'
    elif safe_name == 'ATR':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['output_layer'] = 'volatility_indicator'
    elif safe_name == 'STOCHASTIC':
        period = max(1, _safe_int_param(safe_params, 0, 14))
        smooth_k = max(1, _safe_int_param(safe_params, 1, 3))
        smooth_d = max(1, _safe_int_param(safe_params, 2, 3))
        contract['warmup_bars'] = (period - 1) + (smooth_k - 1) + (smooth_d - 1)
        contract['output_layer'] = 'momentum_indicator'
    elif safe_name == 'ICHIMOKOCLOUDS':
        tenkan_period = max(1, _safe_int_param(safe_params, 0, 9))
        kijun_period = max(1, _safe_int_param(safe_params, 1, 26))
        senkou_b_period = max(1, _safe_int_param(safe_params, 2, 52))
        contract['warmup_bars'] = max(tenkan_period - 1, kijun_period - 1, senkou_b_period - 1) + kijun_period
        contract['patch_bars'] = kijun_period
        contract['output_layer'] = 'structure_indicator'
    elif safe_name == 'DONCHIANCHANNELS':
        period = max(1, _safe_int_param(safe_params, 0, 20))
        contract['requires_full_rebuild'] = False
        contract['supports_partial_rebuild'] = True
        contract['warmup_bars'] = period - 1
        contract['output_layer'] = 'structure_indicator'
    elif safe_name == 'VWAP':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['output_layer'] = 'execution_indicator'
        contract['input_columns'] = ['open', 'high', 'low', 'close', 'volume']
    elif safe_name == 'KELTNERCHANNELS':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['input_columns'] = [str(safe_params[0] if len(safe_params) >= 1 else 'close'), 'high', 'low', 'close']
        contract['output_layer'] = 'volatility_indicator'
    elif safe_name == 'CHOPPINESSINDEX':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['output_layer'] = 'regime_indicator'
    elif safe_name == 'SUPERTREND':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['output_layer'] = 'trend_indicator'
    elif safe_name == 'MARKETREGIME':
        contract['requires_full_rebuild'] = True
        contract['supports_partial_rebuild'] = False
        contract['output_layer'] = 'regime_indicator'

    return contract


def _merge_runtime_contract_defaults(indicator_name: str, raw_params=None, manifest_contract=None):
    safe_name = get_indicator_canonical_name(indicator_name).strip().upper()
    safe_params = list(raw_params or [])
    incremental_mode = get_indicator_incremental_mode(safe_name)
    base_contract = {
        'indicator_name': get_indicator_canonical_name(safe_name),
        'incremental_mode': incremental_mode,
        'supports_partial_rebuild': incremental_mode == 'rolling_window',
        'requires_full_rebuild': incremental_mode != 'rolling_window',
        'warmup_bars': 0,
        'patch_bars': 0,
        'output_layer': 'derived_indicator',
        'input_layer': 'market_data',
        'input_columns': ['open', 'high', 'low', 'close', 'volume'],
    }

    field_values = _build_indicator_field_value_map(indicator_name, safe_params)
    safe_manifest_contract = _resolve_manifest_runtime_value(dict(manifest_contract or {}), field_values)
    contract = {
        **base_contract,
        **safe_manifest_contract,
    }

    contract['indicator_name'] = get_indicator_canonical_name(safe_name)
    contract['incremental_mode'] = str(contract.get('incremental_mode') or incremental_mode).strip().lower()
    contract['supports_partial_rebuild'] = bool(contract.get('supports_partial_rebuild'))
    contract['requires_full_rebuild'] = bool(contract.get('requires_full_rebuild'))
    contract['warmup_bars'] = max(0, int(contract.get('warmup_bars', 0) or 0))
    contract['patch_bars'] = max(0, int(contract.get('patch_bars', 0) or 0))
    contract['output_layer'] = str(contract.get('output_layer') or 'derived_indicator').strip() or 'derived_indicator'
    contract['input_layer'] = str(contract.get('input_layer') or 'market_data').strip() or 'market_data'
    contract['input_columns'] = [
        str(column).strip()
        for column in (contract.get('input_columns') or [])
        if str(column).strip()
    ] or ['open', 'high', 'low', 'close', 'volume']

    if contract['supports_partial_rebuild']:
        contract['requires_full_rebuild'] = False

    return contract


def load_indicator_manifest():
    signature = None
    try:
        stat = MANIFEST_PATH.stat()
        signature = (str(MANIFEST_PATH), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        signature = (str(MANIFEST_PATH), None, None)

    if _manifest_cache['signature'] != signature:
        with MANIFEST_PATH.open('r', encoding='utf-8') as file:
            _manifest_cache['data'] = json.load(file)
        _manifest_cache['signature'] = signature
        _registry_cache['signature'] = None
        _registry_cache['data'] = None

    return _manifest_cache['data']


def get_indicator_registry():
    manifest = load_indicator_manifest()
    signature = _manifest_cache['signature']

    if _registry_cache['signature'] != signature:
        registry = {}

        for entry in manifest:
            import_path = str(entry.get('pythonImport', '')).strip()
            name = str(entry.get('name', '')).strip().upper()

            if not import_path or not name:
                continue

            module_path, class_name = import_path.rsplit('.', 1)
            try:
                module = import_module(module_path)
            except ModuleNotFoundError as error:
                if module_path.startswith('python.') and getattr(error, 'name', '') == 'python':
                    module = import_module(f'backend.{module_path}')
                else:
                    raise
            registry[name] = {
                'class': getattr(module, class_name),
                'manifest': entry,
            }

        _registry_cache['data'] = registry
        _registry_cache['signature'] = signature

    return _registry_cache['data']


def clear_indicator_registry_cache():
    _manifest_cache['signature'] = None
    _manifest_cache['data'] = None
    _registry_cache['signature'] = None
    _registry_cache['data'] = None


def get_indicator_entry(name: str):
    return get_indicator_registry().get(str(name or '').strip().upper())


def get_indicator_manifest(name: str):
    indicator_entry = get_indicator_entry(name)
    return indicator_entry.get('manifest') if indicator_entry else None


def get_indicator_canonical_name(name: str):
    manifest = get_indicator_manifest(name)
    if manifest:
        return str(manifest.get('name', '')).strip()

    return str(name or '').strip()


def get_indicator_class(name: str):
    indicator_entry = get_indicator_entry(name)
    return indicator_entry.get('class') if indicator_entry else None


def get_indicator_incremental_mode(name: str):
    manifest = get_indicator_manifest(name)
    return str(manifest.get('incrementalMode', '')).strip().lower() if manifest else ''


def get_indicator_column_param_indexes(name: str):
    manifest = get_indicator_manifest(name)

    if not manifest:
        return []

    indexes = manifest.get('columnParamIndexes')
    if isinstance(indexes, list):
        return [int(index) for index in indexes]

    fields = manifest.get('fields') or []
    return list(range(len(fields)))


def get_indicator_field_count(name: str):
    manifest = get_indicator_manifest(name)
    return len(manifest.get('fields') or []) if manifest else 0


def _coalesce_indicator_param_parts(name: str, raw_parts):
    manifest = get_indicator_manifest(name) or {}
    fields = manifest.get('fields') or []
    safe_parts = [str(part).strip() for part in (raw_parts or []) if str(part).strip() != '']

    if not fields:
        return list(safe_parts)

    parsed_params = []
    cursor = 0

    for field in fields:
        if cursor >= len(safe_parts):
            return None

        field_type = str(field.get('type') or '').strip().lower()
        if field_type == 'select':
            options = [
                str(option).strip()
                for option in (field.get('options') or [])
                if str(option).strip() != ''
            ]
            option_parts = sorted(
                ((option, option.split('_')) for option in options),
                key=lambda item: len(item[1]),
                reverse=True,
            )

            matched_option = None
            for option, parts in option_parts:
                next_parts = safe_parts[cursor:cursor + len(parts)]
                if next_parts == parts:
                    matched_option = (option, len(parts))
                    break

            if matched_option is not None:
                parsed_params.append(matched_option[0])
                cursor += matched_option[1]
                continue

        parsed_params.append(safe_parts[cursor])
        cursor += 1

    if cursor != len(safe_parts):
        return None

    return parsed_params


def get_indicator_line_catalog(name: str):
    manifest = get_indicator_manifest(name)

    if not manifest:
        return []

    lines = manifest.get('lines')
    if isinstance(lines, list):
        return [dict(line) for line in lines]

    line_catalog = manifest.get('lineCatalog')
    if isinstance(line_catalog, list):
        return [dict(line) for line in line_catalog]

    return []


def get_indicator_line_for_suffix(name: str, suffix: str):
    safe_suffix = str(suffix or '').strip()

    for line in get_indicator_line_catalog(name):
        line_suffix = str(line.get('columnSuffix', line.get('key', ''))).strip()
        if line_suffix == safe_suffix:
            return dict(line)

    return None


def _normalize_indicator_param_fragment(value):
    safe_value = str(value).strip()
    if safe_value == '':
        return ''

    try:
        numeric = float(safe_value)
    except (TypeError, ValueError):
        return safe_value

    if numeric.is_integer():
        return str(int(numeric))

    return str(numeric)


def _build_filled_indicator_params(name: str, raw_params=None):
    manifest = get_indicator_manifest(name) or {}
    fields = manifest.get('fields') or []
    safe_raw_params = list(raw_params or [])

    if not fields:
        return [
            _normalize_indicator_param_fragment(param)
            for param in safe_raw_params
            if str(param).strip() != ''
        ]

    filled = []
    for index, field in enumerate(fields):
        value = safe_raw_params[index] if index < len(safe_raw_params) else field.get('defaultValue')
        if str(value).strip() == '':
            continue
        filled.append(_normalize_indicator_param_fragment(value))

    return filled


def build_indicator_feature_name(indicator_name: str, raw_params=None, line_suffix: str = ''):
    safe_indicator_name = get_indicator_canonical_name(indicator_name)
    safe_raw_params = _build_filled_indicator_params(indicator_name, raw_params)
    safe_line_suffix = str(line_suffix or '').strip()

    if not safe_indicator_name:
        return ''

    parts = [safe_indicator_name, *safe_raw_params]

    if safe_line_suffix:
        parts.extend(safe_line_suffix.split('_'))

    return '_'.join(parts)


@lru_cache(maxsize=1)
def get_indicator_names():
    return tuple(get_indicator_registry().keys())


@lru_cache(maxsize=1)
def get_indicator_names_by_length_desc():
    return tuple(sorted(get_indicator_names(), key=len, reverse=True))


def parse_indicator_feature_name(feature_name: str):
    safe_feature_name = str(feature_name or '').strip()

    if not safe_feature_name:
        return None

    parts = safe_feature_name.split('_')
    normalized_parts = [part.upper() for part in parts]

    for indicator_name in get_indicator_names_by_length_desc():
        indicator_parts = indicator_name.split('_')

        if normalized_parts[:len(indicator_parts)] != indicator_parts:
            continue

        remainder = parts[len(indicator_parts):]
        line_catalog = get_indicator_line_catalog(indicator_name)
        expected_param_count = len(get_indicator_column_param_indexes(indicator_name))

        candidate_lines = sorted(
            line_catalog,
            key=lambda line: len(str(line.get('columnSuffix', line.get('key', ''))).split('_')),
            reverse=True,
        )

        for line in candidate_lines:
            suffix = str(line.get('columnSuffix', line.get('key', ''))).strip()
            suffix_parts = suffix.split('_') if suffix else []

            if suffix_parts:
                if len(remainder) < len(suffix_parts):
                    continue

                if remainder[-len(suffix_parts):] != suffix_parts:
                    continue

                raw_params = remainder[:-len(suffix_parts)]
            else:
                raw_params = remainder

            parsed_raw_params = _coalesce_indicator_param_parts(indicator_name, raw_params)
            if parsed_raw_params is not None and len(parsed_raw_params) <= expected_param_count:
                return {
                    'indicator_name': indicator_name,
                    'raw_params': _build_filled_indicator_params(indicator_name, parsed_raw_params),
                    'line_suffix': suffix,
                    'line': dict(line),
                }

        parsed_remainder = _coalesce_indicator_param_parts(indicator_name, remainder)
        if parsed_remainder is not None and len(parsed_remainder) <= expected_param_count:
            return {
                'indicator_name': indicator_name,
                'raw_params': _build_filled_indicator_params(indicator_name, parsed_remainder),
                'line_suffix': '',
                'line': get_indicator_line_for_suffix(indicator_name, ''),
            }

        field_count = get_indicator_field_count(indicator_name)
        if field_count and parsed_remainder is not None and len(parsed_remainder) <= field_count:
            return {
                'indicator_name': indicator_name,
                'raw_params': _build_filled_indicator_params(indicator_name, parsed_remainder),
                'line_suffix': '',
                'line': get_indicator_line_for_suffix(indicator_name, ''),
            }

    return None


def split_indicator_feature_name(feature_name: str):
    parsed = parse_indicator_feature_name(feature_name)

    if not parsed:
        return None, None

    return parsed['indicator_name'], parsed['raw_params']


def normalize_indicator_feature_name(feature_name: str):
    parsed = parse_indicator_feature_name(feature_name)

    if not parsed:
        return str(feature_name or '').strip()

    return build_indicator_feature_name(
        indicator_name=parsed['indicator_name'],
        raw_params=parsed['raw_params'],
        line_suffix=parsed['line_suffix'],
    )


def describe_indicator_feature_name(feature_name: str):
    parsed = parse_indicator_feature_name(feature_name)

    if not parsed:
        safe_feature_name = str(feature_name or '').strip()
        return {
            'column_name': safe_feature_name,
            'normalized_column_name': safe_feature_name,
            'indicator_name': None,
            'raw_params': [],
            'line_suffix': '',
            'line_key': '',
            'line_label': '',
            'column_layer': classify_symbol_column(safe_feature_name),
            'runtime_contract': None,
        }

    line = parsed.get('line') or {}
    normalized_column_name = build_indicator_feature_name(
        indicator_name=parsed['indicator_name'],
        raw_params=parsed['raw_params'],
        line_suffix=parsed['line_suffix'],
    )

    return {
        'column_name': str(feature_name or '').strip(),
        'normalized_column_name': normalized_column_name,
        'indicator_name': get_indicator_canonical_name(parsed['indicator_name']),
        'raw_params': list(parsed['raw_params']),
        'line_suffix': parsed['line_suffix'],
        'line_key': str(line.get('key', '')).strip(),
        'line_label': str(line.get('label', '')).strip(),
        'column_layer': get_indicator_runtime_contract(parsed['indicator_name'], parsed['raw_params']).get('output_layer'),
        'runtime_contract': get_indicator_runtime_contract(parsed['indicator_name'], parsed['raw_params']),
    }


def describe_indicator_columns(indicator_name: str, raw_params=None, columns=None):
    safe_columns = list(columns or [])
    feature_details = []

    for column_name in safe_columns:
        described = describe_indicator_feature_name(column_name)

        if described.get('indicator_name'):
            feature_details.append(described)
            continue

        feature_details.append({
            'column_name': str(column_name or '').strip(),
            'normalized_column_name': build_indicator_feature_name(indicator_name, raw_params or []),
            'indicator_name': get_indicator_canonical_name(indicator_name),
            'raw_params': list(raw_params or []),
            'line_suffix': '',
            'line_key': '',
            'line_label': '',
            'column_layer': get_indicator_runtime_contract(indicator_name, raw_params or []).get('output_layer'),
            'runtime_contract': get_indicator_runtime_contract(indicator_name, raw_params or []),
        })

    return feature_details


def get_symbol_base_columns():
    return tuple(BASE_MARKET_COLUMNS)


def classify_symbol_column(column_name: str):
    safe_name = str(column_name or '').strip()
    return 'market_data' if safe_name in BASE_MARKET_COLUMNS else 'derived_indicator'


def get_indicator_runtime_contract(indicator_name: str, raw_params=None):
    manifest = get_indicator_manifest(indicator_name) or {}
    manifest_contract = manifest.get('runtimeContract')

    if isinstance(manifest_contract, dict):
        return _merge_runtime_contract_defaults(
            indicator_name=indicator_name,
            raw_params=raw_params,
            manifest_contract=manifest_contract,
        )

    return dict(_build_legacy_indicator_runtime_contract(indicator_name, raw_params))

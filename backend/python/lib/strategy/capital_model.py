import math


SUPPORTED_MARGIN_MODELS = {
    'disabled_legacy',
    'forex_notional',
    'cash_notional',
    'fixed_per_lot',
}

SUPPORTED_VOLUME_MODES = {
    'fixed_volume',
    'max_affordable',
    'base_volume_compounding',
}

DEFAULT_CAPITAL_MODEL_BY_ASSET = {
    'forex': {
        'account_currency': 'USD',
        'contract_size_per_lot': 100_000.0,
        'account_leverage': 50.0,
        'min_lot': 0.01,
        'lot_step': 0.01,
        'max_lot': 100.0,
        'margin_long_rate': None,
        'margin_short_rate': None,
        'margin_per_lot': None,
        'margin_model': 'forex_notional',
        'quote_to_account_conversion_mode': 'assume_quote_equals_account',
    },
    'b3_equity': {
        'account_currency': 'BRL',
        'contract_size_per_lot': 1.0,
        'account_leverage': 1.0,
        'min_lot': 1.0,
        'lot_step': 1.0,
        'max_lot': 1_000_000.0,
        'margin_long_rate': 1.0,
        'margin_short_rate': 1.0,
        'margin_per_lot': None,
        'margin_model': 'cash_notional',
        'quote_to_account_conversion_mode': 'same_currency',
    },
    'b3_option': {
        'account_currency': 'BRL',
        'contract_size_per_lot': 1.0,
        'account_leverage': 1.0,
        'min_lot': 1.0,
        'lot_step': 1.0,
        'max_lot': 1_000_000.0,
        'margin_long_rate': 1.0,
        'margin_short_rate': 1.0,
        'margin_per_lot': None,
        'margin_model': 'cash_notional',
        'quote_to_account_conversion_mode': 'same_currency',
    },
    'b3_term': {
        'account_currency': 'BRL',
        'contract_size_per_lot': 1.0,
        'account_leverage': 1.0,
        'min_lot': 1.0,
        'lot_step': 1.0,
        'max_lot': 1_000_000.0,
        'margin_long_rate': 1.0,
        'margin_short_rate': 1.0,
        'margin_per_lot': None,
        'margin_model': 'cash_notional',
        'quote_to_account_conversion_mode': 'same_currency',
    },
    'b3_mini_future': {
        'account_currency': 'BRL',
        'contract_size_per_lot': 1.0,
        'account_leverage': 1.0,
        'min_lot': 1.0,
        'lot_step': 1.0,
        'max_lot': 500.0,
        'margin_long_rate': None,
        'margin_short_rate': None,
        'margin_per_lot': 500.0,
        'margin_model': 'fixed_per_lot',
        'quote_to_account_conversion_mode': 'same_currency',
    },
}

MINI_FUTURE_MARGIN_PER_LOT_BY_PREFIX = {
    'WIN': 150.0,
    'WDO': 1_000.0,
}


def _coerce_positive_float(value, fallback=None):
    try:
        parsed = float(value)
    except Exception:
        return fallback
    if not math.isfinite(parsed) or parsed <= 0:
        return fallback
    return parsed


def _coerce_optional_rate(value):
    if value is None or value == '':
        return None
    return _coerce_positive_float(value, None)


def _normalize_text(value, fallback=''):
    text = str(value or '').strip()
    return text or fallback


def normalize_volume_mode(value):
    normalized = str(value or '').strip().lower() or 'fixed_volume'
    if normalized not in SUPPORTED_VOLUME_MODES:
        return 'fixed_volume'
    return normalized


def _resolve_asset_defaults(asset_type, symbol=''):
    normalized_asset_type = str(asset_type or '').strip().lower() or 'forex'
    defaults = dict(DEFAULT_CAPITAL_MODEL_BY_ASSET.get(normalized_asset_type) or DEFAULT_CAPITAL_MODEL_BY_ASSET['forex'])
    normalized_symbol = str(symbol or '').strip().upper()
    if normalized_asset_type == 'b3_mini_future':
        for prefix, margin_per_lot in MINI_FUTURE_MARGIN_PER_LOT_BY_PREFIX.items():
            if normalized_symbol.startswith(prefix):
                defaults['margin_per_lot'] = float(margin_per_lot)
                break
    return defaults


def normalize_capital_model(
    raw_model=None,
    *,
    asset_type='forex',
    symbol='',
    initial_balance=10_000.0,
):
    safe_model = dict(raw_model or {})
    defaults = _resolve_asset_defaults(asset_type, symbol=symbol)
    normalized_margin_model = str(
        safe_model.get('marginModel')
        or safe_model.get('margin_model')
        or defaults.get('margin_model')
        or 'disabled_legacy'
    ).strip().lower() or 'disabled_legacy'
    if normalized_margin_model not in SUPPORTED_MARGIN_MODELS:
        normalized_margin_model = defaults.get('margin_model') or 'disabled_legacy'

    normalized = {
        'account_currency': _normalize_text(
            safe_model.get('accountCurrency') or safe_model.get('account_currency'),
            defaults.get('account_currency', 'USD'),
        ).upper(),
        'initial_balance': _coerce_positive_float(
            safe_model.get('initialBalance') or safe_model.get('initial_balance'),
            _coerce_positive_float(initial_balance, 10_000.0),
        ),
        'contract_size_per_lot': _coerce_positive_float(
            safe_model.get('contractSizePerLot') or safe_model.get('contract_size_per_lot'),
            defaults.get('contract_size_per_lot', 1.0),
        ),
        'account_leverage': _coerce_positive_float(
            safe_model.get('accountLeverage') or safe_model.get('account_leverage'),
            defaults.get('account_leverage', 1.0),
        ),
        'min_lot': _coerce_positive_float(
            safe_model.get('minLot') or safe_model.get('min_lot'),
            defaults.get('min_lot', 0.01),
        ),
        'lot_step': _coerce_positive_float(
            safe_model.get('lotStep') or safe_model.get('lot_step'),
            defaults.get('lot_step', 0.01),
        ),
        'max_lot': _coerce_positive_float(
            safe_model.get('maxLot') or safe_model.get('max_lot'),
            defaults.get('max_lot', 100.0),
        ),
        'margin_long_rate': _coerce_optional_rate(
            safe_model.get('marginLongRate') or safe_model.get('margin_long_rate')
        ),
        'margin_short_rate': _coerce_optional_rate(
            safe_model.get('marginShortRate') or safe_model.get('margin_short_rate')
        ),
        'margin_per_lot': _coerce_optional_rate(
            safe_model.get('marginPerLot') or safe_model.get('margin_per_lot')
        ),
        'margin_model': normalized_margin_model,
        'quote_to_account_conversion_mode': _normalize_text(
            safe_model.get('quoteToAccountConversionMode') or safe_model.get('quote_to_account_conversion_mode'),
            defaults.get('quote_to_account_conversion_mode', 'assume_quote_equals_account'),
        ),
        'source': 'custom' if safe_model else 'asset_default',
        'asset_type': str(asset_type or '').strip().lower() or 'forex',
        'symbol': str(symbol or '').strip().upper(),
    }

    if normalized['margin_long_rate'] is None:
        normalized['margin_long_rate'] = defaults.get('margin_long_rate')
    if normalized['margin_short_rate'] is None:
        normalized['margin_short_rate'] = defaults.get('margin_short_rate')
    if normalized['margin_per_lot'] is None:
        normalized['margin_per_lot'] = defaults.get('margin_per_lot')

    if normalized['margin_model'] == 'forex_notional':
        fallback_rate = 1.0 / normalized['account_leverage'] if normalized['account_leverage'] > 0 else 1.0
        normalized['margin_long_rate'] = _coerce_positive_float(normalized['margin_long_rate'], fallback_rate)
        normalized['margin_short_rate'] = _coerce_positive_float(normalized['margin_short_rate'], fallback_rate)
    elif normalized['margin_model'] == 'cash_notional':
        fallback_rate = 1.0 / normalized['account_leverage'] if normalized['account_leverage'] > 0 else 1.0
        normalized['margin_long_rate'] = _coerce_positive_float(normalized['margin_long_rate'], fallback_rate)
        normalized['margin_short_rate'] = _coerce_positive_float(normalized['margin_short_rate'], fallback_rate)
    elif normalized['margin_model'] == 'fixed_per_lot':
        normalized['margin_per_lot'] = _coerce_positive_float(normalized['margin_per_lot'], defaults.get('margin_per_lot', 1.0))
        normalized['margin_long_rate'] = None
        normalized['margin_short_rate'] = None
    else:
        normalized['margin_long_rate'] = None
        normalized['margin_short_rate'] = None
        normalized['margin_per_lot'] = None

    if normalized['max_lot'] < normalized['min_lot']:
        normalized['max_lot'] = normalized['min_lot']

    return normalized


def build_capital_policy(capital_model=None):
    safe_model = dict(capital_model or {})
    return {
        'capital_model_source': safe_model.get('source', 'asset_default'),
        'capital_account_currency': safe_model.get('account_currency'),
        'capital_initial_balance': safe_model.get('initial_balance'),
        'contract_size_per_lot': safe_model.get('contract_size_per_lot'),
        'account_leverage': safe_model.get('account_leverage'),
        'min_lot': safe_model.get('min_lot'),
        'lot_step': safe_model.get('lot_step'),
        'max_lot': safe_model.get('max_lot'),
        'margin_model': safe_model.get('margin_model'),
        'margin_long_rate': safe_model.get('margin_long_rate'),
        'margin_short_rate': safe_model.get('margin_short_rate'),
        'margin_per_lot': safe_model.get('margin_per_lot'),
        'quote_to_account_conversion_mode': safe_model.get('quote_to_account_conversion_mode'),
    }


def quantize_volume(value, *, min_lot, lot_step, max_lot):
    safe_value = _coerce_positive_float(value, 0.0)
    safe_min_lot = _coerce_positive_float(min_lot, 0.01)
    safe_lot_step = _coerce_positive_float(lot_step, safe_min_lot)
    safe_max_lot = _coerce_positive_float(max_lot, safe_min_lot)
    if safe_value < safe_min_lot:
        return 0.0
    bounded = min(safe_value, safe_max_lot)
    steps = math.floor((bounded / safe_lot_step) + 1e-9)
    quantized = steps * safe_lot_step
    if quantized + 1e-9 < safe_min_lot:
        return 0.0
    return min(round(quantized, 8), safe_max_lot)


def compute_margin_per_lot(capital_model, *, open_price, side):
    safe_model = dict(capital_model or {})
    margin_model = str(safe_model.get('margin_model') or 'disabled_legacy').strip().lower() or 'disabled_legacy'
    safe_open_price = _coerce_positive_float(open_price, None)
    if margin_model == 'disabled_legacy':
        return None
    if margin_model == 'fixed_per_lot':
        return _coerce_positive_float(safe_model.get('margin_per_lot'), None)
    if safe_open_price is None:
        return None
    contract_size_per_lot = _coerce_positive_float(safe_model.get('contract_size_per_lot'), 1.0)
    if margin_model in {'forex_notional', 'cash_notional'}:
        rate_key = 'margin_long_rate' if str(side or '').strip().lower() == 'long' else 'margin_short_rate'
        rate = _coerce_positive_float(safe_model.get(rate_key), None)
        if rate is None:
            leverage = _coerce_positive_float(safe_model.get('account_leverage'), 1.0)
            rate = 1.0 / leverage if leverage > 0 else 1.0
        return safe_open_price * contract_size_per_lot * rate
    return None


def compute_required_margin(capital_model, *, volume, open_price, side):
    safe_volume = _coerce_positive_float(volume, 0.0)
    margin_per_lot = compute_margin_per_lot(capital_model, open_price=open_price, side=side)
    if margin_per_lot is None:
        return 0.0
    return float(margin_per_lot) * safe_volume


def compute_max_affordable_volume(capital_model, *, available_margin, open_price, side, volume_cap=None):
    safe_model = dict(capital_model or {})
    safe_available_margin = max(float(available_margin or 0.0), 0.0)
    min_lot = _coerce_positive_float(safe_model.get('min_lot'), 0.01)
    lot_step = _coerce_positive_float(safe_model.get('lot_step'), min_lot)
    max_lot = _coerce_positive_float(safe_model.get('max_lot'), min_lot)
    if volume_cap is not None:
        max_lot = min(max_lot, _coerce_positive_float(volume_cap, max_lot))

    margin_model = str(safe_model.get('margin_model') or 'disabled_legacy').strip().lower() or 'disabled_legacy'
    if margin_model == 'disabled_legacy':
        return quantize_volume(max_lot, min_lot=min_lot, lot_step=lot_step, max_lot=max_lot)

    margin_per_lot = compute_margin_per_lot(safe_model, open_price=open_price, side=side)
    if margin_per_lot is None or margin_per_lot <= 0:
        return 0.0
    raw_volume = safe_available_margin / margin_per_lot
    return quantize_volume(raw_volume, min_lot=min_lot, lot_step=lot_step, max_lot=max_lot)


def resolve_trade_volume(
    *,
    capital_model,
    volume_mode,
    initial_volume,
    fixed_volume=None,
    base_volume=None,
    max_volume_cap=None,
    reference_capital=None,
    sleeve_virtual_equity=None,
    available_margin,
    open_price,
    side,
):
    safe_model = dict(capital_model or {})
    normalized_mode = normalize_volume_mode(volume_mode)
    safe_min_lot = _coerce_positive_float(safe_model.get('min_lot'), 0.01)
    safe_lot_step = _coerce_positive_float(safe_model.get('lot_step'), safe_min_lot)
    safe_max_lot = _coerce_positive_float(safe_model.get('max_lot'), safe_min_lot)
    cap = _coerce_positive_float(max_volume_cap, None)
    if cap is not None:
        safe_max_lot = min(safe_max_lot, cap)

    resolved_fixed_volume = _coerce_positive_float(fixed_volume, None)
    resolved_base_volume = _coerce_positive_float(base_volume, None)
    resolved_initial_volume = _coerce_positive_float(initial_volume, safe_min_lot)
    resolved_reference_capital = _coerce_positive_float(reference_capital, None)
    safe_sleeve_equity = _coerce_positive_float(sleeve_virtual_equity, None)
    available_margin_before = max(float(available_margin or 0.0), 0.0)
    max_affordable_volume = compute_max_affordable_volume(
        safe_model,
        available_margin=available_margin_before,
        open_price=open_price,
        side=side,
        volume_cap=safe_max_lot,
    )

    raw_target_volume = 0.0
    target_volume = 0.0
    reason = ''

    if normalized_mode == 'fixed_volume':
        raw_target_volume = resolved_fixed_volume or resolved_initial_volume
        target_volume = quantize_volume(raw_target_volume, min_lot=safe_min_lot, lot_step=safe_lot_step, max_lot=safe_max_lot)
        if target_volume <= 0:
            reason = 'invalid_fixed_volume'
    elif normalized_mode == 'max_affordable':
        raw_target_volume = safe_max_lot
        target_volume = quantize_volume(raw_target_volume, min_lot=safe_min_lot, lot_step=safe_lot_step, max_lot=safe_max_lot)
    else:
        if resolved_base_volume is None:
            reason = 'invalid_base_volume'
        else:
            reference = resolved_reference_capital or _coerce_positive_float(safe_model.get('initial_balance'), resolved_base_volume)
            sleeve_equity = safe_sleeve_equity or reference
            raw_target_volume = resolved_base_volume * (sleeve_equity / reference) if reference > 0 else resolved_base_volume
            target_floor = max(resolved_base_volume, raw_target_volume)
            target_volume = quantize_volume(target_floor, min_lot=safe_min_lot, lot_step=safe_lot_step, max_lot=safe_max_lot)

    if not reason:
        if normalized_mode == 'max_affordable':
            executed_volume = max_affordable_volume
        else:
            executed_volume = min(target_volume, max_affordable_volume) if max_affordable_volume > 0 else 0.0
        executed_volume = quantize_volume(executed_volume, min_lot=safe_min_lot, lot_step=safe_lot_step, max_lot=safe_max_lot)
        if executed_volume <= 0:
            reason = 'insufficient_margin'
    else:
        executed_volume = 0.0

    required_margin = compute_required_margin(
        safe_model,
        volume=executed_volume,
        open_price=open_price,
        side=side,
    ) if executed_volume > 0 else 0.0
    if executed_volume > 0 and required_margin - available_margin_before > 1e-9:
        executed_volume = 0.0
        required_margin = 0.0
        reason = 'insufficient_margin'

    return {
        'status': 'ok' if executed_volume > 0 else 'skip',
        'reason': reason or '',
        'volume_mode': normalized_mode,
        'raw_target_volume': float(raw_target_volume or 0.0),
        'target_volume': float(target_volume or 0.0),
        'max_affordable_volume': float(max_affordable_volume or 0.0),
        'requested_volume': float(target_volume or 0.0) if normalized_mode != 'max_affordable' else float(max_affordable_volume or 0.0),
        'executed_volume': float(executed_volume or 0.0),
        'required_margin': float(required_margin or 0.0),
        'available_margin_before': float(available_margin_before),
        'available_margin_after_open': float(max(available_margin_before - required_margin, 0.0)),
        'reference_capital': float(resolved_reference_capital or 0.0) if resolved_reference_capital else None,
    }

import math
from copy import deepcopy

import pandas as pd

BACKTEST_COST_PROFILE_DEFAULT = 'broker_active'
BACKTEST_LEGACY_FALLBACK_COST_PROFILE = 'oanda'
BACKTEST_ASSET_TYPE_DEFAULT = 'forex'

BACKTEST_COST_FIELD_KEYS = (
    'spreadInPips',
    'slippageInPips',
    'entrySlippageInPips',
    'closeSlippageInPips',
    'takeProfitSlippageInPips',
    'stopLossSlippageInPips',
    'trailingStopSlippageInPips',
    'minimumStopDistanceInPips',
    'volatilitySlippageMultiplier',
)

BACKTEST_BROKER_METADATA_FIELD_KEYS = (
    'brokerProfileId',
    'brokerProfileLabel',
    'brokerCode',
    'brokerLabel',
    'brokerMarketDomain',
)

BACKTEST_ASSET_TYPE_DEFINITIONS = {
    'forex': {
        'id': 'forex',
        'label': 'Forex',
        'description': 'Pip-based FX pricing with spread and slippage assumptions.',
    },
    'b3_equity': {
        'id': 'b3_equity',
        'label': 'B3 spot equity / ETF / BDR / FII',
        'description': 'Cash-market instruments priced directly in BRL per share or quota.',
    },
    'b3_option': {
        'id': 'b3_option',
        'label': 'B3 options',
        'description': 'Option premium notional model with B3 day-trade differentiation.',
    },
    'b3_term': {
        'id': 'b3_term',
        'label': 'B3 termo',
        'description': 'Financed cash-market operations with percentual explicit fees.',
    },
    'b3_mini_future': {
        'id': 'b3_mini_future',
        'label': 'B3 mini futures',
        'description': 'Point-based futures with fixed contract fees and pip-style PnL inputs.',
    },
}

BACKTEST_COST_PROFILES = {
    'custom': {
        'id': 'custom',
        'label': 'Custom',
        'description': 'Keeps the manual spread and slippage shell already typed by the operator.',
        'values': None,
        'notes': [],
    },
    'broker_active': {
        'id': 'broker_active',
        'label': 'Active broker',
        'description': 'Resolves automatically from the broker selected in the page header.',
        'values': None,
        'notes': [],
    },
    'forex': {
        'id': 'forex',
        'label': 'FOREX.com',
        'description': 'Spread-first FX approximation using the legacy FOREX.com shell.',
        'values': {
            'spreadInPips': 1.2,
            'slippageInPips': 0.2,
            'entrySlippageInPips': 0.2,
            'closeSlippageInPips': 0.2,
            'takeProfitSlippageInPips': 0.0,
            'stopLossSlippageInPips': 0.4,
            'trailingStopSlippageInPips': 0.5,
            'minimumStopDistanceInPips': 0.0,
            'volatilitySlippageMultiplier': 0.0,
        },
        'notes': [],
    },
    'oanda': {
        'id': 'oanda',
        'label': 'OANDA',
        'description': 'Spread-first FX approximation using the cheaper OANDA shell.',
        'values': {
            'spreadInPips': 1.0,
            'slippageInPips': 0.2,
            'entrySlippageInPips': 0.2,
            'closeSlippageInPips': 0.2,
            'takeProfitSlippageInPips': 0.0,
            'stopLossSlippageInPips': 0.4,
            'trailingStopSlippageInPips': 0.5,
            'minimumStopDistanceInPips': 0.0,
            'volatilitySlippageMultiplier': 0.0,
        },
        'notes': [],
    },
    'clear_b3': {
        'id': 'clear_b3',
        'label': 'CLEAR + B3',
        'description': 'Applies explicit B3/CLEAR fees for Brazilian listed products and keeps slippage configurable separately.',
        'values': {
            'spreadInPips': 0.0,
            'slippageInPips': 0.0,
            'entrySlippageInPips': 0.0,
            'closeSlippageInPips': 0.0,
            'takeProfitSlippageInPips': 0.0,
            'stopLossSlippageInPips': 0.0,
            'trailingStopSlippageInPips': 0.0,
            'minimumStopDistanceInPips': 0.0,
            'volatilitySlippageMultiplier': 0.0,
        },
        'notes': [
            'B3/CLEAR execution costs are modeled explicitly.',
            'Brazilian tax estimates are modeled on profitable trades for the supported B3 shells.',
            'IRRF withholding is treated as an advance credit inside the final estimate and is not double-counted separately.',
        ],
        'b3_percent_notional_rates': {
            'b3_equity': {
                'regular': 0.00030,
                'daytrade': 0.00023,
                'label': 'B3 + broker spot-market fee',
                'description': 'Percentual cost over notional for equities, ETFs, BDRs and FIIs.',
            },
            'b3_option': {
                'regular': 0.00134,
                'daytrade': 0.00045,
                'label': 'B3 options fee',
                'description': 'Percentual cost over option premium notional.',
            },
            'b3_term': {
                'regular': 0.00065,
                'daytrade': 0.00065,
                'label': 'B3 termo fee',
                'description': 'Percentual cost over financed notional.',
            },
        },
        'b3_contract_fees': {
            'b3_mini_future': {
                'per_contract': 0.33,
                'label': 'Mini-future contract fee',
                'description': 'Fixed BRL charge per contract side.',
            },
        },
    },
}

B3_TAX_ESTIMATE_RULES = {
    'b3_equity': {
        'common_rate': 0.15,
        'daytrade_rate': 0.20,
        'common_label': 'Estimated IR on taxable B3 spot gain',
        'daytrade_label': 'Estimated IR on B3 day-trade gain',
        'common_description': (
            'Estimated final IR over positive taxable gain for the mixed B3 spot bucket. '
            'The stock-only R$ 20k monthly exemption and FII/FIAGRO common-rate differences are not inferred automatically.'
        ),
        'daytrade_description': (
            'Estimated final IR over positive day-trade gain for the mixed B3 spot bucket. '
            'The 1% withholding is treated as an advance credit and is not added separately.'
        ),
    },
    'b3_option': {
        'common_rate': 0.15,
        'daytrade_rate': 0.20,
        'common_label': 'Estimated IR on B3 option gain',
        'daytrade_label': 'Estimated IR on B3 option day-trade gain',
        'common_description': 'Estimated final IR over positive taxable option gain.',
        'daytrade_description': 'Estimated final IR over positive day-trade option gain. The 1% withholding is treated as an advance credit.',
    },
    'b3_term': {
        'common_rate': 0.15,
        'daytrade_rate': 0.20,
        'common_label': 'Estimated IR on B3 termo gain',
        'daytrade_label': 'Estimated IR on B3 termo day-trade gain',
        'common_description': 'Estimated final IR over positive taxable termo gain.',
        'daytrade_description': 'Estimated final IR over positive day-trade termo gain. The 1% withholding is treated as an advance credit.',
    },
    'b3_mini_future': {
        'common_rate': 0.15,
        'daytrade_rate': 0.20,
        'common_label': 'Estimated IR on B3 mini-future gain',
        'daytrade_label': 'Estimated IR on B3 mini-future day-trade gain',
        'common_description': 'Estimated final IR over positive taxable mini-future gain.',
        'daytrade_description': 'Estimated final IR over positive day-trade mini-future gain. The 1% withholding is treated as an advance credit.',
    },
}

BROKER_CODE_DEFAULT_COST_PROFILES = {
    'forex.com': 'forex',
    'oanda': 'oanda',
    'clear': 'clear_b3',
}

MARKET_DOMAIN_DEFAULT_COST_PROFILES = {
    'forex': BACKTEST_LEGACY_FALLBACK_COST_PROFILE,
    'b3': 'clear_b3',
}

MARKET_DOMAIN_DEFAULT_ASSET_TYPES = {
    'forex': 'forex',
    'b3': 'b3_equity',
}

COST_PROFILE_DEFAULT_ASSET_TYPES = {
    'clear_b3': 'b3_equity',
    'forex': 'forex',
    'oanda': 'forex',
}


def normalize_backtest_cost_profile(value):
    normalized = str(value or '').strip().lower()
    if normalized in BACKTEST_COST_PROFILES:
        return normalized
    return BACKTEST_COST_PROFILE_DEFAULT


def normalize_backtest_asset_type(value):
    normalized = str(value or '').strip().lower()
    if normalized in BACKTEST_ASSET_TYPE_DEFINITIONS:
        return normalized
    return ''


def normalize_broker_code(value):
    return str(value or '').strip().lower()


def normalize_market_domain(value):
    normalized = str(value or '').strip().lower()
    if normalized in {'brazil', 'brasil', 'b3'}:
        return 'b3'
    return normalized


def _normalize_epoch(value):
    if value is None:
        return None
    try:
        numeric = float(value)
    except Exception:
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _epoch_to_sao_paulo_date(value):
    numeric = _normalize_epoch(value)
    if numeric is None:
        return None
    unit = 'ms' if abs(numeric) >= 1_000_000_000_000 else 's'
    try:
        timestamp = pd.to_datetime(numeric, unit=unit, utc=True, errors='coerce')
    except Exception:
        return None
    if pd.isna(timestamp):
        return None
    try:
        localized = timestamp.tz_convert('America/Sao_Paulo')
    except Exception:
        localized = timestamp
    return localized.date()


def classify_b3_day_trade(open_time=None, close_time=None):
    open_date = _epoch_to_sao_paulo_date(open_time)
    close_date = _epoch_to_sao_paulo_date(close_time)
    if open_date is None or close_date is None:
        return False
    return open_date == close_date


def resolve_broker_cost_context(raw_backtest=None, broker_profile=None):
    safe_backtest = dict(raw_backtest or {})
    safe_profile = dict(broker_profile or {})
    safe_profile_payload = (
        safe_profile.get('profile')
        if isinstance(safe_profile.get('profile'), dict)
        else {}
    )

    broker_profile_id = str(
        safe_backtest.get('brokerProfileId')
        or safe_backtest.get('broker_profile_id')
        or safe_profile.get('id')
        or ''
    ).strip()
    broker_profile_label = str(
        safe_backtest.get('brokerProfileLabel')
        or safe_backtest.get('broker_profile_label')
        or safe_profile.get('label')
        or ''
    ).strip()
    broker_code = normalize_broker_code(
        safe_backtest.get('brokerCode')
        or safe_backtest.get('broker_code')
        or safe_profile.get('broker_code')
        or safe_profile_payload.get('broker_code')
        or ''
    )
    broker_label = str(
        safe_backtest.get('brokerLabel')
        or safe_backtest.get('broker_label')
        or broker_profile_label
        or safe_profile.get('label')
        or ''
    ).strip()
    market_domain = normalize_market_domain(
        safe_backtest.get('brokerMarketDomain')
        or safe_backtest.get('broker_market_domain')
        or safe_backtest.get('market_domain')
        or safe_profile.get('market_domain')
        or safe_profile_payload.get('market_domain')
        or ''
    )
    broker_cost_profile = normalize_backtest_cost_profile(
        safe_backtest.get('brokerCostProfile')
        or safe_backtest.get('broker_cost_profile')
        or safe_profile_payload.get('cost_profile')
        or safe_profile_payload.get('costProfile')
        or ''
    )
    if broker_cost_profile == BACKTEST_COST_PROFILE_DEFAULT and not (
        safe_backtest.get('brokerCostProfile')
        or safe_backtest.get('broker_cost_profile')
        or safe_profile_payload.get('cost_profile')
        or safe_profile_payload.get('costProfile')
    ):
        broker_cost_profile = ''
    broker_default_asset_type = normalize_backtest_asset_type(
        safe_backtest.get('brokerDefaultAssetType')
        or safe_backtest.get('broker_default_asset_type')
        or safe_profile_payload.get('default_asset_type')
        or safe_profile_payload.get('defaultAssetType')
        or ''
    )

    return {
        'broker_profile_id': broker_profile_id,
        'broker_profile_label': broker_profile_label,
        'broker_code': broker_code,
        'broker_label': broker_label or broker_profile_label,
        'market_domain': market_domain,
        'broker_cost_profile': broker_cost_profile,
        'broker_default_asset_type': broker_default_asset_type,
    }


def resolve_effective_backtest_cost_profile(value, broker_code='', market_domain=''):
    broker_cost_profile = ''
    if isinstance(broker_code, dict):
        broker_context = broker_code
        broker_code = broker_context.get('broker_code', '')
        market_domain = broker_context.get('market_domain', '')
        broker_cost_profile = broker_context.get('broker_cost_profile', '')
    normalized = normalize_backtest_cost_profile(value)
    if normalized != 'broker_active':
        return normalized

    if broker_cost_profile and broker_cost_profile in BACKTEST_COST_PROFILES and broker_cost_profile != 'broker_active':
        return broker_cost_profile
    safe_broker_code = normalize_broker_code(broker_code)
    safe_market_domain = normalize_market_domain(market_domain)

    if safe_broker_code in BROKER_CODE_DEFAULT_COST_PROFILES:
        return BROKER_CODE_DEFAULT_COST_PROFILES[safe_broker_code]
    if safe_market_domain in MARKET_DOMAIN_DEFAULT_COST_PROFILES:
        return MARKET_DOMAIN_DEFAULT_COST_PROFILES[safe_market_domain]
    return BACKTEST_LEGACY_FALLBACK_COST_PROFILE


def resolve_backtest_asset_type(value, *, broker_code='', market_domain='', cost_profile=''):
    broker_default_asset_type = ''
    if isinstance(broker_code, dict):
        broker_context = broker_code
        broker_code = broker_context.get('broker_code', '')
        market_domain = broker_context.get('market_domain', '')
        broker_default_asset_type = broker_context.get('broker_default_asset_type', '')
    normalized = normalize_backtest_asset_type(value)
    if normalized:
        return normalized

    safe_market_domain = normalize_market_domain(market_domain)
    effective_cost_profile = resolve_effective_backtest_cost_profile(
        cost_profile or BACKTEST_COST_PROFILE_DEFAULT,
        broker_code=broker_code,
        market_domain=market_domain,
    )

    if broker_default_asset_type:
        return broker_default_asset_type
    if safe_market_domain in MARKET_DOMAIN_DEFAULT_ASSET_TYPES:
        return MARKET_DOMAIN_DEFAULT_ASSET_TYPES[safe_market_domain]
    return COST_PROFILE_DEFAULT_ASSET_TYPES.get(effective_cost_profile, BACKTEST_ASSET_TYPE_DEFAULT)


def build_backtest_cost_profile_values(value, *, broker_code='', market_domain=''):
    effective_profile = resolve_effective_backtest_cost_profile(
        value,
        broker_code=broker_code,
        market_domain=market_domain,
    )
    values = BACKTEST_COST_PROFILES.get(effective_profile, {}).get('values') or {}
    return dict(values)


def merge_backtest_cost_profile_values(raw_backtest=None, broker_profile=None):
    safe_backtest = dict(raw_backtest or {})
    broker_context = resolve_broker_cost_context(safe_backtest, broker_profile)
    has_explicit_cost_field = any(field in safe_backtest for field in BACKTEST_COST_FIELD_KEYS)
    raw_cost_profile = str(safe_backtest.get('costProfile') or '').strip()
    if raw_cost_profile:
        requested_cost_profile = normalize_backtest_cost_profile(raw_cost_profile)
    else:
        requested_cost_profile = 'custom' if has_explicit_cost_field else BACKTEST_COST_PROFILE_DEFAULT

    effective_cost_profile = resolve_effective_backtest_cost_profile(
        requested_cost_profile,
        broker_code=broker_context['broker_code'],
        market_domain=broker_context['market_domain'],
    )

    merged = {}
    if requested_cost_profile != 'custom' and not has_explicit_cost_field:
        merged.update(build_backtest_cost_profile_values(
            requested_cost_profile,
            broker_code=broker_context['broker_code'],
            market_domain=broker_context['market_domain'],
        ))
    merged.update(safe_backtest)
    merged['costProfile'] = requested_cost_profile
    merged['assetType'] = resolve_backtest_asset_type(
        safe_backtest.get('assetType'),
        broker_code=broker_context['broker_code'],
        market_domain=broker_context['market_domain'],
        cost_profile=requested_cost_profile,
    )
    merged['resolvedCostProfile'] = effective_cost_profile
    return merged


def _build_b3_percent_fee_items(
    *,
    asset_type,
    definition,
    volume,
    open_price,
    close_price,
    open_time,
    close_time,
):
    rate_table = dict(
        BACKTEST_COST_PROFILES['clear_b3']
        .get('b3_percent_notional_rates', {})
        .get(asset_type, {})
    )
    if not rate_table:
        return []
    safe_volume = abs(float(volume or 0.0))
    if safe_volume <= 0:
        return []
    safe_open_price = _normalize_epoch(open_price)
    safe_close_price = _normalize_epoch(close_price)
    if safe_open_price is None or safe_close_price is None:
        return []

    is_day_trade = classify_b3_day_trade(open_time=open_time, close_time=close_time)
    applied_rate = float(rate_table['daytrade'] if is_day_trade else rate_table['regular'])
    rate_label = 'daytrade' if is_day_trade else 'regular'
    entry_notional = abs(safe_open_price * safe_volume)
    exit_notional = abs(safe_close_price * safe_volume)

    return [
        {
            'id': f'{asset_type}_entry_fee',
            'label': f'Entry {rate_table.get("label") or definition.get("label")}',
            'description': rate_table.get('description') or definition.get('description') or '',
            'category': 'explicit_fee',
            'basis': 'percent_notional',
            'rate': applied_rate,
            'rate_label': rate_label,
            'notional': entry_notional,
            'amount': entry_notional * applied_rate,
            'asset_type': asset_type,
            'applies_to': 'entry',
        },
        {
            'id': f'{asset_type}_exit_fee',
            'label': f'Exit {rate_table.get("label") or definition.get("label")}',
            'description': rate_table.get('description') or definition.get('description') or '',
            'category': 'explicit_fee',
            'basis': 'percent_notional',
            'rate': applied_rate,
            'rate_label': rate_label,
            'notional': exit_notional,
            'amount': exit_notional * applied_rate,
            'asset_type': asset_type,
            'applies_to': 'exit',
        },
    ]


def _build_b3_contract_fee_items(*, asset_type, volume):
    fee_table = dict(
        BACKTEST_COST_PROFILES['clear_b3']
        .get('b3_contract_fees', {})
        .get(asset_type, {})
    )
    if not fee_table:
        return []

    safe_contracts = abs(float(volume or 0.0))
    if safe_contracts <= 0:
        return []

    per_contract = float(fee_table.get('per_contract') or 0.0)
    if per_contract <= 0:
        return []

    return [
        {
            'id': f'{asset_type}_entry_contract_fee',
            'label': f'Entry {fee_table.get("label") or "Contract fee"}',
            'description': fee_table.get('description') or '',
            'category': 'explicit_fee',
            'basis': 'per_contract',
            'rate': per_contract,
            'contracts': safe_contracts,
            'amount': safe_contracts * per_contract,
            'asset_type': asset_type,
            'applies_to': 'entry',
        },
        {
            'id': f'{asset_type}_exit_contract_fee',
            'label': f'Exit {fee_table.get("label") or "Contract fee"}',
            'description': fee_table.get('description') or '',
            'category': 'explicit_fee',
            'basis': 'per_contract',
            'rate': per_contract,
            'contracts': safe_contracts,
            'amount': safe_contracts * per_contract,
            'asset_type': asset_type,
            'applies_to': 'exit',
        },
    ]


def _build_b3_tax_estimate_items(
    *,
    asset_type,
    open_time,
    close_time,
    pre_tax_net_pnl,
):
    rule = dict(B3_TAX_ESTIMATE_RULES.get(asset_type) or {})
    if not rule:
        return []

    taxable_gain = max(float(pre_tax_net_pnl or 0.0), 0.0)
    if taxable_gain <= 0:
        return []

    is_day_trade = classify_b3_day_trade(open_time=open_time, close_time=close_time)
    applied_rate = float(rule['daytrade_rate'] if is_day_trade else rule['common_rate'])
    if applied_rate <= 0:
        return []

    return [{
        'id': f'{asset_type}_estimated_income_tax',
        'label': rule['daytrade_label'] if is_day_trade else rule['common_label'],
        'description': rule['daytrade_description'] if is_day_trade else rule['common_description'],
        'category': 'estimated_tax',
        'basis': 'positive_net_gain',
        'rate': applied_rate,
        'taxable_gain': taxable_gain,
        'amount': taxable_gain * applied_rate,
        'asset_type': asset_type,
        'applies_to': 'round_trip',
        'day_trade': bool(is_day_trade),
    }]


def build_trade_cost_breakdown(
    *,
    cost_profile='broker_active',
    asset_type='forex',
    broker_code='',
    market_domain='',
    volume=1.0,
    pip_value=0.0,
    spread_in_pips=0.0,
    open_price=None,
    close_price=None,
    open_time=None,
    close_time=None,
    gross_pnl=None,
):
    effective_cost_profile = resolve_effective_backtest_cost_profile(
        cost_profile,
        broker_code=broker_code,
        market_domain=market_domain,
    )
    effective_asset_type = resolve_backtest_asset_type(
        asset_type,
        broker_code=broker_code,
        market_domain=market_domain,
        cost_profile=effective_cost_profile,
    )
    asset_definition = BACKTEST_ASSET_TYPE_DEFINITIONS.get(effective_asset_type) or {}

    if effective_cost_profile in {'forex', 'oanda'}:
        spread_cost = max(float(spread_in_pips or 0.0), 0.0) * max(float(pip_value or 0.0), 0.0)
        if spread_cost <= 0:
            return []
        profile_label = BACKTEST_COST_PROFILES[effective_cost_profile]['label']
        return [{
            'id': 'spread',
            'label': 'Spread',
            'description': f'Fixed spread shell from {profile_label}.',
            'category': 'explicit_fee',
            'basis': 'spread_pips',
            'rate': max(float(spread_in_pips or 0.0), 0.0),
            'pip_value': max(float(pip_value or 0.0), 0.0),
            'amount': spread_cost,
            'asset_type': effective_asset_type,
            'applies_to': 'round_trip',
        }]

    if effective_cost_profile != 'clear_b3':
        return []

    if effective_asset_type == 'b3_mini_future':
        explicit_items = _build_b3_contract_fee_items(asset_type=effective_asset_type, volume=volume)
    else:
        explicit_items = _build_b3_percent_fee_items(
            asset_type=effective_asset_type,
            definition=asset_definition,
            volume=volume,
            open_price=open_price,
            close_price=close_price,
            open_time=open_time,
            close_time=close_time,
        )

    if gross_pnl is None:
        return explicit_items

    explicit_total = float(sum(float(item.get('amount') or 0.0) for item in explicit_items))
    tax_items = _build_b3_tax_estimate_items(
        asset_type=effective_asset_type,
        open_time=open_time,
        close_time=close_time,
        pre_tax_net_pnl=float(gross_pnl or 0.0) - explicit_total,
    )
    return merge_cost_breakdown_items(explicit_items, tax_items)


def merge_cost_breakdown_items(*collections):
    merged = {}
    order = []
    for collection in collections:
        for raw_item in list(collection or []):
            item = dict(raw_item or {})
            item_id = str(item.get('id') or '').strip()
            if not item_id:
                continue
            amount = float(item.get('amount') or 0.0)
            if item_id not in merged:
                merged[item_id] = {
                    **item,
                    'amount': amount,
                }
                order.append(item_id)
                continue
            merged[item_id]['amount'] = float(merged[item_id].get('amount') or 0.0) + amount
    return [merged[item_id] for item_id in order]


def partition_cost_breakdown_items(collection=None):
    operational_items = []
    estimated_tax_items = []
    for raw_item in list(collection or []):
        item = dict(raw_item or {})
        category = str(item.get('category') or '').strip().lower()
        if category == 'estimated_tax':
            estimated_tax_items.append(item)
        else:
            operational_items.append(item)
    return {
        'operational': merge_cost_breakdown_items(operational_items),
        'estimated_tax': merge_cost_breakdown_items(estimated_tax_items),
    }


def sum_cost_breakdown_amount(collection=None):
    return float(sum(float((item or {}).get('amount') or 0.0) for item in list(collection or [])))


def build_backtest_cost_policy(backtest_request=None, broker_profile=None):
    safe_backtest = merge_backtest_cost_profile_values(backtest_request or {}, broker_profile=broker_profile)
    broker_context = resolve_broker_cost_context(safe_backtest, broker_profile)
    requested_cost_profile = normalize_backtest_cost_profile(safe_backtest.get('costProfile'))
    effective_cost_profile = resolve_effective_backtest_cost_profile(
        requested_cost_profile,
        broker_code=broker_context['broker_code'],
        market_domain=broker_context['market_domain'],
    )
    effective_asset_type = resolve_backtest_asset_type(
        safe_backtest.get('assetType'),
        broker_code=broker_context['broker_code'],
        market_domain=broker_context['market_domain'],
        cost_profile=requested_cost_profile,
    )
    requested_profile_definition = deepcopy(BACKTEST_COST_PROFILES.get(requested_cost_profile) or {})
    effective_profile_definition = deepcopy(BACKTEST_COST_PROFILES.get(effective_cost_profile) or {})
    asset_type_definition = deepcopy(BACKTEST_ASSET_TYPE_DEFINITIONS.get(effective_asset_type) or {})

    explicit_cost_items = build_trade_cost_breakdown(
        cost_profile=requested_cost_profile,
        asset_type=effective_asset_type,
        broker_code=broker_context['broker_code'],
        market_domain=broker_context['market_domain'],
        volume=float(safe_backtest.get('initialVolume') or 0.0),
        pip_value=max(float(safe_backtest.get('pipValuePerLot') or 0.0), 0.0),
        spread_in_pips=max(float(safe_backtest.get('spreadInPips') or 0.0), 0.0),
        open_price=1.0 if effective_asset_type == 'forex' else 100.0,
        close_price=1.0 if effective_asset_type == 'forex' else 100.0,
        open_time=None,
        close_time=None,
    )
    non_explicit_execution_items = [
        {
            'id': 'entry_slippage',
            'label': 'Entry slippage',
            'description': 'Price worsening applied on entry fills instead of explicit trade_cost.',
            'basis': 'pips',
            'amount': float(
                safe_backtest.get('entrySlippageInPips')
                if safe_backtest.get('entrySlippageInPips') is not None
                else safe_backtest.get('slippageInPips') or 0.0
            ),
        },
        {
            'id': 'close_slippage',
            'label': 'Close slippage',
            'description': 'Price worsening applied on manual close fills instead of explicit trade_cost.',
            'basis': 'pips',
            'amount': float(
                safe_backtest.get('closeSlippageInPips')
                if safe_backtest.get('closeSlippageInPips') is not None
                else safe_backtest.get('slippageInPips') or 0.0
            ),
        },
        {
            'id': 'take_profit_slippage',
            'label': 'Take-profit slippage',
            'description': 'Price worsening applied on take-profit fills instead of explicit trade_cost.',
            'basis': 'pips',
            'amount': float(
                safe_backtest.get('takeProfitSlippageInPips')
                if safe_backtest.get('takeProfitSlippageInPips') is not None
                else safe_backtest.get('slippageInPips') or 0.0
            ),
        },
        {
            'id': 'stop_loss_slippage',
            'label': 'Stop-loss slippage',
            'description': 'Price worsening applied on stop-loss fills instead of explicit trade_cost.',
            'basis': 'pips',
            'amount': float(
                safe_backtest.get('stopLossSlippageInPips')
                if safe_backtest.get('stopLossSlippageInPips') is not None
                else safe_backtest.get('slippageInPips') or 0.0
            ),
        },
        {
            'id': 'trailing_stop_slippage',
            'label': 'Trailing-stop slippage',
            'description': 'Price worsening applied on trailing-stop fills instead of explicit trade_cost.',
            'basis': 'pips',
            'amount': float(
                safe_backtest.get('trailingStopSlippageInPips')
                if safe_backtest.get('trailingStopSlippageInPips') is not None
                else safe_backtest.get('slippageInPips') or 0.0
            ),
        },
    ]

    return {
        'requested_cost_profile': requested_cost_profile,
        'requested_cost_profile_label': requested_profile_definition.get('label') or requested_cost_profile,
        'cost_profile': effective_cost_profile,
        'cost_profile_label': effective_profile_definition.get('label') or effective_cost_profile,
        'cost_profile_description': effective_profile_definition.get('description') or '',
        'cost_profile_source': 'active_broker' if requested_cost_profile == 'broker_active' else 'manual',
        'broker_profile_id': broker_context['broker_profile_id'],
        'broker_profile_label': broker_context['broker_profile_label'],
        'broker_code': broker_context['broker_code'],
        'broker_label': broker_context['broker_label'],
        'market_domain': broker_context['market_domain'],
        'asset_type': effective_asset_type,
        'asset_type_label': asset_type_definition.get('label') or effective_asset_type,
        'asset_type_description': asset_type_definition.get('description') or '',
        'explicit_cost_items': explicit_cost_items,
        'non_explicit_execution_items': non_explicit_execution_items,
        'profile_notes': list(effective_profile_definition.get('notes') or []),
        'taxes_modeled': True,
        'taxes_estimated': True if effective_cost_profile == 'clear_b3' else False,
    }

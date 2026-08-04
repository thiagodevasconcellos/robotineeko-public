export const BACKTEST_COST_FIELD_KEYS = Object.freeze([
    'spreadInPips',
    'slippageInPips',
    'entrySlippageInPips',
    'closeSlippageInPips',
    'takeProfitSlippageInPips',
    'stopLossSlippageInPips',
    'trailingStopSlippageInPips',
    'minimumStopDistanceInPips',
    'volatilitySlippageMultiplier',
])

export const BACKTEST_ASSET_TYPE_DEFINITIONS = Object.freeze({
    forex: Object.freeze({
        id: 'forex',
        label: 'Forex',
        description: 'Pip-based FX pricing with spread and slippage assumptions.',
    }),
    b3_equity: Object.freeze({
        id: 'b3_equity',
        label: 'B3 spot equity / ETF / BDR / FII',
        description: 'Cash-market instruments priced directly in BRL per share or quota.',
    }),
    b3_option: Object.freeze({
        id: 'b3_option',
        label: 'B3 options',
        description: 'Option premium notional model with B3 day-trade differentiation.',
    }),
    b3_term: Object.freeze({
        id: 'b3_term',
        label: 'B3 termo',
        description: 'Financed cash-market operations with percentual explicit fees.',
    }),
    b3_mini_future: Object.freeze({
        id: 'b3_mini_future',
        label: 'B3 mini futures',
        description: 'Point-based futures with fixed contract fees and pip-style PnL inputs.',
    }),
})

export const BACKTEST_COST_PROFILE_DEFINITIONS = Object.freeze({
    custom: Object.freeze({
        id: 'custom',
        label: 'Custom',
        description: 'Keeps the current manual spread and slippage values.',
        values: null,
        notes: Object.freeze([]),
    }),
    broker_active: Object.freeze({
        id: 'broker_active',
        label: 'Active broker',
        description: 'Resolves automatically from the broker selected in the page header.',
        values: null,
        notes: Object.freeze([]),
    }),
    forex: Object.freeze({
        id: 'forex',
        label: 'Forex.com',
        description: 'Spread-first FX approximation using the legacy Forex.com shell.',
        values: Object.freeze({
            spreadInPips: 1.2,
            slippageInPips: 0.2,
            entrySlippageInPips: 0.2,
            closeSlippageInPips: 0.2,
            takeProfitSlippageInPips: 0.0,
            stopLossSlippageInPips: 0.4,
            trailingStopSlippageInPips: 0.5,
            minimumStopDistanceInPips: 0.0,
            volatilitySlippageMultiplier: 0.0,
        }),
        notes: Object.freeze([]),
    }),
    oanda: Object.freeze({
        id: 'oanda',
        label: 'OANDA',
        description: 'Spread-first FX approximation using the cheaper OANDA shell.',
        values: Object.freeze({
            spreadInPips: 1.0,
            slippageInPips: 0.2,
            entrySlippageInPips: 0.2,
            closeSlippageInPips: 0.2,
            takeProfitSlippageInPips: 0.0,
            stopLossSlippageInPips: 0.4,
            trailingStopSlippageInPips: 0.5,
            minimumStopDistanceInPips: 0.0,
            volatilitySlippageMultiplier: 0.0,
        }),
        notes: Object.freeze([]),
    }),
    clear_b3: Object.freeze({
        id: 'clear_b3',
        label: 'CLEAR + B3',
        description: 'Applies explicit B3/CLEAR fees for Brazilian listed products and keeps slippage configurable separately.',
        values: Object.freeze({
            spreadInPips: 0.0,
            slippageInPips: 0.0,
            entrySlippageInPips: 0.0,
            closeSlippageInPips: 0.0,
            takeProfitSlippageInPips: 0.0,
            stopLossSlippageInPips: 0.0,
            trailingStopSlippageInPips: 0.0,
            minimumStopDistanceInPips: 0.0,
            volatilitySlippageMultiplier: 0.0,
        }),
        notes: Object.freeze([
            'B3/CLEAR execution costs are modeled explicitly.',
            'Brazilian tax estimates are modeled on profitable trades for the supported B3 shells.',
            'IRRF withholding is treated as an advance credit inside the final estimate and is not double-counted separately.',
        ]),
    }),
})

export const DEFAULT_BACKTEST_COST_PROFILE = 'broker_active'
export const DEFAULT_BACKTEST_ASSET_TYPE = 'forex'

const BROKER_CODE_DEFAULT_COST_PROFILES = Object.freeze({
    'forex.com': 'forex',
    oanda: 'oanda',
    clear: 'clear_b3',
})

const MARKET_DOMAIN_DEFAULT_COST_PROFILES = Object.freeze({
    forex: 'oanda',
    b3: 'clear_b3',
})

const MARKET_DOMAIN_DEFAULT_ASSET_TYPES = Object.freeze({
    forex: 'forex',
    b3: 'b3_equity',
})

const COST_PROFILE_DEFAULT_ASSET_TYPES = Object.freeze({
    clear_b3: 'b3_equity',
    forex: 'forex',
    oanda: 'forex',
})

function normalizeText(value) {
    return String(value || '').trim()
}

function normalizeLower(value) {
    return normalizeText(value).toLowerCase()
}

function normalizeMarketDomain(value) {
    const normalized = normalizeLower(value)
    if (normalized === 'brazil' || normalized === 'brasil' || normalized === 'b3') {
        return 'b3'
    }
    return normalized
}

export function normalizeBacktestCostProfile(value) {
    const normalized = normalizeLower(value)
    return BACKTEST_COST_PROFILE_DEFINITIONS[normalized] ? normalized : DEFAULT_BACKTEST_COST_PROFILE
}

export function normalizeBacktestAssetType(value) {
    const normalized = normalizeLower(value)
    return BACKTEST_ASSET_TYPE_DEFINITIONS[normalized] ? normalized : ''
}

export function resolveBacktestBrokerCostContext(rawBacktest = null, brokerProfile = null) {
    const safeBacktest = rawBacktest && typeof rawBacktest === 'object' ? rawBacktest : {}
    const safeProfile = brokerProfile && typeof brokerProfile === 'object' ? brokerProfile : {}
    const safeProfilePayload = safeProfile.profile && typeof safeProfile.profile === 'object'
        ? safeProfile.profile
        : {}

    return {
        brokerProfileId: normalizeText(
            safeBacktest.brokerProfileId
            || safeBacktest.broker_profile_id
            || safeProfile.id
            || '',
        ),
        brokerProfileLabel: normalizeText(
            safeBacktest.brokerProfileLabel
            || safeBacktest.broker_profile_label
            || safeProfile.label
            || '',
        ),
        brokerCode: normalizeLower(
            safeBacktest.brokerCode
            || safeBacktest.broker_code
            || safeProfile.broker_code
            || safeProfilePayload.broker_code
            || '',
        ),
        brokerLabel: normalizeText(
            safeBacktest.brokerLabel
            || safeBacktest.broker_label
            || safeProfile.label
            || '',
        ),
        marketDomain: normalizeMarketDomain(
            safeBacktest.brokerMarketDomain
            || safeBacktest.broker_market_domain
            || safeBacktest.market_domain
            || safeProfile.market_domain
            || safeProfilePayload.market_domain
            || '',
        ),
        brokerCostProfile: normalizeLower(
            safeBacktest.brokerCostProfile
            || safeBacktest.broker_cost_profile
            || safeProfilePayload.cost_profile
            || safeProfilePayload.costProfile
            || '',
        ),
        brokerDefaultAssetType: normalizeBacktestAssetType(
            safeBacktest.brokerDefaultAssetType
            || safeBacktest.broker_default_asset_type
            || safeProfilePayload.default_asset_type
            || safeProfilePayload.defaultAssetType
            || '',
        ),
    }
}

export function resolveEffectiveBacktestCostProfile(value, brokerProfile = null, rawBacktest = null) {
    const requestedProfile = normalizeBacktestCostProfile(value)
    if (requestedProfile !== 'broker_active') {
        return requestedProfile
    }

    const brokerContext = resolveBacktestBrokerCostContext(rawBacktest, brokerProfile)
    if (
        brokerContext.brokerCostProfile
        && BACKTEST_COST_PROFILE_DEFINITIONS[brokerContext.brokerCostProfile]
        && brokerContext.brokerCostProfile !== 'broker_active'
    ) {
        return brokerContext.brokerCostProfile
    }
    if (BROKER_CODE_DEFAULT_COST_PROFILES[brokerContext.brokerCode]) {
        return BROKER_CODE_DEFAULT_COST_PROFILES[brokerContext.brokerCode]
    }
    if (MARKET_DOMAIN_DEFAULT_COST_PROFILES[brokerContext.marketDomain]) {
        return MARKET_DOMAIN_DEFAULT_COST_PROFILES[brokerContext.marketDomain]
    }
    return 'oanda'
}

export function resolveBacktestAssetType(value, brokerProfile = null, rawBacktest = null, costProfile = '') {
    const normalized = normalizeBacktestAssetType(value)
    if (normalized) {
        return normalized
    }

    const brokerContext = resolveBacktestBrokerCostContext(rawBacktest, brokerProfile)
    const effectiveProfile = resolveEffectiveBacktestCostProfile(
        costProfile || DEFAULT_BACKTEST_COST_PROFILE,
        brokerProfile,
        rawBacktest,
    )
    if (brokerContext.brokerDefaultAssetType) {
        return brokerContext.brokerDefaultAssetType
    }
    if (MARKET_DOMAIN_DEFAULT_ASSET_TYPES[brokerContext.marketDomain]) {
        return MARKET_DOMAIN_DEFAULT_ASSET_TYPES[brokerContext.marketDomain]
    }
    return COST_PROFILE_DEFAULT_ASSET_TYPES[effectiveProfile] || DEFAULT_BACKTEST_ASSET_TYPE
}

export function coerceBacktestAssetType(value, brokerProfile = null, rawBacktest = null, costProfile = '') {
    const current = normalizeBacktestAssetType(value)
    const effectiveProfile = resolveEffectiveBacktestCostProfile(
        costProfile || DEFAULT_BACKTEST_COST_PROFILE,
        brokerProfile,
        rawBacktest,
    )

    if (effectiveProfile === 'clear_b3') {
        return current && current.startsWith('b3_') ? current : 'b3_equity'
    }

    if (effectiveProfile === 'forex' || effectiveProfile === 'oanda') {
        return 'forex'
    }

    return resolveBacktestAssetType(value, brokerProfile, rawBacktest, costProfile)
}

export function getBacktestCostProfileDefinition(value, brokerProfile = null, rawBacktest = null) {
    return BACKTEST_COST_PROFILE_DEFINITIONS[
        resolveEffectiveBacktestCostProfile(value, brokerProfile, rawBacktest)
    ]
}

export function buildBacktestCostProfileValues(value, brokerProfile = null, rawBacktest = null) {
    const definition = getBacktestCostProfileDefinition(value, brokerProfile, rawBacktest)
    return definition?.values ? { ...definition.values } : {}
}

export function mergeBacktestCostProfileValues(rawBacktest = null, brokerProfile = null) {
    const safeBacktest = rawBacktest && typeof rawBacktest === 'object' ? rawBacktest : {}
    const hasExplicitCostField = BACKTEST_COST_FIELD_KEYS.some((field) => Object.prototype.hasOwnProperty.call(safeBacktest, field))
    const rawCostProfile = normalizeText(safeBacktest.costProfile)
    const costProfile = rawCostProfile
        ? normalizeBacktestCostProfile(rawCostProfile)
        : (hasExplicitCostField ? 'custom' : DEFAULT_BACKTEST_COST_PROFILE)

    return {
        ...(costProfile !== 'custom' && !hasExplicitCostField
            ? buildBacktestCostProfileValues(costProfile, brokerProfile, safeBacktest)
            : {}),
        ...safeBacktest,
        costProfile,
        assetType: resolveBacktestAssetType(safeBacktest.assetType, brokerProfile, safeBacktest, costProfile),
        resolvedCostProfile: resolveEffectiveBacktestCostProfile(costProfile, brokerProfile, safeBacktest),
    }
}

export function getBacktestCostFieldResetValue(field, costProfile, fallbackDefaults, brokerProfile = null, rawBacktest = null) {
    const normalizedField = normalizeText(field)
    if (BACKTEST_COST_FIELD_KEYS.includes(normalizedField)) {
        const profileValues = buildBacktestCostProfileValues(costProfile, brokerProfile, rawBacktest)
        if (Object.prototype.hasOwnProperty.call(profileValues, normalizedField)) {
            return profileValues[normalizedField]
        }
    }

    return fallbackDefaults?.[normalizedField]
}

function buildExplicitCostItems(backtest = null, brokerProfile = null) {
    const safeBacktest = mergeBacktestCostProfileValues(backtest, brokerProfile)
    const effectiveProfile = resolveEffectiveBacktestCostProfile(safeBacktest.costProfile, brokerProfile, safeBacktest)
    const assetType = resolveBacktestAssetType(safeBacktest.assetType, brokerProfile, safeBacktest, safeBacktest.costProfile)
    if (effectiveProfile === 'forex' || effectiveProfile === 'oanda') {
        return [{
            id: 'spread',
            label: 'Spread',
            description: `Fixed spread shell from ${BACKTEST_COST_PROFILE_DEFINITIONS[effectiveProfile]?.label || effectiveProfile}.`,
            basis: 'spread_pips',
            rate: Number(safeBacktest.spreadInPips || 0),
        }]
    }

    if (effectiveProfile !== 'clear_b3') {
        return []
    }

    if (assetType === 'b3_mini_future') {
        return [
            {
                id: 'b3_mini_future_entry_contract_fee',
                label: 'Entry mini-future contract fee',
                description: 'Fixed BRL charge per contract side.',
                basis: 'per_contract',
                rate: 0.33,
            },
            {
                id: 'b3_mini_future_exit_contract_fee',
                label: 'Exit mini-future contract fee',
                description: 'Fixed BRL charge per contract side.',
                basis: 'per_contract',
                rate: 0.33,
            },
        ]
    }

    const percentRates = {
        b3_equity: { regular: 0.00030, daytrade: 0.00023, label: 'B3 + broker spot-market fee' },
        b3_option: { regular: 0.00134, daytrade: 0.00045, label: 'B3 options fee' },
        b3_term: { regular: 0.00065, daytrade: 0.00065, label: 'B3 termo fee' },
    }
    const rateTable = percentRates[assetType]
    if (!rateTable) {
        return []
    }
    return [
        {
            id: `${assetType}_entry_fee`,
            label: `Entry ${rateTable.label}`,
            description: 'Percentual cost over notional.',
            basis: 'percent_notional',
            regularRate: rateTable.regular,
            daytradeRate: rateTable.daytrade,
        },
        {
            id: `${assetType}_exit_fee`,
            label: `Exit ${rateTable.label}`,
            description: 'Percentual cost over notional.',
            basis: 'percent_notional',
            regularRate: rateTable.regular,
            daytradeRate: rateTable.daytrade,
        },
    ]
}

export function buildBacktestCostPolicy(backtest = null, brokerProfile = null) {
    const safeBacktest = mergeBacktestCostProfileValues(backtest, brokerProfile)
    const requestedCostProfile = normalizeBacktestCostProfile(safeBacktest.costProfile)
    const effectiveCostProfile = resolveEffectiveBacktestCostProfile(requestedCostProfile, brokerProfile, safeBacktest)
    const effectiveAssetType = resolveBacktestAssetType(safeBacktest.assetType, brokerProfile, safeBacktest, requestedCostProfile)
    const effectiveProfileDefinition = BACKTEST_COST_PROFILE_DEFINITIONS[effectiveCostProfile]
    const requestedProfileDefinition = BACKTEST_COST_PROFILE_DEFINITIONS[requestedCostProfile]
    const assetDefinition = BACKTEST_ASSET_TYPE_DEFINITIONS[effectiveAssetType]
    const brokerContext = resolveBacktestBrokerCostContext(safeBacktest, brokerProfile)

    return {
        requested_cost_profile: requestedCostProfile,
        requested_cost_profile_label: requestedProfileDefinition?.label || requestedCostProfile,
        cost_profile: effectiveCostProfile,
        cost_profile_label: effectiveProfileDefinition?.label || effectiveCostProfile,
        cost_profile_description: effectiveProfileDefinition?.description || '',
        cost_profile_source: requestedCostProfile === 'broker_active' ? 'active_broker' : 'manual',
        broker_profile_id: brokerContext.brokerProfileId,
        broker_profile_label: brokerContext.brokerProfileLabel,
        broker_code: brokerContext.brokerCode,
        broker_label: brokerContext.brokerLabel || brokerContext.brokerProfileLabel,
        market_domain: brokerContext.marketDomain,
        asset_type: effectiveAssetType,
        asset_type_label: assetDefinition?.label || effectiveAssetType,
        asset_type_description: assetDefinition?.description || '',
        explicit_cost_items: buildExplicitCostItems(safeBacktest, brokerProfile),
        non_explicit_execution_items: [
            {
                id: 'entry_slippage',
                label: 'Entry slippage',
                amount: Number(safeBacktest.entrySlippageInPips ?? safeBacktest.slippageInPips ?? 0),
                basis: 'pips',
            },
            {
                id: 'close_slippage',
                label: 'Close slippage',
                amount: Number(safeBacktest.closeSlippageInPips ?? safeBacktest.slippageInPips ?? 0),
                basis: 'pips',
            },
            {
                id: 'take_profit_slippage',
                label: 'Take-profit slippage',
                amount: Number(safeBacktest.takeProfitSlippageInPips ?? safeBacktest.slippageInPips ?? 0),
                basis: 'pips',
            },
            {
                id: 'stop_loss_slippage',
                label: 'Stop-loss slippage',
                amount: Number(safeBacktest.stopLossSlippageInPips ?? safeBacktest.slippageInPips ?? 0),
                basis: 'pips',
            },
            {
                id: 'trailing_stop_slippage',
                label: 'Trailing-stop slippage',
                amount: Number(safeBacktest.trailingStopSlippageInPips ?? safeBacktest.slippageInPips ?? 0),
                basis: 'pips',
            },
        ],
        profile_notes: [...(effectiveProfileDefinition?.notes || [])],
        taxes_modeled: true,
        taxes_estimated: effectiveCostProfile === 'clear_b3',
    }
}

function normalizeText(value) {
    return String(value || '').trim()
}

function normalizeLower(value) {
    return normalizeText(value).toLowerCase()
}

function toPositiveNumber(value, fallback = null) {
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export const BACKTEST_MARGIN_MODEL_DEFINITIONS = Object.freeze({
    disabled_legacy: Object.freeze({
        id: 'disabled_legacy',
        label: 'Disabled legacy',
        description: 'Keeps the legacy no-margin behavior. Max-volume modes should avoid this mode.',
    }),
    forex_notional: Object.freeze({
        id: 'forex_notional',
        label: 'Forex notional',
        description: 'Reserves margin from notional price x contract size x margin rate.',
    }),
    cash_notional: Object.freeze({
        id: 'cash_notional',
        label: 'Cash notional',
        description: 'Reserves cash/notional directly from BRL price x volume.',
    }),
    fixed_per_lot: Object.freeze({
        id: 'fixed_per_lot',
        label: 'Fixed per lot',
        description: 'Reserves a fixed currency amount for each executed lot or contract.',
    }),
})

export const BACKTEST_CAPITAL_MODEL_DEFAULTS_BY_ASSET = Object.freeze({
    forex: Object.freeze({
        accountCurrency: 'USD',
        contractSizePerLot: 100000,
        accountLeverage: 50,
        minLot: 0.01,
        lotStep: 0.01,
        maxLot: 100,
        marginLongRate: null,
        marginShortRate: null,
        marginPerLot: null,
        marginModel: 'forex_notional',
        quoteToAccountConversionMode: 'assume_quote_equals_account',
    }),
    b3_equity: Object.freeze({
        accountCurrency: 'BRL',
        contractSizePerLot: 1,
        accountLeverage: 1,
        minLot: 1,
        lotStep: 1,
        maxLot: 1000000,
        marginLongRate: 1,
        marginShortRate: 1,
        marginPerLot: null,
        marginModel: 'cash_notional',
        quoteToAccountConversionMode: 'same_currency',
    }),
    b3_option: Object.freeze({
        accountCurrency: 'BRL',
        contractSizePerLot: 1,
        accountLeverage: 1,
        minLot: 1,
        lotStep: 1,
        maxLot: 1000000,
        marginLongRate: 1,
        marginShortRate: 1,
        marginPerLot: null,
        marginModel: 'cash_notional',
        quoteToAccountConversionMode: 'same_currency',
    }),
    b3_term: Object.freeze({
        accountCurrency: 'BRL',
        contractSizePerLot: 1,
        accountLeverage: 1,
        minLot: 1,
        lotStep: 1,
        maxLot: 1000000,
        marginLongRate: 1,
        marginShortRate: 1,
        marginPerLot: null,
        marginModel: 'cash_notional',
        quoteToAccountConversionMode: 'same_currency',
    }),
    b3_mini_future: Object.freeze({
        accountCurrency: 'BRL',
        contractSizePerLot: 1,
        accountLeverage: 1,
        minLot: 1,
        lotStep: 1,
        maxLot: 500,
        marginLongRate: null,
        marginShortRate: null,
        marginPerLot: 500,
        marginModel: 'fixed_per_lot',
        quoteToAccountConversionMode: 'same_currency',
    }),
})

const MINI_FUTURE_MARGIN_PER_LOT_BY_PREFIX = Object.freeze({
    WIN: 150,
    WDO: 1000,
})

function resolveAssetDefaults(assetType = 'forex', symbol = '') {
    const normalizedAssetType = normalizeLower(assetType) || 'forex'
    const normalizedSymbol = normalizeText(symbol).toUpperCase()
    const defaults = {
        ...(BACKTEST_CAPITAL_MODEL_DEFAULTS_BY_ASSET[normalizedAssetType] || BACKTEST_CAPITAL_MODEL_DEFAULTS_BY_ASSET.forex),
    }
    if (normalizedAssetType === 'b3_mini_future') {
        Object.entries(MINI_FUTURE_MARGIN_PER_LOT_BY_PREFIX).forEach(([prefix, marginPerLot]) => {
            if (normalizedSymbol.startsWith(prefix)) {
                defaults.marginPerLot = marginPerLot
            }
        })
    }
    return defaults
}

export function resolveBacktestCapitalModel(rawCapitalModel = null, {
    assetType = 'forex',
    symbol = '',
    initialBalance = 10000,
} = {}) {
    const defaults = resolveAssetDefaults(assetType, symbol)
    const safeModel = rawCapitalModel && typeof rawCapitalModel === 'object' ? rawCapitalModel : {}
    const marginModel = normalizeLower(safeModel.marginModel || safeModel.margin_model || defaults.marginModel || 'disabled_legacy')
    const normalizedMarginModel = BACKTEST_MARGIN_MODEL_DEFINITIONS[marginModel]
        ? marginModel
        : defaults.marginModel

    const resolved = {
        accountCurrency: normalizeText(safeModel.accountCurrency || safeModel.account_currency || defaults.accountCurrency).toUpperCase() || defaults.accountCurrency,
        initialBalance: toPositiveNumber(safeModel.initialBalance ?? safeModel.initial_balance, toPositiveNumber(initialBalance, 10000)),
        contractSizePerLot: toPositiveNumber(safeModel.contractSizePerLot ?? safeModel.contract_size_per_lot, defaults.contractSizePerLot),
        accountLeverage: toPositiveNumber(safeModel.accountLeverage ?? safeModel.account_leverage, defaults.accountLeverage),
        minLot: toPositiveNumber(safeModel.minLot ?? safeModel.min_lot, defaults.minLot),
        lotStep: toPositiveNumber(safeModel.lotStep ?? safeModel.lot_step, defaults.lotStep),
        maxLot: toPositiveNumber(safeModel.maxLot ?? safeModel.max_lot, defaults.maxLot),
        marginLongRate: toPositiveNumber(safeModel.marginLongRate ?? safeModel.margin_long_rate, defaults.marginLongRate),
        marginShortRate: toPositiveNumber(safeModel.marginShortRate ?? safeModel.margin_short_rate, defaults.marginShortRate),
        marginPerLot: toPositiveNumber(safeModel.marginPerLot ?? safeModel.margin_per_lot, defaults.marginPerLot),
        marginModel: normalizedMarginModel,
        quoteToAccountConversionMode: normalizeText(
            safeModel.quoteToAccountConversionMode
            || safeModel.quote_to_account_conversion_mode
            || defaults.quoteToAccountConversionMode,
        ) || defaults.quoteToAccountConversionMode,
        source: Object.keys(safeModel).length ? 'custom' : 'asset_default',
        assetType: normalizeLower(assetType) || 'forex',
        symbol: normalizeText(symbol).toUpperCase(),
    }

    if (resolved.maxLot < resolved.minLot) {
        resolved.maxLot = resolved.minLot
    }

    if (resolved.marginModel === 'forex_notional' || resolved.marginModel === 'cash_notional') {
        const fallbackRate = resolved.accountLeverage > 0
            ? (1 / resolved.accountLeverage)
            : 1
        resolved.marginLongRate = toPositiveNumber(resolved.marginLongRate, fallbackRate)
        resolved.marginShortRate = toPositiveNumber(resolved.marginShortRate, fallbackRate)
        resolved.marginPerLot = null
    } else if (resolved.marginModel === 'fixed_per_lot') {
        resolved.marginPerLot = toPositiveNumber(resolved.marginPerLot, defaults.marginPerLot)
        resolved.marginLongRate = null
        resolved.marginShortRate = null
    } else {
        resolved.marginLongRate = null
        resolved.marginShortRate = null
        resolved.marginPerLot = null
    }

    return resolved
}

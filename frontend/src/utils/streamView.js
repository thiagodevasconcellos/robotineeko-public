import { normalizeIndicator } from './chartSettings.jsx'

export const STREAM_VIEW_QUERY_PARAM = 'view'
export const STREAM_VIEW_QUERY_VALUE = 'stream'
export const STREAM_LAUNCH_KEY_QUERY_PARAM = 'streamLaunchKey'
export const STREAM_SNAPSHOT_STORAGE_PREFIX = 'robotineeko:stream-launch:'

function normalizeTradeMarketValue(value) {
    return String(value || '').trim().toUpperCase()
}

function normalizeRuntimeIndicators(indicators) {
    return Array.isArray(indicators)
        ? indicators.map((indicator) => normalizeIndicator(indicator))
        : []
}

function normalizeStreamRuntimeMarketSeed(seedLike) {
    const safeSeed = seedLike && typeof seedLike === 'object' ? seedLike : {}
    const symbol = normalizeTradeMarketValue(safeSeed.symbol)
    const timeframe = normalizeTradeMarketValue(safeSeed.timeframe)
    if (!symbol || !timeframe) {
        return null
    }

    return {
        symbol,
        timeframe,
        indicators: normalizeRuntimeIndicators(
            Array.isArray(safeSeed.indicators)
                ? safeSeed.indicators
                : safeSeed?.strategy?.featureManifest?.indicators
        ),
        label: String(safeSeed.label || '').trim(),
        sourceStrategyId: String(safeSeed.sourceStrategyId || safeSeed.source_strategy_id || '').trim(),
    }
}

function pickPrimaryRuntimeSleeve(runtimeLike) {
    const runtime = runtimeLike && typeof runtimeLike === 'object' ? runtimeLike : {}
    const sleeves = Array.isArray(runtime?.sleeves)
        ? runtime.sleeves.filter((entry) => entry && typeof entry === 'object')
        : []
    const primarySleeve = sleeves.find((entry) => entry?.enabled !== false) || sleeves[0] || null
    return normalizeStreamRuntimeMarketSeed(primarySleeve)
}

export function isStreamViewLocation(locationLike) {
    if (!locationLike) {
        return false
    }

    const search = typeof locationLike.search === 'string' ? locationLike.search : ''
    const params = new URLSearchParams(search)
    return params.get(STREAM_VIEW_QUERY_PARAM) === STREAM_VIEW_QUERY_VALUE
}

export function buildStreamLaunchStorageKey() {
    return `${STREAM_SNAPSHOT_STORAGE_PREFIX}${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function buildStreamRuntimeSeed(runtimeLike) {
    return pickPrimaryRuntimeSleeve(runtimeLike)
}

export function resolveStreamRuntimeSeed(runtimeLike, snapshot = null) {
    const liveSeed = buildStreamRuntimeSeed(runtimeLike)
    if (liveSeed) {
        return liveSeed
    }
    return normalizeStreamRuntimeMarketSeed(snapshot?.tradeRuntimeSeed)
}

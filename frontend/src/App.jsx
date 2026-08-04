import { useEffect, useMemo, useRef, useState } from 'react'
import { Chart } from './components/Chart'
import { Console } from './components/Console'
import { IndicatorManager } from './components/IndicatorManager'
import { StreamView } from './components/StreamView'
import { SystemLog } from './components/SystemLog'
import { AuthManager } from './components/AuthManager'
import { MobileTraderView } from './components/MobileTraderView'
import { BACKTEST_DEFAULTS } from './components/Console/backtestDefaults.js'
import {
    buildBacktestCostProfileValues,
    coerceBacktestAssetType,
    mergeBacktestCostProfileValues,
    normalizeBacktestCostProfile,
} from './components/Console/backtestCostProfiles.js'
import {
    buildApiUrl,
    buildWebSocketUrl,
    fetchWithServerRetry,
    setApiBaseOverride,
} from './api'
import {
    areChartSettingsEqual,
    buildBackendIndicatorsPayload,
    normalizeChartSettings,
} from './utils/chartSettings.jsx'
import {
    migrateStrategyFeatureNamesToAliases,
    resolveStrategyAliasesInStrategy,
} from './utils/strategyAliases.jsx'
import {
    buildStrategyCollectionChartSettings,
    normalizeStrategyFeatureManifest,
} from './utils/strategyLibrary.js'
import {
    buildSharedWorkspacePatch,
    DEFAULT_SHARED_BATCH_STATE,
    DEFAULT_LOCAL_DRAWING_UI_STATE,
    DEFAULT_SHARED_WORKSPACE_UI_STATE,
    hasStaleSharedConsoleJobs,
    normalizeSharedWorkspaceUiState,
    pickSharedWorkspaceState,
    sanitizeSharedBatchState,
} from './utils/workspaceState.js'
import {
    buildStreamRuntimeSeed,
    buildStreamLaunchStorageKey,
    isStreamViewLocation,
    STREAM_LAUNCH_KEY_QUERY_PARAM,
    STREAM_VIEW_QUERY_PARAM,
    STREAM_VIEW_QUERY_VALUE,
} from './utils/streamView.js'
import { isMobileViewLocation } from './utils/mobileView.js'
import { TIMEFRAME_OPTIONS } from './utils/timeframes.js'
import {
    findBrokerProfileForSymbol,
    getStoredActiveBrokerProfileSelection,
    inferMarketDomainFromSymbol,
    normalizeBrokerProfileApiBaseUrl,
    normalizeBrokerProfileId,
    normalizeBrokerProfileLabel,
    resolveBrokerProfileMarketDomain,
    normalizeBrokerProfileRecord,
    persistStoredActiveBrokerProfileSelection,
    resolveBrokerProfileApiBaseUrl,
} from './utils/brokerProfiles.js'
import './App.css'

const DOCUMENT_GLOBAL = typeof document !== 'undefined' ? document : null
const INVALID_STOPS_ALERT_TTL_MS = 15000
const PENDING_BROKER_SWITCH_CHART_STORAGE_KEY = 'robotineeko_pending_broker_switch_chart'

const DEFAULT_STRATEGY = {
    long: {
        openPrice: 'close[0]',
        closePrice: 'close[0]',
        openIf: 'False',
        closeIf: 'False',
        gainPrice: '',
        lossPrice: '',
        trailingPrice: '',
    },
    short: {
        openPrice: 'close[0]',
        closePrice: 'close[0]',
        openIf: 'False',
        closeIf: 'False',
        gainPrice: '',
        lossPrice: '',
        trailingPrice: '',
    },
    other: {
        allowInversion: false,
        priority: 'Short',
    },
}

const DEFAULT_CHART_SETTINGS = {
    symbol: 'EURUSD',
    timeframe: 'M1',
    bars: 1000,
    indicators: [],
}

const PROJECT_SNAPSHOT_MIN_BARS = DEFAULT_CHART_SETTINGS.bars

const DEFAULT_BACKTEST = {
    ...BACKTEST_DEFAULTS,
    symbol: DEFAULT_CHART_SETTINGS.symbol,
    timeframe: DEFAULT_CHART_SETTINGS.timeframe,
}

const DEFAULT_TRADE = {
    mode: 'parallel_sleeves',
    executionMode: 'paper',
    activeBrokerProfileId: '',
    activeBrokerProfileLabel: '',
    sameSymbolExecutionPolicy: 'independent',
    portfolioStructureVersion: 1,
    portfolios: [],
    status: 'draft',
    selectedTab: 'setup',
    autoArmOnSave: false,
    latencyBudgetMs: 150,
    sleeves: [],
    runtime: {
        armed: false,
        live: false,
        health: 'idle',
        lastEventAt: null,
        bridgeOnline: null,
        lastError: '',
    },
    audit: {
        events: [],
    },
    historyFilters: {
        rangeKey: '7d',
        customDays: 7,
        strategyFilter: '',
        symbolFilter: '',
        statusFilter: 'all',
    },
}

const DEFAULT_STREAM_LAUNCH_DRAFT = {
    includeBacktest: false,
    replayCandleCount: '',
    initialCapital: '100',
    volumeMode: 'relative_capital',
}

const BROKER_BOOTSTRAP_CHART_SETTINGS = Object.freeze({
    forexcom: {
        symbol: 'EURUSD',
        timeframe: 'M1',
    },
    clear: {
        symbol: 'A1AP34',
        timeframe: 'M5',
    },
})

function normalizeBrokerBootstrapKey(value) {
    return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '')
}

function buildBrokerProfileTransportKey(selection = null) {
    const safeId = normalizeBrokerProfileId(
        selection?.id || selection?.activeBrokerProfileId,
    )
    const safeApiBaseUrl = normalizeBrokerProfileApiBaseUrl(
        selection?.apiBaseUrl || selection?.api_base_url || selection?.profile?.api_base_url || '',
    )

    if (!safeApiBaseUrl) {
        return 'default'
    }

    return `${safeId || 'broker'}|${safeApiBaseUrl}`
}

function readPendingBrokerSwitchChartSettings() {
    if (typeof window === 'undefined') {
        return null
    }

    try {
        const rawValue = window.sessionStorage.getItem(PENDING_BROKER_SWITCH_CHART_STORAGE_KEY)
        if (!rawValue) {
            return null
        }
        const parsed = JSON.parse(rawValue)
        const chartSettings = normalizeChartSettings(parsed?.chartSettings || DEFAULT_CHART_SETTINGS)
        const targetBrokerProfileId = normalizeBrokerProfileId(parsed?.targetBrokerProfileId)
        const targetBrokerLabel = normalizeBrokerProfileLabel(parsed?.targetBrokerLabel)
        const targetMarketDomain = String(parsed?.targetMarketDomain || '').trim().toLowerCase()
        if (!chartSettings.symbol || !chartSettings.timeframe) {
            return null
        }
        return {
            chartSettings,
            targetBrokerProfileId,
            targetBrokerLabel,
            targetMarketDomain,
        }
    } catch {
        return null
    }
}

function clearPendingBrokerSwitchChartSettings() {
    if (typeof window === 'undefined') {
        return
    }

    window.sessionStorage.removeItem(PENDING_BROKER_SWITCH_CHART_STORAGE_KEY)
}

function persistPendingBrokerSwitchChartSettings(settings = null, targetProfile = null) {
    if (typeof window === 'undefined') {
        return
    }

    const normalizedSettings = normalizeChartSettings(settings || DEFAULT_CHART_SETTINGS)
    const normalizedTargetProfileId = normalizeBrokerProfileId(targetProfile?.id)
    const normalizedTargetBrokerLabel = normalizeBrokerProfileLabel(
        targetProfile?.label || '',
        targetProfile?.broker_code || targetProfile?.brokerCode || '',
    )
    const normalizedTargetMarketDomain = resolveBrokerProfileMarketDomain(targetProfile)
    window.sessionStorage.setItem(
        PENDING_BROKER_SWITCH_CHART_STORAGE_KEY,
        JSON.stringify({
            chartSettings: sanitizeWorkspaceChartSettingsForPersistence(normalizedSettings),
            targetBrokerProfileId: normalizedTargetProfileId,
            targetBrokerLabel: normalizedTargetBrokerLabel,
            targetMarketDomain: normalizedTargetMarketDomain,
        })
    )
}

function hasMeaningfulBrokerSelection(selection = null) {
    return Boolean(
        normalizeBrokerProfileId(selection?.id || selection?.activeBrokerProfileId)
        || normalizeBrokerProfileLabel(
            selection?.label || selection?.activeBrokerProfileLabel,
            selection?.broker_code || selection?.brokerCode || '',
        )
    )
}

function resolveBrokerBootstrapChartSettings(selection = null, fallbackBars = DEFAULT_CHART_SETTINGS.bars) {
    const normalizedLabel = normalizeBrokerProfileLabel(
        selection?.label || selection?.activeBrokerProfileLabel || '',
        selection?.broker_code || selection?.brokerCode || '',
    )
    const normalizedCodeKey = normalizeBrokerBootstrapKey(
        selection?.broker_code || selection?.brokerCode || '',
    )
    const normalizedLabelKey = normalizeBrokerBootstrapKey(normalizedLabel)
    const preset = BROKER_BOOTSTRAP_CHART_SETTINGS[normalizedCodeKey]
        || BROKER_BOOTSTRAP_CHART_SETTINGS[normalizedLabelKey]
        || DEFAULT_CHART_SETTINGS

    return normalizeChartSettings({
        ...DEFAULT_CHART_SETTINGS,
        ...preset,
        bars: Math.max(1, Number(fallbackBars || DEFAULT_CHART_SETTINGS.bars) || DEFAULT_CHART_SETTINGS.bars),
        indicators: [],
    })
}

function buildBrokerFallbackChartSettings(selection = null, baseChartSettings = DEFAULT_CHART_SETTINGS) {
    const normalizedBaseSettings = normalizeChartSettings(baseChartSettings || DEFAULT_CHART_SETTINGS)
    const bootstrapSettings = resolveBrokerBootstrapChartSettings(
        selection,
        normalizedBaseSettings.bars || DEFAULT_CHART_SETTINGS.bars,
    )

    return normalizeChartSettings({
        ...normalizedBaseSettings,
        symbol: bootstrapSettings.symbol,
        timeframe: normalizedBaseSettings.timeframe || bootstrapSettings.timeframe,
        bars: normalizedBaseSettings.bars || bootstrapSettings.bars,
    })
}

function resolveBrokerCompatibleChartSettings(selection = null, baseChartSettings = DEFAULT_CHART_SETTINGS, catalog = null) {
    const normalizedBaseSettings = normalizeChartSettings(baseChartSettings || DEFAULT_CHART_SETTINGS)
    const currentSymbol = String(normalizedBaseSettings.symbol || '').trim().toUpperCase()
    const targetMarketDomain = resolveBrokerProfileMarketDomain(selection)
    const currentChartMarketDomain = inferMarketDomainFromSymbol(currentSymbol)
    const catalogSymbols = Array.isArray(catalog?.symbols)
        ? catalog.symbols.map((entry) => String(entry || '').trim().toUpperCase()).filter(Boolean)
        : []
    const missingFromCatalog = Boolean(
        catalog?.exhaustive
        && currentSymbol
        && catalogSymbols.length
        && !catalogSymbols.includes(currentSymbol)
    )
    const incompatibleChartMarket = Boolean(
        targetMarketDomain
        && currentChartMarketDomain
        && currentChartMarketDomain !== 'mixed'
        && currentChartMarketDomain !== targetMarketDomain
    )

    if (!missingFromCatalog && !incompatibleChartMarket) {
        return {
            chartSettings: normalizedBaseSettings,
            reason: '',
        }
    }

    return {
        chartSettings: buildBrokerFallbackChartSettings(selection, normalizedBaseSettings),
        reason: incompatibleChartMarket
            ? 'incompatible_chart_symbol'
            : 'missing_chart_symbol',
    }
}

function buildBrokerBootstrapWorkspaceState(state, selection = null) {
    const safeState = state && typeof state === 'object' ? state : {}
    const safeTrade = safeState.trade && typeof safeState.trade === 'object'
        ? safeState.trade
        : DEFAULT_TRADE
    const targetId = normalizeBrokerProfileId(
        selection?.id || selection?.activeBrokerProfileId || safeTrade.activeBrokerProfileId,
    )
    const targetLabel = normalizeBrokerProfileLabel(
        selection?.label || selection?.activeBrokerProfileLabel || safeTrade.activeBrokerProfileLabel,
        selection?.broker_code || selection?.brokerCode || safeTrade?.broker_code || '',
    )
    const bootstrapChartSettings = resolveBrokerBootstrapChartSettings(
        {
            ...safeTrade,
            ...(selection && typeof selection === 'object' ? selection : {}),
        },
        DEFAULT_CHART_SETTINGS.bars,
    )

    return {
        ...safeState,
        chartSettings: bootstrapChartSettings,
        backtest: mergeBacktestDefaults(
            safeState.backtest,
            bootstrapChartSettings,
            selection && typeof selection === 'object'
                ? {
                    broker_code: selection.broker_code || selection.brokerCode || '',
                    market_domain: selection.market_domain || selection.marketDomain || '',
                    profile: selection.profile && typeof selection.profile === 'object'
                        ? selection.profile
                        : {},
                }
                : null,
        ),
        trade: {
            ...DEFAULT_TRADE,
            ...safeTrade,
            activeBrokerProfileId: targetId || DEFAULT_TRADE.activeBrokerProfileId,
            activeBrokerProfileLabel: targetLabel || DEFAULT_TRADE.activeBrokerProfileLabel,
            runtime: {
                ...DEFAULT_TRADE.runtime,
            },
            audit: {
                events: [],
            },
        },
        drawings: [],
        visibleIndicatorColumns: {},
        strategyResponse: null,
        backtestRunResponse: null,
        backtestChartBuffer: null,
        chartBacktestOverlay: null,
    }
}

function resolveBrokerProfileContextFromTradeSelection(tradeSelection = null, availableBrokerProfiles = [], fallbackProfile = null) {
    const safeTradeSelection = tradeSelection && typeof tradeSelection === 'object'
        ? tradeSelection
        : {}
    const safeBrokerProfiles = Array.isArray(availableBrokerProfiles)
        ? availableBrokerProfiles
        : []
    const targetId = normalizeBrokerProfileId(safeTradeSelection.activeBrokerProfileId)
    const targetLabel = normalizeBrokerProfileLabel(safeTradeSelection.activeBrokerProfileLabel)
    return safeBrokerProfiles.find((entry) => entry.id === targetId)
        || safeBrokerProfiles.find((entry) => normalizeBrokerProfileLabel(entry?.label) === targetLabel)
        || fallbackProfile
        || null
}

function resolveChartBrokerBootstrapKey(settings = null) {
    const normalizedSymbol = String(settings?.symbol || '').trim().toUpperCase()
    const normalizedTimeframe = String(settings?.timeframe || '').trim().toUpperCase()
    if (!normalizedSymbol || !normalizedTimeframe) {
        return ''
    }

    for (const [key, preset] of Object.entries(BROKER_BOOTSTRAP_CHART_SETTINGS)) {
        const normalizedPreset = normalizeChartSettings({
            ...DEFAULT_CHART_SETTINGS,
            ...preset,
        })
        if (
            normalizedPreset.symbol === normalizedSymbol
            && normalizedPreset.timeframe === normalizedTimeframe
        ) {
            return key
        }
    }

    return ''
}

function normalizeChartMarkerTime(value) {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) {
        return null
    }
    const normalized = Math.abs(parsed) >= 100_000_000_000 ? (parsed / 1000) : parsed
    const rounded = Math.trunc(normalized)
    if (!Number.isFinite(rounded) || rounded <= 0) {
        return null
    }
    return rounded
}

function alignMarkerTimestampToTimeframe(value, timeframeSeconds) {
    const normalized = normalizeChartMarkerTime(value)
    if (normalized === null) {
        return null
    }
    if (!Number.isFinite(timeframeSeconds) || timeframeSeconds <= 0) {
        return normalized
    }
    return Math.floor(normalized / timeframeSeconds) * timeframeSeconds
}

function buildTradeRuntimeMarkerCandidateTimes(entry, timeframeSeconds) {
    const candidates = []
    const seen = new Set()
    const pushCandidate = (value) => {
        const normalized = alignMarkerTimestampToTimeframe(value, timeframeSeconds)
        if (normalized === null || seen.has(normalized)) {
            return
        }
        seen.add(normalized)
        candidates.push(normalized)
    }

    pushCandidate(entry?.bar_time)
    pushCandidate(entry?.filled_at)
    pushCandidate(entry?.acknowledged_at)
    pushCandidate(entry?.claimed_at)
    pushCandidate(entry?.dispatched_at)
    pushCandidate(entry?.created_at)

    return candidates
}

function toFiniteBacktestNumber(value) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
}

function cloneSerializable(value, fallback = null) {
    try {
        if (typeof structuredClone === 'function') {
            return structuredClone(value)
        }
        return JSON.parse(JSON.stringify(value))
    } catch {
        return fallback
    }
}

function normalizeTradeMarketValue(value, fallback = '') {
    return String(value || fallback || '').trim().toUpperCase()
}

function buildStableComparableValue(value) {
    if (Array.isArray(value)) {
        return value.map((entry) => buildStableComparableValue(entry))
    }
    if (value && typeof value === 'object') {
        return Object.keys(value)
            .sort((left, right) => left.localeCompare(right))
            .reduce((accumulator, key) => {
                accumulator[key] = buildStableComparableValue(value[key])
                return accumulator
            }, {})
    }
    return value
}

function buildComparableSignature(value) {
    try {
        return JSON.stringify(buildStableComparableValue(value))
    } catch {
        return ''
    }
}

function normalizeComparableStrategyPayload(strategy) {
    const safeStrategy = strategy && typeof strategy === 'object'
        ? cloneSerializable(strategy, {})
        : {}

    return {
        long: safeStrategy?.long && typeof safeStrategy.long === 'object' ? safeStrategy.long : {},
        short: safeStrategy?.short && typeof safeStrategy.short === 'object' ? safeStrategy.short : {},
        other: safeStrategy?.other && typeof safeStrategy.other === 'object' ? safeStrategy.other : {},
    }
}

function buildComparableStrategySignature(strategy, {
    symbol = '',
    timeframe = '',
    indicators = [],
} = {}) {
    const safeStrategy = strategy && typeof strategy === 'object'
        ? strategy
        : null
    if (!safeStrategy) {
        return ''
    }

    const aliasChartSettings = normalizeChartSettings({
        symbol: normalizeTradeMarketValue(symbol),
        timeframe: normalizeTradeMarketValue(timeframe),
        indicators: Array.isArray(indicators) ? indicators : [],
    })
    const migratedStrategy = migrateStrategyFeatureNamesToAliases(safeStrategy, aliasChartSettings)
    return buildComparableSignature(normalizeComparableStrategyPayload(migratedStrategy))
}

function buildComparableIndicatorsSignature(indicators) {
    return buildComparableSignature(
        buildBackendIndicatorsPayload(Array.isArray(indicators) ? indicators : [])
    )
}

function ensureDocumentMetaTag(name) {
    if (!DOCUMENT_GLOBAL) {
        return null
    }
    let tag = DOCUMENT_GLOBAL.head?.querySelector(`meta[name="${name}"]`) || null
    if (!tag) {
        tag = DOCUMENT_GLOBAL.createElement('meta')
        tag.setAttribute('name', name)
        DOCUMENT_GLOBAL.head?.appendChild(tag)
    }
    return tag
}

function applySiteMetadata({ title, description, robots }) {
    if (!DOCUMENT_GLOBAL) {
        return
    }

    if (title) {
        DOCUMENT_GLOBAL.title = title
    }

    if (description) {
        const descriptionTag = ensureDocumentMetaTag('description')
        if (descriptionTag) {
            descriptionTag.setAttribute('content', description)
        }
    }

    if (robots) {
        const robotsTag = ensureDocumentMetaTag('robots')
        if (robotsTag) {
            robotsTag.setAttribute('content', robots)
        }
    }
}

function buildComparableRuntimeEntries(runtimeLike) {
    const runtime = runtimeLike && typeof runtimeLike === 'object' ? runtimeLike : {}
    const sleeves = Array.isArray(runtime?.sleeves)
        ? runtime.sleeves.filter((entry) => entry && typeof entry === 'object' && entry?.enabled !== false)
        : []

    return sleeves
        .map((entry, index) => {
            const strategy = entry?.strategy && typeof entry.strategy === 'object' ? entry.strategy : null
            const indicators = Array.isArray(entry?.indicators) && entry.indicators.length
                ? entry.indicators
                : strategy?.featureManifest?.indicators
            const symbol = normalizeTradeMarketValue(entry?.symbol)
            const timeframe = normalizeTradeMarketValue(entry?.timeframe)
            return {
                id: String(entry?.id || `runtime-entry-${index + 1}`).trim() || `runtime-entry-${index + 1}`,
                label: String(entry?.label || entry?.sourceStrategyId || `Sleeve ${index + 1}`).trim() || `Sleeve ${index + 1}`,
                symbol,
                timeframe,
                strategySignature: buildComparableStrategySignature(strategy, {
                    symbol,
                    timeframe,
                    indicators,
                }),
                indicatorsSignature: buildComparableIndicatorsSignature(indicators),
            }
        })
        .filter((entry) => entry.symbol && entry.timeframe)
}

function buildComparableBacktestEntries(response) {
    const request = response?.request && typeof response.request === 'object' ? response.request : {}
    const requestStrategies = Array.isArray(request?.strategies)
        ? request.strategies.filter((entry) => entry && typeof entry === 'object')
        : []
    const rawEntries = requestStrategies.length
        ? requestStrategies
        : (
            request?.strategy && typeof request.strategy === 'object'
                ? [{
                    id: 'backtest-primary',
                    label: 'Backtest primary',
                    symbol: request?.symbol,
                    timeframe: request?.timeframe,
                    strategy: request.strategy,
                }]
                : []
        )

    return rawEntries
        .map((entry, index) => {
            const symbol = normalizeTradeMarketValue(entry?.symbol || request?.symbol)
            const timeframe = normalizeTradeMarketValue(entry?.timeframe || request?.timeframe)
            const strategy = entry?.strategy && typeof entry.strategy === 'object' ? entry.strategy : null
            const indicators = Array.isArray(strategy?.featureManifest?.indicators) && strategy.featureManifest.indicators.length
                ? strategy.featureManifest.indicators
                : request?.indicators
            return {
                id: String(entry?.id || `backtest-entry-${index + 1}`).trim() || `backtest-entry-${index + 1}`,
                label: String(entry?.label || `Backtest entry ${index + 1}`).trim() || `Backtest entry ${index + 1}`,
                symbol,
                timeframe,
                strategySignature: buildComparableStrategySignature(strategy, {
                    symbol,
                    timeframe,
                    indicators,
                }),
            }
        })
        .filter((entry) => entry.symbol && entry.timeframe)
}

function buildStreamLaunchSetup(runtimeLike, fallbackChartSettings) {
    const normalizedFallback = normalizeChartSettings(fallbackChartSettings)
    const runtimeEntries = buildComparableRuntimeEntries(runtimeLike)
    const runtimeSeed = buildStreamRuntimeSeed(runtimeLike)
    const primaryRuntimeEntry = runtimeEntries[0] || null

    return {
        entries: runtimeEntries,
        entryCount: runtimeEntries.length,
        primarySymbol: primaryRuntimeEntry?.symbol || normalizeTradeMarketValue(runtimeSeed?.symbol || normalizedFallback?.symbol),
        primaryTimeframe: primaryRuntimeEntry?.timeframe || normalizeTradeMarketValue(runtimeSeed?.timeframe || normalizedFallback?.timeframe),
        primaryStrategySignature: primaryRuntimeEntry?.strategySignature || '',
    }
}

function hasStreamRuntimeMarketSetup(runtimeLike) {
    if (buildComparableRuntimeEntries(runtimeLike).length > 0) {
        return true
    }

    const runtimeSeed = buildStreamRuntimeSeed(runtimeLike)
    return Boolean(runtimeSeed?.symbol && runtimeSeed?.timeframe)
}

function buildBacktestLaunchSetup(response) {
    const hydratedResponse = hydrateBacktestResponsePayload(response)
    const runContext = extractBacktestRunMarketContext(hydratedResponse)
    const backtestEntries = buildComparableBacktestEntries(hydratedResponse)
    const primaryBacktestEntry = backtestEntries[0] || null

    return {
        entries: backtestEntries,
        entryCount: backtestEntries.length,
        primarySymbol: primaryBacktestEntry?.symbol || runContext.symbol,
        primaryTimeframe: primaryBacktestEntry?.timeframe || runContext.timeframe,
        primaryStrategySignature: primaryBacktestEntry?.strategySignature || '',
    }
}

function evaluateStreamBacktestSourceCompatibility(response, runtimeLike, fallbackChartSettings) {
    const hydratedResponse = hydrateBacktestResponsePayload(response)
    const streamSetup = buildStreamLaunchSetup(runtimeLike, fallbackChartSettings)
    const backtestSetup = buildBacktestLaunchSetup(hydratedResponse)
    const reasons = []

    const streamMarketLabel = [streamSetup.primarySymbol, streamSetup.primaryTimeframe].filter(Boolean).join(' ')
    const backtestMarketLabel = [backtestSetup.primarySymbol, backtestSetup.primaryTimeframe].filter(Boolean).join(' ')

    if (
        streamSetup.primarySymbol
        && streamSetup.primaryTimeframe
        && (
            streamSetup.primarySymbol !== backtestSetup.primarySymbol
            || streamSetup.primaryTimeframe !== backtestSetup.primaryTimeframe
        )
    ) {
        reasons.push(`The loaded backtest market is ${backtestMarketLabel || 'unknown'} but the stream will launch on ${streamMarketLabel || 'unknown'}.`)
    }

    if (
        streamSetup.entryCount > 0
        && backtestSetup.entryCount > 0
        && streamSetup.entryCount !== backtestSetup.entryCount
    ) {
        reasons.push(`The loaded backtest uses ${backtestSetup.entryCount} setup item(s) but the current stream setup uses ${streamSetup.entryCount}.`)
    } else if (
        streamSetup.entryCount > 0
        && backtestSetup.entryCount > 0
        && streamSetup.entryCount === backtestSetup.entryCount
    ) {
        const mismatchIndex = streamSetup.entries.findIndex((entry, index) => {
            const candidate = backtestSetup.entries[index]
            if (!candidate) {
                return true
            }
            if (entry.symbol !== candidate.symbol || entry.timeframe !== candidate.timeframe) {
                return true
            }
            if (entry.strategySignature && candidate.strategySignature && entry.strategySignature !== candidate.strategySignature) {
                return true
            }
            return false
        })

        if (mismatchIndex >= 0) {
            reasons.push('The loaded backtest strategies do not match the current Trader setup.')
        }
    } else if (
        streamSetup.primaryStrategySignature
        && backtestSetup.primaryStrategySignature
        && streamSetup.primaryStrategySignature !== backtestSetup.primaryStrategySignature
    ) {
        reasons.push('The loaded backtest strategy does not match the current stream setup.')
    }

    return {
        compatible: reasons.length === 0,
        reason: reasons[0] || '',
        streamMarketLabel,
        backtestMarketLabel,
        response: hydratedResponse,
    }
}

function extractFiniteSeriesFromResults(results, fields = []) {
    const series = []

    for (const row of Array.isArray(results) ? results : []) {
        let nextValue = null

        for (const field of fields) {
            const value = toFiniteBacktestNumber(row?.[field])
            if (value !== null) {
                nextValue = value
                break
            }
        }

        if (nextValue !== null) {
            series.push(nextValue)
        }
    }

    return series
}

function buildDrawdownSeriesFromBalance(accountBalanceSeries = []) {
    const safeSeries = Array.isArray(accountBalanceSeries)
        ? accountBalanceSeries.map((value) => toFiniteBacktestNumber(value)).filter((value) => value !== null)
        : []

    if (!safeSeries.length) {
        return {
            drawdownAmountSeries: [],
            drawdownPctSeries: [],
        }
    }

    let rollingPeak = safeSeries[0]
    const drawdownAmountSeries = []
    const drawdownPctSeries = []

    for (const balance of safeSeries) {
        rollingPeak = Math.max(rollingPeak, balance)
        const drawdownAmount = Math.max(0, rollingPeak - balance)
        const drawdownPct = rollingPeak > 0 ? (drawdownAmount / rollingPeak) : 0
        drawdownAmountSeries.push(drawdownAmount)
        drawdownPctSeries.push(drawdownPct)
    }

    return {
        drawdownAmountSeries,
        drawdownPctSeries,
    }
}

function isFiniteBacktestNumber(value) {
    return Number.isFinite(Number(value))
}

function assignStatIfMissing(stats, key, value) {
    if (!stats || !key) {
        return
    }

    const current = stats[key]
    const currentFinite = toFiniteBacktestNumber(current)
    const valueIsArray = Array.isArray(value)
    const valueIsNumeric = typeof value === 'number'

    const currentArrayHasFiniteValues = Array.isArray(current)
        ? current.some((entry) => toFiniteBacktestNumber(entry) !== null)
        : false
    const valueArrayHasFiniteValues = valueIsArray
        ? value.some((entry) => toFiniteBacktestNumber(entry) !== null)
        : false

    const isMissing = (
        current === undefined
        || current === null
        || (typeof current === 'number' && !Number.isFinite(current))
        || (valueIsNumeric && currentFinite === null)
        || (valueIsArray && (
            !Array.isArray(current)
            || (current.length === 0 && value.length > 0)
            || (!currentArrayHasFiniteValues && valueArrayHasFiniteValues)
        ))
    )

    if (isMissing && value !== undefined && value !== null) {
        stats[key] = value
    }
}

function deriveBacktestStatsFallback(results = [], stats = {}) {
    const safeResults = Array.isArray(results) ? results : []
    const safeStats = stats && typeof stats === 'object' ? stats : {}

    let grossProfit = 0
    let grossLoss = 0
    let grossPnl = 0
    let netProfit = 0
    let netLoss = 0
    let netPnl = 0
    let totalCost = 0
    let nTrades = 0
    let nGrossProfits = 0
    let nGrossLosses = 0
    let nNetProfits = 0
    let nNetLosses = 0

    for (const row of safeResults) {
        const gross = toFiniteBacktestNumber(row?.trade_gross_pnl) ?? 0
        const net = toFiniteBacktestNumber(row?.trade_net_pnl) ?? 0
        const cost = toFiniteBacktestNumber(row?.trade_cost) ?? 0
        const hasTrade = Math.abs(gross) > 0 || Math.abs(net) > 0 || Math.abs(cost) > 0

        grossPnl += gross
        netPnl += net
        totalCost += cost

        if (gross > 0) {
            grossProfit += gross
            nGrossProfits += 1
        } else if (gross < 0) {
            grossLoss += gross
            nGrossLosses += 1
        }

        if (net > 0) {
            netProfit += net
            nNetProfits += 1
        } else if (net < 0) {
            netLoss += net
            nNetLosses += 1
        }

        if (hasTrade) {
            nTrades += 1
        }
    }

    const accountBalanceSeries = (
        Array.isArray(safeStats.account_balance_series) ? safeStats.account_balance_series : []
    )
        .map((value) => toFiniteBacktestNumber(value))
        .filter((value) => value !== null)

    const initialBalance = accountBalanceSeries.length
        ? accountBalanceSeries[0]
        : (toFiniteBacktestNumber(safeStats.initial_balance) ?? 0)
    const finalBalance = accountBalanceSeries.length
        ? accountBalanceSeries[accountBalanceSeries.length - 1]
        : (toFiniteBacktestNumber(safeStats.final_balance) ?? initialBalance)
    const accountBalanceChange = finalBalance - initialBalance

    const avgGrossProfit = nGrossProfits > 0 ? (grossProfit / nGrossProfits) : 0
    const avgGrossLoss = nGrossLosses > 0 ? (grossLoss / nGrossLosses) : 0
    const avgNetProfit = nNetProfits > 0 ? (netProfit / nNetProfits) : 0
    const avgNetLoss = nNetLosses > 0 ? (netLoss / nNetLosses) : 0
    const winRate = nTrades > 0 ? (nNetProfits / nTrades) : 0
    const lossRate = nTrades > 0 ? (nNetLosses / nTrades) : 0
    const grossProfitFactor = grossLoss !== 0 ? Math.abs(grossProfit / grossLoss) : 0
    const netProfitFactor = netLoss !== 0 ? Math.abs(netProfit / netLoss) : 0
    const riskRewardRatio = avgNetLoss !== 0 ? Math.abs(avgNetProfit / avgNetLoss) : 0
    const expectancyPerTrade = nTrades > 0 ? (netPnl / nTrades) : 0
    const costFactor = grossPnl !== 0 ? Math.abs(totalCost / grossPnl) : 0
    const kellyFraction = (riskRewardRatio > 0 && nTrades > 0)
        ? (winRate - ((1 - winRate) / riskRewardRatio))
        : 0

    const { drawdownAmountSeries, drawdownPctSeries } = buildDrawdownSeriesFromBalance(accountBalanceSeries)
    const maxDrawdown = drawdownAmountSeries.length ? Math.max(...drawdownAmountSeries) : 0
    const maxDrawdownPct = drawdownPctSeries.length ? Math.max(...drawdownPctSeries) : 0
    const recoveryFactor = maxDrawdown > 0 ? Math.abs(netPnl / maxDrawdown) : 0

    const returns = []
    for (let index = 1; index < accountBalanceSeries.length; index += 1) {
        const previous = accountBalanceSeries[index - 1]
        const current = accountBalanceSeries[index]
        if (!isFiniteBacktestNumber(previous) || !isFiniteBacktestNumber(current) || previous <= 0) {
            continue
        }
        returns.push((current - previous) / previous)
    }

    const avgReturn = returns.length
        ? (returns.reduce((sum, value) => sum + value, 0) / returns.length)
        : 0
    const returnVariance = returns.length
        ? (returns.reduce((sum, value) => sum + ((value - avgReturn) ** 2), 0) / returns.length)
        : 0
    const returnStd = Math.sqrt(Math.max(0, returnVariance))
    const downside = returns.filter((value) => value < 0)
    const downsideAvg = downside.length
        ? (downside.reduce((sum, value) => sum + value, 0) / downside.length)
        : 0
    const downsideVariance = downside.length
        ? (downside.reduce((sum, value) => sum + ((value - downsideAvg) ** 2), 0) / downside.length)
        : 0
    const downsideStd = Math.sqrt(Math.max(0, downsideVariance))
    const sharpeRatio = returnStd > 0 ? (avgReturn / returnStd) : 0
    const sortinoRatio = downsideStd > 0 ? (avgReturn / downsideStd) : 0

    return {
        initial_balance: initialBalance,
        final_balance: finalBalance,
        account_balance_change: accountBalanceChange,
        gross_profit: grossProfit,
        gross_loss: grossLoss,
        gross_pnl: grossPnl,
        net_profit: netProfit,
        net_loss: netLoss,
        net_pnl: netPnl,
        total_cost: totalCost,
        n_trades: nTrades,
        n_gross_profits: nGrossProfits,
        n_gross_losses: nGrossLosses,
        n_net_profits: nNetProfits,
        n_net_losses: nNetLosses,
        avg_gross_profit: avgGrossProfit,
        avg_gross_loss: avgGrossLoss,
        avg_net_profit: avgNetProfit,
        avg_net_loss: avgNetLoss,
        win_rate: winRate,
        loss_rate: lossRate,
        gross_profit_factor: grossProfitFactor,
        net_profit_factor: netProfitFactor,
        risk_reward_ratio: riskRewardRatio,
        expectancy_per_trade: expectancyPerTrade,
        cost_factor: costFactor,
        kelly_fraction: kellyFraction,
        account_balance_series: accountBalanceSeries,
        drawdown_amount_series: drawdownAmountSeries,
        drawdown_pct_series: drawdownPctSeries,
        max_drawdown: maxDrawdown,
        max_drawdown_pct: maxDrawdownPct,
        recovery_factor: recoveryFactor,
        avg_return: avgReturn,
        return_std: returnStd,
        downside_std: downsideStd,
        sharpe_ratio: sharpeRatio,
        sortino_ratio: sortinoRatio,
    }
}

function buildFallbackTradeMarkersFromResults(results = []) {
    const markers = []

    for (let index = 0; index < (Array.isArray(results) ? results.length : 0); index += 1) {
        const row = results[index]
        const time = normalizeChartMarkerTime(row?.time)

        if (time === null) {
            continue
        }

        if (Number(row?.long_entry_flag || 0) === 1) {
            markers.push({
                id: `fallback-long-open-${index}`,
                time,
                position: 'belowBar',
                shape: 'arrowUp',
                color: '#f3f4f6',
                text: `Long open @ ${toFiniteBacktestNumber(row?.long_open_price) ?? '-'}`,
                size: 1,
            })
        }

        if (Number(row?.short_entry_flag || 0) === 1) {
            markers.push({
                id: `fallback-short-open-${index}`,
                time,
                position: 'aboveBar',
                shape: 'arrowDown',
                color: '#f3f4f6',
                text: `Short open @ ${toFiniteBacktestNumber(row?.short_open_price) ?? '-'}`,
                size: 1,
            })
        }

        if (Number(row?.long_exit_flag || 0) === 1) {
            const pnl = toFiniteBacktestNumber(row?.trade_net_pnl)
            markers.push({
                id: `fallback-long-close-${index}`,
                time,
                position: 'aboveBar',
                shape: 'square',
                color: pnl !== null && pnl < 0 ? '#ef4444' : '#22c55e',
                text: `Long close | Net ${pnl !== null ? pnl.toFixed(2) : '-'}`,
                size: 1,
            })
        }

        if (Number(row?.short_exit_flag || 0) === 1) {
            const pnl = toFiniteBacktestNumber(row?.trade_net_pnl)
            markers.push({
                id: `fallback-short-close-${index}`,
                time,
                position: 'belowBar',
                shape: 'square',
                color: pnl !== null && pnl < 0 ? '#ef4444' : '#22c55e',
                text: `Short close | Net ${pnl !== null ? pnl.toFixed(2) : '-'}`,
                size: 1,
            })
        }
    }

    return markers
}

function hydrateBacktestResponsePayload(response) {
    if (!response || typeof response !== 'object') {
        return response
    }

    const nextResponse = {
        ...response,
    }
    const nextStats = {
        ...(response?.stats && typeof response.stats === 'object' ? response.stats : {}),
    }
    const results = Array.isArray(response?.results) ? response.results : []

    const accountBalanceSeries = (
        Array.isArray(nextStats.account_balance_series)
            ? nextStats.account_balance_series
            : []
    )
        .map((value) => toFiniteBacktestNumber(value))
        .filter((value) => value !== null)

    if (!accountBalanceSeries.length) {
        const derivedAccountBalanceSeries = extractFiniteSeriesFromResults(results, ['account_balance', 'equity', 'balance'])
        if (derivedAccountBalanceSeries.length) {
            nextStats.account_balance_series = derivedAccountBalanceSeries
        }
    }

    const hasDrawdownAmountSeries = Array.isArray(nextStats.drawdown_amount_series) && nextStats.drawdown_amount_series.length > 0
    const hasDrawdownPctSeries = Array.isArray(nextStats.drawdown_pct_series) && nextStats.drawdown_pct_series.length > 0

    if (!hasDrawdownAmountSeries || !hasDrawdownPctSeries) {
        const baseSeries = Array.isArray(nextStats.account_balance_series) && nextStats.account_balance_series.length
            ? nextStats.account_balance_series
            : extractFiniteSeriesFromResults(results, ['account_balance', 'equity', 'balance'])
        const { drawdownAmountSeries, drawdownPctSeries } = buildDrawdownSeriesFromBalance(baseSeries)
        if (!hasDrawdownAmountSeries && drawdownAmountSeries.length) {
            nextStats.drawdown_amount_series = drawdownAmountSeries
        }
        if (!hasDrawdownPctSeries && drawdownPctSeries.length) {
            nextStats.drawdown_pct_series = drawdownPctSeries
        }
    }

    if (!Array.isArray(nextResponse.trade_markers) || nextResponse.trade_markers.length === 0) {
        const fallbackTradeMarkers = buildFallbackTradeMarkersFromResults(results)
        if (fallbackTradeMarkers.length) {
            nextResponse.trade_markers = fallbackTradeMarkers
        }
    }

    const derivedStats = deriveBacktestStatsFallback(results, nextStats)
    for (const [key, value] of Object.entries(derivedStats)) {
        assignStatIfMissing(nextStats, key, value)
    }

    nextResponse.stats = nextStats
    return nextResponse
}

function extractBacktestRunMarketContext(response) {
    const request = response?.request && typeof response.request === 'object' ? response.request : {}
    const runtimeMarket = response?.runtime?.market && typeof response.runtime.market === 'object'
        ? response.runtime.market
        : {}
    const strategyViewMeta = response?.strategy_view_meta && typeof response.strategy_view_meta === 'object'
        ? response.strategy_view_meta
        : {}

    return {
        symbol: String(
            request?.symbol
            || runtimeMarket?.symbol
            || strategyViewMeta?.symbol
            || ''
        ).trim().toUpperCase(),
        timeframe: String(
            request?.timeframe
            || runtimeMarket?.timeframe
            || strategyViewMeta?.timeframe
            || ''
        ).trim().toUpperCase(),
    }
}

function getTimeframeDurationMinutes(value) {
    const text = String(value || '').trim().toUpperCase()
    const match = text.match(/^([A-Z]+)(\d+)$/)
    if (!match) {
        return null
    }

    const unit = match[1]
    const amount = Number(match[2] || 0)
    if (!Number.isFinite(amount) || amount <= 0) {
        return null
    }

    switch (unit) {
    case 'M':
        return amount
    case 'H':
        return amount * 60
    case 'D':
        return amount * 1440
    case 'W':
        return amount * 10080
    case 'MN':
        return amount * 43200
    default:
        return null
    }
}

function buildBacktestMarketWindowSummary(response) {
    if (!response || typeof response !== 'object') {
        return null
    }

    if (response?.market_window && typeof response.market_window === 'object') {
        return response.market_window
    }

    const request = response?.request && typeof response.request === 'object' ? response.request : {}
    const strategyViewMeta = response?.strategy_view_meta && typeof response.strategy_view_meta === 'object'
        ? response.strategy_view_meta
        : {}
    const timeframe = String(
        request?.timeframe
        || strategyViewMeta?.timeframe
        || ''
    ).trim().toUpperCase()
    const timeframeMinutes = getTimeframeDurationMinutes(timeframe)
    const timeframeSeconds = timeframeMinutes !== null ? timeframeMinutes * 60 : null
    const bars = Math.max(
        0,
        Number(request?.bars || strategyViewMeta?.bars || 0) || 0,
    )
    const results = Array.isArray(response?.results) ? response.results : []
    const candleTimes = results
        .map((row) => Number(row?.time))
        .filter((value) => Number.isFinite(value))

    if (candleTimes.length > 0) {
        const firstCandleTime = Math.min(...candleTimes)
        const lastCandleTime = Math.max(...candleTimes)
        return {
            first_candle_time: firstCandleTime,
            last_candle_time: lastCandleTime,
            inclusive_duration_seconds: Math.max(
                0,
                lastCandleTime - firstCandleTime + Math.max(0, timeframeSeconds || 0),
            ),
            timeframe,
            timeframe_minutes: timeframeMinutes,
            bars,
        }
    }

    if (bars > 0 && timeframeSeconds !== null) {
        return {
            first_candle_time: null,
            last_candle_time: null,
            inclusive_duration_seconds: bars * timeframeSeconds,
            timeframe,
            timeframe_minutes: timeframeMinutes,
            bars,
        }
    }

    return null
}

function normalizeBacktestChartBuffer(payload) {
    if (!payload || typeof payload !== 'object') {
        return null
    }

    const markers = Array.isArray(payload?.markers)
        ? payload.markers.filter((marker) => marker && typeof marker === 'object')
        : []
    const runSymbol = String(payload?.runSymbol || payload?.symbol || '').trim().toUpperCase()
    const runTimeframe = String(payload?.runTimeframe || payload?.timeframe || '').trim().toUpperCase()

    if (!runSymbol || !runTimeframe || !markers.length) {
        return null
    }

    return {
        snapshotKey: String(payload?.snapshotKey || payload?.snapshot_key || '').trim(),
        generatedAt: Number(payload?.generatedAt || payload?.generated_at || 0) || 0,
        runSymbol,
        runTimeframe,
        markers,
    }
}

function extractBacktestMarkerMarketContext(marker, fallbackBuffer = null) {
    const fallback = normalizeBacktestChartBuffer(fallbackBuffer)
    const directSymbol = String(marker?.symbol || '').trim().toUpperCase()
    const directTimeframe = String(marker?.timeframe || '').trim().toUpperCase()
    if (directSymbol && directTimeframe) {
        return {
            symbol: directSymbol,
            timeframe: directTimeframe,
        }
    }

    const markerText = String(marker?.text || '').trim()
    const prefixedContextMatch = markerText.match(/^\[([^\]\s]+)\s+([A-Z0-9]+)\]/i)
    if (prefixedContextMatch) {
        return {
            symbol: String(prefixedContextMatch[1] || '').trim().toUpperCase(),
            timeframe: String(prefixedContextMatch[2] || '').trim().toUpperCase(),
        }
    }

    return {
        symbol: String(fallback?.runSymbol || '').trim().toUpperCase(),
        timeframe: String(fallback?.runTimeframe || '').trim().toUpperCase(),
    }
}

function doesBacktestMarkerMatchChartMarket(marker, chartSettings, fallbackBuffer = null) {
    const markerContext = extractBacktestMarkerMarketContext(marker, fallbackBuffer)
    const chartSymbol = String(chartSettings?.symbol || '').trim().toUpperCase()
    const chartTimeframe = String(chartSettings?.timeframe || '').trim().toUpperCase()

    if (!markerContext.symbol || !markerContext.timeframe || !chartSymbol || !chartTimeframe) {
        return false
    }

    if (markerContext.symbol !== chartSymbol) {
        return false
    }

    if (markerContext.timeframe === chartTimeframe) {
        return true
    }

    const markerMinutes = getTimeframeDurationMinutes(markerContext.timeframe)
    const chartMinutes = getTimeframeDurationMinutes(chartTimeframe)
    if (!Number.isFinite(markerMinutes) || !Number.isFinite(chartMinutes)) {
        return false
    }

    return Boolean(
        chartMinutes <= markerMinutes
        && markerMinutes % chartMinutes === 0
    )
}

function buildBacktestChartBufferFromResponse(response) {
    const hydratedResponse = hydrateBacktestResponsePayload(response)
    const runContext = extractBacktestRunMarketContext(hydratedResponse)
    const markers = Array.isArray(hydratedResponse?.trade_markers) ? hydratedResponse.trade_markers : []
    if (!runContext.symbol || !runContext.timeframe || !markers.length) {
        return null
    }
    return normalizeBacktestChartBuffer({
        snapshotKey: String(hydratedResponse?.snapshot_key || '').trim(),
        generatedAt: getBacktestResponseGeneratedAt(hydratedResponse),
        runSymbol: runContext.symbol,
        runTimeframe: runContext.timeframe,
        markers,
    })
}

function doesBacktestChartBufferMatchChartMarket(buffer, chartSettings) {
    const runContext = normalizeBacktestChartBuffer(buffer)
    if (!runContext?.markers?.length) {
        return false
    }

    return runContext.markers.some((marker) => (
        doesBacktestMarkerMatchChartMarket(marker, chartSettings, runContext)
    ))
}

function buildBacktestChartBufferSummary(buffer) {
    const normalized = normalizeBacktestChartBuffer(buffer)
    if (!normalized) {
        return null
    }

    return {
        snapshotKey: normalized.snapshotKey,
        generatedAt: normalized.generatedAt,
        runSymbol: normalized.runSymbol,
        runTimeframe: normalized.runTimeframe,
        markers: normalized.markers,
    }
}

function parseStreamReplayCandleCount(value) {
    const safeValue = String(value || '').trim()
    if (!safeValue) {
        return null
    }

    if (!/^\d+$/.test(safeValue)) {
        return null
    }

    const parsed = Number(safeValue)
    if (!Number.isInteger(parsed) || parsed <= 0) {
        return null
    }

    return parsed
}

function parseStreamInitialCapital(value) {
    const safeValue = String(value || '').trim().replace(',', '.')
    if (!safeValue) {
        return null
    }

    const parsed = Number(safeValue)
    if (!Number.isFinite(parsed) || parsed <= 0) {
        return null
    }

    return parsed
}

function normalizeStreamVolumeMode(value) {
    const safeValue = String(value || '').trim().toLowerCase()
    return safeValue === 'minimum_operation' ? 'minimum_operation' : 'relative_capital'
}

function resolveStreamLaunchResultUnit(runtimeLike, response = null) {
    const request = response?.request?.backtest && typeof response.request.backtest === 'object'
        ? response.request.backtest
        : {}
    const stats = response?.stats && typeof response.stats === 'object' ? response.stats : {}

    const candidates = [
        runtimeLike?.account_currency,
        runtimeLike?.accountCurrency,
        runtimeLike?.broker_account_currency,
        runtimeLike?.brokerAccountCurrency,
        request?.accountCurrency,
        request?.account_currency,
        stats?.account_currency,
        stats?.currency,
    ]

    const resolved = candidates
        .map((entry) => String(entry || '').trim().toUpperCase())
        .find((entry) => entry && entry.length <= 12)

    return resolved || 'USD'
}

function toFinitePositiveNumber(value) {
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function roundDownVolumeToStep(value, step) {
    const safeValue = toFinitePositiveNumber(value)
    if (!safeValue) {
        return 0
    }

    const safeStep = toFinitePositiveNumber(step) || 0.01
    const precision = Math.min(6, Math.max(2, String(safeStep).split('.')[1]?.length || 0))
    const floored = Math.floor((safeValue + 1e-12) / safeStep) * safeStep
    return Number(floored.toFixed(precision))
}

function resolveStreamLaunchCapitalReference(response = null) {
    const hydratedResponse = hydrateBacktestResponsePayload(response)
    const requestBacktest = hydratedResponse?.request?.backtest && typeof hydratedResponse.request.backtest === 'object'
        ? hydratedResponse.request.backtest
        : {}
    const stats = hydratedResponse?.stats && typeof hydratedResponse.stats === 'object' ? hydratedResponse.stats : {}

    const referenceCapital = (
        toFinitePositiveNumber(requestBacktest?.initialBalance)
        || toFinitePositiveNumber(requestBacktest?.initial_balance)
        || toFinitePositiveNumber(stats?.initial_balance)
        || 10000
    )

    const referenceVolume = (
        toFinitePositiveNumber(requestBacktest?.initialVolume)
        || toFinitePositiveNumber(requestBacktest?.initial_volume)
        || 1.0
    )

    return {
        referenceCapital,
        referenceVolume,
    }
}

function resolveStreamLaunchMinimumVolume(runtimeLike) {
    const runtime = runtimeLike && typeof runtimeLike === 'object' ? runtimeLike : {}
    const enabledSleeves = Array.isArray(runtime?.sleeves)
        ? runtime.sleeves.filter((entry) => entry && typeof entry === 'object' && entry?.enabled !== false)
        : []

    const positiveVolumes = enabledSleeves
        .map((entry) => toFinitePositiveNumber(entry?.volume))
        .filter((value) => value !== null)

    if (positiveVolumes.length) {
        return Math.min(...positiveVolumes)
    }

    return 0.01
}

function buildStreamLaunchCapitalPlan({
    runtimeLike,
    backtestResponse,
    initialCapital,
    volumeMode,
}) {
    const safeInitialCapital = toFinitePositiveNumber(initialCapital) || 100
    const safeVolumeMode = normalizeStreamVolumeMode(volumeMode)
    const minimumOperationVolume = resolveStreamLaunchMinimumVolume(runtimeLike)
    const fallbackReference = backtestResponse
        ? resolveStreamLaunchCapitalReference(backtestResponse)
        : {
            referenceCapital: 100,
            referenceVolume: minimumOperationVolume,
        }
    const { referenceCapital, referenceVolume } = fallbackReference
    const rawRelativeVolume = referenceCapital > 0
        ? (referenceVolume * (safeInitialCapital / referenceCapital))
        : 0
    const relativeVolume = roundDownVolumeToStep(rawRelativeVolume, minimumOperationVolume)
    const selectedVolume = safeVolumeMode === 'minimum_operation'
        ? minimumOperationVolume
        : relativeVolume
    const safeSelectedVolume = Math.max(0, Number(selectedVolume || 0))
    const scaleFactor = referenceVolume > 0 ? (safeSelectedVolume / referenceVolume) : 1
    const unit = resolveStreamLaunchResultUnit(runtimeLike, backtestResponse)

    return {
        initialCapital: safeInitialCapital,
        volumeMode: safeVolumeMode,
        resultUnit: unit,
        referenceCapital,
        referenceVolume,
        minimumOperationVolume,
        relativeVolume,
        selectedVolume: safeSelectedVolume,
        scaleFactor: Number.isFinite(scaleFactor) ? scaleFactor : 1,
        relativeBelowMinimum: safeVolumeMode === 'relative_capital' && safeSelectedVolume <= 0,
        usesBacktestReference: Boolean(backtestResponse),
    }
}

function buildBacktestReplayTimeline(response) {
    const hydratedResponse = hydrateBacktestResponsePayload(response)
    const timeline = []
    const seen = new Set()

    const appendTime = (rawValue) => {
        const normalized = normalizeChartMarkerTime(rawValue)
        if (normalized === null || seen.has(normalized)) {
            return
        }
        seen.add(normalized)
        timeline.push(normalized)
    }

    for (const row of Array.isArray(hydratedResponse?.results) ? hydratedResponse.results : []) {
        appendTime(row?.time)
    }

    if (!timeline.length) {
        for (const marker of Array.isArray(hydratedResponse?.trade_markers) ? hydratedResponse.trade_markers : []) {
            appendTime(marker?.time)
        }
    }

    timeline.sort((left, right) => left - right)
    return timeline
}

function resolveStreamReplayStartTimeFromCandles(response, candleCount) {
    const normalizedCount = Number(candleCount)
    if (!Number.isInteger(normalizedCount) || normalizedCount <= 0) {
        return null
    }

    const timeline = buildBacktestReplayTimeline(response)
    if (!timeline.length) {
        return null
    }

    if (normalizedCount >= timeline.length) {
        return timeline[0]
    }

    return timeline[timeline.length - normalizedCount]
}

function stripBacktestMarkerMarketPrefix(text) {
    return String(text || '').replace(/^\[[^\]]+\]\s*/, '').trim()
}

function normalizeBacktestReplayExitReason(rawValue) {
    const safeValue = String(rawValue || '').trim().toLowerCase()
    if (!safeValue) {
        return ''
    }
    if (safeValue.startsWith('stop loss') || safeValue === 'loss') {
        return 'stop loss'
    }
    if (safeValue.startsWith('stop gain') || safeValue === 'gain') {
        return 'stop gain'
    }
    if (safeValue.startsWith('trail')) {
        return 'trailing stop'
    }
    if (safeValue.startsWith('close normal')) {
        return 'close normal'
    }
    if (safeValue.startsWith('close')) {
        return 'close'
    }
    if (safeValue.startsWith('exit')) {
        return 'exit'
    }
    return safeValue
}

function parseBacktestReplayMarker(marker) {
    const rawText = stripBacktestMarkerMarketPrefix(marker?.text)
    const safeText = String(rawText || '').trim()
    const sideMatch = safeText.match(/\b(long|short)\b/i)
    const normalizedSide = sideMatch ? String(sideMatch[1] || '').trim().toLowerCase() : ''
    const priceMatch = safeText.match(/@\s*(-?\d+(?:\.\d+)?)/)
    const pnlMatch = safeText.match(/\bNet\s*(-?\d+(?:\.\d+)?)/i)
    const labelPrefix = sideMatch
        ? safeText.slice(0, sideMatch.index).trim()
        : safeText
    const sleeveLabel = labelPrefix || 'Backtest'
    const lowerText = safeText.toLowerCase()

    if (lowerText.includes(' skipped ')) {
        return {
            sleeveLabel,
            side: normalizedSide,
            action: 'skip',
            exitReason: '',
            price: priceMatch ? toFiniteBacktestNumber(priceMatch[1]) : null,
            pnl: pnlMatch ? toFiniteBacktestNumber(pnlMatch[1]) : null,
        }
    }

    if (normalizedSide && lowerText.includes(' open ')) {
        return {
            sleeveLabel,
            side: normalizedSide,
            action: 'open',
            exitReason: '',
            price: priceMatch ? toFiniteBacktestNumber(priceMatch[1]) : null,
            pnl: pnlMatch ? toFiniteBacktestNumber(pnlMatch[1]) : null,
        }
    }

    if (normalizedSide) {
        const sideIndex = sideMatch ? sideMatch.index + sideMatch[0].length : 0
        const tail = safeText.slice(sideIndex).trim()
        const exitTokenMatch = tail.match(/^(close normal|stop loss|stop gain|close|gain|loss|trail|exit)\b/i)
        if (exitTokenMatch) {
            return {
                sleeveLabel,
                side: normalizedSide,
                action: 'close',
                exitReason: normalizeBacktestReplayExitReason(exitTokenMatch[1]),
                price: priceMatch ? toFiniteBacktestNumber(priceMatch[1]) : null,
                pnl: pnlMatch ? toFiniteBacktestNumber(pnlMatch[1]) : null,
            }
        }
    }

    return {
        sleeveLabel,
        side: normalizedSide,
        action: '',
        exitReason: '',
        price: priceMatch ? toFiniteBacktestNumber(priceMatch[1]) : null,
        pnl: pnlMatch ? toFiniteBacktestNumber(pnlMatch[1]) : null,
    }
}

function buildBacktestReplayMarketLabel(symbol, timeframe) {
    const safeSymbol = String(symbol || '').trim().toUpperCase()
    const safeTimeframe = String(timeframe || '').trim().toUpperCase()
    return [safeSymbol, safeTimeframe].filter(Boolean).join(' ')
}

function buildStreamBacktestReplay(response, options = {}) {
    const hydratedResponse = hydrateBacktestResponsePayload(response)
    const runContext = extractBacktestRunMarketContext(hydratedResponse)
    const requestBacktest = hydratedResponse?.request?.backtest && typeof hydratedResponse.request.backtest === 'object'
        ? hydratedResponse.request.backtest
        : {}
    const replayVolume = (
        toFinitePositiveNumber(requestBacktest?.initialVolume)
        || toFinitePositiveNumber(requestBacktest?.initial_volume)
        || 1.0
    )
    const filterStartTime = normalizeChartMarkerTime(options?.filterStartTime)
    const rawMarkers = Array.isArray(hydratedResponse?.trade_markers) ? hydratedResponse.trade_markers : []
    const replayMarkers = rawMarkers
        .map((marker, index) => {
            const time = normalizeChartMarkerTime(marker?.time)
            if (time === null) {
                return null
            }
            if (filterStartTime && time < filterStartTime) {
                return null
            }

            const marketContext = extractBacktestMarkerMarketContext(marker, {
                runSymbol: runContext.symbol,
                runTimeframe: runContext.timeframe,
                markers: rawMarkers,
            })
            const parsed = parseBacktestReplayMarker(marker)
            return {
                ...marker,
                id: `stream-replay-${String(marker?.id || index).trim() || index}`,
                time,
                symbol: marketContext.symbol || runContext.symbol,
                timeframe: marketContext.timeframe || runContext.timeframe,
                marketLabel: buildBacktestReplayMarketLabel(
                    marketContext.symbol || runContext.symbol,
                    marketContext.timeframe || runContext.timeframe,
                ),
                sleeveLabel: parsed.sleeveLabel,
                action: parsed.action,
                side: parsed.side,
                exitReason: parsed.exitReason,
                price: parsed.price,
                pnl: parsed.pnl,
                text: String(marker?.text || '').trim(),
                syntheticSource: 'backtest_replay',
            }
        })
        .filter(Boolean)
        .sort((left, right) => Number(left?.time || 0) - Number(right?.time || 0))

    if (!replayMarkers.length) {
        return null
    }

    const closeMarkersByKey = new Map()
    replayMarkers.forEach((marker) => {
        if (marker?.action !== 'close') {
            return
        }
        const key = `${String(marker.marketLabel || '').trim()}|${Number(marker.time || 0)}`
        const group = closeMarkersByKey.get(key) || []
        group.push(marker)
        closeMarkersByKey.set(key, group)
    })

    const exactPnlBuckets = new Map()
    const compositePnlBuckets = []
    for (const row of Array.isArray(hydratedResponse?.results) ? hydratedResponse.results : []) {
        const time = normalizeChartMarkerTime(row?.time)
        const pnl = toFiniteBacktestNumber(row?.trade_net_pnl)
        if (time === null || pnl === null) {
            continue
        }
        if (filterStartTime && time < filterStartTime) {
            continue
        }

        const marketLabels = Array.isArray(row?.market_labels)
            ? row.market_labels
            : String(row?.market_labels || '')
                .split('|')
                .map((entry) => String(entry || '').trim())
                .filter(Boolean)
        const normalizedMarketLabels = marketLabels.length
            ? marketLabels
            : [buildBacktestReplayMarketLabel(row?.symbol || runContext.symbol, row?.timeframe || runContext.timeframe)]

        if (normalizedMarketLabels.length <= 1) {
            const key = `${String(normalizedMarketLabels[0] || '').trim()}|${time}`
            const bucket = exactPnlBuckets.get(key) || []
            bucket.push(pnl)
            exactPnlBuckets.set(key, bucket)
            continue
        }

        compositePnlBuckets.push({
            time,
            marketLabels: normalizedMarketLabels,
            pnl,
        })
    }

    const assignPnlAcrossMarkers = (markers, pnlValues = []) => {
        const unresolved = (Array.isArray(markers) ? markers : []).filter((marker) => toFiniteBacktestNumber(marker?.pnl) === null)
        if (!unresolved.length || !Array.isArray(pnlValues) || !pnlValues.length) {
            return
        }

        if (unresolved.length === pnlValues.length) {
            unresolved.forEach((marker, index) => {
                marker.pnl = toFiniteBacktestNumber(pnlValues[index]) ?? 0
            })
            return
        }

        const totalPnl = pnlValues.reduce((sum, value) => sum + (toFiniteBacktestNumber(value) ?? 0), 0)
        const distributedPnl = unresolved.length ? (totalPnl / unresolved.length) : totalPnl
        unresolved.forEach((marker) => {
            marker.pnl = distributedPnl
        })
    }

    for (const [key, pnlValues] of exactPnlBuckets.entries()) {
        assignPnlAcrossMarkers(closeMarkersByKey.get(key) || [], pnlValues)
    }

    for (const bucket of compositePnlBuckets) {
        const candidates = []
        for (const marketLabel of bucket.marketLabels) {
            const key = `${String(marketLabel || '').trim()}|${Number(bucket.time || 0)}`
            candidates.push(...(closeMarkersByKey.get(key) || []))
        }
        assignPnlAcrossMarkers(candidates, [bucket.pnl])
    }

    const historyRows = []
    const openQueues = new Map()
    replayMarkers.forEach((marker, index) => {
        const action = String(marker?.action || '').trim().toLowerCase()
        const side = String(marker?.side || '').trim().toLowerCase()
        const symbol = String(marker?.symbol || runContext.symbol || '').trim().toUpperCase()
        const timeframe = String(marker?.timeframe || runContext.timeframe || '').trim().toUpperCase()
        const sleeveLabel = String(marker?.sleeveLabel || 'Backtest').trim() || 'Backtest'
        const queueKey = [sleeveLabel, symbol, timeframe, side].join('|')

        if (action === 'open' && side) {
            const row = {
                id: `stream-replay-open-${index}`,
                status: 'filled',
                action: 'open',
                sleeve_id: sleeveLabel,
                sleeve_label: sleeveLabel,
                symbol,
                timeframe,
                side,
                bar_time: marker.time,
                created_at: marker.time,
                filled_at: marker.time,
                message: 'Backtest replay open',
                fill_volume: replayVolume,
                synthetic_open: true,
                synthetic_source: 'backtest_replay',
            }
            historyRows.push(row)
            const queue = openQueues.get(queueKey) || []
            queue.push(row)
            openQueues.set(queueKey, queue)
            return
        }

        if (action === 'close' && side) {
            const queue = openQueues.get(queueKey) || []
            const openingRow = queue.length ? queue.shift() : null
            if (queue.length) {
                openQueues.set(queueKey, queue)
            } else {
                openQueues.delete(queueKey)
            }
            if (openingRow) {
                openingRow.synthetic_open = false
            }

            historyRows.push({
                id: `stream-replay-close-${index}`,
                status: 'filled',
                action: 'close',
                sleeve_id: sleeveLabel,
                sleeve_label: sleeveLabel,
                symbol,
                timeframe,
                side,
                bar_time: marker.time,
                created_at: marker.time,
                filled_at: marker.time,
                fill_volume: replayVolume,
                profit: toFiniteBacktestNumber(marker?.pnl) ?? 0,
                commission: 0,
                swap: 0,
                message: `Backtest replay ${String(marker?.exitReason || 'close').trim() || 'close'}`,
                synthetic_source: 'backtest_replay',
            })
        }
    })

    const eventTimes = replayMarkers
        .map((marker) => normalizeChartMarkerTime(marker?.time))
        .filter((value) => value !== null)
    const earliestEventTime = eventTimes.length ? eventTimes[0] : null
    const sessionStartTime = filterStartTime || earliestEventTime || null

    if (!historyRows.length && !replayMarkers.some((marker) => String(marker?.action || '').trim().toLowerCase() === 'skip')) {
        return null
    }

    return {
        sourceSnapshotKey: String(hydratedResponse?.snapshot_key || '').trim(),
        sourceGeneratedAt: getBacktestResponseGeneratedAt(hydratedResponse),
        sessionStartTime,
        filterStartTime,
        runSymbol: runContext.symbol,
        runTimeframe: runContext.timeframe,
        historyRows,
        tradeMarkers: replayMarkers,
    }
}

function areBacktestChartBufferSummariesEqual(left, right) {
    const normalizedLeft = normalizeBacktestChartBuffer(left)
    const normalizedRight = normalizeBacktestChartBuffer(right)

    if (!normalizedLeft && !normalizedRight) {
        return true
    }

    if (!normalizedLeft || !normalizedRight) {
        return false
    }

    if (normalizedLeft.snapshotKey !== normalizedRight.snapshotKey) {
        return false
    }
    if (normalizedLeft.generatedAt !== normalizedRight.generatedAt) {
        return false
    }
    if (normalizedLeft.runSymbol !== normalizedRight.runSymbol) {
        return false
    }
    if (normalizedLeft.runTimeframe !== normalizedRight.runTimeframe) {
        return false
    }

    const leftMarkers = Array.isArray(normalizedLeft.markers) ? normalizedLeft.markers : []
    const rightMarkers = Array.isArray(normalizedRight.markers) ? normalizedRight.markers : []
    if (leftMarkers.length !== rightMarkers.length) {
        return false
    }

    for (let index = 0; index < leftMarkers.length; index += 1) {
        const leftMarker = leftMarkers[index] || {}
        const rightMarker = rightMarkers[index] || {}
        if (String(leftMarker.id || '') !== String(rightMarker.id || '')) {
            return false
        }
        if (Number(leftMarker.time || 0) !== Number(rightMarker.time || 0)) {
            return false
        }
    }

    return true
}

function buildChartSettingsForBacktestOverlay(currentChartSettings, overlay, historyState = {}) {
    const normalizedOverlay = normalizeBacktestChartBuffer(overlay)
    const currentSettings = normalizeChartSettings(currentChartSettings)
    if (!normalizedOverlay) {
        return currentSettings
    }

    const timeframeMinutes = getTimeframeDurationMinutes(normalizedOverlay.runTimeframe)
    const markerTimes = normalizedOverlay.markers
        .map((marker) => Number(marker?.time))
        .filter((value) => Number.isFinite(value))
        .sort((left, right) => left - right)

    let nextBars = Math.max(1, Number(currentSettings.bars) || 1)
    if (markerTimes.length && Number.isFinite(timeframeMinutes) && timeframeMinutes > 0) {
        const timeframeSeconds = timeframeMinutes * 60
        const earliestMarkerTime = markerTimes[0]
        const latestMarkerTime = markerTimes[markerTimes.length - 1]
        const liveReferenceTime = Math.floor(Date.now() / 1000 / timeframeSeconds) * timeframeSeconds
        const historyReferenceTime = Number(historyState?.lastLoadedTime)
        const comparisonLastTime = Math.max(
            Number.isFinite(historyReferenceTime) ? historyReferenceTime : 0,
            latestMarkerTime,
            liveReferenceTime,
        )
        nextBars = Math.max(
            nextBars,
            Math.ceil((comparisonLastTime - earliestMarkerTime) / timeframeSeconds) + 32,
        )
    }

    return normalizeChartSettings({
        ...currentSettings,
        symbol: normalizedOverlay.runSymbol,
        timeframe: normalizedOverlay.runTimeframe,
        bars: nextBars,
    })
}

function mergeChartMarkers(...markerGroups) {
    const merged = []
    const seenIds = new Set()

    for (const group of markerGroups) {
        for (const marker of Array.isArray(group) ? group : []) {
            const safeId = String(marker?.id || '').trim()
            if (!safeId || seenIds.has(safeId)) {
                continue
            }
            seenIds.add(safeId)
            merged.push(marker)
        }
    }

    return merged.sort((left, right) => {
        const timeDiff = Number(left?.time || 0) - Number(right?.time || 0)
        if (timeDiff !== 0) {
            return timeDiff
        }
        return String(left?.id || '').localeCompare(String(right?.id || ''))
    })
}

function extractTradeRuntimePayload(payload) {
    if (payload?.trade_runtime && typeof payload.trade_runtime === 'object') {
        return payload.trade_runtime
    }
    return payload
}

function findLatestInvalidStopEntry(runtime) {
    const commands = Array.isArray(runtime?.order_commands) ? runtime.order_commands : []
    const intents = Array.isArray(runtime?.order_intents) ? runtime.order_intents : []
    const matchesInvalidStops = (value) => String(value || '').toLowerCase().includes('invalid stops')
    const sortByLatest = (left, right) => (
        Number(right?.rejected_at || right?.filled_at || right?.created_at || 0)
        - Number(left?.rejected_at || left?.filled_at || left?.created_at || 0)
    )

    const commandMatch = commands.filter((entry) => matchesInvalidStops(entry?.message)).sort(sortByLatest)[0]
    if (commandMatch) {
        return {
            id: String(commandMatch?.id || '').trim(),
            detail: String(commandMatch?.message || 'Invalid stops').trim(),
            symbol: String(commandMatch?.symbol || '').trim().toUpperCase(),
            strategy: String(commandMatch?.sleeve_label || commandMatch?.source_strategy_id || '').trim(),
        }
    }

    const intentMatch = intents.filter((entry) => matchesInvalidStops(entry?.rejection_message)).sort(sortByLatest)[0]
    if (intentMatch) {
        return {
            id: String(intentMatch?.id || '').trim(),
            detail: String(intentMatch?.rejection_message || 'Invalid stops').trim(),
            symbol: String(intentMatch?.symbol || '').trim().toUpperCase(),
            strategy: String(intentMatch?.sleeve_label || intentMatch?.source_strategy_id || '').trim(),
        }
    }

    return null
}

function buildTradeRuntimeChartMarkers(runtime, chartSettings) {
    if (!runtime || typeof runtime !== 'object' || !runtime.armed) {
        return []
    }

    const chartSymbol = String(chartSettings?.symbol || '').trim().toUpperCase()
    const chartTimeframe = String(chartSettings?.timeframe || '').trim().toUpperCase()
    const timeframeMinutes = getTimeframeDurationMinutes(chartTimeframe)
    const timeframeSeconds = timeframeMinutes !== null ? timeframeMinutes * 60 : null
    const intents = Array.isArray(runtime.order_intents) ? runtime.order_intents : []
    const commands = Array.isArray(runtime.order_commands) ? runtime.order_commands : []
    const markers = []
    const seenIds = new Set()
    const commandFingerprints = new Set(
        commands
            .map((entry) => String(entry?.fingerprint || '').trim())
            .filter(Boolean)
    )

    const matchesChart = (entry) => {
        const symbol = String(entry?.symbol || '').trim().toUpperCase()
        const timeframe = String(entry?.timeframe || '').trim().toUpperCase()
        if (chartSymbol && symbol && symbol !== chartSymbol) {
            return false
        }
        if (chartTimeframe && timeframe && timeframe !== chartTimeframe) {
            return false
        }
        return true
    }

    const sideVisual = (side, action) => {
        const normalizedSide = String(side || '').trim().toLowerCase()
        const normalizedAction = String(action || '').trim().toLowerCase()
        const isLong = normalizedSide === 'long'
        if (normalizedAction === 'open') {
            return {
                position: isLong ? 'belowBar' : 'aboveBar',
                shape: isLong ? 'arrowUp' : 'arrowDown',
            }
        }
        return {
            position: isLong ? 'aboveBar' : 'belowBar',
            shape: 'square',
        }
    }

    const pushMarker = (marker) => {
        const safeId = String(marker?.id || '').trim()
        if (!safeId || seenIds.has(safeId)) {
            return
        }
        seenIds.add(safeId)
        markers.push(marker)
    }

    intents
        .filter((entry) => entry && matchesChart(entry))
        .forEach((entry) => {
            const fingerprint = String(entry?.fingerprint || '').trim()
            if (fingerprint && commandFingerprints.has(fingerprint)) {
                return
            }
            const candidateTimes = buildTradeRuntimeMarkerCandidateTimes(entry, timeframeSeconds)
            const time = candidateTimes[0] ?? null
            if (time === null) {
                return
            }
            const action = String(entry?.action || '').trim().toLowerCase()
            const side = String(entry?.side || '').trim().toLowerCase()
            const status = String(entry?.status || 'queued').trim().toLowerCase()
            const sleeveLabel = String(entry?.sleeve_label || entry?.sleeve_id || 'Trade').trim()
            const visual = sideVisual(side, action)
            const color = status === 'dispatch_blocked' ? '#f59e0b' : '#f3f4f6'
            const statusLabel = status.replace(/_/g, ' ')
            pushMarker({
                id: `trade-intent-${entry?.id || `${entry?.sleeve_id}-${action}-${side}-${time}`}`,
                time,
                position: visual.position,
                shape: visual.shape,
                color,
                text: `${sleeveLabel} ${action} ${side} intent | ${statusLabel}`.trim(),
                size: 1,
                candidateTimes,
            })
        })

    commands
        .filter((entry) => entry && matchesChart(entry))
        .forEach((entry) => {
            const candidateTimes = buildTradeRuntimeMarkerCandidateTimes(entry, timeframeSeconds)
            const time = candidateTimes[0] ?? null
            if (time === null) {
                return
            }
            const action = String(entry?.action || '').trim().toLowerCase()
            const side = String(entry?.side || '').trim().toLowerCase()
            const status = String(entry?.status || 'queued').trim().toLowerCase()
            const sleeveLabel = String(entry?.sleeve_label || entry?.sleeve_id || 'Trade').trim()
            const visual = sideVisual(side, action)
            const isLong = side === 'long'
            const color = status === 'filled'
                ? (isLong ? '#22c55e' : '#ef4444')
                : status === 'rejected' || status === 'stale'
                    ? '#ef4444'
                    : '#60a5fa'
            const shape = status === 'filled'
                ? 'circle'
                : status === 'rejected' || status === 'stale'
                    ? 'circle'
                    : visual.shape
            const ticketSuffix = entry?.broker_order_id ? ` | ticket ${entry.broker_order_id}` : ''
            pushMarker({
                id: `trade-command-${entry?.id || `${entry?.sleeve_id}-${action}-${side}-${time}`}`,
                time,
                position: visual.position,
                shape,
                color,
                text: `${sleeveLabel} ${action} ${side} ${status}${ticketSuffix}`.trim(),
                size: 1,
                candidateTimes,
            })
        })

    return markers.sort((left, right) => {
        const timeDiff = Number(left?.time || 0) - Number(right?.time || 0)
        if (timeDiff !== 0) {
            return timeDiff
        }
        return String(left?.id || '').localeCompare(String(right?.id || ''))
    })
}

const DEFAULT_WORKSPACE_USER_ID = 'local-user'
const DEFAULT_WORKSPACE_ID = 'default'
const AUTH_TOKEN_STORAGE_KEY = 'robotineeko_auth_token'
const CURRENT_WORKSPACE_SAVE_STORAGE_KEY = 'robotineeko_current_workspace_save'
const CURRENT_WORKSPACE_SAVE_GLOBAL_STORAGE_KEY = 'robotineeko_current_workspace_save_global'

function buildStrategyAliasContextChartSettings(chartSettings, strategy, strategyEntries = []) {
    return buildStrategyCollectionChartSettings(chartSettings, strategy, strategyEntries)
}

function mergeStrategyDefaults(current) {
    return {
        long: {
            openPrice: String(current?.long?.openPrice ?? DEFAULT_STRATEGY.long.openPrice),
            closePrice: String(current?.long?.closePrice ?? DEFAULT_STRATEGY.long.closePrice),
            openIf: String(current?.long?.openIf ?? DEFAULT_STRATEGY.long.openIf),
            closeIf: String(current?.long?.closeIf ?? DEFAULT_STRATEGY.long.closeIf),
            gainPrice: String(current?.long?.gainPrice ?? DEFAULT_STRATEGY.long.gainPrice),
            lossPrice: String(current?.long?.lossPrice ?? DEFAULT_STRATEGY.long.lossPrice),
            trailingPrice: String(current?.long?.trailingPrice ?? DEFAULT_STRATEGY.long.trailingPrice),
        },
        short: {
            openPrice: String(current?.short?.openPrice ?? DEFAULT_STRATEGY.short.openPrice),
            closePrice: String(current?.short?.closePrice ?? DEFAULT_STRATEGY.short.closePrice),
            openIf: String(current?.short?.openIf ?? DEFAULT_STRATEGY.short.openIf),
            closeIf: String(current?.short?.closeIf ?? DEFAULT_STRATEGY.short.closeIf),
            gainPrice: String(current?.short?.gainPrice ?? DEFAULT_STRATEGY.short.gainPrice),
            lossPrice: String(current?.short?.lossPrice ?? DEFAULT_STRATEGY.short.lossPrice),
            trailingPrice: String(current?.short?.trailingPrice ?? DEFAULT_STRATEGY.short.trailingPrice),
        },
        other: {
            allowInversion: Boolean(current?.other?.allowInversion ?? DEFAULT_STRATEGY.other.allowInversion),
            priority: String(current?.other?.priority ?? DEFAULT_STRATEGY.other.priority),
        },
        featureManifest: normalizeStrategyFeatureManifest(current?.featureManifest),
    }
}

function mergeBacktestDefaults(current, fallbackChartSettings = DEFAULT_CHART_SETTINGS, activeBrokerProfile = null) {
    const currentSource = current && typeof current === 'object' ? current : {}
    const normalizedCurrentSource = mergeBacktestCostProfileValues(currentSource, activeBrokerProfile)
    const normalizedFallbackChartSettings = normalizeChartSettings(fallbackChartSettings || DEFAULT_CHART_SETTINGS)
    const source = {
        initialBalance: normalizedCurrentSource.initialBalance,
        assetType: normalizedCurrentSource.assetType,
        initialVolume: normalizedCurrentSource.initialVolume,
        pipSize: normalizedCurrentSource.pipSize,
        pipValuePerLot: normalizedCurrentSource.pipValuePerLot,
        costProfile: normalizedCurrentSource.costProfile,
        spreadInPips: normalizedCurrentSource.spreadInPips,
        slippageInPips: normalizedCurrentSource.slippageInPips,
        entrySlippageInPips: normalizedCurrentSource.entrySlippageInPips,
        closeSlippageInPips: normalizedCurrentSource.closeSlippageInPips,
        takeProfitSlippageInPips: normalizedCurrentSource.takeProfitSlippageInPips,
        stopLossSlippageInPips: normalizedCurrentSource.stopLossSlippageInPips,
        trailingStopSlippageInPips: normalizedCurrentSource.trailingStopSlippageInPips,
        minimumStopDistanceInPips: normalizedCurrentSource.minimumStopDistanceInPips,
        volatilitySlippageMultiplier: normalizedCurrentSource.volatilitySlippageMultiplier,
        executionMode: normalizedCurrentSource.executionMode,
        portfolioMode: normalizedCurrentSource.portfolioMode,
        portfolioStructureVersion: normalizedCurrentSource.portfolioStructureVersion,
        capitalModel: cloneSerializable(normalizedCurrentSource.capitalModel, null),
        portfolios: cloneSerializable(normalizedCurrentSource.portfolios, []),
        symbol: normalizedCurrentSource.symbol,
        timeframe: normalizedCurrentSource.timeframe,
        historyScopeMode: normalizedCurrentSource.historyScopeMode,
        historyScopeBars: normalizedCurrentSource.historyScopeBars,
    }
    const numericFields = [
        'initialBalance',
        'initialVolume',
        'pipSize',
        'pipValuePerLot',
        'spreadInPips',
        'slippageInPips',
        'entrySlippageInPips',
        'closeSlippageInPips',
        'takeProfitSlippageInPips',
        'stopLossSlippageInPips',
        'trailingStopSlippageInPips',
        'minimumStopDistanceInPips',
        'volatilitySlippageMultiplier',
        'historyScopeBars',
    ]
    const stringFields = [
        'assetType',
        'costProfile',
        'executionMode',
        'portfolioMode',
        'symbol',
        'timeframe',
        'historyScopeMode',
    ]

    const merged = {
        ...DEFAULT_BACKTEST,
        ...source,
    }

    for (const field of numericFields) {
        const rawValue = source[field]
        if (rawValue === '' || rawValue === null || rawValue === undefined || Number.isNaN(Number(rawValue))) {
            merged[field] = DEFAULT_BACKTEST[field]
            continue
        }

        merged[field] = Number(rawValue)
    }

    for (const field of stringFields) {
        const rawValue = source[field]
        const normalizedValue = String(rawValue ?? '').trim()
        if (field === 'costProfile') {
            merged[field] = normalizeBacktestCostProfile(normalizedValue || DEFAULT_BACKTEST[field])
            continue
        }
        merged[field] = normalizedValue || DEFAULT_BACKTEST[field]
    }

    merged.symbol = String(source.symbol || normalizedFallbackChartSettings.symbol || DEFAULT_BACKTEST.symbol).trim().toUpperCase() || DEFAULT_BACKTEST.symbol
    merged.timeframe = String(source.timeframe || DEFAULT_BACKTEST.timeframe).trim().toUpperCase() || DEFAULT_BACKTEST.timeframe
    merged.assetType = coerceBacktestAssetType(
        source.assetType || merged.assetType,
        activeBrokerProfile,
        source,
        merged.costProfile,
    )
    if (merged.costProfile !== 'custom') {
        Object.assign(
            merged,
            buildBacktestCostProfileValues(merged.costProfile, activeBrokerProfile, merged),
        )
    }
    merged.portfolioStructureVersion = Number(source.portfolioStructureVersion) >= 2 ? 2 : 1
    merged.capitalModel = source.capitalModel && typeof source.capitalModel === 'object'
        ? cloneSerializable(source.capitalModel, null)
        : null
    merged.portfolios = Array.isArray(source.portfolios)
        ? cloneSerializable(source.portfolios, [])
        : []

    if (merged.historyScopeMode !== 'custom') {
        merged.historyScopeBars = null
    } else {
        merged.historyScopeBars = Math.max(1, Number(merged.historyScopeBars || DEFAULT_BACKTEST.historyScopeBars || 1))
    }

    return merged
}

function sanitizeWorkspaceChartSettingsForPersistence(current) {
    const normalized = normalizeChartSettings(current || DEFAULT_CHART_SETTINGS)
    const persistedBars = Math.max(
        PROJECT_SNAPSHOT_MIN_BARS,
        Math.max(1, Number(normalized.bars) || 1),
    )

    return normalizeChartSettings({
        ...normalized,
        bars: persistedBars,
    })
}

function buildSessionChartSettings(baseSettings, fallbackBars = DEFAULT_CHART_SETTINGS.bars) {
    const normalized = normalizeChartSettings(baseSettings || DEFAULT_CHART_SETTINGS)
    return normalizeChartSettings({
        ...normalized,
        bars: Math.max(1, Number(fallbackBars || DEFAULT_CHART_SETTINGS.bars) || DEFAULT_CHART_SETTINGS.bars),
    })
}

function sanitizeWorkspaceBacktestForPersistence(current, fallbackChartSettings = DEFAULT_CHART_SETTINGS, activeBrokerProfile = null) {
    return mergeBacktestDefaults(current, fallbackChartSettings, activeBrokerProfile)
}

function sanitizeWorkspaceStrategy(current, chartSettings) {
    const migratedStrategy = migrateStrategyFeatureNamesToAliases(current, chartSettings)
    return mergeStrategyDefaults(migratedStrategy)
}

function sanitizeWorkspaceStrategySet(current, chartSettings) {
    return Array.isArray(current)
        ? current
            .filter((entry) => entry && typeof entry === 'object')
            .map((entry) => ({
                ...entry,
                strategy: sanitizeWorkspaceStrategy(entry?.strategy || {}, chartSettings),
            }))
        : []
}

function normalizeWorkspaceStatePayload(payload, fallbackChartSettings) {
    const persistedChartSettings = payload?.chartSettings || fallbackChartSettings || DEFAULT_CHART_SETTINGS
    const normalizedPersistedChartSettings = sanitizeWorkspaceChartSettingsForPersistence(persistedChartSettings)
    const normalizedBacktest = sanitizeWorkspaceBacktestForPersistence(payload?.backtest, normalizedPersistedChartSettings)
    const normalizedChartSettings = normalizeChartSettings(normalizedPersistedChartSettings)
    const rawBacktestStrategySet = Array.isArray(payload?.backtestStrategySet) ? payload.backtestStrategySet : []
    const strategyChartSettings = buildStrategyCollectionChartSettings(
        normalizedChartSettings,
        payload?.strategy || {},
        rawBacktestStrategySet,
    )
    const normalizedStrategy = sanitizeWorkspaceStrategy(payload?.strategy, strategyChartSettings)
    const normalizedBacktestStrategySet = sanitizeWorkspaceStrategySet(rawBacktestStrategySet, strategyChartSettings)

    return {
        chartSettings: normalizedChartSettings,
        strategy: normalizedStrategy,
        backtestStrategySet: normalizedBacktestStrategySet,
        backtest: normalizedBacktest,
        trade: payload?.trade && typeof payload.trade === 'object'
            ? payload.trade
            : {
                ...DEFAULT_TRADE,
            },
        batch: payload?.batch && typeof payload.batch === 'object'
            ? sanitizeSharedBatchState(payload.batch)
            : {
                ...DEFAULT_SHARED_BATCH_STATE,
            },
        research: payload?.research && typeof payload.research === 'object'
            ? payload.research
            : {
                paperShortlist: [],
                decisionLog: [],
                savedStudies: {},
                studyRuns: [],
                benchmarkStrategies: [],
            },
        drawings: Array.isArray(payload?.drawings) ? payload.drawings : [],
        visibleIndicatorColumns: payload?.visibleIndicatorColumns && typeof payload.visibleIndicatorColumns === 'object'
            ? payload.visibleIndicatorColumns
            : {},
        strategyResponse: payload?.strategyResponse || null,
        // Only a lightweight summary of the latest completed backtest is shared
        // through workspace state. Full rows/markers/chart series stay in
        // session/local snapshot storage.
        backtestRunResponse: buildBacktestResponseSummary(payload?.backtestRunResponse, {
            chartSettings: normalizedChartSettings,
            strategy: normalizedStrategy,
            strategies: normalizedBacktestStrategySet,
            backtest: normalizedBacktest,
        }),
        backtestChartBuffer: buildBacktestChartBufferSummary(payload?.backtestChartBuffer),
        chartBacktestOverlay: buildBacktestChartBufferSummary(payload?.chartBacktestOverlay),
        uiState: normalizeSharedWorkspaceUiState(payload?.uiState),
    }
}

function resolveBrokerSafeWorkspaceState(normalizedState, preferredSelection = null) {
    const safeState = normalizedState && typeof normalizedState === 'object'
        ? normalizedState
        : normalizeWorkspaceStatePayload({}, DEFAULT_CHART_SETTINGS)
    const loadedTradeState = safeState.trade && typeof safeState.trade === 'object'
        ? safeState.trade
        : DEFAULT_TRADE
    const preferred = preferredSelection && typeof preferredSelection === 'object'
        ? preferredSelection
        : {}
    const loadedBrokerId = normalizeBrokerProfileId(loadedTradeState.activeBrokerProfileId)
    const loadedBrokerLabel = normalizeBrokerProfileLabel(loadedTradeState.activeBrokerProfileLabel)
    const targetBrokerId = normalizeBrokerProfileId(
        preferred.id || preferred.activeBrokerProfileId || loadedBrokerId,
    )
    const targetBrokerLabel = normalizeBrokerProfileLabel(
        preferred.label || preferred.activeBrokerProfileLabel || loadedBrokerLabel,
        preferred.broker_code || preferred.brokerCode || loadedTradeState?.broker_code || '',
    )
    const targetSelection = {
        ...loadedTradeState,
        ...(preferred && typeof preferred === 'object' ? preferred : {}),
        activeBrokerProfileId: targetBrokerId || loadedTradeState.activeBrokerProfileId,
        activeBrokerProfileLabel: targetBrokerLabel || loadedTradeState.activeBrokerProfileLabel,
    }
    const targetBrokerMarketDomain = resolveBrokerProfileMarketDomain(targetSelection)
    const currentChartMarketDomain = inferMarketDomainFromSymbol(safeState?.chartSettings?.symbol)
    const currentChartBootstrapKey = resolveChartBrokerBootstrapKey(safeState.chartSettings)
    const targetBootstrapSettings = resolveBrokerBootstrapChartSettings(
        targetSelection,
        safeState?.chartSettings?.bars || DEFAULT_CHART_SETTINGS.bars,
    )
    const targetChartBootstrapKey = resolveChartBrokerBootstrapKey(targetBootstrapSettings)
    const brokerMismatch = Boolean(
        (targetBrokerId && loadedBrokerId && targetBrokerId !== loadedBrokerId)
        || (
            !targetBrokerId
            && targetBrokerLabel
            && loadedBrokerLabel
            && targetBrokerLabel !== loadedBrokerLabel
        )
    )
    const staleCrossBrokerBootstrap = Boolean(
        currentChartBootstrapKey
        && targetChartBootstrapKey
        && currentChartBootstrapKey !== targetChartBootstrapKey
    )
    const incompatibleChartMarket = Boolean(
        targetBrokerMarketDomain
        && currentChartMarketDomain
        && currentChartMarketDomain !== 'mixed'
        && currentChartMarketDomain !== targetBrokerMarketDomain
    )
    const shouldBootstrap = brokerMismatch || staleCrossBrokerBootstrap || incompatibleChartMarket
    const reason = brokerMismatch
        ? 'broker_switch'
        : staleCrossBrokerBootstrap
            ? 'stale_bootstrap_chart'
            : incompatibleChartMarket
                ? 'incompatible_chart_symbol'
                : ''

    return {
        state: shouldBootstrap
            ? buildBrokerBootstrapWorkspaceState(safeState, targetSelection)
            : safeState,
        shouldBootstrap,
        reason,
        targetBrokerId,
        targetBrokerLabel: targetBrokerLabel || loadedBrokerLabel,
        targetSelection,
        bootstrapChartSettings: targetBootstrapSettings,
    }
}

const RESULTS_CHART_CACHE_STORAGE_PREFIX = 'robotineeko:results-chart-snapshot'

function hashString(value = '') {
    let hash = 0
    const text = String(value || '')
    for (let index = 0; index < text.length; index += 1) {
        hash = ((hash << 5) - hash) + text.charCodeAt(index)
        hash |= 0
    }
    return Math.abs(hash).toString(36)
}

function buildResultsSnapshotKey({ chartSettings, strategy, strategies = [], backtest }) {
    return hashString(JSON.stringify({
        chartSettings: sanitizeWorkspaceChartSettingsForPersistence(chartSettings, backtest),
        strategy,
        strategies: Array.isArray(strategies) ? strategies : [],
        backtest: sanitizeWorkspaceBacktestForPersistence(backtest, chartSettings),
    }))
}

function buildCanonicalStreamPopupUrl(launchKey) {
    const basePath = String(import.meta.env.BASE_URL || '/').trim() || '/'
    const popupUrl = new URL(basePath, window.location.origin)
    popupUrl.searchParams.set(STREAM_VIEW_QUERY_PARAM, STREAM_VIEW_QUERY_VALUE)
    popupUrl.searchParams.set(STREAM_LAUNCH_KEY_QUERY_PARAM, launchKey)
    popupUrl.hash = ''
    return popupUrl
}

function stripHeavyStatsSeries(stats) {
    if (!stats || typeof stats !== 'object') {
        return null
    }

    const nextStats = { ...stats }
    for (const key of Object.keys(nextStats)) {
        if (/_series$|_curve$/i.test(key)) {
            delete nextStats[key]
        }
    }
    return nextStats
}

function getBacktestResponseGeneratedAt(response) {
    if (!response || typeof response !== 'object') {
        return 0
    }

    const candidates = [
        response.last_results_generated_at,
        response?.runtime?.last_results_generated_at,
        response?.runtime?.results_view?.last_results_generated_at,
    ]

    for (const candidate of candidates) {
        const value = Number(candidate || 0)
        if (Number.isFinite(value) && value > 0) {
            return value
        }
    }

    return 0
}

function buildBacktestResponseSummary(response, context = {}) {
    if (!response || typeof response !== 'object') {
        return null
    }

    const existingSnapshotKey = String(response?.snapshot_key || '').trim()
    const snapshotKey = existingSnapshotKey || buildResultsSnapshotKey(context)

    return {
        status: response.status || 'ok',
        request: response.request || null,
        runtime: response.runtime || null,
        stats: stripHeavyStatsSeries(response.stats),
        results: [],
        trade_markers: [],
        strategy_view_meta: response.strategy_view_meta || null,
        applied_indicators: Array.isArray(response.applied_indicators) ? response.applied_indicators : [],
        available_columns: Array.isArray(response.available_columns) ? response.available_columns : [],
        available_column_details: [],
        has_results: Boolean(response.has_results),
        rows: Number(response.rows || 0),
        summary_only: true,
        snapshot_key: snapshotKey,
        chart_snapshot_available: true,
        market_window: buildBacktestMarketWindowSummary(response),
        last_results_generated_at: response.last_results_generated_at || null,
    }
}

function mergeBacktestResponsePreservingFull(currentResponse, nextResponse) {
    const current = currentResponse && typeof currentResponse === 'object' ? currentResponse : null
    const next = nextResponse && typeof nextResponse === 'object' ? nextResponse : null

    if (!next) {
        return null
    }

    if (!current) {
        return next
    }

    const currentSnapshotKey = String(current.snapshot_key || '').trim()
    const nextSnapshotKey = String(next.snapshot_key || '').trim()
    const isSameSnapshot = Boolean(currentSnapshotKey && nextSnapshotKey && currentSnapshotKey === nextSnapshotKey)
    const currentHasFullPayload = Array.isArray(current.results) && current.results.length > 0
    const nextIsSummaryOnly = Boolean(next.summary_only)
    const currentGeneratedAt = getBacktestResponseGeneratedAt(current)
    const nextGeneratedAt = getBacktestResponseGeneratedAt(next)

    if (isSameSnapshot && currentHasFullPayload && nextIsSummaryOnly) {
        return {
            ...current,
            ...next,
            summary_only: false,
            stats: current.stats,
            results: current.results,
            trade_markers: Array.isArray(current.trade_markers) ? current.trade_markers : [],
            applied_indicators: Array.isArray(current.applied_indicators) ? current.applied_indicators : [],
            available_columns: Array.isArray(current.available_columns) ? current.available_columns : [],
            available_column_details: Array.isArray(current.available_column_details) ? current.available_column_details : [],
        }
    }

    if (
        currentGeneratedAt > 0
        && nextGeneratedAt > 0
        && currentGeneratedAt > nextGeneratedAt
    ) {
        return current
    }

    return next
}

function buildResultsChartCacheStorageKey(workspaceUserId = DEFAULT_WORKSPACE_USER_ID, workspaceId = DEFAULT_WORKSPACE_ID) {
    return `${RESULTS_CHART_CACHE_STORAGE_PREFIX}:${workspaceUserId}:${workspaceId}`
}

function saveResultsChartSnapshotToStorage(workspaceUserId, workspaceId, payload) {
    if (typeof window === 'undefined') {
        return
    }

    try {
        window.localStorage.setItem(
            buildResultsChartCacheStorageKey(workspaceUserId, workspaceId),
            JSON.stringify(payload),
        )
    } catch {
        // ignore local snapshot persistence failures
    }
}

function loadResultsChartSnapshotFromStorage(workspaceUserId, workspaceId) {
    if (typeof window === 'undefined') {
        return null
    }

    try {
        const raw = window.localStorage.getItem(buildResultsChartCacheStorageKey(workspaceUserId, workspaceId)) || ''
        if (!raw) {
            return null
        }
        const parsed = JSON.parse(raw)
        return parsed && typeof parsed === 'object' ? parsed : null
    } catch {
        return null
    }
}

function attachBacktestChartBufferToResponse(response, backtestChartBuffer) {
    const hydratedResponse = hydrateBacktestResponsePayload(response)
    const normalizedBuffer = normalizeBacktestChartBuffer(backtestChartBuffer)
    if (!normalizedBuffer?.markers?.length) {
        return hydratedResponse
    }

    const responseSnapshotKey = String(hydratedResponse?.snapshot_key || '').trim()
    const bufferSnapshotKey = String(normalizedBuffer?.snapshotKey || '').trim()
    if (responseSnapshotKey && bufferSnapshotKey && responseSnapshotKey !== bufferSnapshotKey) {
        return hydratedResponse
    }

    return hydrateBacktestResponsePayload({
        ...hydratedResponse,
        trade_markers: normalizedBuffer.markers,
        summary_only: false,
        chart_snapshot_available: true,
    })
}

function resolveLoadedBacktestResponse(
    primaryResponse,
    workspaceUserId,
    workspaceId = DEFAULT_WORKSPACE_ID,
    backtestChartBuffer = null,
) {
    const hydratedPrimary = hydrateBacktestResponsePayload(primaryResponse)
    const primaryResultCount = Array.isArray(hydratedPrimary?.results) ? hydratedPrimary.results.length : 0
    const primaryMarkerCount = Array.isArray(hydratedPrimary?.trade_markers) ? hydratedPrimary.trade_markers.length : 0

    if (primaryResultCount > 0 || primaryMarkerCount > 0) {
        return {
            response: hydratedPrimary,
            source: 'memory',
        }
    }

    const snapshotKey = String(hydratedPrimary?.snapshot_key || '').trim()
    if (!snapshotKey) {
        return {
            response: hydratedPrimary,
            source: 'summary_only',
        }
    }

    const storedSnapshot = loadResultsChartSnapshotFromStorage(workspaceUserId, workspaceId)
    if (
        !storedSnapshot?.response
        || String(storedSnapshot?.snapshotKey || '').trim() !== snapshotKey
    ) {
        const workspaceBufferedResponse = attachBacktestChartBufferToResponse(hydratedPrimary, backtestChartBuffer)
        const bufferedMarkerCount = Array.isArray(workspaceBufferedResponse?.trade_markers)
            ? workspaceBufferedResponse.trade_markers.length
            : 0
        if (bufferedMarkerCount > 0) {
            return {
                response: workspaceBufferedResponse,
                source: 'workspace_buffer',
            }
        }

        return {
            response: hydratedPrimary,
            source: 'summary_only',
        }
    }

    return {
        response: hydrateBacktestResponsePayload(storedSnapshot.response || null),
        source: 'stored_snapshot',
    }
}

function formatWorkspaceSyncTime(value) {
    if (!value) {
        return '--'
    }

    try {
        return new Date(value * 1000).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        })
    } catch {
        return '--'
    }
}

function buildCurrentWorkspaceSaveStorageKey(workspaceUserId = DEFAULT_WORKSPACE_USER_ID, workspaceId = DEFAULT_WORKSPACE_ID) {
    return `${CURRENT_WORKSPACE_SAVE_STORAGE_KEY}:${workspaceUserId}:${workspaceId}`
}

function getStoredCurrentWorkspaceSaveSnapshot(workspaceUserId = DEFAULT_WORKSPACE_USER_ID, workspaceId = DEFAULT_WORKSPACE_ID) {
    if (typeof window === 'undefined') {
        return null
    }

    const raw = window.localStorage.getItem(buildCurrentWorkspaceSaveStorageKey(workspaceUserId, workspaceId)) || ''
    if (!raw) {
        return null
    }

    try {
        const parsed = JSON.parse(raw)
        if (parsed && typeof parsed === 'object') {
            return {
                id: String(parsed.id || '').trim(),
                name: String(parsed.name || '').trim(),
                score: parsed.score ?? null,
                state: null,
            }
        }
    } catch {
        return {
            id: String(raw || '').trim(),
            name: '',
            score: null,
        }
    }

    return null
}

function getStoredGlobalCurrentWorkspaceSaveSnapshot() {
    if (typeof window === 'undefined') {
        return null
    }

    const raw = window.localStorage.getItem(CURRENT_WORKSPACE_SAVE_GLOBAL_STORAGE_KEY) || ''
    if (!raw) {
        return null
    }

    try {
        const parsed = JSON.parse(raw)
        if (parsed && typeof parsed === 'object') {
            return {
                id: String(parsed.id || '').trim(),
                name: String(parsed.name || '').trim(),
                score: parsed.score ?? null,
                state: null,
            }
        }
    } catch {
        return null
    }

    return null
}

function persistStoredCurrentWorkspaceSaveSnapshot(
    workspaceUserId = DEFAULT_WORKSPACE_USER_ID,
    workspaceId = DEFAULT_WORKSPACE_ID,
    snapshot = null,
) {
    if (typeof window === 'undefined') {
        return
    }

    const storageKey = buildCurrentWorkspaceSaveStorageKey(workspaceUserId, workspaceId)
    if (!snapshot || !String(snapshot.id || '').trim()) {
        window.localStorage.removeItem(storageKey)
        window.localStorage.removeItem(CURRENT_WORKSPACE_SAVE_GLOBAL_STORAGE_KEY)
        return
    }

    const serialized = JSON.stringify({
        id: String(snapshot.id || '').trim(),
        name: String(snapshot.name || '').trim(),
        score: snapshot.score ?? null,
    })

    try {
        window.localStorage.setItem(storageKey, serialized)
        window.localStorage.setItem(CURRENT_WORKSPACE_SAVE_GLOBAL_STORAGE_KEY, serialized)
    } catch {
        window.localStorage.removeItem(storageKey)
        window.localStorage.removeItem(CURRENT_WORKSPACE_SAVE_GLOBAL_STORAGE_KEY)
    }
}

async function waitForChartReady(buildApiUrl, buildAuthHeaders, minimumIndicators = 0, timeoutMs = 15000) {
    const startedAt = Date.now()
    const safeMinimumIndicators = Math.max(0, Number(minimumIndicators) || 0)

    while (Date.now() - startedAt < timeoutMs) {
        const response = await fetch(buildApiUrl('/chart/status'), {
            headers: buildAuthHeaders(),
        })
        const data = await readJsonResponse(response)

        if (response.ok && data?.ready) {
            if (safeMinimumIndicators <= 0) {
                return true
            }

            const chartDataResponse = await fetch(buildApiUrl('/chart/data?timeout=10'), {
                headers: buildAuthHeaders(),
            })
            const chartData = await readJsonResponse(chartDataResponse)
            const appliedIndicators = Array.isArray(chartData?.applied_indicators)
                ? chartData.applied_indicators.length
                : 0
            if (chartDataResponse.ok && chartData?.status === 'ok' && appliedIndicators >= safeMinimumIndicators) {
                return true
            }
        }

        if (data?.error) {
            throw new Error(data.error)
        }

        await new Promise((resolve) => window.setTimeout(resolve, 250))
    }

    return false
}

function formatSystemLogTimestamp(value) {
    if (!value) {
        return ''
    }

    try {
        const date = new Date(value)
        const year = String(date.getFullYear())
        const month = String(date.getMonth() + 1).padStart(2, '0')
        const day = String(date.getDate()).padStart(2, '0')
        const hours = String(date.getHours()).padStart(2, '0')
        const minutes = String(date.getMinutes()).padStart(2, '0')
        const seconds = String(date.getSeconds()).padStart(2, '0')
        return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
    } catch {
        return ''
    }
}

function classifySystemLogLevel(message, explicitLevel = '') {
    const safeExplicitLevel = String(explicitLevel || '').trim().toLowerCase()

    if (safeExplicitLevel) {
        return safeExplicitLevel
    }

    const text = String(message || '').toLowerCase()
    const hasFailurePhrase = (
        text.includes('could not')
        || text.includes('unavailable')
        || text.includes('conflict')
        || /\bfailed to\b/.test(text)
        || /\bfailed(?=[:.!)]|$)/.test(text)
        || /(^|\s)error(?=[:.\s]|$)/.test(text)
    )

    if (hasFailurePhrase) {
        return 'error'
    }

    if (
        text.includes('saved ')
        || text.includes('overwrote ')
        || text.includes('loaded ')
        || text.includes('deleted ')
        || text.includes('renamed ')
        || text.includes('signed in')
        || text.includes('created account')
        || text.includes('updated.')
        || text.includes('applied successfully')
        || text.includes('active on the server')
        || text.includes('disabled on the server')
    ) {
        return 'success'
    }

    return 'info'
}

function normalizeSystemLogCreatedAt(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return Date.now()
    }

    return numeric > 1_000_000_000_000 ? numeric : numeric * 1000
}

function deriveSystemLogScope(message = '') {
    const prefix = String(message || '')
        .split('·')[0]
        .split(':')[0]
        .trim()
        .toLowerCase()

    if (!prefix) {
        return 'system'
    }
    if (prefix === 'strategy') {
        return 'strategy'
    }
    if (prefix === 'strategy library') {
        return 'strategy_library'
    }
    if (prefix === 'backtester' || prefix === 'backtest') {
        return 'backtester'
    }
    if (prefix === 'results') {
        return 'results'
    }
    if (prefix === 'research') {
        return 'research'
    }
    if (prefix === 'batch') {
        return 'batch'
    }
    if (prefix === 'trade' || prefix === 'trader') {
        return 'trade'
    }
    if (prefix === 'neural') {
        return 'neural'
    }
    if (prefix === 'chart') {
        return 'chart'
    }
    if (prefix === 'project' || prefix === 'workspace') {
        return 'workspace'
    }
    if (prefix === 'indicator manager') {
        return 'features'
    }
    return prefix.replace(/\s+/g, '_')
}

function deriveSystemLogCategory(message = '', level = 'info', scope = 'system') {
    if (level === 'error') {
        return 'failure'
    }

    const text = String(message || '').toLowerCase()

    if (text.includes('started') || text.includes('queued') || text.includes('loaded') || text.includes('saved') || text.includes('deleted') || text.includes('updated') || text.includes('applied') || text.includes('restored')) {
        return 'lifecycle'
    }
    if (text.includes('cancel') || text.includes('armed') || text.includes('disarm') || text.includes('reset')) {
        return 'control'
    }
    if (scope === 'workspace' || text.includes('sync') || text.includes('session')) {
        return 'sync'
    }
    if (text.includes('copied') || text.includes('export')) {
        return 'export'
    }

    return 'operator'
}

function normalizeSystemLogSession(session = null) {
    if (!session || typeof session !== 'object') {
        return null
    }

    const normalizedId = Number(session.id)
    return {
        id: Number.isFinite(normalizedId) && normalizedId > 0 ? normalizedId : 0,
        label: String(session.label || '').trim(),
        status: String(session.status || '').trim().toLowerCase() || 'active',
        source: String(session.source || '').trim(),
        metadata: session.metadata && typeof session.metadata === 'object' ? session.metadata : {},
        entryCount: Number(session.entry_count ?? session.entryCount ?? 0) || 0,
        createdAt: normalizeSystemLogCreatedAt(session.created_at ?? session.createdAt),
        updatedAt: normalizeSystemLogCreatedAt(session.updated_at ?? session.updatedAt ?? session.created_at ?? session.createdAt),
        closedAt: session.closed_at || session.closedAt ? normalizeSystemLogCreatedAt(session.closed_at ?? session.closedAt) : null,
        lastEntryAt: session.last_entry_at || session.lastEntryAt ? normalizeSystemLogCreatedAt(session.last_entry_at ?? session.lastEntryAt) : null,
    }
}

function normalizeSystemLogEntry(entry = null) {
    if (!entry || typeof entry !== 'object') {
        return null
    }

    const createdAt = normalizeSystemLogCreatedAt(entry.createdAt ?? entry.created_at)
    const clientEntryId = String(entry.clientEntryId || entry.client_entry_id || '').trim()
    const persistedId = Number(entry.id)
    const id = String(
        clientEntryId
        || (Number.isFinite(persistedId) && persistedId > 0 ? `persisted-${persistedId}` : '')
        || `${createdAt}-${Math.random().toString(36).slice(2, 8)}`
    ).trim()
    const message = String(entry.message || '').trim()
    const level = classifySystemLogLevel(message, entry.level)
    const scope = String(entry.scope || deriveSystemLogScope(message)).trim() || 'system'
    const category = String(entry.category || deriveSystemLogCategory(message, level, scope)).trim() || 'operator'

    return {
        id,
        persistedId: Number.isFinite(persistedId) && persistedId > 0 ? persistedId : null,
        clientEntryId,
        sessionId: Number(entry.sessionId ?? entry.session_id) || 0,
        createdAt,
        timestamp: String(entry.timestamp || formatSystemLogTimestamp(createdAt)).trim(),
        message,
        level,
        source: String(entry.source || '').trim() || 'console_ui',
        scope,
        category,
        context: entry.context && typeof entry.context === 'object' ? entry.context : {},
    }
}

function mergeSystemLogEntries(currentEntries = [], incomingEntries = []) {
    const merged = new Map()

    ;[...(Array.isArray(currentEntries) ? currentEntries : []), ...(Array.isArray(incomingEntries) ? incomingEntries : [])]
        .map((entry) => normalizeSystemLogEntry(entry))
        .filter(Boolean)
        .forEach((entry) => {
            const key = entry.clientEntryId
                || (entry.persistedId ? `persisted:${entry.persistedId}` : '')
                || entry.id
                || `${entry.createdAt}:${entry.message}`
            const existing = merged.get(key)
            if (!existing) {
                merged.set(key, entry)
                return
            }

            const shouldReplace = (
                (!existing.persistedId && entry.persistedId)
                || existing.createdAt <= entry.createdAt
            )

            if (!shouldReplace) {
                return
            }

            merged.set(key, {
                ...existing,
                ...entry,
                context: {
                    ...(existing.context || {}),
                    ...(entry.context || {}),
                },
            })
        })

    return Array.from(merged.values()).sort((left, right) => (
        Number(left.createdAt || 0) - Number(right.createdAt || 0)
        || String(left.id || '').localeCompare(String(right.id || ''))
    ))
}

function mergeSystemLogSessions(currentSessions = [], incomingSessions = [], archivedSessionIds = []) {
    const merged = new Map()
    const archivedIds = new Set((Array.isArray(archivedSessionIds) ? archivedSessionIds : []).map((value) => Number(value) || 0))

    ;[...(Array.isArray(currentSessions) ? currentSessions : []), ...(Array.isArray(incomingSessions) ? incomingSessions : [])]
        .map((session) => normalizeSystemLogSession(session))
        .filter(Boolean)
        .forEach((session) => {
            const key = Number(session.id) || 0
            if (!key) {
                return
            }

            const existing = merged.get(key)
            const nextSession = archivedIds.has(key)
                ? {
                    ...session,
                    status: 'archived',
                }
                : session

            if (!existing || Number(existing.updatedAt || 0) <= Number(nextSession.updatedAt || 0)) {
                merged.set(key, nextSession)
            }
        })

    return Array.from(merged.values()).sort((left, right) => (
        Number(right.updatedAt || 0) - Number(left.updatedAt || 0)
        || Number(right.id || 0) - Number(left.id || 0)
    ))
}

function formatServerTimestamp(value) {
    if (!value) {
        return '--'
    }

    try {
        return new Date(Number(value) * 1000).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        })
    } catch {
        return '--'
    }
}

async function readJsonResponse(response) {
    const text = await response.text()

    if (!text) {
        return {}
    }

    try {
        return JSON.parse(text)
    } catch {
        const preview = text.replace(/\s+/g, ' ').trim().slice(0, 180)
        throw new Error(
            preview
                ? `Server returned an invalid response: ${preview}`
                : 'Server returned an invalid response.'
        )
    }
}

function extractApiErrorMessage(data, fallbackMessage) {
    if (typeof data?.detail?.error === 'string' && data.detail.error.trim()) {
        return data.detail.error
    }

    if (typeof data?.detail === 'string' && data.detail.trim()) {
        return data.detail
    }

    if (typeof data?.error === 'string' && data.error.trim()) {
        return data.error
    }

    return fallbackMessage
}

async function fetchBrokerProfilesSnapshot(authToken = '') {
    if (!authToken) {
        return []
    }

    const response = await fetchWithServerRetry(
        buildApiUrl('/workspace/broker-profiles?workspace_id=default&limit=200'),
        {
            headers: {
                Authorization: `Bearer ${authToken}`,
            },
        },
        {
            attempts: 4,
            retryDelayMs: 750,
        },
    )
    const data = await readJsonResponse(response)
    if (!response.ok || data?.status !== 'ok') {
        throw new Error(extractApiErrorMessage(data, 'Failed to load broker profiles.'))
    }
    return Array.isArray(data?.broker_profiles)
        ? data.broker_profiles.map((entry, index) => normalizeBrokerProfileRecord(entry, index))
        : []
}

function describeBrokerProfileStack(profile) {
    const apiBaseUrl = resolveBrokerProfileApiBaseUrl(profile)
    if (!apiBaseUrl) {
        return 'default stack'
    }

    try {
        return new URL(apiBaseUrl).host
    } catch {
        return apiBaseUrl.replace(/^https?:\/\//i, '') || 'default stack'
    }
}

function buildBrokerProfileHeaderOptionLabel(profile) {
    const safeProfile = normalizeBrokerProfileRecord(profile)
    return safeProfile.label
}

function App() {
    const [chartSettings, setChartSettings] = useState(
        normalizeChartSettings(DEFAULT_CHART_SETTINGS)
    )

    const [loadedChartSettings, setLoadedChartSettings] = useState(
        normalizeChartSettings(DEFAULT_CHART_SETTINGS)
    )

    const [strategy, setStrategy] = useState({
        long: {
            openPrice: 'close[0]',
            closePrice: 'close[0]',
            openIf: 'False',
            closeIf: 'False',
            gainPrice: '',
            lossPrice: '',
            trailingPrice: '',
        },
        short: {
            openPrice: 'close[0]',
            closePrice: 'close[0]',
            openIf: 'False',
            closeIf: 'False',
            gainPrice: '',
            lossPrice: '',
            trailingPrice: '',
        },
        other: {
            allowInversion: false,
            priority: 'Short',
        },
    })

    const [backtest, setBacktest] = useState({
        ...mergeBacktestDefaults(null, DEFAULT_CHART_SETTINGS),
    })
    const [backtestStrategySet, setBacktestStrategySet] = useState([])
    const [tradeState, setTradeState] = useState(() => ({
        ...DEFAULT_TRADE,
        ...(() => {
            const initialStoredBrokerProfileSelection = getStoredActiveBrokerProfileSelection()
            return {
                activeBrokerProfileId: initialStoredBrokerProfileSelection.id || DEFAULT_TRADE.activeBrokerProfileId,
                activeBrokerProfileLabel: initialStoredBrokerProfileSelection.label || DEFAULT_TRADE.activeBrokerProfileLabel,
            }
        })(),
    }))
    const [brokerProfiles, setBrokerProfiles] = useState([])
    const [isBrokerProfilesLoading, setIsBrokerProfilesLoading] = useState(false)
    const [hasBrokerProfilesHydrated, setHasBrokerProfilesHydrated] = useState(false)
    const [brokerProfilesLoadError, setBrokerProfilesLoadError] = useState('')
    const activeHeaderBrokerProfileId = normalizeBrokerProfileId(tradeState?.activeBrokerProfileId)
    const activeHeaderBrokerProfileLabel = String(tradeState?.activeBrokerProfileLabel || '').trim()
    const activeHeaderBrokerProfile = brokerProfiles.find((entry) => entry.id === activeHeaderBrokerProfileId) || null
    const [batchState, setBatchState] = useState({
        ...DEFAULT_SHARED_BATCH_STATE,
    })
    const [researchState, setResearchState] = useState({
        paperShortlist: [],
        decisionLog: [],
        savedStudies: {},
        studyRuns: [],
        benchmarkStrategies: [],
    })

    const [chartRunId, setChartRunId] = useState(0)
    const [chartViewId, setChartViewId] = useState(0)
    const [lastStrategyResponse, setLastStrategyResponse] = useState(null)
    const [lastBacktestResponse, setLastBacktestResponse] = useState(null)
    const [backtestChartBuffer, setBacktestChartBuffer] = useState(null)
    const [chartBacktestOverlay, setChartBacktestOverlay] = useState(null)
    const [liveTradeRuntime, setLiveTradeRuntime] = useState(null)
    const [hasStoredResultsCharts, setHasStoredResultsCharts] = useState(false)
    const [isStreamLaunchOverlayOpen, setIsStreamLaunchOverlayOpen] = useState(false)
    const [streamLaunchDraft, setStreamLaunchDraft] = useState(DEFAULT_STREAM_LAUNCH_DRAFT)
    const [streamLaunchError, setStreamLaunchError] = useState('')
    const [isIndicatorManagerOpen, setIsIndicatorManagerOpen] = useState(false)
    const [isAuthManagerOpen, setIsAuthManagerOpen] = useState(false)
    const [authMode, setAuthMode] = useState('login')
    const [authUser, setAuthUser] = useState(null)
    const [authToken, setAuthToken] = useState(() => {
        if (typeof window === 'undefined') {
            return ''
        }

        return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || ''
    })
    const [authError, setAuthError] = useState('')
    const [isAuthSubmitting, setIsAuthSubmitting] = useState(false)
    const [workspaceSaves, setWorkspaceSaves] = useState([])
    const [hasWorkspaceSavesHydrated, setHasWorkspaceSavesHydrated] = useState(false)
    const [currentWorkspaceSaveSnapshot, setCurrentWorkspaceSaveSnapshot] = useState(() => getStoredGlobalCurrentWorkspaceSaveSnapshot())
    const [currentWorkspaceSaveId, setCurrentWorkspaceSaveId] = useState(() => getStoredGlobalCurrentWorkspaceSaveSnapshot()?.id || '')
    const [, setLastRestoredWorkspaceSaveId] = useState('')
    const [workspaceSyncStatus, setWorkspaceSyncStatus] = useState('saved')
    const [workspaceSyncLabel, setWorkspaceSyncLabel] = useState('Saved')
    const [workspaceSocketStatus, setWorkspaceSocketStatus] = useState('connecting')
    const [workspaceLastSavedAt, setWorkspaceLastSavedAt] = useState(null)
    const [isStatusMenuOpen, setIsStatusMenuOpen] = useState(false)
    const [serverHealth, setServerHealth] = useState(null)
    const [invalidStopOverlay, setInvalidStopOverlay] = useState(null)
    const [isGuestNoticeDismissed, setIsGuestNoticeDismissed] = useState(false)
    const [uiState, setUiState] = useState(DEFAULT_SHARED_WORKSPACE_UI_STATE)
    const [drawingUiState, setDrawingUiState] = useState(DEFAULT_LOCAL_DRAWING_UI_STATE)
    const [systemLogEntries, setSystemLogEntries] = useState([])
    const [systemLogSession, setSystemLogSession] = useState(null)
    const [, setSystemLogSessions] = useState([])
    const [isSystemLogLoading, setIsSystemLogLoading] = useState(false)
    const [systemLogHeight, setSystemLogHeight] = useState(88)
    const [chartHistoryState, setChartHistoryState] = useState({
        loadedCandles: 0,
        historyLoadStep: 0,
        firstLoadedTime: null,
        lastLoadedTime: null,
    })
    const [symbolInputValue, setSymbolInputValue] = useState('EURUSD')
    const [chartSymbolCatalog, setChartSymbolCatalog] = useState({
        symbols: [],
        rows: [],
        exhaustive: false,
        source: '',
        note: '',
    })
    const [chartDrawings, setChartDrawings] = useState([])
    const [visibleIndicatorColumnsSnapshot, setVisibleIndicatorColumnsSnapshot] = useState({})
    const [activeStrategyFieldId, setActiveStrategyFieldId] = useState('')
    const [strategyInsertRequest, setStrategyInsertRequest] = useState(null)
    const [consoleStatusState, setConsoleStatusState] = useState({
        strategyError: '',
        strategyDebugError: '',
        backtestError: '',
        resultsError: '',
        neuralError: '',
        strategyPending: false,
        strategyDebugPending: false,
        strategyDebugReady: false,
        backtestBusy: false,
        backtestPending: false,
        resultsPending: false,
        neuralPending: false,
        neuralReady: false,
    })
    const [isConsoleMaximized, setIsConsoleMaximized] = useState(false)
    const latestChartRequestIdRef = useRef(0)
    const loadedChartSettingsRef = useRef(
        normalizeChartSettings(DEFAULT_CHART_SETTINGS)
    )
    const workspaceSessionIdRef = useRef(`workspace-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`)
    const workspaceRevisionRef = useRef(0)
    const workspacePersistTimerRef = useRef(null)
    const workspacePersistIdleHandleRef = useRef(null)
    const workspacePatchQueueRef = useRef(Promise.resolve())
    const lastPersistedWorkspaceRef = useRef('')
    const workspaceSocketRef = useRef(null)
    const workspaceReconnectTimerRef = useRef(null)
    const workspaceConnectTimeoutRef = useRef(null)
    const systemLogSessionRef = useRef(null)
    const systemLogPersistQueueRef = useRef([])
    const systemLogFlushTimerRef = useRef(null)
    const systemLogFlushInFlightRef = useRef(false)
    const systemLogFlushPromiseRef = useRef(null)
    const brokerProfileReloadTargetRef = useRef('')
    const pendingBrokerSwitchChartAttemptRef = useRef('')
    const invalidBrokerChartCatalogAttemptRef = useRef('')
    const statusMenuRef = useRef(null)
    const strategySocketRef = useRef(null)
    const strategyReconnectTimerRef = useRef(null)
    const [isWorkspaceReady, setIsWorkspaceReady] = useState(false)
    const isAuthenticated = Boolean(authUser && authToken)
    const isGuest = Boolean(authUser?.is_guest)
    const isStreamWindow = typeof window !== 'undefined' && isStreamViewLocation(window.location)
    const isMobileWindow = typeof window !== 'undefined' && isMobileViewLocation(window.location)

    useEffect(() => {
        if (isStreamWindow) {
            applySiteMetadata({
                title: 'Robotineeko Stream',
                description: 'Janela de stream do Robotineeko com execucao visual, operacoes e replay sincronizado.',
                robots: 'noindex,nofollow',
            })
            return
        }

        if (isMobileWindow) {
            applySiteMetadata({
                title: 'Robotineeko Mobile Trader',
                description: 'Superficie mobile do trader do Robotineeko para acompanhamento operacional compacto.',
                robots: 'noindex,nofollow',
            })
            return
        }

        applySiteMetadata({
            title: 'Robotineeko · Research, Backtest e Trader',
            description: 'Robotineeko: broker-aware research, backtesting, neural experimentation, and trader runtime tooling.',
            robots: 'index,follow',
        })
    }, [isMobileWindow, isStreamWindow])

    const uiStateRef = useRef(DEFAULT_SHARED_WORKSPACE_UI_STATE)
    const streamLaunchRuntimeLike = useMemo(() => {
        if (hasStreamRuntimeMarketSetup(liveTradeRuntime)) {
            return liveTradeRuntime
        }
        if (hasStreamRuntimeMarketSetup(tradeState)) {
            return tradeState
        }
        return liveTradeRuntime
    }, [liveTradeRuntime, tradeState])
    const streamLaunchBacktestSource = useMemo(() => {
        const workspaceUserId = getActiveWorkspaceUserId()
        const resolvedLoadedBacktest = resolveLoadedBacktestResponse(
            lastBacktestResponse,
            workspaceUserId,
            DEFAULT_WORKSPACE_ID,
            backtestChartBuffer,
        )
        const loadedBacktestResponse = resolvedLoadedBacktest.response
        const loadedBacktestMarkers = Array.isArray(loadedBacktestResponse?.trade_markers) ? loadedBacktestResponse.trade_markers.length : 0
        const loadedBacktestResults = Array.isArray(loadedBacktestResponse?.results) ? loadedBacktestResponse.results.length : 0
        const fallbackStreamChartSettings = loadedChartSettingsRef.current || loadedChartSettings || chartSettings
        if (loadedBacktestMarkers <= 0 && loadedBacktestResults <= 0) {
            return {
                loaded: false,
                compatible: false,
                reason: 'No completed backtest is currently loaded in the main console. The stream launcher only reuses a backtest that is already loaded there and never starts a new one.',
                response: null,
                source: 'main_console',
                snapshotKey: '',
                symbol: '',
                timeframe: '',
                markerCount: 0,
                resultCount: 0,
            }
        }

        const runContext = extractBacktestRunMarketContext(loadedBacktestResponse)
        const compatibility = evaluateStreamBacktestSourceCompatibility(
            loadedBacktestResponse,
            streamLaunchRuntimeLike,
            fallbackStreamChartSettings,
        )

        return {
            loaded: true,
            compatible: compatibility.compatible,
            reason: compatibility.reason,
            response: compatibility.compatible ? loadedBacktestResponse : null,
            source: resolvedLoadedBacktest.source === 'memory'
                ? 'main_console'
                : 'main_console_rehydrated',
            snapshotKey: String(loadedBacktestResponse?.snapshot_key || '').trim(),
            symbol: runContext.symbol,
            timeframe: runContext.timeframe,
            markerCount: loadedBacktestMarkers,
            resultCount: loadedBacktestResults,
            streamMarketLabel: compatibility.streamMarketLabel,
            backtestMarketLabel: compatibility.backtestMarketLabel,
        }
    }, [backtestChartBuffer, chartSettings, lastBacktestResponse, loadedChartSettings, streamLaunchRuntimeLike])
    const streamLaunchCapitalPreview = useMemo(
        () => buildStreamLaunchCapitalPlan({
            runtimeLike: streamLaunchRuntimeLike,
            backtestResponse: streamLaunchBacktestSource?.compatible ? streamLaunchBacktestSource?.response : null,
            initialCapital: parseStreamInitialCapital(streamLaunchDraft.initialCapital) || 100,
            volumeMode: streamLaunchDraft.volumeMode,
        }),
        [
            streamLaunchBacktestSource?.compatible,
            streamLaunchBacktestSource?.response,
            streamLaunchDraft.initialCapital,
            streamLaunchRuntimeLike,
            streamLaunchDraft.volumeMode,
        ]
    )

    useEffect(() => {
        if (!isGuest) {
            return
        }

        setIsGuestNoticeDismissed(false)
        setWorkspaceSaves([])
        setHasWorkspaceSavesHydrated(true)
        setWorkspaceLastSavedAt(null)
        setWorkspaceSyncStatus('saved')
        setWorkspaceSyncLabel('Temporary')
        setWorkspaceSocketStatus('disconnected')
    }, [isGuest])

    useEffect(() => {
        uiStateRef.current = uiState
    }, [uiState])

    useEffect(() => {
        systemLogSessionRef.current = systemLogSession
    }, [systemLogSession])

    function getActiveWorkspaceUserId(userOverride = null) {
        return userOverride?.workspace_user_id || authUser?.workspace_user_id || DEFAULT_WORKSPACE_USER_ID
    }

    function updateCurrentWorkspaceSave(nextSave, userOverride = null) {
        const previousState = currentWorkspaceSaveSnapshot?.state && typeof currentWorkspaceSaveSnapshot.state === 'object'
            ? currentWorkspaceSaveSnapshot.state
            : null
        const snapshot = nextSave && typeof nextSave === 'object'
            ? {
                id: String(nextSave.id || '').trim(),
                name: String(nextSave.name || '').trim(),
                score: nextSave.score ?? null,
                state: nextSave.state && typeof nextSave.state === 'object' ? nextSave.state : previousState,
            }
            : {
                id: String(nextSave || '').trim(),
                name: '',
                score: null,
                state: null,
            }

        setCurrentWorkspaceSaveId(snapshot.id)
        setCurrentWorkspaceSaveSnapshot(snapshot.id ? snapshot : null)
        persistStoredCurrentWorkspaceSaveSnapshot(getActiveWorkspaceUserId(userOverride), DEFAULT_WORKSPACE_ID, snapshot.id ? snapshot : null)
    }

function buildWorkspaceStateForSync({
        nextChartSettings = loadedChartSettingsRef.current,
        nextStrategy = strategy,
        nextBacktestStrategySet = backtestStrategySet,
        nextBacktest = backtest,
        nextTrade = tradeState,
        nextBatch = batchState,
        nextResearch = researchState,
        nextDrawings = chartDrawings,
        nextVisibleIndicatorColumns = visibleIndicatorColumnsSnapshot,
        nextStrategyResponse = lastStrategyResponse,
        nextBacktestRunResponse = lastBacktestResponse,
        nextBacktestChartBuffer = backtestChartBuffer,
        nextChartBacktestOverlay = chartBacktestOverlay,
        nextUiState = uiState,
    } = {}) {
        const persistedBacktestChartBuffer = buildBacktestChartBufferSummary(nextBacktestChartBuffer)
        const persistedChartBacktestOverlay = buildBacktestChartBufferSummary(nextChartBacktestOverlay)
        const nextBacktestBrokerProfileContext = resolveBrokerProfileContextFromTradeSelection(
            nextTrade,
            brokerProfiles,
            activeBacktestBrokerProfileContext,
        )
        const responseContext = {
            chartSettings: nextChartSettings,
            strategy: nextStrategy,
            strategies: nextBacktestStrategySet,
            backtest: nextBacktest,
        }
        return normalizeWorkspaceStatePayload(
            pickSharedWorkspaceState({
                chartSettings: sanitizeWorkspaceChartSettingsForPersistence(nextChartSettings, nextBacktest),
                strategy: nextStrategy,
                backtestStrategySet: nextBacktestStrategySet,
                backtest: sanitizeWorkspaceBacktestForPersistence(
                    nextBacktest,
                    nextChartSettings,
                    nextBacktestBrokerProfileContext,
                ),
                trade: nextTrade,
                batch: nextBatch,
                research: nextResearch,
                drawings: nextDrawings,
                visibleIndicatorColumns: nextVisibleIndicatorColumns,
                strategyResponse: buildBacktestResponseSummary(nextStrategyResponse, responseContext),
                backtestRunResponse: buildBacktestResponseSummary(nextBacktestRunResponse, responseContext),
                backtestChartBuffer: persistedBacktestChartBuffer,
                chartBacktestOverlay: (
                    persistedChartBacktestOverlay
                    && !areBacktestChartBufferSummariesEqual(
                        persistedBacktestChartBuffer,
                        persistedChartBacktestOverlay,
                    )
                )
                    ? persistedChartBacktestOverlay
                    : null,
                uiState: nextUiState,
            }),
            loadedChartSettingsRef.current
        )
    }

    function buildAuthHeaders(extraHeaders = {}) {
        if (!authToken) {
            return extraHeaders
        }

        return {
            ...extraHeaders,
            Authorization: `Bearer ${authToken}`,
        }
    }

    function getPreferredBrokerSelection(selectionOverride = null) {
        const explicitSelection = selectionOverride && typeof selectionOverride === 'object'
            ? selectionOverride
            : null
        const currentTradeSelection = tradeState && typeof tradeState === 'object'
            ? tradeState
            : {}
        const storedSelection = getStoredActiveBrokerProfileSelection()
        const pendingChart = readPendingBrokerSwitchChartSettings()
        const pendingChartSelection = pendingChart
            ? {
                id: pendingChart.targetBrokerProfileId || '',
                label: pendingChart.targetBrokerLabel || '',
                market_domain: pendingChart.targetMarketDomain || '',
            }
            : null

        if (hasMeaningfulBrokerSelection(pendingChartSelection)) {
            return {
                ...storedSelection,
                ...pendingChartSelection,
            }
        }

        if (hasMeaningfulBrokerSelection(explicitSelection)) {
            return explicitSelection
        }

        if (hasMeaningfulBrokerSelection(currentTradeSelection)) {
            return currentTradeSelection
        }

        if (hasMeaningfulBrokerSelection(storedSelection)) {
            return storedSelection
        }

        return {}
    }

    function normalizeIncomingWorkspaceState(nextState, selectionOverride = null) {
        const normalizedState = normalizeWorkspaceStatePayload(nextState, loadedChartSettingsRef.current)
        return resolveBrokerSafeWorkspaceState(
            normalizedState,
            getPreferredBrokerSelection(selectionOverride),
        )
    }

    function handleSharedConsoleJobChange(jobKey, nextJobState) {
        const nextUiState = normalizeSharedWorkspaceUiState({
            ...uiStateRef.current,
            consoleJobs: {
                ...(uiStateRef.current?.consoleJobs || {}),
                [jobKey]: nextJobState && typeof nextJobState === 'object'
                    ? {
                        status: 'running',
                        label: String(nextJobState.label || '').trim(),
                        startedAt: String(nextJobState.startedAt || new Date().toISOString()).trim(),
                        side: String(nextJobState.side || '').trim(),
                        actor: String(nextJobState.actor || '').trim(),
                        jobId: String(nextJobState.jobId || '').trim(),
                    }
                    : null,
            },
        })

        uiStateRef.current = nextUiState
        setUiState(nextUiState)

        if (!isWorkspaceReady || !isAuthenticated || isGuest) {
            return
        }

        void persistWorkspacePatch(
            { uiState: nextUiState },
            `${workspaceSessionIdRef.current}:console-job:${jobKey}`,
        ).catch((error) => {
            console.error('Failed to persist console job state:', error)
            appendSystemLog(`Workspace sync failed: ${error.message || 'console job save error'}.`)
        })
    }

    function clearScheduledWorkspacePersist() {
        if (workspacePersistTimerRef.current) {
            window.clearTimeout(workspacePersistTimerRef.current)
            workspacePersistTimerRef.current = null
        }

        if (workspacePersistIdleHandleRef.current === null) {
            return
        }

        if (typeof window !== 'undefined' && typeof window.cancelIdleCallback === 'function') {
            window.cancelIdleCallback(workspacePersistIdleHandleRef.current)
        } else {
            window.clearTimeout(workspacePersistIdleHandleRef.current)
        }
        workspacePersistIdleHandleRef.current = null
    }

    function queueWorkspacePersistFlush() {
        const flushWorkspacePatch = () => {
            workspacePersistIdleHandleRef.current = null

            const payload = buildWorkspaceStateForSync()
            const previousState = lastPersistedWorkspaceRef.current
                ? JSON.parse(lastPersistedWorkspaceRef.current)
                : null
            const patch = buildSharedWorkspacePatch(previousState, payload)

            if (!Object.keys(patch).length) {
                return
            }

            void persistWorkspacePatch(patch).catch((error) => {
                console.error('Failed to persist workspace state:', error)
                appendSystemLog(`Workspace sync failed: ${error.message || 'save error'}.`)
            })
        }

        if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
            workspacePersistIdleHandleRef.current = window.requestIdleCallback(
                flushWorkspacePatch,
                { timeout: 1200 },
            )
            return
        }

        workspacePersistIdleHandleRef.current = window.setTimeout(flushWorkspacePatch, 0)
    }

    async function persistWorkspacePatchNow(nextPatch, source = workspaceSessionIdRef.current, attempt = 0) {
        if (isGuest) {
            setWorkspaceSyncStatus('saved')
            setWorkspaceSyncLabel('Temporary')
            setWorkspaceLastSavedAt(null)
            return {
                status: 'ok',
                revision: workspaceRevisionRef.current,
                state: lastPersistedWorkspaceRef.current
                    ? JSON.parse(lastPersistedWorkspaceRef.current)
                    : null,
                updated_at: null,
                temporary: true,
                source,
            }
        }

        setWorkspaceSyncStatus('syncing')
        setWorkspaceSyncLabel('Syncing...')
        const response = await fetch(buildApiUrl('/workspace/state'), {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                ...buildAuthHeaders(),
            },
            body: JSON.stringify({
                user_id: authToken ? null : DEFAULT_WORKSPACE_USER_ID,
                workspace_id: DEFAULT_WORKSPACE_ID,
                expected_revision: workspaceRevisionRef.current,
                source,
                patch: nextPatch,
            }),
        })
        const data = await readJsonResponse(response)

        if (response.status === 409) {
            const latest = data?.detail?.latest
            if (latest) {
                workspaceRevisionRef.current = Number(latest.revision || 0)
                const normalizedLatest = normalizeIncomingWorkspaceState(latest.state).state
                lastPersistedWorkspaceRef.current = JSON.stringify(normalizedLatest)

                if (attempt < 1) {
                    const rebasedState = {
                        ...normalizedLatest,
                        ...nextPatch,
                    }
                    const rebasedPatch = buildSharedWorkspacePatch(
                        normalizedLatest,
                        rebasedState
                    )

                    if (Object.keys(rebasedPatch).length) {
                        return persistWorkspacePatchNow(rebasedPatch, source, attempt + 1)
                    }

                    setWorkspaceSyncStatus('saved')
                    setWorkspaceSyncLabel('Saved')
                    setWorkspaceLastSavedAt(latest.updated_at || null)
                    return {
                        status: 'ok',
                        revision: workspaceRevisionRef.current,
                        state: normalizedLatest,
                        updated_at: latest.updated_at || null,
                    }
                }
            }
            setWorkspaceSyncStatus('error')
            setWorkspaceSyncLabel('Sync conflict')
            throw new Error(data?.detail?.error || 'Project revision conflict.')
        }

        if (!response.ok || data.status !== 'ok') {
            setWorkspaceSyncStatus('error')
            setWorkspaceSyncLabel('Sync error')
            throw new Error(data.error || 'Failed to persist workspace state.')
        }

        workspaceRevisionRef.current = Number(data.revision || 0)
        lastPersistedWorkspaceRef.current = JSON.stringify(normalizeIncomingWorkspaceState(data.state).state)
        setWorkspaceLastSavedAt(data.updated_at || null)
        setWorkspaceSyncStatus('saved')
        setWorkspaceSyncLabel('Saved')
        return data
    }

    function persistWorkspacePatch(nextPatch, source = workspaceSessionIdRef.current) {
        const safePatch = nextPatch && typeof nextPatch === 'object'
            ? nextPatch
            : {}

        if (isGuest || !Object.keys(safePatch).length) {
            if (isGuest) {
                setWorkspaceSyncStatus('saved')
                setWorkspaceSyncLabel('Temporary')
                setWorkspaceLastSavedAt(null)
            }
            return Promise.resolve({
                status: 'ok',
                revision: workspaceRevisionRef.current,
                state: lastPersistedWorkspaceRef.current
                    ? JSON.parse(lastPersistedWorkspaceRef.current)
                    : null,
                updated_at: workspaceLastSavedAt,
                temporary: isGuest,
            })
        }

        const queuedPersist = workspacePatchQueueRef.current
            .catch(() => undefined)
            .then(() => persistWorkspacePatchNow(safePatch, source, 0))

        workspacePatchQueueRef.current = queuedPersist.then(
            () => undefined,
            () => undefined,
        )

        return queuedPersist
    }

    function persistBrokerSelectionWorkspaceState(nextTradeSelection, sourceSuffix = 'broker-selection') {
        if (!isAuthenticated || !isWorkspaceReady || !authToken || isGuest) {
            return Promise.resolve({
                status: 'skipped',
                reason: 'workspace_unavailable',
            })
        }

        const normalizedTradeSelection = nextTradeSelection && typeof nextTradeSelection === 'object'
            ? {
                ...DEFAULT_TRADE,
                ...(tradeState && typeof tradeState === 'object' ? tradeState : {}),
                ...nextTradeSelection,
            }
            : {
                ...DEFAULT_TRADE,
                ...(tradeState && typeof tradeState === 'object' ? tradeState : {}),
            }
        const previousState = lastPersistedWorkspaceRef.current
            ? JSON.parse(lastPersistedWorkspaceRef.current)
            : null
        const nextWorkspaceState = buildWorkspaceStateForSync({
            nextTrade: normalizedTradeSelection,
        })
        const patch = buildSharedWorkspacePatch(previousState, nextWorkspaceState)

        if (!Object.keys(patch).length) {
            return Promise.resolve({
                status: 'ok',
                skipped: true,
                revision: workspaceRevisionRef.current,
            })
        }

        return persistWorkspacePatch(
            patch,
            `${workspaceSessionIdRef.current}:broker-selection:${sourceSuffix}`,
        )
    }

    async function listWorkspaceSaves() {
        if (isGuest) {
            return []
        }

        const query = authToken
            ? `workspace_id=${DEFAULT_WORKSPACE_ID}&limit=20`
            : `user_id=${DEFAULT_WORKSPACE_USER_ID}&workspace_id=${DEFAULT_WORKSPACE_ID}&limit=20`
        const response = await fetch(
            buildApiUrl(`/workspace/saves?${query}`),
            {
                headers: buildAuthHeaders(),
            }
        )

        const data = await readJsonResponse(response)

        if (!response.ok || data.status !== 'ok') {
            throw new Error(extractApiErrorMessage(data, 'Failed to list workspace snapshots.'))
        }

        return Array.isArray(data.saves) ? data.saves : []
    }

    async function refreshWorkspaceSaves() {
        if (isGuest) {
            setWorkspaceSaves([])
            setHasWorkspaceSavesHydrated(true)
            return []
        }

        const saves = await listWorkspaceSaves()
        setWorkspaceSaves(saves)
        setHasWorkspaceSavesHydrated(true)
        return saves
    }

    async function applyWorkspaceState(nextState, options = {}) {
        const {
            revision = workspaceRevisionRef.current,
            source = 'remote',
            logMessage = '',
            shouldApplyStrategy = false,
            forceSessionChartBars = null,
        } = options
        const shouldSanitizeStaleConsoleJobs = hasStaleSharedConsoleJobs(nextState?.uiState)
        const normalizedState = normalizeWorkspaceStatePayload(nextState, loadedChartSettingsRef.current)
        const workspaceChartSettings = normalizeChartSettings(normalizedState.chartSettings)
        const nextChartSettings = forceSessionChartBars !== null
            ? buildSessionChartSettings(workspaceChartSettings, forceSessionChartBars)
            : workspaceChartSettings
        const strategyAliasChartSettings = buildStrategyAliasContextChartSettings(
            nextChartSettings,
            normalizedState.strategy,
            normalizedState.backtestStrategySet,
        )
        const chartNeedsSync = !areChartSettingsEqual(nextChartSettings, loadedChartSettingsRef.current)
        const backendChartSettings = shouldApplyStrategy ? strategyAliasChartSettings : nextChartSettings
        const backendChartNeedsSync = !areChartSettingsEqual(
            backendChartSettings,
            loadedChartSettingsRef.current,
        )

        if (backendChartNeedsSync) {
            await applyChartToBackend(backendChartSettings)
            await waitForChartReady(
                buildApiUrl,
                buildAuthHeaders,
                Array.isArray(backendChartSettings?.indicators) ? backendChartSettings.indicators.length : 0,
            )
        }

        let appliedStrategyResponse = normalizedState.strategyResponse

        if (shouldApplyStrategy) {
            try {
                const resolvedStrategy = resolveStrategyAliasesInStrategy(
                    normalizedState.strategy,
                    strategyAliasChartSettings,
                )

                setConsoleStatusState((current) => ({
                    ...current,
                    strategyError: '',
                    strategyPending: true,
                    backtestBusy: true,
                    backtestPending: false,
                    resultsPending: false,
                }))

                const response = await fetch(
                    buildApiUrl('/strategy/configure?source=workspace_load'),
                    {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            ...buildAuthHeaders(),
                        },
                        body: JSON.stringify({
                            strategy: resolvedStrategy,
                        }),
                    }
                )
                const data = await readJsonResponse(response)

                if (!response.ok || data.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, 'Failed to apply loaded workspace strategy.'))
                }

                appliedStrategyResponse = data
                setConsoleStatusState((current) => ({
                    ...current,
                    strategyError: '',
                    strategyPending: false,
                    backtestBusy: false,
                    backtestPending: false,
                    resultsPending: false,
                }))
            } catch (error) {
                setConsoleStatusState((current) => ({
                    ...current,
                    strategyError: error.message || 'Failed to apply loaded workspace strategy.',
                    strategyPending: false,
                    backtestBusy: false,
                    backtestPending: false,
                    resultsPending: false,
                }))
                appendSystemLog(`Project strategy apply failed: ${error.message || 'unknown error'}`, 'error')
            }
        }

        workspaceRevisionRef.current = Number(revision || 0)
        lastPersistedWorkspaceRef.current = JSON.stringify(normalizedState)
        setWorkspaceLastSavedAt(options?.updatedAt || workspaceLastSavedAt)
        setStrategy(normalizedState.strategy)
        setBacktestStrategySet(Array.isArray(normalizedState.backtestStrategySet) ? normalizedState.backtestStrategySet : [])
        setBacktest(normalizedState.backtest)
        setBatchState(normalizedState.batch)
        setResearchState(normalizedState.research)
        setTradeState(normalizedState.trade)
        setChartSettings(nextChartSettings)
        setLoadedChartSettings(nextChartSettings)
        loadedChartSettingsRef.current = nextChartSettings
        setChartDrawings(normalizedState.drawings)
        setVisibleIndicatorColumnsSnapshot(normalizedState.visibleIndicatorColumns)
        setLastStrategyResponse(appliedStrategyResponse || null)
        if (normalizedState.backtestRunResponse) {
            setLastBacktestResponse((current) => mergeBacktestResponsePreservingFull(current, normalizedState.backtestRunResponse || null))
        } else if (String(source || '').trim() === 'server-load') {
            // Fresh backend load without a stored backtest payload should clear
            // stale local run state from previous sessions.
            setLastBacktestResponse(null)
        }
        setBacktestChartBuffer(buildBacktestChartBufferSummary(normalizedState.backtestChartBuffer))
        setChartBacktestOverlay(buildBacktestChartBufferSummary(normalizedState.chartBacktestOverlay))
        setUiState(normalizedState.uiState)
        setChartViewId((current) => current + 1)
        setWorkspaceSyncStatus('saved')
        setWorkspaceSyncLabel((options?.temporary || isGuest) ? 'Temporary' : 'Saved')

        if (chartNeedsSync) {
            setChartRunId((current) => current + 1)
        }

        if (logMessage) {
            appendSystemLog(logMessage)
        }

        if (shouldSanitizeStaleConsoleJobs && isAuthenticated && !isGuest) {
            void persistWorkspacePatch(
                { uiState: normalizedState.uiState },
                `${workspaceSessionIdRef.current}:sanitize-stale-console-jobs`,
            ).catch((error) => {
                console.error('Failed to sanitize stale console jobs:', error)
            })
            appendSystemLog('Cleared stale console jobs from the shared workspace.')
        }

        return {
            normalizedState,
            source,
        }
    }

    async function applyChartToBackend(nextSettings) {
        const normalized = normalizeChartSettings(nextSettings)

        const response = await fetch(buildApiUrl('/chart/set-request'), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...buildAuthHeaders(),
            },
            body: JSON.stringify({
                symbol: normalized.symbol,
                timeframe: normalized.timeframe,
                bars: normalized.bars,
                indicators: buildBackendIndicatorsPayload(normalized.indicators),
            }),
        })

        const data = await readJsonResponse(response)

        if (!response.ok || data.status !== 'ok') {
            throw new Error(data.error || 'Failed to apply chart settings.')
        }

        return normalized
    }

    async function syncChartSettings(nextSettings) {
        const normalized = normalizeChartSettings(nextSettings)

        setChartSettings(normalized)

        if (areChartSettingsEqual(normalized, loadedChartSettingsRef.current)) {
            return true
        }

        const requestId = latestChartRequestIdRef.current + 1
        latestChartRequestIdRef.current = requestId
        try {
            const applied = await applyChartToBackend(normalized)

            if (requestId !== latestChartRequestIdRef.current) {
                return
            }

            setChartSettings(applied)
            setLoadedChartSettings(applied)
            loadedChartSettingsRef.current = applied
            setLastStrategyResponse(null)
            setChartRunId((current) => current + 1)

            if (isWorkspaceReady && isAuthenticated) {
                try {
                    await persistWorkspacePatch(
                        {
                            chartSettings: sanitizeWorkspaceChartSettingsForPersistence(applied),
                        },
                        `${workspaceSessionIdRef.current}:chart-settings`,
                    )
                } catch (persistError) {
                    console.error('Failed to persist chart settings after sync:', persistError)
                    appendSystemLog(`Workspace sync failed: ${persistError.message || 'chart settings save error'}.`)
                }
            }

            return true
        } catch (error) {
            if (requestId !== latestChartRequestIdRef.current) {
                return false
            }

            console.error('Failed to sync chart settings:', error)
            appendSystemLog(error.message || 'Could not update chart settings.', 'error')
            setChartSettings(loadedChartSettingsRef.current)
            return false
        }
    }

    async function requestChartSymbolChange(nextSymbol) {
        const normalizedSymbol = String(nextSymbol || '').trim().toUpperCase()
        if (!normalizedSymbol) {
            return false
        }

        const nextSettings = normalizeChartSettings({
            ...chartSettings,
            symbol: normalizedSymbol,
        })
        const targetProfile = findBrokerProfileForSymbol(normalizedSymbol, brokerProfiles)
        const targetMarketDomain = resolveBrokerProfileMarketDomain(targetProfile)
        const activeMarketDomain = resolveBrokerProfileMarketDomain(activeHeaderBrokerProfile)

        if (
            targetProfile
            && targetProfile.id !== activeHeaderBrokerProfileId
            && targetMarketDomain
            && activeMarketDomain
            && targetMarketDomain !== activeMarketDomain
        ) {
            void handleHeaderBrokerProfileChange(targetProfile.id, {
                pendingChartSettings: nextSettings,
                logMessage: `Chart symbol "${normalizedSymbol}" belongs to ${targetProfile.label}. Switching broker stack before loading ${normalizedSymbol} ${nextSettings.timeframe}.`,
            })
            return true
        }

        return syncChartSettings(nextSettings)
    }

    function handleSettingsChange(nextSettings) {
        void syncChartSettings(nextSettings)
    }

    function updateSharedChartUiState(transformer, sourceSuffix = 'chart-ui') {
        const normalizedCurrent = normalizeSharedWorkspaceUiState(uiStateRef.current)
        const currentChartUiState = normalizedCurrent?.chart && typeof normalizedCurrent.chart === 'object'
            ? normalizedCurrent.chart
            : DEFAULT_SHARED_WORKSPACE_UI_STATE.chart
        const nextChartUiState = typeof transformer === 'function'
            ? transformer(currentChartUiState)
            : {
                ...currentChartUiState,
                ...(transformer && typeof transformer === 'object' ? transformer : {}),
            }

        const nextUiState = normalizeSharedWorkspaceUiState({
            ...normalizedCurrent,
            chart: {
                ...currentChartUiState,
                ...(nextChartUiState && typeof nextChartUiState === 'object' ? nextChartUiState : {}),
            },
        })

        uiStateRef.current = nextUiState
        setUiState(nextUiState)

        if (!isWorkspaceReady || !isAuthenticated) {
            return
        }

        void persistWorkspacePatch(
            { uiState: nextUiState },
            `${workspaceSessionIdRef.current}:${sourceSuffix}`,
        ).catch((error) => {
            console.error('Failed to persist chart UI state:', error)
            appendSystemLog(`Workspace sync failed: ${error.message || 'chart ui save error'}.`)
        })
    }

    function handleHeaderFieldChange(field, value) {
        if (isGuest && ['symbol', 'timeframe'].includes(String(field || '').trim().toLowerCase())) {
            return
        }
        void syncChartSettings({
            ...chartSettings,
            [field]: value,
        })
    }

    function handlePrecisionStep(step) {
        const currentPrecision = Number(chartSettings.precision ?? 5)
        const nextPrecision = Math.max(0, Math.min(10, currentPrecision + step))
        handleHeaderFieldChange('precision', nextPrecision)
    }

    function handleToggleScrollChartToEndOnTickIncoming() {
        updateSharedChartUiState(
            (currentChartUiState) => ({
                ...currentChartUiState,
                scrollChartToEndOnTickIncoming: !currentChartUiState.scrollChartToEndOnTickIncoming,
            }),
            'chart-scroll-toggle',
        )
    }

    function handleToggleShowVolumePanel() {
        updateSharedChartUiState(
            (currentChartUiState) => ({
                ...currentChartUiState,
                showVolumePanel: !currentChartUiState.showVolumePanel,
            }),
            'chart-volume-panel-toggle',
        )
    }

    function handleVolumeModeChange(nextMode) {
        const safeMode = String(nextMode || '').trim().toLowerCase()
        updateSharedChartUiState(
            (currentChartUiState) => ({
                ...currentChartUiState,
                volumeMode: ['volume', 'tick_volume', 'real_volume'].includes(safeMode) ? safeMode : 'volume',
            }),
            'chart-volume-mode',
        )
    }

    function handleMetaFontSizeChange(nextFontSize) {
        updateSharedChartUiState(
            (currentChartUiState) => ({
                ...currentChartUiState,
                metaFontSize: nextFontSize,
            }),
            'chart-meta-font',
        )
    }

    function handlePendingLineColorChange(nextColor) {
        updateSharedChartUiState(
            (currentChartUiState) => ({
                ...currentChartUiState,
                pendingLineColor: nextColor,
            }),
            'chart-pending-line-color',
        )
    }

    function handleTradeMarkerModeChange(nextMode) {
        updateSharedChartUiState(
            (currentChartUiState) => ({
                ...currentChartUiState,
                tradeMarkerMode: ['trader', 'backtest', 'both'].includes(String(nextMode || '').trim().toLowerCase())
                    ? String(nextMode).trim().toLowerCase()
                    : 'trader',
            }),
            'chart-trade-marker-mode',
        )
    }

    function commitSymbolInput() {
        if (isGuest) {
            setSymbolInputValue(chartSettings.symbol)
            return
        }
        const nextSymbol = symbolInputValue.trim().toUpperCase()
        const knownSymbols = Array.isArray(knownChartSymbols) ? knownChartSymbols : []
        const hasKnownSymbol = knownSymbols.includes(nextSymbol)
        const looksLikeKnownPrefix = !hasKnownSymbol && knownSymbols.some((symbol) => symbol.startsWith(nextSymbol))

        if (!nextSymbol) {
            setSymbolInputValue(chartSettings.symbol)
            return
        }

        if (chartSymbolCatalog?.exhaustive && !hasKnownSymbol) {
            setSymbolInputValue(chartSettings.symbol)
            appendSystemLog(`Chart symbol "${nextSymbol}" is not available in the current MT5 symbol catalog.`, 'warn')
            return
        }

        if (!chartSymbolCatalog?.exhaustive && looksLikeKnownPrefix) {
            setSymbolInputValue(chartSettings.symbol)
            appendSystemLog(`Chart symbol "${nextSymbol}" looks incomplete. Select a full symbol name.`, 'warn')
            return
        }

        if (nextSymbol === chartSettings.symbol) {
            setSymbolInputValue(nextSymbol)
            return
        }

        void requestChartSymbolChange(nextSymbol)
    }

    function handleSymbolInputChange(rawValue) {
        if (isGuest) {
            setSymbolInputValue(chartSettings.symbol)
            return
        }
        const nextSymbol = String(rawValue || '').trim().toUpperCase()
        setSymbolInputValue(nextSymbol)

        if (!nextSymbol || nextSymbol === chartSettings.symbol) {
            return
        }

        if (knownChartSymbols.includes(nextSymbol)) {
            void requestChartSymbolChange(nextSymbol)
        }
    }

    function handleBacktestExecuted(payload) {
        const hydratedStrategyResponse = hydrateBacktestResponsePayload(payload?.strategyResponse || null) || {}
        const normalizedRunChartSettings = normalizeChartSettings(payload.chartSettings)
        const resolvedStrategies = Array.isArray(payload?.strategies)
            ? payload.strategies
            : (Array.isArray(hydratedStrategyResponse?.request?.strategies) ? hydratedStrategyResponse.request.strategies : [])
        const resolvedBacktest = mergeBacktestDefaults(payload.backtest || backtest, normalizedRunChartSettings, activeHeaderBrokerProfile)
        const snapshotKey = buildResultsSnapshotKey({
            chartSettings: normalizedRunChartSettings,
            strategy: payload.strategy,
            strategies: resolvedStrategies,
            backtest: resolvedBacktest,
        })
        const workspaceUserId = getActiveWorkspaceUserId()
        const completedBacktestResponse = {
            ...hydratedStrategyResponse,
            summary_only: false,
            snapshot_key: snapshotKey,
            chart_snapshot_available: true,
        }
        const responseContext = {
            chartSettings: normalizedRunChartSettings,
            strategy: payload.strategy,
            strategies: resolvedStrategies,
            backtest: resolvedBacktest,
        }
        const persistedBacktestSummary = buildBacktestResponseSummary(completedBacktestResponse, responseContext)
        const persistedStrategySummary = buildBacktestResponseSummary(hydratedStrategyResponse, responseContext)
        const nextBacktestChartBuffer = buildBacktestChartBufferFromResponse(completedBacktestResponse)

        setBacktestStrategySet(resolvedStrategies)
        setBacktest(resolvedBacktest)
        setLastStrategyResponse(hydratedStrategyResponse)
        const payloadRows = Number(hydratedStrategyResponse?.rows || 0)
        const payloadHasStats = Boolean(hydratedStrategyResponse?.stats)
        const payloadResultCount = Array.isArray(hydratedStrategyResponse?.results) ? hydratedStrategyResponse.results.length : 0
        const payloadMarkerCount = Array.isArray(hydratedStrategyResponse?.trade_markers) ? hydratedStrategyResponse.trade_markers.length : 0
        const payloadBalanceSeriesCount = Array.isArray(hydratedStrategyResponse?.stats?.account_balance_series)
            ? hydratedStrategyResponse.stats.account_balance_series.length
            : 0
        const payloadDrawdownAmountSeriesCount = Array.isArray(hydratedStrategyResponse?.stats?.drawdown_amount_series)
            ? hydratedStrategyResponse.stats.drawdown_amount_series.length
            : 0
        const payloadDrawdownPctSeriesCount = Array.isArray(hydratedStrategyResponse?.stats?.drawdown_pct_series)
            ? hydratedStrategyResponse.stats.drawdown_pct_series.length
            : 0
        setLastBacktestResponse(completedBacktestResponse)
        setBacktestChartBuffer(nextBacktestChartBuffer)
        appendSystemLog(
            `Backtest payload received: rows=${payloadRows}, stats=${payloadHasStats ? 'yes' : 'no'}, results=${payloadResultCount}, markers=${payloadMarkerCount}, balance_series=${payloadBalanceSeriesCount}, drawdown_amount_series=${payloadDrawdownAmountSeriesCount}, drawdown_pct_series=${payloadDrawdownPctSeriesCount}.`,
            payloadHasStats ? 'success' : 'warn'
        )
        saveResultsChartSnapshotToStorage(workspaceUserId, DEFAULT_WORKSPACE_ID, {
            snapshotKey,
            chartSettings: normalizedRunChartSettings,
            strategy: payload.strategy,
            strategies: resolvedStrategies,
            backtest: resolvedBacktest,
            response: completedBacktestResponse,
            savedAt: Date.now(),
        })
        setHasStoredResultsCharts(true)
        setConsoleStatusState((current) => ({
            ...current,
            strategyError: '',
            backtestError: '',
            resultsError: '',
            strategyPending: false,
            backtestBusy: false,
            backtestPending: false,
            resultsPending: false,
        }))

        if (isWorkspaceReady && isAuthenticated) {
            void persistWorkspacePatch(
                {
                    strategy: payload.strategy,
                    backtestStrategySet: resolvedStrategies,
                    backtest: resolvedBacktest,
                    strategyResponse: persistedStrategySummary,
                    backtestRunResponse: persistedBacktestSummary,
                    backtestChartBuffer: buildBacktestChartBufferSummary(nextBacktestChartBuffer),
                },
                `${workspaceSessionIdRef.current}:backtest-complete:${snapshotKey}`,
            ).catch((error) => {
                console.error('Failed to persist completed backtest summary:', error)
                appendSystemLog(`Workspace sync failed: ${error.message || 'backtest summary save error'}.`)
            })
        }
    }

    function handleLoadStoredResultsCharts() {
        const workspaceUserId = getActiveWorkspaceUserId()
        const currentSnapshotKey = String(lastBacktestResponse?.snapshot_key || '').trim()
        const storedSnapshot = loadResultsChartSnapshotFromStorage(workspaceUserId, DEFAULT_WORKSPACE_ID)

        if (!storedSnapshot?.response) {
            appendSystemLog('No stored results chart snapshot is available for this workspace.', 'warn')
            setHasStoredResultsCharts(false)
            return
        }

        if (currentSnapshotKey && String(storedSnapshot?.snapshotKey || '') !== currentSnapshotKey) {
            appendSystemLog('Stored results charts do not match the current workspace config. Re-run the backtest to inspect charts.', 'warn')
            setHasStoredResultsCharts(false)
            return
        }

        const hydratedStoredResponse = hydrateBacktestResponsePayload(storedSnapshot.response || null)
        const derivedBacktestChartBuffer = buildBacktestChartBufferFromResponse(hydratedStoredResponse)
        setLastBacktestResponse((current) => mergeBacktestResponsePreservingFull(current, hydratedStoredResponse))
        setBacktestChartBuffer((current) => buildBacktestChartBufferSummary(current) || derivedBacktestChartBuffer)
        setHasStoredResultsCharts(true)
        appendSystemLog('Loaded stored results charts for the current workspace.', 'success')

        if (derivedBacktestChartBuffer && !buildBacktestChartBufferSummary(backtestChartBuffer)) {
            void persistWorkspacePatch(
                {
                    backtestChartBuffer: buildBacktestChartBufferSummary(derivedBacktestChartBuffer),
                },
                `${workspaceSessionIdRef.current}:backtest-buffer-upgrade:${String(storedSnapshot?.snapshotKey || '').trim() || 'stored'}`,
            ).catch((error) => {
                console.error('Failed to persist derived backtest chart buffer:', error)
                appendSystemLog(`Workspace sync failed: ${error.message || 'backtest chart buffer save error'}.`)
            })
        }
    }

    function handleResolveLoadedBacktestResponse() {
        const workspaceUserId = getActiveWorkspaceUserId()
        const resolvedLoadedBacktest = resolveLoadedBacktestResponse(
            lastBacktestResponse,
            workspaceUserId,
            DEFAULT_WORKSPACE_ID,
            backtestChartBuffer,
        )

        if (
            (
                resolvedLoadedBacktest.source === 'stored_snapshot'
                || resolvedLoadedBacktest.source === 'workspace_buffer'
            )
            && Boolean(lastBacktestResponse?.summary_only)
        ) {
            setLastBacktestResponse((current) => mergeBacktestResponsePreservingFull(current, resolvedLoadedBacktest.response))
        }

        return resolvedLoadedBacktest.response || null
    }

    useEffect(() => {
        const workspaceUserId = authUser?.workspace_user_id || DEFAULT_WORKSPACE_USER_ID
        const currentSnapshotKey = String(lastBacktestResponse?.snapshot_key || '').trim()
        const resolvedLoadedBacktest = resolveLoadedBacktestResponse(
            lastBacktestResponse,
            workspaceUserId,
            DEFAULT_WORKSPACE_ID,
            backtestChartBuffer,
        )
        const hasMatchingStoredSnapshot = Boolean(
            currentSnapshotKey
            && (
                resolvedLoadedBacktest.source === 'stored_snapshot'
                || resolvedLoadedBacktest.source === 'memory'
                || resolvedLoadedBacktest.source === 'workspace_buffer'
            )
        )
        setHasStoredResultsCharts(hasMatchingStoredSnapshot)

        if (
            (
                resolvedLoadedBacktest.source === 'stored_snapshot'
                || resolvedLoadedBacktest.source === 'workspace_buffer'
            )
            && Boolean(lastBacktestResponse?.summary_only)
        ) {
            setLastBacktestResponse((current) => mergeBacktestResponsePreservingFull(current, resolvedLoadedBacktest.response))
        }
    }, [
        backtestChartBuffer,
        authUser?.workspace_user_id,
        lastBacktestResponse?.results?.length,
        lastBacktestResponse?.snapshot_key,
        lastBacktestResponse?.summary_only,
        lastBacktestResponse?.trade_markers?.length,
    ])

    function handleStrategyStatusChange(payload = {}) {
        setConsoleStatusState((current) => ({
            ...current,
            strategyError: payload.error != null ? String(payload.error || '').trim() : current.strategyError,
            strategyDebugError: payload.strategyDebugError != null ? String(payload.strategyDebugError || '').trim() : current.strategyDebugError,
            strategyPending: payload.strategyPending != null ? Boolean(payload.strategyPending) : current.strategyPending,
            strategyDebugPending: payload.strategyDebugPending != null ? Boolean(payload.strategyDebugPending) : current.strategyDebugPending,
            strategyDebugReady: payload.strategyDebugReady != null ? Boolean(payload.strategyDebugReady) : current.strategyDebugReady,
            backtestBusy: payload.backtestBusy != null ? Boolean(payload.backtestBusy) : current.backtestBusy,
            backtestPending: payload.backtestPending != null ? Boolean(payload.backtestPending) : current.backtestPending,
            resultsPending: payload.resultsPending != null ? Boolean(payload.resultsPending) : current.resultsPending,
        }))
    }

    function handleBacktestStatusChange(payload = {}) {
        setConsoleStatusState((current) => ({
            ...current,
            backtestError: String(payload.backtestError || '').trim(),
            resultsError: String(payload.resultsError || '').trim(),
            strategyPending: Boolean(payload.strategyPending),
            backtestBusy: Boolean(payload.backtestBusy),
            backtestPending: Boolean(payload.backtestPending),
            resultsPending: Boolean(payload.resultsPending),
        }))
    }

    function handleBacktestRunStarted() {
        setLastBacktestResponse(null)
        setHasStoredResultsCharts(false)

        if (isWorkspaceReady && isAuthenticated) {
            void persistWorkspacePatch(
                {
                    backtestRunResponse: null,
                },
                `${workspaceSessionIdRef.current}:backtest-reset`,
            ).catch((error) => {
                console.error('Failed to clear previous backtest summary before a new run:', error)
                appendSystemLog(`Workspace sync failed: ${error.message || 'backtest reset save error'}.`)
            })
        }
    }

    async function handleLoadBacktestFlagsIntoChart() {
        const nextOverlay = buildBacktestChartBufferSummary(backtestChartBuffer)
        if (!nextOverlay) {
            appendSystemLog('No completed backtest flag buffer is available to load into the chart.', 'warn')
            return
        }

        const currentVisualChartSettings = normalizeChartSettings(loadedChartSettingsRef.current || chartSettings)
        const nextChartSettings = buildChartSettingsForBacktestOverlay(
            currentVisualChartSettings,
            nextOverlay,
            chartHistoryState,
        )

        setChartBacktestOverlay(nextOverlay)
        handleTradeMarkerModeChange('backtest')

        const chartWasAdjusted = !areChartSettingsEqual(nextChartSettings, currentVisualChartSettings)
        if (chartWasAdjusted) {
            const didApply = await syncChartSettings(nextChartSettings)
            if (!didApply) {
                appendSystemLog('Backtest flags were loaded, but the chart could not be synchronized to the required market window.', 'warn')
            }
        }

        if (isWorkspaceReady && isAuthenticated) {
            void persistWorkspacePatch(
                {
                    chartBacktestOverlay: nextOverlay,
                },
                `${workspaceSessionIdRef.current}:chart-backtest-overlay`,
            ).catch((error) => {
                console.error('Failed to persist chart backtest overlay:', error)
                appendSystemLog(`Workspace sync failed: ${error.message || 'chart backtest overlay save error'}.`)
            })
        }

        appendSystemLog(
            chartWasAdjusted
                ? `Loaded the latest backtest flags into the chart overlay and synchronized the chart to ${nextChartSettings.symbol} ${nextChartSettings.timeframe} (${Number(nextChartSettings.bars || 0).toLocaleString()} bars).`
                : 'Loaded the latest backtest flags into the chart overlay.',
            'success',
        )
    }

    async function handleLoadBacktestIndicatorsIntoChart() {
        const backtestRequest = lastBacktestResponse?.request
        if (!backtestRequest || typeof backtestRequest !== 'object') {
            appendSystemLog('No loaded backtest request is available to derive indicators for the chart.', 'warn')
            return
        }

        const backtestStrategy = backtestRequest?.strategy && typeof backtestRequest.strategy === 'object'
            ? backtestRequest.strategy
            : {}
        const backtestStrategyEntries = Array.isArray(backtestRequest?.strategies)
            ? backtestRequest.strategies.filter((entry) => entry && typeof entry === 'object')
            : []
        const backtestIndicators = Array.isArray(backtestRequest?.indicators)
            ? backtestRequest.indicators
            : []

        if (!backtestIndicators.length && !backtestStrategyEntries.length && !Object.keys(backtestStrategy).length) {
            appendSystemLog('The loaded backtest does not carry an indicator plan that can be applied to the chart.', 'warn')
            return
        }

        const currentVisualChartSettings = normalizeChartSettings(loadedChartSettingsRef.current || chartSettings)
        const nextChartSettings = buildStrategyCollectionChartSettings(
            normalizeChartSettings({
                ...currentVisualChartSettings,
                indicators: [],
            }),
            backtestStrategy,
            backtestStrategyEntries,
            backtestIndicators,
        )

        setVisibleIndicatorColumnsSnapshot({})
        const didApply = await syncChartSettings(nextChartSettings)
        if (didApply) {
            appendSystemLog('Loaded indicators from the current backtest into the chart.', 'success')
        }
    }

    async function handleLoadStrategyIndicatorsIntoChart(strategyPayload, { label = 'strategy' } = {}) {
        if (!strategyPayload || typeof strategyPayload !== 'object') {
            appendSystemLog('No strategy payload is available to derive indicators for the chart.', 'warn')
            return
        }

        const currentVisualChartSettings = normalizeChartSettings(loadedChartSettingsRef.current || chartSettings)
        const nextChartSettings = buildStrategyCollectionChartSettings(
            normalizeChartSettings({
                ...currentVisualChartSettings,
                indicators: [],
            }),
            strategyPayload,
            [],
        )

        setVisibleIndicatorColumnsSnapshot({})
        const didApply = await syncChartSettings(nextChartSettings)
        if (didApply) {
            appendSystemLog(`Loaded indicators for "${String(label || 'strategy').trim() || 'strategy'}" into the chart.`, 'success')
        }
    }

    const canLoadBacktestIndicatorsIntoChart = Boolean(
        (
            Array.isArray(lastBacktestResponse?.request?.indicators)
            && lastBacktestResponse.request.indicators.length > 0
        )
        || (
            lastBacktestResponse?.request?.strategy
            && typeof lastBacktestResponse.request.strategy === 'object'
            && Object.keys(lastBacktestResponse.request.strategy).length > 0
        )
        || (
            Array.isArray(lastBacktestResponse?.request?.strategies)
            && lastBacktestResponse.request.strategies.length > 0
        )
    )

    function handleNeuralStatusChange(payload = {}) {
        setConsoleStatusState((current) => ({
            ...current,
            neuralError: Object.prototype.hasOwnProperty.call(payload, 'neuralError')
                ? String(payload.neuralError || '').trim()
                : current.neuralError,
            neuralPending: Object.prototype.hasOwnProperty.call(payload, 'neuralPending')
                ? Boolean(payload.neuralPending)
                : current.neuralPending,
            neuralReady: Object.prototype.hasOwnProperty.call(payload, 'neuralReady')
                ? Boolean(payload.neuralReady)
                : current.neuralReady,
        }))
    }

    function handleDrawingToolSelect(nextTool) {
        if (drawingUiState.isActive && drawingUiState.tool === nextTool) {
            setDrawingUiState((current) => ({
                ...current,
                isActive: false,
            }))
            return
        }

        setDrawingUiState((current) => ({
            ...current,
            tool: nextTool,
            isActive: true,
        }))
    }

    function handleClearAllDrawings() {
        setChartDrawings([])
        setDrawingUiState((current) => ({
            ...current,
            isActive: false,
        }))
    }

    function buildSystemLogRequestSource() {
        return `system_log_ui:${workspaceSessionIdRef.current}`
    }

    function buildSystemLogSessionLabel(at = Date.now()) {
        return `System log · ${formatSystemLogTimestamp(at)}`
    }

    function buildSystemLogSessionMetadata(extra = {}) {
        const activeChartSettings = normalizeChartSettings(loadedChartSettingsRef.current || DEFAULT_CHART_SETTINGS)
        return {
            workspace_id: DEFAULT_WORKSPACE_ID,
            workspace_session_id: workspaceSessionIdRef.current,
            workspace_save_id: String(currentWorkspaceSaveSnapshot?.id || currentWorkspaceSaveId || '').trim(),
            workspace_save_name: String(currentWorkspaceSaveSnapshot?.name || '').trim(),
            workspace_save_score: currentWorkspaceSaveSnapshot?.score ?? null,
            chart_symbol: String(activeChartSettings.symbol || '').trim().toUpperCase(),
            chart_timeframe: String(activeChartSettings.timeframe || '').trim().toUpperCase(),
            chart_bars: Math.max(1, Number(activeChartSettings.bars) || 1),
            loaded_chart_candles: Math.max(0, Number(chartHistoryState.loadedCandles) || 0),
            workspace_sync_status: String(workspaceSyncStatus || '').trim(),
            workspace_socket_status: String(workspaceSocketStatus || '').trim(),
            auth_mode: isAuthenticated ? 'authenticated' : 'local',
            auth_workspace_user_id: String(getActiveWorkspaceUserId()).trim(),
            auth_email: String(authUser?.email || '').trim(),
            ...extra,
        }
    }

    function scheduleSystemLogFlush(delayMs = 250) {
        if (typeof window === 'undefined') {
            return
        }

        if (systemLogFlushTimerRef.current) {
            window.clearTimeout(systemLogFlushTimerRef.current)
            systemLogFlushTimerRef.current = null
        }

        if (!isAuthenticated || !isWorkspaceReady || !authToken || isGuest) {
            return
        }

        systemLogFlushTimerRef.current = window.setTimeout(() => {
            systemLogFlushTimerRef.current = null
            void flushSystemLogQueue()
        }, Math.max(0, Number(delayMs) || 0))
    }

    async function flushSystemLogQueue({ sessionIdOverride = null } = {}) {
        if (systemLogFlushInFlightRef.current) {
            return systemLogFlushPromiseRef.current || false
        }

        if (!isAuthenticated || !isWorkspaceReady || !authToken || isGuest) {
            return false
        }

        const queuedEntries = Array.isArray(systemLogPersistQueueRef.current)
            ? [...systemLogPersistQueueRef.current]
            : []

        if (!queuedEntries.length) {
            return false
        }

        systemLogPersistQueueRef.current = []
        systemLogFlushInFlightRef.current = true
        systemLogFlushPromiseRef.current = (async () => {
            try {
                const response = await fetch(buildApiUrl('/workspace/system-log/entries'), {
                    method: 'POST',
                    headers: buildAuthHeaders({
                        'Content-Type': 'application/json',
                    }),
                    body: JSON.stringify({
                        workspace_id: DEFAULT_WORKSPACE_ID,
                        session_id: sessionIdOverride || systemLogSessionRef.current?.id || null,
                        source: buildSystemLogRequestSource(),
                        label: systemLogSessionRef.current?.label || buildSystemLogSessionLabel(),
                        metadata: buildSystemLogSessionMetadata({
                            reason: 'append_entries',
                        }),
                        entries: queuedEntries.map((entry) => ({
                            client_entry_id: entry.clientEntryId || entry.id || '',
                            message: entry.message,
                            level: entry.level,
                            source: entry.source,
                            scope: entry.scope,
                            category: entry.category,
                            context: entry.context || {},
                            created_at: Number(entry.createdAt || 0) > 0
                                ? Number(entry.createdAt) / 1000
                                : null,
                        })),
                    }),
                })
                const data = await readJsonResponse(response)

                if (!response.ok || data.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, 'Could not persist system log entries.'))
                }

                const nextSession = normalizeSystemLogSession(data.session)
                systemLogSessionRef.current = nextSession
                setSystemLogSession(nextSession)
                setSystemLogSessions((current) => mergeSystemLogSessions(current, nextSession ? [nextSession] : []))
                setSystemLogEntries((current) => mergeSystemLogEntries(current, data.entries || []))
                return true
            } catch (error) {
                systemLogPersistQueueRef.current = [
                    ...queuedEntries,
                    ...systemLogPersistQueueRef.current,
                ]
                console.error('Failed to persist system log entries:', error)
                return false
            } finally {
                systemLogFlushInFlightRef.current = false
                systemLogFlushPromiseRef.current = null
                if (systemLogPersistQueueRef.current.length > 0) {
                    scheduleSystemLogFlush(500)
                }
            }
        })()

        return systemLogFlushPromiseRef.current
    }

    function appendSystemLog(message, level = '', options = {}) {
        const createdAt = Date.now()
        const normalizedMessage = String(message || '').trim()
        if (!normalizedMessage) {
            return null
        }

        const nextLevel = classifySystemLogLevel(normalizedMessage, level)
        const nextScope = String(options?.scope || deriveSystemLogScope(normalizedMessage)).trim() || 'system'
        const nextCategory = String(options?.category || deriveSystemLogCategory(normalizedMessage, nextLevel, nextScope)).trim() || 'operator'
        const entry = normalizeSystemLogEntry({
            client_entry_id: `${workspaceSessionIdRef.current}:${createdAt}:${Math.random().toString(36).slice(2, 8)}`,
            created_at: createdAt,
            message: normalizedMessage,
            level: nextLevel,
            source: String(options?.source || 'console_ui').trim() || 'console_ui',
            scope: nextScope,
            category: nextCategory,
            context: buildSystemLogSessionMetadata({
                system_log_session_id: systemLogSessionRef.current?.id || null,
                category: nextCategory,
                scope: nextScope,
                ...(options?.context && typeof options.context === 'object' ? options.context : {}),
            }),
        })

        setSystemLogEntries((current) => mergeSystemLogEntries(current, [entry]))
        setSystemLogSession((current) => (
            current
                ? {
                    ...current,
                    entryCount: Number(current.entryCount || 0) + 1,
                    updatedAt: createdAt,
                    lastEntryAt: createdAt,
                }
                : current
        ))

        if (!isGuest) {
            systemLogPersistQueueRef.current = [
                ...systemLogPersistQueueRef.current,
                entry,
            ]
            scheduleSystemLogFlush()
        }
        return entry
    }

    async function handleStartNewSystemLog() {
        const startedAt = Date.now()

        if (!isAuthenticated || !isWorkspaceReady || !authToken || isGuest) {
            systemLogPersistQueueRef.current = []
            setSystemLogEntries([])
            const nextLocalSession = normalizeSystemLogSession({
                id: 0,
                label: buildSystemLogSessionLabel(startedAt),
                status: 'local',
                source: 'local_console',
                metadata: buildSystemLogSessionMetadata({
                    local_only: true,
                    reason: 'manual_start_without_auth',
                }),
                created_at: startedAt,
                updated_at: startedAt,
            })
            systemLogSessionRef.current = nextLocalSession
            setSystemLogSession(nextLocalSession)
            appendSystemLog(isGuest
                ? 'System log · Started a temporary guest log session.'
                : 'System log · Started a new local log session. Sign in to persist it in the backend.', 'info', {
                scope: 'system_log',
                category: 'audit',
            })
            return
        }

        await flushSystemLogQueue()

        try {
            const response = await fetch(buildApiUrl('/workspace/system-log/start'), {
                method: 'POST',
                headers: buildAuthHeaders({
                    'Content-Type': 'application/json',
                }),
                body: JSON.stringify({
                    workspace_id: DEFAULT_WORKSPACE_ID,
                    source: buildSystemLogRequestSource(),
                    label: buildSystemLogSessionLabel(startedAt),
                    metadata: buildSystemLogSessionMetadata({
                        reason: 'manual_start',
                    }),
                }),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Could not start a new system log.'))
            }

            const nextSession = normalizeSystemLogSession(data.session)
            systemLogPersistQueueRef.current = []
            setSystemLogEntries([])
            systemLogSessionRef.current = nextSession
            setSystemLogSession(nextSession)
            setSystemLogSessions((current) => mergeSystemLogSessions(
                current,
                nextSession ? [nextSession] : [],
                data.archived_session_ids || [],
            ))
            appendSystemLog('System log · Started a new audit session.', 'success', {
                scope: 'system_log',
                category: 'audit',
                context: {
                    archived_session_ids: Array.isArray(data.archived_session_ids) ? data.archived_session_ids : [],
                },
            })
        } catch (error) {
            console.error('Failed to start a new system log session:', error)
            appendSystemLog(`System log · Could not start a new session: ${error.message || 'unknown error'}`, 'error', {
                scope: 'system_log',
                category: 'failure',
            })
        }
    }

    useEffect(() => {
        setConsoleStatusState((current) => (
            current.backtestError || current.resultsError
                ? {
                    ...current,
                    backtestError: '',
                    resultsError: '',
                    backtestBusy: false,
                    backtestPending: false,
                    resultsPending: false,
                }
                : current
        ))
    }, [backtest])

    function handleInsertStrategyText(text) {
        if (!activeStrategyFieldId) {
            appendSystemLog(`Select a Strategy field before inserting "${text}".`)
            return
        }

        setStrategyInsertRequest({
            fieldId: activeStrategyFieldId,
            text,
            nonce: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        })
    }

    function updateIndicatorLineVisibility(indicatorId, columnName, isVisible, restoredPlacement = null) {
        const applyVisibilityToSettings = (settings) => normalizeChartSettings({
            ...settings,
            indicators: (settings?.indicators || []).map((indicator) => {
                if (indicator?.id !== indicatorId) {
                    return indicator
                }

                return {
                    ...indicator,
                    lines: (indicator.lines || []).map((line) => {
                        if (line?.columnName !== columnName) {
                            return line
                        }

                        if (!isVisible) {
                            const previousTarget = String(line?.target || '').trim().toLowerCase()
                            const nextHiddenTarget = previousTarget && previousTarget !== 'hidden'
                                ? previousTarget
                                : 'price'

                            return {
                                ...line,
                                target: 'hidden',
                                paneId: '',
                                hiddenTarget: nextHiddenTarget,
                                hiddenPaneId: nextHiddenTarget === 'separate'
                                    ? (line?.paneId || '')
                                    : '',
                            }
                        }

                        const currentTarget = String(line?.target || '').trim().toLowerCase()
                        const restoredTarget = String(restoredPlacement?.target || '').trim().toLowerCase()
                        const hiddenTarget = String(line?.hiddenTarget || '').trim().toLowerCase()
                        const nextTarget = restoredTarget && restoredTarget !== 'hidden'
                            ? restoredTarget
                            : hiddenTarget && hiddenTarget !== 'hidden'
                                ? hiddenTarget
                            : currentTarget && currentTarget !== 'hidden'
                                ? currentTarget
                                : ''
                        const defaultTarget = String(line?.defaultTarget || '').trim().toLowerCase()
                        const fallbackTarget = nextTarget || (defaultTarget && defaultTarget !== 'hidden' ? defaultTarget : '')

                        return {
                            ...line,
                            target: fallbackTarget,
                            paneId: fallbackTarget === 'separate'
                                ? (restoredPlacement?.paneId || line?.hiddenPaneId || line?.paneId || '')
                                : '',
                            hiddenTarget: '',
                            hiddenPaneId: '',
                        }
                    }),
                }
            }),
        })

        const nextChartSettings = applyVisibilityToSettings(loadedChartSettingsRef.current || chartSettings)

        setChartSettings(nextChartSettings)
        setLoadedChartSettings(nextChartSettings)
        loadedChartSettingsRef.current = nextChartSettings

        if (isWorkspaceReady && isAuthenticated) {
            void persistWorkspacePatch(
                {
                    chartSettings: sanitizeWorkspaceChartSettingsForPersistence(nextChartSettings),
                },
                `${workspaceSessionIdRef.current}:chart-indicator-visibility`,
            ).catch((error) => {
                console.error('Failed to persist indicator visibility change:', error)
                appendSystemLog(`Workspace sync failed: ${error.message || 'indicator visibility save error'}.`)
            })
        }
    }

    function buildResetChartSettings(baseSettings) {
        const normalizedBase = normalizeChartSettings(baseSettings)

        return normalizeChartSettings({
            symbol: normalizedBase.symbol,
            timeframe: normalizedBase.timeframe,
            bars: normalizedBase.bars,
            precision: normalizedBase.precision,
            indicators: [],
        })
    }

    async function handleResetChart() {
        try {
            const freshChartSettings = buildResetChartSettings(loadedChartSettingsRef.current || chartSettings)

            await applyChartToBackend(freshChartSettings)

            setDrawingUiState(DEFAULT_LOCAL_DRAWING_UI_STATE)
            setChartSettings(freshChartSettings)
            setLoadedChartSettings(freshChartSettings)
            loadedChartSettingsRef.current = freshChartSettings
            setChartDrawings([])
            setVisibleIndicatorColumnsSnapshot({})
            setChartViewId((current) => current + 1)
            setChartRunId((current) => current + 1)

            if (isWorkspaceReady && isAuthenticated) {
                try {
                    await persistWorkspacePatch(
                        {
                            chartSettings: sanitizeWorkspaceChartSettingsForPersistence(freshChartSettings),
                            drawings: [],
                            visibleIndicatorColumns: {},
                        },
                        `${workspaceSessionIdRef.current}:chart-reset`,
                    )
                } catch (persistError) {
                    console.error('Failed to persist reset chart state:', persistError)
                    appendSystemLog(`Workspace sync failed: ${persistError.message || 'chart reset save error'}.`)
                }
            }

            appendSystemLog('Reset chart visuals to a clean base. Indicators and drawings were cleared while Backtester and Trader flags stayed intact.', 'success')
        } catch (error) {
            console.error('Failed to reset chart:', error)
            appendSystemLog(error.message || 'Could not reset the chart.', 'error')
        }
    }

    function handleOpenStreamWindow() {
        setStreamLaunchDraft((current) => ({
            includeBacktest: Boolean(current?.includeBacktest && streamLaunchBacktestSource?.compatible),
            replayCandleCount: String(current?.replayCandleCount || ''),
            initialCapital: String(current?.initialCapital || DEFAULT_STREAM_LAUNCH_DRAFT.initialCapital),
            volumeMode: normalizeStreamVolumeMode(current?.volumeMode || DEFAULT_STREAM_LAUNCH_DRAFT.volumeMode),
        }))
        setStreamLaunchError('')
        setIsStreamLaunchOverlayOpen(true)
    }

    function handleCloseStreamLaunchOverlay() {
        setIsStreamLaunchOverlayOpen(false)
        setStreamLaunchError('')
    }

    function handleConfirmStreamWindowLaunch() {
        if (typeof window === 'undefined') {
            return
        }

        const initialCapital = parseStreamInitialCapital(streamLaunchDraft.initialCapital)
        if (!initialCapital) {
            setStreamLaunchError('The operating capital must be greater than zero.')
            return
        }

        let backtestReplay = null
        const compatibleBacktestResponse = streamLaunchBacktestSource?.compatible
            ? streamLaunchBacktestSource?.response
            : null
        const capitalPlan = buildStreamLaunchCapitalPlan({
            runtimeLike: streamLaunchRuntimeLike,
            backtestResponse: compatibleBacktestResponse,
            initialCapital,
            volumeMode: streamLaunchDraft.volumeMode,
        })

        if (capitalPlan.relativeBelowMinimum) {
            setStreamLaunchError('That capital is below the minimum live volume for relative sizing. Increase the capital or switch to minimum operation volume.')
            return
        }

        if (streamLaunchDraft.includeBacktest) {
            if (!streamLaunchBacktestSource?.compatible || !streamLaunchBacktestSource?.response) {
                setStreamLaunchError(streamLaunchBacktestSource?.reason || 'No compatible loaded backtest is available for this stream launch.')
                return
            }

            const replayCandleCount = parseStreamReplayCandleCount(streamLaunchDraft.replayCandleCount)
            if (String(streamLaunchDraft.replayCandleCount || '').trim() && !replayCandleCount) {
                setStreamLaunchError('The replay candle count must be a whole number greater than zero.')
                return
            }

            const filterStartTime = replayCandleCount
                ? resolveStreamReplayStartTimeFromCandles(streamLaunchBacktestSource.response, replayCandleCount)
                : null
            if (replayCandleCount && !filterStartTime) {
                setStreamLaunchError('Could not resolve that candle range from the selected backtest.')
                return
            }

            backtestReplay = buildStreamBacktestReplay(streamLaunchBacktestSource.response, {
                filterStartTime,
            })

            if (!backtestReplay) {
                setStreamLaunchError('The selected backtest does not have operations inside that replay range.')
                return
            }
        }

        const launchKey = buildStreamLaunchStorageKey()
        const tradeRuntimeSeed = buildStreamRuntimeSeed(streamLaunchRuntimeLike)
        const snapshot = {
            createdAt: new Date().toISOString(),
            chartSettings: loadedChartSettingsRef.current || loadedChartSettings || chartSettings,
            chartUi: {
                metaFontSize: uiState?.chart?.metaFontSize,
                showVolumePanel: uiState?.chart?.showVolumePanel,
                volumeMode: uiState?.chart?.volumeMode,
            },
            tradeRuntimeSeed,
            capitalPlan,
            backtestReplay,
        }

        try {
            window.localStorage.setItem(launchKey, JSON.stringify(snapshot))
        } catch (error) {
            console.error('Failed to persist stream launch snapshot:', error)
            appendSystemLog('Could not prepare the stream window snapshot.', 'error')
            return
        }

        const popupUrl = buildCanonicalStreamPopupUrl(launchKey)

        const popup = window.open(
            popupUrl.toString(),
            '_blank',
            'popup=yes,width=1680,height=960,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=no',
        )

        if (!popup) {
            appendSystemLog('The browser blocked the stream popup window.', 'error')
            return
        }

        popup.focus?.()
        setIsStreamLaunchOverlayOpen(false)
        setStreamLaunchError('')
        const launchSymbol = String(tradeRuntimeSeed?.symbol || snapshot.chartSettings?.symbol || 'EURUSD').toUpperCase()
        const launchTimeframe = String(tradeRuntimeSeed?.timeframe || snapshot.chartSettings?.timeframe || 'M1').toUpperCase()
        appendSystemLog(
            backtestReplay
                ? `Opened stream view for ${launchSymbol} ${launchTimeframe} with backtest replay.`
                : `Opened stream view for ${launchSymbol} ${launchTimeframe}.`,
            'success'
        )
    }

    function clearStoredAuthToken() {
        if (typeof window !== 'undefined') {
            window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
        }
    }

    function persistStoredAuthToken(nextToken) {
        if (typeof window !== 'undefined') {
            window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, nextToken)
        }
    }

    function prepareWorkspaceIdentitySwitch() {
        setIsWorkspaceReady(false)
        setWorkspaceSaves([])
        setHasWorkspaceSavesHydrated(false)
        setLastRestoredWorkspaceSaveId('')
        setWorkspaceLastSavedAt(null)
        setWorkspaceSyncStatus('syncing')
        setWorkspaceSyncLabel('Syncing...')
        setWorkspaceSocketStatus('connecting')
        workspaceRevisionRef.current = 0
        lastPersistedWorkspaceRef.current = ''
        setSystemLogEntries([])
        setSystemLogSession(null)
        setSystemLogSessions([])
        setIsSystemLogLoading(false)
        systemLogSessionRef.current = null
        systemLogPersistQueueRef.current = []
        if (typeof window !== 'undefined' && systemLogFlushTimerRef.current) {
            window.clearTimeout(systemLogFlushTimerRef.current)
            systemLogFlushTimerRef.current = null
        }
    }

    async function loadAuthenticatedUser(nextToken) {
        if (!nextToken) {
            setAuthUser(null)
            return null
        }

        const response = await fetch(buildApiUrl('/auth/me'), {
            headers: {
                Authorization: `Bearer ${nextToken}`,
            },
        })

        if (!response.ok) {
            setAuthUser(null)
            return null
        }

        const data = await readJsonResponse(response)
        setAuthUser(data.user || null)
        return data.user || null
    }

    async function handleLogin(credentials) {
        setIsAuthSubmitting(true)
        setAuthError('')
        try {
            const response = await fetch(buildApiUrl('/auth/login'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(credentials),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Could not sign in.'))
            }

            const nextToken = data.session?.token || ''
            prepareWorkspaceIdentitySwitch()
            setIsGuestNoticeDismissed(false)
            setAuthToken(nextToken)
            persistStoredAuthToken(nextToken)
            setAuthUser(data.user || null)
            setIsAuthManagerOpen(false)
            appendSystemLog(`Signed in as ${data.user?.email || 'user'}.`)
        } catch (error) {
            setAuthError(error.message || 'Could not sign in.')
        } finally {
            setIsAuthSubmitting(false)
        }
    }

    async function handleGuestLogin() {
        setIsAuthSubmitting(true)
        setAuthError('')
        try {
            const response = await fetch(buildApiUrl('/auth/guest'), {
                method: 'POST',
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Could not start guest demo.'))
            }

            const nextToken = data.session?.token || ''
            prepareWorkspaceIdentitySwitch()
            setIsGuestNoticeDismissed(false)
            setAuthToken(nextToken)
            persistStoredAuthToken(nextToken)
            setAuthUser(data.user || null)
            setIsAuthManagerOpen(false)
            appendSystemLog('Signed in as guest demo. Heavy runtime actions are disabled.')
        } catch (error) {
            setAuthError(error.message || 'Could not start guest demo.')
        } finally {
            setIsAuthSubmitting(false)
        }
    }

    async function handleRegister(credentials) {
        setIsAuthSubmitting(true)
        setAuthError('')
        try {
            const response = await fetch(buildApiUrl('/auth/register'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(credentials),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Could not create account.'))
            }

            const nextToken = data.session?.token || ''
            prepareWorkspaceIdentitySwitch()
            setAuthToken(nextToken)
            persistStoredAuthToken(nextToken)
            setAuthUser(data.user || null)
            setIsAuthManagerOpen(false)
            appendSystemLog(`Created account ${data.user?.email || ''} and enabled cloud workspace sync.`)
        } catch (error) {
            setAuthError(error.message || 'Could not create account.')
        } finally {
            setIsAuthSubmitting(false)
        }
    }

    async function handleLogout() {
        try {
            await fetch(buildApiUrl('/auth/logout'), {
                method: 'POST',
                headers: buildAuthHeaders(),
            })
        } catch {
            // ignore logout transport errors; local session should still be cleared
        }

        prepareWorkspaceIdentitySwitch()
        setAuthToken('')
        setAuthUser(null)
        setAuthError('')
        clearStoredAuthToken()
        setIsAuthManagerOpen(false)
        appendSystemLog('Signed out. Returned to local workspace mode.')
    }

    useEffect(() => {
        setSymbolInputValue(chartSettings.symbol)
    }, [chartSettings.symbol])

    useEffect(() => {
        if (!currentWorkspaceSaveId) {
            return
        }

        if (!hasWorkspaceSavesHydrated) {
            return
        }

        if (!workspaceSaves.some((save) => String(save.id) === String(currentWorkspaceSaveId))) {
            updateCurrentWorkspaceSave('')
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [workspaceSaves, currentWorkspaceSaveId, hasWorkspaceSavesHydrated])

    useEffect(() => {
        const currentWorkspaceSave = workspaceSaves.find(
            (save) => String(save.id) === String(currentWorkspaceSaveId)
        )

        if (!currentWorkspaceSave) {
            return
        }

        const snapshotName = String(currentWorkspaceSaveSnapshot?.name || '').trim()
        const currentName = String(currentWorkspaceSave.name || '').trim()
        const snapshotScore = currentWorkspaceSaveSnapshot?.score ?? null
        const currentScore = currentWorkspaceSave.score ?? null

        if (snapshotName === currentName && snapshotScore === currentScore) {
            return
        }

        updateCurrentWorkspaceSave(currentWorkspaceSave)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [workspaceSaves, currentWorkspaceSaveId, currentWorkspaceSaveSnapshot?.name, currentWorkspaceSaveSnapshot?.score])

    useEffect(() => {
        let disposed = false

        if (!isAuthenticated || !isWorkspaceReady) {
            setChartSymbolCatalog({
                symbols: [],
                rows: [],
                exhaustive: false,
                source: '',
                note: '',
            })
            return () => {
                disposed = true
            }
        }

        async function loadChartSymbolCatalog() {
            try {
                const response = await fetch(buildApiUrl('/chart/symbols'), {
                    headers: buildAuthHeaders(),
                })
                if (response.status === 404) {
                    if (!disposed) {
                        setChartSymbolCatalog({
                            symbols: [],
                            rows: [],
                            exhaustive: false,
                            source: '',
                            note: '',
                        })
                    }
                    return
                }
                const data = await readJsonResponse(response)

                if (!response.ok || data.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, 'Could not load chart symbols.'))
                }

                const normalizedSymbols = Array.isArray(data.symbols)
                    ? [...new Set(data.symbols.map((entry) => String(entry || '').trim().toUpperCase()).filter(Boolean))].sort()
                    : []
                const normalizedRows = Array.isArray(data.rows)
                    ? data.rows
                        .map((entry) => ({
                            symbol: String(entry?.symbol || '').trim().toUpperCase(),
                            sources: Array.isArray(entry?.sources)
                                ? entry.sources.map((source) => String(source || '').trim()).filter(Boolean)
                                : [],
                        }))
                        .filter((entry) => entry.symbol)
                    : normalizedSymbols.map((symbol) => ({ symbol, sources: [] }))

                if (!disposed) {
                    setChartSymbolCatalog({
                        symbols: normalizedSymbols,
                        rows: normalizedRows,
                        exhaustive: Boolean(data.exhaustive),
                        source: String(data.source || '').trim(),
                        note: String(data.note || '').trim(),
                    })
                }
            } catch {
                if (!disposed) {
                    setChartSymbolCatalog((current) => ({
                        ...(current || {}),
                        exhaustive: false,
                    }))
                }
            }
        }

        void loadChartSymbolCatalog()

        const intervalId = window.setInterval(() => {
            void loadChartSymbolCatalog()
        }, 15000)

        return () => {
            disposed = true
            window.clearInterval(intervalId)
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isAuthenticated, isWorkspaceReady, authToken, activeHeaderBrokerProfileId])

    useEffect(() => {
        if (!isAuthenticated || !isWorkspaceReady || !activeHeaderBrokerProfile || !chartSymbolCatalog?.exhaustive) {
            invalidBrokerChartCatalogAttemptRef.current = ''
            return
        }

        const currentVisualChartSettings = normalizeChartSettings(
            loadedChartSettingsRef.current || chartSettings || DEFAULT_CHART_SETTINGS,
        )
        const currentSymbol = String(currentVisualChartSettings.symbol || '').trim().toUpperCase()
        if (!currentSymbol) {
            invalidBrokerChartCatalogAttemptRef.current = ''
            return
        }

        const catalogSymbols = Array.isArray(chartSymbolCatalog?.symbols)
            ? chartSymbolCatalog.symbols.map((entry) => String(entry || '').trim().toUpperCase()).filter(Boolean)
            : []
        if (!catalogSymbols.length || catalogSymbols.includes(currentSymbol)) {
            invalidBrokerChartCatalogAttemptRef.current = ''
            return
        }

        const { chartSettings: fallbackChartSettings } = resolveBrokerCompatibleChartSettings(
            activeHeaderBrokerProfile,
            currentVisualChartSettings,
            chartSymbolCatalog,
        )
        const fallbackSymbol = String(fallbackChartSettings?.symbol || '').trim().toUpperCase()
        if (!fallbackSymbol || fallbackSymbol === currentSymbol) {
            return
        }

        const attemptKey = [
            activeHeaderBrokerProfile.id,
            currentSymbol,
            fallbackSymbol,
            fallbackChartSettings.timeframe,
        ].join('|')
        if (invalidBrokerChartCatalogAttemptRef.current === attemptKey) {
            return
        }

        invalidBrokerChartCatalogAttemptRef.current = attemptKey
        void (async () => {
            const didApply = await syncChartSettings(fallbackChartSettings)
            if (!didApply) {
                invalidBrokerChartCatalogAttemptRef.current = ''
                return
            }

            invalidBrokerChartCatalogAttemptRef.current = ''
            appendSystemLog(
                `Chart symbol "${currentSymbol}" is not available for ${activeHeaderBrokerProfile.label}. Switched chart to ${fallbackChartSettings.symbol} ${fallbackChartSettings.timeframe}.`,
                'warn',
            )
        })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        activeHeaderBrokerProfile,
        chartSettings,
        chartSymbolCatalog,
        isAuthenticated,
        isWorkspaceReady,
        loadedChartSettings,
    ])

    useEffect(() => {
        let cancelled = false

        async function loadWorkspaceFromBackend() {
            const loadStartedAt = Date.now()
            if (!authToken) {
                setAuthUser(null)
                setIsWorkspaceReady(false)
                setWorkspaceSaves([])
                setWorkspaceLastSavedAt(null)
                setWorkspaceSocketStatus('disconnected')
                setWorkspaceSyncStatus('saved')
                setWorkspaceSyncLabel('Saved')
                setSystemLogEntries([])
                setSystemLogSession(null)
                setSystemLogSessions([])
                setIsSystemLogLoading(false)
                systemLogSessionRef.current = null
                systemLogPersistQueueRef.current = []
                updateCurrentWorkspaceSave(getStoredCurrentWorkspaceSaveSnapshot(DEFAULT_WORKSPACE_USER_ID, DEFAULT_WORKSPACE_ID))
                return
            }

            setIsWorkspaceReady(false)
            setWorkspaceSocketStatus('connecting')
            try {
                let resolvedAuthToken = authToken
                let isAuthenticated = false
                let authenticatedUser = null

                if (authToken) {
                    authenticatedUser = await loadAuthenticatedUser(authToken)
                    isAuthenticated = Boolean(authenticatedUser)

                    if (!authenticatedUser) {
                        resolvedAuthToken = ''
                        setAuthToken('')
                        clearStoredAuthToken()
                    }
                } else {
                    setAuthUser(null)
                }

                const query = `workspace_id=${DEFAULT_WORKSPACE_ID}`
                const response = await fetch(
                    buildApiUrl(`/workspace/state?${query}`),
                    {
                        headers: resolvedAuthToken
                            ? {
                                Authorization: `Bearer ${resolvedAuthToken}`,
                            }
                            : {},
                    }
                )
                const data = await readJsonResponse(response)

                if (!response.ok || data.status !== 'ok') {
                    throw new Error(data.error || 'Failed to load workspace state.')
                }

                if (cancelled) {
                    return
                }

                const isGuestSession = Boolean(authenticatedUser?.is_guest)

                if (isGuestSession) {
                    updateCurrentWorkspaceSave(null, authenticatedUser)
                    setWorkspaceSaves([])
                    setHasWorkspaceSavesHydrated(true)
                    setWorkspaceLastSavedAt(null)
                    setWorkspaceSyncStatus('saved')
                    setWorkspaceSyncLabel('Temporary')
                } else {
                    updateCurrentWorkspaceSave(
                        getStoredCurrentWorkspaceSaveSnapshot(
                            getActiveWorkspaceUserId(isAuthenticated ? authenticatedUser : null),
                            DEFAULT_WORKSPACE_ID,
                        ),
                        authenticatedUser,
                    )
                }

                workspaceRevisionRef.current = Number(data.revision || 0)
                const loadDurationSeconds = ((Date.now() - loadStartedAt) / 1000).toFixed(2)

                if (data.state && Object.keys(data.state).length > 0) {
                    const normalizedLoadedState = normalizeWorkspaceStatePayload(
                        data.state,
                        loadedChartSettingsRef.current,
                    )
                    const loadedTradeState = normalizedLoadedState.trade && typeof normalizedLoadedState.trade === 'object'
                        ? normalizedLoadedState.trade
                        : DEFAULT_TRADE
                    const primaryResolution = normalizeIncomingWorkspaceState(
                        normalizedLoadedState,
                        loadedTradeState,
                    )
                    const primaryWorkspaceState = primaryResolution.state
                    const primaryLogMessage = primaryResolution.shouldBootstrap
                        ? `Loaded workspace from backend (${loadDurationSeconds}s). ${
                            primaryResolution.reason === 'broker_switch'
                                ? 'Broker switch bootstrap applied'
                                : 'Broker-safe chart bootstrap repaired'
                        } for ${primaryResolution.targetBrokerLabel || 'selected broker'}.`
                        : `Loaded workspace from backend (${loadDurationSeconds}s).`

                    try {
                        await applyWorkspaceState(primaryWorkspaceState, {
                            revision: data.revision,
                            source: 'server-load',
                            logMessage: primaryLogMessage,
                            updatedAt: data.last_saved_at,
                            shouldApplyStrategy: !primaryResolution.shouldBootstrap,
                            forceSessionChartBars: DEFAULT_CHART_SETTINGS.bars,
                            temporary: Boolean(data.temporary || isGuestSession),
                        })
                    } catch (applyError) {
                        const fallbackSelection = getPreferredBrokerSelection(loadedTradeState)
                        const fallbackWorkspaceState = buildBrokerBootstrapWorkspaceState(
                            normalizedLoadedState,
                            fallbackSelection,
                        )
                        const fallbackChartSettings = normalizeChartSettings(fallbackWorkspaceState.chartSettings)
                        const fallbackBrokerLabel = normalizeBrokerProfileLabel(
                            fallbackWorkspaceState?.trade?.activeBrokerProfileLabel
                                || primaryResolution.targetBrokerLabel,
                        )

                        appendSystemLog(
                            `Workspace bootstrap fallback: ${applyError.message || 'chart load failed'}.`,
                            'warn',
                        )

                        await applyWorkspaceState(fallbackWorkspaceState, {
                            revision: data.revision,
                            source: 'server-load',
                            logMessage: `Loaded workspace from backend (${loadDurationSeconds}s). Bootstrap fallback applied for ${fallbackBrokerLabel || 'current broker'} using ${fallbackChartSettings.symbol} ${fallbackChartSettings.timeframe}.`,
                            updatedAt: data.last_saved_at,
                            shouldApplyStrategy: false,
                            forceSessionChartBars: DEFAULT_CHART_SETTINGS.bars,
                            temporary: Boolean(data.temporary || isGuestSession),
                        })
                    }
                } else {
                    lastPersistedWorkspaceRef.current = JSON.stringify(buildWorkspaceStateForSync())
                    setWorkspaceLastSavedAt(data.last_saved_at || null)
                    setWorkspaceSyncLabel(isGuestSession ? 'Temporary' : 'Saved')
                    appendSystemLog(`Loaded workspace from backend (${loadDurationSeconds}s).`, 'success')
                }
            } catch (error) {
                if (!cancelled) {
                    console.error('Failed to load workspace state:', error)
                    appendSystemLog(`Workspace sync unavailable: ${error.message || 'backend load failed'}.`)
                    lastPersistedWorkspaceRef.current = JSON.stringify(buildWorkspaceStateForSync())
                }
            } finally {
                if (!cancelled) {
                    setIsWorkspaceReady(true)
                }
            }
        }

        void loadWorkspaceFromBackend()

        return () => {
            cancelled = true
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authToken])

    useEffect(() => {
        let cancelled = false

        async function loadBrokerProfiles() {
            if (!isAuthenticated || !authToken) {
                setBrokerProfiles([])
                setBrokerProfilesLoadError('')
                setIsBrokerProfilesLoading(false)
                setHasBrokerProfilesHydrated(false)
                return
            }

            setIsBrokerProfilesLoading(true)
            setBrokerProfilesLoadError('')

            try {
                const nextProfiles = await fetchBrokerProfilesSnapshot(authToken)
                if (cancelled) {
                    return
                }
                setBrokerProfiles(nextProfiles)
            } catch (error) {
                if (cancelled) {
                    return
                }
                setBrokerProfilesLoadError(error?.message || 'Failed to load broker profiles.')
            } finally {
                if (!cancelled) {
                    setIsBrokerProfilesLoading(false)
                    setHasBrokerProfilesHydrated(true)
                }
            }
        }

        void loadBrokerProfiles()

        return () => {
            cancelled = true
        }
    }, [authToken, isAuthenticated])

    useEffect(() => {
        if (!hasBrokerProfilesHydrated || !brokerProfiles.length) {
            return
        }

        const currentActiveId = normalizeBrokerProfileId(tradeState?.activeBrokerProfileId)
        const currentActiveLabel = String(tradeState?.activeBrokerProfileLabel || '').trim()
        const storedSelection = getStoredActiveBrokerProfileSelection()
        const normalizedCurrentActiveLabel = normalizeBrokerProfileLabel(currentActiveLabel)
        const normalizedStoredLabel = normalizeBrokerProfileLabel(storedSelection.label)
        const preferredProfile = brokerProfiles.find((entry) => entry.id === currentActiveId)
            || brokerProfiles.find((entry) => normalizeBrokerProfileLabel(entry?.label, entry?.broker_code || entry?.brokerCode || '') === normalizedCurrentActiveLabel)
            || brokerProfiles.find((entry) => entry.id === storedSelection.id)
            || brokerProfiles.find((entry) => normalizeBrokerProfileLabel(entry?.label, entry?.broker_code || entry?.brokerCode || '') === normalizedStoredLabel)
            || brokerProfiles.find((entry) => entry.is_default)
            || brokerProfiles[0]
            || null

        if (!preferredProfile) {
            return
        }

        if (preferredProfile.id === currentActiveId && preferredProfile.label === currentActiveLabel) {
            return
        }

        setTradeState((current) => ({
            ...current,
            activeBrokerProfileId: preferredProfile.id,
            activeBrokerProfileLabel: preferredProfile.label,
        }))
    }, [
        brokerProfiles,
        hasBrokerProfilesHydrated,
        tradeState?.activeBrokerProfileId,
        tradeState?.activeBrokerProfileLabel,
    ])

    useEffect(() => {
        const activeBrokerProfileId = normalizeBrokerProfileId(tradeState?.activeBrokerProfileId)
        const activeBrokerProfileLabel = String(tradeState?.activeBrokerProfileLabel || '').trim()
        const storedSelection = getStoredActiveBrokerProfileSelection()
        const storedTransportKey = buildBrokerProfileTransportKey(storedSelection)

        if (!activeBrokerProfileId) {
            persistStoredActiveBrokerProfileSelection(null)
            setApiBaseOverride('')
            if (
                typeof window !== 'undefined'
                && storedTransportKey !== 'default'
                && brokerProfileReloadTargetRef.current !== 'default'
            ) {
                brokerProfileReloadTargetRef.current = 'default'
                window.location.reload()
            }
            return
        }

        const activeProfile = brokerProfiles.find((entry) => entry.id === activeBrokerProfileId) || null
        if (!activeProfile) {
            return
        }

        const resolvedLabel = String(activeProfile.label || activeBrokerProfileLabel).trim()
        const resolvedApiBase = resolveBrokerProfileApiBaseUrl(activeProfile)
        const desiredTransportKey = buildBrokerProfileTransportKey({
            id: activeBrokerProfileId,
            label: resolvedLabel,
            apiBaseUrl: resolvedApiBase,
        })

        persistStoredActiveBrokerProfileSelection({
            id: activeBrokerProfileId,
            label: resolvedLabel,
            apiBaseUrl: resolvedApiBase,
        })

        if (resolvedLabel && resolvedLabel !== activeBrokerProfileLabel) {
            setTradeState((current) => ({
                ...current,
                activeBrokerProfileLabel: resolvedLabel,
            }))
            return
        }

        setApiBaseOverride('')
        if (storedTransportKey === desiredTransportKey) {
            brokerProfileReloadTargetRef.current = ''
            return
        }

        if (typeof window === 'undefined' || brokerProfileReloadTargetRef.current === desiredTransportKey) {
            return
        }

        brokerProfileReloadTargetRef.current = desiredTransportKey
        window.location.reload()
    }, [brokerProfiles, tradeState?.activeBrokerProfileId, tradeState?.activeBrokerProfileLabel])

    useEffect(() => {
        return () => {
            if (typeof window !== 'undefined' && systemLogFlushTimerRef.current) {
                window.clearTimeout(systemLogFlushTimerRef.current)
                systemLogFlushTimerRef.current = null
            }
        }
    }, [])

    useEffect(() => {
        if (!isAuthenticated || !isWorkspaceReady || !authToken || isGuest) {
            if (isGuest) {
                setIsSystemLogLoading(false)
                setSystemLogEntries([])
                setSystemLogSession(null)
                setSystemLogSessions([])
                systemLogSessionRef.current = null
                systemLogPersistQueueRef.current = []
            }
            return undefined
        }

        let cancelled = false

        async function loadSystemLogFromBackend() {
            setIsSystemLogLoading(true)

            try {
                const query = new URLSearchParams({
                    workspace_id: DEFAULT_WORKSPACE_ID,
                    entry_limit: '600',
                })
                const response = await fetch(
                    buildApiUrl(`/workspace/system-log?${query.toString()}`),
                    {
                        headers: buildAuthHeaders(),
                    }
                )
                const data = await readJsonResponse(response)

                if (!response.ok || data.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, 'Could not load the persisted system log.'))
                }

                if (cancelled) {
                    return
                }

                const nextSession = normalizeSystemLogSession(data.session)
                systemLogSessionRef.current = nextSession
                setSystemLogSession(nextSession)
                setSystemLogSessions(mergeSystemLogSessions([], data.sessions || []))
                setSystemLogEntries((current) => mergeSystemLogEntries(current, data.entries || []))
            } catch (error) {
                if (!cancelled) {
                    console.error('Failed to load persisted system log:', error)
                }
            } finally {
                if (!cancelled) {
                    setIsSystemLogLoading(false)
                }
            }
        }

        void loadSystemLogFromBackend()

        return () => {
            cancelled = true
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isAuthenticated, isWorkspaceReady, authToken, isGuest])

    useEffect(() => {
        if (!isAuthenticated || !isWorkspaceReady || !authToken || isGuest) {
            return
        }

        if (!systemLogPersistQueueRef.current.length) {
            return
        }

        scheduleSystemLogFlush(150)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isAuthenticated, isWorkspaceReady, authToken, systemLogSession?.id, isGuest])

    useEffect(() => {
        if (!isWorkspaceReady || !isAuthenticated || isGuest || isMobileWindow) {
            return undefined
        }
        clearScheduledWorkspacePersist()

        workspacePersistTimerRef.current = window.setTimeout(() => {
            workspacePersistTimerRef.current = null
            queueWorkspacePersistFlush()
        }, 900)

        return () => {
            clearScheduledWorkspacePersist()
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        isAuthenticated,
        isWorkspaceReady,
        strategy,
        backtestStrategySet,
        backtest,
        tradeState,
        batchState,
        researchState,
        chartDrawings,
        visibleIndicatorColumnsSnapshot,
        loadedChartSettings,
        lastStrategyResponse,
        lastBacktestResponse,
        uiState,
        isMobileWindow,
    ])

    useEffect(() => {
        if (!isWorkspaceReady || !isAuthenticated || isGuest || isMobileWindow) {
            if (isGuest || isMobileWindow) {
                setWorkspaceSocketStatus('disconnected')
            }
            return undefined
        }

        let disposed = false

        function connectWorkspaceSocket() {
            setWorkspaceSocketStatus('connecting')
            const query = `token=${encodeURIComponent(authToken)}&workspace_id=${DEFAULT_WORKSPACE_ID}`
            const socket = new WebSocket(
                buildWebSocketUrl(`/ws/workspace?${query}`)
            )

            workspaceSocketRef.current = socket
            if (workspaceConnectTimeoutRef.current) {
                window.clearTimeout(workspaceConnectTimeoutRef.current)
            }
            workspaceConnectTimeoutRef.current = window.setTimeout(() => {
                if (socket.readyState === WebSocket.CONNECTING) {
                    setWorkspaceSocketStatus('polling')
                }
            }, 2500)

            socket.onopen = () => {
                if (workspaceConnectTimeoutRef.current) {
                    window.clearTimeout(workspaceConnectTimeoutRef.current)
                    workspaceConnectTimeoutRef.current = null
                }
                setWorkspaceSocketStatus('connected')
            }

            socket.onmessage = (event) => {
                let message = null

                try {
                    message = JSON.parse(event.data)
                } catch (error) {
                    console.error('Invalid workspace websocket payload:', error)
                    return
                }

                if (message?.type === 'workspace.snapshot') {
                    if (Number(message.revision || 0) > workspaceRevisionRef.current) {
                        const resolution = normalizeIncomingWorkspaceState(message.state)
                        void applyWorkspaceState(resolution.state, {
                            revision: message.revision,
                            source: 'workspace.snapshot',
                            updatedAt: message.updated_at,
                            logMessage: resolution.shouldBootstrap
                                ? `Project snapshot kept ${resolution.targetBrokerLabel || 'current broker'} on ${resolution.bootstrapChartSettings.symbol} ${resolution.bootstrapChartSettings.timeframe}.`
                                : '',
                        }).catch((error) => {
                            console.error('Failed to apply workspace snapshot:', error)
                        })
                    }
                    return
                }

                if (message?.type === 'workspace.updated' || message?.type === 'workspace.patch_applied') {
                    const messageSource = String(message.source || '')
                    const isCurrentSessionSource = (
                        messageSource === workspaceSessionIdRef.current
                        || messageSource.startsWith(`${workspaceSessionIdRef.current}:`)
                    )

                    if (isCurrentSessionSource) {
                        workspaceRevisionRef.current = Number(message.revision || workspaceRevisionRef.current)
                        if (message.state && typeof message.state === 'object') {
                            lastPersistedWorkspaceRef.current = JSON.stringify(
                                normalizeIncomingWorkspaceState(message.state).state
                            )
                            setWorkspaceLastSavedAt(message.updated_at || null)
                            setWorkspaceSyncStatus('saved')
                            setWorkspaceSyncLabel('Saved')
                        }
                        return
                    }

                    const restoreMatch = String(message.source || '').match(/^restore_save:(\d+)$/)
                    if (restoreMatch) {
                        setLastRestoredWorkspaceSaveId(String(restoreMatch[1]))
                    }

                    const resolution = normalizeIncomingWorkspaceState(message.state)
                    void applyWorkspaceState(resolution.state, {
                        revision: message.revision,
                        source: message.source || message.type,
                        logMessage: resolution.shouldBootstrap
                            ? `Project updated from another session. Broker-safe bootstrap kept ${resolution.targetBrokerLabel || 'current broker'} on ${resolution.bootstrapChartSettings.symbol} ${resolution.bootstrapChartSettings.timeframe}.`
                            : 'Project updated from another session.',
                        updatedAt: message.updated_at,
                    }).catch((error) => {
                        console.error('Failed to apply workspace update:', error)
                    })
                    return
                }

                if (message?.type === 'workspace.system_log_started') {
                    if (typeof window !== 'undefined') {
                        window.dispatchEvent(new CustomEvent('workspace:system-log-started', {
                            detail: message,
                        }))
                    }
                    const messageSource = String(message.source || '')
                    const isCurrentLogSource = (
                        messageSource === buildSystemLogRequestSource()
                        || messageSource.startsWith(`${buildSystemLogRequestSource()}:`)
                    )
                    const nextSession = normalizeSystemLogSession(message.session)

                    setSystemLogSessions((current) => mergeSystemLogSessions(
                        current,
                        nextSession ? [nextSession] : [],
                        message.archived_session_ids || [],
                    ))

                    if (isCurrentLogSource) {
                        systemLogSessionRef.current = nextSession
                        setSystemLogSession(nextSession)
                        return
                    }

                    systemLogSessionRef.current = nextSession
                    setSystemLogSession(nextSession)
                    setSystemLogEntries([])
                    return
                }

                if (message?.type === 'workspace.system_log_appended') {
                    if (typeof window !== 'undefined') {
                        window.dispatchEvent(new CustomEvent('workspace:system-log-appended', {
                            detail: message,
                        }))
                    }
                    const messageSource = String(message.source || '')
                    const isCurrentLogSource = (
                        messageSource === buildSystemLogRequestSource()
                        || messageSource.startsWith(`${buildSystemLogRequestSource()}:`)
                    )
                    const nextSession = normalizeSystemLogSession(message.session)

                    if (nextSession) {
                        if (
                            !systemLogSessionRef.current
                            || Number(systemLogSessionRef.current.id || 0) === Number(nextSession.id || 0)
                        ) {
                            systemLogSessionRef.current = nextSession
                        }
                        setSystemLogSession((current) => (
                            !current || Number(current.id || 0) === Number(nextSession.id || 0)
                                ? nextSession
                                : current
                        ))
                        setSystemLogSessions((current) => mergeSystemLogSessions(current, [nextSession]))
                    }

                    if (isCurrentLogSource) {
                        return
                    }

                    if (
                        nextSession
                        && systemLogSessionRef.current
                        && Number(systemLogSessionRef.current.id || 0) > 0
                        && Number(systemLogSessionRef.current.id || 0) !== Number(nextSession.id || 0)
                    ) {
                        return
                    }

                    setSystemLogEntries((current) => mergeSystemLogEntries(current, message.entries || []))
                    return
                }

                if (message?.type === 'workspace.research_job_updated') {
                    if (typeof window !== 'undefined') {
                        window.dispatchEvent(new CustomEvent('workspace:research-job-updated', {
                            detail: message?.job || null,
                        }))
                    }
                    return
                }

                if (message?.type === 'workspace.research_batch_updated') {
                    if (typeof window !== 'undefined') {
                        window.dispatchEvent(new CustomEvent('workspace:research-batch-updated', {
                            detail: message?.batch || null,
                        }))
                    }
                }
            }

            socket.onclose = () => {
                if (workspaceConnectTimeoutRef.current) {
                    window.clearTimeout(workspaceConnectTimeoutRef.current)
                    workspaceConnectTimeoutRef.current = null
                }
                setWorkspaceSocketStatus('polling')
                if (disposed) {
                    return
                }

                workspaceReconnectTimerRef.current = window.setTimeout(() => {
                    connectWorkspaceSocket()
                }, 1000)
            }

            socket.onerror = () => {
                setWorkspaceSocketStatus('polling')
            }
        }

        connectWorkspaceSocket()

        return () => {
            disposed = true

            if (workspaceReconnectTimerRef.current) {
                window.clearTimeout(workspaceReconnectTimerRef.current)
                workspaceReconnectTimerRef.current = null
            }

            if (workspaceConnectTimeoutRef.current) {
                window.clearTimeout(workspaceConnectTimeoutRef.current)
                workspaceConnectTimeoutRef.current = null
            }

            if (workspaceSocketRef.current) {
                workspaceSocketRef.current.close()
                workspaceSocketRef.current = null
            }
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isWorkspaceReady, authToken, isAuthenticated, isGuest, isMobileWindow])

    useEffect(() => {
        if (!isWorkspaceReady || !isAuthenticated) {
            return undefined
        }

        let disposed = false

        function connectStrategySocket() {
            const socket = new WebSocket(
                buildWebSocketUrl(`/ws/strategy?source=${workspaceSessionIdRef.current}&token=${encodeURIComponent(authToken)}`)
            )

            strategySocketRef.current = socket

            socket.onmessage = (event) => {
                let message = null

                try {
                    message = JSON.parse(event.data)
                } catch (error) {
                    console.error('Invalid strategy websocket payload:', error)
                    return
                }

                if (message?.type === 'pong') {
                    return
                }

                if (
                    message?.type === 'strategy.snapshot'
                    || message?.type === 'strategy.updated'
                ) {
                    setLastStrategyResponse((current) => {
                        const currentAppliedAt = Number(current?.last_applied_at || 0)
                        const nextAppliedAt = Number(message?.last_applied_at || 0)

                        if (currentAppliedAt > nextAppliedAt && nextAppliedAt > 0) {
                            return current
                        }

                        return {
                            ...(current || {}),
                            ...message,
                        }
                    })
                    setConsoleStatusState((current) => ({
                        ...current,
                        strategyPending: false,
                        backtestBusy: false,
                        backtestPending: false,
                        resultsPending: false,
                        strategyError: message?.status === 'error' ? String(message?.error || current.strategyError || '') : current.strategyError,
                    }))
                }
            }

            socket.onclose = () => {
                if (disposed) {
                    return
                }

                strategyReconnectTimerRef.current = window.setTimeout(() => {
                    connectStrategySocket()
                }, 1000)
            }

            socket.onerror = () => {
                socket.close()
            }
        }

        connectStrategySocket()

        return () => {
            disposed = true

            if (strategyReconnectTimerRef.current) {
                window.clearTimeout(strategyReconnectTimerRef.current)
                strategyReconnectTimerRef.current = null
            }

            if (strategySocketRef.current) {
                strategySocketRef.current.close()
                strategySocketRef.current = null
            }
        }
    }, [isWorkspaceReady, authToken, isAuthenticated])

    useEffect(() => {
        if (!isAuthenticated || !isWorkspaceReady) {
            return
        }

        void refreshWorkspaceSaves().catch((error) => {
            console.error('Failed to refresh workspace saves after workspace load:', error)
        })
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isAuthenticated, isWorkspaceReady])

    useEffect(() => {
        let disposed = false

        async function loadServerHealth() {
            try {
                const response = await fetch(buildApiUrl('/health'))
                const data = await readJsonResponse(response)

                if (!disposed) {
                    setServerHealth(data)
                }
            } catch (error) {
                if (!disposed) {
                    setServerHealth((current) => ({
                        ...(current || {}),
                        status: 'error',
                        error: error.message || 'Health unavailable',
                    }))
                }
            }
        }

        void loadServerHealth()
        const intervalId = window.setInterval(() => {
            void loadServerHealth()
        }, 5000)

        return () => {
            disposed = true
            window.clearInterval(intervalId)
        }
    }, [])

    useEffect(() => {
        function handlePointerDown(event) {
            if (!statusMenuRef.current?.contains(event.target)) {
                setIsStatusMenuOpen(false)
            }
        }

        window.addEventListener('pointerdown', handlePointerDown)

        return () => {
            window.removeEventListener('pointerdown', handlePointerDown)
        }
    }, [])

    const workspaceConnectionLabel = workspaceSocketStatus === 'connected'
        ? 'Live'
        : workspaceSocketStatus === 'connecting'
            ? 'Connecting'
            : workspaceSocketStatus === 'polling'
                ? 'Polling'
                : workspaceSocketStatus === 'disconnected'
                    ? 'Reconnecting'
                : 'Socket error'

    const workspaceSyncTitle = [
        `Workspace sync: ${workspaceSyncLabel}`,
        `Connection: ${workspaceConnectionLabel}`,
        `Last saved: ${formatWorkspaceSyncTime(workspaceLastSavedAt)}`,
    ].join(' • ')
    const serverStatusLabel = serverHealth?.status === 'ok'
        ? 'Healthy'
        : serverHealth?.status === 'degraded'
            ? 'Degraded'
            : serverHealth?.status === 'error'
                ? 'Unavailable'
            : 'Checking'
    const serverStatusTone = serverHealth?.status === 'ok'
        ? 'ok'
        : serverHealth?.status === 'degraded'
            ? 'warn'
            : serverHealth?.status === 'error'
                ? 'error'
            : 'muted'
    const tradeRuntimeAlert = (() => {
        const marketFeed = liveTradeRuntime?.market_feed || {}
        const marketFeedStatus = String(marketFeed?.status || '').trim().toLowerCase()
        const tradeRuntimeStatus = String(liveTradeRuntime?.status || '').trim().toLowerCase()
        const tradeRuntimeArmed = Boolean(liveTradeRuntime?.armed)
        const runtimeErrorText = String(liveTradeRuntime?.last_error || '').trim()
        const staleSymbolSelectMatch = runtimeErrorText.match(/^SymbolSelect failed for\s+([A-Z0-9._-]+)\s+error=\d+/i)
        const staleSymbolSelectTarget = staleSymbolSelectMatch?.[1]?.trim().toUpperCase() || ''
        const knownRuntimeSymbols = new Set(
            [
                ...(Array.isArray(liveTradeRuntime?.active_symbols) ? liveTradeRuntime.active_symbols : []),
                ...Object.keys(liveTradeRuntime?.broker_symbol_rules || {}),
            ]
                .map((value) => String(value || '').trim().toUpperCase())
                .filter(Boolean)
        )

        if (marketFeedStatus === 'stale') {
            if (!tradeRuntimeArmed && tradeRuntimeStatus === 'market_feed_stale') {
                return null
            }
            return {
                tone: 'error',
                title: 'Trade market feed stale',
                detail: String(
                    marketFeed?.detail
                    || liveTradeRuntime?.last_error
                    || 'The trade runtime was sanitized because live market updates stopped arriving.'
                ),
            }
        }
        if (runtimeErrorText) {
            if (
                staleSymbolSelectTarget
                && marketFeed?.bridge_online
                && ['idle', 'healthy', 'waiting'].includes(marketFeedStatus)
                && knownRuntimeSymbols.size > 0
                && !knownRuntimeSymbols.has(staleSymbolSelectTarget)
            ) {
                return null
            }
            if (
                !tradeRuntimeArmed
                && tradeRuntimeStatus === 'market_feed_stale'
                && runtimeErrorText.toLowerCase().includes('market feed')
            ) {
                return null
            }
            return {
                tone: 'error',
                title: 'Trade runtime issue',
                detail: runtimeErrorText,
            }
        }
        return null
    })()
    const latestInvalidStop = findLatestInvalidStopEntry(liveTradeRuntime)
    const currentWorkspaceSave = workspaceSaves.find(
        (save) => String(save.id) === String(currentWorkspaceSaveId)
    ) || null
    const currentWorkspaceSaveName = currentWorkspaceSave?.name || currentWorkspaceSaveSnapshot?.name || ''
    const chartTradeMarkerMode = String(uiState?.chart?.tradeMarkerMode || 'trader').trim().toLowerCase()
    const traderChartMarkers = liveTradeRuntime?.armed
        ? buildTradeRuntimeChartMarkers(liveTradeRuntime, loadedChartSettings)
        : []
    const normalizedBacktestChartBuffer = useMemo(
        () => buildBacktestChartBufferSummary(backtestChartBuffer),
        [backtestChartBuffer]
    )
    const normalizedChartBacktestOverlay = useMemo(
        () => buildBacktestChartBufferSummary(chartBacktestOverlay),
        [chartBacktestOverlay]
    )
    const knownChartSymbols = useMemo(() => {
        const seen = new Set()
        const values = []

        const remember = (rawValue) => {
            const safeValue = String(rawValue || '').trim().toUpperCase()
            if (!safeValue || seen.has(safeValue)) {
                return
            }
            seen.add(safeValue)
            values.push(safeValue)
        }

        for (const symbol of Array.isArray(chartSymbolCatalog?.symbols) ? chartSymbolCatalog.symbols : []) {
            remember(symbol)
        }

        remember(chartSettings?.symbol)
        remember(backtest?.symbol)
        remember(normalizedBacktestChartBuffer?.runSymbol)
        remember(normalizedChartBacktestOverlay?.runSymbol)
        remember(lastBacktestResponse?.request?.symbol)
        remember(liveTradeRuntime?.market_snapshot_symbol)

        for (const symbol of Array.isArray(liveTradeRuntime?.active_symbols) ? liveTradeRuntime.active_symbols : []) {
            remember(symbol)
        }
        for (const symbol of Object.keys(liveTradeRuntime?.broker_symbol_rules || {})) {
            remember(symbol)
        }
        for (const sleeve of Array.isArray(tradeState?.sleeves) ? tradeState.sleeves : []) {
            remember(sleeve?.symbol)
        }
        for (const entry of Array.isArray(backtestStrategySet) ? backtestStrategySet : []) {
            remember(entry?.symbol)
        }

        return values.sort((left, right) => left.localeCompare(right))
    }, [
        backtest?.symbol,
        backtestStrategySet,
        chartSettings?.symbol,
        chartSymbolCatalog?.symbols,
        lastBacktestResponse?.request?.symbol,
        liveTradeRuntime?.active_symbols,
        liveTradeRuntime?.broker_symbol_rules,
        liveTradeRuntime?.market_snapshot_symbol,
        normalizedBacktestChartBuffer?.runSymbol,
        normalizedChartBacktestOverlay?.runSymbol,
        tradeState?.sleeves,
    ])
    const chartSymbolCatalogIsExhaustive = Boolean(chartSymbolCatalog?.exhaustive)
    const guestChartMarketLockTitle = 'Guest demo stays pinned to the live Forex EURUSD M5 showcase feed. Trader runtime remains paper-only.'
    const chartSymbolInputTitle = isGuest
        ? guestChartMarketLockTitle
        : (
            chartSymbolCatalogIsExhaustive
                ? 'Select a symbol from the MT5 symbol catalog.'
                : (chartSymbolCatalog?.note || 'Known MT5 symbols are suggested here. Custom symbols remain allowed.')
        )
    const chartTimeframeSelectTitle = isGuest
        ? guestChartMarketLockTitle
        : 'Select chart timeframe.'
    const normalizedChartSymbolQuery = String(symbolInputValue || '').trim().toUpperCase()
    const chartSymbolDatalistId = 'chart-symbol-suggestions'
    const filteredChartSymbolSuggestions = useMemo(() => {
        const query = normalizedChartSymbolQuery
        const pool = Array.isArray(knownChartSymbols) ? knownChartSymbols : []
        return pool
            .filter((symbol) => !query || symbol.includes(query))
            .slice(0, 12)
    }, [knownChartSymbols, normalizedChartSymbolQuery])
    const backtestRunMarketContext = useMemo(
        () => ({
            symbol: String(normalizedChartBacktestOverlay?.runSymbol || '').trim().toUpperCase(),
            timeframe: String(normalizedChartBacktestOverlay?.runTimeframe || '').trim().toUpperCase(),
        }),
        [normalizedChartBacktestOverlay]
    )
    const backtestMarkersMatchChartMarket = useMemo(
        () => doesBacktestChartBufferMatchChartMarket(normalizedChartBacktestOverlay, loadedChartSettings),
        [normalizedChartBacktestOverlay, loadedChartSettings]
    )
    const backtestMarkerCandidates = useMemo(
        () => (Array.isArray(normalizedChartBacktestOverlay?.markers) ? normalizedChartBacktestOverlay.markers : []),
        [normalizedChartBacktestOverlay]
    )
    const backtestChartMarkers = useMemo(
        () => (
            backtestMarkersMatchChartMarket
                ? backtestMarkerCandidates.filter((marker) => (
                    doesBacktestMarkerMatchChartMarket(marker, loadedChartSettings, normalizedChartBacktestOverlay)
                ))
                : []
        ),
        [backtestMarkerCandidates, backtestMarkersMatchChartMarket, loadedChartSettings, normalizedChartBacktestOverlay]
    )
    const backtestMarkerInfo = useMemo(
        () => ({
            totalCount: backtestMarkerCandidates.length,
            isCompatible: backtestMarkersMatchChartMarket,
            runSymbol: backtestRunMarketContext.symbol,
            runTimeframe: backtestRunMarketContext.timeframe,
            markers: backtestMarkerCandidates,
        }),
        [backtestMarkerCandidates, backtestMarkersMatchChartMarket, backtestRunMarketContext]
    )
    const activeChartTradeMarkers = useMemo(
        () => (
            chartTradeMarkerMode === 'backtest'
                ? backtestChartMarkers
                : chartTradeMarkerMode === 'both'
                    ? mergeChartMarkers(backtestChartMarkers, traderChartMarkers)
                    : traderChartMarkers
        ),
        [backtestChartMarkers, chartTradeMarkerMode, traderChartMarkers]
    )

    useEffect(() => {
        if (!['backtest', 'both'].includes(chartTradeMarkerMode)) {
            return
        }

        if (normalizedChartBacktestOverlay?.markers?.length) {
            return
        }

        const nextOverlay = normalizedBacktestChartBuffer
        if (!nextOverlay) {
            return
        }

        if (areBacktestChartBufferSummariesEqual(normalizedChartBacktestOverlay, nextOverlay)) {
            return
        }

        setChartBacktestOverlay(nextOverlay)

        if (isWorkspaceReady && isAuthenticated) {
            void persistWorkspacePatch(
                {
                    chartBacktestOverlay: nextOverlay,
                },
                `${workspaceSessionIdRef.current}:chart-backtest-overlay-hydrate`,
            ).catch((error) => {
                console.error('Failed to persist hydrated chart backtest overlay:', error)
                appendSystemLog(`Workspace sync failed: ${error.message || 'chart backtest overlay hydrate save error'}.`)
            })
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        chartTradeMarkerMode,
        isAuthenticated,
        isWorkspaceReady,
        normalizedBacktestChartBuffer,
        normalizedChartBacktestOverlay,
    ])

    useEffect(() => {
        let cancelled = false
        let timer = null

        async function syncTradeRuntime() {
            try {
                const response = await fetch(buildApiUrl('/trade/runtime'), {
                    headers: authToken
                        ? { Authorization: `Bearer ${authToken}` }
                        : {},
                })
                const data = await readJsonResponse(response)
                if (!response.ok) {
                    throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to load trade runtime.')}`)
                }
                if (!cancelled) {
                    const nextRuntime = extractTradeRuntimePayload(data)
                    setLiveTradeRuntime(nextRuntime)
                    const nextDelay = nextRuntime?.armed ? 800 : 3000
                    timer = window.setTimeout(() => {
                        void syncTradeRuntime()
                    }, nextDelay)
                }
            } catch {
                if (!cancelled) {
                    setLiveTradeRuntime((current) => current)
                    const nextDelay = liveTradeRuntime?.armed ? 800 : 3000
                    timer = window.setTimeout(() => {
                        void syncTradeRuntime()
                    }, nextDelay)
                }
            }
        }

        void syncTradeRuntime()

        return () => {
            cancelled = true
            if (timer) {
                window.clearTimeout(timer)
            }
        }
    }, [authToken, liveTradeRuntime?.armed])

    useEffect(() => {
        if (!latestInvalidStop?.id) {
            return
        }
        setInvalidStopOverlay((current) => {
            if (current?.id === latestInvalidStop.id) {
                return current
            }
            return {
                ...latestInvalidStop,
                expiresAt: Date.now() + INVALID_STOPS_ALERT_TTL_MS,
            }
        })
    }, [latestInvalidStop])

    useEffect(() => {
        if (!invalidStopOverlay?.expiresAt) {
            return
        }
        const remainingMs = invalidStopOverlay.expiresAt - Date.now()
        if (remainingMs <= 0) {
            setInvalidStopOverlay(null)
            return
        }
        const timer = window.setTimeout(() => {
            setInvalidStopOverlay((current) => (
                current?.id === invalidStopOverlay.id ? null : current
            ))
        }, remainingMs)
        return () => {
            window.clearTimeout(timer)
        }
    }, [invalidStopOverlay])

    function handleBrokerProfilesChanged(nextProfiles = null) {
        const safeProfiles = Array.isArray(nextProfiles)
            ? nextProfiles.map((entry, index) => normalizeBrokerProfileRecord(entry, index))
            : []
        setBrokerProfiles(safeProfiles)
        setBrokerProfilesLoadError('')
        setIsBrokerProfilesLoading(false)
        setHasBrokerProfilesHydrated(true)
    }

    async function handleHeaderBrokerProfileChange(nextProfileId, options = {}) {
        const {
            pendingChartSettings = null,
            logMessage = '',
        } = options
        const safeProfileId = normalizeBrokerProfileId(nextProfileId)
        const targetProfile = brokerProfiles.find((entry) => entry.id === safeProfileId) || null
        if (!targetProfile) {
            return false
        }

        const currentVisualChartSettings = normalizeChartSettings(
            loadedChartSettingsRef.current || chartSettings || DEFAULT_CHART_SETTINGS,
        )
        const resolvedChartAdjustment = pendingChartSettings
            ? {
                chartSettings: normalizeChartSettings(pendingChartSettings),
                reason: '',
            }
            : resolveBrokerCompatibleChartSettings(targetProfile, currentVisualChartSettings)
        const resolvedChartSettings = resolvedChartAdjustment.chartSettings

        const resolvedLabel = String(targetProfile.label || '').trim()
        if (
            targetProfile.id === activeHeaderBrokerProfileId
            && resolvedLabel === activeHeaderBrokerProfileLabel
        ) {
            if (pendingChartSettings) {
                clearPendingBrokerSwitchChartSettings()
                void syncChartSettings(resolvedChartSettings)
            }
            return true
        }

        const nextSelection = {
            id: targetProfile.id,
            label: resolvedLabel,
            apiBaseUrl: resolveBrokerProfileApiBaseUrl(targetProfile),
        }
        const nextTradeSelection = {
            ...DEFAULT_TRADE,
            ...(tradeState && typeof tradeState === 'object' ? tradeState : {}),
            activeBrokerProfileId: targetProfile.id,
            activeBrokerProfileLabel: resolvedLabel,
        }
        const currentSelection = getStoredActiveBrokerProfileSelection()
        const currentTransportKey = buildBrokerProfileTransportKey(currentSelection)
        const nextTransportKey = buildBrokerProfileTransportKey(nextSelection)
        const fallbackLogMessage = resolvedChartAdjustment.reason
            ? `Broker switch requested: ${targetProfile.label}. Current chart symbol "${currentVisualChartSettings.symbol}" is not available for this broker, so the chart will reopen on ${resolvedChartSettings.symbol} ${resolvedChartSettings.timeframe}.`
            : `Broker switch requested: ${targetProfile.label}.`

        persistPendingBrokerSwitchChartSettings(resolvedChartSettings, targetProfile)

        persistStoredActiveBrokerProfileSelection(nextSelection)
        setApiBaseOverride('')

        if (
            typeof window !== 'undefined'
            && nextTransportKey !== currentTransportKey
            && brokerProfileReloadTargetRef.current !== nextTransportKey
        ) {
            appendSystemLog(logMessage || fallbackLogMessage)
            try {
                await persistBrokerSelectionWorkspaceState(
                    nextTradeSelection,
                    targetProfile.id || 'reload',
                )
            } catch (error) {
                console.error('Failed to persist broker selection before reload:', error)
                appendSystemLog(
                    `Workspace broker selection sync failed before reload: ${error.message || 'save error'}.`,
                    'warn',
                )
            }
            brokerProfileReloadTargetRef.current = nextTransportKey
            window.location.reload()
            return true
        }

        setTradeState((current) => ({
            ...current,
            activeBrokerProfileId: targetProfile.id,
            activeBrokerProfileLabel: targetProfile.label,
        }))
        void persistBrokerSelectionWorkspaceState(
            nextTradeSelection,
            targetProfile.id || 'local',
        ).catch((error) => {
            console.error('Failed to persist broker selection:', error)
            appendSystemLog(
                `Workspace broker selection sync failed: ${error.message || 'save error'}.`,
                'warn',
            )
        })

        if (pendingChartSettings) {
            clearPendingBrokerSwitchChartSettings()
            void syncChartSettings(resolvedChartSettings)
        }

        appendSystemLog(logMessage || fallbackLogMessage)
        return true
    }

    const activeBacktestBrokerProfileContext = useMemo(() => (
        activeHeaderBrokerProfile
            ? {
                id: activeHeaderBrokerProfile.id,
                label: activeHeaderBrokerProfile.label,
                broker_code: activeHeaderBrokerProfile.broker_code,
                market_domain: activeHeaderBrokerProfile.market_domain,
                profile: activeHeaderBrokerProfile.profile,
            }
            : null
    ), [activeHeaderBrokerProfile])

    useEffect(() => {
        if (!isAuthenticated || !isWorkspaceReady || !activeHeaderBrokerProfile) {
            return
        }

        const pendingChart = readPendingBrokerSwitchChartSettings()
        if (!pendingChart) {
            pendingBrokerSwitchChartAttemptRef.current = ''
            return
        }

        const matchesActiveProfile = (
            pendingChart.targetBrokerProfileId
                ? pendingChart.targetBrokerProfileId === activeHeaderBrokerProfile.id
                : (
                    pendingChart.targetMarketDomain
                    && pendingChart.targetMarketDomain === resolveBrokerProfileMarketDomain(activeHeaderBrokerProfile)
                )
        )
        if (!matchesActiveProfile) {
            return
        }

        const pendingAttemptKey = [
            activeHeaderBrokerProfile.id,
            pendingChart.chartSettings.symbol,
            pendingChart.chartSettings.timeframe,
        ].join('|')
        if (pendingBrokerSwitchChartAttemptRef.current === pendingAttemptKey) {
            return
        }

        if (areChartSettingsEqual(pendingChart.chartSettings, loadedChartSettingsRef.current)) {
            clearPendingBrokerSwitchChartSettings()
            pendingBrokerSwitchChartAttemptRef.current = ''
            return
        }

        pendingBrokerSwitchChartAttemptRef.current = pendingAttemptKey
        void (async () => {
            const didApply = await syncChartSettings(pendingChart.chartSettings)
            if (!didApply) {
                return
            }
            clearPendingBrokerSwitchChartSettings()
            pendingBrokerSwitchChartAttemptRef.current = ''
            appendSystemLog(
                `Broker switch kept chart on ${pendingChart.chartSettings.symbol} ${pendingChart.chartSettings.timeframe}.`,
                'success',
            )
        })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        activeHeaderBrokerProfile,
        isAuthenticated,
        isWorkspaceReady,
    ])
    const backtestBrokerChartSettings = useMemo(() => ({
        symbol: loadedChartSettings?.symbol,
        timeframe: loadedChartSettings?.timeframe,
        bars: loadedChartSettings?.bars,
    }), [
        loadedChartSettings?.symbol,
        loadedChartSettings?.timeframe,
        loadedChartSettings?.bars,
    ])
    const headerBrokerSelectOptions = brokerProfiles.length
        ? brokerProfiles
        : (
            isGuest
                ? [{ id: 'guest-demo', label: 'Guest demo' }]
                : []
        )
    const brokerProfileSelectValue = activeHeaderBrokerProfileId
        || headerBrokerSelectOptions.find((entry) => entry.is_default)?.id
        || headerBrokerSelectOptions[0]?.id
        || ''
    const brokerProfileSelectTitle = (
        isGuest && !brokerProfiles.length
            ? 'Guest demo uses a curated temporary workspace. Broker switching is disabled for this showcase session.'
            : brokerProfilesLoadError
                || (
                    activeHeaderBrokerProfile
                        ? `Switch broker stack. Current: ${activeHeaderBrokerProfile.label} · ${describeBrokerProfileStack(activeHeaderBrokerProfile)}`
                        : activeHeaderBrokerProfileLabel
                            ? `Switch broker stack. Current: ${activeHeaderBrokerProfileLabel}`
                            : 'Switch broker stack.'
                )
    )

    useEffect(() => {
        if (!activeBacktestBrokerProfileContext) {
            return
        }

        setBacktest((current) => {
            const nextBacktest = mergeBacktestDefaults(current, backtestBrokerChartSettings, activeBacktestBrokerProfileContext)
            return JSON.stringify(current) === JSON.stringify(nextBacktest)
                ? current
                : nextBacktest
        })
    }, [
        activeBacktestBrokerProfileContext,
        backtestBrokerChartSettings,
    ])

    if (!isAuthenticated) {
        return (
            <div id='App' className='authGateApp'>
                <AuthManager
                    isOpen={true}
                    mode={authMode}
                    variant='standalone'
                    isSubmitting={isAuthSubmitting}
                    error={authError}
                    currentUser={null}
                    onClose={() => {}}
                    onLogin={handleLogin}
                    onGuestLogin={handleGuestLogin}
                    onRegister={handleRegister}
                    onLogout={handleLogout}
                />
            </div>
        )
    }

    if (isMobileWindow) {
        return (
            <MobileTraderView
                authToken={authToken}
                authUser={authUser}
                baseChartSettings={loadedChartSettings}
                tradeState={tradeState}
                liveTradeRuntime={liveTradeRuntime}
                serverHealth={serverHealth}
                onRuntimeUpdate={setLiveTradeRuntime}
                onLogEvent={appendSystemLog}
            />
        )
    }

    if (isStreamWindow) {
        return (
            <StreamView
                authToken={authToken}
                baseChartSettings={loadedChartSettings}
                liveTradeRuntime={liveTradeRuntime}
            />
        )
    }

    return (
        <div id='App' className={isConsoleMaximized ? 'consoleIsMaximized' : ''}>
            {tradeRuntimeAlert ? (
                <div className={`appRuntimeAlert is-${tradeRuntimeAlert.tone}`} role='alert'>
                    <div className='appRuntimeAlertEyebrow'>Trade alert</div>
                    <strong>{tradeRuntimeAlert.title}</strong>
                    <span>{tradeRuntimeAlert.detail}</span>
                </div>
            ) : null}
            {invalidStopOverlay ? (
                <div className='appRuntimeAlert appRuntimeAlertInvalidStops' role='alert'>
                    <button
                        type='button'
                        className='appRuntimeAlertClose'
                        onClick={() => setInvalidStopOverlay(null)}
                        aria-label='Close invalid stop alert'
                    >
                        ×
                    </button>
                    <div className='appRuntimeAlertEyebrow'>Trade alert</div>
                    <strong>Invalid stop blocked the order</strong>
                    <span>
                        {invalidStopOverlay.strategy ? `${invalidStopOverlay.strategy} · ` : ''}
                        {invalidStopOverlay.symbol ? `${invalidStopOverlay.symbol} · ` : ''}
                        {invalidStopOverlay.detail}
                    </span>
                </div>
            ) : null}
            {isStreamLaunchOverlayOpen ? (
                <div className='overlayContainer streamLaunchOverlay' role='dialog' aria-modal='true' aria-label='Stream launch settings'>
                    <div className='fog' onClick={handleCloseStreamLaunchOverlay} />
                    <div className='overlay streamLaunchWindow'>
                        <button
                            type='button'
                            className='closeOverlay'
                            onClick={handleCloseStreamLaunchOverlay}
                            aria-label='Close stream launch settings'
                        >
                            ×
                        </button>

                        <div className='streamLaunchPanel'>
                            <div className='streamLaunchTitle'>Stream launch</div>
                            <p className='streamLaunchCopy'>
                                Configure what the broadcast window should preload before the popup opens.
                            </p>

                            <label className='streamLaunchToggle'>
                                <input
                                    type='checkbox'
                                    checked={streamLaunchDraft.includeBacktest && Boolean(streamLaunchBacktestSource?.compatible)}
                                    disabled={!streamLaunchBacktestSource?.compatible}
                                    onChange={(event) => {
                                        setStreamLaunchDraft((current) => ({
                                            ...current,
                                            includeBacktest: event.target.checked,
                                        }))
                                        setStreamLaunchError('')
                                    }}
                                />
                                <div>
                                    <strong>Include backtest</strong>
                                    <span>Replay only the backtest that is already loaded in the main console when it matches the current stream setup.</span>
                                </div>
                            </label>

                            {streamLaunchBacktestSource?.loaded ? (
                                <div className='streamLaunchBacktestMeta'>
                                    <strong>
                                        {streamLaunchBacktestSource.symbol || 'Backtest'} {streamLaunchBacktestSource.timeframe || ''}
                                    </strong>
                                    <span>
                                        {streamLaunchBacktestSource.resultCount.toLocaleString()} results · {streamLaunchBacktestSource.markerCount.toLocaleString()} markers · loaded in main console
                                    </span>
                                </div>
                            ) : (
                                <div className='streamLaunchBacktestWarning'>
                                    {streamLaunchBacktestSource?.reason || 'No completed backtest is currently loaded in the main console. The stream launcher will not run a new backtest for you.'}
                                </div>
                            )}

                            {streamLaunchBacktestSource?.loaded && !streamLaunchBacktestSource?.compatible ? (
                                <div className='streamLaunchBacktestWarning'>
                                    {streamLaunchBacktestSource.reason}
                                </div>
                            ) : null}

                            <label className='streamLaunchField'>
                                <span>Replay candles</span>
                                <input
                                    type='number'
                                    min='1'
                                    step='1'
                                    inputMode='numeric'
                                    value={streamLaunchDraft.replayCandleCount}
                                    disabled={!streamLaunchDraft.includeBacktest || !streamLaunchBacktestSource?.compatible}
                                    placeholder={streamLaunchBacktestSource?.resultCount ? String(streamLaunchBacktestSource.resultCount) : ''}
                                    onChange={(event) => {
                                        setStreamLaunchDraft((current) => ({
                                            ...current,
                                            replayCandleCount: event.target.value,
                                        }))
                                        setStreamLaunchError('')
                                    }}
                                />
                                <small>Leave empty to replay the full backtest history. Fill it to preload only the last N candles of the backtest.</small>
                            </label>

                            <label className='streamLaunchField'>
                                <span>Operating capital ({streamLaunchCapitalPreview.resultUnit})</span>
                                <input
                                    type='number'
                                    min='0.01'
                                    step='0.01'
                                    inputMode='decimal'
                                    value={streamLaunchDraft.initialCapital}
                                    onChange={(event) => {
                                        setStreamLaunchDraft((current) => ({
                                            ...current,
                                            initialCapital: event.target.value,
                                        }))
                                        setStreamLaunchError('')
                                    }}
                                />
                                <small>Used to convert the session into equity and percentage terms. The default suggestion is 100 {streamLaunchCapitalPreview.resultUnit}.</small>
                            </label>

                            <div className='streamLaunchChoiceGroup' role='radiogroup' aria-label='Volume mode'>
                                <strong>Volume mode</strong>
                                <label className='streamLaunchChoice'>
                                    <input
                                        type='radio'
                                        name='streamVolumeMode'
                                        value='relative_capital'
                                        checked={normalizeStreamVolumeMode(streamLaunchDraft.volumeMode) === 'relative_capital'}
                                        onChange={(event) => {
                                            setStreamLaunchDraft((current) => ({
                                                ...current,
                                                volumeMode: event.target.value,
                                            }))
                                            setStreamLaunchError('')
                                        }}
                                    />
                                    <div>
                                        <span>Maximum relative to the initial capital</span>
                                        <small>
                                            {streamLaunchCapitalPreview.relativeVolume > 0
                                                ? `${streamLaunchCapitalPreview.relativeVolume.toFixed(2)} lot from ${streamLaunchCapitalPreview.initialCapital.toFixed(2)} ${streamLaunchCapitalPreview.resultUnit}`
                                                : `Below the minimum live lot with ${streamLaunchCapitalPreview.initialCapital.toFixed(2)} ${streamLaunchCapitalPreview.resultUnit}`}
                                        </small>
                                    </div>
                                </label>

                                <label className='streamLaunchChoice'>
                                    <input
                                        type='radio'
                                        name='streamVolumeMode'
                                        value='minimum_operation'
                                        checked={normalizeStreamVolumeMode(streamLaunchDraft.volumeMode) === 'minimum_operation'}
                                        onChange={(event) => {
                                            setStreamLaunchDraft((current) => ({
                                                ...current,
                                                volumeMode: event.target.value,
                                            }))
                                            setStreamLaunchError('')
                                        }}
                                    />
                                    <div>
                                        <span>Minimum operation volume</span>
                                        <small>{streamLaunchCapitalPreview.minimumOperationVolume.toFixed(2)} lot</small>
                                    </div>
                                </label>
                            </div>

                            <div className='streamLaunchBacktestMeta'>
                                <strong>
                                    Operating volume: {streamLaunchCapitalPreview.selectedVolume.toFixed(2)} lot
                                </strong>
                                <span>
                                    {streamLaunchCapitalPreview.usesBacktestReference
                                        ? `Reference ${streamLaunchCapitalPreview.referenceVolume.toFixed(2)} lot on ${streamLaunchCapitalPreview.referenceCapital.toFixed(2)} ${streamLaunchCapitalPreview.resultUnit}`
                                        : `No compatible backtest reference is loaded, so the preview falls back to the runtime minimum volume.`}
                                </span>
                            </div>

                            {streamLaunchError ? (
                                <div className='streamLaunchBacktestWarning'>{streamLaunchError}</div>
                            ) : null}

                            <div className='overlayActions streamLaunchActions'>
                                <button type='button' onClick={handleCloseStreamLaunchOverlay}>
                                    Cancel
                                </button>
                                <button type='button' className='streamLaunchPrimary' onClick={handleConfirmStreamWindowLaunch}>
                                    Open stream
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            ) : null}
            <section id='Header'>
                <div className='chartHeaderControls'>
                    <div className='chartHeaderMainControls'>
                        <label className='chartHeaderField chartHeaderFieldSymbol'>
                            <span>Symbol</span>
                            <div className='chartSymbolAutocomplete'>
                                <input
                                    type='text'
                                    list={chartSymbolDatalistId}
                                    value={symbolInputValue}
                                    disabled={isGuest}
                                    onChange={(event) => {
                                        handleSymbolInputChange(event.target.value)
                                    }}
                                    onBlur={commitSymbolInput}
                                    autoComplete='off'
                                    spellCheck={false}
                                    title={chartSymbolInputTitle}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Enter') {
                                            event.currentTarget.blur()
                                        } else if (event.key === 'Escape') {
                                            event.currentTarget.blur()
                                        }
                                    }}
                                />
                                <datalist id={chartSymbolDatalistId}>
                                    {filteredChartSymbolSuggestions.map((symbol) => (
                                        <option key={symbol} value={symbol} />
                                    ))}
                                </datalist>
                            </div>
                        </label>

                        <label className='chartHeaderField chartHeaderFieldTimeframe'>
                            <span>Timeframe</span>
                            <select
                                value={chartSettings.timeframe}
                                disabled={isGuest}
                                title={chartTimeframeSelectTitle}
                                onChange={(event) => handleHeaderFieldChange('timeframe', event.target.value)}
                            >
                                {TIMEFRAME_OPTIONS.map(([value, label]) => (
                                    <option key={value} value={value}>
                                        {label}
                                    </option>
                                ))}
                            </select>
                        </label>

                        <label className='chartHeaderField chartHeaderFieldBroker'>
                            <span>Broker</span>
                            <select
                                value={brokerProfileSelectValue}
                                onChange={(event) => {
                                    void handleHeaderBrokerProfileChange(event.target.value)
                                }}
                                disabled={!brokerProfiles.length || isBrokerProfilesLoading}
                                title={brokerProfileSelectTitle}
                            >
                                {!headerBrokerSelectOptions.length ? (
                                    <option value=''>
                                        {isBrokerProfilesLoading
                                            ? 'Loading...'
                                            : brokerProfilesLoadError
                                                ? 'Unavailable'
                                                : 'No profiles'}
                                    </option>
                                ) : headerBrokerSelectOptions.map((entry) => (
                                    <option key={entry.id} value={entry.id}>
                                        {String(entry?.label || '').trim() || buildBrokerProfileHeaderOptionLabel(entry)}
                                    </option>
                                ))}
                            </select>
                        </label>

                        <button
                            type='button'
                            className='headerIconButton headerResetButton'
                            onClick={() => void handleResetChart()}
                            aria-label='Reset chart'
                            title='Reset chart visuals'
                        >
                            <svg viewBox='0 0 24 24' aria-hidden='true'>
                                <path d='M4 12a8 8 0 1 0 2.35-5.65' />
                                <path d='M4 4v5h5' />
                            </svg>
                        </button>

                        <button
                            type='button'
                            className='indicatorManagerTrigger'
                            onClick={() => setIsIndicatorManagerOpen(true)}
                            aria-label='Open feature manager'
                            title='Open feature manager'
                        >
                            <svg viewBox='0 0 24 24' aria-hidden='true'>
                                <path d='M4 7.5h6.5l2 3h7.5' />
                                <path d='M4 12h4l2 3h10' />
                                <path d='M4 16.5h8l2-3h6' />
                            </svg>
                        </button>

                        <button
                            type='button'
                            className='headerToggleButton'
                            onClick={handleOpenStreamWindow}
                            aria-label='Open stream view in a popup window'
                            title='Open stream view in a popup window'
                        >
                            Stream
                        </button>

                        <div className='chartHeaderField chartHeaderFieldPrecision'>
                            <div className='precisionControls'>
                                <button
                                    type='button'
                                    className='precisionButton'
                                    onClick={() => handlePrecisionStep(-1)}
                                    aria-label='Decrease precision'
                                    title='Decrease precision'
                                >
                                    <span className='precisionIcon precisionIconDecrease' aria-hidden='true'>
                                        .00
                                    </span>
                                </button>

                                <button
                                    type='button'
                                    className='precisionButton'
                                    onClick={() => handlePrecisionStep(1)}
                                    aria-label='Increase precision'
                                    title='Increase precision'
                                >
                                    <span className='precisionIcon precisionIconIncrease' aria-hidden='true'>
                                        .00
                                    </span>
                                </button>
                            </div>
                        </div>

                        <button
                            type='button'
                            className={`headerIconButton ${uiState.chart.scrollChartToEndOnTickIncoming ? 'active' : ''}`}
                            onClick={handleToggleScrollChartToEndOnTickIncoming}
                            aria-pressed={uiState.chart.scrollChartToEndOnTickIncoming}
                            aria-label='Scroll chart to the end on tick incoming'
                            title='Scroll chart to the end on tick incoming'
                        >
                            <svg viewBox='0 0 24 24' aria-hidden='true'>
                                <path d='M4 17V8' />
                                <path d='M8 15V6' />
                                <path d='M12 18V9' />
                                <path d='M16 7v10' />
                                <path d='M19 6v12' />
                                <path d='m14 12 4 0' />
                                <path d='m16.5 9.5 2.5 2.5-2.5 2.5' />
                            </svg>
                        </button>

                        <div className='headerVolumeControls'>
                            <button
                                type='button'
                                className={`headerIconButton ${uiState.chart.showVolumePanel ? 'active' : ''}`}
                                onClick={handleToggleShowVolumePanel}
                                aria-pressed={uiState.chart.showVolumePanel}
                                aria-label='Show volume panel'
                                title='Show volume panel'
                            >
                                <svg viewBox='0 0 24 24' aria-hidden='true'>
                                    <path d='M5 18V12' />
                                    <path d='M10 18V8' />
                                    <path d='M15 18V5' />
                                    <path d='M20 18V10' />
                                </svg>
                            </button>

                            <select
                                className='headerVolumeModeSelect'
                                value={uiState.chart.volumeMode || 'volume'}
                                onChange={(event) => handleVolumeModeChange(event.target.value)}
                                title='Volume source'
                                aria-label='Volume source'
                            >
                                <option value='volume'>volume</option>
                                <option value='tick_volume'>tick_volume</option>
                                <option value='real_volume'>real_volume</option>
                            </select>
                        </div>

                        <div className='headerDrawingTools'>
                            <button
                                type='button'
                                className={`headerIconButton ${drawingUiState.isActive && drawingUiState.tool === 'segment' ? 'active' : ''}`}
                                onClick={() => handleDrawingToolSelect('segment')}
                                aria-label='Draw segment line'
                                title='Draw segment line'
                            >
                                <svg viewBox='0 0 24 24' aria-hidden='true'>
                                    <circle cx='6' cy='17' r='3.6' fill='currentColor' stroke='none' />
                                    <circle cx='18' cy='7' r='3.6' fill='currentColor' stroke='none' />
                                    <path d='M7.5 15.5 16.5 8.5' />
                                </svg>
                            </button>

                            <button
                                type='button'
                                className={`headerIconButton ${drawingUiState.isActive && drawingUiState.tool === 'ray' ? 'active' : ''}`}
                                onClick={() => handleDrawingToolSelect('ray')}
                                aria-label='Draw ray line'
                                title='Draw ray line'
                            >
                                <svg viewBox='0 0 24 24' aria-hidden='true'>
                                    <circle cx='6' cy='17' r='3.6' fill='currentColor' stroke='none' />
                                    <path d='M7.5 15.5 20 5.5' />
                                </svg>
                            </button>

                            <button
                                type='button'
                                className={`headerIconButton ${drawingUiState.isActive && drawingUiState.tool === 'horizontal' ? 'active' : ''}`}
                                onClick={() => handleDrawingToolSelect('horizontal')}
                                aria-label='Draw horizontal line'
                                title='Draw horizontal line'
                            >
                                <svg viewBox='0 0 24 24' aria-hidden='true'>
                                    <path d='M4 12h16' />
                                </svg>
                            </button>

                            <button
                                type='button'
                                className={`headerIconButton ${drawingUiState.isActive && drawingUiState.tool === 'vertical' ? 'active' : ''}`}
                                onClick={() => handleDrawingToolSelect('vertical')}
                                aria-label='Draw vertical line'
                                title='Draw vertical line'
                            >
                                <svg viewBox='0 0 24 24' aria-hidden='true'>
                                    <path d='M12 4v16' />
                                </svg>
                            </button>

                            <button
                                type='button'
                                className='headerIconButton'
                                onClick={handleClearAllDrawings}
                                aria-label='Delete all lines'
                                title='Delete all lines'
                            >
                                <svg viewBox='0 0 24 24' aria-hidden='true'>
                                    <path d='M5 7h14' />
                                    <path d='M9 7V5h6v2' />
                                    <path d='M8 7l1 12h6l1-12' />
                                    <path d='M10 10v6' />
                                    <path d='M14 10v6' />
                                </svg>
                            </button>
                        </div>
                    </div>

                    <div className='headerStatusArea'>
                        <div
                            ref={statusMenuRef}
                            className={`workspaceSyncShell is-${workspaceSyncStatus}`}
                        >
                            <button
                                type='button'
                                className={`workspaceSyncBadge is-${workspaceSyncStatus}`}
                                title={workspaceSyncTitle}
                                onClick={() => setIsStatusMenuOpen((current) => !current)}
                                aria-haspopup='dialog'
                                aria-expanded={isStatusMenuOpen}
                            >
                                <span className='workspaceSyncDot' aria-hidden='true' />
                            </button>

                            {isStatusMenuOpen && (
                                <div className='workspaceStatusMenu' role='dialog' aria-label='Server and sync status'>
                                    <div className='workspaceStatusSection'>
                                        <div className='workspaceStatusSectionTitle'>Client</div>
                                        {isGuest ? (
                                            <div className='workspaceStatusRow'>
                                                <span>Access</span>
                                                <strong className='isWarn'>Guest demo</strong>
                                            </div>
                                        ) : null}
                                        <div className='workspaceStatusRow'>
                                            <span>Summary</span>
                                            <strong>
                                                {workspaceSyncLabel} · {workspaceConnectionLabel} · {formatWorkspaceSyncTime(workspaceLastSavedAt)}
                                            </strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Connection</span>
                                            <strong className={workspaceSocketStatus === 'connected' ? 'isOk' : workspaceSocketStatus === 'polling' ? 'isWarn' : 'isMuted'}>
                                                {workspaceConnectionLabel}
                                            </strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Workspace sync</span>
                                            <strong className={workspaceSyncStatus === 'saved' ? 'isOk' : workspaceSyncStatus === 'syncing' ? 'isWarn' : 'isError'}>
                                                {workspaceSyncLabel}
                                            </strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Last saved</span>
                                            <strong>{formatWorkspaceSyncTime(workspaceLastSavedAt)}</strong>
                                        </div>
                                    </div>

                                    <div className='workspaceStatusSection'>
                                        <div className='workspaceStatusSectionTitle'>Server</div>
                                        <div className='workspaceStatusRow'>
                                            <span>Health</span>
                                            <strong className={serverStatusTone === 'ok' ? 'isOk' : serverStatusTone === 'warn' ? 'isWarn' : serverStatusTone === 'error' ? 'isError' : 'isMuted'}>
                                                {serverStatusLabel}
                                            </strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Uptime</span>
                                            <strong>{serverHealth?.service?.uptime_seconds ? `${Math.round(serverHealth.service.uptime_seconds)}s` : '--'}</strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Bridge ready</span>
                                            <strong className={serverHealth?.checks?.bridge?.history_ready ? 'isOk' : 'isWarn'}>
                                                {serverHealth?.checks?.bridge?.history_ready ? 'Yes' : 'No'}
                                            </strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Bridge EA online</span>
                                            <strong className={serverHealth?.checks?.bridge?.ea_online ? 'isOk' : serverHealth?.checks?.bridge?.ea_stale ? 'isWarn' : 'isMuted'}>
                                                {serverHealth?.checks?.bridge?.ea_online ? 'Yes' : serverHealth?.checks?.bridge?.ea_stale ? 'Stale' : 'No'}
                                            </strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Bridge heartbeat</span>
                                            <strong>{formatServerTimestamp(serverHealth?.checks?.bridge?.ea_last_heartbeat_at)}</strong>
                                        </div>
                                        <div className='workspaceStatusRow isMultiline'>
                                            <span>Bridge last error</span>
                                            <strong>{serverHealth?.checks?.bridge?.ea_last_error || '--'}</strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Chart warmed</span>
                                            <strong className={serverHealth?.checks?.chart?.runtime_warmed ? 'isOk' : 'isWarn'}>
                                                {serverHealth?.checks?.chart?.runtime_warmed ? 'Yes' : 'No'}
                                            </strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Strategy ready</span>
                                            <strong className={serverHealth?.checks?.strategy?.runtime_ready ? 'isOk' : 'isWarn'}>
                                                {serverHealth?.checks?.strategy?.runtime_ready ? 'Yes' : 'No'}
                                            </strong>
                                        </div>
                                    </div>

                                    <div className='workspaceStatusSection'>
                                        <div className='workspaceStatusSectionTitle'>Runtime</div>
                                        <div className='workspaceStatusRow'>
                                            <span>Last trigger</span>
                                            <strong>{serverHealth?.runtime_service?.last_trigger || '--'}</strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Last run</span>
                                            <strong>{formatServerTimestamp(serverHealth?.runtime_service?.last_run_at)}</strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Chart warm</span>
                                            <strong>{formatServerTimestamp(serverHealth?.runtime_service?.last_chart_warm_at)}</strong>
                                        </div>
                                        <div className='workspaceStatusRow'>
                                            <span>Strategy refresh</span>
                                            <strong>{formatServerTimestamp(serverHealth?.runtime_service?.last_strategy_refresh_at)}</strong>
                                        </div>
                                        <div className='workspaceStatusRow isMultiline'>
                                            <span>Last error</span>
                                            <strong>{serverHealth?.runtime_service?.last_error || serverHealth?.error || '--'}</strong>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>

                        <button
                            type='button'
                            className='headerAccountButton'
                            onClick={() => {
                                setAuthError('')
                                setAuthMode('login')
                                setIsAuthManagerOpen(true)
                            }}
                            aria-label='Open account panel'
                            title={authUser ? `Signed in as ${authUser.display_name || authUser.email}` : 'Account'}
                        >
                            <svg viewBox='0 0 24 24' aria-hidden='true'>
                                <path d='M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z' />
                                <path d='M4 20a8 8 0 0 1 16 0' />
                            </svg>
                            <span className='headerAccountLabel'>
                                {isGuest ? 'Guest demo' : (authUser?.email || 'Account')}
                            </span>
                        </button>
                    </div>
                </div>
            </section>

            <div className='chartSpacer chartSpacerLeft' aria-hidden='true' />

            <Chart
                key={chartViewId}
                id='Chart'
                authToken={authToken}
                chartSettings={loadedChartSettings}
                runId={chartRunId}
                isDrawLineModeActive={drawingUiState.isActive}
                drawingTool={drawingUiState.tool}
                metaFontSize={uiState.chart.metaFontSize}
                pendingLineColor={uiState.chart.pendingLineColor}
                scrollChartToEndOnTickIncoming={uiState.chart.scrollChartToEndOnTickIncoming}
                showVolumePanel={uiState.chart.showVolumePanel}
                volumeMode={uiState.chart.volumeMode}
                onMetaFontSizeChange={handleMetaFontSizeChange}
                onPendingLineColorChange={handlePendingLineColorChange}
                onRequestDisableDrawingMode={() => setDrawingUiState((current) => ({
                    ...current,
                    isActive: false,
                }))}
                initialDrawings={chartDrawings}
                initialVisibleIndicatorColumns={visibleIndicatorColumnsSnapshot}
                onDrawingsChange={setChartDrawings}
                onVisibleIndicatorColumnsChange={setVisibleIndicatorColumnsSnapshot}
                onIndicatorLineVisibilityChange={updateIndicatorLineVisibility}
                onInsertStrategyText={handleInsertStrategyText}
                onLogEvent={appendSystemLog}
                onHistoryStateChange={setChartHistoryState}
                tradeMarkers={activeChartTradeMarkers}
                backtestMarkerInfo={backtestMarkerInfo}
                tradeMarkerMode={chartTradeMarkerMode}
                onTradeMarkerModeChange={handleTradeMarkerModeChange}
                indicatorLegendLeadingControls={(
                    <button
                        type='button'
                        className='chartAppliedMetaButton'
                        onClick={() => void handleLoadBacktestIndicatorsIntoChart()}
                        disabled={!canLoadBacktestIndicatorsIntoChart}
                        aria-label='Load indicators from the current backtest'
                        title='Load indicators from the current backtest'
                    >
                        <svg viewBox='0 0 24 24' aria-hidden='true'>
                            <path d='M4 7.5h6.5l2 3h7.5' />
                            <path d='M4 12h4l2 3h10' />
                            <path d='M4 16.5h8l2-3h6' />
                            <path d='M12 4v7' />
                            <path d='m9.5 8.5 2.5 2.5 2.5-2.5' />
                        </svg>
                    </button>
                )}
                guestNoticeVisible={isGuest && !isGuestNoticeDismissed}
                onGuestNoticeClose={() => setIsGuestNoticeDismissed(true)}
            />

            <div className='chartSpacer chartSpacerRight' aria-hidden='true' />

            <Console
                authToken={authToken}
                brokerProfiles={brokerProfiles}
                strategy={strategy}
                setStrategy={setStrategy}
                backtestStrategySet={backtestStrategySet}
                setBacktestStrategySet={setBacktestStrategySet}
                backtest={backtest}
                setBacktest={setBacktest}
                tradeState={tradeState}
                setTradeState={setTradeState}
                liveTradeRuntime={liveTradeRuntime}
                setLiveTradeRuntime={setLiveTradeRuntime}
                appliedChartSettings={loadedChartSettings}
                currentWorkspaceSaveName={currentWorkspaceSaveName}
                onLoadStrategyIndicators={handleLoadStrategyIndicatorsIntoChart}
                onLoadBacktestFlags={handleLoadBacktestFlagsIntoChart}
                onBacktestExecuted={handleBacktestExecuted}
                onHydrateBacktestResult={handleBacktestExecuted}
                consoleStatusState={consoleStatusState}
                onStrategyStatusChange={handleStrategyStatusChange}
                onBacktestStatusChange={handleBacktestStatusChange}
                onBacktestRunStarted={handleBacktestRunStarted}
                onNeuralStatusChange={handleNeuralStatusChange}
                lastBacktestResponse={lastBacktestResponse}
                hasBacktestChartBuffer={Boolean(normalizedBacktestChartBuffer?.markers?.length)}
                batchState={batchState}
                setBatchState={setBatchState}
                researchState={researchState}
                setResearchState={setResearchState}
                onLogEvent={appendSystemLog}
                hasStoredResultsCharts={hasStoredResultsCharts}
                onLoadStoredResultsCharts={handleLoadStoredResultsCharts}
                onResolveLoadedBacktestResponse={handleResolveLoadedBacktestResponse}
                onActiveStrategyFieldChange={setActiveStrategyFieldId}
                strategyInsertRequest={strategyInsertRequest}
                systemLogHeight={systemLogHeight}
                loadedChartCandles={chartHistoryState.loadedCandles}
                sharedConsoleJobs={uiState?.consoleJobs}
                onSharedConsoleJobChange={handleSharedConsoleJobChange}
                isWorkspaceReady={isWorkspaceReady}
                workspaceSocketStatus={workspaceSocketStatus}
                onMaximizedChange={setIsConsoleMaximized}
                onBrokerProfilesChanged={handleBrokerProfilesChanged}
                currentUser={authUser}
                activeBrokerProfile={activeHeaderBrokerProfile}
            />

            <SystemLog
                entries={systemLogEntries}
                activeSession={systemLogSession}
                isLoading={isSystemLogLoading}
                onHeightChange={setSystemLogHeight}
                onStartNewLog={handleStartNewSystemLog}
            />

            <IndicatorManager
                isOpen={isIndicatorManagerOpen}
                onClose={() => setIsIndicatorManagerOpen(false)}
                chartSettings={chartSettings}
                onChange={handleSettingsChange}
                onLogEvent={appendSystemLog}
            />

            <AuthManager
                isOpen={isAuthManagerOpen}
                mode={authMode}
                isSubmitting={isAuthSubmitting}
                error={authError}
                currentUser={authUser}
                onClose={() => setIsAuthManagerOpen(false)}
                onLogin={handleLogin}
                onGuestLogin={handleGuestLogin}
                onRegister={handleRegister}
                onLogout={handleLogout}
            />
        </div>
    )
}

export default App

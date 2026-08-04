import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './Backtester.css'
import { buildApiUrl, extractApiErrorMessage, fetchWithServerRetry, readJsonResponse } from '/src/api'
import { BacktestConfigEditor } from './BacktestConfigEditor'
import { BACKTEST_DEFAULTS } from './backtestDefaults.js'
import {
    buildBacktestCostProfileValues,
    normalizeBacktestCostProfile,
    resolveBacktestAssetType,
} from './backtestCostProfiles.js'
import {
    buildBackendIndicatorsPayload,
    normalizeChartSettings,
} from '../../utils/chartSettings.jsx'
import {
    instantiateSavedPortfolioForBacktest,
    rebuildBacktestPortfoliosFromEntries,
    summarizeSavedPortfolio,
} from '../../utils/portfolioLibrary.js'
import { buildBrokerProfileQuery } from '../../utils/brokerProfiles.js'
import { resolveStrategyAliasesInStrategy } from '../../utils/strategyAliases.jsx'
import { buildStrategyCollectionChartSettings } from '../../utils/strategyLibrary.js'
import { TIMEFRAME_OPTIONS } from '../../utils/timeframes.js'

const STRATEGY_LIBRARY_FETCH_LIMIT = 500
const BACKTEST_POLL_TRANSIENT_RETRY_LIMIT = 5

function resolveContextDefaultAssetType(brokerProfile = null, rawBacktest = null, costProfile = '') {
    return resolveBacktestAssetType('', brokerProfile, rawBacktest, costProfile)
}
const BACKTEST_POLL_RETRY_DELAY_MS = 1500
const BACKTEST_POLL_RECOVERY_DELAY_MS = 5000
const BACKTEST_POLL_RECOVERY_TIMEOUT_MS = 30 * 60 * 1000
const BACKTEST_LATEST_RECOVERY_MAX_AGE_MS = 6 * 60 * 60 * 1000

function resolveBacktestRequestBars(chartSettings, backtest) {
    const historyScopeMode = String(backtest?.historyScopeMode || 'loaded_chart').trim().toLowerCase() || 'loaded_chart'
    if (historyScopeMode === 'custom') {
        return Math.max(1, Number(backtest?.historyScopeBars) || 1)
    }

    return Math.max(1, Number(chartSettings?.bars) || 1)
}

function resolveBacktestMarketChartSettings(chartSettings, backtest, strategy, strategyEntries = []) {
    const normalizedChartSettings = normalizeChartSettings(chartSettings)
    const requestBars = resolveBacktestRequestBars(normalizedChartSettings, backtest)

    return buildStrategyCollectionChartSettings(
        {
            ...normalizedChartSettings,
            symbol: String(backtest?.symbol || normalizedChartSettings.symbol).trim().toUpperCase() || normalizedChartSettings.symbol,
            timeframe: String(backtest?.timeframe || normalizedChartSettings.timeframe).trim().toUpperCase() || normalizedChartSettings.timeframe,
            bars: Math.max(1, Number(requestBars) || Number(normalizedChartSettings.bars) || 1),
        },
        strategy,
        strategyEntries,
    )
}

function DiscreetProgressBar({ active = false }) {
    if (!active) {
        return null
    }

    return (
        <div className='consoleJobProgress' aria-hidden='true'>
            <div className='consoleJobProgressFill isIndeterminate' />
        </div>
    )
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

function normalizeBacktestPortfolioStructureVersion(value) {
    return Number(value) >= 2 ? 2 : 1
}

function buildStrategyEntryId(index = 0) {
    return `strategy-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

function buildStrategyEntryLabel(strategy, index = 0) {
    const longOpen = String(strategy?.long?.openIf || '').trim()
    const shortOpen = String(strategy?.short?.openIf || '').trim()
    if (longOpen && shortOpen) {
        return `Strategy ${index + 1} · Long/Short`
    }
    if (longOpen) {
        return `Strategy ${index + 1} · Long`
    }
    if (shortOpen) {
        return `Strategy ${index + 1} · Short`
    }
    return `Strategy ${index + 1}`
}

function normalizeStrategyEntryMarketValue(value, fallback = '') {
    return String(value || fallback || '').trim().toUpperCase()
}

function normalizeStrategyLabelForMatch(value) {
    return String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
}

function extractStrategyLabelMatchTokens(value) {
    return normalizeStrategyLabelForMatch(value)
        .split(' ')
        .map((token) => token.trim())
        .filter(Boolean)
}

function scoreStrategyLabelMatch(targetLabel, candidateLabel) {
    const safeTarget = normalizeStrategyLabelForMatch(targetLabel)
    const safeCandidate = normalizeStrategyLabelForMatch(candidateLabel)
    if (!safeTarget || !safeCandidate) {
        return 0
    }
    if (safeTarget === safeCandidate) {
        return 1000
    }
    if (safeTarget.includes(safeCandidate) || safeCandidate.includes(safeTarget)) {
        return 600
    }

    const targetTokens = extractStrategyLabelMatchTokens(safeTarget)
    const candidateTokens = new Set(extractStrategyLabelMatchTokens(safeCandidate))
    let score = 0
    for (const token of targetTokens) {
        if (!candidateTokens.has(token)) {
            continue
        }
        score += /\d/.test(token) ? 5 : 2
    }
    return score
}

function cloneStrategyEntryForComparison(entry) {
    return {
        symbol: normalizeStrategyEntryMarketValue(entry?.symbol),
        timeframe: normalizeStrategyEntryMarketValue(entry?.timeframe),
        strategy: cloneSerializable(entry?.strategy, {}),
    }
}

function buildStrategyEntrySignature(entry) {
    return JSON.stringify(cloneStrategyEntryForComparison(entry))
}

function isFalseLikeExpression(value) {
    const normalized = String(value || '').trim().toLowerCase()
    return !normalized || normalized === 'false'
}

function isNeutralPlaceholderStrategy(strategy) {
    const safeStrategy = strategy && typeof strategy === 'object' ? strategy : {}
    const long = safeStrategy.long && typeof safeStrategy.long === 'object' ? safeStrategy.long : {}
    const short = safeStrategy.short && typeof safeStrategy.short === 'object' ? safeStrategy.short : {}
    const indicators = Array.isArray(safeStrategy?.featureManifest?.indicators)
        ? safeStrategy.featureManifest.indicators
        : []

    const hasLongLogic = (
        !isFalseLikeExpression(long.openIf)
        || !isFalseLikeExpression(long.closeIf)
        || String(long.gainPrice || '').trim() !== ''
        || String(long.lossPrice || '').trim() !== ''
        || String(long.trailingPrice || '').trim() !== ''
    )
    const hasShortLogic = (
        !isFalseLikeExpression(short.openIf)
        || !isFalseLikeExpression(short.closeIf)
        || String(short.gainPrice || '').trim() !== ''
        || String(short.lossPrice || '').trim() !== ''
        || String(short.trailingPrice || '').trim() !== ''
    )

    return !hasLongLogic && !hasShortLogic && indicators.length === 0
}

function readBenchmarkPrimaryMarketContext(benchmark) {
    const safeBenchmark = benchmark && typeof benchmark === 'object' ? benchmark : {}
    return {
        symbol: normalizeStrategyEntryMarketValue(safeBenchmark.symbol || safeBenchmark?.strategy?.symbol || ''),
        timeframe: normalizeStrategyEntryMarketValue(safeBenchmark.timeframe || safeBenchmark?.strategy?.timeframe || ''),
    }
}

function normalizeStrategySetEntries(entries) {
    if (!Array.isArray(entries)) {
        return []
    }

    return entries
        .map((entry, index) => ({
            ...entry,
            id: String(entry?.id || '').trim() || buildStrategyEntryId(index),
            label: String(entry?.label || '').trim() || buildStrategyEntryLabel(entry?.strategy, index),
            priority: Number.isFinite(Number(entry?.priority)) ? Number(entry.priority) : index,
            enabled: entry?.enabled !== false,
            symbol: normalizeStrategyEntryMarketValue(entry?.symbol),
            timeframe: normalizeStrategyEntryMarketValue(entry?.timeframe),
            allocationMode: String(entry?.allocationMode || 'fixed_volume').trim() || 'fixed_volume',
            allocationValue: entry?.allocationValue ?? null,
            strategy: cloneSerializable(entry?.strategy, null),
        }))
        .filter((entry) => entry.strategy && typeof entry.strategy === 'object')
        .sort((left, right) => (
            Number(left.priority) - Number(right.priority)
            || String(left.id).localeCompare(String(right.id))
        ))
        .map((entry, index) => ({
            ...entry,
            priority: index,
        }))
}

function extractStrategyEntriesFromBacktestPortfolios(portfolios) {
    if (!Array.isArray(portfolios)) {
        return []
    }

    const entries = []
    portfolios.forEach((portfolio, portfolioIndex) => {
        const portfolioId = String(portfolio?.id || `portfolio-${portfolioIndex + 1}`).trim() || `portfolio-${portfolioIndex + 1}`
        const portfolioLabel = String(portfolio?.label || `Portfolio ${portfolioIndex + 1}`).trim() || `Portfolio ${portfolioIndex + 1}`
        const pipelines = Array.isArray(portfolio?.pipelines) ? portfolio.pipelines : []
        pipelines.forEach((pipeline, pipelineIndex) => {
            const pipelineId = String(pipeline?.id || `${portfolioId}-pipeline-${pipelineIndex + 1}`).trim() || `${portfolioId}-pipeline-${pipelineIndex + 1}`
            const pipelineLabel = String(pipeline?.label || `Pipeline ${pipelineIndex + 1}`).trim() || `Pipeline ${pipelineIndex + 1}`
            const strategyEntries = normalizeStrategySetEntries(
                Array.isArray(pipeline?.strategyEntries) ? pipeline.strategyEntries : [],
            )
            strategyEntries.forEach((entry, entryIndex) => {
                entries.push({
                    ...entry,
                    priority: entries.length + entryIndex,
                    portfolioId,
                    portfolioLabel,
                    pipelineId,
                    pipelineLabel,
                    volumeMode: String(entry?.volumeMode || 'fixed_volume').trim().toLowerCase() || 'fixed_volume',
                    fixedVolume: entry?.fixedVolume ?? null,
                    baseVolume: entry?.baseVolume ?? null,
                    maxVolumeCap: entry?.maxVolumeCap ?? null,
                    referenceCapital: entry?.referenceCapital ?? null,
                })
            })
        })
    })

    return entries.map((entry, index) => ({
        ...entry,
        priority: index,
    }))
}

function hasExplicitPortfolioBacktestConfig(backtest) {
    return (
        normalizeBacktestPortfolioStructureVersion(backtest?.portfolioStructureVersion) >= 2
        && Array.isArray(backtest?.portfolios)
        && backtest.portfolios.length > 0
    )
}

function summarizeStrategySet(entries = []) {
    const activeEntries = (entries || []).filter((entry) => entry?.enabled !== false)
    if (!activeEntries.length) {
        return 'Single strategy'
    }
    if (activeEntries.length === 1) {
        return activeEntries[0].label || '1 strategy'
    }
    return `${activeEntries.length} strategies`
}

function getExecutableStrategyEntries(entries = []) {
    const normalizedEntries = normalizeStrategySetEntries(entries)
    const enabledEntries = normalizedEntries.filter((entry) => entry?.enabled !== false)
    return enabledEntries.length ? enabledEntries : normalizedEntries
}

function buildBacktestPipelineContext(entries = []) {
    const executableEntries = getExecutableStrategyEntries(entries)
    const primaryEntry = executableEntries[0] || null

    return {
        executableEntries,
        primaryEntry,
        primaryStrategy: cloneSerializable(primaryEntry?.strategy, {}),
        companionEntries: executableEntries.slice(1),
    }
}

function buildStrategyEntriesFromBenchmark(benchmark, { fallbackSymbol = '', fallbackTimeframe = '', startIndex = 0 } = {}) {
    const safeBenchmark = benchmark && typeof benchmark === 'object' ? benchmark : {}
    const benchmarkMarket = readBenchmarkPrimaryMarketContext(safeBenchmark)
    const safeFallbackSymbol = normalizeStrategyEntryMarketValue(benchmarkMarket.symbol || fallbackSymbol, BACKTEST_DEFAULTS.symbol)
    const safeFallbackTimeframe = normalizeStrategyEntryMarketValue(benchmarkMarket.timeframe || fallbackTimeframe, BACKTEST_DEFAULTS.timeframe)
    const benchmarkLabel = String(safeBenchmark.label || '').trim()
    const benchmarkId = String(safeBenchmark.id || '').trim()
    const rawEntries = []

    if (safeBenchmark.strategy && typeof safeBenchmark.strategy === 'object') {
        const primarySourceLabel = String(safeBenchmark?.strategyLabel || safeBenchmark?.strategy_label || '').trim()
            || buildStrategyEntryLabel(safeBenchmark.strategy, startIndex)
        rawEntries.push({
            label: primarySourceLabel,
            enabled: true,
            symbol: safeFallbackSymbol,
            timeframe: safeFallbackTimeframe,
            allocationMode: 'fixed_volume',
            allocationValue: null,
            strategy: cloneSerializable(safeBenchmark.strategy, safeBenchmark.strategy),
            sourceBenchmarkId: benchmarkId,
            sourceBenchmarkLabel: benchmarkLabel,
            sourceBenchmarkEntryLabel: primarySourceLabel,
        })
    }

    if (Array.isArray(safeBenchmark.strategies)) {
        safeBenchmark.strategies.forEach((entry, index) => {
            if (!entry?.strategy || typeof entry.strategy !== 'object') {
                return
            }
            const sourceEntryLabel = String(entry?.label || '').trim() || buildStrategyEntryLabel(entry.strategy, startIndex + index + 1)
            rawEntries.push({
                label: sourceEntryLabel,
                enabled: entry?.enabled !== false,
                symbol: normalizeStrategyEntryMarketValue(entry?.symbol, safeFallbackSymbol),
                timeframe: normalizeStrategyEntryMarketValue(entry?.timeframe, safeFallbackTimeframe),
                allocationMode: String(entry?.allocationMode || 'fixed_volume').trim() || 'fixed_volume',
                allocationValue: entry?.allocationValue ?? null,
                strategy: cloneSerializable(entry.strategy, entry.strategy),
                sourceBenchmarkId: benchmarkId,
                sourceBenchmarkLabel: benchmarkLabel,
                sourceBenchmarkEntryLabel: sourceEntryLabel,
            })
        })
    }

    const dedupedEntries = []
    const seenSignatures = new Set()
    for (const entry of rawEntries) {
        const signature = buildStrategyEntrySignature(entry)
        if (seenSignatures.has(signature)) {
            continue
        }
        seenSignatures.add(signature)
        dedupedEntries.push(entry)
    }

    const primaryCandidateIndex = dedupedEntries.reduce((bestIndex, entry, index, array) => {
        const currentScore = scoreStrategyLabelMatch(benchmarkLabel, entry?.label)
        const bestScore = bestIndex >= 0 ? scoreStrategyLabelMatch(benchmarkLabel, array[bestIndex]?.label) : -1
        if (currentScore > bestScore) {
            return index
        }
        return bestIndex
    }, -1)

    const orderedEntries = primaryCandidateIndex > 0
        ? [
            dedupedEntries[primaryCandidateIndex],
            ...dedupedEntries.filter((_, index) => index !== primaryCandidateIndex),
        ]
        : dedupedEntries

    return orderedEntries.map((entry, index) => ({
        ...entry,
        id: buildStrategyEntryId(startIndex + index),
        label: index === 0 && benchmarkLabel
            ? benchmarkLabel
            : (String(entry?.label || '').trim() || buildStrategyEntryLabel(entry?.strategy, startIndex + index)),
        priority: startIndex + index,
    }))
}

function selectBenchmarkEntryForRepair(entry, benchmark) {
    const benchmarkEntries = buildStrategyEntriesFromBenchmark(benchmark, {
        fallbackSymbol: entry?.symbol || benchmark?.symbol || BACKTEST_DEFAULTS.symbol,
        fallbackTimeframe: entry?.timeframe || benchmark?.timeframe || BACKTEST_DEFAULTS.timeframe,
    })
    if (!benchmarkEntries.length) {
        return null
    }

    const safeEntryLabel = String(entry?.label || '').trim()
    const safeSourceBenchmarkEntryLabel = String(entry?.sourceBenchmarkEntryLabel || '').trim()
    if (safeSourceBenchmarkEntryLabel) {
        const exactSourceLabelMatch = benchmarkEntries.find((candidate) => (
            String(candidate?.sourceBenchmarkEntryLabel || candidate?.label || '').trim() === safeSourceBenchmarkEntryLabel
        ))
        if (exactSourceLabelMatch) {
            return exactSourceLabelMatch
        }
    }

    const exactLabelMatch = benchmarkEntries.find((candidate) => String(candidate?.label || '').trim() === safeEntryLabel)
    if (exactLabelMatch) {
        return exactLabelMatch
    }

    const comparisonTarget = safeSourceBenchmarkEntryLabel || safeEntryLabel
    return benchmarkEntries.reduce((best, candidate) => {
        if (!best) {
            return candidate
        }
        const candidateLabel = String(candidate?.sourceBenchmarkEntryLabel || candidate?.label || '').trim()
        const bestLabel = String(best?.sourceBenchmarkEntryLabel || best?.label || '').trim()
        return scoreStrategyLabelMatch(comparisonTarget, candidateLabel) > scoreStrategyLabelMatch(comparisonTarget, bestLabel)
            ? candidate
            : best
    }, null)
}

function repairNeutralStrategySetEntriesFromLibrary(entries, benchmarks = []) {
    const safeBenchmarks = Array.isArray(benchmarks) ? benchmarks : []
    return normalizeStrategySetEntries(entries).map((entry) => {
        if (!isNeutralPlaceholderStrategy(entry?.strategy)) {
            return entry
        }

        const safeSourceBenchmarkId = String(entry?.sourceBenchmarkId || '').trim()
        const safeLabel = String(entry?.label || '').trim()
        const safeSymbol = normalizeStrategyEntryMarketValue(entry?.symbol)
        const safeTimeframe = normalizeStrategyEntryMarketValue(entry?.timeframe)

        const matchingBenchmark = safeBenchmarks.find((benchmark) => (
            (safeSourceBenchmarkId && String(benchmark?.id || '').trim() === safeSourceBenchmarkId)
            || (
                String(benchmark?.label || '').trim() === safeLabel
                && normalizeStrategyEntryMarketValue(benchmark?.symbol) === safeSymbol
                && normalizeStrategyEntryMarketValue(benchmark?.timeframe) === safeTimeframe
            )
        ))

        if (!matchingBenchmark) {
            return entry
        }

        const matchingBenchmarkEntry = selectBenchmarkEntryForRepair(entry, matchingBenchmark)
        if (!matchingBenchmarkEntry || isNeutralPlaceholderStrategy(matchingBenchmarkEntry?.strategy)) {
            return entry
        }

        return {
            ...entry,
            sourceBenchmarkId: safeSourceBenchmarkId || String(matchingBenchmark?.id || '').trim(),
            sourceBenchmarkLabel: String(entry?.sourceBenchmarkLabel || matchingBenchmark?.label || '').trim(),
            sourceBenchmarkEntryLabel: String(entry?.sourceBenchmarkEntryLabel || matchingBenchmarkEntry?.sourceBenchmarkEntryLabel || matchingBenchmarkEntry?.label || '').trim(),
            symbol: normalizeStrategyEntryMarketValue(entry?.symbol, matchingBenchmarkEntry?.symbol),
            timeframe: normalizeStrategyEntryMarketValue(entry?.timeframe, matchingBenchmarkEntry?.timeframe),
            strategy: cloneSerializable(matchingBenchmarkEntry.strategy, matchingBenchmarkEntry.strategy),
        }
    })
}

export function Backtester({
    authToken = '',
    backtest,
    setBacktest,
    strategySetEntries = [],
    setStrategySetEntries,
    chartSettings,
    lastBacktestResponse,
    onBacktestExecuted,
    onHydrateBacktestResult,
    onBacktestStatusChange,
    onBacktestRunStarted,
    onLoadStrategyIndicators,
    onLoadBacktestFlags,
    onLogEvent,
    isBusy = false,
    isActive,
    loadedChartCandles = 0,
    isStale = false,
    hasBacktestChartBuffer = false,
    sharedConsoleJobs = null,
    onSharedConsoleJobChange,
    isGuest = false,
    activeBrokerProfileId = '',
    activeBrokerProfileLabel = '',
    activeBrokerProfile = null,
}) {
    const [activeTab, setActiveTab] = useState('strategy_pipe')
    const [librarySourceTab, setLibrarySourceTab] = useState('strategies')
    const [strategyLibraryItems, setStrategyLibraryItems] = useState([])
    const [portfolioLibraryItems, setPortfolioLibraryItems] = useState([])
    const [isStrategyLibraryLoading, setIsStrategyLibraryLoading] = useState(false)
    const [isPortfolioLibraryLoading, setIsPortfolioLibraryLoading] = useState(false)
    const [strategyLibraryError, setStrategyLibraryError] = useState('')
    const [portfolioLibraryError, setPortfolioLibraryError] = useState('')
    const [strategyLibraryListTab, setStrategyLibraryListTab] = useState('all')
    const [selectedStrategyLibraryId, setSelectedStrategyLibraryId] = useState('')
    const [selectedPortfolioLibraryId, setSelectedPortfolioLibraryId] = useState('')
    const [strategyLibraryQuery, setStrategyLibraryQuery] = useState('')
    const authHeaders = useMemo(
        () => authToken
            ? {
                Authorization: `Bearer ${authToken}`,
            }
            : {},
        [authToken],
    )
    const normalizedStrategyLibraryQuery = String(strategyLibraryQuery || '').trim().toLowerCase()
    const visibleStrategyLibraryItems = useMemo(() => (
        (strategyLibraryListTab === 'favorites'
            ? strategyLibraryItems.filter((entry) => Boolean(entry?.is_favorite))
            : strategyLibraryItems)
            .filter((entry) => {
                if (!normalizedStrategyLibraryQuery) {
                    return true
                }
                const haystack = [
                    entry?.label,
                    entry?.notes,
                    entry?.source,
                    entry?.side,
                    ...(Array.isArray(entry?.strategies)
                        ? entry.strategies.flatMap((item) => [item?.label, item?.symbol, item?.timeframe])
                        : []),
                ]
                    .map((value) => String(value || '').trim().toLowerCase())
                    .filter(Boolean)
                    .join(' ')
                return haystack.includes(normalizedStrategyLibraryQuery)
            })
    ), [normalizedStrategyLibraryQuery, strategyLibraryItems, strategyLibraryListTab])
    const selectedStrategyLibraryItem = visibleStrategyLibraryItems.find((entry) => String(entry?.id) === String(selectedStrategyLibraryId)) || null
    const visiblePortfolioLibraryItems = useMemo(() => (
        (strategyLibraryListTab === 'favorites'
            ? portfolioLibraryItems.filter((entry) => Boolean(entry?.is_favorite))
            : portfolioLibraryItems)
            .filter((entry) => {
                if (!normalizedStrategyLibraryQuery) {
                    return true
                }
                const haystack = [
                    entry?.label,
                    entry?.notes,
                    entry?.source,
                    entry?.portfolio?.label,
                    ...(Array.isArray(entry?.portfolio?.pipelines)
                        ? entry.portfolio.pipelines.flatMap((pipeline) => [
                            pipeline?.label,
                            ...(Array.isArray(pipeline?.entries)
                                ? pipeline.entries.flatMap((item) => [item?.label, item?.symbol, item?.timeframe, item?.sourceBenchmarkLabel])
                                : []),
                        ])
                        : []),
                ]
                    .map((value) => String(value || '').trim().toLowerCase())
                    .filter(Boolean)
                    .join(' ')
                return haystack.includes(normalizedStrategyLibraryQuery)
            })
    ), [normalizedStrategyLibraryQuery, portfolioLibraryItems, strategyLibraryListTab])
    const selectedPortfolioLibraryItem = visiblePortfolioLibraryItems.find((entry) => String(entry?.id) === String(selectedPortfolioLibraryId)) || null
    const activeLibraryError = librarySourceTab === 'portfolios' ? portfolioLibraryError : strategyLibraryError
    const logEventRef = useRef(onLogEvent)
    const previousIsActiveRef = useRef(Boolean(isActive))
    const lastStrategyLibraryBootstrapKeyRef = useRef('')
    const latestCompletedRecoveryAttemptRef = useRef('')

    useEffect(() => {
        logEventRef.current = onLogEvent
    }, [onLogEvent])

    function resetAllFields() {
        setBacktest((previous) => ({
            ...previous,
            ...BACKTEST_DEFAULTS,
            ...buildBacktestCostProfileValues(BACKTEST_DEFAULTS.costProfile, activeBrokerProfile, BACKTEST_DEFAULTS),
            assetType: resolveContextDefaultAssetType(activeBrokerProfile, BACKTEST_DEFAULTS, BACKTEST_DEFAULTS.costProfile),
            symbol: String(chartSettings?.symbol || BACKTEST_DEFAULTS.symbol).trim().toUpperCase() || BACKTEST_DEFAULTS.symbol,
            timeframe: BACKTEST_DEFAULTS.timeframe,
        }))
        logEventRef.current?.('Backtester · Reset all execution fields to defaults.')
    }

    function normalizeBacktestPayload() {
        const normalizedCostProfile = normalizeBacktestCostProfile(backtest.costProfile)
        const brokerScopedCostValues = normalizedCostProfile !== 'custom'
            ? buildBacktestCostProfileValues(normalizedCostProfile, activeBrokerProfile, backtest)
            : {}
        const effectiveAssetType = resolveBacktestAssetType(
            backtest.assetType,
            activeBrokerProfile,
            backtest,
            normalizedCostProfile,
        )
        return {
            initialBalance: Number(backtest.initialBalance),
            assetType: String(effectiveAssetType).trim().toLowerCase(),
            initialVolume: Number(backtest.initialVolume),
            pipSize: Number(backtest.pipSize),
            pipValuePerLot: Number(backtest.pipValuePerLot),
            costProfile: normalizedCostProfile,
            spreadInPips: Number(brokerScopedCostValues.spreadInPips ?? backtest.spreadInPips),
            slippageInPips: Number(brokerScopedCostValues.slippageInPips ?? backtest.slippageInPips),
            entrySlippageInPips: Number(brokerScopedCostValues.entrySlippageInPips ?? backtest.entrySlippageInPips),
            closeSlippageInPips: Number(brokerScopedCostValues.closeSlippageInPips ?? backtest.closeSlippageInPips),
            takeProfitSlippageInPips: Number(brokerScopedCostValues.takeProfitSlippageInPips ?? backtest.takeProfitSlippageInPips),
            stopLossSlippageInPips: Number(brokerScopedCostValues.stopLossSlippageInPips ?? backtest.stopLossSlippageInPips),
            trailingStopSlippageInPips: Number(brokerScopedCostValues.trailingStopSlippageInPips ?? backtest.trailingStopSlippageInPips),
            minimumStopDistanceInPips: Number(brokerScopedCostValues.minimumStopDistanceInPips ?? backtest.minimumStopDistanceInPips),
            volatilitySlippageMultiplier: Number(brokerScopedCostValues.volatilitySlippageMultiplier ?? backtest.volatilitySlippageMultiplier),
            executionMode: String(backtest.executionMode || 'next_bar_open').trim().toLowerCase() || 'next_bar_open',
            portfolioMode: String(backtest.portfolioMode || BACKTEST_DEFAULTS.portfolioMode).trim().toLowerCase() || BACKTEST_DEFAULTS.portfolioMode,
            symbol: String(backtest.symbol || chartSettings?.symbol || BACKTEST_DEFAULTS.symbol).trim().toUpperCase() || BACKTEST_DEFAULTS.symbol,
            timeframe: String(backtest.timeframe || BACKTEST_DEFAULTS.timeframe).trim().toUpperCase() || BACKTEST_DEFAULTS.timeframe,
            historyScopeMode: String(backtest.historyScopeMode || 'loaded_chart').trim().toLowerCase() || 'loaded_chart',
            historyScopeBars: String(backtest.historyScopeMode || 'loaded_chart').trim().toLowerCase() === 'custom'
                ? Math.max(1, Number(backtest.historyScopeBars) || 1)
                : null,
            brokerProfileId: String(activeBrokerProfileId || '').trim(),
            brokerProfileLabel: String(activeBrokerProfileLabel || activeBrokerProfile?.label || '').trim(),
            brokerCode: String(activeBrokerProfile?.broker_code || '').trim().toLowerCase(),
            brokerLabel: String(activeBrokerProfile?.label || activeBrokerProfileLabel || '').trim(),
            brokerMarketDomain: String(activeBrokerProfile?.market_domain || '').trim().toLowerCase(),
            brokerCostProfile: String(activeBrokerProfile?.profile?.cost_profile || activeBrokerProfile?.profile?.costProfile || '').trim().toLowerCase(),
            brokerDefaultAssetType: String(activeBrokerProfile?.profile?.default_asset_type || activeBrokerProfile?.profile?.defaultAssetType || '').trim().toLowerCase(),
        }
    }

    const [isRunning, setIsRunning] = useState(false)
    const runAbortControllerRef = useRef(null)
    const backtestJobIdRef = useRef('')
    const backtestPollTimeoutRef = useRef(null)
    const backtestPollFailureCountRef = useRef(0)
    const normalizedLoadedChartCandles = Math.max(0, Number(loadedChartCandles) || 0)
    const sharedBacktestJob = sharedConsoleJobs?.backtest
    const isSharedRunning = sharedBacktestJob?.status === 'running'
    const isBacktestRunning = isRunning || isSharedRunning
    const activeStrategyEntriesCount = strategySetEntries.filter((entry) => entry.enabled !== false).length
    const activeBacktestJobId = String(sharedBacktestJob?.jobId || backtestJobIdRef.current || '').trim()
    const activeBacktestLabel = String(sharedBacktestJob?.label || '').trim() || 'Running backtest'
    const guestRestrictionMessage = 'Guest demo can inspect Backtester settings and the curated strategy, but cannot run backtests or save favorite changes.'
    const refreshStrategyLibrary = useCallback(async ({ quiet = false } = {}) => {
        if (!authToken) {
            setStrategyLibraryItems([])
            setSelectedStrategyLibraryId('')
            setStrategyLibraryError('')
            return
        }
        if (!quiet) {
            setIsStrategyLibraryLoading(true)
        }
        try {
            const response = await fetchWithServerRetry(
                buildApiUrl(`/workspace/strategy-benchmarks?${buildBrokerProfileQuery({
                    workspaceId: 'default',
                    limit: STRATEGY_LIBRARY_FETCH_LIMIT,
                    brokerProfileId: activeBrokerProfileId,
                })}`),
                {
                    headers: authHeaders,
                },
                {
                    attempts: 4,
                    retryDelayMs: 750,
                },
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to load saved strategies.'))
            }
            const benchmarks = Array.isArray(data?.benchmarks) ? data.benchmarks : []
            setStrategyLibraryItems(benchmarks)
            setStrategyLibraryError('')
            setSelectedStrategyLibraryId((current) => {
                if (current && benchmarks.some((entry) => String(entry?.id) === String(current))) {
                    return current
                }
                return String(benchmarks[0]?.id || '')
            })
        } catch (error) {
            const message = error?.message || 'Failed to load saved strategies.'
            setStrategyLibraryError(message)
            logEventRef.current?.(`Backtester · ${message}`)
        } finally {
            if (!quiet) {
                setIsStrategyLibraryLoading(false)
            }
        }
    }, [activeBrokerProfileId, authHeaders, authToken])

    const refreshPortfolioLibrary = useCallback(async ({ quiet = false } = {}) => {
        if (!authToken) {
            setPortfolioLibraryItems([])
            setSelectedPortfolioLibraryId('')
            setPortfolioLibraryError('')
            return
        }
        if (!quiet) {
            setIsPortfolioLibraryLoading(true)
        }
        try {
            const response = await fetchWithServerRetry(
                buildApiUrl(`/workspace/saved-portfolios?${buildBrokerProfileQuery({
                    workspaceId: 'default',
                    limit: STRATEGY_LIBRARY_FETCH_LIMIT,
                    brokerProfileId: activeBrokerProfileId,
                })}`),
                {
                    headers: authHeaders,
                },
                {
                    attempts: 4,
                    retryDelayMs: 750,
                },
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to load saved portfolios.'))
            }
            const portfolios = Array.isArray(data?.portfolios) ? data.portfolios : []
            setPortfolioLibraryItems(portfolios)
            setPortfolioLibraryError('')
            setSelectedPortfolioLibraryId((current) => {
                if (current && portfolios.some((entry) => String(entry?.id) === String(current))) {
                    return current
                }
                return String(portfolios[0]?.id || '')
            })
        } catch (error) {
            const message = error?.message || 'Failed to load saved portfolios.'
            setPortfolioLibraryError(message)
            logEventRef.current?.(`Backtester · ${message}`)
        } finally {
            if (!quiet) {
                setIsPortfolioLibraryLoading(false)
            }
        }
    }, [activeBrokerProfileId, authHeaders, authToken])

    useEffect(() => {
        const bootstrapKey = authToken
            ? `token:${authToken}:broker:${activeBrokerProfileId || 'all'}`
            : `anonymous:broker:${activeBrokerProfileId || 'all'}`
        if (lastStrategyLibraryBootstrapKeyRef.current === bootstrapKey) {
            return
        }
        lastStrategyLibraryBootstrapKeyRef.current = bootstrapKey
        void refreshStrategyLibrary({ quiet: false })
        void refreshPortfolioLibrary({ quiet: false })
    }, [activeBrokerProfileId, authToken, refreshPortfolioLibrary, refreshStrategyLibrary])

    useEffect(() => {
        const safeIsActive = Boolean(isActive)
        const becameActive = safeIsActive && !previousIsActiveRef.current
        previousIsActiveRef.current = safeIsActive
        if (!becameActive) {
            return
        }
        void refreshStrategyLibrary({ quiet: true })
        void refreshPortfolioLibrary({ quiet: true })
    }, [isActive, refreshPortfolioLibrary, refreshStrategyLibrary])

    useEffect(() => {
        setSelectedStrategyLibraryId((current) => {
            if (current && visibleStrategyLibraryItems.some((entry) => String(entry?.id) === String(current))) {
                return current
            }
            return String(visibleStrategyLibraryItems[0]?.id || '')
        })
    }, [visibleStrategyLibraryItems])

    useEffect(() => {
        setSelectedPortfolioLibraryId((current) => {
            if (current && visiblePortfolioLibraryItems.some((entry) => String(entry?.id) === String(current))) {
                return current
            }
            return String(visiblePortfolioLibraryItems[0]?.id || '')
        })
    }, [visiblePortfolioLibraryItems])

    const syncBacktestPortfoliosFromEntries = useCallback((nextEntries, { forceExplicit = false } = {}) => {
        setBacktest((current) => {
            if (!forceExplicit && !hasExplicitPortfolioBacktestConfig(current)) {
                return current
            }
            return {
                ...current,
                portfolioStructureVersion: 2,
                portfolios: rebuildBacktestPortfoliosFromEntries(
                    nextEntries,
                    Array.isArray(current?.portfolios) ? current.portfolios : [],
                    String(current?.portfolioMode || BACKTEST_DEFAULTS.portfolioMode).trim().toLowerCase() || BACKTEST_DEFAULTS.portfolioMode,
                ),
            }
        })
    }, [setBacktest])

    const applyStrategySetEntriesUpdate = useCallback((updater, { forceExplicit = false } = {}) => {
        setStrategySetEntries((current) => {
            const baseEntries = normalizeStrategySetEntries(current)
            const nextEntries = normalizeStrategySetEntries(
                typeof updater === 'function' ? updater(baseEntries) : updater,
            )
            syncBacktestPortfoliosFromEntries(nextEntries, { forceExplicit })
            return nextEntries
        })
    }, [setStrategySetEntries, syncBacktestPortfoliosFromEntries])

    useEffect(() => {
        if (!Array.isArray(strategyLibraryItems) || !strategyLibraryItems.length) {
            return
        }
        const normalizedCurrent = normalizeStrategySetEntries(strategySetEntries)
        if (!normalizedCurrent.length || !normalizedCurrent.some((entry) => isNeutralPlaceholderStrategy(entry?.strategy))) {
            return
        }

        const repairedEntries = repairNeutralStrategySetEntriesFromLibrary(normalizedCurrent, strategyLibraryItems)
        if (JSON.stringify(repairedEntries) === JSON.stringify(normalizedCurrent)) {
            return
        }

        applyStrategySetEntriesUpdate(() => repairedEntries, {
            forceExplicit: hasExplicitPortfolioBacktestConfig(backtest),
        })
        logEventRef.current?.('Backtester · Repaired a neutral saved-strategy copy from the benchmark library.')
    }, [applyStrategySetEntriesUpdate, backtest, strategyLibraryItems, strategySetEntries])

    async function handleToggleFavoriteStrategyInLibrary(targetEntry = selectedStrategyLibraryItem) {
        if (isGuest) {
            logEventRef.current?.('Backtester · Guest demo can inspect saved strategies, but favorite changes are disabled.')
            return
        }
        if (!authToken || !targetEntry?.id) {
            return
        }

        const nextIsFavorite = !targetEntry?.is_favorite
        try {
            const response = await fetch(
                buildApiUrl(`/workspace/strategy-benchmarks/${targetEntry.id}?workspace_id=default`),
                {
                    method: 'PATCH',
                    headers: {
                        ...authHeaders,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        workspace_id: 'default',
                        is_favorite: Boolean(nextIsFavorite),
                    }),
                }
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to update favorite strategy.'))
            }
            logEventRef.current?.(
                nextIsFavorite
                    ? `Backtester · Marked "${targetEntry.label || `Strategy #${targetEntry.id}`}" as favorite.`
                    : `Backtester · Removed "${targetEntry.label || `Strategy #${targetEntry.id}`}" from favorites.`
            )
            await refreshStrategyLibrary({ quiet: true })
        } catch (error) {
            logEventRef.current?.(`Backtester · ${error?.message || 'Failed to update favorite strategy.'}`)
        }
    }

    async function handleToggleFavoritePortfolioInLibrary(targetEntry = selectedPortfolioLibraryItem) {
        if (isGuest) {
            logEventRef.current?.('Backtester · Guest demo can inspect saved portfolios, but favorite changes are disabled.')
            return
        }
        if (!authToken || !targetEntry?.id) {
            return
        }

        const nextIsFavorite = !targetEntry?.is_favorite
        try {
            const response = await fetch(
                buildApiUrl(`/workspace/saved-portfolios/${targetEntry.id}?workspace_id=default`),
                {
                    method: 'PATCH',
                    headers: {
                        ...authHeaders,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        workspace_id: 'default',
                        is_favorite: Boolean(nextIsFavorite),
                    }),
                }
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to update favorite portfolio.'))
            }
            await refreshPortfolioLibrary({ quiet: true })
        } catch (error) {
            logEventRef.current?.(`Backtester · ${error?.message || 'Failed to update favorite portfolio.'}`)
        }
    }

    const clearBacktestPollingState = useCallback(() => {
        runAbortControllerRef.current = null
        backtestJobIdRef.current = ''
        backtestPollFailureCountRef.current = 0
        if (backtestPollTimeoutRef.current) {
            clearTimeout(backtestPollTimeoutRef.current)
            backtestPollTimeoutRef.current = null
        }
        setIsRunning(false)
    }, [])

    const buildHydratedBacktestPayload = useCallback((resultPayload) => {
        const request = resultPayload?.request && typeof resultPayload.request === 'object'
            ? resultPayload.request
            : {}
        const requestStrategies = normalizeStrategySetEntries(request?.strategies || [])
        const requestStrategy = request?.strategy && typeof request.strategy === 'object'
            ? request.strategy
            : cloneSerializable(requestStrategies[0]?.strategy, {})
        const requestBacktest = request?.backtest && typeof request.backtest === 'object'
            ? request.backtest
            : backtest
        const resumedChartSettings = buildStrategyCollectionChartSettings(
            normalizeChartSettings({
                ...(chartSettings || {}),
                symbol: request?.symbol || chartSettings?.symbol,
                timeframe: request?.timeframe || chartSettings?.timeframe,
                bars: Math.max(1, Number(request?.bars) || Number(chartSettings?.bars) || 1000),
                indicators: Array.isArray(request?.indicators) ? request.indicators : (chartSettings?.indicators || []),
            }),
            requestStrategy,
            requestStrategies,
        )

        return {
            chartSettings: resumedChartSettings,
            strategy: requestStrategy,
            strategies: requestStrategies,
            backtest: requestBacktest,
            strategyResponse: resultPayload,
        }
    }, [backtest, chartSettings])

    const hydrateCompletedBacktestResult = useCallback((resultPayload, runContext = null) => {
        if (!resultPayload || resultPayload.status !== 'ok') {
            throw new Error('Backtest job completed without a valid result payload.')
        }

        if (runContext?.finalChartPayload) {
            onBacktestExecuted?.({
                chartSettings: runContext.finalChartPayload,
                strategy: runContext.strategyPayload,
                strategies: runContext.strategiesPayload,
                backtest: runContext.backtestPayload,
                strategyResponse: resultPayload,
            })
            return
        }

        const hydratedPayload = buildHydratedBacktestPayload(resultPayload)
        if (typeof onHydrateBacktestResult === 'function') {
            onHydrateBacktestResult(hydratedPayload)
            return
        }
        onBacktestExecuted?.(hydratedPayload)
    }, [buildHydratedBacktestPayload, onBacktestExecuted, onHydrateBacktestResult])

    const fetchLatestBacktestJobSnapshot = useCallback(async ({ preferJobId = '', status = '' } = {}) => {
        const params = new URLSearchParams()
        if (String(preferJobId || '').trim()) {
            params.set('prefer_job_id', String(preferJobId).trim())
        }
        if (String(status || '').trim()) {
            params.set('status', String(status).trim())
        }
        const query = params.toString()
        const response = await fetchWithServerRetry(
            buildApiUrl(`/strategy/backtest-jobs/latest${query ? `?${query}` : ''}`),
            {
                headers: {
                    ...authHeaders,
                },
            },
            {
                attempts: 3,
                retryDelayMs: 800,
            },
        )
        const data = await readJsonResponse(response)
        if (!response.ok || data?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(data, 'Failed to load the latest backtest job.'))
        }
        return data?.job || null
    }, [authHeaders])

    const runInlineBacktestFallback = useCallback(async ({
        requestPayload = null,
        startedAtPerf = null,
        finalChartPayload = null,
        strategyPayload = null,
        strategiesPayload = [],
        backtestPayload = backtest,
        missingJobId = '',
    } = {}) => {
        if (!requestPayload || typeof requestPayload !== 'object') {
            throw new Error('The active backtest job was lost and no recovery payload was available.')
        }

        const safeJobId = String(missingJobId || '').trim()
        onSharedConsoleJobChange?.('backtest', {
            status: 'running',
            label: safeJobId
                ? `Recovering lost backend job ${safeJobId}.`
                : 'Recovering lost backend backtest job.',
            startedAt: new Date().toISOString(),
            actor: 'backtester',
            jobId: safeJobId || undefined,
        })
        logEventRef.current?.(
            safeJobId
                ? `Backtester · Backend job ${safeJobId} disappeared. Re-running through the inline recovery path.`
                : 'Backtester · Backend backtest job disappeared. Re-running through the inline recovery path.'
        )

        const response = await fetchWithServerRetry(buildApiUrl('/strategy/apply-in-context'), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...authHeaders,
            },
            body: JSON.stringify(requestPayload),
        }, {
            attempts: 3,
            retryDelayMs: 800,
        })
        const data = await readJsonResponse(response)

        if (!response.ok || data?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(data, 'Failed to recover the backtest after the backend job was lost.'))
        }

        hydrateCompletedBacktestResult(data, {
            finalChartPayload,
            strategyPayload,
            strategiesPayload,
            backtestPayload,
        })
        onBacktestStatusChange?.({
            backtestError: '',
            resultsError: '',
            strategyPending: false,
            backtestBusy: false,
            backtestPending: false,
            resultsPending: false,
        })
        onSharedConsoleJobChange?.('backtest', null)
        clearBacktestPollingState()
        if (Number.isFinite(startedAtPerf)) {
            logEventRef.current?.(`Backtest completed through the inline recovery path (${((performance.now() - startedAtPerf) / 1000).toFixed(2)}s).`)
        } else {
            logEventRef.current?.('Backtest completed through the inline recovery path.')
        }
    }, [
        authHeaders,
        backtest,
        buildApiUrl,
        clearBacktestPollingState,
        hydrateCompletedBacktestResult,
        onBacktestStatusChange,
        onSharedConsoleJobChange,
    ])

    const handleBacktestFailure = useCallback((error, options = {}) => {
        const safeMessage = String(error?.message || 'Could not run backtest.')
        const message = options?.resumeOnly && /was not found/i.test(safeMessage)
            ? 'The active backtest job is no longer available. The backend likely restarted before it finished.'
            : safeMessage
        console.error('Failed to run backtest:', error)
        onBacktestStatusChange?.({
            backtestError: message,
            resultsError: message,
            strategyPending: false,
            backtestBusy: false,
            backtestPending: false,
            resultsPending: false,
        })
        onSharedConsoleJobChange?.('backtest', null)
        clearBacktestPollingState()
        logEventRef.current?.(`Backtest run failed: ${message}`)
    }, [clearBacktestPollingState, onBacktestStatusChange, onSharedConsoleJobChange])

    const pollBacktestJob = useCallback(async ({
        jobId,
        abortController,
        startedAtIso = new Date().toISOString(),
        startedAtPerf = null,
        requestedChartBars = null,
        finalChartPayload = null,
        strategyPayload = null,
        strategiesPayload = [],
        backtestPayload = backtest,
        requestPayload = null,
        resumeOnly = false,
    }) => {
        try {
            const pollResponse = await fetch(buildApiUrl(`/strategy/backtest-jobs/${encodeURIComponent(jobId)}`), {
                method: 'GET',
                headers: {
                    ...authHeaders,
                },
                signal: abortController.signal,
            })
            const pollData = await readJsonResponse(pollResponse)
            if (!pollResponse.ok || pollData?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(pollData, 'Failed to read backtest job status.'))
            }

            backtestPollFailureCountRef.current = 0
            const job = pollData?.job || {}
            const jobStatus = String(job?.status || '').trim().toLowerCase()
            const jobDetail = String(job?.detail || '').trim()
            const effectiveStartedAtIso = String(job?.started_at || startedAtIso || '').trim() || new Date().toISOString()
            onSharedConsoleJobChange?.('backtest', {
                status: jobStatus === 'running' || jobStatus === 'queued' ? 'running' : jobStatus,
                label: jobDetail || 'Running backtest',
                startedAt: effectiveStartedAtIso,
                actor: 'backtester',
                jobId,
            })

            if (jobStatus === 'completed') {
                const resultPayload = job?.result || null
                if (requestedChartBars && Number(requestedChartBars) > Math.max(1, Number(finalChartPayload?.bars) || 1)) {
                    logEventRef.current?.(
                        `Backtest used an isolated market context of ${Number(requestedChartBars || 0).toLocaleString()} candles without inflating the visible chart.`
                    )
                }
                hydrateCompletedBacktestResult(resultPayload, {
                    finalChartPayload,
                    strategyPayload,
                    strategiesPayload,
                    backtestPayload,
                })
                onBacktestStatusChange?.({
                    backtestError: '',
                    resultsError: '',
                    strategyPending: false,
                    backtestBusy: false,
                    backtestPending: false,
                    resultsPending: false,
                })
                onSharedConsoleJobChange?.('backtest', null)
                clearBacktestPollingState()
                if (Number.isFinite(startedAtPerf)) {
                    logEventRef.current?.(`Backtest completed (${((performance.now() - startedAtPerf) / 1000).toFixed(2)}s).`)
                } else if (resumeOnly) {
                    logEventRef.current?.(`Backtest completed after reconnecting to job ${jobId}.`)
                } else {
                    logEventRef.current?.('Backtest completed.')
                }
                return
            }

            if (jobStatus === 'failed') {
                throw new Error(String(job?.error || 'Failed to run backtest.'))
            }

            if (jobStatus === 'cancelled') {
                onSharedConsoleJobChange?.('backtest', null)
                clearBacktestPollingState()
                onBacktestStatusChange?.({
                    strategyPending: false,
                    backtestBusy: false,
                    backtestPending: false,
                    resultsPending: false,
                })
                logEventRef.current?.('Backtest run canceled.')
                return
            }

            backtestPollTimeoutRef.current = setTimeout(() => {
                pollBacktestJob({
                    jobId,
                    abortController,
                    startedAtIso: effectiveStartedAtIso,
                    startedAtPerf,
                    requestedChartBars,
                    finalChartPayload,
                    strategyPayload,
                    strategiesPayload,
                    backtestPayload,
                    resumeOnly,
                }).catch((error) => handleBacktestFailure(error, { resumeOnly }))
            }, 1000)
        } catch (error) {
            const safeMessage = String(error?.message || '').trim()
            const normalizedMessage = safeMessage.toLowerCase()
            const shouldRetry = (
                !abortController.signal.aborted
                && !/was not found/i.test(safeMessage)
                && (
                    normalizedMessage.includes('failed to read backtest job status')
                    || normalizedMessage.includes('server returned an invalid response')
                    || normalizedMessage.includes('failed to fetch')
                    || normalizedMessage.includes('networkerror')
                    || normalizedMessage.includes('load failed')
                )
            )

            if (shouldRetry) {
                const nextRetryCount = backtestPollFailureCountRef.current + 1
                if (nextRetryCount <= BACKTEST_POLL_TRANSIENT_RETRY_LIMIT) {
                    backtestPollFailureCountRef.current = nextRetryCount
                    onSharedConsoleJobChange?.('backtest', {
                        status: 'running',
                        label: nextRetryCount === 1
                            ? 'Reconnecting to the backend backtest job.'
                            : `Reconnecting to the backend backtest job (${nextRetryCount}/${BACKTEST_POLL_TRANSIENT_RETRY_LIMIT}).`,
                        startedAt: String(startedAtIso || '').trim() || new Date().toISOString(),
                        actor: 'backtester',
                        jobId,
                    })
                    logEventRef.current?.(
                        `Backtester · Polling retry ${nextRetryCount}/${BACKTEST_POLL_TRANSIENT_RETRY_LIMIT} for active job ${jobId}.`
                    )
                    backtestPollTimeoutRef.current = setTimeout(() => {
                        pollBacktestJob({
                            jobId,
                            abortController,
                            startedAtIso,
                            startedAtPerf,
                            requestedChartBars,
                            finalChartPayload,
                            strategyPayload,
                            strategiesPayload,
                            backtestPayload,
                            requestPayload,
                            resumeOnly,
                        }).catch((nextError) => handleBacktestFailure(nextError, { resumeOnly }))
                    }, BACKTEST_POLL_RETRY_DELAY_MS)
                    return
                }

                const startedAtMs = Date.parse(String(startedAtIso || ''))
                const canKeepRecovering = (
                    !Number.isFinite(startedAtMs)
                    || startedAtMs <= 0
                    || (Date.now() - startedAtMs) < BACKTEST_POLL_RECOVERY_TIMEOUT_MS
                )

                if (canKeepRecovering) {
                    let recoveredJob = null
                    try {
                        recoveredJob = await fetchLatestBacktestJobSnapshot({ preferJobId: jobId })
                    } catch {
                        recoveredJob = null
                    }

                    const recoveredStatus = String(recoveredJob?.status || '').trim().toLowerCase()

                    if (recoveredStatus === 'completed' && recoveredJob?.result?.status === 'ok') {
                        if (requestedChartBars && Number(requestedChartBars) > Math.max(1, Number(finalChartPayload?.bars) || 1)) {
                            logEventRef.current?.(
                                `Backtest used an isolated market context of ${Number(requestedChartBars || 0).toLocaleString()} candles without inflating the visible chart.`
                            )
                        }
                        hydrateCompletedBacktestResult(recoveredJob.result, {
                            finalChartPayload,
                            strategyPayload,
                            strategiesPayload,
                            backtestPayload,
                        })
                        onBacktestStatusChange?.({
                            backtestError: '',
                            resultsError: '',
                            strategyPending: false,
                            backtestBusy: false,
                            backtestPending: false,
                            resultsPending: false,
                        })
                        onSharedConsoleJobChange?.('backtest', null)
                        clearBacktestPollingState()
                        logEventRef.current?.(`Backtest completed after recovering job ${jobId} from workspace storage.`)
                        return
                    }

                    if (recoveredStatus === 'failed') {
                        throw new Error(String(recoveredJob?.error || recoveredJob?.detail || 'Failed to run backtest.'))
                    }

                    if (recoveredStatus === 'cancelled') {
                        onSharedConsoleJobChange?.('backtest', null)
                        clearBacktestPollingState()
                        onBacktestStatusChange?.({
                            strategyPending: false,
                            backtestBusy: false,
                            backtestPending: false,
                            resultsPending: false,
                        })
                        logEventRef.current?.('Backtest run canceled.')
                        return
                    }

                    backtestPollFailureCountRef.current = 0
                    onSharedConsoleJobChange?.('backtest', {
                        status: 'running',
                        label: 'Waiting for backend status recovery.',
                        startedAt: String(startedAtIso || '').trim() || new Date().toISOString(),
                        actor: 'backtester',
                        jobId,
                    })
                    logEventRef.current?.(`Backtester · Waiting for backend status recovery on job ${jobId}.`)
                    backtestPollTimeoutRef.current = setTimeout(() => {
                        pollBacktestJob({
                            jobId,
                            abortController,
                            startedAtIso,
                            startedAtPerf,
                            requestedChartBars,
                            finalChartPayload,
                            strategyPayload,
                            strategiesPayload,
                            backtestPayload,
                            requestPayload,
                            resumeOnly,
                        }).catch((nextError) => handleBacktestFailure(nextError, { resumeOnly }))
                    }, BACKTEST_POLL_RECOVERY_DELAY_MS)
                    return
                }
            }

            if (
                !abortController.signal.aborted
                && requestPayload
                && (
                    /was not found/i.test(safeMessage)
                    || normalizedMessage.includes('no backtest jobs were found')
                    || normalizedMessage.includes('latest workspace backtest job could not be loaded')
                )
            ) {
                await runInlineBacktestFallback({
                    requestPayload,
                    startedAtPerf,
                    finalChartPayload,
                    strategyPayload,
                    strategiesPayload,
                    backtestPayload,
                    missingJobId: jobId,
                })
                return
            }

            throw error
        }
    }, [
        authHeaders,
        backtest,
        clearBacktestPollingState,
        fetchLatestBacktestJobSnapshot,
        handleBacktestFailure,
        hydrateCompletedBacktestResult,
        onBacktestStatusChange,
        onSharedConsoleJobChange,
        runInlineBacktestFallback,
    ])

    useEffect(() => () => {
        runAbortControllerRef.current?.abort()
        if (backtestPollTimeoutRef.current) {
            clearTimeout(backtestPollTimeoutRef.current)
            backtestPollTimeoutRef.current = null
        }
    }, [])

    useEffect(() => {
        if (lastBacktestResponse?.summary_only || typeof setStrategySetEntries !== 'function') {
            return
        }
        const requestEntries = normalizeStrategySetEntries(lastBacktestResponse?.request?.strategies || [])
        if (!requestEntries.length) {
            return
        }
        setStrategySetEntries((current) => {
            const currentEntries = normalizeStrategySetEntries(current)
            return currentEntries.length > 0 ? currentEntries : requestEntries
        })
    }, [lastBacktestResponse?.summary_only, lastBacktestResponse?.request?.strategies, setStrategySetEntries])

    useEffect(() => {
        const sharedJobId = String(sharedBacktestJob?.jobId || '').trim()
        const sharedStatus = String(sharedBacktestJob?.status || '').trim().toLowerCase()
        if (sharedStatus !== 'running' || !sharedJobId) {
            return
        }
        if (backtestJobIdRef.current === sharedJobId || runAbortControllerRef.current) {
            return
        }

        let cancelled = false
        const startedAtIso = String(sharedBacktestJob?.startedAt || '').trim() || new Date().toISOString()

        void fetchLatestBacktestJobSnapshot({ preferJobId: sharedJobId })
            .then((job) => {
                if (cancelled) {
                    return
                }

                const jobStatus = String(job?.status || '').trim().toLowerCase()
                if (jobStatus === 'completed' && job?.result?.status === 'ok') {
                    hydrateCompletedBacktestResult(job.result)
                    onBacktestStatusChange?.({
                        backtestError: '',
                        resultsError: '',
                        strategyPending: false,
                        backtestBusy: false,
                        backtestPending: false,
                        resultsPending: false,
                    })
                    onSharedConsoleJobChange?.('backtest', null)
                    clearBacktestPollingState()
                    logEventRef.current?.(`Backtester · Cleared stale running status after recovering completed job ${sharedJobId} from workspace storage.`)
                    return
                }

                if (jobStatus === 'failed') {
                    handleBacktestFailure(
                        new Error(String(job?.error || job?.detail || 'Failed to run backtest.')),
                        { resumeOnly: true },
                    )
                    return
                }

                if (jobStatus === 'cancelled') {
                    onSharedConsoleJobChange?.('backtest', null)
                    clearBacktestPollingState()
                    onBacktestStatusChange?.({
                        strategyPending: false,
                        backtestBusy: false,
                        backtestPending: false,
                        resultsPending: false,
                    })
                    logEventRef.current?.('Backtest run canceled.')
                    return
                }

                const abortController = new AbortController()
                runAbortControllerRef.current = abortController
                backtestJobIdRef.current = sharedJobId
                setIsRunning(true)
                logEventRef.current?.(`Backtester · Resumed tracking for active job ${sharedJobId}.`)
                pollBacktestJob({
                    jobId: sharedJobId,
                    abortController,
                    startedAtIso,
                    resumeOnly: true,
                }).catch((error) => handleBacktestFailure(error, { resumeOnly: true }))
            })
            .catch(() => {
                if (cancelled) {
                    return
                }

                const abortController = new AbortController()
                runAbortControllerRef.current = abortController
                backtestJobIdRef.current = sharedJobId
                setIsRunning(true)
                logEventRef.current?.(`Backtester · Resumed tracking for active job ${sharedJobId}.`)
                pollBacktestJob({
                    jobId: sharedJobId,
                    abortController,
                    startedAtIso,
                    resumeOnly: true,
                }).catch((error) => handleBacktestFailure(error, { resumeOnly: true }))
            })

        return () => {
            cancelled = true
        }
    }, [
        clearBacktestPollingState,
        fetchLatestBacktestJobSnapshot,
        handleBacktestFailure,
        hydrateCompletedBacktestResult,
        onBacktestStatusChange,
        onSharedConsoleJobChange,
        pollBacktestJob,
        sharedBacktestJob?.jobId,
        sharedBacktestJob?.startedAt,
        sharedBacktestJob?.status,
    ])

    useEffect(() => {
        if (!isActive || isGuest || !authToken) {
            return
        }
        if (isBacktestRunning || lastBacktestResponse?.stats) {
            return
        }

        const recoveryKey = [
            String(backtest?.symbol || ''),
            String(backtest?.timeframe || ''),
            String(backtest?.historyScopeMode || ''),
            String(backtest?.historyScopeBars || ''),
        ].join('|')
        if (latestCompletedRecoveryAttemptRef.current === recoveryKey) {
            return
        }
        latestCompletedRecoveryAttemptRef.current = recoveryKey

        void fetchLatestBacktestJobSnapshot({ status: 'completed' })
            .then((job) => {
                const jobStatus = String(job?.status || '').trim().toLowerCase()
                const finishedAtMs = Number(job?.finished_at || 0) * 1000
                if (jobStatus !== 'completed' || job?.result?.status !== 'ok') {
                    return
                }
                if (Number.isFinite(finishedAtMs) && finishedAtMs > 0) {
                    if ((Date.now() - finishedAtMs) > BACKTEST_LATEST_RECOVERY_MAX_AGE_MS) {
                        return
                    }
                }
                hydrateCompletedBacktestResult(job.result)
                onBacktestStatusChange?.({
                    backtestError: '',
                    resultsError: '',
                    strategyPending: false,
                    backtestBusy: false,
                    backtestPending: false,
                    resultsPending: false,
                })
                logEventRef.current?.(`Backtester · Recovered latest completed job ${job?.id || ''} from workspace storage.`)
            })
            .catch(() => {})
    }, [
        authToken,
        backtest?.historyScopeBars,
        backtest?.historyScopeMode,
        backtest?.symbol,
        backtest?.timeframe,
        fetchLatestBacktestJobSnapshot,
        hydrateCompletedBacktestResult,
        isActive,
        isBacktestRunning,
        isGuest,
        lastBacktestResponse?.stats,
        onBacktestStatusChange,
    ])

    function handleCancelBacktest() {
        if (isGuest) {
            logEventRef.current?.('Backtester · Guest demo cannot control running backtest jobs.')
            return
        }
        const activeJobId = String(backtestJobIdRef.current || '').trim()
        if (activeJobId) {
            fetch(buildApiUrl(`/strategy/backtest-jobs/${encodeURIComponent(activeJobId)}/cancel`), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
            }).catch(() => {})
        }
        runAbortControllerRef.current?.abort()
        clearBacktestPollingState()
        onSharedConsoleJobChange?.('backtest', null)
        onBacktestStatusChange?.({
            strategyPending: false,
            backtestBusy: false,
            backtestPending: false,
            resultsPending: false,
        })
        logEventRef.current?.('Backtest run canceled.')
    }

    function syncStrategySetPriorities(entries) {
        return (entries || []).map((entry, index) => ({
            ...entry,
            priority: index,
        }))
    }

    function scopeImportedStrategyEntriesForExplicitPortfolio(entries = []) {
        return entries.map((entry, index) => ({
            ...entry,
            portfolioId: String(entry?.portfolioId || 'adhoc-imports').trim() || 'adhoc-imports',
            portfolioLabel: String(entry?.portfolioLabel || 'Ad hoc imports').trim() || 'Ad hoc imports',
            pipelineId: String(entry?.pipelineId || 'adhoc-imports-main').trim() || 'adhoc-imports-main',
            pipelineLabel: String(entry?.pipelineLabel || 'Imported strategies').trim() || 'Imported strategies',
            portfolioMode: String(entry?.portfolioMode || backtest?.portfolioMode || BACKTEST_DEFAULTS.portfolioMode).trim().toLowerCase() || BACKTEST_DEFAULTS.portfolioMode,
            priority: index,
        }))
    }

    function handleAddSavedStrategyFromLibrary() {
        const selected = strategyLibraryItems.find((entry) => String(entry?.id) === String(selectedStrategyLibraryId))
        if (!selected?.strategy || typeof selected.strategy !== 'object') {
            logEventRef.current?.('Backtester · Select a saved strategy from the library first.')
            return
        }

        const benchmarkMarket = readBenchmarkPrimaryMarketContext(selected)
        const fallbackSymbol = String(benchmarkMarket.symbol || backtest?.symbol || chartSettings?.symbol || BACKTEST_DEFAULTS.symbol).trim().toUpperCase() || BACKTEST_DEFAULTS.symbol
        const fallbackTimeframe = String(benchmarkMarket.timeframe || backtest?.timeframe || chartSettings?.timeframe || BACKTEST_DEFAULTS.timeframe).trim().toUpperCase() || BACKTEST_DEFAULTS.timeframe
        const shouldAlignBacktestMarket = strategySetEntries.length === 0 && Boolean(benchmarkMarket.symbol && benchmarkMarket.timeframe)
        applyStrategySetEntriesUpdate((current) => {
            const importedEntries = buildStrategyEntriesFromBenchmark(selected, {
                fallbackSymbol,
                fallbackTimeframe,
                startIndex: current.length,
            })
            const scopedEntries = hasExplicitPortfolioBacktestConfig(backtest)
                ? scopeImportedStrategyEntriesForExplicitPortfolio(importedEntries)
                : importedEntries
            return syncStrategySetPriorities([
                ...current,
                ...scopedEntries,
            ])
        }, {
            forceExplicit: hasExplicitPortfolioBacktestConfig(backtest),
        })

        if (shouldAlignBacktestMarket) {
            setBacktest((current) => ({
                ...current,
                symbol: fallbackSymbol,
                timeframe: fallbackTimeframe,
            }))
            logEventRef.current?.(`Backtester · Aligned backtest market to ${fallbackSymbol} ${fallbackTimeframe} from the saved strategy.`)
        }

        const extraCount = Array.isArray(selected?.strategies) ? selected.strategies.filter((entry) => entry?.strategy && typeof entry.strategy === 'object').length : 0
        logEventRef.current?.(
            extraCount > 0
                ? `Backtester · Added "${selected.label || 'saved strategy'}" and ${extraCount} saved companion ${extraCount === 1 ? 'entry' : 'entries'} to the portfolio stack.`
                : `Backtester · Added "${selected.label || 'saved strategy'}" to the portfolio stack.`
        )
    }

    function handleAddSavedPortfolioFromLibrary() {
        const selected = portfolioLibraryItems.find((entry) => String(entry?.id) === String(selectedPortfolioLibraryId))
        if (!selected?.portfolio || typeof selected.portfolio !== 'object') {
            logEventRef.current?.('Backtester · Select a saved portfolio from the library first.')
            return
        }
        const instantiated = instantiateSavedPortfolioForBacktest(selected, {
            existingPortfolioIds: Array.isArray(backtest?.portfolios)
                ? backtest.portfolios.map((entry) => String(entry?.id || '').trim()).filter(Boolean)
                : [],
        })
        const nextPortfolios = [
            ...(hasExplicitPortfolioBacktestConfig(backtest)
                ? cloneSerializable(backtest.portfolios, [])
                : (strategySetEntries.length
                    ? rebuildBacktestPortfoliosFromEntries(
                        strategySetEntries,
                        [],
                        String(backtest?.portfolioMode || BACKTEST_DEFAULTS.portfolioMode).trim().toLowerCase() || BACKTEST_DEFAULTS.portfolioMode,
                    )
                    : [])),
            instantiated,
        ]
        setBacktest((current) => ({
            ...current,
            portfolioStructureVersion: 2,
            portfolios: nextPortfolios,
        }))
        setStrategySetEntries(extractStrategyEntriesFromBacktestPortfolios(nextPortfolios))
        logEventRef.current?.(`Backtester · Added portfolio "${selected.label || 'saved portfolio'}" to the backtest stack.`)
    }

    function handleClearStrategySet() {
        setStrategySetEntries([])
        if (hasExplicitPortfolioBacktestConfig(backtest)) {
            setBacktest((current) => ({
                ...current,
                portfolioStructureVersion: 2,
                portfolios: [],
            }))
        }
        logEventRef.current?.('Backtester · Cleared portfolio strategy stack.')
    }

    function handleRemoveStrategyEntry(entryId) {
        applyStrategySetEntriesUpdate(
            (current) => syncStrategySetPriorities(current.filter((entry) => entry.id !== entryId)),
            {
                forceExplicit: hasExplicitPortfolioBacktestConfig(backtest),
            },
        )
    }

    function handleLoadStrategyEntryIndicators(entry) {
        if (!entry?.strategy || typeof onLoadStrategyIndicators !== 'function') {
            return
        }

        void onLoadStrategyIndicators(entry.strategy, {
            label: entry.label || 'Strategy',
        })
    }

    function handleToggleStrategyEntry(entryId) {
        applyStrategySetEntriesUpdate((current) => current.map((entry) => (
            entry.id === entryId
                ? { ...entry, enabled: entry.enabled === false }
                : entry
        )), {
            forceExplicit: hasExplicitPortfolioBacktestConfig(backtest),
        })
    }

    function handleMoveStrategyEntry(entryId, direction) {
        applyStrategySetEntriesUpdate((current) => {
            const index = current.findIndex((entry) => entry.id === entryId)
            if (index < 0) {
                return current
            }
            const nextIndex = direction === 'up' ? index - 1 : index + 1
            if (nextIndex < 0 || nextIndex >= current.length) {
                return current
            }
            const next = current.slice()
            const [entry] = next.splice(index, 1)
            next.splice(nextIndex, 0, entry)
            return syncStrategySetPriorities(next)
        }, {
            forceExplicit: hasExplicitPortfolioBacktestConfig(backtest),
        })
    }

    function handleRenameStrategyEntry(entryId, label) {
        applyStrategySetEntriesUpdate((current) => current.map((entry) => (
            entry.id === entryId
                ? { ...entry, label }
                : entry
        )), {
            forceExplicit: hasExplicitPortfolioBacktestConfig(backtest),
        })
    }

    function handleUpdateStrategyEntryMarket(entryId, field, value) {
        const normalizedValue = normalizeStrategyEntryMarketValue(value)
        applyStrategySetEntriesUpdate((current) => current.map((entry) => (
            entry.id === entryId
                ? { ...entry, [field]: normalizedValue }
                : entry
        )), {
            forceExplicit: hasExplicitPortfolioBacktestConfig(backtest),
        })
    }

    async function handleRunBacktest() {
        if (isBusy || isBacktestRunning) {
            return
        }
        if (isGuest) {
            logEventRef.current?.('Backtester · Guest demo can inspect this setup, but running backtests is disabled.')
            onBacktestStatusChange?.({
                backtestError: '',
                resultsError: '',
                backtestBusy: false,
                backtestPending: false,
                resultsPending: false,
            })
            return
        }
        if (activeStrategyEntriesCount <= 0) {
            logEventRef.current?.('Backtest run blocked: add at least one enabled strategy to the portfolio stack first.')
            return
        }

        latestCompletedRecoveryAttemptRef.current = ''
        setIsRunning(true)
        try {
            const abortController = new AbortController()
            runAbortControllerRef.current = abortController
            const startedAt = performance.now()
            onSharedConsoleJobChange?.('backtest', {
                status: 'running',
                label: 'Running backtest',
                startedAt: new Date().toISOString(),
                actor: 'backtester',
            })
            onBacktestStatusChange?.({
                backtestError: '',
                resultsError: '',
                strategyPending: false,
                backtestBusy: true,
                backtestPending: true,
                resultsPending: true,
            })
            logEventRef.current?.('Backtest run started.')
            const portfolioExecutableEntries = hasExplicitPortfolioBacktestConfig(backtest)
                ? extractStrategyEntriesFromBacktestPortfolios(backtest?.portfolios)
                : []
            const effectiveStrategyEntries = portfolioExecutableEntries.length
                ? portfolioExecutableEntries
                : strategySetEntries
            const { executableEntries, primaryStrategy, companionEntries } = buildBacktestPipelineContext(effectiveStrategyEntries)
            if (!executableEntries.length) {
                throw new Error('Backtest run requires at least one pipeline strategy.')
            }
            onBacktestRunStarted?.()
            const normalizedChartSettings = normalizeChartSettings(chartSettings)
            const finalChartPayload = resolveBacktestMarketChartSettings(
                normalizedChartSettings,
                backtest,
                primaryStrategy,
                companionEntries,
            )
            const resolvedPrimaryStrategy = resolveStrategyAliasesInStrategy(primaryStrategy, finalChartPayload)
            const resolvedStrategyEntries = executableEntries.map((entry, index) => ({
                ...entry,
                priority: index,
                strategy: resolveStrategyAliasesInStrategy(entry.strategy, finalChartPayload),
            }))
            const requestedChartBars = resolveBacktestRequestBars(normalizedChartSettings, backtest)
            const requestPayload = {
                symbol: finalChartPayload.symbol,
                timeframe: finalChartPayload.timeframe,
                bars: requestedChartBars || Math.max(1, Number(finalChartPayload.bars) || 1000),
                indicators: buildBackendIndicatorsPayload(finalChartPayload.indicators),
                strategy: resolvedPrimaryStrategy,
                strategies: resolvedStrategyEntries.map((entry, index) => ({
                    ...entry,
                    id: entry.id,
                    label: entry.label || buildStrategyEntryLabel(entry.strategy, index),
                    priority: index,
                    enabled: entry.enabled !== false,
                    symbol: normalizeStrategyEntryMarketValue(entry.symbol),
                    timeframe: normalizeStrategyEntryMarketValue(entry.timeframe),
                    allocationMode: entry.allocationMode || 'fixed_volume',
                    allocationValue: entry.allocationValue ?? null,
                    strategy: entry.strategy,
                })),
                portfolioStructureVersion: normalizeBacktestPortfolioStructureVersion(backtest?.portfolioStructureVersion),
                capitalModel: backtest?.capitalModel && typeof backtest.capitalModel === 'object'
                    ? cloneSerializable(backtest.capitalModel, null)
                    : null,
                portfolios: Array.isArray(backtest?.portfolios)
                    ? cloneSerializable(backtest.portfolios, [])
                    : [],
                backtest: normalizeBacktestPayload(),
            }

            const response = await fetchWithServerRetry(buildApiUrl('/strategy/backtest-jobs'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                signal: abortController.signal,
                body: JSON.stringify(requestPayload),
            }, {
                attempts: 4,
                retryDelayMs: 900,
            })

            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                onBacktestStatusChange?.({
                    backtestError: extractApiErrorMessage(data, 'Failed to run backtest.'),
                    resultsError: extractApiErrorMessage(data, 'Failed to run backtest.'),
                })
                logEventRef.current?.(`Backtest run failed: ${extractApiErrorMessage(data, 'Failed to run backtest.')}`)
                return
            }
            const jobId = String(data?.job?.id || '').trim()
            if (!jobId) {
                throw new Error('Backtest job was created without an id.')
            }
            backtestJobIdRef.current = jobId
            onSharedConsoleJobChange?.('backtest', {
                status: 'running',
                label: 'Backtest job queued.',
                startedAt: new Date().toISOString(),
                actor: 'backtester',
                jobId,
            })

            await pollBacktestJob({
                jobId,
                abortController,
                startedAtIso: new Date().toISOString(),
                startedAtPerf: startedAt,
                requestedChartBars,
                finalChartPayload,
                strategyPayload: resolvedPrimaryStrategy,
                strategiesPayload: resolvedStrategyEntries,
                backtestPayload: backtest,
                requestPayload,
            })
        } catch (error) {
            if (error?.name === 'AbortError') {
                onBacktestStatusChange?.({
                    strategyPending: false,
                    backtestBusy: false,
                    backtestPending: false,
                    resultsPending: false,
                })
                onSharedConsoleJobChange?.('backtest', null)
                clearBacktestPollingState()
                return
            }
            handleBacktestFailure(error)
        } finally {
            if (!backtestJobIdRef.current) {
                setIsRunning(false)
            }
        }
    }

    return (
        <div className={`Backtester ${isActive ? 'active' : ''}`}>
            <div className='backtesterPanelToolbar'>
                <div className='backtesterPanelTabs'>
                    <button
                        type='button'
                        className={`backtesterPanelTab ${activeTab === 'strategy_pipe' ? 'active' : ''}`}
                        onClick={() => setActiveTab('strategy_pipe')}
                    >
                        <span className='backtesterPanelTabIcon strategy' aria-hidden='true'>P</span>
                        <span>Strategy Pipe</span>
                    </button>

                    <button
                        type='button'
                        className={`backtesterPanelTab ${activeTab === 'capital' ? 'active' : ''}`}
                        onClick={() => setActiveTab('capital')}
                    >
                        <span className='backtesterPanelTabIcon capital' aria-hidden='true'>$</span>
                        <span>Capital</span>
                    </button>

                    <button
                        type='button'
                        className={`backtesterPanelTab ${activeTab === 'costs' ? 'active' : ''}`}
                        onClick={() => setActiveTab('costs')}
                    >
                        <span className='backtesterPanelTabIcon costs' aria-hidden='true'>%</span>
                        <span>Costs</span>
                    </button>

                    <button
                        type='button'
                        className={`backtesterPanelTab ${activeTab === 'execution' ? 'active' : ''}`}
                        onClick={() => setActiveTab('execution')}
                    >
                        <span className='backtesterPanelTabIcon execution' aria-hidden='true'>E</span>
                        <span>Execution</span>
                    </button>
                </div>

                <div className='backtesterActions'>
                    {isStale ? (
                        <div className='backtesterStaleBadge' title='Backtest settings changed since the last completed run.'>
                            Outdated run
                        </div>
                    ) : null}
                    <button
                        type='button'
                        className='backtesterToolbarButton'
                        onClick={() => onLoadBacktestFlags?.()}
                        disabled={isBusy || isBacktestRunning || !hasBacktestChartBuffer}
                    >
                        Load flags into chart
                    </button>
                    <button
                        type='button'
                        className='backtesterToolbarButton'
                        onClick={resetAllFields}
                        disabled={isBusy || isBacktestRunning}
                    >
                        Reset all
                    </button>
                    {isBacktestRunning ? (
                        <button
                            type='button'
                            className='backtesterToolbarButton'
                            onClick={handleCancelBacktest}
                            disabled={isGuest}
                            title={isGuest ? guestRestrictionMessage : undefined}
                        >
                            Cancel
                        </button>
                    ) : null}
                        <button
                            type='button'
                            className='backtesterToolbarButton isActive'
                            onClick={handleRunBacktest}
                            disabled={isGuest || isBusy || isBacktestRunning || activeStrategyEntriesCount <= 0}
                            title={isGuest ? guestRestrictionMessage : undefined}
                        >
                        {isBusy || isBacktestRunning
                            ? 'Running...'
                            : (lastBacktestResponse?.request?.backtest ? 'Re-run backtest' : 'Run backtest')}
                    </button>
                </div>
            </div>

            {isGuest ? (
                <div className='backtesterGuestNotice' role='status'>
                    {guestRestrictionMessage}
                </div>
            ) : null}

            {isBacktestRunning ? (
                <div className='backtesterJobStatus' role='status' aria-live='polite'>
                    <strong>{activeBacktestLabel}</strong>
                    <span>
                        {activeBacktestJobId
                            ? `Job ${activeBacktestJobId}`
                            : 'Preparing job tracking...'}
                    </span>
                </div>
            ) : null}

            <DiscreetProgressBar
                active={isBacktestRunning}
            />

            {activeTab === 'strategy_pipe' ? (
                <div className='backtesterStrategyPipeShell'>
                    <aside className='backtesterStrategyPipeSidebar'>
                        <div className='backtesterStrategyPipeSidebarHeader'>
                            <h3>{librarySourceTab === 'portfolios' ? 'Saved portfolios' : 'Saved strategies'}</h3>
                            <p>
                                {librarySourceTab === 'portfolios'
                                    ? 'Load portfolio bundles directly into the backtest stack.'
                                    : 'Select directly from the saved strategy library and assemble the backtest pipeline here.'}
                            </p>
                        </div>

                        <div className='backtesterStrategyPipeToolbar'>
                            <div className='backtesterStrategyPipeListTabs'>
                                <button
                                    type='button'
                                    className={librarySourceTab === 'strategies' ? 'active' : ''}
                                    onClick={() => setLibrarySourceTab('strategies')}
                                >
                                    Strategies
                                </button>
                                <button
                                    type='button'
                                    className={librarySourceTab === 'portfolios' ? 'active' : ''}
                                    onClick={() => setLibrarySourceTab('portfolios')}
                                >
                                    Portfolios
                                </button>
                            </div>
                            <div className='backtesterStrategyPipeListTabs'>
                                <button
                                    type='button'
                                    className={strategyLibraryListTab === 'all' ? 'active' : ''}
                                    onClick={() => setStrategyLibraryListTab('all')}
                                >
                                    All
                                </button>
                                <button
                                    type='button'
                                    className={strategyLibraryListTab === 'favorites' ? 'active' : ''}
                                    onClick={() => setStrategyLibraryListTab('favorites')}
                                >
                                    Favorites
                                </button>
                            </div>
                            <button
                                type='button'
                                className='backtesterToolbarButton backtesterStrategyPipeRefreshButton'
                                onClick={() => void (librarySourceTab === 'portfolios'
                                    ? refreshPortfolioLibrary({ quiet: false })
                                    : refreshStrategyLibrary({ quiet: false }))}
                                disabled={(librarySourceTab === 'portfolios' ? isPortfolioLibraryLoading : isStrategyLibraryLoading) || isBusy || isBacktestRunning}
                            >
                                {(librarySourceTab === 'portfolios' ? isPortfolioLibraryLoading : isStrategyLibraryLoading) ? 'Refreshing...' : 'Refresh'}
                            </button>
                        </div>

                        <div className='backtesterStrategyPipeSearchRow'>
                            <input
                                type='text'
                                className='backtesterStrategyLibraryFilter'
                                value={strategyLibraryQuery}
                                onChange={(event) => setStrategyLibraryQuery(event.target.value)}
                                placeholder={librarySourceTab === 'portfolios' ? 'Filter saved portfolios' : 'Filter saved strategies'}
                                aria-label={librarySourceTab === 'portfolios' ? 'Filter saved portfolios' : 'Filter saved strategies'}
                                disabled={isBusy || isBacktestRunning}
                            />
                            {strategyLibraryQuery ? (
                                <button
                                    type='button'
                                    className='backtesterStrategyPipeSearchClear'
                                    onClick={() => setStrategyLibraryQuery('')}
                                    disabled={isBusy || isBacktestRunning}
                                >
                                    Clear
                                </button>
                            ) : null}
                        </div>

                        <div className='backtesterStrategyPipeList'>
                            {activeLibraryError && !(librarySourceTab === 'portfolios' ? portfolioLibraryItems.length : strategyLibraryItems.length) ? (
                                <div className='backtesterStrategySetEmpty backtesterStrategySetError'>
                                    {activeLibraryError}
                                </div>
                            ) : (librarySourceTab === 'portfolios' ? !portfolioLibraryItems.length : !strategyLibraryItems.length) ? (
                                <div className='backtesterStrategySetEmpty'>
                                    {librarySourceTab === 'portfolios' ? 'No saved portfolios yet.' : 'No saved strategies yet.'}
                                </div>
                            ) : normalizedStrategyLibraryQuery && !(librarySourceTab === 'portfolios' ? visiblePortfolioLibraryItems.length : visibleStrategyLibraryItems.length) ? (
                                <div className='backtesterStrategySetEmpty'>
                                    {librarySourceTab === 'portfolios' ? 'No saved portfolios match this filter.' : 'No saved strategies match this filter.'}
                                </div>
                            ) : !(librarySourceTab === 'portfolios' ? visiblePortfolioLibraryItems.length : visibleStrategyLibraryItems.length) ? (
                                <div className='backtesterStrategySetEmpty'>
                                    {librarySourceTab === 'portfolios' ? 'No favorite portfolios yet.' : 'No favorite strategies yet.'}
                                </div>
                            ) : (librarySourceTab === 'portfolios' ? visiblePortfolioLibraryItems : visibleStrategyLibraryItems).map((entry) => (
                                <div key={entry.id} className='backtesterStrategyPipeListEntry'>
                                    <button
                                        type='button'
                                        className={`backtesterStrategyPipeListSelect ${String(librarySourceTab === 'portfolios' ? selectedPortfolioLibraryId : selectedStrategyLibraryId) === String(entry.id) ? 'active' : ''}`.trim()}
                                        onClick={() => (
                                            librarySourceTab === 'portfolios'
                                                ? setSelectedPortfolioLibraryId(String(entry.id))
                                                : setSelectedStrategyLibraryId(String(entry.id))
                                        )}
                                    >
                                        <div className='backtesterStrategyPipeEntryHeader'>
                                            <strong className='backtesterStrategyPipeEntryLabel'>
                                                {entry.is_favorite ? <span className='backtesterStrategyPipeFavoriteStar' aria-hidden='true'>★</span> : null}
                                                <span>{entry.label || `${librarySourceTab === 'portfolios' ? 'Portfolio' : 'Strategy'} #${entry.id}`}</span>
                                            </strong>
                                            {entry.is_favorite ? <span className='backtesterStrategyPipeFavoriteBadge'>Favorite</span> : null}
                                        </div>
                                        {librarySourceTab === 'portfolios' ? (
                                            (() => {
                                                const summary = summarizeSavedPortfolio(entry)
                                                return (
                                                    <>
                                                        <span>{entry.source || 'portfolio bundle'} · {summary.pipelineCount} pipeline(s)</span>
                                                        <small>{summary.entryCount} strategy entries · {entry.notes || 'No notes provided.'}</small>
                                                    </>
                                                )
                                            })()
                                        ) : (
                                            <>
                                                <span>{entry.source || 'manual'}{entry.side ? ` · ${entry.side}` : ''}</span>
                                                <small>{entry.notes || 'No notes provided.'}</small>
                                            </>
                                        )}
                                    </button>
                                    <button
                                        type='button'
                                        className={`backtesterStrategyPipeFavoriteToggle ${entry.is_favorite ? 'active' : ''}`.trim()}
                                        onClick={() => void (librarySourceTab === 'portfolios' ? handleToggleFavoritePortfolioInLibrary(entry) : handleToggleFavoriteStrategyInLibrary(entry))}
                                        title={isGuest ? guestRestrictionMessage : (entry.is_favorite ? 'Remove from favorites' : 'Add to favorites')}
                                        aria-label={entry.is_favorite ? `Remove ${entry.label || `${librarySourceTab === 'portfolios' ? 'Portfolio' : 'Strategy'} #${entry.id}`} from favorites` : `Add ${entry.label || `${librarySourceTab === 'portfolios' ? 'Portfolio' : 'Strategy'} #${entry.id}`} to favorites`}
                                        disabled={isGuest}
                                    >
                                        ★
                                    </button>
                                </div>
                            ))}
                        </div>
                    </aside>

                    <section className='backtesterStrategyPipeContent'>
                        <div className='backtesterStrategyPipeSelectionCard'>
                            {(librarySourceTab === 'portfolios' ? selectedPortfolioLibraryItem : selectedStrategyLibraryItem) ? (
                                <>
                                    <div className='backtesterStrategyPipeSelectionHeader'>
                                        <div>
                                            <h3>
                                                {librarySourceTab === 'portfolios'
                                                    ? (selectedPortfolioLibraryItem?.label || `Portfolio #${selectedPortfolioLibraryItem?.id}`)
                                                    : (selectedStrategyLibraryItem?.label || `Strategy #${selectedStrategyLibraryItem?.id}`)}
                                            </h3>
                                            <p>
                                                {librarySourceTab === 'portfolios'
                                                    ? (selectedPortfolioLibraryItem?.notes || 'No notes provided for this saved portfolio.')
                                                    : (selectedStrategyLibraryItem?.notes || 'No notes provided for this saved strategy.')}
                                            </p>
                                        </div>
                                        <div className='backtesterStrategyPipeSelectionMeta'>
                                            {librarySourceTab === 'portfolios' ? (
                                                <>
                                                    <span>{selectedPortfolioLibraryItem?.source || 'portfolio bundle'}</span>
                                                    <span>{summarizeSavedPortfolio(selectedPortfolioLibraryItem).pipelineCount} pipelines</span>
                                                    <span>{summarizeSavedPortfolio(selectedPortfolioLibraryItem).entryCount} strategy entries</span>
                                                </>
                                            ) : (
                                                <>
                                                    <span>{selectedStrategyLibraryItem.source || 'manual'}</span>
                                                    <span>{selectedStrategyLibraryItem.side || 'unspecified side'}</span>
                                                    <span>{Array.isArray(selectedStrategyLibraryItem?.strategies) ? selectedStrategyLibraryItem.strategies.length : 0} companion entries</span>
                                                </>
                                            )}
                                        </div>
                                    </div>
                                    <div className='backtesterStrategyPipeSelectionActions'>
                                        <button
                                            type='button'
                                            className='backtesterToolbarButton isActive'
                                            onClick={librarySourceTab === 'portfolios' ? handleAddSavedPortfolioFromLibrary : handleAddSavedStrategyFromLibrary}
                                            disabled={isBusy || isBacktestRunning || (librarySourceTab === 'portfolios' ? isPortfolioLibraryLoading : isStrategyLibraryLoading) || !(librarySourceTab === 'portfolios' ? selectedPortfolioLibraryItem : selectedStrategyLibraryItem)}
                                        >
                                            {librarySourceTab === 'portfolios' ? 'Include selected portfolio' : 'Include selected in pipeline'}
                                        </button>
                                        <button
                                            type='button'
                                            className='backtesterToolbarButton'
                                            onClick={handleClearStrategySet}
                                            disabled={isBusy || isBacktestRunning || strategySetEntries.length === 0}
                                        >
                                            Clear pipeline
                                        </button>
                                    </div>
                                </>
                            ) : (
                                <div className='backtesterStrategySetEmpty'>
                                    {librarySourceTab === 'portfolios'
                                        ? 'Select a saved portfolio on the left to inspect it and include it in the backtest stack.'
                                        : 'Select a saved strategy on the left to inspect it and include it in the backtest pipeline.'}
                                </div>
                            )}
                        </div>

                        <div className='backtesterStrategySetCard'>
                            <div className='backtesterStrategySetHeader'>
                                <div>
                                    <h3>Current pipeline</h3>
                                    <p>Manage the strategy stack that the Backtester will execute together in the next run.</p>
                                </div>
                            </div>

                            <div className='backtesterStrategySetMeta'>
                                <span>{summarizeStrategySet(strategySetEntries)}</span>
                                <span>
                                    {String(backtest?.portfolioMode || BACKTEST_DEFAULTS.portfolioMode) === 'parallel_sleeves'
                                        ? 'Parallel sleeves allows same-symbol hedge-like coexistence and keeps different markets independent inside the stack.'
                                        : 'Shared pipe resolves conflicts inside each market group while different markets still run independently.'}
                                </span>
                            </div>

                            {strategySetEntries.length ? (
                                <div className='backtesterStrategySetList'>
                                    {strategySetEntries.map((entry, index) => (
                                        <div key={entry.id} className={`backtesterStrategySetRow ${entry.enabled === false ? 'isDisabled' : ''}`}>
                                            <div className='backtesterStrategySetRowMain'>
                                                <div className='backtesterStrategySetPriority'>#{index + 1}</div>
                                                <div className='backtesterStrategySetFields'>
                                                    <input
                                                        className='backtesterStrategySetLabel'
                                                        type='text'
                                                        value={entry.label}
                                                        onChange={(event) => handleRenameStrategyEntry(entry.id, event.target.value)}
                                                        disabled={isBusy || isBacktestRunning}
                                                    />
                                                    <input
                                                        className='backtesterStrategySetMarketInput'
                                                        type='text'
                                                        value={entry.symbol || ''}
                                                        placeholder={String(backtest?.symbol || chartSettings?.symbol || BACKTEST_DEFAULTS.symbol).trim().toUpperCase() || BACKTEST_DEFAULTS.symbol}
                                                        onChange={(event) => handleUpdateStrategyEntryMarket(entry.id, 'symbol', event.target.value)}
                                                        disabled={isBusy || isBacktestRunning}
                                                    />
                                                    <select
                                                        className='backtesterStrategySetMarketInput'
                                                        value={entry.timeframe || ''}
                                                        onChange={(event) => handleUpdateStrategyEntryMarket(entry.id, 'timeframe', event.target.value)}
                                                        disabled={isBusy || isBacktestRunning}
                                                    >
                                                        <option value=''>default tf</option>
                                                        {TIMEFRAME_OPTIONS.map((option) => (
                                                            <option key={option.value} value={option.value}>{option.label}</option>
                                                        ))}
                                                    </select>
                                                </div>
                                            </div>
                                            <div className='backtesterStrategySetRowActions'>
                                                <button type='button' className='backtesterToolbarButton' onClick={() => handleToggleStrategyEntry(entry.id)} disabled={isBusy || isBacktestRunning}>
                                                    {entry.enabled === false ? 'Enable' : 'Disable'}
                                                </button>
                                                <button type='button' className='backtesterToolbarButton' onClick={() => handleLoadStrategyEntryIndicators(entry)} disabled={isBusy || isBacktestRunning}>
                                                    Load indicators
                                                </button>
                                                <button type='button' className='backtesterToolbarButton' onClick={() => handleMoveStrategyEntry(entry.id, 'up')} disabled={isBusy || isBacktestRunning || index === 0}>
                                                    Up
                                                </button>
                                                <button type='button' className='backtesterToolbarButton' onClick={() => handleMoveStrategyEntry(entry.id, 'down')} disabled={isBusy || isBacktestRunning || index === strategySetEntries.length - 1}>
                                                    Down
                                                </button>
                                                <button type='button' className='backtesterToolbarButton' onClick={() => handleRemoveStrategyEntry(entry.id)} disabled={isBusy || isBacktestRunning}>
                                                    Remove
                                                </button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className='backtesterStrategySetEmpty'>
                                    Select a saved strategy on the left and include it here to build the pipeline for your next backtest run.
                                </div>
                            )}

                            {Array.isArray(lastBacktestResponse?.stats?.portfolio_strategy_stats) && lastBacktestResponse.stats.portfolio_strategy_stats.length ? (
                                <div className='backtesterPortfolioSummary'>
                                    <h4>Last portfolio breakdown</h4>
                                    <div className='backtesterPortfolioGrid'>
                                        {lastBacktestResponse.stats.portfolio_strategy_stats.map((item) => (
                                            <div key={item.strategy_id || item.strategy_label} className='backtesterPortfolioCard'>
                                                <strong>{item.strategy_label || item.strategy_id || 'Strategy'}</strong>
                                                <span>{item.symbol || '--'} · {item.timeframe || '--'}</span>
                                                <span>Trades: {Number(item.trades || 0)}</span>
                                                <span>Net PnL: {Number(item.net_pnl || 0).toFixed(2)}</span>
                                                <span>Win rate: {(Number(item.win_rate || 0) * 100).toFixed(1)}%</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            ) : null}
                        </div>
                    </section>
                </div>
            ) : (
                <BacktestConfigEditor
                    backtest={backtest}
                    setBacktest={setBacktest}
                    activeTab={activeTab}
                    setActiveTab={setActiveTab}
                    onLogEvent={onLogEvent}
                    chartSettings={chartSettings}
                    lazyChartBars={chartSettings?.bars}
                    loadedChartCandles={normalizedLoadedChartCandles}
                    isStale={isStale}
                    showPanelTabs={false}
                    showToolbarActions={false}
                    activeBrokerProfile={activeBrokerProfile}
                />
            )}
        </div>
    )
}

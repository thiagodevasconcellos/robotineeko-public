import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import { getStrategyAliasForColumnName, getStrategyTokenCandidates, resolveStrategyAliasesInStrategy } from '../../utils/strategyAliases.jsx'
import { buildStrategyBenchmarkPayload, buildStrategyCollectionChartSettings } from '../../utils/strategyLibrary.js'
import { ResearchScientificRecord } from './ResearchScientificRecord.jsx'
import { ResearchPositiveStrategiesPane } from './ResearchPositiveStrategiesPane.jsx'
import { ResearchWhatWorkedPane } from './ResearchWhatWorkedPane.jsx'
import {
    buildMarketRegimePresetModel,
    buildMarketRegimePresetRecommendation,
    buildStrategyFromMarketRegimePreset,
    formatPresetMetric,
} from '../../utils/marketRegimePresets.jsx'
import { BACKTEST_MARGIN_MODEL_DEFINITIONS } from './backtestCapitalModels.js'
import './Results.css'

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

function toFiniteNumber(value) {
    const number = Number(value)

    if (!Number.isFinite(number)) {
        return null
    }

    return number
}

function buildSeriesFromArray(values = []) {
    if (!Array.isArray(values)) {
        return []
    }

    return values
        .map((value, index) => {
            const y = toFiniteNumber(value)

            if (y === null) {
                return null
            }

            return { x: index, y }
        })
        .filter(Boolean)
}

function pickFirstDefined(source, keys = []) {
    for (const key of keys) {
        if (source?.[key] !== undefined && source?.[key] !== null) {
            return source[key]
        }
    }

    return undefined
}

function getCompareStrategyCount(entry) {
    return Math.max(
        1,
        Number(entry?.strategy_count || entry?.summary?.strategy_count || 1) || 1,
    )
}

function getPortfolioContributionPreview(entry) {
    const stats = Array.isArray(entry?.portfolio_strategy_stats)
        ? entry.portfolio_strategy_stats
        : Array.isArray(entry?.summary?.portfolio_strategy_stats)
            ? entry.summary.portfolio_strategy_stats
            : []

    return stats
        .slice()
        .sort((left, right) => (Number(right?.net_pnl || 0) - Number(left?.net_pnl || 0)))
        .slice(0, 2)
        .map((item) => {
            const label = String(item?.strategy_label || item?.strategy_id || 'Strategy').trim() || 'Strategy'
            return `${label}: ${formatPresetMetric(item?.net_pnl)}`
        })
        .join(' · ')
}

function buildSeriesFromResultsFields(results = [], fields = []) {
    if (!Array.isArray(results)) {
        return []
    }

    return results
        .map((row, index) => {
            const y = toFiniteNumber(pickFirstDefined(row, fields))

            if (y === null) {
                return null
            }

            return { x: index, y }
        })
        .filter(Boolean)
}

function useSourcedState(sourceValue) {
    const [draft, setDraft] = useState(() => ({
        sourceValue,
        value: sourceValue,
    }))
    const value = Object.is(draft.sourceValue, sourceValue) ? draft.value : sourceValue

    function setValue(nextValue) {
        setDraft((current) => {
            const baseValue = Object.is(current.sourceValue, sourceValue) ? current.value : sourceValue
            return {
                sourceValue,
                value: typeof nextValue === 'function' ? nextValue(baseValue) : nextValue,
            }
        })
    }

    return [value, setValue]
}

function normalizeExportFormatForTarget(target, format) {
    const safeTarget = String(target || 'clipboard')
    const safeFormat = String(format || 'json')

    if (safeTarget === 'open') {
        return 'html'
    }
    if (safeTarget === 'save' && !['html', 'csv'].includes(safeFormat)) {
        return 'html'
    }
    if (safeTarget === 'clipboard' && !['json', 'csv'].includes(safeFormat)) {
        return 'json'
    }
    return safeFormat
}

function getMinMax(points) {
    if (!points.length) {
        return {
            minX: 0,
            maxX: 1,
            minY: 0,
            maxY: 1,
        }
    }

    let minX = points[0].x
    let maxX = points[0].x
    let minY = points[0].y
    let maxY = points[0].y

    for (const point of points) {
        if (point.x < minX) minX = point.x
        if (point.x > maxX) maxX = point.x
        if (point.y < minY) minY = point.y
        if (point.y > maxY) maxY = point.y
    }

    if (minX === maxX) {
        maxX = minX + 1
    }

    if (minY === maxY) {
        maxY = minY + 1
    }

    return { minX, maxX, minY, maxY }
}

function buildLinePath(points, width, height, padding) {
    if (!points.length) {
        return ''
    }

    const { minX, maxX, minY, maxY } = getMinMax(points)
    const innerWidth = width - padding * 2
    const innerHeight = height - padding * 2

    return points
        .map((point, index) => {
            const x = padding + ((point.x - minX) / (maxX - minX)) * innerWidth
            const y = height - padding - ((point.y - minY) / (maxY - minY)) * innerHeight

            return `${index === 0 ? 'M' : 'L'} ${x} ${y}`
        })
        .join(' ')
}

function buildHorizontalGuide(value, minY, maxY, width, height, padding) {
    if (maxY === minY) {
        return null
    }

    const innerHeight = height - padding * 2
    const y = height - padding - ((value - minY) / (maxY - minY)) * innerHeight

    return `M ${padding} ${y} L ${width - padding} ${y}`
}

function formatValue(value, decimals = 6) {
    const number = toFiniteNumber(value)

    if (number === null) {
        return '-'
    }

    return number.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: decimals,
    })
}

function formatMoney(value) {
    const number = toFiniteNumber(value)

    if (number === null) {
        return '-'
    }

    return number.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })
}

function formatInteger(value) {
    const number = toFiniteNumber(value)

    if (number === null) {
        return '-'
    }

    return number.toLocaleString('en-US', {
        maximumFractionDigits: 0,
    })
}

function formatPercent(value, decimals = 2) {
    const number = toFiniteNumber(value)

    if (number === null) {
        return '-'
    }

    return `${(number * 100).toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    })}%`
}

function formatTextLabel(value) {
    const text = String(value || '').trim()

    if (!text) {
        return '-'
    }

    return text.replaceAll('_', ' ')
}

function formatMarginModelLabel(value) {
    const normalized = String(value || '').trim().toLowerCase()

    if (!normalized) {
        return '-'
    }

    return BACKTEST_MARGIN_MODEL_DEFINITIONS[normalized]?.label || formatTextLabel(normalized)
}

function formatSignedNumber(value, decimals = 2) {
    const number = toFiniteNumber(value)

    if (number === null) {
        return '-'
    }

    const signal = number > 0 ? '+' : ''

    return `${signal}${number.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    })}`
}

function formatSignedMoney(value) {
    return formatSignedNumber(value, 2)
}

function formatDateTime(value) {
    const number = toFiniteNumber(value)

    if (number === null || number <= 0) {
        return '-'
    }

    return new Date(number * 1000).toLocaleString()
}

function formatCadence(value) {
    const number = toFiniteNumber(value)

    if (number === null) {
        return '-'
    }

    return number.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })
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

function buildBacktestMarketWindow(response) {
    const safeResponse = response && typeof response === 'object' ? response : {}
    const safeRequest = safeResponse?.request && typeof safeResponse.request === 'object' ? safeResponse.request : {}
    const strategyViewMeta = safeResponse?.strategy_view_meta && typeof safeResponse.strategy_view_meta === 'object'
        ? safeResponse.strategy_view_meta
        : {}
    const persistedWindow = safeResponse?.market_window && typeof safeResponse.market_window === 'object'
        ? safeResponse.market_window
        : {}
    const timeframe = String(
        persistedWindow?.timeframe
        || safeRequest?.timeframe
        || strategyViewMeta?.timeframe
        || ''
    ).trim().toUpperCase()
    const timeframeMinutes = getTimeframeDurationMinutes(timeframe)
    const timeframeSeconds = timeframeMinutes !== null ? timeframeMinutes * 60 : null
    const requestBars = Math.max(
        0,
        Number(
            persistedWindow?.bars
            || safeRequest?.bars
            || strategyViewMeta?.bars
            || 0
        ) || 0,
    )

    const directFirstCandleTime = toFiniteNumber(persistedWindow?.first_candle_time)
    const directLastCandleTime = toFiniteNumber(persistedWindow?.last_candle_time)
    const directDurationSeconds = toFiniteNumber(persistedWindow?.inclusive_duration_seconds)

    if (directFirstCandleTime !== null && directLastCandleTime !== null) {
        return {
            firstCandleTime: directFirstCandleTime,
            lastCandleTime: directLastCandleTime,
            inclusiveDurationSeconds: directDurationSeconds !== null
                ? directDurationSeconds
                : Math.max(
                    0,
                    directLastCandleTime - directFirstCandleTime + Math.max(0, timeframeSeconds || 0),
                ),
            timeframe,
            timeframeMinutes,
            bars: requestBars,
        }
    }

    const resultRows = Array.isArray(safeResponse?.results) ? safeResponse.results : []
    const hasCompleteWindowRows = !safeResponse?.summary_only
        || resultRows.length === Math.max(0, Number(safeResponse?.rows || resultRows.length || 0))
    const candleTimes = hasCompleteWindowRows
        ? resultRows
            .map((row) => toFiniteNumber(row?.time))
            .filter((value) => value !== null)
        : []

    if (candleTimes.length > 0) {
        const firstCandleTime = Math.min(...candleTimes)
        const lastCandleTime = Math.max(...candleTimes)
        const inclusiveDurationSeconds = Math.max(
            0,
            lastCandleTime - firstCandleTime + Math.max(0, timeframeSeconds || 0),
        )

        return {
            firstCandleTime,
            lastCandleTime,
            inclusiveDurationSeconds,
            timeframe,
            timeframeMinutes,
            bars: requestBars,
        }
    }

    if (requestBars > 0 && timeframeSeconds !== null) {
        return {
            firstCandleTime: null,
            lastCandleTime: null,
            inclusiveDurationSeconds: requestBars * timeframeSeconds,
            timeframe,
            timeframeMinutes,
            bars: requestBars,
        }
    }

    return {
        firstCandleTime: null,
        lastCandleTime: null,
        inclusiveDurationSeconds: null,
        timeframe,
        timeframeMinutes,
        bars: requestBars,
    }
}

function buildTradeCadenceMetrics(response, stats) {
    const totalTrades = Math.max(0, Number(stats?.n_trades || 0) || 0)
    const marketWindow = buildBacktestMarketWindow(response)
    const durationSeconds = toFiniteNumber(marketWindow?.inclusiveDurationSeconds)

    if (durationSeconds === null || durationSeconds <= 0) {
        return {
            marketWindow,
            tradesPerDay: null,
            tradesPerWeek: null,
            tradesPerMonth: null,
        }
    }

    const durationDays = durationSeconds / 86400
    const tradesPerDay = totalTrades / durationDays

    return {
        marketWindow,
        tradesPerDay,
        tradesPerWeek: tradesPerDay * 7,
        tradesPerMonth: tradesPerDay * (365.25 / 12),
    }
}

function buildBacktestOperationsRows(response) {
    const safeResponse = response && typeof response === 'object' ? response : {}
    const safeRequest = safeResponse?.request && typeof safeResponse.request === 'object' ? safeResponse.request : {}
    const symbol = String(safeRequest?.symbol || '').trim().toUpperCase() || '-'
    const timeframe = String(safeRequest?.timeframe || '').trim().toUpperCase() || '-'
    const resultRows = Array.isArray(safeResponse?.results) ? safeResponse.results : []

    return resultRows
        .map((row, index) => {
            const safeRow = row && typeof row === 'object' ? row : {}
            const rowTimestamp = toFiniteNumber(safeRow?.time)
            const longExitFlag = Number(safeRow?.long_exit_flag || 0) === 1
            const shortExitFlag = Number(safeRow?.short_exit_flag || 0) === 1
            const longCloseTimestamp = toFiniteNumber(safeRow?.long_close_timestamp)
            const shortCloseTimestamp = toFiniteNumber(safeRow?.short_close_timestamp)
            const longOpenTimestamp = toFiniteNumber(safeRow?.long_open_timestamp)
            const shortOpenTimestamp = toFiniteNumber(safeRow?.short_open_timestamp)
            const orderType = String(safeRow?.order_type || '').trim().toLowerCase()
            const netPnl = toFiniteNumber(safeRow?.trade_net_pnl ?? safeRow?.realized_pnl)
            const grossPnl = toFiniteNumber(safeRow?.trade_gross_pnl)
            const cost = toFiniteNumber(safeRow?.trade_cost)
            const costBreakdown = Array.isArray(safeRow?.trade_cost_breakdown) ? safeRow.trade_cost_breakdown : []
            const isOpenOnlyEvent = orderType.startsWith('open_')
                && longCloseTimestamp === null
                && shortCloseTimestamp === null
                && !longExitFlag
                && !shortExitFlag
            const hasCompletedTradeSignal = !isOpenOnlyEvent && (
                longExitFlag
                || shortExitFlag
                || longCloseTimestamp !== null
                || shortCloseTimestamp !== null
                || (
                    !orderType.startsWith('open_')
                    && (
                        netPnl !== null
                        || grossPnl !== null
                        || cost !== null
                    )
                )
            )

            if (!hasCompletedTradeSignal) {
                return null
            }

            const side = longExitFlag
                || longOpenTimestamp !== null
                || longCloseTimestamp !== null
                || orderType === 'long'
                || orderType === 'buy'
                ? 'Long'
                : shortExitFlag
                    || shortOpenTimestamp !== null
                    || shortCloseTimestamp !== null
                    || orderType === 'short'
                    || orderType === 'sell'
                    ? 'Short'
                    : 'Trade'

            const isLong = side === 'Long'
            const entryTime = isLong
                ? (longOpenTimestamp ?? longCloseTimestamp ?? rowTimestamp)
                : (shortOpenTimestamp ?? shortCloseTimestamp ?? rowTimestamp)
            const exitTime = isLong
                ? (longCloseTimestamp ?? rowTimestamp)
                : (shortCloseTimestamp ?? rowTimestamp)
            const entryPrice = toFiniteNumber(isLong ? safeRow?.long_open_price : safeRow?.short_open_price)
            const exitPrice = toFiniteNumber(isLong ? safeRow?.long_close_price : safeRow?.short_close_price)

            return {
                id: `${symbol}-${timeframe}-${side}-${exitTime ?? entryTime ?? index}-${index}`,
                symbol,
                timeframe,
                side,
                entryTime,
                exitTime,
                entryPrice,
                exitPrice,
                grossPnl,
                netPnl,
                cost,
                costBreakdown,
            }
        })
        .filter(Boolean)
        .sort((left, right) => {
            const exitDiff = Number(left?.exitTime || 0) - Number(right?.exitTime || 0)
            if (exitDiff !== 0) {
                return exitDiff
            }
            const entryDiff = Number(left?.entryTime || 0) - Number(right?.entryTime || 0)
            if (entryDiff !== 0) {
                return entryDiff
            }
            return String(left?.id || '').localeCompare(String(right?.id || ''))
        })
}

function normalizeCostBreakdownItems(items = []) {
    if (!Array.isArray(items)) {
        return []
    }

    return items
        .map((item) => {
            if (!item || typeof item !== 'object') {
                return null
            }
            const amount = toFiniteNumber(item.amount)
            return {
                id: String(item.id || '').trim(),
                label: String(item.label || item.id || '').trim() || 'Cost item',
                description: String(item.description || '').trim(),
                basis: String(item.basis || '').trim(),
                amount: amount ?? 0,
                rate: toFiniteNumber(item.rate),
                regularRate: toFiniteNumber(item.regularRate),
                daytradeRate: toFiniteNumber(item.daytradeRate),
            }
        })
        .filter(Boolean)
}

function formatCostBreakdownItemLabel(item, { withAmount = true } = {}) {
    if (!item) {
        return '-'
    }

    const rateSuffix = item.basis === 'percent_notional'
        ? ` (${formatPercent(item.rate ?? item.regularRate)}${item.daytradeRate !== null && item.daytradeRate !== undefined ? ` / DT ${formatPercent(item.daytradeRate)}` : ''})`
        : item.basis === 'spread_pips'
            ? ` (${formatValue(item.rate)} pips)`
            : item.basis === 'per_contract'
                ? ` (${formatMoney(item.rate)} / contract)`
                : ''

    if (!withAmount) {
        return `${item.label}${rateSuffix}`.trim()
    }

    return `${item.label}: ${formatSignedMoney(item.amount)}${rateSuffix}`.trim()
}

function buildCostBreakdownInline(items = [], { withAmounts = true, maxItems = 2 } = {}) {
    const normalizedItems = normalizeCostBreakdownItems(items)
    if (!normalizedItems.length) {
        return '-'
    }

    const visibleItems = normalizedItems.slice(0, maxItems)
    const suffix = normalizedItems.length > maxItems ? ` +${normalizedItems.length - maxItems} more` : ''
    return `${visibleItems.map((item) => formatCostBreakdownItemLabel(item, { withAmount: withAmounts })).join(' · ')}${suffix}`
}

function buildCostBreakdownTooltip(items = []) {
    const normalizedItems = normalizeCostBreakdownItems(items)
    if (!normalizedItems.length) {
        return ''
    }

    return normalizedItems
        .map((item) => {
            const descriptionSuffix = item.description ? `\n${item.description}` : ''
            return `${formatCostBreakdownItemLabel(item, { withAmount: true })}${descriptionSuffix}`
        })
        .join('\n\n')
}

function formatEvaluationValue(value, format = 'number') {
    const number = toFiniteNumber(value)

    if (number === null) {
        return '-'
    }

    if (format === 'percent') {
        return formatPercent(number)
    }

    return formatValue(number, 2)
}

function buildBlankStrategy() {
    return JSON.parse(JSON.stringify(DEFAULT_STRATEGY))
}

function normalizeResearchStrategyEntries(entries = []) {
    return Array.isArray(entries)
        ? entries
            .filter((entry) => entry && typeof entry === 'object')
            .map((entry, index) => ({
                ...entry,
                priority: Number.isFinite(Number(entry?.priority)) ? Number(entry.priority) : index,
                strategy: entry?.strategy && typeof entry.strategy === 'object'
                    ? cloneSerializable(entry.strategy, {})
                    : {},
            }))
        : []
}

function buildResolvedResearchStrategyCollection(
    baseChartSettings,
    strategy,
    strategyEntries = [],
    extraIndicators = [],
) {
    const safeStrategy = strategy && typeof strategy === 'object'
        ? cloneSerializable(strategy, buildBlankStrategy())
        : buildBlankStrategy()
    const normalizedEntries = normalizeResearchStrategyEntries(strategyEntries)
    const collectionChartSettings = buildStrategyCollectionChartSettings(
        baseChartSettings,
        safeStrategy,
        normalizedEntries,
        extraIndicators,
    )

    return {
        chartSettings: collectionChartSettings,
        strategy: resolveStrategyAliasesInStrategy(safeStrategy, collectionChartSettings),
        strategies: normalizedEntries.map((entry) => ({
            ...entry,
            strategy: resolveStrategyAliasesInStrategy(entry?.strategy || {}, collectionChartSettings),
        })),
    }
}

function buildResearchBaselinePayload(baseChartSettings, strategyApplyResponse) {
    const request = strategyApplyResponse?.request || {}
    const resolvedRequest = buildResolvedResearchStrategyCollection(
        baseChartSettings,
        request?.strategy || buildBlankStrategy(),
        request?.strategies || [],
        baseChartSettings?.indicators || [],
    )

    return {
        id: 'current_strategy',
        label: 'Current strategy',
        strategy: resolvedRequest.strategy,
        strategies: resolvedRequest.strategies,
    }
}

function applyResearchStrategySelection({
    setStrategy,
    setStrategySetEntries,
    strategy,
    strategies = [],
}) {
    if (!setStrategy || !strategy || typeof strategy !== 'object') {
        return
    }

    setStrategy(cloneSerializable(strategy, buildBlankStrategy()))

    if (typeof setStrategySetEntries === 'function') {
        setStrategySetEntries(cloneSerializable(
            Array.isArray(strategies) ? strategies : [],
            [],
        ))
    }
}

function buildHydratedBacktestPayloadFromPipelineRun(run) {
    const pipeline = run?.payload?.pipeline
    const request = pipeline?.request || {}
    const chart = pipeline?.chart || {}

    if (!request?.strategy || !request?.backtest) {
        return null
    }

    return {
        chartSettings: {
            symbol: String(chart?.symbol || '').trim().toUpperCase(),
            timeframe: String(chart?.timeframe || '').trim().toUpperCase(),
            bars: Math.max(1, Number(chart?.bars) || 1),
            indicators: Array.isArray(chart?.indicators) ? chart.indicators : [],
        },
        strategy: request.strategy,
        backtest: request.backtest,
        strategyResponse: {
            status: 'ok',
            request: {
                strategy: request.strategy,
                strategies: Array.isArray(request.strategies) ? request.strategies : [],
                backtest: request.backtest,
            },
            runtime: {
                market: {
                    symbol: String(chart?.symbol || '').trim().toUpperCase(),
                    timeframe: String(chart?.timeframe || '').trim().toUpperCase(),
                    bars: Math.max(1, Number(chart?.bars) || 1),
                },
            },
            rows: Array.isArray(pipeline?.results) ? pipeline.results.length : 0,
            has_results: Array.isArray(pipeline?.results) && pipeline.results.length > 0,
            results: Array.isArray(pipeline?.results) ? pipeline.results : [],
            stats: pipeline?.stats || {},
            strategy_view_meta: pipeline?.strategy_view_meta || {},
            applied_indicators: Array.isArray(pipeline?.applied_indicators) ? pipeline.applied_indicators : [],
            available_columns: Array.isArray(pipeline?.available_columns) ? pipeline.available_columns : [],
            available_column_details: Array.isArray(pipeline?.available_column_details) ? pipeline.available_column_details : [],
            trade_markers: Array.isArray(pipeline?.trade_markers) ? pipeline.trade_markers : [],
        },
    }
}

function cloneSerializable(value, fallback = null) {
    if (value === undefined) {
        return fallback
    }

    try {
        return JSON.parse(JSON.stringify(value))
    } catch {
        return fallback
    }
}

function buildResearchStrategySnapshot(strategyApplyResponse, chartSettings) {
    const request = strategyApplyResponse?.request || {}
    const strategy = cloneSerializable(request?.strategy, buildBlankStrategy())
    const strategies = cloneSerializable(request?.strategies, [])
    const backtest = cloneSerializable(request?.backtest, null)

    if (!strategy) {
        return null
    }

    return {
        savedAt: new Date().toISOString(),
        strategy,
        strategies,
        backtest,
        chartContext: {
            symbol: String(
                request?.symbol
                || strategyApplyResponse?.runtime?.market?.symbol
                || chartSettings?.symbol
                || ''
            ).toUpperCase(),
            timeframe: String(
                request?.timeframe
                || strategyApplyResponse?.runtime?.market?.timeframe
                || chartSettings?.timeframe
                || ''
            ).toUpperCase(),
            bars: Number(
                request?.bars
                || strategyApplyResponse?.rows
                || strategyApplyResponse?.runtime?.market?.bars
                || chartSettings?.bars
                || 0
            ) || 0,
        },
    }
}

function buildResearchChartSettings(chartSettings, strategyApplyResponse) {
    const request = strategyApplyResponse?.request || {}
    const requestSymbol = String(request?.symbol || '').trim().toUpperCase()
    const requestTimeframe = String(request?.timeframe || '').trim().toUpperCase()
    const requestBars = Math.max(0, Number(request?.bars) || 0)
    const runtimeBars = Math.max(0, Number(strategyApplyResponse?.runtime?.market?.bars) || 0)
    const resultRows = Math.max(0, Number(strategyApplyResponse?.rows) || 0)
    const baseIndicators = dedupeResearchIndicatorsByAliasPriority(
        mergeIndicators(
            mergeIndicators(
                normalizeIndicatorsForMerge(chartSettings?.indicators),
                normalizeIndicatorsForMerge(strategyApplyResponse?.applied_indicators),
            ),
            [DEFAULT_MARKET_REGIME_INDICATOR],
        )
    )
    return buildStrategyCollectionChartSettings({
        symbol: requestSymbol || chartSettings?.symbol,
        timeframe: requestTimeframe || chartSettings?.timeframe,
        bars: requestBars || resultRows || runtimeBars || chartSettings?.bars || 1000,
        indicators: baseIndicators,
    }, strategyApplyResponse?.request?.strategy || null, strategyApplyResponse?.request?.strategies || [])
}

function extractSavableStrategyPayloadFromResearchJob(job) {
    const directRequest = job?.request && typeof job.request === 'object' ? job.request : null
    const pipelineRequest = job?.result?.pipeline?.request && typeof job.result.pipeline.request === 'object'
        ? job.result.pipeline.request
        : null
    const resultBaseline = job?.result?.baseline && typeof job.result.baseline === 'object'
        ? job.result.baseline
        : null
    const requestCandidates = [
        pipelineRequest,
        directRequest,
        directRequest?.baseline,
        resultBaseline,
    ].filter((entry) => entry && typeof entry === 'object')

    for (const entry of requestCandidates) {
        const nextStrategy = entry?.strategy && typeof entry.strategy === 'object'
            ? entry.strategy
            : {}
        const nextStrategies = Array.isArray(entry?.strategies) ? entry.strategies : []

        if (Object.keys(nextStrategy).length || nextStrategies.length) {
            return {
                requestPayload: entry,
                strategy: nextStrategy,
                strategies: nextStrategies,
            }
        }
    }

    return {
        requestPayload: directRequest || pipelineRequest || {},
        strategy: {},
        strategies: [],
    }
}

function mergeRemoteResearchEntities(currentEntries = [], incomingEntry, { maxEntries = 100 } = {}) {
    const safeEntries = Array.isArray(currentEntries) ? currentEntries : []
    const safeIncoming = incomingEntry && typeof incomingEntry === 'object' ? incomingEntry : null
    const incomingId = String(safeIncoming?.id || '').trim()

    if (!incomingId) {
        return safeEntries
    }

    const existing = safeEntries.find((entry) => String(entry?.id || '').trim() === incomingId) || null
    const merged = {
        ...(existing || {}),
        ...safeIncoming,
    }

    if (
        existing?.request !== undefined
        && existing?.request_loaded !== false
        && (safeIncoming?.request === undefined || safeIncoming?.request_loaded === false)
    ) {
        merged.request = existing.request
        merged.request_loaded = existing.request_loaded
        merged.request_size_bytes = existing.request_size_bytes ?? safeIncoming?.request_size_bytes
    }

    if (
        existing?.result !== undefined
        && existing?.result_loaded !== false
        && (safeIncoming?.result === undefined || safeIncoming?.result_loaded === false)
    ) {
        merged.result = existing.result
        merged.result_loaded = existing.result_loaded
        merged.result_size_bytes = existing.result_size_bytes ?? safeIncoming?.result_size_bytes
    }

    if (
        existing?.payload !== undefined
        && existing?.payload_loaded !== false
        && (safeIncoming?.payload === undefined || safeIncoming?.payload_loaded === false)
    ) {
        merged.payload = existing.payload
        merged.payload_loaded = existing.payload_loaded
        merged.payload_size_bytes = existing.payload_size_bytes ?? safeIncoming?.payload_size_bytes
    }

    return [
        merged,
        ...safeEntries.filter((entry) => String(entry?.id || '').trim() !== incomingId),
    ]
        .sort((left, right) => Number(right?.created_at || right?.updated_at || right?.id || 0) - Number(left?.created_at || left?.updated_at || left?.id || 0))
        .slice(0, maxEntries)
}

function reconcileRemoteResearchEntities(currentEntries = [], incomingEntries = [], { maxEntries = 100 } = {}) {
    const safeIncomingEntries = Array.isArray(incomingEntries) ? incomingEntries : []
    const incomingIds = new Set(safeIncomingEntries.map((entry) => String(entry?.id || '').trim()).filter(Boolean))
    let mergedEntries = Array.isArray(currentEntries) ? currentEntries : []

    for (const incomingEntry of safeIncomingEntries) {
        mergedEntries = mergeRemoteResearchEntities(mergedEntries, incomingEntry, { maxEntries: Number.MAX_SAFE_INTEGER })
    }

    return mergedEntries
        .filter((entry) => incomingIds.has(String(entry?.id || '').trim()))
        .slice(0, maxEntries)
}

function buildResearchJobChartSettings(job, fallbackChartSettings = {}) {
    const pipelineChart = job?.result?.pipeline?.chart && typeof job.result.pipeline.chart === 'object'
        ? job.result.pipeline.chart
        : {}
    const directRequest = job?.request && typeof job.request === 'object' ? job.request : {}
    const chartContext = directRequest?.chartContext && typeof directRequest.chartContext === 'object'
        ? directRequest.chartContext
        : {}

    return {
        symbol: String(
            pipelineChart?.symbol
            || chartContext?.symbol
            || fallbackChartSettings?.symbol
            || ''
        ).trim().toUpperCase(),
        timeframe: String(
            pipelineChart?.timeframe
            || chartContext?.timeframe
            || fallbackChartSettings?.timeframe
            || ''
        ).trim().toUpperCase(),
        bars: Math.max(
            1,
            Number(
                pipelineChart?.bars
                || chartContext?.bars
                || fallbackChartSettings?.bars
                || 1000
            ) || 1000,
        ),
        indicators: Array.isArray(pipelineChart?.indicators)
            ? pipelineChart.indicators
            : Array.isArray(chartContext?.indicators)
                ? chartContext.indicators
                : Array.isArray(fallbackChartSettings?.indicators)
                    ? fallbackChartSettings.indicators
                    : [],
    }
}

function buildPortfolioSummaryModel(strategyApplyResponse) {
    const requestStrategies = Array.isArray(strategyApplyResponse?.request?.strategies)
        ? strategyApplyResponse.request.strategies
        : []
    const strategyStats = Array.isArray(strategyApplyResponse?.stats?.portfolio_strategy_stats)
        ? strategyApplyResponse.stats.portfolio_strategy_stats
        : []
    const strategyCount = Number(strategyApplyResponse?.stats?.strategy_count || requestStrategies.length || 0)
    const isMulti = strategyCount > 1 || requestStrategies.length > 1 || strategyStats.length > 1
    const analytics = strategyApplyResponse?.stats?.portfolio_analytics || {}
    const pairwise = Array.isArray(analytics?.pairwise) ? analytics.pairwise : []
    const topPositivePair = pairwise
        .filter((item) => Number.isFinite(Number(item?.correlation)))
        .sort((left, right) => Number(right?.correlation || 0) - Number(left?.correlation || 0))[0] || null
    const topNegativePair = pairwise
        .filter((item) => Number.isFinite(Number(item?.correlation)))
        .sort((left, right) => Number(left?.correlation || 0) - Number(right?.correlation || 0))[0] || null

    return {
        isMulti,
        strategyCount,
        requestStrategies,
        strategyStats,
        eventCounts: strategyApplyResponse?.stats?.portfolio_event_counts || {},
        analytics,
        topPositivePair,
        topNegativePair,
    }
}

function formatPairLabel(pair) {
    if (!pair) {
        return '-'
    }
    const left = String(pair?.left_strategy_id || 'A').trim() || 'A'
    const right = String(pair?.right_strategy_id || 'B').trim() || 'B'
    return `${left} × ${right}`
}

function selectRelevantPairwiseRows(pairwise = [], limit = 4) {
    if (!Array.isArray(pairwise) || !pairwise.length) {
        return []
    }

    return pairwise
        .slice()
        .sort((left, right) => {
            const leftScore = Math.max(
                Math.abs(Number(left?.correlation || 0)),
                Number(left?.same_direction_overlap_rate || 0),
                Number(left?.opposite_direction_overlap_rate || 0),
            )
            const rightScore = Math.max(
                Math.abs(Number(right?.correlation || 0)),
                Number(right?.same_direction_overlap_rate || 0),
                Number(right?.opposite_direction_overlap_rate || 0),
            )
            return rightScore - leftScore
        })
        .slice(0, limit)
}

function PortfolioSummaryPane({ strategyApplyResponse }) {
    const portfolio = buildPortfolioSummaryModel(strategyApplyResponse)
    const relevantPairs = useMemo(
        () => selectRelevantPairwiseRows(portfolio.analytics?.pairwise, 4),
        [portfolio.analytics?.pairwise],
    )

    if (!portfolio.isMulti) {
        return null
    }

    return (
        <div className='portfolioSummaryPanel'>
            <div className='portfolioSummaryHeader'>
                <div className='portfolioSummaryTitle'>Portfolio run</div>
                <div className='portfolioSummaryMeta'>
                    <span>{portfolio.strategyCount} strategies</span>
                    <span>Opens: {formatInteger(portfolio.eventCounts.open)}</span>
                    <span>Closes: {formatInteger(portfolio.eventCounts.close)}</span>
                    <span>Stops: {formatInteger(portfolio.eventCounts.stop)}</span>
                    <span>Skipped: {formatInteger(portfolio.eventCounts.skip_open)}</span>
                </div>
            </div>

            <div className='portfolioSummaryAnalyticsGrid'>
                <div className='portfolioSummaryAnalyticsCard'>
                    <strong>Overlap</strong>
                    <span>Simultaneous: {formatPercent(portfolio.analytics?.simultaneous_position_rate || 0, 1)}</span>
                    <span>Same dir: {formatPercent(portfolio.analytics?.same_direction_overlap_rate || 0, 1)}</span>
                    <span>Opposite dir: {formatPercent(portfolio.analytics?.opposite_direction_overlap_rate || 0, 1)}</span>
                </div>
                <div className='portfolioSummaryAnalyticsCard'>
                    <strong>Conflicts</strong>
                    <span>Blocked by direction: {formatInteger(portfolio.eventCounts.skip_open_conflict)}</span>
                    <span>Existing position: {formatInteger(portfolio.eventCounts.skip_open_existing_position)}</span>
                    <span>Max concurrent: {formatInteger(portfolio.analytics?.max_concurrent_strategies || 0)}</span>
                </div>
                <div className='portfolioSummaryAnalyticsCard'>
                    <strong>Top positive pair</strong>
                    <span>{formatPairLabel(portfolio.topPositivePair)}</span>
                    <span>Corr: {formatPercent(portfolio.topPositivePair?.correlation || 0, 1)}</span>
                    <span>Overlap bars: {formatInteger(portfolio.topPositivePair?.overlap_bars || 0)}</span>
                </div>
                <div className='portfolioSummaryAnalyticsCard'>
                    <strong>Top negative pair</strong>
                    <span>{formatPairLabel(portfolio.topNegativePair)}</span>
                    <span>Corr: {formatPercent(portfolio.topNegativePair?.correlation || 0, 1)}</span>
                    <span>Opposite overlap: {formatPercent(portfolio.topNegativePair?.opposite_direction_overlap_rate || 0, 1)}</span>
                </div>
            </div>

            <div className='portfolioSummaryGrid'>
                {portfolio.strategyStats.map((item) => (
                    <div key={item.strategy_id || item.strategy_label} className='portfolioSummaryCard'>
                        <strong>{item.strategy_label || item.strategy_id || 'Strategy'}</strong>
                        <span>Trades: {formatInteger(item.trades)}</span>
                        <span>Net PnL: {formatSignedMoney(item.net_pnl)}</span>
                        <span>Gross PnL: {formatSignedMoney(item.gross_pnl)}</span>
                        <span>Costs: {formatMoney(item.cost)}</span>
                        <span>Win rate: {formatPercent(item.win_rate)}</span>
                    </div>
                ))}
            </div>

            {relevantPairs.length ? (
                <div className='portfolioSummaryPairwisePanel'>
                    <div className='portfolioSummaryTitle'>Pairwise drilldown</div>
                    <div className='portfolioSummaryPairwiseList'>
                        {relevantPairs.map((pair, index) => (
                            <div key={`${pair?.left_strategy_id || 'left'}-${pair?.right_strategy_id || 'right'}-${index}`} className='portfolioSummaryPairwiseItem'>
                                <strong>{formatPairLabel(pair)}</strong>
                                <span>Corr: {formatPercent(pair?.correlation || 0, 1)}</span>
                                <span>Same-dir: {formatPercent(pair?.same_direction_overlap_rate || 0, 1)}</span>
                                <span>Opposite-dir: {formatPercent(pair?.opposite_direction_overlap_rate || 0, 1)}</span>
                                <span>Overlap bars: {formatInteger(pair?.overlap_bars || 0)}</span>
                            </div>
                        ))}
                    </div>
                </div>
            ) : null}
        </div>
    )
}

function buildIndicatorId(indicator, index = 0) {
    const safeName = String(indicator?.name || 'indicator').toLowerCase()
    return indicator?.id || `${safeName}-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

const DEFAULT_MARKET_REGIME_INDICATOR = {
    id: 'research-mreg-default',
    name: 'MarketRegime',
    alias: 'mreg',
    params: [9, 21, 14, 14, 20, 2, 20, 14, 10, 3, 'hlc3', 5, 3],
}

function normalizeIndicatorsForMerge(indicators) {
    if (!Array.isArray(indicators)) {
        return []
    }

    return indicators.map((indicator, index) => ({
        id: buildIndicatorId(indicator, index),
        name: String(indicator?.name || '').trim(),
        params: Array.isArray(indicator?.params) ? indicator.params : [],
        alias: String(indicator?.alias || '').trim(),
        lines: Array.isArray(indicator?.lines) ? indicator.lines : [],
    }))
}

function mergeIndicators(baseIndicators = [], extraIndicators = []) {
    const merged = new Map()

    for (const indicator of [...baseIndicators, ...extraIndicators]) {
        const normalized = {
            id: buildIndicatorId(indicator),
            name: String(indicator?.name || '').trim(),
            params: Array.isArray(indicator?.params) ? indicator.params : [],
            alias: String(indicator?.alias || '').trim(),
            lines: Array.isArray(indicator?.lines) ? indicator.lines : [],
        }

        const key = `${normalized.name.toUpperCase()}:${JSON.stringify(normalized.params)}`

        if (!merged.has(key)) {
            merged.set(key, normalized)
            continue
        }

        const existing = merged.get(key)
        merged.set(key, {
            ...existing,
            alias: existing?.alias || normalized.alias,
            lines: Array.isArray(existing?.lines) && existing.lines.length
                ? existing.lines
                : normalized.lines,
        })
    }

    return Array.from(merged.values())
}

function getResearchIndicatorAliasKey(indicator) {
    const explicitAlias = String(indicator?.alias || '').trim().toLowerCase()
    if (explicitAlias) {
        return explicitAlias
    }

    const indicatorName = String(indicator?.name || '').trim().toLowerCase()
    if (!indicatorName) {
        return ''
    }

    if (indicatorName === 'marketregime') {
        return 'mreg'
    }
    if (indicatorName === 'rsi') {
        return 'rsi'
    }

    return indicatorName
}

function dedupeResearchIndicatorsByAliasPriority(indicators = []) {
    const deduped = []
    const aliasIndex = new Map()

    for (const indicator of Array.isArray(indicators) ? indicators : []) {
        const normalized = {
            id: buildIndicatorId(indicator),
            name: String(indicator?.name || '').trim(),
            params: Array.isArray(indicator?.params) ? indicator.params : [],
            alias: String(indicator?.alias || '').trim(),
            lines: Array.isArray(indicator?.lines) ? indicator.lines : [],
        }
        const aliasKey = getResearchIndicatorAliasKey(normalized)

        if (!aliasKey) {
            deduped.push(normalized)
            continue
        }

        const existingIndex = aliasIndex.get(aliasKey)
        if (existingIndex == null) {
            aliasIndex.set(aliasKey, deduped.length)
            deduped.push(normalized)
            continue
        }

        deduped[existingIndex] = normalized
    }

    return deduped
}

function buildStudyChartContext(chartSettings, strategyApplyResponse) {
    const researchChartSettings = buildResearchChartSettings(chartSettings, strategyApplyResponse)

    return {
        symbol: researchChartSettings.symbol,
        timeframe: researchChartSettings.timeframe,
        bars: researchChartSettings.bars,
        indicators: researchChartSettings.indicators,
    }
}

function buildAbortError() {
    try {
        return new DOMException('The operation was aborted.', 'AbortError')
    } catch {
        const fallback = new Error('The operation was aborted.')
        fallback.name = 'AbortError'
        return fallback
    }
}

function waitForResearchJobPoll(ms, signal) {
    return new Promise((resolve, reject) => {
        if (signal?.aborted) {
            reject(buildAbortError())
            return
        }

        const timeoutId = window.setTimeout(() => {
            cleanup()
            resolve()
        }, Math.max(0, Number(ms || 0)))

        function handleAbort() {
            cleanup()
            reject(buildAbortError())
        }

        function cleanup() {
            window.clearTimeout(timeoutId)
            signal?.removeEventListener?.('abort', handleAbort)
        }

        signal?.addEventListener?.('abort', handleAbort, { once: true })
    })
}

async function cancelResearchJobRequest(authToken, jobId) {
    if (!authToken || !jobId) {
        return null
    }

    try {
        const response = await fetch(buildApiUrl(`/workspace/research-jobs/${jobId}/cancel?workspace_id=default`), {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
            },
        })
        return await readJsonResponse(response)
    } catch {
        // Best-effort cancellation only.
        return null
    }
}

async function cancelResearchBatchRequest(authToken, batchId) {
    if (!authToken || !batchId) {
        return null
    }

    try {
        const response = await fetch(buildApiUrl(`/workspace/research-batches/${batchId}/cancel?workspace_id=default`), {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
            },
        })
        return await readJsonResponse(response)
    } catch {
        // Best-effort cancellation only.
        return null
    }
}

async function runPresetCompareResearchJob({
    authToken,
    requestPayload,
    abortController = null,
    onJobUpdate = null,
    pollIntervalMs = 1250,
}) {
    const response = await fetch(buildApiUrl('/workspace/research-jobs'), {
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${authToken}`,
            'Content-Type': 'application/json',
        },
        signal: abortController?.signal,
        body: JSON.stringify({
            job_type: 'preset_compare',
            request: requestPayload,
        }),
    })
    const createPayload = await readJsonResponse(response)
    if (response.status === 404) {
        throw new Error('Research job endpoint was not found. Restart the backend to load the latest workspace routes.')
    }
    if (!response.ok || createPayload?.status !== 'ok') {
        throw new Error(extractApiErrorMessage(createPayload, 'Failed to queue research job.'))
    }

    const jobId = createPayload?.job?.id
    if (abortController) {
        abortController.jobId = jobId
    }
    if (!jobId) {
        throw new Error('Research job was queued without a valid job id.')
    }
    if (typeof onJobUpdate === 'function') {
        onJobUpdate(createPayload.job)
    }

    try {
        while (true) {
            if (abortController?.signal?.aborted) {
                throw buildAbortError()
            }

            const jobResponse = await fetch(buildApiUrl(`/workspace/research-jobs/${jobId}?workspace_id=default`), {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${authToken}`,
                },
                signal: abortController?.signal,
            })
            const jobPayload = await readJsonResponse(jobResponse)
            if (!jobResponse.ok || jobPayload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(jobPayload, 'Failed to load research job status.'))
            }

            const job = jobPayload?.job || null
            if (typeof onJobUpdate === 'function' && job) {
                onJobUpdate(job)
            }

            const jobStatus = String(job?.status || '').trim().toLowerCase()
            if (jobStatus === 'completed') {
                return job?.result || {}
            }
            if (jobStatus === 'failed') {
                throw new Error(job?.error || job?.detail || 'Research job failed.')
            }
            if (jobStatus === 'cancelled') {
                throw new Error(job?.error || job?.detail || 'Research job was cancelled.')
            }

            await waitForResearchJobPoll(pollIntervalMs, abortController?.signal)
        }
    } catch (error) {
        if (error?.name === 'AbortError') {
            await cancelResearchJobRequest(authToken, jobId)
        }
        throw error
    }
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

function buildDefaultPresetComparisonState() {
    return {
        loading: false,
        error: '',
        baseline: null,
        comparisons: [],
        bestPresetId: '',
        studyLoading: false,
        study: null,
    }
}

function buildDefaultSimpleStudyState() {
    return {
        loading: false,
        error: '',
        study: null,
    }
}

function buildDefaultPromotionCandidateState() {
    return {
        loading: false,
        error: '',
        result: null,
    }
}

function getDisplayFeatureLabel(columnName, chartSettings) {
    const alias = getStrategyAliasForColumnName(columnName, chartSettings)
    return alias || String(columnName || '')
}

function clamp01(value) {
    if (!Number.isFinite(value)) {
        return 0
    }

    return Math.max(0, Math.min(1, value))
}

function scoreHigherIsBetter(value, target) {
    const number = toFiniteNumber(value)

    if (number === null || target <= 0) {
        return null
    }

    return clamp01(number / target)
}

function scoreLowerIsBetter(value, target) {
    const number = toFiniteNumber(value)

    if (number === null || target <= 0) {
        return null
    }

    if (number <= 0) {
        return 1
    }

    return clamp01(target / number)
}

function buildStatisticsEvaluation(stats) {
    if (!stats) {
        return null
    }

    const criteria = [
        {
            key: 'net_profit_factor',
            label: 'Net profit factor',
            targetLabel: '>= 1.75',
            valueFormat: 'number',
            actualValue: stats.net_profit_factor,
            targetValue: 1.75,
            weight: 0.28,
            score: scoreHigherIsBetter(stats.net_profit_factor, 1.75),
        },
        {
            key: 'max_drawdown_pct',
            label: 'Max drawdown %',
            targetLabel: '<= 10.00%',
            valueFormat: 'percent',
            actualValue: Math.abs(toFiniteNumber(stats.max_drawdown_pct) ?? 0),
            targetValue: 0.10,
            weight: 0.24,
            score: scoreLowerIsBetter(Math.abs(toFiniteNumber(stats.max_drawdown_pct) ?? 0), 0.10),
        },
        {
            key: 'sharpe_ratio',
            label: 'Sharpe ratio',
            targetLabel: '>= 1.50',
            valueFormat: 'number',
            actualValue: stats.sharpe_ratio,
            targetValue: 1.50,
            weight: 0.16,
            score: scoreHigherIsBetter(stats.sharpe_ratio, 1.50),
        },
        {
            key: 'sortino_ratio',
            label: 'Sortino ratio',
            targetLabel: '>= 2.00',
            valueFormat: 'number',
            actualValue: stats.sortino_ratio,
            targetValue: 2.00,
            weight: 0.12,
            score: scoreHigherIsBetter(stats.sortino_ratio, 2.00),
        },
        {
            key: 'win_rate',
            label: 'Win rate',
            targetLabel: '>= 55.00%',
            valueFormat: 'percent',
            actualValue: stats.win_rate,
            targetValue: 0.55,
            weight: 0.10,
            score: scoreHigherIsBetter(stats.win_rate, 0.55),
        },
        {
            key: 'risk_reward_ratio',
            label: 'Risk / reward',
            targetLabel: '>= 1.50',
            valueFormat: 'number',
            actualValue: stats.risk_reward_ratio,
            targetValue: 1.50,
            weight: 0.06,
            score: scoreHigherIsBetter(stats.risk_reward_ratio, 1.50),
        },
        {
            key: 'recovery_factor',
            label: 'Recovery factor',
            targetLabel: '>= 2.00',
            valueFormat: 'number',
            actualValue: stats.recovery_factor,
            targetValue: 2.00,
            weight: 0.02,
            score: scoreHigherIsBetter(stats.recovery_factor, 2.00),
        },
        {
            key: 'kelly_fraction',
            label: 'Kelly fraction',
            targetLabel: '>= 20.00%',
            valueFormat: 'percent',
            actualValue: stats.kelly_fraction,
            targetValue: 0.20,
            weight: 0.02,
            score: scoreHigherIsBetter(stats.kelly_fraction, 0.20),
        },
    ]

    const evaluatedCriteria = criteria.map((criterion) => {
        const isMissing = criterion.score === null
        const normalizedScore = isMissing ? 0 : criterion.score
        return {
            ...criterion,
            score: normalizedScore,
            isMissing,
        }
    })
    const totalWeight = evaluatedCriteria.reduce((sum, criterion) => sum + criterion.weight, 0)
    const weightedScore = totalWeight > 0
        ? evaluatedCriteria.reduce((sum, criterion) => sum + (criterion.weight * criterion.score), 0) / totalWeight
        : 0
    const scoreOutOfTen = weightedScore * 10

    return {
        scoreOutOfTen,
        percentScore: weightedScore,
        criteria: evaluatedCriteria.map((criterion) => ({
            ...criterion,
            achievedPct: criterion.score * 100,
            contributionPoints: criterion.score * criterion.weight * 10,
            maxContributionPoints: criterion.weight * 10,
        })),
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;')
}

function triggerTextDownload(filename, mimeType, content) {
    const blob = new Blob([content], { type: mimeType })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
}

function triggerTextOpen(mimeType, content) {
    const blob = new Blob([content], { type: mimeType })
    const url = URL.createObjectURL(blob)
    const opened = window.open(url, '_blank', 'noopener,noreferrer')
    if (!opened) {
        URL.revokeObjectURL(url)
        return false
    }
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    return true
}

function buildCsvString(rows = []) {
    if (!Array.isArray(rows) || rows.length === 0) {
        return ''
    }

    const headers = Array.from(
        rows.reduce((set, row) => {
            Object.keys(row || {}).forEach((key) => set.add(key))
            return set
        }, new Set())
    )

    const escapeCsvCell = (value) => {
        const text = String(value ?? '')
        if (text.includes('"') || text.includes(',') || text.includes('\n')) {
            return `"${text.replaceAll('"', '""')}"`
        }
        return text
    }

    const lines = [
        headers.map(escapeCsvCell).join(','),
        ...rows.map((row) => headers.map((header) => escapeCsvCell(row?.[header])).join(',')),
    ]

    return lines.join('\n')
}

function buildStatisticsRowsForExport(stats = {}, evaluation = null) {
    const executionPolicy = stats.execution_policy || {}

    return [
        { section: 'score', metric: 'score_out_of_ten', value: evaluation?.scoreOutOfTen ?? '' },
        { section: 'score', metric: 'percent_score', value: evaluation?.percentScore ?? '' },
        ...(evaluation?.criteria || []).map((criterion) => ({
            section: 'score_criteria',
            metric: criterion.label,
            value: criterion.actualValue,
            target: criterion.targetValue,
            achieved_pct: criterion.achievedPct,
        })),
        { section: 'summary', metric: 'initial_balance', value: stats.initial_balance ?? '' },
        { section: 'summary', metric: 'final_balance', value: stats.final_balance ?? '' },
        { section: 'summary', metric: 'win_rate', value: stats.win_rate ?? '' },
        { section: 'summary', metric: 'net_profit_factor', value: stats.net_profit_factor ?? '' },
        { section: 'summary', metric: 'max_drawdown_pct', value: stats.max_drawdown_pct ?? '' },
        { section: 'summary', metric: 'total_operational_cost', value: stats.total_operational_cost ?? '' },
        { section: 'summary', metric: 'total_estimated_tax', value: stats.total_estimated_tax ?? '' },
        { section: 'summary', metric: 'total_cost', value: stats.total_cost ?? '' },
        { section: 'execution', metric: 'execution_mode', value: executionPolicy.execution_mode ?? '' },
        { section: 'execution', metric: 'requested_cost_profile', value: executionPolicy.requested_cost_profile_label ?? executionPolicy.requested_cost_profile ?? '' },
        { section: 'execution', metric: 'effective_cost_profile', value: executionPolicy.cost_profile_label ?? executionPolicy.cost_profile ?? '' },
        { section: 'execution', metric: 'asset_type', value: executionPolicy.asset_type_label ?? executionPolicy.asset_type ?? '' },
        { section: 'execution', metric: 'spread_in_pips', value: executionPolicy.spread_in_pips ?? '' },
        { section: 'execution', metric: 'entry_slippage_in_pips', value: executionPolicy.entry_slippage_in_pips ?? '' },
        { section: 'execution', metric: 'close_slippage_in_pips', value: executionPolicy.close_slippage_in_pips ?? '' },
        { section: 'execution', metric: 'take_profit_slippage_in_pips', value: executionPolicy.take_profit_slippage_in_pips ?? '' },
        { section: 'execution', metric: 'stop_loss_slippage_in_pips', value: executionPolicy.stop_loss_slippage_in_pips ?? '' },
        { section: 'execution', metric: 'trailing_stop_slippage_in_pips', value: executionPolicy.trailing_stop_slippage_in_pips ?? '' },
        ...(Array.isArray(stats.cost_breakdown_totals) ? stats.cost_breakdown_totals.map((item) => ({
            section: 'execution_cost_items',
            metric: item?.label || item?.id || 'cost_item',
            value: item?.amount ?? '',
            target: item?.basis ?? '',
        })) : []),
        ...(Array.isArray(stats.estimated_tax_breakdown_totals) ? stats.estimated_tax_breakdown_totals.map((item) => ({
            section: 'execution_tax_items',
            metric: item?.label || item?.id || 'tax_item',
            value: item?.amount ?? '',
            target: item?.basis ?? '',
        })) : []),
    ]
}

function buildHtmlReport({ title, exportedAt, request, backtest, evaluation, stats, results, exportScope }) {
    const statsRows = buildStatisticsRowsForExport(stats, evaluation)
    const statRowsHtml = statsRows
        .map((row) => `<tr><td>${escapeHtml(row.section)}</td><td>${escapeHtml(row.metric)}</td><td>${escapeHtml(row.value)}</td><td>${escapeHtml(row.target ?? '')}</td><td>${escapeHtml(row.achieved_pct ?? '')}</td></tr>`)
        .join('')
    const resultRows = (results || []).slice(0, 200)
    const resultHeaders = Array.from(
        resultRows.reduce((set, row) => {
            Object.keys(row || {}).forEach((key) => set.add(key))
            return set
        }, new Set())
    )
    const resultHeadHtml = resultHeaders.map((header) => `<th>${escapeHtml(header)}</th>`).join('')
    const resultBodyHtml = resultRows.map((row) => (
        `<tr>${resultHeaders.map((header) => `<td>${escapeHtml(row?.[header] ?? '')}</td>`).join('')}</tr>`
    )).join('')

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(title)}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; color: #111827; }
    h1, h2 { margin: 0 0 12px; }
    .meta { margin-bottom: 24px; color: #4b5563; }
    table { border-collapse: collapse; width: 100%; margin: 16px 0 24px; }
    th, td { border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; vertical-align: top; }
    th { background: #f3f4f6; }
    code, pre { background: #f8fafc; border: 1px solid #e5e7eb; padding: 12px; display: block; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>${escapeHtml(title)}</h1>
  <div class="meta">Exported at ${escapeHtml(exportedAt)} | Scope: ${escapeHtml(exportScope)}</div>
  ${(exportScope === 'full_report' || exportScope === 'statistics_only') ? `<h2>Statistics</h2><table><thead><tr><th>Section</th><th>Metric</th><th>Value</th><th>Target</th><th>Achieved %</th></tr></thead><tbody>${statRowsHtml}</tbody></table>` : ''}
  ${(exportScope === 'full_report' || exportScope === 'trade_results') ? `<h2>Trade Results</h2><table><thead><tr>${resultHeadHtml}</tr></thead><tbody>${resultBodyHtml}</tbody></table>` : ''}
  ${(exportScope === 'full_report' || exportScope === 'request_payload') ? `<h2>Request</h2><pre>${escapeHtml(JSON.stringify({ request, backtest }, null, 2))}</pre>` : ''}
</body>
</html>`
}

function getTone(value) {
    const number = toFiniteNumber(value)

    if (number === null || number === 0) {
        return 'neutral'
    }

    return number > 0 ? 'positive' : 'negative'
}

function getEvaluationTone(scoreOutOfTen) {
    if (!Number.isFinite(scoreOutOfTen)) {
        return 'neutral'
    }

    if (scoreOutOfTen >= 7.5) {
        return 'positive'
    }

    if (scoreOutOfTen >= 5) {
        return 'warning'
    }

    return 'negative'
}

function getEvaluationStyle(scoreOutOfTen) {
    const normalized = clamp01((toFiniteNumber(scoreOutOfTen) ?? 0) / 10)
    const hue = normalized * 120

    return {
        background: `linear-gradient(135deg, hsla(${hue}, 72%, 44%, 0.22), rgba(255, 255, 255, 0.03) 62%)`,
        borderColor: `hsla(${hue}, 72%, 52%, 0.45)`,
    }
}

function SeriesChart({ series, canLoadStoredCharts = false, onLoadStoredCharts = null }) {
    const width = 900
    const height = 360
    const padding = 28
    const points = series?.points || []

    if (!points.length) {
        return (
            <div className='resultsChartCard'>
                <div className='resultsChartHeader'>
                    <div className='resultsChartTitle'>{series?.label || 'Series'}</div>
                    <div className='resultsChartMeta'>0 points</div>
                </div>

                <div className='resultsChartEmpty'>
                    <div className='resultsChartEmptyContent'>
                        <div>No data for this series.</div>
                        {canLoadStoredCharts ? (
                            <>
                                <div className='resultsChartEmptyHint'>Data series are stored locally for the current project. Load them on demand to inspect charts.</div>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onLoadStoredCharts?.()}
                                >
                                    Load results charts
                                </button>
                            </>
                        ) : (
                            <div className='resultsChartEmptyHint'>Data series are not stored in saved history. Re-run the backtest to inspect charts.</div>
                        )}
                    </div>
                </div>
            </div>
        )
    }

    const { minY, maxY } = getMinMax(points)
    const linePath = buildLinePath(points, width, height, padding)
    const zeroGuide = minY <= 0 && maxY >= 0
        ? buildHorizontalGuide(0, minY, maxY, width, height, padding)
        : null

    const firstValue = points[0]?.y
    const lastValue = points[points.length - 1]?.y

    return (
        <div className='resultsChartCard'>
            <div className='resultsChartHeader'>
                <div className='resultsChartTitle'>{series.label}</div>

                <div className='resultsChartMeta'>
                    <span>{points.length} points</span>
                    <span>min: {formatValue(minY)}</span>
                    <span>max: {formatValue(maxY)}</span>
                    <span>first: {formatValue(firstValue)}</span>
                    <span>last: {formatValue(lastValue)}</span>
                </div>
            </div>

            <svg
                className='resultsChartSvg'
                viewBox={`0 0 ${width} ${height}`}
                preserveAspectRatio='none'
            >
                <rect x='0' y='0' width={width} height={height} fill='transparent' />

                {zeroGuide && (
                    <path
                        d={zeroGuide}
                        fill='none'
                        stroke='rgba(255,255,255,0.2)'
                        strokeWidth='1'
                        vectorEffect='non-scaling-stroke'
                    />
                )}

                <path
                    d={linePath}
                    fill='none'
                    stroke='#8ab4ff'
                    strokeWidth='2'
                    vectorEffect='non-scaling-stroke'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                />
            </svg>
        </div>
    )
}

function StatisticsGroup({ rows, evaluationCriteriaByLabel }) {
    return (
        <div className='statisticsGroup'>
            <table className='statisticsTable'>
                <tbody>
                    {rows.map((row) => {
                        const evaluationCriterion = evaluationCriteriaByLabel?.[row.label] || null

                        return (
                        <tr key={row.label}>
                            <td className='statisticsLabel'>
                                <div className='statisticsLabelMain'>{row.label}</div>
                                {evaluationCriterion && (
                                    <div className='statisticsLabelMeta'>
                                        {evaluationCriterion.contributionPoints.toFixed(1)}
                                        /
                                        {evaluationCriterion.maxContributionPoints.toFixed(1)}
                                    </div>
                                )}
                            </td>
                            <td className={`statisticsValue ${row.tone || 'neutral'}`}>{row.value}</td>
                        </tr>
                        )
                    })}
                </tbody>
            </table>
        </div>
    )
}

function StatisticsDocTable({ rows = [] }) {
    if (!Array.isArray(rows) || rows.length === 0) {
        return <div className='statisticsEmpty'>No field documentation is available for this section yet.</div>
    }

    return (
        <div className='statisticsGroup'>
            <table className='statisticsTable statisticsDocTable'>
                <thead>
                    <tr>
                        <th>Field</th>
                        <th>Explanation</th>
                        <th>Recommended</th>
                        <th>Formula</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr key={row.field}>
                            <td className='statisticsDocField'>{row.field}</td>
                            <td>{row.explanation || '-'}</td>
                            <td>{row.recommended || '-'}</td>
                            <td className='statisticsDocFormula'>{row.formula || '-'}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

function StatisticsOperationsPane({
    authToken = '',
    strategyApplyResponse = null,
    onResolveLoadedBacktestResponse = null,
    onLogEvent,
}) {
    const activeSnapshotKey = String(strategyApplyResponse?.snapshot_key || '').trim()
    const [state, setState] = useState({
        status: 'idle',
        error: '',
        rows: [],
    })

    useEffect(() => {
        setState({
            status: 'idle',
            error: '',
            rows: [],
        })
    }, [activeSnapshotKey])

    const handleLoadOperations = useCallback(async () => {
        setState((current) => ({
            ...current,
            status: 'loading',
            error: '',
        }))

        try {
            let resolvedResponse = null
            const expectedSnapshotKey = activeSnapshotKey

            if (typeof onResolveLoadedBacktestResponse === 'function') {
                resolvedResponse = await onResolveLoadedBacktestResponse()
            }

            const resolvedRows = Array.isArray(resolvedResponse?.results) ? resolvedResponse.results : []
            if (!resolvedRows.length && Array.isArray(strategyApplyResponse?.results) && strategyApplyResponse.results.length) {
                resolvedResponse = strategyApplyResponse
            }

            if (
                (!resolvedResponse || !Array.isArray(resolvedResponse?.results) || !resolvedResponse.results.length)
                && authToken
            ) {
                const response = await fetch(buildApiUrl('/strategy/backtest-jobs/latest?status=completed'), {
                    headers: {
                        Authorization: `Bearer ${authToken}`,
                    },
                })
                const data = await readJsonResponse(response)

                if (!response.ok || data?.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, 'Failed to load backtest operations.'))
                }

                const latestJobResponse = data?.job?.result || null
                const latestSnapshotKey = String(latestJobResponse?.snapshot_key || '').trim()
                if (expectedSnapshotKey && latestSnapshotKey && latestSnapshotKey !== expectedSnapshotKey) {
                    throw new Error('The current backtest details are no longer cached. Re-run the backtest to inspect its operation rows.')
                }

                resolvedResponse = latestJobResponse
            }

            const operationRows = buildBacktestOperationsRows(resolvedResponse)

            if (!operationRows.length) {
                throw new Error('No completed trade rows are available for the currently loaded backtest.')
            }

            setState({
                status: 'ready',
                error: '',
                rows: operationRows,
            })
            onLogEvent?.(`Results · Loaded ${operationRows.length} backtest operation rows.`)
        } catch (error) {
            setState({
                status: 'error',
                error: String(error?.message || 'Failed to load backtest operations.'),
                rows: [],
            })
            onLogEvent?.(`Results operations load failed: ${error?.message || 'Failed to load backtest operations.'}`)
        }
    }, [activeSnapshotKey, authToken, onLogEvent, onResolveLoadedBacktestResponse, strategyApplyResponse])

    if (state.status === 'idle') {
        return (
            <div className='statisticsOperationsPanel'>
                <div className='statisticsGroupDescription'>
                    <div className='statisticsGroupDescriptionText'>
                        Detailed operation rows stay out of the default Results payload to avoid inflating routine backtest loads.
                    </div>
                    <div className='statisticsGroupDescriptionMeta'>
                        Load them on demand when you want to inspect each completed trade.
                    </div>
                </div>
                <div className='resultsExportActions'>
                    <button type='button' className='resultsActionButton' onClick={() => void handleLoadOperations()}>
                        Load operations
                    </button>
                </div>
            </div>
        )
    }

    if (state.status === 'loading') {
        return <div className='statisticsEmpty'>Loading backtest operations...</div>
    }

    if (state.status === 'error') {
        return (
            <div className='statisticsOperationsPanel'>
                <div className='statisticsEmpty'>{state.error}</div>
                <div className='resultsExportActions'>
                    <button type='button' className='resultsActionButton' onClick={() => void handleLoadOperations()}>
                        Retry
                    </button>
                </div>
            </div>
        )
    }

    return (
        <div className='statisticsOperationsPanel'>
            <div className='statisticsOperationsMeta'>
                <span>{state.rows.length.toLocaleString('en-US')} operations loaded</span>
            </div>
            <div className='statisticsOperationsTableWrap'>
                <table className='statisticsTable statisticsOperationsTable'>
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>TF</th>
                            <th>Side</th>
                            <th>Opened</th>
                            <th>Closed</th>
                            <th>Entry</th>
                            <th>Exit</th>
                            <th>Gross</th>
                            <th>Net</th>
                            <th>Cost</th>
                            <th>Cost details</th>
                        </tr>
                    </thead>
                    <tbody>
                        {state.rows.map((row) => (
                            <tr key={row.id}>
                                <td>{row.symbol}</td>
                                <td>{row.timeframe}</td>
                                <td>{row.side}</td>
                                <td>{formatDateTime(row.entryTime)}</td>
                                <td>{formatDateTime(row.exitTime)}</td>
                                <td>{formatMoney(row.entryPrice)}</td>
                                <td>{formatMoney(row.exitPrice)}</td>
                                <td className={`statisticsValue ${toFiniteNumber(row.grossPnl) > 0 ? 'positive' : toFiniteNumber(row.grossPnl) < 0 ? 'negative' : ''}`.trim()}>
                                    {formatSignedMoney(row.grossPnl)}
                                </td>
                                <td className={`statisticsValue ${toFiniteNumber(row.netPnl) > 0 ? 'positive' : toFiniteNumber(row.netPnl) < 0 ? 'negative' : ''}`.trim()}>
                                    {formatSignedMoney(row.netPnl)}
                                </td>
                                <td className={`statisticsValue ${toFiniteNumber(row.cost) > 0 ? 'negative' : ''}`.trim()}>
                                    {formatSignedMoney(row.cost)}
                                </td>
                                <td title={buildCostBreakdownTooltip(row.costBreakdown)}>
                                    {buildCostBreakdownInline(row.costBreakdown)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

function getRegimeInsightModel(regimeSummary = [], regimeStabilitySummary = []) {
    const stabilityRows = (regimeStabilitySummary || []).flatMap((summary) => summary?.rows || [])
    const matureRow = stabilityRows.find((row) => row.bucket_label === 'mature') || null
    const fragileRow = stabilityRows.find((row) => row.bucket_label === 'fragile') || null

    const rankedStabilityRows = [...stabilityRows]
        .filter((row) => Number.isFinite(Number(row?.trade_count)) && Number(row.trade_count) > 0)
        .sort((left, right) => Number(right?.avg_trade_net_pnl || 0) - Number(left?.avg_trade_net_pnl || 0))

    const bestStabilityRow = rankedStabilityRows[0] || null
    const worstStabilityRow = rankedStabilityRows[rankedStabilityRows.length - 1] || null

    const regimeRows = (regimeSummary || []).flatMap((summary) => summary?.rows || [])
    const dominantRegimeRow = [...regimeRows]
        .filter((row) => Number.isFinite(Number(row?.trade_count)) && Number(row.trade_count) > 0)
        .sort((left, right) => Number(right?.trade_count || 0) - Number(left?.trade_count || 0))[0] || null

    let headline = 'No strong regime-stability pattern was detected yet.'
    if (matureRow && fragileRow) {
        const matureAvg = Number(matureRow.avg_trade_net_pnl || 0)
        const fragileAvg = Number(fragileRow.avg_trade_net_pnl || 0)

        if (matureAvg > fragileAvg) {
            headline = 'This strategy behaves better when the regime is mature than when it is fragile.'
        } else if (matureAvg < fragileAvg) {
            headline = 'This strategy is not improving with mature regimes yet, which is worth investigating.'
        }
    }

    return {
        headline,
        bestStabilityRow,
        worstStabilityRow,
        dominantRegimeRow,
    }
}

function RegimeSummaryPane({ regimeSummary = [], regimeStabilitySummary = [], chartSettings = null }) {
    const hasRegimeSummary = Array.isArray(regimeSummary) && regimeSummary.length > 0
    const hasStabilitySummary = Array.isArray(regimeStabilitySummary) && regimeStabilitySummary.length > 0
    const insight = getRegimeInsightModel(regimeSummary, regimeStabilitySummary)

    if (!hasRegimeSummary && !hasStabilitySummary) {
        return <div className='statisticsEmpty'>No regime-aware trade summary is available for this run.</div>
    }

    return (
        <div className='regimeSummaryPanel'>
            <div className='regimeInsightBanner'>{insight.headline}</div>

            <div className='regimeInsightGrid'>
                <div className='regimeInsightCard'>
                    <div className='regimeInsightLabel'>Best stability bucket</div>
                    <div className='regimeInsightValue'>{insight.bestStabilityRow?.bucket_label || '-'}</div>
                    <div className='regimeInsightMeta'>
                        Avg trade: {insight.bestStabilityRow ? formatSignedMoney(insight.bestStabilityRow.avg_trade_net_pnl) : '-'}
                    </div>
                </div>

                <div className='regimeInsightCard'>
                    <div className='regimeInsightLabel'>Worst stability bucket</div>
                    <div className='regimeInsightValue'>{insight.worstStabilityRow?.bucket_label || '-'}</div>
                    <div className='regimeInsightMeta'>
                        Avg trade: {insight.worstStabilityRow ? formatSignedMoney(insight.worstStabilityRow.avg_trade_net_pnl) : '-'}
                    </div>
                </div>

                <div className='regimeInsightCard'>
                    <div className='regimeInsightLabel'>Most traded regime</div>
                    <div className='regimeInsightValue'>{insight.dominantRegimeRow?.regime_label || '-'}</div>
                    <div className='regimeInsightMeta'>
                        Trades: {insight.dominantRegimeRow ? formatInteger(insight.dominantRegimeRow.trade_count) : '-'}
                    </div>
                </div>
            </div>

            {hasRegimeSummary && regimeSummary.map((summary) => (
                <div key={summary.column} className='regimeSummaryGroup'>
                    <div className='regimeSummaryTitle'>{getDisplayFeatureLabel(summary.column, chartSettings)}</div>
                    <table className='statisticsTable'>
                        <thead>
                            <tr>
                                <th>Regime</th>
                                <th>Trades</th>
                                <th>Win rate</th>
                                <th>Net PnL</th>
                                <th>Avg trade</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(summary.rows || []).map((row) => (
                                <tr key={`${summary.column}:${row.regime_code}`}>
                                    <td>{row.regime_label}</td>
                                    <td>{formatInteger(row.trade_count)}</td>
                                    <td>{formatPercent(row.win_rate)}</td>
                                    <td className={`statisticsValue ${getTone(row.net_pnl)}`}>{formatSignedMoney(row.net_pnl)}</td>
                                    <td className={`statisticsValue ${getTone(row.avg_trade_net_pnl)}`}>{formatSignedMoney(row.avg_trade_net_pnl)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ))}

            {hasStabilitySummary && regimeStabilitySummary.map((summary) => (
                <div key={summary.column} className='regimeSummaryGroup'>
                    <div className='regimeSummaryTitle'>{getDisplayFeatureLabel(summary.column, chartSettings)} buckets</div>
                    <table className='statisticsTable'>
                        <thead>
                            <tr>
                                <th>Bucket</th>
                                <th>Range</th>
                                <th>Trades</th>
                                <th>Avg stability</th>
                                <th>Win rate</th>
                                <th>Net PnL</th>
                                <th>Avg trade</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(summary.rows || []).map((row) => (
                                <tr key={`${summary.column}:${row.bucket_label}`}>
                                    <td>{row.bucket_label}</td>
                                    <td>{row.bucket_range}</td>
                                    <td>{formatInteger(row.trade_count)}</td>
                                    <td>{formatValue(row.avg_stability_score, 2)}</td>
                                    <td>{formatPercent(row.win_rate)}</td>
                                    <td className={`statisticsValue ${getTone(row.net_pnl)}`}>{formatSignedMoney(row.net_pnl)}</td>
                                    <td className={`statisticsValue ${getTone(row.avg_trade_net_pnl)}`}>{formatSignedMoney(row.avg_trade_net_pnl)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ))}
        </div>
    )
}

function PresetComparisonPane({
    authToken,
    chartSettings,
    strategyApplyResponse,
    onApplyStrategyPreset,
    onOpenStrategy,
    onLogEvent,
    initialState,
    onStudyComplete,
    sharedConsoleJobs = null,
    onSharedConsoleJobChange,
}) {
    const [selectedSide, setSelectedSide] = useState('all')
    const selectedInitialState = useMemo(
        () => initialState?.[selectedSide]?.payload || buildDefaultPresetComparisonState(),
        [initialState, selectedSide],
    )
    const [comparisonState, setComparisonState] = useSourcedState(selectedInitialState)
    const requestAbortControllerRef = useRef(null)

    const researchChartSettings = useMemo(
        () => buildResearchChartSettings(chartSettings, strategyApplyResponse),
        [chartSettings, strategyApplyResponse]
    )
    const tokenCandidates = useMemo(
        () => getStrategyTokenCandidates(researchChartSettings),
        [researchChartSettings]
    )
    const marketRegimePresets = useMemo(
        () => buildMarketRegimePresetModel(tokenCandidates),
        [tokenCandidates]
    )

    const sidePresets = selectedSide === 'short'
        ? (marketRegimePresets?.shortEntries || [])
        : (marketRegimePresets?.longEntries || [])
    const recommendation = buildMarketRegimePresetRecommendation(strategyApplyResponse, sidePresets)
    const bestComparison = comparisonState.comparisons.find((entry) => entry.id === comparisonState.bestPresetId) || null
    const bestStudyComparison = comparisonState.study?.comparisons?.find((entry) => entry.id === comparisonState.study?.best_preset_id) || null
    const isSharedRunning = sharedConsoleJobs?.presetCompare?.status === 'running'
    const recommendationMatchesResult = bestComparison && recommendation?.preset
        ? bestComparison.id === recommendation.preset.id
        : null

    const researchVerdict = useMemo(() => {
        if (bestStudyComparison?.consistency?.window_count) {
            const consistency = bestStudyComparison.consistency
            const ratio = Number(consistency.win_ratio_vs_baseline || 0)
            if (ratio >= 0.67) {
                return `Most robust candidate so far: ${bestStudyComparison.label}. It beat the current strategy in ${Number(consistency.wins_vs_baseline || 0)} of ${Number(consistency.window_count || 0)} study windows.`
            }
            if (ratio <= 0.34) {
                return `The current strategy is still stronger than the tested presets across most study windows.`
            }
            return `${bestStudyComparison.label} looks promising, but the study is still mixed across windows.`
        }

        if (bestComparison) {
            return `Best measured preset in the current context: ${bestComparison.label}.`
        }

        return null
    }, [bestComparison, bestStudyComparison])

    function handleApplyBestPreset() {
        if (!bestComparison) {
            return
        }

        const preset = sidePresets.find((entry) => entry.id === bestComparison.id)
        if (!preset) {
            return
        }

        const nextStrategy = buildStrategyFromMarketRegimePreset(selectedSide, preset, buildBlankStrategy())
        onApplyStrategyPreset?.(nextStrategy)
        onOpenStrategy?.()
        onLogEvent?.(`Results · Applied best measured ${selectedSide} Market Regime preset: ${preset.label}.`)
    }

    function handleCancelCompare() {
        requestAbortControllerRef.current?.abort()
        requestAbortControllerRef.current = null
        setComparisonState((current) => ({
            ...current,
            loading: false,
            studyLoading: false,
        }))
        onSharedConsoleJobChange?.('presetCompare', null)
        onLogEvent?.('Results · Preset compare canceled.')
    }

    function buildStudyWindows() {
        const currentRows = Math.max(
            0,
            Number(strategyApplyResponse?.rows || strategyApplyResponse?.results?.length || 0),
        )

        if (currentRows <= 0) {
            return []
        }

        const candidates = [
            Math.max(100, Math.round(currentRows * 0.25)),
            Math.max(200, Math.round(currentRows * 0.5)),
            currentRows,
        ]

        return [...new Set(candidates.map((value) => Math.max(1, Number(value) || 1)))]
    }

    async function handleCompare({ includeStudy = false } = {}) {
        if (!authToken || !marketRegimePresets) {
            return
        }

        let studyWindows = []
        if (includeStudy) {
            studyWindows = buildStudyWindows()
        }

        setComparisonState({
            ...buildDefaultPresetComparisonState(),
            loading: true,
            studyLoading: includeStudy,
        })
        const abortController = new AbortController()
        abortController.jobId = null
        requestAbortControllerRef.current = abortController
        onSharedConsoleJobChange?.('presetCompare', {
            status: 'running',
            label: includeStudy ? 'Comparing presets and running study' : 'Comparing presets',
            startedAt: new Date().toISOString(),
            side: selectedSide,
            actor: 'research',
        })

        const baselinePayload = buildResearchBaselinePayload(researchChartSettings, strategyApplyResponse)
        const presets = sidePresets.map((preset) => {
            const strategy = buildBlankStrategy()
            strategy[selectedSide].openIf = preset.openIf
            strategy[selectedSide].closeIf = preset.closeIf
            strategy[selectedSide].gainPrice = preset.gainPrice
            strategy[selectedSide].lossPrice = preset.lossPrice
            strategy[selectedSide].trailingPrice = preset.trailingPrice

            return {
                id: preset.id,
                label: preset.label,
                strategy: resolveStrategyAliasesInStrategy(strategy, researchChartSettings),
            }
        })

        try {
            const payload = await runPresetCompareResearchJob({
                authToken,
                abortController,
                requestPayload: {
                    backtest: strategyApplyResponse?.request?.backtest || null,
                    baseline: baselinePayload,
                    presets,
                    chartContext: buildStudyChartContext(chartSettings, strategyApplyResponse),
                    ...(studyWindows.length > 0 ? { studyWindows } : {}),
                },
                onJobUpdate: (job) => {
                    onSharedConsoleJobChange?.('presetCompare', {
                        status: 'running',
                        label: job?.phase_label || (includeStudy ? 'Comparing presets and running study' : 'Comparing presets'),
                        detail: job?.detail || '',
                        startedAt: new Date().toISOString(),
                        side: selectedSide,
                        actor: 'research',
                    })
                },
            })

            const nextState = {
                loading: false,
                error: '',
                baseline: payload?.baseline || null,
                comparisons: Array.isArray(payload?.comparisons) ? payload.comparisons : [],
                bestPresetId: String(payload?.best_preset_id || ''),
                studyLoading: false,
                study: payload?.study || null,
            }
            setComparisonState(nextState)
            onStudyComplete?.('preset_compare', selectedSide, nextState)
            requestAbortControllerRef.current = null
            onSharedConsoleJobChange?.('presetCompare', null)
            if (studyWindows.length > 0 && payload?.study) {
                onLogEvent?.(`Results · Compared ${selectedSide} Market Regime presets and studied ${studyWindows.length} windows.`)
            } else {
                onLogEvent?.(`Results · Compared ${selectedSide} Market Regime presets.`)
            }
        } catch (error) {
            if (error?.name === 'AbortError') {
                setComparisonState((current) => ({
                    ...current,
                    loading: false,
                    studyLoading: false,
                }))
                requestAbortControllerRef.current = null
                onSharedConsoleJobChange?.('presetCompare', null)
                return
            }
            setComparisonState({
                ...buildDefaultPresetComparisonState(),
                loading: false,
                error: error?.message || (includeStudy ? 'Failed to run preset study.' : 'Failed to compare presets.'),
            })
            requestAbortControllerRef.current = null
            onSharedConsoleJobChange?.('presetCompare', null)
            onLogEvent?.(`Results preset comparison failed: ${error?.message || (includeStudy ? 'Failed to run preset study.' : 'Failed to compare presets.')}`)
        }
    }

    if (!marketRegimePresets) {
        return <div className='presetCompareEmpty'>Add a Market Regime feature with alias tokens to compare presets here.</div>
    }

    return (
        <div className='presetComparePanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetCompareTitle'>Market Regime preset compare</div>
                    <div className='presetCompareMeta'>Run the built-in `{selectedSide}` presets against the current backtest context. When enough rows are available, the consistency study is generated automatically too.</div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={() => handleCompare({ includeStudy: true })}
                    disabled={!authToken || comparisonState.loading || comparisonState.studyLoading || isSharedRunning}
                >
                    {comparisonState.loading || comparisonState.studyLoading || isSharedRunning ? 'Comparing...' : 'Compare presets'}
                </button>
                {comparisonState.loading || comparisonState.studyLoading || isSharedRunning ? (
                    <button
                        type='button'
                        className='resultsActionButton'
                        onClick={handleCancelCompare}
                    >
                        Cancel
                    </button>
                ) : null}
            </div>

            <DiscreetProgressBar
                active={comparisonState.loading || comparisonState.studyLoading || isSharedRunning}
            />

            <div className='presetCompareSideTabs'>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'long' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('long')}
                >
                    Long
                </button>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'short' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('short')}
                >
                    Short
                </button>
            </div>

            {recommendation ? (
                <div className='presetCompareRecommendation'>
                    Suggested {selectedSide} preset: <strong>{recommendation.preset.label}</strong>
                    {' · '}
                    {recommendation.reason}
                </div>
            ) : null}

            {comparisonState.baseline?.summary ? (
                <div className='presetCompareBaseline'>
                    <div className='presetCompareBaselineTitle'>
                        Baseline: {comparisonState.baseline.label}
                        {getCompareStrategyCount(comparisonState.baseline) > 1 ? (
                            <span className='presetCompareInlineMeta'>
                                Portfolio · {getCompareStrategyCount(comparisonState.baseline)} strategies
                            </span>
                        ) : null}
                    </div>
                    <div className='presetCompareMetrics'>
                        <div><span>Net PnL</span><strong>{formatPresetMetric(comparisonState.baseline.summary.net_pnl)}</strong></div>
                        <div><span>Win rate</span><strong>{formatPresetMetric(comparisonState.baseline.summary.win_rate, 'percent')}</strong></div>
                        <div><span>Avg trade</span><strong>{formatPresetMetric(comparisonState.baseline.summary.expectancy_per_trade)}</strong></div>
                        <div><span>Max DD</span><strong>{formatPresetMetric(comparisonState.baseline.summary.max_drawdown)}</strong></div>
                        <div><span>Trades</span><strong>{formatPresetMetric(comparisonState.baseline.summary.n_trades, 'integer')}</strong></div>
                    </div>
                    {getPortfolioContributionPreview(comparisonState.baseline) ? (
                        <div className='presetCompareContributionPreview'>
                            Top contribution: {getPortfolioContributionPreview(comparisonState.baseline)}
                        </div>
                    ) : null}
                </div>
            ) : null}

            {bestComparison ? (
                <div className='presetCompareRecommendation'>
                    Best measured {selectedSide} preset: <strong>{bestComparison.label}</strong>
                    {' · '}
                    {recommendationMatchesResult === null
                        ? 'Comparison completed for the current side.'
                        : recommendationMatchesResult
                            ? 'This matches the current heuristic recommendation.'
                            : 'This differs from the heuristic recommendation, so the context is worth reviewing more closely.'}
                </div>
            ) : null}

            {researchVerdict ? (
                <div className='presetCompareRecommendation'>
                    <strong>Research verdict:</strong>
                    {' '}
                    {researchVerdict}
                </div>
            ) : null}

            {bestComparison ? (
                <div className='presetCompareActions'>
                    <button
                        type='button'
                        className='resultsActionButton'
                        onClick={handleApplyBestPreset}
                    >
                        Apply best preset to Strategy
                    </button>
                </div>
            ) : null}

            {comparisonState.error ? (
                <div className='presetCompareError'>{comparisonState.error}</div>
            ) : null}

            {comparisonState.comparisons.length > 0 ? (
                <div className='presetCompareCards'>
                    {comparisonState.comparisons.map((entry) => {
                        const summary = entry?.summary || {}
                        const isBest = comparisonState.bestPresetId === entry.id
                        return (
                            <div key={entry.id} className={`presetCompareCard ${isBest ? 'isBest' : ''}`}>
                                <div className='presetCompareCardHeader'>
                                    <div className='presetCompareCardTitle'>{entry.label}</div>
                                    <div className='promotionCandidateBadges'>
                                        {getCompareStrategyCount(entry) > 1 ? (
                                            <div className='presetCompareBadge isPortfolio'>
                                                Portfolio · {getCompareStrategyCount(entry)}
                                            </div>
                                        ) : null}
                                        {isBest ? <div className='presetCompareBadge'>Best</div> : null}
                                    </div>
                                </div>
                                <div className='presetCompareMetrics'>
                                    <div><span>Net PnL</span><strong>{formatPresetMetric(summary.net_pnl)}</strong></div>
                                    <div><span>Win rate</span><strong>{formatPresetMetric(summary.win_rate, 'percent')}</strong></div>
                                    <div><span>Avg trade</span><strong>{formatPresetMetric(summary.expectancy_per_trade)}</strong></div>
                                    <div><span>Max DD</span><strong>{formatPresetMetric(summary.max_drawdown)}</strong></div>
                                    <div><span>Trades</span><strong>{formatPresetMetric(summary.n_trades, 'integer')}</strong></div>
                                </div>
                                {getPortfolioContributionPreview(entry) ? (
                                    <div className='presetCompareContributionPreview'>
                                        Top contribution: {getPortfolioContributionPreview(entry)}
                                    </div>
                                ) : null}
                                {entry?.delta_vs_baseline ? (
                                    <div className='presetCompareDeltaGrid'>
                                        <div><span>dPnL</span><strong>{formatPresetMetric(entry.delta_vs_baseline.net_pnl)}</strong></div>
                                        <div><span>dWin</span><strong>{formatPresetMetric(entry.delta_vs_baseline.win_rate, 'percent')}</strong></div>
                                        <div><span>dAvg</span><strong>{formatPresetMetric(entry.delta_vs_baseline.expectancy_per_trade)}</strong></div>
                                        <div><span>dDD</span><strong>{formatPresetMetric(entry.delta_vs_baseline.max_drawdown)}</strong></div>
                                        <div><span>dTrades</span><strong>{formatPresetMetric(entry.delta_vs_baseline.n_trades, 'integer')}</strong></div>
                                    </div>
                                ) : null}
                            </div>
                        )
                    })}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No preset comparison has been run yet for this result.</div>
            )}

            {comparisonState.study?.comparisons?.length > 0 ? (
                <div className='presetStudyPanel'>
                    <div className='presetStudyTitle'>Consistency study</div>
                    <div className='presetStudyMeta'>
                        Windows tested: {(comparisonState.study.windows || []).map((bars) => `${Number(bars).toLocaleString()} bars`).join(' · ')}
                    </div>
                    <div className='presetStudyCards'>
                        {comparisonState.study.comparisons.map((entry) => {
                            const consistency = entry?.consistency || {}
                            const isBestStudy = comparisonState.study?.best_preset_id === entry.id
                            return (
                                <div key={`study-${entry.id}`} className={`presetStudyCard ${isBestStudy ? 'isBest' : ''}`}>
                                    <div className='presetCompareCardHeader'>
                                        <div className='presetCompareCardTitle'>{entry.label}</div>
                                        {isBestStudy ? <div className='presetCompareBadge'>Most consistent</div> : null}
                                    </div>
                                    <div className='presetCompareMetrics'>
                                        <div><span>Wins vs baseline</span><strong>{`${Number(consistency.wins_vs_baseline || 0)}/${Number(consistency.window_count || 0)}`}</strong></div>
                                        <div><span>Consistency</span><strong>{formatPresetMetric(consistency.win_ratio_vs_baseline, 'percent')}</strong></div>
                                        <div><span>Avg dPnL</span><strong>{formatPresetMetric(consistency.avg_delta_net_pnl)}</strong></div>
                                        <div><span>Avg dTrade</span><strong>{formatPresetMetric(consistency.avg_delta_expectancy)}</strong></div>
                                        <div><span>Avg dDD</span><strong>{formatPresetMetric(consistency.avg_delta_drawdown)}</strong></div>
                                    </div>
                                    <div className='presetStudyWindowTable'>
                                        {(entry.windows || []).map((windowEntry) => (
                                            <div key={`${entry.id}-${windowEntry.bars}`} className='presetStudyWindowRow'>
                                                <span>{Number(windowEntry.bars || 0).toLocaleString()} bars</span>
                                                <strong>{formatPresetMetric(windowEntry?.delta_vs_baseline?.net_pnl)}</strong>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </div>
            ) : null}
        </div>
    )
}

function TimeframeStudyPane({
    authToken,
    chartSettings,
    strategyApplyResponse,
    onLogEvent,
    initialState,
    onStudyComplete,
    sharedConsoleJobs = null,
    onSharedConsoleJobChange,
}) {
    const [selectedSide, setSelectedSide] = useState('long')
    const selectedInitialState = useMemo(
        () => initialState?.[selectedSide]?.payload || buildDefaultSimpleStudyState(),
        [initialState, selectedSide],
    )
    const [studyState, setStudyState] = useSourcedState(selectedInitialState)
    const isSharedRunning = sharedConsoleJobs?.timeframeStudy?.status === 'running'
    const requestAbortControllerRef = useRef(null)

    const researchChartSettings = useMemo(
        () => buildResearchChartSettings(chartSettings, strategyApplyResponse),
        [chartSettings, strategyApplyResponse]
    )
    const tokenCandidates = useMemo(
        () => getStrategyTokenCandidates(researchChartSettings),
        [researchChartSettings]
    )
    const marketRegimePresets = useMemo(
        () => buildMarketRegimePresetModel(tokenCandidates),
        [tokenCandidates]
    )

    const sidePresets = selectedSide === 'short'
        ? (marketRegimePresets?.shortEntries || [])
        : (marketRegimePresets?.longEntries || [])

    const timeframeOptions = useMemo(() => {
        const current = String(chartSettings?.timeframe || '').trim().toUpperCase()
        const base = ['M1', 'M5', 'M15', 'H1']
        return [...new Set([current, ...base].filter(Boolean))]
    }, [chartSettings])

    function handleCancelStudy() {
        requestAbortControllerRef.current?.abort()
        requestAbortControllerRef.current = null
        setStudyState((current) => ({
            ...current,
            loading: false,
        }))
        onSharedConsoleJobChange?.('timeframeStudy', null)
        onLogEvent?.('Research · Timeframe study canceled.')
    }

    async function handleRunStudy() {
        if (!authToken || !marketRegimePresets) {
            return
        }

        const baselinePayload = buildResearchBaselinePayload(researchChartSettings, strategyApplyResponse)
        const presets = sidePresets.map((preset) => {
            const strategy = buildBlankStrategy()
            strategy[selectedSide].openIf = preset.openIf
            strategy[selectedSide].closeIf = preset.closeIf
            strategy[selectedSide].gainPrice = preset.gainPrice
            strategy[selectedSide].lossPrice = preset.lossPrice
            strategy[selectedSide].trailingPrice = preset.trailingPrice

            return {
                id: preset.id,
                label: preset.label,
                strategy: resolveStrategyAliasesInStrategy(strategy, researchChartSettings),
            }
        })

        setStudyState({
            loading: true,
            error: '',
            study: null,
        })
        const abortController = new AbortController()
        abortController.jobId = null
        requestAbortControllerRef.current = abortController
        onSharedConsoleJobChange?.('timeframeStudy', {
            status: 'running',
            label: 'Running timeframe study',
            startedAt: new Date().toISOString(),
            side: selectedSide,
            actor: 'research',
        })

        try {
            const payload = await runPresetCompareResearchJob({
                authToken,
                abortController,
                requestPayload: {
                    backtest: strategyApplyResponse?.request?.backtest || null,
                    baseline: baselinePayload,
                    presets,
                    studyTimeframes: timeframeOptions,
                    chartContext: buildStudyChartContext(chartSettings, strategyApplyResponse),
                },
                onJobUpdate: (job) => {
                    onSharedConsoleJobChange?.('timeframeStudy', {
                        status: 'running',
                        label: job?.phase_label || 'Running timeframe study',
                        detail: job?.detail || '',
                        startedAt: new Date().toISOString(),
                        side: selectedSide,
                        actor: 'research',
                    })
                },
            })

            const nextState = {
                loading: false,
                error: '',
                study: payload?.timeframe_study || null,
            }
            setStudyState(nextState)
            onStudyComplete?.('timeframe_study', selectedSide, nextState)
            requestAbortControllerRef.current = null
            onSharedConsoleJobChange?.('timeframeStudy', null)
            onLogEvent?.(`Research · Ran ${selectedSide} timeframe study across ${timeframeOptions.join(', ')}.`)
        } catch (error) {
            if (error?.name === 'AbortError') {
                setStudyState((current) => ({
                    ...current,
                    loading: false,
                }))
                requestAbortControllerRef.current = null
                onSharedConsoleJobChange?.('timeframeStudy', null)
                return
            }
            setStudyState({
                loading: false,
                error: error?.message || 'Failed to run timeframe study.',
                study: null,
            })
            requestAbortControllerRef.current = null
            onSharedConsoleJobChange?.('timeframeStudy', null)
            onLogEvent?.(`Research timeframe study failed: ${error?.message || 'Failed to run timeframe study.'}`)
        }
    }

    if (!marketRegimePresets) {
        return <div className='presetCompareEmpty'>Add a Market Regime feature with alias tokens to run timeframe research here.</div>
    }

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Timeframe study</div>
                    <div className='presetStudyMeta'>
                        Compare the current strategy and the built-in Market Regime presets across multiple timeframes for the same symbol and feature stack.
                    </div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={handleRunStudy}
                    disabled={!authToken || studyState.loading || isSharedRunning}
                >
                    {studyState.loading || isSharedRunning ? 'Studying...' : 'Run timeframe study'}
                </button>
                {studyState.loading || isSharedRunning ? (
                    <button
                        type='button'
                        className='resultsActionButton'
                        onClick={handleCancelStudy}
                    >
                        Cancel
                    </button>
                ) : null}
            </div>

            <DiscreetProgressBar
                active={studyState.loading || isSharedRunning}
            />

            <div className='presetCompareSideTabs'>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'long' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('long')}
                >
                    Long
                </button>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'short' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('short')}
                >
                    Short
                </button>
            </div>

            <div className='presetStudyMeta'>
                Sweep: {timeframeOptions.join(' · ')}
            </div>

            {studyState.error ? (
                <div className='presetCompareError'>{studyState.error}</div>
            ) : null}

            {studyState.study?.comparisons?.length > 0 ? (
                <div className='presetStudyCards'>
                    {studyState.study.comparisons.map((entry) => {
                        const consistency = entry?.consistency || {}
                        const isBest = studyState.study?.best_preset_id === entry.id
                        return (
                            <div key={`tf-${entry.id}`} className={`presetStudyCard ${isBest ? 'isBest' : ''}`}>
                                <div className='presetCompareCardHeader'>
                                    <div className='presetCompareCardTitle'>{entry.label}</div>
                                    {isBest ? <div className='presetCompareBadge'>Most consistent</div> : null}
                                </div>
                                <div className='presetCompareMetrics'>
                                    <div><span>Wins vs baseline</span><strong>{`${Number(consistency.wins_vs_baseline || 0)}/${Number(consistency.timeframe_count || 0)}`}</strong></div>
                                    <div><span>Consistency</span><strong>{formatPresetMetric(consistency.win_ratio_vs_baseline, 'percent')}</strong></div>
                                    <div><span>Avg dPnL</span><strong>{formatPresetMetric(consistency.avg_delta_net_pnl)}</strong></div>
                                    <div><span>Avg dTrade</span><strong>{formatPresetMetric(consistency.avg_delta_expectancy)}</strong></div>
                                    <div><span>Avg dDD</span><strong>{formatPresetMetric(consistency.avg_delta_drawdown)}</strong></div>
                                </div>
                                <div className='presetStudyWindowTable'>
                                    {(entry.timeframes || []).map((timeframeEntry) => (
                                        <div key={`${entry.id}-${timeframeEntry.timeframe}`} className='presetStudyWindowRow'>
                                            <span>{timeframeEntry.timeframe}</span>
                                            <strong>{formatPresetMetric(timeframeEntry?.delta_vs_baseline?.net_pnl)}</strong>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )
                    })}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No timeframe study has been run yet.</div>
            )}
        </div>
    )
}

function SymbolStudyPane({
    authToken,
    chartSettings,
    strategyApplyResponse,
    onLogEvent,
    initialState,
    onStudyComplete,
    sharedConsoleJobs = null,
    onSharedConsoleJobChange,
}) {
    const [selectedSide, setSelectedSide] = useState('long')
    const selectedInitialState = useMemo(
        () => initialState?.[selectedSide]?.payload || buildDefaultSimpleStudyState(),
        [initialState, selectedSide],
    )
    const [studyState, setStudyState] = useSourcedState(selectedInitialState)
    const isSharedRunning = sharedConsoleJobs?.symbolStudy?.status === 'running'
    const requestAbortControllerRef = useRef(null)

    const researchChartSettings = useMemo(
        () => buildResearchChartSettings(chartSettings, strategyApplyResponse),
        [chartSettings, strategyApplyResponse]
    )
    const tokenCandidates = useMemo(
        () => getStrategyTokenCandidates(researchChartSettings),
        [researchChartSettings]
    )
    const marketRegimePresets = useMemo(
        () => buildMarketRegimePresetModel(tokenCandidates),
        [tokenCandidates]
    )

    const sidePresets = selectedSide === 'short'
        ? (marketRegimePresets?.shortEntries || [])
        : (marketRegimePresets?.longEntries || [])

    const symbolOptions = useMemo(() => {
        const current = String(chartSettings?.symbol || '').trim().toUpperCase()
        const base = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD']
        return [...new Set([current, ...base].filter(Boolean))]
    }, [chartSettings])

    function handleCancelStudy() {
        requestAbortControllerRef.current?.abort()
        requestAbortControllerRef.current = null
        setStudyState((current) => ({
            ...current,
            loading: false,
        }))
        onSharedConsoleJobChange?.('symbolStudy', null)
        onLogEvent?.('Research · Symbol study canceled.')
    }

    async function handleRunStudy() {
        if (!authToken || !marketRegimePresets) {
            return
        }

        const baselinePayload = buildResearchBaselinePayload(researchChartSettings, strategyApplyResponse)
        const presets = sidePresets.map((preset) => {
            const strategy = buildBlankStrategy()
            strategy[selectedSide].openIf = preset.openIf
            strategy[selectedSide].closeIf = preset.closeIf
            strategy[selectedSide].gainPrice = preset.gainPrice
            strategy[selectedSide].lossPrice = preset.lossPrice
            strategy[selectedSide].trailingPrice = preset.trailingPrice

            return {
                id: preset.id,
                label: preset.label,
                strategy: resolveStrategyAliasesInStrategy(strategy, researchChartSettings),
            }
        })

        setStudyState({
            loading: true,
            error: '',
            study: null,
        })
        const abortController = new AbortController()
        abortController.jobId = null
        requestAbortControllerRef.current = abortController
        onSharedConsoleJobChange?.('symbolStudy', {
            status: 'running',
            label: 'Running symbol study',
            startedAt: new Date().toISOString(),
            side: selectedSide,
            actor: 'research',
        })

        try {
            const payload = await runPresetCompareResearchJob({
                authToken,
                abortController,
                requestPayload: {
                    backtest: strategyApplyResponse?.request?.backtest || null,
                    baseline: baselinePayload,
                    presets,
                    studySymbols: symbolOptions,
                    chartContext: buildStudyChartContext(chartSettings, strategyApplyResponse),
                },
                onJobUpdate: (job) => {
                    onSharedConsoleJobChange?.('symbolStudy', {
                        status: 'running',
                        label: job?.phase_label || 'Running symbol study',
                        detail: job?.detail || '',
                        startedAt: new Date().toISOString(),
                        side: selectedSide,
                        actor: 'research',
                    })
                },
            })

            const nextState = {
                loading: false,
                error: '',
                study: payload?.symbol_study || null,
            }
            setStudyState(nextState)
            onStudyComplete?.('symbol_study', selectedSide, nextState)
            requestAbortControllerRef.current = null
            onSharedConsoleJobChange?.('symbolStudy', null)
            onLogEvent?.(`Research · Ran ${selectedSide} symbol study across ${symbolOptions.join(', ')}.`)
        } catch (error) {
            if (error?.name === 'AbortError') {
                setStudyState((current) => ({
                    ...current,
                    loading: false,
                }))
                requestAbortControllerRef.current = null
                onSharedConsoleJobChange?.('symbolStudy', null)
                return
            }
            setStudyState({
                loading: false,
                error: error?.message || 'Failed to run symbol study.',
                study: null,
            })
            requestAbortControllerRef.current = null
            onSharedConsoleJobChange?.('symbolStudy', null)
            onLogEvent?.(`Research symbol study failed: ${error?.message || 'Failed to run symbol study.'}`)
        }
    }

    if (!marketRegimePresets) {
        return <div className='presetCompareEmpty'>Add a Market Regime feature with alias tokens to run symbol research here.</div>
    }

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Symbol study</div>
                    <div className='presetStudyMeta'>
                        Compare the current strategy and the built-in Market Regime presets across multiple symbols on the same timeframe and feature stack.
                    </div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={handleRunStudy}
                    disabled={!authToken || studyState.loading || isSharedRunning}
                >
                    {studyState.loading || isSharedRunning ? 'Studying...' : 'Run symbol study'}
                </button>
                {studyState.loading || isSharedRunning ? (
                    <button
                        type='button'
                        className='resultsActionButton'
                        onClick={handleCancelStudy}
                    >
                        Cancel
                    </button>
                ) : null}
            </div>

            <DiscreetProgressBar
                active={studyState.loading || isSharedRunning}
            />

            <div className='presetCompareSideTabs'>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'long' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('long')}
                >
                    Long
                </button>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'short' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('short')}
                >
                    Short
                </button>
            </div>

            <div className='presetStudyMeta'>
                Sweep: {symbolOptions.join(' · ')}
            </div>

            {studyState.error ? (
                <div className='presetCompareError'>{studyState.error}</div>
            ) : null}

            {studyState.study?.comparisons?.length > 0 ? (
                <div className='presetStudyCards'>
                    {studyState.study.comparisons.map((entry) => {
                        const consistency = entry?.consistency || {}
                        const isBest = studyState.study?.best_preset_id === entry.id
                        return (
                            <div key={`symbol-${entry.id}`} className={`presetStudyCard ${isBest ? 'isBest' : ''}`}>
                                <div className='presetCompareCardHeader'>
                                    <div className='presetCompareCardTitle'>{entry.label}</div>
                                    {isBest ? <div className='presetCompareBadge'>Most consistent</div> : null}
                                </div>
                                <div className='presetCompareMetrics'>
                                    <div><span>Wins vs baseline</span><strong>{`${Number(consistency.wins_vs_baseline || 0)}/${Number(consistency.symbol_count || 0)}`}</strong></div>
                                    <div><span>Consistency</span><strong>{formatPresetMetric(consistency.win_ratio_vs_baseline, 'percent')}</strong></div>
                                    <div><span>Avg dPnL</span><strong>{formatPresetMetric(consistency.avg_delta_net_pnl)}</strong></div>
                                    <div><span>Avg dTrade</span><strong>{formatPresetMetric(consistency.avg_delta_expectancy)}</strong></div>
                                    <div><span>Avg dDD</span><strong>{formatPresetMetric(consistency.avg_delta_drawdown)}</strong></div>
                                </div>
                                <div className='presetStudyWindowTable'>
                                    {(entry.symbols || []).map((symbolEntry) => (
                                        <div key={`${entry.id}-${symbolEntry.symbol}`} className='presetStudyWindowRow'>
                                            <span>{symbolEntry.symbol}</span>
                                            <strong>{formatPresetMetric(symbolEntry?.delta_vs_baseline?.net_pnl)}</strong>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )
                    })}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No symbol study has been run yet.</div>
            )}
        </div>
    )
}

function WalkForwardPane({
    authToken,
    chartSettings,
    strategyApplyResponse,
    onLogEvent,
    initialState,
    onStudyComplete,
    sharedConsoleJobs = null,
    onSharedConsoleJobChange,
}) {
    const [selectedSide, setSelectedSide] = useState('long')
    const selectedInitialState = useMemo(
        () => initialState?.[selectedSide]?.payload || buildDefaultSimpleStudyState(),
        [initialState, selectedSide],
    )
    const [studyState, setStudyState] = useSourcedState(selectedInitialState)
    const isSharedRunning = sharedConsoleJobs?.walkforwardStudy?.status === 'running'
    const requestAbortControllerRef = useRef(null)

    const researchChartSettings = useMemo(
        () => buildResearchChartSettings(chartSettings, strategyApplyResponse),
        [chartSettings, strategyApplyResponse]
    )
    const tokenCandidates = useMemo(
        () => getStrategyTokenCandidates(researchChartSettings),
        [researchChartSettings]
    )
    const marketRegimePresets = useMemo(
        () => buildMarketRegimePresetModel(tokenCandidates),
        [tokenCandidates]
    )

    const sidePresets = selectedSide === 'short'
        ? (marketRegimePresets?.shortEntries || [])
        : (marketRegimePresets?.longEntries || [])

    const walkforwardTestBars = useMemo(() => {
        const currentRows = Math.max(
            0,
            Number(strategyApplyResponse?.rows || strategyApplyResponse?.results?.length || 0),
        )
        if (currentRows <= 0) {
            return null
        }
        return Math.max(100, Math.round(currentRows * 0.2))
    }, [strategyApplyResponse])

    const walkforwardTrainBars = useMemo(() => (
        walkforwardTestBars ? Math.max(walkforwardTestBars, Math.round(walkforwardTestBars * 2)) : null
    ), [walkforwardTestBars])

    const walkforwardWindowBars = walkforwardTestBars
    const walkforwardStepBars = walkforwardTestBars

    function handleCancelStudy() {
        requestAbortControllerRef.current?.abort()
        requestAbortControllerRef.current = null
        setStudyState((current) => ({
            ...current,
            loading: false,
        }))
        onSharedConsoleJobChange?.('walkforwardStudy', null)
        onLogEvent?.('Research · Walk-forward validation canceled.')
    }

    async function handleRunStudy() {
        if (!authToken || !marketRegimePresets) {
            return
        }

        if (!walkforwardWindowBars || !walkforwardTrainBars || !walkforwardTestBars) {
            setStudyState({
                loading: false,
                error: 'No backtest rows are available yet to build walk-forward validation.',
                study: null,
            })
            return
        }

        const baselinePayload = buildResearchBaselinePayload(researchChartSettings, strategyApplyResponse)
        const presets = sidePresets.map((preset) => {
            const strategy = buildBlankStrategy()
            strategy[selectedSide].openIf = preset.openIf
            strategy[selectedSide].closeIf = preset.closeIf
            strategy[selectedSide].gainPrice = preset.gainPrice
            strategy[selectedSide].lossPrice = preset.lossPrice
            strategy[selectedSide].trailingPrice = preset.trailingPrice

            return {
                id: preset.id,
                label: preset.label,
                strategy: resolveStrategyAliasesInStrategy(strategy, researchChartSettings),
            }
        })

        setStudyState({
            loading: true,
            error: '',
            study: null,
        })
        const abortController = new AbortController()
        abortController.jobId = null
        requestAbortControllerRef.current = abortController
        onSharedConsoleJobChange?.('walkforwardStudy', {
            status: 'running',
            label: 'Running walk-forward validation',
            startedAt: new Date().toISOString(),
            side: selectedSide,
            actor: 'research',
        })

        try {
            const payload = await runPresetCompareResearchJob({
                authToken,
                abortController,
                requestPayload: {
                    backtest: strategyApplyResponse?.request?.backtest || null,
                    baseline: baselinePayload,
                    presets,
                    walkforwardWindowBars,
                    walkforwardStepBars,
                    walkforwardTrainBars,
                    walkforwardTestBars,
                    chartContext: buildStudyChartContext(chartSettings, strategyApplyResponse),
                },
                onJobUpdate: (job) => {
                    onSharedConsoleJobChange?.('walkforwardStudy', {
                        status: 'running',
                        label: job?.phase_label || 'Running walk-forward validation',
                        detail: job?.detail || '',
                        startedAt: new Date().toISOString(),
                        side: selectedSide,
                        actor: 'research',
                    })
                },
            })

            const nextState = {
                loading: false,
                error: '',
                study: payload?.walkforward_study || null,
            }
            setStudyState(nextState)
            onStudyComplete?.('walkforward_study', selectedSide, nextState)
            requestAbortControllerRef.current = null
            onSharedConsoleJobChange?.('walkforwardStudy', null)
            onLogEvent?.(`Research · Ran ${selectedSide} walk-forward validation with ${walkforwardTrainBars} train bars and ${walkforwardTestBars} test bars.`)
        } catch (error) {
            if (error?.name === 'AbortError') {
                setStudyState((current) => ({
                    ...current,
                    loading: false,
                }))
                requestAbortControllerRef.current = null
                onSharedConsoleJobChange?.('walkforwardStudy', null)
                return
            }
            setStudyState({
                loading: false,
                error: error?.message || 'Failed to run walk-forward validation.',
                study: null,
            })
            requestAbortControllerRef.current = null
            onSharedConsoleJobChange?.('walkforwardStudy', null)
            onLogEvent?.(`Research walk-forward validation failed: ${error?.message || 'Failed to run walk-forward validation.'}`)
        }
    }

    if (!marketRegimePresets) {
        return <div className='presetCompareEmpty'>Add a Market Regime feature with alias tokens to run walk-forward validation here.</div>
    }

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Walk-forward validation</div>
                    <div className='presetStudyMeta'>
                        Compare the current strategy and Market Regime presets across sequential holdout slices of the current backtest context.
                    </div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={handleRunStudy}
                    disabled={!authToken || studyState.loading || isSharedRunning}
                >
                    {studyState.loading || isSharedRunning ? 'Validating...' : 'Run walk-forward'}
                </button>
                {studyState.loading || isSharedRunning ? (
                    <button
                        type='button'
                        className='resultsActionButton'
                        onClick={handleCancelStudy}
                    >
                        Cancel
                    </button>
                ) : null}
            </div>

            <DiscreetProgressBar
                active={studyState.loading || isSharedRunning}
            />

            <div className='presetCompareSideTabs'>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'long' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('long')}
                >
                    Long
                </button>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'short' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('short')}
                >
                    Short
                </button>
            </div>

            <div className='presetStudyMeta'>
                Train: {walkforwardTrainBars ? `${Number(walkforwardTrainBars).toLocaleString()} bars` : '-'}
                {' · '}
                Test: {walkforwardTestBars ? `${Number(walkforwardTestBars).toLocaleString()} bars` : '-'}
                {' · '}
                Step: {walkforwardStepBars ? `${Number(walkforwardStepBars).toLocaleString()} bars` : '-'}
            </div>

            {studyState.error ? (
                <div className='presetCompareError'>{studyState.error}</div>
            ) : null}

            {studyState.study?.comparisons?.length > 0 ? (
                <div className='presetStudyCards'>
                    {studyState.study.comparisons.map((entry) => {
                        const consistency = entry?.train_test_consistency || entry?.consistency || {}
                        const isBest = studyState.study?.best_preset_id === entry.id
                        return (
                            <div key={`split-${entry.id}`} className={`presetStudyCard ${isBest ? 'isBest' : ''}`}>
                                <div className='presetCompareCardHeader'>
                                    <div className='presetCompareCardTitle'>{entry.label}</div>
                                    {isBest ? <div className='presetCompareBadge'>Most robust</div> : null}
                                </div>
                                <div className='presetCompareMetrics'>
                                    <div><span>Wins vs baseline</span><strong>{`${Number((entry?.consistency || {}).wins_vs_baseline || 0)}/${Number((entry?.consistency || {}).pair_count || (entry?.consistency || {}).segment_count || 0)}`}</strong></div>
                                    <div><span>Pairs</span><strong>{Number(consistency.pair_count || (entry?.consistency || {}).pair_count || 0)}</strong></div>
                                    <div><span>Stable train/test</span><strong>{formatPresetMetric(consistency.stable_pair_ratio, 'percent')}</strong></div>
                                    <div><span>Avg train PnL</span><strong>{formatPresetMetric(consistency.avg_train_net_pnl)}</strong></div>
                                    <div><span>Avg test PnL</span><strong>{formatPresetMetric(consistency.avg_test_net_pnl)}</strong></div>
                                    <div><span>Train→test shift</span><strong>{formatPresetMetric(consistency.avg_train_to_test_net_pnl_shift)}</strong></div>
                                </div>
                                <div className='presetStudyWindowTable'>
                                    {(entry.pairs || entry.segments || []).map((segmentEntry) => (
                                        <div key={`${entry.id}-${segmentEntry.label}`} className='presetStudyWindowRow'>
                                            <span>{segmentEntry.label}</span>
                                            <strong>{formatPresetMetric(segmentEntry?.delta_vs_baseline?.net_pnl)}</strong>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )
                    })}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No walk-forward validation has been run yet.</div>
            )}
        </div>
    )
}

function buildPaperShortlistStorageKey(projectName = '') {
    const safeName = String(projectName || 'unsaved').trim() || 'unsaved'
    return `robotineeko.paper-shortlist.${safeName}`
}

function buildResearchDecisionLogStorageKey(projectName = '') {
    const safeName = String(projectName || 'unsaved').trim() || 'unsaved'
    return `robotineeko.research-decision-log.${safeName}`
}

function buildResearchStudiesStorageKey(projectName = '') {
    const safeName = String(projectName || 'unsaved').trim() || 'unsaved'
    return `robotineeko.research-studies.${safeName}`
}

function buildResearchStudyRunsStorageKey(projectName = '') {
    const safeName = String(projectName || 'unsaved').trim() || 'unsaved'
    return `robotineeko.research-study-runs.${safeName}`
}

function buildResearchBenchmarksStorageKey(projectName = '') {
    const safeName = String(projectName || 'unsaved').trim() || 'unsaved'
    return `robotineeko.research-benchmarks.${safeName}`
}

function buildResearchStudyRunEntry(type, side, payload, options = {}) {
    const now = new Date()
    const comparisons = Array.isArray(payload?.study?.comparisons)
        ? payload.study.comparisons
        : Array.isArray(payload?.comparisons)
            ? payload.comparisons
                : Array.isArray(payload?.result?.candidates)
                    ? payload.result.candidates
                    : []
    const strategySnapshot = cloneSerializable(
        options?.strategySnapshot
        || payload?.strategySnapshot
        || payload?.strategy_snapshot,
        null,
    )
    const bestId = String(
        payload?.study?.best_preset_id
        || payload?.bestPresetId
        || payload?.result?.bestCandidateId
        || ''
    )
    const bestEntry = comparisons.find((entry) => String(entry?.id || '') === bestId) || comparisons[0] || null
    return {
        id: `${type}:${side || 'na'}:${now.getTime()}:${Math.random().toString(36).slice(2, 8)}`,
        run_name: `${String(type || 'study').replaceAll('_', ' ')} · ${String(side || 'default').toUpperCase()} · ${now.toLocaleString()}`,
        version: `v${now.getTime()}`,
        type,
        side,
        at: now.toISOString(),
        atLabel: now.toLocaleString(),
        best_id: bestId,
        best_label: bestEntry?.label || '',
        comparison_count: comparisons.length,
        payload,
        strategy_snapshot: strategySnapshot,
    }
}

const RESEARCH_PLAYBOOK_SECTIONS = [
    {
        id: 'purpose',
        title: 'What This Area Is For',
        summary: 'Understand what the platform is optimizing for before you begin changing strategies.',
        blocks: [
            {
                type: 'paragraphs',
                items: [
                    'The Research area is not just a report viewer. It is the workflow that takes a strategy from an idea to evidence, then from evidence to paper tracking, promotion review, and eventually live readiness.',
                    'The goal is not to maximize a single backtest. The goal is to build a candidate that survives multiple checks: current context, history splits, timeframe shifts, symbol shifts, and walk-forward degradation.',
                ],
            },
            {
                type: 'bullets',
                title: 'Core principle',
                items: [
                    'Treat every strategy as guilty until it survives repeated validation.',
                    'Use the current backtest only as a starting clue, never as final proof.',
                    'Promote candidates because they are robust, not because they had one attractive run.',
                ],
            },
        ],
    },
    {
        id: 'prerequisites',
        title: 'What You Need Before Starting',
        summary: 'Minimum conditions for a research session that produces usable evidence.',
        blocks: [
            {
                type: 'bullets',
                title: 'Minimum setup',
                items: [
                    'A chart loaded with enough candles for the indicators and the strategy to stabilize.',
                    'A strategy that compiles and applies without unknown identifiers.',
                    'A backtest configuration with capital, spread, slippage and execution scope set intentionally.',
                    'At least one feature stack on the chart that reflects the strategy idea, not random decoration.',
                ],
            },
            {
                type: 'terms',
                title: 'Pre-flight checklist',
                rows: [
                    ['Symbol and timeframe', 'Choose the market you actually want to investigate first. Keep the first pass narrow.'],
                    ['Feature aliases', 'Use aliases in strategy conditions so the logic remains readable and resilient to manifest changes.'],
                    ['Execution costs', 'Always set spread and slippage before trusting any strategy result.'],
                    ['History scope', 'Use loaded chart candles when you want the test to match the visible context, or custom scope when running targeted studies.'],
                ],
            },
        ],
    },
    {
        id: 'workflow',
        title: 'Research Workflow',
        summary: 'Recommended sequence from idea to candidate promotion.',
        blocks: [
            {
                type: 'flowchart',
            },
            {
                type: 'steps',
                title: 'Recommended sequence',
                items: [
                    'Load the market and feature stack you want to investigate.',
                    'Write or apply a first strategy version in Strategy.',
                    'Run a backtest with explicit costs and verify the result in Results.',
                    'Open Research and compare presets, timeframes, symbols and walk-forward behavior.',
                    'Rank promotion candidates and move the best ones into the paper shortlist.',
                    'Track paper observations, failure modes, promotion review and live readiness.',
                ],
            },
        ],
    },
    {
        id: 'tools',
        title: 'Main Tools And When To Use Them',
        summary: 'Map the platform areas to the questions they answer.',
        blocks: [
            {
                type: 'terms',
                title: 'Console areas',
                rows: [
                    ['Chart', 'Use to inspect price context, feature alignment, volume and market structure.'],
                    ['Feature manager', 'Add indicators, including neural-derived indicators from their dedicated add-feature subtab, define aliases, and control what exists in the strategy namespace.'],
                    ['Neural', 'Train, test, compare and inspect neural network families and their run history.'],
                    ['Strategy', 'Define entry, close, gain, loss and trailing logic. This is where the trading rules live.'],
                    ['Backtester', 'Define execution assumptions: capital, spread, per-event slippage, history scope and refresh behavior.'],
                    ['Results', 'Inspect performance, evaluation, trades, export, and execution statistics.'],
                    ['Research', 'Run comparative studies, shortlist candidates, review paper evidence and decide promotion status.'],
                    ['Runtime', 'Audit causal alignment, refresh chains, invalidation reasons and consumer state.'],
                ],
            },
            {
                type: 'bullets',
                title: 'When to open Runtime',
                items: [
                    'When a strategy applies but the results look stale.',
                    'When the chart, strategy, results and neural panels seem out of sync.',
                    'When a study fails because market context was not ready or a rebuild path diverged.',
                ],
            },
        ],
    },
    {
        id: 'mreg',
        title: 'How To Use Market Regime',
        summary: 'What each MReg output means and how it should influence research decisions.',
        blocks: [
            {
                type: 'terms',
                title: 'MReg outputs',
                rows: [
                    ['mreg_regime_code', 'Discrete regime label. Use for hard filters like trend, volatile, range or compression states.'],
                    ['mreg_trend_score', 'How directional the market currently is. Higher values mean stronger structure.'],
                    ['mreg_direction_score', 'Directional tilt. Positive favors bullish context; negative favors bearish context.'],
                    ['mreg_compression_score', 'How compressed the market is. Useful for breakout and release logic.'],
                    ['mreg_stability_score', 'How confirmed and stable the regime currently is. Useful for filtering fragile transitions.'],
                    ['mreg_regime_age', 'How long the current regime has lasted. Useful for separating fresh transitions from mature states.'],
                ],
            },
            {
                type: 'bullets',
                title: 'Good uses',
                items: [
                    'Filter longs to bullish directional regimes.',
                    'Avoid acting in low-stability, newly forming contexts unless the strategy is specifically a transition strategy.',
                    'Use compression plus age and stability to search for cleaner breakout conditions.',
                ],
            },
        ],
    },
    {
        id: 'studies',
        title: 'Research Outputs Explained',
        summary: 'What each section in Research is for, what it outputs, and when to trust it.',
        blocks: [
            {
                type: 'terms',
                title: 'Research sections',
                rows: [
                    ['Preset compare', 'Compare built-in regime presets against the current strategy. Use early when testing whether MReg helps at all.'],
                    ['Timeframe study', 'Checks whether the same idea survives across M1, M5, M15, H1. Use to detect timeframe instability.'],
                    ['Symbol study', 'Checks whether the same idea survives across multiple markets. Use to detect symbol dependence.'],
                    ['Walk-forward', 'Checks train/test degradation through sequential segments. Use for out-of-sample discipline.'],
                    ['Promotion candidates', 'Ranks candidates by current edge plus multi-context consistency. Use to decide what deserves the shortlist.'],
                    ['Strategy compare', 'Compare shortlisted candidates and saved benchmarks against the current strategy. Use when you already have multiple serious contenders.'],
                    ['Study archive', 'Keeps versioned research runs. Use to inspect what was measured and preserve evidence.'],
                ],
            },
            {
                type: 'terms',
                title: 'Common output fields',
                rows: [
                    ['Win ratio vs baseline', 'Fraction of contexts where the candidate beat the baseline. Higher is better.'],
                    ['Stable train/test pairs', 'Fraction of walk-forward pairs that remained favorable or acceptable. Higher means better stability.'],
                    ['Avg train→test shift', 'How much the test result degraded or improved relative to train. Negative values are a warning.'],
                    ['Promotion score', 'Composite research score used to rank candidates. Useful as a priority signal, not as absolute truth.'],
                    ['Gate verdict', 'Pass / watch / fail decision from the formal promotion gate. This is more important than a pretty single score.'],
                ],
            },
        ],
    },
    {
        id: 'funnel',
        title: 'Promotion Funnel',
        summary: 'How candidates move through the funnel and what each status means.',
        blocks: [
            {
                type: 'terms',
                title: 'Status meanings',
                rows: [
                    ['queued', 'Candidate is interesting but not yet in paper follow-up.'],
                    ['paper', 'Candidate is being observed with paper results and notes.'],
                    ['review', 'Candidate passed enough checks to deserve final promotion review.'],
                    ['promoted', 'Candidate passed review and is entering live-readiness or controlled promotion.'],
                    ['dropped', 'Candidate is no longer worth continuing in the funnel.'],
                ],
            },
            {
                type: 'bullets',
                title: 'What to do at each stage',
                items: [
                    'Queued: gather more comparative evidence.',
                    'Paper: record observed period, observed PnL, notes and paper verdict.',
                    'Review: write promotion memo and check gate details before changing status.',
                    'Live readiness: confirm execution, monitoring and operational constraints.',
                ],
            },
        ],
    },
    {
        id: 'recipe',
        title: 'Recommended First Research Session',
        summary: 'A concrete recipe for using the platform without getting lost.',
        blocks: [
            {
                type: 'steps',
                title: 'First session recipe',
                items: [
                    'Start with one symbol and one timeframe only.',
                    'Add only the features you can explain.',
                    'Write the simplest strategy version that expresses the idea.',
                    'Turn on realistic costs before reading the outcome.',
                    'Use Preset compare to see whether Market Regime helps the idea.',
                    'Run Timeframe study and Symbol study before trusting the candidate.',
                    'Run Walk-forward before promoting anything.',
                    'Shortlist only the candidates that still look good after those checks.',
                ],
            },
        ],
    },
    {
        id: 'mistakes',
        title: 'Common Mistakes',
        summary: 'Fastest ways to fool yourself and how to avoid them.',
        blocks: [
            {
                type: 'bullets',
                title: 'Avoid these patterns',
                items: [
                    'Changing too many variables at once and then not knowing what improved the strategy.',
                    'Believing a great current-context backtest without checking timeframes, symbols and walk-forward.',
                    'Ignoring spread and slippage.',
                    'Promoting a candidate because it is clever, not because it is stable.',
                    'Adding features that you do not understand just because they improve one chart.',
                ],
            },
        ],
    },
    {
        id: 'decision',
        title: 'How To Decide If A Strategy Is Good Enough',
        summary: 'A practical standard for deciding when to keep, promote or drop.',
        blocks: [
            {
                type: 'bullets',
                title: 'Promote only when most of this is true',
                items: [
                    'The strategy beats the baseline in the current context.',
                    'It remains competitive across timeframes and symbols.',
                    'Walk-forward does not collapse badly from train to test.',
                    'Failure modes are understood and not hidden.',
                    'Paper notes support the same story as the backtest evidence.',
                ],
            },
            {
                type: 'paragraphs',
                items: [
                    'If the evidence is mixed, keep the candidate in paper or review. If the evidence is contradictory, drop it and record why. The system is designed so that a clean drop still teaches something useful.',
                ],
            },
        ],
    },
]

function buildPromotionGate(entry) {
    const currentDeltaNetPnl = Number(entry?.comparison?.delta_vs_baseline?.net_pnl || 0)
    const windowRatio = Number(entry?.windowStudy?.consistency?.win_ratio_vs_baseline || 0)
    const timeframeRatio = Number(entry?.timeframeStudy?.consistency?.win_ratio_vs_baseline || 0)
    const symbolRatio = Number(entry?.symbolStudy?.consistency?.win_ratio_vs_baseline || 0)
    const stablePairRatio = Number(entry?.walkforwardStudy?.train_test_consistency?.stable_pair_ratio || 0)
    const avgTrainToTestShift = Number(entry?.walkforwardStudy?.train_test_consistency?.avg_train_to_test_net_pnl_shift || 0)

    const checks = [
        {
            id: 'current_edge',
            label: 'Current edge',
            passed: currentDeltaNetPnl > 0,
            detail: currentDeltaNetPnl > 0 ? 'Beats baseline in current context.' : 'Does not beat the current baseline now.',
        },
        {
            id: 'window_consistency',
            label: 'Window consistency',
            passed: windowRatio >= 0.5,
            detail: `Win ratio vs baseline across splits: ${formatPercent(windowRatio, 0)}`,
        },
        {
            id: 'timeframe_consistency',
            label: 'Timeframe consistency',
            passed: timeframeRatio >= 0.5,
            detail: `Win ratio vs baseline across timeframes: ${formatPercent(timeframeRatio, 0)}`,
        },
        {
            id: 'symbol_consistency',
            label: 'Symbol consistency',
            passed: symbolRatio >= 0.45,
            detail: `Win ratio vs baseline across symbols: ${formatPercent(symbolRatio, 0)}`,
        },
        {
            id: 'walkforward_stability',
            label: 'Walk-forward stability',
            passed: stablePairRatio >= 0.5,
            detail: `Stable train/test pairs: ${formatPercent(stablePairRatio, 0)}`,
        },
        {
            id: 'train_test_shift',
            label: 'Train/test shift',
            passed: avgTrainToTestShift >= 0,
            detail: `Average train→test shift: ${formatSignedMoney(avgTrainToTestShift)}`,
        },
    ]

    const passedCount = checks.filter((item) => item.passed).length
    const failedChecks = checks.filter((item) => !item.passed)
    const severeFail = (
        stablePairRatio > 0 && stablePairRatio < 0.35
    ) || (
        avgTrainToTestShift < 0 && Math.abs(avgTrainToTestShift) > Math.max(1, Math.abs(currentDeltaNetPnl) * 0.25)
    )

    let verdict = 'watch'
    if (passedCount === checks.length) {
        verdict = 'pass'
    } else if (severeFail || passedCount <= 2) {
        verdict = 'fail'
    }

    let tier = 'conditional'
    if (verdict === 'pass' && passedCount >= 5 && stablePairRatio >= 0.6 && timeframeRatio >= 0.5) {
        tier = 'strong'
    } else if (verdict === 'fail') {
        tier = 'weak'
    }

    return {
        verdict,
        tier,
        severeFail,
        passedCount,
        totalChecks: checks.length,
        checks,
        failedChecks,
    }
}

function buildResearchDecisionEngine(entry) {
    const promotionScore = Number(entry?.promotionScore || 0)
    const trackerStatus = String(entry?.trackerStatus || 'queued')
    const finalDecision = String(entry?.finalDecision || 'pending')
    const paperVerdict = String(entry?.paperVerdict || 'pending')
    const liveReadiness = String(entry?.liveReadiness || 'pending')
    const gateModel = (
        entry?.promotionGate && typeof entry.promotionGate === 'object'
            ? entry.promotionGate
            : {
                verdict: entry?.promotionGateVerdict || 'watch',
                tier: entry?.promotionGateTier || 'conditional',
                severeFail: Boolean(entry?.promotionGateSevereFail),
                passedCount: entry?.promotionGatePassedCount || 0,
                totalChecks: entry?.promotionGateTotalChecks || 0,
            }
    )
    const gateVerdict = String(gateModel?.verdict || 'watch')
    const gateTier = String(gateModel?.tier || 'conditional')
    const gatePassedCount = Number(gateModel?.passedCount || 0)
    const gateTotalChecks = Number(gateModel?.totalChecks || 0)
    const severeFail = Boolean(gateModel?.severeFail)
    const failureCategory = String(entry?.failureModeCategory || 'uncategorized')

    let nextStep = 'keep_queued'
    let statusTone = 'watch'
    let headline = 'Needs more evidence before promotion.'
    let autoDisposition = 'watch'
    let autoReadiness = 'not_ready'
    let confidence = 'moderate'

    if (finalDecision === 'drop' || paperVerdict === 'drop' || liveReadiness === 'not_ready') {
        nextStep = 'drop'
        statusTone = 'reject'
        headline = 'Candidate should be dropped from the promotion path.'
        autoDisposition = 'reject'
        autoReadiness = 'not_ready'
        confidence = 'high'
    } else if (liveReadiness === 'ready' && (trackerStatus === 'promoted' || finalDecision === 'promote')) {
        nextStep = 'ready_for_live_review'
        statusTone = 'promote'
        headline = 'Candidate is ready for final live-review gating.'
        autoDisposition = 'promote'
        autoReadiness = 'live_review'
        confidence = gateTier === 'strong' ? 'high' : 'moderate'
    } else if ((paperVerdict === 'promote' || trackerStatus === 'review') && gateVerdict === 'pass') {
        nextStep = 'ready_for_promotion_review'
        statusTone = 'promote'
        headline = 'Candidate passed the promotion gate and is ready for final review.'
        autoDisposition = 'promote'
        autoReadiness = 'promotion_review'
        confidence = gateTier === 'strong' && promotionScore >= 72 ? 'high' : 'moderate'
    } else if (trackerStatus === 'paper' || paperVerdict === 'continue') {
        nextStep = 'continue_paper'
        statusTone = gateVerdict === 'fail' ? 'reject' : 'watch'
        headline = gateVerdict === 'fail'
            ? 'Paper tracking should probably be stopped unless new evidence appears.'
            : 'Keep this candidate in paper tracking.'
        autoDisposition = gateVerdict === 'fail' ? 'reject' : 'watch'
        autoReadiness = gateVerdict === 'fail' ? 'not_ready' : 'paper'
        confidence = gateVerdict === 'fail' ? 'high' : 'moderate'
    } else if (trackerStatus === 'queued') {
        nextStep = gateVerdict === 'pass' ? 'start_paper' : (gateVerdict === 'fail' ? 'drop' : 'watch')
        statusTone = gateVerdict === 'pass' ? 'promote' : (gateVerdict === 'fail' ? 'reject' : 'watch')
        headline = gateVerdict === 'pass'
            ? 'Candidate is good enough to enter paper tracking.'
            : gateVerdict === 'fail'
                ? 'Candidate should stay out of the funnel until the research evidence improves.'
                : 'Candidate should stay queued until stronger evidence appears.'
        autoDisposition = gateVerdict === 'pass' ? 'promote' : (gateVerdict === 'fail' ? 'reject' : 'watch')
        autoReadiness = gateVerdict === 'pass' ? 'paper' : 'not_ready'
        confidence = gateTier === 'strong' ? 'high' : 'moderate'
    }

    if (severeFail || (gateVerdict === 'fail' && promotionScore < 45) || failureCategory === 'timeframe_instability') {
        nextStep = finalDecision === 'drop' ? 'drop' : nextStep
        statusTone = 'reject'
        autoDisposition = 'reject'
        autoReadiness = 'not_ready'
        confidence = 'high'
        if (trackerStatus !== 'dropped' && finalDecision !== 'drop') {
            headline = 'Candidate is not robust enough yet and should not move forward.'
        }
    } else if (gateVerdict === 'pass' && gateTier === 'strong' && promotionScore >= 82 && trackerStatus === 'queued') {
        headline = 'Candidate has strong evidence and should enter paper tracking with high priority.'
        autoDisposition = 'promote'
        autoReadiness = 'paper'
        confidence = 'high'
    }

    return {
        nextStep,
        statusTone,
        headline,
        gateSummary: `${gatePassedCount}/${gateTotalChecks || 0} checks passed`,
        autoDisposition,
        autoReadiness,
        confidence,
    }
}

function buildAutoResearchTransitionPatch(entry) {
    const decision = buildResearchDecisionEngine(entry)
    const timestampPatch = {
        reviewedAt: new Date().toISOString(),
        reviewedAtLabel: new Date().toLocaleString(),
    }

    if (decision.nextStep === 'drop') {
        return {
            ...timestampPatch,
            trackerStatus: 'dropped',
            finalDecision: 'drop',
            liveReadiness: 'not_ready',
        }
    }

    if (decision.nextStep === 'ready_for_live_review') {
        return {
            ...timestampPatch,
            trackerStatus: 'promoted',
            finalDecision: 'promote',
            paperVerdict: 'promote',
            liveReadiness: 'ready',
        }
    }

    if (decision.nextStep === 'ready_for_promotion_review') {
        return {
            ...timestampPatch,
            trackerStatus: 'review',
            finalDecision: 'promote',
            paperVerdict: 'promote',
        }
    }

    if (decision.nextStep === 'continue_paper') {
        return {
            ...timestampPatch,
            trackerStatus: 'paper',
            finalDecision: 'keep',
            paperVerdict: 'continue',
        }
    }

    if (decision.nextStep === 'start_paper') {
        return {
            ...timestampPatch,
            trackerStatus: 'paper',
            finalDecision: 'keep',
            paperVerdict: 'continue',
            paperStartDate: entry?.paperStartDate || new Date().toISOString().slice(0, 10),
        }
    }

    return {
        ...timestampPatch,
        trackerStatus: entry?.trackerStatus || 'queued',
        finalDecision: entry?.finalDecision || 'pending',
    }
}

function getPromotionReviewCandidates(shortlist = []) {
    return (shortlist || []).filter((entry) => {
        const trackerStatus = String(entry?.trackerStatus || 'queued')
        const finalDecision = String(entry?.finalDecision || 'pending')
        const paperVerdict = String(entry?.paperVerdict || 'pending')

        return (
            trackerStatus === 'review'
            || finalDecision === 'promote'
            || paperVerdict === 'promote'
        )
    })
}

function getLiveReadinessCandidates(shortlist = []) {
    return (shortlist || []).filter((entry) => {
        const trackerStatus = String(entry?.trackerStatus || 'queued')
        const finalDecision = String(entry?.finalDecision || 'pending')
        const paperVerdict = String(entry?.paperVerdict || 'pending')

        return (
            trackerStatus === 'promoted'
            || finalDecision === 'promote'
            || paperVerdict === 'promote'
        )
    })
}

function getFailureModeCandidates(shortlist = []) {
    return (shortlist || []).filter((entry) => {
        const trackerStatus = String(entry?.trackerStatus || 'queued')
        const finalDecision = String(entry?.finalDecision || 'pending')
        const paperVerdict = String(entry?.paperVerdict || 'pending')
        const disposition = String(entry?.disposition || '')
        const liveReadiness = String(entry?.liveReadiness || 'pending')

        return (
            trackerStatus === 'dropped'
            || finalDecision === 'drop'
            || paperVerdict === 'drop'
            || disposition === 'reject'
            || liveReadiness === 'not_ready'
        )
    })
}

function PaperShortlistPane({
    shortlist = [],
    decisionLog = [],
    onRemove,
    onApply,
    onCopy,
    onUpdate,
}) {
    const statusCounts = shortlist.reduce((accumulator, entry) => {
        const status = String(entry?.trackerStatus || 'queued')
        accumulator[status] = (accumulator[status] || 0) + 1
        return accumulator
    }, {})

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Paper shortlist</div>
                    <div className='presetStudyMeta'>
                        Candidates promoted from research and kept ready for controlled paper-trading follow-up.
                    </div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={onCopy}
                    disabled={!shortlist.length}
                >
                    Copy shortlist JSON
                </button>
            </div>

            <div className='presetStudyMeta'>
                Queued: {Number(statusCounts.queued || 0)}
                {' · '}
                Paper: {Number(statusCounts.paper || 0)}
                {' · '}
                Review: {Number(statusCounts.review || 0)}
                {' · '}
                Promoted: {Number(statusCounts.promoted || 0)}
                {' · '}
                Dropped: {Number(statusCounts.dropped || 0)}
            </div>

            <div className='researchDecisionLogPanel'>
                <div className='researchDecisionLogTitle'>Recent decision log</div>
                {decisionLog.length > 0 ? (
                    <div className='researchDecisionLogList'>
                        {decisionLog.slice(0, 10).map((entry) => (
                            <div key={entry.id} className='researchDecisionLogItem'>
                                <div className='researchDecisionLogMain'>
                                    <strong>{entry.label || 'Research event'}</strong>
                                    <span>{entry.message || '-'}</span>
                                </div>
                                <div className='researchDecisionLogMeta'>
                                    <span>{entry.side ? String(entry.side).toUpperCase() : '-'}</span>
                                    <span>{entry.atLabel || '-'}</span>
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='presetCompareEmpty'>No decision log entries yet.</div>
                )}
            </div>

            {shortlist.length > 0 ? (
                <div className='promotionCandidateCards'>
                    {shortlist.map((entry) => {
                        const decision = buildResearchDecisionEngine(entry)
                        return (
                        <div key={entry.shortlist_id} className={`promotionCandidateCard tone-${entry.disposition || 'watch'}`}>
                            <div className='presetCompareCardHeader'>
                                <div className='presetCompareCardTitle'>{entry.label}</div>
                                <div className='promotionCandidateBadges'>
                                    <div className={`promotionDisposition ${entry.disposition || 'watch'}`}>{entry.disposition || 'watch'}</div>
                                </div>
                            </div>
                            <div className='promotionCandidateScoreRow'>
                                <span>Promotion score</span>
                                <strong>{formatValue(entry.promotionScore, 1)}</strong>
                            </div>
                            <div className='presetCompareMetrics'>
                                <div><span>Side</span><strong>{String(entry.side || '').toUpperCase()}</strong></div>
                                <div><span>Project</span><strong>{entry.projectName || 'Unsaved'}</strong></div>
                                <div><span>Saved at</span><strong>{entry.addedAtLabel || '-'}</strong></div>
                                <div><span>Source</span><strong>{entry.source || 'promotion_candidates'}</strong></div>
                                <div><span>Promotion gate</span><strong>{String(entry.promotionGateVerdict || '-').toUpperCase()}</strong></div>
                                <div><span>Gate checks</span><strong>{`${Number(entry.promotionGatePassedCount || 0)}/${Number(entry.promotionGateTotalChecks || 0)}`}</strong></div>
                            </div>
                            <div className='presetCompareRecommendation'>
                                <strong>Auto decision:</strong>
                                {' '}
                                {decision.headline}
                                {' · '}
                                {decision.autoDisposition}
                                {' / '}
                                {decision.autoReadiness}
                                {' / '}
                                {decision.confidence}
                            </div>
                            <div className='paperTrackerGrid'>
                                <label className='paperTrackerField'>
                                    <span>Status</span>
                                    <select
                                        value={String(entry.trackerStatus || 'queued')}
                                        onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                            trackerStatus: event.target.value,
                                            reviewedAt: new Date().toISOString(),
                                            reviewedAtLabel: new Date().toLocaleString(),
                                        })}
                                    >
                                        <option value='queued'>Queued</option>
                                        <option value='paper'>Paper</option>
                                        <option value='review'>Review</option>
                                        <option value='promoted'>Promoted</option>
                                        <option value='dropped'>Dropped</option>
                                    </select>
                                </label>
                                <label className='paperTrackerField'>
                                    <span>Decision</span>
                                    <select
                                        value={String(entry.finalDecision || 'pending')}
                                        onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                            finalDecision: event.target.value,
                                            reviewedAt: new Date().toISOString(),
                                            reviewedAtLabel: new Date().toLocaleString(),
                                        })}
                                    >
                                        <option value='pending'>Pending</option>
                                        <option value='keep'>Keep</option>
                                        <option value='drop'>Drop</option>
                                        <option value='promote'>Promote</option>
                                    </select>
                                </label>
                            </div>
                            <label className='paperTrackerField notes'>
                                <span>Notes</span>
                                <textarea
                                    value={String(entry.notes || '')}
                                    onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                        notes: event.target.value,
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                    placeholder='Why is this candidate interesting? What still needs validation?'
                                />
                            </label>
                            <div className='paperTrackerGrid'>
                                <label className='paperTrackerField'>
                                    <span>Paper start</span>
                                    <input
                                        type='date'
                                        value={String(entry.paperStartDate || '')}
                                        onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                            paperStartDate: event.target.value,
                                            reviewedAt: new Date().toISOString(),
                                            reviewedAtLabel: new Date().toLocaleString(),
                                        })}
                                    />
                                </label>
                                <label className='paperTrackerField'>
                                    <span>Observed period</span>
                                    <input
                                        type='text'
                                        value={String(entry.observedPeriod || '')}
                                        onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                            observedPeriod: event.target.value,
                                            reviewedAt: new Date().toISOString(),
                                            reviewedAtLabel: new Date().toLocaleString(),
                                        })}
                                        placeholder='e.g. 2 weeks / 35 trades'
                                    />
                                </label>
                            </div>
                            <div className='paperTrackerGrid'>
                                <label className='paperTrackerField'>
                                    <span>Observed PnL</span>
                                    <input
                                        type='number'
                                        step='any'
                                        value={entry.observedPnl ?? ''}
                                        onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                            observedPnl: event.target.value === '' ? null : Number(event.target.value),
                                            reviewedAt: new Date().toISOString(),
                                            reviewedAtLabel: new Date().toLocaleString(),
                                        })}
                                        placeholder='0.0'
                                    />
                                </label>
                                <label className='paperTrackerField'>
                                    <span>Paper verdict</span>
                                    <select
                                        value={String(entry.paperVerdict || 'pending')}
                                        onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                            paperVerdict: event.target.value,
                                            reviewedAt: new Date().toISOString(),
                                            reviewedAtLabel: new Date().toLocaleString(),
                                        })}
                                    >
                                        <option value='pending'>Pending</option>
                                        <option value='continue'>Continue</option>
                                        <option value='promote'>Promote</option>
                                        <option value='drop'>Drop</option>
                                    </select>
                                </label>
                            </div>
                            <label className='paperTrackerField notes'>
                                <span>Paper notes</span>
                                <textarea
                                    value={String(entry.paperNotes || '')}
                                    onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                        paperNotes: event.target.value,
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                    placeholder='Observed behavior in paper trading, caveats, execution notes...'
                                />
                            </label>
                            <div className='presetStudyMeta'>
                                Last review: {entry.reviewedAtLabel || '-'}
                            </div>
                            <div className='presetCompareActions'>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onUpdate?.(entry.shortlist_id, buildAutoResearchTransitionPatch(entry))}
                                >
                                    Apply auto step
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onApply?.(entry)}
                                >
                                    Apply to Strategy
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onRemove?.(entry.shortlist_id)}
                                >
                                    Remove
                                </button>
                            </div>
                        </div>
                        )
                    })}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No paper-trading candidates have been shortlisted yet.</div>
            )}
        </div>
    )
}

function PromotionReviewPane({
    shortlist = [],
    onApply,
    onUpdate,
}) {
    const reviewCandidates = getPromotionReviewCandidates(shortlist)

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Promotion review</div>
                    <div className='presetStudyMeta'>
                        Final review stage for candidates that are already flagged for promotion or explicit review.
                    </div>
                </div>
            </div>

            {reviewCandidates.length > 0 ? (
                <div className='promotionCandidateCards'>
                    {reviewCandidates.map((entry) => (
                        (() => {
                            const decision = buildResearchDecisionEngine(entry)
                            return (
                        <div key={entry.shortlist_id} className={`promotionCandidateCard tone-${entry.disposition || 'watch'}`}>
                            <div className='presetCompareCardHeader'>
                                <div className='presetCompareCardTitle'>{entry.label}</div>
                                <div className='promotionCandidateBadges'>
                                    <div className={`promotionDisposition ${entry.disposition || 'watch'}`}>{entry.disposition || 'watch'}</div>
                                    <div className='presetCompareBadge'>Review</div>
                                </div>
                            </div>

                            <div className='promotionCandidateScoreRow'>
                                <span>Promotion score</span>
                                <strong>{formatValue(entry.promotionScore, 1)}</strong>
                            </div>
                            <div className='presetCompareRecommendation'>
                                <strong>Decision engine:</strong>
                                {' '}
                                {decision.headline}
                                {' · '}
                                {decision.gateSummary}
                                {' · '}
                                {decision.autoDisposition}
                                {' / '}
                                {decision.autoReadiness}
                                {' / '}
                                {decision.confidence}
                            </div>

                            <div className='presetCompareMetrics'>
                                <div><span>Status</span><strong>{String(entry.trackerStatus || 'queued')}</strong></div>
                                <div><span>Decision</span><strong>{String(entry.finalDecision || 'pending')}</strong></div>
                                <div><span>Paper verdict</span><strong>{String(entry.paperVerdict || 'pending')}</strong></div>
                                <div><span>Observed PnL</span><strong>{formatValue(entry.observedPnl, 2)}</strong></div>
                            </div>

                            <div className='researchReviewChecklist'>
                                <div className='researchReviewChecklistTitle'>Promotion checklist</div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Observed period</span>
                                    <strong>{entry.observedPeriod || '-'}</strong>
                                </div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Paper notes</span>
                                    <strong>{entry.paperNotes || '-'}</strong>
                                </div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Last review</span>
                                    <strong>{entry.reviewedAtLabel || '-'}</strong>
                                </div>
                            </div>

                            <label className='paperTrackerField notes'>
                                <span>Promotion memo</span>
                                <textarea
                                    value={String(entry.promotionMemo || '')}
                                    onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                        promotionMemo: event.target.value,
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                    placeholder='Final rationale for promote, keep in paper, or drop.'
                                />
                            </label>

                            <div className='presetCompareActions'>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onUpdate?.(entry.shortlist_id, buildAutoResearchTransitionPatch(entry))}
                                >
                                    Apply auto step
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onUpdate?.(entry.shortlist_id, {
                                        trackerStatus: 'promoted',
                                        finalDecision: 'promote',
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                >
                                    Promote
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onUpdate?.(entry.shortlist_id, {
                                        trackerStatus: 'dropped',
                                        finalDecision: 'drop',
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                >
                                    Drop
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onUpdate?.(entry.shortlist_id, {
                                        trackerStatus: 'paper',
                                        finalDecision: 'keep',
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                >
                                    Keep in paper
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onApply?.(entry)}
                                >
                                    Apply to Strategy
                                </button>
                            </div>
                        </div>
                            )
                        })()
                    ))}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No candidates are ready for promotion review yet.</div>
            )}
        </div>
    )
}

function LiveReadinessPane({
    shortlist = [],
    onApply,
    onUpdate,
}) {
    const liveCandidates = getLiveReadinessCandidates(shortlist)

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Live readiness</div>
                    <div className='presetStudyMeta'>
                        Final operational check before promoting a candidate from paper follow-up into controlled live monitoring.
                    </div>
                </div>
            </div>

            {liveCandidates.length > 0 ? (
                <div className='promotionCandidateCards'>
                    {liveCandidates.map((entry) => (
                        (() => {
                            const decision = buildResearchDecisionEngine(entry)
                            return (
                        <div key={entry.shortlist_id} className={`promotionCandidateCard tone-${entry.disposition || 'watch'}`}>
                            <div className='presetCompareCardHeader'>
                                <div className='presetCompareCardTitle'>{entry.label}</div>
                                <div className='promotionCandidateBadges'>
                                    <div className={`promotionDisposition ${entry.disposition || 'watch'}`}>{entry.disposition || 'watch'}</div>
                                    <div className='presetCompareBadge'>Live</div>
                                </div>
                            </div>

                            <div className='presetCompareMetrics'>
                                <div><span>Status</span><strong>{String(entry.trackerStatus || 'queued')}</strong></div>
                                <div><span>Decision</span><strong>{String(entry.finalDecision || 'pending')}</strong></div>
                                <div><span>Paper verdict</span><strong>{String(entry.paperVerdict || 'pending')}</strong></div>
                                <div><span>Promotion score</span><strong>{formatValue(entry.promotionScore, 1)}</strong></div>
                            </div>
                            <div className='presetCompareRecommendation'>
                                <strong>Decision engine:</strong>
                                {' '}
                                {decision.headline}
                                {' · '}
                                {decision.gateSummary}
                                {' · '}
                                {decision.autoDisposition}
                                {' / '}
                                {decision.autoReadiness}
                                {' / '}
                                {decision.confidence}
                            </div>

                            <div className='researchReviewChecklist'>
                                <div className='researchReviewChecklistTitle'>Operational checklist</div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Observed period</span>
                                    <strong>{entry.observedPeriod || '-'}</strong>
                                </div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Observed PnL</span>
                                    <strong>{formatValue(entry.observedPnl, 2)}</strong>
                                </div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Promotion memo</span>
                                    <strong>{entry.promotionMemo || '-'}</strong>
                                </div>
                            </div>

                            <div className='paperTrackerGrid'>
                                <label className='paperTrackerField'>
                                    <span>Live readiness</span>
                                    <select
                                        value={String(entry.liveReadiness || 'pending')}
                                        onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                            liveReadiness: event.target.value,
                                            reviewedAt: new Date().toISOString(),
                                            reviewedAtLabel: new Date().toLocaleString(),
                                        })}
                                    >
                                        <option value='pending'>Pending</option>
                                        <option value='ready'>Ready</option>
                                        <option value='not_ready'>Not ready</option>
                                    </select>
                                </label>
                            </div>

                            <label className='paperTrackerField notes'>
                                <span>Live readiness notes</span>
                                <textarea
                                    value={String(entry.liveReadinessNotes || '')}
                                    onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                        liveReadinessNotes: event.target.value,
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                    placeholder='Broker, execution, monitoring, capital/risk guardrails, operational caveats...'
                                />
                            </label>

                            <div className='presetCompareActions'>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onUpdate?.(entry.shortlist_id, buildAutoResearchTransitionPatch(entry))}
                                >
                                    Apply auto step
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => onApply?.(entry)}
                                >
                                    Apply to Strategy
                                </button>
                            </div>
                        </div>
                            )
                        })()
                    ))}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No candidates are far enough along for live readiness yet.</div>
            )}
        </div>
    )
}

function FailureModesPane({
    shortlist = [],
    onUpdate,
}) {
    const failedCandidates = getFailureModeCandidates(shortlist)

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Failure modes</div>
                    <div className='presetStudyMeta'>
                        Capture why a candidate failed across paper, promotion review or live-readiness checks, so the research loop teaches something reusable.
                    </div>
                </div>
            </div>

            {failedCandidates.length > 0 ? (
                <div className='promotionCandidateCards'>
                    {failedCandidates.map((entry) => (
                        <div key={entry.shortlist_id} className={`promotionCandidateCard tone-${entry.disposition || 'watch'}`}>
                            <div className='presetCompareCardHeader'>
                                <div className='presetCompareCardTitle'>{entry.label}</div>
                                <div className='promotionCandidateBadges'>
                                    <div className={`promotionDisposition ${entry.disposition || 'reject'}`}>{entry.disposition || 'reject'}</div>
                                    <div className='presetCompareBadge'>Failure</div>
                                </div>
                            </div>

                            <div className='presetCompareMetrics'>
                                <div><span>Status</span><strong>{String(entry.trackerStatus || 'queued')}</strong></div>
                                <div><span>Decision</span><strong>{String(entry.finalDecision || 'pending')}</strong></div>
                                <div><span>Paper verdict</span><strong>{String(entry.paperVerdict || 'pending')}</strong></div>
                                <div><span>Live readiness</span><strong>{String(entry.liveReadiness || 'pending')}</strong></div>
                            </div>

                            <div className='researchReviewChecklist'>
                                <div className='researchReviewChecklistTitle'>Failure snapshot</div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Observed period</span>
                                    <strong>{entry.observedPeriod || '-'}</strong>
                                </div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Observed PnL</span>
                                    <strong>{formatValue(entry.observedPnl, 2)}</strong>
                                </div>
                                <div className='researchReviewChecklistItem'>
                                    <span>Promotion memo</span>
                                    <strong>{entry.promotionMemo || '-'}</strong>
                                </div>
                            </div>

                            <label className='paperTrackerField notes'>
                                <span>Failure category</span>
                                <select
                                    value={String(entry.failureModeCategory || 'uncategorized')}
                                    onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                        failureModeCategory: event.target.value,
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                >
                                    <option value='uncategorized'>Uncategorized</option>
                                    <option value='regime_fragility'>Regime fragility</option>
                                    <option value='timeframe_instability'>Timeframe instability</option>
                                    <option value='symbol_dependence'>Symbol dependence</option>
                                    <option value='execution_costs'>Execution costs</option>
                                    <option value='risk_drawdown'>Risk / drawdown</option>
                                    <option value='ops_live_readiness'>Operational readiness</option>
                                </select>
                            </label>

                            <label className='paperTrackerField notes'>
                                <span>Failure mode</span>
                                <textarea
                                    value={String(entry.failureMode || '')}
                                    onChange={(event) => onUpdate?.(entry.shortlist_id, {
                                        failureMode: event.target.value,
                                        reviewedAt: new Date().toISOString(),
                                        reviewedAtLabel: new Date().toLocaleString(),
                                    })}
                                    placeholder='Main reason this candidate failed: regime fragility, execution sensitivity, unstable timeframe behavior, symbol dependence, live ops risk...'
                                />
                            </label>
                        </div>
                    ))}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No failure modes have been recorded yet.</div>
            )}
        </div>
    )
}

function PromotionCandidatesPane({
    authToken,
    chartSettings,
    strategyApplyResponse,
    setStrategy,
    setStrategySetEntries,
    onOpenStrategy,
    onLogEvent,
    currentWorkspaceSaveName,
    onAddToShortlist,
    initialState,
    onStudyComplete,
}) {
    const [selectedSide, setSelectedSide] = useState('long')
    const selectedInitialState = useMemo(
        () => initialState?.[selectedSide]?.payload || buildDefaultPromotionCandidateState(),
        [initialState, selectedSide],
    )
    const [candidateState, setCandidateState] = useSourcedState(selectedInitialState)

    const researchChartSettings = useMemo(
        () => buildResearchChartSettings(chartSettings, strategyApplyResponse),
        [chartSettings, strategyApplyResponse]
    )
    const tokenCandidates = useMemo(
        () => getStrategyTokenCandidates(researchChartSettings),
        [researchChartSettings]
    )
    const marketRegimePresets = useMemo(
        () => buildMarketRegimePresetModel(tokenCandidates),
        [tokenCandidates]
    )

    const sidePresets = selectedSide === 'short'
        ? (marketRegimePresets?.shortEntries || [])
        : (marketRegimePresets?.longEntries || [])

    const studyWindows = useMemo(() => {
        const currentRows = Math.max(
            0,
            Number(strategyApplyResponse?.rows || strategyApplyResponse?.results?.length || 0),
        )
        if (currentRows <= 0) {
            return []
        }
        return [...new Set([
            Math.max(100, Math.round(currentRows * 0.25)),
            Math.max(200, Math.round(currentRows * 0.5)),
            currentRows,
        ])]
    }, [strategyApplyResponse])

    const timeframeOptions = useMemo(() => {
        const current = String(chartSettings?.timeframe || '').trim().toUpperCase()
        const base = ['M1', 'M5', 'M15', 'H1']
        return [...new Set([current, ...base].filter(Boolean))]
    }, [chartSettings])

    const symbolOptions = useMemo(() => {
        const current = String(chartSettings?.symbol || '').trim().toUpperCase()
        const base = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD']
        return [...new Set([current, ...base].filter(Boolean))]
    }, [chartSettings])

    function buildPresetPayloads() {
        return sidePresets.map((preset) => {
            const strategy = buildBlankStrategy()
            strategy[selectedSide].openIf = preset.openIf
            strategy[selectedSide].closeIf = preset.closeIf
            strategy[selectedSide].gainPrice = preset.gainPrice
            strategy[selectedSide].lossPrice = preset.lossPrice
            strategy[selectedSide].trailingPrice = preset.trailingPrice

            return {
                id: preset.id,
                label: preset.label,
                strategy: resolveStrategyAliasesInStrategy(strategy, researchChartSettings),
            }
        })
    }

    function scoreCandidate(entry) {
        const gate = buildPromotionGate(entry)
        const comparisonDelta = Number(entry?.comparison?.delta_vs_baseline?.net_pnl || 0)
        const windowRatio = Number(entry?.windowStudy?.consistency?.win_ratio_vs_baseline || 0)
        const timeframeRatio = Number(entry?.timeframeStudy?.consistency?.win_ratio_vs_baseline || 0)
        const symbolRatio = Number(entry?.symbolStudy?.consistency?.win_ratio_vs_baseline || 0)
        const walkforwardRatio = Number(entry?.walkforwardStudy?.train_test_consistency?.stable_pair_ratio || entry?.walkforwardStudy?.consistency?.win_ratio_vs_baseline || 0)
        const avgDeltaNetPnl = Number(entry?.windowStudy?.consistency?.avg_delta_net_pnl || 0)
            + Number(entry?.timeframeStudy?.consistency?.avg_delta_net_pnl || 0)
            + Number(entry?.symbolStudy?.consistency?.avg_delta_net_pnl || 0)
            + Number(entry?.walkforwardStudy?.consistency?.avg_delta_net_pnl || 0)

        let score = 0
        if (comparisonDelta > 0) score += 15
        score += windowRatio * 22
        score += timeframeRatio * 20
        score += symbolRatio * 18
        score += walkforwardRatio * 25
        if (avgDeltaNetPnl > 0) score += 10
        if (gate.verdict === 'pass') score += 10
        if (gate.verdict === 'fail') score -= 20
        return Math.round(score * 10) / 10
    }

    function classifyCandidate(score) {
        if (score >= 72) {
            return 'promote'
        }
        if (score >= 45) {
            return 'watch'
        }
        return 'reject'
    }

    function buildCandidateAssessment(entry) {
        const gate = buildPromotionGate(entry)
        const windowRatio = Number(entry?.windowStudy?.consistency?.win_ratio_vs_baseline || 0)
        const timeframeRatio = Number(entry?.timeframeStudy?.consistency?.win_ratio_vs_baseline || 0)
        const symbolRatio = Number(entry?.symbolStudy?.consistency?.win_ratio_vs_baseline || 0)
        const walkforwardRatio = Number(entry?.walkforwardStudy?.train_test_consistency?.stable_pair_ratio || entry?.walkforwardStudy?.consistency?.win_ratio_vs_baseline || 0)
        const walkforwardShift = Number(entry?.walkforwardStudy?.train_test_consistency?.avg_train_to_test_net_pnl_shift || 0)
        const issues = []
        let autoFailureCategory = 'uncategorized'
        let autoFailureMessage = ''

        if (walkforwardRatio > 0 && walkforwardRatio < 0.4) {
            issues.push('Weak out-of-sample walk-forward behavior')
            autoFailureCategory = 'timeframe_instability'
            autoFailureMessage = 'Walk-forward validation was unstable relative to the baseline.'
        }
        if (walkforwardShift < 0) {
            issues.push('Performance degraded from train to test segments')
            if (autoFailureCategory === 'uncategorized') {
                autoFailureCategory = 'timeframe_instability'
                autoFailureMessage = 'Train/test walk-forward pairs lost quality in the test segments.'
            }
        }
        if (timeframeRatio > 0 && timeframeRatio < 0.4) {
            issues.push('Inconsistent across timeframes')
            if (autoFailureCategory === 'uncategorized') {
                autoFailureCategory = 'timeframe_instability'
                autoFailureMessage = 'Candidate degraded materially across timeframes.'
            }
        }
        if (symbolRatio > 0 && symbolRatio < 0.4) {
            issues.push('Strong symbol dependence')
            if (autoFailureCategory === 'uncategorized') {
                autoFailureCategory = 'symbol_dependence'
                autoFailureMessage = 'Candidate depended too heavily on the current market.'
            }
        }
        if (windowRatio > 0 && windowRatio < 0.4) {
            issues.push('Weak consistency across history splits')
            if (autoFailureCategory === 'uncategorized') {
                autoFailureCategory = 'regime_fragility'
                autoFailureMessage = 'Candidate was unstable across repeated history-depth splits.'
            }
        }

        let disposition = classifyCandidate(entry.promotionScore)
        if (gate.verdict === 'pass') {
            disposition = 'promote'
        } else if (gate.verdict === 'fail') {
            disposition = 'reject'
        } else if (walkforwardRatio > 0 && walkforwardRatio < 0.55 && disposition === 'promote') {
            disposition = 'watch'
        }

        let recommendation = 'Balanced candidate for continued evaluation.'
        if (disposition === 'promote' && !issues.length) {
            recommendation = `Strong candidate across the research stack and passed ${gate.passedCount}/${gate.totalChecks} promotion gates.`
        } else if (disposition === 'watch' && issues.length) {
            recommendation = `Promising, but still fragile: ${issues[0]}.`
        } else if (disposition === 'reject' && issues.length) {
            recommendation = `Not ready for promotion: ${issues[0]}.`
        }

        return {
            disposition,
            issues,
            recommendation,
            autoFailureCategory,
            autoFailureMessage,
            gate,
        }
    }

    function buildCandidateResult(payload, presets) {
        const comparisonMap = new Map((payload?.comparisons || []).map((entry) => [entry.id, entry]))
        const windowMap = new Map((payload?.study?.comparisons || []).map((entry) => [entry.id, entry]))
        const timeframeMap = new Map((payload?.timeframe_study?.comparisons || []).map((entry) => [entry.id, entry]))
        const symbolMap = new Map((payload?.symbol_study?.comparisons || []).map((entry) => [entry.id, entry]))
        const walkforwardMap = new Map((payload?.walkforward_study?.comparisons || []).map((entry) => [entry.id, entry]))

        const candidates = presets.map((preset) => {
            const comparison = comparisonMap.get(preset.id) || null
            const windowStudy = windowMap.get(preset.id) || null
            const timeframeStudy = timeframeMap.get(preset.id) || null
            const symbolStudy = symbolMap.get(preset.id) || null
            const walkforwardStudy = walkforwardMap.get(preset.id) || null
            const promotionScore = scoreCandidate({
                comparison,
                windowStudy,
                timeframeStudy,
                symbolStudy,
                walkforwardStudy,
            })
            const assessment = buildCandidateAssessment({
                comparison,
                windowStudy,
                timeframeStudy,
                symbolStudy,
                walkforwardStudy,
                promotionScore,
            })

            return {
                id: preset.id,
                label: preset.label,
                strategy: preset.strategy,
                comparison,
                windowStudy,
                timeframeStudy,
                symbolStudy,
                walkforwardStudy,
                promotionScore,
                disposition: assessment.disposition,
                recommendation: assessment.recommendation,
                issues: assessment.issues,
                promotionGate: assessment.gate,
                autoFailureCategory: assessment.autoFailureCategory,
                autoFailureMessage: assessment.autoFailureMessage,
            }
        }).sort((left, right) => right.promotionScore - left.promotionScore)

        return {
            baseline: payload?.baseline || null,
            candidates,
            bestCandidateId: candidates[0]?.id || '',
        }
    }

    function handleApplyCandidate(entry) {
        const preset = sidePresets.find((item) => item.id === entry.id)
        if (!preset) {
            return
        }

        const nextStrategy = buildStrategyFromMarketRegimePreset(selectedSide, preset, buildBlankStrategy())
        applyResearchStrategySelection({
            setStrategy,
            setStrategySetEntries,
            strategy: nextStrategy,
            strategies: [],
        })
        onOpenStrategy?.()
        onLogEvent?.(`Research · Applied ${selectedSide} promotion candidate: ${preset.label}.`)
    }

    async function handleRunCandidates() {
        if (!authToken || !marketRegimePresets) {
            return
        }

        const baselinePayload = buildResearchBaselinePayload(researchChartSettings, strategyApplyResponse)
        const presets = buildPresetPayloads()

        setCandidateState({
            loading: true,
            error: '',
            result: null,
        })

        try {
            const payload = await runPresetCompareResearchJob({
                authToken,
                requestPayload: {
                    backtest: strategyApplyResponse?.request?.backtest || null,
                    baseline: baselinePayload,
                    presets,
                    studyWindows,
                    studyTimeframes: timeframeOptions,
                    studySymbols: symbolOptions,
                    walkforwardWindowBars: Math.max(100, Math.round((strategyApplyResponse?.rows || chartSettings?.bars || 1000) * 0.2)),
                    walkforwardStepBars: Math.max(100, Math.round((strategyApplyResponse?.rows || chartSettings?.bars || 1000) * 0.2)),
                    chartContext: buildStudyChartContext(chartSettings, strategyApplyResponse),
                },
            })

            const nextState = {
                loading: false,
                error: '',
                result: buildCandidateResult(payload, presets),
            }
            setCandidateState(nextState)
            onStudyComplete?.('promotion_candidates', selectedSide, nextState)
            onLogEvent?.(`Research · Ranked ${selectedSide} promotion candidates.`)
        } catch (error) {
            setCandidateState({
                loading: false,
                error: error?.message || 'Failed to build promotion candidates.',
                result: null,
            })
            onLogEvent?.(`Research promotion candidates failed: ${error?.message || 'Failed to build promotion candidates.'}`)
        }
    }

    if (!marketRegimePresets) {
        return <div className='presetCompareEmpty'>Add a Market Regime feature with alias tokens to rank promotion candidates here.</div>
    }

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Promotion candidates</div>
                    <div className='presetStudyMeta'>
                        Rank the current research candidates by combining current-context result, window consistency, timeframe consistency and symbol consistency.
                    </div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={handleRunCandidates}
                    disabled={!authToken || candidateState.loading}
                >
                    {candidateState.loading ? 'Ranking...' : 'Rank candidates'}
                </button>
            </div>

            <div className='presetCompareSideTabs'>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'long' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('long')}
                >
                    Long
                </button>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'short' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('short')}
                >
                    Short
                </button>
            </div>

            <div className='presetStudyMeta'>
                Windows: {studyWindows.map((value) => Number(value).toLocaleString()).join(' · ') || '-'}
                {' | '}
                Timeframes: {timeframeOptions.join(' · ')}
                {' | '}
                Symbols: {symbolOptions.join(' · ')}
            </div>

            {candidateState.error ? (
                <div className='presetCompareError'>{candidateState.error}</div>
            ) : null}

            {candidateState.result?.candidates?.length > 0 ? (
                <div className='promotionCandidateCards'>
                    {candidateState.result.candidates.map((entry) => {
                        const isBest = candidateState.result.bestCandidateId === entry.id
                        return (
                            <div key={`candidate-${entry.id}`} className={`promotionCandidateCard ${isBest ? 'isBest' : ''} tone-${entry.disposition}`}>
                                <div className='presetCompareCardHeader'>
                                    <div className='presetCompareCardTitle'>{entry.label}</div>
                                    <div className='promotionCandidateBadges'>
                                        {isBest ? <div className='presetCompareBadge'>Top</div> : null}
                                        <div className={`promotionDisposition ${entry.disposition}`}>{entry.disposition}</div>
                                    </div>
                                </div>
                                <div className='promotionCandidateScoreRow'>
                                    <span>Promotion score</span>
                                    <strong>{formatValue(entry.promotionScore, 1)}</strong>
                                </div>
                                <div className='presetStudyMeta'>
                                    Gate: {String(entry?.promotionGate?.verdict || 'watch').toUpperCase()}
                                    {' · '}
                                    Passed {Number(entry?.promotionGate?.passedCount || 0)}/{Number(entry?.promotionGate?.totalChecks || 0)}
                                </div>
                                <div className='presetCompareMetrics'>
                                    <div><span>Current dPnL</span><strong>{formatPresetMetric(entry?.comparison?.delta_vs_baseline?.net_pnl)}</strong></div>
                                    <div><span>Window consistency</span><strong>{formatPresetMetric(entry?.windowStudy?.consistency?.win_ratio_vs_baseline, 'percent')}</strong></div>
                                    <div><span>Timeframe consistency</span><strong>{formatPresetMetric(entry?.timeframeStudy?.consistency?.win_ratio_vs_baseline, 'percent')}</strong></div>
                                    <div><span>Symbol consistency</span><strong>{formatPresetMetric(entry?.symbolStudy?.consistency?.win_ratio_vs_baseline, 'percent')}</strong></div>
                                    <div><span>Walk-forward</span><strong>{formatPresetMetric(entry?.walkforwardStudy?.train_test_consistency?.stable_pair_ratio || entry?.walkforwardStudy?.consistency?.win_ratio_vs_baseline, 'percent')}</strong></div>
                                </div>
                                <div className='presetCompareRecommendation'>
                                    <strong>Candidate read:</strong>
                                    {' '}
                                    {entry.recommendation}
                                </div>
                                <div className='presetCompareActions'>
                                    <button
                                        type='button'
                                        className='resultsActionButton'
                                        onClick={() => onAddToShortlist?.({
                                            shortlist_id: `${selectedSide}:${entry.id}:${Date.now()}`,
                                            id: entry.id,
                                            label: entry.label,
                                            side: selectedSide,
                                            promotionScore: entry.promotionScore,
                                            disposition: entry.disposition,
                                            strategy: entry.strategy,
                                            source: 'promotion_candidates',
                                            projectName: currentWorkspaceSaveName || 'Unsaved',
                                            addedAt: new Date().toISOString(),
                                            addedAtLabel: new Date().toLocaleString(),
                                            trackerStatus: 'queued',
                                            finalDecision: 'pending',
                                            notes: '',
                                            paperStartDate: '',
                                            observedPeriod: '',
                                            observedPnl: null,
                                            paperVerdict: 'pending',
                                            paperNotes: '',
                                            promotionMemo: '',
                                            liveReadiness: 'pending',
                                            liveReadinessNotes: '',
                                            promotionGateVerdict: entry?.promotionGate?.verdict || 'watch',
                                            promotionGateTier: entry?.promotionGate?.tier || 'conditional',
                                            promotionGateSevereFail: Boolean(entry?.promotionGate?.severeFail),
                                            promotionGatePassedCount: entry?.promotionGate?.passedCount || 0,
                                            promotionGateTotalChecks: entry?.promotionGate?.totalChecks || 0,
                                            failureModeCategory: entry.autoFailureCategory || 'uncategorized',
                                            failureMode: entry.autoFailureMessage || '',
                                            reviewedAt: null,
                                            reviewedAtLabel: '',
                                        })}
                                    >
                                        Add to shortlist
                                    </button>
                                    <button
                                        type='button'
                                        className='resultsActionButton'
                                        onClick={() => handleApplyCandidate(entry)}
                                    >
                                        Apply to Strategy
                                    </button>
                                </div>
                            </div>
                        )
                    })}
                </div>
            ) : (
                <div className='presetCompareEmpty'>No promotion candidate ranking has been run yet.</div>
            )}
        </div>
    )
}

function StrategyComparePane({
    authToken,
    chartSettings,
    strategyApplyResponse,
    shortlist = [],
    benchmarkStrategies = [],
    setStrategy,
    setStrategySetEntries,
    onOpenStrategy,
    onLogEvent,
    onAddBenchmark,
    onRemoveBenchmark,
    onUpdateBenchmark,
    initialState,
    onStudyComplete,
}) {
    const [selectedSide, setSelectedSide] = useState('all')
    const selectedInitialState = useMemo(
        () => initialState?.[selectedSide]?.payload || buildDefaultPromotionCandidateState(),
        [initialState, selectedSide],
    )
    const [compareState, setCompareState] = useSourcedState(selectedInitialState)

    const researchChartSettings = useMemo(
        () => buildResearchChartSettings(chartSettings, strategyApplyResponse),
        [chartSettings, strategyApplyResponse]
    )
    const baselinePayload = useMemo(
        () => buildResearchBaselinePayload(researchChartSettings, strategyApplyResponse),
        [researchChartSettings, strategyApplyResponse]
    )
    const candidateStrategies = useMemo(() => (
        [
            ...(shortlist || [])
                .filter((entry) => selectedSide === 'all' || !entry?.side || String(entry.side) === selectedSide)
                .filter((entry) => entry?.strategy)
                .map((entry, index) => {
                    const resolvedCandidate = buildResolvedResearchStrategyCollection(
                        researchChartSettings,
                        entry?.strategy || {},
                        entry?.strategies || [],
                        researchChartSettings?.indicators || [],
                    )

                    return {
                        id: `shortlist:${String(entry.shortlist_id || entry.id || entry.label || index)}`,
                        label: entry.label || 'Unnamed strategy',
                        strategy: resolvedCandidate.strategy,
                        strategies: resolvedCandidate.strategies,
                        source: entry,
                        sourceType: 'shortlist',
                    }
                }),
            ...(benchmarkStrategies || [])
                .filter((entry) => selectedSide === 'all' || !entry?.side || String(entry.side) === selectedSide)
                .filter((entry) => entry?.strategy)
                .map((entry, index) => {
                    const resolvedCandidate = buildResolvedResearchStrategyCollection(
                        researchChartSettings,
                        entry?.strategy || {},
                        entry?.strategies || [],
                        researchChartSettings?.indicators || [],
                    )

                    return {
                        id: `benchmark:${String(entry.benchmark_id || entry.id || entry.label || index)}`,
                        label: entry.label || 'Benchmark',
                        strategy: resolvedCandidate.strategy,
                        strategies: resolvedCandidate.strategies,
                        source: entry,
                        sourceType: 'benchmark',
                    }
                }),
        ]
    ), [shortlist, benchmarkStrategies, selectedSide, researchChartSettings])

    const walkforwardTestBars = useMemo(() => {
        const currentRows = Math.max(
            0,
            Number(strategyApplyResponse?.rows || strategyApplyResponse?.results?.length || 0),
        )
        if (currentRows <= 0) {
            return null
        }
        return Math.max(100, Math.round(currentRows * 0.2))
    }, [strategyApplyResponse])
    const walkforwardTrainBars = useMemo(() => (
        walkforwardTestBars ? Math.max(walkforwardTestBars, Math.round(walkforwardTestBars * 2)) : null
    ), [walkforwardTestBars])
    const timeframeOptions = useMemo(() => {
        const current = String(chartSettings?.timeframe || '').trim().toUpperCase()
        const base = ['M1', 'M5', 'M15', 'H1']
        return [...new Set([current, ...base].filter(Boolean))]
    }, [chartSettings])
    const symbolOptions = useMemo(() => {
        const current = String(chartSettings?.symbol || '').trim().toUpperCase()
        const base = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD']
        return [...new Set([current, ...base].filter(Boolean))]
    }, [chartSettings])

    async function handleCompare() {
        if (!authToken || !candidateStrategies.length) {
            return
        }

        setCompareState({
            ...buildDefaultPromotionCandidateState(),
            loading: true,
        })

        try {
            const payload = await runPresetCompareResearchJob({
                authToken,
                requestPayload: {
                    backtest: strategyApplyResponse?.request?.backtest || null,
                    baseline: baselinePayload,
                    presets: candidateStrategies.map((entry) => ({
                        id: entry.id,
                        label: entry.label,
                        strategy: entry.strategy,
                        strategies: entry.strategies,
                    })),
                    studyTimeframes: timeframeOptions,
                    studySymbols: symbolOptions,
                    walkforwardWindowBars: walkforwardTestBars,
                    walkforwardStepBars: walkforwardTestBars,
                    walkforwardTrainBars,
                    walkforwardTestBars,
                    chartContext: buildStudyChartContext(chartSettings, strategyApplyResponse),
                },
            })
            const nextState = {
                loading: false,
                error: '',
                result: {
                    baseline: payload?.baseline || null,
                    comparisons: Array.isArray(payload?.comparisons) ? payload.comparisons : [],
                    bestPresetId: '',
                    timeframeStudy: payload?.timeframe_study || null,
                    symbolStudy: payload?.symbol_study || null,
                    walkforwardStudy: payload?.walkforward_study || null,
                },
            }
            const timeframeMap = new Map((nextState.result?.timeframeStudy?.comparisons || []).map((entry) => [entry.id, entry]))
            const symbolMap = new Map((nextState.result?.symbolStudy?.comparisons || []).map((entry) => [entry.id, entry]))
            const walkforwardMap = new Map((nextState.result?.walkforwardStudy?.comparisons || []).map((entry) => [entry.id, entry]))
            const ranked = [...nextState.result.comparisons].sort((left, right) => {
                function score(entry) {
                    const timeframeRatio = Number(timeframeMap.get(entry.id)?.consistency?.win_ratio_vs_baseline || 0)
                    const symbolRatio = Number(symbolMap.get(entry.id)?.consistency?.win_ratio_vs_baseline || 0)
                    const stablePairRatio = Number(walkforwardMap.get(entry.id)?.train_test_consistency?.stable_pair_ratio || 0)
                    const currentDelta = Number(entry?.delta_vs_baseline?.net_pnl || 0)
                    return (currentDelta > 0 ? 1 : 0) * 20 + timeframeRatio * 30 + symbolRatio * 20 + stablePairRatio * 30
                }
                return score(right) - score(left)
            })
            nextState.result.bestPresetId = String(ranked[0]?.id || payload?.best_preset_id || '')
            setCompareState(nextState)
            onStudyComplete?.('strategy_compare', selectedSide, nextState)
            onLogEvent?.(`Research · Compared ${selectedSide} shortlisted strategies against the current strategy.`)
        } catch (error) {
            setCompareState({
                loading: false,
                error: error?.message || 'Failed to compare strategy candidates.',
                result: null,
            })
            onLogEvent?.(`Research strategy compare failed: ${error?.message || 'Failed to compare strategy candidates.'}`)
        }
    }

    function handleApplyCandidate(entry) {
        const candidate = candidateStrategies.find((item) => item.id === entry?.id)
        if (!candidate?.source?.strategy) {
            return
        }
        applyResearchStrategySelection({
            setStrategy,
            setStrategySetEntries,
            strategy: candidate.source.strategy,
            strategies: candidate.source.strategies || [],
        })
        onOpenStrategy?.()
        onLogEvent?.(`Research · Applied strategy candidate: ${candidate.label}.`)
    }

    function handleSaveCurrentBenchmark() {
        const requestStrategy = strategyApplyResponse?.request?.strategy
        if (!requestStrategy) {
            return
        }
        const timestamp = new Date()
        onAddBenchmark?.({
            benchmark_id: `benchmark:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`,
            label: `Current strategy · ${timestamp.toLocaleString()}`,
            side: selectedSide === 'all' ? 'all' : selectedSide,
            strategy: requestStrategy,
            strategies: Array.isArray(strategyApplyResponse?.request?.strategies)
                ? strategyApplyResponse.request.strategies
                : [],
            source: 'current_strategy',
            notes: '',
            addedAt: timestamp.toISOString(),
            addedAtLabel: timestamp.toLocaleString(),
        })
    }

    const timeframeMap = new Map((compareState.result?.timeframeStudy?.comparisons || []).map((entry) => [entry.id, entry]))
    const symbolMap = new Map((compareState.result?.symbolStudy?.comparisons || []).map((entry) => [entry.id, entry]))
    const walkforwardMap = new Map((compareState.result?.walkforwardStudy?.comparisons || []).map((entry) => [entry.id, entry]))

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Strategy compare</div>
                    <div className='presetStudyMeta'>
                        Compare the current strategy against shortlisted research candidates on the same market context, with an attached train/test walk-forward check.
                    </div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={handleCompare}
                    disabled={!authToken || !candidateStrategies.length || compareState.loading}
                >
                    {compareState.loading ? 'Comparing...' : 'Compare strategies'}
                </button>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={handleSaveCurrentBenchmark}
                    disabled={!strategyApplyResponse?.request?.strategy}
                >
                    Save current as benchmark
                </button>
            </div>

            <div className='presetCompareSideTabs'>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'all' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('all')}
                >
                    All
                </button>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'long' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('long')}
                >
                    Long
                </button>
                <button
                    type='button'
                    className={`presetCompareSideTab ${selectedSide === 'short' ? 'active' : ''}`}
                    onClick={() => setSelectedSide('short')}
                >
                    Short
                </button>
            </div>

            <div className='presetStudyMeta'>
                Candidates: {candidateStrategies.length}
                {' · '}
                Benchmarks: {Number((benchmarkStrategies || []).length)}
                {' · '}
                Timeframes: {timeframeOptions.join(' · ')}
                {' · '}
                Symbols: {symbolOptions.join(' · ')}
                {' · '}
                Walk-forward: {walkforwardTrainBars ? `${Number(walkforwardTrainBars).toLocaleString()} train` : '-'}
                {' / '}
                {walkforwardTestBars ? `${Number(walkforwardTestBars).toLocaleString()} test` : '-'}
            </div>

            {benchmarkStrategies.length > 0 ? (
                <div className='researchDecisionLogPanel'>
                    <div className='researchDecisionLogTitle'>Benchmark archive</div>
                    <div className='promotionCandidateCards'>
                        {benchmarkStrategies.map((entry) => (
                            <div key={`benchmark-archive-${entry.benchmark_id}`} className='promotionCandidateCard tone-watch'>
                                <div className='presetCompareCardHeader'>
                                    <div className='presetCompareCardTitle'>{entry.label || 'Benchmark'}</div>
                                    <div className='promotionCandidateBadges'>
                                        <div className='presetCompareBadge'>Benchmark</div>
                                    </div>
                                </div>
                                <div className='presetCompareMetrics'>
                                    <div><span>Side</span><strong>{String(entry.side || 'all').toUpperCase()}</strong></div>
                                    <div><span>Source</span><strong>{entry.source || 'manual'}</strong></div>
                                    <div><span>Saved at</span><strong>{entry.addedAtLabel || '-'}</strong></div>
                                </div>
                                <div className='paperTrackerGrid'>
                                    <label className='paperTrackerField'>
                                        <span>Label</span>
                                        <input
                                            type='text'
                                            value={String(entry.label || '')}
                                            onChange={(event) => onUpdateBenchmark?.(entry.benchmark_id, {
                                                label: event.target.value,
                                            })}
                                        />
                                    </label>
                                </div>
                                <label className='paperTrackerField notes'>
                                    <span>Notes</span>
                                    <textarea
                                        value={String(entry.notes || '')}
                                        onChange={(event) => onUpdateBenchmark?.(entry.benchmark_id, {
                                            notes: event.target.value,
                                        })}
                                        placeholder='Why keep this benchmark around?'
                                    />
                                </label>
                            </div>
                        ))}
                    </div>
                </div>
            ) : null}

            {compareState.error ? (
                <div className='presetCompareError'>{compareState.error}</div>
            ) : null}

            {!candidateStrategies.length ? (
                <div className='presetCompareEmpty'>Add candidates to the paper shortlist or save the current strategy as a benchmark to compare full strategies here.</div>
            ) : null}

            {compareState.result?.comparisons?.length > 0 ? (
                <div className='presetCompareCards'>
                    {compareState.result.comparisons.map((entry) => {
                        const summary = entry?.summary || {}
                        const timeframeStudy = timeframeMap.get(entry.id) || null
                        const symbolStudy = symbolMap.get(entry.id) || null
                        const walkforward = walkforwardMap.get(entry.id) || null
                        const trainTest = walkforward?.train_test_consistency || {}
                        const isBest = compareState.result?.bestPresetId === entry.id
                        return (
                            <div key={entry.id} className={`presetCompareCard ${isBest ? 'isBest' : ''}`}>
                                <div className='presetCompareCardHeader'>
                                    <div className='presetCompareCardTitle'>{entry.label}</div>
                                    <div className='promotionCandidateBadges'>
                                        {getCompareStrategyCount(entry) > 1 ? (
                                            <div className='presetCompareBadge isPortfolio'>
                                                Portfolio · {getCompareStrategyCount(entry)}
                                            </div>
                                        ) : null}
                                        {isBest ? <div className='presetCompareBadge'>Best</div> : null}
                                        {candidateStrategies.find((item) => item.id === entry.id)?.sourceType === 'benchmark'
                                            ? <div className='presetCompareBadge'>Benchmark</div>
                                            : null}
                                    </div>
                                </div>
                                <div className='presetCompareMetrics'>
                                    <div><span>Net PnL</span><strong>{formatPresetMetric(summary.net_pnl)}</strong></div>
                                    <div><span>Win rate</span><strong>{formatPresetMetric(summary.win_rate, 'percent')}</strong></div>
                                    <div><span>Avg trade</span><strong>{formatPresetMetric(summary.expectancy_per_trade)}</strong></div>
                                    <div><span>Max DD</span><strong>{formatPresetMetric(summary.max_drawdown)}</strong></div>
                                    <div><span>Timeframes</span><strong>{formatPresetMetric(timeframeStudy?.consistency?.win_ratio_vs_baseline, 'percent')}</strong></div>
                                    <div><span>Symbols</span><strong>{formatPresetMetric(symbolStudy?.consistency?.win_ratio_vs_baseline, 'percent')}</strong></div>
                                    <div><span>Stable train/test</span><strong>{formatPresetMetric(trainTest.stable_pair_ratio, 'percent')}</strong></div>
                                </div>
                                {getPortfolioContributionPreview(entry) ? (
                                    <div className='presetCompareContributionPreview'>
                                        Top contribution: {getPortfolioContributionPreview(entry)}
                                    </div>
                                ) : null}
                                {entry?.delta_vs_baseline ? (
                                    <div className='presetCompareDeltaGrid'>
                                        <div><span>dPnL</span><strong>{formatPresetMetric(entry.delta_vs_baseline.net_pnl)}</strong></div>
                                        <div><span>dWin</span><strong>{formatPresetMetric(entry.delta_vs_baseline.win_rate, 'percent')}</strong></div>
                                        <div><span>dAvg</span><strong>{formatPresetMetric(entry.delta_vs_baseline.expectancy_per_trade)}</strong></div>
                                        <div><span>dDD</span><strong>{formatPresetMetric(entry.delta_vs_baseline.max_drawdown)}</strong></div>
                                        <div><span>Train→test</span><strong>{formatPresetMetric(trainTest.avg_train_to_test_net_pnl_shift)}</strong></div>
                                    </div>
                                ) : null}
                                <div className='presetCompareActions'>
                                    <button
                                        type='button'
                                        className='resultsActionButton'
                                        onClick={() => handleApplyCandidate(entry)}
                                    >
                                        Apply to Strategy
                                    </button>
                                    {candidateStrategies.find((item) => item.id === entry.id)?.sourceType === 'benchmark' ? (
                                        <button
                                            type='button'
                                            className='resultsActionButton'
                                            onClick={() => onRemoveBenchmark?.(candidateStrategies.find((item) => item.id === entry.id)?.source?.benchmark_id)}
                                        >
                                            Remove benchmark
                                        </button>
                                    ) : null}
                                </div>
                            </div>
                        )
                    })}
                </div>
            ) : candidateStrategies.length > 0 ? (
                <div className='presetCompareEmpty'>No strategy comparison has been run yet.</div>
            ) : null}
        </div>
    )
}

function StudyArchivePane({
    studyRuns = [],
    selectedRunId: externalSelectedRunId = '',
    onSelectedRunIdChange = null,
    authToken,
    isRemoteSource = false,
    onLoadRunDetail = null,
    onLogEvent,
    onDeleteRun,
    onUpdateRun,
    setStrategy,
    setStrategySetEntries,
    setBacktest,
    onOpenStrategy,
    onHydrateBacktestResult,
    onOpenResults,
}) {
    const [selectedRunIdDraft, setSelectedRunIdDraft] = useState(() => externalSelectedRunId || studyRuns[0]?.id || '')
    const [isLoadingPayload, setIsLoadingPayload] = useState(false)
    const [payloadLoadError, setPayloadLoadError] = useState('')
    const selectedRunId = useMemo(() => {
        const externalId = String(externalSelectedRunId || '')
        if (externalId && studyRuns.some((entry) => String(entry?.id || '') === externalId)) {
            return externalId
        }
        const currentId = String(selectedRunIdDraft || '')
        if (currentId && studyRuns.some((entry) => String(entry?.id || '') === currentId)) {
            return currentId
        }
        return String(studyRuns[0]?.id || '')
    }, [externalSelectedRunId, selectedRunIdDraft, studyRuns])
    const selectedRun = studyRuns.find((entry) => entry?.id === selectedRunId) || studyRuns[0] || null
    const selectedRunPayloadLoaded = selectedRun?.payload_loaded !== false
    const selectedRunPayload = selectedRunPayloadLoaded ? (selectedRun?.payload || null) : null
    const selectedRunDraftState = useMemo(() => ({
        label: String(selectedRun?.run_label || ''),
        notes: String(selectedRun?.run_notes || ''),
        pinned: Boolean(selectedRun?.pinned),
    }), [selectedRun?.pinned, selectedRun?.run_label, selectedRun?.run_notes])
    const [selectedRunDraft, setSelectedRunDraft] = useSourcedState(selectedRunDraftState)
    const draftLabel = selectedRunDraft.label
    const draftNotes = selectedRunDraft.notes
    const draftPinned = selectedRunDraft.pinned
    const shouldLoadRemotePayload = Boolean(
        isRemoteSource
        && authToken
        && selectedRun?.id
        && !selectedRunPayloadLoaded
        && onLoadRunDetail
    )
    const effectiveIsLoadingPayload = shouldLoadRemotePayload ? isLoadingPayload : false
    const effectivePayloadLoadError = shouldLoadRemotePayload ? payloadLoadError : ''

    useEffect(() => {
        if (!shouldLoadRemotePayload) {
            return undefined
        }

        let cancelled = false

        async function loadPayload() {
            setIsLoadingPayload(true)
            setPayloadLoadError('')
            try {
                await onLoadRunDetail(selectedRun.id)
            } catch (error) {
                if (cancelled) {
                    return
                }
                const message = error?.message || 'Could not load archive run details.'
                setPayloadLoadError(message)
                onLogEvent?.(`Research · Could not load archived run details: ${message}`)
            } finally {
                if (!cancelled) {
                    setIsLoadingPayload(false)
                }
            }
        }

        void loadPayload()

        return () => {
            cancelled = true
        }
    }, [onLoadRunDetail, onLogEvent, selectedRun?.id, shouldLoadRemotePayload])

    async function handleCopyPayload() {
        if (!selectedRunPayload) {
            return
        }
        try {
            await navigator.clipboard.writeText(JSON.stringify(selectedRunPayload, null, 2))
            onLogEvent?.(`Research · Copied study run "${selectedRun.run_name || selectedRun.type}" JSON.`)
        } catch (error) {
            onLogEvent?.(`Research · Could not copy study run payload: ${error?.message || 'clipboard error'}`)
        }
    }

    async function handleSaveMetadata() {
        if (!selectedRun?.id || !onUpdateRun) {
            return
        }
        await onUpdateRun(selectedRun.id, {
            run_label: draftLabel,
            run_notes: draftNotes,
            pinned: draftPinned,
        })
    }

    function handleRestoreStrategySnapshot() {
        const snapshot = selectedRun?.strategy_snapshot
            || selectedRunPayload?.strategySnapshot
            || selectedRunPayload?.strategy_snapshot
            || null

        if (!snapshot?.strategy || !setStrategy) {
            onLogEvent?.('Research · This archived run does not contain a restorable strategy snapshot.')
            return
        }

        applyResearchStrategySelection({
            setStrategy,
            setStrategySetEntries,
            strategy: snapshot.strategy,
            strategies: snapshot?.strategies || [],
        })

        if (setBacktest && snapshot?.backtest) {
            setBacktest(cloneSerializable(snapshot.backtest, null))
        }

        onOpenStrategy?.()
        onLogEvent?.(`Research · Restored strategy state from archive run "${selectedRun?.run_name || selectedRun?.type || 'study'}".`)
    }

    function handleLoadPipelineReport() {
        const hydrated = buildHydratedBacktestPayloadFromPipelineRun(selectedRun)
        if (!hydrated) {
            onLogEvent?.('Research · This archive entry does not contain a loadable pipeline report.')
            return
        }
        onHydrateBacktestResult?.(hydrated)
        onOpenResults?.()
        onLogEvent?.(`Research · Loaded pipeline report from "${selectedRun?.run_name || selectedRun?.type || 'study'}" into Results.`)
    }

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Study archive</div>
                    <div className='presetStudyMeta'>
                        Versioned research runs saved with the project. Use this to inspect what was measured, not just the latest visible panel state.
                    </div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={() => void handleCopyPayload()}
                    disabled={!selectedRunPayload || effectiveIsLoadingPayload}
                >
                    Copy selected run JSON
                </button>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={handleRestoreStrategySnapshot}
                    disabled={!selectedRun?.strategy_snapshot && !selectedRunPayload?.strategySnapshot && !selectedRunPayload?.strategy_snapshot}
                >
                    Restore strategy state
                </button>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={handleLoadPipelineReport}
                    disabled={!selectedRunPayload?.pipeline || effectiveIsLoadingPayload}
                >
                    Load report into Results
                </button>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={() => selectedRun?.id && onDeleteRun?.(selectedRun.id)}
                    disabled={!authToken || !selectedRun?.id}
                >
                    Delete selected run
                </button>
            </div>

            {studyRuns.length > 0 ? (
                <div className='statisticsLayout researchLayout'>
                    <aside className='statisticsSidebar researchSidebar'>
                        <div className='statisticsSidebarTitle'>Saved runs</div>
                        <div className='statisticsList'>
                            {studyRuns.map((entry) => (
                                <button
                                    key={entry.id}
                                    type='button'
                                    className={`statisticsListButton ${selectedRun?.id === entry.id ? 'active' : ''}`}
                                    onClick={() => {
                                        setSelectedRunIdDraft(entry.id)
                                        onSelectedRunIdChange?.(entry.id)
                                    }}
                                >
                                    {entry.run_name || entry.type}
                                </button>
                            ))}
                        </div>
                    </aside>
                    <div className='statisticsContent researchContent'>
                        {selectedRun ? (
                            <>
                                <div className='presetCompareBaseline'>
                                    <div className='presetCompareBaselineTitle'>{selectedRun.run_name || selectedRun.type}</div>
                                    <div className='presetCompareMetrics'>
                                        <div><span>Version</span><strong>{selectedRun.version || '-'}</strong></div>
                                        <div><span>Type</span><strong>{String(selectedRun.type || '').replaceAll('_', ' ')}</strong></div>
                                        <div><span>Side</span><strong>{String(selectedRun.side || 'default').toUpperCase()}</strong></div>
                                        <div><span>Saved at</span><strong>{selectedRun.atLabel || '-'}</strong></div>
                                        <div><span>Best</span><strong>{selectedRun.best_label || '-'}</strong></div>
                                        <div><span>Comparisons</span><strong>{formatInteger(selectedRun.comparison_count)}</strong></div>
                                        <div><span>Strategy snapshot</span><strong>{selectedRun?.strategy_snapshot || selectedRunPayload?.strategySnapshot || selectedRunPayload?.strategy_snapshot ? 'Yes' : 'No'}</strong></div>
                                    </div>
                                </div>
                                {(selectedRun?.strategy_snapshot || selectedRunPayload?.strategySnapshot || selectedRunPayload?.strategy_snapshot) ? (
                                    <div className='presetCompareRecommendation'>
                                        <strong>Restore ready:</strong>
                                        {' '}
                                        This archive entry includes the baseline strategy and backtest settings used when the study was generated.
                                    </div>
                                ) : null}
                                <div className='paperTrackerGrid'>
                                    <label className='paperTrackerField'>
                                        <span>Archive label</span>
                                        <input
                                            type='text'
                                            value={draftLabel}
                                            onChange={(event) => setSelectedRunDraft((current) => ({
                                                ...current,
                                                label: event.target.value,
                                            }))}
                                            placeholder='Optional human-friendly label'
                                        />
                                    </label>
                                    <label className='paperTrackerField'>
                                        <span>Pinned</span>
                                        <select
                                            value={draftPinned ? 'yes' : 'no'}
                                            onChange={(event) => setSelectedRunDraft((current) => ({
                                                ...current,
                                                pinned: event.target.value === 'yes',
                                            }))}
                                        >
                                            <option value='no'>No</option>
                                            <option value='yes'>Yes</option>
                                        </select>
                                    </label>
                                </div>
                                <label className='paperTrackerField notes'>
                                    <span>Archive notes</span>
                                    <textarea
                                        value={draftNotes}
                                        onChange={(event) => setSelectedRunDraft((current) => ({
                                            ...current,
                                            notes: event.target.value,
                                        }))}
                                        placeholder='Why does this run matter? What did it prove or invalidate?'
                                    />
                                </label>
                                <div className='presetCompareActions'>
                                    <button
                                        type='button'
                                        className='resultsActionButton'
                                        onClick={() => void handleSaveMetadata()}
                                        disabled={!authToken || !selectedRun?.id}
                                    >
                                        Save archive metadata
                                    </button>
                                </div>
                                <div className='researchReviewChecklist'>
                                    <div className='researchReviewChecklistTitle'>Run payload preview</div>
                                    {effectiveIsLoadingPayload ? (
                                        <div className='presetCompareEmpty'>Loading archived run payload...</div>
                                    ) : effectivePayloadLoadError ? (
                                        <div className='presetCompareEmpty'>{effectivePayloadLoadError}</div>
                                    ) : (
                                        <pre className='researchPayloadPreview'>
                                            {JSON.stringify(selectedRunPayload || {}, null, 2)}
                                        </pre>
                                    )}
                                </div>
                            </>
                        ) : null}
                    </div>
                </div>
            ) : (
                <div className='presetCompareEmpty'>No study runs have been saved yet.</div>
            )}
        </div>
    )
}

function ResearchOperationsPane({
    authToken,
    researchJobs = [],
    researchBatches = [],
    researchCampaigns = [],
    onLogEvent,
    onRefreshJobs,
    onRefreshBatches,
    onRefreshCampaigns,
    onCancelJob,
    onCancelBatch,
    onCreateBatch,
    onCreateCampaign,
    onLaunchCampaign,
    onDeleteCampaign,
    onRerunJob,
    onSaveJobStrategy,
    onOpenArchivedRun,
}) {
    const [statusFilter, setStatusFilter] = useState('all')
    const [typeFilter, setTypeFilter] = useState('all')
    const [batchLabel, setBatchLabel] = useState('Research batch')
    const [campaignLabel, setCampaignLabel] = useState('Research campaign')
    const [campaignDescription, setCampaignDescription] = useState('')
    const activeJobs = researchJobs.filter((entry) => ['queued', 'running'].includes(String(entry?.status || '').toLowerCase()))
    const finishedJobs = researchJobs.filter((entry) => ['completed', 'failed', 'cancelled'].includes(String(entry?.status || '').toLowerCase()))
    const activeBatches = researchBatches.filter((entry) => ['queued', 'running'].includes(String(entry?.status || '').toLowerCase()))
    const finishedBatches = researchBatches.filter((entry) => ['completed', 'failed', 'cancelled'].includes(String(entry?.status || '').toLowerCase()))
    const totalCampaigns = researchCampaigns.length
    const jobTypeOptions = Array.from(new Set(researchJobs.map((entry) => String(entry?.job_type || '').trim()).filter(Boolean)))
    const filteredJobs = researchJobs.filter((job) => {
        const status = String(job?.status || '').trim().toLowerCase()
        const jobType = String(job?.job_type || '').trim().toLowerCase()
        const statusMatches = statusFilter === 'all' ? true : status === statusFilter
        const typeMatches = typeFilter === 'all' ? true : jobType === typeFilter
        return statusMatches && typeMatches
    })
    const latestBatch = researchBatches[0] || null

    async function handleCreateBatch() {
        if (!filteredJobs.length || !onCreateBatch) {
            onLogEvent?.('Research · Select at least one backend job before creating a batch.')
            return
        }

        await onCreateBatch({
            label: String(batchLabel || '').trim() || 'Research batch',
            jobs: filteredJobs.map((job) => ({
                job_type: job?.job_type || 'preset_compare',
                request: job?.request || {},
                run_label: job?.run_label || '',
                run_notes: job?.run_notes || '',
            })),
        })
    }

    async function handleCreateCampaign() {
        if (!filteredJobs.length || !onCreateCampaign) {
            onLogEvent?.('Research · Select at least one backend job before saving a campaign.')
            return
        }

        await onCreateCampaign({
            label: String(campaignLabel || '').trim() || 'Research campaign',
            description: String(campaignDescription || '').trim(),
            jobs: filteredJobs.map((job) => ({
                job_type: job?.job_type || 'preset_compare',
                request: job?.request || {},
                run_label: job?.run_label || '',
                run_notes: job?.run_notes || '',
            })),
        })
    }

    return (
        <div className='presetStudyPanel'>
            <div className='presetCompareHeader'>
                <div className='presetCompareText'>
                    <div className='presetStudyTitle'>Research Ops</div>
                    <div className='presetStudyMeta'>
                        Live execution monitor for backend research jobs. Use this to watch progress, spot failures and cancel work that no longer makes sense.
                    </div>
                </div>
                <button
                    type='button'
                    className='resultsActionButton'
                    onClick={() => void onRefreshJobs?.()}
                    disabled={!authToken}
                >
                    Refresh jobs
                </button>
            </div>

            {authToken ? (
                <>
                    <div className='researchJobsSummary'>
                        <div className='researchJobsSummaryCard'>
                            <span>Active jobs</span>
                            <strong>{formatInteger(activeJobs.length)}</strong>
                        </div>
                        <div className='researchJobsSummaryCard'>
                            <span>Finished jobs</span>
                            <strong>{formatInteger(finishedJobs.length)}</strong>
                        </div>
                        <div className='researchJobsSummaryCard'>
                            <span>Active batches</span>
                            <strong>{formatInteger(activeBatches.length)}</strong>
                        </div>
                        <div className='researchJobsSummaryCard'>
                            <span>Finished batches</span>
                            <strong>{formatInteger(finishedBatches.length)}</strong>
                        </div>
                        <div className='researchJobsSummaryCard'>
                            <span>Campaigns</span>
                            <strong>{formatInteger(totalCampaigns)}</strong>
                        </div>
                    </div>
                    <div className='researchJobsPanel'>
                        <div className='presetCompareBaseline'>
                            <div className='presetCompareBaselineTitle'>Batch orchestration</div>
                            <div className='presetCompareRecommendation'>
                                Group backend jobs into a sequential batch so the backend can keep exploring without the browser driving every execution.
                            </div>
                            <div className='paperTrackerGrid'>
                                <label className='paperTrackerField'>
                                    <span>Batch label</span>
                                    <input
                                        type='text'
                                        value={batchLabel}
                                        onChange={(event) => setBatchLabel(event.target.value)}
                                        placeholder='Research batch'
                                    />
                                </label>
                                <label className='paperTrackerField'>
                                    <span>Campaign label</span>
                                    <input
                                        type='text'
                                        value={campaignLabel}
                                        onChange={(event) => setCampaignLabel(event.target.value)}
                                        placeholder='Research campaign'
                                    />
                                </label>
                            </div>
                            <label className='paperTrackerField notes'>
                                <span>Campaign description</span>
                                <textarea
                                    value={campaignDescription}
                                    onChange={(event) => setCampaignDescription(event.target.value)}
                                    placeholder='Optional reusable description for this backend-controlled research suite.'
                                />
                            </label>
                            <div className='presetCompareActions'>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => void handleCreateBatch()}
                                    disabled={!authToken || !filteredJobs.length}
                                >
                                    Create batch from filtered jobs
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => void handleCreateCampaign()}
                                    disabled={!authToken || !filteredJobs.length}
                                >
                                    Save campaign from filtered jobs
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => void onRefreshBatches?.()}
                                    disabled={!authToken}
                                >
                                    Refresh batches
                                </button>
                                <button
                                    type='button'
                                    className='resultsActionButton'
                                    onClick={() => void onRefreshCampaigns?.()}
                                    disabled={!authToken}
                                >
                                    Refresh campaigns
                                </button>
                            </div>
                        </div>
                        {latestBatch ? (
                            <div className='researchJobCard'>
                                <div className='researchJobHeader'>
                                    <div>
                                        <div className='researchJobTitle'>{latestBatch?.label || 'Research batch'}</div>
                                        <div className='researchJobMeta'>
                                            {String(latestBatch?.status || 'queued')} · {formatInteger(latestBatch?.completed_jobs || 0)}/{formatInteger(latestBatch?.total_jobs || 0)} completed
                                        </div>
                                    </div>
                                    {['queued', 'running'].includes(String(latestBatch?.status || '').toLowerCase()) ? (
                                        <button
                                            type='button'
                                            className='resultsActionButton'
                                            onClick={() => void onCancelBatch?.(latestBatch?.id)}
                                        >
                                            Cancel batch
                                        </button>
                                    ) : null}
                                </div>
                                <div className='researchJobDetail'>{latestBatch?.detail || 'No batch detail yet.'}</div>
                                <div className='researchJobProgressRow'>
                                    <div className='researchJobProgressTrack'>
                                        <div
                                            className='researchJobProgressFill'
                                            style={{ width: `${Math.max(0, Math.min(100, Math.round(Number(latestBatch?.progress || 0) * 100)))}%` }}
                                        />
                                    </div>
                                    <span>{Math.max(0, Math.min(100, Math.round(Number(latestBatch?.progress || 0) * 100)))}%</span>
                                </div>
                                <div className='researchJobMeta'>
                                    Failed: {formatInteger(latestBatch?.failed_jobs || 0)} · Cancelled: {formatInteger(latestBatch?.cancelled_jobs || 0)}
                                </div>
                            </div>
                        ) : (
                            <div className='presetCompareEmpty'>No backend batches yet.</div>
                        )}
                    </div>
                    <div className='researchJobsFilters'>
                        <label className='paperTrackerField'>
                            <span>Status</span>
                            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                                <option value='all'>All</option>
                                <option value='queued'>Queued</option>
                                <option value='running'>Running</option>
                                <option value='completed'>Completed</option>
                                <option value='failed'>Failed</option>
                                <option value='cancelled'>Cancelled</option>
                            </select>
                        </label>
                        <label className='paperTrackerField'>
                            <span>Type</span>
                            <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
                                <option value='all'>All</option>
                                {jobTypeOptions.map((option) => (
                                    <option key={option} value={String(option).toLowerCase()}>{option}</option>
                                ))}
                            </select>
                        </label>
                    </div>
                    {filteredJobs.length ? (
                        <div className='researchJobsList'>
                            {filteredJobs.slice(0, 20).map((job) => {
                                const status = String(job?.status || '').trim().toLowerCase()
                                const progress = Math.max(0, Math.min(100, Math.round(Number(job?.progress || 0) * 100)))
                                const createdAtLabel = job?.created_at
                                    ? new Date(Number(job.created_at) * 1000).toLocaleString()
                                    : '-'

                                return (
                                    <div key={`research-job-${job?.id}`} className='researchJobCard'>
                                        <div className='researchJobHeader'>
                                            <div>
                                                <div className='researchJobTitle'>{job?.phase_label || job?.job_type || 'Research job'}</div>
                                                <div className='researchJobMeta'>
                                                    {String(job?.job_type || 'job').replaceAll('_', ' ')} · {status || 'queued'} · {createdAtLabel}
                                                </div>
                                            </div>
                                            {['queued', 'running'].includes(status) ? (
                                                <button
                                                    type='button'
                                                    className='resultsActionButton'
                                                    onClick={() => void onCancelJob?.(job?.id)}
                                                >
                                                    Cancel
                                                </button>
                                            ) : (
                                                <>
                                                    <button
                                                        type='button'
                                                        className='resultsActionButton'
                                                        onClick={() => void onSaveJobStrategy?.(job)}
                                                        disabled={!authToken}
                                                    >
                                                        Save to Strategy
                                                    </button>
                                                    <button
                                                        type='button'
                                                        className='resultsActionButton'
                                                        onClick={() => void onRerunJob?.(job)}
                                                        disabled={!authToken}
                                                    >
                                                        Re-run
                                                    </button>
                                                    <button
                                                        type='button'
                                                        className='resultsActionButton'
                                                        onClick={() => onOpenArchivedRun?.(job?.run_id)}
                                                        disabled={!job?.run_id}
                                                    >
                                                        Open archive
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                        <div className='researchJobDetail'>{job?.detail || job?.error || 'No detail yet.'}</div>
                                        <div className='researchJobProgressRow'>
                                            <div className='researchJobProgressTrack'>
                                                <div className='researchJobProgressFill' style={{ width: `${progress}%` }} />
                                            </div>
                                            <span>{progress}%</span>
                                        </div>
                                        {job?.error ? (
                                            <div className='researchJobMeta'>{job.error}</div>
                                        ) : null}
                                    </div>
                                )
                            })}
                        </div>
                    ) : (
                        <div className='presetCompareEmpty'>No backend research jobs yet.</div>
                    )}
                    {researchBatches.length ? (
                        <div className='researchJobsList'>
                            {researchBatches.slice(0, 10).map((batch) => {
                                const status = String(batch?.status || '').trim().toLowerCase()
                                const progress = Math.max(0, Math.min(100, Math.round(Number(batch?.progress || 0) * 100)))
                                const createdAtLabel = batch?.created_at
                                    ? new Date(Number(batch.created_at) * 1000).toLocaleString()
                                    : '-'

                                return (
                                    <div key={`research-batch-${batch?.id}`} className='researchJobCard'>
                                        <div className='researchJobHeader'>
                                            <div>
                                                <div className='researchJobTitle'>{batch?.label || 'Research batch'}</div>
                                                <div className='researchJobMeta'>
                                                    batch · {status || 'queued'} · {createdAtLabel}
                                                </div>
                                            </div>
                                            {['queued', 'running'].includes(status) ? (
                                                <button
                                                    type='button'
                                                    className='resultsActionButton'
                                                    onClick={() => void onCancelBatch?.(batch?.id)}
                                                >
                                                    Cancel
                                                </button>
                                            ) : null}
                                        </div>
                                        <div className='researchJobDetail'>{batch?.detail || batch?.error || 'No detail yet.'}</div>
                                        <div className='researchJobProgressRow'>
                                            <div className='researchJobProgressTrack'>
                                                <div className='researchJobProgressFill' style={{ width: `${progress}%` }} />
                                            </div>
                                            <span>{progress}%</span>
                                        </div>
                                        <div className='researchJobMeta'>
                                            Jobs: {formatInteger(batch?.completed_jobs || 0)}/{formatInteger(batch?.total_jobs || 0)} · Failed: {formatInteger(batch?.failed_jobs || 0)} · Cancelled: {formatInteger(batch?.cancelled_jobs || 0)}
                                        </div>
                                    </div>
                                )
                            })}
                        </div>
                    ) : null}
                    {researchCampaigns.length ? (
                        <div className='researchJobsList'>
                            {researchCampaigns.slice(0, 12).map((campaign) => {
                                const jobsCount = Math.max(
                                    Number(campaign?.batch_job_count || 0),
                                    Number(campaign?.job_count || 0),
                                    Array.isArray(campaign?.request?.batch_jobs) ? campaign.request.batch_jobs.length : 0,
                                    Array.isArray(campaign?.request?.jobs) ? campaign.request.jobs.length : 0,
                                )
                                const updatedAtLabel = campaign?.updated_at
                                    ? new Date(Number(campaign.updated_at) * 1000).toLocaleString()
                                    : '-'

                                return (
                                    <div key={`research-campaign-${campaign?.id}`} className='researchJobCard'>
                                        <div className='researchJobHeader'>
                                            <div>
                                                <div className='researchJobTitle'>{campaign?.label || 'Research campaign'}</div>
                                                <div className='researchJobMeta'>
                                                    campaign · {formatInteger(jobsCount)} jobs · {updatedAtLabel}
                                                </div>
                                            </div>
                                            <div>
                                                <button
                                                    type='button'
                                                    className='resultsActionButton'
                                                    onClick={() => void onLaunchCampaign?.(campaign?.id)}
                                                    disabled={!authToken}
                                                >
                                                    Run campaign
                                                </button>
                                                <button
                                                    type='button'
                                                    className='resultsActionButton'
                                                    onClick={() => void onDeleteCampaign?.(campaign?.id)}
                                                    disabled={!authToken}
                                                >
                                                    Delete
                                                </button>
                                            </div>
                                        </div>
                                        <div className='researchJobDetail'>{campaign?.description || 'Reusable backend-controlled research suite.'}</div>
                                    </div>
                                )
                            })}
                        </div>
                    ) : null}
                </>
            ) : (
                <div className='presetCompareEmpty'>Sign in to monitor backend research operations.</div>
            )}
        </div>
    )
}

function StatisticsEvaluationCard({ evaluation }) {
    if (!evaluation) {
        return null
    }

    const tone = getEvaluationTone(evaluation.scoreOutOfTen)

    return (
        <div
            className={`statisticsEvaluationCard ${tone}`}
            style={getEvaluationStyle(evaluation.scoreOutOfTen)}
        >
            <div className='statisticsEvaluationHeader'>
                <div className='statisticsEvaluationLabel'>Strategy score</div>
                <div className='statisticsEvaluationMeta'>Weighted from the most relevant stats only</div>
            </div>

            <div className='statisticsEvaluationScoreRow'>
                <div className='statisticsEvaluationScore'>{evaluation.scoreOutOfTen.toFixed(1)}</div>
                <div className='statisticsEvaluationScoreScale'>/ 10</div>
            </div>

            <div className='statisticsEvaluationBreakdown'>
                {evaluation.criteria.map((criterion) => (
                    <div key={criterion.key} className='statisticsEvaluationItem'>
                        <div className='statisticsEvaluationItemName'>{criterion.label}</div>
                        <div className='statisticsEvaluationItemTarget'>{criterion.targetLabel}</div>
                        <div className='statisticsEvaluationItemActual'>
                            {formatEvaluationValue(criterion.actualValue, criterion.valueFormat)}
                            {' / '}
                            {formatEvaluationValue(criterion.targetValue, criterion.valueFormat)}
                        </div>
                        <div className='statisticsEvaluationItemValue'>{criterion.achievedPct.toFixed(0)}%</div>
                    </div>
                ))}
            </div>
        </div>
    )
}

function ExportPane({
    request,
    backtest,
    stats,
    results,
    evaluation,
}) {
    const [exportScope, setExportScope] = useState('full_report')
    const [exportTarget, setExportTarget] = useState('clipboard')
    const [exportFormatDraft, setExportFormatDraft] = useState('json')
    const exportFormat = normalizeExportFormatForTarget(exportTarget, exportFormatDraft)

    async function handleExport() {
        const exportedAt = new Date().toISOString()
        const title = 'Robotineeko Strategy Report'
        const basePayload = {
            exported_at: exportedAt,
            scope: exportScope,
            strategy: request?.strategy || null,
            backtest: backtest || null,
            strategy_score: evaluation
                ? {
                    score_out_of_ten: evaluation.scoreOutOfTen,
                    percent_score: evaluation.percentScore,
                    criteria: evaluation.criteria,
                }
                : null,
            statistics: stats || null,
            results: results || [],
        }

        const statisticsRows = buildStatisticsRowsForExport(stats || {}, evaluation)
        const csvRows = exportScope === 'trade_results'
            ? (results || [])
            : exportScope === 'statistics_only'
                ? statisticsRows
                : exportScope === 'request_payload'
                    ? [{ strategy: JSON.stringify(request?.strategy || null), backtest: JSON.stringify(backtest || null) }]
                    : [
                        ...statisticsRows,
                        ...(results || []).map((row, index) => ({ row_type: 'trade_result', row_index: index, ...row })),
                    ]
        const csvContent = buildCsvString(csvRows)

        if (exportTarget === 'clipboard') {
            const clipboardContent = exportFormat === 'csv'
                ? csvContent
                : JSON.stringify(basePayload, null, 2)
            await navigator.clipboard.writeText(clipboardContent)
            return
        }

        if (exportFormat === 'csv') {
            triggerTextDownload('robotineeko-report.csv', 'text/csv;charset=utf-8', csvContent)
            return
        }

        const htmlContent = buildHtmlReport({
            title,
            exportedAt,
            request,
            backtest,
            evaluation,
            stats,
            results,
            exportScope,
        })
        if (exportTarget === 'open') {
            triggerTextOpen('text/html;charset=utf-8', htmlContent)
            return
        }
        triggerTextDownload('robotineeko-report.html', 'text/html;charset=utf-8', htmlContent)
    }

    return (
        <div className='resultsExportPanel'>
            <div className='resultsExportGrid'>
                <label className='resultsExportField'>
                    <span>Content</span>
                    <select value={exportScope} onChange={(event) => setExportScope(event.target.value)}>
                        <option value='full_report'>Full report</option>
                        <option value='statistics_only'>Statistics only</option>
                        <option value='trade_results'>Trade results</option>
                        <option value='request_payload'>Request payload</option>
                    </select>
                </label>

                <label className='resultsExportField'>
                    <span>Destination</span>
                    <select value={exportTarget} onChange={(event) => setExportTarget(event.target.value)}>
                        <option value='clipboard'>Clipboard</option>
                        <option value='save'>Save file</option>
                        <option value='open'>Open</option>
                    </select>
                </label>

                <label className='resultsExportField'>
                    <span>Format</span>
                    <select value={exportFormat} onChange={(event) => setExportFormatDraft(event.target.value)}>
                        {exportTarget === 'clipboard' ? (
                            <>
                                <option value='json'>JSON</option>
                                <option value='csv'>CSV</option>
                            </>
                        ) : exportTarget === 'open' ? (
                            <option value='html'>HTML report</option>
                        ) : (
                            <>
                                <option value='html'>HTML report</option>
                                <option value='csv'>CSV</option>
                            </>
                        )}
                    </select>
                </label>
            </div>

            <div className='resultsExportActions'>
                <button type='button' className='resultsActionButton' onClick={() => void handleExport()}>
                    {exportTarget === 'clipboard'
                        ? 'Export to clipboard'
                        : exportTarget === 'open'
                            ? 'Open report'
                            : 'Save report'}
                </button>
            </div>
        </div>
    )
}

function buildResearchExportRows({
    projectName,
    dashboard,
    regimeSummary,
    regimeStabilitySummary,
    shortlist,
    benchmarkStrategies,
    decisionLog,
    savedStudies,
    studyRuns,
}) {
    const counts = dashboard?.counts || {}
    const bestCandidate = dashboard?.bestCandidate || null

    return [
        {
            section: 'research_dashboard',
            metric: 'project',
            value: projectName || '',
        },
        {
            section: 'research_dashboard',
            metric: 'queued',
            value: counts.queued ?? 0,
        },
        {
            section: 'research_dashboard',
            metric: 'paper',
            value: counts.paper ?? 0,
        },
        {
            section: 'research_dashboard',
            metric: 'review',
            value: counts.review ?? 0,
        },
        {
            section: 'research_dashboard',
            metric: 'promoted',
            value: counts.promoted ?? 0,
        },
        {
            section: 'research_dashboard',
            metric: 'dropped',
            value: counts.dropped ?? 0,
        },
        {
            section: 'research_dashboard',
            metric: 'paper_verdicts',
            value: dashboard?.paperVerdictCount ?? 0,
        },
        {
            section: 'research_dashboard',
            metric: 'high_confidence',
            value: dashboard?.highConfidenceCount ?? 0,
        },
        {
            section: 'research_dashboard',
            metric: 'auto_rejects',
            value: dashboard?.autoRejectCount ?? 0,
        },
        {
            section: 'research_dashboard',
            metric: 'best_promotion_score',
            value: bestCandidate?.promotionScore ?? '',
            label: bestCandidate?.label ?? '',
            side: bestCandidate?.side ?? '',
        },
        {
            section: 'research_dashboard',
            metric: 'research_alert',
            value: dashboard?.alert ?? '',
        },
        {
            section: 'research_dashboard',
            metric: 'platform_verdict',
            value: dashboard?.platformVerdict ?? '',
        },
        {
            section: 'research_dashboard',
            metric: 'platform_verdict_detail',
            value: dashboard?.platformVerdictDetail ?? '',
        },
        ...(regimeSummary || []).map((row) => ({
            section: 'regime_summary',
            regime: row?.regime_label || row?.regime_code || '',
            trades: row?.trades ?? '',
            win_rate: row?.win_rate ?? '',
            net_pnl: row?.net_pnl ?? '',
            avg_trade: row?.avg_trade ?? '',
        })),
        ...(regimeStabilitySummary || []).map((row) => ({
            section: 'regime_stability_summary',
            bucket: row?.bucket || '',
            trades: row?.trades ?? '',
            avg_stability: row?.avg_stability ?? '',
            win_rate: row?.win_rate ?? '',
            net_pnl: row?.net_pnl ?? '',
            avg_trade: row?.avg_trade ?? '',
        })),
        ...(shortlist || []).map((entry) => ({
            ...(function () {
                const decision = buildResearchDecisionEngine(entry)
                return {
                    decision_engine_next_step: decision.nextStep,
                    decision_engine_headline: decision.headline,
                    decision_engine_gate_summary: decision.gateSummary,
                    decision_engine_auto_disposition: decision.autoDisposition,
                    decision_engine_auto_readiness: decision.autoReadiness,
                    decision_engine_confidence: decision.confidence,
                }
            })(),
            section: 'paper_shortlist',
            label: entry?.label || '',
            side: entry?.side || '',
            promotion_score: entry?.promotionScore ?? '',
            disposition: entry?.disposition || '',
            tracker_status: entry?.trackerStatus || '',
            final_decision: entry?.finalDecision || '',
            paper_verdict: entry?.paperVerdict || '',
            promotion_gate_verdict: entry?.promotionGateVerdict || '',
            promotion_gate_tier: entry?.promotionGateTier || '',
            promotion_gate_severe_fail: entry?.promotionGateSevereFail ? 'yes' : 'no',
            promotion_gate_passed_count: entry?.promotionGatePassedCount ?? '',
            promotion_gate_total_checks: entry?.promotionGateTotalChecks ?? '',
            observed_pnl: entry?.observedPnl || '',
            observed_period: entry?.observedPeriod || '',
            paper_start_date: entry?.paperStartDate || '',
            last_review: entry?.reviewedAtLabel || '',
            added_at: entry?.addedAtLabel || '',
            notes: entry?.notes || '',
            paper_notes: entry?.paperNotes || '',
            promotion_memo: entry?.promotionMemo || '',
            live_readiness: entry?.liveReadiness || '',
            live_readiness_notes: entry?.liveReadinessNotes || '',
            failure_mode_category: entry?.failureModeCategory || '',
            failure_mode: entry?.failureMode || '',
        })),
        ...(benchmarkStrategies || []).map((entry) => ({
            section: 'strategy_benchmarks',
            label: entry?.label || '',
            side: entry?.side || '',
            source: entry?.source || '',
            added_at: entry?.addedAtLabel || '',
            benchmark_id: entry?.benchmark_id || '',
        })),
        ...(decisionLog || []).map((entry) => ({
            section: 'decision_log',
            action: entry?.action || '',
            label: entry?.label || '',
            side: entry?.side || '',
            message: entry?.message || '',
            timestamp: entry?.at || '',
            timestamp_label: entry?.atLabel || '',
        })),
        ...Object.entries(savedStudies || {}).flatMap(([studyType, sideMap]) => (
            Object.entries(sideMap || {}).map(([side, savedEntry]) => ({
                section: 'saved_studies',
                study_type: studyType,
                side,
                saved_at: savedEntry?.savedAt || '',
                saved_at_label: savedEntry?.savedAtLabel || '',
                best_id: savedEntry?.payload?.study?.best_preset_id || savedEntry?.payload?.bestPresetId || savedEntry?.payload?.result?.bestCandidateId || '',
                comparison_count: Array.isArray(savedEntry?.payload?.study?.comparisons)
                    ? savedEntry.payload.study.comparisons.length
                    : Array.isArray(savedEntry?.payload?.comparisons)
                        ? savedEntry.payload.comparisons.length
                        : Array.isArray(savedEntry?.payload?.result?.candidates)
                            ? savedEntry.payload.result.candidates.length
                            : '',
            }))
        )),
        ...(studyRuns || []).map((entry) => ({
            section: 'study_runs',
            run_name: entry?.run_name || '',
            version: entry?.version || '',
            type: entry?.type || '',
            side: entry?.side || '',
            timestamp: entry?.at || '',
            timestamp_label: entry?.atLabel || '',
            best_id: entry?.best_id || '',
            best_label: entry?.best_label || '',
            comparison_count: entry?.comparison_count ?? '',
        })),
    ]
}

function classifyResearchShortlist(shortlist = []) {
    const readyForPaper = []
    const readyForPromotionReview = []
    const rejectedCandidates = []

    for (const entry of shortlist || []) {
        const trackerStatus = String(entry?.trackerStatus || 'queued')
        const finalDecision = String(entry?.finalDecision || 'pending')
        const paperVerdict = String(entry?.paperVerdict || 'pending')
        const disposition = String(entry?.disposition || '')

        if (finalDecision === 'drop' || paperVerdict === 'drop' || disposition === 'reject') {
            rejectedCandidates.push(entry)
            continue
        }

        if (
            finalDecision === 'promote'
            || paperVerdict === 'promote'
            || trackerStatus === 'review'
        ) {
            readyForPromotionReview.push(entry)
            continue
        }

        if (
            trackerStatus === 'queued'
            || trackerStatus === 'paper'
            || disposition === 'watch'
            || disposition === 'promote'
        ) {
            readyForPaper.push(entry)
        }
    }

    return {
        readyForPaper,
        readyForPromotionReview,
        rejectedCandidates,
    }
}

function buildResearchHtmlReport({
    exportedAt,
    projectName,
    dashboard,
    regimeSummary,
    regimeStabilitySummary,
    shortlist,
    benchmarkStrategies,
    decisionLog,
    savedStudies,
    studyRuns,
    exportScope,
}) {
    const rows = buildResearchExportRows({
        projectName,
        dashboard,
        regimeSummary,
        regimeStabilitySummary,
        shortlist,
        benchmarkStrategies,
        decisionLog,
        savedStudies,
        studyRuns,
    })
    const shortlistGroups = classifyResearchShortlist(shortlist)
    const dashboardRows = rows.filter((row) => row.section === 'research_dashboard')
    const regimeRows = rows.filter((row) => row.section === 'regime_summary')
    const stabilityRows = rows.filter((row) => row.section === 'regime_stability_summary')
    const shortlistRows = rows.filter((row) => row.section === 'paper_shortlist')
    const benchmarkRows = rows.filter((row) => row.section === 'strategy_benchmarks')
    const decisionRows = rows.filter((row) => row.section === 'decision_log')
    const savedStudyRows = rows.filter((row) => row.section === 'saved_studies')
    const studyRunRows = rows.filter((row) => row.section === 'study_runs')

    const buildTable = (title, sectionRows = []) => {
        if (!sectionRows.length) {
            return ''
        }

        const headers = Array.from(
            sectionRows.reduce((set, row) => {
                Object.keys(row || {}).forEach((key) => set.add(key))
                return set
            }, new Set())
        )

        const headHtml = headers.map((header) => `<th>${escapeHtml(header)}</th>`).join('')
        const bodyHtml = sectionRows.map((row) => (
            `<tr>${headers.map((header) => `<td>${escapeHtml(row?.[header] ?? '')}</td>`).join('')}</tr>`
        )).join('')

        return `<h2>${escapeHtml(title)}</h2><table><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`
    }

    const buildCandidateList = (title, entries = []) => {
        if (!entries.length) {
            return ''
        }

        const itemsHtml = entries.map((entry) => (
            `<li><strong>${escapeHtml(entry?.label || 'Unnamed candidate')}</strong> (${escapeHtml(entry?.side || '-')}) · score ${escapeHtml(formatValue(entry?.promotionScore, 1))} · status ${escapeHtml(entry?.trackerStatus || 'queued')} · verdict ${escapeHtml(entry?.paperVerdict || 'pending')}</li>`
        )).join('')

        return `<h2>${escapeHtml(title)}</h2><ul>${itemsHtml}</ul>`
    }

    const bestCandidate = dashboard?.bestCandidate || null
    const promotedEntries = (shortlist || []).filter((entry) => String(entry?.trackerStatus || '') === 'promoted')
    const droppedEntries = shortlistGroups.rejectedCandidates
    const executiveNotes = [
        bestCandidate
            ? `<li><strong>Best candidate:</strong> ${escapeHtml(bestCandidate.label)} (${escapeHtml(bestCandidate.side || '-')}) · score ${escapeHtml(formatValue(bestCandidate.promotionScore, 1))}</li>`
            : '',
        shortlistGroups.readyForPaper.length
            ? `<li><strong>Ready for paper:</strong> ${escapeHtml(shortlistGroups.readyForPaper.map((entry) => entry.label).join(', '))}</li>`
            : '',
        shortlistGroups.readyForPromotionReview.length
            ? `<li><strong>Ready for promotion review:</strong> ${escapeHtml(shortlistGroups.readyForPromotionReview.map((entry) => entry.label).join(', '))}</li>`
            : '',
        promotedEntries.length
            ? `<li><strong>Promoted:</strong> ${escapeHtml(promotedEntries.map((entry) => `${entry.label}${entry.promotionMemo ? ` — ${entry.promotionMemo}` : ''}`).join(' | '))}</li>`
            : '',
        droppedEntries.length
            ? `<li><strong>Dropped:</strong> ${escapeHtml(droppedEntries.map((entry) => `${entry.label}${entry.promotionMemo ? ` — ${entry.promotionMemo}` : ''}`).join(' | '))}</li>`
            : '',
    ].filter(Boolean).join('')

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml('Robotineeko Research Report')}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; color: #111827; }
    h1, h2 { margin: 0 0 12px; }
    .meta { margin-bottom: 24px; color: #4b5563; }
    table { border-collapse: collapse; width: 100%; margin: 16px 0 24px; }
    th, td { border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; vertical-align: top; }
    th { background: #f3f4f6; }
    pre { background: #f8fafc; border: 1px solid #e5e7eb; padding: 12px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>Robotineeko Research Report</h1>
  <div class="meta">Exported at ${escapeHtml(exportedAt)} | Project: ${escapeHtml(projectName || 'Untitled project')} | Scope: ${escapeHtml(exportScope)}</div>
  ${(exportScope === 'full_report' || exportScope === 'dashboard_only') && executiveNotes ? `<h2>Executive Summary</h2><ul>${executiveNotes}</ul>` : ''}
  ${(exportScope === 'full_report' || exportScope === 'dashboard_only') ? buildTable('Research Dashboard', dashboardRows) : ''}
  ${(exportScope === 'full_report' || exportScope === 'dashboard_only') ? `${buildCandidateList('Ready For Paper', shortlistGroups.readyForPaper)}${buildCandidateList('Ready For Promotion Review', shortlistGroups.readyForPromotionReview)}${buildCandidateList('Rejected Candidates', shortlistGroups.rejectedCandidates)}` : ''}
  ${(exportScope === 'full_report' || exportScope === 'regime_only') ? `${buildTable('Regime Summary', regimeRows)}${buildTable('Regime Stability Summary', stabilityRows)}` : ''}
  ${(exportScope === 'full_report' || exportScope === 'paper_shortlist') ? buildTable('Paper Shortlist', shortlistRows) : ''}
  ${(exportScope === 'full_report' || exportScope === 'paper_shortlist') ? buildTable('Strategy Benchmarks', benchmarkRows) : ''}
  ${(exportScope === 'full_report' || exportScope === 'paper_shortlist') ? buildTable('Decision Log', decisionRows) : ''}
  ${(exportScope === 'full_report' || exportScope === 'study_runs') ? `${buildTable('Saved Studies', savedStudyRows)}${buildTable('Study Runs', studyRunRows)}` : ''}
</body>
</html>`
}

function buildResearchPlaybookCsvRows() {
    return RESEARCH_PLAYBOOK_SECTIONS.flatMap((section) => (
        (section.blocks || []).flatMap((block) => {
            if (block.type === 'terms') {
                return (block.rows || []).map(([term, explanation]) => ({
                    section: section.title,
                    block: block.title || '',
                    item: term,
                    value: explanation,
                }))
            }
            return (block.items || []).map((item, index) => ({
                section: section.title,
                block: block.title || block.type || '',
                item: typeof item === 'string' ? item : `${block.type}-${index + 1}`,
                value: typeof item === 'string' ? item : JSON.stringify(item),
            }))
        })
    ))
}

function buildResearchPlaybookHtml() {
    const sectionsHtml = RESEARCH_PLAYBOOK_SECTIONS.map((section) => {
        const blocksHtml = (section.blocks || []).map((block) => {
            if (block.type === 'paragraphs') {
                return `${block.title ? `<h3>${escapeHtml(block.title)}</h3>` : ''}${(block.items || []).map((item) => `<p>${escapeHtml(item)}</p>`).join('')}`
            }
            if (block.type === 'bullets' || block.type === 'steps') {
                const tag = block.type === 'steps' ? 'ol' : 'ul'
                return `${block.title ? `<h3>${escapeHtml(block.title)}</h3>` : ''}<${tag}>${(block.items || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</${tag}>`
            }
            if (block.type === 'terms') {
                return `${block.title ? `<h3>${escapeHtml(block.title)}</h3>` : ''}<table><thead><tr><th>Field</th><th>Use</th></tr></thead><tbody>${(block.rows || []).map(([term, explanation]) => `<tr><td>${escapeHtml(term)}</td><td>${escapeHtml(explanation)}</td></tr>`).join('')}</tbody></table>`
            }
            if (block.type === 'flowchart') {
                return '<h3>Strategy development flow</h3><ol><li>Idea</li><li>Feature stack</li><li>Strategy rules</li><li>Backtest with costs</li><li>Research studies</li><li>Shortlist</li><li>Paper</li><li>Promotion review</li><li>Live readiness</li></ol>'
            }
            return ''
        }).join('')
        return `<section><h2>${escapeHtml(section.title)}</h2><p>${escapeHtml(section.summary || '')}</p>${blocksHtml}</section>`
    }).join('')

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Robotineeko Research Playbook</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; color: #111827; line-height: 1.6; }
    h1, h2, h3 { margin: 0 0 12px; }
    section { margin: 0 0 24px; padding: 16px; border: 1px solid #d1d5db; background: #f8fafc; }
    table { border-collapse: collapse; width: 100%; margin: 12px 0; }
    th, td { border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; vertical-align: top; }
    th { background: #f3f4f6; }
  </style>
</head>
<body>
  <h1>Robotineeko Research Playbook</h1>
  <p>Practical guidance for using the platform to research, validate, shortlist and promote strategy candidates.</p>
  ${sectionsHtml}
</body>
</html>`
}

function ResearchPlaybookFlowchart() {
    const steps = ['Idea', 'Feature stack', 'Strategy rules', 'Backtest', 'Research studies', 'Shortlist', 'Paper', 'Review', 'Live readiness']
    return (
        <div className='researchPlaybookFlowchart'>
            {steps.map((step, index) => (
                <div key={step} className='researchPlaybookFlowStep'>
                    <div className='researchPlaybookFlowNode'>{step}</div>
                    {index < steps.length - 1 ? <div className='researchPlaybookFlowArrow' aria-hidden='true'>→</div> : null}
                </div>
            ))}
        </div>
    )
}

function ResearchPlaybookPane({ onLogEvent }) {
    const [selectedSectionId, setSelectedSectionId] = useState(RESEARCH_PLAYBOOK_SECTIONS[0]?.id || '')
    const [exportTarget, setExportTarget] = useState('clipboard')
    const [exportFormatDraft, setExportFormatDraft] = useState('json')
    const exportFormat = normalizeExportFormatForTarget(exportTarget, exportFormatDraft)

    const selectedSection = RESEARCH_PLAYBOOK_SECTIONS.find((entry) => entry.id === selectedSectionId) || RESEARCH_PLAYBOOK_SECTIONS[0]

    async function handleExport() {
        const payload = {
            exported_at: new Date().toISOString(),
            type: 'research_playbook',
            sections: RESEARCH_PLAYBOOK_SECTIONS,
        }
        const csvContent = buildCsvString(buildResearchPlaybookCsvRows())
        if (exportTarget === 'clipboard') {
            const content = exportFormat === 'csv' ? csvContent : JSON.stringify(payload, null, 2)
            await navigator.clipboard.writeText(content)
            onLogEvent?.(`Research · Exported playbook to clipboard as ${exportFormat.toUpperCase()}.`)
            return
        }
        const htmlContent = buildResearchPlaybookHtml()
        if (exportTarget === 'open') {
            triggerTextOpen('text/html;charset=utf-8', htmlContent)
            onLogEvent?.('Research · Opened playbook as HTML report.')
            return
        }
        if (exportFormat === 'csv') {
            triggerTextDownload('robotineeko-research-playbook.csv', 'text/csv;charset=utf-8', csvContent)
            onLogEvent?.('Research · Saved playbook as CSV.')
            return
        }
        triggerTextDownload('robotineeko-research-playbook.html', 'text/html;charset=utf-8', htmlContent)
        onLogEvent?.('Research · Saved playbook as HTML report.')
    }

    return (
        <div className='researchPlaybookLayout'>
            <aside className='researchPlaybookSidebar'>
                <div className='researchPlaybookSidebarTitle'>Research playbook</div>
                <div className='researchPlaybookSidebarMeta'>How to use the platform for serious strategy research.</div>
                <div className='researchPlaybookSidebarList'>
                    {RESEARCH_PLAYBOOK_SECTIONS.map((section) => (
                        <button
                            key={section.id}
                            type='button'
                            className={`researchPlaybookSidebarButton ${selectedSection?.id === section.id ? 'active' : ''}`}
                            onClick={() => setSelectedSectionId(section.id)}
                        >
                            {section.title}
                        </button>
                    ))}
                </div>
            </aside>

            <div className='researchPlaybookContent'>
                <div className='presetCompareHeader'>
                    <div className='presetCompareText'>
                        <div className='presetStudyTitle'>{selectedSection?.title}</div>
                        <div className='presetStudyMeta'>{selectedSection?.summary}</div>
                    </div>
                    <div className='resultsExportFields'>
                        <label className='resultsExportField'>
                            <span>Destination</span>
                            <select value={exportTarget} onChange={(event) => setExportTarget(event.target.value)}>
                                <option value='clipboard'>Clipboard</option>
                                <option value='save'>Save file</option>
                                <option value='open'>Open</option>
                            </select>
                        </label>
                        <label className='resultsExportField'>
                            <span>Format</span>
                            <select value={exportFormat} onChange={(event) => setExportFormatDraft(event.target.value)}>
                                {exportTarget === 'clipboard' ? (
                                    <>
                                        <option value='json'>JSON</option>
                                        <option value='csv'>CSV</option>
                                    </>
                                ) : exportTarget === 'open' ? (
                                    <option value='html'>HTML report</option>
                                ) : (
                                    <>
                                        <option value='html'>HTML report</option>
                                        <option value='csv'>CSV</option>
                                    </>
                                )}
                            </select>
                        </label>
                        <button type='button' className='resultsActionButton' onClick={() => void handleExport()}>
                            {exportTarget === 'clipboard' ? 'Export playbook' : exportTarget === 'open' ? 'Open playbook' : 'Save playbook'}
                        </button>
                    </div>
                </div>

                <div className='researchPlaybookSections'>
                    {(selectedSection?.blocks || []).map((block, index) => (
                        <div key={`${selectedSection?.id}-block-${index}`} className='researchPlaybookCard'>
                            {block.title ? <div className='researchPlaybookCardTitle'>{block.title}</div> : null}
                            {block.type === 'paragraphs' ? (
                                <div className='researchPlaybookParagraphs'>
                                    {(block.items || []).map((item) => (
                                        <p key={item}>{item}</p>
                                    ))}
                                </div>
                            ) : block.type === 'bullets' ? (
                                <ul className='researchPlaybookList'>
                                    {(block.items || []).map((item) => <li key={item}>{item}</li>)}
                                </ul>
                            ) : block.type === 'steps' ? (
                                <ol className='researchPlaybookList ordered'>
                                    {(block.items || []).map((item) => <li key={item}>{item}</li>)}
                                </ol>
                            ) : block.type === 'terms' ? (
                                <div className='researchPlaybookTerms'>
                                    {(block.rows || []).map(([term, explanation]) => (
                                        <div key={term} className='researchPlaybookTermRow'>
                                            <div className='researchPlaybookTerm'>{term}</div>
                                            <div className='researchPlaybookTermExplanation'>{explanation}</div>
                                        </div>
                                    ))}
                                </div>
                            ) : block.type === 'flowchart' ? (
                                <ResearchPlaybookFlowchart />
                            ) : null}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )
}

function ResearchExportPane({
    projectName,
    dashboard,
    regimeSummary,
    regimeStabilitySummary,
    shortlist,
    benchmarkStrategies,
    decisionLog,
    savedStudies,
    studyRuns,
    onLogEvent,
}) {
    const [exportScope, setExportScope] = useState('full_report')
    const [exportTarget, setExportTarget] = useState('clipboard')
    const [exportFormatDraft, setExportFormatDraft] = useState('json')
    const exportFormat = normalizeExportFormatForTarget(exportTarget, exportFormatDraft)

    async function handleExport() {
        const exportedAt = new Date().toISOString()
        const shortlistGroups = classifyResearchShortlist(shortlist)
        const payload = {
            exported_at: exportedAt,
            project_name: projectName || '',
            scope: exportScope,
            research_dashboard: dashboard || null,
            regime_summary: regimeSummary || [],
            regime_stability_summary: regimeStabilitySummary || [],
            paper_shortlist: shortlist || [],
            strategy_benchmarks: benchmarkStrategies || [],
            decision_log: decisionLog || [],
            saved_studies: savedStudies || {},
            study_runs: studyRuns || [],
            candidate_groups: shortlistGroups,
        }
        const csvRows = buildResearchExportRows({
            projectName,
            dashboard,
            regimeSummary,
            regimeStabilitySummary,
            shortlist,
            benchmarkStrategies,
            decisionLog,
            savedStudies,
            studyRuns,
        }).filter((row) => (
            exportScope === 'full_report'
            || (exportScope === 'dashboard_only' && row.section === 'research_dashboard')
            || (exportScope === 'regime_only' && (row.section === 'regime_summary' || row.section === 'regime_stability_summary'))
            || (exportScope === 'paper_shortlist' && (row.section === 'paper_shortlist' || row.section === 'strategy_benchmarks' || row.section === 'decision_log'))
            || (exportScope === 'study_runs' && (row.section === 'saved_studies' || row.section === 'study_runs'))
        ))
        const csvContent = buildCsvString(csvRows)

        if (exportTarget === 'clipboard') {
            const clipboardContent = exportFormat === 'csv'
                ? csvContent
                : JSON.stringify(payload, null, 2)
            await navigator.clipboard.writeText(clipboardContent)
            onLogEvent?.(`Research · Exported ${exportScope.replaceAll('_', ' ')} to clipboard as ${exportFormat.toUpperCase()}.`)
            return
        }

        if (exportFormat === 'csv') {
            triggerTextDownload('robotineeko-research.csv', 'text/csv;charset=utf-8', csvContent)
            onLogEvent?.(`Research · Saved ${exportScope.replaceAll('_', ' ')} as CSV.`)
            return
        }

        const htmlContent = buildResearchHtmlReport({
            exportedAt,
            projectName,
            dashboard,
            regimeSummary,
            regimeStabilitySummary,
            shortlist,
            benchmarkStrategies,
            decisionLog,
            savedStudies,
            studyRuns,
            exportScope,
        })
        if (exportTarget === 'open') {
            triggerTextOpen('text/html;charset=utf-8', htmlContent)
            onLogEvent?.(`Research · Opened ${exportScope.replaceAll('_', ' ')} as HTML report.`)
            return
        }
        triggerTextDownload('robotineeko-research.html', 'text/html;charset=utf-8', htmlContent)
        onLogEvent?.(`Research · Saved ${exportScope.replaceAll('_', ' ')} as HTML report.`)
    }

    return (
        <div className='resultsExportPanel'>
            <div className='resultsExportGrid'>
                <label className='resultsExportField'>
                    <span>Content</span>
                    <select value={exportScope} onChange={(event) => setExportScope(event.target.value)}>
                        <option value='full_report'>Full research report</option>
                        <option value='dashboard_only'>Dashboard only</option>
                        <option value='regime_only'>Regime studies</option>
                        <option value='paper_shortlist'>Paper shortlist</option>
                        <option value='study_runs'>Study runs</option>
                    </select>
                </label>

                <label className='resultsExportField'>
                    <span>Destination</span>
                    <select value={exportTarget} onChange={(event) => setExportTarget(event.target.value)}>
                        <option value='clipboard'>Clipboard</option>
                        <option value='save'>Save file</option>
                        <option value='open'>Open</option>
                    </select>
                </label>

                <label className='resultsExportField'>
                    <span>Format</span>
                    <select value={exportFormat} onChange={(event) => setExportFormatDraft(event.target.value)}>
                        {exportTarget === 'clipboard' ? (
                            <>
                                <option value='json'>JSON</option>
                                <option value='csv'>CSV</option>
                            </>
                        ) : exportTarget === 'open' ? (
                            <option value='html'>HTML report</option>
                        ) : (
                            <>
                                <option value='html'>HTML report</option>
                                <option value='csv'>CSV</option>
                            </>
                        )}
                    </select>
                </label>
            </div>

            <div className='resultsExportActions'>
                <button type='button' className='resultsActionButton' onClick={() => void handleExport()}>
                    {exportTarget === 'clipboard'
                        ? 'Export to clipboard'
                        : exportTarget === 'open'
                            ? 'Open report'
                            : 'Save report'}
                </button>
            </div>
        </div>
    )
}

function StatisticsPane({
    request,
    backtest,
    stats,
    results,
    strategyApplyResponse,
    authToken = '',
    onResolveLoadedBacktestResponse = null,
    onLogEvent,
}) {
    const [selectedGroupId, setSelectedGroupId] = useState('summary')
    const [selectedView, setSelectedView] = useState('data')

    if (!stats) {
        return <div className='statisticsEmpty'>Run a backtest to see the statistics.</div>
    }

    const evaluation = buildStatisticsEvaluation(stats)
    const evaluationCriteriaByLabel = Object.fromEntries(
        (evaluation?.criteria || []).map((criterion) => [criterion.label, criterion]),
    )
    const tradeCadence = buildTradeCadenceMetrics(strategyApplyResponse, stats)

    const groups = [
        {
            id: 'score',
            title: 'Score',
            description: 'This score summarizes the strategy using the most relevant quality metrics, weighted toward robustness rather than raw profit only.',
            detail: 'Use it as a fast ranking aid, then confirm the verdict in the execution, risk and PnL tabs.',
            rows: [],
            docRows: [
                {
                    field: 'Strategy score',
                    explanation: 'Weighted summary score used to rank the strategy quickly without depending only on raw profit.',
                    recommended: 'Higher is better. Treat 7.5+ as strong, 5.0+ as workable, below that as fragile.',
                    formula: 'Weighted mean of normalized criteria, then scaled to 0..10.',
                },
                {
                    field: 'Net profit factor',
                    explanation: 'How much net profit is produced for each unit of net loss after costs.',
                    recommended: '>= 1.75',
                    formula: 'net_profit / abs(net_loss)',
                },
                {
                    field: 'Max drawdown %',
                    explanation: 'Largest peak-to-trough percentage drop in the account during the run.',
                    recommended: '<= 10%',
                    formula: 'max_drawdown / peak_balance',
                },
                {
                    field: 'Sharpe ratio',
                    explanation: 'Return quality relative to total volatility of returns.',
                    recommended: '>= 1.50',
                    formula: '(mean_return - risk_free_rate) / std_dev(returns)',
                },
                {
                    field: 'Sortino ratio',
                    explanation: 'Return quality relative only to downside volatility.',
                    recommended: '>= 2.00',
                    formula: '(mean_return - risk_free_rate) / downside_deviation',
                },
                {
                    field: 'Win rate',
                    explanation: 'Share of trades that finished positive after costs.',
                    recommended: '>= 55%',
                    formula: 'winning_trades / total_trades',
                },
                {
                    field: 'Risk / reward',
                    explanation: 'Average win size relative to average loss size.',
                    recommended: '>= 1.50',
                    formula: 'avg_net_profit / abs(avg_net_loss)',
                },
                {
                    field: 'Recovery factor',
                    explanation: 'Ability of the strategy to recover losses relative to its worst drawdown.',
                    recommended: '>= 2.00',
                    formula: 'net_profit / max_drawdown',
                },
                {
                    field: 'Kelly fraction',
                    explanation: 'Theoretical capital fraction implied by edge and payoff.',
                    recommended: '>= 20%, but use with caution as a sizing hint only.',
                    formula: 'win_rate - ((1 - win_rate) / reward_to_risk)',
                },
            ],
        },
        {
            id: 'execution',
            title: 'Execution',
            description: 'Execution assumptions define how fills, spread, slippage and history scope were interpreted during the simulation.',
            detail: 'Review this first whenever a result looks too optimistic or too pessimistic.',
            rows: [
                { label: 'Requested cost mode', value: formatTextLabel(stats.execution_policy?.requested_cost_profile_label || stats.execution_policy?.requested_cost_profile) },
                { label: 'Effective cost model', value: formatTextLabel(stats.execution_policy?.cost_profile_label || stats.execution_policy?.cost_profile) },
                { label: 'Broker scope', value: formatTextLabel(stats.execution_policy?.broker_profile_label || stats.execution_policy?.broker_label || stats.execution_policy?.broker_code) },
                { label: 'Asset type', value: formatTextLabel(stats.execution_policy?.asset_type_label || stats.execution_policy?.asset_type) },
                { label: 'Capital model', value: formatMarginModelLabel(stats.execution_policy?.margin_model) },
                { label: 'Capital source', value: formatTextLabel(stats.execution_policy?.capital_model_source) },
                { label: 'Account currency', value: formatTextLabel(stats.execution_policy?.capital_account_currency) },
                { label: 'Contract size / lot', value: formatValue(stats.execution_policy?.contract_size_per_lot) },
                { label: 'Minimum lot', value: formatValue(stats.execution_policy?.min_lot) },
                { label: 'Lot step', value: formatValue(stats.execution_policy?.lot_step) },
                { label: 'Maximum lot', value: formatValue(stats.execution_policy?.max_lot) },
                { label: 'Account leverage', value: formatValue(stats.execution_policy?.account_leverage) },
                { label: 'Margin long rate', value: formatPercent(stats.execution_policy?.margin_long_rate, 3) },
                { label: 'Margin short rate', value: formatPercent(stats.execution_policy?.margin_short_rate, 3) },
                { label: 'Margin per lot', value: formatValue(stats.execution_policy?.margin_per_lot) },
                { label: 'Quote conversion', value: formatTextLabel(stats.execution_policy?.quote_to_account_conversion_mode) },
                { label: 'Operational cost items', value: buildCostBreakdownInline(stats.operational_cost_breakdown_totals, { withAmounts: true, maxItems: 3 }) },
                { label: 'Estimated tax items', value: buildCostBreakdownInline(stats.estimated_tax_breakdown_totals, { withAmounts: true, maxItems: 3 }) },
                { label: 'Execution mode', value: formatTextLabel(stats.execution_policy?.execution_mode) },
                { label: 'Spread (pips)', value: formatValue(stats.execution_policy?.spread_in_pips) },
                { label: 'Entry slippage (pips)', value: formatValue(stats.execution_policy?.entry_slippage_in_pips) },
                { label: 'Close slippage (pips)', value: formatValue(stats.execution_policy?.close_slippage_in_pips) },
                { label: 'Take profit slippage (pips)', value: formatValue(stats.execution_policy?.take_profit_slippage_in_pips) },
                { label: 'Stop loss slippage (pips)', value: formatValue(stats.execution_policy?.stop_loss_slippage_in_pips) },
                { label: 'Trailing stop slippage (pips)', value: formatValue(stats.execution_policy?.trailing_stop_slippage_in_pips) },
                { label: 'Volatility slippage multiplier', value: formatValue(stats.execution_policy?.volatility_slippage_multiplier) },
                { label: 'Volatility reference', value: formatTextLabel(stats.execution_policy?.volatility_slippage_reference) },
                { label: 'Take profit fill', value: formatTextLabel(stats.execution_policy?.take_profit_fill) },
                { label: 'Stop loss fill', value: formatTextLabel(stats.execution_policy?.stop_loss_fill) },
                { label: 'Trailing stop fill', value: formatTextLabel(stats.execution_policy?.trailing_stop_fill) },
                { label: 'Trailing on entry candle', value: stats.execution_policy?.same_bar_trailing_exit ? 'yes' : 'no' },
                { label: 'Intrabar conflict', value: formatTextLabel(stats.execution_policy?.intrabar_conflict_policy) },
            ],
            docRows: [
                { field: 'Requested cost mode', explanation: 'The operator-facing profile requested in the UI before broker resolution.', recommended: 'Keep Active broker as the default unless you are stress-testing a different shell.', formula: 'UI costProfile selection before broker mapping.' },
                { field: 'Effective cost model', explanation: 'The actual cost model applied after resolving the active broker and market domain.', recommended: 'Confirm this matches the broker selected in the page header.', formula: 'resolved cost_profile after broker mapping.' },
                { field: 'Broker scope', explanation: 'Broker profile or broker code used to resolve broker-aware cost schemes.', recommended: 'Should match the current page-header broker when using Active broker.', formula: 'active broker metadata carried in the backtest request.' },
                { field: 'Asset type', explanation: 'Pricing regime used for PnL and explicit cost calculations.', recommended: 'Keep it aligned with the instrument class you are testing.', formula: 'forex pip model or B3 notional/contract model.' },
                { field: 'Capital model', explanation: 'Margin shell used to turn the selected symbol price and lot size into reserved capital.', recommended: 'Use the asset-default model unless you are calibrating a different broker contract.', formula: 'execution_policy.margin_model from the normalized capital model.' },
                { field: 'Capital source', explanation: 'Whether the capital shell came entirely from asset defaults or from manual overrides in the Backtester.', recommended: 'Asset default is the safest common path; custom should be intentional.', formula: 'capital_model_source.' },
                { field: 'Account currency', explanation: 'Reference account currency carried by the capital model.', recommended: 'Keep it aligned with the account you want the run to represent.', formula: 'capital_account_currency.' },
                { field: 'Contract size / lot', explanation: 'How much notional one lot or contract represents in the margin model.', recommended: 'Confirm this whenever you test a non-standard instrument shell.', formula: 'contract_size_per_lot.' },
                { field: 'Minimum lot', explanation: 'Smallest legal lot or contract count the backtest will try to execute under the active capital model.', recommended: 'Match the broker or exchange rule.', formula: 'min_lot.' },
                { field: 'Lot step', explanation: 'Increment used to quantize requested volume before opening trades.', recommended: 'Match the broker or exchange step size.', formula: 'lot_step.' },
                { field: 'Maximum lot', explanation: 'Hard ceiling applied before a volume request can be executed.', recommended: 'Keep this aligned with the broker or your own sizing cap.', formula: 'max_lot.' },
                { field: 'Account leverage', explanation: 'Leverage fallback used to derive notional margin rates when long and short margin rates are not overridden explicitly.', recommended: 'Confirm this before trusting max-volume scenarios.', formula: 'account_leverage, with margin rate fallback 1 / leverage.' },
                { field: 'Margin long rate', explanation: 'Fraction of long notional reserved as margin in notional models.', recommended: 'Review this together with leverage; lower rates allow larger positions.', formula: 'margin_long_rate.' },
                { field: 'Margin short rate', explanation: 'Fraction of short notional reserved as margin in notional models.', recommended: 'Keep it realistic for the broker and asset type.', formula: 'margin_short_rate.' },
                { field: 'Margin per lot', explanation: 'Fixed amount of reserved capital per lot or contract when the model uses fixed-per-lot semantics.', recommended: 'Mainly relevant for future-style shells such as B3 minis.', formula: 'margin_per_lot.' },
                { field: 'Quote conversion', explanation: 'Account-conversion assumption carried by the capital model for notional calculations.', recommended: 'Treat this as an explicit assumption whenever quote currency differs from account currency.', formula: 'quote_to_account_conversion_mode.' },
                { field: 'Operational cost items', explanation: 'Execution fees and other non-tax charges added into trade_cost.', recommended: 'Review these against gross PnL to see whether the raw edge survives real fills and exchange fees.', formula: 'sum of non-tax trade_cost_breakdown items across completed trades.' },
                { field: 'Estimated tax items', explanation: 'Estimated tax lines added into trade_cost and total_cost for supported B3 shells.', recommended: 'Treat these as economic drag, not as broker fees.', formula: 'sum of estimated_tax trade_cost_breakdown items across completed trades.' },
                { field: 'Execution mode', explanation: 'When orders are evaluated and actually filled in the simulation.', recommended: 'Use next bar open as the main realistic mode.', formula: 'Signal at t, fill at t+1 open in next_bar_open.' },
                { field: 'Spread (pips)', explanation: 'Base bid/ask cost added to each trade independently of slippage.', recommended: 'Use a realistic broker average for the pair/timeframe.', formula: 'Fixed pips cost per trade side model.' },
                { field: 'Entry slippage (pips)', explanation: 'Extra execution worsening applied on entries.', recommended: 'Keep small but non-zero for market-style entry.', formula: 'entry_fill_price +/- effective_slippage' },
                { field: 'Close slippage (pips)', explanation: 'Extra execution worsening applied on manual closes.', recommended: 'Usually small and similar to entry.', formula: 'close_fill_price +/- effective_slippage' },
                { field: 'Take profit slippage (pips)', explanation: 'Extra worsening applied when target exits are filled.', recommended: 'Often 0.0 to 0.1 for optimistic-but-reasonable fills.', formula: 'tp_fill_price adjusted by configured slippage' },
                { field: 'Stop loss slippage (pips)', explanation: 'Extra worsening applied when stop exits are hit.', recommended: 'Higher than take-profit; stops usually slip more.', formula: 'stop_fill_price adjusted by configured slippage' },
                { field: 'Trailing stop slippage (pips)', explanation: 'Extra worsening applied on trailing exits.', recommended: 'Usually equal to or worse than stop loss.', formula: 'trailing_fill_price adjusted by configured slippage' },
                { field: 'Volatility slippage multiplier', explanation: 'Makes slippage grow when recent bars were more volatile.', recommended: '0.0 for simple model, low positive value for stress testing.', formula: 'base_slippage + previous_bar_range_pips * multiplier' },
                { field: 'Volatility reference', explanation: 'Source used to scale volatility-aware slippage.', recommended: 'Keep aligned with the engine default unless you are calibrating costs.', formula: 'Currently based on previous bar range.' },
                { field: 'Take profit fill', explanation: 'Policy used to determine the filled price when a target is touched.', recommended: 'Use target price for a fair candle-based assumption.', formula: 'Filled at target when touched.' },
                { field: 'Stop loss fill', explanation: 'Policy used to determine the filled price when a stop is touched.', recommended: 'Use bar extreme for a more pessimistic candle-based model.', formula: 'Long -> low, Short -> high when stop is hit.' },
                { field: 'Trailing stop fill', explanation: 'Policy used to determine the filled price when a trailing stop is touched.', recommended: 'Use the same pessimistic logic as stop loss.', formula: 'Filled at bar extreme under trailing stop hit.' },
                { field: 'Trailing on entry candle', explanation: 'Whether the trailing stop can exit on the same candle as the position opens.', recommended: 'Keep disabled for clearer and more stable semantics.', formula: 'Boolean execution rule.' },
                { field: 'Intrabar conflict', explanation: 'Rule used when a candle touches both positive and negative exit conditions.', recommended: 'Prefer pessimistic ordering for safer backtests.', formula: 'Conflict resolver between stop/trailing and gain.' },
            ],
        },
        {
            id: 'summary',
            title: 'Summary',
            description: 'This table condenses the whole run into account-level outcome, including the main balance and PnL changes.',
            detail: 'It is the fastest place to decide whether the strategy improved the account at all.',
            rows: [
                { label: 'Initial balance', value: formatMoney(stats.initial_balance) },
                { label: 'Final balance', value: formatMoney(stats.final_balance) },
                { label: 'Account change', value: formatSignedMoney(pickFirstDefined(stats, ['account_balance_change', 'balance_change'])), tone: getTone(pickFirstDefined(stats, ['account_balance_change', 'balance_change'])) },
                { label: 'Gross PnL', value: formatSignedMoney(pickFirstDefined(stats, ['gross_pnl', 'gross_result'])), tone: getTone(pickFirstDefined(stats, ['gross_pnl', 'gross_result'])) },
                { label: 'Net PnL', value: formatSignedMoney(pickFirstDefined(stats, ['net_pnl', 'net_result'])), tone: getTone(pickFirstDefined(stats, ['net_pnl', 'net_result'])) },
                { label: 'Operational costs', value: formatSignedMoney(stats.total_operational_cost), tone: getTone(-Math.abs(Number(stats.total_operational_cost || 0))) },
                { label: 'Estimated taxes', value: formatSignedMoney(stats.total_estimated_tax), tone: getTone(-Math.abs(Number(stats.total_estimated_tax || 0))) },
                { label: 'Total cost drag', value: formatSignedMoney(stats.total_cost), tone: getTone(-Math.abs(Number(stats.total_cost || 0))) },
                { label: 'Win rate', value: formatPercent(stats.win_rate) },
            ],
            docRows: [
                { field: 'Initial balance', explanation: 'Account balance at the start of the simulation.', recommended: 'Match the capital you want the run to represent.', formula: 'Input parameter.' },
                { field: 'Final balance', explanation: 'Account balance after all simulated trades and costs.', recommended: 'Should exceed initial balance for a viable strategy.', formula: 'initial_balance + net_pnl' },
                { field: 'Account change', explanation: 'Absolute change in account balance during the run.', recommended: 'Positive and stable over repeated tests.', formula: 'final_balance - initial_balance' },
                { field: 'Gross PnL', explanation: 'Result before deducting modeled trading costs.', recommended: 'Useful as a raw edge reference only.', formula: 'gross_profit - gross_loss' },
                { field: 'Net PnL', explanation: 'Result after modeled spread and slippage costs.', recommended: 'Main profitability metric for candle-based evaluation.', formula: 'net_profit - net_loss' },
                { field: 'Operational costs', explanation: 'Total non-tax execution drag accumulated in trade_cost.', recommended: 'Keep this comfortably smaller than gross PnL and inspect its breakdown in Execution.', formula: 'sum(non-tax trade_cost items)' },
                { field: 'Estimated taxes', explanation: 'Total estimated tax drag accumulated in trade_cost for supported B3 shells.', recommended: 'Interpret this as modeled taxation, not broker charging.', formula: 'sum(estimated_tax trade_cost items)' },
                { field: 'Total cost drag', explanation: 'Combined operational costs plus estimated taxes.', recommended: 'Use this to reconcile gross PnL to net PnL.', formula: 'operational_costs + estimated_taxes' },
                { field: 'Win rate', explanation: 'Share of positive trades after costs.', recommended: 'Interpret together with payoff size, not alone.', formula: 'winning_trades / total_trades' },
            ],
        },
        {
            id: 'trades',
            title: 'Trades',
            description: 'Trade counts show how often the strategy acted and how wins and losses were distributed after costs.',
            detail: 'Use it to catch undertrading, overtrading or suspiciously sparse results.',
            rows: [
                { label: 'Total trades', value: formatInteger(stats.n_trades) },
                { label: 'First candle', value: formatDateTime(tradeCadence.marketWindow.firstCandleTime) },
                { label: 'Last candle', value: formatDateTime(tradeCadence.marketWindow.lastCandleTime) },
                { label: 'Avg trades / day', value: formatCadence(tradeCadence.tradesPerDay) },
                { label: 'Avg trades / week', value: formatCadence(tradeCadence.tradesPerWeek) },
                { label: 'Avg trades / month', value: formatCadence(tradeCadence.tradesPerMonth) },
                { label: 'Gross wins', value: formatInteger(stats.n_gross_profits) },
                { label: 'Gross losses', value: formatInteger(stats.n_gross_losses) },
                { label: 'Net wins', value: formatInteger(stats.n_net_profits) },
                { label: 'Net losses', value: formatInteger(stats.n_net_losses) },
                { label: 'Loss rate', value: formatPercent(stats.loss_rate) },
            ],
            docRows: [
                { field: 'Total trades', explanation: 'Number of completed trades in the backtest.', recommended: 'Enough trades to avoid judging the system on noise only.', formula: 'count(completed_trades)' },
                { field: 'First candle', explanation: 'Open time of the first candle included in the backtest market window.', recommended: 'Use with last candle to confirm which historical slice was actually tested.', formula: 'min(result.time) or persisted market_window.first_candle_time' },
                { field: 'Last candle', explanation: 'Open time of the last candle included in the backtest market window.', recommended: 'Check this to verify whether the test really reached the expected recent edge.', formula: 'max(result.time) or persisted market_window.last_candle_time' },
                { field: 'Avg trades / day', explanation: 'Average number of completed trades normalized by the full backtest candle window.', recommended: 'Use to detect whether the strategy is too sparse for the tested horizon.', formula: 'total_trades / window_duration_days' },
                { field: 'Avg trades / week', explanation: 'Average number of completed trades normalized to a 7-day window.', recommended: 'Useful when the raw day-level value is too small to read comfortably.', formula: 'avg_trades_per_day * 7' },
                { field: 'Avg trades / month', explanation: 'Average number of completed trades normalized to an average calendar month.', recommended: 'Good quick cadence reference when comparing longer backtests.', formula: 'avg_trades_per_day * (365.25 / 12)' },
                { field: 'Gross wins', explanation: 'Trades positive before modeled costs.', recommended: 'Compare against net wins to see cost pressure.', formula: 'count(gross_trade_pnl > 0)' },
                { field: 'Gross losses', explanation: 'Trades negative before modeled costs.', recommended: 'Useful to inspect the raw signal profile.', formula: 'count(gross_trade_pnl < 0)' },
                { field: 'Net wins', explanation: 'Trades positive after modeled costs.', recommended: 'Should not collapse too far below gross wins.', formula: 'count(net_trade_pnl > 0)' },
                { field: 'Net losses', explanation: 'Trades negative after modeled costs.', recommended: 'Use with net wins to judge cost sensitivity.', formula: 'count(net_trade_pnl < 0)' },
                { field: 'Loss rate', explanation: 'Share of trades that finished negative after costs.', recommended: 'Lower is better, but only together with average payoff.', formula: 'net_losses / total_trades' },
            ],
        },
        {
            id: 'operations',
            title: 'Operations',
            description: 'The operations table shows one completed trade per row with timestamps, prices and realized PnL.',
            detail: 'It loads on demand so routine Results payloads stay light even when the backtest itself was large.',
            rows: [],
            docRows: [
                { field: 'Load operations', explanation: 'Fetches detailed completed-trade rows only when the operator explicitly asks for them.', recommended: 'Use this when you need row-by-row inspection instead of the summary aggregates.', formula: 'Resolved from the loaded backtest snapshot or the latest completed persisted backtest job.' },
                { field: 'Opened / Closed', explanation: 'Entry and exit timestamps for each completed trade.', recommended: 'Check these when validating the exact trade sequence behind the summary metrics.', formula: 'long_open_timestamp / short_open_timestamp and close timestamps from result rows.' },
                { field: 'Entry / Exit', explanation: 'Recorded execution prices for the trade entry and exit.', recommended: 'Use them together with side and timestamps to inspect fill quality.', formula: 'long_open_price / short_open_price and matching close prices.' },
                { field: 'Gross / Net / Cost', explanation: 'Per-trade gross result, cost drag and realized net result.', recommended: 'Net is the main row-level outcome. Compare gross vs net to see cost pressure.', formula: 'trade_gross_pnl, trade_cost, trade_net_pnl' },
            ],
        },
        {
            id: 'pnl',
            title: 'PnL',
            description: 'PnL metrics separate gross and net behavior so you can see how much the execution model is eating from raw edge.',
            detail: 'Profit factors and recovery factor are especially useful for comparing candidates quickly.',
            rows: [
                { label: 'Gross profit', value: formatMoney(stats.gross_profit), tone: 'positive' },
                { label: 'Gross loss', value: formatMoney(stats.gross_loss), tone: 'negative' },
                { label: 'Net profit', value: formatMoney(stats.net_profit), tone: 'positive' },
                { label: 'Net loss', value: formatMoney(stats.net_loss), tone: 'negative' },
                { label: 'Gross profit factor', value: formatValue(stats.gross_profit_factor) },
                { label: 'Net profit factor', value: formatValue(stats.net_profit_factor) },
                { label: 'Recovery factor', value: formatValue(stats.recovery_factor) },
            ],
            docRows: [
                { field: 'Gross profit', explanation: 'Sum of all positive trade results before modeled costs.', recommended: 'Use mainly to compare against net profit.', formula: 'sum(max(gross_trade_pnl, 0))' },
                { field: 'Gross loss', explanation: 'Sum of all negative trade results before modeled costs.', recommended: 'Inspect magnitude, not only count.', formula: 'sum(abs(min(gross_trade_pnl, 0)))' },
                { field: 'Net profit', explanation: 'Sum of all positive trade results after modeled costs.', recommended: 'Should remain healthy after execution assumptions.', formula: 'sum(max(net_trade_pnl, 0))' },
                { field: 'Net loss', explanation: 'Sum of all negative trade results after modeled costs.', recommended: 'Lower is better; compare with net profit.', formula: 'sum(abs(min(net_trade_pnl, 0)))' },
                { field: 'Gross profit factor', explanation: 'Raw profitability before costs.', recommended: 'Above 1.0, but do not trust it without net PF.', formula: 'gross_profit / gross_loss' },
                { field: 'Net profit factor', explanation: 'Profitability after costs.', recommended: '>= 1.75 for a stronger candidate.', formula: 'net_profit / net_loss' },
                { field: 'Recovery factor', explanation: 'Ability to earn back losses relative to worst drawdown.', recommended: '>= 2.0 is a healthy target.', formula: 'net_pnl / max_drawdown' },
            ],
        },
        {
            id: 'averages',
            title: 'Averages',
            description: 'Average trade values reveal the shape of the strategy, including expectancy, asymmetry and average cost burden.',
            detail: 'This is where weak reward-to-risk structures often become obvious.',
            rows: [
                { label: 'Avg gross profit', value: formatMoney(stats.avg_gross_profit), tone: 'positive' },
                { label: 'Avg gross loss', value: formatMoney(stats.avg_gross_loss), tone: 'negative' },
                { label: 'Avg net profit', value: formatMoney(stats.avg_net_profit), tone: 'positive' },
                { label: 'Avg net loss', value: formatMoney(stats.avg_net_loss), tone: 'negative' },
                { label: 'Risk reward ratio', value: formatValue(stats.risk_reward_ratio) },
                { label: 'Expectancy / trade', value: formatMoney(stats.expectancy_per_trade) },
                { label: 'Cost factor', value: formatValue(stats.cost_factor) },
            ],
            docRows: [
                { field: 'Avg gross profit', explanation: 'Average size of raw winning trades before costs.', recommended: 'Should be meaningfully larger than avg gross loss for trend systems.', formula: 'gross_profit / gross_winning_trades' },
                { field: 'Avg gross loss', explanation: 'Average size of raw losing trades before costs.', recommended: 'Keep controlled relative to avg gross profit.', formula: 'gross_loss / gross_losing_trades' },
                { field: 'Avg net profit', explanation: 'Average size of winning trades after costs.', recommended: 'Use this as the realistic reward leg.', formula: 'net_profit / net_winning_trades' },
                { field: 'Avg net loss', explanation: 'Average size of losing trades after costs.', recommended: 'Lower magnitude improves resilience.', formula: 'net_loss / net_losing_trades' },
                { field: 'Risk reward ratio', explanation: 'Average positive payoff divided by average negative payoff.', recommended: '>= 1.5 is a good practical starting target.', formula: 'avg_net_profit / abs(avg_net_loss)' },
                { field: 'Expectancy / trade', explanation: 'Expected average result per trade after costs.', recommended: 'Positive and stable across samples.', formula: 'net_pnl / total_trades' },
                { field: 'Cost factor', explanation: 'How strongly execution costs affect the raw edge.', recommended: 'Lower drag is better; watch for large gross vs net gap.', formula: 'gross_pnl / net_pnl or equivalent internal cost relation metric' },
            ],
        },
        {
            id: 'risk',
            title: 'Risk',
            description: 'Risk metrics focus on drawdown, return quality and resilience rather than just total money made.',
            detail: 'A strategy with high return but unstable drawdown will usually look fragile here.',
            rows: [
                { label: 'Max drawdown', value: formatMoney(stats.max_drawdown), tone: 'negative' },
                { label: 'Max drawdown %', value: formatPercent(stats.max_drawdown_pct), tone: 'negative' },
                { label: 'Drawdown duration bars', value: formatInteger(stats.drawdown_duration_bars) },
                { label: 'Sharpe ratio', value: formatValue(stats.sharpe_ratio) },
                { label: 'Sortino ratio', value: formatValue(stats.sortino_ratio) },
                { label: 'Avg return', value: formatValue(stats.avg_return) },
                { label: 'Kelly fraction', value: formatPercent(stats.kelly_fraction) },
            ],
            docRows: [
                { field: 'Max drawdown', explanation: 'Largest absolute peak-to-trough capital decline.', recommended: 'Keep as low as possible relative to the account goal.', formula: 'max(peak_balance - trough_balance)' },
                { field: 'Max drawdown %', explanation: 'Largest percentage peak-to-trough decline.', recommended: '<= 10% for a stronger profile.', formula: 'max_drawdown / peak_balance' },
                { field: 'Drawdown duration bars', explanation: 'Length of the worst underwater period measured in bars.', recommended: 'Shorter is usually healthier.', formula: 'bars_between_peak_and_recovery' },
                { field: 'Sharpe ratio', explanation: 'Return quality relative to total volatility.', recommended: '>= 1.5', formula: '(mean_return - risk_free_rate) / std_dev(returns)' },
                { field: 'Sortino ratio', explanation: 'Return quality relative to downside volatility only.', recommended: '>= 2.0', formula: '(mean_return - risk_free_rate) / downside_deviation' },
                { field: 'Avg return', explanation: 'Average per-trade or per-period return metric used by the engine.', recommended: 'Positive and stable, not just occasional spikes.', formula: 'mean(returns)' },
                { field: 'Kelly fraction', explanation: 'Sizing hint implied by edge and payoff profile.', recommended: 'Use conservatively, not as literal full sizing.', formula: 'win_rate - ((1 - win_rate) / reward_to_risk)' },
            ],
        },
        {
            id: 'export',
            title: 'Export',
            description: 'Use this section to choose exactly what should be exported and whether it should go to the clipboard or be saved as a file.',
            detail: 'Clipboard supports JSON or CSV. Saved files support HTML report or CSV.',
            rows: [],
            docRows: [
                { field: 'Content', explanation: 'Defines which slice of the result payload should be exported.', recommended: 'Use full report for reporting, narrower scopes for tooling.', formula: 'Scope selector for export payload.' },
                { field: 'Destination', explanation: 'Chooses whether the export goes to the clipboard or becomes a downloadable file.', recommended: 'Clipboard for quick sharing, save for reports.', formula: 'UI export target selector.' },
                { field: 'Format', explanation: 'Serialization format used for the chosen export destination.', recommended: 'JSON for tooling, CSV for spreadsheets, HTML for human-readable reports.', formula: 'Depends on destination constraints.' },
            ],
        },
    ]

    const selectedGroup = groups.find((group) => group.id === selectedGroupId) || groups[0]

    return (
        <div className='statisticsPanel'>
            <PortfolioSummaryPane strategyApplyResponse={strategyApplyResponse} />
            <div className='statisticsLayout'>
                <aside className='statisticsSidebar'>
                    <div className='statisticsSubviewTabs'>
                        <button
                            type='button'
                            className={`statisticsSubviewTab ${selectedView === 'data' ? 'active' : ''}`}
                            onClick={() => setSelectedView('data')}
                        >
                            Data
                        </button>
                        <button
                            type='button'
                            className={`statisticsSubviewTab ${selectedView === 'doc' ? 'active' : ''}`}
                            onClick={() => setSelectedView('doc')}
                        >
                            Doc
                        </button>
                    </div>
                    <div className='statisticsSidebarTitle'>Sections</div>
                    <div className='statisticsList'>
                        {groups.map((group) => (
                            <button
                                key={group.id}
                                type='button'
                                className={`statisticsListButton ${selectedGroup?.id === group.id ? 'active' : ''}`}
                                onClick={() => setSelectedGroupId(group.id)}
                            >
                                {group.title}
                            </button>
                        ))}
                    </div>
                </aside>

                <div className='statisticsContent'>
                    {selectedView === 'data' ? (
                        selectedGroup?.id === 'score' ? (
                            <StatisticsEvaluationCard evaluation={evaluation} />
                        ) : selectedGroup?.id === 'operations' ? (
                            <StatisticsOperationsPane
                                authToken={authToken}
                                strategyApplyResponse={strategyApplyResponse}
                                onResolveLoadedBacktestResponse={onResolveLoadedBacktestResponse}
                                onLogEvent={onLogEvent}
                            />
                        ) : selectedGroup?.id === 'export' ? (
                            <ExportPane
                                request={request}
                                backtest={backtest}
                                stats={stats}
                                results={results}
                                evaluation={evaluation}
                            />
                        ) : selectedGroup ? (
                            <StatisticsGroup
                                key={selectedGroup.title}
                                rows={selectedGroup.rows}
                                evaluationCriteriaByLabel={evaluationCriteriaByLabel}
                            />
                        ) : null
                    ) : (
                        <>
                            <div className='statisticsGroupDescription'>
                                <div className='statisticsGroupDescriptionText'>{selectedGroup?.description}</div>
                                <div className='statisticsGroupDescriptionMeta'>{selectedGroup?.detail}</div>
                            </div>
                            <StatisticsDocTable
                                rows={selectedGroup?.docRows || []}
                            />
                        </>
                    )}
                </div>
            </div>
        </div>
    )
}

export function Research({
    isActive,
    backtestResponse,
    authToken,
    isGuest = false,
    workspaceSocketStatus = 'connecting',
    chartSettings,
    currentWorkspaceSaveName = '',
    externalSelectedArchiveRunId = '',
    researchState = null,
    setResearchState = null,
    setStrategy,
    setStrategySetEntries = null,
    setBacktest,
    onOpenStrategy,
    onHydrateBacktestResult,
    onOpenResults,
    onLogEvent,
    sharedConsoleJobs = null,
    onSharedConsoleJobChange,
}) {
    const strategyApplyResponse = backtestResponse
    const [researchSurfaceTab, setResearchSurfaceTab] = useState('scientific-record')
    const [activeTab, setActiveTab] = useState('overview')
    const [selectedArchiveRunId, setSelectedArchiveRunId] = useState('')
    const shortlistStorageKey = useMemo(
        () => buildPaperShortlistStorageKey(currentWorkspaceSaveName),
        [currentWorkspaceSaveName],
    )
    const decisionLogStorageKey = useMemo(
        () => buildResearchDecisionLogStorageKey(currentWorkspaceSaveName),
        [currentWorkspaceSaveName],
    )
    const studiesStorageKey = useMemo(
        () => buildResearchStudiesStorageKey(currentWorkspaceSaveName),
        [currentWorkspaceSaveName],
    )
    const studyRunsStorageKey = useMemo(
        () => buildResearchStudyRunsStorageKey(currentWorkspaceSaveName),
        [currentWorkspaceSaveName],
    )
    const benchmarksStorageKey = useMemo(
        () => buildResearchBenchmarksStorageKey(currentWorkspaceSaveName),
        [currentWorkspaceSaveName],
    )
    const [paperShortlist, setPaperShortlist] = useState([])
    const [decisionLog, setDecisionLog] = useState([])
    const [savedStudies, setSavedStudies] = useState({})
    const [studyRuns, setStudyRuns] = useState([])
    const [benchmarkStrategies, setBenchmarkStrategies] = useState([])
    const [remoteStudyRuns, setRemoteStudyRuns] = useState([])
    const [remoteBenchmarks, setRemoteBenchmarks] = useState([])
    const [remoteResearchJobs, setRemoteResearchJobs] = useState([])
    const [remoteResearchBatches, setRemoteResearchBatches] = useState([])
    const [remoteResearchCampaigns, setRemoteResearchCampaigns] = useState([])
    const researchLoadFailureRef = useRef(new Map())
    const stats = strategyApplyResponse?.stats || null
    const researchChartSettings = useMemo(
        () => buildResearchChartSettings(chartSettings, strategyApplyResponse),
        [chartSettings, strategyApplyResponse],
    )
    const regimeInsight = getRegimeInsightModel(
        stats?.regime_summary || [],
        stats?.regime_stability_summary || [],
    )
    const hasSharedResearchJob = useMemo(
        () => Object.values(sharedConsoleJobs || {}).some((entry) => entry?.actor === 'research' && entry?.status === 'running'),
        [sharedConsoleJobs],
    )
    const activeResearchJobs = useMemo(
        () => remoteResearchJobs.filter((entry) => ['queued', 'running'].includes(String(entry?.status || '').toLowerCase())),
        [remoteResearchJobs],
    )
    const activeResearchBatches = useMemo(
        () => remoteResearchBatches.filter((entry) => ['queued', 'running'].includes(String(entry?.status || '').toLowerCase())),
        [remoteResearchBatches],
    )
    const researchDashboard = useMemo(() => {
        const counts = {
            queued: 0,
            paper: 0,
            review: 0,
            promoted: 0,
            dropped: 0,
        }

        let bestCandidate = null
        let paperVerdictCount = 0
        let readyForPromotionReview = 0
        let readyForLiveReview = 0
        let walkforwardConcernCount = 0
        let autoRejectCount = 0
        let highConfidenceCount = 0

        for (const entry of paperShortlist) {
            const decision = buildResearchDecisionEngine(entry)
            const status = String(entry?.trackerStatus || 'queued')
            if (Object.prototype.hasOwnProperty.call(counts, status)) {
                counts[status] += 1
            }

            const paperVerdict = String(entry?.paperVerdict || 'pending')
            if (paperVerdict !== 'pending') {
                paperVerdictCount += 1
            }

            if (decision.nextStep === 'ready_for_promotion_review') {
                readyForPromotionReview += 1
            }
            if (decision.nextStep === 'ready_for_live_review') {
                readyForLiveReview += 1
            }
            if (decision.autoDisposition === 'reject') {
                autoRejectCount += 1
            }
            if (decision.confidence === 'high') {
                highConfidenceCount += 1
            }

            if (!bestCandidate || Number(entry?.promotionScore || 0) > Number(bestCandidate?.promotionScore || 0)) {
                bestCandidate = entry
            }

            if (
                String(entry?.failureModeCategory || '') === 'timeframe_instability'
                || String(entry?.failureMode || '').toLowerCase().includes('walk-forward')
            ) {
                walkforwardConcernCount += 1
            }
        }

        const bestCandidateHasWalkforwardConcern = (
            bestCandidate
            && (
                String(bestCandidate?.failureModeCategory || '') === 'timeframe_instability'
                || String(bestCandidate?.failureMode || '').toLowerCase().includes('walk-forward')
            )
        )

        let alert = 'No candidates are in the paper-trading funnel yet.'
        if (bestCandidateHasWalkforwardConcern) {
            alert = 'Best candidate is strong in-sample, but still looks weak out-of-sample in walk-forward validation.'
        } else if (readyForLiveReview > 0) {
            alert = `${readyForLiveReview} candidate${readyForLiveReview > 1 ? 's are' : ' is'} ready for final live-review gating.`
        } else if (highConfidenceCount > 0 && counts.queued > 0) {
            alert = `${highConfidenceCount} candidate${highConfidenceCount > 1 ? 's have' : ' has'} high-confidence evidence in the current research funnel.`
        } else if (autoRejectCount > 0) {
            alert = `${autoRejectCount} candidate${autoRejectCount > 1 ? 's are' : ' is'} failing the stricter promotion gate and should be deprioritized.`
        } else if (walkforwardConcernCount > 0) {
            alert = `${walkforwardConcernCount} candidate${walkforwardConcernCount > 1 ? 's show' : ' shows'} walk-forward instability and should be treated cautiously.`
        } else if (readyForPromotionReview > 0) {
            alert = `${readyForPromotionReview} candidate${readyForPromotionReview > 1 ? 's are' : ' is'} ready for promotion review.`
        } else if (counts.paper > 0) {
            alert = `${counts.paper} candidate${counts.paper > 1 ? 's are' : ' is'} currently in paper follow-up.`
        } else if (counts.queued > 0) {
            alert = `${counts.queued} candidate${counts.queued > 1 ? 's are' : ' is'} waiting for paper follow-up.`
        }

        let platformVerdict = 'idle'
        let platformVerdictDetail = 'Research funnel is ready, but still waiting for stronger candidate evidence.'
        if (readyForLiveReview > 0) {
            platformVerdict = 'live_review'
            platformVerdictDetail = 'At least one candidate is ready for final live-review gating.'
        } else if (readyForPromotionReview > 0) {
            platformVerdict = 'promotion_review'
            platformVerdictDetail = 'There are candidates ready for promotion review.'
        } else if (counts.paper > 0 || highConfidenceCount > 0) {
            platformVerdict = 'paper_active'
            platformVerdictDetail = 'The funnel is active and currently carrying candidates through paper validation.'
        } else if (counts.queued > 0) {
            platformVerdict = 'queued_only'
            platformVerdictDetail = 'There are queued candidates, but none are mature enough for paper or promotion review yet.'
        }

        return {
            counts,
            bestCandidate,
            paperVerdictCount,
            readyForPromotionReview,
            readyForLiveReview,
            walkforwardConcernCount,
            autoRejectCount,
            highConfidenceCount,
            bestCandidateHasWalkforwardConcern: Boolean(bestCandidateHasWalkforwardConcern),
            platformVerdict,
            platformVerdictDetail,
            alert,
        }
    }, [paperShortlist])

    const reportResearchLoadFailure = useCallback((key, label, error) => {
        const message = error?.message || 'unknown error'
        const signature = `${key}:${message}`
        const now = Date.now()
        const previousAt = Number(researchLoadFailureRef.current.get(signature) || 0)
        if (now - previousAt < 8000) {
            return
        }
        researchLoadFailureRef.current.set(signature, now)
        onLogEvent?.(`Research · Could not load ${label}: ${message}`)
    }, [onLogEvent])

    const researchWorkbenchSections = [
        { id: 'overview', title: 'Regime overview' },
        { id: 'what-worked', title: 'What worked' },
        { id: 'operations', title: 'Research Ops' },
        { id: 'presets', title: 'Preset compare' },
        { id: 'timeframes', title: 'Timeframe study' },
        { id: 'symbols', title: 'Symbol study' },
        { id: 'splits', title: 'Walk-forward' },
        { id: 'promotion', title: 'Promotion candidates' },
        { id: 'strategies', title: 'Strategy compare' },
        { id: 'archive', title: 'Study archive' },
        { id: 'shortlist', title: 'Paper shortlist' },
        { id: 'review', title: 'Promotion review' },
        { id: 'live', title: 'Live readiness' },
        { id: 'failure', title: 'Failure modes' },
        { id: 'export', title: 'Export' },
        { id: 'playbook', title: 'Research playbook' },
    ]

    function handleApplyStrategyPreset(nextStrategy) {
        applyResearchStrategySelection({
            setStrategy,
            setStrategySetEntries,
            strategy: nextStrategy,
            strategies: [],
        })
    }

    const isScientificRecordSurface = researchSurfaceTab === 'scientific-record'
    const isPositiveHistorySurface = researchSurfaceTab === 'positive-history'
    const isWorkbenchSurface = researchSurfaceTab === 'workbench'

    useEffect(() => {
        if (externalSelectedArchiveRunId) {
            setSelectedArchiveRunId(String(externalSelectedArchiveRunId))
            setResearchSurfaceTab('workbench')
            setActiveTab('archive')
        }
    }, [externalSelectedArchiveRunId])

    useEffect(() => {
        if (activeTab === 'positive-history') {
            setActiveTab('overview')
        }
    }, [activeTab])

    useEffect(() => {
        if (researchState && typeof researchState === 'object') {
            setPaperShortlist(Array.isArray(researchState.paperShortlist) ? researchState.paperShortlist : [])
            return
        }
        try {
            const raw = window.localStorage.getItem(shortlistStorageKey) || '[]'
            const parsed = JSON.parse(raw)
            setPaperShortlist(Array.isArray(parsed) ? parsed : [])
        } catch {
            setPaperShortlist([])
        }
    }, [shortlistStorageKey, researchState])

    useEffect(() => {
        if (researchState && typeof researchState === 'object') {
            setDecisionLog(Array.isArray(researchState.decisionLog) ? researchState.decisionLog : [])
            return
        }
        try {
            const raw = window.localStorage.getItem(decisionLogStorageKey) || '[]'
            const parsed = JSON.parse(raw)
            setDecisionLog(Array.isArray(parsed) ? parsed : [])
        } catch {
            setDecisionLog([])
        }
    }, [decisionLogStorageKey, researchState])

    useEffect(() => {
        if (researchState && typeof researchState === 'object') {
            setSavedStudies(researchState.savedStudies && typeof researchState.savedStudies === 'object' ? researchState.savedStudies : {})
            return
        }
        try {
            const raw = window.localStorage.getItem(studiesStorageKey) || '{}'
            const parsed = JSON.parse(raw)
            setSavedStudies(parsed && typeof parsed === 'object' ? parsed : {})
        } catch {
            setSavedStudies({})
        }
    }, [studiesStorageKey, researchState])

    useEffect(() => {
        if (researchState && typeof researchState === 'object') {
            setStudyRuns(Array.isArray(researchState.studyRuns) ? researchState.studyRuns : [])
            return
        }
        try {
            const raw = window.localStorage.getItem(studyRunsStorageKey) || '[]'
            const parsed = JSON.parse(raw)
            setStudyRuns(Array.isArray(parsed) ? parsed : [])
        } catch {
            setStudyRuns([])
        }
    }, [studyRunsStorageKey, researchState])

    useEffect(() => {
        if (researchState && typeof researchState === 'object') {
            setBenchmarkStrategies(Array.isArray(researchState.benchmarkStrategies) ? researchState.benchmarkStrategies : [])
            return
        }
        try {
            const raw = window.localStorage.getItem(benchmarksStorageKey) || '[]'
            const parsed = JSON.parse(raw)
            setBenchmarkStrategies(Array.isArray(parsed) ? parsed : [])
        } catch {
            setBenchmarkStrategies([])
        }
    }, [benchmarksStorageKey, researchState])

    function syncResearchState(nextPatch) {
        if (typeof setResearchState === 'function') {
            setResearchState((current) => ({
                paperShortlist: Array.isArray(current?.paperShortlist) ? current.paperShortlist : [],
                decisionLog: Array.isArray(current?.decisionLog) ? current.decisionLog : [],
                savedStudies: current?.savedStudies && typeof current.savedStudies === 'object' ? current.savedStudies : {},
                studyRuns: Array.isArray(current?.studyRuns) ? current.studyRuns : [],
                benchmarkStrategies: Array.isArray(current?.benchmarkStrategies) ? current.benchmarkStrategies : [],
                ...nextPatch,
            }))
        }
    }

    function persistPaperShortlist(nextShortlist) {
        setPaperShortlist(nextShortlist)
        syncResearchState({ paperShortlist: nextShortlist })
        try {
            window.localStorage.setItem(shortlistStorageKey, JSON.stringify(nextShortlist))
        } catch {
            // ignore local persistence errors
        }
    }

    function appendDecisionLog(entry) {
        const logEntry = {
            id: `${entry?.action || 'event'}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`,
            at: new Date().toISOString(),
            atLabel: new Date().toLocaleString(),
            ...entry,
        }
        const nextLog = [logEntry, ...decisionLog].slice(0, 100)
        setDecisionLog(nextLog)
        syncResearchState({ decisionLog: nextLog })
        try {
            window.localStorage.setItem(decisionLogStorageKey, JSON.stringify(nextLog))
        } catch {
            // ignore local persistence errors
        }
    }

    function persistSavedStudies(nextStudies) {
        setSavedStudies(nextStudies)
        syncResearchState({ savedStudies: nextStudies })
        try {
            window.localStorage.setItem(studiesStorageKey, JSON.stringify(nextStudies))
        } catch {
            // ignore local persistence errors
        }
    }

    function persistStudyRuns(nextRuns) {
        setStudyRuns(nextRuns)
        syncResearchState({ studyRuns: nextRuns })
        try {
            window.localStorage.setItem(studyRunsStorageKey, JSON.stringify(nextRuns))
        } catch {
            // ignore local persistence errors
        }
    }

    function persistBenchmarkStrategies(nextBenchmarks) {
        setBenchmarkStrategies(nextBenchmarks)
        syncResearchState({ benchmarkStrategies: nextBenchmarks })
        try {
            window.localStorage.setItem(benchmarksStorageKey, JSON.stringify(nextBenchmarks))
        } catch {
            // ignore local persistence errors
        }
    }

    const refreshRemoteStudyRuns = useCallback(async () => {
        if (!authToken) {
            setRemoteStudyRuns([])
            return
        }

        const response = await fetch(buildApiUrl('/workspace/research-runs?workspace_id=default&limit=100&include_payload=false'), {
            headers: {
                Authorization: `Bearer ${authToken}`,
            },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to list research runs.'))
        }
        setRemoteStudyRuns(Array.isArray(payload?.runs) ? payload.runs : [])
    }, [authToken])

    async function loadRemoteStudyRunDetail(runId) {
        if (!authToken || !runId) {
            return null
        }

        const existingRun = remoteStudyRuns.find((entry) => String(entry?.id) === String(runId))
        if (existingRun && existingRun?.payload_loaded !== false) {
            return existingRun || null
        }

        const response = await fetch(buildApiUrl(`/workspace/research-runs/${runId}?workspace_id=default&include_payload=true`), {
            headers: {
                Authorization: `Bearer ${authToken}`,
            },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to load research run details.'))
        }

        const hydratedRun = payload?.run || null
        if (!hydratedRun) {
            return null
        }

        setRemoteStudyRuns((current) => {
            let replaced = false
            const nextRuns = current.map((entry) => {
                if (String(entry?.id) !== String(runId)) {
                    return entry
                }
                replaced = true
                return hydratedRun
            })
            return replaced ? nextRuns : [hydratedRun, ...nextRuns]
        })
        return hydratedRun
    }

    const refreshRemoteResearchJobs = useCallback(async () => {
        if (!authToken) {
            setRemoteResearchJobs([])
            return
        }

        const response = await fetch(buildApiUrl('/workspace/research-jobs?workspace_id=default&limit=100&include_payload=false'), {
            headers: {
                Authorization: `Bearer ${authToken}`,
            },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to list research jobs.'))
        }
        setRemoteResearchJobs((current) => reconcileRemoteResearchEntities(current, Array.isArray(payload?.jobs) ? payload.jobs : []))
    }, [authToken])

    const refreshRemoteResearchBatches = useCallback(async () => {
        if (!authToken) {
            setRemoteResearchBatches([])
            return
        }

        const response = await fetch(buildApiUrl('/workspace/research-batches?workspace_id=default&limit=100&include_payload=false'), {
            headers: {
                Authorization: `Bearer ${authToken}`,
            },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to list research batches.'))
        }
        setRemoteResearchBatches((current) => reconcileRemoteResearchEntities(current, Array.isArray(payload?.batches) ? payload.batches : []))
    }, [authToken])

    async function loadRemoteResearchJobDetail(jobId) {
        const normalizedJobId = String(jobId || '').trim()
        if (!authToken || !normalizedJobId) {
            return null
        }

        const existingJob = remoteResearchJobs.find((entry) => String(entry?.id || '') === normalizedJobId) || null
        if (existingJob && existingJob?.result_loaded !== false) {
            return existingJob
        }

        const response = await fetch(buildApiUrl(`/workspace/research-jobs/${normalizedJobId}?workspace_id=default&include_payload=true`), {
            headers: {
                Authorization: `Bearer ${authToken}`,
            },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to load research job details.'))
        }

        const hydratedJob = payload?.job || null
        if (!hydratedJob) {
            return null
        }

        setRemoteResearchJobs((current) => mergeRemoteResearchEntities(current, hydratedJob))
        return hydratedJob
    }

    const refreshRemoteResearchCampaigns = useCallback(async () => {
        if (!authToken) {
            setRemoteResearchCampaigns([])
            return
        }

        const response = await fetch(buildApiUrl('/workspace/research-campaigns?workspace_id=default&limit=100&include_payload=false'), {
            headers: {
                Authorization: `Bearer ${authToken}`,
            },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to list research campaigns.'))
        }
        setRemoteResearchCampaigns(Array.isArray(payload?.campaigns) ? payload.campaigns : [])
    }, [authToken])

    const refreshRemoteBenchmarks = useCallback(async () => {
        if (!authToken) {
            setRemoteBenchmarks([])
            return
        }

        const response = await fetch(buildApiUrl('/workspace/strategy-benchmarks?workspace_id=default&limit=100'), {
            headers: {
                Authorization: `Bearer ${authToken}`,
            },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to list strategy benchmarks.'))
        }
        setRemoteBenchmarks(Array.isArray(payload?.benchmarks) ? payload.benchmarks.map((entry) => ({
            benchmark_id: String(entry?.id || ''),
            label: entry?.label || 'Benchmark',
            side: entry?.side || '',
            source: entry?.source || '',
            notes: entry?.notes || '',
            strategy: entry?.strategy || {},
            strategies: Array.isArray(entry?.strategies) ? entry.strategies : [],
            addedAt: entry?.created_at ? new Date(Number(entry.created_at) * 1000).toISOString() : '',
            addedAtLabel: entry?.created_at ? new Date(Number(entry.created_at) * 1000).toLocaleString() : '',
        })) : [])
    }, [authToken])

    useEffect(() => {
        if (!authToken) {
            setRemoteStudyRuns([])
            setRemoteBenchmarks([])
            setRemoteResearchJobs([])
            setRemoteResearchBatches([])
            setRemoteResearchCampaigns([])
            return
        }
        if (!isActive || researchSurfaceTab !== 'workbench') {
            return
        }

        void refreshRemoteResearchJobs().catch((error) => {
            reportResearchLoadFailure('research_jobs', 'research jobs', error)
        })
        void refreshRemoteResearchBatches().catch((error) => {
            reportResearchLoadFailure('research_batches', 'research batches', error)
        })
        void refreshRemoteResearchCampaigns().catch((error) => {
            reportResearchLoadFailure('research_campaigns', 'research campaigns', error)
        })
        void refreshRemoteStudyRuns().catch((error) => {
            reportResearchLoadFailure('research_runs', 'remote study runs', error)
        })
        void refreshRemoteBenchmarks().catch((error) => {
            reportResearchLoadFailure('research_benchmarks', 'strategy benchmarks', error)
        })
    }, [authToken, isActive, refreshRemoteBenchmarks, refreshRemoteResearchBatches, refreshRemoteResearchCampaigns, refreshRemoteResearchJobs, refreshRemoteStudyRuns, researchSurfaceTab, reportResearchLoadFailure])

    useEffect(() => {
        if (
            !authToken
            || !isActive
            || researchSurfaceTab !== 'workbench'
            || workspaceSocketStatus === 'connected'
            || (!activeResearchJobs.length && !activeResearchBatches.length && !hasSharedResearchJob)
        ) {
            return undefined
        }

        const intervalId = window.setInterval(() => {
            void refreshRemoteResearchJobs().catch(() => {})
            void refreshRemoteResearchBatches().catch(() => {})
        }, 2000)

        return () => window.clearInterval(intervalId)
    }, [authToken, isActive, refreshRemoteResearchBatches, refreshRemoteResearchJobs, workspaceSocketStatus, activeResearchJobs.length, activeResearchBatches.length, hasSharedResearchJob, researchSurfaceTab])

    useEffect(() => {
        if (!authToken || !isActive || researchSurfaceTab !== 'workbench') {
            return undefined
        }

        function handleResearchJobUpdated(event) {
            const nextJob = event?.detail
            if (nextJob?.id) {
                setRemoteResearchJobs((current) => mergeRemoteResearchEntities(current, nextJob))
                return
            }
            void refreshRemoteResearchJobs().catch(() => {})
        }

        function handleResearchBatchUpdated(event) {
            const nextBatch = event?.detail
            if (nextBatch?.id) {
                setRemoteResearchBatches((current) => mergeRemoteResearchEntities(current, nextBatch))
                return
            }
            void refreshRemoteResearchBatches().catch(() => {})
        }

        window.addEventListener('workspace:research-job-updated', handleResearchJobUpdated)
        window.addEventListener('workspace:research-batch-updated', handleResearchBatchUpdated)
        return () => {
            window.removeEventListener('workspace:research-job-updated', handleResearchJobUpdated)
            window.removeEventListener('workspace:research-batch-updated', handleResearchBatchUpdated)
        }
    }, [authToken, isActive, refreshRemoteResearchBatches, refreshRemoteResearchJobs, researchSurfaceTab])

    function handleStudyComplete(type, side, payload) {
        const strategySnapshot = buildResearchStrategySnapshot(strategyApplyResponse, chartSettings)
        const payloadWithSnapshot = strategySnapshot
            ? {
                ...payload,
                strategySnapshot,
            }
            : payload
        const nextStudies = {
            ...savedStudies,
            [type]: {
                ...(savedStudies?.[type] || {}),
                [side || 'default']: {
                    savedAt: new Date().toISOString(),
                    savedAtLabel: new Date().toLocaleString(),
                    payload: payloadWithSnapshot,
                    strategySnapshot,
                },
            },
        }
        persistSavedStudies(nextStudies)
        const nextRuns = [
            buildResearchStudyRunEntry(type, side, payloadWithSnapshot, { strategySnapshot }),
            ...studyRuns,
        ].slice(0, 40)
        persistStudyRuns(nextRuns)
        if (authToken) {
            const latestRun = nextRuns[0]
            void fetch(buildApiUrl('/workspace/research-runs'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    run_type: latestRun.type,
                    side: latestRun.side,
                    run_name: latestRun.run_name,
                    version: latestRun.version,
                    best_id: latestRun.best_id,
                    best_label: latestRun.best_label,
                    comparison_count: latestRun.comparison_count,
                    payload: latestRun.payload,
                }),
            })
                .then((response) => readJsonResponse(response).then((data) => ({ response, data })))
                .then(({ response, data }) => {
                    if (!response.ok || data?.status !== 'ok') {
                        throw new Error(extractApiErrorMessage(data, 'Failed to save research run.'))
                    }
                    void refreshRemoteStudyRuns()
                })
                .catch((error) => {
                    onLogEvent?.(`Research · Could not save remote study run: ${error?.message || 'unknown error'}`)
                })
        }
    }

    async function handleDeleteRemoteRun(runId) {
        if (!authToken || !runId) {
            return
        }
        try {
            const response = await fetch(buildApiUrl(`/workspace/research-runs/${runId}?workspace_id=default`), {
                method: 'DELETE',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to delete research run.'))
            }
            setRemoteStudyRuns((current) => current.filter((entry) => String(entry?.id) !== String(runId)))
            onLogEvent?.('Research · Deleted a remote study run.')
        } catch (error) {
            onLogEvent?.(`Research · Could not delete remote study run: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleUpdateRemoteRun(runId, patch) {
        if (!authToken || !runId) {
            return
        }
        try {
            const response = await fetch(buildApiUrl(`/workspace/research-runs/${runId}`), {
                method: 'PATCH',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    ...patch,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to update research run.'))
            }
            await refreshRemoteStudyRuns()
            onLogEvent?.('Research · Updated study archive metadata.')
        } catch (error) {
            onLogEvent?.(`Research · Could not update study archive metadata: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleCancelRemoteJob(jobId) {
        if (!authToken || !jobId) {
            return
        }

        try {
            const response = await fetch(buildApiUrl(`/workspace/research-jobs/${jobId}/cancel?workspace_id=default`), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to cancel research job.'))
            }
            if (payload?.job) {
                setRemoteResearchJobs((current) => mergeRemoteResearchEntities(current, payload.job))
            } else {
                await refreshRemoteResearchJobs()
            }
            onLogEvent?.('Research · Cancelled backend research job.')
        } catch (error) {
            onLogEvent?.(`Research · Could not cancel research job: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleCreateResearchBatch(batchPayload) {
        if (!authToken) {
            return
        }

        const jobs = Array.isArray(batchPayload?.jobs) ? batchPayload.jobs.filter((entry) => entry?.request) : []
        if (!jobs.length) {
            onLogEvent?.('Research · No valid jobs were available to create a backend batch.')
            return
        }

        try {
            const response = await fetch(buildApiUrl('/workspace/research-batches'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: String(batchPayload?.label || '').trim() || 'Research batch',
                    jobs,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to create research batch.'))
            }
            if (payload?.batch) {
                setRemoteResearchBatches((current) => mergeRemoteResearchEntities(current, payload.batch))
            } else {
                await refreshRemoteResearchBatches()
            }
            onLogEvent?.(`Research · Queued backend batch "${payload?.batch?.label || 'Research batch'}".`)
        } catch (error) {
            onLogEvent?.(`Research · Could not create research batch: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleCreateResearchCampaign(campaignPayload) {
        if (!authToken) {
            return
        }

        const jobs = Array.isArray(campaignPayload?.jobs) ? campaignPayload.jobs.filter((entry) => entry?.request) : []
        if (!jobs.length) {
            onLogEvent?.('Research · No valid jobs were available to save as a campaign.')
            return
        }

        try {
            const response = await fetch(buildApiUrl('/workspace/research-campaigns'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: String(campaignPayload?.label || '').trim() || 'Research campaign',
                    description: String(campaignPayload?.description || '').trim(),
                    jobs,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to save research campaign.'))
            }
            await refreshRemoteResearchCampaigns()
            onLogEvent?.(`Research · Saved backend research campaign "${payload?.campaign?.label || 'Research campaign'}".`)
        } catch (error) {
            onLogEvent?.(`Research · Could not save research campaign: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleCancelRemoteBatch(batchId) {
        if (!authToken || !batchId) {
            return
        }

        try {
            const payload = await cancelResearchBatchRequest(authToken, batchId)
            if (payload?.batch) {
                setRemoteResearchBatches((current) => mergeRemoteResearchEntities(current, payload.batch))
            } else {
                await refreshRemoteResearchBatches()
            }
            onLogEvent?.('Research · Cancelled backend research batch.')
        } catch (error) {
            onLogEvent?.(`Research · Could not cancel research batch: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleLaunchResearchCampaign(campaignId) {
        if (!authToken || !campaignId) {
            return
        }

        try {
            const response = await fetch(buildApiUrl(`/workspace/research-campaigns/${campaignId}/launch?workspace_id=default`), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to launch research campaign.'))
            }
            if (payload?.batch) {
                setRemoteResearchBatches((current) => mergeRemoteResearchEntities(current, payload.batch))
            } else {
                await refreshRemoteResearchBatches()
            }
            onLogEvent?.('Research · Launched backend research campaign.')
        } catch (error) {
            onLogEvent?.(`Research · Could not launch research campaign: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleDeleteResearchCampaign(campaignId) {
        if (!authToken || !campaignId) {
            return
        }

        try {
            const response = await fetch(buildApiUrl(`/workspace/research-campaigns/${campaignId}?workspace_id=default`), {
                method: 'DELETE',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to delete research campaign.'))
            }
            await refreshRemoteResearchCampaigns()
            onLogEvent?.('Research · Deleted backend research campaign.')
        } catch (error) {
            onLogEvent?.(`Research · Could not delete research campaign: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleRerunRemoteJob(job) {
        if (!authToken || !job?.request) {
            return
        }

        try {
            const response = await fetch(buildApiUrl('/workspace/research-jobs'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    job_type: job?.job_type || 'preset_compare',
                    request: job.request,
                    run_label: job?.run_label || '',
                    run_notes: job?.run_notes || '',
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to re-run research job.'))
            }
            if (payload?.job) {
                setRemoteResearchJobs((current) => mergeRemoteResearchEntities(current, payload.job))
            } else {
                await refreshRemoteResearchJobs()
            }
            onLogEvent?.('Research · Re-queued backend research job.')
        } catch (error) {
            onLogEvent?.(`Research · Could not re-run research job: ${error?.message || 'unknown error'}`)
        }
    }

    async function handleSaveRemoteJobStrategy(job) {
        if (!authToken || !job) {
            return
        }

        let targetJob = job
        let savablePayload = extractSavableStrategyPayloadFromResearchJob(targetJob)
        if (!Object.keys(savablePayload.strategy).length && !savablePayload.strategies.length && job?.id) {
            try {
                targetJob = await loadRemoteResearchJobDetail(job.id) || job
                savablePayload = extractSavableStrategyPayloadFromResearchJob(targetJob)
            } catch (error) {
                onLogEvent?.(`Research · Could not load full job details before saving: ${error?.message || 'unknown error'}`)
            }
        }
        const nextStrategy = savablePayload.strategy
        const nextStrategies = savablePayload.strategies

        if (!Object.keys(nextStrategy).length && !nextStrategies.length) {
            onLogEvent?.('Research · This backend job does not expose a savable strategy payload.')
            return
        }

        const safeLabel = String(
            targetJob?.run_label
            || targetJob?.request?.label
            || targetJob?.result?.pipeline?.label
            || targetJob?.phase_label
            || targetJob?.job_type
            || 'Research job strategy'
        ).trim() || 'Research job strategy'

        try {
            const jobChartSettings = buildResearchJobChartSettings(targetJob, researchChartSettings)
            const benchmarkPayload = buildStrategyBenchmarkPayload({
                label: safeLabel,
                side: 'both',
                source: 'research-job',
                notes: `Saved from backend job #${String(targetJob?.id || '').trim()}.`,
                strategy: nextStrategy,
                strategies: nextStrategies,
                chartSettings: jobChartSettings,
                extraIndicators: Array.isArray(targetJob?.result?.pipeline?.applied_indicators)
                    ? targetJob.result.pipeline.applied_indicators
                    : jobChartSettings?.indicators || [],
            })
            const response = await fetch(buildApiUrl('/workspace/strategy-benchmarks'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    ...benchmarkPayload,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to save strategy from backend job.'))
            }
            await refreshRemoteBenchmarks()
            onLogEvent?.(`Research · Saved job strategy to Strategy Manager: ${safeLabel}.`)
        } catch (error) {
            onLogEvent?.(`Research · Could not save backend job strategy: ${error?.message || 'unknown error'}`)
        }
    }

    function handleOpenArchivedRun(runId) {
        if (!runId) {
            return
        }
        setSelectedArchiveRunId(runId)
        setActiveTab('archive')
        onLogEvent?.('Research · Opened archived run linked to backend job.')
    }

    function handleAddToShortlist(entry) {
        const nextShortlist = [
            entry,
            ...paperShortlist.filter((item) => item?.shortlist_id !== entry?.shortlist_id),
        ].slice(0, 24)
        persistPaperShortlist(nextShortlist)
        appendDecisionLog({
            action: 'add',
            label: entry?.label || 'Unnamed candidate',
            side: entry?.side || '',
            message: `Added to paper shortlist with disposition ${entry?.disposition || 'watch'}.`,
        })
        onLogEvent?.(`Research · Added ${entry.label} to the paper shortlist.`)
    }

    function handleRemoveFromShortlist(shortlistId) {
        const removedEntry = paperShortlist.find((entry) => entry?.shortlist_id === shortlistId) || null
        const nextShortlist = paperShortlist.filter((entry) => entry?.shortlist_id !== shortlistId)
        persistPaperShortlist(nextShortlist)
        if (removedEntry) {
            appendDecisionLog({
                action: 'remove',
                label: removedEntry?.label || 'Unnamed candidate',
                side: removedEntry?.side || '',
                message: 'Removed from paper shortlist.',
            })
        }
        onLogEvent?.('Research · Removed a candidate from the paper shortlist.')
    }

    function handleUpdateShortlistEntry(shortlistId, patch) {
        const currentEntry = paperShortlist.find((entry) => entry?.shortlist_id === shortlistId) || null
        const nextShortlist = paperShortlist.map((entry) => (
            entry?.shortlist_id === shortlistId
                ? { ...entry, ...patch }
                : entry
        ))
        persistPaperShortlist(nextShortlist)
        if (currentEntry) {
            if (patch?.trackerStatus !== undefined && patch.trackerStatus !== currentEntry?.trackerStatus) {
                appendDecisionLog({
                    action: 'status_change',
                    label: currentEntry?.label || 'Unnamed candidate',
                    side: currentEntry?.side || '',
                    message: `Status changed from ${currentEntry?.trackerStatus || 'queued'} to ${patch.trackerStatus}.`,
                })
            }
            if (patch?.finalDecision !== undefined && patch.finalDecision !== currentEntry?.finalDecision) {
                appendDecisionLog({
                    action: 'decision_change',
                    label: currentEntry?.label || 'Unnamed candidate',
                    side: currentEntry?.side || '',
                    message: `Decision changed from ${currentEntry?.finalDecision || 'pending'} to ${patch.finalDecision}.`,
                })
            }
            if (patch?.paperVerdict !== undefined && patch.paperVerdict !== currentEntry?.paperVerdict) {
                appendDecisionLog({
                    action: 'paper_verdict_change',
                    label: currentEntry?.label || 'Unnamed candidate',
                    side: currentEntry?.side || '',
                    message: `Paper verdict changed from ${currentEntry?.paperVerdict || 'pending'} to ${patch.paperVerdict}.`,
                })
            }
        }
    }

    function handleApplyShortlistEntry(entry) {
        if (!entry?.strategy) {
            return
        }
        applyResearchStrategySelection({
            setStrategy,
            setStrategySetEntries,
            strategy: entry.strategy,
            strategies: entry?.strategies || [],
        })
        onOpenStrategy?.()
        appendDecisionLog({
            action: 'apply',
            label: entry?.label || 'Unnamed candidate',
            side: entry?.side || '',
            message: 'Applied shortlist candidate to Strategy.',
        })
        onLogEvent?.(`Research · Applied shortlist candidate: ${entry.label}.`)
    }

    function handleAddBenchmark(entry) {
        const nextBenchmarks = [
            entry,
            ...benchmarkStrategies.filter((item) => item?.benchmark_id !== entry?.benchmark_id),
        ].slice(0, 40)
        persistBenchmarkStrategies(nextBenchmarks)
        if (authToken) {
            const benchmarkPayload = buildStrategyBenchmarkPayload({
                label: entry?.label || 'Benchmark',
                side: entry?.side || '',
                source: entry?.source || '',
                notes: entry?.notes || '',
                strategy: entry?.strategy || {},
                strategies: entry?.strategies || [],
                chartSettings: researchChartSettings,
                extraIndicators: researchChartSettings?.indicators || [],
            })
            void fetch(buildApiUrl('/workspace/strategy-benchmarks'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    ...benchmarkPayload,
                }),
            })
                .then((response) => readJsonResponse(response).then((data) => ({ response, data })))
                .then(({ response, data }) => {
                    if (!response.ok || data?.status !== 'ok') {
                        throw new Error(extractApiErrorMessage(data, 'Failed to save strategy benchmark.'))
                    }
                    void refreshRemoteBenchmarks()
                })
                .catch((error) => {
                    onLogEvent?.(`Research · Could not save strategy benchmark: ${error?.message || 'unknown error'}`)
                })
        }
        appendDecisionLog({
            action: 'benchmark_add',
            label: entry?.label || 'Benchmark',
            side: entry?.side || '',
            message: 'Saved strategy benchmark for future comparisons.',
        })
        onLogEvent?.(`Research · Saved benchmark: ${entry?.label || 'Benchmark'}.`)
    }

    function handleRemoveBenchmark(benchmarkId) {
        const removedEntry = benchmarkStrategies.find((entry) => entry?.benchmark_id === benchmarkId) || null
        const nextBenchmarks = benchmarkStrategies.filter((entry) => entry?.benchmark_id !== benchmarkId)
        persistBenchmarkStrategies(nextBenchmarks)
        if (authToken && benchmarkId && !String(benchmarkId).includes(':')) {
            void fetch(buildApiUrl(`/workspace/strategy-benchmarks/${benchmarkId}?workspace_id=default`), {
                method: 'DELETE',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
                .then((response) => readJsonResponse(response).then((data) => ({ response, data })))
                .then(({ response, data }) => {
                    if (!response.ok || data?.status !== 'ok') {
                        throw new Error(extractApiErrorMessage(data, 'Failed to delete strategy benchmark.'))
                    }
                    void refreshRemoteBenchmarks()
                })
                .catch((error) => {
                    onLogEvent?.(`Research · Could not delete strategy benchmark: ${error?.message || 'unknown error'}`)
                })
        }
        if (removedEntry) {
            appendDecisionLog({
                action: 'benchmark_remove',
                label: removedEntry?.label || 'Benchmark',
                side: removedEntry?.side || '',
                message: 'Removed strategy benchmark from the archive.',
            })
        }
        onLogEvent?.('Research · Removed a strategy benchmark.')
    }

    function handleUpdateBenchmark(benchmarkId, patch) {
        const nextBenchmarks = benchmarkStrategies.map((entry) => (
            entry?.benchmark_id === benchmarkId
                ? { ...entry, ...patch }
                : entry
        ))
        persistBenchmarkStrategies(nextBenchmarks)

        if (authToken && benchmarkId && !String(benchmarkId).includes(':')) {
            void fetch(buildApiUrl(`/workspace/strategy-benchmarks/${benchmarkId}`), {
                method: 'PATCH',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    ...patch,
                }),
            })
                .then((response) => readJsonResponse(response).then((data) => ({ response, data })))
                .then(({ response, data }) => {
                    if (!response.ok || data?.status !== 'ok') {
                        throw new Error(extractApiErrorMessage(data, 'Failed to update strategy benchmark.'))
                    }
                    void refreshRemoteBenchmarks()
                })
                .catch((error) => {
                    onLogEvent?.(`Research · Could not update strategy benchmark: ${error?.message || 'unknown error'}`)
                })
        }
    }

    async function handleCopyShortlist() {
        try {
            await navigator.clipboard.writeText(JSON.stringify(paperShortlist, null, 2))
            onLogEvent?.('Research · Copied paper shortlist JSON.')
        } catch (error) {
            onLogEvent?.(`Research · Could not copy paper shortlist: ${error?.message || 'clipboard error'}`)
        }
    }

    const activeBenchmarks = remoteBenchmarks.length ? remoteBenchmarks : benchmarkStrategies

    return (
        <div className={`Results Research ${isActive ? 'active' : ''}`}>
            <div className='resultsPane resultsPaneRight researchPane'>
                <div className='statisticsPanel regimeTerminalPanel'>
                    <div className='statisticsSubviewTabs researchPrimaryTabs'>
                        <button
                            type='button'
                            className={`statisticsSubviewTab ${isScientificRecordSurface ? 'active' : ''}`}
                            onClick={() => setResearchSurfaceTab('scientific-record')}
                        >
                            Scientific record
                        </button>
                        <button
                            type='button'
                            className={`statisticsSubviewTab ${isWorkbenchSurface ? 'active' : ''}`}
                            onClick={() => setResearchSurfaceTab('workbench')}
                        >
                            Research workbench
                        </button>
                        <button
                            type='button'
                            className={`statisticsSubviewTab ${isPositiveHistorySurface ? 'active' : ''}`}
                            onClick={() => setResearchSurfaceTab('positive-history')}
                        >
                            Positive history
                        </button>
                    </div>

                    <div className='statisticsGroupDescription'>
                        <div className='statisticsGroupDescriptionText'>
                            {isScientificRecordSurface
                                ? (isGuest
                                    ? 'This guest view shows the current reference article as a read-only research display.'
                                    : 'Build the scientific literature of the strategy program here: each article should keep the mandate, feature rationale, experimental chronology, and resulting decisions in one continuous research narrative.')
                                : (isPositiveHistorySurface
                                    ? 'Inspect the cross-study Positive history registry here and refresh it directly from the shared winner catalog.'
                                    : 'Use the workbench to compare candidates, run studies, inspect operations, and manage the promotion funnel.')}
                        </div>
                        <div className='statisticsGroupDescriptionMeta'>
                            {isScientificRecordSurface
                                ? 'The scientific record is the durable reference surface for future research continuity.'
                                : (isPositiveHistorySurface
                                    ? 'This dedicated tab now owns the Positive history table so refresh and review stay isolated from the workbench section rail.'
                                    : regimeInsight?.headline || 'Use this space to validate how regime context and built-in presets change the strategy outcome.')}
                        </div>
                    </div>

                    {isScientificRecordSurface ? (
                        <ResearchScientificRecord
                            authToken={authToken}
                            isActive={isScientificRecordSurface && isActive}
                            isGuest={isGuest}
                            onLogEvent={onLogEvent}
                        />
                    ) : isPositiveHistorySurface ? (
                        <ResearchPositiveStrategiesPane
                            authToken={authToken}
                            isGuest={isGuest}
                        />
                    ) : (
                        <>
                    <div className='researchSummaryBanner'>
                        <div className='researchSummaryTitle'>Research center</div>
                        <div className='researchSummaryText'>
                            {regimeInsight?.headline || 'Use this space to validate how regime context and built-in presets change the strategy outcome.'}
                        </div>
                    </div>

                    <div className='researchDashboardGrid'>
                        <div className='researchDashboardCard'>
                            <span>Queued</span>
                            <strong>{Number(researchDashboard.counts.queued || 0)}</strong>
                        </div>
                        <div className='researchDashboardCard'>
                            <span>Paper</span>
                            <strong>{Number(researchDashboard.counts.paper || 0)}</strong>
                        </div>
                        <div className='researchDashboardCard'>
                            <span>Promoted</span>
                            <strong>{Number(researchDashboard.counts.promoted || 0)}</strong>
                        </div>
                        <div className='researchDashboardCard'>
                            <span>Paper verdicts</span>
                            <strong>{Number(researchDashboard.paperVerdictCount || 0)}</strong>
                        </div>
                        <div className='researchDashboardCard'>
                            <span>Ready for live review</span>
                            <strong>{Number(researchDashboard.readyForLiveReview || 0)}</strong>
                        </div>
                        <div className={`researchDashboardCard ${researchDashboard.walkforwardConcernCount ? 'warning' : ''}`}>
                            <span>Walk-forward concerns</span>
                            <strong>{Number(researchDashboard.walkforwardConcernCount || 0)}</strong>
                        </div>
                        <div className='researchDashboardCard'>
                            <span>High confidence</span>
                            <strong>{Number(researchDashboard.highConfidenceCount || 0)}</strong>
                        </div>
                        <div className={`researchDashboardCard ${activeResearchJobs.length ? 'warning' : ''}`}>
                            <span>Backend jobs</span>
                            <strong>{Number(activeResearchJobs.length || 0)}</strong>
                        </div>
                        <div className={`researchDashboardCard ${researchDashboard.autoRejectCount ? 'warning' : ''}`}>
                            <span>Auto rejects</span>
                            <strong>{Number(researchDashboard.autoRejectCount || 0)}</strong>
                        </div>
                        <div className='researchDashboardCard wide'>
                            <span>Best promotion score</span>
                            <strong>
                                {researchDashboard.bestCandidate
                                    ? `${formatValue(researchDashboard.bestCandidate.promotionScore, 1)} · ${researchDashboard.bestCandidate.label}`
                                    : '-'}
                            </strong>
                        </div>
                        <div className='researchDashboardCard alert'>
                            <span>Research alert</span>
                            <strong>{researchDashboard.alert}</strong>
                        </div>
                        <div className='researchDashboardCard wide'>
                            <span>Platform verdict</span>
                            <strong>{researchDashboard.platformVerdictDetail}</strong>
                        </div>
                    </div>

                    <div className='statisticsLayout researchLayout'>
                        <aside className='statisticsSidebar researchSidebar'>
                            <div className='statisticsSidebarTitle'>Sections</div>
                            <div className='statisticsList'>
                                {researchWorkbenchSections.map((section) => (
                                    <button
                                        key={section.id}
                                        type='button'
                                        className={`statisticsListButton ${activeTab === section.id ? 'active' : ''}`}
                                        onClick={() => setActiveTab(section.id)}
                                    >
                                        {section.title}
                                    </button>
                                ))}
                            </div>
                        </aside>

                        <div className='statisticsContent researchContent'>
                            <div className={`researchSectionPanel ${activeTab === 'overview' ? 'active' : ''}`} hidden={activeTab !== 'overview'}>
                                <RegimeSummaryPane
                                    regimeSummary={stats?.regime_summary || []}
                                    regimeStabilitySummary={stats?.regime_stability_summary || []}
                                    chartSettings={chartSettings}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'what-worked' ? 'active' : ''}`} hidden={activeTab !== 'what-worked'}>
                                <ResearchWhatWorkedPane />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'operations' ? 'active' : ''}`} hidden={activeTab !== 'operations'}>
                                <ResearchOperationsPane
                                    authToken={authToken}
                                    researchJobs={remoteResearchJobs}
                                    researchBatches={remoteResearchBatches}
                                    researchCampaigns={remoteResearchCampaigns}
                                    onLogEvent={onLogEvent}
                                    onRefreshJobs={refreshRemoteResearchJobs}
                                    onRefreshBatches={refreshRemoteResearchBatches}
                                    onRefreshCampaigns={refreshRemoteResearchCampaigns}
                                    onCancelJob={handleCancelRemoteJob}
                                    onCancelBatch={handleCancelRemoteBatch}
                                    onCreateBatch={handleCreateResearchBatch}
                                    onCreateCampaign={handleCreateResearchCampaign}
                                    onLaunchCampaign={handleLaunchResearchCampaign}
                                    onDeleteCampaign={handleDeleteResearchCampaign}
                                    onRerunJob={handleRerunRemoteJob}
                                    onSaveJobStrategy={handleSaveRemoteJobStrategy}
                                    onOpenArchivedRun={handleOpenArchivedRun}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'presets' ? 'active' : ''}`} hidden={activeTab !== 'presets'}>
                                <PresetComparisonPane
                                    authToken={authToken}
                                    chartSettings={chartSettings}
                                    strategyApplyResponse={strategyApplyResponse}
                                    onApplyStrategyPreset={handleApplyStrategyPreset}
                                    onOpenStrategy={onOpenStrategy}
                                    onLogEvent={onLogEvent}
                                    initialState={savedStudies?.preset_compare}
                                    onStudyComplete={handleStudyComplete}
                                    sharedConsoleJobs={sharedConsoleJobs}
                                    onSharedConsoleJobChange={onSharedConsoleJobChange}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'timeframes' ? 'active' : ''}`} hidden={activeTab !== 'timeframes'}>
                                <TimeframeStudyPane
                                    authToken={authToken}
                                    chartSettings={chartSettings}
                                    strategyApplyResponse={strategyApplyResponse}
                                    onLogEvent={onLogEvent}
                                    initialState={savedStudies?.timeframe_study}
                                    onStudyComplete={handleStudyComplete}
                                    sharedConsoleJobs={sharedConsoleJobs}
                                    onSharedConsoleJobChange={onSharedConsoleJobChange}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'symbols' ? 'active' : ''}`} hidden={activeTab !== 'symbols'}>
                                <SymbolStudyPane
                                    authToken={authToken}
                                    chartSettings={chartSettings}
                                    strategyApplyResponse={strategyApplyResponse}
                                    onLogEvent={onLogEvent}
                                    initialState={savedStudies?.symbol_study}
                                    onStudyComplete={handleStudyComplete}
                                    sharedConsoleJobs={sharedConsoleJobs}
                                    onSharedConsoleJobChange={onSharedConsoleJobChange}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'splits' ? 'active' : ''}`} hidden={activeTab !== 'splits'}>
                                <WalkForwardPane
                                    authToken={authToken}
                                    chartSettings={chartSettings}
                                    strategyApplyResponse={strategyApplyResponse}
                                    onLogEvent={onLogEvent}
                                    initialState={savedStudies?.walkforward_study}
                                    onStudyComplete={handleStudyComplete}
                                    sharedConsoleJobs={sharedConsoleJobs}
                                    onSharedConsoleJobChange={onSharedConsoleJobChange}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'promotion' ? 'active' : ''}`} hidden={activeTab !== 'promotion'}>
                                <PromotionCandidatesPane
                                    authToken={authToken}
                                    chartSettings={chartSettings}
                                    strategyApplyResponse={strategyApplyResponse}
                                    currentWorkspaceSaveName={currentWorkspaceSaveName}
                                    setStrategy={setStrategy}
                                    setStrategySetEntries={setStrategySetEntries}
                                    onOpenStrategy={onOpenStrategy}
                                    onLogEvent={onLogEvent}
                                    onAddToShortlist={handleAddToShortlist}
                                    initialState={savedStudies?.promotion_candidates}
                                    onStudyComplete={handleStudyComplete}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'strategies' ? 'active' : ''}`} hidden={activeTab !== 'strategies'}>
                                <StrategyComparePane
                                    authToken={authToken}
                                    chartSettings={chartSettings}
                                    strategyApplyResponse={strategyApplyResponse}
                                    shortlist={paperShortlist}
                                    benchmarkStrategies={activeBenchmarks}
                                    setStrategy={setStrategy}
                                    setStrategySetEntries={setStrategySetEntries}
                                    onOpenStrategy={onOpenStrategy}
                                    onLogEvent={onLogEvent}
                                    onAddBenchmark={handleAddBenchmark}
                                    onRemoveBenchmark={handleRemoveBenchmark}
                                    onUpdateBenchmark={handleUpdateBenchmark}
                                    initialState={savedStudies?.strategy_compare}
                                    onStudyComplete={handleStudyComplete}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'archive' ? 'active' : ''}`} hidden={activeTab !== 'archive'}>
                                <StudyArchivePane
                                    studyRuns={remoteStudyRuns.length ? remoteStudyRuns : studyRuns}
                                    selectedRunId={selectedArchiveRunId}
                                    onSelectedRunIdChange={setSelectedArchiveRunId}
                                    authToken={authToken}
                                    isRemoteSource={remoteStudyRuns.length > 0}
                                    onLoadRunDetail={loadRemoteStudyRunDetail}
                                    onLogEvent={onLogEvent}
                                    onDeleteRun={handleDeleteRemoteRun}
                                    onUpdateRun={handleUpdateRemoteRun}
                                    setStrategy={setStrategy}
                                    setStrategySetEntries={setStrategySetEntries}
                                    setBacktest={setBacktest}
                                    onOpenStrategy={onOpenStrategy}
                                    onHydrateBacktestResult={onHydrateBacktestResult}
                                    onOpenResults={onOpenResults}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'shortlist' ? 'active' : ''}`} hidden={activeTab !== 'shortlist'}>
                                <PaperShortlistPane
                                    shortlist={paperShortlist}
                                    decisionLog={decisionLog}
                                    onRemove={handleRemoveFromShortlist}
                                    onApply={handleApplyShortlistEntry}
                                    onCopy={handleCopyShortlist}
                                    onUpdate={handleUpdateShortlistEntry}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'review' ? 'active' : ''}`} hidden={activeTab !== 'review'}>
                                <PromotionReviewPane
                                    shortlist={paperShortlist}
                                    onApply={handleApplyShortlistEntry}
                                    onUpdate={handleUpdateShortlistEntry}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'live' ? 'active' : ''}`} hidden={activeTab !== 'live'}>
                                <LiveReadinessPane
                                    shortlist={paperShortlist}
                                    onApply={handleApplyShortlistEntry}
                                    onUpdate={handleUpdateShortlistEntry}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'failure' ? 'active' : ''}`} hidden={activeTab !== 'failure'}>
                                <FailureModesPane
                                    shortlist={paperShortlist}
                                    onUpdate={handleUpdateShortlistEntry}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'export' ? 'active' : ''}`} hidden={activeTab !== 'export'}>
                                <ResearchExportPane
                                    projectName={currentWorkspaceSaveName}
                                    dashboard={researchDashboard}
                                    regimeSummary={stats?.regime_summary || []}
                                    regimeStabilitySummary={stats?.regime_stability_summary || []}
                                    shortlist={paperShortlist}
                                    benchmarkStrategies={benchmarkStrategies}
                                    decisionLog={decisionLog}
                                    savedStudies={savedStudies}
                                    studyRuns={studyRuns}
                                    onLogEvent={onLogEvent}
                                />
                            </div>
                            <div className={`researchSectionPanel ${activeTab === 'playbook' ? 'active' : ''}`} hidden={activeTab !== 'playbook'}>
                                <ResearchPlaybookPane
                                    onLogEvent={onLogEvent}
                                />
                            </div>
                        </div>
                    </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    )
}

export function Results({
    isActive,
    backtestResponse,
    authToken,
    chartSettings,
    setStrategy,
    setStrategySetEntries = null,
    onOpenStrategy,
    onLogEvent,
    canLoadStoredCharts = false,
    onLoadStoredCharts = null,
    onResolveLoadedBacktestResponse = null,
}) {
    const strategyApplyResponse = backtestResponse
    const [selectedSeriesIdDraft, setSelectedSeriesIdDraft] = useState('account_balance_series')

    const stats = strategyApplyResponse?.stats || null
    const results = useMemo(() => strategyApplyResponse?.results || [], [strategyApplyResponse?.results])

    const seriesMap = useMemo(() => ({
        account_balance_series: {
            id: 'account_balance_series',
            label: 'Account balance',
            points: buildSeriesFromArray(pickFirstDefined(stats, ['account_balance_series', 'equity_curve'])),
        },
        drawdown_amount_series: {
            id: 'drawdown_amount_series',
            label: 'Drawdown amount',
            points: buildSeriesFromArray(pickFirstDefined(stats, ['drawdown_amount_series', 'drawdown_curve'])),
        },
        drawdown_pct_series: {
            id: 'drawdown_pct_series',
            label: 'Drawdown %',
            points: buildSeriesFromArray(pickFirstDefined(stats, ['drawdown_pct_series', 'drawdown_pct_curve'])),
        },
        trade_net_pnl: {
            id: 'trade_net_pnl',
            label: 'Trade net PnL',
            points: buildSeriesFromResultsFields(results, ['trade_net_pnl', 'net_result', 'results']),
        },
        trade_cost: {
            id: 'trade_cost',
            label: 'Trade cost',
            points: buildSeriesFromResultsFields(results, ['trade_cost', 'cost']),
        },
    }), [results, stats])

    const tabs = Object.values(seriesMap)
    const selectedSeriesId = seriesMap[selectedSeriesIdDraft]
        ? selectedSeriesIdDraft
        : 'account_balance_series'
    const selectedSeries = seriesMap[selectedSeriesId] || tabs[0]

    function handleApplyStrategyPreset(nextStrategy) {
        applyResearchStrategySelection({
            setStrategy,
            setStrategySetEntries,
            strategy: nextStrategy,
            strategies: [],
        })
    }

    return (
        <div className={`Results ${isActive ? 'active' : ''}`}>
            <div className='resultsPane resultsPaneLeft'>
                <div className='resultsTabs'>
                    {tabs.map((series) => (
                        <button
                            key={series.id}
                            type='button'
                            className={`resultsTab ${selectedSeriesId === series.id ? 'active' : ''}`}
                            onClick={() => setSelectedSeriesIdDraft(series.id)}
                        >
                            {series.label}
                        </button>
                    ))}
                </div>

                <SeriesChart
                    series={selectedSeries}
                    canLoadStoredCharts={canLoadStoredCharts}
                    onLoadStoredCharts={onLoadStoredCharts}
                />
            </div>

            <div className='resultsPane resultsPaneRight'>
                <StatisticsPane
                    request={strategyApplyResponse?.request || null}
                    backtest={strategyApplyResponse?.request?.backtest || null}
                    stats={stats}
                    results={results}
                    authToken={authToken}
                    chartSettings={chartSettings}
                    strategyApplyResponse={strategyApplyResponse}
                    onApplyStrategyPreset={handleApplyStrategyPreset}
                    onOpenStrategy={onOpenStrategy}
                    onLogEvent={onLogEvent}
                    onResolveLoadedBacktestResponse={onResolveLoadedBacktestResponse}
                />
            </div>
        </div>
    )
}

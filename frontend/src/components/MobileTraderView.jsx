import { useCallback, useEffect, useMemo, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '../api'
import { Chart } from './Chart'
import { normalizeChartSettings, normalizeIndicator } from '../utils/chartSettings.jsx'
import './MobileTraderView.css'

const MOBILE_CHART_SEED_BARS = 1000
const MOBILE_HISTORY_LIMIT = 180
const GUEST_RESTRICTION_MESSAGE = 'Guest demo can inspect Trader, but cannot arm the bot, evaluate, process intents, reconcile, or reset broker commands.'
const TOKEN_REGEX = /\b([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]/g
const LITERAL_TOKEN_REGEX = /\b(True|False|and|or)\b/g

const MOBILE_TRADER_SCREENS = [
    { id: 'overview', label: 'Overview', description: 'Portfolio scope, chart switcher and consolidated live stats.' },
    { id: 'controls', label: 'Runtime controls', description: 'Arm, dispatch, evaluate, reconcile and queue recovery.' },
    { id: 'summary', label: 'Runtime summary', description: 'Execution mode, feed state and runtime session metadata.' },
    { id: 'markets', label: 'Portfolio scope', description: 'Pipeline and strategy breakdown for the selected portfolio.' },
    { id: 'strategies', label: 'Active strategies', description: 'Runtime sleeves grouped by pipeline with per-strategy results.' },
    { id: 'pipeline', label: 'Decision pipeline', description: 'Queued intents and broker commands for the current data scope.' },
    { id: 'operations', label: 'Recent operations', description: 'Open and closed trades for the current portfolio, pipeline or strategy scope.' },
]

function toFiniteNumberOrNull(value) {
    const numeric = Number(value)
    return Number.isFinite(numeric) ? numeric : null
}

function normalizeUnixTimestamp(value) {
    const numeric = toFiniteNumberOrNull(value)
    if (numeric === null || numeric <= 0) {
        return null
    }
    return Math.trunc(numeric)
}

function normalizeTradeMarketValue(value, fallback = '') {
    return String(value || fallback || '').trim().toUpperCase()
}

function formatDateTimeBr24(value) {
    const numeric = normalizeUnixTimestamp(value)
    if (!numeric) {
        return '—'
    }

    try {
        const date = new Date(numeric * 1000)
        const day = String(date.getDate()).padStart(2, '0')
        const month = String(date.getMonth() + 1).padStart(2, '0')
        const year = String(date.getFullYear())
        const hours = String(date.getHours()).padStart(2, '0')
        const minutes = String(date.getMinutes()).padStart(2, '0')
        const seconds = String(date.getSeconds()).padStart(2, '0')
        return `${day}/${month}/${year} ${hours}:${minutes}:${seconds}`
    } catch {
        return '—'
    }
}

function formatRelativeTimestamp(value) {
    const numeric = normalizeUnixTimestamp(value)
    if (!numeric) {
        return '—'
    }

    const elapsedSeconds = Math.max(0, Math.floor(Date.now() / 1000) - numeric)
    if (elapsedSeconds < 60) {
        return `${elapsedSeconds}s ago`
    }
    if (elapsedSeconds < 3600) {
        return `${Math.floor(elapsedSeconds / 60)}m ago`
    }
    if (elapsedSeconds < 86400) {
        return `${Math.floor(elapsedSeconds / 3600)}h ago`
    }
    return `${Math.floor(elapsedSeconds / 86400)}d ago`
}

function formatPrice(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '—'
    }
    return numeric.toFixed(5)
}

function formatSignedMoney(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '0.00'
    }
    if (numeric > 0) {
        return `+${numeric.toFixed(2)}`
    }
    return numeric.toFixed(2)
}

function formatPercent(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '0.0%'
    }
    return `${(numeric * 100).toFixed(1)}%`
}

function formatSignedPercent(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '0.0%'
    }
    const sign = numeric > 0 ? '+' : ''
    return `${sign}${(numeric * 100).toFixed(1)}%`
}

function formatVolumeLabel(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return ''
    }
    return numeric.toFixed(2)
}

function parseExpressionParts(value) {
    const text = String(value ?? '')
    const indexedMatches = Array.from(text.matchAll(TOKEN_REGEX)).map((match) => ({
        type: 'token',
        tokenType: 'indexed',
        raw: match[0],
        name: match[1],
        index: Number(match[2]),
        start: match.index ?? 0,
        end: (match.index ?? 0) + match[0].length,
    }))
    const literalMatches = Array.from(text.matchAll(LITERAL_TOKEN_REGEX))
        .map((match) => ({
            type: 'token',
            tokenType: 'literal',
            raw: match[0],
            name: match[1],
            index: null,
            start: match.index ?? 0,
            end: (match.index ?? 0) + match[0].length,
        }))
        .filter((match) => !indexedMatches.some((indexed) => (
            match.start >= indexed.start && match.end <= indexed.end
        )))

    const tokenMatches = [...indexedMatches, ...literalMatches].sort((a, b) => a.start - b.start)
    const parts = []
    let cursor = 0

    for (const match of tokenMatches) {
        if (match.start > cursor) {
            parts.push({
                type: 'text',
                value: text.slice(cursor, match.start),
                start: cursor,
                end: match.start,
            })
        }

        parts.push(match)
        cursor = match.end
    }

    if (cursor < text.length || parts.length === 0) {
        parts.push({
            type: 'text',
            value: text.slice(cursor),
            start: cursor,
            end: text.length,
        })
    }

    return parts
}

function MobileReadOnlyExpression({ value = '', singleLine = false }) {
    const parts = parseExpressionParts(value)
    return (
        <div className={`mobileTraderExpressionPreview ${singleLine ? 'singleLine' : ''}`.trim()}>
            <div className='mobileTraderExpressionPreviewText'>
                {parts.map((part, index) => (
                    part.type === 'token'
                        ? (
                            <span
                                key={`token-${part.start}-${index}`}
                                className={`mobileTraderStrategyToken ${part.tokenType === 'literal' ? 'isLiteral' : ''}`.trim()}
                                title={part.raw}
                            >
                                <span className='mobileTraderStrategyTokenLabel'>{part.name}</span>
                                {part.tokenType !== 'literal' ? (
                                    <span className='mobileTraderStrategyTokenIndex'>[{part.index}]</span>
                                ) : null}
                            </span>
                        )
                        : (
                            <span key={`text-${part.start}-${index}`}>{part.value || (index === 0 ? ' ' : '')}</span>
                        )
                ))}
            </div>
        </div>
    )
}

function MobileTradeStrategyReadOnly({ strategy = null }) {
    const safeStrategy = strategy && typeof strategy === 'object' ? strategy : {}
    const sections = [
        {
            id: 'long',
            title: 'Long',
            fields: [
                ['Open price', safeStrategy?.long?.openPrice || ''],
                ['Close price', safeStrategy?.long?.closePrice || ''],
                ['Open if', safeStrategy?.long?.openIf || ''],
                ['Close if', safeStrategy?.long?.closeIf || ''],
                ['Gain price', safeStrategy?.long?.gainPrice || ''],
                ['Loss price', safeStrategy?.long?.lossPrice || ''],
                ['Trailing price', safeStrategy?.long?.trailingPrice || ''],
            ],
        },
        {
            id: 'short',
            title: 'Short',
            fields: [
                ['Open price', safeStrategy?.short?.openPrice || ''],
                ['Close price', safeStrategy?.short?.closePrice || ''],
                ['Open if', safeStrategy?.short?.openIf || ''],
                ['Close if', safeStrategy?.short?.closeIf || ''],
                ['Gain price', safeStrategy?.short?.gainPrice || ''],
                ['Loss price', safeStrategy?.short?.lossPrice || ''],
                ['Trailing price', safeStrategy?.short?.trailingPrice || ''],
            ],
        },
    ]

    return (
        <div className='mobileTraderStrategyReadOnly'>
            <div className='mobileTraderStrategyMeta'>
                <div className='mobileTraderStrategyMetaItem'>
                    <strong>Allow inversion</strong>
                    <span>{safeStrategy?.other?.allowInversion ? 'True' : 'False'}</span>
                </div>
                <div className='mobileTraderStrategyMetaItem'>
                    <strong>Priority</strong>
                    <span>{safeStrategy?.other?.priority || 'Short'}</span>
                </div>
            </div>
            <div className='mobileTraderStrategySections'>
                {sections.map((section) => (
                    <section key={section.id} className='mobileTraderStrategySection'>
                        <div className='mobileTraderStrategySectionTitle'>{section.title}</div>
                        <div className='mobileTraderStrategyFieldList'>
                            {section.fields.map(([label, fieldValue]) => (
                                <div key={`${section.id}-${label}`} className='mobileTraderStrategyFieldItem'>
                                    <div className='mobileTraderStrategyFieldLabel'>{label}</div>
                                    <MobileReadOnlyExpression value={fieldValue} singleLine={label.includes('price')} />
                                </div>
                            ))}
                        </div>
                    </section>
                ))}
            </div>
        </div>
    )
}

function normalizeRuntimePayload(payload) {
    if (payload?.trade_runtime && typeof payload.trade_runtime === 'object') {
        return payload.trade_runtime
    }
    return payload && typeof payload === 'object' ? payload : {}
}

function resolveEffectiveRuntime(liveRuntime, serverHealth) {
    const live = normalizeRuntimePayload(liveRuntime)
    const healthRuntime = normalizeRuntimePayload(serverHealth?.trade_runtime || {})
    const hasLiveSurface = (
        Array.isArray(live?.sleeves)
        || (live?.sleeve_states && typeof live.sleeve_states === 'object')
        || Array.isArray(live?.order_intents)
        || Array.isArray(live?.order_commands)
        || typeof live?.armed === 'boolean'
        || typeof live?.status === 'string'
    )
    if (hasLiveSurface) {
        return {
            ...healthRuntime,
            ...live,
        }
    }
    return healthRuntime
}

function normalizeIndicatorList(indicators) {
    return Array.isArray(indicators)
        ? indicators.map((indicator) => normalizeIndicator(indicator))
        : []
}

function buildRuntimeSource(runtimeLike, tradeState) {
    const liveSleeves = Array.isArray(runtimeLike?.sleeves) ? runtimeLike.sleeves.filter((entry) => entry && typeof entry === 'object') : []
    if (liveSleeves.length) {
        return runtimeLike
    }
    return tradeState && typeof tradeState === 'object' ? tradeState : runtimeLike
}

function normalizeScopeKey(value, fallback = '') {
    return String(value ?? fallback ?? '').trim()
}

function getEntrySleeveId(entry = {}) {
    return normalizeScopeKey(entry?.sleeve_id ?? entry?.id)
}

function getEntryPortfolioId(entry = {}) {
    return normalizeScopeKey(entry?.portfolio_id ?? entry?.portfolioId)
}

function getEntryPortfolioLabel(entry = {}) {
    return normalizeScopeKey(entry?.portfolio_label ?? entry?.portfolioLabel)
}

function getEntryPipelineId(entry = {}) {
    return normalizeScopeKey(entry?.pipeline_id ?? entry?.pipelineId)
}

function getEntryPipelineLabel(entry = {}) {
    return normalizeScopeKey(entry?.pipeline_label ?? entry?.pipelineLabel)
}

function getEntrySourceStrategyId(entry = {}) {
    return normalizeScopeKey(entry?.source_strategy_id ?? entry?.sourceStrategyId)
}

function getEntryLabel(entry = {}) {
    return normalizeScopeKey(
        entry?.label
        || entry?.sleeve_label
        || entry?.strategyName
        || entry?.strategy_name
        || entry?.sourceStrategyLabel
        || entry?.source_strategy_label
        || entry?.sourceStrategyId
        || entry?.source_strategy_id
    )
}

function getEntryStrategy(entry = {}) {
    return entry?.strategy && typeof entry.strategy === 'object' ? entry.strategy : null
}

function getEntryIndicators(entry = {}) {
    const strategy = getEntryStrategy(entry)
    return Array.isArray(entry?.indicators) && entry.indicators.length
        ? entry.indicators
        : strategy?.featureManifest?.indicators
}

function mergeRuntimeEntry(base = {}, patch = {}) {
    const next = { ...(base && typeof base === 'object' ? base : {}) }
    for (const [key, value] of Object.entries(patch && typeof patch === 'object' ? patch : {})) {
        if (value === undefined || value === null) {
            continue
        }
        if (typeof value === 'string' && !value.trim() && typeof next[key] === 'string' && next[key].trim()) {
            continue
        }
        if (Array.isArray(value) && !value.length && Array.isArray(next[key]) && next[key].length) {
            continue
        }
        next[key] = value
    }
    return next
}

function buildRuntimeEntryKey(entry = {}) {
    const sleeveId = getEntrySleeveId(entry)
    if (sleeveId) {
        return sleeveId
    }
    return [
        getEntryPortfolioId(entry),
        getEntryPipelineId(entry),
        normalizeTradeMarketValue(entry?.symbol),
        normalizeTradeMarketValue(entry?.timeframe),
        getEntrySourceStrategyId(entry),
        getEntryLabel(entry),
    ].join('|')
}

function collectRuntimeSleeveEntries(runtimeLike) {
    const sleeveStates = runtimeLike?.sleeve_states && typeof runtimeLike.sleeve_states === 'object'
        ? Object.values(runtimeLike.sleeve_states).filter((entry) => entry && typeof entry === 'object')
        : []
    const registry = new Map()
    for (const entry of Array.isArray(runtimeLike?.sleeves) ? runtimeLike.sleeves : []) {
        const key = buildRuntimeEntryKey(entry)
        if (!key) {
            continue
        }
        registry.set(key, mergeRuntimeEntry(registry.get(key), entry))
    }
    for (const entry of sleeveStates) {
        const key = buildRuntimeEntryKey(entry)
        if (!key) {
            continue
        }
        registry.set(key, mergeRuntimeEntry(registry.get(key), entry))
    }
    return Array.from(registry.values())
}

function buildRuntimeTargetMatcher(target = null) {
    const safeTarget = target && typeof target === 'object' ? target : {}
    const symbol = normalizeTradeMarketValue(safeTarget.symbol)
    const timeframe = normalizeTradeMarketValue(safeTarget.timeframe)
    const sleeveId = normalizeScopeKey(safeTarget.sleeveId ?? safeTarget.sleeve_id ?? safeTarget.id)
    const portfolioId = normalizeScopeKey(safeTarget.portfolioId ?? safeTarget.portfolio_id)
    const pipelineId = normalizeScopeKey(safeTarget.pipelineId ?? safeTarget.pipeline_id)

    return (entry) => {
        if (!entry || typeof entry !== 'object') {
            return false
        }
        if (sleeveId && getEntrySleeveId(entry) !== sleeveId) {
            return false
        }
        if (portfolioId && getEntryPortfolioId(entry) !== portfolioId) {
            return false
        }
        if (pipelineId && getEntryPipelineId(entry) !== pipelineId) {
            return false
        }
        if (symbol && normalizeTradeMarketValue(entry?.symbol) !== symbol) {
            return false
        }
        if (timeframe && normalizeTradeMarketValue(entry?.timeframe) !== timeframe) {
            return false
        }
        return true
    }
}

function buildMobilePortfolioModel(runtimeLike, tradeState, fallbackChartSettings = null) {
    const runtimeSource = buildRuntimeSource(runtimeLike, tradeState)
    const runtimePortfolios = Array.isArray(runtimeSource?.portfolios)
        ? runtimeSource.portfolios.filter((entry) => entry && typeof entry === 'object')
        : []
    const sleeveEntries = collectRuntimeSleeveEntries(runtimeSource)
    const portfolioRegistry = new Map()
    const chartTargetRegistry = new Map()

    const ensurePortfolio = (payload = {}, orderHint = portfolioRegistry.size) => {
        const id = normalizeScopeKey(payload?.id, `portfolio-${orderHint + 1}`)
        const label = normalizeScopeKey(payload?.label, `Portfolio ${orderHint + 1}`)
        const current = portfolioRegistry.get(id) || {
            key: id,
            id,
            label,
            order: orderHint,
            pipelines: [],
            chartTargets: [],
            pipelineMap: new Map(),
        }
        if (label) {
            current.label = label
        }
        current.order = Math.min(Number(current.order ?? orderHint), orderHint)
        portfolioRegistry.set(id, current)
        return current
    }

    const ensurePipeline = (portfolioEntry, payload = {}, orderHint = portfolioEntry.pipelines.length) => {
        const id = normalizeScopeKey(payload?.id, `${portfolioEntry.id}-pipeline-${orderHint + 1}`)
        const label = normalizeScopeKey(payload?.label, `Pipeline ${orderHint + 1}`)
        const existing = portfolioEntry.pipelineMap.get(id) || {
            key: id,
            id,
            label,
            portfolioId: portfolioEntry.id,
            portfolioLabel: portfolioEntry.label,
            order: orderHint,
            chartTargets: [],
        }
        if (label) {
            existing.label = label
        }
        existing.portfolioLabel = portfolioEntry.label
        existing.order = Math.min(Number(existing.order ?? orderHint), orderHint)
        if (!portfolioEntry.pipelineMap.has(id)) {
            portfolioEntry.pipelineMap.set(id, existing)
            portfolioEntry.pipelines.push(existing)
        }
        return existing
    }

    runtimePortfolios.forEach((portfolio, portfolioIndex) => {
        const portfolioEntry = ensurePortfolio(portfolio, portfolioIndex)
        const pipelines = Array.isArray(portfolio?.pipelines)
            ? portfolio.pipelines.filter((entry) => entry && typeof entry === 'object')
            : []
        pipelines.forEach((pipeline, pipelineIndex) => {
            ensurePipeline(portfolioEntry, pipeline, pipelineIndex)
        })
    })

    for (const entry of sleeveEntries) {
        const symbol = normalizeTradeMarketValue(entry?.symbol, fallbackChartSettings?.symbol)
        const timeframe = normalizeTradeMarketValue(entry?.timeframe, fallbackChartSettings?.timeframe || 'M1')
        if (!symbol || !timeframe) {
            continue
        }
        const portfolioEntry = ensurePortfolio({
            id: getEntryPortfolioId(entry) || 'legacy-default',
            label: getEntryPortfolioLabel(entry) || 'Runtime portfolio',
        })
        const pipelineEntry = ensurePipeline(portfolioEntry, {
            id: getEntryPipelineId(entry) || `${portfolioEntry.id}-pipeline-1`,
            label: getEntryPipelineLabel(entry) || 'Primary pipeline',
        })
        const sleeveId = getEntrySleeveId(entry) || `${portfolioEntry.id}|${pipelineEntry.id}|${symbol}|${timeframe}|${getEntrySourceStrategyId(entry) || getEntryLabel(entry)}`
        const label = getEntryLabel(entry) || `${symbol} · ${timeframe}`
        const chartTarget = {
            key: sleeveId,
            id: sleeveId,
            sleeveId,
            label,
            chartLabel: `${label} · ${symbol} · ${timeframe}`,
            detail: `${symbol} · ${timeframe}`,
            symbol,
            timeframe,
            marketKey: `${symbol}::${timeframe}`,
            indicators: normalizeIndicatorList(getEntryIndicators(entry)),
            volume: toFiniteNumberOrNull(entry?.volume),
            enabled: entry?.enabled !== false,
            portfolioId: portfolioEntry.id,
            portfolioLabel: portfolioEntry.label,
            pipelineId: pipelineEntry.id,
            pipelineLabel: pipelineEntry.label,
            sourceStrategyId: getEntrySourceStrategyId(entry),
            strategy: getEntryStrategy(entry),
        }

        if (!chartTargetRegistry.has(chartTarget.key)) {
            chartTargetRegistry.set(chartTarget.key, chartTarget)
            portfolioEntry.chartTargets.push(chartTarget)
            pipelineEntry.chartTargets.push(chartTarget)
        }
    }

    if (!chartTargetRegistry.size) {
        const fallbackSymbol = normalizeTradeMarketValue(fallbackChartSettings?.symbol)
        const fallbackTimeframe = normalizeTradeMarketValue(fallbackChartSettings?.timeframe, 'M1')
        if (fallbackSymbol && fallbackTimeframe) {
            const portfolioEntry = ensurePortfolio({ id: 'mobile-fallback', label: 'Focused chart' }, 0)
            const pipelineEntry = ensurePipeline(portfolioEntry, { id: 'mobile-fallback-pipeline', label: 'Chart' }, 0)
            const chartTarget = {
                key: `${fallbackSymbol}|${fallbackTimeframe}|mobile-fallback`,
                id: `${fallbackSymbol}|${fallbackTimeframe}|mobile-fallback`,
                sleeveId: '',
                label: `${fallbackSymbol} · ${fallbackTimeframe}`,
                chartLabel: `${fallbackSymbol} · ${fallbackTimeframe}`,
                detail: `${fallbackSymbol} · ${fallbackTimeframe}`,
                symbol: fallbackSymbol,
                timeframe: fallbackTimeframe,
                marketKey: `${fallbackSymbol}::${fallbackTimeframe}`,
                indicators: [],
                volume: null,
                enabled: true,
                portfolioId: portfolioEntry.id,
                portfolioLabel: portfolioEntry.label,
                pipelineId: pipelineEntry.id,
                pipelineLabel: pipelineEntry.label,
                sourceStrategyId: '',
                strategy: null,
            }
            chartTargetRegistry.set(chartTarget.key, chartTarget)
            portfolioEntry.chartTargets.push(chartTarget)
            pipelineEntry.chartTargets.push(chartTarget)
        }
    }

    const portfolios = Array.from(portfolioRegistry.values())
        .map((portfolio) => ({
            key: portfolio.key,
            id: portfolio.id,
            label: portfolio.label,
            order: portfolio.order,
            strategyCount: portfolio.chartTargets.length,
            chartTargets: portfolio.chartTargets.slice().sort((left, right) => left.chartLabel.localeCompare(right.chartLabel)),
            pipelines: portfolio.pipelines
                .slice()
                .sort((left, right) => Number(left.order ?? 0) - Number(right.order ?? 0))
                .map((pipeline) => ({
                    key: pipeline.key,
                    id: pipeline.id,
                    label: pipeline.label,
                    portfolioId: pipeline.portfolioId,
                    portfolioLabel: pipeline.portfolioLabel,
                    order: pipeline.order,
                    strategyCount: pipeline.chartTargets.length,
                    chartTargets: pipeline.chartTargets.slice().sort((left, right) => left.chartLabel.localeCompare(right.chartLabel)),
                })),
        }))
        .filter((portfolio) => portfolio.chartTargets.length)
        .sort((left, right) => Number(left.order ?? 0) - Number(right.order ?? 0))

    return {
        portfolios,
        chartTargets: portfolios.flatMap((portfolio) => portfolio.chartTargets),
    }
}

function buildMobileChartSettings(baseChartSettings, selectedMarket, showIndicators = false) {
    const base = baseChartSettings && typeof baseChartSettings === 'object' ? baseChartSettings : {}
    const safeMarket = selectedMarket && typeof selectedMarket === 'object' ? selectedMarket : {}
    const seedBars = Math.min(
        Math.max(1, Number(base?.bars || MOBILE_CHART_SEED_BARS) || MOBILE_CHART_SEED_BARS),
        MOBILE_CHART_SEED_BARS,
    )

    return normalizeChartSettings({
        ...base,
        bars: seedBars,
        symbol: normalizeTradeMarketValue(safeMarket.symbol, base?.symbol || 'EURUSD'),
        timeframe: normalizeTradeMarketValue(safeMarket.timeframe, base?.timeframe || 'M1'),
        indicators: showIndicators ? normalizeIndicatorList(safeMarket.indicators) : [],
    })
}

function buildTradeRuntimeChartMarkers(runtime, chartSettings, target = null) {
    if (!runtime || typeof runtime !== 'object' || !runtime.armed) {
        return []
    }

    const chartSymbol = String(chartSettings?.symbol || '').trim().toUpperCase()
    const chartTimeframe = String(chartSettings?.timeframe || '').trim().toUpperCase()
    const matchesTarget = buildRuntimeTargetMatcher(target)
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
        return matchesTarget(entry)
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
            const time = normalizeUnixTimestamp(entry?.bar_time)
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
            })
        })

    commands
        .filter((entry) => entry && matchesChart(entry))
        .forEach((entry) => {
            const time = normalizeUnixTimestamp(entry?.bar_time)
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
                ? visual.shape
                : status === 'rejected'
                    ? 'circle'
                    : 'square'

            pushMarker({
                id: `trade-command-${entry?.id || `${entry?.sleeve_id}-${action}-${side}-${time}`}`,
                time,
                position: visual.position,
                shape,
                color,
                text: `${sleeveLabel} ${action} ${side} | ${status}`.trim(),
                size: status === 'filled' ? 1 : 0.8,
            })
        })

    return markers.sort((left, right) => Number(left?.time || 0) - Number(right?.time || 0))
}

function buildTradeHistoryView(rows = [], sleeveStates = []) {
    const filledRows = (Array.isArray(rows) ? rows : [])
        .filter((entry) => String(entry?.status || '').trim().toLowerCase() === 'filled')
        .slice()
        .sort((left, right) => {
            const leftTime = Number(left?.filled_at || left?.created_at || left?.record_created_at || 0)
            const rightTime = Number(right?.filled_at || right?.created_at || right?.record_created_at || 0)
            return leftTime - rightTime
        })

    const rowsWithCycle = filledRows.filter((entry) => String(entry?.cycle_id || '').trim())
    const rowsWithoutCycle = filledRows.filter((entry) => !String(entry?.cycle_id || '').trim())
    const consolidated = []

    const readRealizedPnl = (entry) => Number(entry?.profit || 0) + Number(entry?.commission || 0) + Number(entry?.swap || 0)

    const cycleGroups = new Map()
    for (const entry of rowsWithCycle) {
        const cycleId = String(entry?.cycle_id || '').trim()
        if (!cycleId) {
            continue
        }
        const group = cycleGroups.get(cycleId) || { entries: [], openEntry: null, closeEntry: null }
        group.entries.push(entry)
        const action = String(entry?.action || '').trim().toLowerCase()
        if (action === 'open' && !group.openEntry) {
            group.openEntry = entry
        } else if (action === 'close') {
            group.closeEntry = entry
        }
        cycleGroups.set(cycleId, group)
    }

    const liveCycleIds = new Set(
        (Array.isArray(sleeveStates) ? sleeveStates : [])
            .map((entry) => String(entry?.current_cycle_id || '').trim())
            .filter(Boolean)
    )

    for (const [cycleId, group] of cycleGroups.entries()) {
        const openingEntry = group.openEntry || group.entries.find((entry) => String(entry?.action || '').trim().toLowerCase() === 'open') || null
        const closingEntry = group.closeEntry || group.entries.find((entry) => String(entry?.action || '').trim().toLowerCase() === 'close') || null
        const baseEntry = closingEntry || openingEntry || group.entries[group.entries.length - 1] || {}
        const isOpen = !closingEntry
        const syncStatus = isOpen
            ? (liveCycleIds.has(cycleId) ? 'confirmed' : 'unconfirmed')
            : ''

        consolidated.push({
            id: `${isOpen ? 'open' : 'closed'}-${cycleId}`,
            cycleId,
            state: isOpen ? 'open' : 'closed',
            syncStatus,
            strategyLabel: baseEntry?.sleeve_label || baseEntry?.source_strategy_id || '—',
            symbol: baseEntry?.symbol || '—',
            timeframe: baseEntry?.timeframe || '—',
            side: baseEntry?.side || openingEntry?.side || '—',
            brokerPositionTicket: baseEntry?.broker_position_ticket || openingEntry?.broker_position_ticket || closingEntry?.broker_position_ticket || null,
            volume: baseEntry?.fill_volume ?? openingEntry?.fill_volume ?? closingEntry?.fill_volume ?? null,
            entryTime: openingEntry?.filled_at || openingEntry?.created_at || null,
            exitTime: closingEntry?.filled_at || closingEntry?.created_at || null,
            entryPrice: openingEntry?.fill_price ?? null,
            exitPrice: closingEntry?.fill_price ?? null,
            pnl: closingEntry ? readRealizedPnl(closingEntry) : null,
        })
    }

    const openQueues = new Map()

    const getQueueKey = (entry) => [
        String(entry?.sleeve_id || ''),
        String(entry?.symbol || ''),
        String(entry?.timeframe || ''),
        String(entry?.side || ''),
    ].join('|')

    for (const entry of rowsWithoutCycle) {
        const action = String(entry?.action || '').trim().toLowerCase()
        const queueKey = getQueueKey(entry)

        if (action === 'open') {
            const queue = openQueues.get(queueKey) || []
            queue.push(entry)
            openQueues.set(queueKey, queue)
            continue
        }

        if (action === 'close') {
            const queue = openQueues.get(queueKey) || []
            const openingEntry = queue.length ? queue.shift() : null
            if (!queue.length) {
                openQueues.delete(queueKey)
            } else {
                openQueues.set(queueKey, queue)
            }

            consolidated.push({
                id: `closed-${entry?.id || entry?.command_id || Math.random().toString(36).slice(2, 8)}`,
                state: 'closed',
                strategyLabel: entry?.sleeve_label || entry?.source_strategy_id || openingEntry?.sleeve_label || openingEntry?.source_strategy_id || '—',
                symbol: entry?.symbol || openingEntry?.symbol || '—',
                timeframe: entry?.timeframe || openingEntry?.timeframe || '—',
                side: entry?.side || openingEntry?.side || '—',
                brokerPositionTicket: entry?.broker_position_ticket || openingEntry?.broker_position_ticket || null,
                volume: entry?.fill_volume ?? openingEntry?.fill_volume ?? null,
                entryTime: openingEntry?.filled_at || openingEntry?.created_at || null,
                exitTime: entry?.filled_at || entry?.created_at || null,
                entryPrice: openingEntry?.fill_price ?? null,
                exitPrice: entry?.fill_price ?? null,
                pnl: readRealizedPnl(entry),
            })
        }
    }

    const openBrokerKeys = new Set(
        (Array.isArray(sleeveStates) ? sleeveStates : [])
            .map((entry) => {
                if (String(entry?.current_cycle_id || '').trim()) {
                    return ''
                }
                const side = String(entry?.broker_position_side || '').trim().toLowerCase()
                if (side !== 'long' && side !== 'short') {
                    return ''
                }
                return [
                    String(entry?.sleeve_id || ''),
                    String(entry?.symbol || ''),
                    String(entry?.timeframe || ''),
                    side,
                ].join('|')
            })
            .filter(Boolean)
    )

    for (const [queueKey, queue] of openQueues.entries()) {
        if (!queue.length) {
            continue
        }
        const entry = queue[queue.length - 1]
        const syncStatus = openBrokerKeys.has(queueKey) ? 'confirmed' : 'unconfirmed'
        consolidated.push({
            id: `open-${entry?.id || entry?.command_id || Math.random().toString(36).slice(2, 8)}`,
            state: 'open',
            syncStatus,
            strategyLabel: entry?.sleeve_label || entry?.source_strategy_id || '—',
            symbol: entry?.symbol || '—',
            timeframe: entry?.timeframe || '—',
            side: entry?.side || '—',
            brokerPositionTicket: entry?.broker_position_ticket || null,
            volume: entry?.fill_volume ?? null,
            entryTime: entry?.filled_at || entry?.created_at || null,
            exitTime: null,
            entryPrice: entry?.fill_price ?? null,
            exitPrice: null,
            pnl: null,
        })
    }

    consolidated.sort((left, right) => {
        const leftIsOpen = left?.state === 'open'
        const rightIsOpen = right?.state === 'open'
        if (leftIsOpen !== rightIsOpen) {
            return leftIsOpen ? -1 : 1
        }

        const leftTime = Number(left.exitTime || left.entryTime || 0)
        const rightTime = Number(right.exitTime || right.entryTime || 0)
        return rightTime - leftTime
    })

    const closedRows = consolidated.filter((entry) => entry.state === 'closed')
    const openRows = consolidated.filter((entry) => entry.state === 'open')
    const confirmedOpenRows = openRows.filter((entry) => String(entry?.syncStatus || '').trim().toLowerCase() === 'confirmed')
    const unconfirmedOpenRows = openRows.filter((entry) => String(entry?.syncStatus || '').trim().toLowerCase() !== 'confirmed')
    const winningRows = closedRows.filter((entry) => Number(entry.pnl || 0) > 0)
    const realizedPnl = closedRows.reduce((sum, entry) => sum + Number(entry.pnl || 0), 0)

    return {
        rows: consolidated,
        summary: {
            tradeCount: consolidated.length,
            closedCount: closedRows.length,
            openCount: openRows.length,
            confirmedOpenCount: confirmedOpenRows.length,
            unconfirmedOpenCount: unconfirmedOpenRows.length,
            winCount: winningRows.length,
            winRate: closedRows.length ? winningRows.length / closedRows.length : 0,
            realizedPnl,
        },
    }
}

function annotateLiveTradeRows(rows = [], chartTargets = []) {
    const bySleeveId = new Map()
    const byStrategyMarket = new Map()

    for (const target of Array.isArray(chartTargets) ? chartTargets : []) {
        if (!target || typeof target !== 'object') {
            continue
        }
        if (normalizeScopeKey(target.sleeveId)) {
            bySleeveId.set(normalizeScopeKey(target.sleeveId), target)
        }
        const sourceStrategyId = normalizeScopeKey(target.sourceStrategyId)
        if (sourceStrategyId && target.marketKey) {
            byStrategyMarket.set(`${sourceStrategyId}|${target.marketKey}`, target)
        }
    }

    return (Array.isArray(rows) ? rows : []).map((entry) => {
        const safeEntry = entry && typeof entry === 'object' ? entry : {}
        const sleeveId = getEntrySleeveId(safeEntry)
        const marketKey = `${normalizeTradeMarketValue(safeEntry?.symbol)}::${normalizeTradeMarketValue(safeEntry?.timeframe)}`
        const sourceStrategyId = getEntrySourceStrategyId(safeEntry)
        const match = bySleeveId.get(sleeveId) || byStrategyMarket.get(`${sourceStrategyId}|${marketKey}`) || null

        return {
            ...safeEntry,
            sleeve_id: sleeveId || match?.sleeveId || '',
            sleeve_label: normalizeScopeKey(safeEntry?.sleeve_label, match?.label || sourceStrategyId || '—'),
            source_strategy_id: sourceStrategyId || match?.sourceStrategyId || '',
            portfolio_id: getEntryPortfolioId(safeEntry) || match?.portfolioId || '',
            portfolio_label: getEntryPortfolioLabel(safeEntry) || match?.portfolioLabel || '',
            pipeline_id: getEntryPipelineId(safeEntry) || match?.pipelineId || '',
            pipeline_label: getEntryPipelineLabel(safeEntry) || match?.pipelineLabel || '',
            chart_target_key: match?.key || '',
            strategy_scope_key: match?.key || sleeveId || '',
            market_key: marketKey,
        }
    })
}

function filterRowsByMarket(rows = [], market = null) {
    const matchesTarget = buildRuntimeTargetMatcher(market)
    return (Array.isArray(rows) ? rows : []).filter((entry) => matchesTarget(entry))
}

function filterSleeveStatesByMarket(runtime, market = null) {
    const matchesTarget = buildRuntimeTargetMatcher(market)
    const sleeveStates = collectRuntimeSleeveEntries(runtime)
    return sleeveStates.filter((entry) => matchesTarget(entry))
}

function normalizeSignalLabel(value) {
    const normalized = String(value || '').trim().toLowerCase()
    if (!normalized || normalized === 'hold' || normalized === 'flat') {
        return { label: 'HOLD', tone: 'neutral' }
    }
    if (['buy', 'long', 'open_long', 'open-buy'].includes(normalized)) {
        return { label: 'BUY', tone: 'buy' }
    }
    if (['sell', 'short', 'open_short', 'open-sell'].includes(normalized)) {
        return { label: 'SELL', tone: 'sell' }
    }
    if (normalized.startsWith('close')) {
        return { label: 'EXIT', tone: 'warning' }
    }
    if (normalized.includes('blocked') || normalized.includes('reject') || normalized.includes('stale')) {
        return { label: 'BLOCKED', tone: 'warning' }
    }
    return {
        label: normalized.replace(/_/g, ' ').toUpperCase(),
        tone: 'neutral',
    }
}

function getRuntimeOpenPositionSnapshot(entry = {}) {
    const brokerSide = String(entry?.broker_position_side || '').trim().toLowerCase()
    const actualSide = String(entry?.actual_position_side || '').trim().toLowerCase()
    const side = brokerSide === 'long' || brokerSide === 'short'
        ? brokerSide
        : actualSide === 'long' || actualSide === 'short'
            ? actualSide
            : ''
    const brokerCount = Number(entry?.broker_position_count || 0) || 0
    const volume = Math.max(
        Number(entry?.broker_position_volume || 0) || 0,
        Math.abs(Number(entry?.position || 0) || 0),
    )
    if (!side && brokerCount <= 0 && volume <= 0) {
        return null
    }
    const entryPrice = Number(entry?.live_entry_fill_price)
    return {
        side,
        brokerCount,
        volume,
        entryPrice: Number.isFinite(entryPrice) && entryPrice > 0 ? entryPrice : null,
    }
}

function resolveMarketSignal(runtime, market = null) {
    const safeRuntime = runtime && typeof runtime === 'object' ? runtime : {}
    const matchesMarket = buildRuntimeTargetMatcher(market)

    const candidateEvents = [
        ...(Array.isArray(safeRuntime.order_commands) ? safeRuntime.order_commands.map((entry) => ({ ...entry, kind: 'command' })) : []),
        ...(Array.isArray(safeRuntime.order_intents) ? safeRuntime.order_intents.map((entry) => ({ ...entry, kind: 'intent' })) : []),
    ]
        .filter((entry) => entry && matchesMarket(entry))
        .sort((left, right) => (
            Number(
                right?.rejected_at
                || right?.filled_at
                || right?.acknowledged_at
                || right?.claimed_at
                || right?.created_at
                || 0
            ) - Number(
                left?.rejected_at
                || left?.filled_at
                || left?.acknowledged_at
                || left?.claimed_at
                || left?.created_at
                || 0
            )
        ))

    const latestEvent = candidateEvents[0] || null
    if (latestEvent) {
        const action = String(latestEvent?.action || '').trim().toLowerCase()
        const side = String(latestEvent?.side || '').trim().toLowerCase()
        const status = String(latestEvent?.status || '').trim().toLowerCase()
        const time = latestEvent?.rejected_at
            || latestEvent?.filled_at
            || latestEvent?.acknowledged_at
            || latestEvent?.claimed_at
            || latestEvent?.created_at
            || null
        const derivedValue = status === 'dispatch_blocked' || status === 'rejected' || status === 'stale'
            ? status
            : action === 'open'
                ? (side === 'long' ? 'buy' : 'sell')
                : action === 'close'
                    ? 'close'
                    : action
        const signal = normalizeSignalLabel(derivedValue)
        return {
            ...signal,
            source: latestEvent.kind,
            detail: `${String(latestEvent?.sleeve_label || latestEvent?.sleeve_id || 'Trader').trim()} · ${status || action || 'idle'}`,
            at: time,
        }
    }

    const sleeveStates = filterSleeveStatesByMarket(safeRuntime, market)
    const decisions = Array.from(new Set(
        sleeveStates
            .map((entry) => normalizeSignalLabel(entry?.decision).label)
            .filter(Boolean)
    ))
    if (!decisions.length) {
        return {
            label: 'HOLD',
            tone: 'neutral',
            source: 'runtime',
            detail: 'No evaluation recorded for this market yet.',
            at: null,
        }
    }
    const nonHoldDecisions = decisions.filter((entry) => entry !== 'HOLD')
    if (!nonHoldDecisions.length) {
        return {
            label: 'HOLD',
            tone: 'neutral',
            source: 'runtime',
            detail: `${sleeveStates.length} sleeve${sleeveStates.length === 1 ? '' : 's'} evaluated flat`,
            at: Math.max(...sleeveStates.map((entry) => Number(entry?.last_evaluated_at || 0))),
        }
    }
    if (new Set(nonHoldDecisions).size === 1) {
        const signal = normalizeSignalLabel(nonHoldDecisions[0])
        return {
            ...signal,
            source: 'runtime',
            detail: `${sleeveStates.length} sleeve${sleeveStates.length === 1 ? '' : 's'} agree`,
            at: Math.max(...sleeveStates.map((entry) => Number(entry?.last_evaluated_at || 0))),
        }
    }
    return {
        label: 'MIXED',
        tone: 'mixed',
        source: 'runtime',
        detail: nonHoldDecisions.join(' + '),
        at: Math.max(...sleeveStates.map((entry) => Number(entry?.last_evaluated_at || 0))),
    }
}

function resolvePositionSummary(historyView, sleeveStates = [], runtime = null) {
    const runtimeRows = (Array.isArray(sleeveStates) ? sleeveStates : [])
        .map((entry) => getRuntimeOpenPositionSnapshot(entry))
        .filter(Boolean)

    if (runtimeRows.length) {
        const sides = Array.from(new Set(runtimeRows.map((entry) => entry.side).filter(Boolean)))
        const totalVolume = runtimeRows.reduce((sum, entry) => sum + (Number(entry?.volume || 0) || 0), 0)
        const brokerCount = runtimeRows.reduce((sum, entry) => sum + (Number(entry?.brokerCount || 0) || 0), 0)
        if (sides.length === 1) {
            const sideLabel = sides[0] === 'long' ? 'LONG' : 'SHORT'
            return {
                label: `${sideLabel}${totalVolume > 0 ? ` ${formatVolumeLabel(totalVolume)}` : ''}`,
                tone: sides[0] === 'long' ? 'buy' : 'sell',
                detail: `${brokerCount || runtimeRows.length} broker position${(brokerCount || runtimeRows.length) === 1 ? '' : 's'} · runtime confirmed`,
            }
        }
        return {
            label: `${brokerCount || runtimeRows.length} OPEN`,
            tone: 'mixed',
            detail: 'Mixed open sides on this market · runtime confirmed',
        }
    }

    const openRows = Array.isArray(historyView?.rows)
        ? historyView.rows.filter((entry) => String(entry?.state || '').trim().toLowerCase() === 'open')
        : []
    if ((Array.isArray(sleeveStates) ? sleeveStates : []).length) {
        return {
            label: 'FLAT',
            tone: 'neutral',
            detail: openRows.length
                ? `Runtime flat · ignoring ${openRows.length} stale history row${openRows.length === 1 ? '' : 's'}`
                : 'No open position on this market',
        }
    }
    const runtimeStatus = String(runtime?.status || '').trim().toLowerCase()
    const runtimeArmed = Boolean(runtime?.armed)
    if (!runtimeArmed || ['idle', 'configured', 'disarmed'].includes(runtimeStatus)) {
        return {
            label: 'FLAT',
            tone: 'neutral',
            detail: openRows.length
                ? `Runtime inactive · ignoring ${openRows.length} unresolved history row${openRows.length === 1 ? '' : 's'}`
                : 'No open position on this market',
        }
    }
    if (openRows.length) {
        const sides = Array.from(new Set(openRows.map((entry) => String(entry?.side || '').trim().toLowerCase()).filter(Boolean)))
        const totalVolume = openRows.reduce((sum, entry) => sum + (Number(entry?.volume || 0) || 0), 0)
        const unconfirmedCount = openRows.filter((entry) => String(entry?.syncStatus || '').trim().toLowerCase() !== 'confirmed').length
        const detailSuffix = unconfirmedCount
            ? ` · ${unconfirmedCount} waiting runtime sync`
            : ''
        if (sides.length === 1) {
            const sideLabel = sides[0] === 'long' ? 'LONG' : 'SHORT'
            return {
                label: `${sideLabel}${totalVolume > 0 ? ` ${formatVolumeLabel(totalVolume)}` : ''}`,
                tone: sides[0] === 'long' ? 'buy' : 'sell',
                detail: `${openRows.length} open trade${openRows.length === 1 ? '' : 's'}${detailSuffix}`,
            }
        }
        return {
            label: `${openRows.length} OPEN`,
            tone: 'mixed',
            detail: `Mixed open sides on this market${detailSuffix}`,
        }
    }

    const brokerCounts = (Array.isArray(sleeveStates) ? sleeveStates : []).reduce((sum, entry) => sum + (Number(entry?.broker_position_count || 0) || 0), 0)
    if (brokerCounts > 0) {
        return {
            label: `${brokerCounts} OPEN`,
            tone: 'mixed',
            detail: 'Broker still reports open positions',
        }
    }

    return {
        label: 'FLAT',
        tone: 'neutral',
        detail: 'No open position on this market',
    }
}

function resolveEntryPriceSummary(historyView, sleeveStates = [], runtime = null) {
    const openRows = Array.isArray(historyView?.rows)
        ? historyView.rows.filter((entry) => String(entry?.state || '').trim().toLowerCase() === 'open')
        : []
    const runtimeRows = (Array.isArray(sleeveStates) ? sleeveStates : [])
        .map((entry) => getRuntimeOpenPositionSnapshot(entry))
        .filter(Boolean)

    if (runtimeRows.length) {
        const pricedRows = runtimeRows.filter((entry) => Number.isFinite(entry?.entryPrice) && entry.entryPrice > 0)
        const sides = Array.from(new Set(runtimeRows.map((entry) => entry.side).filter(Boolean)))
        const tone = sides.length === 1
            ? (sides[0] === 'long' ? 'buy' : 'sell')
            : runtimeRows.length > 1
                ? 'mixed'
                : 'neutral'
        if (!pricedRows.length) {
            const pricedHistoryRows = openRows.filter((entry) => {
                const entryPrice = Number(entry?.entryPrice)
                return Number.isFinite(entryPrice) && entryPrice > 0
            })
            if (pricedHistoryRows.length) {
                const totalWeight = pricedHistoryRows.reduce((sum, entry) => {
                    const volume = Number(entry?.volume)
                    return sum + (Number.isFinite(volume) && volume > 0 ? volume : 1)
                }, 0)
                const weightedEntry = pricedHistoryRows.reduce((sum, entry) => {
                    const volume = Number(entry?.volume)
                    const weight = Number.isFinite(volume) && volume > 0 ? volume : 1
                    return sum + ((Number(entry?.entryPrice) || 0) * weight)
                }, 0) / (totalWeight || pricedHistoryRows.length || 1)
                return {
                    value: formatPrice(weightedEntry),
                    tone,
                    detail: `History fill fallback${pricedHistoryRows.length > 1 ? ` · ${pricedHistoryRows.length} open trades` : ''} · waiting runtime fill sync`,
                }
            }
            return {
                value: '—',
                tone: 'warning',
                detail: 'Runtime open position found · waiting fill price sync',
            }
        }
        const totalWeight = pricedRows.reduce((sum, entry) => sum + (entry.volume > 0 ? entry.volume : 1), 0)
        const weightedEntry = pricedRows.reduce(
            (sum, entry) => sum + (entry.entryPrice * (entry.volume > 0 ? entry.volume : 1)),
            0,
        ) / (totalWeight || pricedRows.length || 1)
        const detailPrefix = sides.length === 1
            ? `${sides[0] === 'long' ? 'Long' : 'Short'} runtime fill`
            : 'Mixed runtime fills'
        const missingCount = runtimeRows.length - pricedRows.length
        return {
            value: formatPrice(weightedEntry),
            tone,
            detail: `${detailPrefix}${runtimeRows.length > 1 ? ` · ${runtimeRows.length} positions` : ''}${missingCount ? ` · ${missingCount} waiting fill sync` : ''}`,
        }
    }

    if ((Array.isArray(sleeveStates) ? sleeveStates : []).length) {
        return {
            value: '—',
            tone: 'neutral',
            detail: openRows.length
                ? `Runtime flat · ignoring ${openRows.length} stale history row${openRows.length === 1 ? '' : 's'}`
                : 'No open entry on this market',
        }
    }
    const runtimeStatus = String(runtime?.status || '').trim().toLowerCase()
    const runtimeArmed = Boolean(runtime?.armed)
    if (!runtimeArmed || ['idle', 'configured', 'disarmed'].includes(runtimeStatus)) {
        return {
            value: '—',
            tone: 'neutral',
            detail: openRows.length
                ? `Runtime inactive · ignoring ${openRows.length} unresolved history row${openRows.length === 1 ? '' : 's'}`
                : 'No open entry on this market',
        }
    }
    if (!openRows.length) {
        return {
            value: '—',
            tone: 'neutral',
            detail: 'No open entry on this market',
        }
    }

    const pricedRows = openRows.filter((entry) => {
        const entryPrice = Number(entry?.entryPrice)
        return Number.isFinite(entryPrice) && entryPrice > 0
    })
    const sides = Array.from(new Set(openRows.map((entry) => String(entry?.side || '').trim().toLowerCase()).filter(Boolean)))
    const tone = sides.length === 1
        ? (sides[0] === 'long' ? 'buy' : 'sell')
        : openRows.length > 1
            ? 'mixed'
            : 'neutral'
    if (!pricedRows.length) {
        return {
            value: '—',
            tone: 'warning',
            detail: `${openRows.length} open trade${openRows.length === 1 ? '' : 's'} · waiting entry price sync`,
        }
    }

    const totalWeight = pricedRows.reduce((sum, entry) => {
        const volume = Number(entry?.volume)
        return sum + (Number.isFinite(volume) && volume > 0 ? volume : 1)
    }, 0)
    const weightedEntry = pricedRows.reduce((sum, entry) => {
        const volume = Number(entry?.volume)
        const weight = Number.isFinite(volume) && volume > 0 ? volume : 1
        return sum + ((Number(entry?.entryPrice) || 0) * weight)
    }, 0) / (totalWeight || pricedRows.length || 1)
    const unconfirmedCount = openRows.filter((entry) => String(entry?.syncStatus || '').trim().toLowerCase() !== 'confirmed').length
    const detailPrefix = sides.length === 1
        ? `${sides[0] === 'long' ? 'Long' : 'Short'} history entry`
        : 'Mixed history entries'

    return {
        value: formatPrice(weightedEntry),
        tone,
        detail: `${detailPrefix}${openRows.length > 1 ? ` · ${openRows.length} open trades` : ''}${unconfirmedCount ? ` · ${unconfirmedCount} waiting runtime sync` : ''}`,
    }
}

function estimateOpenExposure(historyView, latestClosePrice, preferredUnit = 'USD', sleeveStates = [], runtime = null) {
    const runtimeRows = (Array.isArray(sleeveStates) ? sleeveStates : [])
        .filter((entry) => {
            const brokerSide = String(entry?.broker_position_side || '').trim().toLowerCase()
            const actualSide = String(entry?.actual_position_side || '').trim().toLowerCase()
            const brokerCount = Number(entry?.broker_position_count || 0) || 0
            const position = Math.abs(Number(entry?.position || 0) || 0)
            return (
                brokerSide === 'long'
                || brokerSide === 'short'
                || actualSide === 'long'
                || actualSide === 'short'
                || brokerCount > 0
                || position > 0
            )
        })
    const openRows = Array.isArray(historyView?.rows)
        ? historyView.rows.filter((entry) => String(entry?.state || '').trim().toLowerCase() === 'open')
        : []
    if (!runtimeRows.length && (Array.isArray(sleeveStates) ? sleeveStates : []).length) {
        return {
            openCount: 0,
            hasOpenTrades: false,
            tone: 'neutral',
            value: '—',
            detail: openRows.length
                ? `Runtime flat · ignoring ${openRows.length} stale history row${openRows.length === 1 ? '' : 's'}`
                : 'No open trades on this market',
        }
    }
    const runtimeStatus = String(runtime?.status || '').trim().toLowerCase()
    const runtimeArmed = Boolean(runtime?.armed)
    if ((!runtimeArmed || ['idle', 'configured', 'disarmed'].includes(runtimeStatus)) && openRows.length) {
        return {
            openCount: 0,
            hasOpenTrades: false,
            tone: 'neutral',
            value: '—',
            detail: `Runtime inactive · ignoring ${openRows.length} unresolved history row${openRows.length === 1 ? '' : 's'}`,
        }
    }
    if (!openRows.length) {
        return {
            openCount: 0,
            hasOpenTrades: false,
            tone: 'neutral',
            value: '—',
            detail: 'No open trades on this market',
        }
    }

    const latestPricePayload = latestClosePrice && typeof latestClosePrice === 'object' ? latestClosePrice : {}
    const legacyMarkPrice = Number(latestClosePrice)
    const marketPriceByKey = legacyMarkPrice > 0
        ? {}
        : latestPricePayload
    let resolvedMarkCount = 0
    let totalPnl = 0
    let aggregateReturn = 0
    let hasReturn = false
    let unresolvedCount = 0
    let missingMarkCount = 0
    let mixedUnitCount = 0
    let aggregateUnit = ''
    let canAggregateNumeric = true

    for (const entry of openRows) {
        const symbol = String(entry?.symbol || '').trim().toUpperCase()
        const timeframe = String(entry?.timeframe || '').trim().toUpperCase()
        const side = String(entry?.side || '').trim().toLowerCase()
        const entryPrice = Number(entry?.entryPrice)
        const volumeLots = Number(entry?.volume)
        const marketKey = `${symbol}::${timeframe}`
        const marketPayload = marketPriceByKey[marketKey] || {}
        const markPrice = legacyMarkPrice > 0
            ? legacyMarkPrice
            : Number(marketPayload?.last_close ?? marketPayload?.close)

        if (String(entry?.syncStatus || '').trim().toLowerCase() !== 'confirmed') {
            unresolvedCount += 1
        }
        if (!Number.isFinite(markPrice) || markPrice <= 0) {
            missingMarkCount += 1
            continue
        }
        if (!Number.isFinite(entryPrice) || entryPrice <= 0 || !Number.isFinite(volumeLots) || volumeLots <= 0) {
            resolvedMarkCount += 1
            continue
        }

        resolvedMarkCount += 1
        const signedPriceDelta = side === 'short'
            ? entryPrice - markPrice
            : markPrice - entryPrice
        const normalizedReturn = signedPriceDelta / entryPrice
        if (Number.isFinite(normalizedReturn)) {
            aggregateReturn += normalizedReturn
            hasReturn = true
        }

        if (symbol.length !== 6) {
            continue
        }

        const base = symbol.slice(0, 3)
        const quote = symbol.slice(3, 6)
        const units = volumeLots * 100000
        let estimate = null
        let estimateUnit = String(preferredUnit || '').trim().toUpperCase()

        if (estimateUnit === quote) {
            estimate = signedPriceDelta * units
        } else if (estimateUnit === base) {
            estimate = (signedPriceDelta * units) / markPrice
        } else if (quote === 'USD' && estimateUnit === 'USD') {
            estimate = signedPriceDelta * units
        } else if (base === 'USD' && estimateUnit === 'USD') {
            estimate = (signedPriceDelta * units) / markPrice
        } else {
            estimate = signedPriceDelta * units
            estimateUnit = quote
        }

        if (!Number.isFinite(estimate)) {
            continue
        }
        if (!aggregateUnit) {
            aggregateUnit = estimateUnit
            totalPnl += estimate
            continue
        }
        if (aggregateUnit === estimateUnit && canAggregateNumeric) {
            totalPnl += estimate
            continue
        }
        canAggregateNumeric = false
        mixedUnitCount += 1
    }

    if (!resolvedMarkCount) {
        return {
            openCount: openRows.length,
            hasOpenTrades: true,
            tone: 'warning',
            value: '—',
            detail: `${openRows.length} open trade${openRows.length === 1 ? '' : 's'} · waiting current market price`,
        }
    }

    const hasNumericPnl = canAggregateNumeric && Boolean(aggregateUnit)
    const tone = hasNumericPnl
        ? (totalPnl > 0 ? 'buy' : totalPnl < 0 ? 'sell' : 'neutral')
        : 'warning'
    const value = hasNumericPnl
        ? `${formatSignedMoney(totalPnl)} ${aggregateUnit}${hasReturn ? ` · ${formatSignedPercent(aggregateReturn)}` : ''}`
        : hasReturn
            ? formatSignedPercent(aggregateReturn)
            : '—'
    const detail = [
        `${openRows.length} open trade${openRows.length === 1 ? '' : 's'}`,
        resolvedMarkCount ? `${resolvedMarkCount} market${resolvedMarkCount === 1 ? '' : 's'} priced` : '',
        missingMarkCount ? `${missingMarkCount} waiting price` : '',
        unresolvedCount ? `${unresolvedCount} sync pending` : '',
        mixedUnitCount ? 'mixed quote currencies' : '',
    ].filter(Boolean).join(' · ')

    return {
        openCount: openRows.length,
        hasOpenTrades: true,
        tone,
        value,
        detail,
        pnlValue: hasNumericPnl ? totalPnl : null,
        pnlUnit: hasNumericPnl ? aggregateUnit : '',
        markPrice: resolvedMarkCount === 1 && legacyMarkPrice > 0 ? legacyMarkPrice : null,
        unresolvedCount,
        missingMarkCount,
    }
}

function resolveRuntimeAlert(runtime, historyError = '') {
    const marketFeed = runtime?.market_feed || {}
    const marketFeedStatus = String(marketFeed?.status || '').trim().toLowerCase()
    const tradeRuntimeStatus = String(runtime?.status || '').trim().toLowerCase()
    const tradeRuntimeArmed = Boolean(runtime?.armed)
    const runtimeErrorText = String(runtime?.last_error || '').trim()

    if (marketFeedStatus === 'stale') {
        if (!tradeRuntimeArmed && tradeRuntimeStatus === 'market_feed_stale') {
            return null
        }
        return {
            tone: 'warning',
            title: 'Market paused',
            detail: String(
                marketFeed?.detail
                || runtime?.last_error
                || 'The trade runtime is waiting for fresh market updates before it resumes.'
            ),
        }
    }

    if (marketFeedStatus === 'closed') {
        return {
            tone: 'warning',
            title: 'Market closed',
            detail: String(
                marketFeed?.detail
                || runtime?.last_error
                || 'The trade runtime is waiting for the market to reopen.'
            ),
        }
    }

    if (runtimeErrorText) {
        return {
            tone: 'error',
            title: 'Trade runtime issue',
            detail: runtimeErrorText,
        }
    }

    if (historyError) {
        return {
            tone: 'warning',
            title: 'History sync issue',
            detail: historyError,
        }
    }

    return null
}

function buildPipelineRows(runtime, market = null) {
    const safeRuntime = runtime && typeof runtime === 'object' ? runtime : {}
    const matchesMarket = buildRuntimeTargetMatcher(market)

    return [
        ...(Array.isArray(safeRuntime.order_intents) ? safeRuntime.order_intents.map((entry) => ({ ...entry, kind: 'intent' })) : []),
        ...(Array.isArray(safeRuntime.order_commands) ? safeRuntime.order_commands.map((entry) => ({ ...entry, kind: 'command' })) : []),
    ]
        .filter((entry) => entry && matchesMarket(entry))
        .map((entry) => ({
            id: `${entry.kind}-${entry?.id || entry?.fingerprint || Math.random().toString(36).slice(2, 8)}`,
            kind: entry.kind,
            createdAt: normalizeUnixTimestamp(
                entry?.rejected_at
                || entry?.filled_at
                || entry?.acknowledged_at
                || entry?.claimed_at
                || entry?.created_at
                || entry?.record_created_at
            ),
            action: String(entry?.action || '').trim().toLowerCase(),
            side: String(entry?.side || '').trim().toLowerCase(),
            status: String(entry?.status || '').trim().toLowerCase(),
            label: String(entry?.sleeve_label || entry?.sleeve_id || 'Trader').trim() || 'Trader',
            message: String(entry?.message || entry?.rejection_message || '').trim(),
        }))
        .filter((entry) => entry.createdAt)
        .sort((left, right) => right.createdAt - left.createdAt)
        .slice(0, 12)
}

function MobileStatCard({ label, value, detail = '', tone = 'neutral' }) {
    return (
        <div className={`mobileTraderStatCard tone-${tone}`.trim()}>
            <span>{label}</span>
            <strong>{value}</strong>
            {detail ? <small>{detail}</small> : null}
        </div>
    )
}

function MobileActionButton({ label, onClick, disabled = false, tone = 'neutral' }) {
    return (
        <button
            type='button'
            className={`mobileTraderActionButton tone-${tone}`.trim()}
            onClick={onClick}
            disabled={disabled}
        >
            {label}
        </button>
    )
}

function MobileSelectorChips({ label, options = [], selectedKey = '', onSelect = null, emptyMessage = 'No option available.' }) {
    return (
        <div className='mobileTraderSelectorSection'>
            <span className='mobileTraderSelectorLabel'>{label}</span>
            {options.length ? (
                <div className='mobileTraderSelectorRow'>
                    {options.map((option) => (
                        <button
                            key={option.key}
                            type='button'
                            className={`mobileTraderSelectorChip ${selectedKey === option.key ? 'isActive' : ''}`.trim()}
                            onClick={() => onSelect?.(option.key)}
                        >
                            <strong>{option.label}</strong>
                            {option.description ? <span>{option.description}</span> : null}
                        </button>
                    ))}
                </div>
            ) : (
                <div className='mobileTraderEmptyState'>{emptyMessage}</div>
            )}
        </div>
    )
}

export function MobileTraderView({
    authToken = '',
    authUser = null,
    baseChartSettings = null,
    tradeState = null,
    liveTradeRuntime = null,
    serverHealth = null,
    onRuntimeUpdate = null,
    onLogEvent = null,
}) {
    const isGuest = Boolean(authUser?.is_guest)
    const [isDrawerOpen, setIsDrawerOpen] = useState(false)
    const [activeScreen, setActiveScreen] = useState('overview')
    const [showIndicators, setShowIndicators] = useState(false)
    const [scrollChartToEndOnTickIncoming, setScrollChartToEndOnTickIncoming] = useState(true)
    const [isChartCollapsed, setIsChartCollapsed] = useState(false)
    const [isChartFullscreen, setIsChartFullscreen] = useState(false)
    const [selectedPortfolioKey, setSelectedPortfolioKey] = useState('')
    const [selectedPipelineKey, setSelectedPipelineKey] = useState('all')
    const [selectedStrategyKey, setSelectedStrategyKey] = useState('all')
    const [selectedChartKey, setSelectedChartKey] = useState('')
    const [historyState, setHistoryState] = useState({
        loading: true,
        error: '',
        rows: [],
    })
    const [chartHistoryState, setChartHistoryState] = useState({
        loadedCandles: 0,
        historyLoadStep: 0,
        firstLoadedTime: null,
        lastLoadedTime: null,
        isReady: false,
        error: '',
    })
    const [runtimeActionState, setRuntimeActionState] = useState({
        pendingPath: '',
        error: '',
        message: '',
    })
    const [marketPriceState, setMarketPriceState] = useState({
        loading: false,
        error: '',
        byMarket: {},
    })
    const effectiveTradeRuntime = useMemo(
        () => resolveEffectiveRuntime(liveTradeRuntime, serverHealth),
        [liveTradeRuntime, serverHealth]
    )
    const [chartPanelElement, setChartPanelElement] = useState(null)
    const runtimeSource = useMemo(
        () => buildRuntimeSource(effectiveTradeRuntime, tradeState),
        [effectiveTradeRuntime, tradeState]
    )
    const runtimeSleeves = useMemo(
        () => collectRuntimeSleeveEntries(runtimeSource),
        [runtimeSource]
    )
    const portfolioModel = useMemo(
        () => buildMobilePortfolioModel(effectiveTradeRuntime, tradeState, baseChartSettings),
        [baseChartSettings, effectiveTradeRuntime, tradeState]
    )
    const portfolioOptions = portfolioModel.portfolios

    useEffect(() => {
        if (!portfolioOptions.length) {
            setSelectedPortfolioKey('')
            return
        }
        if (portfolioOptions.some((entry) => entry.key === selectedPortfolioKey)) {
            return
        }
        setSelectedPortfolioKey(portfolioOptions[0].key)
    }, [portfolioOptions, selectedPortfolioKey])

    useEffect(() => {
        function handleFullscreenChange() {
            const isFullscreen = typeof document !== 'undefined'
                && document.fullscreenElement === chartPanelElement
            setIsChartFullscreen(Boolean(isFullscreen))
        }

        if (typeof document !== 'undefined') {
            document.addEventListener('fullscreenchange', handleFullscreenChange)
        }

        return () => {
            if (typeof document !== 'undefined') {
                document.removeEventListener('fullscreenchange', handleFullscreenChange)
            }
        }
    }, [chartPanelElement])

    useEffect(() => {
        if (isChartFullscreen && isChartCollapsed) {
            setIsChartCollapsed(false)
        }
    }, [isChartCollapsed, isChartFullscreen])

    const selectedPortfolio = useMemo(
        () => portfolioOptions.find((entry) => entry.key === selectedPortfolioKey) || portfolioOptions[0] || null,
        [portfolioOptions, selectedPortfolioKey]
    )
    const pipelineOptions = useMemo(() => {
        if (!selectedPortfolio) {
            return []
        }
        return [
            {
                key: 'all',
                id: '',
                label: 'All pipelines',
                description: `${selectedPortfolio.strategyCount} active strategies`,
                isAll: true,
                chartTargets: selectedPortfolio.chartTargets,
            },
            ...selectedPortfolio.pipelines.map((entry) => ({
                ...entry,
                description: `${entry.strategyCount} active strategies`,
                isAll: false,
            })),
        ]
    }, [selectedPortfolio])
    useEffect(() => {
        if (!pipelineOptions.length) {
            setSelectedPipelineKey('all')
            return
        }
        if (pipelineOptions.some((entry) => entry.key === selectedPipelineKey)) {
            return
        }
        setSelectedPipelineKey(pipelineOptions[0].key)
    }, [pipelineOptions, selectedPipelineKey])
    const selectedPipeline = useMemo(
        () => pipelineOptions.find((entry) => entry.key === selectedPipelineKey) || pipelineOptions[0] || null,
        [pipelineOptions, selectedPipelineKey]
    )
    const availableScopeChartTargets = useMemo(() => {
        if (!selectedPortfolio) {
            return portfolioModel.chartTargets
        }
        if (selectedPipeline && !selectedPipeline.isAll) {
            return selectedPipeline.chartTargets
        }
        return selectedPortfolio.chartTargets
    }, [portfolioModel.chartTargets, selectedPipeline, selectedPortfolio])
    const strategyOptions = useMemo(() => [
        {
            key: 'all',
            label: selectedPipeline?.isAll ? 'Portfolio total' : 'Pipeline total',
            description: selectedPipeline?.isAll
                ? 'Aggregate results across every active strategy in this portfolio.'
                : `Aggregate results across every strategy in ${selectedPipeline?.label || 'this pipeline'}.`,
            isAll: true,
        },
        ...availableScopeChartTargets.map((entry) => ({
            ...entry,
            description: `${entry.symbol} · ${entry.timeframe}`,
            isAll: false,
        })),
    ], [availableScopeChartTargets, selectedPipeline])
    useEffect(() => {
        if (!strategyOptions.length) {
            setSelectedStrategyKey('all')
            return
        }
        if (strategyOptions.some((entry) => entry.key === selectedStrategyKey)) {
            return
        }
        setSelectedStrategyKey(strategyOptions[0].key)
    }, [selectedStrategyKey, strategyOptions])
    const selectedStrategy = useMemo(
        () => strategyOptions.find((entry) => entry.key === selectedStrategyKey) || strategyOptions[0] || null,
        [selectedStrategyKey, strategyOptions]
    )
    const chartTargetOptions = useMemo(
        () => availableScopeChartTargets.map((entry) => ({
            ...entry,
            description: `${entry.symbol} · ${entry.timeframe}`,
        })),
        [availableScopeChartTargets]
    )
    useEffect(() => {
        if (!chartTargetOptions.length) {
            setSelectedChartKey('')
            return
        }
        if (chartTargetOptions.some((entry) => entry.key === selectedChartKey)) {
            return
        }
        setSelectedChartKey(chartTargetOptions[0].key)
    }, [chartTargetOptions, selectedChartKey])
    useEffect(() => {
        if (selectedStrategyKey === 'all') {
            return
        }
        const matchingTarget = chartTargetOptions.find((entry) => entry.key === selectedStrategyKey)
        if (!matchingTarget) {
            return
        }
        setSelectedChartKey((current) => (current === matchingTarget.key ? current : matchingTarget.key))
    }, [chartTargetOptions, selectedStrategyKey])
    const selectedChartTarget = useMemo(
        () => chartTargetOptions.find((entry) => entry.key === selectedChartKey) || chartTargetOptions[0] || null,
        [chartTargetOptions, selectedChartKey]
    )
    const dataScope = useMemo(
        () => ({
            portfolioId: selectedPortfolio?.id || '',
            pipelineId: selectedPipeline && !selectedPipeline.isAll ? selectedPipeline.id : '',
            sleeveId: selectedStrategy && !selectedStrategy.isAll ? (selectedStrategy.sleeveId || selectedStrategy.id) : '',
        }),
        [selectedPipeline, selectedPortfolio, selectedStrategy]
    )
    const availableIndicators = useMemo(
        () => normalizeIndicatorList(selectedChartTarget?.indicators),
        [selectedChartTarget?.indicators]
    )
    const visibleChartSettings = useMemo(
        () => buildMobileChartSettings(baseChartSettings, selectedChartTarget, showIndicators),
        [baseChartSettings, selectedChartTarget, showIndicators]
    )
    const chartViewKey = useMemo(
        () => [
            String(visibleChartSettings?.symbol || '').trim().toUpperCase(),
            String(visibleChartSettings?.timeframe || '').trim().toUpperCase(),
            Number(visibleChartSettings?.bars || 0),
            showIndicators ? 'with-indicators' : 'without-indicators',
        ].join('|'),
        [showIndicators, visibleChartSettings]
    )
    const tradeHistoryQuery = useMemo(() => {
        const liveSessionStart = normalizeUnixTimestamp(effectiveTradeRuntime?.last_armed_at)
        const nowSeconds = Math.floor(Date.now() / 1000)
        const elapsedDays = liveSessionStart
            ? Math.max(1, Math.ceil((nowSeconds - liveSessionStart) / 86400) + 1)
            : 7
        return new URLSearchParams({
            range_key: liveSessionStart ? 'custom' : '7d',
            custom_days: String(elapsedDays),
            status_filter: 'all',
            limit: String(MOBILE_HISTORY_LIMIT),
        }).toString()
    }, [effectiveTradeRuntime?.last_armed_at])

    const refreshHistory = useCallback(async () => {
        setHistoryState((current) => ({
            ...current,
            loading: true,
            error: '',
        }))

        try {
            const response = await fetch(buildApiUrl(`/workspace/live-trades?${tradeHistoryQuery}`), {
                headers: authToken
                    ? { Authorization: `Bearer ${authToken}` }
                    : {},
            })
            const data = await readJsonResponse(response)
            if (!response.ok) {
                throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to load live trade history.')}`)
            }
            const rows = Array.isArray(data?.trades) ? data.trades : []
            setHistoryState({
                loading: false,
                error: '',
                rows,
            })
        } catch (error) {
            setHistoryState((current) => ({
                ...current,
                loading: false,
                error: error.message || 'Failed to load live trade history.',
            }))
        }
    }, [authToken, tradeHistoryQuery])

    useEffect(() => {
        let cancelled = false
        let timer = null

        async function syncHistory() {
            if (cancelled) {
                return
            }
            await refreshHistory()
            if (!cancelled) {
                timer = window.setTimeout(() => {
                    void syncHistory()
                }, effectiveTradeRuntime?.armed ? 3000 : 10000)
            }
        }

        void syncHistory()

        return () => {
            cancelled = true
            if (timer) {
                window.clearTimeout(timer)
            }
        }
    }, [effectiveTradeRuntime?.armed, refreshHistory])

    const annotatedHistoryRows = useMemo(
        () => annotateLiveTradeRows(historyState.rows, portfolioModel.chartTargets),
        [historyState.rows, portfolioModel.chartTargets]
    )
    const scopeMarketTargets = useMemo(() => {
        const sourceTargets = selectedStrategy && !selectedStrategy.isAll
            ? availableScopeChartTargets.filter((entry) => entry.key === selectedStrategy.key)
            : availableScopeChartTargets
        const registry = new Map()
        for (const entry of sourceTargets) {
            if (!entry?.marketKey) {
                continue
            }
            if (!registry.has(entry.marketKey)) {
                registry.set(entry.marketKey, {
                    key: entry.marketKey,
                    symbol: entry.symbol,
                    timeframe: entry.timeframe,
                })
            }
        }
        return Array.from(registry.values())
    }, [availableScopeChartTargets, selectedStrategy])
    const scopeMarketRequestKey = useMemo(
        () => scopeMarketTargets.map((entry) => `${entry.symbol}:${entry.timeframe}`).join('|'),
        [scopeMarketTargets]
    )
    useEffect(() => {
        if (!scopeMarketTargets.length) {
            setMarketPriceState({
                loading: false,
                error: '',
                byMarket: {},
            })
            return
        }

        let cancelled = false
        let timer = null

        async function refreshLastCandle() {
            setMarketPriceState((current) => ({
                ...current,
                loading: true,
                error: '',
            }))

            try {
                const response = await fetch(buildApiUrl('/chart/market-tails'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
                    },
                    body: JSON.stringify({
                        markets: scopeMarketTargets.map((entry) => ({
                            symbol: entry.symbol,
                            timeframe: entry.timeframe,
                            bars: 2,
                        })),
                    }),
                })
                const data = await readJsonResponse(response)
                if (!response.ok) {
                    throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to load current market prices.')}`)
                }
                const byMarket = {}
                for (const entry of Array.isArray(data?.markets) ? data.markets : []) {
                    const key = String(entry?.key || `${entry?.symbol || ''}::${entry?.timeframe || ''}`).trim()
                    if (!key) {
                        continue
                    }
                    byMarket[key] = entry
                }
                if (!cancelled) {
                    setMarketPriceState({
                        loading: false,
                        error: '',
                        byMarket,
                    })
                }
            } catch (error) {
                if (cancelled) {
                    return
                }
                setMarketPriceState((current) => ({
                    ...current,
                    loading: false,
                    error: error.message || 'Failed to load current market prices.',
                }))
            }
        }

        async function syncLoop() {
            if (cancelled) {
                return
            }
            await refreshLastCandle()
            if (!cancelled) {
                timer = window.setTimeout(() => {
                    void syncLoop()
                }, effectiveTradeRuntime?.armed ? 3000 : 10000)
            }
        }

        void syncLoop()

        return () => {
            cancelled = true
            if (timer) {
                window.clearTimeout(timer)
            }
        }
    }, [authToken, effectiveTradeRuntime?.armed, scopeMarketRequestKey, scopeMarketTargets])

    const scopedHistoryRows = useMemo(
        () => filterRowsByMarket(annotatedHistoryRows, dataScope),
        [annotatedHistoryRows, dataScope]
    )
    const scopedSleeveStates = useMemo(
        () => filterSleeveStatesByMarket(effectiveTradeRuntime, dataScope),
        [dataScope, effectiveTradeRuntime]
    )
    const scopedHistory = useMemo(
        () => buildTradeHistoryView(scopedHistoryRows, scopedSleeveStates),
        [scopedHistoryRows, scopedSleeveStates]
    )
    const selectedChartRows = useMemo(
        () => filterRowsByMarket(annotatedHistoryRows, selectedChartTarget),
        [annotatedHistoryRows, selectedChartTarget]
    )
    const selectedChartSleeveStates = useMemo(
        () => filterSleeveStatesByMarket(effectiveTradeRuntime, selectedChartTarget),
        [effectiveTradeRuntime, selectedChartTarget]
    )
    const selectedChartHistory = useMemo(
        () => buildTradeHistoryView(selectedChartRows, selectedChartSleeveStates),
        [selectedChartRows, selectedChartSleeveStates]
    )
    const resultUnit = String(
        effectiveTradeRuntime?.account_currency
        || effectiveTradeRuntime?.broker_account_currency
        || serverHealth?.trade_runtime?.account_currency
        || 'USD'
    ).trim().toUpperCase() || 'USD'
    const currentSignal = useMemo(
        () => resolveMarketSignal(effectiveTradeRuntime, dataScope),
        [dataScope, effectiveTradeRuntime]
    )
    const currentPosition = useMemo(
        () => resolvePositionSummary(scopedHistory, scopedSleeveStates, effectiveTradeRuntime),
        [effectiveTradeRuntime, scopedHistory, scopedSleeveStates]
    )
    const currentEntryPrice = useMemo(
        () => resolveEntryPriceSummary(scopedHistory, scopedSleeveStates, effectiveTradeRuntime),
        [effectiveTradeRuntime, scopedHistory, scopedSleeveStates]
    )
    const openExposure = useMemo(
        () => estimateOpenExposure(scopedHistory, marketPriceState.byMarket, resultUnit, scopedSleeveStates, effectiveTradeRuntime),
        [effectiveTradeRuntime, marketPriceState.byMarket, resultUnit, scopedHistory, scopedSleeveStates]
    )
    const streamMarkers = useMemo(
        () => buildTradeRuntimeChartMarkers(effectiveTradeRuntime, visibleChartSettings, selectedChartTarget),
        [effectiveTradeRuntime, selectedChartTarget, visibleChartSettings]
    )
    const latestLatency = useMemo(() => {
        const sleeveLatencies = scopedSleeveStates
            .map((entry) => Number(entry?.last_latency_ms))
            .filter((value) => Number.isFinite(value))
        if (sleeveLatencies.length) {
            return Math.max(...sleeveLatencies)
        }
        const runtimeLatency = Number(effectiveTradeRuntime?.metrics?.last_latency_ms ?? effectiveTradeRuntime?.last_latency_ms)
        return Number.isFinite(runtimeLatency) ? runtimeLatency : null
    }, [effectiveTradeRuntime?.last_latency_ms, effectiveTradeRuntime?.metrics?.last_latency_ms, scopedSleeveStates])
    const latestEvaluationAt = useMemo(() => {
        const values = scopedSleeveStates
            .map((entry) => Number(entry?.last_evaluated_at || 0))
            .filter((value) => Number.isFinite(value) && value > 0)
        if (!values.length) {
            return null
        }
        return Math.max(...values)
    }, [scopedSleeveStates])
    const mobileAlert = useMemo(
        () => resolveRuntimeAlert(effectiveTradeRuntime, historyState.error || marketPriceState.error),
        [effectiveTradeRuntime, historyState.error, marketPriceState.error]
    )
    const runtimeMetrics = effectiveTradeRuntime?.metrics && typeof effectiveTradeRuntime.metrics === 'object'
        ? effectiveTradeRuntime.metrics
        : {}
    const pipelineRows = useMemo(
        () => buildPipelineRows(effectiveTradeRuntime, dataScope),
        [dataScope, effectiveTradeRuntime]
    )
    const pipelineBreakdowns = useMemo(() => {
        if (!selectedPortfolio) {
            return []
        }
        return selectedPortfolio.pipelines.map((pipeline) => {
            const pipelineScope = {
                portfolioId: selectedPortfolio.id,
                pipelineId: pipeline.id,
            }
            const pipelineHistoryRows = filterRowsByMarket(annotatedHistoryRows, pipelineScope)
            const pipelineSleeves = filterSleeveStatesByMarket(effectiveTradeRuntime, pipelineScope)
            const pipelineHistory = buildTradeHistoryView(pipelineHistoryRows, pipelineSleeves)
            const strategies = pipeline.chartTargets.map((target) => {
                const strategyScope = { sleeveId: target.sleeveId || target.id }
                const strategyHistoryRows = filterRowsByMarket(annotatedHistoryRows, strategyScope)
                const strategySleeves = filterSleeveStatesByMarket(effectiveTradeRuntime, strategyScope)
                const strategyHistory = buildTradeHistoryView(strategyHistoryRows, strategySleeves)
                return {
                    ...target,
                    history: strategyHistory,
                    signal: resolveMarketSignal(effectiveTradeRuntime, {
                        sleeveId: target.sleeveId || target.id,
                        symbol: target.symbol,
                        timeframe: target.timeframe,
                    }),
                }
            })
            return {
                ...pipeline,
                history: pipelineHistory,
                strategies,
            }
        })
    }, [annotatedHistoryRows, effectiveTradeRuntime, selectedPortfolio])
    const selectedChartMarketPrice = useMemo(
        () => marketPriceState.byMarket[selectedChartTarget?.marketKey || ''] || null,
        [marketPriceState.byMarket, selectedChartTarget?.marketKey]
    )
    const chartControls = useMemo(
        () => (
            <div className='mobileChartControlStrip'>
                <button
                    type='button'
                    className={`mobileChartControlButton ${isChartCollapsed ? 'isActive' : ''}`.trim()}
                    onClick={() => setIsChartCollapsed((current) => !current)}
                    aria-label={isChartCollapsed ? 'Expand chart' : 'Collapse chart'}
                    title={isChartCollapsed ? 'Expand chart' : 'Collapse chart'}
                >
                    <svg viewBox='0 0 24 24' aria-hidden='true'>
                        <path d={isChartCollapsed ? 'M7 15l5-5 5 5' : 'M7 10l5 5 5-5'} />
                    </svg>
                </button>
                <button
                    type='button'
                    className={`mobileChartControlButton ${showIndicators ? 'isActive' : ''}`.trim()}
                    onClick={() => setShowIndicators((current) => !current)}
                    disabled={!availableIndicators.length}
                    aria-label={showIndicators ? 'Hide indicators' : 'Show indicators'}
                    title={showIndicators ? 'Hide indicators' : 'Show indicators'}
                >
                    <svg viewBox='0 0 24 24' aria-hidden='true'>
                        <path d='M4 7.5h6.5l2 3h7.5' />
                        <path d='M4 12h4l2 3h10' />
                        <path d='M4 16.5h8l2-3h6' />
                    </svg>
                </button>
                <button
                    type='button'
                    className={`mobileChartControlButton ${scrollChartToEndOnTickIncoming ? 'isActive' : ''}`.trim()}
                    onClick={() => setScrollChartToEndOnTickIncoming((current) => !current)}
                    aria-pressed={scrollChartToEndOnTickIncoming}
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
                <button
                    type='button'
                    className={`mobileChartControlButton ${isChartFullscreen ? 'isActive' : ''}`.trim()}
                    onClick={() => {
                        if (!chartPanelElement || typeof document === 'undefined') {
                            return
                        }
                        if (document.fullscreenElement === chartPanelElement) {
                            void document.exitFullscreen?.()
                            return
                        }
                        const requestFullscreen = chartPanelElement.requestFullscreen
                        if (typeof requestFullscreen !== 'function') {
                            return
                        }
                        void requestFullscreen.call(chartPanelElement).then(async () => {
                            try {
                                await window.screen?.orientation?.lock?.('landscape')
                            } catch {
                                // Best-effort only. Many mobile browsers block this.
                            }
                        }).catch(() => {})
                    }}
                    aria-pressed={isChartFullscreen}
                    aria-label={isChartFullscreen ? 'Exit fullscreen chart' : 'Open fullscreen chart'}
                    title={isChartFullscreen ? 'Exit fullscreen chart' : 'Open fullscreen chart'}
                    >
                        <svg viewBox='0 0 24 24' aria-hidden='true'>
                            <path d='M8 4H4v4' />
                        <path d='M16 4h4v4' />
                        <path d='M20 16v4h-4' />
                        <path d='M8 20H4v-4' />
                        </svg>
                </button>
            </div>
        ),
        [availableIndicators.length, chartPanelElement, isChartCollapsed, isChartFullscreen, scrollChartToEndOnTickIncoming, showIndicators]
    )

    const confirmTradeRuntimeAction = useCallback(async (path) => {
        const response = await fetch(buildApiUrl('/health'), {
            headers: authToken
                ? { Authorization: `Bearer ${authToken}` }
                : {},
        })
        const data = await readJsonResponse(response)
        if (!response.ok || data?.status !== 'ok') {
            throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to confirm trade runtime state.')}`)
        }

        const nextRuntime = normalizeRuntimePayload(data?.trade_runtime || data)
        const confirmed = (() => {
            switch (path) {
            case '/trade/runtime/arm':
                return Boolean(nextRuntime?.armed)
            case '/trade/runtime/disarm':
                return nextRuntime?.armed === false
            case '/trade/runtime/arm-live-dispatch':
                return Boolean(nextRuntime?.live_dispatch_armed)
            case '/trade/runtime/disarm-live-dispatch':
                return nextRuntime?.live_dispatch_armed === false
            default:
                return false
            }
        })()

        return {
            confirmed,
            runtime: nextRuntime,
        }
    }, [authToken])

    const postTradeRuntime = useCallback(async (path, payload = null, successMessage = '') => {
        if (isGuest) {
            setRuntimeActionState({
                pendingPath: '',
                error: GUEST_RESTRICTION_MESSAGE,
                message: '',
            })
            onLogEvent?.(`Trade · ${GUEST_RESTRICTION_MESSAGE}`)
            return null
        }

        setRuntimeActionState({
            pendingPath: path,
            error: '',
            message: '',
        })

        try {
            const response = await fetch(buildApiUrl(path), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
                },
                body: payload ? JSON.stringify(payload) : '{}',
            })
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Trade runtime request failed.')}`)
            }

            const nextRuntime = normalizeRuntimePayload(data?.trade_runtime || data)
            onRuntimeUpdate?.(nextRuntime)
            if (successMessage) {
                onLogEvent?.(successMessage)
            }
            setRuntimeActionState({
                pendingPath: '',
                error: '',
                message: successMessage || 'Runtime updated.',
            })
            return nextRuntime
        } catch (error) {
            try {
                const confirmation = await confirmTradeRuntimeAction(path)
                if (confirmation.confirmed) {
                    onRuntimeUpdate?.(confirmation.runtime)
                    if (successMessage) {
                        onLogEvent?.(successMessage)
                    }
                    setRuntimeActionState({
                        pendingPath: '',
                        error: '',
                        message: successMessage || 'Runtime updated.',
                    })
                    return confirmation.runtime
                }
            } catch {
                // Keep original error below.
            }

            const message = error.message || 'Trade runtime request failed.'
            onLogEvent?.(`Trade · ${message}`)
            setRuntimeActionState({
                pendingPath: '',
                error: message,
                message: '',
            })
            return null
        }
    }, [authToken, confirmTradeRuntimeAction, isGuest, onLogEvent, onRuntimeUpdate])

    const isRuntimeArmed = Boolean(effectiveTradeRuntime?.armed)
    const isLiveDispatchArmed = Boolean(effectiveTradeRuntime?.live_dispatch_armed)
    const feedStatus = String(effectiveTradeRuntime?.market_feed?.status || 'idle').trim().toLowerCase() || 'idle'
    const selectedScopeResult = scopedHistory.summary.realizedPnl
    const selectedScopeWinRate = scopedHistory.summary.winRate
    const selectedScopeOpenCount = scopedHistory.summary.openCount
    const selectedScopeClosedCount = scopedHistory.summary.closedCount
    const selectedScopeDecisionCount = scopedSleeveStates.length
    const selectedPortfolioLabel = selectedPortfolio?.label || 'Trader mobile'
    const selectedPipelineLabel = selectedPipeline?.isAll ? 'All pipelines' : selectedPipeline?.label || 'Primary pipeline'
    const selectedScopeLabel = selectedStrategy?.isAll
        ? (selectedPipeline?.isAll ? 'Portfolio total' : `${selectedPipelineLabel} total`)
        : selectedStrategy?.label || 'Strategy'
    const selectedChartLabel = selectedChartTarget?.chartLabel || `${visibleChartSettings.symbol} · ${visibleChartSettings.timeframe}`
    const chartLoading = !chartHistoryState?.isReady && !String(chartHistoryState?.error || '').trim()
    const activeScreenMeta = MOBILE_TRADER_SCREENS.find((entry) => entry.id === activeScreen) || MOBILE_TRADER_SCREENS[0]

    function openMobileScreen(screenId) {
        setActiveScreen(screenId)
        setIsDrawerOpen(false)
    }

    return (
        <div id='MobileApp' className='mobileTraderApp'>
            {mobileAlert ? (
                <div className={`mobileTraderAlert tone-${mobileAlert.tone}`.trim()} role='alert'>
                    <strong>{mobileAlert.title}</strong>
                    <span>{mobileAlert.detail}</span>
                </div>
            ) : null}

            <header className='mobileTraderTopbar'>
                <button
                    type='button'
                    className='mobileTraderMenuButton'
                    onClick={() => setIsDrawerOpen(true)}
                    aria-label='Open mobile trader menu'
                >
                    <span />
                    <span />
                    <span />
                </button>

                <div className='mobileTraderTopbarText'>
                    <strong>{selectedPortfolioLabel}</strong>
                    <span>{selectedScopeLabel} · Chart {selectedChartLabel}</span>
                </div>

                <div className='mobileTraderTopbarChips'>
                    <span className={`mobileTraderChip ${isRuntimeArmed ? 'isGood' : ''}`.trim()}>
                        {isRuntimeArmed ? 'Armed' : 'Safe'}
                    </span>
                    <span className={`mobileTraderChip ${isLiveDispatchArmed ? 'isWarning' : ''}`.trim()}>
                        {isLiveDispatchArmed ? 'Live' : 'Paper'}
                    </span>
                </div>
            </header>

            <main className='mobileTraderMain'>
                <section className='mobileTraderScreenHeader'>
                    <div>
                        <strong>{activeScreenMeta.label}</strong>
                        <span>{activeScreenMeta.description}</span>
                    </div>
                </section>

                {runtimeActionState.error ? (
                    <div className='mobileTraderInlineNotice isError'>{runtimeActionState.error}</div>
                ) : null}
                {runtimeActionState.message ? (
                    <div className='mobileTraderInlineNotice isSuccess'>{runtimeActionState.message}</div>
                ) : null}

                {activeScreen === 'overview' ? (
                    <>
                        <section className='mobileTraderScreenCard'>
                            <MobileSelectorChips
                                label='Portfolio data'
                                options={portfolioOptions.map((entry) => ({
                                    key: entry.key,
                                    label: entry.label,
                                    description: `${entry.strategyCount} active strategies`,
                                }))}
                                selectedKey={selectedPortfolio?.key || ''}
                                onSelect={setSelectedPortfolioKey}
                                emptyMessage='No portfolio is currently active in Trader.'
                            />
                            <MobileSelectorChips
                                label='Pipeline filter'
                                options={pipelineOptions}
                                selectedKey={selectedPipeline?.key || 'all'}
                                onSelect={setSelectedPipelineKey}
                                emptyMessage='This portfolio has no active pipeline.'
                            />
                            <MobileSelectorChips
                                label='Data view'
                                options={strategyOptions}
                                selectedKey={selectedStrategy?.key || 'all'}
                                onSelect={setSelectedStrategyKey}
                                emptyMessage='No active strategy is available in this scope.'
                            />
                            <MobileSelectorChips
                                label='Chart view'
                                options={chartTargetOptions}
                                selectedKey={selectedChartTarget?.key || ''}
                                onSelect={setSelectedChartKey}
                                emptyMessage='No chart target is available in this scope.'
                            />
                            <div className='mobileTraderFocusSummary'>
                                <div>
                                    <span>Scope</span>
                                    <strong>{selectedScopeLabel}</strong>
                                </div>
                                <div>
                                    <span>Chart target</span>
                                    <strong>{selectedChartTarget?.label || '—'}</strong>
                                </div>
                                <div>
                                    <span>Chart price</span>
                                    <strong>{formatPrice(selectedChartMarketPrice?.last_close)}</strong>
                                </div>
                                <div>
                                    <span>Chart trades</span>
                                    <strong>{selectedChartHistory.summary.tradeCount}</strong>
                                </div>
                            </div>
                        </section>
                        <section className={`mobileTraderChartPanel ${isChartCollapsed ? 'isCollapsed' : ''}`.trim()} ref={setChartPanelElement}>
                            <div className='mobileTraderChartShell'>
                                <Chart
                                    key={chartViewKey}
                                    id='MobileTraderChart'
                                    authToken={authToken}
                                    chartSettings={visibleChartSettings}
                                    runId={0}
                                    displayMode='stream'
                                    metaFontSize={0.88}
                                    showVolumePanel={false}
                                    volumeMode='volume'
                                    scrollChartToEndOnTickIncoming={scrollChartToEndOnTickIncoming}
                                    tradeMarkers={streamMarkers}
                                    tradeMarkerMode='trader'
                                    streamLeadingControls={chartControls}
                                    streamMetaPlacement='external'
                                    streamMetaCollapsed={isChartCollapsed}
                                    guestNoticeVisible={false}
                                    onHistoryStateChange={setChartHistoryState}
                                />
                                {chartLoading ? (
                                    <div className='mobileTraderChartLoading'>
                                        <strong>Chart loading</strong>
                                        <span>Hydrating mobile market history…</span>
                                    </div>
                                ) : null}
                            </div>
                        </section>

                        <section className='mobileTraderStatsGrid'>
                            <MobileStatCard
                                label='Signal'
                                value={currentSignal.label}
                                detail={`${currentSignal.detail} · ${currentSignal.at ? formatRelativeTimestamp(currentSignal.at) : 'waiting'}`}
                                tone={currentSignal.tone}
                            />
                            <MobileStatCard
                                label='Position'
                                value={currentPosition.label}
                                detail={currentPosition.detail}
                                tone={currentPosition.tone}
                            />
                            <MobileStatCard
                                label='Entry'
                                value={currentEntryPrice.value}
                                detail={currentEntryPrice.detail}
                                tone={currentEntryPrice.tone}
                            />
                            <MobileStatCard
                                label='Closed P/L'
                                value={`${formatSignedMoney(selectedScopeResult)} ${resultUnit}`}
                                detail={`${selectedScopeClosedCount} closed · ${selectedScopeOpenCount} open`}
                                tone={selectedScopeResult >= 0 ? 'buy' : 'sell'}
                            />
                            <MobileStatCard
                                label='Open P/L'
                                value={openExposure.value}
                                detail={openExposure.detail}
                                tone={openExposure.tone}
                            />
                            <MobileStatCard
                                label='Latency'
                                value={latestLatency === null ? '—' : `${latestLatency} ms`}
                                detail={latestEvaluationAt ? `Last check ${formatRelativeTimestamp(latestEvaluationAt)}` : 'No evaluation yet'}
                                tone={latestLatency !== null && latestLatency > 400 ? 'warning' : 'neutral'}
                            />
                            <MobileStatCard
                                label='Win rate'
                                value={formatPercent(selectedScopeWinRate)}
                                detail={`${scopedHistory.summary.winCount} winners`}
                                tone={selectedScopeResult >= 0 ? 'buy' : 'neutral'}
                            />
                            <MobileStatCard
                                label='Decisions'
                                value={String(selectedScopeDecisionCount || runtimeMetrics.decision_count || 0)}
                                detail={`Feed ${feedStatus}`}
                                tone={feedStatus === 'healthy' ? 'buy' : feedStatus === 'stale' ? 'warning' : 'neutral'}
                            />
                        </section>
                    </>
                ) : null}

                {activeScreen === 'controls' ? (
                    <section className='mobileTraderScreenCard'>
                        <div className='mobileTraderActionGrid'>
                            <MobileActionButton
                                label={isRuntimeArmed ? 'Disarm runtime' : 'Arm runtime'}
                                tone={isRuntimeArmed ? 'warning' : 'buy'}
                                disabled={runtimeActionState.pendingPath !== ''}
                                onClick={() => void postTradeRuntime(
                                    isRuntimeArmed ? '/trade/runtime/disarm' : '/trade/runtime/arm',
                                    null,
                                    isRuntimeArmed ? 'Trade · Runtime disarmed.' : 'Trade · Runtime armed.',
                                )}
                            />
                            <MobileActionButton
                                label={isLiveDispatchArmed ? 'Disable live dispatch' : 'Enable live dispatch'}
                                tone={isLiveDispatchArmed ? 'warning' : 'sell'}
                                disabled={runtimeActionState.pendingPath !== '' || !isRuntimeArmed}
                                onClick={() => void postTradeRuntime(
                                    isLiveDispatchArmed ? '/trade/runtime/disarm-live-dispatch' : '/trade/runtime/arm-live-dispatch',
                                    null,
                                    isLiveDispatchArmed ? 'Trade · Live dispatch disarmed.' : 'Trade · Live dispatch armed.',
                                )}
                            />
                            <MobileActionButton
                                label='Evaluate now'
                                disabled={runtimeActionState.pendingPath !== ''}
                                onClick={() => void postTradeRuntime('/trade/runtime/evaluate', null, 'Trade · Runtime evaluated.')}
                            />
                            <MobileActionButton
                                label='Process intents'
                                disabled={runtimeActionState.pendingPath !== ''}
                                onClick={() => void postTradeRuntime('/trade/runtime/process-intents', null, 'Trade · Order intents processed.')}
                            />
                            <MobileActionButton
                                label='Reconcile'
                                disabled={runtimeActionState.pendingPath !== ''}
                                onClick={() => void postTradeRuntime('/trade/runtime/reconcile', null, 'Trade · Runtime reconciliation completed.')}
                            />
                            <MobileActionButton
                                label='Reset queue'
                                disabled={runtimeActionState.pendingPath !== ''}
                                onClick={() => void postTradeRuntime('/trade/runtime/reset-commands', { clearIntents: false }, 'Trade · Broker command queue reset.')}
                            />
                            <MobileActionButton
                                label='Refresh history'
                                disabled={historyState.loading}
                                onClick={() => void refreshHistory()}
                            />
                        </div>
                    </section>
                ) : null}

                {activeScreen === 'summary' ? (
                    <section className='mobileTraderScreenCard'>
                        <div className='mobileTraderSummaryList'>
                            <div><span>Status</span><strong>{String(effectiveTradeRuntime?.status || 'idle')}</strong></div>
                            <div><span>Execution path</span><strong>{String(effectiveTradeRuntime?.execution_mode || tradeState?.executionMode || 'paper')}</strong></div>
                            <div><span>Portfolio mode</span><strong>{String(effectiveTradeRuntime?.mode || tradeState?.mode || 'parallel_sleeves')}</strong></div>
                            <div><span>Signal validity</span><strong>{Number(effectiveTradeRuntime?.signal_validity_seconds ?? tradeState?.signalValiditySeconds ?? 10)} s</strong></div>
                            <div><span>Latency budget</span><strong>{Number(effectiveTradeRuntime?.latency_budget_ms ?? tradeState?.latencyBudgetMs ?? 150)} ms</strong></div>
                            <div><span>Same-symbol policy</span><strong>{String(effectiveTradeRuntime?.same_symbol_execution_policy || tradeState?.sameSymbolExecutionPolicy || 'independent')}</strong></div>
                            <div><span>Loaded portfolios</span><strong>{portfolioOptions.length}</strong></div>
                            <div><span>Configured sleeves</span><strong>{runtimeSleeves.length}</strong></div>
                            <div><span>Active symbols</span><strong>{Array.isArray(effectiveTradeRuntime?.active_symbols) && effectiveTradeRuntime.active_symbols.length ? effectiveTradeRuntime.active_symbols.join(', ') : '—'}</strong></div>
                            <div><span>Feed</span><strong>{feedStatus}</strong></div>
                            <div><span>EA online</span><strong>{serverHealth?.trade_runtime?.ea_online === true ? 'Yes' : serverHealth?.trade_runtime?.ea_online === false ? 'No' : '—'}</strong></div>
                            <div><span>Account mode</span><strong>{String(serverHealth?.trade_runtime?.account_mode || '—')}</strong></div>
                            <div><span>Hedging</span><strong>{typeof serverHealth?.trade_runtime?.broker_supports_hedging === 'boolean' ? (serverHealth.trade_runtime.broker_supports_hedging ? 'Allowed' : 'Not allowed') : '—'}</strong></div>
                        </div>
                    </section>
                ) : null}

                {activeScreen === 'markets' ? (
                    <>
                        <section className='mobileTraderScreenCard'>
                            <div className='mobileTraderPipelineList'>
                                {pipelineBreakdowns.length ? pipelineBreakdowns.map((pipeline) => (
                                    <article key={pipeline.key} className='mobileTraderPipelineCard'>
                                        <strong>{pipeline.label}</strong>
                                        <span>
                                            {pipeline.history.summary.closedCount} closed · {pipeline.history.summary.openCount} open · {formatPercent(pipeline.history.summary.winRate)}
                                        </span>
                                        <span>{formatSignedMoney(pipeline.history.summary.realizedPnl)} {resultUnit}</span>
                                        <div className='mobileTraderNestedBreakdownList'>
                                            {pipeline.strategies.map((strategy) => (
                                                <button
                                                    key={strategy.key}
                                                    type='button'
                                                    className={`mobileTraderNestedBreakdownCard ${selectedStrategyKey === strategy.key ? 'isActive' : ''}`.trim()}
                                                    onClick={() => {
                                                        setSelectedPipelineKey(pipeline.key)
                                                        setSelectedStrategyKey(strategy.key)
                                                        setSelectedChartKey(strategy.key)
                                                    }}
                                                >
                                                    <strong>{strategy.label}</strong>
                                                    <span>{strategy.signal.label} · {strategy.symbol} · {strategy.timeframe}</span>
                                                    <span>{strategy.history.summary.closedCount} closed · {strategy.history.summary.openCount} open · {formatPercent(strategy.history.summary.winRate)}</span>
                                                    <span>{formatSignedMoney(strategy.history.summary.realizedPnl)} {resultUnit}</span>
                                                </button>
                                            ))}
                                        </div>
                                    </article>
                                )) : (
                                    <div className='mobileTraderEmptyState'>No portfolio breakdown is available yet.</div>
                                )}
                            </div>
                        </section>
                        <section className='mobileTraderScreenCard'>
                            <div className='mobileTraderSleeveList'>
                                {selectedChartSleeveStates.length ? selectedChartSleeveStates.map((entry) => (
                                    <div key={String(entry?.sleeve_id || entry?.label)} className='mobileTraderSleeveCard'>
                                        <strong>{entry?.label || entry?.sleeve_id || 'Sleeve'}</strong>
                                        <span>{entry?.decision || 'hold'} · position {entry?.position ?? 0}</span>
                                        <span>{entry?.broker_position_side || 'flat'} · {entry?.broker_position_count ?? 0} broker pos</span>
                                        <span>{entry?.last_latency_ms ?? '—'} ms · {formatRelativeTimestamp(entry?.last_evaluated_at)}</span>
                                    </div>
                                )) : (
                                    <div className='mobileTraderEmptyState'>No evaluation recorded for the selected chart target yet.</div>
                                )}
                            </div>
                        </section>
                    </>
                ) : null}

                {activeScreen === 'strategies' ? (
                    <section className='mobileTraderScreenCard'>
                        <div className='mobileTraderRuntimeStrategyList'>
                            {pipelineBreakdowns.length ? pipelineBreakdowns.flatMap((pipeline) => pipeline.strategies.map((entry) => (
                                <article key={entry.key} className='mobileTraderRuntimeStrategyCard'>
                                    <div className='mobileTraderRuntimeStrategyHeader'>
                                        <strong>{entry.label}</strong>
                                        <span>{pipeline.label} · {entry.sourceStrategyId || 'Manual strategy'}</span>
                                    </div>
                                    <div className='mobileTraderRuntimeStrategyMeta'>
                                        <span>{normalizeTradeMarketValue(entry.symbol, '—')} · {normalizeTradeMarketValue(entry.timeframe, '—')}</span>
                                        <span>{entry.volume !== null ? `${Math.max(0.01, Number(entry.volume || 0.01) || 0.01).toFixed(2)} lot` : 'Variable volume'}</span>
                                        <span>{entry.history.summary.closedCount} closed · {entry.history.summary.openCount} open</span>
                                        <span>{formatSignedMoney(entry.history.summary.realizedPnl)} {resultUnit} · {formatPercent(entry.history.summary.winRate)}</span>
                                    </div>
                                    <MobileTradeStrategyReadOnly strategy={entry.strategy} />
                                </article>
                            ))) : (
                                <div className='mobileTraderEmptyState'>No runtime sleeves are currently loaded.</div>
                            )}
                        </div>
                    </section>
                ) : null}

                {activeScreen === 'pipeline' ? (
                    <section className='mobileTraderScreenCard'>
                        <div className='mobileTraderPipelineList'>
                            {pipelineRows.length ? pipelineRows.map((entry) => (
                                <div key={entry.id} className={`mobileTraderPipelineCard kind-${entry.kind} status-${entry.status}`.trim()}>
                                    <strong>{entry.label}</strong>
                                    <span>{entry.kind} · {entry.action || 'update'} {entry.side || ''}</span>
                                    <span>{entry.status || 'queued'} · {formatDateTimeBr24(entry.createdAt)}</span>
                                    {entry.message ? <small>{entry.message}</small> : null}
                                </div>
                            )) : (
                                <div className='mobileTraderEmptyState'>No intent or broker command is queued for the current scope right now.</div>
                            )}
                        </div>
                    </section>
                ) : null}

                {activeScreen === 'operations' ? (
                    <section className='mobileTraderScreenCard'>
                        <div className='mobileTraderOperationsList'>
                            {scopedHistory.rows.length ? scopedHistory.rows.slice(0, 20).map((entry) => (
                                <div key={entry.id} className={`mobileTraderOperationCard state-${entry.state}`.trim()}>
                                    <div className='mobileTraderOperationHead'>
                                        <strong>{String(entry.side || '—').trim().toUpperCase()}</strong>
                                        <span>{entry.state === 'open' ? 'Open' : 'Closed'}</span>
                                    </div>
                                    <span>{entry.strategyLabel} · {entry.symbol} · {entry.timeframe}</span>
                                    <span>{formatDateTimeBr24(entry.entryTime)} → {formatDateTimeBr24(entry.exitTime)}</span>
                                    <span>{formatPrice(entry.entryPrice)} → {formatPrice(entry.exitPrice)}</span>
                                    <span>{entry.volume !== null ? `${Number(entry.volume || 0).toFixed(2)} lot` : '—'}</span>
                                    <strong className={Number(entry.pnl || 0) >= 0 ? 'isProfit' : 'isLoss'}>
                                        {entry.pnl === null ? '—' : `${formatSignedMoney(entry.pnl)} ${resultUnit}`}
                                    </strong>
                                </div>
                            )) : (
                                <div className='mobileTraderEmptyState'>No open or closed trade was recorded for the current scope in the current history window.</div>
                            )}
                        </div>
                    </section>
                ) : null}
            </main>

            <div
                className={`mobileTraderDrawerBackdrop ${isDrawerOpen ? 'isOpen' : ''}`.trim()}
                onClick={() => setIsDrawerOpen(false)}
                aria-hidden='true'
            />
            <aside className={`mobileTraderDrawer ${isDrawerOpen ? 'isOpen' : ''}`.trim()}>
                <div className='mobileTraderDrawerHeader'>
                    <div>
                        <strong>Trader controls</strong>
                        <span>Live runtime, sleeves, pipeline and recent operations.</span>
                    </div>
                    <button
                        type='button'
                        className='mobileTraderDrawerClose'
                        onClick={() => setIsDrawerOpen(false)}
                        aria-label='Close mobile trader menu'
                    >
                        ×
                    </button>
                </div>

                <nav className='mobileTraderDrawerNav' aria-label='Mobile trader screens'>
                    {MOBILE_TRADER_SCREENS.map((screen) => (
                        <button
                            key={screen.id}
                            type='button'
                            className={`mobileTraderDrawerNavButton ${activeScreen === screen.id ? 'isActive' : ''}`.trim()}
                            onClick={() => openMobileScreen(screen.id)}
                        >
                            <strong>{screen.label}</strong>
                            <span>{screen.description}</span>
                        </button>
                    ))}
                </nav>
            </aside>
        </div>
    )
}

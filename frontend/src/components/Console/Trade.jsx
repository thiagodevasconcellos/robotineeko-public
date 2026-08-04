import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, fetchWithServerRetry as fetchWithRetry, readJsonResponse as readApiJsonResponse } from '../../api'
import { buildBackendIndicatorsPayload, normalizeChartSettings, normalizeIndicator } from '../../utils/chartSettings.jsx'
import {
    instantiateSavedPortfolioForTrader,
    rebuildTradePortfoliosFromSleeves,
    summarizeSavedPortfolio,
} from '../../utils/portfolioLibrary.js'
import { buildBrokerProfileQuery } from '../../utils/brokerProfiles.js'
import { buildStrategyAliasContextChartSettings, resolveStrategyAliasesInStrategy } from '../../utils/strategyAliases.jsx'
import { TIMEFRAME_OPTIONS } from '../../utils/timeframes.js'
import './Trade.css'

const SUB_TABS = [
    { id: 'setup', label: 'Manager' },
    { id: 'runtime', label: 'Monitor' },
    { id: 'audit', label: 'Audit' },
    { id: 'history', label: 'History' },
    { id: 'reconciliation', label: 'Compare' },
]
const TOKEN_REGEX = /\b([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]/g
const LITERAL_TOKEN_REGEX = /\b(True|False|and|or)\b/g
const HISTORY_RANGE_OPTIONS = [
    { value: 'today', label: 'Today' },
    { value: '7d', label: 'Last 7 days' },
    { value: '30d', label: 'Last 30 days' },
    { value: 'custom', label: 'Last X days' },
    { value: 'all', label: 'All time' },
]
const HISTORY_STATUS_OPTIONS = [
    { value: 'all', label: 'All trades' },
    { value: 'closed', label: 'Closed trades' },
    { value: 'open', label: 'Open trades' },
]
const RECONCILIATION_RANGE_OPTIONS = HISTORY_RANGE_OPTIONS
const STRATEGY_LIBRARY_FETCH_LIMIT = 500

function normalizeTradeMode(value) {
    const normalized = String(value || 'parallel_sleeves').trim().toLowerCase()
    return normalized === 'shared_pipe' ? 'shared_pipe' : 'parallel_sleeves'
}

function normalizeSameSymbolExecutionPolicy(value) {
    const normalized = String(value || 'independent').trim().toLowerCase()
    if (normalized === 'single_active_per_symbol' || normalized === 'block_conflicts') {
        return normalized
    }
    return 'independent'
}

function resolveEffectiveSameSymbolExecutionPolicy(mode, policy) {
    if (normalizeTradeMode(mode) === 'shared_pipe') {
        return 'single_active_per_symbol'
    }
    return normalizeSameSymbolExecutionPolicy(policy)
}

function formatSameSymbolExecutionPolicyLabel(policy, { mode = 'parallel_sleeves' } = {}) {
    const effectivePolicy = resolveEffectiveSameSymbolExecutionPolicy(mode, policy)
    const baseLabel = (() => {
        switch (effectivePolicy) {
        case 'single_active_per_symbol':
            return 'Single active per symbol'
        case 'block_conflicts':
            return 'Block conflicting sides'
        default:
            return 'Independent'
        }
    })()
    return normalizeTradeMode(mode) === 'shared_pipe'
        ? `${baseLabel} via shared pipe`
        : baseLabel
}

function normalizeSleeve(entry, index) {
    const strategy = entry?.strategy && typeof entry.strategy === 'object' ? entry.strategy : null
    return {
        id: String(entry?.id || `sleeve-${index + 1}`).trim() || `sleeve-${index + 1}`,
        label: String(entry?.label || `Sleeve ${index + 1}`).trim() || `Sleeve ${index + 1}`,
        enabled: entry?.enabled !== false,
        symbol: String(entry?.symbol || 'EURUSD').trim().toUpperCase() || 'EURUSD',
        timeframe: String(entry?.timeframe || 'M1').trim().toUpperCase() || 'M1',
        volume: Math.max(0.01, Number(entry?.volume || 0.01) || 0.01),
        volumeMode: String(entry?.volumeMode || entry?.volume_mode || 'fixed_volume').trim().toLowerCase() || 'fixed_volume',
        fixedVolume: entry?.fixedVolume ?? entry?.fixed_volume ?? null,
        baseVolume: entry?.baseVolume ?? entry?.base_volume ?? null,
        maxVolumeCap: entry?.maxVolumeCap ?? entry?.max_volume_cap ?? null,
        referenceCapital: entry?.referenceCapital ?? entry?.reference_capital ?? null,
        strategy,
        indicators: Array.isArray(entry?.indicators) ? entry.indicators : [],
        portfolioId: String(entry?.portfolioId || entry?.portfolio_id || '').trim(),
        portfolioLabel: String(entry?.portfolioLabel || entry?.portfolio_label || '').trim(),
        pipelineId: String(entry?.pipelineId || entry?.pipeline_id || '').trim(),
        pipelineLabel: String(entry?.pipelineLabel || entry?.pipeline_label || '').trim(),
        portfolioMode: String(entry?.portfolioMode || entry?.portfolio_mode || 'parallel_sleeves').trim().toLowerCase() || 'parallel_sleeves',
        sourceStrategyId: String(entry?.sourceStrategyId || entry?.source_strategy_id || '').trim(),
        strategyName: String(
            entry?.strategyName
            || entry?.strategy_name
            || entry?.sourceStrategyLabel
            || entry?.source_strategy_label
            || entry?.sourceStrategyId
            || entry?.source_strategy_id
            || '',
        ).trim(),
    }
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

function normalizeTradePortfolioStructureVersion(value) {
    return Number(value) >= 2 ? 2 : 1
}

function extractSleevesFromTradePortfolios(portfolios) {
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
            const portfolioMode = String(pipeline?.portfolioMode || pipeline?.portfolio_mode || 'parallel_sleeves').trim().toLowerCase() || 'parallel_sleeves'
            const sleeves = Array.isArray(pipeline?.sleeves) ? pipeline.sleeves : []
            sleeves.forEach((sleeve, sleeveIndex) => {
                entries.push(normalizeSleeve({
                    ...sleeve,
                    portfolioId: sleeve?.portfolioId || sleeve?.portfolio_id || portfolioId,
                    portfolioLabel: sleeve?.portfolioLabel || sleeve?.portfolio_label || portfolioLabel,
                    pipelineId: sleeve?.pipelineId || sleeve?.pipeline_id || pipelineId,
                    pipelineLabel: sleeve?.pipelineLabel || sleeve?.pipeline_label || pipelineLabel,
                    portfolioMode: sleeve?.portfolioMode || sleeve?.portfolio_mode || portfolioMode,
                }, entries.length + sleeveIndex))
            })
        })
    })

    return entries
}

function buildTradeSleeveId(index = 0) {
    return `trade-sleeve-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

function buildTradeSleeveLabel(strategy, index = 0) {
    const longOpen = String(strategy?.long?.openIf || '').trim()
    const shortOpen = String(strategy?.short?.openIf || '').trim()
    if (longOpen && shortOpen) {
        return `Sleeve ${index + 1} · Long/Short`
    }
    if (longOpen) {
        return `Sleeve ${index + 1} · Long`
    }
    if (shortOpen) {
        return `Sleeve ${index + 1} · Short`
    }
    return `Sleeve ${index + 1}`
}

function normalizeTradeMarketValue(value, fallback = '') {
    return String(value || fallback || '').trim().toUpperCase()
}

function extractSleeveIndicatorsFromStrategy(strategy) {
    return Array.isArray(strategy?.featureManifest?.indicators)
        ? strategy.featureManifest.indicators.map((indicator) => normalizeIndicator(indicator))
        : []
}

function readBenchmarkPrimaryMarketContext(benchmark) {
    const safeBenchmark = benchmark && typeof benchmark === 'object' ? benchmark : {}
    return {
        symbol: normalizeTradeMarketValue(safeBenchmark.symbol || safeBenchmark?.strategy?.symbol || ''),
        timeframe: normalizeTradeMarketValue(safeBenchmark.timeframe || safeBenchmark?.strategy?.timeframe || ''),
    }
}

function inferBenchmarkCompanionTimeframe(benchmark) {
    const companions = Array.isArray(benchmark?.strategies) ? benchmark.strategies : []
    const timeframes = Array.from(new Set(
        companions
            .map((entry) => normalizeTradeMarketValue(entry?.timeframe || ''))
            .filter(Boolean)
    ))
    return timeframes.length === 1 ? timeframes[0] : ''
}

function buildSleevesFromBenchmark(
    benchmark,
    {
        primarySymbol = '',
        primaryTimeframe = '',
        startIndex = 0,
        defaultVolume = 0.01,
    } = {},
) {
    const safeBenchmark = benchmark && typeof benchmark === 'object' ? benchmark : {}
    const benchmarkMarket = readBenchmarkPrimaryMarketContext(safeBenchmark)
    const safePrimarySymbol = normalizeTradeMarketValue(benchmarkMarket.symbol || primarySymbol, 'EURUSD')
    const safePrimaryTimeframe = normalizeTradeMarketValue(benchmarkMarket.timeframe || primaryTimeframe, 'M1')
    const benchmarkLabel = String(safeBenchmark.label || '').trim() || `Strategy #${safeBenchmark.id || startIndex + 1}`
    const entries = []

    if (safeBenchmark.strategy && typeof safeBenchmark.strategy === 'object') {
        entries.push({
            id: buildTradeSleeveId(startIndex + entries.length),
            label: benchmarkLabel || buildTradeSleeveLabel(safeBenchmark.strategy, startIndex + entries.length),
            enabled: true,
            symbol: safePrimarySymbol,
            timeframe: safePrimaryTimeframe,
            volume: Math.max(0.01, Number(defaultVolume || 0.01) || 0.01),
            strategy: cloneSerializable(safeBenchmark.strategy, safeBenchmark.strategy),
            indicators: extractSleeveIndicatorsFromStrategy(safeBenchmark.strategy),
            sourceStrategyId: String(safeBenchmark.id || `${benchmarkLabel}:primary`).trim(),
            strategyName: benchmarkLabel,
        })
    }

    if (Array.isArray(safeBenchmark.strategies)) {
        safeBenchmark.strategies.forEach((entry, index) => {
            if (!entry?.strategy || typeof entry.strategy !== 'object') {
                return
            }
            const absoluteIndex = startIndex + entries.length
            const companionLabel = String(entry?.label || '').trim() || buildTradeSleeveLabel(entry.strategy, absoluteIndex)
            entries.push({
                id: buildTradeSleeveId(absoluteIndex + index),
                label: companionLabel,
                enabled: entry?.enabled !== false,
                symbol: normalizeTradeMarketValue(entry?.symbol, safePrimarySymbol),
                timeframe: normalizeTradeMarketValue(entry?.timeframe, safePrimaryTimeframe),
                volume: Math.max(0.01, Number(entry?.volume || defaultVolume || 0.01) || 0.01),
                strategy: cloneSerializable(entry.strategy, entry.strategy),
                indicators: extractSleeveIndicatorsFromStrategy(entry.strategy),
                sourceStrategyId: String(entry?.sourceStrategyId || entry?.id || `${safeBenchmark.id || benchmarkLabel}:companion:${index + 1}`).trim(),
                strategyName: companionLabel,
            })
        })
    }

    return entries.map((entry, index) => normalizeSleeve({
        ...entry,
        id: entry.id || buildTradeSleeveId(startIndex + index),
    }, startIndex + index))
}

function extractRuntimePayload(payload) {
    if (payload?.trade_runtime && typeof payload.trade_runtime === 'object') {
        return payload.trade_runtime
    }
    return payload
}

function normalizeComparableStrategy(strategy) {
    const safe = strategy && typeof strategy === 'object' ? strategy : {}
    const long = safe.long && typeof safe.long === 'object' ? safe.long : {}
    const short = safe.short && typeof safe.short === 'object' ? safe.short : {}
    const other = safe.other && typeof safe.other === 'object' ? safe.other : {}
    const featureManifest = safe.featureManifest && typeof safe.featureManifest === 'object' ? safe.featureManifest : {}

    return {
        long: {
            openPrice: String(long.openPrice || ''),
            closePrice: String(long.closePrice || ''),
            openIf: String(long.openIf || ''),
            closeIf: String(long.closeIf || ''),
            gainPrice: String(long.gainPrice || ''),
            lossPrice: String(long.lossPrice || ''),
            trailingPrice: String(long.trailingPrice || ''),
        },
        short: {
            openPrice: String(short.openPrice || ''),
            closePrice: String(short.closePrice || ''),
            openIf: String(short.openIf || ''),
            closeIf: String(short.closeIf || ''),
            gainPrice: String(short.gainPrice || ''),
            lossPrice: String(short.lossPrice || ''),
            trailingPrice: String(short.trailingPrice || ''),
        },
        other: {
            allowInversion: Boolean(other.allowInversion),
            priority: String(other.priority || 'Short'),
        },
        featureManifest: {
            indicators: Array.isArray(featureManifest.indicators)
                ? featureManifest.indicators.map((indicator) => normalizeIndicator(indicator))
                : [],
        },
    }
}

function normalizeComparableIndicators(indicators) {
    if (!Array.isArray(indicators)) {
        return []
    }

    return indicators.map((indicator) => {
        const safe = indicator && typeof indicator === 'object' ? indicator : {}
        return {
            name: String(safe.name || ''),
            params: Array.isArray(safe.params) ? [...safe.params] : [],
            alias: String(safe.alias || ''),
        }
    })
}

function buildComparableTradeConfig(value) {
    const source = value && typeof value === 'object' ? value : {}
    const sourcePortfolios = Array.isArray(source?.portfolios) ? cloneSerializable(source.portfolios, []) : []
    const rawSleeves = Array.isArray(source?.sleeves) && source.sleeves.length
        ? source.sleeves
        : extractSleevesFromTradePortfolios(sourcePortfolios)
    const sleeves = Array.isArray(rawSleeves) ? rawSleeves.map((entry, index) => normalizeSleeve(entry, index)).map((entry) => ({
        id: entry.id,
        label: entry.label,
        enabled: entry.enabled !== false,
        symbol: entry.symbol,
        timeframe: entry.timeframe,
        volume: Math.max(0.01, Number(entry.volume || 0.01) || 0.01),
        volumeMode: entry.volumeMode,
        fixedVolume: entry.fixedVolume,
        baseVolume: entry.baseVolume,
        maxVolumeCap: entry.maxVolumeCap,
        referenceCapital: entry.referenceCapital,
        portfolioId: entry.portfolioId,
        portfolioLabel: entry.portfolioLabel,
        pipelineId: entry.pipelineId,
        pipelineLabel: entry.pipelineLabel,
        portfolioMode: entry.portfolioMode,
        sourceStrategyId: String(entry.sourceStrategyId || '').trim(),
        strategyName: String(entry.strategyName || entry.sourceStrategyId || '').trim(),
        strategy: normalizeComparableStrategy(entry.strategy),
        indicators: normalizeComparableIndicators(entry.indicators),
    })) : []

    return {
        mode: String(source?.mode || 'parallel_sleeves'),
        executionMode: String(source?.executionMode || source?.execution_mode || 'paper'),
        activeBrokerProfileId: String(
            source?.activeBrokerProfileId
            || source?.brokerProfileId
            || source?.broker_profile_id
            || '',
        ).trim(),
        activeBrokerProfileLabel: String(
            source?.activeBrokerProfileLabel
            || source?.brokerProfileLabel
            || source?.broker_profile_label
            || '',
        ).trim(),
        sameSymbolExecutionPolicy: String(source?.sameSymbolExecutionPolicy || source?.same_symbol_execution_policy || 'independent'),
        portfolioStructureVersion: normalizeTradePortfolioStructureVersion(
            source?.portfolioStructureVersion || source?.portfolio_structure_version
        ),
        portfolios: sourcePortfolios,
        signalValiditySeconds: Math.max(0, Number(source?.signalValiditySeconds || source?.signal_validity_seconds || 10) || 0),
        latencyBudgetMs: Math.max(1, Number(source?.latencyBudgetMs || source?.latency_budget_ms || 150) || 150),
        sleeves,
    }
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

function ReadOnlyExpression({ value = '', singleLine = false }) {
    const parts = parseExpressionParts(value)
    return (
        <div className={`tradeExpressionPreview ${singleLine ? 'singleLine' : ''}`.trim()}>
            <div className='tradeExpressionPreviewText'>
                {parts.map((part, index) => (
                    part.type === 'token'
                        ? (
                            <span
                                key={`token-${part.start}-${index}`}
                                className={`tradeStrategyToken ${part.tokenType === 'literal' ? 'isLiteral' : ''}`}
                                title={part.raw}
                            >
                                <span className='tradeStrategyTokenLabel'>{part.name}</span>
                                {part.tokenType !== 'literal' ? (
                                    <span className='tradeStrategyTokenIndex'>[{part.index}]</span>
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

function TradeStrategyReadOnly({ strategy }) {
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
        <div className='tradeStrategyReadOnly'>
            <div className='tradeStrategyReadOnlyMeta'>
                <div className='tradeStrategyReadOnlyMetaItem'>
                    <strong>Allow inversion</strong>
                    <span>{safeStrategy?.other?.allowInversion ? 'True' : 'False'}</span>
                </div>
                <div className='tradeStrategyReadOnlyMetaItem'>
                    <strong>Priority</strong>
                    <span>{safeStrategy?.other?.priority || 'Short'}</span>
                </div>
            </div>
            <div className='tradeStrategyReadOnlySections'>
                {sections.map((section) => (
                    <section key={section.id} className='tradeStrategySection'>
                        <div className='tradeStrategySectionTitle'>{section.title}</div>
                        <div className='tradeStrategyFieldList'>
                            {section.fields.map(([label, fieldValue]) => (
                                <div key={`${section.id}-${label}`} className='tradeStrategyFieldItem'>
                                    <div className='tradeStrategyFieldLabel'>{label}</div>
                                    <ReadOnlyExpression value={fieldValue} singleLine={label.includes('price')} />
                                </div>
                            ))}
                        </div>
                    </section>
                ))}
            </div>
        </div>
    )
}

function MonitorStatCard({ label, value, emphasis = false }) {
    return (
        <div className={`tradeInfoCard ${emphasis ? 'emphasis' : ''}`.trim()}>
            <strong>{label}</strong>
            <span className='tradeInfoValue'>{value}</span>
        </div>
    )
}

function ReconciliationOperationCard({
    verdict,
    index,
    primary,
    secondary = '',
    details = [],
    note = '',
}) {
    const verdictMeta = buildComparisonVerdictMeta(verdict)
    const visibleDetails = Array.isArray(details) ? details.filter(Boolean) : []
    const visibleNote = String(note || '').trim()

    return (
        <article className={`tradeReconciliationOperationCard is-${verdictMeta.tone}`.trim()}>
            <div className='tradeReconciliationOperationCardHeader'>
                <span className={`tradeOperationalPill is-${verdictMeta.tone}`.trim()}>
                    {verdictMeta.label}
                </span>
                <span className='tradeReconciliationOperationIndex'>Slot #{index || '—'}</span>
            </div>
            <strong className='tradeReconciliationOperationPrimary'>{primary || '—'}</strong>
            {secondary ? <span className='tradeReconciliationOperationSecondary'>{secondary}</span> : null}
            {visibleDetails.length ? (
                <div className='tradeReconciliationOperationMeta'>
                    {visibleDetails.map((detail) => (
                        <span key={`${index || 'x'}-${detail}`}>{detail}</span>
                    ))}
                </div>
            ) : null}
            {visibleNote ? <small className='tradeReconciliationOperationNote'>{visibleNote}</small> : null}
        </article>
    )
}

function formatTradeAccountMode(value, { isLiveExecutionMode = false } = {}) {
    const normalized = String(value || '').trim().toLowerCase()
    if (!isLiveExecutionMode) {
        return 'Not in use'
    }
    if (!normalized) {
        return 'Waiting for MT5 heartbeat'
    }
    return `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)}`
}

function formatTradeHedgeSupport(value, { isLiveExecutionMode = false } = {}) {
    if (!isLiveExecutionMode) {
        return 'Not in use'
    }
    if (typeof value !== 'boolean') {
        return 'Waiting for MT5 heartbeat'
    }
    return value ? 'Allowed' : 'Not allowed'
}

function buildSleeveOperationalState(sleeveState, orderIntents = [], orderCommands = []) {
    const safeSleeveId = String(sleeveState?.sleeve_id || '')
    const sleeveIntents = (Array.isArray(orderIntents) ? orderIntents : []).filter((entry) => String(entry?.sleeve_id || '') === safeSleeveId)
    const sleeveCommands = (Array.isArray(orderCommands) ? orderCommands : []).filter((entry) => String(entry?.sleeve_id || '') === safeSleeveId)
    const activeCommand = sleeveCommands.find((entry) => ['queued', 'claimed', 'acknowledged'].includes(String(entry?.status || '').toLowerCase()))
    const activeIntent = sleeveIntents.find((entry) => ['queued', 'broker_queued', 'broker_claimed', 'broker_acknowledged', 'dispatch_blocked'].includes(String(entry?.status || '').toLowerCase()))
    const reconciliationStatus = String(sleeveState?.reconciliation_status || '').trim().toLowerCase()

    if (activeCommand) {
        const action = String(activeCommand?.action || '').trim().toLowerCase()
        const commandStatus = String(activeCommand?.status || '').trim().toLowerCase()
        if (action === 'open') {
            return {
                label: commandStatus === 'acknowledged' ? 'Broker opening' : 'Opening pending',
                tone: 'pending',
                detail: activeCommand?.message || 'An open command is already in the broker pipeline.',
            }
        }
        if (action === 'close') {
            return {
                label: commandStatus === 'acknowledged' ? 'Broker closing' : 'Closing pending',
                tone: 'pending',
                detail: activeCommand?.message || 'A close command is already in the broker pipeline.',
            }
        }
    }

    if (activeIntent) {
        const action = String(activeIntent?.action || '').trim().toLowerCase()
        const intentStatus = String(activeIntent?.status || '').trim().toLowerCase()
        if (intentStatus === 'dispatch_blocked') {
            return {
                label: 'Dispatch blocked',
                tone: 'warning',
                detail: activeIntent?.rejection_message || activeIntent?.message || 'The strategy produced an action, but the broker gate is still blocked.',
            }
        }
        if (action === 'open') {
            return {
                label: 'Open intent pending',
                tone: 'pending',
                detail: 'The strategy asked to open, but the command has not fully materialized yet.',
            }
        }
        if (action === 'close') {
            return {
                label: 'Close intent pending',
                tone: 'pending',
                detail: 'The strategy asked to close, but the command has not fully materialized yet.',
            }
        }
    }

    if (reconciliationStatus === 'missing_broker_position') {
        return {
            label: 'Broker sync issue',
            tone: 'issue',
            detail: sleeveState?.reconciliation_detail || 'The runtime expected a broker position that was not found.',
        }
    }
    if (reconciliationStatus === 'orphan_broker_position' || reconciliationStatus === 'conflicting_broker_position' || reconciliationStatus === 'blocked_multiple_positions' || reconciliationStatus === 'orphan_multiple_positions' || reconciliationStatus === 'conflicting_multiple_positions') {
        return {
            label: 'Reconciliation issue',
            tone: 'issue',
            detail: sleeveState?.reconciliation_detail || 'Broker and runtime state are divergent.',
        }
    }
    if (reconciliationStatus === 'match_open' || reconciliationStatus === 'match_open_multiple') {
        return {
            label: 'Position synchronized',
            tone: 'healthy',
            detail: sleeveState?.reconciliation_detail || 'Runtime and broker agree on the live open position.',
        }
    }
    return {
        label: 'Healthy / flat',
        tone: 'healthy',
        detail: sleeveState?.reconciliation_detail || 'No broker position is open and the runtime also expects flat.',
    }
}

function formatTradeTimestamp(value) {
    if (!value) {
        return '—'
    }
    try {
        return new Date(Number(value) * 1000).toLocaleString()
    } catch {
        return '—'
    }
}

function formatTradeMoney(value) {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) {
        return '—'
    }
    return parsed.toFixed(2)
}

function formatTradeSide(value) {
    const normalized = String(value || '').trim()
    return normalized ? normalized.toUpperCase() : '—'
}

function formatTradeStateLabel(value) {
    const normalized = String(value || '').trim().toLowerCase()
    if (!normalized) {
        return '—'
    }
    return `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)}`
}

function formatTradePrice(value) {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) {
        return '—'
    }
    return parsed.toFixed(5)
}

function buildTradeHistoryView(rows = [], statusFilter = 'all', sleeveStates = []) {
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
        const isOpen = !closingEntry && liveCycleIds.has(cycleId)

        consolidated.push({
            id: `${isOpen ? 'open' : 'closed'}-${cycleId}`,
            cycleId,
            state: isOpen ? 'open' : 'closed',
            strategyLabel: baseEntry?.sleeve_label || baseEntry?.source_strategy_id || '—',
            symbol: baseEntry?.symbol || '—',
            timeframe: baseEntry?.timeframe || '—',
            side: baseEntry?.side || openingEntry?.side || '—',
            brokerPositionTicket: baseEntry?.broker_position_ticket || openingEntry?.broker_position_ticket || closingEntry?.broker_position_ticket || null,
            exitReason: closingEntry?.exit_reason || '—',
            volume: closingEntry?.fill_volume ?? openingEntry?.fill_volume ?? null,
            entryTime: openingEntry?.filled_at || openingEntry?.created_at || null,
            exitTime: closingEntry?.filled_at || closingEntry?.created_at || null,
            entryPrice: openingEntry?.fill_price ?? null,
            exitPrice: closingEntry?.fill_price ?? null,
            pnl: closingEntry ? readRealizedPnl(closingEntry) : null,
            commission: closingEntry ? Number(closingEntry?.commission || 0) : 0,
            swap: closingEntry ? Number(closingEntry?.swap || 0) : 0,
            message: closingEntry?.message || openingEntry?.message || '',
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
                exitReason: entry?.exit_reason || '—',
                volume: entry?.fill_volume ?? openingEntry?.fill_volume ?? null,
                entryTime: openingEntry?.filled_at || openingEntry?.created_at || null,
                exitTime: entry?.filled_at || entry?.created_at || null,
                entryPrice: openingEntry?.fill_price ?? null,
                exitPrice: entry?.fill_price ?? null,
                pnl: readRealizedPnl(entry),
                commission: Number(entry?.commission || 0),
                swap: Number(entry?.swap || 0),
                message: entry?.message || '',
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
        if (!openBrokerKeys.has(queueKey) || !queue.length) {
            continue
        }
        const entry = queue[queue.length - 1]
        consolidated.push({
            id: `open-${entry?.id || entry?.command_id || Math.random().toString(36).slice(2, 8)}`,
            state: 'open',
            strategyLabel: entry?.sleeve_label || entry?.source_strategy_id || '—',
            symbol: entry?.symbol || '—',
            timeframe: entry?.timeframe || '—',
            side: entry?.side || '—',
            brokerPositionTicket: entry?.broker_position_ticket || null,
            exitReason: '—',
            volume: entry?.fill_volume ?? null,
            entryTime: entry?.filled_at || entry?.created_at || null,
            exitTime: null,
            entryPrice: entry?.fill_price ?? null,
            exitPrice: null,
            pnl: null,
            commission: 0,
            swap: 0,
            message: entry?.message || '',
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

    const normalizedStatusFilter = String(statusFilter || 'all').trim().toLowerCase()
    const filteredRows = consolidated.filter((entry) => {
        if (normalizedStatusFilter === 'open') {
            return entry.state === 'open'
        }
        if (normalizedStatusFilter === 'closed') {
            return entry.state === 'closed'
        }
        return true
    })

    const closedRows = filteredRows.filter((entry) => entry.state === 'closed')
    const openRows = filteredRows.filter((entry) => entry.state === 'open')
    const winningRows = closedRows.filter((entry) => Number(entry.pnl || 0) > 0)
    const grossProfit = closedRows.reduce((sum, entry) => sum + Number(entry.pnl || 0), 0)
    const commissionTotal = closedRows.reduce((sum, entry) => sum + Number(entry.commission || 0), 0)
    const swapTotal = closedRows.reduce((sum, entry) => sum + Number(entry.swap || 0), 0)

    return {
        rows: filteredRows,
        summary: {
            trade_count: filteredRows.length,
            closed_count: closedRows.length,
            open_count: openRows.length,
            win_count: winningRows.length,
            win_rate: closedRows.length ? (winningRows.length / closedRows.length) : 0,
            gross_profit: grossProfit,
            commission_total: commissionTotal,
            swap_total: swapTotal,
            realized_pnl: grossProfit,
        },
    }
}

function formatRelativeTimestamp(value) {
    if (!value) {
        return '—'
    }
    try {
        return new Date(Number(value) * 1000).toLocaleTimeString()
    } catch {
        return '—'
    }
}

function formatDelaySeconds(value) {
    const seconds = Number(value)
    if (!Number.isFinite(seconds)) {
        return '—'
    }
    if (seconds < 1) {
        return `${Math.round(seconds * 1000)} ms`
    }
    return `${seconds.toFixed(1)} s`
}

function buildComparisonVerdictMeta(verdict) {
    const safeVerdict = String(verdict || '').trim().toLowerCase()
    if (safeVerdict === 'matched') {
        return { label: 'Matched', tone: 'healthy' }
    }
    if (safeVerdict === 'rejected') {
        return { label: 'Rejected', tone: 'warning' }
    }
    if (safeVerdict === 'unexpected') {
        return { label: 'Unexpected', tone: 'issue' }
    }
    if (safeVerdict === 'side_mismatch') {
        return { label: 'Side mismatch', tone: 'issue' }
    }
    return { label: 'Missed', tone: 'issue' }
}

const COMPARISON_VERDICT_LEGEND = [
    {
        verdict: 'matched',
        description: 'The expected operation has a corresponding executed trade with compatible side and timing.',
    },
    {
        verdict: 'rejected',
        description: 'The strategy wanted to trade, but the live path explicitly rejected the operation instead of filling it.',
    },
    {
        verdict: 'missed',
        description: 'The strategy expected an operation, but no corresponding executed trade was found.',
    },
    {
        verdict: 'unexpected',
        description: 'A trade was executed even though the replayed strategy did not expect one at that point.',
    },
    {
        verdict: 'side_mismatch',
        description: 'A trade exists around the same event, but the live side does not match the expected side.',
    },
]

function readExpectedComparisonTimestamp(entry) {
    return Number(entry?.expected?.expected_entry_time || entry?.expected?.expected_exit_time || 0) || 0
}

function readActualComparisonTimestamp(entry) {
    return Number(entry?.actual?.actual_entry_time || entry?.actual?.actual_exit_time || 0) || 0
}

function readComparisonDisplayTimestamp(entry) {
    return Math.max(readExpectedComparisonTimestamp(entry), readActualComparisonTimestamp(entry))
}

function isInvalidStopsMessage(value) {
    return String(value || '').toLowerCase().includes('invalid stops')
}

function InvalidStopsToken({ detail = '' }) {
    if (!isInvalidStopsMessage(detail)) {
        return null
    }
    return (
        <span className='tradeOperationalPill is-issue tradeInvalidStopsToken' title={detail || 'Invalid stops'}>
            invalid stop
        </span>
    )
}

function buildSparklineGeometry(values, width = 320, height = 120) {
    const safeValues = Array.isArray(values) ? values.map((value) => Number(value)).filter(Number.isFinite) : []
    if (!safeValues.length) {
        return {
            values: [],
            min: null,
            max: null,
            points: [],
            path: '',
        }
    }

    const min = Math.min(...safeValues)
    const max = Math.max(...safeValues)
    const range = max - min || 1
    const points = safeValues.map((value, index) => {
        const x = safeValues.length === 1 ? width / 2 : (index / (safeValues.length - 1)) * width
        const y = height - (((value - min) / range) * height)
        return {
            index,
            value,
            x,
            y,
        }
    })

    return {
        values: safeValues,
        min,
        max,
        points,
        path: points.map((point, index) => (
            `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
        )).join(' '),
    }
}

function RuntimeMiniChart({ title, subtitle, values = [], labels = [], valueFormatter = (value) => String(value ?? '—'), tone = 'default' }) {
    const chartWidth = 320
    const chartHeight = 120
    const chartAxisWidth = tone === 'latency' ? 44 : 0
    const geometry = buildSparklineGeometry(values, chartWidth, chartHeight)
    const safeValues = geometry.values
    const lastValue = safeValues.length ? safeValues[safeValues.length - 1] : null
    const minValue = geometry.min
    const maxValue = geometry.max
    const path = geometry.path
    const lastLabel = Array.isArray(labels) && labels.length ? labels[labels.length - 1] : '—'
    const [hoverIndex, setHoverIndex] = useState(null)
    const hoveredPoint = Number.isInteger(hoverIndex) ? geometry.points[hoverIndex] || null : null
    const hoveredLabel = hoveredPoint ? labels?.[hoveredPoint.index] || '—' : '—'

    function handleChartPointerMove(event) {
        if (!geometry.points.length) {
            return
        }
        const bounds = event.currentTarget.getBoundingClientRect()
        if (!bounds.width) {
            return
        }
        const relativeX = ((event.clientX - bounds.left) / bounds.width) * chartWidth
        let closestPoint = geometry.points[0]
        let closestDistance = Math.abs(relativeX - closestPoint.x)

        for (const point of geometry.points) {
            const distance = Math.abs(relativeX - point.x)
            if (distance < closestDistance) {
                closestPoint = point
                closestDistance = distance
            }
        }

        setHoverIndex(closestPoint.index)
    }

    function handleChartPointerLeave() {
        setHoverIndex(null)
    }

    return (
        <section className={`tradePanel tradeMonitorChartCard tone-${tone}`.trim()}>
            <div className='tradeMonitorSectionHeader'>
                <div>
                    <strong>{title}</strong>
                    <span>{subtitle}</span>
                </div>
            </div>
            <div className='tradeMiniChartMeta'>
                <div>
                    <strong>{lastValue == null ? '—' : valueFormatter(lastValue)}</strong>
                    <span>Latest</span>
                </div>
                <div>
                    <strong>{minValue == null ? '—' : valueFormatter(minValue)}</strong>
                    <span>Min</span>
                </div>
                <div>
                    <strong>{maxValue == null ? '—' : valueFormatter(maxValue)}</strong>
                    <span>Max</span>
                </div>
                <div>
                    <strong>{lastLabel || '—'}</strong>
                    <span>Window end</span>
                </div>
            </div>
            <div
                className='tradeMiniChartCanvas'
                onMouseMove={handleChartPointerMove}
                onMouseLeave={handleChartPointerLeave}
            >
                {path ? (
                    <svg viewBox={`0 0 ${chartWidth + chartAxisWidth} ${chartHeight}`} preserveAspectRatio='none' aria-hidden='true'>
                        {tone === 'latency' ? (
                            <>
                                <line className='tradeMiniChartAxisLine' x1={chartWidth} y1='0' x2={chartWidth} y2={chartHeight} />
                                <text className='tradeMiniChartAxisLabel' x={chartWidth + 6} y='10'>
                                    {maxValue == null ? '—' : valueFormatter(maxValue)}
                                </text>
                                <text className='tradeMiniChartAxisLabel' x={chartWidth + 6} y={chartHeight / 2 + 4}>
                                    {lastValue == null ? '—' : valueFormatter(lastValue)}
                                </text>
                                <text className='tradeMiniChartAxisLabel' x={chartWidth + 6} y={chartHeight - 4}>
                                    {minValue == null ? '—' : valueFormatter(minValue)}
                                </text>
                            </>
                        ) : null}
                        {hoveredPoint ? (
                            <>
                                <line className='tradeMiniChartCrosshair' x1={hoveredPoint.x} y1='0' x2={hoveredPoint.x} y2={chartHeight} />
                                <line className='tradeMiniChartCrosshair' x1='0' y1={hoveredPoint.y} x2={chartWidth} y2={hoveredPoint.y} />
                                <circle className='tradeMiniChartCrosshairPoint' cx={hoveredPoint.x} cy={hoveredPoint.y} r='3' />
                                <g className='tradeMiniChartCrosshairTag' transform={`translate(${Math.max(4, Math.min(chartWidth - 86, hoveredPoint.x - 40))}, ${Math.max(14, hoveredPoint.y - 10)})`}>
                                    <rect x='0' y='-14' rx='4' ry='4' width='82' height='18' />
                                    <text x='6' y='-2'>
                                        {valueFormatter(hoveredPoint.value)}
                                    </text>
                                </g>
                                <g className='tradeMiniChartCrosshairTimeTag' transform={`translate(${Math.max(4, Math.min(chartWidth - 70, hoveredPoint.x - 34))}, ${chartHeight - 4})`}>
                                    <rect x='0' y='-14' rx='4' ry='4' width='68' height='18' />
                                    <text x='6' y='-2'>
                                        {hoveredLabel}
                                    </text>
                                </g>
                            </>
                        ) : null}
                        <path d={path} />
                    </svg>
                ) : (
                    <div className='tradeEmpty'>No data in the current window.</div>
                )}
            </div>
        </section>
    )
}

export function Trade({
    authToken = '',
    isGuest = false,
    tradeState,
    setTradeState,
    liveTradeRuntime = null,
    setLiveTradeRuntime,
    chartSettings,
    onLogEvent,
    activeBrokerProfileId = '',
    activeBrokerProfileLabel = '',
}) {
    const [activeSubTab, setActiveSubTab] = useState(String(tradeState?.selectedTab || 'setup'))
    const [runtimeState, setRuntimeState] = useState(extractRuntimePayload(liveTradeRuntime))
    const [isSyncing, setIsSyncing] = useState(false)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [requestError, setRequestError] = useState('')
    const [historyState, setHistoryState] = useState({
        loading: false,
        error: '',
        rows: [],
        summary: null,
    })
    const [reconciliationState, setReconciliationState] = useState({
        loading: false,
        error: '',
        rows: [],
        summary: null,
        expectedRows: [],
        actualRows: [],
    })
    const [auditLogPage, setAuditLogPage] = useState(0)
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
    const [benchmarkImportDraft, setBenchmarkImportDraft] = useState(null)
    const guestRestrictionMessage = 'Guest demo can inspect Trader, but cannot save runtime config, arm the bot, evaluate, process intents, reconcile, or reset broker commands.'
    const lastHistoryAutoRefreshAtRef = useRef(0)
    const logEventRef = useRef(onLogEvent)
    const lastStrategyLibraryBootstrapKeyRef = useRef('')
    const sleeves = useMemo(
        () => (Array.isArray(tradeState?.sleeves) ? tradeState.sleeves.map(normalizeSleeve) : []),
        [tradeState?.sleeves],
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
                    entry?.symbol,
                    entry?.timeframe,
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
    const selectedStrategyPrimaryMarket = useMemo(
        () => readBenchmarkPrimaryMarketContext(selectedStrategyLibraryItem),
        [selectedStrategyLibraryItem],
    )
    const liveAuditEvents = useMemo(
        () => (Array.isArray(runtimeState?.audit_events)
            ? runtimeState.audit_events
            : Array.isArray(tradeState?.audit?.events)
                ? tradeState.audit.events
                : []),
        [runtimeState?.audit_events, tradeState?.audit?.events],
    )
    const runtimeMetrics = runtimeState?.metrics && typeof runtimeState.metrics === 'object'
        ? runtimeState.metrics
        : {}
    const orderIntents = useMemo(
        () => (Array.isArray(runtimeState?.order_intents) ? runtimeState.order_intents : []),
        [runtimeState?.order_intents],
    )
    const orderCommands = useMemo(
        () => (Array.isArray(runtimeState?.order_commands) ? runtimeState.order_commands : []),
        [runtimeState?.order_commands],
    )
    const runtimeStatus = String(runtimeState?.status || tradeState?.runtime?.health || 'idle')
    const isRuntimeArmed = Boolean(runtimeState?.armed)
    const isLiveDispatchArmed = Boolean(runtimeState?.live_dispatch_armed)
    const isLiveExecutionMode = String(runtimeState?.execution_mode || tradeState?.executionMode || 'paper') === 'live_mt5'
    const brokerAccountMode = String(runtimeState?.broker_account_position_mode || '').trim().toLowerCase()
    const brokerHedgeAllowed = typeof runtimeState?.broker_account_hedge_allowed === 'boolean'
        ? runtimeState.broker_account_hedge_allowed
        : null
    const brokerAccountModeLabel = formatTradeAccountMode(brokerAccountMode, { isLiveExecutionMode })
    const brokerHedgeSupportLabel = formatTradeHedgeSupport(brokerHedgeAllowed, { isLiveExecutionMode })
    const isNettingBrokerAccount = isLiveExecutionMode && (brokerAccountMode === 'netting' || brokerHedgeAllowed === false)
    const isHedgingBrokerAccount = isLiveExecutionMode && (brokerAccountMode === 'hedging' || brokerHedgeAllowed === true)
    const brokerAccountBannerTone = isNettingBrokerAccount
        ? 'warning'
        : isHedgingBrokerAccount
            ? 'healthy'
            : 'info'
    const brokerAccountBannerTitle = isNettingBrokerAccount
        ? 'Connected MT5 account is netting.'
        : isHedgingBrokerAccount
            ? 'Connected MT5 account supports hedging.'
            : 'Waiting for MT5 account-mode confirmation.'
    const brokerAccountBannerDetail = isNettingBrokerAccount
        ? 'Opposite-direction sleeves on the same symbol can collapse into one net broker position even when the runtime keeps sleeve ownership separate.'
        : isHedgingBrokerAccount
            ? 'Same-symbol opposite-direction sleeves can coexist across different sleeves, but reconciliation still expects at most one broker position per sleeve.'
            : 'The bridge heartbeat has not yet confirmed whether the connected MT5 account is hedging or netting.'
    const localTradeMode = normalizeTradeMode(tradeState?.mode || 'parallel_sleeves')
    const runtimeTradeMode = normalizeTradeMode(runtimeState?.mode || tradeState?.mode || 'parallel_sleeves')
    const runtimeSameSymbolPolicy = normalizeSameSymbolExecutionPolicy(
        runtimeState?.same_symbol_execution_policy || tradeState?.sameSymbolExecutionPolicy || 'independent'
    )
    const localSameSymbolPolicyHelp = localTradeMode === 'shared_pipe'
        ? 'Shared pipe already enforces one open same-symbol lane. This selector is kept so your preferred policy is ready if you switch back to Parallel sleeves.'
        : 'Independent keeps same-symbol sleeves free to coexist. Single active per symbol blocks any new same-symbol open if another sleeve is already active there. Block conflicting sides allows same direction but blocks opposite-direction opens on the same symbol.'
    const runtimeNeedsSleeves = Boolean(isRuntimeArmed && !(runtimeState?.sleeves || []).length)
    const marketFeed = runtimeState?.market_feed && typeof runtimeState.market_feed === 'object'
        ? runtimeState.market_feed
        : {}
    const activeSleeveStates = useMemo(
        () => (runtimeState?.sleeve_states && typeof runtimeState.sleeve_states === 'object'
            ? Object.values(runtimeState.sleeve_states)
            : []),
        [runtimeState?.sleeve_states],
    )
    const sleeveOperationalStates = activeSleeveStates.map((entry) => ({
        sleeveId: entry?.sleeve_id || '',
        ...buildSleeveOperationalState(entry, orderIntents, orderCommands),
    }))
    const primarySleeveOperationalState = sleeveOperationalStates[0] || null
    const currentSessionLabel = runtimeState?.last_armed_at
        ? new Date(Number(runtimeState.last_armed_at) * 1000).toLocaleString()
        : 'Not armed yet'
    const primarySleeveError = activeSleeveStates.find((entry) => String(entry?.status || '').toLowerCase() === 'error')?.last_error || ''
    const primaryRuntimeError = String(runtimeState?.last_error || primarySleeveError || '').trim()
    const localComparableConfig = useMemo(
        () => buildComparableTradeConfig({
            ...tradeState,
            sleeves,
        }),
        [sleeves, tradeState],
    )
    const savedComparableConfig = useMemo(
        () => buildComparableTradeConfig(runtimeState || {}),
        [runtimeState],
    )
    const isRuntimeConfigSaved = JSON.stringify(localComparableConfig) === JSON.stringify(savedComparableConfig)
    const draftSleeveCount = localComparableConfig.sleeves.length
    const draftPortfolioCount = Array.isArray(localComparableConfig.portfolios) ? localComparableConfig.portfolios.length : 0
    const runtimeSleeveCount = Array.isArray(runtimeState?.sleeves) ? runtimeState.sleeves.length : 0
    const isLocalLiveExecutionMode = localComparableConfig.executionMode === 'live_mt5'
    const liveDispatchDisabledReason = useMemo(() => {
        if (isGuest) {
            return guestRestrictionMessage
        }
        if (isLiveExecutionMode) {
            return ''
        }
        if (isLocalLiveExecutionMode && !isRuntimeConfigSaved) {
            return 'This Live MT5 setup is still only a workspace draft. Click Save Runtime first so the backend runtime leaves paper mode.'
        }
        if (!draftSleeveCount && !draftPortfolioCount && !runtimeSleeveCount) {
            return 'Add at least one saved strategy or portfolio to Trader before trying to enable live MT5 dispatch.'
        }
        if (!isLocalLiveExecutionMode) {
            return 'Switch Execution Path to Live MT5 and click Save Runtime first.'
        }
        return 'The backend runtime is still not using Live MT5. Save Runtime first.'
    }, [
        draftPortfolioCount,
        draftSleeveCount,
        guestRestrictionMessage,
        isGuest,
        isLiveExecutionMode,
        isLocalLiveExecutionMode,
        isRuntimeConfigSaved,
        runtimeSleeveCount,
    ])
    const hasAnyRuntimeSleeves = sleeves.length > 0 || (Array.isArray(runtimeState?.sleeves) && runtimeState.sleeves.length > 0)
    const historyFilters = {
        rangeKey: String(tradeState?.historyFilters?.rangeKey || '7d'),
        customDays: Math.max(1, Number(tradeState?.historyFilters?.customDays || 7) || 7),
        strategyFilter: String(tradeState?.historyFilters?.strategyFilter || ''),
        symbolFilter: String(tradeState?.historyFilters?.symbolFilter || ''),
        statusFilter: String(tradeState?.historyFilters?.statusFilter || 'all'),
    }
    const reconciliationFilters = {
        rangeKey: String(tradeState?.reconciliationFilters?.rangeKey || '7d'),
        customDays: Math.max(1, Number(tradeState?.reconciliationFilters?.customDays || 7) || 7),
        strategyFilter: String(tradeState?.reconciliationFilters?.strategyFilter || ''),
    }
    const reconciliationStrategyOptions = useMemo(() => {
        const seen = new Set()
        const options = []
        for (const entry of sleeves) {
            const value = String(entry?.strategyName || entry?.sourceStrategyId || entry?.label || '').trim()
            if (!value || seen.has(value)) {
                continue
            }
            seen.add(value)
            options.push({
                value,
                sleeveId: String(entry?.id || ''),
            })
        }
        return options
    }, [sleeves])
    const effectiveReconciliationStrategyFilter = reconciliationFilters.strategyFilter || reconciliationStrategyOptions[0]?.value || ''
    const selectedReconciliationSleeve = useMemo(() => (
        sleeves.find((entry) => String(entry?.strategyName || entry?.sourceStrategyId || entry?.label || '').trim() === effectiveReconciliationStrategyFilter)
        || sleeves[0]
        || null
    ), [effectiveReconciliationStrategyFilter, sleeves])
    const reconciliationAuditRows = useMemo(() => (
        (Array.isArray(reconciliationState.rows) ? [...reconciliationState.rows] : [])
            .sort((left, right) => {
                const rightTime = readComparisonDisplayTimestamp(right)
                const leftTime = readComparisonDisplayTimestamp(left)
                if (rightTime !== leftTime) {
                    return rightTime - leftTime
                }
                return Number(right?.index || 0) - Number(left?.index || 0)
            })
    ), [reconciliationState.rows])
    const recentBacktesterRows = useMemo(() => (
        reconciliationAuditRows
            .filter((entry) => entry?.expected)
            .slice(0, 8)
    ), [reconciliationAuditRows])
    const recentTraderRows = useMemo(() => (
        reconciliationAuditRows
            .filter((entry) => entry?.actual)
            .slice(0, 8)
    ), [reconciliationAuditRows])
    const latencySeries = useMemo(() => {
        const runtimeLatencyEvents = Array.isArray(runtimeState?.latency_events)
            ? [...runtimeState.latency_events].reverse()
            : []
        const points = runtimeLatencyEvents
            .map((entry) => ({
                latencyMs: Number(entry?.latency_ms),
                label: String(entry?.stage || 'runtime'),
            }))
            .filter((entry) => Number.isFinite(entry.latencyMs))
            .slice(-32)
        return {
            values: points.map((entry) => entry.latencyMs),
            labels: points.map((entry) => entry.label),
        }
    }, [runtimeState?.latency_events])
    const balanceSeries = useMemo(() => {
        const rows = Array.isArray(historyState.rows) ? [...historyState.rows] : []
        const sorted = rows.sort((left, right) => {
            const leftTime = Number(left?.filled_at || left?.rejected_at || left?.created_at || left?.record_created_at || 0)
            const rightTime = Number(right?.filled_at || right?.rejected_at || right?.created_at || right?.record_created_at || 0)
            return leftTime - rightTime
        })
        let running = 0
        const values = []
        const labels = []
        for (const entry of sorted) {
            const pnl = Number(entry?.profit || 0) + Number(entry?.commission || 0) + Number(entry?.swap || 0)
            if (!Number.isFinite(pnl)) {
                continue
            }
            running += pnl
            values.push(running)
            labels.push(formatRelativeTimestamp(entry?.filled_at || entry?.rejected_at || entry?.created_at || entry?.record_created_at))
        }
        return { values, labels }
    }, [historyState.rows])
    const historyView = useMemo(
        () => buildTradeHistoryView(historyState.rows, historyFilters.statusFilter, activeSleeveStates),
        [activeSleeveStates, historyFilters.statusFilter, historyState.rows],
    )
    const finalizedTradeExecutionSignature = useMemo(
        () => orderCommands
            .filter((entry) => {
                const status = String(entry?.status || '').toLowerCase()
                return status === 'filled' || status === 'rejected'
            })
            .map((entry) => [
                String(entry?.id || ''),
                String(entry?.status || ''),
                String(entry?.filled_at || ''),
                String(entry?.rejected_at || ''),
                String(entry?.broker_order_id || ''),
                String(entry?.broker_deal_id || ''),
            ].join(':'))
            .join('|'),
        [orderCommands],
    )
    const pagedAuditEvents = useMemo(() => {
        const pageSize = 40
        const allRows = [
            ...liveAuditEvents.map((entry, index) => ({
                kind: 'audit',
                id: `${entry?.id || 'audit'}-${index}`,
                title: entry?.kind || 'event',
                detail: entry?.message || '',
            })),
            ...orderIntents.map((entry) => ({
                kind: 'intent',
                id: entry?.id || `${entry?.sleeve_id}-${entry?.created_at}`,
                title: `${entry?.action || 'intent'} ${entry?.side || ''}`.trim(),
                detail: `${entry?.sleeve_label || entry?.sleeve_id || 'Sleeve'} · ${entry?.symbol || '—'} · ${entry?.status || 'queued'}`,
            })),
            ...orderCommands.map((entry) => ({
                kind: 'command',
                id: entry?.id || `${entry?.sleeve_id}-${entry?.created_at}`,
                title: `cmd ${(entry?.action || 'order')} ${(entry?.side || '')}`.trim(),
                detail: `${entry?.sleeve_label || entry?.sleeve_id || 'Sleeve'} · ${entry?.symbol || '—'} · ${entry?.status || 'queued'}${entry?.age_seconds != null ? ` · ${entry.age_seconds}s` : ''}${entry?.broker_order_id ? ` · ticket ${entry.broker_order_id}` : ''}`,
            })),
        ]
        const totalPages = Math.max(1, Math.ceil(allRows.length / pageSize))
        const currentPage = Math.min(auditLogPage, totalPages - 1)
        const startIndex = currentPage * pageSize
        return {
            pageSize,
            totalRows: allRows.length,
            totalPages,
            currentPage,
            rows: allRows.slice(startIndex, startIndex + pageSize),
        }
    }, [auditLogPage, liveAuditEvents, orderCommands, orderIntents])

    const buildAuthHeaders = useCallback((extraHeaders = {}) => {
        if (!authToken) {
            return extraHeaders
        }
        return {
            ...extraHeaders,
            Authorization: `Bearer ${authToken}`,
        }
    }, [authToken])

    useEffect(() => {
        logEventRef.current = onLogEvent
    }, [onLogEvent])

    function updateTradeState(mutator) {
        setTradeState((current) => {
            const base = current && typeof current === 'object' ? current : {}
            const next = typeof mutator === 'function' ? mutator(base) : base
            return {
                ...base,
                ...next,
            }
        })
    }

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
            const response = await fetchWithRetry(
                buildApiUrl(`/workspace/strategy-benchmarks?${buildBrokerProfileQuery({
                    workspaceId: 'default',
                    limit: STRATEGY_LIBRARY_FETCH_LIMIT,
                    brokerProfileId: activeBrokerProfileId,
                })}`),
                {
                    headers: buildAuthHeaders(),
                },
                {
                    attempts: 4,
                    retryDelayMs: 750,
                },
            )
            const data = await readApiJsonResponse(response)
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
            logEventRef.current?.(`Trade · ${message}`)
        } finally {
            if (!quiet) {
                setIsStrategyLibraryLoading(false)
            }
        }
    }, [activeBrokerProfileId, authToken, buildAuthHeaders])

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
            const response = await fetchWithRetry(
                buildApiUrl(`/workspace/saved-portfolios?${buildBrokerProfileQuery({
                    workspaceId: 'default',
                    limit: STRATEGY_LIBRARY_FETCH_LIMIT,
                    brokerProfileId: activeBrokerProfileId,
                })}`),
                {
                    headers: buildAuthHeaders(),
                },
                {
                    attempts: 4,
                    retryDelayMs: 750,
                },
            )
            const data = await readApiJsonResponse(response)
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
            logEventRef.current?.(`Trade · ${message}`)
        } finally {
            if (!quiet) {
                setIsPortfolioLibraryLoading(false)
            }
        }
    }, [activeBrokerProfileId, authToken, buildAuthHeaders])

    async function handleToggleFavoriteStrategyInLibrary(targetEntry = selectedStrategyLibraryItem) {
        if (!authToken || !targetEntry?.id) {
            return
        }

        const nextIsFavorite = !targetEntry?.is_favorite
        try {
            const response = await fetchWithRetry(
                buildApiUrl(`/workspace/strategy-benchmarks/${targetEntry.id}?workspace_id=default`),
                {
                    method: 'PATCH',
                    headers: buildAuthHeaders({
                        'Content-Type': 'application/json',
                    }),
                    body: JSON.stringify({
                        workspace_id: 'default',
                        is_favorite: Boolean(nextIsFavorite),
                    }),
                },
            )
            const data = await readApiJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to update favorite strategy.'))
            }
            logEventRef.current?.(
                nextIsFavorite
                    ? `Trade · Marked "${targetEntry.label || `Strategy #${targetEntry.id}`}" as favorite.`
                    : `Trade · Removed "${targetEntry.label || `Strategy #${targetEntry.id}`}" from favorites.`
            )
            await refreshStrategyLibrary({ quiet: true })
        } catch (error) {
            logEventRef.current?.(`Trade · ${error?.message || 'Failed to update favorite strategy.'}`)
        }
    }

    async function handleToggleFavoritePortfolioInLibrary(targetEntry = selectedPortfolioLibraryItem) {
        if (!authToken || !targetEntry?.id) {
            return
        }

        const nextIsFavorite = !targetEntry?.is_favorite
        try {
            const response = await fetchWithRetry(
                buildApiUrl(`/workspace/saved-portfolios/${targetEntry.id}?workspace_id=default`),
                {
                    method: 'PATCH',
                    headers: buildAuthHeaders({
                        'Content-Type': 'application/json',
                    }),
                    body: JSON.stringify({
                        workspace_id: 'default',
                        is_favorite: Boolean(nextIsFavorite),
                    }),
                },
            )
            const data = await readApiJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to update favorite portfolio.'))
            }
            await refreshPortfolioLibrary({ quiet: true })
        } catch (error) {
            logEventRef.current?.(`Trade · ${error?.message || 'Failed to update favorite portfolio.'}`)
        }
    }

    function buildConfigurePayload({ sleeveEntries = localComparableConfig.sleeves } = {}) {
        const normalizedSleeves = Array.isArray(sleeveEntries)
            ? sleeveEntries.map((entry, index) => normalizeSleeve(entry, index))
            : localComparableConfig.sleeves
        const syncedPortfolios = rebuildTradePortfoliosFromSleeves(
            normalizedSleeves,
            localComparableConfig.portfolios,
            localComparableConfig.mode,
        )

        return {
            ...localComparableConfig,
            portfolioStructureVersion: normalizeTradePortfolioStructureVersion(
                localComparableConfig.portfolioStructureVersion,
            ),
            brokerProfileId: localComparableConfig.activeBrokerProfileId || activeBrokerProfileId || '',
            brokerProfileLabel: localComparableConfig.activeBrokerProfileLabel || activeBrokerProfileLabel || '',
            portfolios: syncedPortfolios,
            sleeves: normalizedSleeves.map((entry, index) => {
                const normalizedEntry = normalizeSleeve(entry, index)
                const strategyChartSettings = buildStrategyAliasContextChartSettings({
                    symbol: normalizedEntry.symbol,
                    timeframe: normalizedEntry.timeframe,
                    bars: chartSettings?.bars || 1000,
                    indicators: Array.isArray(normalizedEntry.indicators) ? normalizedEntry.indicators : [],
                }, normalizedEntry.strategy, normalizedEntry.indicators)
                return {
                    ...normalizedEntry,
                    indicators: buildBackendIndicatorsPayload(strategyChartSettings.indicators || []),
                    strategy: resolveStrategyAliasesInStrategy(normalizedEntry.strategy, strategyChartSettings),
                }
            }),
        }
    }

    function isBackendRuntimeEmpty(snapshot = runtimeState) {
        const safeSnapshot = snapshot && typeof snapshot === 'object' ? snapshot : {}
        const sleeveCount = Array.isArray(safeSnapshot?.sleeves) ? safeSnapshot.sleeves.length : 0
        const intentCount = Array.isArray(safeSnapshot?.order_intents) ? safeSnapshot.order_intents.length : 0
        const commandCount = Array.isArray(safeSnapshot?.order_commands) ? safeSnapshot.order_commands.length : 0
        return (
            sleeveCount === 0
            && intentCount === 0
            && commandCount === 0
            && safeSnapshot?.armed !== true
            && safeSnapshot?.live_dispatch_armed !== true
        )
    }

    function applyLocallyClearedRuntime(snapshot = runtimeState) {
        const safeSnapshot = snapshot && typeof snapshot === 'object' ? snapshot : {}
        const nextRuntime = {
            ...safeSnapshot,
            armed: false,
            live_dispatch_armed: false,
            live: false,
            status: 'idle',
            sleeves: [],
            sleeve_states: {},
            active_symbols: [],
            order_intents: [],
            order_commands: [],
        }
        setRuntimeState(nextRuntime)
        setLiveTradeRuntime?.(nextRuntime)
        updateTradeState((current) => ({
            ...current,
            sleeves: [],
        }))
    }

    function restoreSavedRuntimeConfig() {
        updateTradeState((current) => ({
            ...current,
            mode: savedComparableConfig.mode,
            executionMode: savedComparableConfig.executionMode,
            activeBrokerProfileId: savedComparableConfig.activeBrokerProfileId,
            activeBrokerProfileLabel: savedComparableConfig.activeBrokerProfileLabel,
            sameSymbolExecutionPolicy: savedComparableConfig.sameSymbolExecutionPolicy,
            portfolioStructureVersion: savedComparableConfig.portfolioStructureVersion,
            portfolios: cloneSerializable(savedComparableConfig.portfolios, []),
            signalValiditySeconds: savedComparableConfig.signalValiditySeconds,
            latencyBudgetMs: savedComparableConfig.latencyBudgetMs,
            sleeves: savedComparableConfig.sleeves.map((entry, index) => normalizeSleeve(entry, index)),
        }))
        onLogEvent?.('Trade · Restored the last runtime config saved in the backend.')
    }

    async function clearRuntimeConfig() {
        if (isGuest) {
            setRequestError(guestRestrictionMessage)
            onLogEvent?.(`Trade · ${guestRestrictionMessage}`)
            return
        }
        if (!hasAnyRuntimeSleeves) {
            onLogEvent?.('Trade · Runtime is already empty.')
            return
        }
        const shouldClear = window.confirm(
            'Clear the saved trade runtime? This removes all loaded sleeves from the backend runtime and from the local Trader setup.'
        )
        if (!shouldClear) {
            return
        }
        if (isBackendRuntimeEmpty()) {
            applyLocallyClearedRuntime()
            setRequestError('')
            onLogEvent?.('Trade · Runtime was already empty in the backend. Cleared the local Trader setup only.')
            return
        }
        if (isLiveDispatchArmed) {
            const disarmedLiveDispatch = await postTradeRuntime(
                '/trade/runtime/disarm-live-dispatch',
                null,
                'Trade · Live dispatch disarmed.',
            )
            if (!disarmedLiveDispatch) {
                if (isBackendRuntimeEmpty()) {
                    applyLocallyClearedRuntime()
                    setRequestError('')
                    onLogEvent?.('Trade · Runtime was already empty in the backend. Cleared the local Trader setup only.')
                }
                return
            }
        }
        if (isRuntimeArmed) {
            const disarmedRuntime = await postTradeRuntime(
                '/trade/runtime/disarm',
                null,
                'Trade · Runtime disarmed.',
            )
            if (!disarmedRuntime) {
                if (isBackendRuntimeEmpty()) {
                    applyLocallyClearedRuntime()
                    setRequestError('')
                    onLogEvent?.('Trade · Runtime was already empty in the backend. Cleared the local Trader setup only.')
                }
                return
            }
        }
        const clearedRuntime = await postTradeRuntime(
            '/trade/runtime/configure',
            buildConfigurePayload({ sleeveEntries: [] }),
            'Trade · Runtime cleared in backend.',
        )
        if (!clearedRuntime && isBackendRuntimeEmpty()) {
            applyLocallyClearedRuntime()
            setRequestError('')
            onLogEvent?.('Trade · Runtime was already empty in the backend. Cleared the local Trader setup only.')
        }
    }

    async function refreshRuntime({ quiet = false } = {}) {
        if (!quiet) {
            setIsSyncing(true)
        }
        try {
            const response = await fetchWithRetry(buildApiUrl('/trade/runtime'), {
                headers: buildAuthHeaders(),
            })
            const data = await readApiJsonResponse(response)
            if (!response.ok) {
                throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to load trade runtime.')}`)
            }
            const nextRuntime = extractRuntimePayload(data)
            setRuntimeState(nextRuntime)
            setLiveTradeRuntime?.(nextRuntime)
            setRequestError('')
            return data
        } catch (error) {
            setRequestError(error.message || 'Failed to load trade runtime.')
            return null
        } finally {
            if (!quiet) {
                setIsSyncing(false)
            }
        }
    }

    async function confirmTradeRuntimeAction(path) {
        const response = await fetchWithRetry(buildApiUrl('/health'), {
            headers: buildAuthHeaders(),
        })
        const data = await readApiJsonResponse(response)
        if (!response.ok || data?.status !== 'ok') {
            throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to confirm trade runtime state.')}`)
        }

        const nextRuntime = extractRuntimePayload(data?.trade_runtime || data)
        if (nextRuntime) {
            setRuntimeState(nextRuntime)
            setLiveTradeRuntime?.(nextRuntime)
        }

        const isConfirmed = (() => {
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
            confirmed: isConfirmed,
            runtime: nextRuntime,
        }
    }

    async function postTradeRuntime(path, payload = null, successMessage = '') {
        if (isGuest) {
            setRequestError(guestRestrictionMessage)
            onLogEvent?.(`Trade · ${guestRestrictionMessage}`)
            return null
        }

        setIsSubmitting(true)
        try {
            const response = await fetchWithRetry(buildApiUrl(path), {
                method: 'POST',
                headers: buildAuthHeaders({
                    'Content-Type': 'application/json',
                }),
                body: payload ? JSON.stringify(payload) : '{}',
            })
            const data = await readApiJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(
                    `${response.status} ${extractApiErrorMessage(data, 'Trade runtime request failed.')}`
                )
            }
            const nextRuntime = data?.trade_runtime || null
            if (nextRuntime) {
                setRuntimeState(nextRuntime)
                setLiveTradeRuntime?.(nextRuntime)
                if (path === '/trade/runtime/configure') {
                    const normalizedSavedConfig = buildComparableTradeConfig(nextRuntime)
                    updateTradeState((current) => ({
                        ...current,
                        mode: normalizedSavedConfig.mode,
                        executionMode: normalizedSavedConfig.executionMode,
                        activeBrokerProfileId: normalizedSavedConfig.activeBrokerProfileId,
                        activeBrokerProfileLabel: normalizedSavedConfig.activeBrokerProfileLabel,
                        sameSymbolExecutionPolicy: normalizedSavedConfig.sameSymbolExecutionPolicy,
                        portfolioStructureVersion: normalizedSavedConfig.portfolioStructureVersion,
                        portfolios: cloneSerializable(normalizedSavedConfig.portfolios, []),
                        signalValiditySeconds: normalizedSavedConfig.signalValiditySeconds,
                        latencyBudgetMs: normalizedSavedConfig.latencyBudgetMs,
                        sleeves: normalizedSavedConfig.sleeves.map((entry, index) => normalizeSleeve(entry, index)),
                    }))
                }
            }
            setRequestError('')
            if (successMessage) {
                onLogEvent?.(successMessage)
            }
            return nextRuntime
        } catch (error) {
            try {
                const confirmation = await confirmTradeRuntimeAction(path)
                if (confirmation.confirmed) {
                    setRequestError('')
                    if (successMessage) {
                        onLogEvent?.(successMessage)
                    }
                    return confirmation.runtime
                }
            } catch {
                // Keep the original error below when confirmation also fails.
            }

            const message = error.message || 'Trade runtime request failed.'
            setRequestError(message)
            onLogEvent?.(`Trade · ${message}`)
            return null
        } finally {
            setIsSubmitting(false)
        }
    }

    async function refreshTradeHistory({ quiet = false } = {}) {
        if (!quiet) {
            setHistoryState((current) => ({
                ...current,
                loading: true,
                error: '',
            }))
        }
        try {
            const query = new URLSearchParams({
                range_key: historyFilters.rangeKey,
                custom_days: String(historyFilters.customDays),
                strategy_filter: historyFilters.strategyFilter,
                symbol_filter: historyFilters.symbolFilter,
                status_filter: 'all',
                limit: '500',
            })
            if (activeBrokerProfileId) {
                query.set('broker_profile_id', activeBrokerProfileId)
            }
            const response = await fetchWithRetry(buildApiUrl(`/workspace/live-trades?${query.toString()}`), {
                headers: buildAuthHeaders(),
            })
            const data = await readApiJsonResponse(response)
            if (!response.ok) {
                throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to load live trade history.')}`)
            }
            setHistoryState({
                loading: false,
                error: '',
                rows: Array.isArray(data?.trades) ? data.trades : [],
                summary: data?.summary && typeof data.summary === 'object' ? data.summary : null,
            })
            return data
        } catch (error) {
            setHistoryState((current) => ({
                ...current,
                loading: false,
                error: error.message || 'Failed to load live trade history.',
            }))
            return null
        }
    }

    async function runReconciliation() {
        if (isGuest) {
            setReconciliationState({
                loading: false,
                error: guestRestrictionMessage,
                rows: [],
                summary: null,
                expectedRows: [],
                actualRows: [],
            })
            onLogEvent?.(`Trader · ${guestRestrictionMessage}`)
            return null
        }

        setReconciliationState({
            loading: true,
            error: '',
            rows: [],
            summary: null,
            expectedRows: [],
            actualRows: [],
        })
        try {
            const targetSleeve = selectedReconciliationSleeve
            if (!targetSleeve?.strategy) {
                throw new Error('Select a loaded trader sleeve before running the comparison.')
            }
            const strategyChartSettings = normalizeChartSettings({
                symbol: targetSleeve.symbol || chartSettings?.symbol || 'EURUSD',
                timeframe: targetSleeve.timeframe || chartSettings?.timeframe || 'M1',
                bars: chartSettings?.bars || 1000,
                indicators: Array.isArray(targetSleeve.indicators) ? targetSleeve.indicators : [],
            })
            const resolvedStrategy = resolveStrategyAliasesInStrategy(targetSleeve.strategy, strategyChartSettings)
            const response = await fetchWithRetry(buildApiUrl('/workspace/trade-reconciliations'), {
                method: 'POST',
                headers: buildAuthHeaders({
                    'Content-Type': 'application/json',
                }),
                body: JSON.stringify({
                    range_key: reconciliationFilters.rangeKey,
                    custom_days: reconciliationFilters.customDays,
                    strategy_filter: effectiveReconciliationStrategyFilter,
                    broker_profile_id: activeBrokerProfileId || undefined,
                    strategy_payload: resolvedStrategy,
                    indicators: Array.isArray(targetSleeve.indicators) ? targetSleeve.indicators : [],
                    symbol: targetSleeve.symbol || chartSettings?.symbol || 'EURUSD',
                    timeframe: targetSleeve.timeframe || chartSettings?.timeframe || 'M1',
                    volume: Number(targetSleeve.volume || 0.01),
                }),
            })
            const data = await readApiJsonResponse(response)
            if (!response.ok) {
                throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to compare expected vs executed operations.')}`)
            }
            const snapshot = data?.reconciliation && typeof data.reconciliation === 'object' ? data.reconciliation : null
            const rows = Array.isArray(snapshot?.rows) ? snapshot.rows : []
            setReconciliationState({
                loading: false,
                error: '',
                rows,
                summary: snapshot?.summary && typeof snapshot.summary === 'object' ? snapshot.summary : null,
                expectedRows: Array.isArray(snapshot?.expected_rows) ? snapshot.expected_rows : [],
                actualRows: Array.isArray(snapshot?.actual_rows) ? snapshot.actual_rows : [],
            })
            onLogEvent?.('Trader · Expected vs executed comparison completed.')
            return rows
        } catch (error) {
            setReconciliationState({
                loading: false,
                error: error.message || 'Failed to compare expected vs executed operations.',
                rows: [],
                summary: null,
                expectedRows: [],
                actualRows: [],
            })
            return null
        }
    }

    useEffect(() => {
        setActiveSubTab(String(tradeState?.selectedTab || 'setup'))
    }, [tradeState?.selectedTab])

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

    useEffect(() => {
        if (liveTradeRuntime && liveTradeRuntime !== runtimeState) {
            setRuntimeState(extractRuntimePayload(liveTradeRuntime))
        }
    }, [liveTradeRuntime, runtimeState])

    useEffect(() => {
        if (!authToken || activeSubTab !== 'history') {
            return undefined
        }

        void refreshTradeHistory()
        const timer = window.setInterval(() => {
            void refreshTradeHistory({ quiet: true })
        }, isRuntimeArmed ? 3000 : 10000)

        return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        authToken,
        activeSubTab,
        activeBrokerProfileId,
        isRuntimeArmed,
        historyFilters.rangeKey,
        historyFilters.customDays,
        historyFilters.strategyFilter,
        historyFilters.symbolFilter,
        historyFilters.statusFilter,
    ])

    useEffect(() => {
        if (!authToken || activeSubTab !== 'history' || !finalizedTradeExecutionSignature) {
            return undefined
        }
        void refreshTradeHistory({ quiet: true })
        return undefined
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeBrokerProfileId, activeSubTab, authToken, finalizedTradeExecutionSignature])

    useEffect(() => {
        if (!authToken || activeSubTab !== 'history' || !isRuntimeArmed || !runtimeState?.last_event_at) {
            return undefined
        }

        const now = Date.now()
        if (now - lastHistoryAutoRefreshAtRef.current < 1500) {
            return undefined
        }

        lastHistoryAutoRefreshAtRef.current = now
        void refreshTradeHistory({ quiet: true })
        return undefined
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeBrokerProfileId, activeSubTab, authToken, isRuntimeArmed, runtimeState?.last_event_at])

    useEffect(() => {
        setAuditLogPage(0)
    }, [liveAuditEvents.length, orderCommands.length, orderIntents.length])

    useEffect(() => {
        if (!authToken || liveTradeRuntime) {
            return undefined
        }
        void refreshRuntime()
        return undefined
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authToken, liveTradeRuntime])

    function handleSubTabChange(tabId) {
        setActiveSubTab(tabId)
        updateTradeState((current) => ({
            ...current,
            selectedTab: tabId,
        }))
    }

    function closeBenchmarkImportOverlay() {
        setBenchmarkImportDraft(null)
    }

    function includeBenchmarkInRuntime(benchmark, { primarySymbol = '', primaryTimeframe = '' } = {}) {
        const importedSleeves = buildSleevesFromBenchmark(benchmark, {
            primarySymbol,
            primaryTimeframe,
            startIndex: sleeves.length,
            defaultVolume: sleeves[0]?.volume || 0.01,
        })
        if (!importedSleeves.length) {
            onLogEvent?.('Trade · The selected saved strategy did not contain any executable sleeves.')
            return
        }

        updateTradeState((current) => {
            const currentSleeves = Array.isArray(current?.sleeves) ? current.sleeves.map(normalizeSleeve) : []
            const hasExplicitPortfolios = Array.isArray(current?.portfolios) && current.portfolios.length > 0
            const scopedSleeves = hasExplicitPortfolios
                ? importedSleeves.map((entry) => ({
                    ...entry,
                    portfolioId: String(entry?.portfolioId || 'adhoc-imports').trim() || 'adhoc-imports',
                    portfolioLabel: String(entry?.portfolioLabel || 'Ad hoc imports').trim() || 'Ad hoc imports',
                    pipelineId: String(entry?.pipelineId || 'adhoc-imports-main').trim() || 'adhoc-imports-main',
                    pipelineLabel: String(entry?.pipelineLabel || 'Imported sleeves').trim() || 'Imported sleeves',
                    portfolioMode: String(entry?.portfolioMode || current?.mode || 'parallel_sleeves').trim().toLowerCase() || 'parallel_sleeves',
                }))
                : importedSleeves
            return {
                ...current,
                sleeves: [...currentSleeves, ...scopedSleeves],
            }
        })
        onLogEvent?.(
            `Trade · Included ${importedSleeves.length} sleeve${importedSleeves.length === 1 ? '' : 's'} from "${benchmark?.label || `Strategy #${benchmark?.id}`}" into the runtime setup.`
        )
    }

    function handleAddSavedStrategyToRuntime() {
        if (!selectedStrategyLibraryItem) {
            return
        }
        const primaryMarket = selectedStrategyPrimaryMarket
        if (!primaryMarket.symbol || !primaryMarket.timeframe) {
            setBenchmarkImportDraft({
                benchmark: selectedStrategyLibraryItem,
                symbol: primaryMarket.symbol,
                timeframe: primaryMarket.timeframe || inferBenchmarkCompanionTimeframe(selectedStrategyLibraryItem) || 'M1',
                error: '',
            })
            return
        }
        includeBenchmarkInRuntime(selectedStrategyLibraryItem, primaryMarket)
    }

    function handleConfirmBenchmarkImportOverlay() {
        if (!benchmarkImportDraft?.benchmark) {
            return
        }
        const symbol = normalizeTradeMarketValue(benchmarkImportDraft.symbol)
        const timeframe = normalizeTradeMarketValue(benchmarkImportDraft.timeframe)
        if (!symbol || !timeframe) {
            setBenchmarkImportDraft((current) => current ? {
                ...current,
                error: 'Choose the primary symbol and timeframe before importing this strategy into Trader.',
            } : current)
            return
        }
        includeBenchmarkInRuntime(benchmarkImportDraft.benchmark, {
            primarySymbol: symbol,
            primaryTimeframe: timeframe,
        })
        setBenchmarkImportDraft(null)
    }

    function handleAddSavedPortfolioToRuntime() {
        if (!selectedPortfolioLibraryItem?.portfolio) {
            return
        }
        const instantiated = instantiateSavedPortfolioForTrader(selectedPortfolioLibraryItem, {
            existingPortfolioIds: Array.isArray(tradeState?.portfolios)
                ? tradeState.portfolios.map((entry) => String(entry?.id || '').trim()).filter(Boolean)
                : [],
        })
        const nextPortfolios = [
            ...((Array.isArray(tradeState?.portfolios) && tradeState.portfolios.length)
                ? cloneSerializable(tradeState.portfolios, [])
                : []),
            instantiated,
        ]
        const nextSleeves = [
            ...sleeves,
            ...extractSleevesFromTradePortfolios([instantiated]),
        ]
        updateTradeState((current) => ({
            ...current,
            portfolioStructureVersion: 2,
            portfolios: nextPortfolios,
            sleeves: nextSleeves,
        }))
        onLogEvent?.(`Trade · Included portfolio "${selectedPortfolioLibraryItem.label || 'saved portfolio'}" into the runtime setup.`)
    }

    function handleRemoveSleeve(targetId) {
        updateTradeState((current) => ({
            ...current,
            sleeves: (Array.isArray(current?.sleeves) ? current.sleeves : []).filter((entry) => String(entry?.id) !== String(targetId)),
        }))
    }

    return (
        <div className='Trade'>
            {benchmarkImportDraft?.benchmark ? (
                <div className='overlayContainer tradeBenchmarkImportOverlay' role='dialog' aria-modal='true' aria-label='Choose benchmark market context'>
                    <div className='fog' onClick={closeBenchmarkImportOverlay} />
                    <div className='overlay tradeBenchmarkImportWindow'>
                        <button type='button' className='closeOverlay' onClick={closeBenchmarkImportOverlay} aria-label='Close benchmark market overlay'>
                            ×
                        </button>
                        <div className='tradeBenchmarkImportPanel'>
                            <h4>Primary market required</h4>
                            <p>
                                <strong>{benchmarkImportDraft.benchmark.label || `Strategy #${benchmarkImportDraft.benchmark.id}`}</strong> was saved without the primary sleeve market.
                                Choose it now so Trader can import the runtime independently from the chart you are currently watching.
                            </p>
                            <label className='tradeField'>
                                <span>Primary symbol</span>
                                <input
                                    type='text'
                                    value={benchmarkImportDraft.symbol}
                                    onChange={(event) => setBenchmarkImportDraft((current) => current ? {
                                        ...current,
                                        symbol: event.target.value.toUpperCase(),
                                        error: '',
                                    } : current)}
                                    placeholder='EURUSD'
                                />
                            </label>
                            <label className='tradeField'>
                                <span>Primary timeframe</span>
                                <select
                                    value={benchmarkImportDraft.timeframe || 'M1'}
                                    onChange={(event) => setBenchmarkImportDraft((current) => current ? {
                                        ...current,
                                        timeframe: event.target.value,
                                        error: '',
                                    } : current)}
                                >
                                    {TIMEFRAME_OPTIONS.map(([value, label]) => (
                                        <option key={value} value={value}>
                                            {label}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            {benchmarkImportDraft.error ? (
                                <div className='tradeErrorLog' role='alert'>
                                    <strong>Import blocked</strong>
                                    <span>{benchmarkImportDraft.error}</span>
                                </div>
                            ) : null}
                            <div className='tradeActions tradeBenchmarkImportActions'>
                                <button type='button' className='tradeSecondaryAction' onClick={closeBenchmarkImportOverlay}>
                                    Cancel
                                </button>
                                <button type='button' className='tradePrimaryAction' onClick={handleConfirmBenchmarkImportOverlay}>
                                    Include in runtime
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            ) : null}
            <div className='batchTabs batchTabsInline tradeTabs' role='tablist' aria-label='Trade views'>
                {SUB_TABS.map((tab) => (
                    <button
                        key={tab.id}
                        type='button'
                        className={`batchTabButton ${activeSubTab === tab.id ? 'active' : ''}`}
                        onClick={() => handleSubTabChange(tab.id)}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {activeSubTab === 'setup' ? (
                <div className='tradePanel'>
                    <div className='tradeManagerShell'>
                        <aside className='tradeStrategyLibrarySidebar'>
                            <div className='tradeStrategyLibrarySidebarHeader'>
                                <h4>{librarySourceTab === 'portfolios' ? 'Saved portfolios' : 'Saved strategies'}</h4>
                                <p>
                                    {librarySourceTab === 'portfolios'
                                        ? 'Load portfolio bundles directly into the trader runtime.'
                                        : 'Select directly from the saved strategy library and load sleeves into the trader runtime without relying on the Strategy editor draft.'}
                                </p>
                            </div>

                            <div className='tradeStrategyLibraryToolbar'>
                                <div className='tradeStrategyLibraryTabs'>
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
                                <div className='tradeStrategyLibraryTabs'>
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
                                    className='tradeStrategyLibraryRefreshButton'
                                    onClick={() => void (librarySourceTab === 'portfolios'
                                        ? refreshPortfolioLibrary({ quiet: false })
                                        : refreshStrategyLibrary({ quiet: false }))}
                                    disabled={librarySourceTab === 'portfolios' ? isPortfolioLibraryLoading : isStrategyLibraryLoading}
                                >
                                    {(librarySourceTab === 'portfolios' ? isPortfolioLibraryLoading : isStrategyLibraryLoading) ? 'Refreshing...' : 'Refresh'}
                                </button>
                            </div>

                            <div className='tradeStrategyLibrarySearchRow'>
                                <input
                                    type='text'
                                    value={strategyLibraryQuery}
                                    onChange={(event) => setStrategyLibraryQuery(event.target.value)}
                                    placeholder={librarySourceTab === 'portfolios' ? 'Filter saved portfolios' : 'Filter saved strategies'}
                                    aria-label={librarySourceTab === 'portfolios' ? 'Filter saved portfolios' : 'Filter saved strategies'}
                                />
                                {strategyLibraryQuery ? (
                                    <button
                                        type='button'
                                        className='tradeStrategyLibrarySearchClear'
                                        onClick={() => setStrategyLibraryQuery('')}
                                    >
                                        Clear
                                    </button>
                                ) : null}
                            </div>

                            <div className='tradeStrategyLibraryList'>
                                {activeLibraryError && !(librarySourceTab === 'portfolios' ? portfolioLibraryItems.length : strategyLibraryItems.length) ? (
                                    <div className='tradeEmpty tradeLibraryError'>{activeLibraryError}</div>
                                ) : (librarySourceTab === 'portfolios' ? !portfolioLibraryItems.length : !strategyLibraryItems.length) ? (
                                    <div className='tradeEmpty'>{librarySourceTab === 'portfolios' ? 'No saved portfolios yet.' : 'No saved strategies yet.'}</div>
                                ) : normalizedStrategyLibraryQuery && !(librarySourceTab === 'portfolios' ? visiblePortfolioLibraryItems.length : visibleStrategyLibraryItems.length) ? (
                                    <div className='tradeEmpty'>{librarySourceTab === 'portfolios' ? 'No saved portfolios match this filter.' : 'No saved strategies match this filter.'}</div>
                                ) : !(librarySourceTab === 'portfolios' ? visiblePortfolioLibraryItems.length : visibleStrategyLibraryItems.length) ? (
                                    <div className='tradeEmpty'>{librarySourceTab === 'portfolios' ? 'No favorite portfolios yet.' : 'No favorite strategies yet.'}</div>
                                ) : (librarySourceTab === 'portfolios' ? visiblePortfolioLibraryItems : visibleStrategyLibraryItems).map((entry) => (
                                    <div key={entry.id} className='tradeStrategyLibraryEntry'>
                                        <button
                                            type='button'
                                            className={`tradeStrategyLibrarySelect ${String(librarySourceTab === 'portfolios' ? selectedPortfolioLibraryId : selectedStrategyLibraryId) === String(entry.id) ? 'active' : ''}`.trim()}
                                            onClick={() => (
                                                librarySourceTab === 'portfolios'
                                                    ? setSelectedPortfolioLibraryId(String(entry.id))
                                                    : setSelectedStrategyLibraryId(String(entry.id))
                                            )}
                                        >
                                            <div className='tradeStrategyLibraryEntryHeader'>
                                                <strong className='tradeStrategyLibraryEntryLabel'>
                                                    {entry.is_favorite ? <span className='tradeStrategyLibraryFavoriteStar' aria-hidden='true'>★</span> : null}
                                                    <span>{entry.label || `${librarySourceTab === 'portfolios' ? 'Portfolio' : 'Strategy'} #${entry.id}`}</span>
                                                </strong>
                                                {entry.is_favorite ? <span className='tradeStrategyLibraryFavoriteBadge'>Favorite</span> : null}
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
                                            className={`tradeStrategyLibraryFavoriteToggle ${entry.is_favorite ? 'active' : ''}`.trim()}
                                            onClick={() => void (librarySourceTab === 'portfolios' ? handleToggleFavoritePortfolioInLibrary(entry) : handleToggleFavoriteStrategyInLibrary(entry))}
                                            title={entry.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
                                            aria-label={entry.is_favorite ? `Remove ${entry.label || `${librarySourceTab === 'portfolios' ? 'Portfolio' : 'Strategy'} #${entry.id}`} from favorites` : `Add ${entry.label || `${librarySourceTab === 'portfolios' ? 'Portfolio' : 'Strategy'} #${entry.id}`} to favorites`}
                                        >
                                            ★
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </aside>

                        <div className='tradeManagerContent'>
                            <div className='tradePanelGrid'>
                                <div className='tradeManagerLeftColumn'>
                                    <section className='tradeCard'>
                                        <h4>Execution Mode</h4>
                                        <div className='tradeConfigHelp'>
                                            <strong>Save runtime to apply</strong>
                                            <span>
                                                Mode, execution path, latency budget, same-symbol policy and the current sleeve list stay local here until you click <code>Save Runtime</code>.
                                            </span>
                                        </div>
                                        <label className='tradeField'>
                                            <span>Mode</span>
                                            <select
                                                value={String(tradeState?.mode || 'parallel_sleeves')}
                                                onChange={(event) => updateTradeState((current) => ({
                                                    ...current,
                                                    mode: event.target.value,
                                                }))}
                                            >
                                                <option value='parallel_sleeves'>Parallel sleeves</option>
                                                <option value='shared_pipe'>Shared pipe</option>
                                            </select>
                                            <small className='tradeFieldHelp'>
                                                Parallel sleeves keeps each strategy in its own execution lane. Shared pipe keeps one open lane per symbol, so same-symbol sleeves take turns while different symbols still coexist independently.
                                            </small>
                                        </label>
                                        <label className='tradeField'>
                                            <span>Execution Path</span>
                                            <select
                                                value={String(tradeState?.executionMode || 'paper')}
                                                onChange={(event) => updateTradeState((current) => ({
                                                    ...current,
                                                    executionMode: event.target.value,
                                                }))}
                                            >
                                                <option value='simulation_backtest'>Simulation backtest</option>
                                                <option value='paper'>Paper / dry-run</option>
                                                <option value='live_mt5'>Live MT5</option>
                                            </select>
                                            <small className='tradeFieldHelp'>
                                                Paper evaluates live market updates and generates intents without sending MT5 orders. Live MT5 enables the real broker-command path.
                                            </small>
                                        </label>
                                        <label className='tradeField'>
                                            <span>Signal validity window (s)</span>
                                            <input
                                                type='number'
                                                min='0'
                                                value={Number(tradeState?.signalValiditySeconds ?? 10)}
                                                onChange={(event) => updateTradeState((current) => ({
                                                    ...current,
                                                    signalValiditySeconds: Math.max(0, Number(event.target.value || 0) || 0),
                                                }))}
                                            />
                                            <small className='tradeFieldHelp'>
                                                For next-bar-open signals, this is how many seconds after the new candle opens the live runtime may still execute the signal. Use <code>0</code> to keep it valid for the whole candle. <code>10</code> is a good default for M1. <code>3-5</code> is stricter. <code>15-20</code> is more tolerant of delay.
                                            </small>
                                        </label>
                                        <label className='tradeField'>
                                            <span>Latency budget (ms)</span>
                                            <input
                                                type='number'
                                                min='1'
                                                value={Number(tradeState?.latencyBudgetMs || 150)}
                                                onChange={(event) => updateTradeState((current) => ({
                                                    ...current,
                                                    latencyBudgetMs: Math.max(1, Number(event.target.value || 1)),
                                                }))}
                                            />
                                            <small className='tradeFieldHelp'>
                                                Operational target for how quickly the runtime should react after market updates.
                                            </small>
                                        </label>
                                        <label className='tradeField'>
                                            <span>Same-symbol execution policy</span>
                                            <select
                                                value={String(tradeState?.sameSymbolExecutionPolicy || 'independent')}
                                                onChange={(event) => updateTradeState((current) => ({
                                                    ...current,
                                                    sameSymbolExecutionPolicy: event.target.value,
                                                }))}
                                            >
                                                <option value='independent'>Independent</option>
                                                <option value='single_active_per_symbol'>Single active per symbol</option>
                                                <option value='block_conflicts'>Block conflicting sides</option>
                                            </select>
                                        </label>
                                        <small className='tradeFieldHelp'>
                                            {localSameSymbolPolicyHelp}
                                        </small>
                                        <div className='tradeActions tradeConfigActions'>
                                            <button
                                                type='button'
                                                className={`tradePrimaryAction ${isRuntimeConfigSaved ? 'isSavedAction' : 'isPendingSaveAction'}`}
                                                title={isGuest ? guestRestrictionMessage : undefined}
                                                onClick={() => void postTradeRuntime(
                                                    '/trade/runtime/configure',
                                                    buildConfigurePayload(),
                                                    'Trade · Runtime configuration saved to backend.',
                                                )}
                                                disabled={isGuest || isSubmitting || isRuntimeConfigSaved}
                                            >
                                                {isRuntimeConfigSaved ? 'Runtime saved' : 'Save Runtime'}
                                            </button>
                                            <button
                                                type='button'
                                                className='tradeSecondaryAction isDangerAction'
                                                onClick={() => void clearRuntimeConfig()}
                                                disabled={isGuest || isSubmitting || !hasAnyRuntimeSleeves}
                                            >
                                                Clear Runtime
                                            </button>
                                            {!isRuntimeConfigSaved ? (
                                                <button
                                                    type='button'
                                                    className='tradeSecondaryAction'
                                                    onClick={restoreSavedRuntimeConfig}
                                                    disabled={isSubmitting}
                                                >
                                                    Restore saved config
                                                </button>
                                            ) : null}
                                        </div>
                                        {requestError ? (
                                            <div className='tradeErrorLog' role='alert'>
                                                <strong>Runtime request error</strong>
                                                <span>{requestError}</span>
                                            </div>
                                        ) : null}
                                        {runtimeNeedsSleeves ? (
                                            <div className='tradeRuntimeWarning'>
                                                Runtime armed, but there are no sleeves loaded in the backend. Load at least one saved strategy and save the runtime before expecting intents or MT5 commands.
                                            </div>
                                        ) : null}
                                    </section>
                                </div>

                                <section className='tradeCard tradeStrategyRuntimeCard'>
                                    <h4>Strategy runtime</h4>
                                    <div className='tradeImportCard'>
                                        {(librarySourceTab === 'portfolios' ? selectedPortfolioLibraryItem : selectedStrategyLibraryItem) ? (
                                            <>
                                                <div className='tradeImportHeader'>
                                                    <strong>
                                                        {librarySourceTab === 'portfolios'
                                                            ? (selectedPortfolioLibraryItem?.label || `Portfolio #${selectedPortfolioLibraryItem?.id}`)
                                                            : (selectedStrategyLibraryItem?.label || `Strategy #${selectedStrategyLibraryItem?.id}`)}
                                                    </strong>
                                                    <span>
                                                        {librarySourceTab === 'portfolios'
                                                            ? 'Load the selected saved portfolio as one or more runtime pipelines. The runtime keeps its own compiled sleeve snapshots after import.'
                                                            : 'Load the selected saved strategy as one or more runtime sleeves. The runtime keeps its own sleeve snapshots after import.'}
                                                    </span>
                                                </div>
                                                {librarySourceTab === 'portfolios' ? (
                                                    <>
                                                        <div className='tradeImportPreview'>
                                                            {selectedPortfolioLibraryItem?.source || 'portfolio bundle'} · {summarizeSavedPortfolio(selectedPortfolioLibraryItem).pipelineCount} pipeline(s) · {summarizeSavedPortfolio(selectedPortfolioLibraryItem).entryCount} strategy entries
                                                        </div>
                                                        <div className='tradeStrategyNameBadge'>
                                                            <span>Selected portfolio</span>
                                                            <strong>{selectedPortfolioLibraryItem?.label || `Portfolio #${selectedPortfolioLibraryItem?.id}`}</strong>
                                                        </div>
                                                    </>
                                                ) : (
                                                    <>
                                                        <div className='tradeImportPreview'>
                                                            {selectedStrategyLibraryItem.source || 'manual'}{selectedStrategyLibraryItem.side ? ` · ${selectedStrategyLibraryItem.side}` : ''} · {selectedStrategyPrimaryMarket.symbol || 'primary market missing'}{selectedStrategyPrimaryMarket.timeframe ? ` ${selectedStrategyPrimaryMarket.timeframe}` : ''} · {Array.isArray(selectedStrategyLibraryItem?.strategies) ? selectedStrategyLibraryItem.strategies.length : 0} companion entries
                                                        </div>
                                                        <div className='tradeStrategyNameBadge'>
                                                            <span>Selected strategy</span>
                                                            <strong>{selectedStrategyLibraryItem.label || `Strategy #${selectedStrategyLibraryItem.id}`}</strong>
                                                        </div>
                                                    </>
                                                )}
                                                <div className='tradeActions'>
                                                    <button type='button' onClick={librarySourceTab === 'portfolios' ? handleAddSavedPortfolioToRuntime : handleAddSavedStrategyToRuntime}>
                                                        {librarySourceTab === 'portfolios' ? 'Include selected portfolio' : 'Include selected in runtime'}
                                                    </button>
                                                </div>
                                            </>
                                        ) : (
                                            <div className='tradeEmpty'>
                                                {librarySourceTab === 'portfolios'
                                                    ? 'Select a saved portfolio on the left to inspect it and include it in the trader runtime.'
                                                    : 'Select a saved strategy on the left to inspect it and include it in the runtime sleeves.'}
                                            </div>
                                        )}
                                    </div>
                                    {!sleeves.length ? (
                                        <div className='tradeEmpty'>
                                            No sleeves configured yet. Include a saved strategy or portfolio here, then click Save Runtime.
                                        </div>
                                    ) : (
                                        <div className='tradeSleeveList'>
                                            {sleeves.map((entry) => (
                                                <div key={entry.id} className={`tradeSleeveRow ${entry.enabled ? '' : 'isDisabled'}`.trim()}>
                                                    <div className='tradeSleeveBody'>
                                                        <div className='tradeSleeveMain'>
                                                            <strong>{entry.label}</strong>
                                                            <span>{entry.symbol} · {entry.timeframe} · {Number(entry.volume || 0).toFixed(2)} lot</span>
                                                            {entry.portfolioLabel || entry.pipelineLabel ? (
                                                                <span>{entry.portfolioLabel || 'Portfolio'}{entry.pipelineLabel ? ` · ${entry.pipelineLabel}` : ''}</span>
                                                            ) : null}
                                                            <div className='tradeStrategyNameBadge isCompact'>
                                                                <span>Strategy loaded</span>
                                                                <strong>{entry.strategyName || 'Manual strategy'}</strong>
                                                            </div>
                                                            <span>{entry.sourceStrategyId ? `source: ${entry.sourceStrategyId}` : 'manual sleeve'}</span>
                                                        </div>
                                                        <label className='tradeField tradeSleeveVolumeField'>
                                                            <span>Volume</span>
                                                            <input
                                                                type='number'
                                                                min='0.01'
                                                                step='0.01'
                                                                value={Number(entry.volume || 0.01)}
                                                                onChange={(event) => {
                                                                    const nextVolume = Math.max(0.01, Number(event.target.value || 0.01) || 0.01)
                                                                    updateTradeState((current) => ({
                                                                        ...current,
                                                                        sleeves: (Array.isArray(current?.sleeves) ? current.sleeves : []).map((candidate, candidateIndex) => {
                                                                            const normalizedCandidate = normalizeSleeve(candidate, candidateIndex)
                                                                            if (String(normalizedCandidate.id) !== String(entry.id)) {
                                                                                return normalizedCandidate
                                                                            }
                                                                            return {
                                                                                ...normalizedCandidate,
                                                                                volume: nextVolume,
                                                                            }
                                                                        }),
                                                                    }))
                                                                }}
                                                            />
                                                            <small className='tradeFieldHelp'>
                                                                Fixed order volume for this sleeve in live execution.
                                                            </small>
                                                        </label>
                                                        <TradeStrategyReadOnly strategy={entry.strategy} />
                                                    </div>
                                                    <div className='tradeSleeveActions'>
                                                        <button type='button' onClick={() => handleRemoveSleeve(entry.id)}>Remove</button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </section>
                            </div>
                        </div>
                    </div>
                </div>
            ) : null}

            {activeSubTab === 'runtime' ? (
                <div className='tradeMonitorLayout'>
                    <section className='tradePanel tradeMonitorSection'>
                        <div className='tradeMonitorSectionHeader'>
                            <div>
                                <strong>Runtime controls</strong>
                                <span>Operate the live session, force checks, advance the queue, and recover from stuck broker state.</span>
                            </div>
                        </div>
                        {isGuest ? (
                            <div className='tradeGuestRestriction' role='alert'>
                                <strong>Guest access</strong>
                                <span>{guestRestrictionMessage}</span>
                            </div>
                        ) : null}
                        <div className='tradeRuntimeControlGrid'>
                            <div className='tradeActionGroup'>
                                <strong>Operator state</strong>
                                <span className='tradeActionGroupHelp'>
                                    Arm turns the runtime on so it can evaluate the loaded sleeve and generate decisions or intents.
                                </span>
                                <div className='tradeActions'>
                                    <button
                                        type='button'
                                        className={isRuntimeArmed ? 'isActiveAction' : 'isInactiveAction'}
                                        title={isGuest ? guestRestrictionMessage : undefined}
                                        onClick={() => void postTradeRuntime(
                                            isRuntimeArmed ? '/trade/runtime/disarm' : '/trade/runtime/arm',
                                            null,
                                            isRuntimeArmed ? 'Trade · Runtime disarmed.' : 'Trade · Runtime armed.',
                                        )}
                                        disabled={isGuest || isSubmitting}
                                    >
                                        {isRuntimeArmed ? 'Armed' : 'Disarmed'}
                                    </button>
                                </div>
                            </div>
                            <div className='tradeActionGroup'>
                                <strong>Live dispatch safety</strong>
                                <span className='tradeActionGroupHelp'>
                                    This second gate only matters for real MT5 execution. It allows broker commands after the runtime is already armed.
                                </span>
                                <div className='tradeActions'>
                                    <button
                                        type='button'
                                        className={isLiveDispatchArmed ? 'isDangerAction isActiveAction' : 'isInactiveAction'}
                                        title={liveDispatchDisabledReason || undefined}
                                        onClick={() => void postTradeRuntime(
                                            isLiveDispatchArmed ? '/trade/runtime/disarm-live-dispatch' : '/trade/runtime/arm-live-dispatch',
                                            null,
                                            isLiveDispatchArmed ? 'Trade · Live dispatch disarmed.' : 'Trade · Live dispatch armed.',
                                        )}
                                        disabled={isGuest || isSubmitting || !isLiveExecutionMode}
                                    >
                                        {isLiveDispatchArmed ? 'Live Dispatch Armed' : 'Live Dispatch Disarmed'}
                                    </button>
                                </div>
                                {liveDispatchDisabledReason ? (
                                    <div className='tradeActionHint isWarning'>
                                        {liveDispatchDisabledReason}
                                    </div>
                                ) : null}
                            </div>
                            <div className='tradeActionGroup'>
                                <strong>Evaluation and queue</strong>
                                <span className='tradeActionGroupHelp'>
                                    Evaluate checks the strategy immediately. Process Intents advances the current queue without requiring a new market update.
                                </span>
                                <div className='tradeActions'>
                                    <button
                                        type='button'
                                        title={isGuest ? guestRestrictionMessage : undefined}
                                        onClick={() => void postTradeRuntime('/trade/runtime/evaluate', null, 'Trade · Runtime evaluated.')}
                                        disabled={isGuest || isSubmitting}
                                    >
                                        Evaluate Now
                                    </button>
                                    <button
                                        type='button'
                                        title={isGuest ? guestRestrictionMessage : undefined}
                                        onClick={() => void postTradeRuntime('/trade/runtime/process-intents', null, 'Trade · Order intents processed.')}
                                        disabled={isGuest || isSubmitting}
                                    >
                                        Process Intents
                                    </button>
                                </div>
                            </div>
                            <div className='tradeActionGroup'>
                                <strong>Recovery</strong>
                                <span className='tradeActionGroupHelp'>
                                    Reconcile checks for stale command state. Reset Commands clears the broker-command queue without rebuilding the whole runtime.
                                </span>
                                <div className='tradeActions'>
                                    <button
                                        type='button'
                                        title={isGuest ? guestRestrictionMessage : undefined}
                                        onClick={() => void postTradeRuntime('/trade/runtime/reconcile', null, 'Trade · Runtime reconciliation completed.')}
                                        disabled={isGuest || isSubmitting}
                                    >
                                        Reconcile
                                    </button>
                                    <button
                                        type='button'
                                        title={isGuest ? guestRestrictionMessage : undefined}
                                        onClick={() => void postTradeRuntime('/trade/runtime/reset-commands', { clearIntents: false }, 'Trade · Broker command queue reset.')}
                                        disabled={isGuest || isSubmitting}
                                    >
                                        Reset Commands
                                    </button>
                                </div>
                            </div>
                        </div>
                    </section>
                    <div className='tradeMonitorChartsRow'>
                        <RuntimeMiniChart
                            title='Latency'
                            subtitle='Recent runtime latency events in the current trade session.'
                            values={latencySeries.values}
                            labels={latencySeries.labels}
                            valueFormatter={(value) => `${Number(value).toFixed(1)} ms`}
                            tone='latency'
                        />
                        <RuntimeMiniChart
                            title='Balance'
                            subtitle='Cumulative realized PnL using the same filter currently selected in History.'
                            values={balanceSeries.values}
                            labels={balanceSeries.labels}
                            valueFormatter={(value) => formatTradeMoney(value)}
                        />
                    </div>
                    <section className='tradePanel tradeMonitorSection'>
                        <div className='tradeMonitorSectionHeader'>
                            <div>
                                <strong>Runtime session</strong>
                                <span>Current execution state, gate status and session-level results.</span>
                            </div>
                        </div>
                        <div className='tradeInfoGrid'>
                            <MonitorStatCard label='Operator state' value={runtimeState?.armed ? 'Armed' : 'Disarmed'} />
                            <MonitorStatCard label='Broker gate' value={runtimeState?.live_dispatch_armed ? 'Live dispatch armed' : 'Live dispatch safe'} />
                            <MonitorStatCard label='Runtime status' value={runtimeStatus} />
                            <MonitorStatCard label='Execution path' value={runtimeState?.execution_mode || tradeState?.executionMode || 'paper'} />
                            <MonitorStatCard label='Broker account mode' value={brokerAccountModeLabel} />
                            <MonitorStatCard label='Broker hedge support' value={brokerHedgeSupportLabel} />
                            <MonitorStatCard label='Same-symbol policy' value={formatSameSymbolExecutionPolicyLabel(runtimeSameSymbolPolicy, { mode: runtimeTradeMode })} />
                            <MonitorStatCard label='Signal window' value={`${runtimeState?.signal_validity_seconds ?? tradeState?.signalValiditySeconds ?? 10}s`} />
                            <MonitorStatCard label='Last runtime event' value={runtimeState?.last_event_at || '—'} />
                            <MonitorStatCard label='Console/backend sync' value={isSyncing ? 'Syncing' : 'Ready'} />
                            <MonitorStatCard label='Last latency (ms)' value={runtimeMetrics.last_latency_ms ?? '—'} />
                            <MonitorStatCard label='Max latency (ms)' value={runtimeMetrics.max_latency_ms ?? '—'} />
                            <MonitorStatCard label='Active symbols' value={runtimeState?.active_symbols?.join(', ') || '—'} />
                            <MonitorStatCard label='Configured sleeves' value={(runtimeState?.sleeves || []).length} />
                            <MonitorStatCard label='Current session since activation' value={currentSessionLabel} emphasis />
                            <MonitorStatCard label='Orders sent to broker' value={runtimeMetrics.command_count ?? 0} emphasis />
                            <MonitorStatCard label='Filled in current session' value={runtimeMetrics.command_fill_count ?? 0} emphasis />
                            <MonitorStatCard label='Rejected in current session' value={runtimeMetrics.command_reject_count ?? 0} emphasis />
                            <MonitorStatCard label='Generated order intents' value={runtimeMetrics.dispatch_count ?? 0} emphasis />
                            <MonitorStatCard label='Market feed' value={marketFeed?.status || 'idle'} />
                            <MonitorStatCard label='Last market update' value={formatRelativeTimestamp(marketFeed?.last_update_at)} />
                            <MonitorStatCard label='Current blocking error' value={primaryRuntimeError || 'None'} emphasis />
                        </div>
                        <div className='tradeMonitorBanner'>
                            <strong>{isRuntimeArmed ? 'Trade runtime is running.' : 'Trade runtime is stopped.'}</strong>
                            <span>
                                {primaryRuntimeError
                                    ? `The current runtime is blocked by an operational error: ${primaryRuntimeError}`
                                    : primarySleeveOperationalState?.tone === 'pending'
                                        ? primarySleeveOperationalState.detail
                                    : primarySleeveOperationalState?.tone === 'issue'
                                        ? primarySleeveOperationalState.detail
                                    : String(marketFeed?.status || '').toLowerCase() === 'waiting'
                                        ? (marketFeed?.detail || 'Waiting for the first live market update.')
                                    : orderCommands.length
                                        ? `There are ${orderCommands.length} broker command(s) recorded in this session.`
                                        : isLiveDispatchArmed
                                            ? 'Live dispatch is armed, but no broker command was generated yet.'
                                            : 'Live dispatch is still blocked; evaluation can run without sending MT5 orders.'}
                            </span>
                        </div>
                        {isLiveExecutionMode ? (
                            <div className={`tradeMonitorBanner tradeMonitorAccountBanner is-${brokerAccountBannerTone}`.trim()}>
                                <strong>{brokerAccountBannerTitle}</strong>
                                <span>{brokerAccountBannerDetail}</span>
                            </div>
                        ) : null}
                        {primaryRuntimeError ? (
                            <div className='tradeErrorLog tradeErrorLogMonitor' role='alert'>
                                <strong>Blocking runtime error</strong>
                                <span>{primaryRuntimeError}</span>
                            </div>
                        ) : null}
                        {sleeveOperationalStates.length ? (
                            <div className='tradeSleeveOperationalGrid'>
                                {sleeveOperationalStates.map((entry) => (
                                    <div key={entry.sleeveId || entry.label} className={`tradeSleeveOperationalCard is-${entry.tone}`.trim()}>
                                        <strong>{activeSleeveStates.find((sleeve) => sleeve?.sleeve_id === entry.sleeveId)?.label || 'Sleeve'}</strong>
                                        <span className={`tradeOperationalPill is-${entry.tone}`.trim()}>{entry.label}</span>
                                        <small>{entry.detail}</small>
                                    </div>
                                ))}
                            </div>
                        ) : null}
                    </section>
                    <section className='tradePanel tradeMonitorSection'>
                        <div className='tradeMonitorSectionHeader'>
                            <div>
                                <strong>Operational metrics</strong>
                                <span>Aggregate counters for the current runtime session, queue, and broker path.</span>
                            </div>
                        </div>
                        <div className='tradeInfoGrid'>
                            <MonitorStatCard label='Audit events' value={runtimeMetrics.event_count ?? 0} />
                            <MonitorStatCard label='Decisions' value={runtimeMetrics.decision_count ?? 0} />
                            <MonitorStatCard label='Order intents' value={runtimeMetrics.dispatch_count ?? 0} />
                            <MonitorStatCard label='Acknowledgements' value={runtimeMetrics.ack_count ?? 0} />
                            <MonitorStatCard label='Fills' value={runtimeMetrics.fill_count ?? 0} />
                            <MonitorStatCard label='Broker commands' value={runtimeMetrics.command_count ?? 0} />
                            <MonitorStatCard label='Broker acks' value={runtimeMetrics.command_ack_count ?? 0} />
                            <MonitorStatCard label='Broker fills' value={runtimeMetrics.command_fill_count ?? 0} />
                            <MonitorStatCard label='Broker rejects' value={runtimeMetrics.command_reject_count ?? 0} />
                            <MonitorStatCard label='Portfolio mode' value={runtimeState?.mode || tradeState?.mode || 'parallel_sleeves'} />
                            <MonitorStatCard label='Queued intents' value={orderIntents.length} />
                            <MonitorStatCard label='Open commands' value={orderCommands.filter((entry) => ['queued', 'claimed', 'acknowledged'].includes(String(entry?.status || '').toLowerCase())).length} />
                        </div>
                    </section>
                    <section className='tradePanel tradeMonitorSection'>
                        <div className='tradeMonitorSectionHeader'>
                            <div>
                                <strong>Latest evaluations</strong>
                                <span>What each loaded strategy last concluded when the runtime evaluated the market.</span>
                            </div>
                        </div>
                        {runtimeState?.sleeve_states && Object.keys(runtimeState.sleeve_states).length ? (
                            <div className='tradeTableViewport'>
                                <table className='tradeTable'>
                                    <thead>
                                        <tr>
                                            <th>Strategy</th>
                                            <th>Market</th>
                                            <th>Operational state</th>
                                            <th>Status</th>
                                            <th>Decision</th>
                                            <th>Position</th>
                                            <th>Broker side</th>
                                            <th>Broker ticket</th>
                                            <th>Broker positions</th>
                                            <th>Latency</th>
                                            <th>Last check</th>
                                            <th>Error</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {Object.values(runtimeState.sleeve_states).map((entry) => {
                                            const operationalState = buildSleeveOperationalState(entry, orderIntents, orderCommands)
                                            return (
                                            <tr key={entry?.sleeve_id || entry?.label}>
                                                <td>{entry?.label || entry?.sleeve_id || 'Sleeve'}</td>
                                                <td>{entry?.symbol || '—'} · {entry?.timeframe || '—'}</td>
                                                <td>
                                                    <span className={`tradeOperationalPill is-${operationalState.tone}`.trim()}>
                                                        {operationalState.label}
                                                    </span>
                                                </td>
                                                <td>{entry?.status || 'idle'}</td>
                                                <td>{entry?.decision || 'hold'}</td>
                                                <td>{entry?.position ?? 0}</td>
                                                <td>{entry?.broker_position_side || entry?.actual_position_side || 'flat'}</td>
                                                <td>{Array.isArray(entry?.broker_position_tickets) && entry.broker_position_tickets.length ? entry.broker_position_tickets.join(', ') : entry?.broker_position_ticket || '—'}</td>
                                                <td>{entry?.broker_position_count ?? 0}</td>
                                                <td>{entry?.last_latency_ms ?? '—'} ms</td>
                                                <td>{formatRelativeTimestamp(entry?.last_evaluated_at)}</td>
                                                <td>{entry?.last_error || '—'}</td>
                                            </tr>
                                        )})}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <div className='tradeEmpty'>No strategy evaluation has been recorded in this session yet.</div>
                        )}
                    </section>
                    <section className='tradePanel tradeMonitorSection'>
                        <div className='tradeMonitorSectionHeader'>
                            <div>
                                <strong>Intent queue</strong>
                                <span>Actions the strategies decided to take and where each one currently sits in the pipeline.</span>
                            </div>
                        </div>
                        {orderIntents.length ? (
                            <div className='tradeTableViewport'>
                                <table className='tradeTable'>
                                    <thead>
                                        <tr>
                                            <th>Created</th>
                                            <th>Strategy</th>
                                            <th>Market</th>
                                            <th>Action</th>
                                            <th>Decision</th>
                                            <th>Status</th>
                                            <th>Trigger</th>
                                            <th>Bar time</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {orderIntents.map((entry) => (
                                            <tr key={entry?.id || `${entry?.sleeve_id}-${entry?.created_at}`}>
                                                <td>{formatRelativeTimestamp(entry?.created_at)}</td>
                                                <td>{entry?.sleeve_label || entry?.sleeve_id || 'Sleeve'}</td>
                                                <td>{entry?.symbol || '—'} · {entry?.timeframe || '—'}</td>
                                                <td>{entry?.action || 'intent'} {entry?.side || ''}</td>
                                                <td>{entry?.decision || '—'}</td>
                                                <td>
                                                    <div className='tradeCellWithToken'>
                                                        <span>{entry?.status || 'queued'}</span>
                                                        <InvalidStopsToken detail={entry?.rejection_message || entry?.message || ''} />
                                                    </div>
                                                </td>
                                                <td>{entry?.trigger || '—'}</td>
                                                <td>{entry?.bar_time ?? '—'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <div className='tradeEmpty'>No strategy action is waiting in the intent queue.</div>
                        )}
                    </section>
                    <section className='tradePanel tradeMonitorSection'>
                        <div className='tradeMonitorSectionHeader'>
                            <div>
                                <strong>Broker command queue</strong>
                                <span>Commands already promoted past intents and prepared for the broker path.</span>
                            </div>
                        </div>
                        {orderCommands.length ? (
                            <div className='tradeTableViewport tradeCommandTableViewport'>
                                <table className='tradeTable'>
                                    <thead>
                                        <tr>
                                            <th>Created</th>
                                            <th>Strategy</th>
                                            <th>Market</th>
                                            <th>Action</th>
                                            <th>Status</th>
                                            <th>Age</th>
                                            <th>Order</th>
                                            <th>Message</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {orderCommands.map((entry) => (
                                            <tr key={entry?.id || `${entry?.sleeve_id}-${entry?.created_at}`}>
                                                <td>{formatRelativeTimestamp(entry?.created_at)}</td>
                                                <td>{entry?.sleeve_label || entry?.sleeve_id || 'Sleeve'}</td>
                                                <td>{entry?.symbol || '—'} · {entry?.timeframe || '—'}</td>
                                                <td>{entry?.action || 'order'} {entry?.side || ''}</td>
                                                <td>
                                                    <div className='tradeCellWithToken'>
                                                        <span>{entry?.status || 'queued'}</span>
                                                        <InvalidStopsToken detail={entry?.message || ''} />
                                                    </div>
                                                </td>
                                                <td>{entry?.age_seconds != null ? `${entry.age_seconds}s` : '—'}</td>
                                                <td>{entry?.broker_order_id || '—'}</td>
                                                <td>
                                                    <div className='tradeCellWithToken'>
                                                        <span>{entry?.message || '—'}</span>
                                                        <InvalidStopsToken detail={entry?.message || ''} />
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <div className='tradeEmpty'>No broker command has been queued in this session yet.</div>
                        )}
                    </section>
                </div>
            ) : null}

            {activeSubTab === 'audit' ? (
                <div className='tradePanel'>
                    <section className='tradeAuditLogPanel'>
                        <div className='tradeMonitorSectionHeader'>
                            <div>
                                <strong>Runtime audit log</strong>
                                <span>Raw runtime events, live intents and broker commands recorded during the current session.</span>
                            </div>
                            <div className='tradeAuditPagination'>
                                <button
                                    type='button'
                                    onClick={() => setAuditLogPage((current) => Math.max(0, current - 1))}
                                    disabled={pagedAuditEvents.currentPage <= 0}
                                >
                                    Prev
                                </button>
                                <label className='tradeAuditPaginationInput'>
                                    <span>Page</span>
                                    <input
                                        type='number'
                                        min='1'
                                        max={pagedAuditEvents.totalPages}
                                        value={pagedAuditEvents.currentPage + 1}
                                        onChange={(event) => {
                                            const nextPage = Math.max(
                                                1,
                                                Math.min(
                                                    pagedAuditEvents.totalPages,
                                                    Number(event.target.value || 1) || 1,
                                                ),
                                            )
                                            setAuditLogPage(nextPage - 1)
                                        }}
                                    />
                                </label>
                                <span>/ {pagedAuditEvents.totalPages}</span>
                                <button
                                    type='button'
                                    onClick={() => setAuditLogPage((current) => Math.min(pagedAuditEvents.totalPages - 1, current + 1))}
                                    disabled={pagedAuditEvents.currentPage >= pagedAuditEvents.totalPages - 1}
                                >
                                    Next
                                </button>
                            </div>
                        </div>
                        <div className='tradeAuditViewport'>
                            {!pagedAuditEvents.rows.length ? (
                                <div className='tradeEmpty'>
                                    No runtime events yet.
                                </div>
                            ) : (
                                <div className='tradeAuditList'>
                                    {pagedAuditEvents.rows.map((entry) => (
                                        <div key={entry.id} className='tradeAuditRow'>
                                            <strong>{entry.title}</strong>
                                            <span>{entry.detail}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </section>
                </div>
            ) : null}

            {activeSubTab === 'history' ? (
                <div className='tradePanel tradeHistoryPanel'>
                    <div className='tradeMonitorSectionHeader'>
                        <div>
                            <strong>Live trade history</strong>
                            <span>Persistent broker-execution history for this workspace, with filters and real results when available.</span>
                        </div>
                    </div>

                    <div className='tradeHistoryFilters'>
                        <label className='tradeField'>
                            <span>Range</span>
                            <select
                                value={historyFilters.rangeKey}
                                onChange={(event) => updateTradeState((current) => ({
                                    ...current,
                                    historyFilters: {
                                        ...(current?.historyFilters || {}),
                                        rangeKey: event.target.value,
                                    },
                                }))}
                            >
                                {HISTORY_RANGE_OPTIONS.map((option) => (
                                    <option key={option.value} value={option.value}>{option.label}</option>
                                ))}
                            </select>
                        </label>

                        {historyFilters.rangeKey === 'custom' ? (
                            <label className='tradeField'>
                                <span>Days</span>
                                <input
                                    type='number'
                                    min='1'
                                    value={historyFilters.customDays}
                                    onChange={(event) => updateTradeState((current) => ({
                                        ...current,
                                        historyFilters: {
                                            ...(current?.historyFilters || {}),
                                            customDays: Math.max(1, Number(event.target.value || 1) || 1),
                                        },
                                    }))}
                                />
                            </label>
                        ) : null}

                        <label className='tradeField'>
                            <span>Strategy</span>
                            <input
                                type='text'
                                value={historyFilters.strategyFilter}
                                onChange={(event) => updateTradeState((current) => ({
                                    ...current,
                                    historyFilters: {
                                        ...(current?.historyFilters || {}),
                                        strategyFilter: event.target.value,
                                    },
                                }))}
                                placeholder='Filter by sleeve or strategy'
                            />
                        </label>

                        <label className='tradeField'>
                            <span>Symbol</span>
                            <input
                                type='text'
                                value={historyFilters.symbolFilter}
                                onChange={(event) => updateTradeState((current) => ({
                                    ...current,
                                    historyFilters: {
                                        ...(current?.historyFilters || {}),
                                        symbolFilter: event.target.value.toUpperCase(),
                                    },
                                }))}
                                placeholder='EURUSD'
                            />
                        </label>

                        <label className='tradeField'>
                            <span>Status</span>
                            <select
                                value={historyFilters.statusFilter}
                                onChange={(event) => updateTradeState((current) => ({
                                    ...current,
                                    historyFilters: {
                                        ...(current?.historyFilters || {}),
                                        statusFilter: event.target.value,
                                    },
                                }))}
                            >
                                {HISTORY_STATUS_OPTIONS.map((option) => (
                                    <option key={option.value} value={option.value}>{option.label}</option>
                                ))}
                            </select>
                        </label>

                        <div className='tradeActions tradeHistoryActions'>
                            <button
                                type='button'
                                onClick={() => void refreshTradeHistory()}
                                disabled={historyState.loading}
                            >
                                Refresh history
                            </button>
                        </div>
                    </div>

                    {historyState.error ? (
                        <div className='tradeErrorLog tradeErrorLogMonitor' role='alert'>
                            <strong>History error</strong>
                            <span>{historyState.error}</span>
                        </div>
                    ) : null}

                    <div className='tradeInfoGrid tradeHistorySummaryGrid'>
                        <MonitorStatCard label='Trades in filter' value={historyView.summary?.trade_count ?? 0} />
                        <MonitorStatCard label='Closed' value={historyView.summary?.closed_count ?? 0} />
                        <MonitorStatCard label='Open' value={historyView.summary?.open_count ?? 0} />
                        <MonitorStatCard label='Win rate' value={historyView.summary ? `${((historyView.summary.win_rate || 0) * 100).toFixed(1)}%` : '0.0%'} />
                        <MonitorStatCard label='Gross profit' value={formatTradeMoney(historyView.summary?.gross_profit)} emphasis />
                        <MonitorStatCard label='Commission' value={formatTradeMoney(historyView.summary?.commission_total)} />
                        <MonitorStatCard label='Swap' value={formatTradeMoney(historyView.summary?.swap_total)} />
                        <MonitorStatCard label='Realized PnL' value={formatTradeMoney(historyView.summary?.realized_pnl)} emphasis />
                    </div>

                    <div className='tradeHistoryTableViewport'>
                        {!historyView.rows.length ? (
                            <div className='tradeEmpty'>
                                {historyState.loading ? 'Loading live trade history...' : 'No live trades found for the current filters.'}
                            </div>
                        ) : (
                            <table className='tradeHistoryTable'>
                                <thead>
                                    <tr>
                                        <th>Status</th>
                                        <th>Strategy</th>
                                        <th>Symbol</th>
                                        <th>Side</th>
                                        <th>Broker ticket</th>
                                        <th>Entry time</th>
                                        <th>Exit time</th>
                                        <th>Entry</th>
                                        <th>Exit</th>
                                        <th>Volume</th>
                                        <th>Exit reason</th>
                                        <th>PnL</th>
                                        <th>Commission</th>
                                        <th>Swap</th>
                                        <th>Message</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {historyView.rows.map((entry) => (
                                        <tr key={entry?.id} className={entry?.state === 'open' ? 'isOpenTrade' : 'isClosedTrade'}>
                                            <td>
                                                <span className={`tradeHistoryStatePill ${entry?.state === 'open' ? 'isOpen' : 'isClosed'}`}>
                                                    {entry?.state === 'open' ? 'OPEN' : 'CLOSED'}
                                                </span>
                                            </td>
                                            <td>{entry?.strategyLabel || '—'}</td>
                                            <td>{entry?.symbol || '—'} · {entry?.timeframe || '—'}</td>
                                            <td>{String(entry?.side || '—').toUpperCase()}</td>
                                            <td>{entry?.brokerPositionTicket || '—'}</td>
                                            <td>{formatTradeTimestamp(entry?.entryTime)}</td>
                                            <td>{formatTradeTimestamp(entry?.exitTime)}</td>
                                            <td>{formatTradePrice(entry?.entryPrice)}</td>
                                            <td>{formatTradePrice(entry?.exitPrice)}</td>
                                            <td>{entry?.volume != null ? Number(entry.volume).toFixed(2) : '—'}</td>
                                            <td>{entry?.state === 'closed' ? (entry?.exitReason || '—') : '—'}</td>
                                            <td>{entry?.state === 'closed' ? formatTradeMoney(entry?.pnl) : '—'}</td>
                                            <td>{entry?.state === 'closed' ? formatTradeMoney(entry?.commission) : '—'}</td>
                                            <td>{entry?.state === 'closed' ? formatTradeMoney(entry?.swap) : '—'}</td>
                                            <td>{entry?.message || '—'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                </div>
            ) : null}

            {activeSubTab === 'reconciliation' ? (
                <div className='tradePanel'>
                    <section className='tradeAuditReconciliation'>
                        <div className='tradeMonitorSectionHeader'>
                            <div>
                                <strong>Backtester vs trader</strong>
                                <span>Replay the selected loaded sleeve in the backtester, show the latest operations from the replay and the live trader, then keep the full slot-by-slot audit below so mismatches are immediately visible.</span>
                            </div>
                        </div>
                        <div className='tradeHistoryFilters'>
                            <label className='tradeField'>
                                <span>Range</span>
                                <select
                                    value={reconciliationFilters.rangeKey}
                                    onChange={(event) => updateTradeState((current) => ({
                                        ...current,
                                        reconciliationFilters: {
                                            ...(current?.reconciliationFilters || {}),
                                            rangeKey: event.target.value,
                                        },
                                    }))}
                                >
                                    {RECONCILIATION_RANGE_OPTIONS.map((option) => (
                                        <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                </select>
                            </label>
                            {reconciliationFilters.rangeKey === 'custom' ? (
                                <label className='tradeField'>
                                    <span>Days</span>
                                    <input
                                        type='number'
                                        min='1'
                                        value={reconciliationFilters.customDays}
                                        onChange={(event) => updateTradeState((current) => ({
                                            ...current,
                                            reconciliationFilters: {
                                                ...(current?.reconciliationFilters || {}),
                                                customDays: Math.max(1, Number(event.target.value || 1) || 1),
                                            },
                                        }))}
                                    />
                                </label>
                            ) : null}
                            <label className='tradeField'>
                                <span>Strategy</span>
                                <select
                                    value={effectiveReconciliationStrategyFilter}
                                    onChange={(event) => updateTradeState((current) => ({
                                        ...current,
                                        reconciliationFilters: {
                                            ...(current?.reconciliationFilters || {}),
                                            strategyFilter: event.target.value,
                                        },
                                    }))}
                                >
                                    {reconciliationStrategyOptions.map((entry) => (
                                        <option key={entry.value} value={entry.value}>{entry.value}</option>
                                    ))}
                                </select>
                                <small className='tradeFieldHelp'>
                                    The comparison replays the selected loaded sleeve over the same time window, then lines it up against the live trades stored for that strategy.
                                </small>
                            </label>
                            <div className='tradeActions tradeHistoryActions'>
                                <button
                                    type='button'
                                    title={isGuest ? guestRestrictionMessage : undefined}
                                    onClick={() => void runReconciliation()}
                                    disabled={isGuest || reconciliationState.loading}
                                >
                                    Run comparison
                                </button>
                            </div>
                        </div>
                        <div className='tradeInfoGrid tradeHistorySummaryGrid'>
                            <MonitorStatCard label='Backtester ops' value={reconciliationState.summary?.expectedCount ?? 0} />
                            <MonitorStatCard label='Matched' value={reconciliationState.summary?.matchedCount ?? 0} />
                            <MonitorStatCard label='Rejected' value={reconciliationState.summary?.rejectedCount ?? 0} />
                            <MonitorStatCard label='Backtester-only' value={reconciliationState.summary?.missedCount ?? 0} />
                            <MonitorStatCard label='Trader-only' value={reconciliationState.summary?.unexpectedCount ?? 0} />
                            <MonitorStatCard label='Side mismatch' value={reconciliationState.summary?.sideMismatchCount ?? 0} />
                            <MonitorStatCard label='Compliance' value={reconciliationState.summary ? `${(reconciliationState.summary.matchRate * 100).toFixed(1)}%` : '0.0%'} emphasis />
                            <MonitorStatCard label='Avg entry drift' value={formatDelaySeconds(reconciliationState.summary?.avgEntryDriftSeconds)} />
                            <MonitorStatCard label='Max entry drift' value={formatDelaySeconds(reconciliationState.summary?.maxEntryDriftSeconds)} />
                            <MonitorStatCard label='Trader PnL' value={reconciliationState.summary ? formatTradeMoney(reconciliationState.summary.realizedPnl) : '—'} emphasis />
                        </div>
                        {reconciliationState.error ? (
                            <div className='tradeErrorLog tradeErrorLogMonitor' role='alert'>
                                <strong>Comparison error</strong>
                                <span>{reconciliationState.error}</span>
                            </div>
                        ) : null}
                        <div className='tradeReconciliationSnapshotGrid'>
                            <section className='tradeReconciliationSnapshotPanel'>
                                <div className='tradeReconciliationSnapshotHeader'>
                                    <div>
                                        <strong>Latest backtester operations</strong>
                                        <span>
                                            {reconciliationState.summary?.expectedCount ?? reconciliationState.expectedRows.length} replayed ops in the selected window.
                                        </span>
                                    </div>
                                </div>
                                <div className='tradeReconciliationOperationList'>
                                    {!recentBacktesterRows.length ? (
                                        <div className='tradeEmpty'>
                                            {reconciliationState.loading
                                                ? 'Waiting for replay results...'
                                                : 'Run the comparison to inspect the latest operations the backtester expected.'}
                                        </div>
                                    ) : recentBacktesterRows.map((entry) => {
                                        const expected = entry?.expected || {}
                                        return (
                                            <ReconciliationOperationCard
                                                key={`expected-${entry?.id}`}
                                                verdict={entry?.verdict}
                                                index={entry?.index}
                                                primary={`${formatTradeSide(expected?.side)} · ${formatTradeTimestamp(expected?.expected_entry_time)}`}
                                                secondary={expected?.expected_exit_time
                                                    ? `Exit ${formatTradeTimestamp(expected.expected_exit_time)}`
                                                    : 'Replay still open at the end of the window'}
                                                details={[
                                                    `Entry ${formatTradePrice(expected?.expected_entry_price)}`,
                                                    expected?.expected_exit_price ? `Exit px ${formatTradePrice(expected.expected_exit_price)}` : '',
                                                    expected?.expected_exit_reason ? `Reason ${expected.expected_exit_reason}` : `State ${formatTradeStateLabel(expected?.expected_state)}`,
                                                ]}
                                                note={entry?.note}
                                            />
                                        )
                                    })}
                                </div>
                            </section>
                            <section className='tradeReconciliationSnapshotPanel'>
                                <div className='tradeReconciliationSnapshotHeader'>
                                    <div>
                                        <strong>Latest trader operations</strong>
                                        <span>
                                            {reconciliationState.summary?.actualCount ?? reconciliationState.actualRows.length} live ops in the same comparison window.
                                        </span>
                                    </div>
                                </div>
                                <div className='tradeReconciliationOperationList'>
                                    {!recentTraderRows.length ? (
                                        <div className='tradeEmpty'>
                                            {reconciliationState.loading
                                                ? 'Waiting for live trade comparison...'
                                                : 'Run the comparison to inspect the latest operations the trader actually executed.'}
                                        </div>
                                    ) : recentTraderRows.map((entry) => {
                                        const actual = entry?.actual || {}
                                        return (
                                            <ReconciliationOperationCard
                                                key={`actual-${entry?.id}`}
                                                verdict={entry?.verdict}
                                                index={entry?.index}
                                                primary={`${formatTradeSide(actual?.side)} · ${formatTradeTimestamp(actual?.actual_entry_time)}`}
                                                secondary={actual?.actual_exit_time
                                                    ? `Exit ${formatTradeTimestamp(actual.actual_exit_time)}`
                                                    : `State ${formatTradeStateLabel(actual?.actual_state)}`}
                                                details={[
                                                    `Entry ${formatTradePrice(actual?.actual_entry_price)}`,
                                                    actual?.actual_exit_price ? `Exit px ${formatTradePrice(actual.actual_exit_price)}` : '',
                                                    actual?.actual_exit_reason ? `Reason ${actual.actual_exit_reason}` : '',
                                                    `P&L ${formatTradeMoney(actual?.pnl)}`,
                                                    actual?.broker_position_ticket ? `Ticket ${actual.broker_position_ticket}` : '',
                                                ]}
                                                note={actual?.message || entry?.note}
                                            />
                                        )
                                    })}
                                </div>
                            </section>
                        </div>
                        <div className='tradeTableViewport tradeReconciliationViewport'>
                            {!reconciliationAuditRows.length ? (
                                <div className='tradeEmpty'>
                                    {reconciliationState.loading
                                        ? 'Replaying strategy and comparing against live executions...'
                                        : 'Run the comparison to inspect the paired backtester/trader audit for the selected sleeve.'}
                                </div>
                            ) : (
                                <table className='tradeTable tradeReconciliationTable'>
                                    <thead>
                                        <tr>
                                            <th>Slot</th>
                                            <th>Verdict</th>
                                            <th>Backtester entry</th>
                                            <th>Backtester side</th>
                                            <th>Backtester exit</th>
                                            <th>Trader entry</th>
                                            <th>Trader side</th>
                                            <th>Trader result</th>
                                            <th>Entry drift</th>
                                            <th>Trader PnL</th>
                                            <th>Note</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {reconciliationAuditRows.map((entry) => {
                                            const verdictMeta = buildComparisonVerdictMeta(entry?.verdict)
                                            const expected = entry?.expected || {}
                                            const actual = entry?.actual || {}
                                            return (
                                                <tr key={entry?.id} className={`is-${verdictMeta.tone}`.trim()}>
                                                    <td>{entry?.index || '—'}</td>
                                                    <td>
                                                        <span className={`tradeOperationalPill is-${verdictMeta.tone}`.trim()}>
                                                            {verdictMeta.label}
                                                        </span>
                                                    </td>
                                                    <td>{formatTradeTimestamp(expected?.expected_entry_time)}</td>
                                                    <td>{formatTradeSide(expected?.side)}</td>
                                                    <td>
                                                        {expected?.expected_exit_time
                                                            ? `${formatTradeTimestamp(expected.expected_exit_time)}${expected?.expected_exit_reason ? ` · ${expected.expected_exit_reason}` : ''}`
                                                            : '—'}
                                                    </td>
                                                    <td>{formatTradeTimestamp(actual?.actual_entry_time)}</td>
                                                    <td>{formatTradeSide(actual?.side)}</td>
                                                    <td>
                                                        <span className='tradeCellWithToken'>
                                                            <span>{formatTradeStateLabel(actual?.actual_state)}</span>
                                                            <InvalidStopsToken detail={actual?.message || ''} />
                                                        </span>
                                                    </td>
                                                    <td>{formatDelaySeconds(entry?.entry_drift_seconds)}</td>
                                                    <td>{formatTradeMoney(actual?.pnl)}</td>
                                                    <td>{entry?.note || actual?.message || '—'}</td>
                                                </tr>
                                            )
                                        })}
                                    </tbody>
                                </table>
                            )}
                        </div>
                        {reconciliationAuditRows.length ? (
                            <div className='tradeReconciliationLegend'>
                                <div className='tradeReconciliationLegendTitle'>Compare verdict legend</div>
                                <div className='tradeReconciliationLegendList'>
                                    {COMPARISON_VERDICT_LEGEND.map((item) => {
                                        const verdictMeta = buildComparisonVerdictMeta(item.verdict)
                                        return (
                                            <div key={item.verdict} className='tradeReconciliationLegendItem'>
                                                <span className={`tradeOperationalPill is-${verdictMeta.tone}`.trim()}>
                                                    {verdictMeta.label}
                                                </span>
                                                <span>{item.description}</span>
                                            </div>
                                        )
                                    })}
                                </div>
                            </div>
                        ) : null}
                    </section>
                </div>
            ) : null}
        </div>
    )
}

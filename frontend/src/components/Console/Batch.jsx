import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import './Batch.css'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import { BatchManager } from '/src/components/BatchManager'
import { BacktestConfigEditor } from './BacktestConfigEditor'
import { BatchFeaturesPanel } from './BatchFeaturesPanel'
import { BACKTEST_DEFAULTS } from './backtestDefaults.js'
import {
    BACKTEST_COST_PROFILE_DEFINITIONS,
    buildBacktestCostProfileValues,
    mergeBacktestCostProfileValues,
    normalizeBacktestCostProfile,
} from './backtestCostProfiles.js'
import { buildBackendIndicatorsPayload, normalizeIndicator } from '../../utils/chartSettings.jsx'
import {
    buildStrategySetAliasContextChartSettings,
    getStrategyTokenNameForIndicatorLine,
    getStrategyTokenGroups,
    migrateStrategyFeatureNamesToAliases,
    resolveStrategyAliasesInStrategy,
} from '../../utils/strategyAliases.jsx'
import { buildStrategyBenchmarkPayload } from '../../utils/strategyLibrary.js'
import { TIMEFRAME_OPTIONS } from '../../utils/timeframes.js'

function buildBlankStrategy() {
    return {
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

function buildBatchStrategyEntryId(index = 0) {
    return `batch-strategy-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

function buildBatchStrategyEntryLabel(strategy, index = 0) {
    const longOpen = String(strategy?.long?.openIf || '').trim()
    const shortOpen = String(strategy?.short?.openIf || '').trim()
    if (longOpen && shortOpen) {
        return `Strategy ${index + 2} · Long/Short`
    }
    if (longOpen) {
        return `Strategy ${index + 2} · Long`
    }
    if (shortOpen) {
        return `Strategy ${index + 2} · Short`
    }
    return `Strategy ${index + 2}`
}

function normalizeBatchStrategyEntries(entries = [], primaryStrategy = null) {
    const normalizedPrimary = cloneSerializable(primaryStrategy, buildBlankStrategy())
    const primarySignature = JSON.stringify(normalizedPrimary || {})
    let droppedPrimary = false
    return (Array.isArray(entries) ? entries : [])
        .map((entry, index) => ({
            id: String(entry?.id || '').trim() || buildBatchStrategyEntryId(index),
            label: String(entry?.label || '').trim() || buildBatchStrategyEntryLabel(entry?.strategy, index),
            priority: Number.isFinite(Number(entry?.priority)) ? Number(entry.priority) : index + 1,
            enabled: entry?.enabled !== false,
            symbol: String(entry?.symbol || '').trim().toUpperCase(),
            timeframe: String(entry?.timeframe || '').trim().toUpperCase(),
            allocationMode: String(entry?.allocationMode || 'fixed_volume').trim() || 'fixed_volume',
            allocationValue: entry?.allocationValue ?? null,
            strategy: cloneSerializable(entry?.strategy, null),
        }))
        .filter((entry) => entry.strategy && typeof entry.strategy === 'object')
        .filter((entry) => {
            if (droppedPrimary) {
                return true
            }
            if (JSON.stringify(entry.strategy) === primarySignature) {
                droppedPrimary = true
                return false
            }
            return true
        })
        .sort((left, right) => (
            Number(left.priority) - Number(right.priority)
            || String(left.id).localeCompare(String(right.id))
        ))
        .map((entry, index) => ({
            ...entry,
            priority: index + 1,
        }))
}

function buildPipelineStrategyEntries(job) {
    const safeJob = normalizeBatchJob(job, 0)
    return [
        {
            id: `${safeJob.id}-primary`,
            label: String(safeJob.label || 'Primary strategy').trim() || 'Primary strategy',
            priority: 0,
            enabled: true,
            allocationMode: 'fixed_volume',
            allocationValue: null,
            strategy: cloneSerializable(safeJob.strategy, buildBlankStrategy()),
        },
        ...normalizeBatchStrategyEntries(safeJob.strategies, safeJob.strategy),
    ]
}

function buildDefaultBacktest() {
    return { ...BACKTEST_DEFAULTS }
}

function formatInteger(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '0'
    }
    return numeric.toLocaleString()
}

function formatDateTime(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return '-'
    }
    return new Date(numeric * 1000).toLocaleString()
}

function formatDurationSeconds(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric < 0) {
        return '-'
    }
    const totalSeconds = Math.max(0, Math.round(numeric))
    const hours = Math.floor(totalSeconds / 3600)
    const minutes = Math.floor((totalSeconds % 3600) / 60)
    const seconds = totalSeconds % 60
    if (hours > 0) {
        return `${hours}h ${String(minutes).padStart(2, '0')}m ${String(seconds).padStart(2, '0')}s`
    }
    if (minutes > 0) {
        return `${minutes}m ${String(seconds).padStart(2, '0')}s`
    }
    return `${seconds}s`
}

function getElapsedSeconds(startedAt, finishedAt = null) {
    const start = Number(startedAt)
    if (!Number.isFinite(start) || start <= 0) {
        return null
    }
    const end = Number.isFinite(Number(finishedAt)) && Number(finishedAt) > 0
        ? Number(finishedAt)
        : (Date.now() / 1000)
    return Math.max(0, end - start)
}

function formatDecimal(value, digits = 2) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '-'
    }
    return numeric.toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: digits,
    })
}

function formatPercent(value, digits = 1) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '-'
    }
    return `${(numeric * 100).toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: digits,
    })}%`
}

function formatMetricValue(metric) {
    const { value, format } = metric || {}
    if (!Number.isFinite(Number(value))) {
        return '-'
    }
    if (format === 'integer') {
        return formatInteger(value)
    }
    if (format === 'percent') {
        return formatPercent(value)
    }
    return formatDecimal(value, 2)
}

function deriveOperationalState(entity) {
    const status = String(entity?.status || '').trim().toLowerCase()
    const phase = String(entity?.phase || '').trim().toLowerCase()
    const phaseLabel = String(entity?.phase_label || '').trim()
    const detail = String(entity?.detail || '').trim()
    const error = String(entity?.error || '').trim()
    const feedStatus = String(entity?.data_feed_status || '').trim().toLowerCase()

    if (phase === 'retrying_job') {
        return {
            tone: 'waiting',
            label: phaseLabel || 'Retrying',
            detail: detail || 'Retrying after a transient worker failure.',
        }
    }
    if (feedStatus === 'stale') {
        return {
            tone: 'issue',
            label: 'Stale',
            detail: detail || error || 'Runtime looks stale or stopped.',
        }
    }
    if (status === 'failed') {
        const interrupted = detail.toLowerCase().includes('interrupted after backend restart')
            || error.toLowerCase().includes('worker was not running in this backend process')
        return {
            tone: 'issue',
            label: interrupted ? 'Interrupted' : (phaseLabel || 'Failed'),
            detail: detail || error || 'Execution failed.',
        }
    }
    if (status === 'cancelled') {
        return {
            tone: 'waiting',
            label: phaseLabel || 'Cancelled',
            detail: detail || 'Execution was cancelled.',
        }
    }
    if (status === 'completed') {
        return {
            tone: 'healthy',
            label: phaseLabel || 'Completed',
            detail: detail || 'Execution completed successfully.',
        }
    }
    if (status === 'running' || status === 'queued') {
        return {
            tone: feedStatus === 'waiting' ? 'waiting' : 'healthy',
            label: phaseLabel || (status === 'queued' ? 'Queued' : 'Running'),
            detail: detail || 'Execution is in progress.',
        }
    }
    return {
        tone: 'idle',
        label: phaseLabel || status || 'Idle',
        detail: detail || error || 'No runtime state available.',
    }
}

function clamp01(value) {
    if (!Number.isFinite(value)) {
        return 0
    }
    return Math.max(0, Math.min(1, value))
}

function scoreHigherIsBetter(value, target) {
    const number = Number(value)
    if (!Number.isFinite(number) || target <= 0) {
        return null
    }
    return clamp01(number / target)
}

function scoreLowerIsBetter(value, target) {
    const number = Number(value)
    if (!Number.isFinite(number) || target <= 0) {
        return null
    }
    if (number <= 0) {
        return 1
    }
    return clamp01(target / number)
}

function computePipelineScore(stats) {
    if (!stats || typeof stats !== 'object') {
        return null
    }

    const criteria = [
        { weight: 0.28, score: scoreHigherIsBetter(stats.net_profit_factor, 1.75) },
        { weight: 0.24, score: scoreLowerIsBetter(Math.abs(Number(stats.max_drawdown_pct) || 0), 0.10) },
        { weight: 0.16, score: scoreHigherIsBetter(stats.sharpe_ratio, 1.50) },
        { weight: 0.12, score: scoreHigherIsBetter(stats.sortino_ratio, 2.00) },
        { weight: 0.10, score: scoreHigherIsBetter(stats.win_rate, 0.55) },
        { weight: 0.06, score: scoreHigherIsBetter(stats.risk_reward_ratio, 1.50) },
        { weight: 0.02, score: scoreHigherIsBetter(stats.recovery_factor, 2.00) },
        { weight: 0.02, score: scoreHigherIsBetter(stats.kelly_fraction, 0.20) },
    ].filter((criterion) => criterion.score !== null)

    const totalWeight = criteria.reduce((sum, criterion) => sum + criterion.weight, 0)
    if (!totalWeight) {
        return null
    }
    const weightedScore = criteria.reduce((sum, criterion) => sum + (criterion.weight * criterion.score), 0) / totalWeight
    return weightedScore * 10
}

const PIPELINE_SUMMARY_METRICS = [
    { key: 'score_out_of_ten', label: 'Score', format: 'decimal' },
    { key: 'net_pnl', label: 'Net PnL', format: 'decimal' },
    { key: 'win_rate', label: 'Win Rate', format: 'percent' },
    { key: 'n_trades', label: 'Trades', format: 'integer' },
    { key: 'expectancy_per_trade', label: 'Expectancy', format: 'decimal' },
    { key: 'max_drawdown', label: 'Max DD', format: 'decimal' },
    { key: 'max_drawdown_pct', label: 'Max DD %', format: 'percent' },
    { key: 'profit_factor', label: 'Profit Factor', format: 'decimal' },
    { key: 'gross_profit_factor', label: 'Gross PF', format: 'decimal' },
    { key: 'final_balance', label: 'Final Balance', format: 'decimal' },
    { key: 'recovery_factor', label: 'Recovery', format: 'decimal' },
]

const PIPELINE_SORT_OPTIONS = [
    { key: 'created_at', label: 'Created' },
    { key: 'label', label: 'Label' },
    { key: 'status', label: 'Status' },
    ...PIPELINE_SUMMARY_METRICS.map((metric) => ({
        key: metric.key,
        label: metric.label,
    })),
]

function extractPipelineSummaryMetrics(job) {
    const stats = job?.result?.pipeline?.stats
    if (!stats || typeof stats !== 'object') {
        return []
    }

    const derivedStats = {
        ...stats,
        score_out_of_ten: computePipelineScore(stats),
    }

    return PIPELINE_SUMMARY_METRICS
        .map((metric) => ({
            ...metric,
            value: Number(derivedStats?.[metric.key]),
        }))
        .filter((metric) => Number.isFinite(metric.value))
}

function getPipelineStat(job, key) {
    const stats = job?.result?.pipeline?.stats
    const derivedValue = key === 'score_out_of_ten'
        ? computePipelineScore(stats)
        : stats?.[key]
    const value = Number(derivedValue)
    return Number.isFinite(value) ? value : null
}

const DEFAULT_RESEARCH_STUDIES = {
    presetCompare: true,
    timeframeStudy: true,
    symbolStudy: true,
    walkforwardStudy: true,
}

function hasAnyResearchStudyEnabled(researchStudies = {}) {
    const normalizedStudies = {
        ...DEFAULT_RESEARCH_STUDIES,
        ...(researchStudies && typeof researchStudies === 'object' ? researchStudies : {}),
    }
    return Object.values(normalizedStudies).some(Boolean)
}

function deriveResearchModeFromStudies(researchStudies = {}) {
    const normalizedStudies = {
        ...DEFAULT_RESEARCH_STUDIES,
        ...(researchStudies && typeof researchStudies === 'object' ? researchStudies : {}),
    }

    if (!hasAnyResearchStudyEnabled(normalizedStudies)) {
        return 'none'
    }

    if (normalizedStudies.presetCompare && !normalizedStudies.timeframeStudy && !normalizedStudies.symbolStudy && !normalizedStudies.walkforwardStudy) {
        return 'preset_compare'
    }

    return 'full'
}

const DEFAULT_PORTFOLIO_MUTATION_OPTIONS = {
    mode: 'mutate_primary_only',
    targetStrategyId: 'primary',
    preserveAuxiliaries: true,
}

function buildDefaultPortfolioMutationOptions(overrides = {}) {
    return {
        ...DEFAULT_PORTFOLIO_MUTATION_OPTIONS,
        ...(overrides && typeof overrides === 'object' ? overrides : {}),
        mode: ['mutate_primary_only', 'mutate_selected_auxiliary'].includes(String(overrides?.mode || '').trim())
            ? String(overrides.mode).trim()
            : DEFAULT_PORTFOLIO_MUTATION_OPTIONS.mode,
        targetStrategyId: String(overrides?.targetStrategyId || DEFAULT_PORTFOLIO_MUTATION_OPTIONS.targetStrategyId).trim() || DEFAULT_PORTFOLIO_MUTATION_OPTIONS.targetStrategyId,
        preserveAuxiliaries: overrides?.preserveAuxiliaries !== false,
    }
}

function applyMutationVariantToJob(jobLike, mutationOptions, variant = {}) {
    const safeJob = normalizeBatchJob(jobLike, 0)
    const normalizedMutation = buildDefaultPortfolioMutationOptions(mutationOptions)
    const mode = normalizedMutation.mode
    const targetStrategyId = normalizedMutation.targetStrategyId

    if (mode === 'mutate_selected_auxiliary') {
        const nextStrategies = normalizeBatchStrategyEntries(safeJob.strategies, safeJob.strategy).map((entry) => (
            String(entry.id) === String(targetStrategyId)
                ? {
                    ...entry,
                    strategy: adjustStrategyForVariant(entry.strategy, variant),
                }
                : entry
        ))
        const targetStillExists = nextStrategies.some((entry) => String(entry.id) === String(targetStrategyId))
        if (targetStillExists) {
            return {
                strategy: cloneSerializable(safeJob.strategy, buildBlankStrategy()),
                strategies: nextStrategies,
                mutationMode: 'mutate_selected_auxiliary',
                mutationTargetStrategyId: targetStrategyId,
                preservedAuxiliaries: false,
            }
        }
    }

    return {
        strategy: adjustStrategyForVariant(safeJob.strategy, variant),
        strategies: normalizeBatchStrategyEntries(safeJob.strategies, safeJob.strategy),
        mutationMode: 'mutate_primary_only',
        mutationTargetStrategyId: 'primary',
        preservedAuxiliaries: true,
    }
}

function buildDefaultBatchOptions(overrides = {}) {
    const nextStudies = {
        ...DEFAULT_RESEARCH_STUDIES,
        ...(overrides?.researchStudies && typeof overrides.researchStudies === 'object' ? overrides.researchStudies : {}),
    }

    if (overrides?.researchMode === 'none') {
        nextStudies.presetCompare = false
        nextStudies.timeframeStudy = false
        nextStudies.symbolStudy = false
        nextStudies.walkforwardStudy = false
    } else if (overrides?.researchMode === 'preset_compare') {
        nextStudies.presetCompare = true
        nextStudies.timeframeStudy = false
        nextStudies.symbolStudy = false
        nextStudies.walkforwardStudy = false
    }

    const nextResearchMode = deriveResearchModeFromStudies(nextStudies)
    const nextResearchEnabled = hasAnyResearchStudyEnabled(nextStudies)

    return {
        studyWindowsCsv: '',
        studyTimeframesCsv: '',
        studySymbolsCsv: '',
        walkforwardTrainBars: '',
        walkforwardTestBars: '',
        reportQuery: '',
        reportStatusFilter: 'all',
        reportSymbolFilter: 'all',
        reportTimeframeFilter: 'all',
        reportSortKey: 'created_at',
        reportSortDirection: 'desc',
        ...overrides,
        activeTemplateId: String(overrides?.activeTemplateId || '').trim(),
        comparisonPresetSelectionMap: overrides?.comparisonPresetSelectionMap && typeof overrides.comparisonPresetSelectionMap === 'object'
            ? overrides.comparisonPresetSelectionMap
            : {},
        portfolioMutation: buildDefaultPortfolioMutationOptions(overrides?.portfolioMutation),
        researchEnabled: nextResearchEnabled,
        researchMode: nextResearchMode,
        researchStudies: nextStudies,
    }
}

function buildDefaultRuntimeViewOptions() {
    return {
        reportQuery: '',
        reportStatusFilter: 'all',
        reportSymbolFilter: 'all',
        reportTimeframeFilter: 'all',
        reportSortKey: 'created_at',
        reportSortDirection: 'desc',
    }
}

function normalizeBatchJob(entry, index = 0) {
    const safe = entry && typeof entry === 'object' ? entry : {}
    const chart = safe.chart && typeof safe.chart === 'object' ? safe.chart : {}
    const strategy = safe.strategy && typeof safe.strategy === 'object' ? safe.strategy : buildBlankStrategy()
    const backtest = {
        ...buildDefaultBacktest(),
        ...mergeBacktestCostProfileValues(safe.backtest && typeof safe.backtest === 'object' ? safe.backtest : {}),
    }
    const researchPlan = safe.researchPlan && typeof safe.researchPlan === 'object' ? safe.researchPlan : {}
    const symbol = String(chart.symbol || '').trim().toUpperCase()
    const timeframe = String(chart.timeframe || '').trim().toUpperCase()
    const bars = Math.max(1, Number(chart.bars) || Number(backtest.historyScopeBars) || 1000)

    return {
        id: String(safe.id || `batch-job-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`),
        label: String(safe.label || `${symbol || 'SYMBOL'} ${timeframe || 'TF'} · Job ${index + 1}`).trim(),
        notes: String(safe.notes || '').trim(),
        chart: {
            symbol,
            timeframe,
            bars,
            indicators: Array.isArray(chart.indicators) ? chart.indicators : [],
        },
        strategy,
        strategies: normalizeBatchStrategyEntries(safe.strategies, strategy),
        backtest,
        researchPlan,
    }
}

function buildBatchJobChartSettings(jobLike, sharedIndicators = []) {
    const safeJob = normalizeBatchJob(jobLike, 0)
    return buildStrategySetAliasContextChartSettings({
        symbol: safeJob.chart?.symbol || '',
        timeframe: safeJob.chart?.timeframe || '',
        bars: Math.max(1, Number(safeJob.chart?.bars || 1000) || 1000),
        indicators: mergeBatchIndicators(sharedIndicators, safeJob.chart?.indicators || []),
    }, safeJob.strategy || {}, normalizeBatchStrategyEntries(safeJob.strategies, safeJob.strategy))
}

function buildSafeResearchPayloadFromProfile(job, options, allJobs = [], sharedIndicators = []) {
    try {
        return {
            payload: buildResearchPayloadFromProfile(job, options, allJobs, sharedIndicators),
            error: '',
        }
    } catch (error) {
        return {
            payload: {
                kind: 'none',
                warning: error?.message || 'Could not build the batch research payload.',
            },
            error: error?.message || 'Could not build the batch research payload.',
        }
    }
}

function buildSafeBatchRequestJobs(jobs, options, sharedIndicators = []) {
    try {
        return {
            jobs: buildBatchRequestJobs(jobs, options, sharedIndicators),
            error: '',
        }
    } catch (error) {
        return {
            jobs: [],
            error: error?.message || 'Could not build the batch request payload.',
        }
    }
}

function parseImportedJobs(rawText) {
    const parsed = JSON.parse(String(rawText || '').trim())
    if (Array.isArray(parsed)) {
        return parsed.map((entry, index) => normalizeBatchJob(entry, index))
    }
    if (parsed && Array.isArray(parsed.jobs)) {
        return parsed.jobs.map((entry, index) => normalizeBatchJob(entry, index))
    }
    if (parsed && typeof parsed === 'object') {
        return [normalizeBatchJob(parsed, 0)]
    }
    throw new Error('Imported JSON must be an object, a jobs wrapper, or an array of objects.')
}

function parseImportedBatchPayload(rawText) {
    const parsed = JSON.parse(String(rawText || '').trim())
    const jobs = parseImportedJobs(rawText)
    const sharedFeatures = parsed && typeof parsed === 'object' && Array.isArray(parsed.shared_features)
        ? parsed.shared_features.map((indicator) => normalizeIndicator(indicator))
        : []

    return {
        jobs,
        sharedFeatures,
    }
}

function mergeBatchIndicators(sharedIndicators = [], localIndicators = []) {
    const merged = []
    const seen = new Set()

    for (const indicator of [...(sharedIndicators || []), ...(localIndicators || [])]) {
        const normalized = normalizeIndicator(indicator)
        const signature = JSON.stringify({
            name: normalized?.name || '',
            alias: normalized?.alias || '',
            params: normalized?.params || [],
        })

        if (seen.has(signature)) {
            continue
        }

        seen.add(signature)
        merged.push(normalized)
    }

    return merged
}

function collectStrategyExpressions(strategy) {
    const safe = strategy && typeof strategy === 'object' ? strategy : {}
    const expressions = []

    for (const section of Object.values(safe)) {
        if (!section || typeof section !== 'object') {
            continue
        }
        for (const value of Object.values(section)) {
            if (typeof value === 'string' && value.trim()) {
                expressions.push(value)
            }
        }
    }

    return expressions
}

function countUsedIndicatorsForStrategy(strategy, chartSettings) {
    const expressions = collectStrategyExpressions(strategy)
    if (!expressions.length) {
        return 0
    }

    const combined = expressions.join('\n')
    const indicators = Array.isArray(chartSettings?.indicators) ? chartSettings.indicators : []

    return indicators.reduce((count, indicator) => {
        const lines = Array.isArray(indicator?.lines) ? indicator.lines : []
        const tokens = new Set()

        for (const line of lines) {
            const tokenName = getStrategyTokenNameForIndicatorLine(indicator, line)
            const columnName = String(line?.columnName || '').trim()
            if (tokenName) {
                tokens.add(tokenName)
            }
            if (columnName) {
                tokens.add(columnName)
            }
        }

        const isUsed = [...tokens].some((token) => token && new RegExp(`\\b${token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(combined))
        return count + (isUsed ? 1 : 0)
    }, 0)
}

function inferSharedFeaturesFromRequestJobs(requestJobs = []) {
    return mergeBatchIndicators(
        [],
        requestJobs.flatMap((entry) => entry?.request?.chart?.indicators || []),
    )
}

function extractCampaignBatchEditorPayload(campaign) {
    const rawRequestJobs = Array.isArray(campaign?.request?.jobs) ? campaign.request.jobs : []
    const persistedBatchJobs = Array.isArray(campaign?.request?.batch_jobs) ? campaign.request.batch_jobs : []
    const campaignJobs = persistedBatchJobs.length > 0 ? persistedBatchJobs : rawRequestJobs
    const inferredSharedFeatures = Array.isArray(campaign?.request?.shared_features)
        ? campaign.request.shared_features.map((indicator) => normalizeIndicator(indicator))
        : inferSharedFeaturesFromRequestJobs(rawRequestJobs)
    const importedJobs = campaignJobs
        .map((entry, index) => {
            const sourceJob = entry?.request && typeof entry.request === 'object'
                ? entry.request
                : (entry && typeof entry === 'object' ? entry : {})
            const strategyChartSettings = buildBatchJobChartSettings({
                chart: sourceJob?.chart || {},
                strategy: sourceJob?.strategy || {},
                strategies: Array.isArray(sourceJob?.strategies) ? sourceJob.strategies : [],
            }, inferredSharedFeatures)
            return normalizeBatchJob({
                id: sourceJob?.id || `campaign-job-${campaign?.id || 'template'}-${index + 1}`,
                label: entry?.run_label || sourceJob?.label || `Job ${index + 1}`,
                notes: entry?.run_notes || sourceJob?.notes || '',
                chart: sourceJob?.chart || {},
                strategy: migrateStrategyFeatureNamesToAliases(sourceJob?.strategy || {}, strategyChartSettings) || {},
                strategies: normalizeBatchStrategyEntries(
                    Array.isArray(sourceJob?.strategies) ? sourceJob.strategies.map((strategyEntry) => ({
                        ...strategyEntry,
                        strategy: migrateStrategyFeatureNamesToAliases(strategyEntry?.strategy || {}, strategyChartSettings) || {},
                    })) : [],
                    sourceJob?.strategy || {},
                ),
                backtest: sourceJob?.backtest || {},
                researchPlan: sourceJob?.researchPlan || {},
            }, index)
        })

    return {
        jobs: importedJobs,
        options: buildDefaultBatchOptions({
            ...(campaign?.request?.options || {}),
            activeTemplateId: String(campaign?.id || '').trim(),
        }),
        sharedFeatures: inferredSharedFeatures,
    }
}

function buildEditorStateFromCampaign(campaign) {
    const extracted = extractCampaignBatchEditorPayload(campaign)
    return {
        jobs: extracted.jobs,
        features: extracted.sharedFeatures,
        options: extracted.options,
    }
}

function buildExecutableJobsFromCampaign(campaign) {
    const extracted = extractCampaignBatchEditorPayload(campaign)
    return buildBatchRequestJobs(
        extracted.jobs,
        extracted.options,
        extracted.sharedFeatures,
    )
}

function describeResearchSelection(options = {}) {
    const normalized = buildDefaultBatchOptions(options)
    const studies = normalized.researchStudies || DEFAULT_RESEARCH_STUDIES
    const enabled = []

    if (studies.presetCompare) enabled.push('preset compare')
    if (studies.timeframeStudy) enabled.push('timeframe study')
    if (studies.symbolStudy) enabled.push('symbol study')
    if (studies.walkforwardStudy) enabled.push('walk-forward')

    if (!enabled.length) {
        return 'backtest only'
    }

    return enabled.join(', ')
}

function buildJobDraft(job = null) {
    const normalized = normalizeBatchJob(job, 0)
    return {
        label: String(normalized?.label || '').trim(),
        symbol: String(normalized?.chart?.symbol || '').trim().toUpperCase(),
        timeframe: String(normalized?.chart?.timeframe || '').trim().toUpperCase(),
        bars: Math.max(1, Number(normalized?.chart?.bars || 1000) || 1000),
        notes: String(normalized?.notes || '').trim(),
        indicators: Array.isArray(normalized?.chart?.indicators) ? normalized.chart.indicators : [],
        strategy: normalized?.strategy || buildBlankStrategy(),
        strategies: Array.isArray(normalized?.strategies) ? normalized.strategies : [],
        backtest: normalized?.backtest || buildDefaultBacktest(),
        researchPlan: normalized?.researchPlan || {},
    }
}

function buildJobFromDraft(draft, baseJob = null, index = 0) {
    const safeDraft = draft && typeof draft === 'object' ? draft : {}
    const base = baseJob && typeof baseJob === 'object' ? baseJob : {}
    return normalizeBatchJob({
        ...base,
        label: String(safeDraft.label || base?.label || '').trim(),
        notes: String(safeDraft.notes || base?.notes || '').trim(),
        chart: {
            ...(base?.chart && typeof base.chart === 'object' ? base.chart : {}),
            symbol: String(safeDraft.symbol || base?.chart?.symbol || '').trim().toUpperCase(),
            timeframe: String(safeDraft.timeframe || base?.chart?.timeframe || '').trim().toUpperCase(),
            bars: Math.max(1, Number(safeDraft.bars || base?.chart?.bars || 1000) || 1000),
            indicators: Array.isArray(safeDraft.indicators)
                ? safeDraft.indicators
                : Array.isArray(base?.chart?.indicators) ? base.chart.indicators : [],
        },
        strategy: safeDraft.strategy && typeof safeDraft.strategy === 'object'
            ? safeDraft.strategy
            : base?.strategy,
        strategies: Array.isArray(safeDraft.strategies)
            ? safeDraft.strategies
            : base?.strategies,
        backtest: safeDraft.backtest && typeof safeDraft.backtest === 'object'
            ? safeDraft.backtest
            : base?.backtest,
        researchPlan: safeDraft.researchPlan && typeof safeDraft.researchPlan === 'object'
            ? safeDraft.researchPlan
            : base?.researchPlan,
    }, index)
}

function splitCsvValues(value) {
    return String(value || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
}

function getComparisonPresetSelectionMap(options) {
    return options?.comparisonPresetSelectionMap && typeof options.comparisonPresetSelectionMap === 'object'
        ? options.comparisonPresetSelectionMap
        : {}
}

function buildComparisonPresets(job, allJobs = [], options = null) {
    const safeJob = normalizeBatchJob(job, 0)
    const embeddedPayload = safeJob.researchPlan?.payload && typeof safeJob.researchPlan.payload === 'object'
        ? safeJob.researchPlan.payload
        : {}
    const embeddedPresets = Array.isArray(embeddedPayload?.presets) ? embeddedPayload.presets.filter((entry) => entry && typeof entry === 'object') : []
    const selectionMap = getComparisonPresetSelectionMap(options)
    const explicitSelectedIds = Array.isArray(selectionMap?.[safeJob.id])
        ? Array.from(new Set(selectionMap[safeJob.id].map((value) => String(value || '').trim()).filter(Boolean)))
        : []
    const normalizedOtherJobs = (Array.isArray(allJobs) ? allJobs : [])
        .filter((entry) => String(entry?.id || '') !== String(safeJob.id))
        .map((entry, index) => normalizeBatchJob(entry, index))
        .filter((entry) => entry?.strategy && typeof entry.strategy === 'object')

    if (explicitSelectedIds.length) {
        const selectedPresets = normalizedOtherJobs
            .filter((entry) => explicitSelectedIds.includes(String(entry.id)))
            .map((entry, index) => ({
                id: String(entry.id || `selected-preset-${index + 1}`),
                label: String(entry.label || `Preset ${index + 1}`).trim(),
                strategy: entry.strategy,
                strategies: normalizeBatchStrategyEntries(entry.strategies, entry.strategy),
            }))

        if (selectedPresets.length) {
            return {
                source: 'selected_jobs',
                presets: selectedPresets,
            }
        }
    }

    if (embeddedPresets.length) {
        return {
            source: 'embedded',
            presets: embeddedPresets,
        }
    }

    const batchPresets = normalizedOtherJobs
        .map((entry, index) => ({
            id: String(entry.id || `batch-preset-${index + 1}`),
            label: String(entry.label || `Preset ${index + 1}`).trim(),
            strategy: entry.strategy,
            strategies: normalizeBatchStrategyEntries(entry.strategies, entry.strategy),
        }))

    if (batchPresets.length) {
        return {
            source: 'batch_jobs',
            presets: batchPresets,
        }
    }

    return {
        source: 'none',
        presets: [],
    }
}

function resolveBatchStrategyEntriesForResearch(entries = [], chartSettings = {}) {
    return normalizeBatchStrategyEntries(entries)
        .map((entry) => ({
            id: String(entry.id || '').trim(),
            label: String(entry.label || '').trim(),
            priority: Number.isFinite(Number(entry.priority)) ? Number(entry.priority) : 0,
            enabled: entry.enabled !== false,
            symbol: String(entry.symbol || '').trim().toUpperCase(),
            timeframe: String(entry.timeframe || '').trim().toUpperCase(),
            allocationMode: String(entry.allocationMode || 'fixed_volume').trim() || 'fixed_volume',
            allocationValue: entry.allocationValue ?? null,
            strategy: resolveStrategyAliasesInStrategy(entry.strategy || {}, chartSettings),
        }))
}

function buildResearchPayloadFromProfile(job, options, allJobs = [], sharedIndicators = []) {
    const safeJob = normalizeBatchJob(job, 0)
    const strategyChartSettings = buildBatchJobChartSettings(safeJob, sharedIndicators)
    const embeddedPayload = safeJob.researchPlan?.payload && typeof safeJob.researchPlan.payload === 'object'
        ? safeJob.researchPlan.payload
        : {}
    const embeddedBaseline = embeddedPayload?.baseline && typeof embeddedPayload.baseline === 'object'
        ? embeddedPayload.baseline
        : {}
    const comparisonPresets = buildComparisonPresets(safeJob, allJobs, options)
    const presets = comparisonPresets.presets.map((preset, index) => ({
        ...preset,
        id: String(preset?.id || `preset-${index + 1}`),
        label: String(preset?.label || `Preset ${index + 1}`).trim(),
        strategy: resolveStrategyAliasesInStrategy(preset?.strategy || {}, strategyChartSettings),
        strategies: resolveBatchStrategyEntriesForResearch(preset?.strategies, strategyChartSettings),
    }))
    const selectedStudies = {
        ...DEFAULT_RESEARCH_STUDIES,
        ...(options?.researchStudies && typeof options.researchStudies === 'object' ? options.researchStudies : {}),
    }
    const shouldRunPresetCompare = Boolean(selectedStudies.presetCompare || selectedStudies.timeframeStudy || selectedStudies.symbolStudy || selectedStudies.walkforwardStudy)

    if (!shouldRunPresetCompare) {
        return { kind: 'none' }
    }

    if (!presets.length) {
        return {
            kind: 'none',
            warning: 'Selected research profile needs at least one comparison preset. Import another job into the batch or include presets in the job JSON.',
        }
    }

    const chartBars = Math.max(1, Number(safeJob.chart?.bars || 1000) || 1000)
    const timeframe = String(safeJob.chart?.timeframe || '').trim().toUpperCase()
    const symbol = String(safeJob.chart?.symbol || '').trim().toUpperCase()
    const studyWindows = splitCsvValues(options?.studyWindowsCsv).map((value) => Math.max(100, Number(value) || 0)).filter((value) => Number.isFinite(value) && value > 0)
    const studyTimeframes = splitCsvValues(options?.studyTimeframesCsv).map((value) => value.toUpperCase())
    const studySymbols = splitCsvValues(options?.studySymbolsCsv).map((value) => value.toUpperCase())
    const walkforwardTestBars = Math.max(100, Number(options?.walkforwardTestBars) || Math.round(chartBars * 0.2))
    const walkforwardTrainBars = Math.max(walkforwardTestBars, Number(options?.walkforwardTrainBars) || Math.round(walkforwardTestBars * 2))

    const basePayload = {
        ...embeddedPayload,
        baseline: {
            ...embeddedBaseline,
            id: String(embeddedBaseline?.id || safeJob.id || '').trim(),
            label: String(embeddedBaseline?.label || safeJob.label || '').trim(),
            strategy: resolveStrategyAliasesInStrategy(
                embeddedBaseline?.strategy || safeJob.strategy || {},
                strategyChartSettings,
            ),
            strategies: resolveBatchStrategyEntriesForResearch(
                embeddedBaseline?.strategies || safeJob.strategies,
                strategyChartSettings,
            ),
        },
        presets,
        backtest: safeJob.backtest,
        chartContext: {
            symbol,
            timeframe,
            bars: chartBars,
            indicators: buildBackendIndicatorsPayload(strategyChartSettings.indicators || []),
        },
        comparisonPresetSource: comparisonPresets.source,
    }

    if (!selectedStudies.timeframeStudy && !selectedStudies.symbolStudy && !selectedStudies.walkforwardStudy) {
        return {
            kind: 'preset_compare',
            payload: {
                ...basePayload,
            },
        }
    }

    return {
        kind: 'preset_compare',
        payload: {
            ...basePayload,
            ...(selectedStudies.timeframeStudy || selectedStudies.symbolStudy ? {
                studyWindows: studyWindows.length ? studyWindows : [Math.max(200, Math.round(chartBars * 0.5)), chartBars],
            } : {}),
            ...(selectedStudies.timeframeStudy ? {
                studyTimeframes: studyTimeframes.length ? studyTimeframes : (timeframe ? [timeframe, 'M5', 'M15'] : ['M1', 'M5', 'M15']),
            } : {}),
            ...(selectedStudies.symbolStudy ? {
                studySymbols: studySymbols.length ? studySymbols : (symbol ? [symbol, 'GBPUSD', 'USDJPY'] : ['EURUSD', 'GBPUSD', 'USDJPY']),
            } : {}),
            ...(selectedStudies.walkforwardStudy ? {
                walkforwardWindowBars: walkforwardTestBars,
                walkforwardStepBars: walkforwardTestBars,
                walkforwardTrainBars,
                walkforwardTestBars,
            } : {}),
        },
    }
}

function buildResearchMutationPreview(researchPlan = {}) {
    const mutation = researchPlan?.mutation && typeof researchPlan.mutation === 'object'
        ? researchPlan.mutation
        : null
    if (!mutation) {
        return null
    }

    const mode = String(mutation.mutationMode || 'manual').trim() || 'manual'
    const label = String(mutation.mutationLabel || '').trim()
    const target = String(mutation.mutationTargetStrategyId || '').trim()
    const preservedAuxiliaries = mutation.preservedAuxiliaries === true
    const targetLabel = target && target !== 'primary' ? ` · target ${target}` : ''
    return {
        mode,
        label,
        target,
        preservedAuxiliaries,
        summary: preservedAuxiliaries
            ? `${mode}${targetLabel} · preserved auxiliaries${label ? ` · ${label}` : ''}`
            : `${mode}${targetLabel}${label ? ` · ${label}` : ''}`,
    }
}

function buildPortfolioLineagePreview(researchPlan = {}) {
    const mutation = researchPlan?.mutation && typeof researchPlan.mutation === 'object'
        ? researchPlan.mutation
        : null
    if (!mutation) {
        return null
    }

    const parentBatchId = mutation.parentBatchId ?? null
    const parentJobId = mutation.parentJobId ?? null
    const target = String(mutation.mutationTargetStrategyId || '').trim()
    const fragments = []

    if (parentBatchId !== null && parentBatchId !== undefined && String(parentBatchId).trim()) {
        fragments.push(`Parent batch #${String(parentBatchId).trim()}`)
    }
    if (parentJobId !== null && parentJobId !== undefined && String(parentJobId).trim()) {
        fragments.push(`Parent job #${String(parentJobId).trim()}`)
    }
    if (target && target !== 'primary') {
        fragments.push(`Target ${target}`)
    }

    if (!fragments.length) {
        return null
    }

    return {
        summary: fragments.join(' · '),
    }
}

function formatPortfolioMutationModeLabel(mode) {
    const normalizedMode = String(mode || '').trim()
    if (normalizedMode === 'mutate_selected_auxiliary') {
        return 'Mutate selected auxiliary'
    }
    return 'Mutate primary only'
}

function describePortfolioMutationMode(mode) {
    const normalizedMode = String(mode || '').trim()
    if (normalizedMode === 'mutate_selected_auxiliary') {
        return 'Follow-up variants will keep the primary strategy untouched and mutate only one chosen auxiliary.'
    }
    return 'Follow-up variants will mutate only the primary strategy and preserve auxiliary strategies as anchors.'
}

function hashPortfolioSignature(signature) {
    let hash = 0
    for (let index = 0; index < signature.length; index += 1) {
        hash = ((hash << 5) - hash + signature.charCodeAt(index)) | 0
    }
    return Math.abs(hash).toString(16).padStart(8, '0').slice(0, 8)
}

function buildPortfolioSignaturePreview(jobLike) {
    const safeJob = normalizeBatchJob(jobLike, 0)
    const strategies = normalizeBatchStrategyEntries(safeJob.strategies, safeJob.strategy)
    const enabledStrategies = strategies.filter((entry) => entry.enabled !== false)
    const signature = buildPortfolioJobSignature(safeJob)
    const signatureId = hashPortfolioSignature(signature)

    return {
        id: signatureId,
        strategyCount: strategies.length,
        enabledCount: enabledStrategies.length,
        summary: `${strategies.length} strategies · ${enabledStrategies.length} enabled · sig ${signatureId}`,
    }
}

function buildPipelineRequest(job, options, allJobs = [], sharedIndicators = []) {
    const safeJob = normalizeBatchJob(job, 0)
    const strategyChartSettings = buildBatchJobChartSettings(safeJob, sharedIndicators)
    const nextBars = Math.max(1, Number(strategyChartSettings?.bars || safeJob.chart.bars || 1000) || 1000)
    const chartContext = {
        ...safeJob.chart,
        bars: nextBars,
        indicators: buildBackendIndicatorsPayload(strategyChartSettings.indicators || []),
    }
    const nextResearchPlan = buildResearchPayloadFromProfile(safeJob, options, allJobs, sharedIndicators)
    const resolvedStrategy = resolveStrategyAliasesInStrategy(safeJob.strategy, strategyChartSettings)
    const resolvedStrategies = buildPipelineStrategyEntries(safeJob).map((entry, index) => ({
        id: entry.id,
        label: String(entry.label || buildBatchStrategyEntryLabel(entry.strategy, index - 1)).trim() || `Strategy ${index + 1}`,
        priority: index,
        enabled: entry.enabled !== false,
        symbol: String(entry.symbol || '').trim().toUpperCase(),
        timeframe: String(entry.timeframe || '').trim().toUpperCase(),
        allocationMode: entry.allocationMode || 'fixed_volume',
        allocationValue: entry.allocationValue ?? null,
        strategy: resolveStrategyAliasesInStrategy(entry.strategy, strategyChartSettings),
    }))

    return {
        id: safeJob.id,
        label: safeJob.label,
        notes: safeJob.notes,
        chart: chartContext,
        strategy: resolvedStrategy,
        strategies: resolvedStrategies,
        backtest: safeJob.backtest,
        researchPlan: nextResearchPlan,
    }
}

function isExecutableBatchJob(job) {
    const safeJob = normalizeBatchJob(job, 0)
    const hasChartIdentity = Boolean(safeJob.chart.symbol && safeJob.chart.timeframe)
    const expressions = [
        ...collectStrategyExpressions(safeJob.strategy || {}),
        ...safeJob.strategies.flatMap((entry) => collectStrategyExpressions(entry?.strategy || {})),
    ]
    const hasStrategyLogic = expressions.some((value) => {
        const normalized = String(value || '').trim()
        return normalized && normalized.toLowerCase() !== 'false'
    })

    return hasChartIdentity && hasStrategyLogic
}

function buildBatchRequestJobs(jobs, options, sharedIndicators = []) {
    return (Array.isArray(jobs) ? jobs : [])
        .filter((job) => isExecutableBatchJob(job))
        .map((job) => ({
        job_type: 'strategy_pipeline',
        request: buildPipelineRequest(job, options, jobs, sharedIndicators),
        run_label: job?.label || '',
        run_notes: job?.notes || '',
    }))
    
}

function buildHydratedBacktestPayloadFromPipelineJob(job) {
    if (job?.result_loaded === false) {
        return null
    }

    const pipeline = job?.result?.pipeline
    const request = pipeline?.request || {}
    const chart = pipeline?.chart || {}
    if (!request?.strategy || !request?.backtest) {
        return null
    }

    const strategyResponse = {
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
    }

    return {
        chartSettings: {
            symbol: strategyResponse.runtime.market.symbol,
            timeframe: strategyResponse.runtime.market.timeframe,
            bars: strategyResponse.runtime.market.bars,
            indicators: Array.isArray(chart?.indicators) ? chart.indicators : [],
        },
        strategy: request.strategy,
        strategies: normalizeBatchStrategyEntries(request.strategies, request.strategy),
        backtest: request.backtest,
        strategyResponse,
    }
}

function buildStrategyLibraryPayloadFromPipelineJob(job) {
    const pipeline = job?.result?.pipeline
    const directRequest = job?.request && typeof job.request === 'object' ? job.request : {}
    const request = pipeline?.request && typeof pipeline.request === 'object'
        ? pipeline.request
        : directRequest
    const chart = pipeline?.chart && typeof pipeline.chart === 'object'
        ? pipeline.chart
        : (directRequest?.chart || {})
    if (!request?.strategy) {
        return null
    }

    const auxiliaryStrategies = normalizeBatchStrategyEntries(request?.strategies, request?.strategy)
    const strategyChartSettings = buildStrategySetAliasContextChartSettings({
        symbol: String(chart?.symbol || '').trim().toUpperCase(),
        timeframe: String(chart?.timeframe || '').trim().toUpperCase(),
        bars: Math.max(1, Number(chart?.bars || 1) || 1),
        indicators: Array.isArray(chart?.indicators) ? chart.indicators : [],
    }, request.strategy, auxiliaryStrategies)
    const primaryStrategy = migrateStrategyFeatureNamesToAliases(request.strategy || {}, strategyChartSettings)
    const strategies = auxiliaryStrategies.map((entry) => ({
        ...entry,
        strategy: migrateStrategyFeatureNamesToAliases(entry?.strategy || {}, strategyChartSettings),
    }))
    const label = String(
        job?.run_label
        || request?.label
        || `${String(chart?.symbol || 'Strategy').trim().toUpperCase()} ${String(chart?.timeframe || '').trim().toUpperCase()}`
    ).trim() || 'Batch strategy'
    const savedFrom = `Saved from Batch dashboard · ${String(chart?.symbol || '--').trim().toUpperCase()} · ${String(chart?.timeframe || '--').trim().toUpperCase()}`
    const notes = [String(job?.run_notes || request?.notes || '').trim(), savedFrom].filter(Boolean).join('\n\n')
    return buildStrategyBenchmarkPayload({
        label,
        notes,
        side: 'both',
        source: 'batch-dashboard',
        strategy: primaryStrategy,
        strategies,
        chartSettings: strategyChartSettings,
    })
}

function mergeRemoteBatchRuntimeEntries(currentEntries = [], incomingEntry, { maxEntries = 100 } = {}) {
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

    return [
        merged,
        ...safeEntries.filter((entry) => String(entry?.id || '').trim() !== incomingId),
    ]
        .sort((left, right) => Number(right?.created_at || right?.updated_at || right?.id || 0) - Number(left?.created_at || left?.updated_at || left?.id || 0))
        .slice(0, maxEntries)
}

function reconcileRemoteBatchRuntimeEntries(currentEntries = [], incomingEntries = [], { maxEntries = 100 } = {}) {
    const safeIncomingEntries = Array.isArray(incomingEntries) ? incomingEntries : []
    const incomingIds = new Set(safeIncomingEntries.map((entry) => String(entry?.id || '').trim()).filter(Boolean))
    let mergedEntries = Array.isArray(currentEntries) ? currentEntries : []

    for (const incomingEntry of safeIncomingEntries) {
        mergedEntries = mergeRemoteBatchRuntimeEntries(mergedEntries, incomingEntry, { maxEntries: Number.MAX_SAFE_INTEGER })
    }

    return mergedEntries
        .filter((entry) => incomingIds.has(String(entry?.id || '').trim()))
        .slice(0, maxEntries)
}

function moveArrayItem(items, fromIndex, toIndex) {
    const safeItems = Array.isArray(items) ? [...items] : []
    if (fromIndex < 0 || toIndex < 0 || fromIndex >= safeItems.length || toIndex >= safeItems.length || fromIndex === toIndex) {
        return safeItems
    }
    const [moved] = safeItems.splice(fromIndex, 1)
    safeItems.splice(toIndex, 0, moved)
    return safeItems
}

function replacePatternNumber(expression, pattern, updater) {
    const source = String(expression || '')
    let replaced = false
    return source.replace(pattern, (...args) => {
        if (replaced) {
            return args[0]
        }
        replaced = true
        const value = Number(args[1])
        if (!Number.isFinite(value)) {
            return args[0]
        }
        const nextValue = updater(value)
        return args[0].replace(args[1], String(nextValue))
    })
}

function adjustStrategyForVariant(strategy, variant = {}) {
    const safe = strategy && typeof strategy === 'object' ? strategy : buildBlankStrategy()
    const next = {
        ...safe,
        long: { ...(safe.long || {}) },
        short: { ...(safe.short || {}) },
        other: { ...(safe.other || {}) },
    }

    const longOpenDelta = Number(variant.longOpenDelta || 0)
    const shortOpenDelta = Number(variant.shortOpenDelta || 0)
    const longCloseDelta = Number(variant.longCloseDelta || 0)
    const shortCloseDelta = Number(variant.shortCloseDelta || 0)
    const gainDelta = Number(variant.gainDelta || 0)
    const lossDelta = Number(variant.lossDelta || 0)
    const trailingDelta = Number(variant.trailingDelta || 0)

    next.long.openIf = replacePatternNumber(next.long.openIf, /(RSI_close_14\[1\]\s*<=\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(5, value + longOpenDelta))
    next.long.openIf = replacePatternNumber(next.long.openIf, /(RSI_close_14\[0\]\s*>\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(5, value + longOpenDelta))
    next.short.openIf = replacePatternNumber(next.short.openIf, /(RSI_close_14\[1\]\s*>=\s*)(\d+(?:\.\d+)?)/, (value) => Math.min(95, value + shortOpenDelta))
    next.short.openIf = replacePatternNumber(next.short.openIf, /(RSI_close_14\[0\]\s*<\s*)(\d+(?:\.\d+)?)/, (value) => Math.min(95, value + shortOpenDelta))
    next.long.closeIf = replacePatternNumber(next.long.closeIf, /(RSI_close_14\[0\]\s*>=\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(5, value + longCloseDelta))
    next.short.closeIf = replacePatternNumber(next.short.closeIf, /(RSI_close_14\[0\]\s*<=\s*)(\d+(?:\.\d+)?)/, (value) => Math.min(95, value + shortCloseDelta))

    next.long.gainPrice = replacePatternNumber(next.long.gainPrice, /(\*\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(1, value + gainDelta))
    next.short.gainPrice = replacePatternNumber(next.short.gainPrice, /(\*\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(1, value + gainDelta))
    next.long.lossPrice = replacePatternNumber(next.long.lossPrice, /(\*\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(1, value + lossDelta))
    next.short.lossPrice = replacePatternNumber(next.short.lossPrice, /(\*\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(1, value + lossDelta))

    if (variant.trailingMode === 'enable' && !String(next.long.trailingPrice || '').trim() && !String(next.short.trailingPrice || '').trim()) {
        next.long.trailingPrice = 'long_open_price[0] + 0.0001 * 3'
        next.short.trailingPrice = 'short_open_price[0] - 0.0001 * 3'
    } else if (trailingDelta) {
        next.long.trailingPrice = replacePatternNumber(next.long.trailingPrice, /(\*\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(1, value + trailingDelta))
        next.short.trailingPrice = replacePatternNumber(next.short.trailingPrice, /(\*\s*)(\d+(?:\.\d+)?)/, (value) => Math.max(1, value + trailingDelta))
    }

    return next
}

function freezePortfolioSignatureValue(value) {
    if (Array.isArray(value)) {
        return value.map((item) => freezePortfolioSignatureValue(item))
    }
    if (value && typeof value === 'object') {
        return Object.keys(value)
            .sort()
            .reduce((accumulator, key) => {
                accumulator[key] = freezePortfolioSignatureValue(value[key])
                return accumulator
            }, {})
    }
    return value
}

function buildPortfolioJobSignature(jobLike) {
    const safeJob = normalizeBatchJob(jobLike, 0)
    return JSON.stringify(freezePortfolioSignatureValue({
        strategy: safeJob.strategy || {},
        strategies: normalizeBatchStrategyEntries(safeJob.strategies, safeJob.strategy).map((entry) => ({
            priority: Number(entry?.priority || 0),
            enabled: entry?.enabled !== false,
            symbol: String(entry?.symbol || '').trim().toUpperCase(),
            timeframe: String(entry?.timeframe || '').trim().toUpperCase(),
            allocationMode: String(entry?.allocationMode || 'fixed_volume'),
            allocationValue: entry?.allocationValue ?? null,
            strategy: entry?.strategy || {},
        })),
    }))
}

function extractRerunCandidateJob(remoteJob, index = 0) {
    const request = remoteJob?.request && typeof remoteJob.request === 'object'
        ? remoteJob.request
        : {}

    return {
        sourceJob: remoteJob,
        normalizedJob: normalizeBatchJob({
            id: String(request?.id || remoteJob?.id || `rerun-candidate-${index + 1}`),
            label: String(request?.label || remoteJob?.run_label || `Rerun candidate ${index + 1}`).trim(),
            notes: String(request?.notes || remoteJob?.run_notes || '').trim(),
            chart: request?.chart || {},
            strategy: request?.strategy || {},
            strategies: Array.isArray(request?.strategies) ? request.strategies : [],
            backtest: request?.backtest || buildDefaultBacktest(),
            researchPlan: request?.researchPlan || {},
        }, index),
    }
}

function resolveMutationParentJob(jobLike, remoteJobs = [], localJobs = []) {
    const safeJob = normalizeBatchJob(jobLike, 0)
    const mutation = safeJob?.researchPlan?.mutation && typeof safeJob.researchPlan.mutation === 'object'
        ? safeJob.researchPlan.mutation
        : null
    if (!mutation) {
        return null
    }

    const parentJobId = String(mutation.parentJobId || '').trim()
    if (!parentJobId) {
        return null
    }

    const localMatch = (Array.isArray(localJobs) ? localJobs : []).find((entry) => String(entry?.id || '').trim() === parentJobId)
    if (localMatch) {
        return normalizeBatchJob(localMatch, 0)
    }

    const remoteMatch = (Array.isArray(remoteJobs) ? remoteJobs : []).find((entry) => (
        String(entry?.id || '').trim() === parentJobId
        || String(entry?.request?.id || '').trim() === parentJobId
    ))
    if (!remoteMatch) {
        return null
    }

    return extractRerunCandidateJob(remoteMatch, 0).normalizedJob
}

function getMutationTargetStrategy(jobLike, targetStrategyId = 'primary') {
    const safeJob = normalizeBatchJob(jobLike, 0)
    if (String(targetStrategyId || '').trim() === 'primary') {
        return {
            label: 'Primary strategy',
            strategy: cloneSerializable(safeJob.strategy, buildBlankStrategy()),
        }
    }

    const entry = normalizeBatchStrategyEntries(safeJob.strategies, safeJob.strategy).find((item) => String(item?.id || '').trim() === String(targetStrategyId || '').trim())
    if (!entry) {
        return null
    }

    return {
        label: String(entry.label || entry.id || 'Auxiliary strategy').trim() || 'Auxiliary strategy',
        strategy: cloneSerializable(entry.strategy, buildBlankStrategy()),
    }
}

function buildMutationDeltaPreview(jobLike, parentJobLike) {
    if (!parentJobLike) {
        return null
    }

    const safeJob = normalizeBatchJob(jobLike, 0)
    const mutation = safeJob?.researchPlan?.mutation && typeof safeJob.researchPlan.mutation === 'object'
        ? safeJob.researchPlan.mutation
        : null
    if (!mutation) {
        return null
    }

    const targetStrategyId = String(mutation.mutationTargetStrategyId || 'primary').trim() || 'primary'
    const childTarget = getMutationTargetStrategy(safeJob, targetStrategyId)
    const parentTarget = getMutationTargetStrategy(parentJobLike, targetStrategyId)
    if (!childTarget || !parentTarget) {
        return null
    }

    const watchedFields = [
        ['long.openIf', childTarget.strategy?.long?.openIf, parentTarget.strategy?.long?.openIf],
        ['short.openIf', childTarget.strategy?.short?.openIf, parentTarget.strategy?.short?.openIf],
        ['long.closeIf', childTarget.strategy?.long?.closeIf, parentTarget.strategy?.long?.closeIf],
        ['short.closeIf', childTarget.strategy?.short?.closeIf, parentTarget.strategy?.short?.closeIf],
        ['long.gainPrice', childTarget.strategy?.long?.gainPrice, parentTarget.strategy?.long?.gainPrice],
        ['short.gainPrice', childTarget.strategy?.short?.gainPrice, parentTarget.strategy?.short?.gainPrice],
        ['long.lossPrice', childTarget.strategy?.long?.lossPrice, parentTarget.strategy?.long?.lossPrice],
        ['short.lossPrice', childTarget.strategy?.short?.lossPrice, parentTarget.strategy?.short?.lossPrice],
        ['long.trailingPrice', childTarget.strategy?.long?.trailingPrice, parentTarget.strategy?.long?.trailingPrice],
        ['short.trailingPrice', childTarget.strategy?.short?.trailingPrice, parentTarget.strategy?.short?.trailingPrice],
    ]

    const changes = watchedFields
        .filter(([, childValue, parentValue]) => String(childValue || '').trim() !== String(parentValue || '').trim())
        .map(([label]) => label)

    const childSignature = buildPortfolioJobSignature(safeJob)
    const parentSignature = buildPortfolioJobSignature(parentJobLike)

    return {
        targetLabel: childTarget.label,
        parentLabel: String(parentJobLike?.label || '').trim() || 'Parent job',
        changedFields: changes,
        signatureChanged: childSignature !== parentSignature,
        summary: changes.length
            ? `${childTarget.label} changed in ${changes.length} field${changes.length === 1 ? '' : 's'}`
            : `${childTarget.label} kept the same tracked fields`,
    }
}

const NEW_BATCH_JOB_ID = '__new_batch_job__'
const BULK_EDIT_JOB_ID = '__bulk_edit_job__'

function buildBlankBulkEditDraft() {
    return {
        label: '',
        symbol: '',
        timeframe: '',
        bars: '',
        notes: '',
        strategy: {
            long: {
                openPrice: '',
                closePrice: '',
                openIf: '',
                closeIf: '',
                gainPrice: '',
                lossPrice: '',
                trailingPrice: '',
            },
            short: {
                openPrice: '',
                closePrice: '',
                openIf: '',
                closeIf: '',
                gainPrice: '',
                lossPrice: '',
                trailingPrice: '',
            },
            other: {
                allowInversion: null,
                priority: '',
            },
        },
        backtest: {
            initialBalance: '',
            assetType: '',
            initialVolume: '',
            pipSize: '',
            pipValuePerLot: '',
            costProfile: '',
            spreadInPips: '',
            slippageInPips: '',
            entrySlippageInPips: '',
            closeSlippageInPips: '',
            takeProfitSlippageInPips: '',
            stopLossSlippageInPips: '',
            trailingStopSlippageInPips: '',
            minimumStopDistanceInPips: '',
            volatilitySlippageMultiplier: '',
            executionMode: '',
            historyScopeMode: '',
            historyScopeBars: '',
        },
    }
}

export function Batch({
    isActive,
    authToken,
    workspaceSocketStatus = 'connecting',
    batchState,
    setBatchState,
    onLogEvent,
    sharedConsoleJobs = null,
    onSharedConsoleJobChange,
    onHydrateBacktestResult,
    onOpenResults,
    onOpenResearchRun,
}) {
    const [runtimeStatus, setRuntimeStatus] = useState({
        isLoading: false,
        error: '',
        lastLoadedAt: 0,
        serviceHealth: null,
        bridgeStatus: null,
    })
    const [actionFeedback, setActionFeedback] = useState({
        tone: 'idle',
        title: '',
        detail: '',
    })
    const [pendingAction, setPendingAction] = useState('')
    const [activeTab, setActiveTab] = useState('manager')
    const [activeBatchId, setActiveBatchId] = useState('')
    const [managerSelectedBatchId, setManagerSelectedBatchId] = useState('')
    const [jobDraft, setJobDraft] = useState(() => buildJobDraft())
    const [bulkEditDraft, setBulkEditDraft] = useState(() => buildBlankBulkEditDraft())
    const [jobDetailTab, setJobDetailTab] = useState('overview')
    const [jobBacktestTab, setJobBacktestTab] = useState('capital')
    const [activeStrategyFieldId, setActiveStrategyFieldId] = useState('')
    const [selectedJobId, setSelectedJobId] = useState(NEW_BATCH_JOB_ID)
    const [pendingLoadedJobId, setPendingLoadedJobId] = useState('')
    const [batchLabel, setBatchLabel] = useState('Batch run')
    const [reportSource, setReportSource] = useState('latest_batch')
    const [reportQuery, setReportQuery] = useState(() => String(batchState?.options?.reportQuery || ''))
    const [reportStatusFilter, setReportStatusFilter] = useState(() => String(batchState?.options?.reportStatusFilter || 'all'))
    const [reportSymbolFilter, setReportSymbolFilter] = useState(() => String(batchState?.options?.reportSymbolFilter || 'all'))
    const [reportTimeframeFilter, setReportTimeframeFilter] = useState(() => String(batchState?.options?.reportTimeframeFilter || 'all'))
    const [reportSortKey, setReportSortKey] = useState(() => String(batchState?.options?.reportSortKey || 'created_at'))
    const [reportSortDirection, setReportSortDirection] = useState(() => String(batchState?.options?.reportSortDirection || 'desc'))
    const [selectedRuntimeJobId, setSelectedRuntimeJobId] = useState('')
    const [templateLabel, setTemplateLabel] = useState('Batch template')
    const [templateDescription, setTemplateDescription] = useState('')
    const [batchCompletionDialog, setBatchCompletionDialog] = useState(null)
    const [remoteJobs, setRemoteJobs] = useState([])
    const [remoteBatches, setRemoteBatches] = useState([])
    const [remoteCampaigns, setRemoteCampaigns] = useState([])
    const strategyFieldRefs = useRef({})
    const lastObservedBatchRef = useRef({ id: '', status: '' })
    const pendingRuntimeViewRef = useRef(null)
    const [strategyFieldSelectionMap, setStrategyFieldSelectionMap] = useState({})
    const rawBatchFeatures = batchState?.features
    const rawJobs = batchState?.jobs
    const rawOptions = batchState?.options
    const batchFeatures = useMemo(
        () => Array.isArray(rawBatchFeatures) ? rawBatchFeatures.map((indicator) => normalizeIndicator(indicator)) : [],
        [rawBatchFeatures],
    )
    const jobs = useMemo(
        () => Array.isArray(rawJobs) ? rawJobs : [],
        [rawJobs],
    )
    const options = useMemo(
        () => buildDefaultBatchOptions(
            rawOptions && typeof rawOptions === 'object'
                ? rawOptions
                : {}
        ),
        [rawOptions],
    )
    const deferredBatchFeatures = useDeferredValue(batchFeatures)
    const deferredJobs = useDeferredValue(jobs)
    const deferredOptions = useDeferredValue(options)
    const activeTemplateId = String(options?.activeTemplateId || '').trim()
    const hydratedTemplateIdRef = useRef('')

    useEffect(() => {
        if (!pendingLoadedJobId) {
            return
        }
        if (!jobs.some((entry) => String(entry?.id) === String(pendingLoadedJobId))) {
            return
        }
        setSelectedJobId(String(pendingLoadedJobId))
        setPendingLoadedJobId('')
    }, [jobs, pendingLoadedJobId])

    useEffect(() => {
        if (selectedJobId === NEW_BATCH_JOB_ID || selectedJobId === BULK_EDIT_JOB_ID) {
            return
        }
        if (!jobs.some((entry) => entry?.id === selectedJobId)) {
            setSelectedJobId(NEW_BATCH_JOB_ID)
        }
    }, [jobs, selectedJobId])

    useEffect(() => {
        setJobBacktestTab('capital')
    }, [selectedJobId])

    useEffect(() => {
        if (!activeTemplateId || !remoteCampaigns.length) {
            hydratedTemplateIdRef.current = ''
            return undefined
        }
        if (hydratedTemplateIdRef.current === activeTemplateId) {
            return undefined
        }
        const matchingCampaign = remoteCampaigns.find((entry) => String(entry?.id || '') === activeTemplateId)
        if (!matchingCampaign) {
            return undefined
        }

        let cancelled = false

        void (async () => {
            let resolvedCampaign = matchingCampaign
            if (matchingCampaign?.request_loaded === false) {
                try {
                    resolvedCampaign = await loadRemoteCampaignDetail(activeTemplateId) || matchingCampaign
                } catch {
                    return
                }
            }
            if (cancelled || !resolvedCampaign) {
                return
            }
            hydratedTemplateIdRef.current = activeTemplateId
            const nextEditorState = buildEditorStateFromCampaign(resolvedCampaign)
            persistPatch(nextEditorState)
            setBatchLabel(String(resolvedCampaign?.label || 'Batch run'))
            setTemplateLabel(String(resolvedCampaign?.label || 'Batch template'))
            setTemplateDescription(String(resolvedCampaign?.description || ''))
            setPendingLoadedJobId(String(nextEditorState.jobs?.[0]?.id || ''))
        })()

        return () => {
            cancelled = true
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTemplateId, remoteCampaigns])

    useEffect(() => {
        if (!authToken || !managerSelectedBatchId) {
            return
        }
        const matchingCampaign = remoteCampaigns.find((entry) => String(entry?.id || '') === String(managerSelectedBatchId)) || null
        if (!matchingCampaign || matchingCampaign?.request_loaded !== false) {
            return
        }
        void loadRemoteCampaignDetail(managerSelectedBatchId).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authToken, managerSelectedBatchId, remoteCampaigns])

    const selectedJob = useMemo(
        () => selectedJobId === NEW_BATCH_JOB_ID
            ? null
            : jobs.find((entry) => entry?.id === selectedJobId) || null,
        [jobs, selectedJobId],
    )
    const isCreatingJob = selectedJobId === NEW_BATCH_JOB_ID
    const isBulkEditingJobs = selectedJobId === BULK_EDIT_JOB_ID
    const draftJobPreview = useMemo(
        () => buildJobFromDraft(jobDraft, null, jobs.length),
        [jobDraft, jobs.length],
    )
    const draftJobChartSettings = useMemo(
        () => buildBatchJobChartSettings(draftJobPreview, deferredBatchFeatures),
        [deferredBatchFeatures, draftJobPreview],
    )
    const selectedJobChartSettings = useMemo(
        () => selectedJob ? buildBatchJobChartSettings(selectedJob, deferredBatchFeatures) : null,
        [deferredBatchFeatures, selectedJob],
    )
    const draftTokenGroups = useMemo(() => getStrategyTokenGroups(draftJobChartSettings), [draftJobChartSettings])
    const selectedTokenGroups = useMemo(
        () => selectedJobChartSettings ? getStrategyTokenGroups(selectedJobChartSettings) : [],
        [selectedJobChartSettings],
    )
    const draftUsedIndicatorCount = useMemo(
        () => countUsedIndicatorsForStrategy(draftJobPreview.strategy, draftJobChartSettings),
        [draftJobChartSettings, draftJobPreview.strategy],
    )
    const selectedUsedIndicatorCount = useMemo(
        () => selectedJobChartSettings ? countUsedIndicatorsForStrategy(selectedJob?.strategy, selectedJobChartSettings) : 0,
        [selectedJob?.strategy, selectedJobChartSettings],
    )
    const latestPipelineBatch = useMemo(() => {
        const pipelineBatches = remoteBatches.filter((entry) => Array.isArray(entry?.request?.jobs) && entry.request.jobs.some((job) => job?.job_type === 'strategy_pipeline'))
        if (!pipelineBatches.length) {
            return null
        }
        if (activeBatchId) {
            return pipelineBatches.find((entry) => String(entry?.id) === String(activeBatchId)) || pipelineBatches[0] || null
        }
        return pipelineBatches[0] || null
    }, [activeBatchId, remoteBatches])
    const pipelineJobs = useMemo(
        () => remoteJobs.filter((entry) => String(entry?.job_type || '').trim().toLowerCase() === 'strategy_pipeline'),
        [remoteJobs],
    )
    const activePipelineJob = useMemo(
        () => pipelineJobs.find((entry) => ['queued', 'running'].includes(String(entry?.status || '').toLowerCase())) || null,
        [pipelineJobs],
    )
    const latestBatchPipelineJobIds = useMemo(() => {
        const requestJobs = Array.isArray(latestPipelineBatch?.request?.jobs) ? latestPipelineBatch.request.jobs : []
        const currentJobId = latestPipelineBatch?.current_job_id
        const resultJobs = Array.isArray(latestPipelineBatch?.result?.jobs) ? latestPipelineBatch.result.jobs : []
        const collected = new Set()

        for (const item of requestJobs) {
            if (String(item?.job_type || '').trim().toLowerCase() === 'strategy_pipeline' && item?.id) {
                collected.add(String(item.id))
            }
        }
        if (currentJobId !== undefined && currentJobId !== null) {
            collected.add(String(currentJobId))
        }
        for (const item of resultJobs) {
            if (item?.job_id !== undefined && item?.job_id !== null) {
                collected.add(String(item.job_id))
            }
        }

        return collected
    }, [latestPipelineBatch])
    const visiblePipelineJobs = useMemo(() => {
        if (reportSource !== 'latest_batch') {
            return pipelineJobs
        }
        if (!latestBatchPipelineJobIds.size) {
            return activePipelineJob ? [activePipelineJob] : []
        }
        return pipelineJobs.filter((entry) => latestBatchPipelineJobIds.has(String(entry?.id)))
    }, [activePipelineJob, latestBatchPipelineJobIds, pipelineJobs, reportSource])
    const reportSymbolOptions = useMemo(
        () => Array.from(new Set(pipelineJobs.map((entry) => String(entry?.result?.pipeline?.chart?.symbol || '').trim().toUpperCase()).filter(Boolean))).sort(),
        [pipelineJobs],
    )
    const reportTimeframeOptions = useMemo(
        () => Array.from(new Set(pipelineJobs.map((entry) => String(entry?.result?.pipeline?.chart?.timeframe || '').trim().toUpperCase()).filter(Boolean))).sort(),
        [pipelineJobs],
    )
    const filteredPipelineJobs = useMemo(() => {
        const query = String(reportQuery || '').trim().toLowerCase()
        const filtered = visiblePipelineJobs.filter((job) => {
            const status = String(job?.status || '').trim().toLowerCase()
            const symbol = String(job?.result?.pipeline?.chart?.symbol || '').trim().toUpperCase()
            const timeframe = String(job?.result?.pipeline?.chart?.timeframe || '').trim().toUpperCase()
            const label = String(job?.run_label || job?.phase_label || job?.job_type || '').trim().toLowerCase()
            const queryMatches = !query || label.includes(query) || symbol.toLowerCase().includes(query) || timeframe.toLowerCase().includes(query)
            const statusMatches = reportStatusFilter === 'all' ? true : status === reportStatusFilter
            const symbolMatches = reportSymbolFilter === 'all' ? true : symbol === reportSymbolFilter
            const timeframeMatches = reportTimeframeFilter === 'all' ? true : timeframe === reportTimeframeFilter
            return queryMatches && statusMatches && symbolMatches && timeframeMatches
        })
        const directionFactor = reportSortDirection === 'asc' ? 1 : -1
        return [...filtered].sort((left, right) => {
            const getSortValue = (job) => {
                if (reportSortKey === 'label') {
                    return String(job?.run_label || job?.phase_label || job?.job_type || '').trim().toLowerCase()
                }
                if (reportSortKey === 'status') {
                    return String(job?.status || '').trim().toLowerCase()
                }
                return getPipelineStat(job, reportSortKey) ?? Number(job?.[reportSortKey] || 0)
            }

            const leftValue = getSortValue(left)
            const rightValue = getSortValue(right)

            if (typeof leftValue === 'string' || typeof rightValue === 'string') {
                return String(leftValue).localeCompare(String(rightValue)) * directionFactor
            }

            return ((Number(leftValue) || 0) - (Number(rightValue) || 0)) * directionFactor
        })
    }, [reportQuery, reportSortDirection, reportSortKey, reportStatusFilter, reportSymbolFilter, reportTimeframeFilter, visiblePipelineJobs])
    const selectedRuntimeJob = useMemo(
        () => filteredPipelineJobs.find((job) => String(job?.id) === String(selectedRuntimeJobId))
            || visiblePipelineJobs.find((job) => String(job?.id) === String(selectedRuntimeJobId))
            || activePipelineJob
            || filteredPipelineJobs[0]
            || visiblePipelineJobs[0]
            || null,
        [activePipelineJob, filteredPipelineJobs, selectedRuntimeJobId, visiblePipelineJobs],
    )

    useEffect(() => {
        const currentId = String(selectedRuntimeJob?.id || '')
        if (currentId && currentId !== String(selectedRuntimeJobId || '')) {
            setSelectedRuntimeJobId(currentId)
        }
    }, [selectedRuntimeJob?.id, selectedRuntimeJobId])

    useEffect(() => {
        const nextRuntimeView = {
            reportQuery: String(options?.reportQuery || ''),
            reportStatusFilter: String(options?.reportStatusFilter || 'all'),
            reportSymbolFilter: String(options?.reportSymbolFilter || 'all'),
            reportTimeframeFilter: String(options?.reportTimeframeFilter || 'all'),
            reportSortKey: String(options?.reportSortKey || 'created_at'),
            reportSortDirection: String(options?.reportSortDirection || 'desc'),
        }
        const pendingRuntimeView = pendingRuntimeViewRef.current
        if (pendingRuntimeView) {
            const matchesPending = Object.keys(pendingRuntimeView).every((key) => String(nextRuntimeView[key] || '') === String(pendingRuntimeView[key] || ''))
            if (!matchesPending) {
                return
            }
            pendingRuntimeViewRef.current = null
        }
        if (nextRuntimeView.reportQuery !== reportQuery) {
            setReportQuery(nextRuntimeView.reportQuery)
        }
        if (nextRuntimeView.reportStatusFilter !== reportStatusFilter) {
            setReportStatusFilter(nextRuntimeView.reportStatusFilter)
        }
        if (nextRuntimeView.reportSymbolFilter !== reportSymbolFilter) {
            setReportSymbolFilter(nextRuntimeView.reportSymbolFilter)
        }
        if (nextRuntimeView.reportTimeframeFilter !== reportTimeframeFilter) {
            setReportTimeframeFilter(nextRuntimeView.reportTimeframeFilter)
        }
        if (nextRuntimeView.reportSortKey !== reportSortKey) {
            setReportSortKey(nextRuntimeView.reportSortKey)
        }
        if (nextRuntimeView.reportSortDirection !== reportSortDirection) {
            setReportSortDirection(nextRuntimeView.reportSortDirection)
        }
    }, [
        options?.reportQuery,
        options?.reportStatusFilter,
        options?.reportSymbolFilter,
        options?.reportTimeframeFilter,
        options?.reportSortDirection,
        options?.reportSortKey,
        reportQuery,
        reportSortDirection,
        reportSortKey,
        reportStatusFilter,
        reportSymbolFilter,
        reportTimeframeFilter,
    ])

    function applyRuntimeViewPatch(nextPatch = {}) {
        const nextRuntimeView = {
            reportQuery,
            reportStatusFilter,
            reportSymbolFilter,
            reportTimeframeFilter,
            reportSortKey,
            reportSortDirection,
            ...(nextPatch && typeof nextPatch === 'object' ? nextPatch : {}),
        }
        pendingRuntimeViewRef.current = nextRuntimeView
        setReportQuery(nextRuntimeView.reportQuery)
        setReportStatusFilter(nextRuntimeView.reportStatusFilter)
        setReportSymbolFilter(nextRuntimeView.reportSymbolFilter)
        setReportTimeframeFilter(nextRuntimeView.reportTimeframeFilter)
        setReportSortKey(nextRuntimeView.reportSortKey)
        setReportSortDirection(nextRuntimeView.reportSortDirection)
        persistPatch({
            options: {
                ...options,
                ...nextRuntimeView,
            },
        })
    }

    function persistPatch(nextPatch) {
        setBatchState?.((current) => ({
            features: Array.isArray(current?.features) ? current.features : [],
            jobs: Array.isArray(current?.jobs) ? current.jobs : [],
            ...nextPatch,
            options: buildDefaultBatchOptions({
                ...(current?.options && typeof current.options === 'object' ? current.options : {}),
                ...(nextPatch?.options && typeof nextPatch.options === 'object' ? nextPatch.options : {}),
            }),
        }))
    }

    function showActionFeedback(tone, title, detail = '') {
        setActionFeedback({
            tone: String(tone || 'idle'),
            title: String(title || '').trim(),
            detail: String(detail || '').trim(),
        })
    }

    function handleToggleComparisonPreset(jobId, comparisonJobId) {
        const selectionMap = getComparisonPresetSelectionMap(options)
        const currentSelected = Array.isArray(selectionMap?.[jobId]) ? selectionMap[jobId] : []
        const normalizedSelected = Array.from(new Set(currentSelected.map((value) => String(value || '').trim()).filter(Boolean)))
        const nextSelected = normalizedSelected.includes(String(comparisonJobId))
            ? normalizedSelected.filter((value) => value !== String(comparisonJobId))
            : [...normalizedSelected, String(comparisonJobId)]
        const nextSelectionMap = {
            ...selectionMap,
            [jobId]: nextSelected,
        }
        if (!nextSelected.length) {
            delete nextSelectionMap[jobId]
        }
        persistPatch({
            options: {
                ...options,
                comparisonPresetSelectionMap: nextSelectionMap,
            },
        })
    }

    function handleResetComparisonPresetSelection(jobId) {
        const selectionMap = getComparisonPresetSelectionMap(options)
        if (!selectionMap?.[jobId]) {
            return
        }
        const nextSelectionMap = { ...selectionMap }
        delete nextSelectionMap[jobId]
        persistPatch({
            options: {
                ...options,
                comparisonPresetSelectionMap: nextSelectionMap,
            },
        })
    }

    function handleToggleResearchStudy(studyKey) {
        const currentStudies = {
            ...DEFAULT_RESEARCH_STUDIES,
            ...(options?.researchStudies && typeof options.researchStudies === 'object' ? options.researchStudies : {}),
        }
        persistPatch({
            options: {
                ...options,
                researchStudies: {
                    ...currentStudies,
                    [studyKey]: !currentStudies?.[studyKey],
                },
            },
        })
    }

    function handleChangePortfolioMutationMode(mode) {
        const nextMode = String(mode || '').trim() === 'mutate_selected_auxiliary'
            ? 'mutate_selected_auxiliary'
            : 'mutate_primary_only'
        const fallbackTargetId = availablePortfolioMutationTargets[0]?.id || 'primary'
        persistPatch({
            options: {
                ...options,
                portfolioMutation: buildDefaultPortfolioMutationOptions({
                    ...portfolioMutationOptions,
                    mode: nextMode,
                    targetStrategyId: nextMode === 'mutate_selected_auxiliary'
                        ? (selectedPortfolioMutationTarget?.id || fallbackTargetId)
                        : 'primary',
                }),
            },
        })
    }

    function handleChangePortfolioMutationTarget(targetStrategyId) {
        persistPatch({
            options: {
                ...options,
                portfolioMutation: buildDefaultPortfolioMutationOptions({
                    ...portfolioMutationOptions,
                    mode: 'mutate_selected_auxiliary',
                    targetStrategyId,
                }),
            },
        })
    }

    async function refreshRuntime({ quiet = false, includeCampaigns = true, includeDiagnostics = true } = {}) {
        if (!authToken) {
            setRemoteJobs([])
            setRemoteBatches([])
            setRemoteCampaigns([])
            setRuntimeStatus({
                isLoading: false,
                error: '',
                lastLoadedAt: 0,
                serviceHealth: null,
                bridgeStatus: null,
            })
            return
        }

        if (!quiet) {
            setRuntimeStatus((current) => ({
                ...current,
                isLoading: true,
                error: '',
            }))
        }

        try {
            const [jobsResponse, batchesResponse, campaignsResponse] = await Promise.all([
                fetch(buildApiUrl('/workspace/research-jobs?workspace_id=default&limit=100&include_payload=false'), {
                    headers: { Authorization: `Bearer ${authToken}` },
                }),
                fetch(buildApiUrl('/workspace/research-batches?workspace_id=default&limit=100&include_payload=false'), {
                    headers: { Authorization: `Bearer ${authToken}` },
                }),
                includeCampaigns
                    ? fetch(buildApiUrl('/workspace/research-campaigns?workspace_id=default&limit=100&include_payload=false'), {
                        headers: { Authorization: `Bearer ${authToken}` },
                    })
                    : Promise.resolve(null),
            ])
            const [jobsPayload, batchesPayload, campaignsPayload] = await Promise.all([
                readJsonResponse(jobsResponse),
                readJsonResponse(batchesResponse),
                campaignsResponse ? readJsonResponse(campaignsResponse) : Promise.resolve(null),
            ])
            if (!jobsResponse.ok || jobsPayload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(jobsPayload, 'Failed to load backend batch jobs.'))
            }
            if (!batchesResponse.ok || batchesPayload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(batchesPayload, 'Failed to load backend batch runs.'))
            }
            if (includeCampaigns && (!campaignsResponse?.ok || campaignsPayload?.status !== 'ok')) {
                throw new Error(extractApiErrorMessage(campaignsPayload, 'Failed to load batch templates.'))
            }

            const sortedJobs = Array.isArray(jobsPayload?.jobs)
                ? [...jobsPayload.jobs].sort((left, right) => Number(right?.created_at || right?.id || 0) - Number(left?.created_at || left?.id || 0))
                : []
            const sortedBatches = Array.isArray(batchesPayload?.batches)
                ? [...batchesPayload.batches].sort((left, right) => Number(right?.created_at || right?.id || 0) - Number(left?.created_at || left?.id || 0))
                : []
            const sortedCampaigns = includeCampaigns && Array.isArray(campaignsPayload?.campaigns)
                ? [...campaignsPayload.campaigns].sort((left, right) => Number(right?.updated_at || right?.created_at || right?.id || 0) - Number(left?.updated_at || left?.created_at || left?.id || 0))
                : []

            const [healthResult, bridgeResult] = includeDiagnostics
                ? await Promise.allSettled([
                    fetch(buildApiUrl('/health/ready'), {
                        headers: { Authorization: `Bearer ${authToken}` },
                    }).then(async (response) => ({
                        ok: response.ok,
                        payload: await readJsonResponse(response),
                    })),
                    fetch(buildApiUrl('/bridge/status'), {
                        headers: { Authorization: `Bearer ${authToken}` },
                    }).then(async (response) => ({
                        ok: response.ok,
                        payload: await readJsonResponse(response),
                    })),
                ])
                : [null, null]

            setRemoteJobs((current) => reconcileRemoteBatchRuntimeEntries(current, sortedJobs))
            setRemoteBatches((current) => reconcileRemoteBatchRuntimeEntries(current, sortedBatches))
            if (includeCampaigns) {
                setRemoteCampaigns((current) => reconcileRemoteBatchRuntimeEntries(current, sortedCampaigns))
            }
            setRuntimeStatus((current) => ({
                isLoading: false,
                error: '',
                lastLoadedAt: Date.now(),
                serviceHealth: includeDiagnostics
                    ? (healthResult?.status === 'fulfilled' && healthResult.value.ok ? healthResult.value.payload : null)
                    : current.serviceHealth,
                bridgeStatus: includeDiagnostics
                    ? (bridgeResult?.status === 'fulfilled' && bridgeResult.value.ok ? bridgeResult.value.payload : null)
                    : current.bridgeStatus,
            }))
        } catch (error) {
            setRuntimeStatus((current) => ({
                isLoading: false,
                error: error?.message || 'Failed to sync backend batch runtime.',
                lastLoadedAt: current.lastLoadedAt || 0,
            }))
            throw error
        }
    }

    async function loadRemoteJobDetail(jobId) {
        const normalizedJobId = String(jobId || '').trim()
        if (!authToken || !normalizedJobId) {
            return null
        }

        const existingJob = remoteJobs.find((entry) => String(entry?.id || '') === normalizedJobId) || null
        if (existingJob && existingJob?.result_loaded !== false) {
            return existingJob
        }

        const response = await fetch(buildApiUrl(`/workspace/research-jobs/${normalizedJobId}?workspace_id=default&include_payload=true`), {
            headers: { Authorization: `Bearer ${authToken}` },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to load backend batch job details.'))
        }

        const hydratedJob = payload?.job || null
        if (!hydratedJob) {
            return null
        }

        setRemoteJobs((current) => mergeRemoteBatchRuntimeEntries(current, hydratedJob))
        return hydratedJob
    }

    async function loadRemoteCampaignDetail(campaignId) {
        const normalizedCampaignId = String(campaignId || '').trim()
        if (!authToken || !normalizedCampaignId) {
            return null
        }

        const existingCampaign = remoteCampaigns.find((entry) => String(entry?.id || '') === normalizedCampaignId) || null
        if (existingCampaign && existingCampaign?.request_loaded !== false) {
            return existingCampaign
        }

        const response = await fetch(buildApiUrl(`/workspace/research-campaigns/${normalizedCampaignId}?workspace_id=default&include_payload=true`), {
            headers: { Authorization: `Bearer ${authToken}` },
        })
        const payload = await readJsonResponse(response)
        if (!response.ok || payload?.status !== 'ok') {
            throw new Error(extractApiErrorMessage(payload, 'Failed to load saved batch details.'))
        }

        const hydratedCampaign = payload?.campaign || null
        if (!hydratedCampaign) {
            return null
        }

        setRemoteCampaigns((current) => mergeRemoteBatchRuntimeEntries(current, hydratedCampaign))
        return hydratedCampaign
    }

    useEffect(() => {
        if (!authToken) {
            setRemoteJobs([])
            setRemoteBatches([])
            setRemoteCampaigns([])
            setRuntimeStatus({
                isLoading: false,
                error: '',
                lastLoadedAt: 0,
                serviceHealth: null,
                bridgeStatus: null,
            })
            return
        }
        if (!isActive) {
            return
        }

        void refreshRuntime().catch((error) => {
            onLogEvent?.(`Batch · Could not load backend runtime: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Backend runtime unavailable', error?.message || 'Could not sync batch state.')
        })
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authToken, isActive])

    useEffect(() => {
        if (
            !authToken
            || !isActive
            || workspaceSocketStatus === 'connected'
            || !latestPipelineBatch
            || !['queued', 'running'].includes(String(latestPipelineBatch?.status || '').toLowerCase())
        ) {
            return undefined
        }
        const intervalId = window.setInterval(() => {
            void refreshRuntime({ quiet: true, includeCampaigns: false, includeDiagnostics: false }).catch(() => {})
        }, 2000)
        return () => window.clearInterval(intervalId)
        // eslint-disable-next-line react-hooks/exhaustive-deps
        }, [authToken, isActive, workspaceSocketStatus, latestPipelineBatch?.id, latestPipelineBatch?.status])

    useEffect(() => {
        const isSharedRunning = sharedConsoleJobs?.batch?.status === 'running'
        const isBackendRunning = ['queued', 'running'].includes(String(latestPipelineBatch?.status || '').toLowerCase())
        if (isSharedRunning && !isBackendRunning) {
            onSharedConsoleJobChange?.('batch', null)
        }
    }, [latestPipelineBatch?.status, onSharedConsoleJobChange, sharedConsoleJobs?.batch?.status])

    useEffect(() => {
        if (!activeBatchId) {
            return
        }
        if (!remoteBatches.some((entry) => String(entry?.id) === String(activeBatchId))) {
            setActiveBatchId('')
        }
    }, [activeBatchId, remoteBatches])

    useEffect(() => {
        const batchId = String(latestPipelineBatch?.id || '')
        const status = String(latestPipelineBatch?.status || '').toLowerCase()
        const previous = lastObservedBatchRef.current

        if (!batchId) {
            lastObservedBatchRef.current = { id: '', status: '' }
            return
        }

        const previousWasRunning = previous.id === batchId && ['queued', 'running'].includes(previous.status)
        const isTerminal = ['completed', 'failed', 'cancelled'].includes(status)

        if (previousWasRunning && isTerminal) {
            setBatchCompletionDialog({
                id: batchId,
                label: latestPipelineBatch?.label || 'Batch',
                status,
                completedJobs: Number(latestPipelineBatch?.completed_jobs || 0),
                failedJobs: Number(latestPipelineBatch?.failed_jobs || 0),
                cancelledJobs: Number(latestPipelineBatch?.cancelled_jobs || 0),
                totalJobs: Number(latestPipelineBatch?.total_jobs || 0),
                detail: latestPipelineBatch?.error || latestPipelineBatch?.detail || 'Batch reached a terminal state.',
                durationLabel: formatDurationSeconds(getElapsedSeconds(latestPipelineBatch?.started_at, latestPipelineBatch?.finished_at)),
            })
        }

        lastObservedBatchRef.current = { id: batchId, status }
    }, [
        latestPipelineBatch?.id,
        latestPipelineBatch?.status,
        latestPipelineBatch?.completed_jobs,
        latestPipelineBatch?.failed_jobs,
        latestPipelineBatch?.cancelled_jobs,
        latestPipelineBatch?.total_jobs,
        latestPipelineBatch?.error,
        latestPipelineBatch?.detail,
        latestPipelineBatch?.label,
        latestPipelineBatch?.started_at,
        latestPipelineBatch?.finished_at,
    ])

    useEffect(() => {
        if (!activeBatchId || String(latestPipelineBatch?.id || '') !== String(activeBatchId)) {
            return
        }

        const status = String(latestPipelineBatch?.status || '').toLowerCase()
        if (['queued', 'running'].includes(status)) {
            return
        }

        setPendingAction((current) => (current === 'cancelBatch' ? '' : current))
        setActionFeedback((current) => {
            const title = String(current?.title || '')
            if (
                title === 'Cancellation requested'
                || title === 'Cancelling batch'
                || title === 'Batch started'
                || title === 'Saved batch started'
            ) {
                return {
                    tone: 'idle',
                    title: '',
                    detail: '',
                }
            }
            return current
        })
        setActiveBatchId('')
    }, [activeBatchId, latestPipelineBatch?.id, latestPipelineBatch?.status])

    useEffect(() => {
        setActionFeedback((current) => {
            const nextBatchId = String(latestPipelineBatch?.id || '')
            const nextStatus = String(latestPipelineBatch?.status || '').toLowerCase()
            const isStaleCancelMessage = current.title === 'Cancellation requested' && (!nextBatchId || !['queued', 'running'].includes(nextStatus) || !latestPipelineBatch?.cancel_requested)
            const isStaleStartMessage = (current.title === 'Batch started' || current.title === 'Saved batch started') && (!nextBatchId || !['queued', 'running'].includes(nextStatus))
            if (!isStaleCancelMessage && !isStaleStartMessage) {
                return current
            }
            return {
                tone: 'idle',
                title: '',
                detail: '',
            }
        })
    }, [latestPipelineBatch?.id, latestPipelineBatch?.status, latestPipelineBatch?.cancel_requested])

    useEffect(() => {
        setActionFeedback((current) => {
            const title = String(current?.title || '').trim()
            const status = String(latestPipelineBatch?.status || '').toLowerCase()
            const hasLiveBatch = ['queued', 'running'].includes(status)
            const isTransientStartTitle = title === 'Starting batch' || title === 'Launching saved batch' || title === 'Creating failed-job rerun'
            const isRunningToneWithoutPendingAction = current?.tone === 'running' && !pendingAction

            if ((!isTransientStartTitle && !isRunningToneWithoutPendingAction) || pendingAction) {
                return current
            }

            if (hasLiveBatch) {
                return current
            }

            return {
                tone: 'idle',
                title: '',
                detail: '',
            }
        })
    }, [latestPipelineBatch?.status, pendingAction])

    useEffect(() => {
        if (!authToken || !isActive) {
            return undefined
        }

        function handleBatchUpdate(event) {
            const nextBatch = event?.detail
            if (nextBatch?.id) {
                setRemoteBatches((current) => mergeRemoteBatchRuntimeEntries(current, nextBatch))
                setRuntimeStatus((current) => ({
                    ...current,
                    lastLoadedAt: Date.now(),
                }))
                return
            }
            void refreshRuntime({ quiet: true }).catch(() => {})
        }

        function handleJobUpdate(event) {
            const nextJob = event?.detail
            if (nextJob?.id) {
                setRemoteJobs((current) => mergeRemoteBatchRuntimeEntries(current, nextJob))
                setRuntimeStatus((current) => ({
                    ...current,
                    lastLoadedAt: Date.now(),
                }))
                return
            }
            void refreshRuntime({ quiet: true }).catch(() => {})
        }

        window.addEventListener('workspace:research-batch-updated', handleBatchUpdate)
        window.addEventListener('workspace:research-job-updated', handleJobUpdate)
        return () => {
            window.removeEventListener('workspace:research-batch-updated', handleBatchUpdate)
            window.removeEventListener('workspace:research-job-updated', handleJobUpdate)
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authToken, isActive])

    function handleImportJobsFromText(rawText) {
        try {
            const importedPayload = parseImportedBatchPayload(rawText)
            persistPatch({
                jobs: importedPayload.jobs,
                features: importedPayload.sharedFeatures,
                options: buildDefaultBatchOptions(
                    (() => {
                        try {
                            const parsed = JSON.parse(String(rawText || '').trim())
                            return parsed && typeof parsed === 'object' ? (parsed.options || {}) : {}
                        } catch {
                            return {}
                        }
                    })()
                ),
            })
            setSelectedJobId(importedPayload.jobs[0]?.id || NEW_BATCH_JOB_ID)
            onLogEvent?.(`Batch · Loaded ${importedPayload.jobs.length} job${importedPayload.jobs.length > 1 ? 's' : ''} into the current batch.`)
            showActionFeedback('success', 'Batch JSON loaded', `${importedPayload.jobs.length} job${importedPayload.jobs.length > 1 ? 's were' : ' was'} imported into the current batch.`)
        } catch (error) {
            onLogEvent?.(`Batch · Could not load batch JSON: ${error?.message || 'invalid payload'}`)
            showActionFeedback('error', 'Could not load batch JSON', error?.message || 'Invalid payload.')
        }
    }

    function handleResetJobDraft() {
        setJobDraft(buildJobDraft())
        setSelectedJobId(NEW_BATCH_JOB_ID)
    }

    function handleResetBulkEditDraft() {
        setBulkEditDraft(buildBlankBulkEditDraft())
        setSelectedJobId(BULK_EDIT_JOB_ID)
    }

    function handleAddJobFromBuilder() {
        const nextJob = buildJobFromDraft(jobDraft, null, jobs.length)
        if (!nextJob.chart.symbol || !nextJob.chart.timeframe) {
            onLogEvent?.('Batch · Fill in at least symbol and timeframe to add a job.')
            return
        }
        const nextJobs = [...jobs, nextJob]
        persistPatch({ jobs: nextJobs })
        setSelectedJobId(nextJob.id)
        setJobDraft(buildJobDraft())
        onLogEvent?.(`Batch · Added "${nextJob.label || 'job'}" from the builder.`)
        showActionFeedback('success', 'Job added', `"${nextJob.label || 'Job'}" is now queued in the batch.`)
    }

    function handlePatchSelectedJob(patch) {
        if (!selectedJob) {
            return
        }
        const nextJobs = jobs.map((entry, index) => (
            String(entry?.id) === String(selectedJob.id)
                ? normalizeBatchJob({
                    ...entry,
                    ...patch,
                    chart: {
                        ...(entry?.chart || {}),
                        ...(patch?.chart || {}),
                    },
                    backtest: {
                        ...(entry?.backtest || {}),
                        ...(patch?.backtest || {}),
                    },
                    researchPlan: {
                        ...(entry?.researchPlan || {}),
                        ...(patch?.researchPlan || {}),
                    },
                }, index)
                : entry
        ))
        persistPatch({ jobs: nextJobs })
    }

    function handlePatchDraftJob(patch) {
        setJobDraft((current) => {
            const currentJob = buildJobFromDraft(current, null, jobs.length)
            const nextJob = normalizeBatchJob({
                ...currentJob,
                ...patch,
                chart: {
                    ...(currentJob?.chart || {}),
                    ...(patch?.chart || {}),
                },
                backtest: {
                    ...(currentJob?.backtest || {}),
                    ...(patch?.backtest || {}),
                },
                researchPlan: {
                    ...(currentJob?.researchPlan || {}),
                    ...(patch?.researchPlan || {}),
                },
            }, jobs.length)

            return buildJobDraft(nextJob)
        })
    }

    function handlePatchBulkEditDraft(patch) {
        setBulkEditDraft((current) => ({
            ...current,
            ...patch,
            strategy: patch?.strategy ? {
                ...(current?.strategy || {}),
                ...patch.strategy,
                long: {
                    ...((current?.strategy || {}).long || {}),
                    ...((patch.strategy || {}).long || {}),
                },
                short: {
                    ...((current?.strategy || {}).short || {}),
                    ...((patch.strategy || {}).short || {}),
                },
                other: {
                    ...((current?.strategy || {}).other || {}),
                    ...((patch.strategy || {}).other || {}),
                },
            } : (current?.strategy || {}),
            backtest: patch?.backtest ? {
                ...(current?.backtest || {}),
                ...patch.backtest,
            } : (current?.backtest || {}),
        }))
    }

    function syncAuxiliaryStrategies(entries = []) {
        return normalizeBatchStrategyEntries(entries, {})
    }

    function captureStrategyAsAuxiliary(job, onPatch) {
        const safeJob = normalizeBatchJob(job, 0)
        const nextEntries = syncAuxiliaryStrategies([
            ...(safeJob.strategies || []),
            {
                id: buildBatchStrategyEntryId(safeJob.strategies?.length || 0),
                label: buildBatchStrategyEntryLabel(safeJob.strategy, safeJob.strategies?.length || 0),
                priority: (safeJob.strategies?.length || 0) + 1,
                enabled: true,
                symbol: String(safeJob.chart?.symbol || '').trim().toUpperCase(),
                timeframe: String(safeJob.chart?.timeframe || '').trim().toUpperCase(),
                allocationMode: 'fixed_volume',
                allocationValue: null,
                strategy: cloneSerializable(safeJob.strategy, buildBlankStrategy()),
            },
        ])
        onPatch({ strategies: nextEntries })
    }

    function patchAuxiliaryStrategyEntries(job, onPatch, updater) {
        const safeJob = normalizeBatchJob(job, 0)
        const currentEntries = Array.isArray(safeJob.strategies) ? safeJob.strategies : []
        const nextEntries = typeof updater === 'function'
            ? updater(currentEntries)
            : updater
        onPatch({
            strategies: syncAuxiliaryStrategies(nextEntries),
        })
    }

    function applyBulkEditToJob(job, draft, index) {
        const safeJob = normalizeBatchJob(job, index)
        const safeDraft = draft && typeof draft === 'object' ? draft : {}
        const nextJob = JSON.parse(JSON.stringify(safeJob))

        if (String(safeDraft.label || '').trim()) nextJob.label = String(safeDraft.label).trim()
        if (String(safeDraft.symbol || '').trim()) nextJob.chart.symbol = String(safeDraft.symbol).trim().toUpperCase()
        if (String(safeDraft.timeframe || '').trim()) nextJob.chart.timeframe = String(safeDraft.timeframe).trim().toUpperCase()
        if (String(safeDraft.notes || '').trim()) nextJob.notes = String(safeDraft.notes).trim()
        if (safeDraft.bars !== '' && safeDraft.bars !== null && safeDraft.bars !== undefined) {
            const barsValue = Math.max(1, Number(safeDraft.bars) || 1)
            nextJob.chart.bars = barsValue
        }

        const strategyDraft = safeDraft.strategy || {}
        for (const section of ['long', 'short']) {
            const sectionDraft = strategyDraft?.[section] || {}
            for (const field of ['openPrice', 'closePrice', 'openIf', 'closeIf', 'gainPrice', 'lossPrice', 'trailingPrice']) {
                if (String(sectionDraft?.[field] || '').trim()) {
                    nextJob.strategy[section][field] = String(sectionDraft[field]).trim()
                }
            }
        }
        if (String(strategyDraft?.other?.priority || '').trim()) {
            nextJob.strategy.other.priority = String(strategyDraft.other.priority).trim()
        }
        if (strategyDraft?.other?.allowInversion === true || strategyDraft?.other?.allowInversion === false) {
            nextJob.strategy.other.allowInversion = Boolean(strategyDraft.other.allowInversion)
        }

        const backtestDraft = safeDraft.backtest || {}
        for (const key of Object.keys(backtestDraft)) {
            const value = backtestDraft[key]
            if (value === '' || value === null || value === undefined) {
                continue
            }
            nextJob.backtest[key] = value
        }

        return normalizeBatchJob(nextJob, index)
    }

    function handleReplaceAcrossJobs() {
        if (!jobs.length) {
            onLogEvent?.('Batch · Add at least one job before using bulk edit.')
            return
        }
        const nextJobs = jobs.map((job, index) => applyBulkEditToJob(job, bulkEditDraft, index))
        persistPatch({ jobs: nextJobs })
        onLogEvent?.(`Batch · Applied the bulk edit to ${nextJobs.length} job${nextJobs.length > 1 ? 's' : ''}.`)
        showActionFeedback('success', 'Bulk edit applied', `${nextJobs.length} job${nextJobs.length > 1 ? 's were' : ' was'} updated.`)
    }

    function registerStrategyFieldRef(fieldId, node) {
        if (!fieldId) {
            return
        }
        if (node) {
            strategyFieldRefs.current[fieldId] = node
            return
        }
        delete strategyFieldRefs.current[fieldId]
    }

    function updateStrategyFieldSelection(fieldId, event) {
        if (!fieldId || !event?.target) {
            return
        }
        setActiveStrategyFieldId(fieldId)
        setStrategyFieldSelectionMap((current) => ({
            ...current,
            [fieldId]: {
                start: event.target.selectionStart ?? 0,
                end: event.target.selectionEnd ?? 0,
            },
        }))
    }

    function insertTokenIntoActiveField(token) {
        const fieldId = String(activeStrategyFieldId || '').trim()
        const node = strategyFieldRefs.current[fieldId]
        if (!fieldId || !node) {
            onLogEvent?.('Batch · Select a strategy field before inserting a token.')
            return
        }

        const selection = strategyFieldSelectionMap[fieldId] || {}
        const start = Number.isFinite(selection.start) ? selection.start : (node.selectionStart ?? 0)
        const end = Number.isFinite(selection.end) ? selection.end : (node.selectionEnd ?? start)
        const currentValue = String(node.value ?? '')
        const tokenText = `${token}[0]`
        const nextValue = `${currentValue.slice(0, start)}${tokenText}${currentValue.slice(end)}`
        const nextCursor = start + tokenText.length

        if (fieldId.startsWith('draft:')) {
            const path = fieldId.replace('draft:', '').split('.')
            if (path[0] === 'strategy' && path.length === 3) {
                handlePatchDraftJob({
                    strategy: {
                        ...draftJobPreview.strategy,
                        [path[1]]: {
                            ...draftJobPreview.strategy[path[1]],
                            [path[2]]: nextValue,
                        },
                    },
                })
            }
        } else if (fieldId.startsWith('bulk:')) {
            const path = fieldId.replace('bulk:', '').split('.')
            if (path[0] === 'strategy' && path.length === 3) {
                handlePatchBulkEditDraft({
                    strategy: {
                        ...bulkEditDraft.strategy,
                        [path[1]]: {
                            ...(bulkEditDraft.strategy?.[path[1]] || {}),
                            [path[2]]: nextValue,
                        },
                    },
                })
            }
        } else if (fieldId.startsWith('selected:')) {
            const path = fieldId.replace('selected:', '').split('.')
            if (selectedJob && path[0] === 'strategy' && path.length === 3) {
                handlePatchSelectedJob({
                    strategy: {
                        ...selectedJob.strategy,
                        [path[1]]: {
                            ...selectedJob.strategy[path[1]],
                            [path[2]]: nextValue,
                        },
                    },
                })
            }
        }

        window.setTimeout(() => {
            const nextNode = strategyFieldRefs.current[fieldId]
            nextNode?.focus?.()
            nextNode?.setSelectionRange?.(nextCursor, nextCursor)
            setStrategyFieldSelectionMap((current) => ({
                ...current,
                [fieldId]: {
                    start: nextCursor,
                    end: nextCursor,
                },
            }))
        }, 0)
    }

    function handleRemoveJob(jobId) {
        const nextJobs = jobs.filter((entry) => entry?.id !== jobId)
        persistPatch({ jobs: nextJobs })
        if (String(selectedJobId) === String(jobId)) {
            setSelectedJobId(NEW_BATCH_JOB_ID)
        }
        onLogEvent?.('Batch · Removed a programmed job.')
    }

    function handleDuplicateJob(job) {
        if (!job) {
            return
        }
        const duplicated = normalizeBatchJob({
            ...job,
            id: '',
            label: `${job.label || 'Job'} copy`,
        }, jobs.length)
        const sourceIndex = jobs.findIndex((entry) => String(entry?.id) === String(job.id))
        const insertAt = sourceIndex >= 0 ? sourceIndex + 1 : jobs.length
        const nextJobs = [...jobs]
        nextJobs.splice(insertAt, 0, duplicated)
        persistPatch({ jobs: nextJobs })
        setSelectedJobId(duplicated.id)
        onLogEvent?.(`Batch · Duplicated "${job.label || 'job'}".`)
    }

    function handleMoveJob(jobId, direction) {
        const currentIndex = jobs.findIndex((entry) => String(entry?.id) === String(jobId))
        if (currentIndex < 0) {
            return
        }
        const targetIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
        const nextJobs = moveArrayItem(jobs, currentIndex, targetIndex)
        persistPatch({ jobs: nextJobs })
        onLogEvent?.(`Batch · Moved job ${direction === 'up' ? 'up' : 'down'} in the queue.`)
    }

    async function handleCopyJsonToClipboard(value, label) {
        try {
            await navigator.clipboard.writeText(value)
            onLogEvent?.(`Batch · Copied ${label} JSON to clipboard.`)
        } catch (error) {
            onLogEvent?.(`Batch · Could not copy ${label} JSON: ${error?.message || 'clipboard error'}`)
        }
    }

    function handleDownloadJson(value, filename, label) {
        try {
            const blob = new Blob([value], { type: 'application/json;charset=utf-8' })
            const url = window.URL.createObjectURL(blob)
            const link = document.createElement('a')
            link.href = url
            link.download = filename
            document.body.appendChild(link)
            link.click()
            document.body.removeChild(link)
            window.URL.revokeObjectURL(url)
            onLogEvent?.(`Batch · Exported ${label} JSON.`)
        } catch (error) {
            onLogEvent?.(`Batch · Could not export ${label} JSON: ${error?.message || 'download error'}`)
        }
    }

    async function handleStartBatch() {
        if (!authToken || !jobs.length) {
            return
        }

        try {
            const requestJobs = buildBatchRequestJobs(jobs, options, batchFeatures)
            if (!requestJobs.length) {
                onLogEvent?.('Batch · No executable jobs were found. Add at least one job with symbol, timeframe, and active strategy conditions.')
                showActionFeedback('warning', 'No executable jobs', 'The current batch only has blank or disabled jobs. Fill in symbol, timeframe, and real strategy conditions before running.')
                return
            }
            setPendingAction('startBatch')
            showActionFeedback('running', 'Starting batch', `Sending ${requestJobs.length} job${requestJobs.length > 1 ? 's' : ''} to the backend.`)
            onSharedConsoleJobChange?.('batch', {
                status: 'running',
                label: 'Running batch',
                startedAt: new Date().toISOString(),
                actor: 'batch',
            })
            const response = await fetch(buildApiUrl('/workspace/research-batches'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: String(batchLabel || '').trim() || 'Batch run',
                    jobs: requestJobs,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to start backend batch.'))
            }
            setActiveBatchId(String(payload?.batch?.id || ''))
            setSelectedRuntimeJobId('')
            await refreshRuntime()
            onLogEvent?.(`Batch · Started backend batch "${payload?.batch?.label || 'Batch run'}".`)
            showActionFeedback('success', 'Batch started', `Backend batch "${payload?.batch?.label || 'Batch run'}" is now running.`)
        } catch (error) {
            onSharedConsoleJobChange?.('batch', null)
            onLogEvent?.(`Batch · Could not start backend batch: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not start batch', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    async function handleSaveTemplate() {
        if (!authToken || !jobs.length) {
            onLogEvent?.('Batch · Import at least one job before saving a template.')
            showActionFeedback('warning', 'Nothing to save', 'Add at least one job before saving this batch.')
            return
        }
        try {
            const persistedOptions = buildDefaultBatchOptions(options)
            const requestJobs = buildBatchRequestJobs(jobs, persistedOptions, batchFeatures)
            setPendingAction('saveTemplate')
            showActionFeedback('running', 'Saving batch', 'Persisting the current batch template to the backend.')
            const response = await fetch(buildApiUrl('/workspace/research-campaigns'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: String(templateLabel || '').trim() || 'Batch template',
                    description: String(templateDescription || '').trim(),
                    jobs: requestJobs,
                    batch_jobs: jobs,
                    shared_features: batchFeatures,
                    options: persistedOptions,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to save batch template.'))
            }
            await refreshRuntime()
            onLogEvent?.(`Batch · Saved template "${payload?.campaign?.label || 'Batch template'}".`)
            showActionFeedback('success', 'Batch saved', `"${payload?.campaign?.label || 'Batch template'}" is now available in Manager.`)
        } catch (error) {
            onLogEvent?.(`Batch · Could not save template: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not save batch', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    async function handleUpdateTemplate(campaign, overrides = {}) {
        if (!authToken || !campaign?.id) {
            return
        }

        const nextLabel = String(overrides?.label ?? campaign?.label ?? '').trim() || 'Batch template'
        const nextDescription = String(overrides?.description ?? campaign?.description ?? '').trim()
        const preserveExistingRequest = overrides?.preserveExistingRequest === true
        const hasExplicitRequestOverride = (
            Array.isArray(overrides?.jobs)
            || Array.isArray(overrides?.batchJobs)
            || Array.isArray(overrides?.sharedFeatures)
            || overrides?.options !== undefined
        )
        const nextJobs = Array.isArray(overrides?.jobs)
            ? overrides.jobs
            : buildBatchRequestJobs(jobs, options, batchFeatures)
        const nextBatchJobs = Array.isArray(overrides?.batchJobs) ? overrides.batchJobs : jobs
        const nextSharedFeatures = Array.isArray(overrides?.sharedFeatures) ? overrides.sharedFeatures : batchFeatures
        const nextOptions = buildDefaultBatchOptions(overrides?.options || options)
        const requestPatch = (!preserveExistingRequest || hasExplicitRequestOverride)
            ? {
                jobs: nextJobs,
                batch_jobs: nextBatchJobs,
                shared_features: nextSharedFeatures,
                options: nextOptions,
            }
            : {}

        try {
            setPendingAction(`updateTemplate:${campaign.id}`)
            showActionFeedback('running', 'Updating saved batch', `Saving changes to "${nextLabel}".`)
            const response = await fetch(buildApiUrl(`/workspace/research-campaigns/${campaign.id}?workspace_id=default`), {
                method: 'PATCH',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: nextLabel,
                    description: nextDescription,
                    ...requestPatch,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to update saved batch.'))
            }
            await refreshRuntime()
            setTemplateLabel(nextLabel)
            setTemplateDescription(nextDescription)
            onLogEvent?.(`Batch · Updated saved batch "${payload?.campaign?.label || nextLabel}".`)
            showActionFeedback('success', 'Saved batch updated', `"${payload?.campaign?.label || nextLabel}" is up to date.`)
        } catch (error) {
            onLogEvent?.(`Batch · Could not update saved batch: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not update saved batch', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    function handleLoadTemplate(campaign) {
        const nextEditorState = buildEditorStateFromCampaign(campaign)
        const importedJobs = nextEditorState.jobs
        persistPatch(nextEditorState)
        setManagerSelectedBatchId(String(campaign?.id || ''))
        setJobDraft(buildJobDraft())
        setJobDetailTab('overview')
        setJobBacktestTab('capital')
        setBatchLabel(String(campaign?.label || 'Batch run'))
        setTemplateLabel(String(campaign?.label || 'Batch template'))
        setTemplateDescription(String(campaign?.description || ''))
        setPendingLoadedJobId(String(importedJobs[0]?.id || ''))
        onLogEvent?.(`Batch · Loaded template "${campaign?.label || 'Batch template'}" into the panel.`)
        showActionFeedback('success', 'Saved batch loaded', `"${campaign?.label || 'Batch template'}" was loaded into the editor.`)
    }

    async function handleLoadTemplateById(campaign) {
        if (!campaign?.id) {
            return
        }

        try {
            setPendingAction(`loadTemplate:${campaign.id}`)
            showActionFeedback('running', 'Loading saved batch', `Fetching "${campaign?.label || 'Batch template'}" from the backend.`)
            const resolvedCampaign = await loadRemoteCampaignDetail(campaign.id)
            if (!resolvedCampaign) {
                throw new Error('Failed to load saved batch.')
            }
            handleLoadTemplate(resolvedCampaign)
            setActiveTab('jobs')
        } catch (error) {
            onLogEvent?.(`Batch · Could not load saved batch: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not load saved batch', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    async function handlePasteBatchJsonFromClipboard() {
        try {
            const text = await navigator.clipboard.readText()
            return text
        } catch (error) {
            onLogEvent?.(`Batch · Could not read batch JSON from clipboard: ${error?.message || 'clipboard error'}`)
            return ''
        }
    }

    async function handleCopySavedBatchJson(campaign) {
        if (!campaign?.id) {
            return
        }
        let resolvedCampaign = campaign
        if (campaign?.request_loaded === false) {
            try {
                resolvedCampaign = await loadRemoteCampaignDetail(campaign.id) || campaign
            } catch (error) {
                onLogEvent?.(`Batch · Could not load saved batch JSON: ${error?.message || 'unknown error'}`)
                showActionFeedback('error', 'Could not copy saved batch', error?.message || 'Unknown error.')
                return
            }
        }
        const persistedBatchJobs = Array.isArray(resolvedCampaign?.request?.batch_jobs) ? resolvedCampaign.request.batch_jobs : []
        const campaignJobs = persistedBatchJobs.length > 0
            ? persistedBatchJobs
            : Array.isArray(resolvedCampaign?.request?.jobs) ? resolvedCampaign.request.jobs : []
        const importedJobs = campaignJobs.map((entry, index) => ({
            id: entry?.request?.id || `saved-batch-job-${resolvedCampaign?.id || 'template'}-${index + 1}`,
            label: entry?.run_label || entry?.request?.label || `Job ${index + 1}`,
            notes: entry?.run_notes || '',
            chart: entry?.request?.chart || {},
            strategy: entry?.request?.strategy || {},
            strategies: Array.isArray(entry?.request?.strategies) ? entry.request.strategies : [],
            backtest: entry?.request?.backtest || {},
            researchPlan: entry?.request?.researchPlan || {},
        }))
        void handleCopyJsonToClipboard(JSON.stringify({
            shared_features: Array.isArray(resolvedCampaign?.request?.shared_features) ? resolvedCampaign.request.shared_features : [],
            options: buildDefaultBatchOptions(resolvedCampaign?.request?.options || {}),
            jobs: importedJobs,
        }, null, 2), 'saved batch')
    }

    async function handleRunTemplate(campaignId, campaign = null) {
        if (!authToken || !campaignId) {
            return
        }
        try {
            setPendingAction(`runTemplate:${campaignId}`)
            showActionFeedback('running', 'Launching saved batch', 'Sending the saved batch template to the backend.')
            onSharedConsoleJobChange?.('batch', {
                status: 'running',
                label: 'Running template batch',
                startedAt: new Date().toISOString(),
                actor: 'batch',
            })
            let resolvedCampaign = campaign && String(campaign?.id || '') === String(campaignId) ? campaign : null

            if (!resolvedCampaign || resolvedCampaign?.request_loaded === false) {
                resolvedCampaign = await loadRemoteCampaignDetail(campaignId)
            }

            if (!resolvedCampaign) {
                throw new Error('Failed to load saved batch.')
            }

            const requestJobs = buildExecutableJobsFromCampaign(resolvedCampaign)
            if (!requestJobs.length) {
                throw new Error('Saved batch has no executable jobs to launch.')
            }

            const response = await fetch(buildApiUrl('/workspace/research-batches'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: String(resolvedCampaign?.label || 'Batch template').trim() || 'Batch template',
                    jobs: requestJobs,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to launch batch template.'))
            }
            setActiveBatchId(String(payload?.batch?.id || ''))
            setSelectedRuntimeJobId('')
            await refreshRuntime()
            onLogEvent?.(`Batch · Launched template "${payload?.batch?.label || 'Batch template'}".`)
            showActionFeedback(
                'success',
                'Saved batch started',
                `Backend batch "${payload?.batch?.label || 'Batch template'}" is now running with saved research mode: ${describeResearchSelection(resolvedCampaign?.request?.options || {})}.`,
            )
        } catch (error) {
            onSharedConsoleJobChange?.('batch', null)
            onLogEvent?.(`Batch · Could not launch template: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not launch saved batch', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    async function handleDeleteTemplate(campaignId) {
        if (!authToken || !campaignId) {
            return
        }
        try {
            setPendingAction(`deleteTemplate:${campaignId}`)
            showActionFeedback('running', 'Deleting saved batch', 'Removing the saved batch from the backend.')
            const response = await fetch(buildApiUrl(`/workspace/research-campaigns/${campaignId}?workspace_id=default`), {
                method: 'DELETE',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to delete batch template.'))
            }
            await refreshRuntime()
            onLogEvent?.('Batch · Deleted a batch template.')
            showActionFeedback('success', 'Saved batch deleted', 'The batch template was removed.')
        } catch (error) {
            onLogEvent?.(`Batch · Could not delete template: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not delete saved batch', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    async function handleCreateFollowUpBatch() {
        if (!authToken) {
            return
        }
        const latestCompletedBatch = remoteBatches.find((batch) => String(batch?.status || '').toLowerCase() === 'completed')
        const resultJobs = Array.isArray(latestCompletedBatch?.result?.jobs) ? latestCompletedBatch.result.jobs : []
        const completedJobs = resultJobs
            .map((item) => remoteJobs.find((job) => String(job?.id) === String(item?.job_id)))
            .filter((job) => String(job?.status || '').toLowerCase() === 'completed' && job?.request?.strategy && job?.request?.chart)
            .map((job) => ({
                job,
                score: getPipelineStat(job, 'score_out_of_ten') ?? -Infinity,
            }))
            .sort((left, right) => right.score - left.score)

        if (!completedJobs.length) {
            showActionFeedback('warning', 'No completed batch to clone', 'Run at least one successful backend batch before generating a follow-up batch.')
            return
        }

        const seeds = completedJobs.slice(0, 3).map((entry) => entry.job)
        const variants = [
            { seedIndex: 0, suffix: 'Control', deltas: {} },
            { seedIndex: 0, suffix: 'Tighter Trigger', deltas: { longOpenDelta: -2, shortOpenDelta: 2 } },
            { seedIndex: 0, suffix: 'Faster Exit', deltas: { longCloseDelta: -3, shortCloseDelta: 3 } },
            { seedIndex: 0, suffix: 'Smaller Stop', deltas: { lossDelta: -1 } },
            { seedIndex: 1, suffix: 'Control', deltas: {} },
            { seedIndex: 1, suffix: 'Wider Target', deltas: { gainDelta: 2 } },
            { seedIndex: 1, suffix: 'Tighter Trigger', deltas: { longOpenDelta: -1, shortOpenDelta: 1 } },
            { seedIndex: 2, suffix: 'Control', deltas: {} },
            { seedIndex: 2, suffix: 'Faster Exit', deltas: { longCloseDelta: -2, shortCloseDelta: 2 } },
            { seedIndex: 2, suffix: 'Trail Probe', deltas: { trailingMode: 'enable' } },
        ]

        const dedupedJobs = []
        const seenSignatures = new Set()
        const portfolioMutation = buildDefaultPortfolioMutationOptions(options?.portfolioMutation)
        variants.forEach((variant, index) => {
            const seed = seeds[Math.min(variant.seedIndex, seeds.length - 1)] || seeds[0]
            const request = seed?.request || {}
            const baseLabel = String(request?.label || seed?.run_label || `Follow-up ${index + 1}`).trim()
            const mutatedPortfolio = applyMutationVariantToJob({
                strategy: request?.strategy || {},
                strategies: Array.isArray(request?.strategies) ? request.strategies : [],
                chart: request?.chart || {},
                backtest: request?.backtest || {},
                researchPlan: request?.researchPlan || {},
            }, portfolioMutation, variant.deltas)
            const mutationMeta = {
                mutationMode: mutatedPortfolio.mutationMode,
                mutationTargetStrategyId: mutatedPortfolio.mutationTargetStrategyId,
                mutationLabel: variant.suffix,
                parentBatchId: latestCompletedBatch?.id ?? null,
                parentJobId: seed?.id ?? null,
                preservedAuxiliaries: mutatedPortfolio.preservedAuxiliaries,
            }
            const nextJob = normalizeBatchJob({
                id: `${String(request?.id || `follow-up-${index + 1}`)}-v${index + 1}`,
                label: `${baseLabel} · ${variant.suffix}`,
                notes: `Follow-up variation generated from top batch result "${baseLabel}" using ${mutatedPortfolio.mutationMode}${mutatedPortfolio.preservedAuxiliaries ? ' with preserved auxiliaries' : ''}.`,
                chart: request?.chart || {},
                strategy: mutatedPortfolio.strategy,
                strategies: mutatedPortfolio.strategies,
                backtest: request?.backtest || buildDefaultBacktest(),
                researchPlan: {
                    ...(request?.researchPlan || {}),
                    mutation: mutationMeta,
                },
            }, dedupedJobs.length)
            const signature = buildPortfolioJobSignature(nextJob)
            if (seenSignatures.has(signature)) {
                return
            }
            seenSignatures.add(signature)
            dedupedJobs.push(nextJob)
        })
        const nextJobs = dedupedJobs

        if (!nextJobs.length) {
            showActionFeedback('warning', 'No unique follow-up jobs', 'The generated follow-up variants collapsed into duplicate portfolio signatures.')
            return
        }

        const followUpLabel = `${String(latestCompletedBatch?.label || 'Batch run').trim()} · Follow-up`
        const followUpDescription = `Generated from the strongest completed presets in batch "${String(latestCompletedBatch?.label || 'Batch run').trim()}".`
        const nextOptions = buildDefaultBatchOptions(options)

        try {
            setPendingAction('createFollowUpBatch')
            showActionFeedback('running', 'Creating follow-up batch', `Saving a new follow-up draft with ${portfolioMutation.mode}.`)
            const response = await fetch(buildApiUrl('/workspace/research-campaigns'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: followUpLabel,
                    description: followUpDescription,
                    jobs: buildBatchRequestJobs(nextJobs, nextOptions, batchFeatures),
                    batch_jobs: nextJobs,
                    shared_features: batchFeatures,
                    options: nextOptions,
                }),
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to create follow-up batch.'))
            }
            await refreshRuntime()
            setManagerSelectedBatchId(String(payload?.campaign?.id || ''))
            handleLoadTemplate(payload?.campaign || {
                id: payload?.campaign?.id,
                label: followUpLabel,
                description: followUpDescription,
                request: {
                    jobs: buildBatchRequestJobs(nextJobs, nextOptions, batchFeatures),
                    batch_jobs: nextJobs,
                    shared_features: batchFeatures,
                    options: nextOptions,
                },
            })
            setSelectedJobId(nextJobs[0]?.id || NEW_BATCH_JOB_ID)
            setActiveTab('jobs')
            showActionFeedback('success', 'Follow-up batch created', `"${payload?.campaign?.label || followUpLabel}" was saved with ${portfolioMutation.mode}.`)
            onLogEvent?.(`Batch · Created follow-up draft "${payload?.campaign?.label || followUpLabel}" using ${portfolioMutation.mode} and portfolio deduplication.`)
        } catch (error) {
            onLogEvent?.(`Batch · Could not create follow-up batch: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not create follow-up batch', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    async function handleCreateFailedRerunBatch() {
        if (!authToken || !latestPipelineBatch?.id) {
            return
        }

        const latestBatchResultJobs = Array.isArray(latestPipelineBatch?.result?.jobs)
            ? latestPipelineBatch.result.jobs
            : []
        const failedBackendJobs = latestBatchResultJobs
            .filter((item) => String(item?.status || '').toLowerCase() === 'failed')
            .map((item) => remoteJobs.find((job) => String(job?.id) === String(item?.job_id)))
            .filter((job) => job?.request?.strategy && job?.request?.chart)
            .map((job, index) => extractRerunCandidateJob(job, index))

        if (!failedBackendJobs.length) {
            showActionFeedback('warning', 'No failed jobs to rerun', 'The latest backend batch does not have loadable failed jobs.')
            return
        }

        const rerunJobs = failedBackendJobs.map((entry, index) => normalizeBatchJob({
            ...entry.normalizedJob,
            id: `${String(entry.normalizedJob?.id || `rerun-${index + 1}`)}-rerun-${index + 1}`,
            label: `${String(entry.normalizedJob?.label || `Failed job ${index + 1}`).trim()} · Retry`,
            notes: `Retry generated from failed jobs in batch "${String(latestPipelineBatch?.label || 'Batch run').trim()}".`,
        }, index))
        const rerunLabel = `${String(latestPipelineBatch?.label || 'Batch run').trim()} · Retry Failed Jobs`
        const rerunDescription = `Saved retry draft generated from failed jobs in batch "${String(latestPipelineBatch?.label || 'Batch run').trim()}".`
        const nextOptions = buildDefaultBatchOptions(options)

        try {
            setPendingAction('createFailedRerunBatch')
            showActionFeedback('running', 'Creating failed-job rerun', 'Saving and launching a retry batch with only the failed jobs from the latest backend batch.')
            const createResponse = await fetch(buildApiUrl('/workspace/research-campaigns'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: rerunLabel,
                    description: rerunDescription,
                    jobs: buildBatchRequestJobs(rerunJobs, nextOptions, batchFeatures),
                    batch_jobs: rerunJobs,
                    shared_features: batchFeatures,
                    options: nextOptions,
                }),
            })
            const createPayload = await readJsonResponse(createResponse)
            if (!createResponse.ok || createPayload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(createPayload, 'Failed to create failed-job rerun.'))
            }

            const createdCampaignId = String(createPayload?.campaign?.id || '')
            if (!createdCampaignId) {
                throw new Error('The retry draft was created without a campaign id.')
            }

            const launchResponse = await fetch(buildApiUrl(`/workspace/research-campaigns/${createdCampaignId}/launch?workspace_id=default`), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const launchPayload = await readJsonResponse(launchResponse)
            if (!launchResponse.ok || launchPayload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(launchPayload, 'Failed to launch failed-job rerun.'))
            }

            setActiveBatchId(String(launchPayload?.batch?.id || ''))
            setSelectedRuntimeJobId('')
            onSharedConsoleJobChange?.('batch', {
                status: 'running',
                label: launchPayload?.batch?.label || rerunLabel,
                startedAt: new Date().toISOString(),
                actor: 'batch',
            })
            await refreshRuntime()
            setManagerSelectedBatchId(createdCampaignId)
            handleLoadTemplate(createPayload?.campaign || {
                id: createPayload?.campaign?.id,
                label: rerunLabel,
                description: rerunDescription,
                request: {
                    jobs: buildBatchRequestJobs(rerunJobs, nextOptions, batchFeatures),
                    batch_jobs: rerunJobs,
                    shared_features: batchFeatures,
                    options: nextOptions,
                },
            })
            setSelectedJobId(rerunJobs[0]?.id || NEW_BATCH_JOB_ID)
            setActiveTab('jobs')
            showActionFeedback('success', 'Failed-job rerun started', `"${launchPayload?.batch?.label || rerunLabel}" was created from the failed jobs and is now running.`)
            onLogEvent?.(`Batch · Created and launched failed-job rerun "${launchPayload?.batch?.label || rerunLabel}" from the latest backend batch.`)
        } catch (error) {
            onLogEvent?.(`Batch · Could not create failed-job rerun: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not create failed-job rerun', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    async function handleCancelBatch() {
        if (!authToken || !latestPipelineBatch?.id) {
            return
        }
        try {
            setPendingAction('cancelBatch')
            showActionFeedback('running', 'Cancelling batch', 'Sending a cancellation request to the backend.')
            const response = await fetch(buildApiUrl(`/workspace/research-batches/${latestPipelineBatch.id}/cancel?workspace_id=default`), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to cancel backend batch.'))
            }
            setActiveBatchId(String(payload?.batch?.id || latestPipelineBatch?.id || ''))
            if (payload?.batch) {
                setRemoteBatches((current) => mergeRemoteBatchRuntimeEntries(current, payload.batch))
            } else {
                await refreshRuntime()
            }
            onLogEvent?.('Batch · Cancellation requested for the active batch.')
            showActionFeedback('warning', 'Cancellation requested', 'The backend received the cancel request and is stopping the active job.')
        } catch (error) {
            onLogEvent?.(`Batch · Could not cancel backend batch: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not cancel batch', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    async function handleLoadResultsFromJob(job) {
        let targetJob = job
        let hydrated = buildHydratedBacktestPayloadFromPipelineJob(targetJob)
        if (!hydrated && job?.id) {
            try {
                targetJob = await loadRemoteJobDetail(job.id) || job
                hydrated = buildHydratedBacktestPayloadFromPipelineJob(targetJob)
            } catch (error) {
                onLogEvent?.(`Batch · Could not load the full job payload: ${error?.message || 'unknown error'}`)
            }
        }
        if (!hydrated) {
            onLogEvent?.('Batch · This backend job does not have a loadable report payload yet.')
            return
        }
        onHydrateBacktestResult?.(hydrated)
        onOpenResults?.()
        onLogEvent?.(`Batch · Loaded "${targetJob?.run_label || targetJob?.phase_label || targetJob?.job_type || 'pipeline job'}" into Results.`)
    }

    async function handleSaveJobAsStrategy(job) {
        if (!authToken) {
            onLogEvent?.('Batch · Sign in before saving a strategy to the library.')
            showActionFeedback('warning', 'Authentication required', 'Sign in before saving this strategy to the library.')
            return
        }

        let targetJob = job
        let payload = buildStrategyLibraryPayloadFromPipelineJob(targetJob)
        if (!payload && job?.id) {
            try {
                targetJob = await loadRemoteJobDetail(job.id) || job
                payload = buildStrategyLibraryPayloadFromPipelineJob(targetJob)
            } catch (error) {
                onLogEvent?.(`Batch · Could not load the full job payload: ${error?.message || 'unknown error'}`)
            }
        }
        if (!payload) {
            onLogEvent?.('Batch · This backend job does not have a loadable strategy payload yet.')
            showActionFeedback('warning', 'Strategy not ready', 'This runtime job does not expose a strategy payload that can be saved yet.')
            return
        }

        const pendingKey = `saveJobStrategy:${String(targetJob?.id || payload.label)}`
        try {
            setPendingAction(pendingKey)
            showActionFeedback('running', 'Saving strategy to library', `Persisting "${payload.label}" in the Strategy library.`)
            const response = await fetch(buildApiUrl('/workspace/strategy-benchmarks'), {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label: payload.label,
                    notes: payload.notes,
                    source: payload.source,
                    side: payload.side,
                    strategy: payload.strategy,
                    strategies: payload.strategies,
                }),
            })
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to save strategy to the library.'))
            }
            onLogEvent?.(`Batch · Saved "${payload.label}" to the Strategy library.`)
            showActionFeedback('success', 'Strategy saved', `"${payload.label}" is now available in the Strategy library.`)
        } catch (error) {
            onLogEvent?.(`Batch · Could not save strategy to library: ${error?.message || 'unknown error'}`)
            showActionFeedback('error', 'Could not save strategy', error?.message || 'Unknown error.')
        } finally {
            setPendingAction('')
        }
    }

    function handleResetRuntimeView() {
        const defaults = buildDefaultRuntimeViewOptions()
        applyRuntimeViewPatch(defaults)
    }

    function handleOpenResearchArchive(job) {
        if (!job?.run_id) {
            onLogEvent?.('Batch · This backend job does not have an archived research run yet.')
            return
        }
        onOpenResearchRun?.(job.run_id)
        onLogEvent?.('Batch · Opened the archived batch report in Research.')
    }

    const latestProgress = Math.max(0, Math.min(100, Math.round(Number(latestPipelineBatch?.progress || 0) * 100)))
    const latestCompletedJobs = Math.max(0, Number(latestPipelineBatch?.completed_jobs || 0))
    const latestFailedJobs = Math.max(0, Number(latestPipelineBatch?.failed_jobs || 0))
    const latestCancelledJobs = Math.max(0, Number(latestPipelineBatch?.cancelled_jobs || 0))
    const latestTotalJobs = Math.max(0, Number(latestPipelineBatch?.total_jobs || 0))
    const latestProcessedJobs = Math.min(latestTotalJobs, latestCompletedJobs + latestFailedJobs + latestCancelledJobs)
    const draftResearchPreviewState = useMemo(
        () => buildSafeResearchPayloadFromProfile(draftJobPreview, deferredOptions, deferredJobs, deferredBatchFeatures),
        [deferredBatchFeatures, deferredJobs, deferredOptions, draftJobPreview],
    )
    const draftResearchPreview = draftResearchPreviewState.payload
    const draftMutationPreview = useMemo(
        () => buildResearchMutationPreview(draftJobPreview?.researchPlan || {}),
        [draftJobPreview],
    )
    const draftLineagePreview = useMemo(
        () => buildPortfolioLineagePreview(draftJobPreview?.researchPlan || {}),
        [draftJobPreview],
    )
    const draftSignaturePreview = useMemo(
        () => buildPortfolioSignaturePreview(draftJobPreview),
        [draftJobPreview],
    )
    const draftParentJob = useMemo(
        () => resolveMutationParentJob(draftJobPreview, remoteJobs, jobs),
        [draftJobPreview, jobs, remoteJobs],
    )
    const draftMutationDeltaPreview = useMemo(
        () => buildMutationDeltaPreview(draftJobPreview, draftParentJob),
        [draftJobPreview, draftParentJob],
    )
    const draftComparisonPresets = useMemo(
        () => buildComparisonPresets(draftJobPreview, deferredJobs, deferredOptions),
        [deferredJobs, deferredOptions, draftJobPreview],
    )
    const draftComparisonSourceLabel = draftComparisonPresets.source === 'embedded'
        ? 'Embedded in imported JSON'
        : draftComparisonPresets.source === 'selected_jobs'
            ? 'Selected manually from imported jobs'
            : draftComparisonPresets.source === 'batch_jobs'
                ? 'Derived from other imported jobs'
                : 'No comparison presets available'
    const selectedJobResearchPreviewState = useMemo(
        () => selectedJob
            ? buildSafeResearchPayloadFromProfile(selectedJob, deferredOptions, deferredJobs, deferredBatchFeatures)
            : { payload: { kind: 'none' }, error: '' },
        [deferredBatchFeatures, deferredJobs, deferredOptions, selectedJob],
    )
    const selectedJobResearchPreview = selectedJobResearchPreviewState.payload
    const selectedJobMutationPreview = useMemo(
        () => buildResearchMutationPreview(selectedJob?.researchPlan || {}),
        [selectedJob],
    )
    const selectedJobLineagePreview = useMemo(
        () => buildPortfolioLineagePreview(selectedJob?.researchPlan || {}),
        [selectedJob],
    )
    const selectedJobSignaturePreview = useMemo(
        () => selectedJob ? buildPortfolioSignaturePreview(selectedJob) : null,
        [selectedJob],
    )
    const selectedJobParent = useMemo(
        () => selectedJob ? resolveMutationParentJob(selectedJob, remoteJobs, jobs) : null,
        [jobs, remoteJobs, selectedJob],
    )
    const selectedJobMutationDeltaPreview = useMemo(
        () => selectedJob ? buildMutationDeltaPreview(selectedJob, selectedJobParent) : null,
        [selectedJob, selectedJobParent],
    )
    const selectedJobComparisonPresets = useMemo(
        () => selectedJob ? buildComparisonPresets(selectedJob, deferredJobs, deferredOptions) : { source: 'none', presets: [] },
        [deferredJobs, deferredOptions, selectedJob],
    )
    const selectedJobComparisonCandidates = selectedJob
        ? jobs.filter((entry) => String(entry?.id || '') !== String(selectedJob.id))
        : []
    const selectedComparisonMap = getComparisonPresetSelectionMap(options)
    const selectedJobComparisonSelection = selectedJob && Array.isArray(selectedComparisonMap?.[selectedJob.id])
        ? selectedComparisonMap[selectedJob.id].map((value) => String(value || '').trim()).filter(Boolean)
        : []
    const selectedJobComparisonSourceLabel = selectedJobComparisonPresets.source === 'embedded'
        ? 'Embedded in imported JSON'
        : selectedJobComparisonPresets.source === 'selected_jobs'
            ? 'Selected manually from imported jobs'
        : selectedJobComparisonPresets.source === 'batch_jobs'
            ? 'Derived from other imported jobs'
            : 'No comparison presets available'
    const selectedRuntimeProgress = Math.max(0, Math.min(100, Math.round(Number(selectedRuntimeJob?.progress || 0) * 100)))
    const selectedJobIndex = selectedJob ? jobs.findIndex((entry) => String(entry?.id) === String(selectedJob.id)) : -1
    const selectedJobExportJson = selectedJob ? JSON.stringify(selectedJob, null, 2) : ''
    const selectedResearchStudies = {
        ...DEFAULT_RESEARCH_STUDIES,
        ...(options?.researchStudies && typeof options.researchStudies === 'object' ? options.researchStudies : {}),
    }
    const portfolioMutationOptions = buildDefaultPortfolioMutationOptions(options?.portfolioMutation)
    const availablePortfolioMutationTargets = useMemo(() => {
        const registry = new Map()
        for (const job of jobs) {
            const normalizedJob = normalizeBatchJob(job, 0)
            const entries = normalizeBatchStrategyEntries(normalizedJob.strategies, normalizedJob.strategy)
            for (const entry of entries) {
                const entryId = String(entry?.id || '').trim()
                if (!entryId || entryId === 'primary' || registry.has(entryId)) {
                    continue
                }
                registry.set(entryId, {
                    id: entryId,
                    label: String(entry?.label || entryId).trim() || entryId,
                })
            }
        }
        return Array.from(registry.values()).sort((left, right) => left.label.localeCompare(right.label))
    }, [jobs])
    const selectedPortfolioMutationTarget = availablePortfolioMutationTargets.find((entry) => entry.id === portfolioMutationOptions.targetStrategyId) || null
    const effectivePortfolioMutationSummary = portfolioMutationOptions.mode === 'mutate_selected_auxiliary'
        ? selectedPortfolioMutationTarget
            ? `${formatPortfolioMutationModeLabel(portfolioMutationOptions.mode)} · target ${selectedPortfolioMutationTarget.label}`
            : `${formatPortfolioMutationModeLabel(portfolioMutationOptions.mode)} · no auxiliary target available`
        : formatPortfolioMutationModeLabel(portfolioMutationOptions.mode)
    const hasAnyResearchStudySelected = Object.values(selectedResearchStudies).some(Boolean)
    const lastRuntimeSyncLabel = runtimeStatus.lastLoadedAt ? new Date(runtimeStatus.lastLoadedAt).toLocaleTimeString() : 'never'
    const currentRequestBuild = useMemo(
        () => buildSafeBatchRequestJobs(deferredJobs, deferredOptions, deferredBatchFeatures),
        [deferredBatchFeatures, deferredJobs, deferredOptions],
    )
    const currentRequestJobs = currentRequestBuild.jobs
    const batchRequestBuildError = currentRequestBuild.error
    const batchRuntimeRankings = useMemo(() => {
        const completedJobs = filteredPipelineJobs.filter((job) => String(job?.status || '').toLowerCase() === 'completed' && job?.result?.pipeline?.stats)
        const rankBy = (key, direction = 'desc') => completedJobs
            .map((job) => ({
                job,
                value: getPipelineStat(job, key),
            }))
            .filter((entry) => entry.value !== null)
            .sort((left, right) => direction === 'asc' ? left.value - right.value : right.value - left.value)
            .slice(0, 3)

        return {
            topScore: rankBy('score_out_of_ten', 'desc'),
            bestNetPnl: rankBy('net_pnl', 'desc'),
            bestWinRate: rankBy('win_rate', 'desc'),
            worstDrawdown: rankBy('max_drawdown', 'asc'),
            completedCount: completedJobs.length,
        }
    }, [filteredPipelineJobs])
    const highlightedRuntimeMetrics = useMemo(() => {
        const highlights = new Set()
        const addMetricHighlights = (entries, metricKeys) => {
            for (const entry of entries || []) {
                for (const metricKey of metricKeys) {
                    highlights.add(`${String(entry?.job?.id || '')}:${metricKey}`)
                }
            }
        }
        addMetricHighlights(batchRuntimeRankings.topScore, ['score_out_of_ten'])
        addMetricHighlights(batchRuntimeRankings.bestNetPnl, ['net_pnl'])
        addMetricHighlights(batchRuntimeRankings.bestWinRate, ['win_rate'])
        addMetricHighlights(batchRuntimeRankings.worstDrawdown, ['max_drawdown', 'max_drawdown_pct'])
        return highlights
    }, [batchRuntimeRankings])
    const latestBatchStatus = String(latestPipelineBatch?.status || '').toLowerCase()
    const isLatestBatchRunning = ['queued', 'running'].includes(latestBatchStatus)
    const processingBatch = latestPipelineBatch
    const latestBatchDurationSeconds = getElapsedSeconds(latestPipelineBatch?.started_at, latestPipelineBatch?.finished_at)
    const isStartPending = pendingAction === 'startBatch'
    const isCreateFollowUpPending = pendingAction === 'createFollowUpBatch'
    const isCreateFailedRerunPending = pendingAction === 'createFailedRerunBatch'
    const isCancelPending = pendingAction === 'cancelBatch' || (isLatestBatchRunning && Boolean(latestPipelineBatch?.cancel_requested))
    const isRunButtonBusy = isStartPending || isLatestBatchRunning
    const canCancelLatestBatch = Boolean(latestPipelineBatch) && isLatestBatchRunning
    const batchActionHint = runtimeStatus.isLoading
        ? 'Syncing backend state. Refreshing jobs, batches, and saved templates.'
        : ''
    const bridgeAgent = runtimeStatus.bridgeStatus?.agent || null
    const bridgeReady = Boolean(runtimeStatus.bridgeStatus?.ready)
    const bridgeLoading = Boolean(runtimeStatus.bridgeStatus?.loading)
    const bridgeMarketData = runtimeStatus.bridgeStatus?.market_data || null
    const researchFeedStatus = activePipelineJob?.data_feed_status || latestPipelineBatch?.data_feed_status || 'idle'
    const processingHealthCards = useMemo(() => {
        const bridgeTone = bridgeAgent?.online
            ? 'healthy'
            : bridgeAgent?.stale || runtimeStatus.bridgeStatus?.error
                ? 'issue'
                : bridgeLoading
                    ? 'waiting'
                    : 'idle'
        const bridgeDetail = bridgeAgent?.online
            ? `EA online · ${String(bridgeAgent?.last_status || 'active')}`
            : runtimeStatus.bridgeStatus?.error
                ? String(runtimeStatus.bridgeStatus.error)
                : bridgeAgent?.stale
                    ? `Heartbeat age ${formatDurationSeconds(bridgeAgent?.heartbeat_age_seconds || 0)}`
                    : bridgeLoading
                        ? 'Bridge is loading the active market request.'
                        : 'No active bridge feed yet.'

        const queueCount = Number(bridgeMarketData?.queued_requests || 0)
        const queueTone = queueCount > 0
            ? 'waiting'
            : bridgeReady
                ? 'healthy'
                : 'idle'
        const queueDetail = queueCount > 0
            ? `${formatInteger(queueCount)} queued request${queueCount > 1 ? 's' : ''}`
            : bridgeReady
                ? 'Market snapshot ready.'
                : 'No queued market-data request.'

        const feedTone = researchFeedStatus === 'receiving'
            ? 'healthy'
            : researchFeedStatus === 'waiting'
                ? 'waiting'
                : researchFeedStatus === 'stale'
                    ? 'issue'
                    : researchFeedStatus === 'finished'
                        ? 'idle'
                        : 'idle'
        const feedDetail = activePipelineJob?.data_feed_detail
            || latestPipelineBatch?.data_feed_detail
            || (researchFeedStatus === 'receiving'
                ? 'Research worker is actively producing updates.'
                : researchFeedStatus === 'waiting'
                    ? 'Research worker heartbeat is healthy.'
                    : researchFeedStatus === 'stale'
                        ? 'Research runtime looks stale.'
                        : 'No active research feed.')

        return [
            {
                key: 'bridge',
                label: 'Bridge',
                value: bridgeAgent?.online ? 'Online' : bridgeAgent?.stale ? 'Stale' : bridgeLoading ? 'Loading' : 'Idle',
                detail: bridgeDetail,
                tone: bridgeTone,
            },
            {
                key: 'marketData',
                label: 'Market data',
                value: queueCount > 0 ? `Queue ${queueCount}` : bridgeReady ? 'Ready' : 'Idle',
                detail: queueDetail,
                tone: queueTone,
            },
            {
                key: 'researchFeed',
                label: 'Research feed',
                value: String(researchFeedStatus || 'idle'),
                detail: feedDetail,
                tone: feedTone,
            },
        ]
    }, [
        activePipelineJob?.data_feed_detail,
        bridgeAgent?.heartbeat_age_seconds,
        bridgeAgent?.last_status,
        bridgeAgent?.online,
        bridgeAgent?.stale,
        bridgeLoading,
        bridgeMarketData?.queued_requests,
        bridgeReady,
        latestPipelineBatch?.data_feed_detail,
        researchFeedStatus,
        runtimeStatus.bridgeStatus?.error,
    ])
    const processingOperationalState = useMemo(
        () => deriveOperationalState(activePipelineJob || latestPipelineBatch || null),
        [activePipelineJob, latestPipelineBatch],
    )
    const processingState = useMemo(() => {
        if (runtimeStatus.error) {
            return {
                tone: 'stale',
                title: 'Backend sync failed',
                detail: runtimeStatus.error,
            }
        }
        if (isCancelPending) {
            return {
                tone: 'waiting',
                title: 'Cancellation requested',
                detail: activePipelineJob?.detail || latestPipelineBatch?.detail || 'The backend is finishing the current step before stopping the batch.',
            }
        }
        if (isLatestBatchRunning) {
            return {
                tone: 'receiving',
                title: activePipelineJob ? `Running ${activePipelineJob.run_label || activePipelineJob.label || 'current job'}` : 'Batch running',
                detail: activePipelineJob?.detail || latestPipelineBatch?.detail || 'The backend is processing jobs in sequence: backtest first, then studies.',
            }
        }
        if (batchRequestBuildError) {
            return {
                tone: 'stale',
                title: 'Batch request has an alias issue',
                detail: batchRequestBuildError,
            }
        }
        if (latestPipelineBatch) {
            const terminalLabel = processingOperationalState.label || 'Completed'
            const terminalDetail = latestPipelineBatch?.error
                || latestPipelineBatch?.detail
                || processingOperationalState.detail
                || 'The latest backend batch is not active right now.'
            return {
                tone: processingOperationalState.tone === 'issue'
                    ? 'stale'
                    : processingOperationalState.tone === 'waiting'
                        ? 'waiting'
                        : 'finished',
                title: `Latest batch ${terminalLabel}`,
                detail: terminalDetail,
            }
        }
        if (processingBatch?.error) {
            return {
                tone: 'stale',
                title: 'Batch finished with an error',
                detail: processingBatch.error,
            }
        }
        if (actionFeedback.title) {
            return {
                tone: actionFeedback.tone === 'error'
                    ? 'stale'
                    : actionFeedback.tone === 'warning'
                        ? 'waiting'
                        : actionFeedback.tone === 'running'
                            ? 'receiving'
                            : 'finished',
                title: actionFeedback.title,
                detail: actionFeedback.detail || 'Batch runtime updates will appear here.',
            }
        }
        return {
            tone: 'finished',
            title: 'Batch ready',
            detail: 'No backend batch is active right now. Configure the jobs you want and start a new run when ready.',
        }
    }, [
        actionFeedback.detail,
        actionFeedback.title,
        actionFeedback.tone,
        activePipelineJob,
        isCancelPending,
        isLatestBatchRunning,
        latestPipelineBatch,
        processingBatch,
        processingOperationalState.detail,
        processingOperationalState.label,
        processingOperationalState.tone,
        batchRequestBuildError,
        runtimeStatus.error,
    ])

    function renderAvailableTokens(groups = []) {
        return (
            <aside className='batchTokenSidebar'>
                <div className='batchTokenSidebarHeader'>Available tokens</div>
                <div className='batchTokenSidebarGroups'>
                    {groups.map((group) => (
                        <section key={group.id} className='batchTokenSidebarGroup'>
                            <div className='batchTokenSidebarGroupTitle'>{group.label}</div>
                            <div className='batchTokenSidebarList'>
                                {group.items.map((item) => (
                                    <button
                                        key={`${group.id}-${item.token}`}
                                        type='button'
                                        className='batchTokenSidebarButton'
                                        onClick={() => insertTokenIntoActiveField(item.token)}
                                        title={`Insert ${item.token}[0]`}
                                    >
                                        <span
                                            className='batchTokenSidebarSwatch'
                                            style={{ backgroundColor: item.color || '#6bb8ff' }}
                                            aria-hidden='true'
                                        />
                                        <span className='batchTokenSidebarButtonLabel'>{item.token}</span>
                                    </button>
                                ))}
                            </div>
                        </section>
                    ))}
                </div>
            </aside>
        )
    }

    function renderStrategyFields(job, onPatch, fieldPrefix, tokenGroups) {
        const strategy = job?.strategy || buildBlankStrategy()
        const isBulkMode = fieldPrefix === 'bulk'

        function renderField(label, section, field, rows = 2) {
            const fieldId = `${fieldPrefix}:strategy.${section}.${field}`
            const isStopField = ['gainPrice', 'lossPrice', 'trailingPrice'].includes(String(field || ''))
            return (
                <div className='batchSummaryCard'>
                    <span>{label}</span>
                    <textarea
                        className='batchEditorTextarea'
                        rows={rows}
                        ref={(node) => registerStrategyFieldRef(fieldId, node)}
                        value={String(strategy?.[section]?.[field] || '')}
                        onFocus={() => setActiveStrategyFieldId(fieldId)}
                        onClick={(event) => updateStrategyFieldSelection(fieldId, event)}
                        onKeyUp={(event) => updateStrategyFieldSelection(fieldId, event)}
                        onSelect={(event) => updateStrategyFieldSelection(fieldId, event)}
                        onChange={(event) => onPatch({
                            strategy: {
                                ...strategy,
                                [section]: {
                                    ...strategy[section],
                                    [field]: event.target.value,
                                },
                            },
                        })}
                    />
                    {isStopField ? (
                        <small className='batchFieldHelp'>
                            Stops use the expression exactly as written and are converted to a price at live execution time. In market orders, the broker validates the minimum stop distance using the real fill price, so very tight gain, loss, or trailing expressions can cause the order to be rejected with invalid stops.
                        </small>
                    ) : null}
                </div>
            )
        }

        return (
            <div className='batchStrategyLayout'>
                <div className='batchStrategyEditor'>
                    {renderField('Long open price', 'long', 'openPrice', 1)}
                    {renderField('Long close price', 'long', 'closePrice', 1)}
                    {renderField('Long open if', 'long', 'openIf')}
                    {renderField('Long close if', 'long', 'closeIf')}
                    {renderField('Long gain price', 'long', 'gainPrice', 1)}
                    {renderField('Long loss price', 'long', 'lossPrice', 1)}
                    {renderField('Long trailing price', 'long', 'trailingPrice', 1)}
                    {renderField('Short open price', 'short', 'openPrice', 1)}
                    {renderField('Short close price', 'short', 'closePrice', 1)}
                    {renderField('Short open if', 'short', 'openIf')}
                    {renderField('Short close if', 'short', 'closeIf')}
                    {renderField('Short gain price', 'short', 'gainPrice', 1)}
                    {renderField('Short loss price', 'short', 'lossPrice', 1)}
                    {renderField('Short trailing price', 'short', 'trailingPrice', 1)}
                    <div className='batchSummaryCard wide'>
                        <span>Priority</span>
                        <select
                            className='batchInlineInput'
                            value={isBulkMode ? String(strategy?.other?.priority || '') : String(strategy?.other?.priority || 'Short')}
                            onChange={(event) => onPatch({
                                strategy: {
                                    ...strategy,
                                    other: {
                                        ...strategy.other,
                                        priority: event.target.value,
                                    },
                                },
                            })}
                        >
                            {isBulkMode ? <option value=''>Keep current value</option> : null}
                            <option value='Long'>Long</option>
                            <option value='Short'>Short</option>
                        </select>
                    </div>
                    <div className='batchSummaryCard wide'>
                        {isBulkMode ? (
                            <>
                                <span>Allow inversion</span>
                                <select
                                    className='batchInlineInput'
                                    value={
                                        strategy?.other?.allowInversion === true
                                            ? 'true'
                                            : strategy?.other?.allowInversion === false
                                                ? 'false'
                                                : ''
                                    }
                                    onChange={(event) => onPatch({
                                        strategy: {
                                            ...strategy,
                                            other: {
                                                ...strategy.other,
                                                allowInversion: event.target.value === ''
                                                    ? null
                                                    : event.target.value === 'true',
                                            },
                                        },
                                    })}
                                >
                                    <option value=''>Keep current value</option>
                                    <option value='true'>True</option>
                                    <option value='false'>False</option>
                                </select>
                            </>
                        ) : (
                            <label className='batchCheckboxRow'>
                                <input
                                    type='checkbox'
                                    checked={Boolean(strategy?.other?.allowInversion)}
                                    onChange={(event) => onPatch({
                                        strategy: {
                                            ...strategy,
                                            other: {
                                                ...strategy.other,
                                                allowInversion: event.target.checked,
                                            },
                                        },
                                    })}
                                />
                                <span>Allow inversion</span>
                            </label>
                        )}
                    </div>
                </div>
                {renderAvailableTokens(tokenGroups)}
            </div>
        )
    }

    function renderAuxiliaryStrategySet(job, onPatch, mode = 'selected') {
        const safeJob = normalizeBatchJob(job, 0)
        const entries = Array.isArray(safeJob.strategies) ? safeJob.strategies : []
        const allowEdit = mode !== 'bulk'

        return (
            <div className='batchPortfolioPanel'>
                <div className='batchPortfolioHeader'>
                    <div>
                        <strong>Portfolio extras</strong>
                        <span>The primary strategy is the one you edit above. Extras run after it in priority order.</span>
                    </div>
                    {allowEdit ? (
                        <button
                            type='button'
                            className='batchActionButton'
                            onClick={() => captureStrategyAsAuxiliary(safeJob, onPatch)}
                        >
                            Capture current as extra
                        </button>
                    ) : null}
                </div>

                {entries.length ? (
                    <div className='batchPortfolioList'>
                        {entries.map((entry, index) => (
                            <div key={entry.id} className={`batchPortfolioRow ${entry.enabled === false ? 'isDisabled' : ''}`}>
                                <div className='batchPortfolioRowMain'>
                                    <span className='batchPortfolioPriority'>#{index + 2}</span>
                                    <div className='batchPortfolioFields'>
                                        <input
                                            className='batchInlineInput'
                                            value={entry.label || ''}
                                            disabled={!allowEdit}
                                            onChange={(event) => patchAuxiliaryStrategyEntries(safeJob, onPatch, (current) => current.map((item) => (
                                                String(item.id) === String(entry.id)
                                                    ? { ...item, label: event.target.value }
                                                    : item
                                            )))}
                                        />
                                        <input
                                            className='batchInlineInput'
                                            value={entry.symbol || ''}
                                            placeholder={String(safeJob.chart?.symbol || '').trim().toUpperCase() || 'default symbol'}
                                            disabled={!allowEdit}
                                            onChange={(event) => patchAuxiliaryStrategyEntries(safeJob, onPatch, (current) => current.map((item) => (
                                                String(item.id) === String(entry.id)
                                                    ? { ...item, symbol: event.target.value.toUpperCase() }
                                                    : item
                                            )))}
                                        />
                                        <select
                                            className='batchInlineInput'
                                            value={entry.timeframe || ''}
                                            disabled={!allowEdit}
                                            onChange={(event) => patchAuxiliaryStrategyEntries(safeJob, onPatch, (current) => current.map((item) => (
                                                String(item.id) === String(entry.id)
                                                    ? { ...item, timeframe: event.target.value.toUpperCase() }
                                                    : item
                                            )))}
                                        >
                                            <option value=''>default tf</option>
                                            {TIMEFRAME_OPTIONS.map((option) => (
                                                <option key={option.value} value={option.value}>{option.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>
                                <div className='batchPortfolioRowActions'>
                                    <button
                                        type='button'
                                        className='batchActionButton'
                                        disabled={!allowEdit}
                                        onClick={() => patchAuxiliaryStrategyEntries(safeJob, onPatch, (current) => current.map((item) => (
                                            String(item.id) === String(entry.id)
                                                ? { ...item, enabled: item.enabled === false }
                                                : item
                                        )))}
                                    >
                                        {entry.enabled === false ? 'Enable' : 'Disable'}
                                    </button>
                                    <button
                                        type='button'
                                        className='batchActionButton'
                                        disabled={!allowEdit || index === 0}
                                        onClick={() => patchAuxiliaryStrategyEntries(safeJob, onPatch, (current) => moveArrayItem(current, index, index - 1))}
                                    >
                                        Up
                                    </button>
                                    <button
                                        type='button'
                                        className='batchActionButton'
                                        disabled={!allowEdit || index === entries.length - 1}
                                        onClick={() => patchAuxiliaryStrategyEntries(safeJob, onPatch, (current) => moveArrayItem(current, index, index + 1))}
                                    >
                                        Down
                                    </button>
                                    <button
                                        type='button'
                                        className='batchActionButton'
                                        disabled={!allowEdit}
                                        onClick={() => patchAuxiliaryStrategyEntries(safeJob, onPatch, (current) => current.filter((item) => String(item.id) !== String(entry.id)))}
                                    >
                                        Remove
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='batchPortfolioEmpty'>
                        No extra strategies yet. Capture the current job strategy here whenever you want this batch job to run as a portfolio.
                    </div>
                )}
            </div>
        )
    }

    function renderBulkBacktestFields() {
        const backtest = bulkEditDraft?.backtest || {}
        const textField = (label, key, type = 'text') => (
            <div className='batchSummaryCard' key={key}>
                <span>{label}</span>
                <input
                    className='batchInlineInput'
                    type={type}
                    value={backtest?.[key] ?? ''}
                    onChange={(event) => handlePatchBulkEditDraft({
                        backtest: {
                            [key]: event.target.value,
                        },
                    })}
                    placeholder='Leave blank to keep each job value'
                />
            </div>
        )

        const normalizedCostProfile = normalizeBacktestCostProfile(backtest?.costProfile)

        return (
            <div className='batchSummaryGrid'>
                {textField('Initial balance', 'initialBalance', 'number')}
                {textField('Asset type', 'assetType')}
                {textField('Initial volume', 'initialVolume', 'number')}
                {textField('Pip size', 'pipSize', 'number')}
                {textField('Pip value per lot', 'pipValuePerLot', 'number')}
                <div className='batchSummaryCard'>
                    <span>Cost profile</span>
                    <select
                        className='batchInlineInput'
                        value={String(backtest?.costProfile ?? '') === '' ? '' : normalizedCostProfile}
                        onChange={(event) => {
                            const nextProfile = String(event.target.value || '').trim().toLowerCase()
                            if (!nextProfile) {
                                handlePatchBulkEditDraft({
                                    backtest: {
                                        costProfile: '',
                                    },
                                })
                                return
                            }

                            handlePatchBulkEditDraft({
                                backtest: {
                                    costProfile: nextProfile,
                                    ...buildBacktestCostProfileValues(nextProfile),
                                },
                            })
                        }}
                    >
                        <option value=''>Leave blank to keep each job value</option>
                        {Object.values(BACKTEST_COST_PROFILE_DEFINITIONS)
                            .filter((profile) => profile.id !== 'custom')
                            .map((profile) => (
                                <option key={profile.id} value={profile.id}>{profile.label}</option>
                            ))}
                        <option value='custom'>Custom</option>
                    </select>
                </div>
                {textField('Spread in pips', 'spreadInPips', 'number')}
                {textField('Entry slippage', 'entrySlippageInPips', 'number')}
                {textField('Close slippage', 'closeSlippageInPips', 'number')}
                {textField('Take-profit slippage', 'takeProfitSlippageInPips', 'number')}
                {textField('Stop-loss slippage', 'stopLossSlippageInPips', 'number')}
                {textField('Trailing-stop slippage', 'trailingStopSlippageInPips', 'number')}
                {textField('Minimum stop distance in pips', 'minimumStopDistanceInPips', 'number')}
                {textField('Volatility slippage multiplier', 'volatilitySlippageMultiplier', 'number')}
                {textField('Execution mode', 'executionMode')}
                {textField('History scope mode', 'historyScopeMode')}
                {textField('History scope bars', 'historyScopeBars', 'number')}
            </div>
        )
    }

    return (
        <div className={`Batch ${isActive ? 'active' : ''}`}>
            {batchCompletionDialog ? (
                <div className='overlayContainer batchCompletionOverlay' role='dialog' aria-modal='true' aria-label='Batch completion summary'>
                    <div className='fog' onClick={() => setBatchCompletionDialog(null)} />
                    <div className='overlay batchCompletionWindow'>
                        <button type='button' className='closeOverlay' onClick={() => setBatchCompletionDialog(null)}>x</button>
                        <div className='batchCompletionHeader'>
                            <span>Batch Finished</span>
                            <strong>{batchCompletionDialog.label}</strong>
                        </div>
                        <div className='batchCompletionMeta'>
                            <span>Status: {batchCompletionDialog.status}</span>
                            <span>Total time: {batchCompletionDialog.durationLabel}</span>
                        </div>
                        <div className='batchCompletionGrid'>
                            <div className='batchSummaryCard'>
                                <span>Completed</span>
                                <strong>{formatInteger(batchCompletionDialog.completedJobs)}</strong>
                            </div>
                            <div className='batchSummaryCard'>
                                <span>Failed</span>
                                <strong>{formatInteger(batchCompletionDialog.failedJobs)}</strong>
                            </div>
                            <div className='batchSummaryCard'>
                                <span>Cancelled</span>
                                <strong>{formatInteger(batchCompletionDialog.cancelledJobs)}</strong>
                            </div>
                            <div className='batchSummaryCard'>
                                <span>Total Jobs</span>
                                <strong>{formatInteger(batchCompletionDialog.totalJobs)}</strong>
                            </div>
                        </div>
                        <div className='batchCompletionDetail'>{batchCompletionDialog.detail}</div>
                        <div className='overlayActions'>
                            <button type='button' className='batchActionButton primary' onClick={() => setBatchCompletionDialog(null)}>
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            ) : null}
            <div className='batchTabs'>
                <button
                    type='button'
                    className={`batchTabButton ${activeTab === 'manager' ? 'active' : ''}`}
                    onClick={() => setActiveTab('manager')}
                >
                    Manager
                </button>
                <button
                    type='button'
                    className={`batchTabButton ${activeTab === 'features' ? 'active' : ''}`}
                    onClick={() => setActiveTab('features')}
                >
                    Features
                </button>
                <button
                    type='button'
                    className={`batchTabButton ${activeTab === 'jobs' ? 'active' : ''}`}
                    onClick={() => setActiveTab('jobs')}
                >
                    Jobs
                </button>
                <button
                    type='button'
                    className={`batchTabButton ${activeTab === 'dashboard' ? 'active' : ''}`}
                    onClick={() => setActiveTab('dashboard')}
                >
                    Dashboard
                </button>
            </div>

            {activeTab === 'manager' ? (
                <BatchManager
                    batches={remoteCampaigns}
                    selectedBatchIdOverride={managerSelectedBatchId}
                    onSelectedBatchIdChange={setManagerSelectedBatchId}
                    currentBatchLabel={templateLabel}
                    currentBatchDescription={templateDescription}
                    currentJobs={currentRequestJobs}
                    currentOptions={options}
                    onRefresh={() => refreshRuntime()}
                    onCurrentBatchLabelChange={setTemplateLabel}
                    onCurrentBatchDescriptionChange={setTemplateDescription}
                    onCreateBatch={() => handleSaveTemplate()}
                    onOverwriteBatch={(campaign, overrides) => handleUpdateTemplate(campaign, overrides)}
                    onRenameBatch={(campaign, overrides) => handleUpdateTemplate(campaign, {
                        ...overrides,
                        preserveExistingRequest: true,
                    })}
                    onLoadBatch={(campaign) => void handleLoadTemplateById(campaign)}
                    onRunBatch={(campaignId, campaign) => handleRunTemplate(campaignId, campaign)}
                    onDeleteBatch={(campaignId) => handleDeleteTemplate(campaignId)}
                    onCopyBatchJson={(campaign) => handleCopySavedBatchJson(campaign)}
                    onImportBatchJson={(rawText) => {
                        handleImportJobsFromText(rawText)
                        setActiveTab('jobs')
                    }}
                    onPasteBatchJsonFromClipboard={handlePasteBatchJsonFromClipboard}
                />
            ) : activeTab === 'features' ? (
                <BatchFeaturesPanel
                    indicators={batchFeatures}
                    onChange={(nextIndicators) => persistPatch({ features: nextIndicators })}
                    onLogEvent={onLogEvent}
                />
            ) : activeTab === 'jobs' ? (
                <div className='batchLayout'>
                    <aside className='batchSidebar'>
                        <div className='batchPanel'>
                            <div className='batchPanelHeader'>
                                <div>
                                    <div className='batchPanelTitle'>Programmed jobs</div>
                                    <div className='batchPanelMeta'>Each job is one strategy + backtest run with the research configured to run after it.</div>
                                </div>
                            </div>
                            <div className='batchJobsList'>
                                <button
                                    type='button'
                                    className={`batchJobListItem batchJobCreateItem ${isCreatingJob ? 'active' : ''}`}
                                    onClick={() => {
                                        setSelectedJobId(NEW_BATCH_JOB_ID)
                                        setJobDraft(buildJobDraft())
                                        setJobDetailTab('overview')
                                    }}
                                >
                                    <strong>New job</strong>
                                    <span>Create another programmed run for this batch.</span>
                                </button>
                                <button
                                    type='button'
                                    className={`batchJobListItem batchJobCreateItem ${isBulkEditingJobs ? 'active' : ''}`}
                                    onClick={() => {
                                        setSelectedJobId(BULK_EDIT_JOB_ID)
                                        setJobDetailTab('overview')
                                    }}
                                >
                                    <strong>Bulk edit</strong>
                                    <span>Fill only the fields you want to replace across all programmed jobs.</span>
                                </button>
                                {jobs.map((job) => (
                                    <button
                                        key={job.id}
                                        type='button'
                                        className={`batchJobListItem ${selectedJob?.id === job.id ? 'active' : ''}`}
                                        onClick={() => setSelectedJobId(job.id)}
                                    >
                                        <strong>{job.label || 'Untitled job'}</strong>
                                        <span>{job.chart?.symbol || '--'} · {job.chart?.timeframe || '--'} · {formatInteger(job.chart?.bars || 0)} bars</span>
                                    </button>
                                ))}
                            </div>
                        </div>

                    </aside>

                    <div className='batchContent'>
                        <div className='batchPanel'>
                            <div className='batchPanelHeader'>
                                <div>
                                    <div className='batchPanelTitle'>{isCreatingJob ? 'New job' : isBulkEditingJobs ? 'Bulk edit' : 'Selected job'}</div>
                                    <div className='batchPanelMeta'>
                                        {isCreatingJob
                                            ? 'Create a new programmed job. After adding it, it becomes part of the batch list on the left.'
                                            : isBulkEditingJobs
                                                ? 'Leave fields blank to keep current values. Filled fields will replace that parameter across all programmed jobs.'
                                                : 'Edit the configured job and inspect its strategy, backtest and research setup.'}
                                    </div>
                                </div>
                                {isBulkEditingJobs ? (
                                    <div className='batchActionRow compact'>
                                        <button
                                            type='button'
                                            className='batchActionButton primary'
                                            onClick={() => handleReplaceAcrossJobs()}
                                            disabled={!jobs.length}
                                        >
                                            Replace
                                        </button>
                                        <button
                                            type='button'
                                            className='batchActionButton'
                                            onClick={() => handleResetBulkEditDraft()}
                                        >
                                            Reset bulk edit
                                        </button>
                                    </div>
                                ) : null}
                                {!isCreatingJob && selectedJob ? (
                                    <div className='batchActionRow compact'>
                                        <button
                                            type='button'
                                            className='batchActionButton'
                                            onClick={() => handleMoveJob(selectedJob.id, 'up')}
                                            disabled={selectedJobIndex <= 0}
                                        >
                                            Move up
                                        </button>
                                        <button
                                            type='button'
                                            className='batchActionButton'
                                            onClick={() => handleMoveJob(selectedJob.id, 'down')}
                                            disabled={selectedJobIndex < 0 || selectedJobIndex >= jobs.length - 1}
                                        >
                                            Move down
                                        </button>
                                        <button type='button' className='batchActionButton' onClick={() => handleDuplicateJob(selectedJob)}>
                                            Duplicate
                                        </button>
                                        <button
                                            type='button'
                                            className='batchActionButton'
                                            onClick={() => void handleCopyJsonToClipboard(selectedJobExportJson, 'job')}
                                        >
                                            Copy JSON
                                        </button>
                                        <button
                                            type='button'
                                            className='batchActionButton'
                                            onClick={() => handleDownloadJson(selectedJobExportJson, `${selectedJob.label || 'batch-job'}.json`, 'job')}
                                        >
                                            Export JSON
                                        </button>
                                        <button type='button' className='batchActionButton danger' onClick={() => handleRemoveJob(selectedJob.id)}>
                                            Remove job
                                        </button>
                                    </div>
                                ) : null}
                            </div>
                            <div className='batchPanelBody'>
                                {isCreatingJob ? (
                                    <div className='batchSummaryGrid'>
                                        <div className='batchSummaryCard wide'>
                                            <div className='batchTabs batchTabsInline'>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'overview' ? 'active' : ''}`} onClick={() => setJobDetailTab('overview')}>Overview</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'strategy' ? 'active' : ''}`} onClick={() => setJobDetailTab('strategy')}>Strategy</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'backtest' ? 'active' : ''}`} onClick={() => setJobDetailTab('backtest')}>Backtest</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'research' ? 'active' : ''}`} onClick={() => setJobDetailTab('research')}>Research</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'raw' ? 'active' : ''}`} onClick={() => setJobDetailTab('raw')}>Raw</button>
                                            </div>
                                            {jobDetailTab === 'overview' ? (
                                                <div className='batchSummaryGrid'>
                                                    <div className='batchSummaryCard'>
                                                        <span>Label</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={jobDraft.label}
                                                            onChange={(event) => setJobDraft((current) => ({ ...current, label: event.target.value }))}
                                                            placeholder='EURUSD M1 · Strategy'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Symbol</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={jobDraft.symbol}
                                                            onChange={(event) => setJobDraft((current) => ({ ...current, symbol: event.target.value.toUpperCase() }))}
                                                            placeholder='EURUSD'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Timeframe</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={jobDraft.timeframe}
                                                            onChange={(event) => setJobDraft((current) => ({ ...current, timeframe: event.target.value.toUpperCase() }))}
                                                            placeholder='M1'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Bars</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            type='number'
                                                            value={jobDraft.bars}
                                                            onChange={(event) => setJobDraft((current) => ({ ...current, bars: Math.max(1, Number(event.target.value) || 1) }))}
                                                            placeholder='1000'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Indicators</span>
                                                        <strong>{formatInteger(draftUsedIndicatorCount)}</strong>
                                                    </div>
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Notes</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={jobDraft.notes}
                                                            onChange={(event) => setJobDraft((current) => ({ ...current, notes: event.target.value }))}
                                                            placeholder='Optional'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard wide'>
                                                        <div className='batchActionRow compact'>
                                                            <button type='button' className='batchActionButton primary' onClick={() => handleAddJobFromBuilder()}>
                                                                Add job
                                                            </button>
                                                            <button type='button' className='batchActionButton' onClick={() => handleResetJobDraft()}>
                                                                Reset new job
                                                            </button>
                                                        </div>
                                                    </div>
                                                </div>
                                            ) : null}
                                            {jobDetailTab === 'strategy' ? (
                                                <>
                                                    {renderStrategyFields(draftJobPreview, handlePatchDraftJob, 'draft', draftTokenGroups)}
                                                    {renderAuxiliaryStrategySet(draftJobPreview, handlePatchDraftJob, 'draft')}
                                                </>
                                            ) : null}
                                            {jobDetailTab === 'backtest' ? (
                                                <BacktestConfigEditor
                                                    backtest={jobDraft.backtest}
                                                    setBacktest={(updater) => {
                                                        const nextValue = typeof updater === 'function' ? updater(jobDraft.backtest) : updater
                                                        handlePatchDraftJob({ backtest: nextValue })
                                                    }}
                                                    activeTab={jobBacktestTab}
                                                    setActiveTab={setJobBacktestTab}
                                                    onLogEvent={onLogEvent}
                                                    loadedChartCandles={draftJobPreview.chart?.bars || 0}
                                                    showToolbarActions={false}
                                                />
                                            ) : null}
                                            {jobDetailTab === 'research' ? (
                                                <div className='batchStrategyEditor'>
                                                    <div className='batchSummaryCard'>
                                                        <span>Research plan</span>
                                                        <strong>{String(draftResearchPreview?.kind || 'none')}</strong>
                                                    </div>
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Research profile preview</span>
                                                        <strong>
                                                            {draftResearchPreview?.warning
                                                                ? draftResearchPreview.warning
                                                                : draftResearchPreview?.kind === 'preset_compare'
                                                                    ? 'Backend will run preset-compare research after the isolated backtest.'
                                                                    : 'No research will run after this backtest.'}
                                                        </strong>
                                                    </div>
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Comparison presets</span>
                                                        <strong>{formatInteger(draftComparisonPresets.presets.length)} · {draftComparisonSourceLabel}</strong>
                                                    </div>
                                                    {draftMutationPreview ? (
                                                        <div className='batchSummaryCard wide'>
                                                            <span>Mutation profile</span>
                                                            <strong>{draftMutationPreview.summary}</strong>
                                                        </div>
                                                    ) : null}
                                                    {draftLineagePreview ? (
                                                        <div className='batchSummaryCard wide'>
                                                            <span>Portfolio lineage</span>
                                                            <strong>{draftLineagePreview.summary}</strong>
                                                        </div>
                                                    ) : null}
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Portfolio signature</span>
                                                        <strong>{draftSignaturePreview.summary}</strong>
                                                    </div>
                                                    {draftMutationDeltaPreview ? (
                                                        <div className='batchSummaryCard wide'>
                                                            <span>Mutation diff</span>
                                                            <strong>{draftMutationDeltaPreview.summary}</strong>
                                                            <small className='batchMutationDiffMeta'>
                                                                {draftMutationDeltaPreview.parentLabel} → {draftMutationDeltaPreview.targetLabel}
                                                                {draftMutationDeltaPreview.signatureChanged ? ' · portfolio signature changed' : ' · portfolio signature unchanged'}
                                                            </small>
                                                            {draftMutationDeltaPreview.changedFields.length ? (
                                                                <div className='batchMutationDiffList'>
                                                                    {draftMutationDeltaPreview.changedFields.slice(0, 6).map((field) => (
                                                                        <span key={`draft-mutation-diff-${field}`} className='batchMutationDiffChip'>{field}</span>
                                                                    ))}
                                                                </div>
                                                            ) : null}
                                                        </div>
                                                    ) : null}
                                                </div>
                                            ) : null}
                                            {jobDetailTab === 'raw' ? (
                                                <pre className='batchPayloadPreview'>{JSON.stringify(draftJobPreview, null, 2)}</pre>
                                            ) : null}
                                        </div>
                                    </div>
                                ) : isBulkEditingJobs ? (
                                    <div className='batchSummaryGrid'>
                                        <div className='batchSummaryCard wide'>
                                            <div className='batchTabs batchTabsInline'>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'overview' ? 'active' : ''}`} onClick={() => setJobDetailTab('overview')}>Overview</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'strategy' ? 'active' : ''}`} onClick={() => setJobDetailTab('strategy')}>Strategy</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'backtest' ? 'active' : ''}`} onClick={() => setJobDetailTab('backtest')}>Backtest</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'raw' ? 'active' : ''}`} onClick={() => setJobDetailTab('raw')}>Raw</button>
                                            </div>
                                            {jobDetailTab === 'overview' ? (
                                                <div className='batchSummaryGrid'>
                                                    <div className='batchSummaryCard'>
                                                        <span>Label</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={bulkEditDraft.label}
                                                            onChange={(event) => setBulkEditDraft((current) => ({ ...current, label: event.target.value }))}
                                                            placeholder='Leave blank to keep current labels'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Symbol</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={bulkEditDraft.symbol}
                                                            onChange={(event) => setBulkEditDraft((current) => ({ ...current, symbol: event.target.value.toUpperCase() }))}
                                                            placeholder='Leave blank to keep current symbols'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Timeframe</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={bulkEditDraft.timeframe}
                                                            onChange={(event) => setBulkEditDraft((current) => ({ ...current, timeframe: event.target.value.toUpperCase() }))}
                                                            placeholder='Leave blank to keep current timeframes'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Bars</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            type='number'
                                                            value={bulkEditDraft.bars}
                                                            onChange={(event) => setBulkEditDraft((current) => ({ ...current, bars: event.target.value }))}
                                                            placeholder='Leave blank to keep current bars'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Notes</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={bulkEditDraft.notes}
                                                            onChange={(event) => setBulkEditDraft((current) => ({ ...current, notes: event.target.value }))}
                                                            placeholder='Leave blank to keep current notes'
                                                        />
                                                    </div>
                                                </div>
                                            ) : null}
                                            {jobDetailTab === 'strategy' ? renderStrategyFields(bulkEditDraft, handlePatchBulkEditDraft, 'bulk', draftTokenGroups) : null}
                                            {jobDetailTab === 'backtest' ? renderBulkBacktestFields() : null}
                                            {jobDetailTab === 'raw' ? (
                                                <pre className='batchPayloadPreview'>{JSON.stringify(bulkEditDraft, null, 2)}</pre>
                                            ) : null}
                                        </div>
                                    </div>
                                ) : selectedJob ? (
                                    <div className='batchSummaryGrid'>
                                        <div className='batchSummaryCard wide'>
                                            <div className='batchTabs batchTabsInline'>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'overview' ? 'active' : ''}`} onClick={() => setJobDetailTab('overview')}>Overview</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'strategy' ? 'active' : ''}`} onClick={() => setJobDetailTab('strategy')}>Strategy</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'backtest' ? 'active' : ''}`} onClick={() => setJobDetailTab('backtest')}>Backtest</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'research' ? 'active' : ''}`} onClick={() => setJobDetailTab('research')}>Research</button>
                                                <button type='button' className={`batchTabButton ${jobDetailTab === 'raw' ? 'active' : ''}`} onClick={() => setJobDetailTab('raw')}>Raw</button>
                                            </div>
                                            {jobDetailTab === 'overview' ? (
                                                <div className='batchSummaryGrid'>
                                                    <div className='batchSummaryCard'>
                                                        <span>Label</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={selectedJob.label || ''}
                                                            onChange={(event) => handlePatchSelectedJob({
                                                                label: event.target.value,
                                                            })}
                                                            placeholder='EURUSD M1 · Strategy'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Symbol</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={selectedJob.chart.symbol || ''}
                                                            onChange={(event) => handlePatchSelectedJob({
                                                                chart: {
                                                                    symbol: event.target.value.toUpperCase(),
                                                                },
                                                            })}
                                                            placeholder='EURUSD'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Timeframe</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={selectedJob.chart.timeframe || ''}
                                                            onChange={(event) => handlePatchSelectedJob({
                                                                chart: {
                                                                    timeframe: event.target.value.toUpperCase(),
                                                                },
                                                            })}
                                                            placeholder='M1'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Bars</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            type='number'
                                                            min='1'
                                                            step='1'
                                                            value={selectedJob.chart.bars || 1}
                                                            onChange={(event) => handlePatchSelectedJob({
                                                                chart: {
                                                                    bars: Math.max(1, Number(event.target.value) || 1),
                                                                },
                                                            })}
                                                            placeholder='1000'
                                                        />
                                                    </div>
                                                    <div className='batchSummaryCard'>
                                                        <span>Indicators</span>
                                                        <strong>{formatInteger(selectedUsedIndicatorCount)}</strong>
                                                    </div>
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Notes</span>
                                                        <input
                                                            className='batchInlineInput'
                                                            value={selectedJob.notes || ''}
                                                            onChange={(event) => handlePatchSelectedJob({
                                                                notes: event.target.value,
                                                            })}
                                                            placeholder='Optional'
                                                        />
                                                    </div>
                                                </div>
                                            ) : null}
                                            {jobDetailTab === 'strategy' ? (
                                                <>
                                                    {renderStrategyFields(selectedJob, handlePatchSelectedJob, 'selected', selectedTokenGroups)}
                                                    {renderAuxiliaryStrategySet(selectedJob, handlePatchSelectedJob, 'selected')}
                                                </>
                                            ) : null}
                                            {jobDetailTab === 'backtest' ? (
                                                <BacktestConfigEditor
                                                    backtest={selectedJob.backtest}
                                                    setBacktest={(updater) => {
                                                        const nextValue = typeof updater === 'function' ? updater(selectedJob.backtest) : updater
                                                        handlePatchSelectedJob({ backtest: nextValue })
                                                    }}
                                                    activeTab={jobBacktestTab}
                                                    setActiveTab={setJobBacktestTab}
                                                    onLogEvent={onLogEvent}
                                                    loadedChartCandles={selectedJob.chart?.bars || 0}
                                                    showToolbarActions={false}
                                                />
                                            ) : null}
                                            {jobDetailTab === 'research' ? (
                                                <div className='batchStrategyEditor'>
                                                    <div className='batchSummaryCard'>
                                                        <span>Research plan</span>
                                                        <strong>{String(selectedJobResearchPreview?.kind || 'none')}</strong>
                                                    </div>
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Research profile preview</span>
                                                        <strong>
                                                            {selectedJobResearchPreview?.warning
                                                                ? selectedJobResearchPreview.warning
                                                                : selectedJobResearchPreview?.kind === 'preset_compare'
                                                                    ? 'Backend will run preset-compare research after the isolated backtest.'
                                                                    : 'No research will run after this backtest.'}
                                                        </strong>
                                                    </div>
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Comparison presets</span>
                                                        <strong>{formatInteger(selectedJobComparisonPresets.presets.length)} · {selectedJobComparisonSourceLabel}</strong>
                                                    </div>
                                                    {selectedJobMutationPreview ? (
                                                        <div className='batchSummaryCard wide'>
                                                            <span>Mutation profile</span>
                                                            <strong>{selectedJobMutationPreview.summary}</strong>
                                                        </div>
                                                    ) : null}
                                                    {selectedJobLineagePreview ? (
                                                        <div className='batchSummaryCard wide'>
                                                            <span>Portfolio lineage</span>
                                                            <strong>{selectedJobLineagePreview.summary}</strong>
                                                        </div>
                                                    ) : null}
                                                    {selectedJobSignaturePreview ? (
                                                        <div className='batchSummaryCard wide'>
                                                            <span>Portfolio signature</span>
                                                            <strong>{selectedJobSignaturePreview.summary}</strong>
                                                        </div>
                                                    ) : null}
                                                    {selectedJobMutationDeltaPreview ? (
                                                        <div className='batchSummaryCard wide'>
                                                            <span>Mutation diff</span>
                                                            <strong>{selectedJobMutationDeltaPreview.summary}</strong>
                                                            <small className='batchMutationDiffMeta'>
                                                                {selectedJobMutationDeltaPreview.parentLabel} → {selectedJobMutationDeltaPreview.targetLabel}
                                                                {selectedJobMutationDeltaPreview.signatureChanged ? ' · portfolio signature changed' : ' · portfolio signature unchanged'}
                                                            </small>
                                                            {selectedJobMutationDeltaPreview.changedFields.length ? (
                                                                <div className='batchMutationDiffList'>
                                                                    {selectedJobMutationDeltaPreview.changedFields.slice(0, 6).map((field) => (
                                                                        <span key={`selected-mutation-diff-${field}`} className='batchMutationDiffChip'>{field}</span>
                                                                    ))}
                                                                </div>
                                                            ) : null}
                                                        </div>
                                                    ) : null}
                                                    <div className='batchSummaryCard wide'>
                                                        <span>Comparison preset builder</span>
                                                        {selectedJobComparisonCandidates.length ? (
                                                            <div className='batchPresetBuilder'>
                                                                <div className='batchPanelMeta'>
                                                                    Select specific comparison jobs for this run. If you leave everything unchecked, the batch stays in auto mode.
                                                                </div>
                                                                <div className='batchPresetBuilderList'>
                                                                    {selectedJobComparisonCandidates.map((entry) => {
                                                                        const isChecked = selectedJobComparisonSelection.includes(String(entry.id))
                                                                        return (
                                                                            <label key={`comparison-candidate-${entry.id}`} className='batchPresetBuilderItem'>
                                                                                <input
                                                                                    type='checkbox'
                                                                                    checked={isChecked}
                                                                                    onChange={() => handleToggleComparisonPreset(selectedJob.id, entry.id)}
                                                                                />
                                                                                <span>
                                                                                    <strong>{entry.label}</strong>
                                                                                    <small>{entry.chart?.symbol || '--'} · {entry.chart?.timeframe || '--'} · {formatInteger(entry.chart?.bars || 0)} bars</small>
                                                                                </span>
                                                                            </label>
                                                                        )
                                                                    })}
                                                                </div>
                                                                <div className='batchActionRow'>
                                                                    <button
                                                                        type='button'
                                                                        className='batchActionButton'
                                                                        onClick={() => handleResetComparisonPresetSelection(selectedJob.id)}
                                                                        disabled={!selectedJobComparisonSelection.length}
                                                                    >
                                                                        Use auto comparison source
                                                                    </button>
                                                                </div>
                                                            </div>
                                                        ) : (
                                                            <strong>Import at least one additional job to build visual comparison presets here.</strong>
                                                        )}
                                                    </div>
                                                </div>
                                            ) : null}
                                            {jobDetailTab === 'raw' ? (
                                                <pre className='batchPayloadPreview'>{JSON.stringify(selectedJob, null, 2)}</pre>
                                            ) : null}
                                        </div>
                                    </div>
                                ) : (
                                    <div className='batchEmpty'>Select a job on the left to inspect its config.</div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            ) : activeTab === 'dashboard' ? (
                <div className='batchRunsLayout'>
                    <div className='batchPanel batchDashboardControls'>
                        <div className='batchPanelHeader'>
                            <div>
                                <div className='batchPanelTitle'>Batch options</div>
                                <div className='batchPanelMeta'>The backend runs one job at a time, always in sequence: backtest first, then the selected research studies.</div>
                            </div>
                            <div className='batchActionRow compact'>
                                <button
                                    type='button'
                                    className='batchActionButton primary'
                                    onClick={() => void handleStartBatch()}
                                    disabled={!authToken || !jobs.length || isRunButtonBusy}
                                >
                                    {isRunButtonBusy ? 'Running' : 'Run batch'}
                                </button>
                                <button
                                    type='button'
                                    className='batchActionButton'
                                    onClick={handleCreateFollowUpBatch}
                                    disabled={isCreateFollowUpPending || !remoteBatches.some((batch) => String(batch?.status || '').toLowerCase() === 'completed')}
                                >
                                    {isCreateFollowUpPending ? 'Creating follow-up...' : 'Create follow-up batch'}
                                </button>
                                <button
                                    type='button'
                                    className='batchActionButton'
                                    onClick={handleCreateFailedRerunBatch}
                                    disabled={isCreateFailedRerunPending || !latestPipelineBatch?.id || !latestFailedJobs}
                                >
                                    {isCreateFailedRerunPending ? 'Creating retry...' : 'Rerun failed jobs'}
                                </button>
                                <button
                                    type='button'
                                    className='batchActionButton'
                                    onClick={() => void handleCancelBatch()}
                                    disabled={!canCancelLatestBatch || isCancelPending || runtimeStatus.isLoading}
                                >
                                    {isCancelPending ? 'Cancelling' : 'Cancel batch'}
                                </button>
                            </div>
                        </div>
                        {batchActionHint ? (
                            <div className='batchActionHint'>{batchActionHint}</div>
                        ) : null}
                        <div className='batchSummaryGrid'>
                            <div className='batchSummaryCard wide batchRunStatusStrip'>
                                <div className='batchRunStatusCard'>
                                    <span>Loaded In Editor</span>
                                    <strong>{String(templateLabel || batchLabel || 'Unsaved batch').trim() || 'Unsaved batch'}</strong>
                                    <small>
                                        {jobs.length
                                            ? `${formatInteger(jobs.length)} programmed job${jobs.length > 1 ? 's' : ''} ready to run when you click Run batch.`
                                            : 'No programmed jobs loaded in the editor yet.'}
                                    </small>
                                </div>
                                <div className='batchRunStatusCard'>
                                    <span>Last Backend Batch</span>
                                    <strong>{latestPipelineBatch?.label || 'No batch has run yet'}</strong>
                                    <small>
                                        {latestPipelineBatch
                                            ? `${String(latestPipelineBatch.status || 'queued')} · ${formatInteger(latestPipelineBatch.total_jobs || 0)} job${Number(latestPipelineBatch.total_jobs || 0) === 1 ? '' : 's'}`
                                            : 'This area only changes after a batch is actually launched in the backend.'}
                                    </small>
                                </div>
                            </div>
                            <div className='batchSummaryCard wide batchSummaryIdentityBlock'>
                                <div className='batchSummaryIdentityMain'>
                                    <span>Batch label</span>
                                    <input
                                        className='batchInlineInput'
                                        value={batchLabel}
                                        onChange={(event) => setBatchLabel(event.target.value)}
                                        placeholder='Batch run'
                                    />
                                </div>
                                {batchRuntimeRankings.completedCount ? (
                                    <div className='batchSummaryIdentitySidebar'>
                                        <span>Batch highlights</span>
                                        <div className='batchRuntimeLeaderboard batchRuntimeLeaderboardInline'>
                                            <div className='batchRuntimeLeaderboardCard'>
                                                <span>Top Score</span>
                                                <div className='batchRuntimeLeaderboardList'>
                                                    {batchRuntimeRankings.topScore.map((entry, index) => (
                                                        <button
                                                            key={`top-score-${entry.job?.id}`}
                                                            type='button'
                                                            className='batchRuntimeLeaderboardItem'
                                                            onClick={() => setSelectedRuntimeJobId(String(entry.job?.id || ''))}
                                                        >
                                                            <strong>{index + 1}. {entry.job?.run_label || `Job #${entry.job?.id}`}</strong>
                                                            <span>{formatDecimal(entry.value, 1)}</span>
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                            <div className='batchRuntimeLeaderboardCard'>
                                                <span>Top Net PnL</span>
                                                <div className='batchRuntimeLeaderboardList'>
                                                    {batchRuntimeRankings.bestNetPnl.map((entry, index) => (
                                                        <button
                                                            key={`net-pnl-${entry.job?.id}`}
                                                            type='button'
                                                            className='batchRuntimeLeaderboardItem'
                                                            onClick={() => setSelectedRuntimeJobId(String(entry.job?.id || ''))}
                                                        >
                                                            <strong>{index + 1}. {entry.job?.run_label || `Job #${entry.job?.id}`}</strong>
                                                            <span>{formatDecimal(entry.value, 2)}</span>
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                            <div className='batchRuntimeLeaderboardCard'>
                                                <span>Top Win Rate</span>
                                                <div className='batchRuntimeLeaderboardList'>
                                                    {batchRuntimeRankings.bestWinRate.map((entry, index) => (
                                                        <button
                                                            key={`win-rate-${entry.job?.id}`}
                                                            type='button'
                                                            className='batchRuntimeLeaderboardItem'
                                                            onClick={() => setSelectedRuntimeJobId(String(entry.job?.id || ''))}
                                                        >
                                                            <strong>{index + 1}. {entry.job?.run_label || `Job #${entry.job?.id}`}</strong>
                                                            <span>{formatPercent(entry.value)}</span>
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                            <div className='batchRuntimeLeaderboardCard'>
                                                <span>Worst Drawdown</span>
                                                <div className='batchRuntimeLeaderboardList'>
                                                    {batchRuntimeRankings.worstDrawdown.map((entry, index) => (
                                                        <button
                                                            key={`drawdown-${entry.job?.id}`}
                                                            type='button'
                                                            className='batchRuntimeLeaderboardItem'
                                                            onClick={() => setSelectedRuntimeJobId(String(entry.job?.id || ''))}
                                                        >
                                                            <strong>{index + 1}. {entry.job?.run_label || `Job #${entry.job?.id}`}</strong>
                                                            <span>{formatDecimal(entry.value, 2)}</span>
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                ) : null}
                            </div>
                            <div className='batchSummaryCard wide'>
                                <span>Studies after each backtest</span>
                                <div className='batchPresetBuilderList twoColumns'>
                                    <label className='batchPresetBuilderItem'>
                                        <input type='checkbox' checked={Boolean(selectedResearchStudies.presetCompare)} onChange={() => handleToggleResearchStudy('presetCompare')} />
                                        <span><strong>Preset compare</strong><small>Run the current-context compare after each backtest.</small></span>
                                    </label>
                                    <label className='batchPresetBuilderItem'>
                                        <input type='checkbox' checked={Boolean(selectedResearchStudies.timeframeStudy)} onChange={() => handleToggleResearchStudy('timeframeStudy')} />
                                        <span><strong>Timeframe study</strong><small>Compare consistency across timeframes.</small></span>
                                    </label>
                                    <label className='batchPresetBuilderItem'>
                                        <input type='checkbox' checked={Boolean(selectedResearchStudies.symbolStudy)} onChange={() => handleToggleResearchStudy('symbolStudy')} />
                                        <span><strong>Symbol study</strong><small>Compare consistency across symbols.</small></span>
                                    </label>
                                    <label className='batchPresetBuilderItem'>
                                        <input type='checkbox' checked={Boolean(selectedResearchStudies.walkforwardStudy)} onChange={() => handleToggleResearchStudy('walkforwardStudy')} />
                                        <span><strong>Walk-forward</strong><small>Run sequential train/test validation.</small></span>
                                    </label>
                                </div>
                            </div>
                            <div className='batchSummaryCard wide'>
                                <span>Research execution</span>
                                <strong>
                                    {hasAnyResearchStudySelected
                                        ? 'Each job will run backtest first and then only the checked studies.'
                                        : 'Only isolated backtests will run. No post-backtest research study is selected.'}
                                </strong>
                            </div>
                            <div className='batchSummaryCard wide'>
                                <span>Portfolio follow-up mutation</span>
                                <div className='batchMutationControlGrid'>
                                    <label className='batchField'>
                                        <span>Mode</span>
                                        <select
                                            value={portfolioMutationOptions.mode}
                                            onChange={(event) => handleChangePortfolioMutationMode(event.target.value)}
                                        >
                                            <option value='mutate_primary_only'>Mutate primary only</option>
                                            <option value='mutate_selected_auxiliary'>Mutate selected auxiliary</option>
                                        </select>
                                    </label>
                                    <label className='batchField'>
                                        <span>Target strategy</span>
                                        <select
                                            value={portfolioMutationOptions.mode === 'mutate_selected_auxiliary' ? (selectedPortfolioMutationTarget?.id || '') : ''}
                                            onChange={(event) => handleChangePortfolioMutationTarget(event.target.value)}
                                            disabled={portfolioMutationOptions.mode !== 'mutate_selected_auxiliary' || !availablePortfolioMutationTargets.length}
                                        >
                                            {!availablePortfolioMutationTargets.length ? (
                                                <option value=''>No auxiliary strategies loaded</option>
                                            ) : null}
                                            {availablePortfolioMutationTargets.map((entry) => (
                                                <option key={`portfolio-mutation-target-${entry.id}`} value={entry.id}>{entry.label}</option>
                                            ))}
                                        </select>
                                    </label>
                                </div>
                                <div className='batchMutationHintRow'>
                                    <strong>{effectivePortfolioMutationSummary}</strong>
                                    <small>{describePortfolioMutationMode(portfolioMutationOptions.mode)}</small>
                                    {portfolioMutationOptions.mode === 'mutate_selected_auxiliary' && !availablePortfolioMutationTargets.length ? (
                                        <small>Load or create at least one auxiliary strategy in your jobs before using auxiliary-targeted follow-ups.</small>
                                    ) : null}
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className='batchRuntimeCard batchDashboardProcessing'>
                        <div className='batchRuntimeHeader'>
                            <div>
                                <div className='batchPanelTitle'>Processing</div>
                                <div className='batchPanelMeta'>
                                    {processingBatch
                                        ? `${processingBatch.label || 'Batch'} · ${String(processingBatch.status || 'queued')}`
                                        : 'No backend batch has run yet.'}
                                </div>
                            </div>
                            <div className='batchRuntimeHeaderActions'>
                                <div className={`batchRuntimeBadge is-${activePipelineJob ? 'running' : processingBatch ? String(processingBatch.status || 'idle').toLowerCase() : 'idle'}`}>
                                    {activePipelineJob ? 'Running' : processingBatch ? String(processingBatch.status || 'idle') : 'Idle'}
                                </div>
                                <button
                                    type='button'
                                    className='batchActionButton'
                                    onClick={() => void refreshRuntime()}
                                    disabled={!authToken || runtimeStatus.isLoading}
                                >
                                    {runtimeStatus.isLoading ? 'Syncing...' : 'Refresh'}
                                </button>
                            </div>
                        </div>
                        <div className='batchRuntimeContextNote'>
                            The processing panel reflects the last batch launched in the backend. Loading a saved template into the editor does not change this panel until you click `Run batch`.
                        </div>
                        <div className={`batchRuntimeFeedState ${processingState.tone}`}>
                            <strong>{processingState.title}</strong>
                            <span>{processingState.detail}</span>
                        </div>
                        {latestPipelineBatch ? (
                            <div className='batchRuntimeOperationalRow'>
                                <span className={`batchOperationalBadge is-${processingOperationalState.tone}`}>
                                    {processingOperationalState.label}
                                </span>
                                <small>{processingOperationalState.detail}</small>
                            </div>
                        ) : null}
                        <div className='batchProgressTrack'>
                            <div className='batchProgressFill' style={{ width: `${processingBatch ? latestProgress : 0}%` }} />
                        </div>
                        <div className='batchRuntimeMeta'>
                            <span>{processingBatch ? latestProgress : 0}%</span>
                            <span>Processed {formatInteger(processingBatch ? latestProcessedJobs : 0)}/{formatInteger(processingBatch ? latestTotalJobs : 0)}</span>
                            <span>Completed {formatInteger(processingBatch ? latestCompletedJobs : 0)}</span>
                            <span>Failed {formatInteger(processingBatch ? latestFailedJobs : 0)}</span>
                            <span>Cancelled {formatInteger(processingBatch ? latestCancelledJobs : 0)}</span>
                            <span>Total time {formatDurationSeconds(latestBatchDurationSeconds)}</span>
                            <span>{runtimeStatus.isLoading ? 'Syncing backend...' : `Last sync ${lastRuntimeSyncLabel}`}</span>
                        </div>
                        <div className='batchRuntimeHealthGrid'>
                            {processingHealthCards.map((card) => (
                                <div key={card.key} className={`batchRuntimeHealthCard is-${card.tone}`}>
                                    <span>{card.label}</span>
                                    <strong>{card.value}</strong>
                                    <small>{card.detail}</small>
                                </div>
                            ))}
                        </div>
                        {processingBatch?.error ? (
                            <div className='batchRuntimeDetail'>{processingBatch.error}</div>
                        ) : null}
                    </div>

                    <div className='batchPanel batchDashboardRuns'>
                        <div className='batchRuntimeToolbar'>
                            <label className='batchField'>
                                <span>Report source</span>
                                <select value={reportSource} onChange={(event) => setReportSource(event.target.value)}>
                                    <option value='latest_batch'>Latest batch</option>
                                    <option value='all_pipeline_runs'>All pipeline runs</option>
                                </select>
                            </label>
                            <label className='batchField'>
                                <span>Search</span>
                                <input
                                    value={reportQuery}
                                    onChange={(event) => applyRuntimeViewPatch({ reportQuery: event.target.value })}
                                    placeholder='Label, symbol, timeframe'
                                />
                            </label>
                            <label className='batchField'>
                                <span>Status</span>
                                <select value={reportStatusFilter} onChange={(event) => applyRuntimeViewPatch({ reportStatusFilter: event.target.value })}>
                                    <option value='all'>All</option>
                                    <option value='queued'>Queued</option>
                                    <option value='running'>Running</option>
                                    <option value='completed'>Completed</option>
                                    <option value='failed'>Failed</option>
                                    <option value='cancelled'>Cancelled</option>
                                </select>
                            </label>
                            <label className='batchField'>
                                <span>Symbol</span>
                                <select value={reportSymbolFilter} onChange={(event) => applyRuntimeViewPatch({ reportSymbolFilter: event.target.value })}>
                                    <option value='all'>All</option>
                                    {reportSymbolOptions.map((value) => (
                                        <option key={value} value={value}>{value}</option>
                                    ))}
                                </select>
                            </label>
                            <label className='batchField'>
                                <span>Timeframe</span>
                                <select value={reportTimeframeFilter} onChange={(event) => applyRuntimeViewPatch({ reportTimeframeFilter: event.target.value })}>
                                    <option value='all'>All</option>
                                    {reportTimeframeOptions.map((value) => (
                                        <option key={value} value={value}>{value}</option>
                                    ))}
                                </select>
                            </label>
                            <label className='batchField'>
                                <span>Sort by</span>
                                <select value={reportSortKey} onChange={(event) => applyRuntimeViewPatch({ reportSortKey: event.target.value })}>
                                    {PIPELINE_SORT_OPTIONS.map((option) => (
                                        <option key={`pipeline-sort-${option.key}`} value={option.key}>{option.label}</option>
                                    ))}
                                </select>
                            </label>
                            <label className='batchField'>
                                <span>Order</span>
                                <select value={reportSortDirection} onChange={(event) => applyRuntimeViewPatch({ reportSortDirection: event.target.value })}>
                                    <option value='desc'>Desc</option>
                                    <option value='asc'>Asc</option>
                                </select>
                            </label>
                            <div className='batchField batchFieldAction'>
                                <span>View</span>
                                <button
                                    type='button'
                                    className='batchActionButton'
                                    onClick={handleResetRuntimeView}
                                >
                                    Reset view
                                </button>
                            </div>
                        </div>
                        {filteredPipelineJobs.length ? (
                            <div className='batchRuntimeList'>
                                {filteredPipelineJobs.slice(0, 30).map((job) => {
                                    const summaryMetrics = extractPipelineSummaryMetrics(job)
                                    const progressValue = Math.max(0, Math.min(100, Math.round(Number(job?.progress || 0) * 100)))
                                    const jobDurationSeconds = getElapsedSeconds(job?.started_at, job?.finished_at)
                                    const operationalState = deriveOperationalState(job)

                                    return (
                                        <div
                                            key={`pipeline-job-${job?.id}`}
                                            className={`batchRuntimeListItem ${String(selectedRuntimeJob?.id || '') === String(job?.id || '') ? 'active' : ''} ${['failed', 'cancelled'].includes(String(job?.status || '').toLowerCase()) ? 'hasIssue' : ''}`}
                                            onClick={() => setSelectedRuntimeJobId(String(job?.id || ''))}
                                            role='button'
                                            tabIndex={0}
                                            onKeyDown={(event) => {
                                                if (event.key === 'Enter' || event.key === ' ') {
                                                    event.preventDefault()
                                                    setSelectedRuntimeJobId(String(job?.id || ''))
                                                }
                                            }}
                                        >
                                            <div className='batchRuntimeListMain'>
                                                <strong>{job?.run_label || job?.phase_label || `Pipeline job #${job?.id}`}</strong>
                                                <div className='batchPanelMeta'>
                                                    {String(job?.status || 'queued')} · {String(job?.result?.pipeline?.chart?.symbol || '--')} · {String(job?.result?.pipeline?.chart?.timeframe || '--')} · {job?.created_at ? new Date(Number(job.created_at) * 1000).toLocaleString() : '-'}
                                                </div>
                                                <div className='batchRuntimeJobMeta'>
                                                    <span className={`batchOperationalBadge is-${operationalState.tone}`}>{operationalState.label}</span>
                                                    <span>{progressValue}%</span>
                                                    <span>{formatDurationSeconds(jobDurationSeconds)}</span>
                                                </div>
                                                <div className='batchProgressTrack compact'>
                                                    <div className='batchProgressFill' style={{ width: `${progressValue}%` }} />
                                                </div>
                                                {summaryMetrics.length ? (
                                                    <div className='batchRuntimeSummaryGrid'>
                                                        {summaryMetrics.map((metric) => (
                                                            <div
                                                                key={`${job?.id}-${metric.key}`}
                                                                className={`batchRuntimeSummaryChip ${highlightedRuntimeMetrics.has(`${String(job?.id || '')}:${metric.key}`) ? 'isHighlighted' : ''}`}
                                                            >
                                                                <span>{metric.label}</span>
                                                                <strong>{formatMetricValue(metric)}</strong>
                                                            </div>
                                                        ))}
                                                    </div>
                                                ) : null}
                                                <div className='batchRuntimeJobDetail'>
                                                    {operationalState.detail || job?.error || job?.detail || 'No job detail available yet.'}
                                                </div>
                                            </div>
                                            <div className='batchActionRow batchActionColumn'>
                                                <button
                                                    type='button'
                                                    className='batchActionButton'
                                                    onClick={(event) => {
                                                        event.stopPropagation()
                                                        void handleSaveJobAsStrategy(job)
                                                    }}
                                                    disabled={!job?.result?.pipeline || !authToken || pendingAction === `saveJobStrategy:${String(job?.id || '')}`}
                                                >
                                                    Save As Strategy
                                                </button>
                                                <button
                                                    type='button'
                                                    className='batchActionButton primary'
                                                    onClick={(event) => {
                                                        event.stopPropagation()
                                                        void handleLoadResultsFromJob(job)
                                                    }}
                                                    disabled={!job?.result?.pipeline}
                                                >
                                                    Open Full Results
                                                </button>
                                                <button
                                                    type='button'
                                                    className='batchActionButton'
                                                    onClick={(event) => {
                                                        event.stopPropagation()
                                                        handleOpenResearchArchive(job)
                                                    }}
                                                    disabled={!job?.run_id}
                                                >
                                                    Open Research
                                                </button>
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
                        ) : (
                            <div className='batchEmpty'>
                                {reportSource === 'latest_batch'
                                    ? 'No batch jobs matched the filters for the latest batch.'
                                    : 'No batch jobs matched the current filters.'}
                            </div>
                        )}
                    </div>

                    <div className='batchRuntimeCard batchDashboardInspector'>
                        {selectedRuntimeJob ? (
                            <div className='batchRuntimeInspector'>
                                <div className='batchPanelHeader'>
                                    <div>
                                        <div className='batchPanelTitle'>Selected run</div>
                                        <div className='batchPanelMeta'>
                                            {selectedRuntimeJob?.run_label || selectedRuntimeJob?.phase_label || `Pipeline job #${selectedRuntimeJob?.id}`}
                                        </div>
                                    </div>
                                    <div className={`batchRuntimeBadge is-${String(selectedRuntimeJob?.status || 'idle').toLowerCase()}`}>
                                        {String(selectedRuntimeJob?.status || 'idle')}
                                    </div>
                                </div>
                                <div className='batchProgressTrack'>
                                    <div className='batchProgressFill' style={{ width: `${selectedRuntimeProgress}%` }} />
                                </div>
                                <div className='batchRuntimeMeta'>
                                    <span>{selectedRuntimeProgress}%</span>
                                    <span>{selectedRuntimeJob?.phase_label || 'Queued'}</span>
                                    <span>{String(selectedRuntimeJob?.result?.pipeline?.chart?.symbol || '--')} · {String(selectedRuntimeJob?.result?.pipeline?.chart?.timeframe || '--')}</span>
                                    <span>Duration {formatDurationSeconds(getElapsedSeconds(selectedRuntimeJob?.started_at, selectedRuntimeJob?.finished_at))}</span>
                                </div>
                                <div className='batchRuntimeInspectorGrid'>
                                    <div className='batchSummaryCard'>
                                        <span>Current detail</span>
                                        <strong>{selectedRuntimeJob?.detail || 'No detail available yet.'}</strong>
                                    </div>
                                    <div className='batchSummaryCard'>
                                        <span>Error</span>
                                        <strong>{selectedRuntimeJob?.error || 'None'}</strong>
                                    </div>
                                    <div className='batchSummaryCard'>
                                        <span>Created</span>
                                        <strong>{formatDateTime(selectedRuntimeJob?.created_at)}</strong>
                                    </div>
                                    <div className='batchSummaryCard'>
                                        <span>Started</span>
                                        <strong>{formatDateTime(selectedRuntimeJob?.started_at)}</strong>
                                    </div>
                                    <div className='batchSummaryCard'>
                                        <span>Finished</span>
                                        <strong>{formatDateTime(selectedRuntimeJob?.finished_at)}</strong>
                                    </div>
                                    <div className='batchSummaryCard'>
                                        <span>Duration</span>
                                        <strong>{formatDurationSeconds(getElapsedSeconds(selectedRuntimeJob?.started_at, selectedRuntimeJob?.finished_at))}</strong>
                                    </div>
                                    <div className='batchSummaryCard'>
                                        <span>Archived report</span>
                                        <strong>{selectedRuntimeJob?.run_id ? `Run #${selectedRuntimeJob.run_id}` : 'Not archived yet'}</strong>
                                    </div>
                                </div>
                                <div className='batchActionRow'>
                                    <button
                                        type='button'
                                        className='batchActionButton'
                                        onClick={() => void handleSaveJobAsStrategy(selectedRuntimeJob)}
                                        disabled={
                                            !selectedRuntimeJob?.result?.pipeline
                                            || !authToken
                                            || pendingAction === `saveJobStrategy:${String(selectedRuntimeJob?.id || '')}`
                                        }
                                    >
                                        Save As Strategy
                                    </button>
                                    <button
                                        type='button'
                                        className='batchActionButton primary'
                                        onClick={() => void handleLoadResultsFromJob(selectedRuntimeJob)}
                                        disabled={!selectedRuntimeJob?.result?.pipeline}
                                    >
                                        Open Full Results
                                    </button>
                                    <button
                                        type='button'
                                        className='batchActionButton'
                                        onClick={() => handleOpenResearchArchive(selectedRuntimeJob)}
                                        disabled={!selectedRuntimeJob?.run_id}
                                    >
                                        Open Research
                                    </button>
                                </div>
                            </div>
                        ) : (
                            <div className='batchEmpty'>Select a run above to inspect its runtime details.</div>
                        )}
                    </div>
                </div>
            ) : null}

        </div>
    )
}

export default Batch

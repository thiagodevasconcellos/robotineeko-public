import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import { TIMEFRAME_OPTIONS } from '/src/utils/timeframes.js'
import './Neural.css'

const NEURAL_PANEL_STORAGE_KEY = 'robotineeko_neural_panel_state_v1'

function FieldShell({ label, description = '', children }) {
    return (
        <div className='field'>
            {label ? <label>{label}</label> : null}
            {children}
            {description ? <div className='fieldDescription'>{description}</div> : null}
        </div>
    )
}

function NeuralActionIcon({ type }) {
    if (type === 'archive') {
        return (
            <svg viewBox='0 0 16 16' aria-hidden='true' focusable='false'>
                <path d='M2.5 3.5h11v3h-11z' fill='none' stroke='currentColor' strokeWidth='1.3' />
                <path d='M3.5 6.5h9v6h-9z' fill='none' stroke='currentColor' strokeWidth='1.3' />
                <path d='M6 9h4' fill='none' stroke='currentColor' strokeWidth='1.3' strokeLinecap='round' />
            </svg>
        )
    }

    if (type === 'delete-file') {
        return (
            <svg viewBox='0 0 16 16' aria-hidden='true' focusable='false'>
                <path d='M5 2.5h6l2 2v8.5h-10v-10.5z' fill='none' stroke='currentColor' strokeWidth='1.3' />
                <path d='M11 2.5v2h2' fill='none' stroke='currentColor' strokeWidth='1.3' />
                <path d='M6 8l4 4M10 8l-4 4' fill='none' stroke='currentColor' strokeWidth='1.3' strokeLinecap='round' />
            </svg>
        )
    }

    if (type === 'delete-run') {
        return (
            <svg viewBox='0 0 16 16' aria-hidden='true' focusable='false'>
                <path d='M5.5 2.5h5l.6 1.5h2.4v1.5h-11v-1.5h2.4z' fill='none' stroke='currentColor' strokeWidth='1.3' strokeLinejoin='round' />
                <path d='M4.5 5.5h7l-.5 8h-6z' fill='none' stroke='currentColor' strokeWidth='1.3' strokeLinejoin='round' />
                <path d='M6.5 7.5v4M9.5 7.5v4' fill='none' stroke='currentColor' strokeWidth='1.3' strokeLinecap='round' />
            </svg>
        )
    }

    return null
}

function formatTimestamp(value) {
    if (!value) {
        return '--'
    }

    try {
        return new Date(Number(value) * 1000).toLocaleString()
    } catch {
        return '--'
    }
}

function formatIsoTimestamp(value) {
    if (!value) {
        return '--'
    }

    try {
        return new Date(value).toLocaleString()
    } catch {
        return '--'
    }
}

function formatScore(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '--'
    }

    return numeric.toFixed(4)
}

function formatPercent(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '--'
    }

    return `${(numeric * 100).toFixed(2)}%`
}

function formatInteger(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '--'
    }

    return String(Math.round(numeric))
}

function formatDuration(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '--'
    }

    return `${numeric.toFixed(2)}s`
}

function formatRuntimeSeconds(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric < 0) {
        return '--'
    }

    const rounded = Math.round(numeric)
    const hours = Math.floor(rounded / 3600)
    const minutes = Math.floor((rounded % 3600) / 60)
    const seconds = rounded % 60

    if (hours > 0) {
        return `${hours}h ${String(minutes).padStart(2, '0')}m ${String(seconds).padStart(2, '0')}s`
    }
    if (minutes > 0) {
        return `${minutes}m ${String(seconds).padStart(2, '0')}s`
    }
    return `${seconds}s`
}

function formatRuntimeAge(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric < 0) {
        return '--'
    }
    return formatRuntimeSeconds(numeric)
}

function formatCount(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '--'
    }

    return Math.round(numeric).toLocaleString()
}

function toFiniteNumber(value) {
    const numeric = Number(value)
    return Number.isFinite(numeric) ? numeric : null
}

function clamp01(value) {
    if (!Number.isFinite(value)) {
        return 0
    }

    return Math.max(0, Math.min(1, value))
}

function scoreHigherIsBetter(value, target) {
    const numeric = toFiniteNumber(value)
    if (numeric === null || target <= 0) {
        return null
    }

    return clamp01(numeric / target)
}

function scoreLowerIsBetter(value, target) {
    const numeric = toFiniteNumber(value)
    if (numeric === null || target <= 0) {
        return null
    }

    if (numeric <= 0) {
        return 1
    }

    return clamp01(target / numeric)
}

function formatRunCompositeScore(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '--'
    }

    return `${numeric.toFixed(1)} / 10`
}

function getRunComparableScore(run) {
    const compositeScore = Number(run?.evaluation?.scoreOutOfTen)
    if (Number.isFinite(compositeScore)) {
        return compositeScore
    }

    const rawScore = Number(run?.score)
    return Number.isFinite(rawScore) ? rawScore : null
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

function buildNeuralRunEvaluation(run, network) {
    if (!run || !network) {
        return null
    }

    const metricsRoot = run.run_type === 'train'
        ? (run.metrics?.validation || run.metrics || null)
        : (run.metrics || null)
    if (!metricsRoot || typeof metricsRoot !== 'object') {
        return null
    }

    const criteria = network.family === 'reinforcement_learning'
        ? [
            {
                key: 'directional_accuracy',
                label: 'Directional accuracy',
                targetLabel: '>= 55.00%',
                valueFormat: 'percent',
                actualValue: metricsRoot.directional_accuracy,
                targetValue: 0.55,
                weight: 0.28,
                score: scoreHigherIsBetter(metricsRoot.directional_accuracy, 0.55),
            },
            {
                key: 'win_rate',
                label: 'Win rate',
                targetLabel: '>= 52.00%',
                valueFormat: 'percent',
                actualValue: metricsRoot.win_rate,
                targetValue: 0.52,
                weight: 0.18,
                score: scoreHigherIsBetter(metricsRoot.win_rate, 0.52),
            },
            {
                key: 'profit_factor',
                label: 'Profit factor',
                targetLabel: '>= 1.20',
                valueFormat: 'score',
                actualValue: metricsRoot.profit_factor,
                targetValue: 1.20,
                weight: 0.24,
                score: scoreHigherIsBetter(metricsRoot.profit_factor, 1.20),
            },
            {
                key: 'max_drawdown',
                label: 'Max drawdown',
                targetLabel: '<= 0.10',
                valueFormat: 'score',
                actualValue: Math.abs(toFiniteNumber(metricsRoot.max_drawdown) ?? 0),
                targetValue: 0.10,
                weight: 0.18,
                score: scoreLowerIsBetter(Math.abs(toFiniteNumber(metricsRoot.max_drawdown) ?? 0), 0.10),
            },
            {
                key: 'mean_reward',
                label: 'Mean reward',
                targetLabel: '>= 0.0010',
                valueFormat: 'score',
                actualValue: metricsRoot.mean_reward,
                targetValue: 0.0010,
                weight: 0.12,
                score: scoreHigherIsBetter(metricsRoot.mean_reward, 0.0010),
            },
        ]
        : [
            {
                key: 'signal_directional_accuracy',
                label: 'Signal accuracy',
                targetLabel: '>= 60.00%',
                valueFormat: 'percent',
                actualValue: metricsRoot.signal_directional_accuracy,
                targetValue: 0.60,
                weight: 0.40,
                score: scoreHigherIsBetter(metricsRoot.signal_directional_accuracy, 0.60),
            },
            {
                key: 'signal_mae',
                label: 'Signal MAE',
                targetLabel: '<= 0.1800',
                valueFormat: 'score',
                actualValue: metricsRoot.signal_mae,
                targetValue: 0.1800,
                weight: 0.24,
                score: scoreLowerIsBetter(metricsRoot.signal_mae, 0.1800),
            },
            {
                key: 'signal_rmse',
                label: 'Signal RMSE',
                targetLabel: '<= 0.2400',
                valueFormat: 'score',
                actualValue: metricsRoot.signal_rmse,
                targetValue: 0.2400,
                weight: 0.16,
                score: scoreLowerIsBetter(metricsRoot.signal_rmse, 0.2400),
            },
            {
                key: 'mean_predicted_signal',
                label: 'Predicted conviction',
                targetLabel: '>= 0.0800 abs mean',
                valueFormat: 'score',
                actualValue: Math.abs(toFiniteNumber(metricsRoot.mean_predicted_signal) ?? 0),
                targetValue: 0.0800,
                weight: 0.08,
                score: scoreHigherIsBetter(Math.abs(toFiniteNumber(metricsRoot.mean_predicted_signal) ?? 0), 0.0800),
            },
            {
                key: 'mean_actual_signal',
                label: 'Signal coverage',
                targetLabel: '>= 0.0800 abs mean',
                valueFormat: 'score',
                actualValue: Math.abs(toFiniteNumber(metricsRoot.mean_actual_signal) ?? 0),
                targetValue: 0.0800,
                weight: 0.08,
                score: scoreHigherIsBetter(Math.abs(toFiniteNumber(metricsRoot.mean_actual_signal) ?? 0), 0.0800),
            },
        ]

    const activeCriteria = criteria.filter((criterion) => criterion.score !== null)
    if (!activeCriteria.length) {
        return null
    }

    const totalWeight = activeCriteria.reduce((sum, criterion) => sum + criterion.weight, 0)
    const weightedScore = totalWeight > 0
        ? activeCriteria.reduce((sum, criterion) => sum + (criterion.weight * criterion.score), 0) / totalWeight
        : 0

    return {
        scoreOutOfTen: weightedScore * 10,
        percentScore: weightedScore,
        criteria: activeCriteria.map((criterion) => ({
            ...criterion,
            achievedPct: criterion.score * 100,
            contributionPoints: criterion.score * criterion.weight * 10,
            maxContributionPoints: criterion.weight * 10,
        })),
    }
}

function formatMetricValue(value, format = 'score') {
    if (format === 'percent') {
        return formatPercent(value)
    }
    if (format === 'integer') {
        return formatInteger(value)
    }
    if (format === 'duration') {
        return formatDuration(value)
    }
    return formatScore(value)
}

function formatSplitSummary(splitSizes) {
    if (!splitSizes) {
        return '--'
    }

    return `train ${splitSizes.train} / validation ${splitSizes.validation} / test ${splitSizes.test}`
}

function formatMetricDelta(currentValue, compareValue, format = 'score') {
    const currentNumeric = Number(currentValue)
    const compareNumeric = Number(compareValue)
    if (!Number.isFinite(currentNumeric) || !Number.isFinite(compareNumeric)) {
        return ''
    }

    const delta = currentNumeric - compareNumeric
    if (Math.abs(delta) < 1e-12) {
        return 'no change'
    }

    const sign = delta > 0 ? '+' : ''
    if (format === 'percent') {
        return `${sign}${(delta * 100).toFixed(2)} pp`
    }
    if (format === 'integer') {
        return `${sign}${Math.round(delta)}`
    }
    return `${sign}${delta.toFixed(4)}`
}

const NETWORK_MENU_SECTIONS = [
    { id: 'feed_forward', label: 'Feed Forward' },
    { id: 'lstm', label: 'LSTM' },
    { id: 'convolutional', label: 'Convolutional' },
    { id: 'reinforcement', label: 'Reinforcement' },
]

function getNetworkMenuSection(network) {
    const architectureType = String(network?.architecture_type || '').trim().toLowerCase()
    if (architectureType) {
        return architectureType
    }

    const family = String(network?.family || '').trim().toLowerCase()
    if (family === 'reinforcement_learning') {
        return 'reinforcement'
    }

    return 'feed_forward'
}

function groupNetworksByMenuSection(networks = []) {
    const groups = new Map(
        NETWORK_MENU_SECTIONS.map((section) => [section.id, {
            id: section.id,
            label: section.label,
            items: [],
        }])
    )

    for (const network of networks) {
        const sectionId = getNetworkMenuSection(network)
        const current = groups.get(sectionId) || {
            id: sectionId,
            label: String(network?.architecture_label || sectionId),
            items: [],
        }
        current.items.push(network)
        groups.set(sectionId, current)
    }

    return Array.from(groups.values())
}

function getMetricValueAtPath(source, metricPath = '') {
    if (!source || !metricPath) {
        return undefined
    }

    const segments = String(metricPath)
        .split('.')
        .map((segment) => segment.trim())
        .filter(Boolean)

    let current = source
    for (const segment of segments) {
        if (current == null || typeof current !== 'object' || !(segment in current)) {
            return undefined
        }
        current = current[segment]
    }

    return current
}

function resolveMetricsSource(section, sources) {
    const source = sources?.[section?.source] || null
    if (!source) {
        return null
    }

    if (!section?.metric_root) {
        return source
    }

    const rooted = getMetricValueAtPath(source, section.metric_root)
    return rooted && typeof rooted === 'object' ? rooted : null
}

function stringifyConfigValue(value) {
    if (typeof value === 'boolean') {
        return value ? 'true' : 'false'
    }
    if (typeof value === 'number') {
        return Number.isFinite(value) ? String(value) : '--'
    }
    if (value == null || value === '') {
        return '--'
    }
    if (typeof value === 'object') {
        try {
            return JSON.stringify(value)
        } catch {
            return String(value)
        }
    }
    return String(value)
}

function getNetworkDisplayLabel(network) {
    return String(network?.display_label || network?.alias || network?.label || network?.id || 'Unknown neural network')
}

function getRunDisplayLabel(run) {
    if (!run) {
        return 'Unknown run'
    }

    const runType = String(run.run_type || 'run').trim()
    const shortId = String(run.id || '').trim().slice(0, 8)
    const titledRunType = runType ? `${runType.charAt(0).toUpperCase()}${runType.slice(1)}` : 'Run'
    return shortId ? `${titledRunType} · ${shortId}` : titledRunType
}

function getRunGeneratedIndexMap(runs) {
    const source = Array.isArray(runs) ? [...runs] : []
    source.sort((left, right) => {
        const startedDelta = Number(left?.started_at || 0) - Number(right?.started_at || 0)
        if (Math.abs(startedDelta) > 1e-12) {
            return startedDelta
        }
        return String(left?.id || '').localeCompare(String(right?.id || ''))
    })

    return new Map(source.map((run, index) => [String(run?.id || ''), index + 1]))
}

function formatRunGeneratedIndex(run, indexMap) {
    const value = indexMap?.get(String(run?.id || ''))
    return Number.isFinite(value) ? String(value) : '--'
}

function formatRunCompareOptionLabel(run, indexMap) {
    const indexLabel = formatRunGeneratedIndex(run, indexMap)
    const typeLabel = String(run?.run_type || 'run').trim().toUpperCase() || 'RUN'
    const scoreLabel = formatRunCompositeScore(run?.evaluation?.scoreOutOfTen)
    return `${indexLabel} - ${typeLabel} ${scoreLabel}`
}

function getRunErrorPreview(run) {
    const error = String(run?.error || '').trim()
    if (!error) {
        return ''
    }

    return error.length > 120
        ? `${error.slice(0, 117)}...`
        : error
}

function shouldHideGuestRun(run) {
    const status = String(run?.status || '').trim().toLowerCase()
    const error = String(run?.error || '').trim()
    return Boolean(error) || ['failed', 'cancelled', 'error'].includes(status)
}

function getRunDirectionValue(run) {
    const trainMetrics = run?.metrics?.validation || run?.metrics || {}
    const numeric = Number(
        trainMetrics?.signal_directional_accuracy
        ?? trainMetrics?.directional_accuracy
    )
    return Number.isFinite(numeric) ? numeric : null
}

function getRunSortMeta(mode) {
    const normalized = String(mode || '').trim().toLowerCase()
    const [column = 'started', direction = 'desc'] = normalized.split('_')
    return {
        column,
        direction: direction === 'asc' ? 'asc' : 'desc',
    }
}

function buildNeuralRunExportPayload(network, run) {
    return {
        exported_at: new Date().toISOString(),
        network: {
            id: String(network?.id || ''),
            label: getNetworkDisplayLabel(network),
            family: String(network?.family || ''),
            network_type: String(network?.network_type || ''),
            task_label: String(network?.task_label || ''),
        },
        run: {
            id: String(run?.id || ''),
            type: String(run?.run_type || ''),
            status: String(run?.status || ''),
            started_at: run?.started_at ?? null,
            started_at_label: formatTimestamp(run?.started_at),
            duration_seconds: Number.isFinite(Number(run?.duration_seconds))
                ? Number(run.duration_seconds)
                : null,
            duration_label: formatDuration(run?.duration_seconds),
            run_score: Number.isFinite(Number(run?.evaluation?.scoreOutOfTen))
                ? Number(run.evaluation.scoreOutOfTen)
                : null,
            raw_score: Number.isFinite(Number(run?.score)) ? Number(run.score) : null,
            promoted_to_best: Boolean(run?.promoted_to_best),
            is_favorite: Boolean(run?.is_favorite),
            is_baseline: Boolean(run?.is_baseline),
            is_archived: Boolean(run?.is_archived),
            note: String(run?.note || ''),
            error: String(run?.error || ''),
            artifact: run?.artifact || null,
            config: run?.config || null,
            metrics: run?.metrics || null,
            evaluation: run?.evaluation || null,
        },
    }
}

function buildRunsTableExportPayload(network, runs = [], options = {}) {
    const safeRuns = Array.isArray(runs) ? runs : []
    const normalizedOptions = options && typeof options === 'object' ? options : {}

    return {
        exported_at: new Date().toISOString(),
        network: {
            id: String(network?.id || ''),
            label: getNetworkDisplayLabel(network),
            family: String(network?.family || ''),
            network_type: String(network?.network_type || ''),
            task_label: String(network?.task_label || ''),
        },
        table: {
            label: String(normalizedOptions.label || 'Filtered neural runs table'),
            filters: normalizedOptions.filters || {},
            total_runs: Number(normalizedOptions.totalRuns || safeRuns.length),
            visible_runs: safeRuns.length,
            rows: safeRuns.map((run) => {
                const validationMetrics = run?.metrics?.validation || run?.metrics || {}
                return {
                    run_id: String(run?.id || ''),
                    type: String(run?.run_type || ''),
                    status: String(run?.status || ''),
                    run_score: Number.isFinite(Number(run?.evaluation?.scoreOutOfTen)) ? Number(run.evaluation.scoreOutOfTen) : null,
                    raw_score: Number.isFinite(Number(run?.score)) ? Number(run.score) : null,
                    started_at: run?.started_at ?? null,
                    started_at_label: formatTimestamp(run?.started_at),
                    duration_seconds: Number.isFinite(Number(run?.duration_seconds)) ? Number(run.duration_seconds) : null,
                    duration_label: formatDuration(run?.duration_seconds),
                    promoted_to_best: Boolean(run?.promoted_to_best),
                    is_favorite: Boolean(run?.is_favorite),
                    is_baseline: Boolean(run?.is_baseline),
                    is_archived: Boolean(run?.is_archived),
                    artifact_exists: Boolean(run?.artifact?.exists),
                    artifact_filename: String(run?.artifact?.filename || ''),
                    signal_directional_accuracy: Number.isFinite(Number(validationMetrics?.signal_directional_accuracy)) ? Number(validationMetrics.signal_directional_accuracy) : null,
                    signal_mae: Number.isFinite(Number(validationMetrics?.signal_mae)) ? Number(validationMetrics.signal_mae) : null,
                    signal_rmse: Number.isFinite(Number(validationMetrics?.signal_rmse)) ? Number(validationMetrics.signal_rmse) : null,
                    mean_predicted_signal: Number.isFinite(Number(validationMetrics?.mean_predicted_signal)) ? Number(validationMetrics.mean_predicted_signal) : null,
                    mean_actual_signal: Number.isFinite(Number(validationMetrics?.mean_actual_signal)) ? Number(validationMetrics.mean_actual_signal) : null,
                    long_bias_rate: Number.isFinite(Number(validationMetrics?.long_bias_rate)) ? Number(validationMetrics.long_bias_rate) : null,
                    short_bias_rate: Number.isFinite(Number(validationMetrics?.short_bias_rate)) ? Number(validationMetrics.short_bias_rate) : null,
                    note: String(run?.note || ''),
                    error: String(run?.error || ''),
                }
            }),
        },
    }
}

async function copyTextToClipboard(text) {
    const safeText = String(text || '')
    if (!safeText.trim()) {
        throw new Error('Nothing to copy.')
    }

    await navigator.clipboard.writeText(safeText)
}

function readStoredNeuralPanelState() {
    if (typeof window === 'undefined') {
        return {}
    }

    try {
        const raw = window.localStorage.getItem(NEURAL_PANEL_STORAGE_KEY) || ''
        if (!raw) {
            return {}
        }
        const parsed = JSON.parse(raw)
        return parsed && typeof parsed === 'object' ? parsed : {}
    } catch {
        return {}
    }
}

function writeStoredNeuralPanelState(state) {
    if (typeof window === 'undefined') {
        return
    }

    try {
        window.localStorage.setItem(NEURAL_PANEL_STORAGE_KEY, JSON.stringify(state))
    } catch {
        // Ignore storage write failures so the neural panel keeps working normally.
    }
}

function groupParameterSchema(network) {
    const schema = Array.isArray(network?.parameter_schema) ? network.parameter_schema : []
    const declaredGroups = Array.isArray(network?.parameter_groups) ? network.parameter_groups : []
    const groupedFields = new Map()

    for (const group of declaredGroups) {
        if (!group?.id) {
            continue
        }
        groupedFields.set(group.id, {
            id: group.id,
            label: group.label || group.id,
            fields: [],
        })
    }

    for (const field of schema) {
        const groupId = field?.group || 'general'
        if (!groupedFields.has(groupId)) {
            groupedFields.set(groupId, {
                id: groupId,
                label: groupId.charAt(0).toUpperCase() + groupId.slice(1),
                fields: [],
            })
        }
        groupedFields.get(groupId).fields.push(field)
    }

    return Array.from(groupedFields.values()).filter((group) => group.fields.length > 0)
}

const DATASET_CONTEXT_FIELD_KEYS = new Set(['symbol', 'timeframe', 'bars'])
const DATASET_SPLIT_FIELD_KEYS = new Set(['validationSplit', 'testSplit'])

function formatTimeframeLabel(value) {
    const normalized = String(value || '').trim().toUpperCase()
    const match = TIMEFRAME_OPTIONS.find(([optionValue]) => optionValue === normalized)
    return match?.[1] || normalized || '--'
}

function formatReadonlyFieldValue(field, value) {
    if (field?.key === 'bars') {
        const numeric = Number(value)
        return Number.isFinite(numeric) ? Math.round(numeric).toLocaleString() : '--'
    }

    if (field?.key === 'timeframe') {
        return formatTimeframeLabel(value)
    }

    return String(value ?? '').trim() || '--'
}

function getFieldLabel(field) {
    if (field?.key === 'symbol') {
        return 'Dataset symbol'
    }
    if (field?.key === 'timeframe') {
        return 'Dataset timeframe'
    }
    if (field?.key === 'bars') {
        return 'Dataset bars'
    }
    return field?.label || ''
}

function getFieldDescription(field) {
    if (field?.key === 'symbol') {
        return 'Defines which symbol the isolated neural dataset should request.'
    }
    if (field?.key === 'timeframe') {
        return 'Defines which timeframe the isolated neural dataset should request.'
    }
    if (field?.key === 'bars') {
        return 'Defines how many candles the isolated neural dataset should load.'
    }
    return field?.description || ''
}

function buildConfigSubgroups(group) {
    const fields = Array.isArray(group?.fields) ? group.fields : []
    const subgroups = []

    if (group?.id === 'dataset') {
        const datasetContextFields = fields.filter((field) => DATASET_CONTEXT_FIELD_KEYS.has(field?.key))
        const datasetSplitFields = fields.filter((field) => DATASET_SPLIT_FIELD_KEYS.has(field?.key))
        const remainingFields = fields.filter((field) => !DATASET_CONTEXT_FIELD_KEYS.has(field?.key) && !DATASET_SPLIT_FIELD_KEYS.has(field?.key))

        if (datasetContextFields.length) {
            subgroups.push({
                id: `${group.id}-context`,
                label: 'Market context',
                hint: 'These fields define the isolated market context the neural worker will request.',
                fields: datasetContextFields,
            })
        }
        if (datasetSplitFields.length) {
            subgroups.push({
                id: `${group.id}-split`,
                label: 'Dataset split',
                fields: datasetSplitFields,
            })
        }
        if (remainingFields.length) {
            subgroups.push({
                id: `${group.id}-settings`,
                label: 'Dataset settings',
                fields: remainingFields,
            })
        }

        return subgroups
    }

    return [{
        id: `${group?.id || 'group'}-default`,
        label: '',
        fields,
    }]
}

function normalizeHiddenLayers(input) {
    const source = Array.isArray(input) ? input : []
    const normalized = source
        .map((layer, index) => {
            if (!layer || typeof layer !== 'object') {
                return null
            }
            const size = Math.max(4, Number(layer.size) || 32)
            const activation = String(layer.activation || 'tanh').trim().toLowerCase()
            const dropout = Math.max(0, Math.min(0.9, Number(layer.dropout) || 0))
            return {
                id: String(layer.id || `layer_${index + 1}`),
                size,
                activation: activation || 'tanh',
                dropout,
            }
        })
        .filter(Boolean)
    return normalized.length ? normalized : [{ id: 'layer_1', size: 32, activation: 'tanh', dropout: 0 }]
}

let neuralLayerIdCounter = 0

function buildNeuralLayerId() {
    neuralLayerIdCounter += 1
    return `layer_${neuralLayerIdCounter}`
}

function normalizeNormalizationColumns(input, network) {
    const allowedColumns = new Set(
        Array.isArray(network?.normalization_targets)
            ? network.normalization_targets.map((target) => String(target?.id || '').trim()).filter(Boolean)
            : [],
    )
    const source = Array.isArray(input) ? input : []
    const normalized = source
        .map((item) => String(item || '').trim())
        .filter((item, index, collection) => item && allowedColumns.has(item) && collection.indexOf(item) === index)
    return normalized
}

function NeuralNormalizationSelector({ network, value = [], onChange, readOnly = false }) {
    const targets = Array.isArray(network?.normalization_targets) ? network.normalization_targets : []
    const selected = new Set(normalizeNormalizationColumns(value, network))

    return (
        <div className='neuralNormalizationSelector'>
            <div className='neuralSectionHint'>
                {readOnly
                    ? 'Green items are normalized; gray items stay in their raw scale.'
                    : 'Click the inputs that should be normalized before training. Green items are normalized; gray items stay in their raw scale.'}
            </div>
            <div className='neuralNormalizationGrid'>
                {targets.map((target) => {
                    const targetId = String(target?.id || '').trim()
                    const isSelected = selected.has(targetId)
                    return (
                        <button
                            key={targetId}
                            type='button'
                            className={`neuralNormalizationChip ${isSelected ? 'active' : ''}`}
                            disabled={readOnly}
                            onClick={() => {
                                if (readOnly) {
                                    return
                                }
                                const nextValues = isSelected
                                    ? Array.from(selected).filter((item) => item !== targetId)
                                    : [...Array.from(selected), targetId]
                                onChange?.(normalizeNormalizationColumns(nextValues, network))
                            }}
                        >
                            {target?.label || targetId}
                        </button>
                    )
                })}
            </div>
        </div>
    )
}

function supportsArchitectureBuilder(network) {
    return [
        'temporal_cnn_indicator_fusion_v1',
        'neural_market_regime_cnn_v1',
        'ema_low_adx_setup_quality_cnn_v1',
        'ema_low_adx_setup_quality_cnn_v2',
        'ema_low_adx_setup_quality_cnn_v3',
        'ema_low_adx_setup_quality_cnn_v4',
        'ema_low_adx_setup_quality_cnn_v5',
        'ema_low_adx_setup_quality_cnn_v6',
        'ema_low_adx_setup_quality_cnn_v7',
        'micro_cost_edge_cnn_v1',
        'micro_cost_edge_cnn_v2',
        'micro_cost_edge_cnn_v3',
        'micro_cost_edge_cnn_v4',
        'micro_cost_edge_cnn_v5',
        'candle_reversal_cnn_v1',
        'candle_reversal_cnn_v2',
        'candle_reversal_cnn_v3',
        'candle_reversal_cnn_v4',
        'candle_reversal_cnn_v5',
        'candle_reversal_cnn_v6',
        'candle_reversal_cnn_v7',
        'candle_reversal_cnn_v7_1',
        'candle_reversal_cnn_v8',
        'candle_reversal_cnn_v9',
        'candle_reversal_cnn_v10',
        'candle_reversal_cnn_v10_1',
        'candle_reversal_cnn_v11',
        'candle_reversal_cnn_v11_scores_only',
        'candle_reversal_cnn_v12_scores_only',
        'candle_reversal_setup_quality_cnn_v1',
    ].includes(String(network?.id || ''))
}

function normalizeConfigFromNetwork(network) {
    const defaults = network?.defaults || {}
    const schema = Array.isArray(network?.parameter_schema) ? network.parameter_schema : []
    const config = {}

    for (const field of schema) {
        const key = field?.key
        if (!key) {
            continue
        }

        if (field.type === 'boolean') {
            config[key] = defaults[key] ?? false
            continue
        }

        config[key] = defaults[key] ?? ''
    }

    if (!String(config.timeframe || '').trim()) {
        config.timeframe = 'M1'
    }

    if (supportsArchitectureBuilder(network)) {
        config.normalizationColumns = normalizeNormalizationColumns(defaults.normalizationColumns, network)
        config.hiddenLayers = normalizeHiddenLayers(defaults.hiddenLayers)
    }

    return config
}

function coerceFieldValue(field, rawValue) {
    if (field?.type === 'boolean') {
        return Boolean(rawValue)
    }

    if (field?.type === 'number') {
        const numeric = Number(rawValue)
        return Number.isFinite(numeric) ? numeric : Number(field?.min ?? 0)
    }

    return String(rawValue ?? '')
}

function sanitizeImportedConfig(input, network) {
    const schema = Array.isArray(network?.parameter_schema) ? network.parameter_schema : []
    const nextConfig = normalizeConfigFromNetwork(network)

    for (const field of schema) {
        if (!field?.key || !(field.key in (input || {}))) {
            continue
        }
        nextConfig[field.key] = coerceFieldValue(field, input[field.key])
    }

    if (!String(nextConfig.timeframe || '').trim()) {
        nextConfig.timeframe = 'M1'
    }

    if (supportsArchitectureBuilder(network)) {
        const legacyNormalizeVolume = input?.normalizeVolume
        const legacyNormalizationMode = String(input?.normalizationMode || '').trim().toLowerCase()
        let nextNormalizationColumns = input?.normalizationColumns
        if (!Array.isArray(nextNormalizationColumns)) {
            if (legacyNormalizationMode === 'all_inputs') {
                nextNormalizationColumns = Array.isArray(network?.normalization_targets)
                    ? network.normalization_targets.map((target) => target.id)
                    : []
            } else if (legacyNormalizationMode === 'volume' || legacyNormalizeVolume) {
                nextNormalizationColumns = ['ff_volume']
            } else {
                nextNormalizationColumns = nextConfig.normalizationColumns
            }
        }
        nextConfig.normalizationColumns = normalizeNormalizationColumns(nextNormalizationColumns, network)
        nextConfig.hiddenLayers = normalizeHiddenLayers(input?.hiddenLayers || nextConfig.hiddenLayers)
    }

    return nextConfig
}

function sanitizeStoredDraftConfig(input, network) {
    const nextConfig = sanitizeImportedConfig(input, network)
    const networkId = String(network?.id || '').trim()
    const legacyBarCap = LEGACY_DRAFT_BAR_CAPS[networkId]
    const draftBars = Number(nextConfig?.bars)

    if (Number.isFinite(legacyBarCap) && Number.isFinite(draftBars) && draftBars > legacyBarCap) {
        nextConfig.bars = legacyBarCap
    }

    return nextConfig
}

function sanitizeStoredDraftMap(storedDrafts, networks) {
    const safeDrafts = storedDrafts && typeof storedDrafts === 'object' ? storedDrafts : {}
    const networkMap = new Map(
        (Array.isArray(networks) ? networks : [])
            .filter((network) => network?.id)
            .map((network) => [network.id, network]),
    )
    const nextDrafts = {}
    let changed = false

    for (const [networkId, draft] of Object.entries(safeDrafts)) {
        const network = networkMap.get(networkId)
        if (!network) {
            changed = true
            continue
        }

        const sanitizedDraft = sanitizeStoredDraftConfig(draft, network)
        nextDrafts[networkId] = sanitizedDraft

        if (JSON.stringify(sanitizedDraft) !== JSON.stringify(draft || {})) {
            changed = true
        }
    }

    return {
        drafts: nextDrafts,
        changed,
    }
}

function renderField(field, value, onChange, options = {}) {
    if (options.readOnly) {
        return (
            <div className='neuralReadonlyFieldValue'>
                {formatReadonlyFieldValue(field, value)}
            </div>
        )
    }

    if (field.type === 'boolean') {
        return (
            <label className='checkboxField'>
                <input
                    type='checkbox'
                    checked={Boolean(value)}
                    onChange={(event) => onChange(event.target.checked)}
                />
                <span>{field.label}</span>
            </label>
        )
    }

    if (field?.key === 'timeframe') {
        return (
            <select
                value={String(value ?? 'M1')}
                onChange={(event) => onChange(event.target.value)}
            >
                {TIMEFRAME_OPTIONS.map(([optionValue, optionLabel]) => (
                    <option key={optionValue} value={optionValue}>
                        {optionLabel}
                    </option>
                ))}
            </select>
        )
    }

    if (Array.isArray(field?.options) && field.options.length > 0) {
        return (
            <select
                value={String(value ?? '')}
                onChange={(event) => onChange(event.target.value)}
            >
                {field.options.map((option) => (
                    <option key={option.value} value={option.value}>
                        {option.label || option.value}
                    </option>
                ))}
            </select>
        )
    }

    const type = field.type === 'number' ? 'number' : 'text'

    return (
        <input
            type={type}
            value={value ?? ''}
            min={field.min}
            max={field.max}
            step={field.step || (field.type === 'number' ? 'any' : undefined)}
            onChange={(event) => onChange(event.target.value)}
        />
    )
}

function NeuralMetricCard({ label, value, hint = '' }) {
    return (
        <div className='neuralMetricCard'>
            <div className='neuralMetricLabel'>{label}</div>
            <div className='neuralMetricValue'>{value}</div>
            {hint ? <div className='neuralMetricHint'>{hint}</div> : null}
        </div>
    )
}

function NeuralRunBadge({ children, tone = 'neutral' }) {
    return <span className={`neuralRunBadge ${tone}`}>{children}</span>
}

function NeuralMetricSection({ title, metrics = [], hint = '' }) {
    if (!metrics.length) {
        return null
    }

    return (
        <div className='neuralMetricSection'>
            <div className='neuralMetricSectionHeader'>
                <div className='neuralMetricSectionTitle'>{title}</div>
                {hint ? <div className='neuralMetricSectionHint'>{hint}</div> : null}
            </div>
            <div className='neuralMetricList'>
                {metrics.map((metric) => (
                    <div key={metric.key} className='neuralMetricRow'>
                        <span className='neuralMetricRowLabel'>
                            {metric.label}
                            {metric.delta ? <span className='neuralMetricRowDelta'>{metric.delta}</span> : null}
                        </span>
                        <span className='neuralMetricRowValue'>{metric.value}</span>
                    </div>
                ))}
            </div>
        </div>
    )
}

function NeuralLayerEditor({ layers = [], onChange, readOnly = false }) {
    const safeLayers = normalizeHiddenLayers(layers)

    function updateLayer(layerId, patch) {
        if (readOnly) {
            return
        }
        onChange?.(safeLayers.map((layer) => (
            layer.id === layerId ? { ...layer, ...patch } : layer
        )))
    }

    function moveLayer(layerId, direction) {
        if (readOnly) {
            return
        }
        const currentIndex = safeLayers.findIndex((layer) => layer.id === layerId)
        const nextIndex = currentIndex + direction
        if (currentIndex < 0 || nextIndex < 0 || nextIndex >= safeLayers.length) {
            return
        }
        const nextLayers = [...safeLayers]
        const [item] = nextLayers.splice(currentIndex, 1)
        nextLayers.splice(nextIndex, 0, item)
        onChange?.(nextLayers)
    }

    function removeLayer(layerId) {
        if (readOnly) {
            return
        }
        const nextLayers = safeLayers.filter((layer) => layer.id !== layerId)
        onChange?.(normalizeHiddenLayers(nextLayers))
    }

    function addLayer() {
        if (readOnly) {
            return
        }
        onChange?.([
            ...safeLayers,
            { id: buildNeuralLayerId(), size: 32, activation: 'tanh', dropout: 0 },
        ])
    }

    return (
        <div className='neuralLayerEditor'>
            {safeLayers.map((layer, index) => (
                <div key={layer.id} className='neuralLayerCard'>
                    <div className='neuralLayerCardHeader'>
                        <strong>Layer {index + 1}</strong>
                        {!readOnly ? (
                            <div className='neuralLayerCardActions'>
                                <button type='button' onClick={() => moveLayer(layer.id, -1)} disabled={index === 0}>↑</button>
                                <button type='button' onClick={() => moveLayer(layer.id, 1)} disabled={index === safeLayers.length - 1}>↓</button>
                                <button type='button' onClick={() => removeLayer(layer.id)} disabled={safeLayers.length <= 1}>Remove</button>
                            </div>
                        ) : null}
                    </div>
                    <div className='neuralLayerRow'>
                        <FieldShell
                            label='Neurons'
                            description='Defines how many units this dense layer has. More neurons increase model capacity, but also increase training cost and the risk of overfitting.'
                        >
                            <input
                                type='number'
                                min='4'
                                value={layer.size}
                                disabled={readOnly}
                                onChange={(event) => updateLayer(layer.id, { size: Math.max(4, Number(event.target.value) || 32) })}
                            />
                        </FieldShell>
                        <FieldShell
                            label='Activation'
                            description='Controls how this layer transforms its weighted input. Different activations change how easily the network learns non-linear patterns and how strongly it reacts to large values.'
                        >
                            <select
                                value={layer.activation}
                                disabled={readOnly}
                                onChange={(event) => updateLayer(layer.id, { activation: event.target.value })}
                            >
                                <option value='tanh'>tanh</option>
                                <option value='relu'>relu</option>
                                <option value='leaky_relu'>leaky relu</option>
                                <option value='elu'>elu</option>
                                <option value='sigmoid'>sigmoid</option>
                                <option value='linear'>linear</option>
                            </select>
                        </FieldShell>
                        <FieldShell
                            label='Dropout'
                            description='Temporarily drops a fraction of this layer during training to reduce overfitting. Higher values regularize more aggressively, but can also make learning slower or weaker.'
                        >
                            <input
                                type='number'
                                min='0'
                                max='0.9'
                                step='0.05'
                                value={layer.dropout ?? 0}
                                disabled={readOnly}
                                onChange={(event) => updateLayer(layer.id, { dropout: Math.max(0, Math.min(0.9, Number(event.target.value) || 0)) })}
                            />
                        </FieldShell>
                    </div>
                    <div className='neuralSectionHint'>
                        Dense layer with {layer.size} neurons, activation <strong>{layer.activation}</strong>
                        {Number(layer.dropout) > 0 ? ` and dropout ${(Number(layer.dropout) * 100).toFixed(0)}%` : ' and no dropout'}.
                    </div>
                </div>
            ))}
            {!readOnly ? (
                <div className='neuralConfigTransferRow'>
                    <button type='button' onClick={addLayer}>Add layer</button>
                </div>
            ) : null}
        </div>
    )
}

function getArchitecturePanels(network) {
    const family = String(network?.family || '').trim().toLowerCase()
    const architectureType = String(network?.architecture_type || '').trim().toLowerCase()

    if (family === 'reinforcement_learning') {
        const activePanelId = architectureType === 'convolutional'
            ? 'conv_policy'
            : architectureType === 'lstm'
                ? 'lstm_policy'
                : 'mlp_policy'
        return [
            {
                id: 'mlp_policy',
                title: 'MLP policy',
                status: activePanelId === 'mlp_policy' ? 'active' : 'planned',
                hint: activePanelId === 'mlp_policy' ? 'Current flow' : 'Planned',
                description: 'Current PPO setup using flattened observations and dense layers for policy/value learning.',
            },
            {
                id: 'lstm_policy',
                title: 'LSTM policy',
                status: activePanelId === 'lstm_policy' ? 'active' : 'planned',
                hint: activePanelId === 'lstm_policy' ? 'Current flow' : 'Sequence-aware',
                description: 'Planned recurrent panel for policies that should learn temporal state across consecutive candles.',
            },
            {
                id: 'conv_policy',
                title: 'Convolutional policy',
                status: activePanelId === 'conv_policy' ? 'active' : 'planned',
                hint: activePanelId === 'conv_policy' ? 'Current flow' : 'Pattern extractor',
                description: 'Planned convolutional panel for local price-pattern extraction before the PPO policy head.',
            },
        ]
    }

    const activePanelId = architectureType === 'convolutional'
        ? 'conv_regressor'
        : architectureType === 'lstm'
            ? 'lstm_regressor'
            : 'dense_regressor'
    return [
        {
            id: 'dense_regressor',
            title: 'Dense / feed-forward',
            status: activePanelId === 'dense_regressor' ? 'active' : 'planned',
            hint: activePanelId === 'dense_regressor' ? 'Current flow' : 'Engineered baseline',
            description: 'Current supervised architecture using dense hidden layers over the engineered candle feature vector.',
        },
        {
            id: 'lstm_regressor',
            title: 'LSTM',
            status: activePanelId === 'lstm_regressor' ? 'active' : 'planned',
            hint: activePanelId === 'lstm_regressor' ? 'Current flow' : 'Sequence-aware',
            description: 'Planned recurrent panel for supervised models that should ingest rolling windows and preserve temporal memory.',
        },
        {
            id: 'conv_regressor',
            title: 'Convolutional',
            status: activePanelId === 'conv_regressor' ? 'active' : 'planned',
            hint: activePanelId === 'conv_regressor' ? 'Current flow' : 'Pattern extractor',
            description: 'Planned convolutional panel for extracting short-term motifs and local structures from candle sequences.',
        },
    ]
}

function NeuralArchitecturePanels({ network }) {
    const panels = getArchitecturePanels(network)

    return (
        <div className='neuralArchitecturePanels'>
            {panels.map((panel) => (
                <div key={panel.id} className={`neuralArchitecturePanel ${panel.status}`}>
                    <div className='neuralArchitecturePanelHeader'>
                        <strong>{panel.title}</strong>
                        <span className={`neuralArchitectureBadge ${panel.status}`}>{panel.hint}</span>
                    </div>
                    <div className='neuralDescription'>{panel.description}</div>
                </div>
            ))}
        </div>
    )
}

function NeuralRunEvaluationCard({ evaluation, network, comparisonRun = null }) {
    if (!evaluation) {
        return null
    }

    const tone = getEvaluationTone(evaluation.scoreOutOfTen)
    const familyLabel = String(network?.family_label || 'neural').trim().toLowerCase()
    const comparisonEvaluation = comparisonRun?.evaluation || null
    const panels = [
        {
            id: 'selected',
            label: 'Selected run',
            tone,
            evaluation,
        },
        comparisonEvaluation ? {
            id: 'compared',
            label: 'Compared run',
            tone: getEvaluationTone(comparisonEvaluation.scoreOutOfTen),
            evaluation: comparisonEvaluation,
        } : null,
    ].filter(Boolean)

    return (
        <div className={`neuralEvaluationCard ${tone}`}>
            <div className='neuralEvaluationHeader'>
                <div className='neuralEvaluationLabel'>Run score</div>
                <div className='neuralEvaluationMeta'>Weighted from the most relevant {familyLabel} metrics</div>
            </div>
            <div className={`neuralEvaluationPanels ${comparisonEvaluation ? 'comparison' : ''}`}>
                {panels.map((panel) => (
                    <div key={panel.id} className={`neuralEvaluationPanel ${panel.tone}`}>
                        <div className='neuralEvaluationPanelHeader'>
                            <div className='neuralEvaluationPanelLabel'>{panel.label}</div>
                        </div>
                        <div className='neuralEvaluationScoreRow'>
                            <div className='neuralEvaluationScore'>{panel.evaluation.scoreOutOfTen.toFixed(1)}</div>
                            <div className='neuralEvaluationScale'>/ 10</div>
                        </div>
                        <div className='neuralEvaluationBreakdown'>
                            {panel.evaluation.criteria.map((criterion) => (
                                <div key={criterion.key} className='neuralEvaluationItem'>
                                    <div className='neuralEvaluationItemName'>{criterion.label}</div>
                                    <div className='neuralEvaluationItemTarget'>{criterion.targetLabel}</div>
                                    <div className='neuralEvaluationItemActual'>
                                        {formatMetricValue(criterion.actualValue, criterion.valueFormat)}
                                    </div>
                                    <div className='neuralEvaluationItemValue'>{criterion.achievedPct.toFixed(0)}%</div>
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}

const DETAIL_TABS = [
    { id: 'config', label: 'Config' },
    { id: 'run', label: 'Run' },
    { id: 'data', label: 'Results' },
]
const RUNS_PAGE_SIZE = 10
const RUN_COMPARE_OPTION_LIMIT = 100
const LEGACY_DRAFT_BAR_CAPS = {
    temporal_cnn_indicator_fusion_v1: 10000,
    neural_market_regime_cnn_v1: 10000,
    ema_low_adx_setup_quality_cnn_v1: 100000,
    ema_low_adx_setup_quality_cnn_v2: 100000,
    ema_low_adx_setup_quality_cnn_v3: 100000,
    ema_low_adx_setup_quality_cnn_v4: 100000,
    ema_low_adx_setup_quality_cnn_v5: 100000,
    ema_low_adx_setup_quality_cnn_v7: 100000,
    micro_cost_edge_cnn_v1: 10000,
    micro_cost_edge_cnn_v2: 10000,
    micro_cost_edge_cnn_v3: 10000,
    micro_cost_edge_cnn_v4: 10000,
    micro_cost_edge_cnn_v5: 10000,
    candle_reversal_cnn_v1: 10000,
    candle_reversal_cnn_v2: 10000,
    candle_reversal_cnn_v3: 10000,
    candle_reversal_cnn_v4: 10000,
    candle_reversal_cnn_v5: 10000,
    candle_reversal_cnn_v6: 10000,
    candle_reversal_cnn_v7: 10000,
    candle_reversal_cnn_v7_1: 10000,
    candle_reversal_cnn_v8: 10000,
    candle_reversal_cnn_v9: 10000,
    candle_reversal_cnn_v10: 10000,
    candle_reversal_cnn_v10_1: 10000,
    candle_reversal_cnn_v11: 10000,
    candle_reversal_cnn_v11_scores_only: 10000,
    candle_reversal_cnn_v12_scores_only: 10000,
    candle_reversal_setup_quality_cnn_v1: 10000,
}

export function Neural({
    authToken = '',
    isGuest = false,
    hasUnreadCompletion = false,
    onStatusChange,
    onLogEvent,
    isConsoleMaximized = false,
    isActive,
}) {
    const storedPanelState = useMemo(() => readStoredNeuralPanelState(), [])
    const [networks, setNetworks] = useState([])
    const [selectedNetworkId, setSelectedNetworkId] = useState(String(storedPanelState.selectedNetworkId || ''))
    const [networkDetail, setNetworkDetail] = useState(null)
    const [configDraft, setConfigDraft] = useState({})
    const [configDraftsByNetwork, setConfigDraftsByNetwork] = useState(() => {
        const storedDrafts = storedPanelState.configDraftsByNetwork
        return storedDrafts && typeof storedDrafts === 'object' ? storedDrafts : {}
    })
    const [activeDetailTab, setActiveDetailTab] = useState(() => {
        const storedTab = String(storedPanelState.activeDetailTab || '').trim().toLowerCase()
        return DETAIL_TABS.some((tab) => tab.id === storedTab) ? storedTab : 'config'
    })
    const [networkListFilter, setNetworkListFilter] = useState('all')
    const [isSavingNetworkAlias, setIsSavingNetworkAlias] = useState(false)
    const [isTogglingNetworkFavoriteId, setIsTogglingNetworkFavoriteId] = useState('')
    const [isLoading, setIsLoading] = useState(false)
    const [isRefreshingNetwork, setIsRefreshingNetwork] = useState(false)
    const [isTraining, setIsTraining] = useState(false)
    const [isTesting, setIsTesting] = useState(false)
    const [isCancellingJob, setIsCancellingJob] = useState(false)
    const [testSourceMode, setTestSourceMode] = useState('latest_train')
    const [testSourceModeByNetwork, setTestSourceModeByNetwork] = useState(() => {
        const storedModes = storedPanelState.testSourceModeByNetwork
        return storedModes && typeof storedModes === 'object' ? storedModes : {}
    })
    const [selectedRunId, setSelectedRunId] = useState('')
    const [comparisonRunId, setComparisonRunId] = useState('')
    const [runTypeFilter, setRunTypeFilter] = useState('all')
    const [runStatusFilter, setRunStatusFilter] = useState('all')
    const [runSortMode, setRunSortMode] = useState('started_desc')
    const [runsPage, setRunsPage] = useState(1)
    const [rowsPerPage, setRowsPerPage] = useState(RUNS_PAGE_SIZE)
    const [archiveFilter, setArchiveFilter] = useState('active')
    const [favoritesFilter, setFavoritesFilter] = useState('all')
    const [runNoteDraft, setRunNoteDraft] = useState('')
    const [networkAliasDraft, setNetworkAliasDraft] = useState('')
    const [isSavingRunMeta, setIsSavingRunMeta] = useState(false)
    const [isArchivingFailedRuns, setIsArchivingFailedRuns] = useState(false)
    const [isSanitizingRuntime, setIsSanitizingRuntime] = useState(false)
    const [isResetHistoryOpen, setIsResetHistoryOpen] = useState(false)
    const [isResettingHistory, setIsResettingHistory] = useState(false)
    const [isDeleteNetworkOpen, setIsDeleteNetworkOpen] = useState(false)
    const [isDeletingNetwork, setIsDeletingNetwork] = useState(false)
    const [isDeletingRunArtifactId, setIsDeletingRunArtifactId] = useState('')
    const [runArtifactDeleteConfirm, setRunArtifactDeleteConfirm] = useState(null)
    const [runDeleteConfirm, setRunDeleteConfirm] = useState(null)
    const [isDeletingRunId, setIsDeletingRunId] = useState('')
    const [runtimePanelJob, setRuntimePanelJob] = useState(null)
    const [trackedRunId, setTrackedRunId] = useState('')
    const [completionCard, setCompletionCard] = useState(null)
    const [runtimeEtaOverlay, setRuntimeEtaOverlay] = useState(null)
    const previousActiveJobRef = useRef(null)
    const pendingRunIdRef = useRef('')
    const surfacedTerminalRunIdRef = useRef('')
    const surfacedCompletedRunIdRef = useRef('')
    const onStatusChangeRef = useRef(onStatusChange)
    const onLogEventRef = useRef(onLogEvent)
    const guestRestrictionMessage = 'Guest demo can inspect this curated neural model, but cannot train, test, rename, favorite, delete, or change run history.'

    const authHeaders = useMemo(
        () => authToken ? { Authorization: `Bearer ${authToken}` } : {},
        [authToken],
    )

    useEffect(() => {
        onStatusChangeRef.current = onStatusChange
    }, [onStatusChange])

    useEffect(() => {
        onLogEventRef.current = onLogEvent
    }, [onLogEvent])

    useEffect(() => {
        if (isGuest && networkListFilter !== 'all') {
            setNetworkListFilter('all')
        }
    }, [isGuest, networkListFilter])

    function logGuestRestriction() {
        onLogEventRef.current?.(`Neural · ${guestRestrictionMessage}`)
    }
    const visibleNetworks = useMemo(() => {
        if (networkListFilter === 'favorites') {
            return networks.filter((network) => network?.is_favorite)
        }
        return networks
    }, [networkListFilter, networks])
    const networkGroups = useMemo(() => groupNetworksByMenuSection(visibleNetworks), [visibleNetworks])

    const selectedNetwork = useMemo(
        () => networkDetail?.network || networks.find((network) => network.id === selectedNetworkId) || null,
        [networkDetail, networks, selectedNetworkId],
    )
    const activeJob = selectedNetwork?.active_job || null
    const activeJobId = String(activeJob?.run_id || '').trim()
    const activeJobStatus = String(activeJob?.status || '').trim()
    const hasActiveJob = Boolean(activeJob)
    const allRuns = useMemo(() => {
        const rawRuns = Array.isArray(networkDetail?.network?.runs) ? networkDetail.network.runs : []
        return rawRuns
            .filter((run) => !isGuest || !shouldHideGuestRun(run))
            .map((run) => ({
                ...run,
                error: isGuest ? '' : String(run?.error || ''),
                evaluation: buildNeuralRunEvaluation(run, selectedNetwork),
            }))
    }, [isGuest, networkDetail?.network?.runs, selectedNetwork])
    const recentRuns = useMemo(() => {
        let filtered = Array.isArray(allRuns) ? [...allRuns] : []

        if (runTypeFilter !== 'all') {
            filtered = filtered.filter((run) => run.run_type === runTypeFilter)
        }

        if (runStatusFilter !== 'all') {
            filtered = filtered.filter((run) => run.status === runStatusFilter)
        }

        if (archiveFilter === 'active') {
            filtered = filtered.filter((run) => !run.is_archived)
        } else if (archiveFilter === 'archived') {
            filtered = filtered.filter((run) => run.is_archived)
        }

        if (favoritesFilter === 'favorites') {
            filtered = filtered.filter((run) => run.is_favorite)
        }

        const sortMeta = getRunSortMeta(runSortMode)
        filtered.sort((left, right) => {
            let leftValue = null
            let rightValue = null

            if (sortMeta.column === 'type') {
                leftValue = String(left?.run_type || '').toLowerCase()
                rightValue = String(right?.run_type || '').toLowerCase()
            } else if (sortMeta.column === 'status') {
                leftValue = String(left?.status || '').toLowerCase()
                rightValue = String(right?.status || '').toLowerCase()
            } else if (sortMeta.column === 'score') {
                leftValue = getRunComparableScore(left)
                rightValue = getRunComparableScore(right)
            } else if (sortMeta.column === 'direction') {
                leftValue = getRunDirectionValue(left)
                rightValue = getRunDirectionValue(right)
            } else {
                leftValue = Number(left?.started_at || 0)
                rightValue = Number(right?.started_at || 0)
            }

            if (leftValue == null && rightValue == null) {
                return Number(right?.started_at || 0) - Number(left?.started_at || 0)
            }
            if (leftValue == null) {
                return 1
            }
            if (rightValue == null) {
                return -1
            }

            let comparison = 0
            if (typeof leftValue === 'string' && typeof rightValue === 'string') {
                comparison = leftValue.localeCompare(rightValue)
            } else {
                comparison = Number(leftValue) - Number(rightValue)
            }

            if (comparison === 0) {
                comparison = Number(right?.started_at || 0) - Number(left?.started_at || 0)
            }

            return sortMeta.direction === 'asc' ? comparison : -comparison
        })

        return filtered
    }, [allRuns, archiveFilter, favoritesFilter, runSortMode, runStatusFilter, runTypeFilter])
    const baselineRun = useMemo(
        () => allRuns.find((run) => run.is_baseline) || null,
        [allRuns],
    )
    const shortlistRuns = useMemo(() => {
        const activeRuns = allRuns.filter((run) => !run.is_archived)
        const favorites = activeRuns.filter((run) => run.is_favorite)
        const shortlisted = []

        if (baselineRun && !baselineRun.is_archived) {
            shortlisted.push(baselineRun)
        }

        for (const run of favorites) {
            if (!shortlisted.some((item) => item.id === run.id)) {
                shortlisted.push(run)
            }
        }

        const bestCompleted = [...activeRuns]
            .filter((run) => run.status === 'completed')
            .sort((left, right) => {
                const leftScore = getRunComparableScore(left)
                const rightScore = getRunComparableScore(right)
                if (leftScore === null && rightScore === null) {
                    return 0
                }
                if (leftScore === null) {
                    return 1
                }
                if (rightScore === null) {
                    return -1
                }
                return rightScore - leftScore
            })[0]

        if (bestCompleted && !shortlisted.some((item) => item.id === bestCompleted.id)) {
            shortlisted.push(bestCompleted)
        }

        const latestCompleted = [...activeRuns]
            .filter((run) => run.status === 'completed')
            .sort((left, right) => Number(right?.started_at || 0) - Number(left?.started_at || 0))[0]

        if (latestCompleted && !shortlisted.some((item) => item.id === latestCompleted.id)) {
            shortlisted.push(latestCompleted)
        }

        return shortlisted.slice(0, 4)
    }, [allRuns, baselineRun])
    const latestTrainRun = recentRuns.find((run) => run.run_type === 'train' && run.status === 'completed') || null
    const latestCompletedTrainRun = useMemo(
        () => (
            [...allRuns]
                .filter((run) => run.run_type === 'train' && run.status === 'completed')
                .sort((left, right) => Number(right?.started_at || 0) - Number(left?.started_at || 0))[0]
            || null
        ),
        [allRuns],
    )
    const latestTestRun = recentRuns.find((run) => run.run_type === 'test' && run.status === 'completed') || null
    const bestModel = selectedNetwork?.best_model || null
    const promotedBestModelRunId = String(bestModel?.run_id || '').trim()
    const bestScoredTrainRun = useMemo(
        () => (
            [...allRuns]
                .filter((run) => run.run_type === 'train' && run.status === 'completed')
                .sort((left, right) => {
                    const leftScore = getRunComparableScore(left)
                    const rightScore = getRunComparableScore(right)
                    if (leftScore === null && rightScore === null) {
                        return Number(right?.started_at || 0) - Number(left?.started_at || 0)
                    }
                    if (leftScore === null) {
                        return 1
                    }
                    if (rightScore === null) {
                        return -1
                    }
                    if (Math.abs(rightScore - leftScore) > 1e-12) {
                        return rightScore - leftScore
                    }
                    return Number(right?.started_at || 0) - Number(left?.started_at || 0)
                })[0]
            || null
        ),
        [allRuns],
    )
    const bestModelRunId = String(bestScoredTrainRun?.id || promotedBestModelRunId || '').trim()
    const bestModelMetrics = useMemo(() => bestModel?.metrics || {}, [bestModel?.metrics])
    const latestTrainMetrics = useMemo(() => latestTrainRun?.metrics || {}, [latestTrainRun?.metrics])
    const latestTestMetrics = useMemo(() => latestTestRun?.metrics || {}, [latestTestRun?.metrics])
    const parameterGroups = useMemo(() => groupParameterSchema(selectedNetwork), [selectedNetwork])
    const snapshotCards = useMemo(
        () => Array.isArray(selectedNetwork?.snapshot_cards) ? selectedNetwork.snapshot_cards : [],
        [selectedNetwork?.snapshot_cards],
    )
    const metricSections = useMemo(
        () => Array.isArray(selectedNetwork?.metric_sections) ? selectedNetwork.metric_sections : [],
        [selectedNetwork?.metric_sections],
    )
    const testSourceOptions = useMemo(
        () => Array.isArray(selectedNetwork?.test_source_options) && selectedNetwork.test_source_options.length > 0
            ? selectedNetwork.test_source_options
            : [
                { id: 'latest_train', label: 'Test latest train' },
                { id: 'best_model', label: 'Test best model' },
            ],
        [selectedNetwork?.test_source_options],
    )
    const metricSources = useMemo(() => ({
        best_model: bestModelMetrics,
        latest_train: latestTrainMetrics,
        latest_test: latestTestMetrics,
    }), [bestModelMetrics, latestTrainMetrics, latestTestMetrics])
    const runTableSort = useMemo(() => getRunSortMeta(runSortMode), [runSortMode])
    const prioritizedRuns = useMemo(() => recentRuns, [recentRuns])
    const runGeneratedIndexMap = useMemo(() => getRunGeneratedIndexMap(prioritizedRuns), [prioritizedRuns])
    const safeRowsPerPage = Math.max(1, Number(rowsPerPage) || RUNS_PAGE_SIZE)
    const totalRunPages = Math.max(1, Math.ceil(prioritizedRuns.length / safeRowsPerPage))
    const pagedRuns = useMemo(() => {
        const startIndex = (runsPage - 1) * safeRowsPerPage
        return prioritizedRuns.slice(startIndex, startIndex + safeRowsPerPage)
    }, [prioritizedRuns, runsPage, safeRowsPerPage])
    const resolvedSnapshotCards = useMemo(() => (
        snapshotCards.map((card) => {
            const source = metricSources[card.source] || null
            const value = getMetricValueAtPath(source, card.metric_path)
            let hint = card.hint || ''

            if (card.source === 'best_model') {
                hint = bestModel
                    ? `updated ${formatTimestamp(bestModel.updated_at)} · ${formatSplitSummary(bestModelMetrics.split_sizes)}`
                    : hint || 'No best model yet.'
            } else if (card.source === 'latest_train') {
                hint = latestTrainRun
                    ? `train run ${formatDuration(latestTrainRun.duration_seconds)} · ${formatSplitSummary(latestTrainMetrics.split_sizes)}`
                    : hint || 'No completed training run yet.'
            } else if (card.source === 'latest_test') {
                hint = latestTestRun
                    ? `test run ${formatDuration(latestTestRun.duration_seconds)} · ${formatSplitSummary(latestTestMetrics.split_sizes)}`
                    : hint || 'No completed test run yet.'
            }

            return {
                id: card.id,
                label: card.label,
                value: formatMetricValue(value, card.format),
                hint,
            }
        })
    ), [bestModel, bestModelMetrics, latestTestMetrics, latestTestRun, latestTrainMetrics, latestTrainRun, metricSources, snapshotCards])
    const resolvedMetricSections = useMemo(() => (
        metricSections.map((section) => {
            const metricSource = resolveMetricsSource(section, metricSources)
            const metrics = Array.isArray(section.metrics)
                ? section.metrics
                    .map((metric) => {
                        const rawValue = metricSource ? metricSource[metric.key] : undefined
                        if (rawValue == null || rawValue === '') {
                            return null
                        }
                        return {
                            key: metric.key,
                            label: metric.label,
                            value: formatMetricValue(rawValue, metric.format),
                        }
                    })
                    .filter(Boolean)
                : []

            let hint = ''
            if (section.source === 'latest_train' && latestTrainRun) {
                hint = `Latest training run · ${formatDuration(latestTrainRun.duration_seconds)}`
            } else if (section.source === 'latest_test' && latestTestRun) {
                hint = `Latest test run · ${formatDuration(latestTestRun.duration_seconds)}`
            } else if (section.source === 'best_model' && bestModel) {
                hint = `Best promoted model · updated ${formatTimestamp(bestModel.updated_at)}`
            }

            return {
                id: section.id,
                label: section.label,
                hint,
                metrics,
            }
        }).filter((section) => section.metrics.length > 0)
    ), [bestModel, latestTestRun, latestTrainRun, metricSections, metricSources])
    const selectedRun = useMemo(
        () => recentRuns.find((run) => run.id === selectedRunId) || recentRuns[0] || null,
        [recentRuns, selectedRunId],
    )
    const displayedRuntimeJob = activeJob || runtimePanelJob || null
    const displayedRuntimeLogs = useMemo(() => {
        const logs = Array.isArray(displayedRuntimeJob?.logs) ? displayedRuntimeJob.logs : []
        return isGuest
            ? logs.filter((entry) => String(entry?.level || '').trim().toLowerCase() !== 'error')
            : logs
    }, [displayedRuntimeJob?.logs, isGuest])
    const displayedRuntimeError = isGuest
        ? ''
        : String(
            displayedRuntimeJob?.error
            || displayedRuntimeJob?.last_error
            || ''
        ).trim()
    const selectedRunEvaluation = selectedRun?.evaluation || null
    const comparisonRun = useMemo(() => {
        if (!allRuns.length) {
            return null
        }

        if (comparisonRunId && comparisonRunId !== selectedRun?.id) {
            return allRuns.find((run) => run.id === comparisonRunId) || null
        }

        if (baselineRun && baselineRun.id !== selectedRun?.id) {
            return baselineRun
        }

        return allRuns.find((run) => run.id !== selectedRun?.id) || null
    }, [allRuns, baselineRun, comparisonRunId, selectedRun?.id])
    const comparisonRunOptions = useMemo(() => (
        allRuns
            .filter((run) => run.id !== selectedRun?.id)
            .slice(0, RUN_COMPARE_OPTION_LIMIT)
    ), [allRuns, selectedRun?.id])
    const selectedRunMetricSections = useMemo(() => {
        if (!selectedRun) {
            return []
        }

        const selectedRunSources = {
            latest_train: selectedRun.metrics || {},
            latest_test: selectedRun.metrics || {},
            best_model: selectedRun.metrics || {},
        }

        return metricSections.map((section) => {
            const appliesToRun = (
                (selectedRun.run_type === 'train' && section.source === 'latest_train')
                || (selectedRun.run_type === 'test' && section.source === 'latest_test')
            )
            if (!appliesToRun) {
                return null
            }

            const metricSource = resolveMetricsSource(section, selectedRunSources)
            const comparisonSources = {
                latest_train: comparisonRun?.metrics || {},
                latest_test: comparisonRun?.metrics || {},
                best_model: comparisonRun?.metrics || {},
            }
            const comparisonMetricSource = comparisonRun ? resolveMetricsSource(section, comparisonSources) : null
            const metrics = Array.isArray(section.metrics)
                ? section.metrics
                    .map((metric) => {
                        const rawValue = metricSource ? metricSource[metric.key] : undefined
                        if (rawValue == null || rawValue === '') {
                            return null
                        }
                        return {
                            key: metric.key,
                            label: metric.label,
                            value: formatMetricValue(rawValue, metric.format),
                            delta: comparisonRun && comparisonMetricSource
                                ? formatMetricDelta(rawValue, comparisonMetricSource[metric.key], metric.format)
                                : '',
                        }
                    })
                    .filter(Boolean)
                : []

            return metrics.length
                ? {
                    id: `${selectedRun.id}-${section.id}`,
                    label: section.label,
                    hint: comparisonRun
                        ? `Compared against ${comparisonRun.run_type} ${comparisonRun.id.slice(0, 8)}.`
                        : (selectedRun.run_type === 'train' ? 'From this training run.' : 'From this test run.'),
                    metrics,
                }
                : null
        }).filter(Boolean)
    }, [comparisonRun, metricSections, selectedRun])
    const configComparisonRows = useMemo(() => {
        if (!selectedRun) {
            return []
        }

        const currentConfig = selectedRun.config || {}
        const baselineConfig = comparisonRun?.config || {}
        const keys = new Set([
            ...Object.keys(currentConfig),
            ...Object.keys(baselineConfig),
        ])

        return Array.from(keys)
            .sort((left, right) => left.localeCompare(right))
            .map((key) => {
                const currentValue = stringifyConfigValue(currentConfig[key])
                const compareValue = comparisonRun ? stringifyConfigValue(baselineConfig[key]) : '--'
                return {
                    key,
                    currentValue,
                    compareValue,
                    changed: comparisonRun ? currentValue !== compareValue : false,
                }
            })
    }, [comparisonRun, selectedRun])
    const comparisonSummary = useMemo(() => {
        if (!selectedRun || !comparisonRun) {
            return null
        }

        const scoreLabel = selectedRun?.evaluation ? 'run score' : (selectedNetwork?.score_label || 'Score')
        const primaryWinner = (() => {
            const selectedScore = getRunComparableScore(selectedRun)
            const comparisonScoreNumeric = getRunComparableScore(comparisonRun)
            if (selectedScore === null || comparisonScoreNumeric === null) {
                return null
            }
            if (Math.abs(selectedScore - comparisonScoreNumeric) < 1e-12) {
                return `Tie on ${scoreLabel.toLowerCase()}.`
            }
            return selectedScore > comparisonScoreNumeric
                ? `Selected run leads on ${scoreLabel.toLowerCase()} by ${formatMetricDelta(selectedScore, comparisonScoreNumeric)}.`
                : `Compared run leads on ${scoreLabel.toLowerCase()} by ${formatMetricDelta(comparisonScoreNumeric, selectedScore)}.`
        })()

        let selectedWins = 0
        let comparisonWins = 0
        const highlights = []

        for (const section of selectedRunMetricSections) {
            for (const metric of section.metrics || []) {
                const deltaText = metric.delta || ''
                if (!deltaText || deltaText === 'no change') {
                    continue
                }
                const deltaNumeric = Number(deltaText.replace(' pp', ''))
                if (!Number.isFinite(deltaNumeric)) {
                    continue
                }
                if (deltaNumeric > 0) {
                    selectedWins += 1
                    highlights.push(`${metric.label} +${deltaText.replace('+', '')}`)
                } else if (deltaNumeric < 0) {
                    comparisonWins += 1
                }
            }
        }

        const edge = selectedWins === comparisonWins
            ? 'Both runs are balanced across the highlighted metrics.'
            : selectedWins > comparisonWins
                ? `Selected run leads in ${selectedWins} highlighted metrics.`
                : `Compared run leads in ${comparisonWins} highlighted metrics.`

        return {
            primaryWinner,
            edge,
            highlights: highlights.slice(0, 3),
        }
    }, [comparisonRun, selectedNetwork?.score_label, selectedRun, selectedRunMetricSections])

    const syncNetworkDetailState = useCallback((data) => {
        setNetworkDetail(data)

        const nextNetwork = data?.network || null
        const nextActiveJob = nextNetwork?.active_job || null
        const nextRuns = Array.isArray(nextNetwork?.runs) ? nextNetwork.runs : []
        const previousActiveJob = previousActiveJobRef.current
        const trackedRunId = String(
            pendingRunIdRef.current
            || previousActiveJob?.run_id
            || '',
        ).trim()
        const trackedRun = trackedRunId
            ? nextRuns.find((run) => String(run?.id || '').trim() === trackedRunId) || null
            : null
        const trackedRunStatus = String(trackedRun?.status || '').trim().toLowerCase()
        const trackedRunError = isGuest ? '' : String(trackedRun?.error || '').trim()
        if (nextActiveJob) {
            previousActiveJobRef.current = nextActiveJob
            setRuntimePanelJob(nextActiveJob)
            onStatusChangeRef.current?.({
                neuralError: '',
                neuralPending: true,
                neuralReady: false,
            })
            return data
        }

        previousActiveJobRef.current = null

        if (trackedRun && (trackedRunStatus === 'failed' || trackedRunStatus === 'cancelled')) {
            if (isGuest) {
                onStatusChangeRef.current?.({
                    neuralError: '',
                    neuralPending: false,
                })
                pendingRunIdRef.current = ''
                setTrackedRunId('')
                return data
            }
            const fallbackMessage = trackedRunStatus === 'cancelled'
                ? 'Neural job cancelled.'
                : 'Neural job failed.'
            const errorMessage = trackedRunError || fallbackMessage
            setRuntimePanelJob((current) => ({
                ...(current || {}),
                run_id: trackedRun.id,
                run_type: trackedRun.run_type,
                status: trackedRun.status,
                progress: 1,
                finished_at: trackedRun.ended_at,
                duration_seconds: trackedRun.duration_seconds,
                error: errorMessage,
                last_message: errorMessage,
                data_feed_status: 'finished',
                data_feed_label: 'Finished',
                data_feed_detail: trackedRunStatus === 'cancelled'
                    ? 'Neural job stopped after cancellation.'
                    : 'Neural job finished with an error.',
                phase: trackedRunStatus,
                phase_label: trackedRunStatus === 'cancelled' ? 'Cancelled' : 'Failed',
                detail: errorMessage,
            }))

            onStatusChangeRef.current?.({
                neuralError: errorMessage,
                neuralPending: false,
            })

            if (surfacedTerminalRunIdRef.current !== trackedRun.id) {
                onLogEventRef.current?.(
                    `Neural ${trackedRun.run_type || 'job'} ${trackedRunStatus}: ${errorMessage}`,
                    trackedRunStatus === 'cancelled' ? 'warn' : 'error',
                )
                surfacedTerminalRunIdRef.current = trackedRun.id
            }

            pendingRunIdRef.current = ''
            setTrackedRunId('')
            return data
        }

        if (trackedRun && trackedRunStatus === 'completed') {
            const completedScore = Number(trackedRun?.evaluation?.scoreOutOfTen)
            setRuntimePanelJob((current) => ({
                ...(current || {}),
                run_id: trackedRun.id,
                run_type: trackedRun.run_type,
                status: trackedRun.status,
                progress: 1,
                finished_at: trackedRun.ended_at,
                duration_seconds: trackedRun.duration_seconds,
                score: trackedRun.score,
                last_message: 'Neural job completed successfully.',
                data_feed_status: 'finished',
                data_feed_label: 'Finished',
                data_feed_detail: 'Neural job completed and the runtime is no longer streaming.',
                phase: 'completed',
                phase_label: 'Completed',
                detail: 'Run completed successfully.',
            }))
            if (surfacedCompletedRunIdRef.current !== trackedRun.id) {
                onStatusChangeRef.current?.({
                    neuralError: '',
                    neuralPending: false,
                    neuralReady: true,
                })
                setCompletionCard({
                    runId: trackedRun.id,
                    runLabel: getRunDisplayLabel(trackedRun),
                    scoreOutOfTen: Number.isFinite(completedScore) ? completedScore : null,
                    rawScore: Number.isFinite(Number(trackedRun?.score)) ? Number(trackedRun.score) : null,
                })
                surfacedCompletedRunIdRef.current = trackedRun.id
            } else {
                onStatusChangeRef.current?.({
                    neuralError: '',
                    neuralPending: false,
                })
            }
            pendingRunIdRef.current = ''
            setTrackedRunId('')
            setSelectedRunId(trackedRun.id)
            return data
        }

        onStatusChangeRef.current?.({
            neuralError: '',
            neuralPending: false,
        })
        return data
    }, [isGuest])

    useEffect(() => {
        setRunNoteDraft(selectedRun?.note || '')
    }, [selectedRun?.id, selectedRun?.note])

    useEffect(() => {
        if (!recentRuns.length) {
            setSelectedRunId('')
            setComparisonRunId('')
            return
        }

        if (!recentRuns.some((run) => run.id === selectedRunId)) {
            setSelectedRunId(recentRuns[0].id)
        }
        if (comparisonRunId && !allRuns.some((run) => run.id === comparisonRunId)) {
            setComparisonRunId('')
        }
    }, [allRuns, comparisonRunId, recentRuns, selectedRunId])

    useEffect(() => {
        setRunsPage(1)
    }, [selectedNetwork?.id, runTypeFilter, runStatusFilter, runSortMode, archiveFilter, favoritesFilter, safeRowsPerPage])

    useEffect(() => {
        if (runsPage > totalRunPages) {
            setRunsPage(totalRunPages)
        }
    }, [runsPage, totalRunPages])

    useEffect(() => {
        const defaultOption = testSourceOptions[0]?.id || 'latest_train'
        if (!testSourceOptions.some((option) => option.id === testSourceMode)) {
            setTestSourceMode(defaultOption)
        }
    }, [selectedNetwork?.id, testSourceOptions, testSourceMode])

    useEffect(() => {
        if (!selectedNetwork) {
            return
        }

        const storedDraft = configDraftsByNetwork[selectedNetwork.id]
        const storedTestSourceMode = String(testSourceModeByNetwork[selectedNetwork.id] || '').trim()
        const sanitizedDraft = storedDraft
            ? sanitizeStoredDraftConfig(storedDraft, selectedNetwork)
            : normalizeConfigFromNetwork(selectedNetwork)
        const nextDraft = sanitizedDraft

        setConfigDraft(nextDraft)
        setNetworkAliasDraft(String(selectedNetwork.alias || ''))
        setTestSourceMode(storedTestSourceMode || (testSourceOptions[0]?.id || 'latest_train'))
    }, [configDraftsByNetwork, selectedNetwork, testSourceModeByNetwork, testSourceOptions])

    useEffect(() => {
        writeStoredNeuralPanelState({
            selectedNetworkId,
            configDraftsByNetwork,
            testSourceModeByNetwork,
            activeDetailTab,
        })
    }, [activeDetailTab, configDraftsByNetwork, selectedNetworkId, testSourceModeByNetwork])

    useEffect(() => {
        if (!isActive || !authToken) {
            return undefined
        }

        let disposed = false

        async function loadNetworks() {
            setIsLoading(true)

            try {
                const response = await fetch(buildApiUrl('/neural/networks'), {
                    headers: authHeaders,
                })
                const data = await readJsonResponse(response)

                if (!response.ok || data.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, 'Failed to load neural networks.'))
                }

                if (disposed) {
                    return
                }

                const nextNetworks = Array.isArray(data.networks) ? data.networks : []
                const activeRuntimeNetwork = nextNetworks.find((network) => network?.active_job) || null
                const activeRuntimeJob = activeRuntimeNetwork?.active_job || null
                setNetworks(nextNetworks)
                setConfigDraftsByNetwork((currentDrafts) => {
                    const sanitizedDraftState = sanitizeStoredDraftMap(currentDrafts, nextNetworks)
                    return sanitizedDraftState.changed ? sanitizedDraftState.drafts : currentDrafts
                })
                setSelectedNetworkId((current) => (
                    String(activeRuntimeNetwork?.id || '').trim()
                    || (nextNetworks.some((network) => network.id === current) ? current : '')
                    || nextNetworks[0]?.id
                    || ''
                ))
                if (activeRuntimeJob) {
                    setActiveDetailTab('run')
                    setRuntimePanelJob(activeRuntimeJob)
                    setTrackedRunId(String(activeRuntimeJob.run_id || '').trim())
                    if (String(activeRuntimeJob.run_id || '').trim()) {
                        setSelectedRunId(String(activeRuntimeJob.run_id || '').trim())
                    }
                }
                onStatusChangeRef.current?.({
                    neuralError: '',
                    neuralPending: Object.keys(data.runtime?.active_jobs || {}).length > 0,
                })
            } catch (error) {
                if (!disposed) {
                    const message = error.message || 'Failed to load neural networks.'
                    onStatusChangeRef.current?.({
                        neuralError: isGuest ? '' : message,
                        neuralPending: false,
                    })
                    if (!isGuest) {
                        onLogEventRef.current?.(`Neural load failed: ${message}`, 'error')
                    }
                }
            } finally {
                if (!disposed) {
                    setIsLoading(false)
                }
            }
        }

        void loadNetworks()

        return () => {
            disposed = true
        }
    }, [authHeaders, authToken, isActive, isGuest])

    useEffect(() => {
        if (!isActive || !authToken || !selectedNetworkId) {
            return undefined
        }

        let disposed = false

        async function loadNetworkDetail() {
            try {
                const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}`), {
                    headers: authHeaders,
                })
                const data = await readJsonResponse(response)

                if (!response.ok || data.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, 'Failed to load neural network details.'))
                }

                if (disposed) {
                    return
                }

                syncNetworkDetailState(data)
            } catch (error) {
                if (!disposed) {
                    const message = error.message || 'Failed to load neural network details.'
                    onStatusChangeRef.current?.({
                        neuralError: isGuest ? '' : message,
                        neuralPending: false,
                    })
                }
            }
        }

        void loadNetworkDetail()

        if (!hasActiveJob && !trackedRunId) {
            return () => {
                disposed = true
            }
        }

        const intervalId = window.setInterval(() => {
            void loadNetworkDetail()
        }, 3000)

        return () => {
            disposed = true
            window.clearInterval(intervalId)
        }
    }, [activeJobId, activeJobStatus, authHeaders, authToken, hasActiveJob, isActive, isGuest, selectedNetworkId, syncNetworkDetailState, trackedRunId])

    function updateConfigField(key, value) {
        setConfigDraft((current) => {
            const nextDraft = {
                ...current,
                [key]: value,
            }

            if (selectedNetwork?.id) {
                setConfigDraftsByNetwork((drafts) => ({
                    ...drafts,
                    [selectedNetwork.id]: nextDraft,
                }))
            }

            return nextDraft
        })
    }

    function buildConfigPayload() {
        const schema = Array.isArray(selectedNetwork?.parameter_schema)
            ? selectedNetwork.parameter_schema
            : []
        const payload = {}

        for (const field of schema) {
            if (!field?.key) {
                continue
            }
            payload[field.key] = coerceFieldValue(field, configDraft[field.key])
        }

        if (supportsArchitectureBuilder(selectedNetwork)) {
            payload.normalizationColumns = normalizeNormalizationColumns(configDraft.normalizationColumns, selectedNetwork)
            delete payload.normalizationMode
            payload.hiddenLayers = normalizeHiddenLayers(configDraft.hiddenLayers)
        }

        return payload
    }

    async function copyCurrentConfig() {
        try {
            const payload = buildConfigPayload()
            await navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
            onLogEvent?.('Copied neural config JSON to clipboard.', 'success')
        } catch (error) {
            onLogEvent?.(`Could not copy neural config: ${error.message || 'unknown error'}`, 'error')
        }
    }

    async function importConfigFromClipboard() {
        try {
            const rawText = await navigator.clipboard.readText()
            const parsed = JSON.parse(rawText || '{}')
            const importedDraft = sanitizeImportedConfig(parsed, selectedNetwork)
            setConfigDraft(importedDraft)
            if (selectedNetwork?.id) {
                setConfigDraftsByNetwork((drafts) => ({
                    ...drafts,
                    [selectedNetwork.id]: importedDraft,
                }))
            }
            onLogEvent?.('Imported neural config from clipboard.', 'success')
        } catch (error) {
            onLogEvent?.(`Could not import neural config: ${error.message || 'unknown error'}`, 'error')
        }
    }

    async function copyRunError(run) {
        try {
            await copyTextToClipboard(run?.error || '')
            onLogEvent?.('Copied neural run error to clipboard.', 'success')
        } catch (error) {
            onLogEvent?.(`Could not copy neural run error: ${error.message || 'unknown error'}`, 'error')
        }
    }

    async function copySelectedRunExport() {
        try {
            if (!selectedRun) {
                throw new Error('Select a run first.')
            }

            const payload = buildNeuralRunExportPayload(selectedNetwork, selectedRun)
            await copyTextToClipboard(JSON.stringify(payload, null, 2))
            onLogEvent?.(`Copied selected neural run ${selectedRun.id.slice(0, 8)} to clipboard.`, 'success')
        } catch (error) {
            onLogEvent?.(`Could not copy selected neural run export: ${error.message || 'unknown error'}`, 'error')
        }
    }

    async function copyRunsTableExport() {
        try {
            const payload = buildRunsTableExportPayload(selectedNetwork, prioritizedRuns, {
                label: 'Filtered neural runs table',
                totalRuns: prioritizedRuns.length,
                filters: {
                    type: runTypeFilter,
                    status: runStatusFilter,
                    sort: runSortMode,
                    archive: archiveFilter,
                    favorites: favoritesFilter,
                },
            })
            await copyTextToClipboard(JSON.stringify(payload, null, 2))
            onLogEvent?.('Copied filtered neural runs table JSON to clipboard.', 'success')
        } catch (error) {
            onLogEvent?.(`Could not copy neural runs table export: ${error.message || 'unknown error'}`, 'error')
        }
    }

    function toggleRunTableSort(column) {
        setRunSortMode((current) => {
            const active = getRunSortMeta(current)
            if (active.column === column) {
                return `${column}_${active.direction === 'asc' ? 'desc' : 'asc'}`
            }

            const defaultDirection = column === 'type' || column === 'status' ? 'asc' : 'desc'
            return `${column}_${defaultDirection}`
        })
    }

    function renderRunSortHeader(label, column) {
        const isActive = runTableSort.column === column
        const direction = isActive ? runTableSort.direction : ''

        return (
            <button
                type='button'
                className={`neuralRunsSortButton ${isActive ? 'active' : ''}`}
                onClick={() => toggleRunTableSort(column)}
            >
                <span>{label}</span>
                <span className={`neuralRunsSortGlyph ${isActive ? 'active' : ''}`} aria-hidden='true'>
                    <span className={`neuralRunsSortTriangle up ${direction === 'asc' ? 'active' : ''}`} />
                    <span className={`neuralRunsSortTriangle down ${direction === 'desc' ? 'active' : ''}`} />
                </span>
            </button>
        )
    }

    async function copyRunConfig(run) {
        try {
            if (!run) {
                throw new Error('Run not found.')
            }

            await copyTextToClipboard(JSON.stringify(run.config || {}, null, 2))
            onLogEvent?.(`Copied neural run config ${run.id.slice(0, 8)} to clipboard.`, 'success')
        } catch (error) {
            onLogEvent?.(`Could not copy neural run config: ${error.message || 'unknown error'}`, 'error')
        }
    }

    function loadConfigFromRun(run) {
        if (!run || !selectedNetwork) {
            return
        }

        const nextDraft = sanitizeImportedConfig(run.config || {}, selectedNetwork)
        setConfigDraft(nextDraft)
        setConfigDraftsByNetwork((drafts) => ({
            ...drafts,
            [selectedNetwork.id]: nextDraft,
        }))
        onLogEvent?.(`Loaded config from neural run ${run.id.slice(0, 8)}.`, 'success')
    }

    async function refreshSelectedNetwork() {
        if (!selectedNetworkId) {
            return null
        }

        setIsRefreshingNetwork(true)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}`), {
                headers: authHeaders,
            })
            const data = await readJsonResponse(response)

            if (response.ok && data.status === 'ok') {
                return syncNetworkDetailState(data)
            }

            throw new Error(extractApiErrorMessage(data, 'Failed to refresh neural network.'))
        } finally {
            setIsRefreshingNetwork(false)
        }
    }

    async function saveNetworkAlias() {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId) {
            return
        }

        setIsSavingNetworkAlias(true)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}`), {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({
                    alias: String(networkAliasDraft || '').trim(),
                }),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to update neural network name.'))
            }

            setNetworkDetail(data)
            setNetworks((current) => current.map((network) => (
                network.id === selectedNetworkId
                    ? { ...network, ...(data.network || {}) }
                    : network
            )))
            onLogEvent?.('Saved neural network name.', 'success')
        } catch (error) {
            onLogEvent?.(`Neural network rename failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsSavingNetworkAlias(false)
        }
    }

    async function toggleNetworkFavorite(network) {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!network?.id || isTogglingNetworkFavoriteId) {
            return
        }

        setIsTogglingNetworkFavoriteId(network.id)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${network.id}`), {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({
                    is_favorite: !network.is_favorite,
                }),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to update neural network favorite state.'))
            }

            if (network.id === selectedNetworkId) {
                setNetworkDetail(data)
            }
            setNetworks((current) => current.map((entry) => (
                entry.id === network.id
                    ? { ...entry, ...(data.network || {}) }
                    : entry
            )))
            onLogEvent?.(
                !network.is_favorite
                    ? `Marked neural network "${getNetworkDisplayLabel(network)}" as favorite.`
                    : `Removed neural network "${getNetworkDisplayLabel(network)}" from favorites.`,
                'success',
            )
        } catch (error) {
            onLogEvent?.(`Neural network favorite update failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsTogglingNetworkFavoriteId('')
        }
    }

    async function updateRunAnnotations(runId, payload, successMessage = '') {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || !runId) {
            return
        }

        setIsSavingRunMeta(true)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/runs/${runId}`), {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify(payload),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to update neural run metadata.'))
            }

            if (successMessage) {
                onLogEvent?.(successMessage, 'success')
            }
            await refreshSelectedNetwork()
        } catch (error) {
            onLogEvent?.(`Neural run update failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsSavingRunMeta(false)
        }
    }

    async function archiveAllFailedRuns() {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || isSavingRunMeta || isArchivingFailedRuns) {
            return
        }

        const failedRuns = allRuns.filter((run) => run.status === 'failed' && !run.is_archived)
        if (!failedRuns.length) {
            onLogEvent?.('No failed runs to archive.', 'info')
            return
        }

        setIsArchivingFailedRuns(true)
        try {
            for (const run of failedRuns) {
                const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/runs/${run.id}`), {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        ...authHeaders,
                    },
                    body: JSON.stringify({ is_archived: true }),
                })
                const data = await readJsonResponse(response)

                if (!response.ok || data.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, `Failed to archive run ${run.id.slice(0, 8)}.`))
                }
            }

            onLogEvent?.(`Archived ${failedRuns.length} failed neural run(s).`, 'success')
            await refreshSelectedNetwork()
        } catch (error) {
            onLogEvent?.(`Bulk archive failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsArchivingFailedRuns(false)
        }
    }

    async function toggleRunArchive(run) {
        if (!run?.id || isSavingRunMeta) {
            return
        }

        await updateRunAnnotations(
            run.id,
            { is_archived: !run.is_archived },
            run.is_archived
                ? `Restored neural run ${run.id.slice(0, 8)}.`
                : `Archived neural run ${run.id.slice(0, 8)}.`,
        )
    }

    async function handleDeleteRunArtifact(run) {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || !run?.id || isSavingRunMeta || isDeletingRunArtifactId) {
            return
        }

        setIsDeletingRunArtifactId(run.id)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/runs/${run.id}/artifact`), {
                method: 'DELETE',
                headers: authHeaders,
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to delete neural model file.'))
            }

            setNetworkDetail(data)
            setNetworks((current) => current.map((network) => (
                network.id === selectedNetworkId
                    ? { ...network, ...(data.network || {}) }
                    : network
            )))
            onLogEvent?.(`Deleted saved model file for neural run ${run.id.slice(0, 8)}.`, 'success')
        } catch (error) {
            onLogEvent?.(`Neural model file delete failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsDeletingRunArtifactId('')
        }
    }

    async function handleDeleteRun(run) {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || !run?.id || isSavingRunMeta || isDeletingRunId) {
            return
        }

        setIsDeletingRunId(run.id)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/runs/${run.id}`), {
                method: 'DELETE',
                headers: authHeaders,
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to delete neural run.'))
            }

            setNetworkDetail(data)
            setNetworks((current) => current.map((network) => (
                network.id === selectedNetworkId
                    ? { ...network, ...(data.network || {}) }
                    : network
            )))
            if (selectedRunId === run.id) {
                setSelectedRunId('')
            }
            if (comparisonRunId === run.id) {
                setComparisonRunId('')
            }
            onLogEvent?.(`Deleted neural run ${run.id.slice(0, 8)}.`, 'success')
        } catch (error) {
            onLogEvent?.(`Neural run delete failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsDeletingRunId('')
        }
    }

    async function handleSanitizeRuntime() {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || isSanitizingRuntime) {
            return
        }

        setIsSanitizingRuntime(true)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/sanitize`), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({ wait_seconds: 2.5 }),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to sanitize neural runtime.'))
            }

            const removedCount = Number(data?.sanitize?.removed_orphans?.length || 0)
            const runningCount = Number(data?.sanitize?.still_running?.length || 0)
            onLogEvent?.(
                runningCount > 0
                    ? `Sanitize requested. ${removedCount} stale job(s) cleaned, ${runningCount} job(s) still running after wait window.`
                    : `Neural runtime sanitized. ${removedCount} stale job(s) cleaned.`,
                runningCount > 0 ? 'warn' : 'success',
            )
            await refreshSelectedNetwork()
        } catch (error) {
            onLogEvent?.(`Neural sanitize failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsSanitizingRuntime(false)
        }
    }

    async function handleTrain() {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || isTraining || isTesting || activeJob) {
            return
        }

        const launchedAt = Math.floor(Date.now() / 1000)
        pendingRunIdRef.current = ''
        previousActiveJobRef.current = null
        surfacedTerminalRunIdRef.current = ''
        surfacedCompletedRunIdRef.current = ''
        setTrackedRunId('')
        setSelectedRunId('')
        setRuntimePanelJob({
            run_id: '',
            run_type: 'train',
            status: 'queued',
            progress: 0,
            started_at: launchedAt,
            logs: [],
            data_feed_status: 'receiving',
            data_feed_label: 'Queued',
            data_feed_detail: 'Waiting for the new neural worker to start.',
            phase: 'queued',
            phase_label: 'Queued',
            detail: 'Preparing a fresh runtime session.',
            last_message: 'Neural job queued. Waiting for worker process.',
        })
        setActiveDetailTab('run')
        setIsTraining(true)
        onStatusChange?.({ neuralError: '', neuralPending: true, neuralReady: false })

        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/train`), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({
                    config: buildConfigPayload(),
                }),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to start neural training.'))
            }

            pendingRunIdRef.current = String(data?.run?.id || '').trim()
            setTrackedRunId(String(data?.run?.id || '').trim())
            setSelectedRunId(String(data?.run?.id || '').trim())
            setRuntimePanelJob((current) => ({
                ...(current || {}),
                run_id: String(data?.run?.id || '').trim(),
            }))
            setActiveDetailTab('run')
            onLogEvent?.(`Neural training started for ${getNetworkDisplayLabel(selectedNetwork) || selectedNetworkId}.`, 'success')
            await refreshSelectedNetwork()
        } catch (error) {
            setRuntimePanelJob((current) => ({
                ...(current || {}),
                status: 'failed',
                progress: 1,
                finished_at: Math.floor(Date.now() / 1000),
                error: error.message || 'Failed to start neural training.',
                last_message: error.message || 'Failed to start neural training.',
                data_feed_status: 'finished',
                data_feed_label: 'Finished',
                data_feed_detail: 'Neural job finished with an error.',
                phase: 'failed',
                phase_label: 'Failed',
                detail: error.message || 'Failed to start neural training.',
            }))
            onStatusChange?.({ neuralError: error.message || 'Failed to start neural training.', neuralPending: false, neuralReady: false })
            onLogEvent?.(`Neural training failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsTraining(false)
        }
    }

    async function handleTest() {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || isTraining || isTesting || activeJob) {
            return
        }

        const launchedAt = Math.floor(Date.now() / 1000)
        pendingRunIdRef.current = ''
        previousActiveJobRef.current = null
        surfacedTerminalRunIdRef.current = ''
        surfacedCompletedRunIdRef.current = ''
        setTrackedRunId('')
        setSelectedRunId('')
        setRuntimePanelJob({
            run_id: '',
            run_type: 'test',
            status: 'queued',
            progress: 0,
            started_at: launchedAt,
            logs: [],
            data_feed_status: 'receiving',
            data_feed_label: 'Queued',
            data_feed_detail: 'Waiting for the new neural worker to start.',
            phase: 'queued',
            phase_label: 'Queued',
            detail: 'Preparing a fresh runtime session.',
            last_message: 'Neural job queued. Waiting for worker process.',
        })
        setActiveDetailTab('run')
        setIsTesting(true)
        onStatusChange?.({ neuralError: '', neuralPending: true, neuralReady: false })

        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/test`), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({
                    config: buildConfigPayload(),
                    source_run_id: testSourceMode === 'latest_train' ? (latestTrainRun?.id || null) : null,
                }),
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to start neural test.'))
            }

            pendingRunIdRef.current = String(data?.run?.id || '').trim()
            setTrackedRunId(String(data?.run?.id || '').trim())
            setSelectedRunId(String(data?.run?.id || '').trim())
            setRuntimePanelJob((current) => ({
                ...(current || {}),
                run_id: String(data?.run?.id || '').trim(),
            }))
            setActiveDetailTab('run')
            onLogEvent?.(`Neural test started for ${getNetworkDisplayLabel(selectedNetwork) || selectedNetworkId}.`, 'success')
            await refreshSelectedNetwork()
        } catch (error) {
            setRuntimePanelJob((current) => ({
                ...(current || {}),
                status: 'failed',
                progress: 1,
                finished_at: Math.floor(Date.now() / 1000),
                error: error.message || 'Failed to start neural test.',
                last_message: error.message || 'Failed to start neural test.',
                data_feed_status: 'finished',
                data_feed_label: 'Finished',
                data_feed_detail: 'Neural job finished with an error.',
                phase: 'failed',
                phase_label: 'Failed',
                detail: error.message || 'Failed to start neural test.',
            }))
            onStatusChange?.({ neuralError: error.message || 'Failed to start neural test.', neuralPending: false, neuralReady: false })
            onLogEvent?.(`Neural test failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsTesting(false)
        }
    }

    function acknowledgeUnreadCompletion() {
        if (!hasUnreadCompletion) {
            return
        }
        onStatusChange?.({ neuralReady: false })
    }

    useEffect(() => {
        if (!completionCard?.runId) {
            return undefined
        }

        const timeoutId = window.setTimeout(() => {
            setCompletionCard((current) => current?.runId === completionCard.runId ? null : current)
        }, 9000)

        return () => window.clearTimeout(timeoutId)
    }, [completionCard])

    useEffect(() => {
        const runtimeStatus = String(displayedRuntimeJob?.status || '').trim().toLowerCase()
        const runtimeEta = Number(displayedRuntimeJob?.eta_seconds)
        const runtimeRunId = String(displayedRuntimeJob?.run_id || '').trim()

        if (!displayedRuntimeJob || !runtimeRunId || ['completed', 'failed', 'cancelled'].includes(runtimeStatus)) {
            setRuntimeEtaOverlay(null)
            return
        }

        if (Number.isFinite(runtimeEta) && runtimeEta > 0) {
            setRuntimeEtaOverlay({
                runId: runtimeRunId,
                etaSeconds: runtimeEta,
                updatedAtMs: Date.now(),
                remainingSeconds: runtimeEta,
            })
        }
    }, [displayedRuntimeJob])

    useEffect(() => {
        if (!runtimeEtaOverlay?.runId) {
            return undefined
        }

        const intervalId = window.setInterval(() => {
            setRuntimeEtaOverlay((current) => {
                if (!current?.runId) {
                    return null
                }

                const remainingSeconds = Math.max(0, current.etaSeconds - ((Date.now() - current.updatedAtMs) / 1000))
                if (remainingSeconds <= 0) {
                    return null
                }

                return {
                    ...current,
                    remainingSeconds,
                }
            })
        }, 1000)

        return () => window.clearInterval(intervalId)
    }, [runtimeEtaOverlay?.runId])

    async function handleCancelActiveJob() {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || !activeJob || isCancellingJob) {
            return
        }

        setIsCancellingJob(true)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/cancel`), {
                method: 'POST',
                headers: authHeaders,
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to cancel neural job.'))
            }

            onLogEvent?.(`Cancellation requested for ${getNetworkDisplayLabel(selectedNetwork) || selectedNetworkId}.`, 'success')
            await refreshSelectedNetwork()
        } catch (error) {
            onLogEvent?.(`Neural cancel failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsCancellingJob(false)
        }
    }

    async function handleResetNetworkHistory() {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || isResettingHistory) {
            return
        }

        setIsResettingHistory(true)
        try {
            const response = await fetch(buildApiUrl(`/neural/networks/${selectedNetworkId}/history`), {
                method: 'DELETE',
                headers: authHeaders,
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to reset neural history.'))
            }

            setNetworkDetail(data)
            setNetworks((current) => current.map((network) => (
                network.id === selectedNetworkId
                    ? { ...network, ...(data.network || {}) }
                    : network
            )))
            setSelectedRunId('')
            setComparisonRunId('')
            setIsResetHistoryOpen(false)
            onLogEvent?.('Reset neural history and removed saved model artifacts.', 'success')
        } catch (error) {
            onLogEvent?.(`Neural history reset failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsResettingHistory(false)
        }
    }

    async function handleDeleteNetwork() {
        if (isGuest) {
            logGuestRestriction()
            return
        }
        if (!selectedNetworkId || isDeletingNetwork) {
            return
        }

        setIsDeletingNetwork(true)
        try {
            const deletedNetworkId = selectedNetworkId
            const deletedNetworkLabel = getNetworkDisplayLabel(selectedNetwork) || deletedNetworkId
            const response = await fetch(buildApiUrl(`/neural/networks/${deletedNetworkId}`), {
                method: 'DELETE',
                headers: authHeaders,
            })
            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to delete neural network.'))
            }

            const nextNetworks = Array.isArray(data.networks) ? data.networks : []
            setNetworks(nextNetworks)
            setNetworkDetail(null)
            setSelectedRunId('')
            setComparisonRunId('')
            setNetworkAliasDraft('')
            setConfigDraft({})
            setConfigDraftsByNetwork((current) => {
                if (!current || typeof current !== 'object') {
                    return current
                }
                const nextDrafts = { ...current }
                delete nextDrafts[deletedNetworkId]
                return nextDrafts
            })
            setTestSourceModeByNetwork((current) => {
                if (!current || typeof current !== 'object') {
                    return current
                }
                const nextModes = { ...current }
                delete nextModes[deletedNetworkId]
                return nextModes
            })
            setSelectedNetworkId(nextNetworks[0]?.id || '')
            setIsDeleteNetworkOpen(false)
            onLogEvent?.(`Deleted neural network "${deletedNetworkLabel}" from this workspace.`, 'success')
        } catch (error) {
            onLogEvent?.(`Neural network delete failed: ${error.message || 'unknown error'}`, 'error')
        } finally {
            setIsDeletingNetwork(false)
        }
    }

    return (
        <div
            className={`Neural ${isActive ? 'active' : ''}`}
            onPointerDownCapture={acknowledgeUnreadCompletion}
            onKeyDownCapture={acknowledgeUnreadCompletion}
        >
            <div className='neuralLayout'>
                <aside className='neuralSidebar'>
                    <div className='neuralSidebarTitle'>Networks</div>
                    {isGuest ? (
                        <div className='neuralGuestNotice'>
                            Guest display shows one curated example neural model.
                        </div>
                    ) : (
                        <div className='neuralSidebarListTabs'>
                            <button
                                type='button'
                                className={networkListFilter === 'all' ? 'active' : ''}
                                onClick={() => setNetworkListFilter('all')}
                            >
                                All
                            </button>
                            <button
                                type='button'
                                className={networkListFilter === 'favorites' ? 'active' : ''}
                                onClick={() => setNetworkListFilter('favorites')}
                            >
                                Favorites
                            </button>
                        </div>
                    )}

                    <div className='neuralNetworkList'>
                        {isLoading && <div className='neuralEmpty'>Loading networks...</div>}
                        {!isLoading && networks.length === 0 && <div className='neuralEmpty'>No neural networks registered.</div>}
                        {!isLoading && networks.length > 0 && visibleNetworks.length === 0 && (
                            <div className='neuralEmpty'>No favorite neural networks yet.</div>
                        )}

                        {networkGroups.map((group) => (
                            <div key={group.id} className='neuralFamilyGroup'>
                                <div className='neuralFamilyGroupTitle'>{group.label}</div>
                                {group.items.length === 0 && (
                                    <div className='neuralEmpty neuralSidebarSubempty'>No networks yet.</div>
                                )}
                                {group.items.map((network) => (
                                    <div key={network.id} className='neuralNetworkCardEntry'>
                                        <button
                                            type='button'
                                            className={`neuralNetworkCard ${network.id === selectedNetworkId ? 'active' : ''}`}
                                            onClick={() => setSelectedNetworkId(network.id)}
                                        >
                                            <div className='neuralNetworkCardHeader'>
                                                <span className='neuralNetworkCardLabel'>
                                                    {network.is_favorite ? <span className='neuralNetworkFavoriteStar' aria-hidden='true'>★</span> : null}
                                                    <span>{getNetworkDisplayLabel(network)}</span>
                                                </span>
                                            </div>
                                            <div className='neuralNetworkCardMeta'>
                                                {network.family_label}
                                            </div>
                                        </button>
                                        {!isGuest ? (
                                            <button
                                                type='button'
                                                className={`neuralNetworkFavoriteToggle ${network.is_favorite ? 'active' : ''}`.trim()}
                                                onClick={() => void toggleNetworkFavorite(network)}
                                                title={network.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
                                                aria-label={network.is_favorite ? `Remove ${getNetworkDisplayLabel(network)} from favorites` : `Add ${getNetworkDisplayLabel(network)} to favorites`}
                                                disabled={isTogglingNetworkFavoriteId === network.id}
                                            >
                                                ★
                                            </button>
                                        ) : null}
                                    </div>
                                ))}
                            </div>
                        ))}
                    </div>
                </aside>

                <section className='neuralContent'>
                    {!selectedNetwork && (
                        <div className='neuralEmptyState'>Select a neural network to inspect and run experiments.</div>
                    )}

                    {selectedNetwork && (
                        <>
                            {completionCard ? (
                                <div className={`neuralCompletionCard ${getEvaluationTone(completionCard.scoreOutOfTen)}`}>
                                    <div className='neuralCompletionCardHeader'>
                                        <div className='neuralCompletionCardTitle'>Run completed</div>
                                        <button
                                            type='button'
                                            className='neuralCompletionCardClose'
                                            onClick={() => setCompletionCard(null)}
                                            aria-label='Dismiss completed run card'
                                        >
                                            ×
                                        </button>
                                    </div>
                                    <div className='neuralCompletionCardBody'>
                                        <div>{completionCard.runLabel}</div>
                                        <div>
                                            Score: <strong>{formatRunCompositeScore(completionCard.scoreOutOfTen)}</strong>
                                        </div>
                                        {Number.isFinite(completionCard.rawScore) ? (
                                            <div>
                                                Raw: <strong>{formatScore(completionCard.rawScore)}</strong>
                                            </div>
                                        ) : null}
                                    </div>
                                </div>
                            ) : null}

                            {isGuest ? (
                                <div className='neuralGuestDisplayCard'>
                                    Guest display only: training, testing, runtime cleanup, renaming, favorites, deletes, and run annotations are disabled.
                                </div>
                            ) : null}

                            <div className={`neuralStickyGroup ${isConsoleMaximized ? 'isPinned' : ''}`}>
                                <div className='neuralHeader'>
                                    <div>
                                        <div className='neuralTitle'>{getNetworkDisplayLabel(selectedNetwork)}</div>
                                        <div className='neuralDescription'>{selectedNetwork.description}</div>
                                        <div className='neuralHeaderMeta'>
                                            <span>{selectedNetwork.family_label}</span>
                                            <span>{selectedNetwork.task_label || selectedNetwork.network_type}</span>
                                        </div>
                                        <div className='neuralContextSummary'>
                                            {selectedRun ? (
                                                <span className='neuralContextChip'>
                                                    Run: <strong>{getRunDisplayLabel(selectedRun)}</strong>
                                                </span>
                                            ) : null}
                                        </div>
                                    </div>

                                    <div className='neuralSummary'>
                                        <div>Best run score: <strong>{formatRunCompositeScore(bestScoredTrainRun?.evaluation?.scoreOutOfTen)}</strong></div>
                                        <div>Best train run: <strong>{bestScoredTrainRun ? formatTimestamp(bestScoredTrainRun.started_at) : '--'}</strong></div>
                                        <div>{selectedNetwork.score_label || 'Promoted score'}: <strong>{formatScore(selectedNetwork.best_model?.score)}</strong></div>
                                        <div>Latest train run: <strong>{latestTrainRun ? formatTimestamp(latestTrainRun.started_at) : '--'}</strong></div>
                                    </div>
                                </div>

                                <div className='neuralTabs'>
                                    <div className='neuralTabsList'>
                                        {DETAIL_TABS.map((tab) => (
                                            <button
                                                key={tab.id}
                                                type='button'
                                                className={`neuralTabButton ${activeDetailTab === tab.id ? 'active' : ''}`}
                                                onClick={() => setActiveDetailTab(tab.id)}
                                            >
                                                {tab.label}
                                            </button>
                                        ))}
                                    </div>
                                    <div className='neuralTabsActions'>
                                        {activeDetailTab === 'config' && !isGuest ? (
                                            <>
                                                <button
                                                    type='button'
                                                    className='neuralTabActionButton neuralRunsToolbarButton'
                                                    onClick={() => void importConfigFromClipboard()}
                                                >
                                                    Import config
                                                </button>
                                                <button
                                                    type='button'
                                                    className='neuralTabActionButton neuralRunsToolbarButton'
                                                    onClick={() => void copyCurrentConfig()}
                                                >
                                                    Export config
                                                </button>
                                            </>
                                        ) : null}
                                        {activeDetailTab === 'data' ? (
                                            <button
                                                type='button'
                                                className='neuralTabActionButton neuralRunsToolbarButton'
                                                onClick={() => void copySelectedRunExport()}
                                                disabled={!selectedRun}
                                            >
                                                Export to clipboard
                                            </button>
                                        ) : null}
                                        {!isGuest ? (
                                            <button
                                                type='button'
                                                className='neuralTabActionButton neuralDangerButton'
                                                onClick={() => setIsDeleteNetworkOpen(true)}
                                                disabled={Boolean(activeJob)}
                                            >
                                                Delete
                                            </button>
                                        ) : null}
                                    </div>
                                </div>
                            </div>

                            {activeDetailTab === 'config' && (
                                <>
                                    <div className='neuralConfigTopRow'>
                                        <div className='neuralSection neuralAliasSection neuralAliasSectionFull'>
                                            <div className='neuralSectionTitle'>Network name</div>
                                            <div className='neuralSectionHint'>
                                                {isGuest
                                                    ? 'Guest demo shows the curated neural model without saving workspace changes.'
                                                    : 'Custom name for this neural network in your workspace. Leave blank to use the default name.'}
                                            </div>
                                            <div className='neuralAliasRow'>
                                                <input
                                                    type='text'
                                                    value={networkAliasDraft}
                                                    placeholder={selectedNetwork.label || selectedNetwork.id}
                                                    disabled={isGuest}
                                                    onChange={(event) => setNetworkAliasDraft(event.target.value)}
                                                />
                                                {!isGuest ? (
                                                    <div className='neuralConfigTransferRow'>
                                                        <button type='button' disabled={isSavingNetworkAlias} onClick={() => void saveNetworkAlias()}>
                                                            {isSavingNetworkAlias ? 'Saving...' : 'Save'}
                                                        </button>
                                                    </div>
                                                ) : null}
                                            </div>
                                        </div>
                                    </div>

                                    <div className='neuralSection'>
                                        <div className='neuralSectionTitle'>Network signature</div>
                                        <div className='neuralNetworkDescriptionCard'>
                                            <div className='neuralDescription'>{selectedNetwork.signature || selectedNetwork.description}</div>
                                        </div>
                                    </div>

                                    <div className='neuralSection'>
                                        <div className='neuralSectionTitle'>Architecture panels</div>
                                        <div className='neuralSectionHint'>
                                            Use these panels to separate the current dense architecture from the planned LSTM and convolutional flows for both supervised and reinforcement learning.
                                        </div>
                                        <NeuralArchitecturePanels network={selectedNetwork} />
                                    </div>

                                    <div className='neuralSection'>
                                        <div className='neuralSectionTitle'>Configuration</div>
                                        <div className='neuralSectionHint'>
                                            The neural dataset uses its own isolated market context. Symbol, timeframe and bars
                                            belong to this network config and no longer mirror the main chart automatically.
                                        </div>

                                        <div className='neuralConfigGroup'>
                                            {parameterGroups.map((group) => (
                                                <div key={group.id} className='neuralConfigGroup'>
                                                    <div className='neuralConfigGroupTitle'>{group.label}</div>
                                                    {group.id === 'normalization' && supportsArchitectureBuilder(selectedNetwork) ? (
                                                        <div className='neuralConfigSubgroup'>
                                                            <div className='neuralConfigSubgroupTitle'>Input normalization</div>
                                                            <NeuralNormalizationSelector
                                                                network={selectedNetwork}
                                                                value={configDraft.normalizationColumns}
                                                                readOnly={isGuest}
                                                                onChange={(value) => updateConfigField('normalizationColumns', value)}
                                                            />
                                                        </div>
                                                    ) : (
                                                        buildConfigSubgroups(group).map((subgroup) => (
                                                            <div key={subgroup.id} className='neuralConfigSubgroup'>
                                                                {subgroup.label ? (
                                                                    <div className='neuralConfigSubgroupTitle'>{subgroup.label}</div>
                                                                ) : null}
                                                                {subgroup.hint ? (
                                                                    <div className='neuralSectionHint neuralConfigSubgroupHint'>{subgroup.hint}</div>
                                                                ) : null}
                                                                <div className='neuralConfigGrid'>
                                                                    {subgroup.fields.map((field) => {
                                                                        return (
                                                                            <FieldShell
                                                                                key={field.key}
                                                                                label={field.type === 'boolean' ? '' : getFieldLabel(field)}
                                                                                description={getFieldDescription(field)}
                                                                            >
                                                                                {renderField(
                                                                                    field,
                                                                                    configDraft[field.key],
                                                                                    (value) => updateConfigField(field.key, value),
                                                                                    { readOnly: isGuest },
                                                                                )}
                                                                            </FieldShell>
                                                                        )
                                                                    })}
                                                                </div>
                                                            </div>
                                                        ))
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    </div>

                                    {supportsArchitectureBuilder(selectedNetwork) ? (
                                        <div className='neuralSection'>
                                            <div className='neuralSectionTitle'>Network architecture</div>
                                            <div className='neuralSectionHint'>
                                                Build the hidden dense stack visually. Each layer uses the output of the previous one.
                                            </div>
                                            <NeuralLayerEditor
                                                layers={configDraft.hiddenLayers}
                                                readOnly={isGuest}
                                                onChange={(value) => updateConfigField('hiddenLayers', normalizeHiddenLayers(value))}
                                            />
                                        </div>
                                    ) : null}

                                </>
                            )}

                            {activeDetailTab === 'run' && (
                                <>
                                    <div className='neuralSection'>
                                        <div className='neuralSectionTitle'>Runtime</div>
                                        {!displayedRuntimeJob && (
                                            <div className='neuralEmpty'>No active neural job.</div>
                                        )}
                                        {displayedRuntimeJob && (
                                            <div className='neuralRuntimeCard'>
                                                <div className='neuralRuntimeHeader'>
                                                    <div className='neuralRuntimeHeaderInfo'>
                                                        <strong>{displayedRuntimeJob.run_type}</strong> · {displayedRuntimeJob.status}
                                                    </div>
                                                    <div className='neuralRuntimeHeaderActions'>
                                                        {runtimeEtaOverlay?.runId === displayedRuntimeJob.run_id ? (
                                                            <div className='neuralRuntimeEtaBadge'>
                                                                <span className='neuralRuntimeEtaBadgeLabel'>Last known ETA</span>
                                                                <strong>
                                                                    {formatRuntimeSeconds(
                                                                        runtimeEtaOverlay.remainingSeconds
                                                                        ?? Math.max(0, runtimeEtaOverlay.etaSeconds - ((Date.now() - runtimeEtaOverlay.updatedAtMs) / 1000))
                                                                    )}
                                                                </strong>
                                                            </div>
                                                        ) : null}
                                                        <div>{Math.round((Number(displayedRuntimeJob.progress) || 0) * 100)}%</div>
                                                        {activeJob ? (
                                                            <button
                                                                type='button'
                                                                className='neuralRuntimeCancelButton'
                                                                onClick={() => void handleCancelActiveJob()}
                                                                disabled={isGuest || isCancellingJob || Boolean(activeJob.cancel_requested)}
                                                                title={isGuest ? guestRestrictionMessage : (activeJob.cancel_requested ? 'Cancellation requested' : 'Cancel running job')}
                                                                aria-label={activeJob.cancel_requested ? 'Cancellation requested' : 'Cancel running job'}
                                                            >
                                                                {activeJob.cancel_requested || isCancellingJob ? '...' : 'X'}
                                                            </button>
                                                        ) : null}
                                                    </div>
                                                </div>
                                                <div className='neuralProgressTrack'>
                                                    <div
                                                        className='neuralProgressFill'
                                                        style={{ width: `${Math.round((Number(displayedRuntimeJob.progress) || 0) * 100)}%` }}
                                                    />
                                                </div>
                                                <div className={`neuralRuntimeFeedState ${displayedRuntimeJob.data_feed_status || 'idle'}`}>
                                                    <strong>{displayedRuntimeJob.data_feed_label || 'Runtime feed'}</strong>
                                                    <span>{displayedRuntimeJob.data_feed_detail || 'Waiting for neural runtime updates.'}</span>
                                                </div>
                                                <div className='neuralRuntimeMeta'>
                                                    <span>Phase: {displayedRuntimeJob.phase_label || displayedRuntimeJob.phase || '--'}</span>
                                                    {displayedRuntimeJob.detail ? <span>Detail: {displayedRuntimeJob.detail}</span> : null}
                                                    <span>Started: {formatTimestamp(displayedRuntimeJob.started_at)}</span>
                                                    <span>Elapsed: {formatRuntimeSeconds(displayedRuntimeJob.elapsed_seconds ?? displayedRuntimeJob.duration_seconds)}</span>
                                                    <span>ETA: {formatRuntimeSeconds(displayedRuntimeJob.eta_seconds)}</span>
                                                    <span>Last update: {formatRuntimeAge(displayedRuntimeJob.update_age_seconds)}</span>
                                                    <span>Heartbeat: {formatRuntimeAge(displayedRuntimeJob.heartbeat_age_seconds)}</span>
                                                    {displayedRuntimeJob.finished_at ? <span>Finished: {formatTimestamp(displayedRuntimeJob.finished_at)}</span> : null}
                                                    {displayedRuntimeJob.total_episodes ? (
                                                        <span>
                                                            Episodes: {formatCount(displayedRuntimeJob.current_episode)} / {formatCount(displayedRuntimeJob.total_episodes)}
                                                        </span>
                                                    ) : null}
                                                    {displayedRuntimeJob.last_episode_reward != null ? (
                                                        <span>Last reward: {formatScore(displayedRuntimeJob.last_episode_reward)}</span>
                                                    ) : null}
                                                    {displayedRuntimeJob.last_episode_steps != null ? (
                                                        <span>Last episode steps: {formatCount(displayedRuntimeJob.last_episode_steps)}</span>
                                                    ) : null}
                                                    <span>Run id: {displayedRuntimeJob.run_id}</span>
                                                    {displayedRuntimeJob.cancel_requested ? <span>Cancellation requested</span> : null}
                                                    {displayedRuntimeJob.auto_sanitize_recommended ? <span>Autosanitize recommended</span> : null}
                                                </div>
                                                <div className='neuralLogPanel'>
                                                    {displayedRuntimeLogs.map((entry, index) => (
                                                        <div key={`${entry.timestamp}-${index}`} className={`neuralLogEntry ${entry.level || 'info'}`}>
                                                            <span className='neuralLogTime'>{formatIsoTimestamp(entry.timestamp)}</span>
                                                            <span>{entry.message}</span>
                                                        </div>
                                                    ))}
                                                    {displayedRuntimeError ? (
                                                        <div className='neuralLogEntry error neuralLogEntryFull'>
                                                            <span className='neuralLogTime'>Runtime error</span>
                                                            <span>{displayedRuntimeError}</span>
                                                        </div>
                                                    ) : null}
                                                    {displayedRuntimeLogs.length === 0 && (
                                                        <div className='neuralEmpty'>
                                                            {displayedRuntimeJob.last_message
                                                                || displayedRuntimeJob.data_feed_detail
                                                                || displayedRuntimeJob.detail
                                                                || `Waiting in ${displayedRuntimeJob.phase_label || displayedRuntimeJob.phase || 'runtime'}...`}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                        <div className='neuralRuntimeControls'>
                                            <button
                                                type='button'
                                                className='neuralPrimary'
                                                onClick={() => void handleTrain()}
                                                disabled={isGuest || isTraining || isTesting || Boolean(activeJob)}
                                                title={isGuest ? guestRestrictionMessage : undefined}
                                            >
                                                {isTraining ? `${selectedNetwork.train_action_label || 'Train'}ing...` : (selectedNetwork.train_action_label || 'Train')}
                                            </button>
                                            <button
                                                type='button'
                                                onClick={() => void handleTest()}
                                                disabled={isGuest || isTraining || isTesting || Boolean(activeJob)}
                                                title={isGuest ? guestRestrictionMessage : undefined}
                                            >
                                                {isTesting ? `${selectedNetwork.test_action_label || 'Test'}ing...` : (selectedNetwork.test_action_label || 'Test')}
                                            </button>
                                            <select
                                                className='neuralTestSourceSelect'
                                                value={testSourceMode}
                                                onChange={(event) => {
                                                    const nextMode = event.target.value
                                                    setTestSourceMode(nextMode)
                                                    if (selectedNetwork?.id) {
                                                        setTestSourceModeByNetwork((current) => ({
                                                            ...current,
                                                            [selectedNetwork.id]: nextMode,
                                                        }))
                                                    }
                                                }}
                                                disabled={isGuest || isTraining || isTesting || Boolean(activeJob)}
                                            >
                                                {testSourceOptions.map((option) => (
                                                    <option key={option.id} value={option.id}>
                                                        {option.label}
                                                    </option>
                                                ))}
                                            </select>
                                            <button
                                                type='button'
                                                onClick={() => void handleSanitizeRuntime()}
                                                disabled={isGuest || isSanitizingRuntime}
                                                title={isGuest ? guestRestrictionMessage : 'Request cancellation, clean orphan runtime state, and refresh the neural worker status.'}
                                            >
                                                {isSanitizingRuntime ? 'Sanitizing...' : 'Sanitize runtime'}
                                            </button>
                                            <button
                                                type='button'
                                                onClick={() => void refreshSelectedNetwork()}
                                                disabled={isRefreshingNetwork}
                                                title='Refresh this neural network view manually.'
                                            >
                                                {isRefreshingNetwork ? 'Refreshing...' : 'Refresh'}
                                            </button>
                                        </div>
                                    </div>

                                </>
                            )}

                            {activeDetailTab === 'data' && (
                                <div className='neuralTabPanel neuralDataTabPanel'>
                                    <div className='neuralSection neuralSectionRecentRuns'>
                                        <div className='neuralSectionTitle'>Recent runs</div>
                                        <div className='neuralRunsToolbar'>
                                            <select
                                                className='neuralRunsFilterSelect'
                                                value={runTypeFilter}
                                                onChange={(event) => setRunTypeFilter(event.target.value)}
                                            >
                                                <option value='all'>All types</option>
                                                <option value='train'>Train only</option>
                                                <option value='test'>Test only</option>
                                            </select>
                                            <select
                                                className='neuralRunsFilterSelect'
                                                value={runStatusFilter}
                                                onChange={(event) => setRunStatusFilter(event.target.value)}
                                            >
                                                <option value='all'>All statuses</option>
                                                <option value='completed'>Completed</option>
                                                <option value='running'>Running</option>
                                                <option value='queued'>Queued</option>
                                                <option value='cancelled'>Cancelled</option>
                                                <option value='failed'>Failed</option>
                                            </select>
                                            <select
                                                className='neuralRunsFilterSelect'
                                                value={runSortMode}
                                                onChange={(event) => setRunSortMode(event.target.value)}
                                            >
                                                <option value='started_desc'>Started newest first</option>
                                                <option value='started_asc'>Started oldest first</option>
                                                <option value='score_desc'>Best run score first</option>
                                                <option value='score_asc'>Worst run score first</option>
                                                <option value='direction_desc'>Best direction first</option>
                                                <option value='direction_asc'>Worst direction first</option>
                                                <option value='type_asc'>Type A-Z</option>
                                                <option value='type_desc'>Type Z-A</option>
                                                <option value='status_asc'>Status A-Z</option>
                                                <option value='status_desc'>Status Z-A</option>
                                            </select>
                                            <select
                                                className='neuralRunsFilterSelect'
                                                value={archiveFilter}
                                                onChange={(event) => setArchiveFilter(event.target.value)}
                                            >
                                                <option value='active'>Hide archived</option>
                                                <option value='all'>Show all runs</option>
                                                <option value='archived'>Archived only</option>
                                            </select>
                                            <select
                                                className='neuralRunsFilterSelect'
                                                value={favoritesFilter}
                                                onChange={(event) => setFavoritesFilter(event.target.value)}
                                            >
                                                <option value='all'>All favorites</option>
                                                <option value='favorites'>Favorites only</option>
                                            </select>
                                            {!isGuest ? (
                                                <button
                                                    type='button'
                                                    className='neuralRunsToolbarButton'
                                                    disabled={isArchivingFailedRuns || isSavingRunMeta}
                                                    onClick={() => void archiveAllFailedRuns()}
                                                >
                                                    {isArchivingFailedRuns ? 'Archiving failed...' : 'Archive Failed/Canceled'}
                                                </button>
                                            ) : null}
                                            <button
                                                type='button'
                                                className='neuralRunsToolbarButton'
                                                disabled={prioritizedRuns.length === 0}
                                                onClick={() => void copyRunsTableExport()}
                                            >
                                                Export table to clipboard
                                            </button>
                                        </div>
                                                <div className='neuralRunsTable'>
                                                <div className='neuralRunsTableHeader'>
                                                    <span className='neuralRunsTableIndexHeader'>IDX</span>
                                                    <span>{renderRunSortHeader('Type', 'type')}</span>
                                                    <span>{renderRunSortHeader('Status', 'status')}</span>
                                                    <span>{renderRunSortHeader('Run score', 'score')}</span>
                                                    <span>{renderRunSortHeader('Started', 'started')}</span>
                                                    <span>{renderRunSortHeader('Direction', 'direction')}</span>
                                                    <span>Config</span>
                                                    <span>Actions</span>
                                                </div>

                                            {pagedRuns.map((run) => (
                                                <div
                                                    key={run.id}
                                                    role='button'
                                                    tabIndex={0}
                                                    className={`neuralRunsTableRow ${selectedRun?.id === run.id ? 'active' : ''}`}
                                                    onClick={() => setSelectedRunId(run.id)}
                                                    onKeyDown={(event) => {
                                                        if (event.key === 'Enter' || event.key === ' ') {
                                                            event.preventDefault()
                                                            setSelectedRunId(run.id)
                                                        }
                                                    }}
                                                >
                                                    <span className='neuralRunsTableIndexCell'>{formatRunGeneratedIndex(run, runGeneratedIndexMap)}</span>
                                                    <span>
                                                        {getRunDisplayLabel(run)}
                                                        <span className='neuralRunRowBadges'>
                                                            {run.id === bestModelRunId ? <NeuralRunBadge tone='accent'>Best model</NeuralRunBadge> : null}
                                                            {run.id === promotedBestModelRunId && run.id !== bestModelRunId ? <NeuralRunBadge tone='neutral'>Promoted</NeuralRunBadge> : null}
                                                            {run.id === latestCompletedTrainRun?.id ? <NeuralRunBadge tone='warning'>Latest train</NeuralRunBadge> : null}
                                                            {run.is_baseline ? <NeuralRunBadge tone='accent'>Baseline</NeuralRunBadge> : null}
                                                            {run.is_favorite ? <NeuralRunBadge tone='success'>Favorite</NeuralRunBadge> : null}
                                                            {run.is_archived ? <NeuralRunBadge tone='muted'>Archived</NeuralRunBadge> : null}
                                                            {run.artifact?.exists ? <NeuralRunBadge tone='success'>File saved</NeuralRunBadge> : null}
                                                        </span>
                                                    </span>
                                                    <span>{run.status}</span>
                                                    <span>{formatRunCompositeScore(run.evaluation?.scoreOutOfTen)}</span>
                                                    <span>{formatTimestamp(run.started_at)}</span>
                                                    <span>{formatPercent(getRunDirectionValue(run))}</span>
                                                    <span className='neuralRunConfigCell'>
                                                        <button
                                                            type='button'
                                                            className='neuralRunConfigButton'
                                                            onClick={(event) => {
                                                                event.stopPropagation()
                                                                void copyRunConfig(run)
                                                            }}
                                                        >
                                                            Export
                                                        </button>
                                                        {!isGuest ? (
                                                            <button
                                                                type='button'
                                                                className='neuralRunConfigButton'
                                                                onClick={(event) => {
                                                                    event.stopPropagation()
                                                                    loadConfigFromRun(run)
                                                                    setActiveDetailTab('config')
                                                                }}
                                                            >
                                                                Load
                                                            </button>
                                                        ) : null}
                                                    </span>
                                                    <span className='neuralRunActionsCell'>
                                                        {isGuest ? (
                                                            <span className='neuralGuestReadOnlyNote'>Read-only</span>
                                                        ) : (
                                                            <>
                                                                <button
                                                                    type='button'
                                                                    className={`neuralArchiveIconButton ${run.is_archived ? 'active' : ''}`}
                                                                    title={run.is_archived ? 'Restore run' : 'Archive run'}
                                                                    aria-label={run.is_archived ? 'Restore run' : 'Archive run'}
                                                                    disabled={isSavingRunMeta}
                                                                    onClick={(event) => {
                                                                        event.stopPropagation()
                                                                        void toggleRunArchive(run)
                                                                    }}
                                                                >
                                                                    <NeuralActionIcon type='archive' />
                                                                </button>
                                                                <button
                                                                    type='button'
                                                                    className='neuralArchiveIconButton neuralDeleteFileIconButton'
                                                                    title={run.artifact?.exists ? 'Delete model file' : 'No model file saved'}
                                                                    aria-label={run.artifact?.exists ? 'Delete model file' : 'No model file saved'}
                                                                    disabled={!run.artifact?.exists || Boolean(isDeletingRunArtifactId) || Boolean(isDeletingRunId)}
                                                                    onClick={(event) => {
                                                                        event.stopPropagation()
                                                                        setRunArtifactDeleteConfirm(run)
                                                                    }}
                                                                >
                                                                    <NeuralActionIcon type='delete-file' />
                                                                </button>
                                                                <button
                                                                    type='button'
                                                                    className='neuralArchiveIconButton neuralDeleteRunIconButton'
                                                                    title='Delete run'
                                                                    aria-label='Delete run'
                                                                    disabled={Boolean(isDeletingRunId) || Boolean(isDeletingRunArtifactId)}
                                                                    onClick={(event) => {
                                                                        event.stopPropagation()
                                                                        setRunDeleteConfirm(run)
                                                                    }}
                                                                >
                                                                    <NeuralActionIcon type='delete-run' />
                                                                </button>
                                                            </>
                                                        )}
                                                    </span>
                                                    {!isGuest && getRunErrorPreview(run) ? (
                                                        <div
                                                            className='neuralRunErrorPreview'
                                                        >
                                                            <button
                                                                type='button'
                                                                className='neuralRunErrorCopyButton'
                                                                title='Copy full error'
                                                                aria-label='Copy full error'
                                                                onClick={(event) => {
                                                                    event.stopPropagation()
                                                                    void copyRunError(run)
                                                                }}
                                                            >
                                                                <span className='neuralRunErrorCopyIcon'>⧉</span>
                                                            </button>
                                                            <span>Error: {getRunErrorPreview(run)}</span>
                                                        </div>
                                                    ) : null}
                                                </div>
                                            ))}

                                            {prioritizedRuns.length === 0 && (
                                                <div className='neuralEmpty'>No runs yet.</div>
                                            )}
                                        </div>
                                        <div className='neuralPagination'>
                                            <button
                                                type='button'
                                                disabled={runsPage <= 1}
                                                onClick={() => setRunsPage((current) => Math.max(1, current - 1))}
                                            >
                                                ←
                                            </button>
                                            <label className='neuralPaginationRows'>
                                                <span>Rows</span>
                                                <input
                                                    type='number'
                                                    min='1'
                                                    max='200'
                                                    value={rowsPerPage}
                                                    onChange={(event) => setRowsPerPage(Math.max(1, Number(event.target.value) || RUNS_PAGE_SIZE))}
                                                />
                                            </label>
                                            <span>Page {runsPage} / {totalRunPages}</span>
                                            <button
                                                type='button'
                                                disabled={runsPage >= totalRunPages}
                                                onClick={() => setRunsPage((current) => Math.min(totalRunPages, current + 1))}
                                            >
                                                →
                                            </button>
                                        </div>
                                        <div className='neuralPagination'>
                                            <span>
                                                Showing {pagedRuns.length} of {prioritizedRuns.length} runs
                                            </span>
                                        </div>
                                    </div>

                                    <div className='neuralSection neuralSectionRunDetails'>
                                        <div className='neuralSectionTitle'>Run details</div>
                                        {!selectedRun && (
                                            <div className='neuralEmpty'>Select a run to inspect its configuration and metrics.</div>
                                        )}
                                        {selectedRun && (
                                            <>
                                                <div className='neuralCompareBar'>
                                                    <div className='neuralCompareLabel'>Compare against</div>
                                                    <select
                                                        className='neuralTestSourceSelect'
                                                        value={comparisonRun?.id || ''}
                                                        onChange={(event) => setComparisonRunId(event.target.value)}
                                                    >
                                                        <option value=''>No comparison</option>
                                                        {comparisonRunOptions.map((run) => (
                                                            <option key={run.id} value={run.id}>
                                                                {formatRunCompareOptionLabel(run, runGeneratedIndexMap)}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>

                                                <NeuralRunEvaluationCard
                                                    evaluation={selectedRunEvaluation}
                                                    network={selectedNetwork}
                                                    comparisonRun={comparisonRun}
                                                />

                                                {comparisonSummary ? (
                                                    <div className='neuralComparisonSummary'>
                                                        <div className='neuralComparisonSummaryTitle'>Comparison verdict</div>
                                                        {comparisonSummary.primaryWinner ? (
                                                            <div className='neuralComparisonSummaryLine'>{comparisonSummary.primaryWinner}</div>
                                                        ) : null}
                                                        <div className='neuralComparisonSummaryLine'>{comparisonSummary.edge}</div>
                                                        {comparisonSummary.highlights.length > 0 ? (
                                                            <div className='neuralComparisonSummaryHighlights'>
                                                                {comparisonSummary.highlights.map((item) => (
                                                                    <span key={item}>{item}</span>
                                                                ))}
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                ) : null}

                                                {!isGuest ? (
                                                    <>
                                                        <div className='neuralRunMetaActions'>
                                                            <button
                                                                type='button'
                                                                disabled={isSavingRunMeta}
                                                                onClick={() => void updateRunAnnotations(
                                                                    selectedRun.id,
                                                                    { is_favorite: !selectedRun.is_favorite },
                                                                    selectedRun.is_favorite ? 'Removed neural run from favorites.' : 'Marked neural run as favorite.',
                                                                )}
                                                            >
                                                                {selectedRun.is_favorite ? 'Unfavorite run' : 'Favorite run'}
                                                            </button>
                                                            <button
                                                                type='button'
                                                                disabled={isSavingRunMeta}
                                                                onClick={() => void updateRunAnnotations(
                                                                    selectedRun.id,
                                                                    { is_baseline: !selectedRun.is_baseline },
                                                                    selectedRun.is_baseline ? 'Removed neural run baseline.' : 'Marked neural run as baseline.',
                                                                )}
                                                            >
                                                                {selectedRun.is_baseline ? 'Clear baseline' : 'Set as baseline'}
                                                            </button>
                                                            <button
                                                                type='button'
                                                                disabled={isSavingRunMeta}
                                                                onClick={() => void updateRunAnnotations(
                                                                    selectedRun.id,
                                                                    { is_archived: !selectedRun.is_archived },
                                                                    selectedRun.is_archived ? 'Restored neural run from archive.' : 'Archived neural run.',
                                                                )}
                                                            >
                                                                {selectedRun.is_archived ? 'Restore run' : 'Archive run'}
                                                            </button>
                                                        </div>

                                                        <div className='neuralRunNoteEditor'>
                                                            <div className='neuralConfigGroupTitle'>Experiment note</div>
                                                            <textarea
                                                                value={runNoteDraft}
                                                                onChange={(event) => setRunNoteDraft(event.target.value)}
                                                                placeholder='Write a short note about what changed, what you expected, or why this run matters.'
                                                            />
                                                            <div className='neuralRunNoteActions'>
                                                                <button
                                                                    type='button'
                                                                    disabled={isSavingRunMeta}
                                                                    onClick={() => void updateRunAnnotations(
                                                                        selectedRun.id,
                                                                        { note: runNoteDraft },
                                                                        'Saved neural run note.',
                                                                    )}
                                                                >
                                                                    Save note
                                                                </button>
                                                            </div>
                                                        </div>
                                                    </>
                                                ) : (
                                                    <div className='neuralGuestReadOnlyPanel'>
                                                        Guest demo can inspect this run, but run annotations and history edits are disabled.
                                                    </div>
                                                )}

                                                {!isGuest && selectedRun.error ? (
                                                    <div className='neuralRunError'>{selectedRun.error}</div>
                                                ) : null}

                                                {selectedRunMetricSections.length > 0 ? (
                                                    <div className='neuralMetricSectionsGrid'>
                                                        {selectedRunMetricSections.map((section) => (
                                                            <NeuralMetricSection
                                                                key={section.id}
                                                                title={section.label}
                                                                hint={section.hint}
                                                                metrics={section.metrics}
                                                            />
                                                        ))}
                                                    </div>
                                                ) : null}

                                                <div className='neuralRunDetailGrid'>
                                                    <div className='neuralRunDetailBlock'>
                                                        <div className='neuralConfigGroupTitle'>Configuration diff</div>
                                                        <div className='neuralCompareTable'>
                                                            <div className='neuralCompareTableHeader'>
                                                                <span>Parameter</span>
                                                                <span>This run</span>
                                                                <span>{comparisonRun ? 'Compared run' : 'Compared run'}</span>
                                                            </div>
                                                            {configComparisonRows.map((row) => (
                                                                <div key={row.key} className={`neuralCompareTableRow ${row.changed ? 'changed' : ''}`}>
                                                                    <span>{row.key}</span>
                                                                    <span>{row.currentValue}</span>
                                                                    <span>{row.compareValue}</span>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    </div>
                                                    <div className='neuralRunDetailBlock'>
                                                        <div className='neuralConfigGroupTitle'>Raw metrics</div>
                                                        <pre className='neuralCodeBlock'>
                                                            {JSON.stringify(selectedRun.metrics || {}, null, 2)}
                                                        </pre>
                                                    </div>
                                                </div>
                                            </>
                                        )}
                                    </div>

                                    <div className='neuralSection neuralSectionSnapshot'>
                                        <div className='neuralSectionTitle'>Experiment snapshot</div>
                                        <div className='neuralMetricsGrid'>
                                            {resolvedSnapshotCards.map((card) => (
                                                <NeuralMetricCard
                                                    key={card.id}
                                                    label={card.label}
                                                    value={card.value}
                                                    hint={card.hint}
                                                />
                                            ))}
                                        </div>
                                    </div>

                                    <div className='neuralSection neuralSectionPerformance'>
                                        <div className='neuralSectionTitle'>Performance lenses</div>
                                        <div className='neuralSectionHint'>
                                            The selected network defines which metrics matter most for validation and final testing.
                                        </div>
                                        <div className='neuralMetricSectionsGrid'>
                                            {resolvedMetricSections.map((section) => (
                                                <NeuralMetricSection
                                                    key={section.id}
                                                    title={section.label}
                                                    hint={section.hint}
                                                    metrics={section.metrics}
                                                />
                                            ))}
                                            {resolvedMetricSections.length === 0 && (
                                                <div className='neuralEmpty'>No performance metrics available yet.</div>
                                            )}
                                        </div>
                                    </div>

                                    <div className='neuralSection neuralSectionShortlist'>
                                        <div className='neuralSectionTitle'>Shortlist</div>
                                        {shortlistRuns.length === 0 ? (
                                            <div className='neuralEmpty'>No shortlisted experiments yet.</div>
                                        ) : (
                                            <div className='neuralShortlistGrid'>
                                                {shortlistRuns.map((run) => (
                                                    <button
                                                        key={run.id}
                                                        type='button'
                                                        className={`neuralShortlistCard ${selectedRun?.id === run.id ? 'active' : ''}`}
                                                        onClick={() => setSelectedRunId(run.id)}
                                                    >
                                                        <div className='neuralShortlistHeader'>
                                                            <span>{getRunDisplayLabel(run)}</span>
                                                            <span>{formatRunCompositeScore(run.evaluation?.scoreOutOfTen)}</span>
                                                        </div>
                                                        <div className='neuralShortlistMeta'>
                                                            {run.is_baseline ? <NeuralRunBadge tone='accent'>Baseline</NeuralRunBadge> : null}
                                                            {run.is_favorite ? <NeuralRunBadge tone='success'>Favorite</NeuralRunBadge> : null}
                                                            {!run.is_baseline && !run.is_favorite ? <NeuralRunBadge tone='neutral'>Candidate</NeuralRunBadge> : null}
                                                        </div>
                                                        <div className='neuralShortlistMeta'>{run.status} · {formatTimestamp(run.started_at)}</div>
                                                        <div className='neuralShortlistMeta'>{formatDuration(run.duration_seconds)}</div>
                                                    </button>
                                                ))}
                                            </div>
                                        )}
                                    </div>

                                    {!isGuest ? (
                                        <div className='neuralSection neuralSectionDanger'>
                                            <div className='neuralSectionTitle'>Reset history</div>
                                            <div className='neuralSectionHint'>
                                                Remove all runs and saved model artifacts for this neural network. Presets and network name stay untouched.
                                            </div>
                                            <div className='neuralConfigTransferRow'>
                                                <button type='button' className='neuralDangerButton' onClick={() => setIsResetHistoryOpen(true)}>
                                                    Reset neural history
                                                </button>
                                            </div>
                                        </div>
                                    ) : null}
                                </div>
                            )}
                        </>
                    )}
                </section>
            </div>

            {!isGuest && isResetHistoryOpen && (
                <div className='neuralOverlay' role='dialog' aria-modal='true' aria-label='Confirm neural history reset'>
                    <div className='neuralOverlayBackdrop' onClick={() => !isResettingHistory && setIsResetHistoryOpen(false)} />
                    <div className='neuralOverlayCard'>
                        <div className='neuralOverlayTitle'>Reset neural history?</div>
                        <div className='neuralOverlayText'>
                            This will delete every saved model artifact and every recorded run for <strong>{getNetworkDisplayLabel(selectedNetwork)}</strong>.
                        </div>
                        <div className='neuralOverlayText'>Presets and the custom network name will be kept.</div>
                        <div className='neuralOverlayActions'>
                            <button type='button' onClick={() => setIsResetHistoryOpen(false)} disabled={isResettingHistory}>Cancel</button>
                            <button type='button' className='neuralDangerButton' onClick={() => void handleResetNetworkHistory()} disabled={isResettingHistory}>
                                {isResettingHistory ? 'Resetting...' : 'Confirm reset'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {!isGuest && isDeleteNetworkOpen && (
                <div className='neuralOverlay' role='dialog' aria-modal='true' aria-label='Confirm neural network deletion'>
                    <div className='neuralOverlayBackdrop' onClick={() => !isDeletingNetwork && setIsDeleteNetworkOpen(false)} />
                    <div className='neuralOverlayCard'>
                        <div className='neuralOverlayTitle'>Delete neural network?</div>
                        <div className='neuralOverlayText'>
                            This will remove <strong>{getNetworkDisplayLabel(selectedNetwork)}</strong> from this workspace.
                        </div>
                        <div className='neuralOverlayText'>
                            Presets, custom name, runs, scores, and saved model artifacts for this network will be deleted.
                        </div>
                        <div className='neuralOverlayActions'>
                            <button type='button' onClick={() => setIsDeleteNetworkOpen(false)} disabled={isDeletingNetwork}>Cancel</button>
                            <button type='button' className='neuralDangerButton' onClick={() => void handleDeleteNetwork()} disabled={isDeletingNetwork}>
                                {isDeletingNetwork ? 'Deleting...' : 'Confirm delete'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {!isGuest && runArtifactDeleteConfirm && (
                <div className='neuralOverlay' role='dialog' aria-modal='true' aria-label='Confirm neural model file deletion'>
                    <div className='neuralOverlayBackdrop' onClick={() => !isDeletingRunArtifactId && setRunArtifactDeleteConfirm(null)} />
                    <div className='neuralOverlayCard'>
                        <div className='neuralOverlayTitle'>Delete model file?</div>
                        <div className='neuralOverlayText'>
                            This will delete the saved model artifact for run <strong>{runArtifactDeleteConfirm.id.slice(0, 8)}</strong>.
                        </div>
                        <div className='neuralOverlayText'>
                            The run record will stay in history, but its file and sidecar metadata will be removed.
                        </div>
                        <div className='neuralOverlayActions'>
                            <button type='button' onClick={() => setRunArtifactDeleteConfirm(null)} disabled={Boolean(isDeletingRunArtifactId)}>Cancel</button>
                            <button
                                type='button'
                                className='neuralDangerButton'
                                onClick={() => void handleDeleteRunArtifact(runArtifactDeleteConfirm)}
                                disabled={Boolean(isDeletingRunArtifactId)}
                            >
                                {isDeletingRunArtifactId ? 'Deleting...' : 'Confirm delete file'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {!isGuest && runDeleteConfirm && (
                <div className='neuralOverlay' role='dialog' aria-modal='true' aria-label='Confirm neural run deletion'>
                    <div className='neuralOverlayBackdrop' onClick={() => !isDeletingRunId && setRunDeleteConfirm(null)} />
                    <div className='neuralOverlayCard'>
                        <div className='neuralOverlayTitle'>Delete run?</div>
                        <div className='neuralOverlayText'>
                            This will permanently remove run <strong>{runDeleteConfirm.id.slice(0, 8)}</strong> from the neural history.
                        </div>
                        <div className='neuralOverlayText'>
                            If this run has a saved model file, that file will also be deleted.
                        </div>
                        <div className='neuralOverlayActions'>
                            <button type='button' onClick={() => setRunDeleteConfirm(null)} disabled={Boolean(isDeletingRunId)}>Cancel</button>
                            <button
                                type='button'
                                className='neuralDangerButton'
                                onClick={() => void handleDeleteRun(runDeleteConfirm)}
                                disabled={Boolean(isDeletingRunId)}
                            >
                                {isDeletingRunId ? 'Deleting...' : 'Confirm delete run'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

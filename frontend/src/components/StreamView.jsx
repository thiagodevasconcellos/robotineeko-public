import { useEffect, useMemo, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '../api'
import { Chart } from './Chart'
import { normalizeChartSettings } from '../utils/chartSettings.jsx'
import { resolveStreamRuntimeSeed, STREAM_LAUNCH_KEY_QUERY_PARAM } from '../utils/streamView.js'
import './StreamView.css'

const STREAM_HISTORY_LIMIT = 800
const STREAM_CHART_SEED_BARS = 1000
const DEFAULT_STREAM_TICKER_HISTORY_LIMIT = 25
const MIN_STREAM_TICKER_HISTORY_LIMIT = 1
const MAX_STREAM_TICKER_HISTORY_LIMIT = 200
const STREAM_TICKER_TITLE_INTERVAL = 4
const STREAM_CONFIG_QUERY_PARAM = 'panel'
const STREAM_CONFIG_QUERY_VALUE = 'config'
const STREAM_SETTINGS_STORAGE_PREFIX = 'robotineeko:stream-settings:'

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

function formatMoneyWithUnit(value, unit = '') {
    const numeric = Number(value)
    const safeUnit = String(unit || '').trim()
    if (!Number.isFinite(numeric)) {
        return safeUnit ? `0.00 ${safeUnit}` : '0.00'
    }
    return safeUnit ? `${numeric.toFixed(2)} ${safeUnit}` : numeric.toFixed(2)
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

function formatSignedMoneyWithUnit(value, unit = '') {
    const amount = formatSignedMoney(value)
    const safeUnit = String(unit || '').trim()
    return safeUnit ? `${amount} ${safeUnit}` : amount
}

function formatSignedPercent(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '0.0%'
    }
    const sign = numeric > 0 ? '+' : ''
    return `${sign}${(numeric * 100).toFixed(1)}%`
}

function calculateRelativeReturn(value, initialCapital) {
    const numeric = Number(value)
    const base = Number(initialCapital)
    if (!Number.isFinite(numeric) || !Number.isFinite(base) || base <= 0) {
        return null
    }
    return numeric / base
}

function formatMoneyWithPercent(value, initialCapital, unit = '') {
    const relativeReturn = calculateRelativeReturn(value, initialCapital)
    const amount = formatSignedMoneyWithUnit(value, unit)
    if (relativeReturn === null) {
        return amount
    }
    return `${amount} · ${formatSignedPercent(relativeReturn)}`
}

function formatCapitalWithPercent(value, initialCapital, unit = '') {
    const numeric = Number(value)
    const base = Number(initialCapital)
    const amount = formatMoneyWithUnit(numeric, unit)
    if (!Number.isFinite(numeric) || !Number.isFinite(base) || base <= 0) {
        return amount
    }
    return `${amount} · ${formatSignedPercent((numeric - base) / base)}`
}

function formatPercent(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '0.0%'
    }
    return `${(numeric * 100).toFixed(1)}%`
}

function formatDateTime(value) {
    const numeric = normalizeUnixTimestamp(value)
    if (!numeric) {
        return '—'
    }
    try {
        return new Date(numeric * 1000).toLocaleString()
    } catch {
        return '—'
    }
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

function formatDateBr(value) {
    const numeric = normalizeUnixTimestamp(value)
    if (!numeric) {
        return '—'
    }

    try {
        const date = new Date(numeric * 1000)
        const day = String(date.getDate()).padStart(2, '0')
        const month = String(date.getMonth() + 1).padStart(2, '0')
        const year = String(date.getFullYear())
        return `${day}/${month}/${year}`
    } catch {
        return '—'
    }
}

function formatTimeBr(value) {
    const numeric = normalizeUnixTimestamp(value)
    if (!numeric) {
        return '—'
    }

    try {
        const date = new Date(numeric * 1000)
        const hours = String(date.getHours()).padStart(2, '0')
        const minutes = String(date.getMinutes()).padStart(2, '0')
        const seconds = String(date.getSeconds()).padStart(2, '0')
        return `${hours}:${minutes}:${seconds}`
    } catch {
        return '—'
    }
}

function formatSince(value) {
    const numeric = normalizeUnixTimestamp(value)
    if (!numeric) {
        return 'Waiting for activation'
    }

    const diffSeconds = Math.max(0, Math.floor(Date.now() / 1000) - numeric)
    const hours = Math.floor(diffSeconds / 3600)
    const minutes = Math.floor((diffSeconds % 3600) / 60)
    const seconds = diffSeconds % 60

    if (hours > 0) {
        return `${hours}h ${minutes}m`
    }
    if (minutes > 0) {
        return `${minutes}m ${seconds}s`
    }
    return `${seconds}s`
}

function formatDurationSpan(startValue, endValue) {
    const start = normalizeUnixTimestamp(startValue)
    const end = normalizeUnixTimestamp(endValue)
    if (!start || !end) {
        return '—'
    }

    const diffSeconds = Math.max(0, end - start)
    const days = Math.floor(diffSeconds / 86400)
    const hours = Math.floor((diffSeconds % 86400) / 3600)
    const minutes = Math.floor((diffSeconds % 3600) / 60)
    const seconds = diffSeconds % 60

    if (days > 0) {
        return `${days}d ${hours}h`
    }
    if (hours > 0) {
        return `${hours}h ${minutes}m`
    }
    if (minutes > 0) {
        return `${minutes}m ${seconds}s`
    }
    return `${seconds}s`
}

function calculateMonthlyResult(value, startValue, endValue) {
    const numeric = Number(value)
    const start = normalizeUnixTimestamp(startValue)
    const end = normalizeUnixTimestamp(endValue)
    if (!Number.isFinite(numeric) || !start || !end || end <= start) {
        return null
    }

    const elapsedSeconds = Math.max(1, end - start)
    return numeric * ((30 * 86400) / elapsedSeconds)
}

function readSessionSnapshot() {
    if (typeof window === 'undefined') {
        return null
    }

    try {
        const params = new URLSearchParams(window.location.search || '')
        const launchKey = String(params.get(STREAM_LAUNCH_KEY_QUERY_PARAM) || '').trim()
        if (!launchKey) {
            return null
        }
        const raw = window.localStorage.getItem(launchKey)
        if (!raw) {
            return null
        }
        const parsed = JSON.parse(raw)
        return parsed && typeof parsed === 'object' ? parsed : null
    } catch {
        return null
    }
}

function readStreamLaunchKeyFromLocation() {
    if (typeof window === 'undefined') {
        return ''
    }

    try {
        const params = new URLSearchParams(window.location.search || '')
        return String(params.get(STREAM_LAUNCH_KEY_QUERY_PARAM) || '').trim()
    } catch {
        return ''
    }
}

function readStreamPanelModeFromLocation() {
    if (typeof window === 'undefined') {
        return ''
    }

    try {
        const params = new URLSearchParams(window.location.search || '')
        return String(params.get(STREAM_CONFIG_QUERY_PARAM) || '').trim().toLowerCase()
    } catch {
        return ''
    }
}

function buildStreamSettingsStorageKey(launchKey = '') {
    const safeLaunchKey = String(launchKey || '').trim()
    return safeLaunchKey ? `${STREAM_SETTINGS_STORAGE_PREFIX}${safeLaunchKey}` : ''
}

function normalizeStreamTickerHistoryLimit(value) {
    const numeric = Math.trunc(Number(value))
    if (!Number.isFinite(numeric)) {
        return DEFAULT_STREAM_TICKER_HISTORY_LIMIT
    }
    return Math.min(MAX_STREAM_TICKER_HISTORY_LIMIT, Math.max(MIN_STREAM_TICKER_HISTORY_LIMIT, numeric))
}

function readStoredStreamSettings(launchKey = '') {
    const safeLaunchKey = String(launchKey || '').trim()
    if (!safeLaunchKey || typeof window === 'undefined') {
        return {
            tickerHistoryLimit: DEFAULT_STREAM_TICKER_HISTORY_LIMIT,
        }
    }

    try {
        const raw = window.localStorage.getItem(buildStreamSettingsStorageKey(safeLaunchKey)) || ''
        if (!raw) {
            return {
                tickerHistoryLimit: DEFAULT_STREAM_TICKER_HISTORY_LIMIT,
            }
        }
        const parsed = JSON.parse(raw)
        return {
            tickerHistoryLimit: normalizeStreamTickerHistoryLimit(parsed?.tickerHistoryLimit),
        }
    } catch {
        return {
            tickerHistoryLimit: DEFAULT_STREAM_TICKER_HISTORY_LIMIT,
        }
    }
}

function persistStoredStreamSettings(launchKey = '', settings = null) {
    const safeLaunchKey = String(launchKey || '').trim()
    if (!safeLaunchKey || typeof window === 'undefined') {
        return {
            tickerHistoryLimit: DEFAULT_STREAM_TICKER_HISTORY_LIMIT,
        }
    }

    const normalized = {
        tickerHistoryLimit: normalizeStreamTickerHistoryLimit(settings?.tickerHistoryLimit),
    }
    window.localStorage.setItem(
        buildStreamSettingsStorageKey(safeLaunchKey),
        JSON.stringify(normalized),
    )
    return normalized
}

function buildStreamConfigWindowUrl(launchKey = '') {
    if (typeof window === 'undefined') {
        return ''
    }

    const url = new URL(window.location.href)
    url.searchParams.set(STREAM_LAUNCH_KEY_QUERY_PARAM, String(launchKey || '').trim())
    url.searchParams.set(STREAM_CONFIG_QUERY_PARAM, STREAM_CONFIG_QUERY_VALUE)
    url.hash = ''
    return url.toString()
}

function formatVolume(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return '—'
    }
    return `${numeric.toFixed(2)} lot`
}

function buildStreamChartSettings(baseChartSettings, snapshot, liveTradeRuntime) {
    const snapshotChartSettings = snapshot?.chartSettings && typeof snapshot.chartSettings === 'object'
        ? snapshot.chartSettings
        : null

    const seededChartSettings = normalizeChartSettings({
        ...(baseChartSettings && typeof baseChartSettings === 'object' ? baseChartSettings : {}),
        ...(snapshotChartSettings || {}),
    })
    const seedBars = Math.min(
        Math.max(1, Number(seededChartSettings?.bars || STREAM_CHART_SEED_BARS) || STREAM_CHART_SEED_BARS),
        STREAM_CHART_SEED_BARS,
    )
    const runtimeSeed = resolveStreamRuntimeSeed(liveTradeRuntime, snapshot)
    if (!runtimeSeed) {
        return normalizeChartSettings({
            ...seededChartSettings,
            bars: seedBars,
            indicators: [],
        })
    }

    return normalizeChartSettings({
        ...seededChartSettings,
        bars: seedBars,
        symbol: runtimeSeed.symbol,
        timeframe: runtimeSeed.timeframe,
        indicators: [],
    })
}

function buildStreamAvailableIndicators(snapshot, liveTradeRuntime) {
    const runtimeSeed = resolveStreamRuntimeSeed(liveTradeRuntime, snapshot)
    return Array.isArray(runtimeSeed?.indicators) ? runtimeSeed.indicators : []
}

function normalizeStreamCapitalPlan(snapshot, liveTradeRuntime) {
    const stored = snapshot?.capitalPlan && typeof snapshot.capitalPlan === 'object'
        ? snapshot.capitalPlan
        : {}
    const resultUnit = String(
        stored?.resultUnit
        || liveTradeRuntime?.account_currency
        || liveTradeRuntime?.broker_account_currency
        || snapshot?.currency
        || 'USD'
    ).trim().toUpperCase() || 'USD'
    const initialCapital = toFiniteNumberOrNull(stored?.initialCapital)
    const selectedVolume = toFiniteNumberOrNull(stored?.selectedVolume)
    const scaleFactor = toFiniteNumberOrNull(stored?.scaleFactor)
    const referenceCapital = toFiniteNumberOrNull(stored?.referenceCapital)
    const referenceVolume = toFiniteNumberOrNull(stored?.referenceVolume)
    const minimumOperationVolume = toFiniteNumberOrNull(stored?.minimumOperationVolume)

    return {
        initialCapital: initialCapital !== null && initialCapital > 0 ? initialCapital : 100,
        volumeMode: String(stored?.volumeMode || 'relative_capital').trim().toLowerCase() === 'minimum_operation'
            ? 'minimum_operation'
            : 'relative_capital',
        resultUnit,
        selectedVolume: selectedVolume !== null && selectedVolume >= 0 ? selectedVolume : 0.01,
        scaleFactor: scaleFactor !== null && scaleFactor > 0 ? scaleFactor : 1,
        referenceCapital: referenceCapital !== null && referenceCapital > 0 ? referenceCapital : 10000,
        referenceVolume: referenceVolume !== null && referenceVolume > 0 ? referenceVolume : 1,
        minimumOperationVolume: minimumOperationVolume !== null && minimumOperationVolume > 0 ? minimumOperationVolume : 0.01,
    }
}

function scaleStreamHistoryRows(rows = [], scaleFactor = 1) {
    const factor = Number(scaleFactor)
    if (!Number.isFinite(factor) || factor === 1) {
        return Array.isArray(rows) ? rows : []
    }

    return (Array.isArray(rows) ? rows : []).map((entry) => ({
        ...entry,
        fill_volume: toFiniteNumberOrNull(entry?.fill_volume) !== null ? Number(entry.fill_volume) * factor : entry?.fill_volume,
        profit: toFiniteNumberOrNull(entry?.profit) !== null ? Number(entry.profit) * factor : entry?.profit,
        commission: toFiniteNumberOrNull(entry?.commission) !== null ? Number(entry.commission) * factor : entry?.commission,
        swap: toFiniteNumberOrNull(entry?.swap) !== null ? Number(entry.swap) * factor : entry?.swap,
    }))
}

function scaleStreamOperationRows(rows = [], scaleFactor = 1) {
    const factor = Number(scaleFactor)
    if (!Number.isFinite(factor) || factor === 1) {
        return Array.isArray(rows) ? rows : []
    }

    return (Array.isArray(rows) ? rows : []).map((entry) => ({
        ...entry,
        pnl: toFiniteNumberOrNull(entry?.pnl) !== null ? Number(entry.pnl) * factor : entry?.pnl,
    }))
}

function buildStreamUi(snapshot = null) {
    const chartUi = snapshot?.chartUi && typeof snapshot.chartUi === 'object'
        ? snapshot.chartUi
        : {}

    return {
        metaFontSize: Number(chartUi.metaFontSize || 0.94) || 0.94,
        scrollChartToEndOnTickIncoming: chartUi.scrollChartToEndOnTickIncoming !== false,
        showVolumePanel: chartUi.showVolumePanel !== false,
        volumeMode: String(chartUi.volumeMode || 'volume').trim() || 'volume',
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

function doesReplayMarkerMatchChartMarket(marker, chartSettings) {
    const markerSymbol = String(marker?.symbol || '').trim().toUpperCase()
    const markerTimeframe = String(marker?.timeframe || '').trim().toUpperCase()
    const chartSymbol = String(chartSettings?.symbol || '').trim().toUpperCase()
    const chartTimeframe = String(chartSettings?.timeframe || '').trim().toUpperCase()

    if (!markerSymbol || !markerTimeframe || !chartSymbol || !chartTimeframe) {
        return false
    }
    if (markerSymbol !== chartSymbol) {
        return false
    }
    if (markerTimeframe === chartTimeframe) {
        return true
    }

    const markerMinutes = getTimeframeDurationMinutes(markerTimeframe)
    const chartMinutes = getTimeframeDurationMinutes(chartTimeframe)
    if (!Number.isFinite(markerMinutes) || !Number.isFinite(chartMinutes)) {
        return false
    }

    return Boolean(
        chartMinutes <= markerMinutes
        && markerMinutes % chartMinutes === 0
    )
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
        const isOpen = !closingEntry && liveCycleIds.has(cycleId)

        consolidated.push({
            id: `${isOpen ? 'open' : 'closed'}-${cycleId}`,
            cycleId,
            state: isOpen ? 'open' : 'closed',
            strategyLabel: baseEntry?.sleeve_label || baseEntry?.source_strategy_id || '—',
            symbol: baseEntry?.symbol || '—',
            timeframe: baseEntry?.timeframe || '—',
            side: baseEntry?.side || openingEntry?.side || '—',
            entryTime: openingEntry?.filled_at || openingEntry?.created_at || null,
            exitTime: closingEntry?.filled_at || closingEntry?.created_at || null,
            volume: closingEntry?.fill_volume ?? openingEntry?.fill_volume ?? null,
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
                entryTime: openingEntry?.filled_at || openingEntry?.created_at || null,
                exitTime: entry?.filled_at || entry?.created_at || null,
                volume: entry?.fill_volume ?? openingEntry?.fill_volume ?? null,
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
        const hasSyntheticOpen = Boolean(entry?.synthetic_open)
        if (!openBrokerKeys.has(queueKey) && !hasSyntheticOpen) {
            continue
        }
        consolidated.push({
            id: `open-${entry?.id || entry?.command_id || Math.random().toString(36).slice(2, 8)}`,
            state: 'open',
            strategyLabel: entry?.sleeve_label || entry?.source_strategy_id || '—',
            symbol: entry?.symbol || '—',
            timeframe: entry?.timeframe || '—',
            side: entry?.side || '—',
            entryTime: entry?.filled_at || entry?.created_at || null,
            exitTime: null,
            volume: entry?.fill_volume ?? null,
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
    const winningRows = closedRows.filter((entry) => Number(entry.pnl || 0) > 0)
    const realizedPnl = closedRows.reduce((sum, entry) => sum + Number(entry.pnl || 0), 0)

    return {
        rows: consolidated,
        summary: {
            tradeCount: consolidated.length,
            closedCount: closedRows.length,
            openCount: openRows.length,
            winCount: winningRows.length,
            winRate: closedRows.length ? winningRows.length / closedRows.length : 0,
            realizedPnl,
        },
    }
}

function buildTradeRuntimeChartMarkers(runtime, chartSettings) {
    if (!runtime || typeof runtime !== 'object' || !runtime.armed) {
        return []
    }

    const chartSymbol = String(chartSettings?.symbol || '').trim().toUpperCase()
    const chartTimeframe = String(chartSettings?.timeframe || '').trim().toUpperCase()
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

function buildReplayOperationRows(replayMarkers = [], chartSettings) {
    const chartSymbol = String(chartSettings?.symbol || '').trim().toUpperCase()
    const chartTimeframe = String(chartSettings?.timeframe || '').trim().toUpperCase()

    return (Array.isArray(replayMarkers) ? replayMarkers : [])
        .map((marker) => {
            const createdAt = normalizeUnixTimestamp(marker?.time)
            if (!createdAt) {
                return null
            }

            const symbol = String(marker?.symbol || '').trim().toUpperCase()
            const timeframe = String(marker?.timeframe || '').trim().toUpperCase()
            const action = String(marker?.action || '').trim().toLowerCase()
            const side = String(marker?.side || '').trim().toLowerCase()
            const sleeveLabel = String(marker?.sleeveLabel || 'Session').trim() || 'Session'
            const isVisibleMarket = Boolean(
                chartSymbol
                && chartTimeframe
                && doesReplayMarkerMatchChartMarket(marker, chartSettings)
            )

            if (!action) {
                return null
            }

            return {
                id: String(marker?.id || `replay-${createdAt}`).trim() || `replay-${createdAt}`,
                kind: 'replay',
                createdAt,
                symbol,
                timeframe,
                sleeveLabel,
                side,
                action,
                status: 'simulated',
                isVisibleMarket,
                message: String(marker?.text || '').trim(),
                pnl: toFiniteNumberOrNull(marker?.pnl),
                volume: toFiniteNumberOrNull(marker?.volume),
            }
        })
        .filter(Boolean)
}

function buildOperationRows(runtime, chartSettings, sessionStart, replayMarkers = [], limit = 48) {
    const chartSymbol = String(chartSettings?.symbol || '').trim().toUpperCase()
    const chartTimeframe = String(chartSettings?.timeframe || '').trim().toUpperCase()
    const entries = [
        ...(Array.isArray(runtime?.order_commands) ? runtime.order_commands.map((entry) => ({ ...entry, _kind: 'command' })) : []),
        ...(Array.isArray(runtime?.order_intents) ? runtime.order_intents.map((entry) => ({ ...entry, _kind: 'intent' })) : []),
    ]

    const liveEntries = entries
        .map((entry) => {
            const createdAt = normalizeUnixTimestamp(
                entry?.rejected_at
                || entry?.filled_at
                || entry?.claimed_at
                || entry?.acknowledged_at
                || entry?.created_at
                || entry?.record_created_at
            )
            if (!createdAt) {
                return null
            }
            if (sessionStart && createdAt < sessionStart) {
                return null
            }

            const symbol = String(entry?.symbol || '').trim().toUpperCase()
            const timeframe = String(entry?.timeframe || '').trim().toUpperCase()
            const status = String(entry?.status || 'queued').trim().toLowerCase()
            const side = String(entry?.side || '').trim().toLowerCase()
            const action = String(entry?.action || entry?._kind || 'update').trim().toLowerCase()
            const sleeveLabel = String(entry?.sleeve_label || entry?.sleeve_id || 'Robot').trim()
            const isVisibleMarket = Boolean(
                chartSymbol
                && chartTimeframe
                && symbol === chartSymbol
                && timeframe === chartTimeframe
            )

            return {
                id: `${entry?._kind}-${entry?.id || `${symbol}-${timeframe}-${createdAt}`}`,
                kind: entry?._kind,
                createdAt,
                symbol,
                timeframe,
                sleeveLabel,
                side,
                action,
                status,
                isVisibleMarket,
                message: String(entry?.message || entry?.rejection_message || '').trim(),
                volume: toFiniteNumberOrNull(entry?.fill_volume),
            }
        })
        .filter(Boolean)

    return [
        ...buildReplayOperationRows(replayMarkers, chartSettings),
        ...liveEntries,
    ]
        .sort((left, right) => {
            if (left.isVisibleMarket !== right.isVisibleMarket) {
                return left.isVisibleMarket ? -1 : 1
            }
            return right.createdAt - left.createdAt
        })
        .slice(0, Math.max(1, Number(limit) || 48))
}

function mergeStreamMarkers(runtimeMarkers = [], replayMarkers = []) {
    const merged = []
    const seenIds = new Set()

    for (const marker of [...(Array.isArray(replayMarkers) ? replayMarkers : []), ...(Array.isArray(runtimeMarkers) ? runtimeMarkers : [])]) {
        const safeId = String(marker?.id || '').trim()
        if (!safeId || seenIds.has(safeId)) {
            continue
        }
        seenIds.add(safeId)
        merged.push(marker)
    }

    return merged.sort((left, right) => Number(left?.time || 0) - Number(right?.time || 0))
}

function resolveOperationTone(operation) {
    const status = String(operation?.status || '').trim().toLowerCase()
    const pnl = toFiniteNumberOrNull(operation?.pnl)
    if (status === 'simulated' && String(operation?.action || '').trim().toLowerCase() === 'close' && pnl !== null) {
        if (pnl > 0) {
            return 'profit'
        }
        if (pnl < 0) {
            return 'loss'
        }
    }
    if (status === 'filled') {
        return operation?.side === 'long' ? 'profit' : 'loss'
    }
    if (status === 'rejected' || status === 'stale' || status === 'dispatch_blocked') {
        return 'warning'
    }
    return 'neutral'
}

function formatOperationStateLabel(value) {
    const state = String(value || '').trim().toLowerCase()
    if (state === 'open') {
        return 'Open'
    }
    if (state === 'closed') {
        return 'Closed'
    }
    if (!state) {
        return '—'
    }
    return state
        .split('_')
        .map((part) => (part ? `${part[0].toUpperCase()}${part.slice(1)}` : ''))
        .filter(Boolean)
        .join(' ')
}

function resolveHistoryRowTone(row) {
    if (String(row?.tone || '').trim()) {
        return String(row.tone).trim()
    }
    const state = String(row?.state || '').trim().toLowerCase()
    const pnl = toFiniteNumberOrNull(row?.pnl)
    if (state === 'open') {
        return 'neutral'
    }
    if (pnl !== null) {
        if (pnl > 0) {
            return 'profit'
        }
        if (pnl < 0) {
            return 'loss'
        }
    }
    return 'neutral'
}

function buildEquityCurvePoints(historyRows = [], fallbackRows = [], initialCapital = 0) {
    const consolidatedClosedRows = (Array.isArray(historyRows) ? historyRows : [])
        .filter((entry) => String(entry?.state || '').trim().toLowerCase() === 'closed')
        .map((entry) => {
            const time = normalizeUnixTimestamp(entry?.exitTime || entry?.entryTime)
            const pnl = toFiniteNumberOrNull(entry?.pnl)
            if (!time || pnl === null) {
                return null
            }
            return {
                id: String(entry?.id || `closed-${time}`).trim() || `closed-${time}`,
                time,
                pnl,
            }
        })
        .filter(Boolean)
        .sort((left, right) => left.time - right.time)

    const sourceRows = consolidatedClosedRows.length
        ? consolidatedClosedRows
        : (Array.isArray(fallbackRows) ? fallbackRows : [])
            .map((entry) => {
                const action = String(entry?.action || '').trim().toLowerCase()
                const time = normalizeUnixTimestamp(entry?.createdAt)
                const pnl = toFiniteNumberOrNull(entry?.pnl)
                if (action !== 'close' || !time || pnl === null) {
                    return null
                }
                return {
                    id: String(entry?.id || `fallback-${time}`).trim() || `fallback-${time}`,
                    time,
                    pnl,
                }
            })
            .filter(Boolean)
            .sort((left, right) => left.time - right.time)

    let equity = Number(initialCapital) || 0
    return sourceRows.map((entry) => {
        equity += Number(entry.pnl || 0)
        return {
            ...entry,
            equity,
        }
    })
}

function StatCard({ label, value, detail = '', tone = 'neutral', emphasis = false }) {
    return (
        <div className={`streamStatCard tone-${tone} ${emphasis ? 'isEmphasis' : ''}`.trim()}>
            <span>{label}</span>
            <strong>{value}</strong>
            {detail ? <small>{detail}</small> : null}
        </div>
    )
}

function StreamTickerSequence({
    rows = [],
    tickerHistoryLimit = DEFAULT_STREAM_TICKER_HISTORY_LIMIT,
    initialCapital = 0,
    resultUnit = 'USD',
}) {
    const titleText = `ULTIMAS ${tickerHistoryLimit} OPERACOES`

    return (
        <div className='streamTickerSequence'>
            {rows.map((entry, index) => {
                const shouldInsertTitle = index === 0 || index % STREAM_TICKER_TITLE_INTERVAL === 0
                const percent = calculateRelativeReturn(entry?.pnl, initialCapital)
                const tone = Number(entry?.pnl || 0) > 0 ? 'profit' : Number(entry?.pnl || 0) < 0 ? 'loss' : 'neutral'

                return (
                    <span key={`${entry?.id || index}-ticker`} className='streamTickerFragment'>
                        {shouldInsertTitle ? (
                            <>
                                <span className='streamTickerSeparator'>◆</span>
                                <span className='streamTickerItem streamTickerTitle'>{titleText}</span>
                            </>
                        ) : null}
                        <span className='streamTickerSeparator'>◆</span>
                        <span className='streamTickerItem streamTickerOperation'>
                            <span>DATA {formatDateBr(entry?.timestamp)}</span>
                            <span>HORA {formatTimeBr(entry?.timestamp)}</span>
                            <span>VOL {formatVolume(entry?.volume)}</span>
                            <span className={`tone-${tone}`.trim()}>
                                RESULT {formatSignedMoneyWithUnit(entry?.pnl, resultUnit)}
                            </span>
                            <span className={`tone-${tone}`.trim()}>
                                RETURN {percent === null ? '—' : formatSignedPercent(percent)}
                            </span>
                        </span>
                    </span>
                )
            })}
            <span className='streamTickerSeparator'>◆</span>
        </div>
    )
}

export function StreamView({
    authToken = '',
    baseChartSettings = null,
    liveTradeRuntime = null,
}) {
    const [sessionSnapshot] = useState(() => readSessionSnapshot())
    const [streamLaunchKey] = useState(() => readStreamLaunchKeyFromLocation())
    const [isConfigWindow] = useState(() => readStreamPanelModeFromLocation() === STREAM_CONFIG_QUERY_VALUE)
    const [streamSettings, setStreamSettings] = useState(() => readStoredStreamSettings(readStreamLaunchKeyFromLocation()))
    const [streamSettingsDraft, setStreamSettingsDraft] = useState(() => readStoredStreamSettings(readStreamLaunchKeyFromLocation()))
    const [streamSettingsFeedback, setStreamSettingsFeedback] = useState({ tone: 'neutral', message: '' })
    const [showIndicators, setShowIndicators] = useState(false)
    const [chartHistoryState, setChartHistoryState] = useState({
        loadedCandles: 0,
        historyLoadStep: 0,
        firstLoadedTime: null,
        lastLoadedTime: null,
        isReady: false,
        error: '',
    })
    const [historyState, setHistoryState] = useState({
        loading: true,
        error: '',
        rows: [],
    })

    const streamChartSettings = useMemo(
        () => buildStreamChartSettings(baseChartSettings, sessionSnapshot, liveTradeRuntime),
        [baseChartSettings, liveTradeRuntime, sessionSnapshot]
    )
    const streamAvailableIndicators = useMemo(
        () => buildStreamAvailableIndicators(sessionSnapshot, liveTradeRuntime),
        [liveTradeRuntime, sessionSnapshot]
    )
    const availableIndicators = useMemo(
        () => (Array.isArray(streamAvailableIndicators) ? streamAvailableIndicators : []),
        [streamAvailableIndicators]
    )
    const visibleChartSettings = useMemo(
        () => normalizeChartSettings({
            ...streamChartSettings,
            indicators: showIndicators ? availableIndicators : [],
        }),
        [availableIndicators, showIndicators, streamChartSettings]
    )
    const streamChartViewKey = useMemo(
        () => [
            String(visibleChartSettings?.symbol || '').trim().toUpperCase(),
            String(visibleChartSettings?.timeframe || '').trim().toUpperCase(),
            Number(visibleChartSettings?.bars || 0),
            showIndicators ? 'with-indicators' : 'without-indicators',
        ].join('|'),
        [showIndicators, visibleChartSettings]
    )
    const streamUi = useMemo(
        () => buildStreamUi(sessionSnapshot),
        [sessionSnapshot]
    )
    const [streamScrollToEndOnTickIncoming, setStreamScrollToEndOnTickIncoming] = useState(
        () => buildStreamUi(sessionSnapshot).scrollChartToEndOnTickIncoming
    )
    const streamCapitalPlan = useMemo(
        () => normalizeStreamCapitalPlan(sessionSnapshot, liveTradeRuntime),
        [liveTradeRuntime, sessionSnapshot]
    )
    const resultUnit = streamCapitalPlan.resultUnit
    const initialCapital = streamCapitalPlan.initialCapital
    const operatedVolume = streamCapitalPlan.selectedVolume
    const operatedVolumeMode = streamCapitalPlan.volumeMode
    const streamScaleFactor = streamCapitalPlan.scaleFactor
    const tickerHistoryLimit = normalizeStreamTickerHistoryLimit(streamSettings?.tickerHistoryLimit)

    useEffect(() => {
        if (!streamLaunchKey || typeof window === 'undefined') {
            return undefined
        }

        const syncSettings = () => {
            setStreamSettings(readStoredStreamSettings(streamLaunchKey))
        }
        const handleStorage = (event) => {
            if (event && event.key && event.key !== buildStreamSettingsStorageKey(streamLaunchKey)) {
                return
            }
            syncSettings()
        }
        const handleVisibilityChange = () => {
            if (document.visibilityState === 'visible') {
                syncSettings()
            }
        }

        syncSettings()
        window.addEventListener('storage', handleStorage)
        window.addEventListener('focus', syncSettings)
        document.addEventListener('visibilitychange', handleVisibilityChange)
        return () => {
            window.removeEventListener('storage', handleStorage)
            window.removeEventListener('focus', syncSettings)
            document.removeEventListener('visibilitychange', handleVisibilityChange)
        }
    }, [streamLaunchKey])

    useEffect(() => {
        setStreamSettingsDraft({
            tickerHistoryLimit,
        })
    }, [tickerHistoryLimit])

    function openStreamConfigWindow() {
        if (!streamLaunchKey || typeof window === 'undefined') {
            return
        }

        const popup = window.open(
            buildStreamConfigWindowUrl(streamLaunchKey),
            `robotineeko-stream-config-${streamLaunchKey}`,
            'popup=yes,width=420,height=340,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes',
        )
        popup?.focus?.()
    }

    function applyStreamSettings(event) {
        event?.preventDefault?.()

        if (!streamLaunchKey || typeof window === 'undefined') {
            setStreamSettingsFeedback({
                tone: 'warning',
                message: 'This stream window does not have a valid launch key.',
            })
            return
        }

        try {
            const nextSettings = persistStoredStreamSettings(streamLaunchKey, {
                tickerHistoryLimit: streamSettingsDraft?.tickerHistoryLimit,
            })
            setStreamSettings(nextSettings)
            setStreamSettingsDraft(nextSettings)
            setStreamSettingsFeedback({
                tone: 'profit',
                message: `Ticker updated to the latest ${nextSettings.tickerHistoryLimit} operations.`,
            })
        } catch (error) {
            setStreamSettingsFeedback({
                tone: 'warning',
                message: error?.message || 'Could not save the stream settings.',
            })
        }
    }

    function resetStreamSettings() {
        const nextSettings = {
            tickerHistoryLimit: DEFAULT_STREAM_TICKER_HISTORY_LIMIT,
        }
        setStreamSettingsDraft(nextSettings)
        if (streamLaunchKey && typeof window !== 'undefined') {
            try {
                persistStoredStreamSettings(streamLaunchKey, nextSettings)
                setStreamSettings(nextSettings)
                setStreamSettingsFeedback({
                    tone: 'neutral',
                    message: `Ticker reset to the default ${DEFAULT_STREAM_TICKER_HISTORY_LIMIT} operations.`,
                })
                return
            } catch (error) {
                setStreamSettingsFeedback({
                    tone: 'warning',
                    message: error?.message || 'Could not reset the stream settings.',
                })
                return
            }
        }
        setStreamSettingsFeedback({
            tone: 'neutral',
            message: `Ticker reset to the default ${DEFAULT_STREAM_TICKER_HISTORY_LIMIT} operations.`,
        })
    }

    useEffect(() => {
        setShowIndicators(false)
    }, [streamChartSettings?.symbol, streamChartSettings?.timeframe])

    const replaySession = sessionSnapshot?.backtestReplay && typeof sessionSnapshot.backtestReplay === 'object'
        ? sessionSnapshot.backtestReplay
        : null
    const replayMarkers = useMemo(
        () => Array.isArray(replaySession?.tradeMarkers) ? replaySession.tradeMarkers : [],
        [replaySession]
    )
    const replayHistoryRows = useMemo(
        () => Array.isArray(replaySession?.historyRows) ? replaySession.historyRows : [],
        [replaySession]
    )
    const hasReplay = replayMarkers.length > 0 || replayHistoryRows.length > 0
    const replaySessionStart = normalizeUnixTimestamp(replaySession?.sessionStartTime)
    const liveSessionStart = normalizeUnixTimestamp(liveTradeRuntime?.last_armed_at)
    const sessionStart = hasReplay
        ? (replaySessionStart || liveSessionStart)
        : liveSessionStart
    const sleeveStates = useMemo(
        () => (
            liveTradeRuntime?.sleeve_states && typeof liveTradeRuntime.sleeve_states === 'object'
                ? Object.values(liveTradeRuntime.sleeve_states)
                : []
        ),
        [liveTradeRuntime?.sleeve_states]
    )

    useEffect(() => {
        if (isConfigWindow) {
            setHistoryState({
                loading: false,
                error: '',
                rows: [],
            })
            return undefined
        }

        let cancelled = false
        let timer = null

        async function syncHistory() {
            const nowSeconds = Math.floor(Date.now() / 1000)
            const elapsedDays = liveSessionStart
                ? Math.max(1, Math.ceil((nowSeconds - liveSessionStart) / 86400) + 1)
                : 7
            const query = new URLSearchParams({
                range_key: liveSessionStart ? 'custom' : '7d',
                custom_days: String(elapsedDays),
                status_filter: 'all',
                limit: String(STREAM_HISTORY_LIMIT),
            })

            try {
                const response = await fetch(buildApiUrl(`/workspace/live-trades?${query.toString()}`), {
                    headers: authToken
                        ? { Authorization: `Bearer ${authToken}` }
                        : {},
                })
                const data = await readJsonResponse(response)
                if (!response.ok) {
                    throw new Error(`${response.status} ${extractApiErrorMessage(data, 'Failed to load stream session history.')}`)
                }
                if (cancelled) {
                    return
                }

                const rows = Array.isArray(data?.trades) ? data.trades : []
                setHistoryState({
                    loading: false,
                    error: '',
                    rows,
                })
            } catch (error) {
                if (cancelled) {
                    return
                }
                setHistoryState((current) => ({
                    ...current,
                    loading: false,
                    error: error.message || 'Failed to load stream session history.',
                }))
            } finally {
                if (!cancelled) {
                    const nextDelay = liveTradeRuntime?.armed ? 3000 : 10000
                    timer = window.setTimeout(() => {
                        void syncHistory()
                    }, nextDelay)
                }
            }
        }

        void syncHistory()

        return () => {
            cancelled = true
            if (timer) {
                window.clearTimeout(timer)
            }
        }
    }, [authToken, isConfigWindow, liveTradeRuntime?.armed, liveSessionStart])

    const liveSessionRows = useMemo(() => {
        if (!liveSessionStart) {
            return Array.isArray(historyState.rows) ? historyState.rows : []
        }
        return (Array.isArray(historyState.rows) ? historyState.rows : []).filter((entry) => {
            const entryTime = normalizeUnixTimestamp(
                entry?.filled_at
                || entry?.rejected_at
                || entry?.created_at
                || entry?.record_created_at
            )
            return !entryTime || entryTime >= liveSessionStart
        })
    }, [historyState.rows, liveSessionStart])

    const sessionRows = useMemo(
        () => [...replayHistoryRows, ...liveSessionRows],
        [liveSessionRows, replayHistoryRows]
    )
    const scaledSessionRows = useMemo(
        () => scaleStreamHistoryRows(sessionRows, streamScaleFactor),
        [sessionRows, streamScaleFactor]
    )

    const sessionHistory = useMemo(
        () => buildTradeHistoryView(scaledSessionRows, sleeveStates),
        [scaledSessionRows, sleeveStates]
    )
    const visibleMarketHistory = useMemo(() => buildTradeHistoryView(
        scaledSessionRows.filter((entry) => (
            String(entry?.symbol || '').trim().toUpperCase() === String(streamChartSettings?.symbol || '').trim().toUpperCase()
            && String(entry?.timeframe || '').trim().toUpperCase() === String(streamChartSettings?.timeframe || '').trim().toUpperCase()
        )),
        sleeveStates.filter((entry) => (
            String(entry?.symbol || '').trim().toUpperCase() === String(streamChartSettings?.symbol || '').trim().toUpperCase()
            && String(entry?.timeframe || '').trim().toUpperCase() === String(streamChartSettings?.timeframe || '').trim().toUpperCase()
        )),
    ), [scaledSessionRows, sleeveStates, streamChartSettings?.symbol, streamChartSettings?.timeframe])
    const replayChartMarkers = useMemo(
        () => replayMarkers.filter((marker) => doesReplayMarkerMatchChartMarket(marker, streamChartSettings)),
        [replayMarkers, streamChartSettings]
    )
    const streamMarkers = useMemo(
        () => mergeStreamMarkers(
            buildTradeRuntimeChartMarkers(liveTradeRuntime, streamChartSettings),
            replayChartMarkers,
        ),
        [liveTradeRuntime, replayChartMarkers, streamChartSettings]
    )
    const sessionWindowEnd = useMemo(() => {
        const timestamps = [
            liveTradeRuntime?.last_event_at,
            liveTradeRuntime?.last_armed_at,
            ...replayMarkers.map((marker) => marker?.time),
            ...sessionRows.map((entry) => (
                entry?.filled_at
                || entry?.rejected_at
                || entry?.created_at
                || entry?.record_created_at
            )),
        ]
            .map((value) => normalizeUnixTimestamp(value))
            .filter(Boolean)

        if (!timestamps.length) {
            return null
        }

        return Math.max(...timestamps)
    }, [liveTradeRuntime?.last_armed_at, liveTradeRuntime?.last_event_at, replayMarkers, sessionRows])

    const sessionRunLabel = 'Session window'
    const sessionRunValue = hasReplay
        ? formatDurationSpan(sessionStart, sessionWindowEnd)
        : formatSince(sessionStart)
    const monthlyResult = calculateMonthlyResult(
        sessionHistory.summary.realizedPnl,
        sessionStart,
        sessionWindowEnd,
    )
    const currentCapital = initialCapital + Number(sessionHistory.summary.realizedPnl || 0)
    const monthlyResultTone = monthlyResult === null
        ? 'neutral'
        : monthlyResult >= 0
            ? 'profit'
            : 'warning'
    const chartError = String(chartHistoryState?.error || '').trim()
    const isChartPreparing = !chartHistoryState?.isReady && !chartError
    const chartLoadingDetail = 'Loading chart candles for the stream window.'
    const availableIndicatorCount = availableIndicators.length
    const indicatorToggleDetail = availableIndicatorCount
        ? (
            showIndicators
                ? `${availableIndicatorCount} indicator${availableIndicatorCount === 1 ? '' : 's'} visible on chart`
                : `${availableIndicatorCount} indicator${availableIndicatorCount === 1 ? '' : 's'} available`
        )
        : 'No indicators are available for this stream setup.'
    const fallbackOperationRows = useMemo(
        () => scaleStreamOperationRows(
            buildOperationRows(
                liveTradeRuntime,
                streamChartSettings,
                sessionStart,
                replayMarkers,
                48,
            ),
            streamScaleFactor,
        ),
        [liveTradeRuntime, replayMarkers, sessionStart, streamChartSettings, streamScaleFactor]
    )
    const operationHistoryRows = useMemo(
        () => {
            const consolidatedRows = Array.isArray(sessionHistory?.rows) ? sessionHistory.rows.slice(0, 48) : []
            if (consolidatedRows.length) {
                return consolidatedRows.map((entry) => ({
                    id: entry.id,
                    entryTime: entry.entryTime,
                    marketLabel: `${entry.symbol || '—'} · ${entry.timeframe || '—'}`,
                    typeLabel: String(entry.side || '—').trim() || '—',
                    strategyLabel: entry.strategyLabel || '—',
                    exitTime: entry.exitTime,
                    stateLabel: formatOperationStateLabel(entry.state),
                    pnl: entry.pnl,
                    tone: resolveHistoryRowTone(entry),
                }))
            }

            return fallbackOperationRows.map((entry) => ({
                id: entry.id,
                entryTime: entry.createdAt,
                marketLabel: `${entry.symbol || '—'} · ${entry.timeframe || '—'}`,
                typeLabel: String(entry.side || entry.action || '—').trim() || '—',
                strategyLabel: entry.sleeveLabel || '—',
                exitTime: String(entry.action || '').trim().toLowerCase() === 'close' ? entry.createdAt : null,
                stateLabel: formatOperationStateLabel(
                    entry.status === 'simulated'
                        ? entry.action
                        : entry.status
                ),
                pnl: entry.pnl,
                tone: resolveOperationTone(entry),
            }))
        },
        [fallbackOperationRows, sessionHistory]
    )
    const tickerRows = useMemo(() => {
        const consolidatedClosedRows = (Array.isArray(sessionHistory?.rows) ? sessionHistory.rows : [])
            .filter((entry) => String(entry?.state || '').trim().toLowerCase() === 'closed')
            .map((entry) => ({
                id: String(entry?.id || '').trim() || `ticker-${entry?.exitTime || entry?.entryTime || Math.random().toString(36).slice(2, 8)}`,
                timestamp: entry?.exitTime || entry?.entryTime || null,
                volume: entry?.volume ?? null,
                pnl: entry?.pnl,
            }))
            .filter((entry) => entry.timestamp && toFiniteNumberOrNull(entry?.pnl) !== null)
            .sort((left, right) => Number(right.timestamp || 0) - Number(left.timestamp || 0))

        if (consolidatedClosedRows.length) {
            return consolidatedClosedRows
        }

        return fallbackOperationRows
            .filter((entry) => String(entry?.action || '').trim().toLowerCase() === 'close')
            .map((entry) => ({
                id: String(entry?.id || '').trim() || `ticker-fallback-${entry?.createdAt || Math.random().toString(36).slice(2, 8)}`,
                timestamp: entry?.createdAt || null,
                volume: entry?.volume ?? null,
                pnl: entry?.pnl,
            }))
            .filter((entry) => entry.timestamp && toFiniteNumberOrNull(entry?.pnl) !== null)
            .sort((left, right) => Number(right.timestamp || 0) - Number(left.timestamp || 0))
    }, [fallbackOperationRows, sessionHistory?.rows])
    const visibleTickerRows = useMemo(
        () => tickerRows.slice(0, tickerHistoryLimit),
        [tickerHistoryLimit, tickerRows]
    )
    const resultCurvePoints = useMemo(
        () => buildEquityCurvePoints(sessionHistory?.rows, fallbackOperationRows, initialCapital),
        [fallbackOperationRows, initialCapital, sessionHistory?.rows]
    )
    const resultCurveGeometry = useMemo(() => {
        if (!resultCurvePoints.length) {
            return null
        }

        const values = resultCurvePoints.map((entry) => Number(entry.equity || 0))
        const minValue = Math.min(...values, initialCapital)
        const maxValue = Math.max(...values, initialCapital)
        const midValue = initialCapital
        const span = Math.max(1, maxValue - minValue)
        const baselineY = ((maxValue - initialCapital) / span) * 100
        const pointCount = resultCurvePoints.length
        const polyline = resultCurvePoints
            .map((entry, index) => {
                const x = pointCount === 1 ? 50 : (index / (pointCount - 1)) * 100
                const y = ((maxValue - Number(entry.equity || 0)) / span) * 100
                return `${x},${y}`
            })
            .join(' ')

        return {
            pointCount,
            polyline,
            baselineY,
            startTime: resultCurvePoints[0]?.time || null,
            endTime: resultCurvePoints[resultCurvePoints.length - 1]?.time || null,
            minValue,
            maxValue,
            midValue,
        }
    }, [initialCapital, resultCurvePoints])
    const streamErrorMessages = useMemo(() => {
        const messages = []
        const runtimeError = String(liveTradeRuntime?.last_error || '').trim()
        const historyError = String(historyState.error || '').trim()

        if (chartError) {
            messages.push({
                id: 'chart',
                label: 'Chart',
                message: chartError,
            })
        }

        if (runtimeError) {
            messages.push({
                id: 'runtime',
                label: 'Runtime',
                message: runtimeError,
            })
        }

        if (historyError) {
            messages.push({
                id: 'history',
                label: 'History',
                message: historyError,
            })
        }

        return messages
    }, [chartError, historyState.error, liveTradeRuntime?.last_error])
    const streamLeadingControls = useMemo(
        () => (
            <>
                <button
                    type='button'
                    className={`streamIndicatorToggleButton ${showIndicators ? 'isActive' : ''}`.trim()}
                    onClick={() => setShowIndicators((current) => !current)}
                    disabled={!availableIndicatorCount}
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
                    className={`streamIndicatorToggleButton ${streamScrollToEndOnTickIncoming ? 'isActive' : ''}`.trim()}
                    onClick={() => setStreamScrollToEndOnTickIncoming((current) => !current)}
                    aria-pressed={streamScrollToEndOnTickIncoming}
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
            </>
        ),
        [availableIndicatorCount, showIndicators, streamScrollToEndOnTickIncoming]
    )

    if (isConfigWindow) {
        return (
            <div id='StreamApp' className='streamModeApp'>
                <section className='streamConfigScreen'>
                    <form className='streamConfigCard' onSubmit={applyStreamSettings}>
                        <div className='streamConfigHeader'>
                            <span className='streamConfigEyebrow'>Stream config</span>
                            <strong>Letreiro de operacoes</strong>
                            <small>
                                Ajuste em janela separada quantas ultimas operacoes fechadas o letreiro principal deve mostrar.
                            </small>
                        </div>

                        <div className='streamConfigContext'>
                            <span>{String(streamChartSettings?.symbol || '—').trim().toUpperCase()} · {String(streamChartSettings?.timeframe || '—').trim().toUpperCase()}</span>
                            <span>Padrao: {DEFAULT_STREAM_TICKER_HISTORY_LIMIT}</span>
                        </div>

                        {!streamLaunchKey ? (
                            <div className='streamRuntimeWarning'>
                                <strong>Launch key missing</strong>
                                <span>This config window is not attached to a valid stream session.</span>
                            </div>
                        ) : null}

                        <label className='streamConfigField'>
                            <span>Ultimas N operacoes no letreiro</span>
                            <input
                                type='number'
                                min={MIN_STREAM_TICKER_HISTORY_LIMIT}
                                max={MAX_STREAM_TICKER_HISTORY_LIMIT}
                                step='1'
                                value={streamSettingsDraft?.tickerHistoryLimit ?? DEFAULT_STREAM_TICKER_HISTORY_LIMIT}
                                onChange={(event) => {
                                    setStreamSettingsDraft({
                                        tickerHistoryLimit: event.target.value,
                                    })
                                    setStreamSettingsFeedback({ tone: 'neutral', message: '' })
                                }}
                            />
                            <small>
                                O letreiro principal passa a usar este limite sem precisar reiniciar a stream.
                            </small>
                        </label>

                        {streamSettingsFeedback.message ? (
                            <div className={`streamConfigStatus tone-${streamSettingsFeedback.tone}`.trim()}>
                                {streamSettingsFeedback.message}
                            </div>
                        ) : null}

                        <div className='streamConfigActions'>
                            <button type='button' className='streamConfigButtonSecondary' onClick={resetStreamSettings}>
                                Reset
                            </button>
                            <button type='submit' className='streamConfigButtonPrimary' disabled={!streamLaunchKey}>
                                Apply
                            </button>
                            <button type='button' className='streamConfigButtonGhost' onClick={() => window.close()}>
                                Close
                            </button>
                        </div>
                    </form>
                </section>
            </div>
        )
    }

    return (
        <div id='StreamApp' className='streamModeApp'>
            <section className='streamScreen'>
                {streamErrorMessages.length ? (
                    <section className='streamErrorBanner' role='alert' aria-live='assertive'>
                        <strong>Stream errors</strong>
                        <div className='streamErrorBannerMessages'>
                            {streamErrorMessages.map((entry) => (
                                <span key={entry.id}>
                                    {entry.label}: {entry.message}
                                </span>
                            ))}
                        </div>
                    </section>
                ) : null}

                <section className='streamBody'>
                    <div className='streamChartShell'>
                        <Chart
                            key={streamChartViewKey}
                            id='StreamChart'
                            authToken={authToken}
                            chartSettings={visibleChartSettings}
                            runId={0}
                            displayMode='stream'
                            metaFontSize={streamUi.metaFontSize}
                            scrollChartToEndOnTickIncoming={streamScrollToEndOnTickIncoming}
                            showVolumePanel={streamUi.showVolumePanel}
                            volumeMode={streamUi.volumeMode}
                            tradeMarkers={streamMarkers}
                            tradeMarkerMode='trader'
                            streamLeadingControls={streamLeadingControls}
                            guestNoticeVisible={false}
                            onHistoryStateChange={setChartHistoryState}
                        />
                    </div>

                    <aside className='streamSidebar'>
                        <section className='streamPanel streamPanelHero'>
                            <div className={`streamResultHero ${sessionHistory.summary.realizedPnl >= 0 ? 'isProfit' : 'isLoss'}`.trim()}>
                                <div className='streamResultHeroValueRow'>
                                    <strong>{formatSignedMoney(sessionHistory.summary.realizedPnl)}</strong>
                                    <span className='streamResultHeroUnit'>{resultUnit}</span>
                                </div>
                                <small className='streamResultHeroDetail'>
                                    {formatSignedPercent(calculateRelativeReturn(sessionHistory.summary.realizedPnl, initialCapital) || 0)}
                                </small>
                            </div>
                        </section>

                        {isChartPreparing ? (
                            <section className='streamPanel streamPanelCompact'>
                                <div className='streamReplayLoadingState'>
                                    <strong>Chart loading</strong>
                                    <span>{chartLoadingDetail}</span>
                                </div>
                            </section>
                        ) : null}

                        <section className='streamPanel'>
                            <div className='streamPanelHeader'>
                                <strong>Live pulse</strong>
                                <button
                                    type='button'
                                    className='streamConfigOpenButton'
                                    onClick={openStreamConfigWindow}
                                    title='Open stream configuration'
                                    aria-label='Open stream configuration'
                                >
                                    <svg viewBox='0 0 24 24' aria-hidden='true'>
                                        <path d='M12 3.5 13.9 6l3-.1.7 2.5 2.4 1.5-1.3 2.7 1.3 2.7-2.4 1.5-.7 2.5-3-.1L12 20.5l-1.9-2.1-3 .1-.7-2.5L4 14.5l1.3-2.7L4 9.1l2.4-1.5.7-2.5 3 .1L12 3.5Z' />
                                        <circle cx='12' cy='12' r='3.1' />
                                    </svg>
                                </button>
                            </div>

                            <div className='streamStatsGrid'>
                                <StatCard label='Initial capital' value={formatMoneyWithUnit(initialCapital, resultUnit)} detail='Stream baseline' />
                                <StatCard label='Current capital' value={formatCapitalWithPercent(currentCapital, initialCapital, resultUnit)} tone={currentCapital >= initialCapital ? 'profit' : 'warning'} />
                                <StatCard label='Operated volume' value={`${operatedVolume.toFixed(2)} lot`} detail={operatedVolumeMode === 'minimum_operation' ? 'Minimum live volume' : 'Relative to initial capital'} />
                                <StatCard label={sessionRunLabel} value={sessionRunValue} detail={sessionStart ? formatDateTime(sessionStart) : hasReplay ? 'The session has no valid start time yet.' : 'The runtime has not armed yet'} emphasis />
                                <StatCard label='Monthly result' value={monthlyResult === null ? '—' : formatMoneyWithPercent(monthlyResult, initialCapital, resultUnit)} detail={monthlyResult === null ? 'Need a measurable session window' : '30d normalized'} tone={monthlyResultTone} />
                                <StatCard label='On-chart PnL' value={formatMoneyWithPercent(visibleMarketHistory.summary.realizedPnl, initialCapital, resultUnit)} detail={availableIndicatorCount ? indicatorToggleDetail : `${visibleMarketHistory.summary.closedCount} closed in view`} tone={visibleMarketHistory.summary.realizedPnl >= 0 ? 'profit' : 'warning'} />
                                <StatCard label='Win rate' value={formatPercent(sessionHistory.summary.winRate)} detail={`${sessionHistory.summary.closedCount} closed · ${sessionHistory.summary.openCount} open`} tone={sessionHistory.summary.realizedPnl >= 0 ? 'profit' : 'warning'} />
                            </div>
                        </section>
                    </aside>
                </section>

                <section className='streamTickerStrip' aria-label={`Ultimas ${tickerHistoryLimit} operacoes fechadas`}>
                    {visibleTickerRows.length ? (
                        <div className='streamTickerViewport'>
                            <div className='streamTickerTrack'>
                                <StreamTickerSequence
                                    rows={visibleTickerRows}
                                    tickerHistoryLimit={tickerHistoryLimit}
                                    initialCapital={initialCapital}
                                    resultUnit={resultUnit}
                                />
                                <StreamTickerSequence
                                    rows={visibleTickerRows}
                                    tickerHistoryLimit={tickerHistoryLimit}
                                    initialCapital={initialCapital}
                                    resultUnit={resultUnit}
                                />
                            </div>
                        </div>
                    ) : (
                        <div className='streamTickerEmpty'>
                            LETREIRO AGUARDANDO AS ULTIMAS {tickerHistoryLimit} OPERACOES FECHADAS
                        </div>
                    )}
                </section>

                <section className='streamHistoryDock'>
                    <div className='streamHistoryDockGrid'>
                        <section className='streamDockPanel'>
                            <div className='streamDockPanelHeader'>
                                <strong>Equity curve</strong>
                                <span>{sessionHistory.summary.closedCount} closed · {resultUnit}</span>
                            </div>

                            {resultCurveGeometry ? (
                                <div className='streamResultCurveShell'>
                                    <div className='streamResultCurvePlot'>
                                        <div className='streamResultCurveYAxis'>
                                            <span className='streamResultCurveYAxisTitle'>Equity ({resultUnit})</span>
                                            <div className='streamResultCurveYAxisLabels'>
                                                <span className='streamResultCurveYAxisLabel isTop'>
                                                    {formatMoneyWithUnit(resultCurveGeometry.maxValue, resultUnit)}
                                                </span>
                                                <span className='streamResultCurveYAxisLabel isMiddle'>
                                                    {formatMoneyWithUnit(resultCurveGeometry.midValue, resultUnit)}
                                                </span>
                                                <span className='streamResultCurveYAxisLabel isBottom'>
                                                    {formatMoneyWithUnit(resultCurveGeometry.minValue, resultUnit)}
                                                </span>
                                            </div>
                                        </div>

                                        <div className={`streamResultCurveCanvas ${sessionHistory.summary.realizedPnl >= 0 ? 'isProfit' : 'isLoss'}`.trim()}>
                                            <svg viewBox='0 0 100 100' preserveAspectRatio='none' className='streamResultCurveSvg' aria-hidden='true'>
                                                <g className='streamResultCurveGrid'>
                                                    {[20, 40, 60, 80].map((y) => (
                                                        <line
                                                            key={`grid-h-${y}`}
                                                            x1='0'
                                                            y1={y}
                                                            x2='100'
                                                            y2={y}
                                                        />
                                                    ))}
                                                    {[20, 40, 60, 80].map((x) => (
                                                        <line
                                                            key={`grid-v-${x}`}
                                                            x1={x}
                                                            y1='0'
                                                            x2={x}
                                                            y2='100'
                                                        />
                                                    ))}
                                                </g>
                                                <line
                                                    className='streamResultCurveBaseline'
                                                    x1='0'
                                                    y1={resultCurveGeometry.baselineY}
                                                    x2='100'
                                                    y2={resultCurveGeometry.baselineY}
                                                />
                                                <polyline
                                                    className='streamResultCurveLine'
                                                    points={resultCurveGeometry.polyline}
                                                />
                                            </svg>
                                        </div>
                                    </div>

                                    <div className='streamResultCurveMeta'>
                                        <span>{formatDateTimeBr24(resultCurveGeometry.startTime)}</span>
                                        <span>{formatDateTimeBr24(resultCurveGeometry.endTime)}</span>
                                    </div>

                                    <div className='streamResultCurveSummary'>
                                        <span>Low {formatCapitalWithPercent(resultCurveGeometry.minValue, initialCapital, resultUnit)}</span>
                                        <span>High {formatCapitalWithPercent(resultCurveGeometry.maxValue, initialCapital, resultUnit)}</span>
                                    </div>
                                </div>
                            ) : (
                                <div className='streamEmptyState'>
                                    No closed operation is available to draw the session curve yet.
                                </div>
                            )}
                        </section>

                        <section className='streamDockPanel'>
                            <div className='streamDockPanelHeader'>
                                <strong>Operation history</strong>
                                <span>{operationHistoryRows.length} rows</span>
                            </div>

                            {operationHistoryRows.length ? (
                                <div className='streamHistoryTableShell'>
                                    <table className='streamHistoryTable'>
                                        <thead>
                                            <tr>
                                                <th>Open time</th>
                                                <th>Market</th>
                                                <th>Type</th>
                                                <th>Strategy</th>
                                                <th>Close time</th>
                                                <th>State</th>
                                                <th className='isNumeric'>P/L</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {operationHistoryRows.map((entry) => {
                                                const rowTone = resolveHistoryRowTone(entry)
                                                return (
                                                    <tr key={entry.id} className={`streamHistoryRow tone-${rowTone}`.trim()}>
                                                        <td>{formatDateTimeBr24(entry.entryTime)}</td>
                                                        <td>{entry.marketLabel || '—'}</td>
                                                        <td className='isCaps'>{String(entry.typeLabel || '—').trim() || '—'}</td>
                                                        <td title={String(entry.strategyLabel || '').trim()}>{entry.strategyLabel || '—'}</td>
                                                        <td>{formatDateTimeBr24(entry.exitTime)}</td>
                                                        <td>{entry.stateLabel || '—'}</td>
                                                        <td className={`isNumeric isPnl tone-${rowTone}`.trim()}>
                                                            {entry.pnl === null ? '—' : formatMoneyWithPercent(entry.pnl, initialCapital, resultUnit)}
                                                        </td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            ) : (
                                <div className='streamEmptyState'>
                                    {hasReplay
                                        ? 'No operation is visible in this session yet.'
                                        : 'No live operation has been recorded for this session yet.'}
                                </div>
                            )}
                        </section>
                    </div>

                    {historyState.loading ? (
                        <div className='streamLoadingState streamHistoryDockLoading'>Refreshing live trade history…</div>
                    ) : null}
                </section>
            </section>
        </div>
    )
}

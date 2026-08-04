import { useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, buildWebSocketUrl, readJsonResponse } from '/src/api'
import {
    buildBackendIndicatorsPayload,
    getIndicatorSeriesOptions,
    mergeAppliedIndicatorsWithVisualSettings,
    normalizeIndicators,
} from '../utils/chartSettings.jsx'
import { getStrategyTokenNameForIndicatorLine } from '../utils/strategyAliases.jsx'
import './Chart.css'

const REALTIME_RIGHT_PADDING_BARS = 20
const UNIX_TIME_MILLISECONDS_THRESHOLD = 100_000_000_000
const CHART_MARKER_POSITIONS = new Set(['aboveBar', 'belowBar', 'inBar'])
const CHART_MARKER_SHAPES = new Set(['arrowUp', 'arrowDown', 'circle', 'square'])
const CHART_SOCKET_STALE_AFTER_MS = 4_000
const CHART_SOCKET_RECONCILIATION_POLL_INTERVAL_MS = 3_000
const CHART_FALLBACK_POLL_INTERVAL_MS = 1_200

function toFiniteNumberOrNull(value) {
    const numeric = Number(value)
    return Number.isFinite(numeric) ? numeric : null
}

function normalizeChartTime(value) {
    const numeric = toFiniteNumberOrNull(value)

    if (numeric === null) {
        return null
    }

    const normalized = Math.abs(numeric) >= UNIX_TIME_MILLISECONDS_THRESHOLD
        ? (numeric / 1000)
        : numeric
    const rounded = Math.trunc(normalized)

    if (!Number.isFinite(rounded) || rounded <= 0) {
        return null
    }

    return rounded
}

function sanitizeCandleRow(candle) {
    const time = normalizeChartTime(candle?.time)
    const open = toFiniteNumberOrNull(candle?.open)
    const high = toFiniteNumberOrNull(candle?.high)
    const low = toFiniteNumberOrNull(candle?.low)
    const close = toFiniteNumberOrNull(candle?.close)

    if (time === null || open === null || high === null || low === null || close === null) {
        return null
    }

    return {
        time,
        open,
        high: Math.max(high, open, close, low),
        low: Math.min(low, open, close, high),
        close,
        volume: toFiniteNumberOrNull(candle?.volume) ?? 0,
        tick_volume: toFiniteNumberOrNull(candle?.tick_volume) ?? (toFiniteNumberOrNull(candle?.volume) ?? 0),
        real_volume: toFiniteNumberOrNull(candle?.real_volume) ?? 0,
    }
}

function buildSanitizedCandleRows(candles = []) {
    const byTime = new Map()
    let dropped = 0

    for (const candle of Array.isArray(candles) ? candles : []) {
        const sanitized = sanitizeCandleRow(candle)

        if (!sanitized) {
            dropped += 1
            continue
        }

        byTime.set(sanitized.time, sanitized)
    }

    return {
        rows: Array.from(byTime.values()).sort((left, right) => left.time - right.time),
        dropped,
    }
}

function buildIndicatorSeriesPoints(indicatorRows = [], rowColumnName = '') {
    const byTime = new Map()
    let dropped = 0

    for (const row of Array.isArray(indicatorRows) ? indicatorRows : []) {
        const time = normalizeChartTime(row?.time)
        const value = toFiniteNumberOrNull(row?.[rowColumnName])

        if (time === null || value === null) {
            if (row?.[rowColumnName] !== null && row?.[rowColumnName] !== undefined) {
                dropped += 1
            }
            continue
        }

        byTime.set(time, { time, value })
    }

    return {
        points: Array.from(byTime.values()).sort((left, right) => left.time - right.time),
        dropped,
    }
}

function normalizeLogicalNumber(value) {
    const numeric = Number(value)
    return Number.isFinite(numeric) ? numeric : null
}

function normalizeLogicalRange(range) {
    const from = normalizeLogicalNumber(range?.from)
    const to = normalizeLogicalNumber(range?.to)

    if (from === null || to === null) {
        return null
    }

    if (to < from) {
        return {
            from: to,
            to: from,
        }
    }

    return {
        from,
        to,
    }
}

function safeGetVisibleLogicalRange(timeScaleApi, onError = null) {
    if (!timeScaleApi || typeof timeScaleApi.getVisibleLogicalRange !== 'function') {
        return null
    }

    try {
        return normalizeLogicalRange(timeScaleApi.getVisibleLogicalRange())
    } catch (error) {
        onError?.(error)
        return null
    }
}

function safeSetVisibleLogicalRange(timeScaleApi, range, onError = null) {
    if (!timeScaleApi || typeof timeScaleApi.setVisibleLogicalRange !== 'function') {
        return false
    }

    const safeRange = normalizeLogicalRange(range)
    if (!safeRange) {
        return false
    }

    try {
        timeScaleApi.setVisibleLogicalRange(safeRange)
        return true
    } catch (error) {
        onError?.(error)
        return false
    }
}

function scrollChartToLatestWithPadding(chartApi, loadedCount) {
    if (!chartApi) {
        return
    }

    const timeScaleApi = chartApi.timeScale?.()
    if (!timeScaleApi) {
        return
    }

    const safeLoadedCount = Math.max(0, Number(loadedCount) || 0)
    if (!safeLoadedCount) {
        return
    }

    const visibleRange = safeGetVisibleLogicalRange(timeScaleApi)
    const span = visibleRange
        ? Math.max(20, Math.ceil(visibleRange.to - visibleRange.from))
        : 60
    const lastLogicalIndex = Math.max(0, safeLoadedCount - 1)
    const rightEdge = lastLogicalIndex + REALTIME_RIGHT_PADDING_BARS

    chartApi.applyOptions?.({
        timeScale: {
            rightOffset: REALTIME_RIGHT_PADDING_BARS,
        },
    })
    safeSetVisibleLogicalRange(timeScaleApi, {
        from: Math.max(0, rightEdge - span),
        to: rightEdge,
    })
}

function focusChartOnMarkerRange(chartApi, markerCandidates = [], loadedTimes = new Set()) {
    if (!chartApi) {
        return false
    }

    const timeScaleApi = chartApi.timeScale?.()
    if (!timeScaleApi) {
        return false
    }

    const sortedLoadedTimes = Array.from(
        loadedTimes instanceof Set
            ? loadedTimes
            : Array.isArray(loadedTimes)
                ? loadedTimes
                : []
    )
        .map((value) => normalizeChartTime(value))
        .filter((value) => value !== null)
        .sort((left, right) => left - right)

    if (!sortedLoadedTimes.length) {
        return false
    }

    const logicalIndexByTime = new Map()
    sortedLoadedTimes.forEach((time, index) => {
        logicalIndexByTime.set(time, index)
    })

    const markerIndexes = []
    for (const marker of Array.isArray(markerCandidates) ? markerCandidates : []) {
        const normalized = normalizeTradeMarker(marker)
        if (!normalized) {
            continue
        }
        const logicalIndex = logicalIndexByTime.get(normalized.time)
        if (logicalIndex === undefined) {
            continue
        }
        markerIndexes.push(logicalIndex)
    }

    if (!markerIndexes.length) {
        return false
    }

    markerIndexes.sort((left, right) => left - right)
    const firstMarkerIndex = markerIndexes[0]
    const lastMarkerIndex = markerIndexes[markerIndexes.length - 1]
    const currentVisibleRange = safeGetVisibleLogicalRange(timeScaleApi)
    if (
        currentVisibleRange
        && currentVisibleRange.from <= lastMarkerIndex
        && currentVisibleRange.to >= firstMarkerIndex
    ) {
        return false
    }

    const desiredSpan = currentVisibleRange
        ? Math.max(40, Math.ceil(currentVisibleRange.to - currentVisibleRange.from))
        : 80
    const rightPadding = Math.min(12, Math.max(4, Math.floor(desiredSpan * 0.15)))
    const nextTo = Math.min(
        sortedLoadedTimes.length - 1 + REALTIME_RIGHT_PADDING_BARS,
        lastMarkerIndex + rightPadding,
    )
    const nextFrom = Math.max(0, nextTo - desiredSpan)
    return safeSetVisibleLogicalRange(timeScaleApi, {
        from: nextFrom,
        to: Math.max(nextFrom + 20, nextTo),
    })
}

function normalizeLineTarget(value) {
    const normalized = String(value || '').trim().toLowerCase()

    if (normalized === 'price' || normalized === 'separate' || normalized === 'hidden') {
        return normalized
    }

    return 'price'
}

function getLinePaneKey(indicator, line) {
    const target = normalizeLineTarget(line?.target)

    if (target === 'price') {
        return '__price__'
    }

    if (target === 'separate') {
        const explicitPaneId = String(line?.paneId || '').trim()
        if (explicitPaneId.toLowerCase() === 'volume') {
            return '__volume__'
        }

        return String(
            explicitPaneId
            || indicator?.alias
            || indicator?.name
            || line?.columnName
            || '__separate__'
        ).trim()
    }

    return '__hidden__'
}

function buildPaneIndexMap(indicators = []) {
    const paneIndexMap = new Map()
    paneIndexMap.set('__price__', 0)
    paneIndexMap.set('__volume__', 1)

    let nextPaneIndex = 2

    for (const indicator of indicators) {
        for (const line of indicator.lines || []) {
            const target = normalizeLineTarget(line?.target)

            if (target !== 'separate') {
                continue
            }

            const paneKey = getLinePaneKey(indicator, line)

            if (!paneIndexMap.has(paneKey)) {
                paneIndexMap.set(paneKey, nextPaneIndex)
                nextPaneIndex += 1
            }
        }
    }

    return paneIndexMap
}

function getPaneIndexForLine(indicator, line, paneIndexMap) {
    const target = normalizeLineTarget(line?.target)

    if (target === 'price') {
        return 0
    }

    if (target === 'separate') {
        const paneKey = getLinePaneKey(indicator, line)
        return paneIndexMap.get(paneKey) ?? 0
    }

    return null
}

function buildChangedIndicatorColumnsSet(indicatorColumnDetails = []) {
    const changedColumns = new Set()

    for (const detail of indicatorColumnDetails || []) {
        const columnName = String(detail?.normalized_column_name || detail?.column_name || '').trim()

        if (columnName) {
            changedColumns.add(columnName)
        }
    }

    return changedColumns
}

function buildIndicatorLineMetaMap(lines = []) {
    const map = new Map()

    for (const line of lines) {
        if (line?.columnName) {
            map.set(line.columnName, line)
        }
    }

    return map
}

function normalizeIndicatorColumnVariant(columnName) {
    return String(columnName || '')
        .trim()
        .split('_')
        .map((part) => {
            if (/^-?\d+\.0+$/.test(part)) {
                return String(Number(part))
            }

            return part
        })
        .join('_')
}

function getChartHistoryTimeoutSeconds(bars) {
    const safeBars = Math.max(1, Number(bars) || 0)
    if (safeBars <= 10000) {
        return 12
    }
    if (safeBars <= 50000) {
        return 20
    }
    if (safeBars <= 100000) {
        return 30
    }
    return 45
}

function buildIndicatorRowColumnLookup(indicatorRows = []) {
    const lookup = new Map()

    for (const row of indicatorRows || []) {
        for (const key of Object.keys(row || {})) {
            if (key === 'time') {
                continue
            }

            const normalizedKey = normalizeIndicatorColumnVariant(key)
            if (!lookup.has(normalizedKey)) {
                lookup.set(normalizedKey, key)
            }
        }
    }

    return lookup
}

function buildIndicatorLinesFromIndicators(indicators = []) {
    return (indicators || []).flatMap((indicator) =>
        (indicator.lines || [])
            .filter((line) => line?.columnName)
            .map((line) => ({
                visibilityKey: `${indicator.id}:${line.columnName}`,
                indicatorId: indicator.id,
                indicatorAlias: indicator.alias || indicator.name,
                indicatorName: indicator.name,
                columnName: line.columnName,
                color: line.color,
                strategyTokenName: getStrategyTokenNameForIndicatorLine(indicator, line),
                label: getStrategyTokenNameForIndicatorLine(indicator, line),
                target: normalizeLineTarget(line?.target),
                paneId: line?.paneId || '',
                hiddenTarget: normalizeLineTarget(line?.hiddenTarget),
                hiddenPaneId: line?.hiddenPaneId || '',
                markerPosition: String(line?.markerPosition || '').trim(),
                markerShape: String(line?.markerShape || '').trim(),
                markerColor: String(line?.markerColor || '').trim(),
                markerText: String(line?.markerText || '').trim(),
                markerSize: toFiniteNumberOrNull(line?.markerSize),
                markerMinValue: toFiniteNumberOrNull(line?.markerMinValue),
            }))
    )
}

function buildIndicatorPatternMarkerPayload(indicatorRows = [], indicators = []) {
    const indicatorLines = buildIndicatorLinesFromIndicators(indicators)
    const lineMetaByColumn = buildIndicatorLineMetaMap(indicatorLines)
    const indicatorRowColumnLookup = buildIndicatorRowColumnLookup(indicatorRows)
    const markers = []
    const candidateIds = new Set()

    for (const [columnName, lineMeta] of lineMetaByColumn.entries()) {
        const markerPosition = String(lineMeta?.markerPosition || '').trim()
        const markerShape = String(lineMeta?.markerShape || '').trim()

        if (!CHART_MARKER_POSITIONS.has(markerPosition) || !CHART_MARKER_SHAPES.has(markerShape)) {
            continue
        }

        const normalizedColumnName = normalizeIndicatorColumnVariant(columnName)
        const rowColumnName = indicatorRowColumnLookup.get(normalizedColumnName) || columnName
        if (!indicatorRowColumnLookup.has(normalizedColumnName)) {
            continue
        }
        const markerThreshold = toFiniteNumberOrNull(lineMeta?.markerMinValue) ?? 0.5

        for (const row of Array.isArray(indicatorRows) ? indicatorRows : []) {
            const time = normalizeChartTime(row?.time)
            const value = toFiniteNumberOrNull(row?.[rowColumnName])
            const markerId = time === null ? '' : `indicator:${columnName}:${time}`

            if (markerId) {
                candidateIds.add(markerId)
            }

            if (time === null || value === null || value < markerThreshold) {
                continue
            }

            const color = String(lineMeta?.markerColor || lineMeta?.color || '#94a3b8').trim() || '#94a3b8'
            const text = String(
                lineMeta?.markerText
                || lineMeta?.label
                || lineMeta?.strategyTokenName
                || columnName
            ).trim()
            const size = toFiniteNumberOrNull(lineMeta?.markerSize)
            markers.push({
                id: markerId,
                time,
                position: markerPosition,
                shape: markerShape,
                color,
                size: size !== null && size > 0 ? size : 1,
                text,
            })
        }
    }

    return {
        markers,
        candidateIds: Array.from(candidateIds.values()),
    }
}

function hasIndicatorPatternMarkerLines(indicators = []) {
    return buildIndicatorLinesFromIndicators(indicators).some((lineMeta) => (
        CHART_MARKER_POSITIONS.has(String(lineMeta?.markerPosition || '').trim())
        && CHART_MARKER_SHAPES.has(String(lineMeta?.markerShape || '').trim())
    ))
}

function areIndicatorVisibilityStatesEqual(left = {}, right = {}) {
    const leftKeys = Object.keys(left)
    const rightKeys = Object.keys(right)

    if (leftKeys.length !== rightKeys.length) {
        return false
    }

    for (const key of leftKeys) {
        if (left[key] !== right[key]) {
            return false
        }
    }

    return true
}

function areChartHistoryStatesEqual(left = {}, right = {}) {
    return (
        Number(left?.loadedCandles || 0) === Number(right?.loadedCandles || 0)
        && Number(left?.historyLoadStep || 0) === Number(right?.historyLoadStep || 0)
        && Number(left?.firstLoadedTime || 0) === Number(right?.firstLoadedTime || 0)
        && Number(left?.lastLoadedTime || 0) === Number(right?.lastLoadedTime || 0)
        && Boolean(left?.isReady) === Boolean(right?.isReady)
        && String(left?.error || '') === String(right?.error || '')
    )
}

function groupIndicatorPatternMarkers(rawMarkers = []) {
    const groupedMarkers = new Map()

    for (const marker of Array.isArray(rawMarkers) ? rawMarkers : []) {
        const groupKey = [
            normalizeChartTime(marker?.time) ?? '',
            String(marker?.position || '').trim(),
            String(marker?.shape || '').trim(),
            String(marker?.color || '').trim(),
        ].join('|')
        const existing = groupedMarkers.get(groupKey) || {
            ids: [],
            texts: [],
            marker: {
                time: marker.time,
                position: marker.position,
                shape: marker.shape,
                color: marker.color,
                size: marker.size,
            },
        }

        existing.ids.push(String(marker?.id || '').trim())
        if (String(marker?.text || '').trim()) {
            existing.texts.push(String(marker.text).trim())
        }
        groupedMarkers.set(groupKey, existing)
    }

    return Array.from(groupedMarkers.values())
        .map((entry) => ({
            ...entry.marker,
            id: `indicator:${entry.marker.time}:${entry.ids.join('|')}`,
            text: [...new Set(entry.texts)].join(' · '),
        }))
        .sort((left, right) => {
            const timeDiff = Number(left?.time || 0) - Number(right?.time || 0)
            if (timeDiff !== 0) {
                return timeDiff
            }
            return String(left?.id || '').localeCompare(String(right?.id || ''))
        })
}

function inferTradeMarkerReason(marker) {
    const text = String(marker?.text || '').trim()
    const normalizedText = text.toLowerCase()

    if (normalizedText.includes('stop gain')) {
        return 'stop gain'
    }

    if (normalizedText.includes('stop loss')) {
        return 'stop loss'
    }

    if (normalizedText.includes('close normal')) {
        return 'close'
    }

    if (normalizedText.includes('close')) {
        return 'close'
    }

    if (String(marker?.id || '').includes('-open-')) {
        return 'open'
    }

    return ''
}

function inferTradeMarkerNetValue(marker) {
    const text = String(marker?.text || '')
    const match = text.match(/Net\s+(-?\d+(?:\.\d+)?)/i)

    if (!match) {
        return null
    }

    const parsed = Number(match[1])
    return Number.isFinite(parsed) ? parsed : null
}

function normalizeTradeMarker(marker) {
    const reason = inferTradeMarkerReason(marker)
    const netValue = inferTradeMarkerNetValue(marker)
    const baseText = String(marker?.text || '').trim()
    const isLong = String(marker?.id || '').startsWith('long-') || /^long\b/i.test(baseText)
    const sideLabel = isLong ? 'Long' : 'Short'
    const time = normalizeChartTime(marker?.time)

    if (time === null) {
        return null
    }

    let color = marker?.color
    let text = baseText

    if (reason === 'open') {
        color = '#f3f4f6'
        if (!text) {
            text = `${sideLabel} open`
        }
    } else if (reason === 'stop gain') {
        color = '#22c55e'
    } else if (reason === 'stop loss') {
        color = '#ef4444'
    } else if (reason === 'close') {
        if (netValue !== null) {
            color = netValue >= 0 ? '#22c55e' : '#ef4444'
        }
    }

    if (netValue !== null) {
        if (reason === 'stop gain') {
            text = `${sideLabel} stop gain | Net ${netValue.toFixed(2)}`
        } else if (reason === 'stop loss') {
            text = `${sideLabel} stop loss | Net ${netValue.toFixed(2)}`
        } else if (reason === 'close') {
            text = `${sideLabel} close | Net ${netValue.toFixed(2)}`
        }
    }

    const fallbackPosition = reason === 'open'
        ? (isLong ? 'belowBar' : 'aboveBar')
        : (isLong ? 'aboveBar' : 'belowBar')
    const fallbackShape = reason === 'open'
        ? (isLong ? 'arrowUp' : 'arrowDown')
        : 'circle'
    const position = CHART_MARKER_POSITIONS.has(String(marker?.position || '').trim())
        ? String(marker.position).trim()
        : fallbackPosition
    const shape = CHART_MARKER_SHAPES.has(String(marker?.shape || '').trim())
        ? String(marker.shape).trim()
        : fallbackShape
    const size = toFiniteNumberOrNull(marker?.size)
    const safeId = String(marker?.id || `${time}:${text || reason || 'marker'}`).trim()
    const candidateTimes = []
    const seenCandidateTimes = new Set()
    const pushCandidateTime = (value) => {
        const normalizedCandidate = normalizeChartTime(value)
        if (normalizedCandidate === null || seenCandidateTimes.has(normalizedCandidate)) {
            return
        }
        seenCandidateTimes.add(normalizedCandidate)
        candidateTimes.push(normalizedCandidate)
    }

    pushCandidateTime(time)
    for (const value of Array.isArray(marker?.candidateTimes) ? marker.candidateTimes : []) {
        pushCandidateTime(value)
    }

    return {
        id: safeId,
        time,
        position,
        shape,
        color: String(color || '#94a3b8').trim() || '#94a3b8',
        text,
        size: size !== null && size > 0 ? size : 1,
        candidateTimes,
    }
}

export function Chart({
    id,
    authToken = '',
    chartSettings,
    runId,
    displayMode = 'default',
    isDrawLineModeActive = false,
    drawingTool = 'segment',
    metaFontSize = 0.84,
    pendingLineColor = '#d9d9d9',
    scrollChartToEndOnTickIncoming = false,
    showVolumePanel = true,
    volumeMode = 'volume',
    onMetaFontSizeChange,
    onPendingLineColorChange,
    onRequestDisableDrawingMode,
    initialDrawings = [],
    initialVisibleIndicatorColumns = {},
    onDrawingsChange,
    onVisibleIndicatorColumnsChange,
    onIndicatorLineVisibilityChange,
    onInsertStrategyText,
    onLogEvent,
    onHistoryStateChange,
    onErrorStateChange,
    tradeMarkers = [],
    backtestMarkerInfo = null,
    tradeMarkerMode = 'trader',
    onTradeMarkerModeChange,
    streamLeadingControls = null,
    indicatorLegendLeadingControls = null,
    guestNoticeVisible = false,
    onGuestNoticeClose,
    streamMetaPlacement = 'overlay',
    streamMetaCollapsed = false,
}) {
    const authHeaders = authToken
        ? {
            Authorization: `Bearer ${authToken}`,
        }
        : {}
    const isStreamDisplayMode = String(displayMode || '').trim().toLowerCase() === 'stream'
    const usesExternalStreamMeta = isStreamDisplayMode && String(streamMetaPlacement || '').trim().toLowerCase() === 'external'
    const containerRef = useRef(null)
    const chartRef = useRef(null)
    const candleSeriesRef = useRef(null)
    const volumeSeriesRef = useRef(null)
    const tradeMarkersPrimitiveRef = useRef(null)
    const indicatorPatternMarkerMapRef = useRef(new Map())
    const indicatorPatternMarkersRef = useRef([])
    const indicatorSeriesRef = useRef({})
    const indicatorSeriesPaneRef = useRef({})
    const indicatorLoadedTimesRef = useRef({})
    const loadedTimesRef = useRef(new Set())
    const candleVolumeByTimeRef = useRef(new Map())
    const pollingActiveRef = useRef(false)
    const marketRevisionRef = useRef(null)
    const marketSocketRef = useRef(null)
    const marketSocketConnectedRef = useRef(false)
    const marketReconnectTimerRef = useRef(null)
    const marketDeltaInFlightRef = useRef(false)
    const marketDeltaQueuedRef = useRef(false)
    const historyExpansionInFlightRef = useRef(false)
    const historyExpansionCooldownUntilRef = useRef(0)
    const lastRealtimeChartSyncAtRef = useRef(0)
    const backtestViewportFocusKeyRef = useRef('')
    const backtestOverlayFollowUnlockedRef = useRef(false)
    const loadedCandleCountRef = useRef(0)
    const historyLoadStepRef = useRef(1000)
    const internalRunIdRef = useRef(0)
    const latestCandleInfoRef = useRef(null)
    const latestIndicatorValuesRef = useRef({})
    const dataSanitizationTotalsRef = useRef({
        candles: 0,
        indicators: 0,
        markers: 0,
    })
    const outOfOrderTotalsRef = useRef({
        candles: 0,
        indicators: {},
    })
    const renderGuardTotalsRef = useRef({
        setData: {},
        update: {},
    })
    const candleTimeBoundsRef = useRef({
        min: null,
        max: null,
    })
    const indicatorTimeBoundsRef = useRef({})
    const pendingLineStartRef = useRef(null)
    const pendingLinePreviewEndRef = useRef(null)
    const hasPendingLineDragRef = useRef(false)
    const hiddenLinePlacementsRef = useRef({})
    const backendIndicatorsPayload = buildBackendIndicatorsPayload(chartSettings?.indicators || [])
    const dataRequestSignature = JSON.stringify({
        symbol: chartSettings?.symbol || 'EURUSD',
        timeframe: chartSettings?.timeframe || 'M1',
        indicators: backendIndicatorsPayload,
    })
    const precision = chartSettings?.precision ?? 5
    const safePrecision = Math.max(0, Math.min(10, precision))
    const normalizedIndicators = useMemo(
        () => normalizeIndicators(chartSettings?.indicators || []),
        [chartSettings?.indicators]
    )
    const visualIndicatorSignature = useMemo(
        () => JSON.stringify(
            normalizedIndicators.map((indicator) => ({
                id: indicator?.id || '',
                alias: indicator?.alias || '',
                name: indicator?.name || '',
                lines: (indicator?.lines || []).map((line) => ({
                    columnName: line?.columnName || '',
                    target: line?.target || '',
                    paneId: line?.paneId || '',
                })),
            }))
        ),
        [normalizedIndicators]
    )
    const allIndicatorLines = useMemo(
        () => buildIndicatorLinesFromIndicators(normalizedIndicators),
        [normalizedIndicators]
    )
    const legendIndicatorLines = allIndicatorLines
    const [visibleIndicatorColumns, setVisibleIndicatorColumns] = useState(() =>
        buildVisibleColumnsState(allIndicatorLines, initialVisibleIndicatorColumns)
    )

    const [chartError, setChartError] = useState('')
    const [isChartReady, setIsChartReady] = useState(false)
    const [flashState, setFlashState] = useState({
        symbol: false,
        timeframe: false,
        bars: false,
    })
    const [isIndicatorLegendMenuOpen, setIsIndicatorLegendMenuOpen] = useState(false)
    const [isIndicatorLegendExpanded, setIsIndicatorLegendExpanded] = useState(false)
    const [indicatorLegendOverflowState, setIndicatorLegendOverflowState] = useState({
        collapsedHeight: 0,
        overflowVisibleCount: 0,
    })
    const hasCompatibleBacktestOverlay = ['backtest', 'both'].includes(String(tradeMarkerMode || '').trim().toLowerCase())
        && backtestMarkerInfo?.isCompatible !== false
        && Array.isArray(backtestMarkerInfo?.markers)
        && backtestMarkerInfo.markers.length > 0
    const [loadedCandleCount, setLoadedCandleCount] = useState(0)
    const [cursorCandle, setCursorCandle] = useState(null)
    const [cursorVolume, setCursorVolume] = useState(null)
    const [indicatorValues, setIndicatorValues] = useState({})
    const [indicatorMarkerVersion, setIndicatorMarkerVersion] = useState(0)
    const [isAwaitingLineEnd, setIsAwaitingLineEnd] = useState(false)
    const [drawings, setDrawings] = useState(() => (
        Array.isArray(initialDrawings) ? initialDrawings : []
    ))
    const [selectedDrawingId, setSelectedDrawingId] = useState('')
    const [draggingDrawing, setDraggingDrawing] = useState(null)
    const [pendingLinePreview, setPendingLinePreview] = useState(null)
    const appliedHeaderValuesRef = useRef(null)
    const lastReportedHistoryStateRef = useRef(null)
    const lastReportedDrawingsRef = useRef(null)
    const lastReportedVisibleIndicatorColumnsRef = useRef(null)
    const indicatorLegendVisibleListRef = useRef(null)
    const legendLinesWithVisibility = useMemo(
        () => legendIndicatorLines.map((line) => ({
            ...line,
            isLegendVisible: Boolean(
                visibleIndicatorColumns[line.visibilityKey] ?? (line.target !== 'hidden')
            ),
        })),
        [legendIndicatorLines, visibleIndicatorColumns]
    )
    const visibleLegendLines = useMemo(
        () => legendLinesWithVisibility.filter((line) => line.isLegendVisible),
        [legendLinesWithVisibility]
    )
    const hiddenLegendLines = useMemo(
        () => legendLinesWithVisibility.filter((line) => !line.isLegendVisible),
        [legendLinesWithVisibility]
    )
    const backtestMarkerStatus = useMemo(() => {
        const markerCandidates = Array.isArray(backtestMarkerInfo?.markers) ? backtestMarkerInfo.markers : []
        const loadedTimes = loadedTimesRef.current instanceof Set ? loadedTimesRef.current : new Set()
        let totalCount = 0
        let visibleCount = 0

        for (const marker of markerCandidates) {
            const normalized = normalizeTradeMarker(marker)
            if (!normalized) {
                continue
            }
            totalCount += 1
            if (loadedTimes.has(normalized.time)) {
                visibleCount += 1
            }
        }

        const isCompatible = backtestMarkerInfo?.isCompatible !== false
        return {
            totalCount,
            visibleCount: isCompatible ? visibleCount : 0,
            hiddenCount: isCompatible ? Math.max(totalCount - visibleCount, 0) : totalCount,
            isCompatible,
            runSymbol: String(backtestMarkerInfo?.runSymbol || '').trim().toUpperCase(),
            runTimeframe: String(backtestMarkerInfo?.runTimeframe || '').trim().toUpperCase(),
        }
    }, [backtestMarkerInfo, loadedCandleCount])

    function formatPrice(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '--'
        }

        return Number(value).toFixed(safePrecision)
    }

    function formatVolume(value) {
        const numeric = Number(value)

        if (!Number.isFinite(numeric)) {
            return '--'
        }

        if (Math.abs(numeric) >= 1_000_000) {
            return `${(numeric / 1_000_000).toFixed(2)}M`
        }

        if (Math.abs(numeric) >= 1_000) {
            return `${(numeric / 1_000).toFixed(2)}K`
        }

        return numeric.toLocaleString(undefined, {
            maximumFractionDigits: 0,
        })
    }

    function resolveDisplayedVolume(candle) {
        const safeMode = String(volumeMode || 'volume').trim().toLowerCase()

        if (safeMode === 'tick' || safeMode === 'tick_volume') {
            return Number(candle?.tick_volume ?? candle?.volume) || 0
        }

        if (safeMode === 'real' || safeMode === 'real_volume') {
            return Number(candle?.real_volume ?? 0) || 0
        }

        return Number(candle?.volume ?? candle?.tick_volume ?? candle?.real_volume) || 0
    }

    function reportSanitizedData(kind, droppedCount, contextLabel) {
        const safeKind = String(kind || '').trim()
        const safeDroppedCount = Math.max(0, Number(droppedCount) || 0)

        if (!safeKind || safeDroppedCount <= 0) {
            return
        }

        const currentTotal = Math.max(0, Number(dataSanitizationTotalsRef.current[safeKind]) || 0)
        const nextTotal = currentTotal + safeDroppedCount
        dataSanitizationTotalsRef.current[safeKind] = nextTotal

        if (nextTotal <= 5 || nextTotal % 25 === 0) {
            onLogEvent?.(
                `Chart sanitized ${nextTotal.toLocaleString()} invalid ${safeKind} entries (${contextLabel}).`,
                'warn'
            )
        }
    }

    function reportOutOfOrderDelta(kind, label) {
        const safeKind = String(kind || '').trim()
        const safeLabel = String(label || '').trim()
        if (!safeKind || !safeLabel) {
            return
        }

        if (safeKind === 'candles') {
            const nextCount = (Number(outOfOrderTotalsRef.current.candles) || 0) + 1
            outOfOrderTotalsRef.current.candles = nextCount
            if (nextCount <= 3 || nextCount % 25 === 0) {
                onLogEvent?.(
                    `Chart skipped out-of-order candle delta (${safeLabel}) while history is being rebased (occurrence ${nextCount}).`,
                    'warn'
                )
            }
            return
        }

        if (safeKind === 'indicators') {
            const currentTotals = outOfOrderTotalsRef.current.indicators || {}
            const nextCount = (Number(currentTotals[safeLabel]) || 0) + 1
            currentTotals[safeLabel] = nextCount
            outOfOrderTotalsRef.current.indicators = currentTotals
            if (nextCount <= 3 || nextCount % 25 === 0) {
                onLogEvent?.(
                    `Chart skipped out-of-order indicator delta (${safeLabel}) while history is being rebased (occurrence ${nextCount}).`,
                    'warn'
                )
            }
        }
    }

    function setCandleBoundsFromSortedData(candleData) {
        if (!Array.isArray(candleData) || candleData.length === 0) {
            candleTimeBoundsRef.current = { min: null, max: null }
            return
        }

        const minTime = Number(candleData[0]?.time)
        const maxTime = Number(candleData[candleData.length - 1]?.time)

        candleTimeBoundsRef.current = {
            min: Number.isFinite(minTime) ? minTime : null,
            max: Number.isFinite(maxTime) ? maxTime : null,
        }
    }

    function updateCandleBoundsWithTime(time) {
        const numericTime = Number(time)
        if (!Number.isFinite(numericTime)) {
            return
        }

        const current = candleTimeBoundsRef.current || { min: null, max: null }
        const nextMin = current.min === null ? numericTime : Math.min(current.min, numericTime)
        const nextMax = current.max === null ? numericTime : Math.max(current.max, numericTime)
        candleTimeBoundsRef.current = {
            min: nextMin,
            max: nextMax,
        }
    }

    function getCurrentMaxLoadedCandleTime() {
        const currentMax = Number(candleTimeBoundsRef.current?.max)
        if (Number.isFinite(currentMax)) {
            return currentMax
        }

        if (!(loadedTimesRef.current instanceof Set) || loadedTimesRef.current.size === 0) {
            return null
        }

        let fallbackMax = null
        for (const time of loadedTimesRef.current) {
            const numericTime = Number(time)
            if (!Number.isFinite(numericTime)) {
                continue
            }
            fallbackMax = fallbackMax === null ? numericTime : Math.max(fallbackMax, numericTime)
        }
        return fallbackMax
    }

    function setIndicatorBoundsFromSortedData(columnName, points) {
        if (!columnName) {
            return
        }

        if (!Array.isArray(points) || points.length === 0) {
            delete indicatorTimeBoundsRef.current[columnName]
            return
        }

        const minTime = Number(points[0]?.time)
        const maxTime = Number(points[points.length - 1]?.time)

        indicatorTimeBoundsRef.current[columnName] = {
            min: Number.isFinite(minTime) ? minTime : null,
            max: Number.isFinite(maxTime) ? maxTime : null,
        }
    }

    function updateIndicatorBoundsWithTime(columnName, time) {
        if (!columnName) {
            return
        }

        const numericTime = Number(time)
        if (!Number.isFinite(numericTime)) {
            return
        }

        const current = indicatorTimeBoundsRef.current[columnName] || { min: null, max: null }
        const nextMin = current.min === null ? numericTime : Math.min(current.min, numericTime)
        const nextMax = current.max === null ? numericTime : Math.max(current.max, numericTime)
        indicatorTimeBoundsRef.current[columnName] = {
            min: nextMin,
            max: nextMax,
        }
    }

    function sanitizeSeriesDataForSetData(data = [], label = '') {
        const inputRows = Array.isArray(data) ? data : []

        if (inputRows.length === 0) {
            return {
                rows: [],
                dropped: 0,
            }
        }

        const safeLabel = String(label || '').trim().toLowerCase()
        const looksLikeCandleSeries = inputRows.some((row) => (
            row
            && typeof row === 'object'
            && (
                Object.prototype.hasOwnProperty.call(row, 'open')
                || Object.prototype.hasOwnProperty.call(row, 'high')
                || Object.prototype.hasOwnProperty.call(row, 'low')
                || Object.prototype.hasOwnProperty.call(row, 'close')
            )
        ))
        const looksLikeValueSeries = !looksLikeCandleSeries && (
            safeLabel.includes('indicator')
            || safeLabel.includes('volume')
            || inputRows.some((row) => (
                row
                && typeof row === 'object'
                && Object.prototype.hasOwnProperty.call(row, 'value')
            ))
        )

        const rowsByTime = new Map()
        let dropped = 0

        for (const row of inputRows) {
            const time = normalizeChartTime(row?.time)

            if (time === null) {
                dropped += 1
                continue
            }

            if (looksLikeCandleSeries) {
                const open = toFiniteNumberOrNull(row?.open)
                const high = toFiniteNumberOrNull(row?.high)
                const low = toFiniteNumberOrNull(row?.low)
                const close = toFiniteNumberOrNull(row?.close)

                if (open === null || high === null || low === null || close === null) {
                    dropped += 1
                    continue
                }

                rowsByTime.set(time, {
                    time,
                    open,
                    high: Math.max(high, open, close, low),
                    low: Math.min(low, open, close, high),
                    close,
                })
                continue
            }

            if (looksLikeValueSeries) {
                const value = toFiniteNumberOrNull(row?.value)

                if (value === null) {
                    dropped += 1
                    continue
                }

                rowsByTime.set(time, {
                    ...(row && typeof row === 'object' ? row : {}),
                    time,
                    value,
                })
                continue
            }

            rowsByTime.set(time, {
                ...(row && typeof row === 'object' ? row : {}),
                time,
            })
        }

        return {
            rows: Array.from(rowsByTime.values()).sort((left, right) => left.time - right.time),
            dropped,
        }
    }

    function safeSetSeriesData(series, data, label) {
        if (!series || typeof series.setData !== 'function') {
            return false
        }

        const safeLabel = String(label || 'series setData').trim() || 'series setData'
        const { rows: safeData, dropped } = sanitizeSeriesDataForSetData(data, safeLabel)

        if (dropped > 0) {
            const kind = safeLabel.toLowerCase().includes('indicator') ? 'indicators' : 'candles'
            reportSanitizedData(kind, dropped, `${safeLabel} setData guard`)
        }

        try {
            series.setData(safeData)
            return true
        } catch (error) {
            const message = String(error?.message || 'unknown error').trim() || 'unknown error'
            const totals = renderGuardTotalsRef.current?.setData || {}
            const nextCount = (Number(totals[safeLabel]) || 0) + 1
            totals[safeLabel] = nextCount
            if (renderGuardTotalsRef.current) {
                renderGuardTotalsRef.current.setData = totals
            }

            if (nextCount <= 3 || nextCount % 25 === 0) {
                onLogEvent?.(
                    `Chart render guard (${safeLabel}) blocked an invalid setData call (occurrence ${nextCount}): ${message}. rows=${safeData.length}.`,
                    'warn'
                )
            }
            return false
        }
    }

    function safeUpdateSeries(series, dataPoint, isHistoricalUpdate, label) {
        if (!series || typeof series.update !== 'function') {
            return false
        }

        try {
            if (isHistoricalUpdate === undefined) {
                series.update(dataPoint)
            } else {
                series.update(dataPoint, isHistoricalUpdate)
            }
            return true
        } catch (error) {
            const safeLabel = String(label || 'series update').trim() || 'series update'
            const message = String(error?.message || 'unknown error').trim() || 'unknown error'
            const totals = renderGuardTotalsRef.current?.update || {}
            const nextCount = (Number(totals[safeLabel]) || 0) + 1
            totals[safeLabel] = nextCount
            if (renderGuardTotalsRef.current) {
                renderGuardTotalsRef.current.update = totals
            }

            if (nextCount <= 3 || nextCount % 25 === 0) {
                onLogEvent?.(
                    `Chart render guard (${safeLabel}) blocked an invalid update call (occurrence ${nextCount}): ${message}.`,
                    'warn'
                )
            }
            return false
        }
    }

    const selectedDrawing = drawings.find((drawing) => drawing.id === selectedDrawingId) || null

    useEffect(() => {
        const loadedTimes = Array.from(loadedTimesRef.current || [])
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value))
            .sort((left, right) => left - right)
        const nextHistoryState = {
            loadedCandles: loadedCandleCount,
            historyLoadStep: historyLoadStepRef.current,
            firstLoadedTime: loadedTimes.length ? loadedTimes[0] : null,
            lastLoadedTime: loadedTimes.length ? loadedTimes[loadedTimes.length - 1] : null,
            isReady: isChartReady,
            error: chartError,
        }
        if (areChartHistoryStatesEqual(lastReportedHistoryStateRef.current, nextHistoryState)) {
            return
        }
        lastReportedHistoryStateRef.current = nextHistoryState
        onHistoryStateChange?.(nextHistoryState)
    }, [chartError, isChartReady, loadedCandleCount, onHistoryStateChange])

    useEffect(() => {
        onErrorStateChange?.(chartError)
    }, [chartError, onErrorStateChange])

    function handleMetaFontStep(step) {
        const next = metaFontSize + step
        const normalized = Math.max(0.58, Math.min(1.05, Number(next.toFixed(2))))
        onMetaFontSizeChange?.(normalized)
    }

    function updateDrawingColor(drawingId, color) {
        setDrawings((current) => current.map((drawing) => (
            drawing.id === drawingId
                ? { ...drawing, color }
                : drawing
        )))
    }

    function handleDrawingEditorColorChange(color) {
        if (selectedDrawing) {
            updateDrawingColor(selectedDrawing.id, color)
            return
        }

        onPendingLineColorChange?.(color)
    }

    function handleDrawingEditorClose() {
        if (selectedDrawing) {
            setDrawings((current) => current.filter((drawing) => drawing.id !== selectedDrawing.id))
            setSelectedDrawingId('')
            setDraggingDrawing(null)
            return
        }

        clearPendingLine()
        onRequestDisableDrawingMode?.()
    }

    function clearPendingLine() {
        pendingLineStartRef.current = null
        pendingLinePreviewEndRef.current = null
        hasPendingLineDragRef.current = false
        setIsAwaitingLineEnd(false)
        setPendingLinePreview(null)
    }

    function getChartPointFromClientPosition(clientX, clientY) {
        const container = containerRef.current
        const chart = chartRef.current
        const candleSeries = candleSeriesRef.current

        if (!container || !chart || !candleSeries) {
            return null
        }

        const bounds = container.getBoundingClientRect()
        const x = clientX - bounds.left
        const y = clientY - bounds.top

        if (x < 0 || y < 0 || x > bounds.width || y > bounds.height) {
            return null
        }

        let logical = null
        let price = null

        try {
            logical = chart.timeScale()?.coordinateToLogical?.(x)
        } catch {
            return null
        }

        try {
            price = candleSeries.coordinateToPrice?.(y)
        } catch {
            return null
        }

        if (logical === null || logical === undefined || price === null || price === undefined) {
            return null
        }

        return { logical, price }
    }

    function getDrawingCoordinates(drawing) {
        const container = containerRef.current
        const chart = chartRef.current
        const candleSeries = candleSeriesRef.current

        if (
            !container
            || !chart
            || !candleSeries
            || !drawing
            || !drawing.start
            || drawing.start.logical === null
            || drawing.start.logical === undefined
            || drawing.start.price === null
            || drawing.start.price === undefined
        ) {
            return null
        }

        const width = container.clientWidth
        const height = container.clientHeight
        let startX = null
        let startY = null

        try {
            startX = chart.timeScale()?.logicalToCoordinate?.(drawing.start.logical)
            startY = candleSeries.priceToCoordinate?.(drawing.start.price)
        } catch {
            return null
        }

        if (startX === null || startX === undefined || startY === null || startY === undefined) {
            return null
        }

        if (drawing.type === 'horizontal') {
            return {
                x1: 0,
                y1: startY,
                x2: width,
                y2: startY,
                startX,
                startY,
                endX: width,
                endY: startY,
            }
        }

        if (drawing.type === 'vertical') {
            return {
                x1: startX,
                y1: 0,
                x2: startX,
                y2: height,
                startX,
                startY,
                endX: startX,
                endY: height,
            }
        }

        if (
            !drawing.end
            || drawing.end.logical === null
            || drawing.end.logical === undefined
            || drawing.end.price === null
            || drawing.end.price === undefined
        ) {
            return null
        }

        let endX = null
        let endY = null

        try {
            endX = chart.timeScale()?.logicalToCoordinate?.(drawing.end.logical)
            endY = candleSeries.priceToCoordinate?.(drawing.end.price)
        } catch {
            return null
        }

        if (endX === null || endX === undefined || endY === null || endY === undefined) {
            return null
        }

        if (drawing.type === 'segment') {
            return {
                x1: startX,
                y1: startY,
                x2: endX,
                y2: endY,
                startX,
                startY,
                endX,
                endY,
            }
        }

        const deltaX = endX - startX
        const deltaY = endY - startY

        if (Math.abs(deltaX) < 0.001) {
            return {
                x1: startX,
                y1: 0,
                x2: startX,
                y2: height,
                startX,
                startY,
                endX: startX,
                endY: height,
            }
        }

        const targetX = deltaX >= 0 ? width : 0
        const slope = deltaY / deltaX
        const targetY = startY + slope * (targetX - startX)

        return {
            x1: startX,
            y1: startY,
            x2: targetX,
            y2: targetY,
            startX,
            startY,
            endX: targetX,
            endY: targetY,
        }
    }

    function getDistanceToPoint(pointX, pointY, targetX, targetY) {
        return Math.hypot(pointX - targetX, pointY - targetY)
    }

    function getDistanceToSegment(pointX, pointY, x1, y1, x2, y2) {
        const deltaX = x2 - x1
        const deltaY = y2 - y1

        if (deltaX === 0 && deltaY === 0) {
            return getDistanceToPoint(pointX, pointY, x1, y1)
        }

        const projection = ((pointX - x1) * deltaX + (pointY - y1) * deltaY) / (deltaX * deltaX + deltaY * deltaY)
        const clampedProjection = Math.max(0, Math.min(1, projection))
        const projectedX = x1 + clampedProjection * deltaX
        const projectedY = y1 + clampedProjection * deltaY

        return getDistanceToPoint(pointX, pointY, projectedX, projectedY)
    }

    function findDrawingDragTarget(clientX, clientY) {
        const container = containerRef.current

        if (!container) {
            return null
        }

        const bounds = container.getBoundingClientRect()
        const localX = clientX - bounds.left
        const localY = clientY - bounds.top
        const handleThreshold = 12
        const lineThreshold = 8

        for (let index = drawings.length - 1; index >= 0; index -= 1) {
            const drawing = drawings[index]
            const coordinates = getDrawingCoordinates(drawing)

            if (!coordinates) {
                continue
            }

            if (drawing.type === 'segment') {
                if (getDistanceToPoint(localX, localY, coordinates.startX, coordinates.startY) <= handleThreshold) {
                    return { drawingId: drawing.id, mode: 'start' }
                }

                if (getDistanceToPoint(localX, localY, coordinates.endX, coordinates.endY) <= handleThreshold) {
                    return { drawingId: drawing.id, mode: 'end' }
                }

                continue
            }

            if (drawing.type === 'ray') {
                if (getDistanceToPoint(localX, localY, coordinates.startX, coordinates.startY) <= handleThreshold) {
                    return { drawingId: drawing.id, mode: 'start' }
                }

                if (getDistanceToPoint(localX, localY, coordinates.endX, coordinates.endY) <= handleThreshold) {
                    return { drawingId: drawing.id, mode: 'end' }
                }

                continue
            }

            if (drawing.type === 'horizontal') {
                if (Math.abs(localY - coordinates.y1) <= lineThreshold) {
                    return { drawingId: drawing.id, mode: 'line' }
                }

                continue
            }

            if (drawing.type === 'vertical') {
                if (Math.abs(localX - coordinates.x1) <= lineThreshold) {
                    return { drawingId: drawing.id, mode: 'line' }
                }

                continue
            }

            if (getDistanceToSegment(localX, localY, coordinates.x1, coordinates.y1, coordinates.x2, coordinates.y2) <= lineThreshold) {
                return { drawingId: drawing.id, mode: 'line' }
            }
        }

        return null
    }

    function normalizeSeriesValue(seriesValue) {
        if (seriesValue === null || seriesValue === undefined) {
            return null
        }

        if (typeof seriesValue === 'number') {
            return seriesValue
        }

        if (typeof seriesValue?.value === 'number') {
            return seriesValue.value
        }

        return null
    }

    function buildVisibleColumnsState(lines, currentState = {}) {
        const nextState = {}

        for (const line of lines) {
            nextState[line.visibilityKey] = currentState[line.visibilityKey] ?? (line.target !== 'hidden')
        }

        return nextState
    }

    function setLatestIndicatorValues(nextValues) {
        latestIndicatorValuesRef.current = nextValues
        setIndicatorValues(nextValues)
    }

    function handleToggleIndicatorLine(lineMeta) {
        const visibilityKey = lineMeta.visibilityKey
        const isCurrentlyVisible = visibleIndicatorColumns[visibilityKey] ?? (lineMeta.target !== 'hidden')
        const nextVisible = !isCurrentlyVisible

        if (!nextVisible) {
            hiddenLinePlacementsRef.current[visibilityKey] = {
                target: lineMeta.target === 'hidden'
                    ? (lineMeta.hiddenTarget || '')
                    : lineMeta.target,
                paneId: lineMeta.target === 'hidden'
                    ? (lineMeta.hiddenPaneId || '')
                    : lineMeta.target === 'separate'
                        ? lineMeta.paneId
                        : '',
            }
        }

        const restoredPlacement = hiddenLinePlacementsRef.current[visibilityKey] || null

        setVisibleIndicatorColumns((current) => ({
            ...current,
            [visibilityKey]: nextVisible,
        }))

        onIndicatorLineVisibilityChange?.(
            lineMeta.indicatorId,
            lineMeta.columnName,
            nextVisible,
            restoredPlacement
        )
    }

    function handleToggleIndicatorGroup(indicator) {
        const indicatorLines = legendIndicatorLines.filter((line) => line.indicatorId === indicator?.id)
        if (!indicatorLines.length) {
            return
        }

        const allVisible = indicatorLines.every(
            (line) => visibleIndicatorColumns[line.visibilityKey] ?? (line.target !== 'hidden')
        )
        const nextVisible = !allVisible
        const nextVisibilityState = { ...visibleIndicatorColumns }

        for (const line of indicatorLines) {
            if (!nextVisible) {
                hiddenLinePlacementsRef.current[line.visibilityKey] = {
                    target: line.target === 'hidden'
                        ? (line.hiddenTarget || '')
                        : line.target,
                    paneId: line.target === 'hidden'
                        ? (line.hiddenPaneId || '')
                        : line.target === 'separate'
                            ? line.paneId
                            : '',
                }
            }

            nextVisibilityState[line.visibilityKey] = nextVisible
        }

        setVisibleIndicatorColumns(nextVisibilityState)

        for (const line of indicatorLines) {
            const restoredPlacement = hiddenLinePlacementsRef.current[line.visibilityKey] || null
            onIndicatorLineVisibilityChange?.(
                line.indicatorId,
                line.columnName,
                nextVisible,
                restoredPlacement
            )
        }
    }

    function handleInsertAtStrategyCursor(text) {
        onInsertStrategyText?.(text)
    }

    useEffect(() => {
        for (const line of allIndicatorLines) {
            if (line.target !== 'hidden') {
                hiddenLinePlacementsRef.current[line.visibilityKey] = {
                    target: line.target,
                    paneId: line.target === 'separate' ? line.paneId : '',
                }
            }
        }

        setVisibleIndicatorColumns((current) => {
            const nextState = buildVisibleColumnsState(allIndicatorLines, current)
            return areIndicatorVisibilityStatesEqual(current, nextState)
                ? current
                : nextState
        })
    }, [allIndicatorLines])

    useEffect(() => {
        function handlePointerDown(event) {
            if (!event.target.closest('.chartIndicatorLegendMenu') && !event.target.closest('.chartLegendToggle')) {
                setIsIndicatorLegendMenuOpen(false)
            }
        }

        window.addEventListener('pointerdown', handlePointerDown)

        return () => {
            window.removeEventListener('pointerdown', handlePointerDown)
        }
    }, [])

    useEffect(() => {
        const container = indicatorLegendVisibleListRef.current
        if (!container) {
            setIndicatorLegendOverflowState((current) => (
                current.collapsedHeight === 0 && current.overflowVisibleCount === 0
                    ? current
                    : {
                        collapsedHeight: 0,
                        overflowVisibleCount: 0,
                    }
            ))
            return
        }

        const measureLegendLayout = () => {
            const items = Array.from(
                container.querySelectorAll('[data-chart-indicator-legend-item="visible"]')
            )

            if (!items.length) {
                setIndicatorLegendOverflowState((current) => (
                    current.collapsedHeight === 0 && current.overflowVisibleCount === 0
                        ? current
                        : {
                            collapsedHeight: 0,
                            overflowVisibleCount: 0,
                        }
                ))
                return
            }

            const tolerance = 2
            const lineTops = []
            for (const item of items) {
                const top = Number(item.offsetTop) || 0
                if (!lineTops.some((candidate) => Math.abs(candidate - top) <= tolerance)) {
                    lineTops.push(top)
                }
            }
            lineTops.sort((left, right) => left - right)

            if (lineTops.length <= 2) {
                setIndicatorLegendOverflowState((current) => (
                    current.collapsedHeight === 0 && current.overflowVisibleCount === 0
                        ? current
                        : {
                            collapsedHeight: 0,
                            overflowVisibleCount: 0,
                        }
                ))
                return
            }

            const secondLineTop = lineTops[1]
            const visibleWithinTwoLines = items.filter(
                (item) => (Number(item.offsetTop) || 0) <= (secondLineTop + tolerance)
            )
            const collapsedHeight = visibleWithinTwoLines.reduce(
                (maximum, item) => Math.max(
                    maximum,
                    (Number(item.offsetTop) || 0) + (Number(item.offsetHeight) || 0)
                ),
                0,
            )
            const overflowVisibleCount = Math.max(items.length - visibleWithinTwoLines.length, 0)

            setIndicatorLegendOverflowState((current) => (
                current.collapsedHeight === collapsedHeight
                && current.overflowVisibleCount === overflowVisibleCount
                    ? current
                    : {
                        collapsedHeight,
                        overflowVisibleCount,
                    }
            ))
        }

        measureLegendLayout()

        if (typeof ResizeObserver === 'function') {
            const observer = new ResizeObserver(() => {
                measureLegendLayout()
            })
            observer.observe(container)
            return () => observer.disconnect()
        }

        window.addEventListener('resize', measureLegendLayout)
        return () => {
            window.removeEventListener('resize', measureLegendLayout)
        }
    }, [metaFontSize, visibleLegendLines, isIndicatorLegendExpanded])

    const hiddenLegendCount = hiddenLegendLines.length
    const overflowVisibleLegendCount = indicatorLegendOverflowState.overflowVisibleCount
    const collapsedLegendHeight = indicatorLegendOverflowState.collapsedHeight
    const hasIndicatorLegendExpansionTarget = overflowVisibleLegendCount > 0 || hiddenLegendCount > 0
    const collapsedLegendMaxHeight = (
        !isIndicatorLegendExpanded
        && overflowVisibleLegendCount > 0
        && collapsedLegendHeight > 0
    )
        ? `${collapsedLegendHeight}px`
        : undefined
    const legendOverflowSummaryParts = [
        overflowVisibleLegendCount > 0 ? `+${overflowVisibleLegendCount} more` : '',
        hiddenLegendCount > 0 ? `${hiddenLegendCount} hidden` : '',
    ].filter(Boolean)

    useEffect(() => {
        if (!hasIndicatorLegendExpansionTarget && isIndicatorLegendExpanded) {
            setIsIndicatorLegendExpanded(false)
        }
    }, [hasIndicatorLegendExpansionTarget, isIndicatorLegendExpanded])

    useEffect(() => {
        const nextValues = {
            symbol: String(chartSettings?.symbol || ''),
            timeframe: String(chartSettings?.timeframe || ''),
        }

        if (!appliedHeaderValuesRef.current) {
            appliedHeaderValuesRef.current = nextValues
            return
        }

        const changedKeys = Object.keys(nextValues).filter(
            (key) => nextValues[key] !== appliedHeaderValuesRef.current[key]
        )

        appliedHeaderValuesRef.current = nextValues

        if (changedKeys.length === 0) {
            return
        }

        setFlashState((current) => {
            const nextState = { ...current }

            for (const key of changedKeys) {
                nextState[key] = false
            }

            return nextState
        })

        const frameId = window.requestAnimationFrame(() => {
            setFlashState((current) => {
                const nextState = { ...current }

                for (const key of changedKeys) {
                    nextState[key] = true
                }

                return nextState
            })
        })

        const timeoutId = window.setTimeout(() => {
            setFlashState((current) => {
                const nextState = { ...current }

                for (const key of changedKeys) {
                    nextState[key] = false
                }

                return nextState
            })
        }, 1800)

        return () => {
            window.cancelAnimationFrame(frameId)
            window.clearTimeout(timeoutId)
        }
    }, [chartSettings?.symbol, chartSettings?.timeframe, loadedCandleCount])

    useEffect(() => {
        const chart = chartRef.current

        if (!chart) {
            return
        }

        chart.applyOptions({
            handleScroll: {
                mouseWheel: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                pressedMouseMove: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                horzTouchDrag: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                vertTouchDrag: !(isDrawLineModeActive || Boolean(draggingDrawing)),
            },
            handleScale: {
                mouseWheel: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                pinch: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                axisPressedMouseMove: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                axisDoubleClickReset: !(isDrawLineModeActive || Boolean(draggingDrawing)),
            },
        })
    }, [draggingDrawing, isDrawLineModeActive])

    useEffect(() => {
        const volumeSeries = volumeSeriesRef.current
        const chart = chartRef.current

        if (!volumeSeries || !chart) {
            return
        }

        volumeSeries.applyOptions({
            priceLineVisible: false,
            lastValueVisible: false,
            visible: showVolumePanel,
            color: showVolumePanel ? 'rgba(117, 167, 255, 0.68)' : 'rgba(117, 167, 255, 0.0)',
        })

        chart.priceScale('volume').applyOptions({
            visible: false,
        })

        const paneIndexMap = buildPaneIndexMap(normalizedIndicators)
        const panes = chart.panes ? chart.panes() : []
        const separatePaneCount = Math.max(0, paneIndexMap.size - 2)

        if (panes[0]) {
            panes[0].setHeight(separatePaneCount > 0 ? 420 : 560)
        }

        if (panes[1]) {
            panes[1].setHeight(showVolumePanel ? 132 : 0)
        }

        for (let paneIndex = 2; paneIndex < panes.length; paneIndex += 1) {
            panes[paneIndex]?.setHeight?.(140)
        }
    }, [normalizedIndicators, showVolumePanel])

    useEffect(() => {
        const volumeSeries = volumeSeriesRef.current

        if (!volumeSeries) {
            return
        }

        const candleRows = Array.from(candleVolumeByTimeRef.current.values())
        if (candleRows.length > 0) {
            const { rows: sanitizedRows, dropped } = buildSanitizedCandleRows(candleRows)
            reportSanitizedData('candles', dropped, 'volume panel refresh')

            const volumeData = sanitizedRows.map((candle) => ({
                time: candle.time,
                value: resolveDisplayedVolume(candle),
                color: candle.close >= candle.open ? 'rgba(72, 187, 120, 0.45)' : 'rgba(239, 68, 68, 0.45)',
            }))

            safeSetSeriesData(volumeSeries, volumeData, 'volume panel refresh')
        }

        const latestVolumeCandle = candleVolumeByTimeRef.current.get(latestCandleInfoRef.current?.time)
        setCursorVolume(resolveDisplayedVolume(latestVolumeCandle))
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [volumeMode])

    useEffect(() => {
        const candleSeries = candleSeriesRef.current

        if (!candleSeries) {
            return
        }

        candleSeries.applyOptions({
            priceFormat: {
                type: 'price',
                precision: safePrecision,
                minMove: Math.pow(10, -safePrecision),
            },
        })
    }, [safePrecision])

    useEffect(() => {
        if (!window.LightweightCharts || !containerRef.current) {
            return
        }

        let isCancelled = false
        const currentRunId = ++internalRunIdRef.current
        const visualIndicators = normalizedIndicators

        if (!chartRef.current) {
            const chart = window.LightweightCharts.createChart(containerRef.current, {
                width: containerRef.current.clientWidth,
                height: containerRef.current.clientHeight,
                handleScroll: {
                    mouseWheel: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                    pressedMouseMove: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                    horzTouchDrag: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                    vertTouchDrag: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                },
                handleScale: {
                    mouseWheel: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                    pinch: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                    axisPressedMouseMove: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                    axisDoubleClickReset: !(isDrawLineModeActive || Boolean(draggingDrawing)),
                },
                layout: {
                    background: { color: '#10161d' },
                    textColor: '#dfe9f5',
                    panes: {
                        separatorColor: 'rgba(168, 193, 224, 0.62)',
                        separatorHoverColor: 'rgba(196, 214, 238, 0.9)',
                    },
                },
                grid: {
                    vertLines: { color: 'rgba(104, 132, 163, 0.13)' },
                    horzLines: { color: 'rgba(104, 132, 163, 0.13)' },
                },
                rightPriceScale: {
                    borderColor: 'rgba(126, 158, 193, 0.22)',
                },
                timeScale: {
                    borderColor: 'rgba(126, 158, 193, 0.18)',
                    timeVisible: true,
                    secondsVisible: false,
                    rightOffset: scrollChartToEndOnTickIncoming ? REALTIME_RIGHT_PADDING_BARS : 0,
                },
            })

            const candleSeries = chart.addSeries(
                window.LightweightCharts.CandlestickSeries,
                {
                    priceFormat: {
                        type: 'price',
                        precision: safePrecision,
                        minMove: Math.pow(10, -safePrecision),
                    }
                },
                0
            )
            const volumeSeries = chart.addSeries(
                window.LightweightCharts.HistogramSeries,
                {
                    priceFormat: {
                        type: 'volume',
                    },
                    priceScaleId: 'volume',
                    color: 'rgba(117, 167, 255, 0.42)',
                    priceLineVisible: false,
                    lastValueVisible: false,
                    base: 0,
                    scaleMargins: {
                        top: 0.02,
                        bottom: 0,
                    },
                },
                1
            )
            chart.priceScale('volume').applyOptions({
                visible: false,
                scaleMargins: {
                    top: 0.02,
                    bottom: 0,
                },
            })

            chartRef.current = chart
            candleSeriesRef.current = candleSeries
            volumeSeriesRef.current = volumeSeries

            if (window.LightweightCharts.createSeriesMarkers) {
                tradeMarkersPrimitiveRef.current = window.LightweightCharts.createSeriesMarkers(
                    candleSeries,
                    []
                )
            }
        }

        chartRef.current?.applyOptions?.({
            timeScale: {
                rightOffset: scrollChartToEndOnTickIncoming ? REALTIME_RIGHT_PADDING_BARS : 0,
            },
        })
        if (scrollChartToEndOnTickIncoming) {
            scrollChartToLatestWithPadding(chartRef.current, loadedTimesRef.current.size || loadedCandleCountRef.current)
        }

        const chart = chartRef.current
        const candleSeries = candleSeriesRef.current
        const volumeSeries = volumeSeriesRef.current

        function isStaleRun() {
            return isCancelled || currentRunId !== internalRunIdRef.current
        }

        function toCandleData(candle) {
            const safeCandle = sanitizeCandleRow(candle)
            if (!safeCandle) {
                return null
            }

            return {
                time: safeCandle.time,
                open: safeCandle.open,
                high: safeCandle.high,
                low: safeCandle.low,
                close: safeCandle.close,
            }
        }

        function toVolumeData(candle) {
            const safeCandle = sanitizeCandleRow(candle)
            if (!safeCandle) {
                return null
            }

            return {
                time: safeCandle.time,
                value: resolveDisplayedVolume(safeCandle),
                color: safeCandle.close >= safeCandle.open ? 'rgba(72, 187, 120, 0.45)' : 'rgba(239, 68, 68, 0.45)',
            }
        }

        function buildSnapshotSeriesData(candles, contextLabel = 'chart snapshot') {
            const { rows: sanitizedRows, dropped } = buildSanitizedCandleRows(candles)
            reportSanitizedData('candles', dropped, contextLabel)

            const candleData = sanitizedRows.map((row) => ({
                time: row.time,
                open: row.open,
                high: row.high,
                low: row.low,
                close: row.close,
            }))
            const volumeData = sanitizedRows.map((row) => ({
                time: row.time,
                value: resolveDisplayedVolume(row),
                color: row.close >= row.open ? 'rgba(72, 187, 120, 0.45)' : 'rgba(239, 68, 68, 0.45)',
            }))

            return {
                sanitizedRows,
                candleData,
                volumeData,
            }
        }

        function applyLatestCandleInfo(candle) {
            if (!candle) {
                latestCandleInfoRef.current = null
                setCursorCandle(null)
                setCursorVolume(null)
                return
            }

            latestCandleInfoRef.current = candle
            setCursorCandle(candle)
            setCursorVolume(resolveDisplayedVolume(candle))
        }

        function applyLoadedCandleCount(nextCount) {
            const safeCount = Math.max(0, Number(nextCount) || 0)
            loadedCandleCountRef.current = safeCount
            setLoadedCandleCount(safeCount)
        }

        function ensureIndicatorTimeSet(columnName) {
            if (!indicatorLoadedTimesRef.current[columnName]) {
                indicatorLoadedTimesRef.current[columnName] = new Set()
            }

            return indicatorLoadedTimesRef.current[columnName]
        }

        function applyPaneHeights(activeIndicators = visualIndicators) {
            if (!chart.panes) {
                return
            }

            const panes = chart.panes()

            if (!Array.isArray(panes) || panes.length === 0) {
                return
            }

            const paneIndexMap = buildPaneIndexMap(activeIndicators)
            const separatePaneCount = Math.max(0, paneIndexMap.size - 2)

            if (panes[0]) {
                panes[0].setHeight(separatePaneCount > 0 ? 420 : 560)
            }

            if (panes[1]) {
                panes[1].setHeight(showVolumePanel ? 132 : 0)
            }

            for (let paneIndex = 2; paneIndex < panes.length; paneIndex += 1) {
                if (panes[paneIndex]) {
                    panes[paneIndex].setHeight(140)
                }
            }
        }

        function syncIndicatorSeries(frontendIndicators = []) {
            const paneIndexMap = buildPaneIndexMap(frontendIndicators)
            const desiredColumns = new Set()

            for (const indicator of frontendIndicators) {
                for (const line of indicator.lines ?? []) {
                    const target = normalizeLineTarget(line?.target)

                    if (target === 'hidden') {
                        continue
                    }

                    if (line.columnName) {
                        desiredColumns.add(line.columnName)
                    }
                }
            }

            for (const [columnName, series] of Object.entries(indicatorSeriesRef.current)) {
                if (!desiredColumns.has(columnName)) {
                    chart.removeSeries(series)
                    delete indicatorSeriesRef.current[columnName]
                    delete indicatorSeriesPaneRef.current[columnName]
                    delete indicatorLoadedTimesRef.current[columnName]
                    delete indicatorTimeBoundsRef.current[columnName]
                }
            }

            let fallbackIndex = 0

            for (const indicator of frontendIndicators) {
                for (const line of indicator.lines ?? []) {
                    const target = normalizeLineTarget(line?.target)
                    const columnName = line.columnName

                    if (target === 'hidden' || !columnName) {
                        continue
                    }

                    const paneIndex = getPaneIndexForLine(indicator, line, paneIndexMap)

                    if (paneIndex === null) {
                        continue
                    }

                    const options = getIndicatorSeriesOptions(indicator, line, fallbackIndex)
                    const currentPaneIndex = indicatorSeriesPaneRef.current[columnName]

                    if (
                        indicatorSeriesRef.current[columnName]
                        && currentPaneIndex !== undefined
                        && currentPaneIndex !== paneIndex
                    ) {
                        chart.removeSeries(indicatorSeriesRef.current[columnName])
                        delete indicatorSeriesRef.current[columnName]
                        delete indicatorLoadedTimesRef.current[columnName]
                    }

                    if (!indicatorSeriesRef.current[columnName]) {
                        indicatorSeriesRef.current[columnName] = chart.addSeries(
                            window.LightweightCharts.LineSeries,
                            options,
                            paneIndex
                        )
                        indicatorSeriesPaneRef.current[columnName] = paneIndex
                    } else {
                        indicatorSeriesRef.current[columnName].applyOptions(options)
                    }

                    ensureIndicatorTimeSet(columnName)
                    fallbackIndex += 1
                }
            }

            applyPaneHeights(frontendIndicators)
        }

        function setIndicatorPatternMarkers(indicatorRows = [], frontendIndicators = [], mode = 'replace') {
            const payload = buildIndicatorPatternMarkerPayload(indicatorRows, frontendIndicators)
            const hasMarkerLines = hasIndicatorPatternMarkerLines(frontendIndicators)

            if (
                mode === 'replace'
                && hasMarkerLines
                && payload.markers.length === 0
                && payload.candidateIds.length === 0
            ) {
                return
            }

            const nextMarkerMap = mode === 'replace'
                ? new Map()
                : new Map(indicatorPatternMarkerMapRef.current)

            for (const candidateId of payload.candidateIds || []) {
                nextMarkerMap.delete(candidateId)
            }

            for (const marker of payload.markers || []) {
                nextMarkerMap.set(marker.id, marker)
            }

            indicatorPatternMarkerMapRef.current = nextMarkerMap
            indicatorPatternMarkersRef.current = groupIndicatorPatternMarkers(
                Array.from(nextMarkerMap.values()),
            )
            setIndicatorMarkerVersion((current) => current + 1)
        }

        function removeAllIndicatorSeries() {
            for (const [columnName, series] of Object.entries(indicatorSeriesRef.current)) {
                chart.removeSeries(series)
                delete indicatorSeriesRef.current[columnName]
                delete indicatorSeriesPaneRef.current[columnName]
            }

            indicatorLoadedTimesRef.current = {}
            indicatorTimeBoundsRef.current = {}
            indicatorPatternMarkerMapRef.current = new Map()
            indicatorPatternMarkersRef.current = []
            setIndicatorMarkerVersion((current) => current + 1)
            applyPaneHeights([])
        }

        function setIndicatorData(indicatorRows, frontendIndicators) {
            syncIndicatorSeries(frontendIndicators)

            if (!Array.isArray(indicatorRows) || indicatorRows.length === 0) {
                setLatestIndicatorValues({})
                setIndicatorPatternMarkers([], frontendIndicators, 'replace')
                setChartError('')
                return
            }

            const nextIndicatorValues = {}
            const indicatorRowColumnLookup = buildIndicatorRowColumnLookup(indicatorRows)
            const lineMetaByColumn = buildIndicatorLineMetaMap(
                buildIndicatorLinesFromIndicators(frontendIndicators)
            )
            const candidateColumns = Object.keys(indicatorSeriesRef.current)

            for (const columnName of candidateColumns) {
                const lineMeta = lineMetaByColumn.get(columnName)
                const series = indicatorSeriesRef.current[columnName]
                const rowColumnName = indicatorRowColumnLookup.get(
                    normalizeIndicatorColumnVariant(columnName)
                ) || columnName

                if (!lineMeta || lineMeta.target === 'hidden' || !series) {
                    continue
                }

                const { points: data, dropped } = buildIndicatorSeriesPoints(indicatorRows, rowColumnName)
                reportSanitizedData('indicators', dropped, `indicator snapshot ${columnName}`)

                const indicatorSetApplied = safeSetSeriesData(
                    series,
                    data,
                    `indicator snapshot ${columnName}`
                )

                if (!indicatorSetApplied) {
                    continue
                }

                indicatorLoadedTimesRef.current[columnName] = new Set(data.map((point) => point.time))
                setIndicatorBoundsFromSortedData(columnName, data)

                if (data.length > 0) {
                    nextIndicatorValues[columnName] = data[data.length - 1].value
                }
            }

            setLatestIndicatorValues(nextIndicatorValues)
            setIndicatorPatternMarkers(indicatorRows, frontendIndicators, 'replace')
            setChartError('')
        }

        function updateIndicatorData(indicatorRows, frontendIndicators, indicatorColumnDetails = []) {
            syncIndicatorSeries(frontendIndicators)

            if (!Array.isArray(indicatorRows) || indicatorRows.length === 0) {
                setLatestIndicatorValues({})
                setIndicatorPatternMarkers([], frontendIndicators, 'replace')
                return
            }

            const nextIndicatorValues = { ...latestIndicatorValuesRef.current }
            const indicatorRowColumnLookup = buildIndicatorRowColumnLookup(indicatorRows)
            const lineMetaByColumn = buildIndicatorLineMetaMap(
                buildIndicatorLinesFromIndicators(frontendIndicators)
            )
            const changedColumns = buildChangedIndicatorColumnsSet(indicatorColumnDetails)
            const candidateColumns = changedColumns.size > 0
                ? Array.from(changedColumns)
                : Object.keys(indicatorSeriesRef.current)

            for (const columnName of candidateColumns) {
                const lineMeta = lineMetaByColumn.get(columnName)
                const series = indicatorSeriesRef.current[columnName]
                const rowColumnName = indicatorRowColumnLookup.get(
                    normalizeIndicatorColumnVariant(columnName)
                ) || columnName

                if (!lineMeta || lineMeta.target === 'hidden' || !series) {
                    continue
                }

                const { points: validRows, dropped } = buildIndicatorSeriesPoints(indicatorRows, rowColumnName)
                reportSanitizedData('indicators', dropped, `indicator delta ${columnName}`)

                if (validRows.length === 0) {
                    continue
                }

                const loadedTimes = ensureIndicatorTimeSet(columnName)
                const knownBounds = indicatorTimeBoundsRef.current[columnName] || { min: null, max: null }
                let effectiveMaxLoadedTime = Number.isFinite(Number(knownBounds.max))
                    ? Number(knownBounds.max)
                    : null
                if (effectiveMaxLoadedTime === null && loadedTimes.size > 0) {
                    for (const time of loadedTimes) {
                        const numericTime = Number(time)
                        if (!Number.isFinite(numericTime)) {
                            continue
                        }
                        effectiveMaxLoadedTime = effectiveMaxLoadedTime === null
                            ? numericTime
                            : Math.max(effectiveMaxLoadedTime, numericTime)
                    }
                }
                let latestAppliedValue = null

                for (const row of validRows) {
                    const alreadyLoaded = loadedTimes.has(row.time)
                    const canAppendAsLatest = effectiveMaxLoadedTime === null || row.time >= effectiveMaxLoadedTime

                    if (!alreadyLoaded && !canAppendAsLatest) {
                        reportOutOfOrderDelta('indicators', columnName)
                        continue
                    }

                    const updated = safeUpdateSeries(
                        series,
                        {
                            time: row.time,
                            value: row.value,
                        },
                        alreadyLoaded ? true : undefined,
                        alreadyLoaded
                            ? `indicator delta historical ${columnName}`
                            : `indicator delta latest ${columnName}`
                    )
                    if (!updated) {
                        continue
                    }

                    loadedTimes.add(row.time)
                    updateIndicatorBoundsWithTime(columnName, row.time)
                    effectiveMaxLoadedTime = effectiveMaxLoadedTime === null
                        ? row.time
                        : Math.max(effectiveMaxLoadedTime, row.time)
                    latestAppliedValue = row.value
                }

                if (latestAppliedValue !== null) {
                    nextIndicatorValues[columnName] = latestAppliedValue
                }
            }

            setLatestIndicatorValues(nextIndicatorValues)
            setIndicatorPatternMarkers(indicatorRows, frontendIndicators, 'merge')
            setChartError('')
        }

        function applyCandleTail(candleRows) {
            if (!Array.isArray(candleRows) || candleRows.length === 0) {
                return
            }

            const { rows: sanitizedRows, dropped } = buildSanitizedCandleRows(candleRows)
            reportSanitizedData('candles', dropped, 'chart delta')

            if (sanitizedRows.length === 0) {
                return
            }

            let shouldScrollToRealtime = false
            let effectiveMaxLoadedTime = Number.isFinite(Number(candleTimeBoundsRef.current?.max))
                ? Number(candleTimeBoundsRef.current.max)
                : null
            if (effectiveMaxLoadedTime === null && loadedTimesRef.current.size > 0) {
                for (const time of loadedTimesRef.current) {
                    const numericTime = Number(time)
                    if (!Number.isFinite(numericTime)) {
                        continue
                    }
                    effectiveMaxLoadedTime = effectiveMaxLoadedTime === null
                        ? numericTime
                        : Math.max(effectiveMaxLoadedTime, numericTime)
                }
            }

            for (let index = 0; index < sanitizedRows.length; index += 1) {
                const candle = toCandleData(sanitizedRows[index])
                if (!candle) {
                    continue
                }
                const isHistoricalUpdate = loadedTimesRef.current.has(candle.time)
                const canAppendAsLatest = effectiveMaxLoadedTime === null || candle.time >= effectiveMaxLoadedTime

                if (!isHistoricalUpdate && !canAppendAsLatest) {
                    reportOutOfOrderDelta('candles', 'candle delta')
                    continue
                }

                let appliedHistoricalUpdate = isHistoricalUpdate

                if (isHistoricalUpdate) {
                    const candleUpdated = safeUpdateSeries(candleSeries, candle, true, 'candle delta historical')
                    if (!candleUpdated) {
                        const fallbackUpdated = safeUpdateSeries(candleSeries, candle, undefined, 'candle delta fallback latest')
                        if (!fallbackUpdated) {
                            continue
                        }
                        appliedHistoricalUpdate = false
                    }
                } else {
                    const candleUpdated = safeUpdateSeries(candleSeries, candle, undefined, 'candle delta latest')
                    if (!candleUpdated) {
                        continue
                    }
                }

                loadedTimesRef.current.add(candle.time)
                updateCandleBoundsWithTime(candle.time)
                effectiveMaxLoadedTime = effectiveMaxLoadedTime === null
                    ? candle.time
                    : Math.max(effectiveMaxLoadedTime, candle.time)
                candleVolumeByTimeRef.current.set(candle.time, sanitizedRows[index])
                applyLatestCandleInfo(candle)
                const volumePoint = toVolumeData(sanitizedRows[index])
                if (volumePoint) {
                    safeUpdateSeries(
                        volumeSeries,
                        volumePoint,
                        appliedHistoricalUpdate ? true : undefined,
                        appliedHistoricalUpdate ? 'volume delta historical' : 'volume delta latest'
                    )
                }
                if (!appliedHistoricalUpdate || index === sanitizedRows.length - 1) {
                    shouldScrollToRealtime = true
                }
            }

            const allowRealtimeAutofollow = !hasCompatibleBacktestOverlay || backtestOverlayFollowUnlockedRef.current
            if (scrollChartToEndOnTickIncoming && shouldScrollToRealtime && allowRealtimeAutofollow) {
                scrollChartToLatestWithPadding(chartRef.current, loadedTimesRef.current.size || loadedCandleCountRef.current)
            }
        }

        async function applyChartSettings() {
            const response = await fetch(buildApiUrl('/chart/set-request'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({
                    symbol: chartSettings?.symbol || 'EURUSD',
                    timeframe: chartSettings?.timeframe || 'M1',
                    bars: Math.max(1, Number(chartSettings?.bars) || 1000),
                    indicators: buildBackendIndicatorsPayload(chartSettings?.indicators || []),
                }),
            })

            const data = await readJsonResponse(response)

            if (!response.ok || data.status !== 'ok') {
                throw new Error(data.error || 'Failed to set chart settings.')
            }

            historyLoadStepRef.current = Math.max(
                250,
                Number(data?.meta?.history_load_step) || historyLoadStepRef.current || 1000,
            )
            onLogEvent?.(
                `Chart request prepared for ${chartSettings?.symbol || 'EURUSD'} ${chartSettings?.timeframe || 'M1'} with lazy seed ${Number(data?.meta?.requested_bars || 0).toLocaleString()} candles.`
            )
            return data
        }

        async function waitUntilReady(options = {}) {
            const timeoutMs = Math.max(1000, Number(options?.timeoutMs) || 15000)
            const pollIntervalMs = Math.max(50, Number(options?.pollIntervalMs) || 100)
            const validateStatus = typeof options?.validateStatus === 'function' ? options.validateStatus : null
            const validationErrorMessage = String(options?.validationErrorMessage || 'Chart history did not stabilize in time.')
            const startedAt = Date.now()

            while (!isStaleRun()) {
                const response = await fetch(buildApiUrl('/chart/status'), {
                    headers: authHeaders,
                })
                const status = await readJsonResponse(response)

                if (isStaleRun()) {
                    return false
                }

                if (status.ready) {
                    if (!validateStatus || validateStatus(status)) {
                        return status
                    }
                }

                if (status.error) {
                    throw new Error(status.error)
                }

                 if (Date.now() - startedAt >= timeoutMs) {
                    throw new Error(validationErrorMessage)
                }

                await new Promise((resolve) => setTimeout(resolve, pollIntervalMs))
            }

            return false
        }

        async function loadInitialData() {
            const timeoutSeconds = getChartHistoryTimeoutSeconds(loadedCandleCountRef.current || chartSettings?.bars || 5000)
            const response = await fetch(buildApiUrl(`/chart/data?timeout=${timeoutSeconds}`), {
                headers: authHeaders,
            })
            const data = await readJsonResponse(response)

            if (isStaleRun()) {
                return
            }

            if (!response.ok) {
                throw new Error(data.error || `Failed to load chart data: ${response.status}`)
            }

            if (data.status === 'partial') {
                const { sanitizedRows, candleData, volumeData } = buildSnapshotSeriesData(
                    data.candles || [],
                    'chart partial snapshot',
                )

                const candlesApplied = safeSetSeriesData(candleSeries, candleData, 'chart partial snapshot candles')
                const volumeApplied = safeSetSeriesData(volumeSeries, volumeData, 'chart partial snapshot volume')
                if (!candlesApplied || !volumeApplied) {
                    setChartError('Chart partial snapshot could not be rendered safely.')
                    return data
                }
                candleVolumeByTimeRef.current = new Map(
                    sanitizedRows.map((candle) => [candle.time, candle])
                )
                loadedTimesRef.current = new Set(candleData.map((candle) => candle.time))
                setCandleBoundsFromSortedData(candleData)
                marketRevisionRef.current = data?.runtime?.market_runtime?.revision ?? null
                historyLoadStepRef.current = Math.max(
                    250,
                    Number(data?.meta?.history_load_step) || historyLoadStepRef.current || 1000,
                )
                applyLoadedCandleCount(candleData.length)
                applyLatestCandleInfo(candleData[candleData.length - 1] || null)
                setChartError(data.error || 'Some indicators could not be loaded.')
                return data
            }

            if (!Array.isArray(data.candles)) {
                const candlesCleared = safeSetSeriesData(candleSeries, [], 'chart empty snapshot candles')
                const volumeCleared = safeSetSeriesData(volumeSeries, [], 'chart empty snapshot volume')
                if (!candlesCleared || !volumeCleared) {
                    setChartError('Chart empty snapshot could not be rendered safely.')
                    return data
                }
                candleVolumeByTimeRef.current = new Map()
                loadedTimesRef.current = new Set()
                setCandleBoundsFromSortedData([])
                marketRevisionRef.current = null
                applyLoadedCandleCount(0)
                applyLatestCandleInfo(null)
                setLatestIndicatorValues({})
                removeAllIndicatorSeries()
                return data
            }

            const { sanitizedRows, candleData, volumeData } = buildSnapshotSeriesData(
                data.candles,
                'chart initial snapshot',
            )

            const initialCandlesApplied = safeSetSeriesData(candleSeries, candleData, 'chart initial snapshot candles')
            const initialVolumeApplied = safeSetSeriesData(volumeSeries, volumeData, 'chart initial snapshot volume')
            if (!initialCandlesApplied || !initialVolumeApplied) {
                setChartError('Chart snapshot could not be rendered safely.')
                return data
            }
            candleVolumeByTimeRef.current = new Map(
                sanitizedRows.map((candle) => [candle.time, candle])
            )
            loadedTimesRef.current = new Set(candleData.map((candle) => candle.time))
            setCandleBoundsFromSortedData(candleData)
            marketRevisionRef.current = data?.runtime?.market_runtime?.revision ?? null
            historyLoadStepRef.current = Math.max(
                250,
                Number(data?.meta?.history_load_step) || historyLoadStepRef.current || 1000,
            )
            applyLoadedCandleCount(candleData.length)
            applyLatestCandleInfo(candleData[candleData.length - 1] || null)

            if (Array.isArray(data.indicators)) {
                const effectiveIndicators = mergeAppliedIndicatorsWithVisualSettings(
                    data.applied_indicators || [],
                    visualIndicators
                )
                setIndicatorData(data.indicators, effectiveIndicators)
            } else {
                removeAllIndicatorSeries()
            }

            return data
        }

        function applyDeltaPayload(data) {
            if (historyExpansionInFlightRef.current) {
                return
            }

            marketRevisionRef.current = data?.runtime?.market_runtime?.revision ?? marketRevisionRef.current

            if (data.mode === 'no_change') {
                return
            }

            if (data.status === 'partial') {
                if (Array.isArray(data.candles) && data.candles.length > 0) {
                    if (data.mode === 'snapshot') {
                        const { sanitizedRows, candleData, volumeData } = buildSnapshotSeriesData(
                            data.candles,
                            'chart delta partial snapshot',
                        )
                        const partialCandlesApplied = safeSetSeriesData(candleSeries, candleData, 'chart delta partial snapshot candles')
                        const partialVolumeApplied = safeSetSeriesData(volumeSeries, volumeData, 'chart delta partial snapshot volume')
                        if (!partialCandlesApplied || !partialVolumeApplied) {
                            setChartError('Chart delta partial snapshot could not be rendered safely.')
                            return
                        }
                        candleVolumeByTimeRef.current = new Map(
                            sanitizedRows.map((candle) => [candle.time, candle])
                        )
                        loadedTimesRef.current = new Set(candleData.map((candle) => candle.time))
                        setCandleBoundsFromSortedData(candleData)
                        applyLoadedCandleCount(candleData.length)
                        applyLatestCandleInfo(candleData[candleData.length - 1] || null)
                    } else {
                        applyCandleTail(data.candles)
                    }
                }

                setChartError(data.error || 'Some indicators could not be updated.')
                return
            }

            const candles = data.candles

            if (!Array.isArray(candles) || candles.length === 0) {
                return
            }

            if (data.mode === 'snapshot') {
                const { sanitizedRows, candleData, volumeData } = buildSnapshotSeriesData(
                    candles,
                    'chart delta snapshot',
                )
                const deltaCandlesApplied = safeSetSeriesData(candleSeries, candleData, 'chart delta snapshot candles')
                const deltaVolumeApplied = safeSetSeriesData(volumeSeries, volumeData, 'chart delta snapshot volume')
                if (!deltaCandlesApplied || !deltaVolumeApplied) {
                    setChartError('Chart delta snapshot could not be rendered safely.')
                    return
                }
                candleVolumeByTimeRef.current = new Map(
                    sanitizedRows.map((candle) => [candle.time, candle])
                )
                loadedTimesRef.current = new Set(candleData.map((candle) => candle.time))
                setCandleBoundsFromSortedData(candleData)
                applyLoadedCandleCount(candleData.length)
                applyLatestCandleInfo(candleData[candleData.length - 1] || null)
            } else {
                applyCandleTail(candles)
            }

            if (Array.isArray(data.indicators)) {
                const effectiveIndicators = mergeAppliedIndicatorsWithVisualSettings(
                    data.applied_indicators || [],
                    visualIndicators
                )
                if (data.mode === 'snapshot') {
                    setIndicatorData(data.indicators, effectiveIndicators)
                } else {
                    updateIndicatorData(
                        data.indicators,
                        effectiveIndicators,
                        data.changed_indicator_column_details || data.indicator_column_details
                    )
                }
            }
        }

        async function pollLastData() {
            if (historyExpansionInFlightRef.current) {
                return
            }

            const params = new URLSearchParams()
            if (marketRevisionRef.current !== null && marketRevisionRef.current !== undefined) {
                params.set('since_revision', String(marketRevisionRef.current))
            }

            const response = await fetch(buildApiUrl(`/chart/delta${params.toString() ? `?${params.toString()}` : ''}`), {
                headers: authHeaders,
            })
            const data = await readJsonResponse(response)

            if (isStaleRun()) {
                return
            }

            if (!response.ok) {
                throw new Error(data.error || `Failed to poll chart data: ${response.status}`)
            }

            applyDeltaPayload(data)
        }

        async function requestLatestData() {
            if (historyExpansionInFlightRef.current) {
                return
            }

            if (marketDeltaInFlightRef.current) {
                marketDeltaQueuedRef.current = true
                return
            }

            marketDeltaInFlightRef.current = true

            try {
                await pollLastData()
            } finally {
                marketDeltaInFlightRef.current = false

                if (marketDeltaQueuedRef.current && !isStaleRun() && pollingActiveRef.current) {
                    marketDeltaQueuedRef.current = false
                    void requestLatestData()
                }
            }
        }

        async function startPollingLoop() {
            pollingActiveRef.current = true

            while (!isStaleRun() && pollingActiveRef.current) {
                try {
                    const socketConnected = marketSocketConnectedRef.current
                    const millisecondsSinceRealtimeSync = Date.now() - (lastRealtimeChartSyncAtRef.current || 0)
                    const shouldRunFallbackPoll = !socketConnected
                        || millisecondsSinceRealtimeSync >= CHART_SOCKET_STALE_AFTER_MS

                    if (shouldRunFallbackPoll) {
                        await requestLatestData()
                    }
                } catch (error) {
                    if (!isStaleRun()) {
                        console.error('Polling error:', error)
                    }
                }

                const nextDelay = marketSocketConnectedRef.current
                    ? CHART_SOCKET_RECONCILIATION_POLL_INTERVAL_MS
                    : CHART_FALLBACK_POLL_INTERVAL_MS
                await new Promise((resolve) => setTimeout(resolve, nextDelay))
            }
        }

        function stopPollingLoop() {
            pollingActiveRef.current = false
            marketDeltaQueuedRef.current = false
        }

        async function loadMoreHistoryLeft() {
            if (historyExpansionInFlightRef.current || isStaleRun()) {
                return
            }

            const chartApi = chartRef.current
            if (!chartApi) {
                return
            }

            historyExpansionInFlightRef.current = true
            const previousVisibleRange = safeGetVisibleLogicalRange(chartApi.timeScale?.())
            const previousLoadedCount = loadedCandleCountRef.current
            const previousLatestLoadedTime = getCurrentMaxLoadedCandleTime()

            try {
                onLogEvent?.(
                    `Chart reached the left edge. Loading ${historyLoadStepRef.current.toLocaleString()} more candles...`
                )

                const response = await fetch(buildApiUrl('/chart/load-more-left'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...authHeaders,
                    },
                    body: JSON.stringify({
                        extra_bars: historyLoadStepRef.current,
                    }),
                })
                const payload = await readJsonResponse(response)

                if (!response.ok || payload.status !== 'ok') {
                    throw new Error(payload.error || 'Failed to request more chart history.')
                }

                historyLoadStepRef.current = Math.max(
                    250,
                    Number(payload?.meta?.history_load_step) || Number(payload?.history_load_step) || historyLoadStepRef.current || 1000,
                )
                const expectedBars = Math.max(
                    previousLoadedCount,
                    Number(payload?.next_bars)
                    || Number(payload?.settings?.bars)
                    || Number(payload?.request?.bars)
                    || (previousLoadedCount + historyLoadStepRef.current),
                )

                const ready = await waitUntilReady({
                    timeoutMs: 20000,
                    validationErrorMessage: 'Chart history expansion returned a stale snapshot and did not stabilize in time.',
                    validateStatus: (status) => {
                        const meta = status?.meta || {}
                        const loadedCandles = Number(meta.loaded_candles || meta.loadedCandles || 0)
                        const nextLastTime = Number(meta.last_time || meta.lastTime || 0)

                        if (Number.isFinite(loadedCandles) && loadedCandles < expectedBars) {
                            return false
                        }

                        if (
                            Number.isFinite(previousLatestLoadedTime)
                            && previousLatestLoadedTime !== null
                            && previousLatestLoadedTime > 0
                            && Number.isFinite(nextLastTime)
                            && nextLastTime > 0
                            && nextLastTime < previousLatestLoadedTime
                        ) {
                            return false
                        }

                        return true
                    },
                })
                if (!ready || isStaleRun()) {
                    return
                }

                const data = await loadInitialData()
                if (isStaleRun()) {
                    return
                }

                const nextLoadedCount = Array.isArray(data?.candles) ? data.candles.length : loadedCandleCountRef.current
                const addedCandles = Math.max(0, nextLoadedCount - previousLoadedCount)

                if (previousVisibleRange && addedCandles > 0) {
                    const timeScaleApi = chartApi.timeScale?.()
                    safeSetVisibleLogicalRange(timeScaleApi, {
                        from: previousVisibleRange.from + addedCandles,
                        to: previousVisibleRange.to + addedCandles,
                    }, (error) => {
                        onLogEvent?.(
                            `Chart viewport restore skipped after history load: ${error?.message || 'invalid range'}.`,
                            'warn'
                        )
                    })
                }

                if (addedCandles > 0) {
                    onLogEvent?.(
                        `Loaded ${addedCandles.toLocaleString()} more candles for ${chartSettings?.symbol || 'EURUSD'} ${chartSettings?.timeframe || 'M1'}. Total loaded: ${nextLoadedCount.toLocaleString()}.`,
                        'success'
                    )
                } else {
                    onLogEvent?.(
                        `No older candles were added for ${chartSettings?.symbol || 'EURUSD'} ${chartSettings?.timeframe || 'M1'}.`,
                        'warn'
                    )
                }
            } catch (error) {
                if (!isStaleRun()) {
                    onLogEvent?.(error.message || 'Could not load more chart history.', 'error')
                }
                historyExpansionCooldownUntilRef.current = Date.now() + 1500
            } finally {
                historyExpansionCooldownUntilRef.current = Math.max(
                    historyExpansionCooldownUntilRef.current,
                    Date.now() + 400,
                )
                historyExpansionInFlightRef.current = false
                if (!isStaleRun()) {
                    void requestLatestData()
                }
            }
        }

        function handleResize() {
            if (!chartRef.current || !containerRef.current) {
                return
            }

            chart.applyOptions({
                width: containerRef.current.clientWidth,
                height: containerRef.current.clientHeight,
            })

            applyPaneHeights(visualIndicators)
        }

        function handleChartKeyboardShortcuts(event) {
            const targetTag = String(event.target?.tagName || '').toLowerCase()
            const isEditable = targetTag === 'input' || targetTag === 'textarea' || event.target?.isContentEditable
            if (isEditable || !chartRef.current) {
                return
            }

            const chartApi = chartRef.current
            const visibleRange = safeGetVisibleLogicalRange(chartApi.timeScale?.())
            if (!visibleRange) {
                return
            }

            const loadedCount = Math.max(0, loadedCandleCountRef.current)
            const span = Math.max(20, Math.ceil(visibleRange.to - visibleRange.from))

            if (event.key === 'Home') {
                event.preventDefault()
                safeSetVisibleLogicalRange(chartApi.timeScale?.(), { from: 0, to: span })
                return
            }

            if (event.key === 'End') {
                event.preventDefault()
                const endTo = Math.max(span, loadedCount)
                if (hasCompatibleBacktestOverlay) {
                    backtestOverlayFollowUnlockedRef.current = true
                }
                safeSetVisibleLogicalRange(chartApi.timeScale?.(), {
                    from: Math.max(0, endTo - span),
                    to: endTo,
                })
                return
            }

            if (event.key === 'PageUp') {
                event.preventDefault()
                safeSetVisibleLogicalRange(chartApi.timeScale?.(), {
                    from: Math.max(0, visibleRange.from - span),
                    to: Math.max(span, visibleRange.to - span),
                })
                return
            }

            if (event.key === 'PageDown') {
                event.preventDefault()
                const maxTo = Math.max(span, loadedCount)
                safeSetVisibleLogicalRange(chartApi.timeScale?.(), {
                    from: Math.min(Math.max(0, maxTo - span), visibleRange.from + span),
                    to: Math.min(maxTo, visibleRange.to + span),
                })
            }
        }

        function handleCrosshairMove(param) {
            if (!param?.point || !containerRef.current) {
                setCursorCandle(latestCandleInfoRef.current)
                setCursorVolume(resolveDisplayedVolume(candleVolumeByTimeRef.current.get(latestCandleInfoRef.current?.time)))
                setIndicatorValues(latestIndicatorValuesRef.current)
                return
            }

            const { x, y } = param.point

            if (
                x < 0
                || y < 0
                || x > containerRef.current.clientWidth
                || y > containerRef.current.clientHeight
            ) {
                setCursorCandle(latestCandleInfoRef.current)
                setCursorVolume(resolveDisplayedVolume(candleVolumeByTimeRef.current.get(latestCandleInfoRef.current?.time)))
                setIndicatorValues(latestIndicatorValuesRef.current)
                return
            }

            const hoveredCandle = param.seriesData?.get?.(candleSeries)
            const hoveredVolume = resolveDisplayedVolume(candleVolumeByTimeRef.current.get(param?.time))

            if (hoveredCandle?.open !== undefined) {
                setCursorCandle({
                    open: hoveredCandle.open,
                    high: hoveredCandle.high,
                    low: hoveredCandle.low,
                    close: hoveredCandle.close,
                })
                setCursorVolume(hoveredVolume)
            } else {
                setCursorCandle(latestCandleInfoRef.current)
                setCursorVolume(resolveDisplayedVolume(candleVolumeByTimeRef.current.get(latestCandleInfoRef.current?.time)))
            }

            const nextIndicatorValues = {}

            for (const [columnName, series] of Object.entries(indicatorSeriesRef.current)) {
                const hoveredValue = normalizeSeriesValue(param.seriesData?.get?.(series))

                if (hoveredValue !== null) {
                    nextIndicatorValues[columnName] = hoveredValue
                } else if (latestIndicatorValuesRef.current[columnName] !== undefined) {
                    nextIndicatorValues[columnName] = latestIndicatorValuesRef.current[columnName]
                }
            }

            setIndicatorValues(nextIndicatorValues)
        }

        function handleRealtimeMarketMessage(message) {
            const eventType = String(message?.type || '')
            const nextRevision = Number(message?.market_runtime?.revision ?? -1)
            const currentRevision = Number(marketRevisionRef.current ?? -1)
            const chartDelta = message?.chart_delta

            if (chartDelta && chartDelta.status && chartDelta.mode) {
                lastRealtimeChartSyncAtRef.current = Date.now()
                applyDeltaPayload(chartDelta)
                return
            }

            const shouldSyncImmediately =
                eventType === 'market.updated'
                || eventType === 'market.history_loaded'
                || eventType === 'market.request_changed'
                || nextRevision > currentRevision

            if (shouldSyncImmediately) {
                lastRealtimeChartSyncAtRef.current = Date.now()
                void requestLatestData()
            }
        }

        function connectRealtimeMarketSocket() {
            const query = authToken
                ? `source=chart&token=${encodeURIComponent(authToken)}`
                : 'source=chart'
            const socket = new WebSocket(buildWebSocketUrl(`/ws/market?${query}`))
            marketSocketRef.current = socket
            marketSocketConnectedRef.current = false

            socket.onmessage = (event) => {
                let message = null

                try {
                    message = JSON.parse(event.data)
                } catch (error) {
                    console.error('Invalid market websocket payload:', error)
                    return
                }

                if (message?.type === 'pong') {
                    return
                }

                handleRealtimeMarketMessage(message)
            }

            socket.onopen = () => {
                marketSocketConnectedRef.current = true
                lastRealtimeChartSyncAtRef.current = Date.now()
            }

            socket.onclose = () => {
                marketSocketConnectedRef.current = false
                if (isStaleRun()) {
                    return
                }

                marketReconnectTimerRef.current = window.setTimeout(() => {
                    connectRealtimeMarketSocket()
                }, 1000)
            }

            socket.onerror = () => {
                marketSocketConnectedRef.current = false
                socket.close()
            }
        }

        async function initialize() {
            setIsChartReady(false)
            setChartError('')
            applyLoadedCandleCount(0)
            stopPollingLoop()
            marketSocketRef.current?.close()
            marketSocketRef.current = null
            if (marketReconnectTimerRef.current) {
                window.clearTimeout(marketReconnectTimerRef.current)
                marketReconnectTimerRef.current = null
            }

            await applyChartSettings()

            if (isStaleRun()) {
                return
            }

            const ready = await waitUntilReady()

            if (!ready || isStaleRun()) {
                return
            }

            const initialData = await loadInitialData()

            if (isStaleRun()) {
                return
            }

            if (Array.isArray(initialData?.candles)) {
                onLogEvent?.(
                    `Chart loaded ${initialData.candles.length.toLocaleString()} candles for ${chartSettings?.symbol || 'EURUSD'} ${chartSettings?.timeframe || 'M1'}.`,
                    'success'
                )
            }

            applyPaneHeights(visualIndicators)
            connectRealtimeMarketSocket()
            startPollingLoop()
            setIsChartReady(true)
        }

        initialize().catch((error) => {
            if (!isStaleRun()) {
                console.error('Chart initialization error:', error)
                setChartError(error.message || 'Failed to fetch chart data.')
            }
        })

        window.addEventListener('resize', handleResize)
        window.addEventListener('keydown', handleChartKeyboardShortcuts)
        chart.subscribeCrosshairMove(handleCrosshairMove)
        const handleVisibleLogicalRangeChange = (range) => {
            if (!range || historyExpansionInFlightRef.current || isStaleRun()) {
                return
            }

            if (Date.now() < historyExpansionCooldownUntilRef.current) {
                return
            }

            const loadedCount = loadedCandleCountRef.current
            if (loadedCount <= 0) {
                return
            }

            const normalizedRange = normalizeLogicalRange(range)
            if (!normalizedRange) {
                return
            }

            if (hasCompatibleBacktestOverlay) {
                const rightEdgeThreshold = Math.max(3, Math.min(18, Math.floor(loadedCount * 0.002)))
                const isAtRightEdge = normalizedRange.to >= Math.max(0, loadedCount - rightEdgeThreshold)
                backtestOverlayFollowUnlockedRef.current = isAtRightEdge
            } else {
                backtestOverlayFollowUnlockedRef.current = false
            }

            const leftThreshold = Math.max(25, Math.min(120, Math.floor(loadedCount * 0.08)))
            if (normalizedRange.from <= leftThreshold) {
                void loadMoreHistoryLeft()
            }
        }
        chart.timeScale()?.subscribeVisibleLogicalRangeChange?.(handleVisibleLogicalRangeChange)
        const resizeObserver = typeof ResizeObserver !== 'undefined' && containerRef.current
            ? new ResizeObserver(() => {
                handleResize()
            })
            : null

        if (resizeObserver && containerRef.current) {
            resizeObserver.observe(containerRef.current)
        }

        return () => {
            isCancelled = true
            stopPollingLoop()
            if (marketReconnectTimerRef.current) {
                window.clearTimeout(marketReconnectTimerRef.current)
                marketReconnectTimerRef.current = null
            }
            if (marketSocketRef.current) {
                marketSocketRef.current.close()
                marketSocketRef.current = null
            }
            marketSocketConnectedRef.current = false
            window.removeEventListener('resize', handleResize)
            window.removeEventListener('keydown', handleChartKeyboardShortcuts)
            chart.unsubscribeCrosshairMove(handleCrosshairMove)
            chart.timeScale()?.unsubscribeVisibleLogicalRangeChange?.(handleVisibleLogicalRangeChange)
            resizeObserver?.disconnect()
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [dataRequestSignature, runId, visualIndicatorSignature])

    useEffect(() => {
        if (!(isDrawLineModeActive || Boolean(draggingDrawing))) {
            clearPendingLine()
        }
    }, [draggingDrawing, isDrawLineModeActive])

    useEffect(() => {
        const nextDrawings = Array.isArray(initialDrawings) ? initialDrawings : []
        setDrawings((current) => (current === nextDrawings ? current : nextDrawings))
    }, [initialDrawings])

    useEffect(() => {
        if (selectedDrawingId && !drawings.some((drawing) => drawing.id === selectedDrawingId)) {
            setSelectedDrawingId('')
        }
    }, [drawings, selectedDrawingId])

    useEffect(() => {
        if (lastReportedDrawingsRef.current === drawings) {
            return
        }
        lastReportedDrawingsRef.current = drawings
        onDrawingsChange?.(drawings)
    }, [drawings, onDrawingsChange])

    useEffect(() => {
        if (areIndicatorVisibilityStatesEqual(lastReportedVisibleIndicatorColumnsRef.current || {}, visibleIndicatorColumns || {})) {
            return
        }
        lastReportedVisibleIndicatorColumnsRef.current = visibleIndicatorColumns
        onVisibleIndicatorColumnsChange?.(visibleIndicatorColumns)
    }, [onVisibleIndicatorColumnsChange, visibleIndicatorColumns])

    useEffect(() => {
        clearPendingLine()
    }, [drawingTool])

    useEffect(() => {
        const candleSeries = candleSeriesRef.current

        if (!window.LightweightCharts || !candleSeries) {
            return
        }

        if (!tradeMarkersPrimitiveRef.current && window.LightweightCharts.createSeriesMarkers) {
            tradeMarkersPrimitiveRef.current = window.LightweightCharts.createSeriesMarkers(
                candleSeries,
                []
            )
        }

        const markerCandidates = [
            ...(Array.isArray(tradeMarkers) ? tradeMarkers : []),
            ...indicatorPatternMarkersRef.current,
        ]
        const loadedTimes = loadedTimesRef.current instanceof Set ? loadedTimesRef.current : new Set()
        const normalizedMarkers = []
        let droppedMarkers = 0

        for (const marker of markerCandidates) {
            const normalized = normalizeTradeMarker(marker)
            if (!normalized) {
                droppedMarkers += 1
                continue
            }
            let resolvedTime = normalized.time
            if (!loadedTimes.has(resolvedTime)) {
                const fallbackTime = (Array.isArray(normalized.candidateTimes) ? normalized.candidateTimes : []).find((candidateTime) => (
                    loadedTimes.has(candidateTime)
                ))
                if (fallbackTime !== undefined) {
                    resolvedTime = fallbackTime
                }
            }
            if (!loadedTimes.has(resolvedTime)) {
                droppedMarkers += 1
                continue
            }
            normalizedMarkers.push({
                ...normalized,
                time: resolvedTime,
            })
        }

        reportSanitizedData('markers', droppedMarkers, 'trade marker render')

        normalizedMarkers.sort((left, right) => {
            const timeDiff = Number(left?.time || 0) - Number(right?.time || 0)
            if (timeDiff !== 0) {
                return timeDiff
            }
            return String(left?.id || '').localeCompare(String(right?.id || ''))
        })

        if (tradeMarkersPrimitiveRef.current?.setMarkers) {
            try {
                tradeMarkersPrimitiveRef.current.setMarkers(normalizedMarkers)
            } catch (error) {
                onLogEvent?.(
                    `Chart marker render failed: ${error?.message || 'invalid marker payload'}.`,
                    'error'
                )
            }
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tradeMarkers, runId, loadedCandleCount, indicatorMarkerVersion])

    useEffect(() => {
        const safeMode = String(tradeMarkerMode || '').trim().toLowerCase()
        if (!['backtest', 'both'].includes(safeMode)) {
            return
        }

        if (backtestMarkerInfo?.isCompatible === false) {
            return
        }

        const markerCandidates = Array.isArray(backtestMarkerInfo?.markers) ? backtestMarkerInfo.markers : []
        if (!markerCandidates.length || !loadedCandleCount) {
            return
        }

        const firstMarkerTime = normalizeChartTime(markerCandidates[0]?.time)
        const lastMarkerTime = normalizeChartTime(markerCandidates[markerCandidates.length - 1]?.time)
        const focusKey = [
            String(backtestMarkerInfo?.runSymbol || '').trim().toUpperCase(),
            String(backtestMarkerInfo?.runTimeframe || '').trim().toUpperCase(),
            markerCandidates.length,
            firstMarkerTime ?? '',
            lastMarkerTime ?? '',
            runId,
        ].join('|')

        if (backtestViewportFocusKeyRef.current === focusKey) {
            return
        }

        const didFocus = focusChartOnMarkerRange(
            chartRef.current,
            markerCandidates,
            loadedTimesRef.current,
        )
        if (didFocus) {
            backtestOverlayFollowUnlockedRef.current = false
            backtestViewportFocusKeyRef.current = focusKey
        }
    }, [backtestMarkerInfo, loadedCandleCount, runId, tradeMarkerMode])

    useEffect(() => {
        return () => {
            if (chartRef.current) {
                chartRef.current.remove()
                chartRef.current = null
                candleSeriesRef.current = null
                tradeMarkersPrimitiveRef.current = null
                indicatorPatternMarkerMapRef.current = new Map()
                indicatorPatternMarkersRef.current = []
                indicatorSeriesRef.current = {}
                indicatorSeriesPaneRef.current = {}
                indicatorLoadedTimesRef.current = {}
                indicatorTimeBoundsRef.current = {}
                loadedTimesRef.current = new Set()
                candleTimeBoundsRef.current = { min: null, max: null }
            }
        }
    }, [])

    function handleDrawingSurfacePointerDown(event) {
        if (event.target.closest('.chartAppliedMeta')) {
            return
        }

        const dragTarget = findDrawingDragTarget(event.clientX, event.clientY)

        if (dragTarget) {
            event.preventDefault()
            event.stopPropagation()
            event.currentTarget.setPointerCapture?.(event.pointerId)
            setSelectedDrawingId(dragTarget.drawingId)
            setDraggingDrawing(dragTarget)
            return
        }

        if (!isDrawLineModeActive) {
            return
        }

        const point = getChartPointFromClientPosition(event.clientX, event.clientY)

        if (!point) {
            return
        }

        setSelectedDrawingId('')

        event.preventDefault()
        event.stopPropagation()
        event.currentTarget.setPointerCapture?.(event.pointerId)

        if (drawingTool === 'horizontal' || drawingTool === 'vertical') {
            setDrawings((current) => [
                ...current,
                {
                    id: `${drawingTool}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    type: drawingTool,
                    color: pendingLineColor,
                    start: point,
                },
            ])
            clearPendingLine()
            event.currentTarget.releasePointerCapture?.(event.pointerId)
            return
        }

        if (!pendingLineStartRef.current) {
            pendingLineStartRef.current = point
            setIsAwaitingLineEnd(true)
            setPendingLinePreview(null)
            return
        }

        pendingLinePreviewEndRef.current = point
        setPendingLinePreview(point)
    }

    function handleDrawingSurfacePointerMove(event) {
        if (draggingDrawing) {
            const point = getChartPointFromClientPosition(event.clientX, event.clientY)

            if (!point) {
                return
            }

            event.preventDefault()
            event.stopPropagation()

            setDrawings((current) => current.map((drawing) => {
                if (drawing.id !== draggingDrawing.drawingId) {
                    return drawing
                }

                if (drawing.type === 'horizontal') {
                    return {
                        ...drawing,
                        start: {
                            ...drawing.start,
                            price: point.price,
                        },
                    }
                }

                if (drawing.type === 'vertical') {
                    return {
                        ...drawing,
                        start: {
                            ...drawing.start,
                            logical: point.logical,
                        },
                    }
                }

                if (draggingDrawing.mode === 'start') {
                    return {
                        ...drawing,
                        start: point,
                    }
                }

                if (draggingDrawing.mode === 'end') {
                    return {
                        ...drawing,
                        end: point,
                    }
                }

                return drawing
            }))
            return
        }

        if (!isDrawLineModeActive || !pendingLineStartRef.current) {
            return
        }

        const point = getChartPointFromClientPosition(event.clientX, event.clientY)

        if (!point) {
            return
        }

        event.preventDefault()
        event.stopPropagation()

        pendingLinePreviewEndRef.current = point
        setPendingLinePreview(point)

        if (event.buttons === 1) {
            hasPendingLineDragRef.current = true
        }
    }

    function handleDrawingSurfacePointerUp(event) {
        if (draggingDrawing) {
            event.preventDefault()
            event.stopPropagation()
            event.currentTarget.releasePointerCapture?.(event.pointerId)
            setDraggingDrawing(null)
            return
        }

        if (!isDrawLineModeActive || !pendingLineStartRef.current) {
            return
        }

        if (drawingTool === 'horizontal' || drawingTool === 'vertical') {
            event.currentTarget.releasePointerCapture?.(event.pointerId)
            return
        }

        const point =
            getChartPointFromClientPosition(event.clientX, event.clientY)
            || pendingLinePreviewEndRef.current

        if (!point) {
            return
        }

        event.preventDefault()
        event.stopPropagation()

        const isSecondGesture = pendingLinePreviewEndRef.current !== null

        if (!hasPendingLineDragRef.current && !isSecondGesture) {
            return
        }

        const startPoint = pendingLineStartRef.current

        setDrawings((current) => {
            const next = [
                ...current,
                {
                    id: `${drawingTool}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    type: drawingTool,
                    color: pendingLineColor,
                    start: startPoint,
                    end: point,
                },
            ]
            return next
        })
        clearPendingLine()
        event.currentTarget.releasePointerCapture?.(event.pointerId)
    }

    function handleDrawingSurfacePointerLeave() {
        if (draggingDrawing) {
            return
        }

        if (!hasPendingLineDragRef.current) {
            setPendingLinePreview(null)
            pendingLinePreviewEndRef.current = null
        }
    }

    return (
        <section
            id={id}
            className={`chartShell ${isDrawLineModeActive ? 'isDrawLineMode' : ''} ${isStreamDisplayMode ? 'isStreamMode' : ''} ${usesExternalStreamMeta ? 'hasExternalStreamMeta' : ''} ${streamMetaCollapsed ? 'isStreamMetaCollapsed' : ''}`.trim()}
            onPointerDownCapture={handleDrawingSurfacePointerDown}
            onPointerMoveCapture={handleDrawingSurfacePointerMove}
            onPointerUpCapture={handleDrawingSurfacePointerUp}
            onPointerLeave={handleDrawingSurfacePointerLeave}
        >
            <div
                className={`chartAppliedMeta ${isStreamDisplayMode ? 'isStreamMode' : ''} ${usesExternalStreamMeta ? 'isExternalPlacement' : ''}`.trim()}
                aria-live='polite'
                style={{
                    '--chart-meta-font-size': `${metaFontSize}rem`,
                }}
            >
                <div className='chartAppliedMetaRow chartAppliedMetaPrimaryRow'>
                    <div className='chartAppliedMetaPrimaryGroup'>
                        {!isStreamDisplayMode ? (
                            <div className='chartAppliedMetaControls'>
                                <button
                                    type='button'
                                    className='chartAppliedMetaButton'
                                    onClick={() => handleMetaFontStep(-0.04)}
                                    aria-label='Decrease top legend font size'
                                    title='Decrease top legend font size'
                                >
                                    -
                                </button>

                                <button
                                    type='button'
                                    className='chartAppliedMetaButton'
                                    onClick={() => handleMetaFontStep(0.04)}
                                    aria-label='Increase top legend font size'
                                    title='Increase top legend font size'
                                >
                                    +
                                </button>
                            </div>
                        ) : null}

                        {!isStreamDisplayMode ? (
                            <label className='chartMarkerModeControl'>
                                <span className='chartMarkerModeLabel'>Flags</span>
                                <select
                                    className='chartMarkerModeSelect'
                                    value={tradeMarkerMode}
                                    onChange={(event) => onTradeMarkerModeChange?.(event.target.value)}
                                    aria-label='Select chart trade marker source'
                                    title='Select chart trade marker source'
                                >
                                    <option value='trader'>Trader</option>
                                    <option value='backtest'>Backtest</option>
                                    <option value='both'>Both</option>
                                </select>
                            </label>
                        ) : null}

                        {!isStreamDisplayMode && backtestMarkerStatus.totalCount > 0 ? (
                            <div className='chartAppliedBadge chartBacktestMarkerBadge'>
                                {backtestMarkerStatus.isCompatible
                                    ? (
                                        backtestMarkerStatus.hiddenCount > 0
                                            ? `Backtest ${backtestMarkerStatus.visibleCount} visible · ${backtestMarkerStatus.hiddenCount} cached`
                                            : `Backtest ${backtestMarkerStatus.visibleCount} visible`
                                    )
                                    : `Backtest cached · ${backtestMarkerStatus.runSymbol} ${backtestMarkerStatus.runTimeframe}`.trim()}
                            </div>
                        ) : null}

                        {isStreamDisplayMode && streamLeadingControls ? streamLeadingControls : null}

                        <div className={`chartAppliedBadge ${flashState.symbol ? 'isFlashing' : ''}`}>
                            {chartSettings?.symbol || 'Symbol'}
                        </div>

                        <div className={`chartAppliedBadge ${flashState.timeframe ? 'isFlashing' : ''}`}>
                            {chartSettings?.timeframe || 'Timeframe'}
                        </div>

                        {showVolumePanel && (
                            <div
                                className='chartAppliedBadge chartAppliedVolumeBadge'
                                title={`Volume (${String(volumeMode || 'volume')})`}
                            >
                                <span className='chartAppliedVolumeSign' aria-hidden='true' />
                                V {formatVolume(cursorVolume)}
                            </div>
                        )}

                        {isStreamDisplayMode ? (
                            <>
                                <div className='chartAppliedBadge chartAppliedPriceBadge isOpen'>
                                    <span className='chartAppliedPriceSign' aria-hidden='true' />
                                    O {formatPrice(cursorCandle?.open)}
                                </div>

                                <div className='chartAppliedBadge chartAppliedPriceBadge isHigh'>
                                    <span className='chartAppliedPriceSign' aria-hidden='true' />
                                    H {formatPrice(cursorCandle?.high)}
                                </div>

                                <div className='chartAppliedBadge chartAppliedPriceBadge isLow'>
                                    <span className='chartAppliedPriceSign' aria-hidden='true' />
                                    L {formatPrice(cursorCandle?.low)}
                                </div>

                                <div className='chartAppliedBadge chartAppliedPriceBadge isClose'>
                                    <span className='chartAppliedPriceSign' aria-hidden='true' />
                                    C {formatPrice(cursorCandle?.close)}
                                </div>
                            </>
                        ) : (
                            <>
                                <button
                                    type='button'
                                    className='chartAppliedBadge chartAppliedPriceBadge chartAppliedInsertButton isOpen'
                                    onClick={() => handleInsertAtStrategyCursor('open[0]')}
                                >
                                    <span className='chartAppliedPriceSign' aria-hidden='true' />
                                    O {formatPrice(cursorCandle?.open)}
                                </button>

                                <button
                                    type='button'
                                    className='chartAppliedBadge chartAppliedPriceBadge chartAppliedInsertButton isHigh'
                                    onClick={() => handleInsertAtStrategyCursor('high[0]')}
                                >
                                    <span className='chartAppliedPriceSign' aria-hidden='true' />
                                    H {formatPrice(cursorCandle?.high)}
                                </button>

                                <button
                                    type='button'
                                    className='chartAppliedBadge chartAppliedPriceBadge chartAppliedInsertButton isLow'
                                    onClick={() => handleInsertAtStrategyCursor('low[0]')}
                                >
                                    <span className='chartAppliedPriceSign' aria-hidden='true' />
                                    L {formatPrice(cursorCandle?.low)}
                                </button>

                                <button
                                    type='button'
                                    className='chartAppliedBadge chartAppliedPriceBadge chartAppliedInsertButton isClose'
                                    onClick={() => handleInsertAtStrategyCursor('close[0]')}
                                >
                                    <span className='chartAppliedPriceSign' aria-hidden='true' />
                                    C {formatPrice(cursorCandle?.close)}
                                </button>
                            </>
                        )}

                    </div>

                    {!isStreamDisplayMode && (selectedDrawing || isDrawLineModeActive) && (
                        <div className='chartAppliedBadge chartDrawingEditorBadge'>
                            <span className='chartDrawingEditorLabel'>
                                {selectedDrawing ? 'Edit line' : 'New line'}
                            </span>
                            <input
                                type='color'
                                value={selectedDrawing?.color || pendingLineColor}
                                onChange={(event) => handleDrawingEditorColorChange(event.target.value)}
                                aria-label={selectedDrawing ? 'Edit selected line color' : 'Choose new line color'}
                                title={selectedDrawing ? 'Edit selected line color' : 'Choose new line color'}
                            />
                            <button
                                type='button'
                                className='chartDrawingEditorClose'
                                onClick={handleDrawingEditorClose}
                                aria-label={selectedDrawing ? 'Delete selected line' : 'Cancel new line'}
                                title={selectedDrawing ? 'Delete selected line' : 'Cancel new line'}
                            >
                                x
                            </button>
                        </div>
                    )}
                </div>

                {!isStreamDisplayMode ? (
                    <div className='chartAppliedMetaRow chartAppliedIndicatorRow'>
                        <div className='chartIndicatorLegendControls'>
                            <div className='chartIndicatorLegendMenuShell'>
                                <button
                                    type='button'
                                    className='chartAppliedMetaButton chartLegendToggle'
                                    onClick={() => setIsIndicatorLegendMenuOpen((current) => !current)}
                                    aria-label={isIndicatorLegendMenuOpen ? 'Hide indicator legend options' : 'Show indicator legend options'}
                                    title={isIndicatorLegendMenuOpen ? 'Hide indicator legend options' : 'Show indicator legend options'}
                                >
                                    <span className={`chartLegendChevron ${isIndicatorLegendMenuOpen ? 'up' : 'down'}`} aria-hidden='true' />
                                </button>

                                {isIndicatorLegendMenuOpen && (
                                    <div className='chartIndicatorLegendMenu'>
                                        {normalizedIndicators.map((indicator) => {
                                            const indicatorLines = legendIndicatorLines.filter(
                                                (line) => line.indicatorId === indicator.id
                                            )

                                            if (indicatorLines.length === 0) {
                                                return null
                                            }

                                            const visibleCount = indicatorLines.filter(
                                                (line) => visibleIndicatorColumns[line.visibilityKey] ?? (line.target !== 'hidden')
                                            ).length
                                            const allVisible = visibleCount === indicatorLines.length
                                            const someVisible = visibleCount > 0 && visibleCount < indicatorLines.length

                                            return (
                                                <div key={indicator.id} className='chartIndicatorLegendGroup'>
                                                    <label className='chartIndicatorLegendGroupHeader'>
                                                        <input
                                                            type='checkbox'
                                                            checked={allVisible}
                                                            ref={(node) => {
                                                                if (node) {
                                                                    node.indeterminate = someVisible
                                                                }
                                                            }}
                                                            onChange={() => handleToggleIndicatorGroup(indicator)}
                                                        />
                                                        <span className='chartIndicatorLegendGroupTitle'>
                                                            {indicator.alias || indicator.name}
                                                        </span>
                                                    </label>

                                                    {indicatorLines.map((line) => (
                                                        <label
                                                            key={`${indicator.id}:${line.columnName}`}
                                                            className='chartIndicatorLegendOption'
                                                        >
                                                            <input
                                                                type='checkbox'
                                                                checked={visibleIndicatorColumns[`${indicator.id}:${line.columnName}`] ?? true}
                                                                onChange={() => handleToggleIndicatorLine(line)}
                                                            />
                                                            <span>{line.strategyTokenName}</span>
                                                        </label>
                                                    ))}
                                                </div>
                                            )
                                        })}
                                    </div>
                                )}
                            </div>

                            {indicatorLegendLeadingControls}
                        </div>

                        <div className='chartIndicatorLegendOverflowShell'>
                            <div
                                ref={indicatorLegendVisibleListRef}
                                className={`chartIndicatorLegendVisibleList ${isIndicatorLegendExpanded ? 'isExpanded' : ''}`.trim()}
                                style={{
                                    maxHeight: collapsedLegendMaxHeight,
                                }}
                            >
                                {visibleLegendLines.map((line) => (
                                    <button
                                        key={line.visibilityKey}
                                        type='button'
                                        className='chartAppliedBadge chartIndicatorValueBadge chartAppliedInsertButton'
                                        onClick={() => handleInsertAtStrategyCursor(`${line.strategyTokenName}[0]`)}
                                        data-chart-indicator-legend-item='visible'
                                    >
                                        <span
                                            className='chartIndicatorValueDot'
                                            style={{ backgroundColor: line.color || '#fff' }}
                                            aria-hidden='true'
                                        />
                                        <span className='chartIndicatorValueLabel'>{line.label}</span>
                                        <span className='chartIndicatorValueNumber'>
                                            {formatPrice(indicatorValues[line.columnName])}
                                        </span>
                                    </button>
                                ))}
                            </div>

                            {hasIndicatorLegendExpansionTarget ? (
                                <div className='chartIndicatorLegendOverflowControls'>
                                    <span className='chartIndicatorLegendOverflowHint'>
                                        {legendOverflowSummaryParts.join(' · ')}
                                    </span>
                                    <button
                                        type='button'
                                        className='chartIndicatorLegendExpandButton'
                                        onClick={() => setIsIndicatorLegendExpanded((current) => !current)}
                                    >
                                        {isIndicatorLegendExpanded ? 'Collapse legends' : 'Expand legends'}
                                    </button>
                                </div>
                            ) : null}

                            {isIndicatorLegendExpanded && hiddenLegendLines.length > 0 ? (
                                <div className='chartIndicatorLegendHiddenSection'>
                                    <div className='chartIndicatorLegendHiddenTitle'>Hidden legends</div>
                                    <div className='chartIndicatorLegendHiddenList'>
                                        {hiddenLegendLines.map((line) => (
                                            <button
                                                key={`${line.visibilityKey}:hidden`}
                                                type='button'
                                                className='chartAppliedBadge chartIndicatorValueBadge chartAppliedInsertButton isHiddenLegend'
                                                onClick={() => handleInsertAtStrategyCursor(`${line.strategyTokenName}[0]`)}
                                            >
                                                <span
                                                    className='chartIndicatorValueDot'
                                                    style={{ backgroundColor: line.color || '#fff' }}
                                                    aria-hidden='true'
                                                />
                                                <span className='chartIndicatorValueLabel'>{line.label}</span>
                                                <span className='chartIndicatorValueNumber'>
                                                    {formatPrice(indicatorValues[line.columnName])}
                                                </span>
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            ) : null}
                        </div>
                    </div>
                ) : null}
            </div>

            <div
                ref={containerRef}
                className='chartCanvas'
            />

            {guestNoticeVisible ? (
                <div className='chartGuestNotice' role='status'>
                    <div className='chartGuestNoticeText'>
                        <strong>Guest demo</strong>
                        <span>Temporary workspace. Changes are not saved.</span>
                    </div>
                    <button
                        type='button'
                        className='chartGuestNoticeClose'
                        onClick={onGuestNoticeClose}
                        aria-label='Close guest demo notice'
                        title='Close guest demo notice'
                    >
                        x
                    </button>
                </div>
            ) : null}

            <svg
                className='chartDrawingOverlay'
                width='100%'
                height='100%'
                preserveAspectRatio='none'
                aria-hidden='true'
            >
                {drawings.map((drawing) => {
                    const coordinates = getDrawingCoordinates(drawing)

                    if (!coordinates) {
                        return null
                    }

                    return (
                        <g key={drawing.id} className={`chartDrawingShape ${selectedDrawingId === drawing.id ? 'isSelected' : ''}`} style={{ '--drawing-color': drawing.color || '#d9d9d9' }}>
                            <line
                                x1={coordinates.x1}
                                y1={coordinates.y1}
                                x2={coordinates.x2}
                                y2={coordinates.y2}
                            />

                            {(drawing.type === 'segment' || drawing.type === 'ray') && (
                                <>
                                    <circle cx={coordinates.startX} cy={coordinates.startY} r='3.5' />
                                    <circle cx={coordinates.endX} cy={coordinates.endY} r='3.5' />
                                </>
                            )}
                        </g>
                    )
                })}

                {isAwaitingLineEnd && pendingLineStartRef.current && (() => {
                    const previewEnd = pendingLinePreview || pendingLineStartRef.current
                    const pendingCoordinates = getDrawingCoordinates({
                        type: 'segment',
                        start: pendingLineStartRef.current,
                        end: previewEnd,
                    })

                    if (!pendingCoordinates) {
                        return null
                    }

                    return (
                        <g className='chartDrawingShape isPending' style={{ '--drawing-color': pendingLineColor }}>
                            <line
                                x1={pendingCoordinates.x1}
                                y1={pendingCoordinates.y1}
                                x2={pendingCoordinates.x2}
                                y2={pendingCoordinates.y2}
                            />
                            <circle cx={pendingCoordinates.startX} cy={pendingCoordinates.startY} r='3.5' />
                        </g>
                    )
                })()}
            </svg>

            {chartError && (
                <div className='chartErrorBadge'>{chartError}</div>
            )}
        </section>
    )
}

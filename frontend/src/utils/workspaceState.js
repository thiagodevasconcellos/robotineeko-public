export const DEFAULT_SHARED_WORKSPACE_UI_STATE = {
    chart: {
        metaFontSize: 0.84,
        pendingLineColor: '#d9d9d9',
        scrollChartToEndOnTickIncoming: true,
        showVolumePanel: true,
        volumeMode: 'volume',
        tradeMarkerMode: 'trader',
    },
    consoleJobs: {
        backtest: null,
        batch: null,
        presetCompare: null,
        timeframeStudy: null,
        symbolStudy: null,
        walkforwardStudy: null,
    },
}

const CONSOLE_JOB_MAX_AGE_MS = {
    backtest: 15 * 60 * 1000,
    batch: 60 * 60 * 1000,
    presetCompare: 15 * 60 * 1000,
    timeframeStudy: 20 * 60 * 1000,
    symbolStudy: 20 * 60 * 1000,
    walkforwardStudy: 25 * 60 * 1000,
}

export const DEFAULT_LOCAL_CONSOLE_UI_STATE = {
    activeTerminal: 'Strategy',
    isMinimized: false,
    height: 450,
}

export const DEFAULT_LOCAL_DRAWING_UI_STATE = {
    isActive: false,
    tool: 'segment',
}

export const DEFAULT_SHARED_BATCH_STATE = {
    features: [],
    jobs: [],
    options: {
        barsOverride: null,
        researchMode: 'none',
        studyWindowsCsv: '',
        studyTimeframesCsv: '',
        studySymbolsCsv: '',
        walkforwardTrainBars: '',
        walkforwardTestBars: '',
        comparisonPresetSelectionMap: {},
        activeTemplateId: '',
    },
}

// Only persistent, cross-device editing state belongs in the shared workspace payload.
// Transient navigation state such as scroll, viewport, open menus, and tool focus should stay local.
export const SHARED_WORKSPACE_STATE_KEYS = [
    'chartSettings',
    'chartBacktestOverlay',
    'strategy',
    'backtestStrategySet',
    'backtest',
    'trade',
    'batch',
    'research',
    'drawings',
    'visibleIndicatorColumns',
    'strategyResponse',
    'backtestRunResponse',
    'backtestChartBuffer',
    'uiState',
]

export function normalizeSharedWorkspaceUiState(payload) {
    const rawVolumeMode = String(payload?.chart?.volumeMode || '').trim().toLowerCase()
    const normalizedVolumeMode = rawVolumeMode === 'tick'
        ? 'tick_volume'
        : rawVolumeMode === 'real'
            ? 'real_volume'
            : ['volume', 'tick_volume', 'real_volume'].includes(rawVolumeMode)
                ? rawVolumeMode
                : DEFAULT_SHARED_WORKSPACE_UI_STATE.chart.volumeMode

    const normalizeJobEntry = (entry, jobKey) => {
        if (!entry || typeof entry !== 'object') {
            return null
        }

        const status = String(entry.status || '').trim().toLowerCase()
        if (status !== 'running') {
            return null
        }

        const startedAtMs = Date.parse(String(entry.startedAt || ''))
        const maxAgeMs = CONSOLE_JOB_MAX_AGE_MS[jobKey] || (15 * 60 * 1000)
        if (Number.isFinite(startedAtMs) && startedAtMs > 0) {
            const elapsedMs = Math.max(0, Date.now() - startedAtMs)
            if (elapsedMs > maxAgeMs) {
                return null
            }
        }

        return {
            status: 'running',
            label: String(entry.label || '').trim(),
            startedAt: String(entry.startedAt || '').trim(),
            side: String(entry.side || '').trim(),
            actor: String(entry.actor || '').trim(),
            jobId: String(entry.jobId || '').trim(),
        }
    }

    return {
        chart: {
            ...DEFAULT_SHARED_WORKSPACE_UI_STATE.chart,
            ...(payload?.chart || {}),
            volumeMode: normalizedVolumeMode,
            tradeMarkerMode: ['trader', 'backtest', 'both'].includes(String(payload?.chart?.tradeMarkerMode || '').trim().toLowerCase())
                ? String(payload.chart.tradeMarkerMode).trim().toLowerCase()
                : DEFAULT_SHARED_WORKSPACE_UI_STATE.chart.tradeMarkerMode,
        },
        consoleJobs: {
            ...DEFAULT_SHARED_WORKSPACE_UI_STATE.consoleJobs,
            backtest: normalizeJobEntry(payload?.consoleJobs?.backtest, 'backtest'),
            batch: normalizeJobEntry(payload?.consoleJobs?.batch, 'batch'),
            presetCompare: normalizeJobEntry(payload?.consoleJobs?.presetCompare, 'presetCompare'),
            timeframeStudy: normalizeJobEntry(payload?.consoleJobs?.timeframeStudy, 'timeframeStudy'),
            symbolStudy: normalizeJobEntry(payload?.consoleJobs?.symbolStudy, 'symbolStudy'),
            walkforwardStudy: normalizeJobEntry(payload?.consoleJobs?.walkforwardStudy, 'walkforwardStudy'),
        },
    }
}

export function hasStaleSharedConsoleJobs(payload) {
    const consoleJobs = payload?.consoleJobs
    if (!consoleJobs || typeof consoleJobs !== 'object') {
        return false
    }

    return Object.entries(CONSOLE_JOB_MAX_AGE_MS).some(([jobKey, maxAgeMs]) => {
        const entry = consoleJobs?.[jobKey]
        if (!entry || typeof entry !== 'object') {
            return false
        }

        const status = String(entry.status || '').trim().toLowerCase()
        if (status !== 'running') {
            return false
        }

        const startedAtMs = Date.parse(String(entry.startedAt || ''))
        if (!Number.isFinite(startedAtMs) || startedAtMs <= 0) {
            return false
        }

        return (Date.now() - startedAtMs) > maxAgeMs
    })
}

export function sanitizeSharedBatchState(payload) {
    const options = payload?.options && typeof payload.options === 'object'
        ? payload.options
        : {}

    return {
        features: [],
        jobs: [],
        options: {
            ...DEFAULT_SHARED_BATCH_STATE.options,
            barsOverride: options?.barsOverride ?? DEFAULT_SHARED_BATCH_STATE.options.barsOverride,
            researchMode: String(options?.researchMode || DEFAULT_SHARED_BATCH_STATE.options.researchMode).trim() || DEFAULT_SHARED_BATCH_STATE.options.researchMode,
            studyWindowsCsv: String(options?.studyWindowsCsv || '').trim(),
            studyTimeframesCsv: String(options?.studyTimeframesCsv || '').trim(),
            studySymbolsCsv: String(options?.studySymbolsCsv || '').trim(),
            walkforwardTrainBars: String(options?.walkforwardTrainBars || '').trim(),
            walkforwardTestBars: String(options?.walkforwardTestBars || '').trim(),
            comparisonPresetSelectionMap: options?.comparisonPresetSelectionMap && typeof options.comparisonPresetSelectionMap === 'object'
                ? options.comparisonPresetSelectionMap
                : {},
            activeTemplateId: String(options?.activeTemplateId || '').trim(),
        },
    }
}

export function pickSharedWorkspaceState(state) {
    return {
        chartSettings: state?.chartSettings ?? null,
        chartBacktestOverlay: state?.chartBacktestOverlay ?? null,
        strategy: state?.strategy ?? null,
        backtestStrategySet: Array.isArray(state?.backtestStrategySet) ? state.backtestStrategySet : [],
        backtest: state?.backtest ?? null,
        trade: state?.trade && typeof state.trade === 'object'
            ? state.trade
            : null,
        batch: state?.batch && typeof state.batch === 'object'
            ? sanitizeSharedBatchState(state.batch)
            : null,
        research: state?.research && typeof state.research === 'object'
            ? state.research
            : null,
        drawings: Array.isArray(state?.drawings) ? state.drawings : [],
        visibleIndicatorColumns: state?.visibleIndicatorColumns && typeof state.visibleIndicatorColumns === 'object'
            ? state.visibleIndicatorColumns
            : {},
        strategyResponse: state?.strategyResponse ?? null,
        backtestRunResponse: state?.backtestRunResponse ?? null,
        backtestChartBuffer: state?.backtestChartBuffer ?? null,
        uiState: normalizeSharedWorkspaceUiState(state?.uiState),
    }
}

export function buildSharedWorkspacePatch(previousState, nextState) {
    const patch = {}

    for (const key of SHARED_WORKSPACE_STATE_KEYS) {
        const previousSerialized = JSON.stringify(previousState?.[key] ?? null)
        const nextSerialized = JSON.stringify(nextState?.[key] ?? null)

        if (previousSerialized !== nextSerialized) {
            patch[key] = nextState[key]
        }
    }

    return patch
}

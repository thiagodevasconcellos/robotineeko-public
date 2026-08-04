import { useCallback, useEffect, useMemo, useState } from 'react'
import { buildApiUrl, readJsonResponse } from '/src/api'
import { ChartStatus } from './ChartStatus'
import { StrategyRuntime } from './StrategyRuntime'
import { ResultsRuntime } from './ResultsRuntime'
import { NeuralRuntime } from './NeuralRuntime'
import './Runtime.css'

const RUNTIME_TABS = [
    { id: 'Chart', label: 'Chart' },
    { id: 'Strategy', label: 'Strategy' },
    { id: 'Results', label: 'Results' },
    { id: 'Neural', label: 'Neural' },
]

function formatValue(value) {
    if (value === null || value === undefined || value === '') {
        return '--'
    }

    if (typeof value === 'boolean') {
        return value ? 'yes' : 'no'
    }

    if (typeof value === 'number' && Number.isFinite(value)) {
        return value.toLocaleString('en-US', {
            maximumFractionDigits: 4,
        })
    }

    return String(value)
}

function formatRelativeTimestamp(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return '--'
    }

    const deltaSeconds = Math.max(0, Math.round(Date.now() / 1000 - numeric))
    if (deltaSeconds < 60) {
        return `${deltaSeconds}s ago`
    }
    if (deltaSeconds < 3600) {
        return `${Math.floor(deltaSeconds / 60)}m ago`
    }
    return `${Math.floor(deltaSeconds / 3600)}h ago`
}

function formatDurationDelta(startValue, endValue) {
    const start = Number(startValue)
    const end = Number(endValue)
    if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0 || end <= 0 || end < start) {
        return '--'
    }

    const delta = end - start
    if (delta < 1) {
        return `${(delta * 1000).toFixed(0)} ms`
    }
    if (delta < 60) {
        return `${delta.toFixed(2)} s`
    }
    return `${Math.floor(delta / 60)}m ${(delta % 60).toFixed(0)}s`
}

function buildRuntimeTone(source = {}) {
    if (source.error) {
        return 'error'
    }
    if (source.warn) {
        return 'warn'
    }
    if (source.ok) {
        return 'ok'
    }
    return 'idle'
}

function RuntimeChip({ label, tone = 'idle', detail }) {
    return (
        <div className={`runtimeTelemetryChip is-${tone}`}>
            <div className='runtimeTelemetryChipLabel'>{label}</div>
            <div className='runtimeTelemetryChipDetail'>{detail}</div>
        </div>
    )
}

function buildPropagationTimeline({ chart, strategy, results, neural }) {
    const chartRuntime = chart.runtime || {}
    const marketRuntime = chartRuntime.market_runtime || {}
    const latestNeuralEvent = Array.isArray(neural.recent_events) ? neural.recent_events[0] : null

    return [
        {
            label: 'Market update',
            at: marketRuntime.last_update_at,
            detail: `rev ${formatValue(marketRuntime.revision)} · event ${formatValue(marketRuntime.last_event)}`,
        },
        {
            label: 'Chart snapshot',
            at: chartRuntime.snapshot_built_at,
            detail: `mode ${formatValue(chartRuntime.snapshot_refresh_mode)} · ctx rev ${formatValue(chartRuntime.snapshot_signature?.market_context_revision)}`,
        },
        {
            label: 'Strategy apply',
            at: strategy.last_applied_at,
            detail: `refresh ${formatValue(strategy.last_refresh_mode)} · rows ${formatValue(strategy.performance?.last_rows)}`,
        },
        {
            label: 'Results materialized',
            at: results.last_results_generated_at || results.last_applied_at,
            detail: `rows ${formatValue(results.rows)} · trades ${formatValue(results.stats?.n_trades)}`,
        },
        {
            label: 'Neural activity',
            at: latestNeuralEvent?.at || neural.last_run_at,
            detail: latestNeuralEvent
                ? `${formatValue(latestNeuralEvent.kind)} · ${formatValue(latestNeuralEvent.network_id)}`
                : `active jobs ${formatValue(Object.keys(neural.active_jobs || {}).length)}`,
        },
    ]
}

function buildLagMetrics({ chart, strategy, results, neural }) {
    const chartBuiltAt = chart.runtime?.snapshot_built_at
    const strategyAppliedAt = strategy.last_applied_at
    const resultsGeneratedAt = results.last_results_generated_at || results.last_applied_at
    const latestNeuralEvent = Array.isArray(neural.recent_events) ? neural.recent_events[0] : null
    const neuralAt = latestNeuralEvent?.at || neural.last_run_at
    const marketAt = chart.runtime?.market_runtime?.last_update_at

    return [
        {
            label: 'Market -> Chart',
            detail: formatDurationDelta(marketAt, chartBuiltAt),
        },
        {
            label: 'Chart -> Strategy',
            detail: formatDurationDelta(chartBuiltAt, strategyAppliedAt),
        },
        {
            label: 'Strategy -> Results',
            detail: formatDurationDelta(strategyAppliedAt, resultsGeneratedAt),
        },
        {
            label: 'Results -> Neural',
            detail: formatDurationDelta(resultsGeneratedAt, neuralAt),
        },
    ]
}

function buildCausalChainItems({ chart, strategy, results, neural }) {
    const chartReady = Boolean(chart.ready)
    const strategyReady = Boolean(strategy.has_strategy)
    const strategyHasResults = Boolean(strategy.has_results)
    const resultsReady = Number(results.rows || 0) > 0 || Boolean(results.has_results)
    const neuralActiveJobs = Object.keys(neural.active_jobs || {}).length
    const strategyRows = Number(strategy.performance?.last_rows || 0)
    const resultRows = Number(results.rows || 0)
    const chartSignature = chart.runtime?.snapshot_signature || {}
    const chartContextRevision = chartSignature.market_context_revision
    const chartCacheKey = chartSignature.market_context_key
    const strategyViewMeta = strategy.strategy_view_meta || {}
    const strategyContextRevision = strategyViewMeta.snapshot_market_context_revision
    const strategyCacheKey = strategyViewMeta.snapshot_cache_key
    const activeNeuralJob = Object.values(neural.active_jobs || {})[0] || null
    const neuralSnapshot = activeNeuralJob?.market_snapshot || {}
    const neuralCacheKey = neuralSnapshot.cache_key || activeNeuralJob?.cache_key || null

    return [
        {
            label: 'Chart -> Strategy',
            tone: buildRuntimeTone({
                error: chartReady && strategyReady && chartContextRevision !== undefined && strategyContextRevision !== undefined && chartContextRevision !== strategyContextRevision,
                warn: (!chartReady && strategyReady) || (chartReady && strategyReady && !strategyHasResults),
                ok: chartReady && strategyReady && (
                    chartContextRevision === undefined
                    || strategyContextRevision === undefined
                    || chartContextRevision === strategyContextRevision
                ),
            }),
            detail: chartReady
                ? (
                    strategyReady
                        ? `rev ${formatValue(chartContextRevision)} -> ${formatValue(strategyContextRevision)} · cache ${formatValue(strategyCacheKey || chartCacheKey)}`
                        : 'Chart is ready, but strategy is not configured yet.'
                )
                : 'Chart is not ready, so strategy cannot stay fully aligned.',
        },
        {
            label: 'Strategy -> Results',
            tone: buildRuntimeTone({
                error: strategyHasResults && !resultsReady,
                warn: strategyReady && resultsReady && strategyRows !== resultRows,
                ok: strategyHasResults && resultsReady && (strategyRows === resultRows || strategyRows === 0 || resultRows === 0),
            }),
            detail: strategyHasResults && resultsReady
                ? `Rows strategy/results: ${formatValue(strategyRows)} / ${formatValue(resultRows)}`
                : 'Results are not fully materialized from the current strategy runtime yet.',
        },
        {
            label: 'Results -> Neural',
            tone: buildRuntimeTone({
                error: neural.last_error || (resultsReady && neuralActiveJobs > 0 && chartCacheKey && neuralCacheKey && chartCacheKey !== neuralCacheKey),
                warn: resultsReady && neuralActiveJobs > 0,
                ok: resultsReady && !neural.last_error && (!chartCacheKey || !neuralCacheKey || chartCacheKey === neuralCacheKey),
            }),
            detail: neuralActiveJobs > 0
                ? `Neural has ${formatValue(neuralActiveJobs)} active jobs · cache ${formatValue(neuralCacheKey || '--')}`
                : (resultsReady ? 'Results are ready and neural is idle.' : 'No active downstream neural consumer right now.'),
        },
    ]
}

function buildRuntimeAlerts({ chart, strategy, results, neural }) {
    const alerts = []
    const chartSignature = chart.runtime?.snapshot_signature || {}
    const strategyViewMeta = strategy.strategy_view_meta || {}
    const chartContextRevision = chartSignature.market_context_revision
    const strategyContextRevision = strategyViewMeta.snapshot_market_context_revision
    const chartCacheKey = chartSignature.market_context_key
    const strategyRows = Number(strategy.performance?.last_rows || 0)
    const resultRows = Number(results.rows || 0)
    const activeNeuralJob = Object.values(neural.active_jobs || {})[0] || null
    const neuralSnapshot = activeNeuralJob?.market_snapshot || {}
    const neuralCacheKey = neuralSnapshot.cache_key || activeNeuralJob?.cache_key || null

    if (chart.ready && strategy.has_strategy && chartContextRevision !== undefined && strategyContextRevision !== undefined && chartContextRevision !== strategyContextRevision) {
        alerts.push({
            tone: 'error',
            title: 'Strategy is behind chart snapshot',
            detail: `Chart revision ${formatValue(chartContextRevision)} differs from strategy revision ${formatValue(strategyContextRevision)}.`,
        })
    }

    if (strategy.has_results && resultRows > 0 && strategyRows > 0 && strategyRows !== resultRows) {
        alerts.push({
            tone: 'warn',
            title: 'Results rows diverge from strategy rows',
            detail: `Strategy rows ${formatValue(strategyRows)} vs results rows ${formatValue(resultRows)}.`,
        })
    }

    if (Object.keys(neural.active_jobs || {}).length > 0 && chartCacheKey && neuralCacheKey && chartCacheKey !== neuralCacheKey) {
        alerts.push({
            tone: 'warn',
            title: 'Neural cache context differs from chart context',
            detail: `Chart cache ${formatValue(chartCacheKey)} vs neural cache ${formatValue(neuralCacheKey)}.`,
        })
    }

    const lagMetrics = buildLagMetrics({ chart, strategy, results, neural })
    for (const item of lagMetrics) {
        const detail = String(item.detail || '')
        if (detail === '--') {
            continue
        }
        const secondsMatch = detail.match(/([\d.]+)\s*s/)
        const msMatch = detail.match(/([\d.]+)\s*ms/)
        const lagSeconds = secondsMatch ? Number(secondsMatch[1]) : (msMatch ? Number(msMatch[1]) / 1000 : null)
        if (lagSeconds !== null && lagSeconds > 5) {
            alerts.push({
                tone: 'warn',
                title: 'Propagation lag exceeds threshold',
                detail: `${item.label} is currently ${detail}.`,
            })
        }
    }

    return alerts.slice(0, 4)
}

export function Runtime({
    authToken = '',
    isActive = false,
}) {
    const [activeTab, setActiveTab] = useState('Chart')
    const [isTelemetryCollapsed, setIsTelemetryCollapsed] = useState(false)
    const [telemetryState, setTelemetryState] = useState({
        loading: false,
        error: '',
        payload: null,
        refreshedAt: null,
    })

    const fetchTelemetry = useCallback(async ({ silent = false } = {}) => {
        if (!authToken) {
            return
        }

        if (!silent) {
            setTelemetryState((current) => ({
                ...current,
                loading: true,
                error: '',
            }))
        }

        try {
            const headers = {
                'Authorization': `Bearer ${authToken}`,
            }

            const [chartResponse, strategyResponse, resultsResponse, neuralResponse] = await Promise.all([
                fetch(buildApiUrl('/chart/status'), { headers }),
                fetch(buildApiUrl('/strategy/status'), { headers }),
                fetch(buildApiUrl('/strategy/results'), { headers }),
                fetch(buildApiUrl('/neural/runtime'), { headers }),
            ])

            const [chartData, strategyData, resultsData, neuralData] = await Promise.all([
                readJsonResponse(chartResponse),
                readJsonResponse(strategyResponse),
                readJsonResponse(resultsResponse),
                readJsonResponse(neuralResponse),
            ])

            setTelemetryState({
                loading: false,
                error: '',
                payload: {
                    chart: chartData,
                    strategy: strategyData,
                    results: resultsData,
                    neural: neuralData,
                },
                refreshedAt: Date.now(),
            })
        } catch (error) {
            setTelemetryState((current) => ({
                ...current,
                loading: false,
                error: error?.message || 'Failed to load runtime telemetry.',
            }))
        }
    }, [authToken])

    useEffect(() => {
        if (!isActive || !authToken) {
            return undefined
        }

        void fetchTelemetry()
        const intervalId = window.setInterval(() => {
            void fetchTelemetry({ silent: true })
        }, 3000)

        return () => window.clearInterval(intervalId)
    }, [authToken, fetchTelemetry, isActive])

    const telemetry = useMemo(() => telemetryState.payload || {}, [telemetryState.payload])
    const chart = useMemo(() => telemetry.chart || {}, [telemetry.chart])
    const strategy = useMemo(() => telemetry.strategy || {}, [telemetry.strategy])
    const results = useMemo(() => telemetry.results || {}, [telemetry.results])
    const neural = useMemo(
        () => telemetry.neural?.runtime || telemetry.neural || {},
        [telemetry.neural],
    )

    const chartTone = buildRuntimeTone({
        error: chart.error,
        warn: chart.loading && !chart.ready,
        ok: chart.ready,
    })
    const strategyTone = buildRuntimeTone({
        error: strategy.error,
        warn: strategy.is_stale || !strategy.has_results,
        ok: strategy.has_results || strategy.has_strategy,
    })
    const resultsTone = buildRuntimeTone({
        error: results.error,
        warn: results.status === 'empty' || !results.has_results,
        ok: results.rows > 0 || results.has_results,
    })
    const neuralTone = buildRuntimeTone({
        error: neural.last_error,
        warn: Object.keys(neural.active_jobs || {}).length > 0,
        ok: neural.last_run_at,
    })

    const crossTelemetryText = useMemo(() => {
        const chartRows = chart.meta?.loaded_candles ?? '--'
        const strategyRows = strategy.performance?.last_rows ?? '--'
        const resultRows = results.rows ?? '--'
        const neuralJobs = Object.keys(neural.active_jobs || {}).length
        return `Chart ${formatValue(chartRows)} rows -> Strategy ${formatValue(strategyRows)} rows -> Results ${formatValue(resultRows)} rows -> Neural ${formatValue(neuralJobs)} active jobs`
    }, [chart.meta?.loaded_candles, strategy.performance?.last_rows, results.rows, neural.active_jobs])
    const causalChainItems = useMemo(
        () => buildCausalChainItems({ chart, strategy, results, neural }),
        [chart, strategy, results, neural]
    )
    const propagationTimeline = useMemo(
        () => buildPropagationTimeline({ chart, strategy, results, neural }),
        [chart, strategy, results, neural]
    )
    const lagMetrics = useMemo(
        () => buildLagMetrics({ chart, strategy, results, neural }),
        [chart, strategy, results, neural]
    )
    const runtimeAlerts = useMemo(
        () => buildRuntimeAlerts({ chart, strategy, results, neural }),
        [chart, strategy, results, neural]
    )
    const tabTones = {
        Chart: chartTone,
        Strategy: strategyTone,
        Results: resultsTone,
        Neural: neuralTone,
    }

    return (
        <div className='RuntimeConsole'>
            <div className='runtimePanelToolbar'>
                <div className='runtimePanelTabs'>
                    {RUNTIME_TABS.map((tab) => (
                        <button
                            key={tab.id}
                            type='button'
                            className={`runtimePanelTab ${activeTab === tab.id ? 'active' : ''}`}
                            onClick={() => setActiveTab(tab.id)}
                        >
                            <span className={`runtimePanelTabDot is-${tabTones[tab.id] || 'idle'}`} aria-hidden='true' />
                            <span>{tab.label}</span>
                        </button>
                    ))}
                </div>

                <div className='runtimeActions'>
                    <button
                        type='button'
                        className='runtimeToolbarButton'
                        onClick={() => void fetchTelemetry()}
                        disabled={!authToken || telemetryState.loading}
                    >
                        {telemetryState.loading ? 'Refreshing...' : 'Refresh telemetry'}
                    </button>
                </div>
            </div>

            <div className='runtimeTelemetry'>
                <div className='runtimeTelemetryHeader'>
                    <div className='runtimeTelemetryTitle'>Cross-runtime telemetry</div>
                    <div className='runtimeTelemetryHeaderActions'>
                        <div className='runtimeTelemetryMeta'>
                            Last refresh: {telemetryState.refreshedAt ? new Date(telemetryState.refreshedAt).toLocaleTimeString() : '--'}
                        </div>
                        <button
                            type='button'
                            className='runtimeTelemetryToggle'
                            onClick={() => setIsTelemetryCollapsed((current) => !current)}
                        >
                            {isTelemetryCollapsed ? 'Expand' : 'Collapse'}
                        </button>
                    </div>
                </div>
                {telemetryState.error ? (
                    <div className='runtimeTelemetryError'>{telemetryState.error}</div>
                ) : !isTelemetryCollapsed ? (
                    <>
                        <div className='runtimeTelemetrySummary'>{crossTelemetryText}</div>
                        {runtimeAlerts.length ? (
                            <div className='runtimeTelemetryAlerts'>
                                {runtimeAlerts.map((alert, index) => (
                                    <div key={`${alert.title}-${index}`} className={`runtimeTelemetryAlert is-${alert.tone}`}>
                                        <div className='runtimeTelemetryAlertTitle'>{alert.title}</div>
                                        <div className='runtimeTelemetryAlertDetail'>{alert.detail}</div>
                                    </div>
                                ))}
                            </div>
                        ) : null}
                        <div className='runtimeTelemetryGrid'>
                            <RuntimeChip label='Chart' tone={chartTone} detail={`ready ${formatValue(chart.ready)} · rows ${formatValue(chart.meta?.loaded_candles)}`} />
                            <RuntimeChip label='Strategy' tone={strategyTone} detail={`results ${formatValue(strategy.has_results)} · refresh ${formatValue(strategy.last_refresh_mode)}`} />
                            <RuntimeChip label='Results' tone={resultsTone} detail={`rows ${formatValue(results.rows)} · status ${formatValue(results.status)}`} />
                            <RuntimeChip label='Neural' tone={neuralTone} detail={`active jobs ${formatValue(Object.keys(neural.active_jobs || {}).length)} · last run ${formatValue(neural.last_run_at ? 'yes' : 'no')}`} />
                        </div>
                        <div className='runtimeTelemetryChain'>
                            {causalChainItems.map((item) => (
                                <RuntimeChip
                                    key={item.label}
                                    label={item.label}
                                    tone={item.tone}
                                    detail={item.detail}
                                />
                            ))}
                        </div>
                        <div className='runtimeTelemetryLagGrid'>
                            {lagMetrics.map((item) => (
                                <div key={item.label} className='runtimeLagItem'>
                                    <div className='runtimeLagLabel'>{item.label}</div>
                                    <div className='runtimeLagValue'>{item.detail}</div>
                                </div>
                            ))}
                        </div>
                        <div className='runtimeTelemetryTimeline'>
                            {propagationTimeline.map((item) => (
                                <div key={item.label} className='runtimeTimelineItem'>
                                    <div className='runtimeTimelineLabel'>{item.label}</div>
                                    <div className='runtimeTimelineTime'>{formatRelativeTimestamp(item.at)}</div>
                                    <div className='runtimeTimelineDetail'>{item.detail}</div>
                                </div>
                            ))}
                        </div>
                    </>
                ) : (
                    <div className='runtimeTelemetrySummary'>
                        {crossTelemetryText}
                    </div>
                )}
            </div>

            <div className='runtimePanelBody'>
                <div className={`runtimePanelView ${activeTab === 'Chart' ? 'active' : ''}`} hidden={activeTab !== 'Chart'}>
                    <ChartStatus authToken={authToken} isActive={isActive && activeTab === 'Chart'} />
                </div>
                <div className={`runtimePanelView ${activeTab === 'Strategy' ? 'active' : ''}`} hidden={activeTab !== 'Strategy'}>
                    <StrategyRuntime authToken={authToken} isActive={isActive && activeTab === 'Strategy'} />
                </div>
                <div className={`runtimePanelView ${activeTab === 'Results' ? 'active' : ''}`} hidden={activeTab !== 'Results'}>
                    <ResultsRuntime authToken={authToken} isActive={isActive && activeTab === 'Results'} />
                </div>
                <div className={`runtimePanelView ${activeTab === 'Neural' ? 'active' : ''}`} hidden={activeTab !== 'Neural'}>
                    <NeuralRuntime authToken={authToken} isActive={isActive && activeTab === 'Neural'} />
                </div>
            </div>
        </div>
    )
}

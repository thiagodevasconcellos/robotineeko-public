import { useCallback, useEffect, useMemo, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import './NeuralRuntime.css'

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

function formatPercent(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '--'
    }

    return `${(numeric * 100).toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}%`
}

function formatTimestamp(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return '--'
    }

    try {
        return new Date(numeric * 1000).toLocaleString()
    } catch {
        return '--'
    }
}

function formatDurationSeconds(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric < 0) {
        return '--'
    }

    if (numeric < 1) {
        return `${numeric.toFixed(2)} s`
    }

    if (numeric < 60) {
        return `${numeric.toFixed(1)} s`
    }

    const minutes = Math.floor(numeric / 60)
    const seconds = numeric % 60
    return `${minutes}m ${seconds.toFixed(0)}s`
}

function FieldRow({ label, value }) {
    return (
        <div className='neuralRuntimeFieldRow'>
            <div className='neuralRuntimeFieldLabel'>{label}</div>
            <div className='neuralRuntimeFieldValue'>{value}</div>
        </div>
    )
}

function SectionCard({ title, children }) {
    return (
        <section className='neuralRuntimeCard'>
            <div className='neuralRuntimeCardTitle'>{title}</div>
            <div className='neuralRuntimeCardBody'>{children}</div>
        </section>
    )
}

function buildEventHistogram(recentEvents = []) {
    const counts = new Map()

    for (const item of recentEvents) {
        const label = `${item?.kind || 'event'}:${item?.run_type || item?.status || 'generic'}`
        counts.set(label, (counts.get(label) || 0) + 1)
    }

    return Array.from(counts.entries())
        .map(([label, count]) => ({ label, count }))
        .sort((left, right) => right.count - left.count || left.label.localeCompare(right.label))
}

export function NeuralRuntime({
    authToken = '',
    isActive = false,
}) {
    const [runtimeState, setRuntimeState] = useState({
        loading: false,
        error: '',
        payload: null,
        refreshedAt: null,
    })

    const fetchStatus = useCallback(async ({ silent = false } = {}) => {
        if (!authToken) {
            return
        }

        if (!silent) {
            setRuntimeState((current) => ({
                ...current,
                loading: true,
                error: '',
            }))
        }

        try {
            const response = await fetch(buildApiUrl('/neural/runtime'), {
                headers: {
                    'Authorization': `Bearer ${authToken}`,
                },
            })
            const data = await readJsonResponse(response)
            if (!response.ok) {
                throw new Error(extractApiErrorMessage(data, 'Failed to load neural runtime.'))
            }

            setRuntimeState({
                loading: false,
                error: '',
                payload: data.runtime || data,
                refreshedAt: Date.now(),
            })
        } catch (error) {
            setRuntimeState((current) => ({
                ...current,
                loading: false,
                error: error?.message || 'Failed to load neural runtime.',
            }))
        }
    }, [authToken])

    useEffect(() => {
        if (!isActive || !authToken) {
            return undefined
        }

        void fetchStatus()
        const intervalId = window.setInterval(() => {
            void fetchStatus({ silent: true })
        }, 2500)

        return () => window.clearInterval(intervalId)
    }, [authToken, fetchStatus, isActive])

    const payload = runtimeState.payload || {}
    const activeJobsMap = payload.active_jobs || {}
    const activeJobs = Object.values(activeJobsMap || {})
        .filter(Boolean)
        .sort((left, right) => Number(right?.started_at || 0) - Number(left?.started_at || 0))
    const activeCounts = payload.active_counts || {}
    const recentEvents = Array.isArray(payload.recent_events) ? payload.recent_events : []
    const eventHistogram = buildEventHistogram(recentEvents)
    const topJob = activeJobs[0] || null

    const statusTone = useMemo(() => {
        if (runtimeState.error || payload.last_error) {
            return 'error'
        }
        if (activeJobs.length > 0) {
            return 'warn'
        }
        if (payload.last_run_at) {
            return 'ok'
        }
        return 'idle'
    }, [activeJobs.length, payload.last_error, payload.last_run_at, runtimeState.error])

    const exportPayload = {
        active_jobs: activeJobsMap,
        active_counts: activeCounts,
        last_run_at: payload.last_run_at,
        last_error: payload.last_error,
        recent_events: recentEvents,
    }

    async function copyText(text) {
        if (!text || !String(text).trim()) {
            return
        }

        try {
            await navigator.clipboard.writeText(String(text))
        } catch (error) {
            setRuntimeState((current) => ({
                ...current,
                error: error?.message || 'Failed to copy neural runtime.',
            }))
        }
    }

    async function handleCopySummary() {
        const summary = [
            'Neural runtime',
            `Active jobs: ${formatValue(activeJobs.length)}`,
            `Queued: ${formatValue(activeCounts.queued || 0)}`,
            `Running: ${formatValue(activeCounts.running || 0)}`,
            `Waiting: ${formatValue(activeCounts.waiting || 0)}`,
            `Last run at: ${formatTimestamp(payload.last_run_at)}`,
            `Last error: ${formatValue(payload.last_error)}`,
            topJob
                ? `Top job: ${formatValue(topJob.network_id)} · ${formatValue(topJob.status)} · ${formatPercent(topJob.progress)}`
                : 'Top job: --',
        ].join('\n')

        await copyText(summary)
    }

    async function handleCopyJson() {
        await copyText(JSON.stringify(exportPayload, null, 2))
    }

    function handleSaveJson() {
        try {
            const blob = new Blob([JSON.stringify(exportPayload, null, 2)], { type: 'application/json' })
            const url = URL.createObjectURL(blob)
            const anchor = document.createElement('a')
            anchor.href = url
            anchor.download = `neural-runtime-${Date.now()}.json`
            anchor.click()
            URL.revokeObjectURL(url)
        } catch (error) {
            setRuntimeState((current) => ({
                ...current,
                error: error?.message || 'Failed to save neural runtime.',
            }))
        }
    }

    return (
        <div className='NeuralRuntime'>
            <div className='neuralRuntimeToolbar'>
                <div className='neuralRuntimeHeadline'>
                    <span className={`neuralRuntimeDot is-${statusTone}`} aria-hidden='true' />
                    <span>Neural runtime</span>
                </div>
                <div className='neuralRuntimeToolbarMeta'>
                    Last refresh: {runtimeState.refreshedAt ? new Date(runtimeState.refreshedAt).toLocaleTimeString() : '--'}
                </div>
                <button type='button' className='neuralRuntimeActionButton' onClick={() => void fetchStatus()} disabled={!authToken || runtimeState.loading}>
                    {runtimeState.loading ? 'Refreshing...' : 'Refresh'}
                </button>
                <button type='button' className='neuralRuntimeActionButton' onClick={() => void handleCopySummary()} disabled={!runtimeState.payload}>
                    Copy summary
                </button>
                <button type='button' className='neuralRuntimeActionButton' onClick={() => void handleCopyJson()} disabled={!runtimeState.payload}>
                    Copy JSON
                </button>
                <button type='button' className='neuralRuntimeActionButton' onClick={handleSaveJson} disabled={!runtimeState.payload}>
                    Save JSON
                </button>
            </div>

            {runtimeState.error ? (
                <div className='neuralRuntimeError'>{runtimeState.error}</div>
            ) : null}

            <div className='neuralRuntimeSummary'>
                <div className='neuralRuntimeSummaryTitle'>Runtime state</div>
                <div className='neuralRuntimeSummaryText'>
                    Active jobs: {formatValue(activeJobs.length)} · Queued: {formatValue(activeCounts.queued || 0)} · Running: {formatValue(activeCounts.running || 0)} · Last run: {formatTimestamp(payload.last_run_at)}
                </div>
            </div>

            <div className='neuralRuntimeSummary'>
                <div className='neuralRuntimeSummaryTitle'>Top active job</div>
                <div className='neuralRuntimeSummaryText'>
                    {topJob
                        ? `${formatValue(topJob.network_id)} · ${formatValue(topJob.phase_label || topJob.phase)} · ${formatValue(topJob.status)} · progress ${formatPercent(topJob.progress)} · runtime ${formatDurationSeconds(topJob.runtime_age_seconds)}`
                        : 'No active neural job is currently running.'}
                </div>
            </div>

            <div className='neuralRuntimeGrid'>
                <SectionCard title='Runtime'>
                    <FieldRow label='Active jobs' value={formatValue(activeJobs.length)} />
                    <FieldRow label='Queued' value={formatValue(activeCounts.queued || 0)} />
                    <FieldRow label='Running' value={formatValue(activeCounts.running || 0)} />
                    <FieldRow label='Completed recent' value={formatValue(activeCounts.completed || 0)} />
                    <FieldRow label='Failed recent' value={formatValue(activeCounts.failed || 0)} />
                    <FieldRow label='Cancelled recent' value={formatValue(activeCounts.cancelled || 0)} />
                    <FieldRow label='Last run at' value={formatTimestamp(payload.last_run_at)} />
                    <FieldRow label='Last error' value={formatValue(payload.last_error)} />
                </SectionCard>

                <SectionCard title='Top job'>
                    <FieldRow label='Network' value={formatValue(topJob?.network_id)} />
                    <FieldRow label='Run id' value={formatValue(topJob?.run_id)} />
                    <FieldRow label='Status' value={formatValue(topJob?.status)} />
                    <FieldRow label='Phase' value={formatValue(topJob?.phase_label || topJob?.phase)} />
                    <FieldRow label='Progress' value={formatPercent(topJob?.progress)} />
                    <FieldRow label='Feed status' value={formatValue(topJob?.data_feed_status)} />
                    <FieldRow label='Runtime age' value={formatDurationSeconds(topJob?.runtime_age_seconds)} />
                    <FieldRow label='Heartbeat age' value={formatDurationSeconds(topJob?.heartbeat_age_seconds)} />
                </SectionCard>
            </div>

            <SectionCard title='Active jobs'>
                {activeJobs.length ? (
                    <div className='neuralRuntimeList'>
                        {activeJobs.map((job, index) => (
                            <div key={`${job.network_id || 'job'}-${job.run_id || index}`} className='neuralRuntimeListItem'>
                                <div className='neuralRuntimeListTitle'>
                                    {formatValue(job.network_id)} · {formatValue(job.status)}
                                </div>
                                <div className='neuralRuntimeListMeta'>
                                    run: {formatValue(job.run_id)} · phase: {formatValue(job.phase_label || job.phase)} · progress: {formatPercent(job.progress)} · feed: {formatValue(job.data_feed_status)}
                                </div>
                                <div className='neuralRuntimeListMeta'>
                                    symbol: {formatValue(job.symbol)} · timeframe: {formatValue(job.timeframe)} · bars: {formatValue(job.bars)} · source run: {formatValue(job.source_run_id)}
                                </div>
                                <div className='neuralRuntimeListSubmeta'>
                                    runtime: {formatDurationSeconds(job.runtime_age_seconds)} · update age: {formatDurationSeconds(job.update_age_seconds)} · heartbeat age: {formatDurationSeconds(job.heartbeat_age_seconds)} · cancel requested: {formatValue(job.cancel_requested)}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='neuralRuntimeEmpty'>No active neural jobs right now.</div>
                )}
            </SectionCard>

            <SectionCard title='Recent neural events'>
                {recentEvents.length ? (
                    <div className='neuralRuntimeList'>
                        {recentEvents.map((item, index) => (
                            <div key={`${item.kind || 'event'}-${item.run_id || index}`} className='neuralRuntimeListItem'>
                                <div className='neuralRuntimeListTitle'>
                                    {formatValue(item.kind)} · {formatValue(item.network_id)}
                                </div>
                                <div className='neuralRuntimeListMeta'>
                                    at: {formatTimestamp(item.at)} · run: {formatValue(item.run_id)} · type: {formatValue(item.run_type)} · status: {formatValue(item.status)}
                                </div>
                                <div className='neuralRuntimeListSubmeta'>
                                    bars: {formatValue(item.bars)} · symbol: {formatValue(item.symbol)} · timeframe: {formatValue(item.timeframe)} · duration: {formatDurationSeconds(item.duration_seconds)}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='neuralRuntimeEmpty'>No neural runtime history recorded yet.</div>
                )}
            </SectionCard>

            <SectionCard title='Event histogram'>
                {eventHistogram.length ? (
                    <div className='neuralRuntimeList'>
                        {eventHistogram.map((item) => (
                            <div key={item.label} className='neuralRuntimeListItem'>
                                <div className='neuralRuntimeListTitle'>{item.label}</div>
                                <div className='neuralRuntimeListMeta'>count: {formatValue(item.count)}</div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='neuralRuntimeEmpty'>No event patterns recorded yet.</div>
                )}
            </SectionCard>
        </div>
    )
}

import { useCallback, useEffect, useMemo, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import './ChartStatus.css'

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

function formatArray(value) {
    if (!Array.isArray(value) || value.length === 0) {
        return '--'
    }

    return value.join(', ')
}

function FieldRow({ label, value }) {
    return (
        <div className='chartStatusFieldRow'>
            <div className='chartStatusFieldLabel'>{label}</div>
            <div className='chartStatusFieldValue'>{value}</div>
        </div>
    )
}

function SectionCard({ title, children }) {
    return (
        <section className='chartStatusCard'>
            <div className='chartStatusCardTitle'>{title}</div>
            <div className='chartStatusCardBody'>{children}</div>
        </section>
    )
}

function buildFullRebuildReason(partialEligible, partialBlockers = []) {
    if (partialEligible) {
        return 'The current feature set is eligible for partial rebuild when the market update allows it.'
    }

    if (!Array.isArray(partialBlockers) || partialBlockers.length === 0) {
        return 'Partial rebuild is currently unavailable, but no explicit blocker was recorded.'
    }

    const primaryBlocker = partialBlockers[0]
    const blockerCount = partialBlockers.length
    const suffix = blockerCount > 1 ? ` (${blockerCount} blockers total)` : ''

    return `${primaryBlocker.name} is currently forcing full rebuild because ${primaryBlocker.reason}.${suffix}`
}

function buildReasonHistogram(recentReasons = []) {
    const counts = new Map()

    for (const item of recentReasons) {
        const label = item?.kind === 'invalidate'
            ? `invalidate:${item?.reason || 'unknown'}`
            : `${item?.mode || 'unknown'}:${item?.reason || 'unknown'}`
        counts.set(label, (counts.get(label) || 0) + 1)
    }

    return Array.from(counts.entries())
        .map(([label, count]) => ({ label, count }))
        .sort((left, right) => right.count - left.count || left.label.localeCompare(right.label))
}

export function ChartStatus({
    authToken = '',
    isActive = false,
}) {
    const [statusState, setStatusState] = useState({
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
            setStatusState((current) => ({
                ...current,
                loading: true,
                error: '',
            }))
        }

        try {
            const response = await fetch(buildApiUrl('/chart/status'), {
                headers: {
                    'Authorization': `Bearer ${authToken}`,
                },
            })
            const data = await readJsonResponse(response)
            if (!response.ok) {
                throw new Error(extractApiErrorMessage(data, 'Failed to load chart status.'))
            }

            setStatusState({
                loading: false,
                error: '',
                payload: data,
                refreshedAt: Date.now(),
            })
        } catch (error) {
            setStatusState((current) => ({
                ...current,
                loading: false,
                error: error?.message || 'Failed to load chart status.',
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

    const payload = statusState.payload || {}
    const meta = payload.meta || {}
    const snapshot = payload.snapshot || {}
    const runtime = payload.runtime || {}
    const marketRuntime = runtime.market_runtime || {}
    const symbolSnapshot = runtime.symbol_snapshot || {}
    const runtimeContracts = Array.isArray(runtime.snapshot_runtime_contracts) ? runtime.snapshot_runtime_contracts : []
    const partialBlockers = Array.isArray(runtime.snapshot_partial_blockers) ? runtime.snapshot_partial_blockers : []
    const partialOpportunity = runtime.snapshot_partial_opportunity || null
    const runtimeWindow = runtime.snapshot_runtime_window || {}
    const performance = runtime.snapshot_performance || {}
    const indicatorCosts = Array.isArray(performance.indicator_costs) ? [...performance.indicator_costs] : []
    indicatorCosts.sort((left, right) => Number(right?.elapsed_ms || 0) - Number(left?.elapsed_ms || 0))
    const topCost = indicatorCosts[0] || null
    const refreshCounts = runtime.snapshot_refresh_counts || {}
    const recentReasons = Array.isArray(runtime.snapshot_recent_reasons) ? runtime.snapshot_recent_reasons : []
    const reasonHistogram = buildReasonHistogram(recentReasons)
    const consumerViews = runtime.consumer_views || {}
    const strategyFeatureView = consumerViews.strategy_feature_view || {}
    const neuralMarketView = consumerViews.neural_market_view || {}
    const fullRebuildReason = buildFullRebuildReason(runtime.snapshot_partial_eligible, partialBlockers)

    const exportPayload = {
        status: payload.status || null,
        request: payload.request || null,
        meta,
        snapshot,
        runtime,
    }

    const statusTone = useMemo(() => {
        if (statusState.error || payload.error) {
            return 'error'
        }
        if (payload.loading && !payload.ready) {
            return 'warn'
        }
        if (payload.ready) {
            return 'ok'
        }
        return 'idle'
    }, [payload.error, payload.loading, payload.ready, statusState.error])

    async function copyText(text) {
        if (!text || !String(text).trim()) {
            return
        }

        try {
            await navigator.clipboard.writeText(String(text))
        } catch (error) {
            setStatusState((current) => ({
                ...current,
                error: error?.message || 'Failed to copy chart runtime status.',
            }))
        }
    }

    async function handleCopySummary() {
        const summary = [
            `Chart runtime`,
            `Status: ${payload.ready ? 'ready' : payload.loading ? 'loading' : 'idle'}`,
            `Symbol: ${formatValue(payload.request?.symbol)}`,
            `Timeframe: ${formatValue(payload.request?.timeframe)}`,
            `Bars: ${formatValue(payload.request?.bars)}`,
            `Refresh mode: ${formatValue(runtime.snapshot_refresh_mode)}`,
            `Partial eligible: ${formatValue(runtime.snapshot_partial_eligible)}`,
            `Why full rebuild?: ${fullRebuildReason}`,
            `Cache key: ${formatValue(meta.cache_key)}`,
            `Loaded candles: ${formatValue(meta.loaded_candles)}`,
            `Market revision: ${formatValue(marketRuntime.revision)}`,
            `Changed features: ${formatArray(marketRuntime.changed_features)}`,
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
            anchor.download = `chart-runtime-${Date.now()}.json`
            anchor.click()
            URL.revokeObjectURL(url)
        } catch (error) {
            setStatusState((current) => ({
                ...current,
                error: error?.message || 'Failed to save chart runtime status.',
            }))
        }
    }

    return (
        <div className={`ChartStatus ${isActive ? 'active' : ''}`}>
            <div className='chartStatusToolbar'>
                <div className='chartStatusHeadline'>
                    <span className={`chartStatusDot is-${statusTone}`} aria-hidden='true' />
                    <span>Chart runtime</span>
                </div>
                <div className='chartStatusToolbarMeta'>
                    Last refresh: {statusState.refreshedAt ? new Date(statusState.refreshedAt).toLocaleTimeString() : '--'}
                </div>
                <button
                    type='button'
                    className='chartStatusRefreshButton'
                    onClick={() => void fetchStatus()}
                    disabled={!authToken || statusState.loading}
                >
                    {statusState.loading ? 'Refreshing...' : 'Refresh'}
                </button>
                <button
                    type='button'
                    className='chartStatusRefreshButton'
                    onClick={() => void handleCopySummary()}
                    disabled={!statusState.payload}
                >
                    Copy summary
                </button>
                <button
                    type='button'
                    className='chartStatusRefreshButton'
                    onClick={() => void handleCopyJson()}
                    disabled={!statusState.payload}
                >
                    Copy JSON
                </button>
                <button
                    type='button'
                    className='chartStatusRefreshButton'
                    onClick={handleSaveJson}
                    disabled={!statusState.payload}
                >
                    Save JSON
                </button>
            </div>

            {statusState.error ? (
                <div className='chartStatusError'>{statusState.error}</div>
            ) : null}

            <div className='chartStatusSummary'>
                <div className='chartStatusSummaryTitle'>Why full rebuild?</div>
                <div className='chartStatusSummaryText'>{fullRebuildReason}</div>
            </div>

            <div className='chartStatusSummary'>
                <div className='chartStatusSummaryTitle'>Snapshot cost</div>
                <div className='chartStatusSummaryText'>
                    Total: {formatValue(performance.total_elapsed_ms)} ms · Indicators: {formatValue(performance.indicator_total_ms)} ms · Other: {formatValue(performance.non_indicator_elapsed_ms)} ms
                    {topCost ? ` · Heaviest feature: ${topCost.name} (${formatValue(topCost.elapsed_ms)} ms)` : ''}
                </div>
            </div>

            <div className='chartStatusGrid'>
                <SectionCard title='Request'>
                    <FieldRow label='Symbol' value={formatValue(payload.request?.symbol)} />
                    <FieldRow label='Timeframe' value={formatValue(payload.request?.timeframe)} />
                    <FieldRow label='Bars' value={formatValue(payload.request?.bars)} />
                    <FieldRow label='Indicators' value={formatValue((payload.request?.indicators || []).length)} />
                </SectionCard>

                <SectionCard title='Market'>
                    <FieldRow label='Ready' value={formatValue(payload.ready)} />
                    <FieldRow label='Loading' value={formatValue(payload.loading)} />
                    <FieldRow label='Source' value={formatValue(meta.source)} />
                    <FieldRow label='Cache key' value={formatValue(meta.cache_key)} />
                    <FieldRow label='Loaded candles' value={formatValue(meta.loaded_candles)} />
                    <FieldRow label='Requested bars' value={formatValue(meta.requested_bars)} />
                    <FieldRow label='Seed bars' value={formatValue(meta.recommended_seed_bars)} />
                    <FieldRow label='Load step' value={formatValue(meta.history_load_step)} />
                    <FieldRow label='First time' value={formatTimestamp(meta.first_time)} />
                    <FieldRow label='Last time' value={formatTimestamp(meta.last_time)} />
                </SectionCard>

                <SectionCard title='Snapshot'>
                    <FieldRow label='Refresh mode' value={formatValue(runtime.snapshot_refresh_mode)} />
                    <FieldRow label='Partial eligible' value={formatValue(runtime.snapshot_partial_eligible)} />
                    <FieldRow label='Full rebuilds' value={formatValue(refreshCounts.full)} />
                    <FieldRow label='Partial rebuilds' value={formatValue(refreshCounts.partial)} />
                    <FieldRow label='Total ms' value={formatValue(performance.total_elapsed_ms)} />
                    <FieldRow label='Indicators ms' value={formatValue(performance.indicator_total_ms)} />
                    <FieldRow label='Other ms' value={formatValue(performance.non_indicator_elapsed_ms)} />
                    <FieldRow label='Affected from index' value={formatValue(snapshot.affected_from_index)} />
                    <FieldRow label='Dirty reason' value={formatValue(snapshot.dirty_reason)} />
                    <FieldRow label='Built at' value={formatTimestamp(snapshot.built_at)} />
                    <FieldRow label='Error' value={formatValue(snapshot.error)} />
                    <FieldRow label='Row count' value={formatValue(symbolSnapshot.row_count)} />
                    <FieldRow label='Market columns' value={formatArray(symbolSnapshot.market_columns)} />
                    <FieldRow label='Derived columns' value={formatArray(symbolSnapshot.derived_columns)} />
                </SectionCard>

                <SectionCard title='Runtime window'>
                    <FieldRow label='Market revision' value={formatValue(marketRuntime.revision)} />
                    <FieldRow label='Tick revision' value={formatValue(marketRuntime.tick_revision)} />
                    <FieldRow label='Candle revision' value={formatValue(marketRuntime.candle_revision)} />
                    <FieldRow label='Last event' value={formatValue(marketRuntime.last_event)} />
                    <FieldRow label='Changed features' value={formatArray(marketRuntime.changed_features)} />
                    <FieldRow label='Context start' value={formatValue(runtimeWindow.context_start)} />
                    <FieldRow label='Recompute from' value={formatValue(runtimeWindow.recompute_from_index)} />
                    <FieldRow label='Affected from' value={formatValue(runtimeWindow.affected_from_index)} />
                </SectionCard>

                <SectionCard title='Consumer views'>
                    <FieldRow label='Strategy rows' value={formatValue(strategyFeatureView.row_count)} />
                    <FieldRow label='Strategy scope' value={formatValue(strategyFeatureView.history_scope_mode)} />
                    <FieldRow label='Strategy bars' value={formatValue(strategyFeatureView.history_scope_bars)} />
                    <FieldRow label='Strategy derived columns' value={formatValue((strategyFeatureView.derived_columns || []).length)} />
                    <FieldRow label='Neural ready' value={formatValue(neuralMarketView.ready)} />
                    <FieldRow label='Neural source' value={formatValue(neuralMarketView.source)} />
                    <FieldRow label='Neural cache key' value={formatValue(neuralMarketView.cache_key)} />
                    <FieldRow label='Neural bars requested' value={formatValue(neuralMarketView.bars_requested)} />
                    <FieldRow label='Neural bars available' value={formatValue(neuralMarketView.bars_available)} />
                </SectionCard>
            </div>

            <SectionCard title='Partial rebuild blockers'>
                {partialBlockers.length ? (
                    <div className='chartStatusList'>
                        {partialBlockers.map((blocker, index) => (
                            <div key={`${blocker.name}-${index}`} className='chartStatusListItem'>
                                <div className='chartStatusListTitle'>{blocker.name}</div>
                                <div className='chartStatusListMeta'>
                                    reason: {formatValue(blocker.reason)} · mode: {formatValue(blocker.incremental_mode)} · warmup: {formatValue(blocker.warmup_bars)} · patch: {formatValue(blocker.patch_bars)}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='chartStatusEmpty'>No blockers. The active feature set is eligible for partial rebuild.</div>
                )}
            </SectionCard>

            <SectionCard title='Partial opportunity lost'>
                {partialOpportunity ? (
                    <>
                        <FieldRow label='Status' value={formatValue(partialOpportunity.status)} />
                        <FieldRow label='Reason' value={formatValue(partialOpportunity.reason)} />
                        <FieldRow label='Source' value={formatValue(partialOpportunity.source)} />
                        <FieldRow label='Blockers' value={formatValue(partialOpportunity.blocker_count)} />
                    </>
                ) : (
                    <div className='chartStatusEmpty'>No lost partial opportunity was recorded for the current snapshot.</div>
                )}
            </SectionCard>

            <SectionCard title='Recent rebuild reasons'>
                {recentReasons.length ? (
                    <div className='chartStatusList'>
                        {recentReasons.map((item, index) => (
                            <div key={`${item.kind || 'entry'}-${index}`} className='chartStatusListItem'>
                                <div className='chartStatusListTitle'>
                                    {item.kind === 'invalidate'
                                        ? `Invalidated · ${formatValue(item.reason)}`
                                        : `${formatValue(item.mode)} rebuild · ${formatValue(item.reason)}`}
                                </div>
                                <div className='chartStatusListMeta'>
                                    at: {formatTimestamp(item.at)} · partial eligible: {formatValue(item.partial_eligible)} · blockers: {formatValue(item.blocker_count)}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='chartStatusEmpty'>No rebuild history recorded yet.</div>
                )}
            </SectionCard>

            <SectionCard title='Reason histogram'>
                {reasonHistogram.length ? (
                    <div className='chartStatusList'>
                        {reasonHistogram.map((item) => (
                            <div key={item.label} className='chartStatusListItem'>
                                <div className='chartStatusListTitle'>{item.label}</div>
                                <div className='chartStatusListMeta'>count: {formatValue(item.count)}</div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='chartStatusEmpty'>No invalidation patterns recorded yet.</div>
                )}
            </SectionCard>

            <SectionCard title='Feature cost'>
                {indicatorCosts.length ? (
                    <div className='chartStatusList'>
                        {indicatorCosts.map((item, index) => (
                            <div key={`${item.name}-${index}`} className='chartStatusListItem'>
                                <div className='chartStatusListTitle'>{item.name}</div>
                                <div className='chartStatusListMeta'>
                                    elapsed: {formatValue(item.elapsed_ms)} ms · columns: {formatValue(item.created_columns)} · rows: {formatValue(item.row_count)}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='chartStatusEmpty'>No feature timing data recorded yet.</div>
                )}
            </SectionCard>

            <SectionCard title='Runtime contracts'>
                {runtimeContracts.length ? (
                    <div className='chartStatusList'>
                        {runtimeContracts.map((contract, index) => (
                            <div key={`${contract.name}-${index}`} className='chartStatusListItem'>
                                <div className='chartStatusListTitle'>{contract.name}</div>
                                <div className='chartStatusListMeta'>
                                    mode: {formatValue(contract.incremental_mode)} · partial: {formatValue(contract.supports_partial_rebuild)} · full: {formatValue(contract.requires_full_rebuild)} · warmup: {formatValue(contract.warmup_bars)} · patch: {formatValue(contract.patch_bars)} · layer: {formatValue(contract.output_layer)}
                                </div>
                                <div className='chartStatusListSubmeta'>
                                    inputs: {formatArray(contract.input_columns)}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='chartStatusEmpty'>No runtime contract data available yet.</div>
                )}
            </SectionCard>
        </div>
    )
}

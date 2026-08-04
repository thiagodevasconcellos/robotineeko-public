import { useCallback, useEffect, useMemo, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import './StrategyRuntime.css'

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

function FieldRow({ label, value }) {
    return (
        <div className='strategyRuntimeFieldRow'>
            <div className='strategyRuntimeFieldLabel'>{label}</div>
            <div className='strategyRuntimeFieldValue'>{value}</div>
        </div>
    )
}

function SectionCard({ title, children }) {
    return (
        <section className='strategyRuntimeCard'>
            <div className='strategyRuntimeCardTitle'>{title}</div>
            <div className='strategyRuntimeCardBody'>{children}</div>
        </section>
    )
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

export function StrategyRuntime({
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
            const response = await fetch(buildApiUrl('/strategy/status'), {
                headers: {
                    'Authorization': `Bearer ${authToken}`,
                },
            })
            const data = await readJsonResponse(response)
            if (!response.ok) {
                throw new Error(extractApiErrorMessage(data, 'Failed to load strategy runtime.'))
            }

            setRuntimeState({
                loading: false,
                error: '',
                payload: data,
                refreshedAt: Date.now(),
            })
        } catch (error) {
            setRuntimeState((current) => ({
                ...current,
                loading: false,
                error: error?.message || 'Failed to load strategy runtime.',
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
    const stats = payload.stats || {}
    const executionPolicy = payload.execution_policy || {}
    const request = payload.request || {}
    const requestBacktest = request.backtest || {}
    const requestStrategy = request.strategy || {}
    const refreshCounts = payload.refresh_counts || {}
    const recentReasons = Array.isArray(payload.recent_reasons) ? payload.recent_reasons : []
    const reasonHistogram = buildReasonHistogram(recentReasons)
    const performance = payload.performance || {}

    const statusTone = useMemo(() => {
        if (runtimeState.error || payload.error) {
            return 'error'
        }
        if (payload.is_stale) {
            return 'warn'
        }
        if (payload.has_results) {
            return 'ok'
        }
        return 'idle'
    }, [payload.error, payload.has_results, payload.is_stale, runtimeState.error])

    const exportPayload = {
        status: payload.status || null,
        request,
        stats,
        runtime: {
            has_strategy: payload.has_strategy,
            has_backtester: payload.has_backtester,
            has_results: payload.has_results,
            backtest_active: payload.backtest_active,
            last_applied_at: payload.last_applied_at,
            last_invalidated_reason: payload.last_invalidated_reason,
            last_invalidated_overlap: payload.last_invalidated_overlap,
            is_stale: payload.is_stale,
            stale_reason: payload.stale_reason,
            stale_overlap: payload.stale_overlap,
            last_refresh_mode: payload.last_refresh_mode,
            last_refresh_from_index: payload.last_refresh_from_index,
            refresh_counts: refreshCounts,
            recent_reasons: recentReasons,
            performance,
            required_features: payload.required_features,
            execution_policy: executionPolicy,
        },
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
                error: error?.message || 'Failed to copy strategy runtime.',
            }))
        }
    }

    async function handleCopySummary() {
        const summary = [
            'Strategy runtime',
            `Has strategy: ${formatValue(payload.has_strategy)}`,
            `Has backtester: ${formatValue(payload.has_backtester)}`,
            `Has results: ${formatValue(payload.has_results)}`,
            `Backtest active: ${formatValue(payload.backtest_active)}`,
            `Last refresh mode: ${formatValue(payload.last_refresh_mode)}`,
            `Last invalidated reason: ${formatValue(payload.last_invalidated_reason)}`,
            `Stale: ${formatValue(payload.is_stale)}`,
            `Full refreshes: ${formatValue(refreshCounts.full)}`,
            `Partial refreshes: ${formatValue(refreshCounts.partial)}`,
            `History scope: ${formatValue(executionPolicy.history_scope_mode)}`,
            `Cost model: ${formatValue(executionPolicy.cost_profile_label || executionPolicy.cost_profile)}`,
            `Asset type: ${formatValue(executionPolicy.asset_type_label || executionPolicy.asset_type)}`,
            `Execution mode: ${formatValue(executionPolicy.execution_mode)}`,
            `Trades: ${formatValue(stats.n_trades)}`,
            `Net PnL: ${formatValue(stats.net_pnl)}`,
            `Win rate: ${formatPercent(stats.win_rate)}`,
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
            anchor.download = `strategy-runtime-${Date.now()}.json`
            anchor.click()
            URL.revokeObjectURL(url)
        } catch (error) {
            setRuntimeState((current) => ({
                ...current,
                error: error?.message || 'Failed to save strategy runtime.',
            }))
        }
    }

    return (
        <div className='StrategyRuntime'>
            <div className='strategyRuntimeToolbar'>
                <div className='strategyRuntimeHeadline'>
                    <span className={`strategyRuntimeDot is-${statusTone}`} aria-hidden='true' />
                    <span>Strategy runtime</span>
                </div>
                <div className='strategyRuntimeToolbarMeta'>
                    Last refresh: {runtimeState.refreshedAt ? new Date(runtimeState.refreshedAt).toLocaleTimeString() : '--'}
                </div>
                <button type='button' className='strategyRuntimeActionButton' onClick={() => void fetchStatus()} disabled={!authToken || runtimeState.loading}>
                    {runtimeState.loading ? 'Refreshing...' : 'Refresh'}
                </button>
                <button type='button' className='strategyRuntimeActionButton' onClick={() => void handleCopySummary()} disabled={!runtimeState.payload}>
                    Copy summary
                </button>
                <button type='button' className='strategyRuntimeActionButton' onClick={() => void handleCopyJson()} disabled={!runtimeState.payload}>
                    Copy JSON
                </button>
                <button type='button' className='strategyRuntimeActionButton' onClick={handleSaveJson} disabled={!runtimeState.payload}>
                    Save JSON
                </button>
            </div>

            {runtimeState.error ? (
                <div className='strategyRuntimeError'>{runtimeState.error}</div>
            ) : null}

            <div className='strategyRuntimeSummary'>
                <div className='strategyRuntimeSummaryTitle'>Runtime state</div>
                <div className='strategyRuntimeSummaryText'>
                    Refresh: {formatValue(payload.last_refresh_mode)} · Stale: {formatValue(payload.is_stale)} · Invalidated by: {formatValue(payload.last_invalidated_reason)} · Required features: {formatValue((payload.required_features || []).length)}
                </div>
            </div>

            <div className='strategyRuntimeSummary'>
                <div className='strategyRuntimeSummaryTitle'>Runtime cost</div>
                <div className='strategyRuntimeSummaryText'>
                    Last run: {formatValue(performance.last_elapsed_ms)} ms · Rows: {formatValue(performance.last_rows)} · Trades: {formatValue(performance.last_n_trades)} · Net PnL: {formatValue(performance.last_net_pnl)}
                </div>
            </div>

            <div className='strategyRuntimeGrid'>
                <SectionCard title='Runtime'>
                    <FieldRow label='Has strategy' value={formatValue(payload.has_strategy)} />
                    <FieldRow label='Has backtester' value={formatValue(payload.has_backtester)} />
                    <FieldRow label='Has results' value={formatValue(payload.has_results)} />
                    <FieldRow label='Backtest active' value={formatValue(payload.backtest_active)} />
                    <FieldRow label='Full refreshes' value={formatValue(refreshCounts.full)} />
                    <FieldRow label='Partial refreshes' value={formatValue(refreshCounts.partial)} />
                    <FieldRow label='Last applied' value={formatTimestamp(payload.last_applied_at)} />
                    <FieldRow label='Last refresh mode' value={formatValue(payload.last_refresh_mode)} />
                    <FieldRow label='Refresh from index' value={formatValue(payload.last_refresh_from_index)} />
                    <FieldRow label='Stale' value={formatValue(payload.is_stale)} />
                </SectionCard>

                <SectionCard title='Invalidation'>
                    <FieldRow label='Last invalidated reason' value={formatValue(payload.last_invalidated_reason)} />
                    <FieldRow label='Overlap' value={formatArray(payload.last_invalidated_overlap)} />
                    <FieldRow label='Stale reason' value={formatValue(payload.stale_reason)} />
                    <FieldRow label='Stale overlap' value={formatArray(payload.stale_overlap)} />
                </SectionCard>

                <SectionCard title='Execution policy'>
                    <FieldRow label='Cost model' value={formatValue(executionPolicy.cost_profile_label || executionPolicy.cost_profile)} />
                    <FieldRow label='Broker scope' value={formatValue(executionPolicy.broker_profile_label || executionPolicy.broker_label || executionPolicy.broker_code)} />
                    <FieldRow label='Asset type' value={formatValue(executionPolicy.asset_type_label || executionPolicy.asset_type)} />
                    <FieldRow
                        label='Operational cost items'
                        value={Array.isArray(stats.operational_cost_breakdown_totals) && stats.operational_cost_breakdown_totals.length
                            ? stats.operational_cost_breakdown_totals.slice(0, 2).map((item) => `${formatValue(item?.label || item?.id)}: ${formatValue(item?.amount)}`).join(' · ')
                            : '-'}
                    />
                    <FieldRow
                        label='Estimated tax items'
                        value={Array.isArray(stats.estimated_tax_breakdown_totals) && stats.estimated_tax_breakdown_totals.length
                            ? stats.estimated_tax_breakdown_totals.slice(0, 2).map((item) => `${formatValue(item?.label || item?.id)}: ${formatValue(item?.amount)}`).join(' · ')
                            : '-'}
                    />
                    <FieldRow label='Execution mode' value={formatValue(executionPolicy.execution_mode)} />
                    <FieldRow label='TP fill' value={formatValue(executionPolicy.take_profit_fill)} />
                    <FieldRow label='SL fill' value={formatValue(executionPolicy.stop_loss_fill)} />
                    <FieldRow label='Trail fill' value={formatValue(executionPolicy.trailing_stop_fill)} />
                    <FieldRow label='Conflict policy' value={formatValue(executionPolicy.intrabar_conflict_policy)} />
                </SectionCard>

                <SectionCard title='History scope'>
                    <FieldRow label='Scope mode' value={formatValue(executionPolicy.history_scope_mode)} />
                    <FieldRow label='Scope bars' value={formatValue(requestBacktest.history_scope_bars ?? executionPolicy.history_scope_bars)} />
                    <FieldRow label='Available bars' value={formatValue(requestBacktest.history_scope_available_bars)} />
                    <FieldRow label='Requested bars' value={formatValue(requestBacktest.history_scope_requested_bars)} />
                </SectionCard>

                <SectionCard title='Result snapshot'>
                    <FieldRow label='Trades' value={formatValue(stats.n_trades)} />
                    <FieldRow label='Net PnL' value={formatValue(stats.net_pnl)} />
                    <FieldRow label='Win rate' value={formatPercent(stats.win_rate)} />
                    <FieldRow label='Avg trade' value={formatValue(stats.expectancy_per_trade)} />
                    <FieldRow label='Max DD' value={formatValue(stats.max_drawdown)} />
                    <FieldRow label='Max DD %' value={formatPercent(stats.max_drawdown_pct)} />
                </SectionCard>

                <SectionCard title='Dependencies'>
                    <FieldRow label='Required features' value={formatArray(payload.required_features)} />
                    <FieldRow label='Available columns' value={formatValue((payload.available_columns || []).length)} />
                    <FieldRow label='Applied indicators' value={formatValue((payload.applied_indicators || []).length)} />
                    <FieldRow label='Priority' value={formatValue(requestStrategy.other?.priority)} />
                    <FieldRow label='Allow inversion' value={formatValue(requestStrategy.other?.allowInversion)} />
                </SectionCard>
            </div>

            <SectionCard title='Recent runtime reasons'>
                {recentReasons.length ? (
                    <div className='strategyRuntimeList'>
                        {recentReasons.map((item, index) => (
                            <div key={`${item.kind || 'entry'}-${index}`} className='strategyRuntimeListItem'>
                                <div className='strategyRuntimeListTitle'>
                                    {item.kind === 'invalidate'
                                        ? `Invalidated · ${formatValue(item.reason)}`
                                        : `${formatValue(item.mode)} refresh · ${formatValue(item.reason)}`}
                                </div>
                                <div className='strategyRuntimeListMeta'>
                                    at: {formatTimestamp(item.at)} · overlap: {formatArray(item.overlap)} · rows: {formatValue(item.rows)} · elapsed: {formatValue(item.elapsed_ms)} ms
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='strategyRuntimeEmpty'>No runtime history recorded yet.</div>
                )}
            </SectionCard>

            <SectionCard title='Reason histogram'>
                {reasonHistogram.length ? (
                    <div className='strategyRuntimeList'>
                        {reasonHistogram.map((item) => (
                            <div key={item.label} className='strategyRuntimeListItem'>
                                <div className='strategyRuntimeListTitle'>{item.label}</div>
                                <div className='strategyRuntimeListMeta'>count: {formatValue(item.count)}</div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='strategyRuntimeEmpty'>No invalidation patterns recorded yet.</div>
                )}
            </SectionCard>
        </div>
    )
}

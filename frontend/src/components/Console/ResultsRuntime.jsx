import { useCallback, useEffect, useMemo, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import './ResultsRuntime.css'

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

function FieldRow({ label, value }) {
    return (
        <div className='resultsRuntimeFieldRow'>
            <div className='resultsRuntimeFieldLabel'>{label}</div>
            <div className='resultsRuntimeFieldValue'>{value}</div>
        </div>
    )
}

function SectionCard({ title, children }) {
    return (
        <section className='resultsRuntimeCard'>
            <div className='resultsRuntimeCardTitle'>{title}</div>
            <div className='resultsRuntimeCardBody'>{children}</div>
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

export function ResultsRuntime({
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
            const response = await fetch(buildApiUrl('/strategy/results'), {
                headers: {
                    'Authorization': `Bearer ${authToken}`,
                },
            })
            const data = await readJsonResponse(response)
            if (!response.ok) {
                throw new Error(extractApiErrorMessage(data, 'Failed to load results runtime.'))
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
                error: error?.message || 'Failed to load results runtime.',
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
    const request = payload.request || {}
    const performance = payload.performance || {}
    const executionPolicy = payload.execution_policy || {}
    const recentReasons = Array.isArray(payload.recent_reasons) ? payload.recent_reasons : []
    const reasonHistogram = buildReasonHistogram(recentReasons)
    const regimeSummary = Array.isArray(stats.regime_summary) ? stats.regime_summary : []
    const stabilitySummary = Array.isArray(stats.regime_stability_summary) ? stats.regime_stability_summary : []
    const topRegime = regimeSummary[0] || null
    const topStability = stabilitySummary[0] || null

    const statusTone = useMemo(() => {
        if (runtimeState.error || payload.error) {
            return 'error'
        }
        if (payload.status === 'empty') {
            return 'idle'
        }
        if (payload.rows > 0 || payload.has_results) {
            return 'ok'
        }
        return 'warn'
    }, [payload.error, payload.has_results, payload.rows, payload.status, runtimeState.error])

    const exportPayload = {
        status: payload.status || null,
        rows: payload.rows || 0,
        request,
        stats,
        performance,
        execution_policy: executionPolicy,
        recent_reasons: recentReasons,
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
                error: error?.message || 'Failed to copy results runtime.',
            }))
        }
    }

    async function handleCopySummary() {
        const summary = [
            'Results runtime',
            `Status: ${formatValue(payload.status)}`,
            `Rows: ${formatValue(payload.rows)}`,
            `Has results: ${formatValue(payload.has_results)}`,
            `Backtest active: ${formatValue(payload.backtest_active)}`,
            `Last refresh mode: ${formatValue(payload.last_refresh_mode)}`,
            `Last elapsed ms: ${formatValue(performance.last_elapsed_ms)}`,
            `Cost model: ${formatValue(executionPolicy.cost_profile_label || executionPolicy.cost_profile)}`,
            `Asset type: ${formatValue(executionPolicy.asset_type_label || executionPolicy.asset_type)}`,
            `Trades: ${formatValue(stats.n_trades)}`,
            `Net PnL: ${formatValue(stats.net_pnl)}`,
            `Win rate: ${formatPercent(stats.win_rate)}`,
            topRegime ? `Top regime bucket: ${formatValue(topRegime.regime_label || topRegime.regime_code)}` : 'Top regime bucket: --',
            topStability ? `Top stability bucket: ${formatValue(topStability.bucket)}` : 'Top stability bucket: --',
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
            anchor.download = `results-runtime-${Date.now()}.json`
            anchor.click()
            URL.revokeObjectURL(url)
        } catch (error) {
            setRuntimeState((current) => ({
                ...current,
                error: error?.message || 'Failed to save results runtime.',
            }))
        }
    }

    return (
        <div className='ResultsRuntime'>
            <div className='resultsRuntimeToolbar'>
                <div className='resultsRuntimeHeadline'>
                    <span className={`resultsRuntimeDot is-${statusTone}`} aria-hidden='true' />
                    <span>Results runtime</span>
                </div>
                <div className='resultsRuntimeToolbarMeta'>
                    Last refresh: {runtimeState.refreshedAt ? new Date(runtimeState.refreshedAt).toLocaleTimeString() : '--'}
                </div>
                <button type='button' className='resultsRuntimeActionButton' onClick={() => void fetchStatus()} disabled={!authToken || runtimeState.loading}>
                    {runtimeState.loading ? 'Refreshing...' : 'Refresh'}
                </button>
                <button type='button' className='resultsRuntimeActionButton' onClick={() => void handleCopySummary()} disabled={!runtimeState.payload}>
                    Copy summary
                </button>
                <button type='button' className='resultsRuntimeActionButton' onClick={() => void handleCopyJson()} disabled={!runtimeState.payload}>
                    Copy JSON
                </button>
                <button type='button' className='resultsRuntimeActionButton' onClick={handleSaveJson} disabled={!runtimeState.payload}>
                    Save JSON
                </button>
            </div>

            {runtimeState.error ? (
                <div className='resultsRuntimeError'>{runtimeState.error}</div>
            ) : null}

            <div className='resultsRuntimeSummary'>
                <div className='resultsRuntimeSummaryTitle'>Result state</div>
                <div className='resultsRuntimeSummaryText'>
                    Status: {formatValue(payload.status)} · Rows: {formatValue(payload.rows)} · Refresh: {formatValue(payload.last_refresh_mode)} · Backtest active: {formatValue(payload.backtest_active)}
                </div>
            </div>

            <div className='resultsRuntimeSummary'>
                <div className='resultsRuntimeSummaryTitle'>Result cost</div>
                <div className='resultsRuntimeSummaryText'>
                    Last run: {formatValue(performance.last_elapsed_ms)} ms · Rows: {formatValue(performance.last_rows)} · Trades: {formatValue(performance.last_n_trades)} · Net PnL: {formatValue(performance.last_net_pnl)}
                </div>
            </div>

            <div className='resultsRuntimeGrid'>
                <SectionCard title='Runtime'>
                    <FieldRow label='Status' value={formatValue(payload.status)} />
                    <FieldRow label='Rows' value={formatValue(payload.rows)} />
                    <FieldRow label='Has results' value={formatValue(payload.has_results)} />
                    <FieldRow label='Backtest active' value={formatValue(payload.backtest_active)} />
                    <FieldRow label='Last refresh mode' value={formatValue(payload.last_refresh_mode)} />
                    <FieldRow label='Last applied' value={formatTimestamp(payload.last_applied_at)} />
                </SectionCard>

                <SectionCard title='Snapshot'>
                    <FieldRow label='Trades' value={formatValue(stats.n_trades)} />
                    <FieldRow label='Net PnL' value={formatValue(stats.net_pnl)} />
                    <FieldRow label='Win rate' value={formatPercent(stats.win_rate)} />
                    <FieldRow label='Avg trade' value={formatValue(stats.expectancy_per_trade)} />
                    <FieldRow label='Max DD' value={formatValue(stats.max_drawdown)} />
                    <FieldRow label='Max DD %' value={formatPercent(stats.max_drawdown_pct)} />
                </SectionCard>

                <SectionCard title='Execution'>
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
                    <FieldRow label='History scope' value={formatValue(executionPolicy.history_scope_mode)} />
                    <FieldRow label='History bars' value={formatValue(executionPolicy.history_scope_bars)} />
                    <FieldRow label='TP fill' value={formatValue(executionPolicy.take_profit_fill)} />
                    <FieldRow label='SL fill' value={formatValue(executionPolicy.stop_loss_fill)} />
                    <FieldRow label='Conflict policy' value={formatValue(executionPolicy.intrabar_conflict_policy)} />
                </SectionCard>

                <SectionCard title='Context'>
                    <FieldRow label='Symbol' value={formatValue(request?.backtest?.symbol || request?.symbol)} />
                    <FieldRow label='Timeframe' value={formatValue(request?.backtest?.timeframe || request?.timeframe)} />
                    <FieldRow label='Requested scope bars' value={formatValue(request?.backtest?.history_scope_requested_bars)} />
                    <FieldRow label='Available scope bars' value={formatValue(request?.backtest?.history_scope_available_bars)} />
                    <FieldRow label='Applied indicators' value={formatValue((payload.applied_indicators || []).length)} />
                    <FieldRow label='Required features' value={formatValue((payload.required_features || []).length)} />
                </SectionCard>
            </div>

            <SectionCard title='Regime buckets'>
                {regimeSummary.length ? (
                    <div className='resultsRuntimeList'>
                        {regimeSummary.map((item, index) => (
                            <div key={`${item.regime_code || 'regime'}-${index}`} className='resultsRuntimeListItem'>
                                <div className='resultsRuntimeListTitle'>{formatValue(item.regime_label || item.regime_code)}</div>
                                <div className='resultsRuntimeListMeta'>
                                    trades: {formatValue(item.n_trades)} · win rate: {formatPercent(item.win_rate)} · net pnl: {formatValue(item.net_pnl)} · avg trade: {formatValue(item.expectancy_per_trade)}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='resultsRuntimeEmpty'>No regime breakdown is available for this result yet.</div>
                )}
            </SectionCard>

            <SectionCard title='Stability buckets'>
                {stabilitySummary.length ? (
                    <div className='resultsRuntimeList'>
                        {stabilitySummary.map((item, index) => (
                            <div key={`${item.bucket || 'bucket'}-${index}`} className='resultsRuntimeListItem'>
                                <div className='resultsRuntimeListTitle'>{formatValue(item.bucket)}</div>
                                <div className='resultsRuntimeListMeta'>
                                    trades: {formatValue(item.n_trades)} · avg stability: {formatValue(item.avg_stability)} · win rate: {formatPercent(item.win_rate)} · net pnl: {formatValue(item.net_pnl)}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='resultsRuntimeEmpty'>No stability breakdown is available for this result yet.</div>
                )}
            </SectionCard>

            <SectionCard title='Recent result reasons'>
                {recentReasons.length ? (
                    <div className='resultsRuntimeList'>
                        {recentReasons.map((item, index) => (
                            <div key={`${item.kind || 'entry'}-${index}`} className='resultsRuntimeListItem'>
                                <div className='resultsRuntimeListTitle'>
                                    {item.kind === 'invalidate'
                                        ? `Invalidated · ${formatValue(item.reason)}`
                                        : `${formatValue(item.mode)} refresh · ${formatValue(item.reason)}`}
                                </div>
                                <div className='resultsRuntimeListMeta'>
                                    at: {formatTimestamp(item.at)} · rows: {formatValue(item.rows)} · elapsed: {formatValue(item.elapsed_ms)} ms
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='resultsRuntimeEmpty'>No result runtime history recorded yet.</div>
                )}
            </SectionCard>

            <SectionCard title='Reason histogram'>
                {reasonHistogram.length ? (
                    <div className='resultsRuntimeList'>
                        {reasonHistogram.map((item) => (
                            <div key={item.label} className='resultsRuntimeListItem'>
                                <div className='resultsRuntimeListTitle'>{item.label}</div>
                                <div className='resultsRuntimeListMeta'>count: {formatValue(item.count)}</div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className='resultsRuntimeEmpty'>No invalidation patterns recorded yet.</div>
                )}
            </SectionCard>
        </div>
    )
}

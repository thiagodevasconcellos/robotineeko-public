import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Strategy } from './Console/Strategy'
import { Backtester } from './Console/Backtester'
import './Console.css'

const Results = lazy(() => import('./Console/Results').then((module) => ({ default: module.Results })))
const Batch = lazy(() => import('./Console/Batch').then((module) => ({ default: module.Batch })))
const Portfolios = lazy(() => import('./Console/Portfolios').then((module) => ({ default: module.Portfolios })))
const Brokers = lazy(() => import('./Console/Brokers').then((module) => ({ default: module.Brokers })))
const Research = lazy(() => import('./Console/Results').then((module) => ({ default: module.Research })))
const Neural = lazy(() => import('./Console/Neural').then((module) => ({ default: module.Neural })))
const Trade = lazy(() => import('./Console/Trade').then((module) => ({ default: module.Trade })))
const Runtime = lazy(() => import('./Console/Runtime').then((module) => ({ default: module.Runtime })))
const Docs = lazy(() => import('./Console/Docs').then((module) => ({ default: module.Docs })))

function ConsoleLazyFallback({ label = 'Loading panel...' }) {
    return (
        <div className='consoleLazyFallback'>
            <div className='consoleLazyFallbackCard'>
                <div className='consoleLazyFallbackTitle'>{label}</div>
                <div className='consoleLazyFallbackText'>Preparing the selected console panel.</div>
            </div>
        </div>
    )
}

function ConsoleLoadingCurtain({
    title = 'Loading console',
    detail = 'Preparing the selected tools.',
    workspaceReady = false,
    chartReady = false,
    showChartStatus = false,
}) {
    return (
        <div className='consoleLoadingCurtain' role='status' aria-live='polite'>
            <div className='consoleLoadingCurtainCard'>
                <div className='consoleLoadingCurtainSpinner' aria-hidden='true' />
                <div className='consoleLoadingCurtainText'>
                    <div className='consoleLoadingCurtainTitle'>{title}</div>
                    <div className='consoleLoadingCurtainDetail'>{detail}</div>
                    <div className='consoleLoadingCurtainStatusList'>
                        {showChartStatus ? (
                            <div className='consoleLoadingCurtainStatusRow'>
                                <span>Chart</span>
                                <strong className={chartReady ? 'is-ready' : ''}>{chartReady ? 'Ready' : 'Waiting'}</strong>
                            </div>
                        ) : null}
                        <div className='consoleLoadingCurtainStatusRow'>
                            <span>Workspace</span>
                            <strong className={workspaceReady ? 'is-ready' : ''}>{workspaceReady ? 'Ready' : 'Waiting'}</strong>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
}

function normalizeBacktestForComparison(backtest = null, fallbackMarketContext = null) {
    const payload = backtest && typeof backtest === 'object' ? backtest : {}
    const rawHistoryScopeMode = String(payload.historyScopeMode || payload.history_scope_mode || 'loaded_chart').trim().toLowerCase() || 'loaded_chart'
    const historyScopeMode = rawHistoryScopeMode === 'custom' ? 'custom' : 'loaded_chart'

    return {
        initialBalance: Number(payload.initialBalance ?? payload.initial_balance ?? 0),
        assetType: String(payload.assetType ?? payload.asset_type ?? '').trim().toLowerCase(),
        initialVolume: Number(payload.initialVolume ?? payload.initial_volume ?? 0),
        pipSize: Number(payload.pipSize ?? payload.pip_size ?? 0),
        pipValuePerLot: Number(payload.pipValuePerLot ?? payload.pip_value_per_lot ?? 0),
        costProfile: String(payload.costProfile ?? payload.cost_profile ?? '').trim().toLowerCase(),
        spreadInPips: Number(payload.spreadInPips ?? payload.spread_in_pips ?? 0),
        slippageInPips: Number(payload.slippageInPips ?? payload.slippage_in_pips ?? 0),
        entrySlippageInPips: Number(payload.entrySlippageInPips ?? payload.entry_slippage_in_pips ?? 0),
        closeSlippageInPips: Number(payload.closeSlippageInPips ?? payload.close_slippage_in_pips ?? 0),
        takeProfitSlippageInPips: Number(payload.takeProfitSlippageInPips ?? payload.take_profit_slippage_in_pips ?? 0),
        stopLossSlippageInPips: Number(payload.stopLossSlippageInPips ?? payload.stop_loss_slippage_in_pips ?? 0),
        trailingStopSlippageInPips: Number(payload.trailingStopSlippageInPips ?? payload.trailing_stop_slippage_in_pips ?? 0),
        minimumStopDistanceInPips: Number(payload.minimumStopDistanceInPips ?? payload.minimum_stop_distance_in_pips ?? 0),
        volatilitySlippageMultiplier: Number(payload.volatilitySlippageMultiplier ?? payload.volatility_slippage_multiplier ?? 0),
        executionMode: String(payload.executionMode ?? payload.execution_mode ?? 'next_bar_open').trim().toLowerCase() || 'next_bar_open',
        portfolioMode: String(payload.portfolioMode ?? payload.portfolio_mode ?? 'parallel_sleeves').trim().toLowerCase() || 'parallel_sleeves',
        brokerProfileId: String(payload.brokerProfileId ?? payload.broker_profile_id ?? '').trim(),
        brokerCode: String(payload.brokerCode ?? payload.broker_code ?? '').trim().toLowerCase(),
        brokerMarketDomain: String(payload.brokerMarketDomain ?? payload.broker_market_domain ?? payload.market_domain ?? '').trim().toLowerCase(),
        symbol: String(payload.symbol ?? payload.market_symbol ?? fallbackMarketContext?.symbol ?? '').trim().toUpperCase(),
        timeframe: String(payload.timeframe ?? payload.market_timeframe ?? fallbackMarketContext?.timeframe ?? '').trim().toUpperCase(),
        historyScopeMode,
        historyScopeBars: historyScopeMode === 'custom'
            ? Math.max(1, Number(payload.historyScopeBars ?? payload.history_scope_bars ?? 1) || 1)
            : null,
    }
}

export function Console({
    authToken = '',
    brokerProfiles = [],
    strategy,
    setStrategy,
    backtestStrategySet = [],
    setBacktestStrategySet,
    backtest,
    setBacktest,
    tradeState,
    setTradeState,
    liveTradeRuntime = null,
    setLiveTradeRuntime,
    appliedChartSettings,
    currentWorkspaceSaveName = '',
    onLoadStrategyIndicators,
    onLoadBacktestFlags,
    onBacktestExecuted,
    consoleStatusState,
    onStrategyStatusChange,
    onBacktestStatusChange,
    onNeuralStatusChange,
    lastBacktestResponse,
    hasBacktestChartBuffer = false,
    onHydrateBacktestResult,
    batchState,
    setBatchState,
    researchState,
    setResearchState,
    onLogEvent,
    hasStoredResultsCharts = false,
    onLoadStoredResultsCharts,
    onResolveLoadedBacktestResponse = null,
    onActiveStrategyFieldChange,
    strategyInsertRequest,
    systemLogHeight = 88,
    loadedChartCandles = 0,
    sharedConsoleJobs = null,
    onSharedConsoleJobChange,
    isWorkspaceReady = false,
    workspaceSocketStatus = 'connecting',
    onMaximizedChange = null,
    onBrokerProfilesChanged = null,
    currentUser = null,
    activeBrokerProfile = null,
}) {
    const isGuest = Boolean(currentUser?.is_guest)
    const [activeTerminal, setActiveTerminal] = useState(() => (isGuest ? 'Research' : 'Strategy'))
    const [currentStrategyLabel, setCurrentStrategyLabel] = useState('')
    const [hasLoadedBacktester, setHasLoadedBacktester] = useState(false)
    const [hasLoadedPortfolios, setHasLoadedPortfolios] = useState(false)
    const [hasLoadedBrokers, setHasLoadedBrokers] = useState(false)
    const [hasLoadedBatch, setHasLoadedBatch] = useState(false)
    const [hasLoadedResearch, setHasLoadedResearch] = useState(false)
    const [focusedResearchRunId, setFocusedResearchRunId] = useState('')
    const [isMinimized, setIsMinimized] = useState(false)
    const [isMaximized, setIsMaximized] = useState(false)
    const [consoleHeight, setConsoleHeight] = useState(450)
    const [isMobileLayout, setIsMobileLayout] = useState(() => (
        typeof window !== 'undefined' ? window.innerWidth <= 900 : false
    ))
    const lastExpandedHeightRef = useRef(consoleHeight)
    const resizeStateRef = useRef(null)
    const backtestJobRunning = sharedConsoleJobs?.backtest?.status === 'running'
    const activeBrokerProfileId = String(tradeState?.activeBrokerProfileId || '').trim()
    const activeBrokerProfileLabel = String(tradeState?.activeBrokerProfileLabel || '').trim()
    const resolvedActiveBrokerProfile = activeBrokerProfile
        || brokerProfiles.find((entry) => String(entry?.id || '').trim() === activeBrokerProfileId)
        || brokerProfiles.find((entry) => String(entry?.label || '').trim() === activeBrokerProfileLabel)
        || null

    useEffect(() => {
        onMaximizedChange?.(Boolean(isMaximized))
    }, [isMaximized, onMaximizedChange])

    useEffect(() => {
        if (typeof window === 'undefined') {
            return undefined
        }

        function handleViewportChange() {
            setIsMobileLayout(window.innerWidth <= 900)
        }

        handleViewportChange()
        window.addEventListener('resize', handleViewportChange)
        return () => window.removeEventListener('resize', handleViewportChange)
    }, [])

    useEffect(() => {
        if (isMinimized) {
            return
        }

        lastExpandedHeightRef.current = consoleHeight
    }, [consoleHeight, isMinimized])

    useEffect(() => {
        function handlePointerMove(event) {
            if (!resizeStateRef.current || isMaximized) {
                return
            }

            const nextHeight = Math.max(
                42,
                Math.min(window.innerHeight - 80, window.innerHeight - event.clientY)
            )

            setIsMinimized(false)
            setConsoleHeight(nextHeight)
        }

        function handlePointerUp() {
            resizeStateRef.current = null
            document.body.style.userSelect = ''
            document.body.style.cursor = ''
        }

        window.addEventListener('pointermove', handlePointerMove)
        window.addEventListener('pointerup', handlePointerUp)

        return () => {
            window.removeEventListener('pointermove', handlePointerMove)
            window.removeEventListener('pointerup', handlePointerUp)
        }
    }, [isMaximized])

    function handleResizeStart(event) {
        if (isMinimized || isMaximized) {
            return
        }

        event.preventDefault()
        event.stopPropagation()

        resizeStateRef.current = {
            pointerId: event.pointerId,
        }

        if (event.currentTarget?.setPointerCapture) {
            try {
                event.currentTarget.setPointerCapture(event.pointerId)
            } catch {
                // ignore capture failures on unsupported browsers/devices
            }
        }

        document.body.style.userSelect = 'none'
        document.body.style.cursor = 'ns-resize'
    }

    function handleTerminalChange(nextTerminal) {
        setActiveTerminal(nextTerminal)
        if (nextTerminal === 'Backtester') {
            setHasLoadedBacktester(true)
        }
        if (nextTerminal === 'Portfolios') {
            setHasLoadedPortfolios(true)
        }
        if (nextTerminal === 'Brokers') {
            setHasLoadedBrokers(true)
        }
        if (nextTerminal === 'Batch') {
            setHasLoadedBatch(true)
        }
        if (nextTerminal === 'Research') {
            setHasLoadedResearch(true)
        }
    }

    const latestRunBacktest = lastBacktestResponse?.request?.backtest ?? null
    const hasStrategyDebugError = Boolean(consoleStatusState?.strategyDebugError)
    const isStrategyDebugBusy = Boolean(consoleStatusState?.strategyDebugPending)
    const hasSuccessfulStrategyDebug = Boolean(consoleStatusState?.strategyDebugReady)
    const strategyStatusTone = hasStrategyDebugError
        ? 'error'
        : isStrategyDebugBusy
            ? 'warn'
            : hasSuccessfulStrategyDebug
                ? 'ok'
                : 'idle'

    const hasBacktestError = Boolean(consoleStatusState?.backtestError)
    const hasBacktestData = Number(lastBacktestResponse?.rows || 0) > 0
        || Boolean(lastBacktestResponse?.has_results)
        || (Array.isArray(lastBacktestResponse?.results) && lastBacktestResponse.results.length > 0)
    const currentBacktestSerialized = JSON.stringify(normalizeBacktestForComparison({
        ...backtest,
        brokerProfileId: activeBrokerProfileId,
        brokerProfileLabel: activeBrokerProfileLabel,
        brokerCode: resolvedActiveBrokerProfile?.broker_code,
        brokerMarketDomain: resolvedActiveBrokerProfile?.market_domain,
    }, appliedChartSettings))
    const appliedBacktestSerialized = JSON.stringify(normalizeBacktestForComparison(latestRunBacktest, {
        symbol: lastBacktestResponse?.request?.symbol,
        timeframe: lastBacktestResponse?.request?.timeframe,
    }))
    const isBacktestStale = Boolean(latestRunBacktest) && currentBacktestSerialized !== appliedBacktestSerialized
    const backtesterStatusTone = hasBacktestError
        ? 'error'
        : consoleStatusState?.backtestPending || backtestJobRunning
            ? 'warn'
            : hasBacktestChartBuffer
                ? 'ok'
                : 'idle'

    const hasResultsError = Boolean(consoleStatusState?.resultsError)
    const hasResults = hasBacktestData
    const resultsStatusTone = hasResults ? 'ok' : 'idle'
    const researchStatusTone = hasResults ? 'ok' : 'idle'
    const batchStatusTone = sharedConsoleJobs?.batch?.status === 'running'
        ? 'warn'
        : 'idle'
    const neuralStatusTone = consoleStatusState?.neuralPending
        ? 'warn'
        : consoleStatusState?.neuralReady
            ? 'ok'
            : 'idle'
    const effectiveTradeRuntime = liveTradeRuntime && typeof liveTradeRuntime === 'object'
        ? liveTradeRuntime
        : (tradeState?.runtime && typeof tradeState.runtime === 'object' ? tradeState.runtime : {})
    const tradeHasSleeveError = Array.isArray(effectiveTradeRuntime?.sleeve_states)
        ? effectiveTradeRuntime.sleeve_states.some((entry) => String(entry?.status || '').trim().toLowerCase() === 'error' || entry?.last_error)
        : false
    const tradeRuntimeHasError = Boolean(
        effectiveTradeRuntime?.last_error
        || effectiveTradeRuntime?.lastError
        || tradeHasSleeveError
    )
    const isTradeRuntimeArmed = Boolean(effectiveTradeRuntime?.armed)
    const isTradeLiveDispatchArmed = Boolean(
        effectiveTradeRuntime?.live_dispatch_armed || effectiveTradeRuntime?.liveDispatchArmed
    )
    const tradeRuntimeTone = tradeRuntimeHasError
        ? 'error'
        : isTradeRuntimeArmed && isTradeLiveDispatchArmed
            ? 'ok'
            : isTradeRuntimeArmed
                ? 'warn'
                : 'idle'
    const shouldShowBrokersTab = !isGuest
    const shouldShowResultsTab = !isGuest || hasResults
    const shouldShowBatchTab = !isGuest
    const shouldShowRuntimeTab = !isGuest
    const guestShowcaseTerminal = 'Research'
    const resolvedActiveTerminal = (
        (activeTerminal === 'Brokers' && !shouldShowBrokersTab)
        || (activeTerminal === 'Batch' && !shouldShowBatchTab)
        || (activeTerminal === 'Runtime' && !shouldShowRuntimeTab)
        || (activeTerminal === 'Results' && !shouldShowResultsTab)
    )
        ? guestShowcaseTerminal
        : activeTerminal
    const runtimeTone = hasResultsError
        ? 'error'
        : consoleStatusState?.strategyPending || consoleStatusState?.backtestPending || consoleStatusState?.neuralPending
            ? 'warn'
            : (loadedChartCandles > 0 || hasBacktestData || consoleStatusState?.neuralReady)
                ? 'ok'
                : 'idle'
    const isChartReady = loadedChartCandles > 0
    const terminalRequiresChart = ['Strategy', 'Backtester', 'Results', 'Research'].includes(resolvedActiveTerminal)
    const showConsoleLoadingCurtain = !isWorkspaceReady || (terminalRequiresChart && !isChartReady)
    const consoleLoadingTitle = isGuest
        ? (
            !isWorkspaceReady && terminalRequiresChart && !isChartReady
                ? 'Opening guest showcase'
                : !isWorkspaceReady
                    ? 'Preparing guest showcase'
                    : 'Loading market view'
        )
        : (
            !isWorkspaceReady && terminalRequiresChart && !isChartReady
                ? 'Waiting for workspace and chart'
                : !isWorkspaceReady
                    ? 'Waiting for workspace'
                    : 'Waiting for chart data'
        )
    const consoleLoadingDetail = isGuest
        ? (
            !isWorkspaceReady && terminalRequiresChart && !isChartReady
                ? 'Loading the temporary guest workspace and the curated chart before the portfolio panels unlock.'
                : !isWorkspaceReady
                    ? 'Preparing the temporary guest workspace and read-only showcase panels.'
                    : 'The guest chart is still loading. Showcase panels will unlock automatically when the market snapshot arrives.'
        )
        : (
            !isWorkspaceReady && terminalRequiresChart && !isChartReady
                ? 'The console is still syncing the workspace and the chart has not finished loading yet.'
                : !isWorkspaceReady
                    ? 'Syncing the current workspace state, saved backend state, and workspace resources before enabling this panel.'
                    : 'The chart is still loading. The panel structure is visible behind this curtain, and the tools will unlock automatically when the data arrives.'
        )
    const docsStatusTone = 'idle'

    function renderToolLabel(label, tone, options = {}) {
        const { icon = null, hideStatusDot = false } = options
        return (
            <>
                {hideStatusDot
                    ? icon
                    : <span className={`toolStatusDot is-${tone}`} aria-hidden='true' />
                }
                <span>{label}</span>
            </>
        )
    }

        return (
            <section
            id='Console'
            className={`${isMinimized ? 'isMinimized' : ''} ${isMaximized ? 'isMaximized' : ''} ${isMobileLayout ? 'isMobileLayout' : ''}`.trim()}
            style={{
                '--console-height': isMaximized
                    ? `calc(100vh - var(--header-height) - ${Math.max(72, Number(systemLogHeight) || 88)}px)`
                    : `${isMinimized ? 42 : consoleHeight}px`,
                '--console-log-height': `${Math.max(72, Number(systemLogHeight) || 88)}px`,
                height: isMaximized ? undefined : (isMinimized ? 42 : consoleHeight),
            }}
        >
            <div
                className='resizeHandle'
                onPointerDown={handleResizeStart}
                aria-hidden='true'
            />

            <div className='toolbar'>
                <div className='toolbarTabs'>
                    <div
                        className={`tool ${resolvedActiveTerminal === 'Strategy' ? 'active' : ''}`}
                        onClick={() => handleTerminalChange('Strategy')}
                    >
                        {renderToolLabel('Strategy', strategyStatusTone)}
                    </div>

                    <div
                        className={`tool ${resolvedActiveTerminal === 'Portfolios' ? 'active' : ''}`}
                        onClick={() => handleTerminalChange('Portfolios')}
                    >
                        {renderToolLabel('Portfolios', 'idle')}
                    </div>

                    {shouldShowBrokersTab ? (
                        <div
                            className={`tool ${resolvedActiveTerminal === 'Brokers' ? 'active' : ''}`}
                            onClick={() => handleTerminalChange('Brokers')}
                        >
                            {renderToolLabel('Brokers', activeBrokerProfileId ? 'ok' : 'idle')}
                        </div>
                    ) : null}

                    <div
                        className={`tool ${resolvedActiveTerminal === 'Backtester' ? 'active' : ''}`}
                        onClick={() => handleTerminalChange('Backtester')}
                    >
                        {renderToolLabel('Backtester', backtesterStatusTone)}
                    </div>

                    {shouldShowResultsTab ? (
                        <div
                            className={`tool ${resolvedActiveTerminal === 'Results' ? 'active' : ''}`}
                            onClick={() => handleTerminalChange('Results')}
                        >
                            {renderToolLabel('Results', resultsStatusTone)}
                        </div>
                    ) : null}

                    <div
                        className={`tool ${resolvedActiveTerminal === 'Research' ? 'active' : ''}`}
                        onClick={() => handleTerminalChange('Research')}
                    >
                        {renderToolLabel('Research', researchStatusTone)}
                    </div>

                    {shouldShowBatchTab ? (
                        <div
                            className={`tool ${resolvedActiveTerminal === 'Batch' ? 'active' : ''}`}
                            onClick={() => handleTerminalChange('Batch')}
                        >
                            {renderToolLabel('Batch', batchStatusTone)}
                        </div>
                    ) : null}

                    <div
                        className={`tool ${resolvedActiveTerminal === 'Neural' ? 'active' : ''}`}
                        onClick={() => {
                            handleTerminalChange('Neural')
                            if (consoleStatusState?.neuralReady) {
                                onNeuralStatusChange?.({ neuralReady: false })
                            }
                        }}
                    >
                        {renderToolLabel('Neural', neuralStatusTone)}
                    </div>

                    <div
                        className={`tool ${resolvedActiveTerminal === 'Trade' ? 'active' : ''}`}
                        onClick={() => handleTerminalChange('Trade')}
                    >
                        {renderToolLabel('Trader', tradeRuntimeTone)}
                    </div>

                    {shouldShowRuntimeTab ? (
                        <div
                            className={`tool ${resolvedActiveTerminal === 'Runtime' ? 'active' : ''}`}
                            onClick={() => handleTerminalChange('Runtime')}
                        >
                            {renderToolLabel('Runtime', runtimeTone, {
                                hideStatusDot: true,
                                icon: (
                                    <span className='toolRuntimeIcon' aria-hidden='true'>
                                        <span className='toolRuntimeIconPane pane-1' />
                                        <span className='toolRuntimeIconPane pane-2' />
                                        <span className='toolRuntimeIconPane pane-3' />
                                        <span className='toolRuntimeIconPulse' />
                                    </span>
                                ),
                            })}
                        </div>
                    ) : null}

                    <div
                        className={`tool ${resolvedActiveTerminal === 'Docs' ? 'active' : ''}`}
                        onClick={() => handleTerminalChange('Docs')}
                    >
                        {renderToolLabel('Docs', docsStatusTone, {
                            hideStatusDot: true,
                            icon: (
                                <span className='toolDocIcon' aria-hidden='true'>
                                    <span className='toolDocIconFold' />
                                    <span className='toolDocIconLine line-1' />
                                    <span className='toolDocIconLine line-2' />
                                    <span className='toolDocIconLine line-3' />
                                </span>
                            ),
                        })}
                    </div>

                </div>

                <button
                    type='button'
                    className='maximizeConsole'
                    onClick={() => {
                        if (isMaximized) {
                            setIsMaximized(false)
                            return
                        }

                        if (!isMinimized) {
                            lastExpandedHeightRef.current = consoleHeight
                        }
                        setIsMinimized(false)
                        setIsMaximized(true)
                    }}
                    aria-label={isMaximized ? 'Restore console size' : 'Maximize console'}
                    title={isMaximized ? 'Restore console size' : 'Maximize console'}
                >
                    <span className={`maximizeConsoleIcon ${isMaximized ? 'restore' : 'maximize'}`} aria-hidden='true' />
                </button>

                <button
                    type='button'
                    className='toggleConsole'
                    onClick={() => {
                        if (isMaximized) {
                            setIsMaximized(false)
                        }
                        const nextMinimized = !isMinimized
                        const nextHeight = nextMinimized ? consoleHeight : lastExpandedHeightRef.current
                        setIsMinimized(nextMinimized)
                        setConsoleHeight(nextHeight)
                    }}
                    aria-label={isMinimized ? 'Expand console panel' : 'Minimize console panel'}
                    title={isMinimized ? 'Expand console panel' : 'Minimize console panel'}
                >
                    <span className={`chevron ${isMinimized ? 'up' : 'down'}`} aria-hidden='true' />
                </button>
            </div>

            <div className='terminal' hidden={isMinimized}>
                {resolvedActiveTerminal === 'Strategy' && (
                    <Strategy
                        authToken={authToken}
                        isGuest={isGuest}
                        strategy={strategy}
                        setStrategy={setStrategy}
                        currentStrategyLabel={currentStrategyLabel}
                        onStrategyLabelChange={setCurrentStrategyLabel}
                        chartSettings={appliedChartSettings}
                        backtest={backtest}
                        setBacktest={setBacktest}
                        backtestStrategySet={backtestStrategySet}
                        setBacktestStrategySet={setBacktestStrategySet}
                        lastBacktestResponse={lastBacktestResponse}
                        onLoadStrategyIndicators={onLoadStrategyIndicators}
                        onStrategyStatusChange={onStrategyStatusChange}
                        onLogEvent={onLogEvent}
                        onActiveStrategyFieldChange={onActiveStrategyFieldChange}
                        insertRequest={strategyInsertRequest}
                        isBusy={Boolean(consoleStatusState?.strategyPending)}
                        isActive={true}
                        activeBrokerProfileId={activeBrokerProfileId}
                        activeBrokerProfileLabel={activeBrokerProfileLabel}
                    />
                )}

                {(resolvedActiveTerminal === 'Portfolios' || hasLoadedPortfolios) && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Portfolios...' />}>
                        <div
                            className={`consolePanel portfoliosPanel ${resolvedActiveTerminal === 'Portfolios' ? 'active' : 'inactive'}`}
                            style={{ display: resolvedActiveTerminal === 'Portfolios' ? 'flex' : 'none' }}
                            aria-hidden={resolvedActiveTerminal !== 'Portfolios'}
                        >
                            <Portfolios
                                isActive={resolvedActiveTerminal === 'Portfolios'}
                                authToken={authToken}
                                currentUser={currentUser}
                                onLogEvent={onLogEvent}
                                activeBrokerProfileId={activeBrokerProfileId}
                                activeBrokerProfileLabel={activeBrokerProfileLabel}
                            />
                        </div>
                    </Suspense>
                )}

                {shouldShowBrokersTab && (resolvedActiveTerminal === 'Brokers' || hasLoadedBrokers) && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Brokers...' />}>
                        <div
                            className={`consolePanel portfoliosPanel ${resolvedActiveTerminal === 'Brokers' ? 'active' : 'inactive'}`}
                            style={{ display: resolvedActiveTerminal === 'Brokers' ? 'flex' : 'none' }}
                            aria-hidden={resolvedActiveTerminal !== 'Brokers'}
                        >
                            <Brokers
                                isActive={resolvedActiveTerminal === 'Brokers'}
                                authToken={authToken}
                                isGuest={isGuest}
                                tradeState={tradeState}
                                setTradeState={setTradeState}
                                onProfilesChanged={onBrokerProfilesChanged}
                                onLogEvent={onLogEvent}
                            />
                        </div>
                    </Suspense>
                )}

                {(resolvedActiveTerminal === 'Backtester' || hasLoadedBacktester || backtestJobRunning) && (
                    <div
                        className={`consolePanel backtesterPanel ${resolvedActiveTerminal === 'Backtester' ? 'active' : 'inactive'}`}
                        style={{ display: resolvedActiveTerminal === 'Backtester' ? 'flex' : 'none' }}
                        aria-hidden={resolvedActiveTerminal !== 'Backtester'}
                    >
                        <Backtester
                            authToken={authToken}
                            backtest={backtest}
                            setBacktest={setBacktest}
                            strategySetEntries={backtestStrategySet}
                            setStrategySetEntries={setBacktestStrategySet}
                            chartSettings={appliedChartSettings}
                            lastBacktestResponse={lastBacktestResponse}
                            onBacktestExecuted={onBacktestExecuted}
                            onHydrateBacktestResult={onHydrateBacktestResult}
                            onBacktestStatusChange={onBacktestStatusChange}
                            onLoadStrategyIndicators={onLoadStrategyIndicators}
                            onLoadBacktestFlags={onLoadBacktestFlags}
                            onLogEvent={onLogEvent}
                            isBusy={Boolean(consoleStatusState?.strategyPending || consoleStatusState?.backtestBusy)}
                            isActive={resolvedActiveTerminal === 'Backtester'}
                            loadedChartCandles={loadedChartCandles}
                            isStale={isBacktestStale}
                            hasBacktestChartBuffer={hasBacktestChartBuffer}
                            sharedConsoleJobs={sharedConsoleJobs}
                            onSharedConsoleJobChange={onSharedConsoleJobChange}
                            isGuest={isGuest}
                            activeBrokerProfileId={activeBrokerProfileId}
                            activeBrokerProfileLabel={activeBrokerProfileLabel}
                            activeBrokerProfile={resolvedActiveBrokerProfile}
                        />
                    </div>
                )}

                {shouldShowResultsTab && resolvedActiveTerminal === 'Results' && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Results...' />}>
                        <Results
                            isActive={true}
                            backtestResponse={lastBacktestResponse}
                            authToken={authToken}
                            isGuest={isGuest}
                            chartSettings={appliedChartSettings}
                            setStrategy={setStrategy}
                            setStrategySetEntries={setBacktestStrategySet}
                            onOpenStrategy={() => handleTerminalChange('Strategy')}
                            onLogEvent={onLogEvent}
                            canLoadStoredCharts={Boolean(lastBacktestResponse?.summary_only && hasStoredResultsCharts)}
                            onLoadStoredCharts={onLoadStoredResultsCharts}
                            onResolveLoadedBacktestResponse={onResolveLoadedBacktestResponse}
                        />
                    </Suspense>
                )}

                {resolvedActiveTerminal === 'Neural' && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Neural...' />}>
                        <Neural
                            authToken={authToken}
                            isGuest={isGuest}
                            hasUnreadCompletion={Boolean(consoleStatusState?.neuralReady)}
                            onStatusChange={onNeuralStatusChange}
                            onLogEvent={onLogEvent}
                            isConsoleMaximized={isMaximized}
                            isActive={true}
                        />
                    </Suspense>
                )}

                {resolvedActiveTerminal === 'Trade' && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Trader...' />}>
                        <Trade
                            isActive={true}
                            authToken={authToken}
                            isGuest={isGuest}
                            tradeState={tradeState}
                            setTradeState={setTradeState}
                            liveTradeRuntime={liveTradeRuntime}
                            setLiveTradeRuntime={setLiveTradeRuntime}
                            chartSettings={appliedChartSettings}
                            onLogEvent={onLogEvent}
                            activeBrokerProfileId={activeBrokerProfileId}
                            activeBrokerProfileLabel={activeBrokerProfileLabel}
                        />
                    </Suspense>
                )}

                {shouldShowBatchTab && (resolvedActiveTerminal === 'Batch' || hasLoadedBatch) && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Batch...' />}>
                        <div
                            className={`consolePanel researchPanel ${resolvedActiveTerminal === 'Batch' ? 'active' : 'inactive'}`}
                            style={{ display: resolvedActiveTerminal === 'Batch' ? 'flex' : 'none' }}
                            aria-hidden={resolvedActiveTerminal !== 'Batch'}
                        >
                            <Batch
                                isActive={resolvedActiveTerminal === 'Batch'}
                                authToken={authToken}
                                workspaceSocketStatus={workspaceSocketStatus}
                                batchState={batchState}
                                setBatchState={setBatchState}
                                onLogEvent={onLogEvent}
                                sharedConsoleJobs={sharedConsoleJobs}
                                onSharedConsoleJobChange={onSharedConsoleJobChange}
                                onHydrateBacktestResult={onHydrateBacktestResult}
                                onOpenResults={() => handleTerminalChange('Results')}
                                onOpenBacktester={() => handleTerminalChange('Backtester')}
                                onOpenResearchRun={(runId) => {
                                    setFocusedResearchRunId(String(runId || ''))
                                    handleTerminalChange('Research')
                                }}
                            />
                        </div>
                    </Suspense>
                )}

                {(resolvedActiveTerminal === 'Research' || hasLoadedResearch) && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Research...' />}>
                        <div
                            className={`consolePanel researchPanel ${resolvedActiveTerminal === 'Research' ? 'active' : 'inactive'}`}
                            style={{ display: resolvedActiveTerminal === 'Research' ? 'flex' : 'none' }}
                            aria-hidden={resolvedActiveTerminal !== 'Research'}
                        >
                            <Research
                                isActive={resolvedActiveTerminal === 'Research'}
                                backtestResponse={lastBacktestResponse}
                                authToken={authToken}
                                isGuest={isGuest}
                                workspaceSocketStatus={workspaceSocketStatus}
                                chartSettings={appliedChartSettings}
                                currentWorkspaceSaveName={currentWorkspaceSaveName}
                                researchState={researchState}
                                setResearchState={setResearchState}
                                setStrategy={setStrategy}
                                setStrategySetEntries={setBacktestStrategySet}
                                setBacktest={setBacktest}
                                onOpenStrategy={() => handleTerminalChange('Strategy')}
                                onHydrateBacktestResult={onHydrateBacktestResult}
                                onOpenResults={() => handleTerminalChange('Results')}
                                onLogEvent={onLogEvent}
                                sharedConsoleJobs={sharedConsoleJobs}
                                onSharedConsoleJobChange={onSharedConsoleJobChange}
                                externalSelectedArchiveRunId={focusedResearchRunId}
                            />
                        </div>
                    </Suspense>
                )}

                {shouldShowRuntimeTab && resolvedActiveTerminal === 'Runtime' && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Runtime...' />}>
                        <Runtime
                            authToken={authToken}
                            isActive={true}
                        />
                    </Suspense>
                )}

                {resolvedActiveTerminal === 'Docs' && (
                    <Suspense fallback={<ConsoleLazyFallback label='Loading Docs...' />}>
                        <Docs
                            authToken={authToken}
                            isActive={true}
                        />
                    </Suspense>
                )}

                {showConsoleLoadingCurtain ? (
                    <ConsoleLoadingCurtain
                        title={consoleLoadingTitle}
                        detail={consoleLoadingDetail}
                        workspaceReady={isWorkspaceReady}
                        chartReady={isChartReady}
                        showChartStatus={terminalRequiresChart}
                    />
                ) : null}
            </div>
        </section>
    )
}

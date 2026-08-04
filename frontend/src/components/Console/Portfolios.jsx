import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, fetchWithServerRetry, readJsonResponse } from '../../api'
import { TIMEFRAME_OPTIONS } from '../../utils/timeframes.js'
import {
    buildSavedPortfolioEntriesFromBenchmark,
    buildSavedPortfolioId,
    buildSavedPortfolioPipelineId,
    cloneSerializable,
    normalizeMarketValue,
    normalizeSavedPortfolioDefinition,
    normalizeSavedPortfolioMode,
    normalizeSavedPortfolioRecord,
    normalizeSavedPortfolioVolumeMode,
    summarizeSavedPortfolio,
} from '../../utils/portfolioLibrary.js'
import { buildBrokerProfileQuery } from '../../utils/brokerProfiles.js'
import './Portfolios.css'

const STRATEGY_LIBRARY_FETCH_LIMIT = 500
const SAVED_PORTFOLIO_FETCH_LIMIT = 500

function buildEmptyPortfolioDraft() {
    const portfolioId = buildSavedPortfolioId('portfolio', 0)
    return normalizeSavedPortfolioRecord({
        id: '',
        label: '',
        source: '',
        notes: '',
        is_favorite: false,
        capitalModel: {},
        portfolio: {
            id: portfolioId,
            label: 'Nova carteira',
            enabled: true,
            capitalMode: 'equity_percent',
            capitalValue: null,
            rebalanceMode: 'static',
            pipelines: [
                {
                    id: buildSavedPortfolioPipelineId(portfolioId, 0),
                    label: 'Pipeline 1',
                    enabled: true,
                    portfolioMode: 'parallel_sleeves',
                    entries: [],
                },
            ],
        },
    })
}

function formatCountLabel(count, singular, plural) {
    return `${count} ${count === 1 ? singular : plural}`
}

export function Portfolios({
    isActive = false,
    authToken = '',
    currentUser = null,
    onLogEvent,
    activeBrokerProfileId = '',
}) {
    const [savedPortfolioItems, setSavedPortfolioItems] = useState([])
    const [strategyLibraryItems, setStrategyLibraryItems] = useState([])
    const [isLoadingSavedPortfolios, setIsLoadingSavedPortfolios] = useState(false)
    const [isLoadingStrategies, setIsLoadingStrategies] = useState(false)
    const [savedPortfolioLoadError, setSavedPortfolioLoadError] = useState('')
    const [strategyLibraryLoadError, setStrategyLibraryLoadError] = useState('')
    const [savedPortfolioListTab, setSavedPortfolioListTab] = useState('all')
    const [strategyLibraryListTab, setStrategyLibraryListTab] = useState('all')
    const [savedPortfolioQuery, setSavedPortfolioQuery] = useState('')
    const [strategyLibraryQuery, setStrategyLibraryQuery] = useState('')
    const [selectedSavedPortfolioId, setSelectedSavedPortfolioId] = useState('')
    const [selectedStrategyLibraryId, setSelectedStrategyLibraryId] = useState('')
    const [selectedPipelineId, setSelectedPipelineId] = useState('')
    const [draft, setDraft] = useState(() => buildEmptyPortfolioDraft())
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [requestError, setRequestError] = useState('')
    const guestRestrictionMessage = 'Guest demo pode inspecionar carteiras, mas não pode salvar, excluir nem alterar a biblioteca.'
    const isGuest = Boolean(currentUser?.is_guest)
    const authHeaders = useMemo(
        () => authToken
            ? { Authorization: `Bearer ${authToken}` }
            : {},
        [authToken],
    )
    const logEventRef = useRef(onLogEvent)
    const previousIsActiveRef = useRef(Boolean(isActive))
    const lastLibraryBootstrapKeyRef = useRef('')

    useEffect(() => {
        logEventRef.current = onLogEvent
    }, [onLogEvent])

    const visibleSavedPortfolioItems = useMemo(() => {
        const normalizedQuery = String(savedPortfolioQuery || '').trim().toLowerCase()
        const base = savedPortfolioListTab === 'favorites'
            ? savedPortfolioItems.filter((entry) => Boolean(entry?.is_favorite))
            : savedPortfolioItems
        return base.filter((entry) => {
            if (!normalizedQuery) {
                return true
            }
            const haystack = [
                entry?.label,
                entry?.source,
                entry?.notes,
                entry?.portfolio?.label,
                ...(Array.isArray(entry?.portfolio?.pipelines)
                    ? entry.portfolio.pipelines.flatMap((pipeline) => [
                        pipeline?.label,
                        ...(Array.isArray(pipeline?.entries)
                            ? pipeline.entries.flatMap((item) => [item?.label, item?.symbol, item?.timeframe, item?.sourceBenchmarkLabel])
                            : []),
                    ])
                    : []),
            ]
                .map((value) => String(value || '').trim().toLowerCase())
                .filter(Boolean)
                .join(' ')
            return haystack.includes(normalizedQuery)
        })
    }, [savedPortfolioItems, savedPortfolioListTab, savedPortfolioQuery])

    const visibleStrategyLibraryItems = useMemo(() => {
        const normalizedQuery = String(strategyLibraryQuery || '').trim().toLowerCase()
        const base = strategyLibraryListTab === 'favorites'
            ? strategyLibraryItems.filter((entry) => Boolean(entry?.is_favorite))
            : strategyLibraryItems
        return base.filter((entry) => {
            if (!normalizedQuery) {
                return true
            }
            const haystack = [
                entry?.label,
                entry?.source,
                entry?.notes,
                entry?.side,
                entry?.symbol,
                entry?.timeframe,
                ...(Array.isArray(entry?.strategies)
                    ? entry.strategies.flatMap((item) => [item?.label, item?.symbol, item?.timeframe])
                    : []),
            ]
                .map((value) => String(value || '').trim().toLowerCase())
                .filter(Boolean)
                .join(' ')
            return haystack.includes(normalizedQuery)
        })
    }, [strategyLibraryItems, strategyLibraryListTab, strategyLibraryQuery])

    const selectedSavedPortfolioItem = visibleSavedPortfolioItems.find((entry) => String(entry?.id) === String(selectedSavedPortfolioId)) || null
    const selectedStrategyLibraryItem = visibleStrategyLibraryItems.find((entry) => String(entry?.id) === String(selectedStrategyLibraryId)) || null
    const selectedPipeline = useMemo(() => (
        (Array.isArray(draft?.portfolio?.pipelines) ? draft.portfolio.pipelines : []).find((pipeline) => String(pipeline?.id) === String(selectedPipelineId))
        || draft?.portfolio?.pipelines?.[0]
        || null
    ), [draft?.portfolio?.pipelines, selectedPipelineId])

    const loadSavedPortfolios = useCallback(async ({ quiet = false } = {}) => {
        if (!authToken) {
            setSavedPortfolioItems([])
            setSelectedSavedPortfolioId('')
            setSavedPortfolioLoadError('')
            return
        }
        if (!quiet) {
            setIsLoadingSavedPortfolios(true)
        }
        try {
            const response = await fetchWithServerRetry(
                buildApiUrl(`/workspace/saved-portfolios?${buildBrokerProfileQuery({
                    workspaceId: 'default',
                    limit: SAVED_PORTFOLIO_FETCH_LIMIT,
                    brokerProfileId: activeBrokerProfileId,
                })}`),
                { headers: authHeaders },
                {
                    attempts: 4,
                    retryDelayMs: 750,
                },
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Falha ao carregar carteiras salvas.'))
            }
            const nextItems = Array.isArray(data?.portfolios)
                ? data.portfolios.map((entry, index) => normalizeSavedPortfolioRecord(entry, index))
                : []
            setSavedPortfolioItems(nextItems)
            setSavedPortfolioLoadError('')
            setSelectedSavedPortfolioId((current) => {
                if (current && nextItems.some((entry) => String(entry?.id) === String(current))) {
                    return current
                }
                return String(nextItems[0]?.id || '')
            })
        } catch (error) {
            const message = error?.message || 'Falha ao carregar carteiras salvas.'
            setSavedPortfolioLoadError(message)
            logEventRef.current?.(`Carteiras · ${message}`)
        } finally {
            if (!quiet) {
                setIsLoadingSavedPortfolios(false)
            }
        }
    }, [activeBrokerProfileId, authHeaders, authToken])

    const loadStrategyLibrary = useCallback(async ({ quiet = false } = {}) => {
        if (!authToken) {
            setStrategyLibraryItems([])
            setSelectedStrategyLibraryId('')
            setStrategyLibraryLoadError('')
            return
        }
        if (!quiet) {
            setIsLoadingStrategies(true)
        }
        try {
            const response = await fetchWithServerRetry(
                buildApiUrl(`/workspace/strategy-benchmarks?${buildBrokerProfileQuery({
                    workspaceId: 'default',
                    limit: STRATEGY_LIBRARY_FETCH_LIMIT,
                    brokerProfileId: activeBrokerProfileId,
                })}`),
                { headers: authHeaders },
                {
                    attempts: 4,
                    retryDelayMs: 750,
                },
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Falha ao carregar estratégias salvas.'))
            }
            const nextItems = Array.isArray(data?.benchmarks) ? data.benchmarks : []
            setStrategyLibraryItems(nextItems)
            setStrategyLibraryLoadError('')
            setSelectedStrategyLibraryId((current) => {
                if (current && nextItems.some((entry) => String(entry?.id) === String(current))) {
                    return current
                }
                return String(nextItems[0]?.id || '')
            })
        } catch (error) {
            const message = error?.message || 'Falha ao carregar estratégias salvas.'
            setStrategyLibraryLoadError(message)
            logEventRef.current?.(`Carteiras · ${message}`)
        } finally {
            if (!quiet) {
                setIsLoadingStrategies(false)
            }
        }
    }, [activeBrokerProfileId, authHeaders, authToken])

    useEffect(() => {
        const bootstrapKey = authToken
            ? `token:${authToken}:broker:${activeBrokerProfileId || 'all'}`
            : `anonymous:broker:${activeBrokerProfileId || 'all'}`
        if (lastLibraryBootstrapKeyRef.current === bootstrapKey) {
            return
        }
        lastLibraryBootstrapKeyRef.current = bootstrapKey
        void loadSavedPortfolios({ quiet: false })
        void loadStrategyLibrary({ quiet: false })
    }, [activeBrokerProfileId, authToken, loadSavedPortfolios, loadStrategyLibrary])

    useEffect(() => {
        const safeIsActive = Boolean(isActive)
        const becameActive = safeIsActive && !previousIsActiveRef.current
        previousIsActiveRef.current = safeIsActive
        if (!becameActive) {
            return
        }
        void loadSavedPortfolios({ quiet: true })
        void loadStrategyLibrary({ quiet: true })
    }, [isActive, loadSavedPortfolios, loadStrategyLibrary])

    useEffect(() => {
        if (!selectedPipelineId && draft?.portfolio?.pipelines?.length) {
            setSelectedPipelineId(String(draft.portfolio.pipelines[0].id || ''))
        }
    }, [draft?.portfolio?.pipelines, selectedPipelineId])

    function setDraftFromRecord(record) {
        const normalized = normalizeSavedPortfolioRecord(record)
        setDraft(normalized)
        setSelectedPipelineId(String(normalized?.portfolio?.pipelines?.[0]?.id || ''))
        setRequestError('')
    }

    function updateDraft(mutator) {
        setDraft((current) => {
            const next = typeof mutator === 'function'
                ? mutator(cloneSerializable(current, current))
                : current
            return normalizeSavedPortfolioRecord(next)
        })
    }

    function handleCreateNewDraft() {
        setDraft(buildEmptyPortfolioDraft())
        setSelectedSavedPortfolioId('')
        setSelectedPipelineId('')
        setRequestError('')
        logEventRef.current?.('Carteiras · Iniciada uma nova carteira.')
    }

    function handleLoadSelectedSavedPortfolio() {
        if (!selectedSavedPortfolioItem) {
            logEventRef.current?.('Carteiras · Selecione uma carteira salva primeiro.')
            return
        }
        setDraftFromRecord(selectedSavedPortfolioItem)
        logEventRef.current?.(`Carteiras · Carregada "${selectedSavedPortfolioItem.label || 'carteira salva'}" no editor.`)
    }

    async function handleToggleFavoriteSavedPortfolio(targetEntry = selectedSavedPortfolioItem) {
        if (isGuest) {
            setRequestError(guestRestrictionMessage)
            logEventRef.current?.(`Carteiras · ${guestRestrictionMessage}`)
            return
        }
        if (!authToken || !targetEntry?.id) {
            return
        }
        try {
            const response = await fetchWithServerRetry(
                buildApiUrl(`/workspace/saved-portfolios/${targetEntry.id}?workspace_id=default`),
                {
                    method: 'PATCH',
                    headers: {
                        ...authHeaders,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        workspace_id: 'default',
                        is_favorite: !targetEntry?.is_favorite,
                    }),
                },
                {
                    attempts: 3,
                    retryDelayMs: 750,
                },
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Falha ao atualizar favorito da carteira.'))
            }
            await loadSavedPortfolios({ quiet: true })
        } catch (error) {
            setRequestError(error?.message || 'Falha ao atualizar favorito da carteira.')
        }
    }

    async function handleSaveDraft({ forceCreate = false } = {}) {
        if (isGuest) {
            setRequestError(guestRestrictionMessage)
            logEventRef.current?.(`Carteiras · ${guestRestrictionMessage}`)
            return
        }
        if (!authToken) {
            return
        }
        const normalizedDraft = normalizeSavedPortfolioRecord(draft)
        const label = String(normalizedDraft.label || normalizedDraft.portfolio.label || '').trim()
        if (!label) {
            setRequestError('Defina um nome para a carteira antes de salvar.')
            return
        }
        if (!(Array.isArray(normalizedDraft.portfolio?.pipelines) && normalizedDraft.portfolio.pipelines.length)) {
            setRequestError('Adicione pelo menos um pipeline antes de salvar a carteira.')
            return
        }
        setIsSubmitting(true)
        setRequestError('')
        try {
            const method = forceCreate || !normalizedDraft.id ? 'POST' : 'PATCH'
            const url = method === 'POST'
                ? buildApiUrl('/workspace/saved-portfolios')
                : buildApiUrl(`/workspace/saved-portfolios/${normalizedDraft.id}`)
            const response = await fetchWithServerRetry(url, {
                method,
                headers: {
                    ...authHeaders,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label,
                    source: normalizedDraft.source,
                    notes: normalizedDraft.notes,
                    is_favorite: normalizedDraft.is_favorite,
                    broker_profile_id: activeBrokerProfileId || undefined,
                    portfolio: normalizedDraft.portfolio,
                    capitalModel: normalizedDraft.capitalModel,
                }),
            }, {
                attempts: 3,
                retryDelayMs: 750,
            })
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Falha ao salvar a carteira.'))
            }
            const savedRecord = normalizeSavedPortfolioRecord(data?.portfolio || {})
            setDraftFromRecord(savedRecord)
            setSelectedSavedPortfolioId(String(savedRecord.id || ''))
            await loadSavedPortfolios({ quiet: true })
            logEventRef.current?.(
                method === 'POST'
                    ? `Carteiras · Criada "${savedRecord.label || 'carteira'}".`
                    : `Carteiras · Atualizada "${savedRecord.label || 'carteira'}".`
            )
        } catch (error) {
            setRequestError(error?.message || 'Falha ao salvar a carteira.')
        } finally {
            setIsSubmitting(false)
        }
    }

    async function handleDeleteSelectedSavedPortfolio() {
        if (isGuest) {
            setRequestError(guestRestrictionMessage)
            logEventRef.current?.(`Carteiras · ${guestRestrictionMessage}`)
            return
        }
        if (!authToken || !selectedSavedPortfolioItem?.id) {
            return
        }
        const confirmed = window.confirm(`Excluir a carteira "${selectedSavedPortfolioItem.label || selectedSavedPortfolioItem.id}"?`)
        if (!confirmed) {
            return
        }
        setIsSubmitting(true)
        setRequestError('')
        try {
            const response = await fetchWithServerRetry(
                buildApiUrl(`/workspace/saved-portfolios/${selectedSavedPortfolioItem.id}?workspace_id=default`),
                {
                    method: 'DELETE',
                    headers: authHeaders,
                },
                {
                    attempts: 3,
                    retryDelayMs: 750,
                },
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Falha ao excluir a carteira.'))
            }
            await loadSavedPortfolios({ quiet: true })
            if (String(draft?.id || '') === String(selectedSavedPortfolioItem.id)) {
                handleCreateNewDraft()
            }
            logEventRef.current?.(`Carteiras · Excluída "${selectedSavedPortfolioItem.label || 'carteira'}".`)
        } catch (error) {
            setRequestError(error?.message || 'Falha ao excluir a carteira.')
        } finally {
            setIsSubmitting(false)
        }
    }

    function handleAddPipeline() {
        updateDraft((current) => {
            const next = cloneSerializable(current, current)
            const portfolio = normalizeSavedPortfolioDefinition(next.portfolio)
            const nextPipeline = {
                id: buildSavedPortfolioPipelineId(portfolio.id, portfolio.pipelines.length),
                label: `Pipeline ${portfolio.pipelines.length + 1}`,
                enabled: true,
                portfolioMode: 'parallel_sleeves',
                entries: [],
            }
            portfolio.pipelines = [...portfolio.pipelines, nextPipeline]
            next.portfolio = portfolio
            return next
        })
    }

    function handleRemovePipeline(pipelineId) {
        updateDraft((current) => {
            const next = cloneSerializable(current, current)
            const portfolio = normalizeSavedPortfolioDefinition(next.portfolio)
            portfolio.pipelines = portfolio.pipelines.filter((pipeline) => String(pipeline.id) !== String(pipelineId))
            if (!portfolio.pipelines.length) {
                portfolio.pipelines = [{
                    id: buildSavedPortfolioPipelineId(portfolio.id, 0),
                    label: 'Pipeline 1',
                    enabled: true,
                    portfolioMode: 'parallel_sleeves',
                    entries: [],
                }]
            }
            next.portfolio = portfolio
            return next
        })
        if (String(selectedPipelineId) === String(pipelineId)) {
            setSelectedPipelineId(String(draft?.portfolio?.pipelines?.[0]?.id || ''))
        }
    }

    function handleUpdatePipeline(pipelineId, field, value) {
        updateDraft((current) => {
            const next = cloneSerializable(current, current)
            next.portfolio.pipelines = (next.portfolio.pipelines || []).map((pipeline) => {
                if (String(pipeline.id) !== String(pipelineId)) {
                    return pipeline
                }
                return {
                    ...pipeline,
                    [field]: field === 'portfolioMode'
                        ? normalizeSavedPortfolioMode(value)
                        : value,
                }
            })
            return next
        })
    }

    function handleUpdatePortfolioField(field, value) {
        updateDraft((current) => {
            const next = cloneSerializable(current, current)
            if (field === 'label') {
                next.label = value
                next.portfolio.label = String(value || '').trim() || next.portfolio.label
            } else if (field === 'notes' || field === 'source') {
                next[field] = value
            } else if (field === 'is_favorite') {
                next.is_favorite = Boolean(value)
            } else if (field === 'capitalMode' || field === 'rebalanceMode') {
                next.portfolio[field] = value
            } else if (field === 'capitalValue') {
                const numeric = Number(value)
                next.portfolio.capitalValue = Number.isFinite(numeric) && numeric > 0 ? numeric : null
            }
            return next
        })
    }

    function handleAddSelectedStrategyToPipeline() {
        if (!selectedPipeline?.id) {
            setRequestError('Selecione um pipeline antes de incluir uma estratégia.')
            return
        }
        if (!selectedStrategyLibraryItem?.strategy || typeof selectedStrategyLibraryItem.strategy !== 'object') {
            setRequestError('Selecione uma estratégia salva primeiro.')
            return
        }
        updateDraft((current) => {
            const next = cloneSerializable(current, current)
            next.portfolio.pipelines = (next.portfolio.pipelines || []).map((pipeline) => {
                if (String(pipeline.id) !== String(selectedPipeline.id)) {
                    return pipeline
                }
                const importedEntries = buildSavedPortfolioEntriesFromBenchmark(
                    selectedStrategyLibraryItem,
                    {
                        fallbackSymbol: normalizeMarketValue(selectedStrategyLibraryItem.symbol, 'EURUSD'),
                        fallbackTimeframe: normalizeMarketValue(selectedStrategyLibraryItem.timeframe, 'M1'),
                        defaultVolumeMode: 'fixed_volume',
                        defaultFixedVolume: 0.01,
                        startIndex: Array.isArray(pipeline.entries) ? pipeline.entries.length : 0,
                    },
                )
                return {
                    ...pipeline,
                    entries: [...(Array.isArray(pipeline.entries) ? pipeline.entries : []), ...importedEntries],
                }
            })
            return next
        })
        logEventRef.current?.(`Carteiras · Incluída "${selectedStrategyLibraryItem.label || 'estratégia salva'}" em ${selectedPipeline.label || 'pipeline'}.`)
    }

    function handleRemovePipelineEntry(pipelineId, entryId) {
        updateDraft((current) => {
            const next = cloneSerializable(current, current)
            next.portfolio.pipelines = (next.portfolio.pipelines || []).map((pipeline) => (
                String(pipeline.id) === String(pipelineId)
                    ? {
                        ...pipeline,
                        entries: (Array.isArray(pipeline.entries) ? pipeline.entries : []).filter((entry) => String(entry.id) !== String(entryId)),
                    }
                    : pipeline
            ))
            return next
        })
    }

    function handleUpdatePipelineEntry(pipelineId, entryId, field, rawValue) {
        updateDraft((current) => {
            const next = cloneSerializable(current, current)
            next.portfolio.pipelines = (next.portfolio.pipelines || []).map((pipeline) => {
                if (String(pipeline.id) !== String(pipelineId)) {
                    return pipeline
                }
                return {
                    ...pipeline,
                    entries: (Array.isArray(pipeline.entries) ? pipeline.entries : []).map((entry) => {
                        if (String(entry.id) !== String(entryId)) {
                            return entry
                        }
                        if (field === 'enabled') {
                            return { ...entry, enabled: Boolean(rawValue) }
                        }
                        if (field === 'symbol' || field === 'timeframe') {
                            return { ...entry, [field]: normalizeMarketValue(rawValue, field === 'symbol' ? 'EURUSD' : 'M1') }
                        }
                        if (field === 'volumeMode') {
                            const nextMode = normalizeSavedPortfolioVolumeMode(rawValue)
                            return { ...entry, volumeMode: nextMode }
                        }
                        if (field === 'fixedVolume' || field === 'baseVolume' || field === 'maxVolumeCap' || field === 'referenceCapital') {
                            const numeric = Number(rawValue)
                            return {
                                ...entry,
                                [field]: Number.isFinite(numeric) && numeric > 0 ? numeric : null,
                            }
                        }
                        return {
                            ...entry,
                            [field]: rawValue,
                        }
                    }),
                }
            })
            return next
        })
    }

    return (
        <div className='portfoliosConsole'>
            <section className='portfoliosEditorPanel'>
                <div className='portfoliosSectionHeader'>
                    <div>
                        <h2>Carteiras</h2>
                        <p>Monte carteiras com múltiplos pipelines e estratégias aplicadas a símbolos e timeframes específicos.</p>
                    </div>
                    <div className='portfoliosHeaderActions'>
                        <button type='button' className='backtesterToolbarButton' onClick={handleCreateNewDraft} disabled={isSubmitting}>Nova</button>
                        <button
                            type='button'
                            className='backtesterToolbarButton'
                            onClick={() => handleSaveDraft({ forceCreate: false })}
                            disabled={isGuest || isSubmitting}
                            title={isGuest ? guestRestrictionMessage : undefined}
                        >
                            {draft?.id ? 'Atualizar' : 'Salvar'}
                        </button>
                        <button
                            type='button'
                            className='backtesterToolbarButton'
                            onClick={() => handleSaveDraft({ forceCreate: true })}
                            disabled={isGuest || isSubmitting}
                            title={isGuest ? guestRestrictionMessage : undefined}
                        >
                            Salvar como nova
                        </button>
                    </div>
                </div>

                {isGuest ? <div className='portfoliosInlineNotice'>{guestRestrictionMessage} Alteracoes locais servem apenas para exploracao visual da carteira atual.</div> : null}
                {requestError ? <div className='portfoliosErrorBanner'>{requestError}</div> : null}

                <div className='portfoliosMetaGrid'>
                    <label className='field'>
                        <span>Nome</span>
                        <input type='text' value={draft.label || ''} onChange={(event) => handleUpdatePortfolioField('label', event.target.value)} />
                    </label>
                    <label className='field'>
                        <span>Origem</span>
                        <input type='text' value={draft.source || ''} onChange={(event) => handleUpdatePortfolioField('source', event.target.value)} placeholder='ex.: multi-sleeve london stack' />
                    </label>
                    <label className='field portfoliosWideField'>
                        <span>Notas</span>
                        <textarea rows='3' value={draft.notes || ''} onChange={(event) => handleUpdatePortfolioField('notes', event.target.value)} />
                    </label>
                    <label className='field'>
                        <span>Capital mode</span>
                        <select value={draft?.portfolio?.capitalMode || 'equity_percent'} onChange={(event) => handleUpdatePortfolioField('capitalMode', event.target.value)}>
                            <option value='equity_percent'>Equity %</option>
                            <option value='fixed_amount'>Valor fixo</option>
                            <option value='legacy_shared'>Legado compartilhado</option>
                        </select>
                    </label>
                    <label className='field'>
                        <span>Capital value</span>
                        <input type='number' min='0' step='0.01' value={draft?.portfolio?.capitalValue ?? ''} onChange={(event) => handleUpdatePortfolioField('capitalValue', event.target.value)} />
                    </label>
                    <label className='field'>
                        <span>Rebalance</span>
                        <select value={draft?.portfolio?.rebalanceMode || 'static'} onChange={(event) => handleUpdatePortfolioField('rebalanceMode', event.target.value)}>
                            <option value='static'>Static</option>
                        </select>
                    </label>
                    <label className='portfoliosCheckboxField'>
                        <input type='checkbox' checked={Boolean(draft?.is_favorite)} onChange={(event) => handleUpdatePortfolioField('is_favorite', event.target.checked)} />
                        <span>Favorita</span>
                    </label>
                </div>

                <div className='portfoliosPipelinesToolbar'>
                    <div>
                        <strong>Pipelines</strong>
                        <span>{formatCountLabel(draft?.portfolio?.pipelines?.length || 0, 'pipeline', 'pipelines')}</span>
                    </div>
                    <button type='button' className='backtesterToolbarButton' onClick={handleAddPipeline}>Adicionar pipeline</button>
                </div>

                <div className='portfoliosPipelineList'>
                    {(draft?.portfolio?.pipelines || []).map((pipeline) => (
                        <article
                            key={pipeline.id}
                            className={`portfoliosPipelineCard ${String(selectedPipeline?.id || '') === String(pipeline.id) ? 'isSelected' : ''}`}
                            onClick={() => setSelectedPipelineId(String(pipeline.id || ''))}
                        >
                            <div className='portfoliosPipelineHeader'>
                                <div className='portfoliosPipelineTitle'>
                                    <input
                                        type='text'
                                        value={pipeline.label || ''}
                                        onChange={(event) => handleUpdatePipeline(pipeline.id, 'label', event.target.value)}
                                    />
                                    <span>{formatCountLabel(pipeline?.entries?.length || 0, 'estratégia', 'estratégias')}</span>
                                </div>
                                <div className='portfoliosPipelineActions'>
                                    <label className='portfoliosCheckboxField'>
                                        <input
                                            type='checkbox'
                                            checked={pipeline.enabled !== false}
                                            onChange={(event) => handleUpdatePipeline(pipeline.id, 'enabled', event.target.checked)}
                                        />
                                        <span>Ativo</span>
                                    </label>
                                    <button type='button' className='backtesterToolbarButton danger' onClick={() => handleRemovePipeline(pipeline.id)}>Remover</button>
                                </div>
                            </div>

                            <div className='portfoliosPipelineMeta'>
                                <label className='field'>
                                    <span>Modo do pipeline</span>
                                    <select value={pipeline.portfolioMode || 'parallel_sleeves'} onChange={(event) => handleUpdatePipeline(pipeline.id, 'portfolioMode', event.target.value)}>
                                        <option value='parallel_sleeves'>Parallel sleeves</option>
                                        <option value='shared_pipe'>Shared pipe</option>
                                    </select>
                                </label>
                            </div>

                            <div className='portfoliosEntryTable'>
                                <div className='portfoliosEntryTableHeader'>
                                    <span>Estratégia</span>
                                    <span>Mercado</span>
                                    <span>TF</span>
                                    <span>Volume</span>
                                    <span>Config</span>
                                    <span>Ações</span>
                                </div>
                                {(pipeline.entries || []).map((entry) => (
                                    <div key={entry.id} className='portfoliosEntryRow'>
                                        <input type='text' value={entry.label || ''} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'label', event.target.value)} />
                                        <input type='text' value={entry.symbol || ''} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'symbol', event.target.value)} />
                                        <select value={entry.timeframe || 'M1'} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'timeframe', event.target.value)}>
                                            {TIMEFRAME_OPTIONS.map((option) => (
                                                <option key={option.value} value={option.value}>{option.label}</option>
                                            ))}
                                        </select>
                                        <select value={entry.volumeMode || 'fixed_volume'} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'volumeMode', event.target.value)}>
                                            <option value='fixed_volume'>Fixo</option>
                                            <option value='max_affordable'>Máximo</option>
                                            <option value='base_volume_compounding'>Base variável</option>
                                        </select>
                                        <div className='portfoliosEntryVolumeFields'>
                                            {entry.volumeMode === 'fixed_volume' ? (
                                                <input type='number' min='0.01' step='0.01' value={entry.fixedVolume ?? ''} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'fixedVolume', event.target.value)} placeholder='Lote' />
                                            ) : null}
                                            {entry.volumeMode === 'base_volume_compounding' ? (
                                                <>
                                                    <input type='number' min='0.01' step='0.01' value={entry.baseVolume ?? ''} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'baseVolume', event.target.value)} placeholder='Base' />
                                                    <input type='number' min='0.01' step='0.01' value={entry.maxVolumeCap ?? ''} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'maxVolumeCap', event.target.value)} placeholder='Cap' />
                                                </>
                                            ) : null}
                                            {entry.volumeMode === 'max_affordable' ? (
                                                <input type='number' min='0.01' step='0.01' value={entry.maxVolumeCap ?? ''} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'maxVolumeCap', event.target.value)} placeholder='Cap opcional' />
                                            ) : null}
                                        </div>
                                        <div className='portfoliosEntryActions'>
                                            <label className='portfoliosCheckboxField'>
                                                <input type='checkbox' checked={entry.enabled !== false} onChange={(event) => handleUpdatePipelineEntry(pipeline.id, entry.id, 'enabled', event.target.checked)} />
                                                <span>Ativa</span>
                                            </label>
                                            <button type='button' className='backtesterToolbarButton danger' onClick={() => handleRemovePipelineEntry(pipeline.id, entry.id)}>Remover</button>
                                        </div>
                                    </div>
                                ))}
                                {!pipeline.entries?.length ? (
                                    <div className='portfoliosEmptyInline'>Nenhuma estratégia neste pipeline ainda.</div>
                                ) : null}
                            </div>
                        </article>
                    ))}
                </div>
            </section>

            <aside className='portfoliosSidebar'>
                <section className='portfoliosSidebarCard'>
                    <div className='portfoliosSidebarHeader'>
                        <div>
                            <strong>Estratégias salvas</strong>
                            <span>Importe snapshots da strategy library para o pipeline selecionado.</span>
                        </div>
                        <button type='button' className='backtesterToolbarButton' onClick={() => loadStrategyLibrary()} disabled={isLoadingStrategies}>Refresh</button>
                    </div>
                    <div className='strategyListTabs'>
                        <button type='button' className={strategyLibraryListTab === 'all' ? 'active' : ''} onClick={() => setStrategyLibraryListTab('all')}>All</button>
                        <button type='button' className={strategyLibraryListTab === 'favorites' ? 'active' : ''} onClick={() => setStrategyLibraryListTab('favorites')}>Favorites</button>
                    </div>
                    {strategyLibraryLoadError ? (
                        <div className='portfoliosInlineNotice isError'>{strategyLibraryLoadError}</div>
                    ) : null}
                    <input
                        type='search'
                        className='strategyLibrarySearch'
                        placeholder='Buscar estratégia...'
                        value={strategyLibraryQuery}
                        onChange={(event) => setStrategyLibraryQuery(event.target.value)}
                    />
                    <div className='portfoliosSidebarList'>
                        {visibleStrategyLibraryItems.map((entry) => (
                            <button
                                key={entry.id}
                                type='button'
                                className={`portfoliosLibraryRow ${String(selectedStrategyLibraryId) === String(entry.id) ? 'isSelected' : ''}`}
                                onClick={() => setSelectedStrategyLibraryId(String(entry.id || ''))}
                            >
                                <strong>{entry.label || `Strategy #${entry.id}`}</strong>
                                <span>{entry.symbol || '--'} · {entry.timeframe || '--'}</span>
                            </button>
                        ))}
                        {!visibleStrategyLibraryItems.length ? <div className='portfoliosEmptyInline'>Nenhuma estratégia encontrada.</div> : null}
                    </div>
                    <button type='button' className='backtesterToolbarButton primary' onClick={handleAddSelectedStrategyToPipeline} disabled={!selectedPipeline?.id}>Adicionar ao pipeline selecionado</button>
                </section>

                <section className='portfoliosSidebarCard'>
                    <div className='portfoliosSidebarHeader'>
                        <div>
                            <strong>Carteiras salvas</strong>
                            <span>Abra, favorite e apague carteiras publicáveis para Backtester e Trader.</span>
                        </div>
                        <button type='button' className='backtesterToolbarButton' onClick={() => loadSavedPortfolios()} disabled={isLoadingSavedPortfolios}>Refresh</button>
                    </div>
                    <div className='strategyListTabs'>
                        <button type='button' className={savedPortfolioListTab === 'all' ? 'active' : ''} onClick={() => setSavedPortfolioListTab('all')}>All</button>
                        <button type='button' className={savedPortfolioListTab === 'favorites' ? 'active' : ''} onClick={() => setSavedPortfolioListTab('favorites')}>Favorites</button>
                    </div>
                    {savedPortfolioLoadError ? (
                        <div className='portfoliosInlineNotice isError'>{savedPortfolioLoadError}</div>
                    ) : null}
                    <input
                        type='search'
                        className='strategyLibrarySearch'
                        placeholder='Buscar carteira...'
                        value={savedPortfolioQuery}
                        onChange={(event) => setSavedPortfolioQuery(event.target.value)}
                    />
                    <div className='portfoliosSidebarList'>
                        {visibleSavedPortfolioItems.map((entry) => {
                            const summary = summarizeSavedPortfolio(entry)
                            return (
                                <button
                                    key={entry.id}
                                    type='button'
                                    className={`portfoliosLibraryRow ${String(selectedSavedPortfolioId) === String(entry.id) ? 'isSelected' : ''}`}
                                    onClick={() => setSelectedSavedPortfolioId(String(entry.id || ''))}
                                >
                                    <strong>{entry.label || `Portfolio #${entry.id}`}</strong>
                                    <span>{formatCountLabel(summary.pipelineCount, 'pipeline', 'pipelines')} · {formatCountLabel(summary.entryCount, 'estratégia', 'estratégias')}</span>
                                </button>
                            )
                        })}
                        {!visibleSavedPortfolioItems.length ? <div className='portfoliosEmptyInline'>Nenhuma carteira salva encontrada.</div> : null}
                    </div>
                    <div className='portfoliosSidebarActions'>
                        <button type='button' className='backtesterToolbarButton' onClick={handleLoadSelectedSavedPortfolio} disabled={!selectedSavedPortfolioItem}>Abrir no editor</button>
                        <button
                            type='button'
                            className='backtesterToolbarButton'
                            onClick={() => handleToggleFavoriteSavedPortfolio()}
                            disabled={isGuest || !selectedSavedPortfolioItem}
                            title={isGuest ? guestRestrictionMessage : undefined}
                        >
                            Favoritar
                        </button>
                        <button
                            type='button'
                            className='backtesterToolbarButton danger'
                            onClick={handleDeleteSelectedSavedPortfolio}
                            disabled={isGuest || !selectedSavedPortfolioItem}
                            title={isGuest ? guestRestrictionMessage : undefined}
                        >
                            Excluir
                        </button>
                    </div>
                </section>
            </aside>
        </div>
    )
}

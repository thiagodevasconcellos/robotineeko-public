import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, fetchWithServerRetry, readJsonResponse } from '../../api'
import {
    BACKTEST_ASSET_TYPE_DEFINITIONS,
    BACKTEST_COST_PROFILE_DEFINITIONS,
} from './backtestCostProfiles.js'
import {
    normalizeBrokerProfileId,
    normalizeBrokerProfileRecord,
    resolveBrokerProfileApiBaseUrl,
    resolveBrokerProfileScopeLabel,
} from '../../utils/brokerProfiles.js'
import './Brokers.css'

const BROKER_CODE_OPTIONS = [
    { value: 'forex.com', label: 'Forex.com' },
    { value: 'oanda', label: 'OANDA' },
    { value: 'clear', label: 'CLEAR' },
    { value: 'generic_mt5', label: 'Generic MT5' },
]

const MARKET_DOMAIN_OPTIONS = [
    { value: 'forex', label: 'Forex' },
    { value: 'brazil', label: 'Brazil' },
    { value: 'indices', label: 'Indices' },
    { value: 'mixed', label: 'Mixed' },
]

const CONNECTOR_KIND_OPTIONS = [
    { value: 'mt5', label: 'MetaTrader 5' },
]

const BROKER_COST_PROFILE_OPTIONS = Object.values(BACKTEST_COST_PROFILE_DEFINITIONS)
    .filter((entry) => entry.id !== 'custom' && entry.id !== 'broker_active')
    .map((entry) => ({
        value: entry.id,
        label: entry.label,
    }))

const BROKER_DEFAULT_ASSET_TYPE_OPTIONS = Object.values(BACKTEST_ASSET_TYPE_DEFINITIONS)
    .map((entry) => ({
        value: entry.id,
        label: entry.label,
    }))

function buildEmptyDraft() {
    return normalizeBrokerProfileRecord({
        id: '',
        label: '',
        broker_code: 'forex.com',
        connector_kind: 'mt5',
        server_name: '',
        market_domain: 'forex',
        base_currency: 'USD',
        notes: '',
        is_default: false,
        is_favorite: false,
        profile: {
            cost_profile: '',
            default_asset_type: '',
        },
    })
}

export function Brokers({
    isActive = false,
    authToken = '',
    isGuest = false,
    tradeState,
    setTradeState,
    onProfilesChanged = null,
    onLogEvent,
}) {
    const [items, setItems] = useState([])
    const [selectedId, setSelectedId] = useState('')
    const [draft, setDraft] = useState(() => buildEmptyDraft())
    const [isLoading, setIsLoading] = useState(false)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [loadError, setLoadError] = useState('')
    const [requestError, setRequestError] = useState('')
    const logEventRef = useRef(onLogEvent)
    const previousIsActiveRef = useRef(Boolean(isActive))
    const lastBootstrapKeyRef = useRef('')
    const activeBrokerProfileId = normalizeBrokerProfileId(tradeState?.activeBrokerProfileId)
    const activeBrokerProfileLabel = String(tradeState?.activeBrokerProfileLabel || '').trim()
    const guestRestrictionMessage = 'Guest demo pode inspecionar perfis de corretora, mas não pode criar, editar nem remover perfis.'
    const authHeaders = useMemo(
        () => authToken
            ? { Authorization: `Bearer ${authToken}` }
            : {},
        [authToken],
    )

    useEffect(() => {
        logEventRef.current = onLogEvent
    }, [onLogEvent])

    const updateTradeState = useCallback((mutator) => {
        setTradeState((current) => {
            const base = current && typeof current === 'object' ? current : {}
            const next = typeof mutator === 'function' ? mutator(base) : base
            return {
                ...base,
                ...next,
            }
        })
    }, [setTradeState])

    const selectedItem = useMemo(
        () => items.find((entry) => entry.id === selectedId) || null,
        [items, selectedId],
    )

    const syncActiveProfile = useCallback((profiles) => {
        const safeProfiles = Array.isArray(profiles) ? profiles : []
        const fallbackProfile = safeProfiles.find((entry) => entry.is_default) || safeProfiles[0] || null
        const resolvedActive = safeProfiles.find((entry) => entry.id === activeBrokerProfileId) || fallbackProfile
        if (!resolvedActive) {
            if (activeBrokerProfileId || activeBrokerProfileLabel) {
                updateTradeState((current) => ({
                    ...current,
                    activeBrokerProfileId: '',
                    activeBrokerProfileLabel: '',
                }))
            }
            return
        }
        if (resolvedActive.id !== activeBrokerProfileId || resolvedActive.label !== activeBrokerProfileLabel) {
            updateTradeState((current) => ({
                ...current,
                activeBrokerProfileId: resolvedActive.id,
                activeBrokerProfileLabel: resolvedActive.label,
            }))
        }
    }, [activeBrokerProfileId, activeBrokerProfileLabel, updateTradeState])

    const loadProfiles = useCallback(async ({ quiet = false } = {}) => {
        if (!authToken) {
            setItems([])
            setSelectedId('')
            setDraft(buildEmptyDraft())
            setLoadError('')
            onProfilesChanged?.([])
            return
        }
        if (!quiet) {
            setIsLoading(true)
        }
        try {
            const response = await fetchWithServerRetry(
                buildApiUrl('/workspace/broker-profiles?workspace_id=default&limit=200'),
                { headers: authHeaders },
                {
                    attempts: 4,
                    retryDelayMs: 750,
                },
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Falha ao carregar perfis de corretora.'))
            }
            const nextItems = Array.isArray(data?.broker_profiles)
                ? data.broker_profiles.map((entry, index) => normalizeBrokerProfileRecord(entry, index))
                : []
            setItems(nextItems)
            setLoadError('')
            onProfilesChanged?.(nextItems)
            syncActiveProfile(nextItems)
            setSelectedId((current) => {
                if (current && nextItems.some((entry) => entry.id === current)) {
                    return current
                }
                return nextItems[0]?.id || ''
            })
        } catch (error) {
            const message = error?.message || 'Falha ao carregar perfis de corretora.'
            setLoadError(message)
            logEventRef.current?.(`Brokers · ${message}`)
        } finally {
            if (!quiet) {
                setIsLoading(false)
            }
        }
    }, [authHeaders, authToken, onProfilesChanged, syncActiveProfile])

    useEffect(() => {
        const bootstrapKey = authToken ? `token:${authToken}` : 'anonymous'
        if (lastBootstrapKeyRef.current === bootstrapKey) {
            return
        }
        lastBootstrapKeyRef.current = bootstrapKey
        void loadProfiles({ quiet: false })
    }, [authToken, loadProfiles])

    useEffect(() => {
        const safeIsActive = Boolean(isActive)
        const becameActive = safeIsActive && !previousIsActiveRef.current
        previousIsActiveRef.current = safeIsActive
        if (!becameActive) {
            return
        }
        void loadProfiles({ quiet: true })
    }, [isActive, loadProfiles])

    useEffect(() => {
        if (!selectedItem) {
            if (!selectedId) {
                setDraft(buildEmptyDraft())
            }
            return
        }
        setDraft(normalizeBrokerProfileRecord(selectedItem))
    }, [selectedId, selectedItem])

    async function handleSaveProfile() {
        if (isGuest) {
            setRequestError(guestRestrictionMessage)
            logEventRef.current?.(`Brokers · ${guestRestrictionMessage}`)
            return
        }
        const label = String(draft.label || '').trim()
        if (!label) {
            setRequestError('Defina um nome para o profile antes de salvar.')
            return
        }
        setIsSubmitting(true)
        setRequestError('')
        try {
            const method = draft.id ? 'PATCH' : 'POST'
            const url = draft.id
                ? buildApiUrl(`/workspace/broker-profiles/${draft.id}`)
                : buildApiUrl('/workspace/broker-profiles')
            const response = await fetchWithServerRetry(url, {
                method,
                headers: {
                    ...authHeaders,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    label,
                    broker_code: draft.broker_code,
                    connector_kind: draft.connector_kind,
                    server_name: draft.server_name,
                    market_domain: draft.market_domain,
                    base_currency: draft.base_currency,
                    notes: draft.notes,
                    is_default: Boolean(draft.is_default),
                    profile: draft.profile,
                }),
            }, {
                attempts: 3,
                retryDelayMs: 750,
            })
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Falha ao salvar o profile de corretora.'))
            }
            const savedProfile = normalizeBrokerProfileRecord(data?.broker_profile || {})
            if (savedProfile.id && (savedProfile.is_default || !activeBrokerProfileId)) {
                updateTradeState((current) => ({
                    ...current,
                    activeBrokerProfileId: savedProfile.id,
                    activeBrokerProfileLabel: savedProfile.label,
                }))
            }
            setSelectedId(savedProfile.id)
            await loadProfiles({ quiet: true })
            logEventRef.current?.(
                method === 'POST'
                    ? `Brokers · Criado "${savedProfile.label}".`
                    : `Brokers · Atualizado "${savedProfile.label}".`
            )
        } catch (error) {
            setRequestError(error?.message || 'Falha ao salvar o profile de corretora.')
        } finally {
            setIsSubmitting(false)
        }
    }

    async function handleActivateProfile(target = selectedItem) {
        if (!target?.id) {
            return
        }
        if (isGuest) {
            setRequestError(guestRestrictionMessage)
            logEventRef.current?.(`Brokers · ${guestRestrictionMessage}`)
            return
        }
        setIsSubmitting(true)
        setRequestError('')
        try {
            const response = await fetchWithServerRetry(buildApiUrl(`/workspace/broker-profiles/${target.id}`), {
                method: 'PATCH',
                headers: {
                    ...authHeaders,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    is_default: true,
                }),
            }, {
                attempts: 3,
                retryDelayMs: 750,
            })
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Falha ao ativar o profile de corretora.'))
            }
            const savedProfile = normalizeBrokerProfileRecord(data?.broker_profile || target)
            updateTradeState((current) => ({
                ...current,
                activeBrokerProfileId: savedProfile.id,
                activeBrokerProfileLabel: savedProfile.label,
            }))
            setSelectedId(savedProfile.id)
            await loadProfiles({ quiet: true })
            logEventRef.current?.(`Brokers · "${savedProfile.label}" agora é a corretora ativa do workspace.`)
        } catch (error) {
            setRequestError(error?.message || 'Falha ao ativar o profile de corretora.')
        } finally {
            setIsSubmitting(false)
        }
    }

    async function handleDeleteProfile() {
        if (!selectedItem?.id) {
            return
        }
        if (isGuest) {
            setRequestError(guestRestrictionMessage)
            logEventRef.current?.(`Brokers · ${guestRestrictionMessage}`)
            return
        }
        const confirmed = window.confirm(`Remover o profile "${selectedItem.label}"?`)
        if (!confirmed) {
            return
        }
        setIsSubmitting(true)
        setRequestError('')
        try {
            const response = await fetchWithServerRetry(
                buildApiUrl(`/workspace/broker-profiles/${selectedItem.id}?workspace_id=default`),
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
                throw new Error(extractApiErrorMessage(data, 'Falha ao remover o profile de corretora.'))
            }
            if (selectedItem.id === activeBrokerProfileId) {
                updateTradeState((current) => ({
                    ...current,
                    activeBrokerProfileId: '',
                    activeBrokerProfileLabel: '',
                }))
            }
            setSelectedId('')
            setDraft(buildEmptyDraft())
            await loadProfiles({ quiet: true })
            logEventRef.current?.(`Brokers · Removido "${selectedItem.label}".`)
        } catch (error) {
            setRequestError(error?.message || 'Falha ao remover o profile de corretora.')
        } finally {
            setIsSubmitting(false)
        }
    }

    return (
        <div className='brokersConsole'>
            <aside className='brokersSidebarCard'>
                <div className='brokersHeader'>
                    <div>
                        <strong>Broker profiles</strong>
                        <span>Cadastre uma corretora por profile e escolha qual delas guia o workspace agora.</span>
                    </div>
                    <div className='brokersHeaderActions'>
                        <button type='button' className='backtesterToolbarButton' onClick={() => {
                            setSelectedId('')
                            setDraft(buildEmptyDraft())
                            setRequestError('')
                        }}>
                            New
                        </button>
                        <button type='button' className='backtesterToolbarButton' onClick={() => void loadProfiles({ quiet: false })} disabled={isLoading}>
                            {isLoading ? 'Refreshing...' : 'Refresh'}
                        </button>
                    </div>
                </div>

                <div className='brokersScopePill'>
                    <span>Current workspace broker</span>
                    <strong>{resolveBrokerProfileScopeLabel(activeBrokerProfileLabel)}</strong>
                </div>

                {loadError && !items.length ? (
                    <div className='brokersMessage isError'>{loadError}</div>
                ) : null}

                <div className='brokersList'>
                    {!items.length ? (
                        <div className='brokersMessage'>
                            Nenhum profile salvo ainda. Crie o primeiro profile para separar bibliotecas, runtime e histórico por corretora.
                        </div>
                    ) : items.map((entry) => (
                        <button
                            key={entry.id}
                            type='button'
                            className={`brokersListButton ${entry.id === selectedId ? 'active' : ''}`.trim()}
                            onClick={() => {
                                setSelectedId(entry.id)
                                setRequestError('')
                            }}
                        >
                            <div className='brokersListTitleRow'>
                                <strong>{entry.label}</strong>
                                {entry.id === activeBrokerProfileId ? <span className='brokersActiveBadge'>Active</span> : null}
                            </div>
                            <span>{entry.broker_code || 'manual'} · {entry.market_domain || 'unscoped'} · {entry.connector_kind || 'mt5'}</span>
                            <small>{entry.server_name || 'No server configured yet.'}</small>
                            <small>
                                Cost model: {entry.profile?.cost_profile || 'auto'} · Asset type: {entry.profile?.default_asset_type || 'auto'}
                            </small>
                            <small>{resolveBrokerProfileApiBaseUrl(entry) || 'Uses the default Robotineeko stack / current API base.'}</small>
                        </button>
                    ))}
                </div>
            </aside>

            <section className='brokersEditorPanel'>
                <div className='brokersHeader'>
                    <div>
                        <strong>{draft.id ? 'Edit broker profile' : 'Create broker profile'}</strong>
                        <span>
                            O profile ativo passa a escopar a Strategy library, Portfolios, Trader history e os próximos saves associados ao workspace.
                        </span>
                    </div>
                </div>

                <div className='brokersFormGrid'>
                    <label className='brokersField'>
                        <span>Label</span>
                        <input
                            type='text'
                            value={draft.label}
                            onChange={(event) => setDraft((current) => ({ ...current, label: event.target.value }))}
                            placeholder='Forex.com main'
                        />
                    </label>

                    <label className='brokersField'>
                        <span>Broker</span>
                        <select
                            value={draft.broker_code}
                            onChange={(event) => setDraft((current) => ({ ...current, broker_code: event.target.value }))}
                        >
                            {BROKER_CODE_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                        </select>
                    </label>

                    <label className='brokersField'>
                        <span>Connector</span>
                        <select
                            value={draft.connector_kind}
                            onChange={(event) => setDraft((current) => ({ ...current, connector_kind: event.target.value }))}
                        >
                            {CONNECTOR_KIND_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                        </select>
                    </label>

                    <label className='brokersField'>
                        <span>Market domain</span>
                        <select
                            value={draft.market_domain}
                            onChange={(event) => setDraft((current) => ({ ...current, market_domain: event.target.value }))}
                        >
                            {MARKET_DOMAIN_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                        </select>
                    </label>

                    <label className='brokersField'>
                        <span>Server name</span>
                        <input
                            type='text'
                            value={draft.server_name}
                            onChange={(event) => setDraft((current) => ({ ...current, server_name: event.target.value }))}
                            placeholder='MetaTrader server / account endpoint'
                        />
                    </label>

                    <label className='brokersField'>
                        <span>Base currency</span>
                        <input
                            type='text'
                            value={draft.base_currency}
                            onChange={(event) => setDraft((current) => ({ ...current, base_currency: event.target.value.toUpperCase() }))}
                            placeholder='USD'
                            maxLength={6}
                        />
                    </label>

                    <label className='brokersField'>
                        <span>Cost model override</span>
                        <select
                            value={draft.profile?.cost_profile || ''}
                            onChange={(event) => setDraft((current) => ({
                                ...current,
                                profile: {
                                    ...(current.profile && typeof current.profile === 'object' ? current.profile : {}),
                                    cost_profile: event.target.value,
                                },
                            }))}
                        >
                            <option value=''>Resolve from broker code</option>
                            {BROKER_COST_PROFILE_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                        </select>
                    </label>

                    <label className='brokersField'>
                        <span>Default asset type</span>
                        <select
                            value={draft.profile?.default_asset_type || ''}
                            onChange={(event) => setDraft((current) => ({
                                ...current,
                                profile: {
                                    ...(current.profile && typeof current.profile === 'object' ? current.profile : {}),
                                    default_asset_type: event.target.value,
                                },
                            }))}
                        >
                            <option value=''>Resolve from market domain</option>
                            {BROKER_DEFAULT_ASSET_TYPE_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                        </select>
                    </label>
                </div>

                <label className='brokersField brokersFieldFull'>
                    <span>Operational API base URL</span>
                    <input
                        type='text'
                        value={draft.profile?.api_base_url || ''}
                        onChange={(event) => setDraft((current) => ({
                            ...current,
                            profile: {
                                ...(current.profile && typeof current.profile === 'object' ? current.profile : {}),
                                api_base_url: event.target.value,
                            },
                        }))}
                        placeholder='http://127.0.0.1:8010'
                    />
                    <small>
                        Optional. If filled, selecting this broker in the page header switches the whole console to this backend stack and port.
                    </small>
                </label>

                <label className='brokersField brokersFieldFull'>
                    <span>Notes</span>
                    <textarea
                        value={draft.notes}
                        onChange={(event) => setDraft((current) => ({ ...current, notes: event.target.value }))}
                        placeholder='Observações operacionais, ativos desse profile, bridge esperado, etc.'
                        rows={5}
                    />
                </label>

                <label className='brokersCheckbox'>
                    <input
                        type='checkbox'
                        checked={Boolean(draft.is_default)}
                        onChange={(event) => setDraft((current) => ({ ...current, is_default: event.target.checked }))}
                    />
                    <span>Set this profile as the default workspace broker when saving</span>
                </label>

                {requestError ? (
                    <div className='brokersMessage isError'>{requestError}</div>
                ) : null}

                <div className='brokersActions'>
                    <button type='button' className='backtesterToolbarButton primary' onClick={() => void handleSaveProfile()} disabled={isSubmitting}>
                        {isSubmitting ? 'Saving...' : (draft.id ? 'Save changes' : 'Create profile')}
                    </button>
                    <button
                        type='button'
                        className='backtesterToolbarButton'
                        onClick={() => void handleActivateProfile()}
                        disabled={isSubmitting || !selectedItem?.id}
                    >
                        Use as active workspace broker
                    </button>
                    <button
                        type='button'
                        className='backtesterToolbarButton danger'
                        onClick={() => void handleDeleteProfile()}
                        disabled={isSubmitting || !selectedItem?.id}
                    >
                        Delete profile
                    </button>
                </div>
            </section>
        </div>
    )
}

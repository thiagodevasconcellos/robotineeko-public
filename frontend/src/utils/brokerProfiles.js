const ACTIVE_BROKER_PROFILE_STORAGE_KEY = 'robotineeko_active_broker_profile'

function normalizeBrokerScopeKey(value) {
    return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '')
}

function normalizeBrokerCode(value) {
    const normalized = normalizeBrokerScopeKey(value)
    if (!normalized) {
        return ''
    }

    if (normalized === 'forexcom') {
        return 'forex.com'
    }

    if (normalized === 'oanda') {
        return 'oanda'
    }

    if (normalized === 'clear') {
        return 'clear'
    }

    return String(value || '').trim().toLowerCase()
}

export function normalizeBrokerProfileMarketDomain(value) {
    const normalized = normalizeBrokerScopeKey(value)
    if (!normalized) {
        return ''
    }

    if (normalized === 'forex' || normalized === 'fx') {
        return 'forex'
    }

    if (normalized === 'b3' || normalized === 'bovespa' || normalized === 'brasil' || normalized === 'brazil') {
        return 'b3'
    }

    return String(value || '').trim().toLowerCase()
}

export function normalizeBrokerProfileDisplayLabel(value, brokerCode = '') {
    const safeValue = String(value || '').trim()
    const safeCode = String(brokerCode || '').trim().toLowerCase()
    const normalizedValue = safeValue.toLowerCase()

    if (
        safeCode === 'forex.com'
        || normalizedValue === 'ditec'
        || normalizedValue === 'forex.com'
        || normalizedValue === 'forexcom'
    ) {
        return 'Forex.com'
    }

    if (safeCode === 'clear' || normalizedValue === 'clear') {
        return 'CLEAR'
    }

    return safeValue
}

export function normalizeBrokerProfileId(value) {
    return String(value || '').trim()
}

export function normalizeBrokerProfileApiBaseUrl(value) {
    const rawValue = String(value || '').trim()
    if (!rawValue) {
        return ''
    }

    const candidateValue = /^[a-z][a-z0-9+.-]*:\/\//i.test(rawValue)
        ? rawValue
        : `http://${rawValue}`

    try {
        const url = new URL(candidateValue)
        return url.toString().replace(/\/+$/, '')
    } catch {
        return rawValue.replace(/\/+$/, '')
    }
}

export function normalizeBrokerProfileLabel(value, brokerCode = '') {
    return normalizeBrokerProfileDisplayLabel(value, brokerCode)
}

export function resolveBrokerProfileMarketDomain(record) {
    const safeRecord = record && typeof record === 'object' ? record : {}
    const safeProfile = safeRecord.profile && typeof safeRecord.profile === 'object'
        ? safeRecord.profile
        : {}
    const explicitMarketDomain = normalizeBrokerProfileMarketDomain(
        safeRecord.market_domain
        || safeRecord.marketDomain
        || safeProfile.market_domain
        || safeProfile.marketDomain
        || ''
    )
    if (explicitMarketDomain) {
        return explicitMarketDomain
    }

    const normalizedBrokerCode = normalizeBrokerCode(
        safeRecord.broker_code
        || safeRecord.brokerCode
        || safeProfile.broker_code
        || safeProfile.brokerCode
        || ''
    )
    if (normalizedBrokerCode === 'forex.com' || normalizedBrokerCode === 'oanda') {
        return 'forex'
    }
    if (normalizedBrokerCode === 'clear') {
        return 'b3'
    }

    const normalizedLabel = normalizeBrokerScopeKey(
        normalizeBrokerProfileLabel(
            safeRecord.label || safeRecord.activeBrokerProfileLabel || '',
            normalizedBrokerCode,
        )
    )
    if (normalizedLabel === 'forexcom' || normalizedLabel === 'oanda') {
        return 'forex'
    }
    if (normalizedLabel === 'clear') {
        return 'b3'
    }

    return ''
}

export function resolveBrokerProfileScopeCode(record) {
    const safeRecord = record && typeof record === 'object' ? record : {}
    const safeProfile = safeRecord.profile && typeof safeRecord.profile === 'object'
        ? safeRecord.profile
        : {}
    const normalizedBrokerCode = normalizeBrokerCode(
        safeRecord.broker_code
        || safeRecord.brokerCode
        || safeProfile.broker_code
        || safeProfile.brokerCode
        || ''
    )
    if (normalizedBrokerCode) {
        return normalizedBrokerCode
    }

    const marketDomain = resolveBrokerProfileMarketDomain(safeRecord)
    if (marketDomain === 'forex') {
        return 'forex.com'
    }
    if (marketDomain === 'b3') {
        return 'clear'
    }

    const normalizedLabel = normalizeBrokerScopeKey(
        normalizeBrokerProfileLabel(
            safeRecord.label || safeRecord.activeBrokerProfileLabel || '',
            normalizedBrokerCode,
        )
    )
    if (normalizedLabel === 'forexcom') {
        return 'forex.com'
    }
    if (normalizedLabel === 'oanda') {
        return 'oanda'
    }
    if (normalizedLabel === 'clear') {
        return 'clear'
    }

    return ''
}

export function isLikelyForexInstrumentSymbol(symbol) {
    return /^[A-Z]{6}$/.test(String(symbol || '').trim().toUpperCase())
}

export function isLikelyClearInstrumentSymbol(symbol) {
    const normalized = String(symbol || '').trim().toUpperCase()
    if (!normalized) {
        return false
    }

    return (
        /^(WIN|WDO|IND|DOL|BGI|CCM|ICF|SJC|DI1|DAP|FRC)/.test(normalized)
        || /^[A-Z0-9]{4}\d{1,2}$/.test(normalized)
        || /^[A-Z0-9]{4}\d{1,2}[A-Z]\d?$/.test(normalized)
        || /^[A-Z0-9]{4}11$/.test(normalized)
    )
}

export function inferMarketDomainFromSymbol(value) {
    const raw = String(value || '').trim().toUpperCase()
    if (!raw || raw === 'MULTI-SYMBOL AGGREGATE') {
        return ''
    }

    const symbolTokens = raw
        .split('+')
        .map((token) => token.trim())
        .filter(Boolean)

    if (!symbolTokens.length) {
        return ''
    }

    const inferredDomains = symbolTokens.map((token) => {
        if (isLikelyForexInstrumentSymbol(token)) {
            return 'forex'
        }
        if (isLikelyClearInstrumentSymbol(token)) {
            return 'b3'
        }
        return ''
    })
    const uniqueDomains = Array.from(new Set(inferredDomains.filter(Boolean)))
    if (uniqueDomains.length === 1) {
        return uniqueDomains[0]
    }
    if (uniqueDomains.length > 1) {
        return 'mixed'
    }
    return ''
}

export function inferBrokerCodeFromSymbol(value) {
    const inferredDomain = inferMarketDomainFromSymbol(value)
    if (inferredDomain === 'forex') {
        return 'forex.com'
    }
    if (inferredDomain === 'b3') {
        return 'clear'
    }
    if (inferredDomain === 'mixed') {
        return 'mixed'
    }
    return ''
}

export function findBrokerProfileForSymbol(symbol, profiles = []) {
    const inferredMarketDomain = inferMarketDomainFromSymbol(symbol)
    if (!inferredMarketDomain || inferredMarketDomain === 'mixed') {
        return null
    }

    const safeProfiles = Array.isArray(profiles) ? profiles : []
    return safeProfiles.find((entry) => resolveBrokerProfileMarketDomain(entry) === inferredMarketDomain)
        || safeProfiles.find((entry) => resolveBrokerProfileScopeCode(entry) === inferBrokerCodeFromSymbol(symbol))
        || null
}

export function buildBrokerProfileQuery({
    workspaceId = 'default',
    limit = null,
    brokerProfileId = '',
    extra = null,
} = {}) {
    const params = new URLSearchParams({
        workspace_id: String(workspaceId || 'default').trim() || 'default',
    })
    if (limit !== null && limit !== undefined) {
        params.set('limit', String(limit))
    }
    const safeBrokerProfileId = normalizeBrokerProfileId(brokerProfileId)
    if (safeBrokerProfileId) {
        params.set('broker_profile_id', safeBrokerProfileId)
    }
    if (extra && typeof extra === 'object') {
        Object.entries(extra).forEach(([key, value]) => {
            if (value === undefined || value === null || value === '') {
                return
            }
            params.set(key, String(value))
        })
    }
    return params.toString()
}

export function normalizeBrokerProfileRecord(record, index = 0) {
    const safeRecord = record && typeof record === 'object' ? record : {}
    const safeProfile = safeRecord.profile && typeof safeRecord.profile === 'object'
        ? safeRecord.profile
        : {}
    const normalizedApiBaseUrl = normalizeBrokerProfileApiBaseUrl(
        safeProfile.api_base_url || safeProfile.apiBaseUrl || ''
    )
    return {
        id: normalizeBrokerProfileId(safeRecord.id || `broker-profile-${index + 1}`),
        label: normalizeBrokerProfileLabel(
            safeRecord.label || `Broker profile ${index + 1}`,
            safeRecord.broker_code || safeProfile.broker_code || '',
        ) || `Broker profile ${index + 1}`,
        broker_code: normalizeBrokerCode(safeRecord.broker_code || '') || 'manual',
        connector_kind: String(safeRecord.connector_kind || '').trim() || 'mt5',
        server_name: String(safeRecord.server_name || '').trim(),
        market_domain: normalizeBrokerProfileMarketDomain(safeRecord.market_domain || ''),
        base_currency: String(safeRecord.base_currency || '').trim().toUpperCase(),
        notes: String(safeRecord.notes || '').trim(),
        is_default: safeRecord.is_default === true,
        is_favorite: safeRecord.is_favorite === true,
        profile: {
            ...safeProfile,
            ...(normalizedApiBaseUrl ? { api_base_url: normalizedApiBaseUrl } : {}),
        },
        created_at: Number(safeRecord.created_at || 0) || null,
        updated_at: Number(safeRecord.updated_at || 0) || null,
    }
}

export function resolveBrokerProfileScopeLabel(brokerProfileLabel) {
    const safeLabel = normalizeBrokerProfileLabel(brokerProfileLabel)
    return safeLabel || 'All broker profiles'
}

export function resolveBrokerProfileApiBaseUrl(record) {
    const safeRecord = record && typeof record === 'object' ? record : {}
    const safeProfile = safeRecord.profile && typeof safeRecord.profile === 'object'
        ? safeRecord.profile
        : {}
    return normalizeBrokerProfileApiBaseUrl(
        safeProfile.api_base_url || safeProfile.apiBaseUrl || ''
    )
}

export function getStoredActiveBrokerProfileSelection() {
    if (typeof window === 'undefined') {
        return {
            id: '',
            label: '',
            apiBaseUrl: '',
        }
    }

    try {
        const rawValue = window.localStorage.getItem(ACTIVE_BROKER_PROFILE_STORAGE_KEY)
        if (!rawValue) {
            return {
                id: '',
                label: '',
                apiBaseUrl: '',
            }
        }
        const parsed = JSON.parse(rawValue)
        const normalizedApiBaseUrl = normalizeBrokerProfileApiBaseUrl(parsed?.apiBaseUrl)
        return {
            id: normalizeBrokerProfileId(parsed?.id),
            label: normalizeBrokerProfileLabel(parsed?.label),
            apiBaseUrl: normalizedApiBaseUrl,
        }
    } catch {
        return {
            id: '',
            label: '',
            apiBaseUrl: '',
        }
    }
}

export function persistStoredActiveBrokerProfileSelection(selection = null) {
    if (typeof window === 'undefined') {
        return
    }

    const safeId = normalizeBrokerProfileId(selection?.id)
    if (!safeId) {
        window.localStorage.removeItem(ACTIVE_BROKER_PROFILE_STORAGE_KEY)
        return
    }

    const payload = {
        id: safeId,
        label: normalizeBrokerProfileLabel(selection?.label),
        apiBaseUrl: normalizeBrokerProfileApiBaseUrl(selection?.apiBaseUrl),
    }
    window.localStorage.setItem(ACTIVE_BROKER_PROFILE_STORAGE_KEY, JSON.stringify(payload))
}

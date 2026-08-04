import {
    getStoredActiveBrokerProfileSelection,
    normalizeBrokerProfileApiBaseUrl,
} from './utils/brokerProfiles.js'

const LEGACY_API_BASE_OVERRIDE_STORAGE_KEY = 'robotineeko_api_base_override'

function isLocalOrPrivateHostname(hostname) {
    const value = String(hostname || '').trim().toLowerCase()
    if (!value) {
        return false
    }

    if (value === 'localhost' || value === '0.0.0.0' || value === '::1' || value.endsWith('.local')) {
        return true
    }

    if (/^127(?:\.\d{1,3}){3}$/.test(value)) {
        return true
    }

    if (/^10(?:\.\d{1,3}){3}$/.test(value)) {
        return true
    }

    if (/^192\.168(?:\.\d{1,3}){2}$/.test(value)) {
        return true
    }

    const private172Match = value.match(/^172\.(\d{1,3})(?:\.\d{1,3}){2}$/)
    if (private172Match) {
        const secondOctet = Number(private172Match[1])
        if (Number.isFinite(secondOctet) && secondOctet >= 16 && secondOctet <= 31) {
            return true
        }
    }

    return false
}

function resolveDefaultApiBase() {
    if (typeof window === 'undefined') {
        return 'http://127.0.0.1:8010'
    }

    const hostname = String(window.location.hostname || '127.0.0.1').trim() || '127.0.0.1'
    if (import.meta.env.DEV || isLocalOrPrivateHostname(hostname)) {
        return `http://${hostname}:8010`
    }

    return window.location.origin
}

function clearLegacyApiBaseOverrideStorage() {
    if (typeof window === 'undefined') {
        return
    }

    try {
        window.localStorage.removeItem(LEGACY_API_BASE_OVERRIDE_STORAGE_KEY)
    } catch {
        // Ignore storage failures.
    }
}

function buildBrokerProfileProxyState() {
    if (typeof window === 'undefined') {
        return null
    }

    clearLegacyApiBaseOverrideStorage()

    const selection = getStoredActiveBrokerProfileSelection()
    const safeBrokerProfileId = String(selection?.id || '').trim()
    const safeApiBaseUrl = normalizeBrokerProfileApiBaseUrl(selection?.apiBaseUrl || '')
    if (!safeBrokerProfileId || !safeApiBaseUrl) {
        return null
    }

    const encodedBrokerProfileId = encodeURIComponent(safeBrokerProfileId)
    return {
        brokerProfileId: safeBrokerProfileId,
        httpBase: `${window.location.origin}/workspace/broker-profiles/${encodedBrokerProfileId}/proxy`,
        wsBasePath: `/ws/broker-profiles/${encodedBrokerProfileId}/proxy`,
    }
}

function stripWebSocketRoutePrefix(path) {
    const safePath = String(path || '').trim()
    if (!safePath) {
        return ''
    }

    if (safePath.startsWith('/ws/')) {
        return safePath.slice(4)
    }

    if (safePath.startsWith('ws/')) {
        return safePath.slice(3)
    }

    return safePath.replace(/^\/+/, '')
}

const DEFAULT_API_BASE = normalizeBrokerProfileApiBaseUrl(
    import.meta.env.VITE_API_BASE || resolveDefaultApiBase()
)

export const API_BASE = DEFAULT_API_BASE
const TRANSIENT_HTTP_STATUSES = new Set([502, 503, 504])

export function getDefaultApiBase() {
    return DEFAULT_API_BASE
}

export function getApiBaseOverride() {
    return ''
}

export function getEffectiveApiBase() {
    return buildBrokerProfileProxyState()?.httpBase || DEFAULT_API_BASE
}

export function setApiBaseOverride(nextBase) {
    void nextBase
    clearLegacyApiBaseOverrideStorage()
    return ''
}

export function buildApiUrl(path) {
    const effectiveApiBase = getEffectiveApiBase()
    if (!effectiveApiBase) {
        return path
    }

    return `${effectiveApiBase}${path}`
}

export function buildWebSocketUrl(path) {
    const proxyState = buildBrokerProfileProxyState()
    if (proxyState && typeof window !== 'undefined') {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const proxyPath = stripWebSocketRoutePrefix(path)
        return `${protocol}//${window.location.host}${proxyState.wsBasePath}/${proxyPath}`
    }

    const effectiveApiBase = getEffectiveApiBase()
    const base = effectiveApiBase
        ? new URL(effectiveApiBase, typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1:8010')
        : new URL(typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1:8010')
    const protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${base.host}${path}`
}

export async function readJsonResponse(response) {
    const text = await response.text()

    if (!text) {
        return {}
    }

    try {
        return JSON.parse(text)
    } catch {
        const preview = text.replace(/\s+/g, ' ').trim().slice(0, 180)
        throw new Error(
            preview
                ? `Server returned an invalid response: ${preview}`
                : 'Server returned an invalid response.'
        )
    }
}

export function extractApiErrorMessage(data, fallbackMessage) {
    if (typeof data?.detail?.error === 'string' && data.detail.error.trim()) {
        return data.detail.error
    }

    if (typeof data?.detail === 'string' && data.detail.trim()) {
        return data.detail
    }

    if (typeof data?.error === 'string' && data.error.trim()) {
        return data.error
    }

    return fallbackMessage
}

export function isTransientApiResponseStatus(status) {
    return TRANSIENT_HTTP_STATUSES.has(Number(status))
}

function waitForDelay(delayMs) {
    return new Promise((resolve) => {
        globalThis.setTimeout(resolve, Math.max(0, Number(delayMs) || 0))
    })
}

export async function fetchWithServerRetry(
    input,
    init = {},
    {
        attempts = 3,
        retryDelayMs = 600,
        retryableStatuses = TRANSIENT_HTTP_STATUSES,
    } = {},
) {
    const maxAttempts = Math.max(1, Number(attempts) || 1)
    const retryableStatusSet = retryableStatuses instanceof Set
        ? retryableStatuses
        : new Set(Array.isArray(retryableStatuses) ? retryableStatuses : [])
    let lastError = null

    for (let attemptIndex = 0; attemptIndex < maxAttempts; attemptIndex += 1) {
        try {
            const response = await fetch(input, init)
            if (!retryableStatusSet.has(Number(response?.status)) || attemptIndex + 1 >= maxAttempts) {
                return response
            }
        } catch (error) {
            const safeErrorName = String(error?.name || '').trim()
            if (safeErrorName === 'AbortError') {
                throw error
            }
            lastError = error
            if (attemptIndex + 1 >= maxAttempts) {
                throw error
            }
        }

        const effectiveDelayMs = Math.max(0, Number(retryDelayMs) || 0) * (attemptIndex + 1)
        await waitForDelay(effectiveDelayMs)
    }

    throw lastError || new Error('Request failed.')
}

import {
    RESEARCH_POSITIVE_STRATEGIES_LAST_UPDATED,
    RESEARCH_POSITIVE_STRATEGY_CATALOG,
} from './researchPositiveStrategiesCatalog.js'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '../../api.js'

const PAPER_ID_PATTERN = /paper\s*([0-9]+)/i
const CANDIDATE_ID_PATTERN = /row\s*`?([a-z0-9_-]+)`?/i

export function createLocalPositiveHistoryCatalogState() {
    return {
        catalog: RESEARCH_POSITIVE_STRATEGY_CATALOG,
        lastUpdated: RESEARCH_POSITIVE_STRATEGIES_LAST_UPDATED,
    }
}

export function createEmptyPositiveHistoryCatalogState() {
    return {
        catalog: [],
        lastUpdated: '',
    }
}

function buildPositiveHistoryCatalogContextKey(entry) {
    return [
        'ctx',
        String(entry?.label || '').trim(),
        String(entry?.study || '').trim(),
        String(entry?.symbol || '').trim(),
        String(entry?.timeframe || '').trim(),
        String(entry?.side || '').trim(),
    ].join('||')
}

function extractPositiveHistoryPaperId(entry) {
    const explicitPaperId = Number(entry?.paperId ?? entry?.paper_id)
    if (Number.isFinite(explicitPaperId)) {
        return explicitPaperId
    }
    const sources = [entry?.id, entry?.label, entry?.study, entry?.checkpointContext]
    for (const source of sources) {
        const match = String(source || '').match(PAPER_ID_PATTERN)
        if (match) {
            const parsed = Number(match[1])
            if (Number.isFinite(parsed)) {
                return parsed
            }
        }
    }
    return null
}

function extractPositiveHistoryCandidateId(entry) {
    const explicitCandidateId = String(entry?.candidateId || entry?.candidate_id || '').trim().toLowerCase()
    if (explicitCandidateId) {
        return explicitCandidateId
    }
    const sources = [entry?.checkpointContext, entry?.id]
    for (const source of sources) {
        const text = String(source || '').trim()
        const match = text.match(CANDIDATE_ID_PATTERN)
        if (match?.[1]) {
            return match[1].trim().toLowerCase()
        }
        const normalized = text.toLowerCase()
        if (normalized.startsWith('s') && /^\d+$/.test(normalized.slice(1))) {
            return normalized
        }
    }
    return ''
}

function buildPositiveHistoryCatalogAliases(entry) {
    const aliases = []
    if (entry?.id) {
        aliases.push(`id:${String(entry.id)}`)
    }
    if (entry?.sharedRegistryKey) {
        aliases.push(String(entry.sharedRegistryKey))
    }
    const paperId = extractPositiveHistoryPaperId(entry)
    const candidateId = extractPositiveHistoryCandidateId(entry)
    if (paperId !== null && candidateId) {
        aliases.push(`paper:${paperId}:candidate:${candidateId}`)
    }
    aliases.push(buildPositiveHistoryCatalogContextKey(entry))
    return Array.from(new Set(aliases.filter(Boolean)))
}

function hasMeaningfulCatalogScalarValue(value) {
    if (value === null || value === undefined) {
        return false
    }
    if (typeof value === 'string') {
        return value.trim() !== ''
    }
    return true
}

function mergePositiveHistoryCatalogEntry(current, incoming, { preferIncoming = false } = {}) {
    if (!current) {
        return incoming
    }
    if (!incoming) {
        return current
    }

    const merged = { ...current }
    for (const [key, value] of Object.entries(incoming)) {
        if (Array.isArray(value)) {
            const currentValue = Array.isArray(merged[key]) ? merged[key] : []
            merged[key] = Array.from(new Set([...currentValue, ...value]))
            continue
        }
        if (preferIncoming && hasMeaningfulCatalogScalarValue(value)) {
            merged[key] = value
            continue
        }
        if (merged[key] === null || merged[key] === undefined || merged[key] === '') {
            merged[key] = value
        }
    }
    return merged
}

export function mergePositiveHistoryCatalogs(localCatalog = [], sharedCatalog = []) {
    const merged = []
    const aliasToIndex = new Map()

    for (const [catalogIndex, catalog] of [localCatalog, sharedCatalog].entries()) {
        const preferIncoming = catalogIndex > 0
        for (const entry of catalog) {
            const aliases = buildPositiveHistoryCatalogAliases(entry)
            const existingIndex = aliases.find((alias) => aliasToIndex.has(alias))

            if (existingIndex) {
                const targetIndex = aliasToIndex.get(existingIndex)
                merged[targetIndex] = mergePositiveHistoryCatalogEntry(merged[targetIndex], entry, { preferIncoming })
                for (const alias of aliases) {
                    aliasToIndex.set(alias, targetIndex)
                }
                continue
            }

            const nextIndex = merged.length
            merged.push(entry)
            for (const alias of aliases) {
                aliasToIndex.set(alias, nextIndex)
            }
        }
    }

    return merged
}

export async function fetchSharedPositiveHistoryCatalog() {
    const response = await fetch(buildApiUrl('/workspace/positive-history/shared-catalog'), {
        method: 'GET',
        cache: 'no-store',
        credentials: 'include',
        headers: {
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        },
    })
    const data = await readJsonResponse(response)
    if (!response.ok) {
        throw new Error(extractApiErrorMessage(data, 'I could not load the shared Positive history catalog right now.'))
    }

    return {
        catalog: Array.isArray(data?.catalog) ? data.catalog : [],
        lastUpdated: typeof data?.lastUpdated === 'string' ? data.lastUpdated : '',
        sharedRegistryLastUpdated: typeof data?.sharedRegistryLastUpdated === 'string'
            ? data.sharedRegistryLastUpdated
            : '',
    }
}

export function mergeLocalAndSharedPositiveHistoryCatalog(sharedPayload) {
    const localState = createLocalPositiveHistoryCatalogState()
    const sharedCatalog = Array.isArray(sharedPayload?.catalog) ? sharedPayload.catalog : []
    return {
        catalog: mergePositiveHistoryCatalogs(localState.catalog, sharedCatalog),
        lastUpdated: String(sharedPayload?.lastUpdated || '').trim() || localState.lastUpdated,
    }
}

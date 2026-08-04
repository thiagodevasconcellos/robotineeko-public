import { getIndicatorManifestEntry } from './indicatorManifest.js'

const FEATURE_FAVORITES_STORAGE_KEY = 'robotineeko_feature_favorites_v1'
const FEATURE_FAVORITES_EVENT = 'robotineeko:feature-favorites-updated'

function normalizeFeatureName(value) {
    return String(value || '').trim()
}

function sortFeatureNames(values = []) {
    return [...new Set(
        (Array.isArray(values) ? values : [])
            .map((value) => normalizeFeatureName(value))
            .filter(Boolean)
    )].sort((left, right) => left.localeCompare(right))
}

export function isNeuralFeatureDefinition(definition) {
    const manifestEntry = getIndicatorManifestEntry(definition?.name)
    if (!manifestEntry) {
        return false
    }

    const classification = String(manifestEntry?.classification || definition?.classification || '').trim().toLowerCase()
    const outputLayer = String(manifestEntry?.runtimeContract?.output_layer || '').trim().toLowerCase()
    const importPath = String(manifestEntry?.pythonImport || '').trim().toLowerCase()

    return (
        classification === 'neural_network'
        || outputLayer === 'neural_indicator'
        || importPath.includes('.indicator.features.neural_')
    )
}

export function isNeuralFeatureName(name) {
    const manifestEntry = getIndicatorManifestEntry(name)
    return isNeuralFeatureDefinition(manifestEntry || { name })
}

export function getFeatureFamily(definition) {
    const manifestEntry = getIndicatorManifestEntry(definition?.name)
    const importPath = String(manifestEntry?.pythonImport || '').toLowerCase()
    const outputLayer = String(manifestEntry?.runtimeContract?.output_layer || '').trim().toLowerCase()

    if (outputLayer === 'neural_indicator') {
        return 'neural'
    }
    if (outputLayer === 'structure_indicator') {
        return 'structure'
    }
    if (importPath.includes('.trend.')) {
        return 'trend'
    }
    if (importPath.includes('.momentum.')) {
        return 'momentum'
    }
    if (importPath.includes('.volatility.')) {
        return 'volatility'
    }
    if (importPath.includes('.overlay.')) {
        return 'overlay'
    }
    if (importPath.includes('.features.')) {
        return 'features'
    }

    return 'other'
}

export function getFeatureFamilyLabel(family) {
    const labels = {
        neural: 'Neural',
        structure: 'Structure',
        trend: 'Trend',
        momentum: 'Momentum',
        volatility: 'Volatility',
        overlay: 'Overlay',
        features: 'Features',
        other: 'Other',
    }

    return labels[family] || 'Other'
}

export function readFavoriteFeatureNames() {
    if (typeof window === 'undefined') {
        return []
    }

    try {
        const raw = window.localStorage.getItem(FEATURE_FAVORITES_STORAGE_KEY) || '[]'
        const parsed = JSON.parse(raw)
        return sortFeatureNames(parsed)
    } catch {
        return []
    }
}

export function persistFavoriteFeatureNames(values = []) {
    const nextValues = sortFeatureNames(values)

    if (typeof window === 'undefined') {
        return nextValues
    }

    window.localStorage.setItem(FEATURE_FAVORITES_STORAGE_KEY, JSON.stringify(nextValues))
    window.dispatchEvent(new CustomEvent(FEATURE_FAVORITES_EVENT, { detail: nextValues }))
    return nextValues
}

export function toggleFavoriteFeatureName(name, currentValues = null) {
    const safeName = normalizeFeatureName(name)
    if (!safeName) {
        return readFavoriteFeatureNames()
    }

    const baseline = currentValues === null ? readFavoriteFeatureNames() : sortFeatureNames(currentValues)
    const nextValues = baseline.includes(safeName)
        ? baseline.filter((value) => value !== safeName)
        : [...baseline, safeName]

    return persistFavoriteFeatureNames(nextValues)
}

export function subscribeFavoriteFeatureNames(listener) {
    if (typeof window === 'undefined' || typeof listener !== 'function') {
        return () => {}
    }

    function handleFavoriteEvent(event) {
        const detail = Array.isArray(event?.detail) ? event.detail : readFavoriteFeatureNames()
        listener(sortFeatureNames(detail))
    }

    function handleStorageEvent(event) {
        if (event.key && event.key !== FEATURE_FAVORITES_STORAGE_KEY) {
            return
        }
        listener(readFavoriteFeatureNames())
    }

    window.addEventListener(FEATURE_FAVORITES_EVENT, handleFavoriteEvent)
    window.addEventListener('storage', handleStorageEvent)

    return () => {
        window.removeEventListener(FEATURE_FAVORITES_EVENT, handleFavoriteEvent)
        window.removeEventListener('storage', handleStorageEvent)
    }
}

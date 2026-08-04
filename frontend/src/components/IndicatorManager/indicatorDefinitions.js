import {
    buildFieldValueMap,
    buildLineDefinitionsForEditor,
    buildParamsFromValues,
    getIndicatorManifestEntry,
    indicatorManifest,
} from '../../utils/indicatorManifest.js'

function normalizeDefinition(definition) {
    return {
        name: definition.name,
        label: definition.label,
        classification: definition.classification || '',
        fields: definition.fields || [],
        getInitialValues(indicator) {
            return buildFieldValueMap(definition, indicator?.params)
        },
        buildParams(values) {
            return buildParamsFromValues(definition, values)
        },
        buildLineDefinitions(values) {
            return buildLineDefinitionsForEditor(definition, values)
        },
    }
}

export const INDICATOR_DEFINITIONS = indicatorManifest.map(normalizeDefinition)
const INDICATOR_DEFINITIONS_BY_NAME = new Map(
    INDICATOR_DEFINITIONS.map((definition) => [definition.name, definition])
)

export function getIndicatorDefinition(name) {
    const normalizedName = String(name || '').trim()
    if (!normalizedName) {
        return null
    }

    if (INDICATOR_DEFINITIONS_BY_NAME.has(normalizedName)) {
        return INDICATOR_DEFINITIONS_BY_NAME.get(normalizedName)
    }

    const manifestEntry = getIndicatorManifestEntry(normalizedName)
    if (!manifestEntry) {
        return null
    }

    const normalizedDefinition = normalizeDefinition(manifestEntry)
    INDICATOR_DEFINITIONS_BY_NAME.set(normalizedName, normalizedDefinition)
    return normalizedDefinition
}

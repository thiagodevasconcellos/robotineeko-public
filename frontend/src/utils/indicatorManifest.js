import indicatorManifest from '../../shared/indicatorManifest.json'

function stringOrEmpty(value) {
    return typeof value === 'string' ? value.trim() : ''
}

function normalizeParams(params) {
    return Array.isArray(params) ? [...params] : []
}

function buildFilledParams(definition, params = []) {
    const safeParams = normalizeParams(params)
    const fields = Array.isArray(definition?.fields) ? definition.fields : []

    if (!fields.length) {
        return safeParams
    }

    return fields.map((field, index) => (
        safeParams[index] ?? field.defaultValue
    ))
}

function coerceNumber(value, fallback, min = null) {
    const parsed = Number(value)
    const safeValue = Number.isFinite(parsed) ? parsed : fallback

    if (min === null || min === undefined) {
        return safeValue
    }

    return Math.max(min, safeValue)
}

const INDICATOR_MANIFEST_BY_NAME = new Map(
    indicatorManifest.map((definition) => [String(definition?.name || '').trim(), definition])
)

export function getIndicatorManifestEntry(name) {
    return INDICATOR_MANIFEST_BY_NAME.get(String(name || '').trim()) || null
}

export function getIndicatorClassification(name) {
    return String(getIndicatorManifestEntry(name)?.classification || '').trim().toLowerCase()
}

export function getIndicatorPriceScaleMode(name) {
    return String(getIndicatorManifestEntry(name)?.defaultPriceScaleMode || 'shared').trim().toLowerCase()
}

export function getIndicatorRuntimeContract(name) {
    const runtimeContract = getIndicatorManifestEntry(name)?.runtimeContract
    return runtimeContract && typeof runtimeContract === 'object'
        ? { ...runtimeContract }
        : null
}

export function buildFieldValueMap(definition, params = []) {
    const values = {}
    const fields = Array.isArray(definition?.fields) ? definition.fields : []

    for (let index = 0; index < fields.length; index += 1) {
        const field = fields[index]
        values[field.key] = params[index] ?? field.defaultValue
    }

    return values
}

export function buildParamsFromValues(definition, values) {
    return (definition?.fields || []).map((field) => {
        const rawValue = values?.[field.key]

        if (field.type === 'number') {
            return coerceNumber(rawValue, field.defaultValue, field.min ?? null)
        }

        return rawValue ?? field.defaultValue
    })
}

export function buildDefaultIndicatorParams(name) {
    const definition = getIndicatorManifestEntry(name)

    if (!definition) {
        return []
    }

    return buildParamsFromValues(definition, {})
}

export function buildManifestLineDefinitions(name, params = []) {
    const definition = getIndicatorManifestEntry(name)

    if (!definition) {
        return []
    }

    if (Array.isArray(definition.lines)) {
        return definition.lines.map((line) => ({ ...line }))
    }

    const dynamicFieldName = Object.keys(definition.dynamicLinesByField || {})[0]
    if (!dynamicFieldName) {
        return []
    }

    const values = buildFieldValueMap(definition, params)
    const activeValue = values?.[dynamicFieldName]
    const lineKeys = definition.dynamicLinesByField?.[dynamicFieldName]?.[activeValue]
        || definition.dynamicLinesByField?.[dynamicFieldName]?.all
        || []

    return lineKeys
        .map((key) => (definition.lineCatalog || []).find((line) => line.key === key))
        .filter(Boolean)
        .map((line) => ({ ...line }))
}

export function getManifestLineDefinition(indicatorName, lineKey = '', params = []) {
    const normalizedKey = String(lineKey || '').trim().toLowerCase()

    return buildManifestLineDefinitions(indicatorName, params).find(
        (line) => String(line?.key || '').trim().toLowerCase() === normalizedKey
    ) || null
}

export function buildBaseColumnNameFromManifest(name, params = []) {
    const safeName = String(name || '').trim()
    const manifestEntry = getIndicatorManifestEntry(safeName)

    if (!safeName) {
        return ''
    }

    if (!manifestEntry) {
        const safeParams = normalizeParams(params)
        return [safeName, ...safeParams].join('_')
    }

    const safeParams = buildFilledParams(manifestEntry, params)

    const paramIndexes = Array.isArray(manifestEntry.columnParamIndexes)
        ? manifestEntry.columnParamIndexes
        : safeParams.map((_, index) => index)

    const selectedParams = paramIndexes
        .map((index) => safeParams[index])
        .filter((value) => value !== undefined && value !== null && value !== '')

    return [safeName, ...selectedParams].join('_')
}

export function buildLegacyBaseColumnNameFromManifest(name, params = []) {
    const safeName = String(name || '').trim()
    const safeParams = normalizeParams(params)
    const manifestEntry = getIndicatorManifestEntry(safeName)

    if (!safeName) {
        return ''
    }

    if (!manifestEntry) {
        return [safeName, ...safeParams].join('_')
    }

    const paramIndexes = Array.isArray(manifestEntry.columnParamIndexes)
        ? manifestEntry.columnParamIndexes
        : safeParams.map((_, index) => index)

    const selectedParams = paramIndexes
        .map((index) => safeParams[index])
        .filter((value) => value !== undefined && value !== null && value !== '')

    return [safeName, ...selectedParams].join('_')
}

export function buildColumnNameFromManifestLine(name, params = [], line = null) {
    const baseColumnName = buildBaseColumnNameFromManifest(name, params)

    if (!baseColumnName) {
        return ''
    }

    const explicitSuffix = stringOrEmpty(line?.columnSuffix)
    if (explicitSuffix) {
        return `${baseColumnName}_${explicitSuffix}`
    }

    if (line && Object.prototype.hasOwnProperty.call(line, 'columnSuffix') && explicitSuffix === '') {
        return baseColumnName
    }

    const normalizedKey = String(line?.key || '').trim().toLowerCase()

    if (!normalizedKey || normalizedKey === 'value' || normalizedKey === 'main') {
        return baseColumnName
    }

    return `${baseColumnName}_${normalizedKey}`
}

export function buildLegacyColumnNameFromManifestLine(name, params = [], line = null) {
    const baseColumnName = buildLegacyBaseColumnNameFromManifest(name, params)

    if (!baseColumnName) {
        return ''
    }

    const explicitSuffix = stringOrEmpty(line?.columnSuffix)
    if (explicitSuffix) {
        return `${baseColumnName}_${explicitSuffix}`
    }

    if (line && Object.prototype.hasOwnProperty.call(line, 'columnSuffix') && explicitSuffix === '') {
        return baseColumnName
    }

    const normalizedKey = String(line?.key || '').trim().toLowerCase()

    if (!normalizedKey || normalizedKey === 'value' || normalizedKey === 'main') {
        return baseColumnName
    }

    return `${baseColumnName}_${normalizedKey}`
}

export function getManifestLineDefinitionsForColumn(indicatorName, columnName, params = []) {
    const normalizedColumnName = String(columnName || '').trim()

    if (!normalizedColumnName) {
        return []
    }

    return buildManifestLineDefinitions(indicatorName, params).filter(
        (line) => buildColumnNameFromManifestLine(indicatorName, params, line) === normalizedColumnName
    )
}

export function findManifestLineByColumnName(columnName = '') {
    const normalizedColumnName = String(columnName || '').trim()

    if (!normalizedColumnName) {
        return null
    }

    for (const definition of indicatorManifest) {
        const name = String(definition?.name || '').trim()
        const paramIndexes = Array.isArray(definition?.columnParamIndexes)
            ? definition.columnParamIndexes
            : (definition.fields || []).map((_, index) => index)

        const parts = normalizedColumnName.split('_')
        if (parts[0] !== name) {
            continue
        }

        const selectedParams = paramIndexes
            .map((index) => parts[index + 1])
            .filter((value) => value !== undefined)

        for (const line of buildManifestLineDefinitions(name, selectedParams)) {
            if (buildColumnNameFromManifestLine(name, selectedParams, line) === normalizedColumnName) {
                return {
                    indicatorName: name,
                    params: selectedParams,
                    line,
                }
            }
        }
    }

    return null
}

export function buildLineDefinitionsForEditor(definition, values) {
    const params = buildParamsFromValues(definition, values)

    return buildManifestLineDefinitions(definition?.name, params).map((line) => ({
        ...line,
        key: line.key,
        label: line.label,
        defaultColor: line.defaultColor,
        defaultLineWidth: line.defaultLineWidth || 2,
    }))
}

export { indicatorManifest }

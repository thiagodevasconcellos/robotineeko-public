import { normalizeChartSettings, normalizeIndicator } from './chartSettings.jsx'
import {
    buildDefaultIndicatorParams,
    buildColumnNameFromManifestLine,
    buildManifestLineDefinitions,
    buildLegacyColumnNameFromManifestLine,
    getIndicatorManifestEntry,
    indicatorManifest,
} from './indicatorManifest.js'

function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function cloneValue(value) {
    if (typeof structuredClone === 'function') {
        return structuredClone(value)
    }

    return JSON.parse(JSON.stringify(value))
}

function normalizeToken(value) {
    return String(value || '').trim()
}

function getIndicatorAliasCandidates(indicator) {
    const primaryAlias = normalizeToken(indicator?.alias)
    const indicatorName = normalizeToken(indicator?.name)
    const normalizedName = indicatorName.toLowerCase()
    const candidates = new Set()
    const hasExplicitCustomAlias = primaryAlias && primaryAlias.toLowerCase() !== normalizedName

    if (!hasExplicitCustomAlias && normalizedName === 'marketregime') {
        candidates.add('mreg')
    }
    if (!hasExplicitCustomAlias && normalizedName === 'rsi') {
        candidates.add('rsi')
    }

    if (primaryAlias) {
        candidates.add(primaryAlias)
        candidates.add(primaryAlias.toLowerCase())
    }

    if (!hasExplicitCustomAlias && indicatorName) {
        candidates.add(indicatorName)
        candidates.add(indicatorName.toLowerCase())
    }

    return [...candidates].filter(Boolean)
}

function hasExplicitCustomIndicatorAlias(indicator) {
    const primaryAlias = normalizeToken(indicator?.alias)
    const normalizedName = normalizeToken(indicator?.name).toLowerCase()
    return Boolean(primaryAlias) && primaryAlias.toLowerCase() !== normalizedName
}

function getColumnSuffix(columnName, indicatorName) {
    const safeColumnName = normalizeToken(columnName)
    const safeIndicatorName = normalizeToken(indicatorName)

    if (!safeColumnName) {
        return ''
    }

    if (!safeIndicatorName) {
        return safeColumnName
    }

    const prefix = `${safeIndicatorName}_`

    if (safeColumnName.startsWith(prefix)) {
        return safeColumnName.slice(prefix.length)
    }

    return safeColumnName
}

function getLineAliasSuffix(line, indicatorName) {
    const key = normalizeToken(line?.key)
    const label = normalizeToken(line?.label)
    const columnName = normalizeToken(line?.columnName)

    const normalizedKey = key.toLowerCase()

    if (normalizedKey === 'value' || normalizedKey === 'main') {
        return 'value'
    }

    if (normalizedKey) {
        return key
    }

    if (label && label.toLowerCase() !== normalizeToken(indicatorName).toLowerCase()) {
        return label
    }

    return getColumnSuffix(columnName, indicatorName)
}

export function getStrategyTokenNameForIndicatorLine(indicator, line) {
    const safeIndicatorAlias = normalizeToken(
        getIndicatorAliasCandidates(indicator)[0] || indicator?.alias || indicator?.name
    )
    const safeIndicatorName = normalizeToken(indicator?.name)
    const lines = Array.isArray(indicator?.lines) ? indicator.lines : []
    const suffix = normalizeToken(getLineAliasSuffix(line, safeIndicatorName))

    if (!safeIndicatorAlias) {
        return suffix
    }

    if (lines.length <= 1) {
        return safeIndicatorAlias
    }

    return suffix ? `${safeIndicatorAlias}_${suffix}` : safeIndicatorAlias
}

function buildAliasRegistry(chartSettings) {
    const normalizedChartSettings = normalizeChartSettings(chartSettings)
    const aliasToColumn = new Map()
    const duplicateAliases = new Map()
    const implicitAliasNameCounts = new Map()

    for (const indicator of normalizedChartSettings.indicators || []) {
        const normalizedName = normalizeToken(indicator?.name).toLowerCase()
        if (!normalizedName || hasExplicitCustomIndicatorAlias(indicator)) {
            continue
        }
        implicitAliasNameCounts.set(
            normalizedName,
            Number(implicitAliasNameCounts.get(normalizedName) || 0) + 1,
        )
    }

    function registerAlias(alias, columnName) {
        const safeAlias = normalizeToken(alias)
        const safeColumnName = normalizeToken(columnName)

        if (!safeAlias || !safeColumnName) {
            return
        }

        const existing = aliasToColumn.get(safeAlias)

        if (!existing) {
            aliasToColumn.set(safeAlias, safeColumnName)
            return
        }

        if (existing !== safeColumnName) {
            duplicateAliases.set(safeAlias, [existing, safeColumnName])
            aliasToColumn.delete(safeAlias)
        }
    }

    for (const indicator of normalizedChartSettings.indicators || []) {
        const safeIndicatorName = normalizeToken(indicator?.name)
        const normalizedIndicatorName = safeIndicatorName.toLowerCase()
        const aliasCandidates = getIndicatorAliasCandidates(indicator).filter((candidate) => {
            const safeCandidate = normalizeToken(candidate)
            if (!safeCandidate) {
                return false
            }

            if (Number(implicitAliasNameCounts.get(normalizedIndicatorName) || 0) <= 1) {
                return true
            }

            const lowerCandidate = safeCandidate.toLowerCase()
            if (lowerCandidate === normalizedIndicatorName) {
                return false
            }
            if (normalizedIndicatorName === 'marketregime' && lowerCandidate === 'mreg') {
                return false
            }

            return true
        })
        const indicatorParams = Array.isArray(indicator?.params) ? indicator.params : []
        const lines = Array.isArray(indicator?.lines) ? indicator.lines : []

        // Single-line indicator:
        // allow plain alias -> full column
        if (lines.length === 1) {
            const onlyLine = lines[0]
            const resolvedColumnName = (
                buildColumnNameFromManifestLine(safeIndicatorName, indicatorParams, onlyLine)
                || buildLegacyColumnNameFromManifestLine(safeIndicatorName, indicatorParams, onlyLine)
                || normalizeToken(onlyLine?.columnName)
            )

            for (const aliasCandidate of aliasCandidates) {
                registerAlias(aliasCandidate, resolvedColumnName)
            }
        }

        // Multi-line indicator:
        // do NOT register plain alias, because it is ambiguous.
        // Register only alias-qualified suffixes such as "bb_upper" or "donch_lower".
        // For single-line indicators, we already registered the explicit alias above.
        // This intentionally avoids generic bare suffixes such as "upper", "lower",
        // "middle" or "width", which collide across multi-line indicators.
        for (const line of lines) {
            if (lines.length === 1) {
                continue
            }

            const suffix = normalizeToken(getLineAliasSuffix(line, safeIndicatorName))
            const columnName = (
                buildColumnNameFromManifestLine(safeIndicatorName, indicatorParams, line)
                || buildLegacyColumnNameFromManifestLine(safeIndicatorName, indicatorParams, line)
                || normalizeToken(line?.columnName)
            )

            if (!columnName) {
                continue
            }

            if (suffix) {
                for (const aliasCandidate of aliasCandidates) {
                    if (aliasCandidate) {
                        registerAlias(`${aliasCandidate}_${suffix}`, columnName)
                    }
                }
            }

            const label = normalizeToken(line?.label)
            if (label && lines.length === 1) {
                registerAlias(label, columnName)
            }
        }
    }

    return {
        aliasToColumn,
        duplicateAliases,
    }
}

function buildColumnToAliasRegistry(chartSettings) {
    const normalizedChartSettings = normalizeChartSettings(chartSettings)
    const columnToAlias = new Map()

    function register(columnName, alias) {
        const safeColumnName = normalizeToken(columnName)
        const safeAlias = normalizeToken(alias)

        if (!safeColumnName || !safeAlias) {
            return
        }

        if (!columnToAlias.has(safeColumnName)) {
            columnToAlias.set(safeColumnName, safeAlias)
        }
    }

    for (const indicator of normalizedChartSettings.indicators || []) {
        const lines = Array.isArray(indicator?.lines) ? indicator.lines : []

        for (const line of lines) {
            const alias = getStrategyTokenNameForIndicatorLine(indicator, line)
            register(line?.columnName, alias)
            register(buildColumnNameFromManifestLine(indicator?.name, indicator?.params || [], line), alias)
            register(buildLegacyColumnNameFromManifestLine(indicator?.name, indicator?.params || [], line), alias)
        }
    }

    return columnToAlias
}

function resolveExpressionAliases(expression, aliasEntries) {
    if (typeof expression !== 'string' || expression.trim() === '') {
        return expression
    }

    let resolved = expression
    const sortedEntries = [...aliasEntries].sort((a, b) => b[0].length - a[0].length)

    for (const [alias, columnName] of sortedEntries) {
        const pattern = new RegExp(`\\b${escapeRegExp(alias)}\\b`, 'g')
        resolved = resolved.replace(pattern, columnName)
    }

    return resolved
}

export function getStrategyManifestIndicators(strategy) {
    return Array.isArray(strategy?.featureManifest?.indicators)
        ? strategy.featureManifest.indicators.map((indicator) => normalizeIndicator(indicator))
        : []
}

function getStrategyExpressions(strategy) {
    return [
        strategy?.long?.openIf,
        strategy?.long?.closeIf,
        strategy?.long?.openPrice,
        strategy?.long?.closePrice,
        strategy?.long?.gainPrice,
        strategy?.long?.lossPrice,
        strategy?.long?.trailingPrice,
        strategy?.short?.openIf,
        strategy?.short?.closeIf,
        strategy?.short?.openPrice,
        strategy?.short?.closePrice,
        strategy?.short?.gainPrice,
        strategy?.short?.lossPrice,
        strategy?.short?.trailingPrice,
    ]
        .filter((value) => typeof value === 'string')
        .map((value) => value.trim())
        .filter(Boolean)
}

const GENERIC_SINGLE_LINE_INDICATOR_NAMES = indicatorManifest
    .map((definition) => normalizeToken(definition?.name))
    .filter(Boolean)
    .filter((name) => {
        const defaultParams = buildDefaultIndicatorParams(name)
        return buildManifestLineDefinitions(name, defaultParams).length === 1
    })

function getGenericSingleLineIndicatorRequests(strategy) {
    const expressions = getStrategyExpressions(strategy)
    const requestedNames = new Set()

    for (const indicatorName of GENERIC_SINGLE_LINE_INDICATOR_NAMES) {
        const pattern = new RegExp(`\\b${escapeRegExp(indicatorName)}\\b(?!_)`, 'i')

        if (expressions.some((expression) => pattern.test(expression))) {
            requestedNames.add(indicatorName)
        }
    }

    return [...requestedNames]
}

export function inferIndicatorsFromStrategyExpressions(strategy) {
    const expressions = getStrategyExpressions(strategy)

    const indicatorMap = new Map()

    function registerIndicator(name, params = [], alias = '') {
        const normalized = normalizeIndicator({ name, params, alias })
        const key = `${normalized.name}:${JSON.stringify(normalized.params || [])}:${String(normalized.alias || '')}`
        if (!indicatorMap.has(key)) {
            indicatorMap.set(key, normalized)
        }
    }

    for (const expression of expressions) {
        const text = String(expression || '')

        for (const match of text.matchAll(/\bRSI_(?:close_)?(\d+)\b/gi)) {
            registerIndicator('RSI', ['close', Number(match[1])])
        }
        if (/\brsi\[\d+\]\b/i.test(text)) {
            registerIndicator('RSI', ['close', 14], 'rsi')
        }

        for (const match of text.matchAll(/\bEMA_(?:close_)?(\d+)\b/gi)) {
            registerIndicator('EMA', ['close', Number(match[1])])
        }
        for (const match of text.matchAll(/\bema(\d+)\[\d+\]\b/gi)) {
            registerIndicator('EMA', ['close', Number(match[1])], `ema${Number(match[1])}`)
        }

        for (const match of text.matchAll(/\bSMA_(?:close_)?(\d+)\b/gi)) {
            registerIndicator('SMA', ['close', Number(match[1])])
        }
        for (const match of text.matchAll(/\bsma(\d+)\[\d+\]\b/gi)) {
            registerIndicator('SMA', ['close', Number(match[1])], `sma${Number(match[1])}`)
        }

        for (const match of text.matchAll(/\bATR_(\d+)\b/gi)) {
            registerIndicator('ATR', [Number(match[1])])
        }
        for (const match of text.matchAll(/\batr(\d+)\[\d+\]\b/gi)) {
            registerIndicator('ATR', [Number(match[1])], `atr${Number(match[1])}`)
        }

        for (const match of text.matchAll(/\bADX_(\d+)\b/gi)) {
            registerIndicator('ADX', [Number(match[1])])
        }
        for (const match of text.matchAll(/\badx(\d+)\[\d+\]\b/gi)) {
            registerIndicator('ADX', [Number(match[1])], `adx${Number(match[1])}`)
        }

        for (const match of text.matchAll(/\bMACD_(?:close_)?(\d+)_(\d+)_(\d+)(?:_[A-Za-z]+)?\b/gi)) {
            registerIndicator('MACD', ['close', Number(match[1]), Number(match[2]), Number(match[3])])
        }
        if (/\bmacd(?:_[a-z]+)?\[\d+\]\b/i.test(text)) {
            registerIndicator('MACD', ['close', 12, 26, 9], 'macd')
        }

        for (const match of text.matchAll(/\bBollingerBands_(?:close_)?(\d+)_(\d+(?:\.\d+)?)(?:_[A-Za-z]+)?\b/gi)) {
            registerIndicator('BollingerBands', ['close', Number(match[1]), Number(match[2])])
        }
        if (/\bbb_(?:upper|lower|middle|width)\[\d+\]\b/i.test(text)) {
            registerIndicator('BollingerBands', ['close', 20, 2], 'bb')
        }
    }

    return Array.from(indicatorMap.values())
}

export function buildStrategyAliasContextChartSettings(baseChartSettings, strategy, extraIndicators = []) {
    const normalizedChartSettings = normalizeChartSettings(baseChartSettings || {})
    const manifestIndicators = getStrategyManifestIndicators(strategy)
    const inferredIndicators = inferIndicatorsFromStrategyExpressions(strategy)
    const providedIndicators = Array.isArray(extraIndicators)
        ? extraIndicators.map((indicator) => normalizeIndicator(indicator))
        : []

    const mergedIndicators = []
    const seen = new Set()

    for (const indicator of [
        ...(Array.isArray(normalizedChartSettings.indicators) ? normalizedChartSettings.indicators : []),
        ...providedIndicators,
        ...manifestIndicators,
        ...inferredIndicators,
    ]) {
        const normalized = normalizeIndicator(indicator)
        const key = `${normalized.name}:${JSON.stringify(normalized.params || [])}:${String(normalized.alias || '')}`
        if (seen.has(key)) {
            continue
        }
        seen.add(key)
        mergedIndicators.push(normalized)
    }

    let nextChartSettings = normalizeChartSettings({
        ...normalizedChartSettings,
        indicators: mergedIndicators,
    })

    const genericIndicatorRequests = getGenericSingleLineIndicatorRequests(strategy)

    if (genericIndicatorRequests.length > 0) {
        const { aliasToColumn } = buildAliasRegistry(nextChartSettings)

        for (const indicatorName of genericIndicatorRequests) {
            const safeIndicatorName = normalizeToken(indicatorName)
            const lowerIndicatorName = safeIndicatorName.toLowerCase()

            if (
                aliasToColumn.has(safeIndicatorName)
                || aliasToColumn.has(lowerIndicatorName)
                || !getIndicatorManifestEntry(safeIndicatorName)
            ) {
                continue
            }

            const defaultParams = buildDefaultIndicatorParams(safeIndicatorName)

            if (buildManifestLineDefinitions(safeIndicatorName, defaultParams).length !== 1) {
                continue
            }

            const normalized = normalizeIndicator({
                name: safeIndicatorName,
                params: defaultParams,
            })
            const key = `${normalized.name}:${JSON.stringify(normalized.params || [])}:${String(normalized.alias || '')}`

            if (seen.has(key)) {
                continue
            }

            seen.add(key)
            mergedIndicators.push(normalized)
        }

        nextChartSettings = normalizeChartSettings({
            ...normalizedChartSettings,
            indicators: mergedIndicators,
        })
    }

    return nextChartSettings
}

export function buildStrategySetAliasContextChartSettings(
    baseChartSettings,
    primaryStrategy,
    strategyEntries = [],
    extraIndicators = [],
) {
    let nextChartSettings = buildStrategyAliasContextChartSettings(
        baseChartSettings,
        primaryStrategy,
        extraIndicators,
    )

    for (const entry of Array.isArray(strategyEntries) ? strategyEntries : []) {
        if (!entry || typeof entry !== 'object') {
            continue
        }
        nextChartSettings = buildStrategyAliasContextChartSettings(
            nextChartSettings,
            entry?.strategy || {},
            nextChartSettings?.indicators || [],
        )
    }

    return nextChartSettings
}

export function resolveStrategyAliasesInStrategy(strategy, chartSettings) {
    const { aliasToColumn, duplicateAliases } = buildAliasRegistry(chartSettings)

    if (duplicateAliases.size > 0) {
        const referencedDuplicates = new Set()
        for (const section of Object.values(strategy || {})) {
            if (!section || typeof section !== 'object') {
                continue
            }
            for (const fieldValue of Object.values(section)) {
                if (typeof fieldValue !== 'string' || !fieldValue.trim()) {
                    continue
                }
                for (const duplicateAlias of duplicateAliases.keys()) {
                    const pattern = new RegExp(`\\b${escapeRegExp(duplicateAlias)}\\b`)
                    if (pattern.test(fieldValue)) {
                        referencedDuplicates.add(duplicateAlias)
                    }
                }
            }
        }
        if (referencedDuplicates.size > 0) {
            const duplicateList = [...referencedDuplicates.keys()].join(', ')
            throw new Error(`Duplicate indicator aliases found: ${duplicateList}`)
        }
    }

    const aliasEntries = [...aliasToColumn.entries()]

    if (aliasEntries.length === 0) {
        return strategy
    }

    const resolvedStrategy = cloneValue(strategy)

    for (const [sectionName, section] of Object.entries(resolvedStrategy || {})) {
        if (!section || typeof section !== 'object') {
            continue
        }

        for (const [fieldName, fieldValue] of Object.entries(section)) {
            if (typeof fieldValue !== 'string') {
                continue
            }

            resolvedStrategy[sectionName][fieldName] = resolveExpressionAliases(fieldValue, aliasEntries)
        }
    }

    return resolvedStrategy
}

export function migrateStrategyFeatureNamesToAliases(strategy, chartSettings) {
    const columnToAlias = buildColumnToAliasRegistry(chartSettings)
    const replacements = [...columnToAlias.entries()].sort((left, right) => right[0].length - left[0].length)

    if (replacements.length === 0) {
        return strategy
    }

    const migratedStrategy = cloneValue(strategy)

    for (const [sectionName, section] of Object.entries(migratedStrategy || {})) {
        if (!section || typeof section !== 'object') {
            continue
        }

        for (const [fieldName, fieldValue] of Object.entries(section)) {
            if (typeof fieldValue !== 'string' || !fieldValue.trim()) {
                continue
            }

            let nextValue = fieldValue
            for (const [columnName, alias] of replacements) {
                const pattern = new RegExp(`\\b${escapeRegExp(columnName)}\\b`, 'g')
                nextValue = nextValue.replace(pattern, alias)
            }

            migratedStrategy[sectionName][fieldName] = nextValue
        }
    }

    return migratedStrategy
}

export function getStrategyTokenCandidates(chartSettings) {
    const { aliasToColumn } = buildAliasRegistry(chartSettings)
    const candidates = new Set([
        'open',
        'high',
        'low',
        'close',
        'long_open_price',
        'short_open_price',
        'True',
        'False',
        'and',
        'or',
    ])

    for (const [alias, columnName] of aliasToColumn.entries()) {
        if (alias) {
            candidates.add(alias)
        }

        if (columnName) {
            candidates.add(columnName)
        }
    }

    return [...candidates]
}

export function getStrategyTokenGroups(chartSettings) {
    const normalizedChartSettings = normalizeChartSettings(chartSettings)
    const groups = [
        {
            id: 'conditionals',
            label: 'Conditionals',
            items: [
                { token: 'True', color: '#22c55e' },
                { token: 'False', color: '#ef4444' },
                { token: 'and', color: '#9aa4b2' },
                { token: 'or', color: '#9aa4b2' },
            ],
        },
        {
            id: 'market',
            label: 'Market',
            items: [
                { token: 'open', color: '#6bb8ff' },
                { token: 'high', color: '#4caf50' },
                { token: 'low', color: '#ffb04d' },
                { token: 'close', color: '#ef4444' },
            ],
        },
        {
            id: 'positions',
            label: 'Positions',
            items: [
                { token: 'long_open_price', color: '#4caf50' },
                { token: 'short_open_price', color: '#ffb04d' },
            ],
        },
    ]

    for (const indicator of normalizedChartSettings.indicators || []) {
        const safeIndicatorAlias = normalizeToken(indicator?.alias || indicator?.name)
        const lines = Array.isArray(indicator?.lines) ? indicator.lines : []

        if (!safeIndicatorAlias || lines.length === 0) {
            continue
        }

        const items = lines
            .map((line) => {
                const token = getStrategyTokenNameForIndicatorLine(indicator, line)

                if (!token) {
                    return null
                }

                return {
                    token,
                    color: normalizeToken(line?.color) || '#6bb8ff',
                    lineLabel: normalizeToken(line?.label),
                }
            })
            .filter(Boolean)

        if (items.length === 0) {
            continue
        }

        groups.push({
            id: indicator.id || safeIndicatorAlias,
            label: safeIndicatorAlias,
            items,
        })
    }

    return groups
}

export function getStrategyAliasForColumnName(columnName, chartSettings) {
    const columnToAlias = buildColumnToAliasRegistry(chartSettings)
    const safeColumnName = normalizeToken(columnName)

    if (!safeColumnName) {
        return ''
    }

    return columnToAlias.get(safeColumnName) || ''
}

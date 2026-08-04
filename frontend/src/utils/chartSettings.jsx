import {
    buildParamsFromValues,
    buildBaseColumnNameFromManifest,
    buildColumnNameFromManifestLine,
    buildManifestLineDefinitions,
    findManifestLineByColumnName,
    getIndicatorManifestEntry,
    getIndicatorPriceScaleMode,
    getManifestLineDefinition,
    getManifestLineDefinitionsForColumn,
} from './indicatorManifest.js'

function stringOrEmpty(value) {
    return typeof value === 'string' ? value.trim() : ''
}

function normalizeStringArray(values) {
    if (!Array.isArray(values)) {
        return []
    }

    const normalized = values
        .map((value) => String(value || '').trim())
        .filter(Boolean)

    return [...new Set(normalized)]
}

function normalizeIndicatorParams(indicator) {
    const rawParams = indicator?.params
    if (Array.isArray(rawParams)) {
        return [...rawParams]
    }

    if (!rawParams || typeof rawParams !== 'object') {
        return []
    }

    const manifestEntry = getIndicatorManifestEntry(indicator?.name)
    if (!manifestEntry) {
        return []
    }

    const values = { ...rawParams }
    if (values.price === undefined && values.source !== undefined) {
        values.price = values.source
    }

    return buildParamsFromValues(manifestEntry, values)
}

function normalizeLineTarget(value) {
    const normalized = String(value || '').trim().toLowerCase()

    if (normalized === 'price' || normalized === 'separate' || normalized === 'hidden') {
        return normalized
    }

    return ''
}

export function buildIndicatorId(indicator, index = 0) {
    const safeName = String(indicator?.name || 'indicator').toLowerCase()
    return indicator?.id || `${safeName}-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

export function getColumnColor(columnName, fallbackIndex = 0, indicatorName = '', lineKey = '') {
    const manifestLine = getManifestLineDefinition(indicatorName, lineKey)
        || getManifestLineDefinitionsForColumn(indicatorName, columnName)[0]
        || findManifestLineByColumnName(columnName)?.line

    if (stringOrEmpty(manifestLine?.defaultColor)) {
        return stringOrEmpty(manifestLine.defaultColor)
    }

    const normalized = String(columnName || '').toLowerCase()

    if (normalized.includes('tenkan_sen')) return '#ff9800'
    if (normalized.includes('kijun_sen')) return '#2196f3'
    if (normalized.includes('senkou_span_a')) return '#4caf50'
    if (normalized.includes('senkou_span_b')) return '#f44336'
    if (normalized.includes('chikou_span')) return '#9c27b0'
    if (normalized.includes('upper')) return '#4caf50'
    if (normalized.includes('lower')) return '#f44336'
    if (normalized.includes('middle')) return '#2196f3'
    if (normalized.includes('signal')) return '#ff9800'
    if (normalized.includes('hist')) return '#9c27b0'
    if (normalized.includes('plus_di')) return '#66bb6a'
    if (normalized.includes('minus_di')) return '#ef5350'
    if (normalized.endsWith('_k')) return '#42a5f5'
    if (normalized.endsWith('_d')) return '#ab47bc'
    if (normalized.includes('rsi')) return '#b388ff'

    const fallbackColors = [
        '#ff9800',
        '#2196f3',
        '#4caf50',
        '#f44336',
        '#9c27b0',
        '#00bcd4',
        '#ffc107',
        '#8bc34a',
    ]

    return fallbackColors[fallbackIndex % fallbackColors.length]
}

function buildBaseColumnName(name, params = []) {
    return buildBaseColumnNameFromManifest(name, params)
}

function buildColumnsFromLineDefinitions(name, params = [], lines = []) {
    if (!Array.isArray(lines) || lines.length === 0) {
        const baseColumnName = buildBaseColumnName(name, params)
        return baseColumnName ? [baseColumnName] : []
    }

    return lines
        .map((line) => {
            const manifestLine = getManifestLineDefinition(name, stringOrEmpty(line?.key))
            return buildColumnNameFromManifestLine(name, params, manifestLine || line)
        })
        .filter(Boolean)
}

function getExpectedColumnNameForLine(name, params = [], line = null) {
    const manifestLine = getManifestLineDefinition(name, stringOrEmpty(line?.key), params)
    return buildColumnNameFromManifestLine(name, params, manifestLine || line)
}

function findBestColumnForLine(indicatorName, params = [], line, columns = [], lineIndex = 0) {
    const normalizedKey = String(line?.key || '').trim().toLowerCase()
    const normalizedLabel = String(line?.label || '').trim().toLowerCase()

    if (columns.length === 0) {
        return ''
    }

    const matchByToken = (token) => columns.find((column) => String(column).toLowerCase().includes(token))
    const manifestLine = getManifestLineDefinition(indicatorName, normalizedKey)
    const manifestColumnName = buildColumnNameFromManifestLine(indicatorName, params, manifestLine)

    if (manifestColumnName && columns.includes(manifestColumnName)) {
        return manifestColumnName
    }

    if (normalizedKey) {
        const directKeyMatch = matchByToken(normalizedKey)
        if (directKeyMatch) {
            return directKeyMatch
        }
    }

    if (normalizedLabel) {
        const directLabelMatch = matchByToken(normalizedLabel)
        if (directLabelMatch) {
            return directLabelMatch
        }
    }

    if (normalizedKey === 'value' || normalizedKey === 'main') {
        return columns[0]
    }

    if (normalizedKey === 'upper') {
        return matchByToken('upper') || columns[lineIndex] || columns[0]
    }

    if (normalizedKey === 'lower') {
        return matchByToken('lower') || columns[lineIndex] || columns[0]
    }

    if (normalizedKey === 'middle' || normalizedKey === 'basis' || normalizedKey === 'center') {
        return matchByToken('middle') || matchByToken('basis') || matchByToken('center') || columns[lineIndex] || columns[0]
    }

    if (normalizedKey === 'signal') {
        return matchByToken('signal') || columns[lineIndex] || columns[0]
    }

    if (normalizedKey === 'histogram' || normalizedKey === 'hist') {
        return matchByToken('hist') || columns[lineIndex] || columns[0]
    }

    if (normalizedKey === 'plus_di') {
        return matchByToken('plus_di') || columns[lineIndex] || columns[0]
    }

    if (normalizedKey === 'minus_di') {
        return matchByToken('minus_di') || columns[lineIndex] || columns[0]
    }

    if (normalizedKey === 'k') {
        return matchByToken('_k') || matchByToken('stochastic') || columns[lineIndex] || columns[0]
    }

    if (normalizedKey === 'd') {
        return matchByToken('_d') || matchByToken('stochastic') || columns[lineIndex] || columns[0]
    }

    return columns[lineIndex] || columns[0]
}

function getDefaultSeparatePaneId(indicatorName, indicatorAlias, line) {
    const safeAlias = stringOrEmpty(indicatorAlias)
    const safeName = stringOrEmpty(indicatorName)

    if (safeAlias) {
        return safeAlias
    }

    if (safeName) {
        return safeName
    }

    const columnName = stringOrEmpty(line?.columnName)
    return columnName || ''
}

function resolveDefaultLinePlacement(indicatorName, indicatorAlias, line) {
    const normalizedKey = String(line?.key || '').trim().toLowerCase()
    const defaultPaneId = getDefaultSeparatePaneId(indicatorName, indicatorAlias, line)
    const manifestLine = getManifestLineDefinition(indicatorName, normalizedKey)
    const normalizedTarget = normalizeLineTarget(manifestLine?.defaultTarget)

    if (!normalizedTarget) {
        return {
            target: 'price',
            paneId: '',
        }
    }

    return {
        target: normalizedTarget,
        paneId: normalizedTarget === 'separate' ? defaultPaneId : '',
    }
}

function buildManifestDerivedLines(name, params = [], alias = '') {
    const manifestLines = buildManifestLineDefinitions(name, params)
    const baseColumnName = buildBaseColumnName(name, params)

    if (!baseColumnName || manifestLines.length === 0) {
        return []
    }

    return manifestLines.map((line, lineIndex) => {
        const columnName = buildColumnNameFromManifestLine(name, params, line)
        const {
            defaultColor,
            defaultLineWidth,
            defaultTarget,
            ...lineExtras
        } = line || {}

        return {
            ...lineExtras,
            key: line.key || columnName || `line-${lineIndex + 1}`,
            label: line.label || alias || name || columnName || `Line ${lineIndex + 1}`,
            columnName,
            color: stringOrEmpty(defaultColor) || getColumnColor(columnName, lineIndex, name, line.key),
            lineWidth: Number(defaultLineWidth) > 0 ? Number(defaultLineWidth) : 2,
            target: normalizeLineTarget(defaultTarget),
            paneId: normalizeLineTarget(defaultTarget) === 'separate'
                ? getDefaultSeparatePaneId(name, alias, { columnName })
                : '',
        }
    })
}

function buildFallbackLines(name, params = [], alias = '', columns = []) {
    const manifestDerivedLines = buildManifestDerivedLines(name, params, alias)

    if (manifestDerivedLines.length > 0) {
        return manifestDerivedLines
    }

    return columns.map((columnName, lineIndex) => ({
        key: columnName || `line-${lineIndex + 1}`,
        label: alias || name || columnName || `Line ${lineIndex + 1}`,
        columnName,
        color: getColumnColor(columnName, lineIndex, name),
        lineWidth: 2,
    }))
}

function buildFallbackColumns(name, params = [], lines = []) {
    const derivedColumns = buildColumnsFromLineDefinitions(name, params, lines)

    if (derivedColumns.length > 0) {
        return derivedColumns
    }

    return buildManifestDerivedLines(name, params).map((line) => line.columnName).filter(Boolean)
}

function normalizeLine(line, fallback = {}, fallbackIndex = 0, indicatorName = '', indicatorAlias = '') {
    const fallbackColumnName = stringOrEmpty(fallback.columnName)
    const fallbackLabel = stringOrEmpty(fallback.label)
    const fallbackKey = stringOrEmpty(fallback.key) || fallbackColumnName || `line-${fallbackIndex + 1}`
    const columnName = stringOrEmpty(
        line?.columnName
        || line?.column
        || fallbackColumnName
    )
    const manifestLine = getManifestLineDefinition(
        indicatorName,
        stringOrEmpty(line?.key) || fallbackKey
    )
    const manifestLineByColumn = !manifestLine && columnName
        ? getManifestLineDefinitionsForColumn(indicatorName, columnName)[0]
        : null
    const resolvedManifestLine = manifestLine || manifestLineByColumn || null

    const parsedLineWidth = Number(
        line?.lineWidth
        ?? line?.lineweight
        ?? line?.width
        ?? fallback?.lineWidth
        ?? resolvedManifestLine?.defaultLineWidth
        ?? 2
    )

    const label = stringOrEmpty(
        line?.label
        || line?.alias
        || fallbackLabel
        || resolvedManifestLine?.label
        || columnName
        || fallbackKey
    )

    const normalizedLine = {
        ...line,
        key: stringOrEmpty(line?.key) || fallbackKey,
        label,
        columnName,
        defaultTarget: normalizeLineTarget(
            line?.defaultTarget
            || fallback?.defaultTarget
            || resolvedManifestLine?.defaultTarget
        ),
        markerPosition: stringOrEmpty(
            line?.markerPosition
            || fallback?.markerPosition
            || resolvedManifestLine?.markerPosition
        ),
        markerShape: stringOrEmpty(
            line?.markerShape
            || fallback?.markerShape
            || resolvedManifestLine?.markerShape
        ),
        markerColor: stringOrEmpty(
            line?.markerColor
            || fallback?.markerColor
            || resolvedManifestLine?.markerColor
        ),
        markerText: stringOrEmpty(
            line?.markerText
            || fallback?.markerText
            || resolvedManifestLine?.markerText
        ),
        markerSize: Number(
            line?.markerSize
            ?? fallback?.markerSize
            ?? resolvedManifestLine?.markerSize
            ?? 0
        ) || undefined,
        markerMinValue: Number(
            line?.markerMinValue
            ?? fallback?.markerMinValue
            ?? resolvedManifestLine?.markerMinValue
            ?? 0
        ) || undefined,
        color: stringOrEmpty(line?.color)
            || stringOrEmpty(fallback?.color)
            || stringOrEmpty(resolvedManifestLine?.defaultColor)
            || getColumnColor(columnName, fallbackIndex, indicatorName, stringOrEmpty(line?.key) || fallbackKey),
        lineWidth: Number.isFinite(parsedLineWidth) && parsedLineWidth > 0 ? parsedLineWidth : 2,
    }

    return applyDefaultLinePlacement(normalizedLine, indicatorName, indicatorAlias)
}

function shouldResetToManifestDefaultPlacement(line, indicatorName, indicatorAlias) {
    const normalizedTarget = normalizeLineTarget(line?.target)
    const manifestLine = getManifestLineDefinition(
        indicatorName,
        stringOrEmpty(line?.key) || stringOrEmpty(line?.columnName)
    )
    const manifestTarget = normalizeLineTarget(manifestLine?.defaultTarget)

    if (normalizedTarget !== 'separate' || manifestTarget !== 'price') {
        return false
    }

    const paneId = stringOrEmpty(line?.paneId).toLowerCase()

    if (!paneId) {
        return true
    }

    const allowedPaneIds = new Set(
        [
            stringOrEmpty(indicatorAlias),
            stringOrEmpty(indicatorName),
            stringOrEmpty(line?.columnName),
            stringOrEmpty(line?.label),
        ]
            .map((value) => value.toLowerCase())
            .filter(Boolean)
    )

    return !allowedPaneIds.has(paneId)
}

export function normalizeIndicator(indicator, index = 0) {
    const name = stringOrEmpty(indicator?.name)
    const alias = stringOrEmpty(indicator?.alias || indicator?.label || name)
    const params = normalizeIndicatorParams(indicator)
    const manifestDerivedLines = buildManifestDerivedLines(name, params, alias)

    let lines = Array.isArray(indicator?.lines) ? [...indicator.lines] : []
    const providedColumns = normalizeStringArray(indicator?.columns)

    if (lines.length === 0 && providedColumns.length > 0) {
        lines = buildFallbackLines(name, params, alias, providedColumns)
    }

    const fallbackColumns = providedColumns.length > 0
        ? providedColumns
        : buildFallbackColumns(name, params, lines)

    if (lines.length === 0) {
        lines = buildFallbackLines(name, params, alias, fallbackColumns)
    }

    const normalizedLines = lines.map((line, lineIndex) => {
        const expectedColumnName = getExpectedColumnNameForLine(name, params, line)
        const mappedColumnName =
            expectedColumnName
            || stringOrEmpty(line?.columnName)
            || findBestColumnForLine(name, params, line, fallbackColumns, lineIndex)

        return normalizeLine(
            {
                ...line,
                key: stringOrEmpty(line?.key) || mappedColumnName || `line-${lineIndex + 1}`,
                columnName: mappedColumnName,
            },
            {
                key: stringOrEmpty(line?.key) || mappedColumnName || `line-${lineIndex + 1}`,
                label: stringOrEmpty(line?.label) || alias || name || mappedColumnName || `Line ${lineIndex + 1}`,
                columnName: mappedColumnName,
                color: stringOrEmpty(line?.color) || getColumnColor(mappedColumnName, lineIndex, name, stringOrEmpty(line?.key)),
                lineWidth: Number(line?.lineWidth) > 0 ? Number(line.lineWidth) : 2,
            },
            lineIndex,
            name,
            alias
        )
    })

    const existingLineKeys = new Set(
        normalizedLines
            .map((line) => stringOrEmpty(line?.key).toLowerCase())
            .filter(Boolean)
    )

    const completedLines = [
        ...normalizedLines,
        ...manifestDerivedLines
            .filter((line) => !existingLineKeys.has(stringOrEmpty(line?.key).toLowerCase()))
            .map((line) => normalizeLine(
                line,
                {
                    key: stringOrEmpty(line?.key) || `line-${normalizedLines.length + 1}`,
                    label: stringOrEmpty(line?.label) || alias || name || line?.columnName || `Line ${normalizedLines.length + 1}`,
                    columnName: stringOrEmpty(line?.columnName),
                    color: stringOrEmpty(line?.color) || getColumnColor(line?.columnName, normalizedLines.length, name, stringOrEmpty(line?.key)),
                    lineWidth: Number(line?.lineWidth) > 0 ? Number(line.lineWidth) : 2,
                },
                normalizedLines.length,
                name,
                alias
            )),
    ]

    const columns = normalizeStringArray([
        ...fallbackColumns,
        ...completedLines.map((line) => line.columnName),
    ])

    const repairedLines = completedLines.map((line, lineIndex) => {
        const safeColumnName = line.columnName || columns[lineIndex] || columns[0] || ''

        return normalizeLine(
            {
                ...line,
                columnName: safeColumnName,
            },
            {
                key: stringOrEmpty(line?.key) || safeColumnName || `line-${lineIndex + 1}`,
                label: line.label || alias || name || safeColumnName || `Line ${lineIndex + 1}`,
                columnName: safeColumnName,
                color: line.color || getColumnColor(safeColumnName, lineIndex, name, stringOrEmpty(line?.key)),
                lineWidth: line.lineWidth || 2,
            },
            lineIndex,
            name,
            alias
        )
    })

    return {
        ...indicator,
        id: buildIndicatorId(indicator, index),
        name,
        alias: alias || name,
        params,
        columns,
        lines: repairedLines,
    }
}

function applyDefaultLinePlacement(line, indicatorName, indicatorAlias) {
    if (shouldResetToManifestDefaultPlacement(line, indicatorName, indicatorAlias)) {
        const placement = resolveDefaultLinePlacement(indicatorName, indicatorAlias, line)

        return {
            ...line,
            target: placement.target,
            paneId: placement.target === 'separate' ? placement.paneId : '',
        }
    }

    const normalizedTarget = normalizeLineTarget(line?.target)

    if (normalizedTarget) {
        return {
            ...line,
            target: normalizedTarget,
            paneId: normalizedTarget === 'separate'
                ? stringOrEmpty(line?.paneId) || getDefaultSeparatePaneId(indicatorName, indicatorAlias, line)
                : '',
        }
    }

    const placement = resolveDefaultLinePlacement(indicatorName, indicatorAlias, line)

    return {
        ...line,
        target: placement.target,
        paneId: placement.target === 'separate' ? placement.paneId : '',
    }
}

export function normalizeIndicators(indicators) {
    if (!Array.isArray(indicators)) {
        return []
    }

    return indicators
        .map((indicator, index) => normalizeIndicator(indicator, index))
        .filter((indicator) => indicator.name)
}

function arePrimitiveArraysEqual(left = [], right = []) {
    if (left.length !== right.length) {
        return false
    }

    for (let i = 0; i < left.length; i += 1) {
        if (left[i] !== right[i]) {
            return false
        }
    }

    return true
}

function areLinesEqual(left = [], right = []) {
    if (left.length !== right.length) {
        return false
    }

    for (let i = 0; i < left.length; i += 1) {
        const a = left[i]
        const b = right[i]

        if (!b) return false
        if (a.key !== b.key) return false
        if (a.label !== b.label) return false
        if (a.columnName !== b.columnName) return false
        if (a.color !== b.color) return false
        if (Number(a.lineWidth) !== Number(b.lineWidth)) return false
        if (a.target !== b.target) return false
        if (stringOrEmpty(a.paneId) !== stringOrEmpty(b.paneId)) return false
    }

    return true
}

export function areIndicatorsEqual(left = [], right = []) {
    if (left.length !== right.length) {
        return false
    }

    for (let i = 0; i < left.length; i += 1) {
        const a = normalizeIndicator(left[i], i)
        const b = normalizeIndicator(right[i], i)

        if (!b) return false
        if (a.name !== b.name) return false
        if (a.alias !== b.alias) return false
        if (!arePrimitiveArraysEqual(a.params || [], b.params || [])) return false
        if (!arePrimitiveArraysEqual(a.columns || [], b.columns || [])) return false
        if (!areLinesEqual(a.lines || [], b.lines || [])) return false
    }

    return true
}

function paramsMatch(left = [], right = []) {
    if (left.length !== right.length) {
        return false
    }

    for (let i = 0; i < left.length; i += 1) {
        if (left[i] !== right[i]) {
            return false
        }
    }

    return true
}

function findMatchingVisualIndicator(appliedIndicator, visualIndicators = []) {
    const normalizedApplied = normalizeIndicator(appliedIndicator)

    for (const candidate of visualIndicators) {
        const normalizedCandidate = normalizeIndicator(candidate)

        const sharesColumn = normalizedApplied.columns.some((column) => normalizedCandidate.columns.includes(column))
        if (sharesColumn) {
            return normalizedCandidate
        }

        if (
            normalizedApplied.name === normalizedCandidate.name
            && paramsMatch(normalizedApplied.params || [], normalizedCandidate.params || [])
        ) {
            return normalizedCandidate
        }
    }

    return null
}

export function mergeAppliedIndicatorsWithVisualSettings(appliedIndicators = [], visualIndicators = []) {
    const normalizedAppliedIndicators = normalizeIndicators(appliedIndicators)
    const normalizedVisualIndicators = normalizeIndicators(visualIndicators)

    if (normalizedVisualIndicators.length === 0) {
        return []
    }

    return normalizedAppliedIndicators.flatMap((appliedIndicator, indicatorIndex) => {
        const visualIndicator = findMatchingVisualIndicator(appliedIndicator, normalizedVisualIndicators)

        if (!visualIndicator) {
            return []
        }

        const visualLinesByColumnName = {}
        for (const line of visualIndicator.lines || []) {
            if (line.columnName) {
                visualLinesByColumnName[line.columnName] = line
            }
        }

        const mergedLines = (appliedIndicator.columns || []).map((columnName, lineIndex) => {
            const manifestMatch = findManifestLineByColumnName(columnName)
            const fallbackManifestLine = manifestMatch?.indicatorName === appliedIndicator.name
                ? manifestMatch.line
                : null
            const visualLine = visualLinesByColumnName[columnName] || fallbackManifestLine

            return normalizeLine(
                {
                    ...(visualLine || {}),
                    key: stringOrEmpty(visualLine?.key) || columnName || `line-${lineIndex + 1}`,
                    columnName,
                    label: visualLine?.label || visualIndicator.alias || appliedIndicator.alias || appliedIndicator.name,
                    target: visualLine?.target,
                    paneId: visualLine?.paneId,
                },
                {
                    key: stringOrEmpty(visualLine?.key) || columnName || `line-${lineIndex + 1}`,
                    label: visualIndicator.alias || appliedIndicator.alias || appliedIndicator.name || columnName || `Line ${lineIndex + 1}`,
                    columnName,
                    color: visualLine?.color || getColumnColor(columnName, lineIndex, appliedIndicator.name, stringOrEmpty(visualLine?.key)),
                    lineWidth: visualLine?.lineWidth || 2,
                },
                lineIndex,
                appliedIndicator.name,
                visualIndicator.alias || appliedIndicator.alias || appliedIndicator.name
            )
        })

        return [
            normalizeIndicator(
                {
                    ...appliedIndicator,
                    alias: visualIndicator.alias || appliedIndicator.alias || appliedIndicator.name,
                    lines: mergedLines,
                },
                indicatorIndex
            ),
        ]
    })
}

export function normalizeChartSettings(settings) {
    return {
        symbol: String(settings?.symbol || 'EURUSD').trim().toUpperCase(),
        timeframe: String(settings?.timeframe || 'M1').trim().toUpperCase(),
        bars: Math.max(1, Number(settings?.bars) || 1),
        indicators: normalizeIndicators(settings?.indicators || []),
        precision: settings.precision ?? 5,
    }
}

export function mergeChartSettingsWithVisuals(settings, visualIndicators = []) {
    const normalizedSettings = normalizeChartSettings(settings)
    const mergedIndicators = mergeAppliedIndicatorsWithVisualSettings(
        normalizedSettings.indicators,
        visualIndicators
    )

    return {
        ...normalizedSettings,
        indicators: mergedIndicators,
    }
}

export function areChartSettingsEqual(left, right) {
    if (!left || !right) {
        return false
    }

    return (
        left.symbol === right.symbol
        && left.timeframe === right.timeframe
        && Number(left.bars) === Number(right.bars)
        && areIndicatorsEqual(left.indicators, right.indicators)
    )
}

export function formatIndicatorLabel(indicator) {
    const normalized = normalizeIndicator(indicator)
    const paramsText = normalized.params.length > 0 ? normalized.params.join(', ') : ''

    if (paramsText && normalized.alias && normalized.alias !== normalized.name) {
        return `${normalized.alias} · ${normalized.name} (${paramsText})`
    }

    if (paramsText) {
        return `${normalized.alias} (${paramsText})`
    }

    if (normalized.alias && normalized.alias !== normalized.name) {
        return `${normalized.alias} · ${normalized.name}`
    }

    return normalized.alias || normalized.name
}

export function buildBackendIndicatorsPayload(indicators = []) {
    return normalizeIndicators(indicators).map((indicator) => ({
        name: indicator.name,
        params: indicator.params ?? [],
        alias: indicator.alias || indicator.name || '',
    }))
}

export function getIndicatorSeriesOptions(indicator, line, fallbackIndex = 0) {
    const columnName = line?.columnName || ''
    const indicatorName = stringOrEmpty(indicator?.name)
    const priceScaleMode = getIndicatorPriceScaleMode(indicatorName)

    const baseOptions = {
        color: stringOrEmpty(line?.color) || getColumnColor(columnName, fallbackIndex, indicatorName, stringOrEmpty(line?.key)),
        lineWidth: Number(line?.lineWidth) > 0 ? Number(line.lineWidth) : 2,
        priceScaleId: 'right',
        priceLineVisible: false,
        lastValueVisible: true,
    }

    if (priceScaleMode === 'per_line') {
        return {
            ...baseOptions,
            priceScaleId: `${indicatorName || 'indicator'}-scale-${columnName}`,
        }
    }

    return baseOptions
}

function cloneSerializable(value, fallback = null) {
    try {
        if (typeof structuredClone === 'function') {
            return structuredClone(value)
        }
        return JSON.parse(JSON.stringify(value))
    } catch {
        return fallback
    }
}

function normalizeMarketValue(value, fallback = '') {
    return String(value || fallback || '').trim().toUpperCase()
}

export function normalizeSavedPortfolioMode(value, fallback = 'parallel_sleeves') {
    const normalized = String(value || fallback || 'parallel_sleeves').trim().toLowerCase()
    return normalized === 'shared_pipe' ? 'shared_pipe' : 'parallel_sleeves'
}

export function normalizeSavedPortfolioVolumeMode(value, fallback = 'fixed_volume') {
    const normalized = String(value || fallback || 'fixed_volume').trim().toLowerCase()
    if (normalized === 'max_affordable' || normalized === 'base_volume_compounding') {
        return normalized
    }
    return 'fixed_volume'
}

export function buildSavedPortfolioId(prefix = 'portfolio', index = 0) {
    return `${prefix}-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

export function buildSavedPortfolioPipelineId(portfolioId = 'portfolio', index = 0) {
    return `${portfolioId}-pipeline-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

export function buildSavedPortfolioEntryId(prefix = 'entry', index = 0) {
    return `${prefix}-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

function buildDerivedStrategyLabel(strategy, index = 0) {
    const longOpen = String(strategy?.long?.openIf || '').trim()
    const shortOpen = String(strategy?.short?.openIf || '').trim()
    if (longOpen && shortOpen) {
        return `Strategy ${index + 1} · Long/Short`
    }
    if (longOpen) {
        return `Strategy ${index + 1} · Long`
    }
    if (shortOpen) {
        return `Strategy ${index + 1} · Short`
    }
    return `Strategy ${index + 1}`
}

function normalizePositiveNumberOrNull(value) {
    const parsed = Number(value)
    if (!Number.isFinite(parsed) || parsed <= 0) {
        return null
    }
    return parsed
}

export function normalizeSavedPortfolioEntry(entry, index = 0) {
    const safeEntry = entry && typeof entry === 'object' ? entry : {}
    const strategy = safeEntry?.strategy && typeof safeEntry.strategy === 'object'
        ? cloneSerializable(safeEntry.strategy, {})
        : {}
    return {
        id: String(safeEntry.id || buildSavedPortfolioEntryId('entry', index)).trim() || buildSavedPortfolioEntryId('entry', index),
        label: String(safeEntry.label || `Strategy ${index + 1}`).trim() || `Strategy ${index + 1}`,
        enabled: safeEntry.enabled !== false,
        sourceBenchmarkId: String(safeEntry.sourceBenchmarkId || safeEntry.source_benchmark_id || '').trim(),
        sourceBenchmarkLabel: String(safeEntry.sourceBenchmarkLabel || safeEntry.source_benchmark_label || '').trim(),
        sourceBenchmarkEntryLabel: String(safeEntry.sourceBenchmarkEntryLabel || safeEntry.source_benchmark_entry_label || '').trim(),
        symbol: normalizeMarketValue(safeEntry.symbol, 'EURUSD'),
        timeframe: normalizeMarketValue(safeEntry.timeframe, 'M1'),
        volumeMode: normalizeSavedPortfolioVolumeMode(safeEntry.volumeMode || safeEntry.volume_mode),
        fixedVolume: normalizePositiveNumberOrNull(safeEntry.fixedVolume ?? safeEntry.fixed_volume),
        baseVolume: normalizePositiveNumberOrNull(safeEntry.baseVolume ?? safeEntry.base_volume),
        maxVolumeCap: normalizePositiveNumberOrNull(safeEntry.maxVolumeCap ?? safeEntry.max_volume_cap),
        referenceCapital: normalizePositiveNumberOrNull(safeEntry.referenceCapital ?? safeEntry.reference_capital),
        strategy,
    }
}

export function normalizeSavedPortfolioPipeline(pipeline, index = 0, portfolioId = 'portfolio') {
    const safePipeline = pipeline && typeof pipeline === 'object' ? pipeline : {}
    const pipelineId = String(safePipeline.id || buildSavedPortfolioPipelineId(portfolioId, index)).trim() || buildSavedPortfolioPipelineId(portfolioId, index)
    const entries = Array.isArray(safePipeline.entries)
        ? safePipeline.entries
        : Array.isArray(safePipeline.strategyEntries)
            ? safePipeline.strategyEntries
            : []
    return {
        id: pipelineId,
        label: String(safePipeline.label || `Pipeline ${index + 1}`).trim() || `Pipeline ${index + 1}`,
        enabled: safePipeline.enabled !== false,
        portfolioMode: normalizeSavedPortfolioMode(safePipeline.portfolioMode || safePipeline.portfolio_mode),
        entries: entries
            .map((entry, entryIndex) => normalizeSavedPortfolioEntry(entry, entryIndex))
            .filter((entry) => entry.strategy && typeof entry.strategy === 'object'),
    }
}

export function normalizeSavedPortfolioDefinition(portfolio, index = 0) {
    const safePortfolio = portfolio && typeof portfolio === 'object' ? portfolio : {}
    const portfolioId = String(safePortfolio.id || buildSavedPortfolioId('portfolio', index)).trim() || buildSavedPortfolioId('portfolio', index)
    return {
        id: portfolioId,
        label: String(safePortfolio.label || `Portfolio ${index + 1}`).trim() || `Portfolio ${index + 1}`,
        enabled: safePortfolio.enabled !== false,
        capitalMode: String(safePortfolio.capitalMode || safePortfolio.capital_mode || 'equity_percent').trim() || 'equity_percent',
        capitalValue: normalizePositiveNumberOrNull(safePortfolio.capitalValue ?? safePortfolio.capital_value),
        rebalanceMode: String(safePortfolio.rebalanceMode || safePortfolio.rebalance_mode || 'static').trim() || 'static',
        pipelines: Array.isArray(safePortfolio.pipelines)
            ? safePortfolio.pipelines.map((pipeline, pipelineIndex) => normalizeSavedPortfolioPipeline(pipeline, pipelineIndex, portfolioId))
            : [],
    }
}

export function normalizeSavedPortfolioRecord(record, index = 0) {
    const safeRecord = record && typeof record === 'object' ? record : {}
    return {
        id: String(safeRecord.id || '').trim(),
        label: String(safeRecord.label || '').trim() || `Portfolio ${index + 1}`,
        source: String(safeRecord.source || '').trim(),
        notes: String(safeRecord.notes || '').trim(),
        is_favorite: Boolean(safeRecord.is_favorite),
        portfolioStructureVersion: 2,
        portfolio: normalizeSavedPortfolioDefinition(safeRecord.portfolio, index),
        capitalModel: safeRecord.capitalModel && typeof safeRecord.capitalModel === 'object'
            ? cloneSerializable(safeRecord.capitalModel, {})
            : {},
        created_at: Number(safeRecord.created_at || safeRecord.createdAt || 0) || null,
        updated_at: Number(safeRecord.updated_at || safeRecord.updatedAt || 0) || null,
    }
}

export function summarizeSavedPortfolio(record) {
    const normalized = normalizeSavedPortfolioRecord(record)
    const pipelines = Array.isArray(normalized.portfolio?.pipelines) ? normalized.portfolio.pipelines : []
    const entryCount = pipelines.reduce((count, pipeline) => count + (Array.isArray(pipeline?.entries) ? pipeline.entries.length : 0), 0)
    return {
        pipelineCount: pipelines.length,
        entryCount,
        enabledEntryCount: pipelines.reduce((count, pipeline) => (
            count + (Array.isArray(pipeline?.entries) ? pipeline.entries.filter((entry) => entry?.enabled !== false).length : 0)
        ), 0),
    }
}

export function buildSavedPortfolioEntriesFromBenchmark(
    benchmark,
    {
        fallbackSymbol = 'EURUSD',
        fallbackTimeframe = 'M1',
        defaultVolumeMode = 'fixed_volume',
        defaultFixedVolume = 0.01,
        startIndex = 0,
    } = {},
) {
    const safeBenchmark = benchmark && typeof benchmark === 'object' ? benchmark : {}
    const primarySymbol = normalizeMarketValue(safeBenchmark.symbol || safeBenchmark?.strategy?.symbol || fallbackSymbol, fallbackSymbol)
    const primaryTimeframe = normalizeMarketValue(safeBenchmark.timeframe || safeBenchmark?.strategy?.timeframe || fallbackTimeframe, fallbackTimeframe)
    const benchmarkLabel = String(safeBenchmark.label || '').trim() || `Strategy ${startIndex + 1}`
    const entries = []
    const seenSignatures = new Set()

    function normalizeLabelForMatch(value) {
        return String(value || '')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
    }

    function scoreLabelMatch(targetLabel, candidateLabel) {
        const safeTarget = normalizeLabelForMatch(targetLabel)
        const safeCandidate = normalizeLabelForMatch(candidateLabel)
        if (!safeTarget || !safeCandidate) {
            return 0
        }
        if (safeTarget === safeCandidate) {
            return 1000
        }
        if (safeTarget.includes(safeCandidate) || safeCandidate.includes(safeTarget)) {
            return 600
        }

        const targetTokens = safeTarget.split(' ').map((token) => token.trim()).filter(Boolean)
        const candidateTokens = new Set(safeCandidate.split(' ').map((token) => token.trim()).filter(Boolean))
        let score = 0
        for (const token of targetTokens) {
            if (!candidateTokens.has(token)) {
                continue
            }
            score += /\d/.test(token) ? 5 : 2
        }
        return score
    }

    function buildEntrySignature(entry) {
        return JSON.stringify({
            symbol: normalizeMarketValue(entry?.symbol),
            timeframe: normalizeMarketValue(entry?.timeframe),
            strategy: cloneSerializable(entry?.strategy, {}),
        })
    }

    function pushEntry(entry, indexOffset, preferredVisibleLabel = '') {
        const normalizedEntry = normalizeSavedPortfolioEntry({
            ...entry,
            id: buildSavedPortfolioEntryId('entry', startIndex + indexOffset),
        }, startIndex + indexOffset)
        const signature = buildEntrySignature(normalizedEntry)
        if (seenSignatures.has(signature)) {
            return
        }
        seenSignatures.add(signature)
        if (preferredVisibleLabel) {
            normalizedEntry.label = preferredVisibleLabel
        }
        entries.push(normalizedEntry)
    }

    if (safeBenchmark.strategy && typeof safeBenchmark.strategy === 'object') {
        const primarySourceLabel = String(safeBenchmark.strategyLabel || safeBenchmark.strategy_label || '').trim()
            || buildDerivedStrategyLabel(safeBenchmark.strategy, startIndex)
        pushEntry({
            label: benchmarkLabel,
            enabled: true,
            sourceBenchmarkId: String(safeBenchmark.id || '').trim(),
            sourceBenchmarkLabel: benchmarkLabel,
            sourceBenchmarkEntryLabel: primarySourceLabel,
            symbol: primarySymbol,
            timeframe: primaryTimeframe,
            volumeMode: defaultVolumeMode,
            fixedVolume: defaultFixedVolume,
            strategy: cloneSerializable(safeBenchmark.strategy, safeBenchmark.strategy),
        }, entries.length, primarySourceLabel)
    }

    if (Array.isArray(safeBenchmark.strategies)) {
        safeBenchmark.strategies.forEach((entry) => {
            if (!entry?.strategy || typeof entry.strategy !== 'object') {
                return
            }
            const sourceEntryLabel = String(entry?.label || '').trim() || benchmarkLabel
            pushEntry({
                label: sourceEntryLabel,
                enabled: entry?.enabled !== false,
                sourceBenchmarkId: String(safeBenchmark.id || '').trim(),
                sourceBenchmarkLabel: benchmarkLabel,
                sourceBenchmarkEntryLabel: sourceEntryLabel,
                symbol: normalizeMarketValue(entry?.symbol, primarySymbol),
                timeframe: normalizeMarketValue(entry?.timeframe, primaryTimeframe),
                volumeMode: normalizeSavedPortfolioVolumeMode(entry?.volumeMode || entry?.volume_mode || defaultVolumeMode),
                fixedVolume: normalizePositiveNumberOrNull(entry?.fixedVolume ?? entry?.fixed_volume ?? entry?.volume) || defaultFixedVolume,
                baseVolume: normalizePositiveNumberOrNull(entry?.baseVolume ?? entry?.base_volume),
                maxVolumeCap: normalizePositiveNumberOrNull(entry?.maxVolumeCap ?? entry?.max_volume_cap),
                referenceCapital: normalizePositiveNumberOrNull(entry?.referenceCapital ?? entry?.reference_capital),
                strategy: cloneSerializable(entry.strategy, entry.strategy),
            }, entries.length)
        })
    }

    const primaryCandidateIndex = entries.reduce((bestIndex, entry, index, array) => {
        const currentScore = scoreLabelMatch(benchmarkLabel, entry?.sourceBenchmarkEntryLabel || entry?.label)
        const bestScore = bestIndex >= 0
            ? scoreLabelMatch(benchmarkLabel, array[bestIndex]?.sourceBenchmarkEntryLabel || array[bestIndex]?.label)
            : -1
        if (currentScore > bestScore) {
            return index
        }
        return bestIndex
    }, -1)

    const orderedEntries = primaryCandidateIndex > 0
        ? [
            entries[primaryCandidateIndex],
            ...entries.filter((_, index) => index !== primaryCandidateIndex),
        ]
        : entries

    return orderedEntries.map((entry, index) => normalizeSavedPortfolioEntry({
        ...entry,
        label: index === 0 ? benchmarkLabel : entry.label,
        id: buildSavedPortfolioEntryId('entry', startIndex + index),
    }, startIndex + index))
}

function buildNeutralPipelineEntries(entries = [], {
    portfolioId = 'portfolio',
    portfolioLabel = 'Portfolio',
    pipelineId = 'pipeline',
    pipelineLabel = 'Pipeline',
    pipelineMode = 'parallel_sleeves',
} = {}) {
    return (Array.isArray(entries) ? entries : [])
        .map((entry, index) => {
            const safeEntry = entry && typeof entry === 'object' ? entry : {}
            return {
                ...safeEntry,
                id: String(safeEntry.id || buildSavedPortfolioEntryId('entry', index)).trim() || buildSavedPortfolioEntryId('entry', index),
                label: String(safeEntry.label || `Strategy ${index + 1}`).trim() || `Strategy ${index + 1}`,
                enabled: safeEntry.enabled !== false,
                portfolioId: String(safeEntry.portfolioId || safeEntry.portfolio_id || portfolioId).trim() || portfolioId,
                portfolioLabel: String(safeEntry.portfolioLabel || safeEntry.portfolio_label || portfolioLabel).trim() || portfolioLabel,
                pipelineId: String(safeEntry.pipelineId || safeEntry.pipeline_id || pipelineId).trim() || pipelineId,
                pipelineLabel: String(safeEntry.pipelineLabel || safeEntry.pipeline_label || pipelineLabel).trim() || pipelineLabel,
                portfolioMode: normalizeSavedPortfolioMode(safeEntry.portfolioMode || safeEntry.portfolio_mode || pipelineMode),
            }
        })
}

function buildExistingPortfolioMetaMap(existingPortfolios = [], entryKey = 'entries') {
    const portfolioMap = new Map()
    const pipelineMap = new Map()

    ;(Array.isArray(existingPortfolios) ? existingPortfolios : []).forEach((portfolio, portfolioIndex) => {
        const safePortfolio = portfolio && typeof portfolio === 'object' ? portfolio : {}
        const portfolioId = String(safePortfolio.id || `portfolio-${portfolioIndex + 1}`).trim() || `portfolio-${portfolioIndex + 1}`
        portfolioMap.set(portfolioId, {
            label: String(safePortfolio.label || `Portfolio ${portfolioIndex + 1}`).trim() || `Portfolio ${portfolioIndex + 1}`,
            enabled: safePortfolio.enabled !== false,
            capitalMode: String(safePortfolio.capitalMode || safePortfolio.capital_mode || 'equity_percent').trim() || 'equity_percent',
            capitalValue: normalizePositiveNumberOrNull(safePortfolio.capitalValue ?? safePortfolio.capital_value),
            rebalanceMode: String(safePortfolio.rebalanceMode || safePortfolio.rebalance_mode || 'static').trim() || 'static',
        })
        const pipelines = Array.isArray(safePortfolio.pipelines) ? safePortfolio.pipelines : []
        pipelines.forEach((pipeline, pipelineIndex) => {
            const safePipeline = pipeline && typeof pipeline === 'object' ? pipeline : {}
            const pipelineId = String(safePipeline.id || `${portfolioId}-pipeline-${pipelineIndex + 1}`).trim() || `${portfolioId}-pipeline-${pipelineIndex + 1}`
            const rawEntries = Array.isArray(safePipeline[entryKey]) ? safePipeline[entryKey] : []
            pipelineMap.set(`${portfolioId}::${pipelineId}`, {
                label: String(safePipeline.label || `Pipeline ${pipelineIndex + 1}`).trim() || `Pipeline ${pipelineIndex + 1}`,
                enabled: safePipeline.enabled !== false,
                portfolioMode: normalizeSavedPortfolioMode(safePipeline.portfolioMode || safePipeline.portfolio_mode),
                order: pipelineIndex,
                entriesById: new Map(
                    rawEntries
                        .map((entry) => [String(entry?.id || '').trim(), entry])
                        .filter(([id]) => Boolean(id))
                ),
            })
        })
    })

    return { portfolioMap, pipelineMap }
}

export function rebuildBacktestPortfoliosFromEntries(entries = [], existingPortfolios = [], fallbackPortfolioMode = 'parallel_sleeves') {
    const { portfolioMap, pipelineMap } = buildExistingPortfolioMetaMap(existingPortfolios, 'strategyEntries')
    const grouped = new Map()

    buildNeutralPipelineEntries(entries, {
        portfolioId: 'legacy-default',
        portfolioLabel: 'Legacy default portfolio',
        pipelineId: 'legacy-pipeline',
        pipelineLabel: 'Legacy pipeline',
        pipelineMode: fallbackPortfolioMode,
    }).forEach((entry) => {
        const portfolioId = String(entry.portfolioId || 'legacy-default').trim() || 'legacy-default'
        const pipelineId = String(entry.pipelineId || 'legacy-pipeline').trim() || 'legacy-pipeline'
        const portfolioLabel = String(entry.portfolioLabel || portfolioMap.get(portfolioId)?.label || 'Portfolio').trim() || 'Portfolio'
        const pipelineLabel = String(entry.pipelineLabel || pipelineMap.get(`${portfolioId}::${pipelineId}`)?.label || 'Pipeline').trim() || 'Pipeline'
        const bucketKey = `${portfolioId}::${pipelineId}`
        if (!grouped.has(bucketKey)) {
            grouped.set(bucketKey, {
                portfolioId,
                portfolioLabel,
                pipelineId,
                pipelineLabel,
                entryList: [],
            })
        }
        grouped.get(bucketKey).entryList.push(entry)
    })

    const portfolioBuckets = new Map()
    Array.from(grouped.values()).forEach((bucket) => {
        const meta = portfolioMap.get(bucket.portfolioId) || {}
        const portfolioRecord = portfolioBuckets.get(bucket.portfolioId) || {
            id: bucket.portfolioId,
            label: bucket.portfolioLabel || meta.label || 'Portfolio',
            enabled: meta.enabled !== false,
            capitalMode: meta.capitalMode || 'equity_percent',
            capitalValue: meta.capitalValue ?? null,
            rebalanceMode: meta.rebalanceMode || 'static',
            pipelines: [],
        }
        const pipelineMeta = pipelineMap.get(`${bucket.portfolioId}::${bucket.pipelineId}`) || {}
        portfolioRecord.pipelines.push({
            id: bucket.pipelineId,
            label: bucket.pipelineLabel || pipelineMeta.label || 'Pipeline',
            enabled: pipelineMeta.enabled !== false,
            portfolioMode: normalizeSavedPortfolioMode(
                bucket.entryList[0]?.portfolioMode || pipelineMeta.portfolioMode || fallbackPortfolioMode,
            ),
            strategyEntries: bucket.entryList.map((entry, index) => ({
                id: entry.id,
                label: entry.label,
                priority: index,
                enabled: entry.enabled !== false,
                symbol: normalizeMarketValue(entry.symbol, 'EURUSD'),
                timeframe: normalizeMarketValue(entry.timeframe, 'M1'),
                allocationMode: String(entry.allocationMode || 'fixed_volume').trim() || 'fixed_volume',
                allocationValue: entry.allocationValue ?? null,
                volumeMode: normalizeSavedPortfolioVolumeMode(entry.volumeMode || entry.volume_mode),
                fixedVolume: normalizePositiveNumberOrNull(entry.fixedVolume ?? entry.fixed_volume),
                baseVolume: normalizePositiveNumberOrNull(entry.baseVolume ?? entry.base_volume),
                maxVolumeCap: normalizePositiveNumberOrNull(entry.maxVolumeCap ?? entry.max_volume_cap),
                referenceCapital: normalizePositiveNumberOrNull(entry.referenceCapital ?? entry.reference_capital),
                portfolioId: bucket.portfolioId,
                portfolioLabel: bucket.portfolioLabel,
                pipelineId: bucket.pipelineId,
                pipelineLabel: bucket.pipelineLabel,
                strategy: cloneSerializable(entry.strategy, entry.strategy),
            })),
        })
        portfolioBuckets.set(bucket.portfolioId, portfolioRecord)
    })

    return Array.from(portfolioBuckets.values())
}

export function rebuildTradePortfoliosFromSleeves(sleeves = [], existingPortfolios = [], fallbackPortfolioMode = 'parallel_sleeves') {
    const { portfolioMap, pipelineMap } = buildExistingPortfolioMetaMap(existingPortfolios, 'sleeves')
    const grouped = new Map()

    buildNeutralPipelineEntries(sleeves, {
        portfolioId: 'legacy-default',
        portfolioLabel: 'Legacy default portfolio',
        pipelineId: 'legacy-pipeline',
        pipelineLabel: 'Legacy pipeline',
        pipelineMode: fallbackPortfolioMode,
    }).forEach((entry) => {
        const portfolioId = String(entry.portfolioId || 'legacy-default').trim() || 'legacy-default'
        const pipelineId = String(entry.pipelineId || 'legacy-pipeline').trim() || 'legacy-pipeline'
        const bucketKey = `${portfolioId}::${pipelineId}`
        if (!grouped.has(bucketKey)) {
            grouped.set(bucketKey, {
                portfolioId,
                portfolioLabel: String(entry.portfolioLabel || portfolioMap.get(portfolioId)?.label || 'Portfolio').trim() || 'Portfolio',
                pipelineId,
                pipelineLabel: String(entry.pipelineLabel || pipelineMap.get(bucketKey)?.label || 'Pipeline').trim() || 'Pipeline',
                sleeveList: [],
            })
        }
        grouped.get(bucketKey).sleeveList.push(entry)
    })

    const portfolioBuckets = new Map()
    Array.from(grouped.values()).forEach((bucket) => {
        const meta = portfolioMap.get(bucket.portfolioId) || {}
        const portfolioRecord = portfolioBuckets.get(bucket.portfolioId) || {
            id: bucket.portfolioId,
            label: bucket.portfolioLabel || meta.label || 'Portfolio',
            enabled: meta.enabled !== false,
            capitalMode: meta.capitalMode || 'equity_percent',
            capitalValue: meta.capitalValue ?? null,
            rebalanceMode: meta.rebalanceMode || 'static',
            pipelines: [],
        }
        const pipelineMeta = pipelineMap.get(`${bucket.portfolioId}::${bucket.pipelineId}`) || {}
        portfolioRecord.pipelines.push({
            id: bucket.pipelineId,
            label: bucket.pipelineLabel || pipelineMeta.label || 'Pipeline',
            enabled: pipelineMeta.enabled !== false,
            portfolioMode: normalizeSavedPortfolioMode(
                bucket.sleeveList[0]?.portfolioMode || pipelineMeta.portfolioMode || fallbackPortfolioMode,
            ),
            sleeves: bucket.sleeveList.map((entry) => ({
                id: entry.id,
                label: entry.label,
                enabled: entry.enabled !== false,
                symbol: normalizeMarketValue(entry.symbol, 'EURUSD'),
                timeframe: normalizeMarketValue(entry.timeframe, 'M1'),
                volume: Math.max(0.01, Number(entry.volume || entry.fixedVolume || entry.baseVolume || 0.01) || 0.01),
                volumeMode: normalizeSavedPortfolioVolumeMode(entry.volumeMode || entry.volume_mode),
                fixedVolume: normalizePositiveNumberOrNull(entry.fixedVolume ?? entry.fixed_volume),
                baseVolume: normalizePositiveNumberOrNull(entry.baseVolume ?? entry.base_volume),
                maxVolumeCap: normalizePositiveNumberOrNull(entry.maxVolumeCap ?? entry.max_volume_cap),
                referenceCapital: normalizePositiveNumberOrNull(entry.referenceCapital ?? entry.reference_capital),
                portfolioId: bucket.portfolioId,
                portfolioLabel: bucket.portfolioLabel,
                pipelineId: bucket.pipelineId,
                pipelineLabel: bucket.pipelineLabel,
                portfolioMode: normalizeSavedPortfolioMode(entry.portfolioMode || entry.portfolio_mode),
                sourceStrategyId: String(entry.sourceStrategyId || entry.source_strategy_id || '').trim(),
                strategyName: String(entry.strategyName || entry.strategy_name || entry.sourceStrategyLabel || '').trim(),
                strategy: cloneSerializable(entry.strategy, entry.strategy),
                indicators: Array.isArray(entry.indicators) ? cloneSerializable(entry.indicators, []) : [],
            })),
        })
        portfolioBuckets.set(bucket.portfolioId, portfolioRecord)
    })

    return Array.from(portfolioBuckets.values())
}

function ensureUniqueScopedId(baseId, existingIds, fallbackPrefix) {
    const normalizedBase = String(baseId || '').trim() || buildSavedPortfolioId(fallbackPrefix, existingIds.size)
    if (!existingIds.has(normalizedBase)) {
        existingIds.add(normalizedBase)
        return normalizedBase
    }
    let attempt = 2
    while (existingIds.has(`${normalizedBase}-${attempt}`)) {
        attempt += 1
    }
    const nextId = `${normalizedBase}-${attempt}`
    existingIds.add(nextId)
    return nextId
}

export function instantiateSavedPortfolioForBacktest(record, {
    existingPortfolioIds = [],
} = {}) {
    const normalized = normalizeSavedPortfolioRecord(record)
    const seenPortfolioIds = new Set(Array.isArray(existingPortfolioIds) ? existingPortfolioIds.map((id) => String(id || '').trim()).filter(Boolean) : [])
    const seenPipelineIds = new Set()
    const portfolioId = ensureUniqueScopedId(normalized.portfolio.id, seenPortfolioIds, 'portfolio')

    return {
        id: portfolioId,
        label: normalized.portfolio.label,
        enabled: normalized.portfolio.enabled !== false,
        capitalMode: normalized.portfolio.capitalMode,
        capitalValue: normalized.portfolio.capitalValue,
        rebalanceMode: normalized.portfolio.rebalanceMode,
        pipelines: (normalized.portfolio.pipelines || []).map((pipeline, pipelineIndex) => {
            const pipelineId = ensureUniqueScopedId(
                String(pipeline.id || `${portfolioId}-pipeline-${pipelineIndex + 1}`).trim(),
                seenPipelineIds,
                `${portfolioId}-pipeline`,
            )
            return {
                id: pipelineId,
                label: pipeline.label,
                enabled: pipeline.enabled !== false,
                portfolioMode: normalizeSavedPortfolioMode(pipeline.portfolioMode),
                strategyEntries: (pipeline.entries || []).map((entry, entryIndex) => ({
                    id: buildSavedPortfolioEntryId('strategy', entryIndex),
                    label: entry.label,
                    priority: entryIndex,
                    enabled: entry.enabled !== false,
                    symbol: entry.symbol,
                    timeframe: entry.timeframe,
                    allocationMode: 'fixed_volume',
                    allocationValue: entry.fixedVolume ?? null,
                    volumeMode: entry.volumeMode,
                    fixedVolume: entry.fixedVolume,
                    baseVolume: entry.baseVolume,
                    maxVolumeCap: entry.maxVolumeCap,
                    referenceCapital: entry.referenceCapital,
                    portfolioId,
                    portfolioLabel: normalized.portfolio.label,
                    pipelineId,
                    pipelineLabel: pipeline.label,
                    sourceStrategyId: entry.sourceBenchmarkId,
                    strategyName: entry.sourceBenchmarkLabel || entry.label,
                    strategy: cloneSerializable(entry.strategy, entry.strategy),
                })),
            }
        }),
    }
}

export function instantiateSavedPortfolioForTrader(record, {
    existingPortfolioIds = [],
} = {}) {
    const normalized = normalizeSavedPortfolioRecord(record)
    const seenPortfolioIds = new Set(Array.isArray(existingPortfolioIds) ? existingPortfolioIds.map((id) => String(id || '').trim()).filter(Boolean) : [])
    const seenPipelineIds = new Set()
    const portfolioId = ensureUniqueScopedId(normalized.portfolio.id, seenPortfolioIds, 'portfolio')

    return {
        id: portfolioId,
        label: normalized.portfolio.label,
        enabled: normalized.portfolio.enabled !== false,
        capitalMode: normalized.portfolio.capitalMode,
        capitalValue: normalized.portfolio.capitalValue,
        rebalanceMode: normalized.portfolio.rebalanceMode,
        pipelines: (normalized.portfolio.pipelines || []).map((pipeline, pipelineIndex) => {
            const pipelineId = ensureUniqueScopedId(
                String(pipeline.id || `${portfolioId}-pipeline-${pipelineIndex + 1}`).trim(),
                seenPipelineIds,
                `${portfolioId}-pipeline`,
            )
            return {
                id: pipelineId,
                label: pipeline.label,
                enabled: pipeline.enabled !== false,
                portfolioMode: normalizeSavedPortfolioMode(pipeline.portfolioMode),
                sleeves: (pipeline.entries || []).map((entry, entryIndex) => ({
                    id: buildSavedPortfolioEntryId('sleeve', entryIndex),
                    label: entry.label,
                    enabled: entry.enabled !== false,
                    symbol: entry.symbol,
                    timeframe: entry.timeframe,
                    volume: Math.max(0.01, Number(entry.fixedVolume || entry.baseVolume || 0.01) || 0.01),
                    volumeMode: entry.volumeMode,
                    fixedVolume: entry.fixedVolume,
                    baseVolume: entry.baseVolume,
                    maxVolumeCap: entry.maxVolumeCap,
                    referenceCapital: entry.referenceCapital,
                    portfolioId,
                    portfolioLabel: normalized.portfolio.label,
                    pipelineId,
                    pipelineLabel: pipeline.label,
                    portfolioMode: normalizeSavedPortfolioMode(pipeline.portfolioMode),
                    sourceStrategyId: entry.sourceBenchmarkId,
                    strategyName: entry.sourceBenchmarkLabel || entry.label,
                    strategy: cloneSerializable(entry.strategy, entry.strategy),
                    indicators: Array.isArray(entry?.strategy?.featureManifest?.indicators)
                        ? cloneSerializable(entry.strategy.featureManifest.indicators, [])
                        : [],
                })),
            }
        }),
    }
}

export { cloneSerializable, normalizeMarketValue }

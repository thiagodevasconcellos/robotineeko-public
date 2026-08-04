import {
    buildStrategyAliasContextChartSettings,
    buildStrategySetAliasContextChartSettings,
    migrateStrategyFeatureNamesToAliases,
} from './strategyAliases.jsx'
import { normalizeIndicator } from './chartSettings.jsx'

export function normalizeStrategyFeatureManifest(manifest) {
    return {
        indicators: Array.isArray(manifest?.indicators)
            ? manifest.indicators.map((indicator) => normalizeIndicator(indicator))
            : [],
    }
}

export function attachStrategyFeatureManifest(strategy, chartSettings, extraIndicators = []) {
    const mergedChartSettings = buildStrategyAliasContextChartSettings(
        chartSettings,
        strategy,
        extraIndicators,
    )
    return {
        ...strategy,
        featureManifest: normalizeStrategyFeatureManifest({
            indicators: mergedChartSettings.indicators,
        }),
    }
}

function normalizeStrategyBenchmarkEntries(entries = []) {
    return Array.isArray(entries)
        ? entries
            .filter((entry) => entry && typeof entry === 'object')
            .map((entry) => ({
                ...entry,
                strategy: entry?.strategy && typeof entry.strategy === 'object' ? entry.strategy : {},
            }))
        : []
}

export function buildStrategyCollectionChartSettings(
    baseChartSettings,
    strategy,
    strategyEntries = [],
    extraIndicators = [],
) {
    return buildStrategySetAliasContextChartSettings(
        baseChartSettings,
        strategy,
        normalizeStrategyBenchmarkEntries(strategyEntries),
        extraIndicators,
    )
}

export function buildStrategyBenchmarkPayload({
    label = '',
    notes = '',
    source = '',
    side = 'both',
    strategy = {},
    strategies = [],
    chartSettings = {},
    extraIndicators = [],
}) {
    const normalizedEntries = normalizeStrategyBenchmarkEntries(strategies)
    const strategyChartSettings = buildStrategyCollectionChartSettings(
        chartSettings,
        strategy,
        normalizedEntries,
        extraIndicators,
    )
    const aliasedPrimaryStrategy = migrateStrategyFeatureNamesToAliases(strategy || {}, strategyChartSettings)
    const aliasedEntries = normalizedEntries.map((entry) => ({
        ...entry,
        strategy: attachStrategyFeatureManifest(
            migrateStrategyFeatureNamesToAliases(entry?.strategy || {}, strategyChartSettings),
            strategyChartSettings,
            strategyChartSettings?.indicators || [],
        ),
    }))

    return {
        label: String(label || '').trim(),
        notes: String(notes || '').trim(),
        source: String(source || '').trim(),
        side: String(side || 'both').trim() || 'both',
        symbol: String(chartSettings?.symbol || '').trim().toUpperCase(),
        timeframe: String(chartSettings?.timeframe || '').trim().toUpperCase(),
        strategy: attachStrategyFeatureManifest(
            aliasedPrimaryStrategy,
            strategyChartSettings,
            strategyChartSettings?.indicators || [],
        ),
        strategies: aliasedEntries,
    }
}

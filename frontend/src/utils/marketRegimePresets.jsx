function getPreferredStrategyToken(tokenCandidates = [], suffix = '') {
    const safeSuffix = String(suffix || '').trim().toLowerCase()
    if (!safeSuffix) {
        return ''
    }

    const candidates = (tokenCandidates || [])
        .map((token) => String(token || '').trim())
        .filter(Boolean)

    const matches = candidates.filter((token) => token.toLowerCase().endsWith(`_${safeSuffix}`))
    if (matches.length > 0) {
        return matches.sort((left, right) => {
            const leftStartsWithIndicator = /^[A-Z][A-Za-z0-9]*_/.test(left)
            const rightStartsWithIndicator = /^[A-Z][A-Za-z0-9]*_/.test(right)

            if (leftStartsWithIndicator !== rightStartsWithIndicator) {
                return leftStartsWithIndicator ? 1 : -1
            }

            const leftIsBareSuffix = left.toLowerCase() === safeSuffix
            const rightIsBareSuffix = right.toLowerCase() === safeSuffix

            if (leftIsBareSuffix !== rightIsBareSuffix) {
                return leftIsBareSuffix ? 1 : -1
            }

            return left.length - right.length
        })[0]
    }

    return candidates.find((token) => token.toLowerCase() === safeSuffix) || ''
}

export function buildMarketRegimePresetRecommendation(lastBacktestResponse, presets = []) {
    const stats = lastBacktestResponse?.stats || {}
    const regimeRows = Array.isArray(stats.regime_summary) ? stats.regime_summary.flatMap((summary) => summary?.rows || []) : []
    const stabilityRows = Array.isArray(stats.regime_stability_summary) ? stats.regime_stability_summary.flatMap((summary) => summary?.rows || []) : []

    if (!regimeRows.length && !stabilityRows.length) {
        return null
    }

    const bestRegime = [...regimeRows].sort(
        (left, right) => Number(right?.avg_trade_net_pnl || 0) - Number(left?.avg_trade_net_pnl || 0)
    )[0] || null
    const bestStability = [...stabilityRows].sort(
        (left, right) => Number(right?.avg_trade_net_pnl || 0) - Number(left?.avg_trade_net_pnl || 0)
    )[0] || null

    let presetId = 'trend_mature'
    let reason = 'Trend continuation is the safest default until the report shows a stronger edge elsewhere.'

    const bestRegimeLabel = String(bestRegime?.regime_label || '')
    const bestBucketLabel = String(bestStability?.bucket_label || '')

    if (bestRegimeLabel.includes('volatile')) {
        presetId = 'volatile_push'
        reason = 'Recent results are stronger in volatile directional regimes.'
    } else if (bestRegimeLabel.includes('compression') || bestBucketLabel === 'building') {
        presetId = 'compression_release'
        reason = 'Recent results suggest the strategy benefits from early regime transitions and compression release.'
    } else if (bestBucketLabel === 'mature') {
        presetId = 'trend_mature'
        reason = 'Recent results are strongest when the regime is already mature and stable.'
    }

    const preset = presets.find((entry) => entry.id === presetId) || presets[0] || null
    if (!preset) {
        return null
    }

    return {
        preset,
        reason,
    }
}

export function formatPresetMetric(value, kind = 'number') {
    const number = Number(value)

    if (!Number.isFinite(number)) {
        return '-'
    }

    if (kind === 'percent') {
        return `${(number * 100).toFixed(2)}%`
    }

    if (kind === 'integer') {
        return `${Math.round(number)}`
    }

    return number.toFixed(2)
}

export const MARKET_REGIME_DOC_ITEMS = [
    {
        key: 'regime_code',
        label: 'mreg_regime_code',
        description: 'Discrete regime label: trend, volatile, range or compression.',
        expected: 'Closed set: -3 volatile_down, -2 trend_down, 0 range, 1 compression, 2 trend_up, 3 volatile_up.',
    },
    {
        key: 'trend_score',
        label: 'mreg_trend_score',
        description: 'How directional the market is right now.',
        expected: 'Continuous score, usually 0.00 to 1.00. Expect < 0.35 in weak/ranging markets, > 0.55 in cleaner trend.',
    },
    {
        key: 'direction_score',
        label: 'mreg_direction_score',
        description: 'Bullish or bearish tilt of the current regime.',
        expected: 'Continuous score, usually -1.00 to 1.00. Negative favors short, positive favors long, around 0 means neutral.',
    },
    {
        key: 'compression_score',
        label: 'mreg_compression_score',
        description: 'How compressed/coiled the market is before expansion.',
        expected: 'Continuous score, usually 0.00 to 1.00. Expect > 0.70 in stronger compression, < 0.35 after expansion.',
    },
    {
        key: 'stability_score',
        label: 'mreg_stability_score',
        description: 'How confirmed and trustworthy the current regime is.',
        expected: 'Continuous score, usually 0.00 to 1.00. Fragile < 0.35, building 0.35-0.65, mature > 0.65.',
    },
    {
        key: 'regime_age',
        label: 'mreg_regime_age',
        description: 'How many bars the current regime has lasted.',
        expected: 'Integer count from 0 upward. Early transition is often 0-2, more mature regime usually 3+ bars.',
    },
]

export function buildMarketRegimePresetModel(tokenCandidates = []) {
    const regimeCode = getPreferredStrategyToken(tokenCandidates, 'regime_code') || 'mreg_regime_code'
    const trendScore = getPreferredStrategyToken(tokenCandidates, 'trend_score') || 'mreg_trend_score'
    const directionScore = getPreferredStrategyToken(tokenCandidates, 'direction_score') || 'mreg_direction_score'
    const compressionScore = getPreferredStrategyToken(tokenCandidates, 'compression_score') || 'mreg_compression_score'
    const stabilityScore = getPreferredStrategyToken(tokenCandidates, 'stability_score') || 'mreg_stability_score'
    const regimeAge = getPreferredStrategyToken(tokenCandidates, 'regime_age') || 'mreg_regime_age'

    return {
        aliases: {
            regimeCode,
            trendScore,
            directionScore,
            compressionScore,
            stabilityScore,
            regimeAge,
        },
        longEntries: [
            {
                id: 'trend_mature',
                label: 'Trend mature',
                description: 'Only enter when bullish regime is already stable and confirmed.',
                openIf: `${regimeCode}[0] == 2 and ${trendScore}[0] >= 0.6 and ${directionScore}[0] > 0.25 and ${stabilityScore}[0] >= 0.55 and ${regimeAge}[0] >= 3`,
                closeIf: `${directionScore}[0] <= 0.05 or ${trendScore}[0] < 0.4 or ${stabilityScore}[0] < 0.35`,
                gainPrice: 'long_open_price[0] + (0.0001 * 18)',
                lossPrice: 'long_open_price[0] - (0.0001 * 10)',
                trailingPrice: 'long_open_price[0] + (0.0001 * (2 + (opened_order_life[0] or 0) * 2))',
            },
            {
                id: 'volatile_push',
                label: 'Volatile push',
                description: 'Allow stronger bullish continuation in volatile regime.',
                openIf: `${regimeCode}[0] == 3 and ${trendScore}[0] >= 0.58 and ${directionScore}[0] > 0.3 and ${stabilityScore}[0] >= 0.45`,
                closeIf: `${directionScore}[0] <= 0.1 or ${stabilityScore}[0] < 0.3`,
                gainPrice: 'long_open_price[0] + (0.0001 * 24)',
                lossPrice: 'long_open_price[0] - (0.0001 * 14)',
                trailingPrice: 'long_open_price[0] + (0.0001 * (3 + (opened_order_life[0] or 0) * 3))',
            },
            {
                id: 'compression_release',
                label: 'Compression release',
                description: 'Use compression plus bullish tilt as an early breakout filter.',
                openIf: `${compressionScore ? `${compressionScore}[0] >= 0.72 and ` : ''}${directionScore}[0] > 0.18 and ${stabilityScore}[0] >= 0.35 and ${regimeAge}[0] >= 1`,
                closeIf: `${trendScore}[0] < 0.3 or ${directionScore}[0] <= 0`,
                gainPrice: 'long_open_price[0] + (0.0001 * 14)',
                lossPrice: 'long_open_price[0] - (0.0001 * 8)',
                trailingPrice: 'long_open_price[0] + (0.0001 * (1 + (opened_order_life[0] or 0) * 1.5))',
            },
        ],
        shortEntries: [
            {
                id: 'trend_mature',
                label: 'Trend mature',
                description: 'Only enter when bearish regime is already stable and confirmed.',
                openIf: `${regimeCode}[0] == -2 and ${trendScore}[0] >= 0.6 and ${directionScore}[0] < -0.25 and ${stabilityScore}[0] >= 0.55 and ${regimeAge}[0] >= 3`,
                closeIf: `${directionScore}[0] >= -0.05 or ${trendScore}[0] < 0.4 or ${stabilityScore}[0] < 0.35`,
                gainPrice: 'short_open_price[0] - (0.0001 * 18)',
                lossPrice: 'short_open_price[0] + (0.0001 * 10)',
                trailingPrice: 'short_open_price[0] - (0.0001 * (2 + (opened_order_life[0] or 0) * 2))',
            },
            {
                id: 'volatile_push',
                label: 'Volatile push',
                description: 'Allow stronger bearish continuation in volatile regime.',
                openIf: `${regimeCode}[0] == -3 and ${trendScore}[0] >= 0.58 and ${directionScore}[0] < -0.3 and ${stabilityScore}[0] >= 0.45`,
                closeIf: `${directionScore}[0] >= -0.1 or ${stabilityScore}[0] < 0.3`,
                gainPrice: 'short_open_price[0] - (0.0001 * 24)',
                lossPrice: 'short_open_price[0] + (0.0001 * 14)',
                trailingPrice: 'short_open_price[0] - (0.0001 * (3 + (opened_order_life[0] or 0) * 3))',
            },
            {
                id: 'compression_release',
                label: 'Compression release',
                description: 'Use compression plus bearish tilt as an early breakout filter.',
                openIf: `${compressionScore ? `${compressionScore}[0] >= 0.72 and ` : ''}${directionScore}[0] < -0.18 and ${stabilityScore}[0] >= 0.35 and ${regimeAge}[0] >= 1`,
                closeIf: `${trendScore}[0] < 0.3 or ${directionScore}[0] >= 0`,
                gainPrice: 'short_open_price[0] - (0.0001 * 14)',
                lossPrice: 'short_open_price[0] + (0.0001 * 8)',
                trailingPrice: 'short_open_price[0] - (0.0001 * (1 + (opened_order_life[0] or 0) * 1.5))',
            },
        ],
    }
}

export function buildStrategyFromMarketRegimePreset(section = 'long', preset = null, baseStrategy = null) {
    if (!preset) {
        return baseStrategy
    }

    const safeSection = section === 'short' ? 'short' : 'long'
    const nextStrategy = JSON.parse(JSON.stringify(baseStrategy || {
        long: {
            openPrice: 'close[0]',
            closePrice: 'close[0]',
            openIf: 'False',
            closeIf: 'False',
            gainPrice: '',
            lossPrice: '',
            trailingPrice: '',
        },
        short: {
            openPrice: 'close[0]',
            closePrice: 'close[0]',
            openIf: 'False',
            closeIf: 'False',
            gainPrice: '',
            lossPrice: '',
            trailingPrice: '',
        },
        other: {
            allowInversion: false,
            priority: 'Short',
        },
    }))

    nextStrategy[safeSection].openIf = preset.openIf
    nextStrategy[safeSection].closeIf = preset.closeIf
    nextStrategy[safeSection].gainPrice = preset.gainPrice || ''
    nextStrategy[safeSection].lossPrice = preset.lossPrice || ''
    nextStrategy[safeSection].trailingPrice = preset.trailingPrice || ''

    return nextStrategy
}

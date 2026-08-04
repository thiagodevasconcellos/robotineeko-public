import { normalizeChartSettings, normalizeIndicator } from './chartSettings.jsx'
import { buildDefaultIndicatorParams } from './indicatorManifest.js'
import { getStrategyTokenNameForIndicatorLine } from './strategyAliases.jsx'

const ELLIOTT_INDICATOR_NAME = 'ElliottWaveProxyV1'
const ELLIOTT_DEFAULT_ALIAS = 'elliott'

function cloneValue(value) {
    if (typeof structuredClone === 'function') {
        return structuredClone(value)
    }

    return JSON.parse(JSON.stringify(value))
}

function findElliottIndicator(chartSettings = {}) {
    const normalizedChartSettings = normalizeChartSettings(chartSettings)
    const existing = (normalizedChartSettings.indicators || []).find(
        (indicator) => String(indicator?.name || '').trim() === ELLIOTT_INDICATOR_NAME
    )

    if (existing) {
        return normalizeIndicator(existing)
    }

    return normalizeIndicator({
        name: ELLIOTT_INDICATOR_NAME,
        alias: ELLIOTT_DEFAULT_ALIAS,
        params: buildDefaultIndicatorParams(ELLIOTT_INDICATOR_NAME),
    })
}

function buildTokenMap(indicator) {
    const tokens = {}

    for (const line of indicator?.lines || []) {
        const key = String(line?.key || '').trim()
        if (!key) {
            continue
        }

        const token = getStrategyTokenNameForIndicatorLine(indicator, line)
        if (token) {
            tokens[key] = token
        }
    }

    return tokens
}

export const ELLIOTT_WAVE_DOC_ITEMS = [
    {
        key: 'breakout_trigger',
        label: 'Breakout trigger',
        description: 'ATR-buffered break above resistance or below support confirms that the swing level was meaningfully violated.',
        expected: 'Used for the first breakout entry, not for the exit.',
    },
    {
        key: 'broken_level',
        label: 'Broken level',
        description: 'The exact support or resistance that got broken and then changes role.',
        expected: 'Acts as retest trigger and as the main logical exit line.',
    },
    {
        key: 'envelope',
        label: 'Retest envelope',
        description: 'ATR-based zone around the broken level to tolerate natural pullbacks without forcing the strategy to stop on noise.',
        expected: 'Protective stop anchor under broken resistance or above broken support.',
    },
    {
        key: 'projection',
        label: 'Projection target',
        description: 'Range-projection target derived from the last confirmed swing span.',
        expected: 'Primary take-profit line for the breakout leg.',
    },
]

export function buildElliottWavePresetModel(chartSettings = {}) {
    const indicator = findElliottIndicator(chartSettings)
    const tokens = buildTokenMap(indicator)

    const waveConfidence = tokens.wave_confidence || `${ELLIOTT_DEFAULT_ALIAS}_wave_confidence`
    const legDirection = tokens.active_leg_direction || `${ELLIOTT_DEFAULT_ALIAS}_active_leg_direction`
    const legSizeAtr = tokens.active_leg_size_atr || `${ELLIOTT_DEFAULT_ALIAS}_active_leg_size_atr`
    const retracementRatio = tokens.retracement_ratio || `${ELLIOTT_DEFAULT_ALIAS}_retracement_ratio`
    const correctionFlag = tokens.candidate_correction_flag || `${ELLIOTT_DEFAULT_ALIAS}_candidate_correction_flag`
    const bullBreakoutFlag = tokens.bull_breakout_flag || `${ELLIOTT_DEFAULT_ALIAS}_bull_breakout_flag`
    const bearBreakoutFlag = tokens.bear_breakout_flag || `${ELLIOTT_DEFAULT_ALIAS}_bear_breakout_flag`
    const bullBreakoutState = tokens.bull_breakout_state || `${ELLIOTT_DEFAULT_ALIAS}_bull_breakout_state`
    const bearBreakoutState = tokens.bear_breakout_state || `${ELLIOTT_DEFAULT_ALIAS}_bear_breakout_state`
    const bullBrokenLevel = tokens.bull_broken_resistance_level || `${ELLIOTT_DEFAULT_ALIAS}_bull_broken_resistance_level`
    const bearBrokenLevel = tokens.bear_broken_support_level || `${ELLIOTT_DEFAULT_ALIAS}_bear_broken_support_level`
    const bullSupportLow = tokens.bull_support_envelope_low || `${ELLIOTT_DEFAULT_ALIAS}_bull_support_envelope_low`
    const bullSupportHigh = tokens.bull_support_envelope_high || `${ELLIOTT_DEFAULT_ALIAS}_bull_support_envelope_high`
    const bearResistanceLow = tokens.bear_resistance_envelope_low || `${ELLIOTT_DEFAULT_ALIAS}_bear_resistance_envelope_low`
    const bearResistanceHigh = tokens.bear_resistance_envelope_high || `${ELLIOTT_DEFAULT_ALIAS}_bear_resistance_envelope_high`
    const bullProjection = tokens.bull_projection_target || `${ELLIOTT_DEFAULT_ALIAS}_bull_projection_target`
    const bearProjection = tokens.bear_projection_target || `${ELLIOTT_DEFAULT_ALIAS}_bear_projection_target`

    return {
        indicator,
        tokens: {
            waveConfidence,
            legDirection,
            legSizeAtr,
            retracementRatio,
            correctionFlag,
            bullBreakoutFlag,
            bearBreakoutFlag,
            bullBreakoutState,
            bearBreakoutState,
            bullBrokenLevel,
            bearBrokenLevel,
            bullSupportLow,
            bullSupportHigh,
            bearResistanceLow,
            bearResistanceHigh,
            bullProjection,
            bearProjection,
        },
        longEntries: [
            {
                id: 'breakout_envelope_support',
                label: 'Breakout envelope support',
                description: 'Enter on bullish breakout or retest of broken resistance acting as support. Exit when that level fails.',
                openIf: `(${legDirection}[0] > 0) and (${waveConfidence}[0] >= 0.45) and (${legSizeAtr}[0] >= 1.0) and ((${bullBreakoutFlag}[0] > 0) or ((${bullBreakoutState}[0] > 0) and (low[0] <= ${bullSupportHigh}[0]) and (close[0] >= ${bullBrokenLevel}[0]) and (close[0] > open[0])))`,
                closeIf: `((${bullBreakoutState}[0] > 0) and (close[0] < ${bullBrokenLevel}[0])) or (${bearBreakoutFlag}[0] > 0) or ((${correctionFlag}[0] > 0) and (${retracementRatio}[0] >= 0.786)) or (${waveConfidence}[0] < 0.25)`,
                gainPrice: `${bullProjection}[0]`,
                lossPrice: `${bullSupportLow}[0]`,
                trailingPrice: '',
            },
        ],
        shortEntries: [
            {
                id: 'breakout_envelope_resistance',
                label: 'Breakout envelope resistance',
                description: 'Enter on bearish breakout or retest of broken support acting as resistance. Exit when that level fails.',
                openIf: `(${legDirection}[0] < 0) and (${waveConfidence}[0] >= 0.45) and (${legSizeAtr}[0] >= 1.0) and ((${bearBreakoutFlag}[0] > 0) or ((${bearBreakoutState}[0] > 0) and (high[0] >= ${bearResistanceLow}[0]) and (close[0] <= ${bearBrokenLevel}[0]) and (close[0] < open[0])))`,
                closeIf: `((${bearBreakoutState}[0] > 0) and (close[0] > ${bearBrokenLevel}[0])) or (${bullBreakoutFlag}[0] > 0) or ((${correctionFlag}[0] > 0) and (${retracementRatio}[0] >= 0.786)) or (${waveConfidence}[0] < 0.25)`,
                gainPrice: `${bearProjection}[0]`,
                lossPrice: `${bearResistanceHigh}[0]`,
                trailingPrice: '',
            },
        ],
    }
}

export function buildStrategyFromElliottWavePreset(section = 'long', preset = null, baseStrategy = null) {
    if (!preset) {
        return baseStrategy
    }

    const safeSection = section === 'short' ? 'short' : 'long'
    const nextStrategy = cloneValue(baseStrategy || {
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
    })

    nextStrategy[safeSection].openIf = preset.openIf
    nextStrategy[safeSection].closeIf = preset.closeIf
    nextStrategy[safeSection].gainPrice = preset.gainPrice || ''
    nextStrategy[safeSection].lossPrice = preset.lossPrice || ''
    nextStrategy[safeSection].trailingPrice = preset.trailingPrice || ''
    nextStrategy[safeSection].openPrice = 'close[0]'
    nextStrategy[safeSection].closePrice = 'close[0]'

    return nextStrategy
}

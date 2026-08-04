import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import {
    createEmptyPositiveHistoryCatalogState,
    createLocalPositiveHistoryCatalogState,
    fetchSharedPositiveHistoryCatalog,
    mergeLocalAndSharedPositiveHistoryCatalog,
} from './positiveHistoryCatalogClient.js'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '../../api.js'
import { inferBrokerCodeFromSymbol } from '../../utils/brokerProfiles.js'

const WINNER_MONTHLY_PERCENT_BASELINE = 10000
const STATISTICAL_RELIABILITY_TRADE_TARGET = 30
const ROBUSTNESS_EVIDENCE_TOTAL_CHECKS = 4
const CATALOG_COLUMN_WIDTHS = ['18%', '10%', '12%', '14%', '6%', '9%', '8%', '9%', '8%', '6%']
const WINNER_COLUMN_WIDTHS = ['18%', '10%', '7%', '9%', '7%', '6%', '9%', '7%', '8%', '7%', '6%', '6%']
const WINNER_PROMOTABLE_COLUMN_WIDTHS = ['16%', '10%', '7%', '9%', '7%', '6%', '9%', '6%', '8%', '7%', '6%', '5%', '4%']
const LOCAL_POSITIVE_HISTORY_STATE = createLocalPositiveHistoryCatalogState()
const DEFAULT_POSITIVE_HISTORY_BROKER_ORDER = ['forex.com', 'clear']
const POSITIVE_HISTORY_BROKER_LABELS = Object.freeze({
    'forex.com': 'Forex.com',
    clear: 'CLEAR',
    oanda: 'OANDA',
    generic_mt5: 'Generic MT5',
    mixed: 'Mixed broker scope',
    unknown: 'Unclassified',
})

const SORTABLE_COLUMNS = {
    label: {
        label: 'Strategy',
        defaultDirection: 'asc',
        getValue: (entry) => String(entry?.label || ''),
    },
    completedAt: {
        label: 'Completed',
        defaultDirection: 'desc',
        getValue: (entry) => extractCompletionSortValue(entry),
    },
    context: {
        label: 'Context',
        defaultDirection: 'asc',
        getValue: (entry) => `${String(entry?.symbol || '')} ${String(entry?.timeframe || '')} ${String(entry?.side || '')}`.trim(),
    },
    checkpoint: {
        label: 'Positive checkpoint',
        defaultDirection: 'desc',
        getValue: (entry) => extractNumericCheckpoint(entry?.positiveCheckpoint),
    },
    trades: {
        label: 'Trades',
        defaultDirection: 'desc',
        getValue: (entry) => hasFiniteNumber(entry?.trades) ? Number(entry.trades) : Number.NEGATIVE_INFINITY,
    },
    candlesPerTrade: {
        label: 'Candles/trade',
        defaultDirection: 'asc',
        getValue: (entry) => {
            const value = extractCandlesPerTrade(entry)
            return value === null ? Number.POSITIVE_INFINITY : value
        },
    },
    tradesPerDay: {
        label: 'Trades/time',
        defaultDirection: 'desc',
        getValue: (entry) => extractTradesTimeSortValue(entry),
    },
    statisticalReliability: {
        label: 'Stat. reliability',
        defaultDirection: 'desc',
        getValue: (entry) => {
            const value = extractStatisticalReliabilityScore(entry)
            return value === null ? Number.NEGATIVE_INFINITY : value
        },
    },
    robustnessEvidence: {
        label: 'Robust. evidence',
        defaultDirection: 'desc',
        getValue: (entry) => extractRobustnessEvidenceSortValue(entry),
    },
    monthly: {
        label: '1M expectation',
        defaultDirection: 'desc',
        getValue: (entry) => extractCatalogMonthlySortValue(entry),
    },
}

function normalizePositiveHistoryBrokerCode(value) {
    const normalized = String(value || '').trim().toLowerCase()
    if (!normalized) {
        return ''
    }
    if (
        normalized === 'clear'
        || normalized === 'clear_b3'
        || normalized === 'b3'
        || normalized === 'brasil'
        || normalized === 'brazil'
        || normalized.startsWith('b3_')
    ) {
        return 'clear'
    }
    if (normalized === 'forex.com' || normalized === 'forex_com' || normalized === 'forex') {
        return 'forex.com'
    }
    if (normalized === 'oanda') {
        return 'oanda'
    }
    if (normalized === 'generic_mt5' || normalized === 'generic mt5') {
        return 'generic_mt5'
    }
    if (normalized === 'mixed') {
        return 'mixed'
    }
    return ''
}

function getPositiveHistoryBrokerLabel(code) {
    return POSITIVE_HISTORY_BROKER_LABELS[code] || 'Unclassified'
}

function inferPositiveHistoryBrokerCodeFromSymbol(value) {
    return inferBrokerCodeFromSymbol(value)
}

function resolvePositiveHistoryBrokerCode(entry) {
    const explicitCandidates = [
        entry?.resolvedBrokerCode,
        entry?.brokerCode,
        entry?.broker_code,
        entry?.broker,
        entry?.brokerLabel,
        entry?.broker_label,
        entry?.brokerProfileLabel,
        entry?.broker_profile_label,
        entry?.costProfile,
        entry?.cost_profile,
        entry?.marketDomain,
        entry?.market_domain,
        entry?.assetType,
        entry?.asset_type,
        entry?.executionPolicy?.broker_code,
        entry?.execution_policy?.broker_code,
        entry?.executionPolicy?.market_domain,
        entry?.execution_policy?.market_domain,
        entry?.executionPolicy?.cost_profile,
        entry?.execution_policy?.cost_profile,
        entry?.executionPolicy?.asset_type,
        entry?.execution_policy?.asset_type,
    ]
    for (const candidate of explicitCandidates) {
        const normalized = normalizePositiveHistoryBrokerCode(candidate)
        if (normalized) {
            return normalized
        }
    }

    const inferredFromSymbol = inferPositiveHistoryBrokerCodeFromSymbol(entry?.symbol)
    if (inferredFromSymbol) {
        return inferredFromSymbol
    }

    return 'unknown'
}

function decoratePositiveHistoryBrokerEntry(entry) {
    if (!entry || typeof entry !== 'object') {
        return entry
    }

    const resolvedBrokerCode = resolvePositiveHistoryBrokerCode(entry)
    return {
        ...entry,
        resolvedBrokerCode,
        resolvedBrokerLabel: getPositiveHistoryBrokerLabel(resolvedBrokerCode),
    }
}

function hasFiniteNumber(value) {
    return value !== null && value !== undefined && Number.isFinite(Number(value))
}

function extractNumericCheckpoint(value) {
    const text = String(value || '')
    const match = text.match(/[-+]?\d+(?:\.\d+)?/)
    if (!match) {
        return Number.NEGATIVE_INFINITY
    }
    const parsed = Number(match[0])
    return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY
}

function extractCompletionSortValue(entry) {
    const label = String(entry?.completedAtLabel || '').trim().toLowerCase()
    if (label === 'ongoing') {
        return Number.NEGATIVE_INFINITY
    }

    const raw = String(entry?.completedAt || '').trim()
    if (!raw) {
        return Number.NEGATIVE_INFINITY
    }

    const parsed = Date.parse(raw)
    return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY
}

function formatApproxNumber(value, digits = 2, suffix = '') {
    const number = Number(value)
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    return `~${number.toFixed(digits)}${suffix}`
}

function formatApproxSignedNumber(value, digits = 2, suffix = '') {
    const number = Number(value)
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    const prefix = number > 0 ? '+' : ''
    return `~${prefix}${number.toFixed(digits)}${suffix}`
}

function formatSignedCheckpoint(value) {
    return String(value || 'n/a')
}

function formatCompletionDateTime(entry) {
    const explicitLabel = String(entry?.completedAtLabel || '').trim()
    if (explicitLabel) {
        return explicitLabel
    }

    const raw = String(entry?.completedAt || '').trim()
    if (!raw) {
        return 'n/a'
    }

    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
        return raw
    }

    const normalized = raw.replace('T', ' ').replace(/Z$/, ' UTC')
    return normalized.length > 19 ? normalized.slice(0, 19) : normalized
}

function extractCandlesPerTrade(entry) {
    if (hasFiniteNumber(entry?.candlesPerTrade)) {
        return Number(entry.candlesPerTrade)
    }
    if (hasFiniteNumber(entry?.candlesEvaluated) && hasFiniteNumber(entry?.trades) && Number(entry.trades) > 0) {
        return Number(entry.candlesEvaluated) / Number(entry.trades)
    }
    return null
}

function extractTimeframeMinutes(timeframe) {
    const normalized = String(timeframe || '').trim().toUpperCase()
    if (!normalized) {
        return null
    }
    if (normalized === 'MN' || normalized === 'MN1') {
        return 43200
    }
    const match = normalized.match(/^([MHDW])(\d+)$/)
    if (!match) {
        return null
    }
    const unit = match[1]
    const size = Number(match[2])
    if (!Number.isFinite(size) || size <= 0) {
        return null
    }
    switch (unit) {
    case 'M':
        return size
    case 'H':
        return size * 60
    case 'D':
        return size * 1440
    case 'W':
        return size * 10080
    default:
        return null
    }
}

function extractTimePerTradeMinutes(entry) {
    const candlesPerTrade = extractCandlesPerTrade(entry)
    const timeframeMinutes = extractTimeframeMinutes(entry?.timeframe)
    if (candlesPerTrade === null || timeframeMinutes === null) {
        return null
    }
    return candlesPerTrade * timeframeMinutes
}

function formatCandlesPerTrade(entry) {
    const value = extractCandlesPerTrade(entry)
    return formatWinnerMetric(value, value !== null && value >= 100 ? 0 : 1)
}

function formatTimePerTrade(entry) {
    const minutes = extractTimePerTradeMinutes(entry)
    if (minutes === null) {
        return 'n/a'
    }
    const hours = minutes / 60
    const days = minutes / 1440
    if (days >= 1) {
        const dayDigits = days >= 10 ? 1 : 2
        return `(${formatApproxNumber(days, dayDigits, ' d')})`
    }
    if (hours >= 1) {
        const hourDigits = hours >= 10 ? 1 : 2
        return `(${formatApproxNumber(hours, hourDigits, ' h')})`
    }
    const minuteDigits = minutes >= 100 ? 0 : 1
    return `(${formatApproxNumber(minutes, minuteDigits, ' min')})`
}

function renderCandlesPerTradeCell(entry) {
    const candlesLabel = formatCandlesPerTrade(entry)
    const timeLabel = formatTimePerTrade(entry)
    return (
        <div className='positiveStrategiesCadenceCell'>
            <strong className='positiveStrategiesWinnerValue'>{candlesLabel}</strong>
            {timeLabel !== 'n/a' ? (
                <span className='positiveStrategiesCadenceSubvalue'>{timeLabel}</span>
            ) : null}
        </div>
    )
}

function extractCatalogMonthlyPercent(entry) {
    const value = extractWinnerMonthlyPercent(entry)
    return value === null ? null : value
}

function extractCatalogMonthlySortValue(entry) {
    const value = extractCatalogMonthlyPercent(entry)
    return value === null ? Number.NEGATIVE_INFINITY : value
}

function formatMonthlyExpectation(entry) {
    const number = extractCatalogMonthlyPercent(entry)
    if (number === null) {
        return 'n/a'
    }
    return formatApproxSignedNumber(number, 2, '%')
}

function formatTradesPerDay(value) {
    const number = Number(value)
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    const perMinute = number / 1440
    const perHour = number / 24
    const perDay = number
    const perWeek = number * 7
    const perMonth = number * 30
    const candidates = [
        { value: perMinute, unit: '/ minute' },
        { value: perHour, unit: '/ hour' },
        { value: perDay, unit: '/ day' },
        { value: perWeek, unit: '/ week' },
        { value: perMonth, unit: '/ month' },
    ]
    const selected = candidates.find((candidate) => candidate.value > 1) || candidates[candidates.length - 1]
    const digits = selected.value < 0.1 ? 3 : selected.value < 10 ? 2 : 1
    return `${formatApproxNumber(selected.value, digits)} ${selected.unit}`
}

function extractTradesTimeSortValue(value) {
    const explicitTradesPerDay = Number(value?.tradesPerDay)
    if (hasFiniteNumber(value?.tradesPerDay) && explicitTradesPerDay > 0) {
        return explicitTradesPerDay
    }

    const timePerTradeMinutes = extractTimePerTradeMinutes(value)
    if (timePerTradeMinutes === null || timePerTradeMinutes <= 0) {
        return Number.NEGATIVE_INFINITY
    }
    return 1440 / timePerTradeMinutes
}

function formatTrades(value) {
    const number = Number(value)
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    return String(number)
}

function extractStatisticalReliabilityTrades(entry) {
    const winnerTrades = extractWinnerTrades(entry)
    if (winnerTrades !== null) {
        return winnerTrades
    }
    if (hasFiniteNumber(entry?.trades)) {
        return Number(entry.trades)
    }
    return null
}

function extractStatisticalReliabilityScore(entry) {
    const trades = extractStatisticalReliabilityTrades(entry)
    if (trades === null || trades <= 0) {
        return null
    }
    const normalized = Math.min(Math.sqrt(trades / STATISTICAL_RELIABILITY_TRADE_TARGET), 1)
    return normalized * 100
}

function formatStatisticalReliabilityScore(entry) {
    const score = extractStatisticalReliabilityScore(entry)
    if (!hasFiniteNumber(score)) {
        return 'n/a'
    }
    return formatApproxNumber(score, 0, '/100')
}

function formatStatisticalReliabilityDetail(entry) {
    const trades = extractStatisticalReliabilityTrades(entry)
    if (trades === null) {
        return 'n/a'
    }
    if (trades >= STATISTICAL_RELIABILITY_TRADE_TARGET) {
        return `${formatTrades(trades)} trades · winner-grade sample`
    }
    if (trades >= 18) {
        return `${formatTrades(trades)} trades · strong sample`
    }
    if (trades >= 8) {
        return `${formatTrades(trades)} trades · developing sample`
    }
    return `${formatTrades(trades)} trades · thin sample`
}

function extractExpectancyPerTrade(entry) {
    if (hasFiniteNumber(entry?.expectancyPerTrade)) {
        return Number(entry.expectancyPerTrade)
    }
    return null
}

function extractWinRate(entry) {
    if (hasFiniteNumber(entry?.winRate)) {
        return Number(entry.winRate)
    }
    return null
}

function extractMaxDrawdown(entry) {
    if (hasFiniteNumber(entry?.maxDrawdown)) {
        return Number(entry.maxDrawdown)
    }
    return null
}

function extractMaxDrawdownPct(entry) {
    if (hasFiniteNumber(entry?.maxDrawdownPct)) {
        return Number(entry.maxDrawdownPct)
    }
    return null
}

function extractReplayDrawdownCoverage(entry) {
    const net = hasFiniteNumber(entry?.netPnl) ? Number(entry.netPnl) : extractWinnerNet(entry)
    const maxDrawdown = extractMaxDrawdown(entry)
    if (net === null || maxDrawdown === null) {
        return null
    }
    const absoluteDrawdown = Math.abs(maxDrawdown)
    if (absoluteDrawdown === 0) {
        return net > 0 ? Number.POSITIVE_INFINITY : 0
    }
    return Math.abs(net) / absoluteDrawdown
}

function buildForwardEvidenceChecks(entry) {
    return [
        {
            key: 'heldOut',
            shortLabel: 'held-out',
            available: entry?.heldOutEvidenceAvailable === true || Boolean(String(entry?.heldOutSummary || '').trim()),
            pass: entry?.heldOutPassed === true,
        },
        {
            key: 'walkForward',
            shortLabel: 'walk-forward',
            available: entry?.walkForwardEvidenceAvailable === true || Boolean(String(entry?.walkForwardSummary || '').trim()),
            pass: entry?.walkForwardPassed === true,
        },
        {
            key: 'cost',
            shortLabel: 'cost',
            available: (
                entry?.costValidationAvailable === true
                || entry?.costConfigured === true
                || Boolean(String(entry?.costValidationSummary || '').trim())
            ),
            pass: entry?.costValidated === true,
        },
    ]
}

function hasForwardRobustnessEvidence(entry) {
    return buildForwardEvidenceChecks(entry).some((check) => check.available)
}

function hasForwardRobustnessProof(entry) {
    return buildForwardEvidenceChecks(entry).some((check) => check.pass)
}

function formatForwardEvidenceSummary(entry) {
    const checks = buildForwardEvidenceChecks(entry)
    const passed = checks.filter((check) => check.pass).map((check) => check.shortLabel)
    const seen = checks.filter((check) => check.available && !check.pass).map((check) => check.shortLabel)
    if (!passed.length && !seen.length) {
        return 'n/a'
    }
    if (passed.length && seen.length) {
        return `${passed.join(' + ')} ok · ${seen.join(' + ')} seen`
    }
    if (passed.length) {
        return `${passed.join(' + ')} ok`
    }
    return `${seen.join(' + ')} seen`
}

function formatForwardEvidenceNarrative(entry) {
    const candidates = [
        entry?.heldOutPassed ? entry?.heldOutSummary : '',
        entry?.walkForwardPassed ? entry?.walkForwardSummary : '',
        entry?.costValidationSummary,
        entry?.heldOutSummary,
        entry?.walkForwardSummary,
    ]
    const snippet = candidates.find((value) => String(value || '').trim())
    return String(snippet || '').trim() || 'No held-out, walk-forward, or explicit cost proof is preserved on this row yet.'
}

function buildRobustnessEvidenceChecks(entry) {
    const trades = extractStatisticalReliabilityTrades(entry)
    const hasReplayStats = (
        extractExpectancyPerTrade(entry) !== null
        || extractWinRate(entry) !== null
        || extractMaxDrawdown(entry) !== null
        || extractMaxDrawdownPct(entry) !== null
    )
    const replayCoverage = extractReplayDrawdownCoverage(entry)
    const hasForwardEvidence = hasForwardRobustnessEvidence(entry)
    const hasForwardProof = hasForwardRobustnessProof(entry)
    return [
        {
            key: 'sample',
            shortLabel: 'sample',
            detailLabel: '30+ trades',
            available: trades !== null,
            pass: trades !== null && trades >= STATISTICAL_RELIABILITY_TRADE_TARGET,
        },
        {
            key: 'replay',
            shortLabel: 'replay',
            detailLabel: 'artifact replay stats',
            available: hasReplayStats,
            pass: hasReplayStats,
        },
        {
            key: 'risk',
            shortLabel: 'risk',
            detailLabel: 'net covers max drawdown',
            available: replayCoverage !== null,
            pass: replayCoverage !== null && replayCoverage >= 1,
        },
        {
            key: 'forward',
            shortLabel: 'forward',
            detailLabel: 'held-out / walk-forward / cost proof',
            available: hasForwardEvidence,
            pass: hasForwardProof,
        },
    ]
}

function extractRobustnessEvidencePassedCount(entry) {
    return buildRobustnessEvidenceChecks(entry).filter((check) => check.pass).length
}

function extractRobustnessEvidenceSortValue(entry) {
    const checks = buildRobustnessEvidenceChecks(entry)
    const passedCount = checks.filter((check) => check.pass).length
    const availableCount = checks.filter((check) => check.available).length
    return (passedCount * 100) + availableCount
}

function formatRobustnessEvidenceScore(entry) {
    const passedCount = extractRobustnessEvidencePassedCount(entry)
    return `${passedCount}/${ROBUSTNESS_EVIDENCE_TOTAL_CHECKS}`
}

function formatRobustnessEvidenceDetail(entry) {
    const checks = buildRobustnessEvidenceChecks(entry)
    const passedLabels = checks.filter((check) => check.pass).map((check) => check.shortLabel)
    if (!passedLabels.length) {
        return 'no robust proof'
    }
    return passedLabels.join(' + ')
}

function formatRobustnessEvidenceBreakdown(entry) {
    return buildRobustnessEvidenceChecks(entry).map((check) => {
        if (check.key === 'forward') {
            const forwardSummary = formatForwardEvidenceSummary(entry)
            if (check.pass) {
                return `${check.detailLabel} ok (${forwardSummary})`
            }
            if (check.available) {
                return `${check.detailLabel} seen (${forwardSummary})`
            }
            return `${check.detailLabel} missing`
        }
        if (check.pass) {
            return `${check.detailLabel} ok`
        }
        if (check.available) {
            return `${check.detailLabel} failed`
        }
        return `${check.detailLabel} missing`
    }).join(' · ')
}

function formatMaxDrawdown(entry) {
    const value = extractMaxDrawdown(entry)
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    return formatApproxSignedNumber(value)
}

function formatMaxDrawdownPct(entry) {
    const value = extractMaxDrawdownPct(entry)
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    return formatApproxSignedNumber(Number(value) * 100, 2, '%')
}

function formatReplayDrawdownCoverage(entry) {
    const value = extractReplayDrawdownCoverage(entry)
    if (value === null) {
        return 'n/a'
    }
    if (!Number.isFinite(value)) {
        return 'net >> drawdown'
    }
    return formatApproxNumber(value, value >= 10 ? 1 : 2, 'x')
}

function formatEntryContext(entry) {
    return `${String(entry?.symbol || '')} · ${String(entry?.timeframe || '')} · ${String(entry?.side || '')}`.trim()
}

function renderTruncatedTextWithTooltip(text) {
    const safeText = String(text || '').trim() || 'n/a'
    const hasTooltip = safeText !== 'n/a'
    return (
        <span className='positiveStrategiesTruncatedInline'>
            <span className='positiveStrategiesTruncatedText'>{safeText}</span>
            {hasTooltip ? (
                <span
                    className='positiveStrategiesTooltipIcon'
                    title={safeText}
                    aria-label={safeText}
                    role='img'
                    onClick={(event) => event.stopPropagation()}
                    onMouseDown={(event) => event.stopPropagation()}
                >
                    ⓘ
                </span>
            ) : null}
        </span>
    )
}

function extractCheckpointMetric(textValue, suffixPattern) {
    const text = String(textValue || '')
    const match = text.match(new RegExp(`([+-]?\\d+(?:\\.\\d+)?)\\s*${suffixPattern}`, 'i'))
    if (!match) {
        return null
    }
    const parsed = Number(match[1])
    return Number.isFinite(parsed) ? parsed : null
}

function extractWinnerNet(entry) {
    return extractCheckpointMetric(entry?.positiveCheckpoint, 'net')
}

function extractWinnerNetPercent(entry) {
    const netAmount = extractWinnerNet(entry)
    if (netAmount === null) {
        return null
    }
    return (netAmount / WINNER_MONTHLY_PERCENT_BASELINE) * 100
}

function extractWinnerMonthlyAmount(entry) {
    return extractCheckpointMetric(entry?.positiveCheckpoint, '(?:per\\s+month|month|monthly)')
}

function extractWinnerMonthlyPercent(entry) {
    const monthlyAmount = extractWinnerMonthlyAmount(entry)
    if (monthlyAmount !== null) {
        return (monthlyAmount / WINNER_MONTHLY_PERCENT_BASELINE) * 100
    }
    if (hasFiniteNumber(entry?.expectedMonthlyPercent)) {
        return Number(entry.expectedMonthlyPercent)
    }
    return null
}

function extractWinnerAnnualAmount(entry) {
    const monthlyAmount = extractWinnerMonthlyAmount(entry)
    if (monthlyAmount !== null) {
        return monthlyAmount * 12
    }
    const monthlyPercent = extractWinnerMonthlyPercent(entry)
    if (monthlyPercent === null) {
        return null
    }
    return (monthlyPercent / 100) * WINNER_MONTHLY_PERCENT_BASELINE * 12
}

function extractWinnerAnnualPercent(entry) {
    const monthlyPercent = extractWinnerMonthlyPercent(entry)
    if (monthlyPercent === null) {
        return null
    }
    return monthlyPercent * 12
}

function extractWinnerMonthlySortValue(entry) {
    const monthlyAmount = extractWinnerMonthlyAmount(entry)
    if (monthlyAmount !== null) {
        return monthlyAmount
    }
    if (hasFiniteNumber(entry?.expectedMonthlyPercent)) {
        return Number(entry.expectedMonthlyPercent)
    }
    return Number.NEGATIVE_INFINITY
}

function formatWinnerMonthlyReference(entry) {
    const monthlyAmount = extractWinnerMonthlyAmount(entry)
    if (monthlyAmount !== null) {
        return `${formatApproxSignedNumber(monthlyAmount)} / month`
    }
    if (hasFiniteNumber(entry?.expectedMonthlyPercent)) {
        return `${formatApproxNumber(entry.expectedMonthlyPercent, 2, '%')} expected`
    }
    return 'n/a'
}

function formatWinnerMonthlyPercent(entry) {
    const monthlyPercent = extractWinnerMonthlyPercent(entry)
    if (!hasFiniteNumber(monthlyPercent)) {
        return 'n/a'
    }
    return formatApproxSignedNumber(monthlyPercent, 2, '%')
}

function formatWinnerNetPercent(entry) {
    const netPercent = extractWinnerNetPercent(entry)
    if (!hasFiniteNumber(netPercent)) {
        return 'n/a'
    }
    return formatApproxSignedNumber(netPercent, 2, '%')
}

function formatWinnerAnnualReference(entry) {
    const annualAmount = extractWinnerAnnualAmount(entry)
    const annualPercent = extractWinnerAnnualPercent(entry)
    if (hasFiniteNumber(annualAmount) && hasFiniteNumber(annualPercent)) {
        return `${formatApproxSignedNumber(annualAmount)} / year · ${formatApproxSignedNumber(annualPercent, 2, '%')}`
    }
    if (hasFiniteNumber(annualAmount)) {
        return `${formatApproxSignedNumber(annualAmount)} / year`
    }
    if (hasFiniteNumber(annualPercent)) {
        return `${formatApproxSignedNumber(annualPercent, 2, '%')} / year`
    }
    return 'n/a'
}

function extractWinnerTrades(entry) {
    if (hasFiniteNumber(entry?.trades)) {
        return Number(entry.trades)
    }
    return extractCheckpointMetric(entry?.positiveCheckpoint, 'trades')
}

function formatWinnerMetric(value, digits = 2, suffix = '') {
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    return formatApproxNumber(Number(value), digits, suffix)
}

function renderWinnerMetricCell(primaryValue, secondaryValue = '') {
    return (
        <div className='positiveStrategiesMetricCell'>
            <strong className='positiveStrategiesWinnerValue'>{primaryValue}</strong>
            {secondaryValue && secondaryValue !== 'n/a' ? (
                <span className='positiveStrategiesMetricSubvalue'>{secondaryValue}</span>
            ) : null}
        </div>
    )
}

function extractPaperNumber(...values) {
    for (const value of values) {
        const match = String(value || '').match(/paper[\s_-]*(\d+)/i)
        if (!match) {
            continue
        }
        const parsed = Number(match[1])
        if (Number.isFinite(parsed)) {
            return parsed
        }
    }
    return null
}

function buildPaperGroupMeta(entry) {
    const paperNumber = extractPaperNumber(entry?.study, entry?.label, entry?.family)
    if (paperNumber === null) {
        return {
            key: `entry:${String(entry?.id || `${entry?.label || 'unknown'}|${entry?.study || ''}|${entry?.symbol || ''}|${entry?.timeframe || ''}`)}`,
            paperNumber: null,
            label: '',
            showHeader: false,
        }
    }
    return {
        key: `paper:${paperNumber}`,
        paperNumber,
        label: `Paper ${paperNumber}`,
        showHeader: true,
    }
}

function extractGroupLeaderScore(entry) {
    const monthlyPercent = extractWinnerMonthlyPercent(entry)
    if (hasFiniteNumber(monthlyPercent)) {
        return Number(monthlyPercent)
    }
    const checkpoint = extractNumericCheckpoint(entry?.positiveCheckpoint)
    if (Number.isFinite(checkpoint) && checkpoint !== Number.NEGATIVE_INFINITY) {
        return checkpoint
    }
    return Number.NEGATIVE_INFINITY
}

function selectPaperGroupLeader(entries) {
    return entries
        .slice()
        .sort((left, right) => {
            const leaderGap = extractGroupLeaderScore(right) - extractGroupLeaderScore(left)
            if (leaderGap !== 0) {
                return leaderGap
            }
            const tradesGap = (hasFiniteNumber(right?.trades) ? Number(right.trades) : Number.NEGATIVE_INFINITY)
                - (hasFiniteNumber(left?.trades) ? Number(left.trades) : Number.NEGATIVE_INFINITY)
            if (tradesGap !== 0) {
                return tradesGap
            }
            return String(left?.label || '').localeCompare(String(right?.label || ''), undefined, { sensitivity: 'base' })
        })[0] || null
}

function compareEntriesByRules(left, right, rules = [], columnMap = {}) {
    for (const rule of rules) {
        const column = columnMap[rule.key]
        if (!column) {
            continue
        }
        const leftValue = column.getValue(left.entry)
        const rightValue = column.getValue(right.entry)
        let comparison = 0
        if (typeof leftValue === 'number' && typeof rightValue === 'number') {
            // Avoid NaN from Infinity sentinels so fallback tie-breakers still work.
            if (Object.is(leftValue, rightValue)) {
                comparison = 0
            } else if (leftValue < rightValue) {
                comparison = -1
            } else if (leftValue > rightValue) {
                comparison = 1
            } else {
                comparison = 0
            }
        } else {
            comparison = String(leftValue).localeCompare(String(rightValue), undefined, { sensitivity: 'base' })
        }
        if (comparison !== 0) {
            return rule.direction === 'asc' ? comparison : -comparison
        }
    }
    return left.index - right.index
}

function groupEntriesByPaper(entries = [], sortRules = [], columnMap = {}) {
    const grouped = []
    const groupedByKey = new Map()

    for (const [index, entry] of entries.entries()) {
        const meta = buildPaperGroupMeta(entry)
        if (!meta.showHeader) {
            grouped.push({
                key: meta.key,
                label: '',
                paperNumber: null,
                showHeader: false,
                entries: [entry],
                leader: entry,
                indexedEntries: [{ entry, index }],
            })
            continue
        }

        const existing = groupedByKey.get(meta.key)
        if (existing) {
            existing.entries.push(entry)
            existing.indexedEntries.push({ entry, index })
            continue
        }

        const nextGroup = {
            key: meta.key,
            label: meta.label,
            paperNumber: meta.paperNumber,
            showHeader: true,
            entries: [entry],
            leader: entry,
            indexedEntries: [{ entry, index }],
        }
        groupedByKey.set(meta.key, nextGroup)
        grouped.push(nextGroup)
    }

    const finalizedGroups = grouped.map((group) => {
        const sortedIndexedEntries = sortRules.length
            ? group.indexedEntries
                .slice()
                .sort((left, right) => compareEntriesByRules(left, right, sortRules, columnMap))
            : group.indexedEntries

        return {
            ...group,
            entries: sortRules.length
                ? sortedIndexedEntries.map(({ entry }) => entry)
                : group.entries,
            showHeader: group.showHeader && group.entries.length > 1,
            leader: sortRules.length
                ? sortedIndexedEntries[0]?.entry || selectPaperGroupLeader(group.entries)
                : selectPaperGroupLeader(group.entries),
        }
    })

    if (!sortRules.length) {
        return finalizedGroups
    }

    return finalizedGroups
        .map((group, index) => ({ group, index }))
        .sort((left, right) => compareEntriesByRules(
            { entry: left.group.leader, index: left.index },
            { entry: right.group.leader, index: right.index },
            sortRules,
            columnMap,
        ))
        .map(({ group }) => group)
}

function isWinnerEntry(entry) {
    if (!entry) {
        return false
    }
    if (entry?.classification === 'promoted') {
        return true
    }
    return String(entry?.operatorVerdict || '').toLowerCase().includes('winner')
}

function getWinnerSaveState(winnerSaveStateById, entryId) {
    if (!entryId || !winnerSaveStateById || typeof winnerSaveStateById !== 'object') {
        return null
    }
    return winnerSaveStateById[entryId] || null
}

function buildStatusCounts(entries = []) {
    return entries.reduce((accumulator, entry) => {
        const key = String(entry?.classification || 'other')
        accumulator[key] = (accumulator[key] || 0) + 1
        return accumulator
    }, {})
}

const WINNER_SORTABLE_COLUMNS = {
    label: {
        label: 'Strategy',
        defaultDirection: 'asc',
        getValue: (entry) => String(entry?.label || ''),
    },
    context: {
        label: 'Context',
        defaultDirection: 'asc',
        getValue: (entry) => `${String(entry?.symbol || '')} ${String(entry?.timeframe || '')} ${String(entry?.side || '')}`.trim(),
    },
    net: {
        label: 'Net',
        defaultDirection: 'desc',
        getValue: (entry) => {
            const value = extractWinnerNet(entry)
            return value === null ? Number.NEGATIVE_INFINITY : value
        },
    },
    monthlyRef: {
        label: 'Monthly ref.',
        defaultDirection: 'desc',
        getValue: (entry) => extractWinnerMonthlySortValue(entry),
    },
    monthlyPercent: {
        label: 'Monthly %',
        defaultDirection: 'desc',
        getValue: (entry) => {
            const value = extractWinnerMonthlyPercent(entry)
            return value === null ? Number.NEGATIVE_INFINITY : value
        },
    },
    trades: {
        label: 'Trades',
        defaultDirection: 'desc',
        getValue: (entry) => {
            const value = extractWinnerTrades(entry)
            return value === null ? Number.NEGATIVE_INFINITY : value
        },
    },
    candlesPerTrade: {
        label: 'Candles/trade',
        defaultDirection: 'asc',
        getValue: (entry) => {
            const value = extractCandlesPerTrade(entry)
            return value === null ? Number.POSITIVE_INFINITY : value
        },
    },
    tradesPerDay: {
        label: 'Trades/time',
        defaultDirection: 'desc',
        getValue: (entry) => extractTradesTimeSortValue(entry),
    },
    statisticalReliability: {
        label: 'Stat. reliability',
        defaultDirection: 'desc',
        getValue: (entry) => {
            const value = extractStatisticalReliabilityScore(entry)
            return value === null ? Number.NEGATIVE_INFINITY : value
        },
    },
    robustnessEvidence: {
        label: 'Robust. evidence',
        defaultDirection: 'desc',
        getValue: (entry) => extractRobustnessEvidenceSortValue(entry),
    },
    completedAt: {
        label: 'Completed',
        defaultDirection: 'desc',
        getValue: (entry) => extractCompletionSortValue(entry),
    },
    verdict: {
        label: 'Verdict',
        defaultDirection: 'asc',
        getValue: (entry) => String(entry?.operatorVerdict || ''),
    },
}

export function ResearchPositiveStrategiesPaneBase({
    catalog = LOCAL_POSITIVE_HISTORY_STATE.catalog,
    lastUpdated = LOCAL_POSITIVE_HISTORY_STATE.lastUpdated,
    onSaveWinner = null,
    winnerSaveStateById = {},
    onRefreshCatalog = null,
    isBootstrappingCatalog = false,
    isRefreshingCatalog = false,
    refreshCatalogMessage = '',
    refreshCatalogStatus = '',
} = {}) {
    const [search, setSearch] = useState('')
    const [brokerFilter, setBrokerFilter] = useState('')
    const [statusFilter, setStatusFilter] = useState('all')
    const [studyFilter, setStudyFilter] = useState('all')
    const [sortRules, setSortRules] = useState([])
    const [winnerSortRules, setWinnerSortRules] = useState([])
    const [selectedId, setSelectedId] = useState('')
    const [autoSelectionTarget, setAutoSelectionTarget] = useState('catalog')
    const [selectionNeedsSync, setSelectionNeedsSync] = useState(true)
    const [collapsedCatalogGroups, setCollapsedCatalogGroups] = useState({})
    const [collapsedWinnerGroups, setCollapsedWinnerGroups] = useState({})

    const catalogWithBrokerContext = useMemo(
        () => catalog.map((entry) => decoratePositiveHistoryBrokerEntry(entry)),
        [catalog],
    )

    const brokerCounts = useMemo(() => {
        const counts = {}
        catalogWithBrokerContext.forEach((entry) => {
            const code = String(entry?.resolvedBrokerCode || 'unknown').trim() || 'unknown'
            counts[code] = (counts[code] || 0) + 1
        })
        return counts
    }, [catalogWithBrokerContext])

    const brokerOptions = useMemo(() => {
        const orderedCodes = Array.from(new Set([
            ...DEFAULT_POSITIVE_HISTORY_BROKER_ORDER,
            ...Object.keys(brokerCounts),
        ]))

        return orderedCodes
            .filter((code) => DEFAULT_POSITIVE_HISTORY_BROKER_ORDER.includes(code) || (brokerCounts[code] || 0) > 0)
            .map((code) => ({
                value: code,
                label: getPositiveHistoryBrokerLabel(code),
                count: brokerCounts[code] || 0,
            }))
    }, [brokerCounts])

    const effectiveBrokerFilter = (
        brokerFilter && brokerOptions.some((option) => option.value === brokerFilter)
            ? brokerFilter
            : (brokerOptions[0]?.value || '')
    )

    const brokerScopedCatalog = useMemo(() => {
        if (!effectiveBrokerFilter) {
            return catalogWithBrokerContext
        }
        return catalogWithBrokerContext.filter((entry) => entry?.resolvedBrokerCode === effectiveBrokerFilter)
    }, [catalogWithBrokerContext, effectiveBrokerFilter])

    const activeBrokerFilterOption = brokerOptions.find((option) => option.value === effectiveBrokerFilter) || null

    const selectedStillExists = selectedId
        ? brokerScopedCatalog.some((entry) => entry?.id === selectedId)
        : false

    const statusOptions = useMemo(
        () => [
            { value: 'all', label: 'All statuses' },
            { value: 'promoted', label: 'Promoted' },
            { value: 'watch', label: 'Watch' },
            { value: 'positive_local', label: 'Local positive' },
            { value: 'checkpoint_positive', label: 'Checkpoint positive' },
            { value: 'experimental', label: 'Experimental' },
        ],
        [],
    )

    const studyOptions = useMemo(() => {
        const values = Array.from(new Set(brokerScopedCatalog.map((entry) => String(entry?.study || 'Unknown study'))))
        return ['all', ...values]
    }, [brokerScopedCatalog])

    const effectiveStudyFilter = studyOptions.includes(studyFilter) ? studyFilter : 'all'

    const filteredEntries = useMemo(() => {
        const needle = search.trim().toLowerCase()
        const matchingEntries = brokerScopedCatalog.filter((entry) => {
            if (statusFilter !== 'all' && entry?.classification !== statusFilter) {
                return false
            }
            if (effectiveStudyFilter !== 'all' && String(entry?.study || '') !== effectiveStudyFilter) {
                return false
            }
            if (!needle) {
                return true
            }
            return [
                entry?.label,
                entry?.study,
                entry?.family,
                entry?.symbol,
                entry?.operatorVerdict,
                entry?.takeaway,
            ].some((value) => String(value || '').toLowerCase().includes(needle))
        })
        if (!sortRules.length) {
            return matchingEntries
        }
        return matchingEntries
            .map((entry, index) => ({ entry, index }))
            .sort((left, right) => compareEntriesByRules(left, right, sortRules, SORTABLE_COLUMNS))
            .map(({ entry }) => entry)
    }, [brokerScopedCatalog, effectiveStudyFilter, search, sortRules, statusFilter])

    const winnerEntries = useMemo(
        () => brokerScopedCatalog
            .filter((entry) => isWinnerEntry(entry))
            .slice()
            .sort((left, right) => {
                const monthlyGap = extractWinnerMonthlySortValue(right) - extractWinnerMonthlySortValue(left)
                if (monthlyGap !== 0) {
                    return monthlyGap
                }
                const completedGap = extractCompletionSortValue(right) - extractCompletionSortValue(left)
                if (completedGap !== 0) {
                    return completedGap
                }
                return String(left?.label || '').localeCompare(String(right?.label || ''), undefined, { sensitivity: 'base' })
            }),
        [brokerScopedCatalog],
    )

    const sortedWinnerEntries = useMemo(() => {
        if (!winnerSortRules.length) {
            return winnerEntries
        }

        return winnerEntries
            .map((entry, index) => ({ entry, index }))
            .sort((left, right) => compareEntriesByRules(left, right, winnerSortRules, WINNER_SORTABLE_COLUMNS))
            .map(({ entry }) => entry)
    }, [winnerEntries, winnerSortRules])

    function handleSortToggle(columnKey, columnMap, setRuleState) {
        const column = columnMap[columnKey]
        if (!column) {
            return
        }
        setRuleState((current) => {
            const defaultDirection = column.defaultDirection || 'desc'
            const alternateDirection = defaultDirection === 'asc' ? 'desc' : 'asc'
            const existingIndex = current.findIndex((rule) => rule.key === columnKey)
            if (existingIndex < 0) {
                return [{ key: columnKey, direction: defaultDirection }]
            }

            if (current[existingIndex].direction === defaultDirection) {
                return [{ key: columnKey, direction: alternateDirection }]
            }

            return []
        })
    }

    function getSortIndicator(ruleState, columnKey) {
        const index = ruleState.findIndex((rule) => rule.key === columnKey)
        if (index < 0) {
            return ''
        }
        const rule = ruleState[index]
        return rule.direction === 'asc' ? '↑' : '↓'
    }

    function getAriaSort(ruleState, columnKey) {
        const direction = ruleState.find((rule) => rule.key === columnKey)?.direction || null
        if (direction === 'asc') {
            return 'ascending'
        }
        if (direction === 'desc') {
            return 'descending'
        }
        return 'none'
    }

    const summary = useMemo(() => {
        const counts = buildStatusCounts(brokerScopedCatalog)
        const bestMonthly = brokerScopedCatalog
            .filter((entry) => extractCatalogMonthlyPercent(entry) !== null)
            .sort((left, right) => extractCatalogMonthlySortValue(right) - extractCatalogMonthlySortValue(left))[0] || null
        const fastest = brokerScopedCatalog
            .filter((entry) => hasFiniteNumber(entry?.hoursPerTrade))
            .sort((left, right) => Number(left.hoursPerTrade) - Number(right.hoursPerTrade))[0] || null
        return {
            total: brokerScopedCatalog.length,
            promoted: counts.promoted || 0,
            watch: counts.watch || 0,
            experimental: counts.experimental || 0,
            winners: winnerEntries.length,
            bestMonthly,
            fastest,
            bestWinner: winnerEntries[0] || null,
        }
    }, [brokerScopedCatalog, winnerEntries])

    const groupedWinnerEntries = useMemo(
        () => groupEntriesByPaper(sortedWinnerEntries, winnerSortRules, WINNER_SORTABLE_COLUMNS),
        [sortedWinnerEntries, winnerSortRules],
    )

    const groupedCatalogEntries = useMemo(
        () => groupEntriesByPaper(filteredEntries, sortRules, SORTABLE_COLUMNS),
        [filteredEntries, sortRules],
    )

    const firstVisibleCatalogEntryId = groupedCatalogEntries[0]?.leader?.id || groupedCatalogEntries[0]?.entries?.[0]?.id || ''

    const firstVisibleWinnerEntryId = groupedWinnerEntries[0]?.leader?.id || groupedWinnerEntries[0]?.entries?.[0]?.id || ''

    const preferredVisibleEntryId = autoSelectionTarget === 'winner'
        ? (firstVisibleWinnerEntryId || firstVisibleCatalogEntryId)
        : (firstVisibleCatalogEntryId || firstVisibleWinnerEntryId)

    const effectiveSelectedId = !brokerScopedCatalog.length
        ? ''
        : (!selectedStillExists || selectionNeedsSync || !selectedId)
            ? preferredVisibleEntryId
            : selectedId

    const selectedEntry = brokerScopedCatalog.find((entry) => entry?.id === effectiveSelectedId)
        || filteredEntries[0]
        || null
    const selectedWinnerSaveState = getWinnerSaveState(winnerSaveStateById, selectedEntry?.id)

    const winnerColumnWidths = onSaveWinner ? WINNER_PROMOTABLE_COLUMN_WIDTHS : WINNER_COLUMN_WIDTHS
    const winnerColumnCount = winnerColumnWidths.length
    const catalogColumnCount = CATALOG_COLUMN_WIDTHS.length
    const winnerGridTemplate = winnerColumnWidths.join(' ')
    const catalogGridTemplate = CATALOG_COLUMN_WIDTHS.join(' ')

    function isGroupCollapsed(groupState, groupKey) {
        return groupState[groupKey] !== false
    }

    function toggleCatalogGroup(groupKey) {
        setCollapsedCatalogGroups((current) => ({
            ...current,
            [groupKey]: current[groupKey] === false ? true : false,
        }))
    }

    function toggleWinnerGroup(groupKey) {
        setCollapsedWinnerGroups((current) => ({
            ...current,
            [groupKey]: current[groupKey] === false ? true : false,
        }))
    }

    function renderGroupHeaderCell(label, value, extraClassName = '') {
        if (value === null || value === undefined || value === '' || value === 'n/a') {
            return null
        }
        return (
            <div className={`positiveStrategiesGroupCell ${extraClassName}`.trim()} key={label}>
                <span className='positiveStrategiesGroupCellLabel'>{label}</span>
                <div className='positiveStrategiesGroupCellValue'>{value}</div>
            </div>
        )
    }

    function renderGroupHeader(group, variant = 'catalog', isCollapsed = true) {
        const leader = group?.leader
        if (!leader) {
            return null
        }

        const firstCell = (
            <div className='positiveStrategiesGroupCell positiveStrategiesGroupCellStrategy' key='strategy'>
                <div className='positiveStrategiesGroupStrategyMeta'>
                    <span className='positiveStrategiesGroupChevron'>{isCollapsed ? '▸' : '▾'}</span>
                    <div className='positiveStrategiesGroupStrategyText'>
                        <div className='positiveStrategiesGroupHeaderMain'>
                            <span className='positiveStrategiesGroupTitle'>{group.label}</span>
                            <span className='positiveStrategiesGroupCount'>{group.entries.length} strategies</span>
                        </div>
                        <div className='positiveStrategiesGroupStrategyLead'>
                            {renderTruncatedTextWithTooltip(leader.label)}
                        </div>
                    </div>
                </div>
            </div>
        )

        if (variant === 'winner') {
            return (
                <div className='positiveStrategiesGroupGrid' style={{ gridTemplateColumns: winnerGridTemplate }}>
                    {firstCell}
                    {renderGroupHeaderCell('Context', formatEntryContext(leader))}
                    {renderGroupHeaderCell('Net', renderWinnerMetricCell(
                        formatWinnerMetric(extractWinnerNet(leader)),
                        formatWinnerNetPercent(leader),
                    ))}
                    {renderGroupHeaderCell('Monthly ref.', renderWinnerMetricCell(
                        formatWinnerMonthlyReference(leader),
                        formatWinnerAnnualReference(leader),
                    ))}
                    {renderGroupHeaderCell('Monthly %', <strong className='positiveStrategiesWinnerValue'>{formatWinnerMonthlyPercent(leader)}</strong>)}
                    {renderGroupHeaderCell('Trades', <strong className='positiveStrategiesWinnerValue'>{formatTrades(extractWinnerTrades(leader))}</strong>)}
                    {renderGroupHeaderCell('Candles/trade', renderCandlesPerTradeCell(leader))}
                    {renderGroupHeaderCell('Trades/time', <strong className='positiveStrategiesWinnerValue'>{formatTradesPerDay(leader.tradesPerDay)}</strong>)}
                    {renderGroupHeaderCell('Stat. reliability', renderWinnerMetricCell(
                        formatStatisticalReliabilityScore(leader),
                        formatStatisticalReliabilityDetail(leader),
                    ))}
                    {renderGroupHeaderCell('Completed', formatCompletionDateTime(leader))}
                    {renderGroupHeaderCell('Verdict', <span className={`positiveStrategiesBadge ${leader.classification}`}>{leader.operatorVerdict}</span>)}
                    {onSaveWinner ? renderGroupHeaderCell('Promote', 'Expand to choose') : null}
                </div>
            )
        }

        return (
            <div className='positiveStrategiesGroupGrid' style={{ gridTemplateColumns: catalogGridTemplate }}>
                {firstCell}
                {renderGroupHeaderCell('Completed', formatCompletionDateTime(leader))}
                {renderGroupHeaderCell('Context', formatEntryContext(leader))}
                {renderGroupHeaderCell('Positive checkpoint', formatSignedCheckpoint(leader.positiveCheckpoint))}
                {renderGroupHeaderCell('Trades', formatTrades(leader.trades))}
                {renderGroupHeaderCell('Candles/trade', renderCandlesPerTradeCell(leader))}
                {renderGroupHeaderCell('Trades/time', formatTradesPerDay(leader.tradesPerDay))}
                {renderGroupHeaderCell('Stat. reliability', renderWinnerMetricCell(
                    formatStatisticalReliabilityScore(leader),
                    formatStatisticalReliabilityDetail(leader),
                ))}
                {renderGroupHeaderCell('1M expectation', formatMonthlyExpectation(leader))}
            </div>
        )
    }

    return (
        <div className='positiveStrategiesPanel'>
            <div className='positiveStrategiesHero'>
                <div className='positiveStrategiesHeroHeader'>
                    <div>
                        <div className='positiveStrategiesTitle'>Positive strategy history</div>
                        <div className='positiveStrategiesSubtitle'>
                            Curated cross-study registry of every strategy family that printed a documented positive checkpoint,
                            including promoted winners, watch-level leads, local positives, and archived experimental pockets.
                        </div>
                    </div>
                    <div className='positiveStrategiesHeroTools'>
                        <div className='positiveStrategiesMeta'>
                            <span>Catalog updated: {lastUpdated || 'Loading live catalog...'}</span>
                            <span>Showing broker: {activeBrokerFilterOption?.label || 'n/a'} · {summary.total} catalog rows.</span>
                            <span>Always update this register when a new future positive entry appears.</span>
                            <span>Click a table header to sort by its default direction. Click again to invert. Third click clears.</span>
                            {isBootstrappingCatalog ? (
                                <span>Loading the latest live Positive history from the backend before trusting this table.</span>
                            ) : null}
                        </div>
                        {onRefreshCatalog ? (
                            <div className='positiveStrategiesRefreshGroup'>
                                <button
                                    type='button'
                                    className='positiveStrategiesActionButton'
                                    onClick={() => {
                                        setAutoSelectionTarget('catalog')
                                        setSelectionNeedsSync(true)
                                        void onRefreshCatalog()
                                    }}
                                    disabled={isRefreshingCatalog}
                                >
                                    {isRefreshingCatalog
                                        ? (isBootstrappingCatalog ? 'Loading latest...' : 'Refreshing...')
                                        : 'Refresh'}
                                </button>
                                {refreshCatalogMessage ? (
                                    <div className={`positiveStrategiesInlineStatus ${refreshCatalogStatus || 'info'}`}>
                                        {refreshCatalogMessage}
                                    </div>
                                ) : null}
                            </div>
                        ) : null}
                    </div>
                </div>
                <div className='positiveStrategiesSummaryGrid'>
                    <div className='positiveStrategiesSummaryCard'>
                        <span>Total entries</span>
                        <strong>{summary.total}</strong>
                    </div>
                    <div className='positiveStrategiesSummaryCard positive'>
                        <span>Promoted</span>
                        <strong>{summary.promoted}</strong>
                    </div>
                    <div className='positiveStrategiesSummaryCard positive'>
                        <span>Winner rows</span>
                        <strong>{summary.winners}</strong>
                    </div>
                    <div className='positiveStrategiesSummaryCard warning'>
                        <span>Watch-level</span>
                        <strong>{summary.watch}</strong>
                    </div>
                    <div className='positiveStrategiesSummaryCard'>
                        <span>Experimental snapshots</span>
                        <strong>{summary.experimental}</strong>
                    </div>
                    <div className='positiveStrategiesSummaryCard wide'>
                        <span>Best monthly ref.</span>
                        <strong>{summary.bestMonthly ? `${summary.bestMonthly.label} · ${formatMonthlyExpectation(summary.bestMonthly)}` : 'n/a'}</strong>
                    </div>
                    <div className='positiveStrategiesSummaryCard wide'>
                        <span>Best winner monthly</span>
                        <strong>{summary.bestWinner ? `${summary.bestWinner.label} · ${formatWinnerMonthlyReference(summary.bestWinner)}` : 'n/a'}</strong>
                    </div>
                    <div className='positiveStrategiesSummaryCard wide'>
                        <span>Fastest cadence ref.</span>
                        <strong>{summary.fastest ? `${summary.fastest.label} · ${formatApproxNumber(summary.fastest.hoursPerTrade, summary.fastest.hoursPerTrade >= 100 ? 0 : 1)} h/trade` : 'n/a'}</strong>
                    </div>
                </div>
            </div>

            <div className='positiveStrategiesFilters'>
                <label className='positiveStrategiesFilterField'>
                    <span>Broker</span>
                    <select value={effectiveBrokerFilter} onChange={(event) => {
                        setAutoSelectionTarget('catalog')
                        setSelectionNeedsSync(true)
                        setBrokerFilter(event.target.value)
                    }}>
                        {brokerOptions.map((option) => (
                            <option key={option.value} value={option.value}>
                                {`${option.label} (${option.count})`}
                            </option>
                        ))}
                    </select>
                </label>
                <label className='positiveStrategiesFilterField'>
                    <span>Search</span>
                    <input
                        type='text'
                        value={search}
                        onChange={(event) => {
                            setAutoSelectionTarget('catalog')
                            setSelectionNeedsSync(true)
                            setSearch(event.target.value)
                        }}
                        placeholder='benchmark-12, USDSEK, NMCE, Paper 32...'
                    />
                </label>
                <label className='positiveStrategiesFilterField'>
                    <span>Status</span>
                    <select value={statusFilter} onChange={(event) => {
                        setAutoSelectionTarget('catalog')
                        setSelectionNeedsSync(true)
                        setStatusFilter(event.target.value)
                    }}>
                        {statusOptions.map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                    </select>
                </label>
                <label className='positiveStrategiesFilterField'>
                    <span>Study</span>
                    <select value={effectiveStudyFilter} onChange={(event) => {
                        setAutoSelectionTarget('catalog')
                        setSelectionNeedsSync(true)
                        setStudyFilter(event.target.value)
                    }}>
                        {studyOptions.map((option) => (
                            <option key={option} value={option}>{option === 'all' ? 'All studies' : option}</option>
                        ))}
                    </select>
                </label>
            </div>

            <div className='positiveStrategiesWinnersCard'>
                <div className='positiveStrategiesHeroHeader'>
                    <div>
                        <div className='positiveStrategiesTitle'>Winning strategies found</div>
                        <div className='positiveStrategiesSubtitle'>
                            Narrow registry of the rows that already crossed the full winner gate in broad replay,
                            plus the historical promoted winner that still anchors the catalog.
                        </div>
                    </div>
                    <div className='positiveStrategiesMeta'>
                        <span>{winnerEntries.length} winner rows catalogued for {activeBrokerFilterOption?.label || 'the selected broker'}</span>
                        <span>Click a row to inspect the same detail card used by the full positive-history table.</span>
                    </div>
                </div>
                <div className='positiveStrategiesTableWrap'>
                    <table className='positiveStrategiesTable positiveStrategiesWinnerTable'>
                        <colgroup>
                            {winnerColumnWidths.map((width, index) => (
                                <col key={`winner-col-${index}`} style={{ width }} />
                            ))}
                        </colgroup>
                        <thead>
                            <tr>
                                <th aria-sort={getAriaSort(winnerSortRules, 'label')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('label', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Strategy</span>
                                        <span>{getSortIndicator(winnerSortRules, 'label')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'context')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('context', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Context</span>
                                        <span>{getSortIndicator(winnerSortRules, 'context')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'net')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('net', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Net</span>
                                        <span>{getSortIndicator(winnerSortRules, 'net')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'monthlyRef')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('monthlyRef', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Monthly ref.</span>
                                        <span>{getSortIndicator(winnerSortRules, 'monthlyRef')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'monthlyPercent')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('monthlyPercent', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Monthly %</span>
                                        <span>{getSortIndicator(winnerSortRules, 'monthlyPercent')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'trades')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('trades', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Trades</span>
                                        <span>{getSortIndicator(winnerSortRules, 'trades')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'candlesPerTrade')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('candlesPerTrade', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Candles/trade</span>
                                        <span>{getSortIndicator(winnerSortRules, 'candlesPerTrade')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'tradesPerDay')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('tradesPerDay', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Trades/time</span>
                                        <span>{getSortIndicator(winnerSortRules, 'tradesPerDay')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'statisticalReliability')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('statisticalReliability', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Stat. reliability</span>
                                        <span>{getSortIndicator(winnerSortRules, 'statisticalReliability')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'robustnessEvidence')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('robustnessEvidence', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Robust. evidence</span>
                                        <span>{getSortIndicator(winnerSortRules, 'robustnessEvidence')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'completedAt')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('completedAt', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Completed</span>
                                        <span>{getSortIndicator(winnerSortRules, 'completedAt')}</span>
                                    </button>
                                </th>
                                <th aria-sort={getAriaSort(winnerSortRules, 'verdict')}>
                                    <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('winner'); setSelectionNeedsSync(true); handleSortToggle('verdict', WINNER_SORTABLE_COLUMNS, setWinnerSortRules) }}>
                                        <span>Verdict</span>
                                        <span>{getSortIndicator(winnerSortRules, 'verdict')}</span>
                                    </button>
                                </th>
                                {onSaveWinner ? (
                                    <th>Promote</th>
                                ) : null}
                            </tr>
                        </thead>
                        <tbody>
                            {groupedWinnerEntries.length ? groupedWinnerEntries.map((group) => {
                                const isCollapsed = group.showHeader ? isGroupCollapsed(collapsedWinnerGroups, group.key) : false
                                return (
                                    <Fragment key={group.key}>
                                        {group.showHeader ? (
                                            <tr className='positiveStrategiesGroupRow'>
                                                <td colSpan={winnerColumnCount}>
                                                    <button
                                                        type='button'
                                                        className='positiveStrategiesGroupButton'
                                                        onClick={() => toggleWinnerGroup(group.key)}
                                                    >
                                                        {renderGroupHeader(group, 'winner', isCollapsed)}
                                                    </button>
                                                </td>
                                            </tr>
                                        ) : null}
                                        {(!group.showHeader || !isCollapsed) ? group.entries.map((entry) => {
                                            const isActive = entry?.id === selectedEntry?.id
                                            const winnerSaveState = getWinnerSaveState(winnerSaveStateById, entry?.id)
                                            return (
                                                <tr
                                                    key={entry.id}
                                                    className={isActive ? 'active' : ''}
                                                    onClick={() => {
                                                        setAutoSelectionTarget('winner')
                                                        setSelectionNeedsSync(false)
                                                        setSelectedId(entry.id)
                                                    }}
                                                >
                                                    <td>
                                                        <div className='positiveStrategiesStrategyCell'>
                                                            <strong className='positiveStrategiesStrategyTitle'>
                                                                {renderTruncatedTextWithTooltip(entry.label)}
                                                            </strong>
                                                            <span className='positiveStrategiesStrategyFamily' title={String(entry.family || '')}>{entry.family}</span>
                                                        </div>
                                                    </td>
                                                    <td>{`${entry.symbol} · ${entry.timeframe} · ${entry.side}`}</td>
                                                    <td>{renderWinnerMetricCell(
                                                        formatWinnerMetric(extractWinnerNet(entry)),
                                                        formatWinnerNetPercent(entry),
                                                    )}</td>
                                                    <td>{renderWinnerMetricCell(
                                                        formatWinnerMonthlyReference(entry),
                                                        formatWinnerAnnualReference(entry),
                                                    )}</td>
                                                    <td><strong className='positiveStrategiesWinnerValue'>{formatWinnerMonthlyPercent(entry)}</strong></td>
                                                    <td><strong className='positiveStrategiesWinnerValue'>{formatTrades(extractWinnerTrades(entry))}</strong></td>
                                                    <td>{renderCandlesPerTradeCell(entry)}</td>
                                                    <td><strong className='positiveStrategiesWinnerValue'>{formatTradesPerDay(entry.tradesPerDay)}</strong></td>
                                                    <td>{renderWinnerMetricCell(
                                                        formatStatisticalReliabilityScore(entry),
                                                        formatStatisticalReliabilityDetail(entry),
                                                    )}</td>
                                                    <td>{renderWinnerMetricCell(
                                                        formatRobustnessEvidenceScore(entry),
                                                        formatRobustnessEvidenceDetail(entry),
                                                    )}</td>
                                                    <td>{formatCompletionDateTime(entry)}</td>
                                                    <td>
                                                        <span className={`positiveStrategiesBadge ${entry.classification}`}>{entry.operatorVerdict}</span>
                                                    </td>
                                                    {onSaveWinner ? (
                                                        <td>
                                                            <div className='positiveStrategiesActionStack'>
                                                                <button
                                                                    type='button'
                                                                    className='positiveStrategiesActionButton'
                                                                    disabled={winnerSaveState?.status === 'saving' || winnerSaveState?.status === 'saved'}
                                                                    onClick={(event) => {
                                                                        event.stopPropagation()
                                                                        setSelectedId(entry.id)
                                                                        void onSaveWinner(entry)
                                                                    }}
                                                                >
                                                                    {winnerSaveState?.status === 'saving'
                                                                        ? 'Saving...'
                                                                        : winnerSaveState?.status === 'saved'
                                                                            ? 'Saved'
                                                                            : 'Promote'}
                                                                </button>
                                                                {winnerSaveState?.message ? (
                                                                    <div className={`positiveStrategiesInlineStatus ${winnerSaveState.status === 'error' ? 'error' : 'success'}`}>
                                                                        {winnerSaveState.message}
                                                                    </div>
                                                                ) : null}
                                                            </div>
                                                        </td>
                                                    ) : null}
                                                </tr>
                                            )
                                        }) : null}
                                    </Fragment>
                                )
                            }) : (
                                <tr>
                                    <td colSpan={winnerColumnCount} className='positiveStrategiesEmptyState'>
                                        No winner rows are catalogued for {activeBrokerFilterOption?.label || 'the selected broker'}.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className='positiveStrategiesTableWrap'>
                <table className='positiveStrategiesTable'>
                    <colgroup>
                        {CATALOG_COLUMN_WIDTHS.map((width, index) => (
                            <col key={`catalog-col-${index}`} style={{ width }} />
                        ))}
                    </colgroup>
                    <thead>
                        <tr>
                            <th aria-sort={getAriaSort(sortRules, 'label')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('label', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Strategy</span>
                                    <span>{getSortIndicator(sortRules, 'label')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'completedAt')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('completedAt', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Completed</span>
                                    <span>{getSortIndicator(sortRules, 'completedAt')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'context')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('context', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Context</span>
                                    <span>{getSortIndicator(sortRules, 'context')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'checkpoint')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('checkpoint', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Positive checkpoint</span>
                                    <span>{getSortIndicator(sortRules, 'checkpoint')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'trades')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('trades', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Trades</span>
                                    <span>{getSortIndicator(sortRules, 'trades')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'candlesPerTrade')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('candlesPerTrade', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Candles/trade</span>
                                    <span>{getSortIndicator(sortRules, 'candlesPerTrade')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'tradesPerDay')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('tradesPerDay', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Trades/time</span>
                                    <span>{getSortIndicator(sortRules, 'tradesPerDay')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'statisticalReliability')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('statisticalReliability', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Stat. reliability</span>
                                    <span>{getSortIndicator(sortRules, 'statisticalReliability')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'robustnessEvidence')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('robustnessEvidence', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>Robust. evidence</span>
                                    <span>{getSortIndicator(sortRules, 'robustnessEvidence')}</span>
                                </button>
                            </th>
                            <th aria-sort={getAriaSort(sortRules, 'monthly')}>
                                <button type='button' className='positiveStrategiesSortButton' onClick={() => { setAutoSelectionTarget('catalog'); setSelectionNeedsSync(true); handleSortToggle('monthly', SORTABLE_COLUMNS, setSortRules) }}>
                                    <span>1M expectation</span>
                                    <span>{getSortIndicator(sortRules, 'monthly')}</span>
                                </button>
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {groupedCatalogEntries.length ? groupedCatalogEntries.map((group) => {
                            const isCollapsed = group.showHeader ? isGroupCollapsed(collapsedCatalogGroups, group.key) : false
                            return (
                                <Fragment key={group.key}>
                                    {group.showHeader ? (
                                        <tr className='positiveStrategiesGroupRow'>
                                            <td colSpan={catalogColumnCount}>
                                                <button
                                                    type='button'
                                                    className='positiveStrategiesGroupButton'
                                                    onClick={() => toggleCatalogGroup(group.key)}
                                                >
                                                    {renderGroupHeader(group, 'catalog', isCollapsed)}
                                                </button>
                                            </td>
                                        </tr>
                                    ) : null}
                                    {(!group.showHeader || !isCollapsed) ? group.entries.map((entry) => {
                                        const isActive = entry?.id === selectedEntry?.id
                                        return (
                                            <tr
                                                key={entry.id}
                                                className={isActive ? 'active' : ''}
                                                onClick={() => {
                                                    setAutoSelectionTarget('catalog')
                                                    setSelectionNeedsSync(false)
                                                    setSelectedId(entry.id)
                                                }}
                                            >
                                                <td>
                                                    <div className='positiveStrategiesStrategyCell'>
                                                        <strong className='positiveStrategiesStrategyTitle'>
                                                            {renderTruncatedTextWithTooltip(entry.label)}
                                                        </strong>
                                                        <span className='positiveStrategiesStrategyFamily' title={String(entry.family || '')}>{entry.family}</span>
                                                        <span className={`positiveStrategiesBadge ${entry.classification}`}>{entry.operatorVerdict}</span>
                                                    </div>
                                                </td>
                                                <td>{formatCompletionDateTime(entry)}</td>
                                                <td>{`${entry.symbol} · ${entry.timeframe} · ${entry.side}`}</td>
                                                <td>{formatSignedCheckpoint(entry.positiveCheckpoint)}</td>
                                                <td>{formatTrades(entry.trades)}</td>
                                                <td>{renderCandlesPerTradeCell(entry)}</td>
                                                <td>{formatTradesPerDay(entry.tradesPerDay)}</td>
                                                <td>{renderWinnerMetricCell(
                                                    formatStatisticalReliabilityScore(entry),
                                                    formatStatisticalReliabilityDetail(entry),
                                                )}</td>
                                                <td>{renderWinnerMetricCell(
                                                    formatRobustnessEvidenceScore(entry),
                                                    formatRobustnessEvidenceDetail(entry),
                                                )}</td>
                                                <td>{formatMonthlyExpectation(entry)}</td>
                                            </tr>
                                        )
                                    }) : null}
                                </Fragment>
                            )
                        }) : (
                            <tr>
                                <td colSpan={catalogColumnCount} className='positiveStrategiesEmptyState'>
                                    No positive-history rows are catalogued for {activeBrokerFilterOption?.label || 'the selected broker'}.
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            {selectedEntry ? (
                <div className='positiveStrategiesDetailCard'>
                    <div className='positiveStrategiesDetailHeader'>
                        <div>
                            <div className='positiveStrategiesDetailTitle'>{selectedEntry.label}</div>
                            <div className='positiveStrategiesDetailMeta'>
                                <span>{selectedEntry.study}</span>
                                <span>{selectedEntry.symbol}</span>
                                <span>{selectedEntry.timeframe}</span>
                                <span>{selectedEntry.side}</span>
                            </div>
                        </div>
                        <div className='positiveStrategiesDetailHeaderActions'>
                            <span className={`positiveStrategiesBadge ${selectedEntry.classification}`}>{selectedEntry.operatorVerdict}</span>
                            {onSaveWinner && isWinnerEntry(selectedEntry) ? (
                                <div className='positiveStrategiesActionStack'>
                                    <button
                                        type='button'
                                        className='positiveStrategiesActionButton'
                                        disabled={selectedWinnerSaveState?.status === 'saving' || selectedWinnerSaveState?.status === 'saved'}
                                        onClick={() => {
                                            setAutoSelectionTarget('winner')
                                            setSelectionNeedsSync(false)
                                            setSelectedId(selectedEntry.id)
                                            void onSaveWinner(selectedEntry)
                                        }}
                                    >
                                        {selectedWinnerSaveState?.status === 'saving'
                                            ? 'Saving...'
                                            : selectedWinnerSaveState?.status === 'saved'
                                                ? 'Saved'
                                                : 'Promote to strategy'}
                                    </button>
                                </div>
                            ) : null}
                        </div>
                    </div>

                    {selectedWinnerSaveState?.message ? (
                        <div className={`positiveStrategiesSaveStatus ${selectedWinnerSaveState.status === 'error' ? 'error' : 'success'}`}>
                            {selectedWinnerSaveState.message}
                        </div>
                    ) : null}

                    <div className='positiveStrategiesDetailGrid'>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Positive checkpoint</span>
                            <strong>{selectedEntry.positiveCheckpoint}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Study completed</span>
                            <strong>{formatCompletionDateTime(selectedEntry)}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Checkpoint context</span>
                            <strong>{selectedEntry.checkpointContext}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Candles evaluated</span>
                            <strong>{hasFiniteNumber(selectedEntry.candlesEvaluated) ? formatApproxNumber(selectedEntry.candlesEvaluated, 0) : 'n/a'}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Trades</span>
                            <strong>{formatTrades(selectedEntry.trades)}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Candles until 1 trade</span>
                            <strong>{hasFiniteNumber(selectedEntry.candlesPerTrade) ? formatApproxNumber(selectedEntry.candlesPerTrade, Number(selectedEntry.candlesPerTrade) >= 100 ? 0 : 1) : 'n/a'}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Hours until 1 trade</span>
                            <strong>{hasFiniteNumber(selectedEntry.hoursPerTrade) ? formatApproxNumber(selectedEntry.hoursPerTrade, Number(selectedEntry.hoursPerTrade) >= 100 ? 0 : 1) : 'n/a'}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Days until 1 trade</span>
                            <strong>{hasFiniteNumber(selectedEntry.daysPerTrade) ? formatApproxNumber(selectedEntry.daysPerTrade, Number(selectedEntry.daysPerTrade) >= 10 ? 1 : 2) : 'n/a'}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Trades/time</span>
                            <strong>{formatTradesPerDay(selectedEntry.tradesPerDay)}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Statistical reliability</span>
                            <strong>{formatStatisticalReliabilityScore(selectedEntry)}</strong>
                            <small>{formatStatisticalReliabilityDetail(selectedEntry)} · anchored to the 30-trade broad-replay gate.</small>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Robustness evidence</span>
                            <strong>{formatRobustnessEvidenceScore(selectedEntry)}</strong>
                            <small>{formatRobustnessEvidenceBreakdown(selectedEntry)}.</small>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Forward evidence</span>
                            <strong>{formatForwardEvidenceSummary(selectedEntry)}</strong>
                            <small>{formatForwardEvidenceNarrative(selectedEntry)}</small>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Expected 1M gain</span>
                            <strong>{formatMonthlyExpectation(selectedEntry)}</strong>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Replay max drawdown</span>
                            <strong>{formatMaxDrawdown(selectedEntry)}</strong>
                            <small>{formatMaxDrawdownPct(selectedEntry)}</small>
                        </div>
                        <div className='positiveStrategiesDetailItem'>
                            <span>Net / max drawdown</span>
                            <strong>{formatReplayDrawdownCoverage(selectedEntry)}</strong>
                            <small>Replay edge cover only, not forward proof.</small>
                        </div>
                    </div>

                    <div className='positiveStrategiesNarrative'>
                        <div className='positiveStrategiesNarrativeBlock'>
                            <span>Takeaway</span>
                            <strong>{selectedEntry.takeaway}</strong>
                        </div>
                        {selectedEntry.cadenceNote ? (
                            <div className='positiveStrategiesNarrativeBlock'>
                                <span>Cadence note</span>
                                <strong>{selectedEntry.cadenceNote}</strong>
                            </div>
                        ) : null}
                        <div className='positiveStrategiesNarrativeBlock'>
                            <span>Evidence refs</span>
                            <div className='positiveStrategiesEvidenceList'>
                                {(selectedEntry.evidenceRefs || []).map((entry) => (
                                    <code key={entry}>{entry}</code>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>
            ) : (
                <div className='statisticsEmpty'>No positive strategy matches the current filters.</div>
            )}
        </div>
    )
}

export function ResearchPositiveStrategiesPane({
    authToken = '',
    isGuest = false,
} = {}) {
    const [winnerSaveStateById, setWinnerSaveStateById] = useState({})
    const [catalogState, setCatalogState] = useState(() => createEmptyPositiveHistoryCatalogState())
    const [hasLiveCatalogHydrated, setHasLiveCatalogHydrated] = useState(false)
    const [catalogRefreshState, setCatalogRefreshState] = useState({
        status: 'info',
        message: 'Loading the latest live Positive history catalog...',
        loading: true,
    })
    const catalogRefreshRequestIdRef = useRef(0)

    async function handleSaveWinner(entry) {
        if (!entry?.id) {
            return
        }

        if (!authToken || isGuest) {
            setWinnerSaveStateById((current) => ({
                ...current,
                [entry.id]: {
                    status: 'error',
                    message: 'Sign in with an authenticated workspace user to promote this strategy.',
                },
            }))
            return
        }

        setWinnerSaveStateById((current) => ({
            ...current,
            [entry.id]: {
                status: 'saving',
                message: null,
            },
        }))

        try {
            const response = await fetch(buildApiUrl('/workspace/strategy-benchmarks/from-positive-history'), {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Authorization': `Bearer ${authToken}`,
                },
                body: JSON.stringify({
                    entry,
                    workspace_id: 'default',
                    is_favorite: false,
                }),
            })
            const data = await readJsonResponse(response)
            if (!response.ok) {
                throw new Error(extractApiErrorMessage(data, 'I could not save this winner to the strategy library.'))
            }

            const benchmarkLabel = String(data?.benchmark?.label || entry.label || 'benchmark').trim()
            setWinnerSaveStateById((current) => ({
                ...current,
                [entry.id]: {
                    status: 'saved',
                    message: data?.already_exists
                        ? `Already available in the strategy library as ${benchmarkLabel}.`
                        : `Saved to the strategy library as ${benchmarkLabel}.`,
                },
            }))
        } catch (error) {
            setWinnerSaveStateById((current) => ({
                ...current,
                [entry.id]: {
                    status: 'error',
                    message: error instanceof Error ? error.message : String(error || 'I could not save this winner to the strategy library.'),
                },
            }))
        }
    }

    async function handleRefreshCatalog({ silent = false } = {}) {
        const requestId = catalogRefreshRequestIdRef.current + 1
        catalogRefreshRequestIdRef.current = requestId

        if (!silent) {
            setCatalogRefreshState({
                status: 'info',
                message: 'Refreshing the live Positive history catalog...',
                loading: true,
            })
        } else if (!hasLiveCatalogHydrated) {
            setCatalogRefreshState({
                status: 'info',
                message: 'Loading the latest live Positive history catalog...',
                loading: true,
            })
        }

        try {
            const sharedPayload = await fetchSharedPositiveHistoryCatalog()
            if (catalogRefreshRequestIdRef.current !== requestId) {
                return
            }
            const mergedState = mergeLocalAndSharedPositiveHistoryCatalog(sharedPayload)
            setCatalogState(mergedState)
            setHasLiveCatalogHydrated(true)
            setCatalogRefreshState({
                status: silent ? '' : 'success',
                message: silent ? '' : `Catalog refreshed from the shared winner registry (${mergedState.lastUpdated}).`,
                loading: false,
            })
            if (!silent) {
                return
            }
        } catch (error) {
            if (catalogRefreshRequestIdRef.current !== requestId) {
                return
            }

            if (!hasLiveCatalogHydrated) {
                const localFallbackState = createLocalPositiveHistoryCatalogState()
                setCatalogState(localFallbackState)
            }

            const errorMessage = error instanceof Error
                ? error.message
                : String(error || 'I could not refresh Positive history right now.')

            setCatalogRefreshState({
                status: 'error',
                message: hasLiveCatalogHydrated
                    ? errorMessage
                    : `${errorMessage} Showing the bundled fallback snapshot until the live catalog responds again.`,
                loading: false,
            })
        }
    }

    useEffect(() => {
        void handleRefreshCatalog({ silent: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    return (
        <ResearchPositiveStrategiesPaneBase
            catalog={catalogState.catalog}
            lastUpdated={catalogState.lastUpdated}
            onSaveWinner={handleSaveWinner}
            winnerSaveStateById={winnerSaveStateById}
            onRefreshCatalog={handleRefreshCatalog}
            isBootstrappingCatalog={!hasLiveCatalogHydrated}
            isRefreshingCatalog={catalogRefreshState.loading}
            refreshCatalogMessage={catalogRefreshState.message}
            refreshCatalogStatus={catalogRefreshState.status}
        />
    )
}

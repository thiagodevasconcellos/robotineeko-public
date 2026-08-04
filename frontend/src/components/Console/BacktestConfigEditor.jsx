import { useEffect, useMemo, useState } from 'react'
import './Backtester.css'
import { TIMEFRAME_OPTIONS } from '../../utils/timeframes.js'
import { BACKTEST_DEFAULTS } from './backtestDefaults.js'
import {
    BACKTEST_ASSET_TYPE_DEFINITIONS,
    BACKTEST_COST_PROFILE_DEFINITIONS,
    buildBacktestCostPolicy,
    buildBacktestCostProfileValues,
    getBacktestCostFieldResetValue,
    getBacktestCostProfileDefinition,
    normalizeBacktestCostProfile,
    resolveBacktestAssetType,
} from './backtestCostProfiles.js'
import {
    BACKTEST_MARGIN_MODEL_DEFINITIONS,
    resolveBacktestCapitalModel,
} from './backtestCapitalModels.js'

const DEFAULT_PRICING_INPUT_MODE = 'asset_default'
const DEFAULT_NUMBER_TOLERANCE = 1e-9

function toFiniteNumber(value, fallback = 0) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : fallback
}

function numbersMatch(left, right, tolerance = DEFAULT_NUMBER_TOLERANCE) {
    return Math.abs(toFiniteNumber(left) - toFiniteNumber(right)) <= tolerance
}

function normalizeBacktestSymbol(value, fallback = BACKTEST_DEFAULTS.symbol) {
    return String(value || fallback).trim().toUpperCase() || BACKTEST_DEFAULTS.symbol
}

function formatCompactNumber(value, maximumFractionDigits = 4) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '0'
    }
    return numeric.toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits,
    })
}

function formatPercentRate(value, maximumFractionDigits = 3) {
    return `${formatCompactNumber(Number(value || 0) * 100, maximumFractionDigits)}%`
}

function formatCapitalCurrencyAmount(value, accountCurrency = '', maximumFractionDigits = 2) {
    const currency = String(accountCurrency || '').trim().toUpperCase()
    if (!currency) {
        return formatCompactNumber(value, maximumFractionDigits)
    }
    return `${currency} ${formatCompactNumber(value, maximumFractionDigits)}`
}

function describeCostPolicyItem(item = null) {
    const safeItem = item && typeof item === 'object' ? item : {}
    const basis = String(safeItem.basis || '').trim().toLowerCase()
    if (basis === 'percent_notional') {
        return `${formatPercentRate(safeItem.regularRate)} regular · ${formatPercentRate(safeItem.daytradeRate)} day trade`
    }
    if (basis === 'per_contract') {
        return `R$ ${formatCompactNumber(safeItem.rate, 2)} per contract`
    }
    if (basis === 'spread_pips') {
        return `${formatCompactNumber(safeItem.rate, 3)} pips`
    }
    if (basis === 'pips') {
        return `${formatCompactNumber(safeItem.amount, 3)} pips`
    }
    if (safeItem.amount != null) {
        return formatCompactNumber(safeItem.amount, 4)
    }
    if (safeItem.rate != null) {
        return formatCompactNumber(safeItem.rate, 4)
    }
    return 'configured'
}

function resolveBacktestPricingPreset(assetType, symbol) {
    const normalizedAssetType = String(assetType || '').trim().toLowerCase()
    const normalizedSymbol = normalizeBacktestSymbol(symbol, '')

    if (normalizedAssetType === 'forex') {
        return {
            id: 'forex_standard',
            label: 'Automatic FX pricing',
            description: 'Uses the standard FX pip baseline for PnL conversion. Most Forex studies should stay on this preset.',
            note: 'Only override these values when the broker uses a non-standard pip convention for the selected symbol.',
            pipSize: 0.0001,
            pipValuePerLot: 10.0,
            symbolHint: normalizedSymbol ? `${normalizedSymbol} detected` : 'FX baseline',
        }
    }

    if (normalizedAssetType === 'b3_mini_future') {
        if (normalizedSymbol.startsWith('WIN')) {
            return {
                id: 'b3_mini_future_win',
                label: 'Automatic WIN mini-index pricing',
                description: 'WIN uses 5-point ticks with an R$ 1.00 tick value per contract in the backtest baseline.',
                note: 'This preset is symbol-specific and is kept in sync automatically while the pricing mode stays automatic.',
                pipSize: 5.0,
                pipValuePerLot: 1.0,
                symbolHint: `${normalizedSymbol} matched WIN`,
            }
        }
        if (normalizedSymbol.startsWith('WDO')) {
            return {
                id: 'b3_mini_future_wdo',
                label: 'Automatic WDO mini-dollar pricing',
                description: 'WDO uses 0.5-point ticks with an R$ 5.00 tick value per contract in the backtest baseline.',
                note: 'This preset is symbol-specific and is kept in sync automatically while the pricing mode stays automatic.',
                pipSize: 0.5,
                pipValuePerLot: 5.0,
                symbolHint: `${normalizedSymbol} matched WDO`,
            }
        }
        return {
            id: 'b3_mini_future_generic',
            label: 'Automatic B3 mini-future fallback',
            description: 'The selected mini-future symbol does not have a dedicated preset yet, so the editor keeps a conservative fallback.',
            note: 'Confirm tick size and BRL tick value for this symbol before relying on the backtest financially.',
            pipSize: 1.0,
            pipValuePerLot: 1.0,
            symbolHint: normalizedSymbol ? `${normalizedSymbol} requires manual confirmation` : 'Generic mini-future fallback',
        }
    }

    return {
        id: normalizedAssetType || 'cash_notional',
        label: 'Automatic direct-price model',
        description: 'This asset type prices PnL directly from BRL price delta times volume, so pip inputs are kept out of the way by default.',
        note: 'Manual pip overrides remain available in Advanced pricing, but most B3 cash, option and termo studies should not need them.',
        pipSize: 0.01,
        pipValuePerLot: 0.0,
        symbolHint: normalizedSymbol ? `${normalizedSymbol} using direct-price baseline` : 'Direct-price baseline',
    }
}

function inferPricingInputMode(backtest, pricingPreset) {
    const explicitMode = String(backtest?.pricingInputMode || '').trim().toLowerCase()
    if (explicitMode === 'custom') {
        return 'custom'
    }
    if (explicitMode === DEFAULT_PRICING_INPUT_MODE) {
        return DEFAULT_PRICING_INPUT_MODE
    }

    const currentPipSize = Number(backtest?.pipSize)
    const currentPipValuePerLot = Number(backtest?.pipValuePerLot)
    if (!Number.isFinite(currentPipSize) || !Number.isFinite(currentPipValuePerLot)) {
        return DEFAULT_PRICING_INPUT_MODE
    }

    if (
        numbersMatch(currentPipSize, pricingPreset.pipSize)
        && numbersMatch(currentPipValuePerLot, pricingPreset.pipValuePerLot)
    ) {
        return DEFAULT_PRICING_INPUT_MODE
    }

    if (
        numbersMatch(currentPipSize, BACKTEST_DEFAULTS.pipSize)
        && numbersMatch(currentPipValuePerLot, BACKTEST_DEFAULTS.pipValuePerLot)
        && pricingPreset.id !== 'forex_standard'
    ) {
        return DEFAULT_PRICING_INPUT_MODE
    }

    return 'custom'
}

function resolveContextDefaultAssetType(brokerProfile = null, rawBacktest = null, costProfile = '') {
    return resolveBacktestAssetType('', brokerProfile, rawBacktest, costProfile)
}

function SelectAssetType({ value, onChange }) {
    return (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
            {Object.values(BACKTEST_ASSET_TYPE_DEFINITIONS).map((assetType) => (
                <option key={assetType.id} value={assetType.id}>{assetType.label}</option>
            ))}
        </select>
    )
}

function SelectExecutionMode({ value, onChange }) {
    return (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
            <option value='next_bar_open'>next bar open</option>
            <option value='same_bar'>same bar</option>
        </select>
    )
}

function SelectCostProfile({ value, onChange }) {
    return (
        <select value={normalizeBacktestCostProfile(value)} onChange={(event) => onChange(event.target.value)}>
            {Object.values(BACKTEST_COST_PROFILE_DEFINITIONS).map((profile) => (
                <option key={profile.id} value={profile.id}>{profile.label}</option>
            ))}
        </select>
    )
}

function SelectPortfolioMode({ value, onChange }) {
    return (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
            <option value='shared_pipe'>shared pipe</option>
            <option value='parallel_sleeves'>parallel sleeves</option>
        </select>
    )
}

function SelectMarginModel({ value, onChange }) {
    return (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
            {Object.values(BACKTEST_MARGIN_MODEL_DEFINITIONS).map((model) => (
                <option key={model.id} value={model.id}>{model.label}</option>
            ))}
        </select>
    )
}

function FieldNumber({
    label,
    description = '',
    value,
    step = 'any',
    onChange,
    onCommit,
    onReset,
    min = undefined,
}) {
    return (
        <div className='field'>
            <div className='fieldHeader'>
                <label>{label}</label>
                {onReset ? (
                    <button type='button' className='fieldResetButton' onClick={onReset} title={`Reset ${label}`} aria-label={`Reset ${label}`}>
                        ↺
                    </button>
                ) : null}
            </div>
            <input
                type='number'
                value={value}
                step={step}
                min={min}
                onChange={(event) => onChange(event.target.value)}
                onBlur={(event) => onCommit?.(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                        event.currentTarget.blur()
                    }
                }}
            />
            {description ? <div className='fieldDescription'>{description}</div> : null}
        </div>
    )
}

export function BacktestConfigEditor({
    backtest,
    setBacktest,
    activeTab,
    setActiveTab,
    onLogEvent,
    chartSettings = null,
    lazyChartBars = 0,
    loadedChartCandles = 0,
    isStale = false,
    showPanelTabs = true,
    showToolbarActions = false,
    onResetAll = null,
    activeBrokerProfile = null,
}) {
    const [advancedSections, setAdvancedSections] = useState({
        capital: false,
        sizing: false,
        costs: false,
        execution: false,
    })
    const normalizedLoadedChartCandles = Math.max(0, Number(loadedChartCandles) || 0)
    const normalizedLazyChartBars = Math.max(0, Number(lazyChartBars) || 0)
    const chartSymbol = String(chartSettings?.symbol || BACKTEST_DEFAULTS.symbol).trim().toUpperCase() || BACKTEST_DEFAULTS.symbol
    const effectiveBacktestSymbol = normalizeBacktestSymbol(backtest?.symbol, chartSymbol)
    const activeCostProfile = normalizeBacktestCostProfile(backtest?.costProfile)
    const activeCostProfileDefinition = getBacktestCostProfileDefinition(activeCostProfile, activeBrokerProfile, backtest)
    const effectiveCostPolicy = buildBacktestCostPolicy(backtest, activeBrokerProfile)
    const effectiveAssetType = resolveBacktestAssetType(backtest?.assetType, activeBrokerProfile, backtest, activeCostProfile)
    const activeAssetTypeDefinition = BACKTEST_ASSET_TYPE_DEFINITIONS[effectiveAssetType]
    const activeBrokerLabel = String(activeBrokerProfile?.label || '').trim()
    const pricingPreset = useMemo(
        () => resolveBacktestPricingPreset(effectiveAssetType, effectiveBacktestSymbol),
        [effectiveAssetType, effectiveBacktestSymbol],
    )
    const pricingInputMode = useMemo(
        () => inferPricingInputMode(backtest, pricingPreset),
        [backtest, pricingPreset],
    )
    const effectiveCapitalModel = useMemo(
        () => resolveBacktestCapitalModel(backtest?.capitalModel, {
            assetType: effectiveAssetType,
            symbol: effectiveBacktestSymbol,
            initialBalance: Number(backtest?.initialBalance),
        }),
        [backtest?.capitalModel, backtest?.initialBalance, effectiveAssetType, effectiveBacktestSymbol],
    )
    const capitalModelDefinition = BACKTEST_MARGIN_MODEL_DEFINITIONS[effectiveCapitalModel.marginModel] || BACKTEST_MARGIN_MODEL_DEFINITIONS.disabled_legacy
    const showCapitalAdvanced = advancedSections.capital || pricingInputMode === 'custom'
    const showSizingAdvanced = advancedSections.sizing || Boolean(backtest?.capitalModel && Object.keys(backtest.capitalModel).length)
    const showCostsAdvanced = advancedSections.costs || activeCostProfile === 'custom'
    const showExecutionAdvanced = advancedSections.execution
    const summarizedCostItems = useMemo(() => {
        const explicitItems = Array.isArray(effectiveCostPolicy?.explicit_cost_items)
            ? effectiveCostPolicy.explicit_cost_items
            : []
        const executionItems = Array.isArray(effectiveCostPolicy?.non_explicit_execution_items)
            ? effectiveCostPolicy.non_explicit_execution_items.filter((item) => Number(item?.amount || 0) > 0)
            : []
        return [...explicitItems, ...executionItems]
    }, [effectiveCostPolicy])

    useEffect(() => {
        if (pricingInputMode !== DEFAULT_PRICING_INPUT_MODE) {
            return
        }
        setBacktest((previous) => {
            const currentSymbol = normalizeBacktestSymbol(previous?.symbol, chartSymbol)
            const previousPreset = resolveBacktestPricingPreset(
                resolveBacktestAssetType(previous?.assetType, activeBrokerProfile, previous, normalizeBacktestCostProfile(previous?.costProfile)),
                currentSymbol,
            )
            const previousMode = inferPricingInputMode(previous, previousPreset)
            if (previousMode !== DEFAULT_PRICING_INPUT_MODE) {
                return previous
            }

            const currentPipSize = Number(previous?.pipSize)
            const currentPipValuePerLot = Number(previous?.pipValuePerLot)
            if (
                numbersMatch(currentPipSize, pricingPreset.pipSize)
                && numbersMatch(currentPipValuePerLot, pricingPreset.pipValuePerLot)
                && String(previous?.pricingInputMode || '').trim().toLowerCase() === DEFAULT_PRICING_INPUT_MODE
            ) {
                return previous
            }
            return {
                ...previous,
                pipSize: pricingPreset.pipSize,
                pipValuePerLot: pricingPreset.pipValuePerLot,
                pricingInputMode: DEFAULT_PRICING_INPUT_MODE,
            }
        })
    }, [activeBrokerProfile, chartSymbol, pricingInputMode, pricingPreset, setBacktest])

    function toggleAdvancedSection(sectionKey) {
        setAdvancedSections((previous) => ({
            ...previous,
            [sectionKey]: !previous[sectionKey],
        }))
    }

    function updateField(field, value) {
        setBacktest((previous) => ({
            ...previous,
            [field]: value,
        }))
    }

    function updateCostField(field, value) {
        setBacktest((previous) => ({
            ...previous,
            [field]: value,
            costProfile: 'custom',
        }))
    }

    function updatePricingField(field, value) {
        setBacktest((previous) => ({
            ...previous,
            [field]: value,
            pricingInputMode: 'custom',
        }))
    }

    function updateCapitalModelField(field, value) {
        setBacktest((previous) => ({
            ...previous,
            capitalModel: {
                ...(previous?.capitalModel && typeof previous.capitalModel === 'object' ? previous.capitalModel : {}),
                [field]: value,
            },
        }))
    }

    function resetCapitalModelField(field) {
        setBacktest((previous) => {
            const nextCapitalModel = { ...(previous?.capitalModel && typeof previous.capitalModel === 'object' ? previous.capitalModel : {}) }
            delete nextCapitalModel[field]
            return {
                ...previous,
                capitalModel: Object.keys(nextCapitalModel).length ? nextCapitalModel : null,
            }
        })
        logFieldChange(`${field} reset`, 'asset default')
    }

    function resetCapitalModelOverrides() {
        setBacktest((previous) => ({
            ...previous,
            capitalModel: null,
        }))
        logFieldChange('Capital model', 'asset defaults')
    }

    function logFieldChange(fieldLabel, value) {
        onLogEvent?.(`Backtester · ${fieldLabel}: ${String(value ?? '')}`)
    }

    function applyAutomaticPricingPreset({ shouldLog = true } = {}) {
        setBacktest((previous) => ({
            ...previous,
            pipSize: pricingPreset.pipSize,
            pipValuePerLot: pricingPreset.pipValuePerLot,
            pricingInputMode: DEFAULT_PRICING_INPUT_MODE,
        }))
        if (shouldLog) {
            logFieldChange('Pricing preset', pricingPreset.label)
        }
    }

    function applyCostProfile(profileId) {
        const normalizedProfile = normalizeBacktestCostProfile(profileId)
        const nextValues = buildBacktestCostProfileValues(normalizedProfile, activeBrokerProfile, backtest)
        const profileLabel = getBacktestCostProfileDefinition(normalizedProfile, activeBrokerProfile, backtest)?.label || normalizedProfile

        setBacktest((previous) => ({
            ...previous,
            ...nextValues,
            costProfile: normalizedProfile,
            assetType: resolveBacktestAssetType(previous?.assetType, activeBrokerProfile, previous, normalizedProfile),
        }))
        logFieldChange('Cost profile', profileLabel)
    }

    function resetField(field) {
        const normalizedField = String(field || '').trim()
        if (normalizedField === 'costProfile') {
            applyCostProfile(BACKTEST_DEFAULTS.costProfile)
            return
        }
        if (normalizedField === 'pipSize' || normalizedField === 'pipValuePerLot') {
            setBacktest((previous) => ({
                ...previous,
                [normalizedField]: pricingPreset[normalizedField],
                pricingInputMode: 'custom',
            }))
            logFieldChange(`${normalizedField} reset`, pricingPreset[normalizedField])
            return
        }
        const nextValue = normalizedField === 'costProfile'
            ? BACKTEST_DEFAULTS.costProfile
            : normalizedField === 'assetType'
                ? resolveContextDefaultAssetType(activeBrokerProfile, backtest, backtest?.costProfile)
                : getBacktestCostFieldResetValue(normalizedField, backtest?.costProfile, BACKTEST_DEFAULTS, activeBrokerProfile, backtest)
        setBacktest((previous) => ({
            ...previous,
            [normalizedField]: nextValue,
        }))
        logFieldChange(`${normalizedField} reset`, nextValue)
    }

    function resetAllFields() {
        setBacktest((previous) => ({
            ...previous,
            ...BACKTEST_DEFAULTS,
            ...buildBacktestCostProfileValues(BACKTEST_DEFAULTS.costProfile, activeBrokerProfile, BACKTEST_DEFAULTS),
            assetType: resolveContextDefaultAssetType(activeBrokerProfile, BACKTEST_DEFAULTS, BACKTEST_DEFAULTS.costProfile),
            symbol: chartSymbol,
            timeframe: BACKTEST_DEFAULTS.timeframe,
            pricingInputMode: DEFAULT_PRICING_INPUT_MODE,
        }))
        onLogEvent?.('Backtester · Reset all execution fields to defaults.')
        onResetAll?.()
    }

    return (
        <div className='Backtester'>
            {showPanelTabs || showToolbarActions ? (
                <div className='backtesterPanelToolbar'>
                    {showPanelTabs ? (
                        <div className='backtesterPanelTabs'>
                            <button
                                type='button'
                                className={`backtesterPanelTab ${activeTab === 'capital' ? 'active' : ''}`}
                                onClick={() => setActiveTab('capital')}
                            >
                                <span>Capital</span>
                            </button>

                            <button
                                type='button'
                                className={`backtesterPanelTab ${activeTab === 'costs' ? 'active' : ''}`}
                                onClick={() => setActiveTab('costs')}
                            >
                                <span>Costs</span>
                            </button>

                            <button
                                type='button'
                                className={`backtesterPanelTab ${activeTab === 'execution' ? 'active' : ''}`}
                                onClick={() => setActiveTab('execution')}
                            >
                                <span>Execution</span>
                            </button>
                        </div>
                    ) : (
                        <div className='backtesterPanelTabs' />
                    )}

                    {showToolbarActions ? (
                        <div className='backtesterActions'>
                            {isStale ? (
                                <div className='backtesterStaleBadge'>
                                    Outdated run
                                </div>
                            ) : null}
                            <button
                                type='button'
                                className='backtesterToolbarButton'
                                onClick={resetAllFields}
                            >
                                Reset all
                            </button>
                        </div>
                    ) : null}
                </div>
            ) : null}

            <div className='backtesterPanel'>
                <div className='backtesterPanelSection'>
                    {isStale ? (
                        <div className='backtesterStaleNotice'>
                            Current Backtester fields differ from the last completed run.
                        </div>
                    ) : null}
                    {activeTab === 'capital' && (
                        <div className='fieldRow'>
                            <FieldNumber label='Initial balance' value={backtest.initialBalance} min='0' step='0.01' onChange={(value) => updateField('initialBalance', value)} onCommit={(value) => logFieldChange('Initial balance', value)} onReset={() => resetField('initialBalance')} />
                            <FieldNumber label='Initial volume' value={backtest.initialVolume} min='0' step='0.01' onChange={(value) => updateField('initialVolume', value)} onCommit={(value) => logFieldChange('Initial volume', value)} onReset={() => resetField('initialVolume')} />
                            <div className='field'>
                                <div className='fieldHeader'>
                                    <label>Asset type</label>
                                    <button type='button' className='fieldResetButton' onClick={() => resetField('assetType')} title='Reset Asset type' aria-label='Reset Asset type'>↺</button>
                                </div>
                                <SelectAssetType value={effectiveAssetType} onChange={(value) => {
                                    setBacktest((previous) => {
                                        const nextValue = {
                                            ...previous,
                                            assetType: value,
                                        }
                                        if (pricingInputMode !== DEFAULT_PRICING_INPUT_MODE) {
                                            return nextValue
                                        }
                                        const nextPreset = resolveBacktestPricingPreset(value, effectiveBacktestSymbol)
                                        return {
                                            ...nextValue,
                                            pipSize: nextPreset.pipSize,
                                            pipValuePerLot: nextPreset.pipValuePerLot,
                                            pricingInputMode: DEFAULT_PRICING_INPUT_MODE,
                                        }
                                    })
                                    logFieldChange('Asset type', value)
                                }} />
                                <div className='fieldDescription'>
                                    {activeAssetTypeDefinition?.description}
                                </div>
                            </div>
                            <div className='backtesterSummaryCard backtesterWideField'>
                                <div className='backtesterSummaryCardHeader'>
                                    <div>
                                        <div className='backtesterSummaryCardTitle'>Pricing inputs</div>
                                        <div className='backtesterSummaryCardMeta'>
                                            {pricingInputMode === DEFAULT_PRICING_INPUT_MODE ? pricingPreset.label : 'Manual pricing override'}
                                        </div>
                                    </div>
                                    <div className='backtesterSummaryCardActions'>
                                        {pricingInputMode === 'custom' ? (
                                            <button type='button' className='backtesterInlineButton' onClick={() => applyAutomaticPricingPreset()}>
                                                Use automatic preset
                                            </button>
                                        ) : null}
                                        <button type='button' className='backtesterInlineButton' onClick={() => toggleAdvancedSection('capital')}>
                                            {showCapitalAdvanced ? 'Hide advanced' : 'Advanced pricing'}
                                        </button>
                                    </div>
                                </div>
                                <div className='fieldDescription'>
                                    {pricingInputMode === DEFAULT_PRICING_INPUT_MODE
                                        ? pricingPreset.description
                                        : 'Current pip inputs are being kept manually instead of following the automatic asset-type preset.'}
                                </div>
                                <div className='backtesterSummaryChipRow'>
                                    <span className='backtesterSummaryChip'>Pip size {formatCompactNumber(backtest.pipSize, 5)}</span>
                                    <span className='backtesterSummaryChip'>Pip value/lot {formatCompactNumber(backtest.pipValuePerLot, 4)}</span>
                                    <span className='backtesterSummaryChip'>{pricingPreset.symbolHint}</span>
                                </div>
                                <div className='fieldDescription'>
                                    {pricingPreset.note}
                                </div>
                            </div>
                            <div className='backtesterSummaryCard backtesterWideField'>
                                <div className='backtesterSummaryCardHeader'>
                                    <div>
                                        <div className='backtesterSummaryCardTitle'>Sizing & margin</div>
                                        <div className='backtesterSummaryCardMeta'>
                                            {capitalModelDefinition.label} · {effectiveCapitalModel.source === 'custom' ? 'manual overrides active' : 'asset defaults'}
                                        </div>
                                    </div>
                                    <div className='backtesterSummaryCardActions'>
                                        {effectiveCapitalModel.source === 'custom' ? (
                                            <button type='button' className='backtesterInlineButton' onClick={resetCapitalModelOverrides}>
                                                Reset model defaults
                                            </button>
                                        ) : null}
                                        <button type='button' className='backtesterInlineButton' onClick={() => toggleAdvancedSection('sizing')}>
                                            {showSizingAdvanced ? 'Hide advanced' : 'Advanced sizing'}
                                        </button>
                                    </div>
                                </div>
                                <div className='fieldDescription'>
                                    {capitalModelDefinition.description}{' '}
                                    {effectiveCapitalModel.source === 'custom'
                                        ? 'Current values include manual overrides on top of the asset defaults.'
                                        : 'Current values are being derived automatically from the active asset type and backtest symbol.'}
                                </div>
                                <div className='backtesterSummaryChipRow'>
                                    <span className='backtesterSummaryChip'>Currency {effectiveCapitalModel.accountCurrency}</span>
                                    <span className='backtesterSummaryChip'>Contract/lot {formatCompactNumber(effectiveCapitalModel.contractSizePerLot, 4)}</span>
                                    <span className='backtesterSummaryChip'>Min lot {formatCompactNumber(effectiveCapitalModel.minLot, 4)}</span>
                                    <span className='backtesterSummaryChip'>Lot step {formatCompactNumber(effectiveCapitalModel.lotStep, 4)}</span>
                                    <span className='backtesterSummaryChip'>Max lot {formatCompactNumber(effectiveCapitalModel.maxLot, 4)}</span>
                                    {effectiveCapitalModel.marginModel === 'fixed_per_lot' ? (
                                        <span className='backtesterSummaryChip'>
                                            Margin/lot {formatCapitalCurrencyAmount(effectiveCapitalModel.marginPerLot, effectiveCapitalModel.accountCurrency)}
                                        </span>
                                    ) : (
                                        <>
                                            <span className='backtesterSummaryChip'>Leverage {formatCompactNumber(effectiveCapitalModel.accountLeverage, 2)}x</span>
                                            <span className='backtesterSummaryChip'>Long rate {formatPercentRate(effectiveCapitalModel.marginLongRate, 3)}</span>
                                            <span className='backtesterSummaryChip'>Short rate {formatPercentRate(effectiveCapitalModel.marginShortRate, 3)}</span>
                                        </>
                                    )}
                                </div>
                                <div className='fieldDescription'>
                                    `Max affordable` and `Base variable` sleeves use this model to decide how much volume can actually be opened. Legacy fixed-volume runs keep the current behavior unless you opt into the explicit portfolio contract.
                                </div>
                            </div>
                            {showCapitalAdvanced ? (
                                <>
                                    <FieldNumber label='Pip size' value={backtest.pipSize} min='0' step='0.00001' onChange={(value) => updatePricingField('pipSize', value)} onCommit={(value) => logFieldChange('Pip size', value)} onReset={() => resetField('pipSize')} />
                                    <FieldNumber label='Pip value per lot' value={backtest.pipValuePerLot} min='0' step='0.01' onChange={(value) => updatePricingField('pipValuePerLot', value)} onCommit={(value) => logFieldChange('Pip value per lot', value)} onReset={() => resetField('pipValuePerLot')} />
                                </>
                            ) : null}
                            {showSizingAdvanced ? (
                                <>
                                    <div className='field'>
                                        <div className='fieldHeader'>
                                            <label>Margin model</label>
                                            <button type='button' className='fieldResetButton' onClick={() => resetCapitalModelField('marginModel')} title='Reset Margin model' aria-label='Reset Margin model'>↺</button>
                                        </div>
                                        <SelectMarginModel value={effectiveCapitalModel.marginModel} onChange={(value) => {
                                            updateCapitalModelField('marginModel', value)
                                            logFieldChange('Margin model', value)
                                        }} />
                                        <div className='fieldDescription'>
                                            {capitalModelDefinition.description}
                                        </div>
                                    </div>
                                    <FieldNumber
                                        label='Contract size per lot'
                                        value={effectiveCapitalModel.contractSizePerLot}
                                        min='0.000001'
                                        step='0.01'
                                        onChange={(value) => updateCapitalModelField('contractSizePerLot', value)}
                                        onCommit={(value) => logFieldChange('Contract size per lot', value)}
                                        onReset={() => resetCapitalModelField('contractSizePerLot')}
                                    />
                                    <FieldNumber
                                        label='Minimum lot'
                                        value={effectiveCapitalModel.minLot}
                                        min='0.000001'
                                        step='0.01'
                                        onChange={(value) => updateCapitalModelField('minLot', value)}
                                        onCommit={(value) => logFieldChange('Minimum lot', value)}
                                        onReset={() => resetCapitalModelField('minLot')}
                                    />
                                    <FieldNumber
                                        label='Lot step'
                                        value={effectiveCapitalModel.lotStep}
                                        min='0.000001'
                                        step='0.01'
                                        onChange={(value) => updateCapitalModelField('lotStep', value)}
                                        onCommit={(value) => logFieldChange('Lot step', value)}
                                        onReset={() => resetCapitalModelField('lotStep')}
                                    />
                                    <FieldNumber
                                        label='Maximum lot'
                                        value={effectiveCapitalModel.maxLot}
                                        min='0.000001'
                                        step='0.01'
                                        onChange={(value) => updateCapitalModelField('maxLot', value)}
                                        onCommit={(value) => logFieldChange('Maximum lot', value)}
                                        onReset={() => resetCapitalModelField('maxLot')}
                                    />
                                    {effectiveCapitalModel.marginModel === 'fixed_per_lot' ? (
                                        <FieldNumber
                                            label='Margin per lot'
                                            description='Used directly for each contract or lot when the model is fixed-per-lot.'
                                            value={effectiveCapitalModel.marginPerLot}
                                            min='0.000001'
                                            step='0.01'
                                            onChange={(value) => updateCapitalModelField('marginPerLot', value)}
                                            onCommit={(value) => logFieldChange('Margin per lot', value)}
                                            onReset={() => resetCapitalModelField('marginPerLot')}
                                        />
                                    ) : (
                                        <>
                                            <FieldNumber
                                                label='Account leverage'
                                                description='When long/short rates are not overridden, the margin rate falls back to 1 / leverage.'
                                                value={effectiveCapitalModel.accountLeverage}
                                                min='0.000001'
                                                step='0.01'
                                                onChange={(value) => updateCapitalModelField('accountLeverage', value)}
                                                onCommit={(value) => logFieldChange('Account leverage', value)}
                                                onReset={() => resetCapitalModelField('accountLeverage')}
                                            />
                                            <FieldNumber
                                                label='Margin long rate'
                                                description='Optional direct override for long-side reserved margin as a fraction of notional.'
                                                value={effectiveCapitalModel.marginLongRate}
                                                min='0.000001'
                                                step='0.0001'
                                                onChange={(value) => updateCapitalModelField('marginLongRate', value)}
                                                onCommit={(value) => logFieldChange('Margin long rate', value)}
                                                onReset={() => resetCapitalModelField('marginLongRate')}
                                            />
                                            <FieldNumber
                                                label='Margin short rate'
                                                description='Optional direct override for short-side reserved margin as a fraction of notional.'
                                                value={effectiveCapitalModel.marginShortRate}
                                                min='0.000001'
                                                step='0.0001'
                                                onChange={(value) => updateCapitalModelField('marginShortRate', value)}
                                                onCommit={(value) => logFieldChange('Margin short rate', value)}
                                                onReset={() => resetCapitalModelField('marginShortRate')}
                                            />
                                        </>
                                    )}
                                </>
                            ) : null}
                        </div>
                    )}

                    {activeTab === 'costs' && (
                        <div className='fieldRow'>
                            <div className='field'>
                                <div className='fieldHeader'>
                                    <label>Cost profile</label>
                                    <button type='button' className='fieldResetButton' onClick={() => resetField('costProfile')} title='Reset Cost profile' aria-label='Reset Cost profile'>↺</button>
                                </div>
                                <SelectCostProfile value={activeCostProfile} onChange={applyCostProfile} />
                                <div className='fieldDescription'>
                                    {activeCostProfileDefinition?.description}
                                    {activeBrokerLabel ? ` Active header broker: ${activeBrokerLabel}.` : ''}
                                    {' '}Manual changes in Advanced costs switch the profile back to `Custom`.
                                </div>
                                <div className='fieldDescription'>
                                    Effective model: {effectiveCostPolicy.cost_profile_label || activeCostProfileDefinition?.label || activeCostProfile}. Asset: {effectiveCostPolicy.asset_type_label || activeAssetTypeDefinition?.label || effectiveAssetType}.
                                </div>
                            </div>
                            <div className='backtesterSummaryCard backtesterWideField'>
                                <div className='backtesterSummaryCardHeader'>
                                    <div>
                                        <div className='backtesterSummaryCardTitle'>Effective costs</div>
                                        <div className='backtesterSummaryCardMeta'>
                                            {effectiveCostPolicy.cost_profile_label || activeCostProfileDefinition?.label || activeCostProfile}
                                        </div>
                                    </div>
                                    <div className='backtesterSummaryCardActions'>
                                        <button type='button' className='backtesterInlineButton' onClick={() => toggleAdvancedSection('costs')}>
                                            {showCostsAdvanced ? 'Hide advanced' : 'Advanced costs'}
                                        </button>
                                    </div>
                                </div>
                                {summarizedCostItems.length ? (
                                    <div className='backtesterSummaryList'>
                                        {summarizedCostItems.map((item) => (
                                            <div key={item.id || item.label} className='backtesterSummaryListItem'>
                                                <span>{item.label || item.id}</span>
                                                <strong>{describeCostPolicyItem(item)}</strong>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className='fieldDescription'>
                                        No additional spread or slippage shell is currently being added beyond the active broker profile defaults.
                                    </div>
                                )}
                                {Array.isArray(effectiveCostPolicy.profile_notes) && effectiveCostPolicy.profile_notes.length ? (
                                    <div className='backtesterSummaryNotes'>
                                        {effectiveCostPolicy.profile_notes.map((note) => (
                                            <div key={note} className='fieldDescription'>{note}</div>
                                        ))}
                                    </div>
                                ) : null}
                                {effectiveCostPolicy.taxes_estimated ? (
                                    <div className='fieldDescription'>
                                        Tax lines are estimated automatically for supported profitable B3 closes and appear separated from operational costs in the results breakdown.
                                    </div>
                                ) : null}
                            </div>
                            {showCostsAdvanced ? (
                                <>
                                    <FieldNumber label='Spread in pips' value={backtest.spreadInPips} min='0' step='0.01' onChange={(value) => updateCostField('spreadInPips', value)} onCommit={(value) => logFieldChange('Spread in pips', value)} onReset={() => resetField('spreadInPips')} />
                                    <FieldNumber label='Entry slippage in pips' value={backtest.entrySlippageInPips} min='0' step='0.01' onChange={(value) => updateCostField('entrySlippageInPips', value)} onCommit={(value) => logFieldChange('Entry slippage in pips', value)} onReset={() => resetField('entrySlippageInPips')} />
                                    <FieldNumber label='Close slippage in pips' value={backtest.closeSlippageInPips} min='0' step='0.01' onChange={(value) => updateCostField('closeSlippageInPips', value)} onCommit={(value) => logFieldChange('Close slippage in pips', value)} onReset={() => resetField('closeSlippageInPips')} />
                                    <FieldNumber label='Take profit slippage in pips' value={backtest.takeProfitSlippageInPips} min='0' step='0.01' onChange={(value) => updateCostField('takeProfitSlippageInPips', value)} onCommit={(value) => logFieldChange('Take profit slippage in pips', value)} onReset={() => resetField('takeProfitSlippageInPips')} />
                                    <FieldNumber label='Stop loss slippage in pips' value={backtest.stopLossSlippageInPips} min='0' step='0.01' onChange={(value) => updateCostField('stopLossSlippageInPips', value)} onCommit={(value) => logFieldChange('Stop loss slippage in pips', value)} onReset={() => resetField('stopLossSlippageInPips')} />
                                    <FieldNumber label='Trailing stop slippage in pips' value={backtest.trailingStopSlippageInPips} min='0' step='0.01' onChange={(value) => updateCostField('trailingStopSlippageInPips', value)} onCommit={(value) => logFieldChange('Trailing stop slippage in pips', value)} onReset={() => resetField('trailingStopSlippageInPips')} />
                                    <FieldNumber label='Minimum stop distance in pips' description='Defaults to 0.0 because MetaTrader has no universal platform-wide minimum stop distance. This field only simulates an extra broker-side floor in backtests; live minimum stop distance still comes from the symbol Stops Level reported by the broker.' value={backtest.minimumStopDistanceInPips} min='0' step='0.01' onChange={(value) => updateCostField('minimumStopDistanceInPips', value)} onCommit={(value) => logFieldChange('Minimum stop distance in pips', value)} onReset={() => resetField('minimumStopDistanceInPips')} />
                                    <FieldNumber label='Volatility slippage multiplier' value={backtest.volatilitySlippageMultiplier} min='0' step='0.001' onChange={(value) => updateCostField('volatilitySlippageMultiplier', value)} onCommit={(value) => logFieldChange('Volatility slippage multiplier', value)} onReset={() => resetField('volatilitySlippageMultiplier')} />
                                </>
                            ) : null}
                        </div>
                    )}

                    {activeTab === 'execution' && (
                        <div className='fieldRow'>
                            <div className='field'>
                                <div className='fieldHeader'>
                                    <label>Backtest market symbol</label>
                                    <button
                                        type='button'
                                        className='fieldResetButton'
                                        onClick={() => {
                                            updateField('symbol', chartSymbol)
                                            logFieldChange('Backtest market symbol', chartSymbol)
                                        }}
                                        title='Reset Backtest market symbol'
                                        aria-label='Reset Backtest market symbol'
                                    >
                                        ↺
                                    </button>
                                </div>
                                <input
                                    type='text'
                                    value={effectiveBacktestSymbol}
                                    onChange={(event) => updateField('symbol', event.target.value.toUpperCase())}
                                    onBlur={(event) => {
                                        const nextValue = String(event.target.value || chartSymbol).trim().toUpperCase() || chartSymbol
                                        updateField('symbol', nextValue)
                                        logFieldChange('Backtest market symbol', nextValue)
                                    }}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Enter') {
                                            event.currentTarget.blur()
                                        }
                                    }}
                                />
                                <div className='fieldDescription'>
                                    Independent from the visible chart. Backtest flags are buffered automatically after a run; the chart only shows them when the visible market is compatible.
                                </div>
                            </div>

                            <div className='field'>
                                <div className='fieldHeader'>
                                    <label>Backtest market timeframe</label>
                                    <button
                                        type='button'
                                        className='fieldResetButton'
                                        onClick={() => {
                                            updateField('timeframe', BACKTEST_DEFAULTS.timeframe)
                                            logFieldChange('Backtest market timeframe', BACKTEST_DEFAULTS.timeframe)
                                        }}
                                        title='Reset Backtest market timeframe'
                                        aria-label='Reset Backtest market timeframe'
                                    >
                                        ↺
                                    </button>
                                </div>
                                <select
                                    value={String(backtest.timeframe || BACKTEST_DEFAULTS.timeframe).trim().toUpperCase() || BACKTEST_DEFAULTS.timeframe}
                                    onChange={(event) => {
                                        const nextValue = String(event.target.value || BACKTEST_DEFAULTS.timeframe).trim().toUpperCase() || BACKTEST_DEFAULTS.timeframe
                                        updateField('timeframe', nextValue)
                                        logFieldChange('Backtest market timeframe', nextValue)
                                    }}
                                >
                                    {TIMEFRAME_OPTIONS.map(([value, label]) => (
                                        <option key={value} value={value}>{label}</option>
                                    ))}
                                </select>
                            </div>

                            <div className='field backtesterHistoryField'>
                                <div className='fieldHeader'>
                                    <label>Backtest history</label>
                                    <button type='button' className='fieldResetButton' onClick={() => {
                                        setBacktest((previous) => ({
                                            ...previous,
                                            historyScopeMode: BACKTEST_DEFAULTS.historyScopeMode,
                                            historyScopeBars: BACKTEST_DEFAULTS.historyScopeBars,
                                        }))
                                        onLogEvent?.('Backtester · Backtest history reset.')
                                    }} title='Reset Backtest history' aria-label='Reset Backtest history'>↺</button>
                                </div>
                                <div className='backtestHistoryScopeRow'>
                                    <select
                                        value={backtest.historyScopeMode || 'loaded_chart'}
                                        onChange={(event) => {
                                            const nextMode = event.target.value
                                            setBacktest((previous) => ({
                                                ...previous,
                                                historyScopeMode: nextMode,
                                                historyScopeBars: nextMode === 'custom'
                                                    ? ((previous.historyScopeBars ?? normalizedLazyChartBars) || 1)
                                                    : null,
                                            }))
                                            logFieldChange('Backtest history', nextMode)
                                        }}
                                    >
                                        <option value='loaded_chart'>Current lazy range</option>
                                        <option value='custom'>Custom range</option>
                                    </select>
                                    <input
                                        type='number'
                                        min='1'
                                        step='1'
                                        value={backtest.historyScopeMode === 'custom' ? ((backtest.historyScopeBars ?? normalizedLazyChartBars) || '') : (normalizedLazyChartBars || '')}
                                        onChange={(event) => {
                                            const nextValue = event.target.value
                                            setBacktest((previous) => ({
                                                ...previous,
                                                historyScopeMode: 'custom',
                                                historyScopeBars: nextValue,
                                            }))
                                        }}
                                        onBlur={(event) => {
                                            const nextValue = Math.max(1, Number(event.target.value) || 1)
                                            setBacktest((previous) => ({
                                                ...previous,
                                                historyScopeMode: 'custom',
                                                historyScopeBars: nextValue,
                                            }))
                                            logFieldChange('Custom backtest candles', nextValue)
                                        }}
                                        onKeyDown={(event) => {
                                            if (event.key === 'Enter') {
                                                event.currentTarget.blur()
                                            }
                                        }}
                                    />
                                </div>
                                <div className='fieldDescription'>
                                    Current lazy range uses the chart bar target as an isolated default depth for this backtest market. Loaded in the visible chart right now: {normalizedLoadedChartCandles.toLocaleString()} candles. Lazy target: {normalizedLazyChartBars.toLocaleString()} candles.
                                </div>
                            </div>
                            <div className='backtesterSummaryCard backtesterWideField'>
                                <div className='backtesterSummaryCardHeader'>
                                    <div>
                                        <div className='backtesterSummaryCardTitle'>Execution behavior</div>
                                        <div className='backtesterSummaryCardMeta'>
                                            Defaults stay on the common research-safe execution path.
                                        </div>
                                    </div>
                                    <div className='backtesterSummaryCardActions'>
                                        <button type='button' className='backtesterInlineButton' onClick={() => toggleAdvancedSection('execution')}>
                                            {showExecutionAdvanced ? 'Hide advanced' : 'Advanced execution'}
                                        </button>
                                    </div>
                                </div>
                                <div className='backtesterSummaryChipRow'>
                                    <span className='backtesterSummaryChip'>Execution {backtest.executionMode || 'next_bar_open'}</span>
                                    <span className='backtesterSummaryChip'>Portfolio {backtest.portfolioMode || BACKTEST_DEFAULTS.portfolioMode}</span>
                                    <span className='backtesterSummaryChip'>
                                        History {backtest.historyScopeMode === 'custom'
                                            ? `${Math.max(1, Number(backtest.historyScopeBars) || 1)} candles`
                                            : 'current lazy range'}
                                    </span>
                                </div>
                                <div className='fieldDescription'>
                                    Shared pipe resolves same-symbol conflicts through one shared lane. Parallel sleeves lets strategies coexist independently in the portfolio backtest.
                                </div>
                            </div>
                            {showExecutionAdvanced ? (
                                <>
                                    <div className='field'>
                                        <div className='fieldHeader'>
                                            <label>Execution mode</label>
                                            <button type='button' className='fieldResetButton' onClick={() => resetField('executionMode')} title='Reset Execution mode' aria-label='Reset Execution mode'>↺</button>
                                        </div>
                                        <SelectExecutionMode value={backtest.executionMode || 'next_bar_open'} onChange={(value) => { updateField('executionMode', value); logFieldChange('Execution mode', value) }} />
                                    </div>

                                    <div className='field'>
                                        <div className='fieldHeader'>
                                            <label>Portfolio mode</label>
                                            <button type='button' className='fieldResetButton' onClick={() => resetField('portfolioMode')} title='Reset Portfolio mode' aria-label='Reset Portfolio mode'>↺</button>
                                        </div>
                                        <SelectPortfolioMode
                                            value={backtest.portfolioMode || BACKTEST_DEFAULTS.portfolioMode}
                                            onChange={(value) => { updateField('portfolioMode', value); logFieldChange('Portfolio mode', value) }}
                                        />
                                        <div className='fieldDescription'>
                                            Shared pipe resolves same-symbol conflicts through one shared lane. Parallel sleeves lets strategies coexist independently in the portfolio backtest.
                                        </div>
                                    </div>
                                </>
                            ) : null}
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}

export default BacktestConfigEditor

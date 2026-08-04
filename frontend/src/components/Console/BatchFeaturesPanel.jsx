import { useEffect, useMemo, useState } from 'react'
import { IndicatorEditor } from '../IndicatorManager/IndicatorEditor'
import { INDICATOR_DEFINITIONS, getIndicatorDefinition } from '../IndicatorManager/indicatorDefinitions'
import {
    buildIndicatorId,
    normalizeIndicator,
} from '../../utils/chartSettings.jsx'
import {
    getFeatureFamily,
    getFeatureFamilyLabel,
    isNeuralFeatureDefinition,
    readFavoriteFeatureNames,
    subscribeFavoriteFeatureNames,
    toggleFavoriteFeatureName,
} from '../../utils/featureCatalog.js'
import '../IndicatorManager.css'

function buildIndicatorLogText(action, indicator) {
    const normalized = normalizeIndicator(indicator)
    const aliasText = normalized.alias || normalized.name
    const definition = getIndicatorDefinition(normalized.name)
    const paramsText = normalized.params.length > 0
        ? normalized.params.map((value, index) => {
            const fieldLabel = definition?.fields?.[index]?.label || `Param ${index + 1}`
            return `${fieldLabel}: ${value}`
        }).join(', ')
        : 'no params'

    return `${action} batch feature ${normalized.name} with alias ${aliasText} (${paramsText}).`
}

export function BatchFeaturesPanel({
    indicators = [],
    onChange,
    onLogEvent,
}) {
    const [activeTab, setActiveTab] = useState('add')
    const [activeAddSubtab, setActiveAddSubtab] = useState('indicator')
    const [selectedIndicatorNameDraft, setSelectedIndicatorNameDraft] = useState(INDICATOR_DEFINITIONS[0]?.name || '')
    const [selectedCurrentIdDraft, setSelectedCurrentIdDraft] = useState('')
    const [collapsedFamilies, setCollapsedFamilies] = useState({})
    const [catalogListTab, setCatalogListTab] = useState('all')
    const [catalogQuery, setCatalogQuery] = useState('')
    const [favoriteFeatureNames, setFavoriteFeatureNames] = useState(() => readFavoriteFeatureNames())

    useEffect(() => subscribeFavoriteFeatureNames(setFavoriteFeatureNames), [])

    const existingAliases = useMemo(
        () => indicators.map((indicator) => indicator.alias).filter(Boolean),
        [indicators],
    )

    const existingPaneIds = useMemo(() => {
        const paneIds = indicators
            .flatMap((indicator) => indicator.lines || [])
            .map((line) => String(line?.paneId || '').trim())
            .filter(Boolean)

        return [...new Set(['volume', ...paneIds])].sort((left, right) => left.localeCompare(right))
    }, [indicators])

    const standardDefinitions = useMemo(
        () => INDICATOR_DEFINITIONS.filter((definition) => !isNeuralFeatureDefinition(definition)),
        []
    )
    const neuralDefinitions = useMemo(
        () => INDICATOR_DEFINITIONS.filter((definition) => isNeuralFeatureDefinition(definition)),
        []
    )
    const sourceDefinitions = activeAddSubtab === 'neural' ? neuralDefinitions : standardDefinitions

    const normalizedCatalogQuery = String(catalogQuery || '').trim().toLowerCase()

    const visibleDefinitions = useMemo(() => {
        let filtered = sourceDefinitions

        if (catalogListTab === 'favorites') {
            filtered = filtered.filter((definition) => favoriteFeatureNames.includes(String(definition?.name || '').trim()))
        }

        if (normalizedCatalogQuery) {
            filtered = filtered.filter((definition) => {
                const label = String(definition?.label || '').trim().toLowerCase()
                const name = String(definition?.name || '').trim().toLowerCase()
                return label.includes(normalizedCatalogQuery) || name.includes(normalizedCatalogQuery)
            })
        }

        return filtered
    }, [catalogListTab, favoriteFeatureNames, normalizedCatalogQuery, sourceDefinitions])

    const visibleDefinitionGroups = useMemo(() => {
        const groups = new Map()
        for (const definition of visibleDefinitions) {
            const family = getFeatureFamily(definition)
            if (!groups.has(family)) {
                groups.set(family, [])
            }
            groups.get(family).push(definition)
        }
        return Array.from(groups.entries())
            .map(([family, definitions]) => ({
                family,
                label: getFeatureFamilyLabel(family),
                definitions: definitions.sort((left, right) => left.label.localeCompare(right.label)),
            }))
            .sort((left, right) => left.label.localeCompare(right.label))
    }, [visibleDefinitions])

    const selectedIndicatorName = visibleDefinitions.some((definition) => definition.name === selectedIndicatorNameDraft)
        ? selectedIndicatorNameDraft
        : (visibleDefinitions[0]?.name || sourceDefinitions[0]?.name || '')

    const selectedCurrentId = indicators.some((indicator) => indicator.id === selectedCurrentIdDraft)
        ? selectedCurrentIdDraft
        : (indicators[0]?.id || '')

    const selectedDefinition = getIndicatorDefinition(selectedIndicatorName)
    const selectedCurrentIndicator = indicators.find((indicator) => indicator.id === selectedCurrentId) || null
    const selectedCurrentDefinition = getIndicatorDefinition(selectedCurrentIndicator?.name)

    function handleToggleFavorite(definitionName) {
        const nextFavorites = toggleFavoriteFeatureName(definitionName, favoriteFeatureNames)
        setFavoriteFeatureNames(nextFavorites)
    }

    function handleAddIndicator(nextIndicator) {
        const nextNumber = indicators.filter((indicator) => indicator.name === nextIndicator.name).length + 1
        const normalized = normalizeIndicator({
            ...nextIndicator,
            id: buildIndicatorId(nextIndicator, nextNumber),
        }, nextNumber)

        onChange?.([...indicators, normalized])
        onLogEvent?.(buildIndicatorLogText('Added', normalized))
        setActiveTab('current')
        setSelectedCurrentIdDraft(normalized.id)
    }

    function handleUpdateIndicator(nextIndicator) {
        if (!selectedCurrentIndicator) {
            return
        }

        const nextIndicators = indicators.map((indicator, index) => (
            indicator.id === selectedCurrentIndicator.id
                ? normalizeIndicator({
                    ...indicator,
                    ...nextIndicator,
                    id: indicator.id,
                }, index)
                : indicator
        ))

        onChange?.(nextIndicators)
        onLogEvent?.(buildIndicatorLogText('Updated', nextIndicator))
    }

    function handleRemoveIndicator() {
        if (!selectedCurrentIndicator) {
            return
        }

        const nextIndicators = indicators.filter((indicator) => indicator.id !== selectedCurrentIndicator.id)
        onChange?.(nextIndicators)
        onLogEvent?.(`Removed batch feature ${selectedCurrentIndicator.alias || selectedCurrentIndicator.name}.`)
        setSelectedCurrentIdDraft(nextIndicators[0]?.id || '')
    }

    return (
        <div className='indicatorManagerLayout'>
            <aside className='indicatorManagerSidebar'>
                <div className='indicatorManagerTabs'>
                    <button
                        type='button'
                        className={activeTab === 'add' ? 'active' : ''}
                        onClick={() => setActiveTab('add')}
                    >
                        Add feature
                    </button>
                    <button
                        type='button'
                        className={activeTab === 'current' ? 'active' : ''}
                        onClick={() => setActiveTab('current')}
                    >
                        Current
                    </button>
                </div>

                {activeTab === 'add' && (
                    <div className='indicatorManagerList indicatorManagerCatalog'>
                        <div className='indicatorManagerCatalogControls'>
                            <div className='indicatorManagerSubtabs'>
                                <button
                                    type='button'
                                    className={activeAddSubtab === 'indicator' ? 'active' : ''}
                                    onClick={() => setActiveAddSubtab('indicator')}
                                >
                                    Indicators
                                </button>
                                <button
                                    type='button'
                                    className={activeAddSubtab === 'neural' ? 'active' : ''}
                                    onClick={() => setActiveAddSubtab('neural')}
                                >
                                    Neural derived
                                </button>
                            </div>
                            <div className='indicatorManagerCatalogFilters'>
                                <button
                                    type='button'
                                    className={catalogListTab === 'all' ? 'active' : ''}
                                    onClick={() => setCatalogListTab('all')}
                                >
                                    All
                                </button>
                                <button
                                    type='button'
                                    className={catalogListTab === 'favorites' ? 'active' : ''}
                                    onClick={() => setCatalogListTab('favorites')}
                                >
                                    Favorites
                                </button>
                            </div>
                            <div className='indicatorManagerSearchRow'>
                                <input
                                    type='text'
                                    value={catalogQuery}
                                    onChange={(event) => setCatalogQuery(event.target.value)}
                                    placeholder='Filter features'
                                    aria-label='Filter features'
                                />
                                {catalogQuery ? (
                                    <button
                                        type='button'
                                        className='indicatorManagerSearchClear'
                                        onClick={() => setCatalogQuery('')}
                                        aria-label='Clear feature filter'
                                        title='Clear feature filter'
                                    >
                                        Clear
                                    </button>
                                ) : null}
                            </div>
                        </div>

                        {catalogListTab === 'favorites' && !favoriteFeatureNames.length ? (
                            <div className='indicatorManagerEmptyList'>No favorite features yet.</div>
                        ) : normalizedCatalogQuery && !visibleDefinitionGroups.length ? (
                            <div className='indicatorManagerEmptyList'>No features match this filter.</div>
                        ) : visibleDefinitionGroups.map((group) => {
                            const isCollapsed = Boolean(collapsedFamilies[group.family])
                            return (
                                <div key={group.family} className='indicatorManagerGroup'>
                                    <button
                                        type='button'
                                        className={`indicatorManagerGroupToggle ${isCollapsed ? 'collapsed' : ''}`}
                                        onClick={() => setCollapsedFamilies((current) => ({
                                            ...current,
                                            [group.family]: !current[group.family],
                                        }))}
                                        aria-expanded={!isCollapsed}
                                    >
                                        <span>{group.label}</span>
                                        <span className='indicatorManagerGroupCaret' aria-hidden='true' />
                                    </button>
                                    {!isCollapsed && (
                                        <div className='indicatorManagerGroupList'>
                                            {group.definitions.map((definition) => (
                                                <div key={definition.name} className='indicatorManagerCatalogEntry'>
                                                    <button
                                                        type='button'
                                                        className={`indicatorManagerCatalogSelect ${selectedIndicatorName === definition.name ? 'active' : ''}`.trim()}
                                                        onClick={() => setSelectedIndicatorNameDraft(definition.name)}
                                                    >
                                                        {definition.label}
                                                    </button>
                                                    <button
                                                        type='button'
                                                        className={`indicatorManagerCatalogFavoriteToggle ${favoriteFeatureNames.includes(definition.name) ? 'isFavorite' : ''}`.trim()}
                                                        onClick={() => handleToggleFavorite(definition.name)}
                                                        title={favoriteFeatureNames.includes(definition.name) ? 'Remove from favorites' : 'Add to favorites'}
                                                        aria-label={favoriteFeatureNames.includes(definition.name) ? `Remove ${definition.label} from favorites` : `Add ${definition.label} to favorites`}
                                                    >
                                                        ★
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                )}

                {activeTab === 'current' && (
                    <div className='indicatorManagerList'>
                        {indicators.length === 0 && (
                            <div className='indicatorManagerEmptyList'>No batch features added yet.</div>
                        )}
                        {indicators.map((indicator) => (
                            <button
                                type='button'
                                key={indicator.id}
                                className={selectedCurrentId === indicator.id ? 'active' : ''}
                                onClick={() => setSelectedCurrentIdDraft(indicator.id)}
                            >
                                {indicator.alias || indicator.name}
                            </button>
                        ))}
                    </div>
                )}
            </aside>

            <section className='indicatorManagerContent'>
                {activeTab === 'add' && (
                    <IndicatorEditor
                        definition={selectedDefinition}
                        existingPaneIds={existingPaneIds}
                        existingAliases={existingAliases}
                        submitLabel='Add feature'
                        onSubmit={handleAddIndicator}
                    />
                )}
                {activeTab === 'current' && (
                    <IndicatorEditor
                        definition={selectedCurrentDefinition}
                        indicator={selectedCurrentIndicator}
                        existingPaneIds={existingPaneIds}
                        existingAliases={existingAliases.filter((alias) => alias !== selectedCurrentIndicator?.alias)}
                        submitLabel='Update feature'
                        onSubmit={handleUpdateIndicator}
                        onRemove={handleRemoveIndicator}
                    />
                )}
            </section>
        </div>
    )
}

export default BatchFeaturesPanel

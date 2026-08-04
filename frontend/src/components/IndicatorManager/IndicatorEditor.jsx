import { useEffect, useMemo, useState } from 'react'
import { normalizeIndicator } from '../../utils/chartSettings.jsx'

function normalizeLineTarget(value) {
    const normalized = String(value || '').trim().toLowerCase()

    if (normalized === 'price' || normalized === 'separate' || normalized === 'hidden') {
        return normalized
    }

    return 'price'
}

function buildDefaultPaneId(indicator, line) {
    return (
        String(line?.paneId || '').trim()
        || String(indicator?.alias || '').trim()
        || String(indicator?.name || '').trim()
        || String(line?.columnName || '').trim()
    )
}

function buildExistingLineLookup(indicator) {
    const lookup = {}

    for (const line of indicator?.lines || []) {
        lookup[line.key] = line
    }

    return lookup
}

function buildLineSettingsScope(definition, indicator) {
    return `${definition?.name || 'definition'}:${indicator?.id || 'new'}`
}

function areLineSettingsEqual(left, right) {
    const leftKeys = Object.keys(left || {})
    const rightKeys = Object.keys(right || {})

    if (leftKeys.length !== rightKeys.length) {
        return false
    }

    for (const key of leftKeys) {
        const leftValue = left?.[key]
        const rightValue = right?.[key]

        if (typeof leftValue === 'object' && leftValue !== null) {
            const nestedLeftKeys = Object.keys(leftValue)
            const nestedRightKeys = Object.keys(rightValue || {})
            if (nestedLeftKeys.length !== nestedRightKeys.length) {
                return false
            }
            for (const nestedKey of nestedLeftKeys) {
                if (leftValue?.[nestedKey] !== rightValue?.[nestedKey]) {
                    return false
                }
            }
            continue
        }

        if (leftValue !== rightValue) {
            return false
        }
    }

    return true
}

export function IndicatorEditor({
    definition,
    indicator,
    existingPaneIds = [],
    existingAliases = [],
    submitLabel,
    onSubmit,
    onRemove,
}) {
    const [values, setValues] = useState({})
    const [alias, setAlias] = useState('')
    const [lineSettings, setLineSettings] = useState({})
    const [error, setError] = useState('')
    const definitionName = String(definition?.name || '')
    const indicatorId = String(indicator?.id || '')
    const lineSettingsScope = buildLineSettingsScope(definition, indicator)

    useEffect(() => {
        if (!definition) {
            setValues({})
            setAlias('')
            setLineSettings({})
            return
        }

        setValues(definition.getInitialValues?.(indicator) || {})
        setAlias(String(indicator?.alias || ''))
        setLineSettings({ __scope: lineSettingsScope })
        setError('')
    }, [definitionName, indicatorId, lineSettingsScope])

    const lineDefinitions = useMemo(
        () => definition?.buildLineDefinitions?.(values) || [],
        [definition, values]
    )

    const normalizedAlias = String(alias || '').trim()
    const hasDuplicateAlias = normalizedAlias
        ? existingAliases.some(
            (currentAlias) => String(currentAlias || '').trim().toLowerCase() === normalizedAlias.toLowerCase()
        )
        : false

    useEffect(() => {
        if (!definition) {
            return
        }

        const existingLineLookup = buildExistingLineLookup(indicator)
        const scopedLineSettings = lineSettings.__scope === lineSettingsScope ? lineSettings : {}

        const normalizedPreview = normalizeIndicator({
            ...(indicator || {}),
            name: definition.name,
            alias: String(alias || indicator?.alias || definition.label || definition.name).trim(),
            params: definition.buildParams(values),
            lines: lineDefinitions.map((line) => {
                const currentLine = scopedLineSettings[line.key]
                const existingLine = existingLineLookup[line.key]

                return {
                    ...line,
                    ...existingLine,
                    ...currentLine,
                    key: line.key,
                    label: line.label,
                    columnName: currentLine?.columnName || existingLine?.columnName || line.columnName || '',
                    color: currentLine?.color || existingLine?.color || line.defaultColor,
                    lineWidth: currentLine?.lineWidth || existingLine?.lineWidth || line.defaultLineWidth || 2,
                    target: currentLine?.target || existingLine?.target,
                    paneId: currentLine?.paneId || existingLine?.paneId || '',
                }
            }),
        })

        const nextLineSettings = {
            __scope: lineSettingsScope,
        }

        for (const line of normalizedPreview.lines || []) {
            nextLineSettings[line.key] = {
                key: line.key,
                label: line.label,
                color: line.color,
                lineWidth: line.lineWidth,
                target: normalizeLineTarget(line.target),
                paneId: line.paneId || '',
                columnName: line.columnName || '',
            }
        }

        setLineSettings((previous) => (
            areLineSettingsEqual(previous, nextLineSettings)
                ? previous
                : nextLineSettings
        ))
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [definitionName, indicatorId, alias, values, lineDefinitions, lineSettingsScope])

    if (!definition) {
        return <div className='indicatorEditorEmpty'>Select a feature to configure it.</div>
    }

    function updateValue(fieldKey, nextValue) {
        setValues((previous) => ({
            ...previous,
            [fieldKey]: nextValue,
        }))
    }

    function updateLineSetting(lineKey, patch) {
        setLineSettings((previous) => {
            const currentScope = previous.__scope === lineSettingsScope ? previous.__scope : lineSettingsScope
            const currentLine = previous.__scope === lineSettingsScope ? (previous[lineKey] || {}) : {}
            const nextLine = {
                ...currentLine,
                ...patch,
            }
            const normalizedTarget = normalizeLineTarget(nextLine.target)

            return {
                ...previous,
                __scope: currentScope,
                [lineKey]: {
                    ...nextLine,
                    target: normalizedTarget,
                    paneId: normalizedTarget === 'separate'
                        ? (String(nextLine.paneId || '').trim() || buildDefaultPaneId({
                            alias: alias || definition.name,
                            name: definition.name,
                        }, nextLine))
                        : '',
                },
            }
        })
    }

    function renderField(field) {
        const fieldValue = values[field.key] ?? field.defaultValue ?? ''

        if (field.type === 'select') {
            return (
                <select
                    value={fieldValue}
                    onChange={(event) => updateValue(field.key, event.target.value)}
                >
                    {field.options.map((option) => {
                        const optionValue = typeof option === 'object' ? option.value : option
                        const optionLabel = typeof option === 'object' ? option.label : option

                        return (
                            <option key={optionValue} value={optionValue}>
                                {optionLabel}
                            </option>
                        )
                    })}
                </select>
            )
        }

        return (
            <input
                type={field.type || 'text'}
                min={field.min}
                step={field.step}
                value={fieldValue}
                onChange={(event) => updateValue(field.key, event.target.value)}
            />
        )
    }

    function handleSubmit() {
        if (!normalizedAlias) {
            setError('Alias is required.')
            return
        }

        if (hasDuplicateAlias) {
            setError(`Alias "${normalizedAlias}" already exists.`)
            return
        }

        setError('')

        onSubmit({
            ...(indicator || {}),
            name: definition.name,
            alias: normalizedAlias,
            params: definition.buildParams(values),
            lines: lineDefinitions.map((line) => ({
                ...line,
                ...lineSettings[line.key],
                key: line.key,
                label: line.label,
                columnName: lineSettings[line.key]?.columnName || line.columnName || '',
                color: lineSettings[line.key]?.color || line.defaultColor,
                lineWidth: lineSettings[line.key]?.lineWidth || line.defaultLineWidth || 2,
                target: lineSettings[line.key]?.target,
                paneId: lineSettings[line.key]?.paneId || '',
            })),
        })
    }

    return (
        <div className='indicatorEditor'>
            <div className='indicatorEditorHeader'>
                <div className='indicatorEditorTitle'>{definition.label}</div>

                <div className='indicatorEditorHeaderActions'>
                    {typeof onRemove === 'function' && (
                        <button type='button' className='indicatorEditorButton secondary' onClick={onRemove}>
                            Remove
                        </button>
                    )}

                    <button
                        type='button'
                        className='indicatorEditorButton primary'
                        onClick={handleSubmit}
                        disabled={!normalizedAlias || hasDuplicateAlias}
                    >
                        {submitLabel}
                    </button>
                </div>
            </div>

            <div className='indicatorEditorBody'>
                <div className='indicatorEditorGrid'>
                    <label className='indicatorEditorField'>
                        <span>Alias</span>
                        <input
                            type='text'
                            className={hasDuplicateAlias ? 'isDuplicate' : ''}
                            value={alias}
                            onChange={(event) => setAlias(event.target.value)}
                            placeholder='Unique alias'
                        />
                    </label>

                    {definition.fields.map((field) => (
                        <label className='indicatorEditorField' key={field.key}>
                            <span>{field.label}</span>
                            {renderField(field)}
                        </label>
                    ))}
                </div>

                <div className='indicatorEditorSectionTitle'>Line settings</div>

                <div className='indicatorEditorLines'>
                    {lineDefinitions.map((line) => {
                        const currentLine = lineSettings[line.key] || {}
                        const normalizedTarget = normalizeLineTarget(currentLine.target)

                        return (
                            <div className='indicatorEditorLineCard' key={line.key}>
                            <div className='indicatorEditorLineHead'>
                                <div className='indicatorEditorLineTitle'>
                                    <span
                                        className='indicatorEditorLineDot'
                                        style={{ backgroundColor: currentLine.color || line.defaultColor }}
                                    />
                                    <span>{line.label}</span>
                                </div>
                            </div>

                                <div className='indicatorEditorLineControls'>
                                    <label className='indicatorEditorField small'>
                                        <span>Color</span>
                                        <input
                                            type='color'
                                            value={currentLine.color || line.defaultColor}
                                            onChange={(event) => updateLineSetting(line.key, { color: event.target.value })}
                                        />
                                    </label>

                                    <label className='indicatorEditorField small'>
                                        <span>Width</span>
                                        <select
                                            value={currentLine.lineWidth || line.defaultLineWidth || 2}
                                            onChange={(event) => updateLineSetting(line.key, {
                                                lineWidth: Math.max(1, Math.min(6, Number(event.target.value) || 2)),
                                            })}
                                        >
                                            <option value='1'>1</option>
                                            <option value='2'>2</option>
                                            <option value='3'>3</option>
                                            <option value='4'>4</option>
                                            <option value='5'>5</option>
                                            <option value='6'>6</option>
                                        </select>
                                    </label>

                                    <label className='indicatorEditorField'>
                                        <span>Target</span>
                                        <select
                                            value={normalizedTarget}
                                            onChange={(event) => updateLineSetting(line.key, { target: event.target.value })}
                                        >
                                            <option value='price'>Price</option>
                                            <option value='separate'>Separate</option>
                                            <option value='hidden'>Hidden</option>
                                        </select>
                                    </label>

                                    {normalizedTarget === 'separate' && (
                                        <label className='indicatorEditorField'>
                                            <span>Pane id</span>
                                            <input
                                                type='text'
                                                list='indicator-pane-ids'
                                                value={currentLine.paneId || ''}
                                                onChange={(event) => updateLineSetting(line.key, { paneId: event.target.value })}
                                            />
                                        </label>
                                    )}
                                </div>
                            </div>
                        )
                    })}
                </div>

                {(hasDuplicateAlias || error) && (
                    <div className='indicatorEditorError'>
                        {hasDuplicateAlias
                            ? `Alias "${normalizedAlias}" already exists.`
                            : error}
                    </div>
                )}
            </div>

            <datalist id='indicator-pane-ids'>
                {existingPaneIds.map((paneId) => (
                    <option key={paneId} value={paneId} />
                ))}
            </datalist>
        </div>
    )
}

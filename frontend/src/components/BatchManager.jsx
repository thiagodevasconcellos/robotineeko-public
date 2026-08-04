import { useEffect, useMemo, useState } from 'react'
import './BatchManager.css'

function formatBatchDate(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return '--'
    }
    return new Date(numeric * 1000).toLocaleString()
}

const DEFAULT_RESEARCH_STUDIES = {
    presetCompare: true,
    timeframeStudy: true,
    symbolStudy: true,
    walkforwardStudy: true,
}

function normalizeBatchOptions(options = {}) {
    const nextStudies = {
        ...DEFAULT_RESEARCH_STUDIES,
        ...(options?.researchStudies && typeof options.researchStudies === 'object' ? options.researchStudies : {}),
    }

    if (options?.researchMode === 'none') {
        nextStudies.presetCompare = false
        nextStudies.timeframeStudy = false
        nextStudies.symbolStudy = false
        nextStudies.walkforwardStudy = false
    } else if (options?.researchMode === 'preset_compare') {
        nextStudies.presetCompare = true
        nextStudies.timeframeStudy = false
        nextStudies.symbolStudy = false
        nextStudies.walkforwardStudy = false
    }

    const researchEnabled = Object.values(nextStudies).some(Boolean)

    return {
        barsOverride: null,
        studyWindowsCsv: '',
        studyTimeframesCsv: '',
        studySymbolsCsv: '',
        walkforwardTrainBars: '',
        walkforwardTestBars: '',
        comparisonPresetSelectionMap: {},
        ...(options && typeof options === 'object' ? options : {}),
        researchEnabled,
        researchMode: researchEnabled
            ? (nextStudies.presetCompare && !nextStudies.timeframeStudy && !nextStudies.symbolStudy && !nextStudies.walkforwardStudy ? 'preset_compare' : 'full')
            : 'none',
        researchStudies: nextStudies,
    }
}

function buildResearchSummary(options = {}) {
    const normalized = normalizeBatchOptions(options)
    const studies = normalized.researchStudies || DEFAULT_RESEARCH_STUDIES
    const enabledLabels = []

    if (studies.presetCompare) enabledLabels.push('Preset compare')
    if (studies.timeframeStudy) enabledLabels.push('Timeframe')
    if (studies.symbolStudy) enabledLabels.push('Symbol')
    if (studies.walkforwardStudy) enabledLabels.push('Walk-forward')

    return {
        enabledLabels,
        summaryLabel: enabledLabels.length ? enabledLabels.join(' · ') : 'Backtest only',
        barsOverride: normalized.barsOverride,
        studyWindowsCsv: String(normalized.studyWindowsCsv || '').trim(),
        studyTimeframesCsv: String(normalized.studyTimeframesCsv || '').trim(),
        studySymbolsCsv: String(normalized.studySymbolsCsv || '').trim(),
        walkforwardTrainBars: String(normalized.walkforwardTrainBars || '').trim(),
        walkforwardTestBars: String(normalized.walkforwardTestBars || '').trim(),
    }
}

function getRequestJobs(batchLike) {
    return Array.isArray(batchLike?.request?.batch_jobs)
        ? batchLike.request.batch_jobs
        : Array.isArray(batchLike?.request?.jobs) ? batchLike.request.jobs : []
}

function getSavedBatchJobCount(batchLike) {
    const loadedJobs = getRequestJobs(batchLike).length
    const persistedJobs = Math.max(
        Number(batchLike?.batch_job_count || 0),
        Number(batchLike?.job_count || 0),
    )
    return Math.max(loadedJobs, persistedJobs)
}

function summarizeBatchStructure(batchLike) {
    const jobs = getRequestJobs(batchLike)
    const jobsCount = getSavedBatchJobCount(batchLike)

    if (batchLike?.request_loaded === false) {
        return {
            jobsCount,
            totalStrategies: 0,
            portfolioJobs: 0,
            summaryLabel: jobsCount ? `${jobsCount} programmed jobs` : 'No jobs',
        }
    }

    let totalStrategies = 0
    let portfolioJobs = 0

    for (const job of jobs) {
        const request = job?.request || {}
        const strategies = Array.isArray(request?.strategies) ? request.strategies : []
        const strategyCount = Math.max(1, strategies.length || (request?.strategy ? 1 : 0))
        totalStrategies += strategyCount
        if (strategyCount > 1) {
            portfolioJobs += 1
        }
    }

    return {
        jobsCount,
        totalStrategies,
        portfolioJobs,
        summaryLabel: portfolioJobs > 0
            ? `${portfolioJobs}/${jobsCount || 0} portfolio jobs · ${totalStrategies} strategies`
            : `${totalStrategies} strategies`,
    }
}

function buildMutationSummary(researchPlan = {}) {
    const mutation = researchPlan?.mutation && typeof researchPlan.mutation === 'object'
        ? researchPlan.mutation
        : null
    if (!mutation) {
        return null
    }

    const mode = String(mutation.mutationMode || 'manual').trim() || 'manual'
    const label = String(mutation.mutationLabel || '').trim()
    const preservedAuxiliaries = mutation.preservedAuxiliaries === true
    return {
        mode,
        label,
        summaryLabel: preservedAuxiliaries
            ? `${mode} · preserved auxiliaries${label ? ` · ${label}` : ''}`
            : `${mode}${label ? ` · ${label}` : ''}`,
    }
}

function freezeSignatureValue(value) {
    if (Array.isArray(value)) {
        return value.map((item) => freezeSignatureValue(item))
    }
    if (value && typeof value === 'object') {
        return Object.keys(value)
            .sort()
            .reduce((accumulator, key) => {
                accumulator[key] = freezeSignatureValue(value[key])
                return accumulator
            }, {})
    }
    return value
}

function hashSignature(signature) {
    let hash = 0
    for (let index = 0; index < signature.length; index += 1) {
        hash = ((hash << 5) - hash + signature.charCodeAt(index)) | 0
    }
    return Math.abs(hash).toString(16).padStart(8, '0').slice(0, 8)
}

function buildPortfolioSignatureSummary(request = {}) {
    const strategies = Array.isArray(request?.strategies) ? request.strategies : []
    const strategyCount = Math.max(1, strategies.length || (request?.strategy ? 1 : 0))
    const enabledCount = strategies.length
        ? strategies.filter((entry) => entry?.enabled !== false).length
        : (request?.strategy ? 1 : 0)
    const signature = JSON.stringify(freezeSignatureValue({
        strategy: request?.strategy || {},
        strategies: strategies.map((entry) => ({
            priority: Number(entry?.priority || 0),
            enabled: entry?.enabled !== false,
            allocationMode: String(entry?.allocationMode || 'fixed_volume'),
            allocationValue: entry?.allocationValue ?? null,
            strategy: entry?.strategy || {},
        })),
    }))

    const signatureId = hashSignature(signature)

    return {
        strategyCount,
        enabledCount,
        signatureId,
        summaryLabel: `${strategyCount} strategies · ${enabledCount} enabled · sig ${signatureId}`,
    }
}

function buildLineageSummary(researchPlan = {}) {
    const mutation = researchPlan?.mutation && typeof researchPlan.mutation === 'object'
        ? researchPlan.mutation
        : null
    if (!mutation) {
        return null
    }

    const fragments = []
    if (mutation.parentBatchId !== null && mutation.parentBatchId !== undefined && String(mutation.parentBatchId).trim()) {
        fragments.push(`Parent batch #${String(mutation.parentBatchId).trim()}`)
    }
    if (mutation.parentJobId !== null && mutation.parentJobId !== undefined && String(mutation.parentJobId).trim()) {
        fragments.push(`Parent job #${String(mutation.parentJobId).trim()}`)
    }
    if (String(mutation.mutationTargetStrategyId || '').trim() && String(mutation.mutationTargetStrategyId || '').trim() !== 'primary') {
        fragments.push(`Target ${String(mutation.mutationTargetStrategyId).trim()}`)
    }

    if (!fragments.length) {
        return null
    }

    return {
        summaryLabel: fragments.join(' · '),
    }
}

export function BatchManager({
    batches = [],
    selectedBatchIdOverride = '',
    onSelectedBatchIdChange,
    currentBatchLabel = '',
    currentBatchDescription = '',
    currentJobs = [],
    currentOptions = {},
    onRefresh,
    onCurrentBatchLabelChange,
    onCurrentBatchDescriptionChange,
    onCreateBatch,
    onOverwriteBatch,
    onRenameBatch,
    onLoadBatch,
    onRunBatch,
    onDeleteBatch,
    onCopyBatchJson,
    onImportBatchJson,
    onPasteBatchJsonFromClipboard,
}) {
    const [activeTab, setActiveTab] = useState('save')
    const [selectedBatchIdDraft, setSelectedBatchIdDraft] = useState('')
    const [renameDraft, setRenameDraft] = useState({
        sourceKey: '',
        label: '',
        description: '',
    })
    const [sortMode, setSortMode] = useState('updated')
    const [filterQuery, setFilterQuery] = useState('')
    const [jsonDraft, setJsonDraft] = useState('')

    const sortedBatches = useMemo(() => {
        const query = String(filterQuery || '').trim().toLowerCase()
        const nextBatches = [...batches].filter((entry) => {
            if (!query) {
                return true
            }
            const label = String(entry?.label || '').toLowerCase()
            const description = String(entry?.description || '').toLowerCase()
            return label.includes(query) || description.includes(query)
        })

        if (sortMode === 'alphabetical') {
            nextBatches.sort((left, right) => {
                const byName = String(left?.label || '').localeCompare(String(right?.label || ''), undefined, {
                    sensitivity: 'base',
                })

                if (byName !== 0) {
                    return byName
                }

                return Number(right?.updated_at || 0) - Number(left?.updated_at || 0)
            })
            return nextBatches
        }

        nextBatches.sort((left, right) => Number(right?.updated_at || 0) - Number(left?.updated_at || 0))
        return nextBatches
    }, [batches, filterQuery, sortMode])

    const selectedBatchId = useMemo(() => {
        const overrideId = String(selectedBatchIdOverride || '')
        if (overrideId && sortedBatches.some((entry) => String(entry?.id) === overrideId)) {
            return overrideId
        }
        const currentId = String(selectedBatchIdDraft || '')
        if (currentId && sortedBatches.some((entry) => String(entry?.id) === currentId)) {
            return currentId
        }
        return String(sortedBatches[0]?.id || '')
    }, [selectedBatchIdDraft, selectedBatchIdOverride, sortedBatches])

    useEffect(() => {
        onSelectedBatchIdChange?.(selectedBatchId)
    }, [onSelectedBatchIdChange, selectedBatchId])

    const selectedBatch = useMemo(
        () => sortedBatches.find((entry) => String(entry?.id) === String(selectedBatchId)) || null,
        [selectedBatchId, sortedBatches],
    )
    const renameSourceKey = `${String(selectedBatch?.id || '')}:${String(selectedBatch?.label || '')}:${String(selectedBatch?.description || '')}`
    const renameName = renameDraft.sourceKey === renameSourceKey
        ? renameDraft.label
        : String(selectedBatch?.label || '')
    const renameDescription = renameDraft.sourceKey === renameSourceKey
        ? renameDraft.description
        : String(selectedBatch?.description || '')

    const currentJobsCount = Array.isArray(currentJobs) ? currentJobs.length : 0
    const selectedJobs = getRequestJobs(selectedBatch)
    const currentResearchSummary = buildResearchSummary(currentOptions)
    const selectedResearchSummary = buildResearchSummary(selectedBatch?.request?.options || {})
    const selectedStructureSummary = summarizeBatchStructure(selectedBatch)
    const currentStructureSummary = summarizeBatchStructure({
        request: {
            jobs: currentJobs.map((job) => ({
                request: job,
            })),
        },
    })

    async function handleCreateBatch() {
        const safeLabel = String(currentBatchLabel || '').trim()
        if (!safeLabel) {
            return
        }

        await onCreateBatch?.({
            label: safeLabel,
            description: String(currentBatchDescription || '').trim(),
        })
    }

    async function handleOverwriteBatch() {
        if (!selectedBatch) {
            return
        }

        const shouldOverwrite = window.confirm(`Overwrite saved batch "${selectedBatch.label}" with the currently programmed jobs?`)
        if (!shouldOverwrite) {
            return
        }

        await onOverwriteBatch?.(selectedBatch, {
            label: String(currentBatchLabel || '').trim() || selectedBatch.label,
            description: String(currentBatchDescription || '').trim(),
        })
    }

    async function handleRenameBatch() {
        if (!selectedBatch) {
            return
        }

        const safeLabel = String(renameName || '').trim()
        if (!safeLabel) {
            return
        }

        await onRenameBatch?.(selectedBatch, {
            label: safeLabel,
            description: String(renameDescription || '').trim(),
        })
    }

    return (
        <div className='batchManagerShell'>
            <div className='batchManagerLayout'>
                <aside className='batchManagerSidebar'>
                    <div className='batchManagerTabs'>
                        <button
                            type='button'
                            className={activeTab === 'save' ? 'active' : ''}
                            onClick={() => setActiveTab('save')}
                        >
                            Save
                        </button>
                        <button
                            type='button'
                            className={activeTab === 'load' ? 'active' : ''}
                            onClick={() => setActiveTab('load')}
                        >
                            Load
                        </button>
                    </div>

                    <div className='batchManagerToolbar'>
                        <div className='batchManagerSortControl'>
                            <label htmlFor='batchManagerSortMode'>Sort</label>
                            <select
                                id='batchManagerSortMode'
                                value={sortMode}
                                onChange={(event) => setSortMode(event.target.value)}
                            >
                                <option value='updated'>Updated</option>
                                <option value='alphabetical'>A-Z</option>
                            </select>
                        </div>
                        <div className='batchManagerFilterControl'>
                            <label htmlFor='batchManagerFilterQuery'>Filter</label>
                            <input
                                id='batchManagerFilterQuery'
                                type='text'
                                value={filterQuery}
                                onChange={(event) => setFilterQuery(event.target.value)}
                                placeholder='Batch name'
                            />
                        </div>
                        <button type='button' onClick={() => void onRefresh?.()}>
                            Refresh
                        </button>
                    </div>

                    <div className='batchManagerList'>
                        {!sortedBatches.length ? (
                            <div className='batchManagerEmpty'>No saved batches yet.</div>
                        ) : sortedBatches.map((batch) => {
                            const isActive = String(batch?.id) === String(selectedBatchId)
                            const structureSummary = summarizeBatchStructure(batch)
                            const researchSummary = buildResearchSummary(batch?.request?.options || {})

                            return (
                                <button
                                    key={batch?.id}
                                    type='button'
                                    className={isActive ? 'active' : ''}
                                    onClick={() => {
                                        const nextId = String(batch?.id || '')
                                        setSelectedBatchIdDraft(nextId)
                                    }}
                                >
                                    <span className='batchManagerListHeader'>
                                        <span className='batchManagerListName'>{batch?.label || 'Saved batch'}</span>
                                        <span className='batchManagerCountBadge'>{structureSummary.jobsCount}</span>
                                    </span>
                                    <span className='batchManagerListMeta'>{structureSummary.summaryLabel}</span>
                                    <span className='batchManagerListMeta'>{researchSummary.summaryLabel}</span>
                                    <span className='batchManagerListDate'>{formatBatchDate(batch?.updated_at)}</span>
                                </button>
                            )
                        })}
                    </div>
                </aside>

                <section className='batchManagerContent'>
                    {activeTab === 'save' && (
                        <div className='batchManagerPanel'>
                            <div className='batchManagerPanelTitle'>Save batch configuration</div>

                            <div className='batchManagerField'>
                                <label htmlFor='batchManagerCurrentName'>Batch name</label>
                                <input
                                    id='batchManagerCurrentName'
                                    type='text'
                                    value={currentBatchLabel}
                                    onChange={(event) => onCurrentBatchLabelChange?.(event.target.value)}
                                    placeholder='Batch template'
                                />
                            </div>

                            <div className='batchManagerField'>
                                <label htmlFor='batchManagerCurrentDescription'>Description</label>
                                <input
                                    id='batchManagerCurrentDescription'
                                    type='text'
                                    value={currentBatchDescription}
                                    onChange={(event) => onCurrentBatchDescriptionChange?.(event.target.value)}
                                    placeholder='Optional'
                                />
                            </div>

                            <div className='batchManagerDetailGrid'>
                                <div className='batchManagerDetailLabel'>Programmed jobs</div>
                                <div>{currentJobsCount}</div>
                                <div className='batchManagerDetailLabel'>Strategies in jobs</div>
                                <div>{currentStructureSummary.totalStrategies}</div>
                                <div className='batchManagerDetailLabel'>Portfolio jobs</div>
                                <div>{currentStructureSummary.portfolioJobs}</div>
                                <div className='batchManagerDetailLabel'>What gets saved</div>
                                <div>All programmed strategies, backtests and post-backtest research plans exactly as currently configured.</div>
                                <div className='batchManagerDetailLabel'>Studies</div>
                                <div>{currentResearchSummary.summaryLabel}</div>
                                <div className='batchManagerDetailLabel'>Bars override</div>
                                <div>{currentResearchSummary.barsOverride ? String(currentResearchSummary.barsOverride) : 'None'}</div>
                                <div className='batchManagerDetailLabel'>Overwrite target</div>
                                <div>{selectedBatch?.label || 'Select a saved batch on the left to overwrite it.'}</div>
                            </div>

                            <div className='batchManagerActions'>
                                <button
                                    type='button'
                                    className='batchManagerPrimary'
                                    onClick={() => void handleCreateBatch()}
                                    disabled={!String(currentBatchLabel || '').trim() || !currentJobsCount}
                                >
                                    Save batch to database
                                </button>
                                <button
                                    type='button'
                                    onClick={() => void handleOverwriteBatch()}
                                    disabled={!selectedBatch || !currentJobsCount}
                                >
                                    Overwrite selected batch
                                </button>
                            </div>
                        </div>
                    )}

                    {activeTab === 'load' && (
                        <div className='batchManagerPanel'>
                            <div className='batchManagerPanelTitle'>Load saved batch</div>

                            {!selectedBatch ? (
                                <div className='batchManagerEmptyState'>
                                    Save a batch first to restore it later.
                                </div>
                            ) : (
                                <>
                                    <div className='batchManagerDetailGrid'>
                                        <div className='batchManagerDetailLabel'>Name</div>
                                        <div>{selectedBatch.label || 'Saved batch'}</div>
                                        <div className='batchManagerDetailLabel'>Updated</div>
                                        <div>{formatBatchDate(selectedBatch.updated_at)}</div>
                                        <div className='batchManagerDetailLabel'>Jobs</div>
                                        <div>{selectedStructureSummary.jobsCount}</div>
                                        <div className='batchManagerDetailLabel'>Strategies in jobs</div>
                                        <div>{selectedBatch?.request_loaded === false ? 'Loading...' : selectedStructureSummary.totalStrategies}</div>
                                        <div className='batchManagerDetailLabel'>Portfolio jobs</div>
                                        <div>{selectedBatch?.request_loaded === false ? 'Loading...' : selectedStructureSummary.portfolioJobs}</div>
                                        <div className='batchManagerDetailLabel'>Studies</div>
                                        <div>{selectedResearchSummary.summaryLabel}</div>
                                        <div className='batchManagerDetailLabel'>Bars override</div>
                                        <div>{selectedResearchSummary.barsOverride ? String(selectedResearchSummary.barsOverride) : 'None'}</div>
                                        <div className='batchManagerDetailLabel'>Description</div>
                                        <div>{selectedBatch.description || 'No description provided.'}</div>
                                    </div>

                                    <div className='batchManagerSection'>
                                        <div className='batchManagerSectionTitle'>Research summary</div>
                                        <div className='batchManagerDetailGrid'>
                                            <div className='batchManagerDetailLabel'>Enabled studies</div>
                                            <div>{selectedResearchSummary.summaryLabel}</div>
                                            <div className='batchManagerDetailLabel'>Study windows</div>
                                            <div>{selectedResearchSummary.studyWindowsCsv || 'Default per job'}</div>
                                            <div className='batchManagerDetailLabel'>Study timeframes</div>
                                            <div>{selectedResearchSummary.studyTimeframesCsv || 'Default per job'}</div>
                                            <div className='batchManagerDetailLabel'>Study symbols</div>
                                            <div>{selectedResearchSummary.studySymbolsCsv || 'Default per job'}</div>
                                            <div className='batchManagerDetailLabel'>Walk-forward train/test</div>
                                            <div>
                                                {selectedResearchSummary.walkforwardTrainBars || 'Default'}
                                                {' / '}
                                                {selectedResearchSummary.walkforwardTestBars || 'Default'}
                                            </div>
                                        </div>
                                    </div>

                                    <div className='batchManagerField'>
                                        <label htmlFor='batchManagerRenameName'>Rename saved batch</label>
                                        <input
                                            id='batchManagerRenameName'
                                            type='text'
                                            value={renameName}
                                            onChange={(event) => setRenameDraft({
                                                sourceKey: renameSourceKey,
                                                label: event.target.value,
                                                description: renameDescription,
                                            })}
                                            placeholder='Saved batch name'
                                        />
                                    </div>

                                    <div className='batchManagerField'>
                                        <label htmlFor='batchManagerRenameDescription'>Saved batch description</label>
                                        <input
                                            id='batchManagerRenameDescription'
                                            type='text'
                                            value={renameDescription}
                                            onChange={(event) => setRenameDraft({
                                                sourceKey: renameSourceKey,
                                                label: renameName,
                                                description: event.target.value,
                                            })}
                                            placeholder='Optional'
                                        />
                                    </div>

                                    <div className='batchManagerActions'>
                                        <button
                                            type='button'
                                            onClick={() => void handleRenameBatch()}
                                            disabled={
                                                !String(renameName || '').trim()
                                                || (renameName.trim() === String(selectedBatch.label || '').trim()
                                                    && renameDescription.trim() === String(selectedBatch.description || '').trim())
                                            }
                                        >
                                            Rename
                                        </button>
                                        <button
                                            type='button'
                                            className='batchManagerPrimary'
                                            onClick={() => onLoadBatch?.(selectedBatch)}
                                        >
                                            Load batch
                                        </button>
                                        <button
                                            type='button'
                                            onClick={() => void onRunBatch?.(selectedBatch?.id, selectedBatch)}
                                        >
                                            Run batch
                                        </button>
                                        <button
                                            type='button'
                                            className='batchManagerDanger'
                                            onClick={() => void onDeleteBatch?.(selectedBatch?.id)}
                                        >
                                            Delete batch
                                        </button>
                                        <button
                                            type='button'
                                            onClick={() => void onCopyBatchJson?.(selectedBatch)}
                                        >
                                            Copy JSON
                                        </button>
                                    </div>

                                    <div className='batchManagerSection'>
                                        <div className='batchManagerSectionTitle'>Programmed jobs</div>
                                        <div className='batchManagerPreviewList'>
                                            {selectedBatch?.request_loaded === false ? (
                                                <div className='batchManagerEmpty'>Loading saved batch details...</div>
                                            ) : selectedJobs.length ? selectedJobs.map((job, index) => (
                                                <div key={`saved-batch-job-${selectedBatch?.id}-${index}`} className='batchManagerPreviewItem'>
                                                    {(() => {
                                                        const mutationSummary = buildMutationSummary(job?.request?.researchPlan)
                                                        const lineageSummary = buildLineageSummary(job?.request?.researchPlan)
                                                        const signatureSummary = buildPortfolioSignatureSummary(job?.request || {})

                                                        return (
                                                            <>
                                                                <strong>{job?.run_label || job?.request?.label || `Job ${index + 1}`}</strong>
                                                                <span>{job?.request?.chart?.symbol || '--'} · {job?.request?.chart?.timeframe || '--'}</span>
                                                                <span>{Array.isArray(job?.request?.strategies) && job.request.strategies.length > 1 ? `${job.request.strategies.length} strategies` : 'Single strategy'}</span>
                                                                <small>{job?.request?.researchPlan?.kind || 'none'} · {job?.request?.chart?.bars || '--'} bars</small>
                                                                {mutationSummary?.summaryLabel ? (
                                                                    <small className='batchManagerMutationMeta'>{mutationSummary.summaryLabel}</small>
                                                                ) : null}
                                                                {lineageSummary?.summaryLabel ? (
                                                                    <small className='batchManagerLineageMeta'>{lineageSummary.summaryLabel}</small>
                                                                ) : null}
                                                                <small className='batchManagerSignatureMeta'>{signatureSummary.summaryLabel}</small>
                                                            </>
                                                        )
                                                    })()}
                                                </div>
                                            )) : (
                                                <div className='batchManagerEmpty'>This saved batch does not have jobs.</div>
                                            )}
                                        </div>
                                    </div>

                                    <div className='batchManagerSection'>
                                        <div className='batchManagerSectionTitle'>JSON integration</div>
                                        <div className='batchManagerMeta'>
                                            Keep JSON only as a flexible fallback. The main batch workflow should stay visual.
                                        </div>
                                        <textarea
                                            className='batchManagerTextarea'
                                            value={jsonDraft}
                                            onChange={(event) => setJsonDraft(event.target.value)}
                                            placeholder='Paste a batch JSON wrapper or an array of jobs.'
                                        />
                                        <div className='batchManagerActions'>
                                            <button type='button' onClick={() => onImportBatchJson?.(jsonDraft)}>
                                                Load JSON into Batch
                                            </button>
                                            <button
                                                type='button'
                                                onClick={async () => {
                                                    const nextText = await onPasteBatchJsonFromClipboard?.()
                                                    if (typeof nextText === 'string') {
                                                        setJsonDraft(nextText)
                                                    }
                                                }}
                                            >
                                                Paste from clipboard
                                            </button>
                                        </div>
                                    </div>
                                </>
                            )}
                        </div>
                    )}
                </section>
            </div>
        </div>
    )
}

export default BatchManager

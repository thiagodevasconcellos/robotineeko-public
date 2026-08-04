import { useMemo, useState } from 'react'
import './WorkspaceManager.css'

function formatSaveDate(value) {
    if (!value) {
        return '--'
    }

    try {
        return new Date(value * 1000).toLocaleString()
    } catch {
        return '--'
    }
}

function formatSaveScore(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return '--'
    }

    return numeric.toFixed(1)
}

function getScoreTone(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) {
        return 'unknown'
    }

    if (numeric >= 7.5) {
        return 'positive'
    }

    if (numeric >= 5) {
        return 'warning'
    }

    return 'negative'
}

export function WorkspaceManager({
    isOpen,
    mode = 'load',
    saves = [],
    isLoading = false,
    isSaving = false,
    isDeleting = false,
    isRenaming = false,
    defaultSaveName = '',
    highlightedSaveId = '',
    onClose,
    onRefresh,
    onCreateSave,
    onOverwriteSave,
    onRestoreSave,
    onDeleteSave,
    onRenameSave,
}) {
    const requestedTab = mode === 'save' ? 'save' : 'load'
    const [activeTabDraft, setActiveTabDraft] = useState({
        sourceTab: requestedTab,
        value: requestedTab,
    })
    const [saveNameDraft, setSaveNameDraft] = useState({
        sourceValue: String(defaultSaveName || ''),
        value: String(defaultSaveName || ''),
    })
    const [selectedSaveIdDraft, setSelectedSaveIdDraft] = useState('')
    const [renameNameDraft, setRenameNameDraft] = useState({
        sourceKey: '',
        value: '',
    })
    const [sortMode, setSortMode] = useState('score')
    const activeTab = activeTabDraft.sourceTab === requestedTab ? activeTabDraft.value : requestedTab
    const saveName = saveNameDraft.sourceValue === String(defaultSaveName || '')
        ? saveNameDraft.value
        : String(defaultSaveName || '')

    const sortedSaves = useMemo(() => {
        const nextSaves = [...saves]

        if (sortMode === 'alphabetical') {
            nextSaves.sort((left, right) => {
                const byName = String(left?.name || '').localeCompare(String(right?.name || ''), undefined, {
                    sensitivity: 'base',
                })

                if (byName !== 0) {
                    return byName
                }

                return Number(right?.created_at || 0) - Number(left?.created_at || 0)
            })
            return nextSaves
        }

        nextSaves.sort((left, right) => {
            const leftScore = Number(left?.score)
            const rightScore = Number(right?.score)
            const leftHasScore = Number.isFinite(leftScore)
            const rightHasScore = Number.isFinite(rightScore)

            if (leftHasScore && rightHasScore && rightScore !== leftScore) {
                return rightScore - leftScore
            }

            if (leftHasScore !== rightHasScore) {
                return leftHasScore ? -1 : 1
            }

            return Number(right?.created_at || 0) - Number(left?.created_at || 0)
        })

        return nextSaves
    }, [saves, sortMode])

    const selectedSaveId = useMemo(() => {
        const currentId = String(selectedSaveIdDraft || '')
        if (currentId && sortedSaves.some((save) => String(save.id) === currentId)) {
            return currentId
        }
        return String(sortedSaves[0]?.id || '')
    }, [selectedSaveIdDraft, sortedSaves])
    const selectedSave = useMemo(
        () => sortedSaves.find((save) => String(save.id) === String(selectedSaveId)) || null,
        [sortedSaves, selectedSaveId]
    )
    const renameSourceKey = `${String(selectedSave?.id || '')}:${String(selectedSave?.name || '')}`
    const renameName = renameNameDraft.sourceKey === renameSourceKey
        ? renameNameDraft.value
        : String(selectedSave?.name || '')

    if (!isOpen) {
        return null
    }

    async function handleCreateSave() {
        const safeName = String(saveName || '').trim()
        if (!safeName) {
            return
        }

        await onCreateSave?.(safeName)
    }

    async function handleOverwriteSave() {
        if (!selectedSave) {
            return
        }

        const shouldOverwrite = window.confirm(`Overwrite project snapshot "${selectedSave.name}"?`)
        if (!shouldOverwrite) {
            return
        }

        await onOverwriteSave?.(selectedSave, selectedSave.name)
    }

    async function handleRestoreSave() {
        if (!selectedSave) {
            return
        }

        await onRestoreSave?.(selectedSave)
    }

    async function handleDeleteSave() {
        if (!selectedSave) {
            return
        }

        const shouldDelete = window.confirm(`Delete project snapshot "${selectedSave.name}"?`)
        if (!shouldDelete) {
            return
        }

        await onDeleteSave?.(selectedSave)
    }

    async function handleRenameSave() {
        if (!selectedSave) {
            return
        }

        const safeName = String(renameName || '').trim()
        if (!safeName) {
            return
        }

        await onRenameSave?.(selectedSave, safeName)
    }

    return (
        <div className='overlayContainer workspaceManagerOverlay'>
            <div className='fog' onClick={onClose} />

            <div className='overlay workspaceManagerWindow'>
                <button type='button' className='closeOverlay' onClick={onClose}>
                    x
                </button>

                <div className='workspaceManagerLayout'>
                    <aside className='workspaceManagerSidebar'>
                        <div className='workspaceManagerTabs'>
                            <button
                                type='button'
                                className={activeTab === 'save' ? 'active' : ''}
                                onClick={() => setActiveTabDraft({
                                    sourceTab: requestedTab,
                                    value: 'save',
                                })}
                            >
                                Save
                            </button>

                            <button
                                type='button'
                                className={activeTab === 'load' ? 'active' : ''}
                                onClick={() => setActiveTabDraft({
                                    sourceTab: requestedTab,
                                    value: 'load',
                                })}
                            >
                                Load
                            </button>
                        </div>

                        <div className='workspaceManagerToolbar'>
                            <div className='workspaceManagerSortControl'>
                                <label htmlFor='workspaceSaveSortMode'>Sort</label>
                                <select
                                    id='workspaceSaveSortMode'
                                    value={sortMode}
                                    onChange={(event) => setSortMode(event.target.value)}
                                >
                                    <option value='score'>Last score</option>
                                    <option value='alphabetical'>A-Z</option>
                                </select>
                            </div>
                            <button type='button' onClick={() => void onRefresh?.()} disabled={isLoading}>
                                Refresh
                            </button>
                        </div>

                        <div className='workspaceManagerList'>
                            {!isLoading && saves.length === 0 && (
                                <div className='workspaceManagerEmpty'>
                                    No project snapshots yet.
                                </div>
                            )}

                            {sortedSaves.map((save) => (
                                <button
                                    type='button'
                                    key={save.id}
                                    className={[
                                        String(selectedSaveId) === String(save.id) ? 'active' : '',
                                        String(highlightedSaveId) === String(save.id) ? 'highlighted' : '',
                                    ].filter(Boolean).join(' ')}
                                    onClick={() => setSelectedSaveIdDraft(String(save.id))}
                                >
                                    <span className='workspaceManagerListHeader'>
                                        <span className='workspaceManagerListName'>{save.name}</span>
                                        <span className={`workspaceManagerScoreBadge is-${getScoreTone(save.score)}`}>
                                            {formatSaveScore(save.score)}
                                        </span>
                                    </span>
                                    <span className='workspaceManagerListDate'>{formatSaveDate(save.created_at)}</span>
                                </button>
                            ))}
                        </div>
                    </aside>

                    <section className='workspaceManagerContent'>
                        {activeTab === 'save' && (
                            <div className='workspaceManagerPanel'>
                                <div className='workspaceManagerPanelTitle'>Create project snapshot</div>
                                <div className='workspaceManagerField'>
                                    <label htmlFor='workspaceSaveName'>Project snapshot name</label>
                                    <input
                                        id='workspaceSaveName'
                                        type='text'
                                        value={saveName}
                                        onChange={(event) => setSaveNameDraft({
                                            sourceValue: String(defaultSaveName || ''),
                                            value: event.target.value,
                                        })}
                                        placeholder='Project snapshot name'
                                    />
                                </div>
                                {selectedSave && (
                                    <div className='workspaceManagerDetailGrid'>
                                        <div className='workspaceManagerDetailLabel'>Selected snapshot</div>
                                        <div>{selectedSave.name}</div>
                                        <div className='workspaceManagerDetailLabel'>Created</div>
                                        <div>{formatSaveDate(selectedSave.created_at)}</div>
                                        <div className='workspaceManagerDetailLabel'>Last score</div>
                                        <div>{formatSaveScore(selectedSave.score)}</div>
                                    </div>
                                )}
                                <div className='workspaceManagerActions'>
                                    <button
                                        type='button'
                                        className='workspaceManagerPrimary'
                                        onClick={() => void handleCreateSave()}
                                        disabled={isSaving || !String(saveName || '').trim()}
                                    >
                                        {isSaving ? 'Saving...' : 'Save project to database'}
                                    </button>
                                    <button
                                        type='button'
                                        onClick={() => void handleOverwriteSave()}
                                        disabled={isSaving || !selectedSave}
                                    >
                                        {isSaving ? 'Overwriting...' : 'Overwrite selected snapshot'}
                                    </button>
                                </div>
                            </div>
                        )}

                        {activeTab === 'load' && (
                            <div className='workspaceManagerPanel'>
                                <div className='workspaceManagerPanelTitle'>Restore project snapshot</div>

                                {!selectedSave && !isLoading && (
                                    <div className='workspaceManagerEmptyState'>
                                        Save a project snapshot first to restore it later.
                                    </div>
                                )}

                                {selectedSave && (
                                    <>
                                        <div className='workspaceManagerDetailGrid'>
                                            <div className='workspaceManagerDetailLabel'>Name</div>
                                            <div>{selectedSave.name}</div>
                                            <div className='workspaceManagerDetailLabel'>Created</div>
                                            <div>{formatSaveDate(selectedSave.created_at)}</div>
                                            <div className='workspaceManagerDetailLabel'>Last score</div>
                                            <div>{formatSaveScore(selectedSave.score)}</div>
                                        </div>

                                        <div className='workspaceManagerField'>
                                            <label htmlFor='workspaceRenameName'>Rename snapshot</label>
                                            <input
                                                id='workspaceRenameName'
                                                type='text'
                                                value={renameName}
                                                onChange={(event) => setRenameNameDraft({
                                                    sourceKey: renameSourceKey,
                                                    value: event.target.value,
                                                })}
                                                placeholder='Project snapshot name'
                                            />
                                        </div>

                                        <div className='workspaceManagerActions'>
                                            <button
                                                type='button'
                                                onClick={() => void handleRenameSave()}
                                                disabled={isRenaming || !String(renameName || '').trim() || renameName.trim() === selectedSave.name}
                                            >
                                                {isRenaming ? 'Renaming...' : 'Rename'}
                                            </button>

                                            <button
                                                type='button'
                                                className='workspaceManagerPrimary'
                                                onClick={() => void handleRestoreSave()}
                                                disabled={isLoading}
                                            >
                                                Restore snapshot
                                            </button>

                                            <button
                                                type='button'
                                                className='workspaceManagerDanger'
                                                onClick={() => void handleDeleteSave()}
                                                disabled={isDeleting}
                                            >
                                                {isDeleting ? 'Deleting...' : 'Delete snapshot'}
                                            </button>
                                        </div>
                                    </>
                                )}
                            </div>
                        )}
                    </section>
                </div>
            </div>
        </div>
    )
}

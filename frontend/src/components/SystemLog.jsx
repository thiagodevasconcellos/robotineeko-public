import { useEffect, useRef, useState } from 'react'
import './SystemLog.css'

function formatSessionTime(value) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return ''
    }

    try {
        return new Date(numeric).toLocaleString()
    } catch {
        return ''
    }
}

function formatSessionStatus(status = '') {
    const normalized = String(status || '').trim().toLowerCase()
    if (normalized === 'archived') {
        return 'Archived'
    }
    if (normalized === 'local') {
        return 'Local only'
    }
    return 'Active'
}

export function SystemLog({
    entries = [],
    activeSession = null,
    isLoading = false,
    onHeightChange,
    onStartNewLog,
}) {
    const [panelHeight, setPanelHeight] = useState(88)
    const resizeStateRef = useRef(null)
    const bodyRef = useRef(null)
    const entryCount = Number(activeSession?.entryCount ?? entries.length ?? 0)

    useEffect(() => {
        function handlePointerMove(event) {
            if (!resizeStateRef.current) {
                return
            }

            const nextHeight = Math.max(
                72,
                Math.min(window.innerHeight - 120, window.innerHeight - event.clientY)
            )

            setPanelHeight(nextHeight)
        }

        function handlePointerUp() {
            resizeStateRef.current = null
            document.body.style.userSelect = ''
            document.body.style.cursor = ''
        }

        window.addEventListener('pointermove', handlePointerMove)
        window.addEventListener('pointerup', handlePointerUp)

        return () => {
            window.removeEventListener('pointermove', handlePointerMove)
            window.removeEventListener('pointerup', handlePointerUp)
        }
    }, [])

    useEffect(() => {
        if (!bodyRef.current) {
            return
        }

        bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }, [entries])

    useEffect(() => {
        onHeightChange?.(panelHeight)
    }, [onHeightChange, panelHeight])

    function handleResizeStart(event) {
        resizeStateRef.current = { pointerId: event.pointerId }
        document.body.style.userSelect = 'none'
        document.body.style.cursor = 'ns-resize'
    }

    async function handleCopyEntry(entry) {
        const message = entry?.message || String(entry || '')
        const text = [entry?.timestamp, message].filter(Boolean).join('  ')

        try {
            await navigator.clipboard.writeText(text)
        } catch (error) {
            console.error('Failed to copy log entry:', error)
        }
    }

    async function handleCopyEntriesFromIndex(startIndex) {
        const relevantEntries = entries.slice(Math.max(0, Number(startIndex) || 0))
        const text = relevantEntries
            .map((entry) => [entry?.timestamp, entry?.message || String(entry || '')].filter(Boolean).join('  '))
            .join('\n')

        if (!text.trim()) {
            return
        }

        try {
            await navigator.clipboard.writeText(text)
        } catch (error) {
            console.error('Failed to copy log entries from index:', error)
        }
    }

    async function handleCopyAllErrors() {
        const errorEntries = entries.filter((entry) => String(entry?.level || '').toLowerCase() === 'error')
        const text = errorEntries
            .map((entry) => [entry?.timestamp, entry?.message || String(entry || '')].filter(Boolean).join('  '))
            .join('\n')

        if (!text.trim()) {
            return
        }

        try {
            await navigator.clipboard.writeText(text)
        } catch (error) {
            console.error('Failed to copy error log entries:', error)
        }
    }

    async function handleCopyAllMessages() {
        const text = entries
            .map((entry) => [entry?.timestamp, entry?.message || String(entry || '')].filter(Boolean).join('  '))
            .join('\n')

        if (!text.trim()) {
            return
        }

        try {
            await navigator.clipboard.writeText(text)
        } catch (error) {
            console.error('Failed to copy all log entries:', error)
        }
    }

    return (
        <section
            id='SystemLog'
            style={{ height: panelHeight }}
        >
            <div className='systemLogToolbar'>
                <div className='systemLogMeta'>
                    <div className='systemLogMetaTitleRow'>
                        <span className='systemLogMetaTitle'>System Log</span>
                        <span className='systemLogMetaCountLabel'>{entryCount} entries</span>
                        {activeSession?.label ? (
                            <span className='systemLogMetaLabel'>{activeSession.label}</span>
                        ) : null}
                        {activeSession ? (
                            <span className={`systemLogMetaBadge is-${activeSession?.status || 'active'}`}>
                                {formatSessionStatus(activeSession?.status)}
                            </span>
                        ) : null}
                    </div>
                    <div className='systemLogMetaRow'>
                        {activeSession?.createdAt ? (
                            <span>Started {formatSessionTime(activeSession.createdAt)}</span>
                        ) : null}
                        {activeSession?.lastEntryAt ? (
                            <span>Last entry {formatSessionTime(activeSession.lastEntryAt)}</span>
                        ) : null}
                        {isLoading ? (
                            <span>Loading persisted log...</span>
                        ) : null}
                    </div>
                </div>

                <div className='systemLogActions'>
                    <button
                        type='button'
                        className='systemLogActionButton'
                        onClick={handleCopyAllMessages}
                        aria-label='Copy all log messages'
                        title='Copy all messages'
                    >
                        Copy all messages
                    </button>

                    <button
                        type='button'
                        className='systemLogActionButton'
                        onClick={handleCopyAllErrors}
                        aria-label='Copy all error log entries'
                        title='Copy all errors'
                    >
                        Copy all errors
                    </button>

                    <button
                        type='button'
                        className='systemLogActionButton systemLogStart'
                        onClick={() => onStartNewLog?.()}
                        aria-label='Clear the current log by starting a new log session'
                        title='Clear log and start a new session'
                        disabled={isLoading}
                    >
                        Clear log
                    </button>
                </div>
            </div>

            <div
                className='resizeHandle'
                onPointerDown={handleResizeStart}
                title='Drag to resize the system log'
                aria-hidden='true'
            />

            <div ref={bodyRef} className='systemLogBody'>
                {entries.length === 0 ? (
                    <div className='systemLogEmpty'>
                        {isLoading ? 'Loading persisted log...' : 'Waiting for log messages...'}
                    </div>
                ) : (
                    entries.map((entry, index) => (
                        <div
                            key={entry.id || index}
                            className={`systemLogEntry is-${entry.level || 'info'}`}
                        >
                            <button
                                type='button'
                                className='systemLogEntryCopy'
                                onClick={() => handleCopyEntry(entry)}
                                aria-label='Copy log line'
                                title='Copy log line'
                            >
                                ⧉
                            </button>
                            <button
                                type='button'
                                className='systemLogEntryCopy'
                                onClick={() => handleCopyEntriesFromIndex(index)}
                                aria-label='Copy from this line down'
                                title='Copy from this line down'
                            >
                                ↓
                            </button>
                            <span className='systemLogEntryTimestamp'>
                                {entry.timestamp || ''}
                            </span>
                            <span className='systemLogEntryMessage'>
                                {entry.message || String(entry)}
                            </span>
                        </div>
                    ))
                )}
            </div>
        </section>
    )
}

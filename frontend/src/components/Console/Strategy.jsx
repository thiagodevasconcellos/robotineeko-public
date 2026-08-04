import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import {
    buildBackendIndicatorsPayload,
} from '../../utils/chartSettings.jsx'
import {
    buildStrategyAliasContextChartSettings,
    getStrategyManifestIndicators,
    getStrategyTokenGroups,
    getStrategyTokenCandidates,
    migrateStrategyFeatureNamesToAliases,
    resolveStrategyAliasesInStrategy,
} from '../../utils/strategyAliases.jsx'
import {
    buildMarketRegimePresetModel,
    buildMarketRegimePresetRecommendation,
    buildStrategyFromMarketRegimePreset,
    formatPresetMetric,
    MARKET_REGIME_DOC_ITEMS,
} from '../../utils/marketRegimePresets.jsx'
import {
    buildElliottWavePresetModel,
    buildStrategyFromElliottWavePreset,
    ELLIOTT_WAVE_DOC_ITEMS,
} from '../../utils/elliottWavePresets.jsx'
import {
    attachStrategyFeatureManifest,
    buildStrategyBenchmarkPayload,
    buildStrategyCollectionChartSettings,
    normalizeStrategyFeatureManifest,
} from '../../utils/strategyLibrary.js'
import { buildBrokerProfileQuery } from '../../utils/brokerProfiles.js'
import { BACKTEST_DEFAULTS } from './backtestDefaults.js'
import {
    mergeBacktestCostProfileValues,
    normalizeBacktestCostProfile,
} from './backtestCostProfiles.js'
import './Strategy.css'

const STRATEGY_LIBRARY_FETCH_LIMIT = 500
const STRATEGY_DEBUG_SOURCE = 'strategy_debug'

const TOKEN_REGEX = /\b([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]/g
const LITERAL_TOKEN_REGEX = /\b(True|False|and|or)\b/g
const IDENTIFIER_REGEX = /[A-Za-z0-9_]/
const RESERVED_IDENTIFIERS = new Set(['True', 'False', 'and', 'or', 'not'])
const LITERAL_TOKENS = new Set(['True', 'False', 'and', 'or'])

function resolveStrategyChartRequestBars(chartSettings) {
    return Math.max(1, Number(chartSettings?.bars) || 1)
}

function normalizePythonBooleanLiterals(value) {
    return String(value ?? '')
        .replace(/\btrue\b/g, 'True')
        .replace(/\bfalse\b/g, 'False')
        .replace(/\band\b/gi, 'and')
        .replace(/\bor\b/gi, 'or')
}

function mergeChartIndicatorsWithInferred(chartSettings, strategy) {
    return buildStrategyAliasContextChartSettings(chartSettings, strategy, getStrategyManifestIndicators(strategy))
}

function normalizeStrategyDebugSession(session) {
    if (!session || typeof session !== 'object') {
        return null
    }

    const id = Number(session.id || 0)
    if (!Number.isFinite(id) || id <= 0) {
        return null
    }

    return {
        id,
        label: String(session.label || '').trim() || 'Strategy debug',
        status: String(session.status || '').trim() || 'debug',
        source: String(session.source || '').trim() || STRATEGY_DEBUG_SOURCE,
        metadata: session.metadata && typeof session.metadata === 'object' ? session.metadata : {},
        createdAt: Number(session.created_at ?? session.createdAt ?? 0) || 0,
        updatedAt: Number(session.updated_at ?? session.updatedAt ?? 0) || 0,
        lastEntryAt: Number(session.last_entry_at ?? session.lastEntryAt ?? 0) || 0,
        entryCount: Number(session.entry_count ?? session.entryCount ?? 0) || 0,
    }
}

function normalizeStrategyDebugEntry(entry) {
    if (!entry || typeof entry !== 'object') {
        return null
    }

    const createdAt = Number(entry.created_at ?? entry.createdAt ?? 0) || Date.now()
    return {
        id: String(entry.id || entry.client_entry_id || entry.clientEntryId || `${createdAt}:${entry.message || ''}`),
        persistedId: Number(entry.id || 0) || null,
        clientEntryId: String(entry.client_entry_id || entry.clientEntryId || '').trim(),
        createdAt,
        message: String(entry.message || '').trim(),
        level: String(entry.level || '').trim() || 'info',
        source: String(entry.source || '').trim() || STRATEGY_DEBUG_SOURCE,
        scope: String(entry.scope || '').trim() || 'strategy_debug',
        category: String(entry.category || '').trim() || 'operator',
        context: entry.context && typeof entry.context === 'object' ? entry.context : {},
    }
}

function mergeStrategyDebugEntries(currentEntries = [], incomingEntries = []) {
    const merged = new Map()

    ;[...(Array.isArray(currentEntries) ? currentEntries : []), ...(Array.isArray(incomingEntries) ? incomingEntries : [])]
        .map((entry) => normalizeStrategyDebugEntry(entry))
        .filter(Boolean)
        .forEach((entry) => {
            const key = entry.clientEntryId
                || (entry.persistedId ? `persisted:${entry.persistedId}` : '')
                || entry.id
            const existing = merged.get(key)
            if (!existing || Number(existing.createdAt || 0) <= Number(entry.createdAt || 0)) {
                merged.set(key, entry)
            }
        })

    return Array.from(merged.values()).sort((left, right) => (
        Number(left.createdAt || 0) - Number(right.createdAt || 0)
        || String(left.id || '').localeCompare(String(right.id || ''))
    ))
}

function formatDebugMetric(value, { style = 'number', digits = 2 } = {}) {
    const numericValue = Number(value)
    if (!Number.isFinite(numericValue)) {
        return '—'
    }

    if (style === 'integer') {
        return Math.round(numericValue).toLocaleString()
    }

    if (style === 'percent') {
        return `${(numericValue * 100).toFixed(digits)}%`
    }

    return numericValue.toFixed(digits)
}

function formatDebugTimestamp(value) {
    const timestamp = Number(value)
    if (!Number.isFinite(timestamp) || timestamp <= 0) {
        return '—'
    }

    try {
        const normalizedTimestamp = timestamp < 1_000_000_000_000 ? timestamp * 1000 : timestamp
        return new Date(normalizedTimestamp).toLocaleString()
    } catch {
        return '—'
    }
}

function isGenericStrategyLabel(value) {
    const normalized = String(value || '').trim().toLowerCase()
    return !normalized || normalized === 'current editor strategy' || normalized === 'current strategy'
}

function hasMeaningfulStrategySection(section = {}, defaultsSection = {}) {
    return (
        String(section?.openPrice ?? defaultsSection?.openPrice ?? '').trim() !== String(defaultsSection?.openPrice ?? '').trim()
        || String(section?.closePrice ?? defaultsSection?.closePrice ?? '').trim() !== String(defaultsSection?.closePrice ?? '').trim()
        || String(section?.openIf ?? defaultsSection?.openIf ?? '').trim() !== String(defaultsSection?.openIf ?? '').trim()
        || String(section?.closeIf ?? defaultsSection?.closeIf ?? '').trim() !== String(defaultsSection?.closeIf ?? '').trim()
        || String(section?.gainPrice ?? defaultsSection?.gainPrice ?? '').trim() !== String(defaultsSection?.gainPrice ?? '').trim()
        || String(section?.lossPrice ?? defaultsSection?.lossPrice ?? '').trim() !== String(defaultsSection?.lossPrice ?? '').trim()
        || String(section?.trailingPrice ?? defaultsSection?.trailingPrice ?? '').trim() !== String(defaultsSection?.trailingPrice ?? '').trim()
    )
}

function getComparisonStrategyCount(comparison) {
    return Math.max(1, Number(comparison?.summary?.strategy_count || comparison?.strategy_count || 1) || 1)
}

function getComparisonContributionPreview(comparison) {
    const stats = Array.isArray(comparison?.summary?.portfolio_strategy_stats)
        ? comparison.summary.portfolio_strategy_stats
        : Array.isArray(comparison?.portfolio_strategy_stats)
            ? comparison.portfolio_strategy_stats
            : []

    return stats
        .slice()
        .sort((left, right) => (Number(right?.net_pnl || 0) - Number(left?.net_pnl || 0)))
        .slice(0, 2)
        .map((item) => {
            const label = String(item?.strategy_label || item?.strategy_id || 'Strategy').trim() || 'Strategy'
            return `${label}: ${formatPresetMetric(item?.net_pnl)}`
        })
        .join(' · ')
}

function FieldLabel({ htmlFor, icon, iconClassName = '', children }) {
    return (
        <label htmlFor={htmlFor} className='fieldLabel'>
            <span className={`fieldLabelIcon ${iconClassName}`.trim()} aria-hidden='true'>
                {icon}
            </span>
            <span>{children}</span>
        </label>
    )
}

function StopFieldHelp() {
    return (
        <div className='fieldDescription'>
            Stops use the expression exactly as written and are converted to a price at live execution time. In market orders, the broker validates the minimum stop distance using the real fill price, so very tight gain, loss, or trailing expressions can cause the order to be rejected with invalid stops.
        </div>
    )
}

function parseExpressionParts(value) {
    const text = String(value ?? '')
    const indexedMatches = Array.from(text.matchAll(TOKEN_REGEX)).map((match) => ({
        type: 'token',
        tokenType: 'indexed',
        raw: match[0],
        name: match[1],
        index: Number(match[2]),
        start: match.index ?? 0,
        end: (match.index ?? 0) + match[0].length,
    }))
    const literalMatches = Array.from(text.matchAll(LITERAL_TOKEN_REGEX))
        .map((match) => ({
            type: 'token',
            tokenType: 'literal',
            raw: match[0],
            name: match[1],
            index: null,
            start: match.index ?? 0,
            end: (match.index ?? 0) + match[0].length,
        }))
        .filter((match) => !indexedMatches.some((indexed) => (
            match.start >= indexed.start && match.end <= indexed.end
        )))

    const tokenMatches = [...indexedMatches, ...literalMatches].sort((a, b) => a.start - b.start)
    const parts = []
    let cursor = 0

    for (const match of tokenMatches) {
        if (match.start > cursor) {
            parts.push({
                type: 'text',
                value: text.slice(cursor, match.start),
                start: cursor,
                end: match.start,
            })
        }

        parts.push(match)
        cursor = match.end
    }

    if (cursor < text.length || parts.length === 0) {
        parts.push({
            type: 'text',
            value: text.slice(cursor),
            start: cursor,
            end: text.length,
        })
    }

    return parts
}

function _getNodeTextLength(node) {
    if (!node) {
        return 0
    }

    if (node.nodeType === Node.TEXT_NODE) {
        return node.textContent?.length || 0
    }

    if (node.nodeType === Node.ELEMENT_NODE) {
        const tokenRaw = node.getAttribute?.('data-token-raw')

        if (tokenRaw !== null && tokenRaw !== undefined) {
            return tokenRaw.length
        }

        return Array.from(node.childNodes || []).reduce(
            (total, child) => total + _getNodeTextLength(child),
            0
        )
    }

    return 0
}

function _getEditorPlainText(root) {
    if (!root) {
        return ''
    }

    function readNode(node) {
        if (!node) {
            return ''
        }

        if (node.nodeType === Node.TEXT_NODE) {
            return node.textContent || ''
        }

        if (node.nodeType === Node.ELEMENT_NODE) {
            const tokenRaw = node.getAttribute?.('data-token-raw')

            if (tokenRaw !== null && tokenRaw !== undefined) {
                return tokenRaw
            }

            return Array.from(node.childNodes || []).map(readNode).join('')
        }

        return ''
    }

    return readNode(root)
}

function replaceSelection(value, selectionStart, selectionEnd, replacement) {
    const text = String(value ?? '')
    const start = Math.max(0, Math.min(text.length, selectionStart))
    const end = Math.max(start, Math.min(text.length, selectionEnd))
    const insertedText = String(replacement ?? '')

    return {
        value: `${text.slice(0, start)}${insertedText}${text.slice(end)}`,
        selectionStart: start + insertedText.length,
        selectionEnd: start + insertedText.length,
    }
}

function normalizeEditableExpression(value, selectionStart, selectionEnd = selectionStart, tokenCandidates = []) {
    const originalText = String(value ?? '')
    const text = normalizePythonBooleanLiterals(originalText)
    const safeStart = Math.max(0, Math.min(text.length, selectionStart))
    const safeEnd = Math.max(safeStart, Math.min(text.length, selectionEnd))

    if (safeStart !== safeEnd) {
        return {
            value: text,
            selectionStart: safeStart,
            selectionEnd: safeEnd,
        }
    }

    let tokenStart = safeStart
    while (tokenStart > 0 && IDENTIFIER_REGEX.test(text[tokenStart - 1])) {
        tokenStart -= 1
    }

    let tokenEnd = safeStart
    while (tokenEnd < text.length && IDENTIFIER_REGEX.test(text[tokenEnd])) {
        tokenEnd += 1
    }

    if (tokenStart === tokenEnd) {
        return {
            value: text,
            selectionStart: safeStart,
            selectionEnd: safeEnd,
        }
    }

    const identifier = text.slice(tokenStart, tokenEnd)
    const startsWithLetterOrUnderscore = /^[A-Za-z_]/.test(identifier)
    const normalizedCandidates = new Set(
        (tokenCandidates || [])
            .map((candidate) => String(candidate || '').trim())
            .filter(Boolean)
    )

    if (!startsWithLetterOrUnderscore || RESERVED_IDENTIFIERS.has(identifier) || !normalizedCandidates.has(identifier)) {
        return {
            value: text,
            selectionStart: safeStart,
            selectionEnd: safeEnd,
        }
    }

    if (LITERAL_TOKENS.has(identifier)) {
        return {
            value: text,
            selectionStart: safeStart,
            selectionEnd: safeEnd,
        }
    }

    if (text.slice(tokenEnd).startsWith('[')) {
        return {
            value: text,
            selectionStart: safeStart,
            selectionEnd: safeEnd,
        }
    }

    const suffix = /\s/.test(text[tokenEnd] || '') ? '' : ' '
    const replacement = `${identifier}[0]${suffix}`
    const nextValue = `${text.slice(0, tokenStart)}${replacement}${text.slice(tokenEnd)}`
    const nextCursor = tokenStart + replacement.length

    return {
        value: normalizePythonBooleanLiterals(nextValue),
        selectionStart: nextCursor,
        selectionEnd: nextCursor,
    }
}

function _getSelectionOffsets(root) {
    const selection = window.getSelection()

    if (!selection || selection.rangeCount === 0 || !root) {
        return { start: 0, end: 0 }
    }

    const range = selection.getRangeAt(0)

    function resolveOffset(node, offset) {
        let total = 0
        let resolved = false

        function walk(currentNode) {
            if (resolved || !currentNode) {
                return
            }

            if (currentNode === node) {
                if (currentNode.nodeType === Node.TEXT_NODE) {
                    total += offset
                } else {
                    const children = Array.from(currentNode.childNodes || [])

                    for (let index = 0; index < offset; index += 1) {
                        total += _getNodeTextLength(children[index])
                    }
                }

                resolved = true
                return
            }

            if (currentNode.nodeType === Node.TEXT_NODE) {
                total += currentNode.textContent?.length || 0
                return
            }

            if (currentNode.nodeType === Node.ELEMENT_NODE) {
                const tokenRaw = currentNode.getAttribute?.('data-token-raw')

                if (tokenRaw !== null && tokenRaw !== undefined) {
                    total += tokenRaw.length
                    return
                }
            }

            for (const child of Array.from(currentNode.childNodes || [])) {
                walk(child)

                if (resolved) {
                    return
                }
            }
        }

        walk(root)
        return total
    }

    const start = resolveOffset(range.startContainer, range.startOffset)
    const end = resolveOffset(range.endContainer, range.endOffset)

    return {
        start: Math.max(0, Math.min(start, end)),
        end: Math.max(start, end),
    }
}

function _setSelectionByOffsets(root, startOffset, endOffset = startOffset) {
    if (!root) {
        return
    }

    const range = document.createRange()
    const selection = window.getSelection()

    function resolvePositionInsideNode(node, targetOffset) {
        if (!node) {
            return { node: root, offset: 0 }
        }

        if (node.nodeType === Node.TEXT_NODE) {
            const length = node.textContent?.length || 0
            return { node, offset: Math.max(0, Math.min(targetOffset, length)) }
        }

        if (node.nodeType !== Node.ELEMENT_NODE) {
            return { node: root, offset: 0 }
        }

        const tokenRaw = node.getAttribute?.('data-token-raw')

        if (tokenRaw !== null && tokenRaw !== undefined) {
            const parentNode = node.parentNode || root
            const childNodes = Array.from(parentNode.childNodes || [])
            const index = childNodes.indexOf(node)

            return {
                node: parentNode,
                offset: targetOffset <= 0 ? index : index + 1,
            }
        }

        let remaining = Math.max(0, targetOffset)
        const children = Array.from(node.childNodes || [])

        if (children.length === 0) {
            return { node, offset: 0 }
        }

        for (let index = 0; index < children.length; index += 1) {
            const child = children[index]
            const childLength = _getNodeTextLength(child)

            if (remaining <= childLength) {
                return resolvePositionInsideNode(child, remaining)
            }

            remaining -= childLength
        }

        return resolvePositionInsideNode(children[children.length - 1], _getNodeTextLength(children[children.length - 1]))
    }

    function resolvePosition(targetOffset) {
        let remaining = Math.max(0, targetOffset)
        const nodes = Array.from(root.childNodes || [])

        if (nodes.length === 0) {
            return { node: root, offset: 0 }
        }

        for (let index = 0; index < nodes.length; index += 1) {
            const node = nodes[index]
            const length = _getNodeTextLength(node)

            if (node.nodeType === Node.TEXT_NODE) {
                if (remaining <= length) {
                    return { node, offset: remaining }
                }

                remaining -= length
                continue
            }

            const tokenRaw = node.getAttribute?.('data-token-raw')

            if (tokenRaw !== null && tokenRaw !== undefined) {
                if (remaining === 0) {
                    return { node: root, offset: index }
                }

                if (remaining <= tokenRaw.length) {
                    return { node: root, offset: index + 1 }
                }

                remaining -= tokenRaw.length
                continue
            }

            if (remaining <= length) {
                return resolvePositionInsideNode(node, remaining)
            }

            remaining -= length
        }

        return { node: root, offset: nodes.length }
    }

    const startPosition = resolvePosition(startOffset)
    const endPosition = resolvePosition(endOffset)

    try {
        range.setStart(startPosition.node, startPosition.offset)
        range.setEnd(endPosition.node, endPosition.offset)
        selection?.removeAllRanges()
        selection?.addRange(range)
    } catch {
        range.selectNodeContents(root)
        range.collapse(false)
        selection?.removeAllRanges()
        selection?.addRange(range)
    }
}

function StrategyTextArea({
    id,
    value,
    onChange,
    onCommit,
    onFocusField,
    onSelectionChange,
    registerFieldRef,
    rows = 2,
    tokenCandidates = [],
}) {
    const editorRef = useRef(null)
    const desiredSelectionRef = useRef(null)
    const parts = parseExpressionParts(value)

    function focusAndSelect(nextStart, nextEnd = nextStart) {
        desiredSelectionRef.current = {
            start: nextStart,
            end: nextEnd,
        }
    }

    function applyNextValue(nextValue, nextStart, nextEnd = nextStart) {
        const normalized = normalizeEditableExpression(
            nextValue,
            nextStart,
            nextEnd,
            tokenCandidates,
        )

        onChange(normalized.value)
        focusAndSelect(normalized.selectionStart, normalized.selectionEnd)
    }

    function applyControlledValue(nextValue, nextStart, nextEnd = nextStart) {
        onChange(nextValue)
        focusAndSelect(nextStart, nextEnd)
    }

    function getSelection() {
        return {
            start: editorRef.current?.selectionStart ?? 0,
            end: editorRef.current?.selectionEnd ?? 0,
        }
    }

    function findTokenContainingOffset(offset) {
        return parts.find((part) => (
            part.type === 'token'
            && offset > part.start
            && offset < part.end
        ))
    }

    function snapSelectionOutOfToken(start, end = start) {
        if (start !== end) {
            return { start, end }
        }

        const token = findTokenContainingOffset(start)

        if (!token) {
            return { start, end }
        }

        const midpoint = token.start + ((token.end - token.start) / 2)
        const snappedOffset = start <= midpoint ? token.start : token.end

        return {
            start: snappedOffset,
            end: snappedOffset,
        }
    }

    function handleKeyDown(event) {
        if (event.key === 'Enter') {
            event.preventDefault()
            event.currentTarget.blur()
            return
        }

        if (event.key !== 'Backspace' && event.key !== 'Delete') {
            return
        }

        const selection = snapSelectionOutOfToken(...Object.values(getSelection()))
        const currentValue = String(value ?? '')
        event.preventDefault()

        if (selection.start !== selection.end) {
            const nextState = replaceSelection(currentValue, selection.start, selection.end, '')
            applyNextValue(nextState.value, nextState.selectionStart, nextState.selectionEnd)
            return
        }

        const targetToken = parts.find((part) => (
            part.type === 'token'
            && (
                (event.key === 'Backspace' && part.end === selection.start)
                || (event.key === 'Delete' && part.start === selection.start)
            )
        ))

        if (targetToken) {
            const nextValue = `${currentValue.slice(0, targetToken.start)}${currentValue.slice(targetToken.end)}`
            applyControlledValue(nextValue, targetToken.start)
            return
        }

        if (event.key === 'Backspace') {
            if (selection.start <= 0) {
                focusAndSelect(0)
                return
            }

            const nextState = replaceSelection(currentValue, selection.start - 1, selection.start, '')
            applyNextValue(nextState.value, nextState.selectionStart, nextState.selectionEnd)
            return
        }

        if (selection.start >= currentValue.length) {
            focusAndSelect(currentValue.length)
            return
        }

        const nextState = replaceSelection(currentValue, selection.start, selection.start + 1, '')
        applyNextValue(nextState.value, nextState.selectionStart, nextState.selectionEnd)
    }

    function handlePaste(event) {
        event.preventDefault()
        const selection = snapSelectionOutOfToken(...Object.values(getSelection()))
        const pastedText = event.clipboardData?.getData('text/plain') || ''
        const nextState = replaceSelection(
            String(value ?? ''),
            selection.start,
            selection.end,
            pastedText.replace(/\r?\n/g, ' ')
        )
        applyNextValue(nextState.value, nextState.selectionStart, nextState.selectionEnd)
    }

    function handleChange(event) {
        const selection = snapSelectionOutOfToken(
            event.target.selectionStart ?? 0,
            event.target.selectionEnd ?? 0,
        )
        applyNextValue(event.target.value, selection.start, selection.end)
    }

    function handleSelectionChange() {
        const selection = snapSelectionOutOfToken(...Object.values(getSelection()))
        onSelectionChange?.(id, selection.start, selection.end)
    }

    function handleTokenRemove(token) {
        const nextValue = `${String(value).slice(0, token.start)}${String(value).slice(token.end)}`
        applyControlledValue(nextValue, token.start)
        onCommit?.(nextValue)
        onFocusField?.(id)
        onSelectionChange?.(id, token.start, token.start)
    }

    function handleTokenShift(token, step) {
        const nextIndex = Math.max(0, token.index + step)
        const replacement = `${token.name}[${nextIndex}]`
        const nextValue = `${String(value).slice(0, token.start)}${replacement}${String(value).slice(token.end)}`
        const nextCursor = token.start + replacement.length

        applyControlledValue(nextValue, nextCursor)
        onCommit?.(nextValue)
        onFocusField?.(id)
        onSelectionChange?.(id, nextCursor, nextCursor)
    }

    useLayoutEffect(() => {
        const editor = editorRef.current
        const pendingSelection = desiredSelectionRef.current

        if (!editor || !pendingSelection) {
            return
        }

        editor.focus()
        editor.setSelectionRange(pendingSelection.start, pendingSelection.end)
        desiredSelectionRef.current = null
    }, [value])

    useEffect(() => {
        registerFieldRef?.(id, editorRef.current)

        return () => {
            registerFieldRef?.(id, null)
        }
    }, [id, registerFieldRef])

    return (
        <div className='strategyExpressionEditorShell'>
            <textarea
                id={id}
                rows={rows}
                className={`strategyExpressionEditor ${rows === 1 ? 'singleLine' : 'multiLine'}`}
                ref={editorRef}
                value={String(value ?? '')}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                onBlur={() => onCommit?.(String(value ?? ''))}
                onFocus={() => {
                    onFocusField?.(id)
                    handleSelectionChange()
                }}
                onClick={handleSelectionChange}
                onKeyUp={handleSelectionChange}
                onSelect={handleSelectionChange}
                spellCheck={false}
            />
            {parts.some((part) => part.type === 'token') && (
                <div className={`strategyExpressionPreview ${rows === 1 ? 'singleLine' : 'multiLine'}`} aria-hidden='true'>
                    {parts.map((part, index) => {
                        if (part.type === 'text') {
                            return (
                                <span key={`${id}-preview-text-${index}`} className='strategyExpressionPreviewText'>
                                    {part.value}
                                </span>
                            )
                        }

                        return (
                            <span
                                key={`${id}-token-${part.start}`}
                                className={`strategyToken ${part.tokenType === 'literal' ? 'isLiteral' : ''}`}
                                data-token-raw={part.raw}
                            >
                                <span className='strategyTokenLabel'>{part.name}</span>
                                {part.tokenType !== 'literal' && (
                                    <>
                                        <span className='strategyTokenIndex'>[{part.index}]</span>
                                        <button
                                            type='button'
                                            className='strategyTokenButton'
                                            tabIndex={-1}
                                            onMouseDown={(event) => event.preventDefault()}
                                            onClick={() => handleTokenShift(part, 1)}
                                            aria-label={`Increase ${part.name} index`}
                                            title='Increase index'
                                        >
                                            ‹
                                        </button>
                                        <button
                                            type='button'
                                            className='strategyTokenButton'
                                            tabIndex={-1}
                                            onMouseDown={(event) => event.preventDefault()}
                                            onClick={() => handleTokenShift(part, -1)}
                                            aria-label={`Decrease ${part.name} index`}
                                            title='Decrease index'
                                        >
                                            ›
                                        </button>
                                    </>
                                )}
                                <button
                                    type='button'
                                    className='strategyTokenButton remove'
                                    tabIndex={-1}
                                    onMouseDown={(event) => event.preventDefault()}
                                    onClick={() => handleTokenRemove(part)}
                                    aria-label={`Remove ${part.name}`}
                                    title='Remove token'
                                >
                                    x
                                </button>
                            </span>
                        )
                    })}
                </div>
            )}
        </div>
    )
}

function SelectPriority({ id, value, onChange }) {
    return (
        <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
            <option value='Long'>Long</option>
            <option value='Short'>Short</option>
        </select>
    )
}

const PRICE_PRESET_OPTIONS = [
    { value: 'open[0]', label: 'Current open' },
    { value: 'close[0]', label: 'Current close' },
]

function resolvePricePreset(value) {
    const normalized = String(value ?? '').trim()
    const preset = PRICE_PRESET_OPTIONS.find((option) => option.value === normalized)
    return preset ? preset.value : '__custom__'
}


function PricePresetField({
    fieldId,
    label,
    section,
    field,
    icon,
    iconClassName,
    value,
    tokenCandidates,
    updateStrategyField,
    logFieldChange,
    handleFocusField,
    updateSelection,
    registerFieldRef,
}) {
    const selectedPreset = resolvePricePreset(value)

    return (
        <div className='field'>
            <FieldLabel htmlFor={fieldId} icon={icon} iconClassName={iconClassName}>
                {label}
            </FieldLabel>
            <div className='strategyPricePresetRow'>
                <label className='strategyPricePresetLabel' htmlFor={`${fieldId}Preset`}>
                    Preset
                </label>
                <select
                    id={`${fieldId}Preset`}
                    className='strategyPricePresetSelect'
                    value={selectedPreset}
                    onChange={(event) => {
                        const nextValue = event.target.value === '__custom__'
                            ? String(value ?? '')
                            : event.target.value
                        updateStrategyField(section, field, nextValue)
                        logFieldChange(section === 'long' ? 'Long' : 'Short', `${label} preset`, event.target.value === '__custom__' ? 'custom' : nextValue)
                    }}
                >
                    {PRICE_PRESET_OPTIONS.map((option) => (
                        <option key={`${fieldId}-${option.value}`} value={option.value}>
                            {option.label}
                        </option>
                    ))}
                    <option value='__custom__'>Custom expression</option>
                </select>
            </div>
            <StrategyTextArea
                id={fieldId}
                rows={1}
                value={value}
                tokenCandidates={tokenCandidates}
                onChange={(nextValue) => updateStrategyField(section, field, nextValue)}
                onCommit={(nextValue) => logFieldChange(section === 'long' ? 'Long' : 'Short', label, nextValue)}
                onFocusField={handleFocusField}
                onSelectionChange={updateSelection}
                registerFieldRef={registerFieldRef}
            />
        </div>
    )
}

export function Strategy({
    authToken = '',
    isGuest = false,
    strategy,
    setStrategy,
    backtestStrategySet = [],
    setBacktestStrategySet,
    currentStrategyLabel = '',
    onStrategyLabelChange,
    chartSettings,
    backtest,
    setBacktest,
    lastBacktestResponse,
    onLoadStrategyIndicators,
    onStrategyStatusChange,
    onLogEvent,
    onActiveStrategyFieldChange,
    insertRequest,
    isBusy = false,
    isActive,
    activeBrokerProfileId = '',
}) {
    const authHeaders = authToken
        ? {
            Authorization: `Bearer ${authToken}`,
        }
        : {}
    const [isDebugRunning, setIsDebugRunning] = useState(false)
    const [strategyViewTab, setStrategyViewTab] = useState(() => (isGuest ? 'manager' : 'editor'))
    const [strategyLibraryTab, setStrategyLibraryTab] = useState(() => (isGuest ? 'load' : 'save'))
    const [strategyLibraryListTab, setStrategyLibraryListTab] = useState('all')
    const [strategyLibraryQuery, setStrategyLibraryQuery] = useState('')
    const [strategyLibraryItems, setStrategyLibraryItems] = useState([])
    const [isStrategyLibraryLoading, setIsStrategyLibraryLoading] = useState(false)
    const [isStrategyLibrarySaving, setIsStrategyLibrarySaving] = useState(false)
    const [selectedStrategyLibraryId, setSelectedStrategyLibraryId] = useState('')
    const [strategySaveLabel, setStrategySaveLabel] = useState('')
    const [strategySaveNotes, setStrategySaveNotes] = useState('')
    const guestRestrictionMessage = 'Guest demo can inspect the curated strategy and load saved examples, but cannot save strategies, change favorites, delete library entries, or run debug.'
    const [strategyDebugState, setStrategyDebugState] = useState({
        loading: false,
        error: '',
        session: null,
        entries: [],
        payload: null,
        refreshedAt: null,
    })
    const [activeTab, setActiveTab] = useState('Long')
    const [activeFieldId, setActiveFieldId] = useState('openLong')
    const [activeSidebarTabBySection, setActiveSidebarTabBySection] = useState({
        long: 'tokens',
        short: 'tokens',
    })
    const [sidebarWidth, setSidebarWidth] = useState(450)
    const [sidebarCollapsedBySection, setSidebarCollapsedBySection] = useState({
        long: false,
        short: false,
    })
    const [presetComparisonState, setPresetComparisonState] = useState({
        long: { loading: false, error: '', comparisons: [], bestPresetId: '' },
        short: { loading: false, error: '', comparisons: [], bestPresetId: '' },
    })
    const fieldRefs = useRef({})
    const selectionRef = useRef({})
    const handledInsertNonceRef = useRef('')
    const sidebarResizeStateRef = useRef(null)
    const strategyStatusChangeRef = useRef(onStrategyStatusChange)
    const tokenCandidates = getStrategyTokenCandidates(chartSettings)
    const tokenGroups = getStrategyTokenGroups(chartSettings)
    const marketRegimePresets = buildMarketRegimePresetModel(tokenCandidates)
    const elliottWavePresets = buildElliottWavePresetModel(chartSettings)
    const normalizedStrategyLibraryQuery = String(strategyLibraryQuery || '').trim().toLowerCase()
    const visibleStrategyLibraryItems = (strategyLibraryListTab === 'favorites'
        ? strategyLibraryItems.filter((entry) => Boolean(entry?.is_favorite))
        : strategyLibraryItems)
        .filter((entry) => {
            if (!normalizedStrategyLibraryQuery) {
                return true
            }

            const haystack = [
                entry?.label,
                entry?.notes,
                entry?.source,
                entry?.side,
                ...(Array.isArray(entry?.strategies)
                    ? entry.strategies.flatMap((item) => [
                        item?.label,
                        item?.symbol,
                        item?.timeframe,
                    ])
                    : []),
            ]
                .map((value) => String(value || '').trim().toLowerCase())
                .filter(Boolean)
                .join(' ')

            return haystack.includes(normalizedStrategyLibraryQuery)
        })
    const selectedStrategyLibraryItem = visibleStrategyLibraryItems.find((entry) => String(entry?.id) === String(selectedStrategyLibraryId)) || null

    useEffect(() => {
        const nextSymbol = String(chartSettings?.symbol || 'EURUSD').toUpperCase()
        const nextTimeframe = String(chartSettings?.timeframe || 'M1').toUpperCase()
        setStrategySaveLabel((current) => current || `${nextSymbol} ${nextTimeframe} · Strategy`)
    }, [chartSettings?.symbol, chartSettings?.timeframe])

    useEffect(() => {
        if (!isGuest) {
            return
        }

        setStrategyViewTab((current) => (current === 'debug' ? 'manager' : current))
        setStrategyLibraryTab((current) => (current === 'save' ? 'load' : current))
    }, [isGuest])

    useEffect(() => {
        function handlePointerMove(event) {
            if (!sidebarResizeStateRef.current) {
                return
            }

            const nextWidth = Math.max(420, Math.min(960, window.innerWidth - event.clientX - 48))
            setSidebarWidth(nextWidth)
        }

        function handlePointerUp() {
            sidebarResizeStateRef.current = null
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

    const defaults = {
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
    }

    const backtestDefaults = BACKTEST_DEFAULTS

    function mergeStrategyDefaults(current) {
        return {
            long: {
                openPrice: String(current?.long?.openPrice ?? defaults.long.openPrice),
                closePrice: String(current?.long?.closePrice ?? defaults.long.closePrice),
                openIf: String(current?.long?.openIf ?? defaults.long.openIf),
                closeIf: String(current?.long?.closeIf ?? defaults.long.closeIf),
                gainPrice: String(current?.long?.gainPrice ?? defaults.long.gainPrice),
                lossPrice: String(current?.long?.lossPrice ?? defaults.long.lossPrice),
                trailingPrice: String(current?.long?.trailingPrice ?? defaults.long.trailingPrice),
            },
            short: {
                openPrice: String(current?.short?.openPrice ?? defaults.short.openPrice),
                closePrice: String(current?.short?.closePrice ?? defaults.short.closePrice),
                openIf: String(current?.short?.openIf ?? defaults.short.openIf),
                closeIf: String(current?.short?.closeIf ?? defaults.short.closeIf),
                gainPrice: String(current?.short?.gainPrice ?? defaults.short.gainPrice),
                lossPrice: String(current?.short?.lossPrice ?? defaults.short.lossPrice),
                trailingPrice: String(current?.short?.trailingPrice ?? defaults.short.trailingPrice),
            },
            other: {
                allowInversion: Boolean(current?.other?.allowInversion ?? defaults.other.allowInversion),
                priority: String(current?.other?.priority ?? defaults.other.priority),
            },
            featureManifest: normalizeStrategyFeatureManifest(current?.featureManifest),
        }
    }

    function buildBlankStrategy() {
        return mergeStrategyDefaults(null)
    }

    function selectEditorTabForStrategy(nextStrategy, preferredSide = '') {
        const normalizedPreferredSide = String(preferredSide || '').trim().toLowerCase()
        const hasLongContent = hasMeaningfulStrategySection(nextStrategy?.long, defaults.long)
        const hasShortContent = hasMeaningfulStrategySection(nextStrategy?.short, defaults.short)

        let nextTab = activeTab
        if (normalizedPreferredSide === 'long') {
            nextTab = 'Long'
        } else if (normalizedPreferredSide === 'short') {
            nextTab = 'Short'
        } else if (hasShortContent && !hasLongContent) {
            nextTab = 'Short'
        } else if (hasLongContent && !hasShortContent) {
            nextTab = 'Long'
        }

        setActiveTab(nextTab)
        setActiveFieldId(nextTab === 'Short' ? 'openShort' : 'openLong')
    }

    function mergeBacktestDefaults(current) {
        return {
            ...backtestDefaults,
            ...(current || {}),
        }
    }

    function sanitizeImportedStrategy(current, strategyChartSettings = chartSettings) {
        const migratedStrategy = migrateStrategyFeatureNamesToAliases(current, strategyChartSettings)
        const nextStrategy = mergeStrategyDefaults(migratedStrategy)

        return {
            long: {
                ...nextStrategy.long,
                openPrice: String(nextStrategy.long.openPrice ?? defaults.long.openPrice),
                closePrice: String(nextStrategy.long.closePrice ?? defaults.long.closePrice),
                openIf: normalizePythonBooleanLiterals(nextStrategy.long.openIf ?? defaults.long.openIf),
                closeIf: normalizePythonBooleanLiterals(nextStrategy.long.closeIf ?? defaults.long.closeIf),
                gainPrice: String(nextStrategy.long.gainPrice ?? defaults.long.gainPrice),
                lossPrice: String(nextStrategy.long.lossPrice ?? defaults.long.lossPrice),
                trailingPrice: String(nextStrategy.long.trailingPrice ?? defaults.long.trailingPrice),
            },
            short: {
                ...nextStrategy.short,
                openPrice: String(nextStrategy.short.openPrice ?? defaults.short.openPrice),
                closePrice: String(nextStrategy.short.closePrice ?? defaults.short.closePrice),
                openIf: normalizePythonBooleanLiterals(nextStrategy.short.openIf ?? defaults.short.openIf),
                closeIf: normalizePythonBooleanLiterals(nextStrategy.short.closeIf ?? defaults.short.closeIf),
                gainPrice: String(nextStrategy.short.gainPrice ?? defaults.short.gainPrice),
                lossPrice: String(nextStrategy.short.lossPrice ?? defaults.short.lossPrice),
                trailingPrice: String(nextStrategy.short.trailingPrice ?? defaults.short.trailingPrice),
            },
            other: {
                ...nextStrategy.other,
                allowInversion: Boolean(nextStrategy.other.allowInversion),
                priority: String(nextStrategy.other.priority || defaults.other.priority) === 'Long' ? 'Long' : 'Short',
            },
            featureManifest: normalizeStrategyFeatureManifest(nextStrategy.featureManifest),
        }
    }

    function sanitizeImportedBacktest(current) {
        const nextBacktest = mergeBacktestDefaults(mergeBacktestCostProfileValues(current))
        const legacySlippage = Number.isFinite(Number(nextBacktest.slippageInPips))
            ? Number(nextBacktest.slippageInPips)
            : backtestDefaults.slippageInPips
        const rawHistoryScopeMode = String(nextBacktest.historyScopeMode || nextBacktest.history_scope_mode || backtestDefaults.historyScopeMode).trim().toLowerCase()
        const historyScopeMode = rawHistoryScopeMode === 'custom' ? 'custom' : 'loaded_chart'
        const historyScopeBars = historyScopeMode === 'custom'
            ? Math.max(1, Number(nextBacktest.historyScopeBars ?? nextBacktest.history_scope_bars ?? backtestDefaults.historyScopeBars ?? 1) || 1)
            : null

        return {
            initialBalance: Number.isFinite(Number(nextBacktest.initialBalance))
                ? Number(nextBacktest.initialBalance)
                : backtestDefaults.initialBalance,
            assetType: String(nextBacktest.assetType || backtestDefaults.assetType).trim().toLowerCase() || backtestDefaults.assetType,
            initialVolume: Number.isFinite(Number(nextBacktest.initialVolume))
                ? Number(nextBacktest.initialVolume)
                : backtestDefaults.initialVolume,
            pipSize: Number.isFinite(Number(nextBacktest.pipSize))
                ? Number(nextBacktest.pipSize)
                : backtestDefaults.pipSize,
            pipValuePerLot: Number.isFinite(Number(nextBacktest.pipValuePerLot))
                ? Number(nextBacktest.pipValuePerLot)
                : backtestDefaults.pipValuePerLot,
            costProfile: normalizeBacktestCostProfile(nextBacktest.costProfile),
            spreadInPips: Number.isFinite(Number(nextBacktest.spreadInPips))
                ? Number(nextBacktest.spreadInPips)
                : backtestDefaults.spreadInPips,
            slippageInPips: Number.isFinite(Number(nextBacktest.slippageInPips))
                ? Number(nextBacktest.slippageInPips)
                : backtestDefaults.slippageInPips,
            entrySlippageInPips: Number.isFinite(Number(nextBacktest.entrySlippageInPips))
                ? Number(nextBacktest.entrySlippageInPips)
                : legacySlippage,
            closeSlippageInPips: Number.isFinite(Number(nextBacktest.closeSlippageInPips))
                ? Number(nextBacktest.closeSlippageInPips)
                : legacySlippage,
            takeProfitSlippageInPips: Number.isFinite(Number(nextBacktest.takeProfitSlippageInPips))
                ? Number(nextBacktest.takeProfitSlippageInPips)
                : legacySlippage,
            stopLossSlippageInPips: Number.isFinite(Number(nextBacktest.stopLossSlippageInPips))
                ? Number(nextBacktest.stopLossSlippageInPips)
                : legacySlippage,
            trailingStopSlippageInPips: Number.isFinite(Number(nextBacktest.trailingStopSlippageInPips))
                ? Number(nextBacktest.trailingStopSlippageInPips)
                : legacySlippage,
            minimumStopDistanceInPips: Number.isFinite(Number(nextBacktest.minimumStopDistanceInPips))
                ? Number(nextBacktest.minimumStopDistanceInPips)
                : backtestDefaults.minimumStopDistanceInPips,
            volatilitySlippageMultiplier: Number.isFinite(Number(nextBacktest.volatilitySlippageMultiplier))
                ? Number(nextBacktest.volatilitySlippageMultiplier)
                : backtestDefaults.volatilitySlippageMultiplier,
            executionMode: String(nextBacktest.executionMode || backtestDefaults.executionMode).trim().toLowerCase() || backtestDefaults.executionMode,
            portfolioMode: String(nextBacktest.portfolioMode || backtestDefaults.portfolioMode).trim().toLowerCase() || backtestDefaults.portfolioMode,
            historyScopeMode,
            historyScopeBars,
        }
    }

    function sanitizeImportedStrategySet(current, strategyChartSettings = chartSettings) {
        return Array.isArray(current)
            ? current
                .filter((entry) => entry && typeof entry === 'object')
                .map((entry) => ({
                    ...entry,
                    strategy: sanitizeImportedStrategy(entry?.strategy || {}, strategyChartSettings),
                }))
            : []
    }

    function serializeStrategyLibraryEntry(entry) {
        return JSON.stringify({
            strategy: sanitizeImportedStrategy(entry?.strategy || {}),
            strategies: sanitizeImportedStrategySet(entry?.strategies || []),
        })
    }

    function getStrategyValue(section, field) {
        return strategy?.[section]?.[field] ?? defaults[section][field]
    }

    function updateStrategyField(section, field, value) {
        setStrategy((prev) => ({
            ...prev,
            [section]: {
                ...(prev?.[section] || {}),
                [field]: value,
            },
        }))
    }

    function registerFieldRef(fieldId, element) {
        if (element) {
            fieldRefs.current[fieldId] = element
            return
        }

        delete fieldRefs.current[fieldId]
    }

    function updateSelection(fieldId, start, end) {
        selectionRef.current[fieldId] = {
            start: Number.isFinite(start) ? start : 0,
            end: Number.isFinite(end) ? end : Number.isFinite(start) ? start : 0,
        }
    }

    function handleFocusField(fieldId) {
        setActiveFieldId(fieldId)
        onActiveStrategyFieldChange?.(fieldId)
        const element = fieldRefs.current[fieldId]

        if (element) {
            updateSelection(
                fieldId,
                element.selectionStart ?? 0,
                element.selectionEnd ?? element.selectionStart ?? 0,
            )
        }
    }

    function getPreferredFieldIdForSection(section) {
        const sectionFieldIds = section === 'long'
            ? ['openPriceLong', 'closePriceLong', 'openLong', 'closeLong', 'gainPriceLong', 'lossPriceLong', 'trailingPriceLong']
            : ['openPriceShort', 'closePriceShort', 'openShort', 'closeShort', 'gainPriceShort', 'lossPriceShort', 'trailingPriceShort']

        if (sectionFieldIds.includes(activeFieldId)) {
            return activeFieldId
        }

        return section === 'long' ? 'openLong' : 'openShort'
    }

    function getFieldTargetById(fieldId) {
        const fieldMap = {
            openPriceLong: ['long', 'openPrice'],
            closePriceLong: ['long', 'closePrice'],
            openLong: ['long', 'openIf'],
            closeLong: ['long', 'closeIf'],
            gainPriceLong: ['long', 'gainPrice'],
            lossPriceLong: ['long', 'lossPrice'],
            trailingPriceLong: ['long', 'trailingPrice'],
            openPriceShort: ['short', 'openPrice'],
            closePriceShort: ['short', 'closePrice'],
            openShort: ['short', 'openIf'],
            closeShort: ['short', 'closeIf'],
            gainPriceShort: ['short', 'gainPrice'],
            lossPriceShort: ['short', 'lossPrice'],
            trailingPriceShort: ['short', 'trailingPrice'],
        }

        return fieldMap[fieldId] || null
    }

    function formatLogValue(value) {
        if (typeof value === 'boolean') {
            return value ? 'true' : 'false'
        }

        return String(value ?? '')
    }

    function logFieldChange(sectionLabel, fieldLabel, value) {
        onLogEvent?.(`Strategy · ${sectionLabel} · ${fieldLabel}: ${formatLogValue(value)}`)
    }

    function insertTextIntoField(fieldId, section, field, text) {
        const currentValue = String(getStrategyValue(section, field) ?? '')
        const selection = selectionRef.current[fieldId] || {
            start: currentValue.length,
            end: currentValue.length,
        }
        const start = Math.max(0, Math.min(currentValue.length, selection.start ?? currentValue.length))
        const end = Math.max(start, Math.min(currentValue.length, selection.end ?? start))
        const nextValue = `${currentValue.slice(0, start)}${text}${currentValue.slice(end)}`
        const nextCursor = start + text.length

        updateStrategyField(section, field, nextValue)
        logFieldChange(section === 'other' ? 'Other' : section === 'long' ? 'Long' : 'Short', field, nextValue)
        updateSelection(fieldId, nextCursor, nextCursor)

        window.requestAnimationFrame(() => {
            const element = fieldRefs.current[fieldId]

            if (!element) {
                return
            }

            element.focus()
            element.setSelectionRange(nextCursor, nextCursor)
        })
    }

    function insertTokenCandidate(section, tokenName) {
        const fieldId = getPreferredFieldIdForSection(section)
        const target = getFieldTargetById(fieldId)

        if (!target) {
            return
        }

        const [targetSection, field] = target
        insertTextIntoField(
            fieldId,
            targetSection,
            field,
            LITERAL_TOKENS.has(tokenName) ? tokenName : `${tokenName}[0]`
        )
    }

    function applyMarketRegimePreset(section, preset) {
        if (!preset) {
            return
        }

        const nextStrategy = buildStrategyFromMarketRegimePreset(section, preset, strategy)
        setStrategy(nextStrategy)
        logFieldChange(section === 'long' ? 'Long' : 'Short', 'Market Regime preset', preset.label)
        onLogEvent?.(`Strategy · ${section === 'long' ? 'Long' : 'Short'} · Market Regime preset applied: ${preset.label}.`)
    }

    function applyElliottWavePreset(section, preset) {
        if (!preset || !elliottWavePresets?.indicator) {
            return
        }

        const baseStrategy = buildStrategyFromElliottWavePreset(section, preset, strategy)
        const nextStrategy = attachStrategyFeatureManifest(
            baseStrategy,
            chartSettings,
            [elliottWavePresets.indicator],
        )

        setStrategy(nextStrategy)
        logFieldChange(section === 'long' ? 'Long' : 'Short', 'Elliott preset', preset.label)
        onLogEvent?.(`Strategy · ${section === 'long' ? 'Long' : 'Short'} · Elliott breakout preset applied: ${preset.label}.`)
    }

    function renderMarketRegimePresetPanel(section) {
        if (!marketRegimePresets) {
            return null
        }

        const presets = section === 'long'
            ? marketRegimePresets.longEntries
            : marketRegimePresets.shortEntries
        const recommendation = buildMarketRegimePresetRecommendation(lastBacktestResponse, presets)
        const comparisonState = presetComparisonState[section] || { loading: false, error: '', comparisons: [], bestPresetId: '' }

        return (
            <section className='strategyPresetPanel'>
                <div className='strategyPresetPanelHeaderRow'>
                    <div className='strategyPresetPanelHeader'>Market Regime presets</div>
                    <button
                        type='button'
                        className='strategyPresetCompareButton'
                        onClick={() => void handleComparePresets(section)}
                        disabled={comparisonState.loading || isDebugRunning || isBusy}
                    >
                        {comparisonState.loading ? 'Comparing...' : 'Compare presets'}
                    </button>
                </div>
                {recommendation && (
                    <div className='strategyPresetRecommendation'>
                        <div className='strategyPresetRecommendationLabel'>Suggested preset</div>
                        <div className='strategyPresetRecommendationValue'>{recommendation.preset.label}</div>
                        <div className='strategyPresetRecommendationText'>{recommendation.reason}</div>
                    </div>
                )}
                {comparisonState.error ? (
                    <div className='strategyPresetCompareError'>{comparisonState.error}</div>
                ) : null}
                {comparisonState.comparisons?.length ? (
                    <div className='strategyPresetCompareList'>
                        {comparisonState.comparisons.map((comparison) => (
                            <div
                                key={`${section}-${comparison.id}`}
                                className={`strategyPresetCompareCard ${comparison.id === comparisonState.bestPresetId ? 'isBest' : ''}`}
                            >
                                <div className='strategyPresetCompareCardHeader'>
                                    <span className='strategyPresetCompareCardTitle'>{comparison.label}</span>
                                    <div className='strategyPresetCompareBadgeRow'>
                                        {getComparisonStrategyCount(comparison) > 1 ? (
                                            <span className='strategyPresetCompareBadge isPortfolio'>
                                                Portfolio · {getComparisonStrategyCount(comparison)}
                                            </span>
                                        ) : null}
                                        {comparison.id === comparisonState.bestPresetId ? (
                                            <span className='strategyPresetCompareBadge'>Best</span>
                                        ) : null}
                                    </div>
                                </div>
                                <div className='strategyPresetCompareMetrics'>
                                    <span>Net PnL: {formatPresetMetric(comparison.summary?.net_pnl)}</span>
                                    <span>Win rate: {formatPresetMetric(comparison.summary?.win_rate, 'percent')}</span>
                                    <span>Avg trade: {formatPresetMetric(comparison.summary?.expectancy_per_trade)}</span>
                                    <span>Max DD: {formatPresetMetric(comparison.summary?.max_drawdown)}</span>
                                    <span>Trades: {formatPresetMetric(comparison.summary?.n_trades, 'integer')}</span>
                                </div>
                                {getComparisonContributionPreview(comparison) ? (
                                    <div className='strategyPresetCompareContribution'>
                                        Top contribution: {getComparisonContributionPreview(comparison)}
                                    </div>
                                ) : null}
                            </div>
                        ))}
                    </div>
                ) : null}
                <div className='strategyPresetPanelBody'>
                    {presets.map((preset) => (
                        <button
                            key={`${section}-${preset.id}`}
                            type='button'
                            className='strategyPresetCard'
                            onClick={() => applyMarketRegimePreset(section, preset)}
                            title={preset.description}
                        >
                            <span className='strategyPresetCardTitle'>{preset.label}</span>
                            <span className='strategyPresetCardText'>{preset.description}</span>
                        </button>
                    ))}
                </div>
                <div className='strategyPresetDoc'>
                    <div className='strategyPresetDocTitle'>Market Regime reference</div>
                    <div className='strategyPresetDocList'>
                        {MARKET_REGIME_DOC_ITEMS.map((item) => (
                            <div key={`${section}-${item.key}`} className='strategyPresetDocItem'>
                                <div className='strategyPresetDocKey'>{item.label}</div>
                                <div className='strategyPresetDocText'>{item.description}</div>
                                <div className='strategyPresetDocExpected'>{item.expected}</div>
                            </div>
                        ))}
                    </div>
                </div>
            </section>
        )
    }

    function renderElliottWavePresetPanel(section) {
        if (!elliottWavePresets?.indicator) {
            return null
        }

        const presets = section === 'long'
            ? elliottWavePresets.longEntries
            : elliottWavePresets.shortEntries

        return (
            <section className='strategyPresetPanel'>
                <div className='strategyPresetPanelHeaderRow'>
                    <div className='strategyPresetPanelHeader'>Elliott breakout presets</div>
                </div>
                <div className='strategyPresetRecommendation'>
                    <div className='strategyPresetRecommendationLabel'>Indicator contract</div>
                    <div className='strategyPresetRecommendationValue'>
                        {String(elliottWavePresets.indicator?.alias || elliottWavePresets.indicator?.name || 'ElliottWaveProxyV1').trim()}
                    </div>
                    <div className='strategyPresetRecommendationText'>
                        This preset attaches the Elliott feature manifest automatically and trades support/resistance breaks through the indicator&apos;s breakout and retest envelopes.
                    </div>
                </div>
                <div className='strategyPresetPanelBody'>
                    {presets.map((preset) => (
                        <button
                            key={`${section}-${preset.id}`}
                            type='button'
                            className='strategyPresetCard'
                            onClick={() => applyElliottWavePreset(section, preset)}
                            title={preset.description}
                        >
                            <span className='strategyPresetCardTitle'>{preset.label}</span>
                            <span className='strategyPresetCardText'>{preset.description}</span>
                        </button>
                    ))}
                </div>
                <div className='strategyPresetDoc'>
                    <div className='strategyPresetDocTitle'>Elliott breakout reference</div>
                    <div className='strategyPresetDocList'>
                        {ELLIOTT_WAVE_DOC_ITEMS.map((item) => (
                            <div key={`${section}-${item.key}`} className='strategyPresetDocItem'>
                                <div className='strategyPresetDocKey'>{item.label}</div>
                                <div className='strategyPresetDocText'>{item.description}</div>
                                <div className='strategyPresetDocExpected'>{item.expected}</div>
                            </div>
                        ))}
                    </div>
                </div>
            </section>
        )
    }

    async function handleComparePresets(section) {
        if (!marketRegimePresets) {
            return
        }

        const presets = section === 'long'
            ? marketRegimePresets.longEntries
            : marketRegimePresets.shortEntries

        const baseStrategy = buildBlankStrategy()
        baseStrategy.other.priority = section === 'long' ? 'Long' : 'Short'

        setPresetComparisonState((current) => ({
            ...current,
            [section]: {
                ...current[section],
                loading: true,
                error: '',
            },
        }))

        try {
            const payload = {
                presets: presets.map((preset) => ({
                    id: preset.id,
                    label: preset.label,
                    strategy: resolveStrategyAliasesInStrategy({
                        ...baseStrategy,
                        [section]: {
                            ...baseStrategy[section],
                            openIf: preset.openIf,
                            closeIf: preset.closeIf,
                            gainPrice: preset.gainPrice || '',
                            lossPrice: preset.lossPrice || '',
                            trailingPrice: preset.trailingPrice || '',
                        },
                    }, chartSettings),
                })),
                backtest: {
                    initialBalance: Number(backtest.initialBalance),
                    assetType: String(backtest.assetType).trim().toLowerCase(),
                    initialVolume: Number(backtest.initialVolume),
                    pipSize: Number(backtest.pipSize),
                    pipValuePerLot: Number(backtest.pipValuePerLot),
                    spreadInPips: Number(backtest.spreadInPips),
                    slippageInPips: Number(backtest.slippageInPips),
                    entrySlippageInPips: Number(backtest.entrySlippageInPips),
                    closeSlippageInPips: Number(backtest.closeSlippageInPips),
                    takeProfitSlippageInPips: Number(backtest.takeProfitSlippageInPips),
                    stopLossSlippageInPips: Number(backtest.stopLossSlippageInPips),
                    trailingStopSlippageInPips: Number(backtest.trailingStopSlippageInPips),
                    volatilitySlippageMultiplier: Number(backtest.volatilitySlippageMultiplier),
                    executionMode: String(backtest.executionMode || 'next_bar_open').trim().toLowerCase() || 'next_bar_open',
                },
            }

            const response = await fetch(buildApiUrl('/strategy/presets/compare'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify(payload),
            })
            const data = await readJsonResponse(response)

            if (response.status === 404) {
                throw new Error('Preset comparison endpoint was not found. Restart the backend to load the latest strategy routes.')
            }

            if (!response.ok || data.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to compare strategy presets.'))
            }

            setPresetComparisonState((current) => ({
                ...current,
                [section]: {
                    loading: false,
                    error: '',
                    comparisons: Array.isArray(data.comparisons) ? data.comparisons : [],
                    bestPresetId: String(data.best_preset_id || ''),
                },
            }))
            onLogEvent?.(`Strategy · ${section === 'long' ? 'Long' : 'Short'} · Compared Market Regime presets.`)
        } catch (error) {
            setPresetComparisonState((current) => ({
                ...current,
                [section]: {
                    ...current[section],
                    loading: false,
                    error: error.message || 'Could not compare presets.',
                },
            }))
            onLogEvent?.(`Strategy preset comparison failed: ${error.message || 'Could not compare presets.'}`)
        }
    }

    function renderTokenSidebar(section) {
        const sidebarTab = activeSidebarTabBySection[section] || 'tokens'
        const isCollapsed = Boolean(sidebarCollapsedBySection[section])

        function handleSidebarResizeStart(event) {
            event.preventDefault()
            sidebarResizeStateRef.current = { pointerId: event.pointerId, section }
            document.body.style.userSelect = 'none'
            document.body.style.cursor = 'ew-resize'
        }

        return (
            <aside
                className={`strategyTokenSidebar ${isCollapsed ? 'isCollapsed' : ''}`}
            >
                <div className='strategyTokenSidebarToolbar'>
                    {!isCollapsed && (
                        <div className='strategyTokenSidebarTitle'>Strategy tools</div>
                    )}
                    <button
                        type='button'
                        className='strategyTokenSidebarCollapse'
                        onClick={() => setSidebarCollapsedBySection((current) => ({ ...current, [section]: !current[section] }))}
                        title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                        aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                    >
                        {isCollapsed ? '‹' : '›'}
                    </button>
                </div>
                {!isCollapsed && (
                    <button
                        type='button'
                        className='strategyTokenSidebarResizeHandle'
                        onPointerDown={handleSidebarResizeStart}
                        aria-hidden='true'
                        tabIndex={-1}
                    />
                )}
                {!isCollapsed && (
                <div className='strategyTokenSidebarLayout'>
                    <div className='strategyTokenSidebarNav'>
                        <button
                            type='button'
                            className={`strategyTokenSidebarNavButton ${sidebarTab === 'tokens' ? 'active' : ''}`}
                            onClick={() => setActiveSidebarTabBySection((current) => ({ ...current, [section]: 'tokens' }))}
                        >
                            Available tokens
                        </button>
                        <button
                            type='button'
                            className={`strategyTokenSidebarNavButton ${sidebarTab === 'presets' ? 'active' : ''}`}
                            onClick={() => setActiveSidebarTabBySection((current) => ({ ...current, [section]: 'presets' }))}
                        >
                            Presets
                        </button>
                    </div>

                    <div className='strategyTokenSidebarContent'>
                        {sidebarTab === 'presets' ? (
                            <>
                                {renderMarketRegimePresetPanel(section)}
                                {renderElliottWavePresetPanel(section)}
                            </>
                        ) : (
                            <>
                                <div className='strategyTokenSidebarHeader'>Available tokens</div>
                                <div className='strategyTokenSidebarGroups'>
                                    {tokenGroups.map((group) => (
                                        <section key={`${section}-${group.id}`} className='strategyTokenSidebarGroup'>
                                            <div className='strategyTokenSidebarGroupTitle'>{group.label}</div>
                                            <div className='strategyTokenSidebarList'>
                                                {group.items.map((item) => (
                                                    <button
                                                        key={`${section}-${group.id}-${item.token}`}
                                                        type='button'
                                                        className='strategyTokenSidebarButton'
                                                        onClick={() => insertTokenCandidate(section, item.token)}
                                                        title={`Insert ${item.token}[0]`}
                                                    >
                                                        <span
                                                            className='strategyTokenSidebarSwatch'
                                                            style={{ backgroundColor: item.color || '#6bb8ff' }}
                                                            aria-hidden='true'
                                                        />
                                                        <span className='strategyTokenSidebarButtonLabel'>{item.token}</span>
                                                    </button>
                                                ))}
                                            </div>
                                        </section>
                                    ))}
                                </div>
                            </>
                        )}
                    </div>
                </div>
                )}
            </aside>
        )
    }

    function getSidebarSectionLayout(section) {
        const isCollapsed = Boolean(sidebarCollapsedBySection[section])

        return {
            className: `strategyPanelSection strategyPanelSectionWithSidebar ${isCollapsed ? 'isSidebarCollapsed' : ''}`,
            style: {
                gridTemplateColumns: isCollapsed
                    ? 'minmax(0, 1fr) 28px'
                    : `minmax(0, 1fr) ${sidebarWidth}px`,
            },
        }
    }

    useEffect(() => {
        if (!insertRequest?.nonce || insertRequest.nonce === handledInsertNonceRef.current) {
            return
        }

        const fieldMap = {
            openPriceLong: ['long', 'openPrice'],
            closePriceLong: ['long', 'closePrice'],
            openLong: ['long', 'openIf'],
            closeLong: ['long', 'closeIf'],
            gainPriceLong: ['long', 'gainPrice'],
            lossPriceLong: ['long', 'lossPrice'],
            trailingPriceLong: ['long', 'trailingPrice'],
            openPriceShort: ['short', 'openPrice'],
            closePriceShort: ['short', 'closePrice'],
            openShort: ['short', 'openIf'],
            closeShort: ['short', 'closeIf'],
            gainPriceShort: ['short', 'gainPrice'],
            lossPriceShort: ['short', 'lossPrice'],
            trailingPriceShort: ['short', 'trailingPrice'],
        }

        const target = fieldMap[insertRequest.fieldId]

        if (!target) {
            handledInsertNonceRef.current = insertRequest.nonce
            return
        }

        const [section, field] = target
        handledInsertNonceRef.current = insertRequest.nonce
        insertTextIntoField(insertRequest.fieldId, section, field, insertRequest.text)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [insertRequest])

    function renderLongPanel() {
        const sidebarLayout = getSidebarSectionLayout('long')

        return (
            <div className={sidebarLayout.className} style={sidebarLayout.style}>
                <div className='strategyPanelSectionMain'>
                <div className='fieldRow'>
                    <PricePresetField
                        fieldId='openPriceLong'
                        label='Open price'
                        section='long'
                        field='openPrice'
                        icon='↗'
                        iconClassName='longOpen'
                        value={getStrategyValue('long', 'openPrice')}
                        tokenCandidates={tokenCandidates}
                        updateStrategyField={updateStrategyField}
                        logFieldChange={logFieldChange}
                        handleFocusField={handleFocusField}
                        updateSelection={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />

                    <PricePresetField
                        fieldId='closePriceLong'
                        label='Close price'
                        section='long'
                        field='closePrice'
                        icon='↘'
                        iconClassName='longClose'
                        value={getStrategyValue('long', 'closePrice')}
                        tokenCandidates={tokenCandidates}
                        updateStrategyField={updateStrategyField}
                        logFieldChange={logFieldChange}
                        handleFocusField={handleFocusField}
                        updateSelection={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />
                </div>

                <div className='field'>
                    <FieldLabel htmlFor='openLong' icon='↗' iconClassName='longOpen'>
                        Open if
                    </FieldLabel>
                    <StrategyTextArea
                        id='openLong'
                        value={getStrategyValue('long', 'openIf')}
                        tokenCandidates={tokenCandidates}
                        onChange={(value) => updateStrategyField('long', 'openIf', value)}
                        onCommit={(value) => logFieldChange('Long', 'Open if', value)}
                        onFocusField={handleFocusField}
                        onSelectionChange={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />
                </div>

                <div className='field'>
                    <FieldLabel htmlFor='closeLong' icon='↘' iconClassName='longClose'>
                        Close if
                    </FieldLabel>
                    <StrategyTextArea
                        id='closeLong'
                        value={getStrategyValue('long', 'closeIf')}
                        tokenCandidates={tokenCandidates}
                        onChange={(value) => updateStrategyField('long', 'closeIf', value)}
                        onCommit={(value) => logFieldChange('Long', 'Close if', value)}
                        onFocusField={handleFocusField}
                        onSelectionChange={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />
                </div>

                <div className='fieldRow'>
                    <div className='field'>
                        <FieldLabel htmlFor='gainPriceLong' icon='▲' iconClassName='longGain'>
                            Take profit price
                        </FieldLabel>
                        <StrategyTextArea
                            id='gainPriceLong'
                            rows={1}
                            value={getStrategyValue('long', 'gainPrice')}
                            tokenCandidates={tokenCandidates}
                            onChange={(value) => updateStrategyField('long', 'gainPrice', value)}
                            onCommit={(value) => logFieldChange('Long', 'Gain price', value)}
                            onFocusField={handleFocusField}
                            onSelectionChange={updateSelection}
                            registerFieldRef={registerFieldRef}
                        />
                    </div>

                    <div className='field'>
                        <FieldLabel htmlFor='lossPriceLong' icon='▼' iconClassName='longLoss'>
                            Stop loss price
                        </FieldLabel>
                        <StrategyTextArea
                            id='lossPriceLong'
                            rows={1}
                            value={getStrategyValue('long', 'lossPrice')}
                            tokenCandidates={tokenCandidates}
                            onChange={(value) => updateStrategyField('long', 'lossPrice', value)}
                            onCommit={(value) => logFieldChange('Long', 'Loss price', value)}
                            onFocusField={handleFocusField}
                            onSelectionChange={updateSelection}
                            registerFieldRef={registerFieldRef}
                        />
                    </div>
                </div>
                <div className='field'>
                    <FieldLabel htmlFor='trailingPriceLong' icon='⇡' iconClassName='longGain'>
                        Trailing stop price
                    </FieldLabel>
                    <StrategyTextArea
                        id='trailingPriceLong'
                        rows={1}
                        value={getStrategyValue('long', 'trailingPrice')}
                        tokenCandidates={tokenCandidates}
                        onChange={(value) => updateStrategyField('long', 'trailingPrice', value)}
                        onCommit={(value) => logFieldChange('Long', 'Trailing stop price', value)}
                        onFocusField={handleFocusField}
                        onSelectionChange={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />
                    <StopFieldHelp />
                </div>
                </div>
                {renderTokenSidebar('long')}
            </div>
        )
    }

    function renderShortPanel() {
        const sidebarLayout = getSidebarSectionLayout('short')

        return (
            <div className={sidebarLayout.className} style={sidebarLayout.style}>
                <div className='strategyPanelSectionMain'>
                <div className='fieldRow'>
                    <PricePresetField
                        fieldId='openPriceShort'
                        label='Open price'
                        section='short'
                        field='openPrice'
                        icon='↘'
                        iconClassName='shortOpen'
                        value={getStrategyValue('short', 'openPrice')}
                        tokenCandidates={tokenCandidates}
                        updateStrategyField={updateStrategyField}
                        logFieldChange={logFieldChange}
                        handleFocusField={handleFocusField}
                        updateSelection={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />

                    <PricePresetField
                        fieldId='closePriceShort'
                        label='Close price'
                        section='short'
                        field='closePrice'
                        icon='↗'
                        iconClassName='shortClose'
                        value={getStrategyValue('short', 'closePrice')}
                        tokenCandidates={tokenCandidates}
                        updateStrategyField={updateStrategyField}
                        logFieldChange={logFieldChange}
                        handleFocusField={handleFocusField}
                        updateSelection={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />
                </div>

                <div className='field'>
                    <FieldLabel htmlFor='openShort' icon='↘' iconClassName='shortOpen'>
                        Open if
                    </FieldLabel>
                    <StrategyTextArea
                        id='openShort'
                        value={getStrategyValue('short', 'openIf')}
                        tokenCandidates={tokenCandidates}
                        onChange={(value) => updateStrategyField('short', 'openIf', value)}
                        onCommit={(value) => logFieldChange('Short', 'Open if', value)}
                        onFocusField={handleFocusField}
                        onSelectionChange={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />
                </div>

                <div className='field'>
                    <FieldLabel htmlFor='closeShort' icon='↗' iconClassName='shortClose'>
                        Close if
                    </FieldLabel>
                    <StrategyTextArea
                        id='closeShort'
                        value={getStrategyValue('short', 'closeIf')}
                        tokenCandidates={tokenCandidates}
                        onChange={(value) => updateStrategyField('short', 'closeIf', value)}
                        onCommit={(value) => logFieldChange('Short', 'Close if', value)}
                        onFocusField={handleFocusField}
                        onSelectionChange={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />
                </div>

                <div className='fieldRow'>
                    <div className='field'>
                        <FieldLabel htmlFor='gainPriceShort' icon='▼' iconClassName='shortGain'>
                            Take profit price
                        </FieldLabel>
                        <StrategyTextArea
                            id='gainPriceShort'
                            rows={1}
                            value={getStrategyValue('short', 'gainPrice')}
                            tokenCandidates={tokenCandidates}
                            onChange={(value) => updateStrategyField('short', 'gainPrice', value)}
                            onCommit={(value) => logFieldChange('Short', 'Gain price', value)}
                            onFocusField={handleFocusField}
                            onSelectionChange={updateSelection}
                            registerFieldRef={registerFieldRef}
                        />
                    </div>

                    <div className='field'>
                        <FieldLabel htmlFor='lossPriceShort' icon='▲' iconClassName='shortLoss'>
                            Stop loss price
                        </FieldLabel>
                        <StrategyTextArea
                            id='lossPriceShort'
                            rows={1}
                            value={getStrategyValue('short', 'lossPrice')}
                            tokenCandidates={tokenCandidates}
                            onChange={(value) => updateStrategyField('short', 'lossPrice', value)}
                            onCommit={(value) => logFieldChange('Short', 'Loss price', value)}
                            onFocusField={handleFocusField}
                            onSelectionChange={updateSelection}
                            registerFieldRef={registerFieldRef}
                        />
                    </div>
                </div>
                <div className='field'>
                    <FieldLabel htmlFor='trailingPriceShort' icon='⇣' iconClassName='shortLoss'>
                        Trailing stop price
                    </FieldLabel>
                    <StrategyTextArea
                        id='trailingPriceShort'
                        rows={1}
                        value={getStrategyValue('short', 'trailingPrice')}
                        tokenCandidates={tokenCandidates}
                        onChange={(value) => updateStrategyField('short', 'trailingPrice', value)}
                        onCommit={(value) => logFieldChange('Short', 'Trailing stop price', value)}
                        onFocusField={handleFocusField}
                        onSelectionChange={updateSelection}
                        registerFieldRef={registerFieldRef}
                    />
                    <StopFieldHelp />
                </div>
                </div>
                {renderTokenSidebar('short')}
            </div>
        )
    }

    function renderOtherPanel() {
        return (
            <div className='strategyPanelSection'>
                <div className='field checkboxField'>
                    <label htmlFor='allowInversion'>Allow inversion</label>
                    <input
                        id='allowInversion'
                        type='checkbox'
                        checked={Boolean(getStrategyValue('other', 'allowInversion'))}
                        onChange={(event) => {
                            updateStrategyField('other', 'allowInversion', event.target.checked)
                            logFieldChange('Other', 'Allow inversion', event.target.checked)
                        }}
                    />
                </div>

                <div className='field'>
                    <label htmlFor='strategyPriority'>Priority</label>
                    <SelectPriority
                        id='strategyPriority'
                        value={getStrategyValue('other', 'priority')}
                        onChange={(value) => {
                            updateStrategyField('other', 'priority', value)
                            logFieldChange('Other', 'Priority', value)
                        }}
                    />
                </div>
            </div>
        )
    }

    const loadStrategyDebugSession = useCallback(async (sessionId, { silent = false } = {}) => {
        const safeSessionId = Number(sessionId || 0)
        if (!authToken || safeSessionId <= 0) {
            return
        }
        const nextAuthHeaders = {
            Authorization: `Bearer ${authToken}`,
        }

        if (!silent) {
            setStrategyDebugState((current) => ({
                ...current,
                loading: true,
                error: '',
            }))
        }

        try {
            const query = new URLSearchParams({
                workspace_id: 'default',
                session_id: String(safeSessionId),
                entry_limit: '600',
            })
            const response = await fetch(buildApiUrl(`/workspace/system-log?${query.toString()}`), {
                headers: nextAuthHeaders,
            })
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to load strategy debug log.'))
            }

            setStrategyDebugState((current) => ({
                ...current,
                loading: false,
                error: '',
                session: normalizeStrategyDebugSession(data.session) || current.session,
                entries: mergeStrategyDebugEntries([], data.entries || []),
                refreshedAt: Date.now(),
            }))
        } catch (error) {
            setStrategyDebugState((current) => ({
                ...current,
                loading: false,
                error: error?.message || 'Failed to load strategy debug log.',
            }))
        }
    }, [authToken])

    async function handleRunStrategyDebug() {
        if (isDebugRunning || isBusy) {
            return
        }
        if (isGuest) {
            setStrategyDebugState((current) => ({
                ...current,
                error: guestRestrictionMessage,
                loading: false,
                refreshedAt: Date.now(),
            }))
            onLogEvent?.(`Strategy debug · ${guestRestrictionMessage}`)
            return
        }

        setIsDebugRunning(true)
        setStrategyViewTab('debug')
        setStrategyDebugState({
            loading: true,
            error: '',
            session: null,
            entries: [],
            payload: null,
            refreshedAt: Date.now(),
        })
        onStrategyStatusChange?.({
            error: '',
            strategyPending: true,
            strategyDebugPending: true,
            strategyDebugError: '',
            strategyDebugReady: false,
            backtestBusy: false,
            backtestPending: false,
            resultsPending: false,
        })

        try {
            const startedAt = performance.now()
            const editorStrategy = mergeStrategyDefaults(strategy)
            const debugChartSettings = mergeChartIndicatorsWithInferred(chartSettings, editorStrategy)
            const resolvedStrategy = resolveStrategyAliasesInStrategy(editorStrategy, debugChartSettings)
            const response = await fetch(buildApiUrl('/strategy/debug'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({
                    draft_label: String(currentStrategyLabel || '').trim(),
                    strategy: resolvedStrategy,
                    backtest,
                    chart: {
                        symbol: String(debugChartSettings?.symbol || chartSettings?.symbol || 'EURUSD').trim().toUpperCase() || 'EURUSD',
                        timeframe: String(debugChartSettings?.timeframe || chartSettings?.timeframe || 'M1').trim().toUpperCase() || 'M1',
                        bars: resolveStrategyChartRequestBars(debugChartSettings),
                        indicators: buildBackendIndicatorsPayload(debugChartSettings?.indicators || []),
                    },
                }),
            })
            const data = await readJsonResponse(response)
            if (!response.ok) {
                throw new Error(extractApiErrorMessage(data, 'Failed to run strategy debug.'))
            }

            const nextSession = normalizeStrategyDebugSession(data?.debug_session)
            const nextEntries = mergeStrategyDebugEntries([], data?.debug_entries || [])
            setStrategyDebugState({
                loading: false,
                error: String(data?.error || '').trim(),
                session: nextSession,
                entries: nextEntries,
                payload: data && typeof data === 'object' ? data : null,
                refreshedAt: Date.now(),
            })
            onStrategyStatusChange?.({
                error: String(data?.error || '').trim(),
                strategyPending: false,
                strategyDebugPending: false,
                strategyDebugError: String(data?.error || '').trim(),
                strategyDebugReady: Boolean(data?.status === 'ok' && nextSession && nextEntries.length > 0),
                backtestBusy: false,
                backtestPending: false,
                resultsPending: false,
            })
            onLogEvent?.(
                data?.status === 'ok'
                    ? `Strategy debug completed (${((performance.now() - startedAt) / 1000).toFixed(2)}s).`
                    : `Strategy debug finished with errors (${((performance.now() - startedAt) / 1000).toFixed(2)}s).`
            )
        } catch (error) {
            console.error('Failed to run strategy debug:', error)
            setStrategyDebugState((current) => ({
                ...current,
                loading: false,
                error: error?.message || 'Could not run strategy debug.',
            }))
            onStrategyStatusChange?.({
                error: error?.message || 'Could not run strategy debug.',
                strategyPending: false,
                strategyDebugPending: false,
                strategyDebugError: error?.message || 'Could not run strategy debug.',
                strategyDebugReady: false,
                backtestBusy: false,
                backtestPending: false,
                resultsPending: false,
            })
            onLogEvent?.(`Strategy debug failed: ${error?.message || 'Could not run strategy debug.'}`)
        } finally {
            setIsDebugRunning(false)
        }
    }

    async function handleImportStrategyFromClipboard() {
        try {
            const clipboardText = await navigator.clipboard.readText()
            const trimmedClipboardText = String(clipboardText || '').trim()
            if (!trimmedClipboardText) {
                throw new Error('Clipboard is empty.')
            }

            let parsed
            try {
                parsed = JSON.parse(trimmedClipboardText)
            } catch {
                throw new Error('Clipboard does not contain valid JSON for a strategy import.')
            }
            const importedStrategy = parsed?.strategy && typeof parsed.strategy === 'object'
                ? parsed.strategy
                : parsed

            const looksLikeStrategy = Boolean(
                importedStrategy
                && typeof importedStrategy === 'object'
                && (
                    typeof importedStrategy.long === 'object'
                    || typeof importedStrategy.short === 'object'
                    || typeof importedStrategy.other === 'object'
                )
            )
            const looksLikeNeuralConfig = Boolean(
                parsed
                && typeof parsed === 'object'
                && !Array.isArray(parsed)
                && 'symbol' in parsed
                && 'timeframe' in parsed
                && (
                    'hiddenLayers' in parsed
                    || 'targetHorizon' in parsed
                    || 'validationSplit' in parsed
                )
            )

            if (looksLikeNeuralConfig) {
                throw new Error('Clipboard JSON looks like a neural config. Use "Import config from clipboard" in the Neural panel.')
            }

            if (!looksLikeStrategy) {
                throw new Error('Clipboard JSON does not contain a valid strategy payload.')
            }

            const nextStrategy = sanitizeImportedStrategy(importedStrategy)
            setStrategy(nextStrategy)
            selectEditorTabForStrategy(nextStrategy)

            if (typeof setBacktest === 'function' && parsed?.backtest && typeof parsed.backtest === 'object') {
                setBacktest(sanitizeImportedBacktest(parsed.backtest))
            }

            onLogEvent?.(
                parsed?.backtest && typeof setBacktest === 'function'
                    ? 'Strategy and backtest imported from clipboard.'
                    : 'Strategy imported from clipboard.'
            )
        } catch (error) {
            console.error('Failed to import strategy from clipboard:', error)
            onLogEvent?.(`Strategy import failed: ${error.message || 'Could not read clipboard JSON.'}`)
        }
    }

    useEffect(() => {
        setStrategy((prev) => mergeStrategyDefaults(prev))
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [setStrategy])

    async function refreshStrategyLibrary({ quiet = false } = {}) {
        if (!authToken) {
            return
        }
        if (!quiet) {
            setIsStrategyLibraryLoading(true)
        }
        try {
            const response = await fetch(
                buildApiUrl(`/workspace/strategy-benchmarks?${buildBrokerProfileQuery({
                    workspaceId: 'default',
                    limit: STRATEGY_LIBRARY_FETCH_LIMIT,
                    brokerProfileId: activeBrokerProfileId,
                })}`),
                {
                    headers: authHeaders,
                }
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to load saved strategies.'))
            }
            const benchmarks = Array.isArray(data?.benchmarks) ? data.benchmarks : []
            setStrategyLibraryItems(benchmarks)
            setSelectedStrategyLibraryId((current) => {
                if (current && benchmarks.some((entry) => String(entry?.id) === String(current))) {
                    return current
                }
                return String(benchmarks[0]?.id || '')
            })
        } catch (error) {
            onLogEvent?.(`Strategy library · ${error?.message || 'Failed to load saved strategies.'}`)
        } finally {
            if (!quiet) {
                setIsStrategyLibraryLoading(false)
            }
        }
    }

    useEffect(() => {
        void refreshStrategyLibrary({ quiet: false })
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authToken, activeBrokerProfileId])

    useEffect(() => {
        setSelectedStrategyLibraryId((current) => {
            if (current && visibleStrategyLibraryItems.some((entry) => String(entry?.id) === String(current))) {
                return current
            }
            return String(visibleStrategyLibraryItems[0]?.id || '')
        })
    }, [visibleStrategyLibraryItems])

    useEffect(() => {
        strategyStatusChangeRef.current = onStrategyStatusChange
    }, [onStrategyStatusChange])

    useEffect(() => {
        if (!authToken || !isActive || isGuest) {
            return
        }

        let cancelled = false
        const nextAuthHeaders = {
            Authorization: `Bearer ${authToken}`,
        }

        async function loadLatestStrategyDebugSession() {
            try {
                const response = await fetch(buildApiUrl('/workspace/system-log/sessions?workspace_id=default&limit=40'), {
                    headers: nextAuthHeaders,
                })
                const data = await readJsonResponse(response)
                if (!response.ok || data?.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(data, 'Failed to load strategy debug sessions.'))
                }
                if (cancelled) {
                    return
                }

                const latestSession = (Array.isArray(data?.sessions) ? data.sessions : [])
                    .map((session) => normalizeStrategyDebugSession(session))
                    .filter((session) => session && session.source === STRATEGY_DEBUG_SOURCE)
                    .sort((left, right) => Number(right.updatedAt || 0) - Number(left.updatedAt || 0))[0]

                if (!latestSession) {
                    return
                }

                setStrategyDebugState((current) => {
                    if (current.session && Number(current.session.id || 0) === Number(latestSession.id || 0)) {
                        return current
                    }
                    return {
                        ...current,
                        session: latestSession,
                    }
                })
                await loadStrategyDebugSession(latestSession.id, { silent: true })
            } catch {
                // keep local debug state if the latest persisted session cannot be loaded
            }
        }

        void loadLatestStrategyDebugSession()

        return () => {
            cancelled = true
        }
    }, [authToken, isActive, isGuest, loadStrategyDebugSession])

    useEffect(() => {
        const debugPayload = strategyDebugState.payload && typeof strategyDebugState.payload === 'object'
            ? strategyDebugState.payload
            : null
        const hasLoadedDebugLog = Boolean(
            strategyDebugState.session
            && Array.isArray(strategyDebugState.entries)
            && strategyDebugState.entries.length > 0
        )
        const debugReady = Boolean(
            !isDebugRunning
            && !strategyDebugState.loading
            && !strategyDebugState.error
            && debugPayload?.status === 'ok'
            && hasLoadedDebugLog
        )
        const debugError = String(
            strategyDebugState.error
            || (
                !isDebugRunning && !strategyDebugState.loading && debugPayload && debugPayload.status !== 'ok'
                    ? (debugPayload.error || 'Strategy debug failed.')
                    : ''
            )
            || ''
        ).trim()

        strategyStatusChangeRef.current?.({
            strategyPending: isDebugRunning,
            strategyDebugPending: isDebugRunning,
            strategyDebugError: debugError,
            strategyDebugReady: debugReady,
        })
    }, [
        isDebugRunning,
        strategyDebugState.entries,
        strategyDebugState.error,
        strategyDebugState.loading,
        strategyDebugState.payload,
        strategyDebugState.session,
    ])

    useEffect(() => {
        function handleSystemLogAppended(event) {
            const detail = event?.detail && typeof event.detail === 'object' ? event.detail : {}
            const session = normalizeStrategyDebugSession(detail.session)
            if (!session || session.source !== STRATEGY_DEBUG_SOURCE) {
                return
            }

            setStrategyDebugState((current) => {
                if (!current.session || Number(current.session.id || 0) !== Number(session.id || 0)) {
                    return current
                }

                return {
                    ...current,
                    session,
                    entries: mergeStrategyDebugEntries(current.entries, detail.entries || []),
                    refreshedAt: Date.now(),
                }
            })
        }

        window.addEventListener('workspace:system-log-appended', handleSystemLogAppended)
        return () => window.removeEventListener('workspace:system-log-appended', handleSystemLogAppended)
    }, [])

    useEffect(() => {
        if (!Array.isArray(strategyLibraryItems) || !strategyLibraryItems.length) {
            return
        }

        const currentSerialized = serializeStrategyLibraryEntry({
            strategy,
            strategies: backtestStrategySet,
        })
        const matchedEntry = strategyLibraryItems.find((entry) => (
            serializeStrategyLibraryEntry(entry) === currentSerialized
        ))

        if (matchedEntry?.label) {
            const nextLabel = String(matchedEntry.label || '').trim()
            if (nextLabel && (isGenericStrategyLabel(currentStrategyLabel) || String(currentStrategyLabel || '').trim() !== nextLabel)) {
                onStrategyLabelChange?.(nextLabel)
            }
            return
        }

        if (isGenericStrategyLabel(currentStrategyLabel)) {
            onStrategyLabelChange?.('')
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [strategy, backtestStrategySet, strategyLibraryItems, currentStrategyLabel, onStrategyLabelChange])

    function handleResetStrategyFields() {
        const nextStrategy = buildBlankStrategy()
        setStrategy(nextStrategy)
        selectEditorTabForStrategy(nextStrategy, 'long')
        onStrategyLabelChange?.('')
        onLogEvent?.('Strategy · Reset all fields to defaults.')
    }

    async function handleSaveStrategyToLibrary() {
        if (!authToken || isDebugRunning || isBusy || isStrategyLibrarySaving) {
            return
        }
        if (isGuest) {
            onLogEvent?.(`Strategy library · ${guestRestrictionMessage}`)
            return
        }
        const safeLabel = String(strategySaveLabel || '').trim()
        if (!safeLabel) {
            onLogEvent?.('Strategy library · Give the strategy a name before saving.')
            return
        }
        setIsStrategyLibrarySaving(true)
        try {
            const response = await fetch(buildApiUrl('/workspace/strategy-benchmarks'), {
                method: 'POST',
                headers: {
                    ...authHeaders,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    workspace_id: 'default',
                    is_favorite: false,
                    broker_profile_id: activeBrokerProfileId || undefined,
                    ...buildStrategyBenchmarkPayload({
                        label: safeLabel,
                        notes: String(strategySaveNotes || '').trim(),
                        source: 'strategy-tab',
                        side: 'both',
                        strategy,
                        strategies: backtestStrategySet,
                        chartSettings,
                    }),
                }),
            })
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to save strategy.'))
            }
            onLogEvent?.(`Strategy library · Saved "${safeLabel}".`)
            setStrategyLibraryTab('load')
            await refreshStrategyLibrary({ quiet: true })
            setSelectedStrategyLibraryId(String(data?.benchmark?.id || ''))
        } catch (error) {
            onLogEvent?.(`Strategy library · ${error?.message || 'Failed to save strategy.'}`)
        } finally {
            setIsStrategyLibrarySaving(false)
        }
    }

    async function handleToggleFavoriteStrategyInLibrary(
        targetEntry = selectedStrategyLibraryItem,
        {
            nextIsFavorite = !targetEntry?.is_favorite,
            logMessage = '',
        } = {},
    ) {
        if (!authToken || !targetEntry?.id) {
            return
        }
        if (isGuest) {
            onLogEvent?.(`Strategy library · ${guestRestrictionMessage}`)
            return
        }

        try {
            const response = await fetch(
                buildApiUrl(`/workspace/strategy-benchmarks/${targetEntry.id}?workspace_id=default`),
                {
                    method: 'PATCH',
                    headers: {
                        ...authHeaders,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        workspace_id: 'default',
                        is_favorite: Boolean(nextIsFavorite),
                    }),
                }
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to update favorite strategy.'))
            }
            onLogEvent?.(
                logMessage
                || (nextIsFavorite
                    ? `Strategy library · Marked "${targetEntry.label || `Strategy #${targetEntry.id}`}" as favorite.`
                    : `Strategy library · Removed "${targetEntry.label || `Strategy #${targetEntry.id}`}" from favorites.`)
            )
            await refreshStrategyLibrary({ quiet: true })
            setSelectedStrategyLibraryId(String(targetEntry.id))
        } catch (error) {
            onLogEvent?.(`Strategy library · ${error?.message || 'Failed to update favorite strategy.'}`)
        }
    }

    function handleLoadStrategyFromLibrary() {
        const selected = strategyLibraryItems.find((entry) => String(entry?.id) === String(selectedStrategyLibraryId))
        if (!selected?.strategy) {
            onLogEvent?.('Strategy library · Select a saved strategy to load.')
            return
        }
        const libraryChartSettings = buildStrategyCollectionChartSettings(
            chartSettings,
            selected.strategy || {},
            selected?.strategies || [],
        )
        const nextStrategy = sanitizeImportedStrategy(selected.strategy, libraryChartSettings)
        const nextStrategySet = sanitizeImportedStrategySet(selected?.strategies || [], libraryChartSettings)
        setStrategy(nextStrategy)
        selectEditorTabForStrategy(nextStrategy, selected?.side)
        if (typeof setBacktestStrategySet === 'function') {
            setBacktestStrategySet(nextStrategySet)
        }
        onStrategyLabelChange?.(String(selected.label || '').trim())
        setStrategyViewTab('editor')
        onLogEvent?.(`Strategy library · Loaded "${selected.label || 'saved strategy'}".`)
    }

    async function handleDeleteStrategyFromLibrary() {
        if (!authToken || !selectedStrategyLibraryId) {
            return
        }
        if (isGuest) {
            onLogEvent?.(`Strategy library · ${guestRestrictionMessage}`)
            return
        }
        const selected = strategyLibraryItems.find((entry) => String(entry?.id) === String(selectedStrategyLibraryId))
        const confirmed = window.confirm(`Delete saved strategy "${selected?.label || selectedStrategyLibraryId}"?`)
        if (!confirmed) {
            return
        }
        try {
            const response = await fetch(
                buildApiUrl(`/workspace/strategy-benchmarks/${selectedStrategyLibraryId}?workspace_id=default`),
                {
                    method: 'DELETE',
                    headers: authHeaders,
                }
            )
            const data = await readJsonResponse(response)
            if (!response.ok || data?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(data, 'Failed to delete saved strategy.'))
            }
            onLogEvent?.(`Strategy library · Deleted "${selected?.label || selectedStrategyLibraryId}".`)
            await refreshStrategyLibrary({ quiet: true })
        } catch (error) {
            onLogEvent?.(`Strategy library · ${error?.message || 'Failed to delete saved strategy.'}`)
        }
    }

    function renderDebugPanel() {
        const debugChartSettings = mergeChartIndicatorsWithInferred(chartSettings, strategy)
        const debugPayload = strategyDebugState.payload && typeof strategyDebugState.payload === 'object'
            ? strategyDebugState.payload
            : null
        const debugStats = debugPayload?.stats && typeof debugPayload.stats === 'object'
            ? debugPayload.stats
            : {}
        const debugSession = strategyDebugState.session
        const debugEntries = Array.isArray(strategyDebugState.entries) ? strategyDebugState.entries : []
        const appliedIndicators = Array.isArray(debugPayload?.applied_indicators) ? debugPayload.applied_indicators : []
        const availableColumns = Array.isArray(debugPayload?.available_columns) ? debugPayload.available_columns : []
        const requiredFeatures = Array.isArray(debugPayload?.strategy_view_meta?.required_features)
            ? debugPayload.strategy_view_meta.required_features
            : []
        const chartBars = resolveStrategyChartRequestBars(debugChartSettings)

        return (
            <div className='strategyDebugShell'>
                <section className='strategyDebugCard'>
                    <div className='strategyDebugCardHeader'>
                        <div>
                            <div className='strategyDebugTitle'>Draft debug</div>
                            <div className='strategyDebugSubtitle'>
                                Debug always runs against the current chart context and opens a new persisted debug session.
                            </div>
                        </div>
                        <div className='strategyDebugActions'>
                            <button
                                type='button'
                                className='strategyDebugSecondaryButton'
                                onClick={() => void onLoadStrategyIndicators?.(strategy, {
                                    label: String(currentStrategyLabel || '').trim() || 'Current strategy',
                                })}
                                disabled={isDebugRunning || isBusy}
                            >
                                Load indicators
                            </button>
                            <button
                                type='button'
                                className='strategyDebugPrimaryButton'
                                onClick={() => void handleRunStrategyDebug()}
                                disabled={isDebugRunning || isBusy}
                            >
                                {isDebugRunning ? 'Running debug...' : 'Run debug'}
                            </button>
                            <button
                                type='button'
                                className='strategyDebugSecondaryButton'
                                onClick={() => void loadStrategyDebugSession(strategyDebugState.session?.id)}
                                disabled={isDebugRunning || strategyDebugState.loading || !strategyDebugState.session?.id}
                            >
                                {strategyDebugState.loading && strategyDebugState.session?.id ? 'Refreshing...' : 'Refresh log'}
                            </button>
                        </div>
                    </div>

                    <div className='strategyDebugSummaryGrid'>
                        <div className='strategyDebugSummaryItem'>
                            <span>Current symbol</span>
                            <strong>{String(debugChartSettings?.symbol || chartSettings?.symbol || 'EURUSD').toUpperCase()}</strong>
                        </div>
                        <div className='strategyDebugSummaryItem'>
                            <span>Current timeframe</span>
                            <strong>{String(debugChartSettings?.timeframe || chartSettings?.timeframe || 'M1').toUpperCase()}</strong>
                        </div>
                        <div className='strategyDebugSummaryItem'>
                            <span>Chart bars</span>
                            <strong>{chartBars.toLocaleString()}</strong>
                        </div>
                        <div className='strategyDebugSummaryItem'>
                            <span>Resolved indicators</span>
                            <strong>{Array.isArray(debugChartSettings?.indicators) ? debugChartSettings.indicators.length.toLocaleString() : '0'}</strong>
                        </div>
                    </div>

                    {strategyDebugState.error ? (
                        <div className='strategyDebugMessage isError'>{strategyDebugState.error}</div>
                    ) : null}

                    {debugPayload ? (
                        <div className='strategyDebugResultGrid'>
                            <div className='strategyDebugResultCard'>
                                <span>Status</span>
                                <strong className={debugPayload.status === 'ok' ? 'isSuccess' : 'isError'}>
                                    {debugPayload.status === 'ok' ? 'OK' : String(debugPayload.status || 'error').trim() || 'error'}
                                </strong>
                            </div>
                            <div className='strategyDebugResultCard'>
                                <span>Rows</span>
                                <strong>{formatDebugMetric(debugPayload.rows, { style: 'integer' })}</strong>
                            </div>
                            <div className='strategyDebugResultCard'>
                                <span>Trades</span>
                                <strong>{formatDebugMetric(debugStats.n_trades, { style: 'integer' })}</strong>
                            </div>
                            <div className='strategyDebugResultCard'>
                                <span>Net PnL</span>
                                <strong>{formatDebugMetric(debugStats.net_pnl)}</strong>
                            </div>
                            <div className='strategyDebugResultCard'>
                                <span>Win rate</span>
                                <strong>{formatDebugMetric(debugStats.win_rate, { style: 'percent' })}</strong>
                            </div>
                            <div className='strategyDebugResultCard'>
                                <span>Required features</span>
                                <strong>{requiredFeatures.length.toLocaleString()}</strong>
                            </div>
                            <div className='strategyDebugResultCard'>
                                <span>Applied indicators</span>
                                <strong>{appliedIndicators.length.toLocaleString()}</strong>
                            </div>
                            <div className='strategyDebugResultCard'>
                                <span>Available columns</span>
                                <strong>{availableColumns.length.toLocaleString()}</strong>
                            </div>
                        </div>
                    ) : null}
                </section>

                <section className='strategyDebugCard'>
                    <div className='strategyDebugCardHeader'>
                        <div>
                            <div className='strategyDebugTitle'>Debug log</div>
                            <div className='strategyDebugSubtitle'>
                                {debugSession
                                    ? `Session #${debugSession.id} · ${debugSession.label}`
                                    : 'No debug session yet.'}
                            </div>
                        </div>
                        <div className='strategyDebugLogMeta'>
                            <span>{debugEntries.length.toLocaleString()} entries</span>
                            <span>Updated {formatDebugTimestamp(strategyDebugState.refreshedAt)}</span>
                        </div>
                    </div>
                    <div className='strategyDebugLogList'>
                        {!debugEntries.length ? (
                            <div className='strategyDebugLogEmpty'>
                                Run debug to create a fresh persisted session and inspect the backend response here.
                            </div>
                        ) : debugEntries.map((entry) => (
                            <article key={entry.id} className={`strategyDebugLogEntry is-${entry.level}`.trim()}>
                                <div className='strategyDebugLogEntryHeader'>
                                    <strong>{String(entry.level || 'info').trim().toUpperCase()}</strong>
                                    <span>{formatDebugTimestamp(entry.createdAt)}</span>
                                </div>
                                <div className='strategyDebugLogEntryMessage'>{entry.message}</div>
                            </article>
                        ))}
                    </div>
                </section>
            </div>
        )
    }

    return (
        <div className={`Strategy ${isActive ? 'active' : ''}`}>
            <div className='strategyViewHeader'>
                <div className='strategyViewTabs batchTabs batchTabsInline'>
                    <button
                        type='button'
                        className={`strategyViewTabButton batchTabButton ${strategyViewTab === 'manager' ? 'active' : ''}`.trim()}
                        onClick={() => setStrategyViewTab('manager')}
                    >
                        Manager
                    </button>
                    <button
                        type='button'
                        className={`strategyViewTabButton batchTabButton ${strategyViewTab === 'editor' ? 'active' : ''}`.trim()}
                        onClick={() => setStrategyViewTab('editor')}
                    >
                        Editor
                    </button>
                    {!isGuest ? (
                        <button
                            type='button'
                            className={`strategyViewTabButton batchTabButton ${strategyViewTab === 'debug' ? 'active' : ''}`.trim()}
                            onClick={() => setStrategyViewTab('debug')}
                        >
                            Debug
                        </button>
                    ) : null}
                </div>
                <div className='strategyViewHeaderActions'>
                    {isGuest ? <div className='strategyGuestNotice'>{guestRestrictionMessage}</div> : null}
                    <div className='strategyLoadedNameBanner'>
                        <strong>
                            STRATEGY LOADED: <span>{String(currentStrategyLabel || '').trim() || 'Current editor strategy'}</span>
                        </strong>
                    </div>
                </div>
            </div>

            {strategyViewTab === 'manager' ? (
                <div className='strategyManagerShell'>
                    <aside className='strategyManagerSidebar'>
                        <div className='strategyManagerTabs'>
                            {!isGuest ? (
                                <button
                                    type='button'
                                    className={strategyLibraryTab === 'save' ? 'active' : ''}
                                    onClick={() => setStrategyLibraryTab('save')}
                                >
                                    Save
                                </button>
                            ) : null}
                            <button
                                type='button'
                                className={strategyLibraryTab === 'load' ? 'active' : ''}
                                onClick={() => setStrategyLibraryTab('load')}
                            >
                                Load
                            </button>
                        </div>
                        <div className='strategyManagerToolbar'>
                            <div className='strategyManagerToolbarTopRow'>
                                <div className='strategyManagerListTabs'>
                                    <button
                                        type='button'
                                        className={strategyLibraryListTab === 'all' ? 'active' : ''}
                                        onClick={() => setStrategyLibraryListTab('all')}
                                    >
                                        All
                                    </button>
                                    <button
                                        type='button'
                                        className={strategyLibraryListTab === 'favorites' ? 'active' : ''}
                                        onClick={() => setStrategyLibraryListTab('favorites')}
                                    >
                                        Favorites
                                    </button>
                                </div>
                                <button
                                    type='button'
                                    className='strategyToolbarButton'
                                    onClick={() => void refreshStrategyLibrary({ quiet: false })}
                                    disabled={isStrategyLibraryLoading}
                                >
                                    {isStrategyLibraryLoading ? 'Refreshing...' : 'Refresh'}
                                </button>
                            </div>
                            <div className='strategyManagerSearchRow'>
                                <input
                                    id='strategyLibrarySearch'
                                    type='text'
                                    value={strategyLibraryQuery}
                                    onChange={(event) => setStrategyLibraryQuery(event.target.value)}
                                    placeholder='Filter saved strategies'
                                    aria-label='Filter saved strategies'
                                />
                                {strategyLibraryQuery ? (
                                    <button
                                        type='button'
                                        className='strategyManagerSearchClear'
                                        onClick={() => setStrategyLibraryQuery('')}
                                        aria-label='Clear strategy filter'
                                        title='Clear strategy filter'
                                    >
                                        Clear
                                    </button>
                                ) : null}
                            </div>
                        </div>
                        <div className='strategyManagerList'>
                            {!strategyLibraryItems.length ? (
                                <div className='strategyManagerEmpty'>No saved strategies yet.</div>
                            ) : normalizedStrategyLibraryQuery && !visibleStrategyLibraryItems.length ? (
                                <div className='strategyManagerEmpty'>No saved strategies match this filter.</div>
                            ) : !visibleStrategyLibraryItems.length ? (
                                <div className='strategyManagerEmpty'>No favorite strategies yet.</div>
                            ) : visibleStrategyLibraryItems.map((entry) => (
                                <div key={entry.id} className='strategyManagerListEntry'>
                                    <button
                                        type='button'
                                        className={`strategyManagerListSelect ${String(selectedStrategyLibraryId) === String(entry.id) ? 'active' : ''}`.trim()}
                                        onClick={() => setSelectedStrategyLibraryId(String(entry.id))}
                                    >
                                        <div className='strategyManagerEntryHeader'>
                                            <strong className='strategyManagerEntryLabel'>
                                                {entry.is_favorite ? <span className='strategyManagerFavoriteStar' aria-hidden='true'>★</span> : null}
                                                <span>{entry.label || `Strategy #${entry.id}`}</span>
                                            </strong>
                                            {entry.is_favorite ? <span className='strategyManagerFavoriteBadge'>Favorite</span> : null}
                                        </div>
                                        <span>{entry.source || 'manual'}{entry.side ? ` · ${entry.side}` : ''}</span>
                                        <small>{entry.notes || 'No notes provided.'}</small>
                                    </button>
                                    <button
                                        type='button'
                                        className={`strategyManagerFavoriteToggle ${entry.is_favorite ? 'active' : ''}`.trim()}
                                        onClick={() => void handleToggleFavoriteStrategyInLibrary(entry)}
                                        disabled={isGuest}
                                        title={entry.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
                                        aria-label={entry.is_favorite ? `Remove ${entry.label || `Strategy #${entry.id}`} from favorites` : `Add ${entry.label || `Strategy #${entry.id}`} to favorites`}
                                    >
                                        ★
                                    </button>
                                </div>
                            ))}
                        </div>
                    </aside>

                    <section className='strategyManagerContent'>
                        {strategyLibraryTab === 'save' ? (
                            <div className='strategyManagerPanel'>
                                <div className='strategyManagerPanelTitle'>Save current strategy</div>
                                <div className='strategyManagerField'>
                                    <label htmlFor='strategyManagerSaveName'>Strategy name</label>
                                    <input
                                        id='strategyManagerSaveName'
                                        type='text'
                                        value={strategySaveLabel}
                                        onChange={(event) => setStrategySaveLabel(event.target.value)}
                                        placeholder='Saved strategy'
                                    />
                                </div>
                                <div className='strategyManagerField'>
                                    <label htmlFor='strategyManagerSaveNotes'>Notes</label>
                                    <input
                                        id='strategyManagerSaveNotes'
                                        type='text'
                                        value={strategySaveNotes}
                                        onChange={(event) => setStrategySaveNotes(event.target.value)}
                                        placeholder='Optional notes'
                                    />
                                </div>
                                <div className='strategyManagerDetailGrid'>
                                    <div className='strategyManagerDetailLabel'>Current symbol</div>
                                    <div>{String(chartSettings?.symbol || 'EURUSD').toUpperCase()}</div>
                                    <div className='strategyManagerDetailLabel'>Current timeframe</div>
                                    <div>{String(chartSettings?.timeframe || 'M1').toUpperCase()}</div>
                                    <div className='strategyManagerDetailLabel'>What gets saved</div>
                                    <div>Only the strategy definition. Workspace continuity is handled separately by persistent workspace state.</div>
                                </div>
                                <div className='strategyManagerActions'>
                                            <button
                                                type='button'
                                                className='strategyManagerPrimary'
                                                onClick={() => void handleSaveStrategyToLibrary()}
                                                disabled={isGuest || isDebugRunning || isBusy || isStrategyLibrarySaving || !String(strategySaveLabel || '').trim()}
                                                title={isGuest ? guestRestrictionMessage : undefined}
                                            >
                                                {isStrategyLibrarySaving ? 'Saving strategy...' : 'Save strategy'}
                                            </button>
                                </div>
                            </div>
                        ) : (
                            <div className='strategyManagerPanel'>
                                <div className='strategyManagerPanelTitle'>Load saved strategy</div>
                                {!selectedStrategyLibraryItem ? (
                                    <div className='strategyManagerEmptyState'>Select a saved strategy on the left.</div>
                                ) : (
                                    <>
                                        <div className='strategyManagerDetailGrid'>
                                            <div className='strategyManagerDetailLabel'>Name</div>
                                            <div className='strategyManagerEntryLabel'>
                                                {selectedStrategyLibraryItem.is_favorite ? <span className='strategyManagerFavoriteStar' aria-hidden='true'>★</span> : null}
                                                <span>{selectedStrategyLibraryItem.label || `Strategy #${selectedStrategyLibraryItem.id}`}</span>
                                            </div>
                                            <div className='strategyManagerDetailLabel'>Favorite</div>
                                            <div>{selectedStrategyLibraryItem.is_favorite ? 'Yes' : 'No'}</div>
                                            <div className='strategyManagerDetailLabel'>Source</div>
                                            <div>{selectedStrategyLibraryItem.source || 'manual'}</div>
                                            <div className='strategyManagerDetailLabel'>Side</div>
                                            <div>{selectedStrategyLibraryItem.side || 'both'}</div>
                                            <div className='strategyManagerDetailLabel'>Notes</div>
                                            <div>{selectedStrategyLibraryItem.notes || 'No notes provided.'}</div>
                                        </div>
                                        <div className='strategyManagerActions'>
                                            <button
                                                type='button'
                                                className='strategyManagerPrimary'
                                                onClick={handleLoadStrategyFromLibrary}
                                            >
                                                Load strategy
                                            </button>
                                            <button
                                                type='button'
                                                onClick={() => void handleToggleFavoriteStrategyInLibrary()}
                                                disabled={isGuest}
                                                title={isGuest ? guestRestrictionMessage : undefined}
                                            >
                                                {selectedStrategyLibraryItem.is_favorite ? 'Unfavorite' : 'Favorite'}
                                            </button>
                                            <button
                                                type='button'
                                                onClick={() => void handleDeleteStrategyFromLibrary()}
                                                disabled={isGuest}
                                                title={isGuest ? guestRestrictionMessage : undefined}
                                            >
                                                Delete
                                            </button>
                                        </div>
                                    </>
                                )}
                            </div>
                        )}
                    </section>
                </div>
            ) : null}

            {strategyViewTab === 'editor' ? (
                <>
            <div className='strategyPanelToolbar'>
                <div className='strategyPanelTabs'>
                    {['Long', 'Short', 'Other'].map((tab) => (
                        <button
                            key={tab}
                            type='button'
                            className={`strategyPanelTab ${activeTab === tab ? 'active' : ''}`}
                            onClick={() => setActiveTab(tab)}
                        >
                            <span className={`strategyPanelTabIcon ${tab.toLowerCase()}`} aria-hidden='true'>
                                {tab === 'Long' ? '↗' : tab === 'Short' ? '↘' : '•'}
                            </span>
                            {tab}
                        </button>
                    ))}
                </div>
                <div className='strategyActions'>
                    <button
                        type='button'
                        className='strategyToolbarButton'
                        onClick={handleResetStrategyFields}
                        disabled={isDebugRunning || isBusy}
                    >
                        Reset all fields to defaults
                    </button>
                    <button
                        type='button'
                        className='strategyToolbarButton'
                        onClick={() => void handleImportStrategyFromClipboard()}
                        disabled={isDebugRunning || isBusy}
                    >
                        Import strategy from clipboard
                    </button>
                </div>
            </div>

            <div className='strategyPanel'>
                {activeTab === 'Long' && renderLongPanel()}
                {activeTab === 'Short' && renderShortPanel()}
                {activeTab === 'Other' && renderOtherPanel()}
            </div>
                </>
            ) : null}

            {strategyViewTab === 'debug' ? renderDebugPanel() : null}
        </div>
    )
}

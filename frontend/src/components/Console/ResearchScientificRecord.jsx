import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import './Docs.css'
import './ResearchScientificRecord.css'
import {
    buildBlankScientificPaperDraft,
    buildDefaultScientificPaperSeeds,
    normalizeScientificArticle,
} from './researchScientificSeeds.js'

function ScientificBadge({ label = '', tone = 'neutral' }) {
    if (!label) {
        return null
    }

    return (
        <span className={`docsBadge tone-${tone}`}>
            {label}
        </span>
    )
}

function renderInlineMarkdown(text, keyBase = 'inline') {
    const source = String(text || '')
    if (!source) {
        return ''
    }

    const fragments = []
    const pattern = /(\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*)/g
    let lastIndex = 0
    let match = null

    while ((match = pattern.exec(source)) !== null) {
        if (match.index > lastIndex) {
            fragments.push(source.slice(lastIndex, match.index))
        }

        if (match[2] !== undefined) {
            fragments.push(
                <span
                    key={`${keyBase}-link-${match.index}`}
                    className='docsInlineLink'
                    title={match[3]}
                >
                    {match[2]}
                </span>
            )
        } else if (match[4] !== undefined) {
            fragments.push(
                <code key={`${keyBase}-code-${match.index}`} className='docsInlineCode'>
                    {match[4]}
                </code>
            )
        } else if (match[5] !== undefined) {
            fragments.push(
                <strong key={`${keyBase}-strong-${match.index}`}>
                    {match[5]}
                </strong>
            )
        }

        lastIndex = pattern.lastIndex
    }

    if (lastIndex < source.length) {
        fragments.push(source.slice(lastIndex))
    }

    return fragments.length ? fragments : source
}

function renderSectionContent(text) {
    const lines = String(text || '').split('\n')
    const blocks = []
    let paragraph = []
    let listItems = []
    let inCode = false
    let codeLines = []

    function flushParagraph(keyBase) {
        if (!paragraph.length) {
            return
        }
        blocks.push(
            <p key={`${keyBase}-paragraph`} className='docsParagraph'>
                {renderInlineMarkdown(paragraph.join(' '), `${keyBase}-paragraph`)}
            </p>
        )
        paragraph = []
    }

    function flushList(keyBase) {
        if (!listItems.length) {
            return
        }
        blocks.push(
            <ul key={`${keyBase}-list`} className='docsList'>
                {listItems.map((item, index) => (
                    <li key={`${keyBase}-item-${index}`}>
                        {renderInlineMarkdown(item, `${keyBase}-item-${index}`)}
                    </li>
                ))}
            </ul>
        )
        listItems = []
    }

    function flushCode(keyBase) {
        if (!codeLines.length) {
            return
        }
        blocks.push(
            <pre key={`${keyBase}-code`} className='docsCodeBlock'>
                <code>{codeLines.join('\n')}</code>
            </pre>
        )
        codeLines = []
    }

    lines.forEach((line, index) => {
        const keyBase = `block-${index}`
        if (line.trim().startsWith('```')) {
            flushParagraph(`${keyBase}-before-code`)
            flushList(`${keyBase}-before-code`)
            if (inCode) {
                flushCode(keyBase)
                inCode = false
            } else {
                inCode = true
            }
            return
        }
        if (inCode) {
            codeLines.push(line)
            return
        }
        if (!line.trim()) {
            flushParagraph(keyBase)
            flushList(keyBase)
            return
        }
        const listMatch = line.match(/^\s*[-*]\s+(.*)$/)
        if (listMatch) {
            flushParagraph(keyBase)
            listItems.push(listMatch[1].trim())
            return
        }
        flushList(keyBase)
        paragraph.push(line.trim())
    })

    flushParagraph('final')
    flushList('final')
    flushCode('final')

    if (!blocks.length) {
        return <div className='docsEmpty'>No content in this section.</div>
    }

    return blocks
}

function buildDraftFromPaper(paper = null) {
    return {
        project_key: String(paper?.project_key || '').trim(),
        title: String(paper?.title || '').trim(),
        status: String(paper?.status || 'draft').trim() || 'draft',
        discipline: String(paper?.discipline || '').trim(),
        symbol: String(paper?.symbol || '').trim(),
        timeframe: String(paper?.timeframe || '').trim(),
        summary: String(paper?.summary || '').trim(),
        article: normalizeScientificArticle(paper?.article || {}),
    }
}

function buildVerdictTone(verdict = '') {
    const safeVerdict = String(verdict || '').trim().toLowerCase()
    if (safeVerdict === 'met') {
        return 'success'
    }
    if (safeVerdict === 'not_met') {
        return 'danger'
    }
    if (safeVerdict === 'partially_met') {
        return 'warning'
    }
    return 'neutral'
}

function buildArticleSections(article) {
    const safeArticle = normalizeScientificArticle(article || {})
    const sections = []

    if (safeArticle.abstract) {
        sections.push({
            id: 'abstract',
            title: 'Abstract',
            kind: 'markdown',
            content: safeArticle.abstract,
            level: 1,
        })
    }

    sections.push({
        id: 'mandate',
        title: 'Research mandate',
        kind: 'mandate',
        mandate: safeArticle.mandate || {},
        level: 1,
    })

    if (Array.isArray(safeArticle.feature_analysis) && safeArticle.feature_analysis.length) {
        sections.push({
            id: 'feature-analysis',
            title: 'Feature analysis',
            kind: 'features',
            items: safeArticle.feature_analysis,
            level: 1,
        })
    }

    if (Array.isArray(safeArticle.experimental_log) && safeArticle.experimental_log.length) {
        sections.push({
            id: 'experimental-chronology',
            title: 'Experimental chronology',
            kind: 'experimental-log',
            items: safeArticle.experimental_log,
            level: 1,
        })
    }

    ;(safeArticle.sections || []).forEach((section) => {
        sections.push({
            id: `section-${section.id}`,
            title: section.title,
            kind: 'markdown',
            content: section.content,
            level: 1,
        })
    })

    return sections
}

function mergeScientificSeedArticle(existingArticle, seedArticle) {
    const current = normalizeScientificArticle(existingArticle || {})
    const seed = normalizeScientificArticle(seedArticle || {})

    return normalizeScientificArticle({
        abstract: current.abstract || seed.abstract,
        keywords: current.keywords.length ? current.keywords : seed.keywords,
        mandate: {
            objective: current.mandate?.objective || seed.mandate?.objective || '',
            strategy_specification: current.mandate?.strategy_specification || seed.mandate?.strategy_specification || '',
            target_parameters: current.mandate?.target_parameters || seed.mandate?.target_parameters || '',
            acceptance_criteria: current.mandate?.acceptance_criteria || seed.mandate?.acceptance_criteria || '',
        },
        feature_analysis: current.feature_analysis.length ? current.feature_analysis : seed.feature_analysis,
        experimental_log: current.experimental_log.length ? current.experimental_log : seed.experimental_log,
        sections: current.sections.length ? current.sections : seed.sections,
    })
}

function articleNeedsSeedHydration(article) {
    const normalized = normalizeScientificArticle(article || {})
    return (
        !String(normalized.mandate?.objective || '').trim()
        || !Array.isArray(normalized.experimental_log)
        || normalized.experimental_log.length === 0
    )
}

function renderMandateSection(mandate = {}) {
    const entries = [
        ['Objective', mandate.objective],
        ['Strategy specification', mandate.strategy_specification],
        ['Target parameters', mandate.target_parameters],
        ['Acceptance criteria', mandate.acceptance_criteria],
    ].filter(([, value]) => String(value || '').trim())

    if (!entries.length) {
        return <div className='docsEmpty'>No scientific mandate recorded yet.</div>
    }

    return (
        <div className='scientificRecordMandateGrid'>
            {entries.map(([label, value]) => (
                <div key={label} className='scientificRecordMandateCard'>
                    <div className='scientificRecordMandateLabel'>{label}</div>
                    <div className='scientificRecordMandateValue'>
                        {renderSectionContent(value)}
                    </div>
                </div>
            ))}
        </div>
    )
}

function renderFeatureAnalysis(items = []) {
    if (!items.length) {
        return <div className='docsEmpty'>No feature analysis recorded yet.</div>
    }

    return (
        <div className='scientificRecordFeatureGrid'>
            {items.map((entry) => (
                <article key={entry.id} className='scientificRecordFeatureCard'>
                    <div className='scientificRecordFeatureHeader'>
                        <h4>{entry.name}</h4>
                        <ScientificBadge label={entry.verdict || 'inconclusive'} tone={buildVerdictTone(entry.verdict)} />
                    </div>
                    <div className='scientificRecordFeatureBlock'>
                        <strong>Why this feature was used</strong>
                        {renderSectionContent(entry.rationale)}
                    </div>
                    <div className='scientificRecordFeatureBlock'>
                        <strong>Expected contribution</strong>
                        {renderSectionContent(entry.expectation)}
                    </div>
                    <div className='scientificRecordFeatureBlock'>
                        <strong>Observed result</strong>
                        {renderSectionContent(entry.observed)}
                    </div>
                </article>
            ))}
        </div>
    )
}

function renderExperimentalLog(items = []) {
    if (!items.length) {
        return <div className='docsEmpty'>No experimental chronology recorded yet.</div>
    }

    return (
        <div className='scientificRecordTimeline'>
            {items.map((entry, index) => (
                <article key={entry.id} className='scientificRecordTimelineCard'>
                    <div className='scientificRecordTimelineStep'>Step {index + 1}</div>
                    <h4>{entry.title}</h4>
                    <div className='scientificRecordFeatureBlock'>
                        <strong>What was done</strong>
                        {renderSectionContent(entry.performed)}
                    </div>
                    <div className='scientificRecordFeatureBlock'>
                        <strong>Why it was done</strong>
                        {renderSectionContent(entry.why)}
                    </div>
                    <div className='scientificRecordFeatureBlock'>
                        <strong>Results and findings</strong>
                        {renderSectionContent(entry.results)}
                    </div>
                    <div className='scientificRecordFeatureBlock'>
                        <strong>Resulting provisions</strong>
                        {renderSectionContent(entry.provisions)}
                    </div>
                </article>
            ))}
        </div>
    )
}

export function ResearchScientificRecord({
    authToken = '',
    isActive = false,
    isGuest = false,
    onLogEvent,
}) {
    const [papers, setPapers] = useState([])
    const [loading, setLoading] = useState(false)
    const [initialLoading, setInitialLoading] = useState(false)
    const [hasCompletedInitialFetch, setHasCompletedInitialFetch] = useState(false)
    const [initialFetchWasEmpty, setInitialFetchWasEmpty] = useState(false)
    const [error, setError] = useState('')
    const [selectedPaperId, setSelectedPaperId] = useState('')
    const [searchQuery, setSearchQuery] = useState('')
    const [editMode, setEditMode] = useState(false)
    const [draft, setDraft] = useState(() => buildBlankScientificPaperDraft())
    const [editingPaperId, setEditingPaperId] = useState('')
    const [saving, setSaving] = useState(false)
    const seedAttemptedRef = useRef(false)
    const seedHydrationAttemptedRef = useRef(false)
    const sectionRefs = useRef({})
    const logEventRef = useRef(onLogEvent)
    const papersRef = useRef([])

    useEffect(() => {
        logEventRef.current = onLogEvent
    }, [onLogEvent])

    useEffect(() => {
        papersRef.current = papers
    }, [papers])

    useEffect(() => {
        if (authToken) {
            return
        }
        setHasCompletedInitialFetch(false)
        setInitialFetchWasEmpty(false)
    }, [authToken])

    const fetchPapers = useCallback(async (preferredPaperId = '') => {
        if (!authToken) {
            papersRef.current = []
            setPapers([])
            setSelectedPaperId('')
            setLoading(false)
            setInitialLoading(false)
            return []
        }

        const shouldShowInitialLoading = papersRef.current.length === 0
        setLoading(true)
        if (shouldShowInitialLoading) {
            setInitialLoading(true)
        }
        setError('')
        try {
            const response = await fetch(buildApiUrl('/workspace/research-papers?workspace_id=default&limit=200'), {
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to load scientific research articles.'))
            }
            const nextPapers = Array.isArray(payload?.papers) ? payload.papers : []
            if (shouldShowInitialLoading) {
                setInitialFetchWasEmpty(nextPapers.length === 0)
            }
            papersRef.current = nextPapers
            setPapers(nextPapers)
            setSelectedPaperId((current) => {
                const desired = String(preferredPaperId || current || nextPapers[0]?.id || '')
                const exists = nextPapers.some((entry) => String(entry?.id || '') === desired)
                return exists ? desired : String(nextPapers[0]?.id || '')
            })
            return nextPapers
        } catch (nextError) {
            const message = nextError?.message || 'Failed to load scientific research articles.'
            if (shouldShowInitialLoading) {
                setInitialFetchWasEmpty(false)
            }
            setError(message)
            logEventRef.current?.(`Research · Could not load scientific record: ${message}`)
            return []
        } finally {
            setLoading(false)
            if (shouldShowInitialLoading) {
                setInitialLoading(false)
            }
            setHasCompletedInitialFetch(true)
        }
    }, [authToken])

    useEffect(() => {
        if (!isActive || !authToken) {
            return
        }
        void fetchPapers()
    }, [authToken, fetchPapers, isActive])

    useEffect(() => {
        if (!isGuest) {
            return
        }
        setEditMode(false)
        setEditingPaperId('')
    }, [isGuest])

    useEffect(() => {
        if (isGuest || !isActive || !authToken || !hasCompletedInitialFetch || !initialFetchWasEmpty || loading || error || papers.length > 0 || seedAttemptedRef.current) {
            return
        }
        seedAttemptedRef.current = true

        async function seedDefaultPapers() {
            const seeds = buildDefaultScientificPaperSeeds()
            for (const seed of seeds) {
                const response = await fetch(buildApiUrl('/workspace/research-papers'), {
                    method: 'POST',
                    headers: {
                        Authorization: `Bearer ${authToken}`,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        workspace_id: 'default',
                        reuse_existing_project_key: true,
                        ...seed,
                    }),
                })
                const payload = await readJsonResponse(response)
                if (!response.ok || payload?.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(payload, `Failed to seed article ${seed.title}.`))
                }
            }
            logEventRef.current?.('Research · Seeded the scientific record with the current research articles.')
            await fetchPapers()
        }

        void seedDefaultPapers().catch((seedError) => {
            const message = seedError?.message || 'Failed to seed scientific record.'
            setError(message)
            logEventRef.current?.(`Research · Could not seed scientific record: ${message}`)
        })
    }, [authToken, error, fetchPapers, hasCompletedInitialFetch, initialFetchWasEmpty, isActive, isGuest, loading, papers.length])

    useEffect(() => {
        if (isGuest || !isActive || !authToken || !papers.length || seedHydrationAttemptedRef.current) {
            return
        }
        seedHydrationAttemptedRef.current = true

        async function hydrateSeedArticles() {
            const seedMap = new Map(
                buildDefaultScientificPaperSeeds().map((seed) => [String(seed.project_key || ''), seed])
            )
            const hydrationTargets = papers.filter((paper) => {
                const seed = seedMap.get(String(paper?.project_key || ''))
                if (!seed) {
                    return false
                }
                return articleNeedsSeedHydration(paper.article || {})
            })
            if (!hydrationTargets.length) {
                return
            }

            for (const paper of hydrationTargets) {
                const seed = seedMap.get(String(paper?.project_key || ''))
                if (!seed) {
                    continue
                }
                const response = await fetch(buildApiUrl(`/workspace/research-papers/${paper.id}`), {
                    method: 'PATCH',
                    headers: {
                        Authorization: `Bearer ${authToken}`,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        workspace_id: 'default',
                        title: paper.title || seed.title,
                        status: paper.status || seed.status,
                        discipline: paper.discipline || seed.discipline,
                        symbol: paper.symbol || seed.symbol,
                        timeframe: paper.timeframe || seed.timeframe,
                        summary: paper.summary || seed.summary,
                        article: mergeScientificSeedArticle(paper.article || {}, seed.article || {}),
                    }),
                })
                const payload = await readJsonResponse(response)
                if (!response.ok || payload?.status !== 'ok') {
                    throw new Error(extractApiErrorMessage(payload, `Failed to hydrate scientific article ${paper.title}.`))
                }
            }

            logEventRef.current?.('Research · Hydrated the scientific record seeds with mandate and chronology fields.')
            await fetchPapers(selectedPaperId)
        }

        void hydrateSeedArticles().catch((hydrationError) => {
            const message = hydrationError?.message || 'Failed to hydrate scientific record seeds.'
            setError(message)
            logEventRef.current?.(`Research · Could not hydrate scientific record: ${message}`)
        })
    }, [authToken, fetchPapers, isActive, isGuest, papers, selectedPaperId])

    const filteredPapers = useMemo(() => {
        const query = searchQuery.trim().toLowerCase()
        if (!query) {
            return papers
        }
        return papers.filter((paper) => (
            String(paper?.title || '').toLowerCase().includes(query)
            || String(paper?.project_key || '').toLowerCase().includes(query)
            || String(paper?.summary || '').toLowerCase().includes(query)
        ))
    }, [papers, searchQuery])

    const activePaper = useMemo(
        () => papers.find((paper) => String(paper?.id || '') === String(selectedPaperId || '')) || null,
        [papers, selectedPaperId],
    )

    useEffect(() => {
        if (!editMode && activePaper) {
            setDraft(buildDraftFromPaper(activePaper))
        }
    }, [activePaper, editMode])

    const visibleArticle = useMemo(
        () => normalizeScientificArticle(editMode ? draft.article : activePaper?.article || {}),
        [activePaper, draft.article, editMode],
    )

    const articleSections = useMemo(
        () => buildArticleSections(visibleArticle),
        [visibleArticle],
    )

    function scrollToSection(sectionId) {
        const element = sectionRefs.current[sectionId]
        if (element) {
            element.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
    }

    function handleDraftChange(field, value) {
        setDraft((current) => ({
            ...current,
            [field]: value,
        }))
    }

    function handleArticleChange(field, value) {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                [field]: value,
            }),
        }))
    }

    function handleMandateChange(field, value) {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                mandate: {
                    ...(current.article?.mandate || {}),
                    [field]: value,
                },
            }),
        }))
    }

    function handleArticleSectionChange(sectionId, field, value) {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                sections: (current.article?.sections || []).map((section) => (
                    section.id === sectionId
                        ? { ...section, [field]: value }
                        : section
                )),
            }),
        }))
    }

    function handleFeatureAnalysisChange(featureId, field, value) {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                feature_analysis: (current.article?.feature_analysis || []).map((entry) => (
                    entry.id === featureId
                        ? { ...entry, [field]: value }
                        : entry
                )),
            }),
        }))
    }

    function handleExperimentalLogChange(entryId, field, value) {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                experimental_log: (current.article?.experimental_log || []).map((entry) => (
                    entry.id === entryId
                        ? { ...entry, [field]: value }
                        : entry
                )),
            }),
        }))
    }

    function handleAddSection() {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                sections: [
                    ...(current.article?.sections || []),
                    {
                        id: `section-${Date.now()}`,
                        title: 'New section',
                        content: '',
                    },
                ],
            }),
        }))
    }

    function handleRemoveSection(sectionId) {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                sections: (current.article?.sections || []).filter((section) => section.id !== sectionId),
            }),
        }))
    }

    function handleAddFeatureAnalysis() {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                feature_analysis: [
                    ...(current.article?.feature_analysis || []),
                    {
                        id: `feature-${Date.now()}`,
                        name: 'New feature family',
                        rationale: '',
                        expectation: '',
                        observed: '',
                        verdict: 'inconclusive',
                    },
                ],
            }),
        }))
    }

    function handleRemoveFeatureAnalysis(featureId) {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                feature_analysis: (current.article?.feature_analysis || []).filter((entry) => entry.id !== featureId),
            }),
        }))
    }

    function handleAddExperimentalLogEntry() {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                experimental_log: [
                    ...(current.article?.experimental_log || []),
                    {
                        id: `experiment-${Date.now()}`,
                        title: 'New experimental step',
                        performed: '',
                        why: '',
                        results: '',
                        provisions: '',
                    },
                ],
            }),
        }))
    }

    function handleRemoveExperimentalLogEntry(entryId) {
        setDraft((current) => ({
            ...current,
            article: normalizeScientificArticle({
                ...current.article,
                experimental_log: (current.article?.experimental_log || []).filter((entry) => entry.id !== entryId),
            }),
        }))
    }

    function startNewPaper() {
        if (isGuest) {
            logEventRef.current?.('Research · Guest demo can inspect the curated article, but cannot create articles.')
            return
        }
        setEditingPaperId('')
        setDraft(buildBlankScientificPaperDraft())
        setEditMode(true)
    }

    function startEditCurrentPaper() {
        if (!activePaper || isGuest) {
            if (isGuest) {
                logEventRef.current?.('Research · Guest demo can inspect the curated article, but cannot edit articles.')
            }
            return
        }
        setEditingPaperId(String(activePaper.id))
        setDraft(buildDraftFromPaper(activePaper))
        setEditMode(true)
    }

    function cancelEditMode() {
        setEditMode(false)
        setEditingPaperId('')
        if (activePaper) {
            setDraft(buildDraftFromPaper(activePaper))
        } else {
            setDraft(buildBlankScientificPaperDraft())
        }
    }

    async function handleSaveDraft() {
        if (!authToken || isGuest) {
            return
        }
        setSaving(true)

        try {
            const payload = {
                workspace_id: 'default',
                project_key: draft.project_key,
                title: draft.title,
                status: draft.status,
                discipline: draft.discipline,
                symbol: draft.symbol,
                timeframe: draft.timeframe,
                summary: draft.summary,
                article: normalizeScientificArticle(draft.article),
            }
            const response = await fetch(buildApiUrl(
                editingPaperId
                    ? `/workspace/research-papers/${editingPaperId}`
                    : '/workspace/research-papers'
            ), {
                method: editingPaperId ? 'PATCH' : 'POST',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            })
            const result = await readJsonResponse(response)
            if (!response.ok || result?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(result, 'Failed to save scientific article.'))
            }
            const savedPaper = result?.paper || null
            if (!savedPaper) {
                throw new Error('Scientific article saved without a paper payload.')
            }
            await fetchPapers(String(savedPaper.id))
            setSelectedPaperId(String(savedPaper.id))
            setEditingPaperId(String(savedPaper.id))
            setDraft(buildDraftFromPaper(savedPaper))
            setEditMode(false)
            logEventRef.current?.(`Research · Saved scientific article: ${savedPaper.title}`)
        } catch (nextError) {
            const message = nextError?.message || 'Failed to save scientific article.'
            setError(message)
            logEventRef.current?.(`Research · Could not save scientific article: ${message}`)
        } finally {
            setSaving(false)
        }
    }

    async function handleDeleteCurrentPaper() {
        if (!authToken || !activePaper || isGuest) {
            return
        }
        const shouldDelete = window.confirm(`Delete the scientific article "${activePaper.title}"?`)
        if (!shouldDelete) {
            return
        }

        setSaving(true)
        try {
            const response = await fetch(buildApiUrl(`/workspace/research-papers/${activePaper.id}?workspace_id=default`), {
                method: 'DELETE',
                headers: {
                    Authorization: `Bearer ${authToken}`,
                },
            })
            const payload = await readJsonResponse(response)
            if (!response.ok || payload?.status !== 'ok') {
                throw new Error(extractApiErrorMessage(payload, 'Failed to delete scientific article.'))
            }
            const nextPapers = await fetchPapers()
            if (!nextPapers.length) {
                setEditMode(false)
                setEditingPaperId('')
                setDraft(buildBlankScientificPaperDraft())
            }
            logEventRef.current?.(`Research · Deleted scientific article: ${activePaper.title}`)
        } catch (nextError) {
            const message = nextError?.message || 'Failed to delete scientific article.'
            setError(message)
            logEventRef.current?.(`Research · Could not delete scientific article: ${message}`)
        } finally {
            setSaving(false)
        }
    }

    return (
        <div className='ScientificRecordConsole'>
            <div className='docsLayout scientificRecordLayout'>
                <aside className='docsSidebar scientificRecordSidebar'>
                    <div className='docsSidebarTitle'>Scientific record</div>
                    {initialLoading ? <div className='docsSidebarStatus'>Loading articles...</div> : null}
                    {error ? <div className='docsSidebarError'>{error}</div> : null}

                    <div className='docsSidebarControls'>
                        <input
                            type='text'
                            className='docsSearchInput'
                            placeholder='Search research articles'
                            value={searchQuery}
                            onChange={(event) => setSearchQuery(event.target.value)}
                        />
                        {!isGuest ? (
                            <button type='button' className='scientificRecordActionButton' onClick={startNewPaper}>
                                New article
                            </button>
                        ) : null}
                        <button type='button' className='scientificRecordActionButton' onClick={() => void fetchPapers(selectedPaperId)}>
                            Refresh
                        </button>
                    </div>

                    <div className='scientificRecordSidebarKey'>
                        {isGuest
                            ? 'Guest demo shows the current reference article as a read-only display.'
                            : 'This surface is the continuous scientific log of the strategy studies. Each article should grow incrementally as the study evolves.'}
                    </div>

                    <div className='docsSidebarGroups'>
                        {!filteredPapers.length ? (
                            <div className='docsSidebarStatus'>No scientific articles match the current search.</div>
                        ) : null}
                        <div className='docsSidebarGroup'>
                            <div className='docsSidebarGroupTitle'>Saved studies</div>
                            <div className='docsSidebarList'>
                                {filteredPapers.map((paper) => {
                                    const isSelected = String(selectedPaperId) === String(paper.id)
                                    const paperSections = isSelected && !editMode ? buildArticleSections(paper.article || {}) : []
                                    return (
                                        <div key={paper.id} className='docsSidebarDocEntry'>
                                            <button
                                                type='button'
                                                className={`docsSidebarItem ${isSelected ? 'active' : ''}`}
                                                onClick={() => {
                                                    setSelectedPaperId(String(paper.id))
                                                    setEditMode(false)
                                                }}
                                            >
                                                <div className='docsSidebarItemTitle'>{paper.title}</div>
                                                <div className='docsSidebarItemMeta'>
                                                    {paper.status ? <ScientificBadge label={paper.status} tone={paper.status === 'active' ? 'success' : 'neutral'} /> : null}
                                                    {paper.symbol ? <ScientificBadge label={paper.symbol} tone='neutral' /> : null}
                                                    {paper.timeframe ? <ScientificBadge label={paper.timeframe} tone='neutral' /> : null}
                                                </div>
                                            </button>
                                            <div className='scientificRecordSidebarKey'>{paper.project_key}</div>
                                            {isSelected && paper.summary ? (
                                                <div className='scientificRecordSidebarSummary'>
                                                    {paper.summary}
                                                </div>
                                            ) : null}
                                            {isSelected && paperSections.length ? (
                                                <div className='docsInlineToc'>
                                                    {paperSections.map((section) => (
                                                        <button
                                                            key={`${paper.id}-${section.id}`}
                                                            type='button'
                                                            className='docsSidebarItem docsInlineTocItem level-2'
                                                            onClick={() => scrollToSection(section.id)}
                                                        >
                                                            {section.title}
                                                        </button>
                                                    ))}
                                                </div>
                                            ) : null}
                                        </div>
                                    )
                                })}
                            </div>
                        </div>
                    </div>
                </aside>

                <section className='docsContent scientificRecordContent'>
                    {activePaper || editMode ? (
                        <>
                            <header className='docsHeader scientificRecordHeader'>
                                <div>
                                    <div className='docsEyebrow'>Research article</div>
                                    <h2 className='docsTitle'>{editMode ? draft.title : activePaper?.title}</h2>
                                    <div className='docsHeaderMeta'>
                                        {(editMode ? draft.status : activePaper?.status) ? (
                                            <ScientificBadge
                                                label={editMode ? draft.status : activePaper?.status}
                                                tone={(editMode ? draft.status : activePaper?.status) === 'active' ? 'success' : 'neutral'}
                                            />
                                        ) : null}
                                        {(editMode ? draft.discipline : activePaper?.discipline) ? (
                                            <ScientificBadge label={editMode ? draft.discipline : activePaper?.discipline} tone='neutral' />
                                        ) : null}
                                        {(editMode ? draft.symbol : activePaper?.symbol) ? (
                                            <ScientificBadge label={editMode ? draft.symbol : activePaper?.symbol} tone='neutral' />
                                        ) : null}
                                        {(editMode ? draft.timeframe : activePaper?.timeframe) ? (
                                            <ScientificBadge label={editMode ? draft.timeframe : activePaper?.timeframe} tone='neutral' />
                                        ) : null}
                                    </div>
                                    <div className='docsSummary'>
                                        {editMode ? draft.summary || 'No summary recorded yet.' : activePaper?.summary || 'No summary recorded yet.'}
                                    </div>
                                </div>
                                {!isGuest ? (
                                <div className='scientificRecordHeaderActions'>
                                    {!editMode ? (
                                        <>
                                            <button type='button' className='scientificRecordActionButton primary' onClick={startEditCurrentPaper}>
                                                Edit article
                                            </button>
                                            <button type='button' className='scientificRecordActionButton danger' disabled={saving} onClick={() => void handleDeleteCurrentPaper()}>
                                                Delete
                                            </button>
                                        </>
                                    ) : (
                                        <>
                                            <button type='button' className='scientificRecordActionButton primary' disabled={saving} onClick={() => void handleSaveDraft()}>
                                                {saving ? 'Saving article...' : 'Save article'}
                                            </button>
                                            <button type='button' className='scientificRecordActionButton' disabled={saving} onClick={cancelEditMode}>
                                                Cancel
                                            </button>
                                        </>
                                    )}
                                </div>
                                ) : (
                                    <div className='scientificRecordHeaderActions'>
                                        <span className='scientificRecordReadOnlyBadge'>Guest display only</span>
                                    </div>
                                )}
                            </header>

                            <div className='scientificRecordBody'>
                                {!editMode ? (
                                    <>
                                        <aside className='scientificRecordNavigator'>
                                            <div className='scientificRecordRailTitle'>Article map</div>
                                            <div className='scientificRecordNavigatorList'>
                                                {articleSections.map((section) => (
                                                    <button
                                                        key={section.id}
                                                        type='button'
                                                        className='scientificRecordNavigatorButton'
                                                        onClick={() => scrollToSection(section.id)}
                                                    >
                                                        {section.title}
                                                    </button>
                                                ))}
                                            </div>
                                        </aside>

                                        <div className='scientificRecordArticlePane'>
                                            <div className='docsSections scientificRecordSections'>
                                                {articleSections.map((section) => (
                                                    <article
                                                        key={section.id}
                                                        className='docsSectionCard'
                                                        ref={(element) => {
                                                            sectionRefs.current[section.id] = element
                                                        }}
                                                    >
                                                        <h3 className={`docsSectionTitle level-${section.level}`}>{section.title}</h3>
                                                        <div className='docsSectionBody'>
                                                            {section.kind === 'mandate' ? renderMandateSection(section.mandate) : null}
                                                            {section.kind === 'features' ? renderFeatureAnalysis(section.items || []) : null}
                                                            {section.kind === 'experimental-log' ? renderExperimentalLog(section.items || []) : null}
                                                            {section.kind === 'markdown' ? renderSectionContent(section.content) : null}
                                                        </div>
                                                    </article>
                                                ))}
                                            </div>
                                        </div>
                                    </>
                                ) : (
                                    <div className='scientificRecordEditor'>
                                        <div className='scientificRecordEditorGrid'>
                                            <label className='scientificRecordField'>
                                                <span>Project key</span>
                                                <input value={draft.project_key} onChange={(event) => handleDraftChange('project_key', event.target.value)} />
                                            </label>
                                            <label className='scientificRecordField wide'>
                                                <span>Title</span>
                                                <input value={draft.title} onChange={(event) => handleDraftChange('title', event.target.value)} />
                                            </label>
                                            <label className='scientificRecordField'>
                                                <span>Status</span>
                                                <select value={draft.status} onChange={(event) => handleDraftChange('status', event.target.value)}>
                                                    <option value='draft'>draft</option>
                                                    <option value='reference'>reference</option>
                                                    <option value='active'>active</option>
                                                    <option value='archived'>archived</option>
                                                </select>
                                            </label>
                                            <label className='scientificRecordField'>
                                                <span>Symbol</span>
                                                <input value={draft.symbol} onChange={(event) => handleDraftChange('symbol', event.target.value.toUpperCase())} />
                                            </label>
                                            <label className='scientificRecordField'>
                                                <span>Timeframe</span>
                                                <input value={draft.timeframe} onChange={(event) => handleDraftChange('timeframe', event.target.value.toUpperCase())} />
                                            </label>
                                            <label className='scientificRecordField wide'>
                                                <span>Discipline</span>
                                                <input value={draft.discipline} onChange={(event) => handleDraftChange('discipline', event.target.value)} />
                                            </label>
                                            <label className='scientificRecordField wide'>
                                                <span>Summary</span>
                                                <textarea rows={3} value={draft.summary} onChange={(event) => handleDraftChange('summary', event.target.value)} />
                                            </label>
                                            <label className='scientificRecordField wide'>
                                                <span>Abstract</span>
                                                <textarea rows={8} value={draft.article?.abstract || ''} onChange={(event) => handleArticleChange('abstract', event.target.value)} />
                                            </label>
                                            <label className='scientificRecordField wide'>
                                                <span>Keywords (comma separated)</span>
                                                <input
                                                    value={Array.isArray(draft.article?.keywords) ? draft.article.keywords.join(', ') : ''}
                                                    onChange={(event) => handleArticleChange(
                                                        'keywords',
                                                        event.target.value
                                                            .split(',')
                                                            .map((entry) => String(entry || '').trim())
                                                            .filter(Boolean),
                                                    )}
                                                />
                                            </label>
                                        </div>

                                        <div className='scientificRecordEditCard'>
                                            <div className='scientificRecordEditorSectionHeader'>
                                                <h3>Mandate and study specification</h3>
                                            </div>
                                            <label className='scientificRecordField'>
                                                <span>Objective</span>
                                                <textarea rows={4} value={draft.article?.mandate?.objective || ''} onChange={(event) => handleMandateChange('objective', event.target.value)} />
                                            </label>
                                            <label className='scientificRecordField'>
                                                <span>Strategy specification</span>
                                                <textarea rows={4} value={draft.article?.mandate?.strategy_specification || ''} onChange={(event) => handleMandateChange('strategy_specification', event.target.value)} />
                                            </label>
                                            <label className='scientificRecordField'>
                                                <span>Target parameters</span>
                                                <textarea rows={4} value={draft.article?.mandate?.target_parameters || ''} onChange={(event) => handleMandateChange('target_parameters', event.target.value)} />
                                            </label>
                                            <label className='scientificRecordField'>
                                                <span>Acceptance criteria</span>
                                                <textarea rows={4} value={draft.article?.mandate?.acceptance_criteria || ''} onChange={(event) => handleMandateChange('acceptance_criteria', event.target.value)} />
                                            </label>
                                        </div>

                                        <div className='scientificRecordEditCard'>
                                            <div className='scientificRecordEditorSectionHeader'>
                                                <h3>Feature analysis</h3>
                                                <button type='button' className='scientificRecordActionButton' onClick={handleAddFeatureAnalysis}>
                                                    Add feature family
                                                </button>
                                            </div>
                                            {(draft.article?.feature_analysis || []).map((entry) => (
                                                <div key={entry.id} className='scientificRecordEditCard'>
                                                    <label className='scientificRecordField'>
                                                        <span>Feature or parameter family</span>
                                                        <input value={entry.name} onChange={(event) => handleFeatureAnalysisChange(entry.id, 'name', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>Why it was used</span>
                                                        <textarea rows={4} value={entry.rationale} onChange={(event) => handleFeatureAnalysisChange(entry.id, 'rationale', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>What was expected</span>
                                                        <textarea rows={4} value={entry.expectation} onChange={(event) => handleFeatureAnalysisChange(entry.id, 'expectation', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>What happened in practice</span>
                                                        <textarea rows={4} value={entry.observed} onChange={(event) => handleFeatureAnalysisChange(entry.id, 'observed', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>Verdict</span>
                                                        <select value={entry.verdict || 'inconclusive'} onChange={(event) => handleFeatureAnalysisChange(entry.id, 'verdict', event.target.value)}>
                                                            <option value='met'>met</option>
                                                            <option value='partially_met'>partially_met</option>
                                                            <option value='not_met'>not_met</option>
                                                            <option value='inconclusive'>inconclusive</option>
                                                        </select>
                                                    </label>
                                                    <div className='scientificRecordEditCardActions'>
                                                        <button type='button' className='scientificRecordActionButton danger' onClick={() => handleRemoveFeatureAnalysis(entry.id)}>
                                                            Remove feature family
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>

                                        <div className='scientificRecordEditCard'>
                                            <div className='scientificRecordEditorSectionHeader'>
                                                <h3>Experimental chronology</h3>
                                                <button type='button' className='scientificRecordActionButton' onClick={handleAddExperimentalLogEntry}>
                                                    Add study step
                                                </button>
                                            </div>
                                            {(draft.article?.experimental_log || []).map((entry) => (
                                                <div key={entry.id} className='scientificRecordEditCard'>
                                                    <label className='scientificRecordField'>
                                                        <span>Step title</span>
                                                        <input value={entry.title} onChange={(event) => handleExperimentalLogChange(entry.id, 'title', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>What was done</span>
                                                        <textarea rows={4} value={entry.performed} onChange={(event) => handleExperimentalLogChange(entry.id, 'performed', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>Why it was done</span>
                                                        <textarea rows={4} value={entry.why} onChange={(event) => handleExperimentalLogChange(entry.id, 'why', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>Results and findings</span>
                                                        <textarea rows={4} value={entry.results} onChange={(event) => handleExperimentalLogChange(entry.id, 'results', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>Resulting provisions</span>
                                                        <textarea rows={4} value={entry.provisions} onChange={(event) => handleExperimentalLogChange(entry.id, 'provisions', event.target.value)} />
                                                    </label>
                                                    <div className='scientificRecordEditCardActions'>
                                                        <button type='button' className='scientificRecordActionButton danger' onClick={() => handleRemoveExperimentalLogEntry(entry.id)}>
                                                            Remove study step
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>

                                        <div className='scientificRecordEditCard'>
                                            <div className='scientificRecordEditorSectionHeader'>
                                                <h3>Article body</h3>
                                                <button type='button' className='scientificRecordActionButton' onClick={handleAddSection}>
                                                    Add body section
                                                </button>
                                            </div>
                                            {(draft.article?.sections || []).map((section) => (
                                                <div key={section.id} className='scientificRecordEditCard'>
                                                    <label className='scientificRecordField'>
                                                        <span>Section title</span>
                                                        <input value={section.title} onChange={(event) => handleArticleSectionChange(section.id, 'title', event.target.value)} />
                                                    </label>
                                                    <label className='scientificRecordField'>
                                                        <span>Section content</span>
                                                        <textarea rows={8} value={section.content} onChange={(event) => handleArticleSectionChange(section.id, 'content', event.target.value)} />
                                                    </label>
                                                    <div className='scientificRecordEditCardActions'>
                                                        <button type='button' className='scientificRecordActionButton danger' onClick={() => handleRemoveSection(section.id)}>
                                                            Remove body section
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </>
                    ) : (
                        <div className='docsEmpty scientificRecordEmpty'>
                            {isGuest ? 'No guest display article is available yet.' : 'Select a scientific article or create a new one.'}
                        </div>
                    )}
                </section>
            </div>
        </div>
    )
}

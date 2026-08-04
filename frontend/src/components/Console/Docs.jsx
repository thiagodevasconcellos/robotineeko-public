import { useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, extractApiErrorMessage, readJsonResponse } from '/src/api'
import './Docs.css'

function groupDocumentsByCategory(documents = []) {
    const groups = new Map()
    for (const doc of documents) {
        const category = doc?.category || 'Other'
        if (!groups.has(category)) {
            groups.set(category, [])
        }
        groups.get(category).push(doc)
    }
    return Array.from(groups.entries()).map(([category, items]) => ({ category, items }))
}

function DocumentationBadge({ label = '', tone = 'neutral' }) {
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

export function Docs({
    authToken = '',
    isActive = false,
}) {
    const [state, setState] = useState({
        loading: false,
        error: '',
        documents: [],
    })
    const [selectedDocId, setSelectedDocId] = useState('')
    const [searchQuery, setSearchQuery] = useState('')
    const [categoryFilter, setCategoryFilter] = useState('All')
    const sectionRefs = useRef({})

    useEffect(() => {
        if (!isActive || !authToken) {
            return undefined
        }

        let cancelled = false

        async function fetchDocs() {
            setState((current) => ({
                ...current,
                loading: true,
                error: '',
            }))

            try {
                const response = await fetch(buildApiUrl('/system/docs'), {
                    headers: {
                        'Authorization': `Bearer ${authToken}`,
                    },
                })
                const data = await readJsonResponse(response)
                if (!response.ok) {
                    throw new Error(extractApiErrorMessage(data, 'Failed to load project docs.'))
                }

                if (cancelled) {
                    return
                }

                const documents = Array.isArray(data?.documents) ? data.documents : []
                setState({
                    loading: false,
                    error: '',
                    documents,
                })
                setSelectedDocId((current) => current || documents[0]?.id || '')
            } catch (error) {
                if (cancelled) {
                    return
                }
                setState({
                    loading: false,
                    error: error?.message || 'Failed to load project docs.',
                    documents: [],
                })
            }
        }

        void fetchDocs()

        return () => {
            cancelled = true
        }
    }, [authToken, isActive])

    const categories = useMemo(
        () => ['All', ...Array.from(new Set(state.documents.map((doc) => doc.category || 'Other')))],
        [state.documents]
    )
    const filteredDocuments = useMemo(() => {
        const query = searchQuery.trim().toLowerCase()
        return state.documents.filter((doc) => {
            const categoryMatches = categoryFilter === 'All' || (doc.category || 'Other') === categoryFilter
            if (!categoryMatches) {
                return false
            }

            if (!query) {
                return true
            }

            const haystack = [
                doc.title,
                doc.category,
                doc.summary,
                doc.path,
                ...(doc.sections || []).map((section) => section.title),
            ].join(' ').toLowerCase()

            return haystack.includes(query)
        })
    }, [categoryFilter, searchQuery, state.documents])
    const groupedDocuments = useMemo(() => groupDocumentsByCategory(filteredDocuments), [filteredDocuments])
    const classificationLegend = useMemo(() => {
        const seen = new Map()
        for (const doc of state.documents) {
            const label = String(doc?.classification || '').trim()
            if (!label || seen.has(label)) {
                continue
            }
            seen.set(label, {
                label,
                tone: String(doc?.classification_tone || 'neutral'),
            })
        }
        return Array.from(seen.values())
    }, [state.documents])
    const selectedDocument = useMemo(
        () => filteredDocuments.find((item) => item.id === selectedDocId) || filteredDocuments[0] || null,
        [selectedDocId, filteredDocuments]
    )

    useEffect(() => {
        if (selectedDocument?.id) {
            return
        }
        setSelectedDocId(filteredDocuments[0]?.id || '')
    }, [filteredDocuments, selectedDocument?.id])

    useEffect(() => {
        sectionRefs.current = {}
    }, [selectedDocument?.id])

    function scrollToSection(sectionId) {
        const element = sectionRefs.current[sectionId]
        if (element) {
            element.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
    }

    return (
        <div className='DocsConsole'>
            <div className='docsLayout'>
                <aside className='docsSidebar'>
                    <div className='docsSidebarTitle'>Documentation</div>
                    {state.loading ? <div className='docsSidebarStatus'>Loading...</div> : null}
                    {state.error ? <div className='docsSidebarError'>{state.error}</div> : null}

                    <div className='docsSidebarControls'>
                        <input
                            type='text'
                            className='docsSearchInput'
                            placeholder='Search docs'
                            value={searchQuery}
                            onChange={(event) => setSearchQuery(event.target.value)}
                        />
                        <select
                            className='docsCategorySelect'
                            value={categoryFilter}
                            onChange={(event) => setCategoryFilter(event.target.value)}
                        >
                            {categories.map((category) => (
                                <option key={category} value={category}>{category}</option>
                            ))}
                        </select>
                    </div>

                    {classificationLegend.length ? (
                        <div className='docsLegend'>
                            {classificationLegend.map((entry) => (
                                <DocumentationBadge
                                    key={entry.label}
                                    label={entry.label}
                                    tone={entry.tone}
                                />
                            ))}
                        </div>
                    ) : null}

                    <div className='docsSidebarGroups'>
                        {!groupedDocuments.length ? (
                            <div className='docsSidebarStatus'>No documents match the current filters.</div>
                        ) : null}
                        {groupedDocuments.map((group) => (
                            <div key={group.category} className='docsSidebarGroup'>
                                <div className='docsSidebarGroupTitle'>{group.category}</div>
                                <div className='docsSidebarList'>
                                    {group.items.map((doc) => (
                                        <div key={doc.id} className='docsSidebarDocEntry'>
                                            <button
                                                type='button'
                                                className={`docsSidebarItem ${selectedDocument?.id === doc.id ? 'active' : ''}`}
                                                onClick={() => setSelectedDocId(doc.id)}
                                            >
                                                <div className='docsSidebarItemTitle'>{doc.title}</div>
                                                {doc.classification ? (
                                                    <div className='docsSidebarItemMeta'>
                                                        <DocumentationBadge
                                                            label={doc.classification}
                                                            tone={doc.classification_tone}
                                                        />
                                                    </div>
                                                ) : null}
                                            </button>
                                            {selectedDocument?.id === doc.id && Array.isArray(doc.sections) && doc.sections.length ? (
                                                <div className='docsInlineToc'>
                                                    {doc.sections.map((section) => (
                                                        <button
                                                            key={`${doc.id}-${section.id}`}
                                                            type='button'
                                                            className={`docsSidebarItem docsInlineTocItem level-${section.level}`}
                                                            onClick={() => scrollToSection(section.id)}
                                                        >
                                                            {section.title}
                                                        </button>
                                                    ))}
                                                </div>
                                            ) : null}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                </aside>

                <section className='docsContent'>
                    {selectedDocument ? (
                        <>
                            <header className='docsHeader'>
                                <div>
                                    <div className='docsEyebrow'>{selectedDocument.category}</div>
                                    <h2 className='docsTitle'>{selectedDocument.title}</h2>
                                    {selectedDocument.classification ? (
                                        <div className='docsHeaderMeta'>
                                            <DocumentationBadge
                                                label={selectedDocument.classification}
                                                tone={selectedDocument.classification_tone}
                                            />
                                            {selectedDocument.category_description ? (
                                                <span className='docsHeaderMetaText'>
                                                    {selectedDocument.category_description}
                                                </span>
                                            ) : null}
                                        </div>
                                    ) : null}
                                    {selectedDocument.summary ? (
                                        <div className='docsSummary'>
                                            {renderInlineMarkdown(selectedDocument.summary, `${selectedDocument.id}-summary`)}
                                        </div>
                                    ) : null}
                                </div>
                                <div className='docsPath'>{selectedDocument.path}</div>
                            </header>

                            <div className='docsSections'>
                                {selectedDocument.sections.map((section) => (
                                    <article
                                        key={`${selectedDocument.id}-${section.id}`}
                                        className='docsSectionCard'
                                        ref={(element) => {
                                            sectionRefs.current[section.id] = element
                                        }}
                                    >
                                        <h3 className={`docsSectionTitle level-${section.level}`}>{section.title}</h3>
                                        <div className='docsSectionBody'>
                                            {renderSectionContent(section.content)}
                                        </div>
                                    </article>
                                ))}
                            </div>
                        </>
                    ) : (
                        <div className='docsEmpty'>No documentation available.</div>
                    )}
                </section>
            </div>
        </div>
    )
}

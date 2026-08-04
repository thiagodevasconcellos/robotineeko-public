function slugifyProjectKey(value = '') {
    return String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
}

export function normalizeScientificArticle(article = {}) {
    const source = article && typeof article === 'object' ? article : {}
    const keywords = Array.isArray(source.keywords)
        ? source.keywords
            .map((entry) => String(entry || '').trim())
            .filter(Boolean)
        : []
    const sections = Array.isArray(source.sections)
        ? source.sections
            .map((entry, index) => {
                if (!entry || typeof entry !== 'object') {
                    return null
                }

                const title = String(entry.title || '').trim() || `Section ${index + 1}`
                return {
                    id: slugifyProjectKey(entry.id || title) || `section-${index + 1}`,
                    title,
                    content: String(entry.content || '').trim(),
                }
            })
            .filter(Boolean)
        : []
    const featureAnalysis = Array.isArray(source.feature_analysis)
        ? source.feature_analysis
            .map((entry, index) => {
                if (!entry || typeof entry !== 'object') {
                    return null
                }

                const name = String(entry.name || '').trim() || `Feature group ${index + 1}`
                return {
                    id: slugifyProjectKey(entry.id || name) || `feature-${index + 1}`,
                    name,
                    rationale: String(entry.rationale || '').trim(),
                    expectation: String(entry.expectation || '').trim(),
                    observed: String(entry.observed || '').trim(),
                    verdict: String(entry.verdict || '').trim().toLowerCase() || 'inconclusive',
                }
            })
            .filter(Boolean)
        : []
    const sourceMandate = source.mandate && typeof source.mandate === 'object'
        ? source.mandate
        : {}
    const experimentalLog = Array.isArray(source.experimental_log)
        ? source.experimental_log
            .map((entry, index) => {
                if (!entry || typeof entry !== 'object') {
                    return null
                }

                const title = String(entry.title || '').trim() || `Experiment step ${index + 1}`
                return {
                    id: slugifyProjectKey(entry.id || title) || `experiment-${index + 1}`,
                    title,
                    performed: String(entry.performed || '').trim(),
                    why: String(entry.why || '').trim(),
                    results: String(entry.results || '').trim(),
                    provisions: String(entry.provisions || '').trim(),
                }
            })
            .filter(Boolean)
        : []

    return {
        abstract: String(source.abstract || '').trim(),
        keywords,
        mandate: {
            objective: String(sourceMandate.objective || '').trim(),
            strategy_specification: String(sourceMandate.strategy_specification || '').trim(),
            target_parameters: String(sourceMandate.target_parameters || '').trim(),
            acceptance_criteria: String(sourceMandate.acceptance_criteria || '').trim(),
        },
        sections,
        feature_analysis: featureAnalysis,
        experimental_log: experimentalLog,
    }
}

export function buildBlankScientificPaperDraft() {
    return {
        project_key: '',
        title: 'Untitled scientific research article',
        status: 'draft',
        discipline: 'technical analysis research',
        symbol: 'EURUSD',
        timeframe: 'M5',
        summary: '',
        article: normalizeScientificArticle({
            abstract: '',
            keywords: ['systematic trading', 'research workflow', 'public snapshot'],
            mandate: {
                objective: '',
                strategy_specification: '',
                target_parameters: '',
                acceptance_criteria: '',
            },
            feature_analysis: [
                {
                    name: 'Primary feature or parameter family',
                    rationale: '',
                    expectation: '',
                    observed: '',
                    verdict: 'inconclusive',
                },
            ],
            experimental_log: [
                {
                    title: 'Initial study step',
                    performed: '',
                    why: '',
                    results: '',
                    provisions: '',
                },
            ],
            sections: [
                {
                    title: 'Introduction',
                    content: '',
                },
                {
                    title: 'Methodology',
                    content: '',
                },
                {
                    title: 'Results And Discussion',
                    content: '',
                },
                {
                    title: 'Conclusion',
                    content: '',
                },
            ],
        }),
    }
}

export function buildDefaultScientificPaperSeeds() {
    return [
        {
            project_key: 'public-snapshot-example-paper',
            title: 'Public Snapshot Example Research Note',
            status: 'reference',
            discipline: 'systematic trading research',
            symbol: 'EURUSD',
            timeframe: 'M5',
            summary: 'Minimal public-safe example that keeps the Research article workflow visible without exposing internal study records.',
            article: normalizeScientificArticle({
                abstract: 'This public repository ships a minimal reference article so the in-product documentation and scientific record UI remain demonstrable without publishing private research history.',
                keywords: [
                    'public snapshot',
                    'research workflow',
                    'documentation',
                ],
                mandate: {
                    objective: 'Demonstrate the scientific record surface with portfolio-safe content.',
                    strategy_specification: 'A representative placeholder article for public review.',
                    target_parameters: 'Readable structure, safe wording, and zero private data.',
                    acceptance_criteria: 'The UI remains functional without exposing internal study material.',
                },
                feature_analysis: [
                    {
                        name: 'Public-safe documentation',
                        rationale: 'Recruiters should be able to inspect the workflow without seeing live data or proprietary studies.',
                        expectation: 'The snapshot should keep the research surface understandable.',
                        observed: 'A reduced seed still preserves the product narrative.',
                        verdict: 'supported',
                    },
                ],
                experimental_log: [
                    {
                        title: 'Snapshot curation',
                        performed: 'Reduced internal seed content to a single public-safe reference article.',
                        why: 'The repository should showcase product structure, not operational history.',
                        results: 'The scientific record UI stays demonstrable with minimal data.',
                        provisions: 'Private research articles remain outside the public repository.',
                    },
                ],
                sections: [
                    {
                        title: 'Introduction',
                        content: 'Robotineeko includes a scientific record surface for documenting research logic, methodology, and conclusions inside the product itself.',
                    },
                    {
                        title: 'Methodology',
                        content: 'The public repository keeps only a skeletal article structure so the workflow remains understandable without exposing internal research logs.',
                    },
                    {
                        title: 'Results And Discussion',
                        content: 'The reduced dataset preserves the UI contracts and communication value of the Research surface.',
                    },
                    {
                        title: 'Conclusion',
                        content: 'The public snapshot demonstrates product design and engineering workflow while keeping operational content private.',
                    },
                ],
            }),
        },
    ]
}

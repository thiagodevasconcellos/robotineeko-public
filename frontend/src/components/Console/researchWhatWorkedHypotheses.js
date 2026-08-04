function makeHypothesis(id, title, hypothesis, whyItFits) {
    return {
        id,
        title,
        hypothesis,
        whyItFits,
    }
}

export const RESEARCH_WHAT_WORKED_HYPOTHESES_LAST_UPDATED = 'Public snapshot'

export const RESEARCH_WHAT_WORKED_HYPOTHESIS_GROUPS = [
    {
        id: 'public-snapshot',
        label: 'Public snapshot examples',
        summary: 'Minimal examples kept in the public repository so the What Worked surface remains understandable without exposing private research hypotheses.',
        items: [
            makeHypothesis(
                'hyp-public-001',
                'Guest showcase remains readable',
                'The public snapshot should preserve the structure of the research workflow even when private catalogs are removed.',
                'Recruiters need to understand the product surface without gaining access to internal studies or generated strategy history.'
            ),
            makeHypothesis(
                'hyp-public-002',
                'Documentation can replace hidden data',
                'A strong in-product explanation layer can communicate system maturity even when operational examples stay private.',
                'The public repo is a recruiter-facing source snapshot, so clarity of product and code structure matters more than raw data volume.'
            ),
        ],
    },
]

export const RESEARCH_WHAT_WORKED_HYPOTHESIS_COUNT = RESEARCH_WHAT_WORKED_HYPOTHESIS_GROUPS.reduce(
    (total, group) => total + group.items.length,
    0,
)

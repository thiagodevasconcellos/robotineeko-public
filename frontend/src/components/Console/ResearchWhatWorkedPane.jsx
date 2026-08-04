import { useEffect, useMemo, useState } from 'react'
import {
    createLocalPositiveHistoryCatalogState,
    fetchSharedPositiveHistoryCatalog,
    mergeLocalAndSharedPositiveHistoryCatalog,
} from './positiveHistoryCatalogClient.js'
import {
    RESEARCH_WHAT_WORKED_HYPOTHESIS_COUNT,
    RESEARCH_WHAT_WORKED_HYPOTHESIS_GROUPS,
    RESEARCH_WHAT_WORKED_HYPOTHESES_LAST_UPDATED,
} from './researchWhatWorkedHypotheses.js'

const PAPER_FRONTIER_IDS = [
    'paper57-pair-switch-age-balance',
    'paper77-lookback-fixed-balance',
    'paper80-carrier-mixture-age-balance',
    'paper83-session-switch-tokyo',
    'paper85-peer-lag-lag23-union',
    'paper93-snapback-p80-sign-or-z-020',
    'paper97-cooldown-lag1-z70',
    'paper106-month-end-26-31-direct-shell',
    'paper115-tp070-sl046-direct-shell',
    'paper116-same-bar-direct-shell',
    'paper128-hybrid-age55-latest-tokyo-prev1',
    'paper130-contrastive-top1-gap',
]

const STRUCTURAL_THESES = [
    {
        id: 'direct-shell-first',
        title: 'The direct shell usually wins by staying in control',
        description: 'Recent progress did not come from inventing a brand-new raw engine. It came from keeping a sparse direct residual shell alive and only removing or rerouting the clearly toxic rows around it.',
        evidenceIds: [
            'paper57-pair-switch-age-balance',
            'paper80-carrier-mixture-age-balance',
            'paper127-regime-embed-latest-prev1',
            'paper128-hybrid-age55-latest-tokyo-prev1',
        ],
    },
    {
        id: 'temporal-conditioning',
        title: 'Temporal ownership beats broad feature proliferation',
        description: 'Age, session, weekday and month-end trims repeatedly improved the shell without changing its core economics. The main edge was not “more indicators”; it was choosing when the shell was allowed to own the row.',
        evidenceIds: [
            'paper57-pair-switch-age-balance',
            'paper83-session-switch-tokyo',
            'paper99-session-mask-us-hours',
            'paper102-tue-wed-direct-shell',
            'paper106-month-end-26-31-direct-shell',
        ],
    },
    {
        id: 'peer-information',
        title: 'Peer information works mainly as veto, lag filter, or ownership switch',
        description: 'GBPUSD peer state was repeatedly useful when it acted like a delayed-context gate. It helped most when it vetoed bad rows, delayed entry after shocks, or flipped a tiny context to a different owner.',
        evidenceIds: [
            'paper77-lookback-fixed-balance',
            'paper85-peer-lag-lag23-union',
            'paper97-cooldown-lag1-z70',
            'paper130-contrastive-top1-gap',
        ],
    },
    {
        id: 'geometry-late',
        title: 'Exit and execution tuning mattered only after the shell was already clean',
        description: 'Adaptive hold, residual snapback exits, TP/SL geometry, and same-bar execution all added lift only after the context shell had already become sparse and disciplined. Applied too early, they did not create an edge by themselves.',
        evidenceIds: [
            'paper91-adaptive-hold-p85-lag23',
            'paper93-snapback-p80-sign-or-z-020',
            'paper115-tp070-sl046-direct-shell',
            'paper116-same-bar-direct-shell',
        ],
    },
    {
        id: 'inverse-fragile',
        title: 'The inverse rescue is real, but it is a single-context niche',
        description: 'The best modern lift came from handing exactly one context to the inverse shell. Every broader version tied only through flat neighbors or degraded immediately once a real second context was admitted.',
        evidenceIds: [
            'paper100-overlap-only-inverse-shell',
            'paper130-contrastive-top1-gap',
        ],
    },
]

const EXHAUSTED_CONTINUATIONS = [
    {
        id: 'elliott-symbolic',
        title: 'Elliott symbolic family',
        papers: 'papers 140-142',
        lesson: 'ElliottWaveProxyV1 explained the same rescue, but never widened the live inventory. The family only re-expressed `M5 prev_4` and died on the first honest loosening.',
    },
    {
        id: 'elliott-neural',
        title: 'Elliott neural owner-router family',
        papers: 'paper 143',
        lesson: 'The feed-forward baseline never opened, because the sufficiency audit showed only one non-flat inverse-owned frontier context. A neural router would have been learning a single niche, not a true routing surface.',
    },
]

const MOTIF_MODELS = [
    {
        id: 'temporal',
        title: 'Temporal gating',
        matcher: /(age|session|tokyo|weekday|month|us-hours|cooldown|overlap-only|tue-wed)/i,
        why: 'Most recent direct-shell improvements came from time ownership: age, session, weekday, and month-end controls repeatedly removed damage without inventing a new engine.',
    },
    {
        id: 'routing',
        title: 'Routing and ownership',
        matcher: /(carrier|switch|routing|lookback|embed|contrastive|pair-choice|hybrid|inverse rescue)/i,
        why: 'Ownership logic was stronger than brute-force signal generation. The biggest jumps came from deciding which shell should own a narrow state, not from broadening participation.',
    },
    {
        id: 'peer',
        title: 'Peer veto and lag state',
        matcher: /(peer|lag|shock|snapback|adaptive hold|cooldown)/i,
        why: 'Peer state worked when it acted like a gate or delayed-warning signal. It was useful as a filter and cooldown, not as a second broad thesis.',
    },
    {
        id: 'execution',
        title: 'Exit and execution geometry',
        matcher: /(hold|snapback|target|tp|sl|same bar|execution|geometry)/i,
        why: 'Trade management improved results only after the context shell was already disciplined. It amplified an existing edge instead of creating one from nothing.',
    },
    {
        id: 'legacy-neural',
        title: 'Legacy neural and benchmark lines',
        matcher: /(neural|cnn|benchmark|mce|ema)/i,
        why: 'These lines matter mostly as historical anchors and cautionary evidence. They proved that isolated positives can exist without becoming reusable operator-facing edges.',
    },
]

function hasFiniteNumber(value) {
    return value !== null && value !== undefined && Number.isFinite(Number(value))
}

function extractCheckpointValue(entry) {
    const text = String(entry?.positiveCheckpoint || '')
    const match = text.match(/[-+]?\d+(?:\.\d+)?/)
    if (!match) {
        return Number.NEGATIVE_INFINITY
    }
    const parsed = Number(match[0])
    return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY
}

function formatApprox(value, digits = 2, suffix = '') {
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    return `~${Number(value).toFixed(digits)}${suffix}`
}

function formatTradesPerDay(value) {
    if (!hasFiniteNumber(value)) {
        return 'n/a'
    }
    return formatApprox(value, Number(value) < 0.1 ? 3 : 2)
}

function median(values = []) {
    const numbers = values
        .filter((value) => hasFiniteNumber(value))
        .map((value) => Number(value))
        .sort((left, right) => left - right)
    if (!numbers.length) {
        return null
    }
    const middle = Math.floor(numbers.length / 2)
    if (numbers.length % 2 === 1) {
        return numbers[middle]
    }
    return (numbers[middle - 1] + numbers[middle]) / 2
}

function findMode(values = []) {
    const counts = new Map()
    for (const value of values) {
        const key = String(value || '').trim()
        if (!key) {
            continue
        }
        counts.set(key, (counts.get(key) || 0) + 1)
    }
    let bestKey = ''
    let bestCount = 0
    for (const [key, count] of counts.entries()) {
        if (count > bestCount) {
            bestKey = key
            bestCount = count
        }
    }
    return { value: bestKey, count: bestCount }
}

function buildEvidenceLookup(entries = []) {
    return new Map(entries.map((entry) => [String(entry?.id || ''), entry]))
}

function formatEdgeLabel(entry) {
    if (hasFiniteNumber(entry?.expectedMonthlyPercent)) {
        return `${formatApprox(entry.expectedMonthlyPercent, 2, '%')} expected monthly`
    }
    const checkpointValue = extractCheckpointValue(entry)
    if (Number.isFinite(checkpointValue) && checkpointValue > Number.NEGATIVE_INFINITY) {
        return `${formatApprox(checkpointValue, 2)} checkpoint proxy`
    }
    return 'n/a'
}

function buildPaperEraEntries(entries = []) {
    return entries.filter((entry) => String(entry?.id || '').startsWith('paper'))
}

function buildFrontierLadder(entriesById) {
    return PAPER_FRONTIER_IDS
        .map((id) => entriesById.get(id))
        .filter(Boolean)
}

function buildMotifRows(entries = []) {
    return MOTIF_MODELS.map((motif) => {
        const matchingEntries = entries.filter((entry) => motif.matcher.test(
            `${String(entry?.label || '')} ${String(entry?.study || '')} ${String(entry?.family || '')}`,
        ))
        const bestEntry = matchingEntries
            .slice()
            .sort((left, right) => extractCheckpointValue(right) - extractCheckpointValue(left))[0] || null
        const medianTradesPerDay = median(matchingEntries.map((entry) => entry?.tradesPerDay))
        return {
            ...motif,
            count: matchingEntries.length,
            bestEntry,
            medianTradesPerDay,
        }
    })
}

function EvidenceChips({ ids = [], entriesById }) {
    const labels = ids
        .map((id) => entriesById.get(id))
        .filter(Boolean)
        .map((entry) => entry.label)
    return (
        <div className='researchWhatWorkedEvidenceList'>
            {labels.map((label) => (
                <span key={label} className='researchWhatWorkedEvidenceChip'>{label}</span>
            ))}
        </div>
    )
}

export function ResearchWhatWorkedPane() {
    const [catalogState, setCatalogState] = useState(() => createLocalPositiveHistoryCatalogState())
    const entries = catalogState.catalog

    useEffect(() => {
        let cancelled = false

        async function hydrateFromSharedRegistry() {
            try {
                const sharedPayload = await fetchSharedPositiveHistoryCatalog()
                if (cancelled) {
                    return
                }
                setCatalogState(mergeLocalAndSharedPositiveHistoryCatalog(sharedPayload))
            } catch {
                // Keep the local catalog as the fallback when the shared registry is unavailable.
            }
        }

        void hydrateFromSharedRegistry()
        return () => {
            cancelled = true
        }
    }, [])

    const entriesById = useMemo(() => buildEvidenceLookup(entries), [entries])
    const paperEraEntries = useMemo(() => buildPaperEraEntries(entries), [entries])
    const frontierLadder = useMemo(() => buildFrontierLadder(entriesById), [entriesById])
    const motifRows = useMemo(() => buildMotifRows(entries), [entries])

    const analysis = useMemo(() => {
        const currentFrontier = entriesById.get('paper130-contrastive-top1-gap') || null
        const promotedAnchor = entries.find((entry) => entry?.classification === 'promoted') || null
        const paperTimeframeMode = findMode(paperEraEntries.map((entry) => entry?.timeframe))
        const paperSymbolMode = findMode(paperEraEntries.map((entry) => entry?.symbol))
        const medianPaperTradesPerDay = median(paperEraEntries.map((entry) => entry?.tradesPerDay))
        const watchCount = paperEraEntries.filter((entry) => entry?.classification === 'watch').length
        return {
            currentFrontier,
            promotedAnchor,
            paperTimeframeMode,
            paperSymbolMode,
            medianPaperTradesPerDay,
            watchCount,
            paperEraCount: paperEraEntries.length,
            totalCount: entries.length,
        }
    }, [entries, entriesById, paperEraEntries])

    return (
        <div className='researchWhatWorkedPanel'>
            <div className='researchWhatWorkedHero presetStudyPanel'>
                <div className='positiveStrategiesHeroHeader'>
                    <div>
                        <div className='positiveStrategiesTitle'>What has already worked, and why</div>
                        <div className='positiveStrategiesSubtitle'>
                            Meta-analysis of the curated positive-history catalog plus the exhausted follow-up families around the current frontier.
                            This panel is meant to answer a harder question than “what is positive?”:
                            it shows which structural ideas repeatedly worked, why they worked, and where continuation honestly stopped.
                        </div>
                    </div>
                    <div className='positiveStrategiesMeta'>
                        <span>Catalog updated: {catalogState.lastUpdated}</span>
                        <span>Scope: curated positive history plus exhausted Elliott/neural follow-ups.</span>
                    </div>
                </div>

                <div className='researchWhatWorkedSummaryGrid'>
                    <div className='researchWhatWorkedSummaryCard'>
                        <span>Catalog entries studied</span>
                        <strong>{analysis.totalCount}</strong>
                        <small>Curated positives only, not every dead paper.</small>
                    </div>
                    <div className='researchWhatWorkedSummaryCard'>
                        <span>Paper-era frontier entries</span>
                        <strong>{analysis.paperEraCount}</strong>
                        <small>{analysis.watchCount} are still watch-level rather than promoted.</small>
                    </div>
                    <div className='researchWhatWorkedSummaryCard'>
                        <span>Dominant modern context</span>
                        <strong>{analysis.paperSymbolMode.value || 'n/a'} · {analysis.paperTimeframeMode.value || 'n/a'}</strong>
                        <small>
                            {analysis.paperSymbolMode.count}/{analysis.paperEraCount} paper-era positives share this symbol family.
                        </small>
                    </div>
                    <div className='researchWhatWorkedSummaryCard'>
                        <span>Median paper-era cadence</span>
                        <strong>{formatTradesPerDay(analysis.medianPaperTradesPerDay)} trades/day</strong>
                        <small>Most modern positives are sparse by design.</small>
                    </div>
                    <div className='researchWhatWorkedSummaryCard wide'>
                        <span>Current live frontier</span>
                        <strong>{analysis.currentFrontier?.label || 'n/a'}</strong>
                        <small>
                            {analysis.currentFrontier
                                ? `${analysis.currentFrontier.positiveCheckpoint} · ${formatTradesPerDay(analysis.currentFrontier.tradesPerDay)} trades/day`
                                : 'n/a'}
                        </small>
                    </div>
                    <div className='researchWhatWorkedSummaryCard wide'>
                        <span>Historical promoted anchor</span>
                        <strong>{analysis.promotedAnchor?.label || 'n/a'}</strong>
                        <small>
                            {analysis.promotedAnchor
                                ? `${formatApprox(analysis.promotedAnchor.expectedMonthlyPercent, 2, '%')} expected monthly · ${formatTradesPerDay(analysis.promotedAnchor.tradesPerDay)} trades/day`
                                : 'n/a'}
                        </small>
                    </div>
                </div>
            </div>

            <div className='researchWhatWorkedGrid'>
                <section className='presetStudyPanel researchWhatWorkedSection'>
                    <div className='presetStudyTitle'>Structural conclusions</div>
                    <div className='presetStudyMeta'>
                        These are the recurring mechanisms that survived the most continuation pressure.
                    </div>
                    <div className='researchWhatWorkedThesisGrid'>
                        {STRUCTURAL_THESES.map((thesis) => (
                            <article key={thesis.id} className='researchWhatWorkedThesisCard'>
                                <div className='researchWhatWorkedThesisTitle'>{thesis.title}</div>
                                <div className='researchWhatWorkedThesisBody'>{thesis.description}</div>
                                <EvidenceChips ids={thesis.evidenceIds} entriesById={entriesById} />
                            </article>
                        ))}
                    </div>
                </section>

                <section className='presetStudyPanel researchWhatWorkedSection'>
                    <div className='presetStudyTitle'>Paper-era frontier ladder</div>
                    <div className='presetStudyMeta'>
                        A compact map of how the modern M5 frontier climbed from temporal gating into cross-symbol contrastive rescue.
                    </div>
                    <div className='researchWhatWorkedLadder'>
                        {frontierLadder.map((entry, index) => (
                            <div key={entry.id} className='researchWhatWorkedLadderRow'>
                                <div className='researchWhatWorkedLadderStep'>{index + 1}</div>
                                <div className='researchWhatWorkedLadderMain'>
                                    <strong>{entry.label}</strong>
                                    <span>{entry.family}</span>
                                </div>
                                <div className='researchWhatWorkedLadderMetric'>
                                    <strong>{formatEdgeLabel(entry)}</strong>
                                    <span>{formatTradesPerDay(entry.tradesPerDay)} trades/day</span>
                                </div>
                            </div>
                        ))}
                    </div>
                </section>

                <section className='presetStudyPanel researchWhatWorkedSection'>
                    <div className='presetStudyTitle'>Motif map</div>
                    <div className='presetStudyMeta'>
                        Each motif below is computed from the current curated positive catalog and summarized with the strongest preserved representative.
                    </div>
                    <div className='researchWhatWorkedMotifTable'>
                        <div className='researchWhatWorkedMotifHeader'>
                            <span>Motif</span>
                            <span>Entries</span>
                            <span>Best representative</span>
                            <span>Median trades/day</span>
                        </div>
                        {motifRows.map((row) => (
                            <div key={row.id} className='researchWhatWorkedMotifRow'>
                                <div>
                                    <strong>{row.title}</strong>
                                    <small>{row.why}</small>
                                </div>
                                <span>{row.count}</span>
                                <span>{row.bestEntry?.label || 'n/a'}</span>
                                <span>{formatTradesPerDay(row.medianTradesPerDay)}</span>
                            </div>
                        ))}
                    </div>
                </section>

                <section className='presetStudyPanel researchWhatWorkedSection'>
                    <div className='presetStudyTitle'>Why recent continuations stopped</div>
                    <div className='presetStudyMeta'>
                        These lines did not earn new positive-history rows, but they are important because they explain where the current frontier stopped scaling.
                    </div>
                    <div className='researchWhatWorkedStopList'>
                        {EXHAUSTED_CONTINUATIONS.map((item) => (
                            <article key={item.id} className='researchWhatWorkedStopCard'>
                                <strong>{item.title}</strong>
                                <span>{item.papers}</span>
                                <p>{item.lesson}</p>
                            </article>
                        ))}
                    </div>
                </section>

                <section className='presetStudyPanel researchWhatWorkedSection researchWhatWorkedSectionWide'>
                    <div className='presetStudyTitle'>100 response hypotheses</div>
                    <div className='presetStudyMeta'>
                        A working hypothesis bank generated from the same `What worked` read.
                        These are not promoted conclusions.
                        They are the next materially different response ideas that stay consistent with what actually survived and what already exhausted.
                    </div>
                    <div className='researchWhatWorkedHypothesisMeta'>
                        <span>{RESEARCH_WHAT_WORKED_HYPOTHESIS_COUNT} hypotheses</span>
                        <span>Updated: {RESEARCH_WHAT_WORKED_HYPOTHESES_LAST_UPDATED}</span>
                        <span>Grouped by response family, not by paper number.</span>
                    </div>
                    <div className='researchWhatWorkedHypothesisGroups'>
                        {RESEARCH_WHAT_WORKED_HYPOTHESIS_GROUPS.map((group, index) => (
                            <details
                                key={group.id}
                                className='researchWhatWorkedHypothesisGroup'
                                open={index === 0}
                            >
                                <summary className='researchWhatWorkedHypothesisSummary'>
                                    <div>
                                        <strong>{group.label}</strong>
                                        <small>{group.summary}</small>
                                    </div>
                                    <span>{group.items.length} items</span>
                                </summary>
                                <div className='researchWhatWorkedHypothesisList'>
                                    {group.items.map((item, itemIndex) => (
                                        <article key={item.id} className='researchWhatWorkedHypothesisCard'>
                                            <div className='researchWhatWorkedHypothesisHeading'>
                                                <span>
                                                    {String(index * 20 + itemIndex + 1).padStart(2, '0')}
                                                </span>
                                                <strong>{item.title}</strong>
                                            </div>
                                            <p>{item.hypothesis}</p>
                                            <small>{item.whyItFits}</small>
                                        </article>
                                    ))}
                                </div>
                            </details>
                        ))}
                    </div>
                </section>

                <section className='presetStudyPanel researchWhatWorkedSection'>
                    <div className='presetStudyTitle'>Operator reading</div>
                    <div className='presetStudyMeta'>
                        High-signal rules that emerge when the positive catalog and the exhausted follow-up families are read together.
                    </div>
                    <div className='researchWhatWorkedReadingList'>
                        <div className='researchWhatWorkedReadingCard'>
                            <strong>What to trust</strong>
                            <p>Trust sparse direct-shell upgrades that remove negative held-out slices without broadening participation. The modern path from papers 57 to 128 repeatedly improved by cleaning ownership first and only tuning exits later.</p>
                        </div>
                        <div className='researchWhatWorkedReadingCard'>
                            <strong>What to treat cautiously</strong>
                            <p>Treat any wider inverse-rescue story cautiously. The current live rescue is real, but the positive catalog plus papers 140-143 show that broadening beyond `M5 prev_4` either ties through flat rows or degrades immediately.</p>
                        </div>
                        <div className='researchWhatWorkedReadingCard'>
                            <strong>What not to repeat mechanically</strong>
                            <p>Do not reopen local families just because a symbolic or neural layer can describe the same niche more elegantly. If the context inventory did not broaden, the system learned explanation, not new edge.</p>
                        </div>
                    </div>
                </section>
            </div>
        </div>
    )
}

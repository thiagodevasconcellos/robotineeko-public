# Research To Trader Workflow

This guide explains the intended end-to-end workflow that turns an idea into an
operable runtime configuration inside Robotineeko.

## Why This Workflow Exists

The platform is built around the idea that a strategy should move through
progressively stricter surfaces instead of jumping directly from "interesting"
to "live".

The normal progression is:

1. `Strategy`
2. `Backtester`
3. `Results`
4. `Research`
5. saved strategy or portfolio library
6. `Trader`
7. `/mobile` monitoring when useful

## Stage 1: Strategy

Use `Strategy` to define or inspect rule logic.

At this stage, the main question is:

- "what is the strategy actually doing?"

This is still an authoring and debugging surface, not a promotion surface.

## Stage 2: Backtester

Use `Backtester` to run the strategy under an explicit simulation contract.

At this stage, the main question is:

- "what happened under these market, broker, capital, and cost assumptions?"

This is where the run becomes comparable and reproducible.

## Stage 3: Results

Use `Results` to interpret the finished run.

At this stage, the main question is:

- "was this run actually good once execution assumptions were applied?"

The point is not only to read net PnL.

It is to inspect:

- execution assumptions
- cost drag
- drawdown
- cadence
- trade distribution

## Stage 4: Research

Use `Research` when the question is no longer about one isolated run.

At this stage, the main question is:

- "does this idea survive comparative scrutiny?"

That includes:

- variant comparison
- walk-forward concerns
- promotion review
- positive-history continuity
- scientific record maintenance

## Positive History Rule

`Positive history` is a continuity surface, not an automatic proof surface.

A row there means:

- the system found something worth preserving and comparing later

It does not automatically mean:

- the strategy is globally validated
- the strategy is ready for trader runtime

## Daytrade-Specific Discipline

For daytrade-oriented studies, short local positives are especially dangerous.

The current discipline is intentionally stricter:

- same-session flattening must hold
- short-window positives can be watch-level evidence
- true winners should survive broader history before being treated as
  operationally meaningful

This is exactly why the research funnel and the trader funnel are separate.

## Stage 5: Save Durable Library Entries

Only after a line becomes meaningfully relevant should it be turned into a
durable saved strategy or saved portfolio.

That library step matters because `Trader` should load stable execution
snapshots, not half-formed editor drafts.

## Stage 6: Trader

Use `Trader` once the strategy or portfolio is already justified as a runtime
candidate.

At this stage, the main question is:

- "how should this be executed and monitored now?"

That includes:

- execution path
- runtime mode
- sleeves or loaded portfolios
- broker scope
- armed state
- live-dispatch gate
- runtime monitor and history

## Stage 7: Mobile Monitoring

Use `/mobile` when you want runtime follow-up away from the desktop console.

It is best understood as:

- a monitoring companion

not:

- a full operator workbench

## Summary Rule

The safest mental model is simple:

- `Strategy` defines
- `Backtester` simulates
- `Results` explains
- `Research` judges
- `Trader` operates

When those roles stay clear, Robotineeko becomes much easier to use well.

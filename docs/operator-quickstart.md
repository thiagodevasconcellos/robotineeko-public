# Operator Quickstart

This guide explains the fastest safe path for an operator to understand and use
Robotineeko without having to reverse-engineer the whole platform first.

## First Mental Model

Think of Robotineeko as one connected workflow:

1. define or load strategies
2. simulate them in `Backtester`
3. inspect them in `Results`
4. compare and archive them in `Research`
5. operate selected lines in `Trader`

If that sequence is clear, the rest of the console becomes much easier to
understand.

## Recommended First Session

For a real first session, use this order:

1. open `Strategy`
2. inspect saved strategies or portfolios
3. move to `Backtester`
4. confirm broker, market, capital, and cost assumptions
5. run or inspect a backtest
6. open `Results`
7. open `Research`
8. only then move to `Trader`

That order prevents a common mistake:

- trying to treat `Trader` as if it were the first place to discover whether a
  strategy is valid

It is not.

`Trader` is the runtime execution surface, not the analytical proof surface.

## Choosing The Right Surface

Use the right tab for the right question.

- `Strategy`: What is the rule logic?
- `Backtester`: What happened under this simulation contract?
- `Results`: Was the run actually good?
- `Research`: Does the idea survive comparison and promotion review?
- `Trader`: What is the runtime doing right now?
- `Docs`: What is the authoritative reference for this subsystem?

## Broker Selection First

Before trusting a run, confirm the active broker context.

The selected broker affects:

- symbol validity
- default asset shell
- cost model
- trader scope

This matters especially when switching between:

- Forex-oriented instruments
- Brazilian B3 instruments

## Backtester Checklist

Before trusting a backtest, confirm:

- symbol
- timeframe
- history scope
- cost model
- asset type
- capital model
- whether the run is one strategy or a portfolio stack

If a run looks too optimistic or too pessimistic, the first place to inspect is
the `Execution` section in `Results`, not the raw PnL headline.

## Research Checklist

Use `Research` when the question is no longer:

- "what did this one run do?"

and becomes:

- "does this idea survive repeated scrutiny?"

Important discipline:

- a positive checkpoint is evidence
- a saved positive-history row is continuity
- neither one is automatic proof of a durable winner

## Trader Checklist

Use `Trader` only after the strategy or portfolio already makes sense as a
simulation and research object.

Before arming runtime behavior, confirm:

- execution path
- active broker profile
- loaded sleeves or portfolios
- operator armed state
- live-dispatch gate state
- runtime monitor and history

## Mobile Route

Use `/mobile` as a runtime companion, not as a full editing workstation.

It is best for:

- monitoring live status
- following runtime history
- checking portfolio or pipeline scope away from the desktop console

## Guest Demo

Guest demo mode is for safe inspection and presentation.

It is useful when you want to:

- show the product to someone
- expose the shape of the platform
- avoid operational or heavy backend actions

It is intentionally restricted and should not be mistaken for a normal operator
session.

## Common Mistakes To Avoid

- Do not judge a strategy only from one pretty chart.
- Do not confuse `Results` with `Research`.
- Do not treat a short local positive as proof of long-window robustness.
- Do not forget that broker context changes the meaning of the same strategy.
- Do not move straight into `Trader` before the strategy survives analysis.

## Recommended Next Reading

After this guide, the best next documents are:

1. [Broker And Cost Models](./broker-and-cost-models.md)
2. [Research To Trader Workflow](./research-to-trader-workflow.md)
3. [Public Surfaces And Access Modes](./public-surfaces-and-access-modes.md)

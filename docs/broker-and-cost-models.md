# Broker And Cost Models

This guide explains how Robotineeko thinks about broker scope, cost models, and
why those assumptions must be inspected before trusting any analytical result.

## Broker Scope Is A First-Class Input

In Robotineeko, broker selection is not just a label.

The active broker can influence:

- symbol compatibility
- market-domain defaults
- asset-type defaults
- cost-profile resolution
- trader runtime scope
- history and comparison interpretation

That is why the active broker should be confirmed early in every serious
backtest or runtime workflow.

## Current Built-In Cost Profiles

The main built-in profiles are:

- `FOREX.com`
- `OANDA`
- `CLEAR + B3`

These profiles do not all behave the same way.

### FOREX.com and OANDA

These are spread-and-slippage-style shells.

They mainly model:

- spread
- entry slippage
- close slippage
- target and stop execution friction

### CLEAR + B3

This profile models Brazilian listed products differently.

It applies:

- operational execution costs
- estimated tax lines for supported B3 shells

This is why the results now separate:

- `Operational costs`
- `Estimated taxes`
- `Total cost drag`

That distinction avoids presenting estimated taxation as if it were only a
broker fee.

## Cost Interpretation Rule

When a run looks expensive, inspect the breakdown before concluding why.

The right question is not only:

- "how big is total_cost?"

It is also:

- "how much of that drag is operational cost?"
- "how much is estimated tax?"

That is especially important for B3 runs.

## Asset Type And Broker Defaults

Broker-aware defaults exist so the common path stays simple, but they do not
replace operator judgment.

A broker switch can change:

- the default asset type
- the default cost profile
- the safest initial market context

This is intended behavior.

The system should not silently keep a stale Forex context when the operator is
actually working inside a B3 broker scope, or the inverse.

## Backtest Trust Checklist

Before trusting a result, confirm these fields in `Backtester` or `Results`:

- requested cost mode
- effective cost model
- broker scope
- asset type
- capital model
- history scope

If those assumptions are wrong, the rest of the run can be misleading even when
the strategy logic itself is correct.

## Trader Scope

Broker scope also affects `Trader`.

The runtime, history, and compare surfaces are expected to stay broker-aware so
that one broker context does not silently contaminate another.

## Research And Positive History

Broker scope matters in research too.

A positive line found under one broker or market shell should not be treated as
identical to the same nominal strategy under another shell unless that was
actually revalidated.

## Practical Rule

If there is any doubt, prefer explicitness.

- confirm the active broker
- confirm the effective cost model
- confirm the asset type
- only then interpret the PnL

In Robotineeko, cost realism is part of analytical correctness, not an optional
cosmetic detail.

# Broker And Cost Models

Robotineeko is not intentionally broker-agnostic. Broker context affects how symbols, cost assumptions, and runtime expectations should be interpreted.

## Why broker awareness matters

The same strategy can look very different depending on:

- instrument class
- execution assumptions
- spread and fee model
- market domain

That is why Robotineeko carries broker-aware context through charting, backtesting, and trader-facing workflows.

## Product implication

Backtesting is not treated as a single generic simulation. Cost modeling and market context are part of the interpretation contract, not an afterthought.

## Public snapshot note

This repository includes the code shape that supports broker-aware behavior, but does not publish live broker configs, runtime credentials, or production transport details.

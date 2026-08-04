# Analysis Workflow

This document explains the public-safe analytical workflow behind Robotineeko.

It focuses on how ideas move from definition to review without exposing private
runtime operation details.

## Core Workflow

The normal progression is:

1. `Strategy`
2. `Backtester`
3. `Results`
4. `Research`

That separation matters because each stage answers a different question.

## Stage 1: Strategy

The first step is to define or inspect the logic itself.

At this stage, the main question is:

- what is the rule set actually doing?

This is an authoring and inspection surface, not a proof surface.

## Stage 2: Backtester

The second step is to run the idea under an explicit simulation contract.

At this stage, the main question is:

- what happened under these assumptions?

Analytical trust depends on making those assumptions visible.

Important inputs include:

- symbol or market context
- timeframe
- history scope
- asset profile
- cost assumptions
- capital assumptions

## Stage 3: Results

The third step is to interpret the finished run.

At this stage, the main question is:

- was this outcome actually good once execution assumptions were applied?

That means looking beyond a single headline number.

Useful interpretation includes:

- cost drag
- drawdown
- cadence
- trade distribution
- consistency of the result shape

## Stage 4: Research

The fourth step is comparative scrutiny.

At this stage, the main question is:

- does this idea survive broader comparison?

This is where isolated results become part of a larger analytical program.

That can include:

- variant comparison
- continuity of promising findings
- structured review of strengths and weaknesses
- preservation of lines worth revisiting later

## Why The Workflow Is Split

Robotineeko intentionally separates these stages so that:

- authoring is not confused with proof
- one positive run is not confused with durable quality
- comparison is not reduced to one screenshot

The product is designed to reward explicitness and repeatability.

## Practical Interpretation Rule

The safest reading pattern is simple:

- `Strategy` defines
- `Backtester` simulates
- `Results` interprets
- `Research` judges

When those roles stay clear, the product is easier to understand and the
analytical output is easier to trust.

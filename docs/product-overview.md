# Product Overview

Robotineeko is a live web product for strategy authoring, simulation,
comparative analysis, and structured review workflows.

This public document is intentionally high level. It explains the product shape
without exposing private runtime logic, operational infrastructure, or
restricted data surfaces.

## What The Product Solves

Serious analytical workflows often get fragmented across too many tools.

Robotineeko is built to keep the main steps connected inside one product:

1. define or inspect strategies
2. run simulations under explicit assumptions
3. interpret the outcome
4. compare ideas over time
5. preserve useful findings in a structured workflow

## Main Product Surfaces

The main console is organized into specialized surfaces:

- `Strategy`
- `Portfolios`
- `Backtester`
- `Results`
- `Research`
- `Batch`
- `Neural`
- `Docs`

Each surface has a different role.

- `Strategy` focuses on rule definition and inspection.
- `Backtester` runs isolated simulations.
- `Results` interprets one completed run.
- `Research` compares, ranks, and preserves findings over time.
- `Batch` handles broader repeated study workflows.
- `Neural` groups model-related workbench features.
- `Docs` keeps product reference material close to the application.

## Architecture At A Glance

At a high level, the product combines:

1. a React-based frontend console
2. a backend API for application state and product workflows
3. execution and analysis services behind the main application flow
4. a documentation layer used both for maintenance and product understanding

This shape exists so the same product can support authoring, simulation,
comparison, and review without forcing the workflow to jump between unrelated
tools.

## Product Philosophy

Robotineeko is not designed around isolated chart screenshots or one-off runs.

The product favors:

- explicit assumptions
- repeatable analytical workflows
- structured comparison
- preserved continuity between promising findings

The goal is not just to produce an interesting result once. The goal is to make
the result inspectable, comparable, and reviewable.

## Public Scope

This public repository shows:

- the product narrative
- public-safe documentation
- repository structure
- portfolio-facing engineering context

It does not expose:

- production source code
- private runtime internals
- restricted data-room material
- credentials or host details
- internal operating logic

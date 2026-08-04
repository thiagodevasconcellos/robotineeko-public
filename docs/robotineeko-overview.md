# Robotineeko Overview

Robotineeko is a broker-aware quantitative trading workbench that unifies
strategy creation, backtesting, research, live trader runtime, and
operator-facing documentation in one product.

## What It Is

At its core, Robotineeko is designed to answer one practical problem:

- a serious trading workflow usually gets fragmented across too many tools

Instead of splitting authoring, simulation, research, live runtime, and
historical inspection into separate products, Robotineeko keeps them connected
inside one application.

## Main Product Surfaces

The main console is organized into specialized tabs:

- `Strategy`
- `Portfolios`
- `Backtester`
- `Results`
- `Research`
- `Batch`
- `Neural`
- `Trader`
- `Runtime`
- `Docs`

Each tab has a different role.

- `Strategy` authors logic.
- `Backtester` runs isolated simulations.
- `Results` interprets one run.
- `Research` compares and promotes ideas over time.
- `Trader` operates live or paper runtime execution.
- `Docs` exposes the project documentation inside the product itself.

## Routes Outside The Main Desktop Console

Robotineeko also exposes additional route families:

- `/mobile` for phone-first trader monitoring
- `/fund` for a private authenticated data room

Those routes are part of the same product family, but they are not the same
surface as the main desktop console.

## Runtime Model

The system combines four important layers:

1. a frontend console
2. a main backend API
3. a dedicated trade runtime service
4. an MT5 bridge layer

That shape is intentional.

- the frontend owns workflow and operator experience
- the main backend owns workspace, strategy, research, docs, and auth
- the trade service owns runtime execution concerns
- the MT5 bridge owns market data and order transport

## Broker-Aware Philosophy

Robotineeko is not broker-agnostic by accident.

Broker selection affects:

- which symbols make sense
- which default asset shell is used
- which cost model is applied
- how backtests should be interpreted
- how trader runtime and history should be scoped

This matters because the same strategy can look very different when:

- the instrument class changes
- the execution assumptions change
- the broker or market domain changes

## Research Philosophy

The platform favors a winner-first workflow.

That means a positive checkpoint is useful evidence, but not proof by itself.

The intended flow is:

1. find a plausible edge
2. replay it under realistic execution assumptions
3. compare it against alternatives
4. promote it only when it survives broader scrutiny

## Documentation Philosophy

Documentation is part of the product, not just repository decoration.

Robotineeko therefore keeps:

- GitHub-facing documentation in `README.md` and `docs/`
- authenticated in-app documentation in the `Docs` tab

That arrangement exists so maintainers, operators, and reviewers can all read
the same product story from different entry points.

## Recommended Next Reading

If you are new to the project, read these next:

1. [Operator Quickstart](./operator-quickstart.md)
2. [Public Surfaces And Access Modes](./public-surfaces-and-access-modes.md)
3. [Broker And Cost Models](./broker-and-cost-models.md)
4. [Research To Trader Workflow](./research-to-trader-workflow.md)

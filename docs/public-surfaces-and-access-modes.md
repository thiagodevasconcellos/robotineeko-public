# Public Surfaces And Access Modes

Robotineeko has more than one route surface and more than one access mode, so
it is important to understand what each one is for before judging the product.

## Main Public Route

The main route is:

- `/`

This is the desktop console shell and the broadest product surface.

It hosts:

- chart
- console tabs
- auth-aware workspace behavior
- guest showcase behavior

## Mobile Route

The mobile companion route is:

- `/mobile`

This is not a second full editing console.

It is a phone-first trader monitoring surface intended for:

- runtime inspection
- live status tracking
- mobile follow-up of portfolio or pipeline scope

## Fund Route

The private data-room route is:

- `/fund`

This route is intentionally separate from the public showcase.

It uses the same Robotineeko account system as the main app, but it is not
meant for guest demo access and does not expose its sensitive content through
the public frontend bundle alone.

## Normal Authenticated Access

Normal authenticated users get the real operator workflow.

That includes:

- workspace persistence
- strategy and portfolio library usage
- backtesting
- research operations
- trader runtime configuration

## Guest Demo Access

Guest demo access is a real authenticated session with a restricted policy.

The guest mode exists to let a reviewer or employer inspect the system without
letting that session stress the runtime or mutate durable operator state.

Guest mode can:

- inspect chart and console surfaces
- browse curated strategy and portfolio examples
- read docs
- inspect research and neural showcase material

Guest mode cannot:

- persist workspace state
- run heavy backtests
- launch research mutations
- arm or operate live trader controls

## Why The Guest Policy Exists

The guest mode is designed for safe presentation, not fake completeness.

That means the product intentionally prefers:

- a smaller but honest showcase

over:

- a misleading surface that pretends to offer full runtime power to a public
  demo

## Route Semantics Matter

Do not judge every route as if it were supposed to solve the same problem.

- `/` is the full console workbench
- `/mobile` is a monitoring companion
- `/fund` is a protected private room

The same is true for access modes:

- operator mode is for real work
- guest mode is for safe guided inspection

## Documentation Visibility

The in-app `Docs` tab is served by the backend docs catalog.

That means documentation is part of the authenticated application experience,
not only part of the repository.

Guest sessions can read a curated documentation surface, which makes the
product easier to understand during a showcase or portfolio review without
exposing the full internal backend and architecture corpus intended for
operators and maintainers.

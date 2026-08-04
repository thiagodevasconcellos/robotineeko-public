# Robotineeko Overview

Robotineeko is a broker-aware quantitative trading workbench that combines strategy authoring, backtesting, comparative research, neural experimentation, and runtime monitoring in one product.

## Core idea

Most serious trading workflows become fragmented across too many disconnected tools. Robotineeko is built around the opposite idea: keep authoring, simulation, review, and runtime follow-up inside one system.

## Main product surfaces

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

Each surface has a distinct job. Strategy defines logic, Backtester simulates, Results interprets one run, Research compares lines over time, and Trader focuses on runtime operation and monitoring.

## Architecture at a glance

The product is split into four main layers:

1. React frontend for the operator workflow.
2. FastAPI backend for auth, workspace state, documentation, chart and strategy APIs.
3. Dedicated runtime-oriented backend flows for trader execution monitoring.
4. A live-system bridge layer for market connectivity.

## Public snapshot policy

This public repository exposes the real application structure and substantial real code, but excludes live data, private runtime artifacts, and restricted business surfaces.

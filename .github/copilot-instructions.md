# Project Guidelines

> **Primary reference:** Read the repo's `CLAUDE.md` first — it is the single source of truth. This file provides supplementary Copilot-specific context.


## Build and Test

- Use Python 3.12+. Install with `pip install -e ".[dev]"`. Test with `pytest` (coverage threshold 80%).
- Lint/format with Ruff. Type check with mypy (strict mode).

## Architecture

- CloudIngenium fork of official Bitfinex Python API client (v2).
- Published as `bitfinex-api-py` v6.0.0. Core library for BfxLendingBot.
- REST + WebSocket communication with Bitfinex exchange.

## Conventions

- Build system: Hatchling. Pre-commit hooks via Ruff.
- Always use `Decimal` for financial values — never `float`.
- WebSocket-first, REST as fallback.

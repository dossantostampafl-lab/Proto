# Benchmark provenance

Proto uses benchmark-driven engineering: external projects are studied for mature patterns, then concepts are reimplemented against Proto's own contracts and safety boundary. This document records the principal references used during the current hardening cycle.

## TemiKayode/parallax

- License verified: MIT.
- Role: prediction-market architecture and risk benchmark.
- Patterns studied: fail-closed/veto-only risk gates, reservations for working orders, cumulative batch checks, correlated exposure limits, reconciliation gating, deterministic paper execution.
- Proto adaptation: Rust risk hardening keeps Proto's existing `rust_decimal` risk model and adds reservations, batch evaluation and reconciliation semantics. The implementation is expressed in Proto types and tests rather than importing the external crate graph.

## spencerfletcher/market-maker

- License verified: MIT.
- Role: execution/data-quality benchmark.
- Patterns studied: exact monetary arithmetic, feed degradation states, persistence discipline, deterministic fixtures and safety-focused regression testing.
- Proto adaptation: exact arithmetic is used at monetary/accounting boundaries; feed health and temporal microstructure features are implemented around Proto's canonical `MarketTick`; execution remains simulation-only.

## nautechsystems/nautilus_trader

- License verified: LGPL-3.0.
- Role: architecture/lifecycle reference.
- Patterns studied: event-driven lifecycle separation, replay/execution abstractions, portfolio accounting boundaries and deterministic research workflows.
- Proto adaptation: concepts and interface boundaries only. Proto does not vendor or copy NautilusTrader implementation code in this hardening cycle.

## Proto-originated design

The following are Proto-specific compositions rather than direct external modules:

- hierarchical `HTF regime -> MTF setup -> LTF trigger` trend context;
- separation of calibrated/fair probability from trend veto decisions;
- deterministic research pipeline combining calibration, edge, expected value, Hawkes, synthetic Greeks and time exposure;
- replay phase order `MARKET_DATA -> FEATURES -> SIGNAL -> RISK -> ORDER -> FILL -> PORTFOLIO`;
- Validation Engine composition: purged walk-forward, embargo, DSR, PBO, block-bootstrap Monte Carlo, regime robustness and parameter stability;
- API and dashboard safety invariants explicitly reporting `financial_connectivity=false` and `real_money_execution=false`.

## Reuse policy

1. Prefer independent reimplementation of concepts using Proto contracts.
2. If substantial MIT-licensed source is ever copied, preserve required copyright/license notices.
3. Treat LGPL references as architecture/interface references unless a deliberate compliance review approves linkage or code reuse.
4. Record future benchmark-derived changes in this file or a successor ADR.
5. Benchmark maturity is not evidence of financial profitability. Proto validation must establish its own empirical evidence from replay/paper datasets.

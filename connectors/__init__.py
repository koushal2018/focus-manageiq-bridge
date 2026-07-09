"""Connector framework — the 'connect a source and it runs' layer.

AnyBank's only production job is to register a data source (type + location +
credential ref + schedule). The platform discovers the export, normalizes it
to FOCUS v1.3, and loads it. Adding a SOURCE INSTANCE is a registry row, no
deploy. Adding a SOURCE TYPE is a new adapter implementing the contract in
`contract.py`.

The hard part (per-provider FOCUS mappings + the asymmetric join) was proven
in the PoC's `normalizer/` and `join/`; this package wraps those as registered
adapters so onboarding is configuration, not code. See
docs/production-architecture.md §0a.
"""

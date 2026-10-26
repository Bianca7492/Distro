"""BOE revenue truth — Stage 1.

Statement ingestion, normalisation, matching and reconciliation.

The invariant this package enforces: **every euro that moves traces to a
statement line**. Unmatched revenue is held in suspense and never distributed.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"

# Revenue Truth — Stage 1

The part of the platform that turns a distributor statement into a per-work net
revenue figure that **matches the distributor's own totals to the cent**.

This is Gate 1. Nothing downstream (splits, notarisation, payouts, instruments)
is trusted until this reconciles.

## The invariant

> Every euro that moves traces to a statement line.

Money is **integer minor units plus a currency code**. Never a float. A
reconciliation that is off by one cent fails the gate.

## What it does

```
statement file ──► parse ──► hash ──► store ──► match ──┬──► revenue_facts
   (DSR XML)                                            │      (matched)
   (CSV)                                                └──► suspense
                                                               (ambiguous + unmatched)
```

Three outcomes per line, and no fourth:

| Outcome | Meaning | Action |
| :--- | :--- | :--- |
| `matched` | exact ISRC / UPC / fingerprint | revenue fact written |
| `ambiguous` | fuzzy title+artist above 0.75 | **held in suspense, human review** |
| `unmatched` | no match at all | **held in suspense, never distributed** |

**Fuzzy matches are never auto-distributed.** That is the rule that keeps a
wrong ISRC from paying the wrong artist.

## Modules

| Module | Responsibility |
| :--- | :--- |
| `money.py` | exact Money type; largest-remainder `split_exact` |
| `hashing.py` | streaming SHA-256; statement manifest + root hash |
| `schema.py` | portable DDL (SQLite dev / Postgres prod) |
| `store.py` | repository; audit log; suspense ledger |
| `dsr_parser.py` | DDEX DSR XML, namespace-aware, memory-bounded |
| `csv_parser.py` | header-tolerant distributor CSV |
| `matcher.py` | ISRC → UPC → fingerprint → fuzzy |
| `pipeline.py` | ingest orchestration + the reconciliation assertion |
| `reconcile.py` | the Gate 1 report |
| `cli.py` | `ingest`, `reconcile`, `inspect` |

## Usage

```bash
pip install lxml rapidfuzz pytest

# ingest a statement against a catalogue
python3 -m revenue.cli --db revenue.db ingest tests/fixtures/sample_dsr.xml \
    --catalogue revenue/examples/catalogue.json

# reconcile a statement (exit 1 if it does not balance)
python3 -m revenue.cli --db revenue.db reconcile <statement-id>

# list the lines with their match status
python3 -m revenue.cli --db revenue.db inspect <statement-id>
```

## Tests

```bash
python3 -m pytest tests/ -q
```

37 tests, including adversarial cases:

| Case | Why it matters |
| :--- | :--- |
| 20,000-line DSR | memory must stay bounded (the `music-metadata` failure) |
| orphaned deal terms | unmatched revenue held, never distributed |
| duplicate lines | summed, not silently deduped |
| negative adjustments | distributor clawbacks must not break the sum |
| mixed currency in one file | rejected, never guessed |
| suspense excluded from matched | facts == matched, always |
| empty statement | fails loudly |
| split across 1,000 holders | no cent lost or created |
| manifest tampering | hash changes detect it |

## Memory safety

`dsr_parser` uses `lxml.iterparse` with `elem.clear()`, so a large statement is
streamed rather than loaded. The 20,000-line test verifies this.

## Next (Stage 2)

The catalogue side: `tracks` rows are currently loaded from JSON. Stage 2 wires
this to the Supabase schema and the ERN delivery path.

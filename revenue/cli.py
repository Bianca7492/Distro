"""CLI for the revenue-truth pipeline.

    python -m revenue.cli ingest <statement-file> [--db PATH] [--catalogue PATH]
    python -m revenue.cli reconcile <statement-id> [--db PATH]
    python -m revenue.cli inspect <statement-id> [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import ingest
from .reconcile import reconcile
from .store import Store


def _load_catalogue(store: Store, path: str) -> int:
    data = json.loads(Path(path).read_text())
    n = 0
    for t in data:
        store.upsert_track(
            internal_id=t["internal_id"],
            title=t["title"],
            artist=t["artist"],
            isrc=t.get("isrc"),
            duration_ms=t.get("duration_ms"),
            fingerprint=t.get("fingerprint"),
        )
        n += 1
    return n


def cmd_ingest(args) -> int:
    store = Store(args.db)
    if args.catalogue:
        n = _load_catalogue(store, args.catalogue)
        print(f"loaded {n} catalogue tracks")
    res = ingest(store, args.statement)
    print(f"statement   {res.statement_id}")
    print(f"file hash   {res.file_hash}")
    print(f"lines       {res.lines}")
    print(f"  matched   {res.matched}")
    print(f"  ambiguous {res.ambiguous}")
    print(f"  unmatched {res.unmatched}")
    rec = reconcile(store, res.statement_id)
    print(rec.report())
    return 0 if rec.balances else 1


def cmd_reconcile(args) -> int:
    store = Store(args.db)
    rec = reconcile(store, args.statement_id)
    print(rec.report())
    return 0 if rec.balances else 1


def cmd_inspect(args) -> int:
    store = Store(args.db)
    for row in store.lines(args.statement_id):
        print(
            f"{row['line_no']:>4}  {row['status']:<10}"
            f" {row['raw_isrc'] or '-':<14}"
            f" {(row['raw_title'] or '-')[:28]:<30}"
            f" {row['net_minor'] / 100:>10.2f} {row['currency']}"
            f"  {row['match_method'] or ''}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="revenue")
    p.add_argument("--db", default="revenue.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="ingest a statement file")
    pi.add_argument("statement")
    pi.add_argument("--catalogue")
    pi.set_defaults(func=cmd_ingest)

    pr = sub.add_parser("reconcile", help="reconcile a statement")
    pr.add_argument("statement_id")
    pr.set_defaults(func=cmd_reconcile)

    px = sub.add_parser("inspect", help="list statement lines")
    px.add_argument("statement_id")
    px.set_defaults(func=cmd_inspect)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

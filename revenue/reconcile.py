"""Reconciliation — the Gate 1 proof.

Gate 1 requires: given a real statement, produce a per-work net revenue figure
that matches the distributor's own totals *to the cent*.

This module produces that comparison and refuses to pass on a discrepancy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .money import Money
from .store import Store


@dataclass
class Reconciliation:
    statement_id: str
    currency: str
    statement_total_minor: int
    matched_minor: int
    suspense_minor: int
    facts_minor: int
    suspense_rows: int

    @property
    def accounted_minor(self) -> int:
        return self.matched_minor + self.suspense_minor

    @property
    def difference_minor(self) -> int:
        return self.statement_total_minor - self.accounted_minor

    @property
    def balances(self) -> bool:
        return self.difference_minor == 0

    def report(self) -> str:
        cur = self.currency
        lines = [
            f"Reconciliation for statement {self.statement_id}",
            f"  currency            {cur}",
            f"  statement total     {Money(self.statement_total_minor, cur)}",
            f"  matched (facts)     {Money(self.matched_minor, cur)}",
            f"  suspense            {Money(self.suspense_minor, cur)}"
            f"   ({self.suspense_rows} lines)",
            f"  accounted           {Money(self.accounted_minor, cur)}",
            f"  difference          {Money(self.difference_minor, cur)}",
            f"  balances            {'YES' if self.balances else 'NO'}",
        ]
        return "\n".join(lines)


def reconcile(store: Store, statement_id: str) -> Reconciliation:
    st = store.get_statement(statement_id)
    currency = st["currency"]

    row = store.conn.execute(
        "SELECT COALESCE(SUM(net_minor),0) AS t FROM statement_lines"
        " WHERE statement_id=?",
        (statement_id,),
    ).fetchone()
    statement_total = int(row["t"])

    row = store.conn.execute(
        "SELECT COALESCE(SUM(net_minor),0) AS t, COUNT(*) AS n"
        " FROM statement_lines WHERE statement_id=? AND status='matched'",
        (statement_id,),
    ).fetchone()
    matched = int(row["t"])

    row = store.conn.execute(
        "SELECT COALESCE(SUM(amount_minor),0) AS t, COUNT(*) AS n"
        " FROM suspense WHERE line_id IN"
        " (SELECT id FROM statement_lines WHERE statement_id=?)",
        (statement_id,),
    ).fetchone()
    suspense = int(row["t"])
    suspense_rows = int(row["n"])

    row = store.conn.execute(
        "SELECT COALESCE(SUM(net_minor),0) AS t FROM revenue_facts"
        " WHERE statement_id=?",
        (statement_id,),
    ).fetchone()
    facts = int(row["t"])

    return Reconciliation(
        statement_id=statement_id,
        currency=currency,
        statement_total_minor=statement_total,
        matched_minor=matched,
        suspense_minor=suspense,
        facts_minor=facts,
        suspense_rows=suspense_rows,
    )


def assert_reconciled(store: Store, statement_id: str) -> Reconciliation:
    """Raise unless the statement reconciles exactly."""
    rec = reconcile(store, statement_id)
    if not rec.balances:
        raise AssertionError(rec.report())
    return rec

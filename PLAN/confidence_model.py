#!/usr/bin/env python3
"""Reproduces every confidence figure in 04-CONFIDENCE-MODEL.md.

Model from the BOE research corpus:
  P(success) = P(gate) * P(adoption) * P(liquid)
  P(gate)    = P(reg) * P(DSR) * P(audit) * P(pool)
"""

REG, DSR, AUDIT, POOL, ADOPT, LIQ = 0.55, 1.00, 0.85, 0.45, 0.927, 0.993

FLOAT, OMEGA, THRESH = 142_311_666, 0.05, 10_000
DAILY_VOL = FLOAT * OMEGA / 21


def liquidity(n: int) -> float:
    """Total float split n ways; the liquidity threshold is per instrument."""
    v = DAILY_VOL / n
    if v >= THRESH * 3:
        return 0.993
    if v >= THRESH:
        return 0.97
    return max(0.05, 0.97 * (v / THRESH))


def p_success(n: int, reg_decay=1.0, audit_decay=1.0, pool_decay=1.0) -> float:
    reg = REG * reg_decay ** (n - 1)
    audit = AUDIT * audit_decay ** (n - 1)
    pool = POOL * pool_decay ** (n - 1)
    return reg * DSR * audit * pool * ADOPT * liquidity(n)


def pool_with_buyers(n: int, q: float, gap=1.50, per_buyer=0.05) -> float:
    """Steelman: each data class has an external buyer with probability q."""
    closed = min(gap, n * q * per_buyer)
    return min(0.90, POOL + 0.30 * (closed / gap))


def main() -> None:
    baseline = REG * DSR * AUDIT * POOL * ADOPT * LIQ
    print(f"daily volume at baseline float: ${DAILY_VOL:,.0f}/day")
    print(f"baseline P(success), N=1: {baseline * 100:.1f}%\n")

    print("Heterogeneous revenue-bearing instruments (reg x0.85, audit x0.98, pool x0.97):")
    for n in (1, 2, 3, 5, 10, 20):
        print(f"  N={n:>3}  {p_success(n, 0.85, 0.98, 0.97) * 100:5.1f}%")

    print("\nSame-class scaling (only audit + liquidity drag):")
    for n in (1, 5, 10, 25, 50):
        print(f"  N={n:>3}  {p_success(n, 1.00, 0.995, 1.00) * 100:5.1f}%")

    print("\nSteelman: each class has an external buyer with probability q")
    print(f"  {'N':>3} " + " ".join(f"{'q=' + str(q):>8}" for q in (1.0, 0.7, 0.5, 0.3)))
    for n in (1, 2, 3, 5, 10, 20):
        row = []
        for q in (1.0, 0.7, 0.5, 0.3):
            reg = REG * 0.85 ** (n * 0.5 - 1) if n * 0.5 >= 1 else REG
            row.append(f"{reg * DSR * AUDIT * pool_with_buyers(n, q) * ADOPT * LIQ * 100:7.1f}%")
        print(f"  {n:>3} " + " ".join(row))

    print("\nLevers (single instrument):")
    print(f"  baseline                                  {baseline * 100:5.1f}%")
    print(f"  + ARPU >= $3 (pool 0.45 -> 0.80)          {REG * DSR * AUDIT * 0.80 * ADOPT * LIQ * 100:5.1f}%")
    print(f"  + licence (reg 0.55 -> 0.85)              {0.85 * DSR * AUDIT * 0.80 * ADOPT * LIQ * 100:5.1f}%")
    print(f"  + audit (audit 0.85 -> 0.95)              {0.85 * DSR * 0.95 * 0.80 * ADOPT * LIQ * 100:5.1f}%")
    print(f"  ceiling (every gate = 1.0)                {ADOPT * LIQ * 100:5.1f}%")


if __name__ == "__main__":
    main()

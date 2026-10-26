#!/usr/bin/env python3
"""Engineered demand, scarcity pricing and network effects.

Reproduces every figure in 05-ADDENDUM-ENGINEERED-DEMAND.md.
"""

import math

REG, DSR, AUDIT, ADOPT, LIQ = 0.55, 1.00, 0.85, 0.927, 0.993
POOL_PER_USER = 20 * 30 * 0.0035  # $2.10 obligation per user/month
RHO = 0.70
BAR = POOL_PER_USER / RHO  # $3.00


def scarcity(d0=100_000.0, s=1_000.0, n_max=20):
    """Price = D(N)/S with demand growing as N**gamma."""
    print("Price under rationing (D grows with N, supply capped):")
    print(f"  {'N':>4} {'gamma=0.5':>12} {'gamma=1.0':>12} {'gamma=2.0':>12}")
    for n in (1, 2, 5, 10, 20):
        cells = [f"{d0 * n ** g / s:>12,.1f}" for g in (0.5, 1.0, 2.0)]
        print(f"  {n:>4} " + " ".join(cells))
    print("  -> price rises iff elasticity gamma > 1\n")


def premium_vs_pool(mau=2_000_000):
    pool = mau * 20 * 30 * 0.0035
    print(f"Monthly pool owed at {mau:,} MAU: ${pool:,.0f}")
    for n, prem in ((5, 5e6), (20, 50e6), (100, 500e6)):
        print(f"  N={n:>3} premium ${prem:>14,.0f} funds pool for {prem / pool:>6.2f} months")
    print("  -> one-time premium is not a recurring payer\n")


def network_effect(d0=0.20, m_base=1_000_000.0):
    print("Per-user data value d = d0 * (M/1M)^delta:")
    print(f"  {'MAU':>12} {'delta=0':>9} {'delta=0.1':>9} {'delta=0.2':>9} {'delta=0.3':>9}")
    for m in (500_000, 2_000_000, 20_000_000):
        cells = [f"{d0 * (m / m_base) ** d:>9.3f}" for d in (0, 0.1, 0.2, 0.3)]
        print(f"  {m:>12,} " + " ".join(cells))
    print("  -> delta=0: MAU does not raise per-user value\n")


def pool_prob(eff, bar=BAR):
    if eff >= bar:
        return min(0.90, 0.80 + 0.10 * min(1.0, (eff - bar) / bar))
    return max(0.10, 0.45 * (eff / bar))


def eff_arpu(m, sub, ads, d0, delta, venue):
    return sub + ads + d0 * (m / 1_000_000) ** delta + venue


def p_success(m, sub, ads, d0, delta, venue):
    a = eff_arpu(m, sub, ads, d0, delta, venue)
    return REG * DSR * AUDIT * pool_prob(a) * ADOPT * LIQ, a


def main() -> None:
    scarcity()
    premium_vs_pool()
    network_effect()

    print("Composite ARPU vs the $3.00 bar:")
    scenarios = [
        ("emerging, no network", 2_000_000, 0.40, 0.40, 0.20, 0.0, 0.10),
        ("emerging + network", 2_000_000, 0.40, 0.40, 0.20, 0.3, 0.10),
        ("mixed + network", 2_000_000, 1.50, 0.70, 0.20, 0.3, 0.30),
        ("engineered 20M + network", 20_000_000, 1.50, 0.70, 0.20, 0.3, 0.30),
        ("engineered 20M, no network", 20_000_000, 0.40, 0.40, 0.20, 0.0, 0.10),
    ]
    print(f"  {'scenario':<30} {'MAU':>11} {'ARPU':>7} {'clears':>7}")
    for label, m, sub, ads, d0, delta, ven in scenarios:
        a = eff_arpu(m, sub, ads, d0, delta, ven)
        print(f"  {label:<30} {m:>11,} {a:>7.2f} {'YES' if a >= BAR else 'no':>7}")

    print("\nThreshold: delta needed at 2M MAU, mixed market")
    base = 1.50 + 0.70 + 0.30
    need = (BAR - base) / 0.20
    delta_star = math.log(need) / math.log(2.0)
    print(f"  base ${base:.2f}; need data ${BAR - base:.2f}/user -> delta* = {delta_star:.2f}")
    print("  (Metcalfe is delta=1.0; realistic data products ~0.1-0.3)\n")

    print("P(success) with the mechanism engaged:")
    print(f"  {'delta':>6} {'MAU':>11} {'ARPU':>7} {'P(success)':>11}")
    for delta in (0.0, 0.1, 0.2, 0.3, 0.4):
        for m in (2_000_000, 20_000_000):
            p, a = p_success(m, 1.50, 0.70, 0.20, delta, 0.30)
            print(f"  {delta:>6.1f} {m:>11,} {a:>7.2f} {p * 100:>10.1f}%")


if __name__ == "__main__":
    main()

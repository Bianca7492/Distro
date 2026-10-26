# Confidence Model — Before and After

The arithmetic behind the confidence rate, computed from the research model
(`boe-research`: `BOE-FINAL-SYNTHESIS.md`, `BOE-OWN-DSP-STRATEGY.md`,
`BOE-2026-AGENT-RAILS.md`).

---

## 1. The formula

```
P(success) = P(gate) × P(adoption ≥ 5,000) × P(market liquid)

P(gate)    = P(regulatory) × P(DSR access) × P(audit) × P(pool funded)
```

| Factor | Value | Source |
| :--- | ---: | :--- |
| Regulatory clearance | 0.55 | No repo changes this; a regulator grants it |
| DSR access (own DSP) | 1.00 | You generate your own DSR |
| Independent audit | 0.85 | `dsrf` conformance helps; a human signs |
| Pool funded (ARPU ≥ $3) | 0.45 | Pricing/market question |
| Adoption ≥ 5,000 artists | 0.927 | Monte Carlo, capacity-capped Bass |
| Market liquid | 0.993 | Float $142M, $339k/day vs $10k threshold |

**Baseline:** `0.55 × 1.00 × 0.85 × 0.45 × 0.927 × 0.993 = 0.194` → **~19%**

---

## 2. The instrument-count question

Adding **more revenue-bearing instruments** does *not* raise confidence. It
lowers it, because the pool has no term for instrument count:

```
Pool owed   = MAU × streams/MAU/day × 30 × rate     ← independent of N
Break-even  = 2.10 / ρ  (ARPU)                      ← independent of N
```

At 2M MAU: **$4.2M/month owed**, regardless of how many instruments exist.
More instruments = **more claims on the same dollars**.

### Heterogeneous instruments ("mint data = any information")

Each new revenue-bearing class multiplies three independent exposures:

| Exposure | Effect per class |
| :--- | :--- |
| Securities (separate instrument → separate characterisation) | ×0.85 |
| Audit (an auditor signs per class) | ×0.98 |
| Liquidity (one float split N ways, threshold per instrument) | dilution |

| Instruments | P(success) |
| ---: | ---: |
| 1 (baseline) | **19.4%** |
| 2 | 15.6% |
| 3 | 12.6% |
| 5 | 8.3% |
| 10 | 2.8% |
| 20 | 0.3% |

**Same-class scaling** (more tracks under one instrument type) is gentler —
19.4% → 16.8% at N=25 — because legal and pool costs are shared. But it still
only ever goes **down**.

---

## 3. The only case that holds: external payers

If each new data class has a **genuine, named, external buyer**, the
pool-funding factor can recover. Let `q` = P(a given class has a real buyer):

| N | q=1.0 | q=0.7 | q=0.5 | q=0.3 |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 19.8% | 19.7% | 19.6% | 19.5% |
| 2 | 20.2% | 20.0% | 19.8% | 19.6% |
| 3 | 19.0% | 18.7% | 18.4% | 18.2% |
| 5 | 16.9% | 16.4% | 16.0% | 15.7% |
| 10 | 12.4% | 11.7% | 11.2% | 10.8% |
| 20 | 6.5% | 5.9% | 5.5% | 5.1% |

**Even at q=1.0 it only holds at N=2, then decays.** The minting never adds
confidence. Only the payer does. `q` is unproven beyond your own engagement data.

**Conclusion:** the plan targets **one revenue-bearing instrument (I1) + one
non-revenue instrument (I2) + data-licensing as a revenue line** — not a
portfolio of minted data claims.

---

## 4. What actually raises the number

| Lever | From → To | Where in the plan |
| :--- | :--- | :--- |
| Demonstrate **$3+ weighted ARPU** | 0.194 → 0.345 | S0-05…07 (W6) |
| Secure the **licence pathway** | 0.345 → 0.534 | S0-09 (W6) |
| Publish the **audit protocol** | 0.534 → 0.596 | S8, but publish at S2 |
| **All three** | ceiling **0.922** | Gates 0 + 8 |

**None of the high-value levers is minting.** They are pricing, licence, audit.

---

## 5. Confidence by plan checkpoint

| Checkpoint | Gate passed | P(success) |
| :--- | :--- | ---: |
| Today (nothing done) | — | 19.4% |
| After Stage 0 (decisions) | GATE0 | **up to 53.4%** if licence pathway + ARPU clear |
| After Stage 1 (revenue truth) | GATE1 | + audit credibility |
| After Stage 4 (splits proven) | GATE4 | build risk removed |
| After Stage 6 (real payout) | GATE6 | adoption evidence begins |
| After Stage 8 (audit passed) | GATE8 | **up to 59.6%** |
| Fully funded + licensed + audited | all | **ceiling 92%** |

**The build is ~92% likely to succeed.** The gap to the ceiling is entirely
institutional: licence, ARPU, audit. Every remaining blocker is a contract or a
price — not code.

---

## 6. The rule that governs every decision

> **Every dollar must trace to a listener or an advertiser.**
> If it traces to a new buyer instead, it is the loop that killed Royal.io.

Corollary for this plan: **mint fewer instruments, not more.** Add *payers*
(data buyers), not *claims*.

---

## 7. How to update this model

The model is a live artifact. Update `model/boe_model.py` (in `boe-research`) or
this table whenever a gate factor moves, and re-run:

```bash
python3 PLAN/confidence_model.py    # reproduces every number in this file
```

Track the four gate factors on the task board at `S9-08` (Gate-factor tracker
update), reviewed at each gate.

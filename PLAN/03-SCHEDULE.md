# Schedule, Milestones and Duration

Derived arithmetically from `task-board.csv` (130 tasks, 435 person-days) at a
capacity of an **8-person team**. All durations are calendar weeks.

---

## 1. Headline

| Metric | Value |
| :--- | ---: |
| Total effort | **435 person-days** (87 person-weeks) |
| Team size | 8 people (see `README.md` §5) |
| **Total calendar to launch** | **~36 weeks (~8.3 months)** |
| Critical path | S0 → S2 → S3 → S4 → S6 → S7 → S8 → S9 (**~41 weeks** worst case) |
| First real artist paid | **Week 18** (end of Stage 6) |
| First real trade | **Week 15** (Stage 5) |
| Production launch | **Week 36** |

The 36-week figure assumes Stage 0 runs **concurrently** with Stages 1 and 2.
If Stage 0 slips, Stages 3 and 5 slip with it — that is the whole risk of the
plan and why Stage 0 is front-loaded.

---

## 2. Stage timetable

| Stage | Effort (pd) | Team | Duration | Starts | Ends | Gate |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| S0 Decisions & Legal | 45 | 1.5 | 6 w | W1 | W6 | GATE0 |
| S1 Revenue Truth (DSR) | 47 | 2.0 | 5 w | W1 | W5 | GATE1 |
| S2 Identity & Catalog | 65 | 3.0 | 5 w | W1 | W5 | GATE2 |
| S3 Minting (I1/I2) | 34 | 2.0 | 4 w | W7 | W10 | GATE3 |
| S4 Royalty Engine & Bridge | 38 | 2.0 | 4 w | W11 | W14 | GATE4 |
| S5 Trading (venue) | 46 | 2.0 | 5 w | W11 | W15 | GATE5 |
| S6 Settlement & Payouts | 39 | 2.5 | 4 w | W15 | W18 | GATE6 |
| S7 Agent-Native & Data Licence | 45 | 2.0 | 5 w | W19 | W23 | GATE7 |
| S8 Hardening & Audit | 47 | 2.0 | 9 w | W24 | W32 | GATE8 |
| S9 Launch & Operate | 29 | 1.5 | 4 w | W33 | W36 | GATE9 |

**Note:** S3 is blocked by **both** S0 and S2, so its start is `max(W6, W5)+1 = W7`.
S5 starts at W11 in parallel with S4 because both depend on S3 only.
S8 carries a **4-week external audit latency** on top of internal work.

---

## 3. Gantt (weeks)

```
Wk  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 ... 32 33 ... 36
S0  ████████████████████
S1  █████████████████
S2  █████████████████
S3                     ███████████████
S4                                    ███████████████
S5                                    ████████████████████
S6                                                   ███████████████
S7                                                                  ████████████████████
S8                                                                                     ████████████████████████████
S9                                                                                                              ███████████████
    ▲        ▲                    ▲         ▲              ▲                    ▲
    │        │                    │         │              │                    │
  start   GATE0/1/2            GATE3     GATE4          GATE6                GATE7/8
```

---

## 4. Milestones

| # | Milestone | Week | Proof |
| ---: | :--- | ---: | :--- |
| M0 | Decisions signed (instrument, licence, pricing, statement access) | W6 | Counsel memo + pricing sheet |
| M1 | Reconciliation matches to the cent | W5 | Reconciliation report |
| M2 | ERN delivered to test DSP and ingested back | W5 | Delivery receipt |
| M3 | I1 transfer to ineligible wallet reverts | W10 | Revert tx hash |
| M4 | Statement → 20 holder credits, reconciled | W14 | Distribution tx hashes |
| M5 | Permissioned I1 market live on testnet | W15 | Trade txs |
| M6 | First test-mode fiat payout reconciled | W18 | Bank reconciliation |
| M7 | Agent payout blocked by contract bound | W23 | Blocked-payment test |
| M8 | External audit passed, no criticals | W32 | Audit report |
| M9 | First real artist paid; first real trade | W36 | Payout + trade tx |

---

## 5. What can be parallelised

| Track | Runs from | Runs to | Notes |
| :--- | :--- | :--- | :--- |
| Legal & pricing (S0) | W1 | W6 | Independent of code |
| Revenue truth (S1) | W1 | W5 | Independent of identity work |
| Identity & catalog (S2) | W1 | W5 | Needs S0 wording for the credential |
| Minting (S3) | W7 | W10 | Hard-blocked by GATE0 |
| Royalty + venue (S4/S5) | W11 | W15 | Overlap; share the eligibility layer |
| Settlement (S6) | W15 | W18 | Needs GATE4 |
| Agent + data (S7) | W19 | W23 | Data-licence pilot can start at W5 (S2-18) |

**Highest-leverage parallelism:** start the **data-licence pilot** (S7-07/08/13)
as soon as the stability index exists (W5) rather than waiting for W19. It is the
one lever that adds real cash to the pool, and it has the longest external lead
time of any commercial task.

---

## 6. Effort distribution

| Role | Approx. person-days | Notes |
| :--- | ---: | :--- |
| BE (backend) | ~150 | Largest single block |
| SC (contracts) | ~75 | Front-loaded into S3–S6 |
| DATA (DDEX/DSR) | ~55 | Concentrated in S1/S2 |
| FE (frontend) | ~30 | Catalog, review queue, venue, payout UI |
| DO (devops) | ~30 | Infra, fingerprint svc, rails, monitoring |
| QA | ~45 | Adversarial and integration testing |
| PM / TL | ~40 | Decisions, gates, methodology, coordination |
| OPS | ~35 | Pricing, statements, payouts, pilot |
| LEG / AUD | ~45 | External; runs in parallel |

---

## 7. Schedule risk

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| **GATE0 slips** | S3/S5 slip 1:1 | Front-load S0; run counsel from W1 |
| No distributor statement by W2 | S1 slips | Fall back to a synthetic DSR fixture; chase in parallel |
| Audit findings at W32 | Launch slips | Audit early drafts at W24, not at the end |
| ARPU below $3.00 | Pool unfunded (see `04`) | Re-price or re-mix markets at W6, not W30 |
| Venue licence refused | S5 must be cut | Keep I2 (collectible) as the shippable fallback |
| Team ramp | Early stages understaffed | 2 BE + 1 DATA from day one |

---

## 8. Fast path (if we must ship something in 12 weeks)

Cut the plan to Stages **0 → 1 → 2 → 4** only, without the venue:

| Stage | Weeks | Deliverable |
| :--- | ---: | :--- |
| S0 | 1–6 | Decisions |
| S1 | 1–5 | Revenue truth proven |
| S2 | 1–5 | Catalog + delivery |
| S4 | 7–10 | Splits + notarisation + artist payouts |
| S6-lite | 11–12 | Fiat payout via one rail |

**Result at W12:** artists onboard, releases delivered, statements reconciled,
royalties split and paid — **no instruments, no venue, no securities exposure.**
This is the "collect the money first" path and it carries the lowest legal risk.

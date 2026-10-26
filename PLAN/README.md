# BOE Platform — Delivery Plan (Stages, Passes, Gates, Task Board)

This folder is the executable plan for the BOE platform described in
`boe-research` (the research corpus). It turns the research's 10-layer
architecture into a sequenced build we can start on Monday and finish.

**Read order:** this file → `00-WORKFLOW-JOURNEY.md` →
`01-STAGES-AND-GATES.md` → `02-TASK-BOARD.md` → `03-SCHEDULE.md` →
`04-CONFIDENCE-MODEL.md` → `05-ADDENDUM-ENGINEERED-DEMAND.md`.

---

## 1. The one-line plan

> **Stage 0 decides. Stage 1 proves the money. Everything after that is
> assembly.** We do not write contracts or open a venue until Stage 0 closes.

## 2. What we start with (Week 1)

| # | Action | Owner | Output by end of Week 1 |
| ---: | :--- | :--- | :--- |
| 1 | Instruct counsel: instrument classification + venue licence question | PM + LEG | Engagement letter signed, scoping call booked |
| 2 | Request **one real distributor statement** (any format) | OPS | A sample file in hand |
| 3 | Stand up the repo as the build home; tag `v0.1.0-plan` | TL | Branch protection + CI skeleton |
| 4 | Run the pricing model for the chosen markets | OPS + TL | Spreadsheet: weighted ARPU vs the $3.00 bar |
| 5 | Spike: parse the sample statement end-to-end | DATA | A per-work net revenue figure |

**Do not start Stage 3 (contracts) or Stage 5 (venue) before Stage 0 closes.**
Everything else may proceed at risk in parallel.

## 3. How a stage works — the three-pass rhythm

Every stage is executed in three passes, then a gate. A stage is not "done"
until its gate passes.

| Pass | Name | Question it answers | Exit artifact |
| :--- | :--- | :--- | :--- |
| **P1** | **Build** | Does it exist and run? | Working code / draft document |
| **P2** | **Verify** | Does it hold against *real* data and adversarial cases? | Test evidence, reconciliation report |
| **P3** | **Gate** | Are we allowed to proceed? | Signed gate note + decision |

A failed P3 does **not** mean rework — it means the stage stays open and the
next stage does not start. Gates are cheap; a wrong token classification is not.

## 4. Definition of Done (applies to every task)

A task is Done only when **all** of these are true:

1. Code merged to `release` with review.
2. Tests pass in CI (real code paths — no mocks where real data exists).
3. Evidence recorded (log, screenshot, reconciliation figure, or signed doc).
4. Docs updated in-repo (no undocumented behaviour).
5. If it touches money, an `audit_log` row is written and reconciles.
6. If it touches a licence obligation, the licence ledger is updated.

## 5. Roles (assign people; the plan assumes ~8 people)

| Code | Role | Count |
| :--- | :--- | ---: |
| PM | Delivery lead / product | 1 |
| TL | Tech lead / architect | 1 |
| BE | Backend engineer | 2 |
| FE | Frontend engineer | 1 |
| SC | Smart-contract engineer | 1 |
| DATA | Data / DDEX / DSR engineer | 1 |
| DO | DevOps / platform | 1 |
| QA | QA / test engineer | 0.5 |
| OPS | Finance, ops, compliance | 1 |
| LEG | External counsel (not FTE) | — |
| AUD | External auditor (not FTE) | — |

## 6. Status legend (task board)

`TODO` · `READY` · `WIP` · `VERIFY` · `GATE` · `DONE` · `BLOCKED` · `CUT`

## 7. File index

| File | What it is |
| :--- | :--- |
| `00-WORKFLOW-JOURNEY.md` | The end-to-end journey a track takes, with swimlanes |
| `01-STAGES-AND-GATES.md` | 10 stages, each with passes, gate criteria, evidence |
| `02-TASK-BOARD.md` | The Kanban board: every task, owner, estimate, dependency |
| `03-SCHEDULE.md` | Timeline, critical path, milestones, total duration |
| `04-CONFIDENCE-MODEL.md` | The arithmetic and the confidence rate, before and after |
| `05-ADDENDUM-ENGINEERED-DEMAND.md` | Engineered demand, scarcity pricing, network effects — and the exact threshold |
| `task-board.csv` | Machine-readable board (import into any tracker) |
| `confidence_model.py` | Runnable: reproduces `04` |
| `engineered_demand.py` | Runnable: reproduces `05` |

## 8. Standing rules

- **Do not push to a remote** unless explicitly instructed. Work stays local.
- **No self-minted ISRC/ISWC/ISNI.** Internal IDs only.
- **Every dollar traces to a listener or an advertiser.** No exceptions.
- **Revenue truth comes from statements, never an oracle.**
- **MIT/Apache-2.0 in product code.** Copyleft runs behind a service boundary.

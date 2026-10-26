# Workflow Journey — A Track From Upload to Trade

This is the end-to-end journey. Every stage in `01-STAGES-AND-GATES.md` exists to
move a track (and its money) one or more steps along this path.

**The rule that governs the whole journey:** every dollar on this path traces to
a **listener** or an **advertiser**. If a step's money traces to a *new buyer*
instead, the step is a loop and must be cut.

---

## 1. The journey in one picture

```
 ACT 1        ACT 2         ACT 3          ACT 4           ACT 5        ACT 6
 ASSERT       CATALOGUE     LISTEN         PROVE           SPLIT        MINT
 & IDENTITY   & DELIVER     & RECORD       & NOTARISE       & PAY        & TRADE
 ──────────   ──────────    ──────────     ───────────     ────────     ───────
 artist       upload        own app        DSR ingest      splitter     I1 mint
 │            │             │              │               │            │
 ├─ KYC       ├─ fingerprint├─ play event  ├─ normalise    ├─ basis-pts ├─ ERC-3643
 ├─ credent.  ├─ metadata   ├─ 30s rule    ├─ match        ├─ accrual   ├─ registry
 ├─ copyright ├─ internal ID├─ dedup       ├─ suspense     ├─ payout    ├─ venue
 │  assertion │             ├─ bot filter  ├─ hash         │            │
 │            ├─ ERN → DSP  │              ├─ notarise ──► │            ├─ I2 mint
 │            │             │              │               │            │
 ▼            ▼             ▼              ▼               ▼            ▼
 artist       release       engagement     proven          artist       investor
 credential   delivered     recorded       revenue         paid         holds claim
```

---

## 2. The journey step by step

| # | Step | Actor | System | Input | Output | Evidence | Stage |
| ---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| J1 | Register | Artist | Web app | Identity + KYC | Artist profile | KYC ref | 2 |
| J2 | Assert rights | Artist | Registry | Copyright retention assertion | Signed VC | VC hash | 2 |
| J3 | Upload master | Artist | Storage | Audio file | Master + hash | Fingerprint | 2 |
| J4 | Deduplicate | System | Fingerprint svc | Fingerprint | New / duplicate | Chromaprint ref | 2 |
| J5 | Enter metadata | Artist | Catalog | Title, artists, splits | Release + internal ID | Catalog row | 2 |
| J6 | Declare splits | Artist | Catalog | Contributors + bps | Split config | Sums to 10,000 | 4 |
| J7 | Deliver | System | stardust-distro | Release | ERN 4.3 → DSP | Delivery receipt | 2 |
| J8 | Receive | System | stardust-dsp | ERN | Ingested release | Ingest log | 2 |
| J9 | Stream | Listener | Own app | Play | Audio + play event | Play event | 3 |
| J10 | Validate play | System | Engagement | Play event | Valid/invalid play | ≥30s rule | 3 |
| J11 | Aggregate | System | Engagement | Valid plays | Per-track metrics | Stability index | 3 |
| J12 | Generate DSR | System | stardust-dsp | Aggregates | DSR file | DSR hash | 3 |
| J13 | Ingest statement | System | DSR pipeline | DSR/CSV/PDF | Statement row | File hash | 1 |
| J14 | Normalise | System | DSR pipeline | Statement | Revenue facts | Recon figure | 1 |
| J15 | Match | System | Matcher | Facts ↔ tracks | matched/ambiguous/unmatched | Match method | 1 |
| J16 | Review | OPS | Review queue | Ambiguous | Resolution | Reviewer ID | 1 |
| J17 | Notarise | System | Bridge | Statement hash | On-chain attestation | Tx hash | 4 |
| J18 | Fund splitter | System | Royalty engine | Net revenue | Funded splitter | Tx hash | 4 |
| J19 | Accrue | System | RevenueVault | Split | Holder credits | Ledger row | 4 |
| J20 | Claim | Artist/Holder | Payout svc | Credit | Fiat/crypto payout | Bank/tx ref | 6 |
| J21 | Mint I1 | System | ERC-3643 | Catalogue | Restricted token | Contract addr | 3 |
| J22 | Mint I2 | System | Zora | Artist | Fan token | Contract addr | 3 |
| J23 | Trade I1 | Investor | Venue (V4 hook) | Eligible buyer | Fill | Trade tx | 5 |
| J24 | Trade I2 | Fan | Zora pool | Buyer | Fill | Trade tx | 5 |
| J25 | Licence data | System | Data product | Engagement record | Buyer contract | Invoice | 7 |

---

## 3. Swimlanes — who does what, where

| Lane | Responsibility | Stages |
| :--- | :--- | :--- |
| **Artist lane** | Register, assert, upload, declare, claim | 2, 4, 6 |
| **Listener lane** | Stream, engage | 3 |
| **Revenue lane** | Statements in, match, prove, notarise | 1, 4 |
| **Money lane** | Split, accrue, payout | 4, 6 |
| **Instrument lane** | Mint, register eligibility, trade | 3, 5 |
| **Data lane** | Aggregate, licence, report | 3, 7 |
| **Governance lane** | Legal, licence, audit, conflict controls | 0, 8 |
| **Agent lane** | Delegate, propose, confirm | 7 |

---

## 4. The three happy paths

**Artist path (cash in hand):**
`Register → Assert → Upload → Deliver → Stream → Statement → Match → Notarise →
Split → Claim → Paid`

**Investor path (instrument):**
`KYC → Eligibility credential → Registry → Buy I1 (hook checks registry) →
Receive pro-rata distributions → Sell to another eligible holder`

**Data-buyer path (the revenue add):**
`Engagement recorded → Aggregated → Audited → Licensed to buyer → Invoice paid`

> Note the third path is the only one that adds **cash** rather than a claim.
> It is the lever that closes the ARPU gap (see `04-CONFIDENCE-MODEL.md`).

---

## 5. Failure and exception journeys (must be designed, not discovered)

| Exception | Trigger | Handling | Stage |
| :--- | :--- | :--- | :--- |
| Unmatched statement line | No ISRC/fingerprint match | Suspend in suspense ledger; **never distribute** | 1 |
| Ambiguous match | Fuzzy confidence below bar | Human review queue | 1 |
| Duplicate upload | Fingerprint collision | Reject / link to existing | 2 |
| Play below 30s | Short play | Not counted | 3 |
| Bot / wash play | Anomaly | Excluded; flagged | 3 |
| Ineligible I1 buyer | Registry check fails | Swap reverts | 5 |
| Agent over spend limit | Contract bound exceeded | Contract refuses | 7 |
| Disputed statement | Artist challenges figure | Hold distribution; audit trail | 4 |
| FX / withholding | Cross-border payout | Applied per treaty table | 6 |

---

## 6. Governance threads that run across the whole journey

1. **Copyright retention** — the distribution terms contain no IP assignment (Stage 0/2).
2. **Conflict of interest** — the platform runs the player *and* the venue; it must
   never trade its own book, and the engagement methodology must be auditable (Stage 5/8).
3. **Licence boundary** — GPL/AGPL components run as services, never linked (all stages).
4. **Audit trail** — append-only `audit_log`; no money moves without a row (all stages).

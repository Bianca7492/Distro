# Stages, Passes and Gates

Ten stages. Each has an objective, entry criteria, the three passes (P1 Build,
P2 Verify, P3 Gate), the **gate criteria**, and the evidence required to pass.

A gate is a **stop**. If it fails, the next stage does not start.

---

## Stage map

```
S0 DECISIONS ──► S1 REVENUE TRUTH ──► S2 IDENTITY & CATALOG ──► S3 MINTING
   (no code)        (prove the money)      (assert + deliver)        (I1/I2)
      │                                                                │
      │  gates S3 and S5 only                                          ▼
      │                                            S4 ROYALTY & BRIDGE (split, notarise)
      │                                                                │
      │                                                                ▼
      └───────────────────────────────────────► S5 TRADING (permissioned venue)
                                                                       │
                                                                       ▼
                                                        S6 SETTLEMENT & PAYOUTS
                                                                       │
                                                                       ▼
                                                          S7 AGENT-NATIVE + DATA LICENCE
                                                                       │
                                                                       ▼
                                                          S8 HARDENING & AUDIT
                                                                       │
                                                                       ▼
                                                          S9 LAUNCH & OPERATE
```

---

## Stage 0 — Decisions and Legal (NO CODE)

**Objective:** answer the questions that determine the product's legal shape.

**Entry:** none. This is the first thing we do.

| Pass | Work |
| :--- | :--- |
| P1 Build | Draft the decision memo; assemble the question list |
| P2 Verify | Counsel review; pricing model run on real market ARPUs |
| P3 Gate | Signed decisions recorded |

**Gate 0 criteria (all required):**
1. Instrument classification confirmed: **I1 = security/financial instrument** (ERC-3643), **I2 = collectible, no revenue**.
2. Jurisdiction + licence pathway named (venue licence or exemption).
3. Distribution terms confirmed to contain **no IP assignment**.
4. Weighted ARPU for chosen markets computed (target ≥ $3.00).
5. At least one distributor agreed to supply statements.

**Evidence:** counsel memo, pricing spreadsheet, distributor email, decision log.
**Duration:** 6 weeks (runs in parallel with S1/S2). **Blocks:** S3, S5.

---

## Stage 1 — Revenue Truth (DSR ingestion)

**Objective:** given a real statement, produce a per-work net revenue figure that
matches the distributor's totals **to the cent**.

**Entry:** one real statement sample.

| Pass | Work |
| :--- | :--- |
| P1 Build | Parser + normaliser; statement/revenue_facts tables; file hashing |
| P2 Verify | Reconcile against the distributor's own totals; adversarial cases |
| P3 Gate | Reconciliation signed off |

**Gate 1 criteria:**
1. Real statement ingested end-to-end.
2. **Reconciliation matches to the cent.**
3. Every line is `matched`, `ambiguous`, or `unmatched` — none silently dropped.
4. Unmatched revenue held in suspense (never distributed).
5. Statement hash recorded and reproducible.

**Evidence:** reconciliation report, hash manifest, suspense ledger export.
**Duration:** 4 weeks. **Blocks:** S4.

---

## Stage 2 — Identity and Catalog

**Objective:** an artist registers, receives a credential, uploads, and a release
is delivered to a test DSP.

**Entry:** S0 decision memo (for the copyright-assertion wording).

| Pass | Work |
| :--- | :--- |
| P1 Build | Supabase schema; upload; fingerprint service; internal IDs; wire stardust-distro/dsp |
| P2 Verify | Duplicate detection; ERN delivered and ingested back |
| P3 Gate | 100 tracks ingested, IDs stable, loop closed |

**Gate 2 criteria:**
1. 100 tracks ingested; duplicate detection works.
2. Internal IDs stable and unique; **no self-minted ISRCs**.
3. An ERN is delivered to `stardust-dsp` and ingested back.
4. Artist credential records the copyright-retention assertion.
5. RLS on every table; artist sees only their own data.

**Evidence:** ingest log, ERN + receipt, RLS test output.
**Duration:** 6 weeks. **Blocks:** S3, S4.

---

## Stage 3 — Minting (I1 and I2)

**Objective:** deploy both instruments on testnet; a transfer of I1 to an
unverified wallet **reverts**.

**Entry:** **Gate 0 passed** + Gate 2 passed.

| Pass | Work |
| :--- | :--- |
| P1 Build | ERC-3643 registry stack (I1); Zora coin (I2); eligibility service |
| P2 Verify | Transfer-restriction tests; identity registry; compliance modules |
| P3 Gate | Restriction proof on testnet |

**Gate 3 criteria:**
1. I1 deployed with full registry stack (identity, trusted issuers, claim topics, modular compliance).
2. **A transfer to an ineligible wallet reverts.**
3. I2 deployed with **no revenue attached**.
4. One catalogue (or track) minted as I1; supply = fraction sold.
5. Eligibility rows exist for test holders.

**Evidence:** contract addresses, revert tx hash, test suite output.
**Duration:** 6 weeks. **Blocks:** S4, S5.

---

## Stage 4 — Royalty Engine and Bridge

**Objective:** ingest a statement → notarise the hash → fund the splitter →
holders and artists receive correct pro-rata amounts.

**Entry:** Gate 1 + Gate 2 + Gate 3.

| Pass | Work |
| :--- | :--- |
| P1 Build | Splitter (bps); RevenueVault (pull payments); `reportRevenue(..., statementRef)`; notarisation |
| P2 Verify | Reconciliation from statement to credits; `statementRef` binding check |
| P3 Gate | End-to-end distribution reconciles |

**Gate 4 criteria:**
1. Splitter sums to 10,000 bps; rejects zero/duplicate payees.
2. Recursive splits capped and declared.
3. **Pull payments only** (no unbounded push).
4. Every on-chain euro bound to a `statementRef`.
5. 20 holders receive correct pro-rata credits, verifiable against the statement.

**Evidence:** distribution tx hashes, reconciliation table, monitor output.
**Duration:** 6 weeks. **Blocks:** S5, S6.

---

## Stage 5 — Trading (permissioned venue)

**Objective:** I1 trades in a permissioned pool; a non-eligible wallet
**cannot buy**; I2 trades openly.

**Entry:** Gate 0 (venue licence pathway) + Gate 3.

| Pass | Work |
| :--- | :--- |
| P1 Build | Uniswap V4 hook calling `IdentityRegistry`; Doppler curves; USDC settlement |
| P2 Verify | Ineligible-buyer revert; caps, cooldown, circuit breaker |
| P3 Gate | Permissioned market proof + conflict-of-interest controls published |

**Gate 5 criteria:**
1. V4 `beforeSwap` calls `IdentityRegistry.isVerified(sender)`.
2. A non-eligible wallet's buy **reverts**; an eligible one succeeds.
3. Caps, cooldown, and circuit breaker active.
4. I2 trades openly with no revenue attached.
5. **Platform does not trade its own book**; methodology published.

**Evidence:** trade txs (allowed + reverted), guardrail test output, published methodology.
**Duration:** 8 weeks. **Blocks:** S6.

---

## Stage 6 — Settlement and Payouts

**Objective:** a real (test-mode) fiat payout reaches a test artist account with a
reconciled statement.

**Entry:** Gate 4.

| Pass | Work |
| :--- | :--- |
| P1 Build | Hyperswitch payout rails; Sablier accrual; FX + withholding tables |
| P2 Verify | Payout reconciles to bank statement; multi-rail failover |
| P3 Gate | Reconciled payout + rail redundancy proof |

**Gate 6 criteria:**
1. Test-mode fiat payout reaches a test account.
2. Ledger and bank statement agree.
3. **Multi-rail**: no single point of failure (BTCPay + Hyperswitch).
4. FX and withholding applied per treaty table.
5. Payout minimums and reserves honoured.

**Evidence:** payout receipt, bank reconciliation, rail failover test.
**Duration:** 6 weeks. **Blocks:** S7, S9.

---

## Stage 7 — Agent-Native and Data Licence

**Objective:** an agent delegated by an artist queries royalties and initiates a
payout that **requires confirmation and respects a contract-enforced spend limit**.

**Entry:** Gate 6.

| Pass | Work |
| :--- | :--- |
| P1 Build | MCP tools; x402 endpoints; delegation credentials; data-licence product |
| P2 Verify | Spend-limit enforcement; confirm-token expiry; MCP auth |
| P3 Gate | Agent cannot exceed bounds; data product sells |

**Gate 7 criteria:**
1. Agent queries royalties via MCP tool.
2. Agent payout **refused** when over the per-payment cap or to a non-approved address.
3. Preview-then-confirm with short-lived token (no funds move without confirm).
4. **Every MCP endpoint authenticated** (no unauthenticated SSE).
5. Engagement data product defined, audited, and sold to a real buyer (pilot).

**Evidence:** blocked-payment test, confirm-expiry test, auth test, pilot invoice.
**Duration:** 4 weeks. **Blocks:** S8.

---

## Stage 8 — Hardening and Audit

**Objective:** external audit passed; no critical findings open.

**Entry:** Gate 7.

| Pass | Work |
| :--- | :--- |
| P1 Build | Monitoring, incident runbook, licence-boundary audit, load test |
| P2 Verify | Pen test; contract audit; dependency licence scan |
| P3 Gate | Audit sign-off |

**Gate 8 criteria:**
1. External contract audit passed; no criticals open.
2. Penetration test passed; MCP auth verified.
3. **Licence boundary audited**: no GPL/AGPL linked into product paths.
4. Load test at target MAU passes.
5. Incident runbook exercised (game day).

**Evidence:** audit report, pen-test report, licence scan, load-test result, game-day notes.
**Duration:** 10 weeks. **Blocks:** S9.

---

## Stage 9 — Launch and Operate

**Objective:** first real artist onboarded, first real payout, first real trade,
first real data licence.

**Entry:** Gate 8.

| Pass | Work |
| :--- | :--- |
| P1 Build | Production deploy; support; on-call |
| P2 Verify | First real cycle end-to-end with a real artist |
| P3 Gate | Operate review at 30/60/90 days |

**Gate 9 criteria:**
1. First real artist onboarded and paid.
2. First real I1 trade by an eligible holder.
3. First real data-licence invoice paid.
4. Reconciliation holds for 3 consecutive months.
5. Gate-factor tracker updated (regulatory / pool / audit).

**Evidence:** payout receipt, trade tx, invoice, 3 monthly reconciliations.
**Duration:** 4 weeks to launch, then ongoing.

---

## Gate dependency summary

| Stage | Blocked by | Blocks |
| :--- | :--- | :--- |
| S0 | — | S3, S5 |
| S1 | statement sample | S4 |
| S2 | S0 (wording) | S3, S4 |
| S3 | S0, S2 | S4, S5 |
| S4 | S1, S2, S3 | S5, S6 |
| S5 | S0, S3 | S6 |
| S6 | S4 | S7, S9 |
| S7 | S6 | S8 |
| S8 | S7 | S9 |
| S9 | S8 | — |

# Addendum A — Engineered Demand, Scarcity and the Network Effect

This responds to a specific argument:

> *"ARPU can increase with increased demand value, so having multiple
> instruments is still profitable if the increased demand limits supply, thereby
> making the value increase."*

**The mechanism is real.** Below is where it works, where it does not, and the
exact threshold. Reproduce with `python3 engineered_demand.py`.

---

## 1. The scarcity mechanism — confirmed

Price under rationing is `P = D / S`. With supply capped per instrument:

| N instruments | D(N), γ=0.5 | price γ=0.5 | γ=1.0 | γ=2.0 |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 100,000 | 100 | 100 | 100 |
| 5 | 223,607 | 224 | 500 | 2,500 |
| 20 | 447,214 | 447 | 2,000 | 40,000 |

**Price rises iff demand elasticity γ > 1.** You are correct that capping supply
raises price, and correct that more instruments can raise aggregate demand.

---

## 2. But price is capital, not revenue

This is the crux, and it is why the mechanism does not fund the pool by itself.

```
Pool obligation   = MAU × streams/day × 30 × rate    (monthly, recurring)
Instrument price  = D / S                            (one-time, at sale)
```

| N | Total premium | Funds the monthly pool for |
| ---: | ---: | ---: |
| 5 | $5M | 1.19 months |
| 20 | $50M | 11.9 months |
| 100 | $500M | 119 months |

**A one-time premium is not a recurring payer.** It cannot fund a monthly
royalty pool at any N, because it happens once and the obligation repeats.

And if the instrument pays revenue, the premium is paid by a **new buyer** — the
reflexive loop. That is the Royal.io failure mode, not a funding source.

---

## 3. Engineered demand — the legitimate version

"Engineer demand for a valueless product" is just **customer acquisition**. It is
legal and standard. The only question that matters is:

> **Does it raise PER-USER ARPU?**

Two channels, and they behave completely differently:

| Channel | Formula | Effect of more MAU |
| :--- | :--- | :--- |
| Linear (subscription, ads) | `ARPU ∝ M⁰` | **No effect** on per-user value |
| Network effect (data product) | `value/user ∝ M^δ` | **Raises** per-user value |

### The network-effect test

Base data value $0.20/user; `d = d0 × (M/1M)^δ`:

| MAU | δ=0 | δ=0.1 | δ=0.2 | δ=0.3 |
| ---: | ---: | ---: | ---: | ---: |
| 500,000 | 0.200 | 0.187 | 0.174 | 0.162 |
| 2,000,000 | 0.200 | 0.214 | 0.230 | 0.246 |
| 20,000,000 | 0.200 | 0.270 | 0.364 | 0.491 |

**At δ=0, MAU does nothing for ARPU.** Engineered MAU only helps if the
value-added service has a genuine network effect (δ > 0) — a bigger, better
dataset is worth more *per user*.

---

## 4. Does it clear the $3.00 bar?

Composite ARPU = subscription + ads + data + venue:

| Scenario | MAU | Eff. ARPU | Clears $3? |
| :--- | ---: | ---: | :--- |
| Emerging, no network effect | 2M | 1.10 | no |
| Emerging + network effect (δ=0.3) | 2M | 1.15 | no |
| Mixed + network effect (δ=0.3) | 2M | 2.75 | no |
| Engineered MAU 20M + network (δ=0.3) | 20M | 2.99 | no (borderline) |
| Engineered MAU 20M, no network | 20M | 1.10 | no |

**Engineered MAU alone never clears the bar** — 20M users in emerging markets
still yields $1.10. It only works with a real network effect *and* high-ARPU
markets *and* large scale, together.

### The threshold

At 2M MAU in a mixed market (base $2.50/user), clearing $3.00 needs:

```
data value ≥ $0.50/user  →  δ* = 1.32
```

**δ = 1.32 is above Metcalfe's law (δ=1.0).** Realistic data products sit at
δ ≈ 0.1–0.3. At δ=0.3 you would need **~21M MAU** to clear the bar via data alone.

**So the mechanism is real but the parameters must be extreme.** It is not a
reason to mint more instruments; it is a reason to build a data product whose
value genuinely scales with the network.

---

## 5. P(success) with the mechanism engaged

| δ | MAU | Eff. ARPU | P(success) |
| ---: | ---: | ---: | ---: |
| 0.0 | 2M | 2.70 | 17.4% |
| 0.0 | 20M | 2.70 | 17.4% |
| 0.2 | 20M | 2.86 | 18.5% |
| 0.3 | 20M | 2.99 | 19.3% |
| **0.4** | **20M** | **3.16** | **34.7%** |

**Baseline 19.4%. The mechanism moves it only when δ ≥ ~0.4 at 20M MAU.** Below
that it is noise (17–19%).

---

## 6. The decisive legal split

This is the part that decides whether the mechanism is an asset or a liability:

| You engineer demand for… | Character | Verdict |
| :--- | :--- | :--- |
| **I2** (valueless collectible) | Marketing / community building | ✅ **Fine** |
| **The data product** (external buyer) | Sales | ✅ **Fine** |
| **I1** (revenue-bearing security) | **Market manipulation** | ❌ **Illegal** |

**The network effect must attach to the data product — the external payer — not
to I1.** Engineering demand for a security's price is the offence; engineering
demand for a service is the business.

---

## 7. What this changes in the plan

**Nothing in the stage sequence.** But it sharpens two tasks:

| Task | Change |
| :--- | :--- |
| `S7-07` Data-licence product definition | Now must **prove a network effect** (δ > 0), not just a data product |
| `S7-13` Data-licence pilot | Must measure **per-user value vs MAU**, to estimate δ empirically |
| `S0-07` Weighted ARPU | Now includes a **δ assumption** that must be evidenced, not assumed |
| `S5-08` Conflict controls | Extend: **no demand engineering on I1**, only on I2 and the data product |

**Net effect on the plan:** the data-licensing track (S7) is promoted from
"nice ancillary" to **the mechanism that closes the ARPU gap** — but it must
demonstrate a real network effect to do so. That is a testable claim, and the
plan now tests it.

---

## 8. Honest conclusion

| Your claim | Verdict |
| :--- | :--- |
| Capping supply raises price | ✅ **True** |
| More instruments can raise aggregate demand | ✅ **True** |
| Engineered demand raises value | ⚠️ **Only via a network effect (δ > 0)** |
| Higher price funds the royalty pool | ❌ **False** — capital, not recurring revenue |
| Therefore multiple instruments are profitable | ❌ **No** — one-time premium cannot fund a monthly pool |

**The profitable version of your intuition is:** build a **data product with a
real network effect** (δ ≥ 0.3–0.4) that external parties pay for monthly. That
is a payer. Minting more revenue-bearing instruments is not.

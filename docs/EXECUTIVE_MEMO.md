# Executive Memo — Recovery Performance and the ₹10 Cr Decision

**To:** Leadership team **Re:** "Recovery has improved 11% month-on-month"
**Period examined:** 2026-01-01 → 2026-08-08 (220 days) **Prepared by:** Data & Analytics

---

## 1. What happened

**Recovery has not improved. It has been flat all year.**

Net recovery has run at **₹0.555 Cr per day**, month after month, with a coefficient of variation
of 2.0%. There is no statistically significant trend — OLS on per-day net recovery gives a slope
of **−0.0034 Cr/day per month at p = 0.11**, and note the sign: the point estimate is negative.
Calls, contacts, promises and payments per day are equally flat.

**The reported 11% is a calendar artifact.** The only +11% anywhere in the data is the
February → March step on the gross-collections series (+11.0%). February has 28 days, March has
31. `31 ÷ 28 − 1 = +10.7%` — the calendar accounts for 10.7 of those 11.0 points. On a per-day
basis the same step is **+0.3%**. The average month-on-month change across the whole period is
**+0.3% gross, −0.1% net**.

Two further corrections move the level, though not the trend:

| | ₹ Cr |
|---|---:|
| Recovery as currently reported (SUCCESS rows) | 134.15 |
| less duplicate payment rows | −2.59 |
| less reversals counted as collections | −9.47 |
| **Actual net recovery** | **122.09** |

Reported recovery is overstated by **₹12.06 Cr (9.9%)**.

The brief also describes twelve months of data. The event tables contain **seven months and eight
days**, and August is partial. Any twelve-month trend in current reporting is either padded or is
comparing an 8-day month against a 31-day one.

**Which metrics are genuinely improving: none. Which are misleading: most of them.**

- *Contact rate* — flat at 19.4–20.5%, and its denominator is unstable (called vs targeted vs
  portfolio gives three different answers).
- *PTP rate* — undercounted by **50%**, because `PTP` and `PROMISE_TO_PAY` are the same event under
  two live taxonomies and dashboards match only one.
- *PTP kept rate* — the version dividing by *all* promises falls every month purely because recent
  months hold more unresolved promises. A manufactured downtrend, the mirror image of the 11%.
- *Channel conversion* — an artifact of the attribution window. Only **3% of payments** have any
  interaction within 24 hours; 97% of recovered money cannot be credited to a channel at all.
- *Cost per ₹ recovered* — **not computable**. There is no cost data in any of the 17 tables.

---

## 2. Why it happened

**It did not happen.** Nothing changed, and we tested every mechanism that could make a flat series
look otherwise:

| Tested | Finding |
|---|---|
| Portfolio mix (risk, DPD, loan type) | Stable within ~1pp; ~100% of the tiny change is within-segment, ~0% mix |
| Cohort effects | Flat once censored to a common 30-day window; uncensored decline is pure exposure |
| Simpson's paradox | No segment moves against the total — nothing to reverse |
| Selection / survivorship | The apparent 6%→58% rise in wasted targeting is history accumulation, not decay |
| Attribution window | Channel shares barely move across a 365× window change |
| Agent, tenure, vendor, time-of-day, attempt number | All null; agent dispersion is 1.07× binomial chance |

There is one genuine, large, actionable finding:

> **The targeting engine carries no information.** 43.1% of targeting aims at accounts last
> recorded CLOSED, WRITEOFF or PAID — against a **42.9% random-selection baseline**. Priority
> score, recommended channel, risk segment and DPD band are all statistically independent of who
> pays. Targeting today is indistinguishable from drawing names from a hat.

And one compliance exposure nobody is reporting:

> **45.9% of calls fall outside the 08:00–21:00 IST window** of the RBI Fair Practices Code. This
> is invisible in current reporting because current reporting never converts the timestamps —
> `event_at` is a naive local string and the timezone sits in a separate column with three values.

---

## 3. How confident we are

| Claim | Confidence | Basis |
|---|---|---|
| Recovery is flat; the 11% is an artifact | **High** | Five independent definitions, all null; mechanism identified exactly |
| Current targeting carries no signal | **High** | 43.1% vs 42.9% baseline, stable across all eight months |
| No operational lever works | **Medium** | Null results on 30k accounts; effects below ~13% would be invisible |
| Every cost and ROI figure | **Low** | The dataset contains no cost data whatsoever |

One number deserves emphasis. The **minimum detectable effect** of this dataset is **12.9%**. Even
if the 11% improvement had been real, *this data could not have seen it.* The claim sits below the
resolution of the instrument used to make it.

---

## 4. What we should do

**Do not deploy ₹10 Cr against any of the six options on this evidence base.**

Scoring them against evidence rather than plausibility:

| Option | Verdict | Why |
|---|---|---|
| 1. Better telephony | **No** | Vendor contact-rate spread is 1.2pp after resolving 15 IDs to 5 real vendors. No better vendor exists to buy. |
| 2. More agents | **No** | Agent dispersion is 1.07× chance. Contact intensity vs recovery: ρ = +0.011. |
| 3. AI voice | **Cannot assess** | Zero agentic-voice or IVR rows exist, despite the brief naming both. No baseline. |
| 4. Better targeting | **Best candidate** | The only option where the data proves a gap rather than failing to disprove one. |
| 5. WhatsApp / digital | **No** | "Conversion" moves with the window, not performance. 26.5 complaints per 1,000 events. |
| 6. Field operations | **No** | 65.9 complaints per 1,000 events (3.8× voice), lowest PTP-kept rate, most expensive per touch. |

**Recommended: a staged commitment to Option 4.**

**Stage 1 — ₹1.2 Cr, months 0–6.** Make the decision answerable.

| | ₹ Cr |
|---|---:|
| Cost instrumentation (per call, message, visit, agent-hour) | 0.20 |
| Golden-layer data platform, productionised with tests | 0.45 |
| Account-state service, so CLOSED means closed | 0.25 |
| Randomised holdout harness and pre-registration process | 0.20 |
| Compliance monitor (calling window, complaint rate) | 0.10 |

**Stage 2 — ₹8.8 Cr, months 6–24.** Released **only** if a pre-registered randomised test clears
a ≥10% lift in net recovery per assigned account over 120 days. 8,500 accounts per arm. If it does
not clear, the ₹8.8 Cr returns to the balance sheet.

Why not simply back Option 4 with the full ₹10 Cr: we can prove the current engine carries no
signal; we **cannot** prove a better one would recover more, because recovery is uncorrelated with
every lever we can measure. Those are different claims and only the first is supported. Funding the
second would repeat the exact reasoning error that produced the 11% headline.

---

## 5. Expected financial impact

**The scale check first.** The modelled annual cost of running this entire collections operation is
**₹5.8 Cr** (assumption-driven; range ₹3.6–9.9 Cr). ₹10 Cr is **1.7× that**. This is not an
increment to the operation — it is a decision to rebuild it at two to three times its cost base,
against an outcome that has not moved in seven months.

Against ₹202.6 Cr of annualised net recovery, assuming the business **owns** the book:

| Scenario | Lift | Incremental recovery | Cost | Net | Year-1 ROI |
|---|---:|---:|---:|---:|---:|
| Downside (what every measured lever predicts) | 0% | ₹0 Cr | ₹10.0 Cr | −₹10.0 Cr | −100% |
| Base | 6% | ₹12.2 Cr | ₹10.0 Cr | +₹2.2 Cr | +22% |
| Upside | 12% | ₹24.3 Cr | ₹10.0 Cr | +₹14.3 Cr | +143% |

- **Break-even, full ₹10 Cr:** +4.9% lift.
- **Break-even, Stage 1 only:** **+0.6% lift** — this is the bet actually being placed.
- **Expected incremental recovery:** not point-estimable from this data. Defensible range **0–13%**,
  with the upper bound set by the 12.9% detection floor. Most likely **0–6%**.

**Two assumptions that could invalidate this.**

1. **The revenue model.** If this book is collected for a fee rather than owned, break-even on
   ₹10 Cr rises to a **+25% lift** (20% fee) or **+41%** (12% fee). Nothing in this data suggests a
   lever of that size exists, and the answer becomes an unambiguous no on all six options.
   **This is question one for the CFO.**
2. **The cost model.** Implied cost-to-collect is 2.9% against an industry norm of 5–15%. Either
   the activity tables are a sample of a larger operation, or the payments table is inflated
   relative to real activity. Either way the cost model is not decision-grade until real cost feeds
   exist — which is itself the argument for funding instrumentation before capacity.

**Downside comparison.** If the lift is zero: full deployment loses ₹10 Cr and leaves the operation
running at 2–3× its cost base with the same output. The staged approach spends ₹1.2 Cr, preserves
₹8.8 Cr, and ends the year knowing the causal answer instead of arguing about it. **That option to
not spend is worth more than any of the six options is worth on current evidence.**

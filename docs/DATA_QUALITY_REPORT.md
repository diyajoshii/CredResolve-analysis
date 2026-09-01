# Data Quality Report

**Dataset:** collections_30k, 17 tables, 639,346 rows
**Window:** 2026-01-01 → 2026-08-08 (220 days; **not** the 12 months the brief describes)
**Prepared:** as part of the recovery-performance investigation

---

## Summary

| | |
|---|---|
| Raw rows across the 8 primary tables | 305,450 |
| Golden rows | 254,963 |
| Removed by cleaning | 50,487 (16.5%) |
| Reported-style recovery (SUCCESS rows, no dedup) | ₹134.15 Cr |
| Golden net recovery (SUCCESS − REVERSED, deduped) | **₹122.09 Cr** |
| Overstatement corrected | ₹12.06 Cr (**+9.9%**) |

Fourteen distinct issues were found. Four of them independently invalidate a metric that
currently appears in leadership reporting. Two of them are the mechanism behind the reported
"+11% month-on-month".

Issues are ordered by **business impact**, not by row count.

---

## Issue register

### 1. The month-on-month series is not exposure-normalised — BLOCKER

**Detection.** Reproduced the reported figure by building a ladder of recovery definitions from
the most naive query to the most defensible. Found exactly one +11% in the dataset: February →
March 2026 on the gross-SUCCESS series (+11.0%). February has 28 days, March has 31.
`31/28 − 1 = +10.7%`.

**Impact.** The entire headline. On a per-day basis Feb → Mar is **+0.3%**. Mean month-on-month
across the seven complete months is **+0.3% gross / −0.1% net**, and an OLS trend on per-day net
recovery returns slope −0.0034 Cr/day per month at **p = 0.11** — no significant trend, and the
point estimate is *negative*.

**Treatment.** All monthly metrics carry `calendar_days` and a `_per_day` variant. A blocking
data-quality assertion fires when headline MoM and per-day MoM diverge by more than 5pp.

**Residual risk.** None for recovery. The same normalisation must be applied to every other
volume metric before it is published.

---

### 2. A partial month sits at the end of the series — BLOCKER

**Detection.** All event tables stop on 2026-08-08. August contains 8 days.

**Impact.** Including it produces a −74.8% "collapse", or, if the analyst notices and instead
compares like-for-like against a full month, a spurious recovery next month. Either direction is
a fabricated story.

**Treatment.** `is_complete_month` flag on the month spine; a blocking assertion prevents a
partial month from entering any MoM view.

---

### 3. Duplicate payment rows inflate recovery by ~10% — HIGH

**Detection.** 486 byte-identical rows plus 14 reused `payment_id` values, out of 25,500.

**Impact.** ₹2.59 Cr of phantom recovery before any other correction.

**Treatment.** Four-rule dedup ladder: identical rows → `payment_id` → `(account, amount,
reference)` → `(account, amount)` within 24h.

**The trap.** `payment_reference` looks like the natural dedup key. It is not. **3,405 references
are reused, and every single one of them spans different accounts and different amounts** — an
ID-space collision, not a duplicate transaction. Deduping on reference alone removes **3,809 rows
instead of 500**, destroying **2,617 genuine successful payments worth ₹19.8 Cr**. The obvious
cleaning rule does more damage than the dirt.

After the first two rules there are **zero** remaining `(account, amount, reference)` repeats and
zero same-account same-amount repeats inside 24 hours. The entire payment-duplication problem in
this dataset is byte-identical re-ingest — not gateway retries, and not reference reuse. Rules 3
and 4 stay in the pipeline because they are the ones that will fire when a real gateway retry
happens; here they correctly remove nothing.

---

### 4. Reversals are not netted out — HIGH

**Detection.** `payment_status` has four values. 1,284 rows totalling ₹9.47 Cr are `REVERSED`;
273 reversal references also appear as `SUCCESS`.

**Impact.** ₹9.47 Cr, or 7.8% of net recovery. Money that came back out is being counted as money
collected.

**Treatment.** `net_recovery = cash_in − cash_out`. `PENDING` and `FAILED` are not money and are
excluded from every recovery metric.

---

### 5. Agent identity is unresolvable — HIGH

**Detection.** 30,000 rows, 1,000 `agent_id`s, 1,099 `employee_code`s. Every `agent_id` carries
~9.5 different `agent_name`s, and disagrees with itself on team, status and vendor. A graph of
`agent_id ↔ employee_code` is a **single connected component of 2,099 nodes**.

**Impact.** Every agent-level, agent-tenure and team metric in existing reporting is unsupported.

**The trap.** Resolving on `employee_code` — the obvious natural key — raises mean calls per
person from 88 to 137, a **+56% productivity improvement created purely by an entity-resolution
choice**. This is the same class of error as the 11%, and it is more seductive because the fix
looks like diligence.

**Treatment.** `dim_agent` is collapsed SCD-1 on `agent_id` for join-safety only and marked
`trust_level = 'LOW'`. No agent-level metric is published.

---

### 6. Three timezones in one naive timestamp column — HIGH

**Detection.** `event_at` is timezone-naive; the zone lives in a separate `timezone` column with
three values (UTC 34%, Asia/Kolkata 34%, Asia/Dubai 34%). `accounts`, `agent_sessions` and
`vendor_telephony` carry their own, different, timezone columns.

**Impact.** 9.8% of calls change **calendar day** after normalisation; 305 change **month**.
Every daily volume, every intraday pattern, and every "best time to call" analysis on raw
timestamps is wrong.

**Compliance impact.** **45.9% of calls fall outside the 08:00–21:00 IST window** of the RBI Fair
Practices Code. No current report surfaces this, because no current report converts the timestamps.

**Treatment.** `event_ist` is the single reporting clock; raw `event_at` retained for audit.
A blocking assertion fires on any unrecognised timezone label.

---

### 7. Disposition taxonomy runs three versions in parallel — HIGH

**Detection.** `disposition_code` contains both `PTP` and `PROMISE_TO_PAY` for the same business
outcome. `disposition_version` is a flat 33/33/33 split of legacy/v1/v2 **in every one of the
eight months** — this is not a migration that completed, it is three taxonomies running
simultaneously.

**Impact.** A dashboard filtering `disposition_code = 'PTP'` reports an 11.2% PTP rate. The true
harmonised rate is 22.4%. **Promises are undercounted by 50%.**

**Treatment.** `disposition_std` maps raw codes to eight business outcomes at the staging layer,
with a blocking assertion on any unmapped code.

---

### 8. `account_status_history` is not a state machine — HIGH

**Detection.** Tested before use: **19,673 status changes occur after an account's first
CLOSED/WRITEOFF/PAID event**, and their distribution is uniform across all seven statuses (14.1%
each). An account that is CLOSED today is ACTIVE tomorrow.

**Impact.** Account state cannot be reconstructed. Any suppression list, any lifecycle funnel, any
"accounts closed this month" metric is built on sand.

**The trap we avoided.** Defining closure as "first terminal event" produces a *wasted targeting*
series rising from 5.7% in January to 58.1% in August — a catastrophic-looking operational
collapse. It is an artifact of history accumulation: early months simply have few status rows, so
most accounts read as *status unknown*. Among accounts with a **known** status the figure is flat
at ~43% all year. Publishing the naive series would have handed leadership a fake crisis to match
their fake improvement.

**Treatment.** Point-in-time (`ASOF`) join on last known status, with an explicit
`status_known_asof` flag so the denominator is never silently wrong.

---

### 9. Targeting is statistically indistinguishable from random — HIGH (finding, not defect)

**Detection.** 43.1% of targeting rows aim at an account whose last known status was
CLOSED/WRITEOFF/PAID. Random selection over seven statuses, three of which are terminal, would hit
42.9%. Priority score, recommended channel, risk segment and DPD band are all independent of who
pays (χ² p > 0.05 on all but `dpd_band`, which shows a non-monotonic 2.4pp spread at p = 0.028).

**Impact.** The targeting engine adds no information. This is the strongest actionable finding in
the dataset.

---

### 10. `accounts` holds current snapshots with no as-of date — MEDIUM

**Detection.** `dpd`, `status` and `outstanding_amount` have no effective-date column;
`opened_at` ends 2025-11-30 while events run through 2026-08.

**Impact.** Look-ahead bias. Segmenting January by an account's August DPD band assigns every
account its end-state label in every past month.

**Treatment.** Columns renamed `_snapshot` so they cannot be used by accident; every segment view
carries the caveat inline.

---

### 11. `campaign_name` is a label, not a definition — MEDIUM

**Detection.** 5 campaign names span 120 `campaign_id`s. Each name appears with up to 5 different
channels, 5 target definitions and 4 strategy versions. `DIGITAL_FOLLOWUP` runs on channel `FIELD`.

**Impact.** Grouping performance by campaign name mixes different channels and different target
populations under one heading.

**Treatment.** Reporting grain is `campaign_id`; roll-ups use
`campaign_key = name | channel | target_definition | strategy_version`.

---

### 12. 15 vendor IDs, 5 actual vendors — MEDIUM

**Detection.** `vendor_telephony` has 15 `vendor_id`s mapping to 5 `vendor_name`s. Airtel alone
appears under five IDs, with three different `schema_version`s and two different timezones.

**Impact.** Vendor performance reported by `vendor_id` splits one supplier across five noisy
lines and creates an apparent 1.8pp spread. Resolved to vendor name, the spread is 1.2pp
(χ² p = 0.044 on 90,000 calls — detectable, but not economically meaningful).

**Treatment.** `vendor_group = vendor_name` is the reporting grain. `vendor_id` is a telephony
*account*, not a vendor.

---

### 13. `borrowers` is an overwritten record dumped with its history — MEDIUM

**Detection.** 30,600 rows for 11,015 borrowers, plus 600 byte-identical duplicates. **15,354 rows
have `updated_at` earlier than `created_at`**, so `updated_at` is not a valid recency key.

**Impact.** Joining un-collapsed multiplies every downstream count by ~2.8×.

**Treatment.** SCD-1 collapse ranked on `GREATEST(created_at, updated_at)`, with the inversion
flagged per row.

---

### 14. Calls, attempts and dispositions are not a hierarchy — MEDIUM

**Detection.** 50% of `call_attempts` and 50% of `call_dispositions` are timestamped **before**
their parent call. `account_status_history` shows an ingestion lag symmetric about zero
(p5 = −21.6h, p50 = −0.1h, p95 = +21.6h) — random jitter, not late arrival.

**Impact.** Any funnel computed as dispositions ÷ calls, or attempts ÷ calls, is meaningless.

**Treatment.** The three streams are modelled independently. RPC is measured on the disposition
stream in its own right, never as a ratio to the call stream.

---

### 15. 8.2% of borrower IDs are orphans — LOW

**Detection.** Consistent ~8.2% of distinct `borrower_id`s in every event table are absent from
`borrowers`. 455 accounts have no `borrower_id` at all. Account IDs, by contrast, resolve 100%.

**Impact.** Borrower-level cuts (geography, demographics) lose ~8% of the population.
Account-level and money-level metrics are unaffected.

**Treatment.** Rows are **kept and flagged**. An account with a broken borrower link still owes
money and still receives payments; dropping it would *understate* recovery, which is the opposite
of the error under investigation.

---

### 16. No cost data exists anywhere in the dataset — HIGH (absence)

**Detection.** Searched all 17 tables for cost, rate, tariff, salary, commission, fee. Nothing.

**Impact.** **Cost per ₹ recovered — one of the metrics the brief asks us to challenge — is not
computable.** Neither is recovery per agent-hour in margin terms, nor channel ROI, nor any
break-even. Every financial figure in the investment case is an assumption with a stated range.

Related: `agent_sessions` holds ~68 logins/day for a 1,000-agent roster placing ~409 calls/day.
It is a sample, not the roster, so agent-hours and every derived cost inherit that uncertainty.

---

## Cleaning ledger (raw → golden)

| Table | Rule | In | Out | Removed | Reason |
|---|---|---:|---:|---:|---|
| borrowers | drop identical rows | 30,600 | 30,000 | 600 | double ingest |
| borrowers | SCD-1 collapse | 30,000 | 11,015 | 18,985 | overwritten record dumped with history |
| accounts | keep all, flag links | 30,000 | 30,000 | 0 | accounts are the business grain |
| agents | SCD-1 collapse | 30,000 | 1,000 | 29,000 | join-safety only; quarantined |
| payments | drop identical rows | 25,500 | 25,014 | 486 | double ingest |
| payments | dedup `payment_id` | 25,014 | 25,000 | 14 | surrogate key contract |
| payments | dedup txn keys | 25,000 | 25,000 | 0 | none remained after the above |
| calls | drop identical rows | 91,350 | 90,079 | 1,271 | double ingest |
| calls | dedup `call_id` | 90,079 | 90,000 | 79 | surrogate key contract |
| calls | window restriction (IST) | 90,000 | 89,948 | 52 | stragglers outside the window |
| call_dispositions | dedup | 35,000 | 35,000 | 0 | clean |
| promises_to_pay | dedup | 18,000 | 18,000 | 0 | clean |
| daily_targeting | dedup | 45,000 | 45,000 | 0 | clean |

Nothing is deleted silently — every removed row is written to `outputs/golden/_rejections.csv`
with a rule and a reason, so `raw = golden + rejected` reconciles.

---

## Production data-quality checks

Implemented in `sql/03_data_quality_checks.sql`. `BLOCK` prevents publication; `WARN` publishes
with a banner.

| Severity | Check | Catches |
|---|---|---|
| BLOCK | Primary key uniqueness on every dim and fact | re-ingest, surrogate reuse |
| BLOCK | `fct_payment.account_id` resolves | orphaned money |
| BLOCK | `payment_status` in domain | a new status silently changing recovery |
| BLOCK | `disposition_std` in domain | an unannounced taxonomy migration |
| BLOCK | `timezone` in domain | silent IST-conversion errors |
| BLOCK | No future-dated events | clock skew at source |
| BLOCK | **No MoM on a partial month** | **the August problem** |
| BLOCK | Monthly aggregate reconciles to the fact table | broken transformation |
| WARN | Headline MoM vs per-day MoM diverge > 5pp | **the February problem** |
| WARN | Daily volume outside 4σ | ingest failure or spike |
| WARN | Rejection rate > 25% | over-aggressive cleaning |
| WARN | Orphan borrower links | source system drift |

The two checks in bold would have stopped the 11% claim before it reached a slide.

---

## What we would need to fix this properly

1. **Cost feeds** — per call, per message, per visit, per agent-hour. Without these, half the
   requested metric set is decorative.
2. **An account-state service** with a real state machine, so CLOSED means closed and a
   suppression list can be built.
3. **A resolvable agent identity** — one person, one ID, with proper SCD-2 history.
4. **Timezone at write time** — store UTC with an offset, not a naive local string plus a label.
5. **A completed disposition migration** — one taxonomy, with the legacy codes retired rather
   than run in parallel.
6. **An `as_of_date` on the accounts snapshot**, or better, a daily account-state fact table.

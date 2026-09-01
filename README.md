# Collections Recovery — Data Analyst Assignment

**The claim:** *"Recovery has improved by 11% month-on-month."*

**The finding:** it has not. Net recovery has been flat at **₹0.555 Cr/day** for the entire
period (CV 2.0%, trend p = 0.11, and the point estimate is negative). The only +11% in the
dataset is the February → March step on gross collections, and `31 ÷ 28 − 1 = +10.7%` of its 11.0
points is the calendar. Reported recovery is also overstated by
**₹12.06 Cr (9.9%)** through duplicate rows and reversals counted as collections.

Start with **[`docs/EXECUTIVE_MEMO.md`](docs/EXECUTIVE_MEMO.md)** (2 pages) or open
`outputs/dashboard.html`.

---

## Repository map

```
├── README.md
├── data/raw/                      the 17 source CSVs, untouched
├── pipeline/                      the reproducible analysis, in order
│   ├── 00_profile.py              what is in the box: grain, keys, ranges, integrity
│   ├── 01_forensics.py            the seven forensic questions (A–G) from the brief
│   ├── 02_recovery_reconstruction.py  where "+11%" comes from — the definition ladder
│   ├── 03_drivers_and_stats.py    mix, cohort, selection, Simpson's, driver scan
│   ├── 04_golden_dataset.py       raw → rejected/corrected → golden, with the ledger
│   ├── 05_counterfactual.py       DiD design, placebo, power, and the experiment to run
│   └── 06_investment_case.py      cost model, option scorecard, ROI and break-even
├── sql/                           the same logic, production-shaped
│   ├── 00_staging.sql             types, IST clock, code harmonisation, DQ flags
│   ├── 01_golden.sql              dedup rules, SCD collapse, as-of joins, grain
│   ├── 02_metrics.sql             one definition per metric, all exposure-normalised
│   └── 03_data_quality_checks.sql the assertions that gate publication
├── notebooks/
│   ├── analysis.ipynb             the reasoning, executed, with charts
│   └── build_notebook.py          notebook source (reviewable in git)
├── outputs/
│   ├── dashboard.html             one screen, for a CEO, in 60 seconds
│   ├── architecture.mermaid       the production design
│   ├── golden/                    the golden dataset + ledgers + scorecards
│   └── reports/                   full text output of every pipeline stage
└── docs/
    ├── EXECUTIVE_MEMO.md          what happened, why, confidence, what to do
    ├── DATA_QUALITY_REPORT.md     16 issues: detection, impact, treatment, residual risk
    └── ARCHITECTURE.md            raw → staging → clean → golden → feature → metrics
```

---

## Running it

```bash
pip install pandas numpy scipy duckdb matplotlib nbformat nbclient ipykernel

# the Python pipeline, in order
for s in pipeline/*.py; do python "$s"; done

# the SQL pipeline (independent implementation — the two reconcile to ₹122.09 Cr)
python -c "
import duckdb; c = duckdb.connect('collections.duckdb')
for f in ['sql/00_staging.sql','sql/01_golden.sql','sql/02_metrics.sql','sql/03_data_quality_checks.sql']:
    c.execute(open(f).read())
print(c.sql('SELECT * FROM metrics.mom_comparison').df())
print(c.sql('SELECT * FROM metrics.dq_results WHERE failures > 0').df())
"

# rebuild and execute the notebook
python notebooks/build_notebook.py
```

Everything is deterministic. No seeds, no sampling, no model fitting.

---

## The four questions

### 1. What happened

Nothing. Net recovery ran at ₹0.555 Cr/day every month; calls, contacts, promises and payments
per day are equally flat. The window is **7 months and 8 days**, not the 12 the brief describes,
and August is partial. (`calls` additionally carries five stray rows outside that window; they are
dropped as stragglers and logged in the reject ledger.)

- **Genuinely improving:** none.
- **Misleading:** contact rate (unstable denominator), PTP rate (undercounted 50%), PTP kept rate
  (manufactured downtrend), channel conversion (an artifact of the attribution window), cost per
  ₹ recovered (not computable — there is no cost data in any of the 17 tables).

### 2. Why it happened

It did not. Mix, cohort, selection, survivorship, Simpson's paradox and attribution-window bias
were each tested and each returns null. Agents, vendors, tenure, time-of-day and attempt number
are all statistically indistinguishable.

Two things *are* real:

- **The targeting engine carries no information.** 43.1% of targeting aims at accounts last
  recorded CLOSED/WRITEOFF/PAID, against a 42.9% random baseline.
- **45.9% of calls fall outside the 08:00–21:00 IST window** of the RBI Fair Practices Code —
  invisible today because no report converts the timestamps.

### 3. Is the 11% real

No. It is one cherry-picked month pair explained by February's length, or the month-on-month
growth of a cumulative series (which grows by construction, always). Actual mean month-on-month:
**+0.3% gross, −0.1% net**, neither distinguishable from zero.

The sharper point: the **minimum detectable effect of this dataset is 12.9%**. Even had the 11%
been real, this data could not have seen it.

### 4. Where the ₹10 Cr should go

**Stage it.** ₹1.2 Cr now on instrumentation and a randomised holdout; ₹8.8 Cr released only on a
pre-registered ≥10% lift. If forced to name one area it is **better borrower targeting** — the
only option where the data proves a *gap* rather than merely failing to disprove one.

Break-even on Stage 1 is **+0.6%**; on the full ₹10 Cr, **+4.9%**. And ₹10 Cr is **1.7×** the
entire modelled annual cost of running this operation — this rebuilds it, it does not extend it.

**Question one for the CFO:** is this book owned or collected for a fee? On a 20% fee, break-even
rises to +25% and the answer becomes an unambiguous no on all six options.

---

## Three traps this analysis avoided

Worth reading even if nothing else is, because each one nearly produced a confident wrong answer.

1. **Deduplicating payments on `payment_reference`.** The obvious natural key. 3,405 references
   are reused across *different accounts and amounts* — an ID-space collision, not a duplicate.
   The naive rule removes 3,809 rows instead of 500, destroying 2,617 genuine successful payments
   worth ₹19.8 Cr.

2. **Resolving agents on `employee_code`.** The obvious natural key again. It raises mean calls
   per person from 88 to 137 — a **+56% productivity improvement invented by an
   entity-resolution choice**. Same class of error as the 11%, and more seductive because the fix
   looks like diligence.

3. **Reporting "wasted targeting" as a trend.** Defining account closure as the first terminal
   status produces a series rising 5.7% → 58.1% across the year: a catastrophic-looking
   operational collapse. It is history accumulation. Among accounts with a *known* status the
   figure is flat at ~43%. Publishing it would have handed leadership a fake crisis to match
   their fake improvement.

The status table turns out not to be a state machine at all — 19,673 status changes occur *after*
an account's first CLOSED/WRITEOFF/PAID event, uniformly distributed. Testing that assumption
before using it is the difference between the third trap and the real finding underneath it.

---

## Evidence grading

Every conclusion in the memo and notebook is graded:

| Grade | Meaning |
|---|---|
| **Fact** | Directly measured, reproducible from the golden layer |
| **Strong evidence** | Multiple independent tests agree; mechanism identified |
| **Correlation** | Association observed, causation not established |
| **Hypothesis** | Plausible, stated as untested |

Confidence on the headline findings: **high** that recovery is flat and the 11% is an artifact;
**high** that targeting carries no signal; **medium** that no operational lever works (effects
below ~13% would be invisible); **low** on every cost and ROI figure, because the dataset
contains no cost data at all.

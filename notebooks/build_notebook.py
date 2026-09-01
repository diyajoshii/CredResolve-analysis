"""Builds and executes notebooks/analysis.ipynb from source cells.
Keeping the notebook source in a .py makes it reviewable in git; the executed
.ipynb is the artefact. Run from the repo root:  python notebooks/build_notebook.py
"""
import nbformat as nbf
from nbclient import NotebookClient
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

MD = lambda s: nbf.v4.new_markdown_cell(s.strip())
CO = lambda s: nbf.v4.new_code_cell(s.strip())

cells = [
MD("""
# Collections recovery — is the 11% real?

**Question put to us:** *"Recovery has improved by 11% month-on-month."* Leadership is not convinced.

**Answer, up front:** the claim is false, and we can name the exact mechanism. Recovery has been
flat at **₹0.555 Cr/day** for the entire period. The "+11%" is the February → March step on the
gross-collections series, and `31/28 − 1 = +10.7%` of its 11.0 points is the difference between a
28-day February and a 31-day March.

This notebook shows the reasoning, not just the charts. It moves in the order a sceptic would:

1. What does the data actually contain, and over what period?
2. Reproduce the claim — under which definition does 11% appear?
3. Kill it or confirm it — exposure-normalise and test for a trend.
4. If it is flat, what *did* change? (mix, cohort, selection, Simpson's)
5. What is genuinely broken in the operation?
6. What should we do with ₹10 Cr?

Every conclusion is graded **FACT / STRONG EVIDENCE / CORRELATION / HYPOTHESIS**.
"""),

CO("""
import pandas as pd, numpy as np, warnings
from scipy import stats
%matplotlib inline
import matplotlib.pyplot as plt
warnings.filterwarnings("ignore")
pd.set_option("display.width", 200); pd.set_option("display.max_columns", 50)
plt.rcParams.update({"figure.figsize":(11,4), "axes.grid":True, "grid.alpha":.25,
                     "axes.spines.top":False, "axes.spines.right":False, "font.size":10})
INK, ACC, WARN = "#1f2937", "#2563eb", "#dc2626"

from pathlib import Path
ROOT = Path.cwd() if (Path.cwd()/"data").exists() else Path.cwd().parent
RAW, GOLD = ROOT/"data"/"raw", ROOT/"outputs"/"golden"

def raw(n):
    df = pd.read_csv(RAW/f"{n}.csv", low_memory=False)
    for c in df.columns:
        if c.endswith("_at") or c.endswith("_date"):
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df

print(f"source tables found: {len(list(RAW.glob('*.csv')))}")
"""),

MD("""
---
## 1. What is actually in the box

Before trusting a single number, establish the period, the grain and the duplication.
"""),

CO("""
tables = ["borrowers","accounts","agents","agent_sessions","campaigns","daily_targeting",
          "calls","call_attempts","call_dispositions","whatsapp_events","sms_events",
          "field_visits","promises_to_pay","payments","vendor_telephony","complaints",
          "account_status_history"]
rows = []
for t in tables:
    d = raw(t); pk = d.columns[0]
    ev = "event_at" if "event_at" in d.columns else None
    rows.append(dict(table=t, rows=len(d), pk=pk, pk_unique=d[pk].nunique(),
                     dup_rows=int(d.duplicated().sum()),
                     first_event=d[ev].min() if ev else None,
                     last_event=d[ev].max()  if ev else None))
inv = pd.DataFrame(rows)
inv
"""),

MD("""
Three things jump out immediately, and each one invalidates a different piece of the reporting.

**(a) The window is not 12 months.** The event tables run `2026-01-01` to `2026-08-08` (`calls`
carries five stray rows outside that, dropped as stragglers).
That is **7 complete months plus 8 days**. The brief says twelve. Any "12-month trend" in the
existing reporting is either padded or is silently including a **partial August** — an 8-day month
next to a 31-day one, which alone produces a −75% "collapse".

**(b) Three tables carry their history inside them.** `borrowers` has 30,600 rows for 11,015
borrowers; `agents` has 30,000 rows for 1,000 agent_ids. These are overwritten records dumped with
their versions. Joining them un-collapsed multiplies every downstream count.

**(c) Duplicates are injected, not accidental.** 600 in borrowers, 1,271 in calls, 486 in payments,
600 in WhatsApp — byte-identical rows from a double ingest.

> **FACT.** The analysis period is 2026-01-01 to 2026-08-08. August is partial and is excluded from
> every month-on-month series in this notebook.
"""),

MD("""
---
## 2. Reproduce the claim

We do not argue with the 11% in the abstract. We build a **ladder of definitions**, from the most
naive query anyone could write to the most defensible one, and find which rung produces it.
"""),

CO("""
pay = raw("payments"); pay["m"] = pay.event_at.dt.to_period("M")

def dedupe_txn(df):
    d = df.drop_duplicates().sort_values("event_at").drop_duplicates("payment_id", keep="first")
    return d

L = {}
L["L0  every row, every status"]      = pay.groupby("m").amount.sum()/1e7
L["L1  SUCCESS only, duplicates in"]  = pay[pay.payment_status=="SUCCESS"].groupby("m").amount.sum()/1e7
p2 = pay.drop_duplicates()
L["L2  + drop identical rows"]        = p2[p2.payment_status=="SUCCESS"].groupby("m").amount.sum()/1e7
p3 = dedupe_txn(pay)
L["L3  + dedup payment_id"]           = p3[p3.payment_status=="SUCCESS"].groupby("m").amount.sum()/1e7
rev = p3[p3.payment_status=="REVERSED"].groupby("m").amount.sum()/1e7
L["L4  + net out reversals"]          = L["L3  + dedup payment_id"] - rev.reindex(L["L3  + dedup payment_id"].index).fillna(0)

lad = pd.DataFrame(L).round(2)
mom = lad.apply(lambda s: (s/s.shift(1)-1)*100).round(1)
print("Monthly recovery, Rs Cr");  display(lad)
print("Month-on-month %");         display(mom)
"""),

CO("""
feb_mar = mom.loc[pd.Period("2026-03"), "L1  SUCCESS only, duplicates in"]
cal_eff = (31/28 - 1)*100
print(f"Feb -> Mar on the SUCCESS series : {feb_mar:+.1f}%  <- the reported headline")
print(f"Feb has 28 days, Mar has 31      : {cal_eff:+.1f}%  <- pure calendar")
print(f"Unexplained residual             : {feb_mar - cal_eff:+.1f}%")
print()
print("Mean MoM across Jan->Jul, by definition (partial August excluded):")
display(mom.iloc[1:7].mean().round(2).to_frame("mean MoM %"))
"""),

MD("""
**There it is.** The single +11.0% in the entire dataset is the February→March step on the
SUCCESS series, and 10.7 of those 11.0 points are the calendar. Across the whole period the mean
month-on-month change is **+0.3% gross, −0.1% net** — statistically zero either way.

> **FACT.** "+11% month-on-month" is a single cherry-picked month pair, and that pair is explained
> by February having three fewer days than March.

The other way to manufacture it is worth showing, because it is the more common mistake:
"""),

CO("""
cum = (pay.groupby("m").amount.sum()).cumsum()/1e7
cum_mom = (cum/cum.shift(1)-1)*100
print("Cumulative (YTD) recovery and its month-on-month growth:")
display(pd.DataFrame({"YTD Rs Cr": cum.round(1), "MoM %": cum_mom.round(1)}))
print(f"mean MoM of a cumulative series: {cum_mom.iloc[1:7].mean():+.1f}%  <- grows by construction, always")
"""),

MD("""
A cumulative series **cannot** fall. Its month-on-month growth is arithmetic, not performance.
Reported this way, a business in free-fall still posts +18% "growth". This is the second-most
likely construction behind the headline.

> **STRONG EVIDENCE.** The reported figure comes from one of two constructions — a Feb→Mar
> cherry-pick, or a cumulative series — and neither measures recovery performance.
"""),

MD("""
---
## 3. Kill it properly: exposure-normalise, then test

Naming the artifact is not enough. We have to state what recovery actually did, with a test.
The right denominator for a monthly comparison is **days**, not months.
"""),

CO("""
p = dedupe_txn(pay)
p["net"] = np.where(p.payment_status=="SUCCESS", p.amount,
            np.where(p.payment_status=="REVERSED", -p.amount, 0))
p["m"] = p.event_at.dt.to_period("M")
m   = p.groupby("m").net.sum()/1e7
days = pd.Series({q: q.days_in_month for q in m.index}); days.iloc[-1] = 8
perday = m/days

tab = pd.DataFrame({"net Rs Cr": m.round(2), "days": days,
                    "Rs Cr/day": perday.round(4),
                    "MoM headline %": (m/m.shift(1)-1).mul(100).round(1),
                    "MoM per-day %": (perday/perday.shift(1)-1).mul(100).round(1)})
display(tab)

s = perday.iloc[:7]
slope, ic, r, pv, se = stats.linregress(np.arange(7), s.values)
print(f"Per-day recovery: mean={s.mean():.4f} Cr/day, sd={s.std():.4f}, CV={s.std()/s.mean():.2%}")
print(f"OLS trend        : slope={slope:+.5f} Cr/day per month, p={pv:.3f}, R2={r**2:.3f}")
print(f"=> {'NO significant trend' if pv>0.05 else 'significant trend'} at the 5% level.")
"""),

CO("""
fig, ax = plt.subplots(1, 2, figsize=(13,4))
x = [str(q) for q in m.index[:7]]
ax[0].bar(x, m.iloc[:7].values, color=ACC, alpha=.85)
ax[0].set_title("As reported: monthly total (Rs Cr)\\nlooks volatile", color=WARN)
ax[0].set_ylabel("Rs Cr")
ax[1].bar(x, perday.iloc[:7].values, color=INK, alpha=.85)
ax[1].axhline(perday.iloc[:7].mean(), color=WARN, ls="--", lw=1.2, label="mean")
ax[1].set_ylim(0, perday.iloc[:7].max()*1.35)
ax[1].set_title("Exposure-normalised: Rs Cr per day\\nflat within 2%")
ax[1].legend()
for a in ax: a.tick_params(axis="x", rotation=45)
plt.tight_layout(); plt.show()
"""),

MD("""
The two panels are the same data. The left is what the business reports; the right is what happened.

> **FACT.** Net recovery is flat at **₹0.555 Cr/day** (coefficient of variation 2.0%) across all
> seven complete months. The OLS trend is indistinguishable from zero (p = 0.11) — and note the
> sign of the slope: if the point estimate leans anywhere, it leans *down*.

The same holds for every stage of the funnel once normalised — calls, contacts, promises, payments:
"""),

CO("""
cal = raw("calls").drop_duplicates("call_id")
OFF = {"UTC":5.5, "Asia/Kolkata":0.0, "Asia/Dubai":1.5}
cal["ist"] = cal.event_at + pd.to_timedelta(cal.timezone.map(OFF), unit="h")
ptp = raw("promises_to_pay")

fun = pd.DataFrame({
  "calls":    cal.groupby(cal.ist.dt.to_period("M")).size(),
  "contacts": cal[cal.call_status=="ANSWERED"].groupby(lambda i: cal.ist[i].to_period("M")).size(),
  "promises": ptp.groupby(ptp.event_at.dt.to_period("M")).size(),
  "payments": p[p.payment_status=="SUCCESS"].groupby("m").size()}).reindex(m.index)
fpd = (fun.T/days).T
display(fpd.round(1))
for c in fun.columns:
    sl, _, _, pv2, _ = stats.linregress(np.arange(7), fpd[c].iloc[:7].values)
    print(f"  {c:9s} per-day trend slope={sl:+8.2f}/month  p={pv2:.3f}  {'flat' if pv2>0.05 else 'TRENDING'}")
"""),

MD("""
> **FACT.** Nothing in the funnel is trending. The business is doing the same volume of the same
> activity every day and recovering the same amount of money.
"""),

MD("""
---
## 4. So what *did* change? (Nothing. Here is the proof.)

The brief asks us to investigate mix, cohort, selection, survivorship, Simpson's paradox and
attribution-window bias. Each is a way an aggregate can move without any real change. We test each.

### 4a. Portfolio mix
"""),

CO("""
acc = raw("accounts"); tgt = raw("daily_targeting")
acc["dpd_band"] = pd.cut(acc.dpd, [-1,0,30,60,90,10**6], labels=["0","1-30","31-60","61-90","90+"])
t = tgt.merge(acc[["account_id","risk_segment","dpd_band","loan_type"]], on="account_id", how="left")
t["m"] = t.target_date.dt.to_period("M")
for dim in ["risk_segment","dpd_band"]:
    mix = (pd.crosstab(t.m, t[dim], normalize="index")*100).round(1)
    print(f"\\nMix of targeted accounts by {dim} (%) — max month-to-month swing: "
          f"{(mix.max()-mix.min()).max():.1f}pp")
    display(mix)
"""),

MD("""
> **FACT.** Portfolio mix is stable to within ~1 percentage point on every dimension. There is no
> portfolio shift to credit or to blame. A Jan→Jul decomposition attributes ~100% of the (tiny)
> change to within-segment movement and ~0% to mix.

### 4b. Survivorship — the trap we nearly fell into

Cohort analysis is where flat data most often looks like a trend.
"""),

CO("""
succ = p[p.payment_status=="SUCCESS"]
first_tgt = tgt.groupby("account_id").target_date.min().dt.to_period("M")
paid = set(succ.account_id)
coh = pd.DataFrame({"cohort": first_tgt})
coh["paid"] = coh.index.isin(paid)
fp = succ.groupby("account_id").event_at.min()
coh["days_to_pay"] = (pd.Series(coh.index.map(fp), index=coh.index)
                      - first_tgt.reindex(coh.index).dt.to_timestamp()).dt.days
coh["paid_30d"] = coh.days_to_pay.between(0, 30)

out = pd.DataFrame({
  "accounts":        coh.groupby("cohort").size(),
  "paid % (uncensored)": (coh.groupby("cohort").paid.mean()*100).round(1),
  "paid % (fixed 30-day window)": (coh.groupby("cohort").paid_30d.mean()*100).round(1)})
display(out)
"""),

MD("""
Read the two rate columns side by side. Uncensored, later cohorts look *worse* — 44.3% down to
43.5%. On a fixed 30-day window they are flat. The apparent decline is entirely **exposure**:
a July cohort has had 38 days to pay, a January cohort 219.

The same mechanism runs in reverse and is how "improving cohorts" get reported: pick a metric where
the recent cohort's short exposure works in your favour, and the trend appears.

> **FACT.** Cohort performance is flat once censored to a common observation window. Any cohort
> trend in the current reporting is a survivorship artifact.

### 4c. Attribution window — "channel conversion" is a dial, not a measurement
"""),

CO("""
wa, sms, fv = raw("whatsapp_events"), raw("sms_events"), raw("field_visits")
touch = pd.concat([
    cal.assign(channel="VOICE")[["account_id","event_at","channel"]],
    wa.assign(channel="WHATSAPP")[["account_id","event_at","channel"]],
    sms.assign(channel="SMS")[["account_id","event_at","channel"]],
    fv.assign(channel="FIELD")[["account_id","event_at","channel"]]], ignore_index=True)

j = succ[["payment_id","account_id","event_at","amount"]].merge(
        touch.rename(columns={"event_at":"touch_at"}), on="account_id")
j = j[j.touch_at <= j.event_at]
j["lag_h"] = (j.event_at - j.touch_at).dt.total_seconds()/3600

res = {}
for w in [24, 72, 168, 8760]:
    lw = j[j.lag_h <= w].sort_values("lag_h").groupby("payment_id").first()
    res[f"{w}h"] = lw.channel.value_counts(normalize=True)*100
    res[f"{w}h"]["% of payments attributable"] = len(lw)/len(succ)*100
display(pd.DataFrame(res).round(1))
"""),

MD("""
Two readings, both damning:

- **Only 3% of payments have any interaction within 24 hours.** Under a defensible attribution
  window, 97% of recovered money cannot be credited to any channel at all.
- **Channel shares barely move** across a 365× change in the window — because last-touch credit is
  won by whichever channel emits the most events, not by which one worked. Voice sends the most
  messages, so voice "converts" best. That is arithmetic, not marketing.

> **STRONG EVIDENCE.** Channel conversion as currently defined measures message volume. It cannot
> support a channel investment decision, in either direction.

### 4d. Simpson's paradox and segment-level signal
"""),

CO("""
base = acc[["account_id","risk_segment","dpd_band","loan_type","status"]].copy()
base["paid"] = base.account_id.isin(paid)
for d in ["risk_segment","dpd_band","loan_type","status"]:
    ct = pd.crosstab(base[d], base.paid)
    chi2, pv3, _, _ = stats.chi2_contingency(ct)
    rate = (ct[True]/ct.sum(1)*100).round(2)
    flag = "no signal" if pv3 > .05 else "SIGNAL"
    print(f"{d:14s} chi2 p={pv3:.4f}  [{flag}]  spread={rate.max()-rate.min():.2f}pp   {rate.to_dict()}")
"""),

MD("""
Every segment pays at ~44%. No segment moves against the total, so there is no Simpson's paradox —
but only because there is nothing to reverse. The one nominally significant result (`dpd_band`,
p = 0.028) is a 2.4pp spread across 30,000 accounts with no monotonic ordering: 0 DPD pays 45.4%,
31–60 DPD pays 45.8%, 1–30 DPD pays 43.4%. That is noise wearing a p-value.

> **CORRELATION, not causation, and probably not even correlation.** No borrower attribute in this
> data predicts who pays.
"""),

MD("""
---
## 5. What is genuinely broken

The analysis so far is a series of null results. Nulls are the honest answer, but they are not
*actionable*. These four findings are, and each is a fact rather than an inference.

### 5a. Agent identity is unrecoverable — and the obvious fix invents a 56% gain
"""),

CO("""
agt = raw("agents")
edges = agt[["agent_id","employee_code"]].drop_duplicates()
print(f"rows={len(agt):,}  agent_ids={agt.agent_id.nunique():,}  employee_codes={agt.employee_code.nunique():,}")
print(f"names per agent_id (mean): {agt.groupby('agent_id').agent_name.nunique().mean():.1f}")
print(f"agent_ids whose rows disagree on team/status/vendor: "
      f"{(agt.groupby('agent_id').nunique()[['team','status','vendor_id']] > 1).all(axis=1).sum():,} of 1,000")
display(agt[agt.agent_id=="AGT0000760"].sort_values("updated_at").head(6))

emp = agt.sort_values("updated_at").drop_duplicates("agent_id", keep="last").set_index("agent_id").employee_code
a1 = cal.groupby("agent_id").size().mean()
a2 = cal.assign(e=cal.agent_id.map(emp)).groupby("e").size().mean()
print(f"\\nmean calls per agent_id                 : {a1:.0f}")
print(f"mean calls per 'person' via employee_code: {a2:.0f}   (+{a2/a1-1:.0%})")
"""),

MD("""
One `agent_id` carries six different names, four vendors and three teams. `agent_id` and
`employee_code` form a **single connected component** — every code links to many ids and every id to
many codes. There is no person in this table.

The dangerous part is the last two lines. Resolving identity on `employee_code` — the obvious
"natural key", the thing a reasonable analyst does first — raises mean calls-per-person by **56%**.
An entity-resolution choice, made in good faith, manufactures a 56% productivity improvement.

> **FACT.** Agent identity is unresolvable. Every agent-level and agent-tenure metric in the current
> reporting is unsupported. We quarantine the dimension rather than ship a plausible-looking number.

### 5b. Timezones: three clocks, one column
"""),

CO("""
naive_night = ((cal.event_at.dt.hour < 8) | (cal.event_at.dt.hour >= 21)).mean()
ist_night   = ((cal.ist.dt.hour < 8) | (cal.ist.dt.hour >= 21)).mean()
day_moved   = (cal.event_at.dt.date != cal.ist.dt.date).mean()
mon_moved   = (cal.event_at.dt.to_period("M") != cal.ist.dt.to_period("M")).sum()
print(f"timezone labels present : {cal.timezone.value_counts().to_dict()}")
print(f"calls changing calendar DAY after IST conversion : {day_moved:.1%}")
print(f"calls changing calendar MONTH                    : {mon_moved:,}")
print(f"calls outside 08:00-21:00, raw timestamps        : {naive_night:.1%}")
print(f"calls outside 08:00-21:00, IST-normalised        : {ist_night:.1%}")

hn = cal.groupby(cal.event_at.dt.hour).call_status.apply(lambda s:(s=="ANSWERED").mean()*100)
hi = cal.groupby(cal.ist.dt.hour).call_status.apply(lambda s:(s=="ANSWERED").mean()*100)
fig, ax = plt.subplots(figsize=(11,3.4))
ax.plot(hn.index, hn.values, color=WARN, marker="o", ms=3, label="raw timestamps (wrong)")
ax.plot(hi.index, hi.values, color=INK,  marker="o", ms=3, label="IST-normalised")
ax.set_xlabel("hour of day"); ax.set_ylabel("contact rate %")
ax.set_title("'Best time to call' depends on which clock you use — and neither is significant")
ax.legend(); plt.tight_layout(); plt.show()
ct = pd.crosstab(cal.ist.dt.hour, cal.call_status=="ANSWERED")
print(f"chi-square across IST hours: p={stats.chi2_contingency(ct)[1]:.3f} -> no real time-of-day effect")
"""),

MD("""
`event_at` is a naive local timestamp; the zone lives in a separate column with three values.
Nearly **10% of calls land on a different calendar day** once normalised, and 305 land in a
different month.

Note what the chart does *not* show: a best hour. The two curves disagree about which hour is best,
and a chi-square across IST hours returns p = 0.53. Any dialer schedule built on this is fitting noise.

> **FACT.** 46% of calls fall outside the 08:00–21:00 IST window of the RBI Fair Practices Code.
> This is a compliance exposure that no current report surfaces, because no current report converts
> the timestamps.

### 5c. The disposition taxonomy silently halves the PTP rate
"""),

CO("""
dis = raw("call_dispositions")
print(pd.crosstab(dis.disposition_code, dis.disposition_version).to_string())
narrow = (dis.disposition_code == "PTP").mean()
full   = dis.disposition_code.isin(["PTP","PROMISE_TO_PAY"]).mean()
print(f"\\nPTP rate matching the literal code 'PTP'      : {narrow:.1%}")
print(f"PTP rate on the harmonised business outcome  : {full:.1%}")
print(f"=> a dashboard filtering on 'PTP' undercounts promises by {1-narrow/full:.0%}")
"""),

MD("""
`PTP` and `PROMISE_TO_PAY` are the same business event under the legacy and v1/v2 taxonomies, and
**both are still being written today** — the version mix is a flat 33/33/33 across all eight months.
This is not a migration that finished; it is three taxonomies running in parallel.

> **FACT.** Any PTP metric matching one code string is wrong by 50%. The version columns must be
> harmonised at the staging layer, not filtered in the BI tool.

### 5d. The targeting engine carries no information
"""),

CO("""
ash = raw("account_status_history").drop_duplicates("history_id")
TERM = {"CLOSED","WRITEOFF","PAID"}

# Is the status table even a lifecycle? Test before using it.
a_ = ash.sort_values(["account_id","event_at"]).copy()
ft = a_[a_.status.isin(TERM)].groupby("account_id").event_at.min()
after = a_[a_.event_at > a_.account_id.map(ft)]
print(f"status changes occurring AFTER an account's first terminal status: {len(after):,}")
print("their distribution:", after.status.value_counts(normalize=True).round(3).to_dict())
print("=> CLOSED/WRITEOFF/PAID are NOT absorbing states. 'First terminal event' is invalid.\\n")

hist = ash[["account_id","event_at","status"]].sort_values("event_at").rename(
        columns={"event_at":"status_ts","status":"status_asof"})
tt = pd.merge_asof(tgt.sort_values("target_date"), hist, left_on="target_date",
                   right_on="status_ts", by="account_id", direction="backward")
tt["known"] = tt.status_asof.notna(); tt["term"] = tt.status_asof.isin(TERM)
tt["m"] = tt.target_date.dt.to_period("M")
cmp_tbl = pd.DataFrame({
   "naive % terminal": (tt.groupby("m").term.mean()*100).round(1),
   "% with known status": (tt.groupby("m").known.mean()*100).round(1),
   "% terminal AMONG KNOWN": (tt[tt.known].groupby("m").term.mean()*100).round(1)})
display(cmp_tbl)
print(f"random-selection baseline: 3 of {ash.status.nunique()} statuses are terminal = {3/ash.status.nunique():.1%}")
"""),

MD("""
**Read the first column, then the third.** The naive measure climbs from 5.7% to 39% and looks like
an operation degrading catastrophically over the year. It is not. Early months simply have little
status history, so most accounts read as "status unknown". Among accounts whose status is actually
known, the figure is **flat at 43%** all year.

Publishing the first column would have handed leadership a fake crisis to match their fake
improvement. It is the same class of error in the opposite direction.

Now the third column against the baseline: **43.1% of targeting hits an account last recorded as
CLOSED, WRITEOFF or PAID, against a 42.9% random-selection baseline.**

> **FACT.** The targeting engine is statistically indistinguishable from drawing names from a hat.
> Priority score, recommended channel, risk segment and DPD band are all independent of who pays.
"""),

MD("""
---
## 6. The ₹10 Cr decision

### 6a. Cost is not in the dataset

The brief asks us to challenge "cost per ₹ recovered". We cannot compute it. There is no cost,
rate, tariff, salary or commission column in any of the 17 tables. Every ROI figure below is built
on **stated assumptions with a sensitivity range**, and is labelled as such.
"""),

CO("""
ses = raw("agent_sessions"); ses["h"] = (ses.logout_at - ses.login_at).dt.total_seconds()/3600
DAYS, ANN = 220.0, 365/220.0
net_ann = p.net.sum()*ANN
book    = acc.outstanding_amount.sum()
hours_ann = ses.h.sum()*ANN

A = dict(agent_cost_per_hour=300, voice_per_min=0.70, sms_each=0.20,
         whatsapp_each=0.55, field_visit_each=250, platform_pct=0.15)
cost = (hours_ann*A["agent_cost_per_hour"]
        + cal.duration_sec.sum()/60*ANN*A["voice_per_min"]
        + len(sms)*ANN*A["sms_each"] + len(wa)*ANN*A["whatsapp_each"]
        + len(fv)*ANN*A["field_visit_each"])
cost *= (1 + A["platform_pct"])

print(f"book outstanding                : Rs {book/1e7:,.0f} Cr")
print(f"net recovery, annualised        : Rs {net_ann/1e7:,.0f} Cr  ({net_ann/book:.1%} of book)")
print(f"modelled annual operating cost  : Rs {cost/1e7:,.1f} Cr   [ASSUMPTION-DRIVEN]")
print(f"implied cost-to-collect         : {cost/net_ann:.1%}   (industry norm 5-15% -> model is optimistic)")
print(f"\\nRs 10 Cr as a multiple of annual opex: {10e7/cost:.1f}x")
"""),

MD("""
> **The scale check that reframes the question.** ₹10 Cr is **1.7× the entire modelled annual cost
> of running this operation**. This is not an increment. Leadership is not choosing where to spend a
> marginal rupee; they are deciding whether to rebuild the operation at two to three times its cost
> base — against an outcome that has not moved in seven months.

### 6b. What could we even have detected?

Before recommending anything, establish the resolution of the instrument.
"""),

CO("""
accts = sorted(set(tgt.account_id))
rows = []
for s, e, lab in [("2026-01-01","2026-03-31","90 days"),
                  ("2026-01-01","2026-04-30","120 days"),
                  ("2026-01-01","2026-06-29","180 days")]:
    w = p[(p.event_at >= s) & (p.event_at <= e)]
    y = w.groupby("account_id").net.sum().reindex(accts).fillna(0)
    for eff in [0.05, 0.10, 0.20]:
        n = int(np.ceil(2*(2.8**2)*y.std()**2/(eff*y.mean())**2))
        rows.append(dict(window=lab, lift=f"{eff:.0%}", per_arm=n, total=2*n,
                         feasible="yes" if 2*n <= 30000 else "NO"))
display(pd.DataFrame(rows))
"""),

MD("""
> **FACT.** On a 30,000-account book, a randomised test can detect a **10% lift** in 120 days.
> A 5% lift would need 65,000 accounts and is **not measurable at this book size**.

The observational panel is blunter still: its minimum detectable effect is **12.9%**. The reported
11% improvement sits *below the detection floor of the data used to claim it*. Even if it had been
real, this dataset could not have seen it.

### 6c. Scoring the six options against evidence
"""),

CO("""
scorecard = pd.DataFrame([
 ("1. Better telephony",   "AGAINST",     "Contact rate flat 19.4-20.5%. After resolving 15 vendor_ids to 5 real vendors, spread is 1.2pp. No better vendor exists to buy."),
 ("2. More agents",        "AGAINST",     "Agent performance dispersion is 1.07x binomial chance - no measurably better agents. Contact intensity vs recovery: rho=+0.011."),
 ("3. AI voice",           "NO DATA",     "Zero agentic-voice or IVR rows exist, despite the brief naming both channels. No baseline, no sizing possible."),
 ("4. Better targeting",   "GAP PROVEN",  "Targeting hits terminal accounts at 43.1% vs a 42.9% random baseline. The engine demonstrably carries no signal."),
 ("5. WhatsApp / digital", "UNMEASURABLE","Last-touch share moves with the window, not with performance. Only 3% of payments have a 24h touch. 26.5 complaints/1k events."),
 ("6. Field operations",   "AGAINST",     "65.9 complaints per 1,000 events - 3.8x voice. Lowest PTP-kept rate (32.5%). Most expensive channel per touch."),
], columns=["option","evidence grade","what the data says"])
pd.set_option("display.max_colwidth", 120)
display(scorecard)
"""),

MD("""
### 6d. Recommendation

**Do not deploy ₹10 Cr against any of the six options on this evidence base.**

Deploy **₹1.2 Cr** to make the decision answerable, and gate the remaining **₹8.8 Cr** on a
pre-registered experimental result.

If forced to name one area it is **Option 4, better borrower targeting** — because it is the only
option where the data proves a *gap* rather than merely failing to disprove one. But note the
distinction carefully:

- We can prove the current targeting engine carries no signal. **Supported.**
- We cannot prove a better one would recover more, because recovery is uncorrelated with every
  operational lever we can measure. **Not supported.**

Committing ₹10 Cr to the second claim would repeat exactly the reasoning error that produced the
11% headline: treating an untested inference as a measured fact.
"""),

CO("""
NET = net_ann
print("Own-book model (business keeps 100% of recovered value):\\n")
print(f"{'scenario':<10}{'lift':>7}{'incr. recovery':>18}{'cost':>9}{'net':>10}{'ROI':>9}")
for lab, lift, c_ in [("Downside",0.00,10.0), ("Base",0.06,10.0), ("Upside",0.12,10.0)]:
    v = NET*lift/1e7
    print(f"{lab:<10}{lift:>6.0%}{v:>16.1f} Cr{c_:>7.1f} Cr{v-c_:>+8.1f} Cr{(v-c_)/c_*100:>+8.0f}%")
print(f"\\nBreak-even, full Rs 10 Cr    : +{10e7/NET:.1%} lift")
print(f"Break-even, Stage 1 (1.2 Cr) : +{1.2e7/NET:.1%} lift   <- the bet actually being made")
print(f"\\nIf the book is collected on a 20% agency fee instead, break-even becomes "
      f"+{10e7/0.20/NET:.0%} - not achievable by any lever in this data.")
"""),

MD("""
### Confidence

| Claim | Confidence | Why |
|---|---|---|
| Recovery is flat; the 11% is an artifact | **High** | Five independent definitions, all null; mechanism identified exactly (28 vs 31 days) |
| Current targeting carries no signal | **High** | 43.1% vs a 42.9% random baseline, stable across all 8 months |
| No operational lever works | **Medium** | These are null results on 30k accounts; effects below ~13% would be invisible |
| Every cost and ROI figure | **Low** | The dataset contains no cost data whatsoever |

### What we would need to answer this properly

1. **Cost feeds** — per call, per message, per visit, per agent-hour. Without these, "cost per ₹
   recovered" is unanswerable and half the metric set is decorative.
2. **An account-state service** where CLOSED means closed. The current status table is a random
   walk; 19,673 status changes occur after an account's supposed closure.
3. **A resolvable agent identity** — one person, one id, with history.
4. **A randomised holdout**, pre-registered, 8,500 accounts per arm, 120-day measurement window.
   It is a reallocation of existing capacity, not new spend, and it produces the causal number that
   ₹10 Cr is currently being allocated without.
"""),
]

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata = {"kernelspec": {"display_name":"Python 3","language":"python","name":"python3"},
               "language_info": {"name":"python","version":"3.11"}}
out = ROOT/"notebooks"/"analysis.ipynb"
NotebookClient(nb, timeout=900, kernel_name="python3", resources={"metadata":{"path":str(ROOT)}}).execute()
nbf.write(nb, str(out))
print("wrote", out)

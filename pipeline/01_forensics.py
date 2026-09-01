"""
01_forensics.py — Data forensics A-G from the brief.
Every check is a hypothesis test against the raw data. Nothing is assumed.
"""
import pandas as pd, numpy as np
from pathlib import Path
pd.set_option("display.width", 200); pd.set_option("display.max_columns", 50)

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT = Path(__file__).resolve().parents[1] / "outputs" / "reports"
OUT.mkdir(parents=True, exist_ok=True)
lines = []
def P(*a):
    s = " ".join(str(x) for x in a); print(s); lines.append(s)

def load(name):
    df = pd.read_csv(RAW / f"{name}.csv", low_memory=False)
    for c in df.columns:
        if c.endswith("_at") or c.endswith("_date"):
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df

pay   = load("payments");    acc = load("accounts");   brw = load("borrowers")
agt   = load("agents");      cal = load("calls");      att = load("call_attempts")
dis   = load("call_dispositions"); ven = load("vendor_telephony")
tgt   = load("daily_targeting");   cmp_ = load("campaigns")
ptp   = load("promises_to_pay");   ash = load("account_status_history")
ses   = load("agent_sessions"); wa = load("whatsapp_events"); sms = load("sms_events")
fv    = load("field_visits")

P("#"*80); P("# A. DUPLICATE PAYMENTS"); P("#"*80)
P(f"payments rows={len(pay):,}  unique payment_id={pay.payment_id.nunique():,}")
P(f"  exact duplicate rows (all cols): {pay.duplicated().sum():,}")
P(f"  duplicate payment_id (surrogate reused): {pay.payment_id.duplicated().sum():,}")

# same payment_id reused for genuinely different rows?
g = pay.groupby("payment_id").nunique()
P(f"  payment_ids where account_id differs across rows: {(g.account_id>1).sum():,}")
P(f"  payment_ids where amount differs across rows: {(g.amount>1).sum():,}")

# payment_reference re-use = the real duplicate-transaction signal
pr = pay.dropna(subset=["payment_reference"])
ref_counts = pr.payment_reference.value_counts()
P(f"\n  distinct payment_reference={pr.payment_reference.nunique():,} over {len(pr):,} rows")
P(f"  references used >1 time: {(ref_counts>1).sum():,}  (rows involved: {ref_counts[ref_counts>1].sum():,})")
multi = pr[pr.payment_reference.isin(ref_counts[ref_counts>1].index)]
gg = multi.groupby("payment_reference").agg(n=("payment_id","size"),
        n_acct=("account_id","nunique"), n_amt=("amount","nunique"),
        n_status=("payment_status","nunique"), n_pid=("payment_id","nunique"))
P(f"    of these: same account & same amount (true dup txn): {((gg.n_acct==1)&(gg.n_amt==1)).sum():,}")
P(f"    same ref but DIFFERENT account (id collision): {(gg.n_acct>1).sum():,}")
P(f"    same ref but different amount: {(gg.n_amt>1).sum():,}")
P("\n  sample duplicated references:")
for r in gg[(gg.n_acct==1)&(gg.n_amt==1)].head(3).index:
    P(multi[multi.payment_reference==r][["payment_id","account_id","event_at","payment_reference","amount","payment_status"]].to_string(index=False))

# inflation impact on SUCCESS value
succ = pay[pay.payment_status=="SUCCESS"]
P(f"\n  SUCCESS rows={len(succ):,}  gross value=Rs {succ.amount.sum()/1e7:,.2f} Cr")
succ_d = succ.drop_duplicates(subset=["payment_id"])
P(f"  after dedup on payment_id: {len(succ_d):,} rows, Rs {succ_d.amount.sum()/1e7:,.2f} Cr")
succ_d2 = succ_d.dropna(subset=["payment_reference"]).drop_duplicates(subset=["payment_reference"])
succ_d2 = pd.concat([succ_d2, succ_d[succ_d.payment_reference.isna()]])
P(f"  after dedup on payment_reference too: {len(succ_d2):,} rows, Rs {succ_d2.amount.sum()/1e7:,.2f} Cr")
P(f"  => overstatement from duplicates: Rs {(succ.amount.sum()-succ_d2.amount.sum())/1e7:,.2f} Cr "
  f"({1-succ_d2.amount.sum()/succ.amount.sum():.2%})")

# reversals / non-success treated as recovery?
P("\n  payment_status value & amount split:")
P(pay.groupby("payment_status").agg(n=("amount","size"), cr=("amount", lambda s: round(s.sum()/1e7,2))).to_string())
P("  NOTE: REVERSED represents money that came back OUT. Any 'recovery' metric that")
P("        counts REVERSED as collected, or fails to net it, is overstated.")

# does a reversal share a reference with a success?
rev = pay[pay.payment_status=="REVERSED"].dropna(subset=["payment_reference"])
shared = set(rev.payment_reference) & set(succ.payment_reference.dropna())
P(f"  REVERSED refs also appearing as SUCCESS: {len(shared):,}")

P("\n"+"#"*80); P("# B. ATTRIBUTION ERRORS"); P("#"*80)
# Naive attribution = last-touch, unbounded window. Test how much it moves.
pay_s = succ_d2.copy()
touch = []
for name, df, col in [("CALL", cal, "event_at"), ("WHATSAPP", wa, "event_at"),
                      ("SMS", sms, "event_at"), ("FIELD", fv, "event_at")]:
    t = df[["account_id", col]].dropna().copy(); t.columns = ["account_id","touch_at"]; t["channel"]=name
    touch.append(t)
touch = pd.concat(touch, ignore_index=True)
P(f"  total interaction touches: {len(touch):,}")

m = pay_s[["payment_id","account_id","event_at","amount"]].merge(touch, on="account_id", how="left")
m = m[m.touch_at <= m.event_at]
m["lag_h"] = (m.event_at - m.touch_at).dt.total_seconds()/3600
last = m.sort_values("lag_h").groupby("payment_id").first()
P(f"  payments with >=1 prior touch: {last.shape[0]:,} of {len(pay_s):,}")
P(f"  last-touch lag (hours) percentiles: "
  f"p10={last.lag_h.quantile(.1):.0f} p50={last.lag_h.quantile(.5):.0f} "
  f"p90={last.lag_h.quantile(.9):.0f} max={last.lag_h.max():.0f}")
P("  share of last-touch credit by channel, UNBOUNDED window:")
P((last.channel.value_counts(normalize=True)).round(4).to_string())
for w in [24, 72, 168]:
    lw = m[m.lag_h <= w].sort_values("lag_h").groupby("payment_id").first()
    P(f"  ... with a {w}h window: n={len(lw):,} ({len(lw)/len(pay_s):.1%} of payments attributable)")
    P("      " + lw.channel.value_counts(normalize=True).round(3).to_dict().__str__())
P("  => Channel 'conversion' is almost entirely an artifact of window choice and of")
P("     how many touches a channel emits. High-volume channels win last-touch by construction.")

# campaign attribution: does a payment get credited to the latest campaign?
P("\n  Campaign attribution: calls carry campaign_id; payments do not.")
P("  Any payment->campaign link must be inferred, so 'campaign performance' in the")
P("  existing reporting is an inference, not a recorded fact.")

P("\n"+"#"*80); P("# C. TIMEZONE PROBLEMS"); P("#"*80)
P("  Tables carrying a per-row timezone label:")
for n, df in [("accounts",acc),("calls",cal),("agent_sessions",ses),("vendor_telephony",ven)]:
    if "timezone" in df.columns:
        P(f"    {n}: {df.timezone.value_counts().to_dict()}")
P("  event_at columns are timezone-NAIVE strings. calls.timezone says which wall clock they are in.")
cal2 = cal.drop_duplicates(subset=["call_id"]).copy()
P(f"\n  Naive (as-stored) hour-of-day distribution of calls, by stated timezone:")
h = cal2.assign(hr=cal2.event_at.dt.hour).pivot_table(index="hr", columns="timezone", values="call_id", aggfunc="size")
P((h/h.sum()).round(4).T.to_string())
OFF = {"UTC": 5.5, "Asia/Kolkata": 0.0, "Asia/Dubai": 1.5}   # hours to add to reach IST
cal2["event_ist"] = cal2.event_at + pd.to_timedelta(cal2.timezone.map(OFF), unit="h")
naive_night = ((cal2.event_at.dt.hour < 8) | (cal2.event_at.dt.hour >= 21)).mean()
ist_night   = ((cal2.event_ist.dt.hour < 8) | (cal2.event_ist.dt.hour >= 21)).mean()
P(f"\n  Share of calls landing outside 08:00-21:00 (RBI-relevant calling window):")
P(f"    using raw naive timestamps : {naive_night:.2%}")
P(f"    after converting to IST    : {ist_night:.2%}")
mis = (cal2.event_at.dt.date != cal2.event_ist.dt.date).mean()
P(f"  Calls whose CALENDAR DAY changes after TZ normalisation: {mis:.2%}")
mm  = (cal2.event_at.dt.to_period('M') != cal2.event_ist.dt.to_period('M')).sum()
P(f"  Calls whose CALENDAR MONTH changes after TZ normalisation: {mm:,}")
P("  => Any 'best time to call' or daily-volume analysis on raw timestamps is wrong.")

# event ordering violations
P("\n  Chronology violations:")
P(f"    borrowers with updated_at < created_at: {(brw.updated_at < brw.created_at).sum():,} of {len(brw):,}")
P(f"    field_visits with event_at < scheduled_at: {(fv.event_at < fv.scheduled_at).sum():,} "
  f"(i.e. visit logged before it was scheduled)")
P(f"    account_status_history with recorded_at < event_at (late/early arriving): "
  f"{(ash.recorded_at < ash.event_at).sum():,} / recorded_at > event_at: {(ash.recorded_at > ash.event_at).sum():,}")
lag = (ash.recorded_at - ash.event_at).dt.total_seconds()/3600
P(f"    ingestion lag hours: p50={lag.median():.1f} p95={lag.quantile(.95):.1f} min={lag.min():.1f} max={lag.max():.1f}")
P(f"    complaints resolved before raised: {(ash.recorded_at.isna()).sum():,} nulls")
# call vs attempt/disposition ordering
j = att.merge(cal2[["call_id","event_at"]].rename(columns={"event_at":"call_at"}), on="call_id", how="left")
P(f"    call_attempts occurring BEFORE their parent call: {(j.event_at < j.call_at).sum():,} of {len(j):,}")
jd = dis.merge(cal2[["call_id","event_at"]].rename(columns={"event_at":"call_at"}), on="call_id", how="left")
P(f"    dispositions logged BEFORE their parent call: {(jd.event_at < jd.call_at).sum():,} of {len(jd):,}")
P("  => calls / call_attempts / call_dispositions are NOT a consistent parent-child hierarchy.")

P("\n"+"#"*80); P("# D. VENDOR MAPPING & DISPOSITION CODE CHANGES"); P("#"*80)
P(ven.to_string(index=False))
P(f"\n  {ven.vendor_id.nunique()} vendor_ids map to only {ven.vendor_name.nunique()} real vendors.")
P("  vendor_name -> vendor_ids:")
for k, v in ven.groupby("vendor_name").vendor_id.apply(list).items():
    P(f"    {k}: {v}")
P("  => Vendor-level performance reported by vendor_id splits one vendor across up to 5 rows.")

P("\n  Disposition code taxonomy:")
P(pd.crosstab(dis.disposition_code, dis.disposition_version).to_string())
P("\n  disposition_version share by month (did the taxonomy migrate?):")
dm = dis.assign(m=dis.event_at.dt.to_period("M"))
P((pd.crosstab(dm.m, dm.disposition_version, normalize="index")*100).round(1).to_string())
P("\n  disposition_code share by month (%):")
P((pd.crosstab(dm.m, dm.disposition_code, normalize="index")*100).round(1).to_string())
P("  NOTE: 'PTP' and 'PROMISE_TO_PAY' are the same business outcome under two codes.")
P("        A PTP-rate metric matching only one string undercounts by ~half.")

P("\n"+"#"*80); P("# E. AGENT IDENTITY"); P("#"*80)
P(f"  agents rows={len(agt):,}  distinct agent_id={agt.agent_id.nunique():,}  "
  f"distinct employee_code={agt.employee_code.nunique():,}")
by_emp = agt.drop_duplicates(["agent_id","employee_code"]).groupby("employee_code").agent_id.nunique()
P(f"  employee_codes mapping to >1 agent_id: {(by_emp>1).sum():,} "
  f"(max ids for one person: {by_emp.max()})")
by_id = agt.drop_duplicates(["agent_id","employee_code"]).groupby("agent_id").employee_code.nunique()
P(f"  agent_ids mapping to >1 employee_code: {(by_id>1).sum():,}")
P("  distribution of agent_ids per employee_code:")
P(by_emp.value_counts().sort_index().to_string())
# do the duplicate rows per agent_id conflict?
conf = agt.groupby("agent_id").nunique()
P(f"\n  agent_ids whose rows disagree on team: {(conf.team>1).sum():,}  "
  f"on status: {(conf.status>1).sum():,}  on vendor_id: {(conf.vendor_id>1).sum():,}  "
  f"on employee_code: {(conf.employee_code>1).sum():,}")
P("  => agents is an overwritten SCD-1 table dumped with history. Latest updated_at wins.")
# how much does agent-level performance move under resolution?
calls_per_id = cal2.groupby("agent_id").size()
emp_map = (agt.sort_values("updated_at").drop_duplicates("agent_id", keep="last")
             .set_index("agent_id").employee_code)
calls_per_person = cal2.assign(emp=cal2.agent_id.map(emp_map)).groupby("emp").size()
P(f"  calls per agent_id: mean={calls_per_id.mean():.0f} p95={calls_per_id.quantile(.95):.0f}")
P(f"  calls per resolved PERSON: mean={calls_per_person.mean():.0f} p95={calls_per_person.quantile(.95):.0f}")
P(f"  => per-agent productivity is understated by ~{calls_per_person.mean()/calls_per_id.mean()-1:.0%} "
  f"when measured on raw agent_id.")

P("\n"+"#"*80); P("# F. PORTFOLIO MIX"); P("#"*80)
P("  accounts.dpd / status / outstanding_amount are CURRENT snapshots (no as-of date).")
P(f"  accounts.opened_at range: {acc.opened_at.min()} .. {acc.opened_at.max()}")
P(f"  dpd distribution: {acc.dpd.describe().round(1).to_dict()}")
P(f"  dpd unique values: {sorted(acc.dpd.unique())[:20]}")
P("\n  Mix of ACCOUNTS TARGETED per month (daily_targeting joined to accounts):")
t = tgt.merge(acc[["account_id","risk_segment","dpd","loan_type","status"]], on="account_id", how="left")
t["m"] = t.target_date.dt.to_period("M")
P((pd.crosstab(t.m, t.risk_segment, normalize="index")*100).round(1).to_string())
P("\n  Mix of dpd band among targeted accounts per month:")
t["dpd_band"] = pd.cut(t.dpd, [-1,0,30,60,90,10**6], labels=["0","1-30","31-60","61-90","90+"])
P((pd.crosstab(t.m, t.dpd_band, normalize="index")*100).round(1).to_string())
P("\n  Mix of ACCOUNTS CALLED per month:")
c = cal2.merge(acc[["account_id","risk_segment","dpd"]], on="account_id", how="left")
c["m"] = c.event_at.dt.to_period("M")
P((pd.crosstab(c.m, c.risk_segment, normalize="index")*100).round(1).to_string())
P("\n  Campaign target_definition mix by month (calls joined to campaigns):")
cc = cal2.merge(cmp_[["campaign_id","target_definition","strategy_version","channel","campaign_name"]],
                on="campaign_id", how="left")
cc["m"] = cc.event_at.dt.to_period("M")
P((pd.crosstab(cc.m, cc.target_definition, normalize="index")*100).round(1).to_string())
P("\n  strategy_version mix by month:")
P((pd.crosstab(cc.m, cc.strategy_version, normalize="index")*100).round(1).to_string())

P("\n  Campaign definition consistency (same name, different channel/target?):")
cn = cmp_.groupby("campaign_name").agg(n=("campaign_id","size"), ch=("channel","nunique"),
        td=("target_definition","nunique"), sv=("strategy_version","nunique"))
P(cn.to_string())
P("  => campaign_name is NOT a stable definition. Grouping performance by name mixes")
P("     different channels and different target populations under one label.")

P("\n"+"#"*80); P("# G. DENOMINATOR MANIPULATION / SURVIVORSHIP"); P("#"*80)
P("  daily_targeting.status by month (%):")
P((pd.crosstab(t.m, t.status_x, normalize="index")*100).round(1).to_string())
P(f"\n  targeted accounts per month:")
P(t.groupby("m").account_id.nunique().to_string())
P("\n  Accounts that leave the population: account_status_history terminal statuses per month")
ash2 = ash.assign(m=ash.event_at.dt.to_period("M"))
P((pd.crosstab(ash2.m, ash2.status)).to_string())
P("\n  Do CLOSED/WRITEOFF accounts still get targeted after closure?")
term = ash[ash.status.isin(["CLOSED","WRITEOFF"])].groupby("account_id").event_at.min()
tt = tgt.assign(term=tgt.account_id.map(term))
P(f"    targeting rows after account closure: {(tt.target_date > tt.term).sum():,} of {len(tt):,}")
P("\n  Accounts present in payments but never in daily_targeting (unattributable recovery):")
P(f"    {len(set(pay.account_id)-set(tgt.account_id)):,} accounts")
P("\n  Contact-rate denominator sensitivity:")
P(f"    distinct accounts called      : {cal2.account_id.nunique():,}")
P(f"    distinct accounts targeted    : {tgt.account_id.nunique():,}")
P(f"    total accounts in portfolio   : {acc.account_id.nunique():,}")
P("    Contact rate over 'called' vs 'targeted' vs 'portfolio' gives three different answers.")

(OUT/"forensics.txt").write_text("\n".join(lines))
print(f"\nWrote {OUT/'forensics.txt'}")

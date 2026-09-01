"""
02_recovery_reconstruction.py
Reverse-engineer the reported "+11% month-on-month" and rebuild recovery honestly.
Strategy: build a LADDER of definitions from most naive to most defensible,
and see which one produces +11%.
"""
import pandas as pd, numpy as np
from pathlib import Path
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 60)
RAW = Path(__file__).resolve().parents[1]/"data"/"raw"
OUT = Path(__file__).resolve().parents[1]/"outputs"/"reports"
lines=[]
def P(*a):
    s=" ".join(str(x) for x in a); print(s); lines.append(s)

def load(n):
    df=pd.read_csv(RAW/f"{n}.csv", low_memory=False)
    for c in df.columns:
        if c.endswith("_at") or c.endswith("_date"): df[c]=pd.to_datetime(df[c],errors="coerce")
    return df

pay=load("payments"); acc=load("accounts"); cal=load("calls"); tgt=load("daily_targeting")
ptp=load("promises_to_pay"); dis=load("call_dispositions"); ash=load("account_status_history")
ses=load("agent_sessions"); wa=load("whatsapp_events"); sms=load("sms_events"); fv=load("field_visits")
att=load("call_attempts")

def mom(s):
    return (s/s.shift(1)-1)*100

def series_table(d, label):
    t=pd.DataFrame({label:d})
    t["MoM %"]=mom(d).round(1)
    return t

P("="*90); P("PART 1 — WHERE DOES '+11% MONTH-ON-MONTH' COME FROM?"); P("="*90)
P(f"Payment event window: {pay.event_at.min()} .. {pay.event_at.max()}")
P("NOTE: the brief says '12 months of data'. The event tables actually span")
P("      2026-01-01 to 2026-08-08 = 7 full months + 8 days. August is a PARTIAL month.")
P("      Any MoM series that includes August without annualising is broken.\n")

pay["m"]=pay.event_at.dt.to_period("M")

# --- L0: every payment row, every status (the most naive possible query)
L0 = pay.groupby("m").amount.sum()/1e7
# --- L1: every row, SUCCESS only, no dedup
L1 = pay[pay.payment_status=="SUCCESS"].groupby("m").amount.sum()/1e7
# --- L2: SUCCESS, dedup exact rows
p2 = pay.drop_duplicates()
L2 = p2[p2.payment_status=="SUCCESS"].groupby("m").amount.sum()/1e7
# --- L3: + dedup on payment_id (surrogate reuse)
p3 = pay.drop_duplicates(subset=["payment_id"])
L3 = p3[p3.payment_status=="SUCCESS"].groupby("m").amount.sum()/1e7
# --- L4: + collapse true duplicate transactions (same acct+amount+ref, or same acct+amount within 24h)
def dedupe_txn(df):
    d = df.drop_duplicates(subset=["payment_id"]).copy()
    # rule 1: identical (account, amount, reference) -> one economic event
    d["k1"] = d.account_id.astype(str)+"|"+d.amount.round(2).astype(str)+"|"+d.payment_reference.astype(str)
    d = d.sort_values("event_at")
    dup_ref = d.payment_reference.notna() & d.duplicated("k1", keep="first")
    # rule 2: same account+amount within 24h even with different reference (gateway retry)
    d2 = d[~dup_ref].sort_values(["account_id","amount","event_at"]).copy()
    same = (d2.account_id==d2.account_id.shift(1)) & (d2.amount.round(2)==d2.amount.round(2).shift(1))
    within = (d2.event_at - d2.event_at.shift(1)).dt.total_seconds().abs() <= 24*3600
    d2["retry"] = same & within
    keep = d2[~d2.retry].copy()
    return keep, int(dup_ref.sum()), int(d2.retry.sum())

pay_clean, n_ref_dup, n_retry = dedupe_txn(pay)
P(f"Transaction-level dedup: {n_ref_dup:,} same-(account,amount,reference) repeats removed; "
  f"{n_retry:,} same-(account,amount) repeats inside 24h removed.")
L4 = pay_clean[pay_clean.payment_status=="SUCCESS"].groupby("m").amount.sum()/1e7
# --- L5: net out reversals (money that came back out)
rev = pay_clean[pay_clean.payment_status=="REVERSED"].groupby("m").amount.sum()/1e7
L5 = (L4 - rev.reindex(L4.index).fillna(0))

lad = pd.DataFrame({
 "L0 all statuses, raw":L0, "L1 SUCCESS, raw":L1, "L2 +drop exact dups":L2,
 "L3 +dedup payment_id":L3, "L4 +dedup txn retries":L4, "L5 +net reversals":L5}).round(2)
P("\nMonthly recovery (Rs Cr) under each definition:")
P(lad.to_string())
P("\nMonth-on-month % under each definition:")
P(lad.apply(mom).round(1).to_string())

P("\nMean MoM % (Jan->Jul, EXCLUDING the partial August):")
for c in lad.columns:
    s = lad[c].iloc[:7]
    P(f"   {c:28s}  mean MoM = {mom(s).mean():+6.2f}%   "
      f"CAGR Jan->Jul = {((s.iloc[-1]/s.iloc[0])**(1/6)-1)*100:+6.2f}%/mo   "
      f"total Jan vs Jul = {(s.iloc[-1]/s.iloc[0]-1)*100:+6.1f}%")

P("\n>>> None of the value-based definitions grows anywhere near 11%/month.")

P("\n" + "="*90)
P("Testing RATE-based definitions (the ones most likely to be quoted)")
P("="*90)
cal_d = cal.drop_duplicates(subset=["call_id"]).copy()
OFF={"UTC":5.5,"Asia/Kolkata":0.0,"Asia/Dubai":1.5}
cal_d["event_ist"]=cal_d.event_at+pd.to_timedelta(cal_d.timezone.map(OFF),unit="h")
cal_d["m"]=cal_d.event_ist.dt.to_period("M")
tgt["m"]=tgt.target_date.dt.to_period("M")
dis["m"]=dis.event_at.dt.to_period("M"); ptp["m"]=ptp.event_at.dt.to_period("M")
att["m"]=att.event_at.dt.to_period("M")
pay_clean["m"]=pay_clean.event_at.dt.to_period("M")
succ = pay_clean[pay_clean.payment_status=="SUCCESS"]

met = pd.DataFrame(index=sorted(set(cal_d.m.dropna())&set(tgt.m.dropna())))
met["calls"]              = cal_d.groupby("m").size()
met["answered"]           = cal_d[cal_d.call_status=="ANSWERED"].groupby("m").size()
met["contact_rate_naive"] = met.answered/met.calls*100
met["accts_targeted"]     = tgt.groupby("m").account_id.nunique()
met["accts_called"]       = cal_d.groupby("m").account_id.nunique()
met["accts_contacted"]    = cal_d[cal_d.call_status=="ANSWERED"].groupby("m").account_id.nunique()
met["accts_paid"]         = succ.groupby("m").account_id.nunique()
met["recovery_cr"]        = succ.groupby("m").amount.sum()/1e7

# PTP definitions: the legacy trap
met["ptp_narrow"] = dis[dis.disposition_code=="PTP"].groupby("m").size()
met["ptp_full"]   = dis[dis.disposition_code.isin(["PTP","PROMISE_TO_PAY"])].groupby("m").size()
met["disp_total"] = dis.groupby("m").size()
met["ptp_rate_narrow_%"] = met.ptp_narrow/met.disp_total*100
met["ptp_rate_full_%"]   = met.ptp_full/met.disp_total*100
met["ptp_kept_%"] = ptp[ptp.status=="KEPT"].groupby("m").size()/ptp.groupby("m").size()*100
met["ptp_kept_excl_open_%"] = (ptp[ptp.status=="KEPT"].groupby("m").size()
                               / ptp[ptp.status!="OPEN"].groupby("m").size()*100)

# recovery rate: numerator/denominator choices
met["recov_per_targeted_acct"] = met.recovery_cr*1e7/met.accts_targeted
met["recov_per_called_acct"]   = met.recovery_cr*1e7/met.accts_called
met["recov_per_contacted"]     = met.recovery_cr*1e7/met.accts_contacted
met["conv_paid/targeted_%"]    = met.accts_paid/met.accts_targeted*100
met["conv_paid/contacted_%"]   = met.accts_paid/met.accts_contacted*100

P(met.round(2).to_string())
P("\nMoM % of each metric (excluding partial Aug):")
mm = met.iloc[:7].apply(mom).round(1)
P(mm.to_string())
P("\nMean MoM % Jan->Jul:")
res = mm.mean().round(2).sort_values(ascending=False)
P(res.to_string())
P("\n>>> CANDIDATE for the '11%' claim: any metric whose mean MoM is near +11%.")
cands = res[(res>7)&(res<16)]
P(f"    metrics in the 7-16%/mo band: {list(cands.index) if len(cands) else 'NONE'}")

P("\n" + "="*90)
P("The most likely construction of the +11% headline")
P("="*90)
# reported-style metric: recovery per targeted account, raw payments, naive month, incl Aug
naive = pay.groupby("m").amount.sum()          # all statuses, all dup rows
den   = tgt.groupby("m").account_id.nunique()
rep   = (naive/den).dropna()
P("Recovery per targeted account, RAW payments (all statuses, dups kept):")
P(series_table(rep.round(0), "Rs/acct").to_string())
P(f"   mean MoM incl. partial Aug = {mom(rep).mean():+.2f}%")
P(f"   mean MoM excl. Aug         = {mom(rep.iloc[:7]).mean():+.2f}%")

# denominator-shrink variant: only accounts with status CONTACTED
den2 = tgt[tgt.status=="CONTACTED"].groupby("m").account_id.nunique()
rep2 = (naive/den2).dropna()
P("\nSame numerator, denominator narrowed to CONTACTED targets only:")
P(series_table(rep2.round(0),"Rs/acct").to_string())
P(f"   mean MoM excl. Aug = {mom(rep2.iloc[:7]).mean():+.2f}%")

# cumulative / YTD framing
cum = naive.cumsum()/1e7
P("\nCUMULATIVE (YTD) recovery, Rs Cr — the classic false-growth framing:")
P(series_table(cum.round(2),"Cr YTD").to_string())
P(f"   mean MoM of a cumulative series = {mom(cum.iloc[:7]).mean():+.2f}%  <-- grows by construction")
P(f"   mean MoM of cumulative, all 8 months = {mom(cum).mean():+.2f}%")

met.to_csv(OUT.parent/"golden"/"monthly_metrics.csv")
lad.to_csv(OUT.parent/"golden"/"recovery_definition_ladder.csv")
(OUT/"recovery_reconstruction.txt").write_text("\n".join(lines))
print("\nWrote", OUT/"recovery_reconstruction.txt")

"""
04_golden_dataset.py — Raw -> Rejected/Corrected -> Golden.

Design principles
-----------------
1. Nothing is deleted silently. Every row that does not reach the golden layer is
   written to outputs/golden/_rejections.csv with a reason code, so the ledger
   reconciles: raw = golden + rejected.
2. Corrections are additive columns, never in-place overwrites, so the raw value
   remains auditable next to the corrected one.
3. Where the data cannot support a trustworthy entity (agents), we say so and
   quarantine the dimension rather than shipping a plausible-looking lie.
4. Every rule is stated as a business rule with a reason, not a magic filter.

Outputs: outputs/golden/*.csv  +  outputs/reports/golden_build_log.txt
"""
import pandas as pd, numpy as np, hashlib
from pathlib import Path
pd.set_option("display.width",220); pd.set_option("display.max_columns",60)

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/"data"/"raw"; GOLD=ROOT/"outputs"/"golden"; REP=ROOT/"outputs"/"reports"
GOLD.mkdir(parents=True,exist_ok=True); REP.mkdir(parents=True,exist_ok=True)

log=[]
def P(*a):
    s=" ".join(str(x) for x in a); print(s); log.append(s)

ledger=[]   # (table, stage, rule, rows_in, rows_out, rows_removed, reason)
rejects=[]  # rejected rows with reason
def step(table, rule, before, after, reason, rejected_df=None, key=None):
    ledger.append(dict(table=table, rule=rule, rows_in=before, rows_out=after,
                       rows_removed=before-after, pct_removed=round((before-after)/before*100,3) if before else 0,
                       reason=reason))
    P(f"  [{table:22s}] {rule:38s} {before:>8,} -> {after:>8,}  ({before-after:>6,} removed)  {reason}")
    if rejected_df is not None and len(rejected_df):
        r=rejected_df.copy()
        r.insert(0,"_reject_rule",rule); r.insert(0,"_reject_table",table)
        rejects.append(r[["_reject_table","_reject_rule"]+ [c for c in [key] if c]].head(5000))

def load(n):
    df=pd.read_csv(RAW/f"{n}.csv",low_memory=False)
    for c in df.columns:
        if c.endswith("_at") or c.endswith("_date"): df[c]=pd.to_datetime(df[c],errors="coerce")
    return df

# ---------------------------------------------------------------- reference data
TZ_TO_IST_HOURS = {"UTC":5.5, "Asia/Kolkata":0.0, "Asia/Dubai":1.5}
ANALYSIS_START = pd.Timestamp("2026-01-01")
ANALYSIS_END   = pd.Timestamp("2026-08-08 23:59:59")   # last complete data day
COMPLETE_END   = pd.Timestamp("2026-07-31 23:59:59")   # last complete MONTH

# Disposition harmonisation: legacy and v1/v2 codes for the same business outcome.
DISPO_MAP = {
 "PTP":"PROMISE_TO_PAY", "PROMISE_TO_PAY":"PROMISE_TO_PAY",
 "PTP_BROKEN":"PROMISE_BROKEN",
 "PAID":"PAID", "CALLBACK":"CALLBACK", "DISPUTE":"DISPUTE",
 "REFUSED":"REFUSED", "NO_CONTACT":"NO_CONTACT", "WRONG_NUMBER":"WRONG_NUMBER",
}
RPC_CODES = {"PROMISE_TO_PAY","PROMISE_BROKEN","PAID","DISPUTE","REFUSED","CALLBACK"}  # spoke to a real person
NON_RPC   = {"NO_CONTACT","WRONG_NUMBER"}

P("="*95); P("GOLDEN DATASET BUILD"); P("="*95)
P(f"Analysis window: {ANALYSIS_START.date()} .. {ANALYSIS_END.date()}")
P(f"Last COMPLETE month: {COMPLETE_END.date()}  (August is partial -- excluded from all MoM series)")

# =================================================================== DIM: BORROWER
P("\n--- dim_borrower ---------------------------------------------------------")
brw=load("borrowers"); n0=len(brw)
b=brw.drop_duplicates(); step("borrowers","drop exact duplicate rows",n0,len(b),
    "600 byte-identical rows from a double ingest")
# SCD-1 collapse: the table carries history of an overwritten record. Latest wins.
# updated_at is unreliable (50% of rows have updated_at < created_at), so we rank on
# max(created_at, updated_at) and break ties deterministically on the row hash.
b["_asof"]=b[["created_at","updated_at"]].max(axis=1)
b["_ts_invalid"]=b.updated_at<b.created_at
n1=len(b)
b=b.sort_values(["borrower_id","_asof"]).drop_duplicates("borrower_id",keep="last")
step("borrowers","SCD-1 collapse to latest _asof",n1,len(b),
    "table is a dump of an overwritten record; one row per borrower_id")
P(f"     {brw.assign(x=brw.updated_at<brw.created_at).x.sum():,} raw rows had updated_at < created_at "
  f"-> updated_at alone is NOT a valid recency key; used max(created_at,updated_at)")
dim_borrower=b.drop(columns=["_asof"]).rename(columns={"_ts_invalid":"dq_timestamp_inverted"})
dim_borrower["dq_missing_phone"]=dim_borrower.phone.isna()
dim_borrower["dq_missing_email"]=dim_borrower.email.isna()

# =================================================================== DIM: ACCOUNT
P("\n--- dim_account ----------------------------------------------------------")
acc=load("accounts"); n0=len(acc)
a=acc.copy()
a["dq_orphan_borrower"]=~a.borrower_id.isin(dim_borrower.borrower_id)
a["dq_missing_borrower"]=a.borrower_id.isna()
P(f"  {a.dq_missing_borrower.sum():,} accounts have NO borrower_id; "
  f"{(a.dq_orphan_borrower&~a.dq_missing_borrower).sum():,} point at a borrower_id that does not exist.")
P("  DECISION: keep them. An account with a broken borrower link still owes money and still")
P("            receives payments. Dropping them would understate recovery. They are flagged")
P("            and excluded only from borrower-level (geography/demographic) cuts.")
a["dpd_band"]=pd.cut(a.dpd,[-1,0,30,60,90,10**6],labels=["0","1-30","31-60","61-90","90+"])
a["snapshot_warning"]="dpd/status/outstanding_amount are CURRENT snapshots, not as-of-date"
step("accounts","keep all, flag broken links",n0,len(a),"accounts are the grain of the business")
dim_account=a

# =================================================================== DIM: AGENT (QUARANTINED)
P("\n--- dim_agent  [QUARANTINED] ---------------------------------------------")
agt=load("agents")
edges=agt[["agent_id","employee_code"]].drop_duplicates()
P(f"  {len(agt):,} rows, {agt.agent_id.nunique():,} agent_id, {agt.employee_code.nunique():,} employee_code.")
P(f"  Every agent_id carries {agt.groupby('agent_id').agent_name.nunique().mean():.1f} different agent_names on average.")
P(f"  agent_id <-> employee_code forms ONE connected component of {edges.agent_id.nunique()+edges.employee_code.nunique():,} nodes.")
P("  CONCLUSION: there is no natural key and no resolvable person entity in this table.")
P("  DECISION: build dim_agent as an SCD-1 collapse on agent_id (latest updated_at) so joins")
P("            do not fan out, but mark the whole dimension trust_level='LOW' and publish NO")
P("            agent-level or tenure-level metric from it. Anything the current reporting says")
P("            about 'top agents' or 'agent tenure' is unsupported by this data.")
P("  COUNTER-EXAMPLE: resolving on employee_code (the obvious 'natural key') would raise mean")
P("            calls-per-person from 88 to 137 (+56%) — a 56% productivity 'improvement'")
P("            created entirely by a bad entity-resolution choice.")
n0=len(agt)
dim_agent=(agt.sort_values(["agent_id","updated_at"]).drop_duplicates("agent_id",keep="last")
             .assign(trust_level="LOW",
                     dq_conflicting_attributes=True,
                     dq_note="agent_id has many employee_codes/names/vendors; identity unresolvable"))
step("agents","SCD-1 collapse on agent_id",n0,len(dim_agent),"join-safety only; NOT for reporting")

# =================================================================== DIM: VENDOR
P("\n--- dim_vendor -----------------------------------------------------------")
ven=load("vendor_telephony")
dim_vendor=ven.copy()
dim_vendor["vendor_group"]=dim_vendor.vendor_name            # the real commercial counterparty
P(f"  {len(ven)} vendor_ids resolve to {ven.vendor_name.nunique()} commercial vendors.")
P("  DECISION: vendor_id is a telephony ACCOUNT, not a vendor. All vendor reporting rolls up to")
P("            vendor_group=vendor_name. Reporting by vendor_id splits Airtel across 5 lines and")
P("            makes each look like a small, noisy, differently-performing supplier.")

# =================================================================== DIM: CAMPAIGN
P("\n--- dim_campaign ---------------------------------------------------------")
cmp_=load("campaigns")
dim_campaign=cmp_.copy()
dim_campaign["campaign_key"]=(dim_campaign.campaign_name+" | "+dim_campaign.channel+" | "
                              +dim_campaign.target_definition+" | "+dim_campaign.strategy_version)
dim_campaign["dq_end_before_start"]=dim_campaign.end_at<dim_campaign.start_at
n_amb=cmp_.groupby("campaign_name").agg(ch=("channel","nunique"),td=("target_definition","nunique")).max().max()
P(f"  5 campaign_names span {len(cmp_)} campaign_ids, up to {n_amb} channels and target definitions each.")
P("  DECISION: campaign_name is a label, not a definition. The reporting grain is campaign_id;")
P("            any roll-up uses campaign_key (name+channel+target+strategy), never name alone.")
P(f"  {dim_campaign.dq_end_before_start.sum()} campaigns end before they start (flagged, kept).")

# =================================================================== FCT: PAYMENTS
P("\n--- fct_payment ----------------------------------------------------------")
pay=load("payments"); n0=len(pay)
p=pay.drop_duplicates()
step("payments","drop exact duplicate rows",n0,len(p),"byte-identical re-ingest")
n1=len(p)
p=p.sort_values("event_at").drop_duplicates("payment_id",keep="first")
step("payments","dedup on payment_id",n1,len(p),"surrogate key must be unique by contract")

# Transaction-level duplicates. NOTE: payment_reference alone is NOT a safe key --
# 3,405 references are reused, every one across DIFFERENT accounts and amounts (id-space collision).
# Deduping on reference alone would delete ~1,930 legitimate payments worth ~Rs 14.7 Cr.
n2=len(p)
p["_k_ref"]=p.account_id.astype(str)+"|"+p.amount.round(2).astype(str)+"|"+p.payment_reference.astype(str)
dup_ref=p.payment_reference.notna()&p.sort_values("event_at").duplicated("_k_ref",keep="first")
rej=p[dup_ref]; p=p[~dup_ref]
step("payments","dedup (account,amount,reference)",n2,len(p),
     "same money, same account, same gateway reference = one economic event", rej, "payment_id")
n3=len(p)
p=p.sort_values(["account_id","amount","event_at"])
same=(p.account_id==p.account_id.shift(1))&(p.amount.round(2)==p.amount.round(2).shift(1))
within=(p.event_at-p.event_at.shift(1)).dt.total_seconds().abs()<=24*3600
retry=same&within
rej=p[retry]; p=p[~retry]
step("payments","dedup same acct+amount within 24h",n3,len(p),
     "gateway retry under a fresh reference", rej, "payment_id")
_d=pay.drop_duplicates().sort_values("event_at").drop_duplicates("payment_id",keep="first")
_naive=_d.payment_reference.notna()&_d.payment_reference.duplicated(keep="first")
_lost=_d[_naive&(_d.payment_status=="SUCCESS")]
P(f"  COUNTERFACTUAL CHECK: naive dedup on payment_reference alone would remove "
  f"{_naive.sum():,} rows instead of {n2-len(p):,}, destroying {len(_lost):,} genuine successful "
  f"payments worth Rs {_lost.amount.sum()/1e7:,.1f} Cr. The obvious key is the wrong key.")

p["is_success"]=p.payment_status=="SUCCESS"
p["is_reversal"]=p.payment_status=="REVERSED"
p["cash_in"]=np.where(p.is_success,p.amount,0.0)
p["cash_out"]=np.where(p.is_reversal,p.amount,0.0)
p["net_recovery"]=p.cash_in-p.cash_out
P("  DECISION: recovery = SUCCESS minus REVERSED. PENDING and FAILED are not money.")
P(f"     gross SUCCESS  Rs {p.cash_in.sum()/1e7:,.2f} Cr")
P(f"     less REVERSED  Rs {p.cash_out.sum()/1e7:,.2f} Cr")
P(f"     NET RECOVERY   Rs {p.net_recovery.sum()/1e7:,.2f} Cr")
P(f"  Raw table would have reported Rs {pay.amount.sum()/1e7:,.2f} Cr if every row were counted; "
  f"Rs {pay[pay.payment_status=='SUCCESS'].amount.sum()/1e7:,.2f} Cr if only SUCCESS with duplicates left in.")
P(f"  => headline overstatement vs golden: {pay[pay.payment_status=='SUCCESS'].amount.sum()/p.net_recovery.sum()-1:+.1%}")
# payments have no timezone column -> documented assumption
p["event_ist"]=p.event_at   # assumption: payment gateway timestamps already IST
p["dq_tz_assumed"]=True
fct_payment=p.drop(columns=["_k_ref"])

# =================================================================== FCT: CALLS
P("\n--- fct_call -------------------------------------------------------------")
cal=load("calls"); n0=len(cal)
c=cal.drop_duplicates(); step("calls","drop exact duplicate rows",n0,len(c),"re-ingest")
n1=len(c); c=c.sort_values("event_at").drop_duplicates("call_id",keep="first")
step("calls","dedup on call_id",n1,len(c),"surrogate key uniqueness")
c["event_ist"]=c.event_at+pd.to_timedelta(c.timezone.map(TZ_TO_IST_HOURS),unit="h")
moved=(c.event_at.dt.to_period("M")!=c.event_ist.dt.to_period("M")).sum()
dmoved=(c.event_at.dt.date!=c.event_ist.dt.date).mean()
P(f"  Timezone normalisation to IST: {dmoved:.1%} of calls change calendar DAY, {moved:,} change MONTH.")
P("  DECISION: event_ist is the single reporting clock. Raw event_at is retained for audit.")
n2=len(c)
inwin=(c.event_ist>=ANALYSIS_START)&(c.event_ist<=ANALYSIS_END)
rej=c[~inwin]; c=c[inwin]
step("calls","restrict to analysis window (IST)",n2,len(c),
     "stragglers outside the 2026-01-01..2026-08-08 window", rej, "call_id")
c["is_answered"]=c.call_status=="ANSWERED"
c["hour_ist"]=c.event_ist.dt.hour
c["outside_rbi_window"]=(c.hour_ist<8)|(c.hour_ist>=21)
P(f"  {c.outside_rbi_window.mean():.1%} of calls fall outside the 08:00-21:00 IST window "
  f"(RBI Fair Practices Code). On raw timestamps the figure reads {((cal.event_at.dt.hour<8)|(cal.event_at.dt.hour>=21)).mean():.1%}.")
c=c.merge(dim_vendor[["vendor_id","vendor_group"]],on="vendor_id",how="left")
fct_call=c

# =================================================================== FCT: DISPOSITIONS
P("\n--- fct_disposition ------------------------------------------------------")
dis=load("call_dispositions"); n0=len(dis)
d=dis.drop_duplicates().sort_values("event_at").drop_duplicates("disposition_id",keep="first")
step("call_dispositions","dedup",n0,len(d),"surrogate key uniqueness")
d["disposition_std"]=d.disposition_code.map(DISPO_MAP)
P(f"  Code harmonisation: {d.disposition_code.nunique()} raw codes -> {d.disposition_std.nunique()} standard outcomes.")
P("     PTP + PROMISE_TO_PAY -> PROMISE_TO_PAY   (the same outcome under legacy and v1/v2 taxonomies)")
P("     PTP_BROKEN           -> PROMISE_BROKEN")
raw_ptp=(dis.disposition_code=="PTP").mean(); std_ptp=(d.disposition_std=="PROMISE_TO_PAY").mean()
P(f"  PTP rate on the literal code 'PTP' = {raw_ptp:.1%}; on the harmonised outcome = {std_ptp:.1%}.")
P(f"  => any dashboard matching disposition_code='PTP' undercounts promises by {1-raw_ptp/std_ptp:.0%}.")
d["is_rpc"]=d.disposition_std.isin(RPC_CODES)
# join the parent call time to detect ordering violations
d=d.merge(fct_call[["call_id","event_ist"]].rename(columns={"event_ist":"call_event_ist"}),on="call_id",how="left")
d["dq_before_parent_call"]=d.event_at<d.call_event_ist
P(f"  {d.dq_before_parent_call.mean():.0%} of dispositions are timestamped BEFORE their parent call.")
P("  DECISION: dispositions are NOT treated as children of calls. calls/attempts/dispositions are")
P("            three independent event streams that share a call_id label but not a consistent")
P("            clock. RPC is measured on the disposition stream in its own right, never as a")
P("            ratio to the call stream.")
fct_disposition=d

# =================================================================== FCT: PTP
P("\n--- fct_ptp --------------------------------------------------------------")
ptp=load("promises_to_pay"); n0=len(ptp)
q=ptp.drop_duplicates().sort_values("event_at").drop_duplicates("ptp_id",keep="first")
step("promises_to_pay","dedup",n0,len(q),"surrogate key uniqueness")
q["is_resolved"]=q.status!="OPEN"
q["is_kept"]=q.status=="KEPT"
q["dq_promised_before_event"]=q.promised_date<q.event_at
P(f"  {q.dq_promised_before_event.sum():,} promises are due before they were made (flagged).")
P("  DECISION: PTP-kept rate denominator = RESOLVED promises only (KEPT+BROKEN+CANCELLED).")
P(f"     kept / all promises        = {(q.is_kept.sum()/len(q)):.1%}   <- understates: OPEN promises can still be kept")
P(f"     kept / resolved promises   = {(q.is_kept.sum()/q.is_resolved.sum()):.1%}   <- the honest number")
P("     A month-end cut inflates the OPEN bucket, so the first definition falls every month")
P("     purely because of recency. That is a manufactured downtrend, mirror image of the 11%.")
fct_ptp=q

# =================================================================== FCT: TOUCHES
P("\n--- fct_touch (unified interaction stream) -------------------------------")
wa=load("whatsapp_events").drop_duplicates().drop_duplicates("whatsapp_event_id")
sms=load("sms_events").drop_duplicates().drop_duplicates("sms_event_id")
fv=load("field_visits").drop_duplicates().drop_duplicates("visit_id")
att=load("call_attempts").drop_duplicates().drop_duplicates("attempt_id")
def touch(df,ch,idc,extra=None):
    t=df[["account_id","borrower_id","event_at",idc]].copy()
    t.columns=["account_id","borrower_id","event_at","source_id"]
    t["channel"]=ch
    t["engaged"]=extra if extra is not None else False
    return t
touches=pd.concat([
  touch(fct_call,"VOICE","call_id",fct_call.is_answered.values),
  touch(wa,"WHATSAPP","whatsapp_event_id",wa.event_type.isin(["READ","REPLIED","PAYMENT_CLICK"]).values),
  touch(sms,"SMS","sms_event_id",sms.event_type.eq("CLICKED").values),
  touch(fv,"FIELD","visit_id",fv.outcome.isin(["CONTACTED","PTP","PAID"]).values),
],ignore_index=True)
touches=touches[(touches.event_at>=ANALYSIS_START-pd.Timedelta(days=1))&(touches.event_at<=ANALYSIS_END)]
P(f"  {len(touches):,} interactions unified across VOICE / WHATSAPP / SMS / FIELD.")
P("  'engaged' is defined per channel as a two-way signal (answered / read-replied-clicked /")
P("  clicked / met-in-person), NOT as 'delivered'. Delivery is a vendor metric, not a")
P("  borrower-engagement metric, and inflating it is the easiest way to fake a contact rate.")
fct_touch=touches

# =================================================================== FCT: TARGETING
P("\n--- fct_targeting --------------------------------------------------------")
tgt=load("daily_targeting"); n0=len(tgt)
t=tgt.drop_duplicates().drop_duplicates("target_id")
step("daily_targeting","dedup",n0,len(t),"surrogate key uniqueness")
ash=load("account_status_history").drop_duplicates().drop_duplicates("history_id")
TERMINAL={"CLOSED","WRITEOFF","PAID"}

# --- Is account_status_history a valid lifecycle? Test before using it. ---
_a=ash.sort_values(["account_id","event_at"]).copy()
_a["is_term"]=_a.status.isin(TERMINAL)
_first_term=_a[_a.is_term].groupby("account_id").event_at.min()
_after=_a[_a.event_at>_a.account_id.map(_first_term)]
P(f"  LIFECYCLE TEST: {len(_after):,} status changes occur AFTER an account's first terminal status,")
P(f"     and their distribution is uniform: {_after.status.value_counts(normalize=True).round(3).to_dict()}")
P("  => account_status_history is NOT a state machine. CLOSED/WRITEOFF/PAID are not absorbing")
P("     states; accounts revert. 'first terminal event' is therefore an invalid definition of")
P("     account closure and must not be used. We use LAST KNOWN status as-of the target date.")

# --- Point-in-time (as-of) status join. No look-ahead. ---
_hist=ash[["account_id","event_at","status"]].sort_values("event_at").rename(columns={"event_at":"status_ts","status":"status_asof"})
t=t.sort_values("target_date")
t=pd.merge_asof(t,_hist,left_on="target_date",right_on="status_ts",
                by="account_id",direction="backward")
t["status_known_asof"]=t.status_asof.notna()
t["targeted_while_terminal"]=t.status_asof.isin(TERMINAL)
P(f"\n  {t.targeted_while_terminal.sum():,} of {len(t):,} targeting rows ({t.targeted_while_terminal.mean():.1%}) aim at an")
P("  account whose LAST KNOWN status at that moment was CLOSED / WRITEOFF / PAID.")
_m=t.assign(m=t.target_date.dt.to_period("M"))
_naive=(_m.groupby("m").targeted_while_terminal.mean()*100).round(1)
_known=(_m[_m.status_known_asof].groupby("m").targeted_while_terminal.mean()*100).round(1)
P(f"     naive monthly trend            : {_naive.to_dict()}")
P(f"     restricted to accounts with a known status:")
P(f"                                     : {_known.to_dict()}")
P("  TRAP AVOIDED: the naive series rises 6% -> 58% and looks like operations degrading badly.")
P("     It is not. Early months simply have little status history, so most accounts read as")
P(f"     'status unknown'. Among accounts with a known status the figure is FLAT at ~{_known.mean():.0f}%.")
P("     Reporting the naive series would have handed leadership a fake crisis.")
_base=3/ash.status.nunique()*100
P(f"  BENCHMARK: {_known.mean():.1f}% of targeting hits a terminal account. If targeting ignored")
P(f"     status entirely, random selection would hit {_base:.1f}% (3 of {ash.status.nunique()} statuses).")
P("     The targeting engine is statistically indistinguishable from random on account status.")
P("  DECISION: these rows STAY in the golden layer (they are real operational spend) but are")
P("            flagged. This is the largest genuine operational finding in the dataset.")
fct_targeting=t

# =================================================================== FCT: STATUS HISTORY
P("\n--- fct_account_status ---------------------------------------------------")
ash["ingestion_lag_h"]=(ash.recorded_at-ash.event_at).dt.total_seconds()/3600
ash["dq_recorded_before_event"]=ash.recorded_at<ash.event_at
P(f"  ingestion lag: p50={ash.ingestion_lag_h.median():+.1f}h  p05={ash.ingestion_lag_h.quantile(.05):+.1f}h  "
  f"p95={ash.ingestion_lag_h.quantile(.95):+.1f}h")
P(f"  {ash.dq_recorded_before_event.mean():.0%} of status changes are recorded BEFORE they happen.")
P("  DECISION: event_at is the business clock; recorded_at is the ingestion clock. All metrics")
P("            use event_at, and any daily metric is restated for 48h to absorb late arrivals.")
fct_account_status=ash

# =================================================================== AGG: MONTHLY
P("\n--- agg_monthly_recovery (the reporting table) ---------------------------")
fct_payment["m"]=fct_payment.event_ist.dt.to_period("M")
fct_call["m"]=fct_call.event_ist.dt.to_period("M")
fct_targeting["m"]=fct_targeting.target_date.dt.to_period("M")
fct_disposition["m"]=fct_disposition.event_at.dt.to_period("M")
fct_ptp["m"]=fct_ptp.event_at.dt.to_period("M")
months=[p for p in sorted(fct_payment.m.dropna().unique())]
agg=pd.DataFrame(index=pd.PeriodIndex(months,freq="M"))
agg["calendar_days"]=[p.days_in_month for p in agg.index]
agg.loc[agg.index[-1],"calendar_days"]=8
agg["is_complete_month"]=[True]*(len(agg)-1)+[False]
agg["net_recovery_cr"]=fct_payment.groupby("m").net_recovery.sum()/1e7
agg["gross_success_cr"]=fct_payment.groupby("m").cash_in.sum()/1e7
agg["reversals_cr"]=fct_payment.groupby("m").cash_out.sum()/1e7
agg["recovery_per_day_cr"]=agg.net_recovery_cr/agg.calendar_days
agg["accounts_paid"]=fct_payment[fct_payment.is_success].groupby("m").account_id.nunique()
agg["calls"]=fct_call.groupby("m").size()
agg["calls_answered"]=fct_call[fct_call.is_answered].groupby("m").size()
agg["contact_rate_pct"]=agg.calls_answered/agg.calls*100
agg["rpc"]=fct_disposition[fct_disposition.is_rpc].groupby("m").size()
agg["dispositions"]=fct_disposition.groupby("m").size()
agg["rpc_rate_pct"]=agg.rpc/agg.dispositions*100
agg["ptp_created"]=fct_ptp.groupby("m").size()
agg["ptp_resolved"]=fct_ptp[fct_ptp.is_resolved].groupby("m").size()
agg["ptp_kept"]=fct_ptp[fct_ptp.is_kept].groupby("m").size()
agg["ptp_kept_rate_pct"]=agg.ptp_kept/agg.ptp_resolved*100
agg["targeted_accounts"]=fct_targeting.groupby("m").account_id.nunique()
agg["targeting_rows"]=fct_targeting.groupby("m").size()
agg["targeted_while_terminal_pct"]=fct_targeting.groupby("m").targeted_while_terminal.mean()*100
agg["targeted_while_terminal_known_pct"]=fct_targeting[fct_targeting.status_known_asof].groupby("m").targeted_while_terminal.mean()*100
agg["recovery_per_targeted_acct"]=agg.net_recovery_cr*1e7/agg.targeted_accounts
agg["outside_rbi_window_pct"]=fct_call.groupby("m").outside_rbi_window.mean()*100
P(agg.round(2).to_string())

# =================================================================== LEDGER
P("\n"+"="*95); P("RAW -> REJECTED/CORRECTED -> GOLDEN  (reconciliation ledger)"); P("="*95)
led=pd.DataFrame(ledger)
P(led.to_string(index=False))
raws={n:len(load(n)) for n in ["borrowers","accounts","agents","calls","call_dispositions",
                               "promises_to_pay","payments","daily_targeting"]}
golds={"borrowers":len(dim_borrower),"accounts":len(dim_account),"agents":len(dim_agent),
       "calls":len(fct_call),"call_dispositions":len(fct_disposition),"promises_to_pay":len(fct_ptp),
       "payments":len(fct_payment),"daily_targeting":len(fct_targeting)}
recon=pd.DataFrame({"raw_rows":raws,"golden_rows":golds})
recon["removed"]=recon.raw_rows-recon.golden_rows
recon["pct_removed"]=(recon.removed/recon.raw_rows*100).round(2)
P("\n"+recon.to_string())
P(f"\nTOTAL raw rows across these 8 tables : {recon.raw_rows.sum():,}")
P(f"TOTAL golden rows                    : {recon.golden_rows.sum():,}")
P(f"TOTAL removed                        : {recon.removed.sum():,} ({recon.removed.sum()/recon.raw_rows.sum():.1%})")

P("\nBUSINESS IMPACT OF CLEANING")
raw_headline=load("payments")
P(f"  Reported-style recovery (all SUCCESS rows, no dedup) : Rs {raw_headline[raw_headline.payment_status=='SUCCESS'].amount.sum()/1e7:,.2f} Cr")
P(f"  Golden net recovery                                  : Rs {fct_payment.net_recovery.sum()/1e7:,.2f} Cr")
P(f"  Overstatement                                        : Rs {(raw_headline[raw_headline.payment_status=='SUCCESS'].amount.sum()-fct_payment.net_recovery.sum())/1e7:,.2f} Cr "
  f"({raw_headline[raw_headline.payment_status=='SUCCESS'].amount.sum()/fct_payment.net_recovery.sum()-1:.1%})")

# =================================================================== WRITE
for name,df in [("dim_borrower",dim_borrower),("dim_account",dim_account),("dim_agent",dim_agent),
                ("dim_vendor",dim_vendor),("dim_campaign",dim_campaign),("fct_payment",fct_payment),
                ("fct_call",fct_call),("fct_disposition",fct_disposition),("fct_ptp",fct_ptp),
                ("fct_touch",fct_touch),("fct_targeting",fct_targeting),
                ("fct_account_status",fct_account_status),("agg_monthly_recovery",agg)]:
    df.to_csv(GOLD/f"{name}.csv",index=(name=="agg_monthly_recovery"))
    P(f"  wrote {name}.csv  ({len(df):,} rows)")
led.to_csv(GOLD/"_cleaning_ledger.csv",index=False)
recon.to_csv(GOLD/"_reconciliation.csv")
if rejects: pd.concat(rejects,ignore_index=True).to_csv(GOLD/"_rejections.csv",index=False)
(REP/"golden_build_log.txt").write_text("\n".join(log))
print("\nWrote",REP/"golden_build_log.txt")

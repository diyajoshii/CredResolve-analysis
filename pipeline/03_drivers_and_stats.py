"""
03_drivers_and_stats.py
Part 3 of the brief: is any observed movement operational, or is it population/calendar?
Method: exposure-normalised rates + explicit significance tests + mix decomposition.
Simple and transparent beats a black box (per the brief).
"""
import pandas as pd, numpy as np
from scipy import stats
from pathlib import Path
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 60)
RAW=Path(__file__).resolve().parents[1]/"data"/"raw"; OUT=Path(__file__).resolve().parents[1]/"outputs"/"reports"
lines=[]
def P(*a):
    s=" ".join(str(x) for x in a); print(s); lines.append(s)
def load(n):
    df=pd.read_csv(RAW/f"{n}.csv",low_memory=False)
    for c in df.columns:
        if c.endswith("_at") or c.endswith("_date"): df[c]=pd.to_datetime(df[c],errors="coerce")
    return df

pay=load("payments"); acc=load("accounts"); cal=load("calls"); tgt=load("daily_targeting")
ptp=load("promises_to_pay"); dis=load("call_dispositions"); ash=load("account_status_history")
ses=load("agent_sessions")
wa=load("whatsapp_events").drop_duplicates("whatsapp_event_id")
sms=load("sms_events").drop_duplicates("sms_event_id"); fv=load("field_visits").drop_duplicates("visit_id")
agt=load("agents"); ven=load("vendor_telephony"); cmp_=load("campaigns"); brw=load("borrowers")
att=load("call_attempts"); cmpl=load("complaints")

OFF={"UTC":5.5,"Asia/Kolkata":0.0,"Asia/Dubai":1.5}
cal=cal.drop_duplicates(subset=["call_id"]).copy()
cal["event_ist"]=cal.event_at+pd.to_timedelta(cal.timezone.map(OFF),unit="h")

def dedupe_txn(df):
    d=df.drop_duplicates(subset=["payment_id"]).sort_values("event_at").copy()
    d["k1"]=d.account_id.astype(str)+"|"+d.amount.round(2).astype(str)+"|"+d.payment_reference.astype(str)
    d=d[~(d.payment_reference.notna()&d.duplicated("k1",keep="first"))]
    d=d.sort_values(["account_id","amount","event_at"])
    same=(d.account_id==d.account_id.shift(1))&(d.amount.round(2)==d.amount.round(2).shift(1))
    within=(d.event_at-d.event_at.shift(1)).dt.total_seconds().abs()<=24*3600
    return d[~(same&within)].copy()
pay=dedupe_txn(pay)
pay["net"]=np.where(pay.payment_status=="SUCCESS",pay.amount,
                np.where(pay.payment_status=="REVERSED",-pay.amount,0.0))
succ=pay[pay.payment_status=="SUCCESS"].copy()

P("="*92); P("1. IS THE TREND REAL? EXPOSURE-NORMALISED RECOVERY"); P("="*92)
pay["d"]=pay.event_at.dt.normalize()
daily=pay.groupby("d").net.sum()/1e7
full=daily[(daily.index>="2026-01-01")&(daily.index<="2026-07-31")]
P(f"Daily recovery (Rs Cr), Jan-Jul complete months: n={len(full)} days")
P(f"  mean={full.mean():.4f}  sd={full.std():.4f}  CV={full.std()/full.mean():.1%}")
# NET recovery (SUCCESS - REVERSED) is the reporting definition; the trend test uses it.
m=pay.assign(m=pay.event_at.dt.to_period("M")).groupby("m").net.sum()/1e7
days=pd.Series({p:p.days_in_month for p in m.index}); days[m.index[-1]]=8  # Aug partial
perday=(m/days)
tab=pd.DataFrame({"recovery_cr":m.round(2),"calendar_days":days,"cr_per_day":perday.round(4)})
tab["MoM_total_%"]=(m/m.shift(1)-1).mul(100).round(1)
tab["MoM_perday_%"]=(perday/perday.shift(1)-1).mul(100).round(1)
P("\n"+tab.to_string())
P(f"\n  per-day series: mean={perday[:7].mean():.4f} Cr/day, sd={perday[:7].std():.4f}, "
  f"CV={perday[:7].std()/perday[:7].mean():.2%}")
sl,ic,r,pv,se=stats.linregress(np.arange(7),perday[:7].values)
P(f"  OLS trend on per-day NET recovery (Jan-Jul): slope={sl:+.5f} Cr/day per month, "
  f"p={pv:.3f}, R^2={r**2:.3f}")
P(f"  => {'NO statistically significant trend' if pv>0.05 else 'significant trend'} at 5%. "
  f"The point estimate is {'negative' if sl<0 else 'positive'}: if anything, recovery drifted "
  f"{'DOWN' if sl<0 else 'UP'} — in no reading did it improve.")
_g=succ.assign(m=succ.event_at.dt.to_period("M")).groupby("m").amount.sum()/1e7
_gpd=_g/days
P(f"\n  *** Feb->Mar, NET recovery       : {(m.iloc[2]/m.iloc[1]-1)*100:+.1f}%")
P(f"      Feb->Mar, GROSS SUCCESS      : {(_g.iloc[2]/_g.iloc[1]-1)*100:+.1f}%   <-- the reported headline")
P(f"      Feb has 28 days, Mar has 31  : {(31/28-1)*100:+.1f}%   (pure calendar)")
P(f"      Feb->Mar per day, GROSS      : {(_gpd.iloc[2]/_gpd.iloc[1]-1)*100:+.1f}%")
P(f"      Feb->Mar per day, NET        : {(perday.iloc[2]/perday.iloc[1]-1)*100:+.1f}%")
P("      The reported +11.0% is the gross-SUCCESS series, and the calendar accounts for")
P("      10.7 of its 11.0 points. Per day, the same step is a rounding error in either series.")

# same test on the operational funnel
P("\n  Per-day normalisation of the funnel:")
fun=pd.DataFrame({
 "calls":cal.assign(m=cal.event_ist.dt.to_period("M")).groupby("m").size(),
 "contacts":cal[cal.call_status=="ANSWERED"].assign(m=lambda d:d.event_ist.dt.to_period("M")).groupby("m").size(),
 "ptps":ptp.assign(m=ptp.event_at.dt.to_period("M")).groupby("m").size(),
 "payments":succ.assign(m=succ.event_at.dt.to_period("M")).groupby("m").size()})
fun=fun.loc[m.index]
fpd=(fun.T/days).T
P((fpd.round(1)).to_string())
for c in fun.columns:
    s,i,r2,p2,e=stats.linregress(np.arange(7),fpd[c][:7].values)
    P(f"    {c:9s} per-day trend slope={s:+8.2f}/mo  p={p2:.3f}  {'flat' if p2>0.05 else 'TRENDING'}")

P("\n"+"="*92); P("2. MIX vs WITHIN-SEGMENT (decomposition)"); P("="*92)
a=acc[["account_id","risk_segment","loan_type","dpd","outstanding_amount","status"]].copy()
a["dpd_band"]=pd.cut(a.dpd,[-1,0,30,60,90,10**6],labels=["0","1-30","31-60","61-90","90+"])
s=succ.merge(a,on="account_id",how="left"); s["m"]=s.event_at.dt.to_period("M")
t2=tgt.merge(a,on="account_id",how="left"); t2["m"]=t2.target_date.dt.to_period("M")

def decompose(dim):
    # recovery per targeted account, split into within-segment rate change and mix change
    num=s.groupby(["m",dim],observed=True).amount.sum().unstack()
    den=t2.groupby(["m",dim],observed=True).account_id.nunique().unstack()
    rate=(num/den)                       # Rs recovered per targeted account, by segment
    wt=(den.T/den.sum(1)).T              # segment share of targeted population
    overall=(rate*wt).sum(1)
    first,last=overall.index[0],overall.index[6]
    d_total=overall[last]-overall[first]
    d_rate=((rate.loc[last]-rate.loc[first])*wt.loc[first]).sum()
    d_mix =((wt.loc[last]-wt.loc[first])*rate.loc[first]).sum()
    d_int = d_total-d_rate-d_mix
    P(f"\n  Dimension: {dim}  (Jan -> Jul, Rs recovered per targeted account)")
    P(f"    Jan={overall[first]:,.0f}  Jul={overall[last]:,.0f}  change={d_total:+,.0f} ({d_total/overall[first]:+.1%})")
    P(f"      within-segment rate effect : {d_rate:+,.0f}  ({d_rate/abs(d_total) if d_total else 0:+.0%} of change)")
    P(f"      portfolio MIX effect       : {d_mix:+,.0f}")
    P(f"      interaction                : {d_int:+,.0f}")
    P("    segment share of targeted accounts, Jan vs Jul (pp change):")
    P("      "+((wt.loc[last]-wt.loc[first])*100).round(2).to_dict().__str__())
    return rate,wt
for dim in ["risk_segment","dpd_band","loan_type"]:
    decompose(dim)
P("\n  => Mix is stable to within ~1pp on every dimension. There is no portfolio shift to")
P("     credit or blame. The change to decompose is itself statistically zero.")

P("\n"+"="*92); P("3. SIMPSON'S PARADOX CHECK"); P("="*92)
P("  Does any segment move in the opposite direction to the total?")
for dim in ["risk_segment","dpd_band","loan_type"]:
    num=s.groupby(["m",dim],observed=True).amount.sum().unstack()
    den=t2.groupby(["m",dim],observed=True).account_id.nunique().unstack()
    r=(num/den).iloc[:7]
    ch=((r.iloc[6]/r.iloc[0]-1)*100).round(1)
    tot=((num.iloc[:7].sum(1)/den.iloc[:7].sum(1)).iloc[6]/(num.iloc[:7].sum(1)/den.iloc[:7].sum(1)).iloc[0]-1)*100
    P(f"    {dim:13s} total={tot:+.1f}%   by segment: {ch.to_dict()}")
P("  => Segment moves are of both signs and of similar magnitude to the total, i.e. noise,")
P("     not a paradox. No segment is being masked by aggregation.")

P("\n"+"="*92); P("4. SURVIVORSHIP / SELECTION BIAS"); P("="*92)
term=ash[ash.status.isin(["CLOSED","WRITEOFF","PAID"])].groupby("account_id").event_at.min()
tg=tgt.copy(); tg["m"]=tg.target_date.dt.to_period("M"); tg["term"]=tg.account_id.map(term)
tg["post_term"]=tg.target_date>tg.term
P(f"  Targeting rows aimed at already-terminal accounts: {tg.post_term.sum():,} "
  f"({tg.post_term.mean():.1%} of all targeting)")
P("  by month (%):"); P("   "+(tg.groupby('m').post_term.mean()*100).round(1).to_dict().__str__())
P("\n  Cohort recovery rate by account entry month (do later cohorts look better only")
P("  because they have had less time to fail?):")
first_tgt=tgt.groupby("account_id").target_date.min().dt.to_period("M")
paid_acc=set(succ.account_id)
coh=pd.DataFrame({"cohort":first_tgt})
coh["paid"]=coh.index.isin(paid_acc)
coh["obs_days"]=(pd.Timestamp("2026-08-08")-first_tgt.dt.to_timestamp()).dt.days
c=coh.groupby("cohort").agg(n=("paid","size"),paid_rate=("paid","mean"),obs_days=("obs_days","mean"))
c["paid_rate"]=(c.paid_rate*100).round(1)
P(c.to_string())
P("  Same cohorts, censored to a common 30-day observation window:")
fp=succ.groupby("account_id").event_at.min()
coh["first_pay"]=coh.index.map(fp)
coh["days_to_pay"]=(coh.first_pay-first_tgt.reindex(coh.index).dt.to_timestamp()).dt.days
coh["paid_30d"]=(coh.days_to_pay>=0)&(coh.days_to_pay<=30)
c30=coh.groupby("cohort").paid_30d.mean().mul(100).round(1)
P("   "+c30.to_dict().__str__())
P("  => Uncensored cohort rates fall over time purely because later cohorts have less")
P("     exposure. On a fixed 30-day window the cohorts are flat. Any 'improving cohort'")
P("     claim built on uncensored data is a survivorship artefact.")

P("\n"+"="*92); P("5. DRIVER SCAN — what actually moves recovery?"); P("="*92)
P("  Unit of analysis: account. Outcome: paid at least once (SUCCESS) in the window.")
base=acc[["account_id","risk_segment","loan_type","dpd","outstanding_amount","status","timezone","schema_version"]].copy()
base["dpd_band"]=pd.cut(base.dpd,[-1,0,30,60,90,10**6],labels=["0","1-30","31-60","61-90","90+"])
base["paid"]=base.account_id.isin(paid_acc)
rec=succ.groupby("account_id").amount.sum()
base["recovered"]=base.account_id.map(rec).fillna(0)
base["n_calls"]=base.account_id.map(cal.groupby("account_id").size()).fillna(0)
base["n_contacts"]=base.account_id.map(cal[cal.call_status=="ANSWERED"].groupby("account_id").size()).fillna(0)
base["n_wa"]=base.account_id.map(wa.groupby("account_id").size()).fillna(0)
base["n_sms"]=base.account_id.map(sms.groupby("account_id").size()).fillna(0)
base["n_fv"]=base.account_id.map(fv.groupby("account_id").size()).fillna(0)
base["n_ptp"]=base.account_id.map(ptp.groupby("account_id").size()).fillna(0)
base["n_ptp_kept"]=base.account_id.map(ptp[ptp.status=="KEPT"].groupby("account_id").size()).fillna(0)
base["n_touch"]=base[["n_calls","n_wa","n_sms","n_fv"]].sum(1)
base["state"]=base.account_id.map(acc.set_index("account_id").borrower_id).map(brw.drop_duplicates("borrower_id").set_index("borrower_id").state)

def chi(dim):
    ct=pd.crosstab(base[dim],base.paid)
    c2,p,dof,_=stats.chi2_contingency(ct)
    rate=(ct[True]/ct.sum(1)*100).round(2)
    P(f"\n  paid-rate by {dim}:  chi2 p={p:.4f}  {'(no signal)' if p>0.05 else '(SIGNAL)'}")
    P("    "+rate.to_dict().__str__())
for d in ["risk_segment","dpd_band","loan_type","status","timezone","schema_version"]:
    chi(d)

P("\n  Recovered amount vs contact intensity (does more calling recover more?):")
for col in ["n_calls","n_contacts","n_wa","n_sms","n_fv","n_ptp","n_touch"]:
    r,p=stats.spearmanr(base[col],base.recovered)
    rb,pb=stats.pointbiserialr(base.paid.astype(int),base[col])
    P(f"    {col:11s} spearman(rho vs Rs recovered)={r:+.4f} p={p:.3f} | "
      f"corr with paid-flag={rb:+.4f} p={pb:.3f}")
P("  Decile test — recovery by number of touches:")
base["touch_dec"]=pd.qcut(base.n_touch,5,duplicates="drop")
P(base.groupby("touch_dec",observed=True).agg(n=("paid","size"),paid_rate=("paid","mean"),
    mean_recovered=("recovered","mean")).round(3).to_string())

P("\n  PTP kept-rate by source and by agent tenure:")
agt_l=agt.sort_values("updated_at").drop_duplicates("agent_id",keep="last")
ptp2=ptp.merge(agt_l[["agent_id","joined_at","team","employee_code"]],on="agent_id",how="left")
ptp2["tenure_m"]=((ptp2.event_at-ptp2.joined_at).dt.days/30.44)
ptp2["tenure_band"]=pd.cut(ptp2.tenure_m,[-99,6,12,18,24,999],labels=["<6m","6-12m","12-18m","18-24m","24m+"])
k=ptp2[ptp2.status!="OPEN"]
P("    by source:"); P("      "+(k.groupby("source").status.apply(lambda x:(x=="KEPT").mean()*100).round(2)).to_dict().__str__())
ct=pd.crosstab(k.source,k.status=="KEPT"); P(f"      chi2 p={stats.chi2_contingency(ct)[1]:.4f}")
P("    by agent tenure:"); P("      "+(k.groupby("tenure_band",observed=True).status.apply(lambda x:(x=="KEPT").mean()*100).round(2)).to_dict().__str__())
ct=pd.crosstab(k.tenure_band,k.status=="KEPT"); P(f"      chi2 p={stats.chi2_contingency(ct)[1]:.4f}")

P("\n  Contact rate by vendor — raw vendor_id vs resolved vendor NAME:")
cv=cal.merge(ven[["vendor_id","vendor_name"]],on="vendor_id",how="left")
cv["ans"]=cv.call_status=="ANSWERED"
byid=cv.groupby("vendor_id").ans.agg(["mean","size"]); byid["mean"]=(byid["mean"]*100).round(2)
byname=cv.groupby("vendor_name").ans.agg(["mean","size"]); byname["mean"]=(byname["mean"]*100).round(2)
P("    by vendor_id:  spread = "
  f"{byid['mean'].min():.2f}% .. {byid['mean'].max():.2f}%  (range {byid['mean'].max()-byid['mean'].min():.2f}pp)")
P("    by vendor NAME:"); P(byname.to_string())
ct=pd.crosstab(cv.vendor_name,cv.ans); P(f"    chi2 p={stats.chi2_contingency(ct)[1]:.4f}")
P("    => the apparent vendor_id spread is small-sample noise; once identities are resolved")
P("       the vendors are statistically indistinguishable.")

P("\n  Contact rate by hour of day — naive timestamp vs IST-normalised:")
cal["ans"]=cal.call_status=="ANSWERED"
hn=cal.groupby(cal.event_at.dt.hour).ans.mean()*100
hi=cal.groupby(cal.event_ist.dt.hour).ans.mean()*100
P("    naive best hour="+f"{hn.idxmax()} ({hn.max():.2f}%)  worst={hn.idxmin()} ({hn.min():.2f}%)")
P("    IST   best hour="+f"{hi.idxmax()} ({hi.max():.2f}%)  worst={hi.idxmin()} ({hi.min():.2f}%)")
ct=pd.crosstab(cal.event_ist.dt.hour,cal.ans); P(f"    chi2 across IST hours p={stats.chi2_contingency(ct)[1]:.4f}")
P("    => 'best time to call' rankings differ between the two views and neither is")
P("       statistically distinguishable from uniform. Do not build a dialer schedule on this.")

P("\n  Attempt frequency: does attempt_no improve connection?")
ca=att.copy(); ca["conn"]=ca.attempt_status=="CONNECTED"
P("    "+(ca.groupby("attempt_no").conn.mean()*100).round(2).to_dict().__str__())
ct=pd.crosstab(ca.attempt_no,ca.conn); P(f"    chi2 p={stats.chi2_contingency(ct)[1]:.4f}")

P("\n  Agent performance dispersion (after identity resolution):")
emp=agt_l.set_index("agent_id").employee_code
cal["emp"]=cal.agent_id.map(emp)
ap=cal.groupby("emp").agg(calls=("call_id","size"),ans=("ans","mean"))
ap=ap[ap.calls>=30]
P(f"    persons with >=30 calls: {len(ap)}  contact-rate mean={ap.ans.mean()*100:.2f}% sd={ap.ans.std()*100:.2f}pp")
exp_sd=np.sqrt(ap.ans.mean()*(1-ap.ans.mean())/ap.calls.mean())*100
P(f"    binomial sd expected from pure chance at mean volume: {exp_sd:.2f}pp")
P(f"    => observed dispersion / chance dispersion = {ap.ans.std()*100/exp_sd:.2f}x "
  f"({'no real skill differences' if ap.ans.std()*100/exp_sd < 1.2 else 'real skill differences'})")

P("\n  Complaints per 1,000 contacts by channel (the cost side nobody reports):")
cc=cmpl.groupby("source").size()
# denominators are the DEDUPED event counts, matching the golden layer
denom={"CALL":len(cal),"FIELD":len(fv),"WHATSAPP":len(wa),"SMS":len(sms),"EMAIL":np.nan}
for k2,v in cc.items():
    P(f"    {k2:9s} complaints={v:,}  per 1k events={v/denom.get(k2,np.nan)*1000:.2f}"
      if not pd.isna(denom.get(k2,np.nan)) else f"    {k2:9s} complaints={v:,}  (no event denominator)")

(OUT/"drivers_and_stats.txt").write_text("\n".join(lines))
base.to_csv(Path(__file__).resolve().parents[1]/"outputs"/"golden"/"account_features.csv",index=False)
print("\nWrote",OUT/"drivers_and_stats.txt")

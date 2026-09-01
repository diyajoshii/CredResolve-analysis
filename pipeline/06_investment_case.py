"""
06_investment_case.py — Part 4 of the mission: where should the Rs 10 Cr go?

Method
------
1. Size the operation from the golden layer (annualised).
2. Build the unit-cost model. THE DATASET CONTAINS NO COST DATA AT ALL, so every
   cost is an explicit, labelled, sensitivity-tested assumption. That absence is
   itself a finding: 'cost per Rs recovered' — one of the metrics the brief asks us
   to challenge — is not computable from any of the 17 tables.
3. Score each of the six options against the EVIDENCE, not against plausibility.
4. Build the ROI, break-even, downside and confidence range for the recommendation.
"""
import pandas as pd, numpy as np
from pathlib import Path
pd.set_option("display.width",230); pd.set_option("display.max_columns",60)
ROOT=Path(__file__).resolve().parents[1]; GOLD=ROOT/"outputs"/"golden"; REP=ROOT/"outputs"/"reports"
log=[]
def P(*a):
    s=" ".join(str(x) for x in a); print(s); log.append(s)

pay=pd.read_csv(GOLD/"fct_payment.csv",parse_dates=["event_ist"])
cal=pd.read_csv(GOLD/"fct_call.csv",parse_dates=["event_ist"])
tgt=pd.read_csv(GOLD/"fct_targeting.csv",parse_dates=["target_date"])
acc=pd.read_csv(GOLD/"dim_account.csv")
ses=pd.read_csv(ROOT/"data"/"raw"/"agent_sessions.csv",parse_dates=["login_at","logout_at"])
wa=pd.read_csv(ROOT/"data"/"raw"/"whatsapp_events.csv").drop_duplicates("whatsapp_event_id")
sms=pd.read_csv(ROOT/"data"/"raw"/"sms_events.csv").drop_duplicates("sms_event_id")
fv=pd.read_csv(ROOT/"data"/"raw"/"field_visits.csv").drop_duplicates("visit_id")
cmpl=pd.read_csv(ROOT/"data"/"raw"/"complaints.csv")

DAYS=220.0; ANN=365.0/DAYS
P("="*98); P("1. THE OPERATION, ANNUALISED FROM THE GOLDEN LAYER"); P("="*98)
net=pay.net_recovery.sum(); net_ann=net*ANN
book=acc.outstanding_amount.sum()
ses["h"]=(ses.logout_at-ses.login_at).dt.total_seconds()/3600
hours=ses.h.sum(); hours_ann=hours*ANN
minutes=cal.duration_sec.sum()/60
base=dict(
  accounts=len(acc), book_cr=book/1e7,
  net_recovery_cr=net/1e7, net_recovery_cr_ann=net_ann/1e7,
  recovery_pct_of_book_ann=net_ann/book*100,
  agent_hours=hours, agent_hours_ann=hours_ann,
  calls=len(cal), calls_ann=len(cal)*ANN, call_minutes_ann=minutes*ANN,
  whatsapp_ann=len(wa)*ANN, sms_ann=len(sms)*ANN, field_visits_ann=len(fv)*ANN,
  complaints_ann=len(cmpl)*ANN,
  recovery_per_agent_hour=net/hours,
  recovery_per_account_ann=net_ann/len(acc),
)
for k,v in base.items(): P(f"  {k:32s} {v:>16,.2f}")
P(f"\n  Observation window = {DAYS:.0f} days (2026-01-01 .. 2026-08-08); annualisation factor {ANN:.3f}.")
P("  CAVEAT: agent_sessions holds only ~68 logins/day for a 1,000-agent roster placing ~409")
P("  calls/day. The session table is a sample, not the roster, so agent-hours and every")
P("  cost-per-hour figure below inherit that uncertainty. Flagged, not hidden.")

P("\n"+"="*98); P("2. UNIT-COST MODEL  (NO COST DATA EXISTS IN THE DATASET)"); P("="*98)
P("  Searched all 17 tables for cost, rate, tariff, salary, commission, fee: none present.")
P("  'Cost per Rs recovered' and 'recovery per agent-hour in Rs of margin' are therefore")
P("  NOT computable. Every figure below is an assumption with a stated range.\n")
A=dict(agent_cost_per_hour=300, voice_per_min=0.70, sms_each=0.20,
       whatsapp_each=0.55, field_visit_each=250, platform_overhead_pct=0.15)
RANGE=dict(agent_cost_per_hour=(200,450), voice_per_min=(0.45,1.10), sms_each=(0.12,0.30),
           whatsapp_each=(0.35,0.90), field_visit_each=(150,450), platform_overhead_pct=(0.10,0.25))
for k,v in A.items(): P(f"  ASSUMPTION  {k:26s} = {v}   (range tested: {RANGE[k]})")

def opex(a):
    c={}
    c["agents"]=base["agent_hours_ann"]*a["agent_cost_per_hour"]
    c["voice"]=base["call_minutes_ann"]*a["voice_per_min"]
    c["sms"]=base["sms_ann"]*a["sms_each"]
    c["whatsapp"]=base["whatsapp_ann"]*a["whatsapp_each"]
    c["field"]=base["field_visits_ann"]*a["field_visit_each"]
    sub=sum(c.values()); c["platform"]=sub*a["platform_overhead_pct"]
    c["TOTAL"]=sub+c["platform"]
    return c
c=opex(A)
P("\n  Annual operating cost under central assumptions (Rs Cr):")
for k,v in c.items(): P(f"    {k:12s} {v/1e7:>8.2f}")
P(f"\n  Cost per Rs 1 recovered  = Rs {c['TOTAL']/net_ann:.3f}")
P(f"  Recovery per agent-hour  = Rs {net_ann/base['agent_hours_ann']:,.0f}")
lo=opex({**A,**{k:v[0] for k,v in RANGE.items()}})["TOTAL"]
hi=opex({**A,**{k:v[1] for k,v in RANGE.items()}})["TOTAL"]
P(f"  Sensitivity: annual opex ranges Rs {lo/1e7:.1f} Cr .. Rs {hi/1e7:.1f} Cr "
  f"=> cost per Rs recovered Rs {lo/net_ann:.3f} .. Rs {hi/net_ann:.3f}")

P("\n  REASONABLENESS CHECK (do not publish a number you have not sanity-checked):")
P(f"    Implied cost-to-collect = {c['TOTAL']/net_ann:.1%} of recovered value.")
P("    Indian third-party collections typically runs 5-15%. {:.1%} is 2-5x too low."
  .format(c['TOTAL']/net_ann))
P("    Two explanations, and they have opposite implications:")
P("      (a) the event tables are a SAMPLE of a larger operation while payments are complete")
P("          -> activity volumes and therefore costs are understated;")
P("      (b) the payments table is inflated relative to real activity.")
P("    Either way the cost model must not be treated as decision-grade until the real cost")
P("    feeds exist. This is a reason to fund instrumentation before capacity.")

P("\n  *** SCALE CHECK — the single most important number in this document ***")
P(f"      Annual cost of running the entire collections operation : Rs {c['TOTAL']/1e7:.1f} Cr")
P(f"      Proposed investment                                     : Rs 10.0 Cr")
P(f"      Ratio                                                   : {10e7/c['TOTAL']:.1f}x")
P("      Rs 10 Cr is not an increment to this operation. Under any assumption in the range")
P("      above it is between one and three times the entire annual cost of running it.")
P("      A question framed as 'where do we deploy 10 Cr' is really 'do we rebuild the")
P("      operation at 2-3x its current cost base', and it deserves that framing.")

P("\n"+"="*98); P("3. THE REVENUE MODEL FORK — WHICH DECIDES EVERYTHING"); P("="*98)
P("  The dataset does not say whether the business OWNS this book or COLLECTS IT FOR A FEE.")
P("  The answer changes the break-even by a factor of eight.\n")
for label,take in [("Own book (keeps 100% of recovery)",1.00),
                   ("Agency, 20% collection fee",0.20),
                   ("Agency, 12% collection fee",0.12)]:
    need=10e7/take
    P(f"  {label:36s} -> Rs 10 Cr needs Rs {need/1e7:>7.1f} Cr of incremental recovery "
      f"= {need/net_ann:>6.1%} lift on Rs {net_ann/1e7:.0f} Cr/yr")
P("\n  Under the agency model a 10 Cr investment requires a 25-41% recovery lift to break")
P("  even in year one. Nothing in this data suggests any lever of that size exists.")
P("  RECOMMENDATION IS CONDITIONAL ON RESOLVING THIS. It is question one for the CFO.")

P("\n"+"="*98); P("4. THE SIX OPTIONS, SCORED ON EVIDENCE"); P("="*98)
rows=[
 dict(option="1. Better telephony infrastructure",
      evidence="Contact rate 19.4-20.5%/month, flat (p=0.55 across months). After resolving 15 vendor_ids to 5 real vendors, contact rate spread is 19.5-20.7% and the between-vendor difference is marginal (chi2 p=0.044 on 90k calls — statistically detectable, economically 1.2pp). No vendor is meaningfully better, so there is no better vendor to buy.",
      grade="STRONG EVIDENCE AGAINST", verdict="No"),
 dict(option="2. More collection agents",
      evidence="Recovery per agent-hour flat. Agent-level performance dispersion is 1.07x what pure binomial chance predicts — i.e. there are no measurably better or worse agents. Contact intensity correlates with recovery at rho=+0.011. Adding agents scales an activity with no measured link to the outcome.",
      grade="STRONG EVIDENCE AGAINST", verdict="No"),
 dict(option="3. AI voice automation",
      evidence="No agentic-voice or IVR channel exists in the data at all, despite the brief naming both. Zero rows. There is no baseline to improve on and no way to size the opportunity. This is not a 'no', it is an 'unmeasurable'.",
      grade="NO DATA", verdict="Cannot assess"),
 dict(option="4. Better borrower targeting",
      evidence="The targeting engine is provably uninformative: 43.2% of targeting hits accounts whose last known status was CLOSED/WRITEOFF/PAID, against a 42.9% random-selection baseline. Priority score, recommended channel, risk segment and DPD band are all independent of who pays (all chi2 p>0.05 except dpd_band at p=0.028, a 2.4pp spread). Targeting today is statistically indistinguishable from drawing names from a hat.",
      grade="STRONG EVIDENCE OF A GAP (not of a fix)", verdict="Best candidate"),
 dict(option="5. WhatsApp / digital engagement",
      evidence="Last-touch credit to WhatsApp swings between 26% and 28% purely by changing the attribution window from 24h to unbounded, and only 3-20% of payments have ANY touch within a plausible window. Digital 'conversion' is an artifact of message volume. Complaints run 26.5 per 1,000 WhatsApp events vs 17.5 for voice.",
      grade="EVIDENCE IS UNINTERPRETABLE", verdict="No"),
 dict(option="6. Field operations",
      evidence="Field generates 65.9 complaints per 1,000 events - 3.8x the voice rate — and is the most expensive channel per touch under every cost assumption. Field PTP-kept rate (32.5%) is the LOWEST of the four sources. Scaling it raises regulatory exposure fastest.",
      grade="STRONG EVIDENCE AGAINST", verdict="No"),
]
for r in rows:
    P(f"\n  {r['option']}   [{r['grade']}]  -> {r['verdict']}")
    for line in [r["evidence"][i:i+92] for i in range(0,len(r["evidence"]),92)]:
        P(f"      {line}")
pd.DataFrame(rows).to_csv(GOLD/"option_scorecard.csv",index=False)

P("\n"+"="*98); P("5. RECOMMENDATION"); P("="*98)
P("""
  HEADLINE: Do not deploy Rs 10 Cr against any of the six options on this evidence base.
  Deploy Rs 1.2 Cr to make the decision answerable, and gate the remaining Rs 8.8 Cr on a
  pre-registered result. If forced to name one area, it is OPTION 4 — better borrower
  targeting — because it is the only option where the data proves a gap rather than merely
  failing to disprove one.

  WHY NOT JUST BACK OPTION 4 WITH THE FULL Rs 10 CR
  We can prove the current targeting engine carries no signal. We CANNOT prove a better one
  would recover more, because in this data recovery is uncorrelated with every operational
  lever we can measure. Those are different claims, and only the first is supported.
  Committing 10 Cr on the second would repeat exactly the error that produced the 11% headline.
""")

P("  STAGE 1 — Rs 1.2 Cr, months 0-6  (the decision-enabling spend)")
stage1=[("Instrument the cost side: per-call, per-message, per-visit, per-agent-hour cost feeds",0.20),
        ("Golden-layer data platform: the pipeline in this repo, productionised with tests",0.45),
        ("Account-state service: a real state machine so 'closed' means closed",0.25),
        ("Randomised holdout harness + pre-registration process",0.20),
        ("Compliance monitor: calling-window and complaint-rate guardrails",0.10)]
for k,v in stage1: P(f"     Rs {v:>4.2f} Cr  {k}")
P(f"     Rs {sum(v for _,v in stage1):>4.2f} Cr  TOTAL")
P("\n  STAGE 2 — Rs 8.8 Cr, months 6-24, RELEASED ONLY IF the Stage 1 experiment clears")
P("     a pre-registered >=10% lift on net recovery per assigned account over 120 days.")
P("     If it does not clear, the 8.8 Cr is returned to the balance sheet. That option")
P("     to NOT spend is worth more than any of the six options is worth on current evidence.")

P("\n"+"="*98); P("6. THE NUMBERS LEADERSHIP ASKED FOR"); P("="*98)
NET=net_ann
for take,tl in [(1.00,"own book"),(0.20,"agency @20%")]:
    P(f"\n  --- under the {tl} revenue model ---")
    P(f"  {'scenario':<12} {'lift':>6} {'incr. recovery':>16} {'gross value':>13} {'cost':>8} {'net':>9} {'ROI':>8} {'payback':>10}")
    for lab,lift,cost in [("Downside",0.00,10.0),("Base",0.06,10.0),("Upside",0.12,10.0)]:
        incr=NET*lift; val=incr*take
        net_v=val/1e7-cost; roi=(val/1e7-cost)/cost*100
        pb=(cost/(val/1e7)*12) if val>0 else np.inf
        P(f"  {lab:<12} {lift:>5.0%} {incr/1e7:>14.1f} Cr {val/1e7:>11.1f} Cr {cost:>6.1f} Cr "
          f"{net_v:>+8.1f} Cr {roi:>+7.0f}% {(f'{pb:.0f} mo' if np.isfinite(pb) else 'never'):>10}")
    be=10e7/take/NET
    P(f"  BREAK-EVEN (year 1, full 10 Cr): +{be:.1%} lift = Rs {10/take:.1f} Cr incremental recovery")
    be2=1.2e7/take/NET
    P(f"  BREAK-EVEN on Stage 1 alone (1.2 Cr): +{be2:.1%} lift  <-- this is the bet actually being made")

P(f"""
  EXPECTED INCREMENTAL RECOVERY
    Point estimate      : not estimable from this data. Stated honestly rather than invented.
    Defensible range    : 0% to 13% on the own-book model. The upper bound is the minimum
                          detectable effect of the existing data ({12.9:.1f}%) — a true effect
                          larger than that would already be visible, and it is not.
    Most likely         : 0-6%. Every measured lever is null; the only proven gap (targeting
                          carries no signal) has an unmeasured payoff.

  KEY ASSUMPTIONS
    1. The book is owned, not collected on a fee. If it is fee-based, break-even needs a
       25-41% lift and the answer becomes an unambiguous no on all six options.
    2. Agent-hour and per-touch costs are within the ranges above. Opex swings Rs {lo/1e7:.0f}-{hi/1e7:.0f} Cr
       across that range, which moves cost per Rs recovered from {lo/net_ann:.2f} to {hi/net_ann:.2f}.
    3. The 220-day window is representative of a full year. It contains no festival season,
       no year-end, and no prior-year comparison, so seasonality is entirely unmodelled.
    4. Recovery stays flat without intervention (the null this analysis established).

  DOWNSIDE SCENARIO
    The lift is zero, which is what every measured lever in this dataset predicts.
    Full deployment  : Rs 10 Cr spent, Rs 0 returned, ROI -100%, and the operation now runs
                       at 2-3x its previous cost base with the same output.
    Staged approach  : Rs 1.2 Cr spent, Rs 8.8 Cr preserved, and the business ends the year
                       knowing the causal answer instead of arguing about it.

  CONFIDENCE
    HIGH   that reported recovery is flat and the 11% claim is an artifact (multiple
           independent definitions, all null; the mechanism is identified exactly).
    HIGH   that current targeting carries no signal (43.2% vs a 42.9% random baseline).
    MEDIUM that no operational lever works (these are null results on ~30k accounts; the
           data can only rule out effects above ~13%, so a genuine 5% lever could be hiding).
    LOW    on every cost and ROI figure, because the dataset contains no cost data at all.
""")
(REP/"investment_case.txt").write_text("\n".join(log))
print("\nWrote",REP/"investment_case.txt")

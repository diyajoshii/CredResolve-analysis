"""
00_profile.py — Raw data profiling.
Purpose: understand what we actually have before trusting anything.
Outputs: outputs/reports/profile.txt
"""
import pandas as pd, numpy as np, os, sys, json
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT = Path(__file__).resolve().parents[1] / "outputs" / "reports"
OUT.mkdir(parents=True, exist_ok=True)

TABLES = ["borrowers","accounts","agents","agent_sessions","campaigns","daily_targeting",
          "calls","call_attempts","call_dispositions","whatsapp_events","sms_events",
          "field_visits","promises_to_pay","payments","vendor_telephony","complaints",
          "account_status_history"]

# natural business key candidates (first column is the surrogate id in every table)
def load(name):
    df = pd.read_csv(RAW / f"{name}.csv", low_memory=False)
    for c in df.columns:
        if c.endswith("_at") or c.endswith("_date"):
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df

lines = []
def P(*a):
    s = " ".join(str(x) for x in a)
    print(s); lines.append(s)

data = {}
for t in TABLES:
    df = load(t)
    data[t] = df
    pk = df.columns[0]
    P(f"\n{'='*70}\n{t}  rows={len(df):,}  cols={len(df.columns)}")
    P(f"  surrogate key '{pk}': unique={df[pk].nunique():,}  dup_rows_on_pk={len(df)-df[pk].nunique():,}")
    P(f"  fully-identical duplicate rows: {df.duplicated().sum():,}")
    # nulls
    nn = df.isna().sum()
    nn = nn[nn > 0]
    if len(nn):
        P("  nulls: " + ", ".join(f"{k}={v:,} ({v/len(df):.1%})" for k, v in nn.items()))
    # low-cardinality categorical value counts
    for c in df.columns:
        if pd.api.types.is_string_dtype(df[c]) and df[c].nunique(dropna=True) <= 25 and not c.endswith("_id"):
            vc = df[c].value_counts(dropna=False).to_dict()
            P(f"  {c}: {vc}")
    # date ranges
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            P(f"  {c}: min={df[c].min()}  max={df[c].max()}")

P("\n\n" + "="*70)
P("CROSS-TABLE ID INTEGRITY")
P("="*70)

def orphan(child, ccol, parent, pcol):
    c = set(data[child][ccol].dropna().unique())
    p = set(data[parent][pcol].dropna().unique())
    miss = c - p
    P(f"  {child}.{ccol} -> {parent}.{pcol}: {len(miss):,} distinct orphan ids "
      f"({len(miss)/max(len(c),1):.2%} of distinct)")
    return miss

for tbl in ["accounts","calls","call_attempts","call_dispositions","whatsapp_events","sms_events",
            "field_visits","promises_to_pay","payments","complaints","account_status_history"]:
    if "borrower_id" in data[tbl].columns:
        orphan(tbl, "borrower_id", "borrowers", "borrower_id")
for tbl in ["calls","call_attempts","call_dispositions","whatsapp_events","sms_events","field_visits",
            "promises_to_pay","payments","complaints","account_status_history","daily_targeting"]:
    orphan(tbl, "account_id", "accounts", "account_id")
for tbl in ["calls","call_attempts","call_dispositions","field_visits","promises_to_pay","agent_sessions"]:
    orphan(tbl, "agent_id", "agents", "agent_id")
orphan("calls", "campaign_id", "campaigns", "campaign_id")
orphan("daily_targeting", "campaign_id", "campaigns", "campaign_id")
orphan("calls", "vendor_id", "vendor_telephony", "vendor_id")
orphan("call_attempts", "vendor_id", "vendor_telephony", "vendor_id")
orphan("payments", "provider_id", "vendor_telephony", "vendor_id")

P("\n" + "="*70)
P("CALL LINKAGE")
P("="*70)
orphan("call_attempts", "call_id", "calls", "call_id")
orphan("call_dispositions", "call_id", "calls", "call_id")

(OUT / "profile.txt").write_text("\n".join(lines))
print(f"\nWrote {OUT/'profile.txt'}")

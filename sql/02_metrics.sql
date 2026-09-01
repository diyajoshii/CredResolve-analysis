-- =====================================================================================
-- 02_metrics.sql — GOLDEN -> METRICS
--
-- Every metric below carries its definition in a comment, because the whole point of
-- this exercise is that the definitions were the problem, not the SQL.
--
-- THE ONE RULE: no month-on-month comparison without per-day normalisation, and no
-- partial month in a MoM series. Those two omissions produced the reported "+11%".
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS metrics;

-- ============================================================ calendar spine
-- Explicit spine so a month with zero activity shows as zero, not as a missing row.
CREATE OR REPLACE VIEW metrics.dim_month AS
SELECT m                                                  AS month,
       DATE_DIFF('day', m, m + INTERVAL 1 MONTH)          AS calendar_days,
       (m + INTERVAL 1 MONTH - INTERVAL 1 DAY
          <= DATE '2026-08-08')                           AS is_complete_month
FROM   (SELECT UNNEST(GENERATE_SERIES(DATE '2026-01-01', DATE '2026-08-01',
                                      INTERVAL 1 MONTH))::DATE AS m);

-- ======================================================== monthly recovery
-- DEFINITION — net recovery:
--   SUCCESS minus REVERSED, deduplicated at the transaction level.
--   NOT: gross of reversals (overstates by 7.8%)
--   NOT: all payment rows regardless of status (overstates by 57%)
--   NOT: SUCCESS with duplicate rows left in (overstates by 9.9%)
CREATE OR REPLACE VIEW metrics.monthly_recovery AS
SELECT
    d.month, d.calendar_days, d.is_complete_month,
    COALESCE(SUM(p.cash_in),      0) / 1e7               AS gross_success_cr,
    COALESCE(SUM(p.cash_out),     0) / 1e7               AS reversals_cr,
    COALESCE(SUM(p.net_recovery), 0) / 1e7               AS net_recovery_cr,
    -- THE metric. Exposure-normalised, so a 28-day month is comparable to a 31-day one.
    COALESCE(SUM(p.net_recovery), 0) / 1e7 / d.calendar_days AS net_recovery_per_day_cr,
    COUNT(DISTINCT CASE WHEN p.is_success THEN p.account_id END) AS accounts_paid
FROM metrics.dim_month d
LEFT JOIN golden.fct_payment p
       ON DATE_TRUNC('month', p.event_ist) = d.month
GROUP BY 1,2,3
ORDER BY 1;

-- ================================================ the 11% claim, reproduced
-- Reproduces the reported headline and shows what it actually measures.
-- Feb 2026 has 28 days, Mar 2026 has 31. 31/28 - 1 = +10.7%.
CREATE OR REPLACE VIEW metrics.mom_comparison AS
SELECT
    month, calendar_days, net_recovery_cr, net_recovery_per_day_cr,
    ROUND(100.0 * (net_recovery_cr
          / LAG(net_recovery_cr) OVER (ORDER BY month) - 1), 1)          AS mom_headline_pct,
    ROUND(100.0 * (net_recovery_per_day_cr
          / LAG(net_recovery_per_day_cr) OVER (ORDER BY month) - 1), 1)  AS mom_per_day_pct,
    ROUND(100.0 * (calendar_days::DOUBLE
          / LAG(calendar_days) OVER (ORDER BY month) - 1), 1)            AS calendar_effect_pct
FROM metrics.monthly_recovery
WHERE is_complete_month
ORDER BY month;

-- ============================================================== contact funnel
-- DEFINITION — contact rate:
--   answered calls / calls placed, on the IST clock, deduplicated.
--   NOT: 'delivered' messages, which is a vendor metric and trivially inflatable.
-- DEFINITION — RPC (right party contact):
--   a disposition indicating a real conversation with the borrower. Measured on the
--   disposition stream in its own right, NEVER as a ratio to the call stream, because
--   50% of dispositions predate their parent call.
CREATE OR REPLACE VIEW metrics.monthly_funnel AS
WITH c AS (
    SELECT DATE_TRUNC('month', event_ist) AS month,
           COUNT(*) AS calls,
           COUNT(*) FILTER (WHERE is_answered) AS calls_answered,
           AVG(CASE WHEN outside_rbi_window THEN 1.0 ELSE 0 END) AS outside_rbi_window_rate
    FROM golden.fct_call GROUP BY 1),
d AS (
    SELECT DATE_TRUNC('month', event_at) AS month,
           COUNT(*) AS dispositions,
           COUNT(*) FILTER (WHERE is_rpc) AS rpc,
           -- both codes, not just the literal string 'PTP'
           COUNT(*) FILTER (WHERE disposition_std = 'PROMISE_TO_PAY') AS ptp_dispositions,
           COUNT(*) FILTER (WHERE disposition_code_raw = 'PTP')       AS ptp_legacy_code_only
    FROM golden.fct_disposition GROUP BY 1),
p AS (
    -- DEFINITION — PTP kept rate: kept / RESOLVED. An OPEN promise is not a broken one.
    -- Dividing by ALL promises makes the rate fall every month purely because the most
    -- recent month has the most unresolved promises: a manufactured downtrend.
    SELECT DATE_TRUNC('month', event_at) AS month,
           COUNT(*) AS ptp_created,
           COUNT(*) FILTER (WHERE is_resolved) AS ptp_resolved,
           COUNT(*) FILTER (WHERE is_kept)     AS ptp_kept
    FROM golden.fct_ptp GROUP BY 1),
t AS (
    SELECT DATE_TRUNC('month', target_date) AS month,
           COUNT(*) AS targeting_rows,
           COUNT(DISTINCT account_id) AS targeted_accounts,
           AVG(CASE WHEN targeted_while_terminal THEN 1.0 ELSE 0 END) AS targeted_terminal_rate,
           AVG(CASE WHEN targeted_while_terminal THEN 1.0 ELSE 0 END)
             FILTER (WHERE status_known_asof) AS targeted_terminal_rate_known
    FROM golden.fct_targeting GROUP BY 1)
SELECT m.month, m.calendar_days, m.is_complete_month,
       c.calls, c.calls_answered,
       ROUND(100.0*c.calls_answered/NULLIF(c.calls,0),2)              AS contact_rate_pct,
       ROUND(100.0*c.outside_rbi_window_rate,2)                       AS outside_rbi_window_pct,
       d.dispositions, d.rpc,
       ROUND(100.0*d.rpc/NULLIF(d.dispositions,0),2)                  AS rpc_rate_pct,
       ROUND(100.0*d.ptp_dispositions/NULLIF(d.dispositions,0),2)     AS ptp_rate_pct,
       ROUND(100.0*d.ptp_legacy_code_only/NULLIF(d.dispositions,0),2) AS ptp_rate_pct_WRONG,
       p.ptp_created, p.ptp_resolved, p.ptp_kept,
       ROUND(100.0*p.ptp_kept/NULLIF(p.ptp_resolved,0),2)             AS ptp_kept_rate_pct,
       ROUND(100.0*p.ptp_kept/NULLIF(p.ptp_created,0),2)              AS ptp_kept_rate_pct_WRONG,
       t.targeting_rows, t.targeted_accounts,
       ROUND(100.0*t.targeted_terminal_rate,2)                        AS targeted_terminal_pct,
       ROUND(100.0*t.targeted_terminal_rate_known,2)                  AS targeted_terminal_pct_known
FROM metrics.dim_month m
LEFT JOIN c USING (month) LEFT JOIN d USING (month)
LEFT JOIN p USING (month) LEFT JOIN t USING (month)
ORDER BY 1;

-- ================================================ channel attribution sensitivity
-- DEFINITION — channel conversion: there isn't a defensible one on this data.
-- This view exists to PROVE that, by showing how the answer moves with the window.
-- Last-touch credit is mechanically won by whichever channel emits the most events.
CREATE OR REPLACE VIEW metrics.attribution_sensitivity AS
WITH w(window_hours) AS (VALUES (24),(72),(168),(8760)),
last_touch AS (
    SELECT w.window_hours, p.payment_id, p.amount,
           FIRST(t.channel ORDER BY t.event_at DESC) AS channel
    FROM golden.fct_payment p
    JOIN w ON TRUE
    JOIN golden.fct_touch t
      ON t.account_id = p.account_id
     AND t.event_at  <= p.event_ist
     AND DATE_DIFF('hour', t.event_at, p.event_ist) <= w.window_hours
    WHERE p.is_success
    GROUP BY 1,2,3)
SELECT window_hours, channel,
       COUNT(*)                                                  AS payments_attributed,
       ROUND(100.0*COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY window_hours),1) AS share_pct,
       ROUND(SUM(amount)/1e7,2)                                  AS value_cr
FROM last_touch GROUP BY 1,2 ORDER BY 1,4 DESC;

-- ======================================================== segment performance
-- Uses the SNAPSHOT segmentation, which is the only one available, and says so.
CREATE OR REPLACE VIEW metrics.monthly_by_segment AS
SELECT DATE_TRUNC('month', p.event_ist) AS month,
       a.risk_segment, a.dpd_band_snapshot, a.loan_type,
       SUM(p.net_recovery)/1e7                                   AS net_recovery_cr,
       COUNT(DISTINCT p.account_id)                              AS accounts_paid
FROM golden.fct_payment p
JOIN golden.dim_account a USING (account_id)
GROUP BY 1,2,3,4;
-- CAVEAT: risk_segment / dpd_band / status are CURRENT snapshots. Grouping a historical
-- month by them assigns each account its END-STATE label in every past month. Directional
-- only; never use for a causal or trend claim.

-- ==================================================== executive summary table
CREATE OR REPLACE VIEW metrics.exec_summary AS
SELECT
    (SELECT ROUND(SUM(net_recovery)/1e7,1) FROM golden.fct_payment)           AS net_recovery_cr,
    (SELECT ROUND(SUM(amount)/1e7,1) FROM stg.payments
       WHERE payment_status='SUCCESS')                                        AS reported_style_cr,
    (SELECT ROUND(AVG(net_recovery_per_day_cr),3) FROM metrics.monthly_recovery
       WHERE is_complete_month)                                               AS avg_cr_per_day,
    (SELECT ROUND(STDDEV(net_recovery_per_day_cr)/AVG(net_recovery_per_day_cr)*100,1)
       FROM metrics.monthly_recovery WHERE is_complete_month)                 AS per_day_cv_pct,
    (SELECT ROUND(100.0*AVG(CASE WHEN targeted_while_terminal THEN 1.0 ELSE 0 END),1)
       FROM golden.fct_targeting WHERE status_known_asof)                     AS targeted_terminal_pct,
    (SELECT ROUND(100.0*AVG(CASE WHEN outside_rbi_window THEN 1.0 ELSE 0 END),1)
       FROM golden.fct_call)                                                  AS calls_outside_window_pct;

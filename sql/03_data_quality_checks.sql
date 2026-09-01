-- =====================================================================================
-- 03_data_quality_checks.sql — the tests that must pass before the dashboard refreshes
--
-- Each check returns zero rows when healthy. In production these are assertions in the
-- orchestrator; a FAIL blocks publication rather than emailing somebody about it.
-- Severity: BLOCK = do not publish. WARN = publish with a banner.
-- =====================================================================================

CREATE OR REPLACE VIEW metrics.dq_results AS

-- ---------------------------------------------------------------- GRAIN / PK
SELECT 'BLOCK' AS severity, 'dim_borrower' AS object, 'primary key unique' AS check_name,
       COUNT(*) - COUNT(DISTINCT borrower_id) AS failures,
       'borrower_id must be unique' AS detail
FROM golden.dim_borrower
UNION ALL
SELECT 'BLOCK','dim_account','primary key unique',
       COUNT(*) - COUNT(DISTINCT account_id), 'account_id must be unique'
FROM golden.dim_account
UNION ALL
SELECT 'BLOCK','fct_payment','primary key unique',
       COUNT(*) - COUNT(DISTINCT payment_id), 'payment_id must be unique'
FROM golden.fct_payment
UNION ALL
SELECT 'BLOCK','fct_call','primary key unique',
       COUNT(*) - COUNT(DISTINCT call_id), 'call_id must be unique'
FROM golden.fct_call

-- ------------------------------------------------------------ REFERENTIAL
UNION ALL
SELECT 'BLOCK','fct_payment','account_id resolves',
       COUNT(*), 'payment on an account that does not exist'
FROM golden.fct_payment p
LEFT JOIN golden.dim_account a USING (account_id)
WHERE a.account_id IS NULL
UNION ALL
SELECT 'WARN','dim_account','borrower_id resolves',
       COUNT(*), 'account points at a borrower_id not in dim_borrower (kept, flagged)'
FROM golden.dim_account WHERE dq_orphan_borrower

-- ----------------------------------------------------------------- VALUES
UNION ALL
SELECT 'BLOCK','fct_payment','amount is non-negative',
       COUNT(*), 'negative or null payment amount'
FROM golden.fct_payment WHERE amount IS NULL OR amount < 0
UNION ALL
SELECT 'BLOCK','fct_payment','status in domain',
       COUNT(*), 'unexpected payment_status — a new status silently changes recovery'
FROM golden.fct_payment
WHERE payment_status NOT IN ('SUCCESS','FAILED','PENDING','REVERSED')
UNION ALL
SELECT 'BLOCK','fct_disposition','disposition code in domain',
       COUNT(*), 'unmapped disposition code — check for a taxonomy migration'
FROM golden.fct_disposition
WHERE disposition_std NOT IN ('PROMISE_TO_PAY','PROMISE_BROKEN','PAID','CALLBACK',
                              'DISPUTE','REFUSED','NO_CONTACT','WRONG_NUMBER')
UNION ALL
SELECT 'BLOCK','stg.calls','timezone in domain',
       COUNT(*), 'unknown timezone label — IST conversion would be silently wrong'
FROM stg.calls WHERE dq_unknown_timezone

-- ------------------------------------------------------------- TIMELINESS
UNION ALL
SELECT 'BLOCK','fct_payment','no future-dated events',
       COUNT(*), 'payment dated after the batch date'
FROM golden.fct_payment WHERE event_ist > CURRENT_TIMESTAMP
UNION ALL
SELECT 'WARN','fct_call','events inside the declared window',
       COUNT(*), 'call outside 2026-01-01..2026-08-08 after IST conversion'
FROM golden.fct_call
WHERE event_ist < TIMESTAMP '2026-01-01' OR event_ist >= TIMESTAMP '2026-08-09'

-- ------------------------------------------------------- METRIC INTEGRITY
UNION ALL
-- The check that would have caught the 11% claim before it reached a slide.
SELECT 'BLOCK','metrics.mom_comparison','no MoM on a partial month',
       COUNT(*), 'a partial month leaked into the month-on-month series'
FROM metrics.monthly_recovery
WHERE NOT is_complete_month
  AND month IN (SELECT month FROM metrics.mom_comparison)
UNION ALL
SELECT 'WARN','metrics.monthly_recovery','headline vs per-day divergence',
       COUNT(*), 'headline MoM differs from per-day MoM by >5pp: calendar-length artifact'
FROM metrics.mom_comparison
WHERE ABS(COALESCE(mom_headline_pct,0) - COALESCE(mom_per_day_pct,0)) > 5
UNION ALL
SELECT 'BLOCK','metrics.monthly_recovery','reconciles to golden',
       CASE WHEN ABS((SELECT SUM(net_recovery_cr) FROM metrics.monthly_recovery)
                   - (SELECT SUM(net_recovery)/1e7 FROM golden.fct_payment)) > 0.01
            THEN 1 ELSE 0 END,
       'monthly aggregate does not sum to the fact table'

-- ------------------------------------------------------------- ANOMALIES
UNION ALL
SELECT 'WARN','fct_payment','daily volume within 4 sigma',
       COUNT(*), 'daily recovery outside 4 standard deviations of the trailing mean'
FROM (SELECT DATE_TRUNC('day', event_ist) d, SUM(net_recovery) v
      FROM golden.fct_payment GROUP BY 1) x,
     (SELECT AVG(v) mu, STDDEV(v) sd FROM
        (SELECT DATE_TRUNC('day', event_ist) d, SUM(net_recovery) v
         FROM golden.fct_payment GROUP BY 1)) s
WHERE ABS(x.v - s.mu) > 4 * s.sd
UNION ALL
SELECT 'WARN','golden.rejections','rejection rate within tolerance',
       CASE WHEN (SELECT COUNT(*) FROM golden.rejections)
                 > 0.25 * (SELECT COUNT(*) FROM stg.borrowers) THEN 1 ELSE 0 END,
       'more than 25% of rows rejected — investigate the source, not the rule';

-- Run it:
--   SELECT * FROM metrics.dq_results WHERE failures > 0 ORDER BY severity, object;
-- Gate the publish on:
--   SELECT COUNT(*) FROM metrics.dq_results WHERE severity='BLOCK' AND failures > 0;

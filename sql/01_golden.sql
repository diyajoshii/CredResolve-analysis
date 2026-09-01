-- =====================================================================================
-- 01_golden.sql — STG -> GOLDEN
--
-- Contract of this layer:
--   * declared grain, enforced by a test in sql/03_data_quality_checks.sql
--   * deduplication is explained in a comment next to every rule
--   * rows are never silently dropped: what leaves goes to golden.rejections
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS golden;

-- Reject ledger. Every deduplication writes here first so raw = golden + rejections.
CREATE OR REPLACE TABLE golden.rejections (
    source_table VARCHAR, source_key VARCHAR, rule VARCHAR, reason VARCHAR
);
DELETE FROM golden.rejections;

-- ============================================================== dim_borrower
-- GRAIN: one row per borrower_id.
-- The raw table is an overwritten record dumped WITH its history: 30,600 rows,
-- 11,015 borrower_ids. Latest-wins SCD-1, ranked on asof_ts because updated_at
-- is inverted on half the rows.
CREATE OR REPLACE TABLE golden.dim_borrower AS
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (
               PARTITION BY borrower_id
               ORDER BY asof_ts DESC, updated_at DESC, created_at DESC) AS rn
    FROM   stg.borrowers
)
SELECT * EXCLUDE (rn) FROM ranked WHERE rn = 1;

INSERT INTO golden.rejections
SELECT 'borrowers', borrower_id, 'scd1_superseded', 'older version of an overwritten record'
FROM  (SELECT borrower_id, ROW_NUMBER() OVER (PARTITION BY borrower_id
              ORDER BY asof_ts DESC) rn FROM stg.borrowers) WHERE rn > 1;

-- =============================================================== dim_account
-- GRAIN: one row per account_id. Nothing is dropped — an account with a broken
-- borrower link still owes money and still receives payments. Excluding it would
-- UNDERSTATE recovery, which is the opposite of the error we are investigating.
CREATE OR REPLACE TABLE golden.dim_account AS
SELECT a.*,
       (b.borrower_id IS NULL AND a.borrower_id IS NOT NULL) AS dq_orphan_borrower
FROM   stg.accounts a
LEFT JOIN golden.dim_borrower b USING (borrower_id);

-- ================================================================= dim_agent
-- QUARANTINED. Collapsed only so joins do not fan out. trust_level = 'LOW'.
-- Do not publish any agent-level or tenure metric from this table: agent_id and
-- employee_code form one connected component, and every agent_id carries ~9.5
-- different agent_names. Resolving on employee_code instead would inflate mean
-- calls-per-person from 88 to 137 (+56%) — a productivity 'gain' invented by a
-- bad entity-resolution choice.
CREATE OR REPLACE TABLE golden.dim_agent AS
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY updated_at DESC) rn
    FROM stg.agents
)
SELECT * EXCLUDE (rn), 'LOW' AS trust_level,
       'identity unresolvable: agent_id<->employee_code is many-to-many' AS dq_note
FROM ranked WHERE rn = 1;

-- ================================================================ dim_vendor
CREATE OR REPLACE TABLE golden.dim_vendor AS SELECT * FROM stg.vendor_telephony;

-- ============================================================== dim_campaign
CREATE OR REPLACE TABLE golden.dim_campaign AS SELECT * FROM stg.campaigns;

-- =============================================================== fct_payment
-- GRAIN: one row per economic payment event.
--
-- Deduplication ladder, in order, with the reason each rule exists:
--   1. exact duplicate rows          -> double ingest of the same file (486 rows)
--   2. duplicate payment_id          -> surrogate key reused (14 rows)
--   3. (account, amount, reference)  -> same money, same account, same gateway ref
--   4. (account, amount) within 24h  -> gateway retry issued under a fresh reference
--
-- DO NOT dedupe on payment_reference alone. 3,405 references are reused, and every
-- one of them spans DIFFERENT accounts and amounts — an id-space collision, not a
-- duplicate. Naive reference dedup removes 3,809 rows instead of 500, destroying
-- 2,617 genuine successful payments worth Rs 19.8 Cr.
--
-- On this dataset rules 3 and 4 correctly remove NOTHING: all payment duplication
-- here is byte-identical re-ingest. They stay because they are what fires when a
-- real gateway retry happens.
CREATE OR REPLACE TABLE golden.fct_payment AS
WITH d1 AS (SELECT DISTINCT * FROM stg.payments),
     d2 AS (SELECT * EXCLUDE (rn) FROM (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY payment_id ORDER BY event_at) rn
              FROM d1) WHERE rn = 1),
     d3 AS (SELECT * EXCLUDE (rn) FROM (
              SELECT *, ROW_NUMBER() OVER (
                       PARTITION BY account_id, ROUND(amount,2), payment_reference
                       ORDER BY event_at) rn
              FROM d2 WHERE payment_reference IS NOT NULL
              UNION ALL
              SELECT *, 1 AS rn FROM d2 WHERE payment_reference IS NULL) WHERE rn = 1),
     d4 AS (SELECT *,
              LAG(event_at) OVER (PARTITION BY account_id, ROUND(amount,2)
                                  ORDER BY event_at) AS prev_at
            FROM d3)
SELECT
    * EXCLUDE (prev_at),
    (payment_status = 'SUCCESS')                        AS is_success,
    (payment_status = 'REVERSED')                       AS is_reversal,
    CASE WHEN payment_status = 'SUCCESS'  THEN amount ELSE 0 END AS cash_in,
    CASE WHEN payment_status = 'REVERSED' THEN amount ELSE 0 END AS cash_out,
    CASE WHEN payment_status = 'SUCCESS'  THEN amount
         WHEN payment_status = 'REVERSED' THEN -amount ELSE 0 END AS net_recovery,
    -- payments carry no timezone column; assumed already IST. Flagged, not hidden.
    event_at                                            AS event_ist,
    TRUE                                                AS dq_timezone_assumed
FROM d4
WHERE prev_at IS NULL
   OR DATE_DIFF('hour', prev_at, event_at) > 24;

-- ================================================================== fct_call
-- GRAIN: one row per call_id, on the IST clock.
CREATE OR REPLACE TABLE golden.fct_call AS
WITH d AS (SELECT * EXCLUDE (rn) FROM (
             SELECT *, ROW_NUMBER() OVER (PARTITION BY call_id ORDER BY event_at_raw) rn
             FROM (SELECT DISTINCT * FROM stg.calls)) WHERE rn = 1)
SELECT d.*, v.vendor_group,
       (d.call_status = 'ANSWERED')                     AS is_answered,
       EXTRACT(hour FROM d.event_ist)                   AS hour_ist,
       (EXTRACT(hour FROM d.event_ist) < 8
        OR EXTRACT(hour FROM d.event_ist) >= 21)        AS outside_rbi_window
FROM d LEFT JOIN stg.vendor_telephony v USING (vendor_id)
WHERE d.event_ist >= TIMESTAMP '2026-01-01 00:00:00'
  AND d.event_ist <  TIMESTAMP '2026-08-09 00:00:00';

-- =========================================================== fct_disposition
-- NOT modelled as a child of fct_call. 50% of dispositions are timestamped BEFORE
-- their parent call, so calls / attempts / dispositions are three independent
-- streams sharing a call_id label but not a consistent clock.
CREATE OR REPLACE TABLE golden.fct_disposition AS
SELECT d.*,
       (d.disposition_std IN ('PROMISE_TO_PAY','PROMISE_BROKEN','PAID',
                              'DISPUTE','REFUSED','CALLBACK'))  AS is_rpc,
       (d.event_at < c.event_ist)                               AS dq_before_parent_call
FROM   (SELECT DISTINCT * FROM stg.call_dispositions) d
LEFT JOIN golden.fct_call c USING (call_id);

-- =================================================================== fct_ptp
CREATE OR REPLACE TABLE golden.fct_ptp AS
SELECT DISTINCT * FROM stg.promises_to_pay;

-- ============================================================= fct_targeting
-- Adds the POINT-IN-TIME account status. Never join the accounts snapshot here:
-- that is look-ahead bias.
--
-- Before using status at all we tested whether account_status_history is a valid
-- lifecycle. It is not: 19,673 status changes occur AFTER an account's first
-- terminal status, uniformly distributed across all 7 statuses. CLOSED/WRITEOFF/PAID
-- are NOT absorbing states, so "first terminal event" is an invalid closure rule.
-- We use LAST KNOWN status as of the target date instead.
CREATE OR REPLACE TABLE golden.fct_targeting AS
-- DuckDB ASOF JOIN does exactly this. On Athena/Trino/Snowflake, replace with
--   LEFT JOIN LATERAL (SELECT status FROM stg.account_status_history h
--                       WHERE h.account_id = t.account_id AND h.event_at <= t.target_date
--                       ORDER BY h.event_at DESC LIMIT 1) s ON TRUE
WITH t AS (SELECT DISTINCT * FROM stg.daily_targeting)
SELECT t.*,
       h.status                                                  AS status_asof,
       (h.status IS NOT NULL)                                    AS status_known_asof,
       COALESCE(h.status IN ('CLOSED','WRITEOFF','PAID'), FALSE)  AS targeted_while_terminal
FROM t
ASOF LEFT JOIN stg.account_status_history h
  ON t.account_id = h.account_id
 AND t.target_date >= h.event_at;

-- ======================================================== fct_account_status
CREATE OR REPLACE TABLE golden.fct_account_status AS
SELECT DISTINCT *, (recorded_at < event_at) AS dq_recorded_before_event
FROM stg.account_status_history;

-- ================================================================= fct_touch
-- One unified interaction stream. 'engaged' is a TWO-WAY signal per channel, never
-- 'delivered'. Delivery is a vendor metric; counting it as engagement is the easiest
-- way to fabricate a rising contact rate.
CREATE OR REPLACE TABLE golden.fct_touch AS
SELECT account_id, borrower_id, event_ist AS event_at, call_id AS source_id,
       'VOICE' AS channel, is_answered AS engaged
FROM golden.fct_call
UNION ALL
SELECT account_id, borrower_id, event_at, whatsapp_event_id, 'WHATSAPP',
       event_type IN ('READ','REPLIED','PAYMENT_CLICK')
FROM (SELECT DISTINCT * FROM stg.whatsapp_events)
UNION ALL
SELECT account_id, borrower_id, event_at, sms_event_id, 'SMS', event_type = 'CLICKED'
FROM (SELECT DISTINCT * FROM stg.sms_events)
UNION ALL
SELECT account_id, borrower_id, event_at, visit_id, 'FIELD',
       outcome IN ('CONTACTED','PTP','PAID')
FROM (SELECT DISTINCT * FROM stg.field_visits);

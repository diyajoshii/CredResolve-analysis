-- =====================================================================================
-- 00_staging.sql — RAW -> STG
-- Dialect: DuckDB (ANSI-compatible; Athena/Trino/Snowflake notes inline where they differ)
--
-- Contract of this layer:
--   * one row per source row, nothing dropped, nothing renamed away
--   * types cast once, here, and never again downstream
--   * every quality problem becomes a BOOLEAN FLAG, never a filter
--
-- Run:  duckdb collections.duckdb < sql/00_staging.sql
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS stg;

-- Point this at wherever the CSVs live.
SET VARIABLE raw_path = 'data/raw/';

-- --------------------------------------------------------------------- borrowers
CREATE OR REPLACE TABLE stg.borrowers AS
SELECT
    borrower_id,
    name,
    phone,
    email,
    city,
    state,
    CAST(created_at AS TIMESTAMP)                       AS created_at,
    CAST(updated_at AS TIMESTAMP)                       AS updated_at,
    GREATEST(CAST(created_at AS TIMESTAMP),
             CAST(updated_at AS TIMESTAMP))             AS asof_ts,
    -- 15,354 of 30,600 raw rows have updated_at BEFORE created_at, so updated_at
    -- alone is not a valid recency key. Flag it, then rank on asof_ts instead.
    (updated_at < created_at)                           AS dq_timestamp_inverted,
    (phone IS NULL)                                     AS dq_missing_phone,
    (email IS NULL)                                     AS dq_missing_email
FROM read_csv_auto(getvariable('raw_path') || 'borrowers.csv');

-- ---------------------------------------------------------------------- accounts
CREATE OR REPLACE TABLE stg.accounts AS
SELECT
    account_id,
    borrower_id,
    loan_type,
    CAST(principal_amount   AS DOUBLE)                  AS principal_amount,
    CAST(outstanding_amount AS DOUBLE)                  AS outstanding_amount,
    CAST(dpd AS INTEGER)                                AS dpd_snapshot,
    CASE WHEN dpd = 0            THEN '0'
         WHEN dpd BETWEEN 1  AND 30 THEN '1-30'
         WHEN dpd BETWEEN 31 AND 60 THEN '31-60'
         WHEN dpd BETWEEN 61 AND 90 THEN '61-90'
         ELSE '90+' END                                 AS dpd_band_snapshot,
    risk_segment,
    status                                              AS status_snapshot,
    CAST(opened_at AS TIMESTAMP)                        AS opened_at,
    timezone,
    schema_version,
    (borrower_id IS NULL)                               AS dq_missing_borrower
FROM read_csv_auto(getvariable('raw_path') || 'accounts.csv');
-- WARNING: dpd, status and outstanding_amount are CURRENT SNAPSHOTS with no as-of date.
-- Using them to segment a historical month is look-ahead bias. Suffixed _snapshot so
-- nobody can use them by accident.

-- ------------------------------------------------------------------------ agents
CREATE OR REPLACE TABLE stg.agents AS
SELECT
    agent_id, employee_code, agent_name, vendor_id, team, status,
    CAST(joined_at  AS TIMESTAMP)                       AS joined_at,
    CAST(updated_at AS TIMESTAMP)                       AS updated_at
FROM read_csv_auto(getvariable('raw_path') || 'agents.csv');
-- WARNING: 30,000 rows, 1,000 agent_ids, 1,099 employee_codes, and the two form a
-- SINGLE connected component. There is no resolvable person here. See dim_agent.

-- ------------------------------------------------------------------------- calls
CREATE OR REPLACE TABLE stg.calls AS
SELECT
    call_id, account_id, borrower_id, agent_id, campaign_id, vendor_id,
    direction, call_status,
    CAST(duration_sec AS INTEGER)                       AS duration_sec,
    timezone,
    CAST(event_at AS TIMESTAMP)                         AS event_at_raw,
    -- Single reporting clock. event_at is a NAIVE local timestamp whose zone is in
    -- the timezone column. 9.8% of calls change calendar day after this conversion.
    CAST(event_at AS TIMESTAMP) + INTERVAL (
        CASE timezone WHEN 'UTC' THEN 330            -- +5h30
                      WHEN 'Asia/Dubai' THEN 90      -- +1h30
                      WHEN 'Asia/Kolkata' THEN 0
                      ELSE 0 END) MINUTE              AS event_ist,
    (timezone NOT IN ('UTC','Asia/Dubai','Asia/Kolkata')) AS dq_unknown_timezone
FROM read_csv_auto(getvariable('raw_path') || 'calls.csv');
-- Athena/Trino: use  date_add('minute', <offset>, event_at)
-- Snowflake:    use  DATEADD(minute, <offset>, event_at)

-- ---------------------------------------------------------------- call_attempts
CREATE OR REPLACE TABLE stg.call_attempts AS
SELECT attempt_id, account_id, borrower_id, call_id, agent_id, vendor_id,
       CAST(attempt_no AS INTEGER) AS attempt_no, attempt_status,
       CAST(event_at AS TIMESTAMP) AS event_at
FROM read_csv_auto(getvariable('raw_path') || 'call_attempts.csv');

-- ------------------------------------------------------------ call_dispositions
CREATE OR REPLACE TABLE stg.call_dispositions AS
SELECT
    disposition_id, account_id, borrower_id, call_id, agent_id,
    disposition_code                                    AS disposition_code_raw,
    disposition_version,
    -- Taxonomy harmonisation. 'PTP' (legacy) and 'PROMISE_TO_PAY' (v1/v2) are the
    -- SAME business outcome. A dashboard matching only 'PTP' undercounts by 50%.
    CASE disposition_code
        WHEN 'PTP'            THEN 'PROMISE_TO_PAY'
        WHEN 'PROMISE_TO_PAY' THEN 'PROMISE_TO_PAY'
        WHEN 'PTP_BROKEN'     THEN 'PROMISE_BROKEN'
        ELSE disposition_code END                       AS disposition_std,
    CAST(event_at AS TIMESTAMP)                         AS event_at
FROM read_csv_auto(getvariable('raw_path') || 'call_dispositions.csv');

-- ----------------------------------------------------------------- promises_to_pay
CREATE OR REPLACE TABLE stg.promises_to_pay AS
SELECT ptp_id, account_id, borrower_id, agent_id, source, status,
       CAST(promised_amount AS DOUBLE) AS promised_amount,
       CAST(promised_date AS TIMESTAMP) AS promised_date,
       CAST(event_at AS TIMESTAMP) AS event_at,
       (status <> 'OPEN')  AS is_resolved,
       (status =  'KEPT')  AS is_kept
FROM read_csv_auto(getvariable('raw_path') || 'promises_to_pay.csv');

-- ---------------------------------------------------------------------- payments
CREATE OR REPLACE TABLE stg.payments AS
SELECT payment_id, account_id, borrower_id, payment_reference, provider_id,
       payment_status, payment_method,
       CAST(amount AS DOUBLE)      AS amount,
       CAST(event_at AS TIMESTAMP) AS event_at,
       (payment_reference IS NULL) AS dq_missing_reference
FROM read_csv_auto(getvariable('raw_path') || 'payments.csv');

-- ------------------------------------------------------------- digital + field
CREATE OR REPLACE TABLE stg.whatsapp_events AS
SELECT whatsapp_event_id, account_id, borrower_id, message_id, event_type,
       template_code, provider_id, CAST(event_at AS TIMESTAMP) AS event_at
FROM read_csv_auto(getvariable('raw_path') || 'whatsapp_events.csv');

CREATE OR REPLACE TABLE stg.sms_events AS
SELECT sms_event_id, account_id, borrower_id, message_id, event_type,
       template_code, provider_id, CAST(event_at AS TIMESTAMP) AS event_at
FROM read_csv_auto(getvariable('raw_path') || 'sms_events.csv');

CREATE OR REPLACE TABLE stg.field_visits AS
SELECT visit_id, account_id, borrower_id, agent_id, visit_type, outcome,
       CAST(latitude AS DOUBLE) AS latitude, CAST(longitude AS DOUBLE) AS longitude,
       CAST(scheduled_at AS TIMESTAMP) AS scheduled_at,
       CAST(event_at AS TIMESTAMP)     AS event_at
FROM read_csv_auto(getvariable('raw_path') || 'field_visits.csv');

-- ------------------------------------------------------------------- operational
CREATE OR REPLACE TABLE stg.daily_targeting AS
SELECT target_id, account_id, campaign_id, CAST(priority AS INTEGER) AS priority,
       recommended_channel, status AS targeting_status,
       CAST(target_date AS TIMESTAMP) AS target_date
FROM read_csv_auto(getvariable('raw_path') || 'daily_targeting.csv');

CREATE OR REPLACE TABLE stg.campaigns AS
SELECT campaign_id, campaign_name, channel, strategy_version, target_definition,
       CAST(start_at AS TIMESTAMP) AS start_at,
       CAST(end_at   AS TIMESTAMP) AS end_at,
       -- campaign_name is a LABEL, not a definition: 5 names span 120 ids, up to 5
       -- channels and 5 target definitions each. Never group by name alone.
       campaign_name || ' | ' || channel || ' | ' || target_definition
         || ' | ' || strategy_version                  AS campaign_key
FROM read_csv_auto(getvariable('raw_path') || 'campaigns.csv');

CREATE OR REPLACE TABLE stg.vendor_telephony AS
SELECT vendor_id, vendor_name AS vendor_group, vendor_account_id,
       timezone, status, schema_version
FROM read_csv_auto(getvariable('raw_path') || 'vendor_telephony.csv');
-- 15 vendor_ids resolve to 5 commercial vendors. vendor_id is an ACCOUNT, not a vendor.

CREATE OR REPLACE TABLE stg.account_status_history AS
SELECT history_id, account_id, borrower_id, status, changed_by, source,
       CAST(event_at    AS TIMESTAMP) AS event_at,      -- business clock
       CAST(recorded_at AS TIMESTAMP) AS recorded_at,   -- ingestion clock
       DATE_DIFF('minute', CAST(event_at AS TIMESTAMP),
                           CAST(recorded_at AS TIMESTAMP))/60.0 AS ingestion_lag_h
FROM read_csv_auto(getvariable('raw_path') || 'account_status_history.csv');

CREATE OR REPLACE TABLE stg.agent_sessions AS
SELECT session_id, agent_id, channel, device_id, timezone,
       CAST(login_at  AS TIMESTAMP) AS login_at,
       CAST(logout_at AS TIMESTAMP) AS logout_at,
       DATE_DIFF('minute', CAST(login_at AS TIMESTAMP),
                           CAST(logout_at AS TIMESTAMP))/60.0 AS session_hours
FROM read_csv_auto(getvariable('raw_path') || 'agent_sessions.csv');

CREATE OR REPLACE TABLE stg.complaints AS
SELECT complaint_id, account_id, borrower_id, complaint_type, severity,
       status, source,
       CAST(event_at      AS TIMESTAMP) AS event_at,
       CAST(resolution_at AS TIMESTAMP) AS resolution_at
FROM read_csv_auto(getvariable('raw_path') || 'complaints.csv');

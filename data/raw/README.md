# Input data

The 17 source CSVs are not committed (they are the assignment's provided dataset, ~63 MB).

Unzip `collections_30k_dataset.zip` into this directory, then run the pipeline:

    for s in pipeline/*.py; do python "$s"; done

Expected files: accounts.csv, account_status_history.csv, agents.csv, agent_sessions.csv,
borrowers.csv, calls.csv, call_attempts.csv, call_dispositions.csv, campaigns.csv,
complaints.csv, daily_targeting.csv, data_dictionary.csv, field_visits.csv, payments.csv,
promises_to_pay.csv, sms_events.csv, vendor_telephony.csv, whatsapp_events.csv

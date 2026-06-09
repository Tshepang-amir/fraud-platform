"""Feast materialisation: offline → online store sync.

Databricks-scheduled version. Materialises features from the offline store
(ADLS Gen2 / Delta Gold) to the online store (Azure Postgres) for real-time
feature serving. Triggered by the Airflow retrain DAG on a daily schedule.

Local-dev equivalent lives in src/train/feast_materialise.py.
"""

# TODO Day 7: Implement Databricks-scheduled materialisation (ADLS/Delta → Postgres online store)

# Fraud Platform - Interview One-Pager

**Candidate:** Tsapang Mashego  
**Project:** Production-style real-time card fraud scoring platform  
**Stack:** Azure, Databricks, Feast, FastAPI, MLflow, Airflow, Grafana, Terraform

## Problem

Fraud teams need low-latency card scoring, model lineage, drift monitoring, and
safe promotion controls. A notebook model alone is not enough: the hard part is
turning it into a governed service that can be monitored, audited, and rolled
back.

## Architecture

```text
PaySim demo stream -> Event Hubs -> ADLS -> Databricks Bronze/Silver/Gold
                                    |
IEEE-CIS training data -> feature engineering -> MLflow champion/challenger
                                    |
Feast + Postgres -> FastAPI on Azure Container Apps -> Grafana Cloud
                                    |
PSI drift -> Airflow retraining DAG -> human approval gate -> promotion
```

## What I Built

| Area | Delivery |
|---|---|
| Data platform | ADLS zones, Databricks notebooks, Gold feature outputs |
| Feature store | Feast definitions, local skew test, Postgres online-store path |
| Model lifecycle | LightGBM champion, CatBoost challenger, MLflow run lineage |
| Serving | Public FastAPI endpoint on Azure Container Apps |
| Observability | OpenTelemetry metrics and Grafana dashboard |
| Governance | Model card, promotion policy, rollback runbook, Airflow approval gate |
| Testing | Unit tests, API smoke tests, live smoke tests, load-test harness |

## Proof Points

| Metric / proof | Result |
|---|---|
| Champion AUC | `0.9200` |
| Champion AUPRC | `0.5833` |
| TPR at `0.1%` FPR | `0.2903` |
| Challenger decision | `KEEP_CHAMPION` |
| Live smoke tests | `6/6` passed |
| API-reported p95 latency | `66.85ms` in direct 50-concurrent sample |
| Grafana | Request rate, latency, errors, decisions, fraud-score distribution |

## Governance Choice

The model cannot promote itself. A challenger must pass automated metric gates
and then wait at an Airflow human approval gate:

```text
fraud_retrain_approval_<challenger_run_id> = approved
```

This mirrors real model-risk practice: automation recommends, a human approves,
and rollback paths are documented.

## Business Framing

At a fixed false-positive budget, catching more fraud value has direct financial
value. The portfolio framing is `+23%` fraud value caught at fixed `0.1%` FPR
versus a stale monthly baseline. This is a scenario-backed portfolio statement,
not a claim of production validation on South African bank traffic.

## Honest Caveats

| Caveat | Current handling |
|---|---|
| PaySim is simulated stream traffic | Declared demo seam |
| Feast Azure online table mismatch | API fallback keeps service available; production fix required |
| External laptop Locust p95 > 100ms | API-reported scoring latency met the target |
| Grafana setup token was exposed | Must rotate before public handoff |
| Airflow UI screenshot still pending | DAG and approval policy are implemented |

## Interview Narrative

"I did not just train a fraud model. I built the platform around it: ingestion,
feature consistency, serving, monitoring, shadow challenger comparison,
governance, and rollback. The strongest part of the project is that it shows the
operational controls a bank would ask for before trusting an ML model."

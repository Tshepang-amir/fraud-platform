# Fraud Scorer v1 Model Card

## Model Overview

| Field | Value |
|---|---|
| Model name | `fraud-scorer-champion` |
| Model family | LightGBM gradient-boosted decision tree classifier |
| Model role | Champion, live decision model |
| Version | v1 |
| MLflow run ID | `9c599d91d7c546df82ad252837990c29` |
| Shadow challenger | CatBoost, run `cd2da7878fd44ad39dab091dde2984fb` |
| Developer | Tsapang Mashego |
| Date | 2026-05-14 |

## Intended Use

The model scores card transactions in real time and returns a fraud probability
in `[0, 1]`. The serving API maps the probability to one of three operational
decisions:

| Decision | Rule |
|---|---|
| `APPROVE` | score `< 0.50` |
| `REVIEW` | score `>= 0.50` and `< 0.90` |
| `DECLINE` | score `>= 0.90` |

The intended users are internal fraud/risk operations teams. The output is not a
credit decision, AML decision, or customer eligibility decision.

## Data

| Item | Description |
|---|---|
| Training source | IEEE-CIS Fraud Detection data from Kaggle/Vesta |
| Rows | 590,540 transactions |
| Fraud rate | 3.50% fraud, about 1:27 class imbalance |
| Split strategy | Temporal train/validation/test split by `TransactionDT`; no random shuffle |
| Feature count | 440 serving features in champion model |
| Categorical features | 31 categorical columns |
| Live stream seam | PaySim-style events reshaped to IEEE-CIS schema for demo streaming |

The model was trained on real historical fraud data, but the live portfolio demo
uses simulated transaction events. It has not been validated on actual South
African bank production traffic.

## Performance

Validation metrics from the champion/challenger comparison:

| Metric | Champion LightGBM | Challenger CatBoost | Winner |
|---|---:|---:|---|
| AUC | 0.9200 | 0.9179 | LightGBM |
| AUPRC | 0.5833 | 0.5575 | LightGBM |
| TPR @ 0.1% FPR | 0.2903 | 0.2535 | LightGBM |
| Brier score | 0.0349 | 0.0585 | LightGBM |

Bootstrap comparison on `TPR @ 0.1% FPR`:

| Statistic | Value |
|---|---:|
| Mean difference, CatBoost minus LightGBM | -0.0303 |
| 95% CI | [-0.0511, -0.0067] |
| Decision | `KEEP_CHAMPION` |

The champion is statistically better than the challenger at the operating point.
The aspirational target of `0.60 TPR @ 0.1% FPR` is not reached by either single
model. At AUC around 0.92, that target likely requires a stronger feature set or
ensemble-level discriminability.

## Business Framing

The portfolio headline is: at fixed `0.1% FPR`, the platform catches materially
more fraud value than a stale monthly retraining baseline while preserving a hard
false-positive budget. The README expresses this as an indicative `+23% fraud
value caught` scenario. That value is a project/business framing figure, not a
claim of production validation on South African bank traffic.

## Subgroup and Segment Notes

| Segment | Observed issue | Model-card action |
|---|---|---|
| `ProductCD=C` | EDA found 11.69% fraud rate vs 2.04% for `ProductCD=W` | Monitor decision mix and false positives by product code |
| Transaction amount | Fraud operations usually has different tolerance for small vs large amounts | Add settled-label evaluation by amount band before production |
| Card tenure/history | Feature values may be sparse for new cards | Monitor fallback/missing feature rate |
| Geography | Not available in IEEE-CIS public schema | Cannot claim geographic fairness validation |
| South African bank population | Not represented directly in IEEE-CIS | Requires local backtest before real deployment |

No protected-class fairness claim is made. The public dataset does not contain
the required demographic fields, and adding proxy fairness claims would be
misleading.

## Serving and Monitoring

The model is deployed behind FastAPI on Azure Container Apps:

```text
https://fraud-scorer-staging.thankfulsky-1fcb5cce.southafricanorth.azurecontainerapps.io
```

Live proof completed:

| Check | Result |
|---|---|
| `/health` | 200 OK |
| `/ready` | 200 OK with `model_ready: true` |
| Live smoke test | 6/6 passed |
| Grafana telemetry | Request rate, latency, error rate, and decisions populated |
| API-reported scoring p95 sample | 66.85ms under 50-concurrent direct sample |
| External Locust p95 from workstation | 1,600ms; does not meet strict external p95 target |

Monitoring is implemented with OpenTelemetry and Grafana Cloud:

- Request rate.
- p95 and p99 scoring latency.
- 5xx error rate.
- Decision distribution.
- Fraud score distribution.
- PSI drift report in `reports/psi_scores.json`.

## Drift and Retraining Policy

Population Stability Index thresholds are fixed by governance policy:

| PSI | Status | Action |
|---|---|---|
| `< 0.10` | Stable | No action |
| `0.10-0.20` | Moderate shift | Warn and investigate |
| `> 0.20` | Major shift | Trigger Airflow retraining DAG |

Current PSI report has no feature above `0.20`. The highest feature in the last
recorded report is `fe_card_entropy_product_7d = 0.1512`, which is a warning
tier, not a retraining trigger.

## Human Approval and Promotion

Production promotion is deliberately gated:

1. Drift or schedule triggers retraining.
2. A challenger is trained and evaluated against the champion.
3. Automated checks decide whether the challenger is eligible for review.
4. Airflow pauses at `wait_for_human_approval`.
5. A human must set `fraud_retrain_approval_<challenger_run_id> = approved`.
6. Only then can the promotion task run.

This is a governance feature, not a missing automation step.

## Known Limitations

- The live demo currently falls back to numeric missing feature defaults when
  Feast cannot find the expected Azure Postgres online table. The API remains
  available, but the online feature-store path must be corrected before real
  production use.
- The Grafana OTLP token was exposed during setup and must be rotated before
  final handoff.
- External p95 latency from the developer workstation did not meet `<100ms`.
  API-reported scoring latency did meet the target in a direct concurrency
  sample.
- PaySim streaming is a declared demo seam, not actual card network traffic.
- IEEE-CIS/Vesta data is from 2017-2018 and may not reflect current fraud
  patterns.
- No real customer-impact decision should be made without a local bank backtest,
  model risk review, and operational sign-off.

## Rollback

Rollback triggers and steps are documented in:

- `governance/promotion_policy.md`
- `governance/rollback_runbook.md`

Rollback may be model-only, by restoring the previous champion run ID, or
runtime-level, by shifting traffic to a previous Azure Container App revision.


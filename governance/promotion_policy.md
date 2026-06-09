# Model Promotion Policy

## Purpose

This policy defines when a challenger fraud model can move from shadow mode to
production. It intentionally separates automated evidence collection from human
approval. Automation can recommend promotion; it cannot perform production
promotion by itself.

## Approval Roles

- **Model owner:** reviews model metrics, drift context, and test evidence.
- **Fraud operations approver:** confirms the false-positive budget is acceptable
  for customer and analyst workload.
- **Platform owner:** confirms deployment, rollback, and monitoring readiness.

For this portfolio build, Tsapang Mashego acts as all three roles. In a bank,
these would be separate people.

## Automatic Retraining Trigger

Rule 6 PSI thresholds are fixed and not runtime-configurable:

| PSI value | Status | Action |
|---|---|---|
| `< 0.10` | Stable | No action |
| `0.10-0.20` | Moderate shift | Log warning and investigate |
| `> 0.20` | Major shift | Trigger Airflow retraining DAG |

The retraining trigger is implemented in `src/monitor/trigger_retrain.py`.

## Promotion Evidence Required

A challenger can reach the Airflow human approval gate only when all automated
checks pass:

- Bootstrapped 95% confidence interval lower bound for challenger minus champion
  `TPR @ 0.1% FPR` is greater than `0`.
- Challenger AUC does not regress by more than `0.005`.
- Challenger Brier score does not regress by more than `0.002`.
- Training/serving skew test passes before deployment.
- Live smoke tests pass on staging.
- Grafana shows non-zero request telemetry and zero sustained 5xx error rate.

The Airflow DAG task `branch_on_evaluation` rejects the challenger unless these
conditions pass.

## Human Approval Gate

Production promotion requires an explicit Airflow Variable:

```text
fraud_retrain_approval_<challenger_run_id> = approved
```

The DAG pauses at `wait_for_human_approval` until that variable is set. This is
the Rule 7 governance feature: there is no automated path from retraining to
production.

## Promotion Steps

1. Confirm the challenger run ID and evaluation metrics.
2. Confirm staging smoke tests and Grafana telemetry.
3. Confirm rollback target: current champion run ID.
4. Set the Airflow approval variable to `approved`.
5. Let the DAG run `promote_to_production`.
6. Watch Grafana p95, p99, 5xx rate, and decision distribution for 30 minutes.
7. Record the promotion in `BUILD_LOG.md`.

## Rollback Triggers

Rollback is required when any of these hold after promotion:

- Sustained 5xx rate above `1%` for 5 minutes.
- p95 scoring latency above `500ms` for 15 minutes.
- Fraud decision distribution shifts unexpectedly, for example a sudden `DECLINE`
  spike not explained by a known fraud wave.
- Champion/challenger comparison shows degraded fraud capture at the operating
  point.
- Data quality or training/serving skew test fails.
- Fraud operations asks for rollback due analyst/customer impact.

Rollback instructions live in `governance/rollback_runbook.md`.


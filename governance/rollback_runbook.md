# Rollback Runbook

## Purpose

This runbook describes how to return the fraud scoring service to the previous
known-good champion model or previous known-good Container App revision.

## When To Roll Back

Rollback immediately when one of these happens:

- Sustained 5xx rate above `1%` for 5 minutes.
- p95 scoring latency above `500ms` for 15 minutes.
- `/ready` returns `model_ready: false`.
- `/score` returns invalid decisions or scores outside `[0, 1]`.
- Grafana decision distribution changes in a way fraud operations cannot explain.
- A training/serving skew check fails.
- A new model promotion is approved in error.

## First Five Minutes

1. Open the Grafana dashboard and capture p95, p99, request rate, 5xx rate, and
   decision distribution.
2. Run live health checks:

   ```powershell
   $url = "https://fraud-scorer-staging.thankfulsky-1fcb5cce.southafricanorth.azurecontainerapps.io"
   Invoke-RestMethod "$url/health"
   Invoke-RestMethod "$url/ready"
   ```

3. Stop further promotion work. Do not approve any Airflow gate while the
   incident is active.
4. Identify whether the issue is model-only or container/runtime.

## Container Revision Rollback

Use this when the previous revision was healthy and the current revision is bad.

```powershell
$az = "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"

& $az containerapp revision list `
  --name fraud-scorer-staging `
  --resource-group rg-fraud-platform `
  -o table

& $az containerapp ingress traffic set `
  --name fraud-scorer-staging `
  --resource-group rg-fraud-platform `
  --revision-weight <GOOD_REVISION_NAME>=100
```

After rollback, run:

```powershell
$env:STAGING_URL = "https://fraud-scorer-staging.thankfulsky-1fcb5cce.southafricanorth.azurecontainerapps.io"
$env:LIVE_SMOKE_VERIFY_TLS = "false"
$env:LIVE_SMOKE_TRUST_ENV = "false"
.\.venv\Scripts\pytest.exe tests\integration\test_api_live.py -v
```

## Model Rollback

Use this when the container is healthy but the promoted model is wrong.

1. Set the champion run ID back to the previous known-good run:

   ```powershell
   & $az containerapp update `
     --name fraud-scorer-staging `
     --resource-group rg-fraud-platform `
     --set-env-vars MLFLOW_CHAMPION_RUN_ID=<PREVIOUS_CHAMPION_RUN_ID>
   ```

2. Wait for the new revision to become healthy.
3. Run live smoke tests.
4. Verify Grafana request rate and error rate.
5. Record the rollback in `BUILD_LOG.md`.

## Communication

For the portfolio build, record all incident notes in `BUILD_LOG.md`.

In a real bank:

- Notify fraud operations that fallback model/revision is active.
- Tell support teams whether customer friction changed.
- Record timestamps for detection, decision, rollback, and verification.

## Recovery Criteria

The incident is resolved only when:

- `/health` and `/ready` pass.
- Live smoke tests pass.
- Grafana 5xx rate is back to `0`.
- p95 latency stabilizes.
- Fraud operations accepts the restored decision distribution.


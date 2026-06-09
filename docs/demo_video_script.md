# Day 13 Demo Video Script

This is the recording plan for a 5 to 7 minute portfolio demo. The goal is to
show proof, not every implementation detail.

## Recording Goal

Show that the fraud platform is not just a notebook project. It has a live API,
model lineage, governance, monitoring, and a clear human approval path before
production promotion.

## Before Recording

Do this once before you press record:

1. Rotate the Grafana OTLP token if it has not already been rotated after setup.
2. Close any terminal tabs that show secrets, tokens, Key Vault values, or Azure
   credentials.
3. Open only safe pages:
   - GitHub or local README.
   - Grafana dashboard.
   - Azure Container App overview for `fraud-scorer-staging`.
   - Model card.
   - Promotion policy or Airflow DAG code.
4. Set the staging URL in PowerShell:

```powershell
$env:STAGING_URL = "https://fraud-scorer-staging.thankfulsky-1fcb5cce.southafricanorth.azurecontainerapps.io"
```

5. Warm up the API and generate Grafana traffic:

```powershell
python scripts/send_demo_traffic.py --url $env:STAGING_URL --requests 250 --concurrency 25
```

If this workstation hits SSL/proxy errors, use the same command with:

```powershell
python scripts/send_demo_traffic.py --url $env:STAGING_URL --requests 250 --concurrency 25 --trust-env false --verify-tls false
```

6. Wait 30 to 90 seconds, then refresh Grafana using the last 15 minutes range.

## Exact Recording Flow

| Time | Screen | What to say | Proof to show |
|---|---|---|---|
| 0:00-0:40 | README headline | "This is a production-style real-time card fraud scoring platform. It replaces a notebook-only model with a deployed scoring service, feature store, monitoring, and model governance." | README headline, tech stack, governance links |
| 0:40-1:20 | Architecture doc or README architecture section | "Events enter through Event Hubs, land in ADLS, are transformed in Databricks, materialised through Feast, scored by FastAPI, and observed in Grafana." | Architecture summary and stack table |
| 1:20-2:05 | Azure resources or build log Day 7/8/9 | "The training data is IEEE-CIS. The live stream is a declared PaySim demo seam reshaped into the same serving schema." | BUILD_LOG Day 7/8/9 or Azure Container App |
| 2:05-3:00 | Terminal | "Now I will prove the public scoring endpoint is alive." Run health, ready, and live smoke tests. | `/health`, `/ready`, `pytest tests/integration/test_api_live.py -v` |
| 3:00-3:50 | Terminal, then Grafana | "This script sends synthetic live scoring traffic so the dashboard has fresh data." Run the demo traffic script if not already run. | Request count, decision distribution, API p95 latency |
| 3:50-4:40 | Grafana dashboard | "Grafana shows request rate, p95 and p99 scoring latency, 5xx error rate, decision distribution, and fraud score distribution." | Dashboard panels with non-zero request data and 0 5xx |
| 4:40-5:30 | Model card | "The champion is LightGBM. CatBoost was tested as a challenger and rejected because the champion won on AUC, AUPRC, TPR at 0.1 percent FPR, and Brier score." | Champion/challenger table and KEEP_CHAMPION decision |
| 5:30-6:15 | Promotion policy or DAG code | "A challenger cannot promote itself. The Airflow DAG pauses at a human approval gate. A person must set the approval variable before production promotion can happen." | `wait_for_human_approval` and promotion policy |
| 6:15-6:50 | README or model card limitations | "The honest caveats are documented: external workstation latency is not the same as API latency, the live Feast path currently has a fallback, and the demo stream is simulated." | Known limitations section |
| 6:50-7:00 | README closing | "The result is a portfolio-ready ML platform artifact with live serving proof, monitoring proof, and governance proof." | Final checklist or README |

## Useful Commands During Recording

PowerShell:

```powershell
$env:STAGING_URL = "https://fraud-scorer-staging.thankfulsky-1fcb5cce.southafricanorth.azurecontainerapps.io"
python scripts/send_demo_traffic.py --url $env:STAGING_URL --requests 250 --concurrency 25
pytest tests/integration/test_api_live.py -v
```

Use this workstation-safe variant if Python reports a self-signed certificate or
proxy connection error:

```powershell
python scripts/send_demo_traffic.py --url $env:STAGING_URL --requests 250 --concurrency 25 --trust-env false --verify-tls false
```

Simple endpoint checks:

```powershell
Invoke-RestMethod "$env:STAGING_URL/health"
Invoke-RestMethod "$env:STAGING_URL/ready"
Invoke-RestMethod "$env:STAGING_URL/score" -Method Post -ContentType "application/json" -Body '{"transaction_id":"demo-recording-001","card1":13926,"TransactionAmt":49.99,"ProductCD":"W"}'
```

## What Not To Show

- Grafana API tokens.
- Azure Key Vault secret values.
- Databricks personal access tokens.
- Event Hubs connection strings.
- Postgres passwords or full DSNs.
- Any terminal scrollback where the old exposed token is visible.

## If Something Fails During Recording

If the live API is cold, rerun `/health` and wait one minute. Container Apps may
need a cold-start moment.

If Grafana says "No data", run the traffic script again, set the time range to
"Last 15 minutes", and wait for the 30 second refresh interval.

If the Airflow UI is not available, show the DAG code and promotion policy. Say:
"The DAG implementation and policy are complete; the live Airflow UI screenshot
is still a remaining proof item before final handoff."

## Final Video Checklist

- Public API proof shown.
- Live smoke test shown.
- Grafana dashboard shown with fresh data.
- Model card and champion/challenger comparison shown.
- Human approval gate explained.
- Known limitations stated honestly.
- No secrets visible.

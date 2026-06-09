# Module: container_apps
# Azure Container Registry (Basic) + Container Apps Environment + Scoring App
#
# The Container App is deployed here via Terraform (Day 9).
# GitHub Actions CD pipeline rolls new image revisions via `az containerapp update`.

resource "azurerm_container_registry" "main" {
  name                = "acrfraud${var.suffix}"  # alphanumeric only, 5-50 chars
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = true  # needed for image pull; Day 10+ migrates to Managed Identity pull
  tags                = var.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-fraud-${var.suffix}"
  location                   = var.location
  resource_group_name        = var.resource_group_name
  log_analytics_workspace_id = var.log_analytics_workspace_id
  tags                       = var.tags
}

# ── Scoring Container App ──────────────────────────────────────────────────────
# Image: pushed by GitHub Actions CD after first `docker build`.
# On first `terraform apply` the image doesn't exist yet — a placeholder is used.
# GitHub Actions then rolls to the real image with `az containerapp update`.

locals {
  acr_server    = azurerm_container_registry.main.login_server
  placeholder   = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
  pg_pass       = var.postgres_password
  pg_fqdn       = var.postgres_fqdn
  decision_dsn  = "postgresql://fraudadmin:${local.pg_pass}@${local.pg_fqdn}:5432/fraud_platform?sslmode=require"
}

resource "azurerm_container_app" "fraud_scorer" {
  name                         = "fraud-scorer-${var.suffix}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  # System-assigned managed identity — used for Key Vault access Day 10+
  identity {
    type = "SystemAssigned"
  }

  # ACR credentials (admin; replaced by Managed Identity pull in Day 10)
  registry {
    server               = local.acr_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "acr-password"
  }

  # Sensitive values stored as Container Apps secrets (never in env vars directly)
  secret {
    name  = "acr-password"
    value = azurerm_container_registry.main.admin_password
  }
  secret {
    name  = "feast-pg-password"
    value = local.pg_pass
  }
  secret {
    name  = "decision-log-dsn"
    value = local.decision_dsn
  }

  template {
    min_replicas = 0  # scale-to-zero when idle
    max_replicas = 3

    container {
      name   = "fraud-scorer"
      image  = local.placeholder  # replaced by CD pipeline after first push
      cpu    = 1.0
      memory = "2Gi"

      # Non-sensitive env vars
      env { name = "MLFLOW_TRACKING_URI";             value = "mlruns" }
      env { name = "MLFLOW_CHAMPION_RUN_ID";          value = "9c599d91d7c546df82ad252837990c29" }
      env { name = "MLFLOW_CHALLENGER_RUN_ID";        value = "cd2da7878fd44ad39dab091dde2984fb" }
      env { name = "FEAST_REPO_PATH";                 value = "/app/feature_repo" }
      env { name = "FEAST_ONLINE_STORE_HOST";         value = local.pg_fqdn }
      env { name = "FEAST_ONLINE_STORE_PORT";         value = "5432" }
      env { name = "FEAST_ONLINE_STORE_USER";         value = "fraudadmin" }
      env { name = "FEAST_ONLINE_STORE_SSLMODE";      value = "require" }
      env { name = "FRAUD_THRESHOLD_REVIEW";          value = "0.50" }
      env { name = "FRAUD_THRESHOLD_DECLINE";         value = "0.90" }

      # Sensitive env vars reference secrets (never appear in Terraform state as plain text)
      env { name = "FEAST_POSTGRES_PASSWORD"; secret_name = "feast-pg-password" }
      env { name = "DECISION_LOG_DSN";        secret_name = "decision-log-dsn" }

      # Liveness + readiness probes
      liveness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8000
        initial_delay = 10
        period_seconds = 15
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/ready"
        port      = 8000
        initial_delay = 20
        period_seconds = 10
      }
    }
  }

  ingress {
    external_enabled           = true
    target_port                = 8000
    allow_insecure_connections = false  # HTTPS only

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

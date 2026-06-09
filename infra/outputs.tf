# Terraform Outputs — Fraud Platform
# These are printed after `terraform apply` and readable via `terraform output`

output "resource_group_name" {
  description = "Resource group containing all project resources"
  value       = azurerm_resource_group.main.name
}

output "resource_suffix" {
  description = "Random 8-char hex suffix used in all resource names"
  value       = random_id.suffix.hex
}

# ── Data Lake ────────────────────────────────────────────────────────────────

output "data_lake_storage_account_name" {
  description = "ADLS Gen2 storage account name (Bronze/Silver/Gold containers)"
  value       = module.data_lake.storage_account_name
}

output "data_lake_storage_account_id" {
  value = module.data_lake.storage_account_id
}

# ── PostgreSQL ───────────────────────────────────────────────────────────────

output "postgres_server_name" {
  value = module.postgres.server_name
}

output "postgres_server_fqdn" {
  description = "Fully-qualified hostname for the PostgreSQL Flexible Server"
  value       = module.postgres.server_fqdn
}

output "postgres_database_name" {
  value = module.postgres.database_name
}

output "postgres_admin_login" {
  value = var.postgres_admin_login
}

# ── Event Hubs ───────────────────────────────────────────────────────────────

output "eventhub_namespace_name" {
  value = module.event_hubs.namespace_name
}

output "eventhub_name" {
  value = module.event_hubs.hub_name
}

# ── Key Vault ────────────────────────────────────────────────────────────────

output "keyvault_name" {
  value = module.keyvault.keyvault_name
}

output "keyvault_uri" {
  description = "Key Vault URI — used by the FastAPI app to fetch secrets at startup"
  value       = module.keyvault.keyvault_uri
}

# ── Container Registry ───────────────────────────────────────────────────────

output "container_registry_login_server" {
  description = "ACR login server — push Docker images here"
  value       = module.container_apps.registry_login_server
}

output "container_apps_environment_id" {
  value = module.container_apps.environment_id
}

# ── Monitoring ───────────────────────────────────────────────────────────────

output "log_analytics_workspace_id" {
  value = module.monitoring.log_analytics_workspace_id
}

output "application_insights_connection_string" {
  description = "Paste into APPLICATIONINSIGHTS_CONNECTION_STRING in Key Vault (Day 9)"
  value       = module.monitoring.application_insights_connection_string
  sensitive   = true
}

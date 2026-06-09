output "storage_account_name" {
  value = azurerm_storage_account.datalake.name
}

output "storage_account_id" {
  value = azurerm_storage_account.datalake.id
}

output "primary_dfs_endpoint" {
  description = "ADLS Gen2 DFS endpoint — used by Databricks and Feast offline store"
  value       = azurerm_storage_account.datalake.primary_dfs_endpoint
}

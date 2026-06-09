output "server_name" {
  value = azurerm_postgresql_flexible_server.main.name
}

output "server_fqdn" {
  description = "Hostname for connection strings — format: server_name.postgres.database.azure.com"
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "database_name" {
  value = azurerm_postgresql_flexible_server_database.fraud_platform.name
}

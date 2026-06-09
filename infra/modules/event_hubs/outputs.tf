output "namespace_name" {
  value = azurerm_eventhub_namespace.main.name
}

output "hub_name" {
  value = azurerm_eventhub.transactions.name
}

output "send_connection_string" {
  description = "Connection string for the PaySim replay producer (send-only)"
  value       = azurerm_eventhub_authorization_rule.producer.primary_connection_string
  sensitive   = true
}

output "listen_connection_string" {
  description = "Connection string for the Azure Function consumer (listen-only) — stored in Key Vault"
  value       = azurerm_eventhub_authorization_rule.consumer.primary_connection_string
  sensitive   = true
}

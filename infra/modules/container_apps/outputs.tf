output "registry_login_server" {
  description = "ACR login server — push images here: docker push <login_server>/fraud-scorer:<tag>"
  value       = azurerm_container_registry.main.login_server
}

output "registry_admin_username" {
  value = azurerm_container_registry.main.admin_username
}

output "registry_admin_password" {
  value     = azurerm_container_registry.main.admin_password
  sensitive = true
}

output "environment_id" {
  description = "Container Apps Environment ID"
  value       = azurerm_container_app_environment.main.id
}

output "environment_name" {
  value = azurerm_container_app_environment.main.name
}

# Added in Day 9
output "scoring_app_url" {
  description = "Public HTTPS URL of the fraud scorer (set as STAGING_URL GitHub variable)"
  value       = "https://${azurerm_container_app.fraud_scorer.ingress[0].fqdn}"
}

output "scoring_app_name" {
  value = azurerm_container_app.fraud_scorer.name
}

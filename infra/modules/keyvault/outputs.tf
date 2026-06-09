output "keyvault_name" {
  value = azurerm_key_vault.main.name
}

output "keyvault_uri" {
  description = "Key Vault URI — set as AZURE_KEYVAULT_URI env var in the Container App"
  value       = azurerm_key_vault.main.vault_uri
}

output "keyvault_id" {
  value = azurerm_key_vault.main.id
}

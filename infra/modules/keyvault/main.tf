# Module: keyvault
# Azure Key Vault (Standard tier) — stores all runtime secrets.
# The FastAPI app retrieves secrets via Managed Identity at startup (Day 9).
# purge_protection_enabled = false so we can fully delete during cleanup.

resource "azurerm_key_vault" "main" {
  name                       = "kv-fraud-${var.suffix}"
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  tags                       = var.tags

  # Terraform deployer gets full secret access to write secrets during apply
  access_policy {
    tenant_id = var.tenant_id
    object_id = var.deployer_object_id

    secret_permissions = [
      "Get", "List", "Set", "Delete", "Purge", "Recover", "Backup", "Restore"
    ]
  }
}

resource "azurerm_key_vault_secret" "postgres_password" {
  name         = "postgres-password"
  value        = var.postgres_password
  key_vault_id = azurerm_key_vault.main.id
}

resource "azurerm_key_vault_secret" "eventhub_connection_string" {
  name         = "eventhub-connection-string"
  value        = var.eventhub_connection_string
  key_vault_id = azurerm_key_vault.main.id
}

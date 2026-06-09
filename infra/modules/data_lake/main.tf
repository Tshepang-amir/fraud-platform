# Module: data_lake
# ADLS Gen2 storage account (hierarchical namespace = true) with
# three containers: bronze (raw), silver (cleaned), gold (feature tables)

data "azurerm_client_config" "current" {}

resource "azurerm_storage_account" "datalake" {
  name                     = "stfraud${var.suffix}"  # max 24 chars, alphanumeric only
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  is_hns_enabled           = true  # enables ADLS Gen2 hierarchical namespace

  blob_properties {
    delete_retention_policy {
      days = 7
    }
  }

  tags = var.tags
}

# Grant the Terraform deployer Storage Blob Data Owner so it can create
# ADLS Gen2 filesystems (data-plane operation, needs explicit RBAC)
resource "azurerm_role_assignment" "deployer_datalake_owner" {
  scope                = azurerm_storage_account.datalake.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_storage_data_lake_gen2_filesystem" "bronze" {
  depends_on         = [azurerm_role_assignment.deployer_datalake_owner]
  name               = "bronze"
  storage_account_id = azurerm_storage_account.datalake.id
}

resource "azurerm_storage_data_lake_gen2_filesystem" "silver" {
  depends_on         = [azurerm_role_assignment.deployer_datalake_owner]
  name               = "silver"
  storage_account_id = azurerm_storage_account.datalake.id
}

resource "azurerm_storage_data_lake_gen2_filesystem" "gold" {
  depends_on         = [azurerm_role_assignment.deployer_datalake_owner]
  name               = "gold"
  storage_account_id = azurerm_storage_account.datalake.id
}

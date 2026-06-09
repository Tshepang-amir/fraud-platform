# Module: postgres
# Azure PostgreSQL Flexible Server B_Standard_B1ms (~$13/month)
# STOP the server when not actively using to save credits.
# pgvector extension is allowlisted so the app can run: CREATE EXTENSION vector;

resource "azurerm_postgresql_flexible_server" "main" {
  name                   = "psql-fraud-${var.suffix}"
  resource_group_name    = var.resource_group_name
  location               = var.location
  version                = "16"
  administrator_login    = var.admin_login
  administrator_password = var.admin_password
  sku_name               = "B_Standard_B1ms"  # 1 vCore, 2 GiB RAM — cheapest tier
  storage_mb             = 32768              # 32 GB minimum
  backup_retention_days  = 7
  geo_redundant_backup_enabled = false

  authentication {
    active_directory_auth_enabled = false
    password_auth_enabled         = true
  }

  tags = var.tags
}

resource "azurerm_postgresql_flexible_server_database" "fraud_platform" {
  name      = "fraud_platform"
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "UTF8"
}

# Allowlist pgvector so the app can run: CREATE EXTENSION vector;
resource "azurerm_postgresql_flexible_server_configuration" "pgvector" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR"
}

# Allow all Azure services (Container Apps, Azure Functions) to connect.
# The 0.0.0.0/0.0.0.0 rule is Azure's special "allow Azure services" flag.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# Optional: allow developer laptop for direct access (pgAdmin, psql)
# Set developer_ip in terraform.tfvars to enable
resource "azurerm_postgresql_flexible_server_firewall_rule" "developer" {
  count            = var.developer_ip != "" ? 1 : 0
  name             = "AllowDeveloper"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = var.developer_ip
  end_ip_address   = var.developer_ip
}

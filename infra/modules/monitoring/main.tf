# Module: monitoring
# Creates Log Analytics Workspace + Application Insights
# Used by Container Apps environment and the FastAPI OpenTelemetry exporter

resource "azurerm_log_analytics_workspace" "main" {
  name                = "law-fraud-${var.suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-fraud-${var.suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = var.tags
}

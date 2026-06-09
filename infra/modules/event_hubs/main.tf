# Module: event_hubs
# Azure Event Hubs Basic tier — Kafka-compatible transaction stream ingestion
# Basic tier: 1-day retention, $Default consumer group only, ~$0.015/million events

resource "azurerm_eventhub_namespace" "main" {
  name                = "evhns-fraud-${var.suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "Basic"
  capacity            = 1
  tags                = var.tags
}

resource "azurerm_eventhub" "transactions" {
  name                = "transactions"
  namespace_name      = azurerm_eventhub_namespace.main.name
  resource_group_name = var.resource_group_name
  partition_count     = 2   # minimum for Basic tier
  message_retention   = 1   # 1 day, maximum on Basic tier
}

# Separate send/listen SAS policies follow least-privilege principle
resource "azurerm_eventhub_authorization_rule" "producer" {
  name                = "fraud-producer"
  namespace_name      = azurerm_eventhub_namespace.main.name
  eventhub_name       = azurerm_eventhub.transactions.name
  resource_group_name = var.resource_group_name
  listen              = false
  send                = true
  manage              = false
}

resource "azurerm_eventhub_authorization_rule" "consumer" {
  name                = "fraud-consumer"
  namespace_name      = azurerm_eventhub_namespace.main.name
  eventhub_name       = azurerm_eventhub.transactions.name
  resource_group_name = var.resource_group_name
  listen              = true
  send                = false
  manage              = false
}

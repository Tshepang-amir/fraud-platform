# Terraform — Fraud Platform Azure Infrastructure
# Modules: monitoring, data_lake, event_hubs, postgres, keyvault, container_apps

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.90"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "azurerm" {
    # Populated via -backend-config=backend.hcl at terraform init
    # Run infra/bootstrap.ps1 first to create the state storage account
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = true
    }
  }
  subscription_id = var.subscription_id
}

# Current caller identity (used for Key Vault access policy + ADLS RBAC)
data "azurerm_client_config" "current" {}

# 8-char hex suffix for globally unique resource names (stable across applies)
resource "random_id" "suffix" {
  byte_length = 4
}

# Auto-generated PostgreSQL admin password — stored in Key Vault, never in code
resource "random_password" "postgres" {
  length           = 24
  special          = true
  override_special = "!#$%&*()-_=+[]{}:?"
  min_lower        = 3
  min_upper        = 3
  min_numeric      = 3
  min_special      = 2
}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.common_tags
}

module "monitoring" {
  source              = "./modules/monitoring"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = var.common_tags
  suffix              = random_id.suffix.hex
}

module "data_lake" {
  source              = "./modules/data_lake"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = var.common_tags
  suffix              = random_id.suffix.hex
}

module "event_hubs" {
  source              = "./modules/event_hubs"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = var.common_tags
  suffix              = random_id.suffix.hex
}

module "postgres" {
  source              = "./modules/postgres"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = var.common_tags
  suffix              = random_id.suffix.hex
  admin_login         = var.postgres_admin_login
  admin_password      = random_password.postgres.result
  developer_ip        = var.developer_ip
}

module "keyvault" {
  source              = "./modules/keyvault"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = var.common_tags
  suffix              = random_id.suffix.hex
  tenant_id           = data.azurerm_client_config.current.tenant_id
  deployer_object_id  = data.azurerm_client_config.current.object_id

  postgres_password          = random_password.postgres.result
  eventhub_connection_string = module.event_hubs.listen_connection_string
}

module "container_apps" {
  source                           = "./modules/container_apps"
  resource_group_name              = azurerm_resource_group.main.name
  location                         = var.location
  tags                             = var.common_tags
  suffix                           = random_id.suffix.hex
  log_analytics_workspace_id       = module.monitoring.log_analytics_workspace_id
  log_analytics_primary_shared_key = module.monitoring.log_analytics_primary_shared_key
  postgres_fqdn                    = module.postgres.server_fqdn
  postgres_password                = random_password.postgres.result
}

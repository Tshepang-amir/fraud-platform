variable "resource_group_name" { type = string }
variable "location"            { type = string }
variable "suffix"              { type = string }
variable "tags"                { type = map(string) }

variable "tenant_id" {
  description = "Azure AD tenant ID (from data.azurerm_client_config.current)"
  type        = string
}

variable "deployer_object_id" {
  description = "Object ID of the Terraform deployer (CLI user or OIDC service principal)"
  type        = string
}

variable "postgres_password" {
  description = "Auto-generated Postgres admin password to store as a secret"
  type        = string
  sensitive   = true
}

variable "eventhub_connection_string" {
  description = "Event Hubs listen-only connection string to store as a secret"
  type        = string
  sensitive   = true
}

variable "resource_group_name" { type = string }
variable "location"            { type = string }
variable "suffix"              { type = string }
variable "tags"                { type = map(string) }

variable "log_analytics_workspace_id" {
  description = "Log Analytics workspace ID (from monitoring module)"
  type        = string
}

variable "log_analytics_primary_shared_key" {
  description = "Log Analytics primary shared key (from monitoring module)"
  type        = string
  sensitive   = true
}

# Added in Day 9: needed to configure the scoring Container App
variable "postgres_fqdn" {
  description = "Azure Postgres Flexible Server FQDN for Feast online store + decision log"
  type        = string
}

variable "postgres_password" {
  description = "Postgres admin password (passed from Terraform random_password resource)"
  type        = string
  sensitive   = true
}

# Terraform Variables — Fraud Platform

variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "resource_group_name" {
  description = "Name of the Azure resource group for all project resources"
  type        = string
  default     = "rg-fraud-platform"
}

variable "location" {
  description = "Azure region for all resources"
  type        = string
  default     = "southafricanorth"
}

variable "postgres_admin_login" {
  description = "PostgreSQL Flexible Server administrator login name"
  type        = string
  default     = "fraudadmin"
}

variable "developer_ip" {
  description = "Your public IP address for direct PostgreSQL access from local machine. Leave empty to allow only Azure services."
  type        = string
  default     = ""
}

variable "common_tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default = {
    project     = "fraud-platform"
    environment = "dev"
    owner       = "tsapang-mashego"
    cost-center = "student-credits"
  }
}

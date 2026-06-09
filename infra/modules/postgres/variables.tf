variable "resource_group_name" { type = string }
variable "location"            { type = string }
variable "suffix"              { type = string }
variable "tags"                { type = map(string) }

variable "admin_login" {
  description = "PostgreSQL administrator username"
  type        = string
}

variable "admin_password" {
  description = "PostgreSQL administrator password (auto-generated in root module)"
  type        = string
  sensitive   = true
}

variable "developer_ip" {
  description = "Developer public IP for firewall rule. Empty = disabled."
  type        = string
  default     = ""
}

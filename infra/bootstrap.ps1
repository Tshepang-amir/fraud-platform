# infra/bootstrap.ps1
# Creates the Azure storage account used to hold Terraform remote state.
# Run this ONCE before your first `terraform init`.
#
# Usage (from repo root):
#   .\infra\bootstrap.ps1 -SubscriptionId "6ed44d73-c305-4a7f-b5b1-606a22f98490"

param(
  [Parameter(Mandatory)]
  [string]$SubscriptionId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Verify az login ──────────────────────────────────────────────────────────
Write-Host "Checking Azure login..."
$accountJson = az account show --subscription $SubscriptionId 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Not logged in or subscription not found. Run 'az login' first, then retry."
    exit 1
}
$account = $accountJson | ConvertFrom-Json
Write-Host "OK — using subscription: $($account.name) ($SubscriptionId)"

# ── Config ───────────────────────────────────────────────────────────────────
$chars     = 'abcdefghijklmnopqrstuvwxyz0123456789'
$suffix    = -join (1..6 | ForEach-Object { $chars[(Get-Random -Minimum 0 -Maximum $chars.Length)] })
$rgName    = "rg-tfstate-fraud"
$saName    = "stterraform$suffix"   # max 24 chars, globally unique
$container = "tfstate"
$location  = "southafricanorth"

# ── Create resources ─────────────────────────────────────────────────────────
Write-Host "Creating resource group: $rgName"
az group create `
  --name $rgName `
  --location $location `
  --subscription $SubscriptionId | Out-Null

Write-Host "Creating storage account: $saName  (this takes ~30 seconds)"
az storage account create `
  --name $saName `
  --resource-group $rgName `
  --location $location `
  --sku Standard_LRS `
  --kind StorageV2 `
  --min-tls-version TLS1_2 `
  --subscription $SubscriptionId | Out-Null

Write-Host "Creating blob container: $container"
az storage container create `
  --name $container `
  --account-name $saName `
  --auth-mode login `
  --subscription $SubscriptionId | Out-Null

# ── Write backend.hcl ────────────────────────────────────────────────────────
$backendHcl = @"
resource_group_name  = "$rgName"
storage_account_name = "$saName"
container_name       = "$container"
key                  = "fraud-platform.tfstate"
"@

$backendPath = Join-Path $PSScriptRoot "backend.hcl"
$backendHcl | Out-File -FilePath $backendPath -Encoding utf8 -NoNewline

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "backend.hcl written to: $backendPath"
Write-Host ""
Write-Host "Next steps — run from the infra/ directory:"
Write-Host ""
Write-Host "  terraform init -backend-config=backend.hcl"
Write-Host "  terraform plan -var-file=terraform.tfvars"
Write-Host "  terraform apply -var-file=terraform.tfvars"

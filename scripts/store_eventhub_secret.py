"""Fetch Event Hubs connection string from Key Vault and store it in Databricks.

Usage:
    python scripts/store_eventhub_secret.py

Prerequisites:
    az login (or az login --use-device-code)
    pip install azure-keyvault-secrets azure-identity

If a corporate TLS proxy blocks Azure SDK calls, set:
    AZURE_CONNECTION_VERIFY=false
"""

from __future__ import annotations

import getpass
import logging
import os
from typing import Any

import requests
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import AzureCliCredential
from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)

KEYVAULT_URL = "https://kv-fraud-f95d0b0e.vault.azure.net/"
KV_SECRET_NAME = "eventhub-connection-string"  # noqa: S105 - Key Vault secret name only
DATABRICKS_HOST = "https://adb-7405604945524635.15.azuredatabricks.net"
DATABRICKS_SCOPE = "fraud-platform"

# Read PAT from env or prompt.
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")


def _connection_verify() -> bool:
    value = os.getenv("AZURE_CONNECTION_VERIFY", "true").strip().lower()
    return value not in {"0", "false", "no"}


def _warn_if_tls_disabled(verify_tls: bool) -> None:
    if not verify_tls:
        logger.warning("TLS certificate verification disabled for Azure/Databricks calls")


def get_eventhub_conn_string() -> str:
    """Read the Event Hubs connection string from Azure Key Vault."""
    verify_tls = _connection_verify()
    _warn_if_tls_disabled(verify_tls)

    logger.info("Fetching '%s' from Key Vault", KV_SECRET_NAME)
    credential = AzureCliCredential()
    transport = RequestsTransport(connection_verify=verify_tls)
    client = SecretClient(vault_url=KEYVAULT_URL, credential=credential, transport=transport)
    secret = client.get_secret(KV_SECRET_NAME)
    value = secret.value
    if not value:
        raise RuntimeError("Secret value is empty; check Terraform output")
    logger.info("Fetched connection string (%d chars)", len(value))
    return value


def _databricks_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def store_databricks_secret(scope: str, key: str, value: str, token: str) -> None:
    """Store a string secret in a Databricks secret scope."""
    url = f"{DATABRICKS_HOST}/api/2.0/secrets/put"
    payload = {"scope": scope, "key": key, "string_value": value}
    resp = requests.post(
        url,
        headers=_databricks_headers(token),
        json=payload,
        verify=_connection_verify(),
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Databricks API error {resp.status_code}: {resp.text}")
    logger.info("Stored secret '%s' in scope '%s'", key, scope)


def list_databricks_secrets(scope: str, token: str) -> list[dict[str, Any]]:
    """Return metadata for secrets in a Databricks scope."""
    url = f"{DATABRICKS_HOST}/api/2.0/secrets/list"
    resp = requests.get(
        url,
        headers=_databricks_headers(token),
        params={"scope": scope},
        verify=_connection_verify(),
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("Could not list secrets: %s", resp.status_code)
        return []
    secrets: list[dict[str, Any]] = resp.json().get("secrets", [])
    return secrets


def main() -> None:
    token = DATABRICKS_TOKEN or getpass.getpass("Enter Databricks PAT: ").strip()
    conn_string = get_eventhub_conn_string()

    logger.info("Storing Event Hubs connection string in Databricks scope '%s'", DATABRICKS_SCOPE)
    store_databricks_secret(DATABRICKS_SCOPE, "eventhub-producer-conn", conn_string, token)

    logger.info("Secrets currently in scope '%s':", DATABRICKS_SCOPE)
    for secret in list_databricks_secrets(DATABRICKS_SCOPE, token):
        logger.info(
            "  %s (last updated: %s)",
            secret["key"],
            secret.get("last_updated_timestamp", "n/a"),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    main()

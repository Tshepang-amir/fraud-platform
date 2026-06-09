"""Download Feast parquet from ADLS gold container to data/feast/ locally.

Authentication uses the signed-in Azure CLI identity and ADLS RBAC. No storage
account keys are stored in this repo. Run `az login` before using this helper.

If a corporate TLS proxy blocks Azure SDK calls, set:
    AZURE_CONNECTION_VERIFY=false
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import pandas as pd
from azure.core.exceptions import AzureError
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeFileSystemClient, DataLakeServiceClient

logger = logging.getLogger(__name__)

STORAGE_ACCOUNT = os.getenv("ADLS_STORAGE_ACCOUNT", "stfraudf95d0b0e")
CONTAINER = os.getenv("ADLS_FEAST_CONTAINER", "gold")
REMOTE_PATH = os.getenv("ADLS_FEAST_REMOTE_PATH", "feast/card_transaction_stats.parquet")
LOCAL_PATH = Path(
    os.getenv(
        "ADLS_FEAST_LOCAL_PATH",
        str(Path(__file__).parent.parent / "data" / "feast" / "card_transaction_stats.parquet"),
    )
)


def _connection_verify() -> bool:
    value = os.getenv("AZURE_CONNECTION_VERIFY", "true").strip().lower()
    return value not in {"0", "false", "no"}


def _download_single_file(
    fs_client: DataLakeFileSystemClient, remote_path: str, local_path: Path
) -> None:
    file_client = fs_client.get_file_client(remote_path)
    download = file_client.download_file()
    with local_path.open("wb") as f:
        download.readinto(f)


def _download_spark_parquet_dir(
    fs_client: DataLakeFileSystemClient, remote_path: str, local_path: Path
) -> None:
    paths = fs_client.get_paths(path=remote_path, recursive=False)
    part_paths = sorted(
        path.name
        for path in paths
        if not path.is_directory
        and Path(path.name).name.startswith("part-")
        and Path(path.name).suffix == ".parquet"
    )
    if not part_paths:
        raise FileNotFoundError(f"No Spark parquet part files found under {remote_path}")

    logger.info("Found %d Spark parquet part files under %s", len(part_paths), remote_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        frames = []
        for part_path in part_paths:
            part_local = tmp_path / Path(part_path).name
            _download_single_file(fs_client, part_path, part_local)
            frames.append(pd.read_parquet(part_local))

    combined = pd.concat(frames, ignore_index=True)
    combined.to_parquet(local_path, index=False)


def download_feast_parquet() -> Path:
    """Download the Feast parquet from ADLS using Azure CLI credentials."""
    verify_tls = _connection_verify()
    if not verify_tls:
        logger.warning("TLS certificate verification disabled for Azure SDK transport")

    credential = AzureCliCredential()
    transport = RequestsTransport(connection_verify=verify_tls)
    service = DataLakeServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.dfs.core.windows.net",
        credential=credential,
        transport=transport,
    )

    fs_client = service.get_file_system_client(CONTAINER)

    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading %s/%s from ADLS account %s", CONTAINER, REMOTE_PATH, STORAGE_ACCOUNT)
    try:
        _download_single_file(fs_client, REMOTE_PATH, LOCAL_PATH)
    except AzureError as exc:
        logger.info("Direct parquet download failed; trying Spark directory layout: %s", exc)
        _download_spark_parquet_dir(fs_client, REMOTE_PATH, LOCAL_PATH)

    size_mb = LOCAL_PATH.stat().st_size / 1024 / 1024
    logger.info("Saved to %s (%.1f MB)", LOCAL_PATH, size_mb)
    return LOCAL_PATH


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    download_feast_parquet()

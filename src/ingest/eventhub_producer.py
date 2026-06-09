"""PaySim → Event Hubs replay producer.

Reads a PaySim CSV, maps it to IEEE-CIS schema, then replays transactions
to Azure Event Hubs in chronological order at a configurable speed multiplier.

Usage (from repo root):
    python -m src.ingest.eventhub_producer \\
        --paysim data/paysim/PS_log.csv \\
        --speed 100 \\
        --max-events 1000

Environment variables:
    EVENTHUB_CONN_STR   Full Event Hubs connection string (with EntityPath).
                        If not set, read from .env or Azure Key Vault at
                        https://kv-fraud-f95d0b0e.vault.azure.net/
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

from src.ingest.paysim_to_ieee import load_paysim, map_paysim_to_ieee

logger = logging.getLogger(__name__)

# Columns included in each Event Hubs message (keep payload small)
_PAYLOAD_COLS = [
    "TransactionID",
    "TransactionDT",
    "TransactionAmt",
    "ProductCD",
    "card1",
    "card4",
    "card6",
    "addr1",
    "P_emaildomain",
    "isFraud",
    # Rolling-window features are computed by Feast, not sent in the event
]

_KEYVAULT_URL = "https://kv-fraud-f95d0b0e.vault.azure.net/"
_KV_SECRET_NAME = "eventhub-connection-string"  # noqa: S105 — not a password, it's a secret name


def _get_conn_str() -> str:
    """Resolve Event Hubs connection string from env or Key Vault."""
    conn = os.environ.get("EVENTHUB_CONN_STR")
    if conn:
        return conn

    logger.info("EVENTHUB_CONN_STR not set — fetching from Key Vault")
    try:
        import warnings

        import urllib3

        warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

        from azure.core.pipeline.transport import RequestsTransport
        from azure.identity import AzureCliCredential
        from azure.keyvault.secrets import SecretClient

        transport = RequestsTransport(connection_verify=False)
        credential = AzureCliCredential()
        client = SecretClient(vault_url=_KEYVAULT_URL, credential=credential, transport=transport)
        secret = client.get_secret(_KV_SECRET_NAME)
        if not secret.value:
            raise RuntimeError("Key Vault returned empty secret")
        return secret.value
    except Exception as exc:
        raise RuntimeError(
            "Could not resolve Event Hubs connection string. "
            "Set EVENTHUB_CONN_STR or run 'az login' first."
        ) from exc


def replay(
    paysim_path: str | Path,
    *,
    speed_multiplier: float = 1.0,
    max_events: int | None = None,
    batch_size: int = 50,
    seed: int = 42,
    dry_run: bool = False,
) -> int:
    """Replay PaySim transactions to Azure Event Hubs.

    Args:
        paysim_path: Path to PaySim CSV.
        speed_multiplier: Time compression factor.  1.0 = real-time,
            100.0 = 100x faster, 0 = fire-and-forget (no sleep).
        max_events: Stop after this many events (None = all).
        batch_size: Events per Event Hubs batch.
        seed: Random seed for paysim_to_ieee mapper.
        dry_run: If True, skip actual Event Hubs send (useful for testing).

    Returns:
        Number of events sent.
    """
    from azure.eventhub import EventHubProducerClient

    paysim_df = load_paysim(paysim_path)
    ieee_df = map_paysim_to_ieee(paysim_df, seed=seed)

    # Sort by TransactionDT so events arrive in chronological order
    ieee_df = ieee_df.sort_values("TransactionDT").reset_index(drop=True)

    if max_events is not None:
        ieee_df = ieee_df.head(max_events)

    payload_cols = [c for c in _PAYLOAD_COLS if c in ieee_df.columns]
    events_df = ieee_df[payload_cols]

    conn_str = "" if dry_run else _get_conn_str()

    producer = None
    if not dry_run:
        producer = EventHubProducerClient.from_connection_string(conn_str=conn_str)

    total_sent = 0
    prev_dt: float | None = None
    prev_wall: float = time.monotonic()

    try:
        batch_records = []
        for _, row in events_df.iterrows():
            curr_dt = float(row["TransactionDT"])

            # Throttle to simulate real-time (or compressed) replay
            if speed_multiplier > 0 and prev_dt is not None:
                sim_gap = (curr_dt - prev_dt) / speed_multiplier
                elapsed = time.monotonic() - prev_wall
                sleep_for = sim_gap - elapsed
                if sleep_for > 0:
                    time.sleep(sleep_for)

            prev_dt = curr_dt
            prev_wall = time.monotonic()

            batch_records.append(row.to_dict())

            if len(batch_records) >= batch_size:
                total_sent += _send_batch(producer, batch_records, dry_run)
                batch_records = []
                logger.info("Sent %d events so far", total_sent)

        # Flush remaining
        if batch_records:
            total_sent += _send_batch(producer, batch_records, dry_run)

    finally:
        if producer is not None:
            producer.close()

    logger.info("Replay complete. Total events sent: %d", total_sent)
    return total_sent


def _send_batch(
    producer: object,
    records: list[dict],
    dry_run: bool,
) -> int:
    if dry_run:
        for rec in records:
            logger.debug("DRY-RUN event: %s", json.dumps(rec, default=str))
        return len(records)

    from azure.eventhub import EventData, EventHubProducerClient

    assert isinstance(producer, EventHubProducerClient)
    batch = producer.create_batch()
    for rec in records:
        payload = json.dumps(rec, default=str).encode()
        batch.add(EventData(payload))
    producer.send_batch(batch)
    return len(records)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PaySim → Event Hubs replay producer")
    parser.add_argument("--paysim", required=True, help="Path to PaySim CSV")
    parser.add_argument(
        "--speed",
        type=float,
        default=100.0,
        help="Speed multiplier (default 100x faster than real time; 0 = no sleep)",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="Stop after N events (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Events per Event Hubs batch (default 50)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log events without sending to Event Hubs",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )
    args = _parse_args()
    sent = replay(
        paysim_path=args.paysim,
        speed_multiplier=args.speed,
        max_events=args.max_events,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    print(f"Done. {sent} events sent.")

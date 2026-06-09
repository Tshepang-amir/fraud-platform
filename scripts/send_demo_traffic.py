# ruff: noqa: T201
"""Send safe live scoring traffic for the Day 13 demo recording.

This script is intentionally small and secret-free. It only needs the public
staging URL and sends synthetic `/score` requests so Grafana panels have fresh
request, latency, decision, and fraud-score data during the demo.

Example:
    python scripts/send_demo_traffic.py --url https://<staging-fqdn> \
        --requests 250 --concurrency 25
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_URL = os.getenv("STAGING_URL", "").rstrip("/")

SAMPLE_CARDS = [
    10057,
    10486,
    10989,
    11839,
    12544,
    13926,
    15066,
    15885,
    17188,
    18375,
]
PRODUCT_CODES = ["W", "C", "H", "R", "S"]


@dataclass(frozen=True)
class ScoreResult:
    ok: bool
    status_code: int | None
    decision: str | None
    api_latency_ms: float | None
    client_latency_ms: float
    error: str | None = None


def parse_bool(value: str) -> bool:
    """Parse CLI booleans written as true/false, 1/0, yes/no."""
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected a boolean value, got {value!r}")


def percentile(values: list[float], q: float) -> float:
    """Return a nearest-rank percentile, or NaN for an empty list."""
    if not values:
        return float("nan")
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    ordered = sorted(values)
    rank = max(1, math.ceil((q / 100) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def build_payload(index: int) -> dict[str, Any]:
    """Build a deterministic synthetic transaction payload."""
    card = SAMPLE_CARDS[index % len(SAMPLE_CARDS)]
    amount = round(9.99 + ((index * 37) % 5_000) / 10, 2)
    hour = index % 24
    product = PRODUCT_CODES[index % len(PRODUCT_CODES)]

    return {
        "transaction_id": f"demo-live-{index:05d}",
        "card1": card,
        "TransactionAmt": amount,
        "ProductCD": product,
        "addr1": 299,
        "C1": index % 8,
        "C2": (index * 3) % 11,
        "D1": hour,
        "Transaction_hour": hour,
    }


def score_once(client: httpx.Client, index: int) -> ScoreResult:
    """Send one scoring request and capture both client and API latency."""
    started = time.perf_counter()
    try:
        response = client.post("/score", json=build_payload(index))
        client_latency_ms = (time.perf_counter() - started) * 1_000
        if response.status_code != 200:
            return ScoreResult(
                ok=False,
                status_code=response.status_code,
                decision=None,
                api_latency_ms=None,
                client_latency_ms=client_latency_ms,
                error=response.text[:300],
            )

        body = response.json()
        return ScoreResult(
            ok=True,
            status_code=response.status_code,
            decision=body.get("decision"),
            api_latency_ms=float(body["latency_ms"]),
            client_latency_ms=client_latency_ms,
        )
    except Exception as exc:
        client_latency_ms = (time.perf_counter() - started) * 1_000
        return ScoreResult(
            ok=False,
            status_code=None,
            decision=None,
            api_latency_ms=None,
            client_latency_ms=client_latency_ms,
            error=str(exc),
        )


def run_traffic(
    base_url: str,
    request_count: int,
    concurrency: int,
    verify_tls: bool,
    trust_env: bool,
) -> list[ScoreResult]:
    """Run concurrent scoring traffic against the staging API."""
    with httpx.Client(
        base_url=base_url,
        timeout=30.0,
        verify=verify_tls,
        trust_env=trust_env,
    ) as client, ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(score_once, client, i) for i in range(request_count)]
        return [future.result() for future in as_completed(futures)]


def format_ms(value: float) -> str:
    if math.isnan(value):
        return "n/a"
    return f"{value:.2f}ms"


def print_summary(results: list[ScoreResult], elapsed_s: float) -> None:
    ok_results = [result for result in results if result.ok]
    failed_results = [result for result in results if not result.ok]
    client_latencies = [result.client_latency_ms for result in ok_results]
    api_latencies = [
        result.api_latency_ms for result in ok_results if result.api_latency_ms is not None
    ]
    decisions = Counter(result.decision for result in ok_results if result.decision)

    print("\nDemo traffic summary")
    print("--------------------")
    print(f"Requests sent: {len(results)}")
    print(f"Successful:     {len(ok_results)}")
    print(f"Failed:         {len(failed_results)}")
    print(f"Elapsed:        {elapsed_s:.2f}s")
    if elapsed_s > 0:
        print(f"Throughput:     {len(results) / elapsed_s:.2f} req/s")

    print("\nDecision distribution")
    if decisions:
        for decision, count in sorted(decisions.items()):
            print(f"- {decision}: {count}")
    else:
        print("- n/a")

    print("\nClient-observed latency")
    if client_latencies:
        print(f"- avg: {format_ms(statistics.mean(client_latencies))}")
        print(f"- p50: {format_ms(percentile(client_latencies, 50))}")
        print(f"- p95: {format_ms(percentile(client_latencies, 95))}")
        print(f"- p99: {format_ms(percentile(client_latencies, 99))}")
    else:
        print("- n/a")

    print("\nAPI-reported scoring latency")
    if api_latencies:
        print(f"- avg: {format_ms(statistics.mean(api_latencies))}")
        print(f"- p50: {format_ms(percentile(api_latencies, 50))}")
        print(f"- p95: {format_ms(percentile(api_latencies, 95))}")
        print(f"- p99: {format_ms(percentile(api_latencies, 99))}")
    else:
        print("- n/a")

    if failed_results:
        print("\nFirst failures")
        for result in failed_results[:5]:
            status = result.status_code if result.status_code is not None else "no response"
            print(f"- {status}: {result.error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Public staging base URL. Defaults to STAGING_URL.",
    )
    parser.add_argument("--requests", type=int, default=250, help="Number of score requests.")
    parser.add_argument("--concurrency", type=int, default=25, help="Concurrent workers.")
    parser.add_argument("--verify-tls", type=parse_bool, default=True)
    parser.add_argument("--trust-env", type=parse_bool, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.url.rstrip("/")
    if not base_url:
        print("Set STAGING_URL or pass --url https://<staging-fqdn>.")
        return 2
    if args.requests < 1:
        print("--requests must be at least 1.")
        return 2
    if args.concurrency < 1:
        print("--concurrency must be at least 1.")
        return 2

    print(f"Sending {args.requests} demo requests to {base_url}")
    print(f"Concurrency: {args.concurrency}")
    started = time.perf_counter()
    results = run_traffic(
        base_url=base_url,
        request_count=args.requests,
        concurrency=args.concurrency,
        verify_tls=args.verify_tls,
        trust_env=args.trust_env,
    )
    elapsed_s = time.perf_counter() - started
    print_summary(results, elapsed_s)

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())

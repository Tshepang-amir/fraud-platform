"""Locust load test: verify p95 latency < 100ms at 50 concurrent users.

Run against a locally running server:
    locust -f tests/load/locustfile.py --host=http://localhost:8000

Run headless (CI):
    locust -f tests/load/locustfile.py --host=http://localhost:8000 \
           --users 50 --spawn-rate 10 --run-time 60s --headless \
           --only-summary --csv=tests/load/results
"""

from __future__ import annotations

import os
import random

import urllib3
from locust import HttpUser, between, task

# Representative transaction payloads for load testing
_PRODUCTS = ["W", "H", "C", "S", "R"]
_CARDS = [10000 + i for i in range(500)]  # 500 distinct cards
_VERIFY_TLS = os.environ.get("LOCUST_VERIFY_TLS", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}
_TRUST_ENV = os.environ.get("LOCUST_TRUST_ENV", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}

if not _VERIFY_TLS:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class FraudScorerUser(HttpUser):
    """Simulates concurrent callers hitting the /score endpoint."""

    wait_time = between(0.05, 0.2)  # 50-200 ms think-time between requests

    def on_start(self) -> None:
        """Apply local proxy/TLS overrides when explicitly requested."""
        self.client.verify = _VERIFY_TLS
        self.client.trust_env = _TRUST_ENV

    @task(10)
    def score_transaction(self) -> None:
        """POST /score — main load-generating task (weight 10)."""
        payload = {
            "transaction_id": f"load-{random.randint(0, 10_000_000)}",
            "card1": random.choice(_CARDS),
            "TransactionAmt": round(random.uniform(1.0, 2000.0), 2),
            "ProductCD": random.choice(_PRODUCTS),
        }
        with self.client.post("/score", json=payload, catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"status {resp.status_code}: {resp.text[:120]}")

    @task(1)
    def health_check(self) -> None:
        """GET /health — occasional liveness probe (weight 1)."""
        self.client.get("/health")

    @task(1)
    def ready_check(self) -> None:
        """GET /ready — occasional readiness probe (weight 1)."""
        self.client.get("/ready")

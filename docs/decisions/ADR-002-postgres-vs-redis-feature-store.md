# ADR-002: Postgres vs Redis for Feast Online Store

## Status
Accepted

## Context
Feast requires an online store backend for low-latency feature retrieval
at inference time. Redis Cache Basic (~$16/month always-on) and Postgres
Flexible Server B1ms (~$13/month, stoppable) are both supported.

## Decision
Postgres Flexible Server B1ms.

## Rationale
- Postgres B1ms costs ~$13/month and can be stopped when idle
- Redis Cache Basic costs ~$16/month always-on (no stop capability)
- Feast supports Postgres as an online store backend
- For demo-scale feature retrieval (<100 req/s), Postgres sub-10ms latency is sufficient
- Postgres also serves as the decision log + shadow log store (one fewer service)

## Consequences
- Upgrade to Redis only if online store becomes a latency bottleneck
- Monitor feature retrieval latency in Grafana dashboard

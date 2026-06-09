# ADR-004: Shadow Mode vs A/B Test for Challenger Validation

## Status
Accepted

## Context
The platform needs a method to validate a challenger model before promoting
it to production. The two options are A/B testing (split traffic, some users
see challenger decisions) and shadow mode (100% traffic scored by both models,
only champion decisions are live).

## Decision
Shadow mode (100% traffic, 0% live decisions from challenger).

## Rationale
- In fraud detection, assigning customers to a potentially inferior model
  exposes them to undetected fraud — this is unacceptable
- Shadow mode validates the challenger on 100% of production traffic without
  any customer risk
- The statistical power is identical to A/B because all requests are scored
  by both models
- Nightly comparison on settled fraud labels provides ground-truth validation

## Consequences
- Challenger scoring adds latency if done synchronously — we use FastAPI
  BackgroundTasks to run it asynchronously (no impact on response time)
- Requires additional Postgres storage for shadow_decisions table
- Promotion decision takes longer (need settled labels, typically 7-14 days)

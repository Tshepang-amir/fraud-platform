# ADR-001: Event Hubs vs Apache Kafka

## Status
Accepted

## Context
The fraud platform requires a streaming event ingestion layer for real-time
transaction processing. The two candidates are Azure Event Hubs (managed) and
Apache Kafka (self-hosted on AKS).

## Decision
Azure Event Hubs Basic tier.

## Rationale
- Event Hubs is wire-compatible with Kafka protocol
- For portfolio-scale event volumes (<1 MB/s), the managed service eliminates
  cluster operations overhead
- Cost difference: Kafka on AKS ~$40/month, Event Hubs Basic ~$0.015/million events

## Consequences
- At sustained >10 MB/s or multi-cloud consumers, revisit Kafka
- Limited to 1 consumer group on Basic tier (sufficient for this use case)

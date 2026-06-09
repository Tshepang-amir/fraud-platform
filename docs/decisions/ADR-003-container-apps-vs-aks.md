# ADR-003: Container Apps vs AKS

## Status
Accepted

## Context
The fraud scoring service is a single stateless FastAPI application that
needs to be deployed to Azure with HTTPS, auto-scaling, and monitoring.

## Decision
Azure Container Apps.

## Rationale
- Container Apps provides auto-scaling, HTTPS, scale-to-zero, and Azure Monitor
  integration without Kubernetes management overhead
- AKS would cost ~$800/month for the minimum viable node pool
- Container Apps costs ~$0 at demo traffic (scale-to-zero)
- Single stateless service doesn't justify Kubernetes complexity

## Consequences
- If the platform grows to multiple microservices or requires custom networking,
  revisit AKS
- Container Apps revision management replaces Kubernetes deployment strategies

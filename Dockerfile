# ============================================================================
# Multi-stage Dockerfile — Fraud Platform Scoring Service
# Non-root user, image target < 400MB
# ============================================================================

# ── Stage 1: Builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY src/ src/
COPY pyproject.toml .

# ── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# LightGBM's native library depends on OpenMP at runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Security: non-root user
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid appuser --shell /bin/false appuser

WORKDIR /app

ENV MPLCONFIGDIR=/tmp/matplotlib

# Copy installed packages from builder
COPY --from=builder /install /usr/local
COPY --from=builder /build/src ./src
COPY --from=builder /build/pyproject.toml .

# Feature store definitions + local registry (needed by FeatureService at startup)
COPY feature_repo/ feature_repo/
# Override local-dev feature_store.yaml with the env-var-based production config
COPY feature_repo/feature_store_prod.yaml feature_repo/feature_store.yaml

# Trained model artifacts (11 MB — self-contained, no MLflow tracking server needed at runtime)
COPY mlruns/ mlruns/

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

EXPOSE 8000

CMD ["uvicorn", "src.serve.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]

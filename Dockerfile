FROM python:3.11-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir hatchling

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir --prefix=/install .


FROM python:3.11-slim

# Security: run as non-root
RUN groupadd --gid 1001 aumos && \
    useradd --uid 1001 --gid aumos --shell /bin/bash --create-home aumos

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=aumos:aumos src/ ./src/

USER aumos

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "aumos_vendor_intelligence.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

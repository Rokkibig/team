# Golden Architecture V5.1 - Production Dockerfile
# Multi-stage build for minimal final image

# Stage 1: Builder
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --user --no-cache-dir --no-warn-script-location \
    -r requirements.txt \
    bcrypt \
    prometheus-client

# Stage 2: Runtime
FROM python:3.11-slim as final

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash golden && \
    mkdir -p /app && \
    chown -R golden:golden /app

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder --chown=golden:golden /root/.local /home/golden/.local

# Copy application code
COPY --chown=golden:golden . .

# Set PATH to include user-installed packages
ENV PATH=/home/golden/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER golden

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Run application
CMD ["uvicorn", "demo_server:app", "--host", "0.0.0.0", "--port", "8000"]

# Multi-stage Dockerfile for Cuba Geocoder

# ===== Stage 1: Builder =====
FROM python:3.12-slim as builder

# Install system dependencies for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlite3-dev \
    libspatialite-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml requirements.txt ./

# Install Python dependencies and the package itself
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ===== Stage 2: Runtime =====
FROM python:3.12-slim as runtime

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-0 \
    libspatialite7 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --no-create-home --shell /bin/bash --uid 1000 geocoder

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=geocoder:geocoder src/ ./src/
COPY --chown=geocoder:geocoder pyproject.toml requirements.txt ./

# Create data directory
RUN mkdir -p /app/data && chown geocoder:geocoder /app/data

# Switch to non-root user
USER geocoder

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" || exit 1

# Run the application
CMD ["python", "-m", "geocoder.cli.main", "serve", "--host", "0.0.0.0", "--port", "8000"]
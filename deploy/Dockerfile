# Airgap runtime image: the geocoder with its full-Cuba data baked in.
#
# Distinct from the repo-root Dockerfile, which is a generic two-stage build
# that mounts data at runtime. This one BAKES the payload, so the resulting
# image answers queries with no volumes, no network, and no configuration.
FROM python:3.12-slim

# libexpat is what osmium links against; nothing here reaches the network at
# runtime. Slim keeps the image close to the payload size.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so the 575 MB payload below does not invalidate the pip
# layer on every code change.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[api]"

# The baked payload. deploy/bake-data.sh has already checkpointed the WAL and
# asserted both files are real; the .dockerignore keeps the -wal/-shm sidecars
# out, since a stale sidecar would shadow the checkpointed data.
COPY data/cuba_routing_full.db /app/data/cuba_routing_full.db
COPY data/tiles/cuba.pmtiles   /app/data/tiles/cuba.pmtiles

# GEOCODER_DATA_DIR is required, not cosmetic: the package is pip-installed into
# site-packages here, so config.py's derivation from __file__ would point
# DATA_DIR at /usr/local/lib/python3.12/data and the /tiles mount would 404.
ENV GEOCODER_DATA_DIR=/app/data \
    GEOCODER_DB_PATH=/app/data/cuba_routing_full.db \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "geocoder.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

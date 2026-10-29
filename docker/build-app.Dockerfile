# Build-only image: produces a baked, relocatable offline tarball.
# Never runs in production — the air-gapped VM runs the extracted venv natively.
FROM ubuntu:24.04
# APT_MIRROR: point apt at a fast/reachable Ubuntu mirror when archive.ubuntu.com
# is blocked or throttled (e.g. build on a cloud server →
# https://mirror.hetzner.com/ubuntu). Empty = stock mirrors.
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i -e "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}/packages|g" \
             -e "s|http://security.ubuntu.com/ubuntu|${APT_MIRROR}/security|g" \
             /etc/apt/sources.list.d/ubuntu.sources; \
    fi \
 && apt-get update && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv curl ca-certificates make tar sqlite3 \
 && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
WORKDIR /build

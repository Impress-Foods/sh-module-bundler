FROM python:3.11-slim-bookworm

# Install system dependencies needed for git-aggregator and general development
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-buildpackage \
    openssh-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install git-aggregator and PyYAML for runtime validation
RUN pip install --no-cache-dir git-aggregator pyyaml requests
RUN mkdir -m 777 -p /tmp/bundler/tmp_git_aggregate

WORKDIR /

FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-buildpackage \
    openssh-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv export --frozen | uv pip sync --system -

RUN mkdir -m 777 -p /tmp/bundler/tmp_git_aggregate

WORKDIR /

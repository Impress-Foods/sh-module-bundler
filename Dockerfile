FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --system

COPY entrypoint.py /entrypoint.py
RUN chmod +x /entrypoint.py
RUN mkdir -m 777 -p /tmp/bundler/tmp_git_aggregate

ENTRYPOINT [ "python", "/entrypoint.py" ]

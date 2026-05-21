FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir git-aggregator

COPY entrypoint.py /entrypoint.py

RUN chmod +x /entrypoint.py
RUN mkdir -m 777 -p /tmp/bundler/tmp_git_aggregate

ENTRYPOINT [ "python", "/entrypoint.py" ]
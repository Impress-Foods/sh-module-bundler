FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir git-aggregator

COPY entrypoint.py /entrypoint.py

RUN chmod +x /entrypoint.py

ENTRYPOINT [ "python", "/entrypoint.py" ]
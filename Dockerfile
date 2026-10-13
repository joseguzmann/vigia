FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIGIA_DB=/data/vigia.db \
    VIGIA_CONFIG=/app/config/vigia.yaml

# tzdata so TZ (e.g. America/Guayaquil) applies to report timestamps.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home --uid 1000 vigia && mkdir -p /data && chown vigia /data
COPY config ./config

USER vigia
VOLUME ["/data"]

HEALTHCHECK --interval=5m --timeout=20s --start-period=2m \
  CMD vigia health --check || exit 1

CMD ["vigia", "serve"]

# NOVA - Beyond the Mission | gameplay backend
#
#   docker build -t nova-backend .
#   docker run -p 8000:8000 nova-backend
#
# SQLite lives in /data (mount a volume to keep saves across restarts).

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/nova

# Dependencies first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY content ./content
COPY scripts ./scripts
COPY docker-entrypoint.sh /usr/local/bin/nova-entrypoint

RUN chmod +x /usr/local/bin/nova-entrypoint \
    && useradd --create-home --uid 10001 nova \
    && mkdir -p /data \
    && chown -R nova:nova /srv/nova /data

USER nova

ENV NOVA_ENVIRONMENT=prod \
    NOVA_DATABASE_URL=sqlite+aiosqlite:////data/nova.db \
    NOVA_AUTO_CREATE_TABLES=false \
    NOVA_LOG_JSON=true \
    NOVA_VALIDATE_CONTENT=true \
    NOVA_VALIDATE_CONTENT_STRICT=true

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["nova-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GWSC_ENV=production \
    GWSC_SSL_ENABLED=false \
    DATA_DIR=/data \
    GWSC_LOG_DIR=/logs \
    GWSC_BACKUP_DIR=/backups

WORKDIR /app

RUN groupadd --system gwsc \
    && useradd --system --gid gwsc --home-dir /app gwsc

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY gwsc_local_termux ./gwsc_local_termux
COPY main.py ./

RUN mkdir -p /data /logs /backups \
    && chown -R gwsc:gwsc /app /data /logs /backups

USER gwsc

EXPOSE 8443/tcp 6672/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8443/health', timeout=3)"

# Terminate TLS at a reverse proxy (nginx/Caddy) in production.
CMD ["python", "-m", "gwsc_local_termux.server"]

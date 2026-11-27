# Mottakar for målingar frå Arduino MKR NB 1500.
# Fungerer på både Intel/AMD (amd64) og ARM (arm64), som er dei vanlege NAS-prosessorane.
FROM python:3.14-slim
LABEL org.opencontainers.image.source="https://github.com/hausnes/mkr-mottak"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# CSV-filene blir lagra i /data. Mappa må eigast av brukaren som køyrer tenesta,
# slik at eit nytt Docker-volum (sjå compose.yaml) får rett eigar.
RUN useradd --system --no-create-home app && mkdir /data && chown app /data
USER app
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
# --proxy-headers: tenesta står bak NAS-en sin reverse proxy, som tek seg av HTTPS
CMD ["uvicorn", "app.main:lag_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

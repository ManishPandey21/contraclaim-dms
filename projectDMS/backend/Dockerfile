FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY rbac_backend/requirements.txt ./rbac_backend/requirements.txt
RUN pip install --upgrade pip && pip install -r rbac_backend/requirements.txt

COPY . .
RUN mkdir -p /app/uploads /app/logs
RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# --proxy-headers: derive request.client.host from X-Forwarded-For so per-IP
# rate limiting and audit logs see the real client, not the Apache gateway
# (without it, every login shares one IP bucket and attempt 61/15min locks
# everyone out). Trusting "*" is safe only because this port is never
# published: the backend sits on internal compose networks behind the gateway,
# and the host nginx overwrites X-Forwarded-For with $remote_addr so a client
# cannot spoof the leftmost entry.
CMD ["uvicorn", "rbac_backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

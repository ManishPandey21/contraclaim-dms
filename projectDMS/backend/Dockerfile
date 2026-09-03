FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TMPDIR=/app/tmp

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ghostscript \
        libmagic1 \
        libpq-dev \
        pngquant \
        poppler-utils \
        qpdf \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-osd \
        unpaper \
    && rm -rf /var/lib/apt/lists/*

COPY rbac_backend/requirements.txt ./rbac_backend/requirements.txt
RUN pip install --upgrade pip && pip install -r rbac_backend/requirements.txt

# The image runs uvicorn and nothing else; nothing at runtime installs a
# package. pip stays behind anyway, and Trivy reads its vendored manifest
# (pip/_vendor/vendor.txt) as if those pins were installed packages -
# msgpack==1.1.2 and setuptools==70.3.0, neither of which is importable
# here. Removing pip after the install removes the finding and the tool
# together. Kept as its own layer so the dependency install stays cached.
RUN pip uninstall -y pip

COPY . .
RUN mkdir -p /app/uploads /app/logs /app/tmp
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

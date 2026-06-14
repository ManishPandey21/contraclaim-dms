FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY rbac_backend/requirements.txt ./rbac_backend/requirements.txt
RUN pip install --upgrade pip && pip install -r rbac_backend/requirements.txt

COPY . .
RUN mkdir -p /app/uploads /app/logs
RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "rbac_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

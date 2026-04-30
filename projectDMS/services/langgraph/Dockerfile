FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY orchestrator.py ./
COPY ./app ./app

EXPOSE 9000

CMD ["uvicorn", "orchestrator:app", "--host", "0.0.0.0", "--port", "9000"]

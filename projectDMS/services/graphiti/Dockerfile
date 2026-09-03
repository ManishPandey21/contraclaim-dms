FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GRAPHITI_GRAPH_NAME=contraclaim

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# The image runs uvicorn and nothing else; nothing at runtime installs a
# package. pip stays behind anyway, and Trivy reads its vendored manifest
# (pip/_vendor/vendor.txt) as if those pins were installed packages -
# msgpack==1.1.2 and setuptools==70.3.0, neither of which is importable
# here. Removing pip after the install removes the finding and the tool
# together. Kept as its own layer so the dependency install stays cached.
RUN pip uninstall -y pip

COPY app.py ./
COPY ./app ./app

RUN mkdir -p /app/logs

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]

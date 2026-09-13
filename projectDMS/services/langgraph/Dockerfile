FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# The base image lags debian-security. Its packages are upgraded before anything
# else is installed, so a fix Debian has already published does not ship here as
# a fixed-but-unapplied finding. See backend/Dockerfile for why this is a
# blanket upgrade rather than a by-name list.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# The image runs uvicorn and nothing else; nothing at runtime installs a
# package, so pip is removed once the dependencies are in place. The removal
# has to happen in the SAME RUN instruction as the install. Trivy scans an
# image layer by layer and reads pip's vendored manifest
# (pip/_vendor/vendor.txt) as an installed package list - msgpack==1.1.2 and
# setuptools==70.3.0, neither of which is importable here and neither of
# which is the version actually installed (setuptools is 84.0.0; msgpack is
# absent). While the removal was its own layer, the install layer still
# carried that manifest, and the exact CI policy reported both as fixable
# HIGH findings against the shipped image even though the very next layer
# whited the file out. Installing and removing in one instruction means no
# layer ever contains the manifest, and that layer's opaque whiteout over
# site-packages/pip also masks the copy inherited from python:3.12-slim,
# which pins setuptools==70.3.0 too.
RUN pip install --upgrade pip && pip install -r requirements.txt && pip uninstall -y pip

COPY orchestrator.py ./
COPY ./app ./app

EXPOSE 9000

CMD ["uvicorn", "orchestrator:app", "--host", "0.0.0.0", "--port", "9000"]

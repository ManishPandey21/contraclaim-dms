FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TMPDIR=/app/tmp

WORKDIR /app

# `apt-get upgrade` before the install. The base image lags debian-security, and
# `--ignore-unfixed` cannot suppress a finding that HAS a fix - nor should it,
# because a fixed advisory against a package this image ships is exactly what
# the gate is for.
#
# THIS REPLACES THE BY-NAME UPGRADE LIST, and the reversal is the point.
# On 2026-09-13 the exact CI Trivy policy went red on a tree whose Dockerfiles
# had not changed - the vulnerability database moved. It was fixed by name,
# twice, and came back a third time:
#
#   backend    5 HIGH   gzip, libpcre2-8-0 x2, libsqlite3-0 x2      (trixie)
#   client     2 HIGH   libpcre2-8-0 x2                             (bookworm)
#   langgraph  12       the same three, PLUS perl-base 3 CRITICAL   (trixie)
#              (9 HIGH, 3 CRITICAL)
#
# Three images, three different lists, one advisory wave, one day. And the
# backend's list was right only by accident: it escaped perl-base because its
# own install list happens to pull a newer perl, which is not a property anyone
# chose or could rely on. A hand-maintained list is the part of this that goes
# stale, on Debian's schedule rather than on ours, and a release window that
# opens days from now would find it stale again.
#
# `apt-get upgrade` still changes the image only where a fix is actually needed -
# which is what the by-name comment set out to achieve. It lets apt decide which
# packages those are instead of a human who last looked on a particular day.
RUN apt-get update \
    && apt-get upgrade -y \
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
RUN pip install --upgrade pip && pip install -r rbac_backend/requirements.txt && pip uninstall -y pip

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

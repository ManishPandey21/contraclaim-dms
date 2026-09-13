FROM node:20-bookworm-slim AS builder
WORKDIR /app
# `.npmrc` sets engine-strict, and it has to arrive before `npm ci` runs.
# Without it this stage would keep building on whatever npm the base image
# happens to carry: npm 10 accepts this lockfile and npm 11 does not, so the
# major that resolved the shipped bundle was decided by a base-image bump rather
# than by anything in this repository (F-A8M-4). Bumping the tag to a node whose
# npm is out of range now fails here, loudly, instead of silently resolving the
# dependency tree a different way.
COPY package*.json .npmrc ./
# `npm ci` alone. This used to be followed by
# `npm install --no-save @rollup/rollup-linux-x64-gnu`, because the lockfile
# was generated on Windows and carried only the win32 rollup binaries - so the
# native binary that links the production bundle was fetched outside the
# lockfile, unpinned and unverified, on every build. The linux binary is in the
# lockfile now at rollup's exact version with its integrity hash, so the
# install is the lockfile and nothing else.
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-bookworm-slim
WORKDIR /app
# Two things happen here beyond installing the healthcheck's wget, and both are
# about what the shipped image carries rather than what it does.
#
# 1. The base image lags bookworm-security, so packages with a published fix are
#    upgraded by name rather than with a blanket `apt-get upgrade`: the image
#    changes only where a fix is actually needed.
#
#    The list is dated, not permanent. libgnutls30 and libcap2 were the whole of
#    it when this stage was written; on 2026-09-13 the exact CI Trivy policy
#    added libpcre2-8-0 (CVE-2026-86145, CVE-2026-89161, 10.42-1 ->
#    10.42-1+deb12u1) on a commit that changed nothing here - the vulnerability
#    database moved. The SAME advisory pair hit backend/Dockerfile's trixie base
#    on the same day, which is the point: a hand-maintained list is the part of
#    this that goes stale, and it will do so again on Debian's schedule rather
#    than on this repository's. Recorded as release debt with the two durable
#    options named (a base-image bump policy, or a blanket upgrade).
#
# 2. `node:*-slim` bundles the npm CLI, corepack and yarn. This stage runs
#    `node scripts/serve-dist.mjs` and nothing else — no install, no script
#    runner — yet npm's vendored dependency tree under
#    /usr/local/lib/node_modules was the source of every Node-side finding in
#    the image scan, one CRITICAL and nineteen HIGH, none of them from this
#    application's own dependencies. Serving static files needs node, not a
#    package manager.
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget \
    && apt-get install -y --only-upgrade libgnutls30 libcap2 libpcre2-8-0 \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/lib/node_modules/npm \
              /usr/local/lib/node_modules/corepack \
              /usr/local/bin/npm \
              /usr/local/bin/npx \
              /usr/local/bin/corepack \
              /usr/local/bin/yarn \
              /usr/local/bin/yarnpkg \
              /opt/yarn-v* \
    && node --version
COPY --from=builder /app/scripts/serve-dist.mjs ./scripts/serve-dist.mjs
COPY --from=builder /app/dist ./dist
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app
EXPOSE 5173
CMD ["node", "scripts/serve-dist.mjs"]

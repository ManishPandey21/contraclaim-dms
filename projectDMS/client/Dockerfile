FROM node:20-bookworm-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci && npm install --no-save @rollup/rollup-linux-x64-gnu
COPY . .
RUN npm run build

FROM node:20-bookworm-slim
WORKDIR /app
# Two things happen here beyond installing the healthcheck's wget, and both are
# about what the shipped image carries rather than what it does.
#
# 1. The base image lags bookworm-security. libgnutls30 and libcap2 are the only
#    packages with published fixes at build time; they are upgraded by name
#    rather than with a blanket `apt-get upgrade`, so the image changes only
#    where a fix is actually needed.
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
    && apt-get install -y --only-upgrade libgnutls30 libcap2 \
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

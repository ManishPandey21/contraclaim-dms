FROM node:20-bookworm-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci && npm install --no-save @rollup/rollup-linux-x64-gnu
COPY . .
RUN npm run build

FROM node:20-bookworm-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/scripts/serve-dist.mjs ./scripts/serve-dist.mjs
COPY --from=builder /app/dist ./dist
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app
EXPOSE 5173
CMD ["node", "scripts/serve-dist.mjs"]

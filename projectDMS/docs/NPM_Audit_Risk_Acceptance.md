# NPM Audit Risk Acceptance

Date: 2026-05-19

## Current Status

`npm audit --audit-level=high` exits successfully. High and critical findings were cleared by removing unused legacy packages and overriding vulnerable transitive `tar`.

Remaining findings are moderate severity and are tied to the Vite/esbuild development-server chain:

- `esbuild <=0.24.2`
- `vite <=6.4.1`
- dependent dev tooling including `vite-node`, `vitest`, `@vitejs/plugin-react-swc`, and `lovable-tagger`

## Accepted Risk

This risk is accepted for the current production build because the finding applies to the local development server. Production must serve static build output from `npm run build`; it must not expose `vite`, `vite preview`, `vitest`, or any development server to the internet.

## Compensating Controls

- Production containers must serve only built static assets.
- Development servers must bind only to localhost or a private developer network.
- Firewall and reverse proxy rules must block direct external access to development ports.
- CI must continue to run `npm audit --audit-level=high`.
- Upgrade path: move to Vite 6+ and compatible test/plugin tooling in a separate frontend toolchain upgrade branch, with visual regression coverage for routing, document viewer, and auth flows.

# Marker CLI Setup

Marker-based contract ingestion uses an external Marker CLI. The backend checks for the CLI on PATH (or uses `MARKER_CMD`) and skips Marker extraction if it is unavailable.

## Install
- Install the Marker CLI following its official instructions for your platform.
- Ensure the `marker` executable is on PATH (or set `MARKER_CMD` to a full command).

## Configure
Add to `.env` (see `.env.example`):

```
MARKER_ENABLED=true
MARKER_CMD=marker
MARKER_OUTPUT_DIR=uploads/marker
```

## Disable
Set `MARKER_ENABLED=false` to skip Marker extraction.

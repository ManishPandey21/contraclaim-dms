# Archive

This folder contains legacy, duplicate, or backup backend files that are not part of the active runtime.
They were moved here to reduce confusion in the main codebase.

Typical reasons for archiving:
- Directory or file names include "Old", "_old", "- Copy", or backups.
- Routers/services/models that are not wired into `backend/rbac_backend/main.py`.

Recover or reuse:
1. Move the file/folder back to its original path under `backend/`.
2. Reconnect any imports/routers/services as needed.
3. Run backend tests and start the service to verify behavior.

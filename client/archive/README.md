# Archive

This folder contains legacy, duplicate, or backup client files that are not part of the active application.
They were moved here to reduce confusion in the main codebase.

Typical reasons for archiving:
- Directory or file names include "Old", "_old", "- Copy", "New folder", or ".bak".
- Historical backups that are not referenced by current routes or imports.

Recover or reuse:
1. Move the file/folder back to its original path under `client/`.
2. Reconnect any imports/routes in `client/src/routes.tsx` or other modules.
3. Run the frontend build/test flow to verify behavior.

@echo off
cd /d C:\SaaS\projectDMS
echo === cowork push start === > cowork_push.log 2>&1
git --version >> cowork_push.log 2>&1
echo --- remote --- >> cowork_push.log 2>&1
git remote -v >> cowork_push.log 2>&1
echo --- current branch --- >> cowork_push.log 2>&1
git rev-parse --abbrev-ref HEAD >> cowork_push.log 2>&1
echo --- create branch --- >> cowork_push.log 2>&1
git checkout -b cowork/phase-0-and-1-hardening >> cowork_push.log 2>&1
echo --- stage phase 0 --- >> cowork_push.log 2>&1
git add backend/rbac_backend/routers/projects.py backend/rbac_backend/routers/documents.py backend/rbac_backend/routers/auth.py backend/rbac_backend/services/authorization_service.py backend/rbac_backend/tests/test_tenant_isolation.py client/src/components/document-viewer/DocumentViewer.tsx client/src/config/rolePermissions.ts >> cowork_push.log 2>&1
git commit -m "Phase 0: security and correctness hardening (C1-C3, H1-H3, H5)" >> cowork_push.log 2>&1
echo --- stage phase 1 --- >> cowork_push.log 2>&1
git add backend/rbac_backend/routers/dashboard.py backend/rbac_backend/routers/search.py backend/rbac_backend/core/config.py client/src/pages/UploadPage.tsx >> cowork_push.log 2>&1
git commit -m "Phase 1: pilot hardening (H7, H6, H4, M10)" >> cowork_push.log 2>&1
echo --- push --- >> cowork_push.log 2>&1
git push -u origin cowork/phase-0-and-1-hardening >> cowork_push.log 2>&1
echo --- final status --- >> cowork_push.log 2>&1
git status >> cowork_push.log 2>&1
echo === cowork push end === >> cowork_push.log 2>&1

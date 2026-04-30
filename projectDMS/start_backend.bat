@echo off
cd /d C:\SaaS\projectDMS\backend
python -m uvicorn rbac_backend.main:app --reload --port 8000

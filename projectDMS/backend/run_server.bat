@echo off
cd c:/SaaS/projectDMS/backend
& C:/SaaS/projectDMS/backend/.venv/Scripts/Activate.ps1
python -m uvicorn rbac_backend.main:app --reload --port 8000

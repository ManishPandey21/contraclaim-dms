@echo off
cd c:/SaaS/projectDMS/backend
C:\SaaS\projectDMS\.venv\Scripts\python.exe -m uvicorn rbac_backend.main:app --reload --port 8000

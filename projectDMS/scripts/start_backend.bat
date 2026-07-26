@echo off
cd /d C:\SaaS\projectDMS\backend
set "PYTHON=C:\SaaS\projectDMS\backend\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Python 3.12 backend virtual environment not found at %PYTHON%.
  echo Create it with: py -3.12 -m venv C:\SaaS\projectDMS\backend\.venv
  exit /b 1
)
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
  echo Backend virtual environment must use Python 3.12. Recreate it with: py -3.12 -m venv C:\SaaS\projectDMS\backend\.venv
  exit /b 1
)
"%PYTHON%" -m uvicorn rbac_backend.main:app --reload --port 8000

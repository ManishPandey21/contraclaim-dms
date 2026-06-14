.PHONY: front back all stop

# Start frontend server
front:
	cd client && npm run dev

# Start backend server
back:
	cd backend && .venv\Scripts\activate && python -m uvicorn rbac_backend.main:app --reload --port 8000

# Start both servers (Windows command - requires separate terminals)
all:
	start cmd /k "cd client && npm run dev"
	start cmd /k "cd backend && .venv\Scripts\activate && python -m uvicorn rbac_backend.main:app --reload --port 8000"

# Alternative: Start both in same terminal (one will run in background)
all-bg:
	cd backend && .venv\Scripts\activate && start python -m uvicorn rbac_backend.main:app --reload --port 8000
	cd client && npm run dev

# Kill processes on port 3000 (frontend) and 8000 (backend) - Windows version
stop:
	@echo "Stopping processes..."
	@for /f "tokens=5" %%a in ('netstat -ano ^| findstr :3000') do taskkill /PID %%a /F 2>nul
	@for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000') do taskkill /PID %%a /F 2>nul
	@echo "Servers stopped"

# Help command
help:
	@echo "Available commands:"
	@echo "  make front      - Start frontend server (port 3000)"
	@echo "  make back       - Start backend server (port 8000)"
	@echo "  make all        - Start both servers in separate terminals"
	@echo "  make all-bg     - Start both (backend in background)"
	@echo "  make stop       - Stop all servers"
	@echo "  make help       - Show this help message"
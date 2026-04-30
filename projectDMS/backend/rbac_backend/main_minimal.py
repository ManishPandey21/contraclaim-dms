from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Minimal app for critical-path testing (roles, permissions, and auth)
from .routers import roles, permissions, auth

app = FastAPI(title="RBAC Backend - Minimal (Roles, Permissions, Auth)")

# CORS (permissive for testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ "https://web.contraclaim.com", "http://localhost:5173", ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_prefix = "/api"
app.include_router(roles.router, prefix=api_prefix)
app.include_router(permissions.router, prefix=api_prefix)
app.include_router(auth.router, prefix=api_prefix)

@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "version": "minimal", "routers": ["roles", "permissions", "auth"]}

@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "healthy", "routers": ["roles", "permissions", "auth"]}

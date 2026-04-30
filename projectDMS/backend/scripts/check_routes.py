import sys
sys.path.insert(0, 'backend')

from rbac_backend.routers import storage_settings

print("Storage Settings Router Routes:")
print("=" * 50)
for route in storage_settings.router.routes:
    print(f"Methods: {route.methods}")
    print(f"Path: {route.path}")
    print(f"Name: {route.name}")
    print("-" * 50)

print(f"\nTotal routes: {len(storage_settings.router.routes)}")

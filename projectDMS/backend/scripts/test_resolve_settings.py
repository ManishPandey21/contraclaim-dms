"""Test script to verify resolve_settings with project short name"""
import asyncio
import sys
sys.path.insert(0, 'backend')

from rbac_backend.services.storage_settings_service import StorageSettingsService
from rbac_backend.models.storage_settings import (
    OrganizationStorageSettings,
    ProjectStorageSettings,
    StorageProviderConfig
)

async def test_resolve():
    """Test the resolve_settings with project short name"""
    service = StorageSettingsService()

    # Setup test data
    test_org_id = "test_org_resolve"
    test_project_id = "test_project_resolve"

    print("=" * 60)
    print("Testing resolve_settings with project short name")
    print("=" * 60)

    try:
        # 1. Create org settings with short name "KEC"
        org_settings = OrganizationStorageSettings(
            org_id=test_org_id,
            org_short_name="KEC",
            providers=[
                StorageProviderConfig(id="local", enabled=True, primary=True)
            ]
        )
        await service.upsert_org_settings(test_org_id, org_settings)
        print("✓ Created org settings with short name: KEC")

        # 2. Create project settings with short name "DC02" and inherit=True
        project_settings = ProjectStorageSettings(
            project_id=test_project_id,
            org_id=test_org_id,
            inherit_from_org=True,
            project_short_name="DC02"
        )
        await service.upsert_project_settings(test_project_id, test_org_id, project_settings)
        print("✓ Created project settings with short name: DC02, inherit=True")

        # 3. Resolve settings
        resolved = await service.resolve_settings(test_org_id, test_project_id)

        print("\n" + "=" * 60)
        print("RESOLVED SETTINGS:")
        print("=" * 60)
        print(f"Org Short Name: {resolved.org_short_name}")
        print(f"Project Short Name: {resolved.project_short_name}")
        print(f"\nBase Paths:")
        print(f"  Incoming: {resolved.base_paths.incoming}")
        print(f"  Outgoing: {resolved.base_paths.outgoing}")
        print(f"  Contracts: {resolved.base_paths.contracts}")

        # 4. Verify the paths contain both org and project short names
        expected_incoming = "/KEC/DC02/incoming"
        actual_incoming = resolved.base_paths.incoming

        if actual_incoming == expected_incoming:
            print(f"\n✅ SUCCESS: Path correctly shows {actual_incoming}")
        else:
            print(f"\n❌ FAIL: Expected {expected_incoming}, got {actual_incoming}")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_resolve())

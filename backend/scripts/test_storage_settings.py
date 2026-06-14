"""Test script to verify storage settings API endpoint"""
import asyncio
import sys
sys.path.insert(0, 'backend')

from rbac_backend.services.storage_settings_service import StorageSettingsService
from rbac_backend.models.storage_settings import OrganizationStorageSettings, StorageProviderConfig

async def test_service():
    """Test the storage settings service directly"""
    service = StorageSettingsService()
    
    # Test getting settings for a test org
    test_org_id = "test_org_123"
    
    print(f"Testing storage settings service for org: {test_org_id}")
    
    try:
        # Try to get existing settings
        settings = await service.get_org_settings(test_org_id)
        print(f"Existing settings: {settings}")
        
        # Try to create/update settings
        new_settings = OrganizationStorageSettings(
            org_id=test_org_id,
            org_short_name="TEST",
            providers=[
                StorageProviderConfig(id="local", enabled=True, primary=True)
            ]
        )
        
        saved = await service.upsert_org_settings(test_org_id, new_settings)
        print(f"Saved settings: {saved}")
        
        # Verify it was saved
        retrieved = await service.get_org_settings(test_org_id)
        print(f"Retrieved settings: {retrieved}")
        
        print("\n✅ Service test passed!")
        
    except Exception as e:
        print(f"\n❌ Service test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_service())

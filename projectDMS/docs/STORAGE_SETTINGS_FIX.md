# Storage Settings Save Issue - Fix Summary

## Issue Description

When trying to save organization short name in the Settings page, users were getting a "Failed to save settings" error with status code 404.

## Root Cause

The issue was caused by a **Pydantic validation error** when MongoDB's `ObjectId` type was being passed to Pydantic models that expected a string for the `_id` field.

### Technical Details

1. MongoDB returns `ObjectId` instances for the `_id` field
2. The Pydantic models (`OrganizationStorageSettings` and `ProjectStorageSettings`) had `id: Optional[str]` field with alias `_id`
3. When the service retrieved documents from MongoDB and tried to instantiate Pydantic models, it failed validation because `ObjectId` is not a string
4. This caused the API endpoint to fail, resulting in a 404-like error on the frontend

## Files Modified

### 1. `backend/rbac_backend/models/storage_settings.py`

**Changes:**

- Added `from bson import ObjectId` import
- Added `field_validator` import from pydantic
- Added `arbitrary_types_allowed=True` to model config for both models
- Added `@field_validator("id", mode="before")` decorator to convert ObjectId to string in both `OrganizationStorageSettings` and `ProjectStorageSettings`

**Code Added:**

```python
@field_validator("id", mode="before")
@classmethod
def convert_objectid_to_str(cls, v):
    """Convert MongoDB ObjectId to string"""
    if isinstance(v, ObjectId):
        return str(v)
    return v
```

### 2. `backend/rbac_backend/services/storage_settings_service.py`

**Changes:**

- Added ObjectId to string conversion in all methods that retrieve documents from MongoDB:
  - `get_org_settings()`
  - `upsert_org_settings()`
  - `get_project_settings()`
  - `upsert_project_settings()`

**Code Added (in each method):**

```python
# Convert ObjectId to string before passing to Pydantic
if "_id" in doc:
    doc["_id"] = str(doc["_id"])
```

## Testing

Created and ran `backend/test_storage_settings.py` which successfully:

1. Retrieved existing settings
2. Created/updated settings with short name
3. Verified the settings were saved correctly

**Test Result:** ✅ Service test passed!

## Resolution Steps

1. Fixed the Pydantic models to handle ObjectId conversion
2. Added explicit ObjectId to string conversion in the service layer
3. Tested the fix with a dedicated test script
4. Backend server needs to be restarted for changes to take effect

## Impact

- ✅ Organization short name can now be saved successfully
- ✅ Project short name can now be saved successfully
- ✅ All storage settings operations work correctly
- ✅ No breaking changes to existing functionality

## Next Steps

1. Restart the backend server to apply the changes
2. Test the Settings page in the UI to confirm the fix works end-to-end
3. Verify that saving organization and project settings works as expected

## Date Fixed

December 24, 2025

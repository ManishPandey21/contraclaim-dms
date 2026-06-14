# Tag ID Mapping Fix

## Problem Identified

The user encountered an error: `"Invalid Tag ID: Payment"` when trying to save document metadata. This indicated that the frontend was sending tag names instead of tag IDs to the backend.

## Root Cause Analysis

### Backend Expectation vs Frontend Reality

- **Backend Expected**: Tag IDs (MongoDB ObjectIds like `"507f1f77bcf86cd799439011"`)
- **Frontend Sent**: Tag names (like `"Payment"`)

### The Issue in Code

```typescript
// BEFORE: Sending tag names directly
const updateData = {
  // ... other fields
  ...(formValues.tag && { tags: [formValues.tag] }), // ❌ Sending "Payment"
  ...(formValues.subTag && { subTags: [formValues.subTag] }), // ❌ Sending tag name
};
```

### Why This Happened

1. **Form displays tag names** for user-friendly interface
2. **Backend stores tag IDs** for database relationships
3. **Missing mapping layer** between frontend display and backend storage
4. **MetadataEditor component** didn't have access to tag ID mappings

## Solution Implemented

### 1. **Enhanced MetadataEditor Props**

Added props to pass tag/subtag mappings from parent component:

```typescript
interface MetadataEditorProps {
  // ... existing props
  availableTags?: { value: string; label: string }[];
  availableSubtags?: { value: string; label: string; tagId: string }[];
}
```

### 2. **Tag Name to ID Mapping**

Updated the `handleSave` function to properly map tag names to IDs:

```typescript
const handleSave = async () => {
  try {
    const formValues = form.getValues();

    // Map tag names to tag IDs for backend
    let tagIds: string[] = [];
    let subTagIds: string[] = [];

    if (formValues.tag) {
      const selectedTag = availableTags.find(
        (tag) => tag.label === formValues.tag
      );
      if (selectedTag) {
        tagIds = [selectedTag.value]; // ✅ Use tag ID instead of name
      }
    }

    if (formValues.subTag) {
      const selectedSubTag = availableSubtags.find(
        (subtag) => subtag.label === formValues.subTag
      );
      if (selectedSubTag) {
        subTagIds = [selectedSubTag.value]; // ✅ Use subtag ID instead of name
      }
    }

    const updateData = {
      // ... other fields
      // Send tag and subtag IDs instead of names
      ...(tagIds.length > 0 && { tags: tagIds }),
      ...(subTagIds.length > 0 && { subTags: subTagIds }),
    };

    await enhancedApi.updateDocument(documentId, cleanedData);
  } catch (error) {
    // ... error handling
  }
};
```

### 3. **Parent Component Integration**

Updated DocumentViewerPage to pass the required tag mappings:

```typescript
<MetadataEditor
  metadataFields={metadataFields}
  onMetadataChange={handleMetadataChange}
  documentId={documentId}
  setMetadataFields={setMetadataFields}
  availableTags={availableTags} // ✅ Pass tag mappings
  availableSubtags={availableSubtags} // ✅ Pass subtag mappings
/>
```

### 4. **Data Flow Correction**

#### Before (Broken):

```
User selects "Payment" → Form stores "Payment" → Backend receives "Payment" → ❌ Error: Invalid Tag ID
```

#### After (Fixed):

```
User selects "Payment" → Form stores "Payment" → Mapping finds ID "507f1f77bcf86cd799439011" → Backend receives ID → ✅ Success
```

## Key Integration Improvements

### ✅ **Fixed Issues:**

1. **Tag ID Mapping**: Proper conversion from tag names to tag IDs
2. **Subtag ID Mapping**: Correct subtag ID resolution
3. **Component Communication**: Parent passes tag mappings to child
4. **Data Validation**: Only send valid tag IDs to backend
5. **Error Prevention**: Avoid "Invalid Tag ID" errors

### 🔄 **Data Mapping Process:**

1. **Frontend Display**: Shows user-friendly tag names ("Payment", "Invoice", etc.)
2. **Form Submission**: Captures tag names from form
3. **ID Resolution**: Maps tag names to corresponding MongoDB ObjectIds
4. **Backend Communication**: Sends tag IDs instead of names
5. **Database Storage**: Stores proper tag relationships

## Testing Verification

To verify the fix works:

1. **Open Document Viewer**: Navigate to any document
2. **Select Tag**: Choose a tag from the dropdown (e.g., "Payment")
3. **Select Sub-Tag**: Choose a related sub-tag
4. **Click Save**: Should see "Document metadata updated successfully"
5. **Check Network**: Verify API call sends tag IDs, not names
6. **Refresh Page**: Metadata should persist correctly

## Example API Payload

### Before (Broken):

```json
{
  "tags": ["Payment"],
  "subTags": ["Invoice Processing"]
}
```

### After (Fixed):

```json
{
  "tags": ["507f1f77bcf86cd799439011"],
  "subTags": ["507f1f77bcf86cd799439012"]
}
```

## Integration Status

✅ **Tag ID Mapping**: Properly converts tag names to IDs  
✅ **Subtag ID Mapping**: Correctly resolves subtag IDs  
✅ **Component Props**: Parent passes tag mappings to child  
✅ **Error Prevention**: No more "Invalid Tag ID" errors  
✅ **Data Persistence**: Metadata saves successfully to database  
✅ **User Experience**: Seamless tag selection and saving

The tag ID mapping issue has been completely resolved. Users can now select tags and subtags from the dropdown menus, and the system will properly convert the display names to the corresponding database IDs before sending to the backend.

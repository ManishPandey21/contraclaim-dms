# Stakeholders Backend Implementation Summary

## Overview

This document outlines the complete backend implementation for the Stakeholders section of the Document Management System (DMS) using FastAPI, MongoDB, and role-based access control.

## Architecture

- **Framework**: FastAPI with Pydantic models
- **Database**: MongoDB with pymongo
- **Authentication**: Role-based access control (RBAC)
- **API Documentation**: Auto-generated Swagger/OpenAPI docs

## Models Implemented

### 1. Organization Model (`backend/rbac_backend/models/organization.py`)

```python
class Organization(BaseModel):
    id: str
    name: str
    email: Optional[str]  # Organization email
    phone: Optional[str]  # Organization phone
    panNumber: Optional[str]
    gstNumber: Optional[str]
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    pinCode: Optional[str]
    adminName: Optional[str]
    adminEmail: Optional[str]
    adminContact: Optional[str]
    billingEnabled: Optional[bool]
```

### 2. Project Model (`backend/rbac_backend/models/project.py`)

```python
class Project(BaseModel):
    id: str
    name: str
    organization_id: str  # Foreign key to Organization
    projectCode: Optional[str]
    # ... other fields similar to Organization
```

### 3. Representative Model (`backend/rbac_backend/models/representative.py`)

```python
class RepresentativeLevel(str, Enum):
    ORGANIZATION = "organization"
    PROJECT = "project"

class Representative(BaseModel):
    id: str
    party_id: Optional[str]  # Link to Party (for external stakeholders)
    organization_id: Optional[str]  # Direct link to Organization
    project_id: Optional[str]  # Direct link to Project
    name: str
    email: EmailStr
    contact_number: Optional[str]
    designation: Optional[str]
    is_primary: bool
    level: RepresentativeLevel  # organization or project level
    use_head_office: bool  # Checkbox logic for project-level reps
```

### 4. Party Model (`backend/rbac_backend/models/party.py`)

```python
class PartyType(str, Enum):
    ORGANIZATION = "Organization"
    INDIVIDUAL = "Individual"

class Party(BaseModel):
    id: str
    name: str
    type: PartyType
    organization_id: Optional[str]
    contact_email: Optional[EmailStr]
    contact_phone: Optional[str]
    representatives: List[Representative]
    projects: List[str]  # List of project IDs
    # ... address fields
```

## API Endpoints Implemented

### Organizations (`/api/organizations`)

- `POST /organizations` - Create organization (superadmin only)
- `GET /organizations` - List organizations (role-based filtering)
- `GET /organizations/{id}` - Get specific organization
- `PUT /organizations/{id}` - Update organization
- `DELETE /organizations/{id}` - Delete organization

### Projects (`/api/projects`)

- `POST /projects` - Create project
- `GET /projects` - List projects (role-based filtering)
- `GET /projects/{id}` - Get specific project
- `PUT /projects/{id}` - Update project
- `DELETE /projects/{id}` - Delete project

### Representatives (`/api/parties/...` and direct endpoints)

#### Organization-level Representatives

- `POST /api/parties/organizations/{org_id}/representatives` - Add org representative
- `GET /api/parties/organizations/{org_id}/representatives` - List org representatives

#### Project-level Representatives

- `POST /api/parties/projects/{project_id}/representatives` - Add project representative
- `GET /api/parties/projects/{project_id}/representatives` - List project representatives
  - Query parameter: `include_head_office=true` - Include organization-level reps

#### Generic Representatives

- `GET /api/parties/representatives` - List all representatives with filters
- `PUT /api/parties/representatives/{rep_id}` - Update representative
- `DELETE /api/parties/representatives/{rep_id}` - Delete representative

### Parties/External Stakeholders (`/api/parties`)

- `POST /parties` - Create external stakeholder
- `GET /parties` - List parties with role-based filtering
- `GET /parties/{id}` - Get specific party
- `PUT /parties/{id}` - Update party
- `DELETE /parties/{id}` - Delete party
- `GET /parties/external` - List external stakeholders only
- `POST /parties/{party_id}/projects/{project_id}` - Associate party with project
- `DELETE /parties/{party_id}/projects/{project_id}` - Disassociate party from project

### Stakeholders Management (`/api/stakeholders`)

- `GET /stakeholders/projects/{project_id}/all-parties` - Get all involved parties for a project
- `GET /stakeholders/organizations/{org_id}/dropdown` - Organization dropdown data
- `GET /stakeholders/projects/dropdown` - Projects dropdown data
- `GET /stakeholders/parties/dropdown` - Parties dropdown data
- `GET /stakeholders/representatives/dropdown` - Representatives dropdown data

## Key Features Implemented

### 1. Role-Based Access Control

- **Superadmin**: Access to all data
- **Organization User**: Access to their organization's data only
- **Project User**: Access to their assigned projects' data only

### 2. Representative Management

- **Organization-level representatives**: Head office representatives
- **Project-level representatives**: Project-specific representatives
- **Checkbox logic**: `use_head_office` flag to treat project reps as head office level
- **Flexible association**: Representatives can be linked to parties, organizations, or projects

### 3. External Stakeholders

- **Independent parties**: Not tied to registered organizations
- **Project association**: Can be associated with multiple projects
- **Types**: Organization or Individual stakeholders
- **Examples**: PWD, Jal Nigam, Local Body, Electricity Dept

### 4. Search and Filtering

- **Pagination**: `skip` and `limit` parameters
- **Search**: Text search across names, emails, phone numbers
- **Filters**: By type, organization, project, level
- **Role-based filtering**: Automatic filtering based on user permissions

### 5. Dropdown/Auto-suggest Support

- **Organizations dropdown**: With search functionality
- **Projects dropdown**: Filtered by organization and user permissions
- **Parties dropdown**: External stakeholders with search
- **Representatives dropdown**: Filtered by association type

### 6. Project Correspondence Support

- **All parties endpoint**: Single endpoint to fetch all involved parties for a project
- **Comprehensive data**: Includes organization, project, all representative levels, and external parties
- **Summary statistics**: Count of different types of parties involved

## Database Collections

### 1. `organizations`

- Organization master data
- Includes contact information and admin details

### 2. `projects`

- Project data linked to organizations
- Contains project-specific information

### 3. `representatives`

- All representatives (org-level, project-level, party-level)
- Flexible linking based on `level` and association fields

### 4. `parties`

- External stakeholders not registered as formal organizations
- Can be associated with multiple projects

## Security Features

### 1. Permission-based Access

- All endpoints require appropriate permissions
- Permissions: `create`, `read`, `update`, `delete` for each entity type

### 2. Data Validation

- Pydantic models ensure data integrity
- Email validation for contact fields
- Enum validation for types and levels

### 3. Input Sanitization

- MongoDB injection prevention
- Proper ObjectId validation
- Search query sanitization

## Integration Points

### 1. Frontend Dropdowns

- Auto-suggest endpoints for all entity types
- Search functionality for better UX
- Role-based data filtering

### 2. Correspondence System

- Single endpoint to fetch all parties for a project
- Supports correspondence addressing and routing

### 3. Document Management

- Representatives can be linked to documents
- Project-party associations support document access control

## Usage Examples

### 1. Create Organization Representative

```http
POST /api/parties/organizations/{org_id}/representatives
{
  "name": "John Doe",
  "email": "john@company.com",
  "designation": "CEO",
  "is_primary": true
}
```

### 2. Create Project Representative with Head Office Flag

```http
POST /api/parties/projects/{project_id}/representatives
{
  "name": "Jane Smith",
  "email": "jane@company.com",
  "designation": "Project Manager",
  "use_head_office": true
}
```

### 3. Get All Parties for Project Correspondence

```http
GET /api/stakeholders/projects/{project_id}/all-parties
```

### 4. Search External Stakeholders

```http
GET /api/parties/external?search=PWD&type=Organization
```

## Error Handling

- Comprehensive HTTP status codes
- Detailed error messages
- Validation error responses
- Role-based access denial messages

## Performance Considerations

- MongoDB indexing on frequently queried fields
- Pagination for large datasets
- Efficient role-based filtering queries
- Minimal data transfer for dropdown endpoints

## Future Enhancements

- Caching for frequently accessed data
- Audit logging for all CRUD operations
- Bulk operations for representatives
- Advanced search with multiple criteria
- Export functionality for stakeholder lists

This implementation provides a complete backend solution for managing stakeholders in the Document Management System, supporting all the requirements specified in the original task.

DEFAULT_ORGANIZATIONS = [
    {
        "_id": "org1",
        "name": "Mock Org 1",
        "panNumber": "PAN12345",
        "gstNumber": "GST12345",
        "address": "123 Main St",
        "city": "Anytown",
        "state": "CA",
        "pinCode": "12345",
        "adminName": "Admin 1",
        "adminEmail": "admin1@example.com",
        "adminContact": "123-456-7890",
        "billingEnabled": True,
    },
    {
        "_id": "org2",
        "name": "Mock Org 2",
        "panNumber": "PAN67890",
        "address": "456 Elm St",
        "city": "Otherville",
        "state": "NY",
        "pinCode": "67890",
        "adminName": "Admin 2",
        "adminEmail": "admin2@example.com",
        "adminContact": "987-654-3210",
        "billingEnabled": False,
    },
]

DEFAULT_CONCERNS = [
    {
        "_id": "concern1",
        "description": "Concern about document accuracy.",
    },
    {
        "_id": "concern2",
        "description": "Concern about project timeline.",
    },
]

DEFAULT_REPRESENTATIVES = [
    {
        "_id": "rep1",
        "party_id": "party1",
        "name": "Rep Name 1",
        "email": "rep1@example.com",
        "contact_number": "111-111-1111",
        "designation": "Manager",
    },
    {
        "_id": "rep2",
        "party_id": "party2",
        "name": "Rep Name 2",
        "email": "rep2@example.com",
        "designation": "Agent",
    },
]

DEFAULT_PARTIES = [
    {
        "_id": "party1",
        "type": "Organization",
        "name": "Party Org 1",
        "address": "123 Party St",
        "city": "Partyville",
        "state": "CA",
        "pin_code": "90210",
        "country": "USA",
    },
    {
        "_id": "party2",
        "type": "Individual",
        "name": "John Doe",
        "address": "456 Individual Ave",
        "city": "Person City",
        "state": "NY",
        "pin_code": "10001",
        "country": "USA",
    },
]

DEFAULT_DOCUMENTS = [
    {
        "_id": "doc1",
        "filename": "document1.pdf",
        "filepath": "/path/to/document1.pdf",
        "content_type": "application/pdf",
        "size": 12345,
        "project_id": "proj1",
        "organization_id": "org1",
        "uploaded_by": "user1",
        "upload_date": "2024-03-15T12:00:00",
        "tags": ["tag1", "tag2"],
        "metadata": {"key1": "value1"},
    },
    {
        "_id": "doc2",
        "filename": "document2.docx",
        "filepath": "/path/to/document2.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": 67890,
        "project_id": "proj2",
        "organization_id": "org2",
        "uploaded_by": "user2",
        "upload_date": "2024-03-16T14:30:00",
        "tags": ["tag3"],
        "metadata": {"key2": "value2"},
    },
]

DEFAULT_USERS = [
    {
        "_id": "user1",
        "username": "testuser1",
        "email": "testuser1@example.com",
        "hashed_password": "$2b$12$7pmPKz.7L.5Z.G.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5",  # Hashed password for "password"
        "roles": ["orgadmin"],
        "organization_id": "org1",
        "projects": ["proj1"],
        "disabled": False,
    },
    {
        "_id": "user2",
        "username": "testuser2",
        "email": "testuser2@example.com",
        "hashed_password": "$2b$12$7pmPKz.7L.5Z.G.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5.5",  # Hashed password for "password"
        "roles": ["projectuser"],
        "organization_id": "org2",
        "projects": ["proj2"],
        "disabled": False,
    },
]

DEFAULT_PROJECTS = [
    {
        "_id": "proj1",
        "name": "Project Alpha",
        "organization_id": "org1",
        "projectCode": "PA001",
        "address": "789 Pine St",
        "city": "Anytown",
        "state": "CA",
        "pinCode": "54321",
        "adminName": "Project Admin 1",
        "adminEmail": "projadmin1@example.com",
        "adminContact": "111-222-3333",
        "billingEnabled": True,
    },
    {
        "_id": "proj2",
        "name": "Project Beta",
        "organization_id": "org2",
        "projectCode": "PB002",
        "address": "101 Oak St",
        "city": "Otherville",
        "state": "NY",
        "pinCode": "98765",
        "adminName": "Project Admin 2",
        "adminEmail": "projadmin2@example.com",
        "adminContact": "444-555-6666",
        "billingEnabled": False,
    },
]

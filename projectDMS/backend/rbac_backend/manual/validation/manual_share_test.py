"""Manual smoke script for the email share flow against a running API server."""

import json
import sys
import time
from typing import Optional

import httpx


API_BASE = "http://localhost:8000/api"


def pp(title: str, data):
    print(f"\n=== {title} ===")
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, default=str))
    else:
        print(data)


def get_token(client: httpx.Client, email: str, password: str) -> str:
    r = client.post(f"{API_BASE}/login", json={"email": email, "password": password}, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {data}")
    return token


def get_suggestions(client: httpx.Client, token: str):
    r = client.get(
        f"{API_BASE}/email/suggestions",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


def get_first_document_id(client: httpx.Client, token: str) -> Optional[str]:
    r = client.get(
        f"{API_BASE}/documents?limit=1",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    docs = data.get("documents", [])
    if not docs:
        return None
    # Try id, then _id
    first = docs[0]
    return first.get("id") or first.get("_id")


def share_document(client: httpx.Client, token: str, document_id: str, recipient_email: str):
    payload = {
        "recipient_email": recipient_email,
        "subject": "Test Share - Critical Path",
        "message": "Testing share of document via automated script.",
        "document_id": document_id,
        "include_linked_documents": True,
    }
    r = client.post(
        f"{API_BASE}/email/share-document",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


def main():
    # Credentials from initial_data/default_users.py (superadmin@example.com/password)
    login_email = "superadmin@example.com"
    login_password = "password"

    # Recipient for test (safe: same superadmin)
    recipient = "superadmin@example.com"

    with httpx.Client() as client:
        # 1) Login
        token = get_token(client, login_email, login_password)
        pp("Access token length", len(token))

        # 2) Suggestions
        suggestions = get_suggestions(client, token)
        pp("Email suggestions (first 5)", suggestions[:5])

        # 3) Fetch a document id
        doc_id = get_first_document_id(client, token)
        if not doc_id:
            pp("No documents available", "Upload a document first to continue the share test.")
            sys.exit(0)

        pp("Using document id", doc_id)

        # 4) Share document
        share_res = share_document(client, token, doc_id, recipient)
        pp("Share response", share_res)

        print("\nCritical-path test completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as e:
        print(f"HTTP error: {e.response.status_code} {e.response.text}")
        sys.exit(2)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

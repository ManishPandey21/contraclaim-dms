from __future__ import annotations

from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.rbac_backend.services.email_service import EmailService
from backend.rbac_backend.services.smtp_settings_service import SmtpSettingsService
from backend.rbac_backend.tests.test_upload_notifications import FakeCollection, FakeDatabase


def _db_with_smtp(*, settings_docs=None, projects=None):
    db = FakeDatabase(projects=projects or [])
    db.smtp_settings = FakeCollection(settings_docs or [])
    return db


def _user(**kwargs):
    return SimpleNamespace(
        id=kwargs.get("id", "user-1"),
        roles=kwargs.get("roles", ["orgadmin"]),
        organization_id=kwargs.get("organization_id", "org-1"),
        organizations=kwargs.get("organizations", ["org-1"]),
        projects=kwargs.get("projects", []),
    )


@pytest.mark.asyncio
async def test_smtp_resolution_prefers_active_project_over_organization():
    db = _db_with_smtp(projects=[{"_id": "proj-1", "organization_id": "org-1"}])
    service = SmtpSettingsService(db)
    db.smtp_settings.docs.extend(
        [
            {
                "scope_type": "organization",
                "organization_id": "org-1",
                "project_id": None,
                "host": "org.smtp.example",
                "port": 587,
                "username": "org-user",
                "encrypted_password": service.encrypt_password("org-secret"),
                "sender_email": "org@example.com",
                "encryption": "starttls",
                "is_active": True,
            },
            {
                "scope_type": "project",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "host": "project.smtp.example",
                "port": 465,
                "username": "project-user",
                "encrypted_password": service.encrypt_password("project-secret"),
                "sender_email": "project@example.com",
                "sender_name": "Project Mail",
                "encryption": "ssl_tls",
                "is_active": True,
            },
        ]
    )

    candidates = await service.resolve_candidates(organization_id="org-1", project_id="proj-1")

    assert candidates[0].source == "project"
    assert candidates[0].host == "project.smtp.example"
    assert candidates[0].sender_header == "Project Mail <project@example.com>"
    assert candidates[1].source == "organization"


@pytest.mark.asyncio
async def test_smtp_resolution_skips_inactive_project_and_uses_organization():
    db = _db_with_smtp(projects=[{"_id": "proj-1", "organization_id": "org-1"}])
    service = SmtpSettingsService(db)
    db.smtp_settings.docs.extend(
        [
            {
                "scope_type": "project",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "host": "project.smtp.example",
                "port": 587,
                "username": "project-user",
                "encrypted_password": service.encrypt_password("project-secret"),
                "sender_email": "project@example.com",
                "encryption": "starttls",
                "is_active": False,
            },
            {
                "scope_type": "organization",
                "organization_id": "org-1",
                "project_id": None,
                "host": "org.smtp.example",
                "port": 587,
                "username": "org-user",
                "encrypted_password": service.encrypt_password("org-secret"),
                "sender_email": "org@example.com",
                "encryption": "starttls",
                "is_active": True,
            },
        ]
    )

    candidates = await service.resolve_candidates(organization_id="org-1", project_id="proj-1")

    assert candidates[0].source == "organization"
    assert candidates[0].sender_email == "org@example.com"


@pytest.mark.asyncio
async def test_share_email_falls_back_when_project_smtp_send_fails(monkeypatch):
    db = _db_with_smtp(projects=[{"_id": "proj-1", "organization_id": "org-1"}])
    service = SmtpSettingsService(db)
    db.smtp_settings.docs.extend(
        [
            {
                "scope_type": "project",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "host": "project.smtp.example",
                "port": 587,
                "username": "project-user",
                "encrypted_password": service.encrypt_password("project-secret"),
                "sender_email": "project@example.com",
                "encryption": "starttls",
                "is_active": True,
            },
            {
                "scope_type": "organization",
                "organization_id": "org-1",
                "project_id": None,
                "host": "org.smtp.example",
                "port": 587,
                "username": "org-user",
                "encrypted_password": service.encrypt_password("org-secret"),
                "sender_email": "org@example.com",
                "encryption": "starttls",
                "is_active": True,
            },
        ]
    )
    email_service = EmailService(db)
    attempted = []

    async def fake_send(message: EmailMessage, config):
        attempted.append((config.source, message["From"]))
        return config.source == "organization"

    monkeypatch.setattr(email_service, "_send", fake_send)

    sent = await email_service.send_share_email(
        to=["recipient@example.com"],
        subject="Shared document",
        text_body="Body",
        organization_id="org-1",
        project_id="proj-1",
    )

    assert sent is True
    assert attempted == [("project", "project@example.com"), ("organization", "org@example.com")]


@pytest.mark.asyncio
async def test_org_admin_cannot_manage_project_in_another_organization():
    db = _db_with_smtp(projects=[{"_id": "proj-2", "organization_id": "org-2"}])
    service = SmtpSettingsService(db)

    with pytest.raises(HTTPException) as exc_info:
        await service.ensure_manage_project(_user(organization_id="org-1"), "proj-2")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_smtp_response_masks_encrypted_password():
    db = _db_with_smtp()
    service = SmtpSettingsService(db)
    db.smtp_settings.docs.append(
        {
            "_id": "smtp-1",
            "scope_type": "organization",
            "organization_id": "org-1",
            "project_id": None,
            "host": "org.smtp.example",
            "port": 587,
            "username": "org-user",
            "encrypted_password": service.encrypt_password("org-secret"),
            "sender_email": "org@example.com",
            "encryption": "starttls",
            "is_active": True,
        }
    )

    response = await service.get_organization_settings("org-1", _user(roles=["superadmin"], organization_id=None))
    payload = response.model_dump() if response else {}

    assert response is not None
    assert response.password_configured is True
    assert "encrypted_password" not in payload

"""Email utility endpoints used by the Share Document workflow."""

from __future__ import annotations

import re
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlencode

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from ..core.security import CurrentUser, get_current_user, require_permission
from ..dependencies import get_email_service
from ..services.authorization_service import AuthorizationService
from ..services.email_group_service import EmailGroupService
from ..services.email_service import EmailService
from ..utils.validation import sanitize_html

router = APIRouter()
auth_service = AuthorizationService()
group_service = EmailGroupService()


class RecipientSuggestion(BaseModel):
    email: EmailStr
    name: str = ""
    organization: Optional[str] = None
    source: Literal["representative", "party"] = "representative"


class RecipientResolveRequest(BaseModel):
    query: Optional[str] = ""
    include_representatives: bool = True
    include_parties: bool = True
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class DocumentShareRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    to: List[EmailStr] = Field(default_factory=list)
    cc: List[EmailStr] = Field(default_factory=list)
    bcc: List[EmailStr] = Field(default_factory=list)
    recipient_email: Optional[EmailStr] = None
    subject: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=10000)
    document_id: str = Field(..., min_length=1)
    include_linked_documents: bool = False
    include_letter_link: bool = True
    include_refs: bool = Field(default=False, alias="includeRefs")
    reference_ids: List[str] = Field(default_factory=list)
    group_ids: List[str] = Field(default_factory=list)
    email_format: Literal["text", "html"] = "text"
    html_template: Optional[str] = None
    registered_by: Optional[str] = Field(default=None, alias="registeredBy")
    distribution_for: Literal["answer", "information"] = Field(
        default="information", alias="distributionFor"
    )

    @model_validator(mode="after")
    def ensure_recipients(cls, values: "DocumentShareRequest"):
        total = len(values.to) + len(values.cc) + len(values.bcc)
        if total == 0 and not values.recipient_email:
            raise ValueError("At least one recipient is required")
        return values


class DocumentShareResponse(BaseModel):
    message: str
    document_name: str
    attachments_count: int


def _coerce_object_id(value: str):
    try:
        return ObjectId(value)
    except Exception:  # noqa: BLE001
        return value


def _dedupe(emails: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for raw in emails:
        email = (raw or "").strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(email)
    return result


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text).strip()


SHARE_METADATA_PLACEHOLDER = "<!-- SHARE_METADATA_PLACEHOLDER -->"
DISTRIBUTION_LABELS = {
    "answer": "Answer",
    "information": "Information",
}


def _build_app_link(app_url: str, path: str) -> str:
    base = (app_url or "").rstrip("/") or "http://localhost:5173"
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{base}{normalized}"


def _build_viewer_link(app_url: str, document_id: str, params: Optional[Dict[str, str]] = None) -> str:
    path = f"/documentviewer/{document_id}"
    if params:
        path = f"{path}?{urlencode(params)}"
    return _build_app_link(app_url, path)


def _format_date_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if value is None:
        return ""
    try:
        text = str(value)
        return text[:10]
    except Exception:  # noqa: BLE001
        return ""


def _format_size_value(value: Any) -> str:
    try:
        size = float(int(value))
    except Exception:  # noqa: BLE001
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


async def _collect_documents_map(db, document_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    identifiers: List[str] = []
    seen: set[str] = set()
    for raw in document_ids or []:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        identifiers.append(text)

    if not identifiers:
        return {}

    object_ids: List[ObjectId] = []
    string_ids: List[str] = []
    for identifier in identifiers:
        try:
            object_ids.append(ObjectId(identifier))
        except Exception:  # noqa: BLE001
            string_ids.append(identifier)

    filters: List[Dict[str, Any]] = []
    if object_ids:
        filters.append({"_id": {"$in": object_ids}})
    if string_ids:
        filters.append({"_id": {"$in": string_ids}})

    if not filters:
        return {}

    query = {"$or": filters} if len(filters) > 1 else filters[0]
    cursor = db.documents.find(query)
    docs = await cursor.to_list(length=None)
    mapped: Dict[str, Dict[str, Any]] = {}
    for doc in docs:
        raw_id = doc.get("_id")
        if isinstance(raw_id, ObjectId):
            key = str(raw_id)
        elif raw_id is not None:
            key = str(raw_id)
        else:
            continue
        mapped[key] = doc
    return mapped


def _extract_reference_ids(document: Dict[str, Any]) -> List[str]:
    refs = document.get("references") or []
    ids: List[str] = []
    for ref in refs:
        document_id: Optional[str] = None
        if isinstance(ref, dict):
            document_id = (
                ref.get("documentId")
                or ref.get("document_id")
                or ref.get("_id")
            )
        else:
            document_id = getattr(ref, "documentId", None)
        if document_id:
            ids.append(str(document_id))
    return ids


async def _build_reference_entries(
    email_service: EmailService,
    document: Dict[str, Any],
    payload: DocumentShareRequest,
) -> List[Dict[str, str]]:
    reference_ids = payload.reference_ids or []
    if not reference_ids and payload.include_refs:
        reference_ids = _extract_reference_ids(document)

    cleaned: List[str] = []
    seen: set[str] = set()
    for ref_id in reference_ids:
        ref_text = str(ref_id).strip()
        if not ref_text or ref_text in seen:
            continue
        seen.add(ref_text)
        cleaned.append(ref_text)

    if not cleaned:
        return []

    documents_map = await _collect_documents_map(email_service.db, cleaned)
    entries: List[Dict[str, str]] = []
    for ref_id in cleaned:
        doc = documents_map.get(ref_id)
        if not doc:
            continue
        target_id = doc.get("_id")
        resolved_id = str(target_id) if target_id is not None else ref_id
        title = (
            doc.get("subject")
            or doc.get("filename")
            or doc.get("title")
            or doc.get("letterNo")
            or resolved_id
        )
        letter_no = doc.get("letterNo") or doc.get("letter_no") or ""
        date_text = _format_date_value(doc.get("date"))
        link = _build_viewer_link(email_service.app_url, resolved_id)
        entries.append(
            {
                "id": resolved_id,
                "title": title,
                "letter": letter_no,
                "date": date_text,
                "link": link,
            }
        )
    return entries


def _build_enclosure_entries(
    document: Dict[str, Any],
    app_url: str,
) -> List[Dict[str, str]]:
    doc_id = document.get("_id") or document.get("id")
    if not doc_id:
        return []
    doc_id = str(doc_id)
    enclosures = document.get("enclosures") or []
    entries: List[Dict[str, str]] = []
    for enc in enclosures:
        enc_data: Dict[str, Any]
        if isinstance(enc, dict):
            enc_data = enc
        else:
            enc_data = {
                "id": getattr(enc, "id", None),
                "filename": getattr(enc, "filename", None),
                "filesize": getattr(enc, "filesize", None),
            }
        enc_id = enc_data.get("id") or enc_data.get("_id")
        filename = enc_data.get("filename") or "Enclosure"
        size_text = _format_size_value(enc_data.get("filesize"))
        params = {"tab": "enclosure"}
        if enc_id:
            params["enclosure"] = str(enc_id)
        link = _build_viewer_link(app_url, doc_id, params)
        entries.append(
            {
                "id": str(enc_id) if enc_id else filename,
                "title": filename,
                "size": size_text,
                "link": link,
            }
        )
    return entries


def _render_reference_section_html(entries: List[Dict[str, str]]) -> str:
    if not entries:
        return ""
    items = []
    for entry in entries:
        meta_parts = [entry.get("letter") or "", entry.get("date") or ""]
        meta = " • ".join(part for part in meta_parts if part)
        meta_html = (
            f'<div style="color:#4a5568;font-size:13px;">{escape(meta)}</div>'
            if meta
            else ""
        )
        items.append(
            f"""
            <li style="margin-bottom:10px;">
                <div style="font-weight:600;color:#1e5bb8;">{escape(entry.get("title", ""))}</div>
                {meta_html}
                <div>
                    <a href="{entry.get("link")}" style="color:#2874d8;text-decoration:none;">View reference</a>
                </div>
            </li>
            """
        )
    return f"""
    <div style="margin-top:24px;">
        <h3 style="margin:0 0 6px 0;font-size:16px;color:#1e5bb8;">Reference letters included</h3>
        <ul style="margin:0;padding-left:18px;list-style:disc;color:#4a5568;font-size:14px;line-height:1.5;">
            {''.join(items)}
        </ul>
    </div>
    """


def _render_enclosure_section_html(entries: List[Dict[str, str]]) -> str:
    if not entries:
        return ""
    items = []
    for entry in entries:
        size_text = entry.get("size") or ""
        size_html = (
            f'<div style="color:#4a5568;font-size:13px;">{escape(size_text)}</div>'
            if size_text
            else ""
        )
        items.append(
            f"""
            <li style="margin-bottom:10px;">
                <div style="font-weight:600;color:#1e5bb8;">{escape(entry.get("title", ""))}</div>
                {size_html}
                <div>
                    <a href="{entry.get("link")}" style="color:#2874d8;text-decoration:none;">View enclosure</a>
                </div>
            </li>
            """
        )
    return f"""
    <div style="margin-top:24px;">
        <h3 style="margin:0 0 6px 0;font-size:16px;color:#1e5bb8;">Enclosures linked</h3>
        <ul style="margin:0;padding-left:18px;list-style:disc;color:#4a5568;font-size:14px;line-height:1.5;">
            {''.join(items)}
        </ul>
    </div>
    """


def _render_reference_section_text(entries: List[Dict[str, str]]) -> str:
    if not entries:
        return ""
    lines = ["Reference letters included:"]
    for entry in entries:
        meta_parts = [entry.get("letter") or "", entry.get("date") or ""]
        meta = " - ".join(part for part in meta_parts if part)
        if meta:
            lines.append(f"- {entry.get('title')}: {meta} -> {entry.get('link')}")
        else:
            lines.append(f"- {entry.get('title')}: {entry.get('link')}")
    return "\n".join(lines)


def _render_enclosure_section_text(entries: List[Dict[str, str]]) -> str:
    if not entries:
        return ""
    lines = ["Enclosures linked:"]
    for entry in entries:
        size_text = entry.get("size")
        if size_text:
            lines.append(
                f"- {entry.get('title')} ({size_text}): {entry.get('link')}"
            )
        else:
            lines.append(f"- {entry.get('title')}: {entry.get('link')}")
    return "\n".join(lines)


def _build_share_metadata_rows_html(
    registered_by: Optional[str], distribution_for: Optional[str]
) -> str:
    rows: List[str] = []
    if registered_by:
        rows.append(
            f"""
            <tr>
                <td style="padding: 8px 0;">
                    <span style="color: #1e5bb8; font-weight: 600; font-size: 14px;">Registered by:</span>
                    <span style="color: #4a5568; font-size: 14px; margin-left: 5px;">{escape(registered_by)}</span>
                </td>
            </tr>
            """
        )
    if distribution_for:
        rows.append(
            f"""
            <tr>
                <td style="padding: 8px 0;">
                    <span style="color: #1e5bb8; font-weight: 600; font-size: 14px;">Distributed for:</span>
                    <span style="color: #4a5568; font-size: 14px; margin-left: 5px;">{escape(distribution_for)}</span>
                </td>
            </tr>
            """
        )
    return "".join(rows).strip()


def _inject_share_metadata_html(body_html: str, rows_html: str) -> str:
    if not rows_html:
        return body_html
    if SHARE_METADATA_PLACEHOLDER in body_html:
        return body_html.replace(SHARE_METADATA_PLACEHOLDER, rows_html, 1)
    fallback_table = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin-top: 15px; border-left: 3px solid #2874d8; padding-left: 20px;">'
        f"{rows_html}</table>"
    )
    lower_body = body_html.lower()
    closing_index = lower_body.rfind("</body>")
    if closing_index != -1:
        return f"{body_html[:closing_index]}{fallback_table}{body_html[closing_index:]}"
    return f"{body_html}{fallback_table}"


def _build_share_metadata_text(
    registered_by: Optional[str], distribution_for: Optional[str]
) -> str:
    parts: List[str] = []
    if registered_by:
        parts.append(f"Registered by: {registered_by}")
    if distribution_for:
        parts.append(f"Distributed for: {distribution_for}")
    return "\n".join(parts).strip()


async def _collect_suggestions(
    email_service: EmailService,
    payload: RecipientResolveRequest,
    limit: int = 50,
) -> List[RecipientSuggestion]:
    db = email_service.db
    query_regex = (
        {"$regex": payload.query, "$options": "i"} if payload.query else None
    )
    suggestions: List[RecipientSuggestion] = []
    seen = set()

    if payload.include_representatives:
        rep_filter: Dict[str, object] = {
            "email": {"$exists": True, "$ne": ""},
            "is_active": {"$ne": False},
        }
        if payload.organization_id:
            rep_filter["organization_id"] = payload.organization_id
        if payload.project_id:
            rep_filter["project_id"] = payload.project_id
        if query_regex:
            rep_filter["$or"] = [
                {"name": query_regex},
                {"email": query_regex},
                {"designation": query_regex},
            ]
        cursor = (
            db.representatives.find(rep_filter)
            .sort("name", 1)
            .limit(limit)
        )
        reps = await cursor.to_list(length=limit)
        for rep in reps:
            email = (rep.get("email") or "").strip()
            if not email:
                continue
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(
                RecipientSuggestion(
                    email=email,
                    name=rep.get("name") or "",
                    organization=rep.get("organization_name")
                    or rep.get("designation")
                    or rep.get("organization_id"),
                    source="representative",
                )
            )
            if len(suggestions) >= limit:
                return suggestions

    if payload.include_parties and len(suggestions) < limit:
        party_filter: Dict[str, object] = {
            "is_active": {"$ne": False},
            "$or": [
                {"contactEmail": {"$exists": True, "$ne": ""}},
                {"contact_email": {"$exists": True, "$ne": ""}},
            ],
        }
        if payload.organization_id:
            party_filter["organizationId"] = payload.organization_id
        if query_regex:
            party_filter.setdefault("$and", []).append(
                {
                    "$or": [
                        {"name": query_regex},
                        {"contactEmail": query_regex},
                        {"contact_email": query_regex},
                    ]
                }
            )
        cursor = (
            db.parties.find(party_filter)
            .sort("name", 1)
            .limit(limit - len(suggestions))
        )
        parties = await cursor.to_list(length=limit - len(suggestions))
        for party in parties:
            email = (
                party.get("contactEmail")
                or party.get("contact_email")
                or ""
            ).strip()
            if not email:
                continue
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(
                RecipientSuggestion(
                    email=email,
                    name=party.get("name") or "",
                    organization=party.get("type"),
                    source="party",
                )
            )
            if len(suggestions) >= limit:
                break

    return suggestions


@router.get("/suggestions", response_model=List[RecipientSuggestion])
async def get_email_suggestions(
    query: Optional[str] = Query(None, description="Search by name or email"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    email_service: EmailService = Depends(get_email_service),
):
    """Return quick email suggestions scoped to the current organization/project."""
    payload = RecipientResolveRequest(
        query=query or "",
        include_parties=True,
        include_representatives=True,
        organization_id=organization_id or current_user.organization_id,
        project_id=project_id,
    )
    return await _collect_suggestions(email_service, payload)


@router.post("/resolve-recipients", response_model=List[RecipientSuggestion])
async def resolve_recipients(
    payload: RecipientResolveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    email_service: EmailService = Depends(get_email_service),
):
    """Resolve recipients across representatives/parties for autocomplete."""
    payload.organization_id = (
        payload.organization_id or current_user.organization_id
    )
    return await _collect_suggestions(email_service, payload)


@router.post("/share-document", response_model=DocumentShareResponse)
async def share_document(
    background_tasks: BackgroundTasks,
    payload: DocumentShareRequest,
    current_user: CurrentUser = Depends(get_current_user),
    email_service: EmailService = Depends(get_email_service),
    _: None = Depends(require_permission("documents:share")),
):
    """Share a document via email."""
    to_emails = payload.to.copy()
    if payload.recipient_email:
        to_emails.append(payload.recipient_email)
    to_list = _dedupe(to_emails)
    cc_list = _dedupe(payload.cc)
    bcc_list = _dedupe(payload.bcc)

    if not to_list:
        raise HTTPException(
            status_code=422, detail="At least one To recipient is required"
        )

    document = await email_service.fetch_document(payload.document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    organization_id = (
        document.get("organization_id") or document.get("organizationId")
    )
    project_id = document.get("project_id") or document.get("projectId")
    await auth_service.check_document_access(
        current_user, organization_id, project_id, "share"
    )

    if payload.group_ids:
        group_emails: List[str] = []
        for group_id in payload.group_ids:
            group = await group_service.get_group_by_id(group_id)
            if group and group.emails:
                group_emails.extend(group.emails)
        if group_emails:
            to_list = _dedupe(to_list + group_emails)

    doc_title = (
        document.get("filename")
        or document.get("title")
        or document.get("subject")
        or document.get("_id")
        or payload.document_id
    )

    doc_link = f"{email_service.app_url}/documentviewer/{document.get('_id')}"
    document_registered_by = document.get("registeredBy") or document.get(
        "registered_by"
    )
    fallback_registered = current_user.username or current_user.email
    registered_by_value = (
        payload.registered_by
        or document_registered_by
        or fallback_registered
        or ""
    ).strip()
    distribution_choice = (payload.distribution_for or "information").strip().lower()
    distribution_label = DISTRIBUTION_LABELS.get(
        distribution_choice, distribution_choice.title()
    )

    if payload.email_format == "text":
        escaped = sanitize_html(payload.message).replace("\n", "<br/>")
        body_html = f"<p>{escaped}</p>"
        plain_body = payload.message.strip()
        metadata_text = _build_share_metadata_text(
            registered_by_value, distribution_label
        )
        if metadata_text:
            plain_body = (
                "\n\n".join(filter(None, [plain_body, metadata_text])).strip()
            )
    else:
        # For HTML format, inject the View Document button link
        body_html = payload.message
        
        if payload.include_letter_link:
            # Replace the placeholder text with actual button
            view_button_html = f'''<tr>
                        <td style="padding: 0 40px 30px 40px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td align="center">
                                        <a href="{doc_link}" style="display: inline-block; background-color: #2874d8; color: #ffffff; text-decoration: none; padding: 14px 40px; border-radius: 6px; font-size: 15px; font-weight: 600; box-shadow: 0 2px 6px rgba(40,116,216,0.3);">
                                            View Document
                                        </a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Secondary Action -->
                    <tr>
                        <td style="padding: 0 40px 30px 40px;" align="center">
                            <p style="margin: 0; color: #718096; font-size: 13px;">
                                Or copy this link: 
                                <a href="{doc_link}" style="color: #2874d8; text-decoration: none; font-weight: 500;">{doc_link}</a>
                            </p>
                        </td>
                    </tr>'''
            
            # Replace the placeholder in the HTML template
            body_html = body_html.replace(
                '''<!-- Action Button Placeholder -->
                    <tr>
                        <td style="padding: 0 40px 30px 40px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td align="center">
                                        <p style="margin: 0; color: #718096; font-size: 14px;">
                                            Click the "View Document" button below to access the document.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>''',
                view_button_html
            )
        
        metadata_rows_html = _build_share_metadata_rows_html(
            registered_by_value, distribution_label
        )
        body_html = _inject_share_metadata_html(body_html, metadata_rows_html)
        plain_body = _html_to_text(body_html)

        if payload.include_letter_link and doc_link not in plain_body:
            plain_body = f"{plain_body}\n\nView document: {doc_link}".strip()

    reference_entries = await _build_reference_entries(
        email_service, document, payload
    )
    enclosure_entries: List[Dict[str, str]] = []
    if payload.include_linked_documents:
        enclosure_entries = _build_enclosure_entries(document, email_service.app_url)

    html_sections: List[str] = []
    text_sections: List[str] = []

    if reference_entries:
        html_sections.append(_render_reference_section_html(reference_entries))
        ref_text = _render_reference_section_text(reference_entries)
        if ref_text:
            text_sections.append(ref_text)

    if enclosure_entries:
        html_sections.append(_render_enclosure_section_html(enclosure_entries))
        enclosure_text = _render_enclosure_section_text(enclosure_entries)
        if enclosure_text:
            text_sections.append(enclosure_text)

    if html_sections:
        body_html = (body_html or "") + "".join(html_sections)

    if text_sections:
        text_block = "\n\n".join(text_sections).strip()
        plain_body = (
            "\n\n".join(filter(None, [plain_body, text_block])).strip()
            if plain_body
            else text_block
        )

    background_tasks.add_task(
        email_service.send_share_email,
        to=to_list,
        cc=cc_list,
        bcc=bcc_list,
        subject=payload.subject,
        html_body=body_html if payload.email_format == "html" else None,
        text_body=plain_body,
    )

    reference_count = len(reference_entries)
    enclosure_count = len(enclosure_entries)
    attachments_count = 1 + reference_count
    if enclosure_count:
        attachments_count += enclosure_count
    if payload.include_letter_link:
        attachments_count += 1

    return DocumentShareResponse(
        message="Email queued for delivery",
        document_name=str(doc_title),
        attachments_count=attachments_count,
    )

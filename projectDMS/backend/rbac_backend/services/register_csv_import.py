from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from ..models.bank_guarantee import BGStatus, BGType, BankGuaranteeCreate
from ..models.csv_import import CSVImportPreview, CSVImportResult, CSVImportRow
from ..models.key_date import KeyDateMilestoneCreate
from .bank_guarantee_service import BankGuaranteeService
from .key_date_service import KeyDateError, KeyDateService, calculate_key_date


MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 1000

KEY_DATE_REQUIRED_HEADERS = ["title", "project_id", "contractual_week_number"]
KEY_DATE_TEMPLATE_HEADERS = [
    "title",
    "project_id",
    "contractual_week_number",
    "project_start_date",
    "milestone_ref",
    "description",
    "responsible_party_id",
    "original_planned_key_date",
    "remarks",
]

BG_REQUIRED_HEADERS = ["project_id", "bg_number"]
BG_TEMPLATE_HEADERS = [
    "project_id",
    "contract_id",
    "bg_number",
    "bg_type",
    "issuing_bank",
    "branch",
    "bg_amount",
    "currency",
    "conversion_rate",
    "submission_date",
    "contractual_required_up_to",
    "bg_expiry_date",
    "claim_expiry_date",
    "bg_status",
    "remarks",
]

KEY_DATE_SAMPLE_ROW = {
    "title": "Basement 2 Structure Complete",
    "project_id": "project-id",
    "contractual_week_number": "8",
    "project_start_date": "2026-01-05",
    "milestone_ref": "M-001",
    "description": "Contractual key date for basement completion",
    "responsible_party_id": "",
    "original_planned_key_date": "",
    "remarks": "Imported from baseline programme",
}

BG_SAMPLE_ROW = {
    "project_id": "project-id",
    "contract_id": "primary",
    "bg_number": "BG-2026-001",
    "bg_type": "performance",
    "issuing_bank": "Sample Bank",
    "branch": "Mumbai",
    "bg_amount": "1000000",
    "currency": "INR",
    "conversion_rate": "1",
    "submission_date": "2026-01-10",
    "contractual_required_up_to": "2026-12-31",
    "bg_expiry_date": "2026-11-30",
    "claim_expiry_date": "2026-12-15",
    "bg_status": "valid",
    "remarks": "Performance security",
}


HEADER_ALIASES = {
    "key date name": "title",
    "key_date_name": "title",
    "milestone name": "title",
    "milestone": "title",
    "name": "title",
    "week": "contractual_week_number",
    "contractual week": "contractual_week_number",
    "contractual_week": "contractual_week_number",
    "project": "project_id",
    "project id": "project_id",
    "start date": "project_start_date",
    "project start": "project_start_date",
    "planned date": "original_planned_key_date",
    "key date": "original_planned_key_date",
    "current key date": "original_planned_key_date",
    "ref": "milestone_ref",
    "reference": "milestone_ref",
    "responsible party": "responsible_party_id",
    "bg no": "bg_number",
    "bg number": "bg_number",
    "bank guarantee number": "bg_number",
    "type": "bg_type",
    "bank": "issuing_bank",
    "amount": "bg_amount",
    "required up to": "contractual_required_up_to",
    "required upto": "contractual_required_up_to",
    "expiry date": "bg_expiry_date",
    "bg expiry": "bg_expiry_date",
    "claim expiry": "claim_expiry_date",
    "status": "bg_status",
}


def _header_key(value: str) -> str:
    raw = (value or "").strip().replace("\ufeff", "")
    lowered = " ".join(raw.replace("-", " ").replace("_", " ").split()).lower()
    return HEADER_ALIASES.get(lowered, lowered.replace(" ", "_"))


def _decode_csv(content: bytes) -> str:
    if len(content) > MAX_CSV_BYTES:
        raise ValueError(f"CSV file is too large. Maximum size is {MAX_CSV_BYTES // (1024 * 1024)} MB.")
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_csv_rows(content: bytes) -> List[Dict[str, Any]]:
    text = _decode_csv(content)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV header row is missing.")

    rows: List[Dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        if row_number - 1 > MAX_CSV_ROWS:
            raise ValueError(f"CSV exceeds the maximum supported {MAX_CSV_ROWS} data rows.")
        normalized = {
            _header_key(str(key)): (value.strip() if isinstance(value, str) else value)
            for key, value in (row or {}).items()
            if key is not None
        }
        if not any(str(value or "").strip() for value in normalized.values()):
            continue
        normalized["_row_number"] = row_number
        rows.append(normalized)
    return rows


def collect_project_ids(content: bytes, default_project_id: Optional[str] = None) -> List[str]:
    ids = set()
    for row in parse_csv_rows(content):
        project_id = str(row.get("project_id") or default_project_id or "").strip()
        if project_id:
            ids.add(project_id)
    return sorted(ids)


def template_csv(headers: Sequence[str], sample_row: Dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(headers), lineterminator="\n")
    writer.writeheader()
    writer.writerow({key: sample_row.get(key, "") for key in headers})
    return buffer.getvalue()


def _blank_to_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date(value: Any, field: str, errors: List[str]) -> Optional[datetime]:
    text = _blank_to_none(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    errors.append(f"{field} must be a valid date")
    return None


def _int(value: Any, field: str, errors: List[str], *, minimum: Optional[int] = None) -> Optional[int]:
    text = _blank_to_none(value)
    if text is None:
        return None
    try:
        parsed = int(float(text))
    except ValueError:
        errors.append(f"{field} must be a whole number")
        return None
    if minimum is not None and parsed < minimum:
        errors.append(f"{field} must be at least {minimum}")
    return parsed


def _float(value: Any, field: str, errors: List[str], *, minimum: Optional[float] = None) -> Optional[float]:
    text = _blank_to_none(value)
    if text is None:
        return None
    try:
        parsed = float(text.replace(",", ""))
    except ValueError:
        errors.append(f"{field} must be a number")
        return None
    if minimum is not None and parsed < minimum:
        errors.append(f"{field} must be at least {minimum}")
    return parsed


def _csv_list(value: Any) -> List[str]:
    text = _blank_to_none(value)
    if not text:
        return []
    return [part.strip() for part in text.replace("|", ";").split(";") if part.strip()]


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _date_key(value: Optional[datetime]) -> str:
    return value.date().isoformat() if isinstance(value, datetime) else ""


async def _cursor_to_list(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=None)
    return [doc async for doc in cursor]


async def _existing_key_date_keys(db: Any, organization_id: Optional[str], project_ids: Iterable[str]) -> set[Tuple[str, str, str]]:
    project_ids = [project_id for project_id in set(project_ids) if project_id]
    if not project_ids:
        return set()
    query: Dict[str, Any] = {"project_id": {"$in": project_ids}}
    if organization_id:
        query["organization_id"] = organization_id
    docs = await _cursor_to_list(db.key_date_milestones.find(query))
    keys = set()
    for doc in docs:
        keys.add(
            (
                str(doc.get("project_id") or ""),
                _norm_text(doc.get("title")),
                _date_key(doc.get("current_approved_key_date") or doc.get("original_planned_key_date") or doc.get("calculated_key_date")),
            )
        )
    return keys


async def _existing_bg_numbers(db: Any, organization_id: Optional[str], project_ids: Iterable[str]) -> set[Tuple[str, str]]:
    project_ids = [project_id for project_id in set(project_ids) if project_id]
    if not project_ids:
        return set()
    query: Dict[str, Any] = {"project_id": {"$in": project_ids}}
    if organization_id:
        query["organization_id"] = organization_id
    docs = await _cursor_to_list(db.bank_guarantees.find(query))
    return {
        (str(doc.get("project_id") or ""), _norm_text(doc.get("bg_number")))
        for doc in docs
        if _norm_text(doc.get("bg_number"))
    }


@dataclass
class _Preview:
    response: CSVImportPreview
    payloads: List[Tuple[int, Any]]


def _response(module: str, rows: List[CSVImportRow], required: List[str], headers: List[str]) -> CSVImportPreview:
    invalid = sum(1 for row in rows if row.errors)
    return CSVImportPreview(
        module=module,
        total_rows=len(rows),
        valid_rows=len(rows) - invalid,
        invalid_rows=invalid,
        can_import=bool(rows) and invalid == 0,
        rows=rows,
        required_headers=required,
        template_headers=headers,
    )


async def preview_key_dates_csv(
    db: Any,
    content: bytes,
    current_user: Any,
    *,
    project_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> _Preview:
    raw_rows = parse_csv_rows(content)
    org = organization_id or getattr(current_user, "organization_id", None)
    service = KeyDateService(db)
    project_ids = [
        str(row.get("project_id") or project_id or "").strip()
        for row in raw_rows
        if str(row.get("project_id") or project_id or "").strip()
    ]
    for selected_project_id in sorted(set(project_ids)):
        try:
            await service._assert_original_baseline_editable({  # type: ignore[attr-defined]
                "project_id": selected_project_id,
                "organization_id": org,
            })
        except KeyDateError as exc:
            raise ValueError(str(exc)) from exc
    existing = await _existing_key_date_keys(db, org, project_ids)
    seen: set[Tuple[str, str, str]] = set()
    out: List[CSVImportRow] = []
    payloads: List[Tuple[int, KeyDateMilestoneCreate]] = []

    for raw in raw_rows:
        errors: List[str] = []
        warnings: List[str] = []
        row_number = int(raw.get("_row_number") or 0)
        title = _blank_to_none(raw.get("title"))
        row_project = _blank_to_none(raw.get("project_id")) or project_id
        if project_id and raw.get("project_id") and str(raw.get("project_id")).strip() != project_id:
            errors.append("project_id does not match the selected project")
        row_org = _blank_to_none(raw.get("organization_id"))
        if row_org and org and row_org != org:
            errors.append("organization_id does not match the current organization")
        if not title:
            errors.append("title is required")
        if not row_project:
            errors.append("project_id is required")
        week = _int(raw.get("contractual_week_number"), "contractual_week_number", errors, minimum=1)
        if week is None:
            errors.append("contractual_week_number is required")

        start = _date(raw.get("project_start_date"), "project_start_date", errors)
        original = _date(raw.get("original_planned_key_date"), "original_planned_key_date", errors)
        calculated: Optional[datetime] = None
        if row_project and week is not None:
            try:
                resolved_start, basis = await service._start_and_basis(row_project, start, org)  # type: ignore[attr-defined]
                calculated = calculate_key_date(resolved_start, week, basis)
            except KeyDateError as exc:
                errors.append(str(exc))
        key_date = original or calculated
        duplicate_key = (str(row_project or ""), _norm_text(title), _date_key(key_date))
        duplicate = False
        if key_date and title and row_project:
            if duplicate_key in existing:
                errors.append("duplicate key date already exists for this project/title/date")
                duplicate = True
            elif duplicate_key in seen:
                errors.append("duplicate key date appears more than once in this CSV")
                duplicate = True
            seen.add(duplicate_key)

        data = {
            "title": title,
            "project_id": row_project,
            "contractual_week_number": week,
            "project_start_date": start.isoformat() if start else None,
            "calculated_key_date": calculated.isoformat() if calculated else None,
            "original_planned_key_date": original.isoformat() if original else None,
            "milestone_ref": _blank_to_none(raw.get("milestone_ref")),
            "description": _blank_to_none(raw.get("description")),
            "responsible_party_id": _blank_to_none(raw.get("responsible_party_id")),
            "remarks": _blank_to_none(raw.get("remarks")),
        }

        payload = None
        if not errors:
            try:
                payload = KeyDateMilestoneCreate(
                    title=title or "",
                    project_id=str(row_project),
                    contractual_week_number=int(week or 1),
                    project_start_date=start,
                    original_planned_key_date=original,
                    milestone_ref=data["milestone_ref"],
                    description=data["description"],
                    responsible_party_id=data["responsible_party_id"],
                    remarks=data["remarks"],
                    linked_document_ids=_csv_list(raw.get("linked_document_ids")),
                    linked_letter_ids=_csv_list(raw.get("linked_letter_ids")),
                    organization_id=org,
                )
            except ValidationError as exc:
                errors.extend(error["msg"] for error in exc.errors())
        if payload is not None and not errors:
            payloads.append((row_number, payload))
        out.append(CSVImportRow(row_number=row_number, data=data, errors=errors, warnings=warnings, duplicate=duplicate))

    return _Preview(_response("key_dates", out, KEY_DATE_REQUIRED_HEADERS, KEY_DATE_TEMPLATE_HEADERS), payloads)


async def import_key_dates_csv(
    db: Any,
    content: bytes,
    current_user: Any,
    *,
    project_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> CSVImportResult:
    preview = await preview_key_dates_csv(db, content, current_user, project_id=project_id, organization_id=organization_id)
    result = CSVImportResult(**preview.response.model_dump(), imported_count=0, created_ids=[])
    if not preview.response.can_import:
        return result

    service = KeyDateService(db)
    for _, payload in preview.payloads:
        created = await service.create_milestone(payload, current_user)
        result.created_ids.append(str(created.get("_id") or created.get("id")))
    result.imported_count = len(result.created_ids)
    return result


async def preview_bank_guarantees_csv(
    db: Any,
    content: bytes,
    current_user: Any,
    *,
    project_id: Optional[str] = None,
    contract_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> _Preview:
    raw_rows = parse_csv_rows(content)
    org = organization_id or getattr(current_user, "organization_id", None)
    project_ids = [
        str(row.get("project_id") or project_id or "").strip()
        for row in raw_rows
        if str(row.get("project_id") or project_id or "").strip()
    ]
    existing = await _existing_bg_numbers(db, org, project_ids)
    seen: set[Tuple[str, str]] = set()
    out: List[CSVImportRow] = []
    payloads: List[Tuple[int, BankGuaranteeCreate]] = []
    valid_types = {item.value for item in BGType}
    valid_statuses = {item.value for item in BGStatus}

    for raw in raw_rows:
        errors: List[str] = []
        warnings: List[str] = []
        row_number = int(raw.get("_row_number") or 0)
        row_project = _blank_to_none(raw.get("project_id")) or project_id
        if project_id and raw.get("project_id") and str(raw.get("project_id")).strip() != project_id:
            errors.append("project_id does not match the selected project")
        row_org = _blank_to_none(raw.get("organization_id"))
        if row_org and org and row_org != org:
            errors.append("organization_id does not match the current organization")
        if not row_project:
            errors.append("project_id is required")
        bg_number = _blank_to_none(raw.get("bg_number"))
        if not bg_number:
            errors.append("bg_number is required")
        bg_type = _blank_to_none(raw.get("bg_type")) or "performance"
        if bg_type not in valid_types:
            errors.append(f"bg_type must be one of: {', '.join(sorted(valid_types))}")
        bg_status = _blank_to_none(raw.get("bg_status")) or "valid"
        if bg_status not in valid_statuses:
            errors.append(f"bg_status must be one of: {', '.join(sorted(valid_statuses))}")
        amount = _float(raw.get("bg_amount"), "bg_amount", errors, minimum=0)
        rate = _float(raw.get("conversion_rate"), "conversion_rate", errors, minimum=0)
        submission = _date(raw.get("submission_date"), "submission_date", errors)
        required_up_to = _date(raw.get("contractual_required_up_to"), "contractual_required_up_to", errors)
        expiry = _date(raw.get("bg_expiry_date"), "bg_expiry_date", errors)
        claim_expiry = _date(raw.get("claim_expiry_date"), "claim_expiry_date", errors)
        duplicate_key = (str(row_project or ""), _norm_text(bg_number))
        duplicate = False
        if bg_number and row_project:
            if duplicate_key in existing:
                errors.append("duplicate bank guarantee number already exists for this project")
                duplicate = True
            elif duplicate_key in seen:
                errors.append("duplicate bank guarantee number appears more than once in this CSV")
                duplicate = True
            seen.add(duplicate_key)

        data = {
            "project_id": row_project,
            "contract_id": _blank_to_none(raw.get("contract_id")) or contract_id or "primary",
            "bg_number": bg_number,
            "bg_type": bg_type,
            "issuing_bank": _blank_to_none(raw.get("issuing_bank")),
            "branch": _blank_to_none(raw.get("branch")),
            "bg_amount": amount,
            "currency": _blank_to_none(raw.get("currency")) or "INR",
            "conversion_rate": rate,
            "submission_date": submission.isoformat() if submission else None,
            "contractual_required_up_to": required_up_to.isoformat() if required_up_to else None,
            "bg_expiry_date": expiry.isoformat() if expiry else None,
            "claim_expiry_date": claim_expiry.isoformat() if claim_expiry else None,
            "bg_status": bg_status,
            "remarks": _blank_to_none(raw.get("remarks")),
        }

        payload = None
        if not errors:
            try:
                payload = BankGuaranteeCreate(
                    project_id=str(row_project),
                    contract_id=data["contract_id"],
                    bg_number=bg_number,
                    bg_type=bg_type,
                    issuing_bank=data["issuing_bank"],
                    branch=data["branch"],
                    bg_amount=amount,
                    currency=data["currency"],
                    conversion_rate=rate,
                    submission_date=submission,
                    contractual_required_up_to=required_up_to,
                    bg_expiry_date=expiry,
                    claim_expiry_date=claim_expiry,
                    bg_status=bg_status,
                    remarks=data["remarks"],
                    linked_document_ids=_csv_list(raw.get("linked_document_ids")),
                    organization_id=org,
                )
            except ValidationError as exc:
                errors.extend(error["msg"] for error in exc.errors())
        if payload is not None and not errors:
            payloads.append((row_number, payload))
        out.append(CSVImportRow(row_number=row_number, data=data, errors=errors, warnings=warnings, duplicate=duplicate))

    return _Preview(_response("bank_guarantees", out, BG_REQUIRED_HEADERS, BG_TEMPLATE_HEADERS), payloads)


async def import_bank_guarantees_csv(
    db: Any,
    content: bytes,
    current_user: Any,
    *,
    project_id: Optional[str] = None,
    contract_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> CSVImportResult:
    preview = await preview_bank_guarantees_csv(
        db, content, current_user, project_id=project_id, contract_id=contract_id, organization_id=organization_id
    )
    result = CSVImportResult(**preview.response.model_dump(), imported_count=0, created_ids=[])
    if not preview.response.can_import:
        return result

    service = BankGuaranteeService(db)
    for _, payload in preview.payloads:
        created = await service.create(payload, current_user)
        result.created_ids.append(str(created.get("_id") or created.get("id")))
    result.imported_count = len(result.created_ids)
    return result

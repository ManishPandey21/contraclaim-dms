from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx


BASE = "http://127.0.0.1:8000/api"


def iso_z(dt: datetime) -> str:
    """Return RFC3339 string with Z (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    s = dt.isoformat()
    return s.replace("+00:00", "Z")


def check_z(field_name: str, value: Optional[str]) -> str:
    if not value or not isinstance(value, str):
        return f"[WARN] {field_name}: not present"
    if not value.endswith("Z"):
        return f"[FAIL] {field_name}: not Z-suffixed -> {value}"
    return f"[OK] {field_name}: {value}"


def pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return str(obj)


def main() -> None:
    print("=== Date/Time Consistency Test (UTC storage, Z transport, IST render) ===")
    with httpx.Client(timeout=20.0) as client:
        # Login
        login_payload = {"email": "superadmin@example.com", "password": "password"}
        r = client.post(f"{BASE}/login", json=login_payload)
        r.raise_for_status()
        token = r.json().get("access_token")
        if not token:
            raise SystemExit("Login failed: No token in response")
        headers = {"Authorization": f"Bearer {token}"}
        print("[OK] Logged in")

        # Create letter with explicit UTC Z date
        zdate = iso_z(datetime.now(timezone.utc))
        create1 = {
            "title": "Test Letter UTCZ",
            "recipient": "Recipient A",
            "subject": "Subject A",
            "content": "Content A",
            "assigned_to": "user1",
            "organization_id": "org1",
            "date": zdate,
        }
        r1 = client.post(f"{BASE}/letters", headers=headers, json=create1)
        if r1.status_code >= 400:
            print("[ERROR] POST /letters (UTCZ) failed:", r1.text)
            r1.raise_for_status()
        l1 = r1.json()
        print("[INFO] Created letter (UTCZ):", pretty(l1))
        print(check_z("l1.created_at", l1.get("created_at")))
        print(check_z("l1.updated_at", l1.get("updated_at")))
        print(check_z("l1.date", l1.get("date")))

        # Create letter with naive local time string (backend should normalize to UTC Z)
        naive = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        create2 = {
            "title": "Test Letter Naive",
            "recipient": "Recipient B",
            "subject": "Subject B",
            "content": "Content B",
            "assigned_to": "user1",
            "organization_id": "org1",
            "date": naive,
        }
        r2 = client.post(f"{BASE}/letters", headers=headers, json=create2)
        if r2.status_code >= 400:
            print("[ERROR] POST /letters (naive) failed:", r2.text)
            r2.raise_for_status()
        l2 = r2.json()
        print("[INFO] Created letter (naive):", pretty(l2))
        print(check_z("l2.created_at", l2.get("created_at")))
        print(check_z("l2.updated_at", l2.get("updated_at")))
        print(check_z("l2.date", l2.get("date")))

        # GET letters
        r3 = client.get(f"{BASE}/letters", headers=headers)
        r3.raise_for_status()
        letters = r3.json()
        if isinstance(letters, list) and letters:
            first = letters[0]
            print("[INFO] GET /letters[0]:", pretty(first))
            print(check_z("letters[0].created_at", first.get("created_at")))
            print(check_z("letters[0].updated_at", first.get("updated_at")))
            if first.get("date") is not None:
                print(check_z("letters[0].date", first.get("date")))
        else:
            print("[WARN] GET /letters returned empty list")

        # Conversation summary for l1
        cid = l1.get("conversation_id")
        if cid:
            rsum = client.get(f"{BASE}/letters/conversations/{cid}/summary", headers=headers)
            if rsum.status_code >= 400:
                print("[ERROR] GET conversation summary failed:", rsum.text)
            else:
                summary = rsum.json()
                print("[INFO] Conversation summary:", pretty(summary))
                print(check_z("summary.created_at", summary.get("created_at")))
                print(check_z("summary.updated_at", summary.get("updated_at")))
        else:
            print("[WARN] l1.conversation_id not present")

        # Input Requests on l1
        letter_id = l1.get("id") or l1.get("_id")
        if letter_id:
            ir_payload = {"requested_from": "userX", "details": "Need data", "due_date": None}
            rir = client.post(f"{BASE}/input-requests/letter/{letter_id}", headers=headers, json=ir_payload)
            if rir.status_code >= 400:
                print("[ERROR] Create InputRequest failed:", rir.text)
            else:
                ir = rir.json()
                print("[INFO] Created InputRequest:", pretty(ir))
                print(check_z("ir.created_at", ir.get("created_at")))
                print(check_z("ir.updated_at", ir.get("updated_at")))

                # list
                rlist = client.get(f"{BASE}/input-requests/letter/{letter_id}", headers=headers)
                if rlist.status_code >= 400:
                    print("[ERROR] List InputRequests failed:", rlist.text)
                else:
                    lst = rlist.json()
                    if isinstance(lst, list) and lst:
                        print("[INFO] IR list[0]:", pretty(lst[0]))
                        print(check_z("irList[0].created_at", lst[0].get("created_at")))
                        print(check_z("irList[0].updated_at", lst[0].get("updated_at")))

                # respond
                if ir.get("id"):
                    rresp = client.post(
                        f"{BASE}/input-requests/{ir['id']}/respond",
                        headers=headers,
                        json={"message": "OK"},
                    )
                    if rresp.status_code >= 400:
                        print("[ERROR] Respond IR failed:", rresp.text)
                    else:
                        ir2 = rresp.json()
                        print("[INFO] Responded IR:", pretty(ir2))
                        print(check_z("ir.respond.updated_at", ir2.get("updated_at")))

                    # close
                    rclose = client.post(f"{BASE}/input-requests/{ir['id']}/close", headers=headers)
                    if rclose.status_code >= 400:
                        print("[ERROR] Close IR failed:", rclose.text)
                    else:
                        ir3 = rclose.json()
                        print("[INFO] Closed IR:", pretty(ir3))
                        print(check_z("ir.close.updated_at", ir3.get("updated_at")))
        else:
            print("[WARN] l1.id/_id not present, skipping InputRequest tests")

        # Documents sample (optional)
        rdocs = client.get(f"{BASE}/documents?limit=1", headers=headers)
        if rdocs.status_code >= 400:
            print("[WARN] GET /documents failed:", rdocs.text)
        else:
            print("[INFO] GET /documents sample:", pretty(rdocs.json()))

    print("=== End of test ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[FATAL]", repr(e))
        raise

import json
import sys
import urllib.request
import urllib.error
from urllib.parse import urlencode

API_BASE = "http://localhost:8000/api"

DEV_HEADERS = {
    # Use lightweight dev headers accepted by the backend security stub
    "X-User-Id": "user_demo",
    "X-User-Role": "superadmin",
    # Optionally scope by org/project if needed:
    # "X-Org-Id": "",
    # "X-Proj-Id": "",
}

def http_get(path: str, params: dict | None = None, headers: dict | None = None):
    url = f"{API_BASE}{path}"
    if params:
        query = urlencode(params, doseq=True)
        url = f"{url}?{query}"
    req = urllib.request.Request(url)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    # Add default headers
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[HTTPError] {e.code} {e.reason} for {url}\n{body}")
        raise
    except Exception as e:
        print(f"[Error] {e} for {url}")
        raise

def fetch_documents(limit: int = 50, skip: int = 0):
    res = http_get(
        "/documents",
        params={"skip": skip, "limit": limit},
        headers=DEV_HEADERS,
    )
    if isinstance(res, dict) and "documents" in res:
        docs = res["documents"]
        total = res.get("total", len(docs))
    elif isinstance(res, list):
        docs = res
        total = len(docs)
    else:
        docs = []
        total = 0
    return docs, total

def print_doc_summary(d):
    print(
        f"- id={d.get('_id') or d.get('id')} "
        f"uploadType={d.get('uploadType')} "
        f"from_={d.get('from_') or d.get('from')} "
        f"to={d.get('to')} "
        f"project_id={d.get('project_id')} "
        f"project_name={d.get('project_name')}"
    )

def main():
    # Accept an optional specific document id to check
    target_id = None
    if len(sys.argv) > 1:
        target_id = sys.argv[1]

    print("Fetching first 50 documents (dev headers auth)...")
    docs, total = fetch_documents(limit=50, skip=0)
    print(f"Total (reported): {total}, Retrieved: {len(docs)}")

    # Basic pagination smoke test
    if total > 50:
        docs2, _ = fetch_documents(limit=50, skip=50)
        print(f"Second page Retrieved: {len(docs2)}")
    else:
        docs2 = []

    all_docs = docs + docs2

    # 1) Verify project_name presence, and that it resolves for various project_id formats
    missing_name = [d for d in all_docs if not d.get("project_name")]
    print(f"Documents missing project_name in first batch: {len(missing_name)}")
    if missing_name:
        print("Sample missing project_name rows:")
        for d in missing_name[:5]:
            print_doc_summary(d)

    # 2) Verify 'From' fallback behavior
    outgoing_blank = [
        d for d in all_docs
        if str(d.get("uploadType", "")).lower() == "outgoing"
        and (not (d.get("from_") or d.get("from")))
    ]
    print(f"Outgoing docs with blank from: {len(outgoing_blank)}")
    if outgoing_blank:
        print("Sample outgoing blank-from rows (should be resolved to organization name now):")
        for d in outgoing_blank[:5]:
            print_doc_summary(d)

    incoming_blank = [
        d for d in all_docs
        if str(d.get("uploadType", "")).lower() == "incoming"
        and (not (d.get("from_") or d.get("from")))
    ]
    print(f"Incoming docs with blank from (kept unchanged by current rule): {len(incoming_blank)}")
    if incoming_blank:
        print("Sample incoming blank-from rows:")
        for d in incoming_blank[:5]:
            print_doc_summary(d)

    # 3) If a specific document id was provided, print its details clearly
    if target_id:
        matches = [
            d for d in all_docs
            if (d.get("_id") == target_id) or (d.get("id") == target_id)
        ]
        if not matches:
            # Try to fetch more pages up to 3 pages
            for page in range(2, 5):
                more, _ = fetch_documents(limit=50, skip=page * 50)
                if not more:
                    break
                all_docs.extend(more)
                matches = [
                    d for d in more
                    if (d.get("_id") == target_id) or (d.get("id") == target_id)
                ]
                if matches:
                    break

        print(f"\nDetails for target document id={target_id}:")
        if matches:
            print_doc_summary(matches[0])
        else:
            print(f"Not found in the sampled pages.")

    # Final short report
    resolved_outgoing = [
        d for d in all_docs
        if str(d.get("uploadType", "")).lower() == "outgoing"
        and (d.get("from_") or d.get("from"))
    ]
    print("\nReport:")
    print(f"- Total sampled: {len(all_docs)}")
    print(f"- Outgoing with non-empty from after fallback: {len(resolved_outgoing)}")
    print(f"- Incoming with blank from (left as-is): {len(incoming_blank)}")
    print("Done.")

if __name__ == "__main__":
    main()

import json
import urllib.request
import urllib.error

BASE = "http://localhost:8000/api"

def post_json(url: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return resp.getcode(), body

def get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.getcode(), dict(resp.getheaders()), resp.read()

def get_with_auth(url: str, token: str):
    try:
        return get(url, {"Authorization": f"Bearer {token}"})
    except urllib.error.HTTPError as e:
        try:
            details = e.read().decode("utf-8")
        except Exception:
            details = ""
        return e.code, {"error": str(e)}, details.encode("utf-8")

def main():
    # 0) Inspect OpenAPI to verify route presence
    try:
        code, headers, body = get("http://localhost:8000/openapi.json")
        print(f"GET /openapi.json -> {code}")
        text = body.decode("utf-8", errors="ignore")
        print(f"Has /api/documents/export: {('/api/documents/export' in text)}")
    except Exception as e:
        print(f"OpenAPI fetch failed: {e}")

    # 1) Login to get token
    code, body = post_json(f"{BASE}/token", {"email": "superadmin@example.com", "password": "password"})
    print(f"POST /api/token -> {code}")
    if code != 200:
        print(body)
        return
    try:
        token = json.loads(body).get("access_token", "")
    except Exception:
        token = ""
    if not token:
        print("No token received")
        return

    # 2) Call export endpoint with Authorization header
    export_url = f"{BASE}/documents/export?limit=10"
    code, headers, body = get_with_auth(export_url, token)
    print(f"GET /api/documents/export -> {code}")
    if isinstance(headers, dict):
        ct = headers.get("Content-Type") or headers.get("content-type")
        print(f"Content-Type: {ct}")
    print(f"Body length: {len(body)} bytes")
    try:
        # Try to decode error JSON for debugging when not 200
        if code != 200:
            print(f"Error body: {body.decode('utf-8', errors='ignore')}")
    except Exception:
        pass
    # Print first few bytes to confirm XLSX signature (PK..)
    print(f"Head bytes: {body[:4]!r}")

if __name__ == "__main__":
    main()

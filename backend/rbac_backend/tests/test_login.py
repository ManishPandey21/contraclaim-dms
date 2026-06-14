import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"


def post_json(path: str, payload: dict, headers: dict | None = None) -> tuple[int, str]:
    url = BASE + path
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except Exception as e:
        print(f"ERROR during POST {path}: {e}", file=sys.stderr)
        sys.exit(2)


def get(path: str, headers: dict | None = None) -> tuple[int, str]:
    url = BASE + path
    h = {}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except Exception as e:
        print(f"ERROR during GET {path}: {e}", file=sys.stderr)
        sys.exit(2)


def main():
    # 1) Login
    status, body = post_json(
        "/api/token",
        {"email": "superadmin@example.com", "password": "password"},
    )
    print("POST /api/token ->", status)
    print(body)

    # 2) If success, call /api/me with the token
    if status == 200:
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        token = data.get("access_token")
        if token:
            status2, body2 = get("/api/me", headers={"Authorization": f"Bearer {token}"})
            print("GET /api/me ->", status2)
            print(body2)
        else:
            print("No access_token in response payload")
            sys.exit(1)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Fail CI if the three LangGraph runtime manifests silently drift."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "requirements.txt",
    ROOT / "backend" / "rbac_backend" / "requirements.txt",
    ROOT / "services" / "langgraph" / "requirements.txt",
]
EXPECTED = {
    "langgraph": "langgraph==1.2.9",
    "langgraph-checkpoint-mongodb": "langgraph-checkpoint-mongodb==0.4.0",
}


def main() -> int:
    bad: list[str] = []
    for path in FILES:
        lines = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
        for package, expected in EXPECTED.items():
            if expected not in lines:
                found = sorted(line for line in lines if line.lower().startswith(package))
                bad.append(f"{path.relative_to(ROOT)}: expected {expected}; found {found or 'nothing'}")
    if bad:
        print("LangGraph requirement mismatch:\n" + "\n".join(bad))
        return 1
    print("LangGraph requirements aligned: " + ", ".join(EXPECTED.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

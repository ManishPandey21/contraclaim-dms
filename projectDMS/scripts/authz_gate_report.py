"""Report routes with no static call path to an authorization sink."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rbac_phase0_route_inventory import app  # noqa: E402
from authz_call_graph import analyze_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=["summary", "json"], default="summary")
    args = parser.parse_args()

    results = analyze_app(app)
    gated = {k: v for k, v in results.items() if v.gated}
    ungated = {k: v for k, v in results.items() if not v.gated}

    if args.format == "json":
        payload = {
            f"{','.join(k[2])} {k[0]} [{k[1]}]": {
                "gated": v.gated,
                "step_up": v.step_up,
                "sink": v.sink,
                "via": v.via,
                "path": v.path,
            }
            for k, v in sorted(results.items())
        }
        print(json.dumps(payload, indent=2))
        return

    print(f"total /api routes : {len(results)}")
    print(f"statically gated  : {len(gated)}")
    print(f"NO gate call-path : {len(ungated)}")
    print()
    for key in sorted(ungated):
        path, name, methods = key
        print(f"  {','.join(methods):6} {path:58} {name}")


if __name__ == "__main__":
    main()

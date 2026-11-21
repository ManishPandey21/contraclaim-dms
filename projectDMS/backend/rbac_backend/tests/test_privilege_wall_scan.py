"""4B-46 (D-27): the privilege wall, read from the source tree.

``services/claim_privileged.py`` is the only module that reads the simulations and the
strategy note. This scan names the places D-27 says they must never reach - the
exporters, the source ledger, escalation and the skill-step stores - and checks, by
following imports, that none of them can reach the privileged module even indirectly.

One edge is allowed and pinned: ``services/project_perspective.py`` imports the single
yes/no check ``any_simulation_for_projects`` (4B-15) and nothing else, which the older
whole-tree test in ``test_claim_findings.py`` already enforces.

Each named module must exist, so a rename cannot silently drop it from the scan.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, Iterable, List, Set

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "rbac_backend"
PRIVILEGED_MODULE = f"{PACKAGE}.services.claim_privileged"
ALLOWED_EDGE = (f"{PACKAGE}.services.project_perspective", PRIVILEGED_MODULE)
PRIVILEGED_NAMES = re.compile(
    r"\b(ClaimSimulation|StrategyNote|ClaimPrivileged|any_simulation_for_projects|strategy_note)\b|[\"']claim_privileged[\"']"
)

GUARDED: Dict[str, List[str]] = {
    "exporters": [
        "services/claim_submission.py",
        "services/evidence_bundle_service.py",
        "services/export_service.py",
        "services/docx_xml.py",
        "services/arbitration_drafting/exporter.py",
        "services/arbitration_drafting/filing_export_queue.py",
        "routers/claims.py",
    ],
    "source ledger": [
        "services/arbitration_drafting/service.py",
        "services/arbitration_drafting/generator.py",
        "services/arbitration_drafting/context.py",
        "services/arbitration_drafting/citation_states.py",
        "services/arbitration_drafting/validator.py",
        "services/arbitration_drafting/case_workspace.py",
        "services/letter_drafting/repository.py",
    ],
    "escalation": ["services/claim_escalation.py", "routers/claim_escalation.py"],
    "step stores": ["services/skills/steps.py", "services/letter_drafting/bench.py"],
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = [PACKAGE, *relative.parts]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _modules() -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("tests/") or "__pycache__" in relative:
            continue
        found[_module_name(path)] = path
    return found


def _imports(path: Path, name: str, known: Iterable[str]) -> Set[str]:
    """Every rbac_backend module ``path`` imports, at any depth in the file (local imports included)."""
    known = set(known)
    package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
    targets: Set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            bases = [alias.name for alias in node.names]
            candidates = bases
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            candidates = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        for candidate in candidates:
            if candidate.startswith("backend."):
                candidate = candidate[len("backend."):]
            if candidate in known:
                targets.add(candidate)
    return targets


def _graph() -> Dict[str, Set[str]]:
    modules = _modules()
    return {name: _imports(path, name, modules) for name, path in modules.items()}


def _path_to_privileged(graph: Dict[str, Set[str]], start: str) -> List[str]:
    """An import chain from ``start`` to the privileged module, or [] when there is none."""
    previous: Dict[str, str] = {start: ""}
    queue = [start]
    while queue:
        current = queue.pop(0)
        for target in sorted(graph.get(current, ())):
            if (current, target) == ALLOWED_EDGE or target in previous:
                continue
            previous[target] = current
            if target == PRIVILEGED_MODULE:
                chain = [target]
                while previous[chain[-1]]:
                    chain.append(previous[chain[-1]])
                return list(reversed(chain))
            queue.append(target)
    return []


def test_every_guarded_module_exists() -> None:
    missing = [path for paths in GUARDED.values() for path in paths if not (ROOT / path).is_file()]
    assert missing == [], f"renamed or removed; update the scan: {missing}"


def test_no_exporter_ledger_escalation_or_step_store_names_privileged_material() -> None:
    for area, paths in GUARDED.items():
        for path in paths:
            source = (ROOT / path).read_text(encoding="utf-8")
            hits = sorted({match.group(0) for match in PRIVILEGED_NAMES.finditer(source)})
            assert hits == [], f"{area}: {path} names {hits}"


def test_no_exporter_ledger_escalation_or_step_store_can_reach_the_privileged_module() -> None:
    graph = _graph()
    assert PRIVILEGED_MODULE in graph, "the privileged module moved; update the scan"
    for area, paths in GUARDED.items():
        for path in paths:
            chain = _path_to_privileged(graph, _module_name(ROOT / path))
            assert chain == [], f"{area}: {path} reaches the privileged module: {' -> '.join(chain)}"


def test_the_scan_finds_a_real_chain() -> None:
    """The scan is live: the findings router does reach the privileged module."""
    graph = _graph()
    chain = _path_to_privileged(graph, f"{PACKAGE}.routers.claim_findings")
    assert chain and chain[-1] == PRIVILEGED_MODULE

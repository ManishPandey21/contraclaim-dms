"""Build the synthetic golden sets for the claims and EOT packs (4B-42).

Every matter here is invented. The sets exercise the evaluation runner and the
composition rules of plan section 7; they are marked synthetic, so a run on them can
support no approval. Real golden sets - anonymised real matters with the owner's consent,
or matters written by the reviewing experts, each labelled by two independent experts and
adjudicated by the legal approver - live in the restricted evaluation store outside the
repository (AQ-10); only their hashes are recorded here.

    python scripts/build_synthetic_golden_sets.py            # write the two files
    python scripts/build_synthetic_golden_sets.py --check    # fail if a file differs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

OUT = Path(__file__).resolve().parents[1] / "backend" / "rbac_backend" / "skill_eval" / "synthetic"
CLAIMS = "indian-infra-claims"
EOT = "indian-infra-eot-claims"
CLAIMS_PROMPT = "claims.v1.head_test"
EOT_PROMPT = "letter_drafting.v3.skill_step"
ELEMENTS = ("event", "notice", "contractual_basis", "causation", "quantum")
PRIVILEGED = "internal settlement range"

# matter id, role, title, facts, clauses, notice days, element states (E N B C Q), classification, tags
ClaimRow = Tuple[str, str, str, str, List[str], List[int], str, str, List[str]]
CLAIM_ROWS: List[ClaimRow] = [
    ("C01", "contractor", "Late access to the depot site",
     "Access to the depot site was given on 2026-03-20; the programme date was 2026-03-02. Notice served 2026-03-25.",
     ["2.1", "20.1"], [28], "SSSSW", "excusable", ["straightforward"]),
    ("C02", "contractor", "Suspension ordered by the Engineer",
     "The Engineer suspended viaduct works on 2026-05-04 by letter ENG/114. Notice served 2026-05-10.",
     ["8.8", "20.1"], [28], "SSSSS", "compensable", ["straightforward"]),
    ("C03", "engineer", "Contractor claim for extra piling",
     "The Contractor claims extra piling under instruction VO-07 dated 2026-02-11. The instruction is on record.",
     ["13.1", "13.3"], [28], "SSSWW", "compensable", ["variation_derived"]),
    ("C04", "employer", "Claim for revised station finishes",
     "Finishes were revised by instruction VO-12 dated 2026-06-02. The Contractor priced the change on 2026-06-20.",
     ["13.1", "13.3", "20.1"], [28], "SSSWM", "compensable", ["variation_derived"]),
    ("C05", "contractor", "Utility diversion delay, notice late",
     "Utility diversion finished on 2026-04-30, 40 days late. Notice served 2026-06-15. The Employer records no prejudice.",
     ["4.12", "20.1"], [28], "SWSSW", "excusable", ["late_notice", "late_notice_without_prejudice"]),
    ("C06", "contractor", "Design change to the concourse, notice late",
     "The concourse design changed on 2026-01-10. Notice served 2026-03-20. The Employer shows it lost the chance to re-sequence works.",
     ["13.1", "20.1"], [28], "SWSWW", "undetermined", ["late_notice", "late_notice_with_prejudice"]),
    ("C07", "engineer", "Weather claim, notice late",
     "Rainfall exceeded the ten-year average in July 2026. Notice served 2026-09-30.",
     ["8.5", "20.1"], [28], "SWSWW", "excusable", ["late_notice"]),
    ("C08", "employer", "Global claim for disruption",
     "The Contractor claims a single sum for disruption across the project without linking costs to events.",
     ["20.1"], [28], "WSWMM", "undetermined", ["global_claim"]),
    ("C09", "engineer", "Global claim for prolongation",
     "The Contractor claims prolongation costs for the whole delay period as one sum, with no event-by-event build-up.",
     ["8.4", "20.1"], [28], "WSWMW", "undetermined", ["global_claim"]),
    ("C10", "contractor", "Idle plant with no plant records",
     "Plant stood idle during the suspension of 2026-05-04. No plant logs are on record.",
     ["8.8", "20.1"], [28], "SSSWM", "compensable", ["missing_records"]),
    ("C11", "contractor", "Labour standing time without muster rolls",
     "Labour stood idle while access was denied. Muster rolls for the period are not on record.",
     ["2.1", "20.1"], [28], "SSSWM", "compensable", ["missing_records"]),
    ("C12", "contractor", "Delayed drawings for the depot",
     "Drawings for the depot roof were issued on 2026-02-28; the drawing schedule date was 2026-02-01. Notice served 2026-03-05.",
     ["1.9", "20.1"], [28], "SSSSW", "excusable", []),
    ("C13", "contractor", "Changed ground conditions at pier 14",
     "Rock was met at pier 14 at a level the site investigation did not show. Notice served 2026-04-08.",
     ["4.12", "20.1"], [28], "SSSWW", "compensable", []),
    ("C14", "contractor", "Employer-supplied rails delivered late",
     "Rails due on 2026-07-01 arrived on 2026-08-10. Notice served 2026-07-15.",
     ["2.5", "20.1"], [28], "SSSSW", "compensable", []),
    ("C15", "contractor", "Fluctuation in steel price",
     "The Contractor claims steel price rise for 2026. The contract carries no price-adjustment clause.",
     ["13.8"], [28], "SSMWW", "contractor_risk", []),
]

# matter id, role, subject, material, clauses, risk owner, criticality, tags
EotRow = Tuple[str, str, str, str, List[str], str, str, List[str]]
EOT_ROWS: List[EotRow] = [
    ("E01", "contractor", "Late access and late steel", "Access was given late while the Contractor's steel was also late; both lay on the critical path for the same weeks.", ["8.4"], "employer", "critical", ["true_concurrency"]),
    ("E02", "engineer", "Drawings and labour shortage", "Drawings were late while the Contractor lacked labour on the same critical activities.", ["8.4", "1.9"], "employer", "critical", ["true_concurrency"]),
    ("E03", "employer", "Utility diversion and plant breakdown", "The utility diversion ran late while the Contractor's crane was broken down on the same critical path.", ["4.12", "8.4"], "employer", "critical", ["true_concurrency"]),
    ("E04", "contractor", "Suspension and late formwork", "The Engineer's suspension overlapped the Contractor's late formwork delivery on critical activities.", ["8.8", "8.4"], "employer", "critical", ["true_concurrency"]),
    ("E05", "contractor", "Exceptional rainfall", "Rainfall in July exceeded the ten-year average and stopped earthworks on the critical path.", ["8.5"], "neutral", "critical", ["neutral_event"]),
    ("E06", "engineer", "Epidemic restrictions", "Government restrictions stopped work for twelve days across the site.", ["19.1"], "neutral", "critical", ["neutral_event"]),
    ("E07", "contractor", "Revised determination of an earlier delay", "The Engineer's first determination of the access delay was replaced by a second determination; the claim rests on the first.", ["3.5", "8.4"], "employer", "critical", ["superseded_determination"]),
    ("E08", "employer", "Determination withdrawn after review", "A determination granting time for drawings was withdrawn and replaced; the request relies on the withdrawn one.", ["3.5", "8.4"], "undetermined", "unproven", ["superseded_determination"]),
    ("E09", "contractor", "Late signage drawings", "Signage drawings were late; the signage activity had float and no programme shows it on the critical path.", ["1.9", "8.4"], "employer", "unproven", ["no_critical_path_proof"]),
    ("E10", "contractor", "Late landscaping access", "Landscaping access was late; no updated programme is on record.", ["2.1", "8.4"], "employer", "unproven", ["no_critical_path_proof"]),
    ("E11", "engineer", "Windows analysis of station works", "The Contractor's windows analysis shows the critical path moving to the station roof in window 3.", ["8.4"], "employer", "critical", ["windows_method"]),
    ("E12", "employer", "Windows analysis of depot works", "A windows analysis shows the depot delay absorbed by float in window 2.", ["8.4"], "contractor", "not_critical", ["windows_method"]),
    ("E13", "contractor", "Contractor's own resequencing", "The Contractor resequenced viaduct pours for its own convenience, delaying the critical path.", ["8.4"], "contractor", "critical", []),
    ("E14", "contractor", "Late rails", "Employer-supplied rails arrived 40 days late on the critical path.", ["2.5", "8.4"], "employer", "critical", []),
    ("E15", "contractor", "Late design approval", "The Engineer's approval of the design took 45 days against the 21 allowed, on the critical path.", ["5.2", "8.4"], "employer", "critical", []),
]

_STATE = {"S": "supported", "W": "weak", "M": "missing", "N": "not_applicable"}


def _claim(row: ClaimRow) -> Dict[str, Any]:
    matter_id, role, title, facts, clauses, notice_days, states, classification, tags = row
    clause_text = " ".join(f"Sub-Clause {clause}." for clause in clauses)
    return {
        "matter_id": matter_id,
        "role": role,
        "payload": {
            "facts": facts,
            "title": title,
            "kind": "time and money",
            "event_summary": facts,
            "clauses": f"{clause_text} Notice within {notice_days[0]} days under Sub-Clause 20.1." if "20.1" in clauses else clause_text,
        },
        "record": {"clauses": clauses, "notice_days": notice_days, "privileged_markers": [PRIVILEGED]},
        "labels": {
            "elements": {name: _STATE[code] for name, code in zip(ELEMENTS, states)},
            "classification": {"field": "classification", "expected": classification},
        },
        "composition": tags,
        "provenance": {"source": "synthetic"},
    }


def _eot(row: EotRow) -> Dict[str, Any]:
    matter_id, role, subject, material, clauses, risk_owner, criticality, tags = row
    return {
        "matter_id": matter_id,
        "role": role,
        "payload": {
            "task": "Classify who carries the risk of this delay event and whether it is shown to be critical.",
            "subject": subject,
            "contract_form": "Not resolved for this project. Do not assume an edition.",
            "material": material,
            "sources": "\n".join(f"- Clause {clause}: indexed" for clause in clauses),
            "inputs": "None.",
        },
        "record": {"clauses": clauses, "notice_days": [28], "privileged_markers": [PRIVILEGED]},
        "labels": {
            "elements": {"criticality": criticality},
            "classification": {"field": "risk_owner", "expected": risk_owner},
        },
        "composition": tags,
        "provenance": {"source": "synthetic"},
    }


def build() -> Dict[str, Dict[str, Any]]:
    return {
        CLAIMS: {"suite_key": f"{CLAIMS}-synthetic", "skill_key": CLAIMS, "prompt_key": CLAIMS_PROMPT,
                 "matters": [_claim(row) for row in CLAIM_ROWS]},
        EOT: {"suite_key": f"{EOT}-synthetic", "skill_key": EOT, "prompt_key": EOT_PROMPT,
              "matters": [_eot(row) for row in EOT_ROWS]},
    }


def render(suite: Dict[str, Any]) -> str:
    return json.dumps(suite, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a committed file differs from the build")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    differs = []
    for key, suite in build().items():
        path = OUT / f"{key}.json"
        text = render(suite)
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                differs.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
    if differs:
        print("out of date: " + ", ".join(differs), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

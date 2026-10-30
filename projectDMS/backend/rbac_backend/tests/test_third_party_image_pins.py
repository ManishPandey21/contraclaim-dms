"""Every third-party image the production compose files run is pinned by digest.

R-A8X adversarial review: `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` said
"no pin moved", but `mongo:8.0`, `httpd:2.4`, `clamav/clamav:1.4` and
`redis:7.4-alpine` are floating tags. Any `compose pull`, pruned cache or new host
would fetch whatever they point at today - the unvalidated 8.0.30, 2.4.68 and
1.4.6 - which is implicit adoption of an image nobody validated, the one thing
the cutover policy forbids. A tag names a line; only a digest names bytes.

The pins are the index digests of the images production and the R-A8W staging
run actually used (read from the running containers' RepoDigests), so pinning
changes no running byte and stales no evidence. Moving a pin is a deliberate,
reviewed act that has to change the digest here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = (REPO_ROOT / "docker-compose.prod.yml", REPO_ROOT / "docker-compose.mongo-replicaset.yml")

IMAGE_LINE = re.compile(r"^\s*image:\s*(\S+)\s*$", re.M)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
#: Built from this repository, scanned and pinned by CI's own image gate.
FIRST_PARTY = re.compile(r"^(\$\{[A-Z_]+(:-[^}]*)?\}|contraclaim)")

#: The exact bytes validated so far. Changing one is a release decision.
#:
#: ClamAV moved in R-A8Y to the 1.4.6 index digest R-A8X scanned (0 fixable
#: CRITICAL/HIGH) and R-A8Y validated in a disposable drill. ClamAV rebuilds its
#: tags to refresh the baked database: on 2026-09-14 `clamav/clamav:1.4.6` already
#: resolved to `f156095071…`, not the scanned `71fbb76b…`. Pinning the tag
#: would have adopted unscanned bytes; the digest is the only name for what
#: was actually validated.
EXPECTED = {
    "mongo:8.0": "ffa440e8d62533e24a67696ae1bbb46e610ebb3167d65abd122b496ae06d28e6",
    "falkordb/falkordb:v4.0.8": "af5f2aa035390f04fa6d1f0c6353669f5f75c101f6b4f385fcb672a288b4edb8",
    "qdrant/qdrant:v1.12.5": "05fecce7dce45d1254e0468bc037e8210e187fd56fa847688b012293d5f08aae",
    "httpd:2.4": "393435ee1a31437adeb1f03c134224c9ce5fa5f527e8c0cb9bea576b0d6fc742",
    "clamav/clamav:1.4.6": "71fbb76b397cd84a90043caf1178a7f81bd0c131a031e7b0619afd721fbfad41",
    "redis:7.4-alpine": "6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99",
}


def third_party_images(text: str) -> list[str]:
    return [ref for ref in IMAGE_LINE.findall(text) if not FIRST_PARTY.match(ref)]


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda path: path.name)
def test_every_third_party_image_is_pinned_by_digest(compose: Path) -> None:
    refs = third_party_images(compose.read_text(encoding="utf-8"))
    assert refs, f"{compose.name} runs no third-party image, so this guard measures nothing"
    floating = [ref for ref in refs if not DIGEST.search(ref)]
    assert not floating, f"{compose.name} runs floating third-party tags: {floating}"


def test_the_pins_are_the_validated_digests() -> None:
    seen: dict[str, set[str]] = {}
    for compose in COMPOSE_FILES:
        for ref in third_party_images(compose.read_text(encoding="utf-8")):
            name, _, digest = ref.partition("@sha256:")
            seen.setdefault(name, set()).add(digest)
    assert set(seen) == set(EXPECTED), f"third-party image set changed: {sorted(seen)}"
    for name, digests in seen.items():
        assert digests == {EXPECTED[name]}, f"{name} is pinned to {digests}, validated {EXPECTED[name]}"


def test_the_guard_rejects_a_floating_tag() -> None:
    """Negative control."""
    sample = "services:\n  db:\n    image: mongo:8.0\n  web:\n    image: contraclaim-backend:latest\n"
    refs = third_party_images(sample)
    assert refs == ["mongo:8.0"]
    assert not DIGEST.search(refs[0])

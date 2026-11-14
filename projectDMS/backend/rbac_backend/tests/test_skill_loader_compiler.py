"""Skill bundles: safe loading, hashing, and compilation into a prompt block."""

from __future__ import annotations

import io
import stat
import zipfile

import pytest

from rbac_backend.services.letter_drafting.prompts import (
    COMPOSE_PROMPT_KEY,
    DEFAULT_COMPOSE_TEMPLATE,
    DEFAULT_SKILL_STEP_TEMPLATE,
    SKILL_STEP_PROMPT_KEY,
    UNTRUSTED_GUARD_SENTINEL,
    PromptRegistry,
    extract_template_variables,
    render_prompt,
)
from rbac_backend.services.skills.compiler import (
    METHOD_NOTICE,
    ROLE_OVERLAYS,
    SkillRoleNotSupported,
    attach_skill_block,
    compile_skill,
    escape_braces,
)
from rbac_backend.services.skills.loader import (
    MAX_FILE_BYTES,
    SkillBundleError,
    load_skill_zip,
    parse_front_matter,
)
from rbac_backend.services.skills.system_skills import (
    ANALYSIS_SKILL_KEY,
    LETTERS_SKILL_KEY,
    VARIATIONS_SKILL_KEY,
    system_skill_version,
    system_skill_versions,
)

SKILL_MD = """---
name: demo-skill
description: A demo skill used in tests.
---

# Demo Skill

Follow the method. Output as {json} is fine in prose.
"""


def _zip(files: dict[str, bytes], symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def test_loads_a_valid_bundle_and_strips_the_root_folder() -> None:
    loaded = load_skill_zip(
        _zip({"demo-skill/SKILL.md": SKILL_MD.encode(), "demo-skill/references/notes.md": b"# Notes\n"})
    )
    assert loaded.skill_key == "demo-skill"
    assert loaded.name == "Demo Skill"
    assert loaded.description == "A demo skill used in tests."
    assert [ref.path for ref in loaded.references] == ["references/notes.md"]
    assert len(loaded.content_hash) == 64


def test_hash_is_stable_across_zip_order_and_line_endings() -> None:
    first = load_skill_zip(_zip({"s/SKILL.md": SKILL_MD.encode(), "s/a.md": b"one\n", "s/b.md": b"two\n"}))
    second = load_skill_zip(
        _zip({"s/b.md": b"two\r\n", "s/a.md": b"one\r\n", "s/SKILL.md": SKILL_MD.replace("\n", "\r\n").encode()})
    )
    assert first.content_hash == second.content_hash


def test_hash_changes_when_any_text_changes() -> None:
    first = load_skill_zip(_zip({"SKILL.md": SKILL_MD.encode(), "a.md": b"one\n"}))
    second = load_skill_zip(_zip({"SKILL.md": SKILL_MD.encode(), "a.md": b"one!\n"}))
    assert first.content_hash != second.content_hash


@pytest.mark.parametrize(
    "name",
    ["../evil.md", "demo/../../evil.md", "/abs/SKILL.md", "C:/x/evil.md", "demo\\evil.md"],
)
def test_rejects_path_traversal_and_absolute_paths(name: str) -> None:
    with pytest.raises(SkillBundleError, match="Unsafe path"):
        load_skill_zip(_zip({"SKILL.md": SKILL_MD.encode(), name: b"x"}))


def test_rejects_non_text_members() -> None:
    with pytest.raises(SkillBundleError, match="Only .md and .txt"):
        load_skill_zip(_zip({"SKILL.md": SKILL_MD.encode(), "run.py": b"import os"}))
    with pytest.raises(SkillBundleError, match="not a text file"):
        load_skill_zip(_zip({"SKILL.md": SKILL_MD.encode(), "blob.md": b"\x00\x01\x02binary"}))


def test_rejects_symlinks() -> None:
    with pytest.raises(SkillBundleError, match="Symbolic links"):
        load_skill_zip(_zip({"SKILL.md": SKILL_MD.encode()}, symlink="link.md"))


def test_rejects_oversize_files_and_bundles() -> None:
    with pytest.raises(SkillBundleError, match="larger than 256 KB"):
        load_skill_zip(_zip({"SKILL.md": SKILL_MD.encode(), "big.md": b"a" * (MAX_FILE_BYTES + 1)}))
    with pytest.raises(SkillBundleError, match="2 MB"):
        load_skill_zip(b"0" * (2 * 1024 * 1024 + 1))
    with pytest.raises(SkillBundleError, match="not a valid zip"):
        load_skill_zip(b"definitely not a zip")


def test_requires_exactly_one_skill_md_with_front_matter() -> None:
    with pytest.raises(SkillBundleError, match="exactly one SKILL.md"):
        load_skill_zip(_zip({"notes.md": b"x"}))
    with pytest.raises(SkillBundleError, match="exactly one SKILL.md"):
        load_skill_zip(_zip({"a/SKILL.md": SKILL_MD.encode(), "b/SKILL.md": SKILL_MD.encode()}))
    with pytest.raises(SkillBundleError, match="front matter"):
        load_skill_zip(_zip({"SKILL.md": b"# No front matter\n"}))
    with pytest.raises(SkillBundleError, match="'name'"):
        load_skill_zip(_zip({"SKILL.md": b"---\nname: Bad Name!\ndescription: x\n---\n"}))


def test_front_matter_parser_reads_flat_pairs() -> None:
    fields, body = parse_front_matter(SKILL_MD)
    assert fields == {"name": "demo-skill", "description": "A demo skill used in tests."}
    assert body.lstrip().startswith("# Demo Skill")


def test_the_three_system_packs_load_with_manifests() -> None:
    versions = {item.skill_key: item for item in system_skill_versions()}
    assert set(versions) == {ANALYSIS_SKILL_KEY, LETTERS_SKILL_KEY, VARIATIONS_SKILL_KEY}
    assert versions[ANALYSIS_SKILL_KEY].kind == "analysis"
    assert versions[LETTERS_SKILL_KEY].manifest.native_role == "employer"
    assert "valuation_limb_stated" in versions[VARIATIONS_SKILL_KEY].manifest.validators
    assert all(item.status == "published" and item.version == "1.0.0" for item in versions.values())


# --------------------------------------------------------------------------- #
# Compiler
# --------------------------------------------------------------------------- #
def test_compile_adds_method_notice_overlay_and_references() -> None:
    letters = system_skill_version(LETTERS_SKILL_KEY)
    compiled = compile_skill(letters, "contractor")
    assert METHOD_NOTICE in compiled.block
    assert ROLE_OVERLAYS["contractor"] in compiled.block
    assert "protect the Contractor's position" in compiled.block
    assert "REFERENCE NOTE: references/common-clauses.md" in compiled.block
    assert not compiled.block.lstrip().startswith("---")  # front matter is not prompt text
    assert "name: indian-infra-contract-letters" not in compiled.block
    assert compiled.overlay_key == "contractor.v1"
    assert compiled.content_hash == letters.content_hash


def test_overlay_differs_by_role_and_changes_the_compiled_hash() -> None:
    letters = system_skill_version(LETTERS_SKILL_KEY)
    hashes = {role: compile_skill(letters, role).compiled_hash for role in ("contractor", "engineer", "employer")}
    assert len(set(hashes.values())) == 3
    assert "act fairly between the Parties" in compile_skill(letters, "engineer").block


def test_skill_is_refused_for_a_role_its_manifest_does_not_list() -> None:
    letters = system_skill_version(LETTERS_SKILL_KEY)
    restricted = letters.model_copy(
        update={"manifest": letters.manifest.model_copy(update={"roles_supported": ["employer"]})}
    )
    with pytest.raises(SkillRoleNotSupported):
        compile_skill(restricted, "contractor")
    with pytest.raises(SkillRoleNotSupported):
        compile_skill(letters, "arbitrator")


def test_braces_in_skill_text_do_not_break_prompt_rendering() -> None:
    letters = system_skill_version(LETTERS_SKILL_KEY)
    braced = letters.model_copy(update={"skill_md": letters.skill_md + "\nReturn {\"a\": {unbalanced\n"})
    compiled = compile_skill(braced, "employer")
    record = __import__("asyncio").run(PromptRegistry(None).get_enabled(COMPOSE_PROMPT_KEY))
    payload = {name: "x" for name in extract_template_variables(record.template)}
    prompt = attach_skill_block(render_prompt(record, payload), compiled)
    assert '{"a": {unbalanced' in prompt
    assert prompt.index("=== SKILL:") < prompt.index("TASK:")
    assert UNTRUSTED_GUARD_SENTINEL in prompt


def test_escape_braces_round_trips_through_format() -> None:
    assert escape_braces("{x} and }{").format() == "{x} and }{"


def test_v3_templates_are_registered_guarded_and_brace_safe() -> None:
    for key, template in (
        (SKILL_STEP_PROMPT_KEY, DEFAULT_SKILL_STEP_TEMPLATE),
        (COMPOSE_PROMPT_KEY, DEFAULT_COMPOSE_TEMPLATE),
    ):
        record = __import__("asyncio").run(PromptRegistry(None).get_enabled(key))
        assert record.template == template
        assert UNTRUSTED_GUARD_SENTINEL in record.template
        variables = extract_template_variables(template)
        assert {"role", "sources", "output_keys"} <= variables
        # Values are format ARGUMENTS, so hostile braces in a letter are inert.
        rendered = template.format(**{name: "{evil} {0}" for name in variables})
        assert "{evil} {0}" in rendered

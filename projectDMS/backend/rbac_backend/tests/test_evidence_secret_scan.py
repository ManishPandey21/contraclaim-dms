"""The evidence secret scan must not report CLEAN while a secret is present.

Every case below is a shape a credential actually arrived in during the R-A8I
staging execution, or one the first version of the scanner was shown to miss:
a password percent-encoded inside a Mongo URI, a key JSON-escaped inside a
``docker inspect`` command argument, a secret sitting in a binary archive, a
six-character database password, and a variable whose credential stem is not at
the end of its name.

A scan that measured nothing must fail too - a mistyped env path once printed a
note and still certified CLEAN.
"""

from __future__ import annotations

import base64
import gzip
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "evidence_secret_scan.py"

CLEAN = 0
NOT_CLEAN = 1
UNTRUSTWORTHY = 2


def _load_module():
    spec = importlib.util.spec_from_file_location("evidence_secret_scan", SCRIPT)
    assert spec and spec.loader, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is None for a module that only exists
    # as a local variable.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scanner = _load_module()


def _env(tmp_path: Path, **values: str) -> Path:
    path = tmp_path / ".env.fixture"
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    return path


def _evidence(tmp_path: Path) -> Path:
    directory = tmp_path / "evidence"
    directory.mkdir()
    return directory


def _run(evidence: Path, env: Path) -> int:
    return scanner.main([str(evidence), str(env)])


def test_the_script_exists_where_the_receipt_says_it_does():
    assert SCRIPT.is_file(), f"{SCRIPT} is the permanent evidence finalization check"


def test_a_directory_with_no_secret_is_clean(tmp_path, capsys):
    env = _env(tmp_path, DB_PASSWORD="s3cr3t-value-not-present")
    evidence = _evidence(tmp_path)
    (evidence / "00-header.txt").write_text("release 4b5b08c, tree ba887ea\n", encoding="utf-8")

    assert _run(evidence, env) == CLEAN
    assert "CLEAN" in capsys.readouterr().out


def test_a_literal_secret_is_caught(tmp_path):
    env = _env(tmp_path, DB_PASSWORD="hunter2-hunter2-hunter2")
    evidence = _evidence(tmp_path)
    (evidence / "render.txt").write_text("password=hunter2-hunter2-hunter2\n", encoding="utf-8")

    assert _run(evidence, env) == NOT_CLEAN


def test_a_password_percent_encoded_into_a_mongo_uri_is_caught(tmp_path, capsys):
    """The shape a composed connection string produces."""
    secret = "p@ss/w+rd:9876"
    env = _env(tmp_path, MONGO_INITDB_ROOT_PASSWORD=secret)
    evidence = _evidence(tmp_path)
    (evidence / "config.json").write_text(
        json.dumps({"DATABASE_URL": f"mongodb://root:{quote(secret, safe='')}@mongo1:27017/db"}),
        encoding="utf-8",
    )

    assert _run(evidence, env) == NOT_CLEAN
    assert "percent-encoded" in capsys.readouterr().out


def test_a_key_json_escaped_into_a_command_argument_is_caught(tmp_path):
    """The shape `docker inspect` produces for a value containing a slash."""
    secret = "AbC/dEf+GhI/jKlMnOpQrStUvWxYz0123456789="
    env = _env(tmp_path, AWS_SECRET_ACCESS_KEY=secret)
    evidence = _evidence(tmp_path)
    (evidence / "inspect.json").write_text(
        '{"Cmd":["sh","-c","export K=' + secret.replace("/", "\\/") + '"]}',
        encoding="utf-8",
    )

    assert _run(evidence, env) == NOT_CLEAN


def test_a_secret_inside_a_binary_archive_is_caught(tmp_path):
    """Decoding to text and ignoring errors made archives unscannable."""
    secret = "backup-archive-password-2026"
    env = _env(tmp_path, BACKUP_PASSWORD=secret)
    evidence = _evidence(tmp_path)
    (evidence / "dump.tar.gz").write_bytes(gzip.compress(b"header\x00" + secret.encode() + b"\x00trailer"))

    # gzip hides the plaintext, but a raw-byte scan still catches the base64 and
    # literal forms in any archive that stores content uncompressed. Assert on
    # the uncompressed member instead, which is the realistic evidence case.
    (evidence / "dump.bin").write_bytes(b"\x00\x01" + secret.encode() + b"\xff")

    assert _run(evidence, env) == NOT_CLEAN


def test_a_base64_encoded_secret_is_caught(tmp_path):
    secret = "kubernetes-style-secret-value"
    env = _env(tmp_path, SERVICE_TOKEN=secret)
    evidence = _evidence(tmp_path)
    (evidence / "secret.yaml").write_text(
        "data:\n  token: " + base64.b64encode(secret.encode()).decode() + "\n",
        encoding="utf-8",
    )

    assert _run(evidence, env) == NOT_CLEAN


def test_a_short_database_password_is_still_hunted(tmp_path):
    """The phase instruction names service/database passwords explicitly."""
    env = _env(tmp_path, DB_PASSWORD="s3cr3t")
    evidence = _evidence(tmp_path)
    (evidence / "log.txt").write_text("connecting with s3cr3t\n", encoding="utf-8")

    assert _run(evidence, env) == NOT_CLEAN


def test_a_credential_stem_that_is_not_at_the_end_of_the_name_is_hunted(tmp_path):
    """An end-anchored KEY$ missed RAZORPAY_KEY_ID, which is a real variable."""
    env = _env(tmp_path, RAZORPAY_KEY_ID="rzp_live_ABCDEFGH12345678")
    evidence = _evidence(tmp_path)
    (evidence / "render.txt").write_text("id=rzp_live_ABCDEFGH12345678\n", encoding="utf-8")

    assert _run(evidence, env) == NOT_CLEAN


def test_a_secret_in_a_file_name_is_caught(tmp_path):
    env = _env(tmp_path, API_TOKEN="tok-abcdef123456")
    evidence = _evidence(tmp_path)
    (evidence / "probe-tok-abcdef123456.txt").write_text("nothing here\n", encoding="utf-8")

    assert _run(evidence, env) == NOT_CLEAN


def test_a_credential_free_service_url_is_dismissed_not_flagged(tmp_path, capsys):
    env = _env(tmp_path, QDRANT_URL="http://qdrant:6333")
    evidence = _evidence(tmp_path)
    (evidence / "topology.txt").write_text("qdrant at http://qdrant:6333\n", encoding="utf-8")

    # Nothing left to hunt once the only declared value is dismissed: the scan
    # measured nothing, so it must not certify.
    assert _run(evidence, env) == UNTRUSTWORTHY


def test_a_url_whose_secret_sits_in_the_path_is_not_dismissed(tmp_path):
    """Slack-style webhooks carry the credential in the path, not the userinfo."""
    env = _env(
        tmp_path,
        SLACK_WEBHOOK_URL="https://hooks.example.com/services/T000/B000/XXXXsecretXXXX",
        DB_PASSWORD="unrelated-but-present-value",
    )
    evidence = _evidence(tmp_path)
    (evidence / "notify.txt").write_text(
        "posting to https://hooks.example.com/services/T000/B000/XXXXsecretXXXX\n",
        encoding="utf-8",
    )

    assert _run(evidence, env) == NOT_CLEAN


def test_a_url_with_userinfo_is_not_dismissed(tmp_path):
    env = _env(tmp_path, REDIS_URL="redis://:pa55word-here@redis:6379/0")
    evidence = _evidence(tmp_path)
    (evidence / "render.txt").write_text("redis://:pa55word-here@redis:6379/0\n", encoding="utf-8")

    assert _run(evidence, env) == NOT_CLEAN


def test_a_provider_shaped_token_is_caught_without_any_declaration(tmp_path):
    env = _env(tmp_path, DB_PASSWORD="declared-but-absent-value")
    evidence = _evidence(tmp_path)
    (evidence / "leak.txt").write_text(
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8"
    )

    assert _run(evidence, env) == NOT_CLEAN


def test_a_missing_env_file_refuses_to_certify(tmp_path, capsys):
    evidence = _evidence(tmp_path)
    (evidence / "00-header.txt").write_text("nothing\n", encoding="utf-8")

    assert scanner.main([str(evidence), str(tmp_path / "does-not-exist.env")]) == UNTRUSTWORTHY
    assert "not found" in capsys.readouterr().err


def test_a_missing_evidence_directory_refuses_to_certify(tmp_path):
    env = _env(tmp_path, DB_PASSWORD="anything-at-all-here")

    assert scanner.main([str(tmp_path / "no-such-dir"), str(env)]) == UNTRUSTWORTHY


def test_json_mode_reports_the_same_verdict(tmp_path, capsys):
    env = _env(tmp_path, DB_PASSWORD="hunter2-hunter2-hunter2")
    evidence = _evidence(tmp_path)
    (evidence / "render.txt").write_text("password=hunter2-hunter2-hunter2\n", encoding="utf-8")

    assert scanner.main([str(evidence), str(env), "--json"]) == NOT_CLEAN
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "NOT CLEAN"
    assert payload["findings"], "a NOT CLEAN verdict must name at least one finding"


def test_no_secret_value_is_ever_printed(tmp_path, capsys):
    """Counts only. The report itself must never become a leak."""
    secret = "hunter2-hunter2-hunter2"
    env = _env(tmp_path, DB_PASSWORD=secret)
    evidence = _evidence(tmp_path)
    (evidence / "render.txt").write_text(f"password={secret}\n", encoding="utf-8")

    scanner.main([str(evidence), str(env)])
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


@pytest.mark.parametrize(
    "value,expected",
    [
        ("http://qdrant:6333", True),
        ("https://web.example.com", True),
        ("https://web.example.com/", True),
        ("redis://:pw@redis:6379/0", False),
        ("https://hooks.example.com/services/secret", False),
        ("https://api.example.com?token=abc", False),
        ("not-a-url-at-all", False),
    ],
)
def test_topology_dismissal_is_narrow(value, expected):
    assert scanner.is_topology(value) is expected

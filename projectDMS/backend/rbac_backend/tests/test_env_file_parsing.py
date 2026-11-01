"""F-A8M-2 - an environment file is data, and no release script may run it.

Four release scripts loaded their environment with

    set -a; source "$ENV_FILE"; set +a

and `.env.staging` carried an unquoted `DATABASE_URL` whose value contains `&`.
`source` executes the file, so the shell read the `&` as a control operator,
backgrounded the assignment, and the variable never reached the script:
`production_backup.sh` exited on `MONGO_URI or DATABASE_URL is required` and
**staging took no backup**. Production's `.env` happens to quote the same value,
which is the only reason production backups work - nothing in the tree required
it, and `.env.example` shipped the unquoted form.

Truncation is the mild failure. `source` on an environment file is arbitrary
code execution as whoever runs the backup, which on this deployment is root.

Two halves, and both are needed:

* `scripts/lib/env_file.sh` reads the file as data. The hostile-value rows below
  are the point of it - a value containing `$( )`, backticks or `;` has to come
  back verbatim, and nothing may run.
* the tracked templates quote what needs quoting, so an operator who copies one
  gets a file that is safe under *any* consumer, including the shell-based ones
  this repository does not control.

Neither half alone closes it: a parser cannot fix a file someone sources by
hand, and a quoted template cannot stop a script from executing the next value
an operator adds.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "scripts" / "lib" / "env_file.sh"
TEMPLATES = (REPO_ROOT / ".env.example", REPO_ROOT / ".env.staging.example")

#: Every release script that loads an environment file. Named rather than
#: globbed: a new script that sources an env file should fail the static gate
#: below by being added here, not slip through because the glob missed it.
ENV_LOADING_SCRIPTS = (
    "scripts/production_backup.sh",
    "scripts/backup_offsite_s3.sh",
    "scripts/pre_deploy_readiness.sh",
    "scripts/post_deploy_verify.sh",
)

#: Characters that change what a line means when the shell reads it.
SHELL_SPECIAL = set(" \t&;|<>()$`\\\"'*?[]#~!{}")

ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _usable_bash():
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run([candidate, "-c", "true"], capture_output=True, timeout=30)
        except OSError:
            continue
        if probe.returncode == 0:
            return candidate
    return None


BASH = _usable_bash()


@pytest.fixture(scope="module", autouse=True)
def _bash_available() -> None:
    """Skip for a developer without bash; FAIL when the run is evidence.

    A whole shell suite that vanishes green is the shape `staging_gate.py`
    exists to prevent: the run reports success having measured nothing. On CI
    and inside a certification run there is always a bash, so an absent one
    there is a broken environment and must be reported as a failure, not as a
    skip.
    """
    if BASH is not None:
        return
    if os.environ.get("CI") or os.environ.get("CONTRACLAIM_STAGING_GATE"):
        pytest.fail(
            "no usable bash on this host, and this run is being offered as "
            "evidence (CI / CONTRACLAIM_STAGING_GATE). A skipped shell suite "
            "measures nothing."
        )
    pytest.skip("no usable bash on this host")


@pytest.fixture()
def load(tmp_path: Path):
    """Parse an env file and report back what the shell ended up holding.

    The values are printed one per record with an explicit terminator rather
    than by `env`, so a value containing a newline survives the round trip and
    is compared as itself.
    """

    def run(content: str, *keys: str):
        env_file = tmp_path / "sample.env"
        env_file.write_text(content, encoding="utf-8", newline="\n")
        readers = "".join(
            f'printf "%s\\037%s\\036" {key!r} "${{{key}-<UNSET>}}"\n' for key in keys
        )
        script = (
            "set -euo pipefail\n"
            f'. "{LIB.as_posix()}"\n'
            f'env_file_load "{env_file.as_posix()}"\n' + readers
        )
        result = subprocess.run(
            [BASH, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={"PATH": os.environ["PATH"], "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
        )
        values = {}
        for record in result.stdout.split("\036"):
            if "\037" in record:
                key, _, value = record.partition("\037")
                values[key] = value
        result.values = values
        return result

    run.dir = tmp_path
    return run


# --- 1. Values that must survive unchanged ---------------------------------


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(
            "mongodb://u:p@mongo1:27017,mongo2:27017/db?replicaSet=rsstg&authSource=admin",
            id="the-uri-that-broke-staging",
        ),
        pytest.param("key=value=more", id="embedded-equals"),
        pytest.param("abc#notacomment", id="hash-with-no-leading-space"),
        pytest.param("C:\\Users\\santo\\path", id="backslashes"),
        pytest.param("p@ssw:rd/with+slashes%20", id="uri-characters"),
        pytest.param("a|b<c>d(e)f{g}h[i]j*k?l~m!n", id="every-other-metacharacter"),
    ],
)
def test_an_unquoted_value_arrives_whole(load, value):
    result = load(f"TARGET={value}\n", "TARGET")

    assert result.returncode == 0, result.stderr
    assert result.values["TARGET"] == value


@pytest.mark.parametrize(
    "line, expected",
    [
        pytest.param('TARGET="a b   c"', "a b   c", id="double-quoted-spaces"),
        pytest.param("TARGET='a b   c'", "a b   c", id="single-quoted-spaces"),
        pytest.param('TARGET="he said \\"hi\\""', 'he said "hi"', id="escaped-quote"),
        pytest.param('TARGET="one\\ntwo"', "one\ntwo", id="escaped-newline"),
        pytest.param("TARGET='literal \\n stays'", "literal \\n stays", id="single-quotes-do-not-escape"),
        pytest.param("TARGET=abc # trailing comment", "abc", id="space-hash-is-a-comment"),
        pytest.param('TARGET="abc # not a comment"', "abc # not a comment", id="hash-inside-quotes"),
        pytest.param("export TARGET=exported", "exported", id="export-prefix"),
        pytest.param("TARGET=", "", id="empty-value"),
        pytest.param("   TARGET=indented", "indented", id="leading-whitespace"),
        pytest.param("TARGET=trailing   ", "trailing", id="trailing-whitespace-stripped"),
    ],
)
def test_the_grammar_is_the_one_compose_reads(load, line, expected):
    result = load(line + "\n", "TARGET")

    assert result.returncode == 0, result.stderr
    assert result.values["TARGET"] == expected


def test_a_quoted_value_may_span_lines(load):
    result = load('TARGET="line1\nline2"\nAFTER=next\n', "TARGET", "AFTER")

    assert result.returncode == 0, result.stderr
    assert result.values["TARGET"] == "line1\nline2"
    assert result.values["AFTER"] == "next", "parsing resumed on the wrong line"


def test_comments_and_blank_lines_are_skipped(load):
    result = load("# a comment\n\n   \n# TARGET=commented-out\nTARGET=real\n", "TARGET")

    assert result.values["TARGET"] == "real"


# --- 2. Nothing may execute -------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("$(touch pwned)", id="command-substitution"),
        pytest.param("`touch pwned`", id="backticks"),
        pytest.param("a;touch pwned", id="semicolon"),
        pytest.param("a&&touch pwned", id="and-and"),
        pytest.param("a|touch pwned", id="pipe"),
        pytest.param("a\n", id="bare-newline"),
        pytest.param("$(touch pwned) && rm -rf /", id="the-whole-shape"),
    ],
)
def test_a_value_containing_shell_syntax_is_data(load, value):
    """The malicious control. An environment file arrives from operators, from
    `scp`, from a template. Under `source` every one of these runs as whoever
    runs the backup, which on this deployment is root."""
    result = load(f"TARGET={value}", "TARGET")

    assert result.returncode == 0, result.stderr
    assert not (load.dir / "pwned").exists(), "the parser executed the value"
    assert "pwned" not in (result.stderr or "")


def test_a_single_quoted_value_containing_shell_syntax_is_data(load):
    result = load("TARGET='$(touch pwned) `id` ; rm -rf /'\n", "TARGET")

    assert result.returncode == 0, result.stderr
    assert result.values["TARGET"] == "$(touch pwned) `id` ; rm -rf /"
    assert not (load.dir / "pwned").exists()


def test_the_control_itself_works(load):
    """Self-check for the rows above. If `touch pwned` could never create the
    marker from this directory, every "nothing ran" assertion is vacuous."""
    marker = load.dir / "pwned"
    subprocess.run([BASH, "-c", "touch pwned"], cwd=str(load.dir), check=True)

    assert marker.exists(), "the marker cannot be created, so the controls prove nothing"
    marker.unlink()


# --- 3. Failing closed, and saying nothing --------------------------------


@pytest.mark.parametrize(
    "content, reason",
    [
        pytest.param("this is not an assignment\n", "not a KEY=VALUE", id="not-an-assignment"),
        pytest.param("9INVALID=x\n", "not a KEY=VALUE", id="key-starts-with-a-digit"),
        pytest.param("HAS-DASH=x\n", "not a KEY=VALUE", id="key-has-a-dash"),
        pytest.param('TARGET="unterminated\n', "unterminated", id="unterminated-quote"),
        pytest.param('TARGET="closed" and then junk\n', "trailing characters", id="junk-after-a-quoted-value"),
    ],
)
def test_a_malformed_line_is_refused(load, content, reason):
    """Fail closed. A parser that skipped what it could not read would give the
    caller a partial environment and no way to know it."""
    result = load(content, "TARGET")

    assert result.returncode != 0
    assert reason in result.stderr


def test_a_parse_error_names_the_line_and_never_the_value(load):
    """The file is secrets. An error message that quoted the offending line
    would put one into a log, a CI transcript, or release evidence."""
    result = load('SECRET=fine\nTARGET="s3cr3t-unterminated\n', "TARGET")

    assert result.returncode != 0
    assert ":2:" in result.stderr
    assert "s3cr3t" not in result.stderr


def test_a_successful_load_prints_nothing(load):
    result = load("SECRET=hunter2\n", "SECRET")

    assert result.returncode == 0
    assert result.stderr == ""
    # The value reaches the caller only through the environment. Everything on
    # stdout here was printed by this test's own reader, not by the loader.
    assert result.values["SECRET"] == "hunter2"


def test_a_missing_file_is_not_an_error(load, tmp_path: Path):
    script = (
        "set -euo pipefail\n"
        f'. "{LIB.as_posix()}"\n'
        f'env_file_load "{(tmp_path / "absent.env").as_posix()}"\n'
        'printf "ok"\n'
    )
    result = subprocess.run([BASH, "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok"


# --- 4. No release script may source an environment file -------------------


@pytest.mark.parametrize("relative", ENV_LOADING_SCRIPTS)
def test_a_release_script_does_not_execute_its_environment_file(relative):
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")

    assert "set -a" not in source, f"{relative} still exports by executing the file"
    assert re.search(r"^\s*(source|\.)\s+\"?\$(\{)?(ENV_FILE|file)", source, re.M) is None, (
        f"{relative} still sources its environment file"
    )
    assert "env_file_load" in source, f"{relative} does not use the data parser"


def test_every_script_that_loads_an_env_file_is_listed_here():
    """Self-check: the gate above is a fixed list, so it has to be told when the
    set grows. Anything that mentions an env file and is not listed fails here.
    """
    unlisted = []
    for path in sorted((REPO_ROOT / "scripts").glob("*.sh")):
        relative = f"scripts/{path.name}"
        if relative in ENV_LOADING_SCRIPTS:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(source|\.)\s+\"?\$(\{)?(ENV_FILE|file)", source, re.M):
            unlisted.append(relative)
    assert not unlisted, (
        "these scripts source an environment file and are not covered by the "
        f"gate above: {unlisted}"
    )


# --- 5. The templates an operator copies -----------------------------------


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_a_template_value_that_needs_quoting_is_quoted(template: Path):
    """The other half. The parser protects the scripts in this repository; the
    quoting protects everything downstream of a file an operator copied - a
    hand-written `source`, an unrelated tool, the next script nobody has written
    yet. `.env.staging.example` shipped the unquoted URI that broke R-A8M.
    """
    offenders = []
    for lineno, raw in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        match = ASSIGNMENT.match(line)
        if not match:
            offenders.append(f"{template.name}:{lineno}: not a KEY=VALUE assignment")
            continue
        key, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            continue
        special = sorted(SHELL_SPECIAL & set(value))
        if special:
            offenders.append(
                f"{template.name}:{lineno}: {key} is unquoted and contains {''.join(special)!r}"
            )

    assert not offenders, (
        "an unquoted value containing shell syntax is F-A8M-2 waiting to happen:"
        "\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_the_template_parses_and_the_uri_arrives_whole(load, template: Path):
    """End to end: the shipped template, through the real parser, with the value
    that used to vanish compared against what the file says it is."""
    result = load(template.read_text(encoding="utf-8"), "DATABASE_URL")

    assert result.returncode == 0, result.stderr
    value = result.values["DATABASE_URL"]
    assert value.startswith("mongodb://")
    assert "&authSource=admin" in value, "the value was truncated at the ampersand"
    assert value.endswith("retryWrites=true")


def test_the_template_check_would_catch_the_r_a8m_shape(tmp_path: Path):
    """Negative control for the check itself, over the exact line that shipped."""
    offending = tmp_path / "regression.env.example"
    offending.write_text(
        "DATABASE_URL=mongodb://u:p@mongo1:27017/db?replicaSet=rsstg&authSource=admin\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="unquoted"):
        test_a_template_value_that_needs_quoting_is_quoted(offending)


# --- 6. The three defects a review found in the parser itself --------------
#
# All three were the parser disagreeing with its own documented grammar, which
# is the failure mode a hand-written parser is for: the header says what compose
# does, and only a test can say whether this file does it.


#: One literal backslash, spelled without escapes so no reader of this file has
#: to count them. The rows below are about backslash handling, and writing them
#: with `\\` is how the defect got past review in the first place.
BACKSLASH = chr(92)


def test_an_escaped_backslash_is_decoded(load):
    """A doubled backslash inside a double-quoted value decodes to one.

    The case arm was quoted, and quoting a case pattern removes its special
    meaning - so it matched a literal two-character string, which a single
    character never is. The arm was dead: the doubled backslash fell through
    undecoded, and the character after it was then re-read as an escape.
    """
    result = load('TARGET="a' + BACKSLASH * 2 + 'b"\n', "TARGET")

    assert result.returncode == 0, result.stderr
    assert result.values["TARGET"] == "a" + BACKSLASH + "b"


def test_an_escaped_backslash_does_not_swallow_the_next_character(load):
    """The second half of the same defect.

    A doubled backslash followed by `n` is a backslash and the letter `n`, not a
    newline: the first backslash consumes the second, so nothing is left to
    escape the `n`. Before the fix the dead arm left the first backslash in
    place and the pair `\\n` was then read as the newline escape.
    """
    result = load('TARGET="a' + BACKSLASH * 2 + 'nb"\n', "TARGET")

    assert result.values["TARGET"] == "a" + BACKSLASH + "nb"
    assert "\n" not in result.values["TARGET"]


def test_an_inline_comment_is_cut_at_the_first_hash_not_the_last(load):
    """Compose reads `foo # one # two` as `foo`. The ERE this replaces was
    greedy in the other direction and kept `foo # one`, so a value could carry
    half a comment into the environment."""
    result = load("TARGET=foo # one # two\n", "TARGET")

    assert result.values["TARGET"] == "foo"


def test_a_hash_with_no_leading_space_is_still_part_of_the_value(load):
    """The positive control for the row above: without it, a parser that cut at
    every `#` would pass the greedy test and be wrong in the other direction."""
    result = load("TARGET=abc#def#ghi\n", "TARGET")

    assert result.values["TARGET"] == "abc#def#ghi"


def test_a_quoted_value_closing_on_a_file_with_no_trailing_newline(load):
    """`read` returns non-zero on a final line with no newline while still
    having filled the variable. Without `|| [[ -n "$line" ]]` in the
    continuation, such a file was rejected as unterminated."""
    result = load('TARGET="l1\nl2"', "TARGET")

    assert result.returncode == 0, result.stderr
    assert result.values["TARGET"] == "l1\nl2"


def test_a_genuinely_unterminated_quote_is_still_refused(load):
    """The negative control for the row above. Accepting an unterminated quote
    to make a no-trailing-newline file work would trade one defect for a worse
    one: a truncated secret that looks like a value."""
    result = load('TARGET="oops\n', "TARGET")

    assert result.returncode != 0
    assert "unterminated" in result.stderr

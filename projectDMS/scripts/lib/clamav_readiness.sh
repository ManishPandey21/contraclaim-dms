# shellcheck shell=bash
# Live antivirus readiness for post_deploy_verify.sh (R-A8Y).
#
# Production's ClamAV was healthy, reachable and fail-closed while serving a
# 70-day-old signature database: every check asked whether clamd answers, none
# asked what it answers with. This runs scripts/check_clamav_signature_freshness.py
# INSIDE the backend container - the network path uploads actually take - by
# feeding the file on stdin, so no image change is needed and the checker that
# runs is the one in the deployed checkout.
#
# Contract (the checker's exit codes): 0 PASS, 3 WARN, anything else FAIL.
# Anything else includes "compose could not exec", "python missing" and
# "invalid policy" - a check that could not be evaluated is not a pass.
#
# Requires the caller's pass/warn/fail helpers and ROOT_DIR, ENV_FILE,
# COMPOSE_FILES. Read-only: VERSION and two INSTREAM scans of in-memory bytes.

# clamav_antivirus_disabled <value>
#   Succeeds when the backend would read ANTIVIRUS_ENABLED as false. Mirrors
#   pydantic's boolean parsing (0/off/f/false/n/no, any case), so a value like
#   `FALSE` or `off` cannot slip past as "enabled" and get a probe instead of a
#   failure.
clamav_antivirus_disabled() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
    0 | off | f | false | n | no) return 0 ;;
    *) return 1 ;;
  esac
}

# clamav_readiness_check <max_age_hours> <warn_hours>
#   Empty arguments fall back to the checker's defaults (48 / 24).
clamav_readiness_check() {
  local max_age=${1:-48} warn_hours=${2:-24} out rc summary
  # `|| rc=$?` keeps a non-zero checker from tripping the caller's `set -e`:
  # a FAIL must be reported and counted, not abort the rest of verification.
  out=$(docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T backend \
    python - --max-age-hours "$max_age" --warn-hours "$warn_hours" \
    <"$ROOT_DIR/scripts/check_clamav_signature_freshness.py" 2>&1) && rc=0 || rc=$?
  printf '%s\n' "$out" >/tmp/clamav_readiness.out
  summary=$(grep -E '^CLAMAV_(READINESS|FRESHNESS)=' <<<"$out" | tail -n 1 || true)
  case "$rc" in
    0)
      pass "ClamAV readiness: clamd reachable, loaded signatures within ${max_age} h, clean file accepted, EICAR rejected (${summary})"
      ;;
    3)
      warn "ClamAV signatures are older than ${warn_hours} h but within ${max_age} h; check freshclam egress (${summary})"
      ;;
    *)
      fail "ClamAV readiness FAILED (exit ${rc}): ${summary:-$(tail -n 3 <<<"$out" | tr '\n' ' ')}; details in /tmp/clamav_readiness.out"
      ;;
  esac
}

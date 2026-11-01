# shellcheck shell=bash
#
# Which edge must answer after a deploy, and what "answer" means here.
#
# `post_deploy_verify.sh` used to decide that inline:
#
#     if [[ -n "$PUBLIC_BASE_URL" ]]; then
#       http_check "${PUBLIC_BASE_URL%/}/health" "Public gateway health responded"
#     fi
#
# Absence was a silent skip, so a production run with that variable unset -
# unexported, misspelt, dropped from `.env` - reported the same "10 PASS, 0
# failures" as one that reached the public edge. The single control over the
# surface every user arrives through was the one control that could disappear
# without saying so.
#
# The mirror image is just as wrong. Same-host staging is built with **no public
# DNS**: its gateway is reached over loopback behind a run-owned TLS terminator
# (R-A8Q had to build one before Gate 3 bullet 1 could be measured at all). A
# check demanding a public hostname there demands a property staging is designed
# not to have, and the only ways to satisfy it are to point the staging run at
# production's hostname - measuring production and filing it as staging evidence
# - or to delete the check.
#
# So the deployment says which mode it is in, and the modes differ:
#
#   production  edge REQUIRED, TLS REQUIRED, public DNS REQUIRED.
#               A missing edge is a FAILURE. Loopback, private ranges and
#               internal-only suffixes are refused: the gateway answers on its
#               own network, so verifying it there passes while every real user
#               is getting a certificate error.
#
#   staging     edge REQUIRED, TLS REQUIRED, public DNS NOT required.
#               Loopback and private addresses are the staging topology, not a
#               defect. TLS still is required, because `AUTH_COOKIE_SECURE=true`
#               means no session cookie survives plain HTTP and the run would be
#               measuring a stack nothing can log into. What staging may not do
#               is address a production host.
#
# The mode is never guessed. `DEPLOY_VERIFY_MODE` states it; otherwise
# `ENVIRONMENT` decides; anything else REFUSES with exit 2, the same rule
# `check_maintenance_time_budget.py` applies to a window it cannot parse.
# Guessing "staging" hands production the staging exemption. Guessing
# "production" makes every staging run demand DNS it does not have. There is no
# safe default, so there is no default.
#
# Contract: `edge_verification_plan` prints one line of `key=value` fields on
# success and exits 0; on refusal it prints the reason on stderr and exits 2.
# Nothing here performs a request - the plan is the decision, the caller makes
# the call. That split is what lets every rule below be tested without a network.

#: Exit status for "this deployment's edge cannot be resolved". Distinct from 1
#: so a caller can tell a refusal from a 404.
EDGE_PLAN_REFUSED=2

_edge_lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

_edge_refuse() {
  printf 'edge verification: %s\n' "$1" >&2
  return "$EDGE_PLAN_REFUSED"
}

#: The scheme of a URL, lowercased. Empty when there is none.
edge_url_scheme() {
  local url=$1
  case "$url" in
    *://*) _edge_lower "${url%%://*}" ;;
    *) printf '' ;;
  esac
}

#: The host of a URL, lowercased, without userinfo, port or brackets.
edge_url_host() {
  local rest=${1#*://}
  rest=${rest%%/*}
  rest=${rest%%\?*}
  rest=${rest##*@}
  case "$rest" in
    \[*\]*)
      rest=${rest#[}
      rest=${rest%%]*}
      ;;
    *:*) rest=${rest%%:*} ;;
  esac
  _edge_lower "$rest"
}

#: Does this host address something only reachable from inside the deployment?
#:
#: Deliberately a positive list of the shapes that are NOT public, rather than a
#: guess at what is: an unrecognised name is treated as public, so the rule can
#: only ever refuse something it recognises.
edge_host_is_non_public() {
  local host
  host=$(_edge_lower "$1")

  case "$host" in
    localhost | localhost.* | *.localhost) return 0 ;;
  esac

  # An IPv6 literal is decided by its range and NOTHING else, and it is decided
  # first. The dot rule at the bottom of this function would otherwise refuse
  # every IPv6 address - they contain no dots - so `2001:db8::1` was being
  # rejected as "not a public DNS name". The `fd??:` shape also missed a short
  # first group: `fd1::1` is a unique-local address and `fd??:` needs exactly two
  # hex digits before the colon.
  case "$host" in
    *:*)
      case "$host" in
        ::1 | 0:0:0:0:0:0:0:1) return 0 ;;
        fe80:* | fe8*:* | fe9*:* | fea*:* | feb*:*) return 0 ;;   # link-local fe80::/10
        fc*:* | fd*:*) return 0 ;;                                 # unique-local fc00::/7
        *) return 1 ;;                                             # global unicast
      esac
      ;;
  esac

  case "$host" in
    127.*) return 0 ;;
    10.*) return 0 ;;
    192.168.*) return 0 ;;
    169.254.*) return 0 ;;
    # 0.0.0.0/8, "this network". No public name begins `0.`; the over-refusal is
    # in the safe direction and would only ever reject a wildcard bind address.
    0.0.0.0 | 0.*) return 0 ;;
    # RFC 1918 172.16.0.0/12 - the second octet, not a prefix match, because
    # 172.200.x is public and `172.2*` would swallow it.
    172.1[6-9].* | 172.2[0-9].* | 172.3[01].*) return 0 ;;
    # Names that resolve only inside a network.
    *.local | *.internal | *.localdomain | *.home.arpa | *.lan | *.intranet) return 0 ;;
  esac

  # A single label with no dot is a container or /etc/hosts name (`gateway`,
  # `backend`), never a public DNS name.
  case "$host" in
    *.*) return 1 ;;
    *) return 0 ;;
  esac
}

#: production | staging, or a refusal. Never a default.
edge_verification_mode() {
  local declared=${DEPLOY_VERIFY_MODE:-}
  local label=${ENVIRONMENT:-}

  if [[ -n "$declared" ]]; then
    case "$(_edge_lower "$declared")" in
      production) printf 'production'; return 0 ;;
      staging) printf 'staging'; return 0 ;;
      *)
        _edge_refuse "cannot decide which edge to verify: DEPLOY_VERIFY_MODE=${declared} is not 'production' or 'staging'"
        return $?
        ;;
    esac
  fi

  case "$(_edge_lower "$label")" in
    production) printf 'production'; return 0 ;;
    staging) printf 'staging'; return 0 ;;
  esac

  _edge_refuse "cannot decide which edge to verify: set DEPLOY_VERIFY_MODE to 'production' or 'staging' (ENVIRONMENT=${label:-<unset>} does not say)"
}

#: The marker `.env.staging.example` ships instead of the real hostnames.
#:
#: It cannot ship them: `test_staging_compose.py` refuses a tracked staging
#: template that names the production host, and it is right to — a template that
#: ships one is a copy-paste away from pointing staging at production, which is
#: the exact failure `PRODUCTION_PUBLIC_HOSTS` exists to prevent. The cost is an
#: operator who never edits the placeholder, whose refusal list then matches
#: nothing while the plan line reports it as declared. A control that is present,
#: green and inert is worse than an absent one, so this is refused rather than
#: treated as a hostname.
EDGE_HOST_PLACEHOLDER="REPLACE-WITH"

_edge_is_placeholder() {
  local value
  value=$(_edge_lower "$1")
  case "$value" in
    *"$(_edge_lower "$EDGE_HOST_PLACEHOLDER")"*) return 0 ;;
    *) return 1 ;;
  esac
}

#: Is this host one of the declared production hosts?
_edge_host_is_production() {
  local host=$1 declared=$2 candidate
  [[ -z "$declared" ]] && return 1
  local IFS=', '
  for candidate in $declared; do
    [[ -z "$candidate" ]] && continue
    if [[ "$host" == "$(_edge_lower "$candidate")" ]]; then
      return 0
    fi
  done
  return 1
}

#: The whole decision, as one function. Prints the plan or refuses.
edge_verification_plan() {
  local mode url host scheme
  mode=$(edge_verification_mode) || return "$EDGE_PLAN_REFUSED"

  local public=${PUBLIC_BASE_URL:-}
  local staging=${STAGING_EDGE_BASE_URL:-}
  local production_hosts=${PRODUCTION_PUBLIC_HOSTS:-}

  if [[ "$mode" == "production" ]]; then
    # Deliberately NOT falling back to STAGING_EDGE_BASE_URL. A staging override
    # left in the environment must not be able to satisfy production.
    url=$public
    [[ -z "$url" ]] && {
      _edge_refuse "production public edge is not configured: PUBLIC_BASE_URL is unset, and in production mode an unset edge is a failure rather than a skipped check"
      return $?
    }
  else
    url=${staging:-$public}
    [[ -z "$url" ]] && {
      _edge_refuse "staging edge is not configured: set STAGING_EDGE_BASE_URL (or PUBLIC_BASE_URL) to the staging gateway. Staging is exempt from public DNS, not from having an edge"
      return $?
    }
  fi

  url=${url%/}
  scheme=$(edge_url_scheme "$url")
  host=$(edge_url_host "$url")

  if [[ "$scheme" != "https" ]]; then
    _edge_refuse "${mode} edge ${url} is not TLS: AUTH_COOKIE_SECURE=true, so no session cookie survives ${scheme:-a schemeless URL} and the run would measure a stack nothing can log into"
    return $?
  fi

  if [[ -z "$host" ]]; then
    _edge_refuse "${mode} edge ${url} has no host"
    return $?
  fi

  # Reported, not just applied. A plan line naming a control it did NOT consult
  # reads as "checked against these" - the same shape of claim as a backup
  # reported `ok` because its mtime was fresh.
  local reported_hosts="${production_hosts:-none-declared}"
  local public_dns_required="not-required"
  if [[ "$mode" == "production" ]]; then
    public_dns_required="required"
    reported_hosts="not-applicable"
    if edge_host_is_non_public "$host"; then
      _edge_refuse "production edge ${url} is not a public DNS name (${host}). The gateway answers on its own network, so this passes while every real user is reaching a different address"
      return $?
    fi
  else
    if [[ -n "$production_hosts" ]] && _edge_is_placeholder "$production_hosts"; then
      _edge_refuse "PRODUCTION_PUBLIC_HOSTS is still the ${EDGE_HOST_PLACEHOLDER}... placeholder from .env.staging.example (${production_hosts}). It would match no host, so the staging-pointed-at-production refusal could never fire while the plan reported the list as declared. Put the real production hostnames in .env.staging, or unset it and accept that the rule cannot fire"
      return $?
    fi
    if _edge_host_is_production "$host" "$production_hosts"; then
      _edge_refuse "staging edge ${url} addresses a production host (${host}). A staging run pointed there measures production and files the result as staging evidence"
      return $?
    fi
  fi

  printf 'mode=%s url=%s tls=required public_dns=%s production_hosts=%s\n' \
    "$mode" "$url" "$public_dns_required" "$reported_hosts"
}

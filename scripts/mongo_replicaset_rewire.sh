#!/usr/bin/env bash
set -euo pipefail

# Conservative MongoDB replica-set diagnostics and repair helper for Ubuntu.
# Default mode is read-only. Reconfiguration requires explicit subcommands/flags.

RS_NAME=${RS_NAME:-rs0}
MONGO_HOST=${MONGO_HOST:-127.0.0.1}
MONGO_PORT=${MONGO_PORT:-27017}
MONGO_URI=${MONGO_URI:-}
MONGO_AUTH_DB=${MONGO_AUTH_DB:-admin}
MONGOD_CONF=${MONGOD_CONF:-/etc/mongod.conf}
BACKUP_DIR=${BACKUP_DIR:-./backups/mongo-rs-config}
MEMBERS=${MEMBERS:-}
SINCE=${SINCE:-"2 hours ago"}

APPLY=false
FORCE=false
I_UNDERSTAND=false

usage() {
  cat <<'EOF'
Usage:
  scripts/mongo_replicaset_rewire.sh diagnose
  scripts/mongo_replicaset_rewire.sh backup-config
  MEMBERS="mongo1:27017,mongo2:27017,mongo3:27017" RS_NAME=rs0 scripts/mongo_replicaset_rewire.sh initiate [--apply]
  MEMBERS="mongo1:27017,mongo2:27017,mongo3:27017" RS_NAME=rs0 scripts/mongo_replicaset_rewire.sh reconfig [--apply] [--force --i-understand-force-reconfig-risk]

Environment:
  RS_NAME       Replica set name. Default: rs0
  MONGO_HOST    Host used for local mongosh checks. Default: 127.0.0.1
  MONGO_PORT    Port used for local mongosh checks. Default: 27017
  MONGO_URI     Optional full MongoDB URI for mongosh. If set, overrides host/port.
  MEMBERS       Comma-separated replica-set member addresses, e.g. mongo1:27017,mongo2:27017,mongo3:27017
  MONGOD_CONF   mongod config path. Default: /etc/mongod.conf
  BACKUP_DIR    Directory for rs.conf()/rs.status() snapshots. Default: ./backups/mongo-rs-config
  SINCE         journalctl window for diagnose. Default: "2 hours ago"

Safety:
  diagnose and backup-config are read-only.
  initiate/reconfig print the planned action unless --apply is provided.
  force reconfig can cause rollback/election hazards and requires both:
    --force --i-understand-force-reconfig-risk
EOF
}

section() {
  printf '\n== %s ==\n' "$1"
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

mongo_target_args() {
  if [[ -n "$MONGO_URI" ]]; then
    printf '%s\n' "$MONGO_URI"
  else
    printf '%s\n' "--host" "$MONGO_HOST" "--port" "$MONGO_PORT"
  fi
}

mongo_eval() {
  if [[ -n "$MONGO_URI" ]]; then
    mongosh "$MONGO_URI" --quiet --eval "$1"
  else
    mongosh --host "$MONGO_HOST" --port "$MONGO_PORT" --quiet --eval "$1"
  fi
}

mongo_script() {
  if [[ -n "$MONGO_URI" ]]; then
    mongosh "$MONGO_URI" --quiet "$@"
  else
    mongosh --host "$MONGO_HOST" --port "$MONGO_PORT" --quiet "$@"
  fi
}

require_mongosh() {
  have mongosh || die "mongosh is required. Install MongoDB Shell before running this script."
}

member_count() {
  awk -F, '{print NF}' <<<"$MEMBERS"
}

validate_members() {
  [[ -n "$MEMBERS" ]] || die "MEMBERS is required, e.g. MEMBERS='mongo1:27017,mongo2:27017,mongo3:27017'"
  local count
  count=$(member_count)
  [[ "$count" -ge 1 ]] || die "MEMBERS must contain at least one host:port"
  IFS=',' read -r -a member_array <<<"$MEMBERS"
  for member in "${member_array[@]}"; do
    [[ "$member" == *:* ]] || die "Member '$member' must be in host:port form"
  done
}

check_reachability() {
  [[ -n "$MEMBERS" ]] || {
    warn "MEMBERS not set; skipping inter-node reachability checks"
    return 0
  }

  section "Member DNS and TCP reachability"
  IFS=',' read -r -a member_array <<<"$MEMBERS"
  for member in "${member_array[@]}"; do
    local host=${member%:*}
    local port=${member##*:}
    printf '\n-- %s --\n' "$member"
    getent hosts "$host" || warn "DNS/hosts lookup failed for $host"
    if have nc; then
      nc -vz -w 3 "$host" "$port" || warn "TCP connection failed to $member"
    else
      timeout 3 bash -c "cat < /dev/null > /dev/tcp/$host/$port" \
        && printf 'TCP OK: %s\n' "$member" \
        || warn "TCP connection failed to $member"
    fi
  done
}

diagnose() {
  require_mongosh

  section "Host identity"
  hostname || true
  hostname -f || true
  hostname -I || true

  section "MongoDB service status"
  if have systemctl; then
    systemctl status mongod --no-pager -l || true
    systemctl is-enabled mongod || true
  else
    warn "systemctl not available"
  fi

  section "MongoDB versions"
  mongod --version 2>/dev/null | head -n 3 || warn "mongod binary not found on PATH"
  mongosh --version || true

  section "Listening ports"
  if have ss; then
    ss -lntp | grep -E "(:${MONGO_PORT}\b|mongod)" || true
  elif have netstat; then
    netstat -lntp | grep -E "(:${MONGO_PORT}\b|mongod)" || true
  else
    warn "Neither ss nor netstat is available"
  fi

  section "MongoDB config summary: $MONGOD_CONF"
  if [[ -r "$MONGOD_CONF" ]]; then
    grep -nE '^(net:|  bindIp:|  port:|replication:|  replSetName:|security:|  authorization:|  keyFile:|storage:|  dbPath:|systemLog:|  path:)' "$MONGOD_CONF" || true
  else
    warn "Cannot read $MONGOD_CONF. Run with sudo or inspect manually."
  fi

  section "Firewall status"
  if have ufw; then
    sudo ufw status verbose || ufw status verbose || true
  else
    warn "ufw not installed"
  fi

  check_reachability

  section "MongoDB ping"
  mongo_eval 'db.adminCommand({ ping: 1 })' || warn "MongoDB ping failed"

  section "Replica set status"
  mongo_eval 'try { printjson(rs.status()) } catch (e) { print(e.codeName || e.name || "Error"); print(e.message) }' || true

  section "Replica set config"
  mongo_eval 'try { printjson(rs.conf()) } catch (e) { print(e.codeName || e.name || "Error"); print(e.message) }' || true

  section "Recent mongod replica-set logs"
  if have journalctl; then
    journalctl -u mongod --since "$SINCE" --no-pager \
      | grep -Ei 'repl|replica|election|heartbeat|keyfile|auth|notyetinitialized|quorum|connection|error|fail|rollback|sync' \
      || true
  else
    warn "journalctl not available"
  fi
}

backup_config() {
  require_mongosh
  mkdir -p "$BACKUP_DIR"
  local stamp
  stamp=$(date '+%Y%m%d-%H%M%S')
  local conf_file="$BACKUP_DIR/rs-conf-$stamp.json"
  local status_file="$BACKUP_DIR/rs-status-$stamp.json"

  mongo_eval 'try { printjson(rs.conf()) } catch (e) { printjson({error: e.message, codeName: e.codeName || e.name}) }' >"$conf_file"
  mongo_eval 'try { printjson(rs.status()) } catch (e) { printjson({error: e.message, codeName: e.codeName || e.name}) }' >"$status_file"

  printf 'Saved replica-set config to %s\n' "$conf_file"
  printf 'Saved replica-set status to %s\n' "$status_file"
}

print_planned_config() {
  validate_members
  RS_NAME="$RS_NAME" MEMBERS="$MEMBERS" mongosh --quiet --nodb <<'EOF'
const members = process.env.MEMBERS.split(",").map((host, idx) => ({
  _id: idx,
  host: host.trim(),
  priority: idx === 0 ? 2 : 1
}));
printjson({
  _id: process.env.RS_NAME,
  members
});
EOF
}

initiate_rs() {
  require_mongosh
  validate_members
  section "Planned rs.initiate config"
  print_planned_config

  if [[ "$APPLY" != true ]]; then
    warn "Dry run only. Re-run with --apply to initiate the replica set."
    return 0
  fi

  backup_config || true
  section "Applying rs.initiate"
  RS_NAME="$RS_NAME" MEMBERS="$MEMBERS" mongo_script <<'EOF'
const setName = process.env.RS_NAME;
const members = process.env.MEMBERS.split(",").map((host, idx) => ({
  _id: idx,
  host: host.trim(),
  priority: idx === 0 ? 2 : 1
}));
try {
  const current = rs.status();
  print(`Replica set already initialized: ${current.set}`);
  quit(0);
} catch (e) {
  if (!["NotYetInitialized", "NoReplicationEnabled"].includes(e.codeName)) {
    print(`Refusing to initiate because rs.status() failed with unexpected error: ${e.codeName || e.name}: ${e.message}`);
    quit(2);
  }
}
printjson(rs.initiate({ _id: setName, members }));
EOF
}

reconfig_rs() {
  require_mongosh
  validate_members
  section "Current config backup"
  backup_config

  section "Planned member replacement"
  print_planned_config

  if [[ "$APPLY" != true ]]; then
    warn "Dry run only. Re-run with --apply to replace the member list."
    return 0
  fi

  if [[ "$FORCE" == true && "$I_UNDERSTAND" != true ]]; then
    die "Force reconfig requires --i-understand-force-reconfig-risk"
  fi

  if [[ "$FORCE" == true ]]; then
    warn "FORCE RECONFIG REQUESTED. Use only when no primary is reachable and you understand rollback/election risk."
  fi

  section "Applying rs.reconfig"
  RS_NAME="$RS_NAME" MEMBERS="$MEMBERS" FORCE="$FORCE" mongo_script <<'EOF'
const setName = process.env.RS_NAME;
const force = process.env.FORCE === "true";
const members = process.env.MEMBERS.split(",").map((host, idx) => ({
  _id: idx,
  host: host.trim(),
  priority: idx === 0 ? 2 : 1
}));
let cfg = rs.conf();
if (cfg._id !== setName) {
  print(`Replica set name mismatch. Current=${cfg._id}; requested=${setName}. Fix mongod.conf first on every node.`);
  quit(2);
}
cfg.members = members;
cfg.version = Number(cfg.version || 1) + 1;
printjson(rs.reconfig(cfg, { force }));
EOF
}

parse_args() {
  COMMAND=${1:-diagnose}
  shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --apply)
        APPLY=true
        ;;
      --force)
        FORCE=true
        ;;
      --i-understand-force-reconfig-risk)
        I_UNDERSTAND=true
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"
  case "$COMMAND" in
    diagnose)
      diagnose
      ;;
    backup-config)
      backup_config
      ;;
    initiate)
      initiate_rs
      ;;
    reconfig)
      reconfig_rs
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      usage
      die "Unknown command: $COMMAND"
      ;;
  esac
}

main "$@"

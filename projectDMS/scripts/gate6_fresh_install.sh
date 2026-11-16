#!/usr/bin/env bash
# Gate 6 bullet 2 - fresh install against a real, EMPTY MongoDB.
#
# What it does, in order, and stops at the first thing that is not as expected:
#
#   1. builds (or takes) the backend image for the commit that is checked out
#   2. starts a disposable mongo:8.0 as a one-member replica set on a private
#      docker network - no published port, no host bind mount, a new anonymous
#      data volume - and proves it holds nothing before anything is run
#   3. proves, from inside the backend container, that the application's own
#      settings resolve to that disposable server and to no other
#   4. migrate_database --list, a --fail-on-warning dry run, then
#      --apply --fail-on-warning, all inside the backend container
#   5. checks the skill and claim migrations (20261114_0001 to _0003,
#      20261115_0001, 20261116_0001 and 20261117_0001) by reading the server: collections, indexes with their uniqueness and
#      partial filters, the three published letter skills at 1.0.0, the four
#      claim and arbitration packs stored as unpublished drafts, and the
#      unique indexes refusing a real duplicate
#   6. applies again and proves nothing moved (idempotency), then --list again
#   7. prints an evidence block to paste into
#      docs/GATE_6_FRESH_INSTALL_EVIDENCE.md
#
# What it never does:
#   - connect to any database other than the one it started itself. It does not
#     read DATABASE_URL, an env file, or a compose project; the backend container
#     gets its connection settings from this script and from nowhere else.
#   - write the evidence document, the release gate document, or
#     MIGRATIONS_PROVEN_ON_A_FRESH_DATABASE. A person does that, in one commit,
#     with this script's output in front of them.
#
# Needs: bash, docker, git. Nothing else on the host - JSON is parsed inside
# the backend container and the server is read with the mongosh that ships in
# the mongo image.
#
#   cd projectDMS && bash scripts/gate6_fresh_install.sh
#
# Options (environment):
#   GATE6_BACKEND_IMAGE        use this image instead of building one. Its
#                              org.opencontainers.image.revision label must
#                              equal the checked-out commit.
#   GATE6_EXPECTED_MIGRATIONS  catalogue size this run must find (default 28)
#   GATE6_MONGO_IMAGE          default mongo:8.0, the image CI uses
#   GATE6_OUT_DIR              where step logs go (default: a new temp dir)
#   GATE6_KEEP=1               leave the containers and network up afterwards
#   GATE6_ALLOW_DIRTY=1        run with uncommitted changes under backend/
#                              (the evidence block then says so)

set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
EXPECTED=${GATE6_EXPECTED_MIGRATIONS:-28}
MONGO_IMAGE=${GATE6_MONGO_IMAGE:-mongo:8.0}
# Lower case throughout: the name is also the server's hostname, and URL parsers
# fold hostnames to lower case.
RUN_ID="gate6-$(date -u +%Y%m%dt%H%M%Sz)-$$"
NET="${RUN_ID}-net"
MONGO="${RUN_ID}-mongo"
RS="gate6rs"
DB="gate6_fresh_install"
MONGO_URL="mongodb://${MONGO}:27017/${DB}?replicaSet=${RS}"
OUT_DIR=${GATE6_OUT_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/${RUN_ID}.XXXXXX")}
BENCH_VERSIONS="20261114_0001 20261114_0002 20261114_0003"
STEP="startup"

say() { printf '\n== %s\n' "$*"; }
ok() { printf 'ok    %s\n' "$*"; }
die() {
  printf '\nGATE 6 FRESH INSTALL: FAILED during "%s"\n  %s\n' "$STEP" "$*" >&2
  printf '  Step logs: %s\n  No evidence block was produced. Do not record this run.\n' "$OUT_DIR" >&2
  exit 1
}
trap 'die "command failed at line $LINENO: $BASH_COMMAND"' ERR

cleanup() {
  if [[ "${GATE6_KEEP:-0}" == "1" ]]; then
    printf '\nGATE6_KEEP=1: left running - container %s, network %s\n' "$MONGO" "$NET" >&2
    return
  fi
  docker rm -f -v "$MONGO" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

command -v docker >/dev/null || die "docker is not installed"
command -v git >/dev/null || die "git is not installed"
docker info >/dev/null 2>&1 || die "the docker daemon is not reachable"
mkdir -p "$OUT_DIR"

# --------------------------------------------------------------------------- #
# 0. What is being measured
# --------------------------------------------------------------------------- #
STEP="identify the commit"
say "$STEP"
GIT_SHA=$(git -C "$ROOT_DIR" rev-parse HEAD)
GIT_BRANCH=$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD)
DIRTY=$(git -C "$ROOT_DIR" status --porcelain -- backend | wc -l | tr -d ' ')
if [[ "$DIRTY" != "0" && "${GATE6_ALLOW_DIRTY:-0}" != "1" ]]; then
  die "backend/ has $DIRTY uncommitted change(s); evidence must name a commit. Commit them, or set GATE6_ALLOW_DIRTY=1."
fi
ok "commit $GIT_SHA on $GIT_BRANCH, uncommitted backend changes: $DIRTY"

STEP="backend image"
say "$STEP"
if [[ -n "${GATE6_BACKEND_IMAGE:-}" ]]; then
  IMAGE=$GATE6_BACKEND_IMAGE
  IMAGE_SOURCE="supplied (GATE6_BACKEND_IMAGE)"
  LABEL=$(docker image inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE")
  [[ "$LABEL" == "$GIT_SHA" ]] || die "image $IMAGE is labelled revision '$LABEL', the checkout is $GIT_SHA"
else
  IMAGE="contraclaim-gate6-backend:${GIT_SHA:0:12}"
  IMAGE_SOURCE="built by this run from backend/Dockerfile"
  BUILD_CMD="docker build -t $IMAGE --build-arg RELEASE_SHA=$GIT_SHA backend"
  (cd "$ROOT_DIR" && $BUILD_CMD) >"$OUT_DIR/00-build.log" 2>&1 || die "image build failed, see $OUT_DIR/00-build.log"
fi
IMAGE_ID=$(docker image inspect -f '{{ .Id }}' "$IMAGE")
ok "$IMAGE ($IMAGE_ID), $IMAGE_SOURCE"

# Everything the backend container knows about its database comes from here.
BACKEND_ENV=(
  -e "DATABASE_URL=$MONGO_URL"
  -e "MONGODB_DATABASE=$DB"
  -e "MONGODB_REPLICA_SET=$RS"
  -e "ENVIRONMENT=development"
  -e "AWS_REGION=ap-south-1"
  # The settings model refuses to load without these. They are placeholders,
  # the same shape CI uses; no migration reads any of them and nothing here
  # talks to S3, SMTP or a language model.
  -e "SECRET_KEY=gate6-placeholder-secret-key-32-characters"
  -e "AWS_ACCESS_KEY_ID=gate6-placeholder"
  -e "AWS_SECRET_ACCESS_KEY=gate6-placeholder"
  -e "AWS_BUCKET_NAME=gate6-placeholder"
  -e "OPENAI_API_KEY=gate6-placeholder"
  -e "SMTP_USERNAME=gate6@example.com"
  -e "SMTP_PASSWORD=gate6-placeholder"
  -e "SMTP_FROM_EMAIL=gate6@example.com"
  -e "LANGGRAPH_API_TOKEN=gate6-placeholder"
  -e "CONTRACT_QUEUE_ENABLED=false"
  -e "FALKORDB_ENABLED=false"
)
MIGRATE="python -m rbac_backend.scripts.migrate_database"

# backend <log name> <command...>: run in a fresh backend container on the
# private network; stdout and stderr are kept; the exit code is returned.
backend() {
  local name=$1
  shift
  docker run --rm --network "$NET" "${BACKEND_ENV[@]}" "$IMAGE" "$@" \
    >"$OUT_DIR/$name.stdout" 2>"$OUT_DIR/$name.stderr"
}
# py <stdin file> <python source>: run python in the backend image with no
# network at all, for parsing what a previous step printed.
py() {
  local input=$1
  shift
  docker run --rm -i --network none "$IMAGE" python -c "$1" <"$input"
}
# mongo_js <log name> <javascript file>: run a script with the server's own shell.
mongo_js() {
  local name=$1 file=$2
  docker cp "$file" "$MONGO:/tmp/$name.js" >/dev/null
  docker exec "$MONGO" mongosh --quiet --norc "mongodb://localhost:27017/${DB}?replicaSet=${RS}" \
    --file "/tmp/$name.js" >"$OUT_DIR/$name.stdout" 2>"$OUT_DIR/$name.stderr"
}

# --------------------------------------------------------------------------- #
# 1. A disposable, empty MongoDB
# --------------------------------------------------------------------------- #
STEP="start a disposable MongoDB"
say "$STEP"
docker network create "$NET" >/dev/null
# No -p and no -v on purpose: nothing outside this network can reach the
# server, and its data directory is a new anonymous volume removed at exit.
docker run -d --name "$MONGO" --hostname "$MONGO" --network "$NET" "$MONGO_IMAGE" \
  --replSet "$RS" --bind_ip_all >/dev/null
for _ in $(seq 1 60); do
  docker exec "$MONGO" mongosh --quiet --norc --eval 'db.adminCommand("ping").ok' >/dev/null 2>&1 && break
  if [[ "$(docker inspect -f '{{.State.Running}}' "$MONGO")" != "true" ]]; then
    docker logs "$MONGO" >"$OUT_DIR/01-mongo.log" 2>&1 || true
    die "the MongoDB container exited (mongo:8.0 refuses Linux kernels 6.19 to 7.0.13, SERVER-121912). See $OUT_DIR/01-mongo.log"
  fi
  sleep 1
done
docker exec "$MONGO" mongosh --quiet --norc --eval \
  "rs.initiate({_id: '$RS', members: [{_id: 0, host: '$MONGO:27017'}]}).ok" >"$OUT_DIR/01-rs-initiate.stdout" 2>&1
for _ in $(seq 1 60); do
  [[ "$(docker exec "$MONGO" mongosh --quiet --norc --eval 'db.hello().isWritablePrimary' 2>/dev/null)" == "true" ]] && break
  sleep 1
done
[[ "$(docker exec "$MONGO" mongosh --quiet --norc --eval 'db.hello().isWritablePrimary')" == "true" ]] \
  || die "the replica set never elected a primary"

MONGO_VERSION=$(docker exec "$MONGO" mongosh --quiet --norc --eval 'db.version()')
MONGO_IMAGE_ID=$(docker image inspect -f '{{ .Id }}' "$MONGO_IMAGE")
RS_READ_BACK=$(docker exec "$MONGO" mongosh --quiet --norc --eval 'rs.status().set')
BIND_MOUNTS=$(docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}} {{end}}{{end}}' "$MONGO")
PUBLISHED=$(docker inspect -f '{{range $p, $b := .NetworkSettings.Ports}}{{if $b}}{{$p}} {{end}}{{end}}' "$MONGO")
[[ "$RS_READ_BACK" == "$RS" ]] || die "rs.status().set read back '$RS_READ_BACK', expected '$RS'"
[[ -z "$BIND_MOUNTS" ]] || die "the MongoDB container has host bind mounts: $BIND_MOUNTS"
[[ -z "$PUBLISHED" ]] || die "the MongoDB container publishes ports: $PUBLISHED"
ok "MongoDB $MONGO_VERSION, replica set '$RS_READ_BACK' read back, no bind mounts, no published ports"

STEP="prove the server is empty before anything runs"
say "$STEP"
cat >"$OUT_DIR/02-empty.js" <<'JS'
const system = new Set(["admin", "config", "local"]);
const names = db.adminCommand({ listDatabases: 1 }).databases.map((d) => d.name).sort();
const extra = names.filter((n) => !system.has(n));
const collections = db.getCollectionNames();
print("databases=" + JSON.stringify(names));
print("user_databases=" + extra.length);
print("target_database=" + db.getName());
print("target_collections=" + collections.length);
print("ledger_rows=" + db.getCollection("schema_migrations").countDocuments({}));
if (extra.length !== 0 || collections.length !== 0) { print("NOT EMPTY"); quit(1); }
print("EMPTY=YES");
JS
mongo_js 02-empty "$OUT_DIR/02-empty.js" || die "the server is not empty: $(cat "$OUT_DIR/02-empty.stdout")"
EMPTY_PROOF=$(cat "$OUT_DIR/02-empty.stdout")
grep -q '^EMPTY=YES$' "$OUT_DIR/02-empty.stdout" || die "emptiness was not proved"
ok "$(tr '\n' ' ' <"$OUT_DIR/02-empty.stdout")"

STEP="prove the backend resolves to the disposable server"
say "$STEP"
backend 03-target python -c '
import asyncio
from urllib.parse import urlparse
from rbac_backend.core import database
from rbac_backend.core.config import settings

async def main():
    db = await database.get_database()
    hello = await db.client.admin.command("hello")
    print("configured_host=" + (urlparse(settings.DATABASE_URL).hostname or ""))
    print("database=" + db.name)
    print("server_me=" + str(hello.get("me")))
    print("server_set=" + str(hello.get("setName")))
    print("environment=" + str(settings.ENVIRONMENT))
    await database.disconnect()

asyncio.run(main())
' || die "the backend container could not reach the disposable server: $(tail -5 "$OUT_DIR/03-target.stderr")"
grep -qx "configured_host=$MONGO" "$OUT_DIR/03-target.stdout" || die "backend is configured for another host: $(cat "$OUT_DIR/03-target.stdout")"
grep -qx "database=$DB" "$OUT_DIR/03-target.stdout" || die "backend resolved another database: $(cat "$OUT_DIR/03-target.stdout")"
grep -qx "server_me=$MONGO:27017" "$OUT_DIR/03-target.stdout" || die "the server that answered is not the disposable one: $(cat "$OUT_DIR/03-target.stdout")"
grep -qx "server_set=$RS" "$OUT_DIR/03-target.stdout" || die "the server that answered is in another replica set"
TARGET_PROOF=$(grep -E '^(configured_host|database|server_me|server_set)=' "$OUT_DIR/03-target.stdout")
ok "$(echo "$TARGET_PROOF" | tr '\n' ' ')"

# --------------------------------------------------------------------------- #
# 2. The install path
# --------------------------------------------------------------------------- #
SUMMARISE_LIST='
import json, sys, collections
rows = json.load(sys.stdin)
counts = collections.Counter(r["status"] for r in rows)
print("total=%d applied=%d pending=%d applied_not_in_catalogue=%d" % (
    len(rows), counts["applied"], counts["pending"], counts["applied_not_in_catalogue"]))
print("versions=" + " ".join(r["version"] for r in rows))
'
SUMMARISE_RUN='
import json, sys, collections
rows = json.load(sys.stdin)
counts = collections.Counter(r["status"] for r in rows)
warnings = sum(len(r.get("warnings") or []) for r in rows)
notices = sum(len(r.get("notices") or []) for r in rows)
print("total=%d applied=%d dry_run=%d skipped=%d warnings=%d notices=%d" % (
    len(rows), counts["applied"], counts["dry_run"], counts["skipped"], warnings, notices))
'

STEP="migrate_database --list (before)"
say "$STEP"
LIST_BEFORE_RC=0
backend 04-list-before $MIGRATE --list || LIST_BEFORE_RC=$?
[[ $LIST_BEFORE_RC -eq 0 ]] || die "--list exited $LIST_BEFORE_RC: $(tail -5 "$OUT_DIR/04-list-before.stderr")"
LIST_BEFORE=$(py "$OUT_DIR/04-list-before.stdout" "$SUMMARISE_LIST")
LIST_BEFORE_COUNTS=$(echo "$LIST_BEFORE" | head -1)
[[ "$LIST_BEFORE_COUNTS" == "total=$EXPECTED applied=0 pending=$EXPECTED applied_not_in_catalogue=0" ]] \
  || die "expected $EXPECTED pending migrations on an empty database, got: $LIST_BEFORE_COUNTS"
for version in $BENCH_VERSIONS; do
  echo "$LIST_BEFORE" | grep -q "$version" || die "the catalogue in this image has no migration $version"
done
ok "exit $LIST_BEFORE_RC, $LIST_BEFORE_COUNTS"

STEP="dry run with --fail-on-warning"
say "$STEP"
DRY_RC=0
backend 05-dry-run $MIGRATE --fail-on-warning || DRY_RC=$?
[[ $DRY_RC -eq 0 ]] || die "the dry run exited $DRY_RC: $(tail -8 "$OUT_DIR/05-dry-run.stderr")"
DRY=$(py "$OUT_DIR/05-dry-run.stdout" "$SUMMARISE_RUN")
[[ "$DRY" == total=$EXPECTED\ applied=0\ dry_run=$EXPECTED\ skipped=0\ warnings=0\ * ]] || die "unexpected dry run: $DRY"
mongo_js 05-empty-after-dry-run "$OUT_DIR/02-empty.js" || die "the dry run wrote to the database: $(cat "$OUT_DIR/05-empty-after-dry-run.stdout")"
ok "exit $DRY_RC, $DRY, database still empty afterwards"

STEP="apply with --fail-on-warning"
say "$STEP"
APPLY_RC=0
backend 06-apply $MIGRATE --apply --fail-on-warning || APPLY_RC=$?
[[ $APPLY_RC -eq 0 ]] || die "the apply exited $APPLY_RC: $(tail -8 "$OUT_DIR/06-apply.stderr")"
APPLY=$(py "$OUT_DIR/06-apply.stdout" "$SUMMARISE_RUN")
[[ "$APPLY" == total=$EXPECTED\ applied=$EXPECTED\ dry_run=0\ skipped=0\ warnings=0\ * ]] || die "unexpected apply: $APPLY"
ok "exit $APPLY_RC, $APPLY"

# --------------------------------------------------------------------------- #
# 3. The skill migrations (Letter Bench, and the draft packs), read from the server
# --------------------------------------------------------------------------- #
STEP="verify the skill migrations"
say "$STEP"
# The expectations below are written out here, not imported from the migration
# modules: a check that reads its expectation from the code under test passes
# whatever that code does.
cat >"$OUT_DIR/07-bench.js" <<'JS'
let failed = 0;
function check(name, pass, detail) {
  print((pass ? "PASS  " : "FAIL  ") + name + (detail ? "  -  " + detail : ""));
  if (!pass) failed += 1;
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const expectedCollections = ["skills", "skill_versions", "skill_bindings", "skill_approvals", "letter_position_boards", "letter_skill_steps", "skill_steps", "claim_notice_requirements", "claim_head_links", "claim_determinations", "claim_privileged", "claim_submissions"];
const present = new Set(db.getCollectionNames());
for (const name of expectedCollections) check("collection " + name + " exists", present.has(name));

const expectedIndexes = [
  ["skills", "skill_owner_key_unique", { owner_scope: 1, skill_key: 1 }, true, null],
  ["skill_versions", "skill_version_unique", { skill_key: 1, version: 1 }, true, null],
  ["skill_versions", "skill_version_status", { skill_key: 1, status: 1 }, false, null],
  ["skill_bindings", "skill_binding_scope_unique", { organization_id: 1, project_id: 1, skill_key: 1 }, true, null],
  ["skill_approvals", "skill_approvals_by_version", { skill_key: 1, version: 1, decided_at: 1 }, false, null],
  ["letter_position_boards", "bench_board_letter_unique", { letter_id: 1 }, true, null],
  ["letter_position_boards", "bench_board_scope_time", { organization_id: 1, project_id: 1, updated_at: -1 }, false, null],
  ["letter_position_boards", "bench_board_document_unique", { organization_id: 1, incoming_document_id: 1 }, true,
    { incoming_document_id: { $type: "string" } }],
  ["letter_position_boards", "bench_board_trigger_unique", { organization_id: 1, "trigger.type": 1, "trigger.id": 1 }, true,
    { "trigger.id": { $type: "string" } }],
  ["letter_skill_steps", "bench_step_letter_time", { letter_id: 1, created_at: 1 }, false, null],
  ["letter_skill_steps", "bench_step_idempotency_unique", { letter_id: 1, skill_key: 1, skill_hash: 1, input_hash: 1 }, true, null],
  ["letter_skill_steps", "bench_step_id_unique", { step_id: 1 }, true, null],
  // 20261116_0001: Claim Bench and Notice Clock.
  ["skill_steps", "skill_step_idempotency_unique", { subject_type: 1, subject_id: 1, skill_key: 1, skill_hash: 1, input_hash: 1 }, true, null],
  ["skill_steps", "skill_step_id_unique", { step_id: 1 }, true, null],
  ["skill_steps", "skill_step_parent_time", { parent_id: 1, created_at: 1 }, false, null],
  ["claim_notice_requirements", "notice_requirement_current_unique", { organization_id: 1, project_id: 1, claim_type: 1 }, true,
    { status: "current" }],
  ["claim_notice_requirements", "notice_requirement_history", { organization_id: 1, project_id: 1, claim_type: 1, created_at: -1 }, false, null],
  ["claims", "claim_origin_unique", { organization_id: 1, "origin.type": 1, "origin.id": 1 }, true,
    { "origin.id": { $type: "string" } }],
  // 20261117_0001: links from a head of claim to hindrances and key-date EOT records.
  ["claim_head_links", "claim_head_link_active_unique", { claim_id: 1, head_id: 1, target_type: 1, target_id: 1 }, true,
    { status: "active" }],
  ["claim_head_links", "claim_head_link_claim_time", { claim_id: 1, status: 1, created_at: 1 }, false, null],
  ["claim_head_links", "claim_head_link_target", { organization_id: 1, target_type: 1, target_id: 1, status: 1 }, false, null],
  // 20261118_0001: determinations, the privileged claim store, submissions.
  ["claim_determinations", "claim_determination_revision_unique", { claim_id: 1, origin: 1, revision: 1 }, true, null],
  ["claim_determinations", "claim_determination_scope_status", { organization_id: 1, project_id: 1, status: 1 }, false, null],
  ["claim_privileged", "claim_privileged_claim_kind_time", { claim_id: 1, kind: 1, created_at: -1 }, false, null],
  ["claim_privileged", "claim_privileged_one_strategy_note", { claim_id: 1 }, true, { kind: "strategy_note" }],
  ["claim_submissions", "claim_submission_claim_unique", { claim_id: 1 }, true, null],
  // 20261119_0001: the link between claims and arbitration cases.
  ["claims", "claim_arbitration_case", { organization_id: 1, arbitration_case_id: 1 }, false,
    { arbitration_case_id: { $type: "string" } }],
  ["arbitration_cases", "arbitration_case_claim_ids", { organization_id: 1, claim_ids: 1 }, false, null],
  // 20261120_0001 (M-05): one matrix row per case and escalation origin, in every matrix collection.
  ...["arbitration_chronology_matrix", "arbitration_claim_matrix", "arbitration_clause_matrix", "arbitration_counterclaim_matrix", "arbitration_defence_matrix", "arbitration_document_index", "arbitration_expert_alignment", "arbitration_issue_matrix", "arbitration_jurisdiction_matrix", "arbitration_notice_compliance", "arbitration_quantum_annexures", "arbitration_rejoinder_matrix"].map((name) =>
    [name, "matrix_case_origin_unique", { case_id: 1, origin_key: 1 }, true, { origin_key: { $type: "string" } }]),
];
for (const [collection, name, key, unique, partial] of expectedIndexes) {
  const found = db.getCollection(collection).getIndexes().find((i) => i.name === name);
  if (!found) { check("index " + collection + "." + name, false, "missing"); continue; }
  const isUnique = found.unique === true;
  const filter = found.partialFilterExpression || null;
  check(
    "index " + collection + "." + name,
    same(found.key, key) && isUnique === unique && same(filter, partial),
    "key=" + JSON.stringify(found.key) + " unique=" + isUnique + " partialFilterExpression=" + JSON.stringify(filter)
  );
}
const partials = expectedIndexes.filter((e) => e[4] !== null).length;
// 7 before 20261120_0001, which adds one per matrix collection (12).
check("partial indexes expected", partials === 19, partials + " declared");

const expectedSkills = ["indian-infra-contract-letters", "indian-infra-letter-analysis", "indian-infra-variations"];
// Stored by 20261115_0001 as DRAFTS: unreviewed method text that must not be
// usable for drafting until its technical and legal approvals exist.
const expectedDrafts = ["indian-infra-arbitration-hearing", "indian-infra-arbitration-pleadings", "indian-infra-claims", "indian-infra-eot-claims"];
const versions = db.skill_versions.find({}).sort({ skill_key: 1 }).toArray();
check("skill_versions holds exactly the three published skills and the four draft packs",
  same(versions.map((v) => v.skill_key), expectedSkills.concat(expectedDrafts).sort()), versions.length + " rows");
for (const v of versions) {
  const hashed = /^[0-9a-f]{64}$/.test(v.content_hash || "");
  if (expectedDrafts.includes(v.skill_key)) {
    const reviews = ((v.manifest || {}).required_approvals || []).map((r) => r.kind + ":" + r.reviewer_role).sort();
    check("skill " + v.skill_key + " stored at 1.0.0 as an unpublished draft that names a legal approver",
      v.version === "1.0.0" && v.status === "draft" && v.owner_scope === "system" && hashed &&
        !v.published_at && !v.published_by && !v.eval_report && reviews.includes("legal:senior_legal_approver") &&
        reviews.some((r) => r.startsWith("technical:")),
      "status=" + v.status + " published_at=" + (v.published_at || null) + " required_approvals=" + JSON.stringify(reviews));
  } else {
    check("skill " + v.skill_key + " seeded at 1.0.0, published, system-owned",
      v.version === "1.0.0" && v.status === "published" && v.owner_scope === "system" && hashed,
      "version=" + v.version + " status=" + v.status + " owner_scope=" + v.owner_scope);
  }
  print("SEED  " + v.skill_key + " " + v.version + " " + v.content_hash);
}
const skills = db.skills.find({}).sort({ skill_key: 1 }).toArray();
check("skills holds exactly the three published skills (a draft gets no latest-published pointer)",
  same(skills.map((s) => s.skill_key), expectedSkills), skills.length + " rows");
for (const s of skills) {
  check("skill " + s.skill_key + " active, latest published 1.0.0",
    s.status === "active" && s.latest_published_version === "1.0.0" && s.owner_scope === "system");
}
check("no skill approval exists on a fresh install", db.skill_approvals.countDocuments({}) === 0,
  db.skill_approvals.countDocuments({}) + " rows");

const ledger = db.schema_migrations.find({}).toArray().map((r) => r.version).sort();
for (const version of ["20261114_0001", "20261114_0002", "20261114_0003", "20261115_0001", "20261116_0001", "20261117_0001"]) {
  check("ledger records " + version, ledger.includes(version));
}
print("ledger_rows=" + ledger.length);
if (failed) { print("BENCH CHECKS FAILED=" + failed); quit(1); }
print("BENCH CHECKS=PASS");
JS
mongo_js 07-bench "$OUT_DIR/07-bench.js" || die "$(grep -E '^FAIL' "$OUT_DIR/07-bench.stdout" || tail -5 "$OUT_DIR/07-bench.stderr")"
grep -q '^BENCH CHECKS=PASS$' "$OUT_DIR/07-bench.stdout" || die "the Bench checks did not complete"
grep -qx "ledger_rows=$EXPECTED" "$OUT_DIR/07-bench.stdout" || die "the ledger does not hold $EXPECTED rows: $(grep ledger_rows "$OUT_DIR/07-bench.stdout")"
BENCH_PASSES=$(grep -c '^PASS' "$OUT_DIR/07-bench.stdout")
ok "$BENCH_PASSES checks passed, 0 failed"

STEP="seeded skill hashes match the files in the image"
say "$STEP"
backend 08-bundled-hashes python -c '
from rbac_backend.services.skills.system_skills import system_skill_versions
for v in sorted(system_skill_versions(), key=lambda v: v.skill_key):
    print("SEED  %s %s %s" % (v.skill_key, v.version, v.content_hash))
' || die "could not read the bundled skills from the image: $(tail -5 "$OUT_DIR/08-bundled-hashes.stderr")"
SEEDED=$(grep '^SEED  ' "$OUT_DIR/07-bench.stdout")
BUNDLED=$(grep '^SEED  ' "$OUT_DIR/08-bundled-hashes.stdout")
[[ -n "$SEEDED" && "$SEEDED" == "$BUNDLED" ]] || die "stored skills differ from the bundled files: stored [$SEEDED] bundled [$BUNDLED]"
ok "7 of 7 stored content hashes equal the bundled files"

STEP="unique indexes refuse a real duplicate"
say "$STEP"
# Observed, not asserted: an index can be declared unique and partial and still
# not mean what was intended. Probe rows are removed again before the re-apply.
cat >"$OUT_DIR/09-probe.js" <<'JS'
let failed = 0;
function check(name, pass, detail) {
  print((pass ? "PASS  " : "FAIL  ") + name + (detail ? "  -  " + detail : ""));
  if (!pass) failed += 1;
}
function attempt(collection, doc) {
  try { db.getCollection(collection).insertOne(doc); return "inserted"; }
  catch (e) { return e.code === 11000 ? "E11000 " + ((e.message.match(/index: (\S+)/) || [])[1] || "") : "error " + e.code; }
}
const org = "gate6-probe-org";
const boards = "letter_position_boards";
check("two boards without a document are both accepted",
  attempt(boards, { letter_id: "p1", organization_id: org }) === "inserted" &&
  attempt(boards, { letter_id: "p2", organization_id: org }) === "inserted");
check("two boards with a null document are both accepted",
  attempt(boards, { letter_id: "p3", organization_id: org, incoming_document_id: null }) === "inserted" &&
  attempt(boards, { letter_id: "p4", organization_id: org, incoming_document_id: null }) === "inserted");
check("first board for a document is accepted",
  attempt(boards, { letter_id: "p5", organization_id: org, incoming_document_id: "doc-1" }) === "inserted");
let r = attempt(boards, { letter_id: "p6", organization_id: org, incoming_document_id: "doc-1" });
check("second board for the same document is refused", r === "E11000 bench_board_document_unique", r);
check("first board for a trigger record is accepted",
  attempt(boards, { letter_id: "p7", organization_id: org, trigger: { type: "variation", id: "v-1" } }) === "inserted");
r = attempt(boards, { letter_id: "p8", organization_id: org, trigger: { type: "variation", id: "v-1" } });
check("second board for the same trigger record is refused", r === "E11000 bench_board_trigger_unique", r);
r = attempt(boards, { letter_id: "p1", organization_id: org });
check("second board for the same letter is refused", r === "E11000 bench_board_letter_unique", r);
r = attempt("skill_versions", { skill_key: "indian-infra-variations", version: "1.0.0", owner_scope: "gate6-probe" });
check("a second 1.0.0 of a seeded skill is refused", r === "E11000 skill_version_unique", r);
r = attempt("skill_versions", { skill_key: "indian-infra-claims", version: "1.0.0", owner_scope: "gate6-probe", status: "published" });
check("a second 1.0.0 of a draft pack is refused", r === "E11000 skill_version_unique", r);

// 20261116_0001. One notice period in force per project and claim type; history is free.
const notice = "claim_notice_requirements";
const scope = { organization_id: org, project_id: "p1", claim_type: "eot" };
check("first current notice period is accepted", attempt(notice, { _id: "n1", ...scope, status: "current" }) === "inserted");
r = attempt(notice, { _id: "n2", ...scope, status: "current" });
check("a second current notice period for the same project and claim type is refused", r === "E11000 notice_requirement_current_unique", r);
check("superseded notice periods are kept as history",
  attempt(notice, { _id: "n3", ...scope, status: "superseded" }) === "inserted" &&
  attempt(notice, { _id: "n4", ...scope, status: "superseded" }) === "inserted");
check("another claim type on the project is accepted",
  attempt(notice, { _id: "n5", ...scope, claim_type: "variation", status: "current" }) === "inserted");
// One claim per record it was raised from; claims with no origin are unconstrained.
check("two claims with no origin are both accepted",
  attempt("claims", { _id: "gate6-c1", organization_id: org, origin: null }) === "inserted" &&
  attempt("claims", { _id: "gate6-c2", organization_id: org }) === "inserted");
check("first claim raised from a hindrance is accepted",
  attempt("claims", { _id: "gate6-c3", organization_id: org, origin: { type: "hindrance", id: "h-1" } }) === "inserted");
r = attempt("claims", { _id: "gate6-c4", organization_id: org, origin: { type: "hindrance", id: "h-1" } });
check("a second claim raised from the same hindrance is refused", r === "E11000 claim_origin_unique", r);
check("a claim raised from a letter with the same id is a different origin",
  attempt("claims", { _id: "gate6-c5", organization_id: org, origin: { type: "letter_bench", id: "h-1" } }) === "inserted");
// A skill step replayed with the same inputs cannot be stored twice.
const stepKey = { subject_type: "claim_head", subject_id: "gate6:H1", skill_key: "indian-infra-claims", skill_hash: "x", input_hash: "y" };
check("first skill step is accepted", attempt("skill_steps", { step_id: "gate6-s1", ...stepKey }) === "inserted");
r = attempt("skill_steps", { step_id: "gate6-s2", ...stepKey });
check("the same skill step replayed is refused", r === "E11000 skill_step_idempotency_unique", r);

// 20261117_0001. One active link per record and head; removed links are history;
// the same hindrance may serve another head.
const links = "claim_head_links";
const link = { organization_id: org, claim_id: "gate6-claim", head_id: "H1", target_type: "hindrance", target_id: "h-1" };
check("first link from a head to a hindrance is accepted", attempt(links, { _id: "k1", ...link, status: "active" }) === "inserted");
r = attempt(links, { _id: "k2", ...link, status: "active" });
check("a second active link from the same head to the same hindrance is refused", r === "E11000 claim_head_link_active_unique", r);
check("removed links to it are kept as history",
  attempt(links, { _id: "k3", ...link, status: "removed" }) === "inserted" &&
  attempt(links, { _id: "k4", ...link, status: "removed" }) === "inserted");
check("the same hindrance can be linked to another head",
  attempt(links, { _id: "k5", ...link, head_id: "H2", status: "active" }) === "inserted");

// 20261118_0001. A determination revision is unique per claim and origin; a claim
// has one strategy note and any number of simulations; one submission per claim.
const dets = "claim_determinations", priv = "claim_privileged", subs = "claim_submissions";
const det = { organization_id: org, claim_id: "gate6-claim", origin: "authored", revision: 1 };
check("first determination revision is accepted", attempt(dets, { _id: "d1", ...det }) === "inserted");
r = attempt(dets, { _id: "d2", ...det });
check("the same revision of an authored determination is refused", r === "E11000 claim_determination_revision_unique", r);
check("a received determination may carry the same revision number",
  attempt(dets, { _id: "d3", ...det, origin: "received" }) === "inserted");
check("a strategy note is accepted", attempt(priv, { _id: "p1", organization_id: org, claim_id: "gate6-claim", kind: "strategy_note" }) === "inserted");
r = attempt(priv, { _id: "p2", organization_id: org, claim_id: "gate6-claim", kind: "strategy_note" });
check("a second strategy note for the claim is refused", r === "E11000 claim_privileged_one_strategy_note", r);
check("simulations of the same claim are many",
  attempt(priv, { _id: "p3", organization_id: org, claim_id: "gate6-claim", kind: "simulation" }) === "inserted" &&
  attempt(priv, { _id: "p4", organization_id: org, claim_id: "gate6-claim", kind: "simulation" }) === "inserted");
check("first submission for a claim is accepted", attempt(subs, { _id: "u1", organization_id: org, claim_id: "gate6-claim" }) === "inserted");
r = attempt(subs, { _id: "u2", organization_id: org, claim_id: "gate6-claim" });
check("a second submission for the claim is refused", r === "E11000 claim_submission_claim_unique", r);

// 20261120_0001 (M-05). One row per case and escalation origin; rows added by hand
// (no origin) are unconstrained; the same origin in another case is another row.
const matrix = "arbitration_claim_matrix";
const mrow = { organization_id: org, case_id: "gate6-case", origin_key: "claim:gate6-claim:head:H1" };
check("first escalated row is accepted", attempt(matrix, { _id: "m1", ...mrow }) === "inserted");
r = attempt(matrix, { _id: "m2", ...mrow });
check("the same origin in the same case is refused", r === "E11000 matrix_case_origin_unique", r);
check("rows added by hand are unconstrained",
  attempt(matrix, { _id: "m3", organization_id: org, case_id: "gate6-case" }) === "inserted" &&
  attempt(matrix, { _id: "m4", organization_id: org, case_id: "gate6-case" }) === "inserted");
check("the same origin in another case is accepted",
  attempt(matrix, { _id: "m5", ...mrow, case_id: "gate6-case-2" }) === "inserted");

for (const name of [dets, priv, subs, matrix]) db.getCollection(name).deleteMany({ organization_id: org });
db.getCollection(boards).deleteMany({ organization_id: org });
db.skill_versions.deleteMany({ owner_scope: "gate6-probe" });
db.getCollection(links).deleteMany({ organization_id: org });
db.getCollection(notice).deleteMany({ organization_id: org });
db.claims.deleteMany({ organization_id: org });
db.skill_steps.deleteMany({ subject_id: "gate6:H1" });
const left = db.getCollection(boards).countDocuments({}) + db.skill_versions.countDocuments({ owner_scope: "gate6-probe" }) +
  db.getCollection(notice).countDocuments({}) + db.claims.countDocuments({}) + db.skill_steps.countDocuments({}) +
  db.getCollection(links).countDocuments({}) + db.getCollection(dets).countDocuments({}) +
  db.getCollection(priv).countDocuments({}) + db.getCollection(subs).countDocuments({}) +
  db.getCollection(matrix).countDocuments({});
check("probe rows removed", left === 0, left + " left");
if (failed) { print("PROBE FAILED=" + failed); quit(1); }
print("PROBE=PASS");
JS
mongo_js 09-probe "$OUT_DIR/09-probe.js" || die "$(grep -E '^FAIL' "$OUT_DIR/09-probe.stdout" || tail -5 "$OUT_DIR/09-probe.stderr")"
grep -q '^PROBE=PASS$' "$OUT_DIR/09-probe.stdout" || die "the duplicate probe did not complete"
PROBE_PASSES=$(grep -c '^PASS' "$OUT_DIR/09-probe.stdout")
ok "$PROBE_PASSES checks passed, 0 failed"

# --------------------------------------------------------------------------- #
# 4. Idempotency
# --------------------------------------------------------------------------- #
cat >"$OUT_DIR/10-fingerprint.js" <<'JS'
// Every collection, its document count and its indexes. Ledger timestamps are
// not included, so two fingerprints are equal exactly when nothing moved.
const state = {};
for (const name of db.getCollectionNames().sort()) {
  const c = db.getCollection(name);
  state[name] = {
    documents: c.countDocuments({}),
    indexes: c.getIndexes().map((i) => [i.name, i.key, i.unique === true, i.partialFilterExpression || null]).sort(),
  };
}
print(JSON.stringify(state));
JS
STEP="second apply is a no-op"
say "$STEP"
mongo_js 10-fingerprint-before "$OUT_DIR/10-fingerprint.js" || die "could not fingerprint the database"
REAPPLY_RC=0
backend 11-reapply $MIGRATE --apply --fail-on-warning || REAPPLY_RC=$?
[[ $REAPPLY_RC -eq 0 ]] || die "the second apply exited $REAPPLY_RC: $(tail -8 "$OUT_DIR/11-reapply.stderr")"
REAPPLY=$(py "$OUT_DIR/11-reapply.stdout" "$SUMMARISE_RUN")
[[ "$REAPPLY" == total=$EXPECTED\ applied=0\ dry_run=0\ skipped=$EXPECTED\ warnings=0\ * ]] || die "the second apply was not a no-op: $REAPPLY"
mongo_js 11-fingerprint-after "$OUT_DIR/10-fingerprint.js" || die "could not fingerprint the database"
cmp -s "$OUT_DIR/10-fingerprint-before.stdout" "$OUT_DIR/11-fingerprint-after.stdout" \
  || die "collections, counts or indexes changed across the second apply"
FINGERPRINT=$(py "$OUT_DIR/11-fingerprint-after.stdout" '
import json, sys
state = json.load(sys.stdin)
print("collections=%d indexes=%d documents=%d" % (
    len(state), sum(len(v["indexes"]) for v in state.values()), sum(v["documents"] for v in state.values())))
')
ok "exit $REAPPLY_RC, $REAPPLY; $FINGERPRINT, identical before and after"

STEP="migrate_database --list (after)"
say "$STEP"
LIST_AFTER_RC=0
backend 12-list-after $MIGRATE --list || LIST_AFTER_RC=$?
[[ $LIST_AFTER_RC -eq 0 ]] || die "--list exited $LIST_AFTER_RC"
LIST_AFTER_COUNTS=$(py "$OUT_DIR/12-list-after.stdout" "$SUMMARISE_LIST" | head -1)
[[ "$LIST_AFTER_COUNTS" == "total=$EXPECTED applied=$EXPECTED pending=0 applied_not_in_catalogue=0" ]] \
  || die "expected $EXPECTED of $EXPECTED applied, got: $LIST_AFTER_COUNTS"
ok "exit $LIST_AFTER_RC, $LIST_AFTER_COUNTS"

# --------------------------------------------------------------------------- #
# 5. Evidence
# --------------------------------------------------------------------------- #
STEP="write the evidence block"
RUN="docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/${DB}?replicaSet=${RS} -e MONGODB_DATABASE=${DB} -e MONGODB_REPLICA_SET=${RS} -e ENVIRONMENT=development ${IMAGE}"
{
  echo '```text'
  echo "GATE 6 BULLET 2 - FRESH INSTALL RUN  ${RUN_ID}"
  echo "Result            PASS - ${EXPECTED}/${EXPECTED} migrations applied to an empty MongoDB"
  echo "Date (UTC)        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Host              $(uname -srm); docker $(docker version -f '{{.Server.Version}}')"
  echo "Commit            ${GIT_SHA} (${GIT_BRANCH}); uncommitted backend changes: ${DIRTY}"
  echo "Backend image     ${IMAGE} ${IMAGE_ID}"
  echo "                  ${IMAGE_SOURCE}"
  echo "MongoDB           ${MONGO_IMAGE} ${MONGO_IMAGE_ID}"
  echo "                  server version ${MONGO_VERSION}; replica set read back from rs.status().set: ${RS_READ_BACK}"
  echo "                  disposable container on a private network: no published port, no host bind mount"
  echo
  echo "Empty before anything ran"
  echo "$EMPTY_PROOF" | sed 's/^/  /'
  echo "Target read back from inside the backend container"
  echo "$TARGET_PROOF" | sed 's/^/  /'
  echo
  echo "Commands (each in a new backend container: ${RUN})"
  echo "  1. ${MIGRATE} --list"
  echo "       exit ${LIST_BEFORE_RC}   ${LIST_BEFORE_COUNTS}"
  echo "  2. ${MIGRATE} --fail-on-warning"
  echo "       exit ${DRY_RC}   ${DRY}; database still empty afterwards"
  echo "  3. ${MIGRATE} --apply --fail-on-warning"
  echo "       exit ${APPLY_RC}   ${APPLY}"
  echo "  4. ${MIGRATE} --apply --fail-on-warning   (second apply)"
  echo "       exit ${REAPPLY_RC}   ${REAPPLY}"
  echo "       ${FINGERPRINT}, identical before and after"
  echo "  5. ${MIGRATE} --list"
  echo "       exit ${LIST_AFTER_RC}   ${LIST_AFTER_COUNTS}"
  echo
  echo "Skill and claim migrations 20261114_0001 to 20261120_0001, read with mongosh: ${BENCH_PASSES} passed, 0 failed"
  grep -E '^(PASS|FAIL)' "$OUT_DIR/07-bench.stdout" | sed 's/^/  /'
  echo "Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES"
  echo "$SEEDED" | sed 's/^SEED  /  /'
  echo "Unique indexes probed with real inserts (probe rows removed afterwards): ${PROBE_PASSES} passed, 0 failed"
  grep -E '^(PASS|FAIL)' "$OUT_DIR/09-probe.stdout" | sed 's/^/  /'
  echo
  echo "Step logs         ${OUT_DIR}"
  echo '```'
} >"$OUT_DIR/evidence.md"

printf '\n===== BEGIN GATE 6 EVIDENCE (paste into docs/GATE_6_FRESH_INSTALL_EVIDENCE.md) =====\n'
cat "$OUT_DIR/evidence.md"
printf '===== END GATE 6 EVIDENCE =====\n'
printf '\nGATE 6 FRESH INSTALL: PASS (%s/%s). This script changed no tracked file.\n' "$EXPECTED" "$EXPECTED"

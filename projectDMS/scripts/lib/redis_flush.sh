# shellcheck shell=bash
#
# Flush a Redis-protocol engine's persistence to disk, and PROVE it landed.
#
# R-A9G left production's FalkorDB engine running out of band: the compose
# `falkordb` service is stopped and preserved for rollback while the live engine
# is a separately created container holding the `falkordb` network alias.
# `production_backup.sh` flushed with
#
#     docker compose $COMPOSE_FILES exec -T falkordb sh -c '... BGSAVE' || true
#
# which printed `service "falkordb" is not running`, and the `|| true` swallowed
# it. The backup then archived the volume and reported success. That is the
# house failure mode this repository exists to refuse: marking success on a
# skipped step (R-A9H D1).
#
# Two things are wrong there and both are fixed here.
#
#   1. RESOLUTION. The compose service name is a deployment detail, not the
#      engine's identity. What the application actually talks to is whatever
#      container answers to the `falkordb` network alias. So resolution asks
#      compose first - the normal case, and the only one that survives a
#      rename - and then falls back to a real alias lookup, which is what the
#      backend's own DNS resolution does.
#
#   2. VERIFICATION. `BGSAVE` returns immediately; its reply says the fork
#      started, not that a file was written. The proof that persistence landed
#      is `LASTSAVE` advancing, so that is what is waited for and reported.
#
# The password is never written onto a host command line. `redis-cli -a "$PW"`
# runs inside the container against the container's own environment, because a
# secret in an argv on the host is recorded by sudo in /var/log/auth.log and by
# the systemd journal - which is exactly how R-A9G's rotated FalkorDB
# credentials reached those files (R-A9H D4).

REDIS_FLUSH_TIMEOUT=${REDIS_FLUSH_TIMEOUT:-30}

# Print the id of the running container that serves $1, or return 1.
redis_flush_resolve() {
  local service=$1 candidate compose_id=""

  compose_id=$(docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps -q "$service" 2>/dev/null \
    | tr -d '\r' | head -n 1)
  if [ -n "$compose_id" ] \
    && [ "$(docker inspect -f '{{.State.Running}}' "$compose_id" 2>/dev/null | tr -d '\r')" = "true" ]; then
    printf '%s\n' "$compose_id"
    return 0
  fi

  for candidate in $(docker ps -q 2>/dev/null | tr -d '\r'); do
    if docker inspect \
      -f '{{range $net, $conf := .NetworkSettings.Networks}}{{range $conf.Aliases}}{{println .}}{{end}}{{end}}' \
      "$candidate" 2>/dev/null | tr -d '\r' | grep -qx -- "$service"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

# redis-cli inside $1, reading the password from the container's own $2.
redis_flush_cli() {
  local container=$1 password_var=$2
  shift 2
  docker exec "$container" sh -c \
    'redis-cli --no-auth-warning -a "$'"$password_var"'" "$@"' _ "$@" 2>/dev/null \
    | tr -d '\r' | head -n 1
}

# redis_flush <compose-service> <password-env-var> [label]
#
# Prints `FLUSH <label>: ok ...` on stdout and returns 0 only when LASTSAVE
# advanced. Every other outcome prints `FLUSH <label>: FAILED ...` on stderr and
# returns 1 - including the one that used to be silent, where nothing is
# listening at all.
redis_flush() {
  local service=$1 password_var=$2 label=${3:-$1}
  local container before after waited=0

  if ! container=$(redis_flush_resolve "$service"); then
    printf 'FLUSH %s: FAILED no running container answers to the compose service or the network alias "%s"\n' \
      "$label" "$service" >&2
    return 1
  fi

  before=$(redis_flush_cli "$container" "$password_var" LASTSAVE)
  case "$before" in
    '' | *[!0-9]*)
      printf 'FLUSH %s: FAILED LASTSAVE unreadable on container %s (engine down, or wrong credential)\n' \
        "$label" "${container:0:12}" >&2
      return 1
      ;;
  esac

  # A rejected BGSAVE is not decided here. "Background save already in progress"
  # is an error reply and a perfectly good outcome, so the verdict is left to
  # LASTSAVE below, which measures the file rather than the reply.
  redis_flush_cli "$container" "$password_var" BGSAVE >/dev/null 2>&1 || true

  while [ "$waited" -lt "$REDIS_FLUSH_TIMEOUT" ]; do
    after=$(redis_flush_cli "$container" "$password_var" LASTSAVE)
    case "$after" in
      '' | *[!0-9]*) : ;;
      *)
        if [ "$after" -gt "$before" ]; then
          printf 'FLUSH %s: ok (container %s, LASTSAVE %s -> %s)\n' \
            "$label" "${container:0:12}" "$before" "$after"
          return 0
        fi
        ;;
    esac
    sleep 1
    waited=$((waited + 1))
  done

  printf 'FLUSH %s: FAILED BGSAVE did not land within %ss on container %s (LASTSAVE still %s)\n' \
    "$label" "$REDIS_FLUSH_TIMEOUT" "${container:0:12}" "$before" >&2
  return 1
}

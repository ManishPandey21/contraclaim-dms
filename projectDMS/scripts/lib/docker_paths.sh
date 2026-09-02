# Shared helper for bind-mounting a host directory into a container.
#
# On Linux (and therefore in production) a host path is passed through
# unchanged. Under Git Bash / MSYS the shell rewrites both halves of
# `-v <host>:<container>`, so the container-side path arrives as something like
# `C:/Program Files/Git/backup` and the mount silently lands somewhere the
# command inside the container cannot see. Converting the host half with
# `cygpath -m` and disabling the rewrite keeps the backup and restore drills
# runnable on a developer machine without changing production behaviour.
#
# Usage:
#   . "$ROOT_DIR/scripts/lib/docker_paths.sh"
#   host=$(docker_host_path "$some_dir")
#   docker_run -v "${host}:/backup" busybox ...

docker_host_path() {
  local path=$1
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$path"
  else
    printf '%s' "$path"
  fi
}

docker_run() {
  if command -v cygpath >/dev/null 2>&1; then
    MSYS_NO_PATHCONV=1 docker run "$@"
  else
    docker run "$@"
  fi
}

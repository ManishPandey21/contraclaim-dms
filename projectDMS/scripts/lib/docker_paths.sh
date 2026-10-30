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
# The same rewrite hits every container-side absolute path, not only mounts:
# `docker exec c ls /FalkorDB` becomes `ls "C:/Program Files/Git/FalkorDB"`, and
# `docker cp c:/FalkorDB/dump.rdb .` copies from a path that does not exist. The
# `docker_exec` and `docker_cp` wrappers suppress it the same way.
#
# `native_path` is the mirror image: an argument that stays on the host but is
# handed to a native Windows executable (python.exe, say) has to be a Windows
# path, because MSYS only rewrites arguments it passes itself.
#
# Usage:
#   . "$ROOT_DIR/scripts/lib/docker_paths.sh"
#   host=$(docker_host_path "$some_dir")
#   docker_run -v "${host}:/backup" busybox ...
#   docker_exec "$container" ls -la /data
#   docker_cp "$container:/FalkorDB/dump.rdb" "$staging/dump.rdb"
#   "$python" "$(native_path "$script")" "$(native_path "$archive")"

docker_host_path() {
  local path=$1
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$path"
  else
    printf '%s' "$path"
  fi
}

native_path() {
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

docker_exec() {
  if command -v cygpath >/dev/null 2>&1; then
    MSYS_NO_PATHCONV=1 docker exec "$@"
  else
    docker exec "$@"
  fi
}

docker_cp() {
  if command -v cygpath >/dev/null 2>&1; then
    MSYS_NO_PATHCONV=1 docker cp "$@"
  else
    docker cp "$@"
  fi
}

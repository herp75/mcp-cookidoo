#!/bin/sh
# Back up a Cookidoo account into a git repository, with the mcp-cookidoo container image.
# Only reads from Cookidoo. Commits only when something changed. See README, "Backup".
#
#   scripts/cookidoo-backup.sh [BACKUP_DIR]          default: ~/cookidoo-backup
#
# COOKIDOO_ENV_FILE  credentials, as for the MCP server (default ~/.config/cookidoo-mcp/cookidoo.env)
# COOKIDOO_IMAGE     image (default ghcr.io/herp75/mcp-cookidoo:2)
# CONTAINER          podman or docker (default: podman if installed)
# Exit status: that of backup.py (0 done, 1 aborted, 2 done with some recipes not read).
set -eu

dir=${1:-$HOME/cookidoo-backup}
env_file=${COOKIDOO_ENV_FILE:-$HOME/.config/cookidoo-mcp/cookidoo.env}
image=${COOKIDOO_IMAGE:-ghcr.io/herp75/mcp-cookidoo:2}
engine=${CONTAINER:-$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)}

[ -r "$env_file" ] || { echo "Cannot read $env_file" >&2; exit 1; }
mkdir -p "$dir"
[ -d "$dir/.git" ] || git -C "$dir" init -q

# Files in the backup belong to the user running this script, not to the container's user
if [ "$engine" = podman ]; then
  set -- --userns=keep-id --user "$(id -u):$(id -g)" --pull=newer
else
  set -- --user "$(id -u):$(id -g)" --pull=always
fi

status=0
"$engine" run --rm "$@" --env-file "$env_file" -v "$dir:/backup:Z" \
  --entrypoint /app/.venv/bin/python "$image" backup.py /backup || status=$?
if [ "$status" -ne 0 ] && [ "$status" -ne 2 ]; then
  echo "Backup failed (exit status $status): nothing committed" >&2
  exit "$status"
fi

git -C "$dir" add -A
if ! git -C "$dir" diff --cached --quiet; then
  if [ -z "$(git -C "$dir" config user.email || true)" ]; then
    set -- -c user.name="Cookidoo backup" -c user.email="cookidoo-backup@localhost"
  else
    set --
  fi
  git -C "$dir" "$@" commit -q -m "Backup $(date +%F)"
fi
exit "$status"

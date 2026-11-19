#!/bin/sh
set -eu
umask 077

REDIS_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$REDIS_DIRECTORY/scripts/lib/redis.sh"

require_docker
backup_directory=${1:-"$REDIS_DIRECTORY/backups"}
backup_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
remote_snapshot="/data/backup-$backup_id.rdb"
local_snapshot="$backup_directory/redis-$backup_id.rdb"

cleanup_snapshot() {
    backup_exit_status=$?
    trap - EXIT
    if ! docker exec "$REDIS_CONTAINER" rm -f "$remote_snapshot"; then
        echo 'Could not remove the temporary Redis backup file.' >&2
        if [ "$backup_exit_status" -eq 0 ]; then
            backup_exit_status=1
        fi
    fi
    exit "$backup_exit_status"
}

mkdir -p "$backup_directory"
chmod 700 "$backup_directory"
trap cleanup_snapshot EXIT
trap 'exit 1' HUP INT TERM

# Take a consistent RDB snapshot; never copy files from the live AOF directory.
redis_cli operator_user "$REDIS_DIRECTORY/secrets/operator.env" --rdb "$remote_snapshot"
docker exec "$REDIS_CONTAINER" redis-check-rdb "$remote_snapshot" >/dev/null
docker cp "$REDIS_CONTAINER:$remote_snapshot" "$local_snapshot" >/dev/null
chmod 600 "$local_snapshot"
sha256sum "$local_snapshot" > "$local_snapshot.sha256"
printf 'Verified backup: %s\n' "$local_snapshot"

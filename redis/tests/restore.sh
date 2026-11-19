#!/bin/sh
# Rehearse recovery only on an isolated test deployment; never on an application server.
set -eu

if [ "${REDIS_RESTORE_TEST_APPROVED:-}" != 'pre-cutover-only' ]; then
    echo 'Set REDIS_RESTORE_TEST_APPROVED=pre-cutover-only for the isolated test target.' >&2
    exit 1
fi

REDIS_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$REDIS_DIRECTORY/scripts/lib/redis.sh"
require_docker

snapshot=${1:?Supply an absolute path to a verified test snapshot}
restore_volume="studio-redis-restore-test-$(date +%s)-$$"
restore_override=$(mktemp)
redis_image=${REDIS_TEST_IMAGE:-redis:8.10.3-alpine}
operator_password="$REDIS_DIRECTORY/secrets/operator.env"

compose() {
    docker compose -f "$REDIS_DIRECTORY/compose.yml" "$@"
}

restore_original_target() {
    restore_exit_status=$?
    trap - EXIT
    if ! compose up -d --wait; then
        echo 'Could not restore the original test target; inspect Docker before continuing.' >&2
        restore_exit_status=1
    fi
    rm -f "$restore_override"
    exit "$restore_exit_status"
}

operator() {
    redis_cli operator_user "$operator_password" "$@"
}

verify_recovered_data() {
    [ "$(operator GET core:infra-restart-persistence)" = durable ]
    pending=$(operator XPENDING core:infra-restore-stream infra-restore-group | head -1)
    [ "$pending" = 1 ]
}

wait_for_aof_rewrite() {
    attempts=0
    while [ "$attempts" -lt 60 ]; do
        persistence=$(operator INFO persistence | tr -d '\r')
        if printf '%s\n' "$persistence" | grep -q '^aof_rewrite_in_progress:0$' &&
           printf '%s\n' "$persistence" | grep -q '^aof_last_bgrewrite_status:ok$'; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 1
    done
    echo 'AOF rewrite did not complete within 60 seconds.' >&2
    return 1
}

cat > "$restore_override" <<'YAML'
services:
  redis:
    entrypoint:
      - redis-server
      - /etc/redis/redis.conf
      - --appendonly
      - 'no'
      - --maxmemory
      - 256mb
YAML
trap restore_original_target EXIT
trap 'exit 1' HUP INT TERM

# The source volume is untouched. Recovery always uses a new rehearsal volume.
docker volume create "$restore_volume" >/dev/null
docker run --rm --entrypoint sh \
    -v "$restore_volume:/restore" -v "$snapshot:/backup.rdb:ro" "$redis_image" -c '
        set -eu
        cp /backup.rdb /restore/dump.rdb
        chown 999:999 /restore /restore/dump.rdb
        redis-check-rdb /restore/dump.rdb >/dev/null
    '
REDIS_VOLUME_NAME="$restore_volume" \
    compose -f "$restore_override" up -d --wait
verify_recovered_data

[ "$(operator CONFIG SET appendonly yes)" = OK ]
wait_for_aof_rewrite
REDIS_VOLUME_NAME="$restore_volume" compose up -d --wait
verify_recovered_data
printf 'PASS: RDB restore, pending stream state, AOF conversion and restart.\n'
printf 'Rehearsal volume retained: %s\n' "$restore_volume"

#!/bin/sh
set -eu

REDIS_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$REDIS_DIRECTORY/scripts/lib/redis.sh"

require_docker
redis_cli monitor_user "$REDIS_DIRECTORY/secrets/monitor_password" \
    INFO memory clients persistence stats

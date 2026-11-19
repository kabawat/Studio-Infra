#!/bin/sh
set -eu

password_file=${REDIS_MONITOR_PASSWORD_FILE:-/run/secrets/monitor_password}
REDISCLI_AUTH=$(cat "$password_file")
export REDISCLI_AUTH
redis-cli --user monitor_user ping | grep -qx PONG

#!/bin/sh
set -eu

max_memory=${REDIS_MAXMEMORY:-256mb}
if ! printf '%s\n' "$max_memory" | grep -Eq '^[1-9][0-9]*([kKmMgG]([bB])?|[bB])?$'; then
    echo 'REDIS_MAXMEMORY must be a positive size, such as 256mb or 1gb.' >&2
    exit 1
fi

exec redis-server /etc/redis/redis.conf --maxmemory "$max_memory"

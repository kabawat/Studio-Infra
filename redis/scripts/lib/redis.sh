# Shared Docker/credential helpers. Source this file; do not execute it directly.
REDIS_CONTAINER=${REDIS_CONTAINER:-redis}

require_docker() {
    command -v docker >/dev/null 2>&1 || {
        echo 'Docker is required for this command.' >&2
        return 1
    }
}

read_redis_password() {
    case "$1" in
        *.env) sed -n 's/^REDISCLI_AUTH=//p' "$1" ;;
        *) cat "$1"; printf '\n' ;;
    esac
}

redis_cli() {
    redis_user=$1
    redis_password_file=$2
    shift 2
    if [ ! -r "$redis_password_file" ] || [ ! -s "$redis_password_file" ]; then
        echo "Missing or unreadable Redis credential file: $redis_password_file" >&2
        return 1
    fi

    # stdin keeps credentials out of Docker arguments and process listings.
    read_redis_password "$redis_password_file" |
        docker exec -i "$REDIS_CONTAINER" sh -c '
            set -eu
            redis_user=$1
            shift
            IFS= read -r REDISCLI_AUTH
            export REDISCLI_AUTH
            exec redis-cli --user "$redis_user" "$@"
        ' sh "$redis_user" "$@"
}

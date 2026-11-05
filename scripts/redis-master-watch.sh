#!/bin/sh
set -eu
export LC_ALL=C
: "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"

auth_command() {
    printf '*2\r\n$4\r\nAUTH\r\n$%s\r\n%s\r\n' "${#REDIS_PASSWORD}" "$REDIS_PASSWORD"
}

query_majority() {
    votes_one=0
    votes_two=0
    for sentinel in sentinel-1 sentinel-2 sentinel-3; do
        reply=$({
            auth_command
            printf '*3\r\n$8\r\nSENTINEL\r\n$23\r\nget-master-addr-by-name\r\n$4\r\nchat\r\n*1\r\n$4\r\nQUIT\r\n'
        } | nc -w 1 "$sentinel" 26379 2>/dev/null) || continue
        host=$(printf '%s\n' "$reply" | tr -d '\r' | awk '
            NR == 1 && $0 != "+OK" { exit }
            NR == 2 && $0 != "*2" { exit }
            NR == 3 && $0 != "$7" { exit }
            NR == 4 && ($0 == "redis-1" || $0 == "redis-2") { host = $0 }
            NR == 5 && $0 != "$4" { exit }
            NR == 6 && $0 == "6379" && host != "" { print host }
        ')
        case "$host" in
            redis-1) votes_one=$((votes_one + 1)) ;;
            redis-2) votes_two=$((votes_two + 1)) ;;
        esac
    done
    if [ "$votes_one" -ge 2 ]; then
        printf '%s\n' redis-1
    elif [ "$votes_two" -ge 2 ]; then
        printf '%s\n' redis-2
    else
        return 1
    fi
}

if [ "${1:-}" = query ]; then
    query_majority
    exit $?
fi

# A failed watcher must terminate the main process: Docker restart policies
# react to process exit, not to an unhealthy status alone.
trap 'kill -TERM 1 2>/dev/null || true' EXIT
trap 'exit 1' HUP INT TERM
current=${REDIS_MASTER_HOST:?REDIS_MASTER_HOST is required}
while :; do
    if next=$(query_majority) && [ "$next" != "$current" ]; then
        result=$(printf 'set server redis_master/master fqdn %s\n' "$next" |
            nc -w 1 127.0.0.1 9998)
        # FQDN acceptance precedes async DNS resolution. Closing sessions now
        # would let reconnects bind to the old address until DNS catches up.
        # Wait for the actual address first, then drain all existing sessions.
        expected_address=$(timeout 2 getent hosts "$next" | awk 'NR == 1 { print $1 }')
        [ -n "$expected_address" ] || exit 1
        route_deadline=$(( $(date +%s) + 10 ))
        while ! printf 'show servers state\n' | nc -w 1 127.0.0.1 9998 |
            awk -v expected="$next" -v address="$expected_address" '
                /^#/ { for (i = 2; i <= NF; ++i) col[$i] = i - 1; next }
                col["be_name"] && $(col["be_name"]) == "redis_master" &&
                $(col["srv_name"]) == "master" &&
                $(col["srv_fqdn"]) == expected &&
                $(col["srv_addr"]) == address { found = 1 }
                END { exit !found }
            '; do
            if [ "$(date +%s)" -ge "$route_deadline" ]; then
                echo 'redis master watcher: route address did not resolve in time' >&2
                exit 1
            fi
            sleep 0.1
        done
        result=$(printf 'shutdown sessions server redis_master/master\n' |
            nc -w 1 127.0.0.1 9998)
        if [ -n "$result" ]; then
            echo 'redis master watcher: session shutdown was not accepted' >&2
            exit 1
        fi
        printf 'redis master route: %s -> %s\n' "$current" "$next"
        current=$next
    fi
    sleep 0.5
done

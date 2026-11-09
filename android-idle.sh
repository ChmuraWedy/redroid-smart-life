#!/bin/sh
set -eu
cd "$(dirname "$0")"

case ${1:-} in
    idle|wake|status) action=$1 ;;
    *) echo "Usage: $0 idle|wake|status" >&2; exit 2 ;;
esac

exec 8>.idle.lock
flock -n 8 || { echo "An Android idle or wake operation is running." >&2; exit 75; }
container=$(sudo -n docker compose ps -q redroid)
if [ -z "$container" ]; then
    if [ "$action" = status ]; then
        echo '{"state":"stopped","ram_mib":0,"swap_mib":0}'
        exit 0
    fi
    echo "Start ReDroid with ./start.sh first." >&2
    exit 1
fi
docker_paused=$(sudo -n docker inspect -f '{{.State.Paused}}' "$container")
if [ "$docker_paused" = true ] && [ "$action" != status ]; then
    echo "Resume the Docker pause with docker compose unpause redroid first." >&2
    exit 1
fi
pid=$(sudo -n docker inspect -f '{{.State.Pid}}' "$container")
group=$(sudo -n awk -F: '$1==0{print $3}' "/proc/$pid/cgroup")
if [ -z "$group" ] || [ "$group" = / ]; then
    echo "Could not identify ReDroid's own Linux memory cgroup." >&2
    exit 1
fi
cgroup=/sys/fs/cgroup$group

freeze() { printf '%s\n' "$1" | sudo -n tee "$cgroup/cgroup.freeze" >/dev/null; }
ram_mib() { awk '{print int(($1 + 1048575) / 1048576)}' "$cgroup/memory.current"; }
swap_mib() { awk '{printf "%.0f", $1/1048576}' "$cgroup/memory.swap.current"; }
wait_for_freeze_state() {
    for attempt in 1 2 3 4 5; do
        if grep -qx "frozen $1" "$cgroup/cgroup.events"; then return 0; fi
        sleep 1
    done
    return 1
}
reclaim() {
    sudo -n timeout 10 sh -c 'printf "%s\n" "$1" > "$2"' sh "$1" "$cgroup/memory.reclaim" 2>/dev/null ||
        echo "The kernel reclaimed less than requested for $1." >&2
}

if [ "$action" = status ]; then
    state=awake
    if [ "$docker_paused" = true ]; then
        state=docker-paused
    elif grep -qx 'frozen 1' "$cgroup/cgroup.events"; then
        state=frozen
    fi
    printf '{"state":"%s","ram_mib":%s,"swap_mib":%s}\n' "$state" "$(ram_mib)" "$(swap_mib)"
    exit 0
fi
if [ "$action" = wake ]; then
    freeze 0
    if ! wait_for_freeze_state 0; then
        echo "Linux could not thaw ReDroid." >&2
        exit 1
    fi
    echo "ReDroid is awake. Android can page its state back from swap as needed."
    exit 0
fi
if [ "$(cat "$cgroup/cgroup.freeze")" = 1 ]; then
    echo "ReDroid is already idle at $(ram_mib) MiB of RAM."
    exit 0
fi

exec 9>.bridge.lock
flock -n 9 || { echo "End the Intel Bluetooth session before idling Android." >&2; exit 75; }
if [ "${REDROID_IDLE_SOURCE:-}" != api ]; then
    endpoint=$(sudo -n docker compose port controller-api 8080 2>/dev/null || true)
    if [ -n "$endpoint" ] && [ -n "$(ss -Htn state established "( sport = :${endpoint##*:} )")" ]; then
        echo "Finish the active controller API connection before idling Android." >&2
        exit 75
    fi
fi

target=120
tolerance=16
trap 'freeze 0' EXIT
trap 'exit 1' HUP INT TERM
freeze 1
if ! wait_for_freeze_state 1; then
    echo "Linux could not freeze ReDroid." >&2
    exit 1
fi

remaining=$(($(ram_mib) - target))
if [ "$remaining" -gt "$tolerance" ]; then
    cache_request=$remaining
    if [ "$cache_request" -gt 512 ]; then cache_request=512; fi
    reclaim "${cache_request}M swappiness=0"
fi

for attempt in 1 2 3 4 5 6; do
    before=$(ram_mib)
    remaining=$((before - target))
    if [ "$remaining" -le "$tolerance" ]; then break; fi
    swap_headroom=$(awk '/^SwapFree:/{print int($2/1024) - 512}' /proc/meminfo)
    if [ "$swap_headroom" -le 0 ]; then break; fi
    request=$remaining
    if [ "$request" -gt 256 ]; then request=256; fi
    if [ "$request" -gt "$swap_headroom" ]; then request=$swap_headroom; fi
    reclaim "${request}M swappiness=200"
    if [ "$(ram_mib)" -ge "$before" ]; then break; fi
done

ram=$(ram_mib)
if [ "$ram" -gt "$((target + tolerance))" ]; then
    echo "ReDroid reached $ram MiB. Resuming because the idle target was missed." >&2
    exit 1
fi

swap=$(swap_mib)
trap - EXIT HUP INT TERM
echo "ReDroid is idle at $ram MiB of RAM and $swap MiB of swap. Run $0 wake to resume."

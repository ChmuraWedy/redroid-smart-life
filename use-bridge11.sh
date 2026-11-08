#!/bin/bash
set -eu
cd "$(dirname "$0")"

exec 9>.bridge.lock
if ! flock -n 9; then
    echo "A Bluetooth bridge session is already running." >&2
    exit 1
fi

index=
for controller in /sys/class/bluetooth/hci*; do
    device=$(readlink -f "$controller/device/..")
    if [[ -r "$device/idVendor" && -r "$device/idProduct" &&
        $(cat "$device/idVendor") == 8087 && $(cat "$device/idProduct") == 0aaa ]]; then
        index=${controller##*hci}
        break
    fi
done
if [[ -z "$index" ]]; then
    echo "Intel Bluetooth controller 8087:0aaa was not found." >&2
    exit 1
fi

container=$(sudo -n docker compose ps -q redroid)
if [[ -z "$container" ]]; then
    echo "Start Android 11 with ./start.sh first." >&2
    exit 1
fi
service=vendor.bluetooth-1-1
stock_hal=/vendor/bin/hw/android.hardware.bluetooth@1.1-service.sim
intel_hal=/vendor/bin/hw/redroid-hci-bridge11
android() { sudo -n docker exec -u 0 "$container" "$@" </dev/null; }
android test -x "$intel_hal"
volume=$(sudo -n docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$container")
if [[ -z "$volume" ]]; then
    echo "The Android /data volume is missing." >&2
    exit 1
fi
socket="$volume/local/tmp/redroid-intel-hci.sock"
if sudo -n test -e "$socket"; then
    echo "The Android HCI socket already exists. Check for an active bridge." >&2
    exit 1
fi

relay=
mounted=
restore() {
    set +e
    if [[ -n "$mounted" ]]; then
        android service call bluetooth_manager 8 s16 com.android.shell i32 1 >/dev/null
        sleep 2
        android setprop ctl.stop "$service"
        sleep 1
        android umount "$stock_hal"
    fi
    android setprop ctl.start "$service"
    if [[ -n "$relay" ]]; then
        sudo -n kill -TERM "$relay" 2>/dev/null || true
        wait "$relay" 2>/dev/null
    fi
    sudo -n rm -f -- "$socket"
}
trap restore EXIT
trap 'exit 0' TERM INT HUP

android service call bluetooth_manager 8 s16 com.android.shell i32 0 >/dev/null
sleep 3
android setprop ctl.stop "$service"
sleep 1
sudo -n timeout --kill-after=5 900 python3 -u intel_bridge11.py "$socket" "$index" > bridge11-live.log 2>&1 </dev/null &
relay=$!
for attempt in $(seq 1 20); do
    if sudo -n test -S "$socket"; then break; fi
    sleep 0.25
done
sudo -n test -S "$socket"
android mount --bind "$intel_hal" "$stock_hal"
mounted=yes
android setprop ctl.start "$service"
sleep 2
android service call bluetooth_manager 6 s16 com.android.shell >/dev/null

for attempt in $(seq 1 20); do
    state=$(android dumpsys bluetooth_manager | awk '/^[[:space:]]*state:/{print $2; exit}')
    if [[ "$state" = ON ]] && grep -q '^HCI complete 0x0c03: 0x00' bridge11-live.log; then
        echo "Bridge for hci$index active on Android 11 for up to 15 minutes"
        result=0
        wait "$relay" || result=$?
        # GNU timeout returns 124 when the pairing window expires normally.
        if [[ "$result" != 0 && "$result" != 124 ]]; then
            echo "Intel relay exited with status $result. See bridge11-live.log." >&2
            exit "$result"
        fi
        exit 0
    fi
    sleep 1
done
echo "Bridge for hci$index did not reach Bluetooth ON. See bridge11-live.log." >&2
exit 1

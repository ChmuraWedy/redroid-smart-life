#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ ! -x android/bluetooth-hci-bridge11 ]; then
    echo "Missing executable Android Bluetooth HAL." >&2
    exit 1
fi

sudo -n modprobe binder_linux devices=binder,hwbinder,vndbinder
sudo -n docker compose up -d
sudo -n docker compose exec -T -u 0 redroid chmod 0666 /dev/binder /dev/hwbinder /dev/vndbinder

for attempt in $(seq 1 50); do
    if [ "$(sudo -n docker compose exec -T redroid getprop sys.boot_completed)" = 1 ]; then
        sudo -n docker compose exec -T redroid pm list features |
            grep -qx 'feature:android.hardware.bluetooth_le'
        echo "ReDroid 11 ready with the Bluetooth LE feature."
        exit 0
    fi
    sleep 2
done

echo "Android did not finish booting. Check docker compose logs redroid." >&2
exit 1

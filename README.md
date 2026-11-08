# ReDroid 11 with Smart Life

Run the upstream `redroid/redroid:11.0.0-latest` image on an x86-64 Linux host and pair devices in Smart Life through its physical Intel Bluetooth adapter. Compose pins the image digest tested on the Dell 5070 and stores Android data in a named volume. [Upstream ReDroid documentation](https://github.com/remote-android/redroid-doc)

## Set up the Linux host

Install Docker Engine with Compose, Python 3, and the Binder kernel module. The host scripts use passwordless sudo for Docker, loading Binder, and opening the Intel HCI channel. The tested Intel USB controller has ID `8087:0aaa`. The script detects its current HCI index at runtime.

Clone the repo as `~/redroid-smart-life` on the Linux host and run the startup script.

```bash
git clone https://github.com/ChmuraWedy/redroid-smart-life.git ~/redroid-smart-life
cd ~/redroid-smart-life
./start.sh
```

The default Compose project name is `redroid11`, so the Dell's existing `redroid11_android-data` volume can be reused. Run only one Android container at a time on a host with shared Binder devices.

## Android setup and root access

The Compose startup applies the Android changes on each container creation. It mounts the Android 11 BLE feature declaration and the Intel HCI service from this repo. `start.sh` loads Binder, applies permissions inside the container, and waits for Android to expose BLE. The bridge script starts a session by mounting the Intel service over Android's stock Bluetooth service and checks that the real controller responds before reporting ready.

Root commands are available on the Linux host through Docker.

```bash
sudo docker compose exec -u 0 redroid id
```

ADB normally runs as Android's shell user. To enable root ADB, add `REDROID_ADB_SECURE=0` to `.env` on the Linux host and rerun `./start.sh`. Compose recreates the container using the existing Android data volume. Restore shell-user ADB by setting the value to `1` and rerunning startup. The port is published on the Dell's loopback interface and reached through SSH.

The HAL binary is the x86-64 Android 11 build tested on the Dell. Its source and checksum are in `android/`. Verify it with `(cd android && sha256sum -c SHA256SUMS)` on Linux.

## Start Smart Life pairing from a Mac

Install ADB and optionally scrcpy. In a clone of this repo on your Mac, create a Python environment and run the control script.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python smart_life_add_device.py
```

The default SSH alias is `dell5070`. Use `--host`, `--remote-dir`, or `--remote-port` for a different host, clone location, or ADB port. The script uses the signed-in Smart Life app with its UI in English. For a fresh Android profile, install Smart Life from a trusted APK source and sign in once. With an SSH tunnel open, install a standalone APK using `adb -s 127.0.0.1:15559 install /path/to/SmartLife.apk`. For split packages, use `adb install-multiple` with the base and matching splits.

The control script starts Android, opens the ADB tunnel, enables the physical Bluetooth bridge, and opens **Add Device**. Run the printed scrcpy command in another terminal to view the screen. Keep the control script open while pairing. Ctrl+C or the 15-minute timeout closes the bridge and turns Android Bluetooth off.

The repo contains the Compose setup, Android configuration, and automation. Android app data and the Smart Life APK live outside the repo.

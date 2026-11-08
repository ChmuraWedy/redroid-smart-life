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

The default Compose project name is `redroid11`, so the Dell's existing `redroid11_android-data` volume can be reused. For a fresh Android profile, stop the old container and set a new `COMPOSE_PROJECT_NAME` in the new deployment's `.env` to get a separate data volume. Run only one Android container at a time on a host with shared Binder devices.

## Android setup and root access

The Compose startup applies the Android changes on each container creation. It mounts the Android 11 BLE feature declaration and the Intel HCI service from this repo. `start.sh` loads Binder, applies permissions inside the container, and waits for Android to expose BLE. The bridge script starts a session by mounting the Intel service over Android's stock Bluetooth service and checks that the real controller responds before reporting ready.

Root commands are available on the Linux host through Docker.

```bash
sudo docker compose exec -u 0 redroid id
```

ADB normally runs as Android's shell user. To enable root ADB, add `REDROID_ADB_SECURE=0` to `.env` on the Linux host and rerun `./start.sh`. Compose recreates the container using the existing Android data volume. Restore shell-user ADB by setting the value to `1` and rerunning startup. The port is published on the Dell's loopback interface and reached through SSH.

The HAL binary is the x86-64 Android 11 build tested on the Dell. Its source and checksum are in `android/`. Verify it with `(cd android && sha256sum -c SHA256SUMS)` on Linux.

## Install Smart Life from a Mac

Install ADB and get Smart Life APKs from a trusted source. The installer starts ReDroid and installs Smart Life. When prompted, it accepts the **User Agreement and Privacy Policy**, declines optional tracking, and selects **Do not remind** and **continue to use** on the rooted-device reminder. It then opens the login form for you to sign in.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python smart_life_add_device.py install-app --apk /path/to/SmartLife.apk --view
```

For a split APK set, pass the base APK and all matching split APKs after `--apk`. `--view` opens scrcpy and keeps the ADB tunnel available while you sign in. It requires scrcpy on the Mac.

## Prefill login or sign-up from a secret file

On the Dell, create `secrets/smart-life.env` inside the deployment directory. Use `chmod 600 secrets/smart-life.env` and enter your values with a local editor.

```dotenv
SMART_LIFE_MODE=login
SMART_LIFE_REGION=
SMART_LIFE_ACCOUNT=
SMART_LIFE_PASSWORD=
```

Use the English region name shown in Smart Life, such as `Poland`. Enter values directly after `=`. Quotes and `#` in a value are taken literally. The `secrets/` directory is excluded from Git and the controller build.

Once the installer opens the login form, run this on the Dell from the deployment directory.

```bash
sudo docker compose run --rm --build controller
```

Compose mounts the file read-only at `/run/secrets/smart_life_login`. In `login` mode, the controller selects the region and fills the account and password. Review the form and tap **Log In** in scrcpy.

To submit a form already filled in Smart Life, run `sudo docker compose run --rm controller --submit-login`. The command uses the current form, accepts the **User Agreement and Privacy Policy** if prompted, and waits for Home or a verification screen.

For a new account, set `SMART_LIFE_MODE=signup` and use your email address as `SMART_LIFE_ACCOUNT`. You can leave `SMART_LIFE_PASSWORD` blank for this step. The controller opens **Sign Up**, fills the region and email, checks the agreement box, and taps **Get** to request a verification code. If the code screen is already open, the controller leaves it there.

To continue, set `SMART_LIFE_PASSWORD` in the same secrets file. Replace `YOUR_CODE` with the six-digit code you received and run:

```bash
sudo docker compose run --rm controller --code YOUR_CODE
```

Smart Life submits the code when the field is filled. The controller fills **Set Password** from the secrets file and taps **Done**. On the privacy screen it declines optional sharing and personalized-ad consent, then taps **Go to App** and waits for Home. The same command can resume from **Set Password** or the privacy screen.

## Start Smart Life pairing from a Mac

Run the control script in the same Python environment.

```bash
.venv/bin/python smart_life_add_device.py
```

The default SSH alias is `dell5070`. Use `--host`, `--remote-dir`, or `--remote-port` for a different host, clone location, or ADB port. Pairing uses the signed-in Smart Life app with its UI in English.

The control script starts Android, opens the ADB tunnel, enables the physical Bluetooth bridge, and opens **Add Device**. Run the printed scrcpy command in another terminal to view the screen. Keep the control script open while pairing. Ctrl+C or the 15-minute timeout closes the bridge and turns Android Bluetooth off.

## Read Account and Security

With Smart Life signed in, run this from the deployment directory on the Dell.

```bash
sudo docker compose run --rm --build account-reader
```

The script prints JSON with `region`, `cloud_development_projects`, `third_party_voice_services`, `third_party_authorizations`, and `user_code`. The third-party entries include the status shown in the app when available. It reads the screens over ADB and returns to **Account and Security**. Keep the output private because it contains the User Code.

## Scan a QR image

With Smart Life signed in, run this on the Dell from the deployment directory.

```bash
base64 < qr.png | sudo docker compose run --rm -T --build qr-scanner
```

For an already encoded image, pipe its base64 text directly to `qr-scanner`. The script also accepts PNG or JPEG data URLs. It opens **Me → Scan → Album**, imports the image through a temporary Android album, and submits it to Smart Life. On first use it handles Smart Life’s camera and photo permission prompts. Review the scan result in scrcpy.

## List rooms from Smart Life

With ReDroid running and Smart Life signed in, run this from your Mac.

```bash
.venv/bin/python smart_life_add_device.py list-rooms
```

The command opens **Me → Home Management** and reads **Room Management** for each home. It prints a JSON array with `home` and `rooms` fields. It uses a temporary ADB tunnel and leaves Smart Life on Home Management.

The repo contains the Compose setup, Android configuration, and automation. Android app data and the Smart Life APK live outside the repo.

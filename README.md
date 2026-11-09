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

## View ReDroid from a Mac

With ADB and scrcpy installed and the Dell controller API running, run this from the Mac checkout.

```bash
./smart_life_add_device.py view --port 15569
```

The command opens one SSH connection with forwards for the controller API and ADB. It reads the API token over SSH, sends the wake request from your Mac, connects ADB, and launches scrcpy. Closing scrcpy closes both forwards and leaves Android awake for manual use. Use `/v1/android/idle` when you’re done. The `view` command works with the Mac’s system Python. If the Dell API listens on a custom port, pass it with `--remote-api-port`.

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

## Call the controller API

The `controller-api` service builds the `redroid-smart-life-controller:local` image and publishes the API on the Dell at `127.0.0.1:18765`. The Android app and its data stay in the ReDroid service. The API uses one worker and runs one Android operation at a time.

From the Dell deployment directory, create the API token once and start the services.

```bash
umask 077
mkdir -p secrets apks
test -f secrets/controller-api-token || python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > secrets/controller-api-token
touch secrets/smart-life.env
./start.sh
sudo install -m 0644 redroid-power-agent@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "redroid-power-agent@$(id -un).service"
sudo docker compose up -d --build controller-api
```

Put one Smart Life APK set in `apks/`, or point `SMART_LIFE_APK_DIR` in the Compose `.env` at your existing cache. The API mounts that directory and the login secrets read-only. Use `REDROID_API_PORT` in `.env` to change the loopback port.

From another machine, open an SSH tunnel with `ssh -N -L 18765:127.0.0.1:18765 dell5070`. The OpenAPI UI is at `http://127.0.0.1:18765/docs`. Send the token from `secrets/controller-api-token` as an `Authorization: Bearer …` header on each `/v1` request.

| Method and path | Operation |
| --- | --- |
| `GET /health` | Check that the API is ready |
| `GET /v1/automation/status` | Read the latest automation, its timestamps, and the idle deadline |
| `GET /v1/android/status` | Read Android power state, RAM, and swap without waking it |
| `GET /v1/android/scrcpy` | Return the one-command Mac launcher and the individual viewing commands |
| `POST /v1/android/wake` | Wake Android for direct ADB or scrcpy use |
| `POST /v1/android/idle` | Freeze Android and reclaim RAM toward 120 MiB |
| `POST /v1/install` | Install the mounted APK set and open Smart Life |
| `POST /v1/account/prepare` | Fill login or start sign-up according to `SMART_LIFE_MODE` |
| `POST /v1/login/submit` | Submit the current login form and handle the agreement |
| `POST /v1/signup/continue` | Accept JSON with a six-digit `code`, set the password, and finish onboarding |
| `GET /v1/rooms` | List rooms by home |
| `GET /v1/account-security` | Read region, authorizations, projects, and User Code |
| `POST /v1/scan/qr` | Accept a raw base64 image as `text/plain` and submit it to the scanner |
| `POST /v1/add-device` | Open Add Device with a running Intel Bluetooth bridge |

Run `./use-bridge11.sh` in a Dell terminal and keep that session open while pairing through `/v1/add-device`. If Smart Life asks for Location to discover nearby devices, the controller uses Android’s one-time permission. For idle periods, `sudo docker compose stop redroid` frees Android’s memory and preserves the data volume. Run `./start.sh` to boot it again. The API service can stay running and returns HTTP 503 for Android operations while ADB is unavailable.

### Keep Android state in swap while idle

Before an Android operation, the controller locks its automation file and checks whether ReDroid needs to wake. It records `starting`, `running`, and `finished` with UTC timestamps, duration, and outcome. The controller stores the latest job and its idle deadline in `/var/lib/redroid-controller/automation.json` on the `controller-state` volume. A concurrent operation receives HTTP 409.

The controller checks the file every five seconds. Five minutes after the latest job finishes, it asks the Dell power agent to freeze ReDroid and reclaim RAM toward 120 MiB. A new job resets the deadline. If an Intel bridge session is active, the controller checks again after the bridge ends. The deadline survives an API restart, and the API stays up while Android is frozen.

The power service runs as the Dell login user and accepts `wake`, `idle`, or `status` over a private Unix socket. The three power routes return `state`, `ram_mib`, and `swap_mib`. Use `/v1/android/wake` before direct ADB, scrcpy, or an Intel bridge session. An explicit wake clears the idle deadline until the next automation finishes or you call `/v1/android/idle`. The host helper `./android-idle.sh` accepts the same power commands on the Dell.

`GET /v1/android/scrcpy` returns the `launcher` to run from your Mac checkout, plus the individual viewing commands. Reading it leaves the Android power state and idle deadline as they are. Set `SCRCPY_SSH_HOST` and `SCRCPY_LOCAL_ADB_PORT` in the Compose `.env` to match your Mac’s SSH alias and local ADB tunnel port.

The repo contains the Compose setup, Android configuration, and automation. Android app data and the Smart Life APK live outside the repo.

#!/usr/bin/env python3
"""Open Smart Life pairing on the Dell 5070. Requires adb, SSH, and uiautomator2."""

import argparse
from contextlib import ExitStack
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time


PACKAGE = "com.tuya.smartlife"
LAUNCH_ACTIVITY = "com.smart.ThingSplashActivity"
HOME_ACTIVITY = "com.thingclips.smart.hometab.activity.FamilyHomeActivity"
ADD_ACTIVITY = "com.thingclips.smart.activator.home.entrance.activity.ActivatorHomeActivity"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def run(command, timeout=15, check=True):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or f"{command[0]} exited with status {result.returncode}")
    return result.stdout.strip()


def stop(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def start_ssh(stack, command, log):
    with log.open("wb") as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT)
    stack.callback(stop, process)
    return process


def wait_for(description, timeout, ready, process=None, log=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            break
        if ready():
            return
        time.sleep(1)
    details = log.read_text(errors="replace").strip() if log else ""
    raise RuntimeError(f"{description} did not become ready."
                       + (f"\n{details}" if details else ""))


def tunnel_listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def open_add_device(android, adb):
    def current_screen():
        current = android.app_current()
        return current["activity"] if current["package"] == PACKAGE else ""

    def visible(selector):
        # Smart Life's plus menu is a separate Android window.
        source = android.dump_hierarchy(max_depth=30, root_in_active=False)
        return android.xpath(selector, source=source).match()

    def click_visible(selector):
        control = visible(selector)
        if control is None:
            return False
        control.click()
        return True

    add_menu = '//*[@content-desc="More manipulate menu" and @resource-id=""]'
    add_device = '//*[@text="Add Device" and @resource-id="com.tuya.smartlife:id/tv_option"]'
    title = '//*[@text="Add Device" and @package="com.tuya.smartlife"]'

    for attempt in range(2):
        if current_screen() not in (HOME_ACTIVITY, ADD_ACTIVITY):
            run([*adb, "shell", "am", "start", "-n", f"{PACKAGE}/{LAUNCH_ACTIVITY}"])
        try:
            wait_for("Smart Life Home or Add Device", 35,
                     lambda: current_screen() in (HOME_ACTIVITY, ADD_ACTIVITY))
            if current_screen() == HOME_ACTIVITY:
                if visible(add_device) is None:
                    wait_for("Smart Life's + button", 25,
                             lambda: current_screen() == HOME_ACTIVITY and click_visible(add_menu))
                wait_for("Smart Life's Add Device menu item", 25,
                         lambda: current_screen() == HOME_ACTIVITY and click_visible(add_device))
            wait_for("Smart Life Add Device screen", 15,
                     lambda: current_screen() == ADD_ACTIVITY and visible(title) is not None)
            return
        except RuntimeError as exc:
            current = android.app_current()
            if attempt == 0 and current["package"] == "com.android.launcher3":
                print("Smart Life returned to the Android launcher. Retrying once.", flush=True)
                continue
            raise RuntimeError(
                f"{exc} Current activity: {current['activity']}. "
                "If Smart Life is waiting for sign-in or a prompt, resolve it and rerun."
            ) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="dell5070", help="SSH host or alias")
    parser.add_argument("--remote-dir", default="redroid-smart-life",
                        help="Clone location, absolute or relative to the SSH login directory")
    parser.add_argument("--remote-port", type=int, default=5555, help="ADB port on the Dell")
    parser.add_argument("--port", type=int, default=15559, help="Local ADB tunnel port")
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.port, args.remote_port)):
        parser.error("ports must be between 1 and 65535")

    import uiautomator2 as u2

    serial = f"127.0.0.1:{args.port}"
    adb = ["adb", "-s", serial]
    dell = [*SSH, "--", args.host]
    remote = f"cd {shlex.quote(args.remote_dir)} &&"
    with socket.socket() as port_check:
        try:
            port_check.bind(("127.0.0.1", args.port))
        except OSError as exc:
            raise RuntimeError(f"Local port {args.port} is unavailable. Use --port.") from exc

    with tempfile.TemporaryDirectory(prefix="smart-life-") as temporary, ExitStack() as stack:
        print(f"Starting ReDroid on {args.host}.", flush=True)
        run([*dell, f"{remote} ./start.sh"], timeout=150)
        tunnel_log = Path(temporary) / "tunnel.log"
        tunnel = start_ssh(
            stack, [*SSH, "-o", "ExitOnForwardFailure=yes", "-N", "-L",
                    f"{serial}:127.0.0.1:{args.remote_port}", "--", args.host], tunnel_log,
        )
        wait_for("ADB SSH tunnel", 12, lambda: tunnel_listening(args.port), tunnel, tunnel_log)
        run(["adb", "disconnect", serial], check=False)
        stack.callback(run, ["adb", "disconnect", serial], check=False)

        def android_booted():
            run(["adb", "connect", serial], timeout=8, check=False)
            return run([*adb, "shell", "getprop", "sys.boot_completed"], check=False) == "1"

        wait_for("Android boot over ADB", 100, android_booted, tunnel, tunnel_log)
        if run([*adb, "shell", "getprop", "ro.build.version.release"]) != "11":
            raise RuntimeError(f"Expected Android 11 at {serial}.")
        if "package:" not in run([*adb, "shell", "pm", "path", PACKAGE]):
            raise RuntimeError("Smart Life is missing from Android 11.")
        if "feature:android.hardware.bluetooth_le" not in run([*adb, "shell", "pm", "list", "features"]):
            raise RuntimeError("Android 11 does not advertise Bluetooth LE support.")
        print(f"Android 11 is ready at {serial}.", flush=True)

        bridge_log = Path(temporary) / "bridge.log"
        bridge = start_ssh(
            stack, [*SSH, "-tt", "--", args.host, f"{remote} ./use-bridge11.sh"],
            bridge_log,
        )
        wait_for("Intel Bluetooth bridge", 45,
                 lambda: "active on Android 11 for up to 15 minutes"
                 in bridge_log.read_text(errors="replace"), bridge, bridge_log)
        print("Intel Bluetooth bridge is active.", flush=True)

        run([*adb, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
        run([*adb, "shell", "wm", "dismiss-keyguard"])
        open_add_device(u2.connect(serial), adb)
        if bridge.poll() is not None or run([*adb, "get-state"]) != "device":
            raise RuntimeError("The Intel bridge or ADB disconnected during Smart Life startup.")

        print("Smart Life is on Add Device.", flush=True)
        print(f"View it with: scrcpy -s {serial} --no-audio", flush=True)
        print("Keep this script running for pairing. Ctrl+C ends the session.", flush=True)
        if bridge.wait():
            raise RuntimeError(bridge_log.read_text(errors="replace").strip())
        print("The Intel bridge session ended.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (ImportError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Smart Life setup failed: {error}", file=sys.stderr)
        sys.exit(1)

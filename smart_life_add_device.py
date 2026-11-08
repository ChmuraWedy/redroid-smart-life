#!/usr/bin/env python3
"""Install Smart Life, open pairing, or list rooms on the Dell 5070."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time


PACKAGE = "com.tuya.smartlife"
LAUNCH_ACTIVITY = "com.smart.ThingSplashActivity"
HOME_ACTIVITY = "com.thingclips.smart.hometab.activity.FamilyHomeActivity"
LOGIN_ACTIVITY = "com.thingclips.smart.login.ui.activity.LoginActivity"
ADD_ACTIVITY = "com.thingclips.smart.activator.home.entrance.activity.ActivatorHomeActivity"
HOME_MANAGEMENT_ACTIVITY = "com.thingclips.smart.family.main.view.activity.FamilyManageActivity"
HOME_SETTINGS_ACTIVITY = "com.thingclips.smart.family.main.view.activity.FamilySettingActivity"
ROOM_MANAGEMENT_ACTIVITY = "com.thingclips.smart.family.roomwithtag.RoomWithTagManagerActivity"
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


def current_screen(android):
    current = android.app_current()
    return current["activity"] if current["package"] == PACKAGE else ""


def connect_android(serial):
    import uiautomator2 as u2

    def android_ready():
        run(["adb", "connect", serial], timeout=8, check=False)
        return run(["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"], check=False) == "1"

    wait_for("ReDroid over ADB", 30, android_ready)
    return u2.connect(serial)


def open_add_device(android, adb):
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
        if current_screen(android) not in (HOME_ACTIVITY, ADD_ACTIVITY):
            run([*adb, "shell", "am", "start", "-n", f"{PACKAGE}/{LAUNCH_ACTIVITY}"])
        try:
            wait_for("Smart Life Home or Add Device", 35,
                     lambda: current_screen(android) in (HOME_ACTIVITY, ADD_ACTIVITY))
            if current_screen(android) == HOME_ACTIVITY:
                if visible(add_device) is None:
                    wait_for("Smart Life's + button", 25,
                             lambda: current_screen(android) == HOME_ACTIVITY and click_visible(add_menu))
                wait_for("Smart Life's Add Device menu item", 25,
                         lambda: current_screen(android) == HOME_ACTIVITY and click_visible(add_device))
            wait_for("Smart Life Add Device screen", 15,
                     lambda: current_screen(android) == ADD_ACTIVITY and visible(title) is not None)
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


def smart_life_id(name):
    return f"{PACKAGE}:id/{name}"


def read_list_names(android, list_id, name_id):
    scroller = android(resourceId=smart_life_id(list_id), scrollable=True)
    if scroller.exists:
        scroller.scroll.toBeginning(steps=40, max_swipes=100)

    selector = f'//*[@resource-id="{smart_life_id(list_id)}"]'
    selector += f'//*[@resource-id="{smart_life_id(name_id)}" and @text!=""]'
    names = {}
    at_end = False
    for _ in range(101):
        source = android.dump_hierarchy(max_depth=40, root_in_active=True)
        for row in android.xpath(selector, source=source).all():
            names[row.text] = None
        if at_end or not scroller.exists:
            return list(names)
        # A scroll can reach the end and report False while exposing new rows.
        at_end = not scroller.scroll.forward(steps=40)
    raise RuntimeError(f"Could not reach the end of Smart Life's {list_id} list.")


def list_rooms(android, adb):
    def control(name, **attributes):
        return android(resourceId=smart_life_id(name), **attributes)

    def wait_for_title(title):
        wait_for(f"Smart Life {title}", 25, control("toolbar_title", text=title).exists)

    def click_control(name, description, **attributes):
        if not control(name, **attributes).click_exists(timeout=15):
            raise RuntimeError(f"Could not find {description} in Smart Life.")

    screens = (HOME_ACTIVITY, ADD_ACTIVITY, HOME_MANAGEMENT_ACTIVITY,
               HOME_SETTINGS_ACTIVITY, ROOM_MANAGEMENT_ACTIVITY)
    if current_screen(android) not in screens:
        run([*adb, "shell", "am", "start", "-n", f"{PACKAGE}/{LAUNCH_ACTIVITY}"])
        wait_for("Smart Life Home or Home Management", 35, lambda: current_screen(android) in screens)

    screen = current_screen(android)
    if screen == ADD_ACTIVITY:
        android.press("back")
        wait_for("Smart Life Home", 15, lambda: current_screen(android) == HOME_ACTIVITY)
        screen = HOME_ACTIVITY
    if screen == ROOM_MANAGEMENT_ACTIVITY:
        android.press("back")
        wait_for_title("Home Settings")
        android.press("back")
    elif screen == HOME_SETTINGS_ACTIVITY:
        android.press("back")
    elif screen == HOME_ACTIVITY:
        click_control("title", "the Me tab", text="Me")
        click_control("content", "Home Management", text="Home Management")

    wait_for_title("Home Management")
    wait_for("Smart Life home list", 25, control("srv_family_list").exists)
    homes = read_list_names(android, "srv_family_list", "tv_family")
    result = []
    for home in homes:
        home_row = control("tv_family", text=home)
        if not home_row.exists:
            control("srv_family_list", scrollable=True).scroll.to(
                resourceId=smart_life_id("tv_family"), text=home,
            )
        click_control("tv_family", f"home {home!r}", text=home)
        wait_for_title("Home Settings")

        room_settings = f'//*[@resource-id="{smart_life_id("aiv_room_manage")}"]'
        count_label = f'{room_settings}//*[@resource-id="{smart_life_id("tv_short_subtitle")}"]'
        count_node = android.xpath(count_label).match()
        count = re.match(r"(\d+)\s+Room", count_node.text or "") if count_node is not None else None
        click_control("aiv_room_manage", "Room Management")
        wait_for_title("Room Management")
        wait_for("Smart Life room list", 25, control("mRvcList").exists)
        if count is not None and int(count[1]) > 0:
            wait_for("Smart Life room names", 25, control("tvRoomName").exists)
        rooms = read_list_names(android, "mRvcList", "tvRoomName")
        if count is not None and len(rooms) != int(count[1]):
            raise RuntimeError(f"Smart Life reported {count[1]} rooms for {home!r}, "
                               f"but the room list contained {len(rooms)}.")
        result.append({"home": home, "rooms": rooms})

        android.press("back")
        wait_for_title("Home Settings")
        android.press("back")
        wait_for_title("Home Management")
    return result


def install_app(adb, apks):
    install = "install" if len(apks) == 1 else "install-multiple"
    print("Installing Smart Life.", flush=True)
    run([*adb, install, "-r", *apks], timeout=300)
    if "package:" not in run([*adb, "shell", "pm", "path", PACKAGE]):
        raise RuntimeError("The supplied APKs did not install Smart Life.")

    run([*adb, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
    run([*adb, "shell", "wm", "dismiss-keyguard"])
    launch = run([*adb, "shell", "am", "start", "-W", "-n", f"{PACKAGE}/{LAUNCH_ACTIVITY}"], timeout=45)
    if "Status: ok" not in launch:
        raise RuntimeError(f"Smart Life did not launch.\n{launch}")
    print("Smart Life is installed and open.", flush=True)


def accept_user_agreement(android):
    title = android(packageName=PACKAGE, resourceId=smart_life_id("titleView"),
                    text="User Agreement and Privacy Policy")
    if not title.wait(timeout=15):
        return

    agree = android(packageName=PACKAGE, resourceId=smart_life_id("positiveButton"),
                    text="Agree")
    if not agree.click_exists(timeout=5) or not title.wait_gone(timeout=15):
        raise RuntimeError("Could not accept the Smart Life User Agreement and Privacy Policy.")
    print("Accepted the Smart Life User Agreement and Privacy Policy.", flush=True)


def decline_tracking(android):
    decline = android(packageName=PACKAGE, resourceId=smart_life_id("tv_cancel"),
                      text="Ask the app not to require tracking")
    if not decline.click_exists(timeout=10):
        return
    if not decline.wait_gone(timeout=15):
        raise RuntimeError("The Smart Life tracking prompt stayed open after declining.")
    print("Declined optional Smart Life tracking.", flush=True)


def dismiss_root_warning(android):
    title = android(packageName=PACKAGE, resourceId=smart_life_id("tv_title"),
                    text="Security reminder")
    if not title.wait(timeout=10):
        return

    reminder = android(packageName=PACKAGE, resourceId=smart_life_id("reminder_check"),
                       text="Do not remind")
    if not reminder.wait(timeout=5):
        raise RuntimeError("Could not find the Smart Life Do not remind checkbox.")
    if not reminder.info["checked"]:
        reminder.click()
        wait_for("Smart Life Do not remind checkbox to be checked", 5,
                 lambda: reminder.info["checked"])

    continue_button = android(packageName=PACKAGE, resourceId=smart_life_id("tv_confirm"),
                              text="continue to use")
    if not continue_button.click_exists(timeout=5) or not title.wait_gone(timeout=15):
        raise RuntimeError("Could not continue past the Smart Life rooted-device reminder.")
    print("Continued past the Smart Life rooted-device reminder with Do not remind checked.",
          flush=True)


def open_login(android):
    if current_screen(android) not in (LOGIN_ACTIVITY, HOME_ACTIVITY):
        login = android(packageName=PACKAGE, resourceId=smart_life_id("btn_login"), text="Log In")
        login.click_exists(timeout=15)
    wait_for("Smart Life login or signed-in Home screen", 15,
             lambda: current_screen(android) in (LOGIN_ACTIVITY, HOME_ACTIVITY))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="add-device",
                        choices=("add-device", "list-rooms", "install-app"),
                        help="open Add Device (default), list rooms as JSON, or install Smart Life")
    parser.add_argument("--apk", nargs="+", type=Path,
                        help="for install-app, a standalone APK or the base APK and all splits")
    parser.add_argument("--view", action="store_true", help="open scrcpy after install-app")
    parser.add_argument("--host", default="dell5070", help="SSH host or alias")
    parser.add_argument("--remote-dir", default="redroid-smart-life",
                        help="Clone location, absolute or relative to the SSH login directory")
    parser.add_argument("--remote-port", type=int, default=5555, help="ADB port on the Dell")
    parser.add_argument("--port", type=int, default=15559, help="Local ADB tunnel port")
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.port, args.remote_port)):
        parser.error("ports must be between 1 and 65535")
    if args.command == "install-app":
        if not args.apk or any(path.suffix.lower() != ".apk" or not path.is_file() for path in args.apk):
            parser.error("install-app requires --apk with existing .apk files")
    elif args.apk or args.view:
        parser.error("--apk and --view are for install-app")

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
        if args.command != "list-rooms":
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
        if args.command == "install-app":
            install_app(adb, args.apk)
            android = u2.connect(serial)
            accept_user_agreement(android)
            decline_tracking(android)
            dismiss_root_warning(android)
            open_login(android)
            if args.view and subprocess.call(["scrcpy", "-s", serial, "--no-audio"]):
                raise RuntimeError("scrcpy exited with an error.")
            return
        if "package:" not in run([*adb, "shell", "pm", "path", PACKAGE]):
            raise RuntimeError("Smart Life is missing from Android 11.")
        if args.command == "list-rooms":
            run([*adb, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
            run([*adb, "shell", "wm", "dismiss-keyguard"])
            print(json.dumps(list_rooms(u2.connect(serial), adb), ensure_ascii=False, indent=2))
            return
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

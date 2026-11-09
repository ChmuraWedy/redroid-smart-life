#!/usr/bin/env python3
"""Send a base64 PNG or JPEG to Smart Life's QR scanner through Album."""

import argparse
import base64
import binascii
from io import BytesIO
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from PIL import Image

from smart_life_account import ACCOUNT_ACTIVITY, SETTINGS_ACTIVITY, click
from smart_life_add_device import (
    HOME_ACTIVITY, LAUNCH_ACTIVITY, PACKAGE, allow_permission, connect_android, current_screen, run,
    smart_life_id, wait_for,
)


SCANNER_ACTIVITY = "com.thingclips.smart.mlkit.qrcode.activity.CaptureActivity"
ALBUM_ACTIVITY = "com.thingclips.smart.gallery.GalleryPickerAct"
MEDIA_URI = "content://media/external/images/media"
MAX_INPUT_BYTES = 12 * 1024 * 1024


def decode_image(source):
    raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise RuntimeError("The base64 image exceeds the 12 MiB input limit.")
    encoded = b"".join(raw.split())
    if encoded.startswith(b"data:"):
        header, _, encoded = encoded.partition(b",")
        if header not in (b"data:image/png;base64", b"data:image/jpeg;base64"):
            raise RuntimeError("Use a base64 PNG or JPEG image.")
    try:
        content = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(content), formats=("PNG", "JPEG")) as image:
            if image.width * image.height > 16_000_000:
                raise RuntimeError("The QR image exceeds 16 megapixels.")
            extension = ".png" if image.format == "PNG" else ".jpg"
            image.verify()
    except (binascii.Error, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise RuntimeError("Expected a valid base64 PNG or JPEG image on stdin.") from None
    return content, extension


def scanner_ready(android):
    allow_permission(android, "Allow SmartLife to take pictures and record video?", "permission_allow_one_time_button")
    return current_screen(android) == SCANNER_ACTIVITY


def album_ready(android):
    allow_permission(android, "Allow SmartLife to access photos and media on your device?", "permission_allow_button")
    return current_screen(android) == ALBUM_ACTIVITY


def open_scanner(android, adb):
    if scanner_ready(android):
        return

    screens = (HOME_ACTIVITY, SETTINGS_ACTIVITY, ACCOUNT_ACTIVITY, ALBUM_ACTIVITY, SCANNER_ACTIVITY)
    if current_screen(android) not in screens:
        run([*adb, "shell", "am", "start", "-n", f"{PACKAGE}/{LAUNCH_ACTIVITY}"])
        wait_for("signed-in Smart Life", 25, lambda: current_screen(android) in screens or scanner_ready(android))

    for source, target in ((ACCOUNT_ACTIVITY, SETTINGS_ACTIVITY), (SETTINGS_ACTIVITY, HOME_ACTIVITY)):
        if current_screen(android) == source:
            android.press("back")
            wait_for("previous Smart Life screen", 10, lambda: current_screen(android) == target)

    for _ in range(2):
        if current_screen(android) != ALBUM_ACTIVITY:
            break
        android.press("back")
        time.sleep(0.5)

    if current_screen(android) == HOME_ACTIVITY:
        click(android, "title", text="Me")
        click(android, "tv_menu_scan", description="Scan")
    wait_for("Smart Life QR scanner", 15, lambda: scanner_ready(android))


def scan_album(android, album):
    click(android, "tv_content", text="Album")
    wait_for("Smart Life Album picker", 15, lambda: album_ready(android))
    click(android, "more_album_ly")
    click(android, "album_name", text=album)
    folder = android(packageName=PACKAGE, resourceId=smart_life_id("more_album"), text=album)
    thumbnail = android(packageName=PACKAGE, resourceId=smart_life_id("img_check"))
    wait_for("the staged QR image in its album", 10, lambda: folder.exists and thumbnail.count == 1)
    if not thumbnail.info["checked"]:
        click(android, "img_check")
    wait_for("the QR image selection", 5, lambda: thumbnail.info["checked"])
    click(android, "pick_confirm_btn", textStartsWith="Confirm")
    wait_for("Smart Life to accept the image from Album", 15,
             lambda: android.app_current()["activity"] != ALBUM_ACTIVITY)
    # The scanner reads the image after the picker returns.
    time.sleep(3)


def stage_and_scan(android, adb, content, extension):
    identifier = uuid4().hex[:12]
    album = f"SmartLifeQR{identifier}"
    name = f"smart-life-qr-{identifier}{extension}"
    folder = f"/sdcard/Pictures/{album}"
    remote = f"{folder}/{name}"
    predicate = f"_display_name='{name}'"
    query = shlex.join(["content", "query", "--uri", MEDIA_URI, "--projection", "_id", "--where", predicate])
    delete = shlex.join(["content", "delete", "--uri", MEDIA_URI, "--where", predicate])
    with tempfile.TemporaryDirectory(prefix="smart-life-qr-") as temporary:
        local = Path(temporary) / name
        local.write_bytes(content)
        run([*adb, "shell", "mkdir", "-p", folder])
        try:
            run([*adb, "push", str(local), remote], timeout=30)
            run([*adb, "shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                 "-d", f"file://{remote}"])
            wait_for("Android to index the QR image", 15, lambda: "Row:" in run([*adb, "shell", query]))
            scan_album(android, album)
        finally:
            run([*adb, "shell", delete], check=False)
            run([*adb, "shell", "rm", "-f", remote], check=False)
            run([*adb, "shell", "rmdir", folder], check=False)


def scan_qr(android, adb, content, extension):
    try:
        open_scanner(android, adb)
    except RuntimeError:
        if android.app_current()["package"] != "com.android.launcher3":
            raise
        open_scanner(android, adb)
    stage_and_scan(android, adb, content, extension)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb-serial", default="redroid:5555", help="Android ADB host and port")
    args = parser.parse_args()
    content, extension = decode_image(sys.stdin.buffer)
    adb = ["adb", "-s", args.adb_serial]
    scan_qr(connect_android(args.adb_serial), adb, content, extension)
    print("The QR image was submitted to Smart Life's scanner. Check the result in scrcpy.")


if __name__ == "__main__":
    try:
        main()
    except (ImportError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Smart Life QR import failed: {error}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Read Smart Life Account and Security as JSON."""

import argparse
import json
import subprocess
import sys
from xml.etree import ElementTree

from smart_life_add_device import (
    HOME_ACTIVITY, LAUNCH_ACTIVITY, PACKAGE, connect_android, current_screen, run, smart_life_id, wait_for,
)


SETTINGS_ACTIVITY = "com.thingclips.smart.personal.setting.plug.activity.SettingActivity"
ACCOUNT_ACTIVITY = "com.thingclips.smart.personal.account.security.plug.activity.AccountSecurityActivity"
PROJECT_TITLE = "Cloud Development Authorization Management"
SECTION_KEYS = {
    "Third-Party Voice Services Authorization Management": "third_party_voice_services",
    "Third-Party Authorization Management": "third_party_authorizations",
}


def read_text(node, resource):
    return next((child.get("text", "") for child in node.iter("node")
                 if child.get("resource-id") == smart_life_id(resource)), "")


def pages(android, scroller):
    at_end = False
    for _ in range(51):
        yield ElementTree.fromstring(android.dump_hierarchy(max_depth=55, root_in_active=True))
        if at_end or not scroller.exists:
            return
        at_end = not scroller.scroll.forward(steps=40)
    raise RuntimeError("Could not reach the end of the Smart Life list.")


def click(android, resource, **selectors):
    if not android(packageName=PACKAGE, resourceId=smart_life_id(resource), **selectors).click_exists(timeout=8):
        raise RuntimeError(f"Could not find {selectors.get('text', resource)} in Smart Life.")


def open_account_security(android, serial):
    project = android(packageName=PACKAGE, resourceId=smart_life_id("gzl_navigationbar_tv_title"), text=PROJECT_TITLE)
    if project.exists:
        click(android, "gzl_capsule_btn_exit")
        wait_for("Account and Security", 12, lambda: current_screen(android) == ACCOUNT_ACTIVITY)
        return

    screens = (HOME_ACTIVITY, SETTINGS_ACTIVITY, ACCOUNT_ACTIVITY)
    if current_screen(android) not in screens:
        run(["adb", "-s", serial, "shell", "am", "start", "-n", f"{PACKAGE}/{LAUNCH_ACTIVITY}"])
        wait_for("signed-in Smart Life", 25, lambda: current_screen(android) in screens)
    if current_screen(android) == HOME_ACTIVITY:
        click(android, "title", text="Me")
        click(android, "tv_menu_setting", description="Settings")
        wait_for("Smart Life Settings", 12, lambda: current_screen(android) == SETTINGS_ACTIVITY)
    if current_screen(android) == SETTINGS_ACTIVITY:
        click(android, "menu_list_title", text="Account and Security")
    wait_for("Account and Security", 12, lambda: current_screen(android) == ACCOUNT_ACTIVITY)


def read_account_rows(android, scroller):
    rows = {}
    sections = {key: {} for key in SECTION_KEYS.values()}
    if scroller.exists:
        scroller.scroll.toBeginning(steps=40, max_swipes=50)
    for page in pages(android, scroller):
        recycler = next((node for node in page.iter("node")
                         if node.get("resource-id") == smart_life_id("recycler_view")), None)
        if recycler is None:
            raise RuntimeError("Could not read the Account and Security list.")
        for group in recycler:
            section = SECTION_KEYS.get(read_text(group, "tv_empty_content"))
            if section:
                for item in group.iter("node"):
                    if item.get("resource-id") == smart_life_id("fl_item"):
                        name = read_text(item, "menu_list_title")
                        if name:
                            sections[section][name] = read_text(item, "menu_list_sub_title") or None
            elif group.get("resource-id") == smart_life_id("fl_item"):
                title = read_text(group, "menu_list_title")
                value = read_text(group, "menu_list_sub_title")
                if title in ("Region", "User Code") and value:
                    rows[title] = value

    for required in ("Region", "User Code"):
        if required not in rows:
            raise RuntimeError(f"Smart Life did not show a value for {required}.")
    return {
        "region": rows["Region"],
        **{section: [{"name": name, "status": status} for name, status in items.items()]
           for section, items in sections.items()},
        "user_code": rows["User Code"],
    }


def read_cloud_projects(android, scroller):
    project_row = android(packageName=PACKAGE, resourceId=smart_life_id("menu_list_title"),
                          text="Cloud Development Project")
    if not project_row.exists and scroller.exists:
        scroller.scroll.to(resourceId=smart_life_id("menu_list_title"), text="Cloud Development Project")
    click(android, "menu_list_title", text="Cloud Development Project")
    heading = android(packageName=PACKAGE, className="android.widget.TextView", text="Account Data Authorization")
    wait_for(PROJECT_TITLE, 40, heading.exists)

    projects = {}
    web_list = android(packageName=PACKAGE, className="android.webkit.WebView", scrollable=True)
    try:
        for page in pages(android, web_list):
            for row in page.iter("node"):
                if not any(child.get("text") == "Revoke" for child in row):
                    continue
                name = next((child.get("text") for child in row.iter("node")
                             if child.get("text") not in (None, "", "Revoke")), None)
                if name:
                    projects[name] = None
    finally:
        click(android, "gzl_capsule_btn_exit")
        wait_for("Account and Security", 12, lambda: current_screen(android) == ACCOUNT_ACTIVITY)
    return list(projects)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb-serial", default="redroid:5555", help="Android ADB host and port")
    args = parser.parse_args()
    android = connect_android(args.adb_serial)
    open_account_security(android, args.adb_serial)
    scroller = android(packageName=PACKAGE, resourceId=smart_life_id("recycler_view"), scrollable=True)
    result = read_account_rows(android, scroller)
    result["cloud_development_projects"] = read_cloud_projects(android, scroller)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ImportError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Smart Life account read failed: {error}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Prepare Smart Life login or continue sign-up with an email code."""

import argparse
from pathlib import Path
import re
import subprocess
import sys
import time

from smart_life_add_device import (
    HOME_ACTIVITY, LOGIN_ACTIVITY, PACKAGE, accept_user_agreement, connect_android, current_screen, open_login,
    smart_life_id, wait_for,
)


ACCOUNT_KEYS = ("SMART_LIFE_REGION", "SMART_LIFE_ACCOUNT", "SMART_LIFE_PASSWORD", "SMART_LIFE_MODE")
SIGNUP_ACTIVITY = "com.thingclips.smart.login.ui.activity.RegisterActivity"
VERIFICATION_ACTIVITY = "com.thingclips.smart.login.ui.activity.VerifyAccountActivity"
SET_PASSWORD_ACTIVITY = "com.thingclips.smart.login.ui.activity.SetPasswordActivity"
PRIVACY_ACTIVITY = "com.thingclips.smart.privacy.setting.ui.PrivacyAdvanceAuthActivity"
POST_SIGNUP_ACTIVITIES = (PRIVACY_ACTIVITY, HOME_ACTIVITY)
REGION_PICKER_ACTIVITY = "com.thingclips.smart.country.select.activity.CountryListActivity"


def read_settings(path):
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeError):
        raise RuntimeError(f"Could not read the Smart Life secrets file at {path}.") from None

    values = {}
    for line in lines:
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key in ACCOUNT_KEYS:
            values[key] = value
    return values


def read_login(path):
    values = read_settings(path)
    for key in ("SMART_LIFE_REGION", "SMART_LIFE_ACCOUNT"):
        values[key] = values.get(key, "").strip()
    mode = values.get("SMART_LIFE_MODE", "").strip() or "login"
    if mode not in ("login", "signup"):
        raise RuntimeError("SMART_LIFE_MODE must be login or signup.")
    values["SMART_LIFE_MODE"] = mode
    required = ACCOUNT_KEYS[:3] if mode == "login" else ACCOUNT_KEYS[:2]
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise RuntimeError(f"Missing account settings in the secrets file: {', '.join(missing)}.")
    return values


def select_region(android, name):
    region = android(packageName=PACKAGE, resourceId=smart_life_id("tv_region"))
    if region.get_text(timeout=5) == name:
        return

    form = current_screen(android)
    picker = android(packageName=PACKAGE, resourceId=smart_life_id("region_view"))
    if not picker.click_exists(timeout=5):
        raise RuntimeError("Could not open the Smart Life region picker.")
    try:
        search_button = android(packageName=PACKAGE, resourceId=smart_life_id("tv_search_box_collapsed_hint"))
        if not search_button.click_exists(timeout=5):
            raise RuntimeError("Could not open the Smart Life region search.")
        search = android(packageName=PACKAGE, resourceId=smart_life_id("et_search_expanded_edit_text"))
        search.set_text(name, timeout=5)
        result = android(packageName=PACKAGE, resourceId=smart_life_id("tv_name"), text=name)
        if not result.click_exists(timeout=10):
            raise RuntimeError("The configured region did not match a Smart Life region name.")
        wait_for("Smart Life account form with the selected region", 10,
                 lambda: current_screen(android) == form and region.get_text(timeout=2) == name)
    finally:
        # Back can dismiss the keyboard or collapse search before closing the picker.
        for _ in range(3):
            if current_screen(android) != REGION_PICKER_ACTIVITY:
                break
            android.press("back")
            time.sleep(0.5)


def open_account_form(android, signup):
    target = SIGNUP_ACTIVITY if signup else LOGIN_ACTIVITY
    screen = current_screen(android)
    if screen in (target, HOME_ACTIVITY):
        return
    if screen in (LOGIN_ACTIVITY, SIGNUP_ACTIVITY):
        back = android(packageName=PACKAGE, className="android.widget.ImageButton", description="Back")
        if not back.click_exists(timeout=5):
            raise RuntimeError("Could not return to the Smart Life welcome screen.")

    if signup:
        register = android(packageName=PACKAGE, resourceId=smart_life_id("btn_register"), text="Sign Up")
        register.click_exists(timeout=15)
        wait_for("Smart Life sign-up or signed-in Home screen", 15,
                 lambda: current_screen(android) in (SIGNUP_ACTIVITY, HOME_ACTIVITY))
    else:
        open_login(android)


def request_signup_code(android):
    if current_screen(android) != SIGNUP_ACTIVITY:
        raise RuntimeError("Smart Life is not on the sign-up form.")
    email = android(packageName=PACKAGE, resourceId=smart_life_id("et_text"),
                    className="android.widget.EditText")
    if not email.get_text(timeout=5):
        raise RuntimeError("Enter an email address before requesting a verification code.")

    agreement = android(packageName=PACKAGE, resourceId=smart_life_id("cb_agreement"))
    if not agreement.wait(timeout=5):
        raise RuntimeError("Could not find the Smart Life sign-up agreement checkbox.")
    if not agreement.info["checked"]:
        agreement.click()
        wait_for("Smart Life sign-up agreement to be checked", 5, lambda: agreement.info["checked"])

    button = android(packageName=PACKAGE, resourceId=smart_life_id("ll_btn"), enabled=True, clickable=True)
    get_code = button.child(resourceId=smart_life_id("tv_text"), text="Get")
    if not get_code.click_exists(timeout=5):
        raise RuntimeError("Could not tap Get on the Smart Life sign-up form.")
    wait_for("Smart Life verification code screen", 30,
             lambda: current_screen(android) == VERIFICATION_ACTIVITY)
    print("Smart Life is waiting for the email verification code.")


def fill_account_form(android, values):
    signup = values["SMART_LIFE_MODE"] == "signup"
    if signup and current_screen(android) == VERIFICATION_ACTIVITY:
        print("Smart Life is already waiting for the email verification code.")
        return
    open_account_form(android, signup)
    if current_screen(android) == HOME_ACTIVITY:
        print("Smart Life is already signed in.")
        return

    select_region(android, values["SMART_LIFE_REGION"])
    field = smart_life_id("et_text")
    try:
        if signup:
            email = android(packageName=PACKAGE, resourceId=field, className="android.widget.EditText")
            email.set_text(values["SMART_LIFE_ACCOUNT"], timeout=5)
        else:
            account = android(packageName=PACKAGE, resourceId=smart_life_id("account_view"))
            password = android(packageName=PACKAGE, resourceId=smart_life_id("password_view"))
            account.child(resourceId=field).set_text(values["SMART_LIFE_ACCOUNT"], timeout=5)
            password.child(resourceId=field).set_text(values["SMART_LIFE_PASSWORD"], timeout=5)
    except Exception:
        raise RuntimeError("Could not populate the Smart Life account fields.") from None
    if signup:
        request_signup_code(android)
    else:
        print("Smart Life login form filled. Submit it in the app when ready.")


def finish_privacy_onboarding(android):
    def control(name, **selectors):
        return android(packageName=PACKAGE, resourceId=smart_life_id(name), **selectors)

    screen = current_screen(android)
    if screen == HOME_ACTIVITY:
        print("Smart Life is already on Home.")
        return
    if screen != PRIVACY_ACTIVITY:
        raise RuntimeError("Smart Life is no longer on the privacy screen.")

    privacy_list = control("advanced_auth_rv", scrollable=True)
    ads_off = control("ad_privacy_desc", textStartsWith="Personalized ads are off.")
    decline = android(packageName=PACKAGE, className="android.widget.Button", text="Do not consent")
    if not decline.exists:
        if privacy_list.exists:
            privacy_list.scroll.toEnd(steps=35, max_swipes=5)
        confirm = control("ad_privacy_action_btn", text="Confirm")
        if not ads_off.exists and confirm.click_exists(timeout=2):
            if not decline.wait(timeout=30):
                raise RuntimeError("Smart Life did not show the personalized-ad opt-out.")
    if decline.click_exists() and not decline.wait_gone(timeout=15):
        raise RuntimeError("The personalized-ad consent dialog stayed open after declining.")
    if control("ad_privacy_title").exists and not ads_off.wait(timeout=10):
        raise RuntimeError("Smart Life did not confirm that personalized ads are off.")

    if privacy_list.exists:
        privacy_list.scroll.toBeginning(steps=35, max_swipes=5)
    sharing = control("auth_item_title", text="Service Maintenance & Improvement Activity")
    recommendations = "Personalized Recommendations"
    if not sharing.wait(timeout=5) or not control("auth_item_title", text=recommendations).wait(timeout=5):
        raise RuntimeError("Could not verify Smart Life's optional sharing choices.")
    # Recommendations are disabled when the service-improvement choice is off.
    enabled_recommendations = control("auth_item_card", clickable=True).child(
        resourceId=smart_life_id("auth_item_title"), text=recommendations,
    )
    if enabled_recommendations.exists:
        if not sharing.click_exists(timeout=5) or not enabled_recommendations.wait_gone(timeout=5):
            raise RuntimeError("Could not turn off Smart Life's optional service-improvement sharing.")

    enter = control("advanced_auth_button_enter", text="Go to App")
    if not enter.click_exists(timeout=5):
        raise RuntimeError("Could not continue from Smart Life's privacy settings.")
    wait_for("Smart Life Home", 30, lambda: current_screen(android) == HOME_ACTIVITY)
    print("Smart Life is on Home with optional sharing and personalized ads declined.")


def continue_account(android, code, secrets_file):
    screen = current_screen(android)
    if screen in POST_SIGNUP_ACTIVITIES:
        finish_privacy_onboarding(android)
        return
    if screen not in (VERIFICATION_ACTIVITY, SET_PASSWORD_ACTIVITY):
        raise RuntimeError("Open Smart Life's verification, password setup, or privacy screen first.")
    password = read_settings(secrets_file).get("SMART_LIFE_PASSWORD")
    if not password:
        raise RuntimeError("Set SMART_LIFE_PASSWORD in the Smart Life secrets file before continuing.")

    if screen == VERIFICATION_ACTIVITY:
        code_field = android(packageName=PACKAGE, resourceId=smart_life_id("et"),
                             className="android.widget.EditText")
        try:
            code_field.set_text(code, timeout=5)
        except Exception:
            raise RuntimeError("Could not enter the Smart Life verification code.") from None
        wait_for("Smart Life password setup or post-sign-up screen", 20,
                 lambda: current_screen(android) in (SET_PASSWORD_ACTIVITY, *POST_SIGNUP_ACTIVITIES))
        screen = current_screen(android)
        if screen in POST_SIGNUP_ACTIVITIES:
            finish_privacy_onboarding(android)
            return
        if screen != SET_PASSWORD_ACTIVITY:
            raise RuntimeError("Smart Life is no longer on Set Password.")

    field = android(packageName=PACKAGE, resourceId=smart_life_id("et_text"),
                    className="android.widget.EditText")
    try:
        field.set_text(password, timeout=5)
    except Exception:
        raise RuntimeError("Could not fill the Smart Life password setup field.") from None

    button = android(packageName=PACKAGE, resourceId=smart_life_id("ll_btn"), enabled=True, clickable=True)
    done = button.child(resourceId=smart_life_id("tv_text"), text="Done")
    if not done.click_exists(timeout=5):
        raise RuntimeError("Smart Life did not enable Done. Check the password requirements in the app.")
    wait_for("Smart Life to finish password setup", 30,
             lambda: current_screen(android) in POST_SIGNUP_ACTIVITIES)
    finish_privacy_onboarding(android)


def submit_login(android):
    screen = current_screen(android)
    next_screens = (HOME_ACTIVITY, VERIFICATION_ACTIVITY, PRIVACY_ACTIVITY)
    if screen not in (LOGIN_ACTIVITY, *next_screens):
        raise RuntimeError("Open the filled Smart Life login form first.")

    agreement = android(packageName=PACKAGE, resourceId=smart_life_id("titleView"),
                        text="User Agreement and Privacy Policy")
    if screen == LOGIN_ACTIVITY and not agreement.exists:
        button = android(packageName=PACKAGE, resourceId=smart_life_id("ll_btn"), enabled=True, clickable=True)
        login = button.child(resourceId=smart_life_id("tv_text"), text="Log In")
        if not login.exists and "mInputShown=true" in android.shell(["dumpsys", "input_method"]).output:
            android.press("back")
        if not login.click_exists(timeout=5):
            raise RuntimeError("Smart Life's Log In button is unavailable. Check the fields in the app.")
        wait_for("Smart Life login result", 25,
                 lambda: current_screen(android) in next_screens or agreement.exists)
        screen = current_screen(android)

    if screen == LOGIN_ACTIVITY and agreement.exists:
        accept_user_agreement(android)
        wait_for("Smart Life to finish signing in", 25, lambda: current_screen(android) in next_screens)
        screen = current_screen(android)

    if screen == HOME_ACTIVITY:
        print("Smart Life is signed in and on Home.")
    elif screen == VERIFICATION_ACTIVITY:
        print("Smart Life is waiting for a verification code.")
    elif screen == PRIVACY_ACTIVITY:
        finish_privacy_onboarding(android)
    else:
        raise RuntimeError("Smart Life stayed on the login form. Check the app for a sign-in message.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb-serial", default="redroid:5555", help="Android ADB host and port")
    parser.add_argument("--secrets-file", type=Path, default=Path("/run/secrets/smart_life_login"))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--code", help="six-digit email code for the open verification screen")
    action.add_argument("--submit-login", action="store_true", help="submit the login form already filled in Smart Life")
    args = parser.parse_args()
    if args.code is not None and not re.fullmatch(r"[0-9]{6}", args.code):
        parser.error("--code must contain exactly six digits")
    values = read_login(args.secrets_file) if args.code is None and not args.submit_login else None

    android = connect_android(args.adb_serial)
    if args.code is not None:
        continue_account(android, args.code, args.secrets_file)
    elif args.submit_login:
        submit_login(android)
    else:
        fill_account_form(android, values)


if __name__ == "__main__":
    try:
        main()
    except (ImportError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Smart Life account setup failed: {error}", file=sys.stderr)
        sys.exit(1)

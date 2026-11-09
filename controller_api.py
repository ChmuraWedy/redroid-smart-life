"""HTTP entry point for the Smart Life Android controller."""

from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from io import BytesIO
import json
import logging
from os import environ
from pathlib import Path
import re
from secrets import compare_digest
from shlex import join as shell_join
import socket
from threading import Event, Thread
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from controller_automation import (
    STATE_DIR, AutomationBusy, automation_lock, finish_automation, mark_running, read_state,
    start_automation, write_state,
)
from smart_life_account import ACCOUNT_ACTIVITY, SETTINGS_ACTIVITY, read_account_security
from smart_life_add_device import (
    ADD_ACTIVITY, HOME_ACTIVITY, HOME_MANAGEMENT_ACTIVITY, HOME_SETTINGS_ACTIVITY, SSH,
    ROOM_MANAGEMENT_ACTIVITY, connect_android, current_screen, finish_install, install_app,
    list_rooms, open_add_device, run, wait_for,
)
from smart_life_login import VERIFICATION_ACTIVITY, continue_account, fill_account_form, read_login, submit_login
from smart_life_scan_qr import MAX_INPUT_BYTES, SCANNER_ACTIVITY, decode_image, scan_qr


SERIAL = "redroid:5555"
ADB = ["adb", "-s", SERIAL]
LOGIN_FILE = Path("/run/secrets/smart_life_login")
TOKEN_FILE = Path("/run/secrets/controller_api_token")
APK_DIR = Path("/apks")
POWER_SOCKET = "/run/android-power/control.sock"
BACK_DESTINATIONS = {
    ACCOUNT_ACTIVITY: SETTINGS_ACTIVITY,
    SETTINGS_ACTIVITY: HOME_ACTIVITY,
    ROOM_MANAGEMENT_ACTIVITY: HOME_SETTINGS_ACTIVITY,
    HOME_SETTINGS_ACTIVITY: HOME_MANAGEMENT_ACTIVITY,
    HOME_MANAGEMENT_ACTIVITY: HOME_ACTIVITY,
    ADD_ACTIVITY: HOME_ACTIVITY,
    SCANNER_ACTIVITY: HOME_ACTIVITY,
}
logger = logging.getLogger("controller_api")
bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app):
    token = TOKEN_FILE.read_bytes().strip()
    if len(token) < 32:
        raise RuntimeError("Configure a controller API token of at least 32 bytes.")
    app.state.api_token = token
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stop_idle = Event()
    Thread(target=idle_worker, args=(stop_idle,), daemon=True).start()
    try:
        yield
    finally:
        stop_idle.set()


app = FastAPI(title="ReDroid Smart Life Controller", lifespan=lifespan)


@app.middleware("http")
async def no_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):
    return JSONResponse(status_code=422, content={"detail": "Invalid request body."})


def authorize(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None or not compare_digest(credentials.credentials.encode(), request.app.state.api_token):
        raise HTTPException(status_code=401, detail="A valid API token is required.",
                            headers={"WWW-Authenticate": "Bearer"})


api = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])


@app.get("/health")
def health():
    return {"status": "ready"}


def power_action(action):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(90 if action == "idle" else 10)
        connection.connect(POWER_SOCKET)
        connection.sendall(f"{action}\n".encode("ascii"))
        with connection.makefile("rb") as reply:
            result = json.loads(reply.readline(4096))
    if not isinstance(result, dict) or result.get("status") not in ("ok", "busy", "error"):
        raise ValueError("Invalid power agent response.")
    if action == "status" and result["status"] == "ok" and not isinstance(result.get("android"), dict):
        raise ValueError("Invalid Android power status.")
    return result


def checked_power_action(action):
    try:
        result = power_action(action)
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="The Dell Android power agent is unavailable.") from None
    if result["status"] == "busy":
        raise HTTPException(status_code=409, detail=result.get("message") or "Android power control is busy.")
    if result["status"] == "error":
        raise HTTPException(status_code=503, detail=result.get("message") or "Android power control failed.")
    return result


@contextmanager
def controller_lock():
    try:
        with automation_lock():
            yield
    except AutomationBusy:
        raise HTTPException(status_code=409, detail="Another Smart Life operation is running.")


def recover_interrupted(state):
    job = state["automation"]
    if job is not None and job["state"] != "finished":
        # The exclusive lock means no live worker owns this unfinished record.
        finish_automation(state, "interrupted")


def idle_due(state):
    deadline = state["idle_after"]
    return deadline is not None and datetime.now(UTC) >= datetime.fromisoformat(deadline)


def idle_if_due():
    state = read_state()
    job = state["automation"]
    if (job is None or job["state"] == "finished") and not idle_due(state):
        return
    with automation_lock():
        state = read_state()
        recover_interrupted(state)
        if not idle_due(state):
            return

        power = android_status()["state"]
        if power == "awake":
            checked_power_action("idle")
        if power in ("awake", "frozen"):
            state["idled_at"] = datetime.now(UTC).isoformat()
        state["idle_after"] = None
        write_state(state)


def idle_worker(stop):
    while not stop.wait(5):
        try:
            idle_if_due()
        except AutomationBusy:
            continue
        except HTTPException as error:
            if error.status_code != 409:
                logger.warning("ReDroid could not idle: %s", error.detail)
        except Exception as error:
            logger.error("ReDroid idle check failed (%s)", type(error).__name__)


def run_android(name, operation):
    try:
        with controller_lock():
            state = read_state()
            start_automation(state, name)
            outcome = "failed"
            try:
                if android_status()["state"] != "awake":
                    checked_power_action("wake")
                try:
                    android = connect_android(SERIAL)
                except Exception as error:
                    logger.warning("ADB connection failed (%s)", type(error).__name__)
                    raise HTTPException(status_code=503, detail="ReDroid is not ready over ADB.") from None
                run([*ADB, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
                run([*ADB, "shell", "wm", "dismiss-keyguard"])
                mark_running(state)
                result = operation(android)
                outcome = "success"
                return result
            finally:
                finish_automation(state, outcome)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Smart Life operation failed (%s)", type(error).__name__)
        raise HTTPException(status_code=502, detail="Smart Life automation failed.") from None


def set_android_power(action):
    with controller_lock():
        state = read_state()
        recover_interrupted(state)
        checked_power_action(action)
        state["idle_after"] = None
        state["idled_at"] = datetime.now(UTC).isoformat() if action == "idle" else None
        write_state(state)
        return android_status()


class AndroidPowerStatus(BaseModel):
    state: Literal["awake", "frozen", "stopped", "docker-paused"]
    ram_mib: int
    swap_mib: int


@api.get("/android/status", response_model=AndroidPowerStatus)
def android_status():
    return checked_power_action("status")["android"]


@api.get("/android/scrcpy")
def android_scrcpy() -> dict[str, str]:
    try:
        local_port = int(environ.get("SCRCPY_LOCAL_ADB_PORT", "15559"))
        remote_port = int(environ.get("REDROID_ADB_PORT", "5555"))
        remote_api_port = int(environ.get("REDROID_API_PORT", "18765"))
    except ValueError:
        raise HTTPException(status_code=503, detail="Configure valid ADB and API ports for scrcpy.") from None
    if not all(1 <= port <= 65535 for port in (local_port, remote_port, remote_api_port)):
        raise HTTPException(status_code=503, detail="Configure valid ADB and API ports for scrcpy.")
    serial = f"127.0.0.1:{local_port}"
    host = environ.get("SCRCPY_SSH_HOST", "dell5070")
    return {
        "launcher": shell_join(["./smart_life_add_device.py", "view", "--host", host,
                                "--port", str(local_port), "--remote-port", str(remote_port),
                                "--remote-api-port", str(remote_api_port)]),
        "command": shell_join(["scrcpy", "-s", serial, "--no-audio"]),
        "adb_connect": shell_join(["adb", "connect", serial]),
        "ssh_tunnel": shell_join([*SSH, "-o", "ExitOnForwardFailure=yes", "-N", "-L",
                                  f"{serial}:127.0.0.1:{remote_port}", "--", host]),
    }


@api.post("/android/wake", response_model=AndroidPowerStatus)
def android_wake():
    return set_android_power("wake")


@api.post("/android/idle", response_model=AndroidPowerStatus)
def android_idle():
    return set_android_power("idle")


@api.get("/automation/status")
def automation_status():
    return read_state()


def back_to_home(android):
    for _ in range(3):
        target = BACK_DESTINATIONS.get(current_screen(android))
        if target is None:
            return
        android.press("back")
        wait_for("the previous Smart Life screen", 10, lambda: current_screen(android) == target)


@api.post("/install")
def install():
    def operation(android):
        apks = sorted(APK_DIR.glob("*.apk"))
        if not apks:
            raise HTTPException(status_code=409, detail="Place the Smart Life APK set in the configured APK directory.")
        install_app(ADB, apks)
        finish_install(android)
        return {"screen": "home" if current_screen(android) == HOME_ACTIVITY else "login"}

    return run_android("install", operation)


@api.post("/account/prepare")
def prepare_account():
    def operation(android):
        values = read_login(LOGIN_FILE)
        fill_account_form(android, values)
        return {"screen": account_screen(android)}

    return run_android("account-prepare", operation)


def account_screen(android):
    return {HOME_ACTIVITY: "home", VERIFICATION_ACTIVITY: "verification"}.get(current_screen(android), "login")


@api.post("/login/submit")
def login_submit():
    def operation(android):
        submit_login(android)
        return {"screen": account_screen(android)}

    return run_android("login-submit", operation)


class VerificationCode(BaseModel):
    code: Annotated[str, Field(pattern=r"^[0-9]{6}$")]


@api.post("/signup/continue")
def signup_continue(body: VerificationCode):
    def operation(android):
        continue_account(android, body.code, LOGIN_FILE)
        return {"screen": account_screen(android)}

    return run_android("signup-continue", operation)


@api.get("/rooms")
def rooms():
    def operation(android):
        back_to_home(android)
        return list_rooms(android, ADB)

    return run_android("rooms", operation)


@api.get("/account-security")
def account_security():
    def operation(android):
        back_to_home(android)
        return read_account_security(android, SERIAL)

    return run_android("account-security", operation)


@api.post("/scan/qr", openapi_extra={"requestBody": {"required": True, "content": {
    "text/plain": {"schema": {"type": "string", "format": "byte"}},
}}})
async def scan(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_INPUT_BYTES:
            raise HTTPException(status_code=413, detail="The base64 image exceeds the 12 MiB input limit.")
        body.extend(chunk)
    try:
        content, extension = decode_image(BytesIO(body))
    except RuntimeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

    def operation(android):
        if current_screen(android) != SCANNER_ACTIVITY:
            back_to_home(android)
        scan_qr(android, ADB, content, extension)
        return {"status": "submitted"}

    return await run_in_threadpool(run_android, "scan-qr", operation)


@api.post("/add-device")
def add_device():
    def operation(android):
        hal = "/vendor/bin/hw/android.hardware.bluetooth@1.1-service.sim"
        bridge = run([*ADB, "shell", f"test -S /data/local/tmp/redroid-intel-hci.sock && grep -F ' {hal} ' /proc/mounts"],
                     check=False)
        bluetooth = run([*ADB, "shell", "dumpsys", "bluetooth_manager"], check=False)
        if not bridge or not re.search(r"^\s*state:\s*ON\s*$", bluetooth, re.MULTILINE):
            raise HTTPException(status_code=409, detail="Start ./use-bridge11.sh on the Dell and keep its session open.")
        if current_screen(android) != ADD_ACTIVITY:
            back_to_home(android)
        open_add_device(android, ADB)
        return {"screen": "add-device"}

    return run_android("add-device", operation)


app.include_router(api)

"""Persist the latest controller job and its idle deadline."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


STATE_DIR = Path("/var/lib/redroid-controller")
IDLE_DELAY = timedelta(minutes=5)


class AutomationBusy(Exception):
    pass


@contextmanager
def automation_lock():
    with (STATE_DIR / "automation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AutomationBusy from None
        yield


def read_state():
    try:
        return json.loads((STATE_DIR / "automation.json").read_text())
    except FileNotFoundError:
        return {"automation": None, "idle_after": None, "idled_at": None}


def write_state(state):
    with NamedTemporaryFile(mode="w", dir=STATE_DIR, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(state, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, STATE_DIR / "automation.json")
        finally:
            temporary.unlink(missing_ok=True)


def start_automation(state, name):
    state.update(
        automation={"name": name, "state": "starting", "started_at": datetime.now(UTC).isoformat()},
        idle_after=None,
        idled_at=None,
    )
    write_state(state)


def mark_running(state):
    state["automation"].update(state="running", running_at=datetime.now(UTC).isoformat())
    write_state(state)


def finish_automation(state, outcome):
    finished = datetime.now(UTC)
    job = state["automation"]
    elapsed = finished - datetime.fromisoformat(job["started_at"])
    job.update(state="finished", finished_at=finished.isoformat(), outcome=outcome,
               duration_ms=max(0, round(elapsed.total_seconds() * 1000)))
    state["idle_after"] = (finished + IDLE_DELAY).isoformat()
    write_state(state)

"""Run the ReDroid power commands on the host over a private Unix socket."""

import fcntl
import json
import os
from pathlib import Path
import socketserver
import subprocess


PROJECT_DIR = Path(__file__).resolve().parent
SOCKET_PATH = PROJECT_DIR / "runtime" / "control.sock"


class PowerHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            action = self.rfile.readline(16).strip()
        except TimeoutError:
            return
        if action in (b"wake", b"idle", b"status"):
            result = subprocess.run(
                [str(PROJECT_DIR / "android-idle.sh"), action.decode("ascii")],
                cwd=PROJECT_DIR,
                env={**os.environ, "REDROID_IDLE_SOURCE": "api"},
                capture_output=True,
                text=True,
            )
            status = {0: "ok", 75: "busy"}.get(result.returncode, "error")
            message = result.stdout.strip() if status == "ok" else result.stderr.strip()
        else:
            status, message = "error", "Expected wake, idle, or status."
        response = {"status": status, "message": message}
        if action == b"status" and status == "ok":
            try:
                response = {"status": status, "android": json.loads(message)}
            except ValueError:
                response = {"status": "error", "message": "Could not read Android's power state."}
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    os.umask(0o077)
    SOCKET_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    with (SOCKET_PATH.parent / "agent.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        SOCKET_PATH.unlink(missing_ok=True)
        with socketserver.UnixStreamServer(str(SOCKET_PATH), PowerHandler) as server:
            SOCKET_PATH.chmod(0o600)
            server.serve_forever()


if __name__ == "__main__":
    main()

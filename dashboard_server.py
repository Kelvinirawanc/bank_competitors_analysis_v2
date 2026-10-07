#!/usr/bin/env python3
"""
Local dashboard server + one-click scraper controller.

The browser talks to this localhost server when opened through open_dashboard.bat.
The server is intentionally bound to 127.0.0.1 only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATUS_FILE = BASE / "data" / "scraper_status.json"
LOG_FILE = BASE / "data" / "last_scraper_run.log"
PORT = 8000
LOCK = threading.Lock()
PROCESS: subprocess.Popen[str] | None = None
RUN_ACTIVE = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(status: str, message: str = "", return_code: int | None = None) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "message": message,
        "return_code": return_code,
        "updated_at_utc": now_iso(),
    }
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_status() -> dict:
    if not STATUS_FILE.exists():
        return {"status": "idle", "message": "No scraper run yet.", "return_code": None}
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "idle", "message": "Status file could not be read.", "return_code": None}


def run_pipeline() -> None:
    global PROCESS, RUN_ACTIVE
    try:
        write_status("preparing", "Installing/updating Python dependencies…")
        with LOG_FILE.open("w", encoding="utf-8") as log:
            log.write(f"Started: {now_iso()}\n")
            log.write("[1/2] Installing dependencies\n")
            install = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=BASE,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if install.returncode != 0:
                write_status("failed", "Dependency installation failed. See data/last_scraper_run.log.", install.returncode)
                return

            write_status("running", "Running adaptive Google Play scraper…")
            log.write("[2/2] Running scraper\n")
            PROCESS = subprocess.Popen(
                [sys.executable, "scrape_google_play_adaptive.py", "--lang", "id", "--country", "id"],
                cwd=BASE,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            code = PROCESS.wait()
            PROCESS = None

        if code == 0:
            write_status("completed", "Scraper completed and dashboard_data.json was rebuilt.", code)
        else:
            write_status("failed", "Scraper failed. See data/last_scraper_run.log.", code)
    except Exception as exc:
        write_status("failed", f"Controller error: {exc}", -1)
    finally:
        with LOCK:
            RUN_ACTIVE = False
            PROCESS = None


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/scraper-status":
            self._json(200, read_status())
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/api/run-scraper":
            self._json(404, {"error": "Not found"})
            return

        global RUN_ACTIVE
        with LOCK:
            if RUN_ACTIVE:
                self._json(409, {"error": "A scraper run is already in progress."})
                return
            RUN_ACTIVE = True
            write_status("queued", "Scraper run queued…")
            thread = threading.Thread(target=run_pipeline, daemon=True)
            thread.start()

        self._json(202, {"status": "queued"})


if __name__ == "__main__":
    os.chdir(BASE)
    write_status("idle", "Ready for a local scraper run.")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard: http://127.0.0.1:{PORT}")
    print("Scraper controller: POST /api/run-scraper")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server…")
    finally:
        server.server_close()

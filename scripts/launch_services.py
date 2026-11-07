"""Launch NastaraOps services silently on Windows, retaining process IDs and logs.

Standard-library-only helper invoked by scripts/start.ps1 after Docker is healthy.
Does not delete volumes, modify .env, or require administrator access.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    if sys.platform != "win32":
        print("This launcher is only supported on Windows.", file=sys.stderr)
        return 2
    if len(sys.argv) != 2:
        print("Usage: launch_services.py PORT", file=sys.stderr)
        return 2
    try:
        port = int(sys.argv[1])
    except ValueError:
        print("Invalid API port.", file=sys.stderr)
        return 2
    if not 1 <= port <= 65535:
        print("API port is out of range.", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parent.parent
    local = root / ".local"
    local.mkdir(exist_ok=True)
    record_path = local / "processes.json"
    if record_path.exists():
        print("A process record already exists. Run scripts/stop.ps1 first.", file=sys.stderr)
        return 2

    specs = [
        ("mcp", ["-m", "app.mcp_server"]),
        ("worker", ["-m", "app.worker"]),
        ("benchmark-worker", ["-m", "app.benchmark_worker"]),
        ("api", ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]),
    ]
    rows: list[dict[str, object]] = []
    try:
        for name, args in specs:
            stdout_path = local / (name + ".stdout.log")
            stderr_path = local / (name + ".stderr.log")
            # CREATE_NO_WINDOW is the Windows CreateProcess flag that avoids
            # a new console or Windows Terminal tab for each Python child.
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                child = subprocess.Popen(
                    [sys.executable, *args],
                    cwd=str(root),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    close_fds=True,
                )
            rows.append({"Name": name, "PID": child.pid, "Arguments": " ".join(args)})
            print(f"Launched {name} silently (PID {child.pid})", flush=True)
            # Give the MCP server a moment to bind its port before workers start.
            if name == "mcp":
                time.sleep(2)
    finally:
        # Preserve PIDs even if a later spawn fails, so stop.ps1 can clean up.
        if rows:
            tmp_path = local / "processes.json.tmp"
            tmp_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            os.replace(tmp_path, record_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

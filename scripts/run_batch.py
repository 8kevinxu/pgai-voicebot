"""One-command runner: boot the bridge server, then place every scenario call in sequence.

Usage:
    python scripts/run_batch.py                 # all scenarios
    python scripts/run_batch.py --scenarios 1 9 10   # a subset

Prereqs (see README): .env filled in, ffmpeg installed, and `ngrok http 8000` running with
its host placed in PUBLIC_HOST.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from scripts.run_call import place_call  # noqa: E402


def wait_for_server(timeout: float = 15.0) -> None:
    url = f"http://127.0.0.1:{config.PORT}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise SystemExit("Local server did not become healthy in time.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", nargs="*", help="subset of scenario ids; default = all")
    parser.add_argument("--no-server", action="store_true",
                        help="don't launch uvicorn (use if you run the server yourself)")
    args = parser.parse_args()

    scenarios = config.load_scenarios()
    # Stable call index per scenario (scenario 9 -> call-09) so partial/resumed runs don't
    # renumber and overwrite earlier calls.
    index_of = {sid: i for i, sid in enumerate(scenarios.keys(), start=1)}
    ids = args.scenarios or list(scenarios.keys())

    server = None
    if not args.no_server:
        print("[batch] starting bridge server...")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.server:app",
             "--host", "0.0.0.0", "--port", str(config.PORT)],
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        wait_for_server()
        print(f"[batch] server healthy on :{config.PORT}")

    try:
        for i, sid in enumerate(ids, start=1):
            if sid not in scenarios:
                print(f"[batch] skipping unknown scenario id: {sid}")
                continue
            print(f"\n=== Call {i}/{len(ids)} — scenario {sid}: {scenarios[sid].title} ===")
            try:
                place_call(sid, index_of[sid])
            except Exception as exc:  # noqa: BLE001 - keep the batch going
                print(f"[batch] call for scenario {sid} errored: {exc}")
            time.sleep(4)  # brief gap between calls
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    print("\n[batch] done. Recordings -> recordings/  Transcripts -> transcripts/")
    print("[batch] next: python scripts/analyze.py")


if __name__ == "__main__":
    main()

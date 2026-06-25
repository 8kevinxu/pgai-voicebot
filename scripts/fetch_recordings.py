"""Replace local recordings with Twilio's server-side dual-channel recordings.

The live bridge taps media frames and reconstructs audio by arrival time, which can pick up
network-jitter artifacts. Twilio also records each call server-side (we enable
record=True, recording_channels="dual"), and that copy is cleanly timed. This script reads each
transcript's call_sid, downloads Twilio's lossless WAV, and re-encodes a clear stereo mp3
(left = PGAI agent, right = our patient bot) into recordings/call-NN.mp3.

Usage:  python scripts/fetch_recordings.py
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from twilio.rest import Client  # noqa: E402

from app import config  # noqa: E402

API_BASE = "https://api.twilio.com/2010-04-01/Accounts"


def download_wav(rec_sid: str, dest: Path) -> None:
    url = f"{API_BASE}/{config.TWILIO_ACCOUNT_SID}/Recordings/{rec_sid}.wav"
    auth = base64.b64encode(
        f"{config.TWILIO_ACCOUNT_SID}:{config.TWILIO_AUTH_TOKEN}".encode()
    ).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())


def main() -> None:
    config.require_env("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)

    transcripts = sorted(config.TRANSCRIPTS_DIR.glob("transcript-*.json"))
    if not transcripts:
        raise SystemExit("No transcripts found. Run scripts/run_batch.py first.")

    for tf in transcripts:
        data = json.loads(tf.read_text())
        idx = data["call_index"]
        call_sid = data.get("call_sid")
        if not call_sid:
            print(f"[fetch] call-{idx:02d}: no call_sid in transcript, skipping")
            continue

        recs = client.recordings.list(call_sid=call_sid)
        if not recs:
            print(f"[fetch] call-{idx:02d}: no Twilio recording for {call_sid}, skipping")
            continue
        rec = max(recs, key=lambda r: int(r.duration or 0))  # the substantive one

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            download_wav(rec.sid, wav_path)
            out = config.RECORDINGS_DIR / f"call-{idx:02d}.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path),
                 "-c:a", "libmp3lame", "-b:a", "64k", str(out)],
                check=True, capture_output=True,
            )
            print(f"[fetch] call-{idx:02d}: {rec.duration}s, {rec.channels}ch -> {out.name}")
        finally:
            wav_path.unlink(missing_ok=True)

    print("[fetch] done.")


if __name__ == "__main__":
    main()

"""Rebuild accurate transcripts from the dual-channel recordings.

The live bridge timestamps each utterance by when its Realtime event arrived, which misorders
the conversation (model output completes early; Whisper input transcription lands late). This
script instead transcribes the ground-truth audio: it splits each recording's two channels
(one speaker each), runs Whisper with segment timestamps on each, and merges by real spoken
time. Speaker attribution is exact (channel-based) and ordering is correct.

Channel role is detected per call: in every scenario the clinic agent greets first, so the
channel whose first speech segment starts earliest is the AGENT.

Usage:  python scripts/transcribe_recordings.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app import config  # noqa: E402
from app.realtime import TranscriptLog  # noqa: E402


def split_channel(src: Path, channel: int, dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-af", f"pan=mono|c0=c{channel}",
         "-ar", "16000", str(dest)],
        check=True, capture_output=True,
    )


def transcribe(client: OpenAI, path: Path) -> list[dict]:
    with path.open("rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
    out = []
    for seg in (resp.segments or []):
        start = seg["start"] if isinstance(seg, dict) else seg.start
        text = (seg["text"] if isinstance(seg, dict) else seg.text).strip()
        if text:
            out.append({"start": float(start), "text": text})
    return out


def first_start(segs: list[dict]) -> float:
    return segs[0]["start"] if segs else float("inf")


def main() -> None:
    config.require_env("OPENAI_API_KEY")
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    recordings = sorted(config.RECORDINGS_DIR.glob("call-*.mp3"))
    if not recordings:
        raise SystemExit("No recordings found. Run fetch_recordings.py first.")

    for rec in recordings:
        idx = int(rec.stem.split("-")[1])
        meta_path = config.TRANSCRIPTS_DIR / f"transcript-{idx:02d}.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        scenarios = config.load_scenarios()
        scenario = scenarios.get(str(meta.get("scenario_id", "")))
        if scenario is None:
            print(f"[transcribe] call-{idx:02d}: unknown scenario, skipping")
            continue

        with tempfile.TemporaryDirectory() as td:
            left, right = Path(td) / "l.wav", Path(td) / "r.wav"
            split_channel(rec, 0, left)
            split_channel(rec, 1, right)
            seg_left = transcribe(client, left)
            seg_right = transcribe(client, right)

        # The agent greets first -> earliest-starting channel is the agent.
        if first_start(seg_left) <= first_start(seg_right):
            agent_segs, patient_segs = seg_left, seg_right
        else:
            agent_segs, patient_segs = seg_right, seg_left

        merged = (
            [{"start": s["start"], "role": TranscriptLog.AGENT, "text": s["text"]} for s in agent_segs]
            + [{"start": s["start"], "role": TranscriptLog.PATIENT, "text": s["text"]} for s in patient_segs]
        )
        merged.sort(key=lambda x: x["start"])

        # Group consecutive segments from the same speaker into one turn.
        log = TranscriptLog(idx, scenario, call_sid=meta.get("call_sid"))
        for seg in merged:
            if log.lines and log.lines[-1]["role"] == seg["role"]:
                log.lines[-1]["text"] += " " + seg["text"]
            else:
                log.lines.append({"t": round(seg["start"], 2), "role": seg["role"], "text": seg["text"]})

        paths = log.write(config.TRANSCRIPTS_DIR)
        print(f"[transcribe] call-{idx:02d}: {len(log.lines)} turns -> {paths['txt'].name}")

    print("[transcribe] done.")


if __name__ == "__main__":
    main()

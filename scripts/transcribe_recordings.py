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


def _attr(obj, key):
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def transcribe(client: OpenAI, path: Path) -> list[dict]:
    """Return speaker turns for one (single-speaker) channel.

    Whisper's segment-level start times are coarse and can misreport leading silence as t=0,
    which scrambles ordering. Word-level timestamps are accurate, so we group words into turns
    on real silence gaps — this keeps ordering correct and each utterance whole.
    """
    with path.open("rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    GAP_S = 1.5  # a silence gap longer than this between words starts a new turn
    turns: list[dict] = []
    for w in (resp.words or []):
        start, end, word = float(_attr(w, "start")), float(_attr(w, "end")), _attr(w, "word")
        if turns and start - turns[-1]["end"] <= GAP_S:
            turns[-1]["text"] += " " + word
            turns[-1]["end"] = end
        else:
            turns.append({"start": start, "end": end, "text": word})
    for t in turns:
        t["text"] = t["text"].strip()
    return turns


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

        # Each channel already returns whole turns; merge the two speakers and sort by time.
        merged = (
            [{"start": t["start"], "role": TranscriptLog.AGENT, "text": t["text"]} for t in agent_segs]
            + [{"start": t["start"], "role": TranscriptLog.PATIENT, "text": t["text"]} for t in patient_segs]
        )
        merged.sort(key=lambda x: x["start"])

        log = TranscriptLog(idx, scenario, call_sid=meta.get("call_sid"))
        for t in merged:
            log.lines.append({"t": round(t["start"], 2), "role": t["role"], "text": t["text"].strip()})

        paths = log.write(config.TRANSCRIPTS_DIR)
        print(f"[transcribe] call-{idx:02d}: {len(log.lines)} turns -> {paths['txt'].name}")

    print("[transcribe] done.")


if __name__ == "__main__":
    main()

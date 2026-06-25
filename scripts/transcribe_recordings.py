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
import re
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


def silence_intervals(path: Path, noise_db: int = -40, min_dur: float = 0.4) -> list[tuple[float, float]]:
    """Detected silence (start, end) intervals for a mono channel, via ffmpeg silencedetect."""
    res = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    intervals, start = [], None
    for line in res.stderr.splitlines():
        if (m := re.search(r"silence_start:\s*(-?[0-9.]+)", line)):
            start = max(0.0, float(m.group(1)))
        elif (m := re.search(r"silence_end:\s*([0-9.]+)", line)) and start is not None:
            intervals.append((start, float(m.group(1))))
            start = None
    if start is not None:  # trailing silence to end of file
        intervals.append((start, float("inf")))
    return intervals


def audio_duration(path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(res.stdout.strip())
    except ValueError:
        return 0.0


def speech_windows(path: Path, merge_gap: float = 1.5, pad: float = 0.2,
                   min_len: float = 0.25) -> list[tuple[float, float]]:
    """Voiced (speech) windows = the complement of silence, with near ones merged into turns."""
    dur = audio_duration(path)
    speech, prev = [], 0.0
    for a, b in silence_intervals(path):
        if a > prev:
            speech.append([prev, a])
        prev = max(prev, dur if b == float("inf") else b)
    if prev < dur:
        speech.append([prev, dur])

    merged: list[list[float]] = []
    for s in speech:
        if merged and s[0] - merged[-1][1] < merge_gap:
            merged[-1][1] = s[1]
        else:
            merged.append(list(s))
    return [(max(0.0, a - pad), b + pad) for a, b in merged if b - a >= min_len]


def transcribe(client: OpenAI, path: Path) -> list[dict]:
    """Return speaker turns for one (single-speaker) channel.

    These channels are mostly silence (the other speaker is on the other channel), and Whisper
    hallucinates filler text over long silences. So instead of transcribing the whole channel,
    we detect the voiced windows (ffmpeg silencedetect) and transcribe only those slices — no
    silence for Whisper to invent words over. Each window's onset is an accurate timestamp, so
    cross-channel ordering is correct and each utterance stays whole and well-punctuated.
    """
    turns: list[dict] = []
    for start, end in speech_windows(path):
        with tempfile.TemporaryDirectory() as td:
            clip = Path(td) / "clip.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(path), str(clip)],
                check=True, capture_output=True,
            )
            with clip.open("rb") as f:
                text = client.audio.transcriptions.create(
                    model="whisper-1", file=f, response_format="text",
                ).strip()
        if text and re.search(r"[A-Za-z0-9]", text):
            turns.append({"start": start, "end": end, "text": text})
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

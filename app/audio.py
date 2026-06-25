"""Dual-channel call recorder.

Twilio Media Streams and the OpenAI Realtime API both exchange 8 kHz G.711 mu-law audio.
We tap both directions of the stream:

  * inbound  frames  = the PGAI agent's voice  -> left channel
  * outbound frames  = our "patient" bot's voice -> right channel

Each frame is stamped with its arrival offset so the two sides line up on a shared timeline.
On finalize we decode mu-law -> PCM16, write a stereo WAV, then shell out to ffmpeg to produce
the mp3/ogg required by the brief.
"""
from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE


def _build_ulaw_table() -> np.ndarray:
    """256-entry G.711 mu-law -> linear PCM16 lookup table."""
    table = np.zeros(256, dtype=np.int16)
    for u in range(256):
        u_val = ~u & 0xFF
        sign = u_val & 0x80
        exponent = (u_val >> 4) & 0x07
        mantissa = u_val & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        table[u] = -sample if sign else sample
    return table


_ULAW_TABLE = _build_ulaw_table()


def ulaw_bytes_to_pcm16(data: bytes) -> np.ndarray:
    return _ULAW_TABLE[np.frombuffer(data, dtype=np.uint8)]


class CallRecorder:
    """Accumulates both sides of a call and writes mp3/ogg on finalize."""

    def __init__(self, call_index: int):
        self.call_index = call_index
        self._start: float | None = None
        # list of (offset_in_samples, pcm16 ndarray)
        self._inbound: list[tuple[int, np.ndarray]] = []
        self._outbound: list[tuple[int, np.ndarray]] = []

    def _offset_samples(self) -> int:
        now = time.monotonic()
        if self._start is None:
            self._start = now
        return int((now - self._start) * SAMPLE_RATE)

    def add_inbound(self, ulaw: bytes) -> None:
        self._inbound.append((self._offset_samples(), ulaw_bytes_to_pcm16(ulaw)))

    def add_outbound(self, ulaw: bytes) -> None:
        self._outbound.append((self._offset_samples(), ulaw_bytes_to_pcm16(ulaw)))

    @staticmethod
    def _render_channel(chunks: list[tuple[int, np.ndarray]], total: int) -> np.ndarray:
        buf = np.zeros(total, dtype=np.int16)
        for offset, samples in chunks:
            end = min(offset + len(samples), total)
            if end > offset:
                buf[offset:end] = samples[: end - offset]
        return buf

    def finalize(self, recordings_dir: Path) -> dict[str, Path] | None:
        if not self._inbound and not self._outbound:
            return None

        all_chunks = self._inbound + self._outbound
        total = max((off + len(arr)) for off, arr in all_chunks)

        left = self._render_channel(self._inbound, total)   # PGAI agent
        right = self._render_channel(self._outbound, total)  # our patient bot
        stereo = np.stack([left, right], axis=1).reshape(-1)

        stem = f"call-{self.call_index:02d}"
        wav_path = recordings_dir / f"{stem}.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(stereo.astype("<i2").tobytes())

        outputs = {"wav": wav_path}
        for ext, args in (("mp3", ["-q:a", "4"]), ("ogg", ["-c:a", "libopus", "-b:a", "32k"])):
            out = recordings_dir / f"{stem}.{ext}"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(wav_path), *args, str(out)],
                    check=True,
                    capture_output=True,
                )
                outputs[ext] = out
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                print(f"[audio] ffmpeg {ext} conversion failed ({exc}). WAV kept at {wav_path}.")
        return outputs

"""OpenAI Realtime API helpers: persona prompt, session config, transcript logging."""
from __future__ import annotations

import json
import time
from pathlib import Path

from websockets.asyncio.client import connect as ws_connect

from .config import (
    OPENAI_API_KEY,
    OPENAI_REALTIME_MODEL,
    OPENAI_REALTIME_VOICE,
    Scenario,
)

BASE_PERSONA = """\
You are a real person calling a medical clinic's phone line. You are the PATIENT/CALLER.
The other party is the clinic's AI receptionist. Your job is to behave like an ordinary human
caller so that the clinic's system can be tested realistically.

CRITICAL ROLE RULES:
- You are ALWAYS the caller/patient. You are NEVER the receptionist. Do not greet the caller,
  do not offer to help, do not ask "what is the reason for your visit" — that is THEIR job.
- ALWAYS speak in English. Ignore automated menu prompts (e.g. "press 1", "para español"),
  hold music, or recorded notices — do not respond to them, repeat them, or translate them.
- Do NOT speak first. Wait silently until a receptionist actually greets you and asks how they
  can help, THEN begin with your reason for calling.
- Never repeat back or echo what the other party just said.

How to talk:
- Speak naturally and conversationally, in short spoken sentences. Use mild filler
  ("um", "okay", "yeah") occasionally, but stay easy to understand.
- Wait for the receptionist to finish before you answer. Do not narrate your actions or mention
  that you are an AI, a test, or a simulation. Never break character.
- Give realistic details (a name, a callback number, a date) only when asked. Invent reasonable
  values; never use real personal data.
- Keep the conversation moving toward your goal. If the receptionist is unclear, ask a natural
  follow-up. If something seems wrong, react like a normal person would ("wait, I thought you
  were closed on weekends?").
- When your goal is resolved (or the receptionist clearly cannot help), wrap up politely and say
  a clear goodbye such as "Okay, thank you, goodbye." Do not drag the call on.
"""


def build_instructions(scenario: Scenario) -> str:
    return (
        f"{BASE_PERSONA}\n"
        f"--- YOUR SITUATION FOR THIS CALL ---\n"
        f"Who you are: {scenario.persona}\n"
        f"What you want to accomplish: {scenario.goal}\n"
        f"You will consider the call successful if: {scenario.success_criteria}\n"
    )


def session_update_payload(scenario: Scenario) -> dict:
    # GA Realtime API shape (audio.input/output nested; audio/pcmu = G.711 u-law for telephony).
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": OPENAI_REALTIME_MODEL,
            "output_modalities": ["audio"],
            "instructions": build_instructions(scenario),
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.55,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 650,
                    },
                    "transcription": {"model": "whisper-1"},
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": OPENAI_REALTIME_VOICE,
                },
            },
        },
    }


async def connect_openai():
    # GA Realtime: no 'OpenAI-Beta: realtime=v1' header (it forces the disabled beta shape).
    url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
    return await ws_connect(
        url,
        additional_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        max_size=None,
    )


class TranscriptLog:
    """Collects timestamped utterances from both sides and writes JSON + readable text."""

    AGENT = "agent"      # the PGAI receptionist (our model's audio input)
    PATIENT = "patient"  # our bot (our model's audio output)

    def __init__(self, call_index: int, scenario: Scenario):
        self.call_index = call_index
        self.scenario = scenario
        self._start = time.monotonic()
        self.lines: list[dict] = []

    def add(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.lines.append({
            "t": round(time.monotonic() - self._start, 2),
            "role": role,
            "text": text,
        })

    @staticmethod
    def _mmss(t: float) -> str:
        return f"{int(t // 60)}:{int(t % 60):02d}"

    def write(self, transcripts_dir: Path) -> dict[str, Path]:
        stem = f"transcript-{self.call_index:02d}"
        data = {
            "call_index": self.call_index,
            "scenario_id": self.scenario.id,
            "scenario_title": self.scenario.title,
            "goal": self.scenario.goal,
            "success_criteria": self.scenario.success_criteria,
            "lines": self.lines,
        }
        json_path = transcripts_dir / f"{stem}.json"
        json_path.write_text(json.dumps(data, indent=2))

        label = {self.AGENT: "AGENT (PGAI)", self.PATIENT: "PATIENT (bot)"}
        txt_lines = [
            f"Call {self.call_index:02d} — Scenario {self.scenario.id}: {self.scenario.title}",
            f"Goal: {self.scenario.goal}",
            "=" * 70,
        ]
        for ln in self.lines:
            who = label.get(ln["role"], ln["role"])
            txt_lines.append(f"[{self._mmss(ln['t'])}] {who}: {ln['text']}")
        txt_path = transcripts_dir / f"{stem}.txt"
        txt_path.write_text("\n".join(txt_lines) + "\n")
        return {"json": json_path, "txt": txt_path}

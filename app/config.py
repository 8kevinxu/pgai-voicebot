"""Central configuration + scenario loading.

Everything reads from environment variables (loaded from .env). Keeping this in one place
makes the rest of the code free of os.getenv scatter.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = ROOT / "recordings"
TRANSCRIPTS_DIR = ROOT / "transcripts"
SCENARIOS_FILE = ROOT / "scenarios" / "scenarios.yaml"

RECORDINGS_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
OPENAI_REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "alloy")
OPENAI_JUDGE_MODEL = os.getenv("OPENAI_JUDGE_MODEL", "gpt-4o")

# --- Twilio ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
PGAI_TEST_NUMBER = os.getenv("PGAI_TEST_NUMBER", "+18054398008")

# --- Networking ---
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "").replace("https://", "").replace("http://", "").strip("/")
PORT = int(os.getenv("PORT", "8000"))
MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "210"))

# 8 kHz, 20 ms G.711 frames — Twilio Media Streams + OpenAI Realtime both speak g711_ulaw,
# so no resampling is needed in the live path.
SAMPLE_RATE = 8000


@dataclass
class Scenario:
    id: str
    title: str
    persona: str
    goal: str
    success_criteria: str
    notes: str = ""


def load_scenarios() -> dict[str, Scenario]:
    raw = yaml.safe_load(SCENARIOS_FILE.read_text())
    out: dict[str, Scenario] = {}
    for item in raw["scenarios"]:
        sid = str(item["id"])
        out[sid] = Scenario(
            id=sid,
            title=item["title"],
            persona=item["persona"],
            goal=item["goal"],
            success_criteria=item["success_criteria"],
            notes=item.get("notes", ""),
        )
    return out


def require_env(*names: str) -> None:
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\nCopy .env.example to .env and fill them in."
        )

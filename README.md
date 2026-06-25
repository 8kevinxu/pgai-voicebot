# PGAI Voice "Patient" Bot

An automated voice bot that **calls the Pretty Good AI test line (+1‑805‑439‑8008)**, acts like
a realistic patient across a range of scenarios, records and transcribes both sides of every
call, and drafts a bug report on the clinic agent's responses.

It bridges **Twilio Media Streams ⇄ the OpenAI Realtime API** (speech‑to‑speech) for a
low‑latency, natural conversation. See [ARCHITECTURE.md](ARCHITECTURE.md) for the design.

```
Twilio outbound call ──<Connect><Stream>──▶ FastAPI bridge ⇄ OpenAI Realtime (patient voice)
                                                │
                       Twilio dual-channel recording → recordings/*.mp3
                            Realtime transcripts → transcripts/*.txt,*.json
                                                │
                                  scripts/analyze.py (LLM judge) → BUG_REPORT.md
```

## Prerequisites

- **Python 3.10+** and **ffmpeg** (`brew install ffmpeg`)
- **ngrok** (or any public tunnel): `brew install ngrok`
- An **OpenAI API key** with Realtime API access
- A **Twilio account** with one purchased phone number (this is the single E.164 caller number
  you report on the submission form)

## Setup

```bash
git clone <your-repo-url> && cd pgai-voicebot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # or: make install

cp .env.example .env                      # then fill in the values
```

Fill `.env`:
- `OPENAI_API_KEY` — your key
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` — from the Twilio console
- `PGAI_TEST_NUMBER` — already set to `+18054398008`
- `PUBLIC_HOST` — set after starting ngrok (next step)

Start the tunnel (in its own terminal) and copy the forwarding host into `.env`:

```bash
ngrok http 8000
# e.g. Forwarding https://ab12-34-56.ngrok-free.app  ->  set PUBLIC_HOST=ab12-34-56.ngrok-free.app
```

## Run

One command boots the bridge server and places a call for every scenario:

```bash
python scripts/run_batch.py            # all 12 scenarios  (or: make run)
```

Pull the clean recordings, rebuild accurate transcripts from the audio, then draft the report:

```bash
python scripts/fetch_recordings.py     # clean stereo mp3s from Twilio   (or: make fetch)
python scripts/transcribe_recordings.py# accurate transcripts from audio (or: make transcribe)
python scripts/analyze.py              # writes BUG_REPORT.md            (or: make analyze)
```

> Why separate fetch/transcribe steps: the live bridge reconstructs a preview mp3 and a live
> transcript from Realtime events, but frame-arrival timing adds audio jitter and — because model
> output completes early while input transcription lands late — the live transcript can misorder
> turns. `fetch_recordings.py` pulls Twilio's cleanly-timed dual-channel recording, and
> `transcribe_recordings.py` rebuilds the transcript from that audio: it detects each channel's
> voiced windows (`ffmpeg silencedetect`) and transcribes only those slices, then merges the two
> speakers by spoken time. Transcribing voiced slices (rather than the whole mostly-silent
> channel) keeps ordering correct and avoids Whisper hallucinating filler over long silences.
> Those are the recording + transcript you submit.

Useful subsets / single calls:

```bash
python scripts/run_batch.py --scenarios 1 9 10     # just these scenarios
python scripts/run_call.py --scenario 9 --index 1  # one call (server must be running)
```

> Tip for the first run: do a single call, **listen to `recordings/call-01.mp3`** and read
> `transcripts/transcript-01.txt`, then tune the persona/VAD in `app/realtime.py` before running
> the full batch. Iterating on that first call is the fastest path to lucid conversations.

## Output

- `recordings/call-NN.mp3` — clean stereo: left = PGAI agent, right = our patient bot
- `transcripts/transcript-NN.txt` / `.json` — timestamped, both sides
- `BUG_REPORT.md` — auto‑drafted findings to curate

## Configuration knobs (`.env`)

| Variable | Purpose |
|---|---|
| `OPENAI_REALTIME_MODEL` | `gpt-realtime` (GA); fall back to `gpt-4o-realtime-preview` if needed |
| `OPENAI_REALTIME_VOICE` | patient voice (alloy, ash, ballad, coral, echo, sage, shimmer, verse) |
| `OPENAI_JUDGE_MODEL` | chat model for `analyze.py` (default `gpt-4o`) |
| `MAX_CALL_SECONDS` | hard safety cap on call length (default 210) |

Scenarios live in [`scenarios/scenarios.yaml`](scenarios/scenarios.yaml) — edit personas/goals or
add your own.

## Submission checklist (manual)

- [ ] 10+ calls with recordings (mp3/ogg) + transcripts committed
- [ ] Curated `BUG_REPORT.md`
- [ ] Loom walkthrough (≤5 min) of approach + what you built
- [ ] Separate ≤5‑min screen recording of prompting AI to debug/fix code
- [ ] Submission form: repo link, Loom link, the single caller number (E.164)

## Troubleshooting

- **Twilio can't reach the websocket** → confirm `PUBLIC_HOST` matches the live ngrok host
  (no `https://`, no trailing slash) and the server is running.
- **No audio / one-sided audio** → ensure ffmpeg is installed; check the call actually connected
  (Twilio console → call logs).
- **Realtime 403/model error** → set `OPENAI_REALTIME_MODEL=gpt-4o-realtime-preview`.
- **Robotic/garbled voice** → you likely changed audio formats; both sides must stay
  `g711_ulaw` (8 kHz) end to end.

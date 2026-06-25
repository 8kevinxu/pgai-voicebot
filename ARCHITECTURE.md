# Architecture

**How it works.** A small Python control plane places an outbound call with the Twilio REST API
to the PGAI test line. The call's TwiML (`app/server.py:/twiml`) returns a `<Connect><Stream>`
that opens a bidirectional WebSocket from Twilio back to our FastAPI server. That server is a
thin **bridge**: it relays 8 kHz G.711 μ‑law audio frames between Twilio and a live **OpenAI
Realtime API** session in both directions. The Realtime session *is* the "patient" — a
speech‑to‑speech model whose persona and per‑call goal are injected as session instructions from
`scenarios/scenarios.yaml`. As audio flows, the bridge taps both directions to build a stereo
recording (left = the PGAI agent, right = our bot) and collects the Realtime transcript events
(our bot's text plus Whisper transcription of the agent) into a timestamped transcript. After the
batch, `scripts/analyze.py` runs an OpenAI chat model as an **LLM judge** over each transcript,
comparing the conversation against the scenario's success criteria to draft `BUG_REPORT.md`.

**Why these choices.** The challenge is judged first on whether the voice conversation is
*lucid* and natural, so the most important decision was using a **speech‑to‑speech Realtime
model** rather than a cascaded STT→LLM→TTS pipeline — it removes inter‑stage latency and gives
human‑like turn‑taking via server‑side VAD, with barge‑in handled by clearing Twilio's playback
buffer the moment the agent starts talking. Both Twilio and OpenAI Realtime natively speak
`g711_ulaw`, so audio passes through untouched (**no resampling**), which keeps the audio clean
and the bridge tiny. Recording is taken **directly from the media‑stream frames** instead of
relying solely on Twilio's recorder, which guarantees we capture *both sides* of every call and
lets us convert to the required mp3/ogg with ffmpeg; Twilio dual‑channel recording is also left
on as a backup. Scenarios are data, not code, so adding or editing test cases (and steering each
call toward a concrete outcome) is a YAML edit. The whole thing is intentionally small and
single‑provider (OpenAI for both the voice and the judge) to stay well under the cost target and
easy to read.

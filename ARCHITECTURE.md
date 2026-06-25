# Architecture

**How it works.** A small Python control plane places an outbound call with the Twilio REST API
to the PGAI test line. The call's TwiML (`app/server.py:/twiml`) returns a `<Connect><Stream>`
that opens a bidirectional WebSocket from Twilio back to our FastAPI server. That server is a
thin **bridge**: it relays 8 kHz G.711 μ‑law audio frames between Twilio and a live **OpenAI
Realtime API** session in both directions. The Realtime session *is* the "patient" — a
speech‑to‑speech model whose persona and per‑call goal are injected as session instructions from
`scenarios/scenarios.yaml`. As audio flows, the bridge taps both directions to build a stereo
recording (left = the PGAI agent, right = our bot) and a live (approximate) transcript from the
Realtime events. The submitted transcripts, however, are rebuilt after the call by
`scripts/transcribe_recordings.py`, which detects each recording channel's voiced windows
(`ffmpeg silencedetect`), transcribes only those slices with Whisper, and merges the two speakers
by spoken time. This fixes the turn misordering that event-arrival timing produces (model output
completes early, input transcription lands late) and avoids Whisper hallucinating filler text over
the long silences in each single-speaker channel.
After the batch, `scripts/analyze.py` runs an OpenAI chat model as an **LLM judge** over each transcript,
comparing the conversation against the scenario's success criteria to draft `BUG_REPORT.md`.

**Why these choices.** The challenge is judged first on whether the voice conversation is
*lucid* and natural, so the most important decision was using a **speech‑to‑speech Realtime
model** rather than a cascaded STT→LLM→TTS pipeline — it removes inter‑stage latency and gives
human‑like turn‑taking via server‑side VAD, with barge‑in handled by clearing Twilio's playback
buffer the moment the agent starts talking. Both Twilio and OpenAI Realtime natively speak
`g711_ulaw`, so audio passes through untouched (**no resampling**), which keeps the bridge tiny.
For the submitted recordings we rely on **Twilio's server‑side dual‑channel recording** (left =
PGAI agent, right = our bot): `scripts/fetch_recordings.py` downloads that cleanly‑timed copy and
re‑encodes a clear stereo mp3. The bridge also taps the media‑stream frames to produce a local
preview mp3, but reconstructing audio from arrival‑time offsets can introduce jitter, so the
Twilio copy is what we ship. Scenarios are data, not code, so adding or editing test cases (and steering each
call toward a concrete outcome) is a YAML edit. The whole thing is intentionally small and
single‑provider (OpenAI for both the voice and the judge) to stay well under the cost target and
easy to read.

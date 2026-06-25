"""FastAPI app: serves TwiML and bridges Twilio Media Streams <-> OpenAI Realtime.

Run with:  uvicorn app.server:app --host 0.0.0.0 --port 8000
Twilio reaches it through a public tunnel (ngrok) at PUBLIC_HOST.
"""
from __future__ import annotations

import asyncio
import base64
import json

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse, Response

from . import config
from .audio import CallRecorder
from .realtime import TranscriptLog, connect_openai, session_update_payload

app = FastAPI()
SCENARIOS = config.load_scenarios()

GOODBYE_HINTS = ("goodbye", "bye now", "have a good", "take care", "thanks, bye", "thank you, bye")


@app.get("/health")
async def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml(request: Request) -> Response:
    """Return TwiML that connects the call's audio to our media-stream websocket."""
    scenario_id = request.query_params.get("scenario_id", "1")
    call_index = request.query_params.get("call_index", "1")
    ws_url = f"wss://{config.PUBLIC_HOST}/media-stream"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Connect><Stream url=\"{ws_url}\">"
        f'<Parameter name="scenario_id" value="{scenario_id}"/>'
        f'<Parameter name="call_index" value="{call_index}"/>'
        "</Stream></Connect>"
        "</Response>"
    )
    return Response(content=xml, media_type="text/xml")


@app.websocket("/media-stream")
async def media_stream(twilio_ws: WebSocket) -> None:
    await twilio_ws.accept()

    state = {"stream_sid": None, "call_sid": None, "should_hangup": False}
    recorder: CallRecorder | None = None
    transcript: TranscriptLog | None = None
    openai_ws = None

    async def hangup_after(delay: float) -> None:
        """Let the bot's goodbye audio flush, then end the call via Twilio REST."""
        await asyncio.sleep(delay)
        if state["call_sid"]:
            try:
                from twilio.rest import Client
                client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
                client.calls(state["call_sid"]).update(status="completed")
            except Exception as exc:  # noqa: BLE001 - best-effort hangup
                print(f"[server] hangup failed: {exc}")

    async def twilio_to_openai() -> None:
        nonlocal recorder, transcript, openai_ws
        async for raw in twilio_ws.iter_text():
            data = json.loads(raw)
            event = data.get("event")

            if event == "start":
                start = data["start"]
                state["stream_sid"] = start["streamSid"]
                state["call_sid"] = start.get("callSid")
                params = start.get("customParameters", {})
                scenario = SCENARIOS.get(params.get("scenario_id", "1"))
                call_index = int(params.get("call_index", "1"))
                recorder = CallRecorder(call_index)
                transcript = TranscriptLog(call_index, scenario)
                openai_ws = await connect_openai()
                await openai_ws.send(json.dumps(session_update_payload(scenario)))
                asyncio.create_task(openai_to_twilio())
                asyncio.create_task(max_duration_guard())
                print(f"[server] call {call_index:02d} started (scenario {scenario.id})")

            elif event == "media" and openai_ws is not None:
                payload = data["media"]["payload"]
                recorder.add_inbound(base64.b64decode(payload))
                await openai_ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": payload,
                }))

            elif event == "stop":
                break

    async def openai_to_twilio() -> None:
        nonlocal recorder, transcript
        async for raw in openai_ws:
            evt = json.loads(raw)
            etype = evt.get("type")

            if etype == "response.output_audio.delta" and evt.get("delta"):
                await twilio_ws.send_json({
                    "event": "media",
                    "streamSid": state["stream_sid"],
                    "media": {"payload": evt["delta"]},
                })
                recorder.add_outbound(base64.b64decode(evt["delta"]))

            elif etype == "input_audio_buffer.speech_started":
                # Caller (the PGAI agent) started talking -> stop our buffered playback.
                await twilio_ws.send_json({"event": "clear", "streamSid": state["stream_sid"]})
                await openai_ws.send(json.dumps({"type": "response.cancel"}))

            elif etype == "response.output_audio_transcript.done":
                text = evt.get("transcript", "")
                transcript.add(TranscriptLog.PATIENT, text)
                if any(h in text.lower() for h in GOODBYE_HINTS) and not state["should_hangup"]:
                    state["should_hangup"] = True
                    asyncio.create_task(hangup_after(3.5))

            elif etype == "conversation.item.input_audio_transcription.completed":
                transcript.add(TranscriptLog.AGENT, evt.get("transcript", ""))

            elif etype == "error":
                print(f"[server] OpenAI error: {evt.get('error')}")

    async def max_duration_guard() -> None:
        await asyncio.sleep(config.MAX_CALL_SECONDS)
        if not state["should_hangup"]:
            print("[server] max call duration reached -> hanging up")
            state["should_hangup"] = True
            await hangup_after(0)

    try:
        await twilio_to_openai()
    finally:
        if openai_ws is not None:
            await openai_ws.close()
        if recorder is not None:
            outputs = recorder.finalize(config.RECORDINGS_DIR)
            if outputs:
                print(f"[server] saved recording: {outputs.get('mp3', outputs.get('wav'))}")
        if transcript is not None:
            paths = transcript.write(config.TRANSCRIPTS_DIR)
            print(f"[server] saved transcript: {paths['txt']}")

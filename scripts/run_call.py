"""Place a single outbound call for one scenario and wait for it to finish.

Assumes the FastAPI bridge is reachable at PUBLIC_HOST (via ngrok) — run_batch.py starts the
server for you. Usage:  python scripts/run_call.py --scenario 1 --index 1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from twilio.rest import Client  # noqa: E402

from app import config  # noqa: E402

TERMINAL = {"completed", "failed", "busy", "no-answer", "canceled"}


def place_call(scenario_id: str, call_index: int) -> str:
    config.require_env(
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "OPENAI_API_KEY"
    )
    if not config.PUBLIC_HOST:
        raise SystemExit("PUBLIC_HOST is not set. Start ngrok and put the host in .env.")

    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    twiml_url = (
        f"https://{config.PUBLIC_HOST}/twiml"
        f"?scenario_id={scenario_id}&call_index={call_index}"
    )
    call = client.calls.create(
        to=config.PGAI_TEST_NUMBER,
        from_=config.TWILIO_FROM_NUMBER,
        url=twiml_url,
        record=True,
        recording_channels="dual",
    )
    print(f"[call {call_index:02d}] dialing {config.PGAI_TEST_NUMBER} (scenario {scenario_id}) "
          f"sid={call.sid}")

    deadline = time.time() + config.MAX_CALL_SECONDS + 60
    status = call.status
    while status not in TERMINAL and time.time() < deadline:
        time.sleep(3)
        status = client.calls(call.sid).fetch().status
    print(f"[call {call_index:02d}] finished with status={status}")
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, help="scenario id from scenarios.yaml")
    parser.add_argument("--index", type=int, default=1, help="call index for output filenames")
    args = parser.parse_args()
    place_call(args.scenario, args.index)


if __name__ == "__main__":
    main()

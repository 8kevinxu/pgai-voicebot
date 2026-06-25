.PHONY: install server tunnel run fetch analyze

install:
	pip install -r requirements.txt

# Run the bridge server by itself (for the smoke test or with --no-server batches).
server:
	uvicorn app.server:app --host 0.0.0.0 --port $${PORT:-8000}

# Convenience reminder for the public tunnel.
tunnel:
	ngrok http $${PORT:-8000}

# One command: boot server + place every scenario call.
run:
	python scripts/run_batch.py

# Pull clean dual-channel recordings from Twilio.
fetch:
	python scripts/fetch_recordings.py

# Draft the bug report from the transcripts.
analyze:
	python scripts/analyze.py

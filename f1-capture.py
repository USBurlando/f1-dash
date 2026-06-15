#!/usr/bin/env python3
"""
F1 Live Timing Capture
Replicates the f1-dash simulator 'save' mode in pure Python.
Saves one JSON per line (NDJSON) compatible with f1-dash replay.

Usage:
    python f1-capture.py barcelona_2026_race.ndjson
"""

import sys
import json
import asyncio
import signal
import uuid
from datetime import datetime
from pathlib import Path

try:
    import requests
    import websockets
except ImportError:
    print("Installing dependencies...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "websockets"])
    import requests
    import websockets

RECORD_SEP = "\x1e"
BASE_URL = "livetiming.formula1.com/signalrcore"

TOPICS = [
    "Heartbeat",
    "CarData.z",
    "Position.z",
    "ExtrapolatedClock",
    "TimingStats",
    "TimingAppData",
    "WeatherData",
    "TrackStatus",
    "SessionStatus",
    "DriverList",
    "RaceControlMessages",
    "SessionInfo",
    "SessionData",
    "LapCount",
    "TimingData",
    "TeamRadio",
    "ChampionshipPrediction",
]

HEADERS = {
    "User-Agent": "BestHTTP",
    "Accept-Encoding": "gzip, identity",
}

stop_event = asyncio.Event()


def negotiate():
    negotiate_url = f"https://{BASE_URL}/negotiate"

    # OPTIONS request to get the AWSALBCORS cookie
    options_res = requests.options(negotiate_url, headers=HEADERS)
    cookie = ""
    if "AWSALBCORS" in options_res.cookies:
        cookie = f"AWSALBCORS={options_res.cookies['AWSALBCORS']}"

    # POST negotiate
    headers = {**HEADERS}
    if cookie:
        headers["Cookie"] = cookie

    res = requests.post(
        negotiate_url,
        params={"negotiateVersion": "1"},
        headers=headers,
    )

    body = res.text.strip()
    if not body:
        raise RuntimeError(f"Empty negotiate response (HTTP {res.status_code})")
    if body == "Denied":
        raise RuntimeError("F1 returned 'Denied' — your IP may be blocked. Try a VPN or wait a bit.")

    data = res.json()
    token = data.get("connectionToken") or data.get("connectionId")
    return token, cookie


async def capture(output_path: Path):
    print(f"[{now()}] Negotiating with F1 SignalR...")
    token, cookie = negotiate()
    print(f"[{now()}] Negotiation OK, connecting WebSocket...")

    ws_url = f"wss://{BASE_URL}"
    if token:
        from urllib.parse import quote
        ws_url += f"?id={quote(token)}"

    extra_headers = {**HEADERS}
    if cookie:
        extra_headers["Cookie"] = cookie

    async with websockets.connect(ws_url, additional_headers=extra_headers) as ws:
        # Handshake
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEP)
        handshake = await ws.recv()
        parsed = json.loads(handshake.rstrip(RECORD_SEP))
        if "error" in parsed:
            raise RuntimeError(f"Handshake error: {parsed['error']}")
        print(f"[{now()}] Handshake OK")

        # Subscribe
        invocation_id = str(uuid.uuid4())
        subscribe_msg = json.dumps({
            "type": 1,
            "invocationId": invocation_id,
            "target": "Subscribe",
            "arguments": [TOPICS],
        }) + RECORD_SEP

        await ws.send(subscribe_msg)
        print(f"[{now()}] Subscribed to {len(TOPICS)} topics, waiting for initial state...")

        # Wait for completion (initial state snapshot)
        initial_state = None
        while initial_state is None:
            raw = await ws.recv()
            for frame in raw.split(RECORD_SEP):
                frame = frame.strip()
                if not frame:
                    continue
                msg = json.loads(frame)
                if msg.get("type") == 3 and msg.get("invocationId") == invocation_id:
                    if "error" in msg:
                        raise RuntimeError(f"Subscribe error: {msg['error']}")
                    initial_state = msg.get("result", {})

        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            # First line: initial state (same format as Rust save.rs)
            f.write(json.dumps(initial_state) + "\n")
            print(f"[{now()}] Initial state saved. Capturing live updates... (Ctrl+C to stop)\n")

            async for raw in ws:
                if stop_event.is_set():
                    break

                f.write(raw + "\n")
                count += 1

                # Parse and print a summary every 50 messages
                if count % 50 == 0:
                    topics_seen = set()
                    for frame in raw.split(RECORD_SEP):
                        frame = frame.strip()
                        if not frame:
                            continue
                        try:
                            msg = json.loads(frame)
                            if msg.get("target") == "feed" and msg.get("arguments"):
                                topics_seen.add(msg["arguments"][0])
                        except Exception:
                            pass
                    print(f"[{now()}] {count} messages captured | latest topics: {', '.join(topics_seen) or 'heartbeat'}")

        print(f"\n[{now()}] Done. {count} messages saved to {output_path}")


def now():
    return datetime.now().strftime("%H:%M:%S")


def main():
    if len(sys.argv) < 2:
        print("Usage: python f1-capture.py <output_file.ndjson>")
        sys.exit(1)

    output_path = Path(sys.argv[1])

    if output_path.exists():
        print(f"Error: {output_path} already exists. Choose a different filename.")
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    loop = asyncio.new_event_loop()

    def handle_sigint():
        print(f"\n[{now()}] Stopping capture...")
        stop_event.set()

    loop.add_signal_handler(signal.SIGINT, handle_sigint)

    try:
        loop.run_until_complete(capture(output_path))
    except RuntimeError as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        loop.close()


if __name__ == "__main__":
    main()

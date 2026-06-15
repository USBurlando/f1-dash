#!/usr/bin/env python3
import sys
import json
import asyncio
import uuid
from datetime import datetime
from pathlib import Path

try:
    import requests
    import websockets
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "websockets"])
    import requests
    import websockets

RECORD_SEP = "\x1e"
BASE_URL = "livetiming.formula1.com/signalrcore"
TOPICS = [
    "Heartbeat","CarData.z","Position.z","ExtrapolatedClock","TimingStats",
    "TimingAppData","WeatherData","TrackStatus","SessionStatus","DriverList",
    "RaceControlMessages","SessionInfo","SessionData","LapCount","TimingData",
    "TeamRadio","ChampionshipPrediction",
]
HEADERS = {"User-Agent": "BestHTTP", "Accept-Encoding": "gzip, identity"}

def now():
    return datetime.now().strftime("%H:%M:%S")

def negotiate():
    url = f"https://{BASE_URL}/negotiate"
    options_res = requests.options(url, headers=HEADERS)
    cookie = ""
    if "AWSALBCORS" in options_res.cookies:
        cookie = f"AWSALBCORS={options_res.cookies['AWSALBCORS']}"
    headers = {**HEADERS, **({"Cookie": cookie} if cookie else {})}
    res = requests.post(url, params={"negotiateVersion": "1"}, headers=headers)
    body = res.text.strip()
    if not body or body == "Denied":
        raise RuntimeError(f"Negotiate failed: {body or 'empty response'}")
    data = res.json()
    token = data.get("connectionToken") or data.get("connectionId")
    return token, cookie

async def capture(output_path: Path):
    print(f"[{now()}] Negotiating...")
    token, cookie = negotiate()
    print(f"[{now()}] OK, connecting WebSocket...")

    from urllib.parse import quote
    ws_url = f"wss://{BASE_URL}" + (f"?id={quote(token)}" if token else "")
    extra_headers = {**HEADERS, **({"Cookie": cookie} if cookie else {})}

    async with websockets.connect(ws_url, additional_headers=extra_headers) as ws:
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEP)
        handshake = await ws.recv()
        parsed = json.loads(handshake.rstrip(RECORD_SEP))
        if "error" in parsed:
            raise RuntimeError(f"Handshake error: {parsed['error']}")
        print(f"[{now()}] Handshake OK, subscribing...")

        inv_id = str(uuid.uuid4())
        await ws.send(json.dumps({
            "type": 1, "invocationId": inv_id,
            "target": "Subscribe", "arguments": [TOPICS],
        }) + RECORD_SEP)

        initial_state = None
        while initial_state is None:
            raw = await ws.recv()
            for frame in raw.split(RECORD_SEP):
                frame = frame.strip()
                if not frame:
                    continue
                msg = json.loads(frame)
                if msg.get("type") == 3 and msg.get("invocationId") == inv_id:
                    if "error" in msg:
                        raise RuntimeError(f"Subscribe error: {msg['error']}")
                    initial_state = msg.get("result", {})

        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(initial_state) + "\n")
            print(f"[{now()}] Initial state saved. Capturing... (Ctrl+C to stop)\n")
            try:
                async for raw in ws:
                    f.write(raw + "\n")
                    count += 1
                    if count % 50 == 0:
                        topics = set()
                        for frame in raw.split(RECORD_SEP):
                            frame = frame.strip()
                            if not frame:
                                continue
                            try:
                                msg = json.loads(frame)
                                if msg.get("target") == "feed" and msg.get("arguments"):
                                    topics.add(msg["arguments"][0])
                            except Exception:
                                pass
                        print(f"[{now()}] {count} msgs | {', '.join(topics) or 'heartbeat'}")
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass

        print(f"\n[{now()}] Done. {count} messages saved to {output_path}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python f1-capture.py <output.ndjson>")
        sys.exit(1)
    output_path = Path(sys.argv[1])
    if output_path.exists():
        print(f"Error: {output_path} already exists.")
        sys.exit(1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(capture(output_path))
    except RuntimeError as e:
        print(f"\nError: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

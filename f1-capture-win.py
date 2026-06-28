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

# Statuses that mean "a segment just ended" (could be end of Q1, end of session, etc.)
SEGMENT_END_STATUSES = ("Finished", "Inactive", "Finalised", "Ends", "Aborted")
# Statuses that mean "the whole session is fully done" — stop capturing entirely
SESSION_DONE_STATUSES = ("Finalised", "Ends")


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


class SegmentWriter:
    """Writes capture data to numbered segment files: base_seg1.ndjson, base_seg2.ndjson, ...
    Starts a new segment file every time SessionStatus goes Started -> Finished/Inactive -> Started again.
    If only one segment ever happens, the file is renamed to just base.ndjson at the end (no suffix).
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.stem = base_path.stem
        self.suffix = base_path.suffix
        self.parent = base_path.parent
        self.segment_num = 0
        self.file = None
        self.path = None
        self.total_count = 0
        self.segment_count = 0

    def _path_for(self, n):
        return self.parent / f"{self.stem}_seg{n}{self.suffix}"

    def start_segment(self, initial_state):
        if self.file:
            self.close_segment()
        self.segment_num += 1
        self.path = self._path_for(self.segment_num)
        self.file = open(self.path, "w", encoding="utf-8")
        self.file.write(json.dumps(initial_state) + "\n")
        self.segment_count = 0
        print(f"[{now()}] >>> Segment {self.segment_num} started -> {self.path.name}")

    def write(self, raw):
        if self.file:
            self.file.write(raw + "\n")
            self.segment_count += 1
            self.total_count += 1

    def close_segment(self):
        if self.file:
            self.file.close()
            print(f"[{now()}] <<< Segment {self.segment_num} closed: {self.segment_count} messages -> {self.path.name}")
            self.file = None

    def finalize(self):
        self.close_segment()
        if self.segment_num <= 1:
            # Only one segment total -> rename to the plain requested filename
            single_path = self._path_for(1)
            if single_path.exists():
                single_path.rename(self.base_path)
                print(f"[{now()}] Single segment renamed to {self.base_path.name}")


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

        writer = SegmentWriter(output_path)
        writer.start_segment(initial_state)

        # Track current/last SessionStatus to detect transitions.
        # IMPORTANT: we only treat a "Started" as the beginning of a NEW
        # segment if we have already seen at least one prior SessionStatus
        # update come through the LIVE feed (not the initial snapshot), and
        # that prior status was a genuine end-state. This avoids a false
        # split being triggered by the very first SessionStatus message of
        # the connection (which is just confirming the current/ongoing
        # session, not announcing a transition).
        last_status = None
        seen_any_status = False
        session_done = False

        print(f"[{now()}] Capturing... (Ctrl+C to stop manually at any time)\n")
        try:
            async for raw in ws:
                writer.write(raw)

                topics = set()
                for frame in raw.split(RECORD_SEP):
                    frame = frame.strip()
                    if not frame:
                        continue
                    try:
                        msg = json.loads(frame)
                        if msg.get("target") == "feed" and msg.get("arguments"):
                            topic, data = msg["arguments"][0], msg["arguments"][1]
                            topics.add(topic)

                            if topic == "SessionStatus":
                                status = data.get("Status") if isinstance(data, dict) else data

                                # Only split if we've already seen a previous
                                # status AND that previous status was a
                                # genuine end-state (not on the very first
                                # status message received).
                                if seen_any_status and status == "Started" and last_status in SEGMENT_END_STATUSES:
                                    writer.start_segment(initial_state={"SessionStatus": {"Status": "Started"}})

                                if status in SESSION_DONE_STATUSES:
                                    print(f"\n[{now()}] SessionStatus='{status}' — session fully ended, stopping in 10s...")
                                    session_done = True

                                last_status = status
                                seen_any_status = True
                    except Exception:
                        pass

                if writer.total_count % 50 == 0:
                    print(f"[{now()}] {writer.total_count} total msgs (segment {writer.segment_num}: {writer.segment_count}) | {', '.join(topics) or 'heartbeat'}")

                if session_done:
                    try:
                        for _ in range(10):
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            writer.write(raw)
                    except asyncio.TimeoutError:
                        pass
                    break
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

        writer.finalize()
        print(f"\n[{now()}] Done. {writer.total_count} total messages across {writer.segment_num} segment(s).")


def main():
    if len(sys.argv) < 2:
        print("Usage: python f1-capture-win.py <output.ndjson>")
        print("If the session has multiple parts (e.g. Quali Q1/Q2/Q3),")
        print("files will be saved as <output>_seg1.ndjson, _seg2.ndjson, etc.")
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

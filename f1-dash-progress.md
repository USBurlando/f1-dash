# f1-dash Local Setup & Data Capture — Progress Notes
**Session date:** June 14, 2026 — Spanish GP (Barcelona) race day  
**Last updated:** June 14, 2026 — full stack working with replay ✅  
**Developer:** Ulises, Tandil, Argentina  
**Repo:** https://github.com/slowlydev/f1-dash

---

## Goal
1. Run f1-dash locally
2. Capture live F1 telemetry during the Spanish GP to build a test dataset for offline development (so the simulator can be used when there's no live session)

---

## Data Source
f1-dash connects to **`livetiming.formula1.com/signalrcore`** — a public, unauthenticated SignalR endpoint run by Formula 1. No cost, no API key needed. The same source used by FastF1, OpenF1, etc.

Topics captured: `Heartbeat`, `CarData.z`, `Position.z`, `ExtrapolatedClock`, `TimingStats`, `TimingAppData`, `WeatherData`, `TrackStatus`, `SessionStatus`, `DriverList`, `RaceControlMessages`, `SessionInfo`, `SessionData`, `LapCount`, `TimingData`, `TeamRadio`, `ChampionshipPrediction`

`CarData.z` and `Position.z` are DEFLATE-compressed.

---

## Security Notes (from code review)
- Backend is Rust/Axum — memory-safe by design
- **CORS:** must set `ORIGIN` env var explicitly in compose.yaml, default is open
- **No authentication** on the UI — don't expose ports 3000/4000 outside localhost without a reverse proxy
- Docker images use `:latest` tag — consider pinning to SHA256 digest for production
- No rate limiting on the `realtime` service
- Run `npm audit` inside `dashboard/` before any public deployment

---

## What the Simulator Does
The repo includes a `simulator` crate with two modes:
- `save <file>` — connects to F1 live stream, saves to NDJSON (one JSON per line)
- `replay <file>` — replays saved file as a local WebSocket server, so `realtime` service thinks it's connected to F1

First line of the NDJSON = full initial state snapshot. Subsequent lines = raw WebSocket frames (deltas).

---

## Setup Journey & What Was Tried

### Docker approach — FAILED
- `docker build -t f1-simulator -f dockerfile --target simulator .`  
  → Failed: the main `dockerfile` has no `simulator` stage (only `api` and `realtime`)
- Created `dockerfile.simulator` (custom, builds only the simulator binary) — builds fine
- `docker run` failed with **`Error: Denied`** from F1's endpoint  
  → Docker Desktop on Windows uses a VM with datacenter-range IPs that F1 blocks

### Rust native — WORKING ✅
- `rustup` installed via `winget install Rustlang.Rustup`
- `cargo` not in PATH → fix: `$env:PATH += ";$env:USERPROFILE\.cargo\bin"`
- Compile failed: **`linker link.exe not found`** — needs Visual Studio Build Tools
- Tried GNU toolchain → failed: **`dlltool.exe not found`**
- Fixed by downloading Build Tools directly: `Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vs_buildtools.exe" -OutFile "$env:TEMP\vs_buildtools.exe"` then running with `--add Microsoft.VisualStudio.Workload.VCTools`
- Build Tools install confirmed: `Test-Path "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"` → True

### Python capture script — WORKING ✅
Since Rust wasn't ready during the live race, wrote a Python equivalent of the Rust `save` mode.

**File:** `f1-capture-win.py` (Windows-compatible, no `signal` module dependency)  
**Location:** repo root `C:\Users\Ulises\Documents\GitHub\f1-dash\`

Dependencies auto-install on first run: `requests`, `websockets`

```powershell
python f1-capture-win.py recordings/barcelona_2026_race_2.ndjson
```

Stop with Ctrl+C. Output is NDJSON compatible with f1-dash's native replay format.

#### Known issues fixed in the Windows version:
- `signal.add_signal_handler` → `NotImplementedError` on Windows → removed, Ctrl+C handled by asyncio directly
- Git Bash expands `/data` to `C:/Program Files/Git/data` → use PowerShell instead

---

## Code Changes Made to the Repo

### 1. `simulator/src/replay/server.rs` — MODIFIED
The original simulator only served `/ws`. It didn't implement the SignalR protocol properly, so the realtime service couldn't connect to it.

**Changes:**
- Added `POST /negotiate` endpoint returning fake connectionToken (required by signalr client before WS connect)
- Added `OPTIONS /negotiate` endpoint for CORS preflight
- Rewrote `handle_ws` to properly implement the SignalR protocol:
  1. Receives protocol handshake and responds with `{}\x1e`
  2. Waits for Subscribe invocation and captures `invocationId`
  3. Responds with COMPLETION (type 3) containing initial state from line 0 of recording
  4. Streams feed messages from recording with 100ms delay

### 2. `signalr/src/lib.rs` — MODIFIED
The original code always negotiated against F1's servers (`livetiming.formula1.com`), even when `F1_DEV_URL` was set. The dev URL was only used for the WebSocket connection, not the negotiate step.

**Changes:**
- Added `negotiate_http()` function that negotiates via `http://` instead of `https://` (for local simulator)
- Modified `create_client()` to check `F1_DEV_URL` first — if set, derives the negotiate base URL from the dev URL and negotiates against the local simulator

### 3. `dashboard/src/lib/map.ts` line 73 — BUGFIX
The `findYellowSectors` function called `.sort()` on `messages` which could be an object instead of an array when `RaceControlMessages` data comes in a non-array format.

**Fix:**
```typescript
// Before
const msgs = messages?.sort(sortUtc).filter((msg) => {
// After
const msgs = (Array.isArray(messages) ? [...messages] : []).sort(sortUtc).filter((msg) => {
```

---

## File Inventory

| File | Location | Purpose |
|---|---|---|
| `dockerfile.simulator` | repo root | Builds the simulator binary in Docker |
| `f1-capture-win.py` | repo root | Python capture script (Windows-compatible) |
| `recordings/barcelona_2026_race.ndjson` | repo root | First capture attempt (empty/partial — discard) |
| `recordings/barcelona_2026_race_2.ndjson` | repo root | ✅ Complete race capture — 35,062 messages, ~6MB |
| `simulator/src/replay/server.rs` | repo | Modified — proper SignalR protocol implementation |
| `signalr/src/lib.rs` | repo | Modified — F1_DEV_URL redirects negotiate to simulator |
| `dashboard/src/lib/map.ts` | repo | Bugfix — Array.isArray check for messages |

---

## Dataset Analysis — barcelona_2026_race_2.ndjson

**Result:** 35,062 messages, ~6MB, full race captured ✅

**Topic breakdown:**
| Topic | Messages | Notes |
|---|---|---|
| `TimingData` | 34,145 | Gaps, sectors, intervals — very rich |
| `TimingAppData` | 626 | Tyre stints, pit counts |
| `TimingStats` | 352 | Best sector times, top speeds |
| `Heartbeat` | 275 | Keep-alive |
| `RaceControlMessages` | 128 | Flags, penalties, VSC, SC events |
| `DriverList` | 93 | Driver/team updates |
| `WeatherData` | 61 | Temp, humidity, wind |
| `SessionData` | 50 | |
| `LapCount` | 40 | Current lap / total |
| `TeamRadio` | 29 | Audio clip URLs |
| `TrackStatus` | 8 | Green/SC/VSC/Red |
| `SessionStatus` | 2 | |
| `SessionInfo` | 1 | |
| `CarData.z` | **0** | ⚠️ Missing — see note below |
| `Position.z` | **0** | ⚠️ Missing — see note below |

### ⚠️ CarData.z / Position.z — Missing
**Conclusion:** Not transmitted by F1 post-race. Only active when cars are on track.  
**Fix for next time:** Connect before session start. FP1 of next GP.

---

## Running the Full Stack (WORKING ✅)

Open 4 PowerShell terminals in the repo root:

```powershell
# Terminal 1 — Simulator (start first, wait for "starting simulator replay server")
$env:RUST_LOG = "info"
$env:ADDRESS = "0.0.0.0:4000"
cargo run -p simulator -- replay recordings/barcelona_2026_race_2.ndjson

# Terminal 2 — Realtime service (start after Terminal 1 is ready)
$env:RUST_LOG = "info"
$env:ADDRESS = "0.0.0.0:4001"
$env:F1_DEV_URL = "ws://localhost:4000/ws"
$env:ORIGIN = "http://localhost:3001"
cargo run -p realtime

# Terminal 3 — API service
$env:RUST_LOG = "info"
$env:ADDRESS = "0.0.0.0:4010"
$env:ORIGIN = "http://localhost:3001"
cargo run -p api

# Terminal 4 — Dashboard
cd dashboard
$env:API_URL = "http://localhost:4010"
$env:NEXT_PUBLIC_LIVE_URL = "http://localhost:4001"
npm run dev
```

Open http://localhost:3001 in browser.

**Port map:**
| Port | Service |
|---|---|
| 4000 | Simulator (serves recording as fake F1 stream) |
| 4001 | Realtime service (processes and redistributes data) |
| 4010 | API service (historical data) |
| 3001 | Dashboard Next.js UI |

---

## Next Steps

### ✅ Completed this session
- [x] Captured full Spanish GP race — 35,062 messages, ~6MB
- [x] Confirmed dataset structure and topic distribution
- [x] Investigated missing CarData.z / Position.z
- [x] Installed Rust + Visual Studio Build Tools
- [x] Fixed simulator to implement proper SignalR protocol
- [x] Fixed signalr client to negotiate against local simulator
- [x] Fixed dashboard map.ts crash when Position.z data is missing
- [x] Full stack running with replay of Barcelona GP ✅

### Capture CarData.z / Position.z — Next GP
- Connect **before** session start (formation lap or earlier)
- Canadian GP is next — check schedule at https://www.formula1.com/en/racing/2026
```powershell
python f1-capture-win.py recordings/canada_2026_fp1.ndjson
```

---

## Useful Commands Cheatsheet

```powershell
# Add cargo to PATH without reopening PowerShell
$env:PATH += ";$env:USERPROFILE\.cargo\bin"

# Count messages in a recording
python -c "
from pathlib import Path
from collections import Counter
import json
lines = Path('recordings/barcelona_2026_race_2.ndjson').read_text(encoding='utf-8').splitlines()
counts = Counter()
for line in lines[1:]:
    for frame in line.split('\x1e'):
        frame = frame.strip()
        if not frame: continue
        try:
            msg = json.loads(frame)
            topic = msg.get('arguments', [None])[0]
            if topic: counts[topic] += 1
        except: pass
for topic, count in counts.most_common():
    print(f'{count:6d}  {topic}')
"

# Check if Build Tools installed
Test-Path "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"

# Force recompile if cargo thinks nothing changed
(Get-Item simulator\src\replay\server.rs).LastWriteTime = Get-Date
cargo build -p simulator

# Kill stale processes on a port
netstat -ano | findstr :4000
Stop-Process -Id <PID> -Force
```

### Scripts de arranque — COMPLETADO ✅
Tres archivos `.bat` creados en la raíz del repo:

| Script | Uso |
|---|---|
| `start-replay.bat` | Lista recordings disponibles, elige uno, abre 4 terminales y el browser |
| `start-live.bat` | Conecta directo a F1 en vivo, abre 3 terminales y el browser |
| `start-capture.bat` | Pide nombre de sesión y captura el stream en vivo a NDJSON |

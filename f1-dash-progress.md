# f1-dash Local Setup & Data Capture — Progress Notes
**Last updated:** June 28, 2026 — Austria GP race day, full pipeline validated end-to-end ✅  
**Developer:** Ulises, Tandil, Argentina  
**Fork:** https://github.com/USBurlando/f1-dash  
**Original repo:** https://github.com/slowlydev/f1-dash

---

## Goal
1. Run f1-dash locally
2. Capture live F1 telemetry to build test datasets for offline development
3. Replay recorded sessions without needing a live race

---

## Data Source
f1-dash connects to **`livetiming.formula1.com/signalrcore`** — a public, unauthenticated SignalR endpoint run by Formula 1. No cost, no API key needed.

### Topics available WITHOUT authentication
`Heartbeat`, `ExtrapolatedClock`, `TimingStats`, `TimingAppData`, `WeatherData`, `TrackStatus`, `SessionStatus`, `DriverList`, `RaceControlMessages`, `SessionInfo`, `SessionData`, `LapCount`, `TimingData`, `TeamRadio`, `ChampionshipPrediction`

### Topics that REQUIRE authentication (confirmed)
`CarData.z`, `Position.z` — these need an F1TV-authenticated token. Confirmed via FastF1's source code, which has a `no_auth` flag and warns that unauthenticated access "may only work for certain sessions or return incomplete data." The auth token is generated via an internal undocumented FastF1 module (`fastf1.internals.f1auth.get_auth_token`), likely requiring a real F1TV login.

**This does NOT block anything important** — see Track Map note below.

---

## 🎉 Key Discovery: Track Map doesn't need Position.z

Inspected `dashboard/src/components/dashboard/Map.tsx`. Line 115 has:
```js
// const positions = useDataStore((state) => state.positions);
```
This is commented out. The dashboard **calculates** car positions via `getDriverPosition()`, interpolating progress through `TimingData.Sectors[].Segments[].Status` (which segment of the track each car has completed). This data comes from `TimingData`, which we capture successfully without auth.

**Conclusion:** The Track Map works perfectly with unauthenticated data. `CarData.z` would only matter for a future raw-telemetry feature (speed traces, RPM, throttle) — not used anywhere currently.

Verified: cars visibly moved on the local dashboard's Track Map during Austria Q1 capture, despite Position.z being absent — confirms the interpolation approach works.

---

## Security Notes (from code review)
- Backend is Rust/Axum — memory-safe by design
- **CORS:** must set `ORIGIN` env var explicitly, default is open
- **No authentication** on the UI — don't expose ports 3000/4000 outside localhost without a reverse proxy
- Docker images use `:latest` tag — consider pinning to SHA256 digest for production
- No rate limiting on the `realtime` service
- Run `npm audit` inside `dashboard/` before any public deployment

---

## Code Changes Made to the Repo (all committed, PR-ready)

### 1. `simulator/src/replay/server.rs` — MODIFIED
Original simulator only served `/ws`, no SignalR negotiation. Added:
- `POST /negotiate` + `OPTIONS /negotiate` endpoints (fake token, CORS headers)
- Rewrote `handle_ws`: handshake ack → wait for Subscribe → COMPLETION with initial state (line 0 of recording) → stream feed messages with 100ms delay

### 2. `signalr/src/lib.rs` — MODIFIED
`F1_DEV_URL` only redirected the WebSocket, not the negotiate step (which always hit F1's real servers). Added `negotiate_http()` and updated `create_client()` to negotiate against the local simulator via `http://` when `F1_DEV_URL` is set.

### 3. `dashboard/src/lib/map.ts` — BUGFIX
`findYellowSectors` crashed calling `.sort()` on `messages` when it arrived as an object instead of array.
```typescript
// Before: const msgs = messages?.sort(sortUtc).filter(...)
// After:  const msgs = (Array.isArray(messages) ? [...messages] : []).sort(sortUtc).filter(...)
```

### 4. `.gitattributes` — ADDED
`*.lock text eol=lf` to prevent yarn.lock CRLF noise in diffs on Windows.

### 5. New files
- `dockerfile.simulator` — standalone Dockerfile for the simulator binary
- `start-replay.bat` — lists recordings, lets you pick one, launches all 4 services + browser
- `start-live.bat` — launches realtime+api+dashboard connected directly to F1
- `start-capture.bat` — prompts for session name, runs the Python capture script
- `f1-capture-win.py` — Windows-compatible capture script (Python, no Rust needed)

**Status:** Branch `feat/local-simulator-replay` pushed to fork, PR opened against `main`.

---

## Bugs Found & Fixed Today

### Bug 1 — start-live.bat / start-replay.bat: trailing space in env vars
`set VAR=valor && next_command` in Windows batch includes the trailing space before `&&` as part of the variable's value. This caused `NEXT_PUBLIC_LIVE_URL` to end up as `"http://localhost:4001 "` (with a space), breaking the EventSource URL in the dashboard (`Failed to construct 'EventSource': ... URL is invalid`).
**Fix:** removed spaces around `&&` in all `set` lines within the .bat scripts.

### Bug 2 — f1-capture-win.py: false segment split on first SessionStatus message
When implementing Q1/Q2/Q3 auto-splitting (segments end on `Finished`/`Inactic"/`Finalised`/`Ends`/`Aborted` and a new one starts on the next `Started`), the initial `last_status` tracking was buggy: it sometimes treated the very FIRST `SessionStatus` message received from the live feed as if it followed an end-state, triggering an immediate false split.

**Symptom:** Austria race capture produced 2 segments instead of 1 — `seg1` had only ~1236 lines (just the connection startup) and `seg2` had the actual ~130,561-line race.

**Fix:** added a `seen_any_status` flag. A "Started" status can only trigger a new segment if we've already seen at least one PRIOR status from the live feed (not the initial snapshot) that was itself a genuine end-state. The very first status message of a connection can never trigger a split, regardless of its value.

### Bug 3 — PowerShell Get-Content silently truncates large NDJSON files
`Get-Content recordings\austria_2026_race_seg2.ndjson` reported **64,968 lines**, while the actual file has **130,561 lines** (confirmed via Python). PowerShell's text-mode line reading appears to mishandle the file — likely related to the `\x1e` (record separator, ASCII 0x1E) character present throughout SignalR frames, which may be misinterpreted as a control/EOF-like character by PowerShell's line-splitting logic.

**Impact:** when concatenating segments using `Get-Content` + `Set-Content` in PowerShell, exactly half the lines of seg2 were silently dropped — no error, no warning.

**Fix:** NEVER use `Get-Content`/`Set-Content` for these NDJSON files. Always process them in Python:
```powershell
python -c "
from pathlib import Path
seg1 = Path('recordings/X_seg1.ndjson').read_text(encoding='utf-8').splitlines()
seg2 = Path('recordings/X_seg2.ndjson').read_text(encoding='utf-8').splitlines()
snapshot = seg1[0]
all_lines = [snapshot] + seg1[1:] + seg2
Path('recordings/X.ndjson').write_text('\n'.join(all_lines) + '\n', encoding='utf-8')
"
```
**Lesson:** Any line-counting or manipulation of .ndjson recordings should always go through Python, never PowerShell's native cmdlets, to avoid silent data loss.

---

## Dataset Inventory

| File | Session | Messages | Notes |
|---|---|---|---|
| `barcelona_2026_race_2.ndjson` | Spanish GP — Race | 35,062 | First successful capture, post-race connection |
| `austria_2026_q1.ndjson` | Austrian GP — Quali Q1 only | 13,542 | Connected mid-Q1, manually stopped with Ctrl+C |
| `austria_2026_race.ndjson` | Austrian GP — Race | 131,797 | Captured live from before start; originally split into 2 segments due to Bug 2, manually reconciled |

All recordings: `CarData.z` / `Position.z` = 0 messages (auth required, see above). All other topics present and rich. Track Map works fine via TimingData-based interpolation.

---

## Running the Full Stack

### Quick start via .bat scripts (recommended)
```
start-replay.bat   → pick a recording, launches everything + opens browser
start-live.bat     → connects directly to F1, launches everything + opens browser
start-capture.bat  → prompts for session name, captures live stream to NDJSON
                      (auto-splits into _seg1/_seg2/... if session has multiple
                       parts like Quali Q1/Q2/Q3; auto-stops 10s after Finalised/Ends)
```

### Manual setup (4 terminals)
```powershell
# Terminal 1 — Simulator (replay mode only — skip for live)
$env:RUST_LOG = "info"
$env:ADDRESS = "0.0.0.0:4000"
cargo run -p simulator -- replay recordings/austria_2026_race.ndjson

# Terminal 2 — Realtime
$env:RUST_LOG = "info"
$env:ADDRESS = "0.0.0.0:4001"
$env:F1_DEV_URL = "ws://localhost:4000/ws"   # omit this line for live mode
$env:ORIGIN = "http://localhost:3001"
cargo run -p realtime

# Terminal 3 — API
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

**Port map:**
| Port | Service |
|---|---|
| 4000 | Simulator (replay mode only) |
| 4001 | Realtime service |
| 4010 | API service |
| 3001 | Dashboard (3000 often taken by another process on this machine) |

---

## Git Workflow Used

```powershell
# Disconnected from upstream, working only against own fork
git remote set-url origin https://github.com/USBurlando/f1-dash.git
git remote remove upstream  # if present

git checkout -b feat/local-simulator-replay
git add <files>
git commit -m "feat: local simulator replay + Windows tooling"
git push -u origin feat/local-simulator-replay
# PR opened at: https://github.com/USBurlando/f1-dash/compare/main...feat/local-simulator-replay
```

---

## Next Steps

### Possible future work
- [ ] Add `stop-all.bat` to kill all 4 services in one command
- [ ] Replay speed selector (0.5x / 1x / 2x / 5x) in simulator
- [ ] Index recordings by session metadata for quick lookup
- [ ] Investigate F1TV auth flow if CarData.z (raw telemetry) becomes needed for a future feature
- [ ] Consider adding basic auth to dashboard for LAN exposure

### Useful one-liners
```powershell
# Topic breakdown of any recording
python -c "
from pathlib import Path
from collections import Counter
import json
lines = Path('recordings/austria_2026_race.ndjson').read_text(encoding='utf-8').splitlines()
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

# Correct line count for any .ndjson (NEVER use Get-Content for this — see Bug 3)
python -c "from pathlib import Path; print(len(Path('recordings/FILE.ndjson').read_text(encoding='utf-8').splitlines()))"

# Add cargo to PATH without reopening PowerShell
$env:PATH += ";$env:USERPROFILE\.cargo\bin"

# Force recompile if cargo thinks nothing changed
cargo clean -p <crate>
cargo build -p <crate>

# Kill stale processes on a port
netstat -ano | findstr :4000
Stop-Process -Id <PID> -Force

# Check Build Tools installed
Test-Path "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
```

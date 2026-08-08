# TouchDesigner Split-Flap Board

A second display client that **uses the same message queue** as the web UI (`board.html`).  
It registers over WebSocket as another board, receives `play_public_message` / `clear_public_message`, layouts text on a **96×5** grid (same rules as the site), and reports `public_message_done` when display time ends.

Nothing on the server has to change. The web board and TouchDesigner can run at the same time; both receive the same play events.

```
  submit / moderator
         │
         ▼
     server.js  ──WebSocket broadcast──►  board.html (web)
                      │
                      └──►  TouchDesigner (this client)
```

---

## What you get

| Path | Purpose |
|------|---------|
| `python/layout.py` | Same wrap / autocenter / color-block layout as `board.html` |
| `python/protocol.py` | Board WebSocket protocol + display timer |
| `python/SplitflapBoardExt.py` | TouchDesigner Extension (COMP) |
| `python/standalone_test.py` | CLI client to verify the socket without TD |
| `assets/` | `ZYN_WIC_Logo.png`, `dc-34-logo.png` for side panels |
| `config.example.json` | Sample host settings |

---

## Protocol (same as web)

**Connect** to `wss://<host>` (or `ws://localhost:3000` locally).

**On open, send:**
```json
{ "type": "register_board", "name": "touchdesigner" }
```

**Server may send:**
```json
{ "type": "registered" }

{ "type": "play_public_message",
  "id": "…",
  "text": "WHERE\n\nMEETS\n",
  "durationMs": 20000,
  "indefinite": false,
  "autocenter": true,
  "urgent": false }

{ "type": "clear_public_message", "id": "…", "urgent": true }
```

**When your display time ends, send:**
```json
{ "type": "public_message_done", "id": "…" }
```

Multiple boards are fine. The first `public_message_done` for an id advances the queue; later ones are ignored.

---

## Quick test (no TouchDesigner)

```bash
cd touchdesigner/python
pip install websocket-client
python3 standalone_test.py wss://splitflap-olvv.onrender.com
```

Then post a message from the moderator UI. You should see the grid print in the terminal.

---

## TouchDesigner setup (native grid)

Tested against TouchDesigner 2022 / 2023-style operators. Names of a few parameters vary by build; adjust if needed.

### 1. Create the container

1. Create a **Container COMP** → rename to `splitflap_board`.
2. On the container: **Extensions** → **Extension Object 1** → point at  
   `touchdesigner/python/SplitflapBoardExt.py`  
   Extension Name: `SplitflapBoardExt`  
   Promote Extension: optional.
3. Re-init extensions (right-click container → Re-Init Extensions).

### 2. Build the child network

In a Textport DAT or the container’s Python:

```python
op('splitflap_board').ext.SplitflapBoardExt.BuildNetwork()
```

This creates (if missing):

- `websocket1` — WebSocket DAT  
- `grid_text` — Text DAT (96×5 lines)  
- `status` — Text DAT (connection / now playing)  
- `grid_table` — Table DAT (optional per-cell)  
- `board_text` — Text TOP (monospace board look)  
- `zyn_logo` / `dc34_logo` — Movie File In TOPs  
- `out1` — Null TOP  
- `ws_callbacks` — sample callback code  

And custom parameters: **Host**, **Use TLS**, **Client Name**, **Cols**, **Rows**, **Active**.

### 3. Wire WebSocket callbacks

Open `websocket1` → **Callbacks** page. Paste from `ws_callbacks` (or):

```python
def onConnect(dat):
	parent().ext.SplitflapBoardExt.OnWsConnect()

def onDisconnect(dat):
	parent().ext.SplitflapBoardExt.OnWsDisconnect()

def onReceive(dat, rowIndex, message, bytes):
	parent().ext.SplitflapBoardExt.OnWsReceiveMessage(message)

def onError(dat, errorCode, errorMsg):
	print('[splitflap ws error]', errorCode, errorMsg)
```

### 4. Connect

1. Set **Host** to your server, e.g. `splitflap-olvv.onrender.com`  
   (local: `localhost:3000` and turn **Use TLS** off).
2. Toggle **Active** on, or call:

```python
op('splitflap_board').ext.SplitflapBoardExt.Connect()
```

3. Watch `status` DAT → should show `registered`.
4. Approve/post a message in the web moderator → `grid_text` / `board_text` update.

### 5. Layout like the web UI (suggested)

Match the web stage: **ZYN (left) | board (center) | DC34 + QR (right)**.

```
[zyn_logo]  [board_text]  [dc34_logo]
                 │
               out1  →  Window COMP / perform mode
```

Suggested proportions (same idea as the web canvas):

| Region | Relative width |
|--------|----------------|
| ZYN panel | ~9% (was 720 of ~7600) |
| Board | ~75% |
| DC34 panel | ~16% (was 1200) |

Tips:

- **board_text**: black background, light gray/white monospace, no word wrap, high resolution (e.g. 3840×400 or full perform size).
- **zyn_logo** / **dc34_logo**: `Fit` = fit outside/inside, pre-multiplied if needed; use files under `touchdesigner/assets/`.
- QR code is **not** generated in TD (web board still shows it). To mirror QR, either screenshot, or open the same submit URL as a QR TOP if you have a generator.

### 6. Optional: pixel-perfect web UI inside TD

If you want the **exact** CSS split-flap look (flap animation, QR, etc.):

1. Add a **Web Render TOP**.
2. URL: `https://splitflap-olvv.onrender.com/board.html`  
   (or local `http://localhost:3000/board.html`).
3. Size to your output resolution.

That loads the real board page (it opens its **own** WebSocket). You then have two boards (web render + optional native). Prefer **either** native client **or** Web Render, not both, unless you want dual registration.

---

## Layout parity with the web

| Feature | Web | TD client |
|---------|-----|-----------|
| Columns × rows | 96 × 5 | 96 × 5 (params) |
| Autocenter | yes | yes (`autocenter` flag) |
| Color blocks 🟥🟧… | yes | in grid text / `layout.COLOR_MAP` |
| Emoji | yes | best-effort |
| Flap animation | CSS 3D | instant text update (extend with Geo COMP if desired) |
| Display duration | server `durationMs` | same timer → `public_message_done` |
| Side logos | HTML panels | Movie File In TOPs + assets |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Never `registered` | Check Host / TLS; Render free tier may cold-start — hit `/api/health` first |
| Connect then drop | Corporate firewall blocking WSS; try local server |
| Messages show on web only | TD not registered; confirm `status` DAT and Active |
| Queue advances too early | Two boards both finishing; OK by design — first done wins |
| Submit says board offline | At least one board (web or TD) must be connected |
| Import errors on Extension | Add `touchdesigner/python` to TD’s Python path or keep Extension Object path absolute |

---

## Development

```bash
# layout unit smoke check
cd touchdesigner/python
python3 -c "
from layout import message_to_grid, grid_to_table_text
g = message_to_grid('Where\\n\\nMeets\\n', autocenter=True)
print(grid_to_table_text(g))
print(len(g), 'cells')
"
```

---

## License

Same as the main project (ISC). ZYN / DEF CON marks are third-party; use only as you are licensed to display them.

"""
TouchDesigner Extension — Splitflap Board display.

Attach this to a Container COMP named `splitflap_board` (or any COMP) that has:

  Custom parameters (create on the COMP):
    Host          (str)   e.g. splitflap-olvv.onrender.com
    UseTls        (toggle) default On
    ClientName    (str)   touchdesigner
    Cols          (int)   96
    Rows          (int)   5
    Active        (toggle) connect when on

  Child operators (created by BuildNetwork() or manually — see README):
    websocket1          WebSocket DAT
    grid_text           Text DAT  (display rows)
    status              Text DAT  (connection / now playing)
    board_text          Text TOP  (optional visual)
    zyn_logo            Movie File In TOP
    dc34_logo           Movie File In TOP
    out1                Out TOP   (final composite)

Usage in TD:
  1. Create Container COMP → Extensions → add this file as Extension Object
  2. Run:  op('splitflap_board').ext.SplitflapBoardExt.BuildNetwork()
  3. Set Host custom par, toggle Active

WebSocket DAT callbacks should call:
  op('splitflap_board').ext.SplitflapBoardExt.OnWsConnect()
  op('splitflap_board').ext.SplitflapBoardExt.OnWsReceive(dat, rowIndex)
  op('splitflap_board').ext.SplitflapBoardExt.OnWsDisconnect()
"""

from __future__ import annotations

import json
import os
import sys

# Ensure sibling modules import when TD loads this file by path
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

try:
    import layout
    import protocol
except ImportError:
    # TD sometimes loads from a copy; try relative package style
    from touchdesigner.python import layout, protocol  # type: ignore


class SplitflapBoardExt:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp
        self.client: protocol.SplitflapBoardClient | None = None
        self._ensure_client()

    # ── parameters ───────────────────────────────────────────────────

    def _par(self, name, default=None):
        p = getattr(self.ownerComp.par, name, None)
        if p is None:
            return default
        return p.eval()

    def _host(self) -> str:
        return str(self._par("Host", "splitflap-olvv.onrender.com") or "")

    def _use_tls(self) -> bool:
        return bool(self._par("Usetls", True) if hasattr(self.ownerComp.par, "Usetls") else self._par("UseTls", True))

    def _client_name(self) -> str:
        return str(self._par("Clientname", "touchdesigner") or "touchdesigner")

    def _cols(self) -> int:
        return int(self._par("Cols", 96) or 96)

    def _rows(self) -> int:
        return int(self._par("Rows", 5) or 5)

    # ── client ───────────────────────────────────────────────────────

    def _ensure_client(self):
        def on_state(state: protocol.BoardState):
            self._push_state(state)

        def send_json(payload: dict):
            ws = self.ownerComp.op("websocket1")
            if ws is None:
                return
            # WebSocket DAT: sendText (TD 2022+) or send
            data = json.dumps(payload, separators=(",", ":"))
            if hasattr(ws, "sendText"):
                ws.sendText(data)
            elif hasattr(ws, "send"):
                ws.send(data)
            else:
                # fallback par pulse patterns vary by build
                try:
                    ws.par.send.val = data
                    ws.par.send.pulse()
                except Exception:
                    pass

        self.client = protocol.SplitflapBoardClient(
            cols=self._cols(),
            rows=self._rows(),
            name=self._client_name(),
            on_state=on_state,
            send_json=send_json,
            layout_module=layout,
        )

    def _push_state(self, state: protocol.BoardState):
        grid = self.ownerComp.op("grid_text")
        if grid is not None:
            grid.text = state.grid_text or ""

        status = self.ownerComp.op("status")
        if status is not None:
            lines = [
                f"status: {state.status}",
                f"connected: {state.connected}",
                f"registered: {state.registered}",
                f"id: {state.current_id or '-'}",
                f"duration_ms: {state.current_duration_ms}",
                f"indefinite: {state.current_indefinite}",
                f"autocenter: {state.current_autocenter}",
                f"event: {state.last_event}",
                f"error: {state.last_error or '-'}",
                "----",
                state.current_text or "(blank)",
            ]
            status.text = "\n".join(lines)

        # Optional: Table DAT cells for per-cell coloring
        table = self.ownerComp.op("grid_table")
        if table is not None and state.grid_text is not None:
            rows = state.grid_text.split("\n")
            # clear + fill
            table.clear()
            for r, row in enumerate(rows):
                chars = list(row)
                while len(chars) < self._cols():
                    chars.append(" ")
                table.appendRow(chars[: self._cols()])

    # ── WebSocket DAT callbacks (wire in DAT callbacks page) ─────────

    def OnWsConnect(self):
        self._ensure_client()
        assert self.client
        self.client.cols = self._cols()
        self.client.rows = self._rows()
        self.client.name = self._client_name()
        self.client.handle_open()

    def OnWsDisconnect(self):
        if self.client:
            self.client.handle_close()

    def OnWsReceive(self, dat=None, rowIndex=None):
        """
        Call from WebSocket DAT callback onReceive.

        In TD WebSocket DAT callbacks:
          def onReceive(dat, rowIndex, message, bytes):
              parent().ext.SplitflapBoardExt.OnWsReceiveMessage(message)
        """
        if dat is None:
            return
        # Prefer last cell in the receive table
        try:
            if rowIndex is not None:
                msg = dat[rowIndex, 0].val
            else:
                msg = dat[dat.numRows - 1, 0].val if dat.numRows else ""
        except Exception:
            msg = ""
        if msg and self.client:
            self.client.handle_message(msg)

    def OnWsReceiveMessage(self, message: str):
        if self.client and message:
            self.client.handle_message(message)

    # ── lifecycle ────────────────────────────────────────────────────

    def Connect(self):
        """Configure WebSocket DAT and activate."""
        self._ensure_client()
        ws = self.ownerComp.op("websocket1")
        if ws is None:
            debug("[splitflap] missing websocket1 DAT — run BuildNetwork()")
            return
        url = protocol.make_ws_url(self._host(), use_tls=self._use_tls())
        # Parameter names differ slightly across TD versions
        for addr_par in ("address", "netaddress", "url"):
            if hasattr(ws.par, addr_par):
                getattr(ws.par, addr_par).val = url
                break
        if hasattr(ws.par, "active"):
            ws.par.active = True
        if hasattr(ws.par, "reconnect"):
            ws.par.reconnect = True
        debug(f"[splitflap] connecting to {url}")

    def Disconnect(self):
        ws = self.ownerComp.op("websocket1")
        if ws is not None and hasattr(ws.par, "active"):
            ws.par.active = False
        if self.client:
            self.client.handle_close()

    def PulseActive(self):
        """Call when Active custom parameter changes."""
        if self._par("Active", False):
            self.Connect()
        else:
            self.Disconnect()

    def FinishCurrent(self):
        """Manual end of current message (sends public_message_done)."""
        if self.client:
            self.client.finish_message()

    def ClearLocal(self):
        if self.client:
            self.client._clear(None, urgent=True)

    # ── one-shot network builder ─────────────────────────────────────

    def BuildNetwork(self):
        """
        Create child operators for a working board shell.
        Safe to re-run; skips existing ops.
        """
        root = self.ownerComp

        def ensure(op_type, name, **pars):
            existing = root.op(name)
            if existing:
                return existing
            # TD create: root.create(opType, name)
            try:
                o = root.create(op_type, name)
            except Exception as e:
                debug(f"[splitflap] could not create {op_type} {name}: {e}")
                return None
            for k, v in pars.items():
                if hasattr(o.par, k):
                    try:
                        getattr(o.par, k).val = v
                    except Exception:
                        pass
            return o

        # Custom parameters on container
        page = None
        try:
            if not hasattr(root.par, "Host"):
                page = root.appendCustomPage("Splitflap")
                page.appendStr("Host", label="Host")
                root.par.Host = "splitflap-olvv.onrender.com"
                page.appendToggle("Usetls", label="Use TLS (wss)")
                root.par.Usetls = True
                page.appendStr("Clientname", label="Client Name")
                root.par.Clientname = "touchdesigner"
                page.appendInt("Cols", label="Columns")
                root.par.Cols = 96
                page.appendInt("Rows", label="Rows")
                root.par.Rows = 5
                page.appendToggle("Active", label="Active")
                root.par.Active = False
                page.appendPulse("Connect", label="Connect")
                page.appendPulse("Disconnect", label="Disconnect")
        except Exception as e:
            debug(f"[splitflap] custom pars: {e}")

        ensure("websocketDAT", "websocket1")
        ensure("textDAT", "grid_text")
        ensure("textDAT", "status")
        ensure("tableDAT", "grid_table")
        ensure("textTOP", "board_text")
        ensure("moviefileinTOP", "zyn_logo")
        ensure("moviefileinTOP", "dc34_logo")
        ensure("nullTOP", "out1")

        # Point logos at assets if present
        assets = os.path.abspath(os.path.join(_THIS_DIR, "..", "assets"))
        zyn = os.path.join(assets, "ZYN_WIC_Logo.png")
        dc = os.path.join(assets, "dc-34-logo.png")
        zop = root.op("zyn_logo")
        dop = root.op("dc34_logo")
        if zop and os.path.isfile(zyn) and hasattr(zop.par, "file"):
            zop.par.file = zyn
        if dop and os.path.isfile(dc) and hasattr(dop.par, "file"):
            dop.par.file = dc

        # Wire board_text to grid_text
        bt = root.op("board_text")
        gt = root.op("grid_text")
        if bt is not None and gt is not None:
            try:
                if hasattr(bt.par, "dat"):
                    bt.par.dat = gt
                elif hasattr(bt.par, "text"):
                    # expression
                    bt.par.text.expr = "op('grid_text').text"
                # styling
                if hasattr(bt.par, "fontmonospace"):
                    bt.par.fontmonospace = True
                if hasattr(bt.par, "font"):
                    bt.par.font = "Roboto Mono"
                if hasattr(bt.par, "bgcolorr"):
                    bt.par.bgcolorr = 0
                    bt.par.bgcolorg = 0
                    bt.par.bgcolorb = 0
                    bt.par.bgalpha = 1
                if hasattr(bt.par, "fontcolorr"):
                    bt.par.fontcolorr = 0.93
                    bt.par.fontcolorg = 0.93
                    bt.par.fontcolorb = 0.94
            except Exception as e:
                debug(f"[splitflap] board_text wire: {e}")

        # WebSocket callbacks script
        cb = ensure("textDAT", "ws_callbacks")
        if cb is not None:
            cb.text = '''# Paste into websocket1 → Callbacks, or execute via DAT Execute

def onConnect(dat):
	parent().ext.SplitflapBoardExt.OnWsConnect()

def onDisconnect(dat):
	parent().ext.SplitflapBoardExt.OnWsDisconnect()

def onReceive(dat, rowIndex, message, bytes):
	parent().ext.SplitflapBoardExt.OnWsReceiveMessage(message)

def onError(dat, errorCode, errorMsg):
	print('[splitflap ws error]', errorCode, errorMsg)
'''
            debug("[splitflap] wrote ws_callbacks — copy into websocket1 Callbacks page")

        # Parameter execute for Active / Connect / Disconnect
        ensure("parexecDAT", "par_exec")

        debug("[splitflap] BuildNetwork complete. Set Host, enable Active, copy ws_callbacks into websocket1.")
        return True

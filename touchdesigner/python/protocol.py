"""
WebSocket protocol client for splitflap boards.

Same messages as public/board.html:
  send:  { "type": "register_board" }
  recv:  { "type": "registered" }
  recv:  { "type": "play_public_message", id, text, durationMs, indefinite, autocenter, urgent }
  recv:  { "type": "clear_public_message", id, urgent }
  send:  { "type": "public_message_done", id }

Works outside TouchDesigner with the `websocket-client` package, or as a pure
state machine driven by TD's WebSocket DAT callbacks.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class BoardState:
    connected: bool = False
    registered: bool = False
    current_id: Optional[str] = None
    current_text: str = ""
    current_indefinite: bool = False
    current_duration_ms: int = 0
    current_autocenter: bool = False
    grid_text: str = ""  # newline-joined rows for display
    status: str = "disconnected"
    last_error: str = ""
    last_event: str = ""
    last_event_at: float = 0.0
    extras: Dict[str, Any] = field(default_factory=dict)


class SplitflapBoardClient:
    """
    Protocol + display timer. Transport is pluggable:

      client = SplitflapBoardClient(on_state=..., send_json=ws_send)
      client.handle_open()
      client.handle_message(raw)
      client.handle_close()
    """

    def __init__(
        self,
        *,
        cols: int = 96,
        rows: int = 5,
        name: str = "touchdesigner",
        default_display_ms: int = 20000,
        on_state: Optional[Callable[[BoardState], None]] = None,
        send_json: Optional[Callable[[dict], None]] = None,
        layout_module=None,
    ):
        self.cols = cols
        self.rows = rows
        self.name = name
        self.default_display_ms = default_display_ms
        self.on_state = on_state
        self.send_json = send_json
        self._layout = layout_module
        self.state = BoardState()
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

        if self._layout is None:
            try:
                from . import layout as layout_module  # type: ignore

                self._layout = layout_module
            except Exception:
                import layout as layout_module  # type: ignore

                self._layout = layout_module

    # ── transport hooks ──────────────────────────────────────────────

    def set_send(self, send_json: Callable[[dict], None]) -> None:
        self.send_json = send_json

    def _emit(self) -> None:
        if self.on_state:
            try:
                self.on_state(self.state)
            except Exception as e:
                self.state.last_error = str(e)

    def _send(self, payload: dict) -> None:
        if not self.send_json:
            return
        try:
            self.send_json(payload)
        except Exception as e:
            self.state.last_error = f"send failed: {e}"
            self._emit()

    def handle_open(self) -> None:
        self.state.connected = True
        self.state.status = "connected"
        self.state.last_event = "open"
        self.state.last_event_at = time.time()
        self._send({"type": "register_board", "name": self.name})
        self._emit()

    def handle_close(self) -> None:
        self._cancel_timer()
        self.state.connected = False
        self.state.registered = False
        self.state.status = "disconnected"
        self.state.last_event = "close"
        self.state.last_event_at = time.time()
        self._emit()

    def handle_message(self, raw: str | bytes) -> None:
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            msg = json.loads(raw)
        except Exception as e:
            self.state.last_error = f"bad message: {e}"
            self._emit()
            return

        if not isinstance(msg, dict) or "type" not in msg:
            return

        t = msg.get("type")
        self.state.last_event = t
        self.state.last_event_at = time.time()

        if t == "registered":
            self.state.registered = True
            self.state.status = "registered"
            self._emit()
            return

        if t == "play_public_message" and isinstance(msg.get("text"), str):
            self._play(
                text=msg["text"],
                msg_id=msg.get("id"),
                duration_ms=int(msg.get("durationMs") or 0),
                indefinite=bool(msg.get("indefinite")),
                autocenter=bool(msg.get("autocenter")),
            )
            return

        if t == "clear_public_message":
            self._clear(msg.get("id"), urgent=bool(msg.get("urgent")))
            return

        # ignore other types

    # ── display ──────────────────────────────────────────────────────

    def _cancel_timer(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _set_grid_from_text(self, text: str, autocenter: bool) -> None:
        grid = self._layout.message_to_grid(
            text, cols=self.cols, rows=self.rows, autocenter=autocenter
        )
        self.state.grid_text = self._layout.grid_to_table_text(
            grid, cols=self.cols, rows=self.rows
        )

    def _blank(self) -> None:
        grid = self._layout.blank_grid(self.cols, self.rows)
        self.state.grid_text = self._layout.grid_to_table_text(
            grid, cols=self.cols, rows=self.rows
        )
        self.state.current_text = ""
        self.state.current_id = None
        self.state.current_indefinite = False
        self.state.current_duration_ms = 0
        self.state.current_autocenter = False

    def _play(
        self,
        text: str,
        msg_id: Optional[str],
        duration_ms: int,
        indefinite: bool,
        autocenter: bool,
    ) -> None:
        self._cancel_timer()
        self.state.current_id = msg_id
        self.state.current_text = text
        self.state.current_indefinite = indefinite
        self.state.current_autocenter = autocenter
        ms = 0 if indefinite else (duration_ms if duration_ms > 0 else self.default_display_ms)
        self.state.current_duration_ms = ms
        self.state.status = "playing"
        self._set_grid_from_text(text, autocenter)
        self._emit()

        if not indefinite and ms > 0 and msg_id:
            def _done(done_id=msg_id):
                self.finish_message(done_id)

            with self._lock:
                self._timer = threading.Timer(ms / 1000.0, _done)
                self._timer.daemon = True
                self._timer.start()

    def _clear(self, msg_id: Optional[str], urgent: bool = False) -> None:
        if (
            not urgent
            and msg_id
            and self.state.current_id
            and msg_id != self.state.current_id
        ):
            return
        self._cancel_timer()
        self._blank()
        self.state.status = "cleared" if self.state.registered else self.state.status
        self._emit()

    def finish_message(self, msg_id: Optional[str] = None) -> None:
        """Call when display time ends (or TD animation completes)."""
        mid = msg_id or self.state.current_id
        if not mid:
            return
        if self.state.current_id and mid != self.state.current_id:
            return
        self._cancel_timer()
        self._send({"type": "public_message_done", "id": mid})
        self._blank()
        self.state.status = "idle" if self.state.registered else self.state.status
        self._emit()


def make_ws_url(host: str, use_tls: bool = True) -> str:
    """
    host: 'splitflap-olvv.onrender.com' or 'localhost:3000' or full ws(s):// URL
    """
    host = (host or "").strip()
    if host.startswith("ws://") or host.startswith("wss://"):
        return host
    if host.startswith("https://"):
        return "wss://" + host[len("https://") :]
    if host.startswith("http://"):
        return "ws://" + host[len("http://") :]
    # bare host
    if "localhost" in host or host.startswith("127.") or host.startswith("0.0.0.0"):
        scheme = "ws"
    else:
        scheme = "wss" if use_tls else "ws"
    return f"{scheme}://{host}"

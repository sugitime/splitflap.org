#!/usr/bin/env python3
"""
CLI test client — registers as a board and prints play/clear events.

  python3 standalone_test.py wss://splitflap-olvv.onrender.com
  python3 standalone_test.py ws://localhost:3000

Requires: pip install websocket-client
"""

from __future__ import annotations

import argparse
import json
import sys
import time

try:
    import websocket
except ImportError:
    print("Install websocket-client:  pip install websocket-client", file=sys.stderr)
    sys.exit(1)

from layout import grid_to_table_text, message_to_grid
from protocol import SplitflapBoardClient, make_ws_url


def main():
    ap = argparse.ArgumentParser(description="Splitflap board WebSocket test client")
    ap.add_argument(
        "host",
        nargs="?",
        default="splitflap-olvv.onrender.com",
        help="Host or ws(s) URL",
    )
    ap.add_argument("--name", default="standalone-test")
    ap.add_argument("--no-tls", action="store_true")
    args = ap.parse_args()

    url = make_ws_url(args.host, use_tls=not args.no_tls)
    print(f"Connecting to {url} …")

    app = {"ws": None}

    def send_json(payload: dict):
        if app["ws"] and app["ws"].sock and app["ws"].sock.connected:
            app["ws"].send(json.dumps(payload))

    def on_state(state):
        print(
            f"\n[{state.status}] id={state.current_id!r} "
            f"dur={state.current_duration_ms} indef={state.current_indefinite}"
        )
        if state.current_text:
            print("--- message ---")
            print(state.current_text)
            print("--- grid (truncated if wide) ---")
            lines = state.grid_text.split("\n")
            for line in lines:
                print(line[:80] + ("…" if len(line) > 80 else ""))
        if state.last_error:
            print("error:", state.last_error)

    client = SplitflapBoardClient(
        name=args.name, on_state=on_state, send_json=send_json
    )

    def on_open(ws):
        print("open")
        client.handle_open()

    def on_message(ws, message):
        client.handle_message(message)

    def on_error(ws, error):
        print("error:", error)

    def on_close(ws, status, msg):
        print("close", status, msg)
        client.handle_close()

    app["ws"] = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    try:
        app["ws"].run_forever(ping_interval=20, ping_timeout=10)
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()

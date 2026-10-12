"""Minimal client for the QST WebSocket API (QST Professional desktop, localhost only).

Protocol (from QST's docs and qst-client.js): open wss://127.0.0.1:8888/websocket, GET
https://localhost:8888/validate (expects {"RES": "OK"}), then send JSON requests such as
{"RQT": "snapshot", "RQI": "<id>", "INS": "<contract>"}; responses echo RQI.

QST must be running and logged in with Properties -> Desktop -> "Enable Extensions API" ticked.
The server uses a self-signed certificate on loopback, so verification is off for localhost only.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from websockets.sync.client import connect

WS_URL = "wss://127.0.0.1:8888/websocket"
VALIDATE_URL = "https://localhost:8888/validate"


def _loopback_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class QstError(RuntimeError):
    pass


class QstClient:
    def __init__(self, ws_url: str = WS_URL, validate_url: str = VALIDATE_URL, timeout: float = 10.0):
        self.ws_url, self.validate_url, self.timeout = ws_url, validate_url, timeout
        self._ws = None
        self._rqi = 0

    def __enter__(self) -> "QstClient":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        ctx = _loopback_ctx()
        try:
            self._ws = connect(self.ws_url, ssl=ctx, open_timeout=self.timeout, max_size=None)
        except OSError as exc:
            raise QstError("Cannot reach QST on 127.0.0.1:8888. Is QST Professional running, logged in, "
                           "with 'Enable Extensions API' ticked (Ctrl+P > Desktop)?") from exc
        try:
            raw = urllib.request.urlopen(self.validate_url, context=ctx, timeout=self.timeout).read()
        except (urllib.error.URLError, OSError) as exc:
            self.close()
            raise QstError(f"QST validate call failed: {exc}") from exc
        try:
            ok = json.loads(raw).get("RES") == "OK"
        except ValueError:
            ok = False
        if not ok:
            self.close()
            raise QstError(f"QST did not validate the connection: {raw[:200]!r}")

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None

    def request(self, rqt: str, **fields) -> dict:
        """Send one request and return the response carrying the same RQI (streamed events skipped)."""
        if self._ws is None:
            raise QstError("not connected")
        self._rqi += 1
        rqi = str(self._rqi)
        self._ws.send(json.dumps({"RQT": rqt, "RQI": rqi, **fields}))
        while True:
            try:
                msg = json.loads(self._ws.recv(timeout=self.timeout))
            except TimeoutError as exc:
                raise QstError(f"timed out waiting for {rqt} {fields}") from exc
            if str(msg.get("RQI")) == rqi:
                return msg

    def snapshot(self, contract: str) -> dict:
        """Quote snapshot: ST settlement, PS previous settlement, TR last trade, INF trading/settled."""
        return self.request("snapshot", INS=contract)

"""Tests for the QST collector against a mock QST server (no real QST needed).

    python -m unittest collector/test_collector.py
"""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import qst_canola  # noqa: E402
from qst_client import QstClient, QstError  # noqa: E402
from websockets.http11 import Response  # noqa: E402
from websockets.sync.server import serve  # noqa: E402

REPLIES = {
    "RSX26": {"RES": "OK", "INS": "RSX26", "ST": "816.7", "PS": "815.9", "TR": "817.0", "INF": "S"},
    "RSF27": {"RES": "OK", "INS": "RSF27", "ST": "", "PS": "822.1", "TR": "823.4", "INF": "T"},
    "RSH27": {"RES": "OK", "INS": "RSH27", "ST": "830.2", "PS": "829.0", "TR": "830.2", "INF": "S"},
}


def make_cert(d: Path):
    cert, key = d / "cert.pem", d / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(cert), "-days", "2", "-subj", "/CN=localhost"], check=True,
                   capture_output=True)
    return cert, key


class MockQst(threading.Thread):
    def __init__(self, ctx):
        super().__init__(daemon=True)
        self.server = serve(self.handler, "127.0.0.1", 0, ssl=ctx, process_request=self.http)

    @property
    def port(self):
        return self.server.socket.getsockname()[1]

    def http(self, connection, request):
        if request.path == "/validate":
            body = json.dumps({"RQT": "validate", "RES": "OK"}).encode()
            return Response(200, "OK", __import__("websockets").Headers({"Content-Type": "application/json"}), body)
        return None

    def handler(self, ws):
        for raw in ws:
            req = json.loads(raw)
            ws.send(json.dumps({"RQT": "quote", "INS": "XX", "TR": "1"}))      # unrelated streaming event
            reply = REPLIES.get(req.get("INS"), {"RES": "ERR", "MSG": "Unknown contract"})
            ws.send(json.dumps({"RQT": req["RQT"], "RQI": req["RQI"], **reply}))

    def run(self):
        self.server.serve_forever()


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ssl
        cls.tmp = tempfile.TemporaryDirectory()
        cert, key = make_cert(Path(cls.tmp.name))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        cls.mock = MockQst(ctx)
        cls.mock.start()
        cls.kw = dict(ws_url=f"wss://127.0.0.1:{cls.mock.port}/websocket",
                      validate_url=f"https://localhost:{cls.mock.port}/validate")

    @classmethod
    def tearDownClass(cls):
        cls.mock.server.shutdown()
        cls.tmp.cleanup()

    def test_snapshot_skips_streaming_events(self):
        with QstClient(**self.kw) as c:
            self.assertEqual(c.snapshot("RSX26")["ST"], "816.7")
            self.assertEqual(c.snapshot("NOPE")["RES"], "ERR")

    def test_front_contracts_skip_delivery_month(self):
        # in Nov 2026 RSX26 is in its delivery month, so the front chain starts at RSF27
        self.assertEqual([t for _, t, _ in qst_canola.front_contracts(date(2026, 11, 2))][:2], ["RSF27", "RSH27"])
        self.assertEqual([t for _, t, _ in qst_canola.front_contracts(date(2026, 10, 12))][:2], ["RSX26", "RSF27"])

    def test_collect_stores_only_settled(self):
        qst_canola.LOG = Path(self.tmp.name) / "log.jsonl"
        with QstClient(**self.kw) as c:
            rows = qst_canola.collect(c, date(2026, 10, 12), date(2026, 10, 12), ["RSX26", "RSF27", "RSH27", "NOPE"])
        self.assertEqual([(r["ticker"], r["settle"]) for r in rows], [("RSX26", 816.7), ("RSH27", 830.2)])
        self.assertEqual(len(qst_canola.LOG.read_text().splitlines()), 4)

    def test_validate_failure_raises(self):
        with self.assertRaises(QstError):
            QstClient(ws_url=self.kw["ws_url"], validate_url=f"https://localhost:{self.mock.port}/nope").open()

    def test_not_running_message(self):
        with self.assertRaises(QstError) as cm:
            QstClient(ws_url="wss://127.0.0.1:1/websocket", timeout=2).open()
        self.assertIn("Extensions API", str(cm.exception))

    def test_main_dry_run(self):
        qst_canola.LOG = Path(self.tmp.name) / "log2.jsonl"
        rc = qst_canola.main(["run", "--dry-run", "--date", "2026-10-12", "--contracts", "RSX26",
                              "--ws-url", self.kw["ws_url"], "--validate-url", self.kw["validate_url"]])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

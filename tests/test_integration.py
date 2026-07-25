import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trunk_uploader.adapters import IcadAdapter, RdioAdapter, TrunkRecordingAdapter
from trunk_uploader.config import Destination, Settings, Profile, parse_talkgroups
from trunk_uploader.model import Call


class Handler(BaseHTTPRequestHandler):
    calls = []
    mode = "ok"
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0")); body = self.rfile.read(length); self.calls.append((self.path, dict(self.headers), body))
        if self.path.endswith("/api/callupload"):
            payload = json.dumps({"CallAudioID": "abc"}).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        self.send_response(500 if self.mode == "fail" else 202); self.end_headers()
    def log_message(self, *args): pass


def server():
    Handler.calls = []; httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start(); return httpd


def call(tmp_path: Path):
    audio = tmp_path / "a.wav"; audio.write_bytes(b"RIFF")
    return Call(audio, tmp_path / "a.json", None, 1, "tag", "desc", 1, 142, "analog", False, "p", (), (), (), False, 1, {}, "fp")


def destination(kind, url):
    return Destination(kind, "test", True, "p", url, "test-key", "2", "auth", "call-upload", parse_talkgroups("*", "x"), parse_talkgroups("", "x"), {})


def test_local_mock_servers(tmp_path):
    httpd = server(); base = f"http://127.0.0.1:{httpd.server_port}"; c = call(tmp_path)
    assert RdioAdapter().upload(c, destination("rdio", base), c.audio_path).success
    assert IcadAdapter().upload(c, destination("icad", base + "/api/call-upload"), c.audio_path).success
    httpd.shutdown()

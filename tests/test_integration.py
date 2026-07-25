import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trunk_uploader.adapters import IcadAdapter, RdioAdapter, TrunkRecordingAdapter
from trunk_uploader.config import Destination, Settings, Profile, parse_talkgroups
from trunk_uploader.model import Call
from types import SimpleNamespace
import shutil
import pytest


class Handler(BaseHTTPRequestHandler):
    calls = []
    mode = "ok"
    sequence = []
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0")); body = self.rfile.read(length); self.calls.append((self.path, dict(self.headers), body))
        if self.path.endswith("/api/callupload"):
            payload = json.dumps({"CallAudioID": "abc"}).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        self.send_response(Handler.sequence.pop(0) if Handler.sequence else (500 if self.mode == "fail" else 202)); self.end_headers()
    def log_message(self, *args): pass


def server():
    Handler.calls = []; Handler.mode = "ok"; Handler.sequence = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start(); return httpd


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


def test_rdio_500_then_success(tmp_path):
    httpd = server(); Handler.sequence = [500, 202]; base = f"http://127.0.0.1:{httpd.server_port}"; c = call(tmp_path)
    destination_obj = destination("rdio", base)
    first = RdioAdapter().upload(c, destination_obj, c.audio_path)
    second = RdioAdapter().upload(c, destination_obj, c.audio_path)
    assert first.retryable and first.status == 500 and second.success and second.status == 202
    httpd.shutdown()


def test_trunk_recording_two_stage_local_server(tmp_path):
    httpd = server(); base = f"http://127.0.0.1:{httpd.server_port}"; c = call(tmp_path)
    output = tmp_path / "converted.mp3"; output.write_bytes(b"mp3")
    settings = SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k")
    adapter = TrunkRecordingAdapter(settings, converter=lambda call, path: path)
    result = adapter.upload(c, destination("trunk-recording", base), output)
    assert result.success
    assert [path for path, _, _ in Handler.calls] == ["/api/callupload", "/api/callaudioupload/abc"]
    httpd.shutdown()


def test_trunk_recording_audio_failure_is_reported(tmp_path):
    httpd = server(); Handler.mode = "fail"; base = f"http://127.0.0.1:{httpd.server_port}"; c = call(tmp_path)
    output = tmp_path / "converted.mp3"; output.write_bytes(b"mp3")
    result = TrunkRecordingAdapter(SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k"), converter=lambda call, path: path).upload(c, destination("trunk-recording", base), output)
    assert not result.success and result.retryable and result.status == 500
    Handler.mode = "ok"; httpd.shutdown()


@pytest.mark.skipif(shutil.which("/usr/bin/ffmpeg") is None, reason="FFmpeg runtime requirement is unavailable")
def test_ffmpeg_converts_mono_mp3(tmp_path):
    source = tmp_path / "source.wav"
    import wave
    with wave.open(str(source), "wb") as output:
        output.setnchannels(2); output.setsampwidth(2); output.setframerate(8000); output.writeframes(b"\0\0\0\0" * 800)
    call_obj = Call(source, tmp_path / "source.json", None, 1, "", "", 1, 142, "wav", False, "p", (), (), (), False, 1, {}, "fp")
    target = tmp_path / "converted.mp3"
    adapter = TrunkRecordingAdapter(SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k"))
    adapter.convert(call_obj, target)
    assert target.stat().st_size > 0

from pathlib import Path
from unittest.mock import Mock, patch
import requests
from trunk_uploader.adapters import RdioAdapter, IcadAdapter, url_join
from trunk_uploader.config import Destination, parse_talkgroups
from trunk_uploader.model import Call


def call(tmp_path):
    audio = tmp_path / "a.wav"; audio.write_bytes(b"RIFF")
    return Call(audio, tmp_path / "a.json", None, 1, "tag", "desc", 1, 142, "analog", False, "p", (), (), (), False, 1, {}, "fp")


def dest(kind="rdio"):
    return Destination(kind, "x", True, "p", "https://example.test/", "secret", "2", "auth", "call-upload", parse_talkgroups("*", "x"), parse_talkgroups("", "x"), {})


@patch("trunk_uploader.adapters.requests.post")
def test_rdio_202_and_url(mock_post, tmp_path):
    response = requests.Response(); response.status_code = 202; mock_post.return_value = response
    result = RdioAdapter().upload(call(tmp_path), dest(), call(tmp_path).audio_path)
    assert result.success and mock_post.call_args.args[0] == "https://example.test/api/call-upload"
    assert url_join("https://x///", "/api/call-upload") == "https://x/api/call-upload"


@patch("trunk_uploader.adapters.requests.post")
def test_icad_headers(mock_post, tmp_path):
    response = requests.Response(); response.status_code = 200; mock_post.return_value = response
    result = IcadAdapter().upload(call(tmp_path), dest("icad"), call(tmp_path).audio_path)
    assert result.success
    assert mock_post.call_args.kwargs["headers"]["X-API-Key"] == "secret"


@patch("trunk_uploader.adapters.requests.post")
def test_http_401_and_encrypted_rdio_skip(mock_post, tmp_path):
    response = requests.Response(); response.status_code = 401; mock_post.return_value = response
    result = RdioAdapter().upload(call(tmp_path), dest(), call(tmp_path).audio_path)
    assert not result.success and not result.retryable and result.status == 401
    encrypted = call(tmp_path).__class__(**{**call(tmp_path).__dict__, "encrypted": True})
    mock_post.reset_mock()
    assert RdioAdapter().upload(encrypted, dest(), encrypted.audio_path).success
    mock_post.assert_not_called()

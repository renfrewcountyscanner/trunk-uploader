from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from trunk_uploader.adapters import RdioAdapter, IcadAdapter, TrunkRecordingAdapter, url_join
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
def test_rdio_sends_offset_bearing_local_datetime(mock_post, tmp_path):
    response = requests.Response(); response.status_code = 202; mock_post.return_value = response
    result = RdioAdapter("America/Toronto").upload(call(tmp_path), dest(), call(tmp_path).audio_path)
    assert result.success
    assert mock_post.call_args.kwargs["data"]["dateTime"] == "1969-12-31T19:00:01-05:00"


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


@patch("trunk_uploader.adapters.requests.post")
def test_trunk_recording_accepts_identifier_casing_and_nested_json(mock_post, tmp_path):
    metadata = requests.Response(); metadata.status_code = 200
    metadata._content = b'{"data": {"callAudioID": "nested-id"}}'
    audio = requests.Response(); audio.status_code = 200
    mock_post.side_effect = [metadata, audio]
    output = tmp_path / "converted.mp3"; output.write_bytes(b"mp3")
    result = TrunkRecordingAdapter(SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k"), converter=lambda call, path: path).upload(call(tmp_path), dest("trunk-recording"), output)
    assert result.success
    assert mock_post.call_args_list[1].args[0].endswith("/api/callaudioupload/nested-id")


@patch("trunk_uploader.adapters.requests.post")
def test_trunk_recording_accepts_plain_text_identifier(mock_post, tmp_path):
    metadata = requests.Response(); metadata.status_code = 200; metadata._content = b"plain-id"
    audio = requests.Response(); audio.status_code = 200
    mock_post.side_effect = [metadata, audio]
    output = tmp_path / "converted.mp3"; output.write_bytes(b"mp3")
    result = TrunkRecordingAdapter(SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k"), converter=lambda call, path: path).upload(call(tmp_path), dest("trunk-recording"), output)
    assert result.success
    assert mock_post.call_args_list[1].args[0].endswith("/api/callaudioupload/plain-id")


@patch("trunk_uploader.adapters.requests.post")
def test_trunk_recording_uses_legacy_iso_start_time(mock_post, tmp_path):
    metadata = requests.Response(); metadata.status_code = 200; metadata._content = b'{"CallAudioID":"id"}'
    audio = requests.Response(); audio.status_code = 200
    mock_post.side_effect = [metadata, audio]
    output = tmp_path / "converted.mp3"; output.write_bytes(b"mp3")
    result = TrunkRecordingAdapter(SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k"), converter=lambda call, path: path).upload(call(tmp_path), dest("trunk-recording"), output)
    assert result.success
    assert mock_post.call_args_list[0].kwargs["json"]["recordedCall"]["startTime"] == "1970-01-01T00:00:01.000000Z"


@patch("trunk_uploader.adapters.requests.post")
def test_trunk_recording_sends_complete_legacy_talkgroup_info(mock_post, tmp_path):
    metadata = requests.Response(); metadata.status_code = 200; metadata._content = b'{"CallAudioID":"id"}'
    audio = requests.Response(); audio.status_code = 200
    mock_post.side_effect = [metadata, audio]
    output = tmp_path / "converted.mp3"; output.write_bytes(b"mp3")
    call_obj = call(tmp_path).__class__(**{**call(tmp_path).__dict__, "system_short_name": "renfrew", "sources": ({"src": 1234, "tag": "Unit"},)})
    result = TrunkRecordingAdapter(SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k"), converter=lambda call, path: path).upload(call_obj, dest("trunk-recording"), output)
    info = mock_post.call_args_list[0].kwargs["json"]["recordedCall"]["talkGroupInfo"]
    assert result.success
    assert info["receiver"] == "Trunk-Recorder 2"
    assert info["systemid"] == "2"
    assert info["sourceid"] == 1234
    assert {"receiverVCO", "sourcelabel", "sourcetag", "systemlabel", "systemtype", "siteid", "sitelabel", "calltype"}.issubset(info)


@patch("trunk_uploader.adapters.requests.post")
def test_trunk_recording_uses_configured_receiver_name(mock_post, tmp_path):
    metadata = requests.Response(); metadata.status_code = 200; metadata._content = b'{"CallAudioID":"id"}'
    audio = requests.Response(); audio.status_code = 200
    mock_post.side_effect = [metadata, audio]
    output = tmp_path / "converted.mp3"; output.write_bytes(b"mp3")
    configured = Destination("trunk-recording", "x", True, "p", "https://example.test", "secret", "2", "auth", "call-upload", parse_talkgroups("*", "x"), parse_talkgroups("", "x"), {}, "RENFREW-FIRE")
    result = TrunkRecordingAdapter(SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k"), converter=lambda call, path: path).upload(call(tmp_path), configured, output)
    info = mock_post.call_args_list[0].kwargs["json"]["recordedCall"]["talkGroupInfo"]
    assert result.success and info["receiver"] == "RENFREW-FIRE"


@patch("trunk_uploader.adapters.requests.post")
def test_trunk_recording_rejects_invalid_api_request_identifier(mock_post, tmp_path):
    metadata = requests.Response(); metadata.status_code = 200; metadata._content = b'{"CallAudioID":"Invalid API request"}'
    mock_post.return_value = metadata
    output = tmp_path / "converted.mp3"; output.write_bytes(b"mp3")
    result = TrunkRecordingAdapter(SimpleNamespace(ffmpeg="/usr/bin/ffmpeg", mp3_bitrate="64k"), converter=lambda call, path: path).upload(call(tmp_path), dest("trunk-recording"), output)
    assert not result.success
    mock_post.assert_called_once()

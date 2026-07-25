import json
import wave
from pathlib import Path
from trunk_uploader.config import load_config
from trunk_uploader.model import normalize
from trunk_uploader.routing import select_profile


def files(tmp_path: Path):
    audio = tmp_path / "call.wav"
    with wave.open(str(audio), "wb") as wav: wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b"\0\0" * 80)
    metadata = tmp_path / "call.json"
    metadata.write_text(json.dumps({"talkgroup": 9056, "short_name": "p", "start_time": 1700000000, "freq": 142000000, "talkgroup_tag": "OPP", "talkgroup_description": "Dispatch", "srcList": [{"src": 12, "pos": 0, "tag": "Unit"}], "encrypted": 0, "emergency": 1, "call_length": 2}))
    return audio, metadata


def test_normalization_and_stable_fingerprint(tmp_path):
    audio, metadata = files(tmp_path)
    one = normalize(audio, metadata); two = normalize(audio, metadata)
    assert one.talkgroup == 9056 and one.sources[0]["src"] == 12 and one.emergency
    assert one.fingerprint == two.fingerprint


def test_profile_selection(tmp_path):
    audio, metadata = files(tmp_path)
    config = Path(tmp_path / "uploader.conf")
    config.write_text((Path(__file__).parents[1] / "config/uploader.conf.example").read_text().replace("default_profile = fleetnet-pembroke", "default_profile = p").replace("[profile:fleetnet-pembroke]", "[profile:p]").replace("profile = fleetnet-pembroke", "profile = p"))
    call = normalize(audio, metadata); settings = load_config(config)
    assert select_profile(call, settings) == "p"
    assert select_profile(call, settings, "p") == "p"

import json
import os
import subprocess
import wave
from pathlib import Path


def test_wrapper_argument_handling(tmp_path):
    wrapper = Path(__file__).parents[1] / "bin/universal-upload.sh"
    missing = subprocess.run([str(wrapper)], text=True, capture_output=True)
    assert missing.returncode == 2 and "Usage:" in missing.stderr
    audio = tmp_path / "call.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(8000); output.writeframes(b"\0\0" * 10)
    metadata = tmp_path / "call.json"; metadata.write_text(json.dumps({"talkgroup": 1, "short_name": "p", "start_time": 1}))
    config = tmp_path / "uploader.conf"
    example = (Path(__file__).parents[1] / "config/uploader.conf.example").read_text().replace("default_profile = fleetnet-pembroke", "default_profile = p").replace("[profile:fleetnet-pembroke]", "[profile:p]").replace("profile = fleetnet-pembroke", "profile = p").replace("database = data/uploader.sqlite3", f"database = {tmp_path}/db.sqlite3").replace("spool_dir = spool", f"spool_dir = {tmp_path}/spool")
    config.write_text(example)
    env = {**os.environ, "TRUNK_UPLOADER_CONFIG": str(config)}
    result = subprocess.run([str(wrapper), str(audio), str(metadata)], env=env, text=True, capture_output=True)
    assert result.returncode == 0
    explicit = subprocess.run([str(wrapper), "p", str(audio), str(metadata)], env=env, text=True, capture_output=True)
    assert explicit.returncode == 0

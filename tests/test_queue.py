import json
import wave
from pathlib import Path
from trunk_uploader.config import load_config
from trunk_uploader.model import normalize
from trunk_uploader.queue import Queue


def setup(tmp_path):
    audio = tmp_path / "a.wav"
    with wave.open(str(audio), "wb") as f: f.setnchannels(1); f.setsampwidth(2); f.setframerate(8000); f.writeframes(b"\0\0" * 20)
    metadata = tmp_path / "a.json"; metadata.write_text(json.dumps({"talkgroup": 1, "short_name": "p", "start_time": 1}))
    conf = tmp_path / "uploader.conf"
    conf.write_text((Path(__file__).parents[1] / "config/uploader.conf.example").read_text().replace("default_profile = fleetnet-pembroke", "default_profile = p").replace("[profile:fleetnet-pembroke]", "[profile:p]").replace("profile = fleetnet-pembroke", "profile = p").replace("database = data/uploader.sqlite3", f"database = {tmp_path}/db.sqlite3").replace("spool_dir = spool", f"spool_dir = {tmp_path}/spool"))
    settings = load_config(conf); return settings, normalize(audio, metadata)


def test_deduplicates_destinations_and_copies_originals(tmp_path):
    settings, call = setup(tmp_path); queue = Queue(settings)
    destinations = [d for d in settings.destinations if d.type == "rdio"][:1]
    assert queue.enqueue(call, "p", destinations) == 1
    assert queue.enqueue(call, "p", destinations) == 0
    row = queue.rows()[0]; assert row["status"] == "pending"
    spool = Path(row["spool_dir"]); assert (spool / "a.wav").is_file() and (spool / "a.json").is_file()
    queue.close()

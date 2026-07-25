import json
import threading
import wave
from pathlib import Path
from trunk_uploader.config import load_config
from trunk_uploader.model import normalize
from trunk_uploader.queue import Queue
from trunk_uploader.http import ResponseResult
from trunk_uploader.adapters import RdioAdapter, TrunkRecordingAdapter


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


def test_concurrent_queue_insertion_is_deduplicated(tmp_path):
    settings, call = setup(tmp_path); queue = Queue(settings)
    destinations = [d for d in settings.destinations if d.type == "rdio"][:1]
    results = []
    def insert(): results.append(queue.enqueue(call, "p", destinations))
    threads = [threading.Thread(target=insert) for _ in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sum(results) == 1 and len(queue.rows()) == 1
    queue.close()


def test_retry_backoff_and_successful_destination_not_resent(tmp_path, monkeypatch):
    settings, call = setup(tmp_path); queue = Queue(settings)
    destination = next(d for d in settings.destinations if d.type == "rdio")
    queue.enqueue(call, "p", [destination])
    responses = [ResponseResult(False, True, 500, "HTTP 500"), ResponseResult(True, False, 202, "")]
    calls = []
    monkeypatch.setattr(RdioAdapter, "upload", lambda *args: calls.append(1) or responses.pop(0))
    assert queue.process() == (0, 1)
    row = queue.rows()[0]; assert row["status"] == "retry" and row["next_retry_time"] > row["last_attempt_time"]
    queue.db.execute("UPDATE destinations SET next_retry_time=0 WHERE id=?", (row["id"],))
    assert queue.process() == (1, 0)
    assert queue.process() == (0, 0)
    assert len(calls) == 2
    queue.close()


def test_mp3_conversion_is_reused_for_two_destinations(tmp_path, monkeypatch):
    settings, call = setup(tmp_path)
    text = settings.path.read_text().replace("[method:trunk-recording]\nenabled = no", "[method:trunk-recording]\nenabled = yes").replace("[trunk-recording:public]\nenabled = no", "[trunk-recording:public]\nenabled = yes")
    text = text.replace("[trunk-recording:public]\nenabled = yes\nprofile = p\nurl = https://record.example.invalid\napi_key = CHANGE_ME\nauth_id = CHANGE_ME", "[trunk-recording:public]\nenabled = yes\nprofile = p\nurl = https://record.example.invalid\napi_key = trunk-key\nauth_id = trunk-auth")
    text += "\n[trunk-recording:local]\nenabled = yes\nprofile = p\nurl = https://record2.example.invalid\napi_key = trunk-key-2\nauth_id = trunk-auth-2\nsystem_id = 101\ntalkgroups = *\nexclude_talkgroups =\n"
    settings.path.write_text(text)
    settings = load_config(settings.path); queue = Queue(settings)
    destinations = [d for d in settings.destinations if d.type == "trunk-recording"]
    queue.enqueue(call, "p", destinations)
    conversions = []
    def convert(self, normalized, output):
        conversions.append(output); output.write_bytes(b"mp3"); return output
    monkeypatch.setattr(TrunkRecordingAdapter, "convert", convert)
    monkeypatch.setattr(TrunkRecordingAdapter, "upload", lambda *args: ResponseResult(True, False, 200, ""))
    assert queue.process() == (2, 0)
    assert len(conversions) == 1
    assert not (Path(queue.rows()[0]["spool_dir"]) / "converted.mp3").exists()
    queue.close()

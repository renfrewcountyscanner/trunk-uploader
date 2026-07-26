from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .adapters import RdioAdapter, IcadAdapter, TrunkRecordingAdapter
from .config import Destination, Settings
from .model import Call, normalize
from .logging import setup, extra


@dataclass(frozen=True)
class Pending:
    id: int
    fingerprint: str
    destination_type: str
    destination_name: str
    profile: str
    attempt_count: int
    spool_dir: Path


class Queue:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.database.parent.mkdir(parents=True, exist_ok=True)
        self.settings.spool_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.settings.database, timeout=30, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS destinations (
          id INTEGER PRIMARY KEY,
          call_fingerprint TEXT NOT NULL,
          destination_type TEXT NOT NULL,
          destination_name TEXT NOT NULL,
          profile TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          last_attempt_time REAL,
          next_retry_time REAL,
          processing_time REAL,
          http_status INTEGER,
          error TEXT,
          spool_dir TEXT NOT NULL,
          call_start_time REAL NOT NULL DEFAULT 0,
          created_at REAL NOT NULL,
          completed_at REAL,
          UNIQUE(call_fingerprint, destination_type, destination_name)
        );
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(destinations)")}
        if "processing_time" not in columns:
            self.db.execute("ALTER TABLE destinations ADD COLUMN processing_time REAL")
        if "call_start_time" not in columns:
            self.db.execute("ALTER TABLE destinations ADD COLUMN call_start_time REAL NOT NULL DEFAULT 0")

    def close(self) -> None: self.db.close()

    def spool(self, call: Call) -> Path:
        target = self.settings.spool_dir / call.fingerprint
        if target.is_dir(): return target
        temp = self.settings.spool_dir / f".{call.fingerprint}.tmp-{time.time_ns()}"
        temp.mkdir(parents=True)
        try:
            shutil.copy2(call.audio_path, temp / call.audio_path.name)
            shutil.copy2(call.json_path, temp / call.json_path.name)
            if call.m4a_path and call.m4a_path.is_file(): shutil.copy2(call.m4a_path, temp / call.m4a_path.name)
            (temp / "call.json").write_text(json.dumps({"audio": call.audio_path.name, "metadata": call.json_path.name, "m4a": call.m4a_path.name if call.m4a_path else ""}), encoding="utf-8")
            try:
                temp.rename(target)
            except FileExistsError:
                shutil.rmtree(temp, ignore_errors=True)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        return target

    def enqueue(self, call: Call, profile: str, destinations: list[Destination]) -> int:
        with self._lock:
            spool = self.spool(call)
            now = time.time(); inserted = 0
            with self.db:
                for destination in destinations:
                    cur = self.db.execute("""INSERT OR IGNORE INTO destinations(call_fingerprint,destination_type,destination_name,profile,spool_dir,call_start_time,next_retry_time,created_at) VALUES(?,?,?,?,?,?,?,?)""", (call.fingerprint, destination.type, destination.name, profile, str(spool), call.start_time, now + self.settings.ordering_delay_seconds, now))
                    inserted += cur.rowcount
            return inserted

    def pending(self, limit: int = 100, now: float | None = None) -> list[Pending]:
        now = time.time() if now is None else now
        rows = self.db.execute("""SELECT * FROM destinations WHERE status IN ('pending','retry') AND (next_retry_time IS NULL OR next_retry_time <= ?) ORDER BY call_start_time, created_at, id LIMIT ?""", (now, limit)).fetchall()
        return [Pending(row["id"], row["call_fingerprint"], row["destination_type"], row["destination_name"], row["profile"], row["attempt_count"], Path(row["spool_dir"])) for row in rows]

    def _claim_pending(self, limit: int = 100) -> list[Pending]:
        now = time.time()
        stale = now - 900
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                rows = self.db.execute("""SELECT * FROM destinations WHERE (status IN ('pending','retry') AND (next_retry_time IS NULL OR next_retry_time <= ?)) OR (status='processing' AND processing_time <= ?) ORDER BY call_start_time, created_at, id LIMIT ?""", (now, stale, limit)).fetchall()
                for row in rows:
                    self.db.execute("UPDATE destinations SET status='processing',processing_time=? WHERE id=?", (now, row["id"]))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return [Pending(row["id"], row["call_fingerprint"], row["destination_type"], row["destination_name"], row["profile"], row["attempt_count"], Path(row["spool_dir"])) for row in rows]

    def _destination(self, item: Pending) -> Destination:
        return next(d for d in self.settings.destinations if d.type == item.destination_type and d.name == item.destination_name)

    def _cleanup_converted_audio(self, fingerprint: str, spool_dir: Path) -> None:
        remaining = self.db.execute("SELECT 1 FROM destinations WHERE call_fingerprint=? AND status IN ('pending','retry','processing') LIMIT 1", (fingerprint,)).fetchone()
        if remaining is None:
            converted = spool_dir / "converted.mp3"
            if converted.exists(): converted.unlink()

    def _cleanup_spool(self, fingerprint: str, spool_dir: Path) -> None:
        remaining = self.db.execute("SELECT 1 FROM destinations WHERE call_fingerprint=? AND status IN ('pending','retry','processing') LIMIT 1", (fingerprint,)).fetchone()
        if remaining is None and spool_dir.is_dir():
            shutil.rmtree(spool_dir, ignore_errors=True)

    def process(self, limit: int = 100) -> tuple[int, int]:
        success = failed = 0
        logger = setup(self.settings.log_level)
        for item in self._claim_pending(limit):
            destination = self._destination(item)
            try:
                manifest = json.loads((item.spool_dir / "call.json").read_text(encoding="utf-8"))
                audio = item.spool_dir / manifest["audio"]
                metadata = item.spool_dir / manifest["metadata"]
                m4a = item.spool_dir / manifest["m4a"] if manifest.get("m4a") else None
                call = normalize(audio, metadata, m4a)
                if not call.talkgroup_known:
                    now = time.time()
                    self.db.execute("UPDATE destinations SET status='skipped',attempt_count=?,last_attempt_time=?,next_retry_time=NULL,processing_time=NULL,error=?,completed_at=? WHERE id=?", (item.attempt_count, now, "talkgroup not in talkgroup file", now, item.id))
                    logger.info("skipped: talkgroup not in talkgroup file", extra=extra(item.fingerprint, item.profile, item.destination_type, item.destination_name))
                    self._cleanup_spool(item.fingerprint, item.spool_dir)
                    continue
                if destination.type == "rdio": result = RdioAdapter(self.settings.timezone).upload(call, destination, m4a if m4a and m4a.is_file() else audio)
                elif destination.type == "icad": result = IcadAdapter().upload(call, destination, audio)
                else:
                    mp3 = item.spool_dir / "converted.mp3"
                    adapter = TrunkRecordingAdapter(self.settings)
                    if not mp3.is_file(): adapter.converter(call, mp3)
                    result = adapter.upload(call, destination, mp3)
            except Exception as exc:
                from .http import classify
                result = classify(error=exc)
            now = time.time()
            attempt = item.attempt_count + 1
            if result.success:
                self.db.execute("UPDATE destinations SET status='success',attempt_count=?,last_attempt_time=?,next_retry_time=NULL,processing_time=NULL,http_status=?,error=?,completed_at=? WHERE id=?", (attempt, now, result.status, result.error, now, item.id)); success += 1
                logger.info("success %s", result.error or "upload succeeded", extra=extra(item.fingerprint, item.profile, item.destination_type, item.destination_name))
            elif result.retryable and attempt < self.settings.retry_max_attempts and not self.settings.discard_failed_calls:
                delay = min(self.settings.retry_max_seconds, self.settings.retry_base_seconds * (2 ** max(0, attempt - 1)))
                self.db.execute("UPDATE destinations SET status='retry',attempt_count=?,last_attempt_time=?,next_retry_time=?,processing_time=NULL,http_status=?,error=? WHERE id=?", (attempt, now, now + delay, result.status, result.error[:500], item.id)); failed += 1
                logger.warning("retry scheduled: %s", result.error, extra=extra(item.fingerprint, item.profile, item.destination_type, item.destination_name))
            else:
                self.db.execute("UPDATE destinations SET status='failed',attempt_count=?,last_attempt_time=?,processing_time=NULL,http_status=?,error=?,completed_at=? WHERE id=?", (attempt, now, result.status, result.error[:500], now, item.id)); failed += 1
                message = "discarded after failure" if self.settings.discard_failed_calls else "permanent failure"
                logger.error("%s: %s", message, result.error, extra=extra(item.fingerprint, item.profile, item.destination_type, item.destination_name))
            self._cleanup_converted_audio(item.fingerprint, item.spool_dir)
            if self.settings.discard_failed_calls: self._cleanup_spool(item.fingerprint, item.spool_dir)
        return success, failed

    def rows(self) -> list[sqlite3.Row]: return self.db.execute("SELECT * FROM destinations ORDER BY call_start_time, created_at, id").fetchall()

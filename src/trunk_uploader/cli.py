from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .logging import setup
from .model import normalize
from .queue import Queue
from .routing import select_profile
from .security import redact


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trunk-uploader")
    p.add_argument("--config", default="config/uploader.conf")
    sub = p.add_subparsers(dest="command", required=True)
    upload = sub.add_parser("upload"); upload.add_argument("--profile"); upload.add_argument("audio"); upload.add_argument("metadata"); upload.add_argument("m4a", nargs="?")
    dry = sub.add_parser("dry-run"); dry.add_argument("--profile"); dry.add_argument("audio"); dry.add_argument("metadata"); dry.add_argument("m4a", nargs="?")
    sub.add_parser("validate"); sub.add_parser("retry").add_argument("--limit", type=int, default=100); sub.add_parser("pending"); sub.add_parser("status"); sub.add_parser("config-example")
    return p


def _call(args): return normalize(args.audio, args.metadata, args.m4a)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "config-example":
        example = Path(__file__).resolve().parents[2] / "config" / "uploader.conf.example"
        print(example.read_text(encoding="utf-8")); return 0
    try: settings = load_config(args.config)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr); return 2
    if args.command == "validate": print(f"configuration valid: {args.config}"); return 0
    if args.command in {"upload", "dry-run"}:
        try:
            call = _call(args); profile = select_profile(call, settings, args.profile); matches = settings.matching(profile, call.talkgroup)
        except ValueError as exc: print(str(exc), file=sys.stderr); return 2
        if args.command == "dry-run":
            selected = {(d.type, d.name) for d in matches}
            print(json.dumps({"fingerprint": call.fingerprint, "profile": profile, "audio": str(call.audio_path), "metadata": str(call.json_path), "talkgroup": call.talkgroup, "talkgroup_tag": call.talkgroup_tag, "talkgroup_description": call.talkgroup_description, "start_time": call.start_time, "frequency": call.frequency, "audio_type": call.audio_type, "encrypted": call.encrypted, "system": call.system_short_name, "sources": call.sources, "aliases": call.aliases, "patches": call.patches, "emergency": call.emergency, "destinations": [{"type": d.type, "name": d.name, "url": d.url, "system_id": d.system_id, "talkgroups": d.talkgroups.raw, "rule_matched": d.matches(call.talkgroup), "selected": (d.type, d.name) in selected, "skip_reason": ("disabled_or_method_disabled" if (d.type, d.name) not in selected and d.enabled else ("encrypted_rdio" if d.type == "rdio" and call.encrypted else "talkgroup_filter" if not d.matches(call.talkgroup) else "not_selected"))} for d in settings.destinations if d.profile == profile]}, indent=2, default=str))
            return 0
        logger = setup(settings.log_level); queue = Queue(settings)
        try:
            queue.process()
            try: queue.enqueue(call, profile, matches)
            except OSError as exc: logger.error("spool copy failed: %s", redact(exc)); return 1
            queue.process()
        finally: queue.close()
        return 0
    queue = Queue(settings)
    try:
        if args.command == "retry":
            ok, failed = queue.process(args.limit); print(f"processed={ok + failed} succeeded={ok} failed_or_retrying={failed}"); return 0
        if args.command == "pending":
            rows = queue.rows(); pending = [r for r in rows if r["status"] in ("pending", "retry")]
            for row in pending: print(f"{row['id']} {row['status']} {row['profile']} {row['destination_type']}:{row['destination_name']} attempts={row['attempt_count']} next={row['next_retry_time']}")
            print(f"pending={len(pending)}"); return 0
        if args.command == "status":
            rows = queue.rows(); counts = {}
            for row in rows: counts[row["status"]] = counts.get(row["status"], 0) + 1
            for key, value in sorted(counts.items()): print(f"{key}={value}")
            print(f"total={len(rows)}"); return 0
    finally: queue.close()
    return 0


if __name__ == "__main__": raise SystemExit(main())

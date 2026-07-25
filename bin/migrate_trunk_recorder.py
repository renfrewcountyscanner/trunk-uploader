#!/usr/bin/env python3
"""Migrate a Trunk Recorder v2 JSON install to trunk-uploader.

The generated uploader configuration keeps credentials local and disabled
destinations enabled only when their legacy destination was configured.
Trunk Recorder's JSON is changed only with --apply, after a timestamped
backup is created.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def slug(value: str) -> str:
    result = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return result or "profile"


def quote(value: Any) -> str:
    return str(value if value is not None else "").strip()


def enabled(value: Any) -> str:
    return "yes" if bool(value) else "no"


def profile_name(system: dict[str, Any], used: set[str]) -> str:
    base = slug(quote(system.get("shortName") or system.get("short_name") or "default"))
    name = base
    index = 2
    while name in used:
        name = f"{base}-{index}"
        index += 1
    used.add(name)
    return name


def destination_section(kind: str, name: str, profile: str, entry: dict[str, Any], *, url: str, key: str, system_id: Any, protocol: str = "call-upload") -> tuple[str, dict[str, str]]:
    values = {
        "enabled": "yes",
        "profile": profile,
        "url": quote(url),
        "api_key": quote(key),
        "system_id": quote(system_id),
        "talkgroups": "*",
        "exclude_talkgroups": "",
    }
    if kind == "icad":
        values["protocol"] = protocol
    return f"{kind}:{name}", values


def migrate(legacy: dict[str, Any], legacy_path: Path, output_path: Path, uploader_root: Path) -> tuple[configparser.ConfigParser, list[dict[str, str]]]:
    systems = legacy.get("systems") or []
    if not isinstance(systems, list) or not systems:
        raise ValueError("legacy config has no systems array")

    parser = configparser.ConfigParser(interpolation=None)
    parser["general"] = {
        "config_version": "1",
        "default_profile": "",
        "database": "data/uploader.sqlite3",
        "spool_dir": "spool",
        "log_level": "INFO",
        "ffmpeg": "/usr/bin/ffmpeg",
        "mp3_bitrate": "64k",
        "timezone": "America/Toronto",
        "ordering_delay_seconds": "30",
        "retry_max_attempts": "8",
        "retry_base_seconds": "30",
        "retry_max_seconds": "3600",
    }
    parser["method:rdio"] = {"enabled": "no"}
    parser["method:icad"] = {"enabled": "no"}
    parser["method:trunk-recording"] = {"enabled": "no"}

    used_profiles: set[str] = set()
    profiles: list[tuple[str, dict[str, Any]]] = []
    systems_by_short: dict[str, str] = {}
    for system in systems:
        if not isinstance(system, dict):
            continue
        profile = profile_name(system, used_profiles)
        short = quote(system.get("shortName") or system.get("short_name") or profile)
        profiles.append((profile, system))
        systems_by_short[short] = profile
        if not parser["general"]["default_profile"]:
            parser["general"]["default_profile"] = profile
        parser[f"profile:{profile}"] = {
            "enabled": "yes",
            "rdio_enabled": "yes",
            "icad_enabled": "yes",
            "trunk_recording_enabled": "no",
        }

    migrated: list[dict[str, str]] = []
    dispatch = legacy.get("icadDispatch") or {}
    if isinstance(dispatch, dict):
        for site, entry in dispatch.items():
            if not isinstance(entry, dict):
                continue
            target_profile = systems_by_short.get(site, profiles[0][0])
            section, values = destination_section(
                "icad", f"{slug(site)}-dispatch", target_profile, entry,
                url=entry.get("url", ""), key=entry.get("apiKey", ""), system_id=entry.get("systemId", ""),
            )
            parser[section] = values
            parser["method:icad"]["enabled"] = "yes"
            migrated.append({"kind": "icad", "section": section, "profile": target_profile})

    for plugin in legacy.get("plugins") or []:
        if not isinstance(plugin, dict) or plugin.get("name") != "rdioscanner_uploader":
            continue
        server = plugin.get("server", "")
        for entry in plugin.get("systems") or []:
            if not isinstance(entry, dict):
                continue
            short = quote(entry.get("shortName") or entry.get("short_name") or "")
            target_profile = systems_by_short.get(short, profiles[0][0])
            section, values = destination_section(
                "rdio", f"{slug(short or target_profile)}-rdio", target_profile, entry,
                url=server, key=entry.get("apiKey", ""), system_id=entry.get("systemId", ""),
            )
            parser[section] = values
            parser["method:rdio"]["enabled"] = "yes"
            migrated.append({"kind": "rdio", "section": section, "profile": target_profile})

    parser["general"]["default_profile"] = profiles[0][0]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        parser.write(handle)
    os.chmod(output_path, stat.S_IRUSR | stat.S_IWUSR)

    return parser, migrated


def add_trunk_recording(parser: configparser.ConfigParser, source: Path, profile: str, system_id: str, receiver_name: str | None = None, auth_id: str | None = None) -> None:
    """Copy one existing trunk-recording destination without printing secrets."""
    source_parser = configparser.ConfigParser(interpolation=None)
    source_parser.read(source)
    candidates = [section for section in source_parser.sections() if section.startswith("trunk-recording:") and source_parser[section].get("enabled", "no").strip().lower() in {"yes", "true", "1", "on"}]
    if not candidates:
        raise ValueError(f"no enabled trunk-recording destination found in {source}")
    old = source_parser[candidates[0]]
    parser["method:trunk-recording"]["enabled"] = "yes"
    parser[f"profile:{profile}"]["trunk_recording_enabled"] = "yes"
    parser["trunk-recording:fire-public"] = {
        "enabled": "yes",
        "profile": profile,
        "url": old.get("url", ""),
        "api_key": old.get("api_key", ""),
        "auth_id": auth_id or old.get("auth_id", ""),
        "system_id": system_id,
        "receiver_name": receiver_name or system_id,
        "talkgroups": "*",
        "exclude_talkgroups": "",
    }


def apply_upload_scripts(legacy_path: Path, uploader_root: Path) -> Path:
    backup = legacy_path.with_name(f"{legacy_path.name}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(legacy_path, backup)
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    wrapper = str(uploader_root / "bin" / "universal-upload.sh")
    for system in payload.get("systems", []):
        if isinstance(system, dict):
            system["uploadScript"] = wrapper
    # The new wrapper owns Rdio delivery. Leaving the legacy plugin loaded
    # would upload every call twice.
    payload["plugins"] = [
        plugin for plugin in payload.get("plugins", [])
        if not (isinstance(plugin, dict) and plugin.get("name") == "rdioscanner_uploader")
    ]
    temporary = legacy_path.with_suffix(legacy_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, legacy_path)
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="generated uploader.conf path")
    parser.add_argument("--uploader-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--trunk-recording-source-config", type=Path, help="copy credentials from an existing uploader.conf")
    parser.add_argument("--trunk-recording-profile", default="", help="profile receiving the Trunk Recording route")
    parser.add_argument("--trunk-recording-system-id", default="", help="system/receiver identity for Trunk Recording")
    parser.add_argument("--trunk-recording-receiver-name", default="", help="display name sent as the Trunk Recording receiver")
    parser.add_argument("--trunk-recording-auth-id", default="", help="API auth label prepended to the receiver by Trunk Recording")
    parser.add_argument("--apply", action="store_true", help="replace each system uploadScript after making a backup")
    args = parser.parse_args(argv)

    try:
        legacy = json.loads(args.legacy_config.read_text(encoding="utf-8"))
        generated, migrated = migrate(legacy, args.legacy_config, args.output, args.uploader_root)
        if args.trunk_recording_source_config:
            profile = args.trunk_recording_profile or generated["general"]["default_profile"]
            if not args.trunk_recording_system_id:
                raise ValueError("--trunk-recording-system-id is required when adding Trunk Recording")
            add_trunk_recording(generated, args.trunk_recording_source_config, profile, args.trunk_recording_system_id, args.trunk_recording_receiver_name, args.trunk_recording_auth_id)
            with args.output.open("w", encoding="utf-8") as handle:
                generated.write(handle)
            os.chmod(args.output, stat.S_IRUSR | stat.S_IWUSR)
            migrated.append({"kind": "trunk-recording", "section": "trunk-recording:fire-public", "profile": profile})
        print(f"generated={args.output}")
        print(f"default_profile={generated['general']['default_profile']}")
        for item in migrated:
            print(f"migrated={item['section']}")
        if args.apply:
            backup = apply_upload_scripts(args.legacy_config, args.uploader_root)
            print(f"trunk_recorder_backup={backup}")
            print(f"upload_script={args.uploader_root / 'bin' / 'universal-upload.sh'}")
        else:
            print("upload_scripts=unchanged (use --apply to switch Trunk Recorder)")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

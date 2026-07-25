from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def first(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if data.get(key) not in (None, ""): return data[key]
    return default


def as_bool(value: Any) -> bool:
    if isinstance(value, str): return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True)
class Call:
    audio_path: Path
    json_path: Path
    m4a_path: Path | None
    talkgroup: int
    talkgroup_tag: str
    talkgroup_description: str
    start_time: float
    frequency: float | int | str
    audio_type: str
    encrypted: bool
    system_short_name: str
    sources: tuple[dict[str, Any], ...]
    aliases: tuple[str, ...]
    patches: tuple[int, ...]
    emergency: bool
    duration: float
    original: dict[str, Any]
    fingerprint: str


def normalize(audio: str | Path, metadata: str | Path, m4a: str | Path | None = None) -> Call:
    audio_path = Path(audio); json_path = Path(metadata)
    if not audio_path.is_file(): raise ValueError(f"audio file not found: {audio_path}")
    if not json_path.is_file(): raise ValueError(f"metadata file not found: {json_path}")
    try: original = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ValueError(f"invalid metadata JSON: {exc}") from exc
    if not isinstance(original, dict): raise ValueError("metadata JSON must be an object")
    tg = first(original, "talkgroup", "tgid", "talkgroup_id", default=0)
    try: talkgroup = int(tg)
    except (TypeError, ValueError) as exc: raise ValueError(f"invalid talkgroup {tg!r}") from exc
    start = first(original, "start_time", "startTime", "time", default=0)
    try: start_time = float(start)
    except (TypeError, ValueError): start_time = 0.0
    freq = first(original, "freq", "frequency", default="")
    sources = first(original, "srcList", "sources", "source_list", default=[])
    if not isinstance(sources, list): sources = []
    aliases = first(original, "unit_aliases", "unitAliases", "aliases", default=[])
    if isinstance(aliases, str): aliases = [aliases]
    if not isinstance(aliases, list): aliases = []
    patches = first(original, "patches", "patched_talkgroups", "patchedTalkgroups", default=[])
    if isinstance(patches, str): patches = [p.strip() for p in patches.split(",") if p.strip()]
    clean_patches = []
    for item in patches if isinstance(patches, list) else []:
        try: clean_patches.append(int(item))
        except (TypeError, ValueError): pass
    size = audio_path.stat().st_size
    digest = hashlib.sha256()
    stable = {"talkgroup": talkgroup, "start": start_time, "frequency": str(freq), "system": first(original, "shortName", "short_name", "system", default=""), "duration": first(original, "call_length", "duration", default=0), "audio_name": audio_path.name, "audio_size": size}
    digest.update(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode())
    return Call(audio_path, json_path, Path(m4a) if m4a else None, talkgroup, str(first(original, "talkgroup_tag", "talkgroupTag", default="")), str(first(original, "talkgroup_description", "talkgroupDescription", default="")), start_time, freq, str(first(original, "audio_type", "audioType", default="wav")), as_bool(first(original, "encrypted", "is_encrypted", default=False)), str(first(original, "shortName", "short_name", "system", default="")), tuple(x for x in sources if isinstance(x, dict)), tuple(str(x) for x in aliases), tuple(clean_patches), as_bool(first(original, "emergency", "is_emergency", default=False)), float(first(original, "call_length", "duration", default=0) or 0), original, digest.hexdigest())

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


TRUE = {"yes", "true", "on", "1"}
FALSE = {"no", "false", "off", "0"}
PLACEHOLDERS = {"", "change_me", "changeme", "<redacted>", "example", "example.invalid"}
COMMON = {"enabled", "profile", "url", "api_key", "system_id", "talkgroups", "exclude_talkgroups"}


def parse_bool(value: str, where: str = "boolean") -> bool:
    value = value.strip().lower()
    if value in TRUE: return True
    if value in FALSE: return False
    raise ValueError(f"{where}: expected yes/no, got {value!r}")


@dataclass(frozen=True)
class TalkgroupRule:
    raw: str
    ranges: tuple[tuple[int, int], ...]
    wildcard: bool = False

    def matches(self, value: int) -> bool:
        return self.wildcard or any(start <= value <= end for start, end in self.ranges)


def parse_talkgroups(value: str, where: str) -> TalkgroupRule:
    parts = [p.strip() for p in value.split(",") if p.strip()]
    ranges: list[tuple[int, int]] = []
    wildcard = False
    for part in parts:
        if part == "*":
            wildcard = True
            continue
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not match:
            raise ValueError(f"{where}: invalid talkgroup {part!r}")
        start = int(match.group(1)); end = int(match.group(2) or start)
        if start > end:
            raise ValueError(f"{where}: reversed talkgroup range {part!r}")
        ranges.append((start, end))
    if wildcard and ranges:
        # A wildcard makes the other terms redundant and usually signals a typo.
        raise ValueError(f"{where}: wildcard cannot be combined with ranges")
    return TalkgroupRule(value.strip(), tuple(ranges), wildcard)


def rules_overlap(a: TalkgroupRule, b: TalkgroupRule) -> bool:
    if a.wildcard or b.wildcard: return True
    return any(max(x1, y1) <= min(x2, y2) for x1, x2 in a.ranges for y1, y2 in b.ranges)


@dataclass(frozen=True)
class Destination:
    type: str
    name: str
    enabled: bool
    profile: str
    url: str = ""
    api_key: str = ""
    system_id: str = ""
    auth_id: str = ""
    protocol: str = "call-upload"
    talkgroups: TalkgroupRule = TalkgroupRule("*", (), True)
    excludes: TalkgroupRule = TalkgroupRule("", (), False)
    extra: dict[str, str] | None = None

    def matches(self, talkgroup: int) -> bool:
        return self.talkgroups.matches(talkgroup) and not self.excludes.matches(talkgroup)


@dataclass(frozen=True)
class Profile:
    name: str
    enabled: bool
    methods: dict[str, bool]


@dataclass(frozen=True)
class Settings:
    path: Path
    default_profile: str
    database: Path
    spool_dir: Path
    log_level: str
    ffmpeg: str
    mp3_bitrate: str
    retry_max_attempts: int
    retry_base_seconds: int
    retry_max_seconds: int
    methods: dict[str, bool]
    profiles: dict[str, Profile]
    destinations: tuple[Destination, ...]

    def matching(self, profile: str, talkgroup: int) -> list[Destination]:
        profile_obj = self.profiles[profile]
        result = []
        for destination in self.destinations:
            if destination.profile != profile or not destination.enabled or not profile_obj.enabled:
                continue
            if not self.methods.get(destination.type, False) or not profile_obj.methods.get(destination.type, False):
                continue
            if destination.matches(talkgroup): result.append(destination)
        # Rdio is deliberately first-match-only.
        rdio = [d for d in result if d.type == "rdio"]
        return [next(iter(rdio))] + [d for d in result if d.type != "rdio"] if rdio else result


def _required(options: configparser.SectionProxy, keys: set[str], section: str, errors: list[str]) -> None:
    for key in keys:
        if not options.get(key, "").strip(): errors.append(f"[{section}] missing required setting {key}")


def _credential(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered not in PLACEHOLDERS and not lowered.startswith("change") and not lowered.startswith("<")


def _int_setting(options: configparser.SectionProxy | dict, key: str, default: str, errors: list[str]) -> int:
    value = options.get(key, default)
    try:
        result = int(value)
        if result < 0: raise ValueError
        return result
    except (TypeError, ValueError):
        errors.append(f"[general] {key}: expected a non-negative integer, got {value!r}")
        return int(default)


def load_config(path: str | Path, validate_only: bool = False) -> Settings:
    path = Path(path)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    errors: list[str] = []
    try: parser.read(path)
    except (OSError, configparser.Error) as exc:
        raise ValueError(f"cannot read configuration {path}: {exc}") from exc
    allowed_sections = {"general", "method:rdio", "method:icad", "method:trunk-recording"}
    allowed_keys = {
        "general": {"config_version", "default_profile", "database", "spool_dir", "log_level", "ffmpeg", "mp3_bitrate", "retry_max_attempts", "retry_base_seconds", "retry_max_seconds"},
        "profile": {"enabled", "rdio_enabled", "icad_enabled", "trunk_recording_enabled"},
        "method": {"enabled"},
        "rdio": COMMON,
        "icad": COMMON | {"protocol"},
        "trunk-recording": COMMON | {"auth_id"},
    }
    for section in parser.sections():
        kind, _, _ = section.partition(":")
        if section not in allowed_sections and kind not in {"profile", "rdio", "icad", "trunk-recording"}:
            errors.append(f"[{section}] unknown section")
            continue
        valid = allowed_keys.get(kind, allowed_keys.get("method", set()))
        for key in parser[section]:
            if key not in valid: errors.append(f"[{section}] unknown configuration key {key}")
    if "general" not in parser:
        errors.append("missing [general] section")
    general = parser["general"] if "general" in parser else {}
    default_profile = general.get("default_profile", "").strip()
    database = Path(general.get("database", "data/uploader.sqlite3"))
    spool = Path(general.get("spool_dir", "spool"))
    base_dir = path.parent.parent if path.parent.name == "config" else path.parent
    if not database.is_absolute(): database = base_dir / database
    if not spool.is_absolute(): spool = base_dir / spool
    methods: dict[str, bool] = {}
    for method in ("rdio", "icad", "trunk-recording"):
        section = f"method:{method}"
        if section not in parser: errors.append(f"missing [{section}] section"); methods[method] = False
        else:
            try: methods[method] = parse_bool(parser[section].get("enabled", ""), f"[{section}] enabled")
            except ValueError as exc: errors.append(str(exc)); methods[method] = False
    profiles: dict[str, Profile] = {}
    for section in parser.sections():
        if not section.startswith("profile:"): continue
        name = section.split(":", 1)[1]
        try:
            enabled = parse_bool(parser[section].get("enabled", ""), f"[{section}] enabled")
            pmethods = {m: parse_bool(parser[section].get(f"{m.replace('-', '_')}_enabled", "yes"), f"[{section}] {m}_enabled") for m in methods}
        except ValueError as exc: errors.append(str(exc)); continue
        profiles[name] = Profile(name, enabled, pmethods)
    if default_profile and default_profile not in profiles: errors.append(f"default_profile {default_profile!r} is not defined")
    destinations: list[Destination] = []
    seen: set[str] = set()
    for section in parser.sections():
        if ":" not in section or section.split(":", 1)[0] not in {"rdio", "icad", "trunk-recording"}: continue
        kind, name = section.split(":", 1); opts = parser[section]
        try: enabled = parse_bool(opts.get("enabled", ""), f"[{section}] enabled")
        except ValueError as exc: errors.append(str(exc)); enabled = False
        profile = opts.get("profile", "").strip()
        if profile not in profiles: errors.append(f"[{section}] references unknown profile {profile!r}")
        key = name
        if key in seen: errors.append(f"duplicate destination {kind}:{name}")
        seen.add(key)
        required = {"enabled", "profile", "url", "api_key", "system_id", "talkgroups"}
        if kind == "trunk-recording": required |= {"auth_id"}
        _required(opts, required, section, errors)
        try:
            allow = parse_talkgroups(opts.get("talkgroups", ""), f"[{section}] talkgroups")
            deny = parse_talkgroups(opts.get("exclude_talkgroups", ""), f"[{section}] exclude_talkgroups")
        except ValueError as exc: errors.append(str(exc)); allow = TalkgroupRule("", (), False); deny = allow
        if enabled:
            if not _credential(opts.get("api_key", "")): errors.append(f"[{section}] enabled destination has placeholder api_key")
            if kind == "trunk-recording" and not _credential(opts.get("auth_id", "")): errors.append(f"[{section}] enabled destination has placeholder auth_id")
            parsed = urlparse(opts.get("url", ""))
            if parsed.scheme not in {"http", "https"}: errors.append(f"[{section}] enabled destination has invalid URL")
        destinations.append(Destination(kind, name, enabled, profile, opts.get("url", "").strip(), opts.get("api_key", "").strip(), opts.get("system_id", "").strip(), opts.get("auth_id", "").strip(), opts.get("protocol", "call-upload").strip(), allow, deny, dict(opts)))
    for profile in profiles:
        routes = [d for d in destinations if d.type == "rdio" and d.profile == profile and d.enabled]
        for i, left in enumerate(routes):
            for right in routes[i + 1:]:
                if rules_overlap(left.talkgroups, right.talkgroups): errors.append(f"[{profile}] overlapping Rdio routes {left.name} and {right.name}")
    retry_max_attempts = _int_setting(general, "retry_max_attempts", "8", errors)
    retry_base_seconds = _int_setting(general, "retry_base_seconds", "30", errors)
    retry_max_seconds = _int_setting(general, "retry_max_seconds", "3600", errors)
    if retry_max_attempts == 0: errors.append("[general] retry_max_attempts must be greater than zero")
    if retry_max_seconds < retry_base_seconds: errors.append("[general] retry_max_seconds must be at least retry_base_seconds")
    if errors: raise ValueError("configuration validation failed:\n" + "\n".join(f"- {e}" for e in errors))
    return Settings(path, default_profile, database, spool, general.get("log_level", "INFO"), general.get("ffmpeg", "/usr/bin/ffmpeg"), general.get("mp3_bitrate", "64k"), retry_max_attempts, retry_base_seconds, retry_max_seconds, methods, profiles, tuple(destinations))

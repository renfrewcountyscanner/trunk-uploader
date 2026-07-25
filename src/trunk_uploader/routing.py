from __future__ import annotations

from .config import Settings
from .model import Call


def select_profile(call: Call, settings: Settings, explicit: str | None = None) -> str:
    if explicit:
        if explicit not in settings.profiles: raise ValueError(f"unknown profile {explicit!r}")
        return explicit
    for key in ("shortName", "short_name", "system", "systemName", "system_name"):
        value = call.original.get(key)
        if isinstance(value, str) and value in settings.profiles: return value
    if settings.default_profile in settings.profiles: return settings.default_profile
    raise ValueError("no profile selected: provide --profile, metadata mapping, or default_profile")

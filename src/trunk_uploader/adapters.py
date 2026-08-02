from __future__ import annotations

import json
import mimetypes
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

from .config import Destination, Settings
from .http import ResponseResult, classify
from .model import Call


def url_join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def source_json(call: Call) -> str:
    return json.dumps([{"pos": source.get("pos", source.get("position", 0)), "src": source.get("src", source.get("source", "")), **({"tag": source["tag"]} if source.get("tag") else {})} for source in call.sources], separators=(",", ":"))


def _call_identifier(response: requests.Response) -> str | None:
    """Extract a Trunk Recording call identifier from compatible responses."""
    try:
        payload = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        payload = None

    names = {"callaudioid", "callid", "id", "identifier"}

    def find(value: object) -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).replace("_", "").replace("-", "").lower() in names and item not in (None, ""):
                    candidate = str(item).strip()
                    if candidate.lower() not in {"invalid api request", "invalid callaudioid", "none", "null"}:
                        return candidate
            for item in value.values():
                found = find(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = find(item)
                if found:
                    return found
        elif isinstance(value, str) and value.strip():
            candidate = value.strip()
            if candidate.lower() not in {"invalid api request", "invalid callaudioid", "none", "null"}:
                return candidate
        return None

    found = find(payload)
    if found:
        return found

    location = response.headers.get("Location", "").rstrip("/")
    if location:
        candidate = location.rsplit("/", 1)[-1]
        if candidate and candidate.lower() not in {"invalid api request", "invalid callaudioid", "none", "null"}:
            return candidate

    if payload is not None:
        return None

    text = response.text.strip()
    if text and len(text) <= 256 and "<" not in text and "\n" not in text and "\r" not in text and text.lower() not in {"invalid api request", "invalid callaudioid", "none", "null"}:
        return text
    return None


class RdioAdapter:
    def __init__(self, timezone_name: str = "UTC"):
        self.timezone = ZoneInfo(timezone_name)

    def upload(self, call: Call, destination: Destination, audio: Path) -> ResponseResult:
        if call.encrypted: return ResponseResult(True, False, None, "encrypted call skipped")
        fields = {
            "key": destination.api_key, "system": destination.system_id, "systemLabel": call.system_short_name,
            "talkgroup": str(call.talkgroup), "talkgroupGroup": str(call.original.get("talkgroup_group", "")),
            "talkgroupLabel": call.talkgroup_tag, "talkgroupTag": call.talkgroup_tag,
            "talkgroupName": call.talkgroup_description, "dateTime": datetime.fromtimestamp(call.start_time, self.timezone).isoformat(timespec="seconds"),
            "frequency": str(call.frequency), "frequencies": json.dumps(call.original.get("freqList", []), separators=(",", ":")),
            "sources": source_json(call), "patches": json.dumps(list(call.patches), separators=(",", ":")),
        }
        fields["audioName"] = audio.name
        fields["audioType"] = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
        fields = {k: v for k, v in fields.items() if v != ""}
        mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
        try:
            with audio.open("rb") as fh:
                response = requests.post(url_join(destination.url, "/api/call-upload"), data=fields, files={"audio": (audio.name, fh, mime)}, headers={"Expect": ""}, timeout=(15, 120))
            return classify(response)
        except (OSError, requests.RequestException) as exc: return classify(error=exc)


class IcadAdapter:
    def upload(self, call: Call, destination: Destination, audio: Path) -> ResponseResult:
        if destination.protocol == "tone-detect":
            field = "file"; fields = {"api_key": destination.api_key, **call.original}
        else:
            field = "audio"; fields = {"key": destination.api_key, "talkgroup": str(call.talkgroup), "start_time": str(call.start_time), "freq": str(call.frequency), "frequency": str(call.frequency), "source": call.system_short_name, "src": call.system_short_name, "profile": call.system_short_name, "system": destination.system_id, "system_id": destination.system_id, "audio_type": call.audio_type, "talkgroup_tag": call.talkgroup_tag, "talkgroup_description": call.talkgroup_description}
        fields = {k: v for k, v in fields.items() if v not in ("", None)}
        try:
            with audio.open("rb") as fh:
                headers = {"Authorization": f"Bearer {destination.api_key}", "X-API-Key": destination.api_key}
                response = requests.post(destination.url, data=fields, files={field: (audio.name, fh, mimetypes.guess_type(audio.name)[0] or "application/octet-stream")}, headers=headers, timeout=(15, 120))
            return classify(response)
        except (OSError, requests.RequestException) as exc: return classify(error=exc)


class TrunkRecordingAdapter:
    def __init__(self, settings: Settings, converter: Callable[[Call, Path], Path] | None = None):
        self.settings = settings; self.converter = converter or self.convert

    def convert(self, call: Call, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([self.settings.ffmpeg, "-y", "-i", str(call.audio_path), "-ac", "1", "-codec:a", "libmp3lame", "-b:a", self.settings.mp3_bitrate, str(output)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output

    def upload(self, call: Call, destination: Destination, audio: Path) -> ResponseResult:
        start_time = datetime.fromtimestamp(call.start_time, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        original = call.original
        first_source = call.sources[0] if call.sources else {}
        exact_name = destination.extra.get("receiver_name_exact", "").strip().lower() in {"yes", "true", "1", "on"}
        receiver_name = destination.receiver_name if exact_name else (destination.receiver_name or "Trunk-Recorder " + destination.system_id)
        talkgroup_info = {
            "callTargets": [{"targetid": call.talkgroup, "targetlabel": call.talkgroup_description, "targettag": call.talkgroup_tag}],
            # The logger derives receiver_id from this field. Exact receiver
            # mode controls the legacy apiAuthID display behavior; it must not
            # erase the receiver metadata sent to the destination.
            "receiver": receiver_name,
            "receiverVCO": original.get("receiverVCO", original.get("receiver_vco", 0)),
            "frequency": call.frequency,
            "sourceid": first_source.get("src", first_source.get("source", original.get("sourceid", ""))),
            "sourcelabel": first_source.get("label", first_source.get("tag", original.get("sourcelabel", ""))),
            "sourcetag": first_source.get("tag", original.get("sourcetag", "")),
            "lcn": original.get("lcn", ""),
            "voiceservice": original.get("voiceservice", original.get("voice_service", "")),
            "systemid": destination.system_id,
            "systemlabel": original.get("systemlabel", call.system_short_name),
            "systemtype": original.get("systemtype", original.get("system_type", "")),
            "siteid": original.get("siteid", original.get("site_id", "")),
            "sitelabel": original.get("sitelabel", original.get("site_label", "")),
            "calltype": original.get("calltype", original.get("call_type", "1")),
        }
        # Trunk Recorder prepends apiAuthID to the displayed receiver name.
        # Exact mode keeps the configured literal receiver name as apiAuthID;
        # receiver metadata is still sent explicitly above for the logger.
        auth_id = destination.receiver_name if exact_name else destination.auth_id
        metadata = {"apiAuthID": auth_id, "apiKey": destination.api_key, "callAudioFormat": "mp3", "recordedCall": {"talkGroupInfo": talkgroup_info, "startTime": start_time, "callDuration": call.duration, "startPositionSec": "00:00:00"}}
        headers = {"Authorization": f"Bearer {destination.api_key}", "X-API-Key": destination.api_key}
        try:
            response = requests.post(url_join(destination.url, "/api/callupload"), json=metadata, headers=headers, timeout=(15, 120))
            first = classify(response)
            if not first.success: return first
            call_id = _call_identifier(response)
            if not call_id: return ResponseResult(False, False, response.status_code, "metadata response missing call identifier")
            with audio.open("rb") as fh:
                second = requests.post(url_join(destination.url, f"/api/callaudioupload/{call_id}"), data=fh, headers={**headers, "Content-Type": "audio/mpeg", "Content-Length": str(audio.stat().st_size)}, timeout=(15, 120))
            return classify(second)
        except (OSError, requests.RequestException, subprocess.SubprocessError) as exc: return classify(error=exc)

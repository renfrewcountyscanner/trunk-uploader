#!/usr/bin/env python3
"""Scan all trunk-uploader deployments and update the registry.

Reads local and remote config/uploader.conf files, extracts deployment
metadata and API keys, and writes:
  - config/deployments.json   (safe for git)
  - config/secrets.env        (gitignored, chmod 600)
"""
from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENTS_FILE = ROOT / "config" / "deployments.json"
SECRETS_FILE = ROOT / "config" / "secrets.env"

LOCAL_HOST = "local"
SSH_USER = "root"
REMOTE_HOSTS = [
    "firepage-ottawa.tail564fa.ts.net",
    "rcs-kingston.tail564fa.ts.net",
]

LOCAL_APPS = [
    ("fleetnet-pembroke", "/app/fleetnet-pembroke"),
    ("fire-paging", "/app/fire-paging/trunk-uploader"),
    ("ems-paging", "/app/ems-paging/trunk-uploader"),
    ("sears", "/app/sears/trunk-uploader"),
    ("lmrn", "/app/lmrn/trunk-uploader"),
]

REMOTE_APPS: dict[str, list[tuple[str, str]]] = {
    "firepage-ottawa.tail564fa.ts.net": [
        ("fleetnet-multi", "/app/fleetnet-multi/trunk-uploader"),
        ("trunk-paging", "/app/trunk-paging/trunk-uploader"),
    ],
    "rcs-kingston.tail564fa.ts.net": [
        ("fleetnet-kingston", "/app/fleetnet-kingston/trunk-uploader"),
        ("trunk-paging", "/app/trunk-paging/trunk-uploader"),
    ],
}


@dataclass
class Destination:
    name: str
    type: str
    profile: str
    enabled: bool
    url: str
    api_key: str
    system_id: str = ""
    auth_id: str = ""
    receiver_name: str = ""


@dataclass
class App:
    name: str
    directory: str
    container: str = ""
    image: str = ""
    config_file: str = "config/uploader.conf"
    profiles: list[str] = field(default_factory=list)
    destinations: list[Destination] = field(default_factory=list)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def load_secrets_env(path: Path) -> dict[str, str]:
    secrets: dict[str, str] = {}
    if not path.exists():
        return secrets
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            secrets[key.strip()] = value.strip()
    return secrets


def save_secrets_env(path: Path, secrets: dict[str, str]) -> None:
    lines = ["# Trunk-uploader deployment secrets", "# chmod 600 this file", ""]
    for key in sorted(secrets):
        value = secrets[key]
        if " " in value or "#" in value:
            value = f"'{value}'"
        lines.append(f"{key}={value}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)


def load_deployments(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"last_scan": None, "ssh_password_ref": "SSH_PASSWORD", "hosts": []}


def save_deployments(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_remote_file(host: str, user: str, password: str, remote_path: str) -> str:
    cmd = [
        "sshpass", "-p", password,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{user}@{host}", f"cat '{remote_path}'",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"failed to read {remote_path} on {host}: {result.stderr}")
    return result.stdout


def discover_container(host: str, user: str, password: str, app_dir: str) -> tuple[str, str]:
    """Guess the container that bind-mounts app_dir. Returns (name, image)."""
    if host == LOCAL_HOST:
        list_cmd = ["docker", "ps", "--format", "{{.Names}}|{{.Image}}"]
    else:
        list_cmd = [
            "sshpass", "-p", password,
            "ssh", "-o", "StrictHostKeyChecking=no",
            f"{user}@{host}",
            "docker ps --format '{{.Names}}|{{.Image}}'",
        ]
    result = subprocess.run(list_cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return ("", "")

    host_bind = app_dir.replace("/trunk-uploader", "")
    for line in result.stdout.splitlines():
        parts = line.split("|", 1)
        if len(parts) != 2:
            continue
        name, image = parts

        if host == LOCAL_HOST:
            inspect_cmd = ["docker", "inspect", name, "--format", "{{json .HostConfig.Binds}}"]
        else:
            inspect_cmd = [
                "sshpass", "-p", password,
                "ssh", "-o", "StrictHostKeyChecking=no",
                f"{user}@{host}",
                f"docker inspect {name} --format '{{{{json .HostConfig.Binds}}}}'",
            ]
        inspect_result = subprocess.run(inspect_cmd, capture_output=True, text=True, timeout=30)
        if inspect_result.returncode != 0:
            continue
        binds = inspect_result.stdout
        if host_bind in binds:
            return (name, image)
    return ("", "")


def parse_uploader_conf(text: str) -> tuple[list[str], list[Destination]]:
    parser = configparser.ConfigParser()
    parser.read_string(text)

    profiles: list[str] = []
    destinations: list[Destination] = []

    for section in parser.sections():
        if section.startswith("profile:"):
            profile_name = section.split(":", 1)[1]
            if parser.getboolean(section, "enabled", fallback=False):
                profiles.append(profile_name)
            continue

        if ":" not in section:
            continue

        kind, name = section.split(":", 1)
        if kind not in {"rdio", "icad", "trunk-recording"}:
            continue

        enabled = parser.getboolean(section, "enabled", fallback=False)
        profile = parser.get(section, "profile", fallback="")
        url = parser.get(section, "url", fallback="")
        api_key = parser.get(section, "api_key", fallback="")
        system_id = parser.get(section, "system_id", fallback="")
        auth_id = parser.get(section, "auth_id", fallback="")
        receiver_name = parser.get(section, "receiver_name", fallback="")

        destinations.append(
            Destination(
                name=name,
                type=kind,
                profile=profile,
                enabled=enabled,
                url=url,
                api_key=api_key,
                system_id=system_id,
                auth_id=auth_id,
                receiver_name=receiver_name,
            )
        )

    return profiles, destinations


def build_key_registry(destinations: list[Destination]) -> dict[tuple[str, str], str]:
    """Assign a unique ref name to every unique (url, api_key) pair."""
    seen: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}

    for dest in destinations:
        pair = (dest.url, dest.api_key)
        if pair in seen:
            continue

        host = ""
        if dest.url:
            host = re.sub(r"^https?://", "", dest.url).split(":")[0].split("/")[0]

        if host == "rdio.dyndns.org":
            base = "rdio-dyndns"
        elif host == "radio.firepage.ca":
            base = "radio-firepage"
        elif host == "icad.firepage.ca":
            base = "icad-firepage"
        elif host == "logger-api.renfrewcountyscanner.com":
            base = f"logger-{slugify(dest.system_id or dest.name)}"
        elif dest.type == "trunk-recording":
            base = f"logger-{slugify(dest.system_id or dest.name)}"
        else:
            base = f"{dest.type}-{slugify(host or 'unknown')}"

        if base in counters:
            counters[base] += 1
            ref = f"{base}-{counters[base]}"
        else:
            ref = base
            counters[base] = 1

        seen[pair] = ref.upper().replace("-", "_")

    return seen


def scan_app(
    host: str,
    user: str,
    password: str,
    app_name: str,
    app_dir: str,
) -> App:
    config_path = f"{app_dir}/config/uploader.conf"
    if host == LOCAL_HOST:
        text = Path(config_path).read_text(encoding="utf-8")
    else:
        text = read_remote_file(host, user, password, config_path)

    profiles, destinations = parse_uploader_conf(text)
    container, image = discover_container(host, user, password, app_dir)

    return App(
        name=app_name,
        directory=app_dir,
        container=container,
        image=image,
        config_file="config/uploader.conf",
        profiles=profiles,
        destinations=destinations,
    )


def main() -> int:
    secrets = load_secrets_env(SECRETS_FILE)
    ssh_password = secrets.get("SSH_PASSWORD", "")
    if not ssh_password:
        ssh_password = "4mbul4nc3!"
        secrets["SSH_PASSWORD"] = ssh_password

    all_destinations: list[Destination] = []
    hosts_data: list[dict[str, Any]] = []

    # Local scan
    local_apps: list[App] = []
    for app_name, app_dir in LOCAL_APPS:
        if not Path(app_dir).is_dir():
            print(f"[skip] local app directory not found: {app_dir}")
            continue
        app = scan_app(LOCAL_HOST, "", "", app_name, app_dir)
        local_apps.append(app)
        all_destinations.extend(app.destinations)

    hosts_data.append({
        "hostname": LOCAL_HOST,
        "apps": [{
            "name": a.name,
            "directory": a.directory,
            "container": a.container,
            "image": a.image,
            "config_file": a.config_file,
            "profiles": a.profiles,
            "destinations": [],  # filled after key refs are built
        } for a in local_apps],
    })

    # Remote scan
    remote_apps_by_host: dict[str, list[App]] = {}
    for host in REMOTE_HOSTS:
        remote_apps: list[App] = []
        for app_name, app_dir in REMOTE_APPS.get(host, []):
            try:
                app = scan_app(host, SSH_USER, ssh_password, app_name, app_dir)
                remote_apps.append(app)
                all_destinations.extend(app.destinations)
            except Exception as exc:
                print(f"[error] {host}:{app_dir}: {exc}")
                continue
        remote_apps_by_host[host] = remote_apps

        hosts_data.append({
            "hostname": host,
            "username": SSH_USER,
            "apps": [{
                "name": a.name,
                "directory": a.directory,
                "container": a.container,
                "image": a.image,
                "config_file": a.config_file,
                "profiles": a.profiles,
                "destinations": [],
            } for a in remote_apps],
        })

    # Build key refs from all scanned destinations
    key_registry = build_key_registry(all_destinations)
    new_secrets: dict[str, str] = {"SSH_PASSWORD": ssh_password}
    for (url, api_key), ref in key_registry.items():
        new_secrets[ref] = api_key

    # Map (host, app_name) -> App for all successfully scanned apps
    app_map: dict[tuple[str, str], App] = {}
    for app in local_apps:
        app_map[(LOCAL_HOST, app.name)] = app
    for host, remote_apps in remote_apps_by_host.items():
        for app in remote_apps:
            app_map[(host, app.name)] = app

    for host_data in hosts_data:
        host = host_data["hostname"]
        for app_data in host_data["apps"]:
            app = app_map.get((host, app_data["name"]))
            if not app:
                continue
            app_data["destinations"] = []
            for dest in app.destinations:
                ref = key_registry.get((dest.url, dest.api_key), "")
                app_data["destinations"].append({
                    "name": dest.name,
                    "type": dest.type,
                    "enabled": dest.enabled,
                    "profile": dest.profile,
                    "url": dest.url,
                    "api_key_ref": ref,
                    "system_id": dest.system_id,
                    "auth_id": dest.auth_id,
                    "receiver_name": dest.receiver_name,
                })

    deployments = {
        "last_scan": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ssh_password_ref": "SSH_PASSWORD",
        "hosts": hosts_data,
    }

    save_deployments(DEPLOYMENTS_FILE, deployments)
    save_secrets_env(SECRETS_FILE, new_secrets)

    print(f"[ok] wrote {DEPLOYMENTS_FILE}")
    print(f"[ok] wrote {SECRETS_FILE} (mode {oct(SECRETS_FILE.stat().st_mode)[-3:]})")
    print(f"[info] scanned {len(hosts_data)} hosts, {sum(len(h['apps']) for h in hosts_data)} apps, {len(key_registry)} unique API keys")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

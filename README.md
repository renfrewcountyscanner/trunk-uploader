# trunk-uploader

`trunk-uploader` is a production-oriented Linux uploader for Trunk Recorder. One invocation can independently deliver a call to Rdio Scanner, iCAD Dispatch, and Trunk Recording, with profile routing, talkgroup filtering, durable retries, and no mutation of the original Trunk Recorder files.

## Install

Requirements are Linux, Python 3.11+, and `/usr/bin/ffmpeg` for Trunk Recording destinations.

```sh
cd /app/trunk-uploader
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
cp config/uploader.conf.example config/uploader.conf
chmod 600 config/uploader.conf   # recommended; performed manually
```

The real configuration is ignored by Git. Keep credentials only in that file.

## Layout

- `src/trunk_uploader/`: package implementation
- `bin/universal-upload.sh`: Trunk Recorder wrapper
- `config/`: example and local configuration
- `data/`: SQLite state
- `spool/`: immutable retry copies
- `reference/`: legacy protocol references and fixtures; not application source

## Configuration

Configuration is INI only. Methods, profiles, and destinations have independent `enabled = yes/no` controls. Each destination must specify a profile, URL, credentials, system ID, and allow/exclude talkgroup rules.

Talkgroup rules support `*`, exact IDs, comma-separated IDs, and inclusive ranges. Exclusions take precedence. Rdio routes must not overlap within one profile; Rdio uses the first matching route.

Validate everything at once:

```sh
python3 -m trunk_uploader.cli --config config/uploader.conf validate
```

## Trunk Recorder

Configure one script:

```json
"uploadScript": "/app/trunk-uploader/bin/universal-upload.sh"
```

Or select an explicit profile:

```json
"uploadScript": "/app/trunk-uploader/bin/universal-upload.sh fleetnet-pembroke"
```

The wrapper accepts `WAV JSON [M4A]` and `PROFILE WAV JSON [M4A]`. It uses an explicit profile first, then metadata names such as `shortName`/`short_name`/`system`, then `default_profile`.

## Dry runs and retries

```sh
python3 -m trunk_uploader.cli --config config/uploader.conf dry-run call.wav call.json call.m4a
python3 -m trunk_uploader.cli --config config/uploader.conf pending
python3 -m trunk_uploader.cli --config config/uploader.conf status
python3 -m trunk_uploader.cli --config config/uploader.conf retry
```

Dry-run parses the files, prints normalized metadata, matching destinations and skips, and never contacts an endpoint. API keys are redacted.

Uploads copy the required files into `spool/<fingerprint>/` before queue insertion. SQLite WAL mode and a unique destination constraint prevent duplicate rows. Retryable network failures and HTTP 408, 429, and 5xx responses use bounded exponential backoff. Successful destinations are never resent.

## Destination notes

Rdio uses the current `/api/call-upload` multipart fields and skips encrypted calls by default. iCAD uses multipart `/api/call-upload` with the compatibility form key and both requested authentication headers. `protocol = tone-detect` is available for legacy iCAD endpoints but is disabled by default. Trunk Recording converts audio once to mono MP3, submits metadata, then uploads audio using the returned identifier.

## Migration

Review `docs/reference-analysis.md` and `config/migration-profiles.conf.example`. The old FleetNet scripts used these intended mappings:

```text
8000-8999, 9056, 17344 -> 100
9000-9999             -> 101
34000-36999            -> 102
3000-3999              -> 103
38000-38999            -> 104
2000-2999              -> 105
```

The legacy filename regexes did not reliably implement five-digit ranges. Verify the active talkgroup metadata before enabling production routes.

Rollback is simply restoring the previous Trunk Recorder `uploadScript` and disabling the new destinations. The uploader never modifies the source recordings.

## Tests and live-test procedure

Run tests without production endpoints:

```sh
python3 -m pytest
```

Before enabling a real URL, require a passing validation, passing tests, and a reviewed dry run. The first live test should copy one completed WAV/JSON/M4A call to a separate test directory and invoke the uploader manually against one reviewed destination. Do not use an actively changing Trunk Recorder call.

## Troubleshooting

- Validation errors list unknown keys, missing fields, malformed rules, overlaps, and placeholder credentials.
- `pending` shows queued and retrying destinations; `status` shows counts by state.
- A missing FFmpeg binary affects only Trunk Recording destinations.
- HTTP 4xx errors other than 408/429 are recorded as permanent failures.
- Check that the service account can write `data/` and `spool/`, while the original recording directories remain read-only if desired.

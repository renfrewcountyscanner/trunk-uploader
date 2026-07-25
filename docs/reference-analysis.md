# Reference analysis

The archives under `reference/` were inspected as protocol and migration references only. They are not application source.

## Discovered implementations

- `ems-paging` and `fire-paging` contain Rdio plugin configuration, iCAD `/api/call-upload` scripts, and older `/tone_detect` uploaders.
- `trunk-pagingOTT` contains iCAD destinations for East Renfrew and Lanark, Rdio configurations, and the Trunk Recording uploader.
- `trunk-pagingKING` contains Frontenac and Lanark tone-detect destinations plus Rdio system 10.
- `fleetnet-kingston`, `fleetnet-multi`, and `fleetnet-pembroke` contain legacy filename-regex Rdio scripts and the two-stage Trunk Recording uploader.

## Rdio protocol

The current Trunk Recorder plugin posts multipart data to `<server>/api/call-upload`. Required fields observed in the upstream implementation are `audio`, `audioName`, `audioType`, `dateTime`, `frequencies`, `frequency`, `key`, `patches`, `talkgroup`, `talkgroupGroup`, `talkgroupLabel`, `talkgroupTag`, `talkgroupName`, `sources`, `system`, and `systemLabel`. The upstream plugin skips encrypted calls, accepts every 2xx status, and uses 15-second connect and 120-second total timeouts.

The older local Python handler instead targets `/api/trunk-recorder-call-upload` and sends only `key`, `system`, `audio`, and `meta`. The rewrite follows the current protocol.

## iCAD protocol

The newer scripts post multipart audio to a complete `/api/call-upload` URL. They send `key` as a form field and also use `Authorization: Bearer <key>` and `X-API-Key: <key>`. Useful fields are `talkgroup`, `start_time`, `freq`, `src`, `system`, `system_id`, `audio_type`, `talkgroup_tag`, and `talkgroup_description`.

The older adapter posts an MP3 under `file` to `/tone_detect`. It is retained only as a separately selectable, disabled-by-default protocol.

## Trunk Recording protocol

The legacy implementation posts JSON metadata to `/api/callupload`, reads `CallAudioID`, then sends MP3 bytes to `/api/callaudioupload/<CallAudioID>`. Metadata contains `apiAuthID`, `apiKey`, `callAudioFormat`, and a `recordedCall` object containing `talkGroupInfo`, `startTime`, and `callDuration`.

## Invocation conventions

Legacy wrappers use both audio-first and profile/site-first forms. Some wrappers derive the system from JSON; others use a fixed site. The new wrapper accepts `WAV JSON [M4A]` and `PROFILE WAV JSON [M4A]` and uses metadata only for profile mapping.

## FleetNet mapping

The active FleetNet scripts repeat this mapping:

| Talkgroup | Rdio system |
|---|---:|
| 8000-8999, 9056, 17344 | 100 |
| 9000-9999 | 101 |
| 34000-36999 | 102 |
| 3000-3999 | 103 |
| 38000-38999 | 104 |
| 2000-2999 | 105 |

The legacy shell regexes match four-digit filename fragments and therefore do not reliably implement the five-digit ranges. The rewrite uses JSON talkgroup metadata and records this as a migration discrepancy rather than silently copying the bug.

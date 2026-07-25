# Design

`trunk-uploader` is a process-per-call Python application with a shared SQLite queue and immutable spool files.

1. Parse INI configuration strictly.
2. Read and normalize the Trunk Recorder JSON without modifying it.
3. Select a profile from the explicit argument, metadata aliases, or `default_profile`.
4. Copy audio and metadata atomically into a fingerprinted spool directory.
5. Resolve independent destination matches.
6. Insert one durable queue row per destination using a unique constraint.
7. Attempt pending destinations independently.
8. Mark success, permanent failure, or retryable failure.

The original Trunk Recorder paths are never used as a deletion or rename target. Retry attempts use only spool copies. Temporary MP3 conversion is keyed by call fingerprint and shared by matching Trunk Recording destinations.

The queue has one row per `(call_fingerprint, destination_type, destination_name)`. A successful row is never attempted again. Retryable errors use bounded exponential backoff.

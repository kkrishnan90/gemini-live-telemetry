# Changelog — All Unpushed Changes

**Base:** GitHub `origin/main` → `d5da9fc` (Release v0.1.1)  
**Total:** 11 files changed, **+838 insertions**, −11 deletions

The local `origin/main` ref was stale — `git fetch` was needed. All changes
below are NOT on GitHub yet.

---

## Feature: EventSink + Pub/Sub Real-Time Streaming

### What & Why

v0.1.1 exports metrics via two channels:
1. **OTel → Cloud Monitoring** — batched every 15s
2. **JSON file** — flushed every 30s

This changeset adds a **third channel**: per-event, real-time streaming
through a pluggable `EventSink` interface. The first implementation
targets **Google Cloud Pub/Sub**.

---

## New Files (6 files, +695 lines)

### `_event_types.py` (+97)

`TelemetryEvent` dataclass — the unit of data flowing through sinks.

- **18 event type constants**: `SESSION_START`, `SESSION_END`, `SESSION_RESUMPTION`, `TURN_COMPLETE`, `GENERATION_COMPLETE`, `TTFB`, `INTERRUPTION`, `TOOL_CALL`, `TOOL_RESPONSE`, `TOOL_CANCELLATION`, `VAD_SIGNAL`, `VOICE_ACTIVITY`, `AUDIO_SENT`, `AUDIO_RECEIVED`, `USAGE_UPDATE`, `CONTENT_SENT`, `GROUNDING`, `GO_AWAY`
- **3 severity levels**: `SEVERITY_INFO`, `SEVERITY_WARNING`, `SEVERITY_ERROR`
- `to_dict()` / `to_json()` serialization
- `.attributes` property — flat `dict[str, str]` for Pub/Sub message attributes

### `_event_bus.py` (+173)

`EventBus` — async fan-out dispatcher between wrappers and sinks.

- **Non-blocking `emit()`** — safe from both sync and async contexts
- **asyncio.Queue** with 10K cap — overflow drops with warning (never blocks SDK)
- **Background `_dispatch_loop()`** — reads queue, fans out to all registered sinks
- **Error isolation** — one sink failing doesn't affect others
- **Graceful `shutdown()`** — drains remaining events, flushes and closes all sinks
- Properties: `sink_count`, `queue_size`, `is_running`

### `sinks/_protocol.py` (+72)

`EventSink` protocol — PEP 544 structural subtyping, `@runtime_checkable`.

Four async methods:
- `publish(event)` — deliver a single event (must not raise)
- `publish_batch(events)` — batch delivery (default: iterates `publish()`)
- `flush()` — flush internal buffers
- `close()` — release resources

### `sinks/_pubsub.py` (+225)

`PubSubSink` — Google Cloud Pub/Sub implementation.

- Publishes JSON body + message attributes (`event_type`, `session_id`, `severity`)
- **Auto-creates topic** if missing (configurable)
- **Event filter** — optionally publish only specific event types
- **Batching** — 100 msgs / 10ms default via `google-cloud-pubsub` client
- **Lazy import** — `google-cloud-pubsub` only loaded on instantiation
- Error tracking via callback + `.stats` property

### `sinks/__init__.py` (+32)

Re-exports `EventSink` and `PubSubSink`.

### `CHANGELOG.md`

This file.

---

## Modified Files (5 files, +143 / −11)

### `__init__.py` (+60 / −3)

EventBus lifecycle wired into `activate()` and shutdown:

| Change | Detail |
|--------|--------|
| `_event_bus = None` at module level | New state variable. Prevents `AttributeError` before activation. |
| `_event_bus` in `global` statement | Allows `activate()` to assign it. |
| `import logging as _logging` | **Bug prevention**: ensures `logging` is available for Pub/Sub section (was only in `except` blocks). |
| EventBus + PubSubSink setup block | Creates bus, auto-creates PubSubSink if enabled, registers custom sinks. |
| `_event_bus.start()` | Starts the async dispatch loop so events actually reach sinks. |
| `get_event_bus()` accessor | New public API. Added to `__all__`. |
| `_shutdown_hook()` extended | Drains EventBus queue + closes sinks on exit. |

### `_wrappers.py` (+40 / −1)

Added `_emit()` helper and **4 event emissions**:

| Wrapper | Event | Data |
|---------|-------|------|
| `wrap_connect()` — session up | `session_start` | `setup_latency_ms` |
| `wrap_connect()` — finally | `session_end` | `duration_ms` |
| `wrap_send_realtime_input()` | `audio_sent` | `byte_count` |
| `wrap_send_tool_response()` | `tool_response` | `tool_name`, `tool_id`, `round_trip_ms` |

New imports: `TelemetryEvent`, `SESSION_START`, `SESSION_END`, `AUDIO_SENT`, `TOOL_RESPONSE`, `ERROR`, `SEVERITY_ERROR`.

### `_receive_handler.py` (+42 / −0)

Added `_emit()` helper and **9 event emissions**:

| Handler | Event | Data |
|---------|-------|------|
| TTFB detection | `ttfb` | `ttfb_ms`, `vad_mode` |
| `_finalize_turn()` — normal | `turn_complete` | `turn_number`, `duration_ms`, `ttfb_ms`, token counts |
| `_finalize_turn()` — barge-in | `interruption` | same + `was_interrupted=True` |
| `_handle_tool_call()` | `tool_call` | `tool_name`, `tool_id` |
| `_handle_tool_cancellation()` | `tool_cancellation` | `tool_id`, `tool_name` |
| Grounding metadata | `grounding` | `chunk_count`, `queries` |
| Go-away signal | `go_away` | `time_left` (severity=WARNING) |
| Session resumption | `session_resumption` | `resumable` |
| `_handle_vad_signal()` — EOS | `vad_signal` | `signal=EOS`, `source=native` |
| `_handle_vad_signal()` — SOS | `vad_signal` | `signal=SOS`, `source=native` |

New imports: `TelemetryEvent`, `TURN_COMPLETE`, `TTFB`, `TOOL_CALL`, `TOOL_CANCELLATION`, `INTERRUPTION`, `GO_AWAY`, `VAD_SIGNAL`, `GROUNDING`, `SESSION_RESUMPTION`, `SEVERITY_WARNING`.

### `config.py` (+9 / −0)

New fields on `InstrumentationConfig`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_pubsub_export` | `bool` | `False` | Enable Pub/Sub streaming |
| `pubsub_topic` | `str` | `"gemini-live-telemetry"` | Topic name |
| `pubsub_auto_create_topic` | `bool` | `True` | Auto-create if missing |
| `pubsub_event_filter` | `list[str] \| None` | `None` (all) | Filter event types |
| `event_sinks` | `list` | `[]` | Custom EventSink instances |

### `pyproject.toml` (+3 / −0)

Added `[project.optional-dependencies.pubsub]`:
```toml
pubsub = ["google-cloud-pubsub>=2.18.0"]
```

---

## Event Coverage (18 of 18 — all emitted, all carry `turn_number`)

Every event type constant has a corresponding `_emit()` call. No dead constants.
All events except `session_start` and `session_end` include `turn_number` in their data payload for turn correlation.

| # | Event | Source | `turn_number` | Key Data Fields | OTel Equivalent |
|---|-------|--------|:---:|------|-----------------|
| 1 | `session_start` | `_wrappers` | — | `setup_latency_ms` | `sessions_active` (+1), `latency_setup` |
| 2 | `session_end` | `_wrappers` | — | `duration_ms` | `sessions_active` (−1) |
| 3 | `audio_sent` | `_wrappers` | ✅ | `byte_count` | `audio_bytes_sent`, `messages_sent` |
| 4 | `tool_response` | `_wrappers` | ✅ | `tool_name`, `tool_id`, `round_trip_ms` | `latency_tool_round_trip`, `messages_sent` |
| 5 | `content_sent` | `_wrappers` | ✅ | `method` | `messages_sent` |
| 6 | `ttfb` | `_receive_handler` | ✅ | `ttfb_ms`, `vad_mode` | `latency_ttfb` |
| 7 | `turn_complete` | `_receive_handler` | ✅ | `duration_ms`, `ttfb_ms`, `inter_turn_gap_ms`, 6 token counts | `turns_total`, `latency_turn_duration`, `latency_inter_turn_gap`, all token counters |
| 8 | `interruption` | `_receive_handler` | ✅ | same as turn_complete + `was_interrupted` | `turns_interrupted` + same |
| 9 | `tool_call` | `_receive_handler` | ✅ | `tool_name`, `tool_id` | `tool_calls_total` |
| 10 | `tool_cancellation` | `_receive_handler` | ✅ | `tool_id`, `tool_name` | `tool_calls_cancellations` |
| 11 | `grounding` | `_receive_handler` | ✅ | `chunk_count`, `queries` | `grounding_invocations` |
| 12 | `go_away` | `_receive_handler` | ✅ | `time_left` (severity=WARNING) | — |
| 13 | `session_resumption` | `_receive_handler` | ✅ | `resumable` | — |
| 14 | `vad_signal` | `_receive_handler` | ✅ | `signal` (SOS/EOS), `source` | — |
| 15 | `voice_activity` | `_receive_handler` | ✅ | `activity` (START/END), `source` | — |
| 16 | `generation_complete` | `_receive_handler` | ✅ | — | — |
| 17 | `audio_received` | `_receive_handler` | ✅ | `byte_count` | `audio_bytes_received` |
| 18 | `usage_update` | `_receive_handler` | ✅ | `prompt_tokens`, `response_tokens`, `total_tokens` | `messages_received` |

Removed `TRANSCRIPTION` and `ERROR` — had no code path or OTel equivalent.

### Pub/Sub Filter (`app.py`)

`pubsub_event_filter` excludes `audio_sent` and `audio_received` from Pub/Sub (high frequency — one per audio chunk, floods the topic). All 18 events still emitted internally to EventBus/OTel/JSON.

### Bug Fixes

- **turn_number off-by-one**: `_finalize_turn()` now increments `current_turn_number` AFTER building the turn metrics and event, so `turn_complete.turn_number` matches all mid-turn events.
- **`_handle_voice_activity()` missing `timing` param**: Added `timing` parameter so `voice_activity` events can include `turn_number`.

### Verified End-to-End

Tested with multi-turn voice session:
- Events received via `gcloud pubsub subscriptions pull`
- All turn numbers consistent (mid-turn events = turn_complete = same number)
- No audio flooding after filter applied
- Grounding, interruption, VAD signals all captured correctly

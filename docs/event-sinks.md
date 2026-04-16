# Event Sinks — Real-Time Telemetry Streaming

## Overview

Event Sinks provide a **per-event, real-time** streaming channel for telemetry
data. While OTel exports to Cloud Monitoring in 15s batches and JSON files flush
every 30s, Event Sinks deliver individual `TelemetryEvent` objects as they happen.

```
google-genai SDK
    │ wrapt wrappers
    ▼
┌─────────────────────┐
│   _emit(event)      │
└──────┬──────────────┘
       │
  ┌────▼────┐
  │ EventBus │  ← async queue (10K cap), non-blocking
  └────┬────┘
       │ fan-out
  ┌────┴────────────────────┐
  │    │         │          │
  ▼    ▼         ▼          ▼
PubSub  Logging  Webhook  Custom
Sink    Sink     Sink     Sink
```

## Quick Start

```python
from gemini_live_telemetry import activate, InstrumentationConfig

activate(InstrumentationConfig(
    project_id="your-project",
    enable_pubsub_export=True,
    pubsub_topic="gemini-live-telemetry",
))
```

All 18 event types are now streamed to Pub/Sub.

> **Note:** Currently only the `PubSubSink` is implemented. Logging, webhook,
> and other sinks can be added by implementing the `EventSink` protocol
> (see [Custom Sinks](#custom-sinks) below).

## Event Types (18)

### Session Lifecycle
| Event | Trigger | Data |
|-------|---------|------|
| `session_start` | Session connected | `setup_latency_ms` |
| `session_end` | Session disconnected | `duration_ms` |
| `session_resumption` | Resumption update from server | `resumable`, `turn_number` |

### Turn Lifecycle
| Event | Trigger | Data |
|-------|---------|------|
| `ttfb` | First audio response after speech end | `ttfb_ms`, `vad_mode`, `turn_number` |
| `turn_complete` | Model turn finalized | `turn_number`, `duration_ms`, `ttfb_ms`, `inter_turn_gap_ms`, 6 token counts |
| `interruption` | User barge-in | Same as `turn_complete` + `was_interrupted: true` |
| `generation_complete` | Model generation done | `turn_number` |

### Tool Calling
| Event | Trigger | Data |
|-------|---------|------|
| `tool_call` | Model requests tool execution | `tool_name`, `tool_id`, `turn_number` |
| `tool_response` | App sends tool result | `tool_name`, `tool_id`, `round_trip_ms`, `turn_number` |
| `tool_cancellation` | Model cancels tool call | `tool_id`, `tool_name`, `turn_number` |

### Voice Activity Detection
| Event | Trigger | Data |
|-------|---------|------|
| `vad_signal` | Native VAD start/end of speech | `signal` (SOS/EOS), `source`, `turn_number` |
| `voice_activity` | Voice activity start/end | `activity` (START/END), `source`, `turn_number` |

### Audio
| Event | Trigger | Data |
|-------|---------|------|
| `audio_sent` | Audio bytes sent to Gemini | `byte_count`, `turn_number` |
| `audio_received` | Audio chunk from Gemini | `byte_count`, `turn_number` |

### Content and Usage
| Event | Trigger | Data |
|-------|---------|------|
| `usage_update` | Per-message token metadata | `prompt_tokens`, `response_tokens`, `total_tokens`, `turn_number` |
| `content_sent` | Client content sent | `method`, `turn_number` |

### Server Signals
| Event | Trigger | Data |
|-------|---------|------|
| `grounding` | Google Search grounding | `chunk_count`, `queries`, `turn_number` |
| `go_away` | Server go-away signal | `time_left`, `turn_number` (severity=WARNING) |

## Pub/Sub Sink

### Configuration

```python
activate(InstrumentationConfig(
    enable_pubsub_export=True,
    pubsub_topic="my-telemetry-topic",
    pubsub_auto_create_topic=True,
    pubsub_event_filter=[...],    # optional: list of event types to publish
))
```

### `pubsub_event_filter`

Controls which events are published to Pub/Sub. Pass a list of event type strings.
If `None` (default), all 18 types are published.

Available event type strings:

| String | Category |
|--------|----------|
| `"session_start"` | Session |
| `"session_end"` | Session |
| `"session_resumption"` | Session |
| `"ttfb"` | Turn |
| `"turn_complete"` | Turn |
| `"interruption"` | Turn |
| `"generation_complete"` | Turn |
| `"tool_call"` | Tools |
| `"tool_response"` | Tools |
| `"tool_cancellation"` | Tools |
| `"vad_signal"` | VAD |
| `"voice_activity"` | VAD |
| `"audio_sent"` | Audio (high freq) |
| `"audio_received"` | Audio (high freq) |
| `"usage_update"` | Content |
| `"content_sent"` | Content |
| `"grounding"` | Grounding |
| `"go_away"` | Server |

Example — only key events:
```python
pubsub_event_filter=[
    "session_start", "session_end", "ttfb",
    "turn_complete", "interruption",
]
```

### Message Format

Each Pub/Sub message has:
- **Body**: JSON via `event.to_json()`
- **Attributes**: `event_type`, `session_id`, `severity` (enables subscription filters)

```json
{
  "event_type": "turn_complete",
  "session_id": "abc-123",
  "timestamp": 1776322200.53,
  "timestamp_iso": "2026-04-16T06:50:00.538+00:00",
  "severity": "INFO",
  "tags": {},
  "data": {
    "turn_number": 1,
    "duration_ms": 2832.1,
    "ttfb_ms": 154.7,
    "inter_turn_gap_ms": null,
    "was_interrupted": false,
    "prompt_tokens": 758,
    "response_tokens": 241,
    "total_tokens": 999,
    "cached_tokens": 0,
    "tool_use_tokens": 0,
    "thoughts_tokens": 0
  }
}
```

### Subscription Filters

```bash
# Only errors
gcloud pubsub subscriptions create errors \
  --topic=my-topic \
  --message-filter='attributes.severity = "ERROR"'

# Only turn events
gcloud pubsub subscriptions create turns \
  --topic=my-topic \
  --message-filter='attributes.event_type = "turn_complete" OR attributes.event_type = "interruption"'

# Specific session
gcloud pubsub subscriptions create debug \
  --topic=my-topic \
  --message-filter='attributes.session_id = "abc-123"'
```

### Recommended Filter

`audio_sent` and `audio_received` fire per audio chunk (~50ms intervals).
Exclude them from Pub/Sub to avoid flooding:

```python
pubsub_event_filter=[
    "session_start", "session_end", "session_resumption",
    "turn_complete", "generation_complete", "ttfb", "interruption",
    "tool_call", "tool_response", "tool_cancellation",
    "vad_signal", "voice_activity",
    "usage_update", "content_sent",
    "grounding", "go_away",
]
```

## Custom Sinks

Implement the `EventSink` protocol (PEP 544, no inheritance needed):

```python
class MyWebhookSink:
    async def publish(self, event):
        await httpx.post("https://my-webhook.com/events",
                         json=event.to_dict())

    async def publish_batch(self, events):
        for event in events:
            await self.publish(event)

    async def flush(self):
        pass

    async def close(self):
        pass
```

Register via config:
```python
activate(InstrumentationConfig(event_sinks=[MyWebhookSink()]))
```

Or post-activation:
```python
from gemini_live_telemetry import get_event_bus
get_event_bus().register(MyWebhookSink())
```

## EventBus

The EventBus sits between wrappers and sinks:

- **Non-blocking emit** - wrappers call this; never blocks the SDK
- **asyncio.Queue** - 10K cap; overflow drops with warning
- **Fan-out** - each event goes to all registered sinks
- **Error isolation** - one sink failing does not affect others
- **Graceful shutdown** - drains queue, flushes and closes all sinks

## Dependencies

```bash
pip install gemini-live-telemetry[pubsub]
```

The `[pubsub]` extra installs `google-cloud-pubsub>=2.18.0`.
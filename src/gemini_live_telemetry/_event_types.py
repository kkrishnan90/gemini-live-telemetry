"""Telemetry event model — the unit of data flowing through EventSinks.

Each TelemetryEvent is a self-contained, JSON-serializable message
representing a single metric event (turn complete, TTFB, tool call, etc.).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Event type constants ─────────────────────────────────────────────────────
# Maps 1:1 to every distinct metric event in _receive_handler.py and _wrappers.py.

# Session lifecycle
SESSION_START = "session_start"
SESSION_END = "session_end"
SESSION_RESUMPTION = "session_resumption"

# Turn lifecycle
TURN_COMPLETE = "turn_complete"
GENERATION_COMPLETE = "generation_complete"
TTFB = "ttfb"
INTERRUPTION = "interruption"

# Tool calling
TOOL_CALL = "tool_call"
TOOL_RESPONSE = "tool_response"
TOOL_CANCELLATION = "tool_cancellation"

# Voice activity detection
VAD_SIGNAL = "vad_signal"           # Native VAD SOS/EOS from server
VOICE_ACTIVITY = "voice_activity"   # Server-side voice activity start/end

# Audio
AUDIO_SENT = "audio_sent"
AUDIO_RECEIVED = "audio_received"

# Content
USAGE_UPDATE = "usage_update"       # Token usage metadata per message
CONTENT_SENT = "content_sent"       # Client content sent (text/file uploads)

# Search/grounding
GROUNDING = "grounding"

# Server signals
GO_AWAY = "go_away"


# Severity levels
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_ERROR = "ERROR"


@dataclass
class TelemetryEvent:
    """A single telemetry event emitted by the instrumentation.

    Attributes:
        event_type: One of the event type constants (SESSION_START, TURN_COMPLETE, etc.).
        session_id: The Gemini session ID this event belongs to.
        timestamp: Unix timestamp (time.time()) when the event occurred.
        data: Event-specific payload. Varies by event_type — always JSON-serializable.
        severity: INFO, WARNING, or ERROR. Used for filtering/routing.
        tags: Optional key-value metadata for custom routing/filtering.
    """

    event_type: str
    session_id: str
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    severity: str = SEVERITY_INFO
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc
            ).isoformat(),
            "severity": self.severity,
            "tags": self.tags,
            "data": self.data,
        }

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), default=str)

    @property
    def attributes(self) -> dict[str, str]:
        """Message attributes for Pub/Sub filtering or similar systems.

        Returns a flat dict of string key-value pairs suitable for
        Pub/Sub message attributes, Kafka headers, etc.
        """
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "severity": self.severity,
            **self.tags,
        }
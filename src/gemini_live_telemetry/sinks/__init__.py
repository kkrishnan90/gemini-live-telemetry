"""Pluggable event sinks for gemini-live-telemetry.

Sinks consume TelemetryEvent objects and deliver them to external systems
(Pub/Sub, logging, webhooks, custom callbacks, etc.).

Usage:
    from gemini_live_telemetry.sinks import EventSink, LoggingSink

    class MySink:
        async def publish(self, event: TelemetryEvent) -> None:
            print(event.to_json())

        async def publish_batch(self, events: list[TelemetryEvent]) -> None:
            for event in events:
                await self.publish(event)

        async def flush(self) -> None:
            pass

        async def close(self) -> None:
            pass
"""

from __future__ import annotations

from ._protocol import EventSink
from ._pubsub import PubSubSink

__all__ = [
    "EventSink",
    "PubSubSink",
]

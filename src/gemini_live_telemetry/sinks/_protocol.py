"""EventSink protocol — the interface any telemetry sink must implement.

This is a structural protocol (PEP 544): any class with the right methods
satisfies it, no explicit inheritance required. Use @runtime_checkable
so ``isinstance(obj, EventSink)`` works at runtime.

Sinks MUST NOT raise from publish() — errors should be logged internally
and the event dropped. The EventBus isolates sinks from each other, but
a sink should still handle its own errors gracefully.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .._event_types import TelemetryEvent


@runtime_checkable
class EventSink(Protocol):
    """Interface for any telemetry event consumer.

    Implement this protocol to deliver TelemetryEvents to an external
    system (Pub/Sub, Kafka, logging, webhooks, BigQuery, etc.).

    Methods:
        publish: Deliver a single event. Must not raise.
        publish_batch: Deliver multiple events (default: calls publish per event).
        flush: Flush any internal buffers. Called periodically + on shutdown.
        close: Release resources (connections, file handles, etc.).
    """

    async def publish(self, event: TelemetryEvent) -> None:
        """Deliver a single event to the sink.

        This method MUST NOT raise exceptions. Errors should be
        logged internally and the event dropped/retried silently.

        Args:
            event: The telemetry event to deliver.
        """
        ...

    async def publish_batch(self, events: list[TelemetryEvent]) -> None:
        """Deliver multiple events to the sink.

        Default implementation calls publish() for each event.
        Override for sinks that support batch operations (e.g., Pub/Sub batch,
        BigQuery streaming insert).

        Args:
            events: List of telemetry events to deliver.
        """
        ...

    async def flush(self) -> None:
        """Flush any internally buffered events.

        Called periodically by the EventBus (configurable interval)
        and on application shutdown. Implementations should ensure
        all buffered data is delivered to the external system.
        """
        ...

    async def close(self) -> None:
        """Release resources held by the sink.

        Called once during shutdown, after flush(). Close connections,
        file handles, publisher clients, etc.
        """
        ...
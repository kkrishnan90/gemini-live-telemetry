"""EventBus — dispatches TelemetryEvents to all registered EventSinks.

The EventBus sits between the instrumentation wrappers and the sinks.
It provides:

1. Non-blocking emit() — wrappers call this from sync/async context.
2. Background async dispatch — a task reads the queue and fans out to sinks.
3. Error isolation — one sink failing doesn't affect others.
4. Overflow protection — queue has a max size; events are dropped (not blocked).
5. Graceful shutdown — drains remaining events, flushes all sinks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._event_types import TelemetryEvent
    from .sinks._protocol import EventSink

logger = logging.getLogger(__name__)

# Default max queue depth — prevents unbounded memory growth.
# Events are dropped (with a warning) when the queue is full.
DEFAULT_MAX_QUEUE_SIZE = 10_000


class EventBus:
    """Fan-out dispatcher for telemetry events.

    Usage::

        bus = EventBus()
        bus.register(PubSubSink(topic="my-topic"))
        bus.register(LoggingSink())
        bus.start()  # starts background dispatch task

        # From wrappers (sync or async):
        bus.emit(TelemetryEvent(event_type="turn_complete", ...))

        # On shutdown:
        await bus.shutdown()
    """

    def __init__(self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE) -> None:
        self._sinks: list[EventSink] = []
        self._queue: asyncio.Queue[TelemetryEvent] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._task: asyncio.Task | None = None
        self._started = False
        self._shutting_down = False

    @property
    def sink_count(self) -> int:
        """Number of registered sinks."""
        return len(self._sinks)

    @property
    def queue_size(self) -> int:
        """Current number of events waiting to be dispatched."""
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        """Whether the background dispatch loop is running."""
        return self._started and self._task is not None and not self._task.done()

    def register(self, sink: EventSink) -> None:
        """Register an EventSink to receive events.

        Call this before start(). Sinks added after start() will still
        receive events, but there's a small window where events dispatched
        between register() and the next queue read might miss the new sink.

        Args:
            sink: Any object implementing the EventSink protocol.
        """
        self._sinks.append(sink)
        logger.debug(
            "Registered sink: %s (total: %d)",
            sink.__class__.__name__,
            len(self._sinks),
        )

    def emit(self, event: TelemetryEvent) -> None:
        """Emit an event to all registered sinks (non-blocking).

        Safe to call from both sync and async contexts. The event
        is placed on an internal queue and dispatched asynchronously
        by the background task.

        If the queue is full, the event is dropped with a warning.
        This ensures the instrumentation NEVER blocks the SDK.

        Args:
            event: The telemetry event to dispatch.
        """
        if self._shutting_down:
            return

        if not self._sinks:
            return  # No sinks registered — skip

        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "EventBus queue full (%d) — dropping %s event for session %s",
                self._queue.maxsize,
                event.event_type,
                event.session_id,
            )

    def start(self) -> None:
        """Start the background dispatch loop.

        Must be called from within a running asyncio event loop.
        Typically called from activate().
        """
        if self._started:
            logger.warning("EventBus already started")
            return

        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(
                self._dispatch_loop(), name="event-bus-dispatch"
            )
            self._started = True
            logger.info(
                "EventBus started with %d sink(s)", len(self._sinks)
            )
        except RuntimeError:
            # No running event loop — defer start to first emit
            logger.info(
                "No running event loop. EventBus will start on first async context."
            )

    async def shutdown(self) -> None:
        """Drain remaining events, flush and close all sinks.

        Call this on application shutdown (e.g., from atexit or
        FastAPI shutdown event).
        """
        self._shutting_down = True

        # Drain remaining queued events
        drained = 0
        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                await self._dispatch_to_sinks(event)
                drained += 1
            except asyncio.QueueEmpty:
                break

        if drained:
            logger.debug("Drained %d events from queue", drained)

        # Flush + close all sinks
        for sink in self._sinks:
            try:
                await sink.flush()
            except Exception:
                logger.exception("Error flushing sink %s", sink.__class__.__name__)
            try:
                await sink.close()
            except Exception:
                logger.exception("Error closing sink %s", sink.__class__.__name__)

        # Cancel background task
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._started = False
        logger.info("EventBus shut down (%d sinks closed)", len(self._sinks))

    # ── Internal ─────────────────────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        """Background task: read events from queue, fan out to sinks."""
        logger.debug("EventBus dispatch loop started")
        try:
            while True:
                event = await self._queue.get()
                await self._dispatch_to_sinks(event)
                self._queue.task_done()
        except asyncio.CancelledError:
            logger.debug("EventBus dispatch loop cancelled")
        except Exception:
            logger.exception("EventBus dispatch loop crashed")

    async def _dispatch_to_sinks(self, event: TelemetryEvent) -> None:
        """Send one event to all registered sinks, isolating errors."""
        for sink in self._sinks:
            try:
                await sink.publish(event)
            except Exception:
                logger.exception(
                    "Sink %s failed on %s event (session %s)",
                    sink.__class__.__name__,
                    event.event_type,
                    event.session_id,
                )
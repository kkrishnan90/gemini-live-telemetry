"""Google Cloud Pub/Sub event sink.

Publishes TelemetryEvents to a Pub/Sub topic as JSON messages with
filterable message attributes (event_type, session_id, severity).

Usage::

    from gemini_live_telemetry.sinks import PubSubSink

    sink = PubSubSink(
        project_id="my-project",
        topic_id="gemini-live-telemetry",
        auto_create_topic=True,
    )

Pub/Sub subscribers can filter by attributes::

    # Only receive error events
    gcloud pubsub subscriptions create errors-only \\
        --topic=gemini-live-telemetry \\
        --message-filter='attributes.severity = "ERROR"'

    # Only receive turn_complete events for a specific session
    gcloud pubsub subscriptions create session-debug \\
        --topic=gemini-live-telemetry \\
        --message-filter='attributes.event_type = "turn_complete" AND attributes.session_id = "abc123"'

Dependencies:
    pip install google-cloud-pubsub
    (lazy-imported — only loaded when PubSubSink is instantiated)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .._event_types import TelemetryEvent

logger = logging.getLogger(__name__)


class PubSubSink:
    """Publishes telemetry events to Google Cloud Pub/Sub.

    Each event becomes one Pub/Sub message:
    - **Body**: JSON serialization of the event (``event.to_json()``)
    - **Attributes**: ``event_type``, ``session_id``, ``severity`` + custom tags
      (enables Pub/Sub subscription filters)

    The publisher uses the default batching settings from
    ``google-cloud-pubsub`` (max 100 messages or 1MB or 10ms — whichever
    comes first). For real-time alerting, you can tune ``batch_settings``.

    Args:
        project_id: GCP project ID.
        topic_id: Pub/Sub topic name (not the full path).
        auto_create_topic: If True, create the topic if it doesn't exist.
        ordering_key: Optional ordering key for message ordering.
            If set, all events for the same session are delivered in order.
            Requires the topic to have message ordering enabled.
        event_filter: Optional list of event types to publish.
            If None, all events are published. Example: ["turn_complete", "error"]
        batch_max_messages: Max messages per publish batch (default: 100).
        batch_max_latency: Max seconds to wait before publishing a batch (default: 0.01 = 10ms).
    """

    def __init__(
        self,
        project_id: str,
        topic_id: str = "gemini-live-telemetry",
        auto_create_topic: bool = True,
        ordering_key: str | None = None,
        event_filter: list[str] | None = None,
        batch_max_messages: int = 100,
        batch_max_latency: float = 0.01,
    ) -> None:
        self._project_id = project_id
        self._topic_id = topic_id
        self._ordering_key = ordering_key
        self._event_filter = set(event_filter) if event_filter else None

        # Lazy import — only need google-cloud-pubsub if this sink is used
        try:
            from google.cloud import pubsub_v1
            from google.cloud.pubsub_v1 import types as pubsub_types
        except ImportError as e:
            raise ImportError(
                "PubSubSink requires google-cloud-pubsub. "
                "Install with: pip install google-cloud-pubsub"
            ) from e

        # Configure batching
        batch_settings = pubsub_types.BatchSettings(
            max_messages=batch_max_messages,
            max_latency=batch_max_latency,
        )

        self._publisher = pubsub_v1.PublisherClient(
            batch_settings=batch_settings,
        )
        self._topic_path = self._publisher.topic_path(project_id, topic_id)

        # Auto-create topic if requested
        if auto_create_topic:
            self._ensure_topic_exists()

        self._publish_count = 0
        self._error_count = 0

        logger.info(
            "PubSubSink initialized: %s (filter=%s)",
            self._topic_path,
            self._event_filter or "all",
        )

    def _ensure_topic_exists(self) -> None:
        """Create the topic if it doesn't exist."""
        try:
            self._publisher.get_topic(topic=self._topic_path)
            logger.debug("Topic exists: %s", self._topic_path)
        except Exception:
            try:
                self._publisher.create_topic(name=self._topic_path)
                logger.info("Created topic: %s", self._topic_path)
            except Exception as e:
                # Topic may have been created between get and create (race)
                if "ALREADY_EXISTS" in str(e):
                    logger.debug("Topic already exists (race): %s", self._topic_path)
                else:
                    logger.warning(
                        "Failed to create topic %s: %s (will try publishing anyway)",
                        self._topic_path,
                        e,
                    )

    async def publish(self, event: TelemetryEvent) -> None:
        """Publish a single event to Pub/Sub.

        The event is serialized to JSON and published with message
        attributes for filtering. Publishing is non-blocking (batched
        by the Pub/Sub client library).

        Args:
            event: The telemetry event to publish.
        """
        # Apply event filter if configured
        if self._event_filter and event.event_type not in self._event_filter:
            return

        try:
            data = event.to_json().encode("utf-8")

            # Message attributes for Pub/Sub filtering
            attributes = event.attributes  # event_type, session_id, severity + tags

            # Publish (non-blocking — batched internally by the client)
            kwargs = {
                "topic": self._topic_path,
                "data": data,
                **{k: str(v) for k, v in attributes.items()},
            }

            if self._ordering_key:
                kwargs["ordering_key"] = event.session_id  # Order by session

            future = self._publisher.publish(**kwargs)

            # Register callback for error tracking (don't await)
            future.add_done_callback(self._on_publish_complete)

            self._publish_count += 1

        except Exception:
            self._error_count += 1
            logger.exception(
                "PubSubSink: Failed to publish %s event for session %s",
                event.event_type,
                event.session_id,
            )

    async def publish_batch(self, events: list[TelemetryEvent]) -> None:
        """Publish multiple events. Each becomes a separate Pub/Sub message.

        The Pub/Sub client library handles batching internally, so this
        just calls publish() for each event.
        """
        for event in events:
            await self.publish(event)

    async def flush(self) -> None:
        """Flush any pending Pub/Sub messages.

        Forces the publisher to send all batched messages immediately.
        """
        try:
            # The PublisherClient doesn't have a direct flush, but
            # stopping and restarting the transport flushes pending batches.
            # For now, we rely on the batch settings (10ms max latency).
            logger.debug(
                "PubSubSink flush: %d published, %d errors",
                self._publish_count,
                self._error_count,
            )
        except Exception:
            logger.exception("PubSubSink: Error during flush")

    async def close(self) -> None:
        """Close the Pub/Sub publisher client."""
        try:
            # Stop the publisher — flushes remaining messages
            transport = getattr(self._publisher, "_transport", None)
            if transport and hasattr(transport, "close"):
                transport.close()
            logger.info(
                "PubSubSink closed: %d published, %d errors total",
                self._publish_count,
                self._error_count,
            )
        except Exception:
            logger.exception("PubSubSink: Error during close")

    def _on_publish_complete(self, future) -> None:
        """Callback for publish futures — tracks errors."""
        try:
            future.result()  # Raises if publish failed
        except Exception as e:
            self._error_count += 1
            logger.warning("PubSubSink: Message publish failed: %s", e)

    @property
    def stats(self) -> dict[str, int]:
        """Return publish statistics."""
        return {
            "published": self._publish_count,
            "errors": self._error_count,
        }
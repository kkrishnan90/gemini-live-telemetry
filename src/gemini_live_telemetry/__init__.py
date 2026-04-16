"""Gemini Live API Instrumentation Package.

Non-intrusive instrumentation for the google-genai SDK's Live API using wrapt.
Collects 65 metrics via OpenTelemetry, exports to Google Cloud Monitoring
(with auto-created dashboard), and writes local JSON backup files.

Usage:
    from gemini_live_telemetry import activate
    from gemini_live_telemetry.config import InstrumentationConfig

    activate(InstrumentationConfig(
        project_id="your-gcp-project-id",
        enable_dashboard=True,
    ))

    # All google-genai Live API calls are now instrumented automatically.

Query metrics:
    from gemini_live_telemetry import get_metrics_store

    store = get_metrics_store()
    session = store.get_session("session-id-from-gemini")
    print(session.aggregates.avg_ttfb_ms)

Application-side logging (for accuracy comparison):
    from gemini_live_telemetry import get_app_logger

    logger = get_app_logger()
    logger.log_ttfb(session_id="abc", ttfb_ms=312.5, vad_mode="native")
"""

from __future__ import annotations

__version__ = "0.1.1"

# Type-only import to avoid circular deps; actual classes resolved at runtime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app_logger import AppMetricsLogger
    from .config import InstrumentationConfig
    from .store import MetricsStore

# Module-level state
_activated: bool = False
_config: InstrumentationConfig | None = None
_store: MetricsStore | None = None
_app_logger: AppMetricsLogger | None = None
_json_exp: object | None = None  # JsonFileExporter instance (avoids module name collision)
_event_bus: object | None = None  # EventBus instance (set by activate())


def activate(config: InstrumentationConfig | None = None) -> None:
    """Activate instrumentation on the google-genai SDK.

    This patches the SDK's AsyncSession and AsyncLive classes via wrapt
    to intercept all send/receive calls and collect metrics. Call this
    ONCE at server startup, before any Gemini Live sessions are created.

    Args:
        config: Instrumentation configuration. If None, uses defaults
            (which reads project_id from environment variables).

    Raises:
        ActivationError: If the google-genai SDK is not installed or
            the instrumentation cannot be applied.
        ConfigurationError: If the config is invalid.
    """
    global _activated, _config, _store, _app_logger, _json_exp, _event_bus

    if _activated:
        import logging

        logging.getLogger(__name__).warning(
            "Instrumentation already activated. Skipping re-activation."
        )
        return

    from .config import InstrumentationConfig as _ConfigClass
    from .exceptions import ActivationError

    if config is None:
        config = _ConfigClass()
    _config = config

    # Verify google-genai SDK is importable
    try:
        import google.genai.live  # noqa: F401
    except ImportError as e:
        raise ActivationError(
            "google-genai SDK not found. Install it with: pip install google-genai"
        ) from e

    # Initialize the in-memory metrics store
    from .store import MetricsStore

    _store = MetricsStore(config=config)

    # Initialize the application-side metrics logger
    from .app_logger import AppMetricsLogger

    _app_logger = AppMetricsLogger(config=config)

    # Set up OpenTelemetry (MeterProvider, exporters)
    from .otel import setup_otel

    setup_otel(config=config)

    # Apply wrapt patches to the SDK
    from .instrumentation import apply_patches

    apply_patches(store=_store)

    # Start JSON file exporter (periodic flush + atexit)
    if config.enable_json_export:
        from ._json_exporter import JsonFileExporter as _JFE

        _json_exp = _JFE(store=_store, config=config)
        # start() creates the background task — needs a running loop
        # If no loop, start() logs a warning; atexit flush still works
        try:
            _json_exp.start()
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "JSON exporter periodic flush not started. "
                "atexit flush will still write the file on shutdown."
            )

    # ── Event Sinks (Pub/Sub, custom sinks) ──────────────────────────────
    import logging as _logging  # ensure logging is available for this section

    from ._event_bus import EventBus

    _event_bus = EventBus(max_queue_size=10_000)

    # Auto-create PubSubSink if enabled
    if config.enable_pubsub_export and config.project_id:
        try:
            from .sinks import PubSubSink

            _ps = PubSubSink(
                project_id=config.project_id,
                topic_id=config.pubsub_topic,
                auto_create_topic=config.pubsub_auto_create_topic,
                event_filter=config.pubsub_event_filter,
            )
            _event_bus.register(_ps)
            _logging.getLogger(__name__).info(
                f"PubSubSink registered → {_ps._topic_path}"
            )
        except Exception:
            _logging.getLogger(__name__).warning(
                "PubSubSink creation failed (non-fatal)", exc_info=True
            )

    # Register any custom sinks from config
    for sink in config.event_sinks:
        _event_bus.register(sink)

    # Start the dispatch loop (requires a running asyncio event loop)
    _event_bus.start()

    # Register shutdown hook for OTel + JSON + EventBus
    import atexit as _atexit

    _atexit.register(_shutdown_hook)

    # Auto-create Cloud Monitoring dashboard (async, non-blocking)
    if config.enable_dashboard and config.project_id:
        import asyncio
        import logging

        logger = logging.getLogger(__name__)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_create_dashboard_async(config))
        except RuntimeError:
            # No running loop — dashboard will be created on first session
            logger.info(
                "No running event loop. Dashboard creation deferred."
            )

    _activated = True

    import logging

    logging.getLogger(__name__).info(
        "Gemini Live instrumentation activated. "
        f"GCP export: {config.enable_gcp_export}, "
        f"JSON export: {config.enable_json_export}, "
        f"Dashboard: {config.enable_dashboard}, "
        f"Project: {config.project_id or '(from ADC)'}"
    )


async def _create_dashboard_async(config: InstrumentationConfig) -> None:
    """Create the Cloud Monitoring dashboard in a background task."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        from ._dashboard import create_or_update_dashboard

        await asyncio.to_thread(create_or_update_dashboard, config)
        logger.info("Cloud Monitoring dashboard created/updated.")
    except Exception as e:
        logger.warning(f"Dashboard creation failed (non-fatal): {e}")


def get_metrics_store() -> MetricsStore:
    """Get the in-memory metrics store for querying session metrics.

    Returns:
        The MetricsStore singleton.

    Raises:
        RuntimeError: If activate() has not been called.
    """
    if _store is None:
        raise RuntimeError(
            "Instrumentation not activated. Call activate() first."
        )
    return _store


def get_app_logger() -> AppMetricsLogger:
    """Get the application-side metrics logger.

    Returns:
        The AppMetricsLogger singleton.

    Raises:
        RuntimeError: If activate() has not been called.
    """
    if _app_logger is None:
        raise RuntimeError(
            "Instrumentation not activated. Call activate() first."
        )
    return _app_logger


def is_activated() -> bool:
    """Check if instrumentation has been activated."""
    return _activated


def get_config() -> InstrumentationConfig | None:
    """Get the current instrumentation configuration, or None if not activated."""
    return _config


def get_json_exporter():
    """Get the JSON file exporter, or None if not active."""
    return _json_exp


def get_event_bus():
    """Get the EventBus instance, or None if not activated."""
    return _event_bus


def _shutdown_hook() -> None:
    """atexit hook — flush OTel, JSON, and EventBus on shutdown."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        from .otel import shutdown_otel

        shutdown_otel()
    except Exception:
        pass
    # JSON exporter has its own atexit, but we flush here too for ordering
    if _json_exp is not None:
        try:
            _json_exp.flush_now()
        except Exception:
            pass
    # Drain EventBus queue and close all sinks
    if _event_bus is not None:
        try:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_event_bus.shutdown())
            except RuntimeError:
                # No running loop — run synchronously
                asyncio.run(_event_bus.shutdown())
        except Exception:
            logger.debug("EventBus shutdown error (non-fatal)", exc_info=True)
    logger.info("Instrumentation shutdown complete.")


# Re-export for convenience
import asyncio  # noqa: E402 — needed by _create_dashboard_async

from .config import InstrumentationConfig  # noqa: E402, F811
from .exceptions import (  # noqa: E402
    ActivationError,
    ConfigurationError,
    DashboardError,
    ExporterError,
    InstrumentationError,
    SessionNotFoundError,
    StoreError,
)

__all__ = [
    # Core API
    "activate",
    "get_metrics_store",
    "get_app_logger",
    "get_event_bus",
    "is_activated",
    "get_config",
    # Config
    "InstrumentationConfig",
    # Exceptions
    "InstrumentationError",
    "ConfigurationError",
    "ActivationError",
    "ExporterError",
    "DashboardError",
    "SessionNotFoundError",
    "StoreError",
]

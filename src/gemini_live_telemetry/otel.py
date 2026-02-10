"""OpenTelemetry setup — MeterProvider, exporters, instruments.

Configures OTel with up to two exporters:
    1. Google Cloud Monitoring (if enable_gcp_export=True)
    2. Console/logging exporter for debugging (always enabled at DEBUG level)

The JSON file export is handled separately by the in-memory store's
periodic flush (not via OTel MetricExporter).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

from . import _instruments

if TYPE_CHECKING:
    from .config import InstrumentationConfig

logger = logging.getLogger(__name__)

_meter_provider: MeterProvider | None = None

# Meter name used for all instruments
METER_NAME = "gemini-live-instrumentation"
METER_VERSION = "0.1.0"


def setup_otel(config: InstrumentationConfig) -> None:
    """Set up OpenTelemetry MeterProvider and create all instruments.

    Args:
        config: Instrumentation configuration.
    """
    global _meter_provider

    if _meter_provider is not None:
        logger.warning("OTel already initialized. Skipping.")
        return

    metric_readers = []

    # GCP Cloud Monitoring exporter
    if config.enable_gcp_export:
        try:
            from opentelemetry.exporter.cloud_monitoring import (
                CloudMonitoringMetricsExporter,
            )

            gcp_exporter = CloudMonitoringMetricsExporter(
                project_id=config.project_id or None,
                prefix=config.metric_prefix,
                add_unique_identifier=config.add_unique_identifier,
            )
            gcp_reader = PeriodicExportingMetricReader(
                gcp_exporter,
                export_interval_millis=int(config.export_interval_s * 1000),
            )
            metric_readers.append(gcp_reader)
            logger.info(
                f"GCP Cloud Monitoring exporter configured "
                f"(project={config.project_id}, prefix={config.metric_prefix}, "
                f"interval={config.export_interval_s}s)"
            )
        except ImportError:
            logger.warning(
                "opentelemetry-exporter-gcp-monitoring not installed. "
                "GCP export disabled."
            )
        except Exception as e:
            logger.warning(f"GCP exporter setup failed: {e}. GCP export disabled.")

    # If no exporters configured, add a no-op reader so instruments still work
    if not metric_readers:
        # ConsoleMetricExporter at WARNING level effectively does nothing
        # unless the user has DEBUG logging — then it logs metric data
        noop_reader = PeriodicExportingMetricReader(
            ConsoleMetricExporter(),
            export_interval_millis=60_000,  # 1 minute — minimal overhead
        )
        metric_readers.append(noop_reader)
        logger.info("No exporters configured. Using console fallback.")

    # Create resource with service metadata
    resource = Resource.create({
        "service.name": config.service_name,
        "service.instance.id": config.service_instance_id,
        "service.version": METER_VERSION,
    })

    # Create and set the MeterProvider
    _meter_provider = MeterProvider(
        metric_readers=metric_readers,
        resource=resource,
    )
    metrics.set_meter_provider(_meter_provider)

    # Create the meter and all instruments
    meter = metrics.get_meter(METER_NAME, METER_VERSION)
    _instruments.create_instruments(meter)

    logger.info(
        f"OTel MeterProvider initialized with {len(metric_readers)} reader(s). "
        f"21 instruments created on meter '{METER_NAME}'."
    )


def shutdown_otel() -> None:
    """Shutdown the MeterProvider (flush pending metrics)."""
    global _meter_provider
    if _meter_provider is not None:
        try:
            _meter_provider.shutdown()
            logger.info("OTel MeterProvider shut down.")
        except Exception as e:
            logger.warning(f"Error shutting down MeterProvider: {e}")
        _meter_provider = None


def get_meter_provider() -> MeterProvider | None:
    """Get the current MeterProvider (for testing)."""
    return _meter_provider

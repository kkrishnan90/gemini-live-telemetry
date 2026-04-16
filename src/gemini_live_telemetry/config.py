"""Centralized configuration for Gemini Live API instrumentation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# Default metric prefix — workload.googleapis.com has 25k descriptor quota
# vs 10k for custom.googleapis.com
DEFAULT_METRIC_PREFIX = "workload.googleapis.com"

# OTel export interval must be >= 10s (GCP Cloud Monitoring minimum write interval)
MIN_EXPORT_INTERVAL_S = 10.0
DEFAULT_EXPORT_INTERVAL_S = 15.0
DEFAULT_JSON_FLUSH_INTERVAL_S = 30.0

# Audio defaults for duration calculation
DEFAULT_INPUT_SAMPLE_RATE = 16000
DEFAULT_OUTPUT_SAMPLE_RATE = 24000
DEFAULT_BITS_PER_SAMPLE = 16
DEFAULT_CHANNELS = 1


@dataclass
class InstrumentationConfig:
    """Configuration for the Gemini Live instrumentation package.

    Args:
        project_id: GCP project ID for Cloud Monitoring export.
            Falls back to GOOGLE_CLOUD_PROJECT or GCLOUD_PROJECT env vars.
        metrics_dir: Directory for local JSON metric files.
        log_dir: Directory for application-side JSONL metric logs.
        export_interval_s: How often OTel exports to GCP (seconds, min 10).
        json_flush_interval_s: How often in-memory metrics flush to JSON file.
        enable_gcp_export: Export metrics to Google Cloud Monitoring.
        enable_json_export: Write metrics to local JSON file.
        enable_dashboard: Auto-create Cloud Monitoring dashboard on activate().
        dashboard_name: Display name for the auto-created dashboard.
        metric_prefix: Domain prefix for GCP custom metrics.
        add_unique_identifier: Append random ID to avoid time series collisions
            when multiple exporters write to the same metric.
        input_sample_rate: Audio input sample rate in Hz (for duration calc).
        output_sample_rate: Audio output sample rate in Hz (for duration calc).
        bits_per_sample: Audio bits per sample (for duration calc).
        channels: Audio channel count (for duration calc).
        service_name: OTel resource service.name attribute.
        service_instance_id: OTel resource service.instance.id attribute.
    """

    project_id: str = field(default_factory=lambda: _resolve_project_id())
    metrics_dir: str = "./metrics"
    log_dir: str = "./metrics/logs"
    export_interval_s: float = DEFAULT_EXPORT_INTERVAL_S
    json_flush_interval_s: float = DEFAULT_JSON_FLUSH_INTERVAL_S
    enable_gcp_export: bool = True
    enable_json_export: bool = True
    enable_dashboard: bool = True
    dashboard_name: str = "Gemini Live API Metrics"
    metric_prefix: str = DEFAULT_METRIC_PREFIX
    add_unique_identifier: bool = False
    input_sample_rate: int = DEFAULT_INPUT_SAMPLE_RATE
    output_sample_rate: int = DEFAULT_OUTPUT_SAMPLE_RATE
    bits_per_sample: int = DEFAULT_BITS_PER_SAMPLE
    channels: int = DEFAULT_CHANNELS
    service_name: str = "gemini-live-api"
    service_instance_id: str = field(default_factory=lambda: _generate_instance_id())

    # ── Event Sinks (Pub/Sub, logging, callbacks) ────────────────────────
    enable_pubsub_export: bool = False
    pubsub_topic: str = "gemini-live-telemetry"
    pubsub_auto_create_topic: bool = True
    pubsub_event_filter: list[str] | None = None  # None = all events

    # Custom sinks — pass any EventSink instances
    event_sinks: list = field(default_factory=list)  # list[EventSink]

    def __post_init__(self) -> None:
        if self.export_interval_s < MIN_EXPORT_INTERVAL_S:
            raise ValueError(
                f"export_interval_s must be >= {MIN_EXPORT_INTERVAL_S}s "
                f"(GCP Cloud Monitoring minimum write interval). "
                f"Got: {self.export_interval_s}"
            )
        if self.json_flush_interval_s <= 0:
            raise ValueError(
                f"json_flush_interval_s must be > 0. Got: {self.json_flush_interval_s}"
            )
        # Ensure directories exist
        Path(self.metrics_dir).mkdir(parents=True, exist_ok=True)
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)

    def bytes_to_duration_ms(self, byte_count: int, *, is_input: bool = True) -> float:
        """Convert audio byte count to duration in milliseconds."""
        sample_rate = self.input_sample_rate if is_input else self.output_sample_rate
        bytes_per_sample = self.bits_per_sample // 8
        bytes_per_second = sample_rate * bytes_per_sample * self.channels
        if bytes_per_second == 0:
            return 0.0
        return (byte_count / bytes_per_second) * 1000.0


def _resolve_project_id() -> str:
    """Resolve GCP project ID from environment variables."""
    for var in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT"):
        value = os.environ.get(var)
        if value:
            return value
    return ""


def _generate_instance_id() -> str:
    """Generate a unique instance ID for this process."""
    import uuid

    return uuid.uuid4().hex[:12]

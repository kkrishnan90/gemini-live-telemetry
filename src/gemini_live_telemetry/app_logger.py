"""Application-side metrics logger for accuracy comparison.

Writes structured JSONL (one JSON object per line) to a log file.
One file per server lifecycle. Used to compare against OTel-collected
metrics to validate instrumentation accuracy.

Each log line: {"timestamp", "session_id", "metric", "value", "attributes"}

Usage:
    from gemini_live_telemetry import get_app_logger
    logger = get_app_logger()
    logger.log_ttfb("session-123", 312.5, vad_mode="native")
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import InstrumentationConfig

logger = logging.getLogger(__name__)

# Flush buffer when it exceeds this many entries
_BUFFER_FLUSH_THRESHOLD = 50
# Flush buffer every N seconds regardless
_BUFFER_FLUSH_INTERVAL_S = 5.0


class AppMetricsLogger:
    """Logs metrics from the application side for accuracy comparison.

    Thread-safe. All log methods are fire-and-forget (never raise).
    Writes JSONL to a timestamped file, one per server lifecycle.
    """

    def __init__(self, config: InstrumentationConfig) -> None:
        self._config = config
        self._log_dir = Path(config.log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename with server start timestamp
        start_ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
        self._file_path = self._log_dir / f"app_metrics_{start_ts}.jsonl"

        self._buffer: list[str] = []
        self._lock = threading.Lock()
        self._last_flush_time = time.time()

        # Register atexit for final flush
        atexit.register(self._shutdown_flush)

        logger.info(f"AppMetricsLogger initialized → {self._file_path}")

    @property
    def file_path(self) -> Path:
        """Path to the current JSONL log file."""
        return self._file_path

    # --- Core logging ---

    def _log(self, session_id: str, metric: str, value: Any,
             attributes: dict[str, Any] | None = None) -> None:
        """Write a single metric record to the buffer."""
        try:
            record = {
                "timestamp": datetime.utcnow().isoformat(),
                "session_id": session_id,
                "metric": metric,
                "value": value,
            }
            if attributes:
                record["attributes"] = attributes

            line = json.dumps(record, default=str)

            with self._lock:
                self._buffer.append(line)
                should_flush = (
                    len(self._buffer) >= _BUFFER_FLUSH_THRESHOLD
                    or (time.time() - self._last_flush_time) >= _BUFFER_FLUSH_INTERVAL_S
                )

            if should_flush:
                self._flush()
        except Exception:
            pass  # Fire-and-forget — never break application

    def _flush(self) -> None:
        """Write buffered lines to the JSONL file."""
        with self._lock:
            if not self._buffer:
                return
            lines = self._buffer.copy()
            self._buffer.clear()
            self._last_flush_time = time.time()

        try:
            with open(self._file_path, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
        except Exception:
            logger.exception("Failed to flush app metrics log")

    def flush_now(self) -> None:
        """Trigger an immediate flush."""
        self._flush()

    def _shutdown_flush(self) -> None:
        """atexit handler."""
        try:
            self._flush()
        except Exception:
            pass

    # --- Metric-specific log methods ---

    def log_ttfb(self, session_id: str, ttfb_ms: float,
                 vad_mode: str = "native") -> None:
        """Log time-to-first-byte measurement."""
        self._log(session_id, "ttfb_ms", ttfb_ms,
                  {"vad_mode": vad_mode})

    def log_turn_complete(
        self, session_id: str, turn_number: int,
        duration_ms: float | None = None,
        was_interrupted: bool = False,
        reason: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Log turn completion with full metadata."""
        attrs: dict[str, Any] = {
            "turn_number": turn_number,
            "was_interrupted": was_interrupted,
        }
        if duration_ms is not None:
            attrs["duration_ms"] = duration_ms
        if reason is not None:
            attrs["turn_complete_reason"] = reason
        if usage is not None:
            attrs["usage"] = usage
        self._log(session_id, "turn_complete", 1, attrs)

    def log_tool_call(self, session_id: str, tool_id: str,
                      tool_name: str, args: dict | None = None) -> None:
        """Log tool call received from model."""
        self._log(session_id, "tool_call", 1,
                  {"tool_id": tool_id, "tool_name": tool_name, "args": args})

    def log_tool_response(self, session_id: str, tool_id: str,
                          tool_name: str, round_trip_ms: float) -> None:
        """Log tool response sent back to model."""
        self._log(session_id, "tool_response", round_trip_ms,
                  {"tool_id": tool_id, "tool_name": tool_name,
                   "round_trip_ms": round_trip_ms})

    def log_tool_cancellation(self, session_id: str,
                              tool_ids: list[str]) -> None:
        """Log tool call cancellations."""
        self._log(session_id, "tool_cancellation", len(tool_ids),
                  {"tool_ids": tool_ids})

    def log_tokens(
        self, session_id: str,
        prompt: int, response: int, total: int,
        cached: int = 0, tool_use: int = 0, thoughts: int = 0,
        prompt_details: list[dict] | None = None,
        response_details: list[dict] | None = None,
    ) -> None:
        """Log token usage for a turn."""
        attrs: dict[str, Any] = {
            "prompt_token_count": prompt,
            "response_token_count": response,
            "total_token_count": total,
        }
        if cached:
            attrs["cached_content_token_count"] = cached
        if tool_use:
            attrs["tool_use_prompt_token_count"] = tool_use
        if thoughts:
            attrs["thoughts_token_count"] = thoughts
        if prompt_details:
            attrs["prompt_tokens_details"] = prompt_details
        if response_details:
            attrs["response_tokens_details"] = response_details
        self._log(session_id, "tokens", total, attrs)

    def log_audio_sent(self, session_id: str, bytes_count: int) -> None:
        """Log audio bytes sent to model."""
        self._log(session_id, "audio_sent", bytes_count)

    def log_audio_received(self, session_id: str, bytes_count: int) -> None:
        """Log audio bytes received from model."""
        self._log(session_id, "audio_received", bytes_count)

    def log_vad_event(self, session_id: str, event_type: str,
                      source: str = "native") -> None:
        """Log voice activity detection event."""
        self._log(session_id, "vad_event", event_type,
                  {"source": source})

    def log_grounding(
        self, session_id: str,
        chunk_count: int = 0,
        confidence_scores: list[float] | None = None,
        retrieval_score: float | None = None,
        search_queries: list[str] | None = None,
    ) -> None:
        """Log grounding/search metadata."""
        attrs: dict[str, Any] = {"chunk_count": chunk_count}
        if confidence_scores:
            attrs["confidence_scores"] = confidence_scores
        if retrieval_score is not None:
            attrs["retrieval_score"] = retrieval_score
        if search_queries:
            attrs["search_queries"] = search_queries
        self._log(session_id, "grounding", 1, attrs)

    def log_session_start(self, session_id: str,
                          model: str | None = None) -> None:
        """Log session start."""
        attrs = {}
        if model:
            attrs["model"] = model
        self._log(session_id, "session_start", 1, attrs or None)

    def log_session_end(self, session_id: str,
                        duration_ms: float | None = None) -> None:
        """Log session end."""
        attrs = {}
        if duration_ms is not None:
            attrs["duration_ms"] = duration_ms
        self._log(session_id, "session_end", 1, attrs or None)

    def log_setup_latency(self, session_id: str,
                          latency_ms: float) -> None:
        """Log session setup latency."""
        self._log(session_id, "setup_latency_ms", latency_ms)

    def log_message_sent(self, session_id: str,
                         method: str) -> None:
        """Log a message sent to model."""
        self._log(session_id, "message_sent", 1, {"method": method})

    def log_message_received(self, session_id: str) -> None:
        """Log a message received from model."""
        self._log(session_id, "message_received", 1)

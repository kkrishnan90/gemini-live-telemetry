"""Periodic JSON file exporter for the in-memory MetricsStore.

Writes the full store state to a JSON file at configurable intervals.
One file per server lifecycle (named with startup timestamp).
Includes atexit hook for final flush on shutdown.

This is NOT an OTel MetricExporter — it reads directly from the
MetricsStore to preserve full session structure (turns, tool calls,
VAD events, grounding) that would be lost in OTel aggregation.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import InstrumentationConfig
    from .store import MetricsStore

logger = logging.getLogger(__name__)


class JsonFileExporter:
    """Periodically flushes MetricsStore to a JSON file.

    File lifecycle:
        1. Created on start() with timestamp-based name
        2. Overwritten on each flush with full store state
        3. Final flush on shutdown (via atexit)
        4. New file on next server start
    """

    def __init__(self, store: MetricsStore, config: InstrumentationConfig) -> None:
        self._store = store
        self._config = config
        self._flush_interval_s = config.json_flush_interval_s
        self._metrics_dir = Path(config.metrics_dir)
        self._flush_task: asyncio.Task | None = None
        self._running = False

        # Generate filename with server start timestamp
        start_ts = store.server_start_time.strftime("%Y-%m-%dT%H-%M-%S")
        self._file_path = self._metrics_dir / f"metrics_{start_ts}.json"

        # Register atexit for final flush
        atexit.register(self._shutdown_flush)

        logger.info(f"JsonFileExporter initialized → {self._file_path}")

    @property
    def file_path(self) -> Path:
        """Path to the current metrics JSON file."""
        return self._file_path

    def start(self) -> None:
        """Start the periodic flush background task.

        Must be called from within a running asyncio event loop.
        """
        if self._running:
            logger.warning("JsonFileExporter already running.")
            return

        self._running = True

        # Initial flush to create the file
        self._flush()

        try:
            loop = asyncio.get_running_loop()
            self._flush_task = loop.create_task(self._periodic_flush_loop())
            logger.info(
                f"Periodic JSON flush started "
                f"(interval={self._flush_interval_s}s, file={self._file_path})"
            )
        except RuntimeError:
            logger.warning(
                "No running event loop. Periodic flush not started. "
                "Only atexit flush will work."
            )

    def stop(self) -> None:
        """Stop periodic flushing and do a final flush."""
        self._running = False
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None
        # Final flush
        self._flush()
        logger.info("JsonFileExporter stopped. Final flush written.")

    def flush_now(self) -> None:
        """Trigger an immediate flush (callable from anywhere)."""
        self._flush()

    def _flush(self) -> None:
        """Write full store state to JSON file (atomic write)."""
        try:
            data = self._store.to_dict()
            data["server_end_time"] = datetime.utcnow().isoformat()
            data["metrics_file"] = str(self._file_path)

            json_bytes = json.dumps(data, indent=2, default=str).encode("utf-8")

            # Atomic write: write to temp file, then rename
            self._metrics_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._metrics_dir),
                prefix=".metrics_",
                suffix=".tmp",
            )
            try:
                os.write(fd, json_bytes)
                os.fsync(fd)
                os.close(fd)
                os.replace(tmp_path, str(self._file_path))
            except Exception:
                os.close(fd) if not os.get_inheritable(fd) else None
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            session_count = len(data.get("sessions", {}))
            logger.debug(
                f"JSON flush: {len(json_bytes)} bytes, "
                f"{session_count} session(s) → {self._file_path}"
            )
        except Exception:
            logger.exception("Failed to flush metrics to JSON file")

    async def _periodic_flush_loop(self) -> None:
        """Background task that flushes at regular intervals."""
        try:
            while self._running:
                await asyncio.sleep(self._flush_interval_s)
                if self._running:
                    self._flush()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error in periodic JSON flush loop")

    def _shutdown_flush(self) -> None:
        """atexit handler — final synchronous flush."""
        try:
            if self._store is not None:
                self._flush()
                logger.info(f"Shutdown flush complete → {self._file_path}")
        except Exception:
            # atexit handlers should not raise
            pass

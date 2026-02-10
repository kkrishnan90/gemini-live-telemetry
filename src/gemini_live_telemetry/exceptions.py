"""Custom exception types for Gemini Live instrumentation."""

from __future__ import annotations


class InstrumentationError(Exception):
    """Base exception for all instrumentation errors."""


class ConfigurationError(InstrumentationError):
    """Raised when instrumentation configuration is invalid."""


class ActivationError(InstrumentationError):
    """Raised when instrumentation activation fails (e.g., SDK not found)."""


class ExporterError(InstrumentationError):
    """Raised when metric export fails (GCP or JSON)."""


class DashboardError(InstrumentationError):
    """Raised when dashboard creation/update fails."""


class SessionNotFoundError(InstrumentationError):
    """Raised when querying metrics for a non-existent session ID."""


class StoreError(InstrumentationError):
    """Raised when metrics store operations fail."""

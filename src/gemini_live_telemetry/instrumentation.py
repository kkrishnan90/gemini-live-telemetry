"""Apply wrapt patches to the google-genai SDK.

Patches 5 methods on AsyncSession and AsyncLive to intercept
all send/receive operations for metrics collection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import wrapt

from . import _wrappers

if TYPE_CHECKING:
    from .store import MetricsStore

logger = logging.getLogger(__name__)

_patches_applied = False


def apply_patches(store: MetricsStore) -> None:
    """Apply wrapt patches to google.genai.live module.

    Patches:
        1. AsyncSession._receive           — intercept all incoming messages
        2. AsyncSession.send_realtime_input — intercept outgoing audio/activity
        3. AsyncSession.send_client_content — intercept outgoing content
        4. AsyncSession.send_tool_response  — intercept tool responses
        5. AsyncLive.connect               — intercept session lifecycle

    Args:
        store: The MetricsStore to record metrics into.

    Raises:
        ActivationError: If google.genai.live module cannot be patched.
    """
    global _patches_applied
    if _patches_applied:
        logger.warning("Patches already applied. Skipping.")
        return

    from .exceptions import ActivationError

    # Set the store reference in wrappers module
    _wrappers.set_store(store)

    try:
        # Patch 1: AsyncSession._receive
        wrapt.wrap_function_wrapper(
            "google.genai.live",
            "AsyncSession._receive",
            _wrappers.wrap_receive,
        )
        logger.debug("Patched: AsyncSession._receive")

        # Patch 2: AsyncSession.send_realtime_input
        wrapt.wrap_function_wrapper(
            "google.genai.live",
            "AsyncSession.send_realtime_input",
            _wrappers.wrap_send_realtime_input,
        )
        logger.debug("Patched: AsyncSession.send_realtime_input")

        # Patch 3: AsyncSession.send_client_content
        wrapt.wrap_function_wrapper(
            "google.genai.live",
            "AsyncSession.send_client_content",
            _wrappers.wrap_send_client_content,
        )
        logger.debug("Patched: AsyncSession.send_client_content")

        # Patch 4: AsyncSession.send_tool_response
        wrapt.wrap_function_wrapper(
            "google.genai.live",
            "AsyncSession.send_tool_response",
            _wrappers.wrap_send_tool_response,
        )
        logger.debug("Patched: AsyncSession.send_tool_response")

        # Patch 5: AsyncLive.connect
        wrapt.wrap_function_wrapper(
            "google.genai.live",
            "AsyncLive.connect",
            _wrappers.wrap_connect,
        )
        logger.debug("Patched: AsyncLive.connect")

        _patches_applied = True
        logger.info(
            "All 5 wrapt patches applied to google.genai.live "
            "(AsyncSession._receive, send_realtime_input, "
            "send_client_content, send_tool_response, AsyncLive.connect)"
        )

    except Exception as e:
        raise ActivationError(
            f"Failed to apply wrapt patches to google.genai.live: {e}"
        ) from e

"""wrapt wrapper functions for the 5 google-genai SDK methods.

Each wrapper intercepts SDK calls, extracts metrics, and records them
in the MetricsStore. Wrappers NEVER break SDK functionality — all
metric extraction is wrapped in try/except.
"""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from .models import (
    SessionMetrics,
    SessionStatus,
    VADEvent,
    VADEventType,
    VADMode,
)
from . import _instruments as inst
from ._session_map import SessionMap

if TYPE_CHECKING:
    from .store import MetricsStore

logger = logging.getLogger(__name__)

# Module-level references set during apply_patches()
_store: MetricsStore | None = None
_session_map: SessionMap = SessionMap()


def set_store(store: MetricsStore) -> None:
    """Set the global store reference. Called once from apply_patches()."""
    global _store
    _store = store


def get_session_map() -> SessionMap:
    """Get the global session map (for testing)."""
    return _session_map


# --- Helper to safely get session metrics ---

def _get_session_metrics(instance: object) -> SessionMetrics | None:
    """Look up SessionMetrics for an AsyncSession instance."""
    if _store is None:
        return None
    entry = _session_map.get(instance)
    if entry is None:
        return None
    try:
        return _store.get_session(entry.session_id)
    except Exception:
        return None


# --- Wrapper 1: AsyncSession._receive() ---

async def wrap_receive(wrapped, instance, args, kwargs):
    """Intercept every incoming LiveServerMessage.

    Extracts: usage_metadata, turn lifecycle, tool calls, VAD signals,
    grounding, transcriptions, audio bytes, timing for TTFB.
    """
    result = await wrapped(*args, **kwargs)

    try:
        session_metrics = _get_session_metrics(instance)
        if session_metrics is None:
            return result

        timing = _session_map.get_timing(instance)
        if timing is None:
            return result

        from ._receive_handler import process_server_message
        process_server_message(result, session_metrics, timing)
    except Exception:
        logger.exception("Error in _receive wrapper (metrics only, SDK unaffected)")

    return result


# --- Wrapper 2: AsyncSession.send_realtime_input() ---

async def wrap_send_realtime_input(wrapped, instance, args, kwargs):
    """Intercept outgoing audio, activity signals, and text.

    Tracks: audio bytes sent, activity_start/end timestamps,
    audio_stream_end, last_audio_send_time for TTFB.
    """
    try:
        session_metrics = _get_session_metrics(instance)
        timing = _session_map.get_timing(instance)

        if session_metrics is not None and timing is not None:
            now = time.time()
            session_metrics.messages_sent += 1
            attrs = {inst.ATTR_SESSION_ID: session_metrics.session_id}

            # OTel: messages sent
            if inst.messages_sent is not None:
                inst.messages_sent.add(1, {**attrs, inst.ATTR_METHOD: "send_realtime_input"})

            # Audio: track bytes and last send time
            audio = kwargs.get("audio")
            if audio is not None:
                timing.last_audio_send_time = now
                data = getattr(audio, "data", None)
                if data is None and isinstance(audio, dict):
                    data = audio.get("data")
                if data is not None:
                    byte_count = len(data)
                    session_metrics.audio_bytes_sent += byte_count
                    # OTel: audio bytes sent
                    if inst.audio_bytes_sent is not None:
                        inst.audio_bytes_sent.add(byte_count, attrs)

            # Activity end (custom VAD) — TTFB reference point
            if kwargs.get("activity_end") is not None:
                timing.last_activity_end_time = now
                timing.first_audio_in_turn_received = False
                session_metrics.vad_events.append(VADEvent(
                    event_type=VADEventType.ACTIVITY_END_SENT,
                    source=VADMode.CUSTOM,
                ))

            # Activity start (custom VAD)
            if kwargs.get("activity_start") is not None:
                session_metrics.vad_events.append(VADEvent(
                    event_type=VADEventType.ACTIVITY_START_SENT,
                    source=VADMode.CUSTOM,
                ))

            # Audio stream end
            if kwargs.get("audio_stream_end"):
                session_metrics.vad_events.append(VADEvent(
                    event_type=VADEventType.AUDIO_STREAM_END_SENT,
                    source=VADMode.CUSTOM,
                ))
    except Exception:
        logger.exception("Error in send_realtime_input wrapper")

    return await wrapped(*args, **kwargs)


# --- Wrapper 3: AsyncSession.send_client_content() ---

async def wrap_send_client_content(wrapped, instance, args, kwargs):
    """Intercept outgoing turn-based content. Tracks message count."""
    try:
        session_metrics = _get_session_metrics(instance)
        if session_metrics is not None:
            session_metrics.messages_sent += 1
            # OTel: messages sent
            if inst.messages_sent is not None:
                inst.messages_sent.add(1, {
                    inst.ATTR_SESSION_ID: session_metrics.session_id,
                    inst.ATTR_METHOD: "send_client_content",
                })
    except Exception:
        logger.exception("Error in send_client_content wrapper")

    return await wrapped(*args, **kwargs)


# --- Wrapper 4: AsyncSession.send_tool_response() ---

async def wrap_send_tool_response(wrapped, instance, args, kwargs):
    """Intercept outgoing tool responses. Computes tool round-trip time.

    Correlates with tool_call received_at by function_response.id.
    """
    try:
        session_metrics = _get_session_metrics(instance)
        if session_metrics is not None:
            session_metrics.messages_sent += 1
            now = datetime.utcnow()
            attrs = {inst.ATTR_SESSION_ID: session_metrics.session_id}

            # OTel: messages sent
            if inst.messages_sent is not None:
                inst.messages_sent.add(1, {**attrs, inst.ATTR_METHOD: "send_tool_response"})

            # Extract function_response IDs for round-trip correlation
            fn_responses = kwargs.get("function_responses")
            if fn_responses is not None:
                # Normalize to list
                if not isinstance(fn_responses, (list, tuple)):
                    fn_responses = [fn_responses]

                for fr in fn_responses:
                    fr_id = getattr(fr, "id", None)
                    if fr_id is None and isinstance(fr, dict):
                        fr_id = fr.get("id")

                    # Find matching tool call and compute round-trip
                    # Match by ID first, then by name (fallback for SDK returning None IDs)
                    fr_name = getattr(fr, "name", None)
                    if fr_name is None and isinstance(fr, dict):
                        fr_name = fr.get("name")

                    if not fr_id and not fr_name:
                        continue  # Can't correlate without ID or name

                    matched_tc = None
                    for tc in reversed(session_metrics.tool_calls):
                        if tc.response_sent_at is not None:
                            continue
                        # Match by ID if both are non-empty
                        if fr_id and tc.tool_id and tc.tool_id == fr_id:
                            matched_tc = tc
                            break
                        # Fallback: match by name (most recent unresponded)
                        if fr_name and tc.tool_name == fr_name:
                            matched_tc = tc
                            break

                    if matched_tc is not None:
                        tc = matched_tc
                        if tc.response_sent_at is None:
                            tc.response_sent_at = now
                            if tc.received_at is not None:
                                delta = (now - tc.received_at).total_seconds()
                                tc.round_trip_ms = delta * 1000
                                # OTel: tool round-trip histogram
                                if inst.latency_tool_round_trip is not None:
                                    inst.latency_tool_round_trip.record(
                                        tc.round_trip_ms,
                                        {**attrs, inst.ATTR_TOOL_NAME: tc.tool_name},
                                    )
                                logger.debug(
                                    f"Tool {tc.tool_name} round-trip: "
                                    f"{tc.round_trip_ms:.2f}ms"
                                )
                            break
    except Exception:
        logger.exception("Error in send_tool_response wrapper")

    return await wrapped(*args, **kwargs)


# --- Wrapper 5: AsyncLive.connect() ---

def wrap_connect(wrapped, instance, args, kwargs):
    """Intercept session lifecycle: connect, setup, disconnect.

    Tracks: session creation, setup latency, session duration,
    active session count.
    """
    original_cm = wrapped(*args, **kwargs)

    @contextlib.asynccontextmanager
    async def _instrumented_connect():
        connect_start = time.time()

        async with original_cm as session:
            # Session is now connected, session_id is available
            session_id = getattr(session, "session_id", None) or "unknown"
            setup_elapsed_ms = (time.time() - connect_start) * 1000

            try:
                if _store is not None:
                    session_metrics = await _store.create_session(session_id)
                    session_metrics.status = SessionStatus.ACTIVE
                    session_metrics.setup_latency_ms = setup_elapsed_ms

                    # Register in session map for wrapper correlation
                    _session_map.register(
                        session, session_id, connect_start
                    )

                    # OTel: setup latency + active sessions
                    attrs = {inst.ATTR_SESSION_ID: session_id}
                    if inst.latency_setup is not None:
                        inst.latency_setup.record(setup_elapsed_ms, attrs)
                    if inst.sessions_active is not None:
                        inst.sessions_active.add(1)

                    logger.info(
                        f"Session {session_id} instrumented "
                        f"(setup: {setup_elapsed_ms:.2f}ms)"
                    )
            except Exception:
                logger.exception("Error in connect wrapper (session start)")

            try:
                yield session
            finally:
                # Session is disconnecting
                try:
                    if _store is not None and _store.has_session(session_id):
                        sm = _store.get_session(session_id)
                        sm.end_time = datetime.utcnow()
                        sm.status = SessionStatus.COMPLETED
                        duration = (time.time() - connect_start) * 1000
                        logger.info(
                            f"Session {session_id} ended "
                            f"(duration: {duration:.0f}ms)"
                        )
                    # OTel: decrement active sessions
                    if inst.sessions_active is not None:
                        inst.sessions_active.add(-1)
                except Exception:
                    logger.exception("Error in connect wrapper (session end)")
                finally:
                    _session_map.remove(session)

    return _instrumented_connect()

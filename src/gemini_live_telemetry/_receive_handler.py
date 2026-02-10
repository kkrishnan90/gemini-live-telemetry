"""Processes LiveServerMessage responses and extracts all metrics.

Called by the _receive() wrapper for every message from the server.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from .models import (
    GroundingEvent,
    ModalityTokens,
    SessionMetrics,
    SessionStatus,
    ToolCallMetrics,
    TurnMetrics,
    UsageMetrics,
    VADEvent,
    VADEventType,
    VADMode,
)
from . import _instruments as inst
from ._session_map import SessionTimingState

logger = logging.getLogger(__name__)

# OTel attribute helpers
def _sid_attrs(session_metrics: SessionMetrics) -> dict:
    return {inst.ATTR_SESSION_ID: session_metrics.session_id}


def process_server_message(
    message: object,
    session_metrics: SessionMetrics,
    timing: SessionTimingState,
) -> None:
    """Extract all metrics from a LiveServerMessage.

    Args:
        message: A google.genai.types.LiveServerMessage instance.
        session_metrics: The SessionMetrics to record into.
        timing: The per-session timing state.
    """
    now = time.time()
    session_metrics.messages_received += 1
    attrs = _sid_attrs(session_metrics)

    # OTel: messages received
    if inst.messages_received is not None:
        inst.messages_received.add(1, attrs)

    # 1. Usage metadata (token counts)
    _handle_usage_metadata(message, timing)

    # 2. Server content (turns, audio, transcription, grounding)
    sc = getattr(message, "server_content", None)
    if sc is not None:
        _handle_server_content(sc, session_metrics, timing, now)

    # 3. Tool calls
    tc = getattr(message, "tool_call", None)
    if tc is not None:
        _handle_tool_call(tc, session_metrics, timing, now)

    # 4. Tool call cancellations
    tcc = getattr(message, "tool_call_cancellation", None)
    if tcc is not None:
        _handle_tool_cancellation(tcc, session_metrics)

    # 5. Voice activity detection signal (native VAD)
    vad_signal = getattr(message, "voice_activity_detection_signal", None)
    if vad_signal is not None:
        _handle_vad_signal(vad_signal, session_metrics, timing, now)

    # 6. Voice activity (native VAD)
    va = getattr(message, "voice_activity", None)
    if va is not None:
        _handle_voice_activity(va, session_metrics, now)

    # 7. Go-away
    go_away = getattr(message, "go_away", None)
    if go_away is not None:
        time_left = getattr(go_away, "time_left", None)
        logger.warning(f"Go-away received. Time left: {time_left}")

    # 8. Session resumption update
    sru = getattr(message, "session_resumption_update", None)
    if sru is not None:
        resumable = getattr(sru, "resumable", None)
        logger.debug(f"Session resumption update: resumable={resumable}")


def _handle_usage_metadata(message: object, timing: SessionTimingState) -> None:
    """Extract token counts from usage_metadata."""
    um = getattr(message, "usage_metadata", None)
    if um is None:
        return

    usage = UsageMetrics(
        prompt_token_count=getattr(um, "prompt_token_count", 0) or 0,
        response_token_count=getattr(um, "response_token_count", 0) or 0,
        total_token_count=getattr(um, "total_token_count", 0) or 0,
        cached_content_token_count=getattr(um, "cached_content_token_count", 0) or 0,
        tool_use_prompt_token_count=getattr(um, "tool_use_prompt_token_count", 0) or 0,
        thoughts_token_count=getattr(um, "thoughts_token_count", 0) or 0,
        traffic_type=str(getattr(um, "traffic_type", None)),
    )

    # Per-modality breakdowns
    for attr, target in [
        ("prompt_tokens_details", "prompt_tokens_details"),
        ("response_tokens_details", "response_tokens_details"),
        ("cache_tokens_details", "cache_tokens_details"),
        ("tool_use_prompt_tokens_details", "tool_use_tokens_details"),
    ]:
        details = getattr(um, attr, None)
        if details:
            setattr(usage, target, [
                ModalityTokens(
                    modality=str(getattr(d, "modality", "UNKNOWN")),
                    token_count=getattr(d, "token_count", 0) or 0,
                )
                for d in details
            ])

    # Overwrite per-turn usage (last one wins — most complete for the turn)
    timing.current_turn_usage = usage


def _handle_server_content(
    sc: object,
    session_metrics: SessionMetrics,
    timing: SessionTimingState,
    now: float,
) -> None:
    """Process server_content: model turn, completion, interruption, grounding."""

    # Check for model content (audio/text) — used for TTFB and turn duration
    model_turn = getattr(sc, "model_turn", None)
    if model_turn is not None:
        parts = getattr(model_turn, "parts", None) or []
        has_audio = any(getattr(p, "inline_data", None) is not None for p in parts)

        # Track first content in turn (for turn duration)
        if timing.turn_first_content_time is None:
            timing.turn_first_content_time = now

        # TTFB: first audio response after speech end
        if has_audio and not timing.first_audio_in_turn_received:
            timing.first_audio_in_turn_received = True
            ref_time = timing.get_ttfb_reference_time()
            if ref_time is not None:
                ttfb_ms = (now - ref_time) * 1000
                session_metrics.ttfb_values.append(ttfb_ms)
                # OTel: TTFB histogram
                if inst.latency_ttfb is not None:
                    vad_mode = "custom" if timing.last_activity_end_time else "native"
                    inst.latency_ttfb.record(ttfb_ms, {
                        **_sid_attrs(session_metrics),
                        inst.ATTR_VAD_MODE: vad_mode,
                    })
                logger.debug(f"TTFB: {ttfb_ms:.2f}ms")

        # Count audio bytes received
        for p in parts:
            inline_data = getattr(p, "inline_data", None)
            if inline_data is not None:
                data = getattr(inline_data, "data", None)
                if data is not None:
                    byte_count = len(data)
                    session_metrics.audio_bytes_received += byte_count
                    # OTel: audio bytes received
                    if inst.audio_bytes_received is not None:
                        inst.audio_bytes_received.add(byte_count, _sid_attrs(session_metrics))

    # Grounding metadata
    gm = getattr(sc, "grounding_metadata", None)
    if gm is not None:
        timing.current_turn_has_grounding = True
        chunks = getattr(gm, "grounding_chunks", None) or []
        supports = getattr(gm, "grounding_supports", None) or []
        confidence_scores = []
        for s in supports:
            scores = getattr(s, "confidence_scores", None) or []
            confidence_scores.extend(scores)

        rm = getattr(gm, "retrieval_metadata", None)
        retrieval_score = (
            getattr(rm, "google_search_dynamic_retrieval_score", None)
            if rm else None
        )
        queries = getattr(gm, "web_search_queries", None) or []

        session_metrics.grounding_events.append(GroundingEvent(
            chunk_count=len(chunks),
            confidence_scores=confidence_scores,
            retrieval_score=retrieval_score,
            web_search_queries=list(queries),
        ))
        # OTel: grounding invocation
        if inst.grounding_invocations is not None:
            inst.grounding_invocations.add(1, _sid_attrs(session_metrics))

    # Turn complete
    if getattr(sc, "turn_complete", False):
        _finalize_turn(session_metrics, timing, now, was_interrupted=False)
        reason = getattr(sc, "turn_complete_reason", None)
        if reason is not None and session_metrics.turns:
            session_metrics.turns[-1].turn_complete_reason = str(reason)

    # Generation complete
    if getattr(sc, "generation_complete", False):
        if session_metrics.turns:
            session_metrics.turns[-1].generation_complete = True

    # Interrupted
    if getattr(sc, "interrupted", False):
        _finalize_turn(session_metrics, timing, now, was_interrupted=True)


def _finalize_turn(
    session_metrics: SessionMetrics,
    timing: SessionTimingState,
    now: float,
    *,
    was_interrupted: bool,
) -> None:
    """Record completed turn metrics and reset timing state."""
    timing.current_turn_number += 1
    turn_start = timing.turn_first_content_time
    duration_ms = (now - turn_start) * 1000 if turn_start is not None else None

    # Get last TTFB for this turn (if any)
    turn_ttfb = (
        session_metrics.ttfb_values[-1]
        if session_metrics.ttfb_values and timing.first_audio_in_turn_received
        else None
    )

    turn = TurnMetrics(
        turn_number=timing.current_turn_number,
        start_time=datetime.utcfromtimestamp(turn_start) if turn_start else None,
        end_time=datetime.utcnow(),
        duration_ms=duration_ms,
        was_interrupted=was_interrupted,
        usage=getattr(timing, "current_turn_usage", None),
        tool_call_count=timing.current_turn_tool_count,
        ttfb_ms=turn_ttfb,
        has_grounding=timing.current_turn_has_grounding,
    )
    session_metrics.turns.append(turn)
    attrs = _sid_attrs(session_metrics)

    # OTel: turn counter
    if inst.turns_total is not None:
        inst.turns_total.add(1, attrs)
    if was_interrupted and inst.turns_interrupted is not None:
        inst.turns_interrupted.add(1, attrs)

    # OTel: turn duration histogram
    if duration_ms is not None and inst.latency_turn_duration is not None:
        inst.latency_turn_duration.record(duration_ms, attrs)

    # OTel: inter-turn gap histogram
    if timing.last_turn_complete_time is not None:
        gap_ms = (now - timing.last_turn_complete_time) * 1000
        # Only record if there was a previous turn (gap > 0 and reasonable)
        if gap_ms > 0 and inst.latency_inter_turn_gap is not None:
            inst.latency_inter_turn_gap.record(gap_ms, attrs)

    # OTel: token counters (record per-turn usage at finalization)
    usage = getattr(timing, "current_turn_usage", None)
    if usage is not None:
        if inst.tokens_prompt is not None:
            inst.tokens_prompt.add(usage.prompt_token_count, attrs)
        if inst.tokens_response is not None:
            inst.tokens_response.add(usage.response_token_count, attrs)
        if inst.tokens_total is not None:
            inst.tokens_total.add(usage.total_token_count, attrs)
        if usage.cached_content_token_count and inst.tokens_cached is not None:
            inst.tokens_cached.add(usage.cached_content_token_count, attrs)
        if usage.tool_use_prompt_token_count and inst.tokens_tool_use is not None:
            inst.tokens_tool_use.add(usage.tool_use_prompt_token_count, attrs)
        if usage.thoughts_token_count and inst.tokens_thoughts is not None:
            inst.tokens_thoughts.add(usage.thoughts_token_count, attrs)

    # Inter-turn gap tracking
    timing.last_turn_complete_time = now

    # Reset per-turn state
    timing.current_turn_usage = None
    timing.reset_for_new_turn()


def _handle_tool_call(
    tc: object,
    session_metrics: SessionMetrics,
    timing: SessionTimingState,
    now: float,
) -> None:
    """Record tool call events."""
    function_calls = getattr(tc, "function_calls", None) or []
    for fc in function_calls:
        tool_id = getattr(fc, "id", None) or ""
        tool_name = getattr(fc, "name", None) or ""
        args = getattr(fc, "args", None)

        session_metrics.tool_calls.append(ToolCallMetrics(
            tool_id=tool_id,
            tool_name=tool_name,
            args=dict(args) if args else None,
            received_at=datetime.utcnow(),
        ))
        timing.current_turn_tool_count += 1
        # OTel: tool call counter
        if inst.tool_calls_total is not None:
            inst.tool_calls_total.add(1, {
                **_sid_attrs(session_metrics),
                inst.ATTR_TOOL_NAME: tool_name,
            })
        logger.debug(f"Tool call: {tool_name} (id={tool_id})")


def _handle_tool_cancellation(tc: object, session_metrics: SessionMetrics) -> None:
    """Mark cancelled tool calls."""
    ids = getattr(tc, "ids", None) or []
    for cancel_id in ids:
        for tc_metric in session_metrics.tool_calls:
            if tc_metric.tool_id == cancel_id:
                tc_metric.was_cancelled = True
                # OTel: cancellation counter
                if inst.tool_calls_cancellations is not None:
                    inst.tool_calls_cancellations.add(1, _sid_attrs(session_metrics))
                logger.debug(f"Tool call cancelled: {cancel_id}")
                break


def _handle_vad_signal(
    vad_signal: object,
    session_metrics: SessionMetrics,
    timing: SessionTimingState,
    now: float,
) -> None:
    """Handle native VAD start/end of speech signals."""
    signal_type = getattr(vad_signal, "vad_signal_type", None)
    if signal_type is None:
        return

    signal_str = str(signal_type)

    if "EOS" in signal_str:
        timing.last_vad_eos_time = now
        session_metrics.vad_events.append(VADEvent(
            event_type=VADEventType.VAD_EOS,
            source=VADMode.NATIVE,
        ))
    elif "SOS" in signal_str:
        session_metrics.vad_events.append(VADEvent(
            event_type=VADEventType.VAD_SOS,
            source=VADMode.NATIVE,
        ))


def _handle_voice_activity(
    va: object,
    session_metrics: SessionMetrics,
    now: float,
) -> None:
    """Handle native voice activity start/end signals."""
    activity_type = getattr(va, "voice_activity_type", None)
    if activity_type is None:
        return

    type_str = str(activity_type)
    if "START" in type_str:
        session_metrics.vad_events.append(VADEvent(
            event_type=VADEventType.ACTIVITY_START_SERVER,
            source=VADMode.NATIVE,
        ))
    elif "END" in type_str:
        session_metrics.vad_events.append(VADEvent(
            event_type=VADEventType.ACTIVITY_END_SERVER,
            source=VADMode.NATIVE,
        ))

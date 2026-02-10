"""Data models for instrumentation metrics.

All models are dataclasses with to_dict() for JSON serialization.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    CONNECTING = "CONNECTING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class VADMode(str, Enum):
    NATIVE = "native"
    CUSTOM = "custom"


class VADEventType(str, Enum):
    # Native VAD signals from server
    VAD_SOS = "VAD_SIGNAL_TYPE_SOS"
    VAD_EOS = "VAD_SIGNAL_TYPE_EOS"
    ACTIVITY_START_SERVER = "ACTIVITY_START"
    ACTIVITY_END_SERVER = "ACTIVITY_END"
    # Custom VAD signals sent by application
    ACTIVITY_START_SENT = "ACTIVITY_START_SENT"
    ACTIVITY_END_SENT = "ACTIVITY_END_SENT"
    AUDIO_STREAM_END_SENT = "AUDIO_STREAM_END_SENT"


@dataclass
class ModalityTokens:
    """Token count for a specific modality."""
    modality: str  # TEXT, AUDIO, IMAGE, VIDEO, DOCUMENT
    token_count: int = 0

    def to_dict(self) -> dict:
        return {"modality": self.modality, "token_count": self.token_count}


@dataclass
class UsageMetrics:
    """Token usage metrics from a single usage_metadata message."""
    prompt_token_count: int = 0
    response_token_count: int = 0
    total_token_count: int = 0
    cached_content_token_count: int = 0
    tool_use_prompt_token_count: int = 0
    thoughts_token_count: int = 0
    prompt_tokens_details: list[ModalityTokens] = field(default_factory=list)
    response_tokens_details: list[ModalityTokens] = field(default_factory=list)
    cache_tokens_details: list[ModalityTokens] = field(default_factory=list)
    tool_use_tokens_details: list[ModalityTokens] = field(default_factory=list)
    traffic_type: str | None = None

    def to_dict(self) -> dict:
        return {
            "prompt_token_count": self.prompt_token_count,
            "response_token_count": self.response_token_count,
            "total_token_count": self.total_token_count,
            "cached_content_token_count": self.cached_content_token_count,
            "tool_use_prompt_token_count": self.tool_use_prompt_token_count,
            "thoughts_token_count": self.thoughts_token_count,
            "prompt_tokens_details": [d.to_dict() for d in self.prompt_tokens_details],
            "response_tokens_details": [d.to_dict() for d in self.response_tokens_details],
            "cache_tokens_details": [d.to_dict() for d in self.cache_tokens_details],
            "tool_use_tokens_details": [d.to_dict() for d in self.tool_use_tokens_details],
            "traffic_type": self.traffic_type,
        }


@dataclass
class TurnMetrics:
    """Metrics for a single conversational turn."""
    turn_number: int
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: float | None = None
    was_interrupted: bool = False
    turn_complete_reason: str | None = None
    generation_complete: bool = False
    usage: UsageMetrics | None = None
    tool_call_count: int = 0
    ttfb_ms: float | None = None
    has_grounding: bool = False

    def to_dict(self) -> dict:
        return {
            "turn_number": self.turn_number,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "was_interrupted": self.was_interrupted,
            "turn_complete_reason": self.turn_complete_reason,
            "generation_complete": self.generation_complete,
            "usage": self.usage.to_dict() if self.usage else None,
            "tool_call_count": self.tool_call_count,
            "ttfb_ms": self.ttfb_ms,
            "has_grounding": self.has_grounding,
        }


@dataclass
class ToolCallMetrics:
    """Metrics for a single tool call."""
    tool_id: str
    tool_name: str
    args: dict[str, Any] | None = None
    received_at: datetime | None = None
    response_sent_at: datetime | None = None
    round_trip_ms: float | None = None
    was_cancelled: bool = False

    def to_dict(self) -> dict:
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "args": self.args,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "response_sent_at": self.response_sent_at.isoformat() if self.response_sent_at else None,
            "round_trip_ms": self.round_trip_ms,
            "was_cancelled": self.was_cancelled,
        }


@dataclass
class VADEvent:
    """A voice activity detection event."""
    event_type: VADEventType
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: VADMode = VADMode.NATIVE

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source.value,
        }


@dataclass
class GroundingEvent:
    """A grounding/search event."""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    chunk_count: int = 0
    confidence_scores: list[float] = field(default_factory=list)
    retrieval_score: float | None = None
    web_search_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "chunk_count": self.chunk_count,
            "confidence_scores": self.confidence_scores,
            "retrieval_score": self.retrieval_score,
            "web_search_queries": self.web_search_queries,
        }


@dataclass
class SessionAggregates:
    """Computed aggregate metrics for a single session."""
    total_turns: int = 0
    total_interrupted_turns: int = 0
    interruption_rate: float = 0.0
    total_tool_calls: int = 0
    total_tool_cancellations: int = 0
    session_total_prompt_tokens: int = 0
    session_total_response_tokens: int = 0
    session_total_tokens: int = 0
    avg_ttfb_ms: float | None = None
    p50_ttfb_ms: float | None = None
    p95_ttfb_ms: float | None = None
    p99_ttfb_ms: float | None = None
    avg_turn_duration_ms: float | None = None
    avg_tool_round_trip_ms: float | None = None
    total_audio_sent_bytes: int = 0
    total_audio_received_bytes: int = 0
    total_grounding_invocations: int = 0

    def to_dict(self) -> dict:
        return {
            "total_turns": self.total_turns,
            "total_interrupted_turns": self.total_interrupted_turns,
            "interruption_rate": self.interruption_rate,
            "total_tool_calls": self.total_tool_calls,
            "total_tool_cancellations": self.total_tool_cancellations,
            "session_total_prompt_tokens": self.session_total_prompt_tokens,
            "session_total_response_tokens": self.session_total_response_tokens,
            "session_total_tokens": self.session_total_tokens,
            "avg_ttfb_ms": self.avg_ttfb_ms,
            "p50_ttfb_ms": self.p50_ttfb_ms,
            "p95_ttfb_ms": self.p95_ttfb_ms,
            "p99_ttfb_ms": self.p99_ttfb_ms,
            "avg_turn_duration_ms": self.avg_turn_duration_ms,
            "avg_tool_round_trip_ms": self.avg_tool_round_trip_ms,
            "total_audio_sent_bytes": self.total_audio_sent_bytes,
            "total_audio_received_bytes": self.total_audio_received_bytes,
            "total_grounding_invocations": self.total_grounding_invocations,
        }


@dataclass
class SessionMetrics:
    """All metrics for a single Gemini Live API session."""
    session_id: str
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime | None = None
    status: SessionStatus = SessionStatus.CONNECTING
    setup_latency_ms: float | None = None

    # Per-turn metrics
    turns: list[TurnMetrics] = field(default_factory=list)
    current_turn_number: int = 0

    # Tool call metrics
    tool_calls: list[ToolCallMetrics] = field(default_factory=list)

    # TTFB tracking
    ttfb_values: list[float] = field(default_factory=list)

    # VAD events
    vad_events: list[VADEvent] = field(default_factory=list)

    # Grounding events
    grounding_events: list[GroundingEvent] = field(default_factory=list)

    # Audio byte counters
    audio_bytes_sent: int = 0
    audio_bytes_received: int = 0

    # Message counters
    messages_sent: int = 0
    messages_received: int = 0

    def compute_aggregates(self) -> SessionAggregates:
        """Compute aggregate metrics for this session."""
        total_turns = len(self.turns)
        interrupted = sum(1 for t in self.turns if t.was_interrupted)
        cancellations = sum(1 for tc in self.tool_calls if tc.was_cancelled)

        # Sum tokens from the last usage_metadata per turn
        prompt_tokens = sum(
            t.usage.prompt_token_count for t in self.turns
            if t.usage is not None
        )
        response_tokens = sum(
            t.usage.response_token_count for t in self.turns
            if t.usage is not None
        )
        total_tokens = sum(
            t.usage.total_token_count for t in self.turns
            if t.usage is not None
        )

        turn_durations = [
            t.duration_ms for t in self.turns if t.duration_ms is not None
        ]
        tool_rts = [
            tc.round_trip_ms for tc in self.tool_calls
            if tc.round_trip_ms is not None
        ]

        return SessionAggregates(
            total_turns=total_turns,
            total_interrupted_turns=interrupted,
            interruption_rate=interrupted / total_turns if total_turns > 0 else 0.0,
            total_tool_calls=len(self.tool_calls),
            total_tool_cancellations=cancellations,
            session_total_prompt_tokens=prompt_tokens,
            session_total_response_tokens=response_tokens,
            session_total_tokens=total_tokens,
            avg_ttfb_ms=_safe_mean(self.ttfb_values),
            p50_ttfb_ms=_safe_percentile(self.ttfb_values, 50),
            p95_ttfb_ms=_safe_percentile(self.ttfb_values, 95),
            p99_ttfb_ms=_safe_percentile(self.ttfb_values, 99),
            avg_turn_duration_ms=_safe_mean(turn_durations),
            avg_tool_round_trip_ms=_safe_mean(tool_rts),
            total_audio_sent_bytes=self.audio_bytes_sent,
            total_audio_received_bytes=self.audio_bytes_received,
            total_grounding_invocations=len(self.grounding_events),
        )

    def to_dict(self) -> dict:
        agg = self.compute_aggregates()
        return {
            "session_id": self.session_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "status": self.status.value if isinstance(self.status, SessionStatus) else str(self.status),
            "setup_latency_ms": self.setup_latency_ms,
            "turns": [t.to_dict() for t in self.turns],
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "ttfb_values": self.ttfb_values,
            "vad_events": [v.to_dict() for v in self.vad_events],
            "grounding_events": [g.to_dict() for g in self.grounding_events],
            "audio_bytes_sent": self.audio_bytes_sent,
            "audio_bytes_received": self.audio_bytes_received,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "aggregates": agg.to_dict(),
        }


@dataclass
class SessionSummary:
    """Lightweight summary of a session for list_sessions()."""
    session_id: str
    start_time: datetime
    end_time: datetime | None
    status: SessionStatus
    total_turns: int
    total_tokens: int
    avg_ttfb_ms: float | None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "status": self.status.value if isinstance(self.status, SessionStatus) else str(self.status),
            "total_turns": self.total_turns,
            "total_tokens": self.total_tokens,
            "avg_ttfb_ms": self.avg_ttfb_ms,
        }


@dataclass
class GlobalAggregates:
    """Aggregated metrics across all sessions since server start."""
    total_sessions: int = 0
    total_turns: int = 0
    total_interrupted_turns: int = 0
    interruption_rate: float = 0.0
    total_tool_calls: int = 0
    total_tool_cancellations: int = 0
    total_prompt_tokens: int = 0
    total_response_tokens: int = 0
    total_tokens: int = 0
    avg_ttfb_ms: float | None = None
    p50_ttfb_ms: float | None = None
    p95_ttfb_ms: float | None = None
    p99_ttfb_ms: float | None = None
    avg_turn_duration_ms: float | None = None
    avg_tool_round_trip_ms: float | None = None
    total_audio_sent_bytes: int = 0
    total_audio_received_bytes: int = 0
    total_grounding_invocations: int = 0

    def to_dict(self) -> dict:
        return {
            "total_sessions": self.total_sessions,
            "total_turns": self.total_turns,
            "total_interrupted_turns": self.total_interrupted_turns,
            "interruption_rate": round(self.interruption_rate, 4),
            "total_tool_calls": self.total_tool_calls,
            "total_tool_cancellations": self.total_tool_cancellations,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_response_tokens": self.total_response_tokens,
            "total_tokens": self.total_tokens,
            "avg_ttfb_ms": round(self.avg_ttfb_ms, 2) if self.avg_ttfb_ms is not None else None,
            "p50_ttfb_ms": round(self.p50_ttfb_ms, 2) if self.p50_ttfb_ms is not None else None,
            "p95_ttfb_ms": round(self.p95_ttfb_ms, 2) if self.p95_ttfb_ms is not None else None,
            "p99_ttfb_ms": round(self.p99_ttfb_ms, 2) if self.p99_ttfb_ms is not None else None,
            "avg_turn_duration_ms": round(self.avg_turn_duration_ms, 2) if self.avg_turn_duration_ms is not None else None,
            "avg_tool_round_trip_ms": round(self.avg_tool_round_trip_ms, 2) if self.avg_tool_round_trip_ms is not None else None,
            "total_audio_sent_bytes": self.total_audio_sent_bytes,
            "total_audio_received_bytes": self.total_audio_received_bytes,
            "total_grounding_invocations": self.total_grounding_invocations,
        }


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.mean(values)


def _safe_percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * pct / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]

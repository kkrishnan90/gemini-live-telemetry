"""In-memory metrics store with per-session bucketing.

Holds all metrics for the lifetime of the server process.
Queryable by session ID or globally across all sessions.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from .exceptions import SessionNotFoundError
from .models import (
    GlobalAggregates,
    SessionMetrics,
    SessionStatus,
    SessionSummary,
)

if TYPE_CHECKING:
    from .config import InstrumentationConfig

logger = logging.getLogger(__name__)


class MetricsStore:
    """Process-lifetime metrics store with per-session bucketing.

    Thread/async-safe via asyncio.Lock for all mutations.
    """

    def __init__(self, config: InstrumentationConfig) -> None:
        self._config = config
        self._sessions: dict[str, SessionMetrics] = {}
        self._server_start_time = datetime.utcnow()
        self._lock = asyncio.Lock()

    @property
    def server_start_time(self) -> datetime:
        return self._server_start_time

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    async def create_session(self, session_id: str) -> SessionMetrics:
        """Create a new session metrics bucket."""
        async with self._lock:
            if session_id in self._sessions:
                logger.warning(f"Session {session_id} already exists. Returning existing.")
                return self._sessions[session_id]
            session = SessionMetrics(session_id=session_id)
            self._sessions[session_id] = session
            logger.info(f"Created metrics bucket for session {session_id}")
            return session

    def get_session(self, session_id: str) -> SessionMetrics:
        """Get metrics for a specific session.

        Raises:
            SessionNotFoundError: If session_id is not found.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session not found: {session_id}")
        return session

    def has_session(self, session_id: str) -> bool:
        """Check if a session exists in the store."""
        return session_id in self._sessions

    def list_sessions(self) -> list[SessionSummary]:
        """List summary of all sessions."""
        summaries = []
        for s in self._sessions.values():
            agg = s.compute_aggregates()
            summaries.append(
                SessionSummary(
                    session_id=s.session_id,
                    start_time=s.start_time,
                    end_time=s.end_time,
                    status=s.status,
                    total_turns=agg.total_turns,
                    total_tokens=agg.session_total_tokens,
                    avg_ttfb_ms=agg.avg_ttfb_ms,
                )
            )
        return summaries

    def get_global_aggregates(self) -> GlobalAggregates:
        """Compute aggregated metrics across all sessions."""
        all_ttfbs: list[float] = []
        total_turns = 0
        total_interrupted = 0
        total_tool_calls = 0
        total_tool_cancellations = 0
        total_prompt_tokens = 0
        total_response_tokens = 0
        total_tokens = 0
        total_audio_sent = 0
        total_audio_received = 0
        total_grounding = 0
        all_turn_durations: list[float] = []
        all_tool_rts: list[float] = []

        for s in self._sessions.values():
            agg = s.compute_aggregates()
            all_ttfbs.extend(s.ttfb_values)
            total_turns += agg.total_turns
            total_interrupted += agg.total_interrupted_turns
            total_tool_calls += agg.total_tool_calls
            total_tool_cancellations += agg.total_tool_cancellations
            total_prompt_tokens += agg.session_total_prompt_tokens
            total_response_tokens += agg.session_total_response_tokens
            total_tokens += agg.session_total_tokens
            total_audio_sent += agg.total_audio_sent_bytes
            total_audio_received += agg.total_audio_received_bytes
            total_grounding += agg.total_grounding_invocations
            all_turn_durations.extend(
                t.duration_ms for t in s.turns if t.duration_ms is not None
            )
            all_tool_rts.extend(
                tc.round_trip_ms for tc in s.tool_calls if tc.round_trip_ms is not None
            )

        interruption_rate = (
            total_interrupted / total_turns if total_turns > 0 else 0.0
        )

        return GlobalAggregates(
            total_sessions=len(self._sessions),
            total_turns=total_turns,
            total_interrupted_turns=total_interrupted,
            interruption_rate=interruption_rate,
            total_tool_calls=total_tool_calls,
            total_tool_cancellations=total_tool_cancellations,
            total_prompt_tokens=total_prompt_tokens,
            total_response_tokens=total_response_tokens,
            total_tokens=total_tokens,
            avg_ttfb_ms=_mean(all_ttfbs),
            p50_ttfb_ms=_percentile(all_ttfbs, 50),
            p95_ttfb_ms=_percentile(all_ttfbs, 95),
            p99_ttfb_ms=_percentile(all_ttfbs, 99),
            avg_turn_duration_ms=_mean(all_turn_durations),
            avg_tool_round_trip_ms=_mean(all_tool_rts),
            total_audio_sent_bytes=total_audio_sent,
            total_audio_received_bytes=total_audio_received,
            total_grounding_invocations=total_grounding,
        )

    def to_dict(self) -> dict:
        """Serialize the entire store to a JSON-compatible dict."""
        return {
            "server_start_time": self._server_start_time.isoformat(),
            "export_timestamp": datetime.utcnow().isoformat(),
            "global_aggregates": self.get_global_aggregates().to_dict(),
            "sessions": {
                sid: s.to_dict() for sid, s in self._sessions.items()
            },
        }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.mean(values)


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * pct / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]

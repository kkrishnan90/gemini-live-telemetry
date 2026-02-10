"""Maps AsyncSession instances to session IDs and per-session timing state.

Uses WeakKeyDictionary so entries are automatically cleaned up when
the AsyncSession instance is garbage collected.
"""

from __future__ import annotations

import logging
import time
import weakref
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SessionTimingState:
    """Per-session timing state for computing latency metrics.

    All timestamps are from time.time() (monotonic-ish, seconds).
    """

    # TTFB reference points (priority: activity_end > vad_eos > last_audio_send)
    last_audio_send_time: float | None = None
    last_activity_end_time: float | None = None
    last_vad_eos_time: float | None = None
    first_audio_in_turn_received: bool = False

    # Turn timing
    turn_first_content_time: float | None = None
    last_turn_complete_time: float | None = None
    current_turn_number: int = 0

    # Per-turn accumulators (reset at turn_complete)
    current_turn_tool_count: int = 0
    current_turn_has_grounding: bool = False

    def reset_for_new_turn(self) -> None:
        """Reset per-turn state after turn_complete or interrupted."""
        self.first_audio_in_turn_received = False
        self.turn_first_content_time = None
        self.current_turn_tool_count = 0
        self.current_turn_has_grounding = False
        # Reset TTFB reference points
        self.last_activity_end_time = None
        self.last_vad_eos_time = None

    def get_ttfb_reference_time(self) -> float | None:
        """Get the most accurate TTFB reference timestamp.

        Priority:
            1. activity_end sent (custom VAD — most precise)
            2. VAD EOS received from server (native VAD — precise)
            3. last audio send time (fallback — approximate)
        """
        if self.last_activity_end_time is not None:
            return self.last_activity_end_time
        if self.last_vad_eos_time is not None:
            return self.last_vad_eos_time
        return self.last_audio_send_time


@dataclass
class SessionEntry:
    """Entry in the session map linking an AsyncSession to its metadata."""
    session_id: str
    timing: SessionTimingState = field(default_factory=SessionTimingState)
    connect_start_time: float = field(default_factory=time.time)


class SessionMap:
    """Maps AsyncSession instances to session IDs and timing state.

    Uses WeakKeyDictionary: entries are auto-removed when the
    AsyncSession object is garbage collected.
    """

    def __init__(self) -> None:
        self._map: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    def register(
        self,
        session: object,
        session_id: str,
        connect_start_time: float | None = None,
    ) -> SessionEntry:
        """Register an AsyncSession with its session ID."""
        entry = SessionEntry(
            session_id=session_id,
            connect_start_time=connect_start_time or time.time(),
        )
        self._map[session] = entry
        logger.debug(f"Registered session {session_id} in session map")
        return entry

    def get(self, session: object) -> SessionEntry | None:
        """Look up the entry for an AsyncSession instance."""
        return self._map.get(session)

    def get_session_id(self, session: object) -> str | None:
        """Get the session ID for an AsyncSession, or None."""
        entry = self._map.get(session)
        return entry.session_id if entry else None

    def get_timing(self, session: object) -> SessionTimingState | None:
        """Get the timing state for an AsyncSession, or None."""
        entry = self._map.get(session)
        return entry.timing if entry else None

    def remove(self, session: object) -> None:
        """Explicitly remove an AsyncSession from the map."""
        try:
            del self._map[session]
        except KeyError:
            pass

    def __len__(self) -> int:
        return len(self._map)

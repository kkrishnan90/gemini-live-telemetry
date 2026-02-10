"""OpenTelemetry instrument definitions for Gemini Live API metrics.

All instruments are module-level singletons, initialized by setup_otel().
Until initialization, all values are None — recording code must null-check.

Instruments:
    15 Counters (monotonically increasing)
    5 Histograms (distributions)
    1 UpDownCounter (active session gauge)
"""

from __future__ import annotations

from opentelemetry.metrics import (
    Counter,
    Histogram,
    Meter,
    UpDownCounter,
)

# --- Attribute key constants ---
ATTR_SESSION_ID = "session_id"
ATTR_TOOL_NAME = "tool_name"
ATTR_MODALITY = "modality"
ATTR_VAD_MODE = "vad_mode"
ATTR_METHOD = "method"

# --- Counters (15) ---
turns_total: Counter | None = None
turns_interrupted: Counter | None = None
tokens_prompt: Counter | None = None
tokens_response: Counter | None = None
tokens_total: Counter | None = None
tokens_cached: Counter | None = None
tokens_tool_use: Counter | None = None
tokens_thoughts: Counter | None = None
tool_calls_total: Counter | None = None
tool_calls_cancellations: Counter | None = None
messages_sent: Counter | None = None
messages_received: Counter | None = None
audio_bytes_sent: Counter | None = None
audio_bytes_received: Counter | None = None
grounding_invocations: Counter | None = None

# --- Histograms (5) ---
latency_ttfb: Histogram | None = None
latency_turn_duration: Histogram | None = None
latency_tool_round_trip: Histogram | None = None
latency_inter_turn_gap: Histogram | None = None
latency_setup: Histogram | None = None

# --- UpDownCounter (1) ---
sessions_active: UpDownCounter | None = None

# Histogram bucket boundaries for latency metrics (milliseconds)
TTFB_BUCKETS = [50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 5000]
DURATION_BUCKETS = [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000]
TOOL_RT_BUCKETS = [100, 500, 1000, 2000, 5000, 8000, 10000, 15000, 30000]
SETUP_BUCKETS = [50, 100, 200, 500, 1000, 2000, 5000]


def create_instruments(meter: Meter) -> None:
    """Create all 21 OTel instruments on the given meter.

    Called once by setup_otel(). After this, all module-level instrument
    variables are non-None and ready for recording.
    """
    global turns_total, turns_interrupted
    global tokens_prompt, tokens_response, tokens_total
    global tokens_cached, tokens_tool_use, tokens_thoughts
    global tool_calls_total, tool_calls_cancellations
    global messages_sent, messages_received
    global audio_bytes_sent, audio_bytes_received
    global grounding_invocations
    global latency_ttfb, latency_turn_duration, latency_tool_round_trip
    global latency_inter_turn_gap, latency_setup
    global sessions_active

    # --- Counters ---
    turns_total = meter.create_counter(
        name="gemini_live.turns.total",
        description="Total completed turns",
        unit="1",
    )
    turns_interrupted = meter.create_counter(
        name="gemini_live.turns.interrupted",
        description="Turns interrupted by user speech",
        unit="1",
    )
    tokens_prompt = meter.create_counter(
        name="gemini_live.tokens.prompt",
        description="Prompt tokens consumed",
        unit="1",
    )
    tokens_response = meter.create_counter(
        name="gemini_live.tokens.response",
        description="Response tokens generated",
        unit="1",
    )
    tokens_total = meter.create_counter(
        name="gemini_live.tokens.total",
        description="Total tokens (prompt + response)",
        unit="1",
    )
    tokens_cached = meter.create_counter(
        name="gemini_live.tokens.cached",
        description="Tokens served from cache",
        unit="1",
    )
    tokens_tool_use = meter.create_counter(
        name="gemini_live.tokens.tool_use",
        description="Tokens consumed by tool-use prompts",
        unit="1",
    )
    tokens_thoughts = meter.create_counter(
        name="gemini_live.tokens.thoughts",
        description="Tokens consumed by model thinking",
        unit="1",
    )
    tool_calls_total = meter.create_counter(
        name="gemini_live.tool_calls.total",
        description="Total tool calls received from model",
        unit="1",
    )
    tool_calls_cancellations = meter.create_counter(
        name="gemini_live.tool_calls.cancellations",
        description="Tool calls cancelled by model",
        unit="1",
    )
    messages_sent = meter.create_counter(
        name="gemini_live.messages.sent",
        description="Messages sent to Gemini",
        unit="1",
    )
    messages_received = meter.create_counter(
        name="gemini_live.messages.received",
        description="Messages received from Gemini",
        unit="1",
    )
    audio_bytes_sent = meter.create_counter(
        name="gemini_live.audio.bytes_sent",
        description="Audio bytes sent to Gemini",
        unit="By",
    )
    audio_bytes_received = meter.create_counter(
        name="gemini_live.audio.bytes_received",
        description="Audio bytes received from Gemini",
        unit="By",
    )
    grounding_invocations = meter.create_counter(
        name="gemini_live.grounding.invocations",
        description="Responses with grounding/search metadata",
        unit="1",
    )

    # --- Histograms ---
    latency_ttfb = meter.create_histogram(
        name="gemini_live.latency.ttfb_ms",
        description="Time to first byte (speech end to first audio response)",
        unit="ms",
    )
    latency_turn_duration = meter.create_histogram(
        name="gemini_live.latency.turn_duration_ms",
        description="Turn duration (first content to turn_complete)",
        unit="ms",
    )
    latency_tool_round_trip = meter.create_histogram(
        name="gemini_live.latency.tool_round_trip_ms",
        description="Tool call round-trip (call received to response sent)",
        unit="ms",
    )
    latency_inter_turn_gap = meter.create_histogram(
        name="gemini_live.latency.inter_turn_gap_ms",
        description="Gap between turns (turn_complete to next content)",
        unit="ms",
    )
    latency_setup = meter.create_histogram(
        name="gemini_live.latency.setup_ms",
        description="Session setup latency (connect to setup_complete)",
        unit="ms",
    )

    # --- UpDownCounter ---
    sessions_active = meter.create_up_down_counter(
        name="gemini_live.sessions.active",
        description="Currently active Gemini Live sessions",
        unit="1",
    )

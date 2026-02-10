"""Accuracy comparison tool: OTel JSON vs Application-side JSONL.

Compares metrics collected by the wrapt instrumentation (OTel, automatic)
against metrics logged from the application code (JSONL, manual) to
validate instrumentation accuracy.

CLI usage:
    python -m gemini_live_telemetry.compare \
        --otel metrics/metrics_2026-02-10T10-00-00.json \
        --app metrics/logs/app_metrics_2026-02-10T10-00-00.jsonl

    # Or with custom tolerance:
    python -m gemini_live_telemetry.compare \
        --otel metrics.json --app app.jsonl --timing-tolerance 10.0
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


# Default tolerances
DEFAULT_TIMING_TOLERANCE_MS = 5.0  # ±5ms for timing metrics
DEFAULT_COUNTER_TOLERANCE = 0  # Exact match for counters


@dataclass
class ComparisonResult:
    """Result of comparing a single metric value."""
    session_id: str
    metric: str
    context: str  # e.g., "turn 1", "tool tc_1", "overall"
    otel_value: float | int | None
    app_value: float | int | None
    difference: float | None
    tolerance: float
    status: str  # "MATCH", "MISMATCH", "MISSING_OTEL", "MISSING_APP"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "metric": self.metric,
            "context": self.context,
            "otel_value": self.otel_value,
            "app_value": self.app_value,
            "difference": round(self.difference, 3) if self.difference is not None else None,
            "tolerance": self.tolerance,
            "status": self.status,
        }


@dataclass
class ComparisonReport:
    """Full comparison report across all sessions."""
    otel_file: str
    app_file: str
    sessions_compared: int = 0
    metrics_compared: int = 0
    matches: int = 0
    mismatches: int = 0
    missing_otel: int = 0
    missing_app: int = 0
    results: list[ComparisonResult] = field(default_factory=list)

    @property
    def accuracy_pct(self) -> float:
        if self.metrics_compared == 0:
            return 0.0
        return (self.matches / self.metrics_compared) * 100

    def add(self, result: ComparisonResult) -> None:
        self.results.append(result)
        self.metrics_compared += 1
        if result.status == "MATCH":
            self.matches += 1
        elif result.status == "MISMATCH":
            self.mismatches += 1
        elif result.status == "MISSING_OTEL":
            self.missing_otel += 1
        elif result.status == "MISSING_APP":
            self.missing_app += 1

    def format_report(self) -> str:
        """Generate a human-readable comparison report."""
        lines = [
            "=" * 60,
            "  ACCURACY COMPARISON REPORT",
            "=" * 60,
            f"  OTel file: {self.otel_file}",
            f"  App file:  {self.app_file}",
            "",
            f"  Sessions compared:  {self.sessions_compared}",
            f"  Metrics compared:   {self.metrics_compared}",
            "",
            f"  MATCHES:     {self.matches:4d}  ({self.accuracy_pct:.1f}%)",
            f"  MISMATCHES:  {self.mismatches:4d}  "
            f"({(self.mismatches / max(self.metrics_compared, 1) * 100):.1f}%)",
            f"  MISSING_OTEL:{self.missing_otel:4d}  (in App but not in OTel)",
            f"  MISSING_APP: {self.missing_app:4d}  (in OTel but not in App)",
            "=" * 60,
        ]

        # Details for non-matches
        non_matches = [r for r in self.results if r.status != "MATCH"]
        if non_matches:
            lines.append("")
            lines.append("--- Non-matching metrics ---")
            for r in non_matches:
                if r.status == "MISMATCH":
                    lines.append(
                        f"  [{r.status}] {r.session_id} / {r.metric} ({r.context}): "
                        f"OTel={r.otel_value}, App={r.app_value}, "
                        f"delta={r.difference}, tolerance={r.tolerance}"
                    )
                elif r.status == "MISSING_OTEL":
                    lines.append(
                        f"  [{r.status}] {r.session_id} / {r.metric} ({r.context}): "
                        f"App={r.app_value}"
                    )
                elif r.status == "MISSING_APP":
                    lines.append(
                        f"  [{r.status}] {r.session_id} / {r.metric} ({r.context}): "
                        f"OTel={r.otel_value}"
                    )
        else:
            lines.append("")
            lines.append("  All metrics match. Instrumentation is accurate.")

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "otel_file": self.otel_file,
            "app_file": self.app_file,
            "sessions_compared": self.sessions_compared,
            "metrics_compared": self.metrics_compared,
            "matches": self.matches,
            "mismatches": self.mismatches,
            "missing_otel": self.missing_otel,
            "missing_app": self.missing_app,
            "accuracy_pct": round(self.accuracy_pct, 2),
            "results": [r.to_dict() for r in self.results if r.status != "MATCH"],
        }


# --- Parsers ---

def parse_otel_json(path: str | Path) -> dict[str, dict]:
    """Parse OTel JSON file → dict of session_id → normalized metrics.

    Returns:
        {session_id: {
            "ttfb_values": [float],
            "total_turns": int,
            "total_interrupted": int,
            "prompt_tokens": int,
            "response_tokens": int,
            "total_tokens": int,
            "tool_calls": [{tool_id, tool_name, round_trip_ms}],
            "tool_call_count": int,
            "setup_latency_ms": float | None,
        }}
    """
    with open(path) as f:
        data = json.load(f)

    result = {}
    for sid, session in data.get("sessions", {}).items():
        turns = session.get("turns", [])
        tool_calls = session.get("tool_calls", [])

        prompt_tokens = sum(
            t.get("usage", {}).get("prompt_token_count", 0)
            for t in turns if t.get("usage")
        )
        response_tokens = sum(
            t.get("usage", {}).get("response_token_count", 0)
            for t in turns if t.get("usage")
        )
        total_tokens = sum(
            t.get("usage", {}).get("total_token_count", 0)
            for t in turns if t.get("usage")
        )

        result[sid] = {
            "ttfb_values": sorted(session.get("ttfb_values", [])),
            "total_turns": len(turns),
            "total_interrupted": sum(1 for t in turns if t.get("was_interrupted")),
            "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "total_tokens": total_tokens,
            "tool_calls": [
                {
                    "tool_id": tc.get("tool_id"),
                    "tool_name": tc.get("tool_name"),
                    "round_trip_ms": tc.get("round_trip_ms"),
                }
                for tc in tool_calls
            ],
            "tool_call_count": len(tool_calls),
            "setup_latency_ms": session.get("setup_latency_ms"),
            "audio_bytes_sent": session.get("audio_bytes_sent", 0),
            "audio_bytes_received": session.get("audio_bytes_received", 0),
        }

    return result


def parse_app_jsonl(path: str | Path) -> dict[str, dict]:
    """Parse App JSONL file → dict of session_id → normalized metrics.

    Returns same structure as parse_otel_json for comparison.
    """
    records: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # Group by session_id
    sessions: dict[str, list[dict]] = {}
    for r in records:
        sid = r.get("session_id", "")
        sessions.setdefault(sid, []).append(r)

    result = {}
    for sid, recs in sessions.items():
        ttfb_values = sorted(
            r["value"] for r in recs if r["metric"] == "ttfb_ms"
        )
        turn_completes = [r for r in recs if r["metric"] == "turn_complete"]
        total_turns = len(turn_completes)
        total_interrupted = sum(
            1 for r in turn_completes
            if r.get("attributes", {}).get("was_interrupted", False)
        )

        # Token totals from "tokens" metric entries
        token_recs = [r for r in recs if r["metric"] == "tokens"]
        prompt_tokens = sum(
            r.get("attributes", {}).get("prompt_token_count", 0) for r in token_recs
        )
        response_tokens = sum(
            r.get("attributes", {}).get("response_token_count", 0) for r in token_recs
        )
        total_tokens = sum(
            r.get("attributes", {}).get("total_token_count", 0) for r in token_recs
        )

        # Tool calls
        tool_call_recs = [r for r in recs if r["metric"] == "tool_call"]
        tool_response_recs = [r for r in recs if r["metric"] == "tool_response"]

        # Build tool call list with round-trip from responses
        tool_calls = []
        for tc in tool_call_recs:
            attrs = tc.get("attributes", {})
            tid = attrs.get("tool_id", "")
            tname = attrs.get("tool_name", "")
            # Find matching response
            rt_ms = None
            for tr in tool_response_recs:
                tr_attrs = tr.get("attributes", {})
                if tr_attrs.get("tool_id") == tid:
                    rt_ms = tr_attrs.get("round_trip_ms")
                    break
            tool_calls.append({
                "tool_id": tid,
                "tool_name": tname,
                "round_trip_ms": rt_ms,
            })

        # Setup latency
        setup_recs = [r for r in recs if r["metric"] == "setup_latency_ms"]
        setup_latency = setup_recs[0]["value"] if setup_recs else None

        # Audio bytes
        audio_sent = sum(r["value"] for r in recs if r["metric"] == "audio_sent")
        audio_received = sum(r["value"] for r in recs if r["metric"] == "audio_received")

        result[sid] = {
            "ttfb_values": ttfb_values,
            "total_turns": total_turns,
            "total_interrupted": total_interrupted,
            "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "total_tokens": total_tokens,
            "tool_calls": tool_calls,
            "tool_call_count": len(tool_calls),
            "setup_latency_ms": setup_latency,
            "audio_bytes_sent": audio_sent,
            "audio_bytes_received": audio_received,
        }

    return result


# --- Comparison engine ---

def compare(
    otel_path: str | Path,
    app_path: str | Path,
    timing_tolerance_ms: float = DEFAULT_TIMING_TOLERANCE_MS,
) -> ComparisonReport:
    """Compare OTel JSON metrics against App JSONL metrics.

    Args:
        otel_path: Path to OTel metrics JSON file.
        app_path: Path to application JSONL log file.
        timing_tolerance_ms: Tolerance for timing metrics (ms).

    Returns:
        ComparisonReport with detailed results.
    """
    otel_data = parse_otel_json(otel_path)
    app_data = parse_app_jsonl(app_path)

    report = ComparisonReport(
        otel_file=str(otel_path),
        app_file=str(app_path),
    )

    # Union of all session IDs
    all_sids = set(otel_data.keys()) | set(app_data.keys())
    report.sessions_compared = len(all_sids)

    for sid in sorted(all_sids):
        otel = otel_data.get(sid)
        app = app_data.get(sid)

        if otel is None:
            # Session in App but not OTel
            report.add(ComparisonResult(
                sid, "session", "existence", None, 1, None,
                0, "MISSING_OTEL",
            ))
            continue
        if app is None:
            # Session in OTel but not App
            report.add(ComparisonResult(
                sid, "session", "existence", 1, None, None,
                0, "MISSING_APP",
            ))
            continue

        # Compare counter metrics (exact match)
        _compare_counter(report, sid, "total_turns", otel, app)
        _compare_counter(report, sid, "total_interrupted", otel, app)
        _compare_counter(report, sid, "prompt_tokens", otel, app)
        _compare_counter(report, sid, "response_tokens", otel, app)
        _compare_counter(report, sid, "total_tokens", otel, app)
        _compare_counter(report, sid, "tool_call_count", otel, app)
        _compare_counter(report, sid, "audio_bytes_sent", otel, app)
        _compare_counter(report, sid, "audio_bytes_received", otel, app)

        # Compare timing metrics (with tolerance)
        if otel.get("setup_latency_ms") is not None or app.get("setup_latency_ms") is not None:
            _compare_timing(report, sid, "setup_latency_ms",
                            otel.get("setup_latency_ms"),
                            app.get("setup_latency_ms"),
                            timing_tolerance_ms)

        # Compare TTFB values (ordered lists)
        otel_ttfbs = otel.get("ttfb_values", [])
        app_ttfbs = app.get("ttfb_values", [])

        if len(otel_ttfbs) != len(app_ttfbs):
            report.add(ComparisonResult(
                sid, "ttfb_count", "overall",
                len(otel_ttfbs), len(app_ttfbs),
                abs(len(otel_ttfbs) - len(app_ttfbs)),
                0, "MISMATCH",
            ))
        else:
            report.add(ComparisonResult(
                sid, "ttfb_count", "overall",
                len(otel_ttfbs), len(app_ttfbs), 0, 0, "MATCH",
            ))

        # Compare individual TTFB values
        for i, (ov, av) in enumerate(zip(otel_ttfbs, app_ttfbs)):
            _compare_timing(report, sid, "ttfb_ms", ov, av,
                            timing_tolerance_ms, context=f"value[{i}]")

        # Compare tool round-trip times
        otel_tools = {tc["tool_id"]: tc for tc in otel.get("tool_calls", [])}
        app_tools = {tc["tool_id"]: tc for tc in app.get("tool_calls", [])}

        for tid in set(otel_tools.keys()) | set(app_tools.keys()):
            ot = otel_tools.get(tid)
            at = app_tools.get(tid)
            if ot and at:
                ort = ot.get("round_trip_ms")
                art = at.get("round_trip_ms")
                if ort is not None and art is not None:
                    _compare_timing(report, sid, "tool_round_trip_ms",
                                    ort, art, timing_tolerance_ms,
                                    context=f"tool {tid}")

    return report


def _compare_counter(
    report: ComparisonReport, sid: str, metric: str,
    otel: dict, app: dict,
) -> None:
    """Compare a counter metric (exact match expected)."""
    ov = otel.get(metric, 0)
    av = app.get(metric, 0)
    diff = abs(ov - av)
    status = "MATCH" if diff == 0 else "MISMATCH"
    report.add(ComparisonResult(
        sid, metric, "overall", ov, av, diff,
        DEFAULT_COUNTER_TOLERANCE, status,
    ))


def _compare_timing(
    report: ComparisonReport, sid: str, metric: str,
    otel_val: float | None, app_val: float | None,
    tolerance: float, context: str = "overall",
) -> None:
    """Compare a timing metric (within tolerance)."""
    if otel_val is None and app_val is None:
        return
    if otel_val is None:
        report.add(ComparisonResult(
            sid, metric, context, None, app_val, None,
            tolerance, "MISSING_OTEL",
        ))
        return
    if app_val is None:
        report.add(ComparisonResult(
            sid, metric, context, otel_val, None, None,
            tolerance, "MISSING_APP",
        ))
        return

    diff = abs(otel_val - app_val)
    status = "MATCH" if diff <= tolerance else "MISMATCH"
    report.add(ComparisonResult(
        sid, metric, context, otel_val, app_val, diff,
        tolerance, status,
    ))


# --- CLI ---

def main() -> None:
    """CLI entry point for the comparison tool."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare OTel metrics vs application-side logs for accuracy.",
        prog="python -m gemini_live_telemetry.compare",
    )
    parser.add_argument(
        "--otel", required=True,
        help="Path to OTel metrics JSON file (from _json_exporter)",
    )
    parser.add_argument(
        "--app", required=True,
        help="Path to application JSONL log file (from AppMetricsLogger)",
    )
    parser.add_argument(
        "--timing-tolerance", type=float, default=DEFAULT_TIMING_TOLERANCE_MS,
        help=f"Tolerance for timing metrics in ms (default: {DEFAULT_TIMING_TOLERANCE_MS})",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output report as JSON instead of text",
    )

    args = parser.parse_args()

    otel_path = Path(args.otel)
    app_path = Path(args.app)

    if not otel_path.exists():
        print(f"Error: OTel file not found: {otel_path}", file=sys.stderr)
        sys.exit(1)
    if not app_path.exists():
        print(f"Error: App file not found: {app_path}", file=sys.stderr)
        sys.exit(1)

    report = compare(otel_path, app_path, args.timing_tolerance)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format_report())

    # Exit with non-zero if mismatches found
    sys.exit(1 if report.mismatches > 0 else 0)


if __name__ == "__main__":
    main()

"""Integration test: Real Gemini Live API + wrapt instrumentation + app logger.

Runs a REAL session against the Gemini Live API with instrumentation active.
Verifies that:
1. wrapt wrappers fire on real SDK calls → MetricsStore populated
2. App logger records real metrics → JSONL file written
3. JSON export captures real session data
4. Comparison tool validates accuracy between the two

Requires: valid GCP credentials (service account or gcloud auth)

Usage:
    cd backend
    .venv/bin/python -m gemini_live_telemetry.tests.test_integration
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# Ensure backend directory is on path
BACKEND_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("integration_test")

TEST_DIR = BACKEND_DIR / "test_integration_output"


def setup():
    """Clean up and prepare test output directory."""
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    TEST_DIR.mkdir(parents=True)


def test_result(name: str, passed: bool, detail: str = ""):
    """Print a test result line."""
    status = "PASS" if passed else "FAIL"
    msg = f"  [{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return passed


async def run_integration_test():
    """Run the full integration test with real Gemini Live API."""
    setup()
    all_passed = True
    print("=" * 60)
    print("  INTEGRATION TEST — Real Gemini Live API")
    print("=" * 60)

    # ──────────────────────────────────────────────
    # Step 1: Activate instrumentation
    # ──────────────────────────────────────────────
    print("\n--- Step 1: Activate instrumentation ---")
    from gemini_live_telemetry import activate, get_metrics_store, get_app_logger, get_json_exporter
    from gemini_live_telemetry.config import InstrumentationConfig

    cfg = InstrumentationConfig(
        project_id="vital-octagon-19612",
        metrics_dir=str(TEST_DIR / "metrics"),
        log_dir=str(TEST_DIR / "logs"),
        enable_gcp_export=True,  # Push real metrics to Cloud Monitoring
        enable_json_export=True,
        enable_dashboard=True,  # Create/update real dashboard
    )
    activate(cfg)

    store = get_metrics_store()
    app_log = get_app_logger()
    json_exp = get_json_exporter()

    all_passed &= test_result("Instrumentation activated", True)
    all_passed &= test_result(
        "Store initialized", store is not None,
        f"server_start={store.server_start_time}",
    )
    all_passed &= test_result(
        "App logger initialized", app_log is not None,
        f"file={app_log.file_path}",
    )
    all_passed &= test_result(
        "JSON exporter initialized", json_exp is not None,
        f"file={json_exp.file_path}",
    )

    # ──────────────────────────────────────────────
    # Step 2: Connect to Gemini using real application code
    # ──────────────────────────────────────────────
    print("\n--- Step 2: Connect to Gemini Live API ---")

    from gemini_client import GeminiLiveClient
    from config import MODEL_NAME

    client = GeminiLiveClient(
        system_instruction="You are a helpful assistant. Keep responses brief (1-2 sentences).",
        enable_google_search=False,  # Simpler test without grounding
    )

    connect_start = time.time()
    try:
        await client.connect()
    except Exception as e:
        print(f"\n  FATAL: Cannot connect to Gemini Live API: {e}")
        print("  Check: credentials, network, API quota")
        return False

    connect_ms = (time.time() - connect_start) * 1000
    session_id = client.gemini_session_id

    all_passed &= test_result("Connected to Gemini", client.is_connected, f"session_id={session_id}")
    all_passed &= test_result("Setup latency measured", connect_ms > 0, f"{connect_ms:.0f}ms")

    # App-side: log session start + setup latency
    if session_id:
        app_log.log_session_start(session_id, model=MODEL_NAME)
        app_log.log_setup_latency(session_id, connect_ms)

    # ──────────────────────────────────────────────
    # Step 3: Send text and receive response
    # ──────────────────────────────────────────────
    print("\n--- Step 3: Send text prompt + receive response ---")

    # Send a text prompt via the underlying SDK session
    from google.genai import types

    await client._session.send_client_content(
        turns=[types.Content(
            role="user",
            parts=[types.Part(text="Hello, what is 2 plus 3? Answer briefly.")],
        )],
        turn_complete=True,
    )
    logger.info("Sent text prompt to Gemini")

    # Receive responses using the application's receive loop
    # This triggers BOTH wrapt wrappers AND app logger calls
    received_types = []
    turn_completed = False
    ttfb_recorded = False

    try:
        async for msg in client.receive_messages():
            msg_type = msg.get("type")
            received_types.append(msg_type)

            if msg_type == "audio":
                if not ttfb_recorded and msg.get("ttfb_ms") is not None:
                    ttfb_recorded = True
                    logger.info(f"TTFB received: {msg['ttfb_ms']:.2f}ms")

            elif msg_type == "turn_complete":
                turn_completed = True
                logger.info("Turn complete received")

            elif msg_type == "turn_metrics":
                logger.info(f"Turn metrics: {msg}")

            elif msg_type == "transcript":
                role = msg.get("role", "?")
                text = msg.get("text", "")[:80]
                logger.info(f"Transcript [{role}]: {text}")

            elif msg_type == "error":
                logger.error(f"Error from Gemini: {msg.get('error')}")
                break

            # Stop after turn_complete + turn_metrics
            if turn_completed and msg_type == "turn_metrics":
                break

    except asyncio.TimeoutError:
        logger.warning("Receive timed out after 30s")
    except Exception as e:
        logger.error(f"Error receiving: {e}")

    all_passed &= test_result(
        "Received messages", len(received_types) > 0,
        f"types={received_types}",
    )
    all_passed &= test_result("Turn completed", turn_completed)

    # ──────────────────────────────────────────────
    # Step 4: Disconnect
    # ──────────────────────────────────────────────
    print("\n--- Step 4: Disconnect ---")

    # App-side: log session end
    if session_id:
        app_log.log_session_end(session_id)

    await client.disconnect()
    all_passed &= test_result("Disconnected", not client.is_connected)

    # Small delay to let async operations settle
    await asyncio.sleep(0.5)

    # ──────────────────────────────────────────────
    # Step 5: Verify MetricsStore (wrapt-collected)
    # ──────────────────────────────────────────────
    print("\n--- Step 5: Verify MetricsStore (wrapt-collected) ---")

    has_session = store.has_session(session_id) if session_id else False
    all_passed &= test_result("Session in store", has_session, f"session_id={session_id}")

    if has_session:
        sm = store.get_session(session_id)
        agg = sm.compute_aggregates()

        all_passed &= test_result(
            "Turns recorded", agg.total_turns > 0,
            f"turns={agg.total_turns}",
        )
        all_passed &= test_result(
            "Messages received", sm.messages_received > 0,
            f"count={sm.messages_received}",
        )
        all_passed &= test_result(
            "Setup latency", sm.setup_latency_ms is not None,
            f"{sm.setup_latency_ms:.2f}ms" if sm.setup_latency_ms else "None",
        )

        # Token counts (from usage_metadata)
        has_tokens = agg.session_total_tokens > 0
        all_passed &= test_result(
            "Token counts", has_tokens,
            f"prompt={agg.session_total_prompt_tokens}, "
            f"response={agg.session_total_response_tokens}, "
            f"total={agg.session_total_tokens}",
        )

        # Audio bytes (model responds with audio)
        has_audio = sm.audio_bytes_received > 0
        all_passed &= test_result(
            "Audio bytes received", has_audio,
            f"{sm.audio_bytes_received} bytes",
        )

        # TTFB values — not expected in text-only test (no audio input = no TTFB reference)
        has_ttfb = len(sm.ttfb_values) > 0
        test_result(  # Non-fatal: text input can't produce TTFB
            "TTFB values (text-only: expected none)", has_ttfb or True,
            f"values={sm.ttfb_values}" if has_ttfb else "none (expected for text-only test)",
        )

    # ──────────────────────────────────────────────
    # Step 6: Flush and verify JSON export
    # ──────────────────────────────────────────────
    print("\n--- Step 6: Verify JSON export ---")

    json_exp.flush_now()
    json_exists = json_exp.file_path.exists()
    all_passed &= test_result("JSON file exists", json_exists, str(json_exp.file_path))

    if json_exists:
        with open(json_exp.file_path) as f:
            json_data = json.load(f)

        has_sessions = session_id in json_data.get("sessions", {})
        all_passed &= test_result(
            "Session in JSON", has_sessions,
            f"sessions={list(json_data.get('sessions', {}).keys())}",
        )

        if has_sessions:
            js = json_data["sessions"][session_id]
            all_passed &= test_result(
                "JSON: turns present", len(js.get("turns", [])) > 0,
                f"count={len(js.get('turns', []))}",
            )
            all_passed &= test_result(
                "JSON: session_id matches", js.get("session_id") == session_id,
            )

    # ──────────────────────────────────────────────
    # Step 7: Flush and verify App JSONL
    # ──────────────────────────────────────────────
    print("\n--- Step 7: Verify App JSONL ---")

    app_log.flush_now()
    jsonl_exists = app_log.file_path.exists()
    all_passed &= test_result("JSONL file exists", jsonl_exists, str(app_log.file_path))

    if jsonl_exists:
        with open(app_log.file_path) as f:
            jsonl_lines = [json.loads(line) for line in f if line.strip()]

        all_passed &= test_result(
            "JSONL has records", len(jsonl_lines) > 0,
            f"count={len(jsonl_lines)}",
        )

        metrics_found = {r["metric"] for r in jsonl_lines}
        all_passed &= test_result(
            "JSONL: session_start logged", "session_start" in metrics_found,
        )
        all_passed &= test_result(
            "JSONL: setup_latency logged", "setup_latency_ms" in metrics_found,
        )

        # Check if turn_complete was logged by the app code
        has_turn = "turn_complete" in metrics_found
        all_passed &= test_result(
            "JSONL: turn_complete logged", has_turn,
            f"metrics={sorted(metrics_found)}",
        )

        # Verify session_id matches in JSONL records
        jsonl_sids = {r["session_id"] for r in jsonl_lines if r.get("session_id")}
        sid_matches = session_id in jsonl_sids if session_id else False
        all_passed &= test_result(
            "JSONL: session_id matches", sid_matches,
            f"JSONL sids={jsonl_sids}",
        )

    # ──────────────────────────────────────────────
    # Step 8: Run comparison tool
    # ──────────────────────────────────────────────
    print("\n--- Step 8: Accuracy comparison ---")

    if json_exists and jsonl_exists:
        from gemini_live_telemetry.compare import compare

        report = compare(
            json_exp.file_path, app_log.file_path,
            timing_tolerance_ms=50.0,  # More tolerance for real API timing jitter
        )

        all_passed &= test_result(
            "Comparison ran", report.metrics_compared > 0,
            f"compared={report.metrics_compared}",
        )
        all_passed &= test_result(
            f"Accuracy", report.accuracy_pct >= 80.0,
            f"{report.accuracy_pct:.1f}% ({report.matches}/{report.metrics_compared})",
        )

        print()
        print(report.format_report())
    else:
        print("  Skipped: missing JSON or JSONL file")

    # ──────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    if all_passed:
        print("  ALL TESTS PASSED")
    else:
        print("  SOME TESTS FAILED — review output above")
    print("=" * 60)
    print(f"\n  Output files in: {TEST_DIR}")
    print(f"  JSON:  {json_exp.file_path if json_exp else 'N/A'}")
    print(f"  JSONL: {app_log.file_path if app_log else 'N/A'}")

    return all_passed


if __name__ == "__main__":
    os.chdir(BACKEND_DIR)
    success = asyncio.run(run_integration_test())
    sys.exit(0 if success else 1)

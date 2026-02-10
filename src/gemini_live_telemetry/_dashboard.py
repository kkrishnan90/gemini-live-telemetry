"""Google Cloud Monitoring dashboard auto-creation.

Creates (or updates) a dashboard with scorecards, time series charts,
and a session_id filter for all Gemini Live API metrics.

Requires IAM role: roles/monitoring.dashboardEditor
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import InstrumentationConfig

logger = logging.getLogger(__name__)


def create_or_update_dashboard(config: InstrumentationConfig) -> None:
    """Create or update the Gemini Live API metrics dashboard.

    Checks if a dashboard with the configured display_name already exists.
    If so, updates it (preserving etag). Otherwise creates a new one.

    Args:
        config: Instrumentation configuration with project_id and dashboard_name.
    """
    try:
        from google.cloud import monitoring_dashboard_v1
        from google.cloud.monitoring_dashboard_v1 import types
    except ImportError:
        logger.warning(
            "google-cloud-monitoring-dashboards not installed. "
            "Dashboard creation skipped."
        )
        return

    if not config.project_id:
        logger.warning("No project_id configured. Dashboard creation skipped.")
        return

    client = monitoring_dashboard_v1.DashboardsServiceClient()
    parent = f"projects/{config.project_id}"
    prefix = config.metric_prefix

    dashboard = _build_dashboard(config.dashboard_name, prefix, types)

    # Check if dashboard already exists
    existing = _find_dashboard(client, parent, config.dashboard_name)

    try:
        if existing:
            dashboard.name = existing.name
            dashboard.etag = existing.etag
            request = monitoring_dashboard_v1.UpdateDashboardRequest(
                dashboard=dashboard
            )
            response = client.update_dashboard(request=request)
            logger.info(f"Dashboard updated: {response.name}")
        else:
            request = monitoring_dashboard_v1.CreateDashboardRequest(
                parent=parent, dashboard=dashboard
            )
            response = client.create_dashboard(request=request)
            logger.info(f"Dashboard created: {response.name}")
    except Exception as e:
        error_str = str(e)
        if "PERMISSION_DENIED" in error_str or "403" in error_str:
            logger.warning(
                f"Dashboard creation failed — insufficient permissions. "
                f"Grant roles/monitoring.dashboardEditor to your service account. "
                f"Error: {e}"
            )
        else:
            logger.warning(f"Dashboard creation failed: {e}")


def _find_dashboard(client, parent: str, display_name: str):
    """Find an existing dashboard by display_name."""
    try:
        from google.cloud import monitoring_dashboard_v1

        request = monitoring_dashboard_v1.ListDashboardsRequest(parent=parent)
        for dashboard in client.list_dashboards(request=request):
            if dashboard.display_name == display_name:
                return dashboard
    except Exception as e:
        logger.debug(f"Error listing dashboards: {e}")
    return None


def _build_dashboard(name: str, prefix: str, types) -> object:
    """Build the complete dashboard definition with all widgets."""

    def _metric(metric_name: str) -> str:
        """Build a Cloud Monitoring filter string for a metric."""
        return f'metric.type="{prefix}/{metric_name}"'

    # --- Row 1: Scorecards ---
    scorecard_ttfb = types.Widget(
        title="Avg TTFB",
        scorecard=types.Scorecard(
            time_series_query=types.TimeSeriesQuery(
                time_series_filter=types.TimeSeriesFilter(
                    filter=_metric("gemini_live.latency.ttfb_ms"),
                    aggregation=types.Aggregation(
                        per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                        cross_series_reducer=types.Aggregation.Reducer.REDUCE_MEAN,
                    ),
                )
            ),
            thresholds=[
                types.Threshold(
                    value=500, color=types.Threshold.Color.YELLOW,
                    direction=types.Threshold.Direction.ABOVE, label="Slow",
                ),
                types.Threshold(
                    value=1000, color=types.Threshold.Color.RED,
                    direction=types.Threshold.Direction.ABOVE, label="Critical",
                ),
            ],
        ),
    )

    scorecard_turns = types.Widget(
        title="Total Turns",
        scorecard=types.Scorecard(
            time_series_query=types.TimeSeriesQuery(
                time_series_filter=types.TimeSeriesFilter(
                    filter=_metric("gemini_live.turns.total"),
                    aggregation=types.Aggregation(
                        per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                        cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                    ),
                )
            ),
        ),
    )

    scorecard_tokens = types.Widget(
        title="Total Tokens",
        scorecard=types.Scorecard(
            time_series_query=types.TimeSeriesQuery(
                time_series_filter=types.TimeSeriesFilter(
                    filter=_metric("gemini_live.tokens.total"),
                    aggregation=types.Aggregation(
                        per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                        cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                    ),
                )
            ),
        ),
    )

    scorecard_interrupts = types.Widget(
        title="Interrupted Turns",
        scorecard=types.Scorecard(
            time_series_query=types.TimeSeriesQuery(
                time_series_filter=types.TimeSeriesFilter(
                    filter=_metric("gemini_live.turns.interrupted"),
                    aggregation=types.Aggregation(
                        per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                        cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                    ),
                )
            ),
        ),
    )

    scorecard_active = types.Widget(
        title="Active Sessions",
        scorecard=types.Scorecard(
            time_series_query=types.TimeSeriesQuery(
                time_series_filter=types.TimeSeriesFilter(
                    filter=_metric("gemini_live.sessions.active"),
                    aggregation=types.Aggregation(
                        per_series_aligner=types.Aggregation.Aligner.ALIGN_MEAN,
                        cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                    ),
                )
            ),
        ),
    )

    scorecard_setup = types.Widget(
        title="Avg Setup Latency",
        scorecard=types.Scorecard(
            time_series_query=types.TimeSeriesQuery(
                time_series_filter=types.TimeSeriesFilter(
                    filter=_metric("gemini_live.latency.setup_ms"),
                    aggregation=types.Aggregation(
                        per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                        cross_series_reducer=types.Aggregation.Reducer.REDUCE_MEAN,
                    ),
                )
            ),
        ),
    )

    # --- Row 2: TTFB + Token charts ---
    chart_ttfb = types.Widget(
        title="TTFB Over Time",
        xy_chart=types.XyChart(
            data_sets=[
                types.XyChart.DataSet(
                    time_series_query=types.TimeSeriesQuery(
                        time_series_filter=types.TimeSeriesFilter(
                            filter=_metric("gemini_live.latency.ttfb_ms"),
                            aggregation=types.Aggregation(
                                per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                                alignment_period={"seconds": 60},
                                cross_series_reducer=types.Aggregation.Reducer.REDUCE_MEAN,
                                group_by_fields=["metric.label.session_id"],
                            ),
                        )
                    ),
                    plot_type=types.XyChart.DataSet.PlotType.LINE,
                ),
            ],
            y_axis=types.XyChart.Axis(label="ms"),
        ),
    )

    chart_tokens = types.Widget(
        title="Tokens per Turn (Prompt vs Response)",
        xy_chart=types.XyChart(
            data_sets=[
                types.XyChart.DataSet(
                    time_series_query=types.TimeSeriesQuery(
                        time_series_filter=types.TimeSeriesFilter(
                            filter=_metric("gemini_live.tokens.prompt"),
                            aggregation=types.Aggregation(
                                per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                                alignment_period={"seconds": 60},
                                cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                                group_by_fields=["metric.label.session_id"],
                            ),
                        )
                    ),
                    plot_type=types.XyChart.DataSet.PlotType.STACKED_BAR,
                ),
                types.XyChart.DataSet(
                    time_series_query=types.TimeSeriesQuery(
                        time_series_filter=types.TimeSeriesFilter(
                            filter=_metric("gemini_live.tokens.response"),
                            aggregation=types.Aggregation(
                                per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                                alignment_period={"seconds": 60},
                                cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                                group_by_fields=["metric.label.session_id"],
                            ),
                        )
                    ),
                    plot_type=types.XyChart.DataSet.PlotType.STACKED_BAR,
                ),
            ],
            y_axis=types.XyChart.Axis(label="tokens"),
        ),
    )

    # --- Row 3: Tool round-trip + Audio ---
    chart_tool_rt = types.Widget(
        title="Tool Call Round-Trip Time",
        xy_chart=types.XyChart(
            data_sets=[
                types.XyChart.DataSet(
                    time_series_query=types.TimeSeriesQuery(
                        time_series_filter=types.TimeSeriesFilter(
                            filter=_metric("gemini_live.latency.tool_round_trip_ms"),
                            aggregation=types.Aggregation(
                                per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                                alignment_period={"seconds": 60},
                                cross_series_reducer=types.Aggregation.Reducer.REDUCE_MEAN,
                                group_by_fields=["metric.label.tool_name"],
                            ),
                        )
                    ),
                    plot_type=types.XyChart.DataSet.PlotType.LINE,
                ),
            ],
            y_axis=types.XyChart.Axis(label="ms"),
        ),
    )

    chart_audio = types.Widget(
        title="Audio Bytes (Sent / Received)",
        xy_chart=types.XyChart(
            data_sets=[
                types.XyChart.DataSet(
                    time_series_query=types.TimeSeriesQuery(
                        time_series_filter=types.TimeSeriesFilter(
                            filter=_metric("gemini_live.audio.bytes_sent"),
                            aggregation=types.Aggregation(
                                per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                                alignment_period={"seconds": 60},
                                cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                            ),
                        )
                    ),
                    plot_type=types.XyChart.DataSet.PlotType.STACKED_AREA,
                ),
                types.XyChart.DataSet(
                    time_series_query=types.TimeSeriesQuery(
                        time_series_filter=types.TimeSeriesFilter(
                            filter=_metric("gemini_live.audio.bytes_received"),
                            aggregation=types.Aggregation(
                                per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                                alignment_period={"seconds": 60},
                                cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                            ),
                        )
                    ),
                    plot_type=types.XyChart.DataSet.PlotType.STACKED_AREA,
                ),
            ],
            y_axis=types.XyChart.Axis(label="bytes"),
        ),
    )

    # --- Row 4: Turn duration + Tool calls count ---
    chart_turn_dur = types.Widget(
        title="Turn Duration Over Time",
        xy_chart=types.XyChart(
            data_sets=[
                types.XyChart.DataSet(
                    time_series_query=types.TimeSeriesQuery(
                        time_series_filter=types.TimeSeriesFilter(
                            filter=_metric("gemini_live.latency.turn_duration_ms"),
                            aggregation=types.Aggregation(
                                per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                                alignment_period={"seconds": 60},
                                cross_series_reducer=types.Aggregation.Reducer.REDUCE_MEAN,
                                group_by_fields=["metric.label.session_id"],
                            ),
                        )
                    ),
                    plot_type=types.XyChart.DataSet.PlotType.LINE,
                ),
            ],
            y_axis=types.XyChart.Axis(label="ms"),
        ),
    )

    chart_tool_calls = types.Widget(
        title="Tool Calls by Type",
        xy_chart=types.XyChart(
            data_sets=[
                types.XyChart.DataSet(
                    time_series_query=types.TimeSeriesQuery(
                        time_series_filter=types.TimeSeriesFilter(
                            filter=_metric("gemini_live.tool_calls.total"),
                            aggregation=types.Aggregation(
                                per_series_aligner=types.Aggregation.Aligner.ALIGN_DELTA,
                                alignment_period={"seconds": 60},
                                cross_series_reducer=types.Aggregation.Reducer.REDUCE_SUM,
                                group_by_fields=["metric.label.tool_name"],
                            ),
                        )
                    ),
                    plot_type=types.XyChart.DataSet.PlotType.STACKED_BAR,
                ),
            ],
            y_axis=types.XyChart.Axis(label="calls"),
        ),
    )

    # --- Build dashboard with session_id filter ---
    dashboard = types.Dashboard(
        display_name=name,
        dashboard_filters=[
            types.DashboardFilter(
                label_key="session_id",
                string_value="",  # Empty = show all sessions
                filter_type=types.DashboardFilter.FilterType.METRIC_LABEL,
                template_variable="session_id",
            ),
        ],
        grid_layout=types.GridLayout(
            columns=3,
            widgets=[
                # Row 1: Scorecards
                scorecard_ttfb,
                scorecard_turns,
                scorecard_tokens,
                scorecard_interrupts,
                scorecard_active,
                scorecard_setup,
                # Row 2: TTFB + Tokens
                chart_ttfb,
                chart_tokens,
                # Row 3: Tools + Audio
                chart_tool_rt,
                chart_audio,
                # Row 4: Turn duration + Tool calls
                chart_turn_dur,
                chart_tool_calls,
            ],
        ),
    )

    return dashboard

"""Optional dashboard: render monitor snapshots and submit validated setpoints."""

from time import monotonic

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html

from .controller import Chiller, ProtocolError, _seconds
from .monitor import Monitor, Snapshot


def _reading(label: str, value: float | None, caption: str, color: str) -> dbc.Card:
    return dbc.Card(
        [
            html.H2(label, className="metric-label"),
            html.Div(
                ["—" if value is None else f"{value:.2f}", html.Span("°C")],
                className=f"metric-value {color}",
            ),
            html.P(caption, className="metric-caption"),
        ],
        body=True,
    )


def render_snapshot(
    snapshot: Snapshot, *, stale_after_s: float = 3.0
) -> tuple[html.Div, go.Figure]:
    latest = snapshot.latest
    age = max(0.0, monotonic() - latest.sampled_monotonic) if latest else None
    stale = age is not None and age > stale_after_s
    available = bool(latest and latest.status.connected and snapshot.running and not stale)
    connection = "Waiting for the first sample"
    if latest:
        connection = "Last poll: Connected" if latest.status.connected else "Last poll: Unavailable"
    if not snapshot.running:
        freshness = "Monitoring stopped"
    elif stale:
        freshness = "Stale"
    else:
        freshness = "Monitoring"
    if snapshot.logging_error:
        recording = "Failed"
    elif snapshot.logging_enabled:
        recording = "Enabled"
    else:
        recording = "Off"
    caption = f"Sample age {age:.1f} s" if age is not None else "Waiting for a reading"
    if latest and not available:
        caption += " · No fresh reading"
    issues = [
        issue
        for issue in (
            latest.error if latest else "",
            snapshot.service_error,
            snapshot.logging_error,
        )
        if issue
    ]
    status = html.Div(
        [
            html.Div(
                [
                    _reading(
                        "Temperature",
                        latest.temperature_c if latest and available else None,
                        caption,
                        "temperature-color",
                    ),
                    _reading(
                        "Reported setpoint",
                        latest.setpoint_c if latest and available else None,
                        "Device readback" if available else "Waiting for fresh readback",
                        "setpoint-color",
                    ),
                    dbc.Card(
                        [
                            html.H2("Session status", className="metric-label"),
                            html.Div(
                                [
                                    dbc.Badge(
                                        latest.status.backend if latest else "No sample",
                                        color="light",
                                        text_color="dark",
                                    ),
                                    dbc.Badge(
                                        freshness,
                                        color="success" if available else "warning",
                                        text_color="dark",
                                    ),
                                ],
                                className="status-badges",
                            ),
                            html.P(connection, className="connection-status"),
                            html.P(
                                f"CSV {recording} · Rows written {snapshot.logged_samples:,}",
                                className="recording-status",
                            ),
                        ],
                        body=True,
                    ),
                ],
                className="reading-grid",
            ),
            html.Div(
                [
                    html.Span(
                        f"Samples {snapshot.sample_count:,} · Failed polls {snapshot.failed_samples:,}"
                    ),
                    html.Span("Software fault" if issues else "No reported software fault"),
                ],
                className="session-summary",
            ),
            *[dbc.Alert(issue, color="danger", className="fault-message") for issue in issues],
        ]
    )
    times = [sample.timestamp_utc for sample in snapshot.history]
    figure = go.Figure(
        [
            go.Scatter(
                x=times,
                y=[sample.temperature_c for sample in snapshot.history],
                name="Temperature",
                mode="lines",
                connectgaps=False,
                line={"color": "#087f8c", "width": 3},
            ),
            go.Scatter(
                x=times,
                y=[sample.setpoint_c for sample in snapshot.history],
                name="Setpoint",
                mode="lines",
                connectgaps=False,
                line={"color": "#b76b18", "width": 2, "dash": "dash"},
            ),
        ]
    )
    figure.update_traces(hovertemplate="%{y:.2f} °C")
    figure.update_layout(
        template="plotly_white",
        xaxis_title="Time (UTC)",
        yaxis_title="Temperature (°C)",
        margin={"l": 55, "r": 20, "t": 45, "b": 55},
        uirevision="history",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.16, "x": 0},
        height=410,
        font={"family": "Segoe UI, sans-serif", "color": "#304b58"},
    )
    return status, figure


def create_app(chiller: Chiller, monitor: Monitor, *, stale_after_s: float = 3.0) -> Dash:
    """The caller connects the chiller, starts monitoring and owns shutdown."""
    stale_after_s = _seconds(stale_after_s, "stale_after_s")
    app = Dash(
        __name__, meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}]
    )
    app.title = "Tark MRC · Temperature monitor"
    low, high = chiller.setpoint_range
    controls = dbc.Card(
        [
            html.H2("Set temperature", className="panel-heading"),
            html.P(f"{chiller.coolant} · {low:g}–{high:g} °C", className="coolant-profile"),
            dbc.Label("Requested temperature (°C)", html_for="setpoint-input"),
            dbc.InputGroup(
                [
                    dbc.Input(
                        id="setpoint-input",
                        type="number",
                        step="any",
                        placeholder="Enter a setpoint",
                    ),
                    dbc.InputGroupText("°C"),
                ]
            ),
            dbc.Button(
                "Apply setpoint",
                id="apply-setpoint",
                n_clicks=0,
                color="primary",
                className="w-100 mt-3",
            ),
            html.Div(id="setpoint-result", role="status", className="action-result"),
            html.P(
                "Every request is checked against the coolant limits. Readback appears above.",
                className="panel-caption mt-3",
            ),
            html.Hr(),
            html.P(
                "The calling program owns the connection and recording. Closing this page does not stop monitoring.",
                className="panel-caption",
            ),
        ],
        body=True,
    )

    def layout() -> dbc.Container:
        status, figure = render_snapshot(monitor.snapshot(), stale_after_s=stale_after_s)
        return dbc.Container(
            [
                html.Header(
                    [
                        html.Div(
                            [
                                html.P("TARK MRC150 / 300", className="instrument-name"),
                                html.H1("Temperature monitor"),
                                html.P(
                                    "Local laboratory session · Celsius · UTC", className="subtitle"
                                ),
                            ]
                        ),
                        html.Span("LAB CONTROL", className="instrument-mark"),
                    ],
                    className="page-header",
                ),
                html.Div(status, id="live-status", role="status"),
                html.Div(
                    [
                        dbc.Card(
                            [
                                html.H2("Temperature history", className="panel-heading"),
                                html.P(
                                    "Drag to zoom. Double-click to reset. Gaps mark unavailable readings.",
                                    className="panel-caption",
                                ),
                                dcc.Graph(
                                    id="temperature-history",
                                    figure=figure,
                                    config={"displaylogo": False, "responsive": True},
                                ),
                            ],
                            body=True,
                            className="history-panel",
                        ),
                        controls,
                    ],
                    className="instrument-panels",
                ),
                html.Footer(
                    "Coolant presence, leaks, flow, fluid level and alarms: telemetry unavailable. "
                    "Software connection status does not establish physical safety.",
                    className="safety-note",
                ),
                dcc.Interval(id="refresh", interval=1000, n_intervals=0),
            ],
            className="dashboard",
            fluid=True,
        )

    app.layout = layout

    @app.callback(
        Output("live-status", "children"),
        Output("temperature-history", "figure"),
        Input("refresh", "n_intervals"),
    )
    def refresh(_ticks: int) -> tuple[html.Div, go.Figure]:
        return render_snapshot(monitor.snapshot(), stale_after_s=stale_after_s)

    @app.callback(
        Output("setpoint-result", "children"),
        Input("apply-setpoint", "n_clicks"),
        State("setpoint-input", "value"),
        prevent_initial_call=True,
    )
    def apply_setpoint(_clicks: int, value: object) -> str:
        try:
            chiller.set_setpoint(value)
        except (ValueError, OSError, ProtocolError) as exc:
            return f"Rejected / failed: {exc}"
        return "Setpoint request completed; monitor readback will show the reported value."

    return app

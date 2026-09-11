"""Dash presentation; refreshing the page never reads a device or starts a worker."""

from datetime import UTC, datetime
from time import monotonic

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html

from .api import Chiller
from .errors import ChillerError
from .monitoring import LiveSnapshot, LiveState, positive_seconds


def _reading_card(label: str, value: float | None, caption: str, color: str) -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                html.H2(label, className="metric-label"),
                html.Div(
                    ["—" if value is None else f"{value:.2f}", html.Span("°C")],
                    className=f"metric-value {color}",
                ),
                html.P(caption, className="metric-caption"),
            ]
        ),
        className="h-100",
    )


def render_snapshot(
    snapshot: LiveSnapshot,
    *,
    now: datetime | None = None,
    stale_after_s: float = 3.0,
) -> tuple[html.Div, go.Figure]:
    latest = snapshot.latest
    age = None
    if latest:
        if now is None and latest.sampled_monotonic is not None:
            age = max(0.0, monotonic() - latest.sampled_monotonic)
        else:
            age = max(0.0, ((now or datetime.now(UTC)) - latest.timestamp_utc).total_seconds())
    stale = age is not None and age > stale_after_s
    available = bool(latest and latest.status.connected and snapshot.running and not stale)
    connection = "Waiting for the first sample"
    if latest:
        connection = "Last poll: Connected" if latest.status.connected else "Last poll: Unavailable"
    freshness = "Monitoring stopped" if not snapshot.running else "Stale" if stale else "Monitoring"
    recording = (
        "Failed" if snapshot.logging_error else "Enabled" if snapshot.logging_enabled else "Off"
    )
    issues = [
        issue
        for issue in (
            latest.error if latest else None,
            snapshot.service_error,
            snapshot.logging_error,
        )
        if issue
    ]
    caption = f"Sample age {age:.1f} s" if age is not None else "Waiting for a reading"
    if not available and latest:
        caption += " · No fresh reading"
    status = html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        _reading_card(
                            "Temperature",
                            latest.temperature_c if latest and available else None,
                            caption,
                            "temperature-color",
                        ),
                        md=6,
                        lg=4,
                    ),
                    dbc.Col(
                        _reading_card(
                            "Reported setpoint",
                            latest.setpoint_c if latest and available else None,
                            "Read from the device" if available else "Waiting for fresh readback",
                            "setpoint-color",
                        ),
                        md=6,
                        lg=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
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
                                ]
                            ),
                            className="h-100",
                        ),
                        lg=4,
                    ),
                ],
                className="g-3",
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
                hovertemplate="%{y:.2f} °C<extra>Temperature</extra>",
            ),
            go.Scatter(
                x=times,
                y=[sample.setpoint_c for sample in snapshot.history],
                name="Setpoint",
                mode="lines",
                connectgaps=False,
                line={"color": "#b76b18", "width": 2, "dash": "dash"},
                hovertemplate="%{y:.2f} °C<extra>Setpoint</extra>",
            ),
        ]
    )
    figure.update_layout(
        template="plotly_white",
        xaxis_title="Time (UTC)",
        yaxis_title="Temperature (°C)",
        margin={"l": 55, "r": 20, "t": 45, "b": 55},
        uirevision="history",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.16, "x": 0},
        font={"family": "Segoe UI, sans-serif", "color": "#304b58"},
        height=410,
    )
    figure.update_xaxes(showgrid=True, gridcolor="#edf1f3")
    figure.update_yaxes(gridcolor="#edf1f3", zeroline=False)
    return status, figure


def submit_setpoint(chiller: Chiller, value: object) -> str:
    try:
        chiller.set_setpoint(value)
    except ChillerError as exc:
        return f"Rejected / failed: {exc}"
    return "Setpoint request completed; monitor readback will show the reported value."


def create_app(chiller: Chiller, state: LiveState, *, stale_after_s: float = 3.0) -> Dash:
    stale_after_s = positive_seconds(stale_after_s, "stale threshold")
    app = Dash(
        __name__, meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}]
    )
    app.title = "Tark MRC · Temperature monitor"

    def layout() -> dbc.Container:
        status, figure = render_snapshot(state.snapshot(), stale_after_s=stale_after_s)
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
                dbc.Row(
                    [
                        dbc.Col(
                            dbc.Card(
                                dbc.CardBody(
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
                                    ]
                                ),
                                className="h-100 history-panel",
                            ),
                            lg=8,
                        ),
                        dbc.Col(
                            dbc.Card(
                                dbc.CardBody(
                                    [
                                        html.H2("Set temperature", className="panel-heading"),
                                        html.P(
                                            f"{chiller.coolant.name} · {chiller.coolant.minimum_c:g}–{chiller.coolant.maximum_c:g} °C",
                                            className="coolant-profile",
                                        ),
                                        dbc.Label(
                                            "Requested temperature (°C)", html_for="setpoint-input"
                                        ),
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
                                        html.Div(
                                            id="setpoint-result",
                                            role="status",
                                            className="action-result",
                                        ),
                                        html.P(
                                            "Every request is checked against the coolant limits. Readback appears above.",
                                            className="panel-caption mt-3",
                                        ),
                                        html.Hr(),
                                        html.H2("Connection", className="panel-heading"),
                                        html.Div(
                                            [
                                                dbc.Button(
                                                    "Connect / retry",
                                                    id="connect-device",
                                                    n_clicks=0,
                                                    outline=True,
                                                    color="secondary",
                                                ),
                                                dbc.Button(
                                                    "Disconnect",
                                                    id="disconnect-device",
                                                    n_clicks=0,
                                                    outline=True,
                                                    color="secondary",
                                                ),
                                            ],
                                            className="connection-actions",
                                        ),
                                        html.Div(
                                            id="connection-result",
                                            role="status",
                                            className="action-result",
                                        ),
                                        html.P(
                                            "Close the terminal session with Ctrl+C to stop monitoring.",
                                            className="panel-caption mt-3",
                                        ),
                                    ]
                                ),
                                className="h-100",
                            ),
                            lg=4,
                        ),
                    ],
                    className="g-3",
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
        return render_snapshot(state.snapshot(), stale_after_s=stale_after_s)

    @app.callback(
        Output("setpoint-result", "children"),
        Input("apply-setpoint", "n_clicks"),
        State("setpoint-input", "value"),
        prevent_initial_call=True,
    )
    def apply_setpoint(_clicks: int, value: object) -> str:
        return submit_setpoint(chiller, value)

    @app.callback(
        Output("connection-result", "children"),
        Input("connect-device", "n_clicks"),
        Input("disconnect-device", "n_clicks"),
        prevent_initial_call=True,
    )
    def connection_action(_connects: int, _disconnects: int) -> str:
        try:
            if ctx.triggered_id == "connect-device":
                chiller.connect()
                if not state.snapshot().running:
                    return "Connected; monitoring is stopped. Restart the monitoring service."
                return "Connected; monitoring will publish fresh readings."
            chiller.disconnect()
            return "Disconnected; monitoring continues and will record unavailable readings."
        except ChillerError as exc:
            return f"Connection action failed: {exc}"

    return app

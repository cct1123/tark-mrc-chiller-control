"""Dash presentation only: refresh never performs acquisition or starts a worker."""

from datetime import UTC, datetime
from time import monotonic

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html

from .api import Chiller
from .errors import ChillerError
from .monitoring import LiveSnapshot, LiveState, positive_seconds


def render_snapshot(
    snapshot: LiveSnapshot,
    *,
    now: datetime | None = None,
    stale_after_s: float = 3.0,
) -> tuple[str, go.Figure]:
    latest = snapshot.latest
    text = "Waiting for the first sample"
    if latest:
        if now is None and latest.sampled_monotonic is not None:
            age = max(0.0, monotonic() - latest.sampled_monotonic)
        else:
            age = max(0.0, ((now or datetime.now(UTC)) - latest.timestamp_utc).total_seconds())
        condition = "Last poll: Connected" if latest.status.connected else "Last poll: Unavailable"
        if not snapshot.running:
            condition += " · Monitoring stopped"
        elif age > stale_after_s:
            condition += " · Stale"
        temperature = "—" if latest.temperature_c is None else f"{latest.temperature_c:.2f} °C"
        setpoint = "—" if latest.setpoint_c is None else f"{latest.setpoint_c:.2f} °C"
        text = (
            f"{latest.status.backend} · {condition} · Sample age {age:.1f} s\n"
            f"Temperature {temperature} · Setpoint {setpoint}\n"
            f"{latest.error or latest.status.detail}"
        )
    recording = "Enabled" if snapshot.logging_enabled else "Off"
    if snapshot.logging_error:
        recording = "Failed"
    text += (
        f"\nMonitor {'running' if snapshot.running else 'stopped'} · "
        f"Samples {snapshot.sample_count} · Failed polls {snapshot.failed_samples}\n"
        f"CSV {recording} · Rows written {snapshot.logged_samples}"
    )
    for issue in (snapshot.service_error, snapshot.logging_error):
        if issue:
            text += f"\n{issue}"
    times = [s.timestamp_utc for s in snapshot.history]
    figure = go.Figure(
        [
            go.Scatter(
                x=times,
                y=[s.temperature_c for s in snapshot.history],
                name="Temperature",
                mode="lines",
                connectgaps=False,
                line={"color": "#087f8c", "width": 3},
            ),
            go.Scatter(
                x=times,
                y=[s.setpoint_c for s in snapshot.history],
                name="Setpoint",
                mode="lines",
                connectgaps=False,
                line={"color": "#c46b20", "dash": "dash"},
            ),
        ]
    )
    figure.update_layout(
        template="plotly_white",
        xaxis_title="Time (UTC)",
        yaxis_title="Temperature (°C)",
        margin={"l": 60, "r": 25, "t": 30, "b": 55},
        uirevision="history",
        legend={"orientation": "h", "y": 1.12},
        height=430,
    )
    return text, figure


def submit_setpoint(chiller: Chiller, value: object) -> str:
    try:
        chiller.set_setpoint(value)
    except ChillerError as exc:
        return f"Rejected / failed: {exc}"
    return "Setpoint request completed; monitor readback will show the reported value."


def create_app(chiller: Chiller, state: LiveState, *, stale_after_s: float = 3.0) -> Dash:
    stale_after_s = positive_seconds(stale_after_s, "stale threshold")
    app = Dash(__name__)
    app.title = "Tark MRC · Temperature monitor"
    panel = {
        "background": "#fff",
        "padding": "24px",
        "borderRadius": "12px",
        "border": "1px solid #dce4e7",
        "marginBottom": "20px",
    }
    app.layout = html.Main(
        [
            html.P("TARK MRC150 / 300", style={"letterSpacing": "2px", "color": "#087f8c"}),
            html.H1("Temperature monitor", style={"marginTop": "0"}),
            html.P("Live acquisition · Celsius · Local session", style={"color": "#52616b"}),
            html.Section(
                [
                    html.H2("Live status", style={"fontSize": "18px"}),
                    html.Div(
                        id="live-status", style={"whiteSpace": "pre-line", "lineHeight": "1.8"}
                    ),
                    html.Button("Connect / retry", id="connect-device", n_clicks=0),
                    html.Button(
                        "Disconnect",
                        id="disconnect-device",
                        n_clicks=0,
                        style={"marginLeft": "12px"},
                    ),
                    html.Div(id="connection-result", role="status", style={"marginTop": "10px"}),
                    html.P(
                        "Coolant presence, leaks, flow, fluid level and alarms: telemetry unavailable.",
                        style={"fontSize": "13px", "color": "#52616b"},
                    ),
                ],
                style=panel,
            ),
            html.Section(
                [dcc.Graph(id="temperature-history", config={"displaylogo": False})], style=panel
            ),
            html.Section(
                [
                    html.H2("Temperature setpoint", style={"fontSize": "18px"}),
                    html.P(
                        f"Profile: {chiller.coolant.name} · "
                        f"{chiller.coolant.minimum_c:g}–{chiller.coolant.maximum_c:g} °C"
                    ),
                    html.Label("Requested temperature (°C)", htmlFor="setpoint-input"),
                    html.Div(
                        [
                            dcc.Input(
                                id="setpoint-input",
                                type="number",
                                placeholder="Enter °C",
                                style={"padding": "10px", "marginRight": "12px"},
                            ),
                            html.Button(
                                "Apply setpoint",
                                id="apply-setpoint",
                                n_clicks=0,
                                style={
                                    "padding": "10px 18px",
                                    "background": "#087f8c",
                                    "color": "white",
                                    "border": "none",
                                    "borderRadius": "6px",
                                },
                            ),
                        ],
                        style={"marginTop": "10px"},
                    ),
                    html.Div(id="setpoint-result", role="status", style={"marginTop": "14px"}),
                ],
                style=panel,
            ),
            dcc.Interval(id="refresh", interval=1000, n_intervals=0),
        ],
        style={
            "fontFamily": "Segoe UI, sans-serif",
            "maxWidth": "1040px",
            "margin": "32px auto",
            "padding": "0 20px",
            "color": "#18313d",
        },
    )

    @app.callback(
        Output("live-status", "children"),
        Output("temperature-history", "figure"),
        Input("refresh", "n_intervals"),
    )
    def refresh(_ticks: int) -> tuple[str, go.Figure]:
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

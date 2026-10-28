"""Dashboard markup and snapshot rendering; no hardware I/O or callbacks."""

from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Literal

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import html

from .controller import TarkChiller, _Sample, _Snapshot
from .protocols import _StatusFields


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


def render_status(
    snapshot: _Snapshot, *, stale_after_s: float = 3.0
) -> tuple[html.Div, html.Div, Literal["RUN", "STANDBY"] | None]:
    """Return session/equipment panels and the available mode action; no hardware I/O."""
    history = snapshot["history"]
    latest = history[-1] if history else None
    age = None
    if latest:
        completed_at = latest["completed_monotonic"]
        age = max(0.0, monotonic() - completed_at)
    stopped = not snapshot["running"]
    recovery = ""
    if not stopped and not snapshot["service_error"]:
        recovery = snapshot["recovery_error"]
    stale = age is not None and age > stale_after_s

    if stopped:
        state = "Monitoring stopped"
    elif snapshot["service_error"]:
        state = "Monitoring fault"
    elif recovery or (latest and latest["error"]):
        state = "Recovering"
    elif not latest:
        state = "Waiting for first poll"
    elif stale:
        state = "Waiting for device" if snapshot["poll_in_progress"] else "Stale"
    else:
        state = "Monitoring"
    live = bool(latest and latest["status"]["connected"] and state == "Monitoring")
    caption = f"Last poll completed {age:.1f} s ago" if age is not None else "Waiting for a reading"
    if latest and not live:
        caption += " · No fresh reading"
        if latest["error"] and latest["temperature_c"] is not None:
            caption += f" · Last validated temperature {latest['temperature_c']:.2f} °C"

    issues = [snapshot["service_error"], snapshot["logging_error"]]
    if latest and latest["error"] not in snapshot["service_error"]:
        issues.append(latest["error"])
    issues = list(dict.fromkeys(issue for issue in issues if issue))
    if snapshot["logging_error"]:
        recording = "Failed"
    elif snapshot["logging_enabled"]:
        recording = "Enabled"
    else:
        recording = "Off"
    if not latest:
        connection = "Waiting for the first sample"
    elif latest["status"]["connected"]:
        connection = "Last poll: Connected"
    else:
        connection = "Last poll: Unavailable"
    session = dbc.Card(
        [
            html.H2("Session status", className="metric-label"),
            html.Div(
                [
                    dbc.Badge(
                        latest["status"]["backend"] if latest else "No sample",
                        color="light",
                        text_color="dark",
                    ),
                    dbc.Badge(state, color="success" if live else "warning", text_color="dark"),
                ],
                className="status-badges",
            ),
            html.P(connection, className="connection-status"),
            html.P(
                "Poll started: " + latest["timestamp_utc"].strftime("%Y-%m-%d %H:%M:%S UTC")
                if latest
                else "Poll started: Unavailable",
                id="poll-started-utc",
                className="poll-timestamp",
            ),
            html.P(
                f"CSV {recording} · Rows written {snapshot['logged_samples']:,}",
                className="recording-status",
            ),
        ],
        body=True,
    )
    alerts = [dbc.Alert(issue, color="danger", className="fault-message") for issue in issues]
    if recovery:
        alerts.insert(0, dbc.Alert(f"Recovering communication: {recovery}", color="warning"))
    # Render structured fields from a fresh completed poll; never parse detail
    # text or issue commands during a browser refresh. Missing is not clear.
    telemetry: _StatusFields = latest["status"] if latest and live else {}
    chiller_on = telemetry.get("chiller_on")
    mode_action: Literal["RUN", "STANDBY"] | None = None
    if type(chiller_on) is bool:
        mode_action = "STANDBY" if chiller_on else "RUN"
    equipment_fields: list[tuple[str, str]] = [("Mode", telemetry.get("mode", "Unavailable"))]
    for label, key in (
        ("Chiller", "chiller_on"),
        ("Dryer", "dryer_on"),
        ("Alarm summary", "summary_alarm"),
    ):
        value = telemetry.get(key)
        reported = "Unavailable"
        if type(value) is bool:
            reported = (
                ("Active" if value else "Clear")
                if key == "summary_alarm"
                else ("On" if value else "Off")
            )
        equipment_fields.append((label, reported))
    alarm_fields = []
    reported_alarms = telemetry.get("alarms", {})
    alarm_definitions = (
        ("float_switch", "Coolant level alarm"),
        ("high_temp", "High temperature alarm"),
        ("low_temp", "Low temperature alarm"),
        ("sensor", "Temperature sensor alarm"),
        ("eeprom", "EEPROM alarm"),
        ("watchdog", "Serial watchdog alarm"),
    )
    for key, label in alarm_definitions:
        active = reported_alarms.get(key)
        alarm_fields.append(
            html.Div(
                [
                    html.Span(label),
                    dbc.Badge(
                        "Active"
                        if active is True
                        else "Clear"
                        if active is False
                        else "Unavailable",
                        color="danger" if active is True else "light",
                        text_color="white" if active is True else "dark",
                    ),
                ],
                id=f"alarm-{key}",
                className="alarm-flag",
            )
        )
    flags_known = all(type(reported_alarms.get(key)) is bool for key, _ in alarm_definitions)
    active_count = sum(reported_alarms.get(key) is True for key, _ in alarm_definitions)
    equipment = dbc.Card(
        [
            html.Div(
                [
                    html.H2("Equipment status", className="panel-heading"),
                    dbc.Badge(
                        f"{active_count} active alarm{'s' if active_count != 1 else ''}"
                        if flags_known
                        else "Alarm states unavailable",
                        id="equipment-alarm-count",
                        color="danger" if flags_known and active_count else "light",
                        text_color="white" if flags_known and active_count else "dark",
                    ),
                ],
                className="equipment-heading",
            ),
            html.Div(
                [
                    html.Div(
                        [html.Span(label), html.Strong(value)],
                        className="equipment-field alarm-active"
                        if label == "Alarm summary" and value == "Active"
                        else "equipment-field",
                    )
                    for label, value in equipment_fields
                ],
                className="equipment-readings",
            ),
            html.H3("Reported alarm states", className="metric-label mt-3"),
            html.Div(alarm_fields, className="alarm-flags"),
            html.P(
                "Clear: no alarm reported. Active: alarm triggered.",
                className="panel-caption mt-3",
            ),
            html.P(
                "Last completed poll; physical flow is not measured."
                if "mode" in telemetry
                else "Structured mode and alarm telemetry unavailable for this protocol."
                if live
                else "Mode and alarm telemetry unavailable; wait for fresh monitoring readings.",
                className="panel-caption mt-3",
            ),
        ],
        id="equipment-status",
        body=True,
        className="equipment-status",
    )
    status = html.Div(
        [
            html.Div(
                [
                    _reading(
                        "Temperature",
                        latest["temperature_c"] if latest and live else None,
                        caption,
                        "temperature-color",
                    ),
                    _reading(
                        "Reported setpoint",
                        latest["setpoint_c"] if latest and live else None,
                        "Device readback" if live else "Waiting for fresh readback",
                        "setpoint-color",
                    ),
                    session,
                ],
                className="reading-grid",
            ),
            html.Div(
                [
                    html.Span(
                        f"Samples {snapshot['sample_count']:,} · Failed polls {snapshot['failed_samples']:,}"
                    ),
                    html.Span("Software fault" if issues else "No reported software fault"),
                    html.Span(
                        f"Monitoring elapsed {latest['elapsed_s']:.1f} s (last poll)"
                        if latest
                        else "Monitoring elapsed: Unavailable",
                        id="monitor-elapsed",
                    ),
                ],
                className="session-summary",
            ),
            *alerts,
        ]
    )
    equipment_panel = html.Div(
        [
            equipment,
            dbc.Accordion(
                [
                    dbc.AccordionItem(
                        html.P(latest["status"]["detail"], className="panel-caption"),
                        title="Controller diagnostics (last poll)",
                        item_id="last-poll",
                    ),
                ],
                id="controller-diagnostics",
                className="controller-diagnostics",
                start_collapsed=True,
                persistence=True,
                persistence_type="memory",
                flush=True,
            )
            if latest
            else None,
        ]
    )
    return status, equipment_panel, mode_action


def render_history(history: list[_Sample]) -> go.Figure:
    """Plot sequential readings; missing values remain gaps in each trace."""
    timestamps = [sample["timestamp_utc"] for sample in history]
    figure = go.Figure(
        [
            go.Scatter(
                x=timestamps,
                y=[sample["temperature_c"] for sample in history],
                name="Temperature",
                mode="lines",
                connectgaps=False,
                line={"color": "#087f8c", "width": 3},
            ),
            go.Scatter(
                x=timestamps,
                y=[sample["setpoint_c"] for sample in history],
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
    return figure


def _build_controls(
    chiller: TarkChiller,
    *,
    modes_enabled: bool,
    mode_action: Literal["RUN", "STANDBY"] | None,
    allow_setpoints: bool,
    interval_s: float,
    csv_path: str | Path | None,
    on_shutdown: Callable[[], None] | None,
) -> tuple[html.Div, html.Div, html.Div]:
    """Build header, figure and equipment controls; callbacks enforce action policy."""
    low, high = chiller.setpoint_range
    header_actions = html.Div(
        [
            html.Div(
                [
                    dbc.Button("Connect", id="connect-device", n_clicks=0, color="primary"),
                    dbc.Button(
                        "Shutdown GUI",
                        id="shutdown-gui",
                        n_clicks=0,
                        color="danger",
                        outline=True,
                        disabled=on_shutdown is None,
                    ),
                ],
                className="header-buttons",
            ),
            html.P(f"Port: {chiller.settings['port']}", className="panel-caption"),
        ],
        className="header-actions",
    )
    monitoring = html.Div(
        [
            html.Div(
                [
                    dbc.Button(
                        "Stop monitoring" if chiller.running else "Start monitoring",
                        id="toggle-monitoring",
                        n_clicks=0,
                        color="primary",
                    ),
                    html.Div(
                        [
                            dbc.Label("Poll interval (s)", html_for="monitor-interval"),
                            dbc.Input(
                                id="monitor-interval",
                                type="number",
                                min=0.01,
                                step="any",
                                value=interval_s,
                            ),
                        ],
                    ),
                    html.Div(
                        [
                            dbc.Label(
                                "CSV path (optional; use a new path for each run)",
                                html_for="monitor-csv",
                            ),
                            dbc.Input(
                                id="monitor-csv",
                                type="text",
                                value=str(csv_path) if csv_path else "",
                                placeholder="Leave blank for no recording",
                            ),
                        ],
                    ),
                ],
                className="monitor-controls",
            ),
            html.Div(
                "Connect, then start monitoring. Reloading this page does not connect.",
                id="session-result",
                role="status",
                className="action-result",
            ),
            html.P(
                "Connect takes serial control and may trigger T255P AUTO START. "
                "Shutdown stops monitoring and releases the port; the chiller/pump may keep running. "
                "Closing this page does not stop monitoring.",
                className="panel-caption mt-3",
            ),
            html.P(
                "" if on_shutdown else "Embedded host: stop the GUI from its calling program.",
                className="panel-caption",
            ),
        ],
        className="monitor-settings",
    )
    mode = dbc.Card(
        [
            html.H2("Pump and temperature control", className="panel-heading"),
            dbc.Button(
                mode_action or "Mode unavailable",
                id="toggle-mode",
                n_clicks=0,
                color="primary",
                className="w-100 mt-2",
                disabled=not modes_enabled or mode_action is None,
            ),
            html.Div(id="mode-result", role="status", className="action-result"),
        ],
        body=True,
    )
    temperature = dbc.Card(
        [
            html.H2("Set temperature", className="panel-heading"),
            dbc.Label(
                "Requested temperature (°C)", html_for="setpoint-input", className="visually-hidden"
            ),
            html.Div(
                [
                    dbc.Input(
                        id="setpoint-input",
                        type="number",
                        min=low,
                        max=high,
                        step=0.1,
                        placeholder="Temperature (°C)",
                        disabled=not allow_setpoints,
                    ),
                    dbc.Button(
                        "Apply",
                        id="apply-setpoint",
                        n_clicks=0,
                        color="primary",
                        disabled=not allow_setpoints,
                        title=""
                        if allow_setpoints
                        else "Read-only session; target changes disabled.",
                    ),
                ],
                className="setpoint-controls mt-2",
                title=f"{chiller.coolant}: {low:g}–{high:g} °C",
            ),
            html.Div(id="setpoint-result", role="status", className="action-result"),
        ],
        body=True,
    )
    return header_actions, monitoring, html.Div([mode, temperature], className="control-panels")

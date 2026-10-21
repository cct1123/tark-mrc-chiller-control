"""Snapshot dashboard with explicit connection, monitoring and shutdown controls."""

from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html
from werkzeug.serving import make_server

from .controller import ProtocolError, TarkChiller, _seconds


def _reading(label: str, value: float | None, caption: str, color: str) -> dbc.Card:
    return dbc.Card([
        html.H2(label, className="metric-label"),
        html.Div(["—" if value is None else f"{value:.2f}", html.Span("°C")],
                 className=f"metric-value {color}"),
        html.P(caption, className="metric-caption"),
    ], body=True)


def render_snapshot(
    snapshot: dict[str, Any], *, stale_after_s: float = 3.0
) -> tuple[html.Div, go.Figure]:
    history = snapshot["history"]
    latest = history[-1] if history else None
    age = (max(0.0, monotonic() - latest.get("completed_monotonic", latest["sampled_monotonic"]))
           if latest else None)
    stopped = not snapshot["running"]
    recovery = snapshot.get("recovery_error", "") if not stopped and not snapshot["service_error"] else ""
    stale = age is not None and age > stale_after_s

    if stopped:
        state = "Monitoring stopped"
    elif snapshot["service_error"]:
        state = "Monitoring fault"
    elif recovery or (latest and latest["error"]):
        state = "Recovering"
    elif stale:
        state = "Waiting for device" if snapshot.get("poll_in_progress") else "Stale"
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
    recording = ("Failed" if snapshot["logging_error"] else
                 "Enabled" if snapshot["logging_enabled"] else "Off")
    connection = ("Last poll: Connected" if latest["status"]["connected"] else
                  "Last poll: Unavailable") if latest else "Waiting for the first sample"
    session = dbc.Card([
        html.H2("Session status", className="metric-label"),
        html.Div([
            dbc.Badge(latest["status"]["backend"] if latest else "No sample",
                      color="light", text_color="dark"),
            dbc.Badge(state, color="success" if live else "warning", text_color="dark"),
        ], className="status-badges"),
        html.P(connection, className="connection-status"),
        html.P(f"CSV {recording} · Rows written {snapshot['logged_samples']:,}",
               className="recording-status"),
    ], body=True)
    alerts = [dbc.Alert(issue, color="danger", className="fault-message") for issue in issues]
    if recovery:
        alerts.insert(0, dbc.Alert(f"Recovering communication: {recovery}", color="warning"))
    status = html.Div([
        html.Div([
            _reading("Temperature", latest["temperature_c"] if latest and live else None,
                     caption, "temperature-color"),
            _reading("Reported setpoint", latest["setpoint_c"] if latest and live else None,
                     "Device readback" if live else "Waiting for fresh readback", "setpoint-color"),
            session,
        ], className="reading-grid"),
        html.Div([
            html.Span(f"Samples {snapshot['sample_count']:,} · Failed polls {snapshot['failed_samples']:,}"),
            html.Span("Software fault" if issues else "No reported software fault"),
        ], className="session-summary"),
        html.P(latest["status"]["detail"] if latest else "", className="panel-caption"),
        *alerts,
    ])
    figure = go.Figure([
        go.Scatter(x=[sample["timestamp_utc"] for sample in history],
                   y=[sample[field] for sample in history], name=name, mode="lines",
                   connectgaps=False, line=line)
        for field, name, line in (
            ("temperature_c", "Temperature", {"color": "#087f8c", "width": 3}),
            ("setpoint_c", "Setpoint", {"color": "#b76b18", "width": 2, "dash": "dash"}),
        )
    ])
    figure.update_traces(hovertemplate="%{y:.2f} °C")
    figure.update_layout(
        template="plotly_white", xaxis_title="Time (UTC)", yaxis_title="Temperature (°C)",
        margin={"l": 55, "r": 20, "t": 45, "b": 55}, uirevision="history", hovermode="x unified",
        legend={"orientation": "h", "y": 1.16, "x": 0}, height=410,
        font={"family": "Segoe UI, sans-serif", "color": "#304b58"},
    )
    return status, figure


def create_app(
    chiller: TarkChiller, *, stale_after_s: float = 3.0, allow_setpoints: bool = True,
    allow_mode_changes: bool | None = None,
    interval_s: float = 1.0, csv_path: str | Path | None = None, history_size: int = 3600,
    on_shutdown: Callable[[], None] | None = None,
) -> Dash:
    """Creating/reloading the app does no I/O; buttons make explicit requests.

    Use run_dashboard() for a standalone server with an enabled shutdown button.
    Embedded hosts may supply their own thread-safe on_shutdown hook.
    """
    stale_after_s = _seconds(stale_after_s, "stale_after_s")
    interval_s = _seconds(interval_s, "interval_s")
    if type(history_size) is not int or history_size <= 0:
        raise ValueError("history_size must be a positive integer")
    if type(allow_setpoints) is not bool:
        raise ValueError("allow_setpoints must be boolean")
    if allow_mode_changes is None:
        allow_mode_changes = allow_setpoints
    if type(allow_mode_changes) is not bool:
        raise ValueError("allow_mode_changes must be boolean")
    modes_enabled = allow_mode_changes and chiller.supports_mode_control
    app = Dash(__name__, meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}])
    app.title = "Chiller · Temperature monitor"
    action_lock = Lock()
    shut_down = Event()
    low, high = chiller.setpoint_range
    controls = dbc.Card([
        html.H2("Session controls", className="panel-heading"),
        html.P(f"Port: {chiller.settings['port']}", className="panel-caption"),
        html.Div([
            dbc.Button("Connect", id="connect-device", n_clicks=0, color="primary"),
            dbc.Button("Stop monitoring" if chiller.running else "Start monitoring",
                       id="toggle-monitoring", n_clicks=0, color="primary"),
            dbc.Button("Shutdown GUI", id="shutdown-gui", n_clicks=0,
                       color="danger", outline=True, disabled=on_shutdown is None),
        ], className="session-buttons"),
        dbc.Label("Poll interval (s)", html_for="monitor-interval", className="mt-3"),
        dbc.Input(id="monitor-interval", type="number", min=0.01, step="any", value=interval_s),
        dbc.Label("CSV path (optional; use a new path for each run)", html_for="monitor-csv",
                  className="mt-3"),
        dbc.Input(id="monitor-csv", type="text", value=str(csv_path) if csv_path else "",
                  placeholder="Leave blank for no recording"),
        html.Div("Connect, then start monitoring. Reloading this page does not connect.",
                 id="session-result", role="status", className="action-result"),
        html.P("Connect takes serial control and may trigger T255P AUTO START. "
               "Shutdown stops monitoring and releases the port; the chiller/pump may keep running.",
               className="panel-caption mt-3"),
        html.P("" if on_shutdown else "Embedded host: stop the GUI from its calling program.",
               className="panel-caption"),
        html.Hr(),
        html.H2("Pump and temperature control", className="panel-heading"),
        html.Div([
            dbc.Button("RUN", id="run-device", n_clicks=0, color="primary",
                       disabled=not modes_enabled),
            dbc.Button("STANDBY", id="standby-device", n_clicks=0, color="secondary",
                       outline=True, disabled=not modes_enabled),
        ], className="session-buttons"),
        html.P("RUN starts circulation and temperature regulation at the existing target. "
               "STANDBY stops both; monitoring continues.", className="panel-caption mt-3"),
        html.P("" if modes_enabled else "Mode changes disabled; T255P write access is required.",
               className="panel-caption"),
        html.Div(id="mode-result", role="status", className="action-result"),
        html.Hr(),
        html.H2("Set temperature", className="panel-heading"),
        html.P(f"{chiller.coolant} · {low:g}–{high:g} °C", className="coolant-profile"),
        dbc.Label("Requested temperature (°C)", html_for="setpoint-input"),
        dbc.InputGroup([
            dbc.Input(id="setpoint-input", type="number", min=low, max=high, step=0.1,
                      placeholder="Enter a setpoint"),
            dbc.InputGroupText("°C"),
        ]),
        dbc.Button("Apply setpoint", id="apply-setpoint", n_clicks=0, color="primary",
                   className="w-100 mt-3", disabled=not allow_setpoints),
        html.P("" if allow_setpoints else "Read-only session; target changes disabled.",
               className="panel-caption"),
        html.Div(id="setpoint-result", role="status", className="action-result"),
        html.P("Every request is checked against the coolant limits. Readback appears above.",
               className="panel-caption mt-3"),
        html.Hr(),
        html.P("Closing this page does not stop monitoring.", className="panel-caption"),
    ], body=True)

    def layout() -> dbc.Container:
        status, figure = render_snapshot(chiller.snapshot(), stale_after_s=stale_after_s)
        header = html.Header([
            html.Div([
                html.P("LABORATORY CHILLER", className="instrument-name"),
                html.H1("Temperature monitor"),
                html.P("Local laboratory session · Celsius · UTC", className="subtitle"),
            ]),
            html.Span("LAB CONTROL", className="instrument-mark"),
        ], className="page-header")
        chart = dbc.Card([
            html.H2("Temperature history", className="panel-heading"),
            html.P("Drag to zoom. Double-click to reset. Gaps mark unavailable readings.",
                   className="panel-caption"),
            dcc.Graph(id="temperature-history", figure=figure,
                      config={"displaylogo": False, "responsive": True}),
        ], body=True, className="history-panel")
        return dbc.Container([
            header, html.Div(status, id="live-status", role="status"),
            html.Div([chart, controls], className="instrument-panels"),
            html.Footer(
                "Physical flow/leak telemetry unavailable; verify coolant and setup independently. "
                "Reported controller/alarm flags are diagnostic only. "
                "Software connection status does not establish physical safety.",
                className="safety-note",
            ),
            dcc.Interval(id="refresh", interval=1000, n_intervals=0),
        ], className="dashboard", fluid=True)

    app.layout = layout

    @app.callback(Output("live-status", "children"), Output("temperature-history", "figure"),
                  Output("toggle-monitoring", "children"),
                  Input("refresh", "n_intervals"))
    def refresh(_ticks: int) -> tuple[html.Div, go.Figure, str]:
        snapshot = chiller.snapshot()
        status, figure = render_snapshot(snapshot, stale_after_s=stale_after_s)
        return status, figure, "Stop monitoring" if snapshot["running"] else "Start monitoring"

    @app.callback(Output("setpoint-result", "children"), Input("apply-setpoint", "n_clicks"),
                  State("setpoint-input", "value"), prevent_initial_call=True)
    def apply_setpoint(_clicks: int, value: object) -> str:
        if not allow_setpoints:
            return "Rejected: read-only session; target changes disabled."
        if not action_lock.acquire(blocking=False):
            return "Another session action is in progress; wait for it to finish."
        try:
            if shut_down.is_set():
                return "Rejected: dashboard has shut down."
            chiller.set_setpoint(value)
        except (ValueError, OSError, ProtocolError) as exc:
            return f"Rejected / failed: {exc}"
        finally:
            action_lock.release()
        return "Setpoint request completed; monitor readback will show the reported value."

    @app.callback(Output("mode-result", "children"),
                  Input("run-device", "n_clicks"), Input("standby-device", "n_clicks"),
                  prevent_initial_call=True)
    def apply_mode(_run: int, _standby: int) -> str:
        if not modes_enabled:
            return "Rejected: mode changes disabled or protocol unsupported."
        if not action_lock.acquire(blocking=False):
            return "Another session action is in progress; wait for it to finish."
        try:
            if shut_down.is_set():
                return "Rejected: dashboard has shut down."
            if ctx.triggered_id == "run-device":
                chiller.run()
                mode = "RUN"
            elif ctx.triggered_id == "standby-device":
                chiller.standby()
                mode = "STANDBY"
            else:
                return "Choose RUN or STANDBY."
        except (ValueError, OSError, ProtocolError) as exc:
            return f"Rejected / failed: {exc}"
        finally:
            action_lock.release()
        return f"{mode} verified by controller readback. Check physical flow and panel separately."

    @app.callback(
        Output("session-result", "children"), Output("refresh", "disabled"),
        Output("live-status", "children", allow_duplicate=True),
        Output("toggle-monitoring", "children", allow_duplicate=True),
        Input("connect-device", "n_clicks"), Input("toggle-monitoring", "n_clicks"),
        Input("shutdown-gui", "n_clicks"),
        State("monitor-interval", "value"), State("monitor-csv", "value"),
        prevent_initial_call=True,
    )
    def session_action(
        _connect: int, _monitor: int, _shutdown: int,
        interval: object, path: str | None,
    ) -> tuple[str, bool, html.Div, str]:
        message, stopped = perform_session_action(interval, path)
        snapshot = chiller.snapshot()
        status, _figure = render_snapshot(snapshot, stale_after_s=stale_after_s)
        return message, stopped, status, "Stop monitoring" if snapshot["running"] else "Start monitoring"

    def perform_session_action(interval: object, path: str | None) -> tuple[str, bool]:
        if not action_lock.acquire(blocking=False):
            return "Another session action is in progress; wait for it to finish.", shut_down.is_set()
        try:
            if shut_down.is_set():
                return "Dashboard has shut down. You can close this page.", True
            action = ctx.triggered_id
            if action == "connect-device":
                if chiller.is_connected:
                    return "Already connected.", False
                chiller.connect()
                return "Connected. Press Start monitoring to acquire readings.", False
            if action == "toggle-monitoring":
                if chiller.running:
                    chiller.stop_monitoring()
                    return "Monitoring stopped; connection remains open.", False
                chiller.start_monitoring(interval_s=_seconds(interval, "Poll interval"), csv_path=path or None,
                                         history_size=history_size)
                return "Monitoring started. Each new run resets the displayed history.", False
            if action == "shutdown-gui":
                if on_shutdown is None:
                    return "Shutdown unavailable: stop the GUI from its calling program.", False
                chiller.disconnect()
                on_shutdown()
                shut_down.set()
                return "GUI shut down; monitoring stopped and port released. You can close this page.", True
            return "Choose a session action.", False
        except (ValueError, OSError, ProtocolError, RuntimeError) as exc:
            return f"Action failed: {exc}", False
        finally:
            action_lock.release()

    return app


def run_dashboard(
    chiller: TarkChiller, *, host: str = "127.0.0.1", port: int = 8056,
    stale_after_s: float = 3.0, allow_setpoints: bool = True,
    allow_mode_changes: bool | None = None,
    interval_s: float = 1.0, csv_path: str | Path | None = None, history_size: int = 3600,
) -> None:
    """Serve until Shutdown GUI or Ctrl+C, then release acquisition and serial I/O.

    The controller is left disconnected at startup unless the caller connected it.
    No reloader is used. Shutdown never commands the physical chiller into standby.
    """
    def shutdown() -> None:
        # BaseServer.shutdown must run outside its serve_forever thread.
        Thread(target=server.shutdown, name="dashboard-shutdown", daemon=True).start()

    app = create_app(chiller, stale_after_s=stale_after_s, allow_setpoints=allow_setpoints,
                     allow_mode_changes=allow_mode_changes,
                     interval_s=interval_s, csv_path=csv_path, history_size=history_size,
                     on_shutdown=shutdown)
    server = make_server(host, port, app.server, threaded=True)
    try:
        server.serve_forever()
    except BaseException as error:
        try:
            chiller.disconnect()
        except BaseException as cleanup:
            error.add_note(f"Dashboard disconnect cleanup failed: {cleanup}")
        raise
    else:
        chiller.disconnect()
    finally:
        server.server_close()

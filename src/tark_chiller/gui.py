"""Snapshot dashboard with explicit connection, monitoring and shutdown controls."""

from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Literal

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html
from werkzeug.serving import make_server

from . import _views, protocols
from .controller import TarkChiller, _seconds


def create_app(
    chiller: TarkChiller,
    *,
    stale_after_s: float = 3.0,
    allow_setpoints: bool = True,
    allow_mode_changes: bool | None = None,
    interval_s: float = 1.0,
    csv_path: str | Path | None = None,
    history_size: int = 3600,
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
    app = Dash(
        __name__, meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}]
    )
    app.title = "Chiller · Temperature monitor"
    action_lock = Lock()
    shut_down = Event()

    def layout() -> dbc.Container:
        snapshot = chiller.snapshot()
        status, equipment, mode = _views.render_status(snapshot, stale_after_s=stale_after_s)
        header_actions, monitoring, controls = _views._build_controls(
            chiller,
            modes_enabled=modes_enabled,
            mode_action=mode,
            allow_setpoints=allow_setpoints,
            interval_s=interval_s,
            csv_path=csv_path,
            on_shutdown=on_shutdown,
        )
        figure = _views.render_history(snapshot["history"])
        header = html.Header(
            [
                html.Div(
                    [
                        html.P("LABORATORY CHILLER", className="instrument-name"),
                        html.H1("Temperature monitor"),
                        html.P("Local laboratory session · Celsius · UTC", className="subtitle"),
                    ]
                ),
                header_actions,
            ],
            className="page-header",
        )
        chart = dbc.Card(
            [
                html.H2("Temperature history", className="panel-heading"),
                html.P(
                    "Drag to zoom. Double-click to reset. Gaps mark unavailable readings.",
                    className="panel-caption",
                ),
                monitoring,
                dcc.Graph(
                    id="temperature-history",
                    figure=figure,
                    config={"displaylogo": False, "responsive": True},
                ),
            ],
            body=True,
            className="history-panel",
        )
        return dbc.Container(
            [
                header,
                html.Div(status, id="live-status", role="status"),
                chart,
                controls,
                html.Div(equipment, id="equipment-panel", role="status"),
                html.Footer(
                    "Physical flow/leak telemetry unavailable; verify coolant and setup independently. "
                    "Reported controller/alarm flags are diagnostic only. "
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
        Output("toggle-monitoring", "children"),
        Output("equipment-panel", "children"),
        Output("toggle-mode", "children"),
        Output("toggle-mode", "disabled"),
        Input("refresh", "n_intervals"),
    )
    def refresh(_ticks: int) -> tuple[html.Div, go.Figure, str, html.Div, str, bool]:
        snapshot = chiller.snapshot()
        status, equipment, mode = _views.render_status(snapshot, stale_after_s=stale_after_s)
        figure = _views.render_history(snapshot["history"])
        return (
            status,
            figure,
            "Stop monitoring" if snapshot["running"] else "Start monitoring",
            equipment,
            mode or "Mode unavailable",
            not modes_enabled or mode is None,
        )

    @app.callback(
        Output("setpoint-result", "children"),
        Input("apply-setpoint", "n_clicks"),
        State("setpoint-input", "value"),
        prevent_initial_call=True,
    )
    def apply_setpoint(_clicks: int, value: object) -> str:
        if not allow_setpoints:
            return "Rejected: read-only session; target changes disabled."
        if not action_lock.acquire(blocking=False):
            return "Another session action is in progress; wait for it to finish."
        try:
            if shut_down.is_set():
                return "Rejected: dashboard has shut down."
            chiller.apply_setpoint(value)
        except (ValueError, OSError, protocols.ProtocolError) as exc:
            return f"Rejected / failed: {exc}"
        finally:
            action_lock.release()
        return ""

    @app.callback(
        Output("mode-result", "children"),
        Input("toggle-mode", "n_clicks"),
        State("toggle-mode", "children"),
        prevent_initial_call=True,
    )
    def apply_mode(_clicks: int, mode: Literal["RUN", "STANDBY"]) -> str:
        if not modes_enabled:
            return "Rejected: mode changes disabled or protocol unsupported."
        if not action_lock.acquire(blocking=False):
            return "Another session action is in progress; wait for it to finish."
        try:
            if shut_down.is_set():
                return "Rejected: dashboard has shut down."
            if mode not in ("RUN", "STANDBY"):
                return "Mode unavailable; wait for fresh monitoring readings."
            chiller.set_mode(mode)
        except (ValueError, OSError, protocols.ProtocolError) as exc:
            return f"Rejected / failed: {exc}"
        finally:
            action_lock.release()
        return ""

    # Timer refresh and session actions both update status/button labels.
    # Actions give immediate feedback; allow_duplicate declares the two writers.
    @app.callback(
        Output("session-result", "children"),
        Output("refresh", "disabled"),
        Output("live-status", "children", allow_duplicate=True),
        Output("toggle-monitoring", "children", allow_duplicate=True),
        Output("equipment-panel", "children", allow_duplicate=True),
        Output("toggle-mode", "children", allow_duplicate=True),
        Output("toggle-mode", "disabled", allow_duplicate=True),
        Input("connect-device", "n_clicks"),
        Input("toggle-monitoring", "n_clicks"),
        Input("shutdown-gui", "n_clicks"),
        State("monitor-interval", "value"),
        State("monitor-csv", "value"),
        prevent_initial_call=True,
    )
    def session_action(
        _connect: int,
        _monitor: int,
        _shutdown: int,
        interval: object,
        path: str | None,
    ) -> tuple[str, bool, html.Div, str, html.Div, str, bool]:
        message, disable_refresh = perform_session_action(ctx.triggered_id, interval, path)
        snapshot = chiller.snapshot()
        status, equipment, mode = _views.render_status(snapshot, stale_after_s=stale_after_s)
        return (
            message,
            disable_refresh,
            status,
            "Stop monitoring" if snapshot["running"] else "Start monitoring",
            equipment,
            mode or "Mode unavailable",
            not modes_enabled or mode is None,
        )

    def perform_session_action(
        action: object, interval: object, path: str | None
    ) -> tuple[str, bool]:
        """Return feedback and whether refresh must stop; serialize lifecycle actions."""
        if not action_lock.acquire(blocking=False):
            return (
                "Another session action is in progress; wait for it to finish.",
                shut_down.is_set(),
            )
        try:
            if shut_down.is_set():
                return "Dashboard has shut down. You can close this page.", True
            if action == "connect-device":
                if chiller.is_connected:
                    return "Already connected.", False
                chiller.connect()
                return "Connected. Press Start monitoring to acquire readings.", False
            if action == "toggle-monitoring":
                if chiller.running:
                    chiller.stop_monitoring()
                    return "Monitoring stopped; connection remains open.", False
                chiller.start_monitoring(
                    interval_s=_seconds(interval, "Poll interval"),
                    csv_path=path or None,
                    history_size=history_size,
                )
                return "Monitoring started. Each new run resets the displayed history.", False
            if action == "shutdown-gui":
                if on_shutdown is None:
                    return "Shutdown unavailable: stop the GUI from its calling program.", False
                chiller.disconnect()
                on_shutdown()
                shut_down.set()
                return (
                    "GUI shut down; monitoring stopped and port released. You can close this page.",
                    True,
                )
            return "Choose a session action.", False
        except (ValueError, OSError, protocols.ProtocolError, RuntimeError) as exc:
            return f"Action failed: {exc}", False
        finally:
            action_lock.release()

    return app


def run_dashboard(
    chiller: TarkChiller,
    *,
    host: str = "127.0.0.1",
    port: int = 8056,
    stale_after_s: float = 3.0,
    allow_setpoints: bool = True,
    allow_mode_changes: bool | None = None,
    interval_s: float = 1.0,
    csv_path: str | Path | None = None,
    history_size: int = 3600,
) -> None:
    """Serve until Shutdown GUI or Ctrl+C, then release acquisition and serial I/O.

    The controller is left disconnected at startup unless the caller connected it.
    No reloader is used. Shutdown never commands the physical chiller into standby.
    """

    def shutdown() -> None:
        # BaseServer.shutdown must run outside its serve_forever thread.
        Thread(target=server.shutdown, name="dashboard-shutdown", daemon=True).start()

    app = create_app(
        chiller,
        stale_after_s=stale_after_s,
        allow_setpoints=allow_setpoints,
        allow_mode_changes=allow_mode_changes,
        interval_s=interval_s,
        csv_path=csv_path,
        history_size=history_size,
        on_shutdown=shutdown,
    )
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

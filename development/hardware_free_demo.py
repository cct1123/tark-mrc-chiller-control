"""Reproducible synthetic end-to-end demonstration; never opens hardware.

Run after installing .[gui,dev]:
python -m development.hardware_free_demo --duration 600 --output outputs/soak
Produces a flushed CSV, standalone Plotly trajectory and acceptance summary.
Dash callbacks use Flask's HTTP test client; monitoring runs without a browser.
"""

import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import plotly.graph_objects as go

from development.testing import make_fake_device
from tark_chiller import Chiller, RecoveryPolicy, SimulatedDevice
from tark_chiller.csvlog import CsvLogger
from tark_chiller.gui import create_app
from tark_chiller.monitoring import LiveState, Monitor, positive_seconds


def run_demo(output: Path, *, duration: float = 600, interval: float = 1) -> dict:
    positive_seconds(duration, "duration")
    positive_seconds(interval, "interval")
    if duration < 2 or interval > duration / 50:
        raise ValueError("Use duration >= 2 s and interval <= duration / 50")
    output.parent.mkdir(parents=True, exist_ok=True)
    simulator = SimulatedDevice(time_constant_s=duration / 20, noise_std_c=0.01, seed=7)
    device, endpoint = make_fake_device(simulator=simulator, transaction_timeout_s=0.01)
    chiller = Chiller(device, recovery=RecoveryPolicy(3, delay_s=0.002))
    state = LiveState(capacity=120)
    logger = CsvLogger(output.with_suffix(".csv"))
    monitor = Monitor(chiller, state, interval_s=interval, logger=logger)
    app = create_app(chiller, state, stale_after_s=max(3 * interval, 0.1))
    refresh_key = next(key for key in app.callback_map if "live-status" in key)
    stop = Event()
    actions = []
    counters = {"refreshes": 0, "page_reloads": 0, "invalid_writes_rejected": 0}

    def apply(client, value):
        response = client.post(
            "/_dash-update-component",
            json={
                "output": "setpoint-result.children",
                "outputs": {"id": "setpoint-result", "property": "children"},
                "inputs": [{"id": "apply-setpoint", "property": "n_clicks", "value": 1}],
                "state": [{"id": "setpoint-input", "property": "value", "value": value}],
                "changedPropIds": ["apply-setpoint.n_clicks"],
            },
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        return response.json["response"]["setpoint-result"]["children"]

    def connection(client, connect):
        target = "connect-device" if connect else "disconnect-device"
        response = client.post(
            "/_dash-update-component",
            json={
                "output": "connection-result.children",
                "outputs": {"id": "connection-result", "property": "children"},
                "inputs": [
                    {"id": "connect-device", "property": "n_clicks", "value": 1},
                    {"id": "disconnect-device", "property": "n_clicks", "value": 1},
                ],
                "state": [],
                "changedPropIds": [target + ".n_clicks"],
            },
        )
        assert response.status_code == 200
        assert "failed" not in response.json["response"]["connection-result"]["children"]

    def browser_activity():
        # Independent client repeatedly opens layouts and consumes snapshots.
        with app.server.test_client() as client:
            while not stop.wait(min(0.1, interval)):
                tick = counters["refreshes"]
                response = client.post(
                    "/_dash-update-component",
                    json={
                        "output": refresh_key,
                        "outputs": [
                            {"id": "live-status", "property": "children"},
                            {"id": "temperature-history", "property": "figure"},
                        ],
                        "inputs": [{"id": "refresh", "property": "n_intervals", "value": tick}],
                        "state": [],
                        "changedPropIds": ["refresh.n_intervals"],
                    },
                )
                assert response.status_code == 200, response.get_data(as_text=True)
                trace = response.json["response"]["temperature-history"]["figure"]["data"]
                assert len(trace[0]["y"]) <= 120
                assert "Rows written" in json.dumps(
                    response.json["response"]["live-status"]["children"]
                )
                counters["refreshes"] += 1
                if tick % 25 == 0:
                    assert client.get("/").status_code == 200
                    assert client.get("/_dash-layout").status_code == 200
                    counters["page_reloads"] += 1
                if tick % 10 == 0:
                    assert "Rejected" in apply(client, -1)
                    counters["invalid_writes_rejected"] += 1

    def wait_until(fraction):
        deadline = started + duration * fraction
        while time.monotonic() < deadline:
            assert state.snapshot().service_error is None
            assert state.snapshot().logging_error is None
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    started = time.monotonic()
    try:
        chiller.connect()
        monitor.start()
        with app.server.test_client() as client:
            assert "completed" in apply(client, 18)
            wait_until(0.10)
            no_browser_samples = state.snapshot().sample_count
            assert no_browser_samples >= 3
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(browser_activity)
                try:
                    wait_until(0.15)
                    endpoint.inject_fault("get_temperature", "timeout", count=2)
                    actions.append("Two transient read timeouts; bounded automatic recovery")
                    wait_until(0.30)
                    failures_before = state.snapshot().failed_samples
                    endpoint.inject_fault("get_temperature", "malformed")
                    # Wait for a failed sample, then explicitly restore intent.
                    deadline = time.monotonic() + max(1, interval * 4)
                    while state.snapshot().failed_samples == failures_before:
                        assert time.monotonic() < deadline, "Malformed reply did not surface"
                        time.sleep(0.005)
                    assert not chiller.is_connected
                    connection(client, True)
                    actions.append("Malformed reply visible; explicit GUI reconnect")
                    wait_until(0.45)
                    connection(client, False)
                    open_count = endpoint.open_count
                    wait_until(0.50)
                    assert endpoint.open_count == open_count, "Polling undid intentional disconnect"
                    connection(client, True)
                    assert "completed" in apply(client, 22)
                    actions.append("Intentional disconnect preserved; GUI reconnect and 22 °C")
                    wait_until(0.65)
                    endpoint.inject_fault("set_setpoint", "ack_lost")
                    writes_before = endpoint.applied_setpoints
                    assert "failed" in apply(client, 18)
                    assert endpoint.applied_setpoints == writes_before + 1
                    # Readback may recover; never replay the uncertain write.
                    assert chiller.get_setpoint() == 18
                    assert endpoint.applied_setpoints == writes_before + 1
                    actions.append(
                        "Lost write acknowledgement reported; write applied exactly once"
                    )
                    wait_until(0.80)
                    endpoint.inject_fault("get_temperature", "disconnect")
                    wait_until(0.90)
                finally:
                    stop.set()
                future.result(timeout=5)
            after_browser_closed = state.snapshot().sample_count
            wait_until(1.0)
            assert state.snapshot().sample_count > after_browser_closed
    finally:
        stop.set()
        monitor.request_stop()
        try:
            chiller.disconnect()
        finally:
            monitor.stop()
            logger.close()
    wall_seconds = time.monotonic() - started
    snapshot = state.snapshot()
    with logger.path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert not snapshot.running and not chiller.is_connected and not logger.is_open
    assert not snapshot.service_error and not snapshot.logging_error
    assert len(rows) == snapshot.sample_count == snapshot.logged_samples == logger.rows_written
    assert len(snapshot.history) == min(120, len(rows))
    assert len(rows) >= duration / interval * 0.8
    assert any(row["error"] for row in rows)
    # A cancelled in-flight read may correctly publish an unavailable shutdown
    # row. Assess the last complete measurement, with freshness at shutdown.
    last_valid = next(row for row in reversed(rows) if not row["error"])
    final_temperature = float(last_valid["temperature_c"])
    assert abs(final_temperature - 18) < 0.2
    assert float(rows[-1]["elapsed_s"]) - float(last_valid["elapsed_s"]) < max(1, 3 * interval)
    assert endpoint.applied_setpoints == 3, "A rejected/uncertain write was applied unexpectedly"
    figure = go.Figure()
    for field, name in (("temperature_c", "Temperature"), ("setpoint_c", "Setpoint")):
        figure.add_scatter(
            x=[r["timestamp_utc"] for r in rows],
            y=[float(r[field]) if r[field] else None for r in rows],
            name=name,
            mode="lines",
            connectgaps=False,
        )
    figure.update_layout(
        title="Hardware-free acceptance trajectory — synthetic serial",
        xaxis_title="Time (UTC)",
        yaxis_title="Temperature (°C)",
        template="plotly_white",
    )
    figure.write_html(output.with_name(output.name + "-trajectory.html"))
    summary = {
        "result": "PASS",
        "scope": "Synthetic memory serial; no physical hardware",
        "duration_requested_s": duration,
        "duration_observed_s": wall_seconds,
        "sample_interval_s": interval,
        "samples": len(rows),
        "history_capacity": 120,
        "history_retained": len(snapshot.history),
        "failed_samples": snapshot.failed_samples,
        "csv_rows": logger.rows_written,
        "session_id": logger.session_id,
        "samples_before_browser": no_browser_samples,
        "samples_after_browser_closed": snapshot.sample_count - after_browser_closed,
        "final_temperature_c": final_temperature,
        "final_setpoint_c": 18,
        "open_count": endpoint.open_count,
        "applied_setpoints": endpoint.applied_setpoints,
        "operation_counts": endpoint.operation_counts,
        "bounded_trace_length": len(endpoint.trace),
        "clean_shutdown": True,
        **counters,
        "actions": actions,
    }
    output.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--output", type=Path, default=Path("outputs/soak"))
    args = parser.parse_args()
    print(
        json.dumps(run_demo(args.output, duration=args.duration, interval=args.interval), indent=2)
    )

"""TCP socket server that receives metrics from agents.

Protocol: each agent opens one TCP connection and sends newline-delimited JSON
messages (one JSON object per line). See README.md for the message formats.

Concurrency: the accept loop starts one thread per connected agent, so many
agents are handled at the same time. A separate thread marks silent machines offline.
"""
import json
import logging
import socket
import threading
import time
from datetime import datetime, timezone

import config
import db

log = logging.getLogger("tcp_server")

# metric key -> (label used in alert messages, threshold in %)
THRESHOLDS = {
    "cpu_percent": ("CPU", config.CPU_ALERT),
    "mem_percent": ("Memory", config.MEM_ALERT),
    "disk_percent": ("Disk", config.DISK_ALERT),
}
LOG_SEVERITIES = {"INFO", "WARNING", "ERROR", "CRITICAL"}
CLIENT_IDLE_TIMEOUT = 60  # seconds; drop connections that send nothing for this long

# Alerts that are currently "firing", as (hostname, metric_key). Shared by all
# client threads, so access is guarded by a lock. This makes us alert once when a
# value crosses the threshold (and once when it recovers) instead of every sample.
_active_alerts = set()
_alerts_lock = threading.Lock()


def _to_datetime(epoch):
    """Agent timestamp (UTC epoch seconds) -> naive UTC datetime for MySQL."""
    if epoch is None:
        return db.utcnow()
    return datetime.fromtimestamp(float(epoch), timezone.utc).replace(tzinfo=None)


def check_thresholds(machine_id, hostname, metrics):
    for key, (label, limit) in THRESHOLDS.items():
        value = metrics.get(key)
        if value is None:
            continue
        alert_key = (hostname, key)
        is_high = value >= limit
        with _alerts_lock:
            was_high = alert_key in _active_alerts
            if is_high:
                _active_alerts.add(alert_key)
            else:
                _active_alerts.discard(alert_key)

        if is_high and not was_high:
            db.insert_event(machine_id, "ALERT", "WARNING",
                            f"{label} usage {value:.1f}% is above the {limit:g}% threshold")
            log.warning("ALERT %s: %s %.1f%% >= %g%%", hostname, label, value, limit)
        elif was_high and not is_high:
            db.insert_event(machine_id, "ALERT", "INFO",
                            f"{label} usage back to normal ({value:.1f}%)")


def process_message(msg, ip):
    """Handle one decoded JSON message from an agent."""
    hostname = str(msg["hostname"])[:255]
    msg_type = msg.get("type")

    if msg_type == "metrics":
        machine_id, previous_status = db.upsert_machine(hostname, ip, msg.get("os"))
        if previous_status is None:
            db.insert_event(machine_id, "STATUS", "INFO", f"New machine registered from {ip}")
        elif previous_status == "offline":
            db.insert_event(machine_id, "STATUS", "INFO", "Machine is back online")
        db.insert_metrics(machine_id, _to_datetime(msg.get("ts")), msg)
        check_thresholds(machine_id, hostname, msg)

    elif msg_type == "log":
        machine_id = db.get_machine_id(hostname)
        if machine_id is None:  # logs before the first metrics sample: ignore
            return
        severity = str(msg.get("severity", "ERROR")).upper()
        if severity not in LOG_SEVERITIES:
            severity = "ERROR"
        db.insert_event(machine_id, "LOG", severity, str(msg.get("line", ""))[:2000],
                        _to_datetime(msg.get("ts")))

    else:
        log.warning("unknown message type from %s: %r", ip, msg_type)


def handle_client(conn, addr):
    """Runs in its own thread: read JSON lines from one agent until it disconnects."""
    ip = addr[0]
    log.info("agent connected from %s:%s", ip, addr[1])
    conn.settimeout(CLIENT_IDLE_TIMEOUT)
    try:
        with conn, conn.makefile("r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    process_message(json.loads(line), ip)
                except Exception:
                    log.exception("could not process message from %s: %.200s", ip, line)
    except OSError as e:  # includes socket timeout / connection reset
        log.info("connection from %s closed: %s", ip, e)
    log.info("agent %s:%s disconnected", ip, addr[1])


def run_tcp_server(host=config.TCP_HOST, port=config.TCP_PORT):
    """Accept loop: one new thread per agent connection. Blocks forever."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen()
    log.info("TCP server listening on %s:%s", host, port)
    while True:
        conn, addr = server.accept()
        threading.Thread(
            target=handle_client, args=(conn, addr), daemon=True, name=f"agent-{addr[0]}:{addr[1]}"
        ).start()


def offline_checker(interval=5):
    """Every few seconds, mark machines that stopped sending data as offline."""
    while True:
        time.sleep(interval)
        try:
            for m in db.mark_offline_machines(config.OFFLINE_AFTER):
                db.insert_event(m["id"], "STATUS", "CRITICAL",
                                f"Machine went offline (no data for {config.OFFLINE_AFTER}s)")
                log.warning("machine %s is offline", m["hostname"])
        except Exception:
            log.exception("offline checker failed")


if __name__ == "__main__":
    # Lets you run the collector without the web dashboard: python tcp_server.py
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    threading.Thread(target=offline_checker, daemon=True, name="offline-checker").start()
    run_tcp_server()

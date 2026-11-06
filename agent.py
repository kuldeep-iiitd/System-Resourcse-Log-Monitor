"""Monitoring agent: runs on each monitored machine.

Every few seconds it collects CPU, memory, disk and network metrics with psutil,
optionally picks up new ERROR/CRITICAL lines from a log file, and sends everything
to the central server over a TCP socket as newline-delimited JSON.

Usage:
    python agent.py                                   # report this machine to 127.0.0.1:9000
    python agent.py --server 10.0.0.5 --interval 10   # remote server, every 10 s
    python agent.py --name web-1 --log /var/log/app.log
"""
import argparse
import json
import os
import platform
import socket
import time

import psutil

LOG_LEVELS = ("CRITICAL", "ERROR")  # log lines containing these words are forwarded

# Drive used for "disk %". On macOS "/" is a small read-only system volume and your
# files live on /System/Volumes/Data, so use that when it exists; everywhere else use "/".
DISK_PATH = "/System/Volumes/Data" if os.path.isdir("/System/Volumes/Data") else "/"


class LogTailer:
    """Remembers a position in a log file and returns only the lines added since last time."""

    def __init__(self, path):
        self.path = path
        # Start at the end of the file: only new lines are interesting.
        self.pos = os.path.getsize(path) if os.path.exists(path) else 0

    def new_lines(self):
        if not os.path.exists(self.path):
            return []
        if os.path.getsize(self.path) < self.pos:  # file was truncated or rotated
            self.pos = 0
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read()
        end = data.rfind(b"\n")
        if end == -1:  # no complete line yet
            return []
        self.pos += end + 1
        return data[:end].decode("utf-8", errors="replace").splitlines()

    def matching_lines(self):
        """Yield (severity, line) for new lines that contain ERROR or CRITICAL."""
        for line in self.new_lines():
            for level in LOG_LEVELS:
                if level in line:
                    yield level, line.strip()
                    break


def collect_metrics(prev):
    """Return (metrics dict, state for next call). Rates are computed from counter deltas."""
    cpu = psutil.cpu_percent(interval=1)  # % averaged over 1 second
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(DISK_PATH)
    dio = psutil.disk_io_counters()        # may be None on some systems
    nio = psutil.net_io_counters()
    now = time.time()

    metrics = {
        "cpu_percent": cpu,
        "mem_percent": vm.percent,
        "mem_used_mb": round(vm.used / 1024 ** 2, 1),
        "disk_percent": disk.percent,
        "disk_read_kbps": 0.0,
        "disk_write_kbps": 0.0,
        "net_sent_kbps": 0.0,
        "net_recv_kbps": 0.0,
    }
    if prev:
        prev_time, prev_dio, prev_nio = prev
        elapsed = max(now - prev_time, 0.001)

        def kbps(new, old):
            return round(max(new - old, 0) / 1024 / elapsed, 2)

        if dio and prev_dio:
            metrics["disk_read_kbps"] = kbps(dio.read_bytes, prev_dio.read_bytes)
            metrics["disk_write_kbps"] = kbps(dio.write_bytes, prev_dio.write_bytes)
        metrics["net_sent_kbps"] = kbps(nio.bytes_sent, prev_nio.bytes_sent)
        metrics["net_recv_kbps"] = kbps(nio.bytes_recv, prev_nio.bytes_recv)

    return metrics, (now, dio, nio)


def main():
    parser = argparse.ArgumentParser(description="System monitoring agent")
    parser.add_argument("--server", default="127.0.0.1", help="server host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="server TCP port (default 9000)")
    parser.add_argument("--interval", type=float, default=5, help="seconds between samples (default 5)")
    parser.add_argument("--name", default=socket.gethostname(), help="machine name to report (default: hostname)")
    parser.add_argument("--log", help="optional log file to watch for ERROR/CRITICAL lines")
    args = parser.parse_args()

    os_name = platform.platform()
    tailer = LogTailer(args.log) if args.log else None
    state = None
    sock = None
    print(f"[agent] reporting as '{args.name}' to {args.server}:{args.port} every {args.interval}s")

    while True:
        started = time.time()
        try:
            if sock is None:
                sock = socket.create_connection((args.server, args.port), timeout=10)
                print("[agent] connected")

            metrics, state = collect_metrics(state)
            messages = [{"type": "metrics", "hostname": args.name, "os": os_name, "ts": time.time(), **metrics}]
            if tailer:
                for severity, line in tailer.matching_lines():
                    messages.append({"type": "log", "hostname": args.name, "ts": time.time(),
                                     "severity": severity, "line": line})

            payload = "".join(json.dumps(m) + "\n" for m in messages)
            sock.sendall(payload.encode("utf-8"))
            print(f"[agent] sent cpu={metrics['cpu_percent']}% mem={metrics['mem_percent']}% "
                  f"disk={metrics['disk_percent']}% logs={len(messages) - 1}")
        except OSError as e:
            print(f"[agent] connection problem ({e}); retrying in {args.interval}s")
            if sock:
                sock.close()
            sock = None

        time.sleep(max(0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[agent] stopped")

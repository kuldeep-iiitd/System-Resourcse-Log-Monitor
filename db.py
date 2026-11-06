"""Tiny data-access layer: plain SQL over PyMySQL, no ORM.

Every function opens its own short-lived connection. PyMySQL connections are not
thread-safe, and the TCP server runs one thread per agent, so "one connection per
operation" is the simplest correct choice for an MVP (a pool would be the next step).
"""
from datetime import datetime, timedelta, timezone

import pymysql
from pymysql.cursors import DictCursor

import config


def utcnow():
    """Current UTC time as a naive datetime (MySQL DATETIME has no timezone)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_conn():
    return pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DB,
        autocommit=True,
        cursorclass=DictCursor,
    )


def _execute(sql, params=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    finally:
        conn.close()


def _fetchall(sql, params=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------- writes

def upsert_machine(hostname, ip_address, os_name):
    """Register the machine (or refresh it) and mark it online.

    Returns (machine_id, previous_status). previous_status is None for a new machine.
    """
    now = utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status FROM machines WHERE hostname = %s", (hostname,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO machines (hostname, ip_address, os, status, first_seen, last_seen) "
                    "VALUES (%s, %s, %s, 'online', %s, %s)",
                    (hostname, ip_address, os_name, now, now),
                )
                return cur.lastrowid, None
            cur.execute(
                "UPDATE machines SET ip_address = %s, os = %s, status = 'online', last_seen = %s "
                "WHERE id = %s",
                (ip_address, os_name, now, row["id"]),
            )
            return row["id"], row["status"]
    finally:
        conn.close()


def get_machine_id(hostname):
    rows = _fetchall("SELECT id FROM machines WHERE hostname = %s", (hostname,))
    return rows[0]["id"] if rows else None


def insert_metrics(machine_id, ts, m):
    _execute(
        "INSERT INTO metrics (machine_id, ts, cpu_percent, mem_percent, mem_used_mb, disk_percent, "
        "disk_read_kbps, disk_write_kbps, net_sent_kbps, net_recv_kbps) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            machine_id, ts,
            m.get("cpu_percent"), m.get("mem_percent"), m.get("mem_used_mb"), m.get("disk_percent"),
            m.get("disk_read_kbps"), m.get("disk_write_kbps"),
            m.get("net_sent_kbps"), m.get("net_recv_kbps"),
        ),
    )


def insert_event(machine_id, event_type, severity, message, ts=None):
    _execute(
        "INSERT INTO events (machine_id, ts, event_type, severity, message) VALUES (%s, %s, %s, %s, %s)",
        (machine_id, ts or utcnow(), event_type, severity, message),
    )


def mark_offline_machines(offline_after_seconds):
    """Flip machines that stopped reporting to 'offline'. Returns the rows that changed."""
    cutoff = utcnow() - timedelta(seconds=offline_after_seconds)
    changed = []
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, hostname FROM machines WHERE status = 'online' AND last_seen < %s", (cutoff,)
            )
            for m in cur.fetchall():
                # Re-check last_seen in the UPDATE so a machine that reported in the meantime
                # is not wrongly flipped to offline.
                cur.execute(
                    "UPDATE machines SET status = 'offline' "
                    "WHERE id = %s AND status = 'online' AND last_seen < %s",
                    (m["id"], cutoff),
                )
                if cur.rowcount:
                    changed.append(m)
    finally:
        conn.close()
    return changed


# ---------------------------------------------------------------- reads (dashboard / API)

def list_machines():
    """All machines plus their most recent metrics sample."""
    return _fetchall(
        """
        SELECT m.id, m.hostname, m.ip_address, m.os, m.status, m.first_seen, m.last_seen,
               x.ts AS metrics_ts, x.cpu_percent, x.mem_percent, x.mem_used_mb, x.disk_percent,
               x.disk_read_kbps, x.disk_write_kbps, x.net_sent_kbps, x.net_recv_kbps
        FROM machines m
        LEFT JOIN metrics x
               ON x.id = (SELECT id FROM metrics WHERE machine_id = m.id ORDER BY ts DESC, id DESC LIMIT 1)
        ORDER BY m.hostname
        """
    )


def get_metrics(machine_id, minutes):
    since = utcnow() - timedelta(minutes=minutes)
    return _fetchall(
        "SELECT ts, cpu_percent, mem_percent, mem_used_mb, disk_percent, disk_read_kbps, "
        "disk_write_kbps, net_sent_kbps, net_recv_kbps "
        "FROM metrics WHERE machine_id = %s AND ts >= %s ORDER BY ts",
        (machine_id, since),
    )


def get_events(limit, machine_id=None):
    sql = (
        "SELECT e.id, e.ts, e.event_type, e.severity, e.message, m.hostname "
        "FROM events e JOIN machines m ON m.id = e.machine_id "
    )
    params = []
    if machine_id is not None:
        sql += "WHERE e.machine_id = %s "
        params.append(machine_id)
    sql += "ORDER BY e.ts DESC, e.id DESC LIMIT %s"
    params.append(limit)
    return _fetchall(sql, params)

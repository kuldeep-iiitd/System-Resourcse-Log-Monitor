-- System Resource & Log Monitor - MySQL schema
-- Run once as an admin user:   mysql -u root < schema.sql

CREATE DATABASE IF NOT EXISTS monitoring;

-- Dedicated application user (change the password for anything beyond local use)
CREATE USER IF NOT EXISTS 'monitor'@'localhost' IDENTIFIED BY 'monitor';
CREATE USER IF NOT EXISTS 'monitor'@'127.0.0.1' IDENTIFIED BY 'monitor';
GRANT ALL PRIVILEGES ON monitoring.* TO 'monitor'@'localhost';
GRANT ALL PRIVILEGES ON monitoring.* TO 'monitor'@'127.0.0.1';
FLUSH PRIVILEGES;

USE monitoring;

-- One row per monitored machine (agent). Holds the current availability status.
CREATE TABLE IF NOT EXISTS machines (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    hostname    VARCHAR(255) NOT NULL UNIQUE,
    ip_address  VARCHAR(45),
    os          VARCHAR(255),
    status      ENUM('online', 'offline') NOT NULL DEFAULT 'online',
    first_seen  DATETIME NOT NULL,          -- UTC
    last_seen   DATETIME NOT NULL           -- UTC, server clock
);

-- One row per metrics sample sent by an agent (time series).
CREATE TABLE IF NOT EXISTS metrics (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    machine_id       INT NOT NULL,
    ts               DATETIME NOT NULL,     -- UTC, agent clock
    cpu_percent      FLOAT,
    mem_percent      FLOAT,
    mem_used_mb      FLOAT,
    disk_percent     FLOAT,
    disk_read_kbps   FLOAT,
    disk_write_kbps  FLOAT,
    net_sent_kbps    FLOAT,
    net_recv_kbps    FLOAT,
    FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE,
    INDEX idx_metrics_machine_ts (machine_id, ts)
);

-- Everything noteworthy that happened: threshold alerts, log lines, status changes.
CREATE TABLE IF NOT EXISTS events (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    machine_id  INT NOT NULL,
    ts          DATETIME NOT NULL,          -- UTC
    event_type  ENUM('ALERT', 'LOG', 'STATUS') NOT NULL,
    severity    ENUM('INFO', 'WARNING', 'ERROR', 'CRITICAL') NOT NULL,
    message     TEXT NOT NULL,
    FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE,
    INDEX idx_events_ts (ts)
);

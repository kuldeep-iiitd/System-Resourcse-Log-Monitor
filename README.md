# System Resource & Log Monitor

A simple monitoring system I made in Python. You run a small agent on each machine and it sends CPU, memory, disk and network usage to a central server. The server saves everything in MySQL and shows it on a web dashboard with graphs and alerts.

I built this to learn about socket programming, multithreading and databases.

![dashboard](docs/dashboard.png)

## Features

- Collects CPU, memory, disk and network usage from each machine every 5 seconds
- Agents send data to the server over TCP sockets
- Server uses a separate thread for each agent, so many machines can connect at the same time
- Stores all the data in MySQL
- Dashboard with live graphs (refreshes every 5 sec)
- Shows if a machine is online or offline (offline if no data for 15 sec)
- Alerts when CPU, memory or disk goes above a limit
- Can watch a log file and report lines that have ERROR or CRITICAL in them

## Tech used

- Python
- FastAPI (for the dashboard and API)
- MySQL
- psutil (to read system stats)
- Chart.js (for the graphs)

## How it works

```
agent.py (machine 1) ---\
agent.py (machine 2) ----+---> server.py (TCP port 9000) ---> MySQL ---> dashboard (port 8000)
agent.py (machine 3) ---/
```

1. `agent.py` reads the system stats using psutil and sends them as JSON to the server over a TCP socket.
2. The server starts a new thread for every agent that connects. The thread saves the data in MySQL and checks if any value is above the alert limit.
3. Another thread checks every few seconds if any machine stopped sending data and marks it offline.
4. The dashboard calls the FastAPI endpoints and draws the graphs.

## Files

```
agent.py        - runs on the machine you want to monitor
server.py       - FastAPI app, also starts the TCP server
tcp_server.py   - TCP socket server, threads, alerts, offline check
db.py           - all the MySQL queries
config.py       - settings (db login, ports, alert limits)
schema.sql      - creates the database and tables
static/index.html - the dashboard page
```

## Setup

You need Python 3.10+ and MySQL.

**1. Install MySQL**

Mac:
```
brew install mysql
brew services start mysql
```

Ubuntu:
```
sudo apt install mysql-server
sudo systemctl start mysql
```

**2. Create the database**

```
mysql -u root < schema.sql
```
(on Ubuntu use `sudo mysql < schema.sql`)

This makes a database called `monitoring` and a user `monitor` with password `monitor`.

**3. Install python packages**

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## How to run

**Start the server**

```
uvicorn server:app --host 0.0.0.0 --port 8000
```

**Start an agent** (in another terminal)

```
python agent.py --name my-laptop
```

Then open http://localhost:8000 in your browser.

If you don't have more machines you can just run a few agents with different names to test it:
```
python agent.py --name web-1 &
python agent.py --name db-1 &
python agent.py --name cache-1 &
```

### Agent options

```
--server     IP of the server (default 127.0.0.1)
--port       server port (default 9000)
--interval   seconds between each update (default 5)
--name       name of the machine (default is the hostname)
--log        path of a log file to watch for errors (optional)
```

Example with a log file:
```
python agent.py --name web-1 --log /var/log/myapp.log
```

## Running on other machines

On the server machine, do the full setup above and start the server. Find its IP (`ipconfig getifaddr en0` on Mac, `hostname -I` on Linux).

On every other machine you only need `agent.py` and psutil:
```
pip install psutil
python agent.py --server <server-ip> --name <some-name>
```

Make sure port 9000 (for agents) and 8000 (for the dashboard) are not blocked by the firewall.

## Database

There are 3 tables:

- `machines` - one row per machine, with its status (online/offline) and last seen time
- `metrics` - all the stats with timestamps
- `events` - alerts, log errors, and when machines go online/offline

## API

- `GET /api/machines` - list of machines with their latest stats
- `GET /api/machines/{id}/metrics?minutes=30` - stats of one machine for the graphs
- `GET /api/events` - latest alerts and events
- `GET /docs` - FastAPI auto docs

## Config

You can change settings with environment variables (see `config.py`). For example to change the alert limits:

```
CPU_ALERT=70 MEM_ALERT=90 uvicorn server:app --port 8000
```

Default limits are CPU 80%, memory 85%, disk 90%.

Note: don't use `--reload` with uvicorn, it starts the TCP server twice and gives a port error.

## Things I want to add

- Password/token so random people can't send data to the server
- Email or Slack alerts
- Delete old data after some days
- Run the agent as a service so it starts on boot
- Docker setup

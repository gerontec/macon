# Macon Geothermal Heat Pump Control

RS485/Modbus RTU control scripts for Macon ground-water heat pumps (Protocol V1.3).

Connect your RS485 adapter to **CN17, Pin A1 (A+) and B1 (B−)**. See `HeatPumpBoard.jpg`.

---

## Architecture

```
Raspberry Pi
│
├─ /dev/ttyAMA0 (RS485)
│       │
│       └─ macon_daemon.py   ← single Modbus owner (systemd service)
│               │
│               ├─ every 2s : read Reg 2136 Bit 3 (Grundwasserpumpe)
│               │             → HTTP → Shelly Plug S Gen3
│               │
│               └─ every 60s: read all registers → MySQL DB
│                             + frequency check + error auto-reset
          + publish status JSON → MQTT topic `heatmacon`
│
├─ shellyplug.py             ← manual CLI: on / off / toggle (MQTT + HTTP)
├─ maconread2db.py           ← standalone: CLI on/off/reset + one-shot DB read
└─ write_freq.py             ← one-shot: set compressor frequency
```

**Only `macon_daemon.py` holds the serial port.** All other scripts are
one-shot tools that open and close the port quickly.

---

## Files

| File | Purpose |
|------|---------|
| `macon_daemon.py` | **Unified daemon** – Shelly control (2 s) + DB logging (60 s) |
| `shellyplug.py` | Manual Shelly Plug CLI: `on \| off \| toggle` via MQTT + HTTP |
| `maconread2db.py` | One-shot register read → MySQL; CLI `on\|off\|reset` for the heat pump |
| `write_freq.py` | One-shot compressor frequency setter with soft-reset |
| `HeatPumpBoard.jpg` | RS485 wiring photo |

---

## Setup

```bash
pip3 install pymodbus paho-mqtt requests pymysql
```

### Systemd service (macon_daemon)

```bash
sudo cp macon-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now macon-daemon
```

**`/etc/systemd/system/macon-daemon.service`:**
```ini
[Unit]
Description=Macon WP Daemon – Grundwasserpumpe + DB-Logging
After=network.target mosquitto.service
Wants=mosquitto.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/python
ExecStart=/usr/bin/python3 /home/pi/python/macon_daemon.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Crontab

`maconread2db.py` is **no longer needed in cron** – the daemon handles DB writes.
Remove or comment out the old entry:

```cron
# * * * * * /home/pi/python/maconread2db.py   ← replaced by macon-daemon service
```

---

## Key Registers (Modbus RTU, baud 2400, slave 1)

| Register | Name | Notes |
|----------|------|-------|
| 2000 | Unit ON/OFF | 0 = OFF, 1 = ON (soft-reset: OFF → wait → ON) |
| 2056 | Host frequency control | 0 = auto, 1 = host-controlled |
| 2057 | Compressor set frequency | 0–120 Hz |
| 2118 | Compressor real frequency | read-only |
| 2135 | System status 2 | Bit 1 = compressor running |
| **2136** | **System status 3** | **Bit 3 = Grundwasserpumpe angefordert** |
| 2137 | Error code | 0 = no error |

### Reg 2136 Bit 3 – Grundwasserpumpe

`macon_daemon.py` polls this bit every **2 seconds**:

- Bit 3 = **1** → Macon requests ground-water pump → **Shelly Plug ON**
- Bit 3 = **0** → no request → **Shelly Plug OFF**

The Shelly is switched only on state **change** to avoid redundant HTTP calls.

---

## Usage

```bash
# Daemon status + live log
sudo systemctl status macon-daemon
sudo journalctl -u macon-daemon -f

# Manual Shelly switch (bypasses daemon, for testing)
./shellyplug.py on
./shellyplug.py off
./shellyplug.py toggle

# One-shot heat pump control
./maconread2db.py on     # turn heat pump ON
./maconread2db.py off    # turn heat pump OFF
./maconread2db.py reset  # soft-reset (OFF → 2s → ON)

# One-shot frequency write
./write_freq.py 80       # set 80 Hz
```

---

## Hardware

- **Protocol**: Modbus RTU, 2400 baud, 8E1, slave ID 1
- **Connection**: RS485 via CN17 (A1 = A+, B1 = B−)
- **Shelly**: Plug S Gen3 at `192.168.178.100` (HTTP RPC)
- **DB**: MySQL/MariaDB at `192.168.178.218`, database `wagodb`, table `macon_pivot`
- **MQTT**: broker `192.168.178.218:1883`, topic `heatmacon` (retained JSON, every 60 s)

---

## Protocol Reference

Macon Protocol V1.3 – Modbus RTU. No dedicated reset register;
use Reg 2000 (write 0, wait 2 s, write 1) for soft-reset.

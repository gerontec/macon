# Macon Geothermal Heat Pump Control

RS485/Modbus RTU control scripts for Macon ground-water heat pumps (Protocol V1.3).

Connect your RS485 adapter to **CN17, Pin A1 (A+) and B1 (B−)**. See `HeatPumpBoard.jpg`.

---

## Architecture

```
Raspberry Pi
│
├─ /dev/ttyAMA0 (RS485) — held exclusively by macon_daemon
│       │
│       └─ macon_daemon.py  (systemd service)
│               │
│               ├─ every 2s : read Reg 2136 Bit 3 (Grundwasserpumpe)
│               │             → HTTP POST → Shelly Plug S Gen3
│               │             → check /tmp/macon_cmd (proxy commands)
│               │
│               └─ every 5s : settings_check + frequency_check + error_check
│                             → read all registers → MySQL DB + MQTT heatmacon
│
├─ shellyplug.py     ← manual CLI: on / off / toggle (MQTT + HTTP)
├─ maconread2db.py   ← proxy CLI: on/off/reset → /tmp/macon_cmd → daemon
└─ write_freq.py     ← one-shot: set compressor frequency (use only when daemon stopped)
```

**Only `macon_daemon.py` holds the serial port.**
All other scripts communicate via `/tmp/macon_cmd` (proxy) or are one-shot tools.

---

## Files

| File | Purpose |
|------|---------|
| `macon_daemon.py` | Unified daemon – Shelly, DB, MQTT, frequency + settings watchdog |
| `shellyplug.py` | Manual Shelly Plug CLI: `on \| off \| toggle` via MQTT + HTTP |
| `maconread2db.py` | Proxy CLI: writes `on\|off\|reset` to `/tmp/macon_cmd` for the daemon |
| `write_freq.py` | One-shot frequency setter (stop daemon first) |
| `HeatPumpBoard.jpg` | RS485 wiring photo |

---

## Setup

```bash
pip3 install pymodbus paho-mqtt requests pymysql
```

### Systemd service

```bash
sudo cp macon-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now macon-daemon
```

**`/etc/systemd/system/macon-daemon.service`:**
```ini
[Unit]
Description=Macon WP Daemon – Grundwasserpumpe + DB + MQTT
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

`maconread2db.py` is **no longer needed in cron** — the daemon handles DB writes.

```cron
# * * * * * /home/pi/python/maconread2db.py   ← replaced by macon-daemon service
```

---

## Daemon Logic

### 2s loop — Grundwasserpumpe → Shelly

Reads **Reg 2136 Bit 3** (System status 3). On state change only:

| Bit 3 | Action |
|-------|--------|
| 1 | Macon requests ground-water pump → Shelly Plug **ON** |
| 0 | No request → Shelly Plug **OFF** |

### 2s loop — Proxy command file

If `/tmp/macon_cmd` exists, the daemon reads it, executes via Modbus, and deletes it.

```bash
echo on    > /tmp/macon_cmd   # Reg 2000 = 1  (WP EIN)
echo off   > /tmp/macon_cmd   # Reg 2000 = 0  (WP AUS)
echo reset > /tmp/macon_cmd   # Reg 2000: 0 → 2s → 1  (Soft-Reset)
```

`maconread2db.py on/off/reset` writes to this file automatically.

### 5s loop — Settings watchdog (settings_check)

Ensures the heat pump stays in **DHW mode**:

| Register | Name | Target | Pi writes? |
|----------|------|--------|-----------|
| **2001** | Working_mode | **5 = DHW/Hot_water** | **Yes** — only if ≠ 5 |
| 2047 | Freq. reduction threshold | 40 Hz (Macon-internal) | **No** — read-only |

> **Reg 2047** is NOT a boolean flag. It is a frequency threshold in Hz set
> internally by the Macon controller ("reduce frequency when within N Hz of
> setpoint"). The Pi never writes this register.

### 5s loop — Frequency control (frequency_check)

The Macon controller has **priority during startup** (first minutes after power-on).
The Pi only takes over host frequency control when all three conditions are met:

```
1. WP is ON          (Reg 2000 = 1)
2. Reg 2056 readable (host control available — Macon startup phase ended)
3. real_frequency > 0 (compressor is actually running)
```

Once conditions are met, the Pi sets:
- **Reg 2056 = 1** — enable host frequency control
- **Reg 2057 = 80 Hz** — limit compressor to 80 Hz (reduced from max 120 Hz)

| Register | Name | Role |
|----------|------|------|
| 2056 | Host frequency control | 0 = Macon controls, 1 = Pi controls |
| 2057 | Compressor set frequency | Written by Macon during startup, then by Pi (80 Hz) |
| 2118 | Compressor real frequency | Read-only — used to detect startup completion |

### 5s loop — Error monitoring (error_check)

Reads all three error registers and logs active bits:

| Register | Name | Action on non-zero |
|----------|------|--------------------|
| 2134 | Error code 1 | Log ERROR + active bit descriptions |
| 2137 | Error code 2 | Log ERROR + active bit descriptions |
| 2138 | Error code 3 | Log ERROR + active bit descriptions |

Additionally: if compressor is running (Reg 2135 Bit 1 = 1) but AC current < 3 A,
an **auto soft-reset** is triggered (Reg 2000: 0 → 2s → 1).

---

## MQTT Payload — topic `heatmacon`

Published every **5 seconds** to broker `192.168.178.218:1883` (retained).

```bash
mosquitto_sub -h 192.168.178.218 -t heatmacon -C 1 | python3 -m json.tool
```

Example payload:
```json
{
  "timestamp": "2026-02-27T11:53:16",
  "mode": "DHW",
  "freq_reduction_threshold_hz": 40,
  "unit_on_off": 1,
  "dhw_setpoint": 48,
  "host_freq_ctrl": 1,
  "set_frequency": 80,
  "real_frequency": 72,
  "ac_current": 6,
  "system_status_1": 1,
  "system_status_2": 36608,
  "water_tank_temp": 36,
  "outlet_water_temp": 38,
  "inlet_water_temp": 38,
  "brine_inlet_temp": 10,
  "brine_outlet_temp": 10,
  "discharge_temp": 20,
  "suction_temp": 17,
  "ambient_temp": 0,
  "shelly_on": true,
  "grundwasserpumpe": true,
  "error_code_1": 0,
  "error_code_2": 0,
  "error_code_3": 0
}
```

---

## Key Registers (Modbus RTU, baud 2400, slave 1)

| Register | Name | Notes |
|----------|------|-------|
| 2000 | Unit ON/OFF | 0 = OFF, 1 = ON |
| 2001 | Working mode | 0=Cooling, 1=Underfloor, 2=Fan-coil, **5=DHW**, 6=Auto |
| 2004 | DHW setpoint | °C |
| 2047 | Freq. reduction threshold | Hz — Macon-internal, Pi reads only |
| 2056 | Host frequency control | 0 = Macon controls, 1 = Pi controls |
| 2057 | Compressor set frequency | 0–120 Hz |
| 2100 | Water tank temperature | °C |
| 2102 | Outlet water temperature | °C |
| 2103 | Inlet water temperature | °C |
| 2118 | Compressor real frequency | Hz, read-only |
| 2133 | System status 1 | Bit 0 = heating active |
| 2134 | Error code 1 | 0 = no error |
| **2136** | **System status 3** | **Bit 3 = Grundwasserpumpe angefordert** |
| 2137 | Error code 2 | 0 = no error |
| 2138 | Error code 3 | 0 = no error |

---

## Usage

```bash
# Daemon
sudo systemctl status macon-daemon
sudo journalctl -u macon-daemon -f

# MQTT live
mosquitto_sub -h 192.168.178.218 -t heatmacon -C 1 | python3 -m json.tool

# Heat pump control (via daemon proxy)
echo on    > /tmp/macon_cmd
echo off   > /tmp/macon_cmd
echo reset > /tmp/macon_cmd
# or:
python3 maconread2db.py on | off | reset

# Manual Shelly (for testing)
./shellyplug.py on | off | toggle
```

---

## Hardware

- **Protocol**: Modbus RTU, 2400 baud, 8E1, slave ID 1
- **Connection**: RS485 via CN17 (A1 = A+, B1 = B−)
- **Shelly**: Plug S Gen3 at `192.168.178.100` (HTTP RPC)
- **DB**: MySQL/MariaDB at `192.168.178.218`, database `wagodb`, table `macon_pivot`
- **MQTT**: broker `192.168.178.218:1883`, topic `heatmacon` (retained, every 5 s)

---

## Protocol Reference

Macon Protocol V1.3 – Modbus RTU. No dedicated reset register;
use Reg 2000 (write 0, wait 2 s, write 1) for soft-reset.

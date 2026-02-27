#!/usr/bin/env python3
"""
macon_daemon.py — Unified Macon WP Daemon

Alle 2s : Modbus Reg 2136 Bit 3 lesen → Grundwasserpumpe angefordert?
          → Shelly Plug S Gen3 per HTTP ein-/ausschalten (nur bei Änderung)
          → Proxy: /tmp/macon_cmd lesen → WP-Register schreiben

Alle 60s: Alle konfigurierten Register lesen → MySQL-DB schreiben
          + Status-JSON → MQTT topic "heatmacon" (broker 192.168.178.218)
          + Kompressorfrequenz prüfen/setzen + Fehler-Auto-Reset

Proxy-Befehle (kein Daemon-Stopp nötig):
  echo on    > /tmp/macon_cmd   # WP einschalten  (Reg 2000 = 1)
  echo off   > /tmp/macon_cmd   # WP ausschalten  (Reg 2000 = 0)
  echo reset > /tmp/macon_cmd   # Soft-Reset       (0 → 2s → 1)

Systemd:  sudo systemctl start macon-daemon
Log:      /tmp/macon_daemon.log

Version: 1.2.0
"""

import os
import time
import json
import logging
import sys
import requests
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

try:
    import pymysql
    HAS_DB = True
except ImportError:
    HAS_DB = False

try:
    import paho.mqtt.client as mqtt_client
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

# ─── Modbus-Konfiguration ──────────────────────────────────────────────────────
MODBUS_PORT     = '/dev/ttyAMA0'
MODBUS_BAUDRATE = 2400
MODBUS_PARITY   = 'E'
MODBUS_STOPBITS = 1
MODBUS_BYTESIZE = 8
MODBUS_TIMEOUT  = 1
SLAVE_ID        = 1

# ─── Register-Definitionen ────────────────────────────────────────────────────
BRINE_PUMP_REG  = 2136   # System status 3 — Bit 3 = Grundwasserpumpe angefordert
BRINE_PUMP_BIT  = 3
HOST_CTRL_REG   = 2056   # Host-Frequenzsteuerung (0=AUS, 1=EIN)
FREQ_SET_REG    = 2057   # Kompressor-Sollfrequenz [Hz]
FREQ_REAL_REG   = 2118   # Kompressor-Istfrequenz  [Hz]
UNIT_REG        = 2000   # WP Ein/Aus (0=AUS, 1=EIN)
ERROR_REG       = 2137   # Fehlercode
COMPRESSOR_REG  = 2135   # System status 2 — Bit 1 = Kompressor läuft
CURRENT_REG     = 2121   # AC-Strom [A]

TARGET_FREQ     = 80     # Ziel-Kompressorfrequenz [Hz]
COMPRESSOR_BIT  = 1

# ─── Shelly Plug S Gen3 ───────────────────────────────────────────────────────
SHELLY_IP       = "192.168.178.100"

# ─── MQTT Status-Publish ──────────────────────────────────────────────────────
MQTT_BROKER     = "192.168.178.218"
MQTT_PORT       = 1883
MQTT_TOPIC      = "heatmacon"

# ─── Timing ───────────────────────────────────────────────────────────────────
POLL_SEC        = 2    # Intervall für Grundwasserpumpen-Poll + Shelly-Steuerung
DB_SEC          = 60   # Intervall für Register-Lesen + DB-Schreiben

# ─── Proxy-Befehls-Datei ──────────────────────────────────────────────────────
CMD_FILE        = "/tmp/macon_cmd"   # echo on|off|reset > /tmp/macon_cmd
RESET_DELAY_SEC = 2.0

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE        = '/tmp/macon_daemon.log'
LOG_MAX_BYTES   = 204800   # 200 kB, 1 Backup

# ─── Datenbank ────────────────────────────────────────────────────────────────
if HAS_DB:
    DB_CONFIG = {
        "host":        "192.168.178.218",
        "user":        "gh",
        "password":    "a12345",
        "database":    "wagodb",
        "charset":     "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }
PIVOT_TABLE = "macon_pivot"

# Register die alle 60s gelesen, in die DB geschrieben und per MQTT publiziert werden
# Reg 2136 wird alle 2s extra gelesen (Shelly-Steuerung) und separat im MQTT-Payload ergänzt
REGISTER_MAP = {
    2000: ("unit_on_off",     ""),
    2057: ("set_frequency",   "Hz"),
    2118: ("real_frequency",  "Hz"),
    2121: ("ac_current",      "A"),
    2135: ("system_status_2", "bits"),
}


# ─── Logging-Setup ────────────────────────────────────────────────────────────

def setup_logging():
    log = logging.getLogger("macon")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=1)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


# ─── Shelly-Steuerung ─────────────────────────────────────────────────────────

def shelly_set(on: bool, log) -> bool:
    """Schaltet Shelly Plug per HTTP-RPC. True bei Erfolg."""
    try:
        r = requests.post(
            f"http://{SHELLY_IP}/rpc/Switch.Set",
            json={"id": 0, "on": on},
            timeout=5,
        )
        if r.status_code == 200:
            return True
        log.warning(f"Shelly HTTP {r.status_code}: {r.text[:60]}")
    except Exception as e:
        log.error(f"Shelly-Fehler: {e}")
    return False


# ─── Modbus-Hilfe ─────────────────────────────────────────────────────────────

def read_reg(client, addr):
    try:
        res = client.read_holding_registers(addr, 1, slave=SLAVE_ID)
        return res.registers[0] if not res.isError() else None
    except Exception:
        return None


def write_reg(client, addr, val, log):
    try:
        res = client.write_register(address=addr, value=val, slave=SLAVE_ID)
        if res.isError():
            log.warning(f"Schreibfehler Reg {addr}={val}")
            return False
        return True
    except Exception as e:
        log.error(f"Modbus Write Reg {addr}: {e}")
        return False


# ─── Proxy: Befehls-Datei ─────────────────────────────────────────────────────

def process_cmd(client, log):
    """
    Liest /tmp/macon_cmd, führt Befehl per Modbus aus, löscht die Datei.
    Unterstützte Befehle: on | off | reset
    """
    try:
        if not os.path.exists(CMD_FILE):
            return
        with open(CMD_FILE) as f:
            cmd = f.read().strip().lower()
        os.remove(CMD_FILE)
    except Exception:
        return

    if cmd == "on":
        log.info("Proxy-Befehl: WP EIN (Reg 2000 = 1)")
        write_reg(client, UNIT_REG, 1, log)
    elif cmd == "off":
        log.info("Proxy-Befehl: WP AUS (Reg 2000 = 0)")
        write_reg(client, UNIT_REG, 0, log)
    elif cmd == "reset":
        log.info("Proxy-Befehl: Soft-Reset (0 → 2s → 1)")
        write_reg(client, UNIT_REG, 0, log)
        time.sleep(RESET_DELAY_SEC)
        write_reg(client, UNIT_REG, 1, log)
        log.info("Proxy-Befehl: Reset abgeschlossen")
    else:
        log.warning(f"Proxy: unbekannter Befehl '{cmd}' (on|off|reset erwartet)")


# ─── 60s-Tasks ────────────────────────────────────────────────────────────────

def frequency_check(client, log):
    """Setzt Kompressorfrequenz auf TARGET_FREQ wenn sie abweicht."""
    set_f = read_reg(client, FREQ_SET_REG)
    if set_f is None:
        return
    if set_f == TARGET_FREQ:
        return
    if read_reg(client, UNIT_REG) == 0:
        log.info("Freq-Check: WP AUS, übersprungen")
        return
    log.info(f"Freq-Korrektur: {set_f} → {TARGET_FREQ} Hz")
    write_reg(client, HOST_CTRL_REG, 1, log)
    time.sleep(0.5)
    write_reg(client, FREQ_SET_REG, TARGET_FREQ, log)


def error_check(client, log):
    """Auto-Reset: Kompressor läuft aber Strom < 3 A → Soft-Reset."""
    status2 = read_reg(client, COMPRESSOR_REG)
    current = read_reg(client, CURRENT_REG)
    if status2 is None or current is None:
        return
    compressor_on = bool(status2 & (1 << COMPRESSOR_BIT))
    if compressor_on and current < 3:
        log.warning("Auto-Reset: Kompressor AN + Strom < 3 A")
        write_reg(client, UNIT_REG, 0, log)
        time.sleep(2)
        write_reg(client, UNIT_REG, 1, log)
        log.info("Auto-Reset abgeschlossen")


def mqtt_publish(results: dict, shelly_state, log):
    """Veröffentlicht Status-JSON auf MQTT topic 'heatmacon'."""
    if not HAS_MQTT:
        log.debug("paho-mqtt nicht verfügbar, MQTT-Publish übersprungen")
        return
    payload = {"timestamp": datetime.now().isoformat(timespec="seconds")}
    for reg, (name, unit) in REGISTER_MAP.items():
        if reg in results:
            payload[name] = results[reg]
            if unit:
                payload[name + "_unit"] = unit
    payload["shelly_on"] = shelly_state if shelly_state is not None else False
    payload["grundwasserpumpe"] = bool(
        (results.get(BRINE_PUMP_REG, 0) >> BRINE_PUMP_BIT) & 1
    )
    try:
        c = mqtt_client.Client()
        c.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
        c.publish(MQTT_TOPIC, json.dumps(payload), retain=True)
        c.disconnect()
        log.info(f"MQTT: {MQTT_TOPIC} ← {len(payload)} Felder")
    except Exception as e:
        log.error(f"MQTT-Publish-Fehler: {e}")


def db_insert(results: dict, log):
    """Schreibt Register-Werte als neue Zeile in macon_pivot."""
    if not HAS_DB:
        log.debug("DB nicht verfügbar (pymysql fehlt)")
        return
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            ts   = datetime.now()
            cols = ["timestamp"]
            vals = [ts]
            ph   = ["%s"]
            for reg, (name, _) in REGISTER_MAP.items():
                if reg in results:
                    cols.append(name)
                    vals.append(results[reg])
                    ph.append("%s")
            cur.execute(
                f"INSERT INTO {PIVOT_TABLE} ({','.join(cols)}) "
                f"VALUES ({','.join(ph)})",
                vals,
            )
        conn.commit()
        conn.close()
        log.info(f"DB: {len(results)} Register geschrieben ({ts:%H:%M:%S})")
    except Exception as e:
        log.error(f"DB-Fehler: {e}")


# ─── Haupt-Loop ───────────────────────────────────────────────────────────────

def main():
    log = setup_logging()
    log.info("=" * 60)
    log.info("macon_daemon v1.0.0 gestartet")
    log.info(f"  Modbus : {MODBUS_PORT} @ {MODBUS_BAUDRATE} Baud, Slave {SLAVE_ID}")
    log.info(f"  Shelly : http://{SHELLY_IP}")
    log.info(f"  Poll   : alle {POLL_SEC}s  |  DB: alle {DB_SEC}s")
    log.info(f"  DB     : {'aktiv' if HAS_DB else 'DEAKTIVIERT (pymysql fehlt)'}")
    log.info("=" * 60)

    client = ModbusSerialClient(
        port=MODBUS_PORT,
        baudrate=MODBUS_BAUDRATE,
        parity=MODBUS_PARITY,
        stopbits=MODBUS_STOPBITS,
        bytesize=MODBUS_BYTESIZE,
        timeout=MODBUS_TIMEOUT,
    )

    shelly_state = None   # None = Initialzustand unbekannt
    last_db_time = 0.0

    while True:
        try:
            # Verbindung sicherstellen
            if not client.connected:
                log.info("Modbus: verbinde …")
                if not client.connect():
                    log.error("Verbindung fehlgeschlagen – Retry in 10 s")
                    time.sleep(10)
                    continue

            # ── 2s-Task: Proxy-Befehl verarbeiten ───────────────────────────
            process_cmd(client, log)

            # ── 2s-Task: Grundwasserpumpe → Shelly ──────────────────────────
            val = read_reg(client, BRINE_PUMP_REG)
            if val is not None:
                pump = bool((val >> BRINE_PUMP_BIT) & 1)
                if pump and shelly_state is not True:
                    log.info(
                        f"Reg {BRINE_PUMP_REG}=0x{val:04X} Bit{BRINE_PUMP_BIT}=1 "
                        f"→ Grundwasserpumpe angefordert → Shelly EIN"
                    )
                    if shelly_set(True, log):
                        shelly_state = True
                        log.info("Shelly: EIN ✓")
                    else:
                        log.error("Shelly EIN fehlgeschlagen – nächster Versuch in 2 s")
                elif not pump and shelly_state is not False:
                    log.info(
                        f"Reg {BRINE_PUMP_REG}=0x{val:04X} Bit{BRINE_PUMP_BIT}=0 "
                        f"→ keine Anforderung → Shelly AUS"
                    )
                    if shelly_set(False, log):
                        shelly_state = False
                        log.info("Shelly: AUS ✓")
                    else:
                        log.error("Shelly AUS fehlgeschlagen – nächster Versuch in 2 s")
            else:
                log.warning(f"Lesefehler Reg {BRINE_PUMP_REG}")

            # ── 60s-Task: Alle Register + DB + MQTT + Frequenz + Fehler ────
            now = time.time()
            if now - last_db_time >= DB_SEC:
                last_db_time = now
                results = {}
                for reg in REGISTER_MAP:
                    v = read_reg(client, reg)
                    if v is not None:
                        results[reg] = v
                frequency_check(client, log)
                error_check(client, log)
                db_insert(results, log)
                mqtt_publish(results, shelly_state, log)

        except ModbusException as e:
            log.error(f"Modbus-Ausnahme: {e}")
            client.close()
        except KeyboardInterrupt:
            log.info("Daemon durch Benutzer beendet.")
            client.close()
            sys.exit(0)
        except Exception as e:
            log.error(f"Unerwarteter Fehler: {e}")

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()

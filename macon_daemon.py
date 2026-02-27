#!/usr/bin/env python3
"""
macon_daemon.py — Unified Macon WP Daemon

Alle 2s : Modbus Reg 2136 Bit 3 lesen → Grundwasserpumpe angefordert?
          → Shelly Plug S Gen3 per HTTP ein-/ausschalten (nur bei Änderung)

Alle 5s : Alle konfigurierten Register lesen → MySQL-DB schreiben
          + Status-JSON → MQTT topic "heatmacon" (broker 192.168.178.218)
          + Kompressorfrequenz prüfen/setzen + Fehler-Logging

Hinweis: Der Daemon schreibt Reg 2000 (WP EIN/AUS) NICHT.
         WP-Steuerung ausschließlich über das Macon-Panel oder maconread2db.py
         (direkter Modbus-Zugriff, nur bei gestopptem Daemon).

Systemd:  sudo systemctl start macon-daemon
Log:      /tmp/macon_daemon.log

Version: 1.4.0
"""

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
COMPRESSOR_REG  = 2135   # System status 2 — Bit 1 = Kompressor läuft
CURRENT_REG     = 2121   # AC-Strom [A]

TARGET_FREQ     = 80     # Ziel-Kompressorfrequenz [Hz]
COMPRESSOR_BIT  = 1

# ─── Betriebseinstellungen (dauerhaft sicherstellen) ──────────────────────────
WORKING_MODE_REG   = 2001   # Betriebsmodus
WORKING_MODE_DHW   = 5      # 5 = Hot_water (DHW)
# Reg 2047 = "Use frequency reduction near setpoint" — Wert ist ein Hz-Schwellwert,
# KEIN Boolean. Macon setzt diesen intern. Pi schreibt hier NICHT rein.

# ─── Fehlerregister mit Bit-Beschreibungen ────────────────────────────────────
ERROR_REGS = {
    2134: {
        "name": "Error code 1",
        "bits": {
            0: "Outlet water temp sensor",
            1: "Inlet water temp sensor",
            2: "Compressor discharge temp sensor",
            3: "Ambient temp sensor",
            4: "Suction temp sensor",
            5: "Brine inlet temp sensor",
            6: "Brine outlet temp sensor",
            7: "IPM temp sensor",
        },
    },
    2137: {
        "name": "Error code 2",
        "bits": {
            0: "High pressure protection",
            1: "Low pressure protection",
            2: "Inlet water temp error",
            3: "Outlet water temp error",
            4: "Compressor overload",
            5: "Phase loss / reverse",
            6: "AC overvoltage",
            7: "AC undervoltage",
            8: "Ambient temp sensor error",
            9: "IPM overtemp",
            10: "Compressor start failure",
        },
    },
    2138: {
        "name": "Error code 3",
        "bits": {
            0: "Water flow switch error",
            1: "Brine flow switch error",
            2: "Communication error",
            3: "EEPROM error",
        },
    },
}

# ─── Shelly Plug S Gen3 ───────────────────────────────────────────────────────
SHELLY_IP       = "192.168.178.100"

# ─── MQTT Status-Publish ──────────────────────────────────────────────────────
MQTT_BROKER     = "192.168.178.218"
MQTT_PORT       = 1883
MQTT_TOPIC      = "heatmacon"

# ─── Timing ───────────────────────────────────────────────────────────────────
POLL_SEC        = 2    # Intervall für Grundwasserpumpen-Poll + Shelly-Steuerung
DB_SEC          = 5    # Intervall für Register-Lesen + DB-Schreiben

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
# Alle Register die gelesen und per MQTT publiziert werden
REGISTER_MAP = {
    # Steuerung
    2000: ("unit_on_off",       ""),
    2004: ("dhw_setpoint",      "C"),
    2056: ("host_freq_ctrl",    ""),
    2057: ("set_frequency",     "Hz"),
    # Betrieb
    2118: ("real_frequency",    "Hz"),
    2121: ("ac_current",        "A"),
    2133: ("system_status_1",   "bits"),
    2135: ("system_status_2",   "bits"),
    # Wassertemperaturen
    2100: ("water_tank_temp",   "C"),
    2102: ("outlet_water_temp", "C"),
    2103: ("inlet_water_temp",  "C"),
    # Kältekreis
    2104: ("discharge_temp",    "C"),
    2105: ("suction_temp",      "C"),
    # Sole / Grundwasser
    2115: ("brine_inlet_temp",  "C"),
    2116: ("brine_outlet_temp", "C"),
    # Umgebung
    2110: ("ambient_temp",      "C"),
}

# Nur diese Register werden in die DB geschrieben (bestehende Spalten in macon_pivot)
DB_REGS = {2000, 2057, 2118, 2121, 2135}


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


# ─── 5s-Tasks ────────────────────────────────────────────────────────────────

def settings_check(client, log) -> dict:
    """
    Prüft Working_mode (Reg 2001) — schreibt nur bei Abweichung von DHW (5).
    Reg 2047 wird NUR gelesen/geloggt — Macon setzt diesen intern als Hz-Schwelle,
    der Pi schreibt dort nicht rein.
    Gibt {reg: val} zurück für MQTT-Payload.
    """
    extra = {}

    # Reg 2001: Working_mode — Pi darf hier schreiben
    val = read_reg(client, WORKING_MODE_REG)
    if val is None:
        log.warning("Settings-Check: Lesefehler Reg 2001 (Working_mode)")
    elif val != WORKING_MODE_DHW:
        log.warning(f"Settings-Check: Reg 2001 Working_mode ist {val} → setze auf {WORKING_MODE_DHW} (DHW)")
        write_reg(client, WORKING_MODE_REG, WORKING_MODE_DHW, log)
        extra[WORKING_MODE_REG] = WORKING_MODE_DHW
    else:
        log.info(f"Settings-Check: Reg 2001 Working_mode=DHW OK ({val})")
        extra[WORKING_MODE_REG] = val

    # Reg 2047: Freq-reduction-Schwelle — NUR lesen, Macon hat Hoheit
    val47 = read_reg(client, 2047)
    if val47 is not None:
        log.info(f"Settings-Check: Reg 2047 freq_reduction_threshold={val47} Hz (Macon-intern, kein Schreibzugriff)")
        extra[2047] = val47

    return extra


def frequency_check(client, log):
    """
    Setzt Kompressorfrequenz auf TARGET_FREQ — aber erst wenn Macon-Startup abgeschlossen.
    Bedingungen: WP EIN + Reg 2056 lesbar (Host-Control verfügbar) + Kompressor läuft (real_freq > 0).
    Macon hat in den ersten Minuten nach dem Start intern Vorrang.
    """
    if read_reg(client, UNIT_REG) == 0:
        log.info("Freq-Check: WP AUS, übersprungen")
        return

    # Prüfe ob Macon Host-Control bereits freigibt (Reg 2056 muss lesbar sein)
    host_ctrl = read_reg(client, HOST_CTRL_REG)
    if host_ctrl is None:
        log.info("Freq-Check: Reg 2056 nicht lesbar — Macon-Startup läuft noch, warte")
        return

    # Prüfe ob Kompressor wirklich läuft (real_freq > 0)
    real_f = read_reg(client, FREQ_REAL_REG)
    if real_f is None or real_f == 0:
        log.info(f"Freq-Check: Kompressor noch nicht gestartet (real_freq={real_f}), warte")
        return

    # Jetzt Host-Control übernehmen und Frequenz setzen
    set_f = read_reg(client, FREQ_SET_REG)
    if set_f == TARGET_FREQ and host_ctrl == 1:
        log.info(f"Freq-Check: OK ({TARGET_FREQ} Hz, Host-Control aktiv)")
        return

    log.info(f"Freq-Check: real={real_f} Hz, set={set_f} Hz → übernehme Host-Control, setze {TARGET_FREQ} Hz")
    write_reg(client, HOST_CTRL_REG, 1, log)
    time.sleep(0.5)
    write_reg(client, FREQ_SET_REG, TARGET_FREQ, log)


def error_check(client, log):
    """
    Liest alle drei Fehlerregister (2134, 2137, 2138) und loggt aktive Bits.
    Schreibt Reg 2000 NICHT — WP-Steuerung ausschließlich über das Panel.
    """
    for reg, info in ERROR_REGS.items():
        val = read_reg(client, reg)
        if val is None:
            log.warning(f"Fehlerregister {reg} ({info['name']}): Lesefehler")
            continue
        if val == 0:
            log.info(f"Reg {reg} {info['name']}: OK (0x0000)")
        else:
            active = [desc for bit, desc in info["bits"].items() if (val >> bit) & 1]
            log.error(
                f"Reg {reg} {info['name']}: FEHLER 0x{val:04X} — "
                + ", ".join(active) if active else f"unbekannte Bits (0x{val:04X})"
            )

    # Kompressor-Status nur loggen, kein Auto-Reset mehr
    status2 = read_reg(client, COMPRESSOR_REG)
    current = read_reg(client, CURRENT_REG)
    if status2 is not None and current is not None:
        compressor_on = bool(status2 & (1 << COMPRESSOR_BIT))
        if compressor_on and current < 3:
            log.warning(f"Kompressor AN aber Strom < 3 A ({current} A) — kein Auto-Reset")


VOLUMEFLOW_TOPIC = "zenner/volumeflow"   # Retained, L/h vom Zenner-Zähler
POWER_GWP_TOPIC  = "em0/54/power_l2"    # Retained, W Grundwasserpumpe (SDM72D L2)
POWER_HP_TOPIC   = "em0/54/power_l3"    # Retained, W Macon HP (SDM72D L3)


def fetch_mqtt_float(topic: str, log) -> float | None:
    """Liest einen Float-Wert von einem retained MQTT-Topic (max 2s Wartezeit)."""
    if not HAS_MQTT:
        return None
    result = [None]
    def _on_msg(client, userdata, msg):
        try:
            result[0] = float(msg.payload.decode())
        except Exception:
            pass
    try:
        c = mqtt_client.Client()
        c.on_message = _on_msg
        c.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
        c.subscribe(topic)
        c.loop_start()
        deadline = time.time() + 2.0
        while result[0] is None and time.time() < deadline:
            time.sleep(0.05)
        c.loop_stop()
        c.disconnect()
    except Exception as e:
        log.warning(f"MQTT fetch '{topic}' Fehler: {e}")
    return result[0]


def mqtt_publish(results: dict, shelly_state, volumeflow, power_gwp, power_hp, log):
    """Veröffentlicht Status-JSON auf MQTT topic 'heatmacon'."""
    if not HAS_MQTT:
        log.debug("paho-mqtt nicht verfügbar, MQTT-Publish übersprungen")
        return
    payload = {"timestamp": datetime.now().isoformat(timespec="seconds")}
    for reg, (name, unit) in REGISTER_MAP.items():
        if reg in results:
            payload[name] = results[reg]
    payload["shelly_on"] = shelly_state if shelly_state is not None else False
    payload["grundwasserpumpe"] = bool(
        (results.get(BRINE_PUMP_REG, 0) >> BRINE_PUMP_BIT) & 1
    )
    # Betriebsmodus aus Reg 2001 (Working_mode)
    mode_map = {0: "cooling", 1: "underfloor_heating", 2: "fan_coil_heating",
                5: "DHW", 6: "auto"}
    payload["mode"]           = mode_map.get(results.get(WORKING_MODE_REG), "unknown")
    payload["freq_reduction_threshold_hz"] = results.get(2047)
    # Volumenstrom + Leistung aus SDM72D
    payload["volumeflow_lh"]  = volumeflow
    payload["power_hp_w"]     = power_hp    # Macon HP (L3)
    payload["power_gwp_w"]    = power_gwp   # Grundwasserpumpe (L2)
    # Fehlerregister
    for reg, info in ERROR_REGS.items():
        if reg in results:
            payload[info["name"].lower().replace(" ", "_")] = results[reg]
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
            for reg in DB_REGS:
                if reg in results and reg in REGISTER_MAP:
                    cols.append(REGISTER_MAP[reg][0])
                    vals.append(results[reg])
                    ph.append("%s")
            cur.execute(
                f"INSERT INTO {PIVOT_TABLE} ({','.join(cols)}) "
                f"VALUES ({','.join(ph)})",
                vals,
            )
        conn.commit()
        conn.close()
        log.info(f"DB: {len(cols)-1} Spalten geschrieben ({ts:%H:%M:%S})")
    except Exception as e:
        log.error(f"DB-Fehler: {e}")


# ─── Haupt-Loop ───────────────────────────────────────────────────────────────

def main():
    log = setup_logging()
    log.info("=" * 60)
    log.info("macon_daemon v1.4.0 gestartet")
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
                extra = settings_check(client, log)
                results.update(extra)
                frequency_check(client, log)
                error_check(client, log)
                volumeflow = fetch_mqtt_float(VOLUMEFLOW_TOPIC, log)
                power_gwp  = fetch_mqtt_float(POWER_GWP_TOPIC, log)
                power_hp   = fetch_mqtt_float(POWER_HP_TOPIC, log)
                db_insert(results, log)
                mqtt_publish(results, shelly_state, volumeflow, power_gwp, power_hp, log)

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

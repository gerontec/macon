#!/usr/bin/env python3
"""
macon_daemon.py — Unified Macon WP Daemon

Alle 2s : Modbus Reg 2136 Bit 3 lesen → Grundwasserpumpe angefordert?
          → Shelly Plug S Gen3 per HTTP ein-/ausschalten (nur bei Änderung)

Alle 5s : Alle konfigurierten Register lesen → MySQL-DB schreiben
          + Status-JSON → MQTT topic "heatmacon" (broker 192.168.178.218)
          + Kompressorfrequenz prüfen/setzen (PV-Überschuss-abhängig) + Fehler-Logging

PV-Überschuss-Steuerung:
  Topic "sofar" → JSON {"ActivePower_PCC_Total": X.XX, ...} [kW]
  0 kW Überschuss → FREQ_MIN (45 Hz)
  >= PV_EXCESS_MAX_KW → FREQ_MAX (80 Hz = 13,2 kW thermisch)
  Wechselrichter offline → fallback auf FREQ_MIN

Hinweis: Der Daemon schreibt Reg 2000 (WP EIN/AUS) NICHT.
         WP-Steuerung ausschließlich über das Macon-Panel oder maconread2db.py
         (direkter Modbus-Zugriff, nur bei gestopptem Daemon).

Systemd:  sudo systemctl start macon-daemon
Log:      /tmp/macon_daemon.log

Version: 1.5.6

HK-Pumpensteuerung (neu):
  Reg 2133 Bit 0 = heating active → WW-Pumpe EIN via pump_control.py
  Reg 2133 Bit 0 = 0              → beide Pumpen AUTO (WAGO-intern)
  WAGO-Host: 192.168.178.2
"""

import subprocess
import time
import json
import logging
import sys
import requests
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
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
HK_PUMP_REG     = 2133   # System status 1 — Bit 0 = heating active (HK-Pumpe angefordert)
HK_PUMP_BIT     = 0
HOST_CTRL_REG   = 2056   # Host-Frequenzsteuerung (0=AUS, 1=EIN)
FREQ_SET_REG    = 2057   # Kompressor-Sollfrequenz [Hz]
FREQ_REAL_REG   = 2118   # Kompressor-Istfrequenz  [Hz]
UNIT_REG        = 2000   # WP Ein/Aus (0=AUS, 1=EIN)
COMPRESSOR_REG  = 2135   # System status 2 — Bit 1 = Kompressor läuft
CURRENT_REG     = 2121   # AC-Strom [A]

COMPRESSOR_BIT  = 1

# ─── Systemstatus-2 Bit-Dekodierung (Reg 2135) ───────────────────────────────
STATUS2_BITS = {
    0:  "unit_on",
    1:  "compressor_on",
    2:  "fan_high_speed",
    5:  "water_pump_on",
    6:  "fourway_valve",
    7:  "electric_heater",
    8:  "water_flow_switch",
    9:  "high_pressure_switch",
    10: "low_pressure_switch",
    11: "remote_onoff_active",
    12: "mode_change_active",
    13: "threeway_valve_1",
    14: "threeway_valve_2",
}

# ─── Systemstatus-3 Bit-Dekodierung (Reg 2136) ───────────────────────────────
STATUS3_BITS = {
    0:  "solenoid_valve",
    1:  "unloading_valve",
    2:  "oil_return_valve",
    3:  "grundwasserpumpe",
    4:  "brine_frost_protect",
    5:  "defrost_active",
    6:  "refrigerant_recovery",
    7:  "oil_return_active",
    8:  "wired_controller",
    9:  "economy_mode",
    10: "frost_protect_primary",
    11: "frost_protect_secondary",
    12: "sterilization",
    13: "secondary_pump",
    14: "remote_onoff",
}

# ─── Frequenzregelung ────────────────────────────────────────────────────────
FREQ_FIXED        = 55              # Hz Festfrequenz (Optimum aus Sweep)
BRINE_WARM_HZ     = 67              # Hz wenn Sole warm (brine_out > 4°C)
BRINE_MIN_OUT_C   = 5.0             # Schutzgrenze Sole-Ausgang [°C]
DISCHARGE_MAX_C   = 64              # Schutzabschaltung: Heißgas-Temperatur [°C]
RL_MAX_C          = 39              # Abschaltung wenn Rücklauf > 39°C (COP <2, Taktbetrieb)

# ─── Betriebseinstellungen (dauerhaft sicherstellen) ──────────────────────────
WORKING_MODE_REG   = 2001   # Betriebsmodus
WORKING_MODE_DHW   = 5      # 5 = Hot_water (DHW)

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
            8: "OEM status bit (dauerhaft, kein Fehler)",  # 0x0100, immer gesetzt
        },
    },
}

# ─── Shelly Plug S Gen3 ───────────────────────────────────────────────────────
SHELLY_IP       = "192.168.178.100"

# ─── WAGO Pumpensteuerung via pump_control.py ─────────────────────────────────
WAGO_HOST       = "192.168.178.2"
PUMP_CTRL       = "/home/pi/python/pump_control.py"

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

# ── COP-Ø der letzten 2 vollständigen Stunden (stundenscharf wie cop_report.py) ──
COP_2H_QUERY = """
SELECT
    ROUND(
        ((MAX(m.Energy) - MIN(m.Energy)) / 1000.0)
        / NULLIF(MAX(s.total_import_active_energy)
               - MIN(s.total_import_active_energy), 0)
    , 2) AS cop_2h
FROM sdm72d s
JOIN mbus2 m ON m.dth = s.hour
WHERE s.timestamp >= %s
  AND s.timestamp <  %s
  AND s.active_power_l3 > 50
  AND m.Power100W > 0
"""

# Register die alle 60s gelesen, in die DB geschrieben und per MQTT publiziert werden
REGISTER_MAP = {
    # Steuerung
    2000: ("unit_on_off",       ""),
    2003: ("heating_setpoint",  "C"),
    2004: ("dhw_setpoint",      "C"),
    2056: ("host_freq_ctrl",    ""),
    2057: ("set_frequency",     "Hz"),
    # Betrieb
    2118: ("real_frequency",    "Hz"),
    2121: ("ac_current",        "A"),
    2133: ("system_status_1",   "bits"),
    2135: ("system_status_2",   "bits"),
    2136: ("system_status_3",   "bits"),
    2134: ("error_code_1",      "bits"),
    2137: ("error_code_2",      "bits"),
    2138: ("error_code_3",      "bits"),
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
    # ── OEM-Register (undokumentiert, experimentell) ──────────────────────────
    2032: ("evap_coil_temp",    "C"),   # Kältemittel Verdampferseite (signed)
    2039: ("low_side_temp",     "C"),   # Kältemittel Niederdruckseite (signed)
    2107: ("ipm_temp",          "C"),   # Inverter-Modul-Temperatur
    2108: ("cond_coil_temp",    "C"),   # Kondensator-Spulentemperatur
    2120: ("ac_voltage",        "V"),   # AC-Eingangsspannung
    2124: ("eev_primary_steps", "steps"), # Primär-EEV Öffnung
    2125: ("eev_secondary_steps","steps"),# Sekundär-EEV Öffnung
    2128: ("run_hours",         "h"),   # Betriebsstunden gesamt
    2140: ("refrig_lo_temp",    "C"),   # Kältemittel Niederdruckbereich ×0.1 (signed)
}

# Signed 16-bit Register (unsigned raw → signed bei Wert ≥ 32768)
SIGNED_REGS = {2032, 2039}

# Signed + Skalierung: Wert = s16(raw) × scale
SCALED_REGS = {2140: 0.1}

# Bulk-Read-Blöcke: (Startadresse, Anzahl) — 6 Frames statt 25 Einzelreads
READ_BLOCKS = [
    (2000,  5),   # 2000–2004: unit_on_off, dhw_setpoint
    (2032,  8),   # 2032–2039: evap_coil_temp, low_side_temp
    (2056,  2),   # 2056–2057: host_freq_ctrl, set_frequency
    (2100, 29),   # 2100–2128: Temperaturen, EEV, Frequenz, Strom
    (2133,  6),   # 2133–2138: system_status_1/2/3, error_code_1/2/3
    (2140,  1),   # 2140:      refrig_lo_temp
]


# ─── DB-Index-Sicherung ───────────────────────────────────────────────────────

def ensure_db_indexes(log):
    """Legt Performance-Indizes an (einmalig beim Start, ignoriert Duplikate)."""
    if not HAS_DB:
        return
    indexes = [
        ("idx_sdm72d_ts",  "CREATE INDEX idx_sdm72d_ts  ON sdm72d (timestamp)"),
        ("idx_mbus2_dth",  "CREATE INDEX idx_mbus2_dth  ON mbus2  (dth)"),
    ]
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            for name, sql in indexes:
                try:
                    cur.execute(sql)
                    conn.commit()
                    log.info(f"DB-Index '{name}' angelegt")
                except pymysql.err.OperationalError as e:
                    if e.args[0] == 1061:   # Duplicate key name — bereits vorhanden
                        log.debug(f"DB-Index '{name}' existiert bereits — OK")
                    else:
                        log.warning(f"DB-Index '{name}': {e}")
        conn.close()
    except Exception as e:
        log.warning(f"ensure_db_indexes Fehler: {e}")


# ─── COP-Ø letzte 2 Stunden ───────────────────────────────────────────────────

def fetch_avg_cop_2h(log) -> float | None:
    """
    Berechnet den kumulativen COP-Ø der letzten 2 Stunden aus wagodb.
    Methode: ΔEthermisch (mbus2.Energy, Wh→kWh) / ΔEelektrisch (sdm72d.total_import_active_energy)
    über den gesamten Zeitraum — identische Logik wie cop_report.py.
    Gibt None zurück wenn keine/zu wenig Daten vorhanden.
    """
    if not HAS_DB:
        return None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        now  = datetime.now()
        until = now.replace(minute=0, second=0, microsecond=0)          # Beginn laufende Stunde
        since = (until - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        until_str = until.strftime("%Y-%m-%d %H:%M:%S")
        with conn.cursor() as cur:
            cur.execute(COP_2H_QUERY, (since, until_str))
            row = cur.fetchone()
        conn.close()
        if row:
            return row["cop_2h"]   # None wenn WP in den letzten 2h nicht lief
    except Exception as e:
        log.warning(f"fetch_avg_cop_2h Fehler: {e}")
    return None


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


# ─── WAGO Pumpensteuerung ─────────────────────────────────────────────────────

def pump_control_call(pump: str, action: str, log) -> bool:
    """Ruft pump_control.py für WAGO-Pumpensteuerung auf. True bei Erfolg."""
    cmd = [sys.executable, PUMP_CTRL, "--host", WAGO_HOST, pump, action]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            log.info(f"pump_control {pump} {action} OK")
            return True
        log.error(f"pump_control {pump} {action} Fehler: {result.stderr.strip()[:120]}")
    except Exception as e:
        log.error(f"pump_control Ausführungsfehler: {e}")
    return False


# ─── Modbus-Hilfe ─────────────────────────────────────────────────────────────

def read_reg(client, addr):
    try:
        res = client.read_holding_registers(addr, 1, slave=SLAVE_ID)
        return res.registers[0] if not res.isError() else None
    except Exception:
        return None


def read_all_regs(client, log) -> dict:
    """Liest alle REGISTER_MAP-Register in 6 Bulk-Frames (FC03)."""
    results = {}
    for start, count in READ_BLOCKS:
        try:
            r = client.read_holding_registers(start, count, slave=SLAVE_ID)
            if r.isError():
                log.warning(f"Bulk-Read Fehler Block {start}–{start+count-1}")
                continue
            for i, raw in enumerate(r.registers):
                addr = start + i
                if addr not in REGISTER_MAP:
                    continue
                v = raw
                if addr in SCALED_REGS:
                    v = round((v if v < 32768 else v - 65536) * SCALED_REGS[addr], 1)
                elif addr in SIGNED_REGS:
                    v = v if v < 32768 else v - 65536
                results[addr] = v
        except Exception as e:
            log.warning(f"Bulk-Read Exception Block {start}: {e}")
    return results


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


def discharge_protect(client, results: dict, log) -> bool:
    """Schutzabschaltung: WP AUS wenn Heißgas-Temperatur > DISCHARGE_MAX_C.
    Gibt True zurück wenn abgeschaltet wurde."""
    disc = results.get(2104)
    if disc is None or disc <= DISCHARGE_MAX_C:
        return False
    log.error(
        f"SCHUTZ: discharge_temp={disc}°C > {DISCHARGE_MAX_C}°C — WP AUS (Reg 2000=0)"
    )
    write_reg(client, UNIT_REG, 0, log)
    return True


# ─── Fehler-Tracking für schnellen 2s-Poll ───────────────────────────────────
# Bits die dauerhaft gesetzt sind und keinen Fehler bedeuten
_ERR_OEM_MASK = {2138: 0x0100}
_last_err_bits: dict = {}   # {reg: masked_val beim letzten Log-Eintrag}


def _dump_error_snapshot(client, triggered: dict, log):
    """Liest alle Register und speichert Snapshot als JSON-Datei + Log-Eintrag."""
    ts = datetime.now()
    snap = {
        "timestamp": ts.isoformat(timespec="seconds"),
        "triggered": {str(r): f"0x{v:04X}" for r, v in triggered.items()},
        "regs": {},
    }
    reg_names = {**{r: n for r, (n, _) in REGISTER_MAP.items()},
                 **{r: info["name"] for r, info in ERROR_REGS.items()},
                 BRINE_PUMP_REG:  "system_status_3_brine",
                 HOST_CTRL_REG:   "host_freq_ctrl",
                 FREQ_SET_REG:    "set_frequency",
                 WORKING_MODE_REG: "working_mode",
                 COMPRESSOR_REG:  "system_status_2",
                 CURRENT_REG:     "ac_current"}
    all_regs = list(dict.fromkeys(
        list(REGISTER_MAP.keys()) + list(ERROR_REGS.keys()) +
        [BRINE_PUMP_REG, HOST_CTRL_REG, FREQ_SET_REG,
         WORKING_MODE_REG, COMPRESSOR_REG, CURRENT_REG]
    ))
    for r in all_regs:
        v = read_reg(client, r)
        name = reg_names.get(r, str(r))
        snap["regs"][f"{r}_{name}"] = v
    fname = f"/tmp/macon_err_{ts:%Y%m%d_%H%M%S}.json"
    try:
        with open(fname, "w") as f:
            json.dump(snap, f, indent=2)
        log.error(f"Snapshot gespeichert → {fname}")
    except Exception as e:
        log.warning(f"Snapshot Schreibfehler: {e}")
    log.error(f"Snapshot Regs: { {k: v for k, v in snap['regs'].items() if v is not None} }")


def fast_error_check(client, log):
    """
    Fehler-Poll für den 2s-Takt.
    Loggt nur bei Zustandsänderung. Bei neuem Fehler: Snapshot aller Register.
    """
    new_errors = {}
    for reg, info in ERROR_REGS.items():
        val = read_reg(client, reg)
        if val is None:
            continue
        masked = val & ~_ERR_OEM_MASK.get(reg, 0)
        prev   = _last_err_bits.get(reg, 0)
        if masked == prev:
            continue  # keine Änderung
        _last_err_bits[reg] = masked
        if masked == 0:
            log.info(f"Reg {reg} {info['name']}: Fehler gelöscht → OK")
        else:
            known   = [desc for bit, desc in info["bits"].items() if (masked >> bit) & 1]
            unknown = [bit for bit in range(16)
                       if (masked >> bit) & 1 and bit not in info["bits"]]
            msg = f"Reg {reg} {info['name']}: 0x{val:04X} — {', '.join(known) or '?'}"
            if unknown:
                msg += f"  +UNBEKANNTE Bits {unknown}"
            log.error(msg)
            new_errors[reg] = val
    if new_errors:
        _dump_error_snapshot(client, new_errors, log)


_last_cop: float = 0.0


# ─── 5s-Tasks ────────────────────────────────────────────────────────────────

def settings_check(client, log) -> dict:
    """
    Liest Working_mode (Reg 2001) — kein Überschreiben, Panel-Einstellung wird respektiert.
    Gibt {reg: val} zurück für MQTT-Payload.
    """
    extra = {}

    val = read_reg(client, WORKING_MODE_REG)
    if val is None:
        log.warning("Settings-Check: Lesefehler Reg 2001 (Working_mode)")
    else:
        mode_map = {0: "Cooling", 1: "Underfloor", 2: "FanCoil", 5: "DHW", 6: "Auto"}
        log.info(f"Settings-Check: Reg 2001 Working_mode={val} ({mode_map.get(val, '?')})")
        extra[WORKING_MODE_REG] = val

    val47 = read_reg(client, 2047)
    if val47 is not None:
        log.info(f"Settings-Check: Reg 2047 freq_reduction_threshold={val47} Hz")
        extra[2047] = val47

    return extra


def frequency_check(client, log, brine_out_c=None):
    """
    Setzt Kompressorfrequenz.
    Regel: brine_out > 4°C → BRINE_WARM_HZ (67 Hz), sonst FREQ_FIXED (55 Hz).
    """
    if read_reg(client, UNIT_REG) == 0:
        log.info("Freq-Check: WP AUS, übersprungen")
        return

    host_ctrl = read_reg(client, HOST_CTRL_REG)
    if host_ctrl is None:
        log.info("Freq-Check: Reg 2056 nicht lesbar — Macon-Startup läuft noch, warte")
        return

    real_f = read_reg(client, FREQ_REAL_REG)
    if real_f is None or real_f == 0:
        log.info(f"Freq-Check: Kompressor noch nicht gestartet (real_freq={real_f}), warte")
        return

    if brine_out_c is not None and brine_out_c > 4.0:
        target_freq = BRINE_WARM_HZ
        rule   = "BRINE_WARM"
        detail = f"brine_out={brine_out_c:.1f}°C > 4°C"
    else:
        target_freq = FREQ_FIXED
        rule   = "FEST"
        detail = f"brine_out={brine_out_c}°C ≤ 4°C"

    set_f = read_reg(client, FREQ_SET_REG)
    if set_f == target_freq and host_ctrl == 1:
        log.info(f"Freq: {target_freq} Hz  [{rule}: {detail}]")
        return

    log.info(f"Freq-CHANGE: {set_f} → {target_freq} Hz  [{rule}: {detail}]  (real={real_f} Hz)")
    write_reg(client, HOST_CTRL_REG, 1, log)
    time.sleep(0.5)
    write_reg(client, FREQ_SET_REG, target_freq, log)


def error_check(client, log):
    """
    Liest alle drei Fehlerregister (2134, 2137, 2138) und loggt aktive Bits.
    """
    for reg, info in ERROR_REGS.items():
        val = read_reg(client, reg)
        if val is None:
            log.warning(f"Fehlerregister {reg} ({info['name']}): Lesefehler")
            continue
        if val == 0:
            continue
        else:
            known   = [desc for bit, desc in info["bits"].items() if (val >> bit) & 1]
            unknown = [bit for bit in range(16)
                       if (val >> bit) & 1 and bit not in info["bits"]]
            if unknown:
                log.error(f"Reg {reg} {info['name']}: UNBEKANNTE Bits 0x{val:04X} — Bits {unknown}")
            else:
                log.info(f"Reg {reg} {info['name']}: 0x{val:04X} — {', '.join(known)}")

    status2 = read_reg(client, COMPRESSOR_REG)
    current = read_reg(client, CURRENT_REG)
    if status2 is not None and current is not None:
        compressor_on = bool(status2 & (1 << COMPRESSOR_BIT))
        if compressor_on and current < 3:
            log.warning(f"Kompressor AN aber Strom < 3 A ({current} A) — kein Auto-Reset")

POWER_ZENNER_TOPIC = "zenner/power"
VOLUMEFLOW_TOPIC = "zenner/volumeflow"
POWER_GWP_TOPIC  = "em0/power_l2"
POWER_HP_TOPIC   = "em0/power_l3"
TANK_TEMP_TOPIC  = "tuya/heatpump/tank_temp"  # Pufferspeicher °C (retained)


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


def mqtt_publish(results: dict, shelly_state, volumeflow, power_gwp, power_hp, power_zenner, cop_avg_2h, log):
    """Veröffentlicht Status-JSON auf MQTT topic 'heatmacon'."""
    if not HAS_MQTT:
        log.debug("paho-mqtt nicht verfügbar, MQTT-Publish übersprungen")
        return
    payload = {"timestamp": datetime.now().isoformat(timespec="seconds")}
    for reg, (name, unit) in REGISTER_MAP.items():
        if reg in results:
            payload[name] = results[reg]
    payload["shelly_on"] = shelly_state if shelly_state is not None else False
    s2 = results.get(COMPRESSOR_REG, 0)
    for bit, name in STATUS2_BITS.items():
        payload[name] = bool((s2 >> bit) & 1)
    s3 = results.get(BRINE_PUMP_REG, 0)
    for bit, name in STATUS3_BITS.items():
        payload[name] = bool((s3 >> bit) & 1)
    mode_map = {0: "cooling", 1: "underfloor_heating", 2: "fan_coil_heating",
                5: "DHW", 6: "auto"}
    mode_val = results.get(WORKING_MODE_REG)
    payload["mode"] = mode_map.get(mode_val, str(mode_val) if mode_val is not None else "?")
    payload["freq_reduction_threshold_hz"] = results.get(2047)
    payload["volumeflow_m3h"]  = volumeflow
    payload["power_zenner_w"] = power_zenner
    payload["power_hp_w"]     = power_hp
    payload["power_gwp_w"]    = power_gwp
    tank_temp_c = fetch_mqtt_float(TANK_TEMP_TOPIC, log)
    payload["tank_temp_tuya_c"] = tank_temp_c
    payload["cop_avg_2h"]       = cop_avg_2h   # kumulativer COP-Ø der letzten 2h (cop_report.py-Logik)
    for reg, info in ERROR_REGS.items():
        if reg in results:
            payload[info["name"].lower().replace(" ", "_")] = results[reg]
    try:
        c = mqtt_client.Client()
        c.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
        c.publish(MQTT_TOPIC, json.dumps(payload), retain=True)
        c.disconnect()
        try:
            dt = results.get(2102, 0) - results.get(2103, 0)
            vf = volumeflow if volumeflow else 0
            q_total = vf * 1163 * dt
            p_total = (power_hp or 0) + (power_gwp or 0)
            cop = round(q_total / p_total, 2) if p_total > 0 else None
            payload["q_total_w"]  = round(q_total, 1)
            payload["cop"]        = cop
            payload["delta_t_k"]  = dt
            if cop:
                global _last_cop
                _last_cop = cop
                log.info(f"COP: {cop:.2f}  Q={q_total:.0f}W  P={p_total:.0f}W  ΔT={dt}K  flow={vf:.3f}m³/h")
                real_freq = results.get(FREQ_REAL_REG, 0)
                try:
                    with open("/home/pi/cop.csv", "a") as f:
                        ts = datetime.now().isoformat(timespec="seconds")
                        evap  = results.get(2032, "")
                        lo    = results.get(2039, "")
                        cond  = results.get(2108, "")
                        rlo   = results.get(2140, "")
                        ipm   = results.get(2107, "")
                        p_hp  = round(power_hp  or 0)
                        p_gwp = round(power_gwp or 0)
                        f.write(f"{ts},{real_freq},{cop},{round(q_total)},{round(p_total)},{p_hp},{p_gwp},{dt},{vf:.3f},{evap},{lo},{cond},{rlo},{ipm}\n")
                except Exception as e:
                    log.warning(f"COP-File Schreibfehler: {e}")
        except Exception as e:
            log.warning(f"COP-Berechnung Fehler: {e}")
        tank_str = f"  Tank={tank_temp_c:.0f}°C" if tank_temp_c is not None else ""
        log.info(f"MQTT: {MQTT_TOPIC} ← {len(payload)} Felder  {tank_str}")
    except Exception as e:
        log.error(f"MQTT-Publish-Fehler: {e}")


def db_insert(results: dict, cop_2h, log):
    """Schreibt alle bekannten Register-Werte + COP als neue Zeile in macon_pivot."""
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
            for reg in REGISTER_MAP:
                if reg in results:
                    cols.append(REGISTER_MAP[reg][0])
                    vals.append(results[reg])
                    ph.append("%s")
            if cop_2h is not None:
                cols.append("cop_avg_2h")
                vals.append(cop_2h)
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
    log.info("macon_daemon v1.9.0 gestartet")
    log.info(f"  Modbus : {MODBUS_PORT} @ {MODBUS_BAUDRATE} Baud, Slave {SLAVE_ID}")
    log.info(f"  Shelly : http://{SHELLY_IP}")
    log.info(f"  Poll   : alle {POLL_SEC}s  |  DB: alle {DB_SEC}s")
    log.info(f"  DB     : {'aktiv' if HAS_DB else 'DEAKTIVIERT (pymysql fehlt)'}")
    log.info(f"  Freq   : FEST={FREQ_FIXED} Hz  BRINE_WARM={BRINE_WARM_HZ} Hz (brine_out > 4°C)")
    log.info(f"  WAGO   : {WAGO_HOST}  HK-Pumpe: Reg {HK_PUMP_REG} Bit {HK_PUMP_BIT}")
    log.info("=" * 60)

    ensure_db_indexes(log)

    client = ModbusSerialClient(
        port=MODBUS_PORT,
        baudrate=MODBUS_BAUDRATE,
        parity=MODBUS_PARITY,
        stopbits=MODBUS_STOPBITS,
        bytesize=MODBUS_BYTESIZE,
        timeout=MODBUS_TIMEOUT,
    )

    shelly_state      = None
    hk_pump_state     = None   # None = unbekannt, True/False = letzter Zustand
    last_db_time      = 0.0
    last_db_insert    = 0.0
    last_freq_time    = 0.0
    _cached_cop_2h    = None   # COP-Cache, wird beim 60s-Insert aktualisiert

    # COP-Log anlegen falls noch nicht vorhanden
    cop_file = "/home/pi/cop.csv"
    try:
        import os
        if not os.path.exists(cop_file):
            with open(cop_file, "w") as f:
                f.write("timestamp,freq_hz,cop,q_w,p_w,p_macon_w,p_gwp_w,delta_t_k,flow_m3h,evap_coil_c,low_side_c,cond_coil_c,refrig_lo_c,ipm_c\n")
    except Exception:
        pass

    while True:
        try:
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
                        if hk_pump_state is True:
                            if pump_control_call("both", "auto", log):
                                log.info("pump_control: both auto (Shelly AUS)")
                            else:
                                log.error("pump_control both auto fehlgeschlagen – nächster Versuch in 2 s")
                    else:
                        log.error("Shelly AUS fehlgeschlagen – nächster Versuch in 2 s")
            else:
                log.warning(f"Lesefehler Reg {BRINE_PUMP_REG}")

            # ── 2s-Task: HK-Pumpe (Reg 2133 Bit 0) → pump_control.py ────────
            hk_val = read_reg(client, HK_PUMP_REG)
            if hk_val is not None:
                hk = bool((hk_val >> HK_PUMP_BIT) & 1)
                if hk and hk_pump_state is not True:
                    log.info(
                        f"Reg {HK_PUMP_REG}=0x{hk_val:04X} Bit{HK_PUMP_BIT}=1 "
                        f"→ HK-Pumpe angefordert → HK+WW-Pumpe EIN"
                    )
                    if pump_control_call("both", "auto", log):
                        hk_pump_state = True
                    else:
                        log.error("pump_control both auto fehlgeschlagen – nächster Versuch in 2 s")
                elif not hk and hk_pump_state is not False:
                    log.info(
                        f"Reg {HK_PUMP_REG}=0x{hk_val:04X} Bit{HK_PUMP_BIT}=0 "
                        f"→ keine HK-Anforderung → beide Pumpen Automatik"
                    )
                    if pump_control_call("both", "on", log):
                        hk_pump_state = False
                    else:
                        log.error("pump_control both on fehlgeschlagen – nächster Versuch in 2 s")
            else:
                log.warning(f"Lesefehler Reg {HK_PUMP_REG}")

            fast_error_check(client, log)

            # ── 5s-Task: Bulk Register-Lesen + MQTT + Frequenz ──────────────
            now = time.time()
            if now - last_db_time >= DB_SEC:
                last_db_time = now
                results = read_all_regs(client, log)
                extra = settings_check(client, log)
                results.update(extra)
                # Schutzabschaltung Heißgas
                if discharge_protect(client, results, log):
                    time.sleep(POLL_SEC)
                    continue
                if now - last_freq_time >= 55:
                    last_freq_time = now
                    frequency_check(client, log,
                                    brine_out_c=results.get(2116))
                volumeflow   = fetch_mqtt_float(VOLUMEFLOW_TOPIC, log)
                power_gwp    = fetch_mqtt_float(POWER_GWP_TOPIC, log)
                power_hp     = fetch_mqtt_float(POWER_HP_TOPIC, log)
                power_zenner = fetch_mqtt_float(POWER_ZENNER_TOPIC, log)
                # 60s DB-Insert mit aktuellem COP
                if now - last_db_insert >= 60:
                    last_db_insert = now
                    _cached_cop_2h = fetch_avg_cop_2h(log)
                    db_insert(results, _cached_cop_2h, log)
                mqtt_publish(results, shelly_state, volumeflow, power_gwp, power_hp, power_zenner, _cached_cop_2h, log)

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

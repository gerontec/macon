#!/usr/bin/python3
# maconread2db.py — Macon WP Modbus + DB + CLI-Steuerung (Cron-sicher)
# Jede Minute: LESEN + DB + Frequenz + Fehler-Reset
# NUR bei CLI-Befehl: Ein/Aus/Reset
# Version: 2.1.0 (Cron-safe: Kein Status-Änderung ohne Befehl)

import argparse
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

# --- LOGGING ---
LOG_FILE = '/tmp/macon_control.log'
RESET_LOG_FILE = '/tmp/macon_soft_reset.log'
logger = logging.getLogger('macon')
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(message)s')
if not logger.handlers:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=102400, backupCount=1)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- CONFIG ---
DISABLE_DB = False
MODBUS_PORT = '/dev/ttyAMA0'
SLAVE_ADDRESS = 1

# DB
if not DISABLE_DB:
    try:
        import pymysql
    except ImportError:
        logger.error(f"{datetime.now().strftime('%H:%M:%S')} pymysql missing. DB disabled.")
        DISABLE_DB = True

if not DISABLE_DB:
    DB_CONFIG = {
        "host": "192.168.178.218", "user": "gh", "password": "a12345",
        "database": "wagodb", "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    PIVOT_TABLE = "macon_pivot"

# --- REGISTER MAP (wichtige) ---
REGISTER_MAP = {
    2000: ("unit_on_off", ""),
    2057: ("set_frequency", "Hz"),
    2118: ("real_frequency", "Hz"),
    2121: ("ac_current", "A"),
    2135: ("system_status_2", "bits"),
    # ... weitere wie gewünscht
}

# --- STEUERUNG ---
TARGET_FREQ = 80
SET_FREQ_REG = 2057
REAL_FREQ_REG = 2118
HOST_CONTROL_REG = 2056
HOST_CONTROL_ON = 1
RESET_REG = 2000
RESET_OFF = 0
RESET_ON = 1
RESET_DELAY = 2.0
REAL_WAIT_MAX = 10
COMPRESSOR_STATUS_REG = 2135
COMPRESSOR_BIT = 1
COMPRESSOR_MASK = 1 << COMPRESSOR_BIT

# --- MODBUS CLIENT ---
client = ModbusSerialClient(port=MODBUS_PORT, baudrate=2400, parity='E', stopbits=1, bytesize=8, timeout=1)

# --- WRITE SAFE ---
def write_register_safe(address, value):
    time_str = datetime.now().strftime("%H:%M:%S")
    try:
        res = client.write_register(address=address, value=value, slave=SLAVE_ADDRESS)
        if not res.isError():
            logger.info(f"{time_str} Wrote {REGISTER_MAP.get(address, ('Reg'+str(address),''))[0]} = {value}")
            return True
        else:
            logger.error(f"{time_str} Write failed: {res}")
            return False
    except Exception as e:
        logger.error(f"{time_str} Write error: {e}")
        return False

# --- CLI: EIN/AUS/RESET (Proxy via macon_daemon) ---
CMD_FILE = "/tmp/macon_cmd"

def control_unit(command):
    """Schreibt Befehl in CMD_FILE – macon_daemon führt ihn im nächsten 2s-Takt aus."""
    if command not in ['on', 'off', 'reset']:
        return False
    time_str = datetime.now().strftime("%H:%M:%S")
    try:
        with open(CMD_FILE, 'w') as f:
            f.write(command + "\n")
        logger.info(f"{time_str} Proxy → '{command}' → {CMD_FILE} (daemon führt aus)")
        return True
    except Exception as e:
        logger.error(f"{time_str} Proxy-Schreibfehler: {e}")
        return False

# --- FREQUENZ SETZEN (nur bei Abweichung) ---
def write_frequency_if_needed():
    set_freq = read_register(SET_FREQ_REG)
    if set_freq is None:
        return False
    if set_freq == TARGET_FREQ:
        return True

    time_str = datetime.now().strftime("%H:%M:%S")
    
    # Prüfe ob WP läuft (Unit_ON_OFF Register)
    wp_status = read_register(RESET_REG)
    if wp_status == RESET_OFF:
        logger.info(f"{time_str} Frequenz-Set übersprungen: WP ist AUS")
        return True  # Kein Fehler, nur nicht aktiv

    logger.info(f"{time_str} SET-Frequenz {set_freq} → {TARGET_FREQ} Hz")

    write_register_safe(HOST_CONTROL_REG, HOST_CONTROL_ON)
    time.sleep(0.5)
    write_register_safe(SET_FREQ_REG, TARGET_FREQ)
    time.sleep(0.5)

    if read_register(SET_FREQ_REG) != TARGET_FREQ:
        logger.error(f"{time_str} Frequenz-Set fehlgeschlagen (WP läuft aber Schreibfehler)")
        return False

    # Warte auf REAL-Frequenz
    start = time.time()
    while time.time() - start < REAL_WAIT_MAX:
        if read_register(REAL_FREQ_REG) == TARGET_FREQ:
            logger.info(f"{time_str} REAL-Frequenz OK")
            return True
        time.sleep(0.5)
    logger.warning(f"{time_str} REAL-Frequenz Timeout")
    return True

# --- SOFT RESET bei Fehler ---
def perform_soft_reset():
    time_str = datetime.now().strftime("%H:%M:%S")
    logger.warning(f"{time_str} FEHLER-RESET: Compressor ON + Strom < 3A")
    with open(RESET_LOG_FILE, 'a') as f:
        f.write(f"{datetime.now()}: Auto-Reset (Strom < 3A)\n")
    write_register_safe(RESET_REG, RESET_OFF)
    time.sleep(RESET_DELAY)
    write_register_safe(RESET_REG, RESET_ON)
    logger.info(f"{time_str} Auto-Reset done")

# --- READ ---
def read_register(addr):
    try:
        res = client.read_holding_registers(addr, 1, slave=SLAVE_ADDRESS)
        return res.registers[0] if not res.isError() else None
    except:
        return None

# --- DB FUNKTIONEN ---
def fetch_volumeflow_mqtt():
    """Hole aktuellen Volumeflow via MQTT (von zenner2db.py)"""
    time_str = datetime.now().strftime("%H:%M:%S")
    try:
        import paho.mqtt.client as mqtt_client
        
        volumeflow_value = None
        received = False
        
        def on_message(client, userdata, msg):
            nonlocal volumeflow_value, received
            try:
                volumeflow_value = float(msg.payload.decode())
                received = True
            except:
                volumeflow_value = 0.0
        
        client = mqtt_client.Client()
        client.on_message = on_message
        client.connect("192.168.178.218", 1883, 60)
        client.subscribe("zenner/volumeflow")
        
        # Kurz warten auf Nachricht (retained message sollte sofort kommen)
        client.loop_start()
        timeout = 2.0
        start = time.time()
        while not received and (time.time() - start) < timeout:
            time.sleep(0.1)
        
        client.loop_stop()
        client.disconnect()
        
        if received:
            logger.info(f"{time_str} Volumeflow via MQTT: {volumeflow_value} m³/h")
        else:
            logger.warning(f"{time_str} Volumeflow MQTT timeout, using 0.0")
        
        return volumeflow_value if received else 0.0
        
    except Exception as e:
        logger.error(f"{time_str} MQTT Volumeflow error: {e}")
        # Fallback auf 0.0 wenn MQTT nicht verfügbar
        return 0.0

def ensure_pivot_table(cursor):
    """Erstelle Pivot-Tabelle falls nicht vorhanden - MIT DROP"""
    try:
        # Lösche alte Tabelle falls vorhanden
        cursor.execute(f"DROP TABLE IF EXISTS {PIVOT_TABLE}")
        
        # Erstelle neue Tabelle
        cursor.execute(f"""
            CREATE TABLE {PIVOT_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp DATETIME NOT NULL,
                volumeflow FLOAT DEFAULT NULL COMMENT 'Volumenstrom [L/h]',
                INDEX idx_timestamp (timestamp)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        logger.info(f"Created fresh table {PIVOT_TABLE}")
    except Exception as e:
        logger.error(f"Table creation error: {e}")

def ensure_pivot_columns(cursor):
    """Stelle sicher dass alle Spalten existieren"""
    try:
        # Hole existierende Spalten
        cursor.execute(f"SHOW COLUMNS FROM {PIVOT_TABLE}")
        existing_cols = {row['Field'] for row in cursor.fetchall()}
        
        # Füge fehlende Spalten hinzu mit sprechenden Namen
        for reg, (name, unit) in REGISTER_MAP.items():
            col_name = name  # Verwende sprechenden Namen statt reg_XXXX
            if col_name not in existing_cols:
                cursor.execute(f"""
                    ALTER TABLE {PIVOT_TABLE}
                    ADD COLUMN {col_name} INT DEFAULT NULL
                    COMMENT 'Register {reg} [{unit}]'
                """)
                logger.info(f"Added column {col_name} (reg {reg})")
        
        # Volumeflow Spalte wird bereits beim CREATE TABLE erstellt
        # Kein separates ALTER TABLE mehr nötig!
            
    except Exception as e:
        logger.error(f"Column check error: {e}")

def insert_pivot_row(cursor, timestamp, results, volumeflow):
    """Füge neue Zeile mit Pivot-Daten ein"""
    try:
        # Baue dynamische Spaltenliste mit sprechenden Namen
        columns = ['timestamp', 'volumeflow']
        values = [timestamp, volumeflow]
        placeholders = ['%s', '%s']
        
        for reg in REGISTER_MAP.keys():
            if reg in results:
                col_name = REGISTER_MAP[reg][0]  # Sprechender Name statt reg_XXXX
                columns.append(col_name)
                values.append(results[reg][1])
                placeholders.append('%s')
        
        sql = f"""
            INSERT INTO {PIVOT_TABLE} 
            ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
        """
        
        cursor.execute(sql, values)
        
    except Exception as e:
        logger.error(f"Insert error: {e}")

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', nargs='?', choices=['on', 'off', 'reset'],
                        help="Optional: on/off/reset")
    args = parser.parse_args()

    # --- CLI-Steuerung ---
    if args.command:
        success = control_unit(args.command)
        exit(0 if success else 1)

    # --- NORMALMODUS: Cron jede Minute ---
    if not client.connect():
        logger.error(f"{datetime.now().strftime('%H:%M:%S')} Modbus failed")
        return

    try:
        timestamp = datetime.now()
        results = {}

        # Lese wichtige Register
        for reg in REGISTER_MAP.keys():
            val = read_register(reg)
            if val is not None:
                name, unit = REGISTER_MAP[reg]
                results[reg] = (name, val, unit)

        # 1. Frequenz prüfen & setzen
        write_frequency_if_needed()

        # 2. Fehlererkennung: Compressor ON + Strom < 3A
        status2 = results.get(2135, (None, None, None))[1]
        current = results.get(2121, (None, None, None))[1]
        compressor_on = status2 is not None and (status2 & COMPRESSOR_MASK) != 0

        if compressor_on and current is not None and current < 3.0:
            perform_soft_reset()
            time.sleep(15)
            write_frequency_if_needed()

        # 3. DB Insert
        if not DISABLE_DB:
            conn = pymysql.connect(**DB_CONFIG)
            with conn.cursor() as cursor:
                volumeflow = fetch_volumeflow_mqtt()  # MQTT statt DB
                ensure_pivot_table(cursor)
                ensure_pivot_columns(cursor)
                insert_pivot_row(cursor, timestamp, results, volumeflow)
            conn.commit()
            conn.close()

        logger.info(f"{timestamp.strftime('%H:%M:%S')} OK")

    except Exception as e:
        logger.error(f"{datetime.now().strftime('%H:%M:%S')} ERR: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    main()

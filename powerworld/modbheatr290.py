#!/usr/bin/python3
"""
modbheatr290.py - Powerworld Waermepumpe R290
  - Liest Daten via TinyTuya (Modbus Slave 2 nicht erreichbar)
  - Sendet JSON an MQTT topic 'powerworld'
  - Speichert verfuegbare Werte in MariaDB wagodb.heatpr290

TinyTuya DPS-Mapping (Powerworld PW58330, Protokoll v3.4):
  1:   power_on (bool)
  2:   mode_setting (str: smart/manual)
  5:   operating_mode (str: heat/wth/cool)
  6:   temp_unit (str: c/f)
  15:  error_code (int)
  101: water_tank_temperature (°C)
  102: outlet_water_temperature (°C)
  103: ambient_temperature (°C)
  104: exhaust_gas_temperature (°C)
  105: suction_gas_temperature (°C)
  106: external_coil_temperature (°C)
  107: inlet_water_temperature (°C)
  108: inner_coil_temperature (°C)
  109: low_pressure_value (raw/10 bar)
  111: fault_flag_2 (int)
  112: dc_water_pump_speed (%)
  113: compressor_actual_frequency (Hz)
  115: compressor_current (A)
  116: compressor_operating_power (W)
"""

import json
import sys

import tinytuya
import pymysql
import paho.mqtt.publish as mqtt_publish
from pymodbus.client import ModbusTcpClient

# --- TinyTuya ---
TUYA_DEVICE_ID  = 'bf9cb1f29fde9800ca83lm'
TUYA_DEVICE_IP  = '192.168.178.93'
TUYA_DEVICE_KEY = "uUb0=8GOxIJ('ojK"
TUYA_VERSION    = 3.4
TUYA_TIMEOUT    = 5

# --- MQTT ---
MQTT_HOST  = '192.168.178.218'
MQTT_PORT  = 1883
MQTT_TOPIC = 'powerworld'

# --- WAGO SPS ---
WAGO_HOST            = '192.168.178.2'
WAGO_PORT            = 502
WAGO_REG_TANK_TEMP   = 12396  # MW108 - Wassertank-Temperatur (int * 100)
WAGO_REG_OUTLET_TEMP = 12400  # MW112 - Vorlauf/Outlet-Temperatur (int * 100)

# --- MariaDB ---
DB_HOST  = '192.168.178.218'
DB_USER  = 'gh'
DB_PASS  = 'a12345'
DB_NAME  = 'wagodb'
DB_TABLE = 'heatpr290'

# DPS -> (db_column, scale)  scale=None bedeutet Rohwert / String
DPS_MAP = {
    '15':  ('fault_flag_1',                    None),
    '101': ('water_tank_temperature',           1.0),
    '102': ('outlet_water_temperature',         1.0),
    '103': ('ambient_temperature',              1.0),
    '104': ('exhaust_gas_temperature',          1.0),
    '105': ('suction_gas_temperature',          1.0),
    '106': ('external_coil_temperature',        1.0),
    '107': ('inlet_water_temperature',          1.0),
    '108': ('inner_coil_temperature',           1.0),
    '109': ('low_pressure_value',               0.01),
    '111': ('fault_flag_2',                     None),
    '112': ('dc_water_pump_speed',              1.0),
    '113': ('compressor_actual_frequency',      1.0),
    '115': ('compressor_current',               1.0),
    '116': ('compressor_operating_power',       1.0),
}

MODE_MAP = {
    'wth':  (0, 'hot_water'),
    'heat': (1, 'heating'),
    'cool': (2, 'cooling'),
}


# -----------------------------------------------------------------------
def collect_data():
    d = tinytuya.Device(TUYA_DEVICE_ID, TUYA_DEVICE_IP, TUYA_DEVICE_KEY)
    d.set_version(TUYA_VERSION)
    d.set_socketTimeout(TUYA_TIMEOUT)

    status = d.status()
    if not status or 'dps' not in status:
        raise RuntimeError(f"TinyTuya Fehler: {status}")

    dps  = status['dps']
    data = {}

    # Betriebsmodus aus DPS 5
    mode_str = dps.get('5', '')
    mode_num, mode_name = MODE_MAP.get(mode_str, (None, mode_str))
    if mode_num is not None:
        data['mode'] = mode_num
    data['mode_name'] = mode_name

    # Power-on Status -> heating_active / hot_water_active / cooling_active
    power_on = bool(dps.get('1', False))
    data['hot_water_active'] = int(power_on and mode_str == 'wth')
    data['heating_active']   = int(power_on and mode_str == 'heat')
    data['cooling_active']   = int(power_on and mode_str == 'cool')
    # compressor als Proxy fuer Betrieb (kein direktes DPS)
    data['compressor'] = int(power_on)

    # Analoge Messwerte aus DPS_MAP
    for dps_key, (col, scale) in DPS_MAP.items():
        val = dps.get(dps_key)
        if val is None:
            continue
        if scale is not None:
            data[col] = round(val * scale, 2)
        else:
            data[col] = val

    return data


# -----------------------------------------------------------------------
def send_mqtt(data):
    payload = json.dumps(data, ensure_ascii=False)
    mqtt_publish.single(
        topic=MQTT_TOPIC,
        payload=payload,
        hostname=MQTT_HOST,
        port=MQTT_PORT,
        qos=1,
    )
    return payload


def write_wago(tank_temp_c, outlet_temp_c):
    """Schreibt Tank- und Outlet-Temperatur (int*100) in WAGO MW108/MW112."""
    wc = ModbusTcpClient(WAGO_HOST, port=WAGO_PORT, timeout=3)
    if not wc.connect():
        raise ConnectionError(f"WAGO TCP-Verbindung zu {WAGO_HOST} fehlgeschlagen")
    try:
        for addr, val_c in [(WAGO_REG_TANK_TEMP, tank_temp_c), (WAGO_REG_OUTLET_TEMP, outlet_temp_c)]:
            r = wc.write_register(address=addr, value=int(round(val_c * 100)), slave=0)
            if r.isError():
                raise IOError(f"WAGO write_register {addr} Fehler: {r}")
    finally:
        wc.close()


def insert_db(data):
    # Nur Spalten einfuegen die tatsaechlich vorhanden sind (flexible Spaltenliste)
    SKIP_COLS = {'mode_name'}
    columns, values = [], []
    for col, val in data.items():
        if col in SKIP_COLS:
            continue
        columns.append(f'`{col}`')
        values.append(val)

    sql = (f"INSERT INTO `{DB_TABLE}` ({', '.join(columns)}) "
           f"VALUES ({', '.join(['%s'] * len(values))})")

    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    try:
        cur = conn.cursor()
        cur.execute(sql, values)
        conn.commit()
    finally:
        conn.close()


# -----------------------------------------------------------------------
def main():
    try:
        data = collect_data()
    except Exception as e:
        print(f"FEHLER: TinyTuya Daten konnten nicht gelesen werden: {e}", file=sys.stderr)
        sys.exit(1)

    if not data:
        print("FEHLER: Keine Daten von TinyTuya erhalten.", file=sys.stderr)
        sys.exit(1)

    # MQTT
    try:
        send_mqtt(data)
        print(f"MQTT gesendet -> {MQTT_HOST} {MQTT_TOPIC}")
    except Exception as e:
        print(f"MQTT Fehler: {e}", file=sys.stderr)

    # WAGO SPS
    tank_temp   = data.get('water_tank_temperature')
    outlet_temp = data.get('outlet_water_temperature')
    if tank_temp is not None and outlet_temp is not None:
        try:
            write_wago(tank_temp, outlet_temp)
            print(f"WAGO MW108={tank_temp}C  MW112={outlet_temp}C")
        except Exception as e:
            print(f"WAGO Fehler: {e}", file=sys.stderr)

    # MariaDB (jede Minute - TinyTuya liefert weniger Spalten als Modbus)
    try:
        insert_db(data)
        print(f"DB gespeichert -> {DB_NAME}.{DB_TABLE}  ({len([c for c in data if c != 'mode_name'])} Spalten)")
    except Exception as e:
        print(f"DB Fehler: {e}", file=sys.stderr)

    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

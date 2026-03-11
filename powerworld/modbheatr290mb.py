#!/usr/bin/python3
"""
modbheatr290.py - Powerworld Waermepumpe R290
  - Liest Modbus-Register via RS485
  - Sendet JSON an MQTT topic 'powerworld'
  - Speichert Werte in MariaDB wagodb.heatpr290

Getestete Verbindungsparameter:
  Port /dev/ttyUSB33, 9600 baud, parity=N, FC3 (holding), 500ms Delay

Zwei getrennte Reads (R290 antwortet bei grossem Bulk instabil):
  Block 1: Bit/Fault-Register  0x0003-0x000D (11 Register)
  Block 2: Analog-Register     0x000E-0x0047 (58 Register)
"""

import glob
import json
import os
import sys
from time import sleep

import pymysql
import paho.mqtt.publish as mqtt_publish
from pymodbus.client import ModbusSerialClient, ModbusTcpClient

# --- Modbus ---
SERIAL_PORT = '/dev/ttyUSB33'
BAUDRATE    = 9600
PARITY      = 'N'
SLAVE_ID    = 1
DELAY_S     = 0.5   # Pause zwischen Anfragen (RS485 half-duplex)
MB_TIMEOUT  = 1.5

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

# -----------------------------------------------------------------------
# Register-Definitionen
# -----------------------------------------------------------------------

# Bit-Maps fuer Status/Flag-Register
WORKING_STATUS_BITS = {
    0: 'hot_water_active',
    2: 'heating_active',
    3: 'cooling_active',
    4: 'dc_fan_1_valid',
    5: 'dc_fan_2_valid',
    7: 'defrosting_active',
}
OUTPUT1_BITS = {0: 'compressor', 5: 'fan_motor', 6: 'four-way_valve'}
OUTPUT2_BITS = {0: 'chassis_electric_heating', 5: 'a/c_electric_heating',
                6: 'three-way_valve', 7: 'water_tank_electric_heating'}
OUTPUT3_BITS = {0: 'circulation_pump', 1: 'crankshaft_electric_heating'}

# Fault-Register: Adresse -> db_column (raw int gespeichert)
FAULT_REGISTERS = [
    (0x0007, 'fault_flag_1'),
    (0x0008, 'fault_flag_2'),
    (0x0009, 'fault_flag_3'),
    (0x000A, 'fault_flag_4'),
    (0x000B, 'fault_flag_5'),
    (0x000C, 'fault_flag_6'),
    (0x000D, 'fault_flag_7'),
]

# Analoge Werte: Adresse -> (db_column, Multiplikator)
ANALOG_REGISTERS = [
    (0x000E, 'inlet_water_temperature',             0.1),
    (0x000F, 'water_tank_temperature',              0.5),
    (0x0011, 'ambient_temperature',                 0.5),
    (0x0012, 'outlet_water_temperature',            0.5),
    (0x0015, 'suction_gas_temperature',             0.5),
    (0x0016, 'external_coil_temperature',           0.5),
    (0x001A, 'inner_coil_temperature',              0.5),
    (0x001B, 'exhaust_gas_temperature',             1.0),
    (0x001C, 'expansion_valve_opening',             1.0),
    (0x001E, 'compressor_actual_frequency',         1.0),
    (0x0023, 'compressor_current',                  1.0),
    (0x0028, 'low_pressure_conversion_temperature', 0.1),
    (0x002A, 'dc_water_pump_speed',                 1.0),
    (0x002B, 'low_pressure_value',                  0.006895),  # psi -> bar
    (0x002E, 'compressor_operating_power',          1.0),
    (0x003F, 'parameter_flag_1',                    1.0),
    (0x0040, 'control_flag_1',                      1.0),
    (0x0041, 'control_flag_2',                      1.0),
    (0x0043, 'mode',                                1.0),
    (0x0044, 'defrost_frequency',                   1.0),
    (0x0045, 'defrost_cycle',                       1.0),
    (0x0046, 'defrost_time',                        1.0),
    (0x0047, 'action_cycle_of_main_expansion_valve',3.0),
]

MODE_NAMES = {
    0: 'hot_water', 1: 'heating', 2: 'cooling',
    3: 'hot_water+heating', 4: 'hot_water+cooling',
}

from CoolProp.CoolProp import PropsSI as _PropsSI
import math as _math

# --- Heizkurve (OSCAT HEAT_TEMP, DIN 4703 / Recknagel) ---
HC_T_INT_CONFIG  = 20.0   # Auslegungs-Raumtemperatur °C
HC_T_EXT_CONFIG  = -12.0  # Norm-Außentemperatur °C (Deutschland)
HC_TY_CONFIG     = 46.0   # Vorlauftemperatur bei Auslegungspunkt °C
HC_T_DIFF        = 6.0    # Spreizung VL/RL bei Auslegung °C
HC_C             = 1.1    # Exponent (1.1 Fußboden, 1.33 Heizkörper)
HC_TY_MIN        = 20.0   # Minimale Vorlauftemperatur °C
HC_TY_MAX        = 44.0   # Maximale VL °C — begrenzt durch Warmwasser-Boiler (P03)
HC_H             = 3.0    # Hysterese: Heizung aus wenn T_EXT + H > T_Raum
HC_MISCHER_OFFSET = 2.0   # Mischer-Offset: WP muss +2K über HK-Sollwert liefern


def heat_curve_vl(t_ext, t_int=20.0):
    """Vorlauftemperatur-Sollwert nach OSCAT HEAT_TEMP (DIN 4703).
    Inkl. +2K Mischeroffset. Begrenzt auf HC_TY_MAX (Warmwasser-Boiler).
    Gibt None zurück wenn keine Heizung nötig."""
    if t_ext is None:
        return None
    t_ext = float(t_ext)
    tr = float(t_int)
    if t_ext + HC_H > tr:
        return None  # kein Heizbedarf
    tx = (tr - t_ext) / (HC_T_INT_CONFIG - HC_T_EXT_CONFIG)
    ty = tr + HC_T_DIFF * 0.5 * tx + (HC_TY_CONFIG - HC_T_DIFF * 0.5 - tr) * (tx ** (1.0 / HC_C))
    ty += HC_MISCHER_OFFSET
    return round(max(HC_TY_MIN, min(HC_TY_MAX, ty)), 1)


def read_pv_surplus(path='/tmp/r.txt'):
    """Liest PV-Überschuss (W) aus /tmp/r.txt. Gibt None zurück wenn nicht vorhanden oder ungültig."""
    try:
        with open(path) as f:
            return float(f.read().strip())
    except Exception:
        return None


def r290_t_sat(p_bar_g):
    """Sättigungstemperatur R290 aus Niederdruck (bar gauge) via CoolProp."""
    if p_bar_g is None or p_bar_g <= 0:
        return None
    try:
        p_pa = (float(p_bar_g) + 1.01325) * 1e5
        t_k  = _PropsSI('T', 'P', p_pa, 'Q', 0, 'Propane')
        return round(t_k - 273.15, 2)
    except Exception:
        return None


# -----------------------------------------------------------------------
def get_prolific_ports():
    """Nur ttyUSB* Ports zurückgeben, die von einem Prolific-Chip (VID 067b) stammen."""
    ports = []
    for path in sorted(glob.glob('/sys/class/tty/ttyUSB*/device')):
        try:
            vid_path = os.path.realpath(path + '/../../idVendor')
            with open(vid_path) as f:
                if f.read().strip().lower() == '067b':
                    dev = '/dev/' + os.path.basename(os.path.dirname(path))
                    ports.append(dev)
        except Exception:
            pass
    return ports


def find_modbus_port():
    """Prolific ttyUSB* Ports testen, denjenigen zurückgeben der auf Slave ID antwortet."""
    ports = get_prolific_ports()
    if not ports:
        print("Keine Prolific ttyUSB-Geräte gefunden", file=sys.stderr)
        return None
    for port in ports:
        try:
            client = ModbusSerialClient(
                port=port, baudrate=BAUDRATE,
                parity=PARITY, stopbits=1, bytesize=8,
                timeout=2,
            )
            if not client.connect():
                client.close()
                continue
            sleep(DELAY_S)
            r = client.read_holding_registers(address=0x0003, count=1, slave=SLAVE_ID)
            client.close()
            if not r.isError():
                print(f"Wärmepumpe gefunden auf {port}")
                return port
        except Exception:
            pass
    print(f"Wärmepumpe (Slave {SLAVE_ID}) auf keinem Prolific-Port gefunden: {ports}", file=sys.stderr)
    return None


# -----------------------------------------------------------------------
def decode_bits(raw, bit_map):
    return {name: int(bool(raw & (1 << bit))) for bit, name in bit_map.items()}


def _read_block(client, start, count, label):
    """Liest einen Modbus-Block mit Pause davor. Gibt Register-Liste oder None zurueck."""
    sleep(DELAY_S)
    r = client.read_holding_registers(address=start, count=count, slave=SLAVE_ID)
    if r.isError():
        print(f"Read Fehler [{label} 0x{start:04X}+{count}]: {r}", file=sys.stderr)
        return None
    return r.registers


def collect_data(client):
    data = {}

    BIT_START = 0x0003
    BIT_COUNT = 0x000D - BIT_START + 1   # 11

    regs_bit = _read_block(client, BIT_START, BIT_COUNT, "Bit/Fault")
    if regs_bit is None:
        print("Bit-Block: Lesefehler, uebersprungen", file=sys.stderr)
    else:
        def get_bit(addr):
            return regs_bit[addr - BIT_START]

        data.update(decode_bits(get_bit(0x0003), WORKING_STATUS_BITS))
        data.update(decode_bits(get_bit(0x0004), OUTPUT1_BITS))
        data.update(decode_bits(get_bit(0x0005), OUTPUT2_BITS))
        data.update(decode_bits(get_bit(0x0006), OUTPUT3_BITS))

        for addr, col in FAULT_REGISTERS:
            data[col] = get_bit(addr)

    ANA_START = 0x000E
    ANA_COUNT = 0x0047 - ANA_START + 1   # 58

    regs_ana = _read_block(client, ANA_START, ANA_COUNT, "Analog")
    if regs_ana is None:
        print("Analog-Block: Lesefehler, uebersprungen", file=sys.stderr)
    else:
        def get_ana(addr):
            return regs_ana[addr - ANA_START]

        invalid = []
        for addr, col, mult in ANALOG_REGISTERS:
            raw = get_ana(addr)
            if raw == 0xFFFF:
                data[col] = None
                invalid.append(col)
            else:
                data[col] = round(raw * mult, 2)

        if invalid:
            print(f"0xFFFF (kein Sensor): {', '.join(invalid)}", file=sys.stderr)

    if 'mode' in data:
        data['mode_name'] = MODE_NAMES.get(int(data.get('mode') or 0), 'unknown')

    # Überhitzung berechnen: T_sat aus Niederdruck (R290), ΔT = T_Sauggas - T_sat
    p_bar_g  = data.get('low_pressure_value')
    t_sauggas = data.get('suction_gas_temperature')
    t_sat = r290_t_sat(p_bar_g)
    data['t_sat'] = t_sat
    if t_sat is not None and t_sauggas is not None:
        data['superheat'] = round(t_sauggas - t_sat, 2)
    else:
        data['superheat'] = None

    data['pv_surplus_w'] = read_pv_surplus()

    t_ext = data.get('ambient_temperature')
    data['vl_soll'] = heat_curve_vl(t_ext)

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


def _ensure_columns(conn):
    """Legt fehlende Spalten in der DB-Tabelle an (idempotent)."""
    cur = conn.cursor()
    for col, coltype in [('t_sat', 'FLOAT'), ('superheat', 'FLOAT'), ('pv_surplus_w', 'FLOAT'), ('vl_soll', 'FLOAT')]:
        cur.execute(
            f"ALTER TABLE `{DB_TABLE}` ADD COLUMN IF NOT EXISTS `{col}` {coltype} DEFAULT NULL"
        )
    conn.commit()


def insert_db(data):
    columns = []
    values  = []
    for col, val in data.items():
        if col == 'mode_name':
            continue
        columns.append(f'`{col}`')
        values.append(val)

    sql = (f"INSERT INTO `{DB_TABLE}` ({', '.join(columns)}) "
           f"VALUES ({', '.join(['%s'] * len(values))})")

    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    try:
        _ensure_columns(conn)
        cur = conn.cursor()
        cur.execute(sql, values)
        conn.commit()
    finally:
        conn.close()


# -----------------------------------------------------------------------
def main():
    from datetime import datetime
    do_db = True

    port = find_modbus_port()
    if not port:
        print("FEHLER: Kein Prolific-Port mit Wärmepumpe gefunden.", file=sys.stderr)
        sys.exit(1)

    client = ModbusSerialClient(
        port=port, baudrate=BAUDRATE,
        parity=PARITY, stopbits=1, bytesize=8,
        timeout=MB_TIMEOUT,
    )
    if not client.connect():
        print("FEHLER: Modbus-Verbindung fehlgeschlagen.", file=sys.stderr)
        sys.exit(1)

    sleep(0.5)

    try:
        data = collect_data(client)
    finally:
        client.close()

    if not data:
        print("FEHLER: Keine Daten gelesen (beide Modbus-Bloecke fehlgeschlagen).", file=sys.stderr)
        sys.exit(1)

    try:
        send_mqtt(data)
        print(f"MQTT gesendet -> {MQTT_HOST} {MQTT_TOPIC}")
    except Exception as e:
        print(f"MQTT Fehler: {e}", file=sys.stderr)

    tank_temp   = data.get('water_tank_temperature')
    outlet_temp = data.get('outlet_water_temperature')
    if tank_temp and outlet_temp is not None:
        try:
            write_wago(tank_temp, outlet_temp)
            print(f"WAGO MW108={tank_temp}C  MW112={outlet_temp}C")
        except Exception as e:
            print(f"WAGO Fehler: {e}", file=sys.stderr)

    if do_db:
        try:
            insert_db(data)
            print(f"DB gespeichert -> {DB_NAME}.{DB_TABLE}")
        except Exception as e:
            print(f"DB Fehler: {e}", file=sys.stderr)

    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

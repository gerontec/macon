#!/usr/bin/python3
import datetime
import time
import struct
import binascii
import logging
import pymysql
import paho.mqtt.client as mqtt
from pymodbus.client import ModbusSerialClient as ModbusClient
from filelock import FileLock

# Konfiguration
MYSQL_HOST = "192.168.178.218"
MYSQL_USER = "gh"
MYSQL_PASSWORD = "a12345"
MYSQL_DATABASE = "wagodb"
MODBUS_PORT = "/dev/ttyUSB33"
MODBUS_BAUDRATE = 9600
MODBUS_SLAVE_ID = 1
LOCK_FILE = "/tmp/sdm72d.lck"
MQTT_BROKER = "localhost"
MQTT_BASE_TOPIC = "em0"

# Logging einrichten
logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.INFO)

# Modbus-Register-Map (Auszug – die wichtigen für uns)
modbus_registers = {
    'total_active_power': 53,     # 30053 Total System Power       → W
    'total_apparent_power': 57,   # 30057 Total System VA
    'total_reactive_power': 61,   # 30061 Total System VAr
    # ... alle anderen Register bleiben erhalten
    # (du kannst die komplette Liste einfach drin lassen)
}

# MySQL-Tabelle erstellen (bleibt unverändert – nur der relevante Teil gezeigt)
def create_sdm72d_table():
    # Dein bestehender Code – unverändert
    # ...
    pass  # oder komplette Funktion hier einfügen

# Modbus-Werte lesen (komplett oder nur die drei Werte – hier optimierte Variante)
def read_modbus_power_values():
    """Nur die drei Gesamtleistungen lesen – deutlich schneller"""
    data = {}
    msys = ModbusClient(
        port=MODBUS_PORT,
        baudrate=MODBUS_BAUDRATE,
        parity='N',
        stopbits=1,
        bytesize=8,
        timeout=1
    )
    if not msys.connect():
        log.error("Modbus-Verbindung fehlgeschlagen")
        return None

    try:
        for param, reg in {
            'total_active_power':   53,   # 30053 Total System Power  [W]
            'total_apparent_power': 57,   # 30057 Total System VA
            'total_reactive_power': 61,   # 30061 Total System VAr
            'power_l1':             13,   # 30013 Phase 1 [W]
            'power_l2':             15,   # 30015 Phase 2 [W] → Grundwasserpumpe
            'power_l3':             17,   # 30017 Phase 3 [W] → Macon HP
        }.items():
            result = msys.read_input_registers(reg - 1, 2, slave=MODBUS_SLAVE_ID)
            if not result.isError():
                v1 = hex(result.registers[0])[2:].zfill(4)
                v2 = hex(result.registers[1])[2:].zfill(4)
                myfloat = struct.unpack('>f', binascii.unhexlify(v1 + v2))[0]
                data[param] = round(myfloat, 3)
            else:
                log.warning(f"Register-Fehler bei {param} (Register {reg})")
                data[param] = None
        return data
    except Exception as e:
        log.error(f"Modbus-Lesefehler: {e}")
        return None
    finally:
        msys.close()

# MQTT Publish – jetzt mit drei Werten
def publish_to_mqtt(data):
    if not data or data.get('total_active_power') is None:
        log.warning("Keine gültigen Leistungsdaten zum Senden")
        return

    try:
        client = mqtt.Client()
        client.connect(MQTT_BROKER, 1883, 60)
        client.loop_start()

        # Einzelne Topics – sauber getrennt und leicht lesbar für Home Assistant & Co.
        client.publish(f"{MQTT_BASE_TOPIC}/power",    str(data['total_active_power']),   qos=1, retain=True)
        client.publish(f"{MQTT_BASE_TOPIC}/apparent", str(data['total_apparent_power']), qos=1, retain=True)
        client.publish(f"{MQTT_BASE_TOPIC}/reactive", str(data['total_reactive_power']), qos=1, retain=True)
        client.publish(f"{MQTT_BASE_TOPIC}/power_l1", str(data.get('power_l1', '')),     qos=1, retain=True)
        client.publish(f"{MQTT_BASE_TOPIC}/power_l2", str(data.get('power_l2', '')),     qos=1, retain=True)  # Grundwasserpumpe
        client.publish(f"{MQTT_BASE_TOPIC}/power_l3", str(data.get('power_l3', '')),     qos=1, retain=True)  # Macon HP

        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        log.info(
            f"MQTT → Gesamt: {data['total_active_power']:7.1f} W  "
            f"L2(GWP): {data.get('power_l2', '?'):>8}W  "
            f"L3(Macon): {data.get('power_l3', '?'):>8}W"
        )

    except Exception as e:
        log.error(f"MQTT Fehler: {e}")

# Deine MySQL-Funktion bleibt unverändert
def write_to_mysql(data):
    # Dein bestehender Code – komplett erhalten
    # ...
    pass

# ────────────────────────────────────────────────
# Hauptprogramm
# ────────────────────────────────────────────────
def main():
    with FileLock(LOCK_FILE):
        create_sdm72d_table()           # bleibt

        # Variante A: Nur die drei Leistungen schnell lesen + MQTT
        power_data = read_modbus_power_values()
        if power_data:
            publish_to_mqtt(power_data)

        # Variante B: Komplettes Auslesen + MySQL + MQTT (wie bisher)
        # full_data = read_modbus_registers()   # deine alte Funktion
        # if full_data:
        #     write_to_mysql(full_data)
        #     publish_to_mqtt(full_data)

if __name__ == "__main__":
    main()

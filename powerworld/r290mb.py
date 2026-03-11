#!/usr/bin/python3
# -*- coding: utf-8 -*-
from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from datetime import datetime
import sys
import pymysql
import json
import paho.mqtt.client as mqtt
from sqlalchemy import create_engine, text

# === KONFIGURATION ===
SLAVE_ID = 1
PORT = '/dev/ttyUSB3'
BAUDRATE = 9600

WAGO_IP = '192.168.178.2'
WAGO_PORT = 502

MQTT_HOST = "192.168.178.218"
MQTT_PORT = 1883
MQTT_TOPIC = "r290/heatpump/all"

DB_CONFIG = {'host': '10.8.0.1', 'user': 'gh', 'password': 'a12345', 'database': 'wagodb'}

def to_signed(val):
    return val if val < 32768 else val - 65536

# === HAUPTTEIL ===
client = ModbusSerialClient(method='rtu', port=PORT, baudrate=BAUDRATE, timeout=1.5)
if not client.connect():
    sys.exit(1)

now = datetime.now()
data = {'zeitstempel': now.strftime("%Y-%m-%d %H:%M:%S")}

try:
    # 1. Status lesen
    r = client.read_holding_registers(0x3, 11, slave=SLAVE_ID)
    if not r.isError():
        reg = r.registers
        keys = ['status_word','output_flags_1','output_flags_2','output_flags_3',
                'fault_flags_1','fault_flags_2','fault_flags_3','fault_flags_4',
                'fault_flags_5','fault_flags_6','fault_flags_7']
        for i, key in enumerate(keys): data[key] = reg[i]

    # 2. Temperaturen lesen
    r = client.read_holding_registers(0xE, 15, slave=SLAVE_ID)
    if not r.isError():
        t = r.registers
        data['temp_inlet'] = to_signed(t[0]) * 0.1
        data['temp_tank'] = to_signed(t[1]) * 0.5
        data['temp_ambient'] = to_signed(t[3]) * 0.5
        data['temp_outlet'] = to_signed(t[4]) * 0.5
        data['temp_suction'] = to_signed(t[7]) * 0.5
        data['temp_ext_coil'] = to_signed(t[8]) * 0.5
        data['temp_int_coil'] = to_signed(t[12]) * 0.5
        data['temp_exhaust'] = to_signed(t[13])

    # 3. Technik & Kompressor
    r = client.read_holding_registers(0x1C, 20, slave=SLAVE_ID)
    if not r.isError():
        s = r.registers
        data.update({
            'exp_valve_main': s[0], 'comp_freq_actual': s[2],
            'dc_bus_voltage': s[5], 'comp_current': s[7], 'comp_freq_target': s[8],
            'fan1_speed': s[10], 'pump_speed': s[14], 'low_pressure_bar': s[15] * 0.1,
            'comp_power': s[18], 'inverter_fault_low': s[3], 'inverter_fault_high': s[4]
        })

    # 4. Modus & Sollwerte
    r = client.read_holding_registers(0x3F, 5, slave=SLAVE_ID)
    if not r.isError():
        data.update({'param_flag': r.registers[0], 'mode': r.registers[4]})
except Exception as e:
    print(f"Fehler beim Auslesen: {e}")
finally:
    client.close()

# === LOGIK: VERSAND NUR WENN TANK > 0 ===
tank_temp = data.get('temp_tank', 0)

if tank_temp and tank_temp > 0:
    # A) MQTT Versand (Alle Messwerte)
    try:
        mq = mqtt.Client()
        mq.connect(MQTT_HOST, MQTT_PORT, 60)
        mq.publish(MQTT_TOPIC, json.dumps(data), retain=True)
        mq.disconnect()
        print(f"✓ MQTT: Alle Messwerte an {MQTT_HOST} gesendet")
    except: print("✗ MQTT Fehler")

    # B) WAGO Versand (Nur Tank-Temp)
    try:
        wc = ModbusTcpClient(WAGO_IP, port=WAGO_PORT)
        if wc.connect():
            wc.write_register(12396, int(tank_temp * 100), slave=0)
            wc.close()
            print(f"✓ WAGO: {tank_temp}°C an SPS übertragen")
    except: print("✗ WAGO Fehler")
else:
    print(f"○ Info: Versand übersprungen (Tank-Temp: {tank_temp}°C)")

# === DATENBANK (Immer speichern für Historie) ===
try:
    conn = pymysql.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        # Erstellt dynamisch die Spaltennamen und Platzhalter aus dem Dictionary
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO heat_powerw ({cols}) VALUES ({placeholders})"
        cur.execute(sql, list(data.values()))
    conn.commit()
    conn.close()
    print("✓ DB: Eintrag gespeichert")
except Exception as e:
    print(f"✗ DB Fehler: {e}")

print(f"DONE: {data['zeitstempel']} | Tank: {tank_temp}°C")

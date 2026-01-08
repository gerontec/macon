#!/usr/bin/python3
# maconread2db.py — Macon Geothermal Heat Pump Modbus reader + MariaDB pivot
# Logs all data to macon_pivot table, including Volumeflow from wagodb.mbus2
# Calls write_freq.py for write operations
# Version: 1.2.3

# Configuration flag to disable database operations
DISABLE_DB = False  # Set to True to disable all database operations and pymysql import

import os
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from datetime import datetime

# Conditionally import pymysql based on DISABLE_DB
if not DISABLE_DB:
    try:
        import pymysql
    except ImportError:
        print("⚠️ pymysql missing. DB disabled.")
        DISABLE_DB = True

# Call write_freq.py
os.system('python3 /home/pi/python/write_freq.py')
# ------------------------------------------------------
# Modbus RTU Configuration
# ------------------------------------------------------
client = ModbusSerialClient(
    port='/dev/ttyAMA0',
    baudrate=2400,
    parity='E',
    stopbits=1,
    bytesize=8,
    timeout=1
)
SLAVE_ADDRESS = 1

# ------------------------------------------------------
# MariaDB Configuration (only used if DB is enabled)
# ------------------------------------------------------
if not DISABLE_DB:
    DB_CONFIG = {
        "host": "192.168.178.23",
        "user": "gh",
        "password": "a12345",
        "database": "wagodb",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    PIVOT_TABLE = "macon_pivot"

# ------------------------------------------------------
# Register Map
# ------------------------------------------------------
REGISTER_MAP = {
    2004: ("Hot_water_temperature", "°C"),
    2007: ("Hot_water_tank_delta_T", "°C"),
    2047: ("Frequency_reduction", None),
    2052: ("Water_pump_mode", None),
    2056: ("Accept_host_frequency_control", None),
    2057: ("Host_unit_compressor_frequency", "Hz"),
    2100: ("Water_tank_temperature", "°C"),
    2102: ("Outlet_water_temperature", "°C"),
    2103: ("Inlet_water_temperature", "°C"),
    2104: ("Discharge_temperature", "°C"),
    2105: ("Suction_temperature", "°C"),
    2107: ("External_coil_temperature", "°C"),
    2108: ("Cooling_coil_temperature", "°C"),
    2110: ("Outdoor_ambient_temperature", "°C"),
    2114: ("IPM_temperature", "°C"),
    2115: ("Brine_inlet_temp", "°C"),
    2116: ("Brine_outlet_temp", "°C"),
    2118: ("Compressor_frequency", "Hz"),
    2120: ("AC_voltage", "V"),
    2121: ("AC_current", "A"),
    2122: ("DC_voltage", "V"),
    2124: ("Primary_EEV_opening", "%"),
    2125: ("Secondary_EEV_opening", "%"),
    2133: ("System_status_1", "bits"),
    2134: ("Error_code_1", "bits"),
    2135: ("System_status_2", "bits"),
    2136: ("System_status_3", "bits"),
    2137: ("Error_code_2", "bits"),
    2138: ("Error_code_3", "bits"),
}

# ------------------------------------------------------
# Bit Mappings
# ------------------------------------------------------
BIT_MAP = {
    2135: {
        1: "Compressor_status",
        5: "Water_pump",
        6: "4way_valve",
        7: "Electric_heater",
        8: "Water_flow_switch",
        9: "High_pressure_switch",
        10: "Low_pressure_switch",
        13: "3way_valve1",
        14: "3way_valve2",
    },
    2136: {
        5: "Defrost",
        8: "Wired_controller",
        9: "Energy_saving",
        10: "Primary_antifreeze",
        11: "Secondary_antifreeze",
        12: "Sterilizing",
        13: "Secondary_pump",
    },
    2137: {
        5: "External_coil_temp_error",
        6: "Discharge_temp_error",
        7: "Suction_temp_error",
        8: "Ambient_temp_error",
        9: "Comm_drive_error",
        10: "Comm_controller_error",
    },
    2138: {
        5: "High_discharge_protect",
        6: "High_pressure_protect",
        7: "Low_pressure_protect",
        8: "Water_flow_protect",
        10: "Low_ambient_protect",
        14: "Low_outlet_temp_protect",
    },
}

# ------------------------------------------------------
# Helpers
# ------------------------------------------------------
def decode_bits(value):
    """Decode 16-bit value into bit dictionary"""
    return {i: (value >> i) & 1 for i in range(16)}

def read_register(address):
    """Read single register"""
    try:
        res = client.read_holding_registers(address=address, count=1, slave=SLAVE_ADDRESS)
        if not res.isError():
            return res.registers[0]
    except ModbusException:
        return None

def read_block(start, count):
    """Read multiple registers"""
    try:
        res = client.read_holding_registers(address=start, count=count, slave=SLAVE_ADDRESS)
        if not res.isError():
            return res.registers
    except ModbusException:
        return None

if not DISABLE_DB:
    def fetch_volumeflow(cursor):
        """Fetch the Volumeflow with the highest id from wagodb.mbus2"""
        try:
            cursor.execute("""
                SELECT Volumeflow
                FROM wagodb.mbus2
                WHERE id = (SELECT MAX(id) FROM wagodb.mbus2)
            """)
            result = cursor.fetchone()
            return result['Volumeflow'] if result else None
        except Exception:
            return None  # Suppress error logging

    def ensure_pivot_table(cursor):
        """Ensure macon_pivot table exists"""
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {PIVOT_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

    def ensure_pivot_columns(cursor):
        """Ensure pivot columns exist for all registers, bit flags, and Volumeflow"""
        cursor.execute(f"SHOW COLUMNS FROM {PIVOT_TABLE}")
        existing = [row["Field"] for row in cursor.fetchall()]

        for _, (name, _) in REGISTER_MAP.items():
            if name not in existing:
                cursor.execute(f"ALTER TABLE {PIVOT_TABLE} ADD COLUMN `{name}` FLOAT NULL;")

        for reg, bits in BIT_MAP.items():
            for bit, label in bits.items():
                col = f"Bit{bit}_{label}"
                if col not in existing:
                    cursor.execute(f"ALTER TABLE {PIVOT_TABLE} ADD COLUMN `{col}` TINYINT(1) DEFAULT 0;")

        if "Volumeflow" not in existing:
            cursor.execute(f"ALTER TABLE {PIVOT_TABLE} ADD COLUMN `Volumeflow` FLOAT NULL;")

    def insert_pivot_row(cursor, timestamp, results, bit_expanded, volumeflow):
        """Insert one row into pivot table with expanded bits and Volumeflow"""
        cols = ["timestamp"]
        vals = [timestamp]

        for r, (n, v, u) in results.items():
            cols.append(n)
            vals.append(v)

        for col, bit_val in bit_expanded.items():
            cols.append(col)
            vals.append(bit_val)

        cols.append("Volumeflow")
        vals.append(volumeflow)

        placeholders = ", ".join(["%s"] * len(vals))
        columns = ", ".join([f"`{c}`" for c in cols])
        cursor.execute(f"INSERT INTO {PIVOT_TABLE} ({columns}) VALUES ({placeholders})", vals)

# ------------------------------------------------------
# Main
# ------------------------------------------------------
def main():
    if not client.connect():
        print("❌ Modbus failed")
        return

    timestamp = datetime.now()
    results = {}
    bit_expanded = {}

    try:
        # Read RW registers
        for reg in [2004, 2007, 2047, 2052, 2056, 2057]:
            val = read_register(reg)
            if val is not None and reg in REGISTER_MAP:
                desc, unit = REGISTER_MAP[reg]
                results[reg] = (desc, val, unit)

        # Read RO registers
        ro_data = read_block(2100, 39)
        if ro_data:
            for i, val in enumerate(ro_data):
                reg = 2100 + i
                if reg in REGISTER_MAP:
                    desc, unit = REGISTER_MAP[reg]
                    results[reg] = (desc, val, unit)
                    if unit == "bits" and reg in BIT_MAP:
                        bits = decode_bits(val)
                        for bit, label in BIT_MAP[reg].items():
                            bit_expanded[f"Bit{bit}_{label}"] = bits.get(bit, 0)

        if not DISABLE_DB:
            conn = pymysql.connect(**DB_CONFIG)
            with conn.cursor() as cursor:
                volumeflow = fetch_volumeflow(cursor)
                ensure_pivot_table(cursor)
                ensure_pivot_columns(cursor)
                insert_pivot_row(cursor, timestamp, results, bit_expanded, volumeflow)
            conn.commit()
            conn.close()

        print(f"✅ {timestamp:%H:%M:%S}")

    except Exception as e:
        print(f"⚠️ Error: {e}")
    finally:
        client.close()

# ------------------------------------------------------
if __name__ == "__main__":
    main()

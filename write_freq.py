#!/usr/bin/python3
# write_freq.py — Sets registers and optional soft reset for Macon heat pump (Modbus RTU)
# - Register 2000: Unit ON/OFF (0=OFF, 1=ON) for soft reset
# - Register 2007: Hot water tank ΔT (5°C)
# - Register 2004: DHW setpoint (45°C)
# - Checks Register 2136, Bit 3 (Brine pump status):
#   - If ON, enables host control (2056=1) and sets compressor frequency (2057=70 Hz)
#   - If OFF, disables host control (2056=0)
# - Checks Register 2137 for errors
# - Single attempt for all read/write operations (no retries)
# - Logs to /home/gh/macon/macon_control.log
# Version: 1.8.3

import logging
import time
import os
from logging.handlers import RotatingFileHandler
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

# Configuration
MODBUS_PORT = '/dev/ttyAMA0'
MODBUS_BAUDRATE = 2400
MODBUS_PARITY = 'E'
MODBUS_STOPBITS = 1
MODBUS_BYTESIZE = 8
MODBUS_TIMEOUT = 1
SLAVE_ID = 1
LOG_FILE = '/tmp/macon_control.log'
LOG_MAX_BYTES = 1024 * 1024  # 1 MB
LOG_BACKUP_COUNT = 3
WRITE_DELAY = 0.2  # Seconds between operations
RESET_DELAY = 2.0  # Seconds for soft reset

# Register Definitions (from Macon Protocol V1.3)
REGISTERS = {
    2000: {"name": "Unit ON/OFF setting", "unit": "", "desc": "0=OFF, 1=ON"},
    2004: {"name": "Hot water temperature setting", "unit": "°C", "desc": "Domestic hot water setpoint"},
    2007: {"name": "Hot water tank ΔT", "unit": "°C", "desc": "Temperature difference for hot water tank"},
    2056: {"name": "Host frequency control", "unit": "", "desc": "Enable/disable host unit frequency control (0=NO, 1=YES)"},
    2057: {"name": "Compressor frequency setting", "unit": "Hz", "desc": "Host unit compressor frequency (0-12)"},
    2136: {"name": "System working status", "unit": "", "desc": "Bit-mapped status (e.g., Bit 3=Brine side water pump)"},
    2137: {"name": "Error code", "unit": "", "desc": "Error codes (e.g., Bit 2=Inlet water temp sensor error)"}
}

BIT_FIELDS = {
    2136: {
        3: "Brine side water pump (0=OFF, 1=ON)",
        5: "Defrost (0=OFF, 1=ON)",
        8: "Wired controller connecting status (0=Disconnected, 1=Connected)"
    },
    2137: {
        2: "Inlet water temp sensor error (0=OK, 1=Error)",
        3: "Outlet water temp sensor error (0=OK, 1=Error)",
        8: "Ambient temperature sensor error (0=OK, 1=Error)"
    }
}

# Register Values
UNIT_ON_OFF_REG = 2000
HOT_WATER_TANK_DELTA_T_REG = 2007
HOT_WATER_TANK_DELTA_T_VALUE = 5
DHW_SETPOINT_REG = 2004
DHW_SETPOINT_VALUE = 45
HOST_CONTROL_REG = 2056
HOST_CONTROL_ON = 1
HOST_CONTROL_OFF = 0
COMPRESSOR_FREQ_REG = 2057
COMPRESSOR_FREQ_VALUE = 70
BRINE_PUMP_STATUS_REG = 2136
BRINE_PUMP_BIT = 3
ERROR_CODE_REG = 2137

# Logging Setup
def setup_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S.%f')
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    if not os.access(os.path.dirname(LOG_FILE), os.W_OK):
        logger.error(f"❌ Log directory {os.path.dirname(LOG_FILE)} is not writable")
        exit(1)
    return logger

logger = setup_logging()
logger.info("Starting write_freq.py v1.8.3")

# Helper Functions
def decode_bits(value, reg):
    return {i: {"value": (value >> i) & 1, "desc": BIT_FIELDS[reg].get(i, f"Bit{i} (Reserved)")} for i in BIT_FIELDS.get(reg, {})}

def read_register(address):
    try:
        res = client.read_holding_registers(address=address, count=1, slave=SLAVE_ID)
        if not res.isError():
            return res.registers[0]
        logger.error(f"⚠️ Error reading {REGISTERS[address]['name']} (Reg {address}): Modbus error")
    except ModbusException as e:
        logger.error(f"Modbus exception reading {REGISTERS[address]['name']} (Reg {address}): {e}")
    logger.error(f"Failed to read {REGISTERS[address]['name']} (Reg {address})")
    return None

def write_register(address, value):
    try:
        res = client.write_register(address=address, value=value, slave=SLAVE_ID)
        if not res.isError():
            logger.info(f"✅ Written: {REGISTERS[address]['name']} (Reg {address}) = {value} {REGISTERS[address]['unit']} - {REGISTERS[address]['desc']}")
            return True
        logger.error(f"⚠️ Error writing {REGISTERS[address]['name']} (Reg {address}) = {value}")
    except ModbusException as e:
        logger.error(f"Modbus exception writing {REGISTERS[address]['name']} (Reg {address}): {e}")
    logger.error(f"Failed to write {REGISTERS[address]['name']} (Reg {address}) = {value}")
    return False

def soft_reset():
    logger.info("Initiating soft reset via Register 2000 (Unit ON/OFF)")
    if not write_register(UNIT_ON_OFF_REG, 0):  # Turn OFF
        logger.error("❌ Failed to turn unit OFF for reset")
        return False
    time.sleep(RESET_DELAY)
    if not write_register(UNIT_ON_OFF_REG, 1):  # Turn ON
        logger.error("❌ Failed to turn unit ON after reset")
        return False
    logger.info("✅ Soft reset completed (Reg 2000 cycled OFF → ON)")
    return True

# Main Program
client = ModbusSerialClient(
    port=MODBUS_PORT,
    baudrate=MODBUS_BAUDRATE,
    parity=MODBUS_PARITY,
    stopbits=MODBUS_STOPBITS,
    bytesize=MODBUS_BYTESIZE,
    timeout=MODBUS_TIMEOUT
)

if not client.connect():
    logger.error("❌ Connection to /dev/ttyAMA0 failed")
    exit(1)

try:
    success = True
    # Optional: Perform soft reset (uncomment to enable)
    # if not soft_reset():
    #     success = False
    # time.sleep(WRITE_DELAY)

    # Write DHW settings
    if not write_register(HOT_WATER_TANK_DELTA_T_REG, HOT_WATER_TANK_DELTA_T_VALUE):
        success = False
    time.sleep(WRITE_DELAY)
    if not write_register(DHW_SETPOINT_REG, DHW_SETPOINT_VALUE):
        success = False
    time.sleep(WRITE_DELAY)

    # Check brine pump status
    status_val = read_register(BRINE_PUMP_STATUS_REG)
    if status_val is None:
        logger.warning(f"⚠️ No value read from {REGISTERS[BRINE_PUMP_STATUS_REG]['name']} (Reg {BRINE_PUMP_STATUS_REG})")
        success = False
    else:
        bits = decode_bits(status_val, BRINE_PUMP_STATUS_REG)
        pump_on = bits.get(BRINE_PUMP_BIT, {"value": 0})["value"]
        logger.info(f"{REGISTERS[BRINE_PUMP_STATUS_REG]['name']} (Reg {BRINE_PUMP_STATUS_REG}) = {status_val} - {REGISTERS[BRINE_PUMP_STATUS_REG]['desc']}")
        for bit, info in bits.items():
            logger.info(f"  Bit{bit}: {info['value']} - {info['desc']}")
        if pump_on:
            logger.info("💧 Brine pump is ON → Enable host control (2056=1) and set frequency to 70 Hz")
            if not write_register(HOST_CONTROL_REG, HOST_CONTROL_ON):
                success = False
            time.sleep(WRITE_DELAY)
            if not write_register(COMPRESSOR_FREQ_REG, COMPRESSOR_FREQ_VALUE):
                success = False
        else:
            logger.info("🚫 Brine pump is OFF → Disable host control (2056=0)")
            if not write_register(HOST_CONTROL_REG, HOST_CONTROL_OFF):
                success = False

    # Check error codes
    error_val = read_register(ERROR_CODE_REG)
    if error_val is None:
        logger.warning(f"⚠️ No value read from {REGISTERS[ERROR_CODE_REG]['name']} (Reg {ERROR_CODE_REG})")
        success = False
    else:
        logger.info(f"{REGISTERS[ERROR_CODE_REG]['name']} (Reg {ERROR_CODE_REG}) = {error_val} - {REGISTERS[ERROR_CODE_REG]['desc']}")
        if error_val != 0:
            bits = decode_bits(error_val, ERROR_CODE_REG)
            for bit, info in bits.items():
                if info['value']:
                    logger.warning(f"  Bit{bit}: {info['value']} - {info['desc']}")
        else:
            logger.info("  No errors detected")

    # Verify written values
    written_registers = [HOT_WATER_TANK_DELTA_T_REG, DHW_SETPOINT_REG, HOST_CONTROL_REG]
    if pump_on:
        written_registers.append(COMPRESSOR_FREQ_REG)
    for reg in written_registers:
        val = read_register(reg)
        if val is not None:
            logger.info(f"✅ Verified: {REGISTERS[reg]['name']} (Reg {reg}) = {val} {REGISTERS[reg]['unit']} - {REGISTERS[reg]['desc']}")
        else:
            logger.warning(f"⚠️ Could not read {REGISTERS[reg]['name']} (Reg {reg})")
            success = False

    logger.info(f"Summary: {'Success' if success else 'Failed'} - Wrote and verified {len(written_registers)} registers")

except Exception as e:
    logger.error(f"Unexpected error: {e}")
finally:
    client.close()
    logger.info("Modbus connection closed")

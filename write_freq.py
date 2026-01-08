#!/usr/bin/python3
# write_freq.py — Sets registers and optional soft reset for Macon heat pump (Modbus RTU)
# - Register 2000: Unit ON/OFF (0=OFF, 1=ON) for soft reset
# - Register 2007: Hot water tank ΔT (5°C)
# - Register 2004: DHW setpoint (45°C)
# - Checks Register 2136, Bit 3 (Brine pump status)
# - Checks Register 2137 for errors
# - Logs to /tmp/macon_control.log, capped at 100kB with round-robin
# Version: 1.8.9

import time
import os
import logging
from logging.handlers import RotatingFileHandler
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from datetime import datetime

# Configuration
MODBUS_PORT = '/dev/ttyAMA0'
MODBUS_BAUDRATE = 2400
MODBUS_PARITY = 'E'
MODBUS_STOPBITS = 1
MODBUS_BYTESIZE = 8
MODBUS_TIMEOUT = 1
SLAVE_ID = 1
WRITE_DELAY = 0.2  # Seconds between operations
RESET_DELAY = 2.0  # Seconds for soft reset
LOG_FILE = '/tmp/macon_control.log'
LOG_MAX_BYTES = 102400  # 100kB
LOG_BACKUP_COUNT = 1  # One backup file for rotation

# Register Definitions
REGISTERS = {
    2000: {"name": "Unit ON/OFF setting", "unit": "", "desc": "0=OFF, 1=ON"},
    2004: {"name": "Hot water temperature setting", "unit": "°C", "desc": "Domestic hot water setpoint"},
    2007: {"name": "Hot water tank ΔT", "unit": "°C", "desc": "Temperature difference for hot water tank"},
    2056: {"name": "Host frequency control", "unit": "", "desc": "0=NO, 1=YES"},
    2057: {"name": "Compressor frequency setting", "unit": "Hz", "desc": "Host unit compressor frequency (0-120)"},
    2136: {"name": "System working status", "unit": "", "desc": "Bit-mapped status (Bit 3=Brine pump)"},
    2137: {"name": "Error code", "unit": "", "desc": "Error codes (e.g., Bit 2=Inlet water temp error)"}
}

BIT_FIELDS = {
    2136: {3: "Brine side water pump (0=OFF, 1=ON)"},
    2137: {
        2: "Inlet water temp sensor error (0=OK, 1=Error)",
        3: "Outlet water temp sensor error (0=OK, 1=Error)",
        8: "Ambient temperature sensor error (0=OK, 1=Error)"
    }
}

# Register Values
UNIT_ON_OFF_REG = 2000
HOT_WATER_TANK_DELTA_T_REG = 2007
HOT_WATER_TANK_DELTA_T_VALUE = 4
DHW_SETPOINT_REG = 2004
DHW_SETPOINT_VALUE = 46
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
    formatter = logging.Formatter('%(asctime)s %(message)s', datefmt='%H:%M:%S')
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if not os.access(os.path.dirname(LOG_FILE), os.W_OK):
        logger.error("❌ Log directory not writable")
        exit(1)
    return logger

logger = setup_logging()
logger.info("Starting write_freq.py v1.8.9")

# Helper Functions
def decode_bits(value, reg):
    return {i: (value >> i) & 1 for i in BIT_FIELDS.get(reg, {})}

def read_register(address):
    try:
        res = client.read_holding_registers(address=address, count=1, slave=SLAVE_ID)
        if not res.isError():
            logger.info(f"📖 Read {REGISTERS[address]['name']} (Reg {address}): {res.registers[0]} {REGISTERS[address]['unit']}")
            return res.registers[0]
        logger.error(f"⚠️ Failed to read {REGISTERS[address]['name']} (Reg {address}): Modbus error")
    except ModbusException as e:
        logger.error(f"⚠️ Modbus exception reading {REGISTERS[address]['name']} (Reg {address}): {e}")
    return None

def write_register(address, value):
    try:
        res = client.write_register(address=address, value=value, slave=SLAVE_ID)
        if not res.isError():
            logger.info(f"✅ Wrote {REGISTERS[address]['name']} (Reg {address}) = {value} {REGISTERS[address]['unit']}")
            return True
        logger.error(f"⚠️ Failed to write {REGISTERS[address]['name']} (Reg {address}) = {value}: Modbus error")
    except ModbusException as e:
        logger.error(f"⚠️ Modbus exception writing {REGISTERS[address]['name']} (Reg {address}) = {value}: {e}")
    return False

def soft_reset():
    logger.info("🔄 Initiating soft reset (Reg 2000)")
    if not write_register(UNIT_ON_OFF_REG, 0):
        logger.error("❌ Failed to turn unit OFF")
        return False
    time.sleep(RESET_DELAY)
    if not write_register(UNIT_ON_OFF_REG, 1):
        logger.error("❌ Failed to turn unit ON")
        return False
    logger.info("✅ Soft reset completed")
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
    logger.error("❌ Modbus connection failed")
    exit(1)

try:
    logger.info("🚀 Starting operations")
    success = True

    # Optional: Perform soft reset (uncomment to enable)
    # if not soft_reset():
    #     success = False
    # time.sleep(WRITE_DELAY)

    # Write DHW settings
    logger.info(f"✏️ Writing DHW settings")
    if not write_register(HOT_WATER_TANK_DELTA_T_REG, HOT_WATER_TANK_DELTA_T_VALUE):
        success = False
    time.sleep(WRITE_DELAY)
    # if not write_register(DHW_SETPOINT_REG, DHW_SETPOINT_VALUE):
    #     success = False
    # time.sleep(WRITE_DELAY)

    # Check brine pump status
    logger.info(f"🔎 Checking {REGISTERS[BRINE_PUMP_STATUS_REG]['name']} (Reg {BRINE_PUMP_STATUS_REG})")
    status_val = read_register(BRINE_PUMP_STATUS_REG)
    if status_val is None:
        success = False
    else:
        bits = decode_bits(status_val, BRINE_PUMP_STATUS_REG)
        pump_on = bits.get(BRINE_PUMP_BIT, 0)
        logger.info(f"💧 Brine pump {'ON' if pump_on else 'OFF'} (Bit {BRINE_PUMP_BIT} = {pump_on})")
        if pump_on:
            logger.info("🔧 Enabling host control and setting frequency")
            if not write_register(HOST_CONTROL_REG, HOST_CONTROL_ON):
                success = False
            time.sleep(WRITE_DELAY)
            if not write_register(COMPRESSOR_FREQ_REG, COMPRESSOR_FREQ_VALUE):
                success = False
        else:
            logger.info("🔧 Disabling host control")
            if not write_register(HOST_CONTROL_REG, HOST_CONTROL_OFF):
                success = False

    # Check error codes
    logger.info(f"🔎 Checking {REGISTERS[ERROR_CODE_REG]['name']} (Reg {ERROR_CODE_REG})")
    error_val = read_register(ERROR_CODE_REG)
    if error_val is None:
        success = False
    elif error_val != 0:
        success = False
        bits = decode_bits(error_val, ERROR_CODE_REG)
        logger.warning(f"⚠️ Errors detected: {error_val}")
        for bit, val in bits.items():
            if val:
                logger.warning(f"  Bit {bit}: {BIT_FIELDS[ERROR_CODE_REG][bit]}")
    else:
        logger.info("✅ No errors detected")

    # Verify written values
    logger.info("🔍 Verifying written registers")
    written_registers = [HOT_WATER_TANK_DELTA_T_REG, DHW_SETPOINT_REG, HOST_CONTROL_REG]
    if pump_on:
        written_registers.append(COMPRESSOR_FREQ_REG)
    for reg in written_registers:
        val = read_register(reg)
        if val is None:
            success = False
        else:
            logger.info(f"✅ Verified {REGISTERS[reg]['name']} (Reg {reg}) = {val} {REGISTERS[reg]['unit']}")

    logger.info(f"🏁 Summary: {'Success' if success else 'Failed'}")

except Exception as e:
    logger.error(f"⚠️ Unexpected error: {e}")
    success = False
finally:
    client.close()
    close_time = datetime.now()
    logger.info(f"🔒 Connection closed at {close_time:%H:%M:%S}")

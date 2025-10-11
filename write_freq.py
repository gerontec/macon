#!/usr/bin/python3
# write_freq.py — Sets registers for geothermal heat pump control (Modbus RTU)
# - Register 2007: Hot water tank ΔT (5°C for longer cycles)
# - Register 2004: Domestic hot water (DHW) setpoint (45°C for better COP)
# - Checks Register 2136, Bit 3 (Brine pump status):
#   - If ON, enables host control (2056=1) and sets compressor frequency (2057=70 Hz)
#   - If OFF, disables host control (2056=0)
# - Logs all operations with register descriptions to file and console
# - No interaction with Register 2047 (frequency reduction function)
# Version: 1.8.0

import logging
import time
import os
from logging.handlers import RotatingFileHandler
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

# ------------------------------------------------------
# Configuration Section
# ------------------------------------------------------
MODBUS_PORT = '/dev/ttyAMA0'
MODBUS_BAUDRATE = 2400
MODBUS_PARITY = 'E'
MODBUS_STOPBITS = 1
MODBUS_BYTESIZE = 8
MODBUS_TIMEOUT = 1
SLAVE_ID = 1

# Log File Configuration
LOG_FILE = '/home/pi/python/macon_control.log'  # Absolute path for Raspberry Pi
LOG_MAX_BYTES = 1024 * 1024  # 1 MB
LOG_BACKUP_COUNT = 3  # Keep 3 backup logs

# Register Definitions (from Macon Protocol V1.3)
REGISTERS = {
    2004: {"name": "Hot water temperature setting", "unit": "°C", "desc": "Domestic hot water setpoint"},
    2007: {"name": "Hot water tank ΔT", "unit": "°C", "desc": "Temperature difference for hot water tank"},
    2056: {"name": "Host frequency control", "unit": "", "desc": "Enable/disable host unit frequency control (0=NO, 1=YES)"},
    2057: {"name": "Compressor frequency setting", "unit": "Hz", "desc": "Host unit compressor frequency (0-12)"},
    2136: {"name": "System working status", "unit": "", "desc": "Bit-mapped status, Bit 3 = Brine side water pump (0=OFF, 1=ON)"}
}

# Register Values
HOT_WATER_TANK_DELTA_T_REG = 2007
HOT_WATER_TANK_DELTA_T_VALUE = 5  # °C, for longer cycles
DHW_SETPOINT_REG = 2004
DHW_SETPOINT_VALUE = 45  # °C, for better COP
HOST_CONTROL_REG = 2056
HOST_CONTROL_ON = 1
HOST_CONTROL_OFF = 0
COMPRESSOR_FREQ_REG = 2057
COMPRESSOR_FREQ_VALUE = 70  # Hz
BRINE_PUMP_STATUS_REG = 2136
BRINE_PUMP_BIT = 3

# Timing
WRITE_DELAY = 0.2  # Seconds
MAX_RETRIES = 3  # Retry attempts for Modbus operations

# ------------------------------------------------------
# Logging Setup
# ------------------------------------------------------
def setup_logging():
    """Configure logging with file rotation and console output"""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    # Create format for logs
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # File handler with rotation
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except PermissionError:
        print(f"❌ Permission denied for log file {LOG_FILE}. Check file permissions.")
        exit(1)
    except Exception as e:
        print(f"❌ Failed to set up log file {LOG_FILE}: {e}")
        exit(1)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Verify log file is writable
    if not os.access(LOG_FILE, os.W_OK):
        logger.error(f"❌ Log file {LOG_FILE} is not writable. Check permissions.")
        exit(1)

    return logger

logger = setup_logging()
logger.info("Starting write_freq.py v1.8.0")

# ------------------------------------------------------
# Helper Functions
# ------------------------------------------------------
def decode_bits(value):
    """Converts 16-bit value to dict {bit_index: 0/1}"""
    return {i: (value >> i) & 1 for i in range(16)}

def read_register(address, retries=MAX_RETRIES):
    """Read single register with retry logic"""
    for attempt in range(retries):
        try:
            res = client.read_holding_registers(address=address, count=1, slave=SLAVE_ID)
            if not res.isError():
                return res.registers[0]
            logger.error(f"⚠️ Error reading {REGISTERS[address]['name']} (Reg {address}): Modbus error")
        except ModbusException as e:
            logger.error(f"Modbus exception reading {REGISTERS[address]['name']} (Reg {address}): {e}")
        time.sleep(WRITE_DELAY)
    logger.error(f"Failed to read {REGISTERS[address]['name']} (Reg {address}) after {retries} attempts")
    return None

def write_register(address, value, retries=MAX_RETRIES):
    """Write single register with retry logic"""
    for attempt in range(retries):
        try:
            res = client.write_register(address=address, value=value, slave=SLAVE_ID)
            if not res.isError():
                logger.info(f"✅ Written: {REGISTERS[address]['name']} (Reg {address}) = {value} {REGISTERS[address]['unit']} - {REGISTERS[address]['desc']}")
                return True
            logger.error(f"⚠️ Error writing {REGISTERS[address]['name']} (Reg {address}) = {value}")
        except ModbusException as e:
            logger.error(f"Modbus exception writing {REGISTERS[address]['name']} (Reg {address}): {e}")
        time.sleep(WRITE_DELAY)
    logger.error(f"Failed to write {REGISTERS[address]['name']} (Reg {address}) = {value} after {retries} attempts")
    return False

# ------------------------------------------------------
# Main Program
# ------------------------------------------------------
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
else:
    try:
        # --- Write optimized settings for DHW ---
        success = True
        # Register 2007: Hot water tank ΔT (5°C for longer cycles)
        if not write_register(HOT_WATER_TANK_DELTA_T_REG, HOT_WATER_TANK_DELTA_T_VALUE):
            success = False
        time.sleep(WRITE_DELAY)

        # Register 2004: DHW setpoint (45°C for better COP)
        if not write_register(DHW_SETPOINT_REG, DHW_SETPOINT_VALUE):
            success = False
        time.sleep(WRITE_DELAY)

        # --- Check brine pump (Register 2136, Bit 3) ---
        status_val = read_register(BRINE_PUMP_STATUS_REG)
        if status_val is None:
            logger.warning(f"⚠️ No value read from {REGISTERS[BRINE_PUMP_STATUS_REG]['name']} (Reg {BRINE_PUMP_STATUS_REG})")
            success = False
        else:
            bits = decode_bits(status_val)
            pump_on = bits.get(BRINE_PUMP_BIT, 0)
            logger.info(f"{REGISTERS[BRINE_PUMP_STATUS_REG]['name']} (Reg {BRINE_PUMP_STATUS_REG}) = {status_val} → Bit{BRINE_PUMP_BIT} (Brine pump): {pump_on} - {REGISTERS[BRINE_PUMP_STATUS_REG]['desc']}")

            if pump_on:
                logger.info("💧 Brine pump is ON → Enable host control (2056=1) and set frequency to 70 Hz")
                if not write_register(HOST_CONTROL_REG, HOST_CONTROL_ON):  # Enable host frequency control
                    success = False
                time.sleep(WRITE_DELAY)
                if not write_register(COMPRESSOR_FREQ_REG, COMPRESSOR_FREQ_VALUE):  # Set frequency = 70 Hz
                    success = False
            else:
                logger.info("🚫 Brine pump is OFF → Disable host control (2056=0)")
                if not write_register(HOST_CONTROL_REG, HOST_CONTROL_OFF):  # Disable host frequency control
                    success = False

        # --- Verify written values ---
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

        # --- Log summary ---
        logger.info(f"Summary: {'Success' if success else 'Failed'} - Wrote and verified {len(written_registers)} registers")

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        client.close()
        logger.info("Modbus connection closed")

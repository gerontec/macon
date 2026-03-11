#!/usr/bin/python3
import csv
import re
import time
import argparse
import logging
from pymodbus.client import ModbusSerialClient as ModbusClient
from pymodbus.exceptions import ModbusException

# Configuration
SLAVE_ADDRESS = 1
PORT = '/dev/ttyUSB3'
BAUDRATE = 9600
TIMEOUT = 3  # Timeout for reliability
REGISTER_START = 0x0000
REGISTER_END = 0x016E
MAX_REGISTERS_PER_READ = 120
CSV_FILE = 'parameters.csv'
ON_OFF_REGISTER = 0x003F  # Register for on/off control
MODE_REGISTER = 0x0043  # Mode register
RETRY_COUNT = 3  # Number of retries for failed reads/writes

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load register mappings from parameters.csv
def load_mapping():
    mapping = {}
    try:
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    address = int(row['Address'], 16)
                    description = row['Description'].strip()
                    setting_range = row['Setting Range'].strip()
                    note = row['Note'].strip()

                    # Infer Type
                    if 'reserved' in description.lower():
                        reg_type = 'reserved'
                    elif note and 'Bit' in note:
                        reg_type = 'bits'
                    elif setting_range and '-' in setting_range and not any(unit in setting_range.lower() for unit in ['℃', 'hz', 'bar', 'min', 'sec', 's']):
                        reg_type = 'enum'
                    elif note and 'n*' in note or any(unit in (setting_range + note).lower() for unit in ['℃', 'hz', 'bar', 'min', 'sec', 's']):
                        reg_type = 'scaled'
                    else:
                        reg_type = 'raw'

                    # Infer Scale
                    scale = None
                    if reg_type == 'scaled':
                        scale_match = re.search(r'n\*([0-9.]+)', note + ' ' + setting_range)
                        if scale_match:
                            scale = float(scale_match.group(1))
                        else:
                            scale = 1.0

                    # Infer Unit
                    unit = ''
                    unit_match = re.search(r'(℃|Hz|bar|min|s|P)', setting_range + ' ' + note)
                    if unit_match:
                        unit = unit_match.group(1)

                    # Parse Bits
                    bits = []
                    if reg_type == 'bits' and note:
                        bit_lines = [line.strip() for line in note.split('\n') if line.strip().startswith('Bit')]
                        bits = [b if ': ' in b and b.split(': ')[1].strip() else f"{b.split(':')[0]}: Reserved" for b in bit_lines]

                    # Combine Note and Setting Range
                    combined_note = setting_range + ('; ' + note if note else '') if setting_range else note

                    mapping[address] = {
                        'name': description,
                        'type': reg_type,
                        'scale': scale,
                        'unit': unit,
                        'note': combined_note,
                        'bits': bits
                    }
                except (KeyError, ValueError) as e:
                    logger.error(f"Error parsing row {row}: {e}")
                    continue
    except FileNotFoundError:
        logger.error(f"{CSV_FILE} not found. Please ensure the file exists.")
        return {}
    except Exception as e:
        logger.error(f"Error reading {CSV_FILE}: {e}")
        return {}
    return mapping

# Decode register value
def decode_register(address, value, mapping):
    if address not in mapping:
        return f"Register 0x{address:04X}: {value} [Unknown register]"
    info = mapping[address]
    explanation = info['name']
    try:
        # Treat value as signed 16-bit integer if out of expected range
        if value > 32767 and info['type'] in ('scaled', 'raw'):
            value_signed = value - 65536
        else:
            value_signed = value

        if info['type'] == 'bits' and info['bits']:
            binary = format(value, '08b')[::-1]
            bits_desc = []
            for bit, b in zip(info['bits'], binary):
                try:
                    bit_desc = bit.split(': ')[1].strip()
                    bits_desc.append(f"{bit_desc}: {b}")
                except IndexError:
                    bits_desc.append(f"Reserved: {b}")
            bits_desc_str = ', '.join(bits_desc)
            explanation += f": {value} (Bits: {bits_desc_str})"
        elif info['type'] == 'scaled' and info['scale'] is not None:
            if address in [0x001F, 0x0020]:
                explanation += f": {value} [Fault flags]"
            elif address == 0x0144:
                explanation += f": {value} [Day bitmask]"
            elif address == 0x0148:
                scaled_value = value_signed * 0.1
                explanation += f": {scaled_value} ℃ (Raw: {value})"
            else:
                scaled_value = value_signed * info['scale']
                explanation += f": {scaled_value} {info['unit']} (Raw: {value})"
        elif info['type'] == 'enum':
            enums = {}
            if info['note']:
                for part in info['note'].split(','):
                    if '-' in part and '/' in part:
                        try:
                            k, v = part.split('-', 1)
                            enums[int(k.strip())] = v.strip().split('/')[0].strip()
                        except ValueError:
                            continue
            explanation += f": {enums.get(value, str(value))} (Raw: {value})"
        else:
            explanation += f": {value_signed}"
        if info['note']:
            explanation += f" [{info['note']}]"
    except Exception as e:
        logger.error(f"Error decoding register 0x{address:04X}: {e}")
        explanation += f": {value} [Decoding error]"
    return explanation

# Check system state (minimal, skips fault registers)
def check_system_state(client, mapping):
    try:
        # Only check mode register
        for attempt in range(RETRY_COUNT):
            try:
                result = client.read_holding_registers(address=MODE_REGISTER, count=1, slave=SLAVE_ADDRESS)
                if result.isError():
                    logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} failed reading mode register 0x{MODE_REGISTER:04X}: {result}")
                    time.sleep(0.6)  # Increased delay
                    continue
                mode = result.registers[0]
                logger.info(f"Current mode: {decode_register(MODE_REGISTER, mode, mapping)}")
                return True, "System state OK"
            except ModbusException as e:
                logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error reading 0x{MODE_REGISTER:04X}: {e}")
                time.sleep(0.6)
                if attempt == RETRY_COUNT - 1:
                    logger.error(f"Error reading mode register 0x{MODE_REGISTER:04X} after {RETRY_COUNT} attempts: {e}")
                    return False, "Mode check failed"
    except Exception as e:
        logger.error(f"General error during system state check: {e}")
        return False, f"Error: {e}"

# Clear serial buffer (workaround for newer pymodbus versions)
def clear_serial_buffer(client):
    try:
        # Read any residual data to clear the buffer
        client.read_holding_registers(address=0xFFFF, count=1, slave=SLAVE_ADDRESS)
    except ModbusException:
        # Ignore errors as this is just to clear the buffer
        pass
    time.sleep(0.6)  # Increased delay for stability

# Switch on the device by setting Bit0 of register 0x003F to 1
def switch_on_device(client, mapping):
    try:
        # Clear serial buffer
        clear_serial_buffer(client)

        # Check system state (minimal)
        state_ok, state_message = check_system_state(client, mapping)
        if not state_ok:
            logger.error(f"Cannot switch ON: {state_message}")
            return False

        # Read current value of 0x003F with retries
        current_value = None
        for attempt in range(RETRY_COUNT):
            try:
                result = client.read_holding_registers(address=ON_OFF_REGISTER, count=1, slave=SLAVE_ADDRESS)
                if result.isError():
                    logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} failed reading 0x{ON_OFF_REGISTER:04X}: {result}")
                    time.sleep(0.6)
                    continue
                current_value = result.registers[0]
                break
            except ModbusException as e:
                logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error reading 0x{ON_OFF_REGISTER:04X}: {e}")
                time.sleep(0.6)
                if attempt == RETRY_COUNT - 1:
                    logger.error(f"Error reading 0x{ON_OFF_REGISTER:04X} after {RETRY_COUNT} attempts: {e}")
                    return False

        if current_value is None:
            logger.error(f"Failed to read current value of 0x{ON_OFF_REGISTER:04X}")
            return False

        logger.info(f"Current value of 0x{ON_OFF_REGISTER:04X}: {current_value} ({decode_register(ON_OFF_REGISTER, current_value, mapping)})")

        # Set Bit0 to 1 while preserving other bits
        new_value = current_value | 0x0001  # Bitwise OR to set Bit0
        logger.debug(f"Writing 0x{new_value:04X} to 0x{ON_OFF_REGISTER:04X}")

        # Write new value to 0x003F with retries
        for attempt in range(RETRY_COUNT):
            try:
                result = client.write_register(address=ON_OFF_REGISTER, value=new_value, slave=SLAVE_ADDRESS)
                time.sleep(0.6)  # Delay after write
                logger.debug(f"Write response for 0x{ON_OFF_REGISTER:04X}: {result}")
                break
            except ModbusException as e:
                logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error writing to 0x{ON_OFF_REGISTER:04X}: {e}")
                time.sleep(0.6)
                if attempt == RETRY_COUNT - 1:
                    logger.error(f"Error writing to 0x{ON_OFF_REGISTER:04X} after {RETRY_COUNT} attempts: {e}")
                    return False

        # Verify the write
        clear_serial_buffer(client)
        for attempt in range(RETRY_COUNT):
            try:
                result = client.read_holding_registers(address=ON_OFF_REGISTER, count=1, slave=SLAVE_ADDRESS)
                if result.isError():
                    logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} failed verifying 0x{ON_OFF_REGISTER:04X}: {result}")
                    time.sleep(0.6)
                    continue
                updated_value = result.registers[0]
                logger.info(f"Updated value of 0x{ON_OFF_REGISTER:04X}: {updated_value} ({decode_register(ON_OFF_REGISTER, updated_value, mapping)})")
                if updated_value & 0x0001:  # Check if Bit0 is 1
                    logger.info("Device switched ON successfully.")
                    return True
                else:
                    logger.error("Failed to switch ON device: Bit0 not set.")
                    return False
            except ModbusException as e:
                logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error verifying 0x{ON_OFF_REGISTER:04X}: {e}")
                time.sleep(0.6)
                if attempt == RETRY_COUNT - 1:
                    logger.error(f"Error verifying 0x{ON_OFF_REGISTER:04X} after {RETRY_COUNT} attempts: {e}")
                    return False

    except Exception as e:
        logger.error(f"General error during switch ON: {e}")
        return False

# Switch off the device by setting Bit0 of register 0x003F to 0
def switch_off_device(client, mapping):
    try:
        # Clear serial buffer
        clear_serial_buffer(client)

        # Read current value of 0x003F with retries
        current_value = None
        for attempt in range(RETRY_COUNT):
            try:
                result = client.read_holding_registers(address=ON_OFF_REGISTER, count=1, slave=SLAVE_ADDRESS)
                if result.isError():
                    logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} failed reading 0x{ON_OFF_REGISTER:04X}: {result}")
                    time.sleep(0.6)
                    continue
                current_value = result.registers[0]
                break
            except ModbusException as e:
                logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error reading 0x{ON_OFF_REGISTER:04X}: {e}")
                time.sleep(0.6)
                if attempt == RETRY_COUNT - 1:
                    logger.error(f"Error reading 0x{ON_OFF_REGISTER:04X} after {RETRY_COUNT} attempts: {e}")
                    return False

        if current_value is None:
            logger.error(f"Failed to read current value of 0x{ON_OFF_REGISTER:04X}")
            return False

        logger.info(f"Current value of 0x{ON_OFF_REGISTER:04X}: {current_value} ({decode_register(ON_OFF_REGISTER, current_value, mapping)})")

        # Clear Bit0 while preserving other bits
        new_value = current_value & ~0x0001  # Bitwise AND with inverse to clear Bit0
        logger.debug(f"Writing 0x{new_value:04X} to 0x{ON_OFF_REGISTER:04X}")

        # Write new value to 0x003F with retries
        for attempt in range(RETRY_COUNT):
            try:
                result = client.write_register(address=ON_OFF_REGISTER, value=new_value, slave=SLAVE_ADDRESS)
                time.sleep(0.6)
                logger.debug(f"Write response for 0x{ON_OFF_REGISTER:04X}: {result}")
                break
            except ModbusException as e:
                logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error writing to 0x{ON_OFF_REGISTER:04X}: {e}")
                time.sleep(0.6)
                if attempt == RETRY_COUNT - 1:
                    logger.error(f"Error writing to 0x{ON_OFF_REGISTER:04X} after {RETRY_COUNT} attempts: {e}")
                    return False

        # Verify the write
        clear_serial_buffer(client)
        for attempt in range(RETRY_COUNT):
            try:
                result = client.read_holding_registers(address=ON_OFF_REGISTER, count=1, slave=SLAVE_ADDRESS)
                if result.isError():
                    logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} failed verifying 0x{ON_OFF_REGISTER:04X}: {result}")
                    time.sleep(0.6)
                    continue
                updated_value = result.registers[0]
                logger.info(f"Updated value of 0x{ON_OFF_REGISTER:04X}: {updated_value} ({decode_register(ON_OFF_REGISTER, updated_value, mapping)})")
                if not (updated_value & 0x0001):  # Check if Bit0 is 0
                    logger.info("Device switched OFF successfully.")
                    return True
                else:
                    logger.error("Failed to switch OFF device: Bit0 still set.")
                    return False
            except ModbusException as e:
                logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error verifying 0x{ON_OFF_REGISTER:04X}: {e}")
                time.sleep(0.6)
                if attempt == RETRY_COUNT - 1:
                    logger.error(f"Error verifying 0x{ON_OFF_REGISTER:04X} after {RETRY_COUNT} attempts: {e}")
                    return False

    except Exception as e:
        logger.error(f"General error during switch OFF: {e}")
        return False

# Read all registers
def read_all_modbus_registers():
    client = ModbusClient(
        method='rtu', port=PORT, baudrate=BAUDRATE, bytesize=8, parity='N', stopbits=1, timeout=TIMEOUT
    )
    mapping = load_mapping()

    if not mapping:
        logger.error("No mapping loaded. Exiting.")
        return

    unknown_registers = []
    try:
        if not client.connect():
            raise Exception("Failed to connect to serial port")
        
        # Print header
        print(f"{'Address':<8} | {'Value':>5} | Description")
        print("-" * 80)
        
        current_address = REGISTER_START
        while current_address <= REGISTER_END:
            num_registers = min(MAX_REGISTERS_PER_READ, REGISTER_END - current_address + 1)
            logger.info(f"Reading {num_registers} registers starting at 0x{current_address:04X}...")
            clear_serial_buffer(client)
            
            for attempt in range(RETRY_COUNT):
                try:
                    result = client.read_holding_registers(
                        address=current_address, count=num_registers, slave=SLAVE_ADDRESS
                    )
                    if result.isError():
                        logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} failed reading registers at 0x{current_address:04X}: {result}")
                        time.sleep(0.6)
                        continue
                    for i, value in enumerate(result.registers):
                        address = current_address + i
                        if address not in mapping:
                            unknown_registers.append(address)
                        print(f"0x{address:04X} | {value:>5} | {decode_register(address, value, mapping)}")
                    break
                except ModbusException as e:
                    logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error reading registers at 0x{current_address:04X}: {e}")
                    time.sleep(0.6)
                    if attempt == RETRY_COUNT - 1:
                        logger.error(f"Error reading registers at 0x{current_address:04X} after {RETRY_COUNT} attempts: {e}")
                        break
            
            current_address += num_registers
            time.sleep(0.6)  # Increased delay between reads

        if unknown_registers:
            print(f"\nUnknown registers ({len(unknown_registers)}): {', '.join(f'0x{addr:04X}' for addr in unknown_registers)}")

    except ModbusException as e:
        logger.error(f"Modbus error: {e}")
    except Exception as e:
        logger.error(f"General error: {e}")
    finally:
        client.close()

# Main function with enhanced argument parsing
def main():
    parser = argparse.ArgumentParser(description="Modbus interface for R290 device")
    parser.add_argument('--switch-on', action='store_true', help='Turn the device ON (sets Bit0 of 0x003F to 1)')
    parser.add_argument('--switch-off', action='store_true', help='Turn the device OFF (sets Bit0 of 0x003F to 0)')
    args = parser.parse_args()

    client = ModbusClient(
        method='rtu', port=PORT, baudrate=BAUDRATE, bytesize=8, parity='N', stopbits=1, timeout=TIMEOUT
    )
    mapping = load_mapping()
    if not mapping:
        logger.error("No mapping loaded. Exiting.")
        return

    try:
        if not client.connect():
            raise Exception("Failed to connect to serial port")

        if args.switch_on:
            logger.info("Attempting to switch ON the device...")
            success = switch_on_device(client, mapping)
            if not success:
                logger.error("Switch ON command failed.")
                return
        elif args.switch_off:
            logger.info("Attempting to switch OFF the device...")
            success = switch_off_device(client, mapping)
            if not success:
                logger.error("Switch OFF command failed.")
                return
        else:
            read_all_modbus_registers()

    except Exception as e:
        logger.error(f"Main error: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    main()

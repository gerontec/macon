#!/usr/bin/python3
import csv
import re
import time
import argparse
import logging
from pymodbus.client import ModbusSerialClient as ModbusClient
from pymodbus.exceptions import ModbusException

# ==============================================================================
# 1. Konfiguration
# ==============================================================================
SLAVE_ADDRESS = 1
PORT = '/dev/ttyUSB3'
BAUDRATE = 9600
TIMEOUT = 3 # Timeout for reliability
REGISTER_START = 0x0000
REGISTER_END = 0x016E # End of Holding Registers (Konfiguration)
MAX_REGISTERS_PER_READ = 32
CSV_FILE = 'parameters.csv'
ON_OFF_REGISTER = 0x003F # Register for on/off control
MODE_REGISTER = 0x0043 # Mode register
RETRY_COUNT = 1 # !!! GEÄNDERT: NUR EIN VERSUCH PRO ABFRAGE !!!

# --- Konfiguration für Live-Daten (Input Registers) ---
LIVE_REGISTER_START = 0x2000 # Typische Startadresse für Live-Status/Sensoren
LIVE_REGISTER_COUNT = 50     # Lese einen Block von 50 Registern, um Live-Daten zu finden

# --- Monitoring Register (Messwerte und Status) ---
MONITORING_REGISTERS = [
    0x0007, # Aktuelle Fehler (Error 1-8)
    0x000B, # Aktuelle Fehler (Error 9-16)
    0x0027, # Heißgastemperatur
    0x0028, # Niederdruck-Saugtemperatur (Fehler-Flag für Niederdrucksensor)
    0x002A, # Hochdruck
    0x002B, # Niederdruck
    0x002C, # Außentemperatur
    0x002D, # Wassertemperatur (Ausgang)
    0x002E, # Wassertemperatur (Eingang)
    0x003D, # Kompressor-Frequenz
    0x0041, # System-Status/Betriebsart
]
# ---

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. Hilfsfunktionen (Mapping, Decoding, State Check)
# ==============================================================================

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
                    elif setting_range and '-' in setting_range and not any(unit in setting_range.lower() for unit in ['℃', 'hz', 'bar', 'min', 'sec', 's', 'p']):
                        reg_type = 'enum'
                    elif note and 'n*' in note or any(unit in (setting_range + note).lower() for unit in ['℃', 'hz', 'bar', 'min', 'sec', 's', 'p']):
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
                            # Standard scale for most temperature/pressure values if not explicitly noted
                            scale = 1.0

                    # Infer Unit
                    unit = ''
                    unit_match = re.search(r'(℃|Hz|bar|min|s|P)', setting_range + ' ' + note, re.IGNORECASE)
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
    # Wenn die Adresse nicht in unserer Mapping-Datei ist
    if address not in mapping:
        return f"Unknown Register 0x{address:04X}"
        
    info = mapping[address]
    explanation = info['name']
    try:
        # Treat value as signed 16-bit integer if out of expected range
        if value > 32767 and info['type'] in ('scaled', 'raw'):
            value_signed = value - 65536
        else:
            value_signed = value

        if info['type'] == 'bits' and info['bits']:
            # Decode bits: Bit 0 is the least significant bit (rightmost)
            binary = format(value, '016b')[::-1] # 16 bits, reversed
            bits_desc = []
            
            for i, bit_line in enumerate(info['bits']):
                if i < len(binary):
                    bit_value = binary[i]
                    try:
                        # Extract description from "Bit X: Description"
                        bit_desc = bit_line.split(': ')[1].strip()
                    except IndexError:
                        bit_desc = "Reserved"
                    bits_desc.append(f"Bit {i} ({bit_desc}): {bit_value}")
                
            bits_desc_str = ', '.join(bits_desc)
            explanation += f" (Raw: {value} / Bits: {bits_desc_str})"
            
        elif info['type'] == 'scaled' and info['scale'] is not None:
            # Spezielle Handhabung für 0.1 Skalierungen (z.B. Temperatur/Druck)
            scaled_value = value_signed * info['scale']
            explanation += f": {scaled_value} {info['unit']} (Raw: {value})"
            
        elif info['type'] == 'enum':
            enums = {}
            if info['note']:
                # Example: "0 - OFF / 1 - ON"
                for part in info['note'].split(';'):
                    if '-' in part:
                        try:
                            k, v = part.split('-', 1)
                            # Remove unit/secondary info like "/Htg"
                            enum_desc = v.strip().split('/')[0].strip()
                            enums[int(k.strip())] = enum_desc
                        except ValueError:
                            continue
            explanation += f": {enums.get(value, str(value))} (Raw: {value})"
            
        else: # Raw or Reserved
            explanation += f": {value_signed}"
            
        if info['note'] and info['type'] not in ('bits', 'enum'):
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
                    time.sleep(0.6)
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
        # Attempt to read non-existent register to clear buffer
        client.read_holding_registers(address=0xFFFF, count=1, slave=SLAVE_ADDRESS)
    except ModbusException:
        # Ignore errors as this is just to clear the buffer
        pass
    time.sleep(0.1) # Short delay for stability

# ==============================================================================
# 3. Modbus Lese-/Schreibfunktionen
# ==============================================================================

# Switch on the device by setting Bit0 of register 0x003F to 1
def switch_on_device(client, mapping):
    try:
        clear_serial_buffer(client)
        state_ok, state_message = check_system_state(client, mapping)
        if not state_ok:
            logger.error(f"Cannot switch ON: {state_message}")
            return False

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

        logger.info(f"Current value of 0x{ON_OFF_REGISTER:04X}: {decode_register(ON_OFF_REGISTER, current_value, mapping)}")

        new_value = current_value | 0x0001 # Bitwise OR to set Bit0
        logger.info(f"Writing 0x{new_value:04X} to 0x{ON_OFF_REGISTER:04X}")

        for attempt in range(RETRY_COUNT):
            try:
                client.write_register(address=ON_OFF_REGISTER, value=new_value, slave=SLAVE_ADDRESS)
                time.sleep(0.6) # Delay after write
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
                logger.info(f"Updated value of 0x{ON_OFF_REGISTER:04X}: {decode_register(ON_OFF_REGISTER, updated_value, mapping)}")
                if updated_value & 0x0001: # Check if Bit0 is 1
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
        clear_serial_buffer(client)

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

        logger.info(f"Current value of 0x{ON_OFF_REGISTER:04X}: {decode_register(ON_OFF_REGISTER, current_value, mapping)}")

        new_value = current_value & ~0x0001 # Bitwise AND with inverse to clear Bit0
        logger.info(f"Writing 0x{new_value:04X} to 0x{ON_OFF_REGISTER:04X}")

        for attempt in range(RETRY_COUNT):
            try:
                client.write_register(address=ON_OFF_REGISTER, value=new_value, slave=SLAVE_ADDRESS)
                time.sleep(0.6)
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
                logger.info(f"Updated value of 0x{ON_OFF_REGISTER:04X}: {decode_register(ON_OFF_REGISTER, updated_value, mapping)}")
                if not (updated_value & 0x0001): # Check if Bit0 is 0
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

# Read Input Registers (Live Status Data)
def read_input_registers(client, mapping, start_address, count):
    logger.info(f"Reading {count} Input Registers (Live Data) starting at 0x{start_address:04X}...")
    
    # Print header for live data
    print(f"\n{'Address':<8} | {'Value':>5} | Description (Input Registers - Live Data)")
    print("-" * 80)
    
    current_address = start_address
    
    while current_address < start_address + count:
        num_registers = min(MAX_REGISTERS_PER_READ, start_address + count - current_address)
        
        for attempt in range(RETRY_COUNT):
            try:
                # Use read_input_registers (Function Code 04)
                result = client.read_input_registers(
                    address=current_address, count=num_registers, slave=SLAVE_ADDRESS
                )
                if result.isError():
                    logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} failed reading Input Registers at 0x{current_address:04X}: {result}")
                    time.sleep(0.6)
                    continue
                
                for i, value in enumerate(result.registers):
                    address = current_address + i
                    # Input Registers sind oft nicht in parameters.csv. Wir decodieren trotzdem, falls sie dort sind.
                    print(f"0x{address:04X} | {value:>5} | {decode_register(address, value, mapping)}")
                break
            except ModbusException as e:
                logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error reading Input Registers at 0x{current_address:04X}: {e}")
                time.sleep(0.6)
                if attempt == RETRY_COUNT - 1:
                    logger.error(f"Error reading Input Registers at 0x{current_address:04X} after {RETRY_COUNT} attempts: {e}")
                    break
        
        current_address += num_registers
        time.sleep(0.6)

# NEUE FUNKTION: Read only Monitoring Registers (Holding and Input)
def read_monitoring_modbus_registers():
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
            
        # --- HOLDING REGISTERS (Messwerte & Status) ---
        print(f"\n{'Address':<8} | {'Value':>5} | Description (Holding Registers - Monitoring)")
        print("-" * 80)
        
        # Gehe die vordefinierte Liste der Register durch
        for address in MONITORING_REGISTERS:
            # Versuche, nur 1 Register auf einmal zu lesen
            num_registers = 1
            clear_serial_buffer(client)
            
            for attempt in range(RETRY_COUNT):
                try:
                    result = client.read_holding_registers(
                        address=address, count=num_registers, slave=SLAVE_ADDRESS
                    )
                    if result.isError():
                        logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} failed reading register 0x{address:04X}: {result}")
                        time.sleep(0.6)
                        continue
                        
                    value = result.registers[0]
                    # Sicherstellen, dass das Register in der Mapping-Datei ist
                    print(f"0x{address:04X} | {value:>5} | {decode_register(address, value, mapping)}")
                    break
                except ModbusException as e:
                    logger.warning(f"Attempt {attempt + 1}/{RETRY_COUNT} Modbus error reading register 0x{address:04X}: {e}")
                    time.sleep(0.6)
                    if attempt == RETRY_COUNT - 1:
                        logger.error(f"Error reading register 0x{address:04X} after {RETRY_COUNT} attempts: {e}")
                        break
            
            time.sleep(0.3) # Kleine Pause zwischen den einzelnen Adressen

        # --- INPUT REGISTERS (Live Data) ---
        read_input_registers(client, mapping, LIVE_REGISTER_START, LIVE_REGISTER_COUNT)

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

    # Client-Initialisierung (notwendig für Mapping-Load)
    client = ModbusClient(
        method='rtu', port=PORT, baudrate=BAUDRATE, bytesize=8, parity='N', stopbits=1, timeout=TIMEOUT
    )
    mapping = load_mapping()
    if not mapping:
        logger.error("No mapping loaded. Exiting.")
        return

    try:
        # Client-Verbindung wird in den Unterfunktionen bei Bedarf neu aufgebaut
        if args.switch_on:
            logger.info("Attempting to switch ON the device...")
            # Hier muss die Verbindung aufgebaut werden, da switch_on_device() dies nicht tut
            if not client.connect():
                raise Exception("Failed to connect to serial port for switch-on command")
            success = switch_on_device(client, mapping)
            if not success:
                logger.error("Switch ON command failed.")
        elif args.switch_off:
            logger.info("Attempting to switch OFF the device...")
            # Hier muss die Verbindung aufgebaut werden, da switch_off_device() dies nicht tut
            if not client.connect():
                raise Exception("Failed to connect to serial port for switch-off command")
            success = switch_off_device(client, mapping)
            if not success:
                logger.error("Switch OFF command failed.")
        else:
            # Standardaktion: Nur Monitoring-Register lesen
            # read_monitoring_modbus_registers stellt die Verbindung selbst her
            read_monitoring_modbus_registers()

    except Exception as e:
        logger.error(f"Main error: {e}")
    finally:
        # Schließe den Client nur, wenn er in main() verbunden wurde
        # Da switch_on/off nun die Verbindung in main() nutzt, ist das nötig.
        # read_monitoring_modbus_registers schließt selbst.
        if (args.switch_on or args.switch_off) and client.is_socket_open():
             client.close()

if __name__ == "__main__":
    main()

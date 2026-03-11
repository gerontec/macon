#!/usr/bin/env python3
# r290_debugger.py (Hauptskript)

import argparse
import sys
from time import sleep

# Import der PyModbus-Klassen
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusIOException, ModbusException

# Import der lokalen, dynamisch generierten Daten
try:
    from csv_parser import REGISTER_DEFINITIONS, FAULT_BIT_MAP
except ImportError:
    print("FATAL: Das Modul csv_parser.py oder die Datei parameters.csv fehlt.")
    sys.exit(1)

# --- Modbus Client Klasse ---
class ModbusClient:
    """PyModbus RTU Client, konfiguriert mit den erfolgreichen 8E1-Parametern."""
    def __init__(self, port, baudrate, slave_id):
        self.slave_id = slave_id
        # PyModbus Initialisierung mit 8E1 (Even Parity, 1 Stop Bit)
        self.client = ModbusSerialClient(
            port=port,
            baudrate=baudrate,
            parity='E',  # E für Even Parity
            stopbits=1,
            bytesize=8,
            timeout=2.0  # Robuster Timeout
        )

    def connect(self):
        """Verbindet den PyModbus Client und prüft die Verbindung."""
        if self.client.connect():
            return True
        else:
            raise ConnectionError(f"Verbindung zum seriellen Port {self.client.port} konnte nicht hergestellt werden.")
        
    def close(self):
        """Schließt die serielle Verbindung."""
        self.client.close()

    def read_registers(self, address, count, function_code):
        """Liest Register mit dem korrekten Funktionstyp (FC3 oder FC4)."""
        
        # FC4: Read Input Registers (R-Access, meist Live-Werte)
        if function_code == 4:
            response = self.client.read_input_registers(address=address, count=count, slave=self.slave_id)
        # FC3: Read Holding Registers (RW-Access, meist Konfiguration)
        elif function_code == 3:
            response = self.client.read_holding_registers(address=address, count=count, slave=self.slave_id)
        else:
            raise ValueError(f"Nicht unterstützter Modbus Function Code: {function_code}")
            
        if response.is_exception():
            raise ModbusIOException(f"Modbus-Exception empfangen (Code: {response.exception_code}). Registeradresse {hex(address)} existiert nicht oder ist gesperrt.")
        
        if not response.registers:
            raise ModbusIOException("Leere oder ungültige Modbus-Antwort.")
            
        return response.registers

# --- Datenverarbeitung ---
def decode_fault_flag(address, raw_value):
    """Analysiert bitweise die dynamisch geladenen Fehlerregister."""
    bit_map = FAULT_BIT_MAP.get(address, {})
    active_faults = []
    
    for bit_index, description in bit_map.items():
        if (raw_value >> bit_index) & 1:
            active_faults.append(description)
    return active_faults

def process_and_display(address, raw_value):
    """Verarbeitet den Rohwert basierend auf der dynamischen Register-Map und gibt ihn aus."""
    if address not in REGISTER_DEFINITIONS:
        print(f"WARN: Adresse 0x{address:04X} in Register-Map nicht gefunden.")
        return

    description, scale, _, _ = REGISTER_DEFINITIONS[address]
    
    if 'Flag' in description or 'Bit' in description:
        active_flags = decode_fault_flag(address, raw_value)
        # Bessere Formatierung für den Scan-Modus
        print(f"0x{address:04X} | {description:<35}: {raw_value} (Dez)")
        if active_flags:
            print(f"       -> AKTIV: {', '.join(active_flags)}")
        return

    # Skalierung für analoge Werte
    physical_value = raw_value * scale
    unit = ""
    if "temperature" in description.lower():
        unit = "°C"
    elif "pressure" in description.lower():
        unit = "bar"
    
    print(f"0x{address:04X} | {description:<35}: {physical_value:.1f} {unit} ({raw_value} Roh)")

# --- NEUE SCAN-FUNKTION ---
def run_scan_all(client):
    """Scannt alle Register in der REGISTER_DEFINITIONS Map."""
    
    print("\n--- STARTE VOLLSTÄNDIGER MODBUS SCAN ---")
    
    sorted_addresses = sorted(REGISTER_DEFINITIONS.keys())

    try:
        client.connect()
    except ConnectionError as e:
        print(f"\nVERBINDUNGSFEHLER: {e}")
        return

    for address in sorted_addresses:
        description, scale, _, reg_type = REGISTER_DEFINITIONS[address]
        function_code = 4 if reg_type == 'Input' else 3
        
        try:
            # Lese raw_values über die Funktion
            raw_values = client.read_registers(address, 1, function_code)
            
            if raw_values:
                process_and_display(address, raw_values[0])
            
        # 🚨 KORRIGIERTE FEHLERBEHANDLUNG: Fängt alle PyModbus-Exceptions ab
        except (ModbusException, ModbusIOException) as e:
            # Bei Fehlern (Timeout, illegaler Adresse) einfach Punkt anzeigen und weitermachen
            sys.stdout.write(f".") 
            sys.stdout.flush() 
        except Exception as e:
            # Fängt alle anderen unerwarteten Fehler ab
            print(f"\nUNERWARTETER FEHLER bei 0x{address:04X}: {e}")
        
        sleep(0.05) # Kurze Pause, um den Bus nicht zu überlasten

    print("\n--- SCAN BEENDET ---")


# --- Hauptlogik (Kombinierter Modus) ---
def run_debugger(port, baudrate, slave_id, target_register):
    """Führt entweder Einzelabfrage oder den Scan-Modus aus."""
    client = ModbusClient(port, baudrate, slave_id)
    
    try:
        if target_register is None:
            # SCAN-MODUS: Kein Register übergeben
            run_scan_all(client)
        else:
            # EINZELABFRAGE: Ein Register übergeben
            if target_register not in REGISTER_DEFINITIONS:
                 print(f"FEHLER: Register-Adresse 0x{target_register:04X} ist unbekannt. Prüfen Sie die CSV.")
                 return
            
            # Führe die Einzelabfrage aus
            _, _, _, reg_type = REGISTER_DEFINITIONS[target_register]
            function_code = 4 if reg_type == 'Input' else 3

            print(f"Versuche, {reg_type}-Register 0x{target_register:04X} zu lesen (FC{function_code})...")
            
            client.connect()
            raw_values = client.read_registers(target_register, 1, function_code)
            
            if raw_values:
                process_and_display(target_register, raw_values[0])

    except Exception as e:
        print(f"HAUPTFEHLER BEI AUSFÜHRUNG: {e}")
    finally:
        # Sorgt dafür, dass die Verbindung immer geschlossen wird.
        client.close() 


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="R290 Modbus Debugger CLI. Starte Vollscan, wenn kein Register angegeben ist.")
    
    # Register ist nun OPTIONAL (nargs='?')
    parser.add_argument("register", nargs='?', type=lambda x: int(x, 0), default=None,
                        help="Zielregister (dezimal oder hex: 43 oder 0x2B). Wenn weggelassen: Starte Vollscan.")
    
    # Optionale Parameter (Defaults sind die erfolgreichen Werte)
    parser.add_argument("-p", "--port", default="/dev/ttyUSB3", help="Serieller Port (Default: /dev/ttyUSB3)")
    parser.add_argument("-b", "--baudrate", type=int, default=9600, help="Baudrate (Default: 9600)")
    parser.add_argument("-s", "--slave", type=int, default=1, help="Modbus Slave ID (Default: 1)")

    args = parser.parse_args()
    
    run_debugger(args.port, args.baudrate, args.slave, args.register)

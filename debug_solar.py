#!/usr/bin/python3
"""Debug Script für Solar-Sensor"""
from pymodbus.client import ModbusTcpClient

SPS_IP = '192.168.178.2'

def to_u(v): return v if v>=0 else v+65536
def calc_so(r): return round((r-26402)/60, 2) if 4000<r<40000 else 0.0

client = ModbusTcpClient(SPS_IP, 502, timeout=5)
if not client.connect():
    print("✗ Keine Verbindung zur SPS")
    exit(1)

try:
    # Lese Messwerte
    result = client.read_holding_registers(12320, 64, slave=0)
    if result.isError():
        print("✗ Modbus Fehler")
        exit(1)
    
    reg = result.registers
    
    # Solar-Werte analysieren
    raw_so = to_u(reg[7])  # xMeasure[8] = MW39
    temp_so_python = calc_so(raw_so)
    
    # PLC-berechneter Wert (falls vorhanden)
    temp_so_plc = to_u(reg[30]) / 100.0  # xMeasure[31] = MW62
    
    print("=" * 60)
    print("SOLAR-SENSOR DEBUG")
    print("=" * 60)
    print(f"Raw-Wert (MW39):        {raw_so}")
    print(f"  → Hex:                0x{raw_so:04X}")
    print(f"  → Gültiger Bereich:   4000 - 40000")
    print(f"  → Im Bereich?         {'✓ JA' if 4000 <= raw_so <= 40000 else '✗ NEIN'}")
    print()
    print(f"Python-Berechnung:      {temp_so_python:.2f}°C")
    print(f"  → Formel: (raw - 26402) / 60")
    print(f"  → Rechnung: ({raw_so} - 26402) / 60 = {temp_so_python:.2f}")
    print()
    print(f"PLC-Berechnung (MW62):  {temp_so_plc:.2f}°C")
    print("=" * 60)
    
    # Diagnose
    print("\nDIAGNOSE:")
    if raw_so < 4000:
        print("✗ Raw-Wert zu niedrig (< 4000)")
        print("  → Sensor eventuell nicht angeschlossen?")
        print("  → Kurzschluss?")
    elif raw_so > 40000:
        print("✗ Raw-Wert zu hoch (> 40000)")
        print("  → Sensor defekt?")
        print("  → Kabelbruch?")
    elif raw_so == 0 or raw_so == 65535:
        print("✗ Raw-Wert ungültig (0 oder 65535)")
        print("  → Sensor nicht verbunden")
        print("  → Multiplexer-Problem?")
    else:
        print("✓ Raw-Wert im gültigen Bereich")
        if abs(temp_so_python - temp_so_plc) > 1.0:
            print("⚠ Python und PLC Berechnung unterschiedlich!")
            
finally:
    client.close()

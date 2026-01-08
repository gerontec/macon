#!/usr/bin/python3
"""Liest DIREKT die physischen Analog-Inputs der WAGO-Karte"""
from pymodbus.client import ModbusTcpClient
import time

SPS_IP = '192.168.178.2'

def to_u(v): return v if v>=0 else v+65536

client = ModbusTcpClient(SPS_IP, 502, timeout=5)
if not client.connect():
    print("✗ Keine Verbindung zur SPS")
    exit(1)

print("=" * 80)
print("PHYSISCHE ANALOG-INPUTS AUSLESEN")
print("=" * 80)
print("\nAnalog-Eingänge (750-461 Karte):")
print("  %IW0 = AI0 = Kanal 0")
print("  %IW1 = AI1 = Kanal 1")
print("  %IW2 = AI2 = Kanal 2")
print("  %IW3 = AI3 = Kanal 3")
print("\nLaut Multiplexer-Logik:")
print("  Phase A: AI3 sollte Kessel sein")
print("  Phase B: AI3 sollte Solar sein")
print("-" * 80)

try:
    # %IW0-%IW3 = Input Words 0-3
    # Diese sind direkt als Modbus Input Registers verfügbar
    for i in range(5):
        result = client.read_input_registers(0, 4, slave=0)
        if result.isError():
            print(f"✗ Fehler beim Lesen der Input-Register")
            continue
        
        iw0 = to_u(result.registers[0])
        iw1 = to_u(result.registers[1])
        iw2 = to_u(result.registers[2])
        iw3 = to_u(result.registers[3])
        
        timestamp = time.strftime("%H:%M:%S")
        print(f"{timestamp} | IW0={iw0:5d} IW1={iw1:5d} IW2={iw2:5d} IW3={iw3:5d}")
        
        # Wenn IW3 im Solar-Bereich ist
        if 25000 <= iw3 <= 27000:
            temp = (iw3 - 26402) / 60.0
            print(f"         | ^^^ IW3 könnte SOLAR sein: {temp:.2f}°C")
        
        time.sleep(0.5)
    
    print("-" * 80)
    print("\nAnalyse:")
    print("  - Wenn IW3 ~8000-9000 zeigt → Kessel (Phase A)")
    print("  - Wenn IW3 ~26000 zeigt → Solar (Phase B)")
    print("  - Wenn IW3 konstant bleibt → Multiplexer schaltet NICHT")
    print()
    print("Falls Multiplexer nicht schaltet, prüfen Sie:")
    print("  - Digital Outputs DO.3 und DO.4 (Multiplexer-Steuerung)")
    print("  - PLC-Code: mux_state Variable")
    print("=" * 80)
    
finally:
    client.close()

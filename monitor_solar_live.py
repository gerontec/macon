#!/usr/bin/python3
"""Kontinuierliches Monitoring - fängt Solar-Werte während Multiplexer-Wechsel"""
from pymodbus.client import ModbusTcpClient
import time
import sys

SPS_IP = '192.168.178.2'

def to_u(v): return v if v>=0 else v+65536
def to_s(v): return v if v<32768 else v-65536

client = ModbusTcpClient(SPS_IP, 502, timeout=5)
if not client.connect():
    print("✗ Keine Verbindung zur SPS")
    exit(1)

print("=" * 80)
print("KONTINUIERLICHES SOLAR-MONITORING (Drücken Sie Ctrl+C zum Beenden)")
print("=" * 80)
print("\nÜberwache alle potenziellen Solar-Register...")
print("Zeit     | Phase | reg[7]  reg[28] reg[29]  reg[30] reg[31] | Status")
print("-" * 80)

try:
    last_values = [0, 0, 0, 0, 0]
    count = 0
    
    while True:
        result = client.read_holding_registers(12320, 32, slave=0)
        if result.isError():
            print("✗ Modbus Fehler")
            time.sleep(0.5)
            continue
        
        reg = result.registers
        
        # Status und Phase
        status = to_s(reg[10])
        phase = 'A' if (status & 0x10) else 'B'
        
        # Alle Solar-relevanten Register
        values = [
            to_u(reg[7]),   # MW39
            to_u(reg[28]),  # MW60
            to_s(reg[29]),  # MW61 (signed wegen negativer Werte)
            to_s(reg[30]),  # MW62 (signed) ← Solar temp × 100
            to_u(reg[31])   # MW63
        ]
        
        # Zeige alle Werte, markiere Änderungen
        changes = []
        for i, (old, new) in enumerate(zip(last_values, values)):
            if old != new:
                changes.append(f"reg[{[7,28,29,30,31][i]}]:{old}→{new}")
        
        timestamp = time.strftime("%H:%M:%S")
        status_text = " ✓ ÄNDERUNG: " + ", ".join(changes) if changes else ""
        
        # Zeige Zeile
        print(f"{timestamp} |   {phase}   | {values[0]:6d} {values[1]:7d} {values[2]:7d} {values[3]:7d} {values[4]:7d} |{status_text}")
        
        # Wenn reg[30] != 0, zeige Temperatur
        if values[3] != 0:
            temp = values[3] / 100.0
            print(f"        | >>> SOLAR-TEMP GEFUNDEN: {temp:.2f}°C <<<")
        
        last_values = values
        count += 1
        
        # Alle 20 Zeilen Header wiederholen
        if count % 20 == 0:
            print("-" * 80)
            print("Zeit     | Phase | reg[7]  reg[28] reg[29]  reg[30] reg[31] | Status")
            print("-" * 80)
        
        time.sleep(0.2)  # 5x pro Sekunde prüfen

except KeyboardInterrupt:
    print("\n" + "=" * 80)
    print("Monitoring beendet")
    print("=" * 80)
finally:
    client.close()

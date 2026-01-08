#!/usr/bin/python3
"""Debug Script für Multiplexer und alle Sensoren"""
from pymodbus.client import ModbusTcpClient
import time

SPS_IP = '192.168.178.2'

def to_u(v): return v if v>=0 else v+65536
def to_s(v): return v if v<32768 else v-65536

client = ModbusTcpClient(SPS_IP, 502, timeout=5)
if not client.connect():
    print("✗ Keine Verbindung zur SPS")
    exit(1)

try:
    print("=" * 80)
    print("MULTIPLEXER & SENSOR DEBUG")
    print("=" * 80)
    
    # Mehrere Messungen über Zeit
    for i in range(3):
        result = client.read_holding_registers(12320, 32, slave=0)
        if result.isError():
            print(f"✗ Modbus Fehler bei Messung {i+1}")
            continue
        
        reg = result.registers
        
        # Alle Raw-Werte (xMeasure[1..8])
        raw_vl = to_u(reg[0])      # MW32 - Phase A
        raw_at = to_u(reg[1])      # MW33 - Phase A
        raw_it = to_u(reg[2])      # MW34 - Phase A
        raw_ke = to_u(reg[3])      # MW35 - Phase A
        raw_ww = to_u(reg[4])      # MW36 - Phase B
        raw_ot = to_u(reg[5])      # MW37 - Phase B
        raw_ru = to_u(reg[6])      # MW38 - Phase B
        raw_so = to_u(reg[7])      # MW39 - Phase B ← SOLAR!
        
        # Status-Word mit Phase-Info
        status = to_s(reg[10])     # MW42
        phase_a = bool(status & 0x10)  # Bit 4
        
        print(f"\n--- Messung {i+1}/3 ---")
        print(f"Phase: {'A' if phase_a else 'B'} (Bit 4 = {status & 0x10})")
        print()
        print("PHASE A Sensoren:")
        print(f"  VL (Vorlauf):      {raw_vl:5d}  {'✓' if 4000 <= raw_vl <= 25000 else '✗'}")
        print(f"  AT (Außen):        {raw_at:5d}  {'✓' if 4000 <= raw_at <= 25000 else '✗'}")
        print(f"  IT (Innen):        {raw_it:5d}  {'✓' if 4000 <= raw_it <= 25000 else '✗'}")
        print(f"  KE (Kessel):       {raw_ke:5d}  {'✓' if 4000 <= raw_ke <= 25000 else '✗'}")
        print()
        print("PHASE B Sensoren:")
        print(f"  WW (Warmwasser):   {raw_ww:5d}  {'✓' if 4000 <= raw_ww <= 45000 else '✗'}")
        print(f"  OT (Öltank):       {raw_ot:5d}  {'✓' if raw_ot > 0 else '✗'}")
        print(f"  RU (Rücklauf):     {raw_ru:5d}  {'✓' if 4000 <= raw_ru <= 25000 else '✗'}")
        print(f"  SO (Solar):        {raw_so:5d}  {'✓' if 4000 <= raw_so <= 40000 else '✗ PROBLEM!'}")
        
        if i < 2:
            time.sleep(0.6)  # Warte auf nächsten Zyklus
    
    print()
    print("=" * 80)
    print("DIAGNOSE:")
    print()
    
    # Finale Messung für Diagnose
    result = client.read_holding_registers(12320, 32, slave=0)
    reg = result.registers
    raw_so = to_u(reg[7])
    raw_vl = to_u(reg[0])
    raw_ww = to_u(reg[4])
    
    if raw_so == 0:
        print("✗ Solar-Sensor Raw-Wert = 0")
        print()
        if raw_vl > 0 and raw_ww > 0:
            print("  → Phase A Sensoren funktionieren")
            print("  → Phase B Sensoren teilweise funktionierend")
            print("  → PROBLEM: Solar-Sensor (analog3 in Phase B)")
            print()
            print("Mögliche Ursachen:")
            print("  1. Solar-Sensor physisch nicht angeschlossen")
            print("  2. Solar-Sensor ist defekt")
            print("  3. Verkabelung zu analog3 in Phase B unterbrochen")
            print("  4. Multiplexer schaltet analog3 nicht korrekt")
        else:
            print("  → Phase B Multiplexer funktioniert möglicherweise nicht")
            print("  → Alle Phase B Sensoren prüfen!")
    elif raw_so < 4000:
        print(f"✗ Solar-Sensor Raw-Wert zu niedrig: {raw_so}")
        print("  → Kurzschluss oder falscher Sensor-Typ?")
    elif raw_so > 40000:
        print(f"✗ Solar-Sensor Raw-Wert zu hoch: {raw_so}")
        print("  → Kabelbruch oder Sensor defekt?")
    else:
        print(f"✓ Solar-Sensor Raw-Wert OK: {raw_so}")
        temp = (raw_so - 26402) / 60.0
        print(f"  → Temperatur: {temp:.2f}°C")
        if temp < -10 or temp > 100:
            print("  ⚠ Temperatur außerhalb plausiblem Bereich")
            print("  → Kalibrierung prüfen!")
    
    print("=" * 80)
    
finally:
    client.close()

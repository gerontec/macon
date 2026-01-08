#!/usr/bin/python3
"""Scannt alle Modbus-Register um Solar-Wert (26000) zu finden"""
from pymodbus.client import ModbusTcpClient

SPS_IP = '192.168.178.2'

def to_u(v): return v if v>=0 else v+65536
def to_s(v): return v if v<32768 else v-65536

client = ModbusTcpClient(SPS_IP, 502, timeout=5)
if not client.connect():
    print("✗ Keine Verbindung zur SPS")
    exit(1)

try:
    print("=" * 80)
    print("MODBUS REGISTER SCAN - Suche nach Solar-Wert (Raw ~26000)")
    print("=" * 80)
    
    # Lese xMeasure Array (MW32-MW95 = 12320-12383)
    result = client.read_holding_registers(12320, 64, slave=0)
    if result.isError():
        print("✗ Fehler beim Lesen")
        exit(1)
    
    reg = result.registers
    
    print("\nxMeasure Array (MW32-MW95 / Modbus 12320-12383):")
    print("-" * 80)
    print("Idx | MW   | Modbus | Raw (uns) | Raw (sig) | Beschreibung")
    print("-" * 80)
    
    for i in range(64):
        raw_u = to_u(reg[i])
        raw_s = to_s(reg[i])
        mw = 32 + i
        modbus = 12320 + i
        
        # Markiere Werte im Solar-Bereich (25000-27000)
        marker = " ← SOLAR?" if 25000 <= raw_u <= 27000 else ""
        
        # Zeige nur interessante Werte
        if raw_u > 0 and (raw_u < 100 or raw_u > 1000):
            desc = ""
            if i == 0: desc = "VL (Vorlauf)"
            elif i == 1: desc = "AT (Außen)"
            elif i == 2: desc = "IT (Innen)"
            elif i == 3: desc = "KE (Kessel)"
            elif i == 4: desc = "WW (Warmwasser)"
            elif i == 5: desc = "OT (Öltank)"
            elif i == 6: desc = "RU (Rücklauf)"
            elif i == 7: desc = "SO (Solar) - PYTHON erwartet hier!"
            elif i == 10: desc = "Status-Word"
            elif i == 11: desc = "temp_diff_ww * 100"
            elif i == 27: desc = "temp_at_sps * 100"
            elif i == 28: desc = "temp_it_sps * 100"
            elif i == 29: desc = "temp_ru_sps * 100"
            elif i == 30: desc = "temp_so_sps * 100"
            
            print(f"[{i:2d}] | MW{mw:2d} | {modbus:5d} | {raw_u:9d} | {raw_s:9d} | {desc}{marker}")
    
    print("-" * 80)
    
    # Suche gezielt nach Solar-Wert
    print("\n🔍 SUCHE nach Raw-Wert ~26000:")
    found = False
    for i in range(64):
        raw_u = to_u(reg[i])
        if 25000 <= raw_u <= 27000:
            mw = 32 + i
            modbus = 12320 + i
            temp = (raw_u - 26402) / 60.0
            print(f"  ✓ Gefunden: Index [{i}] = MW{mw} (Modbus {modbus})")
            print(f"    Raw: {raw_u}")
            print(f"    Temp: {temp:.2f}°C")
            found = True
    
    if not found:
        print("  ✗ Kein Wert im Bereich 25000-27000 gefunden!")
        print("  → PLC schreibt Solar-Wert möglicherweise nicht ins xMeasure Array")
    
    # Prüfe auch berechneten Temp-Wert (-1.3°C = -130 als INT)
    print("\n🔍 SUCHE nach berechneter Temp -1.3°C (als INT*100 = -130):")
    for i in range(64):
        raw_s = to_s(reg[i])
        if -150 <= raw_s <= -100:  # Bereich um -130
            mw = 32 + i
            modbus = 12320 + i
            temp = raw_s / 100.0
            print(f"  ✓ Gefunden: Index [{i}] = MW{mw} (Modbus {modbus})")
            print(f"    Raw (signed): {raw_s}")
            print(f"    Temp: {temp:.2f}°C")
    
    print("=" * 80)
    
    # Zeige was Python aktuell liest
    print("\nWas PYTHON aktuell liest:")
    print(f"  reg[7] (MW39, Modbus 12327) = {to_u(reg[7])} (unsigned) / {to_s(reg[7])} (signed)")
    print(f"  → Sollte Solar Raw sein, ist aber: {to_u(reg[7])}")
    
finally:
    client.close()

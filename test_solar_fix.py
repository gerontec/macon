#!/usr/bin/python3
"""Test: Lese Solar von korrektem Register xMeasure[31]"""
from pymodbus.client import ModbusTcpClient

SPS_IP = '192.168.178.2'

def to_u(v): return v if v>=0 else v+65536
def to_s(v): return v if v<32768 else v-65536
def calc_so(r): return round((r-26402)/60, 2) if 4000<r<40000 else 0.0

client = ModbusTcpClient(SPS_IP, 502, timeout=5)
if not client.connect():
    print("✗ Keine Verbindung zur SPS")
    exit(1)

try:
    result = client.read_holding_registers(12320, 64, slave=0)
    if result.isError():
        print("✗ Modbus Fehler")
        exit(1)
    
    reg = result.registers
    
    print("=" * 80)
    print("SOLAR-SENSOR FIX TEST")
    print("=" * 80)
    
    # ALT: Python liest von reg[7] = xMeasure[8]
    raw_so_alt = to_u(reg[7])
    temp_so_alt = calc_so(raw_so_alt)
    
    print("\n❌ ALTE Methode (FALSCH):")
    print(f"   Register: reg[7] = xMeasure[8] = MW39 (Modbus 12327)")
    print(f"   Raw-Wert: {raw_so_alt}")
    print(f"   Temperatur: {temp_so_alt:.2f}°C")
    
    # NEU: Python soll von reg[30] = xMeasure[31] lesen (berechneter Wert × 100)
    temp_so_plc = to_s(reg[30]) / 100.0
    
    print("\n✅ NEUE Methode (KORREKT):")
    print(f"   Register: reg[30] = xMeasure[31] = MW62 (Modbus 12350)")
    print(f"   PLC-Wert (INT×100): {to_s(reg[30])}")
    print(f"   Temperatur: {temp_so_plc:.2f}°C")
    
    # Zeige auch den Raw-Wert, falls PLC ihn doch irgendwo hinschreibt
    print("\n📋 Alle Solar-relevanten Register:")
    for i in [7, 28, 29, 30, 31]:
        mw = 32 + i
        modbus = 12320 + i
        val_u = to_u(reg[i])
        val_s = to_s(reg[i])
        print(f"   reg[{i:2d}] = MW{mw} ({modbus}) = {val_u:5d} (u) / {val_s:6d} (s)")
    
    print("=" * 80)
    
    if abs(temp_so_plc) > 0.1:
        print(f"\n🎉 ERFOLG! Solar-Temperatur gefunden: {temp_so_plc:.2f}°C")
        print(f"   → Python muss von reg[30] statt reg[7] lesen!")
    else:
        print(f"\n⚠ Warnung: Wert ist {temp_so_plc:.2f}°C")
        print(f"   → Prüfen Sie ob PLC Solar-Wert berechnet")
    
finally:
    client.close()

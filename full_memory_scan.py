#!/usr/bin/python3
"""Vollständiger Speicher-Scan: Findet wo 26000 und -130 tatsächlich sind"""
from pymodbus.client import ModbusTcpClient

SPS_IP = '192.168.178.2'

def to_u(v): return v if v>=0 else v+65536
def to_s(v): return v if v<32768 else v-65536

client = ModbusTcpClient(SPS_IP, 502, timeout=5)
if not client.connect():
    print("✗ Keine Verbindung zur SPS")
    exit(1)

print("=" * 80)
print("VOLLSTÄNDIGER SPEICHER-SCAN")
print("Suche nach Solar-Werten: Raw ~26000 und Temp -130 (= -1.3°C × 100)")
print("=" * 80)

try:
    # Scanne in Blöcken von 64 Registern (Modbus-Limit)
    results = []
    
    for block_start in range(0, 256, 64):
        try:
            result = client.read_holding_registers(block_start, 64, slave=0)
            if not result.isError():
                for i, val in enumerate(result.registers):
                    addr = block_start + i
                    mw = addr - 12288 if addr >= 12288 else addr
                    val_u = to_u(val)
                    val_s = to_s(val)
                    
                    # Suche Solar Raw (25000-27000)
                    if 25000 <= val_u <= 27000:
                        temp = (val_u - 26402) / 60.0
                        results.append({
                            'addr': addr,
                            'mw': mw,
                            'val': val_u,
                            'type': 'SOLAR RAW',
                            'calc': f'{temp:.2f}°C'
                        })
                    
                    # Suche Temp -130 (±20)
                    if -150 <= val_s <= -110:
                        temp = val_s / 100.0
                        results.append({
                            'addr': addr,
                            'mw': mw,
                            'val': val_s,
                            'type': 'SOLAR TEMP',
                            'calc': f'{temp:.2f}°C'
                        })
        except:
            pass  # Skip unreadable blocks
    
    print("\n🔍 SUCHERGEBNISSE:")
    print("-" * 80)
    
    if results:
        print(f"Gefunden: {len(results)} Register mit Solar-Werten\n")
        for r in results:
            print(f"✓ {r['type']}:")
            print(f"  Modbus-Adresse: {r['addr']}")
            print(f"  MW-Register: MW{r['mw']}" if r['addr'] >= 12288 else f"  Direktadresse: {r['addr']}")
            print(f"  Wert: {r['val']}")
            print(f"  Temperatur: {r['calc']}")
            print()
    else:
        print("✗ KEINE Solar-Werte gefunden!")
        print("\nMögliche Ursachen:")
        print("  1. Solar-Sensor ist wirklich nicht angeschlossen")
        print("  2. PLC schreibt Werte in nicht-standard Register")
        print("  3. Werte werden nur temporär während Multiplexer-Phase geschrieben")
        print("\nEmpfehlung: Führen Sie ./monitor_solar_live.py aus")
    
    print("=" * 80)
    
    # Zeige auch die Register, die laut Doku Solar enthalten sollten
    print("\nERWARTETE Register (laut Dokumentation):")
    print("-" * 80)
    
    # Lese nochmal gezielt die erwarteten Register
    result = client.read_holding_registers(12320, 64, slave=0)
    if not result.isError():
        reg = result.registers
        print(f"MW39 (12327) xMeasure[8]  S_Solar Raw:      {to_u(reg[7]):5d}")
        print(f"MW62 (12350) xMeasure[31] temp_solar×100:   {to_s(reg[30]):5d}")
        print()
        if to_u(reg[7]) == 0 and to_s(reg[30]) == 0:
            print("⚠ BEIDE Register sind 0 - Code auf WAGO ≠ GitHub Code!")

finally:
    client.close()

#!/usr/bin/env python3
"""
pump_control.py - Pumpensteuerung via Modbus TCP
Steuert WW-Pumpe (O1) und HK-Pumpe (O2) über xSetpoints[17/18]

xSetpoints[17] = WW-Pumpe:  1=EIN, -1=AUS, 0=Automatik
xSetpoints[18] = HK-Pumpe:  1=EIN, -1=AUS, 0=Automatik

WAGO Modbus-Adresse = MW-Nummer + 12288
  xMeasure  AT %MW32  → Modbus 12320
  xSetpoints AT %MW96  → Modbus 12384
  xSetpoints[17] = MW112 → Modbus 12400
  xSetpoints[18] = MW113 → Modbus 12401
"""

import argparse
import sys
from pymodbus.client import ModbusTcpClient

# ─── Konfiguration ────────────────────────────────────────────────────────────
DEFAULT_HOST = "192.168.1.100"
DEFAULT_PORT = 502
DEFAULT_UNIT = 1

# WAGO Modbus-Adresse = MW-Nummer + 12288
# xMeasure  AT %MW32  → Modbus 12320  (= 12288 + 32)
# xSetpoints AT %MW96  → Modbus 12384  (= 12288 + 96)
MW_OFFSET        = 12288

XSETPOINTS_BASE  = MW_OFFSET + 96   # xSetpoints[1] = MW96 → 12384
REG_WW_OVERRIDE  = MW_OFFSET + 112  # xSetpoints[17] = MW112 → 12400
REG_HK_OVERRIDE  = MW_OFFSET + 113  # xSetpoints[18] = MW113 → 12401
REG_MEASURE_BASE = MW_OFFSET + 32   # xMeasure[1]    = MW32  → 12320
REG_DO_OVERRIDE_STATUS = MW_OFFSET + 64   # xMeasure[33]   = MW64  → 12352
REG_STATUS_WORD        = MW_OFFSET + 42   # xMeasure[11]   = MW42  → 12330

# ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

def int_to_modbus(value: int) -> int:
    """Konvertiert signed INT (-1, 0, 1) in unsigned 16-bit Modbus-Wert."""
    if value < 0:
        return value + 65536
    return value

def modbus_to_int(value: int) -> int:
    """Konvertiert unsigned 16-bit Modbus-Wert zurück in signed INT."""
    if value > 32767:
        return value - 65536
    return value

def pump_state_str(override_val: int) -> str:
    if override_val == 1:
        return "ZWANGS-EIN  (Override)"
    elif override_val == -1:
        return "ZWANGS-AUS  (Override)"
    else:
        return "AUTOMATIK"

def status_word_str(sw: int) -> str:
    parts = []
    if sw & 1:   parts.append("O1:EIN")
    else:        parts.append("O1:AUS")
    if sw & 2:   parts.append("O2:EIN")
    else:        parts.append("O2:AUS")
    if sw & 4:   parts.append("O3:EIN")
    else:        parts.append("O3:AUS")
    if sw & 8:   parts.append("Nacht")
    if sw & 64:  parts.append("WW-Override")
    if sw & 128: parts.append("HK-Override")
    return " | ".join(parts)

# ─── Hauptlogik ───────────────────────────────────────────────────────────────

def connect(host: str, port: int) -> ModbusTcpClient:
    client = ModbusTcpClient(host=host, port=port, timeout=3)
    if not client.connect():
        print(f"[ERROR] Verbindung zu {host}:{port} fehlgeschlagen.", file=sys.stderr)
        sys.exit(1)
    return client

def read_status(client: ModbusTcpClient, unit: int):
    """Liest und zeigt aktuellen Override-Status an."""
    # xSetpoints[17] und [18] lesen
    r_sp = client.read_holding_registers(REG_WW_OVERRIDE, count=2, slave=unit)
    # xMeasure[11] und [33] lesen
    r_ms = client.read_holding_registers(REG_STATUS_WORD, count=1, slave=unit)
    r_ov = client.read_holding_registers(REG_DO_OVERRIDE_STATUS, count=1, slave=unit)

    print("─" * 50)
    print("  Pumpenstatus (aktuell)")
    print("─" * 50)

    if not r_sp.isError():
        ww_val = modbus_to_int(r_sp.registers[0])
        hk_val = modbus_to_int(r_sp.registers[1])
        print(f"  WW-Pumpe (xSetpoints[17]): {pump_state_str(ww_val):30s} (Wert: {ww_val})")
        print(f"  HK-Pumpe (xSetpoints[18]): {pump_state_str(hk_val):30s} (Wert: {hk_val})")
    else:
        print("  [WARN] xSetpoints lesen fehlgeschlagen")

    if not r_ms.isError():
        sw = r_ms.registers[0]
        print(f"  Status-Word:               {status_word_str(sw)}")

    if not r_ov.isError():
        ov = r_ov.registers[0]
        print(f"  xMeasure[33] Override-Bits: WW={'aktiv' if ov & 1 else 'inaktiv'}  HK={'aktiv' if ov & 2 else 'inaktiv'}")
    print("─" * 50)

def set_pump(client: ModbusTcpClient, unit: int, pump: str, action: str):
    """Setzt Override für eine oder beide Pumpen."""
    action_map = {"on": 1, "ein": 1, "off": -1, "aus": -1, "auto": 0}
    value = action_map.get(action.lower())
    if value is None:
        print(f"[ERROR] Unbekannte Aktion '{action}'. Erlaubt: on/ein, off/aus, auto", file=sys.stderr)
        sys.exit(1)

    reg_map = {
        "ww":   [(REG_WW_OVERRIDE, "WW-Pumpe (O1)")],
        "hk":   [(REG_HK_OVERRIDE, "HK-Pumpe (O2)")],
        "both": [(REG_WW_OVERRIDE, "WW-Pumpe (O1)"), (REG_HK_OVERRIDE, "HK-Pumpe (O2)")],
        "alle": [(REG_WW_OVERRIDE, "WW-Pumpe (O1)"), (REG_HK_OVERRIDE, "HK-Pumpe (O2)")],
    }
    targets = reg_map.get(pump.lower())
    if targets is None:
        print(f"[ERROR] Unbekannte Pumpe '{pump}'. Erlaubt: ww, hk, both", file=sys.stderr)
        sys.exit(1)

    modbus_val = int_to_modbus(value)
    action_label = {1: "ZWANGS-EIN", -1: "ZWANGS-AUS", 0: "AUTOMATIK"}[value]

    for reg, name in targets:
        result = client.write_register(reg, modbus_val, slave=unit)
        if result.isError():
            print(f"[ERROR] Schreiben auf {name} (Register {reg}) fehlgeschlagen!", file=sys.stderr)
        else:
            print(f"  ✓  {name:20s} → {action_label}")

# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pumpensteuerung via Modbus TCP (WW- und HK-Pumpe)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python pump_control.py status
  python pump_control.py ww on
  python pump_control.py hk aus
  python pump_control.py both auto
  python pump_control.py --host 10.0.0.5 ww ein
  python pump_control.py --host 10.0.0.5 --port 502 hk off

Pumpen:   ww   = Warmwasser-Pumpe (O1_WWPump)
          hk   = Heizkreis-Pumpe  (O2_UmwaelzHK1)
          both = beide Pumpen gleichzeitig

Aktionen: on / ein  = Zwangs-EIN  (Override aktiv)
          off / aus = Zwangs-AUS  (Override aktiv)
          auto      = Automatik   (Override aufheben)
        """
    )
    parser.add_argument("--host",  default=DEFAULT_HOST, help=f"PLC IP-Adresse (default: {DEFAULT_HOST})")
    parser.add_argument("--port",  default=DEFAULT_PORT, type=int, help=f"Modbus-Port (default: {DEFAULT_PORT})")
    parser.add_argument("--unit",  default=DEFAULT_UNIT, type=int, help=f"Modbus Unit ID (default: {DEFAULT_UNIT})")
    parser.add_argument("pump",    nargs="?", help="Pumpe: ww | hk | both")
    parser.add_argument("action",  nargs="?", help="Aktion: on/ein | off/aus | auto")

    args = parser.parse_args()

    # Nur "status" ohne pump/action
    if args.pump is None or args.pump.lower() == "status":
        client = connect(args.host, args.port)
        try:
            read_status(client, args.unit)
        finally:
            client.close()
        return

    if args.action is None:
        parser.print_help()
        sys.exit(1)

    client = connect(args.host, args.port)
    try:
        print(f"  Verbunden mit {args.host}:{args.port} (Unit {args.unit})")
        set_pump(client, args.unit, args.pump, args.action)
        print()
        read_status(client, args.unit)
    finally:
        client.close()

if __name__ == "__main__":
    main()

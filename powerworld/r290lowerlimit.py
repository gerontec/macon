#!/usr/bin/python3
"""
r290lowerlimit.py - EEV Lower Limit Heizmodus setzen (R290 Wärmepumpe)

Liest oder schreibt die Register 0x0058–0x005F (Mode 00–07).
Ohne Argument: aktuellen Stand anzeigen.
Mit --up:       Alle Modes um eine Stufe (+5 Steps) erhöhen.
Mit Argument:   Neuen Wert (Steps) für Mode 00 und 01 setzen.

Verwendung:
  python3 r290lowerlimit.py            # nur anzeigen
  python3 r290lowerlimit.py --up       # alle Modes um +5 Steps erhöhen
  python3 r290lowerlimit.py 45         # Mode 00+01 auf 45 Steps (90P) setzen
  python3 r290lowerlimit.py 40         # Mode 00+01 auf 40 Steps (80P) setzen

Wertetabelle:
  Steps  Pulse  Anmerkung
  -----  -----  ---------
    50    100   Factory Default Mode 00/01
    45     90   nächste Stufe (=Mode 02–04 Factory)
    40     80   wie Mode 05 Factory
    35     70
    30     60   wie Mode 06/07 Factory
"""

import glob
import os
import sys
from time import sleep

from pymodbus.client import ModbusSerialClient

BAUDRATE  = 9600
PARITY    = 'N'
SLAVE_ID  = 1
DELAY_S   = 0.5
MB_TIMEOUT = 2.0

MODE_REGS = [0x0058, 0x0059, 0x005A, 0x005B, 0x005C, 0x005D, 0x005E, 0x005F]


def get_prolific_ports():
    ports = []
    for path in sorted(glob.glob('/sys/class/tty/ttyUSB*/device')):
        try:
            vid_path = os.path.realpath(path + '/../../idVendor')
            with open(vid_path) as f:
                if f.read().strip().lower() == '067b':
                    ports.append('/dev/' + os.path.basename(os.path.dirname(path)))
        except Exception:
            pass
    return ports


def connect():
    for port in get_prolific_ports():
        client = ModbusSerialClient(port=port, baudrate=BAUDRATE, parity=PARITY,
                                    stopbits=1, bytesize=8, timeout=MB_TIMEOUT)
        if not client.connect():
            continue
        sleep(DELAY_S)
        r = client.read_holding_registers(address=0x0003, count=1, slave=SLAVE_ID)
        if not r.isError():
            print(f"Wärmepumpe auf {port}")
            return client
        client.close()
    print("FEHLER: Wärmepumpe nicht gefunden.", file=sys.stderr)
    sys.exit(1)


def show(client):
    sleep(DELAY_S)
    r = client.read_holding_registers(address=0x0058, count=8, slave=SLAVE_ID)
    if r.isError():
        print(f"Lesefehler: {r}", file=sys.stderr)
        return
    print("\nEEV Lower Limit Heizmodus (0x0058–0x005F):")
    for i, val in enumerate(r.registers):
        marker = ' ← Mode 00/01 (wird gesetzt)' if i < 2 else ''
        print(f"  Mode {i:02d} (0x{0x0058+i:04X}): {val:3d} Steps = {val*2:3d} Pulse{marker}")
    print()


def write_all(client, values):
    """Schreibt alle 8 Mode-Register. values = Liste mit 8 Steps-Werten."""
    print(f"\nSchreibe alle 8 Modes ...")
    for i, (reg, steps) in enumerate(zip(MODE_REGS, values)):
        if not (10 <= steps <= 120):
            print(f"  FEHLER Mode {i:02d}: {steps} Steps außerhalb 10–120", file=sys.stderr)
            continue
        sleep(DELAY_S)
        w = client.write_register(address=reg, value=steps, slave=SLAVE_ID)
        if w.isError():
            print(f"  FEHLER 0x{reg:04X}: {w}", file=sys.stderr)
        else:
            print(f"  OK Mode {i:02d} (0x{reg:04X}) = {steps} Steps ({steps*2}P)")

    # Verifizieren
    sleep(DELAY_S)
    r = client.read_holding_registers(address=0x0058, count=8, slave=SLAVE_ID)
    if not r.isError():
        print("\nVerifizierung:")
        for i, (val, expected) in enumerate(zip(r.registers, values)):
            ok = '✓' if val == expected else f'✗ ABWEICHUNG (erwartet {expected})'
            print(f"  Mode {i:02d}: {val} Steps ({val*2}P) {ok}")


def increment_all(client):
    """Liest alle Modes und erhöht jeden um +5 Steps (eine Stufe)."""
    sleep(DELAY_S)
    r = client.read_holding_registers(address=0x0058, count=8, slave=SLAVE_ID)
    if r.isError():
        print(f"Lesefehler: {r}", file=sys.stderr)
        sys.exit(1)
    current = r.registers
    new_values = [min(v + 5, 120) for v in current]
    print("\nErhöhe alle Modes um +5 Steps (eine Stufe):")
    for i, (old, new) in enumerate(zip(current, new_values)):
        print(f"  Mode {i:02d}: {old} Steps → {new} Steps ({new*2}P)")
    write_all(client, new_values)


def write_mode01(client, steps):
    if not (10 <= steps <= 120):
        print("FEHLER: Wert muss zwischen 10 und 120 Steps liegen.", file=sys.stderr)
        sys.exit(1)

    print(f"\nSetze Mode 00+01 auf {steps} Steps = {steps*2} Pulse ...")
    for reg in [0x0058, 0x0059]:
        sleep(DELAY_S)
        w = client.write_register(address=reg, value=steps, slave=SLAVE_ID)
        if w.isError():
            print(f"  FEHLER 0x{reg:04X}: {w}", file=sys.stderr)
        else:
            print(f"  OK 0x{reg:04X} = {steps} Steps ({steps*2}P)")

    # Verifizieren
    sleep(DELAY_S)
    r = client.read_holding_registers(address=0x0058, count=2, slave=SLAVE_ID)
    if not r.isError():
        for i, val in enumerate(r.registers):
            ok = '✓' if val == steps else '✗ ABWEICHUNG'
            print(f"  Verify Mode {i:02d}: {val} Steps ({val*2}P) {ok}")


def main():
    client = connect()
    try:
        show(client)
        if len(sys.argv) > 1:
            if sys.argv[1] == '--up':
                increment_all(client)
            elif sys.argv[1] == '--down':
                sleep(DELAY_S)
                r = client.read_holding_registers(address=0x0058, count=8, slave=SLAVE_ID)
                if r.isError():
                    print(f"Lesefehler: {r}", file=sys.stderr)
                    sys.exit(1)
                new_values = [max(v - 5, 10) for v in r.registers]
                print("\nReduziere alle Modes um -5 Steps (eine Stufe):")
                for i, (old, new) in enumerate(zip(r.registers, new_values)):
                    print(f"  Mode {i:02d}: {old} Steps -> {new} Steps ({new*2}P)")
                write_all(client, new_values)
            else:
                write_mode01(client, int(sys.argv[1]))
            print()
            show(client)
    finally:
        client.close()


if __name__ == '__main__':
    main()

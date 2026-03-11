#!/usr/bin/python3
"""
r290_taktbetrieb.py — Taktbetrieb-Konfiguration der Powerworld R290 Wärmepumpe

Zeigt und ändert die für kontrollierten Taktbetrieb relevanten Register:

  P01 (0x00E7): Hysterese Heizung/Kühlung — WP startet wieder wenn Tank um
                diesen Wert unter den Sollwert gesunken ist. 2–18°C.
                ↑ Erhöhen → längere Zyklen → besserer COP bei Überdimensionierung

  P02 (0x00E8): Hysterese Warmwasser — analog für Warmwasser-Modus

  Frequenzgrenzen R21–R28 (0x0115–0x011C): Min/Max-Hz je Temperaturbereich
  Betriebsfrequenzen R00–R12 (0x011F–0x012B): Stufen-Frequenzen

Verwendung:
    python3 r290_taktbetrieb.py                     # Nur anzeigen (kein Write)
    python3 r290_taktbetrieb.py --hysteresis 6      # Hysterese Heizung auf 6°C
    python3 r290_taktbetrieb.py --hysteresis-hw 5   # Hysterese Warmwasser auf 5°C
    python3 r290_taktbetrieb.py --freq-min 55       # Alle R21-R24 Untergrenzen auf 55 Hz
    python3 r290_taktbetrieb.py --freq-max 60       # Alle R25-R28 Obergrenzen auf 60 Hz
    python3 r290_taktbetrieb.py --restore            # Zeigt Backup-Werte zum manuellen Vergleich

ACHTUNG: Schreibt direkt in die WP-Steuerung. Vor Änderungen immer zuerst
         r290_backup_all.py ausführen um die aktuellen Settings zu sichern!
"""

import argparse
import glob
import os
import subprocess
import sys
from time import sleep

from pymodbus.client import ModbusSerialClient

# --- Modbus ---
BAUDRATE   = 9600
PARITY     = 'N'
SLAVE_ID   = 1
DELAY_S    = 0.5
MB_TIMEOUT = 2.0

# --- Crontab-Daemon ---
CRON_MATCH = 'modbheatr290mb'

# --- Taktbetrieb-Register ---
REG_HYSTERESIS_HEAT = 0x00E7   # P01 Re-start Temp Diff Heating/Cooling (2–18°C)
REG_HYSTERESIS_HW   = 0x00E8   # P02 Re-start Temp Diff Hot Water (2–18°C)
REG_SETPOINT_HEAT   = 0x00C0   # P05 Heating Set Temperature (15–50°C)
REG_SETPOINT_HW     = 0x00BE   # P03 Hot Water Set Temperature (28–60°C)
REG_SETPOINT_COOL   = 0x00BF   # P04 Cooling Set Temperature (7–30°C)
REG_MODE            = 0x0043   # Betriebsmodus (0=HW, 1=Heizung, 2=Kühlung, ...)
REG_PARAM_FLAG      = 0x003F   # On/Off + EEV-Modus-Bits

# Frequenzgrenzen
FREQ_LOWER_LIMITS = [
    (0x0115, 'R21 Frequenz-Untergrenze 01 (niedrigste Teillast, Hz)'),
    (0x0116, 'R22 Frequenz-Untergrenze 02'),
    (0x0117, 'R23 Frequenz-Untergrenze 03'),
    (0x0118, 'R24 Frequenz-Untergrenze 04'),
]
FREQ_UPPER_LIMITS = [
    (0x0119, 'R25 Frequenz-Obergrenze 01 (Hz)'),
    (0x011A, 'R26 Frequenz-Obergrenze 02'),
    (0x011B, 'R27 Frequenz-Obergrenze 03'),
    (0x011C, 'R28 Frequenz-Obergrenze 04'),
]
FREQ_STEPS = [
    (0x011F, 'R00 Betriebsfrequenz Stufe 1 (Hz)'),
    (0x0120, 'R01 Betriebsfrequenz Stufe 2'),
    (0x0121, 'R02 Betriebsfrequenz Stufe 3'),
    (0x0122, 'R03 Betriebsfrequenz Stufe 4'),
    (0x0123, 'R04 Betriebsfrequenz Stufe 5'),
    (0x0124, 'R05 Betriebsfrequenz Stufe 6'),
    (0x0125, 'R06 Betriebsfrequenz Stufe 7'),
    (0x0126, 'R07 Betriebsfrequenz Stufe 8'),
    (0x0127, 'R08 Betriebsfrequenz Stufe 9'),
    (0x0128, 'R09 Betriebsfrequenz Stufe 10'),
    (0x0129, 'R10 Betriebsfrequenz Stufe 11'),
    (0x012A, 'R11 Betriebsfrequenz Stufe 12'),
    (0x012B, 'R12 Untergrenze Konstanttemperatur-Betrieb (Hz)'),
    (0x012C, 'R13 Obergrenze Konstanttemperatur-Betrieb (Hz)'),
]
MANUAL_FREQ = [(0x011E, 'Manuelle Frequenz (Hz, nur wenn Bit2 0x003F gesetzt)')]


# -----------------------------------------------------------------------
def _get_crontab():
    r = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


def _set_crontab(content):
    subprocess.run(['crontab', '-'], input=content, text=True, check=True)


def stop_daemon():
    lines = _get_crontab().splitlines(keepends=True)
    new_lines = []
    changed = False
    for line in lines:
        if CRON_MATCH in line and not line.startswith('#'):
            new_lines.append('#PAUSED# ' + line)
            changed = True
        else:
            new_lines.append(line)
    if changed:
        _set_crontab(''.join(new_lines))
        print(f"Daemon '{CRON_MATCH}' auskommentiert, warte 3 s …")
    else:
        print(f"Daemon '{CRON_MATCH}' nicht gefunden oder bereits inaktiv.")
    sleep(3)


def start_daemon():
    lines = _get_crontab().splitlines(keepends=True)
    new_lines = []
    changed = False
    for line in lines:
        if line.startswith('#PAUSED# ') and CRON_MATCH in line:
            new_lines.append(line[len('#PAUSED# '):])
            changed = True
        else:
            new_lines.append(line)
    if changed:
        _set_crontab(''.join(new_lines))
        print(f"Daemon '{CRON_MATCH}' wiederhergestellt.")
    else:
        print(f"Kein auskommentierter Daemon '{CRON_MATCH}' gefunden.")


# -----------------------------------------------------------------------
def get_prolific_ports():
    ports = []
    for path in sorted(glob.glob('/sys/class/tty/ttyUSB*/device')):
        try:
            vid_path = os.path.realpath(path + '/../../idVendor')
            with open(vid_path) as f:
                if f.read().strip().lower() == '067b':
                    dev = '/dev/' + os.path.basename(os.path.dirname(path))
                    ports.append(dev)
        except Exception:
            pass
    return ports


def find_modbus_port():
    ports = get_prolific_ports()
    if not ports:
        print("Keine Prolific ttyUSB-Geräte gefunden.", file=sys.stderr)
        return None
    for port in ports:
        try:
            client = ModbusSerialClient(
                port=port, baudrate=BAUDRATE,
                parity=PARITY, stopbits=1, bytesize=8,
                timeout=2,
            )
            if not client.connect():
                client.close()
                continue
            sleep(DELAY_S)
            r = client.read_holding_registers(address=0x0003, count=1, slave=SLAVE_ID)
            client.close()
            if not r.isError():
                print(f"Wärmepumpe: {port}")
                return port
        except Exception:
            pass
    print(f"Keine Wärmepumpe auf Prolific-Ports: {ports}", file=sys.stderr)
    return None


# -----------------------------------------------------------------------
def read_reg(client, addr, label=""):
    sleep(DELAY_S)
    r = client.read_holding_registers(address=addr, count=1, slave=SLAVE_ID)
    if r.isError():
        print(f"  Lesefehler 0x{addr:04X} {label}: {r}", file=sys.stderr)
        return None
    return r.registers[0]


def write_reg(client, addr, value, label=""):
    sleep(DELAY_S)
    r = client.write_register(address=addr, value=value, slave=SLAVE_ID)
    if r.isError():
        print(f"  Schreibfehler 0x{addr:04X} {label}: {r}", file=sys.stderr)
        return False
    # Verify
    sleep(DELAY_S)
    verify = read_reg(client, addr, label)
    if verify == value:
        print(f"  ✓ 0x{addr:04X} {label}: {value}")
        return True
    else:
        print(f"  ✗ 0x{addr:04X} {label}: geschrieben={value}, gelesen={verify}", file=sys.stderr)
        return False


def mode_name(val):
    return {0: 'Warmwasser', 1: 'Heizung', 2: 'Kühlung',
            3: 'WW+Heizung', 4: 'WW+Kühlung'}.get(val, f'Unbekannt({val})')


# -----------------------------------------------------------------------
def show_status(client):
    print("\n" + "=" * 72)
    print("  TAKTBETRIEB-RELEVANTE REGISTER  —  R290 Wärmepumpe")
    print("=" * 72)

    # Aktueller Modus & On/Off
    pf = read_reg(client, REG_PARAM_FLAG, "Parameter Flag 1")
    md = read_reg(client, REG_MODE, "Modus")
    if pf is not None:
        on = bool(pf & 0x01)
        eev_man = bool(pf & 0x02)
        freq_man = bool(pf & 0x04)
        print(f"\nBetrieb:  {'EIN' if on else 'AUS'}  |  EEV: {'MANUELL' if eev_man else 'AUTO'}  |  Freq: {'MANUELL' if freq_man else 'AUTO'}")
        print(f"  (0x{REG_PARAM_FLAG:04X} = 0x{pf:04X})")
    if md is not None:
        print(f"Modus:    {mode_name(md)} ({md})")

    # Sollwerte
    print("\n--- Temperatursollwerte ---")
    for addr, label, unit_str in [
        (REG_SETPOINT_HEAT, "P05 Heizung Sollwert", "°C (15–50)"),
        (REG_SETPOINT_HW,   "P03 Warmwasser Sollwert", "°C (28–60)"),
        (REG_SETPOINT_COOL, "P04 Kühlung Sollwert", "°C (7–30)"),
    ]:
        val = read_reg(client, addr, label)
        if val is not None:
            print(f"  0x{addr:04X}  {label}: {val} {unit_str}")

    # Hysterese — DAS ist der Schlüssel für Taktbetrieb
    print("\n--- ★ HYSTERESE (Taktbetrieb-Schlüsselparameter) ---")
    h_heat = read_reg(client, REG_HYSTERESIS_HEAT, "P01 Hysterese Heizung")
    h_hw   = read_reg(client, REG_HYSTERESIS_HW,   "P02 Hysterese Warmwasser")
    if h_heat is not None:
        print(f"  0x{REG_HYSTERESIS_HEAT:04X}  P01 Hysterese Heizung/Kühlung: {h_heat} °C  (2–18)")
        if h_heat <= 2:
            print(f"          → ZU KLEIN! WP taktet sehr kurz. Empfehlung: 5–8°C für 22kW-Anlage.")
        elif h_heat <= 4:
            print(f"          → Etwas klein. Empfehlung: auf 5–7°C erhöhen.")
        else:
            print(f"          → OK für Taktbetrieb.")
    if h_hw is not None:
        print(f"  0x{REG_HYSTERESIS_HW:04X}  P02 Hysterese Warmwasser:       {h_hw} °C  (2–18)")

    # Frequenzgrenzen
    print("\n--- ★ FREQUENZGRENZEN ---")
    print("  (R21–R24 = Untergrenzen, R25–R28 = Obergrenzen je Außentemperaturbereich)")
    for addr, label in FREQ_LOWER_LIMITS + FREQ_UPPER_LIMITS:
        val = read_reg(client, addr, label)
        if val is not None:
            note = ""
            if addr in (0x0115, 0x0116, 0x0117, 0x0118) and val <= 40:
                note = "  ← Untergrenze Teillast"
            print(f"  0x{addr:04X}  {label}: {val} Hz{note}")

    # Betriebsfrequenz-Stufen
    print("\n--- Betriebsfrequenz-Stufen R00–R13 ---")
    print("  (Verdichter-Betriebspunkte; Stufe 1 = niedrigste Last = meistgenutzter Punkt)")
    for addr, label in FREQ_STEPS + MANUAL_FREQ:
        val = read_reg(client, addr, label)
        if val is not None:
            note = ""
            if addr == 0x011F and val <= 42:
                note = f"  ← Stufe 1 = Minimum-Hz = aktueller Dauerbetriebspunkt!"
            print(f"  0x{addr:04X}  {label}: {val} Hz{note}")

    print("\n" + "=" * 72)


# -----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Taktbetrieb-Konfiguration der R290 Wärmepumpe"
    )
    parser.add_argument(
        '--hysteresis', type=int, metavar='N',
        help='Hysterese Heizung/Kühlung (P01, 0x00E7) setzen, N in °C (2–18). '
             'Empfehlung für 22 kW bei Teillast: 5–8°C.'
    )
    parser.add_argument(
        '--hysteresis-hw', type=int, metavar='N',
        help='Hysterese Warmwasser (P02, 0x00E8) setzen, N in °C (2–18).'
    )
    parser.add_argument(
        '--freq-min', type=int, metavar='HZ',
        help='Alle Frequenz-Untergrenzen R21–R24 (0x0115–0x0118) auf HZ setzen. '
             'Setzt die Mindestfrequenz des Verdichters (z.B. 50 Hz). Vorsicht!'
    )
    parser.add_argument(
        '--r12', type=int, metavar='HZ',
        help='R12 (0x012B) Untergrenze Konstanttemperatur-Betrieb auf HZ setzen (30–80). '
             'Das ist der tatsächliche Verdichter-Floor im Normalbetrieb. '
             'Aktuell 40 Hz → 50 Hz empfohlen für besseren COP.'
    )
    parser.add_argument(
        '--freq-max', type=int, metavar='HZ',
        help='Alle Frequenz-Obergrenzen R25–R28 (0x0119–0x011C) auf HZ setzen. '
             'Begrenzt die maximale Verdichterfrequenz (z.B. 60 Hz). '
             'Verhindert Überhitzung/Überstrom bei hoher Last.'
    )
    args = parser.parse_args()

    do_write = any([args.hysteresis, args.hysteresis_hw, args.freq_min, args.freq_max, args.r12])

    if do_write:
        # Plausibilitätsprüfung
        if args.hysteresis is not None and not (2 <= args.hysteresis <= 18):
            print("FEHLER: --hysteresis muss zwischen 2 und 18 liegen.", file=sys.stderr)
            sys.exit(1)
        if args.hysteresis_hw is not None and not (2 <= args.hysteresis_hw <= 18):
            print("FEHLER: --hysteresis-hw muss zwischen 2 und 18 liegen.", file=sys.stderr)
            sys.exit(1)
        if args.freq_min is not None and not (30 <= args.freq_min <= 80):
            print("FEHLER: --freq-min muss zwischen 30 und 80 Hz liegen.", file=sys.stderr)
            sys.exit(1)
        if args.freq_max is not None and not (30 <= args.freq_max <= 80):
            print("FEHLER: --freq-max muss zwischen 30 und 80 Hz liegen.", file=sys.stderr)
            sys.exit(1)
        if args.r12 is not None and not (30 <= args.r12 <= 80):
            print("FEHLER: --r12 muss zwischen 30 und 80 Hz liegen.", file=sys.stderr)
            sys.exit(1)

    stop_daemon()
    try:
        port = find_modbus_port()
        if not port:
            print("FEHLER: Wärmepumpe nicht gefunden.", file=sys.stderr)
            sys.exit(1)

        client = ModbusSerialClient(
            port=port, baudrate=BAUDRATE,
            parity=PARITY, stopbits=1, bytesize=8,
            timeout=MB_TIMEOUT,
        )
        if not client.connect():
            print("FEHLER: Modbus-Verbindung fehlgeschlagen.", file=sys.stderr)
            sys.exit(1)

        sleep(0.5)
        try:
            show_status(client)

            if do_write:
                print("\n--- SCHREIBE ÄNDERUNGEN ---")

                if args.hysteresis is not None:
                    write_reg(client, REG_HYSTERESIS_HEAT, args.hysteresis,
                              f"P01 Hysterese Heizung → {args.hysteresis}°C")

                if args.hysteresis_hw is not None:
                    write_reg(client, REG_HYSTERESIS_HW, args.hysteresis_hw,
                              f"P02 Hysterese Warmwasser → {args.hysteresis_hw}°C")

                if args.freq_min is not None:
                    for addr, label in FREQ_LOWER_LIMITS:
                        write_reg(client, addr, args.freq_min,
                                  f"{label} → {args.freq_min} Hz")

                if args.freq_max is not None:
                    for addr, label in FREQ_UPPER_LIMITS:
                        write_reg(client, addr, args.freq_max,
                                  f"{label} → {args.freq_max} Hz")

                if args.r12 is not None:
                    write_reg(client, 0x012B, args.r12,
                              f"R12 Untergrenze Konstanttemp-Betrieb → {args.r12} Hz")

                print("\n--- WERTE NACH ÄNDERUNG ---")
                show_status(client)

        finally:
            client.close()

    finally:
        start_daemon()

    if not do_write:
        print("\nHINWEIS: Keine Änderungen durchgeführt (nur Lesemodus).")
        print("Zum Ändern der Hysterese (z.B. auf 6°C für Taktbetrieb):")
        print("  python3 r290_taktbetrieb.py --hysteresis 6")


if __name__ == '__main__':
    main()

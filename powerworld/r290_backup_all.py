#!/usr/bin/python3
"""
r290_backup_all.py — Sichert ALLE RW-Settings der Powerworld R290 Wärmepumpe

Liest alle beschreibbaren Register (0x003F–0x016E, ~300 Adressen) per Modbus
und speichert sie in eine datierte Backup-Datei.

Verwendung:
    python3 r290_backup_all.py                 # Backup erstellen
    python3 r290_backup_all.py --show-key      # Nur Taktbetrieb-relevante Register

Der laufende Daemon (modbheatr290mb per Crontab) wird während der Ausführung
kurz pausiert und danach wieder aktiviert.
"""

import argparse
import csv
import glob
import os
import subprocess
import sys
from datetime import datetime
from time import sleep

from pymodbus.client import ModbusSerialClient

# --- Modbus ---
BAUDRATE   = 9600
PARITY     = 'N'
SLAVE_ID   = 1
DELAY_S    = 0.3
MB_TIMEOUT = 2.0
CHUNK_SIZE = 20   # Register pro Lesezugriff (WP ist instabil bei > ~30)

# --- Dateipfade ---
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PARAMS_CSV   = os.path.join(SCRIPT_DIR, 'parameters.csv')
BACKUP_DIR   = SCRIPT_DIR

# --- Crontab-Daemon ---
CRON_MATCH = 'modbheatr290mb'

# Register-Bereich: alle RW-Register laut parameters.csv
REG_START = 0x003F
REG_END   = 0x016E

# Für --show-key: Taktbetrieb-relevante Register hervorheben
KEY_REGISTERS = {
    0x00C0: 'P05 Heizung Sollwert (Set Temperature Heating)',
    0x00E7: 'P01 Hysterese Heizung/Kühlung (Re-start Temp Diff) ← WICHTIG FÜR TAKTBETRIEB',
    0x00E8: 'P02 Hysterese Warmwasser (Re-start Temp Diff Hot Water)',
    0x0115: 'R21 Frequenzuntergrenze 01 (Hz)',
    0x0116: 'R22 Frequenzuntergrenze 02 (Hz)',
    0x0117: 'R23 Frequenzuntergrenze 03 (Hz)',
    0x0118: 'R24 Frequenzuntergrenze 04 (Hz)',
    0x0119: 'R25 Frequenzobergrenze 01 (Hz)',
    0x011A: 'R26 Frequenzobergrenze 02 (Hz)',
    0x011B: 'R27 Frequenzobergrenze 03 (Hz)',
    0x011C: 'R28 Frequenzobergrenze 04 (Hz)',
    0x011F: 'R00 Verdichter-Betriebsfrequenz 1 (Hz)',
    0x012B: 'R12 Untergrenze Konstanttemperatur-Betriebsfrequenz (Hz)',
    0x012C: 'R13 Obergrenze Konstanttemperatur-Betriebsfrequenz (Hz)',
    0x011E: 'Manuelle Frequenz (Hz)',
    0x003F: 'Parameter Flag 1 (On/Off, EEV-Modus, etc.)',
    0x0040: 'Control Flag 1 (Silent, Powerful, Auto-Heizung, etc.)',
    0x0041: 'Control Flag 2',
    0x0043: 'Betriebsmodus (0=HW, 1=Heizung, 2=Kühlung, 3=HW+Hz, 4=HW+Kü)',
}


# -----------------------------------------------------------------------
# Crontab-Helfer (identisch zu read_eev_settings.py)
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
# Port-Erkennung (Prolific USB-Serial wie in anderen Skripten)
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
                print(f"Wärmepumpe gefunden: {port}")
                return port
        except Exception:
            pass
    print(f"Keine Wärmepumpe auf Prolific-Ports: {ports}", file=sys.stderr)
    return None


# -----------------------------------------------------------------------
# Parameter-Beschreibungen aus parameters.csv laden
# -----------------------------------------------------------------------
def load_param_descriptions():
    """Gibt dict {addr_int: (access, description, setting_range, note)} zurück."""
    desc = {}
    try:
        with open(PARAMS_CSV, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    addr = int(row['Address'], 16)
                    desc[addr] = (
                        row.get('Access', '').strip(),
                        row.get('Description', '').strip().replace('\n', ' '),
                        row.get('Setting Range', '').strip(),
                        row.get('Note', '').strip().replace('\n', ' '),
                    )
                except (ValueError, KeyError):
                    pass
    except FileNotFoundError:
        print(f"Warnung: {PARAMS_CSV} nicht gefunden — keine Beschreibungen.", file=sys.stderr)
    return desc


# -----------------------------------------------------------------------
# Modbus lesen
# -----------------------------------------------------------------------
def signed16(val):
    return val if val < 32768 else val - 65536


def read_registers_all(client):
    """Liest REG_START..REG_END in Chunks, gibt dict {addr: raw_value} zurück."""
    results = {}
    addr = REG_START
    while addr <= REG_END:
        count = min(CHUNK_SIZE, REG_END - addr + 1)
        sleep(DELAY_S)
        r = client.read_holding_registers(address=addr, count=count, slave=SLAVE_ID)
        if r.isError():
            print(f"  Lesefehler 0x{addr:04X}+{count}: {r}", file=sys.stderr)
        else:
            for i, val in enumerate(r.registers):
                results[addr + i] = val
        addr += count
    return results


# -----------------------------------------------------------------------
# Ausgabe & Backup
# -----------------------------------------------------------------------
def format_value(addr, raw, desc_map):
    """Gibt einen lesbaren Wert-String zurück."""
    info = desc_map.get(addr)
    if info is None:
        return f"0x{raw:04X} ({raw})"

    access, name, setting_range, note = info

    # Temperaturen: signed16
    if '℃' in setting_range or '℃' in note:
        val = signed16(raw)
        return f"{val}  [{setting_range}]"
    # n*2P (EEV-Steps → Pulse)
    if 'n*2P' in note:
        pulses = raw * 2
        return f"{raw} Steps = {pulses} Pulse  [{setting_range}]"
    # Hz-Register
    if 'Hz' in setting_range or 'Hz' in note:
        return f"{raw} Hz  [{setting_range}]"
    # Bits
    if 'Bit' in note:
        return f"0x{raw:04X} ({raw})  [Bit-Register]"
    # Sonst raw
    return f"{raw}  [{setting_range}]"


def print_and_save(results, desc_map, backup_path, show_key_only=False):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header_lines = [
        f"# R290 Wärmepumpe — Vollständiges Settings-Backup",
        f"# Datum: {now_str}",
        f"# Register-Bereich: 0x{REG_START:04X}–0x{REG_END:04X}",
        f"# Slave: {SLAVE_ID}  Baud: {BAUDRATE}",
        "# " + "=" * 70,
        "",
    ]

    lines = []

    # Abschnitte nach Funktionsgruppe
    sections = [
        (0x003F, 0x0047, "Kontroll-Flags & Modus"),
        (0x0048, 0x005F, "EEV Initialöffnung & Lower Limits (Heizung)"),
        (0x0060, 0x006A, "EEV Abtau / Warmwasser / Manuell / PID"),
        (0x006B, 0x007E, "Aux-EEV Initialöffnung & Lower Limits"),
        (0x007F, 0x0090, "Aux-EEV Spezial / Drücke / Lüftergeschwindigkeit"),
        (0x0091, 0x00B6, "Aux-EEV Warmwasser-Grenzen / Lüfter / Auspufftemperaturen"),
        (0x00B7, 0x00BD, "Überhitzungs-Korrekturen B77–B83"),
        (0x00BE, 0x00D3, "Temperatursollwerte P03–P16 / Abtau / Correction"),
        (0x00D4, 0x00E6, "Timer-Temperaturen / Reserved"),
        (0x00E7, 0x00EB, "★ HYSTERESE & ABTAU-DIFFERENZ (Taktbetrieb!)"),
        (0x00EC, 0x00FF, "Überhitzungs-Sollwerte Main+Aux EEV"),
        (0x0100, 0x010D, "Aux-EEV Auspufftemperatur-Differenzen"),
        (0x0115, 0x011C, "★ FREQUENZGRENZEN R21–R28 (Taktbetrieb!)"),
        (0x011D, 0x012E, "Enthalpy-Ventil / Manuelle Frequenz / Betriebsfrequenzen R00–R13"),
        (0x012F, 0x016E, "Timer / Desinfektion / Modellauswahl / Pumpe / Lüfter"),
    ]

    for sec_start, sec_end, sec_name in sections:
        sec_lines = []
        has_content = False
        for addr in range(sec_start, sec_end + 1):
            if addr not in results:
                continue
            raw = results[addr]
            info = desc_map.get(addr)
            if info is None:
                name = f"Unbekannt"
                access = '?'
            else:
                access, name, _, _ = info
                if show_key_only and addr not in KEY_REGISTERS:
                    continue  # In --show-key Modus nur KEY_REGISTERS anzeigen
            val_str = format_value(addr, raw, desc_map)
            is_key = addr in KEY_REGISTERS
            marker = " ◄◄◄" if is_key else ""
            sec_lines.append(f"  0x{addr:04X}  [{access}]  {val_str:40s}  {name}{marker}")
            has_content = True

        if has_content or not show_key_only:
            lines.append(f"\n--- {sec_name} ---")
            lines.extend(sec_lines)

    # Alles zusammensetzen
    all_lines = header_lines + lines
    output = '\n'.join(all_lines) + '\n'

    print(output)

    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(output)
    print(f"\n✓ Backup gespeichert: {backup_path}")

    # Taktbetrieb-Empfehlung
    if not show_key_only:
        print("\n" + "=" * 70)
        print("TAKTBETRIEB-EMPFEHLUNG:")
        print("  Register 0x00E7 (P01 Hysterese Heizung): aktueller Wert zeigt wie oft die WP taktet.")
        print("  Erhöhung auf 5–8°C → WP läuft länger pro Zyklus, besserer COP.")
        print("  Ändern mit: python3 r290_taktbetrieb.py --hysteresis 6")
        print("=" * 70)


# -----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Vollständiges Settings-Backup der R290 Wärmepumpe"
    )
    parser.add_argument(
        '--show-key', action='store_true',
        help='Nur Taktbetrieb-relevante Register anzeigen (kein Backup)'
    )
    args = parser.parse_args()

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
        desc_map = load_param_descriptions()
        print(f"Parameter-Beschreibungen geladen: {len(desc_map)} Einträge")

        try:
            print(f"\nLese Register 0x{REG_START:04X}–0x{REG_END:04X} …")
            results = read_registers_all(client)
            print(f"Gelesen: {len(results)} Register")
        finally:
            client.close()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"r290_settings_backup_{ts}.txt")

        print_and_save(results, desc_map, backup_path, show_key_only=args.show_key)

    finally:
        start_daemon()


if __name__ == '__main__':
    main()

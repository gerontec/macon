#!/usr/bin/python3
"""
read_eev_settings.py - Liest EEV/Überhitzungs-Settings der Powerworld R290 per Modbus

Liest:
  - Target Superheat Heizmodus  Mode 1–8  (0x00EC–0x00F3, signed °C, -5…+10)
  - Target Superheat Kühlmodus  Mode 1–4  (0x00F4–0x00F7, signed °C)
  - Correction-Werte            B77–B83   (0x00B7–0x00BD, signed °C, -30…+30)
  - EEV Lower Limit Heizmodus   00–07     (0x0058–0x005F, 0–240, n×2 Pulse)
  - Manueller EEV-Modus                  (0x003F Bit1: 0=auto, 1=manuell)
  - Manuelle EEV-Schritte                (0x0062, 10–225, n×2 Pulse)

Stoppt den laufenden Daemon kurz (systemd-Service), liest dann und startet ihn wieder.
"""

import glob
import os
import sys
import subprocess
from time import sleep

from pymodbus.client import ModbusSerialClient

# --- Modbus ---
BAUDRATE   = 9600
PARITY     = 'N'
SLAVE_ID   = 1
DELAY_S    = 0.5
MB_TIMEOUT = 2.0

# --- Crontab-Skript das den gleichen RS485-Port belegt ---
# Teilstring des Crontab-Eintrags zur Identifikation:
CRON_MATCH = 'modbheatr290mb'


# -----------------------------------------------------------------------
def _get_crontab():
    r = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


def _set_crontab(content):
    subprocess.run(['crontab', '-'], input=content, text=True, check=True)


def stop_daemon():
    """Kommentiert den Crontab-Eintrag aus und wartet bis laufender Job fertig ist."""
    lines   = _get_crontab().splitlines(keepends=True)
    changed = False
    new_lines = []
    for line in lines:
        if CRON_MATCH in line and not line.startswith('#'):
            new_lines.append('#PAUSED# ' + line)
            changed = True
        else:
            new_lines.append(line)
    if changed:
        _set_crontab(''.join(new_lines))
        print(f"Crontab-Eintrag '{CRON_MATCH}' auskommentiert.")
    else:
        print(f"Crontab-Eintrag '{CRON_MATCH}' nicht gefunden oder schon inaktiv.")
    sleep(3)   # ggf. laufenden Job abwarten


def start_daemon():
    """Stellt den Crontab-Eintrag wieder her."""
    lines   = _get_crontab().splitlines(keepends=True)
    changed = False
    new_lines = []
    for line in lines:
        if line.startswith('#PAUSED# ') and CRON_MATCH in line:
            new_lines.append(line[len('#PAUSED# '):])
            changed = True
        else:
            new_lines.append(line)
    if changed:
        _set_crontab(''.join(new_lines))
        print(f"Crontab-Eintrag '{CRON_MATCH}' wiederhergestellt.")
    else:
        print(f"Kein auskommentierter Eintrag '{CRON_MATCH}' gefunden.")


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
        print("Keine Prolific ttyUSB-Geräte gefunden", file=sys.stderr)
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
                print(f"Wärmepumpe gefunden auf {port}")
                return port
        except Exception:
            pass
    print(f"Wärmepumpe (Slave {SLAVE_ID}) auf keinem Prolific-Port gefunden: {ports}",
          file=sys.stderr)
    return None


# -----------------------------------------------------------------------
def signed16(val):
    """Uint16 → signed int16 (Modbus gibt unsigned zurück)."""
    return val if val < 32768 else val - 65536


def read_block(client, start, count, label):
    sleep(DELAY_S)
    r = client.read_holding_registers(address=start, count=count, slave=SLAVE_ID)
    if r.isError():
        print(f"  Lesefehler [{label} 0x{start:04X}+{count}]: {r}", file=sys.stderr)
        return None
    return r.registers


# -----------------------------------------------------------------------
def read_settings(client):
    print("\n" + "=" * 60)
    print("  EEV / Überhitzungs-Settings  R290 Wärmepumpe")
    print("=" * 60)

    # --- Target Superheat Heizmodus Mode 1–8 ---
    regs = read_block(client, 0x00EC, 8, "SH Heating")
    if regs:
        print("\nTarget Superheat Heizmodus (0x00EC–0x00F3):")
        for i, raw in enumerate(regs, start=1):
            val = signed16(raw)
            print(f"  Mode {i}  (0x{0x00EB + i:04X}): {val:+d} °C   [raw=0x{raw:04X}]")

    # --- Target Superheat Kühlmodus Mode 1–4 ---
    regs = read_block(client, 0x00F4, 4, "SH Cooling")
    if regs:
        print("\nTarget Superheat Kühlmodus (0x00F4–0x00F7):")
        for i, raw in enumerate(regs, start=1):
            val = signed16(raw)
            print(f"  Mode {i}  (0x{0x00F3 + i:04X}): {val:+d} °C   [raw=0x{raw:04X}]")

    # --- Correction-Werte B77–B83 (0x00B7–0x00BD) ---
    regs = read_block(client, 0x00B7, 7, "Correction B77-B83")
    if regs:
        print("\nCorrection-Werte B77–B83 (0x00B7–0x00BD, -30…+30 °C):")
        for i, raw in enumerate(regs):
            addr = 0x00B7 + i
            val  = signed16(raw)
            print(f"  B{77 + i}  (0x{addr:04X}): {val:+d} °C   [raw=0x{raw:04X}]")

    # --- EEV Lower Limit Heizmodus 00–07 (0x0058–0x005F) ---
    regs = read_block(client, 0x0058, 8, "Lower Limit Heating")
    if regs:
        print("\nAutomatic Adjustment Lower Limit Heizmodus 00–07 (0x0058–0x005F):")
        for i, raw in enumerate(regs):
            addr   = 0x0058 + i
            pulses = raw * 2
            print(f"  Mode {i:02d} (0x{addr:04X}): {raw} Steps = {pulses} Pulse   [raw=0x{raw:04X}]")

    # --- Manueller EEV-Modus & Schritte ---
    regs_flag = read_block(client, 0x003F, 1, "Parameter Flag")
    regs_man  = read_block(client, 0x0062, 1, "Manual Steps")

    if regs_flag:
        raw      = regs_flag[0]
        man_mode = (raw >> 1) & 0x01   # Bit1
        print(f"\nEEV Modus (0x003F Bit1): {'MANUELL' if man_mode else 'AUTO'}   [0x003F=0x{raw:04X}]")

    if regs_man:
        raw    = regs_man[0]
        pulses = raw * 2
        print(f"Manuelle EEV-Schritte (0x0062): {raw} Steps = {pulses} Pulse   [raw=0x{raw:04X}]")

    print("\n" + "=" * 60)


# -----------------------------------------------------------------------
def main():
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
            read_settings(client)
        finally:
            client.close()

    finally:
        start_daemon()


if __name__ == '__main__':
    main()

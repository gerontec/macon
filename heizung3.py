#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
================================================================================
HEIZUNGSSTEUERUNG v4.4.3 - MIT BITMASKEN-DEKODIERUNG & NACHTABSENKUNG
================================================================================
ÄNDERUNGEN v4.4.3:
- MQTT Timeout erhöht: 3s → 6s für zuverlässigeren Empfang

ÄNDERUNGEN v4.4.2:
- RU ist Drucksensor (nicht PT1000) - zeigt nun raw-Wert statt 0.00°C
- temp_ruecklauf = None in Datenbank (keine falsche Temperatur mehr)

ÄNDERUNGEN v4.4.1:
- MQTT Host korrigiert auf 192.168.178.218
- Tank-Temperatur immer angezeigt (auch wenn '---')
- MQTT-Temperatur immer angezeigt (auch wenn '---')
- MQTT Timeout auf 3s reduziert
- Konsistente Temperatur-Formatierung

ÄNDERUNGEN v4.4.0:
- Nachtabsenkungszeiten über Command-Line konfigurierbar
- Reason-Bytes werden als Bitmasken dekodiert (nicht mehr als einzelne Werte)
- Zeigt kombinierte Gründe an (z.B. "Frost<3°C + VL<Soll")
- Bit-Definitionen direkt aus PLC-Code übernommen
- Erweiterbar für zukünftige Reason-Bits

ÄNDERUNGEN v4.3.0:
- Hardware-Änderung: WW und HK Relais nun invertiert (NC = Normally Closed)
- Status-Word Dekodierung korrigiert für invertierte Logik
- QB0 Physical Output Byte korrekt interpretiert
- Database speichert korrekten Pumpen-Status
- R290 Wassertank-Temperatur wird in DB gespeichert

ÄNDERUNGEN v4.2.0:
- Command-line Argumente für Pumpen-Override
- Default: Status beibehalten (None)
- Verwendung: ./heizung3.py [--ww 0|1|-1] [--hk 0|1|-1] [--br 0|1|-1]
================================================================================
"""
import pymysql
import sys
import argparse
from datetime import datetime
from pymodbus.client import ModbusTcpClient
import paho.mqtt.client as mqtt
import time

VERSION = '4.4.3'

# =============================================================================
# KONFIGURATION
# =============================================================================
SPS_IP = '192.168.178.2'
MQTT_BROKER = '192.168.178.218'  # ← KORRIGIERT: nicht mehr localhost!
MQTT_TOPIC = 'Node3/pin4'
MQTT_TIMEOUT = 6  # ← ERHÖHT: 6s für zuverlässigen Empfang

DB_CONFIG = {
    'host': '10.8.0.1',
    'user': 'gh',
    'password': 'a12345',
    'database': 'wagodb',
    'charset': 'utf8mb4'
}

# REGISTER-ADRESSEN (Modbus-Adresse)
# WICHTIG: Modbus-Adresse = MW-Nummer + 12288
# MW0 → 12288, MW32 → 12320, MW96 → 12384
ADDR_MEASURE = 12320      # xMeasure[1..32] - MW32-MW95
ADDR_SETPOINTS = 12384    # xSetpoints[1..16] - MW96-MW111
ADDR_SYSTEM = 12416       # xSystem[1..8] - MW128-MW135
ADDR_ALARMS = 12432       # xAlarms[1..8] - MW144-MW151

# Setpoint-Offsets (Array-Index - 1, da Array bei [1] startet)
SETPOINT_NACHT_START = 4    # xSetpoints[5] - Nachtabsenkung Start (0-23)
SETPOINT_NACHT_END = 5      # xSetpoints[6] - Nachtabsenkung Ende (0-23)
SETPOINT_TANK_TEMP = 12     # xSetpoints[13] - R290 Wassertank
SETPOINT_WW_OVERRIDE = 13   # xSetpoints[14]
SETPOINT_HK_OVERRIDE = 14   # xSetpoints[15]
SETPOINT_BR_OVERRIDE = 15   # xSetpoints[16]

# =============================================================================
# HARDWARE-KONFIGURATION - INVERTIERTE RELAIS
# =============================================================================
# WICHTIG: Seit 2026-01-03 verwenden WW und HK invertierte Relais (NC)
# - O1_WWPump:     FALSE = Pumpe AN,  TRUE = Pumpe AUS  (invertiert!)
# - O2_UmwaelzHK1: FALSE = Pumpe AN,  TRUE = Pumpe AUS  (invertiert!)
# - O3_Brunnen:    TRUE  = Pumpe AN,  FALSE = Pumpe AUS (normal)
# Grund: Fail-Safe bei SPS-Ausfall (WW+HK laufen weiter)
# =============================================================================

# =============================================================================
# REASON BIT DEFINITIONS (aus PLC-Code)
# =============================================================================
# Warmwasser-Pumpe (bWW_Reason)
WW_REASON_TEMP_DIFF = 0x01    # Bit 0: ΔT≥2°C (temp_diff_ww >= 2.0)
WW_REASON_OVERRIDE = 0x80     # Bit 7: Manueller Override (xSetpoints[14] > 0)

# Heizkreis-Pumpe (bHK_Reason)
HK_REASON_FROSTSCHUTZ = 0x01  # Bit 0: Außentemp < 3°C
HK_REASON_WAERMEBEDARF = 0x02 # Bit 1: VL < Soll (temp_vorlauf < CONST_VL_SOLL_MIN)
HK_REASON_OVERRIDE = 0x80     # Bit 7: Manueller Override (xSetpoints[15] > 0)

# Brunnenpumpe (bBR_Reason)
BR_REASON_HK_ACTIVE = 0x01    # Bit 0: HK-Pumpe läuft (O2_UmwaelzHK1 = FALSE)
BR_REASON_OVERRIDE = 0x80     # Bit 7: Manueller Override (xSetpoints[16] > 0)

# =============================================================================
# REASON DECODER - BITMASKEN-AUSWERTUNG
# =============================================================================
def decode_ww_reason(reason_byte):
    """Dekodiert WW-Reason-Byte als Bitmaske"""
    if reason_byte == 0:
        return "---"
    
    reasons = []
    if reason_byte & WW_REASON_TEMP_DIFF:
        reasons.append("ΔT≥2°C")
    if reason_byte & WW_REASON_OVERRIDE:
        reasons.append("Override")
    
    return " + ".join(reasons) if reasons else f"0x{reason_byte:02X}"

def decode_hk_reason(reason_byte):
    """Dekodiert HK-Reason-Byte als Bitmaske"""
    if reason_byte == 0:
        return "---"
    
    reasons = []
    if reason_byte & HK_REASON_FROSTSCHUTZ:
        reasons.append("Frost<3°C")
    if reason_byte & HK_REASON_WAERMEBEDARF:
        reasons.append("VL<Soll")
    if reason_byte & HK_REASON_OVERRIDE:
        reasons.append("Override")
    
    return " + ".join(reasons) if reasons else f"0x{reason_byte:02X}"

def decode_br_reason(reason_byte):
    """Dekodiert BR-Reason-Byte als Bitmaske"""
    if reason_byte == 0:
        return "---"
    
    reasons = []
    if reason_byte & BR_REASON_HK_ACTIVE:
        reasons.append("HK aktiv")
    if reason_byte & BR_REASON_OVERRIDE:
        reasons.append("Override")
    
    return " + ".join(reasons) if reasons else f"0x{reason_byte:02X}"

# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================
def signed_to_unsigned(val):
    """Konvertiert signed INT (-32768..32767) zu unsigned WORD (0..65535)"""
    return val if val >= 0 else val + 65536

def unsigned_to_signed(val):
    """Konvertiert unsigned WORD (0..65535) zu signed INT (-32768..32767)"""
    return val if val < 32768 else val - 65536

# =============================================================================
# SENSOR-KALIBRIERUNG
# =============================================================================
def calc_pt1000(raw: int) -> float:
    """PT1000 Sensor"""
    if not (4000 <= raw <= 25000):
        return 0.0
    return round((float(raw) - 7134.0) / 25.0, 2)

def calc_boiler(raw: int) -> float:
    """NTC Boiler"""
    if not (4000 <= raw <= 45000):
        return 0.0
    return round((40536.0 - float(raw)) / 303.1, 2)

def calc_solar(raw: int) -> float:
    """NTC Solar"""
    if not (4000 <= raw <= 40000):
        return 0.0
    return round((float(raw) - 26402.0) / 60.0, 2)

# =============================================================================
# MQTT TEMPERATUR
# =============================================================================
mqtt_temp_value = None
mqtt_received = False

def on_message(client, userdata, msg):
    global mqtt_temp_value, mqtt_received
    try:
        mqtt_temp_value = float(msg.payload.decode())
        mqtt_received = True
    except:
        mqtt_temp_value = None

def get_mqtt_temperature():
    """Liest Raumtemperatur von MQTT Node3/pin4 (KY-9A Sensor)"""
    global mqtt_temp_value, mqtt_received
    mqtt_temp_value = None
    mqtt_received = False
    
    try:
        client = mqtt.Client()
        client.on_message = on_message
        client.connect(MQTT_BROKER, 1883, 60)
        client.subscribe(MQTT_TOPIC)
        start_time = time.time()
        client.loop_start()
        
        while not mqtt_received and (time.time() - start_time) < MQTT_TIMEOUT:
            time.sleep(0.1)
        
        client.loop_stop()
        client.disconnect()
        return mqtt_temp_value if mqtt_received else None
    except Exception as e:
        return None  # Fehler stillschweigend ignorieren

# =============================================================================
# STATUS-DEKODIERUNG - MIT INVERTIERTER RELAIS-LOGIK
# =============================================================================
def decode_status_word(sw: int) -> dict:
    """
    Dekodiert Status-Word (Register 11)
    
    WICHTIG: Status-Word wurde von SPS bereits korrigiert!
    Bit 0 = TRUE bedeutet WW-Pumpe tatsächlich AN (SPS macht Invertierung)
    Bit 1 = TRUE bedeutet HK-Pumpe tatsächlich AN (SPS macht Invertierung)
    Bit 2 = TRUE bedeutet BR-Pumpe tatsächlich AN (normal)
    """
    return {
        'ww_pumpe': bool(sw & 0x01),
        'hk_pumpe': bool(sw & 0x02),
        'brunnen': bool(sw & 0x04),
        'nacht': bool(sw & 0x08),
        'phase_a': bool(sw & 0x10),
        'data_ready': bool(sw & 0x20),
        'sensor_error': bool(sw & 0x40),
        'phase': 'A' if (sw & 0x10) else 'B'
    }

def decode_physical_outputs(qb0: int) -> dict:
    """
    Dekodiert Physical Output Byte (%QB0 / Register 32)
    
    WICHTIG: Hier sind die RAW Output-Zustände!
    - Bit 1 (O1_WWPump):     FALSE = Pumpe AN,  TRUE = Pumpe AUS  (invertiert!)
    - Bit 2 (O2_UmwaelzHK1): FALSE = Pumpe AN,  TRUE = Pumpe AUS  (invertiert!)
    - Bit 3 (O3_Brunnen):    TRUE  = Pumpe AN,  FALSE = Pumpe AUS (normal)
    """
    ww_output = bool(qb0 & 0x02)   # Bit 1
    hk_output = bool(qb0 & 0x04)   # Bit 2
    br_output = bool(qb0 & 0x08)   # Bit 3
    
    return {
        'ww_pumpe': not ww_output,  # Invertiert!
        'hk_pumpe': not hk_output,  # Invertiert!
        'brunnen': br_output        # Normal
    }

def format_uptime(sec: int) -> str:
    """Formatiert Uptime"""
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    return f"{d}d {h:02d}:{m:02d}"

def format_runtime(seconds: int) -> str:
    """Formatiert Runtime aus Sekunden in Tage, Stunden, Minuten"""
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    return f"{d}d {h:02d}h {m:02d}m"

def get_override_mode_text(val):
    """Gibt lesbare Override-Mode zurück"""
    if val < 0:
        return "OFF"
    elif val > 0:
        return "ON"
    else:
        return "Auto"

# =============================================================================
# COMMAND-LINE ARGUMENTE
# =============================================================================
def parse_arguments():
    """Parst Command-line Argumente für Pumpen-Override und Nachtabsenkung"""
    parser = argparse.ArgumentParser(
        description='Heizungssteuerung v4.4.1',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Override-Modi:
  -1 = Force OFF (Pumpe aus)
   0 = Auto (normale Logik)
   1 = Force ON (Pumpe an)
   
Nachtabsenkung:
  --nacht-start STUNDE  = Start der Nachtabsenkung (0-23)
  --nacht-end STUNDE    = Ende der Nachtabsenkung (0-23)
  
Wenn kein Argument angegeben wird, bleibt der aktuelle Status erhalten.

Beispiele:
  ./heizung3.py                           # Status beibehalten
  ./heizung3.py --br -1                   # Brunnen OFF, Rest unverändert
  ./heizung3.py --ww 1 --hk 1             # WW und HK erzwingen AN
  ./heizung3.py --nacht-start 22 --nacht-end 5  # Nachtabsenkung 22-5 Uhr
  ./heizung3.py --nacht-start 23          # Nur Start ändern
        """
    )
    
    parser.add_argument('--ww', type=int, default=None, choices=[-1, 0, 1],
                        help='Warmwasser-Pumpe Override (default: unverändert)')
    parser.add_argument('--hk', type=int, default=None, choices=[-1, 0, 1],
                        help='Heizkreis-Pumpe Override (default: unverändert)')
    parser.add_argument('--br', type=int, default=None, choices=[-1, 0, 1],
                        help='Brunnen-Pumpe Override (default: unverändert)')
    
    parser.add_argument('--nacht-start', type=int, default=None, 
                        choices=range(0, 24), metavar='0-23',
                        help='Nachtabsenkung Start (Stunde 0-23, default: unverändert)')
    parser.add_argument('--nacht-end', type=int, default=None,
                        choices=range(0, 24), metavar='0-23',
                        help='Nachtabsenkung Ende (Stunde 0-23, default: unverändert)')
    
    return parser.parse_args()

# =============================================================================
# HAUPT-FUNKTION
# =============================================================================
def run_sync(args):
    now = datetime.now()
    mqtt_temp = get_mqtt_temperature()
    
    client = ModbusTcpClient(SPS_IP, port=502, timeout=5)
    if not client.connect():
        print("✗ Keine Verbindung zur SPS")
        sys.exit(1)
    
    try:
        # =====================================================================
        # 1. NACHTABSENKUNGSZEITEN PRÜFEN/SETZEN
        # =====================================================================
        if args.nacht_start is not None or args.nacht_end is not None:
            print("=== Nachtabsenkung ===")
            
            # Lese aktuelle Werte aus der SPS
            result = client.read_holding_registers(ADDR_SETPOINTS + SETPOINT_NACHT_START, 2, slave=0)
            if not result.isError():
                nacht_start_current = unsigned_to_signed(result.registers[0])
                nacht_end_current = unsigned_to_signed(result.registers[1])
                
                # Defaults aus SPS (23-4 Uhr) wenn noch nicht gesetzt
                if nacht_start_current < 0 or nacht_start_current > 23:
                    nacht_start_current = 23
                if nacht_end_current < 0 or nacht_end_current > 23:
                    nacht_end_current = 4
                
                # Verwende Command-line Argumente ODER behalte aktuellen Wert
                desired_start = args.nacht_start if args.nacht_start is not None else nacht_start_current
                desired_end = args.nacht_end if args.nacht_end is not None else nacht_end_current
                
                # Schreibe Start-Zeit
                if args.nacht_start is not None:
                    client.write_register(ADDR_SETPOINTS + SETPOINT_NACHT_START, desired_start, slave=0)
                    print(f"✓ Nachtabsenkung Start: {nacht_start_current:02d}:00 → {desired_start:02d}:00")
                else:
                    print(f"✓ Nachtabsenkung Start: {desired_start:02d}:00")
                
                # Schreibe End-Zeit
                if args.nacht_end is not None:
                    client.write_register(ADDR_SETPOINTS + SETPOINT_NACHT_END, desired_end, slave=0)
                    print(f"✓ Nachtabsenkung Ende:  {nacht_end_current:02d}:00 → {desired_end:02d}:00")
                else:
                    print(f"✓ Nachtabsenkung Ende:  {desired_end:02d}:00")
                
                # Zeige Zeitbereich
                if desired_start < desired_end:
                    print(f"  → Aktiv: {desired_start:02d}:00 - {desired_end:02d}:00 (tagsüber)")
                else:
                    print(f"  → Aktiv: {desired_start:02d}:00 - {desired_end:02d}:00 (über Nacht)")
        
        # =====================================================================
        # 2. PUMPEN-OVERRIDE PRÜFEN/SETZEN
        # =====================================================================
        if args.ww is not None or args.hk is not None or args.br is not None:
            print("=== Pumpen-Override ===")
            
            # Lese aktuelle Werte aus der SPS
            result = client.read_holding_registers(ADDR_SETPOINTS + SETPOINT_WW_OVERRIDE, 3, slave=0)
            if not result.isError():
                ww_current_unsigned = result.registers[0]
                hk_current_unsigned = result.registers[1]
                br_current_unsigned = result.registers[2]
                
                # Konvertiere zu signed für Vergleich
                ww_current = unsigned_to_signed(ww_current_unsigned)
                hk_current = unsigned_to_signed(hk_current_unsigned)
                br_current = unsigned_to_signed(br_current_unsigned)
                
                # Verwende Command-line Argumente ODER behalte aktuellen Wert
                desired_ww = args.ww if args.ww is not None else ww_current
                desired_hk = args.hk if args.hk is not None else hk_current
                desired_br = args.br if args.br is not None else br_current
                
                # Konvertiere signed zu unsigned für Modbus
                desired_ww_u = signed_to_unsigned(desired_ww)
                desired_hk_u = signed_to_unsigned(desired_hk)
                desired_br_u = signed_to_unsigned(desired_br)
                
                # WW Override
                if args.ww is not None and ww_current_unsigned != desired_ww_u:
                    client.write_register(ADDR_SETPOINTS + SETPOINT_WW_OVERRIDE, desired_ww_u, slave=0)
                    print(f"✓ WW Override: {get_override_mode_text(ww_current)} → {get_override_mode_text(desired_ww)}")
                elif args.ww is not None:
                    print(f"✓ WW Override: {get_override_mode_text(desired_ww)}")
                
                # HK Override
                if args.hk is not None and hk_current_unsigned != desired_hk_u:
                    client.write_register(ADDR_SETPOINTS + SETPOINT_HK_OVERRIDE, desired_hk_u, slave=0)
                    print(f"✓ HK Override: {get_override_mode_text(hk_current)} → {get_override_mode_text(desired_hk)}")
                elif args.hk is not None:
                    print(f"✓ HK Override: {get_override_mode_text(desired_hk)}")
                
                # BR Override
                if args.br is not None and br_current_unsigned != desired_br_u:
                    client.write_register(ADDR_SETPOINTS + SETPOINT_BR_OVERRIDE, desired_br_u, slave=0)
                    print(f"✓ BR Override: {get_override_mode_text(br_current)} → {get_override_mode_text(desired_br)}")
                elif args.br is not None:
                    print(f"✓ BR Override: {get_override_mode_text(desired_br)}")
        
        # =====================================================================
        # 3. UHRZEIT SENDEN
        # =====================================================================
        client.write_register(12288, now.hour, slave=0)
        
        # =====================================================================
        # 4. WARTE AUF DATA-READY
        # =====================================================================
        for retry in range(10):
            result = client.read_holding_registers(ADDR_MEASURE, 32, slave=0)
            if result.isError():
                print("✗ Modbus Fehler")
                return
            
            reg = result.registers
            status_word = reg[10]  # Register 11 = Index 10
            
            if status_word & 0x20:  # Bit 5 = Data Ready
                break
            
            print(f"⚠ Warte auf Data-Ready... ({retry+1}/10)")
            time.sleep(1.2)
        else:
            print("✗ Kein Data-Ready")
            return
        
        # =====================================================================
        # 5. MESSWERTE LESEN
        # =====================================================================
        result = client.read_holding_registers(ADDR_MEASURE, 64, slave=0)
        if result.isError():
            print("✗ Modbus Fehler beim Lesen der Messwerte")
            return
        
        reg = result.registers
        
        # Konvertiere WORD zu vorzeichenlosem INT (0-65535)
        def to_uint(val):
            return val if val >= 0 else val + 65536
        
        # Konvertiere zu signed INT (-32768 bis 32767)
        def to_int(val):
            return val if val < 32768 else val - 65536
        
        # Rohwerte (unsigned)
        raw_vl = to_uint(reg[0])   # [1]
        raw_at = to_uint(reg[1])   # [2]
        raw_it = to_uint(reg[2])   # [3]
        raw_ke = to_uint(reg[3])   # [4]
        raw_ww = to_uint(reg[4])   # [5]
        raw_ot = to_uint(reg[5])   # [6]
        raw_ru = to_uint(reg[6])   # [7]
        raw_so = to_uint(reg[7])   # [8]
        
        # DI-Karte
        di8_raw = to_uint(reg[8])  # [9]
        
        # Status
        hour_of_day = to_int(reg[9])      # [10]
        status_word = to_int(reg[10])     # [11]
        
        # Berechnete Werte
        temp_vl = calc_pt1000(raw_vl)
        temp_at = calc_pt1000(raw_at)
        temp_it = calc_pt1000(raw_it)
        temp_ke = calc_pt1000(raw_ke)
        temp_ww = calc_boiler(raw_ww)
        # RU ist ein Drucksensor (nicht PT1000), daher nur Raw-Wert verwenden
        temp_ru = None  # Drucksensor-Kalibrierung nicht bekannt
        temp_so = calc_solar(raw_so)
        temp_ot = float(raw_ot)
        
        # Weitere Werte (bereits * 100 von SPS, signed!)
        temp_diff_ww = to_int(reg[11]) / 100.0    # [12]
        temp_ke_sps = to_int(reg[12]) / 100.0     # [13]
        temp_ww_sps = to_int(reg[13]) / 100.0     # [14]
        temp_vl_sps = to_int(reg[14]) / 100.0     # [15]
        
        # Status dekodieren (SPS hat bereits invertiert!)
        status = decode_status_word(status_word)
        
        # Version (Byte-Packing!)
        version_word = to_uint(reg[15])  # [16]
        major = (version_word >> 8) & 0xFF
        minor = version_word & 0xFF
        patch = to_int(reg[16])          # [17]
        serial = to_int(reg[17])         # [18]
        
        # Runtime in SEKUNDEN (unsigned, direkter Wert!)
        runtime_ww_sec = to_uint(reg[18])  # [19]
        runtime_hk_sec = to_uint(reg[19])  # [20]
        runtime_br_sec = to_uint(reg[20])  # [21]
        
        # Cycles (unsigned)
        cycles_ww = to_uint(reg[21])  # [22]
        cycles_hk = to_uint(reg[22])  # [23]
        cycles_br = to_uint(reg[23])  # [24]
        
        # Reason Bytes (nur Low-Byte relevant) - BITMASKEN!
        reason_ww = to_uint(reg[24]) & 0xFF  # [25]
        reason_hk = to_uint(reg[25]) & 0xFF  # [26]
        reason_br = to_uint(reg[26]) & 0xFF  # [27]
        
        # WORKAROUND: BR Override-Bit aus Setpoint ableiten (bis PLC gefixt)
        # Wenn BR Override aktiv ist (br_current > 0), dann Override-Bit setzen
        if 'br_current' in locals() and br_current > 0:
            reason_br = reason_br | BR_REASON_OVERRIDE
        
        # Zusätzliche Temperaturen (signed!)
        temp_at_sps = to_int(reg[27]) / 100.0   # [28]
        temp_it_sps = to_int(reg[28]) / 100.0   # [29]
        temp_ru_sps = to_int(reg[29]) / 100.0   # [30]
        temp_so_sps = to_int(reg[30]) / 100.0   # [31]
        
        # Physical Output Byte (RAW!)
        qb0 = to_uint(reg[31]) & 0xFF  # [32]
        
        # Dekodiere Physical Outputs (mit Invertierung!)
        physical_status = decode_physical_outputs(qb0)
        
        # =====================================================================
        # 6. SYSTEM-DIAGNOSE LESEN
        # =====================================================================
        sys_result = client.read_holding_registers(ADDR_SYSTEM, 8, slave=0)
        if not sys_result.isError():
            sys_reg = sys_result.registers
            # Uptime ist 32-bit: Low-Word + High-Word
            uptime_low = sys_reg[0]
            uptime_high = sys_reg[1]
            uptime_sec = (uptime_high << 16) | uptime_low
            
            error_count = sys_reg[2]
            cpu_load = sys_reg[3]
            cycle_min = sys_reg[4]
            cycle_max = sys_reg[5]
            cycle_avg = sys_reg[6]
        else:
            uptime_sec = 0
            error_count = 0
            cpu_load = 0
        
        # =====================================================================
        # 7. NACHTABSENKUNGSZEITEN AUSLESEN (FÜR ANZEIGE)
        # =====================================================================
        nacht_result = client.read_holding_registers(ADDR_SETPOINTS + SETPOINT_NACHT_START, 2, slave=0)
        if not nacht_result.isError():
            nacht_start_display = unsigned_to_signed(nacht_result.registers[0])
            nacht_end_display = unsigned_to_signed(nacht_result.registers[1])
            
            # Validierung & Defaults
            if nacht_start_display < 0 or nacht_start_display > 23:
                nacht_start_display = 23
            if nacht_end_display < 0 or nacht_end_display > 23:
                nacht_end_display = 4
        else:
            nacht_start_display = 23
            nacht_end_display = 4
        
        # =====================================================================
        # 8. R290 WASSERTANK-TEMPERATUR LESEN
        # =====================================================================
        tank_result = client.read_holding_registers(12396, 1, slave=0)
        if not tank_result.isError():
            tank_raw = unsigned_to_signed(tank_result.registers[0])
            temp_tank = tank_raw / 100.0 if tank_raw != 0 else None
        else:
            temp_tank = None
        
        # =====================================================================
        # 9. AUSGABE MIT KONSISTENTER FORMATIERUNG
        # =====================================================================
        # Formatiere Temperaturen für Anzeige
        tank_display = f"{temp_tank:6.2f}°C" if temp_tank is not None else "  ---   "
        mqtt_display = f"{mqtt_temp:6.2f}°C" if mqtt_temp is not None else "  ---   "
        
        print("=" * 80)
        print(f"HEIZUNGSSTEUERUNG v{VERSION} | {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"SPS: v{major}.{minor}.{patch} | Serial: {serial}")
        print("=" * 80)
        print(f"PHASE: {status['phase']} | DATA: {'READY' if status['data_ready'] else 'WAIT'}")
        print(f"NACHT: {'AKTIV' if status['nacht'] else 'INAKTIV'} | Zeit: {nacht_start_display:02d}:00-{nacht_end_display:02d}:00")
        print("-" * 80)
        
        print("TEMPERATUREN:")
        print(f"  VL: {temp_vl:6.2f}°C | AT: {temp_at:6.2f}°C | IT: {temp_it:6.2f}°C")
        print(f"  KE: {temp_ke:6.2f}°C | WW: {temp_ww:6.2f}°C | RU: raw={raw_ru}")
        print(f"  SO: {temp_so:6.2f}°C | OT: {temp_ot:.2f}")
        print(f"  ΔT(Kessel-WW): {temp_diff_ww:.2f}°C")
        print(f"  R290-Tank: {tank_display}")
        print(f"  Node3(KY9A): {mqtt_display}")
        
        print("-" * 80)
        print("PUMPEN:")
        print(f"  WW: {'AN ' if status['ww_pumpe'] else 'AUS'} | Reason: {decode_ww_reason(reason_ww)}")
        print(f"  HK: {'AN ' if status['hk_pumpe'] else 'AUS'} | Reason: {decode_hk_reason(reason_hk)}")
        print(f"  BR: {'AN ' if status['brunnen'] else 'AUS'} | Reason: {decode_br_reason(reason_br)}")
        
        print("-" * 80)
        print("RUNTIME:")
        print(f"  WW: {format_runtime(runtime_ww_sec)} ({cycles_ww} Starts)")
        print(f"  HK: {format_runtime(runtime_hk_sec)} ({cycles_hk} Starts)")
        print(f"  BR: {format_runtime(runtime_br_sec)} ({cycles_br} Starts)")
        
        print("-" * 80)
        print(f"SYSTEM: Uptime {format_uptime(uptime_sec)} | Fehler: {error_count} | CPU: {cpu_load}%")
        print("=" * 80)
        
        # =====================================================================
        # 10. DATENBANK
        # =====================================================================
        # Berechne Stunden für DB (FLOAT!)
        runtime_ww_h = runtime_ww_sec / 3600.0
        runtime_hk_h = runtime_hk_sec / 3600.0
        runtime_br_h = runtime_br_sec / 3600.0
        
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cur:
                sql = """INSERT INTO heizung 
                         (version, zeitstempel, sensor_gruppe, stunde, 
                          zaehler_kwh, zaehler_pumpe, zaehler_brunnen,
                          raw_vorlauf, raw_aussen, raw_innen, raw_kessel, 
                          temp_vorlauf, temp_aussen, temp_innen, temp_kessel, 
                          raw_warmwasser, temp_warmwasser, wert_oeltank, 
                          raw_ruecklauf, temp_ruecklauf, raw_solar, temp_solar, 
                          ky9a, status_word, di8_raw,
                          reason_ww, reason_hk, reason_br,
                          runtime_ww_h, runtime_hk_h, runtime_br_h,
                          cycles_ww, cycles_hk, cycles_br, temp_wassertank)
                         VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, 
                                 %s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s,%s,
                                 %s,%s,%s, %s,%s,%s, %s)"""
                
                # Version als String speichern um Patch nicht zu verlieren
                sps_ver = f"{major}.{minor}.{patch}" if patch > 0 else f"{major}.{minor}"
                
                cur.execute(sql, (
                    sps_ver, now, status['phase'], now.hour,
                    0, 0, 0,  # Deprecated counters
                    raw_vl, raw_at, raw_it, raw_ke,
                    temp_vl, temp_at, temp_it, temp_ke,
                    raw_ww, temp_ww, temp_ot,
                    raw_ru, temp_ru, raw_so, temp_so,
                    mqtt_temp, status_word, di8_raw,  # status_word ist bereits korrekt von SPS!
                    reason_ww, reason_hk, reason_br,  # Raw Byte-Werte für spätere Analyse
                    runtime_ww_h, runtime_hk_h, runtime_br_h,
                    cycles_ww, cycles_hk, cycles_br,
                    temp_tank  # R290 Wassertank-Temperatur
                ))
            
            conn.commit()
            print("✓ Daten gespeichert")
        finally:
            conn.close()
    
    except Exception as e:
        print(f"✗ Fehler: {e}")
        import traceback
        traceback.print_exc()
    finally:
        client.close()

if __name__ == "__main__":
    args = parse_arguments()
    run_sync(args)

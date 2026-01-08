#!/usr/bin/python3
# heizung2.py v4.3.2 - MIT INVERTIERTEN RELAIS & WASSERTANK & RU=DRUCKSENSOR
import pymysql, sys, pandas as pd
from datetime import datetime
from pymodbus.client import ModbusTcpClient
import paho.mqtt.client as mqtt
import time
from sqlalchemy import create_engine, text

# =============================================================================
# WAGO 750-881 MODBUS REGISTER MAP
# =============================================================================
# WICHTIG: Modbus-Adresse = CODESYS MW-Nummer + 12288
#
# INPUTS (von SPS gelesen):
# MW0    = 12288  = Stunden-Setpoint (von Python geschrieben)
# MW32   = 12320  = xMeasure[1]  - Sensor Vorlauf (Raw)
# MW33   = 12321  = xMeasure[2]  - Sensor Außen (Raw)
# ...
# MW95   = 12383  = xMeasure[64] - Letztes Measure-Register
#
# SETPOINTS (von Python geschrieben, von SPS gelesen):
# MW96   = 12384  = xSetpoints[1]
# MW107  = 12395  = xSetpoints[12]
# MW108  = 12396  = xSetpoints[13] - R290 Wassertank-Temperatur (*100)
# MW109  = 12397  = xSetpoints[14] - WW Pumpen-Override (-1/0/1)
# MW110  = 12398  = xSetpoints[15] - HK Pumpen-Override (-1/0/1)
# MW111  = 12399  = xSetpoints[16] - BR Pumpen-Override (-1/0/1)
#
# PHYSICAL OUTPUTS (von SPS gelesen):
# %QB0   = 512    = Physical Output Byte (Relais-Status)
#
# SYSTEM (von SPS gelesen):
# MW128  = 12416  = xSystem[1] - Uptime Low-Word
# MW129  = 12417  = xSystem[2] - Uptime High-Word
# ...
# =============================================================================

SPS_IP = '192.168.178.2'
MQTT_HOST = '192.168.178.218'
DB_URL = 'mysql+pymysql://gh:a12345@10.8.0.1/wagodb'
mqtt_val, mqtt_rx = None, False

def on_msg(c, u, m):
    global mqtt_val, mqtt_rx
    try: mqtt_val, mqtt_rx = float(m.payload.decode()), True
    except: pass

def get_mqtt():
    """Liest Raumtemperatur von MQTT Node3/pin4 (KY-9A Sensor)"""
    global mqtt_val, mqtt_rx
    mqtt_val, mqtt_rx = None, False
    try:
        c = mqtt.Client(); c.on_message = on_msg; c.connect(MQTT_HOST, 1883, 60)
        c.subscribe('Node3/pin4'); st = time.time(); c.loop_start()
        while not mqtt_rx and time.time()-st < 3: time.sleep(0.1)  # Timeout auf 3s reduziert
        c.loop_stop(); c.disconnect()
        return mqtt_val if mqtt_rx else None
    except Exception as e:
        return None  # Fehler stillschweigend ignorieren

calc_pt = lambda r: round((r-7134)/25, 2) if 4000<r<25000 else 0.0
calc_bo = lambda r: round((40536-r)/303.1, 2) if 4000<r<45000 else 0.0
calc_so = lambda r: round((r-26402)/60, 2) if 4000<r<40000 else 0.0
to_u = lambda v: v if v>=0 else v+65536
to_s = lambda v: v if v<32768 else v-65536

def dec_rea(r, t):
    if r==0: return "AUS"
    if t=='WW': return "ΔT≥2°C" if r&1 else ""
    if t=='HK': return "+".join([x for b,x in [(1,"Frost"),(2,"Wärme"),(4,"Ovr")] if r&b])
    return "HK aktiv" if r&1 else ""

def fmt_rt(sec):
    """Formatiert Runtime aus Sekunden in Tage, Stunden, Minuten"""
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    return f"{d}d {h:02d}h {m:02d}m"

def ensure_schema(eng):
    with eng.begin() as c:
        ex = c.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='wagodb' AND table_name='schema_version'")).scalar()
        if not ex:
            c.execute(text("CREATE TABLE schema_version(id INT AUTO_INCREMENT PRIMARY KEY, version INT, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP, description VARCHAR(255))"))
            cv = 0
        else:
            cv = c.execute(text("SELECT COALESCE(MAX(version),0) FROM schema_version")).scalar()

        # Schema-Version 8: VARCHAR version
        if cv < 8:
            tbl_ex = c.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='wagodb' AND table_name='heizung'")).scalar()

            if tbl_ex:
                c.execute(text("ALTER TABLE heizung MODIFY COLUMN version VARCHAR(20) DEFAULT '1.2.0'"))
                c.execute(text("INSERT INTO schema_version(version,description) VALUES(8,'VARCHAR version column')"))
            else:
                c.execute(text("""CREATE TABLE heizung(
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    version VARCHAR(20) DEFAULT '1.2.0',
                    sensor_gruppe CHAR(1) DEFAULT 'B',
                    zeitstempel DATETIME NOT NULL,
                    stunde TINYINT,
                    zaehler_kwh INT UNSIGNED DEFAULT 0,
                    zaehler_pumpe INT UNSIGNED DEFAULT 0,
                    zaehler_brunnen INT UNSIGNED DEFAULT 0,
                    raw_vorlauf SMALLINT UNSIGNED,
                    raw_aussen SMALLINT UNSIGNED,
                    raw_innen SMALLINT UNSIGNED,
                    raw_kessel SMALLINT UNSIGNED,
                    temp_vorlauf DECIMAL(5,2),
                    temp_aussen DECIMAL(5,2),
                    temp_innen DECIMAL(5,2),
                    temp_kessel DECIMAL(5,2),
                    raw_warmwasser SMALLINT UNSIGNED,
                    temp_warmwasser DECIMAL(5,2),
                    wert_oeltank DECIMAL(7,2),
                    raw_ruecklauf SMALLINT UNSIGNED,
                    temp_ruecklauf DECIMAL(5,2) DEFAULT NULL COMMENT 'Drucksensor (nicht PT1000)',
                    raw_solar SMALLINT UNSIGNED,
                    temp_solar DECIMAL(5,2),
                    ky9a DECIMAL(5,2),
                    status_word SMALLINT UNSIGNED,
                    di8_raw SMALLINT UNSIGNED,
                    reason_ww TINYINT UNSIGNED DEFAULT 0,
                    reason_hk TINYINT UNSIGNED DEFAULT 0,
                    reason_br TINYINT UNSIGNED DEFAULT 0,
                    runtime_ww_h DECIMAL(6,2) DEFAULT 0,
                    runtime_hk_h DECIMAL(6,2) DEFAULT 0,
                    runtime_br_h DECIMAL(6,2) DEFAULT 0,
                    cycles_ww INT UNSIGNED DEFAULT 0,
                    cycles_hk INT UNSIGNED DEFAULT 0,
                    cycles_br INT UNSIGNED DEFAULT 0,
                    INDEX(zeitstempel), INDEX(stunde))"""))
                c.execute(text("INSERT INTO schema_version(version,description) VALUES(8,'INT v4.2 with VARCHAR version')"))

        # Schema-Version 9: Wassertank-Temperatur Spalte
        if cv < 9:
            tbl_ex = c.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='wagodb' AND table_name='heizung'")).scalar()
            if tbl_ex:
                # Prüfe ob Spalte bereits existiert
                col_ex = c.execute(text("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='wagodb' AND table_name='heizung' AND column_name='temp_wassertank'")).scalar()
                if not col_ex:
                    c.execute(text("ALTER TABLE heizung ADD COLUMN temp_wassertank DECIMAL(5,2) DEFAULT NULL COMMENT 'R290 Wassertank-Temperatur'"))
                    c.execute(text("INSERT INTO schema_version(version,description) VALUES(9,'Added R290 tank temperature column')"))

        # Schema-Version 10: temp_ruecklauf NULL-fähig machen (RU ist Drucksensor, nicht PT1000)
        if cv < 10:
            tbl_ex = c.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='wagodb' AND table_name='heizung'")).scalar()
            if tbl_ex:
                c.execute(text("ALTER TABLE heizung MODIFY COLUMN temp_ruecklauf DECIMAL(5,2) DEFAULT NULL COMMENT 'Drucksensor (nicht PT1000)'"))
                c.execute(text("INSERT INTO schema_version(version,description) VALUES(10,'temp_ruecklauf nullable for pressure sensor')"))

now = datetime.now(); mqtt_t = get_mqtt(); eng = create_engine(DB_URL, pool_pre_ping=True)
ensure_schema(eng); cl = ModbusTcpClient(SPS_IP, 502, timeout=5)
if not cl.connect(): print("✗ SPS"); sys.exit(1)

try:
    # Stunden-Setpoint schreiben (MW0 = 12288)
    cl.write_register(12288, now.hour, 0)

    # Warte auf Data-Ready und lese Messwerte (MW32-MW95 = 12320-12383)
    for _ in range(10):
        r = cl.read_holding_registers(12320, 64, 0)  # xMeasure[1..64]
        if not r.isError() and r.registers[10]&32: break
        time.sleep(1.2)
    else: print("✗ No ready"); sys.exit(1)

    reg = r.registers
    rv,ra,ri,rk = to_u(reg[0]),to_u(reg[1]),to_u(reg[2]),to_u(reg[3])
    rw,ro,ru,rs = to_u(reg[4]),to_u(reg[5]),to_u(reg[6]),to_u(reg[7])
    sw,hr = to_s(reg[10]),to_s(reg[9])
    di8 = to_u(reg[8])

    # RU ist ein Drucksensor (nicht PT1000), daher nur Raw-Wert speichern
    temp_ruecklauf = None  # Drucksensor-Kalibrierung nicht bekannt

    # Version dekodieren
    ver_word = to_u(reg[15])
    ver_major = (ver_word >> 8) & 0xFF
    ver_minor = ver_word & 0xFF
    ver_patch = to_s(reg[16])
    sps_version = f"{ver_major}.{ver_minor}.{ver_patch}"

    # Runtime in SEKUNDEN
    rtw_sec, rth_sec, rtb_sec = to_u(reg[18]), to_u(reg[19]), to_u(reg[20])
    rtw_h, rth_h, rtb_h = rtw_sec / 3600.0, rth_sec / 3600.0, rtb_sec / 3600.0

    cyw,cyh,cyb = to_u(reg[21]), to_u(reg[22]), to_u(reg[23])
    rww,rhk,rbr = to_u(reg[24])&255, to_u(reg[25])&255, to_u(reg[26])&255

    # Physical Output Byte (%QB0 = 512) - MIT INVERTIERTER LOGIK!
    qb = cl.read_holding_registers(512,1,0)
    qb0 = qb.registers[0]&255 if not qb.isError() else 0

    # Dekodiere Pumpen mit invertierter Relais-Logik
    # Bit 1 (0x02): O1_WWPump     - FALSE = AN (invertiert!)
    # Bit 2 (0x04): O2_UmwaelzHK1 - FALSE = AN (invertiert!)
    # Bit 3 (0x08): O3_Brunnen    - TRUE  = AN (normal)
    ww_pump = not bool(qb0 & 0x02)   # Invertiert!
    hk_pump = not bool(qb0 & 0x04)   # Invertiert!
    br_pump = bool(qb0 & 0x08)       # Normal

    # R290 Wassertank-Temperatur lesen (xSetpoints[13] = MW108 = 12396)
    # Dieses Register wird von r290mb.py geschrieben (Wert * 100)
    tank_result = cl.read_holding_registers(12396, 1, 0)
    tank_raw = None
    temp_tank = None

    if not tank_result.isError():
        tank_raw = to_s(tank_result.registers[0])
        # Nur wenn Wert != 0, als gültig betrachten
        if tank_raw != 0:
            temp_tank = tank_raw / 100.0
        else:
            temp_tank = None

    ph = 'A' if sw&16 else 'B'
    data = {'version': sps_version, 'sensor_gruppe':ph, 'zeitstempel':now, 'stunde':now.hour,
            'zaehler_kwh':0, 'zaehler_pumpe':0, 'zaehler_brunnen':0,
            'raw_vorlauf':rv, 'raw_aussen':ra, 'raw_innen':ri, 'raw_kessel':rk,
            'temp_vorlauf':calc_pt(rv), 'temp_aussen':calc_pt(ra), 'temp_innen':calc_pt(ri), 'temp_kessel':calc_pt(rk),
            'raw_warmwasser':rw, 'temp_warmwasser':calc_bo(rw), 'wert_oeltank':float(ro),
            'raw_ruecklauf':ru, 'temp_ruecklauf':temp_ruecklauf, 'raw_solar':rs, 'temp_solar':calc_so(rs),
            'ky9a':mqtt_t, 'status_word':sw, 'di8_raw':di8,
            'reason_ww':rww, 'reason_hk':rhk, 'reason_br':rbr,
            'runtime_ww_h':rtw_h, 'runtime_hk_h':rth_h, 'runtime_br_h':rtb_h,
            'cycles_ww':cyw, 'cycles_hk':cyh, 'cycles_br':cyb,
            'temp_wassertank': temp_tank}

    # Formatiere Temperaturen für Ausgabe
    tank_display = f"{temp_tank:5.1f}°C" if temp_tank is not None else "  ---  "
    mqtt_display = f"{mqtt_t:5.1f}°C" if mqtt_t is not None else "  ---  "

    print("="*80)
    print(f"HEIZUNG v4.3 | {now:%Y-%m-%d %H:%M:%S} | SPS v{sps_version} | Phase {ph}")
    print(f"VL:{data['temp_vorlauf']:5.1f}°C AT:{data['temp_aussen']:5.1f}°C IT:{data['temp_innen']:5.1f}°C KE:{data['temp_kessel']:5.1f}°C")
    print(f"WW:{data['temp_warmwasser']:5.1f}°C RU:raw={ru} SO:{data['temp_solar']:5.1f}°C Tank:{tank_display}")
    print(f"Node3(KY9A):{mqtt_display}")
    print(f"Pumpen: WW={'AN' if ww_pump else 'AUS'} HK={'AN' if hk_pump else 'AUS'} BR={'AN' if br_pump else 'AUS'}")
    print(f"Runtime: WW={fmt_rt(rtw_sec)}({cyw}×) HK={fmt_rt(rth_sec)}({cyh}×) BR={fmt_rt(rtb_sec)}({cyb}×)")
    print(f"Reason: WW={dec_rea(rww,'WW')} HK={dec_rea(rhk,'HK')} BR={dec_rea(rbr,'BR')}")
    print("="*80)

    pd.DataFrame([data]).to_sql('heizung', eng, if_exists='append', index=False)
    print("✓ Gespeichert")
except Exception as e:
    print(f"✗ {e}")
    import traceback; traceback.print_exc()
finally:
    cl.close()

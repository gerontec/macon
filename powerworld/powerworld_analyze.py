#!/usr/bin/python3
"""
powerworld_analyze.py — Analyse der R290 Wärmepumpen-Betriebsdaten

Wertet heatpr290 (MariaDB) aus, gefiltert auf Kompressor=EIN.
Zeigt Min/Max/Avg/Perzentile für alle relevanten Betriebsgrößen,
Frequenzverteilung, Überhitzungsstatistik und Anomaliehinweise.

Verwendung:
    python3 powerworld_analyze.py              # Gesamt-Analyse
    python3 powerworld_analyze.py --days 1     # Nur letzter Tag
    python3 powerworld_analyze.py --days 7     # Letzte 7 Tage
"""

import argparse
import sys

import pymysql
import pandas as pd
import numpy as np

# --- DB ---
DB_HOST  = '192.168.178.218'
DB_USER  = 'gh'
DB_PASS  = 'a12345'
DB_NAME  = 'wagodb'
DB_TABLE = 'heatpr290'

# Spalten die als TEXT gespeichert sind → CAST nötig
TEXT_COLS = [
    'compressor_actual_frequency',
    'exhaust_gas_temperature',
    'compressor_current',
    'compressor_operating_power',
    'suction_gas_temperature',
    'outlet_water_temperature',
    'inlet_water_temperature',
    'water_tank_temperature',
    'ambient_temperature',
    'external_coil_temperature',
    'inner_coil_temperature',
    'low_pressure_value',
    'dc_water_pump_speed',
    'low_pressure_conversion_temperature',
    'mode',
]

# Spalten die direkt als FLOAT vorliegen
FLOAT_COLS = [
    'expansion_valve_opening',
    't_sat',
    'superheat',
    'pv_surplus_w',
    'vl_soll',
]

# Sinnvolle Anzeigereihenfolge mit Einheiten und Alarmgrenzen
#  (spalte, Anzeigename, Einheit, warn_min, warn_max)
METRICS = [
    ('compressor_actual_frequency',  'Verdichter Hz',        'Hz',  30,   65),
    ('exhaust_gas_temperature',      'Abgastemperatur',      '°C',  30,   80),
    ('compressor_current',           'Strom',                'A',    3,   20),
    ('compressor_operating_power',   'Leistung',             'W',  500, 5000),
    ('suction_gas_temperature',      'Sauggas',              '°C', -15,   20),
    ('low_pressure_value',           'Niederdruck',          'bar', 1.0,  5.0),
    ('t_sat',                        'T_sat R290',           '°C', -20,   10),
    ('superheat',                    'Überhitzung',          'K',    4,   20),
    ('expansion_valve_opening',      'EEV Öffnung',          'P',   80,  400),
    ('outlet_water_temperature',     'Vorlauf',              '°C',  20,   50),
    ('inlet_water_temperature',      'Rücklauf',             '°C',  15,   45),
    ('water_tank_temperature',       'Speicher',             '°C',  20,   55),
    ('ambient_temperature',          'Außentemp',            '°C', -20,   35),
    ('external_coil_temperature',    'Außenwärmetauscher',   '°C', -25,   20),
    ('dc_water_pump_speed',          'Pumpengeschw.',        'rpm',  0, 3000),
    ('vl_soll',                      'VL-Soll (Heizkurve)',  '°C',  20,   44),
]


# Physikalisch plausible Wertebereiche — außerhalb = Sensor-Müll / 0xFFFF
PLAUSIBLE = {
    'compressor_actual_frequency':       (25,   90),
    'exhaust_gas_temperature':           (20,  110),
    'compressor_current':                (1,    30),
    'compressor_operating_power':        (200, 8000),
    'suction_gas_temperature':           (-20,  30),
    'low_pressure_value':                (0.5,   8),
    't_sat':                             (-30,  20),
    'superheat':                         (-5,   35),
    'expansion_valve_opening':           (50,  400),
    'outlet_water_temperature':          (15,   60),
    'inlet_water_temperature':           (10,   55),
    'water_tank_temperature':            (15,   60),
    'ambient_temperature':               (-25,  45),
    'dc_water_pump_speed':               (0,  3000),
    'vl_soll':                           (15,   50),
}


def load_data(days=None):
    where = "WHERE compressor = 1"
    if days:
        where += f" AND ts >= NOW() - INTERVAL {days} DAY"

    cast_exprs = ", ".join(
        f"CAST(`{c}` AS DECIMAL(10,3)) AS `{c}`" for c in TEXT_COLS
    )
    float_exprs = ", ".join(f"`{c}`" for c in FLOAT_COLS)

    sql = (
        f"SELECT ts, compressor, heating_active, hot_water_active, defrosting_active, "
        f"{cast_exprs}, {float_exprs} "
        f"FROM `{DB_TABLE}` {where} ORDER BY ts"
    )

    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    try:
        df = pd.read_sql(sql, conn)
    finally:
        conn.close()

    df['ts'] = pd.to_datetime(df['ts'])

    # Physikalisch unplausible Werte auf NaN setzen (0xFFFF-Reste, Anlauf-Transienten)
    for col, (lo, hi) in PLAUSIBLE.items():
        if col in df.columns:
            mask = (df[col] < lo) | (df[col] > hi)
            df.loc[mask, col] = np.nan

    return df


def fmt_val(v, unit=''):
    if pd.isna(v):
        return '   —   '
    if unit in ('Hz', 'A', 'W', 'rpm', 'P'):
        return f"{v:7.1f} {unit}"
    return f"{v:7.2f} {unit}"


def print_metrics(df):
    print(f"\n{'Messgröße':<26} {'Min':>12} {'p5':>12} {'Avg':>12} {'p95':>12} {'Max':>12}  Warn")
    print("─" * 92)
    for col, name, unit, wmin, wmax in METRICS:
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if len(s) == 0:
            continue
        mn   = s.min()
        p5   = s.quantile(0.05)
        avg  = s.mean()
        p95  = s.quantile(0.95)
        mx   = s.max()
        warn = []
        if mn < wmin:
            warn.append(f"min<{wmin}")
        if mx > wmax:
            warn.append(f"max>{wmax}")
        warn_str = ", ".join(warn) if warn else "OK"
        print(f"  {name:<24} {fmt_val(mn,unit):>13} {fmt_val(p5,unit):>13} "
              f"{fmt_val(avg,unit):>13} {fmt_val(p95,unit):>13} {fmt_val(mx,unit):>13}  {warn_str}")


def print_freq_distribution(df):
    col = 'compressor_actual_frequency'
    if col not in df.columns:
        return
    s = df[col].dropna()
    s = s[s > 0]
    if len(s) == 0:
        return
    bins = [0, 35, 40, 45, 50, 55, 60, 65, 70, 999]
    labels = ['≤35', '36–40', '41–45', '46–50', '51–55', '56–60', '61–65', '66–70', '>70']
    cats = pd.cut(s, bins=bins, labels=labels)
    counts = cats.value_counts().sort_index()
    total = len(s)
    print()
    print("  Hz-Bereich   Minuten   Anteil  Balken")
    print("  " + "─" * 55)
    for label, cnt in counts.items():
        pct = cnt / total * 100
        bar = '█' * int(pct / 2)
        marker = " ← Deckel" if label in ('56–60', '61–65') else ""
        print(f"  {label:<10}  {cnt:6}    {pct:5.1f}%  {bar}{marker}")
    print(f"  {'Gesamt':<10}  {total:6}    100.0%")


def print_mode_breakdown(df):
    if 'mode' not in df.columns:
        return
    mode_names = {0: 'Warmwasser', 1: 'Heizung', 2: 'Kühlung',
                  3: 'WW+Heizung', 4: 'WW+Kühlung'}
    counts = df['mode'].dropna().astype(int).value_counts().sort_index()
    print()
    for m, cnt in counts.items():
        print(f"  Modus {m} ({mode_names.get(m,'?'):<15}): {cnt:5} Minuten")


def print_eev_superheat_correlation(df):
    eev = 'expansion_valve_opening'
    sh  = 'superheat'
    hz  = 'compressor_actual_frequency'
    if not all(c in df.columns for c in [eev, sh, hz]):
        return
    sub = df[[eev, sh, hz]].dropna()
    sub = sub[(sub[hz] > 0)]
    if len(sub) < 20:
        return
    corr = sub[eev].corr(sub[sh])
    print(f"\n  EEV ↔ Überhitzung Korrelation (Pearson r): {corr:+.3f}", end="")
    if abs(corr) < 0.2:
        print("  → kaum Einfluss (EEV nicht begrenzend oder Überfüllung?)")
    elif corr > 0.4:
        print("  → EEV steuert Überhitzung normal")
    else:
        print()

    # EEV-Gruppen vs. Überhitzung
    bins   = [0, 80, 120, 160, 200, 999]
    labels = ['≤80P', '81–120P', '121–160P', '161–200P', '>200P']
    sub['eev_bin'] = pd.cut(sub[eev], bins=bins, labels=labels)
    grp = sub.groupby('eev_bin', observed=True)[sh].agg(['mean', 'count'])
    print()
    print(f"  {'EEV-Bereich':<12}  {'Ø Überhitzung':>14}  {'n':>6}")
    print("  " + "─" * 36)
    for idx, row in grp.iterrows():
        if row['count'] > 0:
            print(f"  {idx:<12}  {row['mean']:>10.2f} K     {int(row['count']):>6}")


def print_anomalies(df):
    issues = []

    sh = df['superheat'].dropna() if 'superheat' in df.columns else pd.Series([], dtype=float)
    if len(sh) > 0:
        low_sh  = (sh < 3).sum()
        high_sh = (sh > 20).sum()
        if low_sh > 0:
            issues.append(f"Überhitzung < 3K: {low_sh}× (Flüssigschlag-Risiko!)")
        if high_sh > 0:
            issues.append(f"Überhitzung > 20K: {high_sh}× (EEV zu weit geschlossen?)")

    ex = df['exhaust_gas_temperature'].dropna() if 'exhaust_gas_temperature' in df.columns else pd.Series([], dtype=float)
    if len(ex) > 0:
        hot = (ex > 80).sum()
        very_hot = (ex > 90).sum()
        if very_hot > 0:
            issues.append(f"Abgastemperatur > 90°C: {very_hot}× (kritisch!)")
        elif hot > 0:
            issues.append(f"Abgastemperatur > 80°C: {hot}× (Warnung)")

    amp = df['compressor_current'].dropna() if 'compressor_current' in df.columns else pd.Series([], dtype=float)
    if len(amp) > 0:
        over = (amp > 20).sum()
        if over > 0:
            issues.append(f"Strom > 20A: {over}× (Überlast)")

    hz = df['compressor_actual_frequency'].dropna() if 'compressor_actual_frequency' in df.columns else pd.Series([], dtype=float)
    if len(hz) > 0:
        over60 = (hz > 60).sum()
        if over60 > 0:
            issues.append(f"Hz > 60 (über Deckel): {over60}× — ältere Daten vor Limitierung")

    if issues:
        for iss in issues:
            print(f"  ⚠  {iss}")
    else:
        print("  Keine Anomalien im Datensatz.")


def main():
    parser = argparse.ArgumentParser(description="R290 Betriebsdaten-Analyse")
    parser.add_argument('--days', type=int, default=None,
                        help='Nur die letzten N Tage auswerten (Standard: alle Daten)')
    args = parser.parse_args()

    print("Lade Daten …", end=' ', flush=True)
    try:
        df = load_data(days=args.days)
    except Exception as e:
        print(f"\nFEHLER beim Laden: {e}", file=sys.stderr)
        sys.exit(1)

    if df.empty:
        print("Keine Daten gefunden (Kompressor=EIN Bedingung nicht erfüllt).")
        sys.exit(0)

    t_from = df['ts'].min().strftime('%Y-%m-%d %H:%M')
    t_to   = df['ts'].max().strftime('%Y-%m-%d %H:%M')
    period = f"letzten {args.days} Tage" if args.days else "alle Daten"
    print(f"OK ({len(df)} Datenpunkte, {period})\n")

    print("=" * 92)
    print(f"  R290 Wärmepumpe — Betriebsanalyse  (Kompressor EIN)")
    print(f"  Zeitraum: {t_from} → {t_to}")
    print("=" * 92)

    # Betriebsmodi
    print("\n▶ Betriebsmodi (Minuten je Modus):")
    print_mode_breakdown(df)

    # Hauptstatistik
    print("\n▶ Betriebsgrößen Min/Avg/Max (nur Kompressor EIN):")
    print_metrics(df)

    # Frequenzverteilung
    print("\n▶ Verdichter-Frequenzverteilung:")
    print_freq_distribution(df)

    # EEV ↔ Überhitzung
    print("\n▶ EEV ↔ Überhitzungs-Analyse:")
    print_eev_superheat_correlation(df)

    # Anomalien
    print("\n▶ Anomalien & Warnungen:")
    print_anomalies(df)

    print("\n" + "=" * 92)


if __name__ == '__main__':
    main()

#!/usr/bin/python3
# cop_report.py — COP-Stundenbericht: sdm72d (el. Energie) JOIN mbus2 (thermische Energie)
# COP = thermische Energie (kWh) / elektrische Energie (kWh)  — Delta Zählerstand pro Stunde
# Thermisch: mbus2.Energy  (Wh, kumulativ) → Δ/1000 = th_kWh
# Elektrisch: sdm72d.total_import_active_energy (kWh, kumulativ) → Δ = el_kWh

import pymysql
import sys
import argparse
from datetime import datetime, timedelta

# ── Konfiguration ────────────────────────────────────────────────────────────
DB = dict(
    host="192.168.178.218",
    user="gh",
    password="a12345",
    database="wagodb",
    cursorclass=pymysql.cursors.DictCursor,
)

# ── Argumente ────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="COP-Stundenbericht Wärmepumpe")
parser.add_argument("--days", type=int, default=1,
                    help="Anzahl Tage zurück (default: 1 = gestern + heute)")
parser.add_argument("--date", type=str, default=None,
                    help="Bestimmtes Datum YYYY-MM-DD (überschreibt --days)")
parser.add_argument("--from", dest="from_dt", type=str, default=None,
                    help="Startzeit YYYY-MM-DD HH:MM (überschreibt --days/--date)")
args = parser.parse_args()

# ── Zeitraum bestimmen ───────────────────────────────────────────────────────
if args.from_dt:
    try:
        start_dt = datetime.strptime(args.from_dt, "%Y-%m-%d %H:%M")
    except ValueError:
        print(f"Ungültiges Datum: {args.from_dt}  (Format: YYYY-MM-DD HH:MM)")
        sys.exit(1)
    end_dt = datetime.now()
elif args.date:
    try:
        start_dt = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"Ungültiges Datum: {args.date}  (Format: YYYY-MM-DD)")
        sys.exit(1)
    end_dt = start_dt + timedelta(days=1)
else:
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=args.days)

start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
end_str   = end_dt.strftime("%Y-%m-%d %H:%M:%S")

# ── SQL ──────────────────────────────────────────────────────────────────────
QUERY = """
SELECT
    s.hour                                                           AS stunde,
    ROUND(MAX(s.total_import_active_energy)
        - MIN(s.total_import_active_energy), 3)                      AS el_kwh,
    ROUND((MAX(m.Energy) - MIN(m.Energy)) / 1000.0, 3)              AS th_kwh,
    ROUND(
        ((MAX(m.Energy) - MIN(m.Energy)) / 1000.0)
        / NULLIF(MAX(s.total_import_active_energy)
               - MIN(s.total_import_active_energy), 0)
    , 2)                                                             AS cop,
    ROUND(AVG(m.Flowtemperature), 1)                                 AS vorlauf_c,
    ROUND(AVG(m.Returntemperature), 1)                               AS ruecklauf_c,
    ROUND(AVG(m.TemperatureDifference), 1)                           AS delta_t,
    ROUND(AVG(m.Volumeflow), 3)                                      AS volumeflow_m3h,
    COUNT(s.id)                                                      AS messpunkte,
    p.outdoor_temp_c                                                 AS aussen_c,
    p.wago_vl_soll_c                                                 AS wago_vl_soll_c
FROM sdm72d s
JOIN mbus2 m ON m.dth = s.hour
LEFT JOIN (
    SELECT DATE_FORMAT(timestamp, '%%Y-%%m-%%d-%%H') AS hour,
           ROUND(AVG(outdoor_temp_c), 1)          AS outdoor_temp_c,
           ROUND(AVG(wago_vl_soll_c), 1)          AS wago_vl_soll_c
    FROM macon_pivot
    WHERE timestamp BETWEEN %s AND %s
      AND outdoor_temp_c IS NOT NULL
    GROUP BY 1
) p ON p.hour = s.hour
WHERE s.timestamp BETWEEN %s AND %s
  AND s.active_power_l3 > 50        -- Nur wenn HP wirklich läuft
  AND m.Power100W > 0
GROUP BY s.hour, p.outdoor_temp_c, p.wago_vl_soll_c
HAVING cop IS NOT NULL AND cop <= 6
ORDER BY s.hour DESC
"""

# ── Heizkurve ────────────────────────────────────────────────────────────────
def heat_curve(t_ext, t_int=20.0, offset=0.0, ty_c=55.0, t_int_c=20.0,
               t_ext_c=-15.0, t_diff_c=10.0, c=1.33, ty_min=25.0, ty_max=70.0):
    """OSCAT HEAT_TEMP: TY = TR + T_DIFF_C/2·TX + (TY_C - T_DIFF_C/2 - TR)·TX^(1/C)"""
    tr = t_int + offset
    tx = (tr - t_ext) / (t_int_c - t_ext_c)
    if tx <= 0:
        return ty_min
    ty = tr + (t_diff_c / 2) * tx + (ty_c - t_diff_c / 2 - tr) * (tx ** (1.0 / c))
    return max(ty_min, min(ty_max, round(ty, 1)))

# ── Ausgabe ──────────────────────────────────────────────────────────────────
def bar(cop, width=20):
    """Einfacher ASCII-Balken für COP-Visualisierung (max COP=6)."""
    filled = int(min(cop / 6.0, 1.0) * width)
    return "█" * filled + "░" * (width - filled)

def main():
    try:
        conn = pymysql.connect(**DB)
    except Exception as e:
        print(f"DB-Verbindungsfehler: {e}")
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            cur.execute(QUERY, (start_str, end_str, start_str, end_str))
            rows = cur.fetchall()

    if not rows:
        print(f"Keine Daten für {start_str} – {end_str}")
        sys.exit(0)

    # Header
    print()
    print(f"  COP-Stundenbericht  |  {start_str[:10]}  bis  {end_str[:10]}")
    print("─" * 122)
    print(f"  {'Stunde':<16}  {'El[kWh]':>7}  {'Th[kWh]':>7}  {'COP':>5}  "
          f"{'VL°C':>5}  {'RL°C':>5}  {'ΔT':>4}  {'l/h':>6}  {'Aus°C':>5}  {'VL-Soll':>7}  {'WAGO-VL':>7}  COP-Balken")
    print("─" * 122)

    cop_sum = 0.0
    cop_count = 0
    el_sum  = 0.0
    th_sum  = 0.0

    for r in rows:
        cop_val = r["cop"] if r["cop"] is not None else 0.0
        cop_sum += cop_val
        cop_count += 1
        el_sum  += float(r["el_kwh"] or 0)
        th_sum  += float(r["th_kwh"] or 0)

        # Volumeflow: m³/h → l/h
        vf_lh = round(r["volumeflow_m3h"] * 1000) if r["volumeflow_m3h"] else 0

        aussen  = f"{r['aussen_c']:>5.1f}" if r['aussen_c'] is not None else "    –"
        if r['aussen_c'] is not None:
            vl_s    = heat_curve(float(r['aussen_c']))
            hs      = " 🔥" if vl_s > 39.0 else ""
            vl_soll = f"{vl_s:>5.1f}{hs}"
        else:
            vl_soll = "      –"
        wago_vl = f"{r['wago_vl_soll_c']:>7.1f}" if r['wago_vl_soll_c'] is not None else "      –"
        print(
            f"  {r['stunde']:<16}  "
            f"{r['el_kwh']:>7.3f}  "
            f"{r['th_kwh']:>7.3f}  "
            f"{cop_val:>5.2f}  "
            f"{r['vorlauf_c']:>5.1f}  "
            f"{r['ruecklauf_c']:>5.1f}  "
            f"{r['delta_t']:>4.1f}  "
            f"{vf_lh:>6}  "
            f"{aussen}  "
            f"{vl_soll}  "
            f"{wago_vl}  "
            f"{bar(cop_val)}"
        )

    print("─" * 122)
    if cop_count:
        cop_avg   = cop_sum / cop_count
        cop_total = th_sum / el_sum if el_sum else 0.0
        print(f"  {'Ø COP':<16}  {'':>7}  {'':>7}  {cop_avg:>5.2f}  "
              f"{'':>5}  {'':>5}  {'':>4}  {'':>6}  {'':>5}  {'':>7}  {'':>7}  {bar(cop_avg)}")
        print(f"  {'Σ':<16}  {el_sum:>7.3f}  {th_sum:>7.3f}  {cop_total:>5.2f}  "
              f"{'':>5}  {'':>5}  {'':>4}  {'':>6}  {'':>5}  {'':>7}  {'':>7}  {bar(cop_total)}")
    print()

if __name__ == "__main__":
    main()

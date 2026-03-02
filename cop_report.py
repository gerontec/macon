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
    COUNT(s.id)                                                      AS messpunkte
FROM sdm72d s
JOIN mbus2 m ON m.dth = s.hour
WHERE s.timestamp BETWEEN %s AND %s
  AND s.active_power_l3 > 50        -- Nur wenn HP wirklich läuft
  AND m.Power100W > 0
GROUP BY s.hour
HAVING cop IS NOT NULL AND cop <= 4
ORDER BY s.hour
"""

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
            cur.execute(QUERY, (start_str, end_str))
            rows = cur.fetchall()

    if not rows:
        print(f"Keine Daten für {start_str} – {end_str}")
        sys.exit(0)

    # Header
    print()
    print(f"  COP-Stundenbericht  |  {start_str[:10]}  bis  {end_str[:10]}")
    print("─" * 95)
    print(f"  {'Stunde':<16}  {'El[kWh]':>7}  {'Th[kWh]':>7}  {'COP':>5}  "
          f"{'VL°C':>5}  {'RL°C':>5}  {'ΔT':>4}  {'l/h':>6}  COP-Balken")
    print("─" * 95)

    cop_sum = 0.0
    cop_count = 0

    for r in rows:
        cop_val = r["cop"] if r["cop"] is not None else 0.0
        cop_sum += cop_val
        cop_count += 1

        # Volumeflow: m³/h → l/h
        vf_lh = round(r["volumeflow_m3h"] * 1000) if r["volumeflow_m3h"] else 0

        print(
            f"  {r['stunde']:<16}  "
            f"{r['el_kwh']:>7.3f}  "
            f"{r['th_kwh']:>7.3f}  "
            f"{cop_val:>5.2f}  "
            f"{r['vorlauf_c']:>5.1f}  "
            f"{r['ruecklauf_c']:>5.1f}  "
            f"{r['delta_t']:>4.1f}  "
            f"{vf_lh:>6}  "
            f"{bar(cop_val)}"
        )

    print("─" * 95)
    if cop_count:
        print(f"  {'Ø COP':<16}  {'':>7}  {'':>7}  {cop_sum/cop_count:>5.2f}  "
              f"{'':>5}  {'':>5}  {'':>4}  {'':>6}  {bar(cop_sum/cop_count)}")
    print()

if __name__ == "__main__":
    main()

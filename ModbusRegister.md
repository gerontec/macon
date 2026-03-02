# MACON MK6019 — Modbus Register-Map

**Board:** MACON MK6019 (Midea-basiertes Mainboard)
**Protokoll:** Macon Protocol V1.3 (2020.07.10)
**Schnittstelle:** RS485 Modbus RTU · 2400 Baud · Parity E · 8N1 · Slave-ID 1
**Erstellt:** 2026-03-01 · Basis: Live-Scan /dev/ttyAMA0

---

## Read/Write Register 2000–2056

| Addr | Beschreibung | Einheit | Wert | Anmerkung |
|------|-------------|---------|------|-----------|
| 2000 | Unit ON/OFF | — | 1 | 0=OFF, 1=ON |
| 2001 | Working mode | — | 5 | 0=Cooling, 1=Fußboden, 2=Fancoil, 5=DHW, 6=Auto |
| 2002 | Cooling setpoint | °C | 49 | |
| 2003 | Heating setpoint | °C | 49 | |
| 2004 | Hot water setpoint | °C | 47 | |
| 2005 | Fan coil Cooling ΔT | °C | 2 | |
| 2006 | Underfloor heating ΔT | °C | 4 | |
| 2007 | Hot water tank ΔT | °C | 4 | |
| 2008 | Fan coil heating ΔT | °C | 5 | |
| 2009 | Main EEV Anfangsöffnung | Steps | 450 | |
| 2010 | UNKNOWN | — | 0 | |
| 2011 | UNKNOWN | — | 15 | möglicherweise Min. Kompressor-Laufzeit (min) |
| 2012 | UNKNOWN | — | 30 | möglicherweise Min. Kompressor-Stoppzeit (min) |
| 2013 | Sterilisierungszeit | min | 23 | |
| 2014 | UNKNOWN | — | 6 | |
| 2015 | UNKNOWN | — | 9 | |
| 2016 | UNKNOWN | — | 17 | |
| 2017 | UNKNOWN | °C? | 48 | nahe an Setpoint-Werten |
| 2018 | UNKNOWN | °C? | 49 | = Cooling setpoint Max? |
| 2019 | UNKNOWN | °C? | 49 | = Heating setpoint Max? |
| 2020 | UNKNOWN | °C? | 49 | = DHW setpoint Max? |
| 2021 | Maximale Solltemperatur | °C | 49 | |
| 2022 | UNKNOWN | — | 0 | |
| 2023 | UNKNOWN | — | 0 | |
| 2024 | UNKNOWN | — | 0 | |
| 2025 | UNKNOWN | — | 0 | |
| 2026 | UNKNOWN | — | 0 | |
| 2027 | UNKNOWN | — | 0 | |
| 2028 | UNKNOWN | — | 0 | |
| 2029 | UNKNOWN | °C? | 70 | möglicherweise Max. Warmwasser-Schutztemperatur |
| 2030 | UNKNOWN | Steps? | 450 | = 2009 EEV-Öffnung (Max?) |
| 2031 | Cooling Außentemp. (Auto-Modus) | °C | 26 | |
| 2032 | Heating Außentemp. (Auto-Modus) | °C | 18 | |
| 2033 | UNKNOWN | — | 24 | möglicherweise Stundenwert |
| 2034 | UNKNOWN | — | 0 | |
| 2035 | UNKNOWN | — | 2 | |
| 2036 | Moduswechsel-Verzögerung (Auto) | s | 30 | |
| 2037 | Abtauzyklus | min | 9 | |
| 2038 | Spulentemp. zum Abtaueintritt | °C | −7 | s16: raw 65529 |
| 2039 | Außentemp. zur Abtauverlängerung | °C | −10 | s16: raw 65526 |
| 2040 | Außen−Spule ΔT zum Abtaueintritt | °C | 10 | |
| 2041 | Abtauzyklusverlängerung | s | 0 | |
| 2042 | Maximale Abtauzeit | s | 8 | |
| 2043 | Spulentemp. zum Abtauaustritt | °C | 13 | |
| 2044 | Wasserrücklauf-Zyklustemperatur | °C | 1 | |
| 2045 | Wasserrücklauf-Zykluszeit | min | 0 | |
| 2046 | Schutz Mindestaußentemperatur | °C | 0 | |
| 2047 | Freq.-Reduktionsschwelle | Hz | 40 | Macon-intern, kein Schreibzugriff |
| 2048 | Cooling Tieftemperaturschutz | °C | 5 | |
| 2049 | Main EEV Regelmodus | — | 0 | 0=Überhitzungsgrad, 1=Festpunkt |
| 2050 | Main EEV Ziel-Überhitzungsgrad | °C | 0 | |
| 2051 | 3-Wege-Ventil 2 Schaltzeit | s | 5 | |
| 2052 | Wasserpumpen-Modus | — | 0 | 0=ON/OFF per Para.45, 1=Immer AUS, 2=Immer EIN |
| 2053 | Wasserpumpen-Intervall | min | 5 | |
| 2054 | Standby-Pumpenzuschaltung Außentemp. | °C | 0 | |
| 2055 | Wasserweg-Reinigungsfunktion | — | 0 | 0=AUS, 1=Pumpe, 2=Pumpe+3W1, 3=Pumpe+3W1+3W2 |
| 2056 | Host-Frequenzsteuerung aktiv | — | 1 | 0=NEIN, 1=JA |

---

## Undokumentiert / Lücke 2057–2099 (Wechselrichter-Daten)

| Addr | Beschreibung | Wert (dez) | Wert (hex) | Anmerkung |
|------|-------------|-----------|-----------|-----------|
| 2057 | Kompressor Sollfrequenz | 67 | 0x0043 | von Daemon beschrieben (FC03 Write) |
| 2058 | UNKNOWN | 0 | 0x0000 | |
| 2059 | UNKNOWN | 43707 | 0xAABB | Magic-Marker / Sync-Byte Wechselrichterbus |
| 2060 | UNKNOWN | 828 | 0x033C | Wechselrichter-intern |
| 2061 | UNKNOWN | 2051 | 0x0803 | Wechselrichter-intern |
| 2062 | UNKNOWN | 17242 | 0x435A | ASCII "CZ" — Modell-ID? |
| 2063 | UNKNOWN | 17152 | 0x4300 | ASCII "C\0" |
| 2064 | UNKNOWN | 0 | 0x0000 | |
| 2065 | UNKNOWN | 0 | 0x0000 | |
| 2066 | UNKNOWN | 39594 | 0x9AAA | Wechselrichter-intern |
| 2067 | UNKNOWN | 39680 | 0x9B00 | Wechselrichter-intern |
| 2068 | UNKNOWN | 30052 | 0x7564 | Wechselrichter-intern |
| 2069 | UNKNOWN | 21622 | 0x5476 | Wechselrichter-intern |
| 2070 | UNKNOWN | 16384 | 0x4000 | Wechselrichter-intern |
| 2071 | UNKNOWN | 41472 | 0xA200 | Wechselrichter-intern |
| 2072 | UNKNOWN | 31021 | 0x792D | Wechselrichter-intern |
| 2073 | UNKNOWN | 0 | 0x0000 | |
| 2074 | UNKNOWN | 1024 | 0x0400 | |
| 2075 | UNKNOWN | 38436 | 0x9624 | Wechselrichter-intern |
| 2076 | UNKNOWN | 3904 | 0x0F40 | |
| 2077 | UNKNOWN | 16404 | 0x4014 | |
| 2078 | UNKNOWN | 5206 | 0x1456 | |
| 2079 | UNKNOWN | 512 | 0x0200 | |
| 2080 | UNKNOWN | 100 | 0x0064 | |
| 2081 | UNKNOWN | 15360 | 0x3C00 | |
| 2082 | UNKNOWN | 0 | 0x0000 | |
| 2083 | UNKNOWN | 0 | 0x0000 | |
| 2084 | UNKNOWN | 152 | 0x0098 | |
| 2085 | UNKNOWN | 22786 | 0x5902 | |
| 2086 | UNKNOWN | 15360 | 0x3C00 | |
| 2087 | UNKNOWN | 60 | 0x003C | |
| 2088 | UNKNOWN | 53248 | 0xD000 | |
| 2089 | UNKNOWN | 38401 | 0x9601 | |
| 2090 | UNKNOWN | 316 | 0x013C | |
| 2091 | UNKNOWN | 45056 | 0xB000 | |
| 2092–2099 | UNKNOWN | 0 | 0x0000 | |

---

## Read-Only Register 2100–2138

| Addr | Beschreibung | Einheit | Wert | Anmerkung |
|------|-------------|---------|------|-----------|
| 2100 | Pufferspeicher-Temperatur | °C | 39 | |
| 2101 | UNKNOWN | — | 0 | möglicherweise 2. Tank-Sensor (nicht vorhanden) |
| 2102 | Vorlauf-Wassertemperatur (Ausgang WP) | °C | 46 | |
| 2103 | Rücklauf-Wassertemperatur (Eingang WP) | °C | 38 | |
| 2104 | Heißgastemperatur | °C | 58 | |
| 2105 | Saugtemperatur | °C | 2 | |
| 2106 | UNKNOWN | — | 0 | möglicherweise Innenspulen-Sensor |
| 2107 | Außenwärmetauscher-Temperatur | °C | 2 | Protokoll: "External coil temp" |
| 2108 | Kühlspulen-Temperatur | °C | 45 | |
| 2109 | UNKNOWN | — | 0 | |
| 2110 | Außenluft-Temperatur | °C | 0 | kein Außenfühler verbaut → immer 0 |
| 2111 | UNKNOWN | — | 0 | |
| 2112 | UNKNOWN | — | 0 | |
| 2113 | UNKNOWN | — | 0 | |
| 2114 | IPM-Temperatur (Invertermodul) | °C | 36 | |
| 2115 | Sole-Eingangstemperatur | °C | 10 | |
| 2116 | Sole-Ausgangstemperatur | °C | 4 | |
| 2117 | UNKNOWN | — | 0 | |
| 2118 | Kompressor Istfrequenz | Hz | 67 | |
| 2119 | DC-Lüftermotor-Drehzahl | RPM | 0 | kein DC-Lüfter verbaut |
| 2120 | AC-Eingangsspannung | V | 237 | |
| 2121 | AC-Eingangsstrom | A | 13 | |
| 2122 | DC-Zwischenkreisspannung | V | 364 | |
| 2123 | UNKNOWN | — | 9 | möglicherweise Ist-Überhitzungsgrad |
| 2124 | Primär-EEV Öffnung | Steps | 157 | |
| 2125 | Sekundär-EEV Öffnung | Steps | 40 | |
| 2126 | UNKNOWN | — | 17 | |
| 2127 | UNKNOWN | — | 3 | |
| 2128 | UNKNOWN (Betriebsstunden?) | h | 1288 | 1288 h ≈ 53 Tage; stabil über alle Scans |
| 2129 | UNKNOWN | — | 5 | |
| 2130 | UNKNOWN | — | 0 | |
| 2131 | UNKNOWN | — | 0 | |
| 2132 | UNKNOWN (AC-Spannung Spiegel?) | V | 235 | ≈ Reg 2120 zeitversetzt |
| 2133 | Systemstatus 1 | Bits | 0x0000 | siehe Bit-Map |
| 2134 | Fehlercode 1 | Bits | 0x0000 | siehe Bit-Map |
| 2135 | Systemstatus 2 | Bits | 0x8F23 | siehe Bit-Map |
| 2136 | Systemstatus 3 | Bits | 0x4009 | siehe Bit-Map |
| 2137 | Fehlercode 2 | Bits | 0x0000 | siehe Bit-Map |
| 2138 | Fehlercode 3 | Bits | 0x0000 | siehe Bit-Map |

---

## Undokumentiert nach RO-Block 2139–2199

| Addr | Beschreibung | Wert (dez) | Anmerkung |
|------|-------------|-----------|-----------|
| 2139 | UNKNOWN | 129 (0x81) | |
| 2140 | UNKNOWN | 15 | |
| 2141–2144 | UNKNOWN | 10 | |
| 2145 | UNKNOWN | 15 | |
| 2146–2169 | UNKNOWN | 0 | |
| 2170 | UNKNOWN | 1283 | |
| 2171–2172 | UNKNOWN | 0 | |
| 2173 | UNKNOWN (Freq. Spiegel?) | 67 | = Reg 2057 Sollfrequenz |
| 2174 | UNKNOWN | 100 | möglicherweise Leistung % |
| 2175 | UNKNOWN | 11384 | |
| 2176 | UNKNOWN (Spannung Spiegel?) | 236 | ≈ Reg 2120 AC-Spannung |
| 2177 | UNKNOWN | 129 | = Reg 2139 |
| 2178 | UNKNOWN | 91 | |
| 2179 | UNKNOWN | 365 | |
| 2180 | UNKNOWN | 0 | |
| 2181 | UNKNOWN | 91 | = Reg 2178 |
| 2182 | UNKNOWN | 0 | |
| 2183 | UNKNOWN | 5124 | |
| 2184 | UNKNOWN | 0 | |
| 2185 | UNKNOWN | 19 | |
| 2186 | UNKNOWN | 6661 | |
| 2187 | UNKNOWN | 5752 | |
| 2188 | UNKNOWN | 51476 | |
| 2189–2190 | UNKNOWN | 0 | |
| 2191 | UNKNOWN | 1 | |
| 2192 | Firmware-String Byte 1/2 | 0x5748 | ASCII "WH" |
| 2193 | Firmware-String Byte 3/4 | 0x5031 | ASCII "P1" |
| 2194 | Firmware-String Byte 5/6 | 0x3333 | ASCII "33" |
| 2195 | Firmware-String Byte 7/8 | 0x3030 | ASCII "00" → gesamt: **"WHP13300"** |
| 2196 | Firmware-String Byte 9/10 | 0x5053 | ASCII "PS" |
| 2197 | Firmware-String Byte 11/12 | 0x4450 | ASCII "DP" |
| 2198 | Firmware-String Byte 13/14 | 0x4338 | ASCII "C8" |
| 2199 | Firmware-String Byte 15/16 | 0x4651 | ASCII "FQ" → gesamt: **"WHP13300PSDPC8FQ"** |

---

## Reserviert 2300–2349

Alle Register antworten mit 0x0000 — reserviert oder nicht belegt.

---

## Factory-Bereich 3000–3049

| Addr | Beschreibung | Wert (hex) | Anmerkung |
|------|-------------|-----------|-----------|
| 3000 | UNKNOWN | 0x0600 | Versions-/Config-Byte |
| 3001 | UNKNOWN | 0x0000 | |
| 3002 | UNKNOWN | 0x0001 | Enable-Flag |
| 3003–3004 | UNKNOWN | 0x0000 | |
| 3005 | UNKNOWN | 0x04CC | |
| 3006 | UNKNOWN | 0x0100 | Config |
| 3007 | UNKNOWN | 0x0400 | Config |
| 3008 | UNKNOWN | 0x0001 | |
| 3009 | UNKNOWN | 0x0100 | |
| 3010–3012 | UNKNOWN | 0x0000 | |
| 3013–3049 | UNKNOWN (Kalibrierungstabelle) | 0xDF40–0xFF00 | NTC-Kennlinien- oder Wechselrichter-Kurvendaten |

---

## EEPROM-Bereich 4000–4019

| Addr | Beschreibung | Wert | Anmerkung |
|------|-------------|------|-----------|
| 4000–4019 | UNKNOWN (uninitialisiert) | 0x0F0F | Standard EEPROM-Füllwert — kein Inhalt |

---

## Bit-Map Systemstatus & Fehlercodes

### Reg 2133 — Systemstatus 1

| Bit | Bedeutung |
|-----|-----------|
| 0 | Frequenz an Obergrenze |
| 1 | Frequenz an Untergrenze |

### Reg 2134 — Fehlercode 1

| Bit | Bedeutung |
|-----|-----------|
| 0 | Sole-Eingang Sensorfehler |
| 1 | Sole-Ausgang Sensorfehler |
| 2 | Sole-Durchflussschutz |
| 3 | Tanksensor-Fehler |

### Reg 2135 — Systemstatus 2

| Bit | Bedeutung |
|-----|-----------|
| 0 | Unit EIN |
| 1 | Kompressor EIN |
| 2 | Hohe Lüfterstufe |
| 5 | Wasserpumpe EIN |
| 6 | 4-Wege-Ventil EIN |
| 7 | Elektroheizung EIN |
| 8 | Wasserdurchfluss-Schalter EIN |
| 9 | Hochdruckschalter EIN |
| 10 | Niederdruckschalter EIN |
| 11 | Remote ON/OFF aktiv |
| 12 | Moduswechsel aktiv |
| 13 | 3-Wege-Ventil 1 EIN |
| 14 | 3-Wege-Ventil 2 EIN |

### Reg 2136 — Systemstatus 3

| Bit | Bedeutung |
|-----|-----------|
| 0 | Magnetventil EIN |
| 1 | Entlastungsventil EIN |
| 2 | Ölrücklaufventil EIN |
| 3 | Sole-Pumpe EIN |
| 4 | Sole-Frostschutz aktiv |
| 5 | Abtauung aktiv |
| 6 | Kältemittelrückgewinnung |
| 7 | Ölrücklauf aktiv |
| 8 | Kabelgebundener Regler verbunden |
| 9 | Energiesparbetrieb |
| 10 | Primärkreis Frostschutz |
| 11 | Sekundärkreis Frostschutz |
| 12 | Hochtemperatur-Sterilisierung |
| 13 | Sekundäre Wasserpumpe EIN |
| 14 | Remote ON/OFF Heizung/Kühlung |

### Reg 2137 — Fehlercode 2

| Bit | Bedeutung |
|-----|-----------|
| 0 | Indoor EE Fehler |
| 1 | Outdoor EE Fehler |
| 2 | Vorlauf Sensorfehler |
| 3 | Rücklauf Sensorfehler |
| 4 | Kühlspulen Frostschutz |
| 5 | Außenspulen Sensorfehler |
| 6 | Heißgas Sensorfehler |
| 7 | Sauggas Sensorfehler |
| 8 | Außentemperatur Sensorfehler |
| 9 | Wechselrichter-Kommunikationsfehler |
| 10 | Bedienpanel-Kommunikationsfehler |
| 11 | Kompressor Startfehler |
| 12 | Innen/Außen-Kommunikationsfehler |
| 13 | IPM-Fehler |
| 14 | Vorlauf Hochtemperaturschutz |
| 15 | AC-Spannungsschutz |

### Reg 2138 — Fehlercode 3

| Bit | Bedeutung |
|-----|-----------|
| 0 | AC-Stromschutz |
| 1 | Sole-Durchflussschalter Schutz |
| 2 | Kommunikationsfehler |
| 3 | EEPROM-Fehler |
| 5 | Heißgas-Temperaturschutz |
| 6 | Hochdruckschalter Schutz |
| 7 | Niederdruckschalter Schutz |
| 8 | Wasserdurchfluss-Schutz |
| 9 | Kühlspulen Überhitzungsschutz |
| 10 | Tieftemperaturschutz Außen |
| 11 | Primärkreis Niederdruckschutz |
| 12 | Sekundärkreis Niederdruckschutz |
| 13 | Großes ΔT Vorlauf/Rücklauf Schutz |
| 14 | Vorlauf Tieftemperaturschutz |
| 15 | Kompressor Differenzdruckschutz |

---

## Hinweise

- **Signed Register:** 2038, 2039 → Wert ≥ 32768 als int16 interpretieren: `v - 65536`
- **Skaliert:** 2140 → `s16(raw) × 0.1`
- **Nicht per Modbus erreichbar:** FC04 (Input Register) antwortet nicht; FC03 Bereiche 2200–2299 antworten nicht
- **Main Control Verification Code** (auf Bedienpanel sichtbar, z.B. −7605): internes Firmware-CRC des Mainboard-MCU, nicht über Modbus exponiert
- **Firmware-String** (Reg 2192–2199): `WHP13300PSDPC8FQ`


Empfehlung Register 2050
Aktuell: Reg 2049 = 0 (Überhitzungsregelung aktiv)
         Reg 2050 = 0 (Ziel-SH = 0 K → unkonfiguriert!)

→ Reg 2050 auf 7 schreiben
python# Beispiel mit pymodbus
client.write_register(2050, 7, slave=1)
```

**Schrittweise:**
1. WP ausschalten (Reg 2000 = 0)
2. Reg 2050 = 7 schreiben
3. WP einschalten, 15–20 min beobachten
4. Ziel: Ist-SH sinkt auf 6–8 K, discharge_temp steigt leicht (>35 °C), COP steigt

---

## Was du beobachten solltest nach der Änderung
```
discharge_temp  → sollte leicht steigen (35 → 45–55 °C wäre ideal)
suction_temp    → sinkt etwas (EEV öffnet mehr)
EEV steps       → Reg 2124 sollte steigen (>157)
COP             → sollte von 3.33 auf >3.5 gehen


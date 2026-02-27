#!/usr/bin/env python3
"""
macon_watchdog.py — Überschwemmungssicherung Grundwasserpumpe
Läuft jede Minute per Cron auf dem MQTT-Host (192.168.178.218).

Liest das retained 'heatmacon'-Topic und prüft den Timestamp.
Falls der letzte Status > TIMEOUT_SEC alt ist UND shelly_on=True:
  → Zwangsabschaltung Shelly (Grundwasserpumpe).
"""

import json
import time
import sys
import requests
from datetime import datetime
import paho.mqtt.client as mqtt

MQTT_BROKER  = "localhost"
MQTT_PORT    = 1883
MQTT_TOPIC   = "heatmacon"
SHELLY_IP    = "192.168.178.100"
TIMEOUT_SEC  = 600


def log(msg: str):
    # Cron leitet stdout → LOG_FILE um, daher nur print() nötig
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def shelly_off() -> bool:
    try:
        r = requests.post(
            f"http://{SHELLY_IP}/rpc/Switch.Set",
            json={"id": 0, "on": False},
            timeout=5,
        )
        return r.status_code == 200
    except Exception as e:
        log(f"ERROR Shelly HTTP: {e}")
        return False


def get_retained_payload() -> dict | None:
    result = [None]

    def on_msg(client, userdata, msg):
        try:
            result[0] = json.loads(msg.payload.decode())
        except Exception:
            pass

    c = mqtt.Client()
    c.on_message = on_msg
    try:
        c.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
        c.subscribe(MQTT_TOPIC)
        c.loop_start()
        deadline = time.time() + 3.0
        while result[0] is None and time.time() < deadline:
            time.sleep(0.1)
        c.loop_stop()
        c.disconnect()
    except Exception as e:
        log(f"ERROR MQTT: {e}")
    return result[0]


def main():
    payload = get_retained_payload()

    if payload is None:
        log("WARNING kein retained MQTT-Payload auf heatmacon — Pi3 noch nie gesendet?")
        sys.exit(0)

    ts_str   = payload.get("timestamp")
    shelly_on = payload.get("shelly_on", False)

    if ts_str is None:
        log("WARNING kein Timestamp im Payload")
        sys.exit(0)

    try:
        ts  = datetime.fromisoformat(ts_str)
        age = (datetime.now() - ts).total_seconds()
    except Exception as e:
        log(f"ERROR Timestamp parse: {e}")
        sys.exit(1)

    if age <= TIMEOUT_SEC:
        # Normalzustand — nur alle 10 Minuten loggen (age < 60 = frisch)
        if age < 70:
            log(f"OK letzter Status vor {int(age)}s, Shelly={'EIN' if shelly_on else 'AUS'}")
        sys.exit(0)

    # Timeout überschritten
    if not shelly_on:
        log(f"WARNING TIMEOUT {int(age)}s — kein Status vom Pi3, Shelly bereits AUS — OK")
        sys.exit(0)

    # Zwangsabschaltung
    log(f"ERROR TIMEOUT {int(age)}s — Pi3 sendet nicht, Shelly=EIN → ZWANGSABSCHALTUNG")
    if shelly_off():
        log("ERROR Zwangsabschaltung Shelly: AUS OK (Überschwemmungsschutz)")
    else:
        log("ERROR Zwangsabschaltung FEHLGESCHLAGEN")
        sys.exit(1)


if __name__ == "__main__":
    main()

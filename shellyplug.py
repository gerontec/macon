#!/usr/bin/env python3
"""
shellyplug.py — Shelly Plug S Gen3: manuelles Schalten per MQTT + HTTP-Fallback

Verwendung:
  ./shellyplug.py on
  ./shellyplug.py off
  ./shellyplug.py toggle

Automatische Steuerung (Grundwasserpumpe) übernimmt macon_daemon.py.
"""

import paho.mqtt.client as mqtt
import json
import time
import sys
import argparse
import requests

# ─── Konfiguration ────────────────────────────────────────────────────────────
MQTT_BROKER   = "localhost"
MQTT_PORT     = 1883
MQTT_USERNAME = None
MQTT_PASSWORD = None

DEVICE_ID     = "shellyplugsg3-8cbfea92c554"
PREFIX        = DEVICE_ID

COMMAND_TOPIC = f"{PREFIX}/rpc"
STATUS_TOPIC  = f"{PREFIX}/status/switch:0"
EVENT_TOPIC   = f"{PREFIX}/events/rpc"

SHELLY_IP     = "192.168.178.100"

CLIENT_ID     = "shelly-ctl-" + str(int(time.time()))
TIMEOUT_SEC   = 6.0
# ─────────────────────────────────────────────────────────────────────────────

final_status    = None
status_received = False


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        client.subscribe(STATUS_TOPIC)
        client.subscribe(EVENT_TOPIC)
    else:
        print(f"Verbindungsfehler: {rc}")
        sys.exit(1)


def on_message(client, userdata, msg):
    global final_status, status_received

    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
    except Exception:
        return

    updated = False

    if topic == STATUS_TOPIC and isinstance(payload, dict):
        if "output" in payload:
            final_status = payload.copy()
            updated = True

    elif topic == EVENT_TOPIC:
        if payload.get("method") == "NotifyStatus":
            params = payload.get("params", {})
            switch = params.get("switch:0", {})
            if switch:
                if final_status is None:
                    final_status = {}
                final_status.update(switch)
                updated = True

    if updated:
        status_received = True


def publish_rpc(client, method, params=None):
    payload = {
        "id": int(time.time() * 1000),
        "src": CLIENT_ID,
        "method": method,
    }
    if params:
        payload["params"] = params

    result = client.publish(COMMAND_TOPIC, json.dumps(payload), qos=1)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        print(f"Publish-Fehler: {result.rc}")
        sys.exit(1)


def wait_for_mqtt_status():
    global final_status, status_received
    start = time.time()
    final_status = None
    status_received = False

    while time.time() - start < TIMEOUT_SEC:
        if status_received and final_status and "output" in final_status:
            return True
        time.sleep(0.1)
    return False


def get_status_via_http():
    try:
        url = f"http://{SHELLY_IP}/rpc/Switch.GetStatus?id=0"
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and "output" in data:
                return data
    except Exception as e:
        print(f"  HTTP-Fehler: {e}")
    return None


def print_status(status_data, source="MQTT"):
    if not status_data or "output" not in status_data:
        print("Kein valider Status verfügbar.")
        return

    out = status_data["output"]
    state = "EIN" if out else "AUS"
    apower = status_data.get("apower", 0.0)
    voltage = status_data.get("voltage", 0.0)

    print(f"\nERFOLG: Steckdose ist jetzt {state} ({source})")
    print(f"  • Schalter: {state} (output: {out})")
    print(f"  • Leistung: {apower:.2f} W")
    if voltage > 100:
        print(f"  • Spannung: {voltage:.1f} V")


def main():
    parser = argparse.ArgumentParser(
        description="Shelly Plug Gen3 – manuelles Schalten (MQTT + HTTP-Fallback)"
    )
    parser.add_argument("action", choices=["on", "off", "toggle"],
                        help="on | off | toggle")
    args = parser.parse_args()

    client = mqtt.Client(client_id=CLIENT_ID, protocol=mqtt.MQTTv5)
    client.on_connect = on_connect
    client.on_message = on_message
    if MQTT_USERNAME and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
    except Exception as e:
        print(f"MQTT fehlgeschlagen: {e} → HTTP-Fallback …")
        get_status_via_http()
        sys.exit(1)

    client.loop_start()
    time.sleep(1.2)

    if args.action == "on":
        publish_rpc(client, "Switch.Set", {"id": 0, "on": True})
    elif args.action == "off":
        publish_rpc(client, "Switch.Set", {"id": 0, "on": False})
    elif args.action == "toggle":
        publish_rpc(client, "Switch.Toggle", {"id": 0})

    print(f"→ Befehl gesendet, warte max. {TIMEOUT_SEC}s …")
    success = False

    if wait_for_mqtt_status():
        print_status(final_status, source="MQTT")
        success = True
    else:
        print("→ MQTT-Timeout → HTTP-Fallback …")
        http_status = get_status_via_http()
        if http_status:
            print_status(http_status, source="HTTP")
            success = True
        else:
            print("→ HTTP ebenfalls fehlgeschlagen.")

    client.loop_stop()
    client.disconnect()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

import serial
import time
import json
import paho.mqtt.client as mqtt
from datetime import datetime

# =================================================
# CONFIGURATION
# =================================================

SERIAL_PORT = "COM5"
BAUDRATE = 9600

MQTT_BROKER = "10.54.128.186"
MQTT_PORT = 1883
MQTT_TOPIC = "parking/places"

TOTAL_PLACES = 19
places_disponibles = TOTAL_PLACES

# =================================================
# MQTT
# =================================================

client = mqtt.Client()
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

# =================================================
# SERIAL
# =================================================

ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
time.sleep(2)

print("PC Edge démarré")
print("En attente des messages Arduino...")

# =================================================
# BOUCLE PRINCIPALE
# =================================================

while True:
    if ser.in_waiting > 0:
        line = ser.readline().decode("utf-8").strip()
        if not line:
            continue

        print(f"Message reçu : {line}")

        # ---------- CAS PLACES ----------
        if line.startswith("PLACES="):
            try:
                new_places = int(line.split("=")[1])
            except ValueError:
                print("⚠️ Valeur PLACES invalide")
                continue

            # Ignore si pas de changement
            if new_places == places_disponibles:
                continue

            places_disponibles = new_places

            timestamp = int(time.time())

            mqtt_payload = {
                "places_disponibles": places_disponibles,
                "timestamp": timestamp
            }

            client.publish(MQTT_TOPIC, json.dumps(mqtt_payload), retain=True)

            print(f"➡️ Places disponibles : {places_disponibles}")

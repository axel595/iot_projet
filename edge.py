import serial
import time
import json
import re
import hashlib
import hmac
import paho.mqtt.client as mqtt
from datetime import datetime

# =================================================
# CONFIGURATION
# =================================================
SERIAL_PORT = "COM5"
BAUDRATE = 9600

MQTT_BROKER = "10.54.128.186"
MQTT_PORT = 1883

TOPIC_PLACES = "parking/places"   # ✅ JSON retain=True (comme avant)
TOPIC_EVENTS = "parking/events"   # optionnel (retain=False)

TOTAL_PLACES = 19
places_disponibles = TOTAL_PLACES

ANTI_REBOND_DELAY = 3  # secondes
last_event_time = 0

# =================================================
# CLÉS HMAC (2 capteurs)
# =================================================
KEY_ENTREE = b"CESI_PARKING_ENTREE"
KEY_SORTIE = b"CESI_PARKING_SORTIE"

# =================================================
# MQTT
# =================================================
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
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
# HELPERS
# =================================================
def can_trigger_event(now: float) -> bool:
    return (now - last_event_time) >= ANTI_REBOND_DELAY


def publish_places(ts: int):
    payload = {
        "places_disponibles": places_disponibles,
        "timestamp": ts
    }
    client.publish(TOPIC_PLACES, json.dumps(payload), retain=True)
    print(f"📤 MQTT -> {TOPIC_PLACES} : {payload}")

def publish_event(event: str, distance, ts: int):
    payload = {
        "event": event,
        "distance": distance,
        "places_disponibles": places_disponibles,
        "timestamp": ts
    }
    client.publish(TOPIC_EVENTS, json.dumps(payload))


# ---------- HMAC ----------
def calculate_hmac_16(message: str, key: bytes) -> str:
    """HMAC-SHA256 tronqué à 16 hex (8 octets)"""
    h = hmac.new(key, message.encode("utf-8"), hashlib.sha256)
    return h.hexdigest()[:16]


def pick_key_for_message(message: str) -> bytes | None:
    """
    Choisit la clé selon le message SANS le |H=
    Exemples acceptés:
      - "ENTREE|D=25.3"
      - "SORTIE|D=10.2"
      - "Entree 33.9 cm"
      - "Sortie 4.7 cm"
      - "BOOT..."
    """
    low = message.lower().strip()

    if low.startswith("entree") or low.startswith("entrée") or low.startswith("entree|") or low.startswith("entrée|") or low.startswith("entree "):
        return KEY_ENTREE
    if low.startswith("sortie") or low.startswith("sortie|") or low.startswith("sortie "):
        return KEY_SORTIE

    if low.startswith("boot"):
        # BOOT : on ne sait pas, on testera les deux
        return None

    return None


def verify_signed_message(raw_message: str) -> tuple[bool, str, str]:
    """
    Retourne (valide, message_sans_hash, reason)
    """
    if "|H=" not in raw_message:
        return False, raw_message, "pas de |H="

    message, hash_recu = raw_message.rsplit("|H=", 1)
    message = message.strip()
    hash_recu = hash_recu.strip().lower()

    key = pick_key_for_message(message)

    # Cas BOOT : tester les 2 clés
    if message.lower().startswith("boot"):
        h1 = calculate_hmac_16(message, KEY_ENTREE)
        h2 = calculate_hmac_16(message, KEY_SORTIE)
        if hash_recu == h1 or hash_recu == h2:
            return True, message, "boot ok"
        return False, message, "boot hash invalide"

    if key is None:
        return False, message, "type inconnu pour choisir la clé"

    hash_calc = calculate_hmac_16(message, key)
    if hash_calc == hash_recu:
        return True, message, "ok"
    return False, message, f"hash invalide (reçu={hash_recu}, calc={hash_calc})"


# ---------- Parsing event/distance ----------
def extract_distance(message: str):
    """
    Supporte:
      - "ENTREE|D=25.3"
      - "SORTIE|D=25.3"
      - "Entree 33.9 cm"
      - "Sortie 4.7 cm"
    """
    # D=xx
    m = re.search(r"\bD=([-+]?\d+(?:[.,]\d+)?)\b", message, flags=re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None

    # "xx cm"
    m = re.search(r"([-+]?\d+(?:[.,]\d+)?)\s*cm", message, flags=re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None

    return None


def detect_event_type(message: str) -> str | None:
    low = message.lower().strip()
    if low.startswith("entree") or low.startswith("entrée") or low.startswith("entree|") or low.startswith("entrée|") or low.startswith("entree "):
        return "ENTREE"
    if low.startswith("sortie") or low.startswith("sortie|") or low.startswith("sortie "):
        return "SORTIE"
    if low.startswith("entree") is False and message.startswith("ENTREE"):
        return "ENTREE"
    if message.startswith("SORTIE"):
        return "SORTIE"
    return None


# =================================================
# BOUCLE PRINCIPALE
# =================================================
while True:
    try:
        if ser.in_waiting <= 0:
            time.sleep(0.05)
            continue

        raw_full = ser.readline().decode("utf-8", errors="ignore").strip()
        if not raw_full:
            continue

        # =================================================
        # ✅ FILTRE ROBUSTE : ignore TOUT ce qui contient les infos debug LoRa
        # (même si ça commence par "Trame brute : ...")
        # =================================================
        if ("+TEST" in raw_full) or ("RSSI:" in raw_full) or ("SNR:" in raw_full) or ("LEN:" in raw_full):
            continue
        if raw_full.startswith("-"):
            continue

        print(f"\n📡 Trame brute : {raw_full}")

        # =================================================
        # ✅ VERIF HMAC + PRINT HASH
        # =================================================
        if "|H=" not in raw_full:
            # si tu veux rejeter les non-signés : ignore direct
            print("❌ Message non signé -> ignoré")
            continue

        payload, recv_h = raw_full.rsplit("|H=", 1)
        payload = payload.strip()
        recv_h = recv_h.strip().lower()

        # détection type
        low = payload.lower()
        if low.startswith("entree") or low.startswith("entrée") or payload.startswith("ENTREE"):
            key = KEY_ENTREE
            key_label = "ENTREE"
        elif low.startswith("sortie") or payload.startswith("SORTIE"):
            key = KEY_SORTIE
            key_label = "SORTIE"
        else:
            print("❌ Type inconnu (pas ENTREE/SORTIE) -> ignoré")
            continue

        calc_h = calculate_hmac_16(payload, key)

        # ✅ ICI : print du hash reçu + calculé (ce que tu demandes)
        print(f"🔐 Type: {key_label}")
        print(f"🔐 Payload utilisé HMAC : '{payload}'")
        print(f"🔐 H reçu     : {recv_h}")
        print(f"🔐 H calculé  : {calc_h}")

        if recv_h != calc_h:
            print("❌ HMAC invalide -> ignoré")
            continue

        print("✅ HMAC validé")

        # =================================================
        # LOGIQUE PLACES (-1 entrée, +1 sortie)
        # =================================================
        now = time.time()
        ts = int(now)

        if not can_trigger_event(now):
            print("⏳ Masque actif (anti-rebond)")
            continue

        distance = extract_distance(payload)  # cm si présent

        if key_label == "ENTREE":
            if places_disponibles <= 0:
                print("ℹ️ Parking plein -> ignoré")
                continue
            places_disponibles -= 1
            event = "ENTREE"
        else:
            if places_disponibles >= TOTAL_PLACES:
                print("ℹ️ Parking au max -> ignoré")
                continue
            places_disponibles += 1
            event = "SORTIE"

        last_event_time = now

        print(f"➡️ {event} | Places dispo = {places_disponibles}")

    except Exception as e:
        print(f"⚠️ Erreur boucle: {e}")
        time.sleep(0.5)

import serial
import time
import json
import re
import hashlib
import hmac
import paho.mqtt.client as mqtt


# =================================================
# CONFIGURATION
# =================================================

# Port série utilisé par la gateway Arduino / LoRa
SERIAL_PORT = "COM5"

# Vitesse de communication série
BAUDRATE = 9600

# Paramètres MQTT
MQTT_BROKER = "10.54.128.226"
MQTT_PORT = 1883

# Topics MQTT
TOPIC_PLACES = "parking/places"

# Capacité maximale du parking
TOTAL_PLACES = 19

# Compteur de places disponibles
places_disponibles = TOTAL_PLACES


# =================================================
# ANTI-REBOND
# =================================================

# Délai minimal entre deux événements identiques
ANTI_REBOND_DELAY = 3

# Anti-rebond séparé ENTREE / SORTIE
last_event_time_by_type = {
    "ENTREE": 0.0,
    "SORTIE": 0.0
}


# =================================================
# OFFLINE / ONLINE (SUPERVISION LIAISON)
# =================================================

# Temps maximal sans trame valide avant OFFLINE
NO_DATA_TIMEOUT = 15  # secondes

# Intervalle de republication OFFLINE vers MQTT
OFFLINE_PUBLISH_INTERVAL = 5  # secondes

# Dernière réception d'une trame valide (HMAC OK)
last_valid_rx_time = time.time()

# Dernière publication OFFLINE
last_offline_publish = 0.0

# État courant de la liaison
is_offline = False


# =================================================
# ACK
# =================================================

ACK_ENABLED = True
ACK_RETRIES = 2
ACK_DELAY = 0.05  # pause après envoi ACK


# =================================================
# CLÉS HMAC
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

ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=0.1)

# Laisse le temps à l’Arduino de démarrer
time.sleep(2)

print("PC Edge démarré")
print("En attente des messages Arduino...")


# =================================================
# HELPERS
# =================================================

def can_trigger_event(event_type: str, now: float) -> bool:
    """
    Vérifie si un événement peut être pris en compte
    (anti-rebond par type ENTREE / SORTIE).
    """
    return (now - last_event_time_by_type.get(event_type, 0.0)) >= ANTI_REBOND_DELAY 


def publish_places(ts: int, status: str = "ONLINE", last_seen_seconds: int = 0):
    """
    Publie l’état global du parking.
    Ajout :
    - status : ONLINE / OFFLINE
    - last_seen_seconds : temps depuis dernière trame valide
    """
    payload = {
        "places_disponibles": places_disponibles,
        "timestamp": ts,
        "status": status,
        "last_seen_seconds": last_seen_seconds
    }
    client.publish(TOPIC_PLACES, json.dumps(payload), retain=True) # Retain pour que les nouveaux abonnés aient l’état à jour
    print(f"📤 MQTT -> {TOPIC_PLACES} : {payload}")

def normalize_line(raw: str) -> str | None:
    """
    Nettoie les lignes reçues depuis le port série :
    - supprime les lignes vides
    - ignore les logs techniques
    - enlève les préfixes de debug Arduino
    """
    s = raw.strip()

    if not s:
        return None

    # Logs gateway informatifs
    if s.startswith("GW:"):
        return s

    # Supprime "Trame brute :" si présent
    if s.lower().startswith("trame brute :"):
        s = s.split(":", 1)[1].strip()

    # Logs techniques à ignorer
    noisy_prefixes = (
        "EVENT_SEQ>>",
        "ACK_BUF+",
        "ACK_BUILD>>",
        "RX>>",
        "TX>>",
    )
    for p in noisy_prefixes:
        if s.startswith(p):
            return None

    low = s.lower()
    if "+test" in low or "rssi" in low or "snr" in low or "len:" in low:
        return None

    if s.startswith("-"):
        return None

    return s


# =================================================
# HMAC
# =================================================

def calculate_hmac_8(message: str, key: bytes) -> str:
    """
    HMAC SHA256 tronqué à 8 caractères 
    """
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()[:8].lower()


# =================================================
# ACK
# =================================================

def gateway_send(payload_ascii: str):
    """
    Envoie une trame ASCII vers la gateway LoRa.
    """
    ser.write(f"TX:{payload_ascii}\n".encode("utf-8"))
    ser.flush()
    print(f"📡 ACK TX -> {payload_ascii}")


def send_ack(seq: int, key: bytes):
    """
    Envoie un ACK signé pour confirmer la réception.
    """
    if not ACK_ENABLED:
        return

    base = f"T=A|ID=EDGE|S={seq}"
    sig = calculate_hmac_8(base, key)
    frame = f"{base}|H={sig}"

    for attempt in range(1, ACK_RETRIES + 1):
        try:
            gateway_send(frame)
            time.sleep(ACK_DELAY)
            break
        except Exception as e:
            print(f"⚠️ ACK erreur ({attempt}/{ACK_RETRIES}): {e}")
            time.sleep(0.2)


# =================================================
# PARSING
# =================================================


def parse_kv(payload: str) -> dict[str, str]:
    """
    Transforme une trame clé=valeur en dictionnaire.
    """
    d = {}
    for part in payload.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def distance_from_dc(d: dict[str, str]) -> float | None:
    """
    Convertit DC (décimètres) en centimètres.
    """
    try:
        return int(d.get("DC", "")) / 10.0
    except Exception:
        return None


# =================================================
# SUPERVISION OFFLINE / ONLINE
# =================================================

def mark_link_alive():
    """
    Appelée lorsqu’une trame VALIDE est reçue.
    Met à jour l’état ONLINE.
    """
    global last_valid_rx_time, is_offline

    last_valid_rx_time = time.time()

    if is_offline:
        is_offline = False
        print("Liaison rétablie -> ONLINE")
        publish_places(int(last_valid_rx_time), "ONLINE", 0)


def supervision_tick():
    """
    Détecte une perte de liaison LoRa.
    Publie OFFLINE périodiquement.
    """
    global is_offline, last_offline_publish

    now = time.time()
    silence = now - last_valid_rx_time

    if silence > NO_DATA_TIMEOUT:
        if not is_offline:
            is_offline = True
            print(f"⚠️ OFFLINE : aucune trame valide depuis {int(silence)}s")

        if (now - last_offline_publish) >= OFFLINE_PUBLISH_INTERVAL:
            last_offline_publish = now
            publish_places(
                ts=int(now),
                status="OFFLINE",
                last_seen_seconds=int(silence)
            )


# =================================================
# BOUCLE PRINCIPALE
# =================================================

while True:
    try:
        # Supervision exécutée même sans données série
        supervision_tick()

        if ser.in_waiting <= 0:
            time.sleep(0.02)
            continue

        raw_full = ser.readline().decode("utf-8", errors="ignore").strip()
        line = normalize_line(raw_full)

        if not line:
            continue

        # Logs gateway
        if line.startswith("GW:"):
            print(f"🟢 {line}")
            continue

        print(f"\n📡 Trame brute : {line}")

        # Exige une signature HMAC
        if "|H=" not in line:
            print("❌ Message non signé -> ignoré")
            continue

        payload, recv_h = line.rsplit("|H=", 1)
        payload = payload.strip()
        recv_h = recv_h.strip().lower()

        # Ignore les ACK entrants
        if payload.startswith("T=A|"):
            continue

        # =================================================
        #  FORMAT
        # =================================================
        if payload.startswith("T=E|"):
            d = parse_kv(payload)
            idv = (d.get("ID") or "").upper()

            if idv == "ENTREE":
                key = KEY_ENTREE
                event_type = "ENTREE"
            elif idv == "SORTIE":
                key = KEY_SORTIE
                event_type = "SORTIE"
            else:
                print("❌ ID inconnu")
                continue

            seq = int(d.get("S", "0"))
            calc_h = calculate_hmac_8(payload, key)

            print(f"🔐 Event: {event_type} | Seq={seq}")
            print(f"🔐 H reçu     : {recv_h}")
            print(f"🔐 H calculé  : {calc_h}")

            if recv_h != calc_h:
                print("❌ HMAC invalide")
                continue

            print("✅ HMAC validé (T=E)")
            mark_link_alive()
            send_ack(seq, key)

            now = time.time()
            ts = int(now)

        # =================================================
        # TRAITEMENT COMMUN
        # =================================================

        if not can_trigger_event(event_type, now):
            print("⏳ Anti-rebond actif")
            continue

        if event_type == "ENTREE":
            if places_disponibles > 0:  # Sécurité anti-dépassement négatif
                places_disponibles -= 1
        else:
            if places_disponibles < TOTAL_PLACES: # Sécurité anti-dépassement
                places_disponibles += 1

        last_event_time_by_type[event_type] = now

        publish_places(ts)
        print(f"➡️ {event_type} | Places dispo = {places_disponibles}")

    except Exception as e:
        print(f"⚠️ Erreur boucle: {e}")
        time.sleep(0.3)

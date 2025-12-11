import requests
import json
import random

# Remplacez par l'IP de votre VPS
VPS_URL = "http://51.178.87.25:5000"
TEAM_ID = "test-debug"

print(f"Test d'envoi vers {VPS_URL}...")

# Fausses données de télémétrie
fake_samples = []
for i in range(100):
    fake_samples.append({
        "d": i * 10, "s": 200 + random.randint(-5, 5),
        "t": 100, "b": 0, "g": 6, "w": 0.0,
        "f": 50.0, "ve": 90.0, "tw": 100
    })

payload = {
    "sessionId": TEAM_ID,
    "lapNumber": 1,
    "driver": "Tester",
    "lapTime": 95.5,
    "samples": fake_samples
}

try:
    resp = requests.post(f"{VPS_URL}/api/telemetry/lap", json=payload, timeout=5)
    print(f"Code retour: {resp.status_code}")
    print(f"Réponse: {resp.text}")
    if resp.status_code == 200:
        print("✅ SUCCÈS : Le VPS a bien reçu la télémétrie !")
    else:
        print("❌ ÉCHEC : Le VPS a répondu une erreur.")
except Exception as e:
    print(f"❌ ERREUR CRITIQUE : Impossible de joindre le VPS. {e}")
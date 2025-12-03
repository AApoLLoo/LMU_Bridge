import threading
import queue
import requests
import json
import time

# --- CONFIGURATION SUPABASE (Tirée de votre fichier .env frontend) ---
SUPABASE_URL = "https://gsbvncqsbaakharvhobg.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdzYnZuY3FzYmFha2hhcnZob2JnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQ3NTYzMjksImV4cCI6MjA4MDMzMjMyOX0.ZlD29ZMfU5OjBOeUlHU3KHB1WZVEI81kEQHhQg3FruQ"


class SupabaseConnector:
    def __init__(self):
        self.url = SUPABASE_URL
        self.headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"  # Pour ne pas récupérer toute la donnée après l'envoi
        }
        self.upload_queue = queue.Queue(maxsize=1)
        self.running = False
        self.worker_thread = None
        self.http_session = requests.Session()

    def start(self):
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(target=self._worker, daemon=True)
            self.worker_thread.start()

    def stop(self):
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=1.0)

    def get_team_info(self, collection, doc_id):
        # Supabase utilise "collection" comme nom de table (ici 'strategies')
        api_url = f"{self.url}/rest/v1/{collection}?id=eq.{doc_id}&select=*"
        try:
            resp = self.http_session.get(api_url, headers=self.headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 0:
                    row = data[0]
                    # On retourne un format similaire à ce que le bridge attend
                    drivers = [d.get('name') for d in row.get('drivers', [])]
                    return {"exists": True, "category": row.get('carCategory', 'Unknown'), "drivers": drivers}
            return {"exists": False}
        except Exception as e:
            print(f"Erreur GET Supabase: {e}")
            return {"exists": False}

    def create_team(self, collection, doc_id, category, drivers_list):
        api_url = f"{self.url}/rest/v1/{collection}"

        formatted_drivers = []
        for d_name in drivers_list:
            d_name = d_name.strip()
            if d_name:
                formatted_drivers.append({
                    "id": d_name,  # Simple ID basé sur le nom
                    "name": d_name,
                    "color": "#3b82f6"  # Bleu par défaut
                })

        # Structure initiale conforme au type TypeScript GameState
        payload = {
            "id": doc_id,
            "carCategory": category,
            "drivers": formatted_drivers,
            "activeDriverId": formatted_drivers[0]["id"] if formatted_drivers else "TBD",
            "createdAt": "NOW()",  # Supabase gère ça si configuré, sinon string
            "currentStint": 0,
            "raceTime": 24 * 3600,
            "sessionTimeRemaining": 24 * 3600,
            "isRaceRunning": False,
            "trackName": "WAITING...",
            "sessionType": "PRE-RACE",
            "weather": "SUNNY",
            "airTemp": 25,
            "trackWetness": 0,
            "fuelCons": 3.65,
            "veCons": 2.5,
            "tankCapacity": 105,
            "raceDurationHours": 24,
            "avgLapTimeSeconds": 210
        }

        try:
            # On utilise POST pour créer, avec "upsert" implicite si configuré, ou POST simple
            # Pour être sûr, on tente un upsert via l'header Prefer: resolution=merge-duplicates
            headers = self.headers.copy()
            headers["Prefer"] = "resolution=merge-duplicates"

            resp = self.http_session.post(api_url, headers=headers, json=payload)
            if resp.status_code in [200, 201, 204]:
                return True
            else:
                print(f"Erreur Création Supabase ({resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            print(f"Exception Création: {e}")
            return False

    def register_driver_if_new(self, collection, doc_id, driver_name):
        # Pour simplifier, on ne gère pas l'ajout dynamique ici pour l'instant
        # car cela requiert de lire le JSONB, l'append et le renvoyer.
        # Le site web le gère mieux.
        return True

    def send_telemetry(self, doc_id, data):
        # On envoie vers la table 'strategies'
        self.send_async("strategies", doc_id, data)

    def send_async(self, collection, doc_id, data):
        if self.upload_queue.full():
            try:
                self.upload_queue.get_nowait()
            except queue.Empty:
                pass
        self.upload_queue.put((collection, doc_id, data))

    def _worker(self):
        while self.running:
            try:
                try:
                    item = self.upload_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                collection, doc_id, data = item
                api_url = f"{self.url}/rest/v1/{collection}?id=eq.{doc_id}"

                # Supabase accepte le JSON direct, pas besoin de formatage complexe comme Firestore
                # On ajoute juste le timestamp côté client pour être sûr
                data['lastPacketTime'] = int(time.time() * 1000)

                try:
                    resp = self.http_session.patch(api_url, headers=self.headers, json=data)
                    if resp.status_code not in [200, 204]:
                        print(f"⚠️ Rejet Supabase ({resp.status_code}): {resp.text}")
                except Exception as e:
                    print(f"Erreur Réseau Worker: {e}")

                self.upload_queue.task_done()
            except Exception as e:
                print(f"Erreur Worker Loop: {e}")
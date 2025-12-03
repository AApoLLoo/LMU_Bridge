import threading
import queue
import requests
import json
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- CONFIGURATION SUPABASE ---
# (Ces valeurs sont tirées de votre configuration précédente)
SUPABASE_URL = "https://gsbvncqsbaakharvhobg.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdzYnZuY3FzYmFha2hhcnZob2JnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQ3NTYzMjksImV4cCI6MjA4MDMzMjMyOX0.ZlD29ZMfU5OjBOeUlHU3KHB1WZVEI81kEQHhQg3FruQ"


class SupabaseConnector:
    def __init__(self):
        self.url = SUPABASE_URL
        self.headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        self.upload_queue = queue.Queue(maxsize=1)
        self.running = False
        self.worker_thread = None

        # --- FIX: GARDE LA CONNEXION OUVERTE (Comme sur Firebase) ---
        self.http_session = requests.Session()
        # On configure le "Keep-Alive" pour éviter de refaire le handshake SSL à chaque requête
        retries = Retry(total=3, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        self.http_session.mount('https://', adapter)
        # ------------------------------------------------------------

    def start(self):
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(target=self._worker, daemon=True)
            self.worker_thread.start()

    def stop(self):
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=1.0)
        # On ferme proprement la session
        self.http_session.close()

    def get_team_info(self, collection, doc_id):
        api_url = f"{self.url}/rest/v1/{collection}?id=eq.{doc_id}&select=*"
        try:
            resp = self.http_session.get(api_url, headers=self.headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 0:
                    row = data[0]
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
                    "id": d_name,
                    "name": d_name,
                    "color": "#3b82f6"
                })

        payload = {
            "id": doc_id,
            "carCategory": category,
            "drivers": formatted_drivers,
            "activeDriverId": formatted_drivers[0]["id"] if formatted_drivers else "TBD",
            "createdAt": "NOW()",
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
        return True

    def send_telemetry(self, doc_id, data):
        self.send_async("strategies", doc_id, data)

    def send_async(self, collection, doc_id, data):
        # Si la file est pleine, on vide le plus vieux paquet pour mettre le plus récent (priorité au temps réel)
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

                # Ajout timestamp client
                data['lastPacketTime'] = int(time.time() * 1000)

                try:
                    # Utilisation de la session persistante
                    resp = self.http_session.patch(api_url, headers=self.headers, json=data)
                    if resp.status_code not in [200, 204]:
                        print(f"⚠️ Rejet Supabase ({resp.status_code}): {resp.text}")
                except Exception as e:
                    print(f"Erreur Réseau Worker: {e}")

                self.upload_queue.task_done()
            except Exception as e:
                print(f"Erreur Worker Loop: {e}")
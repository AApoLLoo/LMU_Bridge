import threading
import queue
import requests
import json
import hashlib
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- CONFIGURATION ---
DEFAULT_API_KEY = "AIzaSyAezT5Np6-v18OBR1ICV3uHoFViQB555sg"
DEFAULT_PROJECT_ID = "le-mans-strat"

def to_firestore_value(value):
    """
    Convertit récursivement n'importe quel type Python en format Firestore REST API.
    Gère les dicts imbriqués, les listes, les nulls, etc.
    """
    if value is None:
        return {"nullValue": None}
    
    if isinstance(value, bool):
        return {"booleanValue": value}
    
    if isinstance(value, int):
        return {"integerValue": str(value)} # Firestore exige des entiers en string
    
    if isinstance(value, float):
        return {"doubleValue": value}
    
    if isinstance(value, str):
        return {"stringValue": value}
    
    if isinstance(value, list) or isinstance(value, tuple):
        return {"arrayValue": {"values": [to_firestore_value(x) for x in value]}}
    
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: to_firestore_value(v) for k, v in value.items()}}}
    
    # Fallback pour tout autre objet (ex: numpy types) -> String
    return {"stringValue": str(value)}

class FirebaseConnector:
    def __init__(self, api_key=DEFAULT_API_KEY, project_id=DEFAULT_PROJECT_ID):
        self.api_key = api_key
        self.project_id = project_id
        self.upload_queue = queue.Queue(maxsize=1) # On garde uniquement le dernier état
        self.running = False
        self.worker_thread = None
        
        self.http_session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        self.http_session.mount('https://', HTTPAdapter(max_retries=retries))

    def start(self):
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(target=self._worker, daemon=True)
            self.worker_thread.start()

    def stop(self):
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)

    def get_team_info(self, collection, doc_id):
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/{collection}/{doc_id}"
        try:
            resp = self.http_session.get(url, params={"key": self.api_key}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                fields = data.get("fields", {})
                
                # Extraction simplifiée
                category = fields.get("carCategory", {}).get("stringValue", "Unknown")
                
                drivers = []
                drivers_raw = fields.get("drivers", {}).get("arrayValue", {}).get("values", [])
                for d in drivers_raw:
                    name = d.get("mapValue", {}).get("fields", {}).get("name", {}).get("stringValue", "")
                    if name: drivers.append(name)
                
                return {"exists": True, "category": category, "drivers": drivers}
            elif resp.status_code == 404:
                return {"exists": False}
        except Exception as e:
            print(f"Erreur GET Firebase: {e}")
        return None

    def create_team(self, collection, doc_id, category, drivers_list):
        """Crée une nouvelle équipe avec la structure complète"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/{collection}/{doc_id}"
        
        formatted_drivers = []
        for d_name in drivers_list:
            d_name = d_name.strip()
            if d_name:
                color_hash = hashlib.md5(d_name.encode()).hexdigest()[:6]
                formatted_drivers.append({
                    "id": d_name, 
                    "name": d_name, 
                    "color": f"#{color_hash}"
                })
        
        initial_data = {
            "id": doc_id,
            "carCategory": category,
            "drivers": formatted_drivers,
            "activeDriverId": formatted_drivers[0]["id"] if formatted_drivers else "TBD",
            "createdAt": "NOW",
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
            "avgLapTimeSeconds": 210,
            "lastPacketTime": 0
        }

        fields = {k: to_firestore_value(v) for k, v in initial_data.items()}
        # Timestamp serveur obligatoire pour createdAt
        fields["createdAt"] = {"timestampValue": "2024-06-15T14:00:00Z"} 
        
        payload = {"fields": fields}
        
        try:
            resp = self.http_session.patch(url, params={"key": self.api_key}, json=payload)
            if resp.status_code == 200:
                return True
            else:
                print(f"Erreur Création ({resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            print(f"Exception Création: {e}")
            return False

    def register_driver_if_new(self, collection, doc_id, driver_name):
        # Simplification: on tente une lecture d'abord
        info = self.get_team_info(collection, doc_id)
        if not info or not info["exists"]: return False
        
        if any(d.lower() == driver_name.lower() for d in info["drivers"]):
            return True # Déjà là
            
        # Ajout (Lecture -> Append -> Ecriture car REST API ne supporte pas arrayUnion natif simple)
        return self._add_driver_unsafe(collection, doc_id, driver_name)

    def _add_driver_unsafe(self, collection, doc_id, driver_name):
        # Cette méthode n'est pas atomique mais suffisante ici
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/{collection}/{doc_id}"
        try:
            # 1. Lire
            resp = self.http_session.get(url, params={"key": self.api_key})
            if resp.status_code != 200: return False
            current_fields = resp.json().get("fields", {})
            
            drivers_array = current_fields.get("drivers", {}).get("arrayValue", {}).get("values", [])
            
            # 2. Modifier
            color_hash = hashlib.md5(driver_name.encode()).hexdigest()[:6]
            new_driver = {
                "mapValue": {
                    "fields": {
                        "id": {"stringValue": driver_name},
                        "name": {"stringValue": driver_name},
                        "color": {"stringValue": f"#{color_hash}"}
                    }
                }
            }
            drivers_array.append(new_driver)
            
            # 3. Ecrire (PATCH partiel)
            payload = {
                "fields": {
                    "drivers": {"arrayValue": {"values": drivers_array}}
                }
            }
            self.http_session.patch(url, params={"key": self.api_key}, json=payload)
            return True
        except:
            return False

    def send_telemetry(self, doc_id, data):
        self.send_async("strategies", doc_id, data)

    def send_async(self, collection, doc_id, data):
        if self.upload_queue.full():
            try: self.upload_queue.get_nowait()
            except queue.Empty: pass
        self.upload_queue.put((collection, doc_id, data))

    def _worker(self):
        while self.running:
            try:
                try: item = self.upload_queue.get(timeout=1.0)
                except queue.Empty: continue
                
                collection, doc_id, data = item
                url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/{collection}/{doc_id}"
                
                # Sécurité: ne jamais écraser la liste des pilotes avec la télémétrie
                if 'drivers' in data: del data['drivers']

                # Conversion robuste
                try:
                    fields = {k: to_firestore_value(v) for k, v in data.items()}
                except Exception as e:
                    print(f"Erreur de conversion JSON: {e}")
                    continue

                payload = {"fields": fields}
                
                try: 
                    resp = self.http_session.patch(url, params={"key": self.api_key}, json=payload)
                    if resp.status_code != 200:
                        # Affichage de l'erreur détaillée pour comprendre pourquoi Firebase refuse
                        print(f"⚠️ Rejet Firebase ({resp.status_code}): {resp.text}")
                except Exception as e:
                    print(f"Erreur Réseau: {e}")
                
                self.upload_queue.task_done()
            except Exception as e:
                print(f"Erreur Worker: {e}")
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
    """Convertit les types Python en types Firestore REST API"""
    if value is None: return {"nullValue": None}
    if isinstance(value, bool): return {"booleanValue": value}
    if isinstance(value, int): return {"integerValue": str(value)}
    if isinstance(value, float): return {"doubleValue": value}
    if isinstance(value, str): return {"stringValue": value}
    if isinstance(value, list): 
        return {"arrayValue": {"values": [to_firestore_value(x) for x in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: to_firestore_value(v) for k, v in value.items()}}}
    return {"stringValue": str(value)}

class FirebaseConnector:
    def __init__(self, api_key=DEFAULT_API_KEY, project_id=DEFAULT_PROJECT_ID):
        self.api_key = api_key
        self.project_id = project_id
        self.upload_queue = queue.Queue(maxsize=1)
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

    # --- MÉTHODES DE GESTION D'ÉQUIPE ---

    def get_team_info(self, collection, doc_id):
        """Vérifie si l'équipe existe et retourne ses infos de base (catégorie, etc.)"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/{collection}/{doc_id}"
        try:
            resp = self.http_session.get(url, params={"key": self.api_key})
            if resp.status_code == 200:
                data = resp.json()
                fields = data.get("fields", {})
                
                # Extraction propre de la catégorie
                category = fields.get("carCategory", {}).get("stringValue", "Unknown")
                
                # Extraction des pilotes pour vérification
                drivers_array = fields.get("drivers", {}).get("arrayValue", {}).get("values", [])
                drivers = []
                for d in drivers_array:
                    drv_map = d.get("mapValue", {}).get("fields", {})
                    name = drv_map.get("name", {}).get("stringValue", "")
                    if name: drivers.append(name)
                
                return {"exists": True, "category": category, "drivers": drivers}
            elif resp.status_code == 404:
                return {"exists": False}
        except Exception as e:
            print(f"Erreur vérification équipe: {e}")
        return None

    def create_team(self, collection, doc_id, category, drivers_list):
        """Crée une nouvelle Line Up complète"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/{collection}/{doc_id}"
        
        # Construction de la liste des pilotes formatée
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
        
        # Données par défaut pour une nouvelle session
        initial_data = {
            "id": doc_id,
            "carCategory": category,
            "drivers": formatted_drivers,
            "activeDriverId": formatted_drivers[0]["id"] if formatted_drivers else "TBD",
            "createdAt": str(requests.utils.quote(str(threading.get_ident()))), # Timestamp simulé ou string
            # Valeurs par défaut du jeu
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

        # Conversion et Envoi
        fields = {k: to_firestore_value(v) for k, v in initial_data.items()}
        # On ajoute un timestamp serveur pour createdAt
        fields["createdAt"] = {"timestampValue": "2024-01-01T00:00:00Z"} # Placeholder, sera écrasé par le serveur si on utilisait transform, mais REST direct c'est plus dur. On met une date fixe ou locale.
        
        payload = {"fields": fields}
        
        try:
            # On utilise PATCH pour créer ou écraser (si on force)
            self.http_session.patch(url, params={"key": self.api_key}, json=payload)
            print(f"✅ Équipe '{doc_id}' créée avec succès (Catégorie: {category}, Pilotes: {len(formatted_drivers)})")
            return True
        except Exception as e:
            print(f"❌ Erreur création équipe: {e}")
            return False

    def register_driver_if_new(self, collection, doc_id, driver_name):
        """Ajoute le pilote s'il n'est pas déjà dans la liste"""
        team_info = self.get_team_info(collection, doc_id)
        if not team_info or not team_info["exists"]:
            return False # L'équipe doit exister pour rejoindre
            
        current_drivers = team_info.get("drivers", [])
        
        # Si le pilote existe déjà, on ne fait rien
        if any(d.lower() == driver_name.lower() for d in current_drivers):
            return True 

        # Sinon on ajoute
        print(f"➕ Ajout du pilote '{driver_name}' à la Line Up...")
        # Note: Pour ajouter proprement à un tableau via REST sans écraser, c'est complexe.
        # Ici on fait : Lecture (déjà faite dans get_team_info) -> Ajout local -> Écriture complète
        # C'est acceptable pour ce cas d'usage (pas de concurrence massive à la milliseconde).
        
        # On reconstruit la liste complète d'objets drivers (car get_team_info ne renvoyait que les noms)
        # Il faut refaire un GET complet ou améliorer get_team_info.
        # Pour simplifier, on utilise la méthode register_driver que j'avais faite avant :
        return self.register_driver(collection, doc_id, driver_name)

    def register_driver(self, collection, doc_id, driver_name):
        """Logique d'ajout (reprise et adaptée)"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/{collection}/{doc_id}"
        try:
            resp = self.http_session.get(url, params={"key": self.api_key})
            if resp.status_code != 200: return False
            
            data = resp.json()
            fields = data.get("fields", {})
            drivers_array = fields.get("drivers", {}).get("arrayValue", {}).get("values", [])
            
            current_driver_objs = []
            for d in drivers_array:
                current_driver_objs.append(d) # On garde le format brut Firestore
            
            # Création du nouveau driver format Firestore
            color_hash = hashlib.md5(driver_name.encode()).hexdigest()[:6]
            new_driver_fs = {
                "mapValue": {
                    "fields": {
                        "id": {"stringValue": driver_name},
                        "name": {"stringValue": driver_name},
                        "color": {"stringValue": f"#{color_hash}"}
                    }
                }
            }
            
            current_driver_objs.append(new_driver_fs)
            
            payload = {
                "fields": {
                    "drivers": {"arrayValue": {"values": current_driver_objs}}
                }
            }
            self.http_session.patch(url, params={"key": self.api_key}, json=payload)
            return True
        except Exception:
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
                
                if 'drivers' in data: del data['drivers'] # Sécurité
                
                fields = {k: to_firestore_value(v) for k, v in data.items()}
                payload = {"fields": fields}
                try: self.http_session.patch(url, params={"key": self.api_key}, json=payload)
                except Exception: pass
                self.upload_queue.task_done()
            except Exception: pass
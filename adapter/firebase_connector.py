import threading
import queue
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- CONFIGURATION (Extraite de bridge.py) ---
# Vous pouvez les passer en arguments ou les laisser ici par défaut
DEFAULT_API_KEY = "AIzaSyAezT5Np6-v18OBR1ICV3uHoFViQB555sg"
DEFAULT_PROJECT_ID = "le-mans-strat"

def to_firestore_value(value):
    """Convertit les types Python en types Firestore REST API"""
    if value is None: return {"nullValue": None}
    if isinstance(value, bool): return {"booleanValue": value}
    if isinstance(value, int): return {"integerValue": str(value)}
    if isinstance(value, float): return {"doubleValue": value}
    if isinstance(value, str): return {"stringValue": value}
    # Fallback pour les autres types
    return {"stringValue": str(value)}

class FirebaseConnector:
    def __init__(self, api_key=DEFAULT_API_KEY, project_id=DEFAULT_PROJECT_ID):
        self.api_key = api_key
        self.project_id = project_id
        
        # File d'attente pour ne pas bloquer le thread principal (maxsize=1 pour avoir toujours la dernière data)
        self.upload_queue = queue.Queue(maxsize=1)
        
        self.running = False
        self.worker_thread = None
        
        # Session HTTP persistante avec politique de retry
        self.http_session = requests.Session()
        retries = Retry(
            total=3, 
            backoff_factor=0.1, 
            status_forcelist=[500, 502, 503, 504]
        )
        self.http_session.mount('https://', HTTPAdapter(max_retries=retries))

    def start(self):
        """Démarre le thread d'envoi en arrière-plan"""
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(target=self._worker, daemon=True)
            self.worker_thread.start()
            print("Firebase Connector: Started")

    def stop(self):
        """Arrête le thread d'envoi"""
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
        print("Firebase Connector: Stopped")

    def send_async(self, collection, doc_id, data):
        """
        Met les données en file d'attente pour envoi.
        Si la file est pleine (donnée précédente non envoyée), on la vide pour mettre la plus récente.
        """
        if self.upload_queue.full():
            try:
                self.upload_queue.get_nowait()
            except queue.Empty:
                pass
        self.upload_queue.put((collection, doc_id, data))

    def send_telemetry(self, driver_name, data):
        """Alias pour la compatibilité avec votre code précédent si nécessaire"""
        # Par défaut on suppose que driver_name est l'ID du document dans une collection "strategies"
        # Ajustez 'strategies' selon votre structure souhaitée
        self.send_async("strategies", driver_name, data)

    def _worker(self):
        """Boucle principale du thread d'envoi"""
        while self.running:
            try:
                # Timeout permet de vérifier self.running régulièrement même si pas de données
                try:
                    item = self.upload_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                collection, doc_id, data = item
                
                # Construction de l'URL Firestore REST
                url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/{collection}/{doc_id}"
                
                # Conversion des données au format attendu par l'API REST Firestore
                fields = {k: to_firestore_value(v) for k, v in data.items()}
                payload = {"fields": fields}
                
                try:
                    # Envoi PATCH pour mettre à jour ou créer
                    self.http_session.patch(url, params={"key": self.api_key}, json=payload)
                except Exception as e:
                    print(f"Firebase Error during request: {e}")
                
                self.upload_queue.task_done()
                
            except Exception as e:
                print(f"Firebase Worker Critical Error: {e}")
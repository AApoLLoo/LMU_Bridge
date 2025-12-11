import socketio
import time


class SocketConnector:
    def __init__(self, server_ip, port=5000):
        # L'adresse de votre VPS
        self.server_url = f"http://{server_ip}:{port}"
        self.sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)
        self.is_connected = False

        @self.sio.event
        def connect():
            print("✅ SocketIO: Connecté !")
            self.is_connected = True

        @self.sio.event
        def disconnect():
            print("❌ SocketIO: Déconnecté")
            self.is_connected = False

    def connect(self):
        if self.sio.connected:
            self.is_connected = True
            return

        try:
            print(f"Tentative de connexion au VPS ({self.server_url})...")
            self.sio.connect(self.server_url, wait_timeout=5)
            self.is_connected = True
            print("✅ Connecté au serveur Relais !")
        except Exception as e:
            if "Already connected" in str(e):
                self.is_connected = True
            else:
                print(f"⚠️ Erreur de connexion VPS : {e}")
                self.is_connected = False

    # --- NOUVELLE FONCTION POUR CRÉER LA LINEUP EN BDD ---
    def register_lineup(self, team_id, driver_name):
        """Envoie une demande de création/enregistrement d'équipe au VPS"""
        if not self.is_connected and not self.sio.connected:
            self.connect()

        payload = {
            "teamId": team_id,
            "creator": driver_name,
            "timestamp": time.time(),
            # On peut ajouter des infos par défaut ici
            "carCategory": "Unknown",
            "status": "CREATED"
        }

        try:
            # Le serveur VPS doit écouter l'événement 'create_team'
            self.sio.emit('create_team', payload)
            print(f"🆕 Demande de création de lineup envoyée pour : {team_id}")
        except Exception as e:
            print(f"❌ Erreur lors de la création de la lineup : {e}")

    def send_data(self, data):
        if not self.is_connected and not self.sio.connected:
            self.connect()
            if not self.is_connected: return

        try:
            self.sio.emit('telemetry_data', data)
        except Exception as e:
            print(f"Erreur d'envoi : {e}")

    def send_telemetry_history(self, data):
        if not self.is_connected and not self.sio.connected:
            self.connect()
            if not self.is_connected: return

        try:
            self.sio.emit('telemetry_history', data)
            print(f"📦 Historique Tour {data.get('lap_number')} envoyé au serveur.")
        except Exception as e:
            print(f"⚠️ Erreur envoi historique : {e}")

    def disconnect(self):
        if self.sio.connected:
            self.sio.disconnect()
import socketio
import time

class SocketConnector:
    def __init__(self, server_url, port=5000):
        if server_url.startswith("http"):
            self.server_url = server_url
        else:
            self.server_url = f"http://{server_url}:{port}"

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
            self.sio.connect(
                self.server_url,
                wait_timeout=10,
            )
            self.is_connected = True
            print("✅ Connecté au serveur Relais !")
        except Exception as e:
            if "Already connected" in str(e):
                self.is_connected = True
            else:
                print(f"⚠️ Erreur de connexion VPS : {e}")
                self.is_connected = False

    # MODIFICATION ICI : On ajoute history_id et session_type
    def register_lineup(self, team_id, driver_name, history_id, session_type):
        if not self.is_connected and not self.sio.connected:
            self.connect()

        payload = {
            "teamId": team_id,          # ID pour le LIVE (ex: "baliverne")
            "historyId": history_id,    # ID pour l'ANALYSE (ex: "baliverne_Race_123456")
            "creator": driver_name,
            "sessionType": session_type,
            "timestamp": time.time(),
            "carCategory": "Unknown",
            "status": "CREATED"
        }

        try:
            self.sio.emit('create_team', payload)
            print(f"🆕 Session Historique créée : {history_id} ({session_type})")
        except Exception as e:
            print(f"❌ Erreur création session : {e}")

    def send_data(self, data):
        if not self.is_connected and not self.sio.connected:
            self.connect()
            if not self.is_connected: return

        try:
            self.sio.emit('telemetry_data', data)
        except Exception as e:
            print(f"Erreur d'envoi : {e}")

    def disconnect(self):
        if self.sio.connected:
            self.sio.disconnect()
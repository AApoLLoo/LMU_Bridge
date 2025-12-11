import socketio
import time


class SocketConnector:
    def __init__(self, server_url, port=5000):
        # Correction : On gère le cas où l'URL contient déjà "http" (Ngrok) ou si c'est juste une IP
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
            # AJOUT CRITIQUE : Ce header permet de passer la sécurité Ngrok
            self.sio.connect(
                self.server_url,
                wait_timeout=5,
                headers={"ngrok-skip-browser-warning": "true"}
            )
            self.is_connected = True
            print("✅ Connecté au serveur Relais !")
        except Exception as e:
            if "Already connected" in str(e):
                self.is_connected = True
            else:
                print(f"⚠️ Erreur de connexion VPS : {e}")
                self.is_connected = False

    def register_lineup(self, team_id, driver_name):
        if not self.is_connected and not self.sio.connected:
            self.connect()

        payload = {
            "teamId": team_id,
            "creator": driver_name,
            "timestamp": time.time(),
            "carCategory": "Unknown",
            "status": "CREATED"
        }

        try:
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

    def disconnect(self):
        if self.sio.connected:
            self.sio.disconnect()
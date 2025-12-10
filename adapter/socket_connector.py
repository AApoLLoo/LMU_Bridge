import socketio
import time

class SocketConnector:
    def __init__(self, server_ip, port=5000):
        # L'adresse de votre VPS
        self.server_url = f"http://{server_ip}:{port}"
        self.sio = socketio.Client()
        self.is_connected = False

    def connect(self):
        if self.is_connected:
            return

        try:
            print(f"Tentative de connexion au VPS ({self.server_url})...")
            self.sio.connect(self.server_url)
            self.is_connected = True
            print("✅ Connecté au serveur Relais !")
        except Exception as e:
            print(f"⚠️ Erreur de connexion VPS : {e}")
            self.is_connected = False

    def send_data(self, data):
        # Si on n'est pas connecté, on réessaie
        if not self.is_connected:
            self.connect()
            return

        try:
            # Envoi des données (le nom 'telemetry_data' doit correspondre au server.py)
            self.sio.emit('telemetry_data', data)
        except Exception as e:
            print(f"Erreur d'envoi : {e}")
            self.is_connected = False
            # On tente de se déconnecter proprement pour reconnecter au prochain tour
            try:
                self.sio.disconnect()
            except:
                pass
import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext
import threading
import time
import sys
import os
import logging
import uuid  # Ajouté pour le Recorder
import requests
# --- FIX IMPORT ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# --- IMPORT DES CONNECTEURS ---
try:
    from adapter.rf2_connector import RF2Info
    from adapter.restapi_connector import RestAPIInfo
    from adapter.rf2_data import (
        TelemetryData, ScoringData, RulesData, ExtendedData,
        PitInfoData, WeatherData, PitStrategyData, Vehicle
    )
    # On utilise le SocketConnector vers le VPS
    from adapter.socket_connector import SocketConnector
except ImportError as e:
    print(f"Erreur d'import critique : {e}")
    sys.exit(1)

COLORS = {
    "bg": "#0f172a", "panel": "#1e293b", "input": "#334155",
    "text": "#f8fafc", "accent": "#6366f1", "success": "#10b981",
    "danger": "#ef4444", "warning": "#eab308", "debug": "#a855f7"
}

# --- CONFIGURATION VPS ---
VPS_IP = "51.178.87.25"  # Votre IP OVH


def normalize_id(name):
    import re
    safe = re.sub(r'[^a-zA-Z0-9]+', '-', name).strip('-').lower()
    return safe


class MockParentAPI:
    def __init__(self):
        self.identifier = "LMU"
        self.isActive = True


# --- CALCULATEUR DE CONSOMMATION ---
class ConsumptionTracker:
    def __init__(self, log_func):
        self.log = log_func
        self.reset()

    def reset(self):
        self.last_lap = -1
        self.fuel_start = -1.0
        self.ve_start = -1.0

        self.fuel_last = 0.0
        self.fuel_avg = 0.0
        self.fuel_samples = 0

        self.ve_last = 0.0
        self.ve_avg = 0.0
        self.ve_samples = 0

    def update(self, current_lap, current_fuel, current_ve, in_pits):
        # 1. Initialisation ou Restart Session
        if self.last_lap == -1 or current_lap < self.last_lap:
            self.last_lap = current_lap
            self.fuel_start = current_fuel
            self.ve_start = current_ve
            if current_lap < self.last_lap and self.last_lap != -1:
                self.log("🔄 Session redémarrée : Reset conso")
            return

        # 2. Passage de ligne (Nouveau tour)
        if current_lap > self.last_lap:
            fuel_delta = self.fuel_start - current_fuel
            ve_delta = self.ve_start - current_ve

            # On ignore les tours où on a ravitaillé (delta négatif) ou si on est dans les stands
            if not in_pits and fuel_delta > 0.01:
                # Mise à jour Fuel
                self.fuel_last = fuel_delta
                self.fuel_samples += 1
                self.fuel_avg = self.fuel_avg + (fuel_delta - self.fuel_avg) / self.fuel_samples

                self.log(f"🏁 Tour {self.last_lap} terminé. Conso: {fuel_delta:.2f}L")

                # Mise à jour VE (Seulement si cohérent)
                if ve_delta > 0.01:
                    self.ve_last = ve_delta
                    self.ve_samples += 1
                    self.ve_avg = self.ve_avg + (ve_delta - self.ve_avg) / self.ve_samples

            elif in_pits:
                self.log(f"🛑 Tour {self.last_lap} ignoré (Stands)")

            # Reset pour le prochain tour
            self.last_lap = current_lap
            self.fuel_start = current_fuel
            self.ve_start = current_ve

    def get_stats(self):
        return {
            "lastLapFuelConsumption": round(self.fuel_last, 2),
            "averageConsumptionFuel": round(self.fuel_avg, 2),
            "lastLapVEConsumption": round(self.ve_last, 2),
            "averageConsumptionVE": round(self.ve_avg, 2)
        }


# --- GESTION DES LOGS GUI ---
class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)

        def append():
            try:
                self.text_widget.config(state=tk.NORMAL)
                self.text_widget.insert(tk.END, "• " + msg + '\n')
                self.text_widget.see(tk.END)
                self.text_widget.config(state=tk.DISABLED)
            except:
                pass

        self.text_widget.after(0, append)


# --- AJOUT DU RECORDER (INTEGRÉ DEPUIS VOTRE AUTRE VERSION) ---
class TelemetryRecorder:
    def __init__(self, api_url, team_id):
        self.api_url = api_url
        self.team_id = team_id
        self.buffer = []
        self.current_lap = -1
        self.driver_name = "Unknown"
        self.last_dist = -1

    def update(self, lap_number, vehicle_idx, telemetry, vehicle, scoring):
        # 1. Gestion du changement de tour (Envoi)
        if self.current_lap != -1 and lap_number > self.current_lap:
            # Récupération du temps du dernier tour via l'objet Scoring
            last_lap_time = 0
            if hasattr(scoring, 'get_vehicle_scoring'):
                v_data = scoring.get_vehicle_scoring(vehicle_idx)
                last_lap_time = v_data.get('last_lap', 0)

            self.flush_lap(self.current_lap, last_lap_time)
            self.buffer = []  # Reset buffer
            self.last_dist = -1

        self.current_lap = lap_number

        # 2. Calcul robuste de la distance (Telemetry OU Scoring)
        dist = 0
        # Essai 1 : Via Télémétrie directe
        if hasattr(telemetry, 'lap_distance'):
            dist = telemetry.lap_distance(vehicle_idx)

        # Essai 2 : Fallback sur Scoring si Télémétrie échoue (0 ou None)
        if (dist == 0 or dist is None) and hasattr(scoring, 'get_vehicle_scoring'):
            v_data = scoring.get_vehicle_scoring(vehicle_idx)
            dist = v_data.get('lap_dist', 0)

        # 3. Enregistrement des points
        # On filtre si on est à l'arrêt ou si on n'a pas assez avancé (< 2m)
        if vehicle.speed(vehicle_idx) > 1:
            if self.last_dist == -1 or abs(dist - self.last_dist) > 2.0:
                self.buffer.append({
                    "d": round(dist, 1),
                    "s": round(vehicle.speed(vehicle_idx), 1),
                    "t": round(telemetry.input_throttle(vehicle_idx) * 100, 0),
                    "b": round(telemetry.input_brake(vehicle_idx) * 100, 0),
                    "g": telemetry.gear(vehicle_idx),
                    "w": round(telemetry.input_steering(vehicle_idx), 2),
                    "f": round(telemetry.fuel_level(vehicle_idx), 2),
                    "ve": round(telemetry.virtual_energy(vehicle_idx), 1),
                    "tw": round(telemetry.tire_wear(vehicle_idx)[0], 1)
                })
                self.last_dist = dist

    def flush_lap(self, lap_num, lap_time):
        # On ignore les tours incomplets ou trop courts (< 50 points)
        if not self.buffer or len(self.buffer) < 50:
            return

        payload = {
            "sessionId": self.team_id,
            "lapNumber": lap_num,
            "driver": self.driver_name,
            "lapTime": lap_time,
            "samples": self.buffer
        }

        def send():
            try:
                # Timeout court pour ne pas bloquer
                requests.post(f"{self.api_url}/api/telemetry/lap", json=payload, timeout=2)
                print(f"💾 Télémétrie Tour {lap_num} envoyée ({len(self.buffer)} points)")
            except Exception as e:
                print(f"⚠️ Erreur envoi tour: {e}")

        threading.Thread(target=send, daemon=True).start()

class BridgeLogic:
    def __init__(self, log_callback, status_callback):
        self.log = log_callback
        self.set_status = status_callback
        self.running = False
        self.debug_mode = False
        self.connector = None
        self.rf2_info = None
        self.rest_info = None
        self.thread = None
        self.line_up_name = ""
        self.team_id = ""
        self.driver_pseudo = ""
        self.tracker = ConsumptionTracker(self.log)
        self.session_id = 0
        self.recorder = None  # Init du recorder

    def set_debug(self, enabled):
        self.debug_mode = enabled
        self.log(f"🔧 Mode Debug {'ACTIVÉ' if enabled else 'DÉSACTIVÉ'}")

    def connect_vps(self):
        """Remplace l'ancienne connexion DB"""
        if self.connector and self.connector.is_connected:
            return True

        try:
            self.connector = SocketConnector(VPS_IP)
            self.connector.connect()
            return True
        except Exception as e:
            self.log(f"❌ Erreur Connexion VPS: {e}")
            return False

    def check_team(self, name):
        # En mode VPS, on fait confiance à l'utilisateur pour l'ID d'équipe
        if not self.connector: self.connect_vps()
        return {"exists": True, "category": "VPS Managed"}

    def create_team(self, name, category, drivers):
        # En mode VPS, la création se fait implicitement ou via le site
        if not self.connector: self.connect_vps()
        return True

    def start_loop(self, line_up_name, driver_pseudo):
        self.session_id += 1
        current_session_id = self.session_id

        self.line_up_name = line_up_name
        self.team_id = normalize_id(line_up_name)
        self.driver_pseudo = driver_pseudo
        self.running = True
        self.tracker.reset()

        # Connexion assurée au VPS
        if not self.connector:
            self.connect_vps()

        # --- AJOUT ICI : Enregistrement de la Lineup en BDD ---
        if self.connector and self.connector.is_connected:
            self.log(f"💾 Enregistrement de l'équipe '{self.team_id}'...")
            self.connector.register_lineup(self.team_id, self.driver_pseudo)
        # ------------------------------------------------------

        self.thread = threading.Thread(target=self._run, args=(current_session_id,), daemon=True)
        self.thread.start()

    def stop(self):
        self.log("🛑 Demande d'arrêt...")
        self.running = False
        self.session_id += 1

        try:
            if self.rf2_info:
                self.log("🧽 Arrêt rF2...")
                self.rf2_info.stop()
        except:
            pass

        try:
            if self.rest_info: self.rest_info.stop()
        except:
            pass

        try:
            if self.connector:
                # Optionnel : déconnexion socket propre
                # self.connector.sio.disconnect()
                pass
        except:
            pass

        if self.thread and self.thread.is_alive():
            self.log("⌛ Attente arrêt thread...")
            try:
                self.thread.join(timeout=3.0)
            except:
                pass

        self.rf2_info = None
        self.rest_info = None
        self.thread = None

        self.set_status("STOPPED", COLORS["danger"])
        self.log("⏹️ Bridge arrêté.")

    def _run(self, my_session_id):
        self.log(f"🚀 Démarrage session #{my_session_id} pour '{self.line_up_name}'")
        self.set_status("WAITING GAME...", COLORS["warning"])

        pit_strategy = PitStrategyData(port=6397)
        mock_parent = MockParentAPI()

        self.rest_info = RestAPIInfo(mock_parent)
        self.rest_info.setConnection({
            "url_host": "localhost",
            "url_port_lmu": 6397,
            "connection_timeout": 1.0,
            "connection_retry": 3,
            "connection_retry_delay": 2,
            "restapi_update_interval": 50,
            "enable_restapi_access": True,
            "enable_weather_info": True,
            "enable_session_info": True,
            "enable_garage_setup_info": True,
            "enable_vehicle_info": True,
            "enable_energy_remaining": True
        })

        telemetry = scoring = rules = extended = pit_info = weather = vehicle_helper = None
        last_game_check = 0
        last_update_time = 0
        UPDATE_RATE = 0.05  # 20 fois par seconde (rapide pour le Live Timing)

        # --- INIT RECORDER & UUID SESSION ---
        # On génère un ID unique pour cette session de jeu
        session_uuid = str(uuid.uuid4())

        # On initialise le recorder avec la méthode d'envoi d'historique du connecteur
        # NB: On suppose que send_telemetry_history existe dans votre SocketConnector (comme dans l'autre version)
        base_url = f"http://{VPS_IP}:5000"
        self.recorder = TelemetryRecorder(base_url, self.team_id)

        while self.running:
            if self.session_id != my_session_id: break

            current_time = time.time()

            if self.rf2_info is None:
                if not self.running: break
                if current_time - last_game_check > 5.0:
                    try:
                        self.rf2_info = RF2Info()
                        self.rf2_info.start()
                        self.rest_info.start()
                        self.log("🎮 Jeu détecté ! Connexion établie.")
                        self.set_status("CONNECTED (GAME)", COLORS["success"])

                        telemetry = TelemetryData(self.rf2_info, self.rest_info)
                        scoring = ScoringData(self.rf2_info)
                        rules = RulesData(self.rf2_info)
                        extended = ExtendedData(self.rf2_info)
                        pit_info = PitInfoData(self.rf2_info)
                        weather = WeatherData(self.rf2_info)
                        vehicle_helper = Vehicle(self.rf2_info)
                        self.tracker.reset()
                    except Exception as e:
                        try:
                            if self.rf2_info: self.rf2_info.stop()
                        except:
                            pass
                        self.rf2_info = None
                    last_game_check = current_time
                time.sleep(0.1)
                continue

            try:
                if not self.running: break
                status = vehicle_helper.get_local_driver_status()

                # Mise à jour des infos contextuelles pour le recorder
                if self.rf2_info and scoring:
                    self.recorder.driver_name = status.get('driver_name', 'Unknown')
                    self.recorder.track_name = scoring.track_name() if scoring else "Unknown"

                if status['is_driving'] and (current_time - last_update_time > UPDATE_RATE):
                    idx = status['vehicle_index']
                    game_driver = status['driver_name']
                    curr_fuel = telemetry.fuel_level(idx)
                    curr_ve = telemetry.virtual_energy(idx)
                    curr_lap = telemetry.lap_number(idx)

                    # --- LOGIQUE RECORDER ---
                    try:
                        self.recorder.update(curr_lap, idx, telemetry, vehicle_helper, scoring)
                    except Exception as rec_err:
                        self.log(f"⚠️ Erreur Recorder: {rec_err}")
                    # ------------------------

                    # --- Météo ---
                    forecast_data = []
                    try:
                        sess_type = scoring.session_type()
                        raw_forecast = None
                        if sess_type < 5:
                            raw_forecast = self.rest_info.telemetry.forecastPractice
                        elif sess_type < 9:
                            raw_forecast = self.rest_info.telemetry.forecastQualify
                        else:
                            raw_forecast = self.rest_info.telemetry.forecastRace

                        if raw_forecast:
                            for node in raw_forecast:
                                r_chance = max(0.0, getattr(node, "rain_chance", 0.0))
                                sky = getattr(node, "sky_type", 0)
                                temp_val = getattr(node, "temperature", 0.0)
                                forecast_data.append(
                                    {"rain": r_chance / 100.0, "cloud": min(max(sky, 0) / 4.0, 1.0), "temp": temp_val})
                    except:
                        pass

                    # --- Conso ---
                    try:
                        scor_veh = scoring.get_vehicle_scoring(idx)
                        in_pits = (scor_veh.get('in_pits', 0) == 1)
                    except:
                        in_pits = False

                    self.tracker.update(curr_lap, curr_fuel, curr_ve, in_pits)
                    stats = self.tracker.get_stats()

                    # --- Construction Payload ---
                    payload = {
                        "teamId": self.team_id,
                        "driverName": game_driver,
                        "activeDriverId": self.driver_pseudo,
                        "lastLapFuelConsumption": stats["lastLapFuelConsumption"],
                        "averageConsumptionFuel": stats["averageConsumptionFuel"],
                        "lastLapVEConsumption": stats["lastLapVEConsumption"],
                        "averageConsumptionVE": stats["averageConsumptionVE"],
                        "weatherForecast": forecast_data,
                        "telemetry": {
                            "gear": telemetry.gear(idx),
                            "rpm": telemetry.rpm(idx),
                            "speed": vehicle_helper.speed(idx),
                            "fuel": curr_fuel,
                            "fuelCapacity": telemetry.fuel_capacity(idx),
                            "inputs": {"thr": telemetry.input_throttle(idx), "brk": telemetry.input_brake(idx),
                                       "clt": telemetry.input_clutch(idx), "str": telemetry.input_steering(idx)},
                            "temps": {"oil": telemetry.temp_oil(idx), "water": telemetry.temp_water(idx)},
                            "tires": {"temp": telemetry.tire_temps(idx), "press": telemetry.tire_pressure(idx),
                                      "wear": telemetry.tire_wear(idx), "brake_wear": telemetry.brake_wear(idx),
                                      "type": telemetry.surface_type(idx),
                                      "brake_temp": telemetry.brake_temp(idx),
                                      "compounds": telemetry.tire_compound_name(idx)},
                            "electric": telemetry.electric_data(idx),
                            "virtual_energy": curr_ve,
                            "max_virtual_energy": 100.0
                        },
                        "scoring": {
                            "track": scoring.track_name(),
                            "time": scoring.time_info(),
                            "flags": scoring.flag_state(),
                            "weather": scoring.weather_env(),
                            "vehicles": [scoring.get_vehicle_scoring(i) for i in range(scoring.vehicle_count())],
                            "vehicle_data": scor_veh
                        },
                        "rules": {
                            "sc": rules.sc_info(),
                            "yellow": rules.yellow_flag(),
                            "my_status": rules.participant_status(idx)
                        },
                        "pit": {
                            "menu": pit_info.menu_status(),
                            "strategy": pit_strategy.pit_estimate()
                        },
                        "weather_det": weather.info(),
                        "extended": {
                            "physics": extended.physics_options(),
                            "pit_limit": extended.pit_limit()
                        }
                    }

                    # --- Envoi vers VPS ---
                    if self.running and self.session_id == my_session_id:
                        if self.connector:
                            self.connector.send_data(payload)

                        last_update_time = current_time
                        self.set_status(f"LIVE ({game_driver})", COLORS["accent"])

                        if self.debug_mode:
                            bw = telemetry.brake_wear(idx)
                            self.log(f"📤 Sent VPS | Spd: {payload['telemetry']['speed']:.0f}")

                elif not status['is_driving']:
                    self.set_status("IDLE (NOT DRIVING)", "#94a3b8")
                    time.sleep(0.5)

            except Exception as e:
                if self.running and self.session_id == my_session_id:
                    self.log(f"⚠️ Erreur boucle: {e}")
                    time.sleep(1.0)
                    try:
                        if self.rf2_info: self.rf2_info.stop()
                    except:
                        pass
                    self.rf2_info = None
                    self.set_status("RECONNECTING...", COLORS["warning"])
                else:
                    break

            time.sleep(0.01)


class BridgeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LMU Telemetry Bridge (VPS)")
        self.root.geometry("500x700")
        self.root.configure(bg=COLORS["bg"])
        self.root.resizable(False, False)

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))

        header_frame = tk.Frame(root, bg=COLORS["bg"])
        header_frame.pack(pady=20)
        tk.Label(header_frame, text="LE MANS", font=("Segoe UI", 24, "bold italic"), bg=COLORS["bg"], fg="white").pack()
        tk.Label(header_frame, text="CLOUD BRIDGE", font=("Segoe UI", 10, "bold"), bg=COLORS["bg"],
                 fg=COLORS["accent"]).pack()

        form_frame = tk.Frame(root, bg=COLORS["panel"], padx=20, pady=20)
        form_frame.pack(padx=30, fill="x", pady=10)

        tk.Label(form_frame, text="NOM DE LA LINE UP (ID)", bg=COLORS["panel"], fg="#94a3b8",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.ent_lineup = tk.Entry(form_frame, bg=COLORS["input"], fg="white", font=("Segoe UI", 12), relief="flat",
                                   insertbackground="white")
        self.ent_lineup.pack(fill="x", pady=(5, 15), ipady=5)

        tk.Label(form_frame, text="VOTRE PSEUDO", bg=COLORS["panel"], fg="#94a3b8", font=("Segoe UI", 8, "bold")).pack(
            anchor="w")
        self.ent_pseudo = tk.Entry(form_frame, bg=COLORS["input"], fg="white", font=("Segoe UI", 12), relief="flat",
                                   insertbackground="white")
        self.ent_pseudo.pack(fill="x", pady=(5, 20), ipady=5)

        self.btn_start = tk.Button(form_frame, text="CONNEXION AU CLOUD", bg=COLORS["accent"], fg="white",
                                   font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2", command=self.on_start)
        self.btn_start.pack(fill="x", ipady=8)

        self.btn_stop = tk.Button(form_frame, text="DÉCONNEXION", bg=COLORS["danger"], fg="white",
                                  font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2", command=self.on_stop)

        self.lbl_status = tk.Label(root, text="READY", bg=COLORS["bg"], fg="#94a3b8", font=("Consolas", 10, "bold"))
        self.lbl_status.pack(pady=5)

        self.var_debug = tk.BooleanVar(value=False)
        self.chk_debug = tk.Checkbutton(root, text="Debug Mode", variable=self.var_debug, bg=COLORS["bg"], fg="#94a3b8",
                                        selectcolor=COLORS["panel"], activebackground=COLORS["bg"],
                                        activeforeground="white",
                                        font=("Segoe UI", 9), command=self.toggle_debug)
        self.chk_debug.pack(pady=0)

        self.txt_log = scrolledtext.ScrolledText(root, bg="#020408", fg="#22c55e", font=("Consolas", 9), height=12,
                                                 relief="flat")
        self.txt_log.pack(fill="both", expand=True, padx=30, pady=(10, 30))
        self.txt_log.config(state=tk.DISABLED)

        handler = TextHandler(self.txt_log)
        formatter = logging.Formatter('%(message)s')
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

        self.logic = BridgeLogic(self.log, self.set_status)

    def log(self, msg):
        self.root.after(0, lambda: self._log_safe(msg))

    def _log_safe(self, msg):
        try:
            self.txt_log.config(state=tk.NORMAL)
            if float(self.txt_log.index('end')) > 500:
                self.txt_log.delete('1.0', '100.0')
            self.txt_log.insert(tk.END, f"> {msg}\n")
            self.txt_log.see(tk.END)
            self.txt_log.config(state=tk.DISABLED)
        except Exception:
            pass

    def set_status(self, text, color):
        self.root.after(0, lambda: self.lbl_status.config(text=text, fg=color))

    def toggle_debug(self):
        self.logic.set_debug(self.var_debug.get())

    def on_start(self):
        lineup = self.ent_lineup.get().strip()
        pseudo = self.ent_pseudo.get().strip()
        if not lineup or not pseudo:
            messagebox.showwarning("Info", "Remplissez les champs.")
            return
        self.btn_start.config(state=tk.DISABLED, text="CONNEXION...")
        threading.Thread(target=self._check_and_start, args=(lineup, pseudo)).start()

    def _check_and_start(self, lineup, pseudo):
        # On tente de se connecter au VPS
        if self.logic.connect_vps():
            self.log(f"☁️ Connecté au VPS !")
            self._activate_ui(True)
            self.logic.start_loop(lineup, pseudo)
        else:
            self.log("❌ Échec connexion VPS.")
            self.root.after(0, lambda: self.btn_start.config(state=tk.NORMAL, text="CONNEXION AU CLOUD"))

    def _activate_ui(self, active):
        if active:
            self.ent_lineup.config(state=tk.DISABLED)
            self.ent_pseudo.config(state=tk.DISABLED)
            self.btn_start.pack_forget()
            self.btn_stop.pack(fill="x", ipady=8)
        else:
            self.ent_lineup.config(state=tk.NORMAL)
            self.ent_pseudo.config(state=tk.NORMAL)
            self.btn_stop.pack_forget()
            self.btn_start.pack(fill="x", ipady=8)
            self.btn_start.config(state=tk.NORMAL, text="CONNEXION AU CLOUD")

    def on_stop(self):
        self.btn_stop.config(text="ARRÊT...", state=tk.DISABLED)
        threading.Thread(target=self._async_stop_process, daemon=True).start()

    def _async_stop_process(self):
        try:
            self.logic.stop()
        except:
            pass
        finally:
            self.root.after(0, lambda: self._activate_ui(False))
            self.root.after(0, lambda: self.log("✅ Déconnecté."))


if __name__ == "__main__":
    root = tk.Tk()
    app = BridgeApp(root)
    root.mainloop()
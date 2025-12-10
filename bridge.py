import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext
import threading
import time
import sys
import os
import logging

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
    from adapter.supabase_connector import SupabaseConnector
except ImportError as e:
    print(f"Erreur d'import critique : {e}")
    sys.exit(1)

COLORS = {
    "bg": "#0f172a", "panel": "#1e293b", "input": "#334155",
    "text": "#f8fafc", "accent": "#6366f1", "success": "#10b981",
    "danger": "#ef4444", "warning": "#eab308", "debug": "#a855f7"
}


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
                self.log(f"🛑 Tour {self.last_lap} ignoré (Stands) | Weather: {scoring.weather_env()}")

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


class BridgeLogic:
    def __init__(self, log_callback, status_callback):
        self.log = log_callback
        self.set_status = status_callback
        self.running = False
        self.debug_mode = False
        self.fb = None
        self.rf2_info = None
        self.rest_info = None
        self.thread = None
        self.line_up_name = ""
        self.team_id = ""
        self.driver_pseudo = ""
        self.tracker = ConsumptionTracker(self.log)
        self.session_id = 0

    def set_debug(self, enabled):
        self.debug_mode = enabled
        self.log(f"🔧 Mode Debug {'ACTIVÉ' if enabled else 'DÉSACTIVÉ'}")

    def connect_db(self):
        try:
            self.fb = SupabaseConnector()
            return True
        except Exception as e:
            self.log(f"❌ Erreur Connexion DB: {e}")
            return False

    def check_team(self, name):
        if not self.fb: self.connect_db()
        return self.fb.get_team_info("strategies", normalize_id(name))

    def create_team(self, name, category, drivers):
        if not self.fb: self.connect_db()
        return self.fb.create_team("strategies", normalize_id(name), category, drivers)

    def start_loop(self, line_up_name, driver_pseudo):
        # NEW: On incrémente l'ID. Tout ancien thread verra que son ID est périmé et s'arrêtera.
        self.session_id += 1
        current_session_id = self.session_id

        self.line_up_name = line_up_name
        self.team_id = normalize_id(line_up_name)
        self.driver_pseudo = driver_pseudo
        self.running = True
        self.tracker.reset()

        if self.fb:
            self.fb.register_driver_if_new("strategies", self.team_id, driver_pseudo)
            self.fb.start()

        # On passe l'ID au thread
        self.thread = threading.Thread(target=self._run, args=(current_session_id,), daemon=True)
        self.thread.start()

    def stop(self):
        self.log("🛑 Demande d'arrêt...")

        # 1. On coupe le flag principal
        self.running = False

        # 2. On invalide la session (force l'arrêt immédiat des boucles 'zombies')
        # Ceci garantit que si le thread est lent, il s'auto-détruira au prochain tour
        self.session_id += 1

        # 3. Fermeture des connecteurs avant le join() pour débloquer le thread principal
        try:
            if self.rf2_info:
                self.log("🧽 Tentative d'arrêt du connecteur rF2...")
                self.rf2_info.stop()
        except Exception as e:
            self.log(f"⚠️ Erreur lors de l'arrêt de rf2_info : {e}")

        try:
            if self.rest_info:
                self.log("🧽 Tentative d'arrêt du connecteur RestAPI...")
                self.rest_info.stop()
        except Exception as e:
            self.log(f"⚠️ Erreur lors de l'arrêt de rest_info : {e}")

        try:
            if self.fb:
                self.log("🧽 Tentative d'arrêt du connecteur Supabase...")
                self.fb.stop()
        except:
            pass

        # 4. Attente sécurisée de la fin du thread principal
        if self.thread and self.thread.is_alive():
            self.log("⌛ Attente de la fin du thread principal (max 3 secondes)...")
            try:
                # Augmentation du timeout pour donner plus de chance à la terminaison
                self.thread.join(timeout=3.0)
            except Exception as e:
                self.log(f"⚠️ Erreur lors du join du thread: {e}")

        # 5. On vide les références
        self.rf2_info = None
        self.rest_info = None
        self.thread = None  # Très important de réinitialiser le thread

        self.set_status("STOPPED", COLORS["danger"])
        self.log("⏹️ Bridge arrêté.")

    def _run(self, my_session_id):
        # Cette fonction reçoit l'ID qui lui a été attribué au démarrage

        self.log(f"🚀 Démarrage session #{my_session_id} pour '{self.line_up_name}'")
        self.set_status("WAITING GAME...", COLORS["warning"])

        pit_strategy = PitStrategyData(port=6397)
        mock_parent = MockParentAPI()

        # On initialise rest_info ici
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
        UPDATE_RATE = 0.05

        while self.running:
            # NEW: Suicide immédiat si ce thread est obsolète (ID différent de l'actuel)
            if self.session_id != my_session_id:
                self.log(f"⚠️ Thread session #{my_session_id} terminé (Obsolète)")
                break

            current_time = time.time()

            if self.rf2_info is None:
                # Si on demande l'arrêt pendant qu'on attend le jeu, on sort
                if not self.running: break

                if current_time - last_game_check > 5.0:
                    try:
                        self.rf2_info = RF2Info()
                        self.rf2_info.start()
                        self.rest_info.start()
                        self.log("🎮 Jeu détecté ! API active.")
                        self.set_status("CONNECTED", COLORS["success"])

                        telemetry = TelemetryData(self.rf2_info, self.rest_info)
                        scoring = ScoringData(self.rf2_info)
                        rules = RulesData(self.rf2_info)
                        extended = ExtendedData(self.rf2_info)
                        pit_info = PitInfoData(self.rf2_info)
                        weather = WeatherData(self.rf2_info)
                        vehicle_helper = Vehicle(self.rf2_info)

                        self.tracker.reset()

                    except Exception as e:
                        # Si erreur d'init, on s'assure de nettoyer
                        try:
                            if self.rf2_info: self.rf2_info.stop()
                        except:
                            pass
                        self.rf2_info = None
                        vehicle_helper = None
                    last_game_check = current_time
                time.sleep(0.1)
                continue

            try:
                # Double check de sécurité avant d'utiliser les objets
                if not self.running or self.session_id != my_session_id: break
                if self.rf2_info is None or vehicle_helper is None: raise Exception("Perte connexion jeu")

                status = vehicle_helper.get_local_driver_status()

                if status['is_driving'] and (current_time - last_update_time > UPDATE_RATE):
                    # ... (Le code de collecte de données reste identique) ...
                    # Je remets le bloc principal abrégé pour la clarté
                    idx = status['vehicle_index']
                    game_driver = status['driver_name']
                    curr_fuel = telemetry.fuel_level(idx)
                    curr_ve = telemetry.virtual_energy(idx)
                    curr_lap = telemetry.lap_number(idx)

                    # ... (Code météo et calculs identique) ...
                    # Pour faire court, je ne répète pas tout le bloc de parsing météo
                    # Assurez-vous de garder votre code existant ici ou de copier/coller
                    # le bloc complet si vous remplacez tout le fichier.

                    # PARTIE METEO (Simplifiée pour l'exemple, gardez votre code)
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
                    try:
                        scor_veh = scoring.get_vehicle_scoring(idx)
                        in_pits = (scor_veh.get('in_pits', 0) == 1)
                    except:
                        in_pits = False

                    self.tracker.update(curr_lap, curr_fuel, curr_ve, in_pits)
                    stats = self.tracker.get_stats()

                    payload = {
                        "driverName": game_driver,
                        "activeDriverId": self.driver_pseudo,
                        "lastLapFuelConsumption": stats["lastLapFuelConsumption"],
                        "averageConsumptionFuel": stats["averageConsumptionFuel"],
                        "lastLapVEConsumption": stats["lastLapVEConsumption"],
                        "averageConsumptionVE": stats["averageConsumptionVE"],
                        "weatherForecast": forecast_data,
                        # ... (Reste du payload identique à votre code) ...
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
                                      "wear": telemetry.tire_wear(idx), "brake_wear": telemetry.brake_wear(idx), "type": telemetry.surface_type(idx),
                                      "brake_temp": telemetry.brake_temp(idx)},
                            "electric": telemetry.electric_data(idx),
                            "virtual_energy": curr_ve,
                            "max_virtual_energy": 100.0
                        },
                        "scoring": {
                            "track": scoring.track_name(),
                            "time": scoring.time_info(),
                            "flags": scoring.flag_state(),
                            "weather": scoring.weather_env(),
                            "vehicles": [scoring.get_vehicle_scoring(i) for i in range(scoring.vehicle_count())]
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
                    # Envoi sécurisé
                    if self.running and self.session_id == my_session_id:
                        self.fb.send_telemetry(self.team_id, payload)
                        last_update_time = current_time
                        self.set_status(f"SENDING ({game_driver})", COLORS["accent"])
                        if self.debug_mode:
                            bw = telemetry.brake_wear(idx)
                            bw_str = f"FL:{bw[0]:.1f}% FR:{bw[1]:.1f}% RL:{bw[2]:.1f}% RR:{bw[3]:.1f}%"
                            self.log(f"📤 Data envoyé | Brake Wear: {bw_str}")

                elif not status['is_driving']:
                    self.set_status("IDLE (NOT DRIVING)", "#94a3b8")
                    time.sleep(0.5)

            except Exception as e:
                # Si erreur critique, on log mais on ne crash pas la boucle sauf si arrêt demandé
                if self.running and self.session_id == my_session_id:
                    self.log(f"⚠️ Erreur boucle: {e}")
                    # Petite pause pour éviter de spammer les erreurs
                    time.sleep(1.0)

                    # Tentative de reconnexion au prochain tour
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
        self.root.title("LMU Telemetry Bridge")
        self.root.geometry("500x700")
        self.root.configure(bg=COLORS["bg"])
        self.root.resizable(False, False)

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))

        header_frame = tk.Frame(root, bg=COLORS["bg"])
        header_frame.pack(pady=20)
        tk.Label(header_frame, text="LE MANS", font=("Segoe UI", 24, "bold italic"), bg=COLORS["bg"], fg="white").pack()
        tk.Label(header_frame, text="STRATEGY BRIDGE", font=("Segoe UI", 10, "bold"), bg=COLORS["bg"],
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

        self.btn_start = tk.Button(form_frame, text="CONNEXION", bg=COLORS["accent"], fg="white",
                                   font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2", command=self.on_start)
        self.btn_start.pack(fill="x", ipady=8)

        self.btn_stop = tk.Button(form_frame, text="DÉCONNEXION", bg=COLORS["danger"], fg="white",
                                  font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2", command=self.on_stop)

        self.lbl_status = tk.Label(root, text="READY", bg=COLORS["bg"], fg="#94a3b8", font=("Consolas", 10, "bold"))
        self.lbl_status.pack(pady=5)

        self.var_debug = tk.BooleanVar(value=False)
        self.chk_debug = tk.Checkbutton(root, text="Afficher les données transmises (Debug)",
                                        variable=self.var_debug, bg=COLORS["bg"], fg="#94a3b8",
                                        selectcolor=COLORS["panel"], activebackground=COLORS["bg"],
                                        activeforeground="white", font=("Segoe UI", 9),
                                        command=self.toggle_debug)
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
        # On utilise root.after pour demander au thread principal de faire l'affichage
        # Cela évite les crashs quand on loggue depuis un thread en arrière-plan
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
        # Même protection pour le label de statut
        self.root.after(0, lambda: self.lbl_status.config(text=text, fg=color))

    def toggle_debug(self):
        self.logic.set_debug(self.var_debug.get())

    def on_start(self):
        lineup = self.ent_lineup.get().strip()
        pseudo = self.ent_pseudo.get().strip()
        if not lineup or not pseudo:
            messagebox.showwarning("Info manquante", "Veuillez remplir tous les champs.")
            return
        self.btn_start.config(state=tk.DISABLED, text="VÉRIFICATION...")
        threading.Thread(target=self._check_and_start, args=(lineup, pseudo)).start()

    def _check_and_start(self, lineup, pseudo):
        info = self.logic.check_team(lineup)
        if info and info["exists"]:
            self.log(f"✅ Line Up trouvée ! ({info.get('category')})")
            self._activate_ui(True)
            self.logic.start_loop(lineup, pseudo)
        else:
            self.root.after(0, lambda: self._show_creation_dialog(lineup, pseudo))

    def _show_creation_dialog(self, lineup, pseudo):
        dialog = tk.Toplevel(self.root)
        dialog.title("Créer Line Up")
        dialog.geometry("350x300")
        dialog.configure(bg=COLORS["panel"])
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text=f"La Line Up '{lineup}' n'existe pas.", bg=COLORS["panel"], fg="white",
                 font=("Segoe UI", 10)).pack(pady=10)
        tk.Label(dialog, text="Catégorie :", bg=COLORS["panel"], fg="#94a3b8").pack()

        cats = ["Hypercar", "LMP2", "LMP2 (ELMS)", "LMP3", "GT3"]
        combo_cat = ttk.Combobox(dialog, values=cats, state="readonly")
        combo_cat.current(0)
        combo_cat.pack(pady=5, ipadx=10)

        tk.Label(dialog, text="Autres pilotes (sép. par virgule):", bg=COLORS["panel"], fg="#94a3b8").pack()
        ent_drivers = tk.Entry(dialog, bg=COLORS["input"], fg="white", relief="flat")
        ent_drivers.pack(fill="x", padx=20, pady=5, ipady=5)

        def confirm_create():
            cat = combo_cat.get()
            others = [d.strip() for d in ent_drivers.get().split(',') if d.strip()]
            all_drivers = [pseudo] + others
            if self.logic.create_team(lineup, cat, all_drivers):
                self.log(f"✅ Line Up créée : {cat}")
                dialog.destroy()
                self._activate_ui(True)
                self.logic.start_loop(lineup, pseudo)
            else:
                messagebox.showerror("Erreur", "Échec création.")
                self._activate_ui(False)

        tk.Button(dialog, text="CRÉER & REJOINDRE", bg=COLORS["success"], fg="white", font=("Segoe UI", 10, "bold"),
                  command=confirm_create, relief="flat").pack(pady=20, ipadx=10)

    def _activate_ui(self, active):
        if active:
            self.ent_lineup.config(state=tk.DISABLED)
            self.ent_pseudo.config(state=tk.DISABLED)
            self.btn_start.pack_forget()
            self.btn_stop.pack(fill="x", ipady=8)
            self.chk_debug.config(state=tk.NORMAL)
        else:
            self.ent_lineup.config(state=tk.NORMAL)
            self.ent_pseudo.config(state=tk.NORMAL)
            self.btn_stop.pack_forget()
            self.btn_start.pack(fill="x", ipady=8)
            self.btn_start.config(state=tk.NORMAL, text="CONNEXION")

    def on_stop(self):
        # 1. On change l'état du bouton pour dire à l'utilisateur de patienter
        self.btn_stop.config(text="ARRÊT EN COURS...", state=tk.DISABLED)
        self.log("⏳ Déconnexion en cours...")

        # 2. On lance l'arrêt dans un thread SÉPARÉ pour ne pas geler l'interface
        threading.Thread(target=self._async_stop_process, daemon=True).start()

    def _async_stop_process(self):
        try:
            # On tente d'arrêter la logique
            self.logic.stop()
        except Exception as e:
            # En cas d'erreur, on l'affiche mais on continue pour débloquer l'interface
            print(f"Erreur critique lors de l'arrêt : {e}")
        finally:
            # QUOI QU'IL ARRIVE (Succès ou Erreur), on réactive l'interface
            self.root.after(0, lambda: self._activate_ui(False))
            self.root.after(0, lambda: self.log("✅ Déconnecté."))


if __name__ == "__main__":
    root = tk.Tk()
    app = BridgeApp(root)
    root.mainloop()
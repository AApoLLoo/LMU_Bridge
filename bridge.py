import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext, simpledialog
import threading
import time
import sys
import queue

# --- IMPORT DES CONNECTEURS ---
try:
    from adapter.rf2_connector import RF2Info
    from adapter.rf2_data import (
        TelemetryData, ScoringData, RulesData, ExtendedData, 
        PitInfoData, WeatherData, PitStrategyData, Vehicle
    )
    from adapter.firebase_connector import FirebaseConnector
except ImportError as e:
    # Fallback pour éviter le crash immédiat si les dossiers sont mal placés
    print(f"Erreur d'import critique : {e}")
    sys.exit(1)

# --- COULEURS & STYLE ---
COLORS = {
    "bg": "#0f172a",       # Bleu nuit foncé
    "panel": "#1e293b",    # Gris bleu
    "input": "#334155",    # Gris clair
    "text": "#f8fafc",     # Blanc cassé
    "accent": "#6366f1",   # Indigo
    "accent_hover": "#4f46e5",
    "success": "#10b981",  # Vert
    "danger": "#ef4444",   # Rouge
}

# --- LOGIQUE MÉTIER (THREAD SÉPARÉ) ---
class BridgeLogic:
    def __init__(self, log_callback, status_callback):
        self.log = log_callback
        self.set_status = status_callback
        self.running = False
        self.fb = None
        self.rf2_info = None
        self.thread = None
        
        # Données de session
        self.line_up_name = ""
        self.driver_pseudo = ""
        self.category = ""

    def connect_firebase(self):
        try:
            self.fb = FirebaseConnector(project_id="le-mans-strat")
            return True
        except Exception as e:
            self.log(f"❌ Erreur Firebase: {e}")
            return False

    def check_team(self, name):
        """Vérifie si l'équipe existe sur Firebase"""
        if not self.fb: self.connect_firebase()
        info = self.fb.get_team_info("strategies", name)
        return info

    def create_team(self, name, category, drivers):
        """Crée l'équipe sur Firebase"""
        if not self.fb: self.connect_firebase()
        return self.fb.create_team("strategies", name, category, drivers)

    def start_loop(self, line_up_name, driver_pseudo):
        self.line_up_name = line_up_name
        self.driver_pseudo = driver_pseudo
        self.running = True
        
        # Enregistrement du pilote
        if self.fb:
            self.fb.register_driver_if_new("strategies", line_up_name, driver_pseudo)
            self.fb.start()
        
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.fb: self.fb.stop()
        if self.thread: self.thread.join(timeout=1.0)
        self.set_status("STOPPED", COLORS["danger"])
        self.log("⏹️ Bridge arrêté.")

    def _run(self):
        self.log(f"🚀 Démarrage pour '{self.line_up_name}' en tant que '{self.driver_pseudo}'")
        self.log("⏳ En attente du jeu (Le Mans Ultimate / rFactor 2)...")
        self.set_status("WAITING GAME...", "#eab308") # Jaune

        # Objets de données
        pit_strategy = PitStrategyData(port=6397)
        telemetry = scoring = rules = extended = pit_info = weather = vehicle_helper = None
        
        last_game_check = 0
        last_update_time = 0
        UPDATE_RATE = 0.5

        while self.running:
            current_time = time.time()

            # 1. Connexion Jeu (Lazy Loading)
            if self.rf2_info is None:
                if current_time - last_game_check > 5.0:
                    try:
                        self.rf2_info = RF2Info()
                        self.log("🎮 Jeu détecté ! Initialisation des capteurs...")
                        self.set_status("CONNECTED", COLORS["success"])
                        
                        # Instanciation
                        telemetry = TelemetryData(self.rf2_info)
                        scoring = ScoringData(self.rf2_info)
                        rules = RulesData(self.rf2_info)
                        extended = ExtendedData(self.rf2_info)
                        pit_info = PitInfoData(self.rf2_info)
                        weather = WeatherData(self.rf2_info)
                        vehicle_helper = Vehicle(self.rf2_info)
                    except:
                        pass # Toujours pas de jeu
                    last_game_check = current_time
                time.sleep(0.1)
                continue

            # 2. Boucle Télémétrie
            try:
                self.rf2_info.update()
                status = vehicle_helper.get_local_driver_status()

                if status['is_driving'] and (current_time - last_update_time > UPDATE_RATE):
                    idx = status['vehicle_index']
                    game_driver = status['driver_name']

                    payload = {
                        "timestamp": current_time,
                        "driverName": game_driver, # Nom Jeu
                        "activeDriverId": self.driver_pseudo, # Nom Bridge
                        "telemetry": {
                            "gear": telemetry.gear(idx),
                            "rpm": telemetry.rpm(idx),
                            "speed": vehicle_helper.speed(idx),
                            "fuel": telemetry.fuel_level(idx),
                            "inputs": {
                                "thr": telemetry.input_throttle(idx),
                                "brk": telemetry.input_brake(idx),
                                "clt": telemetry.input_clutch(idx),
                                "str": telemetry.input_steering(idx)
                            },
                            "temps": {
                                "oil": telemetry.temp_oil(idx),
                                "water": telemetry.temp_water(idx)
                            },
                            "tires": {
                                "temp": telemetry.tire_temps(idx),
                                "press": telemetry.tire_pressure(idx),
                                "wear": telemetry.tire_wear(idx),
                                "type": telemetry.surface_type(idx)
                            },
                            "damage": telemetry.dents(idx),
                            "electric": telemetry.electric_data(idx)
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

                    self.fb.send_telemetry(self.line_up_name, payload)
                    last_update_time = current_time
                    self.set_status(f"SENDING ({game_driver})", COLORS["accent"])
                
                elif not status['is_driving']:
                    self.set_status("IDLE (NOT DRIVING)", "#94a3b8") # Gris
                    time.sleep(0.5)

            except Exception as e:
                self.log(f"⚠️ Perte connexion jeu: {e}")
                self.rf2_info = None # Forcer reconnexion
                self.set_status("DISCONNECTED", COLORS["danger"])

            time.sleep(0.01)

# --- INTERFACE GRAPHIQUE (Tkinter) ---
class BridgeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LMU Telemetry Bridge")
        self.root.geometry("500x650")
        self.root.configure(bg=COLORS["bg"])
        self.root.resizable(False, False)

        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10, "bold"), borderwidth=0)

        # 1. Header
        header_frame = tk.Frame(root, bg=COLORS["bg"])
        header_frame.pack(pady=20)
        tk.Label(header_frame, text="LE MANS", font=("Segoe UI", 24, "bold italic"), bg=COLORS["bg"], fg="white").pack()
        tk.Label(header_frame, text="STRATEGY BRIDGE", font=("Segoe UI", 10, "bold"), bg=COLORS["bg"], fg=COLORS["accent"]).pack()

        # 2. Formulaire
        form_frame = tk.Frame(root, bg=COLORS["panel"], padx=20, pady=20)
        form_frame.pack(padx=30, fill="x", pady=10)

        # Champ Line Up
        tk.Label(form_frame, text="NOM DE LA LINE UP (ID)", bg=COLORS["panel"], fg="#94a3b8", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.ent_lineup = tk.Entry(form_frame, bg=COLORS["input"], fg="white", font=("Segoe UI", 12), relief="flat", insertbackground="white")
        self.ent_lineup.pack(fill="x", pady=(5, 15), ipady=5)

        # Champ Pseudo
        tk.Label(form_frame, text="VOTRE PSEUDO", bg=COLORS["panel"], fg="#94a3b8", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.ent_pseudo = tk.Entry(form_frame, bg=COLORS["input"], fg="white", font=("Segoe UI", 12), relief="flat", insertbackground="white")
        self.ent_pseudo.pack(fill="x", pady=(5, 20), ipady=5)

        # Bouton Start
        self.btn_start = tk.Button(form_frame, text="CONNEXION", bg=COLORS["accent"], fg="white", font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2", command=self.on_start)
        self.btn_start.pack(fill="x", ipady=8)

        # Bouton Stop (Caché au début)
        self.btn_stop = tk.Button(form_frame, text="DÉCONNEXION", bg=COLORS["danger"], fg="white", font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2", command=self.on_stop)

        # 3. Status Bar
        self.lbl_status = tk.Label(root, text="READY", bg=COLORS["bg"], fg="#94a3b8", font=("Consolas", 10, "bold"))
        self.lbl_status.pack(pady=5)

        # 4. Logs
        self.txt_log = scrolledtext.ScrolledText(root, bg="#020408", fg="#22c55e", font=("Consolas", 9), height=10, relief="flat")
        self.txt_log.pack(fill="both", expand=True, padx=30, pady=(0, 30))
        self.txt_log.config(state=tk.DISABLED)

        # Logic
        self.logic = BridgeLogic(self.log, self.set_status)

    def log(self, msg):
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, f"> {msg}\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)

    def set_status(self, text, color):
        self.lbl_status.config(text=text, fg=color)

    def on_start(self):
        lineup = self.ent_lineup.get().strip()
        pseudo = self.ent_pseudo.get().strip()

        if not lineup or not pseudo:
            messagebox.showwarning("Info manquante", "Veuillez remplir le nom de la Line Up et votre Pseudo.")
            return

        self.btn_start.config(state=tk.DISABLED, text="VÉRIFICATION...")
        
        # Lancer la vérification dans un thread pour ne pas geler l'UI
        threading.Thread(target=self._check_and_start, args=(lineup, pseudo)).start()

    def _check_and_start(self, lineup, pseudo):
        info = self.logic.check_team(lineup)
        
        if info and info["exists"]:
            self.log(f"✅ Line Up trouvée ! ({info.get('category')})")
            self._activate_ui(True)
            self.logic.start_loop(lineup, pseudo)
        else:
            # Demander la création via un dialogue modal (sur le thread principal)
            self.root.after(0, lambda: self._show_creation_dialog(lineup, pseudo))

    def _show_creation_dialog(self, lineup, pseudo):
        # Création d'une fenêtre modale personnalisée
        dialog = tk.Toplevel(self.root)
        dialog.title("Créer Line Up")
        dialog.geometry("350x300")
        dialog.configure(bg=COLORS["panel"])
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text=f"La Line Up '{lineup}' n'existe pas.", bg=COLORS["panel"], fg="white", font=("Segoe UI", 10)).pack(pady=10)
        tk.Label(dialog, text="Sélectionnez la catégorie :", bg=COLORS["panel"], fg="#94a3b8", font=("Segoe UI", 9)).pack()

        cats = ["Hypercar", "LMP2", "LMP2 (ELMS)", "LMP3", "GT3"]
        combo_cat = ttk.Combobox(dialog, values=cats, state="readonly", font=("Segoe UI", 10))
        combo_cat.current(0)
        combo_cat.pack(pady=10, ipadx=10)

        tk.Label(dialog, text="Autres pilotes (optionnel, sép. par virgule):", bg=COLORS["panel"], fg="#94a3b8", font=("Segoe UI", 9)).pack()
        ent_drivers = tk.Entry(dialog, bg=COLORS["input"], fg="white", relief="flat")
        ent_drivers.pack(fill="x", padx=20, pady=5, ipady=5)

        def confirm_create():
            cat = combo_cat.get()
            others = [d.strip() for d in ent_drivers.get().split(',') if d.strip()]
            all_drivers = [pseudo] + others # On inclut le créateur
            
            if self.logic.create_team(lineup, cat, all_drivers):
                self.log(f"✅ Line Up créée : {cat}")
                dialog.destroy()
                self._activate_ui(True)
                self.logic.start_loop(lineup, pseudo)
            else:
                messagebox.showerror("Erreur", "Impossible de créer l'équipe.")
                self._activate_ui(False)

        tk.Button(dialog, text="CRÉER & REJOINDRE", bg=COLORS["success"], fg="white", font=("Segoe UI", 10, "bold"), command=confirm_create, relief="flat").pack(pady=20, ipadx=10)

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
            self.btn_start.config(state=tk.NORMAL, text="CONNEXION")

    def on_stop(self):
        self.logic.stop()
        self._activate_ui(False)

if __name__ == "__main__":
    root = tk.Tk()
    app = BridgeApp(root)
    root.mainloop()
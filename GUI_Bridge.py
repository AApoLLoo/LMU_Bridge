import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, font

# --- Import des modules du projet ---
try:
    from adapter.rf2_connector import RF2Info
    from adapter.restapi_connector import RestAPIInfo
    from adapter.rf2_data import (
        TelemetryData, ScoringData, RulesData, ExtendedData, 
        PitInfoData, WeatherData, PitStrategyData, Vehicle
    )
    from adapter.firebase_connector import FirebaseConnector
except ImportError as e:
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, f"Erreur d'import : {e}", "Erreur Fatale", 0x10)
    sys.exit(1)

# --- PALETTE DE COULEURS ---
COLORS = {
    "bg": "#020408",          # Fond principal
    "panel": "#0f172a",       # Slate 900
    "input": "#1e293b",       # Slate 800
    "text_main": "#ffffff",
    "text_dim": "#94a3b8",    # Slate 400
    "primary": "#4f46e5",     # Indigo 600
    "primary_hover": "#4338ca",
    "danger": "#ef4444",      # Red 500
    "danger_hover": "#dc2626",
    "success": "#10b981",     # Emerald 500
    "accent": "#22d3ee",      # Cyan 400
    "border": "#334155"       # Slate 700
}

class ModernBridgeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Le Mans 24H Bridge")
        self.root.geometry("700x550")
        self.root.configure(bg=COLORS["bg"])
        
        # Icône (optionnel)
        try: self.root.iconbitmap("icon.ico") 
        except: pass

        # État
        self.running = False
        self.thread = None
        self.stop_event = threading.Event()
        
        # Connecteurs
        self.rf2_info = None
        self.rf2_rest = None
        self.fb = None

        self._configure_styles()
        self._setup_ui()
        
        # Redirection stdout
        sys.stdout = self
        sys.stderr = self

    def _configure_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')

        # Configuration générique
        self.style.configure("TFrame", background=COLORS["bg"])
        self.style.configure("Panel.TFrame", background=COLORS["panel"], relief="flat")
        
        # Labels
        self.style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text_main"], font=("Segoe UI", 10))
        self.style.configure("Dim.TLabel", background=COLORS["panel"], foreground=COLORS["text_dim"], font=("Segoe UI", 9))
        self.style.configure("Header.TLabel", background=COLORS["bg"], foreground=COLORS["text_main"], font=("Segoe UI", 24, "bold italic"))
        self.style.configure("SubHeader.TLabel", background=COLORS["bg"], foreground=COLORS["primary"], font=("Segoe UI", 10, "bold"))

    def _setup_ui(self):
        # --- HEADER ---
        header_frame = ttk.Frame(self.root, padding="20 20 20 10")
        header_frame.pack(fill="x")
        
        # Titre stylisé
        title_cont = ttk.Frame(header_frame)
        title_cont.pack(anchor="w")
        
        ttk.Label(title_cont, text="LE MANS", style="Header.TLabel").pack(side="left")
        lbl_24 = ttk.Label(title_cont, text=" 24H", style="Header.TLabel")
        lbl_24.pack(side="left")
        lbl_24.configure(foreground=COLORS["accent"])
        
        ttk.Label(header_frame, text="STRATEGY BRIDGE & TELEMETRY LINK", style="SubHeader.TLabel").pack(anchor="w")

        # --- PANNEAU DE CONFIGURATION ---
        config_frame = tk.Frame(self.root, bg=COLORS["panel"], padx=20, pady=20)
        config_frame.pack(fill="x", padx=20, pady=10)
        
        # Bordure gauche colorée
        tk.Frame(config_frame, bg=COLORS["primary"], width=4).place(x=0, y=0, relheight=1)

        # Label Input (CORRECTION ICI : pady=(0, 5) remplace mb=5)
        tk.Label(config_frame, text="NOM DE LA LINE UP (Collection Firebase)", 
                 bg=COLORS["panel"], fg=COLORS["text_dim"], 
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 5))

        # Input personnalisé
        self.entry_lineup = tk.Entry(config_frame, 
                                     bg=COLORS["input"], 
                                     fg=COLORS["text_main"],
                                     insertbackground="white", # Curseur blanc
                                     font=("Consolas", 12),
                                     relief="flat",
                                     bd=5)
        self.entry_lineup.pack(fill="x", ipady=3)
        self.entry_lineup.insert(0, "baliverne")

        # --- BOUTONS D'ACTION ---
        btn_frame = tk.Frame(self.root, bg=COLORS["bg"])
        btn_frame.pack(fill="x", padx=20, pady=10)

        # Bouton Start
        self.btn_start = self._create_custom_button(btn_frame, "DÉMARRER LE BRIDGE", COLORS["primary"], self.start_bridge)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 5))

        # Bouton Stop
        self.btn_stop = self._create_custom_button(btn_frame, "ARRÊTER", COLORS["danger"], self.stop_bridge)
        self.btn_stop.pack(side="right", fill="x", expand=True, padx=(5, 0))
        self.btn_stop['state'] = 'disabled'
        self.btn_stop.config(bg=COLORS["input"], fg=COLORS["text_dim"])

        # --- CONSOLE DE LOGS ---
        log_frame = tk.Frame(self.root, bg=COLORS["panel"], padx=2, pady=2)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        
        # Header Logs
        tk.Label(log_frame, text="> SYSTÈME LOGS", bg=COLORS["panel"], fg=COLORS["accent"], 
                 font=("Consolas", 9, "bold")).pack(anchor="w", padx=5, pady=2)

        self.text_logs = scrolledtext.ScrolledText(
            log_frame, 
            bg="#050a10",
            fg=COLORS["text_dim"],
            insertbackground="white",
            font=("Consolas", 9),
            relief="flat",
            state='disabled',
            height=10
        )
        self.text_logs.pack(fill="both", expand=True)
        
        # Tags pour la coloration des logs
        self.text_logs.tag_config("INFO", foreground=COLORS["text_main"])
        self.text_logs.tag_config("SUCCESS", foreground=COLORS["success"])
        self.text_logs.tag_config("ERROR", foreground=COLORS["danger"])
        self.text_logs.tag_config("WARN", foreground="#fbbf24")

    def _create_custom_button(self, parent, text, bg_color, command):
        """Crée un bouton stylisé avec effets de survol"""
        btn = tk.Button(parent, text=text, bg=bg_color, fg="white", 
                        activebackground="white", activeforeground=bg_color,
                        font=("Segoe UI", 10, "bold"), relief="flat", cursor="hand2",
                        command=command, pady=8)
        
        # Hover effects
        def on_enter(e):
            if btn['state'] != 'disabled':
                btn.config(bg=bg_color) # Pas de changement pour l'instant pour rester simple
        def on_leave(e):
            if btn['state'] != 'disabled':
                btn.config(bg=bg_color)
                
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    def write(self, text):
        self.root.after(0, self._append_log, text)

    def flush(self): pass

    def _append_log(self, text):
        self.text_logs.configure(state='normal')
        
        tag = "INFO"
        if "Erreur" in text or "Exception" in text or "FATAL" in text: tag = "ERROR"
        elif "Connecté" in text or "prêts" in text: tag = "SUCCESS"
        elif "Attention" in text or "WAITING" in text: tag = "WARN"
        
        self.text_logs.insert(tk.END, text, tag)
        self.text_logs.see(tk.END)
        self.text_logs.configure(state='disabled')

    def start_bridge(self):
        line_up = self.entry_lineup.get().strip()
        if not line_up:
            messagebox.showwarning("Configuration Manquante", "Veuillez entrer un nom de Line Up.")
            return

        self.running = True
        self.stop_event.clear()
        
        # Update UI state
        self.btn_start.config(state="disabled", bg=COLORS["input"], fg=COLORS["text_dim"], cursor="arrow")
        self.entry_lineup.config(state="disabled")
        self.btn_stop.config(state="normal", bg=COLORS["danger"], fg="white", cursor="hand2")
        
        self.text_logs.configure(state='normal')
        self.text_logs.delete(1.0, tk.END)
        self.text_logs.configure(state='disabled')

        self.thread = threading.Thread(target=self.run_logic, args=(line_up,), daemon=True)
        self.thread.start()

    def stop_bridge(self):
        if not self.running: return
        print("\n> Arrêt en cours...")
        self.running = False
        self.stop_event.set()

    def run_logic(self, line_up_name):
        try:
            print(f"> Initialisation pour : {line_up_name}")
            
            # 1. Firebase
            print("> Connexion Firebase...")
            path_to_json = "serviceAccountKey.json"
            self.fb = FirebaseConnector(path_to_json, line_up_name)
            self.fb.start()

            # 2. Mémoire Partagée
            print("> Connexion Mémoire Partagée Le Mans Ultimate...")
            self.rf2_info = RF2Info()
            self.rf2_info.setMode(1) 

            # 3. API REST
            print("> Connexion API REST...")
            self.rf2_rest = RestAPIInfo(self.rf2_info)
            
            rest_config = {
                "restapi_update_interval": 500,
                "enable_restapi_access": True,
                "url_host": "localhost",
                "url_port_lmu": 6397,
                "url_port_rf2": 5397,
                "connection_timeout": 1.0,
                "connection_retry": 1,
                "connection_retry_delay": 1.0,
                "enable_weather_info": True,
                "enable_session_info": True,
                "enable_garage_setup_info": True,
                "enable_vehicle_info": True,
                "enable_energy_remaining": True
            }
            self.rf2_rest.setConnection(rest_config)

            self.rf2_info.start()
            self.rf2_rest.start()

            # Adaptateurs
            telemetry = TelemetryData(self.rf2_info)
            scoring = ScoringData(self.rf2_info)
            rules = RulesData(self.rf2_info)
            extended = ExtendedData(self.rf2_info)
            pit_info = PitInfoData(self.rf2_info)
            weather = WeatherData(self.rf2_info)
            vehicle_helper = Vehicle(self.rf2_info)
            pit_strategy = PitStrategyData()

            print("> Systèmes prêts. En attente de conduite...")
            
            last_update_time = 0
            UPDATE_RATE = 0.5

            while self.running and not self.stop_event.is_set():
                status = vehicle_helper.get_local_driver_status()

                if status['is_driving'] and (time.time() - last_update_time > UPDATE_RATE):
                    idx = status['vehicle_index']
                    driver_name = status['driver_name']

                    payload = {
                        "timestamp": time.time(),
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

                    self.fb.send_telemetry(driver_name, payload)
                    last_update_time = time.time()

                else:
                    time.sleep(0.5)
                
                time.sleep(0.01)

        except Exception as e:
            print(f"ERREUR CRITIQUE: {e}")
        finally:
            self._cleanup()

    def _cleanup(self):
        print("> Fermeture des connexions...")
        try:
            if self.rf2_info: self.rf2_info.stop()
            if self.rf2_rest: self.rf2_rest.stop()
            if self.fb: self.fb.stop()
        except Exception as e:
            print(f"Erreur nettoyage: {e}")
        
        print("> Bridge arrêté.")
        self.running = False
        self.root.after(0, self._reset_ui_state)

    def _reset_ui_state(self):
        self.btn_start.config(state="normal", bg=COLORS["primary"], fg="white", cursor="hand2")
        self.entry_lineup.config(state="normal")
        self.btn_stop.config(state="disabled", bg=COLORS["input"], fg=COLORS["text_dim"], cursor="arrow")

if __name__ == "__main__":
    root = tk.Tk()
    app = ModernBridgeApp(root)
    root.mainloop()
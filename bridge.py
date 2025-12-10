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
    from adapter.socket_connector import SocketConnector
except ImportError as e:
    print(f"Erreur d'import critique : {e}")
    sys.exit(1)

COLORS = {
    "bg": "#0f172a", "panel": "#1e293b", "input": "#334155",
    "text": "#f8fafc", "accent": "#6366f1", "success": "#10b981",
    "danger": "#ef4444", "warning": "#eab308", "debug": "#a855f7"
}

VPS_IP = "51.178.87.25"


def normalize_id(name):
    import re
    safe = re.sub(r'[^a-zA-Z0-9]+', '-', name).strip('-').lower()
    return safe


class MockParentAPI:
    def __init__(self):
        self.identifier = "LMU"
        self.isActive = True


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
        if self.last_lap == -1 or current_lap < self.last_lap:
            self.last_lap = current_lap
            self.fuel_start = current_fuel
            self.ve_start = current_ve
            return

        if current_lap > self.last_lap:
            fuel_delta = self.fuel_start - current_fuel
            ve_delta = self.ve_start - current_ve

            if not in_pits and fuel_delta > 0.01:
                self.fuel_last = fuel_delta
                self.fuel_samples += 1
                self.fuel_avg = self.fuel_avg + (fuel_delta - self.fuel_avg) / self.fuel_samples
                self.log(f"🏁 Tour {self.last_lap} terminé. Conso: {fuel_delta:.2f}L")

                if ve_delta > 0.01:
                    self.ve_last = ve_delta
                    self.ve_samples += 1
                    self.ve_avg = self.ve_avg + (ve_delta - self.ve_avg) / self.ve_samples

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
        self.connector = None
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

    def connect_vps(self):
        if self.connector and self.connector.is_connected:
            return True
        try:
            self.connector = SocketConnector(VPS_IP)
            self.connector.connect()
            return True
        except Exception as e:
            self.log(f"❌ Erreur Connexion VPS: {e}")
            return False

    def start_loop(self, line_up_name, driver_pseudo):
        self.session_id += 1
        current_session_id = self.session_id
        self.line_up_name = line_up_name
        self.team_id = normalize_id(line_up_name)
        self.driver_pseudo = driver_pseudo
        self.running = True
        self.tracker.reset()
        if not self.connector: self.connect_vps()
        self.thread = threading.Thread(target=self._run, args=(current_session_id,), daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.session_id += 1
        try:
            if self.rf2_info: self.rf2_info.stop()
            if self.rest_info: self.rest_info.stop()
        except:
            pass
        self.set_status("STOPPED", COLORS["danger"])
        self.log("⏹️ Bridge arrêté.")

    def _run(self, my_session_id):
        self.log(f"🚀 Session #{my_session_id} démarrée")
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
        UPDATE_RATE = 0.05

        while self.running:
            if self.session_id != my_session_id: break
            current_time = time.time()

            if self.rf2_info is None:
                if current_time - last_game_check > 5.0:
                    try:
                        self.rf2_info = RF2Info()
                        self.rf2_info.start()
                        self.rest_info.start()
                        self.log("🎮 Jeu détecté !")
                        self.set_status("CONNECTED (GAME)", COLORS["success"])
                        telemetry = TelemetryData(self.rf2_info, self.rest_info)
                        scoring = ScoringData(self.rf2_info)
                        rules = RulesData(self.rf2_info)
                        extended = ExtendedData(self.rf2_info)
                        pit_info = PitInfoData(self.rf2_info)
                        weather = WeatherData(self.rf2_info)
                        vehicle_helper = Vehicle(self.rf2_info)
                        self.tracker.reset()
                    except:
                        self.rf2_info = None
                    last_game_check = current_time
                time.sleep(0.1)
                continue

            try:
                status = vehicle_helper.get_local_driver_status()
                if status['is_driving'] and (current_time - last_update_time > UPDATE_RATE):
                    idx = status['vehicle_index']
                    game_driver = status['driver_name']
                    curr_fuel = telemetry.fuel_level(idx)
                    curr_ve = telemetry.virtual_energy(idx)
                    curr_lap = telemetry.lap_number(idx)

                    # --- CORRECTION POSITION DE CLASSE ---
                    all_vehicles = [scoring.get_vehicle_scoring(i) for i in range(scoring.vehicle_count())]
                    scor_veh = scoring.get_vehicle_scoring(idx)

                    my_class = scor_veh.get('class')
                    class_rank = 1
                    if my_class:
                        for v in all_vehicles:
                            if v['id'] != scor_veh['id'] and v['class'] == my_class and v['position'] < scor_veh[
                                'position']:
                                class_rank += 1

                    # On injecte la position dans l'objet véhicule
                    scor_veh['classPosition'] = class_rank

                    # --- CORRECTION TEMPS RESTANT ---
                    time_info = scoring.time_info()
                    time_rem = 0
                    if time_info['end'] > 0:
                        time_rem = max(0, time_info['end'] - time_info['current'])

                    in_pits = (scor_veh.get('in_pits', 0) == 1)
                    self.tracker.update(curr_lap, curr_fuel, curr_ve, in_pits)
                    stats = self.tracker.get_stats()

                    payload = {
                        "teamId": self.team_id,
                        "driverName": game_driver,
                        "activeDriverId": self.driver_pseudo,
                        "sessionTimeRemainingSeconds": time_rem,
                        "lastLapFuelConsumption": stats["lastLapFuelConsumption"],
                        "averageConsumptionFuel": stats["averageConsumptionFuel"],
                        "lastLapVEConsumption": stats["lastLapVEConsumption"],
                        "averageConsumptionVE": stats["averageConsumptionVE"],
                        "telemetry": {
                            "gear": telemetry.gear(idx),
                            "rpm": telemetry.rpm(idx),
                            "speed": vehicle_helper.speed(idx),
                            "fuel": curr_fuel,
                            "fuelCapacity": telemetry.fuel_capacity(idx),
                                                        "inputs": {"thr": telemetry.input_throttle(idx), "brk": telemetry.input_brake(idx),
                                       "clt": telemetry.input_clutch(idx), "str": telemetry.input_steering(idx)},
                            "temps": {
                                "oil": telemetry.temp_oil(idx),
                                "water": telemetry.temp_water(idx)
                            },
                            "wheels": telemetry.wheel_details(idx),
                            "state": telemetry.car_state(idx),
                            "electric": telemetry.electric_data(idx),
                            "virtual_energy": curr_ve
                        },
                        "scoring": {
                            "track": scoring.track_name(),
                            "time": time_info,
                            "flags": scoring.flag_state(),
                            "weather": scoring.weather_env(),
                            "vehicles": all_vehicles,
                            "vehicle_data": scor_veh
                        },
                        "rules": {
                            "sc": rules.sc_info(),
                            "yellow": rules.yellow_flag(),
                            "my_status": rules.participant_status(idx)
                        },
                        "pit": {
                            "strategy": pit_strategy.pit_estimate()
                        },
                        "extended": {
                            "pit_limit": extended.pit_limit()
                        }
                    }

                    if self.connector: self.connector.send_data(payload)
                    last_update_time = current_time
                    self.set_status(f"LIVE P{class_rank} ({game_driver})", COLORS["accent"])

                    # --- DEBUG MODE (RÉACTIVÉ) ---
                    if self.debug_mode:
                        self.log(f"📤 Sent VPS | Payload: {payload}")

                elif not status['is_driving']:
                    self.set_status("IDLE (NOT DRIVING)", "#94a3b8")
                    time.sleep(0.5)

            except Exception as e:
                self.log(f"⚠️ Erreur: {e}")
                time.sleep(1.0)

            time.sleep(0.01)


class BridgeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LMU Bridge (VPS Fix)")
        self.root.geometry("500x600")
        self.root.configure(bg=COLORS["bg"])

        frame = tk.Frame(root, bg=COLORS["bg"])
        frame.pack(pady=20)
        tk.Label(frame, text="LMU BRIDGE", font=("Segoe UI", 20, "bold"), bg=COLORS["bg"], fg="white").pack()

        form = tk.Frame(root, bg=COLORS["panel"], padx=20, pady=20)
        form.pack(padx=30, fill="x")

        self.ent_lineup = tk.Entry(form, bg=COLORS["input"], fg="white", font=("Segoe UI", 12))
        self.ent_lineup.pack(fill="x", pady=5)
        self.ent_lineup.insert(0, "TeamName")

        self.ent_pseudo = tk.Entry(form, bg=COLORS["input"], fg="white", font=("Segoe UI", 12))
        self.ent_pseudo.pack(fill="x", pady=5)
        self.ent_pseudo.insert(0, "DriverName")

        self.btn_start = tk.Button(form, text="CONNECTER", bg=COLORS["accent"], fg="white", command=self.on_start)
        self.btn_start.pack(fill="x", pady=10)

        # Checkbox Debug
        self.var_debug = tk.BooleanVar(value=False)
        self.chk_debug = tk.Checkbutton(form, text="Mode Debug (Logs)", variable=self.var_debug,
                                        bg=COLORS["panel"], fg="white", selectcolor=COLORS["bg"],
                                        activebackground=COLORS["panel"], activeforeground="white",
                                        command=self.toggle_debug)
        self.chk_debug.pack(pady=5)

        self.lbl_status = tk.Label(root, text="READY", bg=COLORS["bg"], fg="#94a3b8")
        self.lbl_status.pack(pady=5)

        self.txt_log = scrolledtext.ScrolledText(root, bg="#020408", fg="#22c55e", height=10)
        self.txt_log.pack(fill="both", expand=True, padx=30, pady=10)

        self.logic = BridgeLogic(self.log, self.set_status)

    def log(self, msg):
        self.txt_log.insert(tk.END, f"> {msg}\n")
        self.txt_log.see(tk.END)

    def set_status(self, text, color):
        self.lbl_status.config(text=text, fg=color)

    def toggle_debug(self):
        self.logic.set_debug(self.var_debug.get())

    def on_start(self):
        self.logic.start_loop(self.ent_lineup.get(), self.ent_pseudo.get())


if __name__ == "__main__":
    root = tk.Tk()
    app = BridgeApp(root)
    root.mainloop()
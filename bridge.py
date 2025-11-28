from __future__ import annotations
import time
import sys
import os
import math
import hashlib
import re
import threading
import queue

# --- CONFIGURATION PUBLIQUE (SÉCURISÉ POUR PARTAGE) ---
# Cette clé est faite pour être publique (restreinte par les règles Firestore)
FIREBASE_API_KEY = "AIzaSyAezT5Np6-v18OBR1ICV3uHoFViQB555sg"
FIREBASE_PROJECT_ID = "le-mans-strat"
# ------------------------------------------------------

# --- IMPORTS ET CHEMINS ---
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Import Requests
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("FATAL: Le module 'requests' n'est pas installé.")
    print("👉 Faites: pip install requests")
    sys.exit(1)

# Import SimInfoAPI
try:
    from pyRfactor2SharedMemory.sharedMemoryAPI import SimInfoAPI
except ImportError:
    print("FATAL: Le module 'pyRfactor2SharedMemory' n'est pas trouvé.")
    sys.exit(1)

# --- CONSTANTES ---
KELVIN_TO_CELSIUS = -273.15
EMPTY_DICT = {}
PITEST_DEFAULT = (0.0, 0.0, 0.0, 0.0, 0)

SESSION_MAP = {
    0: "TEST DAY",
    1: "PRACTICE 1", 2: "PRACTICE 2", 3: "PRACTICE 3", 4: "PRACTICE 4",
    5: "QUALIFY 1", 6: "QUALIFY 2", 7: "QUALIFY 3", 8: "QUALIFY 4",
    9: "WARMUP",
    10: "RACE 1", 11: "RACE 2", 12: "RACE 3", 13: "RACE 4"
}

# --- FONCTIONS UTILITAIRES ---
def get_team_color(category):
    cat_lower = category.lower()
    if 'hyper' in cat_lower: return 'bg-red-600'
    if 'lmp2' in cat_lower: return 'bg-blue-600'
    if 'lmp3' in cat_lower: return 'bg-purple-600'
    if 'gte' in cat_lower or 'gt3' in cat_lower or 'lmgt3' in cat_lower: return 'bg-orange-500'
    if 'elms' in cat_lower: return 'bg-sky-500'
    return 'bg-slate-600'

def to_firestore_value(value):
    if value is None: return {"nullValue": None}
    if isinstance(value, bool): return {"booleanValue": value}
    if isinstance(value, int): return {"integerValue": str(value)}
    if isinstance(value, float): return {"doubleValue": value}
    if isinstance(value, str): return {"stringValue": value}
    return {"stringValue": str(value)}

# --- LOGIQUE PIT ---
rex_number_extract = re.compile(r"\d*\.?\d+")
PIT_FUNC_MAP = {} 

# (J'ai compressé les fonctions PIT pour la lisibilité, elles restent fonctionnelles)
def set_stopgo_state(raw, ref_time, temp):
    if raw.get("currentSetting", 0) != 0: temp.state_stopgo = 2 if ref_time.get("SimultaneousStopGo", False) else 1
def count_tyre_change(raw, ref_time, temp): temp.tyre_change += (raw.get("currentSetting", 0) != raw.get("default", 0))
def count_pressure_change(raw, ref_time, temp): temp.pressure_change += (raw.get("currentSetting", 0) != raw.get("default", 0))
def set_time_damage(raw, ref_time, temp):
    c = raw.get("currentSetting", 0)
    if c == 1: s = ref_time.get("FixAeroDamage", 0)
    elif c == 2: s = ref_time.get("FixAllDamage", 0)
    else: s = 0.0
    return s, 0.0, ref_time.get("FixTimeConcurrent", 0)
def set_time_driver(raw, ref_time, temp):
    return (ref_time.get("DriverChange", 0) if raw.get("currentSetting") != raw.get("default") else 0.0), 0.0, ref_time.get("DriverConcurrent", 0)
def set_time_virtual_energy(raw, ref_time, temp):
    current = raw.get("currentSetting", 0)
    seconds = ref_time.get("virtualEnergyInsert", 0) + ref_time.get("virtualEnergyRemove", 0)
    fill_rate = ref_time.get("virtualEnergyFillRate", 0) * 100
    refill = current - temp.nrg_remaining
    if refill > 0 < fill_rate: seconds += refill / fill_rate
    else: seconds = 0.0
    temp.nrg_abs_refill = current; temp.nrg_rel_refill = refill
    return seconds, 0.0, ref_time.get("virtualEnergyTimeConcurrent", 0)
def set_time_tyre(ref_time, temp):
    delay = ref_time.get("RandomTireDelay", 0)
    concurrent = ref_time.get("TireTimeConcurrent", 0)
    pressure_on_fly = ref_time.get("OnTheFlyPressure", False)
    pres_seconds = ref_time.get("PressureChange", 0) if temp.pressure_change and (temp.tyre_change or pressure_on_fly) else 0.0
    if 2 < temp.tyre_change: seconds = ref_time.get("FourTireChange", 0)
    elif 0 < temp.tyre_change: seconds = ref_time.get("TwoTireChange", 0)
    else: seconds = 0.0
    return max(seconds, pres_seconds), delay, concurrent
def set_time_front_wing(raw, ref_time, temp): return (ref_time.get("FrontWingAdjust", 0) if raw.get("currentSetting") != raw.get("default") else 0.0), 0.0, 1
def set_time_rear_wing(raw, ref_time, temp): return (ref_time.get("RearWingAdjust", 0) if raw.get("currentSetting") != raw.get("default") else 0.0), 0.0, 1
def set_time_radiator(raw, ref_time, temp): return (ref_time.get("RadiatorChange", 0) if raw.get("currentSetting") != raw.get("default") else 0.0), 0.0, 1
def set_time_brake(raw, ref_time, temp): return (ref_time.get("BrakeChange", 0) if raw.get("currentSetting") > 0 else 0.0), 0.0, ref_time.get("BrakeTimeConcurrent", 0)
def set_time_fuel(fuel_absolute, ref_time, temp):
    seconds = ref_time.get("FuelInsert", 0) + ref_time.get("FuelRemove", 0)
    fill_rate = ref_time.get("FuelFillRate", 0)
    refill = fuel_absolute - temp.fuel_remaining
    if refill > 0 < fill_rate: seconds += refill / fill_rate
    else: seconds = 0.0
    temp.fuel_abs_refill = fuel_absolute; temp.fuel_rel_refill = refill
    return seconds, 0.0, ref_time.get("FuelTimeConcurrent", 0)
def set_time_fuel_only(raw, ref_time, temp):
    try:
        val = raw.get("settings")[raw.get("currentSetting", 0)]["text"]
        fuel = float(rex_number_extract.search(val).group())
        if "gal" in val.lower(): fuel *= 3.7854118
    except: fuel = 0.0
    return set_time_fuel(fuel, ref_time, temp)
def set_time_fuel_energy(raw, ref_time, temp):
    try: fuel = float(raw.get("settings")[raw.get("currentSetting", 0)]["text"].strip()) * temp.nrg_abs_refill
    except: fuel = 0.0
    return set_time_fuel(fuel, ref_time, temp)

PIT_FUNC_MAP = {
    "STOP/GO:": set_stopgo_state, "DAMAGE:": set_time_damage, "DRIVER:": set_time_driver,
    "VIRTUAL ENERGY:": set_time_virtual_energy, "FUEL RATIO:": set_time_fuel_energy, "FUEL:": set_time_fuel_only,
    "FL TIRE:": count_tyre_change, "FR TIRE:": count_tyre_change, "RL TIRE:": count_tyre_change, "RR TIRE:": count_tyre_change,
    "FL PRESS:": count_pressure_change, "FR PRESS:": count_pressure_change, "RL PRESS:": count_pressure_change, "RR PRESS:": count_pressure_change,
    "F WING:": set_time_front_wing, "R WING:": set_time_rear_wing, "GRILLE:": set_time_radiator, "REPLACE BRAKES:": set_time_brake,
}

class EstimatePitTime:
    __slots__ = ("state_stopgo", "tyre_change", "pressure_change", "nrg_rel_refill", "fuel_rel_refill", "nrg_abs_refill", "fuel_abs_refill", "nrg_remaining", "fuel_remaining")
    def __init__(self): self.state_stopgo = 0; self.tyre_change = 0; self.pressure_change = 0; self.nrg_rel_refill = 0.0; self.fuel_rel_refill = 0.0; self.nrg_abs_refill = 0.0; self.fuel_abs_refill = 0.0; self.nrg_remaining = 0.0; self.fuel_remaining = 0.0
    def __call__(self, dataset):
        pit_menu = dataset.get("pitMenu", {}).get("pitMenu", None)
        ref_time = dataset.get("pitStopTimes", {}).get("times", None)
        if not isinstance(pit_menu, list) or not isinstance(ref_time, dict): return PITEST_DEFAULT
        fuel_info = dataset.get("fuelInfo", {})
        self.state_stopgo = 0; self.tyre_change = 0; self.pressure_change = 0
        nrg_max = fuel_info.get("maxVirtualEnergy", 0.0)
        self.nrg_remaining = fuel_info.get("currentVirtualEnergy", 0.0) / nrg_max * 100 if nrg_max else 0.0
        self.fuel_remaining = fuel_info.get("currentFuel", 0.0)
        
        sum_concurrent = 0.0; sum_separate = 0.0; sum_concurrent_delay = 0.0; sum_separate_delay = 0.0
        for service_time, random_delay, is_concurrent in self.__process(pit_menu, ref_time):
            service_time_delay = service_time + random_delay
            if is_concurrent:
                if sum_concurrent < service_time: sum_concurrent = service_time
                if sum_concurrent_delay < service_time_delay: sum_concurrent_delay = service_time_delay
            else:
                sum_separate += service_time; sum_separate_delay += service_time_delay
        return (sum_concurrent + sum_separate, sum_concurrent_delay + sum_separate_delay, self.fuel_rel_refill, self.nrg_rel_refill, self.state_stopgo)

    def __process(self, pit_menu, ref_time):
        for raw in pit_menu:
            pit_func = PIT_FUNC_MAP.get(raw.get("name"))
            if pit_func:
                value = pit_func(raw, ref_time, self)
                if value is not None: yield value
                elif self.state_stopgo == 1: return
        yield set_time_tyre(ref_time, self)

# =============================================================================
# CLASSE PRINCIPALE OPTIMISÉE POUR DISTRIBUTION
# =============================================================================

class LMUBridge:
    def __init__(self):
        self.sim = SimInfoAPI()
        self.current_session_id = None
        self._reset_metrics()
        self.last_driver_register_time = 0
        self.team_registered = False 
        self.last_registered_driver = None
        
        # --- OPTIMISATION HTTP PERSISTANTE ---
        # On crée une session qui reste ouverte. C'est le secret de la vitesse sans clé privée.
        self.http_session = requests.Session()
        
        # Configuration des retries pour la robustesse
        retries = Retry(total=3, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        self.http_session.mount('https://', HTTPAdapter(max_retries=retries))
        
        # Queue non bloquante
        self.upload_queue = queue.Queue(maxsize=1)
        self.running = True
        
        print("\n" + "="*50)
        print("🏁  BRIDGE LMU PUBLIC (Sécurisé & Rapide) 🏁")
        print("="*50)
        
        while True:
            raw_id = input("👉 ID de la voiture (ex: hypercar-50) : ").strip().lower()
            self.manual_team_id = raw_id.replace(" ", "-")
            if self.manual_team_id: break
        
        # Thread d'envoi
        self.sender_thread = threading.Thread(target=self._sender_worker, daemon=True)
        self.sender_thread.start()
        
        print(f"\n✅ ID ACTIVÉ : {self.manual_team_id}")
        print("🚀 Mode Haute Performance activé (Session HTTP persistante)")
        print("En attente du jeu...")

    def _reset_metrics(self):
        self.last_fuel = -1.0; self.last_lap = 0; self.last_lap_fuel_start = -1.0
        self.fuel_history = []; self.avg_fuel_consumption = 0.0; self.last_lap_fuel_consumption = 0.0
        self.last_lap_energy_start_pct = -1.0; self.energy_history = []; self.avg_energy_consumption = 0.0
        self.last_lap_energy_consumption = 0.0; self.ema_lap_time = 0.0
        self.last_tire_wear_cumulative = [0.0] * 4; self.average_wear_per_lap = [0.0] * 4; self.lap_counter_wear = 0

    @staticmethod
    def _safe_decode(bytestring: bytes) -> str:
        try: return bytes(bytestring).partition(b'\0')[0].decode('utf_8').rstrip()
        except: return bytes(bytestring).partition(b'\0')[0].decode('utf_8', 'ignore').rstrip()

    def _get_player_data(self):
        try:
            veh_tele = self.sim.playersVehicleTelemetry()
            veh_scor = self.sim.playersVehicleScoring()
            if veh_tele and veh_scor and veh_scor.mIsPlayer: return veh_tele, veh_scor
        except: pass
        return None, None

    def _get_rest_data(self) -> dict:
        # Optimisation: Timeout très court sur l'API locale du jeu
        try:
            resp = requests.get("http://localhost:6397/rest/garage/UIScreen/RepairAndRefuel", timeout=0.05)
            if resp.status_code == 200: return resp.json()
        except: pass
        return {}

    def _get_damage_status(self, veh_tele):
        if not veh_tele: return 0, False
        return sum(veh_tele.mDentSeverity), bool(veh_tele.mOverheating)

    def _get_session_id(self) -> str:
        try:
            scor_info = self.sim.Rf2Scor.mScoringInfo
            track = self._safe_decode(scor_info.mTrackName)
            session = scor_info.mSession
            start = scor_info.mStartET
            return f"{track}_{session}_{start}"
        except: return "unknown_session"

    def _update_lap_metrics(self, current_lap, current_fuel, current_energy_pct, current_lap_time_last, current_tire_wear_cumulative):
        if self.last_lap == 0 and current_lap > 0:
            self.last_tire_wear_cumulative = current_tire_wear_cumulative
            self.last_fuel = current_fuel; self.last_lap = current_lap; self.last_lap_fuel_start = current_fuel; self.last_lap_energy_start_pct = current_energy_pct
            return

        is_new_lap = current_lap != self.last_lap and current_lap > 0
        alpha = 0.1 

        if is_new_lap:
            fuel_used = self.last_lap_fuel_start - current_fuel if self.last_lap_fuel_start > 0 else 0.0
            self.last_lap_fuel_consumption = round(fuel_used, 2)
            if fuel_used > 0.5:
                self.fuel_history.append(fuel_used)
                if len(self.fuel_history) > 5: self.fuel_history.pop(0)
                if self.fuel_history: self.avg_fuel_consumption = round(sum(self.fuel_history) / len(self.fuel_history), 3)
            self.last_lap_fuel_start = current_fuel 

            if self.last_lap_energy_start_pct >= 0:
                energy_used = self.last_lap_energy_start_pct - current_energy_pct
                if energy_used > 0.1: 
                    self.last_lap_energy_consumption = round(energy_used, 2)
                    self.energy_history.append(energy_used)
                    if len(self.energy_history) > 5: self.energy_history.pop(0)
                    if self.energy_history: self.avg_energy_consumption = round(sum(self.energy_history) / len(self.energy_history), 2)
            self.last_lap_energy_start_pct = current_energy_pct
            
            if 0 < current_lap_time_last < 999: 
                self.ema_lap_time = round(current_lap_time_last, 3) if self.ema_lap_time == 0.0 else round(alpha * current_lap_time_last + (1 - alpha) * self.ema_lap_time, 3)
            
            for i in range(4):
                wear_delta = max(0, current_tire_wear_cumulative[i] - self.last_tire_wear_cumulative[i])
                if self.lap_counter_wear < 5:
                    if wear_delta > 0.0: self.average_wear_per_lap[i] = round(self.average_wear_per_lap[i] * self.lap_counter_wear + wear_delta / (self.lap_counter_wear + 1), 4)
                else: self.average_wear_per_lap[i] = round(alpha * wear_delta + (1 - alpha) * self.average_wear_per_lap[i], 4)
            if self.lap_counter_wear < 5: self.lap_counter_wear += 1

            self.last_tire_wear_cumulative = current_tire_wear_cumulative
            self.last_lap = current_lap

    def _register_team_in_directory(self, team_id, vehicle_name, car_category, driver_name):
        if self.team_registered and self.last_registered_driver == driver_name: return

        cat_lower = car_category.lower()
        category = 'other'
        if 'hyper' in cat_lower: category = 'hypercar'
        elif 'lmp3' in cat_lower: category = 'lmp3'
        elif 'gt3' in cat_lower: category = 'lmgt3'
        elif 'lmp2' in cat_lower: category = 'lmp2 (elms)' if 'elms' in cat_lower else 'lmp2'
        if category == 'lmp2' and 'elms' in team_id.lower(): category = 'lmp2 (elms)'

        try: display_name = vehicle_name.replace(" #", " ").upper()
        except: display_name = team_id.upper()

        data = {
            "id": team_id, "name": display_name, "category": category,
            "color": get_team_color(category), "currentDriver": driver_name,
            "isActive": True
        }
        
        self.send_async("teams", team_id, data)
        print(f"✨ Équipe mise à jour: {display_name} ({driver_name})")
        self.team_registered = True
        self.last_registered_driver = driver_name

    # --- WORKER THREAD OPTIMISÉ (Persistent REST) ---
    def _sender_worker(self):
        # Utilisation de la session persistante (Keep-Alive TCP)
        while self.running:
            try:
                collection, doc_id, data = self.upload_queue.get(timeout=1.0)
                
                url = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/{collection}/{doc_id}"
                
                # Conversion manuelle rapide
                fields = {}
                for k, v in data.items():
                    fields[k] = to_firestore_value(v)
                payload = {"fields": fields}
                
                # PATCH avec session persistante (pas de nouveau handshake SSL)
                self.http_session.patch(url, params={"key": FIREBASE_API_KEY}, json=payload)
                
                self.upload_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Erreur envoi: {e}")

    def send_async(self, collection, doc_id, data):
        if self.upload_queue.full():
            try: self.upload_queue.get_nowait()
            except queue.Empty: pass
        self.upload_queue.put((collection, doc_id, data))

    def run(self):
        pit_estimator = EstimatePitTime()

        while True:
            # 30Hz pour fluidité
            time.sleep(0.033)
            
            if not self.sim.isRF2running():
                print("⏳ Jeu non détecté...", end="\r")
                time.sleep(1)
                continue
            
            new_sess = self._get_session_id()
            if self.current_session_id is not None and new_sess != self.current_session_id and new_sess != "unknown_session":
                self._reset_metrics()
            if new_sess != "unknown_session": self.current_session_id = new_sess

            veh_tele, veh_scor = self._get_player_data()
            if not veh_tele or not veh_scor: continue
            
            in_garage = bool(veh_scor.mInGarageStall)
            in_pits_raw = bool(veh_scor.mInPits)
            in_pit_lane = in_pits_raw and not in_garage
            is_active_driver = not in_garage
            
            team_id = self.manual_team_id
            driver_name = self._safe_decode(veh_scor.mDriverName)
            vehicle_name_raw = self._safe_decode(veh_scor.mVehicleName)
            car_category = self._safe_decode(veh_scor.mVehicleClass)

            self._register_team_in_directory(team_id, vehicle_name_raw, car_category, driver_name)

            position = int(veh_scor.mPlace)
            current_lap = int(veh_scor.mTotalLaps) 
            fuel = float(veh_tele.mFuel)
            lap_time_last = float(veh_scor.mLastLapTime)
            fuel_capacity = float(veh_tele.mFuelCapacity)
            battery_soc_pct = round(float(veh_tele.mBatteryChargeFraction) * 100.0, 1)

            car_number = "0"
            try: car_number = vehicle_name_raw.split(' ')[0].replace('#', '')
            except: pass
            
            throttle_pct = round(float(veh_tele.mUnfilteredThrottle) * 100.0, 1)
            brake_pct = round(float(veh_tele.mUnfilteredBrake) * 100.0, 1)
            
            vx = float(veh_tele.mLocalVel.x); vy = float(veh_tele.mLocalVel.y); vz = float(veh_tele.mLocalVel.z)
            speed_kmh = round(math.sqrt(vx*vx + vy*vy + vz*vz) * 3.6, 0)
            
            rpm = round(float(veh_tele.mEngineRPM), 0)
            max_rpm = round(float(veh_tele.mEngineMaxRPM), 0)
            water_temp = round(float(veh_tele.mEngineWaterTemp), 1)
            oil_temp = round(float(veh_tele.mEngineOilTemp), 1)
            
            scor_info = self.sim.Rf2Scor.mScoringInfo
            physics = self.sim.Rf2Ext.mPhysics
            ambient_temp_c = round(float(scor_info.mAmbientTemp), 1)
            
            tire_wear_values = []; brake_temp_values = []; tire_temp_center_values = []
            
            for wheel in veh_tele.mWheels:
                tire_wear_values.append(float(wheel.mWear))
                try: bt = float(wheel.mBrakeTemp); brake_temp_values.append(ambient_temp_c if bt < 10.0 else bt + KELVIN_TO_CELSIUS)
                except: brake_temp_values.append(ambient_temp_c)
                try: tt = float(wheel.mTemperature[1]); tire_temp_center_values.append(ambient_temp_c if tt < 10.0 else tt + KELVIN_TO_CELSIUS)
                except: tire_temp_center_values.append(ambient_temp_c)

            try: f_compound = self._safe_decode(veh_tele.mFrontTireCompoundName).split(" ")[0].upper()
            except: f_compound = "---"
            try: r_compound = self._safe_decode(veh_tele.mRearTireCompoundName).split(" ")[0].upper()
            except: r_compound = "---"
            
            track_wetness_pct = round(float(scor_info.mAvgPathWetness) * 100.0, 1)
            session_remaining_time = float(scor_info.mEndET) - float(scor_info.mCurrentET)
            track_name = self._safe_decode(scor_info.mTrackName)
            session_name = SESSION_MAP.get(int(scor_info.mSession), "UNKNOWN")

            rain_severity = float(scor_info.mRaining)
            weather_status = "RAIN" if rain_severity > 0.4 else ("CLOUDY" if rain_severity > 0.05 or float(scor_info.mDarkCloud) > 0.5 else "SUNNY")

            engine_mode = int(veh_tele.mElectricBoostMotorState)
            tc_setting = int(physics.mTractionControl)
            if tc_setting == 0: tc_setting = engine_mode
            brake_bias_front_pct = round((1.0 - float(veh_tele.mRearBrakeBias)) * 100.0, 1)

            pit_state = int(veh_scor.mPitState)
            damage_index, is_overheating = self._get_damage_status(veh_tele)
            estimated_lap_game = float(veh_scor.mEstimatedLapTime)

            rest_data = self._get_rest_data()
            est_pit_time = 0.0; strategy_fuel_add = 0.0; strategy_tires_count = 0
            
            ve_pct = 0.0
            if rest_data and "fuelInfo" in rest_data:
                 fi = rest_data["fuelInfo"]
                 ve_pct = round(float(fi.get("currentVirtualEnergy", 0.0)) / float(fi.get("maxVirtualEnergy", 1.0)) * 100.0, 1)

            if rest_data:
                pit_values = pit_estimator(rest_data)
                est_pit_time = round(pit_values[0], 1)
                strategy_fuel_add = round(pit_estimator.fuel_abs_refill, 1)
                strategy_tires_count = pit_estimator.tyre_change

            self._update_lap_metrics(current_lap, fuel, ve_pct, lap_time_last, tire_wear_values)
            wear_remaining_pct = [round((1.0 - w) * 100.0, 1) for w in tire_wear_values]

            data_to_send = {
                "isRaceRunning": True, "driverName": driver_name,
                "activeDriverId": driver_name if is_active_driver else None,
                "carNumber": car_number, "carCategory": car_category, "teamId": team_id,
                "trackName": track_name, "sessionType": session_name, "position": position,
                "throttle": throttle_pct, "brake": brake_pct, "speedKmh": speed_kmh,
                "rpm": rpm, "maxRpm": max_rpm, "waterTemp": water_temp, "oilTemp": oil_temp,
                "currentLap": current_lap, "lapTimeLast": lap_time_last, 
                "fuelRemainingL": round(fuel, 2), "averageConsumptionFuel": self.avg_fuel_consumption, 
                "lastLapFuelConsumption": self.last_lap_fuel_consumption,
                "tireCompoundFL": f_compound, "tireCompoundFR": f_compound,
                "tireCompoundRL": r_compound, "tireCompoundRR": r_compound,
                "batterySoc": battery_soc_pct, "virtualEnergyRemainingPct": ve_pct,
                "virtualEnergyConsumptionLastLap": self.last_lap_energy_consumption,
                "virtualEnergyAverageConsumption": self.avg_energy_consumption,
                "averageLapTime": self.ema_lap_time,
                "sessionTimeRemainingSeconds": round(max(0, session_remaining_time), 0),
                "fuelTankCapacityL": round(fuel_capacity, 2),
                "pitState": pit_state, "inPitLane": in_pit_lane, "inGarage": in_garage,          
                "damageIndex": damage_index, "isOverheating": is_overheating,
                "gameEstimatedLapTime": estimated_lap_game, "strategyFuelToAdd": strategy_fuel_add,
                "strategyTiresChanged": strategy_tires_count, "strategyEstPitTime": est_pit_time,
                "weather": weather_status, "airTemp": ambient_temp_c, "trackWetness": track_wetness_pct,
                "tcSetting": tc_setting, "brakeBiasFront": brake_bias_front_pct, "engineMode": engine_mode,
                "lastPacketTime": int(time.time() * 1000), 
                "tireWearFL" : 100.0 - wear_remaining_pct[0] if len(wear_remaining_pct) > 0 else 100.0,
                "tireWearFR" : 100.0 - wear_remaining_pct[1] if len(wear_remaining_pct) > 1 else 100.0,
                "tireWearRL" : 100.0 - wear_remaining_pct[2] if len(wear_remaining_pct) > 2 else 100.0,
                "tireWearRR" : 100.0 - wear_remaining_pct[3] if len(wear_remaining_pct) > 3 else 100.0,
                "avgWearPerLapFL": self.average_wear_per_lap[0], "avgWearPerLapFR": self.average_wear_per_lap[1],
                "avgWearPerLapRL": self.average_wear_per_lap[2], "avgWearPerLapRR": self.average_wear_per_lap[3],
                "brakeTempFLC": round(brake_temp_values[0], 1) if len(brake_temp_values) > 0 else 0.0,
                "brakeTempFRC": round(brake_temp_values[1], 1) if len(brake_temp_values) > 1 else 0.0,
                "brakeTempRLC": round(brake_temp_values[2], 1) if len(brake_temp_values) > 2 else 0.0,
                "brakeTempRRC": round(brake_temp_values[3], 1) if len(brake_temp_values) > 3 else 0.0,
                "tireTempCenterFLC": round(tire_temp_center_values[0], 1) if len(tire_temp_center_values) > 0 else 0.0,
                "tireTempCenterFRC": round(tire_temp_center_values[1], 1) if len(tire_temp_center_values) > 1 else 0.0,
                "tireTempCenterRLC": round(tire_temp_center_values[2], 1) if len(tire_temp_center_values) > 2 else 0.0,
                "tireTempCenterRRC": round(tire_temp_center_values[3], 1) if len(tire_temp_center_values) > 3 else 0.0,
            }

            if not is_active_driver:
                 if "activeDriverId" in data_to_send: del data_to_send["activeDriverId"]

            self.send_async("strategies", team_id, data_to_send)
            status = "[GARAGE]" if in_garage else "[PITS]" if in_pit_lane else "[TRACK]"
            print(f"🚀 {team_id} | {status} | {speed_kmh} km/h | Fuel: {data_to_send['fuelRemainingL']}L", end="\r")
            self.last_fuel = fuel

if __name__ == "__main__":
    bridge = LMUBridge()
    try: bridge.run()
    except KeyboardInterrupt: 
        print("\nArrêt.")
        bridge.running = False
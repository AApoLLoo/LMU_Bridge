from __future__ import annotations
import time
import sys
import os
import json
import urllib.request
from typing import Tuple, Any
import re
import math
import hashlib

# --- REGEX POUR L'ANALYSE ---
rex_number_extract = re.compile(r"\d*\.?\d+")

# Ensure local package directory is on sys.path
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Import Firebase Admin SDK
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    print("FATAL: Le module 'firebase-admin' n'est pas installé. Exécutez 'pip install firebase-admin'.")
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

# Mapping des sessions
SESSION_MAP = {
    0: "TEST DAY",
    1: "PRACTICE 1", 2: "PRACTICE 2", 3: "PRACTICE 3", 4: "PRACTICE 4",
    5: "QUALIFY 1", 6: "QUALIFY 2", 7: "QUALIFY 3", 8: "QUALIFY 4",
    9: "WARMUP",
    10: "RACE 1", 11: "RACE 2", 12: "RACE 3", 13: "RACE 4"
}

# --- CONNEXION FIREBASE ---
try:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print(f"🔥 Connecté à Firebase")
except Exception as e:
    print(f"❌ Erreur initialisation Firebase : {e}")
    db = None

# --- UTILITAIRES ---
def generate_driver_color(name):
    """Génère une couleur unique basée sur le nom."""
    hash_obj = hashlib.md5(name.encode())
    return '#' + hash_obj.hexdigest()[:6]

def get_team_color(category):
    """Couleur par défaut selon la catégorie."""
    if 'hyper' in category.lower(): return 'bg-red-600'
    if 'lmp2' in category.lower(): return 'bg-blue-600'
    if 'gte' in category.lower() or 'gt3' in category.lower(): return 'bg-orange-500'
    return 'bg-slate-600'

# =============================================================================
# LOGIQUE D'ESTIMATION DES STANDS
# =============================================================================

class EstimatePitTime:
    __slots__ = (
        "state_stopgo", "tyre_change", "pressure_change",
        "nrg_rel_refill", "fuel_rel_refill",
        "nrg_abs_refill", "fuel_abs_refill",
        "nrg_remaining", "fuel_remaining",
    )

    def __init__(self):
        self.state_stopgo = 0
        self.tyre_change = 0
        self.pressure_change = 0
        self.nrg_rel_refill = 0.0
        self.fuel_rel_refill = 0.0
        self.nrg_abs_refill = 0.0
        self.fuel_abs_refill = 0.0
        self.nrg_remaining = 0.0
        self.fuel_remaining = 0.0

    def __call__(self, dataset: dict) -> tuple[float, float, float, float, int]:
        pit_menu = dataset.get("pitMenu", EMPTY_DICT).get("pitMenu", None)
        ref_time = dataset.get("pitStopTimes", EMPTY_DICT).get("times", None)
        fuel_info = dataset.get("fuelInfo", EMPTY_DICT)

        if not isinstance(pit_menu, list) or not isinstance(ref_time, dict):
            return PITEST_DEFAULT

        self.state_stopgo = 0
        self.tyre_change = 0
        self.pressure_change = 0
        nrg_current = fuel_info.get("currentVirtualEnergy", 0.0)
        nrg_max = fuel_info.get("maxVirtualEnergy", 0.0)
        self.nrg_remaining = nrg_current / nrg_max * 100 if nrg_max else 0.0
        self.fuel_remaining = fuel_info.get("currentFuel", 0.0)

        gen_pit_time = self.__process(pit_menu, ref_time)
        
        sum_concurrent = 0.0
        sum_separate = 0.0
        sum_concurrent_delay = 0.0
        sum_separate_delay = 0.0

        for service_time, random_delay, is_concurrent in gen_pit_time:
            service_time_delay = service_time + random_delay
            if is_concurrent:
                if sum_concurrent < service_time:
                    sum_concurrent = service_time
                if sum_concurrent_delay < service_time_delay:
                    sum_concurrent_delay = service_time_delay
            else:
                sum_separate += service_time
                sum_separate_delay += service_time_delay

        return (
            sum_concurrent + sum_separate,
            sum_concurrent_delay + sum_separate_delay,
            self.fuel_rel_refill,
            self.nrg_rel_refill,
            self.state_stopgo,
        )

    def __process(self, pit_menu: list, ref_time: dict):
        for raw in pit_menu:
            pit_func = PIT_FUNC_MAP.get(raw.get("name"))
            if pit_func:
                value = pit_func(raw, ref_time, self)
                if value is not None:
                    yield value
                elif self.state_stopgo == 1:
                    return
        yield set_time_tyre(ref_time, self)

# --- FONCTIONS D'AIDE POUR LE PIT STOP ---
def set_stopgo_state(raw, ref_time, temp):
    if raw.get("currentSetting", 0) != 0:
        if ref_time.get("SimultaneousStopGo", False):
            temp.state_stopgo = 2
        else:
            temp.state_stopgo = 1
    return None

def count_tyre_change(raw, ref_time, temp):
    temp.tyre_change += (raw.get("currentSetting", 0) != raw.get("default", 0))
    return None

def count_pressure_change(raw, ref_time, temp):
    temp.pressure_change += (raw.get("currentSetting", 0) != raw.get("default", 0))
    return None

def set_time_damage(raw, ref_time, temp):
    current = raw.get("currentSetting", 0)
    delay = ref_time.get("FixRandomDelay", 0)
    concurrent = ref_time.get("FixTimeConcurrent", 0)
    if current == 1:
        seconds = ref_time.get("FixAeroDamage", 0)
    elif current == 2:
        seconds = ref_time.get("FixAllDamage", 0)
    else:
        delay = seconds = 0.0
    return seconds, delay, concurrent

def set_time_driver(raw, ref_time, temp):
    current = raw.get("currentSetting", 0)
    default = raw.get("default", 0)
    delay = ref_time.get("DriverRandom", 0)
    concurrent = ref_time.get("DriverConcurrent", 0)
    if current != default:
        seconds = ref_time.get("DriverChange", 0)
    else:
        delay = seconds = 0.0
    return seconds, delay, concurrent

def set_time_virtual_energy(raw, ref_time, temp):
    current = raw.get("currentSetting", 0)
    delay = ref_time.get("virtualEnergyRandomDelay", 0)
    concurrent = ref_time.get("virtualEnergyTimeConcurrent", 0)
    seconds = ref_time.get("virtualEnergyInsert", 0)
    seconds += ref_time.get("virtualEnergyRemove", 0)
    fill_rate = ref_time.get("virtualEnergyFillRate", 0) * 100
    refill = current - temp.nrg_remaining
    if refill > 0 < fill_rate:
        seconds += refill / fill_rate
    else:
        delay = seconds = 0.0
    temp.nrg_abs_refill = current
    temp.nrg_rel_refill = refill
    return seconds, delay, concurrent

def set_time_tyre(ref_time, temp):
    delay = ref_time.get("RandomTireDelay", 0)
    concurrent = ref_time.get("TireTimeConcurrent", 0)
    pressure_on_fly = ref_time.get("OnTheFlyPressure", False)
    if temp.pressure_change and (temp.tyre_change or pressure_on_fly):
        pres_seconds = ref_time.get("PressureChange", 0)
    else:
        pres_seconds = 0.0
    if 2 < temp.tyre_change:
        seconds = ref_time.get("FourTireChange", 0)
    elif 0 < temp.tyre_change:
        seconds = ref_time.get("TwoTireChange", 0)
    else:
        delay = seconds = 0.0
    return max(seconds, pres_seconds), delay, concurrent

def set_time_front_wing(raw, ref_time, temp):
    current = raw.get("currentSetting", 0)
    default = raw.get("default", 0)
    seconds = ref_time.get("FrontWingAdjust", 0) if current != default else 0.0
    return seconds, 0.0, 1

def set_time_rear_wing(raw, ref_time, temp):
    current = raw.get("currentSetting", 0)
    default = raw.get("default", 0)
    seconds = ref_time.get("RearWingAdjust", 0) if current != default else 0.0
    return seconds, 0.0, 1

def set_time_radiator(raw, ref_time, temp):
    current = raw.get("currentSetting", 0)
    default = raw.get("default", 0)
    seconds = ref_time.get("RadiatorChange", 0) if current != default else 0.0
    return seconds, 0.0, 1

def set_time_brake(raw, ref_time, temp):
    current = raw.get("currentSetting", 0)
    delay = ref_time.get("RandomBrakeDelay", 0)
    concurrent = ref_time.get("BrakeTimeConcurrent", 0)
    seconds = ref_time.get("BrakeChange", 0) if current > 0 else 0.0
    return seconds, delay, concurrent

def set_time_fuel(fuel_absolute, ref_time, temp):
    delay = ref_time.get("FuelRandomDelay", 0)
    concurrent = ref_time.get("FuelTimeConcurrent", 0)
    seconds = ref_time.get("FuelInsert", 0) + ref_time.get("FuelRemove", 0)
    fill_rate = ref_time.get("FuelFillRate", 0)
    refill = fuel_absolute - temp.fuel_remaining
    if refill > 0 < fill_rate:
        seconds += refill / fill_rate
    else:
        delay = seconds = 0.0
    temp.fuel_abs_refill = fuel_absolute
    temp.fuel_rel_refill = refill
    return seconds, delay, concurrent

def set_time_fuel_only(raw, ref_time, temp):
    try:
        current = raw.get("currentSetting", 0)
        selector = raw.get("settings")
        raw_value = selector[current]["text"]
        fuel = float(rex_number_extract.search(raw_value).group())
        if "gal" in raw_value.lower():
            fuel *= 3.7854118
    except (AttributeError, TypeError, IndexError, ValueError):
        fuel = 0.0
    return set_time_fuel(fuel, ref_time, temp)

def set_time_fuel_energy(raw, ref_time, temp):
    try:
        current = raw.get("currentSetting", 0)
        selector = raw.get("settings")
        raw_value = selector[current]["text"].strip(" ")
        fuel = float(raw_value) * temp.nrg_abs_refill
    except (AttributeError, TypeError, IndexError, ValueError):
        fuel = 0.0
    return set_time_fuel(fuel, ref_time, temp)

PIT_FUNC_MAP = {
    "STOP/GO:": set_stopgo_state,
    "DAMAGE:": set_time_damage,
    "DRIVER:": set_time_driver,
    "VIRTUAL ENERGY:": set_time_virtual_energy,
    "FUEL RATIO:": set_time_fuel_energy,
    "FUEL:": set_time_fuel_only,
    "FL TIRE:": count_tyre_change, "FR TIRE:": count_tyre_change,
    "RL TIRE:": count_tyre_change, "RR TIRE:": count_tyre_change,
    "FL PRESS:": count_pressure_change, "FR PRESS:": count_pressure_change,
    "RL PRESS:": count_pressure_change, "RR PRESS:": count_pressure_change,
    "F WING:": set_time_front_wing, "R WING:": set_time_rear_wing,
    "GRILLE:": set_time_radiator, "REPLACE BRAKES:": set_time_brake,
}


# =============================================================================
# PONT LMU (MAIN)
# =============================================================================

class LMUBridge:
    def __init__(self):
        self.sim = SimInfoAPI()
        self.current_session_id = None
        self._reset_metrics()
        self.last_driver_register_time = 0
        self.team_registered = False 
        
        print("\n" + "="*50)
        print("🏁  BRIDGE PYTHON - MULTI VOITURES 🏁")
        print("="*50)
        
        # Demande de l'ID avec nettoyage
        while True:
            raw_id = input("👉 ID de la voiture (ex: hypercar-50) : ").strip().lower()
            self.manual_team_id = raw_id.replace(" ", "-")
            if self.manual_team_id:
                break
        
        print(f"\n✅ ID ACTIVÉ : {self.manual_team_id}")
        print(f"📡 Envoi vers Firebase collection 'strategies', document '{self.manual_team_id}'")
        print("En attente du jeu...")

    def _reset_metrics(self):
        self.last_fuel = -1.0
        self.last_lap = 0
        self.last_lap_fuel_start = -1.0
        self.fuel_history = [] 
        self.avg_fuel_consumption = 0.0
        self.last_lap_fuel_consumption = 0.0 # <--- AJOUT INITIALISATION
        self.last_lap_energy_start_pct = -1.0
        self.energy_history = []
        self.avg_energy_consumption = 0.0
        self.last_lap_energy_consumption = 0.0
        self.ema_lap_time = 0.0
        self.last_tire_wear_cumulative = [0.0] * 4 
        self.average_wear_per_lap = [0.0] * 4 
        self.lap_counter_wear = 0

    @staticmethod
    def _safe_decode(bytestring: bytes) -> str:
        try:
            return bytes(bytestring).partition(b'\0')[0].decode('utf_8').rstrip()
        except Exception:
            return bytes(bytestring).partition(b'\0')[0].decode('utf_8', 'ignore').rstrip()

    def _get_player_data(self) -> Tuple[Any, Any]:
        try:
            veh_tele = self.sim.playersVehicleTelemetry()
            veh_scor = self.sim.playersVehicleScoring()
            if veh_tele and veh_scor and veh_scor.mIsPlayer:
                return veh_tele, veh_scor
        except Exception:
            pass
        return None, None

    def _get_rest_data(self) -> dict:
        try:
            url = "http://localhost:6397/rest/garage/UIScreen/RepairAndRefuel"
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return json.loads(response.read().decode())
        except Exception:
            pass
        return {}

    def _get_damage_status(self, veh_tele):
        if not veh_tele:
            return 0, False
        return sum(veh_tele.mDentSeverity), bool(veh_tele.mOverheating)

    def _get_session_id(self) -> str:
        try:
            scor_info = self.sim.Rf2Scor.mScoringInfo
            track_name = self._safe_decode(scor_info.mTrackName)
            server_name = self._safe_decode(scor_info.mServerName)
            session_type = scor_info.mSession
            start_et = scor_info.mStartET
            return f"{server_name}_{track_name}_{session_type}_{start_et}"
        except Exception:
            return "unknown_session"

    def _update_lap_metrics(self, current_lap, current_fuel, current_energy_pct, current_lap_time_last, current_tire_wear_cumulative):
        if self.last_lap == 0 and current_lap > 0:
            self.last_tire_wear_cumulative = current_tire_wear_cumulative
            self.last_fuel = current_fuel
            self.last_lap = current_lap
            self.last_lap_fuel_start = current_fuel
            self.last_lap_energy_start_pct = current_energy_pct
            return

        is_new_lap = current_lap != self.last_lap and current_lap > 0
        alpha = 0.1 

        if is_new_lap:
            fuel_used_this_lap = self.last_lap_fuel_start - current_fuel if self.last_lap_fuel_start > 0 else 0.0
            
            self.last_lap_fuel_consumption = round(fuel_used_this_lap, 2) # <--- AJOUT CALCUL CONSO TOUR

            if fuel_used_this_lap > 0.5:
                self.fuel_history.append(fuel_used_this_lap)
                if len(self.fuel_history) > 5:
                    self.fuel_history.pop(0)
                if self.fuel_history:
                    self.avg_fuel_consumption = round(sum(self.fuel_history) / len(self.fuel_history), 3)
            self.last_lap_fuel_start = current_fuel 

            if self.last_lap_energy_start_pct >= 0:
                energy_used_this_lap = self.last_lap_energy_start_pct - current_energy_pct
                if energy_used_this_lap > 0.1: 
                    self.last_lap_energy_consumption = round(energy_used_this_lap, 2)
                    self.energy_history.append(energy_used_this_lap)
                    if len(self.energy_history) > 5:
                        self.energy_history.pop(0)
                    if self.energy_history:
                        self.avg_energy_consumption = round(sum(self.energy_history) / len(self.energy_history), 2)
            self.last_lap_energy_start_pct = current_energy_pct
            
            if 0 < current_lap_time_last < 999: 
                if self.ema_lap_time == 0.0:
                    self.ema_lap_time = round(current_lap_time_last, 3)
                else:
                    self.ema_lap_time = round(alpha * current_lap_time_last + (1 - alpha) * self.ema_lap_time, 3)
            
            for i in range(4):
                wear_delta = max(0, current_tire_wear_cumulative[i] - self.last_tire_wear_cumulative[i])
                if self.lap_counter_wear < 5:
                    if wear_delta > 0.0:
                        self.average_wear_per_lap[i] = round(self.average_wear_per_lap[i] * self.lap_counter_wear + wear_delta / (self.lap_counter_wear + 1), 4)
                else:
                    self.average_wear_per_lap[i] = round(alpha * wear_delta + (1 - alpha) * self.average_wear_per_lap[i], 4)
            
            if self.lap_counter_wear < 5:
                self.lap_counter_wear += 1

            self.last_tire_wear_cumulative = current_tire_wear_cumulative
            self.last_lap = current_lap

    def _register_team_in_directory(self, team_id, vehicle_name, car_category):
        """Enregistre l'équipe dans la collection 'teams'."""
        if not db or self.team_registered: return

        cat_lower = car_category.lower()
        if 'hyper' in cat_lower: category = 'hypercar'
        elif 'lmp2' in cat_lower: category = 'lmp2'
        else: category = 'other'

        try:
            display_name = vehicle_name.replace(" #", " ").upper()
        except:
            display_name = team_id.upper()

        data = {
            "id": team_id,
            "name": display_name,
            "category": category,
            "color": get_team_color(category),
            "lastUpdate": firestore.SERVER_TIMESTAMP,
            "isActive": True
        }

        try:
            db.collection("teams").document(team_id).set(data, merge=True)
            print(f"✨ Équipe '{display_name}' enregistrée dans la liste publique !")
            self.team_registered = True
        except Exception as e:
            print(f"⚠️ Impossible d'enregistrer l'équipe dans la liste : {e}")

    def run(self):
        pit_estimator = EstimatePitTime()

        while True:
            time.sleep(0.1)
            if not self.sim.isRF2running():
                print("⏳ Jeu non détecté...", end="\r")
                continue
            
            new_session_id = self._get_session_id()
            if self.current_session_id is not None and new_session_id != self.current_session_id and new_session_id != "unknown_session":
                print(f"\n🔄 Nouvelle session détectée : Reset des données...")
                self._reset_metrics()
            
            if new_session_id != "unknown_session":
                self.current_session_id = new_session_id

            veh_tele, veh_scor = self._get_player_data()
            
            if not veh_tele or not veh_scor:
                print("⏳ En attente du véhicule joueur... ", end="\r")
                continue
            
            in_garage = bool(veh_scor.mInGarageStall)
            in_pits_raw = bool(veh_scor.mInPits)
            in_pit_lane = in_pits_raw and not in_garage
            
            is_active_driver = not in_garage
            team_id = self.manual_team_id
            driver_name = self._safe_decode(veh_scor.mDriverName)
            vehicle_name_raw = self._safe_decode(veh_scor.mVehicleName)
            car_category = self._safe_decode(veh_scor.mVehicleClass)

            # Enregistrement dans l'annuaire
            self._register_team_in_directory(team_id, vehicle_name_raw, car_category)

            # Enregistrement du pilote (Périodique)
            if time.time() - self.last_driver_register_time > 10 and db:
                try:
                    driver_entry = {
                        "id": driver_name, 
                        "name": driver_name,
                        "color": generate_driver_color(driver_name)
                    }
                    db.collection("strategies").document(team_id).set({
                        "drivers": firestore.ArrayUnion([driver_entry])
                    }, merge=True)
                    self.last_driver_register_time = time.time()
                except Exception as e:
                    pass

            position = int(veh_scor.mPlace)
            current_lap = int(veh_scor.mTotalLaps) 
            fuel = float(veh_tele.mFuel)
            lap_time_last = float(veh_scor.mLastLapTime)
            fuel_capacity = float(veh_tele.mFuelCapacity)
            battery_soc_pct = round(float(veh_tele.mBatteryChargeFraction) * 100.0, 1)

            car_number = "0"
            try:
                first_part = vehicle_name_raw.split(' ')[0]
                car_number = first_part.replace('#', '')
            except Exception:
                pass
            
            throttle_pct = round(float(veh_tele.mUnfilteredThrottle) * 100.0, 1)
            brake_pct = round(float(veh_tele.mUnfilteredBrake) * 100.0, 1)
            
            vx = float(veh_tele.mLocalVel.x)
            vy = float(veh_tele.mLocalVel.y)
            vz = float(veh_tele.mLocalVel.z)
            speed_ms = math.sqrt(vx*vx + vy*vy + vz*vz)
            speed_kmh = round(speed_ms * 3.6, 0)
            
            rpm = round(float(veh_tele.mEngineRPM), 0)
            max_rpm = round(float(veh_tele.mEngineMaxRPM), 0)
            water_temp = round(float(veh_tele.mEngineWaterTemp), 1)
            oil_temp = round(float(veh_tele.mEngineOilTemp), 1)
            
            # --- RECUPERATION DONNÉES CLIMAT ---
            scor_info = self.sim.Rf2Scor.mScoringInfo
            physics = self.sim.Rf2Ext.mPhysics
            ambient_temp_c = round(float(scor_info.mAmbientTemp), 1)
            
            # --- LECTURE TEMPERATURES & USURE PNEUS ---
            tire_wear_values = [] 
            brake_temp_values = []
            tire_temp_center_values = []
            
            for wheel in veh_tele.mWheels:
                # 1. Usure
                tire_wear_values.append(float(wheel.mWear))
                
                # 2. Température Freins (Gestion des valeurs nulles)
                try:
                    brake_temp_k = float(wheel.mBrakeTemp)
                    # Si < 10 Kelvin, on considère que c'est froid/buggé -> on met la température de l'air
                    if brake_temp_k < 10.0:
                        brake_temp_values.append(ambient_temp_c)
                    else:
                        brake_temp_values.append(brake_temp_k + KELVIN_TO_CELSIUS)
                except:
                    brake_temp_values.append(ambient_temp_c)

                # 3. Température Pneus (Centre)
                # On essaie de lire directement l'index 1 (Centre) sans vérifier la longueur
                try:
                    tire_temp_k = float(wheel.mTemperature[1])
                    if tire_temp_k < 10.0:
                        tire_temp_center_values.append(ambient_temp_c)
                    else:
                        tire_temp_center_values.append(tire_temp_k + KELVIN_TO_CELSIUS)
                except:
                    tire_temp_center_values.append(ambient_temp_c)
            
            track_wetness_pct = round(float(scor_info.mAvgPathWetness) * 100.0, 1)
            session_remaining_time = float(scor_info.mEndET) - float(scor_info.mCurrentET)
            
            track_name = self._safe_decode(scor_info.mTrackName)
            session_raw = int(scor_info.mSession)
            session_name = SESSION_MAP.get(session_raw, "UNKNOWN")

            rain_severity = float(scor_info.mRaining)
            if rain_severity > 0.4: weather_status = "RAIN"
            elif rain_severity > 0.05 or float(scor_info.mDarkCloud) > 0.5: weather_status = "CLOUDY"
            else: weather_status = "SUNNY"

            engine_mode = int(veh_tele.mElectricBoostMotorState)
            tc_setting = int(physics.mTractionControl)
            if tc_setting == 0: tc_setting = engine_mode
            brake_bias_front_pct = round((1.0 - float(veh_tele.mRearBrakeBias)) * 100.0, 1)

            pit_state = int(veh_scor.mPitState)
            damage_index, is_overheating = self._get_damage_status(veh_tele)
            estimated_lap_game = float(veh_scor.mEstimatedLapTime)

            rest_data = self._get_rest_data()
            est_pit_time = 0.0
            strategy_fuel_add = 0.0
            strategy_tires_count = 0
            
            ve_pct = 0.0
            if rest_data and "fuelInfo" in rest_data:
                 fi = rest_data["fuelInfo"]
                 ve_remaining = float(fi.get("currentVirtualEnergy", 0.0))
                 ve_max = float(fi.get("maxVirtualEnergy", 0.0))
                 if ve_max > 0:
                     ve_pct = round(ve_remaining / ve_max * 100.0, 1)

            if rest_data:
                pit_values = pit_estimator(rest_data)
                est_pit_time = round(pit_values[0], 1)
                strategy_fuel_add = round(pit_estimator.fuel_abs_refill, 1)
                strategy_tires_count = pit_estimator.tyre_change

            self._update_lap_metrics(current_lap, fuel, ve_pct, lap_time_last, tire_wear_values)
            
            wear_remaining_pct = [round((1.0 - w) * 100.0, 1) for w in tire_wear_values]

            data_to_send = {
                "isRaceRunning": True,
                "driverName": driver_name,
                "activeDriverId": driver_name if is_active_driver else None,
                "carNumber": car_number,       
                "carCategory": car_category,
                "teamId": team_id,
                "trackName": track_name,     
                "sessionType": session_name,   
                "position": position,
                "throttle": throttle_pct,
                "brake": brake_pct,
                "speedKmh": speed_kmh,
                "rpm": rpm,
                "maxRpm": max_rpm,
                "waterTemp": water_temp,
                "oilTemp": oil_temp,
                "currentLap": current_lap,
                "lapTimeLast": lap_time_last, 
                "fuelRemainingL": round(fuel, 2),
                "averageConsumptionFuel": self.avg_fuel_consumption, 
                "lastLapFuelConsumption": self.last_lap_fuel_consumption, # <--- AJOUT DANS L'ENVOI
                "batterySoc": battery_soc_pct,
                "virtualEnergyRemainingPct": ve_pct,
                "virtualEnergyConsumptionLastLap": self.last_lap_energy_consumption,
                "virtualEnergyAverageConsumption": self.avg_energy_consumption,
                "averageLapTime": self.ema_lap_time,
                "sessionTimeRemainingSeconds": round(max(0, session_remaining_time), 0),
                "fuelTankCapacityL": round(fuel_capacity, 2),
                "pitState": pit_state,
                "inPitLane": in_pit_lane,      
                "inGarage": in_garage,          
                "damageIndex": damage_index,
                "isOverheating": is_overheating,
                "gameEstimatedLapTime": estimated_lap_game,
                "strategyFuelToAdd": strategy_fuel_add,
                "strategyTiresChanged": strategy_tires_count,
                "strategyEstPitTime": est_pit_time,
                "weather": weather_status,
                "airTemp": ambient_temp_c,
                "trackWetness": track_wetness_pct,
                "tcSetting": tc_setting,
                "brakeBiasFront": brake_bias_front_pct,
                "engineMode": engine_mode,
                "lastPacketTime": int(time.time() * 1000), 
                "tireWearFL" : 100.0 - wear_remaining_pct[0] if len(wear_remaining_pct) > 0 else 100.0,
                "tireWearFR" : 100.0 - wear_remaining_pct[1] if len(wear_remaining_pct) > 1 else 100.0,
                "tireWearRL" : 100.0 - wear_remaining_pct[2] if len(wear_remaining_pct) > 2 else 100.0,
                "tireWearRR" : 100.0 - wear_remaining_pct[3] if len(wear_remaining_pct) > 3 else 100.0,
                "avgWearPerLapFL": self.average_wear_per_lap[0],
                "avgWearPerLapFR": self.average_wear_per_lap[1],
                "avgWearPerLapRL": self.average_wear_per_lap[2],
                "avgWearPerLapRR": self.average_wear_per_lap[3],
                "brakeTempFLC": round(brake_temp_values[0], 1) if len(brake_temp_values) > 0 else 0.0,
                "brakeTempFRC": round(brake_temp_values[1], 1) if len(brake_temp_values) > 1 else 0.0,
                "brakeTempRLC": round(brake_temp_values[2], 1) if len(brake_temp_values) > 2 else 0.0,
                "brakeTempRRC": round(brake_temp_values[3], 1) if len(brake_temp_values) > 3 else 0.0,
                "tireTempCenterFLC": round(tire_temp_center_values[0], 1) if len(tire_temp_center_values) > 0 else 0.0,
                "tireTempCenterFRC": round(tire_temp_center_values[1], 1) if len(tire_temp_center_values) > 1 else 0.0,
                "tireTempCenterRLC": round(tire_temp_center_values[2], 1) if len(tire_temp_center_values) > 2 else 0.0,
                "tireTempCenterRRC": round(tire_temp_center_values[3], 1) if len(tire_temp_center_values) > 3 else 0.0,
            }

            # Suppression du champ "activeDriverId" si on est en mode "spectateur" (garage)
            # pour ne pas écraser le pilote qui conduit réellement
            if not is_active_driver:
                 del data_to_send["activeDriverId"]

            if db:
                try:
                    db.collection("strategies").document(team_id).set(data_to_send, merge=True)
                    
                    status = "[GARAGE]" if in_garage else "[TRACK]"
                    print(f"📡 {status} {team_id} | {driver_name} | RPM: {rpm} | Fuel: {data_to_send['fuelRemainingL']}L", end="\r")
                    self.last_fuel = fuel
                except Exception as e:
                    print(f"\n❌ Erreur envoi : {e}")

if __name__ == "__main__":
    bridge = LMUBridge()
    try:
        bridge.run()
    except KeyboardInterrupt:
        print("\nArrêt.")
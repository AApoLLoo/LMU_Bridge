import time
import sys
from typing import Tuple, Any
import os

# Ensure local package directory is on sys.path so running the script from another
# working directory still finds the local `pyRfactor2SharedMemory` package.
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

# Import SimInfoAPI depuis la bibliothèque locale
try:
    from pyRfactor2SharedMemory.sharedMemoryAPI import SimInfoAPI
except ImportError:
    print("FATAL: Le module 'pyRfactor2SharedMemory' n'est pas trouvé. Vérifiez les chemins d'accès ou l'installation.")
    sys.exit(1)

# --- CONSTANTES ---

KELVIN_TO_CELSIUS = -273.15 # 0K = -273.15C
DEFAULT_TEAM_ID_LMP2 = "lemans-2025-lmp2"
DEFAULT_TEAM_ID_HYPERCAR = "lemans-2025-hypercar"

# --- CONNEXION FIREBASE ---

try:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print(f"🔥 Connecté à Firebase")
except Exception as e:
    print(f"❌ Erreur initialisation Firebase : {e}")
    db = None 

class LMUBridge:
    def __init__(self):
        self.sim = SimInfoAPI()
        self.last_fuel = -1.0
        self.last_lap = 0
        self.fuel_start_5_laps = -1.0
        self.consumption_5_laps = 0.0
        self.lap_counter_5_laps = 0
        print("🏁 En attente du jeu (assure-toi d'être EN PISTE, plugin shared memory actif)...")

    @staticmethod
    def _safe_decode(bytestring: bytes) -> str:
        try:
            return bytes(bytestring).partition(b'\0')[0].decode('utf_8').rstrip()
        except Exception:
            try:
                return bytes(bytestring).partition(b'\0')[0].decode('cp1252').rstrip()
            except Exception:
                return bytes(bytestring).partition(b'\0')[0].decode('utf_8', 'ignore').rstrip()
        return ""

    def _get_player_data(self) -> Tuple[Any, Any]:
        try:
            veh_tele = self.sim.playersVehicleTelemetry()
            veh_scor = self.sim.playersVehicleScoring()
            if veh_tele and veh_scor and veh_scor.mIsPlayer:
                return veh_tele, veh_scor
        except Exception:
            pass
        return None, None

    def calculate_consumption(self, current_lap: int, current_fuel: float):
        if current_lap != self.last_lap and current_lap > 0 and current_fuel > 0:
            if self.lap_counter_5_laps == 0:
                self.fuel_start_5_laps = current_fuel
                self.lap_counter_5_laps = 1
                self.consumption_5_laps = 0.0
            elif self.lap_counter_5_laps < 5:
                self.lap_counter_5_laps += 1
            elif self.lap_counter_5_laps == 5:
                fuel_used = self.fuel_start_5_laps - current_fuel
                if fuel_used > 0:
                    self.consumption_5_laps = round(fuel_used / 5.0, 3)
                else:
                    self.consumption_5_laps = 0.0
                self.fuel_start_5_laps = current_fuel
                self.lap_counter_5_laps = 1
            self.last_lap = current_lap

    def run(self):
        while True:
            time.sleep(0.5)
            if not self.sim.isRF2running():
                print("⏳ Jeu ou plugin shared memory non détecté...", end="\r")
                continue
            
            veh_tele, veh_scor = self._get_player_data()
            if not veh_tele or not veh_scor or veh_scor.mPlace <= 0:
                print("✅ Connecté, mais joueur non valide (es-tu en piste ?) ", end="\r")
                continue

            driver_name = self._safe_decode(veh_scor.mDriverName)
            car_category = self._safe_decode(veh_scor.mVehicleClass)
            position = int(veh_scor.mPlace)
            current_lap = int(veh_scor.mTotalLaps) 
            fuel = float(veh_tele.mFuel)

            tire_wear_values = [] # Fraction 0.0 à 1.0
            brake_temp_values = [] # Celsius
            tire_temp_center_values = [] # Celsius
            for wheel in veh_tele.mWheels:
                tire_wear_values.append(float(wheel.mWear))
                brake_temp_values.append(float(wheel.mBrakeTemp))
                if len(wheel.mTemperature) > 1:
                    temp_k = float(wheel.mTemperature[1])
                    temp_c = temp_k + KELVIN_TO_CELSIUS
                    tire_temp_center_values.append(temp_c)
                else:
                    tire_temp_center_values.append(0.0)

            avg_wear = sum(tire_wear_values) / len(tire_wear_values) if tire_wear_values else 0.0
            self.calculate_consumption(current_lap, fuel)

            # --- Détermination dynamique du TEAM_ID ---
            if "LMP2" in car_category.upper():
                team_id = DEFAULT_TEAM_ID_LMP2
            elif "HYPER" in car_category.upper():
                team_id = DEFAULT_TEAM_ID_HYPERCAR
            else:
                team_id = "unknown-category"

            data_to_send = {
                "isRaceRunning": True,
                "driverName": driver_name,
                "carCategory": car_category,
                "teamId": team_id,
                "position": position,
                "currentLap": current_lap,
                "fuelRemainingL": round(fuel, 2),
                "fuelConsumptionPerLapL": self.consumption_5_laps,
                "tireWearAvgPct": round(avg_wear * 100.0, 2),
                "tireWearFLPct": round(tire_wear_values[0] * 100.0, 2) if len(tire_wear_values) > 0 else 0.0,
                "tireWearFRPct": round(tire_wear_values[1] * 100.0, 2) if len(tire_wear_values) > 1 else 0.0,
                "tireWearRLPct": round(tire_wear_values[2] * 100.0, 2) if len(tire_wear_values) > 2 else 0.0,
                "tireWearRRPct": round(tire_wear_values[3] * 100.0, 2) if len(tire_wear_values) > 3 else 0.0,
                "brakeTempFLC": round(brake_temp_values[0], 1) if len(brake_temp_values) > 0 else 0.0,
                "brakeTempFRC": round(brake_temp_values[1], 1) if len(brake_temp_values) > 1 else 0.0,
                "brakeTempRLC": round(brake_temp_values[2], 1) if len(brake_temp_values) > 2 else 0.0,
                "brakeTempRRC": round(brake_temp_values[3], 1) if len(brake_temp_values) > 3 else 0.0,
                "tireTempCenterFLC": round(tire_temp_center_values[0], 1) if len(tire_temp_center_values) > 0 else 0.0,
                "tireTempCenterFRC": round(tire_temp_center_values[1], 1) if len(tire_temp_center_values) > 1 else 0.0,
                "tireTempCenterRLC": round(tire_temp_center_values[2], 1) if len(tire_temp_center_values) > 2 else 0.0,
                "tireTempCenterRRC": round(tire_temp_center_values[3], 1) if len(tire_temp_center_values) > 3 else 0.0,
            }

            if (
                self.last_fuel < 0
                or abs(fuel - self.last_fuel) > 0.05
                or current_lap != self.last_lap
            ):
                if db:
                    try:
                        db.collection("strategies").document(team_id).set(
                            data_to_send, merge=True
                        )
                        print(
                            f"📡 ENVOI | {driver_name} ({car_category}) P{position} | Lap {current_lap} | "
                            f"Fuel: {data_to_send['fuelRemainingL']} L | Conso: {self.consumption_5_laps} L/tour | "
                            f"Usure moy: {data_to_send['tireWearAvgPct']} % | Team: {team_id}",
                            end="\r",
                        )
                        self.last_fuel = fuel
                    except Exception as e:
                        print(f"\n❌ Erreur envoi Firestore : {e}")
                else:
                    print(
                        f"📊 DONNÉES | {driver_name} ({car_category}) P{position} | Lap {current_lap} | "
                        f"Fuel: {data_to_send['fuelRemainingL']} L | Conso: {self.consumption_5_laps} L/tour | "
                        f"Usure moy: {data_to_send['tireWearAvgPct']} % | Team: {team_id}",
                        end="\r",
                    )
                    self.last_fuel = fuel

if __name__ == "__main__":
    bridge = LMUBridge()
    try:
        bridge.run()
    except KeyboardInterrupt:
        print("\nArrêt du pont.")

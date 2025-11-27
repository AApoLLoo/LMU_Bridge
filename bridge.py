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
    # Assurez-vous que serviceAccountKey.json est dans le même dossier
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
        
        # Remplacement de la consommation 5-tours par EMA
        self.last_lap_fuel_start = -1.0 # Le point de départ du fuel pour le calcul de consommation du tour
        self.ema_fuel_consumption = 0.0
        
        # Ajout du temps au tour moyen (EMA)
        self.ema_lap_time = 0.0
        
        # Champs pour l'usure des pneus par tour (fraction worn)
        self.last_tire_wear_cumulative = [0.0] * 4 
        self.average_wear_per_lap = [0.0] * 4 
        self.lap_counter_wear = 0 
        
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

    def _update_lap_metrics(self, current_lap: int, current_fuel: float, current_lap_time_last: float, current_tire_wear_cumulative: list[float]):
        """Met à jour la consommation, l'usure et le temps moyen après un tour complet."""

        # Initialisation des valeurs de référence au premier appel si ce n'est pas déjà fait
        if self.last_lap == 0 and current_lap > 0:
            self.last_tire_wear_cumulative = current_tire_wear_cumulative
            self.last_fuel = current_fuel
            self.last_lap = current_lap
            self.last_lap_fuel_start = current_fuel 
            return

        is_new_lap = current_lap != self.last_lap and current_lap > 0
        alpha = 0.1 # Facteur de lissage EMA

        if is_new_lap:
            
            # --- Consommation de Fuel (EMA) ---
            # Calcule la consommation sur le tour précédent
            fuel_used = self.last_lap_fuel_start - current_fuel if self.last_lap_fuel_start > 0 else 0.0

            if fuel_used > 0:
                if self.ema_fuel_consumption == 0.0:
                    self.ema_fuel_consumption = round(fuel_used, 3)
                else:
                    # Applique l'EMA
                    self.ema_fuel_consumption = round(alpha * fuel_used + (1 - alpha) * self.ema_fuel_consumption, 3)
            
            self.last_lap_fuel_start = current_fuel # Mise à jour du point de départ pour le prochain tour
            
            # --- Temps au Tour Moyen (EMA) ---
            if current_lap_time_last > 0 and current_lap_time_last < 999: # Filtre les temps valides et non excessifs
                if self.ema_lap_time == 0.0:
                    self.ema_lap_time = round(current_lap_time_last, 3)
                else:
                    # Applique l'EMA
                    self.ema_lap_time = round(alpha * current_lap_time_last + (1 - alpha) * self.ema_lap_time, 3)
            
            # --- Usure des Pneus par Tour (Moyenne lissée) ---
            
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


    def run(self):
        while True:
            time.sleep(0.1)  # Petite pause pour éviter une boucle trop rapide
            if not self.sim.isRF2running():
                print("⏳ Jeu ou plugin shared memory non détecté...", end="\r")
                continue
            
            veh_tele, veh_scor = self._get_player_data()
            
            try:
                scor_info = self.sim.Rf2Scor.mScoringInfo
                physics = self.sim.Rf2Ext.mPhysics
            except Exception:
                scor_info = None
                physics = None

            if not veh_tele or not veh_scor or veh_scor.mPlace <= 0:
                print("✅ Connecté, mais joueur non valide (es-tu en piste ?) ", end="\r")
                continue

            # --- LECTURE DES DONNÉES DE BASE ---
            driver_name = self._safe_decode(veh_scor.mDriverName)
            car_category = self._safe_decode(veh_scor.mVehicleClass)
            position = int(veh_scor.mPlace)
            current_lap = int(veh_scor.mTotalLaps) 
            fuel = float(veh_tele.mFuel)
            lap_time_last = float(veh_scor.mLastLapTime)
            
            # NOUVELLE DONNÉE : Capacité du réservoir
            fuel_capacity = float(veh_tele.mFuelCapacity) if veh_tele else 0.0
            session_remaining_time = 0.0
            
            # --- DONNÉES PNEUS & TEMPÉRATURES ---
            tire_wear_values = [] 
            brake_temp_values = []
            tire_temp_center_values = []

            for wheel in veh_tele.mWheels:
                tire_wear_values.append(float(wheel.mWear))
                # NOTE : Conversion de la température des freins
                brake_temp_values.append(float(wheel.mBrakeTemp)+ KELVIN_TO_CELSIUS) 
                if len(wheel.mTemperature) > 1:
                    temp_k = float(wheel.mTemperature[1])
                    temp_c = temp_k + KELVIN_TO_CELSIUS
                    tire_temp_center_values.append(temp_c)
                else:
                    tire_temp_center_values.append(0.0)

            avg_wear = sum(tire_wear_values) / len(tire_wear_values) if tire_wear_values else 0.0
            
            # --- NOUVELLES DONNÉES MÉTÉO & SESSION ---
            ambient_temp_c = 0.0
            track_wetness_pct = 0.0
            weather_status = "UNKNOWN"
            
            if scor_info:
                # Météo
                ambient_temp_k = float(scor_info.mAmbientTemp)
                ambient_temp_c = round(ambient_temp_k, 1)
                track_wetness_pct = round(float(scor_info.mAvgPathWetness) * 100.0, 1)
                rain_severity = float(scor_info.mRaining)
                
                if rain_severity > 0.4:
                    weather_status = "RAIN"
                elif rain_severity > 0.05 or float(scor_info.mDarkCloud) > 0.5:
                    weather_status = "CLOUDY"
                else:
                    weather_status = "SUNNY"
                    
                # NOUVELLE DONNÉE : Durée restante = Temps de fin - Temps écoulé
                session_remaining_time = float(scor_info.mEndET) - float(scor_info.mCurrentET)

            # --- NOUVELLES DONNÉES RÉGLAGES/AIDES (Incluant correction TC) ---
            tc_setting = -1
            brake_bias_front_pct = 0.0
            
            # Mode Moteur / ERS (doit être lu avant la correction TC)
            engine_mode = int(veh_tele.mElectricBoostMotorState) if veh_tele else 0

            if physics:
                 tc_setting = int(physics.mTractionControl)
            
            # Correction du TC : si l'assistance est à 0, utiliser le mode moteur à la place.
            if tc_setting == 0: 
                tc_setting = engine_mode
                
            if veh_tele:
                brake_bias_rear = float(veh_tele.mRearBrakeBias)
                brake_bias_front_pct = round((1.0 - brake_bias_rear) * 100.0, 1)

            # --- CALCULS/MISE À JOUR DE L'ÉTAT ---
            # Appel de la fonction de mise à jour des métriques (incluant le nouveau temps au tour)
            self._update_lap_metrics(current_lap, fuel, lap_time_last, tire_wear_values)
            
            # --- MAPPING POUR FIREBASE ---
            if "LMP2" in car_category.upper():
                team_id = DEFAULT_TEAM_ID_LMP2
            elif "HYPER" in car_category.upper():
                team_id = DEFAULT_TEAM_ID_HYPERCAR
            else:
                team_id = "unknown-category"

            wear_remaining_pct = [round((1.0 - w) * 100.0, 1) for w in tire_wear_values]

            data_to_send = {
                "isRaceRunning": True,
                "driverName": driver_name,
                "carCategory": car_category,
                "teamId": team_id,
                "position": position,
                "currentLap": current_lap,
                "lapTimeLast": lap_time_last, 
                "fuelRemainingL": round(fuel, 2),
                
                # Remplacement de l'ancienne consommation 5-tours par l'EMA
                "averageConsumptionFuel": self.ema_fuel_consumption, 
                
                # Ajout du temps au tour moyen (EMA)
                "averageLapTime": self.ema_lap_time,
                
                # --- NOUVELLES DONNÉES DE SESSION ET CAPACITÉ ---
                "sessionTimeRemainingSeconds": round(max(0, session_remaining_time), 0),
                "fuelTankCapacityL": round(fuel_capacity, 2),
                
                # --- DONNÉES MÉTÉO & SETUP ---
                "weather": weather_status,
                "airTemp": ambient_temp_c,
                "trackWetness": track_wetness_pct,
                "tcSetting": tc_setting, # Le TC corrigé est envoyé
                "brakeBiasFront": brake_bias_front_pct,
                "engineMode": engine_mode,

                # Usure restante des pneus (télémétrie)
                "tireWearFL" : 100.0 - wear_remaining_pct[0] if len(wear_remaining_pct) > 0 else 100.0,
                "tireWearFR" : 100.0 - wear_remaining_pct[1] if len(wear_remaining_pct) > 1 else 100.0,
                "tireWearRL" : 100.0 - wear_remaining_pct[2] if len(wear_remaining_pct) > 2 else 100.0,
                "tireWearRR" : 100.0 - wear_remaining_pct[3] if len(wear_remaining_pct) > 3 else 100.0,

                # Usure moyenne par tour (stratégie)
                "avgWearPerLapFL": self.average_wear_per_lap[0],
                "avgWearPerLapFR": self.average_wear_per_lap[1],
                "avgWearPerLapRL": self.average_wear_per_lap[2],
                "avgWearPerLapRR": self.average_wear_per_lap[3],
                
                # Anciennes données (pour compatibilité)
                "tireWearAvgPct": round(avg_wear * 100.0, 2),
                "brakeTempFLC": round(brake_temp_values[0], 1) if len(brake_temp_values) > 0 else 0.0,
                "brakeTempFRC": round(brake_temp_values[1], 1) if len(brake_temp_values) > 1 else 0.0,
                "brakeTempRLC": round(brake_temp_values[2], 1) if len(brake_temp_values) > 2 else 0.0,
                "brakeTempRRC": round(brake_temp_values[3], 1) if len(brake_temp_values) > 3 else 0.0,
                "tireTempCenterFLC": round(tire_temp_center_values[0], 1) if len(tire_temp_center_values) > 0 else 0.0,
                "tireTempCenterFRC": round(tire_temp_center_values[1], 1) if len(tire_temp_center_values) > 1 else 0.0,
                "tireTempCenterRLC": round(tire_temp_center_values[2], 1) if len(tire_temp_center_values) > 2 else 0.0,
                "tireTempCenterRRC": round(tire_temp_center_values[3], 1) if len(tire_temp_center_values) > 3 else 0.0,
            }

            # --- ENVOI FIREBASE ---
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
                        # AFFICHAGE MIS À JOUR
                        print(
                            f"📡 ENVOI | {driver_name} ({car_category}) P{position} | Lap {current_lap} | "
                            f"Fuel: {data_to_send['fuelRemainingL']} L / {data_to_send['fuelTankCapacityL']} L | "
                            f"Time Left: {data_to_send['sessionTimeRemainingSeconds']:.0f}s | Bias: {data_to_send['brakeBiasFront']}% | TC/Mode: {tc_setting} | Avg Lap: {self.ema_lap_time:.3f}s",
                            end="\r",
                        )
                        self.last_fuel = fuel
                    except Exception as e:
                        print(f"\n❌ Erreur envoi Firestore : {e}")
                else:
                    print(
                        f"📊 DONNÉES | {driver_name} ({car_category}) P{position} | Lap {current_lap} | "
                        f"Fuel: {data_to_send['fuelRemainingL']} L / {data_to_send['fuelTankCapacityL']} L | "
                        f"Time Left: {data_to_send['sessionTimeRemainingSeconds']:.0f}s | Bias: {data_to_send['brakeBiasFront']}% | TC/Mode: {tc_setting} | Météo: {weather_status} | Avg Lap: {self.ema_lap_time:.3f}s",
                        end="\r",
                    )
                    self.last_fuel = fuel

if __name__ == "__main__":
    bridge = LMUBridge()
    try:
        bridge.run()
    except KeyboardInterrupt:
        print("\nArrêt du pont.")
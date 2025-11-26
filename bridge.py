import time

import firebase_admin
from firebase_admin import credentials, firestore

from pyRfactor2SharedMemory.sharedMemoryAPI import SimInfoAPI

# --- CONFIGURATION ---

TEAM_ID = "lemans-2025-hypercar"  # Change ici pour ta team (ex: "lemans-2025-lmp2")

# --- CONNEXION FIREBASE ---

try:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print(f"🔥 Connecté à Firebase pour : {TEAM_ID}")
except Exception as e:
    print(f"❌ Erreur initialisation Firebase : {e}")
    exit(1)


class LMUBridge:
    def __init__(self):
        # API shared memory rF2/LMU via pyRfactor2SharedMemory
        self.sim = SimInfoAPI()

        # Logique conso
        self.last_fuel = -1.0
        self.last_lap = 0
        self.fuel_start_5_laps = -1.0
        self.consumption_5_laps = 0.0
        self.lap_counter_5_laps = 0

        print("🏁 En attente du jeu (assure-toi d'être EN PISTE, plugin shared memory actif)...")

    # --- Lecture des données de télémétrie ---

    def read_telemetry_data(self):
        """
        Utilise la structure Telemetry exposée par pyRfactor2SharedMemory.
        Les noms exacts des champs peuvent varier légèrement selon la version de la lib,
        adapte-les si besoin en regardant sharedMemoryStruct.py.
        """
        telem = self.sim.readTelemetry()
        if telem is None:
            return None, None

        # Exemple de champs usuels : mFuel, mTyreWear[4]
        try:
            fuel = float(telem.mFuel)

            # mTyreWear peut être un tableau/list de 4 valeurs (FL, FR, RL, RR)
            wear_values = list(telem.mTyreWear)
            if len(wear_values) >= 4:
                avg_wear = sum(wear_values[:4]) / 4.0
            else:
                avg_wear = 0.0

            return fuel, avg_wear

        except Exception:
            return None, None

    # --- Lecture des données de scoring ---

    def read_scoring_data(self):
        """
        Utilise la structure Scoring exposée par pyRfactor2SharedMemory.
        On récupère le véhicule joueur, son nom, sa classe, sa position et son tour.
        """
        scor = self.sim.readScoring()
        if scor is None:
            return None, None, None, None

        try:
            player_index = scor.mPlayerVehicle
            if player_index < 0 or player_index >= scor.mNumVehicles:
                return None, None, None, None

            veh = scor.mVehicles[player_index]

            driver_name = veh.mDriverName.strip()
            car_category = veh.mScoringClass.strip()
            position = int(veh.mPlace)
            current_lap = int(veh.mLap)

            return driver_name, car_category, position, current_lap

        except Exception:
            return None, None, None, None

    # --- Calcul conso sur 5 tours ---

    def calculate_consumption(self, current_lap, current_fuel):
        if current_lap != self.last_lap and current_lap > 0 and current_fuel > 0:
            if self.lap_counter_5_laps == 0:
                # Début d'une fenêtre de 5 tours
                self.fuel_start_5_laps = current_fuel
                self.lap_counter_5_laps = 1
                self.consumption_5_laps = 0.0

            elif self.lap_counter_5_laps < 5:
                # On compte jusqu'à 5 tours
                self.lap_counter_5_laps += 1

            elif self.lap_counter_5_laps == 5:
                # Fin de fenêtre : calcul conso moyenne
                fuel_used = self.fuel_start_5_laps - current_fuel
                if fuel_used > 0:
                    self.consumption_5_laps = round(fuel_used / 5.0, 3)
                else:
                    self.consumption_5_laps = 0.0

                # Nouvelle fenêtre
                self.fuel_start_5_laps = current_fuel
                self.lap_counter_5_laps = 1

            self.last_lap = current_lap

    # --- Boucle principale ---

    def run(self):
        while True:
            time.sleep(0.5)

            # Vérifier que le jeu tourne / shared memory dispo
            if not self.sim.isRF2running():
                print("⏳ Jeu ou plugin shared memory non détecté...", end="\r")
                continue

            fuel, avg_wear = self.read_telemetry_data()
            driver_name, car_category, position, current_lap = self.read_scoring_data()

            if (
                fuel is None
                or driver_name is None
                or position is None
                or position <= 0
            ):
                print("✅ Connecté, mais joueur non valide (es-tu en piste ?) ", end="\r")
                continue

            # Conso moyenne
            self.calculate_consumption(current_lap, fuel)

            data_to_send = {
                "isRaceRunning": True,
                "driverName": driver_name,
                "carCategory": car_category,
                "position": int(position),
                "currentLap": int(current_lap),
                "tireWearAvgPct": round(avg_wear * 100.0, 2),
                "fuelRemainingL": round(fuel, 2),
                "fuelConsumptionPerLapL": self.consumption_5_laps,
            }

            # Envoi Firebase (on peut limiter les envois au changement de fuel/lap)
            if (
                self.last_fuel < 0
                or abs(fuel - self.last_fuel) > 0.05
                or current_lap != self.last_lap
            ):
                try:
                    db.collection("strategies").document(TEAM_ID).set(
                        data_to_send, merge=True
                    )
                    print(
                        f"📡 ENVOI | {driver_name} ({car_category}) "
                        f"P{position} | Lap {current_lap} | "
                        f"Fuel: {data_to_send['fuelRemainingL']} L | "
                        f"Conso: {self.consumption_5_laps} L/tour | "
                        f"Usure moy: {data_to_send['tireWearAvgPct']} %",
                        end="\r",
                    )
                    self.last_fuel = fuel
                except Exception as e:
                    print(f"\n❌ Erreur envoi Firestore : {e}")


if __name__ == "__main__":
    bridge = LMUBridge()
    try:
        bridge.run()
    except KeyboardInterrupt:
        print("\nArrêt du pont.")

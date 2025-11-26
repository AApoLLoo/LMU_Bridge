import mmap
import ctypes
import time

# Noms des fichiers de mapping mémoire créés par le plugin
RF2_SCORING_MAP = "$rFactor2SMMP_Scoring$"
RF2_TELEMETRY_MAP = "$rFactor2SMMP_Telemetry$"
RF2_MAX_VEHICLES = 128
RF2_MAX_WHEELS = 4
RF2_MAX_PIT_INFO = 48

# --- STRUCTURES DE SCORING (Pour Pilote, Position, Catégorie) ---

class rF2VehicleScoring(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mID", ctypes.c_long), # 0
        ("mPlace", ctypes.c_ubyte), # 4 <--- POSITION
        ("mIsPlayer", ctypes.c_ubyte), # 5
        ("mControl", ctypes.c_ubyte), # 6
        ("mInPits", ctypes.c_ubyte), # 7
        ("mLap", ctypes.c_long), # 8
        ("mSector", ctypes.c_long), # 12
        ("mFinishStatus", ctypes.c_ubyte), # 16
        ("mUnused1", ctypes.c_ubyte * 3), # 17 (padding)
        ("mPosition", ctypes.c_double * 3), # 20
        ("mOrientation", ctypes.c_double * 3), # 44
        ("mHeadlights", ctypes.c_ubyte), # 68
        ("mUnused2", ctypes.c_ubyte * 7), # 69 (padding)
        ("mDriverName", ctypes.c_char * 64), # 76 <--- NOM DU PILOTE
        ("mVehicleName", ctypes.c_char * 64), # 140
        ("mScoringClass", ctypes.c_char * 32), # 204 <--- CATÉGORIE
        ("mNumPitstops", ctypes.c_long), # 236
        # La taille totale de cette structure est environ 304 octets.
    ]

class rF2Scoring(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mID", ctypes.c_long),
        ("mNumVehicles", ctypes.c_long),
        ("mMaxLaps", ctypes.c_long),
        ("mEndET", ctypes.c_double),
        # On utilise une grande zone de padding pour atteindre le tableau de véhicules.
        # La taille de la Scoring Info seule est d'environ 200 octets.
        ("mSessionPadding", ctypes.c_ubyte * 256), 
        ("mVehicles", rF2VehicleScoring * RF2_MAX_VEHICLES) # Tableau des 128 véhicules
    ]

# --- STRUCTURES DE TÉLÉMÉTRIE (Pour le Fuel et l'Usure des Pneus) ---

class rF2WheelTelemetry(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mDetached", ctypes.c_int), # 0
        ("mFlat", ctypes.c_int),     # 4
        ("mBrakeTemp", ctypes.c_float), # 8
        ("mPressure", ctypes.c_float),  # 12
        ("mTemperature", ctypes.c_float * 3), # 16, 20, 24
        ("mWear", ctypes.c_float) # 28 <--- USURE DU PNEU
        # ... Reste de la structure Wheel (non nécessaire ici)
    ]

class rF2Telemetry(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mID", ctypes.c_long), # 0
        ("mUnused1", ctypes.c_ubyte * 4), # 4 (padding)
        ("mPosition", ctypes.c_double * 3), # 8
        ("mDirection", ctypes.c_double * 3), # 32
        ("mRight", ctypes.c_double * 3), # 56
        ("mSuspensionTravel", ctypes.c_double * 4), # 80
        ("mUnused2", ctypes.c_ubyte * 320), # Padding pour le reste des données physiques
        ("mFuel", ctypes.c_double), # <--- FUEL RESTANT (position approximative)
        ("mFuelCapacity", ctypes.c_double),
        ("mUnused3", ctypes.c_ubyte * 3000), # Remplissage pour atteindre les roues
        ("mWheels", rF2WheelTelemetry * RF2_MAX_WHEELS), # Tableau des 4 roues
        # ... La structure entière contient aussi l'array des autres véhicules
    ]

class SimData:
    # Laisser vide pour la logique dans bridge.py
    pass
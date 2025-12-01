import time
import sys

# Import des connecteurs
from adapter.rf2_connector import RF2Info
from adapter.restapi_connector import RestAPIInfo
from adapter.rf2_data import (
    TelemetryData, 
    ScoringData, 
    RulesData, 
    ExtendedData, 
    PitInfoData, 
    WeatherData, 
    PitStrategyData,
    Vehicle
)
# Import de VOTRE connecteur Firebase existant
from adapter.firebase_connector import FirebaseConnector

def main():
    print("Démarrage du Bridge (Hybride SM + REST)...")
    
    # Configuration
    line_up_name = input("Nom de la Line Up (Collection Firebase) : ").strip()
    if not line_up_name:
        print("Nom invalide.")
        return
    
    # --- Initialisation ---
    try:
        # 1. Firebase
        path_to_json = "serviceAccountKey.json" # Vérifiez le chemin
        fb = FirebaseConnector(path_to_json, line_up_name)
        fb.start() # Démarrage du thread d'envoi Firebase
        
        # 2. Mémoire Partagée (Haute fréquence)
        rf2_info = RF2Info()
        rf2_info.setMode(1) # Mode accès direct (plus rapide)
        
        # 3. API REST (Basse fréquence)
        # CORRECTION 1: On passe rf2_info comme 'parent_api'
        rf2_rest = RestAPIInfo(rf2_info) 
        
        # CORRECTION 2: Configuration obligatoire pour l'API REST
        rest_config = {
            "restapi_update_interval": 500, # ms
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
        rf2_rest.setConnection(rest_config)

        print("Connecté aux systèmes de LMU et Firebase.")

        # Instanciation des adaptateurs
        telemetry = TelemetryData(rf2_info)
        scoring = ScoringData(rf2_info)
        rules = RulesData(rf2_info)
        extended = ExtendedData(rf2_info)
        pit_info = PitInfoData(rf2_info)
        weather = WeatherData(rf2_info)
        vehicle_helper = Vehicle(rf2_info)
        
        # CORRECTION 3: PitStrategyData ne prend pas d'arguments rf2_info/rf2_rest dans votre fichier rf2_data.py
        pit_strategy = PitStrategyData() 

        # CORRECTION 4: Démarrage des threads de mise à jour automatique
        # Ces classes utilisent des threads, il ne faut pas appeler update() manuellement dans la boucle
        rf2_info.start()
        rf2_rest.start()

    except Exception as e:
        print(f"Erreur d'initialisation: {e}")
        return

    print(f"En attente de conduite pour : {line_up_name}")
    
    last_update_time = 0
    UPDATE_RATE = 0.5  # 2Hz (ajustable selon vos besoins Firebase)

    try:
        while True:
            # CORRECTION 5: Suppression de rf2_info.update() et rf2_rest.update()
            # Les données sont mises à jour automatiquement en arrière-plan par les threads start() ci-dessus.

            # Vérification du pilote
            status = vehicle_helper.get_local_driver_status()

            if status['is_driving'] and (time.time() - last_update_time > UPDATE_RATE):
                
                idx = status['vehicle_index']
                driver_name = status['driver_name']

                # Construction du JSON
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
                        "menu": pit_info.menu_status(),       # Shared Memory
                        "strategy": pit_strategy.pit_estimate() # API REST (Synchronous request)
                    },

                    "weather_det": weather.info(),

                    "extended": {
                        "physics": extended.physics_options(),
                        "pit_limit": extended.pit_limit()
                    }
                }

                # Envoi
                fb.send_telemetry(driver_name, payload)
                last_update_time = time.time()
                # Petit print de debug pour confirmer que ça tourne
                print(f"Data envoyée pour {driver_name}", end="\r")

            else:
                # Pause plus longue si on ne conduit pas
                time.sleep(0.5)

            # Petite pause pour ne pas saturer le CPU
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nArrêt du programme.")
    except Exception as e:
        print(f"\nErreur en cours d'exécution: {e}")
    finally:
        try:
            # Arrêt propre des threads
            rf2_info.stop()
            rf2_rest.stop()
            fb.stop()
        except:
            pass

if __name__ == "__main__":
    main()
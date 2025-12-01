"""
rF2 API data set - Option B (Hybrid Shared Memory + Direct REST)
"""
from __future__ import annotations
import requests
from validator import bytes_to_str as tostr
from validator import infnan_to_zero as rmnan
from adapter import rf2_connector
from process.pitstop import EstimatePitTime

class DataAdapter:
    __slots__ = ("shmm",)
    def __init__(self, shmm: rf2_connector.RF2Info) -> None:
        self.shmm = shmm

class TelemetryData(DataAdapter):
    __slots__ = ()
    def id(self, index: int | None = None) -> int: return self.shmm.rf2TeleVeh(index).mID
    def time_elapsed(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mElapsedTime)
    def lap_number(self, index: int | None = None) -> int: return self.shmm.rf2TeleVeh(index).mLapNumber
    def gear(self, index: int | None = None) -> int: return self.shmm.rf2TeleVeh(index).mGear
    def rpm(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mEngineRPM)
    def rpm_max(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mEngineMaxRPM)
    def temp_oil(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mEngineOilTemp)
    def temp_water(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mEngineWaterTemp)
    def turbo_pressure(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mTurboBoostPressure)
    def fuel_level(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mFuel)
    def fuel_capacity(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mFuelCapacity)
    def input_throttle(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mFilteredThrottle)
    def input_brake(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mFilteredBrake)
    def input_clutch(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mFilteredClutch)
    def input_steering(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mFilteredSteering)
    def wing_front(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mFrontWingHeight)
    def downforce_front(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mFrontDownforce)
    def downforce_rear(self, index: int | None = None) -> float: return rmnan(self.shmm.rf2TeleVeh(index).mRearDownforce)
    def local_velocity(self, index: int | None = None) -> tuple[float, float, float]: 
        vel = self.shmm.rf2TeleVeh(index).mLocalVel
        return rmnan(vel.x), rmnan(vel.y), rmnan(vel.z)

    # --- CORRECTION FORMAT DICTIONNAIRE ---
    def tire_temps(self, index: int | None = None) -> dict:
        wheels = self.shmm.rf2TeleVeh(index).mWheels
        return {
            "fl": [rmnan(t) - 273.15 for t in wheels[0].mTemperature],
            "fr": [rmnan(t) - 273.15 for t in wheels[1].mTemperature],
            "rl": [rmnan(t) - 273.15 for t in wheels[2].mTemperature],
            "rr": [rmnan(t) - 273.15 for t in wheels[3].mTemperature]
        }

    def tire_pressure(self, index: int | None = None) -> list[float]: return [rmnan(w.mPressure) for w in self.shmm.rf2TeleVeh(index).mWheels]
    def tire_wear(self, index: int | None = None) -> list[float]: return [rmnan(w.mWear) for w in self.shmm.rf2TeleVeh(index).mWheels]
    def brake_temp(self, index: int | None = None) -> list[float]: return [rmnan(w.mBrakeTemp) - 273.15 for w in self.shmm.rf2TeleVeh(index).mWheels]
    def surface_type(self, index: int | None = None) -> list[int]: return [w.mSurfaceType for w in self.shmm.rf2TeleVeh(index).mWheels]
    def wheel_detached(self, index: int | None = None) -> list[bool]: return [w.mDetached for w in self.shmm.rf2TeleVeh(index).mWheels]
    def tire_flat(self, index: int | None = None) -> list[bool]: return [w.mFlat for w in self.shmm.rf2TeleVeh(index).mWheels]
    def dents(self, index: int | None = None) -> list[int]: return list(self.shmm.rf2TeleVeh(index).mDentSeverity)
    def overheating(self, index: int | None = None) -> bool: return self.shmm.rf2TeleVeh(index).mOverheating
    def electric_data(self, index: int | None = None) -> dict:
        veh = self.shmm.rf2TeleVeh(index)
        return {"charge": rmnan(veh.mBatteryChargeFraction), "torque": rmnan(veh.mElectricBoostMotorTorque), "rpm": rmnan(veh.mElectricBoostMotorRPM), "temp_motor": rmnan(veh.mElectricBoostMotorTemperature), "temp_water": rmnan(veh.mElectricBoostWaterTemperature), "state": veh.mElectricBoostMotorState}

# ... (Reste du fichier identique à la version précédente)
class ScoringData(DataAdapter):
    __slots__ = ()
    def track_name(self) -> str: return tostr(self.shmm.rf2ScorInfo.mTrackName)
    def session_type(self) -> int: return self.shmm.rf2ScorInfo.mSession
    def time_info(self) -> dict:
        info = self.shmm.rf2ScorInfo
        return {"current": rmnan(info.mCurrentET), "end": rmnan(info.mEndET), "max_laps": info.mMaxLaps}
    def game_phase(self) -> int: return self.shmm.rf2ScorInfo.mGamePhase
    def flag_state(self) -> dict:
        info = self.shmm.rf2ScorInfo
        return {"yellow_global": info.mYellowFlagState, "sector_flags": list(info.mSectorFlag), "in_realtime": info.mInRealtime}
    def weather_env(self) -> dict:
        info = self.shmm.rf2ScorInfo
        return {"ambient_temp": rmnan(info.mAmbientTemp), "track_temp": rmnan(info.mTrackTemp), "rain": rmnan(info.mRaining), "darkness": rmnan(info.mDarkCloud), "wetness_path": (rmnan(info.mMinPathWetness), rmnan(info.mMaxPathWetness)), "wind_speed": rmnan((info.mWind.x**2 + info.mWind.y**2 + info.mWind.z**2)**0.5)}
    def vehicle_count(self) -> int: return self.shmm.rf2ScorInfo.mNumVehicles
    def get_vehicle_scoring(self, index: int) -> dict:
        veh = self.shmm.rf2ScorVeh(index)
        sector_map = {0: 3, 1: 1, 2: 2} 
        return {"id": veh.mID, "driver": tostr(veh.mDriverName), "vehicle": tostr(veh.mVehicleName), "class": tostr(veh.mVehicleClass), "position": veh.mPlace, "is_player": veh.mIsPlayer, "laps": veh.mTotalLaps, "sector": sector_map.get(veh.mSector, 0), "status": veh.mFinishStatus, "in_pits": veh.mInPits, "pit_stops": veh.mNumPitstops, "penalties": veh.mNumPenalties, "lap_dist": rmnan(veh.mLapDist), "best_lap": rmnan(veh.mBestLapTime), "last_lap": rmnan(veh.mLastLapTime), "sectors_best": (rmnan(veh.mBestSector1), rmnan(veh.mBestSector2)), "sectors_cur": (rmnan(veh.mCurSector1), rmnan(veh.mCurSector2)), "gap_leader": rmnan(veh.mTimeBehindLeader), "gap_next": rmnan(veh.mTimeBehindNext)}

class RulesData(DataAdapter):
    __slots__ = ()
    def sc_info(self) -> dict:
        rules = self.shmm.Rf2Rules.mTrackRules
        return {"active": rules.mSafetyCarActive, "laps": rules.mSafetyCarLaps, "instruction": rules.mSafetyCarInstruction}
    def yellow_flag(self) -> dict:
        rules = self.shmm.Rf2Rules.mTrackRules
        return {"detected": rules.mYellowFlagDetected, "state": rules.mYellowFlagState, "laps": rules.mYellowFlagLaps}
    def message(self) -> str: return tostr(self.shmm.Rf2Rules.mTrackRules.mMessage)
    def participant_status(self, index: int) -> dict:
        if index >= 128: return {}
        part = self.shmm.Rf2Rules.mParticipants[index]
        return {"id": part.mID, "frozen_order": part.mFrozenOrder, "yellow_severity": rmnan(part.mYellowSeverity), "relative_laps": part.mRelativeLaps, "pits_open": part.mPitsOpen, "message": tostr(part.mMessage)}

class ExtendedData(DataAdapter):
    __slots__ = ()
    def physics_options(self) -> dict:
        phy = self.shmm.rf2Ext.mPhysics
        return {"tc": phy.mTractionControl, "abs": phy.mAntiLockBrakes, "fuel_mult": phy.mFuelMult, "tire_mult": phy.mTireMult}
    def pit_limit(self) -> float: return rmnan(self.shmm.rf2Ext.mCurrentPitSpeedLimit)

class PitInfoData(DataAdapter):
    __slots__ = ()
    def menu_status(self) -> dict:
        menu = self.shmm.Rf2Pit.mPitMenu
        return {"cat_idx": menu.mCategoryIndex, "cat_name": tostr(menu.mCategoryName), "choice_idx": menu.mChoiceIndex, "choice_str": tostr(menu.mChoiceString), "num_choices": menu.mNumChoices}

class WeatherData(DataAdapter):
    __slots__ = ()
    def info(self) -> dict:
        winfo = self.shmm.Rf2Weather.mWeatherInfo
        # Correction index tableau pluie
        return {"et": rmnan(winfo.mET), "cloudiness": rmnan(winfo.mCloudiness), "ambient_temp": rmnan(winfo.mAmbientTempK) - 273.15, "rain_intensity": rmnan(winfo.mRaining[4])}

class PitStrategyData:
    __slots__ = ("_pit_estimator", "_port")
    def __init__(self, port=5397):
        self._pit_estimator = EstimatePitTime()
        self._port = port
    def pit_estimate(self) -> dict:
        try:
            url = f"http://localhost:{self._port}/rest/garage/UIScreen/RepairAndRefuel"
            resp = requests.get(url, timeout=0.1)
            if resp.status_code == 200:
                est = self._pit_estimator(resp.json())
                return {"time_min": est[0], "time_max": est[1], "fuel_to_add": est[2], "laps_to_add": est[3]}
        except: pass
        return {}

class Vehicle(DataAdapter):
    __slots__ = ()
    def speed(self, index: int | None = None) -> float:
        vel = self.shmm.rf2TeleVeh(index).mLocalVel
        return (vel.x**2 + vel.y**2 + vel.z**2)**0.5
    def aero_damage(self, index: int | None = None) -> float: return 0.0
    def get_local_driver_status(self) -> dict:
        player_idx = 0; found = False
        for i in range(self.shmm.rf2ScorInfo.mNumVehicles):
            if self.shmm.rf2ScorVeh(i).mIsPlayer:
                player_idx = i; found = True; break
        if not found: return {"is_driving": False, "driver_name": "Unknown"}
        scor_veh = self.shmm.rf2ScorVeh(player_idx)
        is_driving = (scor_veh.mIsPlayer == 1 and scor_veh.mControl == 0 and self.shmm.rf2ScorInfo.mInRealtime == 1)
        return {"is_driving": is_driving, "driver_name": tostr(scor_veh.mDriverName), "vehicle_index": player_idx}
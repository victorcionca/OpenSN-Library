
import math
import ephem
import datetime
from opensn.const.const_var import R_EARTH,LIGHT_SPEED_M_S
from opensn.model.position import Position
from instance_types import TYPE_SATELLITE,TYPE_GROUND_STATION
from instance_types import EX_TLE0_KEY,EX_TLE1_KEY,EX_TLE2_KEY,EX_LATITUDE_KEY,EX_LONGITUDE_KEY,EX_ALTITUDE_KEY
from opensn.model.instance import Instance

def deg2rad(deg: float) -> float:
    return deg / 180 * math.pi
        
def get_orbital_period(instance: Instance) -> int:
    """Returns the orbital period of a satellite in seconds"""
    ephem_obj = ephem.readtle(
        instance.extra[EX_TLE0_KEY],
        instance.extra[EX_TLE1_KEY],
        instance.extra[EX_TLE2_KEY],
    )
    return int(24*3600/ephem_obj.n)

def get_ra(instance: Instance) -> float:
    """Returns the right ascension in degrees"""
    ephem_obj = ephem.readtle(
        instance.extra[EX_TLE0_KEY],
        instance.extra[EX_TLE1_KEY],
        instance.extra[EX_TLE2_KEY],
    )
    return ephem_obj.a_ra/(2*math.pi)*360

def str_checksum(line: str) -> int:
    sum_num = 0
    for c in line:
        if c.isdigit():
            sum_num += int(c)
        elif c == '-':
            sum_num += 1
    return sum_num % 10

def satellite_change_longitude(inst: Instance, new_longitude:float):
    # Get line 2 from TLE
    inst_tle2 = inst.extra[EX_TLE2_KEY]
    # Remove the checksum as it will be recomputed
    inst_tle2 = inst_tle2[:-1]
    inst_tle2_fields = inst_tle2.split()
    # Rebuild the line
    upd_line_2 = "2 00000  %02.4f %08.4f 0000011   0.0000 %8.4f %11.8f00000"%(
                        float(inst_tle2_fields[2]),
                        new_longitude,
                        float(inst_tle2_fields[6]),
                        float(inst_tle2_fields[7]))
    # Recompute the checksum
    chksum = str_checksum(upd_line_2)
    upd_line_2 += str(chksum)
    inst.extra[EX_TLE2_KEY] = upd_line_2

def check_orbit_has_coverage(instance: Instance, time:datetime.datetime,
                             point:Position, distance: int) -> bool:
    """
    Checks if the next orbit of the given satellite will cover the given point.
    Coverage is defined as the satellite being within distance of the point.
    
    Calculates all subsequent positions of the satellite from the current
    date, until the satellite completes an orbit, and evaluates the distance
    from the point.

    Returns: true if the point is covered, false otherwise.
    """
    sat_orbit_period = get_orbital_period(instance)
    start_date = ephem.Date(time)
    end_date = start_date + ephem.second*sat_orbit_period
    sat = ephem.readtle(
            instance.extra[EX_TLE0_KEY],
            instance.extra[EX_TLE1_KEY],
            instance.extra[EX_TLE2_KEY],
        )
    while start_date < end_date:
        sat.compute(start_date)
        dist = distance_meter(Position(sat.sublat, sat.sublong), point)
        if dist < distance: return True
        start_date += ephem.second*60   # Advance 1min at a time
    return False

def calculate_postion(instance: Instance,time:datetime.datetime) -> Position:
    ret = Position()
    if instance.type == TYPE_SATELLITE and instance.start:
        ephem_time = ephem.Date(time)
        ephem_obj = ephem.readtle(
            instance.extra[EX_TLE0_KEY],
            instance.extra[EX_TLE1_KEY],
            instance.extra[EX_TLE2_KEY],
        )
        ephem_obj.compute(ephem_time)
        ret.latitude = ephem_obj.sublat
        ret.longitude = ephem_obj.sublong
        ret.altitude = ephem_obj.elevation
    elif instance.type == TYPE_GROUND_STATION:
        ret.latitude = deg2rad(float(instance.extra[EX_LATITUDE_KEY]))
        ret.longitude = deg2rad(float(instance.extra[EX_LONGITUDE_KEY]))
        ret.altitude = deg2rad(float(instance.extra[EX_ALTITUDE_KEY]))
    return ret

def distance_meter(one:Position,another:Position) -> float: # meter
    z1 = (one.altitude+R_EARTH) * math.sin(one.latitude)
    base1 = (one.altitude+R_EARTH) * math.cos(one.latitude)
    x1 = base1 * math.cos(one.longitude)
    y1 = base1 * math.sin(one.longitude)
    z2 = (another.altitude+R_EARTH) * math.sin(another.latitude)
    base2 = (another.altitude+R_EARTH) * math.cos(another.latitude)
    x2 = base2 * math.cos(another.longitude)
    y2 = base2 * math.sin(another.longitude)
    return math.sqrt((x1-x2)**2+(y1-y2)**2+(z1-z2)**2)

def get_propagation_delay_s(distance_meter:float) -> float: # second
    return distance_meter / LIGHT_SPEED_M_S

def select_closest_satellite(
        ground_station:Instance,
        position_map:dict[str,Position],
        instance_map:dict[str,Instance]
    ) -> (str,bool) :
    closet_distance = math.inf
    select_satellite_id = ""
    change = True
    for instance_id,instance_info in instance_map.items():
        if instance_info.type != TYPE_SATELLITE:
            continue
        new_distance = distance_meter(
            position_map[instance_id],
            position_map[ground_station.instance_id],
        )
        if new_distance < closet_distance:
            closet_distance = new_distance
            select_satellite_id = instance_id
    if len(ground_station.connections) < 0 and select_satellite_id == "":
        return "",False
    
    for end_info in ground_station.connections.values():
        if select_satellite_id == end_info.instance_id:
            change = False
    return select_satellite_id,change
